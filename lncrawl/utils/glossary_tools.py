"""Copy the legacy `NovelGlossary.terms` JSON dict into glossary rows.

The glossary used to be a single JSON dict per (novel, language). Every entry in
those dicts is a human decision, so imported entries are born locked and strict:
an engine run must never be able to overwrite them.

Both the Alembic migration (server dialects) and the SQLite schema reconcile call
`backfill_legacy_glossaries`, so the two paths cannot drift apart. It works off
reflected tables rather than the ORM models, because the migration must keep
working after the models evolve. It is idempotent: existing terms and entries
are left untouched, so a retried or partially-completed run is safe.
"""

import time

import sqlalchemy as sa

from ..enums import TermGender
from .crypto_tools import generate_term_handle
from .text_tools import generate_uuid, normalize_term_key

IMPORT_NOTE = "Imported from the legacy glossary JSON"
_REQUIRED_TABLES = (
    "novel_glossaries",
    "glossary_terms",
    "glossary_entries",
    "glossary_revisions",
)


def _unique_handle(conn: sa.Connection, terms_table: sa.Table) -> str:
    for _ in range(8):
        handle = generate_term_handle()
        taken = conn.execute(sa.select(terms_table.c.id).where(terms_table.c.handle == handle))
        if taken.first() is None:
            return handle
    raise RuntimeError("Unable to generate an unused glossary term handle")


def backfill_legacy_glossaries(conn: sa.Connection) -> int:
    """Import legacy glossary dicts into terms/entries/revisions.

    Returns the number of entries created. Safe to call on a database that has
    already been backfilled, that never had a legacy dict, or whose `terms`
    column has already been dropped.
    """
    inspector = sa.inspect(conn)
    if not all(inspector.has_table(name) for name in _REQUIRED_TABLES):
        return 0

    meta = sa.MetaData()
    glossaries = sa.Table("novel_glossaries", meta, autoload_with=conn)
    if "terms" not in glossaries.c:
        return 0
    terms_table = sa.Table("glossary_terms", meta, autoload_with=conn)
    entries = sa.Table("glossary_entries", meta, autoload_with=conn)
    revisions = sa.Table("glossary_revisions", meta, autoload_with=conn)

    now = int(time.time() * 1000)
    created = 0
    rows = conn.execute(
        sa.select(
            glossaries.c.id,
            glossaries.c.novel_id,
            glossaries.c.language,
            glossaries.c.terms,
            glossaries.c.revision,
        )
    ).all()

    for glossary_id, novel_id, language, terms, revision in rows:
        if not isinstance(terms, dict):
            continue
        imported = 0
        for source, rendering in terms.items():
            source = str(source or "").strip()
            rendering = str(rendering or "").strip()
            source_key = normalize_term_key(source)
            if not source_key or not rendering:
                continue

            term_id = conn.execute(
                sa.select(terms_table.c.id).where(
                    terms_table.c.novel_id == novel_id,
                    terms_table.c.source_key == source_key,
                )
            ).scalar()
            if term_id is None:
                term_id = generate_uuid()
                conn.execute(
                    terms_table.insert().values(
                        id=term_id,
                        novel_id=novel_id,
                        source=source,
                        source_key=source_key,
                        handle=_unique_handle(conn, terms_table),
                        freq=0,
                        created_at=now,
                        updated_at=now,
                        extra={},
                    )
                )

            existing = conn.execute(
                sa.select(entries.c.id).where(
                    entries.c.term_id == term_id,
                    entries.c.language == language,
                )
            ).first()
            if existing is not None:
                continue

            conn.execute(
                entries.insert().values(
                    id=generate_uuid(),
                    term_id=term_id,
                    language=language,
                    rendering=rendering,
                    locked=True,
                    gender=TermGender.unknown.name,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    extra={},
                )
            )
            conn.execute(
                revisions.insert().values(
                    id=generate_uuid(),
                    term_id=term_id,
                    language=language,
                    version=1,
                    rendering=rendering,
                    note=IMPORT_NOTE,
                    created_at=now,
                    updated_at=now,
                    extra={},
                )
            )
            imported += 1

        if imported:
            created += imported
            if not revision:
                conn.execute(
                    sa.update(glossaries)
                    .where(glossaries.c.id == glossary_id)
                    .values(revision=1, updated_at=now)
                )

    return created


def reset_unbacked_frequencies(conn: sa.Connection) -> int:
    """Clear the frequency cache of every term no stored scan has counted.

    The old pre-pass added a chapter's count to `freq` on every run, so the same
    text could be counted twice. `freq` is now derived from
    `glossary_occurrences`, which means a term without an occurrence row has no
    reproducible number — keeping the old one would put two different meanings
    in one column, sorted together in the review list. A cleared term is
    repopulated by the next scan of the chapters it appears in.

    Idempotent: a cleared row stops matching, and new terms start at zero.
    """
    inspector = sa.inspect(conn)
    if not all(inspector.has_table(name) for name in _REQUIRED_TABLES):
        return 0
    if not inspector.has_table("glossary_occurrences"):
        return 0

    meta = sa.MetaData()
    terms = sa.Table("glossary_terms", meta, autoload_with=conn)
    occurrences = sa.Table("glossary_occurrences", meta, autoload_with=conn)
    counted = sa.select(occurrences.c.term_id)
    result = conn.execute(
        sa.update(terms).where(terms.c.freq != 0).where(terms.c.id.notin_(counted)).values(freq=0)
    )
    return int(result.rowcount or 0)
