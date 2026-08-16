from __future__ import annotations

import asyncio

from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.recording_live import apply_recording_agent_edit
from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestFacts,
    SelectBinding,
    compile_capability_to_api_request,
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
                    category="user_param", source_kind="page_context",
                    source={
                        "kind": "page_context",
                        "context_key": "pageNo",
                        "default_value": 1,
                        "caller_override": True,
                        "required_state": "optional",
                    },
                    exposed_to_user=True, editable=True, required=False,
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
    page = next(field for field in query.inputs if field.key == "pageNo")
    assert page.source_kind == "page_context"
    assert page.required is False
    assert page.source["default_value"] == 1


def test_compiler_includes_dependencies_grounded_by_the_same_recording():
    spec = _verified_graph()
    spec.meta["verification_log"] = []
    for link in spec.links:
        link.confirmed = False
        link.meta = {
            "verified": False,
            (
                "captured_structure_match"
                if link.kind == "response_key_map"
                else "captured_value_match"
            ): True,
        }

    compilation = compile_capabilities(spec, _semantic_plan())

    assert compilation.errors == []
    submit = next(cap for cap in compilation.spec.capabilities if cap.name == "submit_leave")
    assert submit.step_ids == ["definition", "approval", "submit"]
    assert submit.evidence[0]["source"] == "grounded_request_graph"


def test_live_sources_compile_into_one_executable_multi_api_contract():
    """A live conclusion must change execution, not only the workbench label."""
    spec = FlowSpec.model_validate({
        "steps": [
            {
                "step_id": "save_chat",
                "method": "POST",
                "path": "/chat/save",
                "body_source": '{"user_id":"user-1","name":"hello"}',
                "params": [
                    {"path": "user_id", "key": "user_id", "value": "user-1"},
                    {"path": "name", "key": "name", "value": "hello"},
                ],
                "response_json": {"data": {"conversation_id": "29cba714-c6ce-4747-baec-e2c5d37d6868"}},
                "source_meta": {"request_id": "req-save", "sequence": 1},
            },
            {
                "step_id": "get_appid",
                "method": "GET",
                "path": "/auth/getappid",
                "params": [
                    {"path": "query.appId", "key": "appId", "value": "29cba714-c6ce-4747-baec-e2c5d37d6868"},
                    {"path": "query.appName", "key": "appName", "value": "generated-name"},
                    {"path": "query.timeStamp", "key": "timeStamp", "value": 1782891442000},
                ],
                "response_json": {"data": "app-code-1"},
                "source_meta": {"request_id": "req-appid", "sequence": 2},
            },
            {
                "step_id": "chat",
                "method": "POST",
                "path": "/chat/run",
                "body_source": (
                    '{"sys_query":"hello","wybs":"51e561cb-49e9-4f96-817a-2d0a7e2a4360",'
                    '"token":"token-1","appCode":"app-code-1",'
                    '"conversation_id":"29cba714-c6ce-4747-baec-e2c5d37d6868"}'
                ),
                "params": [
                    {"path": "sys_query", "key": "sys_query", "value": "hello"},
                    {"path": "wybs", "key": "wybs", "value": "51e561cb-49e9-4f96-817a-2d0a7e2a4360"},
                    {"path": "token", "key": "token", "value": "token-1"},
                    {"path": "appCode", "key": "appCode", "value": "app-code-1"},
                    {"path": "conversation_id", "key": "conversation_id", "value": "29cba714-c6ce-4747-baec-e2c5d37d6868"},
                ],
                "response_json": {"ok": True},
                "source_meta": {"request_id": "req-chat", "sequence": 3, "role": "business_write"},
            },
        ],
        "request_facts": {
            "requests": [
                {
                    "request_id": "req-save", "sequence": 1, "method": "POST", "path": "/chat/save",
                    "post_data": '{"user_id":"user-1","name":"hello"}',
                    "response_json": {"data": {"conversation_id": "29cba714-c6ce-4747-baec-e2c5d37d6868"}},
                },
                {
                    "request_id": "req-appid", "sequence": 2, "method": "GET", "path": "/auth/getappid",
                    "response_json": {"data": "app-code-1"},
                },
                {
                    "request_id": "req-chat", "sequence": 3, "method": "POST", "path": "/chat/run",
                    "post_data": (
                        '{"sys_query":"hello","wybs":"51e561cb-49e9-4f96-817a-2d0a7e2a4360",'
                        '"token":"token-1","appCode":"app-code-1",'
                        '"conversation_id":"29cba714-c6ce-4747-baec-e2c5d37d6868"}'
                    ),
                    "response_json": {"ok": True},
                },
            ],
        },
    })

    edits = [
        {"op": "set_param_source", "step_id": "save_chat", "path": "user_id", "source_kind": "session", "session_key": "localStorage:user.user_id", "reason": "登录态用户"},
        {"op": "set_param_source", "step_id": "save_chat", "path": "name", "source_kind": "caller_input", "reason": "调用方输入"},
        {"op": "set_param_source", "step_id": "get_appid", "path": "query.appId", "source_kind": "generated", "strategy": "uuid", "reason": "运行时生成"},
        {"op": "set_param_source", "step_id": "get_appid", "path": "query.appName", "source_kind": "generated", "strategy": "random_string", "reason": "运行时生成"},
        {"op": "set_param_source", "step_id": "get_appid", "path": "query.timeStamp", "source_kind": "generated", "strategy": "now_ms", "reason": "运行时时间"},
        {"op": "set_param_source", "step_id": "chat", "path": "sys_query", "source_kind": "caller_input", "reason": "调用方输入"},
        {"op": "set_param_source", "step_id": "chat", "path": "wybs", "source_kind": "generated", "strategy": "uuid", "reason": "运行时生成"},
        {"op": "set_param_source", "step_id": "chat", "path": "token", "source_kind": "session", "session_key": "localStorage:auth.token", "reason": "登录令牌"},
        {"op": "set_param_source", "step_id": "chat", "path": "appCode", "source_kind": "response_binding", "origin_request_id": "req-appid", "origin_path": "data", "reason": "上游接口返回"},
        {"op": "set_param_source", "step_id": "chat", "path": "conversation_id", "source_kind": "response_binding", "origin_request_id": "req-save", "origin_path": "data.conversation_id", "reason": "上游接口返回"},
    ]
    for edit in edits:
        apply_recording_agent_edit(spec, edit)

    compiled = compile_capabilities(spec, {"capabilities": [{
        "name": "run_chat",
        "title": "运行对话",
        "intent": "创建会话、取得应用码并发起对话",
        "kind": "submit",
        "anchor_step_id": "chat",
    }]}).spec
    api_request, errors = flow_spec_to_api_request(compiled)

    assert errors == []
    assert api_request is not None
    assert api_request["params"] == ["name", "sys_query"]
    assert [step["step_id"] for step in api_request["steps"]] == [
        "save_chat", "get_appid", "chat",
    ]
    save_step, appid_step, chat_step = api_request["steps"]
    assert save_step["identity"] == [{
        "path": "user_id",
        "source": "localStorage:user.user_id",
        "evidence": [
            "request://body.user_id",
            "identity://localStorage:user.user_id",
        ],
        "tokens": ["user_id"],
    }]
    assert {item["kind"] for item in appid_step["runtime_fields"]} == {
        "uuid", "random_string", "now_ms",
    }
    assert {item["kind"] for item in chat_step["system_values"]} == {"uuid"}
    assert chat_step["identity"][0]["source"] == "localStorage:auth.token"
    assert {(item["source_step"], item["source_path"], item["target_path"]) for item in chat_step["links"]} == {
        (0, "data.conversation_id", "conversation_id"),
        (1, "data", "appCode"),
    }


def test_orchestration_keeps_safely_compiled_capabilities_when_one_boundary_is_invalid():
    plan = _semantic_plan()
    plan["capabilities"].append({
        "name": "duplicate_query_boundary",
        "title": "重复查询边界",
        "kind": "query",
        "anchor_step_id": "query",
        "request_refs": [{"step_id": "query", "usage": "execute"}],
    })

    result = asyncio.run(orchestrate_flow_capabilities(
        _verified_graph(),
        submission={"semantic_plan": plan, "ops": []},
        generation_mode="initial",
    ))

    assert [cap.name for cap in result.capabilities] == ["query_leave", "submit_leave"]
    assert result.meta["capability_model"]["status"] == "needs_review"
    assert result.meta["capability_model"]["proposal_gate"]["accepted"] is False
    assert result.meta["capability_model"]["capability_compilation"]["errors"]


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


def test_read_ability_exposes_an_upstream_value_outside_its_own_execution() -> None:
    record_id = "record-123456"
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="query",
                method="GET",
                path="/applications/page",
                source_meta={"request_id": "req-query", "role": "business_get"},
                response_json={"data": {"list": [{"id": record_id, "title": "出差"}]}},
            ),
            FlowStep(
                step_id="detail",
                method="GET",
                path=f"/applications/get?id={record_id}",
                source_meta={"request_id": "req-detail", "role": "business_get"},
                params=[ParamField(
                    path="query.id",
                    key="id",
                    value=record_id,
                    type="string",
                    category="runtime_var",
                    source_kind="previous_response",
                    source={
                        "kind": "previous_response",
                        "step_id": "query",
                        "response_path": "data.list[0].id",
                    },
                    exposed_to_user=False,
                )],
                response_json={"data": {"id": record_id, "title": "出差"}},
            ),
        ],
        links=[FlowLink(
            link_id="query-detail",
            source_step_id="query",
            source_path="data.list[0].id",
            target_step_id="detail",
            target_path="query.id",
            confirmed=True,
            meta={"captured_value_match": True},
        )],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-query", method="GET", path="/applications/page"),
            RequestFact(request_id="req-detail", method="GET", path="/applications/get"),
        ]),
    )
    plan = {"capabilities": [
        {
            "name": "query_applications",
            "title": "查询申请",
            "kind": "query",
            "anchor_step_id": "query",
        },
        {
            "name": "inspect_application",
            "title": "查看申请详情",
            "kind": "inspect",
            "anchor_step_id": "detail",
        },
    ]}

    compiled = compile_capabilities(spec, plan).spec
    prepared = flow_spec_module.prepare_flow_spec_for_publish(compiled)
    assert [
        (
            relation.from_capability, relation.from_output,
            relation.to_capability, relation.to_input,
        )
        for relation in prepared.capability_relations
    ] == [
        ("query_applications", "records[].id", "inspect_application", "id"),
    ]
    detail = next(cap for cap in compiled.capabilities if cap.name == "inspect_application")

    assert detail.step_ids == ["detail"]
    assert [(ref.step_id, ref.usage) for ref in detail.request_refs] == [
        ("detail", "execute"),
    ]
    assert {field.key for field in detail.inputs} == {"id"}

    api_request, errors = compile_capability_to_api_request(
        compiled, capability_name="inspect_application",
    )
    assert errors == []
    assert api_request is not None
    assert api_request["params"] == ["id"]
    contract = api_request["capabilities"][0]
    assert contract["input_schema"]["required"] == ["id"], contract
    assert contract["input_schema"]["properties"]["id"]["x-dano-external-source"] == {
        "step_id": "query",
        "response_path": "data.list[0].id",
    }
    assert [
        (
            relation["from_capability"], relation["from_output"],
            relation["to_capability"], relation["to_input"],
        )
        for relation in api_request["capability_relations"]
    ] == [
        ("query_applications", "records[].id", "inspect_application", "id"),
    ]


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


def test_recording_goal_cannot_relabel_a_grounded_submit_as_update() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/applications/submit-process",
        source_meta={"role": "business_write"},
    )])
    plan = {
        "capabilities": [{
            "name": "update",
            "title": "编辑申请",
            "kind": "update",
            "kind_source": "recording_goal",
            "anchor_step_id": "submit",
        }],
    }

    result = compile_capabilities(spec, plan)

    assert result.errors == []
    assert result.capabilities[0].kind == "submit"
    assert "replaced by grounded kind 'submit'" in result.warnings[0]


def test_existing_entity_form_submit_is_grounded_as_update() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="save-existing",
        method="POST",
        path="/applications/submit-process",
        source_meta={
            "role": "business_write",
            "trigger_op": "click",
            "trigger_locator": "text=提交",
            "trigger_page_context": {
                "url": "https://example.test/applications/editor?id=entity-1",
            },
        },
        params=[
            ParamField(
                path="id", key="id", value="entity-1",
                category="runtime_var", source_kind="unknown",
                source={"kind": "selected_entity_id"}, exposed_to_user=False,
            ),
            ParamField(
                path="reason", key="reason", value="changed",
                category="user_param", source_kind="user_input",
                exposed_to_user=True,
            ),
        ],
    )])
    plan = {"capabilities": [{
        "name": "update_application",
        "title": "编辑申请",
        "kind": "update",
        "kind_source": "recording_goal",
        "anchor_step_id": "save-existing",
    }]}

    result = compile_capabilities(spec, plan)

    assert result.errors == []
    assert result.warnings == []
    assert result.capabilities[0].kind == "update"


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
