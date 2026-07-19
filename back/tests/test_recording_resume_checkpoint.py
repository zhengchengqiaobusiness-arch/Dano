from __future__ import annotations

import asyncio

import pytest

from dano.execution.page.flow_spec import FlowSpec, flow_spec_fingerprint
from dano.gateway import app as gateway
from dano.onboarding import recording_pi


def _spec(version: int, *, title: str = "recording") -> FlowSpec:
    return FlowSpec.model_validate({
        "title": title,
        "steps": [],
        "capabilities": [],
        "meta": {"current_version": version},
    })


def test_accepted_plan_and_repair_replace_resume_checkpoint_immediately() -> None:
    state = {"connection_generation": 7}
    plan = _spec(1, title="plan")
    repair = _spec(2, title="repair")

    assert gateway._checkpoint_accepted_recording_pi_submission(
        state, plan, submission_kind="plan", connection_generation=7,
    )
    assert state["flow_spec_version"] == 1
    assert state["flow_spec_fingerprint"] == flow_spec_fingerprint(plan)
    assert state["submission_kind"] == "plan"

    assert gateway._checkpoint_accepted_recording_pi_submission(
        state, repair, submission_kind="repair", connection_generation=7,
    )
    assert state["flow_spec_version"] == 2
    assert state["flow_spec_fingerprint"] == flow_spec_fingerprint(repair)
    assert state["submission_kind"] == "repair"
    assert state["flow_spec"].title == "repair"


def test_old_connection_cannot_overwrite_new_generation_checkpoint() -> None:
    current = _spec(4, title="new connection")
    state = {
        "connection_generation": 9,
        "flow_spec": current,
        "flow_spec_version": 4,
        "flow_spec_fingerprint": flow_spec_fingerprint(current),
    }

    assert not gateway._checkpoint_accepted_recording_pi_submission(
        state,
        _spec(5, title="late old connection"),
        submission_kind="repair",
        connection_generation=8,
    )
    assert state["flow_spec"].title == "new connection"
    assert state["flow_spec_version"] == 4


def test_same_version_with_different_fingerprint_is_rejected() -> None:
    accepted = _spec(3, title="accepted")
    state = {
        "connection_generation": 2,
        "flow_spec": accepted,
        "flow_spec_version": 3,
        "flow_spec_fingerprint": flow_spec_fingerprint(accepted),
    }

    with pytest.raises(RuntimeError, match="fingerprint conflict"):
        gateway._checkpoint_accepted_recording_pi_submission(
            state,
            _spec(3, title="conflicting"),
            submission_kind="plan",
            connection_generation=2,
        )


@pytest.mark.asyncio
async def test_pi_submission_checkpoints_before_validation_response(monkeypatch) -> None:  # noqa: ANN001
    current = _spec(0)
    updated = _spec(1, title="accepted before response")
    checkpoints: list[tuple[FlowSpec, str]] = []
    session = recording_pi.RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "a" * 32,
        on_submission_accepted=lambda spec, kind: checkpoints.append((spec, kind)),
    )
    session.flow_spec = current

    async def apply(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        return updated

    monkeypatch.setattr(
        "dano.execution.page.flow_spec.apply_recording_agent_submission",
        apply,
    )
    validation_started = asyncio.Event()

    def validate(spec):  # noqa: ANN001, ANN202
        assert checkpoints == [(updated, "plan")]
        validation_started.set()
        return {"ok": True, "version": spec.meta["current_version"]}

    monkeypatch.setattr(
        "dano.execution.page.flow_spec.recording_agent_validation",
        validate,
    )

    result = await session.apply_submission({}, mode="plan", base_flow_version=0)

    assert validation_started.is_set()
    assert result == {"ok": True, "version": 1}
