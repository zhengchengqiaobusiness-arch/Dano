"""Stage 6: capability contract validation reports."""
from __future__ import annotations

from typing import Any
import re
from dano.execution.page.flow_spec_core.models import (
    FlowCapability,
    FlowSpec,
    ParamField,
)
from dano.execution.page.recording_facts import (
    _WRITE_METHODS,
    _request_fact_items,
    _request_fact_key_from_entry,
    _request_fact_signature_key,
)
from dano.execution.page.flow_materialization.request_usage import (
    _materialized_step_id_for_request,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_requires_caller_input,
)


def _capability_warning(
    section: dict[str, Any],
    warnings: list[str],
    *,
    code: str,
    message: str,
    target: dict[str, Any],
) -> None:
    entry = {"code": code, "message": message, "target": target}
    section.setdefault("warnings", []).append(entry)
    warnings.append(message)


def _capability_error(
    section: dict[str, Any],
    *,
    code: str,
    message: str,
    target: dict[str, Any],
) -> None:
    section.setdefault("errors", []).append({"code": code, "message": message, "target": target})


def _capability_param_enum_issue(param: ParamField) -> str:
    if param.type not in {"enum", "list-enum"}:
        return ""
    if param.source_kind == "api_option":
        # API candidates are resolved at runtime. An empty capture snapshot (or
        # a source that is being reselected) is valid and must not block publish.
        return ""
    if not param.enum_options:
        return "缺少可执行枚举选项 label/value"
    # A DOM snapshot is display evidence, not automatically an executable wire
    # contract.  A partial snapshot is safe when the recorded request itself uses
    # the displayed label (or when a real label->value pair covers it); we simply
    # avoid hard-coding the partial list in the public schema.  If the request uses
    # a code/ID, however, the missing map remains a hard blocker.
    if (
        param.source_kind == "page_enum"
        and (param.source or {}).get("enum_confirmed") is False
        and not _incomplete_page_enum_is_executable(param)
    ):
        return "页面枚举快照不完整：只捕获到显示名称，缺少完整的真实 label→value 映射"
    if param.source_kind == "manual_enum" and not _manual_enum_mapping_complete(param):
        return "人工枚举必须为每个显示名称提供明确的真实 label→value 映射"
    if not _enum_map_covers_recorded_value(param):
        return "枚举 label/value 不能映射录制提交值"
    if _enum_options_look_value_only(param):
        return "枚举候选看起来只有内部值，缺少可展示 label"
    return ""


def _capability_param_enum_warning(param: ParamField) -> str:
    """Return non-blocking evidence quality advice for an executable enum."""
    if (
        param.type in {"enum", "list-enum"}
        and param.source_kind == "page_enum"
        and (param.source or {}).get("enum_confirmed") is False
        and param.enum_options
        and _incomplete_page_enum_is_executable(param)
        and not _enum_options_look_value_only(param)
    ):
        return "页面枚举快照可能不完整；已验证当前录制值可执行，未把候选列表作为完整约束"
    return ""


def _capability_validation_report(spec: FlowSpec, *, prepared: bool = False) -> dict[str, Any]:
    spec = ensure_recorded_goal(_sync_capability_io_schemas(spec.model_copy(deep=True)))
    _normalize_capability_references(spec)
    errors: list[str] = []
    warnings: list[str] = []
    caps = list(spec.capabilities or [])
    step_by_id = {s.step_id: s for s in spec.steps}
    request_items = _request_fact_items(spec)
    materialized_keys = {_step_request_key(s) for s in spec.steps}
    materialized_signatures = {_step_request_signature_key(s) for s in spec.steps}
    unmaterialized_business = [
        item for item in request_items
        if _eligible_business_write_fact(item)
        and not _materialized_step_id_for_request(spec, item)
    ]
    high_conf_unused = [
        {
            "request_id": item.get("request_id"),
            "request_index": item.get("request_index"),
            "method": item.get("method"),
            "path": item.get("path") or item.get("url"),
            "role": item.get("role"),
            "confidence": item.get("confidence"),
            "reason": item.get("reason"),
        }
        for item in request_items
        if float(item.get("confidence") or 0) >= 0.9
        and (item.get("role") or "") in {"submit_anchor", "business_write", "business_get", "read_context", "read_option"}
        and _request_fact_key_from_entry(item) not in materialized_keys
        and _request_fact_signature_key(item) not in materialized_signatures
    ]
    checked_requests: list[dict[str, Any]] = []
    checked_manual_requests: list[dict[str, Any]] = []
    capability_reports: list[dict[str, Any]] = []
    capability_internal = {
        "passed": True,
        "errors": [],
        "warnings": [],
        "capabilities": [],
    }
    capability_relations = {
        "passed": True,
        "errors": [],
        "warnings": [],
        "relations": [],
    }
    skill_level = {
        "passed": True,
        "errors": [],
        "warnings": [],
        "summary": {
            "capabilities": len(caps),
            "confirmed_capabilities": len([c for c in caps if c.confirmed]),
            "relations": len(spec.capability_relations or []),
        },
    }
    materialization_integrity = {
        "passed": True,
        "errors": [],
        "unmaterialized_business_requests": [],
        "unassigned_business_steps": [],
        "unassigned_materialized_steps": [],
        "duplicate_business_step_memberships": [],
    }

    def add_integrity_error(code: str, message: str, target: dict[str, Any]) -> None:
        entry = {"code": code, "message": message, "target": target}
        materialization_integrity["errors"].append(entry)
        skill_level["errors"].append(entry)
        errors.append(message)

    for item in unmaterialized_business:
        target = {
            "kind": "captured_request",
            "request_id": item.get("request_id"),
            "request_index": item.get("request_index"),
            "method": item.get("method"),
            "path": item.get("path") or item.get("url"),
        }
        materialization_integrity["unmaterialized_business_requests"].append(target)
        add_integrity_error(
            "unmaterialized_business_request",
            f"未物化业务操作：{item.get('method')} {item.get('path') or item.get('url')}",
            target,
        )

    memberships_by_step: dict[str, set[str]] = {}
    removed_capability_step_ids = _retired_capability_step_ids(spec)
    internal_step_ids = {
        str(item)
        for item in (spec.meta or {}).get("internal_step_ids") or []
        if item
    }
    for capability in caps:
        capability_name = capability.name or capability.capability_id or "<unnamed>"
        for step_id in _capability_node_step_ids(capability):
            memberships_by_step.setdefault(step_id, set()).add(capability_name)
        for request_ref in capability.request_refs or []:
            if request_ref.step_id:
                memberships_by_step.setdefault(request_ref.step_id, set()).add(capability_name)
    for item in request_items:
        role = str(item.get("role") or "")
        requires_membership = bool(
            item.get("keep")
            and role in {"business_write", "submit_anchor", "business_get", "read_context", "read_option"}
        )
        if not requires_membership:
            continue
        step_id = _materialized_step_id_for_request(spec, item)
        if not step_id or step_id in removed_capability_step_ids:
            continue
        memberships = memberships_by_step.get(step_id, set())
        target = {
            "kind": "flow_step",
            "step_id": step_id,
            "request_id": item.get("request_id"),
            "method": item.get("method"),
            "path": item.get("path") or item.get("url"),
        }
        if not memberships:
            if step_id in internal_step_ids:
                continue
            is_public_business = role in {"business_write", "submit_anchor", "business_get"}
            bucket = "unassigned_business_steps" if is_public_business else "unassigned_materialized_steps"
            materialization_integrity[bucket].append(target)
            add_integrity_error(
                "unassigned_business_step" if is_public_business else "unassigned_materialized_step",
                f"已物化步骤未归属任何能力或内部用途：{item.get('method')} {item.get('path') or item.get('url')}",
                target,
            )
        elif _eligible_business_write_fact(item) and len(set(memberships)) > 1:
            target["capabilities"] = sorted(set(memberships))
            materialization_integrity["duplicate_business_step_memberships"].append(target)
            add_integrity_error(
                "duplicate_business_step_membership",
                f"业务写步骤同时归属多个能力：{item.get('method')} {item.get('path') or item.get('url')}",
                target,
            )
    materialization_integrity["passed"] = not materialization_integrity["errors"]
    if spec.steps and not caps:
        skill_level["passed"] = not skill_level["errors"]
        warnings.append("FlowSpec 未生成业务能力编排，前端只能按底层接口展示")
        _capability_warning(
            skill_level,
            warnings,
            code="missing_capabilities",
            message="Skill 层未生成 capability，P1 仅记录为能力编排缺口",
            target={"kind": "flow", "flow_id": spec.flow_id},
        )
        return {
            "passed": False,
            "errors": errors,
            "warnings": warnings,
            "capabilities": [],
            "checked_requests": checked_requests,
            "checked_manual_requests": checked_manual_requests,
            "unused_high_confidence_requests": high_conf_unused,
            "capability_internal": capability_internal,
            "capability_relations": capability_relations,
            "skill_level": skill_level,
            "materialization_integrity": materialization_integrity,
        }

    allowed_kinds = ALLOWED_CAPABILITY_KINDS
    allowed_nodes = {"call", "map", "filter", "condition", "foreach", "select", "return"}
    seen_names: set[str] = set()
    request_ids, request_indexes = _capability_request_indexes(spec)
    for cap in caps:
        label = cap.name or cap.kind or "<unnamed>"
        cap_errors: list[str] = []
        cap_warnings: list[str] = []
        internal_section = {
            "name": cap.name,
            "capability_id": cap.capability_id,
            "step_ids": [],
            "request_refs": [],
            "fields": [],
            "dependencies": [],
            "outputs": [],
            "warnings": [],
            "errors": [],
        }
        if not cap.name:
            cap_errors.append("Capability 缺少 name")
        elif cap.name in seen_names:
            cap_errors.append(f"Capability `{cap.name}` 重名")
        seen_names.add(cap.name)

        if cap.kind not in allowed_kinds:
            cap_errors.append(f"Capability `{label}` kind `{cap.kind}` 不在允许范围内")

        if cap.kind in {"submit_batch", "validate_batch"} and not _capability_is_batch(spec, cap):
            cap_errors.append(
                f"Capability `{label}` 被声明为批量能力，但没有批量接口事实或明确的 entries 循环设计"
            )
        if cap.kind in {"submit_batch", "validate_batch"}:
            item_props, _item_required = _capability_schema_array_item_props(cap.input_schema, "entries")
            routing_names = {
                name for name in item_props
                if _ROUTING_FIELD_RE.search(str(name or ""))
            }
            if item_props and routing_names == item_props:
                cap_errors.append(
                    f"Capability `{label}` 的 entries 只有审批/路由字段，不能把人员列表当成批量业务条目"
                )

        node_step_ids = _capability_node_step_ids(cap)
        if not node_step_ids:
            cap_errors.append(f"Capability `{label}` 没有绑定真实接口，空能力不能发布")
            _capability_error(
                internal_section,
                code="capability_empty",
                message=f"Capability `{label}` 没有绑定真实接口",
                target={"kind": "capability", "capability": label},
            )
        missing_step_ids = [sid for sid in node_step_ids if sid not in step_by_id]
        if missing_step_ids:
            msg = f"Capability `{label}` 指向不存在的步骤: {missing_step_ids}"
            if cap.confirmed:
                cap_errors.append(msg)
            else:
                cap_warnings.append(msg)

        if not cap.confirmed or cap.requires_human_confirm:
            cap_warnings.append(f"Capability `{label}` 尚未确认，需要确认或移除后再发布")
        elif not cap.confirmation_hash:
            cap_warnings.append(f"Capability `{label}` 来自旧版确认记录；下次合同编辑后将启用版本指纹校验")
        elif cap.confirmation_hash != _capability_confirmation_hash(spec, cap, prepared=prepared):
            cap_errors.append(f"Capability `{label}` 确认后合同已变化，请复核并重新确认")

        cap_steps = [step_by_id[sid] for sid in node_step_ids if sid in step_by_id]
        cap_step_id_set = {s.step_id for s in cap_steps}
        internal_section["step_ids"] = [
            {"step_id": sid, "exists": sid in step_by_id}
            for sid in node_step_ids
        ]
        cap_request_keys: list[str] = []
        for st in cap_steps:
            key = _step_request_key(st)
            if key not in cap_request_keys:
                cap_request_keys.append(key)
                req_item = {
                    "step_id": st.step_id,
                    "request_key": key,
                    "method": st.method,
                    "path": st.path or st.url,
                    "manual_added": bool((st.source_meta or {}).get("manual_added")),
                }
                checked_requests.append(req_item)
                if req_item["manual_added"]:
                    checked_manual_requests.append(req_item)
            for param in st.params or []:
                enum_issue = _capability_param_enum_issue(param)
                target = {
                    "kind": "capability_enum",
                    "capability": label,
                    "step_id": st.step_id,
                    "path": param.path,
                }
                if enum_issue:
                    msg = f"Capability `{label}` 枚举字段 `{param.key or param.path}` {enum_issue}"
                    if cap.confirmed:
                        cap_errors.append(msg)
                        _capability_error(internal_section, code="capability_enum_mapping_missing", message=msg, target=target)
                    else:
                        _capability_warning(
                            internal_section,
                            warnings,
                            code="capability_enum_mapping_missing",
                            message=msg,
                            target=target,
                        )
                enum_warning = _capability_param_enum_warning(param)
                if enum_warning:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_enum_snapshot_incomplete",
                        message=f"Capability `{label}` 枚举字段 `{param.key or param.path}` {enum_warning}",
                        target=target,
                    )

        for ref in cap.request_refs or []:
            ref_id = _capability_ref_key(ref.request_id)
            ref_index = _capability_ref_key(ref.request_index)
            step_exists = not ref.step_id or ref.step_id in cap_step_id_set
            request_exists = (
                (not ref_id and not ref_index)
                or (ref_id and ref_id in request_ids)
                or (ref_index and ref_index in request_indexes)
            )
            internal_section["request_refs"].append({
                "request_id": ref.request_id,
                "request_index": ref.request_index,
                "step_id": ref.step_id,
                "step_exists": step_exists,
                "request_exists": request_exists,
            })
            if not step_exists:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_request_ref_step_missing",
                    message=f"Capability `{label}` request_ref 指向能力闭包外步骤 `{ref.step_id}`",
                    target={"kind": "capability_request_ref", "capability": label, "step_id": ref.step_id},
                )
            if not request_exists:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_request_ref_missing",
                    message=f"Capability `{label}` request_ref `{ref_id or ref_index}` 找不到对应请求事实",
                    target={"kind": "capability_request_ref", "capability": label, "request_id": ref_id, "request_index": ref_index},
                )

        input_props = ((cap.input_schema or {}).get("properties") or {})
        dependency_targets = {
            (
                str((dep.target or {}).get("step_id") or ""),
                _strip_body_prefix(str((dep.target or {}).get("path") or "")),
            )
            for dep in cap.dependencies or []
        }
        canonical_fields = [
            *(cap.inputs or []),
            *(cap.request_fields or []),
            *(cap.internal_fields or []),
            *(cap.computed_fields or []),
            *(cap.outputs or []),
        ]

        seen_field_entries: set[tuple[str, str, str, str]] = set()
        for field in canonical_fields:
            field_key = (field.field_id, field.scope, field.step_id, field.path or field.key)
            if field_key in seen_field_entries:
                continue
            seen_field_entries.add(field_key)
            field_name = field.key or field.path or field.display_name or field.field_id
            field_step = step_by_id.get(field.step_id or "")
            if field.step_id and field.step_id not in cap_step_id_set:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_field_step_outside_closure",
                    message=f"Capability `{label}` 字段 `{field_name}` 绑定到能力闭包外步骤 `{field.step_id}`",
                    target={"kind": "capability_field", "capability": label, "field_id": field.field_id, "step_id": field.step_id},
                )
            field_path_exists = True
            if field.scope in {"request_field", "internal"} and field.step_id:
                field_path_exists = _capability_step_param_exists(field_step, field.path or field.key)
            elif field.scope == "input" and field_name:
                field_path_exists = (
                    _schema_path_exists(cap.input_schema, field.path, field.key)
                    or field_name in input_props
                    or _capability_step_param_exists(field_step, field.path or field.key)
                )
            internal_section["fields"].append({
                "field_id": field.field_id,
                "scope": field.scope,
                "path": field.path,
                "key": field.key,
                "step_id": field.step_id,
                "path_exists": field_path_exists,
            })
            if not field_path_exists:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_field_path_missing",
                    message=f"Capability `{label}` 字段 `{field_name}` 找不到对应字段路径",
                    target={"kind": "capability_field", "capability": label, "field_id": field.field_id, "path": field.path},
                )
            if (
                field.scope in {"request_field", "internal"}
                and not _capability_field_has_valid_source(field, dependency_targets)
            ):
                msg = f"Capability `{label}` 内部字段 `{field_name}` 缺少上游响应、系统值或固定来源"
                target = {"kind": "capability_field", "capability": label, "field_id": field.field_id, "path": field.path}
                if cap.confirmed and field.required:
                    cap_errors.append(msg)
                    _capability_error(internal_section, code="capability_field_source_missing", message=msg, target=target)
                else:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_field_source_missing",
                        message=msg,
                        target=target,
                    )
            if (
                field.scope in {"input", "request_field"}
                and field.exposed_to_caller
                and _capability_field_looks_internal(field)
            ):
                msg = f"Capability `{label}` 字段 `{field_name}` 看起来是内部 ID/短码/状态码，不能直接暴露给调用方"
                target = {"kind": "capability_field", "capability": label, "field_id": field.field_id, "path": field.path}
                if _capability_execute_record_selector(cap, field):
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_internal_field_exposed",
                        message=msg,
                        target=target,
                    )
                elif cap.confirmed:
                    cap_errors.append(msg)
                    _capability_error(internal_section, code="capability_internal_field_exposed", message=msg, target=target)
                else:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_internal_field_exposed",
                        message=msg,
                        target=target,
                    )

        for dep in cap.dependencies or []:
            source = dep.source or {}
            target = dep.target or {}
            source_step_id = str(source.get("step_id") or "")
            target_step_id = str(target.get("step_id") or "")
            source_step = step_by_id.get(source_step_id)
            target_step = step_by_id.get(target_step_id)
            source_in_closure = bool(source_step_id and source_step_id in cap_step_id_set)
            target_in_closure = bool(target_step_id and target_step_id in cap_step_id_set)
            source_path = str(source.get("path") or "")
            target_path = str(target.get("path") or "")
            source_exists = _capability_response_path_exists(source_step, source_path)
            target_exists = _capability_step_param_exists(target_step, target_path)
            internal_section["dependencies"].append({
                "dependency_id": dep.dependency_id,
                "source_step_id": source_step_id,
                "target_step_id": target_step_id,
                "source_in_closure": source_in_closure,
                "target_in_closure": target_in_closure,
                "source_path_exists": source_exists,
                "target_path_exists": target_exists,
            })
            if not source_in_closure or not target_in_closure:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_dependency_outside_closure",
                    message=f"Capability `{label}` 依赖 `{dep.dependency_id}` 端点不都在能力闭包内",
                    target={"kind": "capability_dependency", "capability": label, "dependency_id": dep.dependency_id},
                )
            if not source_exists or not target_exists:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_dependency_endpoint_missing",
                    message=f"Capability `{label}` 依赖 `{dep.dependency_id}` 的 source/target 路径无法确认存在",
                    target={"kind": "capability_dependency", "capability": label, "dependency_id": dep.dependency_id},
                )

        for idx, mapping in enumerate(cap.output_mapping or []):
            output_entry = {"index": idx, "interpretable": True}
            if not isinstance(mapping, dict):
                output_entry.update({"interpretable": False, "reason": "not_object"})
                internal_section["outputs"].append(output_entry)
                msg = f"Capability `{label}` output_mapping[{idx}] 不是对象，无法解释输出"
                target = {"kind": "capability_output", "capability": label, "index": idx}
                if cap.confirmed:
                    cap_errors.append(msg)
                    _capability_error(internal_section, code="capability_output_mapping_invalid", message=msg, target=target)
                else:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_output_mapping_invalid",
                        message=msg,
                        target=target,
                    )
                continue
            out_step_id = str(mapping.get("step_id") or mapping.get("from") or "")
            out_path = str(mapping.get("response_path") or mapping.get("path") or mapping.get("field") or "")
            output_entry.update({"step_id": out_step_id, "path": out_path})
            if out_step_id and out_step_id not in cap_step_id_set:
                output_entry["interpretable"] = False
                output_entry["reason"] = "step_outside_closure"
            elif out_step_id and not _capability_response_path_exists(step_by_id.get(out_step_id), out_path):
                output_entry["interpretable"] = False
                output_entry["reason"] = "response_path_missing"
            elif not (mapping.get("kind") or out_step_id or out_path or mapping.get("name") or mapping.get("field")):
                output_entry["interpretable"] = False
                output_entry["reason"] = "missing_source"
            internal_section["outputs"].append(output_entry)
            if not output_entry["interpretable"]:
                msg = f"Capability `{label}` output_mapping[{idx}] 无法解释为能力输出"
                if cap.confirmed:
                    cap_errors.append(msg)
                    internal_section.setdefault("errors", []).append({
                        "code": "capability_output_mapping_uninterpretable",
                        "message": msg,
                        "target": {"kind": "capability_output", "capability": label, "index": idx},
                    })
                else:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_output_mapping_uninterpretable",
                        message=msg,
                        target={"kind": "capability_output", "capability": label, "index": idx},
                    )
        if not cap.output_mapping and not cap.output_schema and not any(
            isinstance(n, dict) and n.get("type") == "return" for n in _iter_capability_nodes(cap.nodes or [])
        ):
            msg = f"Capability `{label}` 缺少 output_schema/output_mapping/return 输出说明"
            target = {"kind": "capability", "capability": label}
            if cap.confirmed:
                cap_errors.append(msg)
                _capability_error(internal_section, code="capability_output_missing", message=msg, target=target)
            else:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_output_missing",
                    message=msg,
                    target=target,
                )

        input_props = ((cap.input_schema or {}).get("properties") or {})
        flat_nodes = _iter_capability_nodes(cap.nodes or [])
        cap_node_ids = {str(n.get("id") or "") for n in flat_nodes if isinstance(n, dict) and n.get("id")}
        return_sources = [
            f"{sid}({step_by_id[sid].method} {step_by_id[sid].path or step_by_id[sid].url})"
            for sid in node_step_ids
            if sid in step_by_id
        ]
        has_return_node = any(isinstance(n, dict) and n.get("type") == "return" for n in flat_nodes)
        for node in flat_nodes:
            if not isinstance(node, dict):
                cap_errors.append(f"Capability `{label}` 包含非法节点")
                continue
            node_type = str(node.get("type") or "")
            node_id = str(node.get("id") or node_type or "<node>")
            if node_type not in allowed_nodes:
                cap_errors.append(f"Capability `{label}` 节点 `{node_id}` 类型 `{node_type}` 不支持")
            if node_type == "call":
                call_step_id = str(node.get("step_id") or "")
                call_usage = str(node.get("usage") or "")
                if call_usage == "option_source" and not call_step_id:
                    pass
                elif call_step_id not in step_by_id:
                    cap_errors.append(f"Capability `{label}` call 节点 `{node_id}` 未绑定有效接口步骤")
            if node_type == "condition":
                expr = str(node.get("condition") or node.get("check") or node.get("expr") or "")
                if not expr:
                    cap_errors.append(f"Capability `{label}` condition 节点 `{node_id}` 缺少 condition/check 表达式")
                else:
                    for ref in _capability_input_refs(expr):
                        if ref not in input_props:
                            cap_errors.append(f"Capability `{label}` condition 节点 `{node_id}` 引用的输入 `{ref}` 不存在")
                if not any(isinstance(node.get(k), list) and node.get(k) for k in ("then", "steps", "children", "otherwise", "else")):
                    cap_warnings.append(f"Capability `{label}` condition 节点 `{node_id}` 没有任何分支步骤")
            if node_type == "foreach":
                items = str(node.get("items") or "")
                if not items:
                    cap_errors.append(f"Capability `{label}` foreach 节点 `{node_id}` 缺少 items 数组来源")
                elif items.startswith("input."):
                    field = items.split(".", 1)[1].split(".", 1)[0]
                    schema = input_props.get(field) or {}
                    if field not in input_props:
                        cap_errors.append(f"Capability `{label}` foreach 节点 `{node_id}` 引用的输入 `{field}` 不存在")
                    elif schema.get("type") != "array":
                        cap_errors.append(f"Capability `{label}` foreach 节点 `{node_id}` 的输入 `{field}` 不是数组")
                    item_props, _item_required = _capability_schema_array_item_props(cap.input_schema or {}, field)
                    child_step_ids = {
                        str(n.get("step_id") or "")
                        for n in _iter_capability_nodes(_capability_child_nodes(node, "steps", "children"))
                        if isinstance(n, dict) and n.get("type") == "call"
                    }
                    if child_step_ids:
                        root_inputs = set(input_props.keys())
                        for child_sid in child_step_ids:
                            child_step = step_by_id.get(child_sid)
                            for param in (child_step.params if child_step else []):
                                if not _param_requires_caller_input(param):
                                    continue
                                pname = param.key or param.path
                                item_shaped = str(param.path or "").startswith("[") or bool(child_step and _looks_batch_step(child_step))
                                if pname not in item_props and (pname not in root_inputs or item_shaped):
                                    _capability_warning(
                                        internal_section,
                                        warnings,
                                        code="capability_loop_item_field_missing",
                                        message=f"Capability `{label}` foreach `{node_id}` 的条目 schema 未覆盖必填字段 `{pname}`",
                                        target={"kind": "capability_node", "capability": label, "node_id": node_id, "field": pname},
                                    )
                if not isinstance(node.get("steps"), list) and not any(
                    isinstance(n, dict) and n.get("type") == "call" for n in _iter_capability_nodes([node])
                ):
                    cap_warnings.append(f"Capability `{label}` foreach 节点 `{node_id}` 没有子步骤，运行期将退化为重复执行能力闭包")
            if node_type == "map":
                source = str(node.get("source") or "")
                target = str(node.get("target") or "")
                if not source or not target:
                    cap_errors.append(f"Capability `{label}` map 节点 `{node_id}` 缺少 source 或 target")
                elif not _capability_value_ref_exists(
                    source,
                    input_props=input_props,
                    cap_node_ids=cap_node_ids,
                    step_by_id=step_by_id,
                    cap_step_id_set=cap_step_id_set,
                ):
                    cap_errors.append(f"Capability `{label}` map 节点 `{node_id}` 来源 `{source}` 不存在")
                elif target.startswith("input."):
                    field = target.split(".", 1)[1].split(".", 1)[0]
                    if field not in input_props:
                        cap_errors.append(f"Capability `{label}` map 节点 `{node_id}` 目标输入 `{field}` 不存在")
                elif not target.startswith(("var.", "computed.", "loop.", "item.", "node.")):
                    head = target.split(".", 1)[0]
                    if head in cap_step_id_set:
                        tail = target.split(".", 1)[1] if "." in target else ""
                        if not _capability_step_param_exists(step_by_id.get(head), tail):
                            cap_errors.append(f"Capability `{label}` map 节点 `{node_id}` 目标 `{target}` 找不到接口字段")
                    else:
                        cap_warnings.append(f"Capability `{label}` map 节点 `{node_id}` 目标 `{target}` 无法静态确认，将按计算变量处理")
            if node_type == "return" and not (node.get("value") or node.get("from") or node.get("path")):
                hint = f"，可选来源: {return_sources[-1]}" if return_sources else "，当前能力没有有效 call 步骤可返回"
                cap_errors.append(f"Capability `{label}` return 节点 `{node_id}` 缺少返回来源{hint}")
            if node_type == "return" and node.get("from"):
                ref = str(node.get("from") or "")
                if ref and ref not in step_by_id and ref not in cap_node_ids and not ref.startswith(("input.", "var.", "node.")):
                    hint = f"；可选来源: {', '.join(return_sources[-3:])}" if return_sources else "；当前能力没有有效 call 步骤"
                    cap_errors.append(f"Capability `{label}` return 节点 `{node_id}` 引用的来源 `{ref}` 不存在{hint}")
                if ref == node_id:
                    hint = f"；可选来源: {return_sources[-1]}" if return_sources else ""
                    cap_errors.append(f"Capability `{label}` return 节点 `{node_id}` 不能引用自身作为返回来源{hint}")
        for idx, pre in enumerate(cap.preconditions or []):
            if not isinstance(pre, dict):
                cap_errors.append(f"Capability `{label}` preconditions[{idx}] 不是对象")
                continue
            expr = str(pre.get("check") or pre.get("condition") or pre.get("expr") or "")
            if not expr:
                cap_errors.append(f"Capability `{label}` preconditions[{idx}] 缺少 check/condition 表达式")
                continue
            input_refs = re.findall(r"\binput\.([a-zA-Z_][\w]*)", expr)
            bare_refs = []
            if re.fullmatch(r"[a-zA-Z_][\w]*\s*(?:==|!=|>=|<=|>|<).+", expr):
                bare_refs.append(re.split(r"==|!=|>=|<=|>|<", expr, 1)[0].strip())
            for ref in [*input_refs, *bare_refs]:
                if ref and ref not in input_props:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_precondition_input_missing",
                        message=f"Capability `{label}` 前置条件引用的输入 `{ref}` 不在 input_schema 中",
                        target={"kind": "capability_precondition", "capability": label, "index": idx, "input": ref},
                    )
        if cap.confirmed and cap.nodes and not cap.output_mapping and not has_return_node:
            cap_warnings.append(f"Capability `{label}` 已确认但没有 return 节点，外部调用只能拿到底层原始响应")

        if internal_section.get("errors"):
            capability_internal.setdefault("errors", []).extend(internal_section.get("errors") or [])

        if not cap.confirmed:
            errors.extend(cap_errors)
            warnings.extend(cap_warnings)
            capability_reports.append({
                "name": cap.name,
                "kind": cap.kind,
                "confirmed": cap.confirmed,
                "step_ids": node_step_ids,
                "request_keys": cap_request_keys,
                "nodes": cap.nodes,
                "errors": cap_errors,
                "warnings": cap_warnings,
            })
            capability_internal["capabilities"].append(internal_section)
            continue

        if cap.kind in {"submit", "submit_batch"} and not any((s.method or "").upper() in _WRITE_METHODS for s in cap_steps):
            cap_errors.append(f"Capability `{label}` 已确认提交能力，但没有关联写请求步骤")
        if cap.kind == "query_status" and not (cap_steps or cap.evidence):
            cap_errors.append(f"Capability `{label}` 已确认状态查询能力，但缺少读接口步骤或 RequestFacts 证据")
        if cap.kind == "list_options":
            fields = (((cap.input_schema or {}).get("properties") or {}).get("field") or {}).get("enum") or []
            if not fields and not cap.evidence:
                cap_errors.append(f"Capability `{label}` 已确认候选项查询能力，但缺少字段清单或候选源证据")
        errors.extend(cap_errors)
        warnings.extend(cap_warnings)
        capability_reports.append({
            "name": cap.name,
            "kind": cap.kind,
            "confirmed": cap.confirmed,
            "step_ids": node_step_ids,
            "request_keys": cap_request_keys,
            "nodes": cap.nodes,
            "errors": cap_errors,
            "warnings": cap_warnings,
        })
        capability_internal["capabilities"].append(internal_section)
    dedup_checked = list({r["request_key"]: r for r in checked_requests}.values())
    dedup_manual = list({r["request_key"]: r for r in checked_manual_requests}.values())
    cap_by_ref: dict[str, FlowCapability] = {}
    for cap in caps:
        for key in {cap.name, cap.capability_id}:
            if key:
                cap_by_ref[str(key)] = cap
    for relation in spec.capability_relations or []:
        from_key = str(relation.from_capability or "")
        to_key = str(relation.to_capability or "")
        from_cap = cap_by_ref.get(from_key)
        to_cap = cap_by_ref.get(to_key)
        requires_fields = _capability_relation_requires_fields(relation)
        from_type = _capability_field_type(from_cap, relation.from_output, direction="output") if from_cap and requires_fields else ""
        to_type = _capability_field_type(to_cap, relation.to_input, direction="input") if to_cap and requires_fields else ""
        compatible = not requires_fields or _capability_types_compatible(from_type, to_type)
        cardinality = str(relation.cardinality or "")
        transform_owner = str(relation.transform_owner or "")
        cardinality_valid = cardinality in {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
        transform_owner_valid = transform_owner in {"caller", "skill", "runtime"}
        relation_entry = {
            "relation_id": relation.relation_id,
            "type": relation.type,
            "from_capability": relation.from_capability,
            "from_output": relation.from_output,
            "from_exists": from_cap is not None,
            "from_output_type": from_type,
            "to_capability": relation.to_capability,
            "to_input": relation.to_input,
            "to_exists": to_cap is not None,
            "to_input_type": to_type,
            "type_compatible": compatible,
            "requires_field_mapping": requires_fields,
            "cardinality": cardinality,
            "cardinality_valid": cardinality_valid,
            "transform_owner": transform_owner,
            "transform_owner_valid": transform_owner_valid,
        }
        capability_relations["relations"].append(relation_entry)
        if not cardinality_valid or not transform_owner_valid:
            invalid_parts = []
            if not cardinality_valid:
                invalid_parts.append(f"cardinality={cardinality!r}")
            if not transform_owner_valid:
                invalid_parts.append(f"transform_owner={transform_owner!r}")
            msg = f"Capability relation `{relation.relation_id}` 编排契约无效: {', '.join(invalid_parts)}"
            issue = {
                "code": "capability_relation_contract_invalid",
                "message": msg,
                "target": {"kind": "capability_relation", "relation_id": relation.relation_id},
            }
            if relation.confirmed:
                capability_relations.setdefault("errors", []).append(issue)
                errors.append(msg)
            else:
                _capability_warning(
                    capability_relations, warnings,
                    code=issue["code"], message=msg, target=issue["target"],
                )
        elif from_cap is None or to_cap is None:
            msg = f"Capability relation `{relation.relation_id}` 指向不存在的 from/to capability"
            if relation.confirmed:
                capability_relations.setdefault("errors", []).append({
                    "code": "capability_relation_endpoint_missing",
                    "message": msg,
                    "target": {"kind": "capability_relation", "relation_id": relation.relation_id},
                })
                errors.append(msg)
            else:
                _capability_warning(
                    capability_relations,
                    warnings,
                    code="capability_relation_endpoint_missing",
                    message=msg,
                    target={"kind": "capability_relation", "relation_id": relation.relation_id},
                )
        elif requires_fields and (not from_type or not to_type):
            msg = f"Capability relation `{relation.relation_id}` 的 output/input 字段缺少可解析类型"
            if relation.confirmed:
                capability_relations.setdefault("errors", []).append({
                    "code": "capability_relation_field_missing",
                    "message": msg,
                    "target": {"kind": "capability_relation", "relation_id": relation.relation_id},
                })
                errors.append(msg)
            else:
                _capability_warning(
                    capability_relations,
                    warnings,
                    code="capability_relation_field_missing",
                    message=msg,
                    target={"kind": "capability_relation", "relation_id": relation.relation_id},
                )
        elif requires_fields and not compatible:
            msg = f"Capability relation `{relation.relation_id}` output/input 类型不兼容: {from_type} -> {to_type}"
            if relation.confirmed:
                capability_relations.setdefault("errors", []).append({
                    "code": "capability_relation_type_mismatch",
                    "message": msg,
                    "target": {"kind": "capability_relation", "relation_id": relation.relation_id},
                })
                errors.append(msg)
            else:
                _capability_warning(
                    capability_relations,
                    warnings,
                    code="capability_relation_type_mismatch",
                    message=msg,
                    target={"kind": "capability_relation", "relation_id": relation.relation_id},
                )
    for cap in caps:
        if cap.confirmed and not cap.requires_human_confirm:
            continue
        cap_ref = cap.name or cap.capability_id
        message = f"Capability `{cap_ref}` 是未确认的公开能力；请确认该能力或从发布范围移除"
        _capability_warning(
            skill_level,
            warnings,
            code="unconfirmed_public_capability",
            message=message,
            target={"kind": "capability", "capability": cap_ref},
        )
    confirmed_caps = [c for c in caps if c.confirmed]
    strict_skill_level = bool((spec.meta or {}).get("publish_gate") or (spec.meta or {}).get("strict_skill_level"))
    if confirmed_caps:
        skill_issues: list[tuple[str, str]] = []
        if not str(spec.business_description or "").strip():
            skill_issues.append(("skill_description_missing", "Skill 缺少面向调用方的整体说明"))
        # Multiple independent capabilities require explicit selection, not a
        # fabricated call order or relation. A relation is required only when
        # a concrete output-to-input mapping exists and is validated above.
        failure_text = " ".join([
            str((spec.meta or {}).get("failure_handling") or ""),
            str(spec.business_description or ""),
            *[str(x) for cap in confirmed_caps for x in (cap.skill_responsibilities or [])],
            *[str(x) for cap in confirmed_caps for x in (cap.preconditions or [])],
        ])
        if not re.search(r"失败|错误|异常|重试|failed|error|exception", failure_text, re.I):
            skill_issues.append(("skill_failure_handling_missing", "Skill 缺少失败处理或异常边界说明"))
        for code, message in skill_issues:
            target = {"kind": "flow", "flow_id": spec.flow_id}
            if strict_skill_level:
                entry = {"code": code, "message": message, "target": target}
                skill_level.setdefault("errors", []).append(entry)
                errors.append(message)
            else:
                _capability_warning(skill_level, warnings, code=code, message=message, target=target)
    capability_internal["passed"] = not capability_internal["errors"]
    capability_relations["passed"] = not capability_relations["errors"]
    skill_level["passed"] = not skill_level["errors"]
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "capabilities": capability_reports,
        "checked_requests": dedup_checked,
        "checked_manual_requests": dedup_manual,
        "unused_high_confidence_requests": high_conf_unused,
        "capability_internal": capability_internal,
        "capability_relations": capability_relations,
        "skill_level": skill_level,
        "materialization_integrity": materialization_integrity,
    }

_PENDING_FLOW_SPEC_HELPERS = {'_ROUTING_FIELD_RE': 'dano.execution.page.capability_contracts', '_capability_child_nodes': 'dano.execution.page.capability_refs', '_capability_confirmation_hash': 'dano.execution.page.capability_views', '_capability_execute_record_selector': 'dano.execution.page.capability_contracts', '_capability_field_has_valid_source': 'dano.execution.page.capability_contracts', '_capability_field_looks_internal': 'dano.execution.page.capability_contracts', '_capability_field_type': 'dano.execution.page.capability_contracts', '_capability_input_refs': 'dano.execution.page.capability_contracts', '_capability_is_batch': 'dano.execution.page.capability_contracts', '_capability_node_step_ids': 'dano.execution.page.capability_refs', '_capability_ref_key': 'dano.execution.page.capability_contracts', '_capability_relation_requires_fields': 'dano.execution.page.capability_contracts', '_capability_request_indexes': 'dano.execution.page.capability_refs', '_capability_response_path_exists': 'dano.execution.page.capability_contracts', '_capability_schema_array_item_props': 'dano.execution.page.capability_io', '_capability_step_param_exists': 'dano.execution.page.capability_contracts', '_capability_types_compatible': 'dano.execution.page.capability_contracts', '_capability_value_ref_exists': 'dano.execution.page.capability_contracts', '_eligible_business_write_fact': 'dano.execution.page.capability_contracts', '_enum_map_covers_recorded_value': 'dano.execution.page.flow_release', '_enum_options_look_value_only': 'dano.execution.page.flow_release', '_incomplete_page_enum_is_executable': 'dano.execution.page.flow_release', '_iter_capability_nodes': 'dano.execution.page.capability_nodes', '_looks_batch_step': 'dano.execution.page.capability_kinds', '_manual_enum_mapping_complete': 'dano.execution.page.flow_release', '_normalize_capability_references': 'dano.execution.page.capability_nodes', '_retired_capability_step_ids': 'dano.execution.page.capability_refs', '_schema_path_exists': 'dano.execution.page.capability_io', '_step_request_key': 'dano.execution.page.capability_refs', '_step_request_signature_key': 'dano.execution.page.capability_contracts', '_strip_body_prefix': 'dano.execution.page.flow_spec_core.normalization', '_sync_capability_io_schemas': 'dano.execution.page.capability_io', 'ALLOWED_CAPABILITY_KINDS': 'dano.execution.page.capability_kinds', 'ensure_recorded_goal': 'dano.execution.page.flow_materialization.builder'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
