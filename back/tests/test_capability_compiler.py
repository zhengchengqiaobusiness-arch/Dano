from __future__ import annotations

import asyncio

from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.flow_spec import (
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestFacts,
    SelectBinding,
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
