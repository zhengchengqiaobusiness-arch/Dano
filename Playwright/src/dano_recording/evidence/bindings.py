"""Deterministically correlate controls, wire fields, and option endpoints."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from dano_recording.domain.enums import ChoiceEvidenceSource, ChoiceOption, EvidenceCompleteness
from dano_recording.domain.fields import FieldFact
from dano_recording.domain.operations import CompiledRequest, RequestDisposition
from dano_recording.evidence.dom_controls import DOMControl
from dano_recording.evidence.enum_extractor import EnumCandidate
from dano_recording.evidence.provenance import EvidenceBinding
from dano_recording.evidence.runtime_components import RuntimeComponentClue


def _wire_key(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]", r".\1", str(value))
    return ".".join(part for part in value.strip(".").casefold().split(".") if part)


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _option_rows(value: Any) -> tuple[ChoiceOption, ...]:
    if isinstance(value, dict):
        for key in ("options", "items", "choices", "records", "list", "data"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if not isinstance(value, list):
        return ()
    output: list[ChoiceOption] = []
    for index, row in enumerate(value):
        if isinstance(row, dict):
            label = next((row[key] for key in ("label", "name", "title", "text") if key in row), None)
            wire = next((row[key] for key in ("value", "id", "code", "key") if key in row), None)
            if label is None or wire is None:
                return ()
            output.append(ChoiceOption(label=str(label), value=wire, disabled=bool(row.get("disabled"))))
        elif isinstance(row, (str, int, float, bool)):
            output.append(ChoiceOption(label=str(row), value=row))
        else:
            return ()
        if index >= 999:
            break
    return tuple(output)


@dataclass(frozen=True, slots=True)
class CorrelatedEvidence:
    bindings: tuple[EvidenceBinding, ...]
    candidates: tuple[EnumCandidate, ...]
    symbol_bindings: dict[str, EvidenceBinding]


def correlate_recording_evidence(
    *,
    controls: Iterable[DOMControl],
    runtime_clues: Iterable[RuntimeComponentClue],
    requests: Iterable[CompiledRequest],
    fields: Iterable[FieldFact],
) -> CorrelatedEvidence:
    """Create strong bindings only from exact, unique wire/control evidence."""

    controls = tuple(controls)
    requests = tuple(requests)
    fields = tuple(fields)
    fields_by_key: dict[str, list[FieldFact]] = defaultdict(list)
    for field in fields:
        for key in {_wire_key(field.wire_path), _wire_key(field.wire_name)}:
            if key:
                fields_by_key[key].append(field)

    bindings: list[EvidenceBinding] = []
    by_control: dict[str, list[EvidenceBinding]] = defaultdict(list)
    candidates: list[EnumCandidate] = []
    for control in controls:
        matches = fields_by_key.get(_wire_key(control.name), []) if control.name else []
        # A DOM name must identify exactly one wire field. Ambiguity stays weak.
        if len(matches) != 1:
            if control.options:
                candidates.append(EnumCandidate(
                    source_kind=ChoiceEvidenceSource.NATIVE_SELECT if control.tag == "select" else ChoiceEvidenceSource.DOM_OVERLAY,
                    options=control.options,
                    control_id=control.control_id,
                    completeness=EvidenceCompleteness.COMPLETE if control.tag == "select" and not control.options_truncated else EvidenceCompleteness.PARTIAL,
                    proofs=(f"unbound DOM control {control.control_id}",),
                ))
            continue
        field = matches[0]
        binding = EvidenceBinding(
            field_contract_id=field.field_contract_id,
            control_id=control.control_id,
            request_id=field.request_id,
            wire_path=field.wire_path,
        )
        bindings.append(binding)
        by_control[control.control_id].append(binding)
        candidates.append(EnumCandidate(
            source_kind=ChoiceEvidenceSource.NATIVE_SELECT if control.tag == "select" else ChoiceEvidenceSource.DOM_OVERLAY,
            options=control.options,
            field_contract_id=binding.field_contract_id,
            control_id=binding.control_id,
            request_id=binding.request_id,
            wire_path=binding.wire_path,
            completeness=EvidenceCompleteness.COMPLETE if control.tag == "select" and not control.options_truncated else EvidenceCompleteness.PARTIAL,
            proofs=(f"exact DOM name/wire binding {control.name}",),
        ))
        observed = {_stable(value) for value in field.observed_values}
        selected = tuple(option for option in control.options if _stable(option.value) in observed)
        if selected:
            candidates.append(EnumCandidate(
                source_kind=ChoiceEvidenceSource.WIRE_SELECTION,
                options=selected,
                field_contract_id=binding.field_contract_id,
                control_id=binding.control_id,
                request_id=binding.request_id,
                wire_path=binding.wire_path,
                completeness=EvidenceCompleteness.PARTIAL,
                proofs=("captured wire value equals DOM option value",),
            ))

    symbol_bindings: dict[str, EvidenceBinding] = {}
    for clue in runtime_clues:
        linked = by_control.get(str(clue.control_id or ""), [])
        binding = linked[0] if len(linked) == 1 else None
        candidates.append(EnumCandidate(
            source_kind=ChoiceEvidenceSource.RUNTIME_COMPONENT,
            options=clue.options,
            field_contract_id=binding.field_contract_id if binding else None,
            control_id=binding.control_id if binding else clue.control_id,
            request_id=binding.request_id if binding else None,
            wire_path=binding.wire_path if binding else None,
            symbol_path=clue.property_path,
            completeness=EvidenceCompleteness.PARTIAL,
            proofs=clue.proofs,
        ))
        if binding and clue.property_path:
            symbol_bindings[clue.property_path] = binding

    # An option endpoint becomes strong only when its complete typed option set
    # exactly equals one uniquely bound control's option set.
    option_requests = [request for request in requests if request.disposition is RequestDisposition.OPTION_SOURCE]
    for request in option_requests:
        options = _option_rows(request.response_body)
        if not options:
            continue
        key = {_stable((option.label, option.value, option.disabled)) for option in options}
        matching_controls = [
            control for control in controls
            if control.options and key == {_stable((item.label, item.value, item.disabled)) for item in control.options}
        ]
        if len(matching_controls) == 1 and len(by_control.get(matching_controls[0].control_id, [])) == 1:
            binding = by_control[matching_controls[0].control_id][0]
            candidates.append(EnumCandidate(
                source_kind=ChoiceEvidenceSource.OPTION_ENDPOINT,
                options=options,
                field_contract_id=binding.field_contract_id,
                control_id=binding.control_id,
                request_id=binding.request_id,
                wire_path=binding.wire_path,
                completeness=EvidenceCompleteness.COMPLETE,
                proofs=(f"option endpoint {request.request_id} exactly matches bound control options",),
            ))
        else:
            candidates.append(EnumCandidate(
                source_kind=ChoiceEvidenceSource.OPTION_ENDPOINT,
                options=options,
                request_id=request.request_id,
                completeness=EvidenceCompleteness.UNKNOWN,
                confidence=0.25,
                proofs=("option endpoint lacks a unique field binding",),
            ))

    return CorrelatedEvidence(tuple(bindings), tuple(candidates), symbol_bindings)


__all__ = ["CorrelatedEvidence", "correlate_recording_evidence"]
