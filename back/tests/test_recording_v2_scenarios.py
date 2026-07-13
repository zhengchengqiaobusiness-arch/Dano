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
    CapabilityRelation,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    SelectBinding,
    apply_flow_edits,
    build_default_flow_capabilities,
    flow_spec_to_api_request,
    flow_spec_to_client,
    orchestrate_flow_capabilities,
    prepare_flow_spec_for_publish,
    promote_request_to_step,
    run_recording_pi_loop,
    sync_flow_spec_models,
    to_flow_spec,
    validate_flow_spec,
)
from dano.execution.page.repair_ops import collect_capability_findings


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


def test_to_flow_spec_materializes_high_confidence_business_query_and_dependency_closure():
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

    assert [step.method for step in spec.steps] == ["GET", "GET", "POST"]
    assert [step.path.split("?", 1)[0] for step in spec.steps] == [
        "/daily-report/page",
        "/process/definition/get",
        "/daily-report/submit",
    ]
    assert len(spec.request_facts.requests) == 3
    independent = next(fact for fact in spec.request_facts.requests if "/daily-report/page" in fact.path)
    assert independent.request_id in {
        (step.source_meta or {}).get("request_id") for step in spec.steps
    }
    assert spec.request_facts.usage[independent.request_id].state == "materialized"

    orchestrated = asyncio.run(orchestrate_flow_capabilities(spec))
    by_kind = {cap.kind: cap for cap in orchestrated.capabilities}
    assert set(by_kind) == {"query_status", "submit"}
    assert [orchestrated.steps[[s.step_id for s in orchestrated.steps].index(sid)].path.split("?", 1)[0]
            for sid in by_kind["query_status"].step_ids] == ["/daily-report/page"]
    assert [orchestrated.steps[[s.step_id for s in orchestrated.steps].index(sid)].path.split("?", 1)[0]
            for sid in by_kind["submit"].step_ids] == ["/process/definition/get", "/daily-report/submit"]


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

    # 同一个值来自多个上游响应时来源不唯一，不能生成随机候选依赖。
    assert ambiguous.links == []
    assert all(link.confidence == 0.85 for link in ambiguous.links)


def test_default_capabilities_keep_enum_inside_submit_contract_without_empty_ability():
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

    assert set(by_kind) == {"submit"}
    assert by_kind["submit"].step_ids == ["submit"]
    assert by_kind["submit"].name == "submit"
    assert by_kind["submit"].input_schema["properties"]["请假类型"]["enum"] == ["病假", "事假"]


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


def test_daily_report_builds_independent_query_and_batch_submit_capabilities():
    query_steps = [
        FlowStep(
            step_id=f"query_{idx}",
            name=f"查询日报阶段{idx}",
            method="GET",
            url=f"/daily-report/query/{idx}",
            path=f"/daily-report/query/{idx}",
            source_meta={"role": "business_get", "sequence": idx},
            response_json={"data": {"filled_dates": ["2026-05-01"], "missing_dates": ["2026-05-11"]}},
        )
        for idx in range(1, 6)
    ]
    submit_preflights = [
        FlowStep(
            step_id=f"submit_context_{idx}",
            name=f"填报上下文{idx}",
            method="GET",
            url=f"/daily-report/submit-context/{idx}",
            path=f"/daily-report/submit-context/{idx}",
            source_meta={"role": "read_context", "sequence": idx + 5, "control_preflight_for_write": True},
        )
        for idx in range(1, 4)
    ]
    submit = FlowStep(
        step_id="submit_batch",
        name="批量填写日报",
        method="POST",
        url="/daily-report/submit-batch",
        path="/daily-report/submit-batch",
        body_source='[{"date":"2026-05-11","content":"开发"}]',
        source_meta={"role": "submit_anchor", "sequence": 9},
        params=[
            ParamField(path="[0].date", key="日期", type="date", category="user_param", source_kind="user_input"),
            ParamField(path="[0].content", key="工作内容", category="user_param", source_kind="user_input"),
        ],
    )
    spec = FlowSpec(flow_id="daily-two-capabilities", steps=[*query_steps, *submit_preflights, submit])

    capabilities = build_default_flow_capabilities(spec)
    by_kind = {cap.kind: cap for cap in capabilities}

    assert set(by_kind) == {"query_status", "submit_batch"}
    assert by_kind["query_status"].step_ids == [step.step_id for step in query_steps]
    assert by_kind["submit_batch"].step_ids == [step.step_id for step in [*submit_preflights, submit]]
    assert {mapping["name"] for mapping in by_kind["query_status"].output_mapping} == {
        "filled_dates", "missing_dates",
    }
    assert {mapping["step_id"] for mapping in by_kind["query_status"].output_mapping} == {"query_5"}
    assert all(mapping["kind"] == "batch_result" for mapping in by_kind["submit_batch"].output_mapping)
    assert not set(by_kind["query_status"].step_ids) & set(by_kind["submit_batch"].step_ids)

    orchestrated = asyncio.run(orchestrate_flow_capabilities(spec))
    assert len(orchestrated.capability_relations) == 1
    relation = orchestrated.capability_relations[0]
    assert relation.type == "external_transform"
    assert relation.transform_owner == "caller"
    assert relation.from_output == "missing_dates"
    assert relation.to_input == "entries"
    api_request, errors = flow_spec_to_api_request(orchestrated)
    assert errors == []
    assert api_request["capability_graph"]["relations"][0]["mode"] == "external_transform"


def test_query_output_mapping_uses_stable_names_for_numeric_urls():
    steps = [
        FlowStep(
            step_id=f"query_{idx}",
            method="GET",
            url=f"/daily/query/{idx}",
            path=f"/daily/query/{idx}",
            source_meta={"role": "business_get", "confidence": 0.93},
            response_json={"data": {"value": idx}},
        )
        for idx in range(1, 4)
    ]

    cap = build_default_flow_capabilities(FlowSpec(flow_id="numeric-query-output", steps=steps))[0]

    assert [mapping["name"] for mapping in cap.output_mapping] == ["query_1", "query_2", "query_3"]


def test_missing_dates_query_and_single_row_submit_compile_to_foreach_batch_contract():
    query = FlowStep(
        step_id="query_missing", method="GET", path="/daily/page",
        source_meta={"role": "business_get", "confidence": 0.96},
        response_json={"data": {"filled_dates": ["2026-05-01"], "missing_dates": ["2026-05-11"]}},
    )
    submit = FlowStep(
        step_id="submit_one", method="POST", path="/daily/submit", url="/daily/submit",
        body_source='{"date":"2026-05-11","content":"开发"}',
        source_meta={"role": "submit_anchor"},
        params=[
            ParamField(path="date", key="日报日期", type="date", category="user_param", source_kind="user_input"),
            ParamField(path="content", key="工作内容", category="user_param", source_kind="user_input"),
        ],
    )

    out = asyncio.run(orchestrate_flow_capabilities(FlowSpec(steps=[query, submit])))
    batch = next(cap for cap in out.capabilities if cap.kind == "submit_batch")

    assert batch.input_schema["properties"]["entries"]["type"] == "array"
    assert any(node.get("type") == "foreach" for node in batch.nodes)
    assert out.capability_relations[0].type == "external_transform"


def test_legacy_query_url_materializes_capability_inputs_from_step_params():
    query = FlowStep(
        step_id="query",
        method="GET",
        url="/daily/page?keyword=alice&pageNo=1&pageSize=20",
        path="/daily/page",
        source_meta={"role": "business_get"},
        response_json={"data": {"records": []}},
    )

    spec = FlowSpec(steps=[query])
    params = {param.path: param for param in spec.steps[0].params}
    cap = build_default_flow_capabilities(spec)[0]

    assert set(params) == {"query.keyword", "query.pageNo", "query.pageSize"}
    assert params["query.keyword"].category == "user_param"
    assert params["query.pageNo"].category == "system_const"
    assert set(cap.input_schema["properties"]) == {"keyword"}


def test_query_output_fields_use_mapped_response_schema_types():
    query = FlowStep(
        step_id="query",
        method="GET",
        path="/daily/page",
        source_meta={"role": "business_get"},
        response_json={"data": {"missing_dates": ["2026-05-11"], "total": 1}},
    )

    out = asyncio.run(orchestrate_flow_capabilities(FlowSpec(steps=[query])))
    cap = out.capabilities[0]
    fields = {field.key: field.type for field in cap.outputs}

    assert fields["missing_dates"] == "array"
    assert fields["total"] == "number"
    assert set(cap.output_schema["required"]) == {"missing_dates", "total"}
    assert all(field.required for field in cap.outputs)


def test_planner_batch_kind_uses_same_recorded_evidence_as_default_planner():
    query = FlowStep(
        step_id="query_missing",
        method="GET",
        path="/daily/page",
        source_meta={"role": "business_get"},
        response_json={"data": {"missing_dates": ["2026-05-11"]}},
    )
    submit = FlowStep(
        step_id="submit_one",
        method="POST",
        path="/daily/submit",
        body_source='{"date":"2026-05-11","content":"开发"}',
        params=[
            ParamField(path="date", key="日报日期", type="date", source_kind="user_input"),
            ParamField(path="content", key="工作内容", source_kind="user_input"),
        ],
    )
    spec = FlowSpec(
        steps=[query, submit],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            step_ids=["submit_one"],
            evidence=[{"kind": "planner"}],
        )],
    )

    repaired = flow_spec_module._repair_generated_capability_contracts(spec)

    assert repaired.capabilities[0].kind == "submit_batch"
    assert repaired.capabilities[0].name == "submit_batch"


def test_external_transform_relation_prunes_only_stale_derived_mapping():
    query = FlowCapability(
        name="query_status",
        kind="query_status",
        output_schema={"type": "object", "properties": {"records": {"type": "array"}}},
    )
    submit = FlowCapability(
        name="submit_batch",
        kind="submit_batch",
        input_schema={"type": "object", "properties": {"entries": {"type": "array"}}},
    )
    stale = CapabilityRelation(
        relation_id="stale",
        from_capability="query_status",
        from_output="missing_dates",
        to_capability="submit_batch",
        to_input="entries",
        evidence={"kind": "typed_capability_contract"},
    )
    manual = stale.model_copy(deep=True)
    manual.relation_id = "manual"
    manual.evidence = {"kind": "user_confirmed"}
    spec = FlowSpec(capabilities=[query, submit], capability_relations=[stale, manual])

    flow_spec_module._ensure_external_transform_relations(spec)

    assert [relation.relation_id for relation in spec.capability_relations] == ["manual"]


def test_query_then_submit_creates_caller_decision_without_fake_field_mapping():
    query = FlowStep(
        step_id="query_status", method="GET", path="/records/page",
        source_meta={"role": "business_get"},
        response_json={"data": {"records": [{"date": "2026-05-01"}]}},
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/records/submit",
        body_source='{"date":"2026-05-02","content":"开发"}',
        source_meta={"role": "submit_anchor"},
        params=[ParamField(path="date", key="日期", type="date", source_kind="user_input")],
    )

    out = asyncio.run(orchestrate_flow_capabilities(FlowSpec(steps=[query, submit])))

    assert {cap.kind for cap in out.capabilities} == {"query_status", "submit"}
    assert len(out.capability_relations) == 1
    relation = out.capability_relations[0]
    assert relation.type == "caller_decision"
    assert relation.from_output == ""
    assert relation.to_input == ""
    assert relation.evidence["automatic_execution"] is False
    report = validate_flow_spec(out)
    assert not any("output/input 字段" in message for message in report["errors"])


def test_page_enum_binding_is_projected_to_param_and_capability_contract():
    step = FlowStep(
        step_id="submit",
        method="POST",
        path="/leave/submit",
        body_source='{"type":"2"}',
        params=[ParamField(
            path="type", key="请假类型", value="2", type="string",
            category="user_param", source_kind="user_input", required=True,
        )],
        selects=[SelectBinding(
            param="请假类型", path="type", enum_source="dom", enum_confirmed=True,
            options=[
                {"label": "病假", "value": "1"},
                {"label": "事假", "value": "2"},
                {"label": "婚假", "value": "3"},
            ],
            option_map={"病假": "1", "事假": "2", "婚假": "3"},
        )],
        response_json={"code": 0, "data": {"id": "leave-1"}},
    )
    spec = FlowSpec(
        steps=[step],
        capabilities=[FlowCapability(name="submit", kind="submit", step_ids=["submit"])],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    param = prepared.steps[0].params[0]
    capability_field = prepared.capabilities[0].inputs[0]
    api_request, errors = flow_spec_to_api_request(prepared)

    assert errors == []
    assert (param.type, param.source_kind) == ("enum", "page_enum")
    assert param.enum_value_map == {"病假": "1", "事假": "2", "婚假": "3"}
    assert capability_field.source_kind == "page_enum"
    assert capability_field.enum_options == param.enum_options
    assert api_request["capabilities"][0]["input_schema"]["properties"]["请假类型"]["enum"] == ["病假", "事假", "婚假"]
    assert not any("内部 ID/短码" in message for message in validate_flow_spec(prepared)["errors"])


def test_api_option_binding_preserves_source_request_in_capability_field():
    step = FlowStep(
        step_id="submit",
        method="POST",
        path="/leave/submit",
        body_source='{"assigneeId":"142"}',
        params=[ParamField(
            path="assigneeId", key="审批人", value="142", type="string",
            category="user_param", source_kind="user_input", required=True,
        )],
        selects=[SelectBinding(
            param="审批人", path="assigneeId", source_url="/users/options",
            source_method="GET", source_role="read_option", source_request_id="users-options",
            value_key="id", label_key="name", enum_source="api", enum_confirmed=True,
            options=[{"label": "张三", "value": "142"}], option_map={"张三": "142"},
        )],
        response_json={"code": 0},
    )
    spec = FlowSpec(
        steps=[step],
        capabilities=[FlowCapability(name="submit", kind="submit", step_ids=["submit"])],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    param = prepared.steps[0].params[0]
    field = prepared.capabilities[0].inputs[0]

    assert (param.type, param.source_kind) == ("string", "api_option")
    assert param.source["source_url"] == "/users/options"
    assert param.source["source_request_id"] == "users-options"
    assert field.source_kind == "api_option"
    assert field.source["source_url"] == "/users/options"
    assert field.enum_value_map == {"张三": "142"}


def test_api_option_reselection_refreshes_candidates_without_changing_field_type():
    captured = [
        _get(1, "/api/old/options", {"data": []}),
        _get(2, "/api/new/options", {"data": [
            {"code": 2, "title": "行政章"},
            {"code": 3, "title": "合同章"},
        ]}),
        _post(3, "/seal/borrow", {"sealCode": 2}),
    ]
    spec = to_flow_spec(captured, samples={"公章": 2})
    submit = spec.steps[-1]
    param = next(item for item in submit.params if item.path == "sealCode")
    param.type = "number"
    param.source_kind = "api_option"
    submit.selects = [SelectBinding(
        param=param.key,
        path=param.path,
        source_url="https://oa.example.test/api/old/options",
        source_request_id="1",
        value_key="id",
        label_key="name",
        options=[],
        enum_source="api",
    )]

    spec = sync_flow_spec_models(spec)
    binding = spec.steps[-1].selects[0]
    binding.source_url = "https://oa.example.test/api/new/options"
    spec = sync_flow_spec_models(spec)
    submit = spec.steps[-1]
    param = next(item for item in submit.params if item.path == "sealCode")
    binding = submit.selects[0]

    assert (param.type, param.source_kind) == ("number", "api_option")
    assert (binding.value_key, binding.label_key) == ("code", "title")
    assert binding.options == [
        {"label": "行政章", "value": 2},
        {"label": "合同章", "value": 3},
    ]
    assert param.enum_options == binding.options


def test_empty_api_candidates_are_valid_and_do_not_emit_dynamic_enum_warning():
    step = FlowStep(
        step_id="submit",
        method="POST",
        path="/seal/borrow",
        params=[ParamField(
            path="sealId",
            key="公章",
            type="enum",
            category="user_param",
            source_kind="api_option",
            source={"kind": "api_option"},
            enum_options=None,
            enum_value_map=None,
        )],
        selects=[SelectBinding(param="公章", path="sealId", options=[])],
    )
    report = validate_flow_spec(FlowSpec(
        steps=[step],
        capabilities=[FlowCapability(name="submit", kind="submit", step_ids=["submit"])],
    ))
    messages = [*report["errors"], *report["warnings"]]

    assert not any("动态枚举缺少可执行的实时来源接口" in message for message in messages)
    assert not any("标记为接口选项，但缺少可执行" in message for message in messages)


def test_option_endpoint_unmatched_filters_are_constants_but_recorded_search_is_input():
    spec = to_flow_spec([_get(
        1,
        "/system/seal/simple-list?status=0&keyword=%E8%A1%8C%E6%94%BF",
        {"data": [{"id": "s1", "name": "行政章"}]},
    )], samples={"搜索词": "行政"})
    step = promote_request_to_step(spec, request_index=1)
    by_path = {param.path: param for param in step.params}

    assert (by_path["query.status"].category, by_path["query.status"].source_kind) == (
        "system_const", "constant",
    )
    assert (by_path["query.keyword"].category, by_path["query.keyword"].source_kind) == (
        "user_param", "user_input",
    )


def test_complex_business_domains_split_into_independent_capabilities():
    steps = [
        FlowStep(
            step_id="leave-query", method="GET", path="/oa/leave/page",
            source_meta={"role": "business_get"},
            response_json={"data": {"list": [{"id": 1}]}},
        ),
        FlowStep(
            step_id="expense-query", method="GET", path="/oa/expense/page",
            source_meta={"role": "business_get"},
            response_json={"data": {"list": [{"id": 2}]}},
        ),
        FlowStep(step_id="leave-submit", method="POST", path="/oa/leave/submit"),
        FlowStep(step_id="expense-submit", method="POST", path="/oa/expense/submit"),
    ]

    capabilities = build_default_flow_capabilities(FlowSpec(steps=steps))
    by_name = {cap.name: cap for cap in capabilities}

    assert set(by_name) == {
        "query_status_leave", "query_status_expense", "submit_leave", "submit_expense",
    }
    assert by_name["query_status_leave"].step_ids == ["leave-query"]
    assert by_name["query_status_expense"].step_ids == ["expense-query"]
    assert by_name["submit_leave"].step_ids == ["leave-submit"]
    assert by_name["submit_expense"].step_ids == ["expense-submit"]


def test_cross_domain_write_dependency_prevents_unsafe_automatic_split():
    spec = FlowSpec(
        steps=[
            FlowStep(step_id="draft", method="POST", path="/oa/draft/create"),
            FlowStep(step_id="archive", method="POST", path="/oa/archive/commit"),
        ],
        links=[FlowLink(
            source_step_id="draft", source_path="data.id",
            target_step_id="archive", target_path="draftId",
        )],
    )

    capabilities = build_default_flow_capabilities(spec)

    assert [(cap.name, cap.step_ids) for cap in capabilities] == [
        ("submit", ["draft", "archive"]),
    ]


def test_initial_generation_splits_empty_list_from_previous_page_without_changing_incremental_rules():
    query = FlowStep(
        step_id="seal-page",
        method="GET",
        path="/admin-api/oa/seal-apply/page?pageNo=1&pageSize=10",
        # SPA route changes retain the same browser page_id; the response-backed
        # /page contract must still win over the initial preflight heuristic.
        source_meta={"page_id": "page_1", "control_preflight_for_write": True},
        response_json={"data": {"list": [], "total": 0}},
        params=[
            ParamField(path="query.useTime[0]", key="useTime[0]", type="datetime", category="user_param"),
            ParamField(path="query.useTime[1]", key="useTime[1]", type="datetime", category="user_param"),
        ],
    )
    definition = FlowStep(
        step_id="definition",
        method="GET",
        path="/admin-api/bpm/process-definition/get?key=oa_seal_apply",
        source_meta={"page_id": "page_1", "control_preflight_for_write": True},
    )
    approval = FlowStep(
        step_id="approval",
        method="GET",
        path="/admin-api/bpm/process-instance/get-approval-detail",
        source_meta={"page_id": "page_1", "control_preflight_for_write": True},
    )
    submit = FlowStep(
        step_id="submit",
        method="POST",
        path="/admin-api/oa/seal-apply/submit-process",
        source_meta={"page_id": "page_1"},
        params=[
            ParamField(path="sealId", key="印章编号", type="enum", category="user_param"),
            ParamField(path="applyTitle", key="申请标题", category="user_param"),
            ParamField(path="useTime", key="使用日期", type="datetime", category="user_param"),
            ParamField(path="returnTime", key="归还日期", type="datetime", category="user_param"),
            ParamField(path="description", key="使用描述", category="user_param"),
            ParamField(path="remark", key="备注", category="user_param"),
        ],
    )

    raw = FlowSpec(steps=[query, definition, approval, submit])
    unchanged_default = build_default_flow_capabilities(raw)
    generated = flow_spec_module.ensure_flow_capabilities(raw.model_copy(deep=True))
    capabilities = generated.capabilities
    by_kind = {cap.kind: cap for cap in capabilities}

    # The ordinary/default builder and incremental rules are intentionally not
    # changed; only the zero-capability initialization corrects page boundaries.
    assert [(cap.kind, cap.step_ids) for cap in unchanged_default] == [(
        "submit", ["seal-page", "definition", "approval", "submit"],
    )]
    assert set(by_kind) == {"query_status", "submit"}
    assert by_kind["query_status"].step_ids == ["seal-page"]
    assert set(by_kind["query_status"].input_schema["properties"]) == {"useTime[0]", "useTime[1]"}
    assert by_kind["submit"].step_ids == ["definition", "approval", "submit"]
    assert set(by_kind["submit"].input_schema["properties"]) == {
        "印章编号", "申请标题", "使用日期", "归还日期", "使用描述", "备注",
    }

    incremental = asyncio.run(orchestrate_flow_capabilities(
        FlowSpec(
            steps=[query, definition, approval, submit],
            capabilities=unchanged_default,
        ),
        llm_client=None,
        model=None,
    ))
    assert [(cap.kind, cap.step_ids) for cap in incremental.capabilities] == [(
        "submit", ["seal-page", "definition", "approval", "submit"],
    )]


def _seal_semantic_spec() -> FlowSpec:
    return FlowSpec(steps=[
        FlowStep(
            step_id="seal-page", method="GET",
            path="/admin-api/oa/seal-apply/page?pageNo=1&pageSize=10",
            source_meta={"page_id": "page-list", "control_preflight_for_write": True},
            response_json={"data": {"list": [], "total": 0}},
            params=[
                ParamField(
                    path="query.useTime[0]", key="useTime[0]", value="2026-07-09 00:00:00",
                    type="datetime", category="user_param", source_kind="user_input",
                ),
                ParamField(
                    path="query.useTime[1]", key="useTime[1]", value="2026-08-11 23:59:59",
                    type="datetime", category="user_param", source_kind="user_input",
                ),
            ],
        ),
        FlowStep(
            step_id="definition", method="GET",
            path="/admin-api/bpm/process-definition/get?key=oa_seal_apply",
            source_meta={"page_id": "page-form", "control_preflight_for_write": True},
        ),
        FlowStep(
            step_id="approval", method="GET",
            path="/admin-api/bpm/process-instance/get-approval-detail",
            source_meta={"page_id": "page-form", "control_preflight_for_write": True},
        ),
        FlowStep(
            step_id="submit", method="POST", path="/admin-api/oa/seal-apply/submit-process",
            source_meta={"page_id": "page-form"},
            params=[
                ParamField(path="sealId", key="印章编号", type="enum", category="user_param"),
                ParamField(path="applyTitle", key="申请标题", category="user_param"),
                ParamField(path="useTime", key="使用日期", type="datetime", category="user_param"),
                ParamField(path="returnTime", key="归还日期", type="datetime", category="user_param"),
                ParamField(path="description", key="使用描述", category="user_param"),
                ParamField(path="remark", key="备注", category="user_param"),
            ],
        ),
    ])


class _CompleteSemanticPlanner:
    def __init__(self):
        self.requests = []

    async def complete_json_messages(self, **kwargs):
        self.requests.append(kwargs["messages"])
        return {
            "semantic_plan": {
                "business_understanding": {
                    "intent": "查询公章借阅记录并提交公章借阅申请",
                },
                "request_roles": [
                    {"step_id": "seal-page", "role": "business_query", "name": "查询公章借阅记录", "reason": "列表页查询"},
                    {"step_id": "definition", "role": "submit_preflight", "name": "获取公章申请流程定义", "reason": "提交前流程定义"},
                    {"step_id": "approval", "role": "submit_preflight", "name": "获取公章申请审批配置", "reason": "提交前审批配置"},
                    {"step_id": "submit", "role": "business_write", "name": "提交公章借阅申请", "reason": "最终写接口"},
                ],
                "field_semantics": [
                    {
                        "step_id": step.step_id,
                        "wire_path": param.path,
                        "public_name": {
                            "query.useTime[0]": "查询开始时间",
                            "query.useTime[1]": "查询结束时间",
                        }.get(param.path, param.key),
                        "business_type": param.type,
                        "source_kind": param.source_kind,
                        "confidence": 0.99,
                    }
                    for step in _seal_semantic_spec().steps
                    for param in step.params
                ],
                "capabilities": [
                    {
                        "name": "query_status", "kind": "query_status",
                        "title": "查询公章借阅记录", "intent": "查询现有公章借阅记录",
                        "step_ids": ["seal-page"],
                    },
                    {
                        "name": "submit", "kind": "submit",
                        "title": "提交公章借阅申请", "intent": "提交单个公章借阅申请",
                        "step_ids": ["definition", "approval", "submit"],
                    },
                ],
                "capability_relations": [{
                    "from": "query_status", "to": "submit", "type": "caller_decision",
                }],
                "unresolved_items": [],
            },
            "ops": [],
        }


def test_initial_semantic_generation_names_indexed_range_inherits_context_and_reuses_result():
    planner = _CompleteSemanticPlanner()
    generated = asyncio.run(run_recording_pi_loop(
        _seal_semantic_spec(), llm_client=planner, model="semantic-model", mode="plan",
    ))

    query = next(step for step in generated.steps if step.step_id == "seal-page")
    assert [(param.key, param.path) for param in query.params if "useTime" in param.path] == [
        ("查询开始时间", "query.useTime[0]"),
        ("查询结束时间", "query.useTime[1]"),
    ]
    assert {cap.kind for cap in generated.capabilities} == {"query_status", "submit"}
    assert {step.step_id: step.name for step in generated.steps} == {
        "seal-page": "查询公章借阅记录",
        "definition": "获取公章申请流程定义",
        "approval": "获取公章申请审批配置",
        "submit": "提交公章借阅申请",
    }
    assert generated.meta["capability_generation"]["initial_completed"] is True
    assert generated.meta["capability_generation"]["status"] == "ready"
    initial_call_count = len(planner.requests)
    assert 2 <= initial_call_count <= 8  # plan/repair loop is bounded
    assert planner.requests[1][:3] == planner.requests[0]

    optimized = asyncio.run(run_recording_pi_loop(
        generated, llm_client=planner, model="semantic-model", mode="plan",
    ))
    assert len(planner.requests) == initial_call_count
    assert optimized.meta["recording_pi_loop"]["cache_hit"] is True


def test_small_manual_change_runs_one_incremental_planner_then_reuses_result():
    initial_planner = _CompleteSemanticPlanner()
    generated = asyncio.run(run_recording_pi_loop(
        _seal_semantic_spec(), llm_client=initial_planner, model="semantic-model", mode="plan",
    ))
    submit = next(step for step in generated.steps if step.step_id == "submit")
    remark = next(param for param in submit.params if param.path == "remark")
    remark.required = False
    remark.required_source = "manual"

    class DeltaPlanner:
        def __init__(self):
            self.requests = []

        async def complete_json_messages(self, **kwargs):
            self.requests.append(kwargs["messages"])
            return {
                "reviewed_scope": {
                    "changed_fields": ["submit:remark"],
                    "affected_capabilities": ["submit"],
                    "reason": "调用方将备注改为可选",
                },
                "ops": [],
                "unresolved_items": [],
            }

    delta_planner = DeltaPlanner()
    optimized = asyncio.run(run_recording_pi_loop(
        generated, llm_client=delta_planner, model="semantic-model", mode="plan",
    ))

    assert len(delta_planner.requests) == 1
    assert "增量优化" in delta_planner.requests[0][-1]["content"]
    assert optimized.meta["capability_generation"]["application_cache_hit"] is False
    assert optimized.meta["capability_generation"]["model_calls"] == 1

    reused = asyncio.run(run_recording_pi_loop(
        optimized, llm_client=delta_planner, model="semantic-model", mode="plan",
    ))
    assert len(delta_planner.requests) == 1
    assert reused.meta["capability_generation"]["application_cache_hit"] is True
    assert reused.meta["capability_generation"]["model_calls"] == 0


def test_indexed_range_semantics_are_grounded_even_when_model_is_unavailable():
    generated = asyncio.run(run_recording_pi_loop(
        _seal_semantic_spec(), llm_client=None, model=None, mode="plan",
    ))
    query = next(step for step in generated.steps if step.step_id == "seal-page")
    assert [(param.key, param.path) for param in query.params if "useTime" in param.path] == [
        ("查询开始时间", "query.useTime[0]"),
        ("查询结束时间", "query.useTime[1]"),
    ]
    assert generated.meta["capability_generation"]["status"] == "degraded_deterministic"


def test_complete_semantic_plan_can_split_one_deterministic_write_family_on_first_run():
    class SplitPlanner:
        async def complete_json(self, **_kwargs):
            return {"semantic_plan": {
                "business_understanding": {"intent": "分别保存草稿并提交订单"},
                "request_roles": [
                    {"step_id": "draft", "role": "business_write", "name": "保存订单草稿", "reason": "独立保存动作"},
                    {"step_id": "commit", "role": "business_write", "name": "提交订单", "reason": "独立提交动作"},
                ],
                "field_semantics": [],
                "capabilities": [
                    {"name": "save_draft", "title": "保存订单草稿", "kind": "submit", "intent": "保存草稿", "step_ids": ["draft"]},
                    {"name": "commit_order", "title": "提交订单", "kind": "submit", "intent": "提交订单", "step_ids": ["commit"]},
                ],
                "capability_relations": [],
                "unresolved_items": [],
            }, "ops": []}

    spec = FlowSpec(steps=[
        FlowStep(step_id="draft", method="POST", path="/api/order/draft", body_source="{}"),
        FlowStep(step_id="commit", method="POST", path="/api/order/commit", body_source="{}"),
    ])
    generated = asyncio.run(orchestrate_flow_capabilities(
        spec, llm_client=SplitPlanner(), model="semantic", generation_mode="initial",
    ))

    assert {(cap.name, tuple(cap.step_ids)) for cap in generated.capabilities} == {
        ("save_draft", ("draft",)),
        ("commit_order", ("commit",)),
    }


class _InitialSingleCapabilityPlanner:
    async def complete_json(self, **_kwargs):
        return {
            "ops": [{
                "op": "add_request_to_capability",
                "capability": "submit",
                "step_id": "seal-page",
            }],
            "abilities": [{
                "name": "submit_all",
                "kind": "submit",
                "step_ids": ["seal-page", "definition", "approval", "submit"],
            }],
        }


def test_initial_planner_cannot_merge_deterministic_page_boundaries_back_into_one_capability():
    query = FlowStep(
        step_id="seal-page", method="GET", path="/oa/seal-apply/page",
        source_meta={"control_preflight_for_write": True},
        response_json={"data": {"list": [], "total": 0}},
    )
    definition = FlowStep(
        step_id="definition", method="GET", path="/bpm/process-definition/get",
        source_meta={"control_preflight_for_write": True},
    )
    approval = FlowStep(
        step_id="approval", method="GET", path="/bpm/process-instance/get-approval-detail",
        source_meta={"control_preflight_for_write": True},
    )
    submit = FlowStep(step_id="submit", method="POST", path="/oa/seal-apply/submit-process")

    out = asyncio.run(orchestrate_flow_capabilities(
        FlowSpec(steps=[query, definition, approval, submit]),
        llm_client=_InitialSingleCapabilityPlanner(),
        model="fake",
    ))
    by_kind = {cap.kind: cap for cap in out.capabilities}

    assert set(by_kind) == {"query_status", "submit"}
    assert by_kind["query_status"].step_ids == ["seal-page"]
    assert by_kind["submit"].step_ids == ["definition", "approval", "submit"]


def test_publish_preparation_removes_stale_batch_fields_outputs_and_goal_capability():
    step = FlowStep(
        step_id="submit", method="POST", path="/leave/submit",
        body_source='{"reason":"事假"}',
        params=[ParamField(path="reason", key="原因", value="事假", required=True)],
        response_json={"code": 0, "data": {"id": "leave-1"}},
    )
    capability = FlowCapability(
        name="submit", kind="submit", step_ids=["submit"],
        input_schema={"type": "object", "properties": {"entries": {"type": "array"}}, "required": ["entries"]},
        output_schema={"type": "object", "properties": {}},
        inputs=[CapabilityField(scope="input", key="entries", path="entries", type="array", locked=True)],
        outputs=[CapabilityField(scope="output", key="response", path="response", type="object", locked=True)],
        output_mapping=[{"kind": "final_response", "name": "result", "step_id": "submit", "response_path": "response"}],
    )
    spec = FlowSpec(
        steps=[step], capabilities=[capability],
        goal={"intent": "提交请假", "capabilities": ["list_options", "submit"]},
    )

    prepared = prepare_flow_spec_for_publish(spec)
    api_request, errors = flow_spec_to_api_request(prepared)

    assert errors == []
    assert prepared.goal["capabilities"] == ["submit"]
    assert set(prepared.capabilities[0].input_schema["properties"]) == {"原因"}
    assert [field.key for field in prepared.capabilities[0].inputs] == ["原因"]
    assert "result" in prepared.capabilities[0].output_schema["properties"]
    assert collect_capability_findings(api_request) == []


def test_final_response_output_uses_one_canonical_name_in_fields_and_schema():
    step = FlowStep(
        step_id="submit", method="POST", path="/submit",
        body_source='{"reason":"test"}',
        params=[ParamField(path="reason", key="原因", value="test", required=True)],
        response_json={"code": 0, "data": {"id": "one"}},
    )
    spec = FlowSpec(
        steps=[step],
        capabilities=[FlowCapability(
            name="submit", kind="submit", step_ids=["submit"],
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
        )],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    cap = prepared.capabilities[0]
    api_request, errors = flow_spec_to_api_request(prepared)

    assert errors == []
    assert set(cap.output_schema["properties"]) == {"output_1"}
    assert [field.key for field in cap.outputs] == ["output_1"]
    assert collect_capability_findings(api_request) == []


def test_relation_without_field_mapping_is_canonicalized_as_caller_decision():
    query = FlowStep(
        step_id="query", method="GET", path="/leave/page",
        response_json={"data": {"records": []}},
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/leave/submit",
        body_source='{"reason":"test"}',
        params=[ParamField(path="reason", key="原因", value="test")],
        response_json={"code": 0},
    )
    spec = FlowSpec(
        steps=[query, submit],
        capabilities=[
            FlowCapability(name="query_status", kind="query_status", step_ids=["query"]),
            FlowCapability(name="submit", kind="submit", step_ids=["submit"]),
        ],
        capability_relations=[CapabilityRelation(
            relation_id="legacy-empty-transform",
            type="suggested_call_chain",
            mode="external_transform",
            from_capability="query_status",
            to_capability="submit",
            confirmed=True,
        )],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    relation = prepared.capability_relations[0]
    api_request, errors = flow_spec_to_api_request(prepared)

    assert errors == []
    assert (relation.type, relation.mode) == ("caller_decision", "caller_decision")
    assert relation.from_output == "" and relation.to_input == ""
    assert not any("relation" in item["kind"] for item in collect_capability_findings(api_request))


def test_external_transform_with_only_one_field_remains_invalid():
    relation = CapabilityRelation(
        type="external_transform", mode="external_transform",
        from_capability="query_status", from_output="records",
        to_capability="submit", to_input="",
    )

    normalized = flow_spec_module._normalize_capability_relation_semantics(relation)

    assert normalized.type == "external_transform"
    assert normalized.mode == "external_transform"
