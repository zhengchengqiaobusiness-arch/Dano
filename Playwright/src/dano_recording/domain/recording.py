"""Recording session aggregates."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from dano_recording.domain._base import DomainModel, new_id, utc_now


class RecordingStatus(StrEnum):
    CREATED = "created"
    RECORDING = "recording"
    COMPILING = "compiling"
    DRAFT = "draft"
    REVIEWING = "reviewing"
    PUBLISHED = "published"
    FAILED = "failed"
    CLOSED = "closed"


class RecordingSession(DomainModel):
    tenant: str = Field(min_length=1)
    recording_id: str = Field(default_factory=new_id)
    status: RecordingStatus = RecordingStatus.CREATED
    base_url: str = ""
    current_revision: int = Field(default=0, ge=0)
    browser_lease_until: datetime | None = None
    resume_token_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
