"""Field-safe enum evidence binding and low-confidence suggestions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dano_recording.domain.enums import (
    ChoiceEvidence,
    ChoiceEvidenceSource,
    ChoiceOption,
    EvidenceCompleteness,
)
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.evidence.provenance import EnumSuggestion, EvidenceBinding
from dano_recording.evidence.option_safety import is_identity_option_collection

if TYPE_CHECKING:
    from dano_recording.evidence.dom_controls import DOMControl
    from dano_recording.evidence.js_ast_worker import JSAnalysisResult
    from dano_recording.evidence.runtime_components import RuntimeComponentClue


_SOURCE_CONFIDENCE = {
    ChoiceEvidenceSource.WIRE_SELECTION: 1.0,
    ChoiceEvidenceSource.OPTION_ENDPOINT: 0.95,
    ChoiceEvidenceSource.RUNTIME_COMPONENT: 0.85,
    ChoiceEvidenceSource.NATIVE_SELECT: 0.82,
    ChoiceEvidenceSource.DOM_OVERLAY: 0.70,
    ChoiceEvidenceSource.SOURCEMAP: 0.58,
    ChoiceEvidenceSource.SCRIPT_STATIC: 0.40,
    ChoiceEvidenceSource.PI_SUGGESTION: 0.20,
}


def _stable_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


@dataclass(frozen=True, slots=True)
class EnumCandidate:
    source_kind: ChoiceEvidenceSource
    options: tuple[ChoiceOption, ...]
    field_contract_id: str | None = None
    control_id: str | None = None
    request_id: str | None = None
    wire_path: str | None = None
    script_url: str | None = None
    script_hash: str | None = None
    symbol_path: str | None = None
    completeness: EvidenceCompleteness = EvidenceCompleteness.UNKNOWN
    confidence: float | None = None
    proofs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EnumExtractionResult:
    evidence: tuple[ChoiceEvidence, ...]
    suggestions: tuple[EnumSuggestion, ...]


class EnumExtractor:
    """Binds candidates only through explicit control/request/wire provenance."""

    def __init__(self, *, redaction: RedactionPolicy | None = None) -> None:
        self.redaction = redaction or RedactionPolicy()

    @staticmethod
    def from_dom_control(
        control: "DOMControl",
        *,
        field_contract_id: str | None = None,
        request_id: str | None = None,
        wire_path: str | None = None,
    ) -> EnumCandidate:
        source = (
            ChoiceEvidenceSource.NATIVE_SELECT
            if control.option_source == "native_select" or control.tag == "select"
            else ChoiceEvidenceSource.DOM_OVERLAY
        )
        return EnumCandidate(
            source_kind=source,
            options=control.options,
            field_contract_id=field_contract_id,
            control_id=control.control_id,
            request_id=request_id,
            wire_path=wire_path,
            completeness=(
                EvidenceCompleteness.COMPLETE
                if control.tag == "select" and not control.options_truncated
                else EvidenceCompleteness.PARTIAL
            ),
            proofs=(f"DOM control {control.control_id}",),
        )

    @staticmethod
    def from_runtime_component(
        clue: "RuntimeComponentClue",
        *,
        field_contract_id: str | None = None,
        request_id: str | None = None,
        wire_path: str | None = None,
    ) -> EnumCandidate:
        return EnumCandidate(
            source_kind=ChoiceEvidenceSource.RUNTIME_COMPONENT,
            options=clue.options,
            field_contract_id=field_contract_id,
            control_id=clue.control_id,
            request_id=request_id,
            wire_path=wire_path,
            symbol_path=clue.property_path,
            completeness=EvidenceCompleteness.PARTIAL,
            proofs=clue.proofs,
        )

    @staticmethod
    def from_static_analysis(
        result: "JSAnalysisResult",
        *,
        source_kind: ChoiceEvidenceSource = ChoiceEvidenceSource.SCRIPT_STATIC,
        binding: EvidenceBinding | None = None,
        symbol_bindings: dict[str, EvidenceBinding] | None = None,
    ) -> tuple[EnumCandidate, ...]:
        if source_kind not in {
            ChoiceEvidenceSource.SCRIPT_STATIC,
            ChoiceEvidenceSource.SOURCEMAP,
        }:
            raise ValueError("static analysis source must be script_static or sourcemap")
        candidates = result.candidates
        # A script-level binding is safe only when analysis produced one
        # candidate.  With multiple arrays, each symbol needs its own proven
        # binding; otherwise every array remains an unbound suggestion.
        single_binding = binding if len(candidates) == 1 else None
        bindings_by_symbol = symbol_bindings or {}
        output: list[EnumCandidate] = []
        for item in candidates:
            item_binding = bindings_by_symbol.get(item.symbol_path) or single_binding
            output.append(EnumCandidate(
                source_kind=source_kind,
                options=item.options,
                field_contract_id=item_binding.field_contract_id if item_binding else None,
                control_id=item_binding.control_id if item_binding else None,
                request_id=item_binding.request_id if item_binding else None,
                wire_path=item_binding.wire_path if item_binding else None,
                script_url=result.script_url,
                script_hash=result.script_hash,
                symbol_path=item.symbol_path,
                completeness=item.completeness,
                proofs=item.proofs,
            ))
        return tuple(output)

    def resolve(
        self,
        candidates: tuple[EnumCandidate, ...] | list[EnumCandidate],
        bindings: tuple[EvidenceBinding, ...] | list[EvidenceBinding],
    ) -> EnumExtractionResult:
        evidence: list[ChoiceEvidence] = []
        suggestions: list[EnumSuggestion] = []
        for candidate in candidates:
            if self._is_sensitive_candidate(candidate):
                continue
            options = self._deduplicate(self._sanitise_options(candidate.options))
            if not options:
                continue
            matched = self._matching_bindings(candidate, bindings)
            if len(matched) != 1:
                suggestions.append(
                    self._suggestion(
                        candidate,
                        options,
                        reason="unbound" if not matched else "ambiguous_binding",
                    )
                )
                continue
            binding = matched[0]
            confidence = candidate.confidence
            if confidence is None:
                confidence = _SOURCE_CONFIDENCE[candidate.source_kind]
            confidence = max(0.0, min(float(confidence), _SOURCE_CONFIDENCE[candidate.source_kind]))
            evidence_id = self._identifier("evidence", candidate, options, binding)
            evidence.append(
                ChoiceEvidence(
                    evidence_id=evidence_id,
                    field_contract_id=binding.field_contract_id,
                    source_kind=candidate.source_kind,
                    options=options,
                    control_id=binding.control_id,
                    request_id=binding.request_id,
                    wire_path=binding.wire_path,
                    script_url=candidate.script_url,
                    script_hash=candidate.script_hash,
                    symbol_path=candidate.symbol_path,
                    completeness=candidate.completeness,
                    confidence=confidence,
                    proofs=candidate.proofs,
                )
            )
        return EnumExtractionResult(tuple(evidence), tuple(suggestions))

    def _is_sensitive_candidate(self, candidate: EnumCandidate) -> bool:
        context = " ".join(
            str(value or "")
            for value in (
                candidate.field_contract_id,
                candidate.control_id,
                candidate.wire_path,
                candidate.symbol_path,
            )
        )
        if is_identity_option_collection(
            context=context,
            options=(
                {"label": option.label, "value": option.value}
                for option in candidate.options
            ),
        ):
            return True
        for value in (
            candidate.field_contract_id,
            candidate.control_id,
            candidate.wire_path,
            candidate.symbol_path,
        ):
            for part in re.split(r"[^A-Za-z0-9_-]+", value or ""):
                if part and self.redaction.is_sensitive_key(part):
                    return True
        return False

    def _sanitise_options(self, options: tuple[ChoiceOption, ...]) -> tuple[ChoiceOption, ...]:
        return tuple(
            ChoiceOption(
                label=self.redaction.redact_text(option.label),
                value=self.redaction.redact_value(option.value),
                disabled=option.disabled,
            )
            for option in options
        )

    @staticmethod
    def _matching_bindings(
        candidate: EnumCandidate,
        bindings: tuple[EvidenceBinding, ...] | list[EvidenceBinding],
    ) -> list[EvidenceBinding]:
        # An array found merely by name in a bundle has no binding anchor.  It
        # stays a suggestion even when only one field happens to exist.
        anchors = {
            "field_contract_id": candidate.field_contract_id,
            "control_id": candidate.control_id,
            "request_id": candidate.request_id,
            "wire_path": candidate.wire_path,
        }
        if not any(value for value in anchors.values()):
            return []
        matched = []
        for binding in bindings:
            if all(not value or getattr(binding, name) == value for name, value in anchors.items()):
                matched.append(binding)
        return matched

    def _suggestion(
        self,
        candidate: EnumCandidate,
        options: tuple[ChoiceOption, ...],
        *,
        reason: str,
    ) -> EnumSuggestion:
        proposed = candidate.confidence if candidate.confidence is not None else 0.20
        confidence = max(0.0, min(float(proposed), 0.25))
        return EnumSuggestion(
            suggestion_id=self._identifier("suggestion", candidate, options, None),
            source_kind=candidate.source_kind,
            options=options,
            confidence=confidence,
            reason=reason,
            field_contract_id=candidate.field_contract_id,
            control_id=candidate.control_id,
            request_id=candidate.request_id,
            wire_path=candidate.wire_path,
            script_url=candidate.script_url,
            script_hash=candidate.script_hash,
            symbol_path=candidate.symbol_path,
            proofs=candidate.proofs,
        )

    @staticmethod
    def _deduplicate(options: tuple[ChoiceOption, ...]) -> tuple[ChoiceOption, ...]:
        seen: set[tuple[str, str, bool]] = set()
        output: list[ChoiceOption] = []
        for option in options:
            key = (option.label, _stable_value(option.value), option.disabled)
            if key in seen:
                continue
            seen.add(key)
            output.append(option.model_copy(deep=True))
        return tuple(output)

    @staticmethod
    def _identifier(
        prefix: str,
        candidate: EnumCandidate,
        options: tuple[ChoiceOption, ...],
        binding: EvidenceBinding | None,
    ) -> str:
        payload = {
            "source": candidate.source_kind.value,
            "field": binding.field_contract_id if binding else candidate.field_contract_id,
            "control": binding.control_id if binding else candidate.control_id,
            "request": binding.request_id if binding else candidate.request_id,
            "wire": binding.wire_path if binding else candidate.wire_path,
            "script_hash": candidate.script_hash,
            "symbol": candidate.symbol_path,
            "options": [option.model_dump(mode="json") for option in options],
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:24]
        return f"{prefix}_{digest}"
