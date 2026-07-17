"""Revisioned editing commands for the existing recording workbench.

The compatibility ``full_spec`` is a projection, never the system of record.  This
module translates UI edits into a new server-owned revision and records exactly
which dimensions belong to the user.  Pi merges consult those pins and therefore
cannot overwrite an explicit human decision.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable
from uuid import UUID, uuid4

from dano_recording.executability import check_executability
from dano_recording.domain.fields import (
    AxisDecision,
    AxisOrigin,
    FieldDimension,
    RequiredState,
    SourceBinding,
    SourceBindingKind,
)
from dano_recording.field_registry import (
    BindingDirection,
    BindingRole,
    FieldAlias,
    FieldAliasKind,
    FieldRegistry,
    FieldWireBinding,
    clear_snapshot_manual_axis,
    sync_snapshot_axis_decision,
)
from dano_recording.pi_semantic_ops import (
    apply_pi_semantic_operations,
    is_semantic_operation_submission,
)


class DecisionCommandError(ValueError):
    pass


def _is_v3(spec: dict[str, Any]) -> bool:
    return int(spec.get("recording_contract_version") or 0) >= 1


def _stable_id(prefix: str, value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def _items(spec: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = spec.setdefault(key, [])
    if not isinstance(value, list):
        raise DecisionCommandError(f"{key} must be a list")
    return value


def _pins(spec: dict[str, Any]) -> dict[str, str]:
    meta = spec.setdefault("meta", {})
    if not isinstance(meta, dict):
        raise DecisionCommandError("meta must be an object")
    pins = meta.setdefault("decision_origins", {})
    if not isinstance(pins, dict):
        pins = {}
        meta["decision_origins"] = pins
    return pins


def _pin(spec: dict[str, Any], path: str) -> None:
    _pins(spec)[path] = "user"


def _request_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    facts = spec.setdefault("request_facts", {})
    if isinstance(facts, list):
        rows = facts
        spec["request_facts"] = {"requests": rows}
        return rows
    if not isinstance(facts, dict):
        raise DecisionCommandError("request_facts must be an object")
    rows = facts.setdefault("requests", [])
    if not isinstance(rows, list):
        raise DecisionCommandError("request_facts.requests must be a list")
    return rows


def _set_request_materialization(
    spec: dict[str, Any],
    request_id: str,
    *,
    step_id: str | None,
) -> None:
    if not request_id:
        return
    materialized = bool(step_id)
    for row in _request_rows(spec):
        if str(row.get("request_id") or "") != request_id:
            continue
        row.update({
            "disposition": "materialized" if materialized else "review_candidate",
            "role": "materialized" if materialized else "review_candidate",
            "keep": materialized,
            "materialized_step_id": step_id,
            "state": "materialized" if materialized else "removed_by_user",
            "reason": row.get("reason") if materialized else "user removed materialization",
        })
    facts = spec.get("request_facts") or {}
    if isinstance(facts, dict):
        analysis = facts.setdefault("analysis", {}).setdefault(request_id, {})
        analysis.update({
            "disposition": "materialized" if materialized else "review_candidate",
            "role": "materialized" if materialized else "review_candidate",
            "keep": materialized,
            "reason": analysis.get("reason") if materialized else "user removed materialization",
        })
        usage = facts.setdefault("usage", {}).setdefault(request_id, {})
        usage.update({
            "request_id": request_id,
            "materialized_step_id": step_id,
            "state": "materialized" if materialized else "removed_by_user",
        })


def _validated_uuid(value: Any, *, label: str, generate: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw and generate:
        return str(uuid4())
    try:
        return str(UUID(raw))
    except (TypeError, ValueError) as exc:
        raise DecisionCommandError(f"{label} must be a valid UUID") from exc


def _step_key(step: dict[str, Any]) -> str:
    """Return the permanent V3 identity, with the mutable legacy id as fallback."""

    return str(step.get("step_uuid") or step.get("step_id") or "")


def _find_step(spec: dict[str, Any], step_ref: str) -> dict[str, Any]:
    """Resolve permanent identity first and retain step_id for legacy snapshots."""

    steps = _items(spec, "steps")
    step = next(
        (item for item in steps if str(item.get("step_uuid") or "") == step_ref),
        None,
    )
    if step is None:
        step = next(
            (item for item in steps if str(item.get("step_id") or "") == step_ref),
            None,
        )
    if step is None:
        raise DecisionCommandError(f"step not found: {step_ref}")
    return step


def _find_edit_step(spec: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    step_uuid = str(edit.get("step_uuid") or "").strip()
    if _is_v3(spec) and not step_uuid:
        raise DecisionCommandError("recording V3 step edits require step_uuid")
    if step_uuid:
        _validated_uuid(step_uuid, label="step_uuid")
        # A supplied permanent identity must never silently fall back to a
        # coincidentally equal mutable id.
        step = next(
            (
                item
                for item in _items(spec, "steps")
                if str(item.get("step_uuid") or "") == step_uuid
            ),
            None,
        )
        if step is None:
            raise DecisionCommandError(f"step_uuid not found: {step_uuid}")
        return step
    return _find_step(spec, str(edit.get("step_id") or ""))


def _find_param(
    step: dict[str, Any],
    edit: dict[str, Any],
    *,
    require_uuid: bool = False,
) -> dict[str, Any]:
    field_uuid = str(edit.get("field_uuid") or "").strip()
    path = str(edit.get("param_path") or "").removeprefix("body.")
    key = str(edit.get("param_key") or "")
    label = str(edit.get("param_label") or "")
    params = step.setdefault("params", [])
    if require_uuid and not field_uuid:
        raise DecisionCommandError("recording V3 field edits require field_uuid")
    if field_uuid:
        _validated_uuid(field_uuid, label="field_uuid")
        param = next(
            (
                item
                for item in params
                if str(item.get("field_uuid") or item.get("field_id") or "")
                == field_uuid
            ),
            None,
        )
        if param is None:
            raise DecisionCommandError(
                f"field_uuid not found on {_step_key(step)}: {field_uuid}"
            )
        return param
    for param in params:
        candidate_path = str(param.get("path") or "").removeprefix("body.")
        if path and candidate_path == path:
            return param
        if key and str(param.get("key") or "") == key:
            return param
        if label and str(param.get("label") or "") == label:
            return param
    raise DecisionCommandError(
        f"parameter not found on {_step_key(step)}: {path or key or label}"
    )


def _param_ref(param: dict[str, Any]) -> str:
    return str(
        param.get("field_uuid")
        or param.get("field_id")
        or param.get("path")
        or param.get("key")
        or param.get("label")
        or ""
    )


_AXIS_EDIT_KEYS: dict[FieldDimension, frozenset[str]] = {
    FieldDimension.DISPLAY_NAME: frozenset({"display_name", "name", "label", "key"}),
    FieldDimension.BUSINESS_TYPE: frozenset({"business_type", "type"}),
    FieldDimension.CLASSIFICATION: frozenset({"classification", "category"}),
    FieldDimension.SOURCE_BINDING: frozenset({"source_binding", "source_kind", "source"}),
    FieldDimension.DEFAULT_VALUE: frozenset({"default_value", "default"}),
    FieldDimension.CALLER_REQUIRED: frozenset({"caller_required", "required"}),
    FieldDimension.WIRE_REQUIRED: frozenset({"wire_required"}),
    FieldDimension.REQUIRED_CONDITIONS: frozenset({"required_conditions"}),
    FieldDimension.EXPOSURE: frozenset({"exposed", "exposed_to_caller", "exposed_to_user"}),
    FieldDimension.ENUM_BINDING: frozenset({"enum_binding", "choice_contract", "enum_options"}),
}


def _axis_for_edit(field: str) -> FieldDimension | None:
    return next(
        (axis for axis, keys in _AXIS_EDIT_KEYS.items() if field in keys),
        None,
    )


def _required_state(value: Any) -> RequiredState:
    if value is True or str(value).casefold() == "true":
        return RequiredState.TRUE
    if value is False or str(value).casefold() == "false":
        return RequiredState.FALSE
    return RequiredState.UNKNOWN


def _manual_source_binding(param: dict[str, Any], value: Any) -> SourceBinding:
    if isinstance(value, dict):
        try:
            return SourceBinding.model_validate(value)
        except ValueError as exc:
            raise DecisionCommandError(f"source_binding must be atomic and complete: {exc}") from exc
    kind = str(value or param.get("source_kind") or "").casefold()
    if kind in {"caller", "caller_input", "user_input"}:
        return SourceBinding(kind=SourceBindingKind.CALLER)
    if kind in {"runtime_context", "page_context", "request_header", "system"}:
        resolver = str(param.get("runtime_resolver") or "").strip()
        if not resolver:
            resolver = "runtime_context.request_headers" if kind == "request_header" else "runtime_context.page"
        return SourceBinding(
            kind=SourceBindingKind.RUNTIME_CONTEXT,
            runtime_resolver=resolver,
        )
    if kind in {"previous_response", "dependency_response", "response"}:
        request_ref = param.get("source_request_definition_id") or param.get("source_request_id")
        response_path = param.get("source_path") or param.get("response_path")
        if not request_ref or not response_path:
            raise DecisionCommandError(
                "response source requires request_definition_id and response_path"
            )
        return SourceBinding(
            kind=SourceBindingKind.PREVIOUS_RESPONSE,
            request_definition_id=str(request_ref),
            response_path=str(response_path),
        )
    if kind == "constant":
        constant = param.get("constant", param.get("default_value"))
        if constant is None:
            raise DecisionCommandError("constant source requires a concrete constant")
        return SourceBinding(kind=SourceBindingKind.CONSTANT, value=constant)
    if kind in {"computed", "derived"}:
        expression = str(param.get("expression") or "").strip()
        if not expression:
            raise DecisionCommandError("derived source requires an expression")
        return SourceBinding(kind=SourceBindingKind.DERIVED, expression=expression)
    if kind in {"unknown", "unresolved", ""}:
        return SourceBinding(kind=SourceBindingKind.UNKNOWN)
    raise DecisionCommandError(f"unsupported source binding kind: {kind}")


_ENUM_SOURCE_KINDS = {
    "api_option", "manual_enum", "page_enum", "form_option", "static_enum",
}


def _source_binding(value: Any) -> SourceBinding:
    try:
        return value if isinstance(value, SourceBinding) else SourceBinding.model_validate(value)
    except ValueError as exc:
        raise DecisionCommandError(f"source_binding must be atomic and complete: {exc}") from exc


def _source_projection(
    binding: SourceBinding,
    *,
    preferred_kind: str | None = None,
) -> tuple[str, dict[str, Any], bool]:
    preferred = str(preferred_kind or "").casefold()
    if binding.kind is SourceBindingKind.CALLER:
        if preferred not in _ENUM_SOURCE_KINDS | {"user_input"}:
            preferred = "user_input"
        return preferred, {"kind": preferred}, False
    if binding.kind in {
        SourceBindingKind.PREVIOUS_RESPONSE,
        SourceBindingKind.DEPENDENCY_RESPONSE,
    }:
        request_ref = binding.request_definition_id or binding.request_id
        return (
            "previous_response",
            {
                "kind": "previous_response",
                "source_request_id": request_ref,
                "request_definition_id": request_ref,
                "source_path": binding.response_path,
                "response_path": binding.response_path,
                "manual": True,
            },
            False,
        )
    if binding.kind is SourceBindingKind.CONSTANT:
        return "constant", {"kind": "constant", "constant": binding.value, "manual": True}, False
    if binding.kind is SourceBindingKind.DEFAULT:
        return "constant", {"kind": "constant", "constant": binding.value, "manual": True}, False
    if binding.kind is SourceBindingKind.DERIVED:
        return "computed", {"kind": "computed", "expression": binding.expression, "manual": True}, False
    if binding.kind is SourceBindingKind.RUNTIME_CONTEXT:
        resolver = str(binding.runtime_resolver or "")
        if resolver.startswith("runtime_context.request_headers"):
            header = resolver.removeprefix("runtime_context.request_headers").lstrip(".")
            return "request_header", {"kind": "request_header", "header": header, "runtime_resolver": resolver, "manual": True}, not bool(header)
        if resolver == "runtime_context.current_user" or resolver.startswith("runtime_context.current_user."):
            return "current_user", {"kind": "current_user", "runtime_resolver": resolver, "manual": True}, False
        if resolver == "runtime_context.system_time" or resolver.startswith("runtime_context.system_time."):
            return "system_time", {"kind": "system_time", "runtime_resolver": resolver, "manual": True}, False
        if resolver.startswith("runtime_context.generated."):
            strategy = resolver.rsplit(".", 1)[-1]
            return "system_generated", {"kind": "system_generated", "strategy": strategy, "runtime_resolver": resolver, "manual": True}, False
        context_key = resolver.removeprefix("runtime_context.")
        return "page_context", {"kind": "page_context", "context_key": context_key, "runtime_resolver": resolver, "manual": True}, not bool(context_key)
    if preferred in {"previous_response", "computed"}:
        return preferred, {"kind": preferred, "manual": True}, True
    return "unknown", {}, True


def _source_kind_matches(binding: SourceBinding, preferred_kind: str) -> bool:
    projected, _, _ = _source_projection(binding, preferred_kind=preferred_kind)
    return projected == preferred_kind


def _automatic_source_projection_kind(
    param: dict[str, Any],
    binding: SourceBinding,
    step: dict[str, Any] | None = None,
) -> str | None:
    if binding.kind is not SourceBindingKind.CALLER:
        return None
    path = str(param.get("path") or "")
    key = str(param.get("key") or "")
    selects = [
        item for item in (step or {}).get("selects") or ()
        if isinstance(item, dict)
        and (
            str(item.get("path") or "") == path
            or str(item.get("id_path") or "") == path
            or str(item.get("param") or "") == key
        )
    ]
    if any(item.get("source_url") for item in selects):
        return "api_option"
    if (
        param.get("enum_binding")
        or param.get("enum_options")
        or any(item.get("options") or item.get("enum_source") for item in selects)
    ):
        return "manual_enum"
    return None


def _clear_enum_projection(
    spec: dict[str, Any],
    step: dict[str, Any],
    param: dict[str, Any],
) -> None:
    param["enum_binding"] = None
    param["enum_options"] = None
    param["enum_value_map"] = None
    path = str(param.get("path") or "")
    key = str(param.get("key") or "")
    step["selects"] = [
        item for item in step.get("selects") or []
        if str(item.get("path") or "") != path
        and str(item.get("id_path") or "") != path
        and str(item.get("param") or "") != key
    ]
    _sync_manual_axis(spec, param, field="enum_binding", value=None)
    field_ref = _param_ref(param)
    step_ref = _step_key(step)
    for field in ("enum_binding", "enum_options", "enum_value_map"):
        _pin(spec, f"field:{step_ref}:{field_ref}:{field}")
    _pin(spec, f"step:{step_ref}:selects")


def _manual_axis_value(
    param: dict[str, Any],
    axis: FieldDimension,
    edited_value: Any,
) -> Any:
    if axis is FieldDimension.SOURCE_BINDING:
        return _manual_source_binding(param, edited_value)
    if axis in {FieldDimension.CALLER_REQUIRED, FieldDimension.WIRE_REQUIRED}:
        return _required_state(edited_value)
    if axis is FieldDimension.EXPOSURE:
        return bool(edited_value)
    return edited_value


def _sync_manual_axis(
    spec: dict[str, Any],
    param: dict[str, Any],
    *,
    field: str,
    value: Any,
) -> None:
    axis = _axis_for_edit(field)
    field_uuid = str(param.get("field_uuid") or "")
    if axis is None or not field_uuid or not isinstance(spec.get("field_registry"), dict):
        return
    decision = AxisDecision(
        decision_id=str(uuid4()),
        axis=axis,
        value=_manual_axis_value(param, axis, value),
        origin=AxisOrigin.MANUAL,
        evidence_ids=(f"manual_edit:{axis.value}",),
        confidence=1.0,
        decided_at_revision=int(spec.get("revision") or 0) + 1,
        manual_override=True,
    )
    sync_snapshot_axis_decision(
        spec,
        field_uuid=field_uuid,
        decision=decision,
    )


def _register_manual_param(
    spec: dict[str, Any],
    step: dict[str, Any],
    param: dict[str, Any],
) -> None:
    payload = spec.get("field_registry")
    if not isinstance(payload, dict):
        requested_uuid = _validated_uuid(
            param.get("field_uuid") or param.get("field_id"),
            label="field_uuid",
            generate=True,
        )
        param["field_uuid"] = requested_uuid
        param["field_id"] = requested_uuid
        return
    registry = FieldRegistry.from_snapshot(payload)
    context = str(
        step.get("request_definition_id")
        or step.get("request_id")
        or step.get("step_uuid")
        or step.get("step_id")
    )
    path = str(param.get("path") or param.get("wire_path") or param.get("key") or "$")
    location = str(param.get("location") or "body")
    aliases = [
        FieldAlias(
            kind=FieldAliasKind.WIRE_PATH,
            value=path,
            context=f"manual:{context}:{location}",
            introduced_at_revision=int(spec.get("revision") or 0) + 1,
        )
    ]
    label = str(param.get("display_name") or param.get("label") or param.get("key") or "").strip()
    if label:
        aliases.append(
            FieldAlias(
                kind=FieldAliasKind.BUSINESS_NAME,
                value=label,
                context=f"manual:{context}:{location}",
                introduced_at_revision=int(spec.get("revision") or 0) + 1,
            )
        )
    requested_uuid = _validated_uuid(
        param.get("field_uuid") or param.get("field_id"),
        label="field_uuid",
        generate=True,
    )
    canonical = registry.register_field(field_uuid=requested_uuid, aliases=aliases)
    try:
        definition_id = UUID(str(step.get("request_definition_id") or ""))
        step_uuid = UUID(str(step.get("step_uuid") or ""))
    except ValueError:
        definition_id = None
        step_uuid = None
    if definition_id is not None and step_uuid is not None:
        tokens: list[str | int] = []
        for part in path.replace("]", "").replace("[", ".").split("."):
            if part:
                tokens.append(int(part) if part.isdigit() else part)
        registry.add_wire_binding(
            FieldWireBinding(
                field_uuid=canonical.field_uuid,
                request_definition_id=definition_id,
                step_uuid=step_uuid,
                direction=BindingDirection.INPUT,
                wire_path=path,
                wire_tokens=tuple(tokens),
                binding_role=BindingRole.CALLER_INPUT,
            )
        )
    spec["field_registry"] = registry.snapshot().model_dump(mode="json")
    param.update({
        "field_uuid": str(canonical.field_uuid),
        "field_id": str(canonical.field_uuid),
        "lineage_id": str(registry.lineage_id),
        "aliases": [item.model_dump(mode="json") for item in canonical.aliases],
        "axis_decisions": {},
    })


def _find_link(spec: dict[str, Any], link_id: str) -> dict[str, Any]:
    link = next((item for item in _items(spec, "links") if str(item.get("link_id")) == link_id), None)
    if link is None:
        raise DecisionCommandError(f"link not found: {link_id}")
    return link


def _capability(spec: dict[str, Any], edit: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    capabilities = _items(spec, "capabilities")
    v3 = int(spec.get("recording_contract_version") or 0) >= 1
    if v3:
        ref = str(edit.get("capability_uuid") or edit.get("capability_ref") or "")
        try:
            UUID(ref)
        except (TypeError, ValueError) as exc:
            raise DecisionCommandError(
                "recording V3 capability edits require capability_uuid"
            ) from exc
        for index, item in enumerate(capabilities):
            if ref == str(item.get("capability_uuid") or ""):
                return index, item
        raise DecisionCommandError(f"capability_uuid not found: {ref}")
    raw_index = edit.get("capability_index")
    if raw_index is not None:
        index = int(raw_index)
        if index < 0 or index >= len(capabilities):
            raise DecisionCommandError(f"capability index out of range: {index}")
        return index, capabilities[index]
    ref = str(edit.get("capability_ref") or edit.get("capability_id") or "")
    for index, item in enumerate(capabilities):
        if ref in {str(item.get("name") or ""), str(item.get("capability_id") or "")}:
            return index, item
    raise DecisionCommandError(f"capability not found: {ref}")


def _cap_ref(capability: dict[str, Any], index: int) -> str:
    return str(
        capability.get("capability_uuid")
        or capability.get("capability_id")
        or capability.get("name")
        or f"idx:{index}"
    )


def _materialize_request(spec: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    v3 = _is_v3(spec)
    request_keys = ("request_id", "observation_id", "request_definition_id")
    supplied = {
        key: str(edit.get(key) or "").strip()
        for key in request_keys
        if str(edit.get(key) or "").strip()
    }
    if v3 and edit.get("request_index") is not None:
        raise DecisionCommandError("recording V3 request_index is display-only")
    if v3 and not supplied:
        raise DecisionCommandError(
            "recording V3 request materialization requires request_id, "
            "observation_id, or request_definition_id"
        )
    request_id = supplied.get("request_id", "")
    request_index = edit.get("request_index")
    rows = _request_rows(spec)
    row = next(
        (
            item
            for item in rows
            if (
                all(
                    str(item.get(key) or "") == value
                    for key, value in supplied.items()
                )
                if v3
                else any(
                    str(item.get(key) or "") == value
                    for key, value in supplied.items()
                )
            )
        ),
        None,
    )
    if (
        row is None
        and v3
        and len(supplied) > 1
        and all(
            any(str(item.get(key) or "") == value for item in rows)
            for key, value in supplied.items()
        )
    ):
        raise DecisionCommandError("recording V3 stable request identities conflict")
    if row is None and not v3 and request_index is not None:
        row = next((item for item in rows if int(item.get("request_index", -1)) == int(request_index)), None)
    if row is None:
        raise DecisionCommandError("captured request not found")
    existing = next(
        (step for step in _items(spec, "steps") if str(step.get("request_id") or "") == str(row.get("request_id") or "")),
        None,
    )
    if existing is not None:
        return existing
    request_id = str(row.get("request_id") or _stable_id("request", row))
    step_id = str(row.get("materialized_step_id") or _stable_id("step", request_id))
    step_uuid = None
    if v3:
        step_uuid = _validated_uuid(
            edit.get("step_uuid") or row.get("step_uuid"),
            label="step_uuid",
            generate=True,
        )
    step = {
        "step_id": step_id,
        "step_uuid": step_uuid,
        "request_id": request_id,
        "name": f"{str(row.get('method') or 'GET').upper()} {row.get('path') or '/'}",
        "method": str(row.get("method") or "GET").upper(),
        "url": str(row.get("url") or row.get("path") or ""),
        "path": str(row.get("path") or "/"),
        "headers": deepcopy(row.get("headers") or {}),
        "query": deepcopy(row.get("query") or {}),
        "body": deepcopy(row.get("post_data")),
        "body_template": deepcopy(row.get("post_data")),
        "content_type": str(row.get("content_type") or ""),
        "params": [],
        "selects": [],
        "sample_inputs": {},
        "response_schema": deepcopy(row.get("response_schema") or {}),
        "risk_level": "L3" if str(row.get("method") or "GET").upper() in {"POST", "PUT", "PATCH", "DELETE"} else "L1",
        "requires_human_confirm": str(row.get("method") or "GET").upper() in {"POST", "PUT", "PATCH", "DELETE"},
        "origin": "captured_request",
    }
    _items(spec, "steps").append(step)
    _set_request_materialization(spec, request_id, step_id=step_id)
    _pin(spec, f"step:{_step_key(step)}:__added__")
    return step


def _remap_step(spec: dict[str, Any], removed: str, kept: str) -> None:
    for link in _items(spec, "links"):
        if link.get("source_step_id") == removed:
            link["source_step_id"] = kept
        if link.get("target_step_id") == removed:
            link["target_step_id"] = kept
    for capability in _items(spec, "capabilities"):
        capability["step_ids"] = list(dict.fromkeys(
            kept if value == removed else value for value in capability.get("step_ids") or []
        ))
        for ref in capability.get("request_refs") or []:
            if ref.get("step_id") == removed:
                ref["step_id"] = kept


def _dedupe(spec: dict[str, Any]) -> None:
    seen: dict[str, dict[str, Any]] = {}
    kept: list[dict[str, Any]] = []
    for step in _items(spec, "steps"):
        identity = str(step.get("request_id") or "")
        if not identity:
            identity = json.dumps(
                [step.get("method"), step.get("path") or step.get("url"), step.get("query"), step.get("body")],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        prior = seen.get(identity)
        if prior is None:
            seen[identity] = step
            kept.append(step)
            continue
        _remap_step(spec, str(step.get("step_id")), str(prior.get("step_id")))
    spec["steps"] = kept
    link_seen: set[tuple[Any, ...]] = set()
    spec["links"] = [
        link for link in _items(spec, "links")
        if not (
            (key := (
                link.get("source_step_id"), link.get("source_path"),
                link.get("target_step_id"), link.get("target_path"),
            )) in link_seen or link_seen.add(key)
        )
    ]


def apply_edits(snapshot: dict[str, Any], edits: Iterable[dict[str, Any]]) -> dict[str, Any]:
    spec = deepcopy(snapshot)
    for raw in edits:
        if not isinstance(raw, dict):
            raise DecisionCommandError("each edit must be an object")
        edit = deepcopy(raw)
        op = str(edit.get("op") or "")
        if op == "update_flow":
            field = str(edit.get("field") or "")
            if not field or field in {"tenant", "recording_id", "request_facts", "meta", "revision"}:
                raise DecisionCommandError(f"flow field is not editable: {field}")
            spec[field] = edit.get("value")
            _pin(spec, f"flow:{field}")
        elif op in {"clear_field_axis", "clear_axis_override"}:
            step = _find_edit_step(spec, edit)
            param = _find_param(step, edit, require_uuid=_is_v3(spec))
            raw_axis = str(edit.get("axis") or edit.get("field") or "")
            axis = _axis_for_edit(raw_axis)
            if axis is None:
                try:
                    axis = FieldDimension(raw_axis)
                except ValueError as exc:
                    raise DecisionCommandError(f"unknown field axis: {raw_axis}") from exc
            field_uuid = str(param.get("field_uuid") or "")
            if not field_uuid:
                raise DecisionCommandError("clear_field_axis requires a permanent field_uuid")
            clear_snapshot_manual_axis(
                spec,
                field_uuid=field_uuid,
                axis=axis,
                revision=int(spec.get("revision") or 0) + 1,
            )
            if axis is FieldDimension.SOURCE_BINDING:
                restored_value = param.get("source_binding")
                if isinstance(restored_value, dict):
                    restored = _source_binding(restored_value)
                    preferred_kind = _automatic_source_projection_kind(
                        param, restored, step,
                    )
                    source_kind, source, needs_configuration = _source_projection(
                        restored,
                        preferred_kind=preferred_kind,
                    )
                    param["source_kind"] = source_kind
                    param["source"] = source
                    param["need_human_confirm"] = needs_configuration
            pins = _pins(spec)
            ref = _param_ref(param)
            for step_ref in {_step_key(step), str(step.get("step_id") or "")}:
                for key in _AXIS_EDIT_KEYS[axis]:
                    pins.pop(f"field:{step_ref}:{ref}:{key}", None)
        elif op == "update" and edit.get("link_id"):
            link = _find_link(spec, str(edit["link_id"]))
            field = str(edit.get("field") or "")
            link[field] = edit.get("value")
            _pin(spec, f"link:{link.get('link_id')}:{field}")
        elif op == "update":
            step = _find_edit_step(spec, edit)
            field = str(edit.get("field") or "")
            if (
                edit.get("field_uuid")
                or edit.get("param_path")
                or edit.get("param_key")
                or edit.get("param_label")
            ):
                param = _find_param(step, edit, require_uuid=_is_v3(spec))
                if field == "source_binding":
                    prior_source_kind = str(param.get("source_kind") or "")
                    binding = _source_binding(edit.get("value"))
                    preferred_kind = str(edit.get("source_kind_projection") or "").casefold()
                    if preferred_kind and not _source_kind_matches(binding, preferred_kind):
                        raise DecisionCommandError(
                            "source_kind_projection does not match source_binding"
                        )
                    source_kind, source, needs_configuration = _source_projection(
                        binding,
                        preferred_kind=preferred_kind or None,
                    )
                    param["source_binding"] = binding.model_dump(
                        mode="json", exclude_none=True,
                    )
                    param["source_kind"] = source_kind
                    param["source"] = source
                    param["need_human_confirm"] = needs_configuration
                    _sync_manual_axis(
                        spec,
                        param,
                        field=field,
                        value=param["source_binding"],
                    )
                    ref = _param_ref(param)
                    step_ref = _step_key(step)
                    for alias in ("source_binding", "source_kind", "source"):
                        _pin(spec, f"field:{step_ref}:{ref}:{alias}")
                    if (
                        prior_source_kind in _ENUM_SOURCE_KINDS
                        and prior_source_kind != source_kind
                    ):
                        _clear_enum_projection(spec, step, param)
                else:
                    param[field] = edit.get("value")
                    if _axis_for_edit(field) is FieldDimension.BUSINESS_TYPE:
                        param["business_type"] = edit.get("value")
                        param["type"] = edit.get("value")
                    _sync_manual_axis(
                        spec,
                        param,
                        field=field,
                        value=edit.get("value"),
                    )
                    _pin(spec, f"field:{_step_key(step)}:{_param_ref(param)}:{field}")
            else:
                step[field] = edit.get("value")
                _pin(spec, f"step:{_step_key(step)}:{field}")
        elif op == "remove" and edit.get("link_id"):
            link_id = str(edit["link_id"])
            spec["links"] = [item for item in _items(spec, "links") if str(item.get("link_id")) != link_id]
            _pin(spec, f"link:{link_id}:__removed__")
        elif op == "remove":
            step = _find_edit_step(spec, edit)
            param = _find_param(step, edit, require_uuid=_is_v3(spec))
            ref = _param_ref(param)
            step["params"] = [item for item in step.get("params") or [] if item is not param]
            _pin(spec, f"field:{_step_key(step)}:{ref}:__removed__")
        elif op == "add" and isinstance(edit.get("param"), dict):
            step = _find_edit_step(spec, edit)
            param = deepcopy(edit["param"])
            _register_manual_param(spec, step, param)
            step.setdefault("params", []).append(param)
            for field, value in tuple(param.items()):
                _sync_manual_axis(spec, param, field=field, value=value)
            _pin(spec, f"field:{_step_key(step)}:{_param_ref(param)}:__added__")
        elif op == "add" and isinstance(edit.get("link"), dict):
            link = deepcopy(edit["link"])
            link.setdefault("link_id", _stable_id("link", link))
            _items(spec, "links").append(link)
            _pin(spec, f"link:{link['link_id']}:__added__")
        elif op == "add_request_step":
            _materialize_request(spec, edit)
        elif op == "remove_step":
            step = _find_edit_step(spec, edit)
            step_id = str(step.get("step_id") or "")
            step_uuid = str(step.get("step_uuid") or "")
            request_id = str(step.get("request_id") or "")
            spec["steps"] = [item for item in _items(spec, "steps") if item is not step]
            spec["links"] = [
                item for item in _items(spec, "links")
                if item.get("source_step_id") != step_id
                and item.get("target_step_id") != step_id
                and item.get("source_step_uuid") != step_uuid
                and item.get("target_step_uuid") != step_uuid
            ]
            for capability in _items(spec, "capabilities"):
                capability["step_ids"] = [value for value in capability.get("step_ids") or [] if value != step_id]
                capability["step_uuids"] = [
                    value
                    for value in capability.get("step_uuids") or []
                    if value != step_uuid
                ]
                capability["request_refs"] = [
                    value
                    for value in capability.get("request_refs") or []
                    if value.get("step_id") != step_id
                    and value.get("step_uuid") != step_uuid
                ]
                capability["confirmed"] = False
            _set_request_materialization(spec, request_id, step_id=None)
            _pin(spec, f"step:{step_uuid or step_id}:__removed__")
        elif op == "reorder_steps":
            use_uuids = bool(edit.get("step_uuids"))
            if _is_v3(spec) and not use_uuids:
                raise DecisionCommandError(
                    "recording V3 step reordering requires step_uuids"
                )
            wanted = [
                str(value)
                for value in (
                    edit.get("step_uuids") if use_uuids else edit.get("step_ids")
                )
                or []
            ]
            by_id = {
                str(item.get("step_uuid") if use_uuids else item.get("step_id")): item
                for item in _items(spec, "steps")
            }
            if len(wanted) != len(by_id) or set(wanted) != set(by_id):
                raise DecisionCommandError("reorder_steps must contain every step exactly once")
            spec["steps"] = [by_id[value] for value in wanted]
            _pin(spec, "flow:step_order")
        elif op == "dedupe_steps":
            _dedupe(spec)
            _pin(spec, "flow:dedupe_steps")
        elif op == "resolve_review":
            review_id = str(edit.get("review_id") or "")
            requested_fingerprint = str(edit.get("fingerprint") or "")
            requested_kind = str(edit.get("issue_kind") or "")
            if not review_id or not requested_fingerprint:
                raise DecisionCommandError(
                    "resolve_review requires the current issue id and fingerprint"
                )
            stored_reviews = _items(spec, "review_items")
            stored_review = next(
                (
                    item for item in stored_reviews
                    if (not review_id or str(item.get("id") or item.get("issue_id") or "") == review_id)
                    and (not requested_fingerprint or str(item.get("fingerprint") or "") == requested_fingerprint)
                ),
                None,
            )
            recomputed = check_executability(spec)
            server_issues = [
                *list(recomputed.get("contract_faults") or ()),
                *list(recomputed.get("advisories") or ()),
            ]
            review = next(
                (
                    item for item in server_issues
                    if (not review_id or str(item.get("id") or item.get("issue_id") or "") == review_id)
                    and (not requested_fingerprint or str(item.get("fingerprint") or "") == requested_fingerprint)
                ),
                None,
            )
            if review is None:
                raise DecisionCommandError(
                    f"review item not found: {review_id or requested_fingerprint}"
                )
            actual_kind = str(review.get("kind") or review.get("type") or "")
            if requested_kind and actual_kind and requested_kind != actual_kind:
                raise DecisionCommandError("review kind does not match server issue")
            resolved = bool(edit.get("resolved", True))
            if resolved and actual_kind == "contract_fault":
                raise DecisionCommandError("ContractFault cannot be ignored; fix its concrete contract")
            if resolved and actual_kind != "advisory":
                raise DecisionCommandError("only Advisory issues can be ignored")
            if stored_review is not None:
                stored_review["resolved"] = resolved
            fingerprint = str(review.get("fingerprint") or "")
            if fingerprint and actual_kind == "advisory":
                ignored = spec.setdefault("meta", {}).setdefault("ignored_advisory_fingerprints", [])
                if resolved and fingerprint not in ignored:
                    ignored.append(fingerprint)
                elif not resolved:
                    spec["meta"]["ignored_advisory_fingerprints"] = [
                        value for value in ignored if str(value) != fingerprint
                    ]
            _pin(spec, f"review:{review_id or fingerprint}:resolved")
        elif op == "add_capability":
            capability = deepcopy(edit.get("capability") or {})
            if int(spec.get("recording_contract_version") or 0) >= 1:
                capability_uuid = _validated_uuid(
                    capability.get("capability_uuid") or capability.get("capability_id"),
                    label="capability_uuid",
                    generate=True,
                )
                capability["capability_uuid"] = capability_uuid
                capability.setdefault("capability_id", capability_uuid)
            capability.setdefault("capability_id", _stable_id("capability", capability))
            _items(spec, "capabilities").append(capability)
            _pin(spec, f"capability:{capability['capability_id']}:__added__")
        elif op == "remove_capability":
            index, capability = _capability(spec, edit)
            _items(spec, "capabilities").pop(index)
            _pin(spec, f"capability:{_cap_ref(capability, index)}:__removed__")
        elif op == "update_capability":
            index, capability = _capability(spec, edit)
            field = str(edit.get("field") or "")
            capability[field] = edit.get("value")
            _pin(spec, f"capability:{_cap_ref(capability, index)}:{field}")
        elif op in {"add_capability_step", "remove_capability_step"}:
            index, capability = _capability(spec, edit)
            step_ref = str(edit.get("step_uuid") or edit.get("step_id") or "")
            if not step_ref and op == "add_capability_step":
                step = _materialize_request(spec, edit)
            else:
                step = _find_edit_step(spec, edit)
            step_id = str(step.get("step_id") or "")
            step_uuid = str(step.get("step_uuid") or step_id)
            step_ids = list(capability.get("step_ids") or [])
            step_uuids = list(capability.get("step_uuids") or [])
            refs = [dict(value) for value in capability.get("request_refs") or []]
            if op == "add_capability_step":
                usage = str(edit.get("usage") or "execute")
                if usage != "option_source" and step_id not in step_ids:
                    step_ids.append(step_id)
                if usage != "option_source" and step_uuid not in step_uuids:
                    step_uuids.append(step_uuid)
                refs = [
                    value
                    for value in refs
                    if value.get("step_id") != step_id
                    and value.get("step_uuid") != step_uuid
                ]
                refs.append({
                    "step_id": step_id,
                    "step_uuid": step_uuid,
                    "usage": usage,
                    "origin": "manual",
                    "pinned": True,
                    "confirmed": bool(edit.get("confirmed", True)),
                })
            else:
                step_ids = [value for value in step_ids if value != step_id]
                step_uuids = [value for value in step_uuids if value != step_uuid]
                refs = [
                    value
                    for value in refs
                    if value.get("step_id") != step_id
                    and value.get("step_uuid") != step_uuid
                ]
            capability.update({
                "step_ids": step_ids,
                "step_uuids": step_uuids,
                "request_refs": refs,
                "confirmed": False,
            })
            _pin(spec, f"capability:{_cap_ref(capability, index)}:membership:{step_uuid}")
        elif op == "reorder_capabilities":
            refs = [str(value) for value in edit.get("capability_refs") or []]
            capabilities = _items(spec, "capabilities")
            by_ref = {_cap_ref(item, index): item for index, item in enumerate(capabilities)}
            if (
                int(spec.get("recording_contract_version") or 0) < 1
                and set(refs) != set(by_ref)
            ):
                # Name is the UI's preferred reference, so also accept it when id exists.
                by_ref = {str(item.get("name") or _cap_ref(item, index)): item for index, item in enumerate(capabilities)}
            if set(refs) != set(by_ref):
                raise DecisionCommandError("reorder_capabilities contains unknown or duplicate refs")
            spec["capabilities"] = [by_ref[value] for value in refs]
            _pin(spec, "flow:capability_order")
        else:
            raise DecisionCommandError(f"unsupported flow edit: {op}")
    validate_workbench(spec, raise_on_error=True)
    return spec


def apply_replacement(current: dict[str, Any], replacement: dict[str, Any]) -> dict[str, Any]:
    """Apply the JSON editor as a user decision while retaining server-owned evidence."""

    if not isinstance(replacement, dict):
        raise DecisionCommandError("flow replacement must be an object")
    next_spec = deepcopy(replacement)
    server_owned = {
        "tenant",
        "recording_id",
        "request_facts",
        "lineage_id",
        "capture_generation",
        "recording_contract_version",
        "capture_store",
        "field_registry",
        "value_evidence",
        "field_evidence",
        "enum_evidence",
        "enum_suggestions",
        "evidence_graph",
        "evidence_graph_summary",
        "migration_issues",
    }
    for key in server_owned:
        if key in current:
            next_spec[key] = deepcopy(current[key])
        else:
            next_spec.pop(key, None)
    server_meta = deepcopy(current.get("meta") or {})
    client_meta = next_spec.get("meta") if isinstance(next_spec.get("meta"), dict) else {}
    next_spec["meta"] = {**client_meta, **server_meta, "recording_engine": "playwright_v3"}
    next_spec["revision"] = int(current.get("revision") or 0)
    pins = _pins(next_spec)
    for field in set(next_spec) - {*server_owned, "meta", "revision"}:
        pins[f"flow:{field}"] = "user"
    pins["flow:json_replacement"] = "user"
    v3 = int(next_spec.get("recording_contract_version") or 0) >= 1
    current_steps_by_id = {
        str(item.get("step_id") or ""): item for item in current.get("steps") or []
    }
    current_steps = {
        _step_key(item): item for item in current.get("steps") or []
    }
    next_steps: dict[str, dict[str, Any]] = {}
    for step in next_spec.get("steps") or []:
        if not isinstance(step, dict):
            raise DecisionCommandError("step must be an object")
        step_id = str(step.get("step_id") or "")
        prior: dict[str, Any] | None = None
        if v3:
            supplied_uuid = str(step.get("step_uuid") or "").strip()
            if supplied_uuid:
                supplied_uuid = _validated_uuid(
                    supplied_uuid, label="step_uuid"
                )
                step["step_uuid"] = supplied_uuid
                prior = current_steps.get(supplied_uuid)
                prior_by_id = current_steps_by_id.get(step_id)
                if prior is None and prior_by_id is not None:
                    raise DecisionCommandError(
                        "supplied step_uuid does not match the existing step"
                    )
            else:
                prior = current_steps_by_id.get(step_id)
                step["step_uuid"] = (
                    _step_key(prior)
                    if prior is not None
                    else _validated_uuid(None, label="step_uuid", generate=True)
                )
            step_ref = str(step["step_uuid"])
        else:
            prior = current_steps_by_id.get(step_id)
            step_ref = step_id
        if not step_ref or step_ref in next_steps:
            raise DecisionCommandError("every step requires a unique permanent identity")
        next_steps[step_ref] = step
        if prior is not None:
            # Immutable evidence identity never comes back from the JSON editor.
            for field in (
                "request_id",
                "request_definition_id",
                "observation_id",
                "step_uuid",
                "source_meta",
            ):
                if field in prior:
                    step[field] = deepcopy(prior[field])
        else:
            pins[f"step:{step_ref}:__added__"] = "user"
        for field in step:
            if field != "params":
                pins[f"step:{step_ref}:{field}"] = "user"
        prior_params = list((prior or {}).get("params") or [])
        prior_params_by_uuid = {
            str(item.get("field_uuid") or item.get("field_id") or ""): item
            for item in prior_params
            if item.get("field_uuid") or item.get("field_id")
        }
        for param in step.get("params") or []:
            if not isinstance(param, dict):
                raise DecisionCommandError("parameter must be an object")
            supplied_field_uuid = str(
                param.get("field_uuid") or param.get("field_id") or ""
            ).strip()
            prior_param = None
            legacy_match = next(
                (
                    item for item in prior_params
                    if (
                        param.get("path")
                        and str(item.get("path") or "") == str(param.get("path"))
                    ) or (
                        param.get("key")
                        and str(item.get("key") or "") == str(param.get("key"))
                    )
                ),
                None,
            )
            if v3 and supplied_field_uuid:
                supplied_field_uuid = _validated_uuid(
                    supplied_field_uuid, label="field_uuid"
                )
                param["field_uuid"] = supplied_field_uuid
                param["field_id"] = supplied_field_uuid
                prior_param = prior_params_by_uuid.get(supplied_field_uuid)
                if prior_param is None and legacy_match is not None:
                    raise DecisionCommandError(
                        "supplied field_uuid does not match the existing field"
                    )
            elif v3 and legacy_match is not None:
                prior_param = legacy_match
                param["field_uuid"] = str(
                    prior_param.get("field_uuid") or prior_param.get("field_id")
                )
                param["field_id"] = param["field_uuid"]
            elif not v3:
                prior_param = legacy_match
            if prior_param is None:
                _register_manual_param(next_spec, step, param)
            else:
                for immutable in (
                    "field_uuid",
                    "field_id",
                    "field_contract_id",
                    "lineage_id",
                    "aliases",
                    "wire_bindings",
                    "step_uuid",
                    "request_id",
                ):
                    if immutable in prior_param:
                        param[immutable] = deepcopy(prior_param[immutable])
                param["axis_decisions"] = deepcopy(
                    prior_param.get("axis_decisions") or {}
                )
            for editable, value in tuple(param.items()):
                if _axis_for_edit(editable) is None:
                    continue
                if prior_param is None or value != prior_param.get(editable):
                    _sync_manual_axis(
                        next_spec,
                        param,
                        field=editable,
                        value=value,
                    )
            ref = _param_ref(param)
            if prior_param is None:
                pins[f"field:{step_ref}:{ref}:__added__"] = "user"
            for field in param:
                pins[f"field:{step_ref}:{ref}:{field}"] = "user"
        _set_request_materialization(
            next_spec,
            str(step.get("request_id") or ""),
            step_id=step_id,
        )
    for step_ref, step in current_steps.items():
        if step_ref not in next_steps:
            pins[f"step:{step_ref}:__removed__"] = "user"
            _set_request_materialization(
                next_spec,
                str(step.get("request_id") or ""),
                step_id=None,
            )

    if v3:
        current_capabilities = {
            str(item.get("capability_uuid") or ""): item
            for item in current.get("capabilities") or []
            if item.get("capability_uuid")
        }
        current_caps_by_legacy = {
            str(item.get("capability_id") or item.get("name") or ""): item
            for item in current.get("capabilities") or []
        }
        seen_capabilities: set[str] = set()
        for capability in next_spec.get("capabilities") or []:
            if not isinstance(capability, dict):
                raise DecisionCommandError("capability must be an object")
            supplied = str(capability.get("capability_uuid") or "").strip()
            legacy_ref = str(
                capability.get("capability_id") or capability.get("name") or ""
            )
            if supplied:
                supplied = _validated_uuid(supplied, label="capability_uuid")
                if supplied not in current_capabilities and legacy_ref in current_caps_by_legacy:
                    raise DecisionCommandError(
                        "supplied capability_uuid does not match the existing capability"
                    )
            else:
                prior_capability = current_caps_by_legacy.get(legacy_ref)
                supplied = (
                    str(prior_capability.get("capability_uuid"))
                    if prior_capability is not None
                    else _validated_uuid(
                        None, label="capability_uuid", generate=True
                    )
                )
            if supplied in seen_capabilities:
                raise DecisionCommandError("duplicate capability_uuid")
            seen_capabilities.add(supplied)
            capability["capability_uuid"] = supplied
            capability.setdefault("capability_id", supplied)

    for key, prefix, identity in (
        ("links", "link", lambda item, _index: str(item.get("link_id") or "")),
        ("capabilities", "capability", _cap_ref),
    ):
        current_items = {identity(item, index): item for index, item in enumerate(current.get(key) or [])}
        next_items = {identity(item, index): item for index, item in enumerate(next_spec.get(key) or [])}
        for ref, item in next_items.items():
            if ref not in current_items:
                pins[f"{prefix}:{ref}:__added__"] = "user"
            for field in item:
                pins[f"{prefix}:{ref}:{field}"] = "user"
        for ref in set(current_items) - set(next_items):
            pins[f"{prefix}:{ref}:__removed__"] = "user"
    validate_workbench(next_spec, raise_on_error=True)
    return next_spec


def _is_pinned(spec: dict[str, Any], path: str) -> bool:
    return _pins(spec).get(path) == "user"


def _merge_fields(current: dict[str, Any], candidate: dict[str, Any], prefix: str) -> None:
    for key, value in candidate.items():
        if key.startswith("_") or _is_pinned(current, f"{prefix}:{key}"):
            continue
        current[key] = deepcopy(value)


def merge_pi_submission(snapshot: dict[str, Any], submission: dict[str, Any]) -> dict[str, Any]:
    """Merge a bounded Pi proposal without deleting evidence or user-owned dimensions."""

    # All live Pi tooling uses this branch.  The legacy object-shaped merge
    # below remains only as an internal migration adapter for already persisted
    # V3 turns; it is no longer exposed by the Pi sidecar.
    if is_semantic_operation_submission(submission):
        spec = apply_pi_semantic_operations(snapshot, submission)
        validate_workbench(spec, raise_on_error=True)
        return spec

    spec = deepcopy(snapshot)
    candidate: Any = submission.get("full_spec") or submission.get("flow_spec") or submission.get("plan") or submission
    if not isinstance(candidate, dict):
        raise DecisionCommandError("Pi submission must contain an object plan")
    for field in ("title", "business_description", "action", "goal", "risk_level"):
        if field in candidate and not _is_pinned(spec, f"flow:{field}"):
            spec[field] = deepcopy(candidate[field])

    current_steps = {str(item.get("step_id") or item.get("request_id") or ""): item for item in _items(spec, "steps")}
    request_ids = {str(item.get("request_id") or "") for item in _request_rows(spec)}
    for proposed in candidate.get("steps") or []:
        if not isinstance(proposed, dict):
            continue
        identity = str(proposed.get("step_id") or proposed.get("request_id") or "")
        existing = current_steps.get(identity)
        if existing is None:
            request_id = str(proposed.get("request_id") or "")
            if not request_id or request_id not in request_ids:
                continue
            if any(_is_pinned(spec, f"step:{value}:__removed__") for value in {identity, proposed.get("step_id")} if value):
                continue
            existing = _materialize_request(spec, {"request_id": request_id})
        step_id = str(existing.get("step_id") or "")
        for key, value in proposed.items():
            if key in {"params", "request_id", "step_id"} or _is_pinned(spec, f"step:{step_id}:{key}"):
                continue
            existing[key] = deepcopy(value)
        params = existing.setdefault("params", [])
        for proposed_param in proposed.get("params") or []:
            if not isinstance(proposed_param, dict):
                continue
            match = next((item for item in params if _param_ref(item) and _param_ref(item) in {
                _param_ref(proposed_param), str(proposed_param.get("path") or ""), str(proposed_param.get("key") or "")
            }), None)
            if match is None:
                continue
            field_ref = _param_ref(match)
            for key, value in proposed_param.items():
                if not _is_pinned(spec, f"field:{step_id}:{field_ref}:{key}"):
                    match[key] = deepcopy(value)

    capabilities = _items(spec, "capabilities")
    step_ids = {str(item.get("step_id") or "") for item in _items(spec, "steps")}
    for proposed in candidate.get("capabilities") or []:
        if not isinstance(proposed, dict):
            continue
        ref = str(proposed.get("capability_id") or proposed.get("name") or "")
        existing_index = next((i for i, item in enumerate(capabilities) if ref in {
            str(item.get("capability_id") or ""), str(item.get("name") or "")
        }), None)
        if existing_index is None:
            if _is_pinned(spec, f"capability:{ref}:__removed__"):
                continue
            proposed_steps = set(proposed.get("step_ids") or [])
            if not proposed_steps.issubset(step_ids):
                continue
            item = deepcopy(proposed)
            item.setdefault("capability_id", _stable_id("capability", item))
            capabilities.append(item)
            continue
        existing = capabilities[existing_index]
        cap_ref = _cap_ref(existing, existing_index)
        _merge_fields(existing, proposed, f"capability:{cap_ref}")

    links = _items(spec, "links")
    by_link = {str(item.get("link_id") or ""): item for item in links}
    for proposed in candidate.get("links") or []:
        if not isinstance(proposed, dict):
            continue
        link_id = str(proposed.get("link_id") or _stable_id("link", proposed))
        if _is_pinned(spec, f"link:{link_id}:__removed__"):
            continue
        existing = by_link.get(link_id)
        if existing is not None:
            _merge_fields(existing, proposed, f"link:{link_id}")
        elif {proposed.get("source_step_id"), proposed.get("target_step_id")}.issubset(step_ids):
            item = {"link_id": link_id, **deepcopy(proposed)}
            links.append(item)
            by_link[link_id] = item

    # A planner can also submit compact per-field decisions without repeating steps.
    for decision in candidate.get("field_decisions") or []:
        if not isinstance(decision, dict):
            continue
        try:
            step = _find_step(spec, str(decision.get("step_id") or ""))
            param = _find_param(step, {
                "param_path": decision.get("path"),
                "param_key": decision.get("key"),
                "param_label": decision.get("label"),
            })
        except DecisionCommandError:
            continue
        ref = _param_ref(param)
        for key, value in (decision.get("dimensions") or {}).items():
            if not _is_pinned(spec, f"field:{step.get('step_id')}:{ref}:{key}"):
                param[str(key)] = deepcopy(value)

    spec.setdefault("meta", {})["pi_last_submission"] = {
        "kind": str(submission.get("kind") or "plan"),
        "summary": str(submission.get("summary") or ""),
    }
    validate_workbench(spec, raise_on_error=True)
    return spec


def rebase_user_decisions(
    previous: dict[str, Any] | None,
    deterministic: dict[str, Any],
) -> dict[str, Any]:
    """Reapply user pins when new immutable facts are deterministically compiled."""

    if not previous:
        return deepcopy(deterministic)
    result = deepcopy(deterministic)
    prior = deepcopy(previous)
    pins = deepcopy(_pins(prior))
    result.setdefault("meta", {})["decision_origins"] = pins
    for path, owner in pins.items():
        if owner != "user":
            continue
        parts = path.split(":")
        if len(parts) == 2 and parts[0] == "flow" and parts[1] in prior:
            if parts[1] not in {"json_replacement", "step_order", "capability_order", "dedupe_steps"}:
                result[parts[1]] = deepcopy(prior[parts[1]])

    prior_steps = {_step_key(item): item for item in prior.get("steps") or []}
    result_steps = {_step_key(item): item for item in result.get("steps") or []}

    def step_pin(step: dict[str, Any], suffix: str) -> bool:
        return any(
            _is_pinned(prior, f"step:{ref}:{suffix}")
            for ref in {_step_key(step), str(step.get("step_id") or "")}
            if ref
        )

    def field_pin(step: dict[str, Any], ref: str, suffix: str) -> bool:
        return any(
            _is_pinned(prior, f"field:{step_ref}:{ref}:{suffix}")
            for step_ref in {_step_key(step), str(step.get("step_id") or "")}
            if step_ref
        )
    # A removed object is intentionally absent from the previous projection, so
    # apply its tombstone against the freshly compiled deterministic object first.
    for step_ref, step in tuple(result_steps.items()):
        if step_pin(step, "__removed__"):
            result_steps.pop(step_ref, None)
            _set_request_materialization(
                result,
                str(step.get("request_id") or ""),
                step_id=None,
            )
    for step_ref, step in prior_steps.items():
        if step_pin(step, "__removed__"):
            result_steps.pop(step_ref, None)
            _set_request_materialization(
                result,
                str(step.get("request_id") or ""),
                step_id=None,
            )
            continue
        if step_pin(step, "__added__") and step_ref not in result_steps:
            result_steps[step_ref] = deepcopy(step)
            _set_request_materialization(
                result,
                str(step.get("request_id") or ""),
                step_id=str(step.get("step_id") or ""),
            )
        target = result_steps.get(step_ref)
        if target is None:
            continue
        for key, value in step.items():
            if step_pin(step, key):
                target[key] = deepcopy(value)
        prior_params = {_param_ref(item): item for item in step.get("params") or []}
        target_params = {_param_ref(item): item for item in target.get("params") or []}
        for ref, param in prior_params.items():
            if field_pin(step, ref, "__removed__"):
                target_params.pop(ref, None)
                continue
            if field_pin(step, ref, "__added__") and ref not in target_params:
                target_params[ref] = deepcopy(param)
            current = target_params.get(ref)
            if current is None:
                continue
            for key, value in param.items():
                if field_pin(step, ref, key):
                    current[key] = deepcopy(value)
        target["params"] = list(target_params.values())
    natural_order = [_step_key(item) for item in result.get("steps") or []]
    if _is_pinned(prior, "flow:step_order"):
        preferred = [_step_key(item) for item in prior.get("steps") or []]
        order = [value for value in preferred if value in result_steps]
        order.extend(value for value in natural_order if value not in order and value in result_steps)
    else:
        order = [value for value in natural_order if value in result_steps]
        order.extend(value for value in result_steps if value not in order)
    result["steps"] = [result_steps[value] for value in order]

    def rebase_collection(key: str, prefix: str, identity) -> None:  # noqa: ANN001
        current_items = list(result.get(key) or [])
        by_id = {identity(item, index): item for index, item in enumerate(current_items)}
        for ref in tuple(by_id):
            if _is_pinned(prior, f"{prefix}:{ref}:__removed__"):
                by_id.pop(ref, None)
        for index, item in enumerate(prior.get(key) or []):
            ref = identity(item, index)
            if _is_pinned(prior, f"{prefix}:{ref}:__removed__"):
                by_id.pop(ref, None)
                continue
            if _is_pinned(prior, f"{prefix}:{ref}:__added__") and ref not in by_id:
                by_id[ref] = deepcopy(item)
            target = by_id.get(ref)
            if target is None:
                continue
            for field, value in item.items():
                if _is_pinned(prior, f"{prefix}:{ref}:{field}") or (
                    prefix == "capability" and any(
                        pin.startswith(f"capability:{ref}:membership:") for pin in pins
                    ) and field in {"step_ids", "step_uuids", "request_refs"}
                ):
                    target[field] = deepcopy(value)
        result[key] = list(by_id.values())

    rebase_collection("links", "link", lambda item, _index: str(item.get("link_id") or ""))
    rebase_collection("capabilities", "capability", _cap_ref)

    # A removed step stays absent even when later collection-level pins are
    # replayed. Keep the editable graph closed over the surviving steps while
    # the immutable request remains in request_facts as a review candidate.
    surviving_step_ids = {
        str(item.get("step_id") or "")
        for item in result.get("steps") or []
        if isinstance(item, dict) and item.get("step_id")
    }
    surviving_step_uuids = {
        str(item.get("step_uuid") or "")
        for item in result.get("steps") or []
        if isinstance(item, dict) and item.get("step_uuid")
    }
    step_id_by_uuid = {
        str(item.get("step_uuid")): str(item.get("step_id") or "")
        for item in result.get("steps") or []
        if isinstance(item, dict) and item.get("step_uuid")
    }
    for link in result.get("links") or []:
        if not isinstance(link, dict):
            continue
        source_uuid = str(link.get("source_step_uuid") or "")
        target_uuid = str(link.get("target_step_uuid") or "")
        if source_uuid in step_id_by_uuid:
            link["source_step_id"] = step_id_by_uuid[source_uuid]
        if target_uuid in step_id_by_uuid:
            link["target_step_id"] = step_id_by_uuid[target_uuid]
    result["links"] = [
        link
        for link in result.get("links") or []
        if isinstance(link, dict)
        and str(link.get("source_step_id") or "") in surviving_step_ids
        and str(link.get("target_step_id") or "") in surviving_step_ids
    ]
    for capability in result.get("capabilities") or []:
        if not isinstance(capability, dict):
            continue
        prior_step_ids = list(capability.get("step_ids") or [])
        prior_step_uuids = list(capability.get("step_uuids") or [])
        prior_refs = list(capability.get("request_refs") or [])
        capability["step_ids"] = [
            step_id for step_id in prior_step_ids if str(step_id) in surviving_step_ids
        ]
        capability["step_uuids"] = [
            step_uuid
            for step_uuid in prior_step_uuids
            if str(step_uuid) in surviving_step_uuids
        ]
        capability["request_refs"] = [
            request_ref
            for request_ref in prior_refs
            if isinstance(request_ref, dict)
            and (
                not request_ref.get("step_id")
                or str(request_ref.get("step_id")) in surviving_step_ids
                or str(request_ref.get("step_uuid") or "") in surviving_step_uuids
            )
        ]
        if (
            capability["step_ids"] != prior_step_ids
            or capability["step_uuids"] != prior_step_uuids
            or capability["request_refs"] != prior_refs
        ):
            capability["confirmed"] = False
    if _is_pinned(prior, "flow:capability_order"):
        by_ref = {_cap_ref(item, index): item for index, item in enumerate(result.get("capabilities") or [])}
        preferred = [_cap_ref(item, index) for index, item in enumerate(prior.get("capabilities") or [])]
        ordered = [by_ref.pop(value) for value in preferred if value in by_ref]
        ordered.extend(by_ref.values())
        result["capabilities"] = ordered
    validate_workbench(result, raise_on_error=True)
    return result


def validate_workbench(spec: dict[str, Any], *, raise_on_error: bool = False) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    def add(code: str, message: str, severity: str = "high", target: dict[str, Any] | None = None) -> None:
        issues.append({
            "id": f"v3:{code}:{len(issues)}",
            "type": code,
            "severity": severity,
            "title": code.replace("_", " "),
            "reason": message,
            "resolved": False,
            "target": target or {"kind": "flow"},
        })

    steps = spec.get("steps")
    capabilities = spec.get("capabilities")
    links = spec.get("links")
    if not isinstance(steps, list):
        add("invalid_steps", "steps must be a list")
        steps = []
    if not isinstance(capabilities, list):
        add("invalid_capabilities", "capabilities must be a list")
        capabilities = []
    if not isinstance(links, list):
        add("invalid_links", "links must be a list")
        links = []
    step_ids = [str(item.get("step_id") or "") for item in steps if isinstance(item, dict)]
    if "" in step_ids or len(step_ids) != len(set(step_ids)):
        add("invalid_step_identity", "every step requires a unique step_id")
    valid_ids = set(step_ids)
    for step in steps:
        if not isinstance(step, dict):
            add("invalid_step", "step must be an object")
            continue
        method = str(step.get("method") or "").upper()
        if not method or not (step.get("path") or step.get("url")):
            add("incomplete_step", "step requires method and path/url", target={"kind": "step", "step_id": step.get("step_id")})
        governed_by_capability = any(
            step.get("step_id") in (capability.get("step_ids") or [])
            and (
                capability.get("requires_human_confirm")
                or capability.get("requires_confirmation")
                or capability.get("risk_level") in {"L3", "L4", "L5"}
            )
            for capability in capabilities if isinstance(capability, dict)
        )
        if method in {"POST", "PUT", "PATCH", "DELETE"} and not (
            step.get("requires_human_confirm")
            or step.get("risk_level") in {"L3", "L4", "L5"}
            or governed_by_capability
        ):
            add("unsafe_write", "write request requires risk/confirmation policy", target={"kind": "step", "step_id": step.get("step_id")})
    for link in links:
        if not isinstance(link, dict) or link.get("source_step_id") not in valid_ids or link.get("target_step_id") not in valid_ids:
            add("dangling_link", "link references a missing step", target={"kind": "link", "link_id": link.get("link_id") if isinstance(link, dict) else None})
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, dict):
            add("invalid_capability", "capability must be an object")
            continue
        unknown = set(capability.get("step_ids") or []) - valid_ids
        if unknown:
            add("dangling_capability", f"capability references missing steps: {sorted(unknown)}", target={"kind": "capability", "capability_index": index})
    rows: list[dict[str, Any]] = []
    try:
        rows = _request_rows(spec)
    except DecisionCommandError as exc:
        add("invalid_request_facts", str(exc))
    request_step_ids = {str(item.get("request_id") or "") for item in steps if isinstance(item, dict)}
    allowed = {"materialized", "supporting", "option_source", "identity", "preflight", "review_candidate", "unsupported", "ignored_resource"}
    for row in rows:
        disposition = str(row.get("disposition") or row.get("role") or "")
        if disposition not in allowed:
            add("missing_disposition", f"captured request {row.get('request_id')} lacks a valid disposition", target={"kind": "request", "request_id": row.get("request_id")})
        if disposition == "materialized" and str(row.get("request_id") or "") not in request_step_ids:
            add("missing_materialization", f"materialized request {row.get('request_id')} has no step", target={"kind": "request", "request_id": row.get("request_id")})
    errors = [item for item in issues if item["severity"] == "high"]
    report = {
        "passed": not errors,
        "issues": issues,
        "review_items": issues,
        "errors": [item["reason"] for item in errors],
        "warnings": [item["reason"] for item in issues if item["severity"] != "high"],
        "summary": {"steps": len(steps), "capabilities": len(capabilities), "requests": len(rows)},
    }
    executability = check_executability(spec)
    report.update({
        "executability_status": executability["executability_status"],
        "direct_call_enabled": executability["direct_call_enabled"],
        "contract_faults": executability["contract_faults"],
        "advisories": executability["advisories"],
        "contract_fault_count": executability["contract_fault_count"],
        "advisory_count": executability["advisory_count"],
    })
    # Compatibility list for the unchanged workbench.  Its consumers can now
    # distinguish entries by ``kind`` without receiving new controls/buttons.
    report["review_items"] = issues + executability["contract_faults"] + executability["visible_advisories"]
    if raise_on_error and errors:
        raise DecisionCommandError("; ".join(report["errors"]))
    return report
