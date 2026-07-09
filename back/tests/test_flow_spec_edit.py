"""Step B · FlowSpec 字段/link/step 编辑测试。"""

import asyncio
import json
from pathlib import Path

import pytest
import dano.execution.page.flow_spec as flow_spec_module

from dano.execution.page.flow_spec import (
    FlowSpec, FlowStep, FlowLink, ParamField, SelectBinding, FlowCapability,
    CapabilityDependency, CapabilityField, CapabilityRelation,
    apply_flow_edits, validate_flow_spec, _infer_type_from_value,
    add_llm_review_recommendations, refresh_review_items, flow_spec_to_api_request,
    capability_to_flow_spec_view, compile_capability_to_api_request, flow_spec_capability_contracts,
    flow_spec_to_client,
    auto_fix_flow_spec, orchestrate_flow_capabilities, run_recording_pi_loop, sync_flow_spec_models,
    flow_spec_canonical_summary, flow_spec_shadow_diff, migrate_v1_flow_spec_to_capability_spec,
    migrate_v2_flow_spec_to_capability_spec, capability_spec_to_legacy_flow_spec,
    capability_spec_to_api_request,
)


def _make_spec():
    param1 = ParamField(path="form.userId", key="userId", value="123", type="string", required=True)
    param2 = ParamField(path="form.name", key="name", value="test", type="string", required=True)
    step1 = FlowStep(
        step_id="step1", method="POST", url="/api/submit", path="/api/submit",
        params=[param1, param2], risk_level="L3", sample_inputs={"userId": "123", "name": "test"},
    )
    return FlowSpec(flow_id="test", steps=[step1])


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
    assert "来源 `input.unknown` 不存在" in text


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
    assert len(out.capabilities) == 1
    cap = out.capabilities[0]
    assert cap.name == "submit_batch"
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


def test_capability_scoped_fields_and_dependencies_survive_without_changing_step_ids():
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
    assert cap.request_fields[0].field_id == "manual-field-reason"
    assert cap.dependencies[0].dependency_id == "manual-dep-status-to-submit"

    api_request, errors = flow_spec_to_api_request(edited)

    assert errors == []
    exported = api_request["capabilities"][0]
    assert exported["step_ids"] == ["submit"]
    assert exported["compiled_step_ids"] == ["submit"]
    assert exported["request_fields"][0]["field_id"] == "manual-field-reason"
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


def test_edit_type_to_string_clears_wrong_enum_binding():
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
    assert new.steps[0].selects == []


def test_edit_type_to_enum_sets_editable_manual_enum_source():
    new = apply_flow_edits(_make_spec(), [{
        "op": "update",
        "step_id": "step1",
        "param_path": "form.userId",
        "field": "type",
        "value": "enum",
    }])

    param = new.steps[0].params[0]
    assert param.type == "enum"
    assert param.source_kind == "manual_enum"
    assert param.category == "user_param"
    assert param.exposed_to_user is True


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


def test_remove_link():
    spec = _two_step_spec_with_link()
    new = apply_flow_edits(spec, [{"op": "remove", "link_id": "l1"}])
    assert len(new.links) == 0


def test_remove_link_resets_target_param_source():
    spec = _two_step_spec_with_link()
    synced = apply_flow_edits(spec, [{"op": "update", "link_id": "l1", "field": "confirmed", "value": True}])
    before = {p.path: p for p in synced.steps[1].params}["y"]
    assert before.category == "runtime_var"
    assert before.source_kind == "previous_response"
    assert before.editable is False

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


def test_capability_scoped_view_preserves_locked_field_while_deriving_new_fields():
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
    assert locked.key == "leave_type"
    assert locked.display_name == "请假类型"
    assert locked.type == "enum"
    assert locked.locked is True
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
    assert "submit" in by_name["submit_batch"].step_ids


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


def test_capability_validation_reports_p1_internal_layers_as_warnings():
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
    assert "capability_field_path_missing" in internal_codes
    assert "capability_dependency_endpoint_missing" in internal_codes
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
            ParamField(path="[0].content", key="content", value="x", type="string", required=True),
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
    assert "capability_loop_item_field_missing" in text
    assert cap_report["capability_internal"]["passed"] is True


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
    assert "bad-dep" in text and "missing_status" in text
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


class _FakeLlmClient:
    def __init__(self, payload):
        self.payload = payload
        self.last_user = ""

    async def complete_json(self, *, model: str, system: str, user: str, timeout_s: float):
        self.last_user = user
        return self.payload


@pytest.mark.asyncio
async def test_llm_recommendations_attach_to_review_without_mutating_flow():
    source = FlowStep(
        step_id="s1", method="GET", url="/api/detail", path="/api/detail",
        response_json={"data": {"taskId": "T-100"}},
    )
    target_param = ParamField(
        path="taskId", key="taskId", value="T-100", type="string",
        category="runtime_var", source_kind="unknown",
    )
    target = FlowStep(
        step_id="s2", method="POST", url="/api/submit", path="/api/submit",
        params=[target_param], body_source='{"taskId":"T-100"}',
    )
    spec = refresh_review_items(FlowSpec(flow_id="llm", steps=[source, target]))
    client = _FakeLlmClient({"suggestions": [{
        "review_id": spec.review_items[0].id,
        "action": "bind_previous_response",
        "source_step_id": "s1",
        "source_path": "data.taskId",
        "confidence": 0.86,
        "reason": "字段名与上游 taskId 一致",
    }]})

    out = await add_llm_review_recommendations(spec, llm_client=client, model="fake")

    assert out.steps[1].params[0].source_kind == "unknown"
    assert out.links == []
    assert out.review_items[0].llm_suggestions[0]["action"] == "bind_previous_response"
    assert out.review_items[0].llm_suggestions[0]["source_path"] == "data.taskId"
    assert "T-100" not in client.last_user


@pytest.mark.asyncio
async def test_llm_recommendations_reject_ungrounded_source_path():
    source = FlowStep(
        step_id="s1", method="GET", url="/api/detail", path="/api/detail",
        response_json={"data": {"taskId": "T-100"}},
    )
    target_param = ParamField(
        path="taskId", key="taskId", value="T-100", type="string",
        category="runtime_var", source_kind="unknown",
    )
    target = FlowStep(step_id="s2", method="POST", url="/api/submit", path="/api/submit", params=[target_param])
    spec = refresh_review_items(FlowSpec(flow_id="llm", steps=[source, target]))
    client = _FakeLlmClient({"suggestions": [{
        "review_id": spec.review_items[0].id,
        "action": "bind_previous_response",
        "source_step_id": "s1",
        "source_path": "data.notExist",
        "confidence": 0.9,
        "reason": "bad",
    }]})

    out = await add_llm_review_recommendations(spec, llm_client=client, model="fake")

    assert out.review_items[0].llm_suggestions == []


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
