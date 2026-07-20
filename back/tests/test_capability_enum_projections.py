from __future__ import annotations

from dano.execution.page import flow_spec as flow_spec_module
from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    SelectBinding,
    sync_flow_spec_models,
)


def test_mixed_enum_shapes_normalize_once_then_project_to_capability() -> None:
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            path="/api/leave",
            params=[ParamField(
                path="leaveType",
                key="请假类型",
                type="string",
                value="2",
                category="user_param",
                source_kind="user_input",
            )],
            selects=[SelectBinding(
                param="请假类型",
                path="leaveType",
                source_url="/api/dict/leave-types",
                value_key="value",
                label_key="label",
                options=["年假", {"label": "事假", "value": "2"}, ("病假", "3")],
                option_map={"年假": "1", "事假": "2", "病假": "3"},
                enum_source="api",
                enum_confirmed=True,
            )],
        )],
        request_facts=RequestFacts(
            requests=[RequestFact(
                request_id="req-dict",
                method="GET",
                path="/api/dict/leave-types",
                response_json=[
                    {"label": "年假", "value": "1"},
                    {"label": "事假", "value": "2"},
                    {"label": "病假", "value": "3"},
                ],
            )],
            analysis={"req-dict": RequestAnalysis(
                request_id="req-dict", role="read_option", semantic_roles=["enum_options"],
            )},
            option_sources=[{"kind": "api_response", "request_id": "req-dict"}],
        ),
        capabilities=[FlowCapability(
            name="submit_leave",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        )],
    )

    synced = sync_flow_spec_models(spec)
    param = synced.steps[0].params[0]
    capability_field = synced.capabilities[0].inputs[0]

    expected = [
        {"label": "年假", "value": "1"},
        {"label": "事假", "value": "2"},
        {"label": "病假", "value": "3"},
    ]
    assert param.enum_options == expected
    assert param.enum_value_map == {"年假": "1", "事假": "2", "病假": "3"}
    assert capability_field.enum_options == expected
    assert capability_field.enum_value_map == param.enum_value_map
    schema_spec = flow_spec_module._sync_capability_io_schemas(synced)
    schema = schema_spec.capabilities[0].input_schema["properties"]["请假类型"]
    assert schema["x-options-snapshot"] == expected


def test_incomplete_dom_select_preserves_enum_without_executable_label_fallback() -> None:
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="query",
            method="GET",
            path="/api/applications/page",
            source_meta={"page_id": "applications", "frame_id": "main"},
            params=[ParamField(
                path="query.status",
                key="status",
                value="2",
                type="string",
                wire_type="string",
                category="user_param",
                source_kind="user_input",
            )],
        )],
        request_facts=RequestFacts(option_sources=[{
            "kind": "page_enum_options",
            "options": {
                "status": {
                    "field_key": "状态",
                    "field_aliases": ["status"],
                    "control_kind": "select",
                    "selected_label": "处理中",
                    "selected_value": "2",
                    "mapping_complete": False,
                    "options": ["待处理", "处理中", "已完成"],
                    "page_id": "applications",
                    "frame_id": "main",
                },
            },
        }]),
    )

    synced = sync_flow_spec_models(spec)
    param = synced.steps[0].params[0]
    binding = synced.steps[0].selects[0]

    assert (param.type, param.wire_type) == ("enum", "string")
    assert (param.category, param.source_kind) == ("user_param", "page_enum")
    assert param.enum_options == ["待处理", {"label": "处理中", "value": "2"}, "已完成"]
    assert param.enum_value_map == {"处理中": "2"}
    assert param.source["enum_confirmed"] is False
    assert param.need_human_confirm is True
    assert binding.enum_confirmed is False
    assert flow_spec_module._runtime_select_bindings(synced.steps[0]) == []


def test_autofix_context_only_exposes_admitted_option_sources() -> None:
    option_rows = [{"id": "1", "name": "财务章"}, {"id": "2", "name": "合同章"}]
    menu_rows = [{"id": "1", "name": "首页"}, {"id": "2", "name": "系统管理"}]
    spec = FlowSpec(request_facts=RequestFacts(
        requests=[
            RequestFact(
                request_id="req-option", method="GET", path="/api/seals/simple-list",
                response_json=option_rows,
            ),
            RequestFact(
                request_id="req-menu", method="GET", path="/api/menus/list",
                response_json=menu_rows,
            ),
        ],
        analysis={
            "req-option": RequestAnalysis(request_id="req-option", role="read_option"),
            "req-menu": RequestAnalysis(request_id="req-menu", role="business_get"),
        },
        option_sources=[{
            "kind": "api_response",
            "request_id": "req-option",
            "path": "/api/seals/simple-list",
        }],
    ))

    context = flow_spec_module._flow_autofix_context(
        spec, {"capability_validation": {}},
    )

    assert [source["request_id"] for source in context["candidate_option_sources"]] == [
        "req-option",
    ]


def test_api_option_source_is_an_index_to_request_fact_not_a_response_copy() -> None:
    facts = flow_spec_module._build_request_facts(
        captured_requests=[{
            "method": "GET",
            "url": "https://example.test/api/dict/leave-types",
            "response_json": [{"label": "年假", "value": "1"}],
        }],
        request_roles=[{
            "role": "read_option",
            "semantic_roles": ["enum_options"],
            "keep": True,
        }],
        selected_keys=set(),
    )

    source = next(item for item in facts.option_sources if item["kind"] == "api_response")
    assert source["request_id"] == facts.requests[0].request_id
    assert source["path"] == "/api/dict/leave-types"
    assert "response_json" not in source
    assert facts.requests[0].response_json == [{"label": "年假", "value": "1"}]


def test_request_facts_and_agent_context_preserve_complete_selection_trace() -> None:
    page_events = [
        {
            "event_id": "event-open",
            "kind": "action",
            "action_id": "action-select-seal",
            "transaction_id": "page-1|main|action-select-seal",
            "op": "control_open",
            "field": "公章",
            "observed_at": 1000,
            "page_id": "page-1",
            "frame_id": "main",
        },
        {
            "event_id": "event-pick",
            "kind": "action",
            "action_id": "action-select-seal",
            "transaction_id": "page-1|main|action-select-seal",
            "op": "pick",
            "field": "公章",
            "observed_at": 1100,
            "page_id": "page-1",
            "frame_id": "main",
        },
        {
            "event_id": "event-submit",
            "kind": "action",
            "action_id": "action-submit",
            "transaction_id": "page-1|main|action-submit",
            "op": "submit",
            "observed_at": 1200,
            "page_id": "page-1",
            "frame_id": "main",
        },
    ]
    field_evidence = [{
        "path": "sealId",
        "label": "公章",
        "field_aliases": ["sealId"],
        "control_kind": "select",
        "required": True,
        "page_id": "page-1",
        "frame_id": "main",
    }]
    page_options = {
        "公章": {
            "field_key": "公章",
            "field_aliases": ["sealId"],
            "control_kind": "select",
            "options": [
                {"label": "公司章", "value": "seal-a"},
                {"label": "财务章", "value": "seal-b"},
            ],
            "selected": "公司章",
            "selected_label": "公司章",
            "selected_value": "seal-a",
            "mapping_complete": True,
            "enum_source": "api",
            "source_url": "https://example.test/api/seals/options?enabled=1",
            "dict_type": "seal_type",
            "action_id": "action-select-seal",
            "transaction_id": "page-1|main|action-select-seal",
            "observed_at": 1100,
            "page_id": "page-1",
            "frame_id": "main",
        },
    }
    captured = [
        {
            "index": 1,
            "request_id": "req-options",
            "sequence": 1,
            "method": "GET",
            "url": "https://example.test/api/seals/options?enabled=1",
            "response_json": {"result": [
                {"id": "seal-a", "name": "公司章"},
                {"id": "seal-b", "name": "财务章"},
            ]},
            "trigger_action_id": "action-select-seal",
            "trigger_transaction_id": "page-1|main|action-select-seal",
            "trigger_event_id": "event-open",
            "action_delta_ms": 25,
            "page_id": "page-1",
            "frame_id": "main",
        },
        {
            "index": 2,
            "request_id": "req-submit",
            "sequence": 2,
            "method": "POST",
            "url": "https://example.test/api/seal-apply",
            "post_data": '{"sealId":"seal-a","title":"Project"}',
            "response_json": {"ok": True},
            "trigger_action_id": "action-submit",
            "trigger_transaction_id": "page-1|main|action-submit",
            "trigger_event_id": "event-submit",
            "action_delta_ms": 15,
            "page_id": "page-1",
            "frame_id": "main",
        },
    ]
    facts = flow_spec_module._build_request_facts(
        captured_requests=captured,
        request_roles=[
            {"role": "read_option", "semantic_roles": ["enum_options"], "keep": False},
            {"role": "business_write", "keep": True},
        ],
        selected_keys={2},
        page_enum_options=page_options,
        page_events=page_events,
        field_evidence=field_evidence,
    )

    option_fact = next(fact for fact in facts.requests if fact.request_id == "req-options")
    submit_fact = next(fact for fact in facts.requests if fact.request_id == "req-submit")
    assert option_fact.query == {"enabled": ["1"]}
    assert option_fact.query_paths == ["query.enabled"]
    assert submit_fact.body_paths == ["sealId", "title"]
    assert facts.field_evidence == field_evidence
    assert facts.page_events == page_events

    page_source = next(source for source in facts.option_sources if source["kind"] == "page_enum_options")
    seal_source = page_source["options"]["公章"]
    assert seal_source["field_aliases"] == ["sealId"]
    assert seal_source["selected_label"] == "公司章"
    assert seal_source["selected_value"] == "seal-a"
    assert seal_source["mapping_complete"] is True
    assert seal_source["source_request_ids"] == ["req-options"]
    assert seal_source["transaction_id"] == "page-1|main|action-select-seal"
    assert seal_source["trace_status"] == {
        "control": "observed",
        "candidates": "observed",
        "selection": "observed",
        "selected_value": "observed",
        "source_request": "observed",
        "submitted_value": "observed",
        "mapping": "complete",
    }
    assert seal_source["request_value_observations"] == [{
        "request_id": "req-submit",
        "request_index": 2,
        "method": "POST",
        "path": "/api/seal-apply",
        "wire_path": "sealId",
        "value": "seal-a",
        "sequence": 2,
        "trigger_action_id": "action-submit",
        "trigger_transaction_id": "page-1|main|action-submit",
        "action_delta_ms": 15,
    }]

    spec = FlowSpec(request_facts=facts)
    state = flow_spec_module.recording_agent_state(spec)
    semantic_facts = state["facts"]
    assert semantic_facts["field_evidence"] == field_evidence
    assert semantic_facts["option_sources"][0]["options"]["公章"]["selected_value"] == "seal-a"
    assert semantic_facts["captured_requests"][0]["query_paths"] == ["query.enabled"]
    assert semantic_facts["captured_requests"][1]["body_paths"] == ["sealId", "title"]
    assert semantic_facts["page_events"][0]["transaction_id"] == "page-1|main|action-select-seal"

    validation = flow_spec_module.recording_agent_validation(spec)
    repair_context = validation["repair_context"]
    assert repair_context["recorded_field_evidence"] == field_evidence
    assert repair_context["recorded_option_sources"][0]["options"]["公章"]["source_request_ids"] == [
        "req-options",
    ]
    assert repair_context["page_events"][1]["op"] == "pick"


def test_recording_fact_projection_redacts_values_using_wire_path_identity() -> None:
    spec = FlowSpec(request_facts=RequestFacts(
        field_evidence=[{
            "path": "accessToken",
            "field_aliases": ["accessToken"],
            "value": "secret-token",
        }],
        option_sources=[{
            "kind": "page_enum_options",
            "options": {
                "Token": {
                    "field_aliases": ["accessToken"],
                    "selected_value": "secret-token",
                    "request_value_observations": [{
                        "wire_path": "accessToken",
                        "value": "secret-token",
                    }],
                },
            },
        }],
    ))

    projected = flow_spec_module.recording_agent_state(spec)["facts"]
    assert projected["field_evidence"][0]["value"] == "***"
    option = projected["option_sources"][0]["options"]["Token"]
    assert option["selected_value"] == "***"
    assert option["request_value_observations"][0]["value"] == "***"
