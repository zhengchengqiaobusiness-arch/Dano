"""Step B · FlowSpec 字段/link/step 编辑测试。"""

import asyncio
import json
from pathlib import Path

import pytest
import dano.execution.page.flow_spec as flow_spec_module

from dano.execution.page.flow_spec import (
    FlowSpec, FlowStep, FlowLink, ParamField, SelectBinding, IdentityBinding, FlowCapability,
    CapabilityDependency, CapabilityField, CapabilityRelation,
    RequestFacts,
    apply_flow_edits, validate_flow_spec, _infer_type_from_value,
    refresh_review_items, flow_spec_to_api_request,
    compile_capability_to_api_request,
    flow_spec_to_client,
    apply_recording_agent_submission, auto_fix_flow_spec, orchestrate_flow_capabilities, sync_flow_spec_models,
    flow_spec_canonical_summary, to_flow_spec,
    build_default_flow_capabilities, prepare_flow_spec_for_publish, prepare_flow_release_candidate,
    flow_operation_report,
)
from dano.execution.page.request_capture import execute_api_request
from dano.execution.page.repair_ops import collect_capability_findings, collect_repair_findings


def _call_nodes(step_ids: list[str]) -> list[dict]:
    return [
        {"id": f"call_{index}", "type": "call", "step_id": step_id}
        for index, step_id in enumerate(step_ids)
    ]


def _request_facts_from_graph_fixture(graph: dict) -> RequestFacts:
    """Translate legacy-shaped test evidence into the canonical test contract."""
    requests = []
    analysis = {}
    usage = {}
    seen = set()
    bucket_rank = {
        "all_requests": 0,
        "filtered_requests": 1,
        "candidate_reads": 2,
        "selected_steps": 3,
    }
    for bucket in ("all_requests", "filtered_requests", "candidate_reads", "selected_steps"):
        for raw in graph.get(bucket) or []:
            entry = dict(raw)
            request_id = str(entry.get("request_id") or (
                f"idx:{entry.get('request_index')}" if entry.get("request_index") is not None else ""
            ))
            entry["request_id"] = request_id
            identity = (request_id, entry.get("request_index"))
            if identity not in seen:
                requests.append({
                    key: value for key, value in entry.items()
                    if key not in {
                        "role", "semantic_roles", "keep", "reason", "confidence",
                        "evidence", "bucket", "filter_reason", "state",
                        "materialized_step_id", "used_by_capabilities",
                    }
                })
                seen.add(identity)
            current = analysis.get(request_id)
            current_rank = bucket_rank.get(str((current or {}).get("bucket") or ""), -1)
            if bucket_rank[bucket] >= current_rank:
                analysis[request_id] = {
                    "request_id": request_id,
                    "role": entry.get("role") or "",
                    "semantic_roles": entry.get("semantic_roles") or [],
                    "keep": bool(entry.get("keep")),
                    "reason": entry.get("reason") or "",
                    "confidence": float(entry.get("confidence") or 0),
                    "evidence": entry.get("evidence") or {},
                    "bucket": bucket,
                    "filter_reason": entry.get("filter_reason") or "",
                }
            materialized_step_id = str(entry.get("materialized_step_id") or "")
            usage[request_id] = {
                "request_id": request_id,
                "materialized_step_id": materialized_step_id,
                "state": "materialized" if materialized_step_id else entry.get("state") or "captured",
                "used_by_capabilities": entry.get("used_by_capabilities") or [],
            }
    return RequestFacts.model_validate({
        "requests": requests,
        "analysis": analysis,
        "usage": usage,
    })


def _make_spec():
    param1 = ParamField(path="form.userId", key="userId", value="123", type="string", required=True)
    param2 = ParamField(path="form.name", key="name", value="test", type="string", required=True)
    step1 = FlowStep(
        step_id="step1", method="POST", url="/api/submit", path="/api/submit",
        params=[param1, param2], risk_level="L3", sample_inputs={"userId": "123", "name": "test"},
    )
    return FlowSpec(flow_id="test", steps=[step1])


def test_single_required_flag_excludes_runtime_value_from_caller_schema():
    runtime_value = ParamField(
        path="applicantId", key="申请人标识", required=False,
        category="runtime_var", source_kind="current_user",
        exposed_to_user=False,
    )
    title = ParamField(
        path="title", key="申请标题", required=True,
        category="user_param", source_kind="user_input",
        exposed_to_user=True,
    )
    optional_remark = ParamField(
        path="remark", key="备注", required=False,
        category="user_param", source_kind="user_input",
        exposed_to_user=True,
    )
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/submit",
        params=[runtime_value, title, optional_remark],
    )])

    schema = flow_spec_module._capability_input_schema(spec.steps[0].params)
    assert set(schema["properties"]) == {"申请标题", "备注"}
    assert schema["required"] == ["申请标题"]
    assert flow_spec_module.flow_spec_required_params(spec) == ["申请标题"]


@pytest.mark.parametrize("required", [True, False], ids=["required", "optional"])
def test_required_flag_changes_caller_schema_without_blocking_publish(required):
    step = FlowStep(
        step_id="submit",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"title":"demo"}',
        params=[ParamField(
            path="title",
            key="申请标题",
            value="demo",
            required=required,
            category="user_param",
            source_kind="user_input",
            exposed_to_user=True,
        )],
        success_rule={"kind": "http_status", "values": [200]},
    )
    spec = FlowSpec(
        flow_id=f"required-is-contract-{required}",
        steps=[step],
        capabilities=[FlowCapability(
            name="submit",
            kind="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    report = validate_flow_spec(prepared)

    expected_required = ["申请标题"] if required else []
    assert prepared.capabilities[0].input_schema["required"] == expected_required
    assert report["api_preview"]["required"] == expected_required
    assert report["passed"] is True
    assert report["errors"] == []




def test_system_generated_runtime_value_is_compiled_and_not_exposed():
    step = FlowStep(
        step_id="submit",
        method="POST",
        path="/api/submit",
        url="/api/submit",
        body_source='{"nonce":"recorded"}',
        params=[ParamField(
            path="nonce",
            key="nonce",
            value="recorded",
            category="runtime_var",
            source_kind="system_generated",
            source={"kind": "system_generated", "strategy": "uuid"},
            exposed_to_user=False,
        )],
    )

    api_request, errors = flow_spec_module._flow_step_to_api_step(step)
    assert errors == []
    assert api_request is not None
    assert api_request["params"] == []
    assert {item["kind"] for item in api_request["system_values"]} == {"uuid"}
    dry = asyncio.run(execute_api_request(api_request, {}, send=False))
    assert dry["ok"] is True
    assert dry["body"]["nonce"] != "recorded"


def test_duplicate_param_does_not_merge_foreign_enum_domains():
    leave = ParamField(
        path="type", key="请假类型", label="请假类型", value="2", type="enum",
        source_kind="page_enum", source={"enum_confirmed": True},
        enum_options=[{"label": "病假", "value": 1}, {"label": "事假", "value": 2}],
        enum_value_map={"病假": 1, "事假": 2}, confidence=0.95,
        description="页面枚举选项：病假=1、事假=2",
    )
    department = ParamField(
        path="type", key="请假类型", label="请假类型", value="2", type="enum",
        source_kind="api_option", source={"source_url": "/system/dept/list"},
        enum_options=[{"label": "研发部", "value": 103}, {"label": "市场部", "value": 104}],
        enum_value_map={"研发部": 103, "市场部": 104}, confidence=0.7,
    )
    spec = FlowSpec(flow_id="enum-domain", steps=[FlowStep(step_id="submit", method="POST", params=[leave, department])])

    refreshed = refresh_review_items(spec)
    param = refreshed.steps[0].params[0]

    assert param.enum_value_map == {"病假": 1, "事假": 2}
    assert "研发部" not in (param.description or "")


def test_publish_canonicalizes_submit_alias_and_all_relations_atomically():
    step = FlowStep(
        step_id="submit-step", method="POST", path="/submit", body_source='{"reason":"x"}',
        params=[ParamField(path="reason", key="原因", value="x", category="user_param", exposed_to_user=True)],
    )
    spec = FlowSpec(
        flow_id="canonical-capability", steps=[step],
        capabilities=[FlowCapability(
            capability_id="write-cap", name="submit_batch", kind="submit",
            nodes=[{"id": "call", "type": "call", "step_id": "submit-step"}],
        )],
        capability_relations=[CapabilityRelation(
            from_capability="query_status", to_capability="submit_batch", type="caller_decision",
        )],
        goal={"capabilities": ["query_status", "submit_batch"]},
    )

    prepared = prepare_flow_spec_for_publish(spec)

    assert prepared.capabilities[0].name == "submit"
    assert prepared.capability_relations[0].to_capability == "submit"
    assert prepared.goal["capabilities"] == ["submit"]


def test_leave_range_is_single_submit_but_grounded_missing_day_flow_is_batch():
    leave_query = FlowStep(
        step_id="leave-query", method="GET",
        url="/leave/page?startDate=2026-07-01&endDate=2026-07-11",
        response_json={"data": {"list": [{"startDate": "2026-07-01", "endDate": "2026-07-11"}]}},
        source_meta={"role": "business_get", "confidence": 0.95},
    )
    leave_submit = FlowStep(
        step_id="leave-submit", method="POST", path="/leave/submit", body_source='{"startDate":"2026-07-01","endDate":"2026-07-11","reason":"x"}',
        params=[
            ParamField(path="startDate", key="开始日期", value="2026-07-01", type="date", category="user_param", exposed_to_user=True),
            ParamField(path="endDate", key="结束日期", value="2026-07-11", type="date", category="user_param", exposed_to_user=True),
        ],
    )
    leave_caps = build_default_flow_capabilities(FlowSpec(flow_id="leave", steps=[leave_query, leave_submit]))
    assert next(cap for cap in leave_caps if cap.kind in {"submit", "submit_batch"}).kind == "submit"

    daily_query = FlowStep(
        step_id="daily-query", method="GET", path="/daily/missing",
        response_json={"data": {"missingDates": ["2026-07-11", "2026-07-12"]}},
        source_meta={"role": "business_get", "confidence": 0.95},
    )
    daily_submit = FlowStep(
        step_id="daily-submit", method="POST", path="/daily/submit", body_source='{"reportDate":"2026-07-11","workContent":"x"}',
        params=[
            ParamField(path="reportDate", key="日报日期", value="2026-07-11", type="date", category="user_param", exposed_to_user=True),
            ParamField(path="workContent", key="工作内容", value="x", category="user_param", exposed_to_user=True),
        ],
    )
    daily_caps = build_default_flow_capabilities(FlowSpec(flow_id="daily", steps=[daily_query, daily_submit]))
    batch = next(cap for cap in daily_caps if cap.kind in {"submit", "submit_batch"})
    assert batch.kind == "submit_batch"
    assert batch.input_schema["properties"]["entries"]["type"] == "array"


def test_sync_upgrades_default_query_step_to_richer_captured_search_fact():
    default = FlowStep(
        step_id="query", method="GET", url="/leave/page?pageNo=1&pageSize=10",
        path="/leave/page?pageNo=1&pageSize=10",
        params=[
            ParamField(path="query.pageNo", key="pageNo", value="1"),
            ParamField(path="query.pageSize", key="pageSize", value="10"),
        ],
        source_meta={"request_id": "req-default", "request_index": 1, "role": "business_get"},
    )
    spec = FlowSpec.model_validate({
        "flow_id": "upgrade-query",
        "steps": [default.model_dump()],
        "request_facts": {
            "requests": [
                {"request_id": "req-default", "request_index": 1, "method": "GET", "url": "/leave/page?pageNo=1&pageSize=10"},
                {
                    "request_id": "req-search", "request_index": 2, "method": "GET", "url": "/leave/page",
                    "query": {"type": ["1"], "startDate": ["2026-07-01"], "endDate": ["2026-07-11"], "pageNo": ["1"], "pageSize": ["10"]},
                    "response_json": {"data": {"list": [], "total": 0}},
                },
            ],
            "analysis": {
                "req-default": {"request_id": "req-default", "role": "business_get", "confidence": 0.9},
                "req-search": {"request_id": "req-search", "role": "business_get", "confidence": 0.95},
            },
        },
    })

    synced = sync_flow_spec_models(spec)
    step = synced.steps[0]

    assert (step.source_meta or {})["request_id"] == "req-search"
    assert "startDate=2026-07-01" in step.url
    assert {p.path for p in step.params} >= {"query.type", "query.startDate", "query.endDate"}


def test_planner_foreach_alone_does_not_promote_single_leave_submit_to_batch():
    submit = FlowStep(
        step_id="submit", method="POST", path="/leave/submit",
        body_source='[{"type":1,"approverId":2}]',
        params=[
            ParamField(path="[0].type", key="请假类型", type="enum", category="user_param", exposed_to_user=True),
            ParamField(path="[0].approverId", key="审批人", type="enum", category="user_param", exposed_to_user=True),
        ],
    )
    cap = FlowCapability(
        name="submit_batch", kind="submit_batch", updated_by="planner",
        nodes=[{"id": "foreach", "type": "foreach", "items": "input.entries", "steps": [
            {"id": "call", "type": "call", "step_id": "submit"},
        ]}],
        input_schema={"type": "object", "properties": {"entries": {"type": "array", "items": {"type": "object"}}}},
    )
    spec = FlowSpec(flow_id="single-leave", steps=[submit], capabilities=[cap])

    repaired = flow_spec_module._repair_generated_capability_contracts(spec)

    assert repaired.capabilities[0].kind == "submit"
    assert repaired.capabilities[0].name == "submit"
    report = flow_spec_module._capability_validation_report(repaired)
    assert not any("只有审批/路由字段" in message for message in report["errors"])


def test_single_submit_prunes_stale_entries_relations_without_inventing_decision_relation():
    query = FlowCapability(
        capability_id="query-cap", name="query_status", kind="query_status",
        nodes=_call_nodes(["query"]), output_schema={"type": "object", "properties": {"records": {"type": "array"}}},
    )
    submit = FlowCapability(
        capability_id="submit-cap", name="submit", kind="submit",
        nodes=_call_nodes(["submit"]), input_schema={"type": "object", "properties": {"原因": {"type": "string"}}},
    )
    spec = FlowSpec(
        flow_id="stale-relations",
        steps=[
            FlowStep(step_id="query", method="GET", path="/records", response_json={"data": {"list": []}}),
            FlowStep(step_id="submit", method="POST", path="/submit", body_source='{"reason":"x"}', params=[
                ParamField(path="reason", key="原因", value="x", category="user_param", exposed_to_user=True),
            ]),
        ],
        capabilities=[query, submit],
        capability_relations=[
            CapabilityRelation(type="external_transform", mode="external_transform", from_capability="query_status", from_output="data.list", to_capability="submit", to_input="entries"),
            CapabilityRelation(type="external_transform", mode="external_transform", from_capability="query_status", from_output="records", to_capability="submit", to_input="entries"),
        ],
    )

    prepared = prepare_flow_spec_for_publish(spec)

    assert prepared.capability_relations == []


def test_capability_interface_reorder_updates_flat_calls_even_with_return_node():
    spec = FlowSpec(
        flow_id="order",
        steps=[
            FlowStep(step_id="a", method="GET", path="/a"),
            FlowStep(step_id="b", method="POST", path="/b"),
        ],
        capabilities=[FlowCapability(
            name="submit",
            nodes=[
                {"id": "call_a", "type": "call", "step_id": "a"},
                {"id": "call_b", "type": "call", "step_id": "b"},
                {"id": "return_b", "type": "return", "from": "b"},
            ],
        )],
    )

    updated = apply_flow_edits(spec, [{
        "op": "reorder_capability_steps",
        "capability_index": 0,
        "step_ids": ["b", "a"],
    }])

    calls = [node["step_id"] for node in updated.capabilities[0].nodes if node.get("type") == "call"]
    assert calls == ["b", "a"]


def test_generated_capabilities_repair_wrong_option_query_batch_and_stale_output():
    option = FlowStep(
        step_id="tenant-options",
        method="GET",
        path="/admin-api/system/tenant/simple-list",
        semantic_role="read_option",
        response_json={"data": [{"id": 1, "name": "默认租户", "status": 0}]},
    )
    submit = FlowStep(
        step_id="leave-submit",
        method="POST",
        path="/admin-api/oa/duty-leave/submit-process",
        body_source='{"type":"2","reason":"年假"}',
        params=[
            ParamField(
                path="type", key="请假类型", type="enum", value="2", required=True,
                enum_options=[{"label": "病假", "value": "2"}, {"label": "事假", "value": "3"}],
            ),
            ParamField(path="reason", key="原因", value="年假", required=True),
        ],
        response_json={"code": 0, "data": "ok"},
    )
    spec = FlowSpec(
        flow_id="repair-generated",
        steps=[option, submit],
        capabilities=[
            FlowCapability(
                name="query_status", kind="query_status",
                nodes=[{"id": "call_option", "type": "call", "step_id": "tenant-options"}],
                output_mapping=[{"step_id": "old-step", "response_path": "response"}],
                evidence=[{"kind": "read_step", "step_id": "tenant-options"}],
                confirmed=True,
            ),
            FlowCapability(
                name="submit_batch", title="批量提交业务申请", kind="submit_batch",
                nodes=[{
                    "id": "foreach_entries", "type": "foreach", "items": "input.entries",
                    "steps": [{"id": "call_submit", "type": "call", "step_id": "leave-submit"}],
                }],
                output_mapping=[{"step_id": "old-step", "response_path": "response"}],
                evidence=[{"kind": "write_steps", "step_ids": ["leave-submit"]}],
                confirmed=True,
            ),
        ],
    )

    repaired = flow_spec_module._repair_generated_capability_contracts(spec)
    repaired = flow_spec_module._sync_capability_io_schemas(repaired)

    assert [cap.name for cap in repaired.capabilities] == ["submit"]
    cap = repaired.capabilities[0]
    assert cap.kind == "submit"
    assert not any(node.get("type") == "foreach" for node in flow_spec_module._iter_capability_nodes(cap.nodes))
    assert cap.output_mapping == [{
        "kind": "final_response",
        "name": "result",
        "step_id": "leave-submit",
        "response_path": "response",
    }]
    assert cap.input_schema["properties"]["请假类型"]["enum"] == ["病假", "事假"]
    report_text = "\n".join([
        *validate_flow_spec(repaired).get("errors", []),
        *validate_flow_spec(repaired).get("warnings", []),
    ])
    assert "output_mapping" not in report_text
    assert "foreach" not in report_text
    assert "tenant-options" not in report_text


def test_repair_mode_fixes_warning_only_batch_nodes_and_dangling_relations():
    submit = FlowStep(
        step_id="submit",
        method="POST",
        path="/leave/submit-process",
        body_source='{"type":"2","reason":"年假","startTime":1,"endTime":2}',
        params=[
            ParamField(
                path="type", key="类型", value="2", type="enum", required=True,
                source_kind="manual_enum", enum_options=[{"label": "病假", "value": "2"}],
            ),
            ParamField(path="reason", key="原因", value="年假", required=True),
            ParamField(path="startTime", key="start_time", value="1", required=True),
            ParamField(path="endTime", key="end_time", value="2", required=True),
        ],
        response_json={"code": 0},
        success_rule={"path": "code", "equals": 0},
    )
    spec = FlowSpec(
        flow_id="warning-only-repair",
        steps=[submit],
        capabilities=[FlowCapability(
            name="submit_batch2",
            title="批量提交业务申请",
            kind="submit_batch",
            nodes=[
                {
                    "id": "foreach_entries", "type": "foreach", "items": "input.entries",
                    "steps": [{"id": "call_submit", "type": "call", "step_id": "submit"}],
                },
                {"id": "map_date", "type": "map", "source": "item.date", "target": "submit.[0].date"},
                {"id": "has_entries", "type": "condition", "condition": "input.entries.length > 0", "then": []},
            ],
            input_schema={"type": "object", "properties": {"entries": {"type": "array"}}},
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
            confirmed=True,
            confidence=0.95,
            updated_by="planner",
        )],
        capability_relations=[CapabilityRelation(
            relation_id="dangling",
            type="output_to_input",
            from_capability="missing_query",
            from_output="result",
            to_capability="submit_batch2",
            to_input="entries",
        )],
        meta={"capability_model": {"status": "ready"}},
    )
    before = validate_flow_spec(spec)
    assert before["passed"] is True
    assert any("没有批量接口事实" in item for item in before["suggestions"])

    fixed = asyncio.run(apply_recording_agent_submission(spec, submission={"ops": []}, mode="repair"))

    assert len(fixed.capabilities) == 1
    cap = fixed.capabilities[0]
    assert cap.kind == "submit"
    node_types = [node.get("type") for node in flow_spec_module._iter_capability_nodes(cap.nodes)]
    assert "foreach" not in node_types
    assert "map" not in node_types
    assert "condition" not in node_types
    assert fixed.capability_relations == []
    after_text = "\n".join([*validate_flow_spec(fixed)["errors"], *validate_flow_spec(fixed)["warnings"]])
    assert "foreach_entries" not in after_text
    assert "map_date" not in after_text
    assert "has_entries" not in after_text


def test_process_variables_date_span_is_computed_and_hidden_from_skill_inputs():
    approval = FlowStep(
        step_id="approval",
        method="GET",
        path="/approval-detail",
        url="/approval-detail",
        params=[ParamField(
            path="query.processVariablesStr",
            key="processVariablesStr",
            value='{"day":9}',
            required=True,
        )],
    )
    submit = FlowStep(
        step_id="submit",
        method="POST",
        path="/leave/submit",
        body_source='{"startTime":1782835200000,"endTime":1783612800000}',
        params=[
            ParamField(path="startTime", key="start_time", value="1782835200000", type="datetime", required=True),
            ParamField(path="endTime", key="end_time", value="1783612800000", type="datetime", required=True),
        ],
    )
    spec = FlowSpec(flow_id="computed-days", steps=[approval, submit])

    flow_spec_module._infer_computed_runtime_fields(spec)
    param = approval.params[0]
    assert param.category == "runtime_var"
    assert param.source_kind == "computed"
    assert param.exposed_to_user is False
    assert "processVariablesStr" not in flow_spec_module.flow_spec_user_params(spec)

    api_request, errors = flow_spec_module._flow_step_to_api_step(approval)
    assert errors == []
    dry = asyncio.run(execute_api_request(api_request, {
        "start_time": "1782835200000",
        "end_time": "1783612800000",
    }, send=False))
    assert dry["ok"] is True
    assert dry["query"]["processVariablesStr"] == '{"day":9}'


def test_rebuild_dependency_promotes_unique_internal_id_constant_to_upstream_response():
    source = FlowStep(
        step_id="definition",
        method="GET",
        path="/process-definition/get",
        response_json={"data": {"id": "oa_leave:4:abc"}},
    )
    target = FlowStep(
        step_id="approval",
        method="GET",
        path="/approval-detail",
        params=[ParamField(
            path="query.processDefinitionId",
            key="processDefinitionId",
            value="oa_leave:4:abc",
            category="system_const",
            source_kind="constant",
            exposed_to_user=False,
        )],
    )
    spec = FlowSpec(flow_id="id-link", steps=[source, target])

    assert flow_spec_module.rebuild_flow_dependencies(spec) == 1
    assert len(spec.links) == 1
    assert target.params[0].source_kind == "previous_response"
    assert target.params[0].source["step_id"] == "definition"


def test_sync_clears_select_pair_path_that_is_not_a_request_field():
    step = FlowStep(
        step_id="submit",
        method="POST",
        path="/submit",
        params=[ParamField(path="approverId", key="审批人", type="enum")],
        selects=[SelectBinding(
            param="审批人",
            path="approverId",
            source_url="/users/page",
            value_key="id",
            label_key="nickname",
            id_path="data.list",
        )],
    )
    spec = FlowSpec(flow_id="bad-id-path", steps=[step])

    sync_flow_spec_models(spec)

    assert step.selects[0].id_path is None


def test_manual_category_and_source_change_preserves_independently_managed_link():
    source = FlowStep(
        step_id="source", method="GET", url="/api/source", path="/api/source",
        response_json={"data": {"id": "LIVE"}},
    )
    target = FlowStep(
        step_id="target", method="POST", url="/api/submit", path="/api/submit",
        body_source='{"businessId":"OLD"}',
        params=[ParamField(
            path="businessId", key="业务编号", value="OLD", type="string",
            category="runtime_var", source_kind="previous_response",
            source={"kind": "previous_response", "link_id": "link-1"},
        )],
    )
    spec = FlowSpec(
        flow_id="unlink-on-manual-edit",
        steps=[source, target],
        links=[FlowLink(
            link_id="link-1", source_step_id="source", source_path="data.id",
            target_step_id="target", target_path="businessId", confirmed=True,
        )],
    )

    edited = apply_flow_edits(spec, [{
        "op": "update", "step_id": "target", "param_path": "businessId",
        "field": "category", "value": "user_param",
    }, {
        "op": "update", "step_id": "target", "param_path": "businessId",
        "field": "source_kind", "value": "user_input",
    }])

    param = edited.steps[1].params[0]
    assert [link.link_id for link in edited.links] == ["link-1"]
    assert param.category == "user_param"
    assert param.source_kind == "user_input"
    assert param.editable is True


def test_capability_input_schema_drops_deleted_fields():
    spec = _make_spec()
    spec.capabilities = [FlowCapability(
        name="submit", title="提交", kind="submit", nodes=_call_nodes(["step1"]), confirmed=True,
        input_schema={
            "type": "object",
            "properties": {"old": {"type": "string"}, "name": {"type": "number", "description": "旧说明"}},
            "required": ["old"],
        },
    )]

    edited = apply_flow_edits(spec, [{
        "op": "update", "step_id": "step1", "param_path": "form.name",
        "field": "required", "value": True,
    }])

    schema = edited.capabilities[0].input_schema
    assert set(schema["properties"]) == {"userId", "name"}
    assert "old" not in schema["required"]
    assert schema["properties"]["name"]["type"] == "string"


def test_unconfirmed_capabilities_are_generation_advice_not_publish_policy():
    spec = _make_spec()
    spec.capabilities = [FlowCapability(
        name="submit", title="提交", kind="submit", nodes=_call_nodes(["step1"]), confirmed=False,
    )]

    report = validate_flow_spec(spec)

    assert not any("未确认的公开能力" in message for message in report["errors"])
    assert any("未确认的公开能力" in message for message in report["suggestions"])
    assert not any(
        item.get("code") in {"capability_unconfirmed", "unconfirmed_public_capability"}
        for items in report["issue_groups"].values() for item in items
    )


def test_confirmed_capability_contract_change_is_advice_not_publish_policy():
    spec = FlowSpec(
        title="请假申请",
        business_description="提交失败时报告接口错误且不自动重试。",
        steps=[FlowStep(
            step_id="submit", method="POST", path="/api/leave/submit",
            body_source='{"reason":"事假"}',
            params=[ParamField(
                path="reason", key="请假原因", label="请假原因", value="事假",
                required=True,
                category="user_param", source_kind="user_input", exposed_to_user=True,
            )],
            success_rule={"path": "code", "equals": 0},
        )],
        capabilities=[FlowCapability(
            capability_id="submit-cap", name="submit", title="提交请假申请", kind="submit",
            nodes=[
                {"id": "call_submit", "type": "call", "step_id": "submit"},
                {"id": "return_result", "type": "return", "from": "submit", "path": "response"},
            ],
            confirmed=True, requires_human_confirm=False,
        )],
    )
    spec = prepare_flow_spec_for_publish(spec)
    cap = spec.capabilities[0]
    cap.confirmation_hash = flow_spec_module._capability_confirmation_hash(spec, cap)
    assert not any("确认后合同已变化" in error for error in validate_flow_spec(spec)["errors"])

    spec.steps[0].params[0].label = "事由"
    spec.steps[0].params[0].key = "事由"
    report = validate_flow_spec(spec)

    assert not any("确认后合同已变化" in error for error in report["errors"])
    assert any("确认后合同已变化" in item for item in report["suggestions"])


def test_generated_placeholder_output_name_is_normalized_to_stable_result():
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="submit", method="POST", path="/api/submit",
            body_source="{}", response_json={"code": 0, "data": {"id": "1"}},
        )],
        capabilities=[FlowCapability(
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            output_mapping=[{
                "kind": "final_response", "name": "output_1",
                "step_id": "submit", "response_path": "response",
            }],
        )],
    )

    repaired = flow_spec_module._repair_generated_capability_contracts(spec)

    assert repaired.capabilities[0].output_mapping[0]["name"] == "result"


def test_publish_preparation_strips_actionable_placeholder_field_name_atomically():
    placeholder = "请输入撤回原因"
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="withdraw",
            method="DELETE",
            path="/api/process/withdraw",
            body_source='{"reason":"测试原因"}',
            sample_inputs={placeholder: "测试原因"},
            params=[ParamField(
                path="reason",
                key=placeholder,
                label=placeholder,
                value="测试原因",
                required=True,
                category="user_param",
                source_kind="user_input",
            )],
        )],
        capabilities=[FlowCapability(
            name="withdraw_request",
            kind="submit",
            nodes=[{"id": "call_withdraw", "type": "call", "step_id": "withdraw"}],
            output_mapping=[{
                "kind": "final_response",
                "name": "result",
                "step_id": "withdraw",
                "response_path": "response",
            }],
        )],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    param = prepared.steps[0].params[0]
    api_request, errors = flow_spec_to_api_request(prepared)

    assert errors == []
    assert param.key == "撤回原因"
    assert param.label == "撤回原因"
    assert prepared.steps[0].sample_inputs == {"撤回原因": "测试原因"}
    assert set(prepared.capabilities[0].input_schema["properties"]) == {"撤回原因"}
    assert api_request is not None
    assert "请输入撤回原因" not in api_request["params"]
    assert not any(
        finding.get("kind") == "placeholder_name"
        for finding in collect_repair_findings(api_request)
    )


def test_manual_placeholder_name_remains_locatable_nonblocking_and_stays_ignored():
    placeholder = "请输入撤回原因"
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="withdraw",
            method="DELETE",
            path="/api/process/withdraw",
            body_source='{"reason":"测试原因"}',
            response_json={"code": 0},
            params=[ParamField(
                path="reason",
                key=placeholder,
                label=placeholder,
                value="测试原因",
                required=True,
                category="user_param",
                source_kind="user_input",
                locked=True,
                name_source="manual",
            )],
        )],
        capabilities=[FlowCapability(
            name="withdraw_request",
            kind="submit",
            nodes=[{"id": "call_withdraw", "type": "call", "step_id": "withdraw"}],
            output_mapping=[{
                "kind": "final_response",
                "name": "result",
                "step_id": "withdraw",
                "response_path": "response",
            }],
        )],
    )

    current = refresh_review_items(spec)
    review = next(item for item in current.review_items if item.type == "compiled_placeholder_name")
    report = validate_flow_spec(current)
    issue = next(
        item for item in report["issue_groups"]["field"]
        if item.get("code") == "placeholder_name"
    )

    assert current.steps[0].params[0].key == placeholder
    assert report["passed"] is True
    assert issue["blocking"] is False
    assert issue["ignorable"] is True
    assert issue["review_id"] == review.id
    assert issue["target"] == {
        "kind": "param",
        "step_id": "withdraw",
        "path": "reason",
        "key": placeholder,
    }

    ignored = apply_flow_edits(current, [{
        "op": "resolve_review",
        "review_id": review.id,
        "resolved": True,
    }])
    prepared = prepare_flow_spec_for_publish(ignored)
    repeated = validate_flow_spec(prepared)

    assert next(item for item in repeated["review_items"] if item["id"] == review.id)["resolved"] is True
    assert not any(
        item.get("code") == "placeholder_name"
        for items in repeated["issue_groups"].values()
        for item in items
    )


def test_no_response_final_response_mapping_builds_consistent_object_output_contract():
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="withdraw",
            method="DELETE",
            path="/api/process/withdraw",
            body_source='{"id":"one"}',
            params=[ParamField(
                path="id",
                key="业务编号",
                value="one",
                category="user_param",
                source_kind="user_input",
            )],
        )],
        capabilities=[FlowCapability(
            name="withdraw_request",
            kind="submit",
            nodes=[{"id": "call_withdraw", "type": "call", "step_id": "withdraw"}],
            output_mapping=[{
                "kind": "final_response",
                "name": "result",
                "step_id": "withdraw",
                "response_path": "response",
            }],
        )],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    capability = prepared.capabilities[0]
    api_request, errors = flow_spec_to_api_request(prepared)

    assert errors == []
    assert capability.output_schema["properties"] == {"result": {
        "description": "接口原始响应；录制未捕获可推导的响应结构",
        "x-dano-untyped-response": True,
    }}
    assert [(field.key, field.type) for field in capability.outputs] == [("result", "unknown")]
    assert api_request is not None
    assert collect_capability_findings(api_request) == []


def test_compiled_capability_output_issue_targets_capability_io_not_request_field():
    spec = FlowSpec(capabilities=[FlowCapability(
        name="withdraw_request",
        kind="submit",
        nodes=_call_nodes(["withdraw"]),
    )])
    groups = flow_spec_module._compiled_contract_issue_groups(
        spec,
        {"steps": [{"step_id": "withdraw"}]},
        [{
            "kind": "capability_output_schema_missing",
            "capability": "withdraw_request",
            "field": "result",
            "detail": "result 未进入 output_schema",
        }],
    )

    issue = groups["capability"][0]
    assert issue["target"] == {
        "kind": "capability_output",
        "capability": "withdraw_request",
        "field": "result",
    }
    assert issue["review_id"]
    assert issue["issue_id"] == f"review:{issue['review_id']}"


def test_select_source_is_hydrated_from_captured_post_read_contract():
    spec = _make_spec()
    spec.request_facts = flow_spec_module.RequestFacts.model_validate({
        "requests": [{
            "request_id": "options-1",
            "request_index": 4,
            "method": "POST",
            "url": "https://oa.example.test/api/options/query",
            "path": "/api/options/query",
            "content_type": "application/json",
            "post_data": '{"pageNo":1}',
            "response_json": {"data": [{"id": "A", "name": "选项A"}]},
        }],
        "analysis": {
            "options-1": {
                "request_id": "options-1", "role": "read_option", "keep": True,
                "reason": "列表查询", "confidence": 0.95,
            },
        },
    })

    edited = apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "step1",
        "field": "selects",
        "value": [{
            "param": "userId", "path": "form.userId",
            "source_url": "https://oa.example.test/api/options/query",
            "value_key": "id", "label_key": "name",
        }],
    }])

    binding = edited.steps[0].selects[0]
    assert binding.source_method == "POST"
    assert binding.source_role == "read_option"
    assert binding.source_request_id == "options-1"
    assert binding.source_body == '{"pageNo":1}'


def _request_fact_entry(**overrides):
    entry = {
        "request_index": 7,
        "request_id": "req-7",
        "sequence": 7,
        "method": "GET",
        "url": "https://oa.example.com/api/status?id=PO-1",
        "path": "/api/status",
        "role": "business_get",
        "keep": True,
        "reason": "状态查询会被能力引用",
        "confidence": 0.96,
        "state": "captured",
        "response_status": 200,
        "response_json": {"code": 0, "data": {"status": "pending", "date": "2026-05-12"}},
        "response_schema": {"type": "object"},
    }
    entry.update(overrides)
    return entry


# ── Param 编辑 ──
def test_edit_key():
    spec = _make_spec()
    new = apply_flow_edits(spec, [{"op": "update", "step_id": "step1",
                                   "param_path": "form.userId", "field": "key", "value": "newUserId"}])
    assert spec.steps[0].params[0].key == "userId"
    assert new.steps[0].params[0].key == "newUserId"
    assert new.steps[0].params[0].name_source == "manual"
    assert new.steps[0].params[0].locked is True
    assert new.meta["current_version"] == 1
    assert new.meta["versions"][0]["action"] == "flow_edit"


def test_update_param_falls_back_to_key_when_path_is_stale():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="step1",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            params=[ParamField(path="body.type", key="type", label="请假类型", value="2", type="number")],
        )],
    )

    new = apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "step1",
        "param_path": "type",
        "param_key": "type",
        "param_label": "请假类型",
        "field": "type",
        "value": "enum",
    }])

    assert new.steps[0].params[0].type == "enum"


def test_bind_option_source_updates_param_and_select_binding():
    spec = FlowSpec(
        flow_id="f",
        steps=[
            FlowStep(
                step_id="dict",
                method="GET",
                url="/api/dict/type",
                path="/api/dict/type",
                response_json={"data": [{"label": "病假", "value": "1"}]},
            ),
            FlowStep(
                step_id="submit",
                method="POST",
                url="/api/leave",
                path="/api/leave",
                params=[ParamField(path="type", key="类型", value="1", type="number")],
            ),
        ],
    )

    new = apply_flow_edits(spec, [{
        "op": "bind_option_source",
        "target_step": "submit",
        "target_path": "type",
        "source_step": "dict",
        "value_key": "value",
        "label_key": "label",
        "id_path": "type",
        "options": ["病假"],
        "option_map": {"病假": "1"},
    }])

    param = new.steps[1].params[0]
    assert param.type == "enum"
    assert param.wire_type == "number"
    assert param.source_kind == "api_option"
    assert param.enum_value_map == {"病假": "1"}
    assert param.locked is True
    assert {item.get("field") for item in param.evidence if item.get("source") == "manual_edit"} >= {
        "category", "source_kind", "source", "exposed_to_user",
    }
    assert new.steps[1].selects[0].source_url == "/api/dict/type"
    assert new.steps[1].selects[0].value_key == "value"
    assert new.steps[1].selects[0].label_key == "label"


def test_capability_loop_and_return_edits():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[FlowCapability(name="submit_batch", kind="submit", nodes=_call_nodes(["submit"]))],
    )

    new = apply_flow_edits(spec, [
        {"op": "set_loop_source", "capability_name": "submit_batch", "items": "input.entries"},
        {"op": "set_return_mapping", "capability_name": "submit_batch", "mapping": [{
            "kind": "final_response",
            "step_id": "submit",
            "response_path": "response",
        }]},
    ])

    cap = new.capabilities[0]
    assert cap.kind == "submit_batch"
    assert any(n.get("type") == "foreach" and n.get("items") == "input.entries" for n in cap.nodes)
    assert cap.output_mapping[0]["step_id"] == "submit"


def test_capability_scoped_patch_ops_update_fields_dependencies_nodes_and_relations():
    spec = FlowSpec(
        flow_id="f",
        steps=[
            FlowStep(
                step_id="query",
                method="GET",
                url="/api/query",
                path="/api/query",
                response_json={"data": {"missing_dates": ["2026-06-11"]}},
            ),
            FlowStep(
                step_id="submit",
                method="POST",
                url="/api/submit",
                path="/api/submit",
                params=[ParamField(path="[0].date", key="date", value="2026-06-11", type="date", required=True)],
                response_json={"code": 0},
            ),
        ],
        capabilities=[
            FlowCapability(
                name="query_status",
                kind="query_status",
                nodes=_call_nodes(["query"]),
                output_schema={"type": "object", "properties": {"missing_dates": {"type": "array"}}},
            ),
            FlowCapability(name="submit_batch", kind="submit_batch", nodes=_call_nodes(["submit"])),
        ],
    )

    new = apply_flow_edits(spec, [
        {"op": "upsert_input_field", "capability_name": "submit_batch", "field": {
            "key": "entries", "type": "array", "required": True, "confirmed": True,
        }},
        {"op": "upsert_request_field", "capability_name": "submit_batch", "field": {
            "step_id": "submit", "path": "[0].date", "key": "date", "type": "date",
            "source_kind": "loop_item", "exposed_to_caller": False, "confirmed": True,
        }},
        {"op": "bind_dependency", "capability_name": "submit_batch", "source": {
            "step_id": "query", "path": "data.missing_dates",
        }, "target": {
            "step_id": "submit", "path": "[0].date",
        }, "confidence": 0.91, "confirmed": True, "locked": True},
        {"op": "set_map", "capability_name": "submit_batch", "node": {
            "id": "map_entries", "source": "input.entries", "target": "var.entries",
        }},
        {"op": "set_condition", "capability_name": "submit_batch", "node": {
            "id": "has_entries", "condition": "input.entries.length > 0", "then": [],
        }},
        {"op": "set_output_mapping", "capability_name": "submit_batch", "mapping": [{
            "kind": "final_response", "step_id": "submit", "response_path": "response",
        }]},
        {"op": "set_capability_relation", "from_capability": "query_status", "from_output": "missing_dates",
         "to_capability": "submit_batch", "to_input": "entries", "confidence": 0.86},
    ])

    cap = next(c for c in new.capabilities if c.name == "submit_batch")
    assert cap.inputs[0].key == "entries"
    assert cap.request_fields[0].path == "[0].date"
    assert cap.dependencies[0].locked is True
    assert any(n.get("id") == "map_entries" for n in cap.nodes)
    assert any(n.get("id") == "has_entries" for n in cap.nodes)
    assert cap.output_mapping[0]["step_id"] == "submit"
    assert len(new.links) == 1
    assert new.links[0].confirmed is True
    assert new.capability_relations[0].from_capability == "query_status"


def test_capability_validator_checks_condition_and_map_refs():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            params=[ParamField(path="body.date", key="date", value="2026-06-11", type="date", required=True)],
        )],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            input_schema={"type": "object", "properties": {"entries": {"type": "array"}}},
            nodes=[
                {"id": "call_submit", "type": "call", "step_id": "submit"},
                {"id": "bad_condition", "type": "condition", "condition": "input.missing.length > 0", "then": []},
                {"id": "bad_map", "type": "map", "source": "input.unknown", "target": "submit.nope"},
            ],
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
        )],
    )

    report = validate_flow_spec(spec)

    cap_report = report["capability_validation"]
    text = "\n".join([*(cap_report.get("errors") or []), *(cap_report.get("warnings") or [])])
    assert "引用的输入 `missing` 不存在" in text
    assert "来源 `input.unknown` 不存在" not in text


def test_batch_map_top_level_input_is_rewritten_to_loop_item():
    """批量 Schema 只公开 entries 时，旧 Planner 的 input.<字段> 必须迁移为 item.<字段>。"""
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            path="/api/work-hours/batch",
            source_meta={"batch": True},
            params=[ParamField(
                path="[0].projectId", key="项目ID", value="P-1", required=True,
                category="user_param", source_kind="user_input", exposed_to_user=True,
            )],
        )],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=[
                {"id": "foreach_entries", "type": "foreach", "items": "input.entries", "steps": [
                    {"id": "call_submit", "type": "call", "step_id": "submit"},
                ]},
                {"id": "map_project", "type": "map", "source": "input.项目ID", "target": "submit.[0].projectId"},
            ],
        )],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    maps = [node for node in prepared.capabilities[0].nodes if node.get("type") == "map"]

    assert maps[0]["source"] == "item.项目ID"
    assert not any("map 节点 `map_project` 来源" in error for error in validate_flow_spec(prepared)["errors"])


def test_duplicate_caller_field_names_are_disambiguated_without_splitting_shared_inputs():
    first = FlowStep(
        step_id="lookup",
        method="GET",
        path="/api/project",
        params=[ParamField(path="query.projectId", key="项目ID", value="P-1", category="user_param", source_kind="user_input")],
    )
    second = FlowStep(
        step_id="submit",
        method="POST",
        path="/api/submit",
        params=[
            ParamField(path="body.projectId", key="项目ID", value="P-1", category="user_param", source_kind="user_input"),
            ParamField(path="body.parentProjectId", key="项目ID", value="P-2", category="user_param", source_kind="user_input"),
        ],
    )
    spec = FlowSpec(
        steps=[first, second],
        capabilities=[FlowCapability(name="submit_project", kind="submit", nodes=_call_nodes(["lookup", "submit"]))],
    )

    normalized = flow_spec_module._sync_capability_io_schemas(spec)
    keys = [param.key for step in normalized.steps for param in step.params]
    assert keys.count("项目ID") == 2  # 同一 projectId 跨接口共享调用参数
    assert "项目ID#2" in keys
    renamed = normalized.steps[1].params[1]
    assert renamed.source["original_key"] == "项目ID"
    assert normalized.steps[1].sample_inputs["项目ID#2"] == "P-2"
    assert set(normalized.capabilities[0].input_schema["properties"]) == {"项目ID", "项目ID#2"}


def test_flow_operation_report_explains_noop_and_changes():
    before = _make_spec()
    unchanged = before.model_copy(deep=True)
    internal_only = before.model_copy(deep=True)
    internal_only.request_facts.diagnostics.append({"type": "console", "message": "derived audit"})
    capability_only = before.model_copy(deep=True)
    capability_only.capabilities = [FlowCapability(
        name="submit_request",
        title="仅修改能力名称",
        nodes=_call_nodes([before.steps[0].step_id]),
    )]
    changed = before.model_copy(deep=True)
    changed.steps[0].params[0].key = "申请人ID"
    changed.steps[0].params[0].default_value = "new-default"

    noop = flow_operation_report(before, unchanged, operation="plan")
    internal = flow_operation_report(before, internal_only, operation="plan")
    capability_delta = flow_operation_report(before, capability_only, operation="plan")
    delta = flow_operation_report(before, changed, operation="plan")

    assert noop["changed"] is False
    assert noop["summary"]
    assert internal["changed"] is False
    assert capability_delta["changes"]["capabilities"] == 1
    assert capability_delta["field_changes"] == []
    assert delta["changed"] is True
    assert delta["changes"]["fields"] == 1
    assert delta["field_changes"][0]["path"] == changed.steps[0].params[0].path
    assert set(delta["field_changes"][0]["axes"]) == {"name", "default"}
    assert delta["summary"] == "实际修改：字段1项"
    assert noop["summary"] == "未修改任何字段、能力或关联"


def _mixed_stale_repair_ops() -> list[dict]:
    return [
            {"op": "rename_field", "step_id": "query", "path": "query.status", "label": "状态"},
            {"op": "rename_field", "step_id": "query", "path": "query.key", "label": "关键词"},
        ]


def test_auto_fix_skips_stale_field_patch_and_keeps_valid_suggestions():
    spec = FlowSpec(
        flow_id="stale-patch",
        steps=[FlowStep(
            step_id="query",
            method="POST",
            path="/api/items/search",
            params=[ParamField(path="query.key", key="key", value="hotel")],
        )],
        capabilities=[FlowCapability(
            name="search_items",
            kind="submit",
            nodes=_call_nodes(["query"]),
        )],
    )

    fixed = asyncio.run(auto_fix_flow_spec(
        spec,
        repair_ops=_mixed_stale_repair_ops(),
        max_rounds=1,
        expand_requests=False,
    ))

    assert fixed.steps[0].params[0].key == "关键词"
    rejected = fixed.meta["auto_fix_history"][0]["rejected_edits"]
    assert rejected[0]["path"] == "query.status"
    assert "param not found" in rejected[0]["error"]


def _capability_repair_ops() -> list[dict]:
    return [
            {"op": "upsert_input_field", "capability": "submit_batch", "field": {
                "key": "entries", "type": "array", "required": True,
            }},
            {"op": "set_map", "capability": "submit_batch", "node": {
                "id": "map_entries", "source": "input.entries", "target": "var.entries",
            }},
            {"op": "set_output_mapping", "capability": "submit_batch", "mapping": [{
                "kind": "final_response", "step_id": "submit", "response_path": "response",
            }]},
        ]


def test_auto_fix_accepts_capability_scoped_pi_repair_ops():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[FlowCapability(name="submit_batch", kind="submit_batch", nodes=_call_nodes(["submit"]))],
    )

    fixed = asyncio.run(auto_fix_flow_spec(spec, repair_ops=_capability_repair_ops(), max_rounds=1))

    cap = fixed.capabilities[0]
    assert cap.inputs[0].key == "entries"
    assert any(n.get("id") == "map_entries" for n in cap.nodes)
    assert cap.output_mapping[0]["step_id"] == "submit"


def _capability_plan_submission() -> dict:
    return {"ops": [
            {"op": "upsert_capability", "capability": {
                "name": "submit_batch",
                "title": "批量提交日报",
                "kind": "submit_batch",
                "intent": "按调用方传入的 entries 批量提交日报",
            }},
            {"op": "add_request_to_capability", "capability": "submit_batch", "step_id": "submit"},
            {"op": "upsert_input_field", "capability": "submit_batch", "field": {
                "key": "entries", "type": "array", "required": True,
            }},
            {"op": "set_output_mapping", "capability": "submit_batch", "mapping": [{
                "kind": "final_response", "step_id": "submit", "response_path": "response",
            }]},
        ]}


def test_orchestrate_flow_capabilities_prefers_patch_ops_and_keeps_same_batch_ops():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/report/batch", path="/api/report/batch")],
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec, submission=_capability_plan_submission()))

    assert out.meta["capability_model"]["source"] == "pi_agent_patch"
    assert {cap.name for cap in out.capabilities} == {"submit_batch"}
    cap = out.capabilities[0]
    assert cap.kind == "submit_batch"
    assert cap.step_ids == ["submit"]
    assert cap.inputs[0].key == "entries"
    assert cap.output_mapping[0]["step_id"] == "submit"


def _false_batch_submission() -> dict:
    return {"ops": [
            {"op": "upsert_capability", "capability": {
                "name": "submit", "title": "批量提交用印申请", "kind": "submit_batch",
            }},
            {"op": "upsert_input_field", "capability": "submit", "field": {
                "key": "entries", "type": "array", "required": True, "confirmed": True,
            }},
            {"op": "set_loop_source", "capability": "submit", "items": "input.entries"},
            {"op": "set_condition", "capability": "submit", "node": {
                "id": "has_entries", "condition": "input.entries.length > 0", "then": [],
            }},
        ]}


def test_single_form_defaults_to_submit_even_when_planner_invents_confirmed_entries():
    spec = FlowSpec(
        flow_id="single-seal-form",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            path="/oa/seal-apply/submit-process",
            body_source='{"applyTitle":"测试","useInfo":"借章"}',
            params=[
                ParamField(path="applyTitle", key="申请标题", value="测试", required=True, category="user_param"),
                ParamField(path="useInfo", key="使用描述", value="借章", required=True, category="user_param"),
            ],
            response_json={"code": 0},
        )],
    )

    out = asyncio.run(orchestrate_flow_capabilities(
        spec,
        submission=_false_batch_submission(),
    ))
    cap = out.capabilities[0]
    messages = [*validate_flow_spec(out)["errors"], *validate_flow_spec(out)["warnings"]]

    assert cap.kind == "submit"
    assert cap.name == "submit"
    assert "entries" not in (cap.input_schema.get("properties") or {})
    assert not any(node.get("type") in {"foreach", "condition"}
                   for node in flow_spec_module._iter_capability_nodes(cap.nodes))
    assert not any("批量能力" in message or "entries" in message for message in messages)


def test_user_capability_kind_transition_is_atomic_and_later_param_type_edit_stays_valid():
    submit = FlowStep(
        step_id="submit",
        method="POST",
        path="/oa/seal-apply/submit-process",
        body_source='{"applyTitle":"测试","useTime":"2026-07-13","backTime":"2026-07-14","useInfo":"借章","remark":"无"}',
        params=[
            ParamField(path="applyTitle", key="申请标题", value="测试", required=True, category="user_param"),
            ParamField(path="useTime", key="使用日期", value="2026-07-13", required=True, category="user_param"),
            ParamField(path="backTime", key="归还日期", value="2026-07-14", required=True, category="user_param"),
            ParamField(path="useInfo", key="使用描述", value="借章", required=True, category="user_param"),
            ParamField(path="remark", key="备注", value="无", required=True, category="user_param"),
        ],
        response_json={"code": 0, "data": "ok"},
    )
    spec = FlowSpec(
        flow_id="atomic-kind",
        steps=[submit],
        capabilities=[FlowCapability(
            name="submit_batch",
            title="提交用印申请",
            kind="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            confirmed=True,
        )],
    )

    batch = apply_flow_edits(spec, [{
        "op": "update_capability",
        "capability_index": 0,
        "field": "kind",
        "value": "submit_batch",
    }])
    cap = batch.capabilities[0]
    entries = cap.input_schema["properties"]["entries"]

    assert cap.kind == "submit_batch"
    assert entries["type"] == "array"
    assert set(entries["items"]["properties"]) == {"申请标题", "使用日期", "归还日期", "使用描述", "备注"}
    assert any(node.get("type") == "foreach" and node.get("items") == "input.entries"
               for node in flow_spec_module._iter_capability_nodes(cap.nodes))
    assert not any(node.get("id") == "has_entries" for node in flow_spec_module._iter_capability_nodes(cap.nodes))

    changed = apply_flow_edits(batch, [{
        "op": "update",
        "step_id": "submit",
        "param_path": "useTime",
        "field": "type",
        "value": "datetime",
    }])
    api_request, build_errors = flow_spec_to_api_request(changed)
    findings = collect_capability_findings(api_request)
    messages = [*build_errors, *validate_flow_spec(changed)["errors"], *[
        str(item.get("detail") or "") for item in findings
    ]]

    assert changed.capabilities[0].input_schema["properties"]["entries"]["items"]["properties"]["使用日期"]["format"] == "date-time"
    assert not any("未进入 input_schema" in message for message in messages)
    assert not any("entries` 不存在" in message or "没有批量接口事实" in message for message in messages)

    ordinary = apply_flow_edits(changed, [{
        "op": "update_capability",
        "capability_index": 0,
        "field": "kind",
        "value": "submit",
    }])
    ordinary_cap = ordinary.capabilities[0]
    assert "entries" not in ordinary_cap.input_schema["properties"]
    assert not any(node.get("type") in {"foreach", "condition"}
                   for node in flow_spec_module._iter_capability_nodes(ordinary_cap.nodes))


def test_auto_fix_deterministically_adds_batch_loop_maps_and_output():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/report/batch",
            path="/api/report/batch",
            body_source='[{"date":"2026-06-11","content":"日报"}]',
            params=[
                ParamField(path="[0].date", key="date", value="2026-06-11", type="date", required=True),
                ParamField(path="[0].content", key="content", value="日报", type="string", required=True),
            ],
            response_json={"code": 0},
        )],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )

    fixed = asyncio.run(auto_fix_flow_spec(spec, repair_ops=[], max_rounds=1))
    cap = fixed.capabilities[0]

    assert any(n.get("type") == "foreach" for n in cap.nodes)
    assert any(n.get("type") == "map" and n.get("target") == "submit.[0].date" for n in cap.nodes)
    assert any(n.get("type") == "map" and n.get("target") == "submit.[0].content" for n in cap.nodes)
    assert cap.output_mapping and cap.output_mapping[0]["step_id"] == "submit"


def test_recording_v3_golden_matrix_fixtures_are_parseable():
    fixture_dir = Path(__file__).parent / "fixtures" / "recording_v3"
    expected = {
        "daily_report_flow_spec.json",
        "leave_flow_spec.json",
        "work_hours_flow_spec.json",
        "multi_enum_flow_spec.json",
        "multi_capability_flow_spec.json",
        "promoted_request_flow_spec.json",
    }
    names = {p.name for p in fixture_dir.glob("*.json")}

    assert expected <= names
    for name in expected:
        raw = json.loads((fixture_dir / name).read_text(encoding="utf-8"))
        spec = FlowSpec.model_validate(raw)
        assert spec.request_facts.requests or spec.steps
        assert spec.capabilities
        summary = flow_spec_canonical_summary(spec)
        assert summary["capabilities"]


def test_reject_dependency_records_lock_and_removes_link():
    link = FlowLink(
        link_id="l1",
        source_step_id="read",
        source_path="data.id",
        target_step_id="write",
        target_path="body.id",
    )
    spec = FlowSpec(
        flow_id="f",
        steps=[
            FlowStep(step_id="read", method="GET", url="/api/read", path="/api/read"),
            FlowStep(step_id="write", method="POST", url="/api/write", path="/api/write"),
        ],
        links=[link],
    )

    new = apply_flow_edits(spec, [{"op": "reject_dependency", "link_id": "l1"}])

    assert new.links == []
    rejected = new.meta.get("rejected_dependencies") or []
    assert rejected and rejected[0]["source_step_id"] == "read"


def test_add_request_step_is_idempotent_for_same_request_id():
    spec = FlowSpec(
        flow_id="f",
        request_facts=_request_facts_from_graph_fixture({"all_requests": [
            {
                "request_index": 1,
                "request_id": "r1",
                "method": "GET",
                "url": "/admin-api/bpm/process-definition/get?key=oa_duty_leave",
                "path": "/admin-api/bpm/process-definition/get",
                "role": "business_get",
                "confidence": 0.96,
                "response_status": 200,
                "response_json": {"data": {"id": "p1"}},
            },
                {
                    "request_index": 2,
                    "request_id": "r1",
                "method": "GET",
                "url": "/admin-api/bpm/process-definition/get?key=oa_duty_leave",
                "path": "/admin-api/bpm/process-definition/get",
                "role": "business_get",
                "confidence": 0.96,
                "response_status": 200,
                "response_json": {"data": {"id": "p1"}},
            },
        ]}),
    )

    one = apply_flow_edits(spec, [{"op": "add_request_step", "request_index": 1, "request_id": "r1"}])
    two = apply_flow_edits(one, [{"op": "add_request_step", "request_index": 2, "request_id": "r1"}])

    assert len(two.steps) == 1
    assert two.steps[0].path == "/admin-api/bpm/process-definition/get"


def test_request_facts_are_first_class_and_client_omits_legacy_graph():
    entry = _request_fact_entry(
        request_id="req-options",
        request_index=12,
        sequence=12,
        role="read_option",
        headers={"Authorization": "Bearer secret"},
        post_data={"scope": "all"},
        response_json={"token": "secret", "data": [{"label": "病假", "value": "2"}]},
    )
    spec = FlowSpec(
        flow_id="canonical-request-facts",
        request_facts=_request_facts_from_graph_fixture({
            "candidate_reads": [entry],
        }),
        meta={"request_graph": {"all_requests": [{"request_id": "must-not-project"}]}},
    )

    client = flow_spec_to_client(spec)

    assert [item["request_id"] for item in client["request_facts"]["requests"]] == ["req-options"]
    assert client["request_facts"]["analysis"]["req-options"]["role"] == "read_option"
    assert client["request_facts"]["requests"][0]["headers"]["Authorization"] == "***"
    assert client["request_facts"]["requests"][0]["post_data"] == ""
    assert client["request_facts"]["requests"][0]["response_json"]["token"] == "***"
    assert "request_graph" not in client["meta"]


def test_to_flow_spec_request_facts_filter_static_assets_and_keep_page_enums():
    captured = [
        {"index": 1, "request_id": "css-1", "method": "GET", "url": "/assets/app.css", "path": "/assets/app.css", "response_status": 200},
        {"index": 2, "request_id": "js-1", "method": "GET", "url": "/assets/app.js", "path": "/assets/app.js", "response_status": 200},
        {
            "index": 3,
            "request_id": "post-1",
            "method": "POST",
            "url": "/admin-api/oa/duty-leave/submit-process",
            "path": "/admin-api/oa/duty-leave/submit-process",
            "headers": {"content-type": "application/json"},
            "post_data": '{"type":"2","reason":"test"}',
            "response_status": 200,
            "response_json": {"code": 0},
            "trigger_action_id": "action_3",
            "trigger_event_id": "event_3",
            "action_delta_ms": 42,
            "causality_confidence": "high",
        },
    ]
    page_enums = {"type": {"options": [{"label": "病假", "value": "2"}], "option_map": {"病假": "2"}}}
    page_events = [{"event_id": "event_3", "kind": "action", "action_id": "action_3", "op": "submit"}]

    spec = to_flow_spec(captured, page_enum_options=page_enums, page_events=page_events)

    ids = {fact.request_id for fact in spec.request_facts.requests}
    assert "post-1" in ids
    assert "css-1" not in ids
    assert "js-1" not in ids
    assert spec.request_facts.option_sources
    page_source = spec.request_facts.option_sources[0]["options"]["type"]
    assert page_source["options"] == page_enums["type"]["options"]
    assert page_source["option_map"] == page_enums["type"]["option_map"]
    assert page_source["trace_status"]["submitted_value"] == "missing"
    assert spec.request_facts.page_events == page_events
    post_fact = next(fact for fact in spec.request_facts.requests if fact.request_id == "post-1")
    assert post_fact.trigger_action_id == "action_3"
    assert post_fact.trigger_action_id == "action_3"


def test_observer_anchor_is_required_for_auto_confirming_discovered_links():
    captured = [
        {
            "index": 1,
            "request_id": "detail",
            "method": "GET",
            "url": "/api/entity/detail?id=E-1",
            "query": {"id": ["E-1"]},
            "response_json": {"data": {"id": "ENTITY-1234"}},
            "status": 200,
            "trigger_action_id": "action_1",
            "trigger_locator": "role=button[name=查询]",
            "causality_confidence": "high",
        },
        {
            "index": 2,
            "request_id": "submit",
            "method": "POST",
            "url": "/api/entity/submit",
            "headers": {"content-type": "application/json"},
            "post_data": '{"entityId":"ENTITY-1234","reason":"ok"}',
            "response_json": {"ok": True},
            "status": 200,
            "trigger_action_id": "action_2",
            "trigger_locator": "role=button[name=提交]",
            "causality_confidence": "high",
        },
    ]
    events = [
        {"kind": "action", "action_id": "action_1", "op": "click"},
        {"kind": "action", "action_id": "action_2", "op": "submit"},
    ]

    anchored = to_flow_spec(captured, page_events=events)
    assert anchored.links and anchored.links[0].confirmed is True
    assert anchored.links[0].evidence["target_action_id"] == "action_2"

    unanchored_requests = [dict(item) for item in captured]
    for key in ("trigger_action_id", "trigger_locator", "causality_confidence"):
        unanchored_requests[1].pop(key, None)
    unanchored = to_flow_spec(unanchored_requests, page_events=events)
    assert unanchored.links and unanchored.links[0].confirmed is False
    assert "缺少操作因果锚点" in unanchored.links[0].reason


def test_valid_locked_capability_fields_and_dependencies_survive():
    spec = FlowSpec(
        flow_id="cap-scoped",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            body_source='{"reason":"补充材料"}',
            params=[ParamField(
                path="reason",
                key="reason",
                value="补充材料",
                type="string",
                required=True,
                category="user_param",
                source_kind="user_input",
                exposed_to_user=True,
            )],
        )],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )
    scoped_fields = [{
        "field_id": "manual-field-reason",
        "scope": "request_field",
        "display_name": "提交原因",
        "path": "reason",
        "key": "reason",
        "type": "string",
        "required": True,
        "step_id": "submit",
        "source_kind": "user_input",
        "locked": True,
    }]
    scoped_dependencies = [{
        "dependency_id": "manual-dep-status-to-submit",
        "type": "request_fact_to_field",
        "source": {"request_id": "req-status", "path": "data.status"},
        "target": {"step_id": "submit", "path": "reason"},
        "confidence": 0.88,
        "confirmed": True,
        "locked": True,
        "reason": "人工确认的能力内依赖",
    }]

    edited = apply_flow_edits(spec, [
        {
            "op": "upsert_request_field",
            "capability_name": "submit_batch",
            "field": scoped_fields[0],
        },
        {
            "op": "update_capability",
            "capability_name": "submit_batch",
            "field": "dependencies",
            "value": scoped_dependencies,
        },
    ])

    cap = edited.capabilities[0]
    assert cap.step_ids == ["submit"]
    assert cap.request_fields[0].field_id == "request_field:submit:reason"
    assert cap.request_fields[0].path == "reason"
    assert cap.dependencies[0].dependency_id == "manual-dep-status-to-submit"

    api_request, errors = flow_spec_to_api_request(edited)

    assert errors == []
    exported = api_request["capabilities"][0]
    assert exported["step_ids"] == ["submit"]
    assert exported["compiled_step_ids"] == ["submit"]
    assert exported["request_fields"][0]["field_id"] == "request_field:submit:reason"
    assert exported["dependencies"][0]["dependency_id"] == "manual-dep-status-to-submit"


def test_refresh_review_items_dedupes_duplicate_params_and_keeps_enum_options():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="s1",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            params=[
                ParamField(path="type", key="请假类型", value="2", type="number", source_kind="unknown"),
                ParamField(
                    path="body.type",
                    key="请假类型",
                    value="2",
                    type="enum",
                    source_kind="api_option",
                    enum_options=["病假", "事假"],
                    enum_value_map={"病假": "1", "事假": "2"},
                    confidence=0.9,
                ),
            ],
        )],
    )

    new = refresh_review_items(spec)

    assert len(new.steps[0].params) == 1
    assert new.steps[0].params[0].type == "enum"
    assert new.steps[0].params[0].enum_options == ["病假", "事假"]


def test_edit_key_syncs_label_select_and_exported_api_request():
    param = ParamField(
        path="form.systemName",
        key="oldName",
        label="oldName",
        value="系统A",
        type="string",
        required=True,
        category="user_param",
        source_kind="form_option",
    )
    step = FlowStep(
        step_id="step1",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"form":{"systemName":"系统A","systemId":"id-a"}}',
        params=[param],
        selects=[SelectBinding(
            param="staleAutoName",
            path="form.systemName",
            source_url="/api/options",
            value_key="id",
            label_key="name",
            id_path="form.systemId",
            options=[
                {"label": "系统A", "value": "id-a"},
                {"label": "系统B", "value": "id-b"},
            ],
            option_map={"系统A": "id-a", "系统B": "id-b"},
            enum_confirmed=True,
        )],
        sample_inputs={"oldName": "系统A"},
    )
    spec = FlowSpec(flow_id="f", steps=[step])

    new = apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "step1",
        "param_path": "form.systemName",
        "field": "key",
        "value": "应用系统名称",
    }])
    assert new.steps[0].params[0].label == "应用系统名称"
    assert new.steps[0].selects[0].param == "应用系统名称"

    apir, errors = flow_spec_to_api_request(new)

    assert errors == []
    assert apir["params"] == ["应用系统名称"]
    assert apir["sample_inputs"] == {"应用系统名称": "系统A"}
    assert apir["selects"][0]["param"] == "应用系统名称"


def test_edit_param_path_syncs_select_and_target_link():
    step1 = FlowStep(
        step_id="read",
        method="GET",
        url="/api/read",
        path="/api/read",
        response_json={"data": {"id": "A-1"}},
    )
    step2 = FlowStep(
        step_id="write",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"form":{"oldPath":"系统A","systemId":"id-a"}}',
        params=[ParamField(
            path="form.oldPath",
            key="系统名称",
            value="系统A",
            type="enum",
            category="user_param",
            source_kind="form_option",
        )],
        selects=[SelectBinding(
            param="系统名称",
            path="form.oldPath",
            source_url="/api/options",
            value_key="id",
            label_key="name",
            id_path="form.systemId",
        )],
    )
    spec = FlowSpec(
        flow_id="f",
        steps=[step1, step2],
        links=[FlowLink(
            link_id="l1",
            source_step_id="read",
            source_path="data.id",
            target_step_id="write",
            target_path="form.oldPath",
        )],
    )

    new = apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "write",
        "param_path": "form.oldPath",
        "field": "path",
        "value": "form.systemName",
    }])

    assert new.steps[1].params[0].path == "form.systemName"
    assert new.steps[1].selects[0].path == "form.systemName"
    assert new.links[0].target_path == "form.systemName"


def test_static_enum_options_on_param_are_exported_as_selects():
    param = ParamField(
        path="form.leaveType",
        key="请假类型",
        label="请假类型",
        value="事假",
        type="enum",
        required=True,
        category="user_param",
        source_kind="form_option",
        enum_options=["事假", "病假", "年假"],
    )
    step = FlowStep(
        step_id="step1",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"form":{"leaveType":"事假"}}',
        params=[param],
        sample_inputs={"请假类型": "事假"},
    )
    spec = FlowSpec(flow_id="f", steps=[step])

    apir, errors = flow_spec_to_api_request(spec)

    assert errors == []
    assert apir["params"] == ["请假类型"]
    assert apir["field_types"]["请假类型"] == "enum"
    assert apir["selects"][0]["param"] == "请假类型"
    assert apir["selects"][0]["options"] == ["事假", "病假", "年假"]
    assert apir["selects"][0]["enum_source"] == "manual"
    assert apir["selects"][0]["enum_confirmed"] is True


def test_update_select_binding_from_frontend_dicts_is_validated_and_exported():
    param = ParamField(
        path="form.approverId",
        key="审批人",
        label="审批人",
        value="张三",
        type="enum",
        required=True,
        category="user_param",
        source_kind="form_option",
    )
    step = FlowStep(
        step_id="step1",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"form":{"approverId":"115"}}',
        params=[param],
        sample_inputs={"审批人": "张三"},
    )
    spec = FlowSpec(flow_id="f", steps=[step])

    new = apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "step1",
        "field": "selects",
        "value": [{
            "param": "审批人",
            "path": "form.approverId",
            "source_url": "/admin-api/system/user/page?pageNo=1&pageSize=10",
            "value_key": "id",
            "label_key": "nickname",
            "options": ["张三", "李四"],
            "count": 2,
        }],
    }])

    assert isinstance(new.steps[0].selects[0], SelectBinding)
    assert new.steps[0].selects[0].value_key == "id"

    apir, errors = flow_spec_to_api_request(new)

    assert errors == []
    assert apir["selects"][0]["source_url"] == "/admin-api/system/user/page?pageNo=1&pageSize=10"
    assert apir["selects"][0]["value_key"] == "id"
    assert apir["selects"][0]["label_key"] == "nickname"
    assert apir["field_types"]["审批人"] == "enum"


def test_edit_required():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "param_path": "form.userId", "field": "required", "value": False}])
    assert new.steps[0].params[0].required is False


def test_edit_value():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "param_path": "form.userId", "field": "value", "value": "456"}])
    assert new.steps[0].params[0].value == "456"
    assert new.steps[0].params[0].default_value == "456"
    assert new.steps[0].sample_inputs["userId"] == "456"


def test_edit_type():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "param_path": "form.userId", "field": "type", "value": "number"}])
    assert new.steps[0].params[0].type == "number"


def test_edit_type_only_changes_type_and_preserves_recorded_enum_evidence():
    step = FlowStep(
        step_id="step1",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        params=[ParamField(
            path="form.type",
            key="类型",
            value="A",
            type="enum",
            category="user_param",
            source_kind="page_enum",
            reason="用户选择；页面枚举选项：类型A=A、类型B=B",
            description="业务类型；页面枚举选项：类型A=A、类型B=B",
            enum_options=[{"label": "类型A", "value": "A"}, {"label": "类型B", "value": "B"}],
            enum_value_map={"类型A": "A", "类型B": "B"},
        )],
        selects=[SelectBinding(
            param="类型",
            path="form.type",
            options=[{"label": "类型A", "value": "A"}],
            option_map={"类型A": "A"},
            enum_source="dom",
        )],
    )
    spec = FlowSpec(flow_id="f", steps=[step])

    new = apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "step1",
        "param_path": "form.type",
        "field": "type",
        "value": "string",
    }])

    param = new.steps[0].params[0]
    assert param.type == "string"
    assert param.source_kind == "page_enum"
    assert param.enum_options == [{"label": "类型A", "value": "A"}]
    assert param.enum_value_map == {"类型A": "A"}
    assert "页面枚举选项" in (param.reason or "")
    assert "业务类型" in (param.description or "")
    assert len(new.steps[0].selects) == 1


def test_manual_field_contract_is_not_reinferred_from_stale_select_binding():
    spec = FlowSpec(flow_id="manual-contract", steps=[FlowStep(
        step_id="submit", method="POST", url="/submit", path="/submit",
        params=[ParamField(
            path="type", key="请假类型", value="2", type="string",
            category="system_const", source_kind="constant", source={"kind": "constant"},
            reason="分页参数内部提交；枚举选项：模式一=1、模式二=2",
            description="分页配置；页面枚举选项：模式一=1、模式二=2",
            enum_options=[{"label": "模式一", "value": 1}],
            enum_value_map={"模式一": 1},
            locked=True,
            evidence=[{"source": "manual_edit", "field": "category", "value": "system_const"}],
        )],
        selects=[SelectBinding(
            param="请假类型", path="type", enum_source="dom",
            options=[{"label": "病假", "value": 2}], option_map={"病假": 2},
        )],
    )])

    synced = sync_flow_spec_models(spec)
    param = synced.steps[0].params[0]

    assert param.type == "string"
    assert param.category == "system_const"
    assert param.source_kind == "constant"
    assert param.enum_options is None
    assert param.enum_value_map is None
    assert param.reason == "分页参数内部提交"
    assert param.description == "分页配置"


def test_edit_type_to_enum_does_not_overwrite_category_or_source():
    new = apply_flow_edits(_make_spec(), [{
        "op": "update",
        "step_id": "step1",
        "param_path": "form.userId",
        "field": "type",
        "value": "enum",
    }])

    param = new.steps[0].params[0]
    assert param.type == "enum"
    assert param.source_kind == "unknown"


def test_manual_type_category_source_combination_survives_publish_sync():
    """The backend must persist an operator combination even when it looks unusual."""
    spec = FlowSpec(flow_id="manual-axis-combination", steps=[FlowStep(
        step_id="query",
        method="GET",
        url="/api/search?status=1",
        path="/api/search",
        params=[ParamField(
            path="query.status",
            key="状态",
            value="1",
            type="enum",
            category="user_param",
            source_kind="page_enum",
            enum_options=[{"label": "启用", "value": "1"}],
            enum_value_map={"启用": "1"},
        )],
        selects=[SelectBinding(
            param="状态",
            path="query.status",
            options=[{"label": "启用", "value": "1"}],
            option_map={"启用": "1"},
            enum_source="dom",
            enum_confirmed=True,
        )],
    )])

    edited = apply_flow_edits(spec, [
        {"op": "update", "step_id": "query", "param_path": "query.status",
         "field": "type", "value": "string", "actor": "user"},
        {"op": "update", "step_id": "query", "param_path": "query.status",
         "field": "category", "value": "runtime_var", "actor": "user"},
        {"op": "update", "step_id": "query", "param_path": "query.status",
         "field": "source_kind", "value": "user_input", "actor": "user"},
    ])
    prepared = prepare_flow_spec_for_publish(edited)
    param = prepared.steps[0].params[0]

    assert (param.type, param.category, param.source_kind) == (
        "string", "runtime_var", "user_input",
    )
    assert validate_flow_spec(edited)["passed"] is True


def test_add_param():
    new = apply_flow_edits(_make_spec(), [{"op": "add", "step_id": "step1", "param": {
        "path": "form.email", "key": "email", "value": "test@example.com",
        "type": "string", "required": False}}])
    assert len(new.steps[0].params) == 3
    assert new.steps[0].sample_inputs["email"] == "test@example.com"


def test_user_added_enum_runtime_unknown_survives_sync_and_publish():
    spec = FlowSpec(flow_id="add-manual-enum", steps=[FlowStep(
        step_id="query", method="GET", url="/api/search", path="/api/search",
    )])
    added = apply_flow_edits(spec, [{
        "op": "add",
        "step_id": "query",
        # actor omitted intentionally: workbench/UI additions default to user.
        "param": {
            "path": "query.status", "key": "状态", "value": "1",
            "type": "enum", "category": "runtime_var", "source_kind": "unknown",
            "source": {}, "exposed_to_user": False,
        },
    }])
    prepared = prepare_flow_spec_for_publish(added)
    param = prepared.steps[0].params[0]

    assert (param.type, param.category, param.source_kind, param.exposed_to_user) == (
        "enum", "runtime_var", "unknown", False,
    )
    assert param.locked is True
    assert {item.get("field") for item in param.evidence if item.get("source") == "manual_edit"} >= {
        "type", "category", "source_kind", "source", "exposed_to_user",
    }
    assert validate_flow_spec(added)["passed"] is True


def test_user_added_page_number_remains_string_user_input_after_publish_sync():
    spec = FlowSpec(flow_id="add-manual-page-number", steps=[FlowStep(
        step_id="query", method="GET", url="/api/search", path="/api/search",
    )])
    added = apply_flow_edits(spec, [{
        "op": "add", "step_id": "query", "param": {
            "path": "query.pageNo", "key": "页码", "value": "1",
            "type": "string", "category": "user_param", "source_kind": "user_input",
            "source": {"kind": "sample", "path": "query.pageNo"},
            "exposed_to_user": True,
        },
    }])
    prepared = prepare_flow_spec_for_publish(added)
    param = prepared.steps[0].params[0]
    api_request, errors = flow_spec_to_api_request(added)

    assert (param.type, param.category, param.source_kind, param.exposed_to_user) == (
        "string", "user_param", "user_input", True,
    )
    assert param.locked is True
    assert api_request is not None
    assert errors == []
    assert "页码" in api_request["params"]


@pytest.mark.parametrize("actor", ["planner", "repair"])
def test_automated_add_cannot_claim_manual_param_ownership(actor):
    spec = FlowSpec(flow_id=f"automated-add-{actor}", steps=[FlowStep(
        step_id="query", method="GET", url="/api/search", path="/api/search",
    )])
    added = apply_flow_edits(spec, [{
        "op": "add", "step_id": "query", "actor": actor, "param": {
            "path": "query.pageNo", "key": "pageNo", "value": "1",
            "type": "string", "category": "user_param", "source_kind": "user_input",
            "locked": True,
            "evidence": [{"source": "manual_edit", "field": "category", "value": "user_param"}],
        },
    }])
    param = added.steps[0].params[0]

    assert param.locked is False
    assert not any(item.get("source") == "manual_edit" for item in param.evidence)
    # Pagination has a safe default but remains caller-overridable.
    assert (param.category, param.source_kind) == ("user_param", "user_input")


def test_remove_param():
    spec = _make_spec()
    spec.steps[0].selects = [SelectBinding(param="name", path="form.name")]
    spec.steps[0].identity = [IdentityBinding(path="form.name", source="recorded")]
    spec.links = [FlowLink(
        link_id="name-link",
        source_step_id="step1",
        source_path="response.name",
        target_step_id="step1",
        target_path="form.name",
    )]

    new = apply_flow_edits(spec, [{"op": "remove", "step_id": "step1", "param_path": "form.name"}])

    assert len(new.steps[0].params) == 1
    assert "name" not in new.steps[0].sample_inputs
    assert new.steps[0].selects == []
    assert new.steps[0].identity == []
    assert new.links == []


def test_nonexistent_step_lists_available():
    """Bug 修复:step not found 错误含可用 step_id 列表,前端据此自动同步。"""
    spec = _make_spec()
    with pytest.raises(ValueError) as exc:
        apply_flow_edits(spec, [{"op": "update", "step_id": "nope", "field": "url", "value": "/x"}])
    msg = str(exc.value)
    assert "available:" in msg
    assert "step1" in msg


# ── Step 编辑 ──
def test_edit_url():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "field": "url", "value": "/api/v2/submit"}])
    assert new.steps[0].url == "/api/v2/submit"


def test_edit_method():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "field": "method", "value": "PUT"}])
    assert new.steps[0].method == "PUT"


def test_edit_headers():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "field": "headers", "value": {"X-Foo": "bar"}}])
    assert new.steps[0].headers == {"X-Foo": "bar"}


def test_edit_step_role_updates_source_meta_and_semantic_role():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "field": "role", "value": "submit_anchor"}])
    assert new.steps[0].source_meta["role"] == "submit_anchor"
    assert new.steps[0].semantic_role == "submit_anchor"


def test_update_flow_business_description():
    new = apply_flow_edits(_make_spec(), [{
        "op": "update_flow",
        "field": "business_description",
        "value": "人工修正说明",
    }])
    assert new.business_description == "人工修正说明"


# ── Reorder ──
def _three_step_spec():
    def _st(sid, p):
        return FlowStep(step_id=sid, name=sid, method="POST", url=p, path=p,
                        params=[ParamField(path="x", key="x", value="1", type="string", required=True)])
    return FlowSpec(flow_id="f", steps=[_st("A", "/a"), _st("B", "/b"), _st("C", "/c")])


def test_reorder_basic():
    spec = _three_step_spec()
    new = apply_flow_edits(spec, [{"op": "reorder_steps", "step_ids": ["C", "B", "A"]}])
    assert [s.step_id for s in new.steps] == ["C", "B", "A"]
    assert [s.step_id for s in spec.steps] == ["A", "B", "C"]


def test_reorder_missing_raises():
    spec = _three_step_spec()
    with pytest.raises(ValueError, match="reorder_steps"):
        apply_flow_edits(spec, [{"op": "reorder_steps", "step_ids": ["A", "B"]}])


def test_reorder_capabilities_basic():
    spec = _three_step_spec()
    spec.capabilities = [
        FlowCapability(name="query_status", kind="query_status", nodes=_call_nodes(["A"])),
        FlowCapability(name="submit_batch", kind="submit_batch", nodes=_call_nodes(["B"])),
    ]

    new = apply_flow_edits(spec, [{"op": "reorder_capabilities", "capability_names": ["submit_batch", "query_status"]}])

    assert [c.name for c in new.capabilities] == ["submit_batch", "query_status"]
    assert [c.name for c in spec.capabilities] == ["query_status", "submit_batch"]


def test_reorder_capabilities_missing_raises():
    spec = _three_step_spec()
    spec.capabilities = [
        FlowCapability(name="query_status", kind="query_status", nodes=_call_nodes(["A"])),
        FlowCapability(name="submit_batch", kind="submit_batch", nodes=_call_nodes(["B"])),
    ]

    with pytest.raises(ValueError, match="reorder_capabilities"):
        apply_flow_edits(spec, [{"op": "reorder_capabilities", "capability_names": ["query_status"]}])


def test_reorder_capabilities_by_capability_id_when_name_empty():
    spec = _three_step_spec()
    spec.capabilities = [
        FlowCapability(name="", capability_id="cap_a", kind="query_status", nodes=_call_nodes(["A"])),
        FlowCapability(name="", capability_id="cap_b", kind="submit_batch", nodes=_call_nodes(["B"])),
    ]

    new = apply_flow_edits(spec, [{"op": "reorder_capabilities", "capability_refs": ["cap_b", "cap_a"]}])

    assert [c.capability_id for c in new.capabilities] == ["cap_b", "cap_a"]


def test_remove_step_removes_related_links():
    spec = _three_step_spec()
    spec.links = [
        FlowLink(link_id="ab", source_step_id="A", source_path="data.x", target_step_id="B", target_path="x"),
        FlowLink(link_id="bc", source_step_id="B", source_path="data.y", target_step_id="C", target_path="x"),
    ]

    new = apply_flow_edits(spec, [{"op": "remove_step", "step_id": "B"}])

    assert [s.step_id for s in new.steps] == ["A", "C"]
    assert new.links == []
    assert [s.step_id for s in spec.steps] == ["A", "B", "C"]


def test_add_duplicate_link_merges_existing_link_instead_of_raising():
    spec = _three_step_spec()
    spec.links = [
        FlowLink(link_id="ab", source_step_id="A", source_path="data.key",
                 target_step_id="B", target_path="x", confirmed=False, confidence=0.3,
                 reason="old")
    ]

    new = apply_flow_edits(spec, [{"op": "add", "step_id": "A", "link": {
        "source_step_id": "A",
        "source_path": "data.key",
        "target_step_id": "B",
        "target_path": "x",
        "confirmed": True,
        "confidence": 0.9,
        "reason": "new",
    }}])

    assert len(new.links) == 1
    assert new.links[0].link_id == "ab"
    assert new.links[0].confirmed is True
    assert new.links[0].confidence == 0.9
    assert new.links[0].reason == "new"


def test_update_link_to_duplicate_merges_existing_link_instead_of_raising():
    spec = _three_step_spec()
    spec.links = [
        FlowLink(link_id="keep", source_step_id="A", source_path="data.key",
                 target_step_id="B", target_path="x", confirmed=False, confidence=0.2),
        FlowLink(link_id="edit", source_step_id="A", source_path="data.other",
                 target_step_id="B", target_path="x", confirmed=True, confidence=0.8),
    ]

    new = apply_flow_edits(spec, [{"op": "update", "link_id": "edit", "field": "source_path", "value": "data.key"}])

    assert len(new.links) == 1
    assert new.links[0].link_id == "keep"
    assert new.links[0].confirmed is True
    assert new.links[0].confidence == 0.8


def test_default_submit_capability_uses_dependency_closure_not_all_reads():
    option = FlowStep(
        step_id="opt", name="GET_simple-list", method="GET", url="/system/dict-data/simple-list", path="/system/dict-data/simple-list",
        source_meta={"role": "read_option"},
    )
    query = FlowStep(
        step_id="query", name="GET_process", method="GET", url="/bpm/process-definition/get", path="/bpm/process-definition/get",
        source_meta={"role": "business_get"}, response_json={"data": {"key": "oa_seal_apply"}},
    )
    submit = FlowStep(
        step_id="submit", name="POST_submit-process", method="POST", url="/oa/seal-apply/submit-process", path="/oa/seal-apply/submit-process",
        params=[ParamField(path="processDefKey", key="processDefKey", value="oa_seal_apply", type="string", category="runtime_var")],
    )
    spec = FlowSpec(flow_id="cap-prune", steps=[option, query, submit], links=[
        FlowLink(source_step_id="query", source_path="data.key", target_step_id="submit", target_path="processDefKey")
    ])

    caps = flow_spec_module.build_default_flow_capabilities(spec)
    submit_cap = next(c for c in caps if c.kind == "submit")

    assert submit_cap.step_ids == ["query", "submit"]
    assert "opt" not in submit_cap.step_ids


def test_default_capabilities_keep_independent_query_and_submit_separate():
    query_status = FlowStep(
        step_id="status",
        name="GET_page",
        method="GET",
        url="/admin-api/oa/daily-report/page",
        path="/admin-api/oa/daily-report/page",
        source_meta={"role": "business_get"},
        response_json={"data": {"list": [{"date": "2026-05-01"}]}},
    )
    preread = FlowStep(
        step_id="definition",
        name="GET_process",
        method="GET",
        url="/admin-api/bpm/process-definition/get",
        path="/admin-api/bpm/process-definition/get",
        source_meta={"role": "business_get"},
        response_json={"data": {"taskId": "T-1"}},
    )
    submit = FlowStep(
        step_id="submit",
        name="POST_submit",
        method="POST",
        url="/admin-api/oa/daily-report/submit",
        path="/admin-api/oa/daily-report/submit",
        params=[ParamField(path="taskId", key="taskId", value="T-1", type="string", category="runtime_var")],
    )
    spec = FlowSpec(flow_id="multi-cap", steps=[query_status, preread, submit], links=[
        FlowLink(source_step_id="definition", source_path="data.taskId", target_step_id="submit", target_path="taskId")
    ])

    caps = flow_spec_module.build_default_flow_capabilities(spec)
    by_kind = {c.kind: c for c in caps}

    assert "query_status" in by_kind
    assert "submit" in by_kind
    assert by_kind["query_status"].step_ids == ["status"]
    assert by_kind["submit"].step_ids == ["definition", "submit"]


def test_default_query_capabilities_split_distinct_commands_inside_one_domain():
    query = FlowStep(
        step_id="query",
        name="GET_page",
        method="GET",
        path="/admin-api/oa/work-hours/page",
        source_meta={
            "role": "business_get",
            "trigger_op": "click",
            "trigger_locator": "role=button[name=查询]",
            "causality_confidence": "high",
        },
        response_json={"data": {"list": [{"id": "row-1"}], "total": 1}},
    )
    detail = FlowStep(
        step_id="detail",
        name="GET_detail",
        method="GET",
        path="/admin-api/oa/work-hours/detail",
        source_meta={
            "role": "business_get",
            "trigger_op": "click",
            "trigger_locator": "role=button[name=查看详情]",
            "causality_confidence": "high",
        },
        response_json={"data": {"id": "row-1", "status": "approved"}},
    )

    caps = build_default_flow_capabilities(FlowSpec(flow_id="action-split", steps=[query, detail]))

    assert len(caps) == 2
    assert {tuple(cap.step_ids) for cap in caps} == {("query",), ("detail",)}


def test_default_query_capability_groups_multiple_requests_from_same_command():
    steps = [
        FlowStep(
            step_id="page",
            method="GET",
            path="/admin-api/oa/hotel-apply/page",
            source_meta={
                "role": "business_get",
                "trigger_op": "click",
                "trigger_locator": "role=button[name=查询]",
                "causality_confidence": "high",
            },
            response_json={"data": {"list": [{"id": "H-1"}], "total": 1}},
        ),
        FlowStep(
            step_id="statistics",
            method="GET",
            path="/admin-api/oa/hotel-apply/statistics",
            source_meta={
                "role": "business_get",
                "trigger_op": "click",
                "trigger_locator": "role=button[name=查询]",
                "causality_confidence": "high",
            },
            response_json={"data": {"pending": 1}},
        ),
    ]

    caps = build_default_flow_capabilities(FlowSpec(flow_id="same-command", steps=steps))

    assert len(caps) == 1
    assert caps[0].step_ids == ["page", "statistics"]


def test_saved_page_enum_repairs_internal_type_category_and_source_on_sync():
    spec = FlowSpec(
        flow_id="repair-page-enum",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            path="/admin-api/hotel/order/create",
            params=[ParamField(
                path="roomLevel",
                key="roomLevel",
                value="3",
                type="number",
                category="system_const",
                source_kind="constant",
            )],
        )],
        request_facts=RequestFacts(option_sources=[{
            "kind": "page_enum_options",
            "options": {
                    "房间等级": {
                        "field_key": "房间等级",
                        "field_aliases": ["roomLevel"],
                        "control_kind": "select",
                        "enum_source": "dom",
                        "mapping_complete": True,
                        "selected_label": "豪华",
                        "selected_value": 3,
                    "options": [
                        {"label": "普通", "value": 1},
                        {"label": "豪华", "value": 3},
                    ],
                },
            },
        }]),
    )

    fixed = sync_flow_spec_models(spec)
    param = fixed.steps[0].params[0]

    assert param.key == "房间等级"
    assert param.type == "enum"
    assert param.category == "user_param"
    assert param.source_kind == "page_enum"
    assert param.enum_value_map == {"普通": 1, "豪华": 3}
    assert fixed.steps[0].selects[0].enum_confirmed is True


def test_sync_capability_scoped_views_prunes_noisy_submit_steps():
    option = FlowStep(
        step_id="opt", name="GET_online-status", method="GET", url="/im/user/online-status", path="/im/user/online-status",
        source_meta={"role": "read_option"}, params=[ParamField(path="id", key="id", value="1")],
    )
    query = FlowStep(
        step_id="query", name="GET_process", method="GET", url="/bpm/process-definition/get", path="/bpm/process-definition/get",
        source_meta={"role": "business_get"}, response_json={"data": {"key": "oa_seal_apply"}},
    )
    submit = FlowStep(
        step_id="submit", name="POST_submit-process", method="POST", url="/oa/seal-apply/submit-process", path="/oa/seal-apply/submit-process",
        params=[ParamField(path="processDefKey", key="processDefKey", value="oa_seal_apply")],
    )
    spec = FlowSpec(
        flow_id="cap-sync-prune",
        steps=[option, query, submit],
        links=[FlowLink(source_step_id="query", source_path="data.key", target_step_id="submit", target_path="processDefKey")],
        capabilities=[FlowCapability(name="submit_batch", kind="submit_batch", nodes=_call_nodes(["opt", "query", "submit"]))],
    )

    synced = sync_flow_spec_models(spec)

    assert synced.capabilities[0].step_ids == ["query", "submit"]


def test_dedupe_steps_keeps_latest_repeated_read_step():
    def _get(sid, url):
        return FlowStep(
            step_id=sid,
            name=sid,
            method="GET",
            url=url,
            path=url,
            source_meta={"role": "business_get"},
            params=[ParamField(path="query.day", key="day", value="1", type="number")],
        )

    spec = FlowSpec(flow_id="f", steps=[
        _get("old1", "/admin-api/bpm/process-instance/get-approval-detail?processVariablesStr=null"),
        _get("old2", "/admin-api/bpm/process-instance/get-approval-detail?processVariablesStr=1"),
        FlowStep(step_id="submit", name="submit", method="POST", url="/admin-api/oa/duty-leave/submit-process",
                 path="/admin-api/oa/duty-leave/submit-process"),
    ])
    spec.links = [
        FlowLink(link_id="bad", source_step_id="old1", source_path="data.id", target_step_id="submit", target_path="x"),
        FlowLink(link_id="ok", source_step_id="old2", source_path="data.id", target_step_id="submit", target_path="y"),
    ]

    new = apply_flow_edits(spec, [{"op": "dedupe_steps"}])

    assert [s.step_id for s in new.steps] == ["old2", "submit"]
    assert [l.link_id for l in new.links] == ["ok"]
    assert new.meta["deduped_step_count"] == 1


# ── Link 编辑 ──
def _two_step_spec_with_link():
    s1 = FlowStep(step_id="A", name="A", method="POST", url="/a", path="/a",
                  params=[ParamField(path="x", key="x", value="1", type="string", required=True)])
    s2 = FlowStep(step_id="B", name="B", method="POST", url="/b", path="/b",
                  params=[ParamField(path="y", key="y", value="2", type="string", required=True)])
    lk = FlowLink(link_id="l1", source_step_id="A", source_path="data.x",
                  target_step_id="B", target_path="y", confirmed=False, confidence=0.85)
    return FlowSpec(flow_id="f", steps=[s1, s2], links=[lk])


def test_add_link():
    spec = _two_step_spec_with_link()
    new = apply_flow_edits(spec, [{"op": "add", "step_id": "A", "link": {
        "source_step_id": "B", "source_path": "data.z",
        "target_step_id": "A", "target_path": "x",
    }}])
    assert len(new.links) == 2
    param = new.steps[0].params[0]
    assert (param.type, param.category, param.source_kind) == (
        "string", "user_param", "previous_response",
    )
    assert param.need_human_confirm is True
    assert param.source["response_path"] == "data.z"
    assert param.locked is True


def test_add_link_bad_source_raises():
    spec = _two_step_spec_with_link()
    with pytest.raises(ValueError, match="source step not found"):
        apply_flow_edits(spec, [{"op": "add", "step_id": "A", "link": {
            "source_step_id": "NOPE", "source_path": "x",
            "target_step_id": "A", "target_path": "x",
        }}])


def test_update_link_confirmed():
    spec = _two_step_spec_with_link()
    new = apply_flow_edits(spec, [{"op": "update", "link_id": "l1",
                                   "field": "confirmed", "value": True}])
    assert new.links[0].confirmed is True
    param = new.steps[1].params[0]
    assert (param.type, param.category, param.source_kind) == (
        "string", "user_param", "previous_response",
    )
    assert param.need_human_confirm is False

    param_contract = param.model_dump()
    unconfirmed = apply_flow_edits(new, [{
        "op": "update", "actor": "user", "link_id": "l1",
        "field": "confirmed", "value": False,
    }])
    assert unconfirmed.links[0].confirmed is False
    assert unconfirmed.steps[1].params[0].model_dump() == param_contract


def test_update_param_path_updates_link_target_and_source_target_path():
    spec = _two_step_spec_with_link()
    spec.links[0].target_path = "body.y"
    spec.steps[1].params[0].source = {
        "kind": "previous_response",
        "step_id": "A",
        "response_path": "data.x",
        "target_path": "body.y",
    }

    new = apply_flow_edits(spec, [{"op": "update", "step_id": "B", "param_path": "y", "field": "path", "value": "z"}])

    assert new.steps[1].params[0].path == "z"
    assert new.links[0].target_path == "z"
    assert new.steps[1].params[0].source["target_path"] == "z"


def test_remove_link():
    spec = _two_step_spec_with_link()
    new = apply_flow_edits(spec, [{"op": "remove", "link_id": "l1"}])
    assert len(new.links) == 0
    assert new.meta.get("rejected_dependencies")


def test_remove_link_without_record_rejection_keeps_dependency_rebindable():
    spec = _two_step_spec_with_link()
    new = apply_flow_edits(spec, [{"op": "remove", "link_id": "l1", "record_rejection": False}])

    assert len(new.links) == 0
    assert not new.meta.get("rejected_dependencies")


def test_remove_link_resets_target_param_source():
    spec = _two_step_spec_with_link()
    synced = apply_flow_edits(spec, [{"op": "update", "link_id": "l1", "field": "confirmed", "value": True}])
    before = {p.path: p for p in synced.steps[1].params}["y"]
    # Explicit response binding changes the source contract only; category/type
    # remain independent operator axes.
    assert before.category == "user_param"
    assert before.source_kind == "previous_response"
    assert before.editable is True

    new = apply_flow_edits(synced, [{"op": "remove", "link_id": "l1", "reset_target": True}])
    after = {p.path: p for p in new.steps[1].params}["y"]
    assert len(new.links) == 0
    assert after.category == "user_param"
    assert after.source_kind == "user_input"
    assert after.editable is True
    assert after.exposed_to_user is True
    assert after.locked is True
    assert {item.get("field") for item in after.evidence if item.get("source") == "manual_edit"} >= {
        "category", "source_kind", "source", "exposed_to_user",
    }


def test_reset_param_source_removes_incoming_link():
    spec = _two_step_spec_with_link()
    synced = apply_flow_edits(spec, [{"op": "update", "link_id": "l1", "field": "confirmed", "value": True}])
    new = apply_flow_edits(synced, [{"op": "reset_param_source", "step_id": "B", "param_path": "y", "to": "user_input"}])
    assert new.links == []
    param = {p.path: p for p in new.steps[1].params}["y"]
    assert param.category == "user_param"
    assert param.source_kind == "user_input"
    assert param.locked is True


@pytest.mark.parametrize(("target_source", "expected"), [
    ("user_input", ("string", "user_param", "user_input", True)),
    ("constant", ("string", "system_const", "constant", False)),
])
def test_user_reset_page_number_source_survives_pagination_sync(target_source, expected):
    param = ParamField(
        path="query.pageNo", key="页码", value="1", type="string",
        category="runtime_var", source_kind="previous_response",
        source={"kind": "previous_response", "step_id": "source", "response_path": "data.page"},
        exposed_to_user=False,
    )
    spec = FlowSpec(flow_id="reset-page-number", steps=[
        FlowStep(step_id="source", method="GET", path="/api/config", response_json={"data": {"page": "1"}}),
        FlowStep(step_id="target", method="GET", path="/api/search", params=[param]),
    ], links=[FlowLink(
        link_id="page-link", source_step_id="source", source_path="data.page",
        target_step_id="target", target_path="query.pageNo", confirmed=True,
    )])
    # Simulate the exact editor state immediately before reset. Initial model
    # inference may normalize pagination values, but a source-only user action
    # must preserve the type visible in that editor state through publish sync.
    spec.steps[1].params[0].type = "string"

    reset = apply_flow_edits(spec, [{
        "op": "reset_param_source", "step_id": "target",
        "param_path": "query.pageNo", "to": target_source,
    }])
    prepared = prepare_flow_spec_for_publish(reset)
    result = prepared.steps[1].params[0]

    assert (result.type, result.category, result.source_kind, result.exposed_to_user) == expected
    assert result.locked is True
    assert not prepared.links


def test_user_confirmed_link_overrides_manual_source_then_reset_is_stable():
    source = FlowStep(
        step_id="source", method="GET", path="/api/source",
        response_json={"data": {"page": "2"}},
    )
    target_param = ParamField(
        path="query.pageNo", key="页码", value="1", type="string",
        category="user_param", source_kind="user_input",
        source={"kind": "sample", "path": "query.pageNo"},
        exposed_to_user=True, locked=True,
        evidence=[{"source": "manual_edit", "field": "source_kind", "value": "user_input"}],
    )
    target = FlowStep(
        step_id="target", method="GET", path="/api/search", params=[target_param],
    )
    spec = FlowSpec(flow_id="user-link-binding", steps=[source, target])

    bound = apply_flow_edits(spec, [{
        "op": "add", "actor": "user", "link": {
            "link_id": "manual-page-link",
            "source_step_id": "source", "source_path": "data.page",
            "target_step_id": "target", "target_path": "query.pageNo",
            "confirmed": True, "confidence": 1.0,
        },
    }])
    bound_param = bound.steps[1].params[0]
    assert (bound_param.type, bound_param.category) == ("string", "user_param")
    assert bound_param.source_kind == "previous_response"
    assert bound_param.source["link_id"] == "manual-page-link"
    assert bound_param.source["step_id"] == "source"
    assert bound_param.source["response_path"] == "data.page"

    removed = apply_flow_edits(bound, [{
        "op": "remove", "actor": "user", "link_id": "manual-page-link",
        "reset_target": True,
    }])
    removed_param = prepare_flow_spec_for_publish(removed).steps[1].params[0]
    assert (removed_param.type, removed_param.category, removed_param.source_kind) == (
        "string", "user_param", "user_input",
    )
    assert removed_param.locked is True


def test_add_candidate_step_promotes_request_fact():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="write", method="POST", url="/api/save", path="/api/save")],
        request_facts=_request_facts_from_graph_fixture({
                "selected_steps": [],
                "candidate_reads": [{
                    "request_index": 7,
                    "method": "GET",
                    "url": "https://oa.example.com/gsgl/xm/getProjectInfosByBt?keyword=abc",
                    "path": "/gsgl/xm/getProjectInfosByBt",
                    "role": "read_option",
                    "confidence": 0.88,
                    "response_status": 200,
                    "response_json": {"data": [{"xmId": "YF001", "xmName": "项目A"}]},
                }],
                "filtered_requests": [],
            }),
    )

    new = apply_flow_edits(spec, [{"op": "add_candidate_step", "request_index": 7}])

    assert len(new.steps) == 2
    promoted = new.steps[0]
    assert promoted.method == "GET"
    assert promoted.path == "/gsgl/xm/getProjectInfosByBt"
    assert any(p.path == "query.keyword" and p.value == "abc" for p in promoted.params)
    assert [p.path for p in promoted.params] == ["query.keyword"]
    assert promoted.source_meta["manual_added"] is True
    assert new.steps[1].step_id == "write"
    usage = new.request_facts.usage["idx:7"]
    assert usage.materialized_step_id == promoted.step_id
    assert usage.state == "materialized"


def test_add_request_step_keeps_same_path_distinct_request_ids():
    spec = FlowSpec(
        flow_id="f",
        request_facts=_request_facts_from_graph_fixture({"all_requests": [
            {
                "request_index": 1,
                "request_id": "req-a",
                "method": "GET",
                "url": "/api/detail?id=1",
                "path": "/api/detail",
                "role": "business_get",
                "confidence": 0.96,
                "response_json": {"data": {"id": 1}},
            },
            {
                "request_index": 2,
                "request_id": "req-b",
                "method": "GET",
                "url": "/api/detail?id=2",
                "path": "/api/detail",
                "role": "business_get",
                "confidence": 0.96,
                "response_json": {"data": {"id": 2}},
            },
        ]}),
    )

    new = apply_flow_edits(spec, [
        {"op": "add_request_step", "request_id": "req-a"},
        {"op": "add_request_step", "request_id": "req-b"},
    ])

    assert len(new.steps) == 2
    assert {s.source_meta.get("request_id") for s in new.steps} == {"req-a", "req-b"}


def test_promoted_read_is_ordered_before_write_and_rebuilds_dependency():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="write",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            content_type="application/json",
            body_source='[{"sbrq":"2026-05-12"}]',
            source_meta={"request_index": 20, "sequence": 20},
            params=[ParamField(
                path="[0].sbrq",
                key="startDate",
                value="2026-05-12",
                type="date",
                required=True,
                category="user_param",
                source_kind="user_input",
            )],
        )],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=[{"id": "call_1", "type": "call", "step_id": "write"}],
            confirmed=True,
            requires_human_confirm=False,
        )],
        request_facts=_request_facts_from_graph_fixture({"all_requests": [{
            "request_index": 10,
            "request_id": "req-date",
            "sequence": 10,
            "method": "GET",
            "url": "https://oa.example.com/api/missing-days?start=2026-05-01",
            "path": "/api/missing-days?start=2026-05-01",
            "role": "business_get",
            "confidence": 0.96,
            "response_status": 200,
            "response_json": {"code": 0, "data": {"startDate": "2026-05-12", "missingDates": ["2026-05-12"]}},
        }]})
    )

    new = apply_flow_edits(spec, [{
        "op": "add_capability_step",
        "capability_name": "submit_batch",
        "request_id": "req-date",
    }])

    assert [s.method for s in new.steps] == ["GET", "POST"]
    assert new.capabilities[0].step_ids == [new.steps[0].step_id, "write"]
    assert [n["step_id"] for n in new.capabilities[0].nodes if n.get("type") == "call"] == [new.steps[0].step_id, "write"]
    assert len(new.links) == 1
    link = new.links[0]
    assert link.source_step_id == new.steps[0].step_id
    assert link.target_step_id == "write"
    assert link.source_path == "data.startDate"
    assert link.target_path == "[0].sbrq"
    param = new.steps[1].params[0]
    assert param.source_kind == "previous_response"
    assert param.source["step_id"] == new.steps[0].step_id

    cap_report = validate_flow_spec(new)["capability_validation"]
    assert cap_report["checked_manual_requests"]
    assert cap_report["checked_manual_requests"][0]["step_id"] == new.steps[0].step_id


def test_add_capability_step_from_request_fact_updates_usage_index_and_refs():
    request_fact = _request_fact_entry(
        request_id="req-date",
        request_index=10,
        sequence=10,
        url="https://oa.example.com/api/missing-days?start=2026-05-01",
        path="/api/missing-days",
        response_json={"code": 0, "data": {"startDate": "2026-05-12"}},
    )
    spec = FlowSpec(
        flow_id="cap-request-fact-usage",
        steps=[FlowStep(
            step_id="write",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            source_meta={"request_index": 20, "sequence": 20},
            params=[ParamField(path="date", key="date", value="2026-05-12", type="date", required=True)],
        )],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=[{"id": "call_write", "type": "call", "step_id": "write"}],
        )],
        request_facts={
            "requests": [request_fact],
            "analysis": {
                "req-date": {
                    "request_id": "req-date",
                    "role": "business_get",
                    "keep": True,
                    "bucket": "candidate_reads",
                    "confidence": 0.96,
                    "reason": "补充缺失日期事实",
                }
            },
        },
    )

    new = apply_flow_edits(spec, [{
        "op": "add_capability_step",
        "capability_name": "submit_batch",
        "request_index": 10,
    }])

    promoted = next(s for s in new.steps if (s.source_meta or {}).get("request_id") == "req-date")
    cap = new.capabilities[0]
    assert promoted.step_id in cap.step_ids
    assert any(n.get("type") == "call" and n.get("step_id") == promoted.step_id for n in cap.nodes)
    assert any(ref.request_id == "req-date" and ref.step_id == promoted.step_id for ref in cap.request_refs)

    usage = new.request_facts.usage["req-date"]
    assert usage.state == "materialized"
    assert usage.materialized_step_id == promoted.step_id
    assert "submit_batch" in usage.used_by_capabilities


def test_remove_capability_step_clears_request_usage_without_deleting_step():
    request_fact = _request_fact_entry(request_id="req-date", request_index=10)
    spec = FlowSpec(
        flow_id="cap-request-fact-remove",
        steps=[FlowStep(
            step_id="read",
            method="GET",
            url="/api/status",
            path="/api/status",
            source_meta={"request_id": "req-date", "request_index": 10},
            response_json={"code": 0, "data": {"status": "pending"}},
        )],
        capabilities=[FlowCapability(
            name="query_status",
            kind="query_status",
            nodes=[{"id": "call_read", "type": "call", "step_id": "read"}],
        )],
        request_facts={
            "requests": [request_fact],
            "analysis": {"req-date": {"request_id": "req-date", "role": "business_get", "keep": True}},
        },
    )

    synced = sync_flow_spec_models(spec)
    assert "query_status" in synced.request_facts.usage["req-date"].used_by_capabilities

    edited = apply_flow_edits(synced, [{
        "op": "remove_capability_step",
        "capability_name": "query_status",
        "step_id": "read",
    }])

    assert any(step.step_id == "read" for step in edited.steps)
    assert edited.capabilities[0].step_ids == []
    assert edited.request_facts.usage["req-date"].used_by_capabilities == []
    assert edited.request_facts.usage["req-date"].materialized_step_id == "read"








def test_auto_fix_promotes_high_confidence_request_into_capability_closure():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="write",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            content_type="application/json",
            body_source='{"date":"2026-05-12"}',
            source_meta={"request_index": 20, "sequence": 20},
            params=[ParamField(path="date", key="date", value="2026-05-12", type="date", required=True)],
        )],
        request_facts=_request_facts_from_graph_fixture({"all_requests": [{
            "request_index": 10,
            "request_id": "req-date",
            "sequence": 10,
            "method": "GET",
            "url": "https://oa.example.com/api/missing-days?start=2026-05-01",
            "path": "/api/missing-days?start=2026-05-01",
            "role": "business_get",
            "confidence": 0.96,
            "response_status": 200,
            "response_json": {"code": 0, "data": {"startDate": "2026-05-12"}},
        }]})
    )

    fixed = asyncio.run(auto_fix_flow_spec(spec, repair_ops=[], max_rounds=2))

    assert len(fixed.steps) == 2
    assert fixed.steps[0].source_meta["request_id"] == "req-date"
    assert fixed.capabilities
    assert fixed.steps[0].step_id in fixed.capabilities[0].step_ids
    assert "auto_fix_history" in fixed.meta


def test_capability_scoped_view_uses_param_as_truth_over_locked_mirror():
    spec = FlowSpec(
        flow_id="cap-locked-field",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            params=[
                ParamField(path="type", key="type", label="type", value="2", type="number", required=True),
                ParamField(path="reason", key="reason", label="reason", value="事由", type="string", required=True),
            ],
        )],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            request_fields=[CapabilityField(
                field_id="request_field:submit:type",
                scope="request_field",
                display_name="请假类型",
                path="type",
                key="leave_type",
                type="enum",
                step_id="submit",
                locked=True,
                confirmed=True,
            )],
        )],
    )

    synced = sync_flow_spec_models(spec)
    fields = synced.capabilities[0].request_fields

    locked = next(f for f in fields if f.path == "type")
    assert locked.key == "type"
    assert locked.display_name == "type"
    assert locked.type == "number"
    assert locked.locked is False
    assert any(f.path == "reason" and f.key == "reason" for f in fields)


def test_auto_fix_routes_option_and_status_requests_to_matching_capabilities():
    spec = FlowSpec(
        flow_id="cap-route-requests",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            content_type="application/json",
            body_source='{"type":"2"}',
            source_meta={"request_index": 30, "sequence": 30},
            params=[ParamField(path="type", key="type", value="2", type="enum", required=True)],
        )],
        capabilities=[
            FlowCapability(
                name="list_options",
                kind="list_options",
                nodes=[],
                confirmed=False,
            ),
            FlowCapability(
                name="query_status",
                kind="query_status",
                nodes=[],
                confirmed=False,
            ),
            FlowCapability(
                name="submit_batch",
                kind="submit_batch",
                nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
                confirmed=False,
            ),
        ],
        request_facts=_request_facts_from_graph_fixture({"all_requests": [
            {
                "request_index": 10,
                "request_id": "req-options",
                "sequence": 10,
                "method": "GET",
                "url": "https://oa.example.com/api/options",
                "path": "/api/options",
                "role": "read_option",
                "confidence": 0.96,
                "response_status": 200,
                "response_json": {"data": [{"label": "病假", "value": "2"}]},
            },
            {
                "request_index": 20,
                "request_id": "req-status",
                "sequence": 20,
                "method": "GET",
                "url": "https://oa.example.com/api/status",
                "path": "/api/status",
                "role": "business_get",
                "confidence": 0.96,
                "response_status": 200,
                "response_json": {"data": {"status": "draft"}},
            },
        ]}),
    )

    fixed = asyncio.run(auto_fix_flow_spec(spec, repair_ops=[], max_rounds=2))
    by_name = {cap.name: cap for cap in fixed.capabilities}

    assert any((step.source_meta or {}).get("request_id") == "req-options" for step in fixed.steps)
    assert any((step.source_meta or {}).get("request_id") == "req-status" for step in fixed.steps)
    assert any(
        fixed_step.source_meta.get("request_id") == "req-options"
        for fixed_step in fixed.steps
        if fixed_step.step_id in by_name["list_options"].step_ids
    )
    assert any(
        fixed_step.source_meta.get("request_id") == "req-status"
        for fixed_step in fixed.steps
        if fixed_step.step_id in by_name["query_status"].step_ids
    )
    assert "submit" in by_name["submit"].step_ids


def test_recording_agent_submission_records_plan_history():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            content_type="application/json",
            body_source='{"date":"2026-05-12"}',
            params=[ParamField(path="date", key="date", value="2026-05-12", type="date", required=True)],
        )],
    )

    out = asyncio.run(apply_recording_agent_submission(
        spec, submission={"ops": []}, mode="plan", max_rounds=2,
    ))

    assert out.capabilities
    assert out.meta["recording_agent_session"]["mode"] == "plan"
    assert out.meta["recording_agent_session"]["rounds"]


def test_high_confidence_duplicate_path_is_treated_as_already_covered():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="read1",
            method="GET",
            url="/api/detail?id=1",
            path="/api/detail",
            source_meta={"request_id": "req-1"},
        )],
        request_facts=_request_facts_from_graph_fixture({"all_requests": [
            {
                "request_index": 1,
                "request_id": "req-1",
                "method": "GET",
                "url": "https://oa.example.com/api/detail?id=1",
                "path": "/api/detail",
                "role": "business_get",
                "confidence": 0.96,
            },
            {
                "request_index": 2,
                "request_id": "req-2",
                "method": "GET",
                "url": "https://oa.example.com/api/detail?id=2",
                "path": "/api/detail",
                "role": "business_get",
                "confidence": 0.96,
            },
        ]}),
    )

    cap_report = validate_flow_spec(spec)["capability_validation"]

    assert cap_report["unused_high_confidence_requests"] == []


def test_capability_validation_drops_stale_missing_node_step():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="query", method="GET", url="/api/query", path="/api/query")],
        capabilities=[FlowCapability(
            name="query_status",
            kind="query_status",
            nodes=[{"id": "bad_call", "type": "call", "step_id": "missing"}],
            confirmed=True,
            requires_human_confirm=False,
        )],
    )

    report = validate_flow_spec(spec)

    assert not any("missing" in x or "stale-request-id" in x for x in report["errors"])
    assert not any("未绑定有效接口步骤" in x for x in report["errors"])


def test_capability_validation_ignores_stale_scoped_copies_but_checks_active_output_mapping():
    spec = FlowSpec(
        flow_id="f",
        steps=[
            FlowStep(
                step_id="read",
                method="GET",
                url="/api/read",
                path="/api/read",
                response_json={"data": {"id": "u-1"}},
            ),
            FlowStep(
                step_id="write",
                method="POST",
                url="/api/write",
                path="/api/write",
                body_source='{"name":"alice","userId":"u-1"}',
                params=[
                    ParamField(path="name", key="name", value="alice", type="string", required=True),
                    ParamField(
                        path="userId",
                        key="userId",
                        value="u-1",
                        type="string",
                        required=True,
                        category="runtime_var",
                        source_kind="previous_response",
                        source={"step_id": "read", "path": "data.id"},
                    ),
                ],
                success_rule={"path": "code", "equals": 0},
            ),
        ],
        capabilities=[FlowCapability(
            name="submit_user",
            kind="submit",
            nodes=[
                {"id": "call_read", "type": "call", "step_id": "read"},
                {"id": "call_write", "type": "call", "step_id": "write"},
                {"id": "return_result", "type": "return", "from": "write", "path": "response"},
            ],
            fields=[
                CapabilityField(
                    field_id="request_field:write:missing",
                    scope="request_field",
                    path="missing",
                    key="missing",
                    step_id="write",
                    locked=True,
                )
            ],
            dependencies=[
                CapabilityDependency(
                    dependency_id="dep_bad_target",
                    source={"step_id": "read", "path": "data.id"},
                    target={"step_id": "write", "path": "missing"},
                    locked=True,
                )
            ],
            output_mapping=[{
                "kind": "final_response",
                "step_id": "outside",
                "response_path": "response",
            }],
            confirmed=True,
            requires_human_confirm=False,
        )],
    )

    report = validate_flow_spec(spec)
    cap_report = report["capability_validation"]

    assert report["passed"] is True
    assert "capability_internal" in cap_report
    assert "capability_relations" in cap_report
    assert "skill_level" in cap_report
    internal_codes = {
        item["code"]
        for cap in cap_report["capability_internal"]["capabilities"]
        for item in [*(cap.get("warnings") or []), *(cap.get("errors") or [])]
    }
    assert "capability_field_path_missing" not in internal_codes
    assert "capability_dependency_endpoint_missing" not in internal_codes
    assert "capability_output_mapping_uninterpretable" in internal_codes


def test_capability_validator_reports_deep_p1_field_and_loop_findings():
    submit = FlowStep(
        step_id="submit",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='[{"type":"2","date":"2026-05-12","content":"x"}]',
        params=[
            ParamField(path="[0].type", key="type", value="2", type="number", required=True),
            ParamField(path="[0].date", key="date", value="2026-05-12", type="date", required=True),
            ParamField(
                path="[0].content", key="content", value="x", type="string", required=True,
                category="runtime_var", source_kind="unknown", exposed_to_user=False,
            ),
        ],
    )
    spec = FlowSpec(
        flow_id="deep-validator",
        steps=[submit],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=[{
                "id": "foreach_entries",
                "type": "foreach",
                "items": "input.entries",
                "steps": [{"id": "call_submit", "type": "call", "step_id": "submit"}],
            }],
            input_schema={
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"date": {"type": "string"}},
                            "required": ["date"],
                        },
                    }
                },
                "required": ["entries"],
            },
            request_fields=[
                CapabilityField(
                    field_id="internal-type",
                    scope="request_field",
                    path="[0].type",
                    key="type",
                    type="number",
                    required=True,
                    step_id="submit",
                    source_kind="unknown",
                    exposed_to_caller=True,
                    locked=True,
                ),
                CapabilityField(
                    field_id="hidden-content",
                    scope="internal",
                    path="[0].content",
                    key="content",
                    type="string",
                    required=True,
                    step_id="submit",
                    source_kind="unknown",
                    exposed_to_caller=False,
                    locked=True,
                ),
            ],
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
            confirmed=False,
        )],
    )

    report = validate_flow_spec(spec)
    cap_report = report["capability_validation"]
    text = _report_text(report)

    assert "capability_internal_field_exposed" in text
    assert "capability_field_source_missing" in text
    assert "capability_loop_item_field_missing" not in text
    assert cap_report["capability_internal"]["passed"] is True


def test_confirmed_capability_blocks_enum_without_label_value_mapping():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            params=[ParamField(
                path="type",
                key="类型",
                value="2",
                type="enum",
                required=True,
                source_kind="manual_enum",
                enum_options=["1", "2", "3"],
            )],
            response_json={"code": 0},
        )],
        capabilities=[FlowCapability(
            name="submit_leave",
            kind="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
            confirmed=True,
            requires_human_confirm=False,
        )],
    )

    report = validate_flow_spec(spec)
    text = _report_text(report)

    assert report["passed"] is False
    assert "capability_enum_mapping_missing" in text


def test_confirmed_capability_blocks_required_internal_request_field_without_source():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            params=[ParamField(
                path="runtimeTicket", key="runtimeTicket", value="p1", type="string", required=True,
                category="runtime_var", source_kind="unknown", exposed_to_user=False,
            )],
            response_json={"code": 0},
        )],
        capabilities=[FlowCapability(
            name="submit_leave",
            kind="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            request_fields=[CapabilityField(
                field_id="proc",
                scope="request_field",
                step_id="submit",
                path="runtimeTicket",
                key="runtimeTicket",
                type="string",
                required=True,
                exposed_to_caller=False,
                source_kind="unknown",
                locked=True,
            )],
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
            confirmed=True,
            requires_human_confirm=False,
        )],
    )

    report = validate_flow_spec(spec)
    text = _report_text(report)

    assert report["passed"] is False
    assert "capability_field_source_missing" in text


def test_confirmation_records_operator_decision_without_model_veto():
    spec = FlowSpec(
        flow_id="confirm-preflight",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/submit",
            path="/submit",
            body_source='{"runtimeTicket":"recorded"}',
            params=[ParamField(
                path="runtimeTicket", key="runtimeTicket", value="recorded",
                category="runtime_var", source_kind="unknown", exposed_to_user=False,
            )],
            response_json={"code": 0},
        )],
        capabilities=[FlowCapability(
            name="submit",
            kind="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
        )],
    )

    confirmed = apply_flow_edits(spec, [{
        "op": "update_capability", "capability_name": "submit",
        "field": "confirmed", "value": True,
    }])

    assert confirmed.capabilities[0].confirmed is True
    assert confirmed.capabilities[0].requires_human_confirm is False


def test_capability_confidence_above_sixty_percent_is_auto_adopted():
    spec = FlowSpec(capabilities=[
        FlowCapability(name="manual", confidence=0.6, confirmed=False, requires_human_confirm=True),
        FlowCapability(name="automatic", confidence=0.61, confirmed=False, requires_human_confirm=True),
    ])

    result = flow_spec_module._auto_confirm_ready_capabilities(spec)

    assert result.capabilities[0].confirmed is False
    assert result.capabilities[1].confirmed is True
    assert result.capabilities[1].requires_human_confirm is False


def test_legacy_dual_required_fields_are_retained_as_extra_fields():
    field = CapabilityField.model_validate({
        "key": "申请标题", "required": True,
        "page_required": False, "required_source": "page",
    })

    payload = field.model_dump()
    assert payload["required"] is True
    assert payload["page_required"] is False
    assert payload["required_source"] == "page"


def test_scalar_type_controls_capability_schema_without_deleting_enum_evidence():
    spec = FlowSpec(
        flow_id="enum-scalar-contract",
        steps=[FlowStep(
            step_id="submit", method="POST", url="/submit", path="/submit",
            body_source='{"type":"A"}',
            params=[ParamField(
                path="type", key="类型", value="A", type="enum",
                source_kind="page_enum", category="user_param",
                enum_options=[{"label": "甲", "value": "A"}],
                enum_value_map={"甲": "A"},
            )],
            selects=[SelectBinding(param="类型", path="type", options=[{"label": "甲", "value": "A"}])],
        )],
        capabilities=[FlowCapability(
            name="submit", kind="submit", nodes=_call_nodes(["submit"]),
            input_schema={"type": "object", "properties": {
                "类型": {"type": "string", "enum": ["甲"], "x-options": ["甲"], "format": "name-ref"},
            }},
        )],
    )

    edited = apply_flow_edits(spec, [{
        "op": "update", "step_id": "submit", "param_path": "type",
        "field": "type", "value": "string",
    }])
    schema = edited.capabilities[0].input_schema["properties"]["类型"]

    assert schema["type"] == "string"
    assert not ({"enum", "x-options", "x-enum-value-map", "format"} & set(schema))
    assert len(edited.steps[0].selects) == 1


def test_enum_option_description_replaces_stale_snapshot_instead_of_appending():
    spec = FlowSpec(
        flow_id="enum-description-refresh",
        steps=[FlowStep(
            step_id="submit", method="POST", url="/submit", path="/submit",
            params=[ParamField(
                path="type", key="请假类型", value=2, type="enum",
                category="user_param", source_kind="page_enum",
                description="页面枚举选项：病假=2",
                enum_options=[{"label": "病假", "value": 2}],
            )],
            selects=[SelectBinding(
                param="请假类型", path="type", enum_source="dom",
                options=[
                    {"label": "病假", "value": 2},
                    {"label": "事假", "value": 1},
                    {"label": "年假", "value": 3},
                ],
            )],
        )],
    )

    synced = sync_flow_spec_models(spec)

    assert synced.steps[0].params[0].description == "页面枚举选项：病假=2、事假=1、年假=3"


def test_manual_business_query_membership_survives_all_sync_layers_without_lock_flag():
    spec = FlowSpec(
        flow_id="manual-query-membership",
        steps=[FlowStep(
            step_id="records", method="GET", url="/api/leave/page", path="/api/leave/page",
            source_meta={"role": "read_option", "request_id": "req-records", "request_index": 7},
            response_json={"data": {"list": [{"id": "1"}], "total": 1}},
        )],
        capabilities=[FlowCapability(name="query_status", kind="query_status")],
    )

    edited = apply_flow_edits(spec, [{
        "op": "add_capability_step",
        "capability_name": "query_status",
        "step_id": "records",
        "usage": "execute",
        "origin": "manual",
        # Legacy clients may still send this property; keep it as extra membership metadata.
        "pinned": True,
    }])
    prepared = prepare_flow_spec_for_publish(edited)

    capability = prepared.capabilities[0]
    assert capability.step_ids == ["records"]
    assert any(node.get("type") == "call" and node.get("step_id") == "records" for node in capability.nodes)
    assert capability.request_refs[0].usage == "execute"
    assert capability.request_refs[0].origin == "manual"
    assert capability.request_refs[0].confirmed is True
    assert capability.request_refs[0].model_dump().get("pinned") is True


def test_option_source_membership_is_visible_but_not_executed():
    spec = FlowSpec(
        flow_id="option-source-membership",
        steps=[FlowStep(
            step_id="options", method="GET", url="/api/dict/options", path="/api/dict/options",
            source_meta={"role": "read_option", "request_id": "req-options"},
            response_json={"data": [{"label": "甲", "value": 1}]},
        )],
        capabilities=[FlowCapability(name="submit", kind="submit")],
    )

    edited = apply_flow_edits(spec, [{
        "op": "add_capability_step", "capability_name": "submit", "step_id": "options",
        "usage": "option_source", "origin": "manual", "pinned": True,
    }])
    prepared = prepare_flow_spec_for_publish(edited)

    capability = prepared.capabilities[0]
    assert capability.step_ids == []
    assert not any(node.get("type") == "call" for node in capability.nodes)
    assert [(ref.step_id, ref.usage, ref.origin, ref.confirmed) for ref in capability.request_refs] == [
        ("options", "option_source", "manual", True),
    ]
    assert capability.request_refs[0].model_dump().get("pinned") is True


def test_default_query_capability_uses_business_records_not_process_configuration():
    spec = FlowSpec(
        flow_id="query-evidence",
        steps=[
            FlowStep(
                step_id="definition", method="GET",
                url="/admin-api/bpm/process-definition/get", path="/admin-api/bpm/process-definition/get",
                source_meta={"role": "business_get"}, response_json={"data": {"id": "definition-id"}},
            ),
            FlowStep(
                step_id="records", method="GET",
                url="/admin-api/oa/duty-leave/page", path="/admin-api/oa/duty-leave/page",
                source_meta={"role": "read_option"},
                response_json={"data": {"list": [{"id": "leave-1", "type": 2}], "total": 1}},
            ),
        ],
    )

    capabilities = build_default_flow_capabilities(spec)
    query = next(cap for cap in capabilities if cap.kind == "query_status")

    assert query.step_ids == ["records"]


def test_capability_schema_separates_enum_business_type_from_numeric_wire_type():
    spec = FlowSpec(
        flow_id="enum-wire-contract",
        steps=[FlowStep(
            step_id="submit", method="POST", url="/submit", path="/submit",
            params=[ParamField(
                path="type", key="请假类型", value=2, type="enum", wire_type="number",
                category="user_param", source_kind="page_enum",
                enum_options=[{"label": "病假", "value": 2}], enum_value_map={"病假": 2},
            )],
        )],
        capabilities=[FlowCapability(name="submit", kind="submit", nodes=_call_nodes(["submit"]))],
    )

    prepared, release = prepare_flow_release_candidate(spec)
    field = prepared.capabilities[0].input_schema["properties"]["请假类型"]

    assert field["type"] == "string"
    assert field["x-dano-business-type"] == "single_enum"
    assert field["x-dano-wire-type"] == "number"
    assert release["interface_inventory"][0]["step_ids"] == ["submit"]


def test_page_enum_truth_is_not_overwritten_by_business_record_list():
    spec = FlowSpec(
        flow_id="page-enum-priority",
        request_facts=RequestFacts(option_sources=[{
            "kind": "page_enum_options",
            "options": {
                    "type": {
                        "field_key": "type",
                        "field_aliases": ["type"],
                        "control_kind": "select",
                        "enum_source": "dom",
                        "mapping_complete": True,
                        "selected_label": "婚假",
                        "selected_value": 3,
                        "options": [
                        {"label": "病假", "value": 2},
                        {"label": "事假", "value": 1},
                        {"label": "婚假", "value": 3},
                    ],
                    "option_map": {"病假": 2, "事假": 1, "婚假": 3},
                },
            },
        }]),
        steps=[FlowStep(
            step_id="submit", method="POST", url="/submit", path="/submit",
            params=[ParamField(path="type", key="请假类型", value=3, type="enum", wire_type="number")],
            selects=[SelectBinding(
                param="请假类型", path="type",
                source_url="/api/leave/page", label_key="type", value_key="type",
                options=[{"label": "3", "value": 3}], option_map={"3": 3}, enum_source="api", enum_confirmed=True,
            )],
        )],
    )

    synced = sync_flow_spec_models(spec)
    param = synced.steps[0].params[0]

    assert param.source_kind == "page_enum"
    assert param.enum_value_map == {"病假": 2, "事假": 1, "婚假": 3}
    assert [item["label"] for item in param.enum_options] == ["病假", "事假", "婚假"]


def test_strict_skill_level_reports_advice_without_publish_veto():
    spec = FlowSpec(
        flow_id="f",
        meta={"publish_gate": True},
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            body_source="{}",
            response_json={"code": 0},
        )],
        capabilities=[FlowCapability(
            name="submit_form",
            kind="submit",
            nodes=[
                {"id": "call_submit", "type": "call", "step_id": "submit"},
                {"id": "return_result", "type": "return", "from": "submit", "path": "response"},
            ],
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
            confirmed=True,
            requires_human_confirm=False,
        )],
    )

    report = validate_flow_spec(spec)
    skill_level = report["capability_validation"]["skill_level"]
    codes = {item["code"] for item in skill_level["errors"]}

    assert report["passed"] is True
    assert {"skill_description_missing", "skill_failure_handling_missing"} <= codes


def test_unconfirmed_capability_relation_type_mismatch_is_p1_warning_not_publish_gate():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="write",
            method="POST",
            url="/api/write",
            path="/api/write",
            body_source='{"items":[]}',
            params=[ParamField(path="items", key="items", value="", type="array", required=True)],
            success_rule={"path": "code", "equals": 0},
        )],
        capabilities=[
            FlowCapability(
                name="read_count",
                kind="submit",
                nodes=[
                    {"id": "call_write", "type": "call", "step_id": "write"},
                    {"id": "return_result", "type": "return", "from": "write", "path": "response"},
                ],
                output_schema={"type": "object", "properties": {"count": {"type": "number"}}},
                confirmed=True,
                requires_human_confirm=False,
            ),
            FlowCapability(
                name="submit_items",
                kind="submit",
                nodes=[
                    {"id": "call_write", "type": "call", "step_id": "write"},
                    {"id": "return_result", "type": "return", "from": "write", "path": "response"},
                ],
                inputs=[{"field_id": "in-items", "scope": "input", "path": "items", "key": "items", "type": "array"}],
                input_schema={"type": "object", "properties": {"items": {"type": "array"}}},
                output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
                confirmed=True,
                requires_human_confirm=False,
            ),
        ],
        capability_relations=[CapabilityRelation(
            relation_id="rel_bad_type",
            from_capability="read_count",
            from_output="count",
            to_capability="submit_items",
            to_input="items",
            confirmed=False,
        )],
    )

    report = validate_flow_spec(spec)
    relation_report = report["capability_validation"]["capability_relations"]

    assert report["passed"] is True
    assert relation_report["relations"][0]["type_compatible"] is False
    assert relation_report["warnings"][0]["code"] == "capability_relation_type_mismatch"


def test_confirmed_capability_relation_type_mismatch_blocks_publish_gate():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="write",
            method="POST",
            url="/api/write",
            path="/api/write",
            body_source='{"items":[]}',
            params=[ParamField(path="items", key="items", value="", type="array", required=True)],
            success_rule={"path": "code", "equals": 0},
        )],
        capabilities=[
            FlowCapability(
                name="read_count",
                kind="submit",
                nodes=[{"id": "call_write", "type": "call", "step_id": "write"}],
                output_schema={"type": "object", "properties": {"count": {"type": "number"}}},
                confirmed=True,
                requires_human_confirm=False,
            ),
            FlowCapability(
                name="submit_items",
                kind="submit",
                nodes=[{"id": "call_write", "type": "call", "step_id": "write"}],
                input_schema={"type": "object", "properties": {"items": {"type": "array"}}},
                confirmed=True,
                requires_human_confirm=False,
            ),
        ],
        capability_relations=[CapabilityRelation(
            relation_id="rel_confirmed_bad_type",
            from_capability="read_count",
            from_output="count",
            to_capability="submit_items",
            to_input="items",
            confirmed=True,
        )],
    )

    report = validate_flow_spec(spec)
    relation_report = report["capability_validation"]["capability_relations"]

    assert report["passed"] is True
    assert relation_report["passed"] is False
    assert relation_report["errors"][0]["code"] == "capability_relation_type_mismatch"


def test_generate_capabilities_edit_is_incremental():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[FlowCapability(
            name="submit_batch",
            title="人工确认标题",
            kind="submit_batch",
            nodes=[],
            confirmed=True,
            requires_human_confirm=False,
            locked=True,
            status="confirmed",
            updated_by="user",
            confidence=0.2,
        )],
    )

    new = apply_flow_edits(spec, [{"op": "generate_capabilities"}])

    cap = new.capabilities[0]
    assert cap.title == "人工确认标题"
    assert cap.confirmed is True
    assert cap.locked is True
    assert cap.status == "confirmed"
    assert cap.updated_by == "user"
    assert cap.step_ids == ["submit"]
    assert any(n.get("type") == "call" and n.get("step_id") == "submit" for n in cap.nodes)


def test_generate_capabilities_respects_removed_capability_step():
    spec = FlowSpec(
        flow_id="f",
        steps=[
            FlowStep(step_id="read", method="GET", url="/api/read", path="/api/read"),
            FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit"),
        ],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=[
                {"id": "call_1", "type": "call", "step_id": "read"},
                {"id": "call_2", "type": "call", "step_id": "submit"},
            ],
        )],
    )

    edited = apply_flow_edits(spec, [{"op": "remove_capability_step", "capability_index": 0, "step_id": "read"}])
    regenerated = apply_flow_edits(edited, [{"op": "generate_capabilities"}])

    assert "read" not in regenerated.capabilities[0].step_ids
    assert all(n.get("step_id") != "read" for n in regenerated.capabilities[0].nodes if n.get("type") == "call")


def test_update_capability_step_order_preserves_user_order_independent_of_global_steps():
    spec = FlowSpec(
        flow_id="f",
        steps=[
            FlowStep(step_id="read", method="GET", url="/api/read", path="/api/read"),
            FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit"),
        ],
        capabilities=[FlowCapability(
            name="custom_order",
            kind="submit",
            nodes=[
                {"id": "call_1", "type": "call", "step_id": "read"},
                {"id": "call_2", "type": "call", "step_id": "submit"},
            ],
        )],
    )

    edited = apply_flow_edits(spec, [{
        "op": "reorder_capability_steps",
        "capability_name": "custom_order",
        "step_ids": ["submit", "read"],
    }])

    assert edited.capabilities[0].step_ids == ["submit", "read"]
    assert [n["step_id"] for n in edited.capabilities[0].nodes if n.get("type") == "call"] == ["submit", "read"]


def test_generate_capabilities_respects_removed_capability():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[FlowCapability(name="submit_batch", kind="submit_batch", nodes=_call_nodes(["submit"]))],
    )

    edited = apply_flow_edits(spec, [{"op": "remove_capability", "capability_index": 0}])
    regenerated = apply_flow_edits(edited, [{"op": "generate_capabilities"}])

    assert regenerated.capabilities == []


def test_batch_capability_exports_execution_contract_and_entries_schema():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            content_type="application/json",
            body_source='[{"date":"2026-05-12","content":"x"}]',
            source_meta={"batch_intent": True},
            params=[
                ParamField(path="[0].date", key="date", value="2026-05-12", type="date", required=True),
                ParamField(path="[0].content", key="content", value="x", type="string", required=True),
            ],
        )],
    )
    spec = apply_flow_edits(spec, [{"op": "generate_capabilities"}])

    api_request, errors = flow_spec_to_api_request(spec)

    assert errors == []
    cap = api_request["capabilities"][0]
    assert cap["kind"] == "submit_batch"
    assert cap["execution_contract"]["protocol"] == "dano.capability_plan.v1"
    assert cap["execution_contract"]["batch"]["enabled"] is True
    assert cap["execution_contract"]["batch"]["items_field"] == "entries"
    assert "entries" in cap["input_schema"]["properties"]
    assert any(n.get("type") == "foreach" for n in cap["workflow_nodes"])
    assert api_request["capability_protocol"] == "dano.capability_plan.v1"
    report = validate_flow_spec(spec)
    assert not any("条目 schema 未覆盖" in warning for warning in report["warnings"])


def test_single_array_wrapped_form_is_not_inferred_as_batch_without_evidence():
    spec = FlowSpec(
        flow_id="single-array-form",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/form/submit",
            path="/api/form/submit",
            content_type="application/json",
            body_source='[{"reason":"x"}]',
            params=[ParamField(path="[0].reason", key="reason", value="x", required=True)],
        )],
    )

    planned = apply_flow_edits(spec, [{"op": "generate_capabilities"}])

    assert len(planned.capabilities) == 1
    assert planned.capabilities[0].kind == "submit"
    assert not any(node.get("type") == "foreach" for node in planned.capabilities[0].nodes)


def _two_capability_compile_spec():
    status = FlowStep(
        step_id="status",
        method="GET",
        url="/api/status?caseId=C-1",
        path="/api/status",
        params=[ParamField(
            path="query.caseId",
            key="caseId",
            value="C-1",
            type="string",
            required=True,
            category="user_param",
            source_kind="user_input",
            exposed_to_user=True,
        )],
        sample_inputs={"caseId": "C-1"},
        response_json={"code": 0, "data": {"status": "pending"}},
    )
    submit = FlowStep(
        step_id="submit",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"caseId":"C-1","reason":"补充材料"}',
        params=[
            ParamField(
                path="caseId",
                key="caseId",
                value="C-1",
                type="string",
                required=True,
                category="user_param",
                source_kind="user_input",
                exposed_to_user=True,
            ),
            ParamField(
                path="reason",
                key="reason",
                value="补充材料",
                type="string",
                required=True,
                category="user_param",
                source_kind="user_input",
                exposed_to_user=True,
            ),
        ],
        sample_inputs={"caseId": "C-1", "reason": "补充材料"},
        response_json={"code": 0, "data": {"processId": "P-1"}},
    )
    return FlowSpec(
        flow_id="cap-compile",
        steps=[status, submit],
        capabilities=[
            FlowCapability(
                name="query_status",
                capability_id="cap-query",
                kind="query_status",
                nodes=[
                    {"id": "call_status", "type": "call", "step_id": "status"},
                    {"id": "return_status", "type": "return", "from": "status", "path": "response.data.status"},
                ],
                input_schema={"type": "object", "properties": {"caseId": {"type": "string"}}},
                output_schema={"type": "object", "properties": {"status": {"type": "string"}}},
                outputs=[{"field_id": "out-status", "scope": "output", "path": "status", "key": "status"}],
                output_mapping=[{"kind": "response_path", "step_id": "status", "response_path": "data.status"}],
                confirmed=True,
                requires_human_confirm=False,
            ),
            FlowCapability(
                name="submit_batch",
                capability_id="cap-submit",
                kind="submit_batch",
                nodes=[
                    {"id": "call_submit", "type": "call", "step_id": "submit"},
                    {"id": "return_submit", "type": "return", "from": "submit", "path": "response.data.processId"},
                ],
                input_schema={
                    "type": "object",
                    "properties": {"caseId": {"type": "string"}, "reason": {"type": "string"}},
                    "required": ["caseId", "reason"],
                },
                output_schema={"type": "object", "properties": {"processId": {"type": "string"}}},
                outputs=[{"field_id": "out-process", "scope": "output", "path": "processId", "key": "processId"}],
                output_mapping=[{"kind": "response_path", "step_id": "submit", "response_path": "data.processId"}],
                confirmed=True,
                requires_human_confirm=False,
            ),
        ],
    )


def _compiled_step_ids(api_request):
    return [s["step_id"] for s in api_request.get("steps") or [api_request]]


def _report_text(report):
    chunks = list(report.get("errors") or [])
    chunks.extend(report.get("warnings") or [])
    cap_report = report.get("capability_validation") or {}
    chunks.extend(cap_report.get("errors") or [])
    chunks.extend(cap_report.get("warnings") or [])
    for layer in (cap_report.get("layers") or {}).values():
        chunks.extend(layer.get("errors") or [])
        chunks.extend(layer.get("warnings") or [])
    for key in ("capability_internal", "capability_relations", "skill_level"):
        layer = cap_report.get(key) or {}
        chunks.append(layer)
        chunks.extend(layer.get("errors") or [])
        chunks.extend(layer.get("warnings") or [])
    for cap in cap_report.get("capabilities") or []:
        chunks.extend(cap.get("errors") or [])
        chunks.extend(cap.get("warnings") or [])
    for rel in cap_report.get("relations") or []:
        chunks.extend(rel.get("errors") or [])
        chunks.extend(rel.get("warnings") or [])
    return "\n".join(str(x) for x in chunks)


def test_compile_capability_requires_explicit_capability_selection():
    api_request, errors = compile_capability_to_api_request(_two_capability_compile_spec())

    assert api_request is None
    assert errors and "capability" in errors[0]


def test_flow_spec_to_api_request_can_compile_single_capability_without_changing_full_export():
    spec = _two_capability_compile_spec()

    full, full_errors = flow_spec_to_api_request(spec)
    scoped, scoped_errors = flow_spec_to_api_request(spec, capability_name="query_status")
    direct, direct_errors = compile_capability_to_api_request(spec, capability_id="cap-query")
    full_again, full_again_errors = flow_spec_to_api_request(spec)

    assert full_errors == []
    assert full_again_errors == []
    assert scoped_errors == []
    assert direct_errors == []
    assert _compiled_step_ids(full) == ["status", "submit"]
    assert [c["name"] for c in full["capabilities"]] == ["query_status", "submit_batch"]
    assert full_again == full

    assert _compiled_step_ids(scoped) == ["status"]
    assert [c["name"] for c in scoped["capabilities"]] == ["query_status"]
    assert list(scoped["workflow_nodes"]) == ["query_status"]
    assert scoped["capabilities"][0]["compiled_step_ids"] == ["status"]
    assert direct["selected_capability"]["name"] == "query_status"




def test_capability_validation_reports_three_layers_and_bad_dependency_output_relation():
    spec = _two_capability_compile_spec()
    spec.capabilities[1].dependencies = [CapabilityDependency(
        dependency_id="bad-dep",
        type="response_to_request",
        source={"step_id": "missing_status", "path": "data.status"},
        target={"step_id": "submit", "path": "caseId"},
        confirmed=True,
        locked=True,
    )]
    spec.capabilities[1].output_mapping = [{
        "kind": "response_path",
        "step_id": "missing_submit",
        "response_path": "data.processId",
    }]
    spec.capability_relations = [CapabilityRelation(
        relation_id="bad-rel",
        type="suggested_call_chain",
        from_capability="query_status",
        from_output="missingStatus",
        to_capability="submit_batch",
        to_input="missingInput",
        confirmed=True,
    )]

    report = validate_flow_spec(spec)
    cap_report = report["capability_validation"]
    text = _report_text(report)

    assert {"capability_internal", "capability_relations", "skill_level"} <= set(cap_report)
    assert "bad-dep" not in text and "missing_status" not in text
    assert "missing_submit" in text and "output" in text
    assert "bad-rel" in text and "missingStatus" in text and "missingInput" in text


def test_legacy_flow_spec_without_capabilities_keeps_single_request_api_shape():
    spec = FlowSpec(
        flow_id="legacy-single",
        steps=[FlowStep(
            step_id="legacy_submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            body_source='{"reason":"old"}',
            params=[ParamField(
                path="reason",
                key="reason",
                value="old",
                type="string",
                required=True,
                category="user_param",
                source_kind="user_input",
                exposed_to_user=True,
            )],
            sample_inputs={"reason": "old"},
        )],
    )

    api_request, errors = flow_spec_to_api_request(spec)

    assert errors == []
    assert "steps" not in api_request
    assert "capabilities" not in api_request
    assert "capability_protocol" not in api_request
    assert api_request["method"] == "POST"
    assert api_request["path"] == "/api/submit"
    assert api_request["body_template"] == {"reason": "{{reason}}"}
    assert api_request["params"] == ["reason"]
    assert api_request["sample_inputs"] == {"reason": "old"}


def test_flow_spec_to_api_request_syncs_goal_required_inputs_after_param_rename():
    spec = FlowSpec(
        flow_id="f",
        title="提交请假申请",
        goal={
            "intent": "submit-process 流程(3 步)",
            "required_inputs": ["type"],
            "success_criteria": ["提交接口返回成功规则通过"],
            "forbidden_actions": ["删除"],
            "risk_level": "L3",
        },
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/admin-api/oa/duty-leave/submit-process",
            path="/admin-api/oa/duty-leave/submit-process",
            content_type="application/json",
            body_source='{"type":"2"}',
            params=[ParamField(path="type", key="类型", label="类型", value="2", type="enum", required=True)],
        )],
    )

    api_request, errors = flow_spec_to_api_request(spec)

    assert errors == []
    assert api_request["params"] == ["类型"]
    assert api_request["goal"]["required_inputs"] == ["类型"]
    assert "type" not in api_request["goal"]["required_inputs"]


def test_capability_return_node_without_source_is_normalized_to_last_call():
    spec = FlowSpec(
        flow_id="f",
        steps=[
            FlowStep(step_id="read", method="GET", url="/api/read", path="/api/read"),
            FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit"),
        ],
        capabilities=[FlowCapability(
            name="submit_batch",
            title="提交业务申请",
            kind="submit_batch",
            nodes=[
                {"id": "node_1", "type": "call", "step_id": "read"},
                {"id": "node_2", "type": "call", "step_id": "submit"},
                {"id": "node_4", "type": "return"},
            ],
        )],
    )

    report = validate_flow_spec(spec)

    assert not any("return 节点 `node_4` 缺少返回来源" in e for e in report["errors"])
    assert not any("return 节点 `node_4` 缺少返回来源" in w for w in report["warnings"])


def test_add_candidate_step_is_idempotent_when_request_already_exists():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(
            step_id="read",
            method="GET",
            url="https://oa.example.com/gsgl/xm/getProjectInfosByBt?keyword=abc",
            path="/gsgl/xm/getProjectInfosByBt?keyword=abc",
            source_meta={"request_index": 7},
        )],
        request_facts=_request_facts_from_graph_fixture({
                "selected_steps": [],
                "candidate_reads": [{
                    "request_index": 7,
                    "method": "GET",
                    "url": "https://oa.example.com/gsgl/xm/getProjectInfosByBt?keyword=abc",
                    "path": "/gsgl/xm/getProjectInfosByBt",
                    "role": "read_option",
                    "confidence": 0.95,
                    "response_status": 200,
                    "response_json": {"data": [{"xmId": "YF001", "xmName": "项目A"}]},
                }],
                "filtered_requests": [],
            }),
    )

    new = apply_flow_edits(spec, [{"op": "add_candidate_step", "request_index": 7}])

    assert len(new.steps) == 1
    usage = new.request_facts.usage["idx:7"]
    assert usage.materialized_step_id == "read"
    assert usage.state == "materialized"


def test_nonexistent_link_lists_available():
    spec = _two_step_spec_with_link()
    with pytest.raises(ValueError) as exc:
        apply_flow_edits(spec, [{"op": "remove", "link_id": "nope"}])
    msg = str(exc.value)
    assert "available:" in msg
    assert "l1" in msg


def test_resolve_review_item_is_preserved_in_validation():
    spec = _two_step_spec_with_link()
    spec = apply_flow_edits(spec, [])
    item = next(i for i in spec.review_items if i.type == "link_confirmation")

    new = apply_flow_edits(spec, [{"op": "resolve_review", "review_id": item.id, "resolved": True}])

    assert next(i for i in new.review_items if i.id == item.id).resolved is True
    report = validate_flow_spec(new)
    assert next(i for i in report["review_items"] if i["id"] == item.id)["resolved"] is True


def test_resolve_reviews_excluding_high():
    spec = _two_step_spec_with_link()
    spec.steps[0].params[0].category = "system_const"
    spec.steps[0].params[0].source_kind = "constant"
    spec.steps[0].params[0].exposed_to_user = True
    spec = apply_flow_edits(spec, [])
    high_items = [item for item in spec.review_items if item.severity == "high"]

    assert high_items
    assert any(item.type == "system_const_exposed" for item in high_items)

    new = apply_flow_edits(spec, [{
        "op": "resolve_reviews",
        "exclude_severities": ["high"],
        "resolved": True,
    }])

    for item in new.review_items:
        if item.severity == "high":
            assert item.resolved is False
        else:
            assert item.resolved is True


@pytest.mark.parametrize(
    "capability_model",
    [None, {"status": "ready"}],
    ids=["not-generated", "all-capabilities-removed"],
)
def test_unknown_source_review_is_hidden_until_a_capability_exists(capability_model):
    spec = _make_spec()
    spec.steps[0].body_source = '{"form":{"userId":"123","name":"test"}}'
    spec.steps[0].params[0].category = "runtime_var"
    spec.steps[0].params[0].source_kind = "unknown"
    spec.steps[0].params[0].need_human_confirm = True
    if capability_model is not None:
        spec.meta = {**(spec.meta or {}), "capability_model": capability_model}

    report = validate_flow_spec(spec)

    assert not any(
        item.get("type") in {"field_source_unknown", "field_source_incomplete"}
        for item in report["review_items"]
    )
    assert not any(
        item.get("code") in {"field_source_unknown", "field_source_incomplete"}
        for items in report["issue_groups"].values()
        for item in items
    )


def test_unknown_source_review_is_locatable_ignorable_and_non_blocking_after_capability_generation():
    spec = _make_spec()
    param = spec.steps[0].params[0]
    param.category = "runtime_var"
    param.source_kind = "unknown"
    param.need_human_confirm = True
    spec.capabilities = [FlowCapability(
        name="submit",
        kind="submit",
        nodes=[{"id": "call_step1", "type": "call", "step_id": "step1"}],
    )]

    new = apply_flow_edits(spec, [])
    target_items = [i for i in new.review_items if i.target.get("path") == param.path]

    assert [i.type for i in target_items] == ["field_source_unknown"]
    item = target_items[0]
    assert item.severity == "medium"
    assert item.blocking is False
    assert item.ignorable is True
    assert item.target == {
        "kind": "param",
        "step_id": "step1",
        "step_name": "",
        "path": "form.userId",
        "key": "userId",
        "param_type": "string",
        "category": "runtime_var",
        "source_kind": "unknown",
    }
    # Unknown user-input sources receive the same field-local warning instead
    # of being silently omitted because their category is not runtime_var.
    assert any(
        candidate.type == "field_source_unknown"
        and candidate.target.get("path") == "form.name"
        for candidate in new.review_items
    )

    new.steps[0].body_source = '{"form":{"userId":"123","name":"test"}}'
    report = validate_flow_spec(new)
    assert report["passed"] is True
    assert report["errors"] == []
    assert report["warnings"] == []
    issue = next(
        issue for issue in report["issue_groups"]["field"]
        if issue["target"].get("path") == "form.userId"
    )
    assert issue["source"] == "review"
    assert issue["blocking"] is False
    assert issue["ignorable"] is True
    assert issue["review_id"] == item.id
    accepted, gate = flow_spec_module._semantic_candidate_gate(new, new.model_copy(deep=True))
    assert accepted is True
    assert gate["reasons"] == []

    ignored = apply_flow_edits(new, [{
        "op": "resolve_review", "review_id": item.id, "resolved": True,
    }])
    ignored_report = validate_flow_spec(ignored)
    assert not any(
        issue.get("review_id") == item.id
        for issues in ignored_report["issue_groups"].values()
        for issue in issues
    )


@pytest.mark.parametrize(("source_kind", "source", "expected_code", "message_part"), [
    ("unknown", {}, "field_source_unknown", "来源尚未识别"),
    ("previous_response", {"step_id": "upstream"}, "field_source_incomplete", "缺少步骤或响应字段"),
    ("request_header", {"kind": "request_header"}, "field_source_incomplete", "缺少 header 名称"),
    ("system_generated", {"strategy": "unsupported"}, "field_source_incomplete", "缺少有效生成策略"),
    (
        "computed",
        {"strategy": "date_span_days_json", "start_field": "start"},
        "field_source_incomplete",
        "缺少可执行规则",
    ),
    ("page_context", {"kind": "page_context"}, "field_source_incomplete", "缺少 context_key"),
])
def test_missing_runtime_source_configuration_is_advisory(
    source_kind, source, expected_code, message_part,
):
    spec = FlowSpec(flow_id=f"source-advice-{source_kind}", steps=[FlowStep(
        step_id="query",
        method="GET",
        url="/api/search",
        path="/api/search",
        params=[ParamField(
            path="query.runtimeId",
            key="运行期标识",
            value="",
            category="runtime_var",
            source_kind=source_kind,
            source=source,
            exposed_to_user=False,
            need_human_confirm=True,
        )],
        success_rule={"kind": "http_status", "values": [200]},
    )], capabilities=[FlowCapability(
        name="query_status",
        kind="query_status",
        nodes=[{"id": "call_query", "type": "call", "step_id": "query"}],
    )])

    api_request, build_errors = flow_spec_to_api_request(spec)
    report = validate_flow_spec(spec)

    assert api_request is not None
    assert build_errors == []
    assert report["passed"] is True
    assert report["errors"] == []
    assert report["warnings"] == []
    issue = next(
        item for item in report["issue_groups"]["field"]
        if item.get("code") == expected_code
    )
    assert issue["target"]["step_id"] == "query"
    assert issue["target"]["path"] == "query.runtimeId"
    assert issue["blocking"] is False
    assert issue["ignorable"] is True
    assert message_part in issue["message"]
    ignored = apply_flow_edits(spec, [{
        "op": "resolve_review", "review_id": issue["review_id"], "resolved": True,
    }])
    ignored_report = validate_flow_spec(ignored)
    assert not any(
        item.get("review_id") == issue["review_id"]
        for items in ignored_report["issue_groups"].values()
        for item in items
    )


def test_source_warning_does_not_hide_unrelated_request_builder_error():
    spec = FlowSpec(flow_id="source-warning-with-hard-error", steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/api/submit",
        params=[ParamField(
            path="runtimeId", key="运行期标识", value="",
            category="runtime_var", source_kind="unknown", source={},
            exposed_to_user=False,
        )],
    )], capabilities=[FlowCapability(
        name="submit",
        kind="submit",
        nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
    )])

    report = validate_flow_spec(spec)

    assert report["passed"] is False
    assert any("缺少请求体" in message for message in report["errors"])
    assert report["issue_groups"]["execution"][0]["blocking"] is True
    assert report["issue_groups"]["field"][0]["blocking"] is False
    assert report["issue_groups"]["field"][0]["ignorable"] is True


def test_orchestrate_existing_capability_reanalyses_uncovered_recorded_interfaces():
    spec = FlowSpec(
        flow_id="incremental-only",
        steps=[
            FlowStep(step_id="status", method="GET", url="/api/status", path="/api/status"),
            FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit"),
        ],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            title="用户已经编辑的能力",
            updated_by="user",
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))

    by_kind = {cap.kind: cap for cap in out.capabilities}
    assert set(by_kind) == {"submit_batch", "query_status"}
    assert by_kind["submit_batch"].title == "用户已经编辑的能力"
    assert by_kind["submit_batch"].step_ids == ["submit"]
    assert by_kind["query_status"].step_ids == ["status"]


def _scope_expanding_submission() -> dict:
    return {"ops": [
            {"op": "upsert_capability", "capability": {
                "name": "submit_batch", "title": "完善后的批量提交", "kind": "submit_batch",
            }},
            {"op": "upsert_capability", "capability": {
                "name": "unexpected_query", "title": "不应新增", "kind": "query_status",
            }},
            {"op": "add_request_to_capability", "capability": "submit_batch", "step_id": "status"},
            {"op": "remove_request_from_capability", "capability": "submit_batch", "step_id": "submit"},
        ]}


def _destructive_incremental_submission() -> dict:
    return {"ops": [
            {"op": "remove_capability", "capability": "query_status"},
            {"op": "remove_request_from_capability", "capability": "submit", "step_id": "submit"},
            {"op": "reject_dependency", "link_id": "confirmed-link"},
            {"op": "upsert_capability", "capability": {
                "name": "unexpected", "title": "不得新增", "kind": "submit",
            }},
            {"op": "upsert_capability", "capability": {
                "name": "submit", "title": "完善后的提交能力", "kind": "submit",
            }},
        ]}


def test_recording_submission_rejects_destructive_incremental_operations():
    query = FlowStep(
        step_id="query", method="GET", path="/records/page",
        response_json={"data": {"records": []}},
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/records/submit",
        body_source='{"recordId":"one"}',
        params=[ParamField(
            path="recordId", key="记录", value="one", category="runtime_var",
            source_kind="previous_response",
            source={"kind": "previous_response", "step_id": "query", "response_path": "data.records[0].id"},
        )],
        response_json={"code": 0},
    )
    link = FlowLink(
        link_id="confirmed-link", source_step_id="query", source_path="data.records[0].id",
        target_step_id="submit", target_path="recordId", confirmed=True, locked=True,
    )
    relation = CapabilityRelation(
        relation_id="confirmed-relation", type="caller_decision", mode="caller_decision",
        from_capability="query_status", to_capability="submit", confirmed=True,
    )
    spec = FlowSpec(
        steps=[query, submit], links=[link], capability_relations=[relation],
        capabilities=[
            FlowCapability(
                name="query_status", kind="query_status",
                nodes=[{"id": "call_query", "type": "call", "step_id": "query"}],
            ),
            FlowCapability(
                name="submit", kind="submit",
                nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            ),
        ],
        meta={"capability_model": {"status": "ready"}},
    )

    with pytest.raises(ValueError, match="remove_capability"):
        asyncio.run(apply_recording_agent_submission(
            spec, submission=_destructive_incremental_submission(), mode="plan", max_rounds=1,
        ))


def test_repeated_orchestration_can_reanalyse_real_interfaces_but_not_remove_them():
    spec = FlowSpec(
        flow_id="scope-locked",
        steps=[
            FlowStep(step_id="status", method="GET", url="/api/status", path="/api/status"),
            FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit"),
        ],
        capabilities=[FlowCapability(
            name="submit_batch",
            title="批量提交",
            kind="submit_batch",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec, submission=_scope_expanding_submission()))

    by_name = {cap.name: cap for cap in out.capabilities}
    assert "submit_batch" in by_name
    assert "query_status" in by_name
    assert by_name["submit_batch"].title == "完善后的批量提交"
    # remove_request_from_capability is not in the Pi whitelist, while the
    # already materialized status interface is allowed into the re-analysis.
    assert "submit" in by_name["submit_batch"].step_ids
    assert "status" in by_name["submit_batch"].step_ids


def test_manual_field_contract_is_locked_against_planner_overwrite():
    spec = FlowSpec(
        flow_id="manual-field-lock",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/daily/submit",
            path="/daily/submit",
            params=[ParamField(
                path="content",
                key="工作内容",
                category="runtime_var",
                source_kind="unknown",
            )],
        )],
        capabilities=[FlowCapability(
            name="submit",
            kind="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )
    edited = apply_flow_edits(spec, [
        {"op": "update", "step_id": "submit", "param_path": "content", "field": "category", "value": "user_param"},
        {"op": "update", "step_id": "submit", "param_path": "content", "field": "source_kind", "value": "user_input"},
        {"op": "update", "step_id": "submit", "param_path": "content", "field": "source", "value": {"kind": "sample", "path": "content"}},
    ])

    fixed = asyncio.run(auto_fix_flow_spec(
        edited,
        repair_ops=[{"op": "mark_field_as_system_var", "step_id": "submit", "path": "content"}],
        max_rounds=1,
        expand_requests=False,
        allow_scope_changes=False,
    ))
    param = fixed.steps[0].params[0]

    assert param.locked is True
    assert param.category == "user_param"
    assert param.source_kind == "user_input"


def test_manual_source_override_is_preserved_without_rewriting_type_or_evidence():
    """人工来源是最终结果；类型和录制证据保持独立，不做跨字段强制改写。"""
    spec = FlowSpec(
        flow_id="manual-query-source-override",
        steps=[FlowStep(
            step_id="query",
            method="GET",
            path="/oa/seal-apply/page",
            params=[ParamField(
                path="query.useInfo",
                key="使用描述",
                value="12",
                type="enum",
                wire_type="string",
                category="user_param",
                source_kind="api_option",
                source={"kind": "api_option", "source_url": "/unrelated/options"},
                enum_options=[{"label": "候选十二", "value": "12"}],
                enum_value_map={"候选十二": "12"},
            )],
            selects=[SelectBinding(
                param="使用描述",
                path="query.useInfo",
                source_url="/unrelated/options",
                value_key="id",
                label_key="name",
                options=[{"label": "候选十二", "value": "12"}],
                option_map={"候选十二": "12"},
                enum_source="api",
            )],
        )],
        capabilities=[FlowCapability(
            name="query_status",
            kind="query_status",
            nodes=[{"id": "call_query", "type": "call", "step_id": "query"}],
        )],
    )

    edited = apply_flow_edits(spec, [{
        "op": "update", "step_id": "query", "param_path": "query.useInfo",
        "field": "source_kind", "value": "user_input", "actor": "user",
    }, {
        "op": "update", "step_id": "query", "param_path": "query.useInfo",
        "field": "source", "value": {"kind": "sample", "path": "query.useInfo"}, "actor": "user",
    }])
    param = edited.steps[0].params[0]
    assert param.locked is True
    assert param.type == "enum"
    assert param.category == "user_param"
    assert param.source_kind == "user_input"
    assert param.source["kind"] == "sample"
    assert param.enum_options == [{"label": "候选十二", "value": "12"}]
    assert param.enum_value_map == {"候选十二": "12"}
    assert len(edited.steps[0].selects) == 1

    synced = sync_flow_spec_models(edited)
    synced_param = synced.steps[0].params[0]
    assert synced_param.type == "enum"
    assert synced_param.source_kind == "user_input"
    assert len(synced.steps[0].selects) == 1

    optimized = asyncio.run(auto_fix_flow_spec(
        synced,
        repair_ops=[{
            "op": "bind_option_source",
            "target_step": "query",
            "target_path": "query.useInfo",
            "source_url": "/unrelated/options",
            "value_key": "id",
            "label_key": "name",
        }],
        max_rounds=1,
        expand_requests=False,
        allow_scope_changes=False,
    ))
    optimized_param = optimized.steps[0].params[0]
    assert optimized_param.type == "enum"
    assert optimized_param.source_kind == "user_input"
    assert len(optimized.steps[0].selects) == 1


def _manual_param_capability_spec(*, locked: bool = True) -> FlowSpec:
    return FlowSpec(
        flow_id="manual-param-capability-protection",
        steps=[FlowStep(
            step_id="query",
            method="GET",
            url="/api/search?status=enabled",
            path="/api/search",
            params=[ParamField(
                path="query.status",
                key="状态",
                value="enabled",
                type="string",
                category="user_param",
                source_kind="user_input",
                source={"kind": "sample", "path": "query.status"},
                exposed_to_user=True,
                locked=locked,
                evidence=[{
                    "source": "manual_edit", "field": "source_kind", "value": "user_input",
                }],
            )],
            success_rule={"kind": "http_status", "values": [200]},
        )],
        capabilities=[FlowCapability(
            name="query_status",
            kind="query_status",
            nodes=[{"id": "call_query", "type": "call", "step_id": "query"}],
        )],
    )


def _overwrite_manual_param_as_internal_op(*, actor: str) -> dict:
    return {
        "op": "upsert_internal_field",
        "capability_name": "query_status",
        "actor": actor,
        "field_data": {
            "step_id": "query",
            "path": "query.status",
            "key": "状态",
            "type": "number",
            "source_kind": "computed",
            "source": {"strategy": "date_span_days_json"},
            "exposed_to_caller": False,
        },
    }


def test_planner_capability_field_cannot_overwrite_manual_param_axes():
    spec = _manual_param_capability_spec(locked=True)

    planned = apply_flow_edits(spec, [
        _overwrite_manual_param_as_internal_op(actor="planner"),
    ])
    param = planned.steps[0].params[0]
    assert (param.type, param.category, param.source_kind, param.source, param.exposed_to_user) == (
        "string", "user_param", "user_input",
        {"kind": "sample", "path": "query.status"}, True,
    )

    # The same scope edit is allowed when it is an explicit operator action.
    user_edited = apply_flow_edits(planned, [
        _overwrite_manual_param_as_internal_op(actor="user"),
    ])
    user_param = user_edited.steps[0].params[0]
    assert (user_param.type, user_param.category, user_param.source_kind, user_param.exposed_to_user) == (
        "number", "runtime_var", "computed", False,
    )


def test_autofix_capability_field_cannot_overwrite_manual_evidence_when_unlocked():
    # Imported/legacy operator edits may carry manual evidence without the newer
    # locked bit. Autofix must honor either form of ownership.
    spec = _manual_param_capability_spec(locked=False)
    repair_op = _overwrite_manual_param_as_internal_op(actor="repair")
    converted = flow_spec_module._autofix_ops_to_edits(
        spec, [repair_op], allow_scope_changes=False,
    )
    assert any(item.get("op") == "upsert_internal_field" for item in converted)

    fixed = asyncio.run(auto_fix_flow_spec(
        spec,
        repair_ops=[repair_op],
        max_rounds=1,
        expand_requests=False,
        allow_scope_changes=False,
    ))
    param = fixed.steps[0].params[0]
    assert (param.type, param.category, param.source_kind, param.source, param.exposed_to_user) == (
        "number", "user_param", "user_input",
        {"kind": "sample", "path": "query.status"}, True,
    )


@pytest.mark.parametrize("actor", [
    "planner", "Planner", "repair", " repair ", "autofix", "optimizer", "system",
])
@pytest.mark.parametrize("locked", [True, False])
def test_raw_automated_param_update_cannot_overwrite_manual_contract(actor, locked):
    spec = _manual_param_capability_spec(locked=locked)

    updated = apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "query",
        "param_path": "query.status",
        "field": "category",
        "value": "runtime_var",
        "actor": actor,
    }])

    param = updated.steps[0].params[0]
    assert (param.category, param.source_kind, param.source, param.exposed_to_user) == (
        "user_param", "user_input",
        {"kind": "sample", "path": "query.status"}, True,
    )


def test_incremental_planner_cannot_overwrite_user_confirmed_capability_identity_or_title():
    spec = FlowSpec(
        steps=[FlowStep(step_id="submit", method="POST", path="/submit")],
        capabilities=[FlowCapability(
            name="submit", title="人工确认标题", intent="人工确认意图", kind="submit",
            nodes=_call_nodes(["submit"]), confirmed=True, locked=True, updated_by="user",
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    optimized = asyncio.run(orchestrate_flow_capabilities(
        spec,
        submission={"ops": [{
            "op": "upsert_capability",
            "capability": {
                "name": "submit",
                "title": "模型覆盖标题",
                "kind": "submit_batch",
                "intent": "模型覆盖意图",
            },
        }]},
        generation_mode="optimize",
    ))
    capability = optimized.capabilities[0]
    assert capability.name == "submit"
    assert capability.kind == "submit"
    assert capability.title == "人工确认标题"
    assert capability.intent == "人工确认意图"
    assert capability.confirmed is True


def test_incremental_semantic_candidate_with_new_validation_error_is_rolled_back_atomically():
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="submit", method="POST", path="/submit", body_source='{"title":"x"}',
            params=[ParamField(path="title", key="标题", category="user_param", source_kind="user_input")],
        )],
        capabilities=[FlowCapability(
            name="submit", title="提交", kind="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    optimized = asyncio.run(orchestrate_flow_capabilities(
        spec,
        submission={"ops": [{
            "op": "set_condition",
            "capability": "submit",
            "node": {
                "id": "bad_entries_condition",
                "condition": "input.entries.length > 0",
                "then": [{"id": "call_submit", "type": "call", "step_id": "submit"}],
            },
        }]},
        generation_mode="optimize",
    ))
    assert optimized.meta["capability_model"]["source"] == "incremental_rejected"
    assert optimized.meta["capability_model"]["proposal_gate"]["accepted"] is False
    assert not any(
        node.get("type") == "condition"
        for node in flow_spec_module._iter_capability_nodes(optimized.capabilities[0].nodes)
    )


def test_page_context_missing_key_is_advisory_and_differs_from_upstream_response():
    step = FlowStep(
        step_id="submit",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        params=[ParamField(
            path="departmentId",
            key="部门",
            category="runtime_var",
            source_kind="page_context",
            source={"kind": "page_context", "path": "departmentId"},
        )],
    )
    spec = FlowSpec(
        flow_id="context-source",
        steps=[step],
        capabilities=[FlowCapability(
            name="submit",
            kind="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )

    invalid = validate_flow_spec(spec)
    assert not any("context_key" in message for message in invalid["errors"])
    context_issue = next(
        item for item in invalid["issue_groups"]["field"]
        if item.get("code") == "field_source_incomplete"
    )
    assert "context_key" in context_issue["message"]
    assert context_issue["blocking"] is False
    assert context_issue["ignorable"] is True

    step.params[0].source = {"kind": "page_context", "context_key": "department_id", "path": "departmentId"}
    valid = validate_flow_spec(spec)
    assert not any("context_key" in message for message in valid["errors"])
    assert not any(
        "context_key" in item.get("message", "")
        for items in valid["issue_groups"].values()
        for item in items
    )


def test_orchestration_removes_empty_planner_capability():
    spec = FlowSpec(
        flow_id="empty-capability",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[
            FlowCapability(name="list_options", kind="list_options", confirmed=True),
            FlowCapability(
                name="submit",
                kind="submit",
                nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            ),
        ],
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))

    assert [cap.name for cap in out.capabilities] == ["submit"]


def test_only_empty_capability_is_replaced_with_real_baseline():
    spec = FlowSpec(
        flow_id="only-empty-capability",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[FlowCapability(name="list_options", kind="list_options", confirmed=True)],
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))

    assert len(out.capabilities) == 1
    assert out.capabilities[0].kind == "submit"
    assert out.capabilities[0].step_ids == ["submit"]


def _split_independent_writes_submission() -> dict:
    return {"ops": [
            {"op": "upsert_capability", "capability": {
                "name": "submit_daily", "title": "提交日报", "kind": "submit",
            }},
            {"op": "add_request_to_capability", "capability": "submit_daily", "step_id": "daily"},
            {"op": "upsert_capability", "capability": {
                "name": "submit_weekly", "title": "提交周报", "kind": "submit",
            }},
            {"op": "add_request_to_capability", "capability": "submit_weekly", "step_id": "weekly"},
        ]}


def test_initial_planner_can_split_multiple_write_capabilities_without_family_merge():
    spec = FlowSpec(
        flow_id="two-write-capabilities",
        steps=[
            FlowStep(step_id="daily", method="POST", url="/daily/submit", path="/daily/submit"),
            FlowStep(step_id="weekly", method="POST", url="/weekly/submit", path="/weekly/submit"),
        ],
    )

    out = asyncio.run(orchestrate_flow_capabilities(
        spec,
        submission=_split_independent_writes_submission(),
    ))

    assert {cap.name for cap in out.capabilities} == {"submit_daily", "submit_weekly"}
    assert {cap.name: cap.step_ids for cap in out.capabilities} == {
        "submit_daily": ["daily"],
        "submit_weekly": ["weekly"],
    }


def test_batch_input_schema_requires_entries_and_keeps_only_shared_fields_at_top_level():
    shared = FlowStep(
        step_id="query",
        method="GET",
        url="/daily/context",
        path="/daily/context",
        params=[ParamField(
            path="query.project",
            key="项目",
            required=True,
            category="user_param",
            source_kind="user_input",
            evidence=[{"kind": "page_required", "required": True}],
        )],
        source_meta={"role": "read_context", "control_preflight_for_write": True},
    )
    submit = FlowStep(
        step_id="submit",
        method="POST",
        url="/daily/submit-batch",
        path="/daily/submit-batch",
        body_source='[{"date":"2026-05-11","content":"开发"}]',
        params=[
            ParamField(path="[0].date", key="日期", required=True, category="user_param", source_kind="user_input"),
            ParamField(path="[0].content", key="工作内容", required=True, category="user_param", source_kind="user_input"),
        ],
    )
    spec = FlowSpec(
        flow_id="batch-contract",
        steps=[shared, submit],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            nodes=[
                {"id": "call_query", "type": "call", "step_id": "query"},
                {"id": "foreach_entries", "type": "foreach", "items": "input.entries", "steps": [
                    {"id": "call_submit", "type": "call", "step_id": "submit"},
                ]},
            ],
        )],
    )

    synced = flow_spec_module._sync_capability_io_schemas(spec)
    schema = synced.capabilities[0].input_schema

    assert set(schema["properties"]) == {"项目", "entries"}
    assert schema["required"] == ["项目", "entries"]
    assert set(schema["properties"]["entries"]["items"]["properties"]) == {"日期", "工作内容"}
    assert schema["properties"]["entries"]["items"]["required"] == ["日期", "工作内容"]


def test_approval_list_in_single_form_is_not_exported_as_batch_entries():
    submit = FlowStep(
        step_id="submit",
        method="POST",
        url="/leave/submit-process",
        path="/leave/submit-process",
        body_source=json.dumps([{
            "type": 2, "reason": "x", "startTime": "2026-07-01",
            "leaderApprover": "u1", "hrApprover": "u2",
        }]),
        params=[
            ParamField(path="[0].type", key="请假类型", category="user_param", source_kind="page_enum"),
            ParamField(path="[0].reason", key="原因", category="user_param", source_kind="user_input"),
            ParamField(path="[0].startTime", key="开始时间", category="user_param", source_kind="user_input"),
            ParamField(path="[0].leaderApprover", key="领导审批人", category="user_param", source_kind="api_option"),
            ParamField(path="[0].hrApprover", key="人力审批人", category="user_param", source_kind="api_option"),
        ],
    )
    spec = FlowSpec(
        flow_id="single-leave",
        steps=[submit],
        capabilities=[FlowCapability(
            name="submit_batch",
            title="批量提交",
            kind="submit_batch",
            nodes=[{"id": "foreach_entries", "type": "foreach", "items": "input.entries", "steps": [
                {"id": "call_submit", "type": "call", "step_id": "submit"},
            ]}],
            evidence=[{"step_id": "submit"}],
            updated_by="planner",
        )],
    )

    repaired = flow_spec_module._repair_generated_capability_contracts(spec)
    synced = flow_spec_module._sync_capability_io_schemas(repaired)

    cap = synced.capabilities[0]
    assert cap.kind == "submit"
    assert "entries" not in cap.input_schema["properties"]
    assert set(cap.input_schema["properties"]) == {"请假类型", "原因", "开始时间", "领导审批人", "人力审批人"}
    assert not any(node.get("type") == "foreach" for node in cap.nodes)


def test_dynamic_option_snapshot_is_not_a_hard_capability_enum():
    param = ParamField(
        path="approverId",
        key="审批人",
        label="审批人",
        type="enum",
        category="user_param",
        source_kind="api_option",
        source={"source_step_id": "users", "source_url": "/users/list"},
        enum_options=[{"label": "录制时用户", "value": "u1"}],
        enum_value_map={"录制时用户": "u1"},
    )

    schema = flow_spec_module._capability_input_schema([param])
    field = schema["properties"]["审批人"]

    assert field["format"] == "name-ref"
    assert field["x-options-source"] is True
    assert field["x-options-snapshot"] == [{"label": "录制时用户", "value": "u1"}]
    assert "enum" not in field


def test_manual_activity_id_source_is_not_overridden_by_engine_specific_rules():
    step = FlowStep(
        step_id="approval",
        method="GET",
        path="/approval-detail",
        params=[ParamField(
            path="query.activityId",
            key="activityId",
            value="StartUserNode",
            category="system_const",
            source_kind="constant",
            source={"kind": "constant"},
            exposed_to_user=False,
        )],
    )
    spec = FlowSpec(
        flow_id="stable-activity",
        steps=[step],
        capabilities=[FlowCapability(
            name="submit",
            kind="submit",
            nodes=_call_nodes(["approval"]),
            confirmed=True,
            request_fields=[CapabilityField(
                field_id="request_field:approval:query.activityId",
                scope="request_field",
                display_name="activityId",
                path="query.activityId",
                key="activityId",
                step_id="approval",
                source_kind="unknown",
                exposed_to_caller=True,
                locked=True,
            )],
        )],
    )

    changed = apply_flow_edits(spec, [
        {"op": "update", "step_id": "approval", "param_path": "query.activityId",
         "field": "category", "value": "runtime_var"},
        {"op": "update", "step_id": "approval", "param_path": "query.activityId",
         "field": "source_kind", "value": "system_generated"},
        {"op": "update", "step_id": "approval", "param_path": "query.activityId",
         "field": "source", "value": {"kind": "system_generated", "strategy": "uuid"}},
        {"op": "update", "step_id": "approval", "param_path": "query.activityId",
         "field": "exposed_to_user", "value": False},
    ])

    param = changed.steps[0].params[0]
    assert param.category == "runtime_var"
    assert param.source_kind == "system_generated"
    assert param.source == {"kind": "system_generated", "strategy": "uuid"}
    assert param.value == "StartUserNode"
    field = changed.capabilities[0].internal_fields[0]
    assert field.source_kind == "system_generated"
    assert field.exposed_to_caller is False
    report = validate_flow_spec(changed)
    text = "\n".join([*report["errors"], *report["warnings"]])
    assert "activityId" not in text


def test_capability_sync_preserves_process_field_and_excludes_option_reads_from_query():
    process_id = "oa_duty_leave:4:f92ff5"
    process = FlowStep(
        step_id="process",
        method="GET",
        path="/admin-api/bpm/process-definition/get?key=oa_duty_leave",
        response_json={"data": {"id": process_id}},
        source_meta={"role": "business_get"},
    )
    users = FlowStep(
        step_id="users",
        method="GET",
        path="/admin-api/system/user/page",
        response_json={"data": {"list": [{"id": 1, "nickname": "张三"}], "total": 1}},
        source_meta={"role": "business_get"},
    )
    approval = FlowStep(
        step_id="approval",
        method="GET",
        path="/admin-api/bpm/process-instance/get-approval-detail",
        params=[ParamField(
            path="query.processDefinitionId",
            key="processDefinitionId",
            value=process_id,
            category="system_const",
            source_kind="constant",
            exposed_to_user=False,
        )],
        response_json={"data": {"node": "StartUserNode"}},
        source_meta={"role": "read_context", "control_preflight_for_write": True},
    )
    leave_page = FlowStep(
        step_id="leave-page",
        method="GET",
        path="/admin-api/oa/duty-leave/page",
        response_json={"data": {"list": [{"id": "L1", "reason": "测试"}], "total": 1}},
        source_meta={"role": "business_get"},
    )
    submit = FlowStep(
        step_id="submit",
        method="POST",
        path="/admin-api/oa/duty-leave/submit-process",
        body_source='[{"type":3,"reason":"测试","approver1":1,"approver2":2}]',
        params=[
            ParamField(path="[0].type", key="类型", type="enum", category="user_param", source_kind="page_enum"),
            ParamField(path="[0].reason", key="原因", category="user_param", source_kind="user_input"),
            ParamField(path="[0].approver1", key="审批人1", type="enum", category="user_param",
                       source_kind="api_option", source={"source_step_id": "users", "source_url": users.path}),
            ParamField(path="[0].approver2", key="审批人2", type="enum", category="user_param",
                       source_kind="api_option", source={"source_step_id": "users", "source_url": users.path}),
        ],
    )
    spec = FlowSpec(
        flow_id="leave-complete",
        steps=[process, users, approval, leave_page, submit],
        capabilities=[
            FlowCapability(
                name="query_status", kind="query_status",
                nodes=_call_nodes(["users", "leave-page"]), confirmed=True,
                output_mapping=[
                    {"name": "records", "step_id": "users", "response_path": "data.list"},
                    {"name": "records_2", "step_id": "leave-page", "response_path": "data.list"},
                ],
            ),
            FlowCapability(
                name="submit", kind="submit",
                nodes=_call_nodes(["process", "approval", "submit"]), confirmed=True,
                input_schema={"type": "object", "properties": {
                    "entries": {"type": "array", "items": {"type": "object", "properties": {
                        "审批人1": {"type": "string"}, "审批人2": {"type": "string"},
                    }}},
                    "processDefinitionId": {"type": "string"},
                }},
            ),
        ],
    )

    synced = flow_spec_module._sync_capability_io_schemas(spec)
    query = next(cap for cap in synced.capabilities if cap.name == "query_status")
    write = next(cap for cap in synced.capabilities if cap.name == "submit")

    assert query.step_ids == ["leave-page"]
    assert set(query.output_schema["properties"]) == {"records", "total"}
    assert "username" not in json.dumps(query.output_schema, ensure_ascii=False)
    assert "entries" not in write.input_schema["properties"]
    assert "processDefinitionId" not in write.input_schema["properties"]
    assert set(write.input_schema["properties"]) == {"类型", "原因", "审批人1", "审批人2"}
    process_param = approval.params[0]
    assert process_param.category == "system_const"
    assert process_param.source_kind == "constant"
    assert not synced.links


def test_query_list_response_uses_records_and_total_output_contract():
    step = FlowStep(
        step_id="query",
        method="GET",
        path="/leave/page",
        response_json={"data": {"list": [{"id": "1", "status": 1}], "total": 1}},
    )
    mappings = flow_spec_module._query_output_mappings([step])
    assert {(item["name"], item["response_path"]) for item in mappings} == {
        ("records", "data.list"), ("total", "data.total"),
    }


def test_incremental_orchestration_keeps_enum_in_existing_capability():
    spec = FlowSpec(
        flow_id="incremental-enum",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            params=[ParamField(
                path="sealId",
                key="印章",
                value="seal-1",
                type="enum",
                category="user_param",
                source_kind="api_option",
                enum_options=[{"label": "行政公章", "value": "seal-1"}],
                enum_value_map={"行政公章": "seal-1"},
            )],
        )],
        capabilities=[FlowCapability(
            name="submit_seal",
            kind="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))

    assert {cap.kind for cap in out.capabilities} == {"submit"}
    assert out.capabilities[0].step_ids == ["submit"]


def test_publish_issue_groups_deduplicate_same_link_and_classify_diagnostics():
    source = FlowStep(
        step_id="source",
        method="GET",
        url="/source",
        path="/source",
        response_json={"data": {"id": "TASK-001"}},
    )
    target = FlowStep(
        step_id="target",
        method="POST",
        url="/target",
        path="/target",
        body_source='{"taskId":"TASK-001"}',
        params=[ParamField(
            path="taskId",
            key="taskId",
            value="TASK-001",
            category="runtime_var",
            source_kind="previous_response",
            source={"kind": "previous_response", "step_id": "source", "response_path": "data.id"},
        )],
        success_rule={"kind": "http_status", "values": [200]},
    )
    link = FlowLink(
        link_id="same-link",
        source_step_id="source",
        source_path="data.id",
        target_step_id="target",
        target_path="taskId",
        confirmed=False,
        confidence=0.85,
    )
    spec = FlowSpec(
        flow_id="issue-groups",
        steps=[source, target],
        links=[link],
        diagnostics=[
            {"type": "console", "level": "error", "message": "Failed to load: net::ERR_CONNECTION_CLOSED"},
            {"type": "pageerror", "message": "render failed"},
        ],
    )
    spec.review_items = flow_spec_module.build_review_items(spec)

    report = validate_flow_spec(spec)
    assert not any(report["issue_groups"].values())
    assert any("same-link" in item for item in report["suggestions"])
    assert any("页面异常" in item for item in report["suggestions"])
    assert not any("ERR_CONNECTION_CLOSED" in item for item in report["suggestions"])


def test_request_builder_issue_has_flow_anchor_without_waiver_protocol():
    grouped = flow_spec_module._publish_issue_groups(["无法构造请求"], [])
    item = grouped["execution"][0]

    assert item["target"] == {"kind": "flow"}
    assert item["blocking"] is True
    assert "ignorable" not in item
    assert "ignored" not in item


def test_remove_capability_cleans_relations_and_scopes_publish_findings():
    keep_step = FlowStep(
        step_id="keep",
        method="GET",
        url="/api/keep",
        path="/api/keep",
        params=[ParamField(
            path="query.keep",
            key="keep",
            value="yes",
            category="user_param",
            source_kind="user_input",
            required=True,
        )],
    )
    removed_step = FlowStep(
        step_id="removed",
        method="GET",
        url="/api/removed",
        path="/api/removed",
        params=[ParamField(
            path="query.runtimeId",
            key="runtimeId",
            value="old-id",
            category="runtime_var",
            source_kind="unknown",
            required=True,
            need_human_confirm=True,
        )],
    )
    spec = FlowSpec(
        flow_id="remove-scope",
        steps=[keep_step, removed_step],
        capabilities=[
            FlowCapability(
                name="keep_cap",
                kind="query_status",
                nodes=[{"id": "call_keep", "type": "call", "step_id": "keep"}],
            ),
            FlowCapability(
                name="removed_cap",
                kind="query_status",
                nodes=[{"id": "call_removed", "type": "call", "step_id": "removed"}],
            ),
        ],
        capability_relations=[CapabilityRelation(
            relation_id="rel-1",
            type="output_to_input",
            from_capability="removed_cap",
            from_output="runtimeId",
            to_capability="keep_cap",
            to_input="keep",
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    edited = apply_flow_edits(spec, [{"op": "remove_capability", "capability_name": "removed_cap"}])
    report = validate_flow_spec(edited)

    assert [cap.name for cap in edited.capabilities] == ["keep_cap"]
    assert edited.capability_relations == []
    assert "keep" in (report["api_preview"]["params"] or [])
    assert "runtimeId" not in (report["api_preview"]["params"] or [])
    removed_reviews = [
        item for item in report["review_items"]
        if (item.get("target") or {}).get("step_id") == "removed"
    ]
    assert [item["type"] for item in removed_reviews] == ["field_source_unknown"]
    assert removed_reviews[0]["blocking"] is False
    assert removed_reviews[0]["ignorable"] is True
    assert any(
        item.get("review_id") == removed_reviews[0]["id"]
        for item in report["issue_groups"].get("field", [])
    )
    assert "runtimeId" not in "\n".join(report["errors"] + report["warnings"])


def test_publish_validation_does_not_promote_generated_field_advice_to_issues():
    spec = FlowSpec(
        flow_id="issue-groups",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            body_source='{"type":"2"}',
            params=[ParamField(
                path="body.type",
                key="type",
                value="2",
                type="enum",
                category="user_param",
                source_kind="manual_enum",
                enum_options=["1", "2"],
                required=True,
            )],
        )],
        capabilities=[FlowCapability(name="submit", kind="submit", nodes=_call_nodes(["submit"]), confirmed=True)],
        meta={"capability_model": {"status": "ready"}},
    )

    report = validate_flow_spec(spec)

    assert report["passed"] is True
    assert "field" not in report["issue_groups"]
    assert any("枚举字段" in item for item in report["suggestions"])


def test_publish_validation_keeps_generator_advice_outside_issue_groups():
    spec = FlowSpec(
        flow_id="internal-advice",
        steps=[FlowStep(step_id="query", method="GET", url="/api/query", path="/api/query")],
        capabilities=[FlowCapability(
            name="query_status",
            kind="query_status",
            nodes=[{"id": "call_query", "type": "call", "step_id": "query"}],
            confirmed=True,
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    report = validate_flow_spec(spec)
    assert report["passed"] is True
    assert not [
        item for group in report["issue_groups"].values() for item in group
        if item.get("source") == "validator"
    ]
    assert report["suggestions"]


def test_capability_map_accepts_quoted_constants_and_compiled_values():
    step = FlowStep(
        step_id="submit",
        method="POST",
        url="/api/submit",
        path="/api/submit",
        body_source='{"billType":"oa_duty_leave","processDefKey":"oa_duty_leave",'
                    '"activityId":"StartUserNode","processVariablesStr":"{\\"day\\":2}"}',
        params=[
            ParamField(path="billType", key="billType", value="oa_duty_leave", category="system_const", source_kind="constant"),
            ParamField(path="processDefKey", key="processDefKey", value="oa_duty_leave", category="system_const", source_kind="constant"),
            ParamField(path="activityId", key="activityId", value="StartUserNode", category="system_const", source_kind="constant"),
            ParamField(path="processVariablesStr", key="processVariablesStr", value='{"day":2}', category="system_const", source_kind="constant"),
        ],
        success_rule={"kind": "http_status", "values": [200]},
    )
    spec = FlowSpec(
        flow_id="literal-map",
        steps=[step],
        capabilities=[FlowCapability(
            name="submit",
            kind="submit",
            nodes=[
                {"id": "map_billType", "type": "map", "source": "'oa_duty_leave'", "target": "submit.billType"},
                {"id": "map_processDefKey", "type": "map", "source": '"oa_duty_leave"', "target": "submit.processDefKey"},
                {"id": "map_activityId", "type": "map", "source": "'StartUserNode'", "target": "submit.activityId"},
                {"id": "map_processVariablesStr", "type": "map", "source": 'computed:{"day":2}', "target": "submit.processVariablesStr"},
                {"id": "call_submit", "type": "call", "step_id": "submit"},
            ],
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
            confirmed=True,
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    report = validate_flow_spec(spec)

    assert not any("map 节点" in message and "来源" in message and "不存在" in message for message in report["errors"])


def _enum_policy_spec(param: ParamField, *, confirmed: bool = False) -> FlowSpec:
    return FlowSpec(
        flow_id="enum-policy",
        steps=[FlowStep(
            step_id="query", method="GET", url="/api/apply/page", path="/api/apply/page",
            params=[param], response_json={"data": {"list": []}}, success_rule={"path": "data.list"},
        )],
        capabilities=[FlowCapability(
            name="query_status", kind="query_status",
            nodes=[{"id": "call_query", "type": "call", "step_id": "query"}],
            output_mapping=[{"kind": "final_response", "step_id": "query", "response_path": "response"}],
            confirmed=confirmed, requires_human_confirm=not confirmed,
        )],
        meta={"capability_model": {"status": "ready"}},
    )


def test_manual_enum_guess_does_not_veto_operator_confirmation():
    spec = _enum_policy_spec(ParamField(
        path="query.processStatus", key="流程状态", value="1", type="enum",
        category="user_param", source_kind="manual_enum",
        # Simulates a client turning label-only DOM text into value=label.
        enum_options=[
            {"label": "未提交", "value": "未提交"},
            {"label": "审批中", "value": "审批中"},
        ],
        enum_value_map={"未提交": "未提交", "审批中": "审批中"},
        source={"enum_confirmed": True},
    ))

    confirmed = apply_flow_edits(spec, [{
        "op": "update_capability", "capability_index": 0,
        "field": "confirmed", "value": True,
    }])
    assert confirmed.capabilities[0].confirmed is True


def test_change_enum_source_only_preserves_type_and_recorded_option_evidence():
    spec = _enum_policy_spec(ParamField(
        path="query.useInfo", key="使用描述", value="12", type="enum", wire_type="string",
        category="user_param", source_kind="api_option",
        enum_options=[{"label": "测试", "value": "7"}], enum_value_map={"测试": "7"},
    ))
    spec.steps[0].selects = [SelectBinding(
        param="使用描述", path="query.useInfo", source_url="/api/options",
        value_key="id", label_key="name", enum_source="api", enum_confirmed=True,
    )]

    edited = apply_flow_edits(spec, [{
        "op": "update", "step_id": "query", "param_path": "query.useInfo",
        "field": "source_kind", "value": "user_input", "actor": "user",
    }])
    param = edited.steps[0].params[0]

    assert (param.type, param.source_kind) == ("enum", "user_input")
    assert param.enum_options == [{"label": "测试", "value": "7"}]
    assert param.enum_value_map == {"测试": "7"}
    assert len(edited.steps[0].selects) == 1
    assert "枚举字段" not in "\n".join(validate_flow_spec(edited)["errors"])
    api_request, errors = flow_spec_to_api_request(edited)
    assert errors == []
    assert api_request["selects"] == []


def test_get_query_export_keeps_confirmed_runtime_enum_binding():
    spec = _enum_policy_spec(ParamField(
        path="query.processStatus", key="流程状态", value="1", type="string", wire_type="string",
        category="user_param", source_kind="api_option",
        enum_options=[
            {"label": "未提交", "value": "0"},
            {"label": "审批中", "value": "1"},
        ],
        enum_value_map={"未提交": "0", "审批中": "1"},
        source={"kind": "api_option", "source_url": "/api/dict", "enum_confirmed": True},
    ))
    spec.steps[0].selects = [SelectBinding(
        param="流程状态", path="query.processStatus", source_url="/api/dict",
        value_key="value", label_key="label", category_key="dictType",
        category_value="process_status", enum_source="api", enum_confirmed=True,
        options=[
            {"label": "未提交", "value": "0"},
            {"label": "审批中", "value": "1"},
        ],
        option_map={"未提交": "0", "审批中": "1"},
    )]

    api_request, errors = flow_spec_to_api_request(spec)

    assert errors == []
    assert api_request["method"] == "GET"
    assert api_request["selects"] == [{
        "param": "流程状态",
        "path": "query.processStatus",
        "source_url": "/api/dict",
        "source_method": "GET",
        "source_headers": {},
        "source_content_type": "",
        "source_role": "",
        "source_request_id": "",
        "value_key": "value",
        "label_key": "label",
        "category_key": "dictType",
        "category_value": "process_status",
        "multi": False,
        "count": 0,
        "options": [
            {"label": "未提交", "value": "0"},
            {"label": "审批中", "value": "1"},
        ],
        "option_map": {"未提交": "0", "审批中": "1"},
        "enum_source": "api",
        "enum_confirmed": True,
    }]


def test_executable_partial_page_enum_is_only_a_generation_suggestion():
    spec = _enum_policy_spec(ParamField(
        path="query.processStatus", key="流程状态", value="审批中", type="enum",
        category="user_param", source_kind="page_enum",
        enum_options=["未提交", "审批中"],
        source={"enum_confirmed": False},
    ))
    report = validate_flow_spec(spec)
    assert report["passed"] is True
    assert not any("快照" in item for item in report["errors"])
    assert any("快照" in item for item in report["suggestions"])
    assert not any(
        item.get("code") == "enum_snapshot_incomplete"
        for items in report["issue_groups"].values() for item in items
    )


def test_suspected_internal_value_is_not_a_publish_issue():
    spec = _enum_policy_spec(ParamField(
        path="query.billCode", key="单据编号", value="11", type="string",
        category="user_param", source_kind="user_input", exposed_to_user=True,
    ), confirmed=True)
    report = validate_flow_spec(spec)
    assert report["passed"] is True
    assert any("内部 ID/短码" in item for item in report["suggestions"])
    assert not any(
        item.get("code") == "suspected_internal_value"
        for items in report["issue_groups"].values() for item in items
    )


def test_inferred_enum_mapping_does_not_block_publish_or_confirmation():
    spec = _enum_policy_spec(ParamField(
        path="query.processStatus", key="流程状态", value="1", type="enum",
        category="user_param", source_kind="page_enum",
        enum_options=["未提交", "审批中"], source={"enum_confirmed": False},
    ))
    report = validate_flow_spec(spec)
    assert report["passed"] is True
    assert any("枚举字段" in item for item in report["suggestions"])
    assert not any(
        item.get("code") == "enum_mapping_missing"
        for items in report["issue_groups"].values() for item in items
    )
    confirmed = apply_flow_edits(spec, [{
        "op": "update_capability", "capability_index": 0,
        "field": "confirmed", "value": True,
    }])
    assert confirmed.capabilities[0].confirmed is True


# ── Type inference ──
def test_infer_type_number():
    assert _infer_type_from_value("123") == "number"


def test_infer_type_boolean():
    assert _infer_type_from_value("true") == "boolean"


def test_infer_type_date():
    assert _infer_type_from_value("2024-01-01") == "date"


def test_infer_type_datetime():
    assert _infer_type_from_value("2024-01-01T12:00:00") == "datetime"


def test_infer_type_string():
    assert _infer_type_from_value("hello") == "string"
    assert _infer_type_from_value(None) == "string"


def test_array_first_row_dependency_is_replaced_by_grounded_option_binding():
    history = FlowStep(
        step_id="history",
        method="GET",
        path="/admin-api/oa/seal-apply/page?pageNo=1&pageSize=10",
        source_meta={"role": "business_get"},
        response_json={
            "data": {
                "list": [{"sealId": "seal-1", "applyTitle": "old record"}],
                "total": 1,
            },
        },
    )
    submit_param = ParamField(
        path="sealId",
        key="sealId",
        label="sealId",
        value="seal-1",
        category="runtime_var",
        source_kind="previous_response",
        source={
            "kind": "previous_response",
            "step_id": "history",
            "response_path": "data.list[0].sealId",
            "link_id": "bad-link",
        },
        exposed_to_user=False,
    )
    submit = FlowStep(
        step_id="submit",
        method="POST",
        path="/admin-api/oa/seal-apply/submit-process",
        source_meta={"role": "submit_anchor"},
        params=[submit_param],
        response_json={"code": 0, "data": "ok"},
    )
    options = FlowStep(
        step_id="seal-options",
        method="GET",
        path="/admin-api/bd/seal/simple-list?status=0",
        source_meta={"role": "business_get"},
        params=[ParamField(
            path="query.status",
            key="status",
            value="0",
            category="system_const",
            source_kind="constant",
            exposed_to_user=False,
        )],
        response_json={
            "code": 0,
            "data": [
                {"id": "seal-1", "name": "Company Seal", "status": 0, "remark": ""},
                {"id": "seal-2", "name": "Finance Seal", "status": 0, "remark": ""},
            ],
        },
    )
    spec = FlowSpec(
        flow_id="grounded-option-repair",
        steps=[history, submit, options],
        links=[FlowLink(
            link_id="bad-link",
            source_step_id="history",
            source_path="data.list[0].sealId",
            target_step_id="submit",
            target_path="sealId",
            confirmed=False,
            confidence=0.85,
            reason="录制值与上游响应唯一匹配，自动建立依赖",
            evidence={"kind": "value_match"},
        )],
    )

    flow_spec_module.rebuild_flow_dependencies(spec)
    repaired = flow_spec_module._repair_structural_option_bindings(spec)

    assert repaired == 1
    assert spec.links == []
    assert submit_param.category == "user_param"
    assert submit_param.source_kind == "api_option"
    assert submit_param.exposed_to_user is True
    assert submit_param.source["source_step_id"] == "seal-options"
    assert submit_param.source["value_key"] == "id"
    assert submit_param.source["label_key"] == "name"
    assert submit_param.enum_value_map == {
        "Company Seal": "seal-1",
        "Finance Seal": "seal-2",
    }

    spec.capabilities = build_default_flow_capabilities(spec)
    assert {cap.kind for cap in spec.capabilities} == {"query_status", "submit"}
    submit_capability = next(cap for cap in spec.capabilities if cap.kind == "submit")
    assert submit_capability.step_ids == ["submit"]
    flow_spec_module._attach_option_source_memberships(spec)
    source_ref = next(ref for ref in submit_capability.request_refs if ref.step_id == "seal-options")
    assert source_ref.usage == "option_source"
    assert "seal-options" not in submit_capability.step_ids

@pytest.mark.parametrize(
    ("target_path", "target_label", "source_path", "value_key", "label_key", "selected"),
    [
        ("projectId", "Project", "/gateway/projects/options", "id", "name", "project-2"),
        ("teamId", "Team", "/api/teams/simple-list", "id", "displayName", "team-2"),
        ("workTypeCode", "Work Type", "/v2/work-types/candidates", "code", "label", "type-2"),
        ("approverId", "Approver", "/system/users/select", "userId", "nickName", "user-2"),
    ],
)
def test_structural_option_binding_generalizes_across_business_domains(
    target_path,
    target_label,
    source_path,
    value_key,
    label_key,
    selected,
):
    rows = [
        {value_key: selected.replace("-2", "-1"), label_key: f"{target_label} A"},
        {value_key: selected, label_key: f"{target_label} B"},
    ]
    target_param = ParamField(
        path=target_path,
        key=target_label,
        label=target_label,
        value=selected,
        type="string",
        wire_type="string",
        category="user_param",
        source_kind="user_input",
        exposed_to_user=True,
        evidence=[{
            "kind": "page_control",
            "control_kind": "select",
            "editable": True,
            "disabled": False,
            "read_only": False,
        }],
    )
    target = FlowStep(
        step_id="write",
        method="POST",
        path="/api/business/submit",
        params=[target_param],
        source_meta={"role": "submit_anchor"},
    )
    source = FlowStep(
        step_id="options",
        method="GET",
        path=source_path,
        response_json={"data": rows},
        source_meta={"role": "business_get"},
    )
    spec = FlowSpec(flow_id=f"generic-{target_path}", steps=[target, source])

    assert flow_spec_module._repair_structural_option_bindings(spec) == 1
    assert target_param.key == target_label
    assert target_param.type == "enum"
    assert target_param.wire_type == "string"
    assert target_param.category == "user_param"
    assert target_param.source_kind == "api_option"
    assert target_param.source["source_step_id"] == "options"
    assert target_param.source["value_key"] == value_key
    assert target_param.source["label_key"] == label_key
    assert target_param.enum_value_map[f"{target_label} B"] == selected

def test_latest_leave_recording_binds_full_dictionary_and_captured_user_directory():
    submit = FlowStep(
        step_id="submit",
        method="POST",
        path="/admin-api/oa/leave/submit-process",
        source_meta={"role": "submit_anchor", "page_id": "leave-page"},
        params=[
            ParamField(
                path="type", key="请假类型", label="请假类型", value=2,
                type="number", wire_type="number", category="user_param",
                source_kind="user_input", exposed_to_user=True,
            ),
            ParamField(
                path="startUserSelectAssignees.Activity_approve[0]",
                key="审批人", label="审批人", value=149,
                type="number", wire_type="number", category="user_param",
                source_kind="user_input", exposed_to_user=True,
            ),
            ParamField(
                path="startUserSelectAssignees.Activity_hr[0]",
                key="审批人", label="审批人", value=144,
                type="number", wire_type="number", category="user_param",
                source_kind="user_input", exposed_to_user=True,
            ),
        ],
    )
    dictionary_rows = [
        {"dictType": "system_user_sex", "value": 1, "label": "男"},
        {"dictType": "system_user_sex", "value": 2, "label": "女"},
        {"dictType": "oa_duty_leave_type", "value": 1, "label": "病假"},
        {"dictType": "oa_duty_leave_type", "value": 2, "label": "事假"},
        {"dictType": "oa_duty_leave_type", "value": 3, "label": "婚假"},
    ]
    users = [
        {"id": 149, "nickname": "hunk", "status": 0, "createTime": 1784419173000, "remark": ""},
        {"id": 144, "nickname": "姜楠", "status": 0, "createTime": 1784419174000, "remark": ""},
    ]
    facts = RequestFacts(
        requests=[
            flow_spec_module.RequestFact(
                request_id="req_10", request_index=10, sequence=10, method="GET",
                path="/admin-api/system/dict-data/simple-list",
                response_json={"code": 0, "data": dictionary_rows},
            ),
            flow_spec_module.RequestFact(
                request_id="req_68", request_index=68, sequence=68, method="GET",
                path="/admin-api/system/user/page?pageNo=1&pageSize=100",
                response_json={"code": 0, "data": {"list": users, "total": 2}},
            ),
            flow_spec_module.RequestFact(
                request_id="req_70", request_index=70, sequence=70, method="GET",
                path="/admin-api/system/user/page?pageNo=1&pageSize=100",
                response_json={"code": 0, "data": {"list": users, "total": 2}},
            ),
        ],
        analysis={
            "req_10": flow_spec_module.RequestAnalysis(request_id="req_10", role="read_option", confidence=0.99),
            # Reproduce the stale misclassification observed in the real log.
            "req_68": flow_spec_module.RequestAnalysis(request_id="req_68", role="business_get", confidence=0.9),
            "req_70": flow_spec_module.RequestAnalysis(request_id="req_70", role="business_get", confidence=0.9),
        },
        option_sources=[{
            "kind": "page_enum_options",
            "options": {
                "label=请假类型": {
                    "field_key": "请假类型",
                    "field_aliases": ["type"],
                    "control_kind": "select",
                    "mapping_complete": False,
                    "selected_label": "事假",
                    "page_id": "leave-page",
                    "options": [{"label": "病假"}, {"label": "事假"}, {"label": "婚假"}],
                },
            },
        }],
    )
    spec = FlowSpec(flow_id="latest-leave-log", steps=[submit], request_facts=facts)

    assert flow_spec_module._repair_structural_option_bindings(spec) == 3

    leave_type, first_approver, second_approver = submit.params
    assert leave_type.type == "enum"
    assert leave_type.wire_type == "number"
    assert leave_type.source_kind == "api_option"
    assert leave_type.source["source_request_id"] == "req_10"
    assert leave_type.source["category_key"] == "dictType"
    assert leave_type.source["category_value"] == "oa_duty_leave_type"
    assert leave_type.enum_value_map == {"病假": 1, "事假": 2, "婚假": 3}

    for approver, expected_label, expected_value in (
        (first_approver, "hunk", 149),
        (second_approver, "姜楠", 144),
    ):
        assert approver.type == "enum"
        assert approver.wire_type == "number"
        assert approver.source_kind == "api_option"
        assert approver.source["source_request_id"] == "req_70"
        assert approver.source["label_key"] == "nickname"
        assert approver.enum_value_map[expected_label] == expected_value

    spec.capabilities = build_default_flow_capabilities(spec)
    flow_spec_module._attach_option_source_memberships(spec)
    submit_capability = next(cap for cap in spec.capabilities if cap.kind == "submit")
    captured_option_refs = {
        ref.request_id for ref in submit_capability.request_refs
        if ref.usage == "option_source" and not ref.step_id
    }
    assert captured_option_refs == {"req_10", "req_70"}

def test_structural_option_binding_repairs_get_query_enum_from_recorded_facts():
    process_status = ParamField(
        path="query.processStatus", key="Process Status", label="Process Status", value=1,
        type="string", wire_type="number", category="user_param",
        source_kind="user_input", exposed_to_user=True,
    )
    query = FlowStep(
        step_id="query-applications", method="GET",
        path="/admin-api/oa/seal-apply/page?processStatus=1",
        source_meta={"role": "business_get", "page_id": "seal-list"},
        params=[process_status],
    )
    dictionary_rows = [
        {"dictType": "system_user_sex", "value": 1, "label": "Male"},
        {"dictType": "system_user_sex", "value": 2, "label": "Female"},
        {"dictType": "bpm_process_instance_status", "value": 0, "label": "Draft"},
        {"dictType": "bpm_process_instance_status", "value": 1, "label": "Pending"},
        {"dictType": "bpm_process_instance_status", "value": 2, "label": "Approved"},
        {"dictType": "bpm_process_instance_status", "value": 3, "label": "Rejected"},
        {"dictType": "bpm_process_instance_status", "value": 4, "label": "Cancelled"},
    ]
    facts = RequestFacts(
        requests=[flow_spec_module.RequestFact(
            request_id="req_8", method="GET",
            path="/admin-api/system/dict-data/simple-list",
            response_json={"code": 0, "data": dictionary_rows},
        )],
        analysis={"req_8": flow_spec_module.RequestAnalysis(
            request_id="req_8", role="read_option", confidence=0.99,
        )},
        option_sources=[{"kind": "page_enum_options", "options": {
            "label=Process Status": {
                "field_key": "Process Status", "field_aliases": ["processStatus"],
                "control_kind": "select", "mapping_complete": False,
                "selected_label": "Pending", "page_id": "seal-list",
                "options": [{"label": label} for label in (
                    "Draft", "Pending", "Approved", "Rejected", "Cancelled",
                )],
            },
        }}],
    )
    spec = FlowSpec(flow_id="get-query-enum-repair", steps=[query], request_facts=facts)

    assert flow_spec_module._repair_structural_option_bindings(spec) == 1
    assert (process_status.type, process_status.wire_type) == ("enum", "string")
    assert process_status.source_kind == "api_option"
    assert process_status.source["source_request_id"] == "req_8"
    assert process_status.source["category_value"] == "bpm_process_instance_status"
    assert process_status.enum_value_map == {
        "Draft": 0, "Pending": 1, "Approved": 2, "Rejected": 3, "Cancelled": 4,
    }


def test_sync_migrates_legacy_api_option_business_type_without_changing_wire_type():
    param = ParamField(
        path="type", key="请假类型", value=2,
        type="number", wire_type="number", category="user_param",
        source_kind="api_option", source={"kind": "api_option", "source_url": "/api/dict"},
    )
    step = FlowStep(
        step_id="submit", method="POST", path="/api/leave", params=[param],
        selects=[SelectBinding(
            param="请假类型", path="type", id_path="type",
            source_url="/api/dict", value_key="value", label_key="label",
            options=[{"label": "病假", "value": 1}, {"label": "事假", "value": 2}],
            option_map={"病假": 1, "事假": 2}, enum_source="api", enum_confirmed=True,
        )],
    )
    spec = FlowSpec(flow_id="legacy-api-option", steps=[step])

    flow_spec_module._sync_step_option_contracts(spec, step)

    assert param.type == "enum"
    assert param.wire_type == "number"
    assert param.source_kind == "api_option"
def test_r2_automated_field_resolution_requires_exact_step_path_identity():
    first = ParamField(path="body.customer.id", key="id", label="编号")
    second = ParamField(path="body.order.id", key="id", label="编号")
    spec = FlowSpec(steps=[FlowStep(step_id="submit", params=[first, second])])

    assert flow_spec_module._apply_capability_field_to_param(
        spec,
        {
            "step_id": "submit",
            "path": "id",
            "key": "客户编号",
            "display_name": "客户编号",
            "type": "string",
        },
        scope="input",
        actor="planner",
    ) is False
    assert [param.key for param in spec.steps[0].params] == ["id", "id"]

    assert flow_spec_module._apply_capability_field_to_param(
        spec,
        {
            "step_id": "submit",
            "path": "body.customer.id",
            "key": "客户编号",
            "display_name": "客户编号",
            "type": "string",
        },
        scope="input",
        actor="planner",
    ) is True
    assert [param.key for param in spec.steps[0].params] == ["客户编号", "id"]


def test_r2_manual_field_evidence_protects_only_its_owned_axis():
    param = ParamField(
        path="useTime",
        key="useTime",
        label="useTime",
        type="string",
        wire_type="number",
        required=True,
        category="system_const",
        source_kind="unknown",
        default_value=1784476800000,
        evidence=[{"source": "manual_edit", "field": "required", "value": True}],
    )
    spec = FlowSpec(steps=[FlowStep(step_id="submit", params=[param])])

    assert flow_spec_module._apply_capability_field_to_param(
        spec,
        {
            "step_id": "submit",
            "path": "useTime",
            "key": "使用日期",
            "display_name": "使用日期",
            "type": "datetime",
            "category": "user_param",
            "source_kind": "user_input",
            "required": False,
            "visible_default": "2026-07-20",
            "evidence": [{
                "source": "screenshot",
                "control_kind": "date",
                "editable": True,
                "required": False,
            }],
        },
        scope="input",
        actor="planner",
    ) is True

    assert param.key == "使用日期"
    assert (param.type, param.wire_type) == ("datetime", "number")
    assert param.category == "user_param"
    assert param.source_kind == "user_input"
    assert param.required is True
    assert param.default_value == 1784476800000


def test_r2_semantic_completion_does_not_resurrect_unreviewed_field_values():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        params=[
            ParamField(path="title", key="旧标题", category="user_param"),
            ParamField(path="tenantId", key="旧租户", category="system_const"),
        ],
    )])
    proposed = {
        "field_semantics": [{
            "step_id": "submit",
            "wire_path": "title",
            "public_name": "申请标题",
            "business_type": "string",
            "source_kind": "user_input",
            "confidence": 0.95,
        }],
        "request_roles": [],
        "capabilities": [],
        "capability_relations": [],
        "unresolved_items": [],
    }

    completed = flow_spec_module._complete_semantic_plan_from_spec(spec, proposed)
    assert [field["wire_path"] for field in completed["field_semantics"]] == ["title"]
    coverage = flow_spec_module._semantic_plan_coverage(
        spec, {"semantic_plan": completed},
    )
    assert coverage["total_fields"] == 2
    assert coverage["covered_fields"] == 1
    assert "field_semantics" in coverage["missing"]
