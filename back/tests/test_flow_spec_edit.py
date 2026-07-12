"""Step B · FlowSpec 字段/link/step 编辑测试。"""

import asyncio
import json
from pathlib import Path

import pytest
import dano.execution.page.flow_spec as flow_spec_module

from dano.execution.page.flow_spec import (
    FlowSpec, FlowStep, FlowLink, ParamField, SelectBinding, FlowCapability,
    CapabilityDependency, CapabilityField, CapabilityRelation,
    RequestFacts,
    apply_flow_edits, validate_flow_spec, _infer_type_from_value,
    refresh_review_items, flow_spec_to_api_request,
    capability_to_flow_spec_view, compile_capability_to_api_request, flow_spec_capability_contracts,
    flow_spec_to_client,
    auto_fix_flow_spec, orchestrate_flow_capabilities, run_recording_pi_loop, sync_flow_spec_models,
    flow_spec_canonical_summary, flow_spec_shadow_diff, migrate_v1_flow_spec_to_capability_spec,
    migrate_v2_flow_spec_to_capability_spec, capability_spec_to_legacy_flow_spec,
    capability_spec_to_api_request, to_flow_spec,
    build_default_flow_capabilities, prepare_flow_spec_for_publish, prepare_flow_release_candidate,
)
from dano.execution.page.request_capture import execute_api_request


def _make_spec():
    param1 = ParamField(path="form.userId", key="userId", value="123", type="string", required=True)
    param2 = ParamField(path="form.name", key="name", value="test", type="string", required=True)
    step1 = FlowStep(
        step_id="step1", method="POST", url="/api/submit", path="/api/submit",
        params=[param1, param2], risk_level="L3", sample_inputs={"userId": "123", "name": "test"},
    )
    return FlowSpec(flow_id="test", steps=[step1])


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


def test_capability_interface_reorder_updates_flat_calls_even_with_return_node():
    spec = FlowSpec(
        flow_id="order",
        steps=[
            FlowStep(step_id="a", method="GET", path="/a"),
            FlowStep(step_id="b", method="POST", path="/b"),
        ],
        capabilities=[FlowCapability(
            name="submit",
            step_ids=["a", "b"],
            nodes=[
                {"id": "call_a", "type": "call", "step_id": "a"},
                {"id": "call_b", "type": "call", "step_id": "b"},
                {"id": "return_b", "type": "return", "from": "b"},
            ],
        )],
    )

    updated = apply_flow_edits(spec, [{
        "op": "update_capability",
        "capability_index": 0,
        "field": "step_ids",
        "value": ["b", "a"],
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
                name="query_status", kind="query_status", step_ids=["tenant-options"],
                nodes=[{"id": "call_option", "type": "call", "step_id": "tenant-options"}],
                output_mapping=[{"step_id": "old-step", "response_path": "response"}],
                evidence=[{"kind": "read_step", "step_id": "tenant-options"}],
                confirmed=True,
            ),
            FlowCapability(
                name="submit_batch", title="批量提交业务申请", kind="submit_batch",
                step_ids=["leave-submit"],
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
            step_ids=["submit"],
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
    assert before["passed"] is False
    assert any("没有批量接口事实" in error for error in before["errors"])

    fixed = asyncio.run(run_recording_pi_loop(spec, llm_client=None, model=None, mode="repair"))

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


def test_manual_category_change_removes_conflicting_incoming_link():
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
    assert edited.links == []
    assert param.category == "user_param"
    assert param.source_kind == "user_input"
    assert param.editable is True


def test_capability_input_schema_drops_deleted_fields():
    spec = _make_spec()
    spec.capabilities = [FlowCapability(
        name="submit", title="提交", kind="submit", step_ids=["step1"], confirmed=True,
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


def test_unconfirmed_capabilities_block_publish_validation():
    spec = _make_spec()
    spec.capabilities = [FlowCapability(
        name="submit", title="提交", kind="submit", step_ids=["step1"], confirmed=False,
    )]

    report = validate_flow_spec(spec)

    assert report["passed"] is False
    assert any("已确认能力" in message for message in report["errors"])


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
    assert param.source_kind == "api_option"
    assert param.enum_value_map == {"病假": "1"}
    assert new.steps[1].selects[0].source_url == "/api/dict/type"
    assert new.steps[1].selects[0].value_key == "value"
    assert new.steps[1].selects[0].label_key == "label"


def test_capability_loop_and_return_edits():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[FlowCapability(name="submit_batch", kind="submit", step_ids=["submit"])],
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
                step_ids=["query"],
                output_schema={"type": "object", "properties": {"missing_dates": {"type": "array"}}},
            ),
            FlowCapability(name="submit_batch", kind="submit_batch", step_ids=["submit"]),
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
            step_ids=["submit"],
            input_schema={"type": "object", "properties": {"entries": {"type": "array"}}},
            nodes=[
                {"id": "bad_condition", "type": "condition", "condition": "input.missing.length > 0", "then": []},
                {"id": "bad_map", "type": "map", "source": "input.unknown", "target": "submit.nope"},
            ],
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
        )],
    )

    report = validate_flow_spec(spec)

    text = "\n".join(report["errors"] + report["warnings"])
    assert "引用的输入 `missing` 不存在" in text
    assert "来源 `input.unknown` 不存在" not in text


class _FakeFixClient:
    async def complete_json(self, **_kwargs):
        return {"ops": [
            {"op": "upsert_input_field", "capability": "submit_batch", "field": {
                "key": "entries", "type": "array", "required": True,
            }},
            {"op": "set_map", "capability": "submit_batch", "node": {
                "id": "map_entries", "source": "input.entries", "target": "var.entries",
            }},
            {"op": "set_output_mapping", "capability": "submit_batch", "mapping": [{
                "kind": "final_response", "step_id": "submit", "response_path": "response",
            }]},
        ]}


def test_auto_fix_accepts_capability_scoped_patch_ops_from_llm():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[FlowCapability(name="submit_batch", kind="submit_batch", step_ids=["submit"])],
    )

    fixed = asyncio.run(auto_fix_flow_spec(spec, llm_client=_FakeFixClient(), model="fake", max_rounds=1))

    cap = fixed.capabilities[0]
    assert cap.inputs[0].key == "entries"
    assert any(n.get("id") == "map_entries" for n in cap.nodes)
    assert cap.output_mapping[0]["step_id"] == "submit"


class _FakePlannerPatchClient:
    async def complete_json(self, **_kwargs):
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
        steps=[FlowStep(step_id="submit", method="POST", url="/api/report", path="/api/report")],
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec, llm_client=_FakePlannerPatchClient(), model="fake"))

    assert out.meta["capability_model"]["source"] == "llm_patch"
    assert {cap.name for cap in out.capabilities} == {"submit_batch"}
    cap = out.capabilities[0]
    assert cap.kind == "submit_batch"
    assert cap.step_ids == ["submit"]
    assert cap.inputs[0].key == "entries"
    assert cap.output_mapping[0]["step_id"] == "submit"


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
            step_ids=["submit"],
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )

    fixed = asyncio.run(auto_fix_flow_spec(spec, max_rounds=1))
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
        meta={"request_graph": {"all_requests": [
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
        ]}},
    )

    one = apply_flow_edits(spec, [{"op": "add_request_step", "request_index": 1, "request_id": "r1"}])
    two = apply_flow_edits(one, [{"op": "add_request_step", "request_index": 2, "request_id": "r1"}])

    assert len(two.steps) == 1
    assert two.steps[0].path == "/admin-api/bpm/process-definition/get"


def test_request_facts_are_first_class_and_sync_with_legacy_request_graph():
    legacy_entry = _request_fact_entry(request_id="req-status", request_index=11, sequence=11)
    legacy = FlowSpec(
        flow_id="legacy-request-graph",
        meta={"request_graph": {"all_requests": [legacy_entry], "candidate_reads": [legacy_entry]}},
    )

    assert legacy.request_facts.protocol == "dano.request_facts.v1"
    assert [r.request_id for r in legacy.request_facts.requests] == ["req-status"]
    assert legacy.request_facts.analysis["req-status"].bucket == "candidate_reads"

    client = flow_spec_to_client(legacy)
    assert client["request_facts"]["requests"][0]["request_id"] == "req-status"
    assert client["meta"]["request_graph"]["candidate_reads"][0]["request_id"] == "req-status"

    modern_entry = _request_fact_entry(request_id="req-options", request_index=12, sequence=12, role="read_option")
    modern = FlowSpec(
        flow_id="modern-request-facts",
        request_facts={
            "requests": [modern_entry],
            "analysis": {
                "req-options": {
                    "request_id": "req-options",
                    "role": "read_option",
                    "keep": True,
                    "bucket": "candidate_reads",
                    "confidence": 0.91,
                    "reason": "候选项读取",
                }
            },
        },
    )

    graph = modern.meta["request_graph"]
    assert graph["all_requests"][0]["request_id"] == "req-options"
    assert graph["candidate_reads"][0]["request_id"] == "req-options"


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
        },
    ]
    page_enums = {"type": {"options": [{"label": "病假", "value": "2"}], "option_map": {"病假": "2"}}}

    spec = to_flow_spec(captured, page_enum_options=page_enums)

    ids = {fact.request_id for fact in spec.request_facts.requests}
    assert "post-1" in ids
    assert "css-1" not in ids
    assert "js-1" not in ids
    assert spec.request_facts.option_sources
    assert spec.request_facts.option_sources[0]["options"] == page_enums


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
            step_ids=["submit"],
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
            "op": "update_capability",
            "capability_name": "submit_batch",
            "field": "request_fields",
            "value": scoped_fields,
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
    assert new.steps[0].sample_inputs["userId"] == "456"


def test_edit_type():
    new = apply_flow_edits(_make_spec(), [{"op": "update", "step_id": "step1",
                                           "param_path": "form.userId", "field": "type", "value": "number"}])
    assert new.steps[0].params[0].type == "number"


def test_edit_type_to_string_atomically_removes_enum_contract():
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
    assert param.source_kind == "user_input"
    assert param.enum_options is None
    assert param.enum_value_map is None
    assert param.reason == "字段已改为普通输入，不再使用旧枚举候选"
    assert param.description == "业务类型"
    assert new.steps[0].selects == []


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


def test_add_param():
    new = apply_flow_edits(_make_spec(), [{"op": "add", "step_id": "step1", "param": {
        "path": "form.email", "key": "email", "value": "test@example.com",
        "type": "string", "required": False}}])
    assert len(new.steps[0].params) == 3
    assert new.steps[0].sample_inputs["email"] == "test@example.com"


def test_remove_param():
    new = apply_flow_edits(_make_spec(), [{"op": "remove", "step_id": "step1", "param_path": "form.name"}])
    assert len(new.steps[0].params) == 1
    assert "name" not in new.steps[0].sample_inputs


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
        FlowCapability(name="query_status", kind="query_status", step_ids=["A"]),
        FlowCapability(name="submit_batch", kind="submit_batch", step_ids=["B"]),
    ]

    new = apply_flow_edits(spec, [{"op": "reorder_capabilities", "capability_names": ["submit_batch", "query_status"]}])

    assert [c.name for c in new.capabilities] == ["submit_batch", "query_status"]
    assert [c.name for c in spec.capabilities] == ["query_status", "submit_batch"]


def test_reorder_capabilities_missing_raises():
    spec = _three_step_spec()
    spec.capabilities = [
        FlowCapability(name="query_status", kind="query_status", step_ids=["A"]),
        FlowCapability(name="submit_batch", kind="submit_batch", step_ids=["B"]),
    ]

    with pytest.raises(ValueError, match="reorder_capabilities"):
        apply_flow_edits(spec, [{"op": "reorder_capabilities", "capability_names": ["query_status"]}])


def test_reorder_capabilities_by_capability_id_when_name_empty():
    spec = _three_step_spec()
    spec.capabilities = [
        FlowCapability(name="", capability_id="cap_a", kind="query_status", step_ids=["A"]),
        FlowCapability(name="", capability_id="cap_b", kind="submit_batch", step_ids=["B"]),
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
        capabilities=[FlowCapability(name="submit_batch", kind="submit_batch", step_ids=["opt", "query", "submit"])],
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
    assert before.category == "runtime_var"
    assert before.source_kind == "previous_response"
    assert before.editable is True

    new = apply_flow_edits(synced, [{"op": "remove", "link_id": "l1", "reset_target": True}])
    after = {p.path: p for p in new.steps[1].params}["y"]
    assert len(new.links) == 0
    assert after.category == "user_param"
    assert after.source_kind == "user_input"
    assert after.editable is True
    assert after.exposed_to_user is True


def test_reset_param_source_removes_incoming_link():
    spec = _two_step_spec_with_link()
    synced = apply_flow_edits(spec, [{"op": "update", "link_id": "l1", "field": "confirmed", "value": True}])
    new = apply_flow_edits(synced, [{"op": "reset_param_source", "step_id": "B", "param_path": "y", "to": "user_input"}])
    assert new.links == []
    param = {p.path: p for p in new.steps[1].params}["y"]
    assert param.category == "user_param"
    assert param.source_kind == "user_input"


def test_add_candidate_step_promotes_request_graph_entry():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="write", method="POST", url="/api/save", path="/api/save")],
        meta={
            "request_graph": {
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
            }
        },
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
    graph = new.meta["request_graph"]
    assert graph["candidate_reads"] == []
    assert graph["selected_steps"][0]["request_index"] == 7
    assert graph["selected_steps"][0]["state"] == "materialized"
    assert graph["selected_steps"][0]["materialized_step_id"] == promoted.step_id


def test_add_request_step_keeps_same_path_distinct_request_ids():
    spec = FlowSpec(
        flow_id="f",
        meta={"request_graph": {"all_requests": [
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
        ]}},
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
            step_ids=["write"],
            nodes=[{"id": "call_1", "type": "call", "step_id": "write"}],
            confirmed=True,
            requires_human_confirm=False,
        )],
        meta={"request_graph": {"all_requests": [{
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
        }]}}
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
            step_ids=["write"],
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
            step_ids=["read"],
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


def test_recording_v3_golden_shadow_and_adapters():
    fixture = Path(__file__).parent / "fixtures" / "recording_v3" / "daily_report_flow_spec.json"
    spec = FlowSpec.model_validate(json.loads(fixture.read_text(encoding="utf-8")))

    migrated = migrate_v2_flow_spec_to_capability_spec(spec)
    canonical = flow_spec_canonical_summary(migrated)
    shadow = flow_spec_shadow_diff(migrated)
    legacy_view = capability_spec_to_legacy_flow_spec(migrated, capability_name="submit_batch")
    scoped_api, scoped_errors = capability_spec_to_api_request(migrated, capability_name="submit_batch")

    assert canonical["protocol"] == "dano.recording_shadow.v1"
    assert canonical["request_facts"]["request_count"] == 2
    assert canonical["capabilities"][0]["name"] == "submit_batch"
    assert canonical["capabilities"][0]["node_types"] == ["call", "foreach", "call", "return"]
    assert canonical["summary_hash"]

    assert shadow["passed"] is True
    assert shadow["legacy"]["shape"]["capability_protocol"] == "dano.capability_plan.v1"
    assert shadow["capabilities"][0]["passed"] is True

    assert [s.step_id for s in legacy_view.steps] == ["query_missing", "submit_report"]
    assert scoped_errors == []
    assert scoped_api["selected_capability"]["name"] == "submit_batch"
    assert scoped_api["capability_contracts"][0]["execution_contract"]["batch"]["items_field"] == "entries"


def test_v2_migration_repairs_public_capability_name_kind_mismatch():
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="submit", method="POST", path="/submit", url="/submit",
            body_source='{"reason":"x"}',
            params=[ParamField(path="reason", key="原因")],
        )],
        capabilities=[FlowCapability(
            name="submit_batch", title="批量提交请假申请", kind="submit",
            step_ids=["submit"], nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )

    migrated = migrate_v2_flow_spec_to_capability_spec(spec)
    cap = migrated.capabilities[0]

    assert cap.name == "submit"
    assert cap.kind == "submit"
    assert "批量" not in cap.title
    assert "entries" not in (cap.input_schema.get("properties") or {})


def test_v1_adapter_generates_default_capability_without_request_facts():
    spec = FlowSpec(
        flow_id="legacy-v1",
        title="提交旧表单",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            body_source='{"reason":"old"}',
            params=[ParamField(path="reason", key="reason", value="old", type="string", required=True)],
            sample_inputs={"reason": "old"},
        )],
    )

    migrated = migrate_v1_flow_spec_to_capability_spec(spec)
    api_request, errors = capability_spec_to_api_request(migrated)

    assert errors == []
    assert migrated.capabilities
    assert migrated.request_facts.protocol == "dano.request_facts.v1"
    assert api_request["capability_protocol"] == "dano.capability_plan.v1"


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
        meta={"request_graph": {"all_requests": [{
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
        }]}}
    )

    fixed = asyncio.run(auto_fix_flow_spec(spec, llm_client=None, max_rounds=2))

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
            step_ids=["submit"],
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

    synced = sync_flow_spec_models(spec, prefer_request_facts=False)
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
                step_ids=[],
                nodes=[],
                confirmed=False,
            ),
            FlowCapability(
                name="query_status",
                kind="query_status",
                step_ids=[],
                nodes=[],
                confirmed=False,
            ),
            FlowCapability(
                name="submit_batch",
                kind="submit_batch",
                step_ids=["submit"],
                nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
                confirmed=False,
            ),
        ],
        meta={"request_graph": {"all_requests": [
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
        ]}},
    )

    fixed = asyncio.run(auto_fix_flow_spec(spec, llm_client=None, max_rounds=2))
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


def test_recording_pi_loop_records_planner_and_repair_history():
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

    out = asyncio.run(run_recording_pi_loop(spec, llm_client=None, model=None, mode="plan", max_rounds=2))

    assert out.capabilities
    assert out.meta["recording_pi_loop"]["mode"] == "plan"
    assert out.meta["recording_pi_loop"]["rounds"]


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
        meta={"request_graph": {"all_requests": [
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
        ]}},
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
            step_ids=["query", "stale-request-id"],
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
            step_ids=["read", "write"],
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

    assert report["passed"] is False
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
            step_ids=["submit"],
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
            step_ids=["submit"],
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
            step_ids=["submit"],
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


def test_confirmation_is_atomic_and_rejects_invalid_contract_before_state_change():
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
            step_ids=["submit"],
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            output_mapping=[{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
        )],
    )

    with pytest.raises(ValueError, match="能力确认失败"):
        apply_flow_edits(spec, [{
            "op": "update_capability", "capability_name": "submit",
            "field": "confirmed", "value": True,
        }])

    assert spec.capabilities[0].confirmed is False


def test_enum_to_scalar_rebuilds_capability_schema_without_stale_keywords():
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
            name="submit", kind="submit", step_ids=["submit"],
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
    assert edited.steps[0].selects == []


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


def test_manual_pinned_business_query_membership_survives_all_sync_layers():
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
        "pinned": True,
    }])
    prepared = prepare_flow_spec_for_publish(edited)

    capability = prepared.capabilities[0]
    assert capability.step_ids == ["records"]
    assert any(node.get("type") == "call" and node.get("step_id") == "records" for node in capability.nodes)
    assert capability.request_refs[0].pinned is True
    assert capability.request_refs[0].usage == "execute"


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
    assert [(ref.step_id, ref.usage, ref.pinned) for ref in capability.request_refs] == [
        ("options", "option_source", True),
    ]


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
        capabilities=[FlowCapability(name="submit", kind="submit", step_ids=["submit"])],
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


def test_strict_skill_level_blocks_missing_description_and_failure_handling():
    spec = FlowSpec(
        flow_id="f",
        meta={"publish_gate": True},
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            response_json={"code": 0},
        )],
        capabilities=[FlowCapability(
            name="submit_form",
            kind="submit",
            step_ids=["submit"],
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

    assert report["passed"] is False
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
                step_ids=["write"],
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
                step_ids=["write"],
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
                step_ids=["write"],
                nodes=[{"id": "call_write", "type": "call", "step_id": "write"}],
                output_schema={"type": "object", "properties": {"count": {"type": "number"}}},
                confirmed=True,
                requires_human_confirm=False,
            ),
            FlowCapability(
                name="submit_items",
                kind="submit",
                step_ids=["write"],
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

    assert report["passed"] is False
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
            step_ids=[],
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
            step_ids=["read", "submit"],
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
            step_ids=["read", "submit"],
            nodes=[
                {"id": "call_1", "type": "call", "step_id": "read"},
                {"id": "call_2", "type": "call", "step_id": "submit"},
            ],
        )],
    )

    edited = apply_flow_edits(spec, [{
        "op": "update_capability",
        "capability_name": "custom_order",
        "field": "step_ids",
        "value": ["submit", "read"],
    }])

    assert edited.capabilities[0].step_ids == ["submit", "read"]
    assert [n["step_id"] for n in edited.capabilities[0].nodes if n.get("type") == "call"] == ["submit", "read"]


def test_generate_capabilities_respects_removed_capability():
    spec = FlowSpec(
        flow_id="f",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[FlowCapability(name="submit_batch", kind="submit_batch", step_ids=["submit"])],
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
                step_ids=["status"],
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
                step_ids=["submit"],
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


def test_shadow_diff_detects_existing_step_missing_from_scoped_compile(monkeypatch):
    spec = _two_capability_compile_spec()
    real_compile = flow_spec_module.capability_spec_to_api_request

    def fake_compile(flow_spec, *args, **kwargs):
        api, errors = real_compile(flow_spec, *args, **kwargs)
        if kwargs.get("capability_id") and api:
            api = json.loads(json.dumps(api, ensure_ascii=False))
            api["steps"] = []
            api.pop("step_id", None)
            for cap in api.get("capabilities") or []:
                cap["compiled_step_ids"] = []
        return api, errors

    monkeypatch.setitem(flow_spec_shadow_diff.__globals__, "capability_spec_to_api_request", fake_compile)

    shadow = flow_spec_shadow_diff(spec)
    submit_report = next(x for x in shadow["capabilities"] if x["capability_id"] == "cap-submit")

    assert shadow["passed"] is False
    assert submit_report["missing_steps"] == ["submit"]


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
            step_ids=["read", "submit"],
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
        meta={
            "request_graph": {
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
            }
        },
    )

    new = apply_flow_edits(spec, [{"op": "add_candidate_step", "request_index": 7}])

    assert len(new.steps) == 1
    graph = new.meta["request_graph"]
    assert graph["candidate_reads"] == []
    assert graph["selected_steps"][0]["request_index"] == 7


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
    spec.steps[0].risk_level = "L4"
    spec = apply_flow_edits(spec, [])

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


def test_runtime_unknown_review_is_not_duplicated_as_field_category():
    spec = _make_spec()
    param = spec.steps[0].params[0]
    param.category = "runtime_var"
    param.source_kind = "unknown"
    param.need_human_confirm = True

    new = apply_flow_edits(spec, [])
    target_items = [i for i in new.review_items if i.target.get("path") == param.path]

    assert [i.type for i in target_items] == ["runtime_var_source"]
    assert target_items[0].severity == "high"


def test_orchestrate_existing_nonempty_capability_does_not_expand_from_defaults():
    spec = FlowSpec(
        flow_id="incremental-only",
        steps=[
            FlowStep(step_id="status", method="GET", url="/api/status", path="/api/status"),
            FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit"),
        ],
        capabilities=[FlowCapability(
            name="submit_batch",
            kind="submit_batch",
            step_ids=["submit"],
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            title="用户已经编辑的能力",
            updated_by="user",
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec, llm_client=None, model=None))

    assert len(out.capabilities) == 1
    assert out.capabilities[0].title == "用户已经编辑的能力"
    assert out.capabilities[0].step_ids == ["submit"]


class _ScopeExpandingPlanner:
    async def complete_json(self, **_kwargs):
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


class _DestructiveIncrementalPlanner:
    async def complete_json(self, **_kwargs):
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


def test_repeated_pi_loop_preserves_capabilities_interfaces_links_and_relations():
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
            FlowCapability(name="query_status", kind="query_status", step_ids=["query"],
                           nodes=[{"id": "call_query", "type": "call", "step_id": "query"}]),
            FlowCapability(name="submit", kind="submit", step_ids=["submit"],
                           nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}]),
        ],
        meta={"capability_model": {"status": "ready"}},
    )

    out = asyncio.run(run_recording_pi_loop(
        spec, llm_client=_DestructiveIncrementalPlanner(), model="fake", mode="plan", max_rounds=1,
    ))

    assert [cap.name for cap in out.capabilities] == ["query_status", "submit"]
    assert {cap.name: cap.step_ids for cap in out.capabilities} == {
        "query_status": ["query"], "submit": ["submit"],
    }
    assert out.capabilities[1].title == "完善后的提交能力"
    assert [item.link_id for item in out.links] == ["confirmed-link"]
    assert out.links[0].confirmed is True and out.links[0].locked is True
    assert [item.relation_id for item in out.capability_relations] == ["confirmed-relation"]
    assert out.capability_relations[0].confirmed is True


def test_repeated_orchestration_repairs_contract_without_expanding_interface_scope():
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
            step_ids=["submit"],
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec, llm_client=_ScopeExpandingPlanner(), model="fake"))

    assert [cap.name for cap in out.capabilities] == ["submit_batch"]
    assert out.capabilities[0].kind == "submit_batch"
    assert out.capabilities[0].title == "完善后的批量提交"
    assert out.capabilities[0].step_ids == ["submit"]


class _OverwriteManualFieldPlanner:
    async def complete_json(self, **_kwargs):
        return {"ops": [{"op": "mark_field_as_system_var", "step_id": "submit", "path": "content"}]}


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
            step_ids=["submit"],
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
        llm_client=_OverwriteManualFieldPlanner(),
        model="fake",
        max_rounds=1,
        expand_requests=False,
        allow_scope_changes=False,
    ))
    param = fixed.steps[0].params[0]

    assert param.locked is True
    assert param.category == "user_param"
    assert param.source_kind == "user_input"


def test_page_context_requires_explicit_context_key_and_differs_from_upstream_response():
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
            step_ids=["submit"],
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )

    invalid = validate_flow_spec(spec)
    assert any("context_key" in message for message in invalid["errors"])

    step.params[0].source = {"kind": "page_context", "context_key": "department_id", "path": "departmentId"}
    valid = validate_flow_spec(spec)
    assert not any("context_key" in message for message in valid["errors"])


def test_orchestration_removes_empty_planner_capability():
    spec = FlowSpec(
        flow_id="empty-capability",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[
            FlowCapability(name="list_options", kind="list_options", confirmed=True),
            FlowCapability(
                name="submit",
                kind="submit",
                step_ids=["submit"],
                nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            ),
        ],
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec))

    assert [cap.name for cap in out.capabilities] == ["submit"]


def test_only_empty_capability_is_replaced_with_real_baseline():
    spec = FlowSpec(
        flow_id="only-empty-capability",
        steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
        capabilities=[FlowCapability(name="list_options", kind="list_options", confirmed=True)],
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec))

    assert len(out.capabilities) == 1
    assert out.capabilities[0].kind == "submit"
    assert out.capabilities[0].step_ids == ["submit"]


class _SplitIndependentWritesPlanner:
    async def complete_json(self, **_kwargs):
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
        llm_client=_SplitIndependentWritesPlanner(),
        model="fake",
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
            step_ids=["query", "submit"],
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
            step_ids=["submit"],
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


def test_activity_id_cannot_be_changed_into_random_uuid_and_capability_view_stays_in_sync():
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
            step_ids=["approval"],
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
    assert param.category == "system_const"
    assert param.source_kind == "constant"
    assert param.source["semantic"] == "workflow_identifier"
    assert param.value == "StartUserNode"
    field = changed.capabilities[0].internal_fields[0]
    assert field.source_kind == "constant"
    assert field.exposed_to_caller is False
    report = validate_flow_spec(changed)
    text = "\n".join([*report["errors"], *report["warnings"]])
    assert "activityId" not in text


def test_leave_skill_repairs_process_id_flattens_submit_and_excludes_option_reads_from_query():
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
                step_ids=["users", "leave-page"], confirmed=True,
                output_mapping=[
                    {"name": "records", "step_id": "users", "response_path": "data.list"},
                    {"name": "records_2", "step_id": "leave-page", "response_path": "data.list"},
                ],
            ),
            FlowCapability(
                name="submit", kind="submit",
                step_ids=["process", "approval", "submit"], confirmed=True,
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
    assert process_param.category == "runtime_var"
    assert process_param.source_kind == "previous_response"
    assert process_param.source["step_id"] == "process"
    assert process_param.source["response_path"] == "data.id"


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
            step_ids=["submit"],
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )

    out = asyncio.run(orchestrate_flow_capabilities(spec))

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
    dependency_items = report["issue_groups"].get("dependency") or []
    diagnostic_items = report["issue_groups"].get("diagnostic") or []

    assert len([item for item in dependency_items if item.get("target", {}).get("link_id") == "same-link"]) == 1
    assert any("页面异常" in item["message"] for item in diagnostic_items)
    assert not any("ERR_CONNECTION_CLOSED" in item["message"] for item in diagnostic_items)


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
                step_ids=["keep"],
                nodes=[{"id": "call_keep", "type": "call", "step_id": "keep"}],
            ),
            FlowCapability(
                name="removed_cap",
                kind="query_status",
                step_ids=["removed"],
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
    assert all((item.get("target") or {}).get("step_id") != "removed" for item in report["review_items"])
    assert "runtimeId" not in "\n".join(report["errors"] + report["warnings"])


def test_publish_validation_exposes_structured_issue_groups():
    spec = FlowSpec(
        flow_id="issue-groups",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
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
        capabilities=[FlowCapability(name="submit", kind="submit", step_ids=["submit"], confirmed=True)],
        meta={"capability_model": {"status": "ready"}},
    )

    report = validate_flow_spec(spec)

    assert "field" in report["issue_groups"]
    assert all(item.get("message") for item in report["issue_groups"]["field"])
    assert all(item.get("audience") == "operator" for item in report["issue_groups"]["field"])
    assert all(item.get("actionable") is True for item in report["issue_groups"]["field"])


def test_publish_validation_marks_non_blocking_validator_advice_internal():
    spec = FlowSpec(
        flow_id="internal-advice",
        steps=[FlowStep(step_id="query", method="GET", url="/api/query", path="/api/query")],
        capabilities=[FlowCapability(
            name="query_status",
            kind="query_status",
            step_ids=["query"],
            nodes=[{"id": "call_query", "type": "call", "step_id": "query"}],
            confirmed=True,
        )],
        meta={"capability_model": {"status": "ready"}},
    )

    report = validate_flow_spec(spec)
    warnings = [
        item
        for group in report["issue_groups"].values()
        for item in group
        if item.get("source") == "validator" and item.get("severity") == "warning"
    ]

    assert warnings
    assert all(item.get("audience") == "internal" for item in warnings)
    assert all(item.get("actionable") is False for item in warnings)
    assert all(item.get("auto_fixable") is True for item in warnings)


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
            step_ids=["submit"],
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
