"""Explicit capability relationship contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from dano_recording.domain._base import FrozenModel


class RelationType(StrEnum):
    DATA_FLOW = "data_flow"
    CALLER_SELECTION = "caller_selection"
    PRECONDITION = "precondition"
    CALLER_DECISION = "caller_decision"
    EXTERNAL_TRANSFORM = "external_transform"


class CapabilityRelation(FrozenModel):
    relation_id: str
    relation_type: RelationType
    from_capability_id: str
    to_capability_id: str
    from_request_id: str | None = None
    to_request_id: str | None = None
    from_path: str | None = None
    to_path: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: tuple[str, ...] = ()
    confirmed: bool = False
