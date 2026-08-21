"""Capability-page save must rewrite the stored FlowSpec and do nothing else."""

from __future__ import annotations

from dano.execution.page.flow_spec import FlowCapability, FlowSpec, FlowStep, RequestFact, RequestFacts
from dano.onboarding.recording_results import (
    apply_recording_result_edits,
    latest_recording_spec,
    stage_six_result_body,
)
from dano.onboarding.recording_stage_seven import (
    baseline_fingerprint,
    checkpoint_dict,
    load_resumable_working_spec,
    working_fingerprint,
)


def _query_spec() -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        title="查询销售订单",
        steps=[
            FlowStep(
                step_id="step_get",
                method="GET",
                path="/erp/sale-order/page",
                source_meta={"request_id": "req_get"},
            ),
        ],
        capabilities=[
            FlowCapability(
                name="list_sale_order",
                title="查询销售订单",
                kind="query_status",
                capability_id="cap_list",
                step_ids=["step_get"],
                nodes=[
                    {
                        "id": "call_get",
                        "type": "call",
                        "usage": "execute",
                        "request_id": "req_get",
                        "method": "GET",
                        "path": "/erp/sale-order/page",
                        "step_id": "step_get",
                    },
                ],
            ),
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(request_id="req_get", method="GET", path="/erp/sale-order/page"),
            ],
        ),
    )


def test_save_edits_only_updates_stored_flow_spec() -> None:
    spec = _query_spec().model_dump(mode="json")
    body = stage_six_result_body(
        action="action_saved",
        title="销售订单管理",
        goal="查询销售订单",
        tenant="tenant",
        subsystem="oa",
        draft=spec,
        published=False,
        machine_verification_ran=True,
    )
    body["machine_verification_status"] = "verified"
    body["skill_plan"] = {"selected_capability_ids": ["cap_list"]}
    body["skill_export_status"] = "exported"
    body["skill_needs_reexport"] = False

    updated = apply_recording_result_edits(
        body,
        [
            {
                "op": "update_capability",
                "actor": "user",
                "capability_id": "cap_list",
                "capability_name": "list_sale_order",
                "field": "title",
                "value": "查询订单",
            },
        ],
        expected_fingerprint=working_fingerprint(spec),
    )

    assert updated["flow_spec"]["capabilities"][0]["title"] == "查询订单"
    assert updated["machine_verification_status"] == "verified"
    assert updated["skill_plan"] == {"selected_capability_ids": ["cap_list"]}
    assert updated["skill_export_status"] == "exported"
    assert updated["skill_needs_reexport"] is False
    assert updated.get("skill_plan_valid") is not False


def test_save_keeps_continue_analysis_on_latest_spec() -> None:
    spec = _query_spec()
    body = stage_six_result_body(
        action="action_saved",
        title="销售订单管理",
        goal="查询销售订单",
        tenant="tenant",
        subsystem="oa",
        draft=spec.model_dump(mode="json"),
    )
    body["stage_seven"] = checkpoint_dict(
        attempt_id="attempt-1",
        revision=1,
        status="incomplete",
        baseline=spec,
        working=spec,
    )

    updated = apply_recording_result_edits(
        body,
        [
            {
                "op": "update_capability",
                "actor": "user",
                "capability_id": "cap_list",
                "capability_name": "list_sale_order",
                "field": "title",
                "value": "查询订单",
            },
        ],
        expected_fingerprint=working_fingerprint(spec),
    )

    latest = latest_recording_spec(updated)
    assert latest is not None
    assert latest["capabilities"][0]["title"] == "查询订单"
    assert updated["stage_seven"]["working_flow_spec"]["capabilities"][0]["title"] == "查询订单"
    assert updated["stage_seven"]["baseline_fingerprint"] == baseline_fingerprint(updated["flow_spec"])
    draft, checkpoint, reason = load_resumable_working_spec(updated)
    assert reason == ""
    assert checkpoint is not None
    assert draft["capabilities"][0]["title"] == "查询订单"


def test_stale_working_copy_yields_to_saved_flow_spec() -> None:
    spec = _query_spec()
    stale = spec.model_copy(deep=True)
    stale.capabilities[0].title = "旧标题"
    body = stage_six_result_body(
        action="action_saved",
        title="销售订单管理",
        goal="查询销售订单",
        tenant="tenant",
        subsystem="oa",
        draft=spec.model_dump(mode="json"),
    )
    body["flow_spec"]["capabilities"][0]["title"] = "查询订单"
    body["stage_seven"] = checkpoint_dict(
        attempt_id="attempt-2",
        revision=1,
        status="incomplete",
        baseline=spec,
        working=stale,
    )
    latest = latest_recording_spec(body)
    assert latest is not None
    assert latest["capabilities"][0]["title"] == "查询订单"
    draft, checkpoint, reason = load_resumable_working_spec(body)
    assert reason == ""
    assert checkpoint is None
    assert draft["capabilities"][0]["title"] == "查询订单"
