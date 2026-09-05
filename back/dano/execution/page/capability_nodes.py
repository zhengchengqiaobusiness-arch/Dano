"""Stage 6: capability nodes, upserts, and scoped graph edits."""
from __future__ import annotations

from typing import Any
import copy
from dano.execution.page.flow_spec_core.models import (
    CapabilityDependency,
    CapabilityField,
    CapabilityRelation,
    CapabilityRequestRef,
    FlowCapability,
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.request_capture import (
    normalized_leaf_paths,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _SCREENSHOT_INTERNAL_SOURCE_KINDS,
    _grounded_control_evidence,
    _grounded_screenshot_query_path,
    _looks_user_entered_business_field,
    _param_axis_manually_edited,
    _param_field_manually_edited,
    _param_has_executable_source,
    _param_has_full_lock,
    _param_has_grounded_public_name,
    _param_has_grounded_type,
    _screenshot_control_evidence,
    _screenshot_control_supports_axis,
)
from dano.execution.page.recording_facts import (
    _SCREENSHOT_OPTION_CONTROL_KINDS,
)
from dano.execution.page.flow_materialization.field_contracts.option_repair import (
    _weak_automatic_text_option_binding,
)


def _invalidate_capabilities_for_steps(spec: FlowSpec, step_ids: set[str]) -> None:
    if not step_ids:
        return
    for cap in spec.capabilities or []:
        if not (set(_capability_node_step_ids(cap)) & step_ids):
            continue
        _invalidate_capability_contract(cap)


def _invalidate_capability_contract(cap: FlowCapability) -> None:
    cap.confirmed = False
    cap.confirmation_hash = ""
    cap.status = "draft"
    cap.requires_human_confirm = True


def _apply_capability_field_to_param(
    spec: FlowSpec,
    raw: dict[str, Any],
    *,
    scope: str,
    actor: str = "user",
) -> bool:
    """Persist a step-bound capability field edit on its canonical ParamField.

    Automated semantic plans may fill unresolved axes, but recorded DOM/API facts
    and operator edits remain authoritative. Capability views must never degrade
    the canonical field contract.
    """
    normalized_actor = str(actor or "user").strip().lower()
    automated = normalized_actor in _AUTOMATED_FIELD_EDIT_ACTORS
    step_id = str(raw.get("step_id") or "")
    path = str(
        raw.get("path") or raw.get("wire_path")
        or (raw.get("key") if not automated else "")
        or ""
    )
    if not step_id or not path:
        return False
    from dano.execution.page.recording_field_identity import FieldRef, FieldReferenceError, resolve_field_ref

    is_step_id = any(item.step_id == step_id for item in spec.steps)
    try:
        resolved = resolve_field_ref(spec, FieldRef(
            step_id=step_id if is_step_id else "",
            request_id="" if is_step_id else step_id,
            wire_path=path,
        ))
        step = resolved.step
        param = resolved.param
        path = resolved.stored_path
    except FieldReferenceError:
        step = next((item for item in spec.steps if item.step_id == step_id), None)
        param = None
    if step is None:
        return False
    if automated:
        if param is None:
            grounded_path = _grounded_screenshot_query_path(step, raw)
            if grounded_path is None:
                return False
            path = grounded_path
            raw = {**raw, "path": path}
            param = next((item for item in step.params if item.path == path), None)
            if param is None:
                control = _screenshot_control_evidence(raw)
                control_kind = str((control or {}).get("control_kind") or "").lower()
                option_control = control_kind in _SCREENSHOT_OPTION_CONTROL_KINDS
                options = (
                    raw.get("enum_options")
                    if isinstance(raw.get("enum_options"), list)
                    else (control or {}).get("options")
                )
                source_kind = "form_option" if option_control and not options else (
                    "page_enum" if option_control else "user_input"
                )
                raw = {
                    **raw,
                    "source_kind": source_kind,
                    "source": {
                        "kind": source_kind, "path": path,
                        **({"enum_confirmed": False} if option_control else {}),
                    },
                    **({"enum_options": options} if isinstance(options, list) and options else {}),
                }
                param = ParamField(
                    path=path,
                    key=str(raw.get("key") or raw.get("display_name") or path.rsplit(".", 1)[-1]),
                    label=str(raw.get("display_name") or raw.get("key") or ""),
                    value="",
                    type="string",
                    wire_type="string",
                    required=bool((control or {}).get("required") is True),
                    category="user_param",
                    source_kind="unknown",
                    default_value=None,
                    need_human_confirm=bool(option_control and not options),
                    evidence=[{
                        "source": "response_schema_match",
                        "path": next(
                            response_path
                            for response_path in normalized_leaf_paths(step.response_json)
                            if response_path.rsplit(".", 1)[-1].lower()
                            == path.rsplit(".", 1)[-1].lower()
                        ),
                    }],
                )
                step.params.append(param)
    else:
        try:
            param = _find_param(
                step, path,
                param_key=str(raw.get("key") or ""),
                param_label=str(raw.get("display_name") or ""),
            )
        except ValueError:
            return False
    if automated and _param_has_full_lock(param):
        return True

    screenshot_control = _grounded_control_evidence(raw) if automated else None
    screenshot_name_axis = _screenshot_control_supports_axis(screenshot_control, "name")
    screenshot_type_axis = _screenshot_control_supports_axis(screenshot_control, "type")
    screenshot_category_axis = _screenshot_control_supports_axis(
        screenshot_control, "category"
    )
    screenshot_source_axis = _screenshot_control_supports_axis(
        screenshot_control, "source"
    )
    screenshot_required_axis = _screenshot_control_supports_axis(
        screenshot_control, "required"
    )
    allow_name = (
        not automated
        or (
            not _param_axis_manually_edited(param, "key", "label", "name", "display_name")
            and (
                not _param_has_grounded_public_name(param)
                or screenshot_name_axis
            )
        )
    )
    screenshot_editable = bool(
        screenshot_control is not None
        and screenshot_control.get("editable") is not False
        and not screenshot_control.get("disabled")
        and not screenshot_control.get("read_only")
    )
    screenshot_control_kind = str(
        (screenshot_control or {}).get("control_kind") or ""
    ).strip().lower()
    screenshot_option_control = bool(
        screenshot_control_kind in _SCREENSHOT_OPTION_CONTROL_KINDS
        or (
            screenshot_control_kind == "checkbox"
            and (screenshot_control or {}).get("options")
        )
    )
    screenshot_direct_input = bool(
        screenshot_editable and not screenshot_option_control
    )
    screenshot_editable_input = bool(
        screenshot_editable
        and screenshot_source_axis
        and str(raw.get("source_kind") or "") == "user_input"
    )
    screenshot_page_enum = bool(
        screenshot_editable
        and screenshot_source_axis
        and screenshot_option_control
        and str(raw.get("source_kind") or "") in {
            "page_enum", "static_enum", "form_option",
        }
        and param.source_kind != "api_option"
    )
    screenshot_user_category = bool(
        screenshot_editable
        and screenshot_category_axis
        and str(raw.get("category") or "") == "user_param"
    )
    screenshot_safe_internal_source = bool(
        screenshot_control is not None
        and screenshot_source_axis
        and (
            screenshot_control.get("editable") is False
            or screenshot_control.get("disabled")
            or screenshot_control.get("read_only")
        )
        and str(raw.get("source_kind") or "") in _SCREENSHOT_INTERNAL_SOURCE_KINDS
    )
    screenshot_safe_internal_category = bool(
        screenshot_control is not None
        and screenshot_category_axis
        and (
            screenshot_control.get("editable") is False
            or screenshot_control.get("disabled")
            or screenshot_control.get("read_only")
        )
        and str(raw.get("category") or "") in {"runtime_var", "system_const"}
    )
    stale_text_option_recovery = bool(
        automated
        and str(raw.get("source_kind") or "") == "user_input"
        and str(raw.get("type") or "") in {"string", "text", "textarea"}
        and not _param_axis_manually_edited(
            param, "source_kind", "source", "category",
            "exposed_to_user", "exposed_to_caller",
        )
        and _weak_automatic_text_option_binding(param)
    )
    semantic_text_type_recovery = bool(
        automated
        and str(raw.get("type") or "") in {"string", "text", "textarea"}
        and str(param.wire_type or "") == "string"
        and str(param.type or "") not in {"string", "text", "textarea"}
        and str(raw.get("source_kind") or param.source_kind or "") == "user_input"
        and _looks_user_entered_business_field(param.key, param.path)
    )
    allow_type = (
        not automated
        or (
            not _param_field_manually_edited(param, "type")
            and (
                not _param_has_grounded_type(param)
                or screenshot_type_axis
                or stale_text_option_recovery
                or semantic_text_type_recovery
            )
        )
    )
    allow_source = (
        not automated
        or (
            not _param_axis_manually_edited(
                param, "source_kind", "source", "category",
                "exposed_to_user", "exposed_to_caller",
            )
            and (
                str(param.source_kind or "unknown") in {"", "unknown"}
                or (
                    (
                        screenshot_editable_input
                        or screenshot_page_enum
                        or screenshot_safe_internal_source
                    )
                    and not _param_has_executable_source(param)
                )
                or (
                    screenshot_editable_input
                    and screenshot_direct_input
                )
                or stale_text_option_recovery
            )
        )
    )
    # Category answers who supplies the value; source answers where option
    # values come from.  An editable select is a caller input even though its
    # choices still come from a captured API.
    allow_category = (
        not automated
        or (
            not _param_axis_manually_edited(
                param, "category", "exposed_to_user", "exposed_to_caller",
                "source_kind", "source",
            )
            and (
                str(param.category or "unknown") in {"", "unknown"}
                or screenshot_user_category
                or screenshot_safe_internal_category
            )
        )
    )

    if raw.get("key") and allow_name:
        if str(raw["key"]) != param.key:
            _rename_param_public_key(spec, step, param, str(raw["key"]), actor=normalized_actor)
        param.label = str(raw.get("display_name") or raw["key"])
    if raw.get("display_name") and allow_name:
        param.label = str(raw["display_name"])
    if raw.get("type") and allow_type:
        _transition_param_type(param, raw["type"])
    screenshot_required = bool(
        screenshot_control is not None
        and screenshot_required_axis
        and screenshot_control.get("required") is True
    )
    screenshot_optional = bool(
        screenshot_control is not None
        and screenshot_required_axis
        and screenshot_control.get("required") is False
        and screenshot_control.get("required_convention_confirmed") is True
        and screenshot_control.get("label_region_complete") is True
    )
    allow_required = not automated or (
        (screenshot_required or screenshot_optional)
        and not _param_field_manually_edited(param, "required")
    )
    if "required" in raw and allow_required:
        param.required = bool(raw["required"])
    if raw.get("source_kind") and allow_source:
        param.source_kind = str(raw["source_kind"])
    if isinstance(raw.get("source"), dict) and allow_source:
        param.source = dict(raw["source"])
    if (
        (screenshot_direct_input or stale_text_option_recovery)
        and allow_source
        and raw.get("source_kind") == "user_input"
    ):
        param.source = {"kind": "user_input", "path": param.path}
        param.enum_options = None
        param.enum_value_map = None
        step.selects = [
            binding for binding in (step.selects or [])
            if _strip_body_prefix(binding.path or "") != _strip_body_prefix(param.path or "")
        ]
    if screenshot_page_enum and allow_source:
        param.source = {"kind": str(raw.get("source_kind") or "page_enum"), "path": param.path}
        if isinstance(raw.get("enum_options"), list):
            param.enum_options = copy.deepcopy(raw["enum_options"])
            param.enum_value_map = None
    # Screenshot values are observations used for identity matching, not proof
    # of an initial default. Recorded request values may be temporary user input.
    allow_default = not automated
    if "visible_default" in raw and allow_default:
        param.default_value = copy.deepcopy(raw.get("visible_default"))
    if "exposed_to_caller" in raw and (not automated or allow_category):
        param.exposed_to_user = bool(raw["exposed_to_caller"])
    if scope == "input" and allow_category:
        param.category = "user_param"
        param.exposed_to_user = True
    elif scope == "internal" and allow_category:
        param.category = "system_const" if param.source_kind == "constant" else "runtime_var"
        param.exposed_to_user = False
    if not automated and "locked" in raw:
        param.locked = bool(raw.get("locked"))
    if "confirmed" in raw:
        param.need_human_confirm = not bool(raw.get("confirmed"))
    incoming_evidence = [
        evidence for evidence in (raw.get("evidence") or [])
        if isinstance(evidence, dict)
    ]
    if automated and any(
        evidence.get("canonical_screenshot_control") is True
        for evidence in incoming_evidence
    ):
        param.evidence = [
            evidence for evidence in (param.evidence or [])
            if not (
                isinstance(evidence, dict)
                and (
                    evidence.get("canonical_screenshot_control") is True
                    or str(evidence.get("source") or "").strip().lower()
                    in {"screenshot", "reference_screenshot", "uploaded_screenshot"}
                )
            )
        ]
    param.evidence.append({
        "source": "capability_field_edit", "scope": scope, "actor": normalized_actor,
        "applied_axes": {
            "name": bool(allow_name), "type": bool(allow_type),
            "category": bool(allow_category), "source": bool(allow_source),
            "required": bool(allow_required), "default": bool(allow_default),
        },
    })
    for evidence in incoming_evidence:
        param.evidence.append({
            **evidence,
            "source": str(evidence.get("source") or "planner_semantic_evidence"),
        })
    if normalized_actor == "user":
        manual_fields = [
            field for field in ("type", "source_kind", "source", "exposed_to_caller")
            if field in raw
        ]
        if raw.get("key"):
            manual_fields.append("key")
        if raw.get("display_name"):
            manual_fields.append("label")
        if "required" in raw:
            manual_fields.append("required")
        if scope in {"input", "internal"}:
            manual_fields.extend(["category", "exposed_to_user"])
        for field in dict.fromkeys(manual_fields):
            value = (
                param.exposed_to_user if field in {"exposed_to_caller", "exposed_to_user"}
                else getattr(param, field, None)
            )
            param.evidence.append({
                "source": "manual_edit", "field": field, "value": value,
            })
    return True


def _sanitize_capability_nodes(spec: FlowSpec, cap: FlowCapability) -> list[dict[str, Any]]:
    """Remove deterministically stale planner nodes before exposing validation warnings."""
    by_id = {step.step_id: step for step in spec.steps}
    cap_step_ids = set(cap.step_ids or [])
    is_batch = _capability_is_batch(spec, cap)
    batch_schema = _batch_capability_input_schema(
        [by_id[step_id] for step_id in cap.step_ids if step_id in by_id]
    ) if is_batch else {}
    batch_top_inputs = set((batch_schema.get("properties") or {}).keys())
    batch_item_inputs, _batch_item_required = _capability_schema_array_item_props(batch_schema, "entries")

    def clean(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in nodes or []:
            if not isinstance(raw, dict):
                continue
            node = dict(raw)
            node_type = str(node.get("type") or "")
            if node_type == "foreach" and not is_batch:
                # Query/list abilities must never retain a batch loop inferred from a URL containing list/batch.
                children = node.get("steps") if isinstance(node.get("steps"), list) else []
                out.extend(clean(children))
                continue
            for child_key in ("children", "steps", "then", "else", "otherwise"):
                if isinstance(node.get(child_key), list):
                    node[child_key] = clean(node[child_key])
            if node_type == "call":
                step_id = str(node.get("step_id") or "")
                usage = str(node.get("usage") or "")
                if step_id not in cap_step_ids and not (
                    usage in {"option_source", "fact_check"}
                    and (node.get("request_id") or node.get("path"))
                ):
                    continue
            elif node_type == "map":
                source = str(node.get("source") or "")
                target = str(node.get("target") or "")
                if not source or not target:
                    has_children = any(
                        isinstance(node.get(key), list) and node.get(key)
                        for key in ("children", "steps", "then", "else", "otherwise")
                    )
                    if has_children:
                        out.append(node)
                    continue
                if is_batch and source.startswith("input."):
                    # A batch capability exposes one top-level ``entries`` array.
                    # Older Planner output addressed row fields as input.<field>;
                    # migrate that reference only when the derived item schema
                    # proves the field exists, otherwise keep it for validation.
                    suffix = source.split(".", 1)[1]
                    field = suffix.split(".", 1)[0]
                    if field not in batch_top_inputs and field in batch_item_inputs:
                        node["source"] = f"item.{suffix}"
                        source = str(node["source"])
                if not is_batch and source.startswith(("item.", "loop.", "input.entries")):
                    continue
                if "." in target and not target.startswith(("var.", "computed.", "loop.", "item.", "node.", "input.")):
                    step_id, path = target.split(".", 1)
                    if step_id in by_id and not _capability_step_param_exists(by_id[step_id], path):
                        continue
            elif node_type == "return":
                ref = str(node.get("from") or node.get("source") or "")
                if ref and ref not in cap_step_ids and not ref.startswith(("input.", "var.", "node.")):
                    node_ids = {str(item.get("id") or "") for item in _iter_capability_nodes(out)}
                    if ref not in node_ids:
                        continue
            out.append(node)
        return out

    cleaned = clean(cap.nodes or [])
    cap.nodes = cleaned
    _sync_capability_order(spec, cap)
    return cap.nodes


def _default_capability_nodes(
    steps: list[FlowStep], *, kind: str, force_batch: bool = False,
) -> list[dict[str, Any]]:
    if not steps:
        return []
    if kind == "submit_batch" and (force_batch or any(_looks_batch_step(s) for s in steps)):
        read_steps = [s for s in steps[:-1] if not _is_write_step(s)]
        final = steps[-1]
        nodes = [
            {
                "id": f"call_{idx}",
                "type": "call",
                "step_id": st.step_id,
                "method": st.method,
                "path": st.path or st.url,
            }
            for idx, st in enumerate(read_steps, 1)
        ]
        nodes.append({
            "id": "foreach_entries",
            "type": "foreach",
            "items": "input.entries",
            "as": "item",
            "steps": [{
                "id": "call_submit_each",
                "type": "call",
                "step_id": final.step_id,
                "method": final.method,
                "path": final.path or final.url,
            }],
        })
        nodes.append({"id": "return_batch_result", "type": "return", "value": "batch_result"})
        return nodes
    return _capability_call_nodes(steps)


def _materialize_capability_call_nodes_from_membership(
    spec: FlowSpec, cap: FlowCapability,
) -> None:
    """Turn declared request_refs/step_ids into call nodes when the node plan omits them.

    Recording results (and any producer that only writes membership) must still
    compile. Empty nodes are not an instruction to delete the capability.
    """
    from dano.execution.page.capability_refs import _capability_declared_step_ids

    known = {step.step_id for step in spec.steps}
    for step_id in _capability_declared_step_ids(cap):
        if step_id in known:
            _add_step_id_to_capability(spec, cap, step_id)


def _add_step_id_to_capability(spec: FlowSpec, cap: FlowCapability, step_id: str) -> None:
    """Insert one call node in stable captured-step order."""
    if not step_id or step_id in _capability_call_step_ids_from_nodes(cap.nodes or []):
        return
    node = {
        "id": f"call_{len(_capability_call_step_ids_from_nodes(cap.nodes or [])) + 1}",
        "type": "call",
        "step_id": step_id,
    }
    if any(
        item.get("type") not in {"call", "return"}
        for item in (cap.nodes or [])
        if isinstance(item, dict)
    ):
        return_index = next(
            (
                index for index, item in enumerate(cap.nodes)
                if isinstance(item, dict) and item.get("type") == "return"
            ),
            len(cap.nodes),
        )
        cap.nodes.insert(return_index, node)
        return

    order = {step.step_id: index for index, step in enumerate(spec.steps)}
    new_order = order.get(step_id, 10_000)
    insert_at = next(
        (
            index for index, item in enumerate(cap.nodes or [])
            if (
                isinstance(item, dict)
                and item.get("type") == "call"
                and order.get(str(item.get("step_id") or ""), 10_000) > new_order
            )
        ),
        next(
            (
                index for index, item in enumerate(cap.nodes or [])
                if isinstance(item, dict) and item.get("type") == "return"
            ),
            len(cap.nodes or []),
        ),
    )
    cap.nodes.insert(insert_at, node)


def _set_capability_request_membership(
    spec: FlowSpec,
    cap: FlowCapability,
    step: FlowStep,
    *,
    usage: str,
    origin: str,
    extra_fields: dict[str, Any] | None = None,
) -> CapabilityRequestRef:
    current = next((ref for ref in (cap.request_refs or []) if ref.step_id == step.step_id), None)
    ref = _capability_request_ref_from_step(spec, step, current, extra=extra_fields)
    ref.usage = usage if usage in {"execute", "option_source", "fact_check", "preflight"} else "execute"
    ref.origin = origin or "manual"
    ref.confirmed = ref.origin in {"manual", "user"}
    cap.request_refs = [item for item in (cap.request_refs or []) if item.step_id != step.step_id]
    cap.request_refs.append(ref)
    return ref


def _normalize_capability_relation_semantics(relation: CapabilityRelation) -> CapabilityRelation:
    """Resolve legacy type/mode defaults from the actual relation contract."""
    has_from = bool(str(relation.from_output or "").strip())
    has_to = bool(str(relation.to_input or "").strip())
    if not has_from and not has_to:
        relation.type = "caller_decision"
        relation.mode = "caller_decision"
        relation.transform_owner = "caller"
        relation.required = False
        relation.requires_user_confirmation = True
        relation.input_schema = {}
        relation.output_schema = {}
        relation.source_selector = ""
        relation.target_path = ""
    return relation


def _normalize_capability_references(spec: FlowSpec) -> FlowSpec:
    """清理能力里指向不存在步骤的历史脏引用。

    能力只能引用已经物化为 FlowStep 的 step_id。捕获请求需要先通过
    add_capability_step/promote_request_to_step 转成步骤，不能把 request_id/hash
    直接塞进 capability.step_ids 或 call node。
    """
    step_ids = {s.step_id for s in spec.steps}

    def valid_step_id(value: Any) -> str:
        sid = str(value or "")
        return sid if sid in step_ids else ""

    def clean_nodes(nodes: list[dict[str, Any]], fallback_step_ids: list[str]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        node_ids: set[str] = set()
        local_call_step_ids: list[str] = []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            node_type = str(node.get("type") or "")
            copied = dict(node)
            if node_type == "call":
                sid = valid_step_id(copied.get("step_id"))
                usage = str(copied.get("usage") or "")
                if not sid:
                    if (
                        usage in {"option_source", "fact_check"}
                        and (copied.get("request_id") or copied.get("path"))
                    ):
                        cleaned.append(copied)
                        node_ids.add(str(copied.get("id") or ""))
                    continue
                copied["step_id"] = sid
                if sid not in local_call_step_ids:
                    local_call_step_ids.append(sid)
            elif node_type in {"foreach", "condition", "filter", "select", "map"}:
                for child_key in ("children", "steps", "then", "else", "otherwise"):
                    if isinstance(copied.get(child_key), list):
                        copied[child_key] = clean_nodes(copied[child_key], fallback_step_ids + local_call_step_ids)
            elif node_type == "return":
                ref = str(copied.get("from") or copied.get("source") or "")
                fallback = (fallback_step_ids + local_call_step_ids)
                if not (copied.get("value") or copied.get("from") or copied.get("source") or copied.get("path")):
                    if fallback:
                        copied["from"] = fallback[-1]
                        copied.setdefault("path", "response")
                    else:
                        continue
                if ref and ref not in step_ids and ref not in node_ids:
                    if fallback:
                        copied["from"] = fallback[-1]
                        copied.setdefault("path", "response")
                    else:
                        continue
            if not copied.get("id"):
                copied["id"] = f"{node_type or 'node'}_{len(cleaned) + 1}"
            cleaned.append(copied)
            node_ids.add(str(copied.get("id") or ""))
        return cleaned

    for cap in spec.capabilities or []:
        cap.nodes = clean_nodes(cap.nodes or [], [])
        legacy_refs = list(cap.request_refs or [])
        _materialize_capability_call_nodes_from_membership(spec, cap)
        _sync_capability_order(spec, cap)
        if not cap.locked:
            membership_by_step = {ref.step_id: ref for ref in cap.request_refs if ref.step_id}
            for legacy_ref in legacy_refs:
                current = membership_by_step.get(legacy_ref.step_id)
                if (
                    current is not None
                    and legacy_ref.usage == "preflight"
                    and legacy_ref.origin in {"manual", "user"}
                ):
                    current.usage = "preflight"
                    current.origin = legacy_ref.origin
                    current.confirmed = legacy_ref.confirmed
    return spec


def _remove_capability_step_nodes(nodes: list[dict[str, Any]], step_id: str) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        if node.get("type") == "call" and str(node.get("step_id") or "") == step_id:
            continue
        if node.get("type") == "return" and str(node.get("from") or node.get("source") or "") == step_id:
            continue
        copied = dict(node)
        for child_key in ("children", "steps", "then", "else", "otherwise"):
            if isinstance(copied.get(child_key), list):
                copied[child_key] = _remove_capability_step_nodes(copied[child_key], step_id)
        cleaned.append(copied)
    return cleaned


def _reorder_capability_call_nodes(
    nodes: list[dict[str, Any]], order: dict[str, int],
) -> list[dict[str, Any]]:
    copied_nodes: list[dict[str, Any]] = []
    for raw in nodes or []:
        if not isinstance(raw, dict):
            continue
        copied = dict(raw)
        for child_key in ("children", "steps", "then", "else", "otherwise"):
            if isinstance(copied.get(child_key), list):
                copied[child_key] = _reorder_capability_call_nodes(copied[child_key], order)
        copied_nodes.append(copied)
    call_positions = [
        index for index, node in enumerate(copied_nodes)
        if node.get("type") == "call" and str(node.get("step_id") or "") in order
    ]
    ordered_calls = sorted(
        (copied_nodes[index] for index in call_positions),
        key=lambda node: order[str(node.get("step_id") or "")],
    )
    for index, node in zip(call_positions, ordered_calls):
        copied_nodes[index] = node
    return copied_nodes


def _iter_capability_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        out.append(node)
        for key in ("steps", "then", "otherwise", "else", "children"):
            child = node.get(key)
            if isinstance(child, list):
                out.extend(_iter_capability_nodes([n for n in child if isinstance(n, dict)]))
    return out


def _select_flow_capability(
    spec: FlowSpec,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> FlowCapability | None:
    cap_id = str(capability_id or "").strip()
    cap_name = str(capability_name or "").strip()
    if not cap_id and not cap_name:
        return None
    for cap in spec.capabilities or []:
        if cap_id and cap.capability_id == cap_id:
            return cap
        if cap_name and cap.name == cap_name:
            return cap
    return None


def _transition_capability_kind(spec: FlowSpec, cap: FlowCapability, value: Any) -> None:
    """Atomically migrate submit contracts when the operator changes the kind."""
    old_kind = str(cap.kind or "submit")
    new_kind = str(value or "submit")
    cap.kind = new_kind
    if old_kind == new_kind or {old_kind, new_kind} - {"submit", "submit_batch"}:
        return

    by_id = {step.step_id: step for step in spec.steps}
    cap_steps = [
        by_id[step_id]
        for step_id in _capability_node_step_ids(cap)
        if step_id in by_id
    ]
    if not cap_steps:
        return
    write_steps = [step for step in cap_steps if _is_write_step(step)]
    final_write = write_steps[-1] if write_steps else cap_steps[-1]

    if new_kind == "submit_batch":
        # Selecting "批量提交" is explicit operator intent. Build the complete
        # executable contract in the same edit instead of leaving kind/schema/
        # nodes in three mutually contradictory states.
        cap.evidence = [
            item for item in (cap.evidence or [])
            if not (isinstance(item, dict) and item.get("kind") == "user_capability_kind")
        ]
        cap.evidence.append({
            "kind": "user_capability_kind",
            "batch_intent": True,
            "repeated_submission": True,
            "from": old_kind,
            "to": new_kind,
        })
        cap.input_schema = _batch_capability_input_schema(cap_steps)
        cap.inputs = _capability_inputs_from_top_level_schema(cap.input_schema)
        cap.nodes = _default_capability_nodes(cap_steps, kind="submit_batch", force_batch=True)
        cap.output_schema = {
            "type": "object",
            "properties": {
                "total": {"type": "number"},
                "success_count": {"type": "number"},
                "failed_count": {"type": "number"},
                "results": {"type": "array", "items": {"type": "object"}},
                "failed_items": {"type": "array", "items": {"type": "object"}},
            },
        }
        cap.output_mapping = [
            {"kind": "batch_result", "name": name, "response_path": name}
            for name in ("total", "success_count", "failed_count", "results", "failed_items")
        ]
        if "批量" not in str(cap.title or ""):
            cap.title = "批量" + (str(cap.title or "提交业务申请"))
        return

    # Leaving batch mode must remove every entries-dependent node/schema/relation
    # in the same transaction; otherwise later field edits resurrect stale
    # has_entries/foreach validation errors.
    cap.evidence = [
        item for item in (cap.evidence or [])
        if not (
            isinstance(item, dict)
            and (
                item.get("kind") == "user_capability_kind"
                or item.get("batch_intent")
                or item.get("repeated_submission")
            )
        )
    ]
    params = [param for step in cap_steps for param in (step.params or [])]
    cap.input_schema = _capability_input_schema(params)
    cap.nodes = _default_capability_nodes(cap_steps, kind="submit")
    cap.output_mapping = [{
        "kind": "final_response",
        "name": "result",
        "step_id": final_write.step_id,
        "response_path": "response",
    }]
    cap.inputs = []
    if "批量提交" in str(cap.title or ""):
        cap.title = str(cap.title).replace("批量提交", "提交", 1)
    elif str(cap.title or "").startswith("批量"):
        cap.title = str(cap.title)[2:] or "提交业务申请"
    cap_refs = {str(cap.name or ""), str(cap.capability_id or "")}
    spec.capability_relations = [
        relation for relation in (spec.capability_relations or [])
        if not (
            str(relation.to_capability or "") in cap_refs
            and str(relation.to_input or "") in {"entries", "items"}
        )
    ]


def _set_capability_loop_source(cap: FlowCapability, items: str = "input.entries") -> None:
    items = str(items or "input.entries")
    existing_calls = (
        [n for n in cap.nodes if isinstance(n, dict)]
        if cap.nodes else
        [{"id": f"call_{idx}", "type": "call", "step_id": sid} for idx, sid in enumerate(cap.step_ids, 1)]
    )
    if not any(n.get("type") == "foreach" for n in existing_calls):
        call_nodes = [n for n in existing_calls if n.get("type") == "call"]
        cap.nodes = [{
            "id": "foreach_entries",
            "type": "foreach",
            "items": items,
            "steps": call_nodes,
        }]
    else:
        for node in _iter_capability_nodes(existing_calls):
            if node.get("type") == "foreach":
                node["items"] = items
                break
        cap.nodes = existing_calls
    cap.kind = "submit_batch" if cap.kind == "submit" else cap.kind
    cap.updated_by = "repair"


def _set_capability_return(cap: FlowCapability, mapping: list[dict[str, Any]]) -> None:
    cap.output_mapping = [dict(x) for x in mapping if isinstance(x, dict)]
    if cap.output_mapping and not any(n.get("type") == "return" for n in _iter_capability_nodes(cap.nodes or [])):
        first = cap.output_mapping[0]
        cap.nodes.append({
            "id": "return_result",
            "type": "return",
            "from": first.get("step_id") or first.get("from") or "",
            "path": first.get("response_path") or first.get("path") or "response",
        })
    cap.updated_by = "repair"


def _upsert_capability_field(
    cap: FlowCapability, data: dict[str, Any], *, default_scope: str,
) -> CapabilityField:
    raw = dict(data or {})
    raw.setdefault("scope", default_scope)
    raw.setdefault("locked", True)
    raw.setdefault("confirmed", True)
    field = CapabilityField.model_validate(raw)
    name = str(field.key or field.path or field.display_name or "").strip()
    if field.scope in {"input", "output"} and not field.step_id:
        if not name:
            raise ValueError(f"capability {field.scope} field requires a name")
        schema_name = "input_schema" if field.scope == "input" else "output_schema"
        schema = dict(getattr(cap, schema_name) or {})
        schema.setdefault("type", "object")
        properties = dict(schema.get("properties") or {})
        properties[name] = _capability_schema_field(field)
        schema["properties"] = properties
        required = [str(value) for value in (schema.get("required") or []) if str(value) in properties]
        if field.required and name not in required:
            required.append(name)
        elif not field.required:
            required = [value for value in required if value != name]
        schema["required"] = required
        setattr(cap, schema_name, schema)
        cap.updated_by = "repair"
        return field
    if field.scope != "computed" or field.step_id:
        raise ValueError(
            "request/internal fields require a canonical FlowStep ParamField; "
            "only capability-level computed fields may be persisted"
        )
    for index, existing in enumerate(cap.computed_fields or []):
        if not _same_capability_computed_field(existing, field):
            continue
        merged = existing.model_dump()
        merged.update(field.model_dump(exclude_unset=True))
        cap.computed_fields[index] = CapabilityField.model_validate(merged)
        cap.updated_by = "repair"
        return cap.computed_fields[index]
    cap.computed_fields.append(field)
    cap.updated_by = "repair"
    return field


def _upsert_capability_dependency(cap: FlowCapability, data: dict[str, Any]) -> CapabilityDependency:
    dep = CapabilityDependency.model_validate(dict(data or {}))
    dep_sig = (
        dep.dependency_id,
        str((dep.source or {}).get("step_id") or ""),
        str((dep.source or {}).get("path") or ""),
        str((dep.target or {}).get("step_id") or ""),
        str((dep.target or {}).get("path") or ""),
    )
    for idx, existing in enumerate(cap.dependencies or []):
        existing_sig = (
            existing.dependency_id,
            str((existing.source or {}).get("step_id") or ""),
            str((existing.source or {}).get("path") or ""),
            str((existing.target or {}).get("step_id") or ""),
            str((existing.target or {}).get("path") or ""),
        )
        if existing_sig[0] == dep_sig[0] or existing_sig[1:] == dep_sig[1:]:
            merged = existing.model_dump()
            merged.update(dep.model_dump(exclude_unset=True))
            cap.dependencies[idx] = CapabilityDependency.model_validate(merged)
            cap.updated_by = "repair"
            return cap.dependencies[idx]
    cap.dependencies.append(dep)
    cap.updated_by = "repair"
    return dep


def _upsert_capability_node(cap: FlowCapability, node_type: str, data: dict[str, Any]) -> dict[str, Any]:
    raw = dict(data or {})
    raw["type"] = node_type
    node_id = str(raw.get("id") or f"{node_type}_{len(cap.nodes or []) + 1}")
    raw["id"] = node_id
    for idx, node in enumerate(cap.nodes or []):
        if str(node.get("id") or "") == node_id:
            next_node = dict(node)
            next_node.update(raw)
            cap.nodes[idx] = next_node
            cap.updated_by = "repair"
            return next_node
    cap.nodes.append(raw)
    cap.updated_by = "repair"
    return raw


def _upsert_capability_relation(spec: FlowSpec, data: dict[str, Any]) -> CapabilityRelation:
    rel = _normalize_capability_relation_semantics(CapabilityRelation.model_validate(dict(data or {})))
    rel_sig = (
        rel.relation_id,
        rel.from_capability,
        rel.from_output,
        rel.to_capability,
        rel.to_input,
    )
    for idx, existing in enumerate(spec.capability_relations or []):
        existing_sig = (
            existing.relation_id,
            existing.from_capability,
            existing.from_output,
            existing.to_capability,
            existing.to_input,
        )
        if existing_sig[0] == rel_sig[0] or existing_sig[1:] == rel_sig[1:]:
            merged = existing.model_dump()
            merged.update(rel.model_dump(exclude_unset=True))
            spec.capability_relations[idx] = CapabilityRelation.model_validate(merged)
            return spec.capability_relations[idx]
    spec.capability_relations.append(rel)
    return rel

_PENDING_FLOW_SPEC_HELPERS = {'_AUTOMATED_FIELD_EDIT_ACTORS': 'dano.execution.page.flow_spec_core.controlled_edits', '_batch_capability_input_schema': 'dano.execution.page.capability_io', '_capability_call_nodes': 'dano.execution.page.capability_refs', '_capability_call_step_ids_from_nodes': 'dano.execution.page.capability_refs', '_capability_input_schema': 'dano.execution.page.capability_io', '_capability_inputs_from_top_level_schema': 'dano.execution.page.capability_io', '_capability_is_batch': 'dano.execution.page.capability_contracts', '_capability_node_step_ids': 'dano.execution.page.capability_refs', '_capability_request_ref_from_step': 'dano.execution.page.capability_refs', '_capability_schema_array_item_props': 'dano.execution.page.capability_io', '_capability_schema_field': 'dano.execution.page.capability_io', '_capability_step_param_exists': 'dano.execution.page.capability_contracts', '_find_param': 'dano.execution.page.flow_spec_core.controlled_edits', '_is_write_step': 'dano.execution.page.capability_kinds', '_looks_batch_step': 'dano.execution.page.capability_kinds', '_rename_param_public_key': 'dano.execution.page.flow_spec_core.controlled_edits', '_same_capability_computed_field': 'dano.execution.page.capability_contracts', '_strip_body_prefix': 'dano.execution.page.flow_spec_core.normalization', '_sync_capability_order': 'dano.execution.page.capability_orchestration', '_transition_param_type': 'dano.execution.page.flow_spec_core.controlled_edits'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
