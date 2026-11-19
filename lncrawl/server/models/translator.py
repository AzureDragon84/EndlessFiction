"""Shapes for the admin-side translator key pool: one probe per stored key."""

from typing import List, Optional

from pydantic import BaseModel, Field


class GeminiKeyStatus(BaseModel):
    provider: str = Field(description="Pool provider id, e.g. 'gemini-pool-001'")
    fingerprint: str = Field(description="Masked key, enough to tell two keys apart")
    alive: Optional[bool] = Field(
        default=None,
        description="True when the key works, False when the provider rejected it, "
        "None when the probe itself failed",
    )
    status: Optional[int] = Field(default=None, description="HTTP status of the probe")
    error: str = Field(default="", description="Provider message or transport error")


class GeminiKeyPruneResult(BaseModel):
    checked: int = Field(description="Keys probed")
    removed: List[str] = Field(default_factory=list, description="Pool ids dropped as dead")
    remaining: int = Field(default=0, description="Keys left in the pool")
