"""Generalized screenshot field-axis regressions for the recording compiler."""

from __future__ import annotations

import pytest

from dano.execution.page import flow_spec as flow_module
from dano.execution.page.flow_spec import FlowSpec, FlowStep, ParamField


@pytest.mark.parametrize(
    (
        "control_kind", "multiple", "editable", "proposed_type",
        "source_kind", "scope", "expected_type", "expected_category",
        "expected_required",
    ),
    [
        ("text", False, True, "enum", "user_input", "input", "string", "user_param", True),
        ("textarea", False, True, "number", "user_input", "input", "string", "user_param", True),
        ("number", False, True, "string", "user_input", "input", "number", "user_param", True),
        ("date", False, True, "string", "user_input", "input", "date", "user_param", True),
        ("datetime", False, True, "string", "user_input", "input", "datetime", "user_param", True),
        ("switch", False, True, "string", "user_input", "input", "boolean", "user_param", True),
        ("select", False, True, "string", "page_enum", "input", "enum", "user_param", True),
        ("checkbox", True, True, "string", "page_enum", "input", "list-enum", "user_param", True),
        ("text", False, False, "enum", "current_user", "internal", "string", "runtime_var", False),
    ],
)
def test_screenshot_field_axes_generalize_and_remain_stable_on_reanalysis(
    control_kind: str,
    multiple: bool,
    editable: bool,
    proposed_type: str,
    source_kind: str,
    scope: str,
    expected_type: str,
    expected_category: str,
    expected_required: bool,
) -> None:
    path = f"payload.{control_kind}_{'multi' if multiple else 'single'}"
    original_default = "server-owned-default"
    param = ParamField(
        path=path,
        key="staleName",
        label="staleName",
        value="recorded-value",
        default_value=original_default,
        type="string",
        wire_type="string",
        required=False,
        category="unknown",
        source_kind="unknown",
    )
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/api/generic/submit",
        params=[param],
    )])
    evidence = {
        "source": "screenshot",
        "screenshot_name": "generic-form.png",
        "visible_label": f"字段-{control_kind}",
        "control_kind": control_kind,
        "editable": editable,
        "disabled": not editable,
        "read_only": not editable,
        "multiple": multiple,
        "required": expected_required,
        "visible_value": "仅用于匹配，不是默认值",
    }
    if control_kind in {"select", "checkbox"}:
        evidence["options"] = ["选项甲", "选项乙"]
    normalized_type = flow_module._screenshot_control_business_type(
        evidence, proposed_type,
    )
    raw = {
        "step_id": "submit",
        "wire_path": path,
        "key": f"字段-{control_kind}",
        "display_name": f"字段-{control_kind}",
        "type": normalized_type,
        "category": expected_category,
        "source_kind": source_kind,
        "source": {"kind": source_kind, "path": path},
        "required": expected_required,
        "visible_default": "截图观察值",
        "enum_options": ["选项甲", "选项乙"],
        "evidence": [evidence],
    }

    for _ in range(2):
        assert flow_module._apply_capability_field_to_param(
            spec, raw, scope=scope, actor="planner",
        )
        assert param.path == path
        assert (param.key, param.label) == (
            f"字段-{control_kind}", f"字段-{control_kind}",
        )
        assert param.default_value == original_default
        assert param.type == expected_type
        assert param.category == expected_category
        assert param.source_kind == source_kind
        assert param.required is expected_required

    if expected_type in {"enum", "list-enum"}:
        assert param.enum_options == ["选项甲", "选项乙"]
    else:
        assert param.enum_options is None


def test_screenshot_observation_cannot_change_wire_path_or_default() -> None:
    param = ParamField(
        path="payload.actualWireName",
        key="旧名称",
        value="temporary-recorded-input",
        default_value=None,
    )
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/submit", params=[param],
    )])

    assert flow_module._apply_capability_field_to_param(
        spec,
        {
            "step_id": "submit",
            "wire_path": "payload.actualWireName",
            "path": "payload.actualWireName",
            "key": "截图名称",
            "type": "string",
            "source_kind": "user_input",
            "required": True,
            "visible_default": "截图里的当前输入",
            "evidence": [{
                "source": "screenshot",
                "screenshot_name": "form.png",
                "control_kind": "text",
                "editable": True,
                "required": True,
            }],
        },
        scope="input",
        actor="planner",
    )

    assert param.path == "payload.actualWireName"
    assert param.key == "截图名称"
    assert param.default_value is None
