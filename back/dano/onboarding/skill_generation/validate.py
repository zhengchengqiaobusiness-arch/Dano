"""Deterministic SkillPlan checks. Model text is never treated as fact."""

from __future__ import annotations

from typing import Any

from dano.execution.page.flow_spec_core.models import FlowCapability, FlowSpec
from dano.onboarding.skill_generation.catalog import (
    capability_by_id,
    capability_ref,
    cardinality_compatible,
    confirmed_fixed_or_system_inputs,
    field_type,
    is_risk_write,
    is_write_capability,
    relation_is_usable,
    schema_has_field,
    schema_required,
    types_compatible,
    usable_relations,
)
from dano.onboarding.skill_generation.models import (
    PlanningMode,
    RouteBinding,
    SkillPlan,
    SkillRoute,
)


class PlanValidation:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.clarifications: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.errors and not self.clarifications

    def error(self, message: str) -> None:
        if message and message not in self.errors:
            self.errors.append(message)

    def clarify(self, message: str) -> None:
        if message and message not in self.clarifications:
            self.clarifications.append(message)


def validate_skill_plan(
    plan: SkillPlan,
    spec: FlowSpec,
    *,
    verified_capability_ids: set[str],
    expected_fingerprint: str = "",
) -> PlanValidation:
    result = PlanValidation()
    caps = capability_by_id(spec)
    verified = {str(item) for item in verified_capability_ids}
    selected = [str(item) for item in plan.selected_capability_ids if str(item)]
    if expected_fingerprint and plan.source_flow_fingerprint != expected_fingerprint:
        result.error("规划指纹与当前阶段7 FlowSpec 不一致")
    if not selected:
        result.error("selected_capability_ids 不能为空")
    for cap_id in selected:
        cap = caps.get(cap_id)
        if cap is None:
            result.error(f"所选能力不存在: {cap_id}")
            continue
        if capability_ref(cap) not in verified and cap.name not in verified and cap.capability_id not in verified:
            result.error(f"所选能力未通过阶段7验证: {cap_id}")
    if plan.planning_mode == PlanningMode.FIXED and len(plan.routes) != 1:
        result.error("固定规划模式只能有一条主要路线")
    if plan.planning_mode == PlanningMode.DYNAMIC and not plan.routes:
        result.error("动态规划模式至少需要一条有效路线")
    used_in_routes: set[str] = set()
    for route in plan.routes:
        used_in_routes.update(_validate_route(result, route, plan, spec, caps, verified))
    selected_set = set(selected)
    if used_in_routes - selected_set:
        extra = ", ".join(sorted(used_in_routes - selected_set))
        result.error(f"路线引用了未入选能力: {extra}")
    if selected_set - used_in_routes:
        extra = ", ".join(sorted(selected_set - used_in_routes))
        result.error(f"导出所选能力必须等于有效路线并集，未进入路线: {extra}")
    unused_ids = {item.capability_id for item in plan.unused_capabilities}
    for cap in spec.capabilities:
        cap_id = capability_ref(cap)
        if cap_id in selected_set or cap.name in selected_set:
            continue
        if cap_id in verified or cap.name in verified:
            if cap_id not in unused_ids and cap.name not in unused_ids:
                result.error(f"未使用能力必须记录原因: {cap_id}")
    return result


def _validate_route(
    result: PlanValidation,
    route: SkillRoute,
    plan: SkillPlan,
    spec: FlowSpec,
    caps: dict[str, FlowCapability],
    verified: set[str],
) -> set[str]:
    used: set[str] = set()
    if not str(route.when_to_use or "").strip():
        result.error(f"路线 {route.route_id} 缺少 when_to_use")
    if not str(route.done_when or "").strip():
        result.error(f"路线 {route.route_id} 缺少 done_when")
    if not route.examples:
        result.error(f"路线 {route.route_id} 缺少自然语言示例")
    if not route.capability_sequence:
        result.error(f"路线 {route.route_id} 没有能力调用顺序")
    if route.step_ids and len(route.step_ids) != len(route.capability_sequence):
        result.error(f"路线 {route.route_id} 的步骤身份数量与能力顺序不一致")
    if route.step_ids and len(set(route.step_ids)) != len(route.step_ids):
        result.error(f"路线 {route.route_id} 的步骤身份必须独立，不能只用能力名称作为引用键")
    repeated = {
        cap_id
        for cap_id in route.capability_sequence
        if route.capability_sequence.count(cap_id) > 1
    }
    write_caps: list[FlowCapability] = []
    for cap_id in route.capability_sequence:
        cap = caps.get(cap_id)
        if cap is None:
            result.error(f"路线 {route.route_id} 引用了不存在的能力: {cap_id}")
            continue
        used.add(cap.capability_id or cap_id)
        if capability_ref(cap) not in verified and cap.name not in verified:
            result.error(f"路线 {route.route_id} 引用了未验证能力: {cap_id}")
        if is_write_capability(cap):
            write_caps.append(cap)
    if write_caps and not route.requires_confirmation:
        result.error(f"路线 {route.route_id} 包含写能力，必须保留确认要求")
    for cap in write_caps:
        if is_risk_write(cap) and not _route_mentions_risk(route, cap):
            result.error(
                f"路线 {route.route_id} 的风险操作 {cap.title or cap.name} 必须明确提示确认"
            )
    for binding in route.bindings:
        _validate_binding(result, binding, route, spec, caps, repeated)
    _validate_required_inputs(result, route, caps)
    for example in route.examples:
        if not str(example.user_request or "").strip():
            result.error(f"路线 {route.route_id} 的示例缺少用户请求")
        if not str(example.done_when or "").strip():
            result.clarify(f"请补充路线 {route.route_id} 示例的完成条件")
        if example.capability_sequence and example.capability_sequence != list(route.capability_sequence):
            result.error(f"路线 {route.route_id} 示例的能力顺序与路线不一致")
    return used


def _route_mentions_risk(route: SkillRoute, cap: FlowCapability) -> bool:
    title = str(cap.title or cap.name or "")
    texts = [
        route.when_to_use,
        route.done_when,
        route.failure_behavior,
        " ".join(route.preconditions),
        " ".join(example.user_request for example in route.examples),
        " ".join(
            point
            for example in route.examples
            for point in example.confirmation_points
        ),
    ]
    blob = " ".join(texts)
    return bool(title and title in blob) or bool(route.requires_confirmation)


def _validate_binding(
    result: PlanValidation,
    binding: RouteBinding,
    route: SkillRoute,
    spec: FlowSpec,
    caps: dict[str, FlowCapability],
    repeated: set[str] | None = None,
) -> None:
    if binding.source == "user_input":
        if binding.to_input and binding.to_input not in route.required_user_inputs:
            result.error(f"路线 {route.route_id} 用户输入 {binding.to_input} 未列入 required_user_inputs")
        return
    if binding.source in {"fixed_value", "system_value"}:
        target = caps.get(binding.to_capability)
        if target is None:
            result.error(f"绑定目标能力不存在: {binding.to_capability}")
            return
        if not schema_has_field(target.input_schema, binding.to_input):
            result.error(f"绑定目标输入不存在: {binding.to_capability}.{binding.to_input}")
        return
    if binding.from_capability not in route.capability_sequence:
        result.error(f"绑定来源能力不在当前路线中: {binding.from_capability}")
    if binding.to_capability not in route.capability_sequence:
        result.error(f"绑定目标能力不在当前路线中: {binding.to_capability}")
    source = caps.get(binding.from_capability)
    target = caps.get(binding.to_capability)
    if source is None or target is None:
        result.error("绑定引用了不存在的能力")
        return
    if not schema_has_field(source.output_schema, binding.from_output):
        result.error(f"绑定来源输出不存在: {binding.from_capability}.{binding.from_output}")
    if not schema_has_field(target.input_schema, binding.to_input):
        result.error(f"绑定目标输入不存在: {binding.to_capability}.{binding.to_input}")
    source_type = field_type(source.output_schema, binding.from_output)
    target_type = field_type(target.input_schema, binding.to_input)
    if source_type and target_type and not types_compatible(source_type, target_type):
        result.error(
            f"绑定类型不兼容: {binding.from_capability}.{binding.from_output}({source_type}) "
            f"-> {binding.to_capability}.{binding.to_input}({target_type})"
        )
    if not cardinality_compatible(
        source.output_schema,
        binding.from_output,
        target.input_schema,
        binding.to_input,
        source_selector=binding.source_selector,
    ):
        result.error(
            f"绑定基数不兼容: {binding.from_capability}.{binding.from_output} "
            f"-> {binding.to_capability}.{binding.to_input}"
        )
    repeated_caps = repeated or set()
    if (
        binding.from_capability in repeated_caps or binding.to_capability in repeated_caps
    ) and not (binding.from_step and binding.to_step):
        result.error(
            f"同一能力多次调用时，绑定必须使用独立步骤身份: "
            f"{binding.from_capability}->{binding.to_capability}"
        )
    if not _binding_has_confirmed_relation(spec, binding):
        result.error(
            f"未确认关系不能进入自动路线: {binding.from_capability}.{binding.from_output} "
            f"-> {binding.to_capability}.{binding.to_input}"
        )
    if binding.transform_owner == "caller" and not (binding.source_selector and binding.target_path):
        result.clarify(
            f"transform_owner=caller 的绑定需要已确认的 source_selector 和 target_path: "
            f"{binding.from_capability}->{binding.to_capability}"
        )


def _binding_has_confirmed_relation(spec: FlowSpec, binding: RouteBinding) -> bool:
    for relation in usable_relations(spec):
        if (
            relation.from_capability in {binding.from_capability}
            or _same_cap(spec, relation.from_capability, binding.from_capability)
        ) and (
            relation.to_capability in {binding.to_capability}
            or _same_cap(spec, relation.to_capability, binding.to_capability)
        ):
            if relation.from_output and relation.from_output != binding.from_output:
                continue
            if relation.to_input and relation.to_input != binding.to_input:
                continue
            return True
    return False


def _same_cap(spec: FlowSpec, left: str, right: str) -> bool:
    caps = capability_by_id(spec)
    a = caps.get(left)
    b = caps.get(right)
    if a is None or b is None:
        return left == right
    return capability_ref(a) == capability_ref(b) or a.name == b.name


def _validate_required_inputs(
    result: PlanValidation,
    route: SkillRoute,
    caps: dict[str, FlowCapability],
) -> None:
    provided: dict[str, set[str]] = {}
    for binding in route.bindings:
        if binding.to_capability and binding.to_input:
            provided.setdefault(binding.to_capability, set()).add(binding.to_input)
    for cap_id in route.capability_sequence:
        cap = caps.get(cap_id)
        if cap is None:
            continue
        satisfied = confirmed_fixed_or_system_inputs(cap)
        bound = provided.get(cap.capability_id, set()) | provided.get(cap.name, set()) | provided.get(cap_id, set())
        for field in schema_required(cap.input_schema):
            if field in satisfied or field in bound:
                continue
            if field not in route.required_user_inputs:
                result.error(
                    f"路线 {route.route_id} 的必填输入 {cap_id}.{field} 没有来源，"
                    "应列为 required_user_inputs，不得编造关联"
                )


def plan_to_contract_payload(plan: SkillPlan) -> dict[str, Any]:
    return {
        "planning_mode": str(plan.planning_mode),
        "selected_capability_ids": list(plan.selected_capability_ids),
        "routes": [route.model_dump(mode="json") for route in plan.routes],
        "bindings": [
            binding.model_dump(mode="json")
            for route in plan.routes
            for binding in route.bindings
        ],
        "unused_capabilities": [item.model_dump(mode="json") for item in plan.unused_capabilities],
        "source_flow_fingerprint": plan.source_flow_fingerprint,
    }
