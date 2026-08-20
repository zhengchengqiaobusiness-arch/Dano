"""Propose a SkillPlan, then accept only a deterministically validated result."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from dano.execution.page.capability_kinds import READ_CAPABILITY_KINDS
from dano.execution.page.flow_spec_core.models import FlowCapability, FlowSpec
from dano.onboarding.skill_generation.catalog import (
    capability_by_id,
    capability_ref,
    confirmed_fixed_or_system_inputs,
    is_write_capability,
    public_capability_catalog,
    schema_required,
    usable_relations,
)
from dano.onboarding.skill_generation.models import (
    PlanningMode,
    RouteBinding,
    RouteExample,
    SkillGenerationRequest,
    SkillGenerationResult,
    SkillPlan,
    SkillRoute,
    UnusedCapability,
)
from dano.onboarding.skill_generation.validate import validate_skill_plan

PlanProposer = Callable[[FlowSpec, SkillGenerationRequest, set[str], str], Awaitable[SkillPlan | dict[str, Any]]]

_QUERY_HINTS = ("查询", "查看", "列表", "待办", "记录", "检索", "筛选")
_OPTION_HINTS = ("选项", "字典", "下拉", "候选")
_SUBMIT_HINTS = ("提交", "保存", "审批", "写入", "新建", "编辑", "更新")
_LOOKUP_HINTS = ("回查", "确认提交", "确认成功", "再查询", "查询状态")
_SECRET_RE = re.compile(r"(token|cookie|storage_state|password|authorization|bearer\s+\S+)", re.I)


def _text(request: SkillGenerationRequest) -> str:
    parts = [
        request.title,
        request.business_description,
        " ".join(request.example_requests),
        request.success_criteria,
        request.forbidden_actions,
    ]
    return "\n".join(str(item or "") for item in parts)


def _mentions(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _family(cap: FlowCapability) -> str:
    kind = str(cap.kind or "").strip().lower()
    title = f"{cap.title} {cap.name} {cap.intent}"
    if kind == "list_options" or _mentions(title, _OPTION_HINTS):
        return "option"
    if kind in READ_CAPABILITY_KINDS or _mentions(title, _QUERY_HINTS):
        return "query"
    if is_write_capability(cap) or _mentions(title, _SUBMIT_HINTS):
        return "write"
    return kind or "other"


def _score_capability(cap: FlowCapability, text: str) -> int:
    family = _family(cap)
    score = 0
    title = f"{cap.title} {cap.name} {cap.intent}"
    if cap.title and cap.title in text:
        score += 6
    if cap.name and cap.name in text:
        score += 4
    if family == "query" and _mentions(text, _QUERY_HINTS):
        score += 3
    if family == "option" and _mentions(text, _OPTION_HINTS):
        score += 3
    if family == "write" and _mentions(text, _SUBMIT_HINTS):
        score += 3
    if title.strip() and any(token and token in text for token in re.split(r"\s+", title) if len(token) >= 2):
        score += 1
    return score


def _select_capabilities(
    spec: FlowSpec,
    request: SkillGenerationRequest,
    verified_ids: set[str],
) -> tuple[list[FlowCapability], list[UnusedCapability]]:
    caps = [
        cap for cap in spec.capabilities
        if capability_ref(cap) in verified_ids or cap.name in verified_ids
    ]
    text = _text(request)
    if not caps:
        return [], []
    scored = [(cap, _score_capability(cap, text)) for cap in caps]
    mentioned = [cap for cap, score in scored if score > 0]
    if not mentioned:
        selected = list(caps)
    else:
        selected = mentioned
        families = {_family(cap) for cap in selected}
        if "write" in families and "query" not in families and _mentions(text, _LOOKUP_HINTS):
            selected.extend(cap for cap in caps if _family(cap) == "query" and cap not in selected)
    selected_ids = {capability_ref(cap) for cap in selected}
    unused = [
        UnusedCapability(
            capability_id=capability_ref(cap),
            name=cap.name,
            title=cap.title or cap.name,
            reason="业务描述未要求该能力",
        )
        for cap in caps
        if capability_ref(cap) not in selected_ids
    ]
    return selected, unused


def _relation_pair(spec: FlowSpec, left: FlowCapability, right: FlowCapability) -> list[RouteBinding]:
    bindings: list[RouteBinding] = []
    left_ids = {capability_ref(left), left.name, left.capability_id}
    right_ids = {capability_ref(right), right.name, right.capability_id}
    for relation in usable_relations(spec):
        if relation.from_capability in left_ids and relation.to_capability in right_ids:
            bindings.append(RouteBinding(
                from_capability=capability_ref(left),
                from_output=relation.from_output,
                to_capability=capability_ref(right),
                to_input=relation.to_input,
                source="capability_output",
                transform_owner=relation.transform_owner,
                source_selector=relation.source_selector,
                target_path=relation.target_path or relation.to_input,
            ))
    return bindings


def _required_user_inputs(cap: FlowCapability, bound_inputs: set[str]) -> list[str]:
    satisfied = set(confirmed_fixed_or_system_inputs(cap))
    return [
        field
        for field in schema_required(cap.input_schema)
        if field not in satisfied and field not in bound_inputs
    ]


def _step_ids(sequence: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    ids: list[str] = []
    for cap_id in sequence:
        count = seen.get(cap_id, 0) + 1
        seen[cap_id] = count
        suffix = "before" if count == 1 and sequence.count(cap_id) > 1 else "after" if count > 1 else "once"
        if sequence.count(cap_id) == 1:
            ids.append(cap_id)
        elif count == 1:
            ids.append(f"{cap_id}_query_before" if suffix == "before" else f"{cap_id}_{count}")
        else:
            ids.append(f"{cap_id}_query_after")
    return ids


def _route(
    *,
    route_id: str,
    name: str,
    when_to_use: str,
    sequence: list[FlowCapability],
    bindings: list[RouteBinding],
    request: SkillGenerationRequest,
    extra_preconditions: list[str] | None = None,
) -> SkillRoute:
    cap_ids = [capability_ref(cap) for cap in sequence]
    bound_by_cap: dict[str, set[str]] = {}
    for binding in bindings:
        bound_by_cap.setdefault(binding.to_capability, set()).add(binding.to_input)
    required: list[str] = []
    for cap in sequence:
        required.extend(_required_user_inputs(cap, bound_by_cap.get(capability_ref(cap), set())))
    required = list(dict.fromkeys(required))
    writes = [cap for cap in sequence if is_write_capability(cap)]
    confirmation = [cap.title or cap.name for cap in writes]
    done = str(request.success_criteria or "").strip() or (
        "写操作已确认并执行成功" if writes else "已返回查询结果"
    )
    example_request = next((item for item in request.example_requests if str(item).strip()), "")
    if not example_request:
        example_request = f"{when_to_use}。{request.business_description}".strip()
    failure = "任一能力失败立即停止；写操作结果不明时不得重试，先用已有只读能力核查。"
    if request.forbidden_actions:
        failure = f"{failure} 禁止：{request.forbidden_actions}"
    return SkillRoute(
        route_id=route_id,
        name=name,
        when_to_use=when_to_use,
        capability_sequence=cap_ids,
        step_ids=_step_ids(cap_ids),
        required_user_inputs=required,
        bindings=bindings,
        preconditions=list(extra_preconditions or []),
        requires_confirmation=bool(writes),
        done_when=done,
        failure_behavior=failure,
        examples=[
            RouteExample(
                user_request=example_request,
                route_id=route_id,
                collected_fields=required,
                capability_sequence=cap_ids,
                bindings=list(bindings),
                confirmation_points=confirmation,
                done_when=done,
            )
        ],
    )


def propose_deterministic_plan(
    spec: FlowSpec,
    request: SkillGenerationRequest,
    verified_ids: set[str],
    source_flow_fingerprint: str,
) -> SkillPlan:
    selected, unused = _select_capabilities(spec, request, verified_ids)
    text = _text(request)
    by_family: dict[str, list[FlowCapability]] = {"query": [], "option": [], "write": [], "other": []}
    for cap in selected:
        by_family.setdefault(_family(cap), []).append(cap)
    queries = by_family.get("query") or []
    options = by_family.get("option") or []
    writes = by_family.get("write") or []
    routes: list[SkillRoute] = []

    if request.planning_mode == PlanningMode.FIXED:
        sequence: list[FlowCapability] = []
        bindings: list[RouteBinding] = []
        if queries and (_mentions(text, _QUERY_HINTS) or writes):
            sequence.append(queries[0])
        if writes:
            if sequence:
                bindings.extend(_relation_pair(spec, sequence[-1], writes[0]))
            sequence.append(writes[0])
        if _mentions(text, _LOOKUP_HINTS) and queries:
            sequence.append(queries[0])
        if not sequence:
            sequence = list(selected[:1] or spec.capabilities[:1])
        routes.append(_route(
            route_id="main",
            name="主要业务步骤",
            when_to_use=request.business_description or "按固定步骤完成该页面业务",
            sequence=sequence,
            bindings=bindings,
            request=request,
        ))
    else:
        if queries:
            routes.append(_route(
                route_id="query_only",
                name="只查询记录",
                when_to_use="用户只要求查看或查询记录，不要执行提交或其他写操作",
                sequence=[queries[0]],
                bindings=[],
                request=request,
            ))
        if writes:
            write = writes[0]
            routes.append(_route(
                route_id="write_direct",
                name="直接提交",
                when_to_use="用户已经提供完整提交字段，不需要先查询",
                sequence=[write],
                bindings=[],
                request=request,
            ))
            if queries:
                bindings = _relation_pair(spec, queries[0], write)
                routes.append(_route(
                    route_id="query_then_write",
                    name="查询后提交",
                    when_to_use="需要先查询记录，再对选中记录执行提交",
                    sequence=[queries[0], write],
                    bindings=bindings,
                    request=request,
                ))
            if options:
                bindings = _relation_pair(spec, options[0], write)
                if bindings:
                    routes.append(_route(
                        route_id="option_then_write",
                        name="选项后提交",
                        when_to_use="提交字段需要从选项中选择",
                        sequence=[options[0], write],
                        bindings=bindings,
                        request=request,
                    ))
            if _mentions(text, _LOOKUP_HINTS) and queries:
                routes.append(_route(
                    route_id="write_then_query",
                    name="提交后回查",
                    when_to_use="提交后需要再查询状态确认完成",
                    sequence=[write, queries[0]],
                    bindings=[],
                    request=request,
                    extra_preconditions=["回查使用独立步骤身份，不得用能力名当作结果键"],
                ))
        if not routes:
            routes.append(_route(
                route_id="single",
                name=selected[0].title or selected[0].name or "页面能力",
                when_to_use=request.business_description or "使用该页面已验证能力",
                sequence=selected[:1],
                bindings=[],
                request=request,
            ))

    used_ids = {cap_id for route in routes for cap_id in route.capability_sequence}
    selected = [cap for cap in selected if capability_ref(cap) in used_ids]
    unused.extend(
        UnusedCapability(
            capability_id=capability_ref(cap),
            name=cap.name,
            title=cap.title or cap.name,
            reason="已规划路线未使用该能力",
        )
        for cap in [
            cap for cap in spec.capabilities
            if (capability_ref(cap) in verified_ids or cap.name in verified_ids)
            and capability_ref(cap) not in used_ids
        ]
        if capability_ref(cap) not in {item.capability_id for item in unused}
    )
    safety = [
        "只使用阶段7已验证能力，不得发明字段、接口或输出。",
        "写操作必须先取得用户确认。",
        "不得输出 token、cookie、storage_state 或密码。",
    ]
    if request.forbidden_actions:
        safety.append(f"禁止或限制：{request.forbidden_actions}")
    return SkillPlan(
        source_flow_fingerprint=source_flow_fingerprint,
        planning_mode=request.planning_mode,
        summary=request.business_description.strip() or (selected[0].title if selected else ""),
        trigger_phrases=[request.title] + list(request.example_requests),
        selected_capability_ids=[capability_ref(cap) for cap in selected],
        unused_capabilities=unused,
        routes=routes,
        safety_rules=safety,
    )


def _parse_proposed_plan(raw: SkillPlan | dict[str, Any], fallback: SkillPlan) -> SkillPlan:
    if isinstance(raw, SkillPlan):
        return raw
    payload = dict(raw or {})
    payload.setdefault("source_flow_fingerprint", fallback.source_flow_fingerprint)
    payload.setdefault("planning_mode", fallback.planning_mode)
    return SkillPlan.model_validate(payload)


async def _llm_propose(
    spec: FlowSpec,
    request: SkillGenerationRequest,
    verified_ids: set[str],
    source_flow_fingerprint: str,
    *,
    repair_errors: list[str] | None = None,
) -> SkillPlan:
    from dano.infra.llm import openai_text_spawn

    catalog = public_capability_catalog(spec, verified_ids)
    relations = [
        {
            "from_capability": relation.from_capability,
            "from_output": relation.from_output,
            "to_capability": relation.to_capability,
            "to_input": relation.to_input,
            "confirmed": relation.confirmed,
            "type": relation.type,
            "transform_owner": relation.transform_owner,
            "source_selector": relation.source_selector,
            "target_path": relation.target_path,
        }
        for relation in usable_relations(spec)
    ]
    prompt = {
        "task": "为已验证的页面能力规划文件式 Skill，只输出 JSON 对象。",
        "rules": [
            "只能使用 verified_capabilities 中的 capability_id",
            "选择完成业务描述所需的最少能力，不要排列组合",
            "不得发明能力、字段或关系",
            "未确认 suggested_call_chain 不得进入 bindings",
            "写能力必须 requires_confirmation=true",
            "每条 route 必须有 when_to_use、done_when 和至少一个 example",
            "fixed 模式只能一条 route，dynamic 可以多条有效路线",
            "unused_capabilities 必须写明原因",
        ],
        "request": request.model_dump(mode="json"),
        "source_flow_fingerprint": source_flow_fingerprint,
        "verified_capabilities": catalog,
        "confirmed_relations": relations,
        "repair_errors": list(repair_errors or []),
    }
    text = await openai_text_spawn(
        json.dumps(prompt, ensure_ascii=False),
        tag="skill_plan",
        json_mode=True,
    )
    if not str(text or "").strip():
        raise ValueError("模型没有返回规划")
    if _SECRET_RE.search(text):
        raise ValueError("模型输出包含敏感凭证字段")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("模型规划必须是 JSON 对象")
    payload.setdefault("source_flow_fingerprint", source_flow_fingerprint)
    payload.setdefault("planning_mode", request.planning_mode)
    return SkillPlan.model_validate(payload)


async def generate_skill_plan(
    spec: FlowSpec,
    request: SkillGenerationRequest,
    *,
    verified_capability_ids: set[str],
    source_flow_fingerprint: str,
    proposer: PlanProposer | None = None,
) -> SkillGenerationResult:
    if not str(request.business_description or "").strip():
        return SkillGenerationResult(
            status="generation_failed",
            errors=["业务描述不能为空"],
        )
    if _SECRET_RE.search(_text(request)):
        return SkillGenerationResult(
            status="generation_failed",
            errors=["业务描述或示例不得包含 token、cookie、storage_state 或密码"],
        )
    if not verified_capability_ids:
        return SkillGenerationResult(
            status="generation_failed",
            errors=["未运行阶段7或没有已验证能力，禁止生成 Skill"],
        )

    fallback = propose_deterministic_plan(spec, request, verified_capability_ids, source_flow_fingerprint)
    proposed: SkillPlan = fallback
    errors: list[str] = []
    used_llm = False
    if proposer is not None:
        try:
            proposed = _parse_proposed_plan(
                await proposer(spec, request, verified_capability_ids, source_flow_fingerprint),
                fallback,
            )
        except Exception as exc:  # noqa: BLE001 - proposer failure is reported, not guessed
            return SkillGenerationResult(
                status="generation_failed",
                errors=[str(exc) or "规划提案失败"],
            )
    else:
        api_key = ""
        try:
            from dano.config import get_settings
            api_key = str(get_settings().pi_api_key or "").strip()
        except Exception:  # noqa: BLE001 - missing settings keep the deterministic plan
            api_key = ""
        if api_key:
            try:
                proposed = await _llm_propose(
                    spec, request, verified_capability_ids, source_flow_fingerprint,
                )
                used_llm = True
            except Exception as exc:  # noqa: BLE001 - deterministic plan is the fallback candidate
                errors.append(str(exc) or "模型规划失败")
                proposed = fallback

    checked = validate_skill_plan(
        proposed,
        spec,
        verified_capability_ids=verified_capability_ids,
        expected_fingerprint=source_flow_fingerprint,
    )
    if checked.ok:
        return SkillGenerationResult(status="planned", plan=proposed)

    if used_llm:
        try:
            proposed = await _llm_propose(
                spec,
                request,
                verified_capability_ids,
                source_flow_fingerprint,
                repair_errors=checked.errors + checked.clarifications,
            )
        except Exception as exc:  # noqa: BLE001 - one repair only, then report
            errors.append(str(exc) or "规划修复失败")
        else:
            checked = validate_skill_plan(
                proposed,
                spec,
                verified_capability_ids=verified_capability_ids,
                expected_fingerprint=source_flow_fingerprint,
            )
            if checked.ok:
                return SkillGenerationResult(status="planned", plan=proposed)

    if checked.clarifications and not checked.errors:
        return SkillGenerationResult(
            status="needs_clarification",
            plan=proposed,
            clarification_questions=checked.clarifications,
            errors=errors,
        )
    if checked.clarifications:
        return SkillGenerationResult(
            status="needs_clarification",
            plan=None,
            clarification_questions=checked.clarifications,
            errors=checked.errors + errors,
        )
    return SkillGenerationResult(
        status="generation_failed",
        errors=checked.errors + errors,
    )
