from typing import Optional

import sqlmodel as sa

from ..enums import TermCategory, TermGender, TermStrategy, TermVerdict
from ._base import BaseTable


class GlossaryTerm(BaseTable, table=True):
    """Source-language identity of a term, shared by every target language.

    Identity (`source_key`) and display (`source`) are separate: the same term
    must not become two rows because of a stray space, and `handle` is what
    `data-g` markers point at, so it is minted once and never regenerated.
    """

    __tablename__ = "glossary_terms"  # type: ignore
    __table_args__ = (sa.UniqueConstraint("novel_id", "source_key"),)

    novel_id: str = sa.Field(foreign_key="novels.id", ondelete="CASCADE", index=True)
    source: str = sa.Field(description="Source-language surface form as extracted")
    source_key: str = sa.Field(description="Normalised identity of the source, unique per novel")
    handle: str = sa.Field(
        unique=True,
        index=True,
        description="Stable public handle used in data-g markers, e.g. 't_7fk39m2q'",
    )

    reading: Optional[str] = sa.Field(
        default=None,
        description="Romanised reading in the source language: pinyin, romaji or Hangul",
    )
    category: Optional[TermCategory] = sa.Field(
        default=None,
        sa_column=sa.Column(sa.Enum(TermCategory, native_enum=False), nullable=True),
        description="What kind of thing the term names",
    )
    freq: int = sa.Field(default=0, description="Occurrences seen across crawled chapters")
    found_by: Optional[str] = sa.Field(
        default=None,
        description="What found this term: `local` for the free scan, else the model id",
    )
    verdict: Optional[TermVerdict] = sa.Field(
        default=None,
        sa_column=sa.Column(sa.Enum(TermVerdict, native_enum=False), nullable=True),
        description="Review verdict: null undecided, skip/fragment hidden by default",
    )
    judged_by: Optional[str] = sa.Field(
        default=None, description="Model that last judged this term"
    )
    judged_at: Optional[int] = sa.Field(
        default=None, sa_type=sa.BigInteger, description="When the verdict was judged"
    )
    first_chapter_serial: Optional[int] = sa.Field(
        default=None, description="Chapter this term was first seen in"
    )
    fragment_of: Optional[str] = sa.Field(
        default=None,
        foreign_key="glossary_terms.id",
        ondelete="SET NULL",
        nullable=True,
        description="Canonical term this surface is a fragment of",
    )


class GlossaryTermCount(BaseTable, table=True):
    """Occurrences of one term in one chapter — the unit `freq` is derived from.

    A scan replaces the rows for the chapters it read instead of adding to
    `GlossaryTerm.freq`, so rescanning a batch leaves every number where it
    was. `freq` is the sum of these rows, never a separate tally.
    """

    __tablename__ = "glossary_term_counts"  # type: ignore
    __table_args__ = (sa.UniqueConstraint("term_id", "chapter_id"),)

    term_id: str = sa.Field(foreign_key="glossary_terms.id", ondelete="CASCADE", index=True)
    chapter_id: str = sa.Field(foreign_key="chapters.id", ondelete="CASCADE", index=True)
    occurrences: int = sa.Field(
        default=0,
        sa_column=sa.Column(sa.Integer, nullable=False, server_default="0"),
        description="Occurrences of the term in this chapter",
    )


class GlossaryScan(BaseTable, table=True):
    """One extraction batch and what it did, so a rescan can be compared.

    Counts are replaced per chapter, so a run's `new`/`updated` describe what
    changed relative to the previous rows rather than how much text was read.
    `found` is what was stored after the cap and `found_total` what the batch
    actually produced, which is what makes a truncated run visible.
    """

    __tablename__ = "glossary_scans"  # type: ignore
    __table_args__ = (sa.Index("ix_glossary_scan_lookup", "novel_id", "created_at"),)

    novel_id: str = sa.Field(foreign_key="novels.id", ondelete="CASCADE", index=True)
    first_chapter: int = sa.Field(default=0, description="First chapter index this batch covered")
    requested: int = sa.Field(default=0, description="Chapters the batch asked for")
    scanned: int = sa.Field(default=0, description="Chapters that had text to read")
    found: int = sa.Field(default=0, description="Terms stored after the cap")
    found_total: int = sa.Field(default=0, description="Terms produced before the cap")
    new_terms: int = sa.Field(default=0, description="Terms this scan discovered")
    updated_terms: int = sa.Field(default=0, description="Terms whose stored numbers changed")
    cap: int = sa.Field(default=0, description="Term cap applied to this batch")
    capped: bool = sa.Field(default=False, description="Whether the cap dropped terms")


class GlossaryEntry(BaseTable, table=True):
    """The decision for one term in one target language (what `data-g` resolves to)."""

    __tablename__ = "glossary_entries"  # type: ignore
    __table_args__ = (sa.UniqueConstraint("term_id", "language"),)

    term_id: str = sa.Field(foreign_key="glossary_terms.id", ondelete="CASCADE")
    language: str = sa.Field(description="Target language code, e.g. 'en'")
    rendering: Optional[str] = sa.Field(
        default=None, description="Chosen rendering in `language`; null means undecided"
    )
    locked: bool = sa.Field(
        default=False, description="Engine output may never overwrite a locked entry"
    )
    gender: TermGender = sa.Field(
        default=TermGender.unknown,
        sa_column=sa.Column(sa.Enum(TermGender, native_enum=False), nullable=False),
        description="Gender used for pronouns; unknown until a human decides",
    )
    version: int = sa.Field(default=1, description="Revision number of this decision")


class GlossaryUserEntry(BaseTable, table=True):
    """A reader's personal rendering for one term.

    The admin glossary stays authoritative for translations; this only changes
    what this user sees, so a disagreement never forks the stored chapter.
    """

    __tablename__ = "glossary_user_entries"  # type: ignore
    __table_args__ = (sa.UniqueConstraint("user_id", "term_id", "language"),)

    user_id: str = sa.Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    term_id: str = sa.Field(foreign_key="glossary_terms.id", ondelete="CASCADE")
    language: str = sa.Field(description="Target language code, e.g. 'en'")
    rendering: str = sa.Field(description="What this reader wants to see instead")


class GlossaryCandidate(BaseTable, table=True):
    """A proposal from one engine/strategy; losers are kept for review and audit."""

    __tablename__ = "glossary_candidates"  # type: ignore
    __table_args__ = (sa.UniqueConstraint("term_id", "language", "normalized"),)

    term_id: str = sa.Field(foreign_key="glossary_terms.id", ondelete="CASCADE")
    language: str = sa.Field(description="Target language code, e.g. 'en'")
    engine: str = sa.Field(description="Engine or model id that produced the proposal")
    strategy: TermStrategy = sa.Field(
        default=TermStrategy.literal,
        sa_column=sa.Column(sa.Enum(TermStrategy, native_enum=False), nullable=False),
        description="Which prompt produced the proposal",
    )
    rendering: str = sa.Field(description="Proposed rendering")
    normalized: str = sa.Field(description="Dedupe key: casefolded, spaces collapsed")
    votes: int = sa.Field(default=1, description="Agreements across engines or rounds")
    chosen: bool = sa.Field(default=False, description="Whether this became the rendering")


class GlossaryOverride(BaseTable, table=True):
    """The rejected sheet: suppresses a surface before extraction ever stores it."""

    __tablename__ = "glossary_overrides"  # type: ignore
    __table_args__ = (sa.UniqueConstraint("novel_id", "surface_key"),)

    novel_id: str = sa.Field(foreign_key="novels.id", ondelete="CASCADE", index=True)
    surface: str = sa.Field(description="Surface form shown in the review UI")
    surface_key: str = sa.Field(description="Normalised surface this override applies to")
    reason: Optional[str] = sa.Field(default=None, description="Why it was rejected")


class GlossaryRevision(BaseTable, table=True):
    """Append-only log of every change to an entry."""

    __tablename__ = "glossary_revisions"  # type: ignore
    __table_args__ = (sa.UniqueConstraint("term_id", "language", "version"),)

    term_id: str = sa.Field(foreign_key="glossary_terms.id", ondelete="CASCADE")
    language: str = sa.Field(description="Target language code, e.g. 'en'")
    version: int = sa.Field(description="Entry version this revision produced")
    rendering: Optional[str] = sa.Field(default=None, description="Rendering after the change")
    engine: Optional[str] = sa.Field(default=None, description="Engine that proposed it, if any")
    user_id: Optional[str] = sa.Field(
        default=None,
        foreign_key="users.id",
        ondelete="SET NULL",
        nullable=True,
        description="User who made the change, if any",
    )
    note: Optional[str] = sa.Field(default=None, description="Short change description")
