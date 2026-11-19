"""Glossary review: extract a novel's terms, decide them, reject false positives.

The translator dashboard belongs to the embedded package and knows about engines,
not novels, so the novel-side glossary workflow lives here: a small JSON API plus
a self-contained screen (`/api/glossary/ui`) that uses it. The screen signs in with
an admin account and keeps the token in sessionStorage.

Scanning is coverage-based: a chapter counts as read for a method once its
`glossary_scans` row exists, so repeated calls walk a novel to the end instead of
re-reading the first chapters. Everything a scan proposes is stored before it is
applied — a rendering decision never requires re-reading a chapter.
"""

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Body, Path as PathParam, Query, Security
from fastapi.responses import HTMLResponse

from ...context import ctx
from ...dao import LanguageCode, User
from ...exceptions import ServerErrors
from ..models import (
    GlossaryExtractorInfo,
    GlossaryImportRequest,
    GlossaryImportResult,
    GlossaryRejectRequest,
    GlossaryScanRequest,
    GlossaryScanResult,
    GlossaryScanStatus,
    GlossaryTermInfo,
    GlossaryTermPage,
    GlossaryTermUpdate,
)
from ..security import ensure_user

# Data routes; mounted with the admin/translation gate.
router = APIRouter()
# The page itself is public (it is an empty shell until you sign in).
ui_router = APIRouter()

_UI_FILE = Path(__file__).parent.parent / "glossary" / "index.html"


@ui_router.get("/ui", include_in_schema=False, response_class=HTMLResponse)
def glossary_ui() -> HTMLResponse:
    return HTMLResponse(_UI_FILE.read_text(encoding="utf-8"))


@router.get(
    "/{novel_id}/extractors",
    summary="Extraction methods available for this novel",
)
def list_glossary_extractors(
    novel_id: str = PathParam(),
) -> List[GlossaryExtractorInfo]:
    return [GlossaryExtractorInfo(**item) for item in ctx.novels.list_glossary_extractors(novel_id)]


@router.get(
    "/{novel_id}/scan",
    summary="How much of the novel the chosen methods have scanned",
)
def glossary_scan_status(
    novel_id: str = PathParam(),
    extractors: Optional[List[str]] = Query(
        default=None, description="Methods to report on; empty means the defaults"
    ),
) -> GlossaryScanStatus:
    report = ctx.novels.glossary_scan_status(novel_id, extractors)
    return GlossaryScanStatus(**report)


@router.post(
    "/{novel_id}/extract",
    summary="Scan the novel's stored chapters for terms",
)
def extract_glossary_terms(
    novel_id: str = PathParam(),
    body: GlossaryScanRequest = Body(default=GlossaryScanRequest()),
    user: User = Security(ensure_user),
) -> GlossaryScanResult:
    if not ctx.tier.translation_enabled(user):
        raise ServerErrors.tier_not_allowed
    try:
        report = ctx.novels.extract_glossary_terms(
            novel_id,
            extractors=body.extractors,
            limit=body.limit,
            min_freq=body.min_freq,
            force=body.force,
        )
    except ValueError as error:
        raise ServerErrors.invalid_input.with_extra(str(error))
    return GlossaryScanResult(**report)


@router.post(
    "/{novel_id}/import",
    summary="Store a pasted term list without reading any chapter",
)
def import_glossary_terms(
    novel_id: str = PathParam(),
    body: GlossaryImportRequest = Body(default=GlossaryImportRequest()),
    user: User = Security(ensure_user),
) -> GlossaryImportResult:
    if not ctx.tier.translation_enabled(user):
        raise ServerErrors.tier_not_allowed
    report = ctx.novels.import_glossary_terms(novel_id, body.terms)
    return GlossaryImportResult(**report)


@router.get(
    "/{novel_id}/terms",
    summary="List a novel's glossary terms with their decision in one language",
)
def list_glossary_terms(
    novel_id: str = PathParam(),
    language: LanguageCode = Query(default=LanguageCode.english),
    search: str = Query(default="", description="Filter by source text"),
    tier: str = Query(default="all", description="'all', 'core' or 'tail'"),
    include_fragments: bool = Query(
        default=False, description="Include surfaces that sit inside a longer term"
    ),
    min_freq: int = Query(
        default=1,
        ge=0,
        description="Hide terms no occurrence backs any more; imported terms stay",
    ),
    categorized: bool = Query(default=False, description="Only terms the extractor could classify"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> GlossaryTermPage:
    items, total = ctx.novels.list_glossary_terms(
        novel_id,
        language.value,
        offset=offset,
        limit=limit,
        search=search,
        tier=tier,
        include_fragments=include_fragments,
        min_freq=min_freq,
        categorized=categorized,
    )
    return GlossaryTermPage(
        items=[GlossaryTermInfo(**item) for item in items],
        total=total,
    )


@router.put(
    "/{novel_id}/terms/{term_id}",
    summary="Decide one term; an empty rendering makes it undecided again",
)
def update_glossary_term(
    novel_id: str = PathParam(),
    term_id: str = PathParam(),
    language: LanguageCode = Query(default=LanguageCode.english),
    body: GlossaryTermUpdate = Body(default=GlossaryTermUpdate()),
    user: User = Security(ensure_user),
) -> bool:
    if not ctx.tier.translation_enabled(user):
        raise ServerErrors.tier_not_allowed
    return ctx.novels.update_glossary_term(
        novel_id,
        term_id,
        language,
        body.rendering,
        user_id=user.id,
    )


@router.post(
    "/{novel_id}/terms/{term_id}/reject",
    summary="Reject a surface and stop the extractor proposing it again",
)
def reject_glossary_term(
    novel_id: str = PathParam(),
    term_id: str = PathParam(),
    body: GlossaryRejectRequest = Body(default=GlossaryRejectRequest()),
    user: User = Security(ensure_user),
) -> bool:
    if not ctx.tier.translation_enabled(user):
        raise ServerErrors.tier_not_allowed
    return ctx.novels.reject_glossary_term(novel_id, term_id, body.reason)
