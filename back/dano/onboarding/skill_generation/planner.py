"""Project a capability contract into a deterministic SkillPlan."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from dano.execution.page.flow_spec_core.models import FlowCapability, FlowSpec
from dano.onboarding.skill_generation.catalog import (
    capability_family,
    capability_ref,
    confirmed_fixed_or_system_inputs,
    distinct_stage8_capabilities,
    is_write_capability,
    schema_properties,
    schema_required,
    usable_relations,
)
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

_OBJECT_PREFIXES = (
    "搜索/筛选", "搜索", "筛选", "查询", "查看", "新增", "新建", "修改", "编辑",
    "审批", "审核", "反审", "反审核", "删除", "提交", "办理",
)


def _stable_route_id(sequence: list[FlowCapability]) -> str:
    raw = "-然后-".join(_cap_title(cap) for cap in sequence if _cap_title(cap)) or "业务路线"
    safe = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "-", raw)
    safe = re.sub(r"\s+", "-", safe).strip(" .-")
    if len(safe) > 96:
        suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        safe = f"{safe[:85].rstrip(' .-')}-{suffix}"
    return safe or "业务路线"


def _route_done_when(request: SkillGenerationRequest, writes: list[FlowCapability]) -> str:
    del request
    if writes:
        return "用户已确认且写操作返回成功；有可用只读核查时已核查，否则明确标记为未回查"
    return "已返回可核对的查询结果"


def _family(cap: FlowCapability, spec: FlowSpec | None = None) -> str:
    return capability_family(cap, spec)


def _select_capabilities(
    spec: FlowSpec,
    request: SkillGenerationRequest,
    verified_ids: set[str],
) -> tuple[list[FlowCapability], list[UnusedCapability]]:
    del request
    verified = [
        cap for cap in spec.capabilities
        if capability_ref(cap) in verified_ids or cap.name in verified_ids
    ]
    caps, duplicates = distinct_stage8_capabilities(spec, verified)
    if not caps:
        return [], []
    unused = [
        UnusedCapability(
            capability_id=capability_ref(cap),
            name=cap.name,
            title=cap.title or cap.name,
            reason=duplicates[capability_ref(cap)],
        )
        for cap in verified
        if capability_ref(cap) in duplicates
    ]
    return list(caps), unused


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
    properties = set(schema_properties(cap.input_schema))
    return [
        field
        for field in schema_required(cap.input_schema)
        if field in properties and field not in satisfied and field not in bound_inputs
    ]


def _step_ids_for(sequence: list[FlowCapability], spec: FlowSpec | None = None) -> list[str]:
    families = [_family(cap, spec) for cap in sequence]
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


def _normalize_intent_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s*[,，]\s*", "，", text)
    return re.sub(r" {2,}", " ", text)


def _clean_when(value: Any, fallback: str) -> str:
    text = _normalize_intent_text(value)
    if text:
        return text
    return fallback


def _example_request(
    request: SkillGenerationRequest,
    when_to_use: str,
    sequence: list[FlowCapability],
) -> str:
    del request
    titles = [_cap_title(cap) for cap in sequence]
    if when_to_use:
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
    del request
    first = _cap_title(selected[0]) if selected else ""
    return _object_from_title(first) or first or "本页业务"


def _title_for_ref(selected: list[FlowCapability], cap_id: str) -> str:
    for cap in selected:
        if capability_ref(cap) == cap_id or cap.name == cap_id:
            return _cap_title(cap)
    return ""


def _operation_when(cap: FlowCapability, spec: FlowSpec | None = None) -> str:
    title = _cap_title(cap)
    family = _family(cap, spec)
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


def _build_composition(
    request: SkillGenerationRequest,
    selected: list[FlowCapability],
    routes: list[SkillRoute],
    spec: FlowSpec | None = None,
) -> tuple[str, list[str]]:
    titles = [_cap_title(cap) for cap in selected]
    page = _page_object_name(request, selected)
    actions = "、".join(titles) if titles else "已打包操作"
    summary = f"本页办理{page}：可{actions}。每次只执行用户当前要求的能力。"
    notes: list[str] = []
    combinations = [route for route in routes if len(route.capability_sequence) > 1]
    reads = [cap for cap in selected if not is_write_capability(cap, spec)]
    writes = [cap for cap in selected if is_write_capability(cap, spec)]
    if reads and writes:
        notes.append("只要只读操作时，只执行对应只读操作，不得执行写入。")
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
            "能力合同没有声明关联关系，因此不能自动传值或编排先后顺序；"
            "只执行用户当前要求的一项业务操作。"
        )
    else:
        notes.append("没有自动传值。一次对话只执行用户当前要求的一项业务操作。")
    return summary, [item for item in notes if item]


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
    return [
        binding
        for binding in _relation_pair(spec, left, right)
        if binding.from_output and binding.to_input
    ]


def _needs_target(cap: FlowCapability) -> bool:
    required = set(schema_required(cap.input_schema))
    satisfied = set(confirmed_fixed_or_system_inputs(cap))
    return bool(required - satisfied)


def _is_create(cap: FlowCapability) -> bool:
    return str(cap.kind or "").strip().lower() == "create" or any(
        token in _cap_title(cap) for token in ("新增", "新建", "创建", "录入", "添加")
    )


def _step_done_when(cap: FlowCapability, spec: FlowSpec | None = None) -> str:
    title = _cap_title(cap)
    if is_write_capability(cap, spec):
        return f"「{title}」已展示影响、获得确认且返回成功；未配置只读回查时不得宣称业务状态已复核"
    return f"「{title}」已返回可核对的业务结果"


def _step_failure(cap: FlowCapability, spec: FlowSpec | None = None) -> str:
    title = _cap_title(cap)
    if is_write_capability(cap, spec):
        return f"「{title}」失败、结果未知或用户取消时立即停止，不得静默重试"
    return f"「{title}」失败或结果为空时停止，并说明未继续后续步骤"


def _placeholder_request(sequence: list[FlowCapability], fallback: str, spec: FlowSpec | None = None) -> str:
    titles = [_cap_title(cap) for cap in sequence if _cap_title(cap)]
    if len(titles) > 1:
        tail = "，再".join(titles[1:])
        if _family(sequence[0], spec) == "query":
            last = sequence[-1]
            if _is_create(last):
                return f"先帮我{titles[0]}，确认哪些项目还需要处理后再{tail}"
            return f"先帮我{titles[0]}，结果出来后让我选择目标，再{tail}"
        return f"先帮我{titles[0]}，完成后再{tail}"
    if titles:
        return fallback if fallback and not fallback.startswith("按「") else f"帮我{titles[0]}"
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
    step_ids = _step_ids_for(sequence, spec)
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
            and (
                _needs_target(cap)
                or is_write_capability(cap, spec)
                or _is_create(cap)
            )
        ):
            previous_is_write = is_write_capability(prev, spec)
            create_handoff = _family(prev, spec) == "query" and _is_create(cap)
            field_labels = "、".join(f"`{field}`" for field in user_fields) or "必要字段"
            checkpoint = HumanCheckpoint(
                after_step=step_ids[index - 1],
                before_step=step_key,
                required_fields=user_fields,
                prompt=(
                    f"请根据「{_cap_title(prev)}」的结果确认哪些项目仍需新增，再按「{_cap_title(cap)}」输入表单补齐结构化内容（{field_labels}，以及该步骤仍缺的字段）；"
                    "不得把查询结果直接当作新增输入"
                    if create_handoff
                    else
                    f"请确认下一步「{_cap_title(cap)}」的目标和必要字段，不得沿用或猜测上一步输入"
                    if previous_is_write
                    else f"请从「{_cap_title(prev)}」的结果中选定下一步「{_cap_title(cap)}」的目标，不要默认第一条"
                ),
                choice_source="free_text" if previous_is_write or create_handoff else "previous_result",
                selection_mode="text" if previous_is_write or create_handoff else "single",
                resume_when=(
                    "用户已确认剩余范围、调用方已提供结构化内容且输入校验通过"
                    if create_handoff
                    else "用户已选定有效目标并通过输入校验"
                ),
                on_cancel="停止并报告未执行",
            )
            checkpoints.append(checkpoint)
            if steps:
                steps[-1] = steps[-1].model_copy(update={"checkpoint": checkpoint})
            mode = CompositionMode.HANDOFF
        confirm = bool(cap.requires_human_confirm) or is_write_capability(cap, spec)
        steps.append(RouteStep(
            step_key=step_key,
            capability_id=cap_id,
            input_sources=sources,
            bindings=pair,
            checkpoint=None,
            confirm_before_execute=confirm,
            done_when=_step_done_when(cap, spec),
            on_failure=_step_failure(cap, spec),
        ))
    required = list(dict.fromkeys(required))
    writes = [cap for cap in sequence if is_write_capability(cap, spec)]
    confirmation = [
        _cap_title(cap)
        for cap in sequence
        if cap.requires_human_confirm or is_write_capability(cap, spec)
    ]
    done = _route_done_when(request, writes)
    cleaned_when = _clean_when(
        when_to_use,
        " → ".join(_cap_title(cap) for cap in sequence) or "按本页已打包操作办理",
    )
    example_request = _placeholder_request(
        sequence,
        _example_request(request, cleaned_when, sequence),
        spec,
    )
    failure = "任一能力失败立即停止；写操作结果不明时不得重试，先用已有只读能力核查。用户取消或候选无效时停止并报告未执行。"
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
        requires_confirmation=any(step.confirm_before_execute for step in steps),
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


def _confirmed_relation_routes(
    spec: FlowSpec,
    selected: list[FlowCapability],
    request: SkillGenerationRequest,
) -> list[SkillRoute]:
    """Compile only relations explicitly present in the capability contract."""

    by_ref = _caps_by_id(selected)
    routes: list[SkillRoute] = []
    seen: set[tuple[str, str]] = set()
    for relation in usable_relations(spec):
        left = by_ref.get(str(relation.from_capability or ""))
        right = by_ref.get(str(relation.to_capability or ""))
        if left is None or right is None or left is right:
            continue
        pair = (capability_ref(left), capability_ref(right))
        if pair in seen:
            continue
        seen.add(pair)
        sequence = [left, right]
        bindings = [
            binding
            for binding in _relation_pair(spec, left, right)
            if binding.from_output and binding.to_input
        ]
        routes.append(_route(
            route_id=_stable_route_id(sequence),
            name=f"{_cap_title(left)} → {_cap_title(right)}",
            when_to_use=(
                str(relation.reason or "").strip()
                or f"按能力合同先「{_cap_title(left)}」，再「{_cap_title(right)}」"
            ),
            sequence=sequence,
            bindings=bindings,
            request=request,
            spec=spec,
        ))
    return routes


def _atomic_fallback_routes(
    selected: list[FlowCapability],
    request: SkillGenerationRequest,
    spec: FlowSpec,
    existing: list[SkillRoute],
) -> list[SkillRoute]:
    existing_singles = {
        route.capability_sequence[0]
        for route in existing
        if len(route.capability_sequence) == 1
    }
    routes: list[SkillRoute] = []
    for cap in selected:
        cap_id = capability_ref(cap)
        if cap_id in existing_singles:
            continue
        sequence = [cap]
        routes.append(_route(
            route_id=_stable_route_id(sequence),
            name=_cap_title(cap),
            when_to_use=_operation_when(cap, spec),
            sequence=sequence,
            bindings=[],
            request=request,
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
    # The Stage-8 plan is a projection of the capability contract. Free-form
    # export prose must not select capabilities, create relationships, or set
    # execution order.
    branches: list[IntentBranch] = []
    clarifications: list[str] = []
    routes = _confirmed_relation_routes(spec, selected, request)
    routes.extend(_atomic_fallback_routes(selected, request, spec, routes))
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
    # Triggers are capability/route facts too.  User-authored examples are not
    # allowed to become executable behavior unless the capability contract
    # already produced a matching route.
    triggers: list[str] = []
    for route in routes:
        when = str(route.when_to_use or "").strip()
        if when and when not in triggers:
            triggers.append(when)
    composition_summary, composition_notes = _build_composition(request, selected, routes, spec)
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


async def generate_skill_plan(
    spec: FlowSpec,
    request: SkillGenerationRequest,
    *,
    verified_capability_ids: set[str],
    source_flow_fingerprint: str,
    proposer: PlanProposer | None = None,
) -> SkillGenerationResult:
    # Kept only for API compatibility. Stage 8 never delegates capability facts,
    # routes, or handbook wording to a model.
    del proposer
    if not verified_capability_ids:
        _log_plan("skill.plan.failed", summary="没有可导出能力", status="failed", level="error")
        return SkillGenerationResult(
            status="generation_failed",
            errors=["没有可导出能力，不能生成 Skill"],
        )

    fallback = propose_deterministic_plan(spec, request, verified_capability_ids, source_flow_fingerprint)
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
    _log_plan(
        "skill.plan.projected",
        summary="已按能力原样生成规划",
        status="succeeded",
        used_external_proposer=False,
        selected_capability_ids=list(fallback.selected_capability_ids),
        route_ids=[route.route_id for route in fallback.routes],
    )
    return SkillGenerationResult(status="planned", plan=fallback)
