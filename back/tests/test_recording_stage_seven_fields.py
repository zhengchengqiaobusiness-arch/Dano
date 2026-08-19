"""Stage-seven empty fields stay unknown without positive caller evidence."""

from __future__ import annotations

from dano.execution.page.flow_spec import FlowSpec, FlowStep, ParamField, SelectBinding
from dano.onboarding.recording_stage_seven import apply_stage_seven_recorded_evidence_fixes
from dano.onboarding.recording_verify import apply_recorded_evidence_fixes


def _step(**kwargs) -> FlowStep:  # noqa: ANN003
    return FlowStep(step_id="step_edit", method="PUT", path="/erp/sale-order/update", **kwargs)


def test_empty_field_with_editable_control_becomes_optional_caller_input() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            _step(params=[
                ParamField(
                    path="query.remark",
                    key="remark",
                    value="",
                    source_kind="unknown",
                    evidence=[{"op": "fill", "editable": True, "disabled": False}],
                ),
            ]),
        ],
    )
    param = apply_stage_seven_recorded_evidence_fixes(spec).steps[0].params[0]
    assert param.source_kind == "user_input"
    assert param.exposed_to_user is True


def test_empty_field_without_evidence_stays_unknown() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[_step(params=[ParamField(path="query.remark", key="remark", value="", source_kind="unknown")])],
    )
    param = apply_stage_seven_recorded_evidence_fixes(spec).steps[0].params[0]
    assert param.source_kind == "unknown"
    legacy = apply_recorded_evidence_fixes(spec).steps[0].params[0]
    assert legacy.source_kind == "unknown"


def test_readonly_empty_field_is_not_exposed() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            _step(params=[
                ParamField(
                    path="body.createdAt",
                    key="createdAt",
                    value="",
                    source_kind="unknown",
                    evidence=[{"editable": False, "read_only": True}],
                ),
            ]),
        ],
    )
    param = apply_stage_seven_recorded_evidence_fixes(spec).steps[0].params[0]
    assert param.source_kind in {"previous_response", "unknown"}
    assert param.exposed_to_user is not True


def test_previous_response_empty_field_keeps_upstream_source() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            _step(
                params=[ParamField(path="query.id", key="id", value="", source_kind="previous_response")],
                selects=[SelectBinding(path="query.id", field_projections={"query.id": "data.id"})],
            ),
        ],
    )
    param = apply_stage_seven_recorded_evidence_fixes(spec).steps[0].params[0]
    assert param.source_kind == "previous_response"


def test_session_and_audit_fields_are_not_exposed() -> None:
    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            _step(params=[
                ParamField(path="body.tenantId", key="tenant_id", value="", source_kind="unknown"),
                ParamField(path="body.createdBy", key="created_by", value="", source_kind="unknown"),
                ParamField(path="header.Authorization", key="token", value="", source_kind="unknown"),
            ]),
        ],
    )
    fixed = apply_stage_seven_recorded_evidence_fixes(spec)
    kinds = [param.source_kind for param in fixed.steps[0].params]
    assert kinds == ["unknown", "unknown", "unknown"]
    assert all(param.exposed_to_user is not True for param in fixed.steps[0].params)
