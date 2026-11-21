"""Glossary write helpers shared by the API editor and the translation engine.

A decision is keyed by (term, target language): terms are novel-level identity,
entries are per-language decisions. Editor writes lock their entry; an engine
merge may fill an unlocked entry but never touches a locked one. Every version
bump appends a `glossary_revisions` row, so an entry's `version` and the log
cannot disagree.

Terms arrive two ways: the frequency pre-pass (`record_extracted_terms`) stores
what recurs in the source text before any chapter is translated, and an engine
merge (`get_or_create_term`) adds whatever it meets while translating. Every
proposal is also archived as a candidate, rejected or not, which is what the
review sheet shows.

The per-(novel, language) lock lives here because both writers read-modify-write
the same entry set: without it an editor save racing a chapter merge can create
the same entry twice, or one can clobber the other's version. A term row is
shared by every language, so scans take the novel-wide lock instead.
"""

from threading import Lock
from typing import Dict, Optional, Sequence, Set, Tuple, cast

from sqlalchemy import Table
import sqlmodel as sq

from ..dao import (
    Chapter,
    GlossaryCandidate,
    GlossaryEntry,
    GlossaryOverride,
    GlossaryRevision,
    GlossaryTerm,
    GlossaryTermCount,
    NovelGlossary,
)
from ..enums import HIDDEN_VERDICTS, TermStrategy
from ..utils.crypto_tools import generate_term_handle
from ..utils.text_tools import normalize_rendering_key, normalize_term_key
from .glossary_extract import ExtractedTerm
from .llm_json import normalize_rendering

_locks: Dict[str, Lock] = {}
_locks_guard = Lock()


def glossary_lock(novel_id: str, language: str) -> Lock:
    """The lock guarding one glossary: both writers must hold it."""
    key = f"{novel_id}:{language}"
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = Lock()
            _locks[key] = lock
        return lock


def term_lock(novel_id: str) -> Lock:
    """The lock guarding a novel's term rows, which every language shares."""
    return glossary_lock(novel_id, "")


def bump_revision(sess: sq.Session, novel_id: str, language: str) -> None:
    """Advance a language's glossary version by one batch of changes.

    Called only when something actually changed, so the header row is created
    by the first real write rather than by a no-op request.
    """
    row = sess.exec(
        sq.select(NovelGlossary).where(
            sq.col(NovelGlossary.novel_id) == novel_id,
            sq.col(NovelGlossary.language) == language,
        )
    ).first()
    if row is None:
        row = NovelGlossary(novel_id=novel_id, language=language, revision=1)
    else:
        row.revision += 1
    sess.add(row)


def get_or_create_term(sess: sq.Session, novel_id: str, source: str) -> GlossaryTerm:
    """The term row for `source`, minting a stable `handle` the first time."""
    source_key = normalize_term_key(source)
    term = sess.exec(
        sq.select(GlossaryTerm).where(
            sq.col(GlossaryTerm.novel_id) == novel_id,
            sq.col(GlossaryTerm.source_key) == source_key,
        )
    ).first()
    if term is None:
        term = GlossaryTerm(
            novel_id=novel_id,
            source=source,
            source_key=source_key,
            handle=_new_handle(sess),
        )
        sess.add(term)
        sess.flush()
    return term


def record_extracted_terms(
    sess: sq.Session,
    novel_id: str,
    terms: Sequence[ExtractedTerm],
    *,
    chapter_ids: Optional[Sequence[str]] = None,
    found_by: Optional[str] = None,
) -> Dict[str, int]:
    """Store a pre-pass batch and return `{"new": n, "updated": m}`.

    Occurrences are stored per chapter and the batch's own rows are replaced,
    so running the same scan twice leaves every number exactly as it was. With
    `found_by` set to a model id the replacement is narrowed to that run's own
    surfaces, so a paid extraction can cover a batch the free scan already read
    without zeroing its frequencies.
    `freq` is derived as the sum of those rows, never incremented, which is why
    a repeat is not a discovery: the extraction tab used to print the sum of
    the old counters as "new", and four identical runs looked like four
    separate discoveries.

    A rejected surface (`glossary_overrides`) is never stored, so a false
    positive stays gone however often the scanner meets it again. A term's
    `freq` before this change is an aggregate with no per-chapter rows behind
    it; it is replaced the first time a scan covers that term's chapters.
    """
    if not terms and not chapter_ids:
        return {"new": 0, "updated": 0, "blocked": 0, "hidden": 0}
    rejected = set(
        sess.exec(
            sq.select(GlossaryOverride.surface_key).where(
                sq.col(GlossaryOverride.novel_id) == novel_id
            )
        ).all()
    )
    batch = _batch_chapters(sess, novel_id, terms, chapter_ids)
    scope = set(batch.values())
    if str(found_by or "local") == "local":
        touched = _drop_batch_counts(sess, scope)
    else:
        # A paid extractor names a handful of surfaces, not every term of the
        # batch, so it replaces only the rows of the terms it stores. Treating
        # it as authoritative for the whole batch would zero the free scan's
        # numbers for the same chapters, which is the one comparison P5b exists
        # to make.
        touched = _drop_scoped_counts(sess, novel_id, scope, [term.source for term in terms])

    counts = {"new": 0, "updated": 0, "blocked": 0, "hidden": 0}
    stored: Dict[str, GlossaryTerm] = {}
    before: Dict[str, Tuple[object, ...]] = {}
    for extracted in terms:
        source_key = normalize_term_key(extracted.source)
        if not source_key:
            continue
        if source_key in rejected:
            # The block-list is permanent: counting these is what makes it
            # visible that a rescan met a rejected surface and stayed away.
            counts["blocked"] += 1
            continue
        term = sess.exec(
            sq.select(GlossaryTerm).where(
                sq.col(GlossaryTerm.novel_id) == novel_id,
                sq.col(GlossaryTerm.source_key) == source_key,
            )
        ).first()
        if term is None:
            term = get_or_create_term(sess, novel_id, extracted.source)
            term.first_chapter_serial = extracted.first_chapter_serial
            term.reading = extracted.reading
            term.category = extracted.category
            term.found_by = extracted.found_by or "local"
            sess.add(term)
            counts["new"] += 1
        else:
            if term.verdict in HIDDEN_VERDICTS:
                # Counts are still replaced (the text really does contain it),
                # but the reviewer is told this batch is refreshing a hidden
                # term rather than discovering something new.
                counts["hidden"] += 1
            before[term.id] = _state(term)
            _absorb(term, extracted)
            sess.add(term)
        stored[term.id] = term
        for serial, count in extracted.chapter_counts:
            chapter_id = batch.get(serial)
            if chapter_id and count > 0:
                sess.add(
                    GlossaryTermCount(term_id=term.id, chapter_id=chapter_id, occurrences=count)
                )
    sess.flush()

    changed_ids = touched | set(stored)
    if not changed_ids:
        return counts
    missing = changed_ids - set(stored)
    if missing:
        for term in sess.exec(
            sq.select(GlossaryTerm).where(sq.col(GlossaryTerm.id).in_(list(missing)))
        ).all():
            stored[term.id] = term
    totals = {
        term_id: int(total or 0)
        for term_id, total in sess.exec(
            sq.select(GlossaryTermCount.term_id, sq.func.sum(GlossaryTermCount.occurrences))
            .where(sq.col(GlossaryTermCount.term_id).in_(list(changed_ids)))
            .group_by(sq.col(GlossaryTermCount.term_id))
        ).all()
    }
    for term_id, term in stored.items():
        term.freq = totals.get(term_id, 0)
        sess.add(term)
    counts["updated"] = sum(
        1 for term_id, state in before.items() if _state(stored[term_id]) != state
    )
    return counts


def _batch_chapters(
    sess: sq.Session,
    novel_id: str,
    terms: Sequence[ExtractedTerm],
    chapter_ids: Optional[Sequence[str]],
) -> Dict[int, str]:
    """Serial -> chapter id for the chapters this batch read.

    The scope comes from `chapter_ids` when the caller knows it, because a
    chapter that no longer yields a surface still has to lose the row it had.
    """
    stmt = sq.select(Chapter.id, Chapter.serial).where(sq.col(Chapter.novel_id) == novel_id)
    if chapter_ids is not None:
        if not chapter_ids:
            return {}
        stmt = stmt.where(sq.col(Chapter.id).in_(list(chapter_ids)))
    else:
        serials = {serial for term in terms for serial, _ in term.chapter_counts}
        if not serials:
            return {}
        stmt = stmt.where(sq.col(Chapter.serial).in_(serials))
    return {serial: chapter_id for chapter_id, serial in sess.exec(stmt).all()}


def _drop_scoped_counts(
    sess: sq.Session, novel_id: str, chapter_ids: Set[str], surfaces: Sequence[str]
) -> Set[str]:
    """Clear the rows a paid run owns: its own terms, in this batch's chapters.

    Same delete as `_drop_batch_counts`, narrowed to the surfaces this run
    names, which is what lets two extraction modes cover one batch without
    erasing each other's frequencies.
    """
    if not chapter_ids or not surfaces:
        return set()
    keys = sorted({normalize_term_key(surface) for surface in surfaces if surface})
    if not keys:
        return set()
    term_ids = set(
        sess.exec(
            sq.select(GlossaryTerm.id).where(
                sq.col(GlossaryTerm.novel_id) == novel_id,
                sq.col(GlossaryTerm.source_key).in_(keys),
            )
        ).all()
    )
    if not term_ids:
        return set()
    scope = list(chapter_ids)
    table = cast(Table, getattr(GlossaryTermCount, "__table__"))
    sess.execute(
        sq.delete(table).where(table.c.chapter_id.in_(scope), table.c.term_id.in_(list(term_ids)))
    )
    sess.flush()
    return term_ids


def _drop_batch_counts(sess: sq.Session, chapter_ids: Set[str]) -> Set[str]:
    """Clear this batch's rows and return the terms whose counts they were."""
    if not chapter_ids:
        return set()
    scope = list(chapter_ids)
    touched = set(
        sess.exec(
            sq.select(GlossaryTermCount.term_id)
            .where(sq.col(GlossaryTermCount.chapter_id).in_(scope))
            .distinct()
        ).all()
    )
    # Core delete: the ORM form emits DELETE ... USING with RETURNING here,
    # which SQLite refuses for a plain `IN (…)` predicate.
    table = cast(Table, getattr(GlossaryTermCount, "__table__"))
    sess.execute(sq.delete(table).where(table.c.chapter_id.in_(scope)))
    sess.flush()
    return touched


def _state(term: GlossaryTerm) -> Tuple[object, ...]:
    """The stored values a scan may change, compared before and after."""
    return (term.freq, term.first_chapter_serial, term.reading, term.category)


def _absorb(term: GlossaryTerm, extracted: ExtractedTerm) -> None:
    """Fill in what a known term is missing without replacing anything decided.

    Occurrences live in `GlossaryTermCount`, so nothing here touches `freq`.
    """
    serial = extracted.first_chapter_serial
    if term.first_chapter_serial is None or (
        serial is not None and serial < term.first_chapter_serial
    ):
        term.first_chapter_serial = serial
    if not term.reading and extracted.reading:
        term.reading = extracted.reading
    if not term.category and extracted.category:
        term.category = extracted.category
    # Provenance is filled, never replaced: what first found a surface is the
    # answer a reviewer comparing the two modes is looking for.
    if not term.found_by and extracted.found_by:
        term.found_by = extracted.found_by


def log_revision(
    sess: sq.Session,
    entry: GlossaryEntry,
    *,
    engine: Optional[str] = None,
    user_id: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Append the entry's current state to the change log."""
    sess.add(
        GlossaryRevision(
            term_id=entry.term_id,
            language=entry.language,
            version=entry.version,
            rendering=entry.rendering,
            engine=engine,
            user_id=user_id,
            note=note,
        )
    )


def create_manual_entry(
    sess: sq.Session,
    term_id: str,
    language: str,
    rendering: str,
    *,
    user_id: Optional[str] = None,
) -> GlossaryEntry:
    """A new editor decision: locked from birth."""
    entry = GlossaryEntry(
        term_id=term_id,
        language=language,
        rendering=rendering,
        locked=True,
        version=1,
    )
    sess.add(entry)
    log_revision(sess, entry, user_id=user_id, note="added by editor")
    return entry


def decide_manually(
    sess: sq.Session,
    entry: GlossaryEntry,
    rendering: Optional[str],
    *,
    user_id: Optional[str] = None,
) -> None:
    """Apply an editor decision to an existing entry, versioned and logged.

    `rendering=None` makes the entry undecided again instead of deleting it, so
    the term keeps its identity, handle and revision history.
    """
    entry.rendering = rendering
    entry.locked = rendering is not None
    entry.version += 1
    sess.add(entry)
    log_revision(
        sess,
        entry,
        user_id=user_id,
        note="edited by editor" if rendering else "removed by editor",
    )


def record_candidate(
    sess: sq.Session,
    term_id: str,
    language: str,
    rendering: str,
    *,
    engine: Optional[str] = None,
    strategy: TermStrategy = TermStrategy.literal,
    chosen: bool = False,
) -> None:
    """Archive a rendering proposal: a repeat is a vote, and only one stays chosen."""
    normalized = normalize_rendering_key(rendering)
    if not normalized:
        return
    row = sess.exec(
        sq.select(GlossaryCandidate).where(
            sq.col(GlossaryCandidate.term_id) == term_id,
            sq.col(GlossaryCandidate.language) == language,
            sq.col(GlossaryCandidate.normalized) == normalized,
        )
    ).first()
    if row is None:
        sess.add(
            GlossaryCandidate(
                term_id=term_id,
                language=language,
                engine=engine or "unknown",
                strategy=strategy,
                rendering=rendering,
                normalized=normalized,
                votes=1,
                chosen=chosen,
            )
        )
    else:
        row.votes += 1
        row.chosen = row.chosen or chosen
        sess.add(row)
    if chosen:
        sess.exec(
            sq.update(GlossaryCandidate)
            .where(
                sq.col(GlossaryCandidate.term_id) == term_id,
                sq.col(GlossaryCandidate.language) == language,
                sq.col(GlossaryCandidate.normalized) != normalized,
            )
            .values(chosen=False)
        )


def apply_engine_proposal(
    sess: sq.Session,
    novel_id: str,
    language: str,
    source: str,
    rendering: str,
    *,
    engine: Optional[str] = None,
) -> bool:
    """Store an engine proposal unless an editor has locked the entry.

    Every proposal is archived as a candidate, including one a lock rejects:
    those alternatives are exactly what the review sheet offers.
    """
    term = get_or_create_term(sess, novel_id, source)
    # Store-time casing: an engine answer and a later re-answer must not differ
    # in style because two model versions disagree (BUG-11).
    rendering = normalize_rendering(rendering, term.category)
    entry = sess.exec(
        sq.select(GlossaryEntry).where(
            sq.col(GlossaryEntry.term_id) == term.id,
            sq.col(GlossaryEntry.language) == language,
        )
    ).first()
    stored = False
    if entry is None:
        entry = GlossaryEntry(
            term_id=term.id,
            language=language,
            rendering=rendering,
            version=1,
        )
        sess.add(entry)
        stored = True
    elif not entry.locked and entry.rendering != rendering:
        entry.rendering = rendering
        entry.version += 1
        sess.add(entry)
        stored = True

    if stored:
        log_revision(sess, entry, engine=engine, note="engine merge")
    record_candidate(
        sess,
        term.id,
        language,
        rendering,
        engine=engine,
        chosen=not entry.locked and entry.rendering == rendering,
    )
    return stored


def _new_handle(sess: sq.Session) -> str:
    for _ in range(8):
        handle = generate_term_handle()
        taken = sess.exec(
            sq.select(GlossaryTerm.id).where(sq.col(GlossaryTerm.handle) == handle)
        ).first()
        if not taken:
            return handle
    raise RuntimeError("Unable to generate an unused glossary term handle")
