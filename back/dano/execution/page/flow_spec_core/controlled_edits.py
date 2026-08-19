"""Stage-independent controlled edit primitives."""
from __future__ import annotations

from typing import Any
import copy
import re
from pydantic import ValidationError
from dano.execution.page.flow_spec_core.models import (
    CapabilityDependency,
    CapabilityField,
    CapabilityRequestRef,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowSpecConflictError,
    FlowStep,
    IdentityBinding,
    ParamField,
    SelectBinding,
)
from dano.execution.page.flow_spec_core.normalization import (
    _infer_type_from_value,
    _strip_body_prefix,
)
from dano.execution.page.flow_spec_core.versioning import (
    append_flow_version,
)
from dano.execution.page.flow_spec_core.fingerprints import (
    flow_spec_fingerprint,
)


_PARAM_ALLOWED_FIELDS = frozenset({
    "category", "source_kind", "source", "label",
    "reason", "confidence", "name_source", "enum_options",
    "enum_value_map", "locked", "evidence", "description",
})


_STEP_ALLOWED_FIELDS = frozenset({
    "selects", "identity", "params", "sample_inputs",
    "source_meta", "semantic_role", "success_rule", "fact_check",
    "response_json", "notes",
})


def _record_param_manual_contract(param: ParamField, fields: list[str] | tuple[str, ...]) -> None:
    """Mark explicit operator-owned ParamField axes before any derived sync."""
    axis_by_field = {
        "key": "name", "label": "name", "name": "name", "display_name": "name",
        "path": "path", "value": "default_value", "default_value": "default_value",
        "type": "type", "wire_type": "path", "category": "category",
        "exposed_to_user": "category", "exposed_to_caller": "category",
        "editable": "category", "source_kind": "source", "source": "source",
        "enum_options": "source", "enum_value_map": "source",
        "required": "required",
    }
    for field in dict.fromkeys(fields):
        if not hasattr(param, field):
            continue
        param.evidence.append({
            "source": "manual_edit",
            "field": field,
            "axis": axis_by_field.get(field, field),
            "status": "locked",
            "kind": "manual_override",
            "value": getattr(param, field),
        })


def _reset_param_source(
    param: ParamField,
    *,
    reason: str | None = None,
    actor: str = "system",
) -> None:
    """把字段从运行期/接口来源恢复成普通用户输入，供删除依赖/重置来源使用。"""
    normalized_actor = str(actor or "system").strip().lower()
    if normalized_actor in _AUTOMATED_FIELD_EDIT_ACTORS and (
        param.locked
        or _param_axis_manually_edited(
            param, "category", "source_kind", "source", "editable",
            "exposed_to_user", "need_human_confirm",
        )
    ):
        return
    param.category = "user_param"
    param.source_kind = "user_input"
    param.source = {"kind": "sample", "path": param.path}
    param.editable = True
    param.exposed_to_user = True
    param.need_human_confirm = False
    param.confidence_tier = "manual"
    param.reason = reason or "已取消运行期/接口来源绑定，改为调用 Skill 时由用户填写"
    if normalized_actor == "user":
        _record_param_manual_contract(param, (
            "category", "source_kind", "source", "editable",
            "exposed_to_user", "need_human_confirm",
        ))


def _transition_param_type(param: ParamField, value: Any) -> None:
    """Apply only the explicitly edited type; never rewrite other field choices."""
    param.type = str(value or "string")


_AUTOMATED_FIELD_EDIT_ACTORS = frozenset({
    "planner", "repair", "auto", "autofix", "optimizer", "system",
})


def _remove_param_incoming_links(spec: FlowSpec, step: FlowStep, param: ParamField) -> None:
    """人工把字段改离上游响应时，依赖与字段来源必须在同一事务内解除。"""
    removed = [
        link for link in spec.links
        if link.target_step_id == step.step_id
        and _reference_targets_param(step, link.target_path, param)
    ]
    for link in removed:
        _record_rejected_dependency(spec, link)
    if removed:
        removed_ids = {link.link_id for link in removed}
        spec.links = [link for link in spec.links if link.link_id not in removed_ids]


def _apply_link_sources(steps: list[FlowStep], links: list[FlowLink]) -> None:
    by_id = {s.step_id: s for s in steps}
    for lk in links:
        if (lk.meta or {}).get("actor") == "agent" and not (lk.meta or {}).get("verified"):
            continue
        link_kind = _flow_link_kind(lk)
        target = by_id.get(lk.target_step_id)
        source = by_id.get(lk.source_step_id)
        if target is None or source is None:
            continue
        target_path = lk.target_path
        if link_kind == "response_key_map":
            # The response supplies the *keys* of this request object, not its
            # assignee values. Keep the stable label-to-value map as caller
            # input while execution translates labels to the latest keys.
            public = next((p for p in target.params if p.path == target_path), None)
            binding = lk.value_binding or {}
            input_field = str(binding.get("input_field") or "").strip()
            if public is not None and input_field:
                option_source = binding.get("option_source")
                input_fields_by_label = {
                    str(label): str(field)
                    for label, field in dict(binding.get("input_fields_by_label") or {}).items()
                    if str(label) and str(field)
                }
                if input_fields_by_label:
                    public.category = "runtime_var"
                    public.source_kind = "dynamic_structure"
                    public.source = {
                        "kind": "dynamic_structure_leaf",
                        "required_state": "internal",
                    }
                    public.required = False
                    public.editable = False
                    public.exposed_to_user = False
                    public.need_human_confirm = False
                    samples = public.value if isinstance(public.value, dict) else {}
                    target.sample_inputs.pop(input_field, None)
                    for label, field in input_fields_by_label.items():
                        if label in samples:
                            target.sample_inputs[field] = copy.deepcopy(samples[label])
                    continue
                public.key = input_field
                public.type = "object"
                public.wire_type = "object"
                public.category = "user_param"
                public.source_kind = "user_input"
                public.source = {
                    "kind": "dynamic_structure_input",
                    "required_state": "required",
                    **({"option_source": copy.deepcopy(option_source)} if isinstance(option_source, dict) else {}),
                }
                public.required = True
                public.editable = True
                public.exposed_to_user = True
                public.need_human_confirm = False
                # response_key_map exposes a stable label-to-value object.
                # Keep the canonical sample in that public form as well; the
                # executor alone translates labels to the latest response keys.
                if isinstance(public.value, dict):
                    target.sample_inputs[input_field] = copy.deepcopy(public.value)
            continue
        if link_kind == "structure":
            # A structure link controls request keys only. It is not a value
            # dependency and must not replace the request container itself.
            continue
        target_param = _resolve_param_reference(target, target_path)
        for p in [target_param] if target_param is not None else []:
            hydration = bool(
                (lk.meta or {}).get("captured_record_hydration")
                or (lk.evidence or {}).get("kind") == "record_hydration"
            )
            captured_binding_overrides_agent_input = bool(
                p.source_kind in {
                    "user_input", "page_default", "unknown", *_OPTION_SOURCE_KINDS,
                }
                and not _param_was_caller_typed(p)
                and lk.confirmed
                and float(lk.confidence or 0.0) >= 0.95
                and not (lk.meta or {}).get("unverified_reason")
                and any(
                    (lk.meta or {}).get(key) is True
                    for key in (
                        "captured_value_match",
                        "captured_structure_match",
                        "captured_record_hydration",
                    )
                )
            )
            if p.locked or _param_axis_manually_edited(
                p, "category", "source_kind", "source", "editable", "exposed_to_user",
            ) or (
                _param_source_agent_classified(p)
                and p.source_kind != "chained"
                and not captured_binding_overrides_agent_input
                and not hydration
            ):
                # 依赖连线和字段来源是独立可编辑的事实。人工已选择
                # 分类/来源后，同步层不得再用旧连线覆盖用户结果。
                continue
            if not _auto_dependency_link_allowed(p, lk.source_path, lk):
                continue
            caller_editable = (
                not _param_control_is_readonly(p)
                and (
                    _param_has_editable_control_evidence(p)
                    or hydration
                    or _param_was_caller_typed(p)
                )
            )
            option_source = dict(p.source or {}) if p.source_kind in _OPTION_SOURCE_KINDS else {}
            if not option_source and isinstance((p.source or {}).get("option_source"), dict):
                option_source = dict((p.source or {}).get("option_source") or {})
            p.category = "user_param" if caller_editable else "runtime_var"
            p.source_kind = "previous_response"
            p.source = {
                "kind": "previous_response",
                "step_id": source.step_id,
                "step_name": source.name,
                "response_path": lk.source_path,
                "target_path": target_path,
                "link_id": lk.link_id,
                "allow_caller_override": caller_editable,
                **({"option_source": option_source} if option_source else {}),
            }
            p.editable = True
            p.exposed_to_user = caller_editable
            if not caller_editable:
                p.default_value = None
                p.required = False
                p.source = {**(p.source or {}), "required_state": "optional"}
            if caller_editable:
                p.reason = (
                    f"编辑场景默认来自上一步 `{source.name or source.path}` 的响应 `{lk.source_path}`；"
                    "调用方仍可修改该字段，显式输入优先于上游默认值"
                )
            else:
                p.reason = (
                    f"该字段由上一步 `{source.name or source.path}` 的响应 `{lk.source_path}` 提供，"
                    "运行期自动注入，不能使用录制旧值"
                )
            if _link_is_auto_generated(lk) or any(
                (lk.meta or {}).get(key) is True
                for key in ("captured_value_match", "captured_structure_match")
            ):
                p.confidence = max(
                    float(p.confidence or 0.0), float(lk.confidence or 0.0),
                )
                if lk.confirmed:
                    p.need_human_confirm = False
            p.confidence_tier = "linked"
            if p.key in target.sample_inputs:
                target.sample_inputs.pop(p.key, None)
            break


def _apply_user_link_source(steps: list[FlowStep], link: FlowLink) -> None:
    """Persist a user-created UI response binding without rewriting type/category."""
    by_id = {step.step_id: step for step in steps}
    source_step = by_id.get(link.source_step_id)
    target_step = by_id.get(link.target_step_id)
    if source_step is None or target_step is None:
        return
    target_path = link.target_path
    param = _resolve_param_reference(target_step, target_path)
    if param is None:
        return
    param.source_kind = "previous_response"
    param.source = {
        "kind": "previous_response",
        "step_id": source_step.step_id,
        "step_name": source_step.name,
        "response_path": link.source_path,
        "target_path": target_path,
        "link_id": link.link_id,
    }
    param.editable = True
    if not param.exposed_to_user:
        param.default_value = None
    param.need_human_confirm = not bool(link.confirmed)
    param.reason = (
        f"该字段由用户绑定到 `{source_step.name or source_step.path or source_step.step_id}` "
        f"的响应 `{link.source_path}`"
    )
    param.confidence = max(float(param.confidence or 0.0), float(link.confidence or 0.0))
    param.confidence_tier = "manual"
    target_step.sample_inputs.pop(param.key, None)
    _record_param_manual_contract(param, ("source_kind", "source"))


def _find_step(spec: FlowSpec, step_id: str) -> FlowStep:
    for step in spec.steps:
        if step.step_id == step_id:
            return step
    available = [s.step_id for s in spec.steps]
    raise ValueError(f"step not found: {step_id} (available: {available})")


def _find_param(step: FlowStep, param_path: str, *, field_id: str = "", param_key: str = "", param_label: str = "") -> ParamField:
    stable_id = str(field_id or "")
    if stable_id:
        matched = next((param for param in step.params if param.field_id == stable_id), None)
        if matched is not None:
            return matched
    needle = str(param_path or "")
    for param in step.params:
        if param.path == needle:
            return param
    available = [f"{p.path}({p.key})" for p in step.params]
    raise ValueError(f"param not found: {param_path} in step {step.step_id}; available={available}")


def _resolve_param_reference(step: FlowStep, reference_path: str) -> ParamField | None:
    """Resolve legacy body-prefixed paths without collapsing distinct fields."""
    reference = str(reference_path or "")
    if not reference:
        return None
    exact = next((param for param in step.params if param.path == reference), None)
    if exact is not None:
        return exact
    normalized = _strip_body_prefix(reference)
    matches = [
        param for param in step.params
        if _strip_body_prefix(param.path) == normalized
    ]
    return matches[0] if len(matches) == 1 else None


def _reference_targets_param(step: FlowStep, reference_path: str, param: ParamField) -> bool:
    return _resolve_param_reference(step, reference_path) is param


def _find_link(spec: FlowSpec, link_id: str) -> FlowLink:
    for link in spec.links:
        if link.link_id == link_id:
            return link
    available = [link.link_id for link in spec.links]
    raise ValueError(f"link not found: {link_id} (available: {available})")


def _validate_link_endpoint(spec: FlowSpec, step_id: str, label: str) -> None:
    if not any(s.step_id == step_id for s in spec.steps):
        raise ValueError(f"{label} step not found: {step_id}")


def _ensure_unique_link(spec: FlowSpec, link: FlowLink) -> None:
    dup = any(
        existing.source_step_id == link.source_step_id
        and existing.target_step_id == link.target_step_id
        and existing.source_path == link.source_path
        and existing.target_path == link.target_path
        and existing.link_id != link.link_id
        for existing in spec.links
    )
    if dup:
        raise ValueError("duplicate link (same source/target/path exists)")


def _matching_link(spec: FlowSpec, link: FlowLink) -> FlowLink | None:
    for existing in spec.links:
        if (
            existing.source_step_id == link.source_step_id
            and existing.target_step_id == link.target_step_id
            and _strip_body_prefix(existing.source_path) == _strip_body_prefix(link.source_path)
            and _strip_body_prefix(existing.target_path) == _strip_body_prefix(link.target_path)
            and existing.link_id != link.link_id
        ):
            return existing
    return None


def _merge_link(existing: FlowLink, incoming: FlowLink) -> None:
    existing.confirmed = bool(existing.confirmed or incoming.confirmed)
    existing.confidence = max(float(existing.confidence or 0), float(incoming.confidence or 0))
    existing.reason = incoming.reason or existing.reason
    existing.locked = bool(getattr(existing, "locked", False) or getattr(incoming, "locked", False))
    if incoming.param_name:
        existing.param_name = incoming.param_name


def _remove_step(spec: FlowSpec, step_id: str) -> None:
    step = _find_step(spec, step_id)
    spec.steps.remove(step)
    spec.links = [
        lk for lk in spec.links
        if lk.source_step_id != step_id and lk.target_step_id != step_id
    ]
    spec.review_items = [
        item for item in spec.review_items
        if item.target.get("step_id") != step_id
        and item.target.get("source_step_id") != step_id
        and item.target.get("target_step_id") != step_id
    ]


def _rename_param_public_key(
    spec: FlowSpec,
    step: FlowStep,
    param: ParamField,
    new_key: str,
    *,
    actor: str,
) -> None:
    """Atomically rename a caller-facing field without touching its wire path.

    ``ParamField.path`` is the executable request contract. ``key``/``label``
    are the public business name.  Keeping the mutation here prevents model
    naming, manual naming and capability-schema regeneration from drifting into
    three different representations of the same field.
    """
    proposed = str(new_key or "").strip()
    if not proposed:
        raise ValueError("field key cannot be empty")
    if proposed == param.key:
        return
    if any(other is not param and other.key == proposed for other in step.params):
        raise ValueError(f"duplicate param key: {proposed}")

    old_key = param.key
    param.key = proposed
    param.label = proposed
    if actor == "user":
        param.name_source = "manual"
        evidence_source = "manual_edit"
    else:
        # A model proposal is useful semantic evidence, not an operator lock.
        # It must remain editable and must never self-confirm its own decision.
        param.name_source = "planner" if actor == "planner" else actor
        evidence_source = f"{actor}_proposal"
    param.evidence.append({
        "source": evidence_source,
        "field": "key",
        **({"axis": "name", "status": "locked", "kind": "manual_override"} if actor == "user" else {}),
        "previous": old_key,
        "value": proposed,
    })

    if old_key in step.sample_inputs:
        step.sample_inputs[proposed] = step.sample_inputs.pop(old_key)
    elif param.value not in (None, ""):
        step.sample_inputs.setdefault(proposed, param.value)
    for binding in step.selects:
        if binding.path == param.path or binding.param == old_key:
            binding.param = proposed

    for capability in spec.capabilities or []:
        for collection_name in (
            "fields", "inputs", "request_fields", "internal_fields",
            "computed_fields", "outputs",
        ):
            for field in getattr(capability, collection_name, []) or []:
                same_wire_field = bool(
                    field.step_id == step.step_id
                    and _strip_body_prefix(field.path or "") == _strip_body_prefix(param.path)
                )
                if same_wire_field or (field.step_id == step.step_id and field.key == old_key):
                    field.key = proposed
                    if field.display_name in {"", old_key}:
                        field.display_name = proposed
        for relation in spec.capability_relations or []:
            if relation.to_capability in {capability.name, capability.capability_id} and relation.to_input == old_key:
                relation.to_input = proposed

        def rename_node_refs(nodes: list[dict[str, Any]]) -> None:
            old_ref = f"input.{old_key}"
            new_ref = f"input.{proposed}"
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                for field_name in ("source", "items", "condition", "check"):
                    value = node.get(field_name)
                    if isinstance(value, str):
                        node[field_name] = value.replace(old_ref, new_ref)
                for child_name in ("children", "steps", "then", "else", "otherwise"):
                    if isinstance(node.get(child_name), list):
                        rename_node_refs(node[child_name])

        rename_node_refs(capability.nodes or [])


def apply_flow_edits(spec: FlowSpec, edits: list[dict[str, Any]]) -> FlowSpec:
    """应用编辑列表，返回新 FlowSpec（深拷贝）。"""
    if not edits:
        return refresh_review_items(spec.model_copy(deep=True))

    new_spec = spec.model_copy(deep=True)
    bulk_review_resolutions: list[tuple[set, set, bool]] = []
    needs_dependency_rebuild = False

    for edit in edits:
        op = edit.get("op")

        from dano.execution.page.recording_live import LIVE_RECORDING_AGENT_OPS, apply_recording_agent_edit
        if op in LIVE_RECORDING_AGENT_OPS:
            apply_recording_agent_edit(new_spec, edit)
            continue

        if op == "resolve_reviews":
            resolved = bool(edit.get("resolved", True))
            severities = set(edit.get("severities") or [])
            exclude_severities = set(edit.get("exclude_severities") or [])
            bulk_review_resolutions.append((severities, exclude_severities, resolved))
            generated = _generated_review_items(new_spec)
            old_by_id = {item.id: item for item in new_spec.review_items}
            for item in generated:
                if item.id in old_by_id:
                    item.resolved = old_by_id[item.id].resolved
                if severities and item.severity not in severities:
                    continue
                if exclude_severities and item.severity in exclude_severities:
                    continue
                item.resolved = resolved
            new_spec.review_items = generated
            continue

        if op == "resolve_review":
            item_id = str(edit.get("review_id") or "")
            if not item_id:
                raise ValueError("resolve_review missing review_id")
            found = False
            for item in new_spec.review_items:
                if item.id == item_id:
                    item.resolved = bool(edit.get("resolved", True))
                    found = True
                    break
            if not found:
                generated = _generated_review_items(new_spec)
                for item in generated:
                    if item.id == item_id:
                        item.resolved = bool(edit.get("resolved", True))
                        found = True
                        break
                if found:
                    new_spec.review_items = generated
            if not found:
                raise ValueError(f"review item not found: {item_id}")
            continue

        if op == "update_flow":
            field = str(edit.get("field") or "")
            value = edit.get("value")
            allowed = {"title", "business_description", "risk_level", "goal", "meta"}
            if field not in allowed:
                raise ValueError(f"unknown flow field: {field}")
            setattr(new_spec, field, value)
            actor = str(edit.get("actor") or "user")
            if field == "title" and actor != "planner":
                new_spec.meta = {**(new_spec.meta or {}), "title_source": "user"}
            if field == "business_description" and actor != "planner":
                new_spec.meta = {
                    **(new_spec.meta or {}),
                    "business_description_source": "user",
                }
            continue

        if op == "dedupe_steps":
            _dedupe_flow_steps(new_spec)
            continue

        # 重排步骤
        if op == "reorder_steps":
            order = edit.get("step_ids")
            if not isinstance(order, list):
                raise ValueError("reorder_steps missing step_ids list")
            existing_ids = {s.step_id for s in new_spec.steps}
            new_order_ids = set(order)
            if existing_ids != new_order_ids or len(order) != len(new_spec.steps):
                raise ValueError(
                    f"reorder_steps must include exactly all existing step_ids; "
                    f"got {sorted(new_order_ids)}, expected {sorted(existing_ids)}"
                )
            by_id = {s.step_id: s for s in new_spec.steps}
            new_spec.steps = [by_id[sid] for sid in order]
            continue

        if op == "remove_step":
            step_id = str(edit.get("step_id") or "")
            if not step_id:
                raise ValueError("remove_step missing step_id")
            _remove_step(new_spec, step_id)
            continue

        if op in {"add_candidate_step", "add_request_step"}:
            request_index = edit.get("request_index")
            request_id = str(edit.get("request_id") or "")
            promote_request_to_step(new_spec, request_index=request_index, request_id=request_id)
            needs_dependency_rebuild = True
            continue

        if op == "generate_capabilities":
            raise ValueError(
                "generate_capabilities is retired; submit a strict semantic plan"
            )

        if op == "add_capability":
            raw = dict(edit.get("capability") or {})
            raw.setdefault("name", _flow_capability_id(str(raw.get("kind") or "submit"), str(len(new_spec.capabilities) + 1)))
            raw.setdefault("title", raw["name"])
            raw.setdefault("kind", "submit")
            try:
                cap = FlowCapability.model_validate(raw)
            except ValidationError as e:
                raise ValueError(f"invalid capability data: {e}")
            if any(c.name == cap.name for c in new_spec.capabilities):
                raise ValueError(f"duplicate capability name: {cap.name}")
            _forget_removed_capability(new_spec, cap.name, cap.kind)
            new_spec.capabilities.append(cap)
            continue

        if op == "remove_capability":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities.pop(idx)
            _remember_removed_capability(new_spec, cap.name, cap.kind)
            for step_id in _capability_node_step_ids(cap):
                _remember_removed_capability_step(new_spec, cap.name, step_id)
            removed_refs = {str(cap.name or ""), str(cap.capability_id or "")}
            new_spec.capability_relations = [
                relation for relation in (new_spec.capability_relations or [])
                if str(relation.from_capability or "") not in removed_refs
                and str(relation.to_capability or "") not in removed_refs
            ]
            continue

        if op == "reorder_capabilities":
            refs = edit.get("capability_refs")
            if refs is None:
                refs = edit.get("capability_names")
            if not isinstance(refs, list):
                raise ValueError("reorder_capabilities missing capability_refs list")

            def cap_ref(cap: FlowCapability, idx: int) -> str:
                return str(cap.name or cap.capability_id or f"idx:{idx}")

            by_ref = {cap_ref(c, i): c for i, c in enumerate(new_spec.capabilities)}
            current = set(by_ref)
            requested = {str(x) for x in refs}
            if current != requested or len(refs) != len(new_spec.capabilities):
                raise ValueError(
                    f"reorder_capabilities must include exactly all capability refs; "
                    f"got {sorted(requested)}, expected {sorted(current)}"
                )
            new_spec.capabilities = [by_ref[str(ref)] for ref in refs]
            continue

        if op == "update_capability":
            idx = _find_capability_index(new_spec, edit)
            actor = str(edit.get("actor") or "user").strip().lower()
            field = str(edit.get("field") or "")
            if field not in _CAPABILITY_ALLOWED_FIELDS:
                raise ValueError(f"unknown capability field: {field}")
            if field in {
                "step_ids", "request_refs", "fields", "inputs",
                "request_fields", "internal_fields", "outputs",
            }:
                raise ValueError(f"derived capability field is read-only: {field}")
            value = edit.get("value")
            cap = new_spec.capabilities[idx]
            if field == "name":
                value = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "")).strip("_").lower()
                if not value:
                    raise ValueError("capability name cannot be empty")
                if any(i != idx and c.name == value for i, c in enumerate(new_spec.capabilities)):
                    raise ValueError(f"duplicate capability name: {value}")
            if field in {"confirmed", "requires_human_confirm"}:
                value = bool(value)
            if field == "confidence":
                value = max(0.0, min(1.0, float(value or 0)))
            if field == "computed_fields":
                value = [CapabilityField.model_validate(x) for x in (value or [])]
                if any(item.step_id or item.scope != "computed" for item in value):
                    raise ValueError(
                        "computed_fields only accepts capability-level computed values"
                    )
            if field == "dependencies":
                value = [CapabilityDependency.model_validate(x) for x in (value or [])]
            if field == "confirmed" and value:
                # Confirmation records the operator's decision.  Capability
                # shape, field type/source and enum quality may be generated by
                # a model, so they must not veto that decision.  Executability
                # is checked later by the deterministic request compiler.
                value = True
            if field == "kind":
                _transition_capability_kind(new_spec, cap, value)
            else:
                setattr(cap, field, value)
            if field == "confirmed" and value:
                cap.requires_human_confirm = False
                cap.status = "confirmed"
                cap.confirmation_hash = _capability_confirmation_hash(new_spec, cap)
            elif field == "confirmed":
                cap.status = "draft"
                cap.confirmation_hash = ""
            elif field != "updated_by":
                cap.updated_by = actor
                if field in {
                    "name", "title", "intent", "kind", "request_refs", "step_ids", "nodes",
                    "fields", "inputs", "request_fields", "internal_fields", "computed_fields",
                    "outputs", "dependencies", "input_schema", "output_schema", "output_mapping",
                    "preconditions", "caller_responsibilities", "skill_responsibilities",
                }:
                    cap.confirmed = False
                    cap.confirmation_hash = ""
                    cap.status = "draft"
                    cap.requires_human_confirm = True
            if field in {"step_ids", "nodes"}:
                _sync_capability_order(new_spec, cap)
            continue

        if op == "upsert_capability":
            raw = dict(edit.get("capability") or {})
            actor = str(edit.get("actor") or "user")
            name = str(raw.get("name") or edit.get("capability_name") or edit.get("name") or "")
            if not name:
                raise ValueError("upsert_capability missing name")
            idx = next((i for i, c in enumerate(new_spec.capabilities) if c.name == name), -1)
            if idx < 0:
                raw.setdefault("name", name)
                raw.setdefault("title", raw["name"])
                raw.setdefault("kind", "submit")
                raw.setdefault("confidence", 0.7)
                raw.setdefault("requires_human_confirm", True)
                created = FlowCapability.model_validate(raw)
                created.updated_by = actor
                if actor == "planner":
                    created.confirmed = False
                    created.locked = False
                new_spec.capabilities.append(created)
            else:
                cap = new_spec.capabilities[idx]
                planner_protected = bool(
                    actor == "planner"
                    and (
                        cap.locked
                        or cap.updated_by == "user"
                        # Automatically accepted (>60%) planner drafts remain
                        # optimizable.  A legacy/manual confirmation without a
                        # planner provenance stays protected conservatively.
                        or (cap.confirmed and cap.updated_by != "planner")
                    )
                )
                for key, value in raw.items():
                    if key not in _CAPABILITY_ALLOWED_FIELDS:
                        continue
                    if planner_protected and key not in {"confidence"}:
                        continue
                    if key in {"fields", "inputs", "request_fields", "internal_fields", "computed_fields", "outputs"}:
                        value = [CapabilityField.model_validate(x) for x in (value or [])]
                    elif key == "dependencies":
                        value = [CapabilityDependency.model_validate(x) for x in (value or [])]
                    elif key == "request_refs":
                        value = [CapabilityRequestRef.model_validate(x) for x in (value or [])]
                    setattr(cap, key, value)
                if not planner_protected:
                    cap.updated_by = actor
            continue

        if op in {
            "upsert_capability_field",
            "upsert_input_field",
            "upsert_request_field",
            "upsert_internal_field",
            "upsert_computed_field",
            "upsert_output_field",
        }:
            idx = _find_capability_index(new_spec, edit)
            default_scope = {
                "upsert_input_field": "input",
                "upsert_request_field": "request_field",
                "upsert_internal_field": "internal",
                "upsert_computed_field": "computed",
                "upsert_output_field": "output",
            }.get(op, str(edit.get("scope") or "request_field"))
            raw = dict(edit.get("field_data") or edit.get("field") or {})
            actor = str(edit.get("actor") or "user")
            if actor == "planner":
                # Planner output is a proposal. It cannot self-confirm/self-lock
                # a synthetic aggregate field and then use that field as proof
                # that the recorded request was batch-shaped.
                raw["locked"] = False
                raw["confirmed"] = False
                raw["evidence"] = [
                    *list(raw.get("evidence") or []),
                    {"source": "planner_proposal"},
                ]
            if "field" in edit and not isinstance(edit.get("field"), dict):
                raw["key"] = str(edit.get("field") or "")
            for alias in ("field_id", "key", "path", "step_id", "request_id", "request_index", "type", "source_kind"):
                if alias in edit and alias not in raw:
                    raw[alias] = edit.get(alias)
            if not _apply_capability_field_to_param(
                new_spec, raw, scope=default_scope, actor=actor,
            ):
                # Only capability-owned aggregate inputs/outputs are persisted on
                # FlowCapability. Step-bound fields are redirected to ParamField.
                _upsert_capability_field(new_spec.capabilities[idx], raw, default_scope=default_scope)
            new_spec.capabilities[idx].updated_by = actor
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op in {"add_request_to_capability", "add_capability_step"}:
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            actor = str(edit.get("actor") or edit.get("origin") or "user")
            step_id = str(edit.get("step_id") or "")
            if not step_id and ("request_index" in edit or edit.get("request_id")):
                step_id = promote_request_to_step(
                    new_spec,
                    request_index=edit.get("request_index"),
                    request_id=str(edit.get("request_id") or ""),
                ).step_id
                needs_dependency_rebuild = True
            step = _find_step(new_spec, step_id)
            usage = str(edit.get("usage") or "execute")
            origin = str(edit.get("origin") or actor or "manual")
            extra_fields = {
                k: v for k, v in edit.items()
                if k not in {"op", "capability_name", "capability_id", "step_id", "actor", "usage", "origin", "request_id", "request_index", "request", "source", "target"}
            }
            _forget_removed_capability_step(new_spec, cap.name, step_id)
            _set_capability_request_membership(
                new_spec, cap, step, usage=usage, origin=origin, extra_fields=extra_fields,
            )
            cap.updated_by = "planner" if actor == "planner" else "user"
            _invalidate_capability_contract(cap)
            if usage in {"execute", "preflight"}:
                _add_step_id_to_capability(new_spec, cap, step_id)
            _sync_capability_order(new_spec, cap)
            continue

        if op in {"remove_request_from_capability", "remove_capability_step"}:
            idx = _find_capability_index(new_spec, edit)
            step_id = str(edit.get("step_id") or "")
            actor = str(edit.get("actor") or edit.get("origin") or "user")
            if actor != "planner":
                _remember_removed_capability_step(
                    new_spec, new_spec.capabilities[idx].name, step_id
                )

            new_spec.capabilities[idx].request_refs = [
                ref
                for ref in new_spec.capabilities[idx].request_refs
                if ref.step_id != step_id
            ]
            new_spec.capabilities[idx].nodes = _remove_capability_step_nodes(
                new_spec.capabilities[idx].nodes or [], step_id,
            )
            new_spec.capabilities[idx].updated_by = (
                "planner" if actor == "planner" else "user"
            )
            _invalidate_capability_contract(new_spec.capabilities[idx])
            _sync_capability_order(new_spec, new_spec.capabilities[idx])
            _sync_capability_output_after_step_removal(new_spec.capabilities[idx])
            continue

        if op == "reorder_capability_steps":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            requested = [str(value) for value in (edit.get("step_ids") or []) if str(value)]
            current = _capability_call_step_ids_from_nodes(cap.nodes or [])
            if len(requested) != len(current) or set(requested) != set(current):
                raise ValueError(
                    "reorder_capability_steps must contain every executable step exactly once"
                )
            cap.nodes = _reorder_capability_call_nodes(
                cap.nodes or [],
                {step_id: index for index, step_id in enumerate(requested)},
            )
            cap.updated_by = str(edit.get("actor") or "user")
            _invalidate_capability_contract(cap)
            _sync_capability_order(new_spec, cap)
            continue

        if op == "bind_dependency":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            raw = dict(edit.get("dependency") or {})
            raw.setdefault("type", edit.get("type") or "response_to_request")
            raw.setdefault("source", edit.get("source") or {
                "step_id": edit.get("source_step") or edit.get("source_step_id") or "",
                "path": edit.get("source_path") or "",
            })
            raw.setdefault("target", edit.get("target") or {
                "step_id": edit.get("target_step") or edit.get("target_step_id") or "",
                "path": edit.get("target_path") or "",
            })
            raw.setdefault("confirmed", bool(edit.get("confirmed", False)))
            raw.setdefault("locked", bool(edit.get("locked", False)))
            raw.setdefault("confidence", float(edit.get("confidence") or 0.75))
            raw.setdefault("reason", edit.get("reason") or "能力级修复绑定的依赖")
            dep = _upsert_capability_dependency(cap, raw)
            # 能力内依赖的两个端点必须同属该能力执行闭包；否则依赖视图会在下一次
            # 同步时被正确判为无效并丢弃，造成“刚绑定又消失”。
            for endpoint in (dep.source or {}, dep.target or {}):
                endpoint_step_id = str(endpoint.get("step_id") or "")
                if endpoint_step_id:
                    _find_step(new_spec, endpoint_step_id)
                    _add_step_id_to_capability(new_spec, cap, endpoint_step_id)
                    if not any(
                        n.get("type") == "call" and n.get("step_id") == endpoint_step_id
                        for n in _iter_capability_nodes(cap.nodes or [])
                        if isinstance(n, dict)
                    ):
                        cap.nodes.append({
                            "id": f"call_{len(cap.nodes or []) + 1}",
                            "type": "call",
                            "step_id": endpoint_step_id,
                        })
            _upsert_global_link_from_capability_dependency(new_spec, dep)
            _sync_capability_order(new_spec, cap)
            _invalidate_capability_contract(cap)
            continue

        if op in {"set_map", "set_condition"}:
            idx = _find_capability_index(new_spec, edit)
            node_type = "map" if op == "set_map" else "condition"
            raw = dict(edit.get("node") or {})
            if node_type == "map":
                raw.setdefault("source", edit.get("source") or "")
                raw.setdefault("target", edit.get("target") or "")
            else:
                raw.setdefault("condition", edit.get("condition") or edit.get("check") or "")
                for branch_key in ("then", "else", "steps", "children", "otherwise"):
                    if branch_key in edit and branch_key not in raw:
                        raw[branch_key] = edit[branch_key]
            if edit.get("node_id"):
                raw.setdefault("id", edit.get("node_id"))
            _upsert_capability_node(new_spec.capabilities[idx], node_type, raw)
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op == "set_output_mapping":
            idx = _find_capability_index(new_spec, edit)
            mapping = edit.get("mapping")
            if isinstance(mapping, dict):
                mapping = [mapping]
            if not isinstance(mapping, list):
                mapping = [{
                    "kind": edit.get("kind") or "final_response",
                    "step_id": edit.get("step_id") or edit.get("from") or "",
                    "response_path": edit.get("response_path") or edit.get("path") or "response",
                    "name": edit.get("name") or edit.get("field") or "",
                }]
            _set_capability_return(new_spec.capabilities[idx], mapping)
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op == "set_capability_relation":
            raw = dict(edit.get("relation") or {})
            for alias in ("type", "from_capability", "from_output", "to_capability", "to_input", "confidence", "confirmed", "reason", "evidence"):
                if alias in edit and alias not in raw:
                    raw[alias] = edit.get(alias)
            raw.setdefault("requires_user_confirmation", bool(edit.get("requires_user_confirmation", True)))
            _upsert_capability_relation(new_spec, raw)
            refs = {str(raw.get("from_capability") or ""), str(raw.get("to_capability") or "")}
            for capability in new_spec.capabilities:
                if capability.name in refs or capability.capability_id in refs:
                    _invalidate_capability_contract(capability)
            continue

        if op == "bind_option_source":
            _bind_option_source(
                new_spec,
                target_step_id=str(edit.get("target_step") or edit.get("target_step_id") or edit.get("step_id") or ""),
                target_path=str(edit.get("target_path") or edit.get("param_path") or ""),
                source_step_id=str(edit.get("source_step") or edit.get("source_step_id") or ""),
                source_url=str(edit.get("source_url") or ""),
                value_key=str(edit.get("value_key") or ""),
                label_key=str(edit.get("label_key") or ""),
                id_path=str(edit.get("id_path") or ""),
                options=edit.get("options") if isinstance(edit.get("options"), list) else None,
                option_map=edit.get("option_map") if isinstance(edit.get("option_map"), dict) else None,
                multi=bool(edit.get("multi")),
                actor=str(edit.get("actor") or "user"),
            )
            _invalidate_capabilities_for_steps(new_spec, {
                str(edit.get("target_step") or edit.get("target_step_id") or edit.get("step_id") or "")
            })
            continue

        if op == "set_loop_source":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            items = str(edit.get("items") or edit.get("source") or "input.entries")
            _set_capability_loop_source(cap, items)
            cap.updated_by = str(edit.get("actor") or "user")
            _sync_capability_order(new_spec, cap)
            _invalidate_capability_contract(cap)
            continue

        if op == "set_return_mapping":
            idx = _find_capability_index(new_spec, edit)
            mapping = edit.get("mapping")
            if isinstance(mapping, dict):
                mapping = [mapping]
            if not isinstance(mapping, list):
                mapping = [{
                    "kind": edit.get("kind") or "final_response",
                    "step_id": edit.get("step_id") or edit.get("from") or "",
                    "response_path": edit.get("response_path") or edit.get("path") or "response",
                }]
            _set_capability_return(new_spec.capabilities[idx], mapping)
            new_spec.capabilities[idx].updated_by = str(edit.get("actor") or "user")
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op == "reject_dependency":
            link_id = str(edit.get("link_id") or "")
            if link_id:
                link = _find_link(new_spec, link_id)
                _record_rejected_dependency(new_spec, link)
                if link in new_spec.links:
                    new_spec.links.remove(link)
                continue
            source_step_id = str(edit.get("source_step_id") or edit.get("source_step") or "")
            source_path = str(edit.get("source_path") or "")
            target_step_id = str(edit.get("target_step_id") or edit.get("target_step") or "")
            target_path = str(edit.get("target_path") or "")
            if not all([source_step_id, source_path, target_step_id, target_path]):
                raise ValueError("reject_dependency missing link_id or source/target tuple")
            _record_rejected_dependency_raw(
                new_spec,
                source_step_id=source_step_id,
                source_path=source_path,
                target_step_id=target_step_id,
                target_path=target_path,
            )
            new_spec.links = [
                lk for lk in new_spec.links
                if _dependency_sig(lk.source_step_id, lk.source_path, lk.target_step_id, lk.target_path)
                not in _rejected_dependency_sigs(new_spec)
            ]
            continue

        # 链接编辑
        if edit.get("link_id"):
            link_id = edit["link_id"]
            if op == "update":
                link = _find_link(new_spec, link_id)
                field = edit.get("field")
                value = edit.get("value")
                if not field:
                    raise ValueError("link update missing field")
                identity_fields = {
                    "source_step_id", "source_path", "target_step_id", "target_path",
                }
                old_identity_value = str(getattr(link, field, "")) if field in identity_fields else ""
                if field == "confirmed":
                    link.confirmed = bool(value)
                elif field == "param_name":
                    link.param_name = str(value) if value is not None else None
                elif field == "source_path":
                    _validate_link_endpoint(new_spec, link.source_step_id, "source")
                    link.source_path = str(value)
                    link.source_tokens = None
                elif field == "target_path":
                    _validate_link_endpoint(new_spec, link.target_step_id, "target")
                    link.target_path = str(value)
                    link.target_tokens = None
                elif field == "source_step_id":
                    _validate_link_endpoint(new_spec, str(value), "source")
                    link.source_step_id = str(value)
                    link.source_tokens = None
                elif field == "target_step_id":
                    _validate_link_endpoint(new_spec, str(value), "target")
                    link.target_step_id = str(value)
                    link.target_tokens = None
                elif field == "link_id":                   # H19 修复:显式禁改 link_id(会被唯一性校验破坏)
                    raise ValueError("link_id is immutable")
                else:
                    # H19 修复:不再 hasattr 兜底(避免改 link_id/reason/internal 等关键字段)
                    raise ValueError(f"unknown link field: {field}")
                if field in identity_fields and str(getattr(link, field, "")) != old_identity_value:
                    from dano.execution.page.recording_live import invalidate_dependency_verification

                    invalidate_dependency_verification(link, f"依赖字段 {field} 已变化，需要重新验证")
                duplicate = _matching_link(new_spec, link)
                if duplicate is not None:
                    _merge_link(duplicate, link)
                    if link in new_spec.links:
                        new_spec.links.remove(link)
                    effective_link = duplicate
                else:
                    effective_link = link
                if (
                    str(edit.get("actor") or "user").strip().lower() == "user"
                    and field == "confirmed"
                    and effective_link.confirmed
                ):
                    _apply_user_link_source(new_spec.steps, effective_link)
                continue

            if op == "remove":
                link = _find_link(new_spec, link_id)
                if edit.get("reset_target"):
                    target_step = _find_step(new_spec, link.target_step_id)
                    target_param = _find_param(target_step, link.target_path)
                    actor = str(edit.get("actor") or "user").strip().lower()
                    if actor in _AUTOMATED_FIELD_EDIT_ACTORS and (
                        target_param.locked or _param_has_manual_contract(target_param)
                    ):
                        continue
                    _reset_param_source(
                        target_param,
                        reason="依赖已由用户移除，字段已恢复为用户输入",
                        actor=actor,
                    )
                if edit.get("record_rejection", True):
                    _record_rejected_dependency(new_spec, link)
                new_spec.links.remove(link)
                continue

        # 添加链接
        if op == "add" and edit.get("link"):
            link_data = dict(edit["link"])
            link_data.setdefault("source_step_id", "")
            link_data.setdefault("target_step_id", "")
            link_data.setdefault("source_path", "")
            link_data.setdefault("target_path", "")
            _validate_link_endpoint(new_spec, link_data["source_step_id"], "source")
            _validate_link_endpoint(new_spec, link_data["target_step_id"], "target")
            try:
                new_link = FlowLink(**link_data)
            except ValidationError as e:
                raise ValueError(f"invalid link data: {e}")
            existing = _matching_link(new_spec, new_link)
            if existing is not None:
                _merge_link(existing, new_link)
                effective_link = existing
            else:
                _ensure_unique_link(new_spec, new_link)
                new_spec.links.append(new_link)
                effective_link = new_link
            actor = str(edit.get("actor") or "user").strip().lower()
            if actor == "user":
                _apply_user_link_source(new_spec.steps, effective_link)
            continue

        # 步骤/参数编辑
        step_id = edit.get("step_id")
        if not step_id:
            raise ValueError("edit missing step_id")

        step = _find_step(new_spec, step_id)

        if op == "update":
            param_path = edit.get("param_path")
            field = edit.get("field")
            value = edit.get("value")
            actor = str(edit.get("actor") or "user").strip().lower()

            if not field:
                raise ValueError("update edit missing field")

            if param_path:
                # 参数级编辑
                if actor in _AUTOMATED_FIELD_EDIT_ACTORS:
                    param = next((item for item in step.params if item.path == param_path), None)
                    if param is None:
                        continue
                    if field == "locked":
                        continue
                    protected_axes = {
                        "key": ("key", "label", "name", "display_name"),
                        "label": ("key", "label", "name", "display_name"),
                        "value": ("value", "default_value"),
                        "source_kind": (
                            "source_kind", "source", "category",
                            "exposed_to_user", "exposed_to_caller",
                        ),
                        "source": (
                            "source_kind", "source", "category",
                            "exposed_to_user", "exposed_to_caller",
                        ),
                        "category": (
                            "category", "exposed_to_user", "exposed_to_caller",
                            "source_kind", "source",
                        ),
                        "exposed_to_user": (
                            "category", "exposed_to_user", "exposed_to_caller",
                            "source_kind", "source",
                        ),
                    }.get(str(field), (str(field),))
                    if _param_has_full_lock(param) or _param_axis_manually_edited(param, *protected_axes):
                        continue
                else:
                    param = _find_param(
                        step,
                        param_path,
                        field_id=str(edit.get("field_id") or ""),
                        param_key=str(edit.get("param_key") or ""),
                        param_label=str(edit.get("param_label") or ""),
                    )
                comparable_value = value
                if field in {"required", "exposed_to_user", "editable", "need_human_confirm", "locked"}:
                    comparable_value = bool(value)
                elif field in {"key", "label", "description", "path", "type", "category", "source_kind"}:
                    comparable_value = str(value or "").strip() if field == "path" else str(value)
                elif field == "value":
                    comparable_value = str(value)
                if hasattr(param, str(field)) and getattr(param, str(field)) == comparable_value:
                    continue
                if field == "key":
                    _rename_param_public_key(new_spec, step, param, str(value), actor=actor)
                elif field == "path":
                    old_path = param.path
                    new_path = str(value or "").strip()
                    if not new_path:
                        raise ValueError("param path cannot be empty")
                    if any(p is not param and p.path == new_path for p in step.params):
                        raise ValueError(f"duplicate param path: {new_path}")
                    linked_targets = [
                        lk for lk in new_spec.links
                        if lk.target_step_id == step.step_id
                        and _reference_targets_param(step, lk.target_path, param)
                    ]
                    source_targets_param = bool(
                        isinstance(param.source, dict)
                        and _reference_targets_param(
                            step, str(param.source.get("target_path") or ""), param,
                        )
                    )
                    param.path = new_path
                    for sb in step.selects:
                        if sb.path == old_path:
                            sb.path = new_path
                        if sb.id_path == old_path:
                            sb.id_path = new_path
                    for idn in step.identity:
                        if idn.path == old_path:
                            idn.path = new_path
                    for sv in step.system_values:
                        if sv.path == old_path:
                            sv.path = new_path
                    for lk in linked_targets:
                        lk.target_path = new_path
                    if isinstance(param.source, dict) and source_targets_param:
                        param.source["target_path"] = new_path
                elif field == "value":
                    param.value = str(value)
                    param.default_value = param.value
                    step.sample_inputs[param.key] = param.value
                elif field == "type":
                    _transition_param_type(param, value)
                elif field == "required":
                    param.required = bool(value)
                elif field == "exposed_to_user":           # H22 修复:bool 字段显式 bool() 转换
                    param.exposed_to_user = bool(value)
                elif field == "editable":
                    param.editable = bool(value)
                elif field == "need_human_confirm":
                    param.need_human_confirm = bool(value)
                elif field in _PARAM_ALLOWED_FIELDS:
                    setattr(param, field, value)
                    if field in {"label", "description"}:
                        param.name_source = "manual"
                else:
                    # H19 修复:不再 hasattr 兜底(避免改 path/source_kind/internal 等关键字段)
                    raise ValueError(f"unknown param field: {field}")
                if actor == "user" and field in {
                    "key", "label", "description", "value", "type", "category", "source_kind", "source",
                    "required", "exposed_to_user", "editable", "need_human_confirm", "enum_options", "enum_value_map",
                }:
                    if field != "key":
                        _record_param_manual_contract(param, (str(field),))
                if field in {
                    "key", "path", "label", "description", "value", "type", "category", "source_kind",
                    "source", "required", "exposed_to_user", "editable", "need_human_confirm",
                    "enum_options", "enum_value_map",
                }:
                    _invalidate_capabilities_for_steps(new_spec, {step.step_id})
            else:
                # 步骤级编辑
                if field == "url":
                    step.url = str(value)
                elif field == "method":
                    step.method = str(value).upper()
                elif field == "headers":
                    step.headers = dict(value)
                elif field == "content_type":
                    step.content_type = str(value)
                elif field == "name":
                    step.name = str(value)
                elif field == "role":
                    role = str(value)
                    step.source_meta = {**(step.source_meta or {}), "role": role}
                    step.semantic_role = role
                elif field == "risk_level":
                    step.risk_level = str(value)
                elif field == "body_source":
                    step.body_source = str(value) if value is not None else ""
                elif field == "path":
                    step.path = str(value)
                    step.url = str(value)
                elif field == "step_id":                   # H19 修复:显式禁改 step_id
                    raise ValueError("step_id is immutable")
                elif field == "selects":
                    try:
                        step.selects = [SelectBinding.model_validate(x) for x in (value or [])]
                        for binding in step.selects:
                            _hydrate_select_source_contract(new_spec, binding)
                            if (
                                actor == "user"
                                and binding.enum_confirmed is None
                                and (
                                    (
                                        binding.source_url
                                        and binding.value_key
                                        and binding.label_key
                                        and (binding.options or binding.option_map)
                                    )
                                    or (
                                        not binding.source_url
                                        and binding.options
                                        and len(_enum_option_map_from_options(binding.options))
                                        == len(binding.options)
                                    )
                                )
                            ):
                                # A complete binding explicitly saved by the
                                # operator is a confirmation, not a model guess.
                                binding.enum_confirmed = True
                    except ValidationError as e:
                        raise ValueError(f"invalid selects data: {e}")
                elif field == "identity":
                    try:
                        step.identity = [IdentityBinding.model_validate(x) for x in (value or [])]
                    except ValidationError as e:
                        raise ValueError(f"invalid identity data: {e}")
                elif field == "params":
                    try:
                        step.params = [ParamField.model_validate(x) for x in (value or [])]
                    except ValidationError as e:
                        raise ValueError(f"invalid params data: {e}")
                elif field in _STEP_ALLOWED_FIELDS:
                    setattr(step, field, value)
                else:
                    # H19 修复:不再 hasattr 兜底
                    raise ValueError(f"unknown step field: {field}")
                if field in {
                    "url", "method", "headers", "content_type", "name", "role", "risk_level",
                    "body_source", "path", "selects", "identity", "params", "source_meta",
                    "semantic_role", "success_rule", "fact_check", "response_json",
                }:
                    _invalidate_capabilities_for_steps(new_spec, {step.step_id})
            continue

        elif op == "reset_param_source":
            param_path = edit.get("param_path")
            if not param_path:
                raise ValueError("reset_param_source missing param_path")
            param = _find_param(
                step,
                param_path,
                field_id=str(edit.get("field_id") or ""),
                param_key=str(edit.get("param_key") or ""),
                param_label=str(edit.get("param_label") or ""),
            )
            target = str(edit.get("to") or "user_input")
            actor = str(edit.get("actor") or "user").strip().lower()
            if actor in _AUTOMATED_FIELD_EDIT_ACTORS and (
                param.locked or _param_has_manual_contract(param)
            ):
                continue
            new_spec.links = [
                lk for lk in new_spec.links
                if not (lk.target_step_id == step.step_id and _reference_targets_param(step, lk.target_path, param))
            ]
            if target == "constant":
                param.category = "system_const"
                param.source_kind = "constant"
                param.source = {"kind": "constant", "path": param.path, "manual": True}
                param.editable = True
                param.exposed_to_user = False
                param.need_human_confirm = False
                param.reason = "已重置为系统固定值，发布后按当前录制值提交"
                if actor == "user":
                    _record_param_manual_contract(param, (
                        "category", "source_kind", "source", "editable",
                        "exposed_to_user", "need_human_confirm",
                    ))
            else:
                _reset_param_source(param, actor=actor)
                step.sample_inputs[param.key] = param.value
            continue

        elif op == "add":
            raw_param_data = edit.get("param")
            if not isinstance(raw_param_data, dict) or not raw_param_data:
                raise ValueError("add edit missing param")
            param_data = dict(raw_param_data)
            explicit_fields = set(param_data)
            if "type" not in param_data and "value" in param_data:
                param_data["type"] = _infer_type_from_value(param_data["value"])
            try:
                new_param = ParamField(**param_data)
            except ValidationError as e:
                raise ValueError(f"invalid param data: {e}")
            actor = str(edit.get("actor") or "user").strip().lower()
            if actor == "user":
                # A field added in the workbench is already an explicit
                # operator decision. Record each supplied contract axis before
                # the final sync so enum/pagination heuristics cannot rewrite it.
                manual_fields = [field for field in (
                    "type", "category", "source_kind", "source",
                    "exposed_to_user", "editable", "required",
                    "need_human_confirm", "enum_options", "enum_value_map",
                ) if field in explicit_fields]
                _record_param_manual_contract(new_param, manual_fields)
                new_param.locked = True
            elif actor in _AUTOMATED_FIELD_EDIT_ACTORS:
                # Planner/repair payloads are proposals and cannot grant
                # themselves operator ownership through locked/manual markers.
                new_param.locked = False
                new_param.evidence = [
                    item for item in (new_param.evidence or [])
                    if not isinstance(item, dict) or item.get("source") != "manual_edit"
                ]
            step.params.append(new_param)
            if new_param.value:
                step.sample_inputs[new_param.key] = new_param.value
            continue

        elif op == "remove":
            param_path = edit.get("param_path")
            if not param_path:
                raise ValueError("remove edit missing param_path")
            param = _find_param(
                step,
                param_path,
                field_id=str(edit.get("field_id") or ""),
                param_key=str(edit.get("param_key") or ""),
                param_label=str(edit.get("param_label") or ""),
            )
            # 字段删除是一个完整的契约删除：不能只移除 params，却留下指向该字段的
            # 依赖、枚举绑定或身份绑定。否则前端看似删除成功，下一轮同步/校验又会
            # 从这些残留引用中恢复旧字段，表现为“修改后无法删除”。
            _remove_param_incoming_links(new_spec, step, param)
            key_is_unique = sum(item.key == param.key for item in step.params) == 1
            label_is_unique = bool(param.label) and sum(item.label == param.label for item in step.params) == 1
            step.selects = [
                binding for binding in (step.selects or [])
                if not (
                    _reference_targets_param(step, binding.path or binding.id_path or "", param)
                    or (
                        not binding.path and not binding.id_path
                        and (
                            (key_is_unique and binding.param == param.key)
                            or (label_is_unique and binding.param == param.label)
                        )
                    )
                )
            ]
            step.identity = [
                binding for binding in (step.identity or [])
                if not _reference_targets_param(step, binding.path or "", param)
            ]
            step.params.remove(param)
            if param.key in step.sample_inputs:
                del step.sample_inputs[param.key]
            _invalidate_capabilities_for_steps(new_spec, {step.step_id})
            continue

        else:
            raise ValueError(f"unknown edit op: {op}")

    _sync_link_sources(new_spec.steps, new_spec.links)
    if needs_dependency_rebuild:
        rebuild_flow_dependencies(new_spec)
    if bulk_review_resolutions:
        generated = _generated_review_items(new_spec)
        old_by_id = {item.id: item for item in new_spec.review_items}
        for item in generated:
            if item.id in old_by_id:
                item.resolved = old_by_id[item.id].resolved
            for severities, exclude_severities, resolved in bulk_review_resolutions:
                if severities and item.severity not in severities:
                    continue
                if exclude_severities and item.severity in exclude_severities:
                    continue
                item.resolved = resolved
        new_spec.review_items = generated

    # 验证
    try:
        FlowSpec.model_validate(new_spec.model_dump())
    except ValidationError as e:
        raise ValueError(f"invalid spec after edits: {e}")

    actions = ",".join(str(e.get("op") or "edit") for e in edits)
    _normalize_capability_references(new_spec)
    return append_flow_version(
        refresh_review_items(_sync_capability_io_schemas(new_spec)),
        "flow_edit",
        reason=actions[:200],
        actor="user",
    )


_INTERNAL_SOURCE_CONTRACT = {
    "user_input": ("user_param", True),
    "api_option": ("user_param", True),
    "page_enum": ("user_param", True),
    "static_enum": ("user_param", True),
    "manual_enum": ("user_param", True),
    "form_option": ("user_param", True),
    "constant": ("system_const", False),
    "page_default": ("user_param", True),
    "page_rule": ("runtime_var", False),
    "request_header": ("runtime_var", False),
    "current_user": ("runtime_var", False),
    "storage": ("runtime_var", False),
    "cookie": ("runtime_var", False),
    "session": ("runtime_var", False),
    "page_context": ("runtime_var", False),
    "context": ("runtime_var", False),
    "previous_response": ("runtime_var", False),
    "dynamic_structure": ("runtime_var", False),
    "selected_option_field": ("runtime_var", False),
    "computed": ("runtime_var", False),
    "generated": ("runtime_var", False),
    "system_time": ("runtime_var", False),
    "system_generated": ("runtime_var", False),
    "unknown": ("runtime_var", False),
}


def apply_client_flow_patch(
    spec: FlowSpec,
    edits: list[dict[str, Any]],
    *,
    expected_fingerprint: str,
) -> FlowSpec:
    """Apply a browser patch without accepting a client-owned FlowSpec.

    The fingerprint gates concurrent edits. RequestFacts and sensitive step
    transport evidence remain authoritative on the server.
    """
    expected = str(expected_fingerprint or "")
    current = flow_spec_fingerprint(spec)
    if not expected:
        raise ValueError("expected_fingerprint is required")
    if expected != current:
        raise FlowSpecConflictError(expected, current)
    if not isinstance(edits, list) or not edits:
        raise ValueError("flow patch requires a non-empty edits list")
    if len(edits) > 200:
        raise ValueError("flow patch contains too many edits")

    safe_edits: list[dict[str, Any]] = []
    for raw_edit in edits:
        if not isinstance(raw_edit, dict):
            raise ValueError("flow patch edits must be objects")
        edit = dict(raw_edit)
        op = str(edit.get("op") or "")
        if op == "update_flow" and str(edit.get("field") or "") == "meta":
            raise ValueError("server-owned flow field: meta")
        if (
            op == "update"
            and edit.get("param_path")
            and str(edit.get("field") or "") == "category"
        ):
            raise ValueError("derived parameter field: category")
        if op in {"add_capability", "upsert_capability"}:
            raw_capability = edit.get("capability")
            if isinstance(raw_capability, dict) and (
                "step_ids" in raw_capability or "request_refs" in raw_capability
            ):
                raise ValueError("client capability membership must be expressed through nodes")
            derived_fields = {
                "fields", "inputs", "request_fields", "internal_fields", "outputs",
            }
            if isinstance(raw_capability, dict) and derived_fields.intersection(raw_capability):
                raise ValueError("client capability field projections are read-only")
        if op == "update" and not edit.get("param_path"):
            field = str(edit.get("field") or "")
            if field in _CLIENT_SERVER_OWNED_STEP_FIELDS or field == "selects":
                raise ValueError(f"server-owned step field: {field}")
        if op == "upsert_select":
            safe_edits.append(_client_select_patch(spec, edit))
        elif (
            op == "update"
            and edit.get("param_path")
            and str(edit.get("field") or "") == "source_kind"
        ):
            safe_edits.extend(_client_source_patch(spec, edit))
        else:
            safe_edits.append(edit)
    return apply_flow_edits(spec, safe_edits)

_PENDING_FLOW_SPEC_HELPERS = {'refresh_review_items': 'dano.execution.page.flow_materialization.review_items', 'CapabilityDependency': 'dano.execution.page.flow_spec_core.models', 'CapabilityField': 'dano.execution.page.flow_spec_core.models', 'CapabilityRequestRef': 'dano.execution.page.flow_spec_core.models', 'FlowCapability': 'dano.execution.page.flow_spec_core.models', 'FlowLink': 'dano.execution.page.flow_spec_core.models', 'FlowSpec': 'dano.execution.page.flow_spec_core.models', 'FlowSpecConflictError': 'dano.execution.page.flow_spec_core.models', 'FlowStep': 'dano.execution.page.flow_spec_core.models', 'IdentityBinding': 'dano.execution.page.flow_spec_core.models', 'ParamField': 'dano.execution.page.flow_spec_core.models', 'SelectBinding': 'dano.execution.page.flow_spec_core.models', 'LIVE_RECORDING_AGENT_OPS': 'dano.execution.page.recording_live', 'apply_recording_agent_edit': 'dano.execution.page.recording_live', '_CAPABILITY_ALLOWED_FIELDS': 'dano.execution.page.capability_contracts', '_find_capability_index': 'dano.execution.page.capability_contracts', '_flow_capability_id': 'dano.execution.page.capability_contracts', '_upsert_global_link_from_capability_dependency': 'dano.execution.page.capability_contracts', '_CLIENT_SERVER_OWNED_STEP_FIELDS': 'dano.execution.page.flow_client_projection', '_client_select_patch': 'dano.execution.page.flow_client_projection', '_client_source_patch': 'dano.execution.page.flow_client_projection', '_OPTION_SOURCE_KINDS': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_enum_option_map_from_options': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_auto_dependency_link_allowed': 'dano.execution.page.flow_materialization.links', '_dependency_sig': 'dano.execution.page.flow_materialization.links', '_flow_link_kind': 'dano.execution.page.flow_materialization.links', '_link_is_auto_generated': 'dano.execution.page.flow_materialization.links', '_record_rejected_dependency': 'dano.execution.page.flow_materialization.links', '_record_rejected_dependency_raw': 'dano.execution.page.flow_materialization.links', '_rejected_dependency_sigs': 'dano.execution.page.flow_materialization.links', '_sync_link_sources': 'dano.execution.page.flow_materialization.links', 'rebuild_flow_dependencies': 'dano.execution.page.flow_materialization.links', '_bind_option_source': 'dano.execution.page.flow_materialization.field_contracts.option_sync', '_hydrate_select_source_contract': 'dano.execution.page.flow_materialization.field_contracts.option_sync', '_dedupe_flow_steps': 'dano.execution.page.flow_materialization.request_steps', 'promote_request_to_step': 'dano.execution.page.flow_materialization.request_steps', '_generated_review_items': 'dano.execution.page.flow_release', '_infer_type_from_value': 'dano.execution.page.flow_spec_core.normalization', '_strip_body_prefix': 'dano.execution.page.flow_spec_core.normalization', '_param_axis_manually_edited': 'dano.execution.page.flow_materialization.field_contracts.common', '_param_control_is_readonly': 'dano.execution.page.flow_materialization.field_contracts.common', '_param_has_full_lock': 'dano.execution.page.flow_materialization.field_contracts.common', '_param_has_manual_contract': 'dano.execution.page.flow_materialization.field_contracts.common', '_param_source_agent_classified': 'dano.execution.page.flow_materialization.field_contracts.common', '_param_has_editable_control_evidence': 'dano.execution.page.flow_materialization.field_contracts.caller_ownership', '_param_was_caller_typed': 'dano.execution.page.flow_materialization.field_contracts.caller_ownership', 'append_flow_version': 'dano.execution.page.flow_spec_core.versioning', 'flow_spec_fingerprint': 'dano.execution.page.flow_spec_core.fingerprints', '_add_step_id_to_capability': 'dano.execution.page.capability_nodes', '_apply_capability_field_to_param': 'dano.execution.page.capability_nodes', '_capability_call_step_ids_from_nodes': 'dano.execution.page.capability_refs', '_capability_confirmation_hash': 'dano.execution.page.capability_views', '_capability_node_step_ids': 'dano.execution.page.capability_refs', '_forget_removed_capability': 'dano.execution.page.capability_refs', '_forget_removed_capability_step': 'dano.execution.page.capability_refs', '_invalidate_capabilities_for_steps': 'dano.execution.page.capability_nodes', '_invalidate_capability_contract': 'dano.execution.page.capability_nodes', '_iter_capability_nodes': 'dano.execution.page.capability_nodes', '_normalize_capability_references': 'dano.execution.page.capability_nodes', '_remember_removed_capability': 'dano.execution.page.capability_refs', '_remember_removed_capability_step': 'dano.execution.page.capability_refs', '_remove_capability_step_nodes': 'dano.execution.page.capability_nodes', '_reorder_capability_call_nodes': 'dano.execution.page.capability_nodes', '_set_capability_loop_source': 'dano.execution.page.capability_nodes', '_set_capability_request_membership': 'dano.execution.page.capability_nodes', '_set_capability_return': 'dano.execution.page.capability_nodes', '_sync_capability_io_schemas': 'dano.execution.page.capability_io', '_sync_capability_order': 'dano.execution.page.capability_orchestration', '_sync_capability_output_after_step_removal': 'dano.execution.page.capability_io', '_transition_capability_kind': 'dano.execution.page.capability_nodes', '_upsert_capability_dependency': 'dano.execution.page.capability_nodes', '_upsert_capability_field': 'dano.execution.page.capability_nodes', '_upsert_capability_node': 'dano.execution.page.capability_nodes', '_upsert_capability_relation': 'dano.execution.page.capability_nodes'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
