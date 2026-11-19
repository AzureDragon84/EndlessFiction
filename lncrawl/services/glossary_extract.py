"""Term extraction methods: the glossary pre-pass.

The translator package only reports the terms it happens to meet while
translating a chapter, so a novel's cast list otherwise assembles itself one
paid chapter at a time. These methods read the source text before the engine
sees it and propose what recurs. They propose surfaces only, never renderings —
a rendering is a glossary decision.

Three methods ship, and they are not interchangeable:

- `frequency` counts repeated Han/Hangul n-grams and capitalised Latin names. It
  is the only method that can find a term inside an unsegmented run of Chinese,
  and it needs no tokenizer for other scripts. For a Chinese script jieba is
  still the referee (see below): an n-gram it never emits as one token is a
  cross-boundary span, and the longer of two real surfaces hides the shorter as
  a fragment.
- `pos` asks jieba's dictionary and HMM model for nouns and proper nouns. It
  keeps compound names whole and labels people, places and organisations, but
  its bundled dictionary is simplified Chinese, so traditional text falls back
  to the HMM model and is noisier.
- `seed` is an imported list: terms an editor already knows, with no
  occurrences. It is never run over chapters.

A scan unions the chosen methods per chapter by taking the **maximum** count a
surface got from any one method, never the sum: two methods reading the same
sentence see the same occurrences, and adding them would double every number.

Before anything is stored, every Chinese surface passes two gates that keep the
glossary about names instead of about words. jieba must have emitted the span as
a single token somewhere in the batch (`艘星舰` is `艘` + `星舰`, not a term), and
its tag must name something rather than grammar (`自己`, `知道`, `成功` are words,
not terms). Neither gate applies to scripts jieba does not read, and both are
skipped when jieba is not installed. What survives is classified from its
suffix, then jieba's tag, then the words around it: a token followed by `说` is
a character, one after `把` is an item, one after `在` is a place.

What is stored is governed by `min_freq` (per script when unset). A surface that
reaches it in any single chapter is stored; one that never does is a `tail`
candidate, kept only when a harvest pass asks for `min_freq=1`. `tier` records
which of the two a term is, so review can hide the long tail without losing it.
`MAX_CANDIDATES_PER_CHAPTER` is a backstop against pathological input, not a
term budget, and a scan reports how many candidates it dropped.
"""

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..enums import TermCategory, TermTier

# A surface has to recur before it is worth a reviewer's attention.
MIN_FREQ = 2
# Latin names are noisier, because a capitalised word also starts sentences.
LATIN_MIN_FREQ = 3
# Longest Han/Hangul sequence treated as one surface.
MAX_NGRAM = 6
# Backstop only: a chapter producing more candidates than this is degenerate.
MAX_CANDIDATES_PER_CHAPTER = 2000

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

# Context vocabularies: a token next to one of these is likely a person, a
# place or an object. This is what gives a plain noun a category at all —
# `赵辰` beside `说` is a character however jieba tagged it.
_SPEECH = frozenset(
    "说 说道 道 问道 答 答道 笑道 笑 喝道 喊道 叫道 开口 回答 点头 摇头 冷哼 皱眉 苦笑 叹道".split()
)
_MEASURE_ITEM = frozenset("把 柄 枚 颗 架 艘 件 株 瓶 块 台 张 具 副 支 杆".split())
_PERSON_CLASSIFIER = frozenset("位 名".split())
# A classifier only counts when a numeral or demonstrative precedes it: `一名`
# before a name is a person, `名为` before a ship is not.
_NUMERAL = frozenset("一 二 两 三 四 五 六 七 八 九 十 几 数 这 那 每 某 整".split())

# Words that recur in every chapter of every novel and never name anything.
# Grammar is filtered by part of speech already; these are the nouns that are
# left over (`事情`, `时候`, `声音`) and would otherwise become glossary rows.
_COMMON_NOUNS = frozenset(
    "事情 时候 东西 地方 问题 感觉 样子 办法 声音 眼睛 脸上 手中 身体 心里 面前 身边"
    " 表情 目光 眼神 口气 语气 意思 想法 动作 部分 关系 情况 结果 原因 方法 方向"
    " 位置 距离 年纪 年龄 名字 笑容 呼吸 脚步 技术 艺术 学术".split()
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
            "舰": TermCategory.item,
            "船": TermCategory.item,
            "炮": TermCategory.item,
            "甲": TermCategory.item,
            "盾": TermCategory.item,
            "地": TermCategory.location,
            "长": TermCategory.honorific,
            "境": TermCategory.rank_realm,
            "层": TermCategory.rank_realm,
            "阶": TermCategory.rank_realm,
            "品": TermCategory.rank_realm,
            "重": TermCategory.rank_realm,
            "段": TermCategory.rank_realm,
            "元": TermCategory.rank_realm,
            "兽": TermCategory.creature,
            "兽人": TermCategory.creature,
            "虫": TermCategory.creature,
            "怪": TermCategory.creature,
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
class _Token:
    word: str
    flag: str


@dataclass
class _Passage:
    """One chapter segmented once: the basis of every Chinese gate below."""

    plain: str
    tokens: List[_Token]
    token_seen: set
    flags: Dict[str, set]
    votes: Dict[str, Dict[TermCategory, int]]


@dataclass(frozen=True)
class Extractor:
    """One way of proposing surfaces, and the conditions it needs to run."""

    id: str
    name: str
    description: str
    kind: str = "scan"
    languages: Tuple[str, ...] = ()
    default: bool = False
    probe: Optional[Callable[[], Optional[str]]] = None
    count: Optional[Callable[[_Passage], Dict[str, int]]] = None

    def applies(self, language: Optional[str]) -> bool:
        """Whether this method handles the novel's script."""
        if not self.languages:
            return True
        code = _language_code(language)
        return any(code.startswith(prefix) for prefix in self.languages)

    def unavailable_reason(self) -> Optional[str]:
        """Why the method cannot run here, or None when it can."""
        return self.probe() if self.probe else None


@dataclass
class _Collected:
    """One chapter as read, before the batch decides what to store."""

    serial: int
    content_hash: str
    counts: Dict[str, int]
    origins: Dict[str, str]
    method_counts: Dict[str, Dict[str, int]]
    truncated: int = 0


@dataclass(frozen=True)
class ScanTerm:
    """A proposed term after the batch was combined, ranked and classified."""

    source: str
    freq: int
    first_chapter_serial: int
    origin: str
    tier: TermTier
    category: Optional[TermCategory] = None
    pinyin: Optional[str] = None
    fragment_of: Optional[str] = None


@dataclass
class MethodScan:
    """What one method contributed in one chapter.

    Methods overlap by design: the stored count is the union's maximum, so a
    surface that both methods saw belongs to both rows. These figures describe
    a method's own view; adding them up would count the same text twice.
    """

    terms: int = 0
    occurrences: int = 0
    fragments: int = 0
    suppressed: int = 0
    truncated: int = 0


@dataclass
class ChapterScan:
    """What one chapter contributed to the batch."""

    serial: int
    content_hash: str
    counts: Dict[str, int]
    origins: Dict[str, str]
    methods: Dict[str, MethodScan]


@dataclass
class ScanBatch:
    """Everything a batch of chapters produced, before anything is stored."""

    chapters: List[ChapterScan]
    terms: List[ScanTerm]
    extractors: List[str]
    suppressed: int = 0
    fragments: int = 0
    truncated: int = 0

    @property
    def occurrences(self) -> int:
        return sum(count for chapter in self.chapters for count in chapter.counts.values())


def _count_frequency(passage: _Passage) -> Dict[str, int]:
    """Repeat-counted Han/Hangul n-grams and capitalised Latin names."""
    text = passage.plain
    counts: Dict[str, int] = {}
    for run in _CJK_RUN.findall(text):
        for size in range(2, min(MAX_NGRAM, len(run)) + 1):
            for start in range(len(run) - size + 1):
                surface = run[start : start + size]
                if not _STOP_CHARS.isdisjoint(surface) or surface in _HANGUL_STOP:
                    continue
                _add(counts, surface)

    for match in _LATIN_NAME.finditer(text):
        surface = _POSSESSIVE.sub("", _ARTICLE.sub("", match.group(0))).strip()
        words = surface.split()
        if len(surface) < 3 or not words:
            continue
        if any(word.lower() in _LATIN_STOP for word in words):
            continue
        _add(counts, surface)
    return counts


def _count_pos(passage: _Passage) -> Dict[str, int]:
    """jieba's own segmentation: the nouns and proper nouns of the chapter."""
    counts: Dict[str, int] = {}
    for token in passage.tokens:
        word = token.word
        if len(word) < 2 or not _nominal(token.flag, word):
            continue
        if not _STOP_CHARS.isdisjoint(word) or word in _HANGUL_STOP:
            continue
        _add(counts, word)
    return counts


def _passage(plain: str) -> _Passage:
    """Read a chapter once, when a Chinese script is in it."""
    if not _HAS_HAN.search(plain) or not _jieba_ready():
        return _Passage(plain=plain, tokens=[], token_seen=set(), flags={}, votes={})
    return _read(plain)


def _read(plain: str) -> _Passage:
    """Segment a chapter and collect what the gates and the classifier need."""
    import jieba.posseg as posseg

    tokens: List[_Token] = []
    token_seen: set = set()
    flags: Dict[str, set] = {}
    votes: Dict[str, Dict[TermCategory, int]] = {}
    for item in posseg.cut(plain):
        word = str(getattr(item, "word", item) or "").strip()
        if not word:
            continue
        flag = str(getattr(item, "flag", "") or "")
        tokens.append(_Token(word, flag))
        if len(word) > 1 and _HAS_HAN.search(word):
            token_seen.add(word)
            flags.setdefault(word, set()).add(flag)
    for index, token in enumerate(tokens):
        word = token.word
        if len(word) < 2 or not _HAS_HAN.search(word):
            continue
        previous = tokens[index - 1].word if index else ""
        following = tokens[index + 1].word if index + 1 < len(tokens) else ""
        before = tokens[index - 2].word if index >= 2 else ""
        if following in _SPEECH:
            _vote(votes, word, TermCategory.character, 3)
        if previous in _PERSON_CLASSIFIER and before in _NUMERAL:
            _vote(votes, word, TermCategory.character, 2)
        if previous in _MEASURE_ITEM and before in _NUMERAL:
            _vote(votes, word, TermCategory.item, 2)
    return _Passage(plain=plain, tokens=tokens, token_seen=token_seen, flags=flags, votes=votes)


def _vote(
    votes: Dict[str, Dict[TermCategory, int]],
    surface: str,
    category: TermCategory,
    weight: int,
) -> None:
    tally = votes.setdefault(surface, {})
    tally[category] = tally.get(category, 0) + weight


def _jieba_ready() -> bool:
    global _JIEBA_READY
    if _JIEBA_READY is None:
        _JIEBA_READY = _probe_jieba() is None
    return _JIEBA_READY


def _nominal(flag: str, surface: str) -> bool:
    """Whether jieba's tag names a thing rather than grammar."""
    if flag in {"n", "ng", "nz", "j"}:
        return True
    if flag.startswith(("nr", "ns", "nt")):
        return True
    # A short entry jieba calls an idiom is usually a set phrase naming
    # something (`半兽人`); a four-character idiom is not a term.
    return flag == "i" and len(surface) <= 3


def _supported(surface: str, token_seen: set, flags: Dict[str, set]) -> bool:
    """Whether jieba's own segmentation backs this Han surface.

    A span jieba never emits as one token is a boundary artefact (`艘` + `星舰`),
    which is how n-gram counting invents terms. A surface it tags only as
    grammar (`自己`, `知道`, `成功`) is a word, not a name.
    """
    if surface not in token_seen:
        return False
    tags = flags.get(surface)
    return not tags or any(_nominal(tag, surface) for tag in tags)


def _classify(
    surface: str, flags: Iterable[str], votes: Dict[TermCategory, int]
) -> Optional[TermCategory]:
    """What a surface names: its suffix, jieba's tag, then nearby words."""
    by_suffix = _category(surface)
    if by_suffix:
        return by_suffix
    for flag in flags:
        if flag.startswith("nr"):
            return TermCategory.character
        if flag.startswith("ns"):
            return TermCategory.location
        if flag.startswith("nt"):
            return TermCategory.sect_org
    if "nz" in flags:
        # jieba knows it is a proper noun, not what kind; that is still worth
        # recording, because it separates a name from a common noun.
        return TermCategory.special
    if votes:
        ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0].value))
        best, weight = ranked[0]
        if weight >= 3 and weight * 2 >= sum(votes.values()):
            return best
    if not _HAS_HAN.search(surface) and any(char.isalpha() for char in surface):
        # A capitalised Latin surface that survived the stop-word filter is a name.
        return TermCategory.special
    return None


_JIEBA_READY: Optional[bool] = None


def _probe_jieba() -> Optional[str]:
    try:
        import jieba.posseg  # noqa: F401
    except Exception:
        return "jieba is not installed"
    return None


EXTRACTORS: Tuple[Extractor, ...] = (
    Extractor(
        id="frequency",
        name="Frequency",
        description=(
            "Counts repeated Han/Hangul n-grams and capitalised Latin names. "
            "Chinese spans are kept only when jieba's segmentation agrees with them."
        ),
        default=True,
        count=_count_frequency,
    ),
    Extractor(
        id="pos",
        name="Part of speech (jieba)",
        description=(
            "jieba's dictionary and HMM model keep compound names whole and mark people, "
            "places and organisations. Its dictionary is simplified Chinese, so traditional "
            "text is noisier. Chinese scripts only."
        ),
        languages=("zh",),
        default=True,
        probe=_probe_jieba,
        count=_count_pos,
    ),
    Extractor(
        id="seed",
        name="Imported list",
        description="Terms you paste in, one per line: no chapter is read and no count is recorded.",
        kind="import",
    ),
)

EXTRACTOR_BY_ID: Dict[str, Extractor] = {extractor.id: extractor for extractor in EXTRACTORS}


def describe_extractors(language: Optional[str] = None) -> List[Dict[str, Any]]:
    """The method list the review screen renders, with availability and reasons."""
    described: List[Dict[str, Any]] = []
    for extractor in EXTRACTORS:
        if extractor.kind == "scan" and not extractor.applies(language):
            available, reason = False, f"only for {', '.join(extractor.languages)}"
        elif extractor.kind != "scan":
            available, reason = True, None
        else:
            reason = extractor.unavailable_reason()
            available = reason is None
        described.append(
            {
                "id": extractor.id,
                "name": extractor.name,
                "description": extractor.description,
                "kind": extractor.kind,
                "languages": list(extractor.languages),
                "default": extractor.default and extractor.applies(language),
                "available": available,
                "reason": reason,
            }
        )
    return described


def default_extractors(language: Optional[str] = None) -> List[str]:
    """The methods a scan runs when the caller does not choose: every usable default."""
    return [
        extractor.id
        for extractor in EXTRACTORS
        if extractor.kind == "scan"
        and extractor.default
        and extractor.applies(language)
        and extractor.unavailable_reason() is None
    ]


def resolve_extractors(
    ids: Optional[Sequence[str]],
    language: Optional[str] = None,
) -> List[Extractor]:
    """The methods a scan runs, validated.

    No ids means the defaults for the novel's script. An explicit id that cannot
    run raises, because a caller who asked for jieba deserves the reason rather
    than a silently different scan.
    """
    if not ids:
        return [EXTRACTOR_BY_ID[extractor_id] for extractor_id in default_extractors(language)]

    chosen: List[Extractor] = []
    for raw in ids:
        extractor = EXTRACTOR_BY_ID.get(str(raw or "").strip().lower())
        if extractor is None:
            raise ValueError(f"Unknown extraction method: {raw}")
        if extractor.kind != "scan":
            raise ValueError(f"{extractor.name} is imported, not scanned")
        if not extractor.applies(language):
            raise ValueError(f"{extractor.name} does not handle {language or 'this language'}")
        reason = extractor.unavailable_reason()
        if reason:
            raise ValueError(f"{extractor.name} is unavailable: {reason}")
        if extractor not in chosen:
            chosen.append(extractor)
    return chosen


def extract_terms(
    pages: Iterable[Tuple[int, str]],
    *,
    language: Optional[str] = None,
    extractors: Optional[Sequence[str]] = None,
    min_freq: Optional[int] = None,
) -> ScanBatch:
    """Propose the recurring surfaces of `pages` — (chapter serial, chapter text).

    Every selected method reads every chapter; the union per chapter is the
    maximum count any one method saw, so overlapping methods cannot inflate a
    term. Fragments of kept surfaces are returned too, marked with their
    canonical parent, so a reviewer can promote one.
    """
    methods = resolve_extractors(extractors, language)
    per_chapter: List[_Collected] = []
    token_seen: set = set()
    flags: Dict[str, set] = {}
    votes: Dict[str, Dict[TermCategory, int]] = {}
    for serial, text in pages:
        source = str(text or "")
        plain = _plain_text(source)
        passage = _passage(plain)
        token_seen.update(passage.token_seen)
        for surface, tags in passage.flags.items():
            flags.setdefault(surface, set()).update(tags)
        for surface, tally in passage.votes.items():
            target = votes.setdefault(surface, {})
            for category, weight in tally.items():
                target[category] = target.get(category, 0) + weight
        counts: Dict[str, int] = {}
        origins: Dict[str, str] = {}
        method_counts: Dict[str, Dict[str, int]] = {}
        for method in methods:
            if method.count is None:
                continue
            found = method.count(passage)
            method_counts[method.id] = found
            for surface, value in found.items():
                if surface not in counts:
                    origins[surface] = method.id
                _merge(counts, surface, value)
        truncated = 0
        if len(counts) > MAX_CANDIDATES_PER_CHAPTER:
            ranked = sorted(counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))[
                :MAX_CANDIDATES_PER_CHAPTER
            ]
            truncated = len(counts) - len(ranked)
            counts = dict(ranked)
            origins = {surface: where for surface, where in origins.items() if surface in counts}
            method_counts = {
                method_id: {
                    surface: value for surface, value in values.items() if surface in counts
                }
                for method_id, values in method_counts.items()
            }
        per_chapter.append(
            _Collected(
                serial=int(serial),
                content_hash=_digest(source),
                counts=counts,
                origins=origins,
                method_counts=method_counts,
                truncated=truncated,
            )
        )

    totals: Dict[str, int] = {}
    best: Dict[str, int] = {}
    first: Dict[str, int] = {}
    origin: Dict[str, str] = {}
    for chapter in per_chapter:
        for surface, found in chapter.counts.items():
            totals[surface] = totals.get(surface, 0) + found
            best[surface] = max(best.get(surface, 0), found)
            first.setdefault(surface, chapter.serial)
            origin.setdefault(surface, chapter.origins.get(surface) or methods[0].id)

    stored_peaks = {
        surface: peak
        for surface, peak in best.items()
        if surface not in _COMMON_NOUNS
        and peak >= (min_freq if min_freq is not None else _min_freq(surface))
    }
    if token_seen:
        # For a Chinese script jieba's segmentation is the referee: a span it
        # never emits as one token is two words glued together by an n-gram,
        # and one it tags only as grammar is a word, not a name.
        stored_peaks = {
            surface: peak
            for surface, peak in stored_peaks.items()
            if not _HAS_HAN.search(surface) or _supported(surface, token_seen, flags)
        }
    # A surface that met the default threshold (core) may only be shadowed by
    # another core surface: a one-off kept by a harvest pass must never hide a
    # term the default scan accepted, or one cheap pass buries half the
    # glossary. Tails may shadow and be shadowed freely, which is what keeps a
    # harvest pass' long-tail n-gram families out of the review list.
    core = {surface for surface, peak in stored_peaks.items() if peak >= _min_freq(surface)}
    kept, parent_of = _maximal(stored_peaks)
    fragment_of = {
        surface: parent
        for surface, parent in parent_of.items()
        if surface in stored_peaks
        and surface not in kept
        and (surface not in core or parent in core)
    }
    stored = list(stored_peaks)

    terms: List[ScanTerm] = []
    for surface in sorted(stored, key=lambda item: (-totals[item], -len(item), item)):
        tier = _tier(surface, best[surface])
        terms.append(
            ScanTerm(
                source=surface,
                freq=totals[surface],
                first_chapter_serial=first[surface],
                origin=origin[surface],
                tier=tier,
                category=_classify(surface, flags.get(surface, ()), votes.get(surface, {})),
                pinyin=_pinyin(surface, language) if tier is TermTier.core else None,
                fragment_of=fragment_of.get(surface),
            )
        )

    chapters: List[ChapterScan] = []
    for chapter in per_chapter:
        present = {
            surface: found for surface, found in chapter.counts.items() if surface in stored_peaks
        }
        methods_seen: Dict[str, MethodScan] = {}
        for method_id, values in chapter.method_counts.items():
            mine = {surface: found for surface, found in values.items() if surface in stored_peaks}
            methods_seen[method_id] = MethodScan(
                terms=len(mine),
                occurrences=sum(mine.values()),
                fragments=sum(1 for surface in mine if surface in fragment_of),
                suppressed=len(values) - len(mine),
            )
        # The per-chapter cap applies to the union, so it is recorded once, on
        # the first method's row, and totals stay honest.
        first_method = methods[0].id if methods else ""
        row = methods_seen.setdefault(first_method, MethodScan())
        row.truncated = chapter.truncated
        chapters.append(
            ChapterScan(
                serial=chapter.serial,
                content_hash=chapter.content_hash,
                counts=present,
                origins={
                    surface: chapter.origins[surface]
                    for surface in present
                    if surface in chapter.origins
                },
                methods=methods_seen,
            )
        )

    return ScanBatch(
        chapters=chapters,
        terms=terms,
        extractors=[method.id for method in methods],
        suppressed=sum(
            stats.suppressed for chapter in chapters for stats in chapter.methods.values()
        ),
        fragments=len(fragment_of),
        truncated=sum(
            stats.truncated for chapter in chapters for stats in chapter.methods.values()
        ),
    )


def _merge(counts: Dict[str, int], surface: str, found: int) -> None:
    """Union two methods' view of one chapter: the larger count wins."""
    if found > counts.get(surface, 0):
        counts[surface] = found


def _add(counts: Dict[str, int], surface: str) -> None:
    counts[surface] = counts.get(surface, 0) + 1


def _plain_text(text: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(str(text or ""), "html.parser").get_text(" ")


def _digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", "ignore")).hexdigest()


def _maximal(
    eligible: Dict[str, int],
) -> Tuple[Dict[str, int], Dict[str, str]]:
    """Keep the longest surface and record what it shadows.

    `青云宗` is kept, so `云宗` does not become a term of its own; `青云`
    survives only because it recurs more often than the names containing it.
    What the kept surfaces do account for is returned with its canonical parent
    instead of being dropped, so review can still promote one.
    """
    kept: Dict[str, int] = {}
    shadow: Dict[str, int] = {}
    parent_of: Dict[str, str] = {}
    for surface in sorted(eligible, key=lambda item: (-len(item), item)):
        count = eligible[surface]
        if parent_of.get(surface) and shadow.get(surface, 0) >= count:
            continue
        kept[surface] = count
        for size in range(2, len(surface)):
            for start in range(len(surface) - size + 1):
                fragment = surface[start : start + size]
                if shadow.get(fragment, 0) < count:
                    shadow[fragment] = count
                    parent_of[fragment] = surface
    return kept, parent_of


def _tier(surface: str, peak: int) -> TermTier:
    """core when the surface met the default threshold, tail when only a harvest pass saw it."""
    return TermTier.core if peak >= _min_freq(surface) else TermTier.tail


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
    if _language_code(language) == "ja":
        return None
    from pypinyin import lazy_pinyin

    return " ".join(lazy_pinyin(surface)).strip() or None


def _language_code(language: Optional[str]) -> str:
    return str(language or "").split("-")[0].strip().lower()
