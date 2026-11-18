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
from typing import Dict, Optional, Sequence

import sqlmodel as sq

from ..dao import (
    GlossaryCandidate,
    GlossaryEntry,
    GlossaryOverride,
    GlossaryRevision,
    GlossaryTerm,
    NovelGlossary,
)
from ..enums import TermStrategy
from ..utils.crypto_tools import generate_term_handle
from ..utils.text_tools import normalize_rendering_key, normalize_term_key
from .glossary_extract import ExtractedTerm

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
) -> int:
    """Store a pre-pass batch and return how many terms it changed.

    A rejected surface (`glossary_overrides`) is never stored, so a false
    positive stays gone however often the scanner meets it again.
    """
    if not terms:
        return 0
    rejected = set(
        sess.exec(
            sq.select(GlossaryOverride.surface_key).where(
                sq.col(GlossaryOverride.novel_id) == novel_id
            )
        ).all()
    )
    changed = 0
    for extracted in terms:
        source_key = normalize_term_key(extracted.source)
        if not source_key or source_key in rejected:
            continue
        term = sess.exec(
            sq.select(GlossaryTerm).where(
                sq.col(GlossaryTerm.novel_id) == novel_id,
                sq.col(GlossaryTerm.source_key) == source_key,
            )
        ).first()
        if term is None:
            term = get_or_create_term(sess, novel_id, extracted.source)
            term.freq = extracted.freq
            term.first_chapter_serial = extracted.first_chapter_serial
            term.pinyin = extracted.pinyin
            term.category = extracted.category
            sess.add(term)
            changed += 1
        elif _absorb(term, extracted):
            sess.add(term)
            changed += 1
    return changed


def _absorb(term: GlossaryTerm, extracted: ExtractedTerm) -> bool:
    """Fold one scan into a known term without replacing anything already decided."""
    changed = False
    if extracted.freq:
        term.freq += extracted.freq
        changed = True
    serial = extracted.first_chapter_serial
    if term.first_chapter_serial is None or (
        serial is not None and serial < term.first_chapter_serial
    ):
        term.first_chapter_serial = serial
        changed = True
    if not term.pinyin and extracted.pinyin:
        term.pinyin = extracted.pinyin
        changed = True
    if not term.category and extracted.category:
        term.category = extracted.category
        changed = True
    return changed


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
