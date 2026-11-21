"""Novel translation on top of the embedded `lncrawl-translator` package.

Translation runs in-process: the package's `TranslatorService` (stateless,
multi-engine, glossary-aware) is owned lazily by this service, and its
dashboard is mounted into the server API. This service owns the glossary
loop: it loads a novel's stored glossary, sends it with every request, and
merges the `new_terms` the engine returns back into storage so names stay
consistent across chapters.
"""

from functools import cached_property
from hashlib import sha256
import logging
from threading import Event
from typing import TYPE_CHECKING, Any, Callable, Dict, List, NoReturn, Optional, TypeVar

import sqlmodel as sq

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
from ..enums import LanguageCode, TranslationMode
from ..exceptions import AbortedException, ServerErrors
from .glossary import (
    apply_engine_proposal,
    bump_revision,
    glossary_lock,
    record_extracted_terms,
    term_lock,
)
from .glossary_extract import extract_terms

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


def _code(lang: Any) -> str:
    """The bare language code, e.g. 'en' (enum members stringify to their name otherwise)."""
    return lang.value if isinstance(lang, LanguageCode) else str(lang)


def _estimate_tokens(texts: List[str]) -> int:
    """Rough token cost of a request, used only for pool accounting."""
    return max(1, sum(len(text or "") for text in texts) // 4)


def _estimate_output(result: Any) -> int:
    """Rough token length of an engine answer."""
    text = getattr(result, "html", None) or ""
    if not text:
        text = "".join(getattr(result, "translations", None) or [])
    return max(1, len(text) // 4)


def _source_code(novel: Novel) -> Optional[str]:
    """A known source language for `novel`, or None to let the service auto-detect."""
    lang = getattr(novel, "language", None)
    if not lang:
        return None
    try:
        return LanguageCode(lang).value
    except ValueError:
        return None


def _raise_call_error(error: BaseException) -> NoReturn:
    """Map one engine error onto the status it actually deserves.

    A busy provider is a 503 and a rejected request is a 422, but a crash in
    the translation code itself is a 500 with a traceback. Everything used to
    be a 503, so the package's `look-behind requires fixed-width pattern`
    reached the dashboard as a quota problem and sent everyone to the key
    pool. A fault that is retried on every key also spends the whole pool on
    the same failure, which is why the unexpected cases raise immediately.
    """
    from translator import ApiError, InvalidRequestError

    if isinstance(error, InvalidRequestError):
        message = str(getattr(error, "message", "") or error)
        logger.error(f"translation request rejected: {message}", exc_info=error)
        raise ServerErrors.invalid_input.with_extra(message) from error

    if isinstance(error, ApiError):
        status_code = int(getattr(error, "status_code", 0) or 0)
        message = str(getattr(error, "message", "") or error)
        if status_code in (429, 503):
            retry = getattr(error, "retry_after_seconds", None)
            extra = f"retry after {retry}s" if retry else (message or "all engines busy")
            raise ServerErrors.translation_quota_exhausted.with_extra(extra) from error
        if status_code == 502:
            raise ServerErrors.translation_service_unavailable.with_extra(message) from error
        if 400 <= status_code < 500:
            raise ServerErrors.invalid_input.with_extra(
                f"{status_code}: {message}".strip(": ")
            ) from error
        logger.error(f"translation failed ({status_code}): {message}", exc_info=error)
        raise ServerErrors.translation_internal_error.with_extra(
            f"{status_code}: {message}".strip(": ")
        ) from error

    logger.error("translation crashed", exc_info=error)
    raise ServerErrors.translation_internal_error.with_extra(
        f"{type(error).__name__}: {error}"
    ) from error


class TranslationService:
    @cached_property
    def engine(self) -> "TranslatorService":
        """The embedded translator: engines, routing, and rate limits run
        in-process on the package's own event-loop thread.

        The config file is projected from the key pool first, so one provider
        exists per API key and the package rate-limits each key on its own. The
        file is only rewritten when the pool actually changed.
        """
        from translator import TranslatorService

        ctx.key_pool.ensure_config()
        return TranslatorService(config_path=ctx.config.translator.config_file)

    def reload(self) -> None:
        """Rebuild the embedded translator after a pool/config change."""
        if "engine" in self.__dict__:
            try:
                self.engine.close()
            except Exception:
                logger.warning("Failed to close the translator service", exc_info=True)
            del self.__dict__["engine"]

    def close(self) -> None:
        if "engine" in self.__dict__:
            self.engine.close()

    def selftest(self) -> Dict[str, Any]:
        """Exercise the chapter-translation plumbing without spending a key.

        Every check here is a class of failure that used to surface as "503
        quota exhausted" while no key had been touched: a pattern that cannot
        compile, a chunker that loses markup, a glossary round-trip that does
        not restore its terms, or a pool that projects no engine.
        """
        checks: List[Dict[str, Any]] = []

        def check(name: str, call: Callable[[], str]) -> None:
            try:
                checks.append({"check": name, "ok": True, "detail": call()})
            except Exception as error:
                logger.error(f"selftest failed: {name}", exc_info=True)
                checks.append(
                    {
                        "check": name,
                        "ok": False,
                        "detail": f"{type(error).__name__}: {error}",
                    }
                )

        def sentence_split() -> str:
            from .mt import _SENTENCE_SPLIT

            pieces = [
                piece
                for piece in _SENTENCE_SPLIT.split("这是一句话。这也是！真的吗？" * 50)
                if piece
            ]
            return f"{len(pieces)} sentence piece(s)"

        def chunking() -> str:
            from translator.text.html import chunk_html, estimate_tokens

            # The configured budget never splits anything, so force a small one.
            # The chunker refuses to split one oversized element mid-markup, so
            # the fixture is many small paragraphs rather than one long <p>. A
            # bypassed chunker returns the 1000+ token sample whole, which the
            # size check below already fails on. Sizes use the chunker's own
            # estimator because that is the budget it promises to keep.
            budget = 200
            html = "".join(
                f"<p>第{index}段：这是一段用于测试分块的文本。</p>" for index in range(1, 61)
            )
            chunks = chunk_html(html, budget)
            if "".join(chunks) != html:
                raise AssertionError("chunks do not reassemble to the original html")
            if any(estimate_tokens(chunk) > budget for chunk in chunks):
                raise AssertionError(f"a chunk exceeds the forced {budget}-token budget")
            return (
                f"{len(chunks)} chunk(s) at a forced {budget} tokens"
                f" from {estimate_tokens(html)} source tokens, reassembled exactly"
            )

        def glossary_roundtrip() -> str:
            from translator.text.glossary import protect, reinject

            html = "<p>赵辰登上了星舰。</p>"
            protected, mapping = protect(html, {"赵辰": "Zhao Chen", "星舰": "starship"})
            if not mapping or protected == html:
                raise AssertionError("glossary protection replaced nothing")
            restored = reinject(protected, mapping)
            for expected in ("Zhao Chen", "starship"):
                if expected not in restored:
                    raise AssertionError(f"{expected!r} missing after reinject")
            if "\ue000" in restored:
                raise AssertionError("a placeholder survived reinject")
            return f"{len(mapping)} term(s) protected and restored"

        def extraction() -> str:
            sample = "赵辰说道。赵辰笑了。赵辰走了。John Smith met John Smith."
            found = extract_terms([(1, sample)], language="zh", limit=50)
            return f"{len(found)} local candidate(s)"

        def pool() -> str:
            units = ctx.key_pool.units()
            if not units:
                raise AssertionError("no Gemini key saved")
            engine = self.engine
            return f"{len(units)} unit(s); {type(engine).__name__} built from the pool"

        check("sentence split", sentence_split)
        check("html chunking", chunking)
        check("glossary round-trip", glossary_roundtrip)
        check("local extraction", extraction)
        check("engine config", pool)
        return {
            "ok": all(item["ok"] for item in checks),
            "checks": checks,
            "budget": int(ctx.config.translator.max_request_tokens),
            "models": ctx.key_pool.chain("translate"),
        }

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
        from translator import AbortedError

        try:
            return call()
        except AbortedError as e:
            raise AbortedException(str(e)) from e
        except Exception as e:
            _raise_call_error(e)

    def _attempt(self, call: Callable[[], T]):
        """Run one engine call and return `(result, error)` instead of raising.

        The pool needs the error to decide whether to rest this key, try the
        next one, or stop; mapping it to an HTTP error happens only after the
        last candidate is spent.
        """
        from translator import AbortedError, ApiError, InvalidRequestError

        try:
            return call(), None
        except AbortedError as e:
            raise AbortedException(str(e)) from e
        except (ApiError, InvalidRequestError) as e:
            return None, e
        except Exception as e:
            # Anything else is a fault in the translation code, not an engine
            # answer. Hand it back so it is reported once as a 500.
            return None, e

    def _dispatch(
        self,
        build: Callable[[Optional[str]], T],
        *,
        estimated_tokens: int,
        signal: Optional[Event] = None,
    ) -> T:
        """Run a translation through the key pool, one unit at a time.

        The pool picks the unit (model on one key) with headroom and this pins
        `engine=` to it, so the package never silently moves the call to
        another model. A quota answer rests that key and tries the next; when
        no unit can serve the call the job is paused to the reset instead of
        failing, which is what the notebook's `auto_pause_on_daily` does.
        """
        from translator import InvalidRequestError

        from .key_pool import PoolExhausted

        units = ctx.key_pool.units()
        if not units:
            raise ServerErrors.translation_failure.with_extra(
                "No Gemini key saved. Add keys in the key pool settings."
            )

        tried: List[str] = []
        last_error = None
        while len(tried) < len(units):
            lease = ctx.key_pool.lease(
                "translate",
                estimated_tokens=estimated_tokens,
                exclude=tried,
                units=units,
            )
            if lease is None:
                break
            unit = lease.unit
            tried.append(unit.id)
            result, error = self._attempt(lambda: build(unit.engine_id))
            if result is not None:
                ctx.key_pool.report(
                    lease,
                    tokens_in=estimated_tokens,
                    tokens_out=_estimate_output(result),
                )
                return result

            last_error = error
            if error is None:
                continue
            if isinstance(error, InvalidRequestError):
                # A rejected request is our bug; trying it on the next key only
                # spends the whole pool on the same failure.
                _raise_call_error(error)
            status = int(getattr(error, "status_code", 0) or 0)
            if status in (429, 503):
                kind = "rate" if status == 429 else "quota"
            elif status in (500, 502, 504, 529):
                kind = "failed"
            else:
                # Not an engine answer at all: surface it as an internal error
                # instead of a spent key.
                _raise_call_error(error)
            detail = getattr(error, "message", "") or str(error)
            ctx.key_pool.report(lease, error=kind, detail=str(detail)[:200])

        if ctx.config.translator.auto_pause_on_daily and ctx.key_pool.all_exhausted():
            raise PoolExhausted(ctx.key_pool.resume_seconds())
        retry = ctx.key_pool.resume_seconds()
        raise ServerErrors.translation_quota_exhausted.with_extra(
            f"all keys are spent; next reset in {retry}s"
            if last_error is None
            else f"{last_error}; next reset in {retry}s"
        )

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
        engine: Optional[str] = None,
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
        if engine:
            payload["engine"] = engine
        else:
            return self._dispatch(
                lambda pinned: self.engine.translate_text(
                    {**payload, "engine": pinned},
                    signal=signal,
                    timeout=ctx.config.translator.request_timeout,
                ),
                estimated_tokens=_estimate_tokens(texts),
                signal=signal,
            )
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
        engine: Optional[str] = None,
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
        if engine:
            payload["engine"] = engine
        else:
            return self._dispatch(
                lambda pinned: self.engine.translate_html(
                    {**payload, "engine": pinned},
                    signal=signal,
                    timeout=ctx.config.translator.request_timeout,
                ),
                estimated_tokens=_estimate_tokens([html]),
                signal=signal,
            )
        return self._invoke(
            lambda: self.engine.translate_html(
                payload,
                signal=signal,
                timeout=ctx.config.translator.request_timeout,
            )
        )

    def machine_html(
        self,
        html: str,
        target: Any,
        *,
        source: Optional[str] = None,
        glossary: Optional[Dict[str, str]] = None,
        signal: Optional[Event] = None,
    ) -> str:
        """Pinned keyless Bing pass, used as the Web lane's fallback."""
        result = self._translate_html(
            html,
            target,
            source=source,
            glossary=glossary,
            signal=signal,
            engine="bing",
        )
        return result.html

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

        Frequency is cumulative, so this runs only on a chapter's first
        translation: scanning a retranslation would count the same text twice.
        Extraction is auxiliary — a chapter must still translate when the
        scanner cannot read it.
        """
        try:
            terms = extract_terms(
                [(chapter.serial, content)], language=ctx.terms.novel_language(novel)
            )
        except Exception as error:
            logger.warning(f"glossary pre-pass skipped for chapter {chapter.serial}: {error}")
            return
        if not terms:
            return
        with term_lock(chapter.novel_id), ctx.db.session() as sess:
            record_extracted_terms(sess, chapter.novel_id, terms, chapter_ids=[chapter.id])
            sess.commit()

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
        mode: TranslationMode = TranslationMode.ai,
    ) -> None:
        mode = TranslationMode(mode)
        if mode in (TranslationMode.web, TranslationMode.web_plus):
            self._translate_chapter_web(chapter, target, signal, mode)
            return

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
        translated = ctx.terms.mark(body.html, chapter.novel_id, _code(target))
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

    def _translate_chapter_web(
        self,
        chapter: Chapter,
        target: LanguageCode,
        signal: Optional[Event],
        mode: TranslationMode,
    ) -> None:
        """Keyless Web/Web+ pass, cached as a file and independent of the pool.

        Web+ enforces the glossary through placeholders and then marks the
        rendered terms, so a later glossary edit re-renders this file instead of
        translating it again.
        """
        language = _code(target)
        path = ChapterTranslation.mode_path(chapter.novel_id, chapter.serial, language, mode)
        try:
            if ctx.files.exists(path) and ctx.files.load_text(path).strip():
                return
        except Exception:
            pass

        content = ctx.files.load_text(chapter.content_file)
        novel = ctx.novels.get(chapter.novel_id)
        glossary = (
            self._load_glossary(chapter.novel_id, target)
            if mode == TranslationMode.web_plus
            else {}
        )
        try:
            translated, engine = ctx.mt.translate(
                content,
                language,
                source=_source_code(novel),
                glossary=glossary,
                signal=signal,
            )
        except Exception as error:
            raise ServerErrors.translation_failure.with_extra(str(error)) from error

        if mode == TranslationMode.web_plus:
            translated = ctx.terms.mark(translated, chapter.novel_id, language)
        ctx.files.save_text(path, translated)
        logger.info(f"Web translation ({mode.value}) chapter {chapter.serial} via {engine}")
