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
