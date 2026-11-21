from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PoolUpdateRequest(BaseModel):
    """Admin edit of the key pool. Every field is optional: only what is sent changes."""

    api_keys: Optional[str] = Field(
        default=None,
        description="Keys, one per line: `AIza...`, `LABEL=AIza...` or `AIza...,project`",
    )
    pool_models: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Model blocks: model, keys, rpm, tpm, rpd",
    )
    pool_tasks: Optional[Dict[str, List[str]]] = Field(
        default=None, description="Task chains: translate, extract"
    )
    auto_pause_on_daily: Optional[bool] = Field(
        default=None, description="Pause instead of failing when every key is spent"
    )
    reset_tz: Optional[str] = Field(default=None, description="Timezone of the daily reset")
    max_request_tokens: Optional[int] = Field(
        default=None, description="Upper bound on source tokens per request"
    )
    web_engine_order: Optional[List[str]] = Field(
        default=None, description="Keyless engines for Web/Web+: google, bing"
    )
    enabled: Optional[bool] = Field(
        default=None, description="Whether translation work may be started at all"
    )


class PoolChainRequest(BaseModel):
    task: str = Field(description="translate or extract")
    model: Optional[str] = Field(
        default=None, description="Default model for that task; empty puts it back on translate"
    )


class TermUpdateRequest(BaseModel):
    rendering: Optional[str] = Field(default=None, description="Admin decision; empty clears it")
    category: Optional[str] = Field(default=None, description="Term category; empty clears it")
    verdict: Optional[str] = Field(
        default=None, description="Review verdict: keep, skip, fragment or merge; empty clears it"
    )


class TermBlockRequest(BaseModel):
    reason: Optional[str] = Field(default=None, description="Why the surface was rejected")


class TermMergeRequest(BaseModel):
    into: Optional[str] = Field(
        default=None, description="Canonical term's source or handle; empty unlinks it"
    )


class OverrideRequest(BaseModel):
    rendering: Optional[str] = Field(
        default=None, description="This reader's rendering; empty removes the override"
    )


class GlossaryTranslateRequest(BaseModel):
    limit: int = Field(default=40, ge=1, le=200, description="How many terms to ask about")


class StyleRequest(BaseModel):
    """Per-novel source language and rendering style; only what is sent changes."""

    language: Optional[str] = Field(
        default=None, description="Source language override, e.g. zh; empty clears it"
    )
    honorifics: Optional[str] = Field(
        default=None, description="strip or keep a character's honorific"
    )
    name_order: Optional[str] = Field(default=None, description="family-given or given-family")
    categories: Optional[List[str]] = Field(
        default=None, description="Enabled categories; empty means every category"
    )
