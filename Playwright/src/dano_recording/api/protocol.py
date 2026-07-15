"""Typed public protocol for recording-v3 session negotiation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


RecordingMode = Literal["record_only", "real_submit"]


class CreateRecordingRequest(BaseModel):
    subsystem: str = Field(min_length=1, max_length=120)
    start_url: str = Field(min_length=1, max_length=8_192)
    base_url: str = Field(default="", max_length=8_192)
    recording_mode: RecordingMode = "record_only"

    model_config = ConfigDict(extra="forbid")

    @field_validator("subsystem", "start_url", "base_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class ResumeRecordingRequest(BaseModel):
    resume_token: str = Field(min_length=16, max_length=1_024)

    model_config = ConfigDict(extra="forbid")


class SessionConnectionResponse(BaseModel):
    recording_id: str
    websocket_ticket: str
    ticket_expires_at: str
    current_revision: int = Field(ge=0)
    resume_token: str | None = None
    snapshot: dict[str, Any] | None = None
    pi_status: dict[str, Any] = Field(default_factory=dict)


class RecordingError(BaseModel):
    type: Literal["error"] = "error"
    detail: str
    code: str = "recording_error"
    retryable: bool = False
    operation: str | None = None
    operation_id: str | None = None
    expected_revision: int | None = None
    actual_revision: int | None = None
    full_spec: dict[str, Any] | None = None
    check_report: dict[str, Any] | None = None


__all__ = [
    "CreateRecordingRequest",
    "RecordingError",
    "RecordingMode",
    "ResumeRecordingRequest",
    "SessionConnectionResponse",
]
