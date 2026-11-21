"""Admin translation surface and reader glossary layer.

The external translator package already mounts its own dashboard at
`/api/translator`; this router is the part lncrawl owns: the key pool the
notebook used, the free glossary scan, and the one paid pass that settles a
batch. Reader overrides live here too, because they change what a reader sees
without touching any stored translation.
"""

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Path, Query, Security
import sqlmodel as sq

from ...context import ctx
from ...dao import (
    Chapter,
    ChapterTranslation,
    GlossaryEntry,
    GlossaryTerm,
    Novel,
    User,
)
from ...enums import TermCategory, coerce_category, coerce_verdict
from ...exceptions import ServerErrors
from ...services.key_pool import TASKS
from ...services.llm_json import (
    CASING_NOTE,
    JudgeItem,
    SurfaceItem,
    call_items,
    category_prompt,
)
from ..models.translation import (
    GlossaryTranslateRequest,
    OverrideRequest,
    PoolChainRequest,
    PoolUpdateRequest,
    StyleRequest,
    TermBlockRequest,
    TermMergeRequest,
    TermUpdateRequest,
)
from ..security import ensure_admin, ensure_user

router = APIRouter()

_TASK_HINTS = {
    "extract": (
        "You judge candidate glossary terms from a Chinese web novel. Each candidate "
        "comes with the sentences it was found in. Decide whether it belongs in the "
        "glossary and, if it is a term, give its category and English rendering."
    ),
}

_DECIDE_CONTRACT = (
    "Return ONLY a JSON array of objects, one per candidate, for example "
    '[{"source": "青云宗", "verdict": "keep", "category": "sect_org", '
    '"rendering": "Qingyun Sect", "merge_into": ""}]. '
    "verdict is keep (a real name, place, sect or faction, rank, technique or item "
    "of this novel), skip (ordinary vocabulary, a phrase or system noise), fragment "
    "(only part of a longer term) or merge (another way of writing a term listed here: "
    "put that term in merge_into and leave rendering empty). "
    "For keep, add the category and an English rendering."
)
# One decision batch; a bigger batch costs one long reply for the same decisions.
DECIDE_BATCH = 50
_SURFACE_HINT = (
    "You extract glossary terms from Chinese web-novel chapter text. A term is a "
    "proper name a translator must render the same way every time: a person, "
    "place, sect, rank or realm, technique, item, formation, creature, race or "
    "title. Skip ordinary vocabulary, pronouns, measure words, numbers, "
    "sound effects, system messages, stat names and job descriptions such as "
    "舰长 or 工程师: a term names one thing, not a phrase that describes it."
)
_SURFACE_CONTRACT = (
    "Return ONLY a JSON array of objects, for example "
    '[{"surface": "青云宗", "category": "sect_org"}]. '
    "Use the surface exactly as it appears in the text, and list each surface "
    "once however often it occurs. An empty array is a valid answer.\n" + category_prompt()
)


def _task_result(
    asked: int,
    parsed: int,
    stored: int,
    invalid: int,
    skipped: int,
    model: Optional[str],
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """The one shape every paid glossary task returns.

    `stored + invalid + skipped == asked` always, so a partial or malformed
    model answer is visible as a shortfall instead of looking like success.
    """
    return {
        "asked": asked,
        "parsed": parsed,
        "stored": stored,
        "invalid": invalid,
        "skipped": skipped,
        "model": model,
        "error": error,
    }


def _language(value: str) -> str:
    return (value or "en").strip().lower()


def _style_note(style: Dict[str, Any]) -> str:
    """The novel's honorific policy as one instruction line for a paid prompt.

    A rendering cannot decide this for itself: a sect master either keeps the
    title or does not.
    """
    return (
        "Keep a character's honorific with the name (Sect Master Zhao)."
        if style.get("honorifics") == "keep"
        else "Drop honorifics from renderings and translate the name alone."
    )


# ------------------------------------------------------------------ #
#                             Key pool                               #
# ------------------------------------------------------------------ #


@router.get("/pool", summary="Key pool status", dependencies=[Security(ensure_admin)])
def pool_status() -> Dict[str, Any]:
    return ctx.key_pool.status()


@router.put("/pool", summary="Update the key pool", dependencies=[Security(ensure_admin)])
def pool_update(body: PoolUpdateRequest = Body()) -> Dict[str, Any]:
    translator = ctx.config.translator
    if body.api_keys is not None:
        translator.api_keys = body.api_keys
    if body.pool_models is not None:
        translator.pool_models = body.pool_models
    if body.pool_tasks is not None:
        translator.pool_tasks = body.pool_tasks
    if body.auto_pause_on_daily is not None:
        translator.auto_pause_on_daily = body.auto_pause_on_daily
    if body.reset_tz is not None:
        translator.reset_tz = body.reset_tz
    if body.max_request_tokens is not None:
        translator.max_request_tokens = body.max_request_tokens
    if body.web_engine_order is not None:
        translator.web_engine_order = body.web_engine_order
    if body.enabled is not None:
        translator.enabled = body.enabled

    ctx.key_pool.ensure_config()
    ctx.translator.reload()
    return ctx.key_pool.status()


def pinned_model(
    model: Optional[str] = Query(
        default=None, description="Pin every attempt, including the re-ask, to this model"
    ),
) -> Optional[str]:
    """The `model` query parameter, or a 422 when the pool has no such model.

    A dependency rather than a line inside each handler: a typo must read as a
    422 before any work is planned, including when nothing is pending, and the
    check itself lives once, in `KeyPool.pin`.
    """
    return ctx.key_pool.pin(model)


@router.put(
    "/pool/chain",
    summary="Remember one task's default model",
    dependencies=[Security(ensure_admin)],
)
def pool_chain(body: PoolChainRequest) -> Dict[str, Any]:
    """Write `pool_tasks[task] = [model]`, which is what the next run starts from.

    An empty model clears the entry, putting the task back on the translate
    chain. The chains live in the config file, so they survive a restart.
    """
    if body.task not in TASKS:
        raise ServerErrors.invalid_input.with_extra(
            f"Unknown task {body.task!r}; expected one of {', '.join(TASKS)}."
        )
    tasks = dict(ctx.config.translator.pool_tasks or {})
    pinned = ctx.key_pool.pin(body.model)
    # An empty chain means "no default", which `chain()` reads as the translate
    # chain; storing it is the same as not having the key at all.
    tasks[body.task] = [pinned] if pinned else []
    ctx.config.translator.pool_tasks = tasks
    return ctx.key_pool.status()


@router.post(
    "/pool/clear",
    summary="Remove every saved key, its ledger and its health rows",
    dependencies=[Security(ensure_admin)],
)
def pool_clear() -> Dict[str, Any]:
    state = ctx.key_pool.clear()
    ctx.translator.reload()
    return state


@router.post(
    "/pool/probe",
    summary="Check every key and model against Google",
    dependencies=[Security(ensure_admin)],
)
def pool_probe(
    key: Optional[str] = Query(default=None, description="Only this key label"),
    model: Optional[str] = Query(default=None, description="Only this model"),
) -> Dict[str, Any]:
    ctx.key_pool.probe(labels=[key] if key else None, models=[model] if model else None)
    return ctx.key_pool.status()


@router.post(
    "/pool/estimate",
    summary="Pre-flight estimate for a run",
    dependencies=[Security(ensure_admin)],
)
def pool_estimate(
    chapters: int = Query(default=1, ge=0),
    chars_per_chapter: int = Query(default=4000, ge=100),
) -> Dict[str, Any]:
    return ctx.key_pool.estimate(chapters, chars_per_chapter)


@router.post(
    "/pool/generate",
    summary="Run one prompt on the pool (the glossary pass uses this)",
    dependencies=[Security(ensure_admin)],
)
def pool_generate(
    task: str = Query(default="translate"),
    prompt: str = Body(embed=True),
) -> Dict[str, Any]:
    result = ctx.key_pool.generate(task, prompt)
    if result is None:
        return {"text": None, "model": None}
    text, model = result
    return {"text": text, "model": model}


@router.get(
    "/overview",
    summary="Dashboard counters: library, translations, glossary and pool",
    dependencies=[Security(ensure_admin)],
)
def overview(
    language: str = Query(default="en"),
    novel_id: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    lang = _language(language)
    with ctx.db.session() as sess:
        novels = sess.scalar(sq.select(sq.func.count()).select_from(Novel)) or 0
        chapter_stmt = sq.select(sq.func.count()).select_from(Chapter)
        translation_stmt = sq.select(sq.func.count()).select_from(ChapterTranslation)
        term_stmt = sq.select(sq.func.count()).select_from(GlossaryTerm)
        decided_stmt = (
            sq.select(sq.func.count())
            .select_from(GlossaryEntry)
            .join(GlossaryTerm, sq.col(GlossaryTerm.id) == sq.col(GlossaryEntry.term_id))
            .where(
                sq.col(GlossaryEntry.language) == lang,
                sq.col(GlossaryEntry.rendering).is_not(None),
            )
        )
        if novel_id:
            chapter_stmt = chapter_stmt.where(sq.col(Chapter.novel_id) == novel_id)
            translation_stmt = translation_stmt.where(
                sq.col(ChapterTranslation.novel_id) == novel_id,
                sq.col(ChapterTranslation.language) == lang,
            )
            term_stmt = term_stmt.where(sq.col(GlossaryTerm.novel_id) == novel_id)
            decided_stmt = decided_stmt.where(sq.col(GlossaryTerm.novel_id) == novel_id)
        else:
            translation_stmt = translation_stmt.where(sq.col(ChapterTranslation.language) == lang)
        chapters = sess.scalar(chapter_stmt) or 0
        translated = sess.scalar(translation_stmt) or 0
        terms = sess.scalar(term_stmt) or 0
        decided = sess.scalar(decided_stmt) or 0

    pool = ctx.key_pool.status()
    return {
        "language": lang,
        "novel_id": novel_id,
        "novels": novels,
        "chapters": chapters,
        "translated": translated,
        "terms": terms,
        "decided": decided,
        "pool": {
            "configured": pool["configured"],
            "paused": pool["paused"],
            "units": len(pool["units"]),
            "keys": len({unit["project"] for unit in pool["units"]}),
            "ready": sum(1 for unit in pool["units"] if unit["state"] == "ok"),
            "free_today": sum(unit["rpd"] - unit["used_today"] for unit in pool["units"]),
            "reset_at": pool["reset_at"],
            "enabled": pool["settings"]["enabled"],
        },
    }


# ------------------------------------------------------------------ #
#                         Chapter translation                        #
# ------------------------------------------------------------------ #


@router.get(
    "/{novel_id}/chapters",
    summary="Chapter translation status for one novel",
    dependencies=[Security(ensure_admin)],
)
def chapter_statuses(
    novel_id: str = Path(),
    language: str = Query(default="en"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, Any]:
    lang = _language(language)
    chapters = ctx.chapters.list(novel_id=novel_id)
    batch = chapters[offset : offset + limit]
    with ctx.db.session() as sess:
        translated = set(
            sess.exec(
                sq.select(ChapterTranslation.chapter_serial).where(
                    sq.col(ChapterTranslation.novel_id) == novel_id,
                    sq.col(ChapterTranslation.language) == lang,
                )
            ).all()
        )
    items = []
    for chapter in batch:
        items.append(
            {
                "id": chapter.id,
                "serial": chapter.serial,
                "title": chapter.title,
                "has_source": chapter.is_available,
                "has_ai": chapter.serial in translated,
                "has_web": ctx.files.exists(
                    ChapterTranslation.mode_path(novel_id, chapter.serial, lang, "web")
                ),
                "has_web_plus": ctx.files.exists(
                    ChapterTranslation.mode_path(novel_id, chapter.serial, lang, "web_plus")
                ),
            }
        )
    return {
        "novel_id": novel_id,
        "language": lang,
        "total": len(chapters),
        "offset": offset,
        "limit": limit,
        "items": items,
    }


@router.post(
    "/selftest",
    summary="Prove the translation plumbing works without spending a key",
    dependencies=[Security(ensure_admin)],
)
def translation_selftest() -> Dict[str, Any]:
    """Compile the splitters, chunk HTML and round-trip a glossary.

    These are the failures that used to surface as "503 quota exhausted": a
    pattern that cannot compile, a chunker that loses markup, a glossary
    round-trip that does not restore its terms, a pool with no engine. No
    engine call and no key is involved.
    """
    return ctx.translator.selftest()


# ------------------------------------------------------------------ #
#                             Glossary                             #
# ------------------------------------------------------------------ #


@router.delete(
    "/glossary",
    summary="Wipe glossary terms, decisions, proposals and revisions",
    dependencies=[Security(ensure_admin)],
)
def glossary_clear(novel_id: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    """Remove the glossary for one novel, or for every novel when unset."""
    return ctx.terms.clear(novel_id)


@router.get(
    "/{novel_id}/glossary",
    summary="Glossary review sheet",
    dependencies=[Security(ensure_admin)],
)
def glossary_list(
    novel_id: str = Path(),
    language: str = Query(default="en"),
    category: Optional[str] = Query(default=None),
    source: Optional[str] = Query(
        default=None, description="Only what this found: `local` or a model id"
    ),
    query: str = Query(default=""),
    hidden: bool = Query(
        default=False, description="Include skip/fragment verdicts and blocked surfaces"
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(
        default=50,
        ge=0,
        le=2000,
        description="Rows to return; 0 means all, capped at 2000",
    ),
) -> Dict[str, Any]:
    return ctx.terms.list_terms(
        novel_id,
        _language(language),
        category=category,
        found_by=source,
        query=query,
        offset=offset,
        limit=limit,
        show_hidden=hidden,
    )


@router.get(
    "/{novel_id}/style",
    summary="Per-novel source language and rendering style",
    dependencies=[Security(ensure_admin)],
)
def glossary_style(novel_id: str = Path()) -> Dict[str, Any]:
    """The language profile serving this novel and the style its prompts use.

    `requested_code` versus `profile.code` is how the panel shows that a
    missing optional dependency degraded this novel to the fallback profile.
    """
    return ctx.terms.style_report(novel_id)


@router.put(
    "/{novel_id}/style",
    summary="Set a novel's source language and rendering style",
    dependencies=[Security(ensure_admin)],
)
def glossary_set_style(novel_id: str = Path(), body: StyleRequest = Body()) -> Dict[str, Any]:
    """Store the style in `Novel.extra`; an omitted field keeps its value."""
    ctx.terms.set_translation_style(
        novel_id,
        honorifics=body.honorifics,
        categories=body.categories,
        language=body.language,
    )
    return ctx.terms.style_report(novel_id)


@router.post(
    "/{novel_id}/glossary/extract",
    summary="Tab 1: extract recurring terms from a batch of chapters",
    dependencies=[Security(ensure_admin)],
)
def glossary_extract(
    novel_id: str = Path(),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=40, ge=1, le=200),
    min_freq: int = Query(default=2, ge=1, le=10),
    term_limit: int = Query(
        default=200,
        ge=10,
        le=5000,
        description="Term cap for this batch; echoed back as `cap` with `capped`",
    ),
) -> Dict[str, Any]:
    return ctx.terms.scan(
        novel_id,
        offset=offset,
        limit=limit,
        min_freq=min_freq,
        term_limit=term_limit,
    )


@router.get(
    "/{novel_id}/glossary/extract/plan",
    summary="Pre-flight: calls and headroom for a paid extraction run",
    dependencies=[Security(ensure_admin)],
)
def glossary_extract_plan(
    novel_id: str = Path(),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=40, ge=1, le=200),
) -> Dict[str, Any]:
    """What the LLM extractor would spend on this batch, sending nothing.

    The panel prints this line before a run and the run re-checks it, so the
    number an admin is shown is the number the pool is asked for.
    """
    batch = ctx.terms.llm_batch(novel_id, offset, limit)
    headroom = ctx.key_pool.estimate(0)
    calls = len(batch.chunks)
    return {
        "offset": offset,
        "chapters": len(batch.chapters),
        "readable": len(batch.pages),
        "chars": batch.chars,
        "calls": calls,
        "remaining_calls_today": headroom["remaining_calls_today"],
        "enough": calls <= headroom["remaining_calls_today"],
        "reset_in_seconds": headroom["reset_in_seconds"],
    }


@router.post(
    "/{novel_id}/glossary/extract/llm",
    summary="Tab 1b: let the extract chain name the terms of one batch",
    dependencies=[Security(ensure_admin)],
)
def glossary_extract_llm(
    novel_id: str = Path(),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=40, ge=1, le=200),
    term_limit: int = Query(default=200, ge=10, le=5000),
    model: Optional[str] = Depends(pinned_model),
) -> Dict[str, Any]:
    """The opt-in second extraction mode: one paid call per ~8k chars.

    The free scan stays the default; this runs on the same batch so the two can
    be compared honestly. It is refused before a single call when the batch
    needs more requests than the pool has left today.
    """
    batch = ctx.terms.llm_batch(novel_id, offset, limit)
    if not batch.chunks:
        return {
            **_task_result(0, 0, 0, 0, 0, None, "No crawled chapter text in this batch."),
            "found": 0,
            "found_total": 0,
            "new": 0,
            "updated": 0,
            "blocked": 0,
            "hidden": 0,
            "calls": 0,
            "chapters": len(batch.chapters),
            "readable": 0,
            "chars": 0,
            "offset": offset,
            "next_offset": offset + len(batch.chapters),
            "total_chapters": batch.total_chapters,
            "cap": term_limit,
            "capped": False,
        }
    headroom = ctx.key_pool.estimate(0)
    if len(batch.chunks) > headroom["remaining_calls_today"]:
        raise ServerErrors.pool_headroom_insufficient.with_extra(
            f"~{len(batch.chunks)} call(s) needed, "
            f"{headroom['remaining_calls_today']} left today. Nothing was sent."
        )
    proposed: Dict[str, Optional[TermCategory]] = {}
    answered_by: Optional[str] = None
    error: Optional[str] = None
    answered = 0
    for chunk in batch.chunks:
        text = "\n\n".join(page for _, page in chunk)
        prompt = f"{_SURFACE_HINT}\n\n{_SURFACE_CONTRACT}\n\nChapter text:\n{text}"
        reply = call_items("extract", prompt, SurfaceItem, model=model)
        answered += len(reply.items)
        answered_by = reply.model or answered_by
        if reply.error and not error:
            error = reply.error
        for item in reply.items:
            surface = item.source.strip()
            if surface:
                proposed.setdefault(surface, coerce_category(item.category))
    result = ctx.terms.store_llm_terms(
        novel_id,
        batch,
        proposed,
        model=answered_by or "unknown",
        offset=offset,
        requested=max(1, limit),
        term_limit=term_limit,
    )
    stored = max(0, result["found"] - result["blocked"])
    invalid = max(0, len(proposed) - stored)
    if not error and invalid:
        error = f"{invalid} surface(s) were not in the text or were over the cap."
    return {
        **_task_result(
            answered,
            len(proposed),
            stored,
            invalid,
            max(0, answered - len(proposed)),
            answered_by,
            error,
        ),
        "found": result["found"],
        "found_total": result["found_total"],
        "new": result["new"],
        "updated": result["updated"],
        "blocked": result["blocked"],
        "hidden": result["hidden"],
        "calls": len(batch.chunks),
        "chapters": len(batch.chapters),
        "readable": len(batch.pages),
        "chars": batch.chars,
        "offset": offset,
        "next_offset": offset + len(batch.chapters),
        "total_chapters": batch.total_chapters,
        "cap": result["cap"],
        "capped": result["capped"],
        "local": result.get("local", 0),
    }


@router.post(
    "/{novel_id}/glossary/decide",
    summary="Decide a batch: verdict, category and rendering in one paid call",
    dependencies=[Security(ensure_admin)],
)
def glossary_decide(
    novel_id: str = Path(),
    language: str = Query(default="en"),
    model: Optional[str] = Depends(pinned_model),
    body: Optional[GlossaryTranslateRequest] = Body(default=None),
) -> Dict[str, Any]:
    """Settle a batch in one call: verdict, category and English rendering.

    These were three paid stages that each re-read the same sentences, so
    reaching one decision cost three times the quota. The evidence sent with
    each candidate is the sentences it occurs in, because the surface alone
    cannot tell a name from a phrase.
    """
    lang = _language(language)
    limit = min(DECIDE_BATCH, body.limit if body else DECIDE_BATCH)
    queue = ctx.terms.decide_queue(novel_id, lang, limit)
    if not queue:
        return {
            **_task_result(0, 0, 0, 0, 0, None),
            "merged": 0,
            "categorized": 0,
            "rendered": 0,
        }
    entries = [{"source": item["source"], "context": item["context"]} for item in queue]
    style = ctx.terms.translation_style(novel_id)
    prompt = (
        f"{_TASK_HINTS['extract']}\n\n{CASING_NOTE}\n{_style_note(style)}\n"
        f"{_DECIDE_CONTRACT}\n{category_prompt(style['categories'])}\n\n"
        f"Candidates: {json.dumps(entries, ensure_ascii=False)}"
    )
    reply = call_items(
        "extract",
        prompt,
        JudgeItem,
        asked=len(queue),
        check=lambda item: coerce_verdict(item.verdict) is not None,
        model=model,
    )
    wanted = {item["source"] for item in queue}
    answers = [item.model_dump() for item in reply.items if item.source in wanted]
    numbers = ctx.terms.apply_decisions(novel_id, lang, answers, model=reply.model or "unknown")
    invalid = len(answers) - numbers["stored"]
    error = reply.error
    if not error and invalid:
        error = f"{invalid} answer(s) named no term in this batch or changed nothing."
    return {
        **_task_result(
            len(queue),
            len(reply.items),
            numbers["stored"],
            invalid,
            len(queue) - len(answers),
            reply.model,
            error,
        ),
        "merged": numbers["merged"],
        "categorized": numbers["categorized"],
        "rendered": numbers["rendered"],
    }


@router.put(
    "/{novel_id}/glossary/terms/{handle}/merge",
    summary="Link a variant or class to its canonical term",
    dependencies=[Security(ensure_admin)],
)
def glossary_merge_term(
    novel_id: str = Path(),
    handle: str = Path(),
    body: TermMergeRequest = Body(),
) -> Dict[str, Any]:
    """Record an alias group (`莎莎` → `蒋莎莎`); an empty `into` unlinks it."""
    linked = bool(str(body.into or "").strip())
    return {
        "handle": handle,
        "ok": ctx.terms.merge_term(novel_id, handle, body.into),
        "linked": linked,
    }


@router.put(
    "/{novel_id}/glossary/terms/{handle}",
    summary="Admin decision for one term",
    dependencies=[Security(ensure_admin)],
)
def glossary_update_term(
    novel_id: str = Path(),
    handle: str = Path(),
    language: str = Query(default="en"),
    body: TermUpdateRequest = Body(),
) -> Dict[str, Any]:
    result: Dict[str, Any] = {"handle": handle}
    if "category" in body.model_fields_set:
        result["category_ok"] = ctx.terms.set_category(novel_id, handle, body.category)
    if "rendering" in body.model_fields_set:
        result["rendering_ok"] = ctx.terms.set_rendering(
            novel_id, _language(language), handle, body.rendering
        )
    if "verdict" in body.model_fields_set:
        result["verdict_ok"] = ctx.terms.set_verdict(novel_id, handle, body.verdict)
    return result


@router.put(
    "/{novel_id}/glossary/terms/{handle}/block",
    summary="Reject a surface for good",
    dependencies=[Security(ensure_admin)],
)
def glossary_block_term(
    novel_id: str = Path(),
    handle: str = Path(),
    body: Optional[TermBlockRequest] = Body(default=None),
) -> Dict[str, Any]:
    """Add the term's surface to the block-list, which extraction must respect.

    The rejection is keyed by the normalised surface, so a rescan meets it and
    skips it instead of re-creating the row this request is about.
    """
    reason = body.reason if body else None
    return {"handle": handle, "blocked": ctx.terms.block_term(novel_id, handle, reason)}


@router.delete(
    "/{novel_id}/glossary/terms/{handle}/block",
    summary="Allow a rejected surface again",
    dependencies=[Security(ensure_admin)],
)
def glossary_unblock_term(
    novel_id: str = Path(),
    handle: str = Path(),
) -> Dict[str, Any]:
    return {"handle": handle, "unblocked": ctx.terms.unblock_term(novel_id, handle)}


# ------------------------------------------------------------------ #
#                          Reader overrides                          #
# ------------------------------------------------------------------ #


@router.get(
    "/{novel_id}/glossary/card/{handle}",
    summary="Hover card for one term",
)
def glossary_card(
    novel_id: str = Path(),
    handle: str = Path(),
    language: str = Query(default="en"),
    user: User = Security(ensure_user),
) -> Optional[Dict[str, Any]]:
    return ctx.terms.card(novel_id, _language(language), handle, user.id)


@router.put(
    "/{novel_id}/glossary/card/{handle}",
    summary="Set this reader's meaning for one term",
)
def glossary_override(
    novel_id: str = Path(),
    handle: str = Path(),
    language: str = Query(default="en"),
    body: OverrideRequest = Body(),
    user: User = Security(ensure_user),
) -> Dict[str, Any]:
    return ctx.terms.set_override(user.id, novel_id, handle, _language(language), body.rendering)
