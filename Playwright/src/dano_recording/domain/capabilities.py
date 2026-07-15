"""Public capability contracts derived from action transactions."""

from __future__ import annotations

from enum import StrEnum

from dano_recording.domain._base import FrozenModel


class CapabilityRisk(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class Capability(FrozenModel):
    capability_id: str
    transaction_id: str
    name: str
    title: str
    # Stable executable vocabulary (query/submit/approve/...).  ``name`` is
    # retained as the workbench/business alias for visual compatibility.
    operation: str | None = None
    request_ids: tuple[str, ...]
    field_contract_ids: tuple[str, ...] = ()
    risk_level: CapabilityRisk = CapabilityRisk.L1
    execution_enabled: bool = True
    explicit_confirmation: bool = False
    provisional: bool = True
    origin: str = "deterministic"
