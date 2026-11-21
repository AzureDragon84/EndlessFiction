import asyncio
from contextlib import asynccontextmanager
import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from ..assets.version import get_version
from ..context import ctx
from ..exceptions import ServerErrors, get_exception_handlers
from .api import router as api
from .middleware.staticfiles import CustomStaticFiles, StaticFilesGuard

web_dir = (Path(__file__).parent / "web").absolute()
ui_dir = (Path(__file__).parent / "ui").absolute()

# Loaded as a classic script, before the SPA bundle, on the SPA shell only.
# The SPA is generated output; this is the seam that hands its reader and
# translator routes over to the pages this project owns.
SPA_BRIDGE = '<script src="/ui/spa-bridge.js"></script>'


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        ctx.setup()
        ctx.mail.start()
        ctx.scheduler.start()
        ctx.recommendations.warmup()
        yield
    finally:
        ctx.destroy()


app = FastAPI(
    version=get_version(),
    title="Lightnovel Crawler",
    description="Download novels from online sources and generate e-books",
    lifespan=lifespan,
    exception_handlers=get_exception_handlers(),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    terms_of_service="https://raw.githubusercontent.com/lncrawl/lncrawl-web/refs/heads/artifacts/TERMS_OF_SERVICE.html",
    contact={
        "email": "lncrawl@pm.me",
        "name": "Lightnovel Crawler",
        "url": "https://github.com/lncrawl/lightnovel-crawler",
    },
    license_info={
        "name": "License: GPLv3",
        "url": "https://raw.githubusercontent.com/lncrawl/lightnovel-crawler/dev/LICENSE",
    },
)


# Add middleares
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Refresh-Token"],
)

app.add_middleware(
    GZipMiddleware,
    minimum_size=1000,
)

app.add_middleware(StaticFilesGuard, prefix="/static")

# Experimental Features
if ctx.config.server.enable_browse_route:
    from .middleware.browser import BrowserNavigation

    app.add_middleware(BrowserNavigation, prefix="/browse")


# Add APIs
app.include_router(api, prefix="/api")


# Mount static files
app.mount("/static", CustomStaticFiles(), name="static")


# Lightweight liveness probe — no auth, used by Docker healthcheck
@app.get("/health", include_in_schema=False)
async def health():
    job_count = ctx.jobs.count()
    user_count = ctx.users.count()
    return {
        "status": "ok",
        "version": get_version(),
        "users": user_count,
        "jobs": job_count,
    }


def _page(name: str) -> HTMLResponse:
    text = (ui_dir / name).read_text(encoding="utf-8")
    return HTMLResponse(
        text,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# The SPA's Read button navigates to `/read/<chapter id>` and its translator
# entry to `/translator`; both are served here instead of the SPA shell.
# Reader and dashboard styles and scripts. Kept outside `server/web/` because
# that directory is generated build output, and served no-cache because these
# file names are not content-hashed.
@app.get("/ui/{path:path}", include_in_schema=False)
async def serve_ui(path: str) -> FileResponse:
    target = (ui_dir / path).resolve()
    if not target.is_relative_to(ui_dir) or not target.is_file():
        raise ServerErrors.not_found
    mime_type, _ = mimetypes.guess_type(target)
    return FileResponse(
        target,
        media_type=mime_type or "application/octet-stream",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/read", include_in_schema=False)
@app.get("/read/", include_in_schema=False)
@app.get("/read/{chapter_id}", include_in_schema=False)
@app.get("/read/{chapter_id}/{language}", include_in_schema=False)
async def serve_reader(
    chapter_id: str = "",
    language: str = "",
) -> HTMLResponse:
    return _page("reader.html")


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def serve_admin() -> HTMLResponse:
    return _page("admin.html")


@app.get("/reader", include_in_schema=False)
@app.get("/reader/", include_in_schema=False)
async def serve_legacy_reader() -> RedirectResponse:
    return RedirectResponse("/read", status_code=307)


# The translator package's dashboard used to be mounted here. Its settings now
# live in the Admin Dashboard, together with the glossary and chapter lanes.
@app.get("/translator", include_in_schema=False)
@app.get("/api/translator", include_in_schema=False)
@app.get("/api/translator/{rest:path}", include_in_schema=False)
async def retired_translator_dashboard(
    request: Request,
    rest: str = "",
) -> RedirectResponse:
    query = str(request.url.query)
    return RedirectResponse("/admin" + (f"?{query}" if query else ""), status_code=307)


# The SPA's worker answers every navigation with the app shell, so once it is
# installed a returning visitor's `/read/...` and `/admin` requests never reach
# these routes. Its navigation denylist is the only supported place to declare a
# server-rendered page, so the worker is served with this project's routes added
# to it. A worker that no longer has that shape is served untouched, because a
# blind rewrite would be worse than the shell winning a race.
_SW_DENYLIST = (
    "{denylist:[/^\\/api/,/^\\/static/,/^\\/docs/,/^\\/health/]}",
    "{denylist:[/^\\/api/,/^\\/static/,/^\\/docs/,/^\\/health/,/^\\/admin/,/^\\/read/,/^\\/reader/,/^\\/ui/]}",
)


@app.get("/sw.js", include_in_schema=False)
async def serve_service_worker() -> Response:
    text = (web_dir / "sw.js").read_text(encoding="utf-8")
    original, patched = _SW_DENYLIST
    if original in text:
        text = text.replace(original, patched)
    return Response(
        text,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


def _spa_shell() -> str:
    """The generated SPA index, with the bridge script added on the way out."""
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    if SPA_BRIDGE in html:
        return html
    return html.replace("</body>", f"  {SPA_BRIDGE}\n  </body>")


# Mount frontend
@app.get("/{fallback:path}", include_in_schema=False)
async def serve_web(fallback: str):
    # Unknown API paths must fail fast, not silently serve the SPA shell.
    if fallback == "api" or fallback.startswith("api/"):
        raise ServerErrors.not_found
    target_file = web_dir.joinpath(fallback)
    if not target_file.is_relative_to(web_dir):
        raise ServerErrors.not_found
    loop = asyncio.get_event_loop()
    if not await loop.run_in_executor(None, target_file.is_file):
        target_file = web_dir / "index.html"
    if target_file.name == "index.html":
        return HTMLResponse(
            await loop.run_in_executor(None, _spa_shell),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    mime_type, _ = mimetypes.guess_type(target_file)
    if not mime_type:
        mime_type = "application/octet-stream"
    if mime_type == "text/javascript":
        mime_type = "application/javascript"
    if target_file.name in {"sw.js", "registerSW.js"}:
        return FileResponse(
            target_file,
            media_type=mime_type,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return FileResponse(target_file, media_type=mime_type)
