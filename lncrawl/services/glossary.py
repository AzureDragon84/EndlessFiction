"""Glossary write helpers shared by the API editor and the translation engine.

A decision is keyed by (term, target language): terms are novel-level identity,
entries are per-language decisions. Editor writes lock their entry; an engine
merge may fill an unlocked entry but never touches a locked one. Every version
bump appends a `glossary_revisions` row, so an entry's `version` and the log
cannot disagree.

Terms arrive three ways: a chapter scan (`scan_novel_terms`) records what the
extraction methods propose, an imported list (`import_glossary_terms`) stores
terms an editor already knows, and an engine merge (`get_or_create_term`) adds
whatever it meets while translating. Only engine merges archive candidates.

The per-(novel, language) lock lives here because both writers read-modify-write
the same entry set: without it an editor save racing a chapter merge can create
the same entry twice, or one can clobber the other's version. A term row is
shared by every language, so scans take the novel-wide lock instead.
"""

import logging
from threading import Lock
import time
from typing import Any, Dict, List, Optional, Sequence

import sqlmodel as sq

from ..context import ctx
from ..dao import (
    GlossaryCandidate,
    GlossaryEntry,
    GlossaryOccurrence,
    GlossaryOverride,
    GlossaryRevision,
    GlossaryScan,
    GlossaryTerm,
    Novel,
    NovelGlossary,
)
from ..enums import TermStrategy, TermTier
from ..exceptions import ServerErrors
from ..utils.crypto_tools import generate_term_handle
from ..utils.text_tools import normalize_rendering_key, normalize_term_key

logger = logging.getLogger(__name__)

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


def get_or_create_term(
    sess: sq.Session,
    novel_id: str,
    source: str,
    *,
    origin: str = "engine",
) -> GlossaryTerm:
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
            origin=origin,
        )
        sess.add(term)
        sess.flush()
    return term


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


def scan_novel_terms(
    novel_id: str,
    *,
    extractors: Optional[Sequence[str]] = None,
    limit: int = 50,
    min_freq: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Read up to `limit` uncovered chapters and store what they propose.

    Coverage is per (chapter, method): a chapter already scanned with every
    requested method is skipped, so calling this repeatedly walks the novel to
    the end instead of re-reading the same first chapters. `force` re-reads
    covered chapters, replacing that pass's occurrences rather than adding to
    them. Text is read before the term lock is taken, because reading is the
    slow part and needs no shared state; the lock covers only the write.
    """
    from ..dao import Chapter
    from .glossary_extract import extract_terms, resolve_extractors

    started = time.monotonic()
    with ctx.db.session() as sess:
        novel = sess.get(Novel, novel_id)
        if novel is None:
            raise ServerErrors.no_such_novel
        language = novel.language or None
        methods = resolve_extractors(extractors, language)
        method_ids = [method.id for method in methods]
        serials = _chapter_serials(sess, novel_id)
        covered = _covered_serials(sess, novel_id)

    selected: List[int] = []
    for serial in serials:
        if not force and all(method in covered.get(serial, ()) for method in method_ids):
            continue
        selected.append(serial)
        if len(selected) >= max(1, limit):
            break

    pages: List[tuple] = []
    unreadable = 0
    for serial in selected:
        try:
            pages.append((serial, ctx.files.load_text(Chapter.content_path(novel_id, serial))))
        except Exception as error:
            logger.warning(f"glossary scan skipped chapter {serial}: {error}")
            unreadable += 1
            pages.append((serial, ""))

    batch = extract_terms(
        pages,
        language=language,
        extractors=method_ids,
        min_freq=min_freq,
    )
    with term_lock(novel_id), ctx.db.session() as sess:
        changed = _persist_batch(sess, novel_id, batch)
        sess.commit()
        status = _scan_status(sess, novel_id, method_ids)

    return {
        "scanned": len(pages) - unreadable,
        "terms": changed,
        "occurrences": batch.occurrences,
        "suppressed": batch.suppressed,
        "fragments": batch.fragments,
        "truncated": batch.truncated,
        "unreadable": unreadable,
        "extractors": method_ids,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        **status,
    }


def scan_chapter_terms(
    novel_id: str,
    serial: int,
    text: str,
    language: Optional[str],
    *,
    extractors: Optional[Sequence[str]] = None,
    min_freq: Optional[int] = None,
) -> int:
    """Scan one chapter in flight, from the translation pre-pass.

    Same write path as a batch scan, so a chapter translated before the review
    screen ran is already covered and its numbers are not counted twice.
    """
    from .glossary_extract import extract_terms

    batch = extract_terms(
        [(serial, text)],
        language=language,
        extractors=extractors,
        min_freq=min_freq,
    )
    if not batch.terms:
        return 0
    with term_lock(novel_id), ctx.db.session() as sess:
        changed = _persist_batch(sess, novel_id, batch)
        sess.commit()
        return changed


def glossary_scan_status(
    novel_id: str,
    *,
    extractors: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """How much of a novel the chosen methods have covered, and what comes next."""
    from .glossary_extract import resolve_extractors

    with ctx.db.session() as sess:
        novel = sess.get(Novel, novel_id)
        if novel is None:
            raise ServerErrors.no_such_novel
        method_ids = [
            method.id for method in resolve_extractors(extractors, novel.language or None)
        ]
        per_method = {
            str(extractor): int(count)
            for extractor, count in sess.exec(
                sq.select(GlossaryScan.extractor, sq.func.count())
                .where(sq.col(GlossaryScan.novel_id) == novel_id)
                .group_by(sq.col(GlossaryScan.extractor))
            ).all()
        }
        status = _scan_status(sess, novel_id, method_ids)
    return {"extractors": method_ids, "per_method": per_method, **status}


def import_glossary_terms(novel_id: str, terms: Sequence[str]) -> Dict[str, int]:
    """Store a pasted term list: no chapter is read and no count is recorded.

    An import never writes an entry, so an existing decision — locked or not —
    survives it, and a surface that was rejected stays rejected.
    """
    with term_lock(novel_id), ctx.db.session() as sess:
        if sess.get(Novel, novel_id) is None:
            raise ServerErrors.no_such_novel
        rejected = set(
            sess.exec(
                sq.select(GlossaryOverride.surface_key).where(
                    sq.col(GlossaryOverride.novel_id) == novel_id
                )
            ).all()
        )
        created = existing = skipped = 0
        seen = set()
        for raw in terms:
            source = str(raw or "").strip()
            source_key = normalize_term_key(source)
            if not source_key or source_key in seen:
                continue
            seen.add(source_key)
            if source_key in rejected:
                skipped += 1
                continue
            term = sess.exec(
                sq.select(GlossaryTerm).where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryTerm.source_key) == source_key,
                )
            ).first()
            if term is None:
                term = get_or_create_term(sess, novel_id, source, origin="seed")
                term.tier = TermTier.core
                sess.add(term)
                created += 1
            else:
                existing += 1
        sess.commit()
    return {"created": created, "existing": existing, "rejected": skipped}


def _persist_batch(sess: sq.Session, novel_id: str, batch: Any) -> int:
    """Write a scan batch: terms first, then coverage rows and occurrences.

    `freq` is never accumulated here; it is recomputed from the occurrence rows
    afterwards, so re-scanning a chapter replaces its numbers instead of adding
    them twice. A rejected surface is skipped entirely, which is what keeps a
    false positive gone however often a scan meets it again.
    """
    if not batch.chapters:
        return 0

    rejected = set(
        sess.exec(
            sq.select(GlossaryOverride.surface_key).where(
                sq.col(GlossaryOverride.novel_id) == novel_id
            )
        ).all()
    )
    stored: Dict[str, GlossaryTerm] = {}
    changed = 0
    for proposed in batch.terms:
        source_key = normalize_term_key(proposed.source)
        if not source_key or source_key in rejected:
            continue
        term = sess.exec(
            sq.select(GlossaryTerm).where(
                sq.col(GlossaryTerm.novel_id) == novel_id,
                sq.col(GlossaryTerm.source_key) == source_key,
            )
        ).first()
        if term is None:
            term = get_or_create_term(sess, novel_id, proposed.source, origin=proposed.origin)
            term.tier = proposed.tier
            term.pinyin = proposed.pinyin
            term.category = proposed.category
            term.first_chapter_serial = proposed.first_chapter_serial
            changed += 1
        else:
            changed += int(_absorb_term(term, proposed))
        stored[proposed.source] = term
        sess.add(term)

    for proposed in batch.terms:
        term = stored.get(proposed.source)
        parent = stored.get(proposed.fragment_of or "")
        if term is None or parent is None or parent.id == term.id:
            continue
        # A core term may only be hidden by another core term: the batch's own
        # threshold says nothing about a row a stricter scan already accepted.
        if term.tier is TermTier.core and parent.tier is not TermTier.core:
            continue
        if term.fragment_of == parent.id:
            continue
        term.fragment_of = parent.id
        sess.add(term)
        changed += 1

    # Un-shadow any term whose recorded parent is only a tail. This is the
    # repair for relations written before that rule existed, and it has to run
    # on every stored term, not just the ones this batch proposed as roots.
    for term in {term.id: term for term in stored.values()}.values():
        if term.tier is not TermTier.core or not term.fragment_of:
            continue
        parent = sess.get(GlossaryTerm, term.fragment_of)
        if parent is not None and parent.tier is not TermTier.tail:
            continue
        term.fragment_of = None
        sess.add(term)
        changed += 1

    dropped: set = set()
    for chapter in batch.chapters:
        rows: Dict[str, GlossaryScan] = {}
        for extractor in batch.extractors:
            row = _scan_row(sess, novel_id, chapter.serial, extractor)
            # Occasions this scan is about to replace: a term may be absent
            # from the new batch because the extractor no longer proposes it,
            # and its cached `freq` has to fall with its occurrences.
            dropped.update(
                str(term_id)
                for term_id in sess.exec(
                    sq.select(GlossaryOccurrence.term_id).where(
                        sq.col(GlossaryOccurrence.scan_id) == row.id
                    )
                ).all()
            )
            sess.exec(
                sq.delete(GlossaryOccurrence).where(sq.col(GlossaryOccurrence.scan_id) == row.id)
            )
            sess.flush()
            stats = chapter.methods.get(extractor)
            row.content_hash = chapter.content_hash
            row.terms = stats.terms if stats else 0
            row.occurrences = stats.occurrences if stats else 0
            row.fragments = stats.fragments if stats else 0
            row.suppressed = stats.suppressed if stats else 0
            row.truncated = stats.truncated if stats else 0
            rows[extractor] = row
        for surface, count in chapter.counts.items():
            term = stored.get(surface)
            if term is None:
                continue
            extractor = chapter.origins.get(surface) or batch.extractors[0]
            row = rows.get(extractor) or rows[batch.extractors[0]]
            sess.add(GlossaryOccurrence(scan_id=row.id, term_id=term.id, count=count))
        for row in rows.values():
            sess.add(row)

    touched: Dict[str, GlossaryTerm] = {term.id: term for term in stored.values()}
    for term_id in dropped - set(touched):
        term = sess.get(GlossaryTerm, term_id)
        if term is not None:
            touched[term.id] = term
    _refresh_derived(sess, list(touched.values()))
    return changed


def _absorb_term(term: GlossaryTerm, proposed: Any) -> bool:
    """Fold a proposal into a known term without replacing a decision.

    A reading or category that already exists is not overwritten, and a term
    that earned `core` never slips back to `tail`.
    """
    changed = False
    if not term.pinyin and proposed.pinyin:
        term.pinyin = proposed.pinyin
        changed = True
    if not term.category and proposed.category:
        term.category = proposed.category
        changed = True
    if term.tier is not TermTier.core and proposed.tier is TermTier.core:
        term.tier = TermTier.core
        changed = True
    return changed


def _scan_row(sess: sq.Session, novel_id: str, serial: int, extractor: str) -> GlossaryScan:
    """The coverage row for one (chapter, method), created on first write."""
    row = sess.exec(
        sq.select(GlossaryScan).where(
            sq.col(GlossaryScan.novel_id) == novel_id,
            sq.col(GlossaryScan.serial) == serial,
            sq.col(GlossaryScan.extractor) == extractor,
        )
    ).first()
    if row is None:
        row = GlossaryScan(novel_id=novel_id, serial=serial, extractor=extractor)
        sess.add(row)
        sess.flush()
    return row


def _refresh_derived(sess: sq.Session, terms: Sequence[GlossaryTerm]) -> None:
    """Recompute the `freq` and `first_chapter_serial` caches from occurrences."""
    by_id = {term.id: term for term in terms if term.id}
    ids = list(by_id)
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        found = {
            str(term_id): (int(total or 0), serial)
            for term_id, total, serial in sess.exec(
                sq.select(
                    GlossaryOccurrence.term_id,
                    sq.func.sum(GlossaryOccurrence.count),
                    sq.func.min(GlossaryScan.serial),
                )
                .join(GlossaryScan, sq.col(GlossaryScan.id) == sq.col(GlossaryOccurrence.scan_id))
                .where(sq.col(GlossaryOccurrence.term_id).in_(chunk))
                .group_by(sq.col(GlossaryOccurrence.term_id))
            ).all()
        }
        for term_id in chunk:
            total, serial = found.get(term_id, (0, None))
            by_id[term_id].freq = total
            if serial is not None:
                by_id[term_id].first_chapter_serial = int(serial)
            sess.add(by_id[term_id])


def _chapter_serials(sess: sq.Session, novel_id: str) -> List[int]:
    from ..dao import Chapter

    return [
        int(serial)
        for serial in sess.exec(
            sq.select(Chapter.serial)
            .where(sq.col(Chapter.novel_id) == novel_id)
            .order_by(sq.col(Chapter.serial))
        ).all()
    ]


def _covered_serials(sess: sq.Session, novel_id: str) -> Dict[int, set]:
    """Which methods have already scanned each chapter."""
    covered: Dict[int, set] = {}
    for serial, extractor in sess.exec(
        sq.select(GlossaryScan.serial, GlossaryScan.extractor).where(
            sq.col(GlossaryScan.novel_id) == novel_id
        )
    ).all():
        covered.setdefault(int(serial), set()).add(str(extractor))
    return covered


def _scan_status(sess: sq.Session, novel_id: str, method_ids: Sequence[str]) -> Dict[str, Any]:
    """Coverage of the requested methods: chapters done, chapters left, next serial."""
    serials = _chapter_serials(sess, novel_id)
    covered = _covered_serials(sess, novel_id)
    done = {
        serial for serial, seen in covered.items() if all(method in seen for method in method_ids)
    }
    remaining = len([serial for serial in serials if serial not in done])
    return {
        "scans_total": len([serial for serial in serials if serial in done]),
        "total_chapters": len(serials),
        "remaining": remaining,
        "next_serial": next((serial for serial in serials if serial not in done), None),
        "finished": remaining == 0 and bool(serials),
    }


def reject_term(
    sess: sq.Session,
    novel_id: str,
    term_id: str,
    reason: Optional[str] = None,
) -> bool:
    """Forget a term and remember its surface as rejected.

    The override is what stops the extractor proposing the surface again; the
    term row and its entries, candidates and history go with it, and every
    language that had a decision gets a revision bump.
    """
    term = sess.get(GlossaryTerm, term_id)
    if term is None or term.novel_id != novel_id:
        return False

    languages = sess.exec(
        sq.select(GlossaryEntry.language).where(sq.col(GlossaryEntry.term_id) == term_id)
    ).all()
    for language in languages:
        bump_revision(sess, novel_id, language)

    rejected = sess.exec(
        sq.select(GlossaryOverride).where(
            sq.col(GlossaryOverride.novel_id) == novel_id,
            sq.col(GlossaryOverride.surface_key) == term.source_key,
        )
    ).first()
    if rejected is None:
        sess.add(
            GlossaryOverride(
                novel_id=novel_id,
                surface=term.source,
                surface_key=term.source_key,
                reason=(reason or "").strip() or None,
            )
        )
    sess.delete(term)
    return True


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
