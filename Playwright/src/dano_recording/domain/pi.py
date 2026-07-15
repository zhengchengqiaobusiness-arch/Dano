"""Pi session/event metadata persisted by the recording coordinator."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from dano_recording.domain._base import FrozenModel, new_id, utc_now


class PiRole(StrEnum):
    PLANNER = "planner"
    ACCEPTANCE = "acceptance"
    SECURITY = "security"
    COMPLIANCE = "compliance"


class PiSessionStatus(StrEnum):
    OPEN = "open"
    RUNNING = "running"
    IDLE = "idle"
    FAILED = "failed"
    CLOSED = "closed"


class PiUsage(FrozenModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)


class PiSessionMetadata(FrozenModel):
    tenant: str
    recording_id: str
    pi_session_id: str
    role: PiRole
    model_id: str
    status: PiSessionStatus = PiSessionStatus.OPEN
    last_revision: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PiEvent(FrozenModel):
    event_id: str = Field(default_factory=new_id)
    tenant: str
    recording_id: str
    pi_session_id: str
    event_type: str
    turn_index: int = Field(default=0, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    usage: PiUsage | None = None
    occurred_at: datetime = Field(default_factory=utc_now)
