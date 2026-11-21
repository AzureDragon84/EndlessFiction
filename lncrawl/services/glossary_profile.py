"""Language profiles: the language-specific half of glossary extraction.

Extraction has two halves. The generic half — frequency, spread, subsumption,
the term cap — is arithmetic over surfaces and never looks at the script. The
half that cannot be generic lives here: what a word even is in this language,
which surfaces are candidates, which words are noise, and how a name is
romanised. Chinese needs a POS tagger and a boundary repair pass; English needs
capitalisation and sentence-start evidence; Japanese has no spaces but has
katakana runs and honorifics; Korean has particles glued to every noun.

A profile is chosen from the novel's source language. Anything the registry
does not know — or a profile whose optional dependency is missing — degrades to
`FallbackProfile` with one logged warning: a scan with cruder boundaries is
better than a scan that refuses to run, and the alternative (crashing a chapter
pre-pass because a dictionary is not installed) would break translation itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from importlib.util import find_spec
import logging
import re
from typing import ClassVar, Dict, Iterable, List, Optional, Set, Tuple
import unicodedata

logger = logging.getLogger(__name__)


@dataclass
class Batch:
    """What one scan read: per-chapter surfaces and which of them are names."""

    counts: Dict[int, Dict[str, int]] = field(default_factory=dict)
    names: Set[str] = field(default_factory=set)


@lru_cache(maxsize=None)
def _has(module: str) -> bool:
    """Whether an optional package can be imported, decided once per process."""
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False


# Latin-script names: a capitalised run that does not start a sentence in the
# wrong way, minus the words that are capitalised as grammar rather than as
# names. Shared by every profile, because a Chinese or Japanese web novel
# happily drops an English name into the middle of a chapter.
_LATIN_NAME = re.compile(r"(?<![A-Za-z'’-])[A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*){0,3}")
_ARTICLE = re.compile(r"^(?:The|A|An)\s+")
_POSSESSIVE = re.compile(r"['’]s$")
LATIN_STOP = frozenset(
    "the a an and but or if so then when while after before he she it they we you i "
    "his her their our its this that these those what which who whom whose why how where "
    "chapter volume part book prologue epilogue mr mrs ms dr sir yes no oh ah well "
    "jesus god".split()
)

# Any letter in any script, two or more: the crudest possible candidate and the
# floor every profile shares.
_WORD_RUN = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def latin_names(text: str) -> Dict[str, int]:
    """Capitalised runs outside the stop list: Latin-script names."""
    counts: Dict[str, int] = {}
    for match in _LATIN_NAME.finditer(text):
        surface = _POSSESSIVE.sub("", _ARTICLE.sub("", match.group(0))).strip()
        words = surface.split()
        if len(surface) < 3 or not words:
            continue
        if any(word.lower() in LATIN_STOP for word in words):
            continue
        counts[surface] = counts.get(surface, 0) + 1
    return counts


def word_runs(text: str) -> Dict[str, int]:
    """Every letter run of two or more characters, whatever the script."""
    counts: Dict[str, int] = {}
    for match in _WORD_RUN.finditer(text):
        surface = match.group(0)
        counts[surface] = counts.get(surface, 0) + 1
    return counts


def non_latin(surface: str) -> bool:
    """Whether a surface is written in a script where two characters is a word."""
    return bool(_NON_ASCII.search(surface))


def _honorific_stems(counts: Dict[str, int], honorifics: Iterable[str]) -> Set[str]:
    """Bare names an honorific reveals, counted as the address that carried them.

    `田中さん` is evidence for `田中`: the suffix says the surface is a person's
    name, so the bare form is counted too rather than lost with the address.
    """
    stems: Set[str] = set()
    for surface in list(counts):
        for honorific in honorifics:
            bare = surface[: -len(honorific)]
            if surface.endswith(honorific) and len(bare) >= 2:
                counts[bare] = counts.get(bare, 0) + counts[surface]
                stems.add(bare)
                break
    return stems


class LanguageProfile:
    """What one source language contributes to extraction.

    Subclasses override the pieces that are genuinely language-specific. The
    generic stages (frequency, spread across chapters, subsumption, the cap) run
    after `collect()` and never look at `code`.
    """

    code: ClassVar[str] = "fallback"
    label: ClassVar[str] = "Any language"
    #: Optional package this profile needs. Missing means the registry serves
    #: the fallback profile and says so once, instead of failing a scan.
    requires: ClassVar[Optional[str]] = None
    stopwords: ClassVar[frozenset] = frozenset()
    honorifics: ClassVar[Tuple[str, ...]] = ()
    #: Whether `candidates()` already reports Latin-script names. When it does
    #: not, `collect()` adds them: a chapter in any script can still name
    #: something in English.
    candidates_include_latin: ClassVar[bool] = False

    def available(self) -> bool:
        return self.requires is None or _has(self.requires)

    def reading_supported(self) -> bool:
        """Whether `reading()` can return anything for this language at all."""
        return False

    def normalize(self, text: str) -> str:
        return unicodedata.normalize("NFKC", str(text or ""))

    def candidates(self, text: str) -> Dict[str, int]:
        """Surfaces this language offers on its own, with their counts."""
        return word_runs(text)

    def names(self, counts: Dict[str, int]) -> Set[str]:
        """Which candidates are names on this language's own evidence.

        Only a profile that can see a name directly says so here (an honorific
        suffix, a Latin capital); everything else is left to frequency and
        spread, so returning nothing is a valid answer. An implementation may
        add a bare form to `counts` when a suffix is what revealed it.
        """
        return set()

    def collect(self, pages: Iterable[Tuple[int, str]]) -> Batch:
        """Per-chapter candidate counts for a batch of chapters."""
        batch = Batch()
        for serial, text in pages:
            body = self.normalize(text)
            counts = self.candidates(body)
            if not self.candidates_include_latin:
                for surface, count in latin_names(body).items():
                    counts[surface] = counts.get(surface, 0) + count
                    batch.names.add(surface)
            batch.names.update(self.names(counts))
            kept = {
                surface: count for surface, count in counts.items() if surface not in self.stopwords
            }
            if kept:
                batch.counts[serial] = kept
        return batch

    def reading(self, surface: str) -> Optional[str]:
        """The Romanised reading of a surface, or None when there is none."""
        return None

    def describe(self) -> Dict[str, object]:
        return {
            "code": self.code,
            "label": self.label,
            "requires": self.requires,
            "reading": self.reading_supported(),
        }


class ZhProfile(LanguageProfile):
    """Chinese: jieba POS, PMI/entropy repair, suffix and speech-tag patterns.

    The whole Chinese pipeline lives in `glossary_zh` — its stop words and
    titles included — so this class is only what the registry needs: the
    dependency it degrades on, the reading, and the Latin names a Chinese
    chapter still contains.
    """

    code = "zh"
    label = "Chinese"
    requires = "jieba"

    def reading_supported(self) -> bool:
        return _has("pypinyin")

    def reading(self, surface: str) -> Optional[str]:
        if not _has("pypinyin") or not _NON_ASCII.search(surface):
            return None
        from pypinyin import lazy_pinyin

        return " ".join(lazy_pinyin(surface)).strip() or None

    def collect(self, pages: Iterable[Tuple[int, str]]) -> Batch:
        from . import glossary_zh

        texts = [(serial, self.normalize(text)) for serial, text in pages]
        batch = glossary_zh.collect(texts)
        # A Chinese chapter still names things in Latin script; those runs are
        # names on the same evidence as anywhere else.
        for serial, text in texts:
            counts = batch.counts.setdefault(serial, {})
            for surface, count in latin_names(text).items():
                counts[surface] = counts.get(surface, 0) + count
                batch.names.add(surface)
        return batch


class EnProfile(LanguageProfile):
    """English: capitalisation, possessives and a stop list.

    A capitalised run is the whole signal, because there is no segmenter to
    consult; that is also why the scan asks for spread across chapters before
    it keeps a common noun.
    """

    code = "en"
    label = "English"
    candidates_include_latin = True

    def candidates(self, text: str) -> Dict[str, int]:
        return latin_names(text)

    def names(self, counts: Dict[str, int]) -> Set[str]:
        # Every candidate here is a name: a lower-case word never has one.
        return set(counts)


_KATA_RUN = re.compile("[\u30a1-\u30fa\u30fc]{2,}")
_KANJI_RUN = re.compile("[\u4e00-\u9fff]{2,8}")

_JA_SUFFIXES = (
    "帝国",
    "連邦",
    "王国",
    "共和国",
    "学院",
    "学園",
    "騎士団",
    "商会",
    "艦隊",
    "一族",
    "公爵家",
)
_JA_PATTERNS = (
    re.compile("[\u4e00-\u9fff]{1,6}(?:" + "|".join(_JA_SUFFIXES) + ")"),
    re.compile("[\u4e00-\u9fff]{2,4}(?:級|型|号)"),
    re.compile("[\u30a1-\u30fa]{2,10}(?:級|型|号)"),
)
# Suffixes that turn a bare name into an address; the name is the part a
# glossary needs, the suffix is context.
_JA_HONORIFICS = ("さん", "様", "殿", "君", "ちゃん", "先生", "先輩", "氏")

# Katakana → Romaji. Only kana can be read without a dictionary: a kanji
# surface returns None rather than guessing, which is why the Reading column is
# per-term rather than per-novel.
_KANA: Dict[str, str] = {
    "ア": "a",
    "イ": "i",
    "ウ": "u",
    "エ": "e",
    "オ": "o",
    "カ": "ka",
    "キ": "ki",
    "ク": "ku",
    "ケ": "ke",
    "コ": "ko",
    "サ": "sa",
    "シ": "shi",
    "ス": "su",
    "セ": "se",
    "ソ": "so",
    "タ": "ta",
    "チ": "chi",
    "ツ": "tsu",
    "テ": "te",
    "ト": "to",
    "ナ": "na",
    "ニ": "ni",
    "ヌ": "nu",
    "ネ": "ne",
    "ノ": "no",
    "ハ": "ha",
    "ヒ": "hi",
    "フ": "fu",
    "ヘ": "he",
    "ホ": "ho",
    "マ": "ma",
    "ミ": "mi",
    "ム": "mu",
    "メ": "me",
    "モ": "mo",
    "ヤ": "ya",
    "ユ": "yu",
    "ヨ": "yo",
    "ラ": "ra",
    "リ": "ri",
    "ル": "ru",
    "レ": "re",
    "ロ": "ro",
    "ワ": "wa",
    "ヲ": "o",
    "ン": "n",
    "ヴ": "vu",
    "ガ": "ga",
    "ギ": "gi",
    "グ": "gu",
    "ゲ": "ge",
    "ゴ": "go",
    "ザ": "za",
    "ジ": "ji",
    "ズ": "zu",
    "ゼ": "ze",
    "ゾ": "zo",
    "ダ": "da",
    "ヂ": "ji",
    "ヅ": "zu",
    "デ": "de",
    "ド": "do",
    "バ": "ba",
    "ビ": "bi",
    "ブ": "bu",
    "ベ": "be",
    "ボ": "bo",
    "パ": "pa",
    "ピ": "pi",
    "プ": "pu",
    "ペ": "pe",
    "ポ": "po",
    "ァ": "a",
    "ィ": "i",
    "ゥ": "u",
    "ェ": "e",
    "ォ": "o",
    "ャ": "ya",
    "ュ": "yu",
    "ョ": "yo",
}
_SMALL_VOWELS = {"ァ": "a", "ィ": "i", "ゥ": "u", "ェ": "e", "ォ": "o"}
_SMALL_Y = {"ャ": "a", "ュ": "u", "ョ": "o"}


def kana_romaji(text: str) -> Optional[str]:
    """Romaji for a pure-katakana surface, or None when it contains kanji."""
    if not text:
        return None
    out: List[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if char == "ー":
            if out and out[-1][-1:] in "aiueo":
                out.append(out[-1][-1])
            index += 1
            continue
        if char == "ッ":
            syllable = _KANA.get(following) or ""
            if syllable:
                out.append(syllable[0])
            index += 1
            continue
        base = _KANA.get(char)
        if base is None:
            return None
        if following in _SMALL_VOWELS or following in _SMALL_Y:
            vowel = _SMALL_VOWELS.get(following) or _SMALL_Y[following]
            stem = base[:-1] if base[-1:] in "aiueo" else base
            if char == "ウ" and following in _SMALL_VOWELS:
                stem = "w"
            elif char == "ヴ" and following in _SMALL_VOWELS:
                stem = "v"
            out.append(stem + vowel)
            index += 2
            continue
        out.append(base)
        index += 1
    return "".join(out) or None


class JaProfile(LanguageProfile):
    """Japanese: katakana runs, kanji runs, suffix patterns, honorifics.

    A tagger (fugashi, Sudachi) would segment better, but it is a heavy
    optional dependency and a wrong split here only costs the reviewer time,
    so the profile reads the script it can see.
    """

    code = "ja"
    label = "Japanese"
    honorifics = _JA_HONORIFICS
    stopwords = frozenset(
        "これ それ あれ ここ そこ どこ わたし あなた たち です ます ない よう こと もの "
        "ため ところ そう でも しかし そして から まで より だけ しか".split()
    )

    def reading_supported(self) -> bool:
        return True

    def reading(self, surface: str) -> Optional[str]:
        return kana_romaji(surface)

    def candidates(self, text: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for pattern in (_KATA_RUN, _KANJI_RUN):
            for run in pattern.findall(text):
                counts[run] = counts.get(run, 0) + 1
        for pattern in _JA_PATTERNS:
            for match in pattern.finditer(text):
                surface = match.group(0)
                counts[surface] = max(counts.get(surface, 0), 1)
        return counts

    def names(self, counts: Dict[str, int]) -> Set[str]:
        return _honorific_stems(counts, self.honorifics)


_HANGUL_RUN = re.compile("[\uac00-\ud7a3]{2,}")
# Particles glued to the noun; stripping them is what makes `한국은` and `한국이`
# one term instead of two.
_KO_PARTICLES = (
    "에서",
    "에게",
    "으로",
    "부터",
    "까지",
    "이라고",
    "라고",
    "이라",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "도",
    "와",
    "과",
    "로",
    "만",
    "께",
)
_KO_HONORIFICS = ("님", "씨", "군", "양", "선생", "박사")

# Revised Romanization, syllable by syllable. The tables are the standard
# decomposition of U+AC00..U+D7A3, so this needs no dependency.
_KO_INITIAL = [
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s",
    "ss", "", "j", "jj", "ch", "k", "t", "p", "h",
]  # fmt: skip
_KO_VOWEL = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae",
    "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
]  # fmt: skip
_KO_FINAL = [
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "p", "t", "t",
    "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t",
]  # fmt: skip


def hangul_roman(text: str) -> Optional[str]:
    """Revised Romanization of a pure-Hangul surface, or None otherwise."""
    if not text:
        return None
    out: List[str] = []
    for char in text:
        code = ord(char)
        if not 0xAC00 <= code <= 0xD7A3:
            return None
        initial, rest = divmod(code - 0xAC00, 588)
        vowel, final = divmod(rest, 28)
        out.append(_KO_INITIAL[initial] + _KO_VOWEL[vowel] + _KO_FINAL[final])
    return "".join(out) or None


class KoProfile(LanguageProfile):
    """Korean: Hangul runs with particles stripped, honorifics as evidence."""

    code = "ko"
    label = "Korean"
    honorifics = _KO_HONORIFICS
    stopwords = frozenset(
        "그리고 그러나 하지만 그래서 이것 그것 저것 우리 저희 너희 사람 때 것 수 등 및 "
        "있다 없다 하다 되다 이다 아니 그런 이런 저런".split()
    )

    def reading_supported(self) -> bool:
        return True

    def reading(self, surface: str) -> Optional[str]:
        return hangul_roman(surface)

    def candidates(self, text: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for run in _HANGUL_RUN.findall(text):
            surface = run
            for particle in _KO_PARTICLES:
                bare = run[: -len(particle)]
                if run.endswith(particle) and len(bare) >= 2:
                    surface = bare
                    break
            counts[surface] = counts.get(surface, 0) + 1
        return counts

    def names(self, counts: Dict[str, int]) -> Set[str]:
        return _honorific_stems(counts, self.honorifics)


class FallbackProfile(LanguageProfile):
    """Everything else: letter runs, no readings, no language-specific rules.

    It is also what a known language degrades to when its optional dependency
    is absent, so it must never raise.
    """

    code = "fallback"
    label = "Any language (fallback)"


_PROFILES: Dict[str, LanguageProfile] = {
    profile.code: profile
    for profile in (ZhProfile(), EnProfile(), JaProfile(), KoProfile(), FallbackProfile())
}
FALLBACK = _PROFILES["fallback"]

_warned: Set[str] = set()


def _warn(message: str) -> None:
    """Log a degradation once: a per-chapter pre-pass would repeat it forever."""
    if message in _warned:
        return
    _warned.add(message)
    logger.warning(message)


def base_code(language: Optional[str]) -> str:
    """The bare language code: `zh-CN`, `zh_TW` and `zh` are all `zh`."""
    code = str(language or "").strip().lower().replace("_", "-")
    return code.split("-")[0]


def profile_for(language: Optional[str]) -> LanguageProfile:
    """The profile for a language, degraded with a warning when unavailable."""
    code = base_code(language)
    profile = _PROFILES.get(code)
    if profile is None:
        if code:
            _warn(f"No glossary profile for {language!r}; using the fallback profile")
        return FALLBACK
    if not profile.available():
        _warn(
            f"Glossary profile {profile.code!r} needs {profile.requires!r}, which is not"
            " installed; using the fallback profile"
        )
        return FALLBACK
    return profile


def describe_profiles() -> List[Dict[str, object]]:
    """Every registered profile, for the admin's language picker."""
    return [profile.describe() for profile in _PROFILES.values()]
