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
    assert "query_then_write" not in {route.route_id for route in handoff.routes}
    assert any("没有已确认绑定" in item for item in handoff.composition_notes)
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
    query = next(route for route in merged.routes if route.route_id == "query_only")
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
    route_ids = {route.route_id for route in result.plan.routes}
    assert route_ids >= {"query_only", "write_direct"}
    assert "query_then_write" not in route_ids
    write = next(route for route in result.plan.routes if route.route_id == "write_direct")
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
    sequences = [tuple(route.capability_sequence) for route in plan.routes]
    assert ("cap_query",) in sequences
    write_direct = next(route for route in plan.routes if route.route_id == "write_direct")
    assert write_direct.capability_sequence == ["cap_submit", "cap_query"]
    query_then_write = next(route for route in plan.routes if route.route_id == "query_then_write")
    assert query_then_write.capability_sequence == ["cap_query", "cap_submit", "cap_query"]
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
    combo = next(route for route in plan.routes if route.route_id == "query_then_write")
    assert combo.bindings
    assert any(route.route_id.startswith("op_") for route in plan.routes)
    assert not any(route.route_id.startswith("solo_") for route in plan.routes)
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
    """Freeze the current sales-order gap: notes exist, executable combos do not."""
    from stage8_sale_order_fixture import (
        combination_routes,
        sale_order_request,
        sale_order_spec,
        sale_order_verified_ids,
    )

    spec = sale_order_spec()
    request = sale_order_request()
    plan = propose_deterministic_plan(spec, request, sale_order_verified_ids(spec), "fp-sale-order")
    assert len(spec.capabilities) == 7
    assert spec.capability_relations == []
    assert all(len(route.capability_sequence) == 1 for route in plan.routes)
    assert combination_routes(plan) == []
    assert plan.clarification_questions == []
    assert any("没有已确认绑定" in item or "先查再问" in item for item in plan.composition_notes)
    assert all(not route.bindings for route in plan.routes)


def test_explicit_multistep_description_requires_combo_or_clarification() -> None:
    """业务描述有明确多步分支时，合同必须有组合路线或澄清项。"""
    from stage8_sale_order_fixture import (
        combination_routes,
        sale_order_request,
        sale_order_spec,
        sale_order_verified_ids,
    )

    spec = sale_order_spec()
    request = sale_order_request()
    plan = propose_deterministic_plan(spec, request, sale_order_verified_ids(spec), "fp-sale-order")
    assert combination_routes(plan) or plan.clarification_questions, (
        "明确写出的查询后再编辑/审核/反审核/删除不能静默降级成原子列表"
    )


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
