"""Strict evidence bindings and Pi-safe evidence projections."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any

from dano_recording.domain.enums import ChoiceEvidence, ChoiceEvidenceSource, ChoiceOption


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    """A proven control-to-wire association.

    Strong enum evidence is emitted only through a complete binding.  This is
    what prevents a plausible array from one field leaking into another field.
    """

    field_contract_id: str
    control_id: str
    request_id: str
    wire_path: str

    def __post_init__(self) -> None:
        for name in ("field_contract_id", "control_id", "request_id", "wire_path"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required for evidence binding")


@dataclass(frozen=True, slots=True)
class EnumSuggestion:
    """Unbound static/runtime candidate; never treated as a field contract."""

    suggestion_id: str
    source_kind: ChoiceEvidenceSource
    options: tuple[ChoiceOption, ...]
    confidence: float
    reason: str
    field_contract_id: str | None = None
    control_id: str | None = None
    request_id: str | None = None
    wire_path: str | None = None
    script_url: str | None = None
    script_hash: str | None = None
    symbol_path: str | None = None
    proofs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 0.30:
            raise ValueError("unbound suggestions must remain low confidence (<= 0.30)")


class EvidenceRegistry:
    """Append-only evidence registry with no cross-field aggregation."""

    def __init__(self) -> None:
        self._evidence: list[ChoiceEvidence] = []
        self._suggestions: list[EnumSuggestion] = []
        self._ids: set[str] = set()

    def add_evidence(self, evidence: ChoiceEvidence) -> None:
        if not all((evidence.control_id, evidence.request_id, evidence.wire_path)):
            raise ValueError("choice evidence requires control_id, request_id, and wire_path")
        if evidence.evidence_id in self._ids:
            return
        self._ids.add(evidence.evidence_id)
        self._evidence.append(evidence.model_copy(deep=True))

    def add_suggestion(self, suggestion: EnumSuggestion) -> None:
        if suggestion.suggestion_id in self._ids:
            return
        self._ids.add(suggestion.suggestion_id)
        self._suggestions.append(suggestion)

    def for_field(self, field_contract_id: str) -> tuple[ChoiceEvidence, ...]:
        return tuple(
            item.model_copy(deep=True)
            for item in self._evidence
            if item.field_contract_id == field_contract_id
        )

    def suggestions(self) -> tuple[EnumSuggestion, ...]:
        return tuple(self._suggestions)

    def all_evidence(self) -> tuple[ChoiceEvidence, ...]:
        return tuple(item.model_copy(deep=True) for item in self._evidence)


_RAW_CODE_KEYS = frozenset(
    {
        "source",
        "source_text",
        "script_source",
        "raw_js",
        "raw_source",
        "source_content",
        "source_contents",
        "sourcescontent",
        "source_reference",
    }
)


def project_evidence_for_pi(value: Any) -> Any:
    """Recursively project metadata while making raw JavaScript unrepresentable."""

    if isinstance(value, ChoiceEvidence):
        return {
            "evidence_id": value.evidence_id,
            "field_contract_id": value.field_contract_id,
            "control_id": value.control_id,
            "request_id": value.request_id,
            "wire_path": value.wire_path,
            "source_kind": value.source_kind.value,
            "options": [option.model_dump(mode="json") for option in value.options],
            "script_url": value.script_url,
            "script_hash": value.script_hash,
            "symbol_path": value.symbol_path,
            "completeness": value.completeness.value,
            "confidence": value.confidence,
            "proofs": list(value.proofs),
        }
    if isinstance(value, Enum):
        return value.value
    projector = getattr(value, "pi_projection", None)
    if callable(projector):
        return project_evidence_for_pi(projector())
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            normalised = str(key).replace("_", "").lower()
            if normalised in {item.replace("_", "") for item in _RAW_CODE_KEYS}:
                continue
            output[str(key)] = project_evidence_for_pi(item)
        return output
    if isinstance(value, (list, tuple, set, frozenset)):
        return [project_evidence_for_pi(item) for item in value]
    if isinstance(value, bytes):
        return {"omitted": "binary", "size": len(value)}
    return value
