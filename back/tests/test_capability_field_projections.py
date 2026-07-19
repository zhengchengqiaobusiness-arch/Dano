from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import (
    CapabilityField,
    FlowCapability,
    FlowSpec,
    FlowStep,
    ParamField,
    apply_flow_edits,
    sync_capability_scoped_views,
)


def _field_spec() -> FlowSpec:
    return FlowSpec(
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            path="/api/submit",
            params=[ParamField(
                path="enabled",
                key="是否启用",
                type="boolean",
                category="user_param",
                source_kind="user_input",
                required=True,
            )],
        )],
        capabilities=[FlowCapability(
            name="submit",
            nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
            inputs=[CapabilityField(
                scope="input",
                step_id="submit",
                path="enabled",
                key="错误名称",
                type="enum",
                source_kind="api_option",
                locked=True,
            )],
            request_fields=[CapabilityField(
                scope="request_field",
                step_id="submit",
                path="enabled",
                key="错误名称",
                type="enum",
                locked=True,
            )],
        )],
    )


def test_capability_field_views_are_rebuilt_from_params_not_old_mirrors() -> None:
    spec = sync_capability_scoped_views(_field_spec())
    cap = spec.capabilities[0]

    assert "fields" not in cap.model_dump()
    assert [(field.key, field.type, field.source_kind) for field in cap.inputs] == [
        ("是否启用", "boolean", "user_input"),
    ]
    assert [(field.key, field.type) for field in cap.request_fields] == [
        ("是否启用", "boolean"),
    ]


@pytest.mark.parametrize(
    "field",
    ["fields", "inputs", "request_fields", "internal_fields", "outputs"],
)
def test_derived_capability_field_views_are_read_only(field: str) -> None:
    with pytest.raises(ValueError, match="derived capability field is read-only"):
        apply_flow_edits(_field_spec(), [{
            "op": "update_capability",
            "capability_index": 0,
            "field": field,
            "value": [],
        }])


def test_capability_level_input_is_stored_in_schema_and_projected() -> None:
    updated = apply_flow_edits(_field_spec(), [{
        "op": "upsert_input_field",
        "capability_index": 0,
        "field": {"key": "entries", "type": "array", "required": True},
    }])
    cap = updated.capabilities[0]

    assert cap.input_schema["properties"]["entries"]["x-dano-capability-owned"] is True
    assert "entries" in cap.input_schema["required"]
    assert any(field.key == "entries" for field in cap.inputs)