"""Propose a SkillPlan, then accept only a deterministically validated result."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from dano.execution.page.capability_kinds import READ_CAPABILITY_KINDS
from dano.execution.page.flow_spec_core.models import FlowCapability, FlowSpec
from dano.onboarding.skill_generation.catalog import (
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

log = structlog.get_logger(__name__)


def _log_plan(event: str, *, summary: str, status: str = "progress", level: str = "info", **details: Any) -> None:
    payload = {key: value for key, value in details.items() if value is not None}
    writer = log.error if level in {"error", "exception"} else log.warning if level == "warning" else log.info
    writer(event, summary=summary, status=status, **payload)
    try:
        from dano.infra.run_logging import emit_run_event

        emit_run_event(
            event,
            stage="plan",
            status=status,
            summary=summary,
            level=level,
            details=payload,
        )
    except Exception:  # noqa: BLE001 - planning logs must not fail export
        pass


PlanProposer = Callable[[FlowSpec, SkillGenerationRequest, set[str], str], Awaitable[SkillPlan | dict[str, Any]]]

_QUERY_HINTS = ("查询", "查看", "列表", "待办", "记录", "检索", "筛选")
_OPTION_HINTS = ("选项", "字典", "下拉", "候选")
_SUBMIT_HINTS = ("提交", "保存", "审批", "写入", "新建", "编辑", "更新")
_LOOKUP_HINTS = ("回查", "确认提交", "确认成功", "再查询", "查询状态")
_SECRET_RE = re.compile(r"(token|cookie|storage_state|password|authorization|bearer\s+\S+)", re.I)
_RECORDING_COPY_MARKERS = ("本页面的实际操作流程", "能力录制", "录制结果", "阶段1")


def _is_recording_copy(value: Any) -> bool:
    text = str(value or "")
    return any(marker in text for marker in _RECORDING_COPY_MARKERS)


def _operation_route_id(cap: FlowCapability) -> str:
    raw = str(cap.name or capability_ref(cap) or "operation")
    slug = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "_", raw.casefold().replace("-", "_"))).strip("_")
    return f"op_{slug or capability_ref(cap)}"


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


def _step_ids_for(sequence: list[FlowCapability]) -> list[str]:
    families = [_family(cap) for cap in sequence]
    totals = {family: families.count(family) for family in set(families)}
    seen: dict[str, int] = {}
    ids: list[str] = []
    for index, cap in enumerate(sequence):
        family = families[index]
        seen[family] = seen.get(family, 0) + 1
        count = seen[family]
        total = totals[family]
        if family == "query":
            if total > 1:
                ids.append("query_before" if count == 1 else "query_after")
            else:
                ids.append("query")
        elif family == "write":
            ids.append("submit_selected" if "query" in families else "submit")
        elif family == "option":
            ids.append("option" if total == 1 else f"option_{count}")
        else:
            cap_id = capability_ref(cap) or f"step_{index + 1}"
            ids.append(cap_id if total == 1 else f"{cap_id}_{count}")
    return ids


def _annotate_bindings(
    sequence: list[FlowCapability],
    step_ids: list[str],
    bindings: list[RouteBinding],
) -> list[RouteBinding]:
    first_step: dict[str, str] = {}
    last_step: dict[str, str] = {}
    for cap, step_id in zip(sequence, step_ids, strict=False):
        cap_id = capability_ref(cap)
        first_step.setdefault(cap_id, step_id)
        last_step[cap_id] = step_id
    annotated: list[RouteBinding] = []
    for binding in bindings:
        payload = binding.model_dump()
        if not payload.get("from_step"):
            payload["from_step"] = first_step.get(binding.from_capability, "")
        if not payload.get("to_step"):
            payload["to_step"] = last_step.get(binding.to_capability, "")
        annotated.append(RouteBinding.model_validate(payload))
    return annotated


def _clean_when(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if text and not _is_recording_copy(text):
        return text
    return fallback


def _example_request(
    request: SkillGenerationRequest,
    when_to_use: str,
    sequence: list[FlowCapability],
) -> str:
    for item in request.example_requests:
        text = str(item).strip()
        if text and not _is_recording_copy(text):
            return text
    if when_to_use and not _is_recording_copy(when_to_use):
        return when_to_use
    titles = "、".join(cap.title or cap.name for cap in sequence if cap.title or cap.name)
    return f"请{titles}" if titles else "按本页已打包操作办理"


def _cap_title(cap: FlowCapability) -> str:
    return str(cap.title or cap.name or capability_ref(cap) or "该操作")


def _build_composition(
    request: SkillGenerationRequest,
    selected: list[FlowCapability],
    routes: list[SkillRoute],
) -> tuple[str, list[str]]:
    titles = [_cap_title(cap) for cap in selected]
    summary = _clean_when(
        request.business_description,
        f"本页原子能力：{'、'.join(titles)}。按用户意图选择一项，或按已规划路线组合。",
    )
    notes: list[str] = [
        "一页面对应一个 Skill；阶段 6/7 产出的是原子能力，本 Skill 用自然语言规划它们如何组合。",
    ]
    reads = [cap for cap in selected if not is_write_capability(cap)]
    writes = [cap for cap in selected if is_write_capability(cap)]
    if reads and writes:
        notes.append("用户只要只读操作时，只执行对应只读能力，不得执行写入。")
    combinations = [route for route in routes if len(route.capability_sequence) > 1]
    if combinations:
        for route in combinations:
            sequence = " → ".join(f"`{cap_id}`" for cap_id in route.capability_sequence)
            if route.bindings:
                bound = "；".join(
                    f"{binding.from_output} → {binding.to_input}"
                    for binding in route.bindings
                    if binding.from_output and binding.to_input
                )
                notes.append(
                    f"组合路线「{route.name}」按 {sequence} 执行"
                    + (f"，已确认绑定：{bound}" if bound else "，使用已确认绑定传值")
                    + "。"
                )
            else:
                notes.append(
                    f"组合路线「{route.name}」按 {sequence} 执行，但没有已确认绑定；"
                    "下一步输入向用户收集，不得按字段同名猜测。"
                )
    elif reads and writes:
        notes.append(
            "本页同时有只读和写入能力，但没有已确认绑定，不能生成自动传值的组合路线。"
            "需要先后办理时，先执行只读能力，再请用户指定记录后执行写入。"
        )
    else:
        notes.append("未规划自动传值的组合路线。一次对话只执行用户当前要求的那条路线。")
    return summary, notes


def _append_lookup(
    sequence: list[FlowCapability],
    queries: list[FlowCapability],
    text: str,
) -> list[FlowCapability]:
    if not _mentions(text, _LOOKUP_HINTS) or not queries:
        return list(sequence)
    lookup = queries[0]
    if not sequence:
        return [lookup]
    if sequence[-1] is lookup or capability_ref(sequence[-1]) == capability_ref(lookup):
        return list(sequence)
    return [*sequence, lookup]


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
    step_ids = _step_ids_for(sequence)
    annotated = _annotate_bindings(sequence, step_ids, bindings)
    bound_by_cap: dict[str, set[str]] = {}
    for binding in annotated:
        bound_by_cap.setdefault(binding.to_capability, set()).add(binding.to_input)
    required: list[str] = []
    for cap in sequence:
        required.extend(_required_user_inputs(cap, bound_by_cap.get(capability_ref(cap), set())))
    required = list(dict.fromkeys(required))
    writes = [cap for cap in sequence if is_write_capability(cap)]
    confirmation = [_cap_title(cap) for cap in writes]
    done = str(request.success_criteria or "").strip() or (
        "写操作已确认并执行成功" if writes else "已返回查询结果"
    )
    cleaned_when = _clean_when(
        when_to_use,
        " → ".join(_cap_title(cap) for cap in sequence) or "按本页已打包操作办理",
    )
    example_request = _example_request(request, cleaned_when, sequence)
    failure = "任一能力失败立即停止；写操作结果不明时不得重试，先用已有只读能力核查。"
    if request.forbidden_actions:
        failure = f"{failure} 禁止：{request.forbidden_actions}"
    return SkillRoute(
        route_id=route_id,
        name=name,
        when_to_use=cleaned_when,
        capability_sequence=cap_ids,
        step_ids=step_ids,
        required_user_inputs=required,
        bindings=annotated,
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
                bindings=list(annotated),
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
            write = writes[0]
            if sequence:
                pair = _relation_pair(spec, sequence[-1], write)
                if pair:
                    bindings.extend(pair)
            sequence.append(write)
        sequence = _append_lookup(sequence, queries, text)
        if not sequence:
            sequence = list(selected[:1] or spec.capabilities[:1])
        routes.append(_route(
            route_id="main",
            name=" → ".join(_cap_title(cap) for cap in sequence) or "主要业务步骤",
            when_to_use=_clean_when(
                request.business_description,
                "按用户描述的顺序组合本页已选原子能力",
            ),
            sequence=sequence,
            bindings=bindings,
            request=request,
            extra_preconditions=(
                ["回查使用独立步骤身份 query_before / submit_selected / query_after"]
                if _mentions(text, _LOOKUP_HINTS) and queries
                else None
            ),
        ))
    else:
        if queries:
            query = queries[0]
            routes.append(_route(
                route_id="query_only",
                name=_cap_title(query),
                when_to_use=f"用户只要{_cap_title(query)}，不要执行提交或其他写操作",
                sequence=[query],
                bindings=[],
                request=request,
            ))
        if writes:
            if len(writes) == 1:
                write = writes[0]
                write_direct = _append_lookup([write], queries, text)
                routes.append(_route(
                    route_id="write_direct",
                    name=_cap_title(write),
                    when_to_use=f"用户要{_cap_title(write)}，且已提供完整字段，不需要先查询",
                    sequence=write_direct,
                    bindings=[],
                    request=request,
                    extra_preconditions=(
                        ["提交后回查使用独立步骤身份，不得单独生成 C3→C1 路线"]
                        if len(write_direct) > 1
                        else None
                    ),
                ))
            for write in writes:
                if queries:
                    bindings = _relation_pair(spec, queries[0], write)
                    if bindings:
                        routes.append(_route(
                            route_id="query_then_write" if len(writes) == 1 else f"query_then_{capability_ref(write)}",
                            name=f"查询后{write.title or write.name}",
                            when_to_use=f"需要先查询记录，再对选中记录执行「{write.title or write.name}」",
                            sequence=_append_lookup([queries[0], write], queries, text),
                            bindings=bindings,
                            request=request,
                        ))
                if options:
                    bindings = _relation_pair(spec, options[0], write)
                    if bindings:
                        routes.append(_route(
                            route_id="option_then_write" if len(writes) == 1 else f"option_then_{capability_ref(write)}",
                            name=f"选项后{write.title or write.name}",
                            when_to_use=f"「{write.title or write.name}」字段需要从选项中选择",
                            sequence=_append_lookup([options[0], write], queries, text),
                            bindings=bindings,
                            request=request,
                        ))
        if not routes:
            routes.append(_route(
                route_id="single",
                name=_cap_title(selected[0]),
                when_to_use=_clean_when(request.business_description, f"用户要{_cap_title(selected[0])}"),
                sequence=selected[:1],
                bindings=[],
                request=request,
            ))
        existing_singles = {
            route.capability_sequence[0]
            for route in routes
            if len(route.capability_sequence) == 1
        }
        for cap in selected:
            cap_id = capability_ref(cap)
            if cap_id in existing_singles:
                continue
            routes.append(_route(
                route_id=_operation_route_id(cap),
                name=cap.title or cap.name or cap_id,
                when_to_use=f"用户要{cap.title or cap.name}",
                sequence=[cap],
                bindings=[],
                request=request,
            ))
            existing_singles.add(cap_id)

    if request.planning_mode == PlanningMode.FIXED:
        used_ids = {cap_id for route in routes for cap_id in route.capability_sequence}
        dropped = [cap for cap in selected if capability_ref(cap) not in used_ids]
        selected = [cap for cap in selected if capability_ref(cap) in used_ids]
        unused.extend(
            UnusedCapability(
                capability_id=capability_ref(cap),
                name=cap.name,
                title=cap.title or cap.name,
                reason="已规划路线未使用该能力",
            )
            for cap in dropped
            if capability_ref(cap) not in {item.capability_id for item in unused}
        )
    selected_ids = {capability_ref(cap) for cap in selected}
    unused.extend(
        UnusedCapability(
            capability_id=capability_ref(cap),
            name=cap.name,
            title=cap.title or cap.name,
            reason="业务描述未要求该能力",
        )
        for cap in spec.capabilities
        if (capability_ref(cap) in verified_ids or cap.name in verified_ids)
        and capability_ref(cap) not in selected_ids
        and capability_ref(cap) not in {item.capability_id for item in unused}
    )
    safety = [
        "只使用当前页面已识别能力，不得发明字段、接口或输出。",
        "写操作必须先取得用户确认。",
        "不得输出 token、cookie、storage_state 或密码。",
    ]
    if request.forbidden_actions:
        safety.append(f"禁止或限制：{request.forbidden_actions}")
    triggers = [f"用户要{cap.title or cap.name}时使用" for cap in selected]
    triggers.extend(
        str(item).strip()
        for item in request.example_requests
        if str(item).strip() and not _is_recording_copy(item)
    )
    if request.title and not _is_recording_copy(request.title):
        title_trigger = str(request.title).strip()
        if title_trigger and title_trigger not in triggers:
            triggers.insert(0, f"用户要{title_trigger}时使用")
    composition_summary, composition_notes = _build_composition(request, selected, routes)
    summary = request.business_description.strip()
    if _is_recording_copy(summary):
        summary = composition_summary or "、".join(_cap_title(cap) for cap in selected)
    return SkillPlan(
        source_flow_fingerprint=source_flow_fingerprint,
        planning_mode=request.planning_mode,
        summary=summary or (selected[0].title if selected else ""),
        trigger_phrases=triggers,
        selected_capability_ids=[capability_ref(cap) for cap in selected],
        unused_capabilities=unused,
        routes=routes,
        safety_rules=safety,
        composition_summary=composition_summary,
        composition_notes=composition_notes,
    )


def _plan_is_usable(plan: SkillPlan) -> bool:
    if not plan.selected_capability_ids or not plan.routes:
        return False
    return all(bool(route.capability_sequence) and bool(route.examples) for route in plan.routes)


def _parse_proposed_plan(raw: SkillPlan | dict[str, Any], fallback: SkillPlan) -> SkillPlan:
    if isinstance(raw, SkillPlan):
        plan = raw
    else:
        payload = dict(raw or {})
        payload.setdefault("source_flow_fingerprint", fallback.source_flow_fingerprint)
        payload.setdefault("planning_mode", fallback.planning_mode)
        plan = SkillPlan.model_validate(payload)
    if not _plan_is_usable(plan):
        return fallback
    return plan


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
            "组合路线必须有已确认 bindings，不得按字段同名猜测",
            "不要单独生成写后查询路线；回查应追加到写路线末尾并使用 query_before/submit_selected/query_after",
            "同一能力多次调用必须有独立 step_ids，绑定必须带 from_step/to_step",
            "不要生成 solo_ 路线；未进入组合路线的能力仍留在 selected_capability_ids，作为独立操作",
            "多写能力时不要只为第一个写能力生成 write_direct",
            "一页面对应一个 Skill：用业务描述说明原子能力如何组合，并写进 composition_notes",
            "组合路线必须出现在 routes 里，when_to_use 用自然语言说明何时走这条组合",
            "没有已确认绑定仍可写推荐顺序，但 bindings 必须为空，不得按字段同名猜测",
            "route.examples.user_request 必须是业务例句，禁止使用录制套话",
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
        _log_plan("skill.plan.failed", summary="业务描述为空", status="failed", level="error")
        return SkillGenerationResult(
            status="generation_failed",
            errors=["业务描述不能为空"],
        )
    if _SECRET_RE.search(_text(request)):
        _log_plan("skill.plan.failed", summary="业务描述包含敏感字段", status="failed", level="error")
        return SkillGenerationResult(
            status="generation_failed",
            errors=["业务描述或示例不得包含 token、cookie、storage_state 或密码"],
        )
    if not verified_capability_ids:
        _log_plan("skill.plan.failed", summary="没有可导出能力", status="failed", level="error")
        return SkillGenerationResult(
            status="generation_failed",
            errors=["没有可导出能力，不能生成 Skill"],
        )

    fallback = propose_deterministic_plan(spec, request, verified_capability_ids, source_flow_fingerprint)
    proposed: SkillPlan = fallback
    errors: list[str] = []
    used_llm = False
    _log_plan(
        "skill.plan.deterministic",
        summary="已生成确定性规划草案",
        fingerprint=source_flow_fingerprint,
        selected_capability_ids=list(fallback.selected_capability_ids),
        unused_capability_ids=[item.capability_id for item in fallback.unused_capabilities],
        routes=[
            {
                "route_id": route.route_id,
                "sequence": list(route.capability_sequence),
                "bindings": len(route.bindings),
                "user_inputs": list(route.required_user_inputs),
            }
            for route in fallback.routes
        ],
    )
    if proposer is not None:
        try:
            proposed = _parse_proposed_plan(
                await proposer(spec, request, verified_capability_ids, source_flow_fingerprint),
                fallback,
            )
        except Exception as exc:  # noqa: BLE001 - proposer failure is reported, not guessed
            _log_plan(
                "skill.plan.failed",
                summary="规划提案失败",
                status="failed",
                level="error",
                error=str(exc) or "规划提案失败",
            )
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
                proposed = _parse_proposed_plan(
                    await _llm_propose(
                        spec, request, verified_capability_ids, source_flow_fingerprint,
                    ),
                    fallback,
                )
                used_llm = True
            except Exception as exc:  # noqa: BLE001 - deterministic plan is the fallback candidate
                errors.append(str(exc) or "模型规划失败")
                proposed = fallback
                _log_plan(
                    "skill.plan.llm_fallback",
                    summary="模型规划失败，回退确定性草案",
                    status="warning",
                    level="warning",
                    error=str(exc) or "模型规划失败",
                )
            else:
                _log_plan(
                    "skill.plan.llm",
                    summary="模型已返回规划草案",
                    selected_capability_ids=list(proposed.selected_capability_ids),
                    route_ids=[route.route_id for route in proposed.routes],
                )

    checked = validate_skill_plan(
        proposed,
        spec,
        verified_capability_ids=verified_capability_ids,
        expected_fingerprint=source_flow_fingerprint,
    )
    if checked.ok:
        _log_plan(
            "skill.plan.validated",
            summary="规划校验通过",
            status="succeeded",
            used_llm=used_llm,
            selected_capability_ids=list(proposed.selected_capability_ids),
            route_ids=[route.route_id for route in proposed.routes],
        )
        return SkillGenerationResult(status="planned", plan=proposed)

    _log_plan(
        "skill.plan.validation_failed",
        summary="规划校验未通过，尝试修复或回退",
        status="warning",
        level="warning",
        used_llm=used_llm,
        errors=list(checked.errors),
        clarifications=list(checked.clarifications),
    )
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
            proposed = _parse_proposed_plan(proposed, fallback)
            checked = validate_skill_plan(
                proposed,
                spec,
                verified_capability_ids=verified_capability_ids,
                expected_fingerprint=source_flow_fingerprint,
            )
            if checked.ok:
                _log_plan(
                    "skill.plan.validated",
                    summary="模型修复后规划校验通过",
                    status="succeeded",
                    used_llm=True,
                    repaired=True,
                    selected_capability_ids=list(proposed.selected_capability_ids),
                    route_ids=[route.route_id for route in proposed.routes],
                )
                return SkillGenerationResult(status="planned", plan=proposed)

    if used_llm:
        fallback_checked = validate_skill_plan(
            fallback,
            spec,
            verified_capability_ids=verified_capability_ids,
            expected_fingerprint=source_flow_fingerprint,
        )
        if fallback_checked.ok:
            _log_plan(
                "skill.plan.llm_fallback",
                summary="模型规划无效，已回退确定性规划",
                status="warning",
                level="warning",
                selected_capability_ids=list(fallback.selected_capability_ids),
                route_ids=[route.route_id for route in fallback.routes],
            )
            return SkillGenerationResult(status="planned", plan=fallback)

    if checked.clarifications and not checked.errors:
        _log_plan(
            "skill.plan.needs_clarification",
            summary="规划需要补充说明",
            status="warning",
            level="warning",
            clarifications=list(checked.clarifications),
            errors=list(errors),
        )
        return SkillGenerationResult(
            status="needs_clarification",
            plan=proposed,
            clarification_questions=checked.clarifications,
            errors=errors,
        )
    if checked.clarifications:
        _log_plan(
            "skill.plan.needs_clarification",
            summary="规划校验失败且需要补充说明",
            status="failed",
            level="error",
            clarifications=list(checked.clarifications),
            errors=list(checked.errors) + list(errors),
        )
        return SkillGenerationResult(
            status="needs_clarification",
            plan=None,
            clarification_questions=checked.clarifications,
            errors=checked.errors + errors,
        )
    _log_plan(
        "skill.plan.failed",
        summary="规划校验失败",
        status="failed",
        level="error",
        errors=list(checked.errors) + list(errors),
    )
    return SkillGenerationResult(
        status="generation_failed",
        errors=checked.errors + errors,
    )
