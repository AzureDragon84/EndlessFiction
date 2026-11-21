"""Read-time glossary rendering.

A translated chapter stores a `data-g` handle on every term it renders, never
the decision itself as immutable text. That is what makes the glossary behave
like WTR-Lab's: editing a term re-renders the chapter the reader already has,
instead of paying to translate it again. The admin glossary is authoritative;
a reader's personal rendering sits on top of it and changes only what that
reader sees.

`mark` runs once, when a translation is produced. `render` runs on every read
and is cheap: one query for the handles the chapter actually uses.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, cast

from bs4 import BeautifulSoup
from bs4.element import NavigableString
from sqlalchemy import Table
import sqlmodel as sq

from ..context import ctx
from ..dao import (
    Chapter,
    GlossaryCandidate,
    GlossaryEntry,
    GlossaryOverride,
    GlossaryRevision,
    GlossaryScan,
    GlossaryTerm,
    GlossaryTermCount,
    GlossaryUserEntry,
    Novel,
    NovelGlossary,
)
from ..enums import (
    HIDDEN_VERDICTS,
    TermCategory,
    TermVerdict,
    coerce_category,
    coerce_verdict,
)
from ..utils.text_tools import normalize_term_key
from ..utils.time_utils import current_timestamp
from .glossary import (
    apply_engine_proposal,
    bump_revision,
    create_manual_entry,
    decide_manually,
    glossary_lock,
    record_extracted_terms,
    term_lock,
)
from .glossary_extract import ExtractedTerm, extract_terms, plausible_surface
from .glossary_profile import base_code, describe_profiles, profile_for

# Attribute the reader uses to find a term again after a glossary edit.
TERM_ATTR = "data-g"

# "All rows" on the review sheet: the sheet serves a page, not the whole table.
NO_LIMIT = 2000
# Source characters one paid extraction request carries. Larger chunks cost the
# same call but risk the reply's token budget; smaller ones multiply the spend.
LLM_CHUNK_CHARS = 8000

# Per-novel rendering style, stored in `Novel.extra` rather than a table: it is
# three settings about one novel, not a record with a life of its own.
STYLE_KEY = "translation_style"
STYLE_DEFAULTS: Dict[str, Any] = {
    "honorifics": "strip",
    "name_order": "family-given",
    "categories": [],
}
HONORIFIC_POLICIES = ("strip", "keep")
NAME_ORDERS = ("family-given", "given-family")


def _clean_categories(names: Iterable[Any]) -> List[str]:
    """Known categories only, de-duplicated and sorted; an unknown name is dropped."""
    found = {category.value for category in map(coerce_category, names) if category is not None}
    return sorted(found)


def _clean_style(raw: Any) -> Dict[str, Any]:
    """A stored style with defaults filled in and anything unusable dropped.

    What is read and what is written go through the same pass, so a value an
    older version stored (or a hand-edited `extra`) can never reach a prompt.
    """
    style = dict(STYLE_DEFAULTS)
    if isinstance(raw, dict):
        if raw.get("honorifics") in HONORIFIC_POLICIES:
            style["honorifics"] = raw["honorifics"]
        if raw.get("name_order") in NAME_ORDERS:
            style["name_order"] = raw["name_order"]
        style["categories"] = _clean_categories(raw.get("categories") or [])
    return style


@dataclass(frozen=True)
class LlmBatch:
    """One paid extraction batch, already grouped into prompt-sized chunks."""

    chapters: List[Chapter]
    """Every chapter in the batch, read or not, so the scan scope is exact."""
    pages: List[Tuple[int, str]]
    """(chapter serial, plain text) for the chapters that have text."""
    chunks: List[List[Tuple[int, str]]]
    """Whole chapters grouped up to `LLM_CHUNK_CHARS`, one request each."""
    chars: int
    total_chapters: int


def _table(model: Any) -> Table:
    """The Core table behind a SQLModel class (pyright exposes no `__table__`)."""
    return cast(Table, getattr(model, "__table__"))


# A sentence boundary in Chinese prose, with the closing punctuation kept.
_SENTENCE = re.compile(r"[^。！？!?…\n]+[。！？!?…]?")


def _plain_text(html: str) -> str:
    return BeautifulSoup(str(html or ""), "html.parser").get_text(" ")


def _context_sentences(text: str, surface: str, limit: int = 2) -> List[str]:
    """Up to `limit` sentences of `text` that contain `surface`."""
    if not text or not surface:
        return []
    found: List[str] = []
    for match in _SENTENCE.finditer(text):
        sentence = match.group(0).strip()
        if surface in sentence:
            found.append(sentence[:240])
            if len(found) >= limit:
                break
    return found


def _mark_pattern(renderings: Sequence[str]) -> "re.Pattern[str]":
    """Build the glossary marking pattern, longest alternative first.

    A Latin rendering is matched on word boundaries, because a bare substring
    match wrapped `start` inside `starting` and stored `<span>start</span>ing`
    in the reader. Han and Hangul renderings need no boundary: they have no
    spaces to anchor to.
    """
    alternatives: List[str] = []
    for rendering in renderings:
        escaped = re.escape(rendering)
        if rendering[:1].isascii() and rendering[:1].isalnum():
            escaped = f"(?<![A-Za-z0-9]){escaped}"
        if rendering[-1:].isascii() and rendering[-1:].isalnum():
            escaped = f"{escaped}(?![A-Za-z0-9])"
        alternatives.append(escaped)
    return re.compile("|".join(alternatives))


class TermService:
    # ------------------------------------------------------------------
    # Stored decisions
    # ------------------------------------------------------------------

    def glossary(self, novel_id: str, language: str) -> Dict[str, str]:
        """Decided `source -> rendering` pairs for one target language."""
        with ctx.db.session() as sess:
            rows = sess.exec(
                sq.select(GlossaryTerm.source, GlossaryEntry.rendering)
                .join(GlossaryEntry, sq.col(GlossaryEntry.term_id) == sq.col(GlossaryTerm.id))
                .where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryEntry.language) == language,
                    sq.col(GlossaryEntry.rendering).is_not(None),
                )
            ).all()
        return {source: rendering for source, rendering in rows if rendering}

    def _rejected(self, sess: sq.Session, novel_id: str) -> Set[str]:
        """Surface keys on this novel's block-list."""
        return set(
            sess.exec(
                sq.select(GlossaryOverride.surface_key).where(
                    sq.col(GlossaryOverride.novel_id) == novel_id
                )
            ).all()
        )

    def _by_handle(
        self, novel_id: str, language: str, user_id: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Handle -> term row merged with the admin and reader renderings."""
        with ctx.db.session() as sess:
            terms = sess.exec(
                sq.select(GlossaryTerm).where(sq.col(GlossaryTerm.novel_id) == novel_id)
            ).all()
            entries = {
                entry.term_id: entry
                for entry in sess.exec(
                    sq.select(GlossaryEntry).where(sq.col(GlossaryEntry.language) == language)
                ).all()
            }
            overrides: Dict[str, str] = {}
            if user_id and terms:
                term_ids = [term.id for term in terms]
                for term_id, rendering in sess.exec(
                    sq.select(GlossaryUserEntry.term_id, GlossaryUserEntry.rendering).where(
                        sq.col(GlossaryUserEntry.user_id) == user_id,
                        sq.col(GlossaryUserEntry.language) == language,
                        sq.col(GlossaryUserEntry.term_id).in_(term_ids),
                    )
                ).all():
                    overrides[term_id] = rendering

        index: Dict[str, Dict[str, Any]] = {}
        rows = [(term, entries.get(term.id)) for term in terms]
        for term, entry in rows:
            index[term.handle] = {
                "term_id": term.id,
                "source": term.source,
                "reading": term.reading,
                "category": term.category.value if term.category else None,
                "freq": term.freq,
                "rendering": overrides.get(term.id) or (entry.rendering if entry else None),
                "admin_rendering": entry.rendering if entry else None,
                "override": overrides.get(term.id),
                "locked": entry.locked if entry else False,
            }
        return index

    # ------------------------------------------------------------------
    # Marking and rendering
    # ------------------------------------------------------------------

    def mark(self, html: str, novel_id: str, language: str) -> str:
        """Wrap every rendered term in `<span class="glossary-term" data-g=…>`.

        Only the target renderings that occur in this chapter are wrapped, and
        longest first, so a term containing another wins the match.
        """
        if not html:
            return html
        index = self._by_handle(novel_id, language)
        pairs: List[Tuple[str, str]] = sorted(
            (
                (entry["rendering"], handle)
                for handle, entry in index.items()
                if entry.get("rendering")
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        if not pairs:
            return html

        handle_of = {rendering: handle for rendering, handle in pairs}
        pattern = _mark_pattern([rendering for rendering, _ in pairs])
        soup = BeautifulSoup(html, "html.parser")
        for node in list(soup.find_all(string=True)):
            text = str(node)
            if not text.strip() or not pattern.search(text):
                continue
            parent = node.parent
            if parent is None or parent.name in ("script", "style"):
                continue
            if parent.get(TERM_ATTR):
                continue
            pieces: List[Any] = []
            cursor = 0
            for match in pattern.finditer(text):
                if match.start() > cursor:
                    pieces.append(NavigableString(text[cursor : match.start()]))
                span = soup.new_tag("span")
                span["class"] = "glossary-term"
                span[TERM_ATTR] = handle_of[match.group(0)]
                span.string = match.group(0)
                pieces.append(span)
                cursor = match.end()
            if cursor < len(text):
                pieces.append(NavigableString(text[cursor:]))
            node.replace_with(*pieces)
        return str(soup)

    def render(
        self,
        html: Optional[str],
        novel_id: str,
        language: str,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """Apply the current decisions to a stored chapter.

        A handle with no decision yet keeps the text it was stored with, so a
        chapter never turns into a blank where a term was.
        """
        if not html or TERM_ATTR not in html:
            return html
        soup = BeautifulSoup(html, "html.parser")
        spans = soup.select(f"[{TERM_ATTR}]")
        if not spans:
            return html
        handles = {str(span.get(TERM_ATTR)) for span in spans}
        index = self._by_handle(novel_id, language, user_id)
        for span in spans:
            entry = index.get(str(span.get(TERM_ATTR)))
            if entry and entry.get("rendering"):
                span.string = entry["rendering"]
        _ = handles
        return str(soup)

    def card(
        self, novel_id: str, language: str, handle: str, user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Everything a hover card needs for one handle."""
        return self._by_handle(novel_id, language, user_id).get(handle)

    # ------------------------------------------------------------------
    # Reader overrides
    # ------------------------------------------------------------------

    def set_override(
        self,
        user_id: str,
        novel_id: str,
        handle: str,
        language: str,
        rendering: Optional[str],
    ) -> Dict[str, Any]:
        """Set or clear one reader's meaning for one term."""
        with ctx.db.session() as sess:
            term = sess.exec(
                sq.select(GlossaryTerm).where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryTerm.handle) == handle,
                )
            ).first()
            if term is None:
                from ..exceptions import ServerErrors

                raise ServerErrors.no_such_term
            row = sess.exec(
                sq.select(GlossaryUserEntry).where(
                    sq.col(GlossaryUserEntry.user_id) == user_id,
                    sq.col(GlossaryUserEntry.term_id) == term.id,
                    sq.col(GlossaryUserEntry.language) == language,
                )
            ).first()
            value = str(rendering or "").strip()
            if not value:
                if row is not None:
                    sess.delete(row)
                    sess.commit()
                return {"handle": handle, "rendering": None}
            if row is None:
                row = GlossaryUserEntry(
                    user_id=user_id,
                    term_id=term.id,
                    language=language,
                    rendering=value,
                )
                sess.add(row)
            else:
                row.rendering = value
                sess.add(row)
            sess.commit()
        return {"handle": handle, "rendering": value}

    # ------------------------------------------------------------------
    # Wiping the glossary
    # ------------------------------------------------------------------

    def clear(self, novel_id: Optional[str] = None) -> Dict[str, int]:
        """Delete the glossary for one novel, or for every novel.

        Rows go child-first: a `data-g` handle only means anything while its
        term row exists, so an entry or proposal left behind is worse than no
        glossary at all. `novel_glossaries` is removed too, so a rebuilt
        glossary starts its revision counter from zero again.

        Chapter text and stored translations are not touched. A handle with no
        decision renders exactly the text the chapter was stored with, so the
        library keeps reading normally once this returns.
        """
        # Core statements over the tables, never the ORM entities: the ORM
        # bulk-delete path turns this predicate into a multi-table DELETE,
        # which SQLite refuses.
        term_table = _table(GlossaryTerm)
        term_ids = sq.select(term_table.c.id)
        if novel_id:
            term_ids = term_ids.where(term_table.c.novel_id == novel_id)
        children = (
            ("revisions", GlossaryRevision),
            ("candidates", GlossaryCandidate),
            ("user_entries", GlossaryUserEntry),
            ("entries", GlossaryEntry),
            ("counts", GlossaryTermCount),
        )
        by_novel = (
            ("terms", GlossaryTerm),
            ("overrides", GlossaryOverride),
            ("scans", GlossaryScan),
            ("headers", NovelGlossary),
        )
        counts: Dict[str, int] = {}
        with ctx.db.session() as sess:
            for name, model in children:
                table = _table(model)
                result = sess.execute(sq.delete(table).where(table.c.term_id.in_(term_ids)))
                # `Session.execute` is typed as a plain Result, while the
                # delete really returns a CursorResult that carries rowcount.
                counts[name] = int(getattr(result, "rowcount", 0) or 0)
            for name, model in by_novel:
                table = _table(model)
                stmt = sq.delete(table)
                if novel_id:
                    stmt = stmt.where(table.c.novel_id == novel_id)
                result = sess.execute(stmt)
                counts[name] = int(getattr(result, "rowcount", 0) or 0)
            sess.commit()
        return counts

    # ------------------------------------------------------------------
    # Admin glossary
    # ------------------------------------------------------------------

    def scan(
        self,
        novel_id: str,
        *,
        offset: int = 0,
        limit: int = 40,
        min_freq: int = 2,
        term_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Extract recurring surfaces from a batch of chapters.

        Every chapter of the batch is in scope, read or not, so the batch's
        per-chapter count rows are replaced as a unit: rescanning the same
        chapters leaves `freq` and the counters exactly where they were. The
        run itself is recorded in `glossary_scans`.

        `term_limit` is this batch's term cap, applied after every filter and
        reported back with `found_total`/`capped`. The old fixed 200 dropped the
        tail of a batch silently, so a truncated scan looked exactly like a
        complete one. One extra term is requested to make "the cap bit"
        observable. `blocked` counts the surfaces a rescan met and refused to
        re-store, and `hidden` the ones it refreshed that review had hidden.
        """
        from .glossary_extract import MAX_TERMS

        cap = max(1, int(term_limit or MAX_TERMS))
        chapters = ctx.chapters.list(novel_id=novel_id)
        batch = chapters[offset : offset + max(1, limit)]
        pages: List[Tuple[int, str]] = []
        for chapter in batch:
            if chapter.is_available:
                pages.append((chapter.serial, ctx.files.load_text(chapter.content_file)))
        found = extract_terms(
            pages,
            language=self.novel_language(ctx.novels.get(novel_id)),
            min_freq=min_freq,
            limit=cap + 1,
        )
        found_total = len(found)
        capped = found_total > cap
        if capped:
            found = found[:cap]
        numbers, scan_id = self._store_scan(
            novel_id,
            batch,
            found,
            scanned=len(pages),
            found_total=found_total,
            cap=cap,
            capped=capped,
            offset=offset,
            requested=max(1, limit),
        )
        return {
            "scan_id": scan_id,
            "scanned": len(pages),
            "offset": offset,
            "next_offset": offset + len(batch),
            "total_chapters": len(chapters),
            "found": len(found),
            "found_total": found_total,
            "new": numbers["new"],
            "updated": numbers["updated"],
            "hidden": numbers["hidden"],
            "blocked": numbers["blocked"],
            "cap": cap,
            "capped": capped,
        }

    def llm_batch(self, novel_id: str, offset: int, limit: int) -> LlmBatch:
        """The batch a paid extraction run would read, before spending anything.

        The same method serves the preflight estimate and the run itself, so the
        call count an admin is shown is the one that will be spent. Whole
        chapters are grouped, never split: the model needs a complete scene to
        tell a name from an ordinary phrase.
        """
        chapters = ctx.chapters.list(novel_id=novel_id)
        batch = list(chapters[offset : offset + max(1, limit)])
        pages = [
            (chapter.serial, _plain_text(ctx.files.load_text(chapter.content_file)))
            for chapter in batch
            if chapter.is_available
        ]
        chunks: List[List[Tuple[int, str]]] = []
        current: List[Tuple[int, str]] = []
        size = 0
        for page in pages:
            length = len(page[1])
            if current and size + length > LLM_CHUNK_CHARS:
                chunks.append(current)
                current, size = [], 0
            current.append(page)
            size += length
        if current:
            chunks.append(current)
        return LlmBatch(
            chapters=batch,
            pages=pages,
            chunks=chunks,
            chars=sum(len(text) for _, text in pages),
            total_chapters=len(chapters),
        )

    def store_llm_terms(
        self,
        novel_id: str,
        batch: LlmBatch,
        surfaces: Mapping[str, Optional[TermCategory]],
        *,
        model: str,
        offset: int = 0,
        requested: int = 0,
        term_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Count and store the surfaces a model named for one batch.

        The model proposes a surface and a category; the counts are ours. A
        surface the text does not contain is dropped rather than stored with a
        guessed frequency, which is how a paraphrase never becomes a glossary
        row with `freq: 0`. Nothing here decides a rendering: the judge and a
        human own that, exactly as they do for the free scan.
        """
        from .glossary_extract import MAX_TERMS, extract_terms

        cap = max(1, int(term_limit or MAX_TERMS))
        language = self.novel_language(ctx.novels.get(novel_id))
        profile = profile_for(language)
        per_chapter: Dict[str, Dict[int, int]] = {}
        display: Dict[str, str] = {}
        categories: Dict[str, Optional[TermCategory]] = {}
        for surface, category in surfaces.items():
            text = str(surface or "").strip()
            key = normalize_term_key(text)
            if not key or key in per_chapter or not plausible_surface(text):
                continue
            counts = {serial: page.count(text) for serial, page in batch.pages}
            counts = {serial: count for serial, count in counts.items() if count}
            if not counts:
                continue
            display[key] = text
            per_chapter[key] = counts
            categories[key] = category
        totals = {key: sum(counts.values()) for key, counts in per_chapter.items()}
        ranked = sorted(totals.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
        found = [
            ExtractedTerm(
                source=display[key],
                freq=freq,
                first_chapter_serial=min(per_chapter[key]),
                category=categories.get(key),
                reading=profile.reading(display[key]),
                chapter_counts=tuple(sorted(per_chapter[key].items())),
                found_by=model,
            )
            for key, freq in ranked[:cap]
        ]
        # The free scan reads the same batch alongside the model. jieba knows the
        # compounds a prompt skips (a fused sect name, a hull class) and costs no
        # quota, so the model's answer is a floor rather than the whole batch. Its
        # own picks keep their place; the scan fills what is left under the cap.
        known = {normalize_term_key(term.source) for term in found}
        added = 0
        for term in extract_terms(batch.pages, language=language, limit=cap):
            key = normalize_term_key(term.source)
            if key in known or len(found) >= cap:
                continue
            known.add(key)
            found.append(term)
            added += 1
        stored_model = min(len(ranked), cap)
        found_total = len(ranked) + added
        capped = found_total > cap
        numbers, scan_id = self._store_scan(
            novel_id,
            batch.chapters,
            found,
            scanned=len(batch.pages),
            found_total=found_total,
            cap=cap,
            capped=capped,
            offset=offset,
            requested=requested,
            found_by=model,
        )
        return {
            "scan_id": scan_id,
            "found": len(found),
            "found_total": found_total,
            "local": added,
            "dropped": len(surfaces) - stored_model,
            "cap": cap,
            "capped": capped,
            **numbers,
        }

    def _store_scan(
        self,
        novel_id: str,
        chapters: Sequence[Chapter],
        found: Sequence[ExtractedTerm],
        *,
        scanned: int,
        found_total: int,
        cap: int,
        capped: bool,
        offset: int,
        requested: int,
        found_by: Optional[str] = None,
    ) -> Tuple[Dict[str, int], Optional[str]]:
        """Write one extraction batch and its scan row; shared by both modes."""
        numbers = {"new": 0, "updated": 0, "blocked": 0, "hidden": 0}
        if not found and not chapters:
            return numbers, None
        with term_lock(novel_id), ctx.db.session() as sess:
            numbers = record_extracted_terms(
                sess,
                novel_id,
                found,
                chapter_ids=[chapter.id for chapter in chapters],
                found_by=found_by,
            )
            scan = GlossaryScan(
                novel_id=novel_id,
                first_chapter=offset,
                requested=requested,
                scanned=scanned,
                found=len(found),
                found_total=found_total,
                new_terms=numbers["new"],
                updated_terms=numbers["updated"],
                cap=cap,
                capped=capped,
            )
            sess.add(scan)
            sess.commit()
            return numbers, scan.id

    def apply_renderings(
        self,
        novel_id: str,
        language: str,
        proposals: Dict[str, str],
        *,
        engine: str,
    ) -> int:
        """Store engine proposals without touching a locked entry."""
        if not proposals:
            return 0
        changed = 0
        with glossary_lock(novel_id, language), ctx.db.session() as sess:
            for source, rendering in proposals.items():
                source = str(source or "").strip()
                rendering = str(rendering or "").strip()
                if not source or not rendering:
                    continue
                if apply_engine_proposal(
                    sess, novel_id, language, source, rendering, engine=engine
                ):
                    changed += 1
            if changed:
                bump_revision(sess, novel_id, language)
            sess.commit()
        return changed

    def set_rendering(
        self, novel_id: str, language: str, handle: str, rendering: Optional[str]
    ) -> bool:
        """Admin decision for one term, locked like an editor save."""
        value = str(rendering or "").strip()
        with glossary_lock(novel_id, language), ctx.db.session() as sess:
            term = sess.exec(
                sq.select(GlossaryTerm).where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryTerm.handle) == handle,
                )
            ).first()
            if term is None:
                return False
            entry = sess.exec(
                sq.select(GlossaryEntry).where(
                    sq.col(GlossaryEntry.term_id) == term.id,
                    sq.col(GlossaryEntry.language) == language,
                )
            ).first()
            if entry is None:
                if value:
                    create_manual_entry(sess, term.id, language, value)
            else:
                decide_manually(sess, entry, value or None)
            bump_revision(sess, novel_id, language)
            sess.commit()
        return True

    def set_category(self, novel_id: str, handle: str, category: Optional[str]) -> bool:
        with ctx.db.session() as sess:
            term = sess.exec(
                sq.select(GlossaryTerm).where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryTerm.handle) == handle,
                )
            ).first()
            if term is None:
                return False
            if category:
                coerced = coerce_category(category)
                if coerced is None:
                    return False
                term.category = coerced
            else:
                term.category = None
            sess.add(term)
            sess.commit()
        return True

    def _visible(self, stmt: Any, rejected: Set[str]) -> Any:
        """Restrict a term statement to what the review sheet shows by default.

        A verdict of skip or fragment hides a term, and so does being on the
        block-list; `NULL` is `undecided`, which must stay visible, so the test
        is written as `null OR not hidden` rather than `not in`.
        """
        stmt = stmt.where(
            sq.or_(
                sq.col(GlossaryTerm.verdict).is_(None),
                sq.col(GlossaryTerm.verdict).not_in(list(HIDDEN_VERDICTS)),
            )
        )
        if rejected:
            stmt = stmt.where(sq.col(GlossaryTerm.source_key).not_in(sorted(rejected)))
        return stmt

    def set_verdict(self, novel_id: str, handle: str, verdict: Optional[str]) -> bool:
        """Record what review decided; an empty value clears the verdict.

        `skip` and `fragment` take the term out of the default sheet and out of
        the paid tabs, which is how a reviewer stops the same noise being paid
        for twice.
        """
        text = str(verdict or "").strip().lower()
        value: Optional[TermVerdict] = None
        if text:
            try:
                value = TermVerdict(text)
            except ValueError:
                return False
        with ctx.db.session() as sess:
            term = sess.exec(
                sq.select(GlossaryTerm).where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryTerm.handle) == handle,
                )
            ).first()
            if term is None:
                return False
            term.verdict = value
            if value is not TermVerdict.merge:
                # `merge` is the only verdict that carries a target, so any
                # other decision releases the link it was pointing at.
                term.fragment_of = None
            sess.add(term)
            sess.commit()
        return True

    def block_term(self, novel_id: str, handle: str, reason: Optional[str] = None) -> bool:
        """Reject a surface for good: extraction never stores it again.

        The block-list is keyed by `source_key`, not by this term row, so the
        rejection survives the term being renamed, merged or deleted.
        """
        with ctx.db.session() as sess:
            term = sess.exec(
                sq.select(GlossaryTerm).where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryTerm.handle) == handle,
                )
            ).first()
            if term is None:
                return False
            note = str(reason or "").strip() or None
            row = sess.exec(
                sq.select(GlossaryOverride).where(
                    sq.col(GlossaryOverride.novel_id) == novel_id,
                    sq.col(GlossaryOverride.surface_key) == term.source_key,
                )
            ).first()
            if row is None:
                sess.add(
                    GlossaryOverride(
                        novel_id=novel_id,
                        surface=term.source,
                        surface_key=term.source_key,
                        reason=note,
                    )
                )
            elif note:
                row.reason = note
                sess.add(row)
            sess.commit()
        return True

    def unblock_term(self, novel_id: str, handle: str) -> bool:
        """Remove a surface from the block-list, so a rescan may store it again."""
        with ctx.db.session() as sess:
            term = sess.exec(
                sq.select(GlossaryTerm).where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryTerm.handle) == handle,
                )
            ).first()
            if term is None:
                return False
            table = _table(GlossaryOverride)
            sess.execute(
                sq.delete(table).where(
                    table.c.novel_id == novel_id,
                    table.c.surface_key == term.source_key,
                )
            )
            sess.commit()
        return True

    def merge_term(self, novel_id: str, handle: str, into: Optional[str]) -> bool:
        """Link a variant to its canonical term; an empty `into` unlinks it.

        This is how an alias group (`莎莎` ↔ `蒋莎莎`) or a hull class
        (`鬣狗级`) is recorded: the alias keeps its own row and handle, but
        shares the canonical term's decision instead of competing with it.
        `into` accepts a handle or a source surface, because a reviewer links
        by what they see.
        """
        with ctx.db.session() as sess:
            term = sess.exec(
                sq.select(GlossaryTerm).where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryTerm.handle) == handle,
                )
            ).first()
            if term is None:
                return False
            text = str(into or "").strip()
            if not text:
                term.fragment_of = None
                if term.verdict is TermVerdict.merge:
                    term.verdict = None
                sess.add(term)
                sess.commit()
                return True
            target = sess.exec(
                sq.select(GlossaryTerm).where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryTerm.id) != term.id,
                    sq.or_(
                        sq.col(GlossaryTerm.handle) == text,
                        sq.col(GlossaryTerm.source_key) == normalize_term_key(text),
                    ),
                )
            ).first()
            if target is None:
                return False
            term.fragment_of = target.id
            term.verdict = TermVerdict.merge
            sess.add(term)
            sess.commit()
        return True

    def decide_queue(self, novel_id: str, language: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Terms the paid pass still has something to decide, with evidence.

        One queue for verdict, category and rendering: a term is asked about
        once however many of its fields are open, so the three used to cost
        three calls and three looks at the same sentences. Only an open field
        keeps a term here — a settled verdict is never re-judged, and a merged
        alias has no rendering of its own to write.

        A bare surface (`浩然`) is ambiguous; two sentences from the chapter
        that uses it most are enough to tell a name from a phrase, and the
        per-chapter counts already planted by the scan say which chapter that
        is.
        """
        decided = sq.select(GlossaryEntry.term_id).where(
            sq.col(GlossaryEntry.language) == language,
            sq.col(GlossaryEntry.rendering).is_not(None),
        )
        with ctx.db.session() as sess:
            stmt = self._visible(
                sq.select(GlossaryTerm).where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.or_(
                        sq.col(GlossaryTerm.verdict).is_(None),
                        sq.and_(
                            sq.col(GlossaryTerm.verdict) != TermVerdict.merge,
                            sq.or_(
                                sq.col(GlossaryTerm.id).not_in(decided),
                                sq.col(GlossaryTerm.category).is_(None),
                            ),
                        ),
                    ),
                ),
                self._rejected(sess, novel_id),
            )
            terms = sess.exec(
                stmt.order_by(sq.desc(sq.col(GlossaryTerm.freq))).limit(max(1, limit))
            ).all()
            if not terms:
                return []
            term_ids = [term.id for term in terms]
            picks = sess.exec(
                sq.select(GlossaryTermCount.term_id, Chapter.serial)
                .join(Chapter, sq.col(Chapter.id) == sq.col(GlossaryTermCount.chapter_id))
                .where(sq.col(GlossaryTermCount.term_id).in_(term_ids))
                .order_by(sq.desc(sq.col(GlossaryTermCount.occurrences)))
            ).all()

        chapter_of: Dict[str, int] = {}
        for term_id, serial in picks:
            chapter_of.setdefault(term_id, serial)
        texts: Dict[str, str] = {}
        items: List[Dict[str, Any]] = []
        for term in terms:
            serial = chapter_of.get(term.id, term.first_chapter_serial or 0)
            path = Chapter.content_path(novel_id, serial)
            if path not in texts:
                # A chapter row can outlive its file when a crawl stopped early;
                # the term is still worth deciding without its evidence.
                texts[path] = (
                    _plain_text(ctx.files.load_text(path)) if ctx.files.exists(path) else ""
                )
            items.append(
                {
                    "handle": term.handle,
                    "source": term.source,
                    "reading": term.reading,
                    "freq": term.freq,
                    "first_chapter_serial": serial,
                    "context": _context_sentences(texts.get(path, ""), term.source),
                }
            )
        return items

    def apply_decisions(
        self,
        novel_id: str,
        language: str,
        answers: Sequence[Dict[str, Any]],
        *,
        model: str,
    ) -> Dict[str, int]:
        """Store one paid decision pass and report what it changed.

        An answer carries a verdict, a category and a rendering, and each is
        written only where it is still open: a verdict review already recorded
        stands, a category only fills an empty one, and a rendering never
        overwrites an entry an editor locked. The model is the last word on what
        a surface *is*, not on what a reviewer decided to call it.
        """
        numbers = {"stored": 0, "merged": 0, "categorized": 0, "rendered": 0}
        if not answers:
            return numbers
        with glossary_lock(novel_id, language), ctx.db.session() as sess:
            rejected = self._rejected(sess, novel_id)
            rows = sess.exec(
                sq.select(GlossaryTerm).where(sq.col(GlossaryTerm.novel_id) == novel_id)
            ).all()
            by_key = {term.source_key: term for term in rows if term.source_key not in rejected}
            for answer in answers:
                term = by_key.get(normalize_term_key(str(answer.get("source") or "")))
                if term is None:
                    continue
                changed = False
                verdict = coerce_verdict(answer.get("verdict"))
                if verdict is not None and verdict is not term.verdict:
                    if verdict is TermVerdict.merge:
                        target = by_key.get(normalize_term_key(str(answer.get("merge_into") or "")))
                        if target is None or target.id == term.id:
                            verdict = None
                        else:
                            term.fragment_of = target.id
                            numbers["merged"] += 1
                    elif term.verdict is TermVerdict.merge:
                        term.fragment_of = None
                    if verdict is not None:
                        term.verdict = verdict
                        term.judged_by = model
                        term.judged_at = current_timestamp()
                        changed = True
                if (verdict or term.verdict) is TermVerdict.keep:
                    category = coerce_category(answer.get("category"))
                    if category and term.category is None:
                        term.category = category
                        numbers["categorized"] += 1
                        changed = True
                    rendering = str(answer.get("rendering") or "").strip()
                    if rendering and apply_engine_proposal(
                        sess, novel_id, language, term.source, rendering, engine=model
                    ):
                        numbers["rendered"] += 1
                        changed = True
                if changed:
                    sess.add(term)
                    numbers["stored"] += 1
            sess.commit()
        return numbers

    def list_terms(
        self,
        novel_id: str,
        language: str,
        *,
        user_id: Optional[str] = None,
        category: Optional[str] = None,
        found_by: Optional[str] = None,
        query: str = "",
        offset: int = 0,
        limit: int = 50,
        show_hidden: bool = False,
    ) -> Dict[str, Any]:
        """The review sheet: every term with its current decision.

        `show_hidden` reveals what the default view keeps out of the way: terms
        review hid (skip/fragment) and surfaces on the block-list, so they can be
        unhidden or unblocked. `found_by` scores one extraction mode at a time —
        `local` or a model id — which is how the two modes are compared on the
        same batch. `limit=0` means "all rows", capped at `NO_LIMIT` so one
        request still cannot walk an unbounded table.
        """
        page = NO_LIMIT if limit <= 0 else limit
        with ctx.db.session() as sess:
            rejected = self._rejected(sess, novel_id)
            stmt = sq.select(GlossaryTerm).where(sq.col(GlossaryTerm.novel_id) == novel_id)
            if category:
                stmt = stmt.where(GlossaryTerm.category == TermCategory(category))
            if found_by:
                stmt = stmt.where(sq.col(GlossaryTerm.found_by) == found_by)
            if query:
                stmt = stmt.where(sq.col(GlossaryTerm.source).ilike(f"%{query}%"))
            if not show_hidden:
                stmt = self._visible(stmt, rejected)
            rows = sess.exec(
                stmt.order_by(sq.desc(sq.col(GlossaryTerm.freq))).offset(offset).limit(page)
            ).all()
            seen: Dict[str, int] = {}
            if rows:
                term_ids = [term.id for term in rows]
                for term_id, total in sess.exec(
                    sq.select(GlossaryTermCount.term_id, sq.func.count())
                    .where(sq.col(GlossaryTermCount.term_id).in_(term_ids))
                    .group_by(sq.col(GlossaryTermCount.term_id))
                ).all():
                    seen[str(term_id)] = int(total or 0)
            parents: Dict[str, str] = {}
            parent_ids = {term.fragment_of for term in rows if term.fragment_of}
            if parent_ids:
                parents = dict(
                    sess.exec(
                        sq.select(GlossaryTerm.id, GlossaryTerm.source).where(
                            sq.col(GlossaryTerm.id).in_(list(parent_ids))
                        )
                    ).all()
                )
            revision = sess.exec(
                sq.select(NovelGlossary.revision).where(
                    sq.col(NovelGlossary.novel_id) == novel_id,
                    sq.col(NovelGlossary.language) == language,
                )
            ).first()
            # Every source this novel has seen, with how many terms it owns, so
            # the filter offers the modes that actually stored something and the
            # two can be compared at a glance.
            sources = sess.exec(
                sq.select(GlossaryTerm.found_by, sq.func.count())
                .where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryTerm.found_by).is_not(None),
                )
                .group_by(sq.col(GlossaryTerm.found_by))
            ).all()
        index = self._by_handle(novel_id, language, user_id)
        items = []
        for term in rows:
            merged = index.get(term.handle) or {}
            items.append(
                {
                    "handle": term.handle,
                    "source": term.source,
                    "reading": term.reading,
                    "category": term.category.value if term.category else None,
                    "freq": term.freq,
                    "first_chapter_serial": term.first_chapter_serial,
                    "rendering": merged.get("admin_rendering"),
                    "locked": merged.get("locked", False),
                    "override": merged.get("override"),
                    "verdict": term.verdict.value if term.verdict else None,
                    "judged_by": term.judged_by,
                    "found_by": term.found_by,
                    "merged_into": parents.get(term.fragment_of or ""),
                    "blocked": term.source_key in rejected,
                    "chapters": seen.get(term.id, 0),
                }
            )
        return {
            "revision": revision or 0,
            "sources": sorted(
                (
                    {"name": str(source), "count": int(count or 0)}
                    for source, count in sources
                    if source
                ),
                key=lambda source: source["name"],
            ),
            "items": items,
        }

    def novel_language(self, novel: Novel) -> Optional[str]:
        """The novel's source language: stored value first, detection second.

        A crawled novel carries the code its crawler detected; an imported one
        may not, and extraction still has to choose a profile. One chapter is
        enough of a sample. Nothing is written back: the admin owns
        `Novel.language`, and a read must not change it.
        """
        stored = str(getattr(novel, "language", "") or "").strip()
        if stored:
            return stored
        for chapter in ctx.chapters.list(novel_id=novel.id):
            if not chapter.is_available:
                continue
            text = _plain_text(ctx.files.load_text(chapter.content_file))
            if text:
                return ctx.translator.detect_language(text[:4000])
        return None

    # ------------------------------------------------------------------
    # Per-novel translation style
    # ------------------------------------------------------------------

    def translation_style(self, novel_id: str) -> Dict[str, Any]:
        """The novel's rendering style, with defaults filled in and validated.

        Honorific policy, name order and enabled categories steer the paid
        prompts and the review sheet's category picker; the free scan is not
        affected, because its rules are language rules, not house style.
        """
        novel = ctx.novels.get(novel_id)
        return _clean_style((novel.extra or {}).get(STYLE_KEY) if novel else None)

    def set_translation_style(
        self,
        novel_id: str,
        *,
        honorifics: Optional[str] = None,
        name_order: Optional[str] = None,
        categories: Optional[Sequence[str]] = None,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Store one novel's style and/or source language; only what is sent changes.

        An empty `language` clears the override, putting the novel back on
        detection. An empty `categories` means every category is enabled.
        """
        from ..exceptions import ServerErrors

        with ctx.db.session() as sess:
            novel = sess.get(Novel, novel_id)
            if novel is None:
                raise ServerErrors.no_such_novel
            # A setting that is sent must be one the prompts understand; a
            # setting that was stored is only ever repaired, never rejected.
            style = _clean_style((novel.extra or {}).get(STYLE_KEY))
            if honorifics is not None:
                value = str(honorifics).strip().lower()
                if value not in ("", *HONORIFIC_POLICIES):
                    raise ServerErrors.invalid_input.with_extra(
                        f"Unknown honorific policy {value!r}; expected strip or keep."
                    )
                style["honorifics"] = value or STYLE_DEFAULTS["honorifics"]
            if name_order is not None:
                value = str(name_order).strip().lower()
                if value not in ("", *NAME_ORDERS):
                    raise ServerErrors.invalid_input.with_extra(
                        f"Unknown name order {value!r}; expected family-given or given-family."
                    )
                style["name_order"] = value or STYLE_DEFAULTS["name_order"]
            if categories is not None:
                style["categories"] = _clean_categories(categories)
            extra = dict(novel.extra or {})
            extra[STYLE_KEY] = style
            novel.extra = extra
            if language is not None:
                # The column is CHAR(2), so `zh-CN` is stored as `zh`.
                novel.language = str(language).strip().lower()[:2] or None
            sess.commit()
        return style

    def style_report(self, novel_id: str) -> Dict[str, Any]:
        """Everything the style card needs: language, profile, settings.

        `requested_code` is the language the novel asks for and `profile.code`
        what actually serves it, so the panel can say plainly when a missing
        dependency degraded this novel to the fallback profile.
        """
        novel = ctx.novels.get(novel_id)
        stored = str(getattr(novel, "language", "") or "")
        resolved = self.novel_language(novel)
        profile = profile_for(resolved)
        return {
            "language": resolved,
            "stored_language": stored or None,
            "detected_language": resolved if resolved and not stored else None,
            "requested_code": base_code(resolved),
            "profile": profile.describe(),
            "profiles": describe_profiles(),
            "style": self.translation_style(novel_id),
            "honorific_policies": list(HONORIFIC_POLICIES),
            "name_orders": list(NAME_ORDERS),
            "categories": [category.value for category in TermCategory],
        }

    def chapter_batch(self, novel_id: str, offset: int, limit: int) -> Sequence[Chapter]:
        return ctx.chapters.list(novel_id=novel_id)[offset : offset + limit]
