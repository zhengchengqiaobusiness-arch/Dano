"""Composed FlowSpec validation across materialized, capability, and release inputs."""
from __future__ import annotations

from typing import Any
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    ParamField,
)
from dano.execution.page.request_capture import (
    self_check,
)
from dano.execution.page.flow_release import (
    _PUBLISH_BLOCKING_REVIEW_TYPES,
    _compiled_contract_issue_groups,
    _diagnostic_publish_findings,
    _enum_map_covers_recorded_value,
    _enum_mapping_issues,
    _enum_options_look_value_only,
    _field_source_review_issues,
    _param_looks_exposed_internal_value,
    _publish_issue_groups,
    prepare_flow_spec_for_publish,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _field_source_configuration_advice,
)
from dano.execution.page.capability_contracts import (
    _is_business_query_step,
    _params_can_share_caller_key,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_exposed_to_caller,
)
from dano.execution.page.flow_spec_core.request_contract import (
    dry_run_flow_spec,
    flow_spec_required_params,
    flow_spec_to_api_request,
    flow_spec_user_params,
)
from dano.execution.page.flow_materialization.builder import (
    refresh_review_items,
)


def validate_flow_spec(
    spec: FlowSpec,
    *,
    _prepared: bool = False,
    _projection: bool = False,
) -> dict:
    from dano.execution.page.repair_ops import collect_repair_findings

    # 校验只面对规范化后的当前事实。字段、接口顺序或能力范围改变后产生的旧
    # input/map/return/link 由同步层确定性清理，不能继续作为“用户待处理”告警。
    if not _prepared:
        spec = prepare_flow_spec_for_publish(spec)
    for capability in spec.capabilities or []:
        capability.nodes = _sanitize_capability_nodes(spec, capability)
    spec = _prune_empty_capabilities(spec)
    _normalize_capability_references(spec)
    errors: list[str] = []
    warnings: list[str] = []
    suggestions: list[str] = []
    active_step_ids = _active_capability_step_ids(spec)
    active_steps = [
        step for step in spec.steps
        if active_step_ids is None or step.step_id in active_step_ids
    ]
    review_items = (
        list(spec.review_items)
        if _projection
        else refresh_review_items(
            spec.model_copy(deep=True), prepared=True,
        ).review_items
    )
    blocking_reviews = [
        item for item in review_items
        if item.severity == "high" and not item.resolved and item.type in _PUBLISH_BLOCKING_REVIEW_TYPES
    ]
    # Review items and capability/field classifications are generator output,
    # not publish policy.  Keep them available to the editor as suggestions but
    # never place them in the publish issue list: doing so made a model guess
    # look like a mandatory system rule.
    suggestions.extend([f"生成建议: {item.title}" for item in blocking_reviews])
    diag_errors, diag_warnings = _diagnostic_publish_findings(spec)
    suggestions.extend([*diag_errors, *diag_warnings])
    capability_validation = _capability_validation_report(spec, prepared=True)
    capability_errors = list(capability_validation.get("errors") or [])
    capability_warnings = list(capability_validation.get("warnings") or [])
    # Capability validation describes the executable public contract.  A
    # malformed boundary or illegal membership cannot be repaired by the
    # lower-level request builder, so it is a hard publish error.
    errors.extend(capability_errors)
    suggestions.extend(capability_warnings)
    by_step_id = {step.step_id: step for step in spec.steps}
    for capability in spec.capabilities or []:
        cap_label = capability.title or capability.name or capability.capability_id
        caller_params = [
            param
            for step_id in _capability_node_step_ids(capability)
            for param in (by_step_id.get(step_id).params if by_step_id.get(step_id) else [])
            if _param_exposed_to_caller(param)
        ]
        caller_by_key: dict[str, list[ParamField]] = {}
        for param in caller_params:
            caller_by_key.setdefault(str(param.key or param.path or ""), []).append(param)
        for field_name, duplicates in caller_by_key.items():
            if len(duplicates) > 1 and any(
                not _params_can_share_caller_key(duplicates[0], other)
                for other in duplicates[1:]
            ):
                suggestions.append(f"Capability `{cap_label}` 输入字段 `{field_name}` 同名但对应不同请求字段，建议重命名或解除锁定后自动消歧")
        for field_name, field_schema in (capability.input_schema.get("properties") or {}).items():
            if isinstance(field_schema, dict) and field_schema.get("x-dano-conflicts"):
                suggestions.append(f"Capability `{cap_label}` 输入字段 `{field_name}` 在多个接口中类型或路径冲突")
        if capability.kind == "query_status":
            cap_steps = [by_step_id[sid] for sid in _capability_node_step_ids(capability) if sid in by_step_id]
            if cap_steps and not any(_is_business_query_step(step) for step in cap_steps):
                suggestions.append(f"Capability `{cap_label}` 没有返回业务记录/状态的查询接口，仅包含配置或前置接口")
    api_request, build_errors = flow_spec_to_api_request(
        spec,
        _prepared=True,
        _include_capability_contracts=not _projection,
    )
    errors.extend(build_errors)
    if not flow_spec_user_params(spec):
        suggestions.append("FlowSpec 没有 user_param，发布后的 Skill 不会要求用户输入参数")
    for st in active_steps:
        select_by_path = {s.path: s for s in st.selects if s.path}
        select_by_param = {s.param: s for s in st.selects if s.param}
        for p in st.params:
            enum_contract_error = _capability_param_enum_issue(p)
            if enum_contract_error:
                suggestions.append(f"枚举字段 `{p.key or p.path}` {enum_contract_error}")
            enum_contract_warning = _capability_param_enum_warning(p)
            if enum_contract_warning:
                suggestions.append(f"枚举字段 `{p.key or p.path}` {enum_contract_warning}")
            source_advice = _field_source_configuration_advice(p)
            if source_advice:
                suggestions.append(source_advice)
            if p.category == "runtime_var" and p.source_kind == "unknown":
                suggestions.append(f"字段 `{p.path}` 被判为 runtime_var，但来源仍需确认")
            if p.category == "system_const" and p.exposed_to_user:
                suggestions.append(f"字段 `{p.path}` 是 system_const，但仍暴露给用户")
            if p.source_kind == "api_option":
                sel = select_by_path.get(p.path) or select_by_param.get(p.key)
                if sel and sel.source_url and (sel.source_method or "GET").upper() not in {"GET", "HEAD"} and sel.source_role not in {
                    "business_get", "read_context", "read_option",
                }:
                    suggestions.append(
                        f"字段 `{p.key or p.path}` 的接口选项源 `{sel.source_method} {sel.source_url}` "
                        "未被识别为只读接口，运行期调用可能产生副作用"
                    )
            has_executable_api_options = p.source_kind == "api_option"
            if not has_executable_api_options and _param_looks_exposed_internal_value(p):
                suggestions.append(
                    f"字段 `{p.key or p.path}` 看起来是内部 ID/短码/空标识，不能直接暴露给用户；"
                    "请改为接口枚举映射或系统常量"
                )
            if (
                p.type in {"enum", "list-enum"}
                and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
                and p.enum_options
                and not _enum_map_covers_recorded_value(p)
            ):
                suggestions.append(
                    f"枚举字段 `{p.key or p.path}` 当前提交值 `{p.value}` 没有完整 label→value 映射，"
                    "请补充真实选项值映射或重新录制到字典接口"
                )
            if (
                p.type in {"enum", "list-enum"}
                and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
                and p.enum_options
                and _enum_options_look_value_only(p)
            ):
                suggestions.append(
                    f"枚举字段 `{p.key or p.path}` 的候选看起来全是内部值/短码，"
                    "不能作为用户可选项导出；请填写 `显示名=真实值`（如 `病假=2`）或重新录制真实下拉"
                )
    for lk in spec.links:
        if active_step_ids is not None and not (
            lk.source_step_id in active_step_ids and lk.target_step_id in active_step_ids
        ):
            continue
        if not lk.confirmed:
            suggestions.append(f"链接 `{lk.link_id}` 尚未人工确认")
    if active_steps and not any((st.success_rule for st in active_steps)):
        suggestions.append("未识别到明确 success_rule，运行期只能使用通用成功判断")
    self_check_errors: list[str] = []
    compiled_issue_groups: dict[str, list[dict[str, Any]]] = {}
    if api_request is not None:
        self_check_errors = self_check(api_request)
        suggestions.extend(self_check_errors)
        repair_findings = collect_repair_findings(api_request)
        compiled_issue_groups = _compiled_contract_issue_groups(
            spec,
            api_request,
            repair_findings,
            resolved_review_ids={item.id for item in review_items if item.resolved},
        )
        # 系统化:session_constant 仅当对应字段**真的被识别为 system_const/constant** 时才算发布阻断;
        # 若字段在 spec 里被标 runtime_var/unknown → 这部分错误让前端 review_items 兜底,
        # 避免一锅端。修复者应在 dynamic_run 时再注入。
        params_by_path: dict[str, dict] = {}
        for st in active_steps:
            for p in st.params:
                params_by_path[p.path] = p.model_dump() if hasattr(p, "model_dump") else p.dict()
        session_errors: list[str] = []
        for f in repair_findings:
            if f.get("kind") != "session_constant":
                continue
            detail = f.get("detail", "")
            path = (f.get("path") or [])
            path_str = ".".join(str(p) for p in path) if isinstance(path, (list, tuple)) else str(path)
            spec_field = params_by_path.get(path_str) or {}
            if spec_field.get("category") in ("runtime_var", "system_const"):
                continue
            session_errors.append(detail)
        suggestions.extend(session_errors)
    dry_run = dry_run_flow_spec(
        spec,
        _prepared=True,
        _compiled=(api_request, build_errors),
    )
    errors = list(dict.fromkeys(str(item) for item in errors if item))
    warnings = list(dict.fromkeys(str(item) for item in warnings if item))
    suggestions = list(dict.fromkeys(str(item) for item in suggestions if item))
    # Generated ReviewItems remain advisory. Field-source items are also
    # projected into the operator field-warning group for visibility, while
    # staying explicitly ignorable and outside the publish pass/fail decision.
    issue_groups = _publish_issue_groups(errors, warnings)
    field_source_issues = _field_source_review_issues(review_items)
    if field_source_issues:
        issue_groups.setdefault("field", []).extend(field_source_issues)
    enum_mapping_issues = _enum_mapping_issues(active_steps)
    if enum_mapping_issues:
        issue_groups.setdefault("field", []).extend(enum_mapping_issues)
    for group, items in compiled_issue_groups.items():
        issue_groups.setdefault(group, []).extend(items)
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "issue_groups": issue_groups,
        "dry_run": dry_run,
        "review_items": [item.model_dump() for item in review_items],
        "review_summary": {
            "total": len(review_items),
            "high": len([i for i in review_items if i.severity == "high"]),
            "medium": len([i for i in review_items if i.severity == "medium"]),
            "low": len([i for i in review_items if i.severity == "low"]),
        },
        "self_check": self_check_errors,
        "api_preview": {
            "workflow_steps": len(api_request.get("steps") or []) if api_request else 0,
            "method": api_request.get("method") if api_request else None,
            "path": api_request.get("path") if api_request else None,
            "params": flow_spec_user_params(spec),
            "required": flow_spec_required_params(spec),
        },
        "capability_preview": [
            {
                "name": c.name,
                "kind": c.kind,
                "step_ids": c.step_ids,
                "nodes": c.nodes,
                "confirmed": c.confirmed,
                "requires_human_confirm": c.requires_human_confirm,
                "confidence": c.confidence,
                "status": c.status,
            }
            for c in (spec.capabilities or [])
        ],
        "capability_validation": capability_validation,
    }

_PENDING_FLOW_SPEC_HELPERS = {'_sanitize_capability_nodes': 'dano.execution.page.capability_nodes', '_prune_empty_capabilities': 'dano.execution.page.capability_orchestration', '_normalize_capability_references': 'dano.execution.page.capability_nodes', '_active_capability_step_ids': 'dano.execution.page.capability_refs', '_capability_validation_report': 'dano.execution.page.capability_validation', '_capability_node_step_ids': 'dano.execution.page.capability_refs', '_capability_param_enum_issue': 'dano.execution.page.capability_validation', '_capability_param_enum_warning': 'dano.execution.page.capability_validation'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
