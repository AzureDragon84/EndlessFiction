"""The JSON contract every paid prompt task shares.

A model answer is prose until proven otherwise: Gemma wraps its reasoning in
`<thought>` blocks, every model fences its code, and some reply with a bare
`{"term": "rendering"}` map instead of the array they were asked for. One
tolerant reader plus one re-ask turns that into the counts an operator can act
on — `asked`, `parsed`, `stored`, `invalid`, `skipped` — instead of the old
"No key in the pool could answer", which a parse failure used to look like.

The category definitions and the casing rule live here too, so the classifier
prompt and the store path agree on one vocabulary and one style.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
from typing import Any, Callable, ClassVar, Dict, List, Optional, Sequence, Tuple, Type, TypeVar

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from ..context import ctx
from ..enums import TermCategory, coerce_category

logger = logging.getLogger(__name__)

# Appended when a reply could not be used: the answer, not the key, was the problem.
RETRY_NOTE = "Respond with JSON only. No prose, no markdown."
# Raw replies are logged truncated: enough to debug a parse, not a log flood.
RAW_LOG_LIMIT = 2000

# A term is Title Case when it names something; these are the categories whose
# renderings are common nouns.
_COMMON_NOUNS = frozenset(
    {
        TermCategory.artifact,
        TermCategory.creature,
        TermCategory.currency,
        TermCategory.special,
    }
)
_SMALL_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
# Characters a finished word cannot end on.
_TRUNCATED_ENDINGS = "-–—&/,(['\"「『“"

# One-line meaning and two examples per member: the classifier prompt carries
# this table verbatim, so the model never has to guess at genre jargon. Order is
# the order a reviewer expects to find them in, not the enum's.
CATEGORY_GUIDE: Dict[TermCategory, Tuple[str, Tuple[str, str]]] = {
    TermCategory.character: ("a person: name, alias or nickname", ("赵辰", "Lin Wan'er")),
    TermCategory.location: (
        "a city, mountain, realm or secret area",
        ("青云城", "Frost Mountains"),
    ),
    TermCategory.sect_org: (
        "a sect, clan, guild, empire or faction",
        ("青云宗", "Star Fleet"),
    ),
    TermCategory.rank_realm: (
        "a cultivation stage or power level",
        ("筑基", "Golden Core"),
    ),
    TermCategory.technique: (
        "a martial art, spell or movement technique",
        ("御剑术", "Nine Heavens Sword Art"),
    ),
    TermCategory.artifact: (
        "an object: weapon, treasure, pill or talisman",
        ("青锋剑", "Qi Pill"),
    ),
    TermCategory.special: (
        "anything that fits no other category",
        ("气运", "Fortune"),
    ),
    TermCategory.honorific: (
        "a title or form of address",
        ("长老", "Senior Brother"),
    ),
    TermCategory.creature: ("a spirit beast, demon or monster", ("妖兽", "Star Beast")),
    TermCategory.race: ("a people or lineage: human, demon, spirit", ("魔族", "Demon Race")),
    TermCategory.formation: (
        "a formation, barrier or seal",
        ("护山大阵", "Nine Gates Array"),
    ),
    TermCategory.constitution: (
        "a physique, innate talent or bloodline",
        ("剑体", "Dragon Bloodline"),
    ),
    TermCategory.currency: ("money and units of measure", ("灵石", "Spirit Stone")),
    TermCategory.concept: ("an abstract idea: Dao, Qi, karma", ("道", "Karma")),
    TermCategory.idiom: (
        "a recurring saying or cultural expression",
        ("三十年河东", "A crane among chickens"),
    ),
}

CASING_NOTE = "Write proper names in Title Case and common nouns in lower case."


@dataclass
class JsonReply:
    """What one paid JSON call produced, and why it fell short when it did."""

    items: List[Any] = field(default_factory=list)
    model: Optional[str] = None
    error: Optional[str] = None


class TermItem(BaseModel):
    """One contract item: the term it is about, plus the field this task fills."""

    source: str
    value_field: ClassVar[str] = ""

    @property
    def value(self) -> str:
        return str(getattr(self, self.value_field, "") or "")


class RenderingItem(TermItem):
    rendering: str = ""
    value_field: ClassVar[str] = "rendering"


class CategoryItem(TermItem):
    category: str = ""
    value_field: ClassVar[str] = "category"


class SurfaceItem(TermItem):
    """One surface a model named as a term, with the category it proposes.

    The extraction prompt asks for `surface` and `category`; `source` is
    accepted too, so the tolerant reader's map fallback and the other tasks'
    contract keep validating against the same base.
    """

    source: str = Field(validation_alias=AliasChoices("surface", "source"))
    category: str = ""
    value_field: ClassVar[str] = "category"


class JudgeItem(TermItem):
    """One verdict: what the surface is, and what to call it if it is a term."""

    verdict: str = ""
    category: str = ""
    rendering: str = ""
    merge_into: str = ""
    value_field: ClassVar[str] = "verdict"


TItem = TypeVar("TItem", bound=TermItem)


def category_prompt(enabled: Optional[Sequence[str]] = None) -> str:
    """The classifier's category table: enum, one-line meaning, two examples.

    `enabled` narrows the table to a novel's configured categories; an empty or
    missing list offers every category, which is the default. The closing note
    is the distinction models get wrong most often: a realm and the art
    practised to reach it belong in different rows.
    """
    wanted = {coerce_category(name) for name in enabled or []}
    rows = [
        f"- {category.value}: {CATEGORY_GUIDE[category][0]}"
        f" (e.g. {CATEGORY_GUIDE[category][1][0]}, {CATEGORY_GUIDE[category][1][1]})"
        for category in CATEGORY_GUIDE
        if not wanted or category in wanted
    ]
    rows.append(
        "A cultivation rank or realm is `rank_realm`; the technique practised to "
        "reach it is `technique`. Never give both the same category."
    )
    return "\n".join(rows)


_THOUGHT = re.compile(r"<thought>.*?</thought>", re.IGNORECASE | re.DOTALL)
_UNCLOSED_THOUGHT = re.compile(r"<thought>.*\Z", re.IGNORECASE | re.DOTALL)


def strip_thought(text: str) -> str:
    """Drop Gemma's `<thought>` blocks and any ``` fences.

    A thought block often contains a JSON-shaped scratch answer; parsing that
    instead of the real reply is how a model that answered correctly looked
    like a parse failure. An unterminated block is dropped too, since nothing
    after it is trustworthy either.
    """
    body = _THOUGHT.sub(" ", str(text or ""))
    body = _UNCLOSED_THOUGHT.sub(" ", body)
    return "\n".join(line for line in body.splitlines() if not line.strip().startswith("```"))


def extract_json(text: str) -> Optional[Any]:
    """The first parseable JSON object or array in `text`, ignoring prose.

    Every `{` or `[` is tried as a start, so a fenced answer, a preamble or a
    trailing "hope this helps" does not matter; the first candidate that
    decodes wins. Slicing "first brace to last brace" fails on all three.
    """
    body = strip_thought(text)
    decoder = json.JSONDecoder()
    for index, char in enumerate(body):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(body[index:])
        except ValueError:
            continue
        if isinstance(value, (dict, list)):
            return value
    return None


def parse_items(text: str, item_model: Type[TItem]) -> List[TItem]:
    """Validate every item of a reply, keeping the ones that fit the schema.

    A bare `{source: value}` map is accepted as well as the array the prompt
    asks for: models produce both, and refusing one is how a good answer used
    to become "nothing was stored".
    """
    payload = extract_json(text)
    if payload is None:
        return []
    if isinstance(payload, dict):
        entries: List[Any] = [
            {"source": source, item_model.value_field: value} for source, value in payload.items()
        ]
    else:
        entries = payload
    items: List[TItem] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            item = item_model.model_validate(entry)
        except ValidationError:
            continue
        if item.source.strip():
            items.append(item)
    return items


def call_items(
    task: str,
    prompt: str,
    item_model: Type[TItem],
    *,
    asked: int = 0,
    retries: int = 1,
    check: Optional[Callable[[TItem], bool]] = None,
    model: Optional[str] = None,
) -> JsonReply:
    """Run one paid JSON prompt, tolerantly, and report what came back.

    `check` rejects an item the schema accepts but the task cannot store (an
    empty or mid-word rendering); a reply with rejected items is re-asked once,
    which is the BUG-10 "truncated rendering" case. A reply with no usable item
    at all is re-asked too, with `RETRY_NOTE`, and its raw text is logged so the
    failure can be read instead of guessed at.

    A parse failure still carries the model that produced it: `model` is never
    null once an engine answered, which is what separates "the model replied
    badly" from "no key could answer".

    `model` pins every attempt, including the re-ask, to one model; an unknown
    model is rejected rather than quietly served by the task chain.
    """
    answered_by: Optional[str] = None
    parsed = 0
    salvaged: List[TItem] = []
    for attempt in range(1 + max(0, retries)):
        reply = ctx.key_pool.generate(
            task,
            prompt if attempt == 0 else f"{prompt}\n\n{RETRY_NOTE}",
            temperature=0.0,
            model=model,
        )
        if reply is None:
            if salvaged:
                # A re-ask that could not reach a key must not throw away the
                # usable answer we already have.
                return JsonReply(items=salvaged, model=answered_by)
            return JsonReply(
                model=answered_by,
                error="No key in the pool could answer; add a key under Keys & models.",
            )
        text, answered_by = reply
        items = parse_items(text, item_model)
        parsed = len(items)
        if items:
            incomplete = check is not None and not all(check(item) for item in items)
            if not incomplete or attempt >= retries:
                # Hand back every parsed item, including one `check` rejected:
                # the caller counts it as invalid, which is what the model did.
                return JsonReply(items=items, model=answered_by)
            logger.info(f"{task}: {answered_by} answered incompletely; asking once more")
            salvaged = items
            continue
        logger.warning(
            f"{task}: unusable reply from {answered_by} ({parsed} item(s) parsed):"
            f" {text[:RAW_LOG_LIMIT]}"
        )
    return JsonReply(
        model=answered_by,
        error=f"unparseable response from {answered_by} ({parsed} of {asked or parsed} terms)",
    )


def looks_truncated(rendering: str) -> bool:
    """A rendering that cannot be the end of a word.

    Empty, punctuation-only, or cut off on a dangling separator — the shapes a
    reply takes when the model runs out of tokens mid-term. Nothing here tries
    to second-guess a complete answer.
    """
    text = str(rendering or "").strip()
    if not text:
        return True
    if not any(char.isalnum() for char in text):
        return True
    return text[-1] in _TRUNCATED_ENDINGS


def normalize_rendering(rendering: str, category: Optional[TermCategory] = None) -> str:
    """One casing per category, applied wherever a model's text is stored.

    Two model versions cannot disagree on style if the store path settles it: a
    proper name is Title Case, a common noun lower case. An unknown category is
    treated as a name, which is what most glossary rows are.
    """
    text = " ".join(str(rendering or "").split())
    if not text:
        return ""
    if coerce_category(category) in _COMMON_NOUNS:
        return text.lower()
    return _title_case(text)


def _title_case(text: str) -> str:
    words: List[str] = []
    for index, word in enumerate(text.split()):
        if any(char.isupper() for char in word[1:]):
            words.append(word)  # keep McDonald, AI, ZhaoChen
        elif index and word.lower() in _SMALL_WORDS:
            words.append(word.lower())
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)
