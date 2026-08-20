"""Stage 4: stored capability plans must not force submission_complete."""

from __future__ import annotations

from dano.execution.page.flow_spec import (
    FlowSpec,
    RequestFact,
    RequestFacts,
    recording_agent_validation,
)
from dano.onboarding.recording_pi import RecordingPiSession


def _spec_with_plan_and_ops(op_results: list[dict]) -> FlowSpec:
    spec = FlowSpec(tenant="t", subsystem="oa")
    spec.meta = {
        "current_version": 1,
        "capability_model": {
            "status": "ready",
            "semantic_plan": {
                "capabilities": [{"name": "create_doc", "anchor_request_id": "req_1"}],
            },
        },
        "recording_agent_session": {"op_results": op_results},
    }
    spec.request_facts = RequestFacts(requests=[RequestFact(request_id="req_1")])
    return spec


def test_rejected_field_op_keeps_plan_received_but_not_submission_complete() -> None:
    spec = _spec_with_plan_and_ops([
        {"index": 0, "op": "declare_capabilities", "status": "applied"},
        {"index": 1, "op": "set_param_source", "status": "rejected"},
    ])
    result = recording_agent_validation(spec)
    assert result["capability_plan_received"] is True
    assert result["capability_plan_complete"] is True
    assert result["submission_complete"] is False


def test_must_retry_and_version_conflict_are_incomplete_submissions() -> None:
    spec = _spec_with_plan_and_ops([
        {"index": 0, "op": "set_param_required", "status": "must_retry"},
    ])
    assert recording_agent_validation(spec)["submission_complete"] is False
    spec = _spec_with_plan_and_ops([
        {"index": 0, "op": "set_param_source", "status": "version_conflict"},
    ])
    assert recording_agent_validation(spec)["submission_complete"] is False
    spec = _spec_with_plan_and_ops([
        {"index": 0, "op": "set_param_source", "status": "rolled_back"},
    ])
    assert recording_agent_validation(spec)["submission_complete"] is False


def test_all_field_ops_applied_marks_submission_complete() -> None:
    spec = _spec_with_plan_and_ops([
        {"index": 0, "op": "declare_capabilities", "status": "applied"},
        {"index": 1, "op": "set_param_source", "status": "applied"},
    ])
    result = recording_agent_validation(spec)
    assert result["capability_plan_received"] is True
    assert result["submission_complete"] is True


async def test_plan_mode_does_not_force_complete_when_field_ops_fail(monkeypatch) -> None:
    spec = _spec_with_plan_and_ops([
        {"index": 1, "op": "set_param_source", "status": "rejected"},
    ])

    async def fake_apply(current, *, submission, mode):  # noqa: ARG001, ANN001
        return spec

    monkeypatch.setattr(
        "dano.execution.page.flow_spec.apply_recording_agent_submission",
        fake_apply,
    )

    def unexpected_full_validation(_spec):  # noqa: ANN001
        raise AssertionError("submission acceptance ran full release validation")

    monkeypatch.setattr(
        "dano.execution.page.flow_spec.recording_agent_validation",
        unexpected_full_validation,
    )
    session = RecordingPiSession(
        tenant="t",
        subsystem="oa",
        recording_id="recording_" + ("a" * 32),
        resume_history=False,
    )
    session.flow_spec = spec
    result = await session.apply_submission(
        {"semantic_plan": {"capabilities": [{"name": "create_doc"}]}, "ops": []},
        mode="plan",
        base_flow_version=1,
    )
    assert result["capability_plan_received"] is True
    assert result["submission_complete"] is False
    assert result["must_retry"] == [1]
