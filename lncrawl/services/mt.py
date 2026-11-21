"""Keyless machine translation for the reader's Web and Web+ modes.

The translator package has no Google engine kind, and Google Cloud
Translation v3 needs a service account and billing, which this project does not
use. The free browser endpoint (`translate.googleapis.com/translate_a/single`
with `client=gtx`) is what `translate.google.co.in` calls, so it is used
directly here when it works. It is unofficial: it can rate-limit by address
and change without notice, which is why Bing — the package's keyless Edge
translator, HTML-native and glossary-aware — is the fallback, and the order is
an admin setting.

Both paths return HTML, so a stored Web/Web+ chapter keeps its paragraphs.
Glossary enforcement on Google is placeholder substitution: each source term is
replaced with a private-use codepoint before the request and swapped back after
translation, exactly the trick the package uses for NMT engines that cannot
take a dictionary.
"""

from __future__ import annotations

from html import unescape
import logging
import re
from threading import Lock
import time
from typing import Dict, List, Optional, Tuple

from ..context import ctx

logger = logging.getLogger(__name__)

# Three shapes of the same free endpoint. `gtx` is the classic one but some
# addresses get its "Sorry..." block page; the dict-chrome-ex clients are the
# ones Chrome's built-in translator uses and they keep HTML intact.
# POST avoids the URL-length limit a whole chapter blows through; clients5 only
# answers GET and is kept as the short-payload fallback.
_GOOGLE_POST_URL = "https://translate.googleapis.com/translate_a/single"
_GOOGLE_POST_URL_ALT = "https://translate.google.com/translate_a/single"
_GOOGLE_GET_URL = "https://clients5.google.com/translate_a/t"
# The free endpoint starts refusing long payloads well before it says so.
_CHUNK_CHARS = 3500
# One request at a time keeps a burst from looking like abuse to Google.
_MIN_INTERVAL = 0.7
# A closing block tag ends a piece; the tag itself stays with its text so the
# joined result is still the caller's HTML.
_BLOCK_END = re.compile(r"</(?:p|div|h[1-6]|li|blockquote|tr)>\s*", re.IGNORECASE)

# A single oversized block is cut on sentence ends. It is a module constant so
# the admin self test can compile the exact pattern a real request uses.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s*")

_lock = Lock()
_last_call = 0.0


class MachineTranslationError(Exception):
    pass


class MachineTranslateService:
    """Free HTML translation with a configurable engine order."""

    def translate(
        self,
        html: str,
        target: str,
        *,
        source: Optional[str] = None,
        glossary: Optional[Dict[str, str]] = None,
        signal=None,
    ) -> Tuple[str, str]:
        """Translate `html`, returning `(html, engine_name)`.

        Raises `MachineTranslationError` only when every configured engine
        failed; the caller decides whether that is a job failure or a 404.
        """
        errors: List[str] = []
        order = ctx.config.translator.web_engine_order or ["google", "bing"]
        for name in order:
            try:
                if name == "google":
                    return self._google(html, target, source, glossary, signal), "google"
                if name == "bing":
                    return (
                        ctx.translator.machine_html(
                            html,
                            target,
                            source=source,
                            glossary=glossary,
                            signal=signal,
                        ),
                        "bing",
                    )
                errors.append(f"unknown engine {name!r}")
            except Exception as error:
                errors.append(f"{name}: {error}")
                logger.info(f"Web translation engine {name} failed: {error}")
        raise MachineTranslationError("; ".join(errors) or "no free engine configured")

    # ------------------------------------------------------------------
    # Google
    # ------------------------------------------------------------------

    def _google(
        self,
        html: str,
        target: str,
        source: Optional[str],
        glossary: Optional[Dict[str, str]],
        signal,
    ) -> str:
        from translator.text.glossary import protect, reinject

        if not html.strip():
            return html
        protected, mapping = protect(html, glossary or {})
        parts = self._google_chunks(protected, target, source, signal)
        return reinject("".join(parts), mapping)

    def _google_chunks(self, html: str, target: str, source: Optional[str], signal) -> List[str]:
        chunks = self._split(html)
        return [self._google_one(chunk, target, source, signal) for chunk in chunks]

    @staticmethod
    def _split(html: str) -> List[str]:
        """Group block-level pieces into requests the endpoint will accept."""
        blocks: List[str] = []
        last = 0
        for match in _BLOCK_END.finditer(html):
            blocks.append(html[last : match.end()])
            last = match.end()
        if last < len(html):
            blocks.append(html[last:])
        chunks: List[str] = []
        current = ""
        for block in blocks:
            if current and len(current) + len(block) > _CHUNK_CHARS:
                chunks.append(current)
                current = ""
            if len(block) > _CHUNK_CHARS:
                # A single huge paragraph: cut on sentence ends, not mid-tag.
                for piece in _SENTENCE_SPLIT.split(block):
                    if current and len(current) + len(piece) > _CHUNK_CHARS:
                        chunks.append(current)
                        current = ""
                    current += piece
                continue
            current += block
        if current:
            chunks.append(current)
        return chunks or [html]

    def _google_one(self, html: str, target: str, source: Optional[str], signal) -> str:
        global _last_call
        # No `format=html` here: that flag makes this endpoint strip every tag
        # from the reply. Without it the markup comes back untouched.
        params = {
            "client": "dict-chrome-ex",
            "sl": source or "auto",
            "tl": target,
            "dt": "t",
        }
        errors: List[str] = []
        attempts = (
            ("post", _GOOGLE_POST_URL),
            ("post", _GOOGLE_POST_URL_ALT),
            ("get", _GOOGLE_GET_URL),
        )
        for method, url in attempts:
            with _lock:
                wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
                if wait > 0:
                    if signal is not None:
                        signal.wait(wait)
                    else:
                        time.sleep(wait)
                _last_call = time.monotonic()

                try:
                    if method == "post":
                        response = ctx.http.post(
                            url,
                            signal=signal,
                            data={**params, "q": html},
                            timeout=90,
                        )
                    else:
                        response = ctx.http.get_response(
                            url,
                            signal=signal,
                            params={**params, "q": html},
                            timeout=90,
                        )
                except Exception as error:
                    errors.append(f"{url}: {error}")
                    continue

            if response.status_code == 429:
                errors.append(f"{url}: 429")
                continue
            if response.status_code in (301, 302, 303, 307, 308):
                # The free endpoint answers a burst of automated calls by
                # redirecting to its captcha page; the other shapes of the same
                # endpoint are behind the same block, so stop trying.
                location = str(response.headers.get("location") or "")
                if "/sorry/" in location:
                    errors.append(f"{url}: blocked by Google (captcha)")
                    break
                errors.append(f"{url}: http {response.status_code}")
                continue
            if response.status_code != 200:
                errors.append(f"{url}: http {response.status_code}")
                continue
            try:
                translated = _extract(response.json())
            except Exception as error:
                errors.append(f"{url}: unreadable response: {error}")
                continue
            translated = unescape(translated)
            if not translated.strip():
                errors.append(f"{url}: empty")
                continue
            if "<" in html and "<" not in translated:
                errors.append(f"{url}: dropped the markup")
                continue
            return translated

        raise MachineTranslationError("; ".join(errors) or "google failed")


def _extract(payload) -> str:
    """Text out of either free-endpoint shape.

    `clients5` answers `["translated"]`; `translate_a/single` answers
    `[[["translated", "source", ...], ...], ...]`.
    """
    if not isinstance(payload, list) or not payload:
        return ""
    if all(isinstance(item, str) for item in payload):
        return "".join(payload)
    first = payload[0]
    if isinstance(first, str):
        return "".join(str(item) for item in payload if isinstance(item, str))
    if isinstance(first, list) and first and isinstance(first[0], list):
        return "".join(
            str(segment[0])
            for segment in first
            if isinstance(segment, list) and segment and isinstance(segment[0], str)
        )
    if isinstance(first, list):
        return "".join(
            str(segment[0])
            for segment in payload
            if isinstance(segment, list) and segment and isinstance(segment[0], str)
        )
    return ""
