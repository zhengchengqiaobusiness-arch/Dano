from __future__ import annotations

import asyncio

from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestFacts,
    SelectBinding,
    flow_spec_to_api_request,
    orchestrate_flow_capabilities,
)
from dano.execution.page import flow_spec as flow_spec_module


def _verified_graph() -> FlowSpec:
    steps = [
        FlowStep(
            step_id="query",
            method="GET",
            path="/leave/page",
            params=[
                ParamField(
                    path="query.pageNo", key="pageNo", value=1,
                    category="user_param", source_kind="user_input",
                    exposed_to_user=True, required=False,
                ),
                ParamField(
                    path="query.type", key="type", value=1,
                    category="user_param", source_kind="api_option",
                    exposed_to_user=True, required=False,
                ),
            ],
            selects=[SelectBinding(
                path="query.type",
                source_request_id="req-leave-types",
                value_key="value",
                label_key="label",
            )],
            source_meta={"request_id": "req-query", "role": "business_get"},
            response_json={"data": {"list": [], "total": 0}},
        ),
        FlowStep(
            step_id="leave-types",
            method="GET",
            path="/dict/leave-types",
            source_meta={"request_id": "req-leave-types", "role": "read_option"},
            response_json={"data": [{"value": 1, "label": "病假"}]},
        ),
        FlowStep(
            step_id="definition",
            method="GET",
            path="/process-definition/get",
            source_meta={"request_id": "req-definition", "role": "read_context"},
            response_json={"data": {"id": "definition-current"}},
        ),
        FlowStep(
            step_id="approval",
            method="GET",
            path="/approval-detail",
            source_meta={"request_id": "req-approval", "role": "read_context"},
            response_json={"data": {"activityNodes": [{"id": "node-current", "name": "领导审批"}]}},
        ),
        FlowStep(
            step_id="users",
            method="GET",
            path="/system/user/page",
            source_meta={"request_id": "req-users", "role": "read_option"},
            response_json={"data": {"list": [{"id": 160, "nickname": "负责人"}]}},
        ),
        FlowStep(
            step_id="submit",
            method="POST",
            path="/leave/create",
            params=[ParamField(
                path="body.approvers", key="approvers", value={"领导审批": 160},
                category="user_param", source_kind="user_input", exposed_to_user=True,
                source={
                    "kind": "dynamic_structure_input",
                    "option_source": {
                        "request_id": "req-users",
                        "value_path": "id",
                        "label_path": "nickname",
                    },
                },
            )],
            selects=[SelectBinding(
                path="body.approvers",
                source_request_id="req-users",
                value_key="id",
                label_key="nickname",
            )],
            source_meta={"request_id": "req-submit", "role": "business_write"},
            fact_check={
                "source_request_id": "req-query",
                "verification_id": "verify-write",
                "verified": True,
            },
        ),
    ]
    links = [
        FlowLink(
            link_id="definition-to-approval",
            source_step_id="definition",
            source_path="data.id",
            target_step_id="approval",
            target_path="query.processDefinitionId",
            confirmed=True,
            meta={"verified": True, "verification_id": "verify-definition"},
        ),
        FlowLink(
            link_id="approval-to-submit",
            kind="response_key_map",
            source_step_id="approval",
            source_path="data.activityNodes",
            target_step_id="submit",
            target_path="body.startUserSelectAssignees",
            confirmed=True,
            meta={"verified": True, "verification_id": "verify-approval"},
        ),
    ]
    requests = [
        RequestFact(request_id="req-query", method="GET", path="/leave/page"),
        RequestFact(request_id="req-leave-types", method="GET", path="/dict/leave-types"),
        RequestFact(request_id="req-definition", method="GET", path="/process-definition/get"),
        RequestFact(request_id="req-approval", method="GET", path="/approval-detail"),
        RequestFact(request_id="req-users", method="GET", path="/system/user/page"),
        RequestFact(request_id="req-submit", method="POST", path="/leave/create"),
    ]
    return FlowSpec(
        steps=steps,
        links=links,
        request_facts=RequestFacts(requests=requests),
        meta={"verification_log": [
            {"verification_id": verification_id, "status": "passed", "kind": kind}
            for verification_id, kind in (
                ("verify-definition", "dependency_execute"),
                ("verify-approval", "dependency_execute"),
                ("verify-write", "write_execute"),
            )
        ]},
    )


def _semantic_plan() -> dict:
    return {
        "business_understanding": {"summary": "查询并提交请假"},
        "capabilities": [
            {
                "name": "query_leave",
                "title": "查询请假",
                "kind": "query",
                "anchor_step_id": "query",
                "request_refs": [
                    {"step_id": "query", "usage": "execute"},
                    {"step_id": "submit", "usage": "preflight"},
                ],
            },
            {
                "name": "submit_leave",
                "title": "提交请假",
                "kind": "submit",
                "anchor_step_id": "submit",
                "request_refs": [
                    {"step_id": "query", "usage": "preflight"},
                    {"step_id": "users", "usage": "execute"},
                    {"step_id": "submit", "usage": "execute"},
                ],
            },
        ],
        "unresolved_items": [],
    }


def test_compiler_ignores_model_membership_and_builds_verified_graph_roles():
    compilation = compile_capabilities(_verified_graph(), _semantic_plan())

    assert compilation.errors == []
    by_name = {cap.name: cap for cap in compilation.spec.capabilities}
    query = by_name["query_leave"]
    submit = by_name["submit_leave"]

    assert query.step_ids == ["query"]
    assert [(ref.step_id, ref.usage) for ref in query.request_refs] == [
        ("query", "execute"),
        ("leave-types", "option_source"),
    ]
    assert submit.step_ids == ["definition", "approval", "submit"]
    assert [(ref.step_id, ref.usage) for ref in submit.request_refs] == [
        ("definition", "preflight"),
        ("approval", "preflight"),
        ("submit", "execute"),
        ("users", "option_source"),
        ("query", "fact_check"),
    ]
    assert {field.key for field in submit.inputs} == {"approvers"}
    assert "pageNo" not in {field.key for field in submit.inputs}


def test_unverified_dependency_is_not_admitted_as_preflight():
    spec = _verified_graph()
    spec.links[0].meta["verification_id"] = "missing-verification"

    compilation = compile_capabilities(spec, _semantic_plan())
    submit = next(cap for cap in compilation.spec.capabilities if cap.name == "submit_leave")

    assert submit.step_ids == ["approval", "submit"]
    assert "definition" not in {ref.step_id for ref in submit.request_refs}


def test_capability_request_builder_ignores_links_outside_compiled_membership():
    spec = FlowSpec(
        steps=[
            FlowStep(step_id="support", method="GET", path="/support"),
            FlowStep(step_id="query", method="GET", path="/items"),
        ],
        links=[FlowLink(
            link_id="pending-link",
            source_step_id="support",
            source_path="data.id",
            target_step_id="query",
            target_path="query.id",
            confirmed=False,
        )],
        capabilities=[FlowCapability(
            name="query_items",
            kind="query_status",
            step_ids=["query"],
            nodes=[
                {"id": "call_1", "type": "call", "step_id": "query"},
                {"id": "return_final", "type": "return", "from": "query", "path": "response"},
            ],
        )],
    )

    request, errors = flow_spec_to_api_request(spec)

    assert errors == []
    assert request is not None
    assert request["path"] == "/items"


def test_query_can_be_public_and_the_same_request_can_verify_a_write():
    compilation = compile_capabilities(_verified_graph(), _semantic_plan())
    by_name = {cap.name: cap for cap in compilation.spec.capabilities}

    assert by_name["query_leave"].request_refs[0].usage == "execute"
    assert next(
        ref for ref in by_name["submit_leave"].request_refs if ref.step_id == "query"
    ).usage == "fact_check"


def test_strict_pi_plan_is_recompiled_before_orchestration_is_persisted():
    result = asyncio.run(orchestrate_flow_capabilities(
        _verified_graph(),
        submission={"semantic_plan": _semantic_plan(), "ops": []},
        generation_mode="initial",
    ))

    assert result.meta["capability_model"]["source"] == "verified_request_graph"
    assert result.meta["capability_model"]["capability_compilation"]["errors"] == []
    submit = next(cap for cap in result.capabilities if cap.name == "submit_leave")
    assert submit.step_ids == ["definition", "approval", "submit"]


def test_orchestration_without_strict_plan_does_not_generate_fallback_abilities():
    result = asyncio.run(orchestrate_flow_capabilities(
        _verified_graph(),
        submission={"ops": []},
        generation_mode="initial",
    ))

    assert result.capabilities == []
    assert result.meta["capability_model"]["source"] == "strict_plan_pending"
    assert result.meta["capability_model"]["proposal_gate"] == {
        "accepted": False,
        "reasons": ["strict_semantic_plan_required"],
        "producer": "verified_request_graph",
    }


def test_legacy_abilities_payload_cannot_create_public_abilities():
    result = asyncio.run(orchestrate_flow_capabilities(
        _verified_graph(),
        submission={
            "abilities": [{
                "name": "legacy_submit",
                "title": "旧提交能力",
                "kind": "submit",
                "step_ids": ["submit"],
            }],
            "ops": [],
        },
        generation_mode="initial",
    ))

    assert result.capabilities == []
    assert result.meta["capability_model"]["source"] == "strict_plan_pending"


def test_strict_compiler_replaces_stale_machine_generated_ability():
    spec = _verified_graph()
    spec.capabilities = [FlowCapability(
        capability_id="stale",
        name="stale_fallback",
        title="旧启发式能力",
        kind="submit",
        step_ids=["submit"],
        nodes=[{"id": "call", "type": "call", "step_id": "submit"}],
        updated_by="planner",
    )]
    spec.meta["capability_model"] = {"source": "deterministic"}

    result = asyncio.run(orchestrate_flow_capabilities(
        spec,
        submission={"semantic_plan": _semantic_plan(), "ops": []},
        generation_mode="initial",
    ))

    assert {cap.name for cap in result.capabilities} == {"query_leave", "submit_leave"}
    assert "stale_fallback" not in {cap.name for cap in result.capabilities}


def test_compiler_uses_recorded_operation_kind_instead_of_model_guess():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/applications/create",
        source_meta={"role": "business_write"},
    )])
    plan = {
        "capabilities": [{
            "name": "create_application",
            "title": "创建申请",
            "kind": "submit_batch",
            "anchor_step_id": "submit",
            "request_refs": [{"step_id": "submit", "usage": "execute"}],
        }],
    }

    result = compile_capabilities(spec, plan)

    assert result.errors == []
    assert result.capabilities[0].kind == "create"
    assert "replaced by grounded kind 'create'" in result.warnings[0]


def test_strict_pi_plan_coverage_uses_the_declared_anchor_contract() -> None:
    coverage = flow_spec_module._semantic_plan_coverage(
        _verified_graph(),
        {
            "semantic_plan": {
                "business_understanding": {"summary": "查询并提交请假"},
                **_semantic_plan(),
                "unresolved_items": [],
            },
        },
    )

    assert coverage["complete"] is True
    assert coverage["missing"] == []
    assert coverage["covered_steps"] == 2


def test_semantic_completion_preserves_only_the_strict_pi_contract() -> None:
    compiled = compile_capabilities(_verified_graph(), _semantic_plan()).spec
    completed = flow_spec_module._complete_semantic_plan_from_spec(
        compiled,
        {
            "business_understanding": {"summary": "查询并提交请假"},
            **_semantic_plan(),
            "unresolved_items": [],
        },
    )

    assert set(completed) == {
        "business_understanding", "capabilities", "unresolved_items",
    }
    assert len(completed["capabilities"]) == 2
    for capability in completed["capabilities"]:
        assert set(capability) == {
            "name", "title", "kind", "anchor_step_id", "request_refs",
        }
        assert capability["anchor_step_id"]
        assert capability["request_refs"]


def test_compiler_separates_recording_business_name_from_ability_call_key() -> None:
    spec = _verified_graph()
    submit = next(step for step in spec.steps if step.step_id == "submit")
    submit.params.append(ParamField(
        path="body.startTime",
        key="开始时间",
        label="开始时间",
        name_source="dom",
        value=1785945600000,
        type="datetime",
        wire_type="number",
        wire_format="epoch_ms",
        category="user_param",
        source_kind="user_input",
        exposed_to_user=True,
        required=True,
    ))

    compiled = compile_capabilities(spec, _semantic_plan()).spec
    compiled_submit = next(step for step in compiled.steps if step.step_id == "submit")
    compiled_param = next(param for param in compiled_submit.params if param.path == "body.startTime")
    capability = next(cap for cap in compiled.capabilities if cap.name == "submit_leave")
    public = next(field for field in capability.inputs if field.path == "body.startTime")

    assert submit.params[-1].key == "开始时间"
    assert compiled_param.key == "startTime"
    assert public.key == "startTime"
    assert public.display_name == "开始时间"


def test_compiler_preserves_legacy_key_as_display_name_when_label_is_empty() -> None:
    spec = _verified_graph()
    submit = next(step for step in spec.steps if step.step_id == "submit")
    submit.params.append(ParamField(
        path="body.reason",
        key="申请原因",
        label="",
        value="出差",
        type="string",
        wire_type="string",
        category="user_param",
        source_kind="user_input",
        exposed_to_user=True,
        required=True,
    ))

    compiled = compile_capabilities(spec, _semantic_plan()).spec
    compiled_submit = next(step for step in compiled.steps if step.step_id == "submit")
    compiled_param = next(param for param in compiled_submit.params if param.path == "body.reason")
    capability = next(cap for cap in compiled.capabilities if cap.name == "submit_leave")
    public = next(field for field in capability.inputs if field.path == "body.reason")

    assert compiled_param.key == "reason"
    assert compiled_param.label == "申请原因"
    assert public.key == "reason"
    assert public.display_name == "申请原因"


def test_successful_strict_compilation_refreshes_stale_generation_state() -> None:
    spec = _verified_graph()
    spec.meta["capability_generation"] = {
        "protocol": "dano.capability-generation.v2",
        "initial_completed": False,
        "status": "incomplete_agent_plan",
    }

    compiled = compile_capabilities(spec, {
        "business_understanding": {"summary": "查询并提交请假"},
        **_semantic_plan(),
        "unresolved_items": [],
    }).spec

    assert compiled.meta["capability_generation"]["initial_completed"] is True
    assert compiled.meta["capability_generation"]["status"] == "ready"


def test_semantic_coverage_ignores_internal_requests_outside_ability_scope() -> None:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="query",
                method="GET",
                path="/orders/page",
                source_meta={
                    "role": "business_get",
                    "trigger_op": "click",
                    "trigger_locator": "button:has-text('查询')",
                },
                response_json={"data": {"list": [], "total": 0}},
            ),
            FlowStep(
                step_id="refresh-token",
                method="POST",
                path="/auth/refresh-token",
                source_meta={"role": "auth"},
                params=[ParamField(
                    path="body.refreshToken",
                    key="refreshToken",
                    value="captured-secret",
                    type="unknown",
                    category="unknown",
                    source_kind="unknown",
                )],
            ),
        ],
    )
    semantic_plan = {
        "business_understanding": {"summary": "查询订单"},
        "capabilities": [{
            "name": "query_orders",
            "title": "查询订单",
            "kind": "query_status",
            "anchor_step_id": "query",
            "request_refs": [{"step_id": "query", "usage": "execute"}],
        }],
        "unresolved_items": [],
    }

    coverage = flow_spec_module._semantic_plan_coverage(  # noqa: SLF001
        spec, {"semantic_plan": semantic_plan},
    )

    assert coverage["complete"] is True
    assert coverage["total_steps"] == 1
    assert coverage["total_fields"] == 0
