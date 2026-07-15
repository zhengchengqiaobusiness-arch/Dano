from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReleaseCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    recording_id: str
    tenant: str
    subsystem: str
    action: str
    revision: int
    content_hash: str
    body: dict[str, Any]
    validation: dict[str, Any] = Field(default_factory=dict)
