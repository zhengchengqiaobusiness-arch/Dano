"""Versioned recording snapshots and release artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from dano_recording.domain._base import FrozenModel, new_id, utc_now


class RecordingRevision(FrozenModel):
    tenant: str
    recording_id: str
    revision: int = Field(ge=1)
    parent_revision: int = Field(ge=0)
    content_hash: str
    snapshot: dict[str, Any]
    actor: str
    created_at: datetime = Field(default_factory=utc_now)


class RecordingArtifact(FrozenModel):
    artifact_id: str = Field(default_factory=new_id)
    tenant: str
    recording_id: str
    revision: int = Field(ge=0)
    kind: str
    content_hash: str
    storage_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
