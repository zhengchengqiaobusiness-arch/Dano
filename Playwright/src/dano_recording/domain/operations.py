"""Compiled requests and idempotent command metadata."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from dano_recording.domain._base import FrozenModel, utc_now


class RequestDisposition(StrEnum):
    MATERIALIZED = "materialized"
    SUPPORTING = "supporting"
    OPTION_SOURCE = "option_source"
    IDENTITY = "identity"
    PREFLIGHT = "preflight"
    REVIEW_CANDIDATE = "review_candidate"
    UNSUPPORTED = "unsupported"
    IGNORED_RESOURCE = "ignored_resource"


class RequestAnalysis(FrozenModel):
    request_id: str
    disposition: RequestDisposition
    reason: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class CompiledRequest(FrozenModel):
    """Lossless compiler ledger row for one captured request."""

    tenant: str
    recording_id: str
    request_id: str
    transaction_id: str
    sequence: int
    method: str
    url: str
    path: str
    query: tuple[tuple[str, str], ...] = ()
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any | None = None
    body_present: bool = False
    response_status: int | None = None
    response_body: Any | None = None
    response_schema: dict[str, Any] | None = None
    disposition: RequestDisposition
    disposition_reason: str
    capability_eligible: bool = False


class OperationStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class RecordingOperation(FrozenModel):
    tenant: str
    operation_id: str
    recording_id: str
    kind: str
    request_hash: str
    status: OperationStatus = OperationStatus.STARTED
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
