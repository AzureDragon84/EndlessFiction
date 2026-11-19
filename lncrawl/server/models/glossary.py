"""Shapes for the glossary review screen: one row per term, per target language."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ...enums import TermCategory, TermTier


class GlossaryTermInfo(BaseModel):
    id: str = Field(description="Term id")
    source: str = Field(description="Source-language surface form")
    pinyin: Optional[str] = Field(default=None, description="Romanised reading, when known")
    category: Optional[TermCategory] = Field(
        default=None, description="What kind of thing it names"
    )
    tier: TermTier = Field(
        default=TermTier.core,
        description="core, or tail when only a harvest pass saw it",
    )
    origin: str = Field(default="frequency", description="Extraction method that proposed it")
    fragment_of: Optional[str] = Field(
        default=None,
        description="Source form of the longer term this surface is part of",
    )
    freq: int = Field(default=0, description="Occurrences summed over the chapters scanned")
    first_chapter_serial: Optional[int] = Field(
        default=None, description="Chapter the term was first seen in"
    )
    rendering: Optional[str] = Field(
        default=None, description="Decided rendering in the target language"
    )
    locked: bool = Field(default=False, description="Editor decision the engine cannot overwrite")
    candidates: List[str] = Field(
        default_factory=list, description="Proposed renderings, best first"
    )


class GlossaryTermPage(BaseModel):
    items: List[GlossaryTermInfo] = Field(default_factory=list)
    total: int = Field(default=0, description="Terms matching the query")


class GlossaryTermUpdate(BaseModel):
    rendering: Optional[str] = Field(
        default=None,
        description="New rendering; null or empty makes the term undecided again",
    )


class GlossaryRejectRequest(BaseModel):
    reason: Optional[str] = Field(default=None, description="Why the surface is not a term")


class GlossaryExtractorInfo(BaseModel):
    id: str = Field(description="Method id a scan request passes back")
    name: str = Field(description="Display name")
    description: str = Field(description="What it finds and where it falls short")
    kind: str = Field(description="'scan' runs over chapters, 'import' only accepts a list")
    languages: List[str] = Field(
        default_factory=list, description="Language prefixes it handles; empty means every script"
    )
    default: bool = Field(default=False, description="Runs when a request names no method")
    available: bool = Field(default=True, description="Whether it can run on this novel")
    reason: Optional[str] = Field(default=None, description="Why it cannot run here")


class GlossaryScanRequest(BaseModel):
    extractors: Optional[List[str]] = Field(
        default=None,
        description="Methods to run; null means the defaults for the novel's script",
    )
    limit: int = Field(default=50, ge=1, le=500, description="Chapters to read")
    min_freq: Optional[int] = Field(
        default=None,
        ge=1,
        description="Occurrences a surface needs; 1 is a harvest pass that keeps the tail",
    )
    force: bool = Field(default=False, description="Re-read chapters that are already covered")


class GlossaryScanResult(BaseModel):
    scanned: int = Field(description="Chapters read")
    terms: int = Field(description="Terms created or updated")
    occurrences: int = Field(default=0, description="Surface occurrences counted")
    suppressed: int = Field(default=0, description="Surfaces seen but not stored")
    fragments: int = Field(default=0, description="Surfaces kept hidden inside a longer term")
    truncated: int = Field(default=0, description="Surfaces dropped by the per-chapter backstop")
    unreadable: int = Field(default=0, description="Chapters whose content could not be read")
    extractors: List[str] = Field(default_factory=list, description="Methods that ran")
    elapsed_ms: int = Field(default=0, description="Wall time of the whole call")
    scans_total: int = Field(default=0, description="Chapters now covered by every method")
    total_chapters: int = Field(default=0, description="Chapters the novel has")
    remaining: int = Field(default=0, description="Chapters still unscanned")
    next_serial: Optional[int] = Field(default=None, description="Where the next scan resumes")
    finished: bool = Field(default=False, description="Whether every chapter is covered")


class GlossaryScanStatus(BaseModel):
    extractors: List[str] = Field(default_factory=list, description="Methods the status refers to")
    per_method: Dict[str, int] = Field(
        default_factory=dict, description="Chapters scanned so far, per method"
    )
    scans_total: int = Field(default=0, description="Chapters covered by every method")
    total_chapters: int = Field(default=0, description="Chapters the novel has")
    remaining: int = Field(default=0, description="Chapters still unscanned")
    next_serial: Optional[int] = Field(default=None, description="Where the next scan resumes")
    finished: bool = Field(default=False, description="Whether every chapter is covered")


class GlossaryImportRequest(BaseModel):
    terms: List[str] = Field(
        default_factory=list, description="Source surfaces; blanks and repeats are ignored"
    )


class GlossaryImportResult(BaseModel):
    created: int = Field(default=0, description="Terms added")
    existing: int = Field(default=0, description="Terms that were already stored")
    rejected: int = Field(default=0, description="Surfaces skipped because they were rejected")
