from enum import Enum, IntEnum
from typing import Any, Dict, Optional


class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"
    LOCAL = "local"


class UserTier(IntEnum):
    BASIC = 0
    PREMIUM = 1
    VIP = 2


class JobType(IntEnum):
    NOVEL = 0
    NOVEL_BATCH = 1
    NOVEL_TRANSLATION = 2
    NOVEL_TRANSLATION_BATCH = 3
    FULL_NOVEL = 5
    FULL_NOVEL_BATCH = 6
    FULL_NOVEL_TRANSLATION = 7
    FULL_NOVEL_TRANSLATION_BATCH = 8
    CHAPTER = 10
    CHAPTER_BATCH = 11
    CHAPTER_TRANSLATION = 12
    CHAPTER_TRANSLATION_BATCH = 13

    VOLUME = 20
    VOLUME_BATCH = 21
    VOLUME_TRANSLATION = 22
    VOLUME_TRANSLATION_BATCH = 23
    IMAGE = 30
    IMAGE_BATCH = 31
    ARTIFACT = 40
    ARTIFACT_BATCH = 41
    SEARCH_SOURCE = 50
    SEARCH_ALL_SOURCES = 51
    FETCH_MISSING = 60
    FETCH_LATEST = 61


class JobStatus(IntEnum):
    PENDING = 0
    RUNNING = 1
    SUCCESS = 2
    FAILED = 3
    CANCELED = 4
    PAUSED = 5


class JobPriority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2


class OutputFormat(str, Enum):
    json = "json"
    epub = "epub"
    text = "txt"
    pdf = "pdf"
    mobi = "mobi"
    docx = "docx"
    rtf = "rtf"
    fb2 = "fb2"
    azw3 = "azw3"
    lit = "lit"
    lrf = "lrf"
    pdb = "pdb"
    rb = "rb"
    tcr = "tcr"
    htmlz = "htmlz"
    kepub = "kepub"
    pmlz = "pmlz"
    snb = "snb"

    def __str__(self) -> str:
        return self.value


class NotificationItem(IntEnum):
    JOB_RUNNING = 10
    JOB_SUCCESS = 20
    JOB_FAILURE = 30
    JOB_CANCELED = 40
    NOVEL_SUCCESS = 50
    ARTIFACT_SUCCESS = 60


class FeedbackType(IntEnum):
    GENERAL = 0
    ISSUE = 1
    FEATURE = 2


class FeedbackStatus(IntEnum):
    PENDING = 0
    ACCEPTED = 1
    RESOLVED = 2


class NovelSort(str, Enum):
    popular = "popular"
    updated = "updated"
    created = "created"
    chapters = "chapters"
    title_asc = "title_asc"
    title_desc = "title_desc"


class ActivityType(IntEnum):
    LIBRARY = 1
    NOVEL = 2
    NOVEL_TRANSLATION = 3
    VOLUME = 4
    VOLUME_TRANSLATION = 5
    CHAPTER = 6
    CHAPTER_TRANSLATION = 7
    ACCOUNT = 8
    SOURCES = 9
    REQUEST = 10
    DOWNLOAD = 11
    ARTIFACT = 12


class LanguageCode(str, Enum):
    arabic = "ar"
    bangla = "bn"
    chinese = "zh"
    chinese_simplified = "zh-cn"
    chinese_traditional = "zh-tw"
    english = "en"
    french = "fr"
    german = "de"
    hindi = "hi"
    indonesian = "id"
    japanese = "ja"
    korean = "ko"
    portuguese = "pt"
    russian = "ru"
    spanish = "es"
    thai = "th"
    turkish = "tr"
    urdu = "ur"
    vietnamese = "vi"


class TermCategory(str, Enum):
    """What kind of thing a term names, in the shape a translator works in.

    These are the buckets a web-novel glossary is actually kept in: a realm and
    the technique practised to reach it are different decisions, a formation is
    not an array, and a title of address is not a person. `special` is the
    deliberate catch-all — every candidate lands somewhere on the first pass,
    and an editor drains it into the specific members as the sheet fills up.
    """

    character = "character"
    location = "location"
    sect_org = "sect_org"
    rank_realm = "rank_realm"
    technique = "technique"
    artifact = "artifact"
    special = "special"
    honorific = "honorific"
    creature = "creature"
    race = "race"
    formation = "formation"
    constitution = "constitution"
    currency = "currency"
    concept = "concept"
    idiom = "idiom"


# Member names stored before this set. Enum columns hold the name, so a rename
# is a data change rather than a schema change: the migration script rewrites
# the server's rows and the boot-time map covers SQLite, which never replays
# scripts. Reading through `coerce_category()` keeps an old value usable in the
# meantime. The neutral set in the middle of the list is what this fork shipped
# first; `bloodline` went through `system` and comes back out as a physique.
TERM_CATEGORY_RENAMES: Dict[str, TermCategory] = {
    "place": TermCategory.location,
    "organization": TermCategory.sect_org,
    "title": TermCategory.honorific,
    "rank": TermCategory.rank_realm,
    "item": TermCategory.artifact,
    "system": TermCategory.special,
    "ship": TermCategory.special,
    "event": TermCategory.special,
    "other": TermCategory.special,
    "bloodline": TermCategory.constitution,
}


def coerce_category(value: Any) -> Optional[TermCategory]:
    """A category from a stored or proposed value, including a pre-§5.2 name."""
    if isinstance(value, TermCategory):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    try:
        return TermCategory(text)
    except ValueError:
        return TERM_CATEGORY_RENAMES.get(text)


class TermVerdict(str, Enum):
    """What review decided about a candidate surface.

    `skip` is noise and `fragment` is a piece of a longer term, so both are
    hidden from the default review sheet; `keep` was accepted and `merge`
    points at a canonical term. Null (no verdict) is the undecided state.
    """

    keep = "keep"
    skip = "skip"
    fragment = "fragment"
    merge = "merge"


# What a model may call each verdict; a paid answer is prose until mapped.
_VERDICT_WORDS = {
    "keep": TermVerdict.keep,
    "ok": TermVerdict.keep,
    "term": TermVerdict.keep,
    "skip": TermVerdict.skip,
    "noise": TermVerdict.skip,
    "common": TermVerdict.skip,
    "fragment": TermVerdict.fragment,
    "part": TermVerdict.fragment,
    "partial": TermVerdict.fragment,
    "merge": TermVerdict.merge,
    "alias": TermVerdict.merge,
    "variant": TermVerdict.merge,
}


def coerce_verdict(value: Any) -> Optional[TermVerdict]:
    """A verdict from a stored, submitted or model-proposed value."""
    if isinstance(value, TermVerdict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    try:
        return TermVerdict(text)
    except ValueError:
        return _VERDICT_WORDS.get(text)


# Verdicts the review sheet keeps out of the way until "Show hidden" is on.
HIDDEN_VERDICTS = (TermVerdict.skip, TermVerdict.fragment)


class TranslationMode(str, Enum):
    """How a chapter is presented to a reader.

    `raw` serves the crawled source, `web` a keyless machine translation,
    `web_plus` that translation with the glossary enforced, and `ai` the
    keyed model translation with glossary and context.
    """

    raw = "raw"
    web = "web"
    web_plus = "web_plus"
    ai = "ai"


class TermGender(str, Enum):
    unknown = "unknown"
    male = "male"
    female = "female"
    neutral = "neutral"


class TermStrategy(str, Enum):
    transliteration = "transliteration"
    literal = "literal"
    literary = "literary"
    concise = "concise"
