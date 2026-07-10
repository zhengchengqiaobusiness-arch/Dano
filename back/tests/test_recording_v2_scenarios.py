"""Recording V2 capability-centric scenario regressions.

These tests exercise cross-model invariants instead of isolated helper output:
request facts remain complete, capability nodes define execution scope, and the
derived field/dependency/schema views stay aligned with that scope.
"""

from __future__ import annotations

import asyncio
import json

import dano.execution.page.flow_spec as flow_spec_module
from dano.execution.page.flow_spec import (
    CapabilityField,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    apply_flow_edits,
    build_default_flow_capabilities,
    flow_spec_to_client,
    orchestrate_flow_capabilities,
    run_recording_pi_loop,
    to_flow_spec,
    validate_flow_spec,
)


def _get(index: int, path: str, response_json: dict) -> dict:
    return {
        "index": index,
        "sequence": index,
        "method": "GET",
        "url": f"https://oa.example.test{path}",
        "content_type": "application/json",
        "headers": {"Authorization": "Bearer test"},
        "response_status": 200,
        "response_json": response_json,
    }


def _post(index: int, path: str, body: dict | list, response_json: dict | None = None) -> dict:
    return {
        "index": index,
        "sequence": index,
        "method": "POST",
        "url": f"https://oa.example.test{path}",
        "content_type": "application/json",
        "headers": {"Authorization": "Bearer test", "Content-Type": "application/json"},
        "post_data": json.dumps(body, ensure_ascii=False),
        "response_status": 200,
        "response_json": response_json or {"code": 0, "data": True},
    }


def _walk_nodes(nodes: list[dict]) -> list[dict]:
    flattened: list[dict] = []
    for node in nodes:
        flattened.append(node)
        for key in ("children", "steps", "then", "else", "otherwise"):
            child = node.get(key)
            if isinstance(child, list):
                flattened.extend(_walk_nodes([item for item in child if isinstance(item, dict)]))
    return flattened


def test_capability_nodes_expand_stale_step_ids_and_derive_all_three_step_views():
    definition = FlowStep(
        step_id="definition",
        method="GET",
        url="/process/definition",
        path="/process/definition",
        params=[ParamField(path="query.key", key="流程类型", value="leave", category="system_const")],
        response_json={"data": {"id": "PROC-001"}},
    )
    detail = FlowStep(
        step_id="detail",
        method="GET",
        url="/process/detail",
        path="/process/detail",
        params=[ParamField(
            path="query.processId",
            key="流程定义ID",
            value="PROC-001",
            category="runtime_var",
            source_kind="previous_response",
        )],
        response_json={"data": {"approverId": "USER-009"}},
    )
    submit = FlowStep(
        step_id="submit",
        method="POST",
        url="/leave/submit",
        path="/leave/submit",
        params=[
            ParamField(
                path="approverId",
                key="审批人",
                value="USER-009",
                category="runtime_var",
                source_kind="previous_response",
            ),
            ParamField(path="reason", key="原因", value="年假", category="user_param", required=True),
        ],
    )
    spec = FlowSpec(
        flow_id="three-call-capability",
        steps=[definition, detail, submit],
        links=[
            FlowLink(
                source_step_id="definition",
                source_path="data.id",
                target_step_id="detail",
                target_path="query.processId",
                confirmed=True,
            ),
            FlowLink(
                source_step_id="detail",
                source_path="data.approverId",
                target_step_id="submit",
                target_path="approverId",
                confirmed=True,
            ),
        ],
        capabilities=[FlowCapability(
            name="submit_leave",
            kind="submit",
            step_ids=["submit"],
            nodes=[
                {"id": "call_definition", "type": "call", "step_id": "definition"},
                {"id": "call_detail", "type": "call", "step_id": "detail"},
                {"id": "call_submit", "type": "call", "step_id": "submit"},
                {"id": "return_submit", "type": "return", "from": "submit", "path": "response"},
            ],
        )],
    )

    flow_spec_module._normalize_capability_references(spec)
    synced = flow_spec_module._sync_capability_io_schemas(spec)
    cap = synced.capabilities[0]

    assert cap.step_ids == ["definition", "detail", "submit"]
    assert {field.step_id for field in cap.request_fields} == {"definition", "detail", "submit"}
    assert {
        (dep.source.get("step_id"), dep.target.get("step_id"))
        for dep in cap.dependencies
    } == {("definition", "detail"), ("detail", "submit")}
    client_cap = flow_spec_to_client(synced)["capabilities"][0]
    assert client_cap["step_ids"] == ["definition", "detail", "submit"]
    assert set(client_cap["input_schema"]["properties"]) == {"原因"}


def test_remove_capability_step_recursively_clears_nested_condition_map_and_loop_calls():
    spec = FlowSpec(
        flow_id="nested-remove",
        steps=[
            FlowStep(step_id="keep", method="GET", url="/keep", path="/keep"),
            FlowStep(step_id="remove", method="POST", url="/remove", path="/remove"),
        ],
        capabilities=[FlowCapability(
            name="nested",
            kind="submit",
            step_ids=["keep", "remove"],
            nodes=[{
                "id": "condition_1",
                "type": "condition",
                "then": [{"id": "call_remove_1", "type": "call", "step_id": "remove"}],
                "else": [{
                    "id": "map_1",
                    "type": "map",
                    "children": [{
                        "id": "loop_1",
                        "type": "loop",
                        "steps": [
                            {"id": "call_keep", "type": "call", "step_id": "keep"},
                            {"id": "call_remove_2", "type": "call", "step_id": "remove"},
                        ],
                    }],
                }],
            }],
        )],
    )

    edited = apply_flow_edits(spec, [{
        "op": "remove_capability_step",
        "capability_name": "nested",
        "step_id": "remove",
    }])
    cap = edited.capabilities[0]
    call_ids = [node.get("step_id") for node in _walk_nodes(cap.nodes) if node.get("type") == "call"]

    assert cap.step_ids == ["keep"]
    assert call_ids == ["keep"]
    assert any(step.step_id == "remove" for step in edited.steps)


def test_stale_capability_fields_are_removed_from_validation_and_input_schema():
    stale = CapabilityField(
        field_id="stale-field",
        scope="input",
        display_name="已删除字段",
        key="stale",
        path="missing.path",
        step_id="submit",
        source_kind="user_input",
        exposed_to_caller=True,
        locked=True,
        confirmed=True,
    )
    spec = FlowSpec(
        flow_id="stale-field-prune",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/leave/submit",
            path="/leave/submit",
            params=[ParamField(
                path="reason",
                key="原因",
                value="年假",
                category="user_param",
                source_kind="user_input",
                required=True,
            )],
            success_rule={"kind": "http_status", "values": [200]},
        )],
        capabilities=[FlowCapability(
            name="submit_leave",
            title="提交请假",
            kind="submit",
            step_ids=["submit"],
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            fields=[stale],
            inputs=[stale],
            input_schema={
                "type": "object",
                "properties": {"stale": {"type": "string"}},
                "required": ["stale"],
            },
            confirmed=True,
            requires_human_confirm=False,
            status="confirmed",
        )],
    )

    edited = apply_flow_edits(spec, [{
        "op": "update_capability",
        "capability_name": "submit_leave",
        "field": "title",
        "value": "提交请假",
    }])
    cap = edited.capabilities[0]
    report = validate_flow_spec(edited)

    assert {field.path for field in cap.fields} == {"reason"}
    assert set(cap.input_schema["properties"]) == {"原因"}
    assert cap.input_schema["required"] == ["原因"]
    assert all("missing.path" not in message and "stale" not in message for message in report["errors"])


def test_to_flow_spec_keeps_independent_get_as_fact_and_materializes_dependency_closure():
    captured = [
        _get(1, "/daily-report/page", {"data": {"list": [{"date": "2026-05-01"}]}}),
        _get(2, "/process/definition/get?key=daily", {"data": {"id": "PROC-UNIQUE-001"}}),
        _post(
            3,
            "/daily-report/submit",
            {"processId": "PROC-UNIQUE-001", "content": "完成回归测试"},
        ),
    ]

    spec = to_flow_spec(captured, samples={"content": "完成回归测试"})

    assert [step.method for step in spec.steps] == ["GET", "POST"]
    assert [step.path.split("?", 1)[0] for step in spec.steps] == [
        "/process/definition/get",
        "/daily-report/submit",
    ]
    assert len(spec.request_facts.requests) == 3
    independent = next(fact for fact in spec.request_facts.requests if "/daily-report/page" in fact.path)
    assert independent.request_id not in {
        (step.source_meta or {}).get("request_id") for step in spec.steps
    }
    assert spec.request_facts.usage[independent.request_id].state == "captured"


def test_unique_real_value_dependency_is_confirmed_but_ambiguous_value_is_not():
    unique = to_flow_spec([
        _get(1, "/process/definition/get", {"data": {"taskId": "TASK-UNIQUE-001"}}),
        _post(2, "/leave/submit", {"taskId": "TASK-UNIQUE-001", "reason": "年假"}),
    ], samples={"reason": "年假"})

    assert len(unique.links) == 1
    assert unique.links[0].confirmed is True
    assert unique.links[0].confidence == 0.96

    ambiguous = to_flow_spec([
        _get(1, "/process/definition/get", {"data": {"taskId": "TASK-SHARED-001"}}),
        _get(2, "/process/instance/detail", {"data": {"taskId": "TASK-SHARED-001"}}),
        _post(3, "/leave/submit", {"taskId": "TASK-SHARED-001", "reason": "年假"}),
    ], samples={"reason": "年假"})

    assert len(ambiguous.links) >= 1
    assert all(link.confirmed is False for link in ambiguous.links)
    assert all(link.confidence == 0.85 for link in ambiguous.links)


def test_default_capabilities_use_submit_for_single_post_and_add_list_options_for_enum():
    spec = FlowSpec(
        flow_id="single-submit-with-enum",
        steps=[FlowStep(
            step_id="submit",
            name="POST_submit",
            method="POST",
            url="/leave/submit",
            path="/leave/submit",
            params=[
                ParamField(
                    path="leaveType",
                    key="请假类型",
                    value="2",
                    type="enum",
                    enum_options=[{"label": "病假", "value": "2"}, {"label": "事假", "value": "3"}],
                    category="user_param",
                    source_kind="page_enum",
                    required=True,
                ),
                ParamField(path="reason", key="原因", value="年假", category="user_param"),
            ],
        )],
    )

    capabilities = build_default_flow_capabilities(spec)
    by_kind = {cap.kind: cap for cap in capabilities}

    assert set(by_kind) == {"list_options", "submit"}
    assert by_kind["submit"].step_ids == ["submit"]
    assert by_kind["submit"].name == "submit"
    assert by_kind["list_options"].input_schema["properties"]["field"]["enum"] == ["请假类型"]
    assert by_kind["list_options"].confirmed is True


def test_seal_application_keeps_control_preflights_and_maps_long_id_enum():
    seal_id = "f13a450364df1b8a269365f90f44aee0"
    process_id = "oa_seal_apply:1:aa840521"
    option_response = {"data": [
        {"id": seal_id, "name": "行政公章"},
        {"id": "d8896f988f51434ea6cdb1a48d71ee99", "name": "合同章"},
    ]}
    captured = [
        _get(1, "/system/seal/simple-list", option_response),
        _get(2, "/bpm/process-definition/get?key=oa_seal_apply", {"data": {"id": process_id}}),
        _get(
            3,
            "/bpm/approval-detail?processDefinitionId=oa_seal_apply%3A1%3Aaa840521&activityId=StartUserNode",
            {"data": {"node": "StartUserNode"}},
        ),
        _post(4, "/seal-apply/submit-process", {
            "sealId": seal_id,
            "applyTitle": "出差用章申请",
            "billType": "oa_seal_apply",
            "processDefKey": "oa_seal_apply",
        }),
    ]

    spec = to_flow_spec(
        captured,
        reads=[{"url": captured[0]["url"], "json": option_response, "role": "read_option"}],
        samples={"印章": "行政公章", "申请标题": "出差用章申请"},
    )

    assert [step.method for step in spec.steps] == ["GET", "GET", "POST"]
    assert all((step.source_meta or {}).get("control_preflight_for_write") for step in spec.steps[:2])
    assert len(spec.links) == 1
    assert spec.links[0].target_path == "query.processDefinitionId"
    assert spec.links[0].confirmed is True
    submit = spec.steps[-1]
    seal = next(param for param in submit.params if param.path == "sealId")
    assert seal.key == "印章"
    assert seal.type == "enum"
    assert seal.category == "user_param"
    assert seal.source_kind == "api_option"
    assert seal.enum_value_map == {
        "行政公章": seal_id,
        "合同章": "d8896f988f51434ea6cdb1a48d71ee99",
    }
    assert seal.need_human_confirm is False
    assert all(
        not param.need_human_confirm
        for step in spec.steps
        for param in step.params
        if param.path in {"query.key", "query.processDefinitionId", "query.activityId", "billType", "processDefKey"}
    )

    orchestrated = asyncio.run(orchestrate_flow_capabilities(spec))
    submit_cap = next(cap for cap in orchestrated.capabilities if cap.kind == "submit")
    assert submit_cap.step_ids == [step.step_id for step in spec.steps]
    assert not any(cap.kind == "query_status" for cap in orchestrated.capabilities)

    planned = asyncio.run(run_recording_pi_loop(spec, mode="plan"))
    planned_submit = next(cap for cap in planned.capabilities if cap.kind == "submit")
    assert planned_submit.confirmed is True
    assert planned_submit.requires_human_confirm is False
    assert "## 8.1 失败处理" in planned.business_description
    planned_report = validate_flow_spec(planned)
    assert planned_report["passed"] is True
    assert not any("前置接口保留" in item.title for item in planned.review_items if not item.resolved)
