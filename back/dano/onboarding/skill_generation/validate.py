"""Deterministic SkillPlan checks. Model text is never treated as fact."""

from __future__ import annotations

from typing import Any

from dano.execution.page.flow_spec_core.models import FlowCapability, FlowSpec
from dano.onboarding.skill_generation.intent import looks_like_ordered_multi_step
from dano.onboarding.skill_generation.catalog import (
    capability_by_id,
    capability_ref,
    cardinality_compatible,
    confirmed_fixed_or_system_inputs,
    field_type,
    is_risk_write,
    is_write_capability,
    schema_has_field,
    schema_required,
    types_compatible,
    usable_relations,
)
from dano.onboarding.skill_generation.models import (
    CompositionMode,
    InputSourceKind,
    PlanningMode,
    RouteBinding,
    SkillPlan,
    SkillRoute,
)

HANDBOOK_BAN_MARKERS = (
    "本页面的实际操作流程",
    "能力录制",
    "录制结果",
    "阶段1",
    "本页原子能力",
    "按用户意图选择一项",
    "阶段 6",
    "阶段6",
    "阶段 7",
    "阶段7",
    "阶段 8",
    "阶段8",
    "录制识别顺序",
    "FlowSpec",
    "fingerprint",
    "capability_id",
    "x-dano",
    "规划依据",
    "原子能力",
    "一页面对应一个 Skill",
    "原样来自",
    "生成器",
)

DEFAULT_SKILL_PLAYBOOK = "先查找再办理。只要查看时不要写入。没有已确认绑定就先查再问人。"


def handbook_text_is_banned(value: Any) -> bool:
    text = str(value or "")
    return any(marker in text for marker in HANDBOOK_BAN_MARKERS)


def handbook_ban_hit(value: Any) -> str:
    text = str(value or "")
    for marker in HANDBOOK_BAN_MARKERS:
        if marker in text:
            return marker
    return ""


def is_stock_playbook(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or handbook_text_is_banned(text):
        return True
    compact = "".join(text.split())
    return compact == "".join(DEFAULT_SKILL_PLAYBOOK.split())


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
        result.error("规划指纹与当前 FlowSpec 不一致")
    if not selected:
        result.error("selected_capability_ids 不能为空")
    for cap_id in selected:
        cap = caps.get(cap_id)
        if cap is None:
            result.error(f"所选能力不存在: {cap_id}")
            continue
        if capability_ref(cap) not in verified and cap.name not in verified and cap.capability_id not in verified:
            result.error(f"所选能力不在可导出集合中: {cap_id}")
    if plan.planning_mode == PlanningMode.FIXED and len(plan.routes) != 1:
        result.error("固定规划模式只能有一条主要路线")
    if plan.planning_mode == PlanningMode.DYNAMIC and not plan.routes:
        result.error("动态规划模式至少需要一条有效路线")
    _validate_unique_route_ids(result, plan)
    used_in_routes: set[str] = set()
    for route in plan.routes:
        used_in_routes.update(_validate_route(result, route, plan, spec, caps, verified))
    selected_set = set(selected)
    if used_in_routes - selected_set:
        extra = ", ".join(sorted(used_in_routes - selected_set))
        result.error(f"路线引用了未入选能力: {extra}")
    unused_ids = {item.capability_id for item in plan.unused_capabilities}
    packed = {
        capability_ref(cap)
        for cap in spec.capabilities
        if capability_ref(cap) in verified or cap.name in verified
    }
    if plan.planning_mode == PlanningMode.DYNAMIC:
        missing = packed - selected_set - unused_ids
        if missing:
            extra = ", ".join(sorted(missing))
            result.error(f"动态模式必须覆盖全部已打包操作: {extra}")
        for item in plan.unused_capabilities:
            reason = str(item.reason or "")
            if "禁止" not in reason and "限制" not in reason:
                result.error(f"动态模式不得因描述未点名而丢弃操作: {item.capability_id}")
    for cap in spec.capabilities:
        cap_id = capability_ref(cap)
        if cap_id in selected_set or cap.name in selected_set:
            continue
        if cap_id in verified or cap.name in verified:
            if cap_id not in unused_ids and cap.name not in unused_ids:
                result.error(f"未使用能力必须记录原因: {cap_id}")
    _validate_handbook_language(result, plan)
    _validate_intent_coverage(result, plan)
    _validate_no_silent_sequence(result, plan, spec)
    _validate_route_execution_contracts(result, plan, caps)
    _validate_forbidden_routes(result, plan)
    _validate_done_when_matches_route(result, plan, caps)
    return result


def _validate_unique_route_ids(result: PlanValidation, plan: SkillPlan) -> None:
    seen: dict[str, int] = {}
    for route in plan.routes:
        route_id = str(route.route_id or "").strip()
        if not route_id:
            result.error("路线缺少 route_id")
            continue
        seen[route_id] = seen.get(route_id, 0) + 1
    duplicates = [route_id for route_id, count in seen.items() if count > 1]
    for route_id in duplicates:
        result.error(f"路线 ID 重复，不能覆盖路线文件: {route_id}")


def _validate_done_when_matches_route(
    result: PlanValidation,
    plan: SkillPlan,
    caps: dict[str, FlowCapability],
) -> None:
    write_markers = ("写入已确认", "写操作已确认")
    for route in plan.routes:
        writes = [
            cap_id
            for cap_id in route.capability_sequence
            if caps.get(cap_id) and is_write_capability(caps[cap_id])
        ]
        if writes:
            continue
        done = str(route.done_when or "")
        if any(marker in done for marker in write_markers):
            result.error(f"只读路线 {route.route_id} 不能继承写入完成标准")


def _sequence_covers(route_sequence: list[str], branch_sequence: list[str]) -> bool:
    if list(route_sequence) == list(branch_sequence):
        return True
    return (
        len(route_sequence) == len(branch_sequence) + 1
        and list(route_sequence[:-1]) == list(branch_sequence)
        and route_sequence[-1] == route_sequence[0]
    )


def _validate_intent_coverage(result: PlanValidation, plan: SkillPlan) -> None:
    for question in plan.clarification_questions:
        result.clarify(question)
    blocked = {
        item.capability_id
        for item in plan.unused_capabilities
        if "禁止" in (item.reason or "") or "限制" in (item.reason or "")
    }
    for branch in plan.intent_branches:
        if any(cap_id in blocked for cap_id in branch.capability_sequence):
            continue
        if branch.unresolved or branch.conflicting or not branch.capability_sequence:
            for item in branch.unresolved or ["请澄清互相冲突或无法映射的办理顺序"]:
                result.clarify(item)
            continue
        if plan.planning_mode == PlanningMode.FIXED and len(branch.capability_sequence) < 2:
            continue
        matches = [
            route
            for route in plan.routes
            if _sequence_covers(list(route.capability_sequence), list(branch.capability_sequence))
            or (
                plan.planning_mode == PlanningMode.FIXED
                and set(branch.capability_sequence) <= set(route.capability_sequence)
            )
        ]
        if not matches:
            result.error(f"明确分支没有对应路线，不能静默原子化: {branch.trigger}")
        elif len({tuple(route.capability_sequence) for route in matches}) > 1 and not branch.independent:
            result.clarify(f"「{branch.trigger}」对应了多条不同合同的路线，请确认唯一顺序")


def _validate_no_silent_sequence(result: PlanValidation, plan: SkillPlan, spec: FlowSpec) -> None:
    caps = list(spec.capabilities or [])
    texts = [plan.summary, *plan.trigger_phrases, *(branch.trigger for branch in plan.intent_branches)]
    if not any(looks_like_ordered_multi_step(str(text), caps) for text in texts if text):
        return
    has_combo = any(len(route.capability_sequence) > 1 for route in plan.routes)
    has_clarify = bool(plan.clarification_questions) or any(
        branch.unresolved or branch.conflicting for branch in plan.intent_branches
    )
    if not has_combo and not has_clarify:
        result.clarify(
            "业务描述像是要按顺序办理多项操作，但没有形成组合路线，也不能静默拆成互不相关的原子操作。"
            "请用「先…再…」写明每一步。"
        )


def _validate_route_execution_contracts(
    result: PlanValidation,
    plan: SkillPlan,
    caps: dict[str, FlowCapability],
) -> None:
    for route in plan.routes:
        if len(route.capability_sequence) > 1 and not route.examples:
            result.error(f"多步路线 {route.route_id} 必须有完整示例")
        if route.steps:
            if [step.capability_id for step in route.steps] != list(route.capability_sequence):
                result.error(f"路线 {route.route_id} 的步骤顺序与能力顺序不一致")
            for step in route.steps:
                if not str(step.done_when or "").strip():
                    result.error(f"路线 {route.route_id} 步骤 {step.step_key} 缺少完成条件")
                cap = caps.get(step.capability_id)
                if cap is None:
                    continue
                needs_confirm = bool(cap.requires_human_confirm) or is_write_capability(cap)
                if needs_confirm != bool(step.confirm_before_execute):
                    result.error(
                        f"路线 {route.route_id} 步骤 {step.step_key} 的确认点必须与能力契约一致"
                    )
                provided = {source.field for source in step.input_sources}
                bound = {binding.to_input for binding in step.bindings if binding.to_input}
                satisfied = set(confirmed_fixed_or_system_inputs(cap))
                for field in schema_required(cap.input_schema):
                    if field in satisfied or field in bound or field in provided or field in route.required_user_inputs:
                        continue
                    result.error(f"路线 {route.route_id} 步骤 {step.step_key} 的输入 {field} 没有来源")
                for source in step.input_sources:
                    if source.source == InputSourceKind.CONFIRMED_BINDING and not step.bindings:
                        result.error(f"路线 {route.route_id} 步骤 {step.step_key} 把未确认关系当成了自动绑定")
                    if (
                        source.source == InputSourceKind.USER
                        and source.field
                        and not schema_has_field(cap.input_schema, source.field)
                    ):
                        result.error(
                            f"路线 {route.route_id} 用户输入 {source.field} 不在能力契约中，"
                            "不得发明调用方字段"
                        )
        if (
            len(route.capability_sequence) > 1
            and not route.bindings
            and route.composition_mode in {CompositionMode.HANDOFF, CompositionMode.ATOMIC}
        ):
            if not route.checkpoints and not any(step.checkpoint for step in route.steps):
                dependent = False
                for cap_id in route.capability_sequence[1:]:
                    cap = caps.get(cap_id)
                    if cap is not None and is_write_capability(cap) and _needs_user_or_prior(cap):
                        dependent = True
                if dependent:
                    result.error(f"路线 {route.route_id} 没有确认绑定，必须设置人工交接点")
        failure = str(route.failure_behavior or "")
        if any(caps.get(cap_id) and is_write_capability(caps[cap_id]) for cap_id in route.capability_sequence):
            if "不得重试" not in failure and "不能重试" not in failure:
                result.error(f"路线 {route.route_id} 写结果未知时必须禁止自动重试")
        user_fields = [
            source.field
            for step in route.steps
            for source in step.input_sources
            if source.source == InputSourceKind.USER and source.field
        ]
        if list(dict.fromkeys(user_fields)) != list(route.required_user_inputs):
            result.error(f"路线 {route.route_id} 的调用方字段与步骤输入来源不一致")


def _needs_user_or_prior(cap: FlowCapability) -> bool:
    required = set(schema_required(cap.input_schema))
    satisfied = set(confirmed_fixed_or_system_inputs(cap))
    return bool(required - satisfied)


def _validate_forbidden_routes(result: PlanValidation, plan: SkillPlan) -> None:
    blocked = {
        item.capability_id
        for item in plan.unused_capabilities
        if "禁止" in (item.reason or "") or "限制" in (item.reason or "")
    }
    if not blocked:
        return
    for route in plan.routes:
        hit = [cap_id for cap_id in route.capability_sequence if cap_id in blocked]
        if hit:
            result.error(f"被禁止的操作不能进入可执行路线: {', '.join(hit)}")


def _validate_handbook_language(result: PlanValidation, plan: SkillPlan) -> None:
    texts = [plan.summary, plan.composition_summary, *plan.composition_notes]
    texts.extend(plan.trigger_phrases)
    for route in plan.routes:
        texts.append(route.when_to_use)
        texts.append(route.done_when)
        texts.append(route.failure_behavior)
        texts.extend(route.preconditions)
        for example in route.examples:
            texts.append(example.user_request)
    for text in texts:
        hit = handbook_ban_hit(text)
        if hit:
            result.error(f"手册用语不合格: {hit}")
            return


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
            result.error(f"路线 {route.route_id} 引用了不可导出能力: {cap_id}")
        if is_write_capability(cap):
            write_caps.append(cap)
    needs_confirm = any(
        caps[cap_id].requires_human_confirm or is_write_capability(caps[cap_id])
        for cap_id in route.capability_sequence
        if cap_id in caps
    )
    if needs_confirm != bool(route.requires_confirmation):
        result.error(f"路线 {route.route_id} 的确认要求必须与能力契约一致")
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
        "intent_branches": [branch.model_dump(mode="json") for branch in plan.intent_branches],
        "composition_summary": plan.composition_summary,
        "composition_notes": list(plan.composition_notes),
    }
