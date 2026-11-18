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
"""

from dataclasses import dataclass
import re
from typing import Dict, Iterable, List, Optional, Tuple

from ..enums import TermCategory

# A surface has to recur before it is worth a reviewer's attention.
MIN_FREQ = 2
# Latin names are noisier, because a capitalised word also starts sentences.
LATIN_MIN_FREQ = 3
# Longest Han/Hangul sequence treated as one surface.
MAX_NGRAM = 6
# Surfaces stored per scan, highest frequency first.
MAX_TERMS = 40

_HAN = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HANGUL = "\uac00-\ud7a3"
_CJK_RUN = re.compile(f"[{_HAN}{_HANGUL}]+")
_HAS_HAN = re.compile(f"[{_HAN}]")
_LATIN_NAME = re.compile(r"(?<![A-Za-z'’-])[A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*){0,3}")
_ARTICLE = re.compile(r"^(?:The|A|An)\s+")
_POSSESSIVE = re.compile(r"['’]s$")

# Characters that turn any containing sequence into grammar rather than a name.
# They are particles, pronouns and auxiliary verbs, never name components.
_STOP_CHARS = frozenset(
    "的了着过是在有和与也都就还很太更最不没别我你他她它们这那哪谁什么怎为以而且但或如若因所之其于及等再又还只把被从对里后前时吗呢吧啊哦呀么上下中来去要到会能得让给做想看听走笑说问答叫个一"
)
# Korean verb endings are as common as names and just as frequent.
_HANGUL_STOP = frozenset(
    {
        "하고",
        "했다",
        "한다",
        "에서",
        "으로",
        "지만",
        "는데",
        "있다",
        "없다",
        "그리고",
        "하지만",
        "때문",
        "하는",
        "되는",
        "같은",
        "이런",
        "저런",
        "그런",
        "어떤",
        "무슨",
        "우리",
    }
)
# Sentence starters and function words that survive capitalisation.
_LATIN_STOP = frozenset(
    "the a an and but or if so then when while after before he she it they we you i "
    "his her their our its this that these those what which who whom whose why how where "
    "chapter volume part book prologue epilogue mr mrs ms dr sir yes no oh ah well "
    "jesus god".split()
)

# A Han suffix says what kind of thing a name is; the longest match wins.
_CATEGORY_SUFFIXES: Tuple[Tuple[str, TermCategory], ...] = tuple(
    sorted(
        {
            "宗": TermCategory.sect_org,
            "门": TermCategory.sect_org,
            "派": TermCategory.sect_org,
            "会": TermCategory.sect_org,
            "帮": TermCategory.sect_org,
            "殿": TermCategory.sect_org,
            "阁": TermCategory.sect_org,
            "盟": TermCategory.sect_org,
            "教": TermCategory.sect_org,
            "堂": TermCategory.sect_org,
            "院": TermCategory.sect_org,
            "城": TermCategory.location,
            "山": TermCategory.location,
            "谷": TermCategory.location,
            "峰": TermCategory.location,
            "洞": TermCategory.location,
            "州": TermCategory.location,
            "国": TermCategory.location,
            "界": TermCategory.location,
            "域": TermCategory.location,
            "林": TermCategory.location,
            "湖": TermCategory.location,
            "岛": TermCategory.location,
            "村": TermCategory.location,
            "镇": TermCategory.location,
            "诀": TermCategory.technique,
            "功": TermCategory.technique,
            "术": TermCategory.technique,
            "经": TermCategory.technique,
            "掌": TermCategory.technique,
            "拳": TermCategory.technique,
            "阵": TermCategory.technique,
            "剑法": TermCategory.technique,
            "身法": TermCategory.technique,
            "丹": TermCategory.item,
            "剑": TermCategory.item,
            "刀": TermCategory.item,
            "枪": TermCategory.item,
            "符": TermCategory.item,
            "石": TermCategory.item,
            "果": TermCategory.item,
            "草": TermCategory.item,
            "药": TermCategory.item,
            "珠": TermCategory.item,
            "鼎": TermCategory.item,
            "境": TermCategory.rank_realm,
            "层": TermCategory.rank_realm,
            "阶": TermCategory.rank_realm,
            "品": TermCategory.rank_realm,
            "重": TermCategory.rank_realm,
            "元": TermCategory.rank_realm,
            "兽": TermCategory.creature,
            "妖": TermCategory.creature,
            "魔": TermCategory.creature,
            "龙": TermCategory.creature,
            "凤": TermCategory.creature,
            "灵": TermCategory.creature,
            "鬼": TermCategory.creature,
            "大人": TermCategory.honorific,
            "公子": TermCategory.honorific,
            "小姐": TermCategory.honorific,
            "长老": TermCategory.honorific,
            "前辈": TermCategory.honorific,
            "师尊": TermCategory.honorific,
            "陛下": TermCategory.honorific,
            "殿下": TermCategory.honorific,
            "娘娘": TermCategory.honorific,
        }.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)


@dataclass(frozen=True)
class ExtractedTerm:
    """One recurring surface found by a scan, before any decision is made."""

    source: str
    freq: int
    first_chapter_serial: int
    category: Optional[TermCategory] = None
    pinyin: Optional[str] = None


def extract_terms(
    pages: Iterable[Tuple[int, str]],
    *,
    language: Optional[str] = None,
) -> List[ExtractedTerm]:
    """Rank the recurring surfaces of `pages` — (chapter serial, chapter text).

    HTML is stripped; `language` only gates the reading, because a Japanese
    kanji name is not read the Chinese way.
    """
    counts: Dict[str, int] = {}
    first: Dict[str, int] = {}
    for serial, text in pages:
        for surface, count in _count_surfaces(_plain_text(text)).items():
            counts[surface] = counts.get(surface, 0) + count
            first.setdefault(surface, serial)

    ranked = sorted(_maximal(counts).items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    return [
        ExtractedTerm(
            source=source,
            freq=freq,
            first_chapter_serial=first.get(source, 0),
            category=_category(source),
            pinyin=_pinyin(source, language),
        )
        for source, freq in ranked[:MAX_TERMS]
    ]


def _plain_text(text: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(str(text or ""), "html.parser").get_text(" ")


def _count_surfaces(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for run in _CJK_RUN.findall(text):
        for size in range(2, min(MAX_NGRAM, len(run)) + 1):
            for start in range(len(run) - size + 1):
                surface = run[start : start + size]
                if _STOP_CHARS.isdisjoint(surface) and surface not in _HANGUL_STOP:
                    counts[surface] = counts.get(surface, 0) + 1

    for match in _LATIN_NAME.finditer(text):
        surface = _POSSESSIVE.sub("", _ARTICLE.sub("", match.group(0))).strip()
        words = surface.split()
        if len(surface) < 3 or not words:
            continue
        if any(word.lower() in _LATIN_STOP for word in words):
            continue
        counts[surface] = counts.get(surface, 0) + 1
    return counts


def _maximal(counts: Dict[str, int]) -> Dict[str, int]:
    """Drop a fragment a kept longer surface already accounts for.

    `青云宗` is kept, so `云宗` does not become a term of its own; `青云`
    survives only because it recurs more often than the names containing it.
    """
    kept: Dict[str, int] = {}
    fragment_of: Dict[str, int] = {}
    for surface in sorted(counts, key=len, reverse=True):
        count = counts[surface]
        if count < _min_freq(surface) or fragment_of.get(surface, 0) >= count:
            continue
        kept[surface] = count
        for size in range(2, len(surface)):
            for start in range(len(surface) - size + 1):
                fragment = surface[start : start + size]
                if fragment_of.get(fragment, 0) < count:
                    fragment_of[fragment] = count
    return kept


def _min_freq(surface: str) -> int:
    return MIN_FREQ if _CJK_RUN.fullmatch(surface) else LATIN_MIN_FREQ


def _category(surface: str) -> Optional[TermCategory]:
    if not _HAS_HAN.search(surface):
        return None
    for suffix, category in _CATEGORY_SUFFIXES:
        if surface.endswith(suffix):
            return category
    return None


def _pinyin(surface: str, language: Optional[str]) -> Optional[str]:
    if not _HAS_HAN.search(surface):
        return None
    if language and language.split("-")[0].lower() == "ja":
        return None
    from pypinyin import lazy_pinyin

    return " ".join(lazy_pinyin(surface)).strip() or None
