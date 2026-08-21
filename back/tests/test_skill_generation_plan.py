"""Deterministic stage-8 SkillPlan validation and capability selection."""

from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import CapabilityRelation, FlowCapability, FlowSpec, FlowStep
from dano.onboarding.skill_generation import (
    PlanningMode,
    SkillGenerationRequest,
    generate_skill_plan,
    propose_deterministic_plan,
    validate_skill_plan,
)
from dano.onboarding.skill_generation.planner import _merge_proposed_plan
from dano.onboarding.skill_generation.models import RouteBinding, RouteExample, SkillPlan, SkillRoute, UnusedCapability


def _cap(
    *,
    capability_id: str,
    name: str,
    title: str,
    kind: str,
    required: list[str] | None = None,
    input_props: dict | None = None,
    output_props: dict | None = None,
    confirm: bool = False,
) -> FlowCapability:
    properties = input_props or {item: {"type": "string"} for item in (required or [])}
    return FlowCapability(
        capability_id=capability_id,
        name=name,
        title=title,
        kind=kind,
        requires_human_confirm=confirm or kind in {"submit", "delete", "withdraw"},
        input_schema={
            "type": "object",
            "properties": properties,
            "required": list(required or []),
        },
        output_schema={
            "type": "object",
            "properties": output_props or {},
        },
    )


def _three_cap_spec(*, confirmed_query_submit: bool = True, confirmed_option_submit: bool = True) -> FlowSpec:
    relations = []
    if confirmed_query_submit:
        relations.append(CapabilityRelation(
            relation_id="rel_query_submit",
            type="data_mapping",
            mode="field_mapping",
            from_capability="cap_query",
            from_output="records[].id",
            to_capability="cap_submit",
            to_input="id",
            confirmed=True,
            transform_owner="caller",
            source_selector="records[].id",
            target_path="id",
        ))
    if confirmed_option_submit:
        relations.append(CapabilityRelation(
            relation_id="rel_option_submit",
            type="data_mapping",
            mode="field_mapping",
            from_capability="cap_option",
            from_output="options[].value",
            to_capability="cap_submit",
            to_input="status",
            confirmed=True,
            transform_owner="caller",
            source_selector="options[].value",
            target_path="status",
        ))
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        title="请假管理",
        steps=[
            FlowStep(step_id="s1", method="GET", path="/oa/leave/page"),
            FlowStep(step_id="s2", method="GET", path="/oa/leave/options"),
            FlowStep(step_id="s3", method="POST", path="/oa/leave/submit"),
        ],
        capabilities=[
            _cap(
                capability_id="cap_query",
                name="query_leave",
                title="查询待办记录",
                kind="query",
                output_props={"records": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}}}}},
            ),
            _cap(
                capability_id="cap_option",
                name="query_leave_options",
                title="查询请假选项",
                kind="list_options",
                output_props={"options": {"type": "array", "items": {"type": "object", "properties": {"value": {"type": "string"}}}}},
            ),
            _cap(
                capability_id="cap_submit",
                name="submit_leave",
                title="提交请假",
                kind="submit",
                required=["id", "status"],
                input_props={"id": {"type": "string"}, "status": {"type": "string"}},
                confirm=True,
            ),
        ],
        capability_relations=relations,
    )


VERIFIED = {"cap_query", "cap_option", "cap_submit"}


def test_fixed_plan_can_select_two_of_three_capabilities() -> None:
    spec = _three_cap_spec()
    request = SkillGenerationRequest(
        title="请假办理",
        business_description="用户可以查询待办记录，也可以查询后选择一条记录进行提交；不要使用选项字典。",
        planning_mode=PlanningMode.FIXED,
        example_requests=["帮我查待办并提交一条"],
        success_criteria="选中记录已提交",
    )
    plan = propose_deterministic_plan(spec, request, VERIFIED, "fp-1")
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp-1")
    assert checked.ok, checked.errors
    assert plan.selected_capability_ids == ["cap_query", "cap_submit"]
    unused_ids = {item.capability_id for item in plan.unused_capabilities}
    assert unused_ids == {"cap_option"}
    assert all("选项" in item.reason or "未要求" in item.reason or "未使用" in item.reason for item in plan.unused_capabilities)
    assert len(plan.routes) == 1
    assert plan.routes[0].capability_sequence == ["cap_query", "cap_submit"]


@pytest.mark.asyncio
async def test_dynamic_plan_supports_multiple_valid_routes() -> None:
    spec = _three_cap_spec()
    request = SkillGenerationRequest(
        title="请假办理",
        business_description="用户可以只查询待办，也可以直接提交，也可以先查询再提交；提交字段也可从选项中选择。",
        planning_mode=PlanningMode.DYNAMIC,
        example_requests=["只看待办", "直接提交", "先查再提交"],
    )
    result = await generate_skill_plan(
        spec,
        request,
        verified_capability_ids=VERIFIED,
        source_flow_fingerprint="fp-dyn",
        proposer=lambda *_args, **_kwargs: _async_plan(
            propose_deterministic_plan(spec, request, VERIFIED, "fp-dyn")
        ),
    )
    assert result.status == "planned"
    plan = result.plan
    assert plan is not None
    sequences = [tuple(route.capability_sequence) for route in plan.routes]
    assert ("cap_query",) in sequences
    assert ("cap_query", "cap_submit") in sequences
    assert ("cap_option", "cap_submit") in sequences
    assert ("cap_submit", "cap_query") not in sequences
    assert ("cap_option", "cap_query", "cap_option") not in sequences
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp-dyn")
    assert checked.ok, checked.errors


async def _async_plan(plan: SkillPlan):
    return plan


@pytest.mark.asyncio
async def test_plan_rejects_unknown_capability() -> None:
    spec = _three_cap_spec()
    request = SkillGenerationRequest(
        title="请假",
        business_description="查询待办",
        planning_mode=PlanningMode.FIXED,
    )
    bad = propose_deterministic_plan(spec, request, {"cap_query"}, "fp")
    bad.selected_capability_ids = ["cap_missing"]
    bad.routes[0].capability_sequence = ["cap_missing"]

    async def proposer(*_args, **_kwargs):
        return bad

    result = await generate_skill_plan(
        spec, request, verified_capability_ids={"cap_query"}, source_flow_fingerprint="fp", proposer=proposer,
    )
    assert result.status == "generation_failed"
    assert any("不存在" in item for item in result.errors)


@pytest.mark.asyncio
async def test_plan_rejects_unverified_capability() -> None:
    spec = _three_cap_spec()
    request = SkillGenerationRequest(
        title="请假",
        business_description="查询待办并提交",
        planning_mode=PlanningMode.FIXED,
    )
    bad = propose_deterministic_plan(spec, request, VERIFIED, "fp")
    bad.selected_capability_ids = ["cap_query", "cap_submit"]

    async def proposer(*_args, **_kwargs):
        return bad

    result = await generate_skill_plan(
        spec, request, verified_capability_ids={"cap_query"}, source_flow_fingerprint="fp", proposer=proposer,
    )
    assert result.status == "generation_failed"
    assert any("可导出" in item or "未验证" in item for item in result.errors)


def test_plan_rejects_unconfirmed_relation() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    spec.capability_relations = [
        CapabilityRelation(
            relation_id="rel_guess",
            type="suggested_call_chain",
            from_capability="cap_query",
            from_output="records[].id",
            to_capability="cap_submit",
            to_input="id",
            confirmed=False,
        )
    ]
    plan = SkillPlan(
        source_flow_fingerprint="fp",
        planning_mode=PlanningMode.FIXED,
        selected_capability_ids=["cap_query", "cap_submit"],
        unused_capabilities=[UnusedCapability(capability_id="cap_option", reason="未要求")],
        routes=[
            SkillRoute(
                route_id="main",
                name="主路线",
                when_to_use="查询后提交",
                capability_sequence=["cap_query", "cap_submit"],
                required_user_inputs=["status"],
                bindings=[
                    RouteBinding(
                        from_capability="cap_query",
                        from_output="records[].id",
                        to_capability="cap_submit",
                        to_input="id",
                    )
                ],
                requires_confirmation=True,
                done_when="已提交",
                examples=[
                    RouteExample(
                        user_request="查完提交",
                        capability_sequence=["cap_query", "cap_submit"],
                        done_when="已提交",
                    )
                ],
            )
        ],
    )
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp")
    assert not checked.ok
    assert any("未确认关系" in item for item in checked.errors)


def test_plan_rejects_incompatible_binding_cardinality() -> None:
    spec = _three_cap_spec()
    spec.capabilities[2].input_schema["properties"]["payload"] = {"type": "object"}
    spec.capability_relations[0].from_output = "records"
    spec.capability_relations[0].to_input = "payload"
    spec.capability_relations[0].source_selector = "records"
    spec.capability_relations[0].target_path = "payload"
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="查询待办后提交",
            planning_mode=PlanningMode.FIXED,
        ),
        VERIFIED,
        "fp",
    )
    plan.routes[0].bindings = [
        RouteBinding(
            from_capability="cap_query",
            from_output="records",
            to_capability="cap_submit",
            to_input="payload",
            source_selector="records",
            target_path="payload",
            transform_owner="caller",
        )
    ]
    plan.routes[0].required_user_inputs = list(dict.fromkeys(
        [*plan.routes[0].required_user_inputs, "id", "status"]
    ))
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp")
    assert not checked.ok
    assert any("基数不兼容" in item for item in checked.errors)


def test_plan_rejects_incompatible_binding_types() -> None:
    spec = _three_cap_spec()
    spec.capability_relations[0].from_output = "records"
    spec.capability_relations[0].source_selector = "records"
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="查询待办后提交",
            planning_mode=PlanningMode.FIXED,
        ),
        VERIFIED,
        "fp",
    )
    plan.routes[0].bindings = [
        RouteBinding(
            from_capability="cap_query",
            from_output="records",
            to_capability="cap_submit",
            to_input="id",
        )
    ]
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp")
    assert not checked.ok
    assert any("类型不兼容" in item for item in checked.errors)


def test_missing_required_input_becomes_user_input() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    request = SkillGenerationRequest(
        title="请假",
        business_description="用户已经提供完整提交字段，直接提交请假。",
        planning_mode=PlanningMode.FIXED,
        example_requests=["帮我提交请假"],
    )
    plan = propose_deterministic_plan(spec, request, {"cap_submit"}, "fp")
    write_routes = [route for route in plan.routes if "cap_submit" in route.capability_sequence]
    assert write_routes
    route = write_routes[0]
    assert "id" in route.required_user_inputs
    assert "status" in route.required_user_inputs
    assert not any(binding.source == "capability_output" for binding in route.bindings)
    checked = validate_skill_plan(plan, spec, verified_capability_ids={"cap_submit"}, expected_fingerprint="fp")
    assert checked.ok, checked.errors


def test_write_route_requires_confirmation() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="查询待办后提交",
            planning_mode=PlanningMode.FIXED,
        ),
        VERIFIED,
        "fp",
    )
    assert plan.routes[0].requires_confirmation is True
    plan.routes[0].requires_confirmation = False
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp")
    assert any("确认" in item for item in checked.errors)


def test_composition_notes_describe_confirmed_and_handoff_routes() -> None:
    confirmed = propose_deterministic_plan(
        _three_cap_spec(),
        SkillGenerationRequest(
            title="请假办理",
            business_description="用户可以只查询待办，也可以查询后选择一条记录进行提交。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-compose",
    )
    assert any(item.startswith("组合约定：") for item in confirmed.composition_notes)
    assert any("组合路线" in item and "已确认绑定" in item for item in confirmed.composition_notes)
    assert any(len(route.capability_sequence) > 1 and route.bindings for route in confirmed.routes)
    for route in confirmed.routes:
        assert "本页面的实际操作流程" not in route.when_to_use
        assert "本页面的实际操作流程" not in route.examples[0].user_request

    handoff = propose_deterministic_plan(
        _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False),
        SkillGenerationRequest(
            title="请假办理",
            business_description="用户可以查询待办记录，也可以查询后选择一条记录进行提交。只要求查询时不要提交。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-handoff",
    )
    combo = next((route for route in handoff.routes if len(route.capability_sequence) > 1), None)
    assert combo is not None
    assert combo.bindings == []
    assert combo.checkpoints or any(step.checkpoint for step in combo.steps)
    assert any("没有已确认绑定" in item or "人工" in item or "先查再问" in item for item in handoff.composition_notes)
    assert any("只读" in item and "不得执行写入" in item for item in handoff.composition_notes)


def test_model_wording_cannot_replace_recorded_plan_structure() -> None:
    spec = _three_cap_spec()
    request = SkillGenerationRequest(
        title="请假办理",
        business_description="用户可以只查询待办，也可以查询后选择一条记录进行提交。",
        planning_mode=PlanningMode.DYNAMIC,
    )
    base = propose_deterministic_plan(spec, request, VERIFIED, "fp-merge")
    skinny = propose_deterministic_plan(spec, request, {"cap_query"}, "fp-merge")
    skinny.routes[0].when_to_use = "只看待办，不要提交"
    skinny.routes[0].examples[0].user_request = "帮我看一下待办"
    merged = _merge_proposed_plan(base, skinny)
    assert merged.selected_capability_ids == base.selected_capability_ids
    assert {tuple(route.capability_sequence) for route in merged.routes} == {
        tuple(route.capability_sequence) for route in base.routes
    }
    query = next(route for route in merged.routes if route.capability_sequence == ["cap_query"])
    assert query.when_to_use == "只看待办，不要提交"
    assert query.examples[0].user_request == "帮我看一下待办"
    skinny.summary = "模型另写的摘要，丢掉用户约定"
    skinny.composition_summary = "模型另写的组合摘要"
    skinny.composition_notes = ["模型另写的说明"]
    merged = _merge_proposed_plan(base, skinny)
    assert merged.summary == base.summary
    assert merged.composition_summary == base.composition_summary
    assert merged.composition_notes == base.composition_notes


def test_every_route_has_example_and_done_when() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="用户可以查询、从选项选择并提交。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp",
    )
    assert plan.routes
    for route in plan.routes:
        assert route.done_when
        assert route.when_to_use
        assert route.examples
        assert route.examples[0].user_request
        assert route.examples[0].done_when
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp")
    assert checked.ok, checked.errors


@pytest.mark.asyncio
async def test_incomplete_relation_plans_independent_routes() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    request = SkillGenerationRequest(
        title="请假",
        business_description="用户可以查询待办记录，也可以查询后选择一条记录进行提交。",
        planning_mode=PlanningMode.DYNAMIC,
    )
    result = await generate_skill_plan(
        spec,
        request,
        verified_capability_ids=VERIFIED,
        source_flow_fingerprint="fp",
        proposer=lambda *_args, **_kwargs: _async_plan(
            propose_deterministic_plan(spec, request, VERIFIED, "fp")
        ),
    )
    assert result.status == "planned"
    assert result.plan is not None
    sequences = {tuple(route.capability_sequence) for route in result.plan.routes}
    assert ("cap_query",) in sequences
    combo = next(route for route in result.plan.routes if len(route.capability_sequence) > 1)
    assert combo.bindings == []
    assert combo.checkpoints or any(step.checkpoint for step in combo.steps)
    write = next(route for route in result.plan.routes if route.capability_sequence[0] == "cap_submit")
    assert write.bindings == []
    assert "id" in write.required_user_inputs


@pytest.mark.asyncio
async def test_empty_proposed_plan_falls_back_to_deterministic() -> None:
    spec = _three_cap_spec()
    request = SkillGenerationRequest(
        title="请假",
        business_description="用户可以查询待办记录，也可以直接提交。",
        planning_mode=PlanningMode.DYNAMIC,
    )

    async def proposer(*_args, **_kwargs):
        return {
            "source_flow_fingerprint": "fp",
            "planning_mode": "dynamic",
            "selected_capability_ids": [],
            "routes": [
                {"route_id": f"route_{index}", "name": f"路线{index}", "when_to_use": "x", "done_when": "y"}
                for index in range(1, 6)
            ],
        }

    result = await generate_skill_plan(
        spec,
        request,
        verified_capability_ids=VERIFIED,
        source_flow_fingerprint="fp",
        proposer=proposer,
    )
    assert result.status == "planned"
    assert result.plan is not None
    assert result.plan.selected_capability_ids
    assert all(route.capability_sequence and route.examples for route in result.plan.routes)
    checked = validate_skill_plan(result.plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp")
    assert checked.ok, checked.errors


def test_dynamic_plan_keeps_all_mentioned_capabilities() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    spec.capabilities.append(
        _cap(
            capability_id="cap_delete",
            name="delete_leave",
            title="删除请假",
            kind="delete",
            required=["id"],
            confirm=True,
        )
    )
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="可以查询待办、提交请假，也可以删除请假。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        {"cap_query", "cap_option", "cap_submit", "cap_delete"},
        "fp",
    )
    checked = validate_skill_plan(
        plan,
        spec,
        verified_capability_ids={"cap_query", "cap_option", "cap_submit", "cap_delete"},
        expected_fingerprint="fp",
    )
    assert checked.ok, checked.errors
    used = {cap_id for route in plan.routes for cap_id in route.capability_sequence}
    assert set(plan.selected_capability_ids) == {"cap_query", "cap_option", "cap_submit", "cap_delete"}
    assert "cap_query" in used
    assert "cap_option" in used
    assert not any(route.route_id.startswith("solo_") for route in plan.routes)


def test_fixed_plan_without_relation_uses_user_inputs() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="先查询待办记录，再提交一条记录。",
            planning_mode=PlanningMode.FIXED,
        ),
        VERIFIED,
        "fp",
    )
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp")
    assert checked.ok, checked.errors
    assert plan.routes[0].capability_sequence == ["cap_query", "cap_submit"]
    assert plan.routes[0].bindings == []
    assert plan.routes[0].checkpoints or any(step.checkpoint for step in plan.routes[0].steps)
    assert "id" in plan.routes[0].required_user_inputs


def test_fixed_lookup_uses_independent_step_ids() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="先查询待办记录，用户选择一条记录后提交，最后查询状态确认提交成功。",
            planning_mode=PlanningMode.FIXED,
            success_criteria="提交后状态已回查确认",
        ),
        VERIFIED,
        "fp",
    )
    assert plan.routes[0].capability_sequence == ["cap_query", "cap_submit", "cap_query"]
    assert plan.routes[0].step_ids == ["query_before", "submit_selected", "query_after"]
    assert all(binding.from_step and binding.to_step for binding in plan.routes[0].bindings)
    assert plan.routes[0].bindings[0].from_step == "query_before"
    assert plan.routes[0].bindings[0].to_step == "submit_selected"
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp")
    assert checked.ok, checked.errors


@pytest.mark.asyncio
async def test_dynamic_lookup_appends_to_write_routes_not_standalone_c3_c1() -> None:
    spec = _three_cap_spec()
    request = SkillGenerationRequest(
        title="请假办理",
        business_description="用户可以只查询待办，也可以直接提交，也可以先查询再提交；提交后执行已有回查确认成功。",
        planning_mode=PlanningMode.DYNAMIC,
    )
    result = await generate_skill_plan(
        spec,
        request,
        verified_capability_ids=VERIFIED,
        source_flow_fingerprint="fp-lookup",
        proposer=lambda *_args, **_kwargs: _async_plan(
            propose_deterministic_plan(spec, request, VERIFIED, "fp-lookup")
        ),
    )
    assert result.status == "planned"
    plan = result.plan
    assert plan is not None
    route_ids = {route.route_id for route in plan.routes}
    assert "write_then_query" not in route_ids
    assert len(route_ids) == len(plan.routes)
    sequences = [tuple(route.capability_sequence) for route in plan.routes]
    assert ("cap_query",) in sequences
    write_direct = next(route for route in plan.routes if route.capability_sequence == ["cap_submit", "cap_query"])
    assert write_direct.capability_sequence == ["cap_submit", "cap_query"]
    query_then_write = next(
        route for route in plan.routes if route.capability_sequence == ["cap_query", "cap_submit", "cap_query"]
    )
    assert query_then_write.step_ids == ["query_before", "submit_selected", "query_after"]
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp-lookup")
    assert checked.ok, checked.errors


def test_dynamic_plan_keeps_all_packed_operations_with_confirmed_query_write() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假办理",
            business_description="可以只查询，也可以查询后再提交。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-all",
    )
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp-all")
    assert checked.ok, checked.errors
    assert set(plan.selected_capability_ids) == {"cap_query", "cap_option", "cap_submit"}
    combo = next(
        route
        for route in plan.routes
        if route.capability_sequence[:2] == ["cap_query", "cap_submit"] and route.bindings
    )
    assert combo.bindings
    assert any(route.capability_sequence == ["cap_option"] for route in plan.routes)
    assert not any(route.route_id.startswith("solo_") for route in plan.routes)
    assert len({route.route_id for route in plan.routes}) == len(plan.routes)
    singles = [route for route in plan.routes if len(route.capability_sequence) == 1]
    assert {route.capability_sequence[0] for route in singles} >= {"cap_query", "cap_option", "cap_submit"}


def test_stock_playbook_is_not_treated_as_custom_composition() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假办理",
            business_description="先查找再办理。只要查看时不要写入。没有已确认绑定就先查再问人。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-stock",
    )
    assert "本页原子能力" not in plan.composition_summary
    assert "按用户意图选择一项" not in plan.composition_summary
    assert "阶段" not in plan.composition_summary
    assert "原子能力" not in plan.composition_summary
    assert plan.composition_summary.startswith("本页办理")
    assert any("先查再问" in item for item in plan.composition_notes)
    assert any("只读" in item and "不得执行写入" in item for item in plan.composition_notes)


@pytest.mark.asyncio
async def test_empty_business_description_fails() -> None:
    spec = _three_cap_spec()
    result = await generate_skill_plan(
        spec,
        SkillGenerationRequest(title="请假", business_description="   ", planning_mode=PlanningMode.DYNAMIC),
        verified_capability_ids=VERIFIED,
        source_flow_fingerprint="fp",
    )
    assert result.status == "generation_failed"
    assert any("业务描述" in item for item in result.errors)


def test_sale_order_baseline_is_description_without_combo() -> None:
    """Sales-order description now compiles to handoff combos, not notes-only."""
    from stage8_sale_order_fixture import (
        SALE_ORDER_EXPLICIT_COMBOS,
        combination_routes,
        combo_pair,
        route_has_human_checkpoint,
        sale_order_request,
        sale_order_spec,
        sale_order_verified_ids,
    )

    spec = sale_order_spec()
    request = sale_order_request()
    plan = propose_deterministic_plan(spec, request, sale_order_verified_ids(spec), "fp-sale-order")
    assert len(spec.capabilities) == 7
    assert spec.capability_relations == []
    combos = combination_routes(plan)
    assert plan.clarification_questions == []
    assert {combo_pair(route) for route in combos} >= set(SALE_ORDER_EXPLICIT_COMBOS)
    assert ("cap_search", "cap_create") not in {combo_pair(route) for route in combos}
    assert ("cap_detail", "cap_create") not in {combo_pair(route) for route in combos}
    assert len({route.route_id for route in plan.routes}) == len(plan.routes)
    assert all(not route.bindings for route in combos)
    assert all(route_has_human_checkpoint(route) for route in combos if not route.bindings)
    assert {cap.capability_id for cap in spec.capabilities} <= {
        cap_id for route in plan.routes for cap_id in route.capability_sequence
    }
    for route in plan.routes:
        if list(route.capability_sequence) in (["cap_search"], ["cap_detail"]):
            assert "写入已确认" not in route.done_when
            assert "写操作已确认" not in route.done_when


def test_consecutive_connector_compiles_query_then_submit() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="查询完成紧接着提交请假",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-consecutive",
    )
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp-consecutive")
    assert not plan.clarification_questions
    assert any(route.capability_sequence[:2] == ["cap_query", "cap_submit"] for route in plan.routes)
    assert checked.ok, checked.errors + checked.clarifications


def test_unparsed_sequence_must_clarify_instead_of_atomizing() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="查询和提交要连着办",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-clarify-seq",
    )
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp-clarify-seq")
    assert plan.clarification_questions or checked.clarifications
    assert not (
        any(len(route.capability_sequence) > 1 for route in plan.routes)
        and not (plan.clarification_questions or checked.clarifications)
    )


def test_explicit_multistep_description_requires_combo_or_clarification() -> None:
    """业务描述有明确多步分支时，合同必须有组合路线或澄清项。"""
    from stage8_sale_order_fixture import (
        combination_routes,
        sale_order_request,
        sale_order_spec,
        sale_order_verified_ids,
    )

    from stage8_sale_order_fixture import SALE_ORDER_EXPLICIT_COMBOS, combo_pair

    spec = sale_order_spec()
    request = sale_order_request()
    plan = propose_deterministic_plan(spec, request, sale_order_verified_ids(spec), "fp-sale-order")
    combos = combination_routes(plan)
    assert {combo_pair(route) for route in combos} >= set(SALE_ORDER_EXPLICIT_COMBOS)
    assert not plan.clarification_questions


def test_unbound_dependent_route_requires_human_checkpoint() -> None:
    """没有确认绑定的依赖路线必须有人工交接点。"""
    from stage8_sale_order_fixture import (
        combination_routes,
        route_has_human_checkpoint,
        sale_order_request,
        sale_order_spec,
        sale_order_verified_ids,
    )

    spec = sale_order_spec()
    request = sale_order_request()
    plan = propose_deterministic_plan(spec, request, sale_order_verified_ids(spec), "fp-sale-order")
    dependents = [route for route in combination_routes(plan) if not route.bindings]
    assert dependents, "没有确认绑定的依赖序列必须编译成组合路线，不能只剩原子列表"
    assert all(route_has_human_checkpoint(route) for route in dependents)
    assert all(not route.bindings for route in dependents)


def test_single_query_intent_stays_atomic() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(title="请假", business_description="只查询待办记录，不要提交。", planning_mode=PlanningMode.DYNAMIC),
        VERIFIED,
        "fp-single-query",
    )
    combos = [route for route in plan.routes if len(route.capability_sequence) > 1]
    query = next(route for route in plan.routes if route.capability_sequence == ["cap_query"])
    assert "cap_submit" not in query.capability_sequence
    assert query.composition_mode == query.composition_mode.__class__("atomic") or str(query.composition_mode) == "atomic"
    assert not any(route.capability_sequence == ["cap_query", "cap_submit"] and "只查询" in route.when_to_use for route in combos)


def test_single_write_with_target_given_confirms() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="用户已经提供完整提交字段，直接提交请假。",
            planning_mode=PlanningMode.DYNAMIC,
            example_requests=["帮我提交请假，目标已给出"],
        ),
        {"cap_submit"},
        "fp-write-given",
    )
    write = next(route for route in plan.routes if "cap_submit" in route.capability_sequence)
    assert write.requires_confirmation
    assert write.done_when
    assert all(step.confirm_before_execute for step in write.steps if step.capability_id == "cap_submit")


def test_query_then_edit_without_relation_uses_handoff() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(title="请假", business_description="先查询待办记录再提交请假。", planning_mode=PlanningMode.DYNAMIC),
        VERIFIED,
        "fp-handoff",
    )
    combo = next(route for route in plan.routes if route.capability_sequence[:2] == ["cap_query", "cap_submit"])
    assert combo.bindings == []
    assert combo.checkpoints
    assert combo.composition_mode == combo.composition_mode.__class__("handoff") or str(combo.composition_mode) == "handoff"


def test_query_then_edit_with_relation_uses_exact_binding() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(title="请假", business_description="先查询待办记录再提交请假。", planning_mode=PlanningMode.DYNAMIC),
        VERIFIED,
        "fp-bound",
    )
    combo = next(route for route in plan.routes if route.capability_sequence[:2] == ["cap_query", "cap_submit"] and route.bindings)
    assert combo.bindings[0].from_output == "records[].id"
    assert combo.bindings[0].to_input == "id"
    assert str(combo.composition_mode) == "bound"


def test_independent_multi_step_keeps_user_order() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="先查询请假选项，再查询待办记录。两步输入各自独立。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-indep",
    )
    combo = next((route for route in plan.routes if len(route.capability_sequence) > 1), None)
    assert combo is not None
    assert combo.capability_sequence == ["cap_option", "cap_query"]
    assert combo.bindings == []
    assert all(source.source != "confirmed_binding" for step in combo.steps for source in step.input_sources)


def test_unknown_action_returns_clarification() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(title="请假", business_description="请把待办记录导出成报表。", planning_mode=PlanningMode.DYNAMIC),
        VERIFIED,
        "fp-unknown",
    )
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp-unknown")
    assert plan.clarification_questions or checked.clarifications
    assert not any("导出" in route.name and len(route.capability_sequence) > 1 for route in plan.routes)


def test_conflicting_order_returns_clarification() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="先查询待办再提交。另外必须先提交再查询待办。",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-conflict",
    )
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp-conflict")
    assert plan.clarification_questions or checked.clarifications


def test_readonly_branch_does_not_require_write_confirm() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(title="请假", business_description="只查询待办记录，不要写入。", planning_mode=PlanningMode.DYNAMIC),
        VERIFIED,
        "fp-ro",
    )
    query = next(route for route in plan.routes if route.capability_sequence == ["cap_query"])
    assert query.requires_confirmation is False
    assert all(not step.confirm_before_execute for step in query.steps)


def test_forbidden_action_is_not_an_executable_route() -> None:
    spec = _three_cap_spec()
    spec.capabilities.append(
        _cap(capability_id="cap_delete", name="delete_leave", title="删除请假", kind="delete", required=["id"], confirm=True)
    )
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="可以查询待办并提交请假。",
            forbidden_actions="删除请假",
            planning_mode=PlanningMode.DYNAMIC,
        ),
        {"cap_query", "cap_option", "cap_submit", "cap_delete"},
        "fp-forbid",
    )
    used = {cap_id for route in plan.routes for cap_id in route.capability_sequence}
    assert "cap_delete" not in used
    assert any(item.capability_id == "cap_delete" for item in plan.unused_capabilities)


def test_same_contract_merges_trigger_examples() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(
            title="请假",
            business_description="先查询待办再提交请假。",
            example_requests=["先查再提交", "查完帮我提交一条"],
            planning_mode=PlanningMode.DYNAMIC,
        ),
        VERIFIED,
        "fp-merge-ex",
    )
    combos = [route for route in plan.routes if route.capability_sequence[:2] == ["cap_query", "cap_submit"]]
    assert len(combos) == 1
    assert len(combos[0].examples) >= 1


def test_one_described_combo_does_not_explode() -> None:
    spec = _three_cap_spec()
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(title="请假", business_description="先查询待办再提交请假。", planning_mode=PlanningMode.DYNAMIC),
        VERIFIED,
        "fp-no-perm",
    )
    combos = [tuple(route.capability_sequence) for route in plan.routes if len(route.capability_sequence) > 1]
    assert ("cap_query", "cap_submit") in {item[:2] for item in combos}
    assert ("cap_option", "cap_query", "cap_submit") not in combos
    assert ("cap_submit", "cap_query", "cap_option") not in combos


def test_sale_order_contract_quality_gates() -> None:
    from stage8_sale_order_fixture import (
        combination_routes,
        route_has_human_checkpoint,
        sale_order_request,
        sale_order_spec,
        sale_order_verified_ids,
    )

    spec = sale_order_spec()
    request = sale_order_request()
    plan = propose_deterministic_plan(spec, request, sale_order_verified_ids(spec), "fp-sale-order")
    checked = validate_skill_plan(plan, spec, verified_capability_ids=sale_order_verified_ids(spec), expected_fingerprint="fp-sale-order")
    assert checked.ok, checked.errors + checked.clarifications
    silent_loss = [
        branch
        for branch in plan.intent_branches
        if branch.capability_sequence
        and not branch.unresolved
        and not any(list(route.capability_sequence)[:len(branch.capability_sequence)] == list(branch.capability_sequence) for route in plan.routes)
    ]
    assert silent_loss == []
    illegal_binds = [binding for route in plan.routes for binding in route.bindings]
    assert illegal_binds == []
    assert all(route_has_human_checkpoint(route) for route in combination_routes(plan) if not route.bindings)
    assert all(route.requires_confirmation for route in plan.routes if any(cap_id.startswith("cap_") and cap_id not in {"cap_search", "cap_detail"} for cap_id in route.capability_sequence if cap_id in {"cap_update", "cap_approve", "cap_unapprove", "cap_create", "cap_delete"}))
    assert all(route.done_when and all(step.done_when for step in route.steps) for route in plan.routes)


def test_unmapped_parallel_action_needs_clarification() -> None:
    from stage8_sale_order_fixture import sale_order_request, sale_order_spec, sale_order_verified_ids

    spec = sale_order_spec()
    request = sale_order_request()
    request.business_description = "先查询或查看，再对选中的订单做编辑、审核、反审核或注销。"
    request.example_requests = []
    plan = propose_deterministic_plan(spec, request, sale_order_verified_ids(spec), "fp-partial")
    assert plan.clarification_questions
    checked = validate_skill_plan(
        plan,
        spec,
        verified_capability_ids=sale_order_verified_ids(spec),
        expected_fingerprint="fp-partial",
    )
    assert checked.clarifications
    assert not checked.ok or checked.clarifications


EXPLICIT_ORDER_PHRASES = (
    "查询结束就提交请假",
    "查询继而提交请假",
    "查询优先，提交其次",
    "查询在前，提交在后",
    "完成查询方可提交",
)


def test_explicit_order_phrases_compile_to_query_then_submit() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    for phrase in EXPLICIT_ORDER_PHRASES:
        plan = propose_deterministic_plan(
            spec,
            SkillGenerationRequest(title="请假", business_description=phrase, planning_mode=PlanningMode.DYNAMIC),
            VERIFIED,
            f"fp-order-{phrase}",
        )
        checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint=f"fp-order-{phrase}")
        combos = [tuple(route.capability_sequence[:2]) for route in plan.routes if len(route.capability_sequence) > 1]
        assert ("cap_query", "cap_submit") in combos, phrase
        assert not any(
            branch.branch_id == "desc_unresolved_sequence" and not branch.capability_sequence
            for branch in plan.intent_branches
        ), phrase
        assert checked.ok, f"{phrase}: {checked.errors + checked.clarifications}"


def test_immediate_single_write_stays_atomic() -> None:
    spec = _three_cap_spec(confirmed_query_submit=False, confirmed_option_submit=False)
    plan = propose_deterministic_plan(
        spec,
        SkillGenerationRequest(title="请假", business_description="马上提交请假", planning_mode=PlanningMode.DYNAMIC),
        VERIFIED,
        "fp-immediate",
    )
    checked = validate_skill_plan(plan, spec, verified_capability_ids=VERIFIED, expected_fingerprint="fp-immediate")
    assert checked.ok, checked.errors + checked.clarifications
    assert not plan.clarification_questions
    assert not any(branch.unresolved for branch in plan.intent_branches)
    submit = next(route for route in plan.routes if route.capability_sequence == ["cap_submit"])
    assert submit.composition_mode == submit.composition_mode.__class__("atomic") or str(submit.composition_mode) == "atomic"
    assert not any(
        route.capability_sequence[:2] == ["cap_query", "cap_submit"] and "马上提交" in route.when_to_use
        for route in plan.routes
    )
