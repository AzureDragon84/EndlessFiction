import hashlib
import logging
from pathlib import Path
import shutil
from threading import Event
from typing import Any, Optional

import requests
from scraper import extract_base

from ..assets.images import favicon_icon
from ..context import ctx

logger = logging.getLogger(__name__)


class FetchService:
    """Shared HTTP client for non-crawl traffic (translator service, Calibre
    API, favicons, source index).

    One scraper for the process, from `ctx.scraper.plain()`. A job's abort signal is
    passed per request rather than assigned to the session, so nothing a caller sets
    can reach another thread's request.
    """

    def post(
        self,
        url: str,
        signal: Optional[Event] = None,
        **kwargs: Any,
    ) -> requests.Response:
        return ctx.scraper.plain().post(url, signal=signal, **kwargs)

    def get(
        self,
        url: str,
        signal: Optional[Event] = None,
    ) -> bytes:
        resp = ctx.scraper.plain().get(url, signal=signal)
        resp.raise_for_status()
        return resp.content

    def get_response(
        self,
        url: str,
        signal: Optional[Event] = None,
        **kwargs: Any,
    ) -> requests.Response:
        """A GET whose status the caller inspects itself.

        The free translation endpoint reports quota in the body and status, and
        a 429 there is an ordinary answer to fall back from, not an exception.
        """
        return ctx.scraper.plain().get(url, signal=signal, **kwargs)

    def download(
        self,
        url: str,
        file: Path,
        signal: Optional[Event] = None,
    ) -> None:
        ctx.scraper.plain().get_file(url, output_file=file, signal=signal)
        logger.debug(f"Downloaded: {file}")

    def favicon(
        self,
        url: str,
        signal: Optional[Event] = None,
    ) -> Path:
        favicon_url = f"{extract_base(url)}favicon.ico"

        filename = hashlib.md5(favicon_url.encode()).hexdigest()
        out_file = ctx.files.resolve(f"images/{filename}.ico")
        if out_file.is_file():
            return out_file

        try:
            self.download(favicon_url, out_file, signal)
        except Exception:
            logger.info(f"Failed to download favicon: {url}")
            shutil.copy(favicon_icon(), out_file)

        return out_file
