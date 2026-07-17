"""Evidence-grounded, revision-bound semantic operations for Recording Pi.

Pi never receives an object-replacement primitive.  The only write boundary is a
batch of operations from ``ALLOWED_OPERATIONS``.  A batch is applied to a deep
copy and returned only after every target, evidence reference and schema path is
validated, which gives the caller all-or-nothing semantics.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Iterable
from uuid import UUID, uuid4

from dano_recording.domain.fields import (
    AxisDecision,
    AxisOrigin,
    FieldDimension,
    SourceBinding,
    SourceBindingKind,
)
from dano_recording.field_registry import (
    BindingDirection,
    BindingRole,
    FieldRegistry,
    FieldWireBinding,
    sync_snapshot_axis_decision,
)


class PiSemanticOperationError(ValueError):
    pass


def _canonical_uuid(value: Any, *, label: str) -> str:
    try:
        return str(UUID(str(value or "")))
    except (TypeError, ValueError) as exc:
        raise PiSemanticOperationError(f"{label} must be a valid UUID") from exc


ALLOWED_OPERATIONS = frozenset({
    "set_field_axis",
    "link_field_binding",
    "unlink_field_binding",
    "create_capability",
    "delete_capability",
    "merge_capabilities",
    "split_capability",
    "move_request_to_capability",
    "set_input_schema",
    "set_output_schema",
    "set_flow_goal",
    "set_flow_action",
    "set_business_description",
    "set_step_name",
    "set_step_title",
    "set_capability_name",
    "set_capability_title",
    "set_capability_description",
})

FIELD_AXES = frozenset({
    "display_name",
    "business_type",
    "classification",
    "source_binding",
    "default_value",
    "caller_required",
    "wire_required",
    "required_conditions",
    "exposure",
    "enum_binding",
})

_DANGEROUS = {"POST", "PUT", "PATCH", "DELETE"}
_RISK_ORDER = {f"L{value}": value for value in range(1, 6)}
_EVIDENCE_KEY = re.compile(
    r"(?:evidence|observation|request|control|action|transaction|capture|binding|fact).*ids?$",
    re.I,
)
_STABLE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z")


def _identity(value: dict[str, Any], names: Iterable[str]) -> str:
    return next((str(value.get(name)) for name in names if value.get(name)), "")


def _field_id(value: dict[str, Any]) -> str:
    return _identity(value, ("field_uuid", "field_contract_id", "field_id"))


def _step_id(value: dict[str, Any]) -> str:
    return _identity(value, ("step_uuid", "step_id"))


def _capability_id(value: dict[str, Any]) -> str:
    return _identity(value, ("capability_uuid", "capability_id"))


def _request_id(value: dict[str, Any]) -> str:
    return _identity(value, ("request_definition_id", "request_id", "observation_id"))


def _collect_fields(spec: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}

    def add(raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        ref = _field_id(raw)
        if ref:
            values = result.setdefault(ref, [])
            if all(value is not raw for value in values):
                values.append(raw)

    for value in spec.get("effective_fields") or spec.get("fields") or []:
        add(value)
    for step in spec.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for value in step.get("params") or step.get("fields") or []:
            add(value)
    for capability in spec.get("capabilities") or []:
        if not isinstance(capability, dict):
            continue
        for key in ("fields", "inputs", "request_fields", "internal_fields", "computed_fields"):
            for value in capability.get(key) or []:
                add(value)
    return result


def _request_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    raw = spec.get("request_facts") or []
    if isinstance(raw, dict):
        raw = raw.get("requests") or []
    return [value for value in raw if isinstance(value, dict)] if isinstance(raw, list) else []


def _evidence_ids(spec: dict[str, Any]) -> set[str]:
    result: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                if _EVIDENCE_KEY.search(str(child_key)):
                    if isinstance(child, (str, int)) and str(child):
                        result.add(str(child))
                    elif isinstance(child, (list, tuple, set)):
                        result.update(str(item) for item in child if isinstance(item, (str, int)) and str(item))
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child, key)

    # Evidence is immutable and lives in these server-owned projections.  Do not
    # scan meta/user-editable prose, otherwise a model could mint its own proof.
    for source in (
        spec.get("request_facts"), spec.get("facts"), spec.get("evidence_graph"),
        spec.get("field_evidence"), spec.get("enum_evidence"), spec.get("transactions"),
    ):
        visit(source)
    for row in _request_rows(spec):
        for name in ("request_id", "request_definition_id", "observation_id", "action_id"):
            if row.get(name):
                result.add(str(row[name]))
    for step in spec.get("steps") or []:
        if isinstance(step, dict) and step.get("request_id"):
            result.add(str(step["request_id"]))
        if isinstance(step, dict):
            visit(step.get("source_meta") or {})
            for field in step.get("params") or []:
                if isinstance(field, dict):
                    visit({"evidence_ids": field.get("evidence_ids") or []})
    return result


def _require_evidence(operation: dict[str, Any], available: set[str]) -> tuple[str, ...]:
    values = operation.get("evidence_ids")
    if not isinstance(values, list) or not values:
        raise PiSemanticOperationError("every Pi semantic operation requires evidence_ids")
    evidence = tuple(dict.fromkeys(str(value) for value in values if str(value)))
    missing = [value for value in evidence if value not in available]
    if missing:
        raise PiSemanticOperationError(f"unknown evidence references: {missing}")
    return evidence


def _one(mapping: dict[str, list[dict[str, Any]]], ref: str, kind: str) -> list[dict[str, Any]]:
    values = mapping.get(ref) or []
    if not values:
        raise PiSemanticOperationError(f"{kind} target does not exist: {ref}")
    return values


def _manual_axis(spec: dict[str, Any], fields: list[dict[str, Any]], target: str, axis: str) -> bool:
    legacy_keys = {
        "display_name": {"display_name", "name", "label", "public_name"},
        "business_type": {"business_type", "type"},
        "classification": {"classification", "category"},
        "source_binding": {"source_binding", "value_provider", "source", "source_kind"},
        "default_value": {"default_value"},
        "caller_required": {"caller_required", "required"},
        "wire_required": {"wire_required", "required_by_wire"},
        "required_conditions": {"required_conditions", "required_contract"},
        "exposure": {"exposure", "exposed", "exposed_to_caller"},
        "enum_binding": {"enum_binding", "choice_contract", "enum_options"},
    }[axis]
    candidates = list(fields)
    registry = spec.get("field_registry") or {}
    if isinstance(registry, dict):
        for raw in registry.get("fields") or ():
            if isinstance(raw, dict) and _field_id(raw) == target:
                candidates.append(raw)
    for field in candidates:
        decisions = field.get("axis_decisions") or field.get("decisions") or {}
        decision = decisions.get(axis) if isinstance(decisions, dict) else None
        if isinstance(decision, dict) and (
            decision.get("manual_override") is True
            or str(decision.get("origin") or "").lower() in {"manual", "user"}
        ):
            return True
    pins = (spec.get("meta") or {}).get("decision_origins") or {}
    if not isinstance(pins, dict):
        return False
    for path, owner in pins.items():
        if str(owner).lower() not in {"manual", "user"}:
            continue
        parts = str(path).split(":")
        if len(parts) >= 4 and parts[0] == "field" and parts[-1] in legacy_keys:
            if target in parts or any(_field_id(field) == target for field in fields):
                return True
    return False


def _manual_capability_axes(
    spec: dict[str, Any],
    capability: dict[str, Any] | None,
    capability_id: str,
) -> set[str]:
    axes: set[str] = set()
    decisions = (capability or {}).get("semantic_decisions") or {}
    if isinstance(decisions, dict):
        for axis, raw in decisions.items():
            if isinstance(raw, dict) and (
                raw.get("manual_override") is True
                or str(raw.get("origin") or "").casefold() in {"manual", "user"}
            ):
                axes.add(str(axis))
    pins = (spec.get("meta") or {}).get("decision_origins") or {}
    if isinstance(pins, dict):
        for raw_path, owner in pins.items():
            if str(owner).casefold() not in {"manual", "user"}:
                continue
            parts = str(raw_path).split(":")
            if capability_id in parts and "capability" in parts:
                axes.add(parts[-1])
    return axes


def _reject_manual_capability(
    spec: dict[str, Any],
    capability: dict[str, Any] | None,
    capability_id: str,
    *axes: str,
    destructive: bool = False,
) -> None:
    manual = _manual_capability_axes(spec, capability, capability_id)
    if (destructive and manual) or manual.intersection(axes):
        raise PiSemanticOperationError(
            f"manual capability decision cannot be overwritten: "
            f"{capability_id}:{','.join(sorted(manual))}"
        )


def _semantic_decision(
    *, revision: int, evidence: tuple[str, ...], confidence: float,
) -> dict[str, Any]:
    return {
        "origin": "pi",
        "revision": revision,
        "confidence": confidence,
        "evidence_ids": list(evidence),
        "manual_override": False,
    }


def _flow_uuid(spec: dict[str, Any]) -> str:
    """Resolve the immutable lineage identity used by flow-level writes."""

    registry = spec.get("field_registry") or {}
    capture = spec.get("capture_store") or {}
    value = (
        spec.get("lineage_id")
        or (registry.get("lineage_id") if isinstance(registry, dict) else None)
        or (capture.get("lineage_id") if isinstance(capture, dict) else None)
    )
    if not value:
        raise PiSemanticOperationError("flow target requires a stable lineage_id")
    return _canonical_uuid(value, label="lineage_id")


def _manual_flow_axis(spec: dict[str, Any], axis: str) -> bool:
    decisions = (spec.get("meta") or {}).get("flow_semantic_decisions") or {}
    decision = decisions.get(axis) if isinstance(decisions, dict) else None
    if isinstance(decision, dict) and (
        decision.get("manual_override") is True
        or str(decision.get("origin") or "").casefold() in {"manual", "user"}
    ):
        return True
    pins = (spec.get("meta") or {}).get("decision_origins") or {}
    return isinstance(pins, dict) and str(pins.get(f"flow:{axis}") or "").casefold() in {
        "manual", "user",
    }


def _manual_step_axis(
    spec: dict[str, Any], step: dict[str, Any], step_uuid: str, axis: str,
) -> bool:
    decisions = step.get("semantic_decisions") or {}
    decision = decisions.get(axis) if isinstance(decisions, dict) else None
    if isinstance(decision, dict) and (
        decision.get("manual_override") is True
        or str(decision.get("origin") or "").casefold() in {"manual", "user"}
    ):
        return True
    pins = (spec.get("meta") or {}).get("decision_origins") or {}
    if not isinstance(pins, dict):
        return False
    identities = {step_uuid, str(step.get("step_id") or "")}
    return any(
        str(owner).casefold() in {"manual", "user"}
        and str(path).split(":")[-1] == axis
        and len(parts := str(path).split(":")) >= 3
        and parts[0] == "step"
        and parts[1] in identities
        for path, owner in pins.items()
    )


def _safe_goal(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PiSemanticOperationError("flow goal must be an object")
    if not value:
        raise PiSemanticOperationError("flow goal must be a non-empty object")
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PiSemanticOperationError("flow goal is not JSON serializable") from exc
    return deepcopy(value)


def _semantic_text(value: Any, *, label: str, identifier: bool = False) -> str:
    if not isinstance(value, str) or not (text := value.strip()):
        raise PiSemanticOperationError(f"{label} must be a non-empty string")
    if identifier and _STABLE_IDENTIFIER.fullmatch(text) is None:
        raise PiSemanticOperationError(
            f"{label} must be an ASCII identifier beginning with a letter"
        )
    return text


def _schema_has_path(schema: Any, path: str) -> bool:
    if not path:
        return False
    node = schema
    for token in [value for value in re.split(r"\.|\[\d+\]", path) if value]:
        if not isinstance(node, dict):
            return False
        if node.get("type") == "array" or "items" in node:
            node = node.get("items") or {}
        properties = node.get("properties")
        if isinstance(properties, dict) and token in properties:
            node = properties[token]
        elif token in node and token not in {"type", "required", "description"}:
            node = node[token]
        else:
            return False
    return True


def _validate_source_binding(spec: dict[str, Any], value: Any) -> dict[str, Any]:
    try:
        binding = SourceBinding.model_validate(value)
    except (TypeError, ValueError) as exc:
        raise PiSemanticOperationError(
            f"invalid atomic source_binding: {exc}"
        ) from exc
    if binding.kind not in {
        SourceBindingKind.PREVIOUS_RESPONSE,
        SourceBindingKind.DEPENDENCY_RESPONSE,
    }:
        return binding.model_dump(mode="json", exclude_none=True)
    source_id = str(binding.request_definition_id or binding.request_id or "")
    path = str(binding.response_path or "")
    source = next((
        step for step in spec.get("steps") or []
        if isinstance(step, dict)
        and source_id
        in {
            _step_id(step),
            _request_id(step),
            str(step.get("request_id") or ""),
            str(step.get("request_definition_id") or ""),
        }
    ), None)
    if source is None or not _schema_has_path(source.get("response_schema") or {}, path):
        raise PiSemanticOperationError(
            f"source_binding response path does not exist: {source_id}:{path}"
        )
    return binding.model_dump(mode="json", exclude_none=True)


def _write_axis(
    spec: dict[str, Any], target: str, fields: list[dict[str, Any]], axis: str, value: Any, *, revision: int,
    evidence_ids: tuple[str, ...], confidence: float,
) -> None:
    if axis in {"caller_required", "wire_required"} and isinstance(value, bool):
        value = "true" if value else "false"
    decision = AxisDecision(
        decision_id=str(uuid4()),
        axis=FieldDimension(axis),
        value=value,
        origin=AxisOrigin.PI,
        evidence_ids=evidence_ids,
        confidence=confidence,
        decided_at_revision=revision,
        manual_override=False,
    )
    try:
        synced = sync_snapshot_axis_decision(
            spec,
            field_uuid=target,
            decision=decision,
        )
    except (KeyError, ValueError) as exc:
        raise PiSemanticOperationError(
            f"registry rejected field axis decision {target}:{axis}: {exc}"
        ) from exc
    if synced is not None:
        return
    mirrors = {
        "display_name": ("display_name", "name", "label"),
        "business_type": ("business_type", "type"),
        "classification": ("classification", "category"),
        "source_binding": ("source_binding", "value_provider"),
        "default_value": ("default_value",),
        "caller_required": ("caller_required", "required"),
        "wire_required": ("wire_required", "required_by_wire"),
        "required_conditions": ("required_conditions",),
        "exposure": ("exposure", "exposed"),
        "enum_binding": ("enum_binding", "choice_contract"),
    }[axis]
    for field in fields:
        field[mirrors[0]] = deepcopy(value)
        # Mirror only an already-present compatibility key.  This keeps one
        # canonical decision while allowing the unchanged workbench to render it.
        for name in mirrors[1:]:
            if name in field:
                field[name] = deepcopy(value)
        decisions = field.setdefault("axis_decisions", {})
        decisions[axis] = decision.model_dump(mode="json")


def _safe_schema(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PiSemanticOperationError("schema value must be an object")
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PiSemanticOperationError("schema is not JSON serializable") from exc
    if value.get("type") not in (None, "object", "array", "string", "number", "integer", "boolean", "null"):
        raise PiSemanticOperationError("unsupported JSON Schema type")
    return deepcopy(value)


def _capabilities(spec: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    values = spec.setdefault("capabilities", [])
    if not isinstance(values, list):
        raise PiSemanticOperationError("capabilities must be a list")
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise PiSemanticOperationError("capability must be an object")
        capability_uuid = _canonical_uuid(
            value.get("capability_uuid"), label="capability_uuid"
        )
        if capability_uuid in result:
            raise PiSemanticOperationError("duplicate capability_uuid")
        value["capability_uuid"] = capability_uuid
        result[capability_uuid] = value
    return values, result


def _harden_capability(spec: dict[str, Any], capability: dict[str, Any]) -> None:
    step_ids = {
        _canonical_uuid(value, label="step_uuid")
        for value in capability.get("step_uuids") or []
    }
    dangerous = any(
        isinstance(step, dict)
        and _step_id(step) in step_ids
        and str(step.get("method") or "").upper() in _DANGEROUS
        for step in spec.get("steps") or []
    )
    if dangerous:
        risk = str(capability.get("risk_level") or "L1").upper()
        if _RISK_ORDER.get(risk, 0) < 3:
            capability["risk_level"] = "L3"
        capability["requires_confirmation"] = True
        capability["requires_human_confirm"] = True


def _apply_operation(
    spec: dict[str, Any], operation: dict[str, Any], *, revision: int,
    evidence: tuple[str, ...], fields: dict[str, list[dict[str, Any]]],
) -> None:
    kind = str(operation.get("op") or "")
    target = str(operation.get("target_uuid") or "")
    if not target:
        raise PiSemanticOperationError("target_uuid is required")
    value = deepcopy(operation.get("value"))
    confidence = float(operation.get("confidence", 0.0))
    if confidence < 0 or confidence > 1:
        raise PiSemanticOperationError("confidence must be between 0 and 1")

    if kind == "set_field_axis":
        target = _canonical_uuid(target, label="field_uuid")
        axis = str(operation.get("axis") or "")
        if axis not in FIELD_AXES:
            raise PiSemanticOperationError(f"field axis is not writable: {axis}")
        targets = _one(fields, target, "field")
        if _manual_axis(spec, targets, target, axis):
            raise PiSemanticOperationError(f"manual field axis cannot be overwritten: {target}:{axis}")
        if axis == "source_binding":
            value = _validate_source_binding(spec, value)
        _write_axis(
            spec, target, targets, axis, value, revision=revision,
            evidence_ids=evidence, confidence=confidence,
        )
        return

    flow_keys = {
        "set_flow_goal": "goal",
        "set_flow_action": "action",
        "set_business_description": "business_description",
    }
    if kind in flow_keys:
        target = _canonical_uuid(target, label="lineage_id")
        expected_target = _flow_uuid(spec)
        if target != expected_target:
            raise PiSemanticOperationError(
                f"flow target does not exist: {target}"
            )
        key = flow_keys[kind]
        if _manual_flow_axis(spec, key):
            raise PiSemanticOperationError(
                f"manual flow decision cannot be overwritten: {target}:{key}"
            )
        spec[key] = (
            _safe_goal(value)
            if key == "goal"
            else _semantic_text(
                value,
                label=f"flow {key}",
                identifier=key == "action",
            )
        )
        spec.setdefault("meta", {}).setdefault(
            "flow_semantic_decisions", {}
        )[key] = _semantic_decision(
            revision=revision,
            evidence=evidence,
            confidence=confidence,
        )
        return

    if kind in {"set_step_name", "set_step_title"}:
        target = _canonical_uuid(target, label="step_uuid")
        steps = [
            step for step in spec.get("steps") or [] if isinstance(step, dict)
        ]
        matches = [
            step
            for step in steps
            if _canonical_uuid(step.get("step_uuid"), label="step_uuid") == target
        ]
        if len(matches) != 1:
            raise PiSemanticOperationError(
                f"step target does not exist uniquely: {target}"
            )
        step = matches[0]
        key = "name" if kind == "set_step_name" else "title"
        if _manual_step_axis(spec, step, target, key):
            raise PiSemanticOperationError(
                f"manual step decision cannot be overwritten: {target}:{key}"
            )
        step[key] = _semantic_text(value, label=f"step {key}")
        step.setdefault("semantic_decisions", {})[key] = _semantic_decision(
            revision=revision,
            evidence=evidence,
            confidence=confidence,
        )
        return

    capabilities, by_capability = _capabilities(spec)
    if kind in {
        "set_input_schema",
        "set_output_schema",
        "set_capability_name",
        "set_capability_title",
        "set_capability_description",
    }:
        target = _canonical_uuid(target, label="capability_uuid")
        capability = by_capability.get(target)
        if capability is None:
            raise PiSemanticOperationError(f"capability target does not exist: {target}")
        key = {
            "set_input_schema": "input_schema",
            "set_output_schema": "output_schema",
            "set_capability_name": "name",
            "set_capability_title": "title",
            "set_capability_description": "description",
        }[kind]
        _reject_manual_capability(spec, capability, target, key)
        capability[key] = (
            _safe_schema(value)
            if key.endswith("schema")
            else _semantic_text(
                value,
                label=f"capability {key}",
                identifier=key == "name",
            )
        )
        capability.setdefault("semantic_decisions", {})[key] = _semantic_decision(
            revision=revision,
            evidence=evidence,
            confidence=confidence,
        )
        return

    if kind == "link_field_binding":
        target = _canonical_uuid(target, label="field_uuid")
        targets = _one(fields, target, "field")
        if _manual_axis(spec, targets, target, "source_binding"):
            raise PiSemanticOperationError(
                f"manual field axis cannot be overwritten: {target}:source_binding"
            )
        if not isinstance(value, dict):
            raise PiSemanticOperationError("field binding value must be an object")
        binding_id = _canonical_uuid(
            value.get("binding_uuid") or value.get("binding_id"),
            label="binding_uuid",
        )
        request_id = _canonical_uuid(
            value.get("request_definition_id"),
            label="request_definition_id",
        )
        if not isinstance(spec.get("field_registry"), dict):
            raise PiSemanticOperationError(
                "permanent field binding requires field_registry"
            )
        if not binding_id or not request_id:
            raise PiSemanticOperationError("binding_uuid and request_definition_id are required")
        request = next(
            (
                row
                for row in _request_rows(spec)
                if request_id
                in {
                    _request_id(row),
                    str(row.get("request_id") or ""),
                    str(row.get("request_definition_id") or ""),
                    str(row.get("observation_id") or ""),
                }
            ),
            None,
        )
        if request is None:
            raise PiSemanticOperationError(f"binding request does not exist: {request_id}")
        binding = {
            **value,
            "binding_uuid": binding_id,
            "field_uuid": target,
            "origin": "pi",
            "evidence_ids": list(evidence),
            "decided_at_revision": revision,
        }
        bindings = spec.setdefault("field_wire_bindings", [])
        if any(str(item.get("binding_uuid") or item.get("binding_id") or "") == binding_id for item in bindings):
            raise PiSemanticOperationError(f"binding already exists: {binding_id}")
        bindings.append(binding)
        for field in targets:
            field.setdefault("wire_binding_ids", []).append(binding_id)
        registry_payload = spec["field_registry"]
        try:
                registry = FieldRegistry.from_snapshot(registry_payload)
                definition_uuid = UUID(request_id)
                binding_uuid = UUID(binding_id)
                step = next(
                    (
                        item
                        for item in spec.get("steps") or []
                        if isinstance(item, dict)
                        and request_id
                        in {
                            str(item.get("request_definition_id") or ""),
                            str(item.get("request_id") or ""),
                        }
                    ),
                    None,
                )
                if step is None:
                    raise ValueError("binding request has no materialized step")
                step_uuid = UUID(
                    _canonical_uuid(step.get("step_uuid"), label="step_uuid")
                )
                wire_path = str(
                    value.get("wire_path")
                    or value.get("path")
                    or targets[0].get("wire_path")
                    or targets[0].get("path")
                    or ""
                )
                if not wire_path:
                    raise ValueError("wire_path is required")
                raw_tokens = value.get("wire_tokens")
                if isinstance(raw_tokens, list):
                    tokens = tuple(raw_tokens)
                else:
                    tokens = tuple(
                        int(part) if part.isdigit() else part
                        for part in wire_path.replace("]", "").replace("[", ".").split(".")
                        if part
                    )
                registry.add_wire_binding(
                    FieldWireBinding(
                        binding_id=binding_uuid,
                        field_uuid=UUID(target),
                        request_definition_id=definition_uuid,
                        observation_ids=tuple(
                            str(item) for item in value.get("observation_ids") or ()
                        ),
                        step_uuid=step_uuid,
                        direction=BindingDirection(str(value.get("direction") or "input")),
                        wire_path=wire_path,
                        wire_tokens=tokens,
                        binding_role=BindingRole(
                            str(value.get("binding_role") or "caller_input")
                        ),
                    )
                )
                spec["field_registry"] = registry.snapshot().model_dump(mode="json")
        except (TypeError, ValueError) as exc:
            raise PiSemanticOperationError(
                f"invalid permanent field binding: {exc}"
            ) from exc
        return

    if kind == "unlink_field_binding":
        target = _canonical_uuid(target, label="field_uuid")
        targets = _one(fields, target, "field")
        if _manual_axis(spec, targets, target, "source_binding"):
            raise PiSemanticOperationError(
                f"manual field axis cannot be overwritten: {target}:source_binding"
            )
        raw_binding_id = (value or {}).get("binding_uuid") if isinstance(value, dict) else value
        binding_id = _canonical_uuid(raw_binding_id, label="binding_uuid")
        if not isinstance(spec.get("field_registry"), dict):
            raise PiSemanticOperationError(
                "permanent field binding removal requires field_registry"
            )
        bindings = spec.setdefault("field_wire_bindings", [])
        if not any(str(item.get("binding_uuid") or item.get("binding_id") or "") == binding_id for item in bindings):
            raise PiSemanticOperationError(f"binding does not exist: {binding_id}")
        spec["field_wire_bindings"] = [
            item for item in bindings
            if str(item.get("binding_uuid") or item.get("binding_id") or "") != binding_id
        ]
        for field in targets:
            field["wire_binding_ids"] = [item for item in field.get("wire_binding_ids") or [] if str(item) != binding_id]
        registry_payload = spec["field_registry"]
        try:
            registry = FieldRegistry.from_snapshot(registry_payload)
            registry.remove_wire_binding(target, binding_id)
            spec["field_registry"] = registry.snapshot().model_dump(mode="json")
        except (TypeError, ValueError) as exc:
            raise PiSemanticOperationError(
                f"invalid permanent field binding removal: {exc}"
            ) from exc
        return

    if kind == "create_capability":
        if not isinstance(value, dict):
            raise PiSemanticOperationError("capability value must be an object")
        # target_uuid is an existing request/step anchor; the new identity is
        # explicit and never derived from a mutable name or list position.
        target = _canonical_uuid(target, label="target_uuid")
        anchors = {
            _canonical_uuid(step.get("step_uuid"), label="step_uuid")
            for step in spec.get("steps") or [] if isinstance(step, dict)
        } | {
            _canonical_uuid(
                row.get("request_definition_id"),
                label="request_definition_id",
            )
            for row in _request_rows(spec)
            if row.get("request_definition_id")
        }
        if target not in anchors:
            raise PiSemanticOperationError(f"capability anchor does not exist: {target}")
        new_id = _canonical_uuid(
            value.get("capability_uuid"), label="capability_uuid"
        )
        if not new_id or new_id in by_capability:
            raise PiSemanticOperationError("a unique capability_uuid is required")
        _reject_manual_capability(
            spec,
            None,
            new_id,
            "deleted",
            "existence",
            "membership",
        )
        steps_by_uuid = {
            _canonical_uuid(step.get("step_uuid"), label="step_uuid"): step
            for step in spec.get("steps") or [] if isinstance(step, dict)
        }
        requested_step_uuids = [
            _canonical_uuid(item, label="step_uuid")
            for item in value.get("step_uuids") or value.get("step_ids") or []
        ]
        if not set(requested_step_uuids).issubset(steps_by_uuid):
            raise PiSemanticOperationError("capability references an unknown step UUID")
        item = {
            **value,
            "capability_uuid": new_id,
            "capability_id": new_id,
            "step_uuids": requested_step_uuids,
            "step_ids": [
                str(steps_by_uuid[item].get("step_id") or "")
                for item in requested_step_uuids
            ],
            "origin": "pi",
        }
        supplied_refs = [
            dict(ref) for ref in value.get("request_refs") or ()
            if isinstance(ref, dict)
        ]
        refs: list[dict[str, Any]] = []
        for step_uuid in requested_step_uuids:
            step = steps_by_uuid[step_uuid]
            ref = next(
                (
                    item for item in supplied_refs
                    if str(item.get("step_uuid") or "") == step_uuid
                    or str(item.get("step_id") or "")
                    == str(step.get("step_id") or "")
                ),
                {},
            )
            refs.append({
                **ref,
                "request_id": str(step.get("request_id") or ""),
                "step_id": str(step.get("step_id") or ""),
                "step_uuid": step_uuid,
                "origin": "pi",
            })
        item["request_refs"] = refs
        _harden_capability(spec, item)
        capabilities.append(item)
        return

    if kind == "move_request_to_capability":
        if not isinstance(value, dict):
            raise PiSemanticOperationError("move value must be an object")
        request_id = _canonical_uuid(target, label="request_definition_uuid")
        destination_id = _canonical_uuid(
            value.get("capability_uuid"), label="capability_uuid"
        )
        destination = by_capability.get(destination_id)
        if destination is None:
            raise PiSemanticOperationError(f"destination capability does not exist: {destination_id}")
        _reject_manual_capability(spec, destination, destination_id, "membership", "members")
        step = next((
            item for item in spec.get("steps") or []
            if isinstance(item, dict) and request_id in {_request_id(item), _step_id(item)}
        ), None)
        if step is None:
            raise PiSemanticOperationError(f"request target does not exist: {request_id}")
        step_ref = _step_id(step)
        step_ref = _canonical_uuid(step_ref, label="step_uuid")
        mutable_step_id = str(step.get("step_id") or "")
        for item in capabilities:
            if step_ref in {str(member) for member in item.get("step_uuids") or []}:
                _reject_manual_capability(
                    spec,
                    item,
                    _capability_id(item),
                    "membership",
                    "members",
                )
            item["step_uuids"] = [
                member for member in item.get("step_uuids") or []
                if str(member) != step_ref
            ]
            item["step_ids"] = [
                member for member in item.get("step_ids") or []
                if str(member) != mutable_step_id
            ]
            item["request_refs"] = [
                member for member in item.get("request_refs") or []
                if str(member.get("step_uuid") or "") != step_ref
                and str(member.get("step_id") or "") != mutable_step_id
            ]
        destination.setdefault("step_uuids", []).append(step_ref)
        destination["step_uuids"] = list(dict.fromkeys(destination["step_uuids"]))
        destination.setdefault("step_ids", []).append(mutable_step_id)
        destination["step_ids"] = list(dict.fromkeys(destination["step_ids"]))
        destination.setdefault("request_refs", []).append({
            "request_id": _request_id(step),
            "step_id": mutable_step_id,
            "step_uuid": step_ref,
            "usage": str(value.get("usage") or "execute"), "origin": "pi",
            "evidence_ids": list(evidence),
        })
        _harden_capability(spec, destination)
        return

    target = _canonical_uuid(target, label="capability_uuid")
    capability = by_capability.get(target)
    if capability is None:
        raise PiSemanticOperationError(f"capability target does not exist: {target}")
    if kind == "delete_capability":
        _reject_manual_capability(spec, capability, target, destructive=True)
        capabilities.remove(capability)
        return
    if kind == "merge_capabilities":
        if not isinstance(value, dict):
            raise PiSemanticOperationError("merge value must be an object")
        source_ids = [
            _canonical_uuid(item, label="source_capability_uuid")
            for item in value.get("source_capability_uuids") or []
        ]
        sources = [by_capability.get(item) for item in source_ids]
        if not source_ids or any(item is None for item in sources) or target in source_ids:
            raise PiSemanticOperationError("merge references invalid source capabilities")
        _reject_manual_capability(spec, capability, target, "membership", "members")
        for source in sources:
            assert source is not None
            _reject_manual_capability(
                spec,
                source,
                _capability_id(source),
                destructive=True,
            )
            capability.setdefault("step_ids", []).extend(source.get("step_ids") or [])
            capability.setdefault("step_uuids", []).extend(
                source.get("step_uuids") or []
            )
            capability.setdefault("request_refs", []).extend(deepcopy(source.get("request_refs") or []))
            source_risk = str(source.get("risk_level") or "L1").upper()
            current_risk = str(capability.get("risk_level") or "L1").upper()
            capability["risk_level"] = max((current_risk, source_risk), key=lambda item: _RISK_ORDER.get(item, 0))
            capabilities.remove(source)
        capability["step_ids"] = list(dict.fromkeys(capability.get("step_ids") or []))
        capability["step_uuids"] = list(
            dict.fromkeys(capability.get("step_uuids") or [])
        )
        _harden_capability(spec, capability)
        return
    if kind == "split_capability":
        _reject_manual_capability(spec, capability, target, "membership", "members")
        if not isinstance(value, dict) or not isinstance(value.get("capabilities"), list):
            raise PiSemanticOperationError("split requires a capabilities list")
        steps_by_uuid = {
            _canonical_uuid(step.get("step_uuid"), label="step_uuid"): step
            for step in spec.get("steps") or [] if isinstance(step, dict)
        }
        original_steps = {
            _canonical_uuid(item, label="step_uuid")
            for item in capability.get("step_uuids") or ()
        }
        replacements: list[dict[str, Any]] = []
        replacement_ids: set[str] = set()
        assigned: set[str] = set()
        for raw in value["capabilities"]:
            if not isinstance(raw, dict):
                raise PiSemanticOperationError("split capability must be an object")
            new_id = _canonical_uuid(
                raw.get("capability_uuid"), label="capability_uuid"
            )
            steps = {
                _canonical_uuid(item, label="step_uuid")
                for item in raw.get("step_uuids") or raw.get("step_ids") or []
            }
            if (
                new_id in by_capability
                or new_id in replacement_ids
                or steps & assigned
                or not steps.issubset(original_steps)
            ):
                raise PiSemanticOperationError("split capability identity or membership is invalid")
            replacement_ids.add(new_id)
            assigned.update(steps)
            item = {
                **raw,
                "capability_uuid": new_id,
                "capability_id": new_id,
                "step_uuids": sorted(steps),
                "step_ids": [
                    str(steps_by_uuid[item].get("step_id") or "")
                    for item in sorted(steps)
                ],
                "origin": "pi",
            }
            original_refs = [
                ref for ref in capability.get("request_refs") or ()
                if isinstance(ref, dict)
            ]
            item["request_refs"] = [
                {
                    **next(
                        (
                            ref for ref in original_refs
                            if str(ref.get("step_uuid") or "") == step_uuid
                            or str(ref.get("step_id") or "")
                            == str(steps_by_uuid[step_uuid].get("step_id") or "")
                        ),
                        {},
                    ),
                    "request_id": str(
                        steps_by_uuid[step_uuid].get("request_id") or ""
                    ),
                    "step_id": str(
                        steps_by_uuid[step_uuid].get("step_id") or ""
                    ),
                    "step_uuid": step_uuid,
                    "origin": "pi",
                }
                for step_uuid in sorted(steps)
            ]
            _harden_capability(spec, item)
            replacements.append(item)
        if assigned != original_steps:
            raise PiSemanticOperationError("split must assign every original step exactly once")
        index = capabilities.index(capability)
        capabilities[index:index + 1] = replacements
        return
    raise PiSemanticOperationError(f"unsupported semantic operation: {kind}")


def _harden_delete_safety(spec: dict[str, Any]) -> None:
    step_methods = {
        _canonical_uuid(step.get("step_uuid"), label="step_uuid"):
        str(step.get("method") or "").upper()
        for step in spec.get("steps") or [] if isinstance(step, dict)
    }
    for step in spec.get("steps") or []:
        if isinstance(step, dict) and str(step.get("method") or "").upper() == "DELETE":
            risk = str(step.get("risk_level") or "L1").upper()
            if _RISK_ORDER.get(risk, 0) < 3:
                step["risk_level"] = "L3"
            step["requires_confirmation"] = True
            step["requires_human_confirm"] = True
    for capability in spec.get("capabilities") or []:
        if not isinstance(capability, dict):
            continue
        if any(
            step_methods.get(_canonical_uuid(value, label="step_uuid")) == "DELETE"
            for value in capability.get("step_uuids") or []
        ):
            _harden_capability(spec, capability)


def apply_pi_semantic_operations(
    snapshot: dict[str, Any], submission: dict[str, Any],
) -> dict[str, Any]:
    """Apply one Pi turn's operation batch atomically."""

    revision = int(snapshot.get("revision") or 0)
    expected = int(submission.get("expected_revision", -1))
    if expected != revision:
        raise PiSemanticOperationError(
            f"semantic operation revision conflict: expected {expected}, current {revision}"
        )
    operations = submission.get("operations")
    if operations is None and isinstance(submission.get("plan"), dict):
        operations = submission["plan"].get("operations")
    if not isinstance(operations, list):
        raise PiSemanticOperationError("Pi submission requires an operations list")
    spec = deepcopy(snapshot)
    available = _evidence_ids(spec)
    fields = _collect_fields(spec)
    audit: list[dict[str, Any]] = []
    for raw in operations:
        if not isinstance(raw, dict):
            raise PiSemanticOperationError("semantic operation must be an object")
        operation = deepcopy(raw)
        kind = str(operation.get("op") or "")
        if kind not in ALLOWED_OPERATIONS:
            raise PiSemanticOperationError(f"semantic operation is not allowed: {kind}")
        op_revision = int(operation.get("expected_revision", -1))
        if op_revision != revision:
            raise PiSemanticOperationError(
                f"operation {kind} expected revision {op_revision}, current {revision}"
            )
        evidence = _require_evidence(operation, available)
        _apply_operation(
            spec, operation, revision=revision, evidence=evidence, fields=fields,
        )
        audit.append({
            "op": kind,
            "target_uuid": str(operation.get("target_uuid") or ""),
            "axis": str(operation.get("axis") or ""),
            "evidence_ids": list(evidence),
            "confidence": float(operation.get("confidence", 0.0)),
        })
    _harden_delete_safety(spec)
    spec.setdefault("meta", {})["pi_semantic_commit"] = {
        "expected_revision": revision,
        "operation_count": len(audit),
        "operations": audit,
    }
    return spec


def is_semantic_operation_submission(submission: dict[str, Any]) -> bool:
    if isinstance(submission.get("operations"), list):
        return True
    plan = submission.get("plan")
    return isinstance(plan, dict) and isinstance(plan.get("operations"), list)
