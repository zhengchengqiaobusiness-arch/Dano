"""Regression coverage for the capability-owned Skill export contract."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dano.execution.page import request_capture
from dano.execution.page.flow_materialization.field_contracts.option_sync import (
    _sync_step_option_contracts,
)
from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowSpec,
    FlowStep,
    ParamField,
    SelectBinding,
    flow_spec_to_api_request,
)
from dano.execution.page.flow_spec_core.request_contract import _flow_step_to_api_step
from dano.export.skill_package.renderer import (
    _capabilities_md,
    _capability_plans,
    _options_md,
)


def test_selected_option_field_is_synchronized_to_the_owning_select_binding() -> None:
    selector_path = "items[0].productId"
    target_path = "items[0].productPrice"
    step = FlowStep(
        step_id="create",
        method="POST",
        path="/orders",
        body_source=json.dumps({"items": [{"productId": 7, "productPrice": 12.5}]}),
        params=[
            ParamField(
                path=selector_path,
                key="items",
                type="list-enum",
                wire_type="number",
                category="user_param",
                source_kind="api_option",
                source={
                    "kind": "api_option",
                    "source_url": "/products",
                    "value_key": "id",
                    "label_key": "name",
                },
                enum_options=[{"label": "A", "value": 7}],
                enum_value_map={"A": 7},
            ),
            ParamField(
                path=target_path,
                key="productPrice",
                type="number",
                category="runtime_var",
                source_kind="selected_option_field",
                source={
                    "kind": "selected_option_field",
                    "selector_path": selector_path,
                    "selector_param": "items",
                    "source_url": "/products",
                    "response_path": "salePrice",
                    "target_path": target_path,
                },
                exposed_to_user=False,
                editable=False,
                required=False,
            ),
        ],
        selects=[SelectBinding(
            param="items",
            path=selector_path,
            source_url="/products",
            value_key="id",
            label_key="name",
            options=[{"label": "A", "value": 7}],
            option_map={"A": 7},
            enum_source="api",
            enum_confirmed=True,
        )],
    )
    spec = FlowSpec(steps=[step], meta={"stage_1_6_contract_version": 2})

    _sync_step_option_contracts(spec, step)

    assert step.selects[0].field_projections[target_path] == "salePrice"


@pytest.mark.asyncio
async def test_selected_option_projection_updates_each_dynamic_array_row(monkeypatch) -> None:
    async def options(*_args, **_kwargs):
        return [{"id": 7, "name": "A", "salePrice": 12.5}]

    monkeypatch.setattr(request_capture, "_fetch_select_list", options)
    fields = {"items": [{"productId": "A", "count": 2}]}
    resolved = await request_capture._resolve_list_selects(
        {"selects": [{
            "param": "items",
            "path": "items[0].productId",
            "multi": True,
            "source_url": "/products",
            "label_key": "name",
            "value_key": "id",
            "label_subkey": "productId",
            "element_template": {"productId": {"item_key": "id"}},
            "field_projections": {"items[0].productPrice": "salePrice"},
        }]},
        fields,
        base_url="",
        storage_state=None,
        token_key=None,
        verify=True,
    )

    assert resolved["items"] == [{"productId": 7, "count": 2, "productPrice": 12.5}]


def test_page_rule_with_formula_compiles_to_an_executable_runtime_field() -> None:
    step = FlowStep(
        step_id="query",
        method="GET",
        path="/orders",
        params=[
            ParamField(
                path="query.startedAt",
                key="startedAt",
                type="date",
                value="2026-08-21 00:00:00",
                category="user_param",
                source_kind="user_input",
            ),
            ParamField(
                path="query.endedAt",
                key="endedAt",
                type="datetime",
                value="2026-08-21 23:59:59",
                category="runtime_var",
                source_kind="page_rule",
                source={
                    "kind": "date_range_end",
                    "start_field": "startedAt",
                    "output_format": "%Y-%m-%d 23:59:59",
                    "formula": {
                        "kind": "date_range_end",
                        "start_field": "startedAt",
                        "output_format": "%Y-%m-%d 23:59:59",
                    },
                    "executable": True,
                },
                exposed_to_user=False,
                editable=False,
                required=False,
            ),
        ],
    )

    compiled, errors = _flow_step_to_api_step(step)

    assert errors == []
    assert compiled is not None
    runtime = compiled["runtime_fields"][0]
    assert runtime["kind"] == "date_range_end"
    assert runtime["start_field"] == "startedAt"
    assert str(compiled["query_template"]["endedAt"]).startswith("{{__dano_runtime_")
    values = request_capture._apply_runtime_fields({"startedAt": "2026-08-21"}, compiled)
    assert values[runtime["name"]] == "2026-08-21 23:59:59"


def test_compiled_capability_contains_its_own_executable_steps() -> None:
    step = FlowStep(
        step_id="submit",
        method="POST",
        path="/orders",
        body_source=json.dumps({"name": "recorded"}),
        params=[ParamField(
            path="name",
            key="name",
            value="recorded",
            category="user_param",
            source_kind="user_input",
        )],
    )
    cap = FlowCapability(
        capability_id="cap_submit",
        name="submit_order",
        title="提交订单",
        step_ids=[step.step_id],
        nodes=[{"type": "call", "step_id": step.step_id}],
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "x-flow-path": "name"}},
            "required": ["name"],
        },
    )

    compiled, errors = flow_spec_to_api_request(
        FlowSpec(
            steps=[step], capabilities=[cap],
            meta={"stage_1_6_contract_version": 2},
        ),
        _prepared=True,
    )

    assert errors == []
    assert compiled is not None
    contract = compiled["capabilities"][0]["execution_contract"]
    assert [item["step_id"] for item in contract["steps"]] == ["submit"]
    assert contract["steps"][0]["body_template"]["name"] == "{{name}}"


def test_skill_plan_uses_capability_owned_steps_instead_of_global_steps() -> None:
    capability = {
        "capability_id": "cap_query",
        "name": "query_orders",
        "title": "查询订单",
        "kind": "query",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "output_schema": {"type": "object"},
        "execution_contract": {
            "protocol": "dano.capability_plan.v2",
            "steps": [{"step_id": "right", "method": "GET", "path": "/right"}],
            "links": [],
            "verification_ids": [],
        },
    }
    api_request = {
        "steps": [{"step_id": "right", "method": "GET", "path": "/wrong"}],
        "capabilities": [capability],
    }
    skill = SimpleNamespace(skill_id="orders", api_request=api_request, capabilities=[])

    plans = _capability_plans(skill, None, api_request)

    assert plans[0]["steps"][0]["path"] == "/right"


def test_capability_and_option_documents_keep_the_complete_contract() -> None:
    options = [
        {"label": f"候选{index}", "value": f"value-{index}"}
        for index in range(123)
    ]
    plan = {
        "name": "submit_order",
        "capability_id": "cap_submit",
        "title": "提交订单",
        "kind": "submit",
        "script": "submit_order",
        "requires_confirmation": True,
        "requires_verify": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "x-flow-path": "status",
                    "x-options": options,
                },
            },
            "required": ["status"],
        },
        "output_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
        "preconditions": [],
        "caller_responsibilities": [],
        "skill_responsibilities": [],
        "steps": [{
            "step_id": "submit",
            "method": "POST",
            "path": "/orders",
            "runtime_fields": [{"name": "total", "kind": "sum", "left_field": "a", "right_field": "b"}],
        }],
        "links": [],
        "fact_checks": [],
        "contract": {
            "protocol": "dano.capability_contract.v2",
            "execution_contract": {"protocol": "dano.capability_plan.v2"},
        },
    }
    skill = SimpleNamespace(skill_id="orders", title="订单", api_request={})

    capabilities = _capabilities_md(skill, [plan])
    option_doc = _options_md([plan])

    assert '"runtime_fields"' in capabilities
    assert '"execution_contract"' in capabilities
    assert "候选0" in option_doc
    assert "value-0" in option_doc
    assert "候选122" in option_doc
    assert "value-122" in option_doc
