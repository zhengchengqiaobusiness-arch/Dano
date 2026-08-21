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
from dano.onboarding.skill_generation.intent import branch_needs_clarification, extract_intent_branches
from dano.onboarding.skill_generation.models import (
    CompositionMode,
    HumanCheckpoint,
    InputSourceKind,
    IntentBranch,
    PlanningMode,
    RouteBinding,
    RouteExample,
    RouteStep,
    SkillGenerationRequest,
    SkillGenerationResult,
    SkillPlan,
    SkillRoute,
    StepInputSource,
    UnusedCapability,
)
from dano.onboarding.skill_generation.validate import (
    handbook_text_is_banned,
    is_stock_playbook,
    validate_skill_plan,
)

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
_LOOKUP_HINTS = ("回查", "确认提交成功", "确认提交", "查询状态确认", "查询状态")
_SECRET_RE = re.compile(r"(token|cookie|storage_state|password|authorization|bearer\s+\S+)", re.I)
_OBJECT_PREFIXES = (
    "搜索/筛选", "搜索", "筛选", "查询", "查看", "新增", "新建", "修改", "编辑",
    "审批", "审核", "反审", "反审核", "删除", "提交", "办理",
)


def _is_recording_copy(value: Any) -> bool:
    return handbook_text_is_banned(value) or is_stock_playbook(value)


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


def _forbidden_capability_ids(
    caps: list[FlowCapability],
    request: SkillGenerationRequest,
) -> set[str]:
    text = str(request.forbidden_actions or "").strip()
    if not text:
        return set()
    hits: set[str] = set()
    for cap in caps:
        title = str(cap.title or "").strip()
        name = str(cap.name or "").strip()
        if title and title in text:
            hits.add(capability_ref(cap))
        elif name and name in text:
            hits.add(capability_ref(cap))
    return hits


def _select_capabilities(
    spec: FlowSpec,
    request: SkillGenerationRequest,
    verified_ids: set[str],
) -> tuple[list[FlowCapability], list[UnusedCapability]]:
    caps = [
        cap for cap in spec.capabilities
        if capability_ref(cap) in verified_ids or cap.name in verified_ids
    ]
    if not caps:
        return [], []
    forbidden = _forbidden_capability_ids(caps, request)
    unused = [
        UnusedCapability(
            capability_id=capability_ref(cap),
            name=cap.name,
            title=cap.title or cap.name,
            reason="禁止或限制的操作",
        )
        for cap in caps
        if capability_ref(cap) in forbidden
    ]
    available = [cap for cap in caps if capability_ref(cap) not in forbidden]
    if request.planning_mode == PlanningMode.DYNAMIC:
        return available, unused
    text = _text(request)
    scored = [(cap, _score_capability(cap, text)) for cap in available]
    mentioned = [cap for cap, score in scored if score > 0]
    if not mentioned:
        selected = list(available)
    else:
        selected = mentioned
        families = {_family(cap) for cap in selected}
        if "write" in families and "query" not in families and _mentions(text, _LOOKUP_HINTS):
            selected.extend(cap for cap in available if _family(cap) == "query" and cap not in selected)
    selected_ids = {capability_ref(cap) for cap in selected}
    unused.extend(
        UnusedCapability(
            capability_id=capability_ref(cap),
            name=cap.name,
            title=cap.title or cap.name,
            reason="业务描述未要求该能力",
        )
        for cap in available
        if capability_ref(cap) not in selected_ids
    )
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
            base = "submit_selected" if "query" in families else "submit"
            ids.append(base if total == 1 else f"{base}_{count}")
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
    titles = [_cap_title(cap) for cap in sequence]
    for item in request.example_requests:
        text = str(item).strip()
        if text and not _is_recording_copy(text) and any(title and title in text for title in titles):
            return text
    for item in request.example_requests:
        text = str(item).strip()
        if text and not _is_recording_copy(text):
            return text
    if when_to_use and not _is_recording_copy(when_to_use):
        return when_to_use
    joined = "、".join(title for title in titles if title)
    return f"请{joined}" if joined else "按本页已打包操作办理"


def _cap_title(cap: FlowCapability) -> str:
    return str(cap.title or cap.name or "该操作")


def _object_from_title(title: str) -> str:
    text = str(title or "").strip()
    for prefix in _OBJECT_PREFIXES:
        if text.startswith(prefix):
            rest = text[len(prefix):].strip(" /")
            return rest or text
    return text


def _page_object_name(request: SkillGenerationRequest, selected: list[FlowCapability]) -> str:
    title = str(request.title or "").strip()
    if title and not handbook_text_is_banned(title) and "能力录制" not in title and "等" not in title:
        return title
    first = _cap_title(selected[0]) if selected else ""
    return _object_from_title(first) or first or "本页业务"


def _title_for_ref(selected: list[FlowCapability], cap_id: str) -> str:
    for cap in selected:
        if capability_ref(cap) == cap_id or cap.name == cap_id:
            return _cap_title(cap)
    return ""


def _truncate_playbook(text: str, limit: int = 800) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…"


def _operation_when(cap: FlowCapability) -> str:
    title = _cap_title(cap)
    family = _family(cap)
    if family == "query":
        return f"只要{title}，不要改也不要写"
    if family == "option":
        return f"只要获取「{title}」，不要写入"
    if family == "write":
        return f"要执行「{title}」且已指定对象或已备齐字段时"
    return f"只要「{title}」"


def _binding_note(binding: RouteBinding, selected: list[FlowCapability]) -> str:
    source = _title_for_ref(selected, binding.from_capability)
    target = _title_for_ref(selected, binding.to_capability)
    if source and target:
        return f"{source} 的 {binding.from_output} → {target} 的 {binding.to_input}"
    return f"{binding.from_output} → {binding.to_input}"


def _has_custom_playbook(request: SkillGenerationRequest) -> bool:
    return not is_stock_playbook(request.business_description)


def _build_composition(
    request: SkillGenerationRequest,
    selected: list[FlowCapability],
    routes: list[SkillRoute],
) -> tuple[str, list[str]]:
    titles = [_cap_title(cap) for cap in selected]
    description = str(request.business_description or "").strip()
    page = _page_object_name(request, selected)
    if _has_custom_playbook(request):
        summary = _truncate_playbook(description)
    else:
        actions = "、".join(titles) if titles else "已打包操作"
        summary = (
            f"本页办理{page}：可{actions}。"
            "每次只做用户当前要求的那一件；要先后办理时先查，再请用户指定记录。"
        )
    notes: list[str] = []
    if _has_custom_playbook(request):
        notes.append(f"组合约定：{description}")
        mentioned = [title for title in titles if title and title in description]
        if len(mentioned) >= 2:
            notes.append("用户描述中的先后：" + " → ".join(mentioned) + "。")
    reads = [cap for cap in selected if not is_write_capability(cap)]
    writes = [cap for cap in selected if is_write_capability(cap)]
    if reads and writes:
        notes.append("只要只读操作时，只执行对应只读操作，不得执行写入。")
    combinations = [route for route in routes if len(route.capability_sequence) > 1]
    if combinations:
        for route in combinations:
            sequence = " → ".join(
                _title_for_ref(selected, cap_id) or route.name
                for cap_id in route.capability_sequence
            )
            if route.bindings:
                bound = "；".join(
                    _binding_note(binding, selected)
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
                    "先查再问，下一步输入向用户收集，不得按字段同名猜测。"
                )
    elif reads and writes:
        notes.append(
            "没有已确认绑定，不能自动传值。先后办理就先查再问："
            "先执行只读操作，停下来请用户指定记录，再写。"
        )
    else:
        notes.append("没有自动传值。一次对话只执行用户当前要求的那一件。")
    return summary, [item for item in notes if item and not _is_recording_copy(item)]


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


def _pair_bindings(
    spec: FlowSpec,
    left: FlowCapability | None,
    right: FlowCapability,
    all_bindings: list[RouteBinding],
) -> list[RouteBinding]:
    if left is None:
        return []
    left_ids = {capability_ref(left), left.name, left.capability_id}
    right_ids = {capability_ref(right), right.name, right.capability_id}
    matched = [
        binding
        for binding in all_bindings
        if binding.from_capability in left_ids and binding.to_capability in right_ids
    ]
    if matched:
        return matched
    return _relation_pair(spec, left, right)


def _needs_target(cap: FlowCapability) -> bool:
    required = set(schema_required(cap.input_schema))
    satisfied = set(confirmed_fixed_or_system_inputs(cap))
    return bool(required - satisfied)


def _step_done_when(cap: FlowCapability) -> str:
    title = _cap_title(cap)
    if is_write_capability(cap):
        return f"「{title}」已展示影响、获得确认并执行成功，结果已核对"
    return f"「{title}」已返回可核对的业务结果"


def _step_failure(cap: FlowCapability) -> str:
    title = _cap_title(cap)
    if is_write_capability(cap):
        return f"「{title}」失败、结果未知或用户取消时立即停止，不得静默重试"
    return f"「{title}」失败或结果为空时停止，并说明未继续后续步骤"


def _placeholder_request(sequence: list[FlowCapability], fallback: str) -> str:
    titles = "、".join(_cap_title(cap) for cap in sequence if _cap_title(cap))
    if any(is_write_capability(cap) for cap in sequence) and len(sequence) > 1:
        return f"请先查出目标，再办理{titles}。目标用 <业务编号> 占位，不要填历史样本。"
    if titles:
        return fallback if fallback and "<" in fallback else f"请办理{titles}，必要输入用 <字段> 占位"
    return fallback


def _route(
    *,
    route_id: str,
    name: str,
    when_to_use: str,
    sequence: list[FlowCapability],
    bindings: list[RouteBinding],
    request: SkillGenerationRequest,
    extra_preconditions: list[str] | None = None,
    independent: bool = False,
    spec: FlowSpec | None = None,
) -> SkillRoute:
    cap_ids = [capability_ref(cap) for cap in sequence]
    step_ids = _step_ids_for(sequence)
    annotated = _annotate_bindings(sequence, step_ids, bindings)
    bound_by_cap: dict[str, set[str]] = {}
    for binding in annotated:
        bound_by_cap.setdefault(binding.to_capability, set()).add(binding.to_input)
    required: list[str] = []
    steps: list[RouteStep] = []
    checkpoints: list[HumanCheckpoint] = []
    mode = CompositionMode.ATOMIC
    if len(sequence) > 1:
        if annotated:
            mode = CompositionMode.BOUND
        elif independent:
            mode = CompositionMode.INDEPENDENT
        else:
            mode = CompositionMode.HANDOFF
    for index, cap in enumerate(sequence):
        cap_id = capability_ref(cap)
        prev = sequence[index - 1] if index else None
        step_key = step_ids[index]
        pair = _pair_bindings(spec, prev, cap, annotated) if spec is not None else [
            binding for binding in annotated if binding.to_capability in {cap_id, cap.name}
        ]
        pair = _annotate_bindings(sequence, step_ids, pair)
        bound_fields = {binding.to_input for binding in pair if binding.to_input}
        bound_by_cap.setdefault(cap_id, set()).update(bound_fields)
        user_fields = _required_user_inputs(cap, bound_fields)
        required.extend(user_fields)
        sources: list[StepInputSource] = []
        satisfied = confirmed_fixed_or_system_inputs(cap)
        for field, kind in satisfied.items():
            sources.append(StepInputSource(
                field=field,
                source=InputSourceKind.SYSTEM_CONTEXT if kind == "system_value" else InputSourceKind.FIXED_VALUE,
            ))
        for binding in pair:
            if binding.to_input:
                sources.append(StepInputSource(
                    field=binding.to_input,
                    source=InputSourceKind.CONFIRMED_BINDING,
                    from_step_key=binding.from_step or (step_ids[index - 1] if index else ""),
                    notes=f"{binding.from_output} → {binding.to_input}",
                ))
        for field in user_fields:
            sources.append(StepInputSource(field=field, source=InputSourceKind.USER))
        checkpoint = None
        if (
            prev is not None
            and not pair
            and not independent
            and _needs_target(cap)
            and not is_write_capability(prev)
        ):
            checkpoint = HumanCheckpoint(
                after_step=step_ids[index - 1],
                before_step=step_key,
                required_fields=user_fields,
                prompt=f"请从「{_cap_title(prev)}」的结果中选定下一步「{_cap_title(cap)}」的目标，不要默认第一条",
                choice_source="previous_result",
                selection_mode="single",
                resume_when="用户已选定有效目标并通过输入校验",
                on_cancel="停止并报告未执行",
            )
            checkpoints.append(checkpoint)
            if steps:
                steps[-1] = steps[-1].model_copy(update={"checkpoint": checkpoint})
            mode = CompositionMode.HANDOFF
        confirm = is_write_capability(cap)
        steps.append(RouteStep(
            step_key=step_key,
            capability_id=cap_id,
            input_sources=sources,
            bindings=pair,
            checkpoint=None,
            confirm_before_execute=confirm,
            done_when=_step_done_when(cap),
            on_failure=_step_failure(cap),
        ))
    required = list(dict.fromkeys(required))
    writes = [cap for cap in sequence if is_write_capability(cap)]
    confirmation = [_cap_title(cap) for cap in writes]
    done = str(request.success_criteria or "").strip() or (
        "写操作已确认并执行成功，结果已核对" if writes else "已返回可核对的查询结果"
    )
    cleaned_when = _clean_when(
        when_to_use,
        " → ".join(_cap_title(cap) for cap in sequence) or "按本页已打包操作办理",
    )
    example_request = _placeholder_request(sequence, _example_request(request, cleaned_when, sequence))
    failure = "任一能力失败立即停止；写操作结果不明时不得重试，先用已有只读能力核查。用户取消或候选无效时停止并报告未执行。"
    if request.forbidden_actions:
        failure = f"{failure} 禁止：{request.forbidden_actions}"
    ask_at = [
        f"{item.after_step} → {item.before_step}"
        for item in checkpoints
    ]
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
        composition_mode=mode,
        steps=steps,
        checkpoints=checkpoints,
        examples=[
            RouteExample(
                user_request=example_request,
                route_id=route_id,
                collected_fields=required,
                capability_sequence=cap_ids,
                bindings=list(annotated),
                confirmation_points=confirmation,
                done_when=done,
                input_origins=[
                    f"{source.field}:{source.source}"
                    for step in steps
                    for source in step.input_sources
                ],
                auto_bound_fields=[
                    f"{binding.from_output}→{binding.to_input}"
                    for binding in annotated
                    if binding.from_output and binding.to_input
                ],
                ask_at=ask_at,
                confirm_at=confirmation,
                on_cancel="用户取消或拒绝确认后停止，不执行后续写入",
                on_empty_or_ambiguous="候选为空或多条且要求单条时停问，不得默认第一条",
                on_unknown_write_result="写入结果未知时停止并请人处理，不得重试",
            )
        ],
    )


def _caps_by_id(selected: list[FlowCapability]) -> dict[str, FlowCapability]:
    index: dict[str, FlowCapability] = {}
    for cap in selected:
        for key in (capability_ref(cap), cap.capability_id, cap.name):
            if key:
                index[str(key)] = cap
    return index


def _route_merge_key(route: SkillRoute) -> tuple:
    sources = tuple(
        (step.step_key, source.field, str(source.source))
        for step in route.steps
        for source in step.input_sources
    )
    bindings = tuple(
        (binding.from_capability, binding.from_output, binding.to_capability, binding.to_input)
        for binding in route.bindings
    )
    checks = tuple((item.after_step, item.before_step, tuple(item.required_fields)) for item in route.checkpoints)
    return (
        tuple(route.capability_sequence),
        sources,
        bindings,
        checks,
        str(route.composition_mode),
        bool(route.requires_confirmation),
        route.done_when,
    )


def _merge_equivalent_routes(routes: list[SkillRoute]) -> list[SkillRoute]:
    merged: list[SkillRoute] = []
    index: dict[tuple, int] = {}
    for route in routes:
        key = _route_merge_key(route)
        existing = index.get(key)
        if existing is None:
            index[key] = len(merged)
            merged.append(route)
            continue
        current = merged[existing]
        examples = list(current.examples)
        for example in route.examples:
            if example.user_request and all(example.user_request != item.user_request for item in examples):
                examples.append(example)
        merged[existing] = current.model_copy(update={"examples": examples})
    return merged


def _compile_branch_route(
    branch: IntentBranch,
    spec: FlowSpec,
    selected: list[FlowCapability],
    request: SkillGenerationRequest,
    queries: list[FlowCapability],
) -> SkillRoute | str:
    caps_index = _caps_by_id(selected)
    sequence = [caps_index[cap_id] for cap_id in branch.capability_sequence if cap_id in caps_index]
    if len(sequence) != len(branch.capability_sequence):
        return f"无法把「{branch.trigger}」映射到已验证能力，请说明要使用哪一个已有操作"
    if not sequence:
        return branch.unresolved[0] if branch.unresolved else f"无法解释「{branch.trigger}」"
    text = _text(request)
    if len(sequence) == 1 and is_write_capability(sequence[0]) and _mentions(text, _LOOKUP_HINTS):
        sequence = _append_lookup(sequence, queries, text)
    bindings: list[RouteBinding] = []
    for left, right in zip(sequence, sequence[1:], strict=False):
        bindings.extend(_relation_pair(spec, left, right))
    if len(sequence) == 1:
        route_id = "query_only" if _family(sequence[0]) == "query" else _operation_route_id(sequence[0])
        if is_write_capability(sequence[0]) and len([cap for cap in selected if is_write_capability(cap)]) == 1:
            route_id = "write_direct"
        when = _operation_when(sequence[0])
        if branch.target_given and is_write_capability(sequence[0]):
            when = f"要执行「{_cap_title(sequence[0])}」且目标或必要字段已经给出"
    else:
        tail = sequence[-1]
        head = sequence[0]
        if _family(head) == "query" and is_write_capability(tail) and len(sequence) <= 3:
            route_id = "query_then_write" if sum(1 for cap in selected if is_write_capability(cap)) == 1 else f"query_then_{capability_ref(tail)}"
        else:
            route_id = "handoff_" + "_".join(capability_ref(cap) for cap in sequence)
        when = branch.trigger if len(branch.trigger) <= 80 else f"按「{_cap_title(head)} → {_cap_title(tail)}」办理"
        sequence = _append_lookup(sequence, queries, text)
        bindings = []
        for left, right in zip(sequence, sequence[1:], strict=False):
            bindings.extend(_relation_pair(spec, left, right))
    return _route(
        route_id=route_id,
        name=" → ".join(_cap_title(cap) for cap in sequence),
        when_to_use=when,
        sequence=sequence,
        bindings=bindings,
        request=request,
        independent=branch.independent,
        spec=spec,
    )


def _confirmed_relation_routes(
    spec: FlowSpec,
    selected: list[FlowCapability],
    request: SkillGenerationRequest,
    mentioned: set[str],
    queries: list[FlowCapability],
    options: list[FlowCapability],
) -> list[SkillRoute]:
    routes: list[SkillRoute] = []
    writes = [cap for cap in selected if is_write_capability(cap)]
    for write in writes:
        if capability_ref(write) not in mentioned and write.name not in mentioned:
            continue
        if queries:
            bindings = _relation_pair(spec, queries[0], write)
            if bindings:
                routes.append(_route(
                    route_id="query_then_write" if len(writes) == 1 else f"query_then_{capability_ref(write)}",
                    name=f"查询后{_cap_title(write)}",
                    when_to_use=f"需要先查询记录，再对选中记录执行「{_cap_title(write)}」",
                    sequence=_append_lookup([queries[0], write], queries, _text(request)),
                    bindings=bindings,
                    request=request,
                    spec=spec,
                ))
        if options:
            bindings = _relation_pair(spec, options[0], write)
            if bindings:
                routes.append(_route(
                    route_id="option_then_write" if len(writes) == 1 else f"option_then_{capability_ref(write)}",
                    name=f"选项后{_cap_title(write)}",
                    when_to_use=f"「{_cap_title(write)}」字段需要从选项中选择",
                    sequence=_append_lookup([options[0], write], queries, _text(request)),
                    bindings=bindings,
                    request=request,
                    spec=spec,
                ))
    return routes


def _atomic_fallback_routes(
    selected: list[FlowCapability],
    request: SkillGenerationRequest,
    queries: list[FlowCapability],
    spec: FlowSpec,
    existing: list[SkillRoute],
) -> list[SkillRoute]:
    existing_singles = {
        route.capability_sequence[0]
        for route in existing
        if len(route.capability_sequence) == 1
    }
    routes: list[SkillRoute] = []
    writes = [cap for cap in selected if is_write_capability(cap)]
    for cap in selected:
        cap_id = capability_ref(cap)
        if cap_id in existing_singles:
            continue
        sequence = [cap]
        extra = None
        route_id = _operation_route_id(cap)
        if _family(cap) == "query" and "query_only" not in {route.route_id for route in existing}:
            route_id = "query_only"
        elif is_write_capability(cap) and len(writes) == 1:
            sequence = _append_lookup([cap], queries, _text(request))
            route_id = "write_direct"
            extra = (
                ["提交后回查使用独立步骤身份，不得单独生成 C3→C1 路线"]
                if len(sequence) > 1
                else None
            )
        routes.append(_route(
            route_id=route_id,
            name=_cap_title(cap),
            when_to_use=_operation_when(cap),
            sequence=sequence,
            bindings=[],
            request=request,
            extra_preconditions=extra,
            spec=spec,
        ))
        existing_singles.add(cap_id)
    return routes


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
    branches = extract_intent_branches(request, selected)
    forbidden_ids = {item.capability_id for item in unused if "禁止" in item.reason or "限制" in item.reason}
    clarifications: list[str] = []
    routes: list[SkillRoute] = []

    if request.planning_mode == PlanningMode.FIXED:
        compiled: list[FlowCapability] = []
        for branch in branches:
            if branch_needs_clarification(branch):
                clarifications.extend(branch.unresolved or [f"请澄清「{branch.trigger}」要走哪一条顺序"])
                continue
            if len(branch.capability_sequence) >= 2:
                caps_index = _caps_by_id(selected)
                compiled = [caps_index[item] for item in branch.capability_sequence if item in caps_index]
                break
        sequence: list[FlowCapability] = list(compiled)
        bindings: list[RouteBinding] = []
        if not sequence:
            if queries and (_mentions(text, _QUERY_HINTS) or writes):
                sequence.append(queries[0])
            if writes:
                write = writes[0]
                if sequence:
                    pair = _relation_pair(spec, sequence[-1], write)
                    if pair:
                        bindings.extend(pair)
                sequence.append(write)
        else:
            for left, right in zip(sequence, sequence[1:], strict=False):
                bindings.extend(_relation_pair(spec, left, right))
        sequence = _append_lookup(sequence, queries, text)
        if not sequence:
            sequence = list(selected[:1] or spec.capabilities[:1])
        routes.append(_route(
            route_id="main",
            name=" → ".join(_cap_title(cap) for cap in sequence) or "主要业务步骤",
            when_to_use=_clean_when(
                request.business_description if _has_custom_playbook(request) else "",
                "按用户描述的顺序办理本页已选操作",
            ),
            sequence=sequence,
            bindings=bindings,
            request=request,
            extra_preconditions=(
                ["回查使用独立步骤身份 query_before / submit_selected / query_after"]
                if _mentions(text, _LOOKUP_HINTS) and queries
                else None
            ),
            spec=spec,
        ))
    else:
        mentioned = {
            cap_id
            for branch in branches
            for cap_id in branch.capability_sequence
        }
        write_mentioned = any(
            _cap_title(cap) in text or cap.name in text or any(token in text for token in ("提交", "编辑", "审核", "删除", "新增", "新建"))
            for cap in writes
        )
        for branch in branches:
            if any(cap_id in forbidden_ids for cap_id in branch.capability_sequence):
                continue
            if branch_needs_clarification(branch):
                clarifications.extend(branch.unresolved or [f"请澄清「{branch.trigger}」"])
                continue
            compiled_route = _compile_branch_route(branch, spec, selected, request, queries)
            if isinstance(compiled_route, str):
                clarifications.append(compiled_route)
                continue
            routes.append(compiled_route)
        relation_ids = {capability_ref(cap) for cap in selected} if write_mentioned else mentioned
        if relation_ids:
            routes.extend(_confirmed_relation_routes(spec, selected, request, relation_ids, queries, options))
        elif not branches:
            routes.extend(_confirmed_relation_routes(
                spec, selected, request, {capability_ref(cap) for cap in selected}, queries, options,
            ))
        if not routes and selected:
            routes.append(_route(
                route_id="single",
                name=_cap_title(selected[0]),
                when_to_use=_clean_when(
                    request.business_description if _has_custom_playbook(request) else "",
                    _operation_when(selected[0]),
                ),
                sequence=selected[:1],
                bindings=[],
                request=request,
                spec=spec,
            ))
        routes.extend(_atomic_fallback_routes(selected, request, queries, spec, routes))
        routes = _merge_equivalent_routes(routes)

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
        "只使用当前页面已打包操作，不得发明字段、接口或输出。",
        "写操作必须先取得用户确认。",
        "只有已确认绑定可以自动带入跨步骤字段；没有绑定就在交接点停问。",
        "不得输出 token、cookie、storage_state 或密码。",
    ]
    if request.forbidden_actions:
        safety.append(f"禁止或限制：{request.forbidden_actions}")
    triggers = [
        str(item).strip()
        for item in request.example_requests
        if str(item).strip() and not _is_recording_copy(item)
    ]
    for route in routes:
        when = str(route.when_to_use or "").strip()
        if when and when not in triggers and not _is_recording_copy(when):
            triggers.append(when)
    composition_summary, composition_notes = _build_composition(request, selected, routes)
    summary = request.business_description.strip()
    if is_stock_playbook(summary):
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
        clarification_questions=list(dict.fromkeys(item for item in clarifications if item)),
        composition_summary=composition_summary,
        composition_notes=composition_notes,
        intent_branches=branches,
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


def _clean_phrase(value: Any) -> str:
    text = str(value or "").strip()
    if not text or _is_recording_copy(text):
        return ""
    return text


def _merge_proposed_plan(fallback: SkillPlan, proposed: SkillPlan) -> SkillPlan:
    """Keep recorded structure; take model wording only."""
    if not _plan_is_usable(proposed):
        return fallback
    overlay = {
        tuple(route.capability_sequence): route
        for route in proposed.routes
        if route.capability_sequence
    }
    merged_routes: list[SkillRoute] = []
    for route in fallback.routes:
        extra = overlay.get(tuple(route.capability_sequence))
        payload = route.model_dump()
        if extra is not None:
            name = _clean_phrase(extra.name)
            if name:
                payload["name"] = name
            when = _clean_phrase(extra.when_to_use)
            if when:
                payload["when_to_use"] = when
            done = _clean_phrase(extra.done_when)
            if done:
                payload["done_when"] = done
            failure = _clean_phrase(extra.failure_behavior)
            if failure:
                payload["failure_behavior"] = failure
            if extra.steps and route.steps:
                polished = []
                for frozen, incoming in zip(route.steps, extra.steps, strict=False):
                    step_payload = frozen.model_dump()
                    step_done = _clean_phrase(incoming.done_when)
                    if step_done:
                        step_payload["done_when"] = step_done
                    step_fail = _clean_phrase(incoming.on_failure)
                    if step_fail:
                        step_payload["on_failure"] = step_fail
                    if frozen.checkpoint is not None and incoming.checkpoint is not None:
                        prompt = _clean_phrase(incoming.checkpoint.prompt)
                        if prompt:
                            step_payload["checkpoint"] = frozen.checkpoint.model_copy(update={"prompt": prompt}).model_dump()
                    polished.append(step_payload)
                payload["steps"] = polished
            examples = []
            for example in extra.examples:
                request_text = _clean_phrase(example.user_request)
                if not request_text:
                    continue
                if example.capability_sequence and example.capability_sequence != list(route.capability_sequence):
                    continue
                base = route.examples[0] if route.examples else None
                examples.append(
                    RouteExample(
                        user_request=request_text,
                        route_id=route.route_id,
                        collected_fields=list(route.required_user_inputs),
                        capability_sequence=list(route.capability_sequence),
                        bindings=list(route.bindings),
                        confirmation_points=list(
                            example.confirmation_points
                            or (base.confirmation_points if base else [])
                        ),
                        done_when=done or route.done_when,
                        input_origins=list(base.input_origins if base else []),
                        auto_bound_fields=list(base.auto_bound_fields if base else []),
                        ask_at=list(base.ask_at if base else []),
                        confirm_at=list(base.confirm_at if base else []),
                        on_cancel=base.on_cancel if base else route.failure_behavior,
                        on_empty_or_ambiguous=base.on_empty_or_ambiguous if base else "",
                        on_unknown_write_result=base.on_unknown_write_result if base else "",
                    )
                )
            if examples:
                payload["examples"] = [examples[0].model_dump()]
        payload["bindings"] = [item.model_dump() for item in route.bindings]
        payload["capability_sequence"] = list(route.capability_sequence)
        payload["step_ids"] = list(route.step_ids)
        payload["composition_mode"] = route.composition_mode
        payload["checkpoints"] = [item.model_dump() for item in route.checkpoints]
        merged_routes.append(SkillRoute.model_validate(payload))
    return fallback.model_copy(update={
        "routes": merged_routes,
        "selected_capability_ids": list(fallback.selected_capability_ids),
        "unused_capabilities": list(fallback.unused_capabilities),
        "summary": fallback.summary,
        "trigger_phrases": list(fallback.trigger_phrases),
        "composition_summary": fallback.composition_summary,
        "composition_notes": list(fallback.composition_notes),
        "intent_branches": list(fallback.intent_branches),
        "clarification_questions": list(fallback.clarification_questions),
    })


async def _llm_propose(
    spec: FlowSpec,
    request: SkillGenerationRequest,
    verified_ids: set[str],
    source_flow_fingerprint: str,
    *,
    frozen: SkillPlan,
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
        "task": "不要重新规划能力或路线。只润色已冻结规划的自然语言，输出完整 JSON。",
        "purpose": (
            "输出给办理本页业务的 Agent 阅读。"
            "禁止阶段号、禁止录制过程、禁止把操作名复读成清单。"
        ),
        "rules": [
            "selected_capability_ids、unused_capabilities、intent_branches、每条 route 的 route_id、capability_sequence、bindings、step_ids、steps、checkpoints、composition_mode 必须与 frozen_plan 完全一致",
            "只能改 name、when_to_use、done_when、failure_behavior、checkpoint.prompt、examples.user_request 的措辞",
            "不得新增未验证能力、不得新增或改变绑定、不得改变能力顺序、不得删除确认门禁",
            "不得把人工交接改成自动传值，不得把未知结果改成成功",
            "when_to_use 和例句用用户说法，示例 ID/姓名/日期必须用 <占位符>",
            "禁止出现：阶段、原子能力、录制、FlowSpec、fingerprint、capability_id、规划依据",
        ],
        "request": request.model_dump(mode="json"),
        "frozen_plan": frozen.model_dump(mode="json"),
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
                proposed = _merge_proposed_plan(
                    fallback,
                    _parse_proposed_plan(
                        await _llm_propose(
                            spec, request, verified_capability_ids, source_flow_fingerprint,
                            frozen=fallback,
                        ),
                        fallback,
                    ),
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
                    summary="模型用语已并入确定性规划，路线结构未改",
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
                frozen=fallback,
                repair_errors=checked.errors + checked.clarifications,
            )
        except Exception as exc:  # noqa: BLE001 - one repair only, then report
            errors.append(str(exc) or "规划修复失败")
        else:
            proposed = _merge_proposed_plan(fallback, _parse_proposed_plan(proposed, fallback))
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
