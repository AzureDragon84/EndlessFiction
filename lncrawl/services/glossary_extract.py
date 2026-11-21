"""Frequency-first term extraction: the glossary pre-pass.

The translator package only reports the terms it happens to meet while
translating a chapter, so a novel's cast list otherwise assembles itself one
paid chapter at a time. This scanner reads a chapter's source text before the
engine sees it: recurring names are stored as terms with their frequency, first
chapter, reading and category, ready to be reviewed and locked.

It proposes surfaces only, never renderings — a rendering is a glossary
decision. The rules are deliberately conservative (a surface used once in a
chapter is almost always noise), and everything here is a first guess: a
category, reading or display form that is already known is never replaced by a
later scan.

This module is the language-neutral half: plain-text, the statistical stages
and the `ExtractedTerm` shape every caller stores. What a word is, which words
are noise and how one is read live in `glossary_profile`.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from ..enums import TermCategory
from .glossary_profile import non_latin, profile_for

# A surface has to recur before it is worth a reviewer's attention.
MIN_FREQ = 2
# Latin names are noisier, because a capitalised word also starts sentences.
LATIN_MIN_FREQ = 3
# A short surface whose occurrences are almost all inside a longer kept one is
# a fragment of it, not a term: `云宗` inside `青云宗`.
SUBSUMPTION = 0.8
# Hull classes are cut at their weight word by the segmenter: `蜂王级重型` is
# the front half of `蜂王级重型星际战列舰`, never a term on its own.
_HULL_MODIFIERS = ("轻型", "中型", "重型", "小型", "大型")


@dataclass(frozen=True)
class ExtractedTerm:
    """One recurring surface found by a scan, before any decision is made."""

    source: str
    freq: int
    first_chapter_serial: int
    category: Optional[TermCategory] = None
    reading: Optional[str] = None
    chapter_counts: Tuple[Tuple[int, int], ...] = ()
    """(chapter serial, occurrences) pairs, so a scan can store per-chapter rows."""
    found_by: str = "local"
    """What found this surface: `local` for the scan, else the model that named it."""


def extract_terms(
    pages: Iterable[Tuple[int, str]],
    *,
    language: Optional[str] = None,
    limit: Optional[int] = None,
    min_freq: int = MIN_FREQ,
) -> List[ExtractedTerm]:
    """Rank the recurring surfaces of `pages` — (chapter serial, chapter text).

    HTML is stripped and the text handed to the language's profile, which turns
    it into candidate surfaces; everything after that is arithmetic. `language`
    selects the profile (a 20-chapter batch of Japanese is not segmented the
    Chinese way) and the reading that comes back with it. `min_freq` and `limit`
    are settings rather than constants because the glossary tab rescans in
    batches and a short chapter needs a lower bar than a whole-novel scan.
    `limit` is optional: unset stores every surface that passed the rules, ranked
    highest frequency first. A cap that is not asked for drops the tail of a
    batch silently, which is how a scan looked complete at exactly 200 terms.
    """
    profile = profile_for(language)
    texts = [(serial, _plain_text(text)) for serial, text in pages]
    batch = profile.collect(texts)
    per_chapter: Dict[str, Dict[int, int]] = {}
    names = set(batch.names)
    for serial, _ in texts:
        for surface, count in batch.counts.get(serial, {}).items():
            per_chapter.setdefault(surface, {})[serial] = count

    first = {surface: min(chapters) for surface, chapters in per_chapter.items() if chapters}
    totals = {surface: sum(chapters.values()) for surface, chapters in per_chapter.items()}
    # Spread (stage 5): a name is evidence the first time it appears, so its
    # bar is one occurrence; a common noun has to recur in more than one
    # chapter. That is what keeps one-off narrative words out without a
    # vocabulary list long enough to name them all.
    kept = {
        surface: count
        for surface, count in totals.items()
        if count >= (1 if surface in names else _min_freq(surface, min_freq))
        and (surface in names or len(per_chapter[surface]) >= 2)
    }
    ranked = sorted(
        _maximal(kept, names).items(), key=lambda item: (-item[1], -len(item[0]), item[0])
    )
    return [
        ExtractedTerm(
            source=source,
            freq=freq,
            first_chapter_serial=first.get(source, 0),
            reading=profile.reading(source),
            chapter_counts=tuple(sorted(per_chapter.get(source, {}).items())),
        )
        for source, freq in (ranked[: max(1, limit)] if limit else ranked)
    ]


def _plain_text(text: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(str(text or ""), "html.parser").get_text(" ")


def _maximal(counts: Dict[str, int], names: Optional[set] = None) -> Dict[str, int]:
    """Drop a fragment a kept longer surface already accounts for.

    `青云宗` is kept, so `云宗` does not become a term of its own; `青云`
    survives only because it recurs substantially more often than the names
    containing it. A surface seen once is never dropped as a fragment: `李威`
    inside one long fused string is not evidence against the name. Frequency
    was already judged by the caller, so it is not re-checked here.
    """
    order = sorted(counts, key=len, reverse=True)
    kept: Dict[str, int] = {}
    fragment_of: Dict[str, int] = {}
    for surface in order:
        count = counts[surface]
        # A name is never a fragment of a longer string: a fused line like
        # `的灭绝师太居` must not delete the character it mentions. The judge
        # stage is what merges real variants (`莎莎` → `蒋莎莎`).
        if (
            count >= 2
            and (not names or surface not in names)
            and fragment_of.get(surface, 0) >= count * SUBSUMPTION
        ):
            continue
        if surface.endswith(_HULL_MODIFIERS) and any(
            other.startswith(surface) for other in counts if other != surface
        ):
            continue
        kept[surface] = count
        for size in range(2, len(surface)):
            for start in range(len(surface) - size + 1):
                fragment = surface[start : start + size]
                if fragment_of.get(fragment, 0) < count:
                    fragment_of[fragment] = count
    return kept


def _min_freq(surface: str, min_freq: int = MIN_FREQ) -> int:
    return min_freq if non_latin(surface) else max(min_freq, LATIN_MIN_FREQ)
