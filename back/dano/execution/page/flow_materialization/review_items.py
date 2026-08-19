"""Stage 5 review-item projection after mechanical field contracts."""
from __future__ import annotations

from typing import Any
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    ReviewItem,
)
from dano.execution.page.flow_spec_core.normalization import (
    _FLOW_PATH_MISSING,
    _flow_path_lookup,
    _strip_body_prefix,
)
from dano.execution.page.flow_materialization.request_steps import (
    _dedupe_step_params,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _field_source_configuration_advice,
)
from dano.execution.page.recording_facts import (
    _request_path,
)
from dano.execution.page.flow_spec_core.request_contract import (
    flow_spec_user_params,
)


def _review_id(item_type: str, target: dict[str, Any]) -> str:
    parts = [
        item_type,
        str(target.get("step_id") or ""),
        str(target.get("path") or ""),
        str(target.get("link_id") or ""),
        str(target.get("request_index") or ""),
        str(target.get("capability") or target.get("capability_name") or target.get("capability_id") or ""),
        str(target.get("field") or ""),
    ]
    raw = "|".join(parts)
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower()
    return f"review_{safe[:96]}" if safe else f"review_{item_type}"


def _review_item(
    item_type: str,
    *,
    severity: str,
    title: str,
    target: dict[str, Any],
    current_guess: str = "",
    suggested_action: str = "",
    reason: str = "",
    confidence: float = 0.0,
    blocking: bool = False,
    ignorable: bool = True,
) -> ReviewItem:
    return ReviewItem(
        id=_review_id(item_type, target),
        type=item_type,
        severity=severity,
        title=title,
        target=target,
        current_guess=current_guess,
        suggested_action=suggested_action,
        reason=reason,
        confidence=confidence,
        blocking=blocking,
        ignorable=ignorable,
    )


def build_review_items(spec: FlowSpec) -> list[ReviewItem]:
    """把 FlowSpec 中的低置信/高风险判断整理成人工确认项。"""
    items: list[ReviewItem] = []
    active_step_ids = _active_capability_step_ids(spec)
    visible_steps = [
        step for step in spec.steps
        if active_step_ids is None or step.step_id in active_step_ids
    ]
    step_ids = {s.step_id for s in visible_steps}
    steps_by_id = {s.step_id: s for s in visible_steps}
    visible_request_indexes = {
        str(step.source_meta.get("request_index"))
        for step in visible_steps
        if step.source_meta.get("request_index") is not None
    }
    visible_request_paths = {
        _request_path({"path": step.path or step.url})
        for step in visible_steps
        if step.path or step.url
    }
    confirmed_dependency_sources = {
        link.source_step_id for link in spec.links
        if link.confirmed and link.source_step_id in step_ids and link.target_step_id in step_ids
    }

    # 来源建议属于能力合同的编辑反馈。尚未生成能力时，字段还没有发布
    # 边界和可定位的能力锚点，不能提前制造“待处理”告警。能力存在后仍
    # 覆盖未归属步骤，但用户明确删除能力时，其原步骤已退出编辑范围。
    if spec.capabilities:
        removed_step_ids = _retired_capability_step_ids(spec)
        for st in spec.steps:
            if st.step_id in removed_step_ids:
                continue
            for p in st.params:
                target = {
                    "kind": "param",
                    "step_id": st.step_id,
                    "step_name": st.name,
                    "path": p.path,
                    "key": p.key,
                    "param_type": p.type,
                    "category": p.category,
                    "source_kind": p.source_kind or "unknown",
                }
                guess = f"{p.category}/{p.source_kind}"
                source_unknown = str(p.source_kind or "").strip().lower() in {"", "unknown"}
                source_advice = _field_source_configuration_advice(p)
                if source_unknown:
                    items.append(_review_item(
                        "field_source_unknown",
                        severity="medium",
                        title=f"字段 {p.path} 的来源尚未识别",
                        target=target,
                        current_guess=guess,
                        suggested_action="configure_or_ignore_field_source",
                        # reason=(
                        #     "系统会保留当前类型、分类和来源组合，不会自动改写或阻止保存、优化、发布；"
                        #     "可补充明确来源，或确认当前人工配置后忽略此提示"
                        # ),
                        confidence=p.confidence,
                        blocking=False,
                        ignorable=True,
                    ))
                elif source_advice:
                    items.append(_review_item(
                        "field_source_incomplete",
                        severity="medium",
                        title=f"字段 {p.path} 的来源配置不完整",
                        target=target,
                        current_guess=guess,
                        suggested_action="configure_or_ignore_field_source",
                        reason=(
                            f"{source_advice}；系统会保留当前人工配置，"
                            "该提示可忽略且不会阻止保存、优化、发布"
                        ),
                        confidence=p.confidence,
                        blocking=False,
                        ignorable=True,
                    ))

    for st in visible_steps:
        for p in st.params:
            target = {
                "kind": "param",
                "step_id": st.step_id,
                "step_name": st.name,
                "path": p.path,
                "key": p.key,
                "param_type": p.type,
                "category": p.category,
                "source_kind": p.source_kind or "unknown",
            }
            guess = f"{p.category}/{p.source_kind}"

            source_unknown = str(p.source_kind or "").strip().lower() in {"", "unknown"}

            source_advice = _field_source_configuration_advice(p)

            if p.need_human_confirm and not source_unknown and not source_advice:
                items.append(_review_item(
                    "field_category",
                    severity="medium",
                    title=f"确认字段 {p.path} 的分类和来源",
                    target=target,
                    current_guess=guess,
                    suggested_action="confirm_field_source",
                    reason=p.reason or "该字段分类由规则推断，建议人工确认",
                    confidence=p.confidence,
                ))

            if p.category == "system_const" and p.exposed_to_user:
                items.append(_review_item(
                    "system_const_exposed",
                    severity="high",
                    title=f"隐藏系统常量 {p.path}",
                    target=target,
                    current_guess=guess,
                    suggested_action="hide_system_const",
                    reason="系统常量不应作为普通 Skill 入参暴露给 agent 或最终用户",
                    confidence=p.confidence,
                ))

    for lk in spec.links:
        # A capability-scoped request may compile only links whose two
        # endpoints are inside that capability's verified closure.  Including
        # a half-owned pending link made its outside endpoint look like a
        # missing step and blocked an otherwise valid ability.
        if active_step_ids is not None and not (
            lk.source_step_id in active_step_ids and lk.target_step_id in active_step_ids
        ):
            continue
        source_step = steps_by_id.get(lk.source_step_id)
        target_step = steps_by_id.get(lk.target_step_id)
        source_label = f"{source_step.name or source_step.path or source_step.url}" if source_step else lk.source_step_id
        target_label = f"{target_step.name or target_step.path or target_step.url}" if target_step else lk.target_step_id
        link_label = f"{source_label}.{lk.source_path} -> {target_label}.{lk.target_path}"
        target = {
            "kind": "link",
            "link_id": lk.link_id,
            "source_step_id": lk.source_step_id,
            "source_path": lk.source_path,
            "target_step_id": lk.target_step_id,
            "target_path": lk.target_path,
        }
        if lk.source_step_id not in step_ids or lk.target_step_id not in step_ids:
            items.append(_review_item(
                "broken_link",
                severity="high",
                title=f"修复断开的接口依赖 {link_label}",
                target=target,
                current_guess="invalid_link",
                suggested_action="fix_or_remove_link",
                reason="该 link 指向不存在的步骤，执行计划无法可靠生成",
                confidence=lk.confidence,
            ))
            continue

        source_path = lk.source_tokens or lk.source_path
        if source_step and source_step.response_json is not None and _flow_path_lookup(source_step.response_json, source_path) is _FLOW_PATH_MISSING:
            items.append(_review_item(
                "link_source_missing",
                severity="high",
                title=f"修复接口依赖来源 {source_label}.{lk.source_path}",
                target=target,
                current_guess="missing_source_path",
                suggested_action="fix_link_source",
                reason="该 link 的 source_path 在上游响应样例里不存在，运行期无法取到要注入的值",
                confidence=lk.confidence,
            ))

        target_path = _strip_body_prefix(lk.target_path)
        if target_step and target_path and not any(p.path == target_path or p.path == lk.target_path for p in target_step.params):
            items.append(_review_item(
                "link_target_missing",
                severity="high",
                title=f"修复接口依赖目标 {target_label}.{lk.target_path}",
                target=target,
                current_guess="missing_target_path",
                suggested_action="fix_link_target",
                reason="该 link 的 target_path 不在目标步骤字段中，运行期可能无法注入",
                confidence=lk.confidence,
            ))

        if not lk.confirmed:
            items.append(_review_item(
                "link_confirmation",
                severity="high",
                title=f"确认接口依赖 {link_label}",
                target=target,
                current_guess="previous_response",
                suggested_action="confirm_link",
                reason=lk.reason or "该 link 由响应值与请求值匹配自动生成，需要人工确认",
                confidence=lk.confidence,
            ))

    for role in spec.meta.get("request_roles") or []:
        role_index = str(role.get("index")) if role.get("index") is not None else ""
        role_path = _request_path({"path": str(role.get("path") or role.get("url") or "")})
        matched_step = next((
            step for step in visible_steps
            if (
                role_index
                and str(step.source_meta.get("request_index")) == role_index
            ) or (
                role_path
                and _request_path({"path": step.path or step.url}) == role_path
            )
        ), None)
        role_is_active = bool(
            matched_step
            or (role_index and role_index in visible_request_indexes)
            or (role_path and role_path in visible_request_paths)
        )
        confidence = float(role.get("confidence") or 0.0)
        needs_role_confirmation = bool(
            role.get("keep")
            and role.get("role") in {"business_get", "read_context"}
            and role_is_active
            and confidence < 0.9
            and not bool(matched_step and matched_step.source_meta.get("manual_added"))
            and not bool(matched_step and matched_step.source_meta.get("control_preflight_for_write"))
            and not bool(matched_step and matched_step.step_id in confirmed_dependency_sources)
        )
        if needs_role_confirmation:
            items.append(_review_item(
                "request_role",
                severity="medium",
                title=f"确认前置接口保留: {role.get('path') or role.get('url')}",
                target={
                    "kind": "request_role",
                    "request_index": role.get("index"),
                    "method": role.get("method"),
                    "path": role.get("path") or role.get("url"),
                },
                current_guess=str(role.get("role") or ""),
                suggested_action="confirm_request_role",
                reason=str(role.get("reason") or "该读接口被自动保留为流程前置步骤"),
                confidence=confidence,
            ))

    if visible_steps and not flow_spec_user_params(spec):
        items.append(_review_item(
            "no_user_param",
            severity="low",
            title="确认 Skill 是否不需要用户输入",
            target={"kind": "flow", "flow_id": spec.flow_id},
            current_guess="no_user_param",
            suggested_action="confirm_or_expose_param",
            reason="当前 FlowSpec 没有 user_param，发布后的 Skill 不会要求用户填写业务参数",
        ))

    if visible_steps and not any((st.success_rule for st in visible_steps)):
        items.append(_review_item(
            "success_rule_missing",
            severity="medium",
            title="补充成功判断规则",
            target={"kind": "flow", "flow_id": spec.flow_id},
            current_guess="missing_success_rule",
            suggested_action="add_success_rule",
            reason="未识别到明确 success_rule，运行期只能使用通用成功判断",
        ))

    deduped: dict[str, ReviewItem] = {}
    for item in items:
        existing = deduped.get(item.id)
        if existing is None or _severity_rank(item.severity) > _severity_rank(existing.severity):
            deduped[item.id] = item
    return list(deduped.values())


def _severity_rank(severity: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(severity, 0)


def refresh_review_items(spec: FlowSpec, *, prepared: bool = False) -> FlowSpec:
    """重建 review_items，并保留同 id 项的已解决状态。

    ID 是稳定 hash(target)，所以同一字段/同一依赖在重建前后 ID 不变，
    用户的 resolved 标记会随 ID 一起被复用，告警不会因为字段重渲染而复活。
    """
    for step in spec.steps:
        _dedupe_step_params(step)
    old_resolved: dict[str, bool] = {}
    legacy_source_resolved: dict[tuple[str, str, str], bool] = {}
    for item in spec.review_items:
        # id 已是 target 的稳定 hash；同字段前后 ID 一致，resolved 跟着保留。
        old_resolved.setdefault(item.id, item.resolved)
        # Preserve dismissals while migrating the two legacy runtime-only
        # source warnings to category-agnostic field source review items.
        legacy_type = {
            "runtime_var_source": "field_source_unknown",
            "runtime_var_missing_source": "field_source_incomplete",
        }.get(item.type)
        if legacy_type:
            target_key = (
                legacy_type,
                str(item.target.get("step_id") or ""),
                str(item.target.get("path") or ""),
            )
            legacy_source_resolved.setdefault(target_key, item.resolved)
    spec.review_items = _generated_review_items(spec, prepared=prepared)
    for item in spec.review_items:
        if item.id in old_resolved:
            item.resolved = old_resolved[item.id]
        elif item.type in {"field_source_unknown", "field_source_incomplete"}:
            target_key = (
                item.type,
                str(item.target.get("step_id") or ""),
                str(item.target.get("path") or ""),
            )
            if target_key in legacy_source_resolved:
                item.resolved = legacy_source_resolved[target_key]
    return spec

_PENDING_FLOW_SPEC_HELPERS = {'_active_capability_step_ids': 'dano.execution.page.capability_refs', '_retired_capability_step_ids': 'dano.execution.page.capability_refs', '_generated_review_items': 'dano.execution.page.flow_release'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
