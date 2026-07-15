"""Immutable facts emitted by browser capture."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, field_validator, model_validator

from dano_recording.domain._base import FrozenModel, freeze_json, new_id, utc_now


class FactKind(StrEnum):
    ACTION = "action"
    PAGE = "page"
    DOM_CONTROL = "dom_control"
    DOM_MUTATION = "dom_mutation"
    REQUEST = "request"
    RESPONSE = "response"
    REQUEST_FAILED = "request_failed"
    SCRIPT = "script"
    DIAGNOSTIC = "diagnostic"


class RecordingFact(FrozenModel):
    """Append-only observed fact.

    ``frozen=True`` prevents accidental updates in the application.  The
    database migration additionally rejects UPDATE/DELETE for stored facts.
    """

    fact_id: str = Field(default_factory=new_id)
    tenant: str = Field(min_length=1)
    recording_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    kind: FactKind
    observed_at: datetime = Field(default_factory=utc_now)
    action_id: str | None = None
    page_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    redacted: bool = True

    @field_validator("tenant", "recording_id", "fact_id")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("identifier must not be blank")
        return value

    @model_validator(mode="after")
    def _freeze_payload(self) -> "RecordingFact":
        object.__setattr__(self, "payload", freeze_json(self.payload))
        return self


class RequestFact(RecordingFact):
    kind: FactKind = FactKind.REQUEST
    request_id: str = Field(default_factory=new_id)
    method: str
    url: str
    resource_type: str = "fetch"
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body: Any | None = None
    request_body_present: bool = False
    response_status: int | None = None
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_body: Any | None = None
    failed_reason: str | None = None

    @field_validator("method")
    @classmethod
    def _method_upper(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("method must not be blank")
        return value

    @model_validator(mode="after")
    def _infer_body_presence(self) -> "RequestFact":
        if self.request_body is not None and not self.request_body_present:
            object.__setattr__(self, "request_body_present", True)
        object.__setattr__(self, "request_headers", freeze_json(self.request_headers))
        object.__setattr__(self, "response_headers", freeze_json(self.response_headers))
        object.__setattr__(self, "request_body", freeze_json(self.request_body))
        object.__setattr__(self, "response_body", freeze_json(self.response_body))
        return self

    @property
    def path(self) -> str:
        return urlsplit(self.url).path or "/"

    @property
    def query_items(self) -> tuple[tuple[str, str], ...]:
        return tuple(parse_qsl(urlsplit(self.url).query, keep_blank_values=True))


class ActionFact(RecordingFact):
    kind: FactKind = FactKind.ACTION
    action_id: str
    action_type: str
    label: str = ""
    locator: str | None = None


class ActionTransaction(FrozenModel):
    transaction_id: str
    tenant: str
    recording_id: str
    action_id: str | None = None
    action_label: str = ""
    request_ids: tuple[str, ...]
    first_sequence: int
    last_sequence: int
