"""Novel translation on top of the embedded `lncrawl-translator` package.

Translation runs in-process: the package's `TranslatorService` (stateless,
multi-engine, glossary-aware) is owned lazily by this service, and its
dashboard is mounted into the server API. This service owns the glossary
loop: it loads a novel's stored glossary, sends it with every request, and
merges the `new_terms` the engine returns back into storage so names stay
consistent across chapters.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import cached_property
from hashlib import sha256
import json
import logging
import re
from threading import Event
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, TypeVar

import sqlmodel as sq

from ..config import TRANSLATOR_KEY_STATUS_FILE, split_lines
from ..context import ctx
from ..dao import (
    Chapter,
    ChapterTranslation,
    GlossaryEntry,
    GlossaryTerm,
    Novel,
    NovelTranslation,
    Volume,
    VolumeTranslation,
)
from ..enums import LanguageCode
from ..exceptions import AbortedException, ServerErrors
from .glossary import (
    apply_engine_proposal,
    bump_revision,
    glossary_lock,
    scan_chapter_terms,
)

if TYPE_CHECKING:
    from translator import (
        TranslateHtmlResponse,
        TranslateTextResponse,
        TranslatorService,
    )

logger = logging.getLogger(__name__)

T = TypeVar("T")

# How many characters of the previous translated chapter to pass as continuity context.
_PREV_TAIL_CHARS = 500
# How long an admin settings save waits for the engine to swap in a new config.
_CONFIG_APPLY_TIMEOUT = 30

# ---------------------------------------------------------------------------------------------
# Gemini key pool
# ---------------------------------------------------------------------------------------------
# The translator routes to an engine, and an engine is one model on one provider (one
# account, one key). A key pool therefore materialises as one provider per key and one
# engine per (model, key), all carrying this prefix so a rebuild can remove exactly what
# the previous one created. The user-facing knobs stay model-centric: an ordered model
# list plus a list of keys.
GEMINI_POOL_PREFIX = "gemini-pool-"
_POOL_ENGINE_SEPARATOR = "@"
_POOL_LANES = ("chapter", "short_text")
MAX_POOL_KEYS = 100
MAX_POOL_MODELS = 10

# A probe is one GET of the provider's model list: cheap, and rejected immediately
# when the key is not usable.
_PROBE_TIMEOUT = 10
_PROBE_WORKERS = 5
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def _pool_providers(config: Any) -> List[Any]:
    """The pool's providers, in the order the keys were stored."""
    providers = [p for p in config.providers if p.id.startswith(GEMINI_POOL_PREFIX)]
    return sorted(providers, key=lambda p: p.id)


def _fingerprint(key: str) -> str:
    """Enough of a key to tell two of them apart without showing either."""
    return f"{key[:4]}…{key[-4:]}" if len(key) > 8 else "…"


def _probe_key(url: str, provider_id: str, key: str) -> Dict[str, Any]:
    """One liveness probe: does this key still list the provider's models?"""
    row: Dict[str, Any] = {
        "provider": provider_id,
        "fingerprint": _fingerprint(key),
        "alive": None,
        "status": None,
        "error": "",
    }
    try:
        response = ctx.http.get_response(
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=_PROBE_TIMEOUT,
        )
    except Exception as error:
        # Rejected keys arrive as an HTTPError; only a transport failure has no response.
        response = getattr(error, "response", None)
        if response is None:
            row["error"] = str(error)[:200]
            return row
    return _classify_probe(row, response)


def _error_message(body: str) -> str:
    """The provider's message from a JSON error body, on one line."""
    text = (body or "").strip()
    if text.startswith("{"):
        try:
            error = json.loads(text).get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])[:200]
        except ValueError:
            pass
    return re.sub(r"\s+", " ", text)[:200]


def _classify_probe(row: Dict[str, Any], response: Any) -> Dict[str, Any]:
    """Read a probe response: 2xx works, 4xx usually means the key is gone."""
    status = response.status_code
    detail = _error_message(getattr(response, "text", "") or "")
    row["status"] = status
    if status == 200:
        row["alive"] = True
    elif status == 429:
        # Throttled, but the key is real: pruning it would throw quota away.
        row["alive"] = True
        row["error"] = "rate limited"
    elif status in (400, 401, 403):
        row["alive"] = False
        row["error"] = detail
    else:
        row["error"] = detail
    return row


def _write_key_status(rows: List[Dict[str, Any]]) -> None:
    """Cache the last probe so the settings page can show it without probing."""
    try:
        TRANSLATOR_KEY_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRANSLATOR_KEY_STATUS_FILE.write_text(
            json.dumps({"checked_at": int(time.time() * 1000), "rows": rows}, indent=2),
            encoding="utf-8",
        )
    except OSError as error:
        logger.warning(f"Unable to cache the Gemini key status: {error}")


def _code(lang: Any) -> str:
    """The bare language code, e.g. 'en' (enum members stringify to their name otherwise)."""
    return lang.value if isinstance(lang, LanguageCode) else str(lang)


def _source_code(novel: Novel) -> Optional[str]:
    """A known source language for `novel`, or None to let the service auto-detect."""
    lang = getattr(novel, "language", None)
    if not lang:
        return None
    try:
        return LanguageCode(lang).value
    except ValueError:
        return None


class TranslationService:
    @cached_property
    def engine(self) -> "TranslatorService":
        """The embedded translator: engines, routing, and rate limits run
        in-process on the package's own event-loop thread. Its config file
        lives in the app data dir and is edited via the mounted dashboard."""
        from translator import TranslatorService

        return TranslatorService(config_path=ctx.config.translator.config_file)

    def close(self) -> None:
        if "engine" in self.__dict__:
            self.engine.close()

    def reload_config(self) -> None:
        """Apply a config file written outside the dashboard (the admin settings page).

        Before the engine is first used there is nothing to swap — it reads the
        file when it is constructed — so this is a no-op rather than a lock on the
        settings page.
        """
        if "engine" not in self.__dict__:
            return
        from translator.config import load_config

        config = load_config(ctx.config.translator.config_file)
        store = self.engine.store
        if store.loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(store.apply(config), store.loop)
        try:
            future.result(timeout=_CONFIG_APPLY_TIMEOUT)
        except Exception as error:
            # The key is already saved; the next start picks it up regardless.
            logger.warning(f"translator config not applied live: {error}")

    # ---------------------------------------------------------------------------------------------
    # Engine plumbing
    # ---------------------------------------------------------------------------------------------

    def detect_language(self, text: str) -> Optional[str]:
        """Locally detected ISO 639-1 code for `text`, or None when unknown.
        No engine quota and no event loop involved."""
        from translator import detect_code

        return detect_code(text)

    def _invoke(self, call: Callable[[], T]) -> T:
        """Run an engine call, mapping the package's error taxonomy to ServerErrors."""
        from translator import AbortedError, ApiError, InvalidRequestError

        try:
            return call()
        except AbortedError as e:
            raise AbortedException(str(e)) from e
        except ApiError as e:
            if e.status_code == 503:
                retry = e.retry_after_seconds
                extra = f"retry after {retry}s" if retry else (e.message or "all engines busy")
                raise ServerErrors.translation_quota_exhausted.with_extra(extra) from e
            raise ServerErrors.translation_failure.with_extra(
                f"{e.status_code}: {e.message}".strip()
            ) from e
        except InvalidRequestError as e:
            raise ServerErrors.translation_failure.with_extra(e.message) from e

    # ---------------------------------------------------------------------------------------------
    # Endpoint wrappers
    # ---------------------------------------------------------------------------------------------

    def _translate_texts(
        self,
        texts: List[str],
        target: Any,
        *,
        source: Optional[str] = None,
        glossary: Optional[Dict[str, str]] = None,
        context: Optional[str] = None,
        signal: Optional[Event] = None,
    ) -> "TranslateTextResponse":
        payload: Dict[str, Any] = {
            "texts": texts,
            "target_lang": _code(target),
            "glossary": glossary or {},
        }
        if source:
            payload["source_lang"] = source
        if context:
            payload["context"] = context
        return self._invoke(
            lambda: self.engine.translate_text(
                payload,
                signal=signal,
                timeout=ctx.config.translator.request_timeout,
            )
        )

    def _translate_html(
        self,
        html: str,
        target: Any,
        *,
        source: Optional[str] = None,
        glossary: Optional[Dict[str, str]] = None,
        context: Optional[Dict[str, str]] = None,
        extract_terms: bool = True,
        signal: Optional[Event] = None,
    ) -> "TranslateHtmlResponse":
        payload: Dict[str, Any] = {
            "html": html,
            "target_lang": _code(target),
            "glossary": glossary or {},
            "extract_terms": extract_terms,
        }
        if source:
            payload["source_lang"] = source
        if context:
            payload["context"] = context
        return self._invoke(
            lambda: self.engine.translate_html(
                payload,
                signal=signal,
                timeout=ctx.config.translator.request_timeout,
            )
        )

    # ---------------------------------------------------------------------------------------------
    # Gemini key pool
    # ---------------------------------------------------------------------------------------------

    def sync_gemini_pool(self, keys: Optional[List[str]] = None) -> None:
        """Rebuild the Gemini providers, engines and routing from models x keys.

        The lane lists the pool model-major: every key of the preferred model is tried
        before the next model is used, so the model list stays the quality order and the
        keys only spread the quota. Passing no `keys` keeps the stored pool.
        """
        from translator.config import load_config, save_config

        path = ctx.config.translator.config_file
        config = load_config(path)
        account = config.provider("gemini")
        template = config.engine("gemini-flash-lite-latest") or config.engine("gemini-flash-latest")
        if account is None or template is None:
            raise ValueError("The translator config has no gemini provider or Gemini engine")

        models = split_lines(ctx.config.translator.gemini_models)[:MAX_POOL_MODELS]
        if keys is None:
            keys = [key for key in (p.api_key or "" for p in _pool_providers(config)) if key]
        keys = [key for key in keys if key][:MAX_POOL_KEYS]

        config.providers = [p for p in config.providers if not p.id.startswith(GEMINI_POOL_PREFIX)]
        config.engines = [e for e in config.engines if not e.id.startswith(GEMINI_POOL_PREFIX)]
        for lane in _POOL_LANES:
            order = [
                i for i in getattr(config.routing, lane) if not i.startswith(GEMINI_POOL_PREFIX)
            ]
            setattr(config.routing, lane, order)

        for index, key in enumerate(keys, start=1):
            provider_id = f"{GEMINI_POOL_PREFIX}{index:03d}"
            config.providers.append(account.model_copy(update={"id": provider_id, "api_key": key}))
            for model in models:
                config.engines.append(
                    template.model_copy(
                        update={
                            "id": f"{provider_id}{_POOL_ENGINE_SEPARATOR}{model}",
                            "provider": provider_id,
                            "model": model,
                        }
                    )
                )

        pooled = [
            f"{GEMINI_POOL_PREFIX}{index:03d}{_POOL_ENGINE_SEPARATOR}{model}"
            for model in models
            for index in range(1, len(keys) + 1)
        ]
        for lane in _POOL_LANES:
            setattr(config.routing, lane, pooled + list(getattr(config.routing, lane)))

        save_config(config, path)
        logger.info(f"gemini pool: {len(keys)} keys x {len(models)} models")
        self.reload_config()

    def check_gemini_keys(self) -> List[Dict[str, Any]]:
        """Probe every pooled key and cache the result for the settings page.

        `alive` is None when the probe itself failed (network, timeout), which is kept
        distinct from a provider rejecting the key so a flaky check cannot prune it.
        """
        from translator.config import load_config

        config = load_config(ctx.config.translator.config_file)
        accounts = _pool_providers(config)
        rows: List[Dict[str, Any]] = []
        if accounts:
            account = config.provider("gemini")
            base_url = (account.base_url if account else "") or _GEMINI_BASE_URL
            url = f"{base_url.rstrip('/')}/models"
            with ThreadPoolExecutor(max_workers=_PROBE_WORKERS) as pool:
                rows = list(pool.map(lambda a: _probe_key(url, a.id, a.api_key or ""), accounts))
        _write_key_status(rows)
        return rows

    def prune_gemini_keys(self) -> Dict[str, Any]:
        """Drop the pooled keys a check rejected and rebuild the pool around the rest."""
        from translator.config import load_config

        rows = self.check_gemini_keys()
        dead = {row["provider"] for row in rows if row["alive"] is False}
        if dead:
            config = load_config(ctx.config.translator.config_file)
            keys = [p.api_key or "" for p in _pool_providers(config) if p.id not in dead]
            self.sync_gemini_pool(keys=keys)
        remaining = len(_pool_providers(load_config(ctx.config.translator.config_file)))
        return {"checked": len(rows), "removed": sorted(dead), "remaining": remaining}

    # ---------------------------------------------------------------------------------------------
    # Glossary storage
    # ---------------------------------------------------------------------------------------------

    def _load_glossary(self, novel_id: str, target: Any) -> Dict[str, str]:
        """Decided renderings for one target language, in the plain
        `source -> rendering` shape the translator package expects."""
        with ctx.db.session() as sess:
            rows = sess.exec(
                sq.select(GlossaryTerm.source, GlossaryEntry.rendering)
                .join(GlossaryEntry, sq.col(GlossaryEntry.term_id) == sq.col(GlossaryTerm.id))
                .where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryEntry.language) == _code(target),
                    sq.col(GlossaryEntry.rendering).is_not(None),
                )
            ).all()
        return {source: rendering for source, rendering in rows if rendering}

    def _merge_glossary(
        self,
        novel_id: str,
        target: Any,
        new_terms: Dict[str, str],
        engine: Optional[str] = None,
    ) -> None:
        """Store engine-proposed terms; a locked (editor) entry is never overwritten."""
        if not new_terms:
            return
        language = _code(target)
        with glossary_lock(novel_id, language), ctx.db.session() as sess:
            changed = 0
            for source, rendering in new_terms.items():
                source = str(source or "").strip()
                rendering = str(rendering or "").strip()
                if not source or not rendering:
                    continue
                if apply_engine_proposal(
                    sess,
                    novel_id,
                    language,
                    source,
                    rendering,
                    engine=engine,
                ):
                    changed += 1
            if changed:
                bump_revision(sess, novel_id, language)
            sess.commit()

    def _run_prepass(self, novel: Novel, chapter: Chapter, content: str) -> None:
        """Store the chapter's recurring names before the engine sees it.

        This writes the same coverage rows as a review scan, so a chapter
        translated first is already covered and a later scan replaces its
        numbers instead of counting the same text twice. Extraction is
        auxiliary — a chapter must still translate when the scanner cannot.
        """
        try:
            scan_chapter_terms(
                chapter.novel_id,
                chapter.serial,
                content,
                novel.language or None,
            )
        except Exception as error:
            logger.warning(f"glossary pre-pass skipped for chapter {chapter.serial}: {error}")

    def _previous_chapter_tail(self, chapter: Chapter, target: Any) -> Optional[str]:
        if chapter.serial <= 1:
            return None
        with ctx.db.session() as sess:
            prev = sess.exec(
                sq.select(ChapterTranslation).where(
                    sq.col(ChapterTranslation.novel_id) == chapter.novel_id,
                    sq.col(ChapterTranslation.chapter_serial) == chapter.serial - 1,
                    sq.col(ChapterTranslation.language) == _code(target),
                )
            ).first()
        if not prev or not prev.is_available:
            return None
        from bs4 import BeautifulSoup

        text = ctx.files.load_text(prev.content_file)
        plain = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
        return plain[-_PREV_TAIL_CHARS:] if plain else None

    # ---------------------------------------------------------------------------------------------
    # Public methods
    # ---------------------------------------------------------------------------------------------

    def translate_novel(
        self,
        novel: Novel,
        target: LanguageCode,
        signal: Optional[Event] = None,
    ) -> None:
        if ctx.novels.get_novel_translation(novel, target):
            return

        source = _source_code(novel)
        glossary = self._load_glossary(novel.id, target)

        result = self._translate_texts(
            [novel.title, novel.authors or ""],
            target,
            source=source,
            glossary=glossary,
            context="Web novel title and author names",
            signal=signal,
        )
        translations = result.translations
        terms = result.new_terms
        engine = result.engine
        title = translations[0] if translations else novel.title
        authors = translations[1] if len(translations) > 1 else (novel.authors or "")

        synopsis = ""
        if novel.synopsis:
            syn = self._translate_html(
                novel.synopsis,
                target,
                source=source,
                glossary={**glossary, **terms},
                context={"novel_title": title},
                signal=signal,
            )
            synopsis = syn.html
            terms = {**terms, **syn.new_terms}

        with ctx.db.session() as sess:
            sess.add(
                NovelTranslation(
                    novel_id=novel.id,
                    language=_code(target),
                    title=title,
                    authors=authors or None,
                    synopsis=synopsis or None,
                    extra={"engine": engine} if engine else {},
                )
            )
            sess.commit()

        self._merge_glossary(novel.id, target, terms, engine=engine)

    def translate_volume(
        self,
        volume: Volume,
        target: LanguageCode,
        signal: Optional[Event] = None,
    ) -> None:
        if ctx.volumes.get_volume_translation(volume, target):
            return

        glossary = self._load_glossary(volume.novel_id, target)
        result = self._translate_texts(
            [volume.title],
            target,
            glossary=glossary,
            context="Web novel volume title",
            signal=signal,
        )
        terms = result.new_terms
        engine = result.engine
        title = result.translations[0] if result.translations else volume.title

        with ctx.db.session() as sess:
            sess.add(
                VolumeTranslation(
                    novel_id=volume.novel_id,
                    volume_serial=volume.serial,
                    language=_code(target),
                    volume_title=title,
                    extra={"engine": engine} if engine else {},
                )
            )
            sess.commit()

        self._merge_glossary(volume.novel_id, target, terms, engine=engine)

    def translate_chapter(
        self,
        chapter: Chapter,
        target: LanguageCode,
        signal: Optional[Event] = None,
    ) -> None:
        translation = ctx.chapters.get_chapter_translation(chapter, target)

        content = ctx.files.load_text(chapter.content_file)
        content_hash = sha256(content.encode()).hexdigest()
        if translation and translation.content_hash == content_hash and translation.is_available:
            return

        novel = ctx.novels.get(chapter.novel_id)
        if translation is None:
            self._run_prepass(novel, chapter, content)
        source = _source_code(novel)
        glossary = self._load_glossary(chapter.novel_id, target)

        context: Dict[str, str] = {"chapter_title": chapter.title}
        if novel.title:
            context["novel_title"] = novel.title
        if novel.synopsis:
            context["synopsis"] = novel.synopsis
        tail = self._previous_chapter_tail(chapter, target)
        if tail:
            context["previous_chapter_tail"] = tail

        body = self._translate_html(
            content,
            target,
            source=source,
            glossary=glossary,
            context=context,
            signal=signal,
        )
        translated = body.html
        terms = body.new_terms
        engine = body.engine
        if body.warnings:
            logger.info(f"translate/html warnings (chapter {chapter.serial}): {body.warnings}")

        title_result = self._translate_texts(
            [chapter.title],
            target,
            source=source,
            glossary={**glossary, **terms},
            context="Web novel chapter title",
            signal=signal,
        )
        title_terms = title_result.new_terms
        title = title_result.translations[0] if title_result.translations else chapter.title

        with ctx.db.session() as sess:
            if not translation:
                translation = ChapterTranslation(
                    novel_id=chapter.novel_id,
                    chapter_serial=chapter.serial,
                    language=_code(target),
                    chapter_title=title,
                    content_hash=content_hash,
                    extra={"engine": engine} if engine else {},
                )
                sess.add(translation)
            else:
                extra = dict(**translation.extra)
                if engine:
                    extra["engine"] = engine
                sess.exec(
                    sq.update(ChapterTranslation)
                    .where(sq.col(ChapterTranslation.id) == translation.id)
                    .values(
                        chapter_title=title,
                        content_hash=content_hash,
                        extra=extra,
                    )
                )
            sess.commit()

        ctx.files.save_text(translation.content_file, translated)
        self._merge_glossary(chapter.novel_id, target, {**terms, **title_terms}, engine=engine)
