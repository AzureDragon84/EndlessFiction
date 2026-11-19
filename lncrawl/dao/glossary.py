from typing import Optional

import sqlmodel as sa

from ..enums import TermCategory, TermGender, TermStrategy, TermTier
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

    pinyin: Optional[str] = sa.Field(default=None, description="Romanised reading")
    category: Optional[TermCategory] = sa.Field(
        default=None,
        sa_column=sa.Column(sa.Enum(TermCategory, native_enum=False), nullable=True),
        description="What kind of thing the term names",
    )
    freq: int = sa.Field(
        default=0,
        description="Derived cache: occurrences summed over `glossary_occurrences`",
    )
    first_chapter_serial: Optional[int] = sa.Field(
        default=None, description="Derived cache: lowest chapter serial that has a scan"
    )
    tier: TermTier = sa.Field(
        default=TermTier.core,
        sa_column=sa.Column(
            sa.Enum(TermTier, native_enum=False), nullable=False, server_default="core"
        ),
        description="core when a chapter reached the scan threshold, tail when only a harvest pass saw it",
    )
    origin: str = sa.Field(
        default="frequency",
        sa_column=sa.Column(sa.String, nullable=False, server_default="frequency"),
        description="Extraction method that first proposed the surface",
    )
    fragment_of: Optional[str] = sa.Field(
        default=None,
        foreign_key="glossary_terms.id",
        ondelete="SET NULL",
        nullable=True,
        description="Canonical term this surface is a fragment of",
    )


class GlossaryScan(BaseTable, table=True):
    """One (chapter, method) pass: the unit of scan coverage.

    A chapter counts as covered for a method once its row exists, which is what
    lets a scan resume where the last one stopped instead of re-reading chapters
    it already handled. The counters are what the review screen reports, and
    `content_hash` is what a later staleness check compares the stored chapter
    file against.
    """

    __tablename__ = "glossary_scans"  # type: ignore
    __table_args__ = (
        sa.UniqueConstraint("novel_id", "serial", "extractor"),
        sa.Index("ix_glossary_scan_novel_serial", "novel_id", "serial"),
    )

    novel_id: str = sa.Field(foreign_key="novels.id", ondelete="CASCADE")
    serial: int = sa.Field(description="Chapter serial this pass read")
    extractor: str = sa.Field(description="Extraction method that produced the pass")
    content_hash: Optional[str] = sa.Field(
        default=None, description="Digest of the text read, for staleness checks"
    )
    terms: int = sa.Field(default=0, description="Stored terms this method proposed in the chapter")
    occurrences: int = sa.Field(
        default=0,
        description="Occurrences this method saw; methods overlap, so rows do not add up",
    )
    fragments: int = sa.Field(
        default=0, description="Surfaces this method put inside a longer term"
    )
    suppressed: int = sa.Field(default=0, description="Surfaces this method saw but did not store")
    truncated: int = sa.Field(
        default=0,
        description="Surfaces the per-chapter backstop dropped; recorded once per chapter",
    )


class GlossaryOccurrence(BaseTable, table=True):
    """How often a term appeared in one chapter scan — the rows `freq` derives from."""

    __tablename__ = "glossary_occurrences"  # type: ignore
    __table_args__ = (
        sa.UniqueConstraint("scan_id", "term_id"),
        sa.Index("ix_glossary_occurrence_scan", "scan_id"),
        sa.Index("ix_glossary_occurrence_term", "term_id"),
    )

    scan_id: str = sa.Field(foreign_key="glossary_scans.id", ondelete="CASCADE")
    term_id: str = sa.Field(foreign_key="glossary_terms.id", ondelete="CASCADE")
    count: int = sa.Field(default=0, description="Occurrences of the surface in that chapter")


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
