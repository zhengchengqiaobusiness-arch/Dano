"""Offline regressions for the real screenshot false-success incident.

The strict xfails are removed phase-by-phase as the production boundary is fixed.
They keep the baseline green while proving each historical failure still exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import dano.agent_tools.tools as agent_tools
from dano.execution.page import flow_spec as flow_module
from dano.execution.page.flow_spec import FlowSpec, FlowStep, ParamField
from dano.gateway import app as gateway


_FIXTURE = Path(__file__).parent / "fixtures" / "recording_screenshot_false_success.json"


def _incident() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _six_field_spec() -> FlowSpec:
    names = ["申请标题", "公章", "使用日期", "归还日期", "使用描述", "备注"]
    return FlowSpec(steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/api/applications/submit",
        source_meta={"role": "business_write"},
        params=[
            ParamField(path=f"field{index}", key=name, label=name, value="recorded")
            for index, name in enumerate(names)
        ],
    )])


def test_screenshot_prose_only_plan_keeps_the_fact_baseline_available() -> None:
    incident = _incident()

    normalized = agent_tools._normalize_recording_plan_submission(
        incident["bridge_output_plan"], _six_field_spec(),
    )

    assert normalized["semantic_plan"]["field_semantics"] == []


def test_screenshot_zero_match_report_is_non_blocking_review() -> None:
    before = _six_field_spec()
    after = before.model_copy(deep=True)
    after.meta = {
        "capability_model": {
            "semantic_plan": {"field_semantics": [], "unresolved_items": []},
            "semantic_coverage": {"complete": False},
            "proposal_gate": {"accepted": True, "reasons": []},
        },
        "capability_generation": {"last_mode": "optimize"},
    }

    report = gateway._analysis_application_report(
        before=before,
        after=after,
        operation_report={
            "changed": False,
            "summary": "未修改任何字段、能力或关联",
            "changes": {},
            "field_changes": [],
            "proposal_gate": {"accepted": True, "reasons": []},
        },
        screenshots=[{"name": "list.png"}, {"name": "form.png"}],
        delivered_image_count=2,
        operation_id="plan-offline-regression",
    )

    assert report["matched_field_count"] == 0
    assert report["status"] == "needs_review"


def test_screenshot_value_never_writes_default_value() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/api/submit",
        params=[ParamField(path="days", key="天数", value="2", default_value=None)],
    )])

    flow_module._apply_capability_field_to_param(
        spec,
        {
            "step_id": "submit",
            "wire_path": "days",
            "key": "天数",
            "visible_default": "2",
            "evidence": [{
                "source": "screenshot",
                "screenshot_name": "form.png",
                "control_kind": "number",
                "editable": True,
                "visible_value": "2",
            }],
        },
        scope="input",
        actor="planner",
    )

    assert spec.steps[0].params[0].default_value is None


def test_confirmed_required_marker_convention_can_set_optional() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/api/submit",
        params=[ParamField(path="remark", key="备注", required=True)],
    )])

    flow_module._apply_capability_field_to_param(
        spec,
        {
            "step_id": "submit",
            "wire_path": "remark",
            "key": "备注",
            "required": False,
            "evidence": [{
                "source": "screenshot",
                "screenshot_name": "complete-form.png",
                "control_kind": "textarea",
                "editable": True,
                "required": False,
                "required_convention_confirmed": True,
                "label_region_complete": True,
            }],
        },
        scope="input",
        actor="planner",
    )

    assert spec.steps[0].params[0].required is False


def test_offline_incident_fixture_keeps_the_six_visible_controls() -> None:
    incident = _incident()

    assert incident["screenshot_count"] == 2
    assert len(incident["recognized_controls"]) == 6
    assert incident["bridge_output_plan"]["semantic_plan"]["field_semantics"] == []
