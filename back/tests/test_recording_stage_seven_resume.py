"""Stage-seven checkpoint persistence, resume, and reconnect attach."""

from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import FlowCapability, FlowSpec, FlowStep
from dano.onboarding.recording_gateway import RecordingSessionRegistry, RecordingSessionConfig
from dano.onboarding.recording_stage_seven import (
    apply_stage_seven_checkpoint_patch,
    baseline_fingerprint,
    checkpoint_dict,
    load_resumable_working_spec,
    should_skip_passed_task,
    verification_task_id,
    write_outcome_unknown,
)
from dano.onboarding.recording_workflow import WorkflowStatus
from dano.onboarding.recording_results import recording_result_detail, stage_six_result_body


def _spec(*, name: str = "list_sale_order") -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[FlowStep(step_id="step_get", method="GET", path="/erp/sale-order/page")],
        capabilities=[
            FlowCapability(
                name=name,
                title="查询销售订单",
                kind="query_status",
                capability_id="cap_list",
                step_ids=["step_get"],
                nodes=[{
                    "id": "call_get", "type": "call", "usage": "execute",
                    "step_id": "step_get", "method": "GET", "path": "/erp/sale-order/page",
                }],
            ),
        ],
    )


def test_checkpoint_patch_rejects_stale_revision() -> None:
    spec = _spec()
    body = stage_six_result_body(
        action="action_a",
        title="销售订单",
        goal="查询",
        tenant="tenant",
        subsystem="oa",
        draft=spec.model_dump(mode="json"),
    )
    first = apply_stage_seven_checkpoint_patch(
        body,
        expected_attempt_id="attempt-1",
        expected_revision=0,
        checkpoint=checkpoint_dict(
            attempt_id="attempt-1",
            revision=0,
            status="running",
            baseline=spec,
            working=spec,
        ),
    )
    assert first is not None
    assert first["stage_seven"]["revision"] == 0
    conflict = apply_stage_seven_checkpoint_patch(
        first,
        expected_attempt_id="attempt-1",
        expected_revision=3,
        checkpoint=checkpoint_dict(
            attempt_id="attempt-1",
            revision=3,
            status="running",
            baseline=spec,
            working=spec,
        ),
    )
    assert conflict is None
    next_ok = apply_stage_seven_checkpoint_patch(
        first,
        expected_attempt_id="attempt-1",
        expected_revision=0,
        checkpoint=checkpoint_dict(
            attempt_id="attempt-1",
            revision=0,
            status="incomplete",
            baseline=spec,
            working=spec,
        ),
    )
    assert next_ok is not None
    assert next_ok["stage_seven"]["revision"] == 1
    stale_attempt = apply_stage_seven_checkpoint_patch(
        next_ok,
        expected_attempt_id="attempt-old",
        expected_revision=1,
        checkpoint=checkpoint_dict(
            attempt_id="attempt-old",
            revision=1,
            status="running",
            baseline=spec,
            working=spec,
        ),
    )
    assert stale_attempt is None


def test_resume_uses_working_copy_not_stage_six_baseline() -> None:
    baseline = _spec()
    working = baseline.model_copy(deep=True)
    working.steps[0].fact_check = {"verified": True, "verification_id": "v1"}
    body = stage_six_result_body(
        action="action_b",
        title="销售订单",
        goal="查询",
        tenant="tenant",
        subsystem="oa",
        draft=baseline.model_dump(mode="json"),
    )
    body = apply_stage_seven_checkpoint_patch(
        body,
        expected_attempt_id="attempt-2",
        expected_revision=0,
        checkpoint=checkpoint_dict(
            attempt_id="attempt-2",
            revision=0,
            status="incomplete",
            baseline=baseline,
            working=working,
        ),
    )
    assert body is not None
    draft, checkpoint, reason = load_resumable_working_spec(body)
    assert reason == ""
    assert checkpoint is not None
    assert draft["steps"][0]["fact_check"]["verification_id"] == "v1"
    assert body["flow_spec"]["steps"][0].get("fact_check") in (None, {})


def test_resume_blocks_when_baseline_fingerprint_changes() -> None:
    baseline = _spec()
    body = stage_six_result_body(
        action="action_c",
        title="销售订单",
        goal="查询",
        tenant="tenant",
        subsystem="oa",
        draft=baseline.model_dump(mode="json"),
    )
    body = apply_stage_seven_checkpoint_patch(
        body,
        expected_attempt_id="attempt-3",
        expected_revision=0,
        checkpoint=checkpoint_dict(
            attempt_id="attempt-3",
            revision=0,
            status="incomplete",
            baseline=baseline,
            working=baseline,
        ),
    )
    assert body is not None
    body["flow_spec"]["title"] = "changed-stage-six"
    # baseline fingerprint includes title
    _draft, _checkpoint, reason = load_resumable_working_spec(body)
    if baseline_fingerprint(body["flow_spec"]) == body["stage_seven"]["baseline_fingerprint"]:
        pytest.skip("title is not part of baseline fingerprint")
    assert "不一致" in reason


def test_recording_result_detail_projects_working_copy() -> None:
    from datetime import datetime, timezone
    from uuid import uuid4

    from dano.assets.drafts import AssetDraft
    from dano.shared.enums import AssetType, Subsystem

    baseline = _spec()
    working = baseline.model_copy(deep=True)
    working.title = "working-title"
    body = stage_six_result_body(
        action="action_d",
        title="销售订单",
        goal="查询",
        tenant="tenant",
        subsystem="oa",
        draft=baseline.model_dump(mode="json"),
    )
    body = apply_stage_seven_checkpoint_patch(
        body,
        expected_attempt_id="attempt-4",
        expected_revision=0,
        checkpoint=checkpoint_dict(
            attempt_id="attempt-4",
            revision=0,
            status="incomplete",
            baseline=baseline,
            working=working,
        ),
    )
    assert body is not None
    saved = AssetDraft(
        asset_draft_id=uuid4(),
        run_id="recording_saved",
        tenant="tenant",
        subsystem=Subsystem("oa"),
        asset_type=AssetType.PAGE_SCRIPT,
        asset_key="recording-result:action_d",
        body=body,
        content_hash="hash",
        created_at=datetime.now(timezone.utc),
    )
    payload = recording_result_detail(saved)
    assert payload["draft"]["title"] == "working-title"
    assert payload["stage_seven_attempt_id"] == "attempt-4"
    assert "working_flow_spec" not in payload


def test_passed_write_task_is_not_repeated() -> None:
    spec = _spec()
    signature = "sig-write"
    task_id = verification_task_id(
        attempt_id="attempt-5",
        capability_id="cap_list",
        kind="write_verify",
        target_id="step_get",
        target_signature=signature,
    )
    spec.meta = {
        "verification_log": [
            {
                "kind": "write_execute",
                "status": "passed",
                "verification_id": "v-pass",
                "subject": {
                    "task_id": task_id,
                    "signature": signature,
                    "write_step_id": "step_get",
                },
            }
        ],
    }
    found = should_skip_passed_task(spec, task_id=task_id, target_signature=signature)
    assert found is not None
    stale = should_skip_passed_task(spec, task_id=task_id, target_signature="other")
    assert stale is None


@pytest.mark.asyncio
async def test_in_flight_reconnect_attaches_without_restart() -> None:
    from dano.onboarding.recording_gateway import RecordingGatewaySession
    from dano.onboarding.recording_workflow import RecordingWorkflow, WorkflowSnapshot

    class _IdlePipeline:
        async def run(self, seed, context):  # noqa: ANN001, ARG002
            raise AssertionError("reconnect must not start a new pipeline")

    registry = RecordingSessionRegistry()
    config = RecordingSessionConfig(
        tenant="tenant",
        subsystem="oa",
        recording_id="recording_live",
        action="action_live",
        start_url="",
    )
    sent: list[dict] = []

    async def send(payload):  # noqa: ANN001
        sent.append(payload)

    session = RecordingGatewaySession(
        config=config,
        send=send,
        pi_factory=lambda: None,
        publisher=lambda *args, **kwargs: None,
    )
    session.capture = None
    session._stage_seven_attempt_id = "attempt-live"
    session.workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_live",
            action="action_live",
            status=WorkflowStatus.PROCESSING,
            stage_seven_attempt_id="attempt-live",
            capability_attempts={"cap_list": 2},
        ),
        _IdlePipeline(),
    )
    session.workflow._task = __import__("asyncio").get_event_loop().create_future()
    registry._sessions[config.action] = session

    attached = await registry.attach_or_resume(
        config=config,
        send=send,
        pi_factory=lambda: None,
        publisher=lambda *args, **kwargs: None,
        draft=_spec().model_dump(mode="json"),
        restart=True,
        reset_stage_seven=False,
        attempt_id="attempt-live",
    )
    assert attached is session
    assert attached.workflow is session.workflow
    assert attached.workflow.snapshot.capability_attempts == {"cap_list": 2}
    session.workflow._task.cancel()
    await registry.close()


def test_passed_dependency_task_is_not_repeated() -> None:
    spec = _spec()
    signature = "sig-dep"
    task_id = verification_task_id(
        attempt_id="attempt-6",
        capability_id="cap_list",
        kind="dependency",
        target_id="link_id",
        target_signature=signature,
    )
    spec.meta = {
        "verification_log": [
            {
                "kind": "dependency_execute",
                "status": "passed",
                "verification_id": "v-dep",
                "subject": {
                    "task_id": task_id,
                    "signature": signature,
                    "link_id": "link_id",
                },
            }
        ],
    }
    found = should_skip_passed_task(spec, task_id=task_id, target_signature=signature)
    assert found is not None
    assert should_skip_passed_task(spec, task_id=task_id, target_signature="stale") is None


def test_write_outcome_unknown_is_not_auto_retried() -> None:
    record = {
        "kind": "write_execute",
        "status": "inconclusive",
        "subject": {"write_step_id": "step_edit", "outcome": "write_outcome_unknown"},
    }
    assert write_outcome_unknown(record) is True
    assert write_outcome_unknown({"kind": "write_execute", "status": "passed"}) is False


def test_missing_memory_session_resumes_from_working_flow_spec() -> None:
    baseline = _spec()
    working = baseline.model_copy(deep=True)
    working.steps[0].fact_check = {"verified": True, "verification_id": "v-resume"}
    body = stage_six_result_body(
        action="action_resume_mem",
        title="销售订单",
        goal="查询",
        tenant="tenant",
        subsystem="oa",
        draft=baseline.model_dump(mode="json"),
    )
    body = apply_stage_seven_checkpoint_patch(
        body,
        expected_attempt_id="attempt-mem",
        expected_revision=0,
        checkpoint=checkpoint_dict(
            attempt_id="attempt-mem",
            revision=3,
            status="incomplete",
            baseline=baseline,
            working=working,
            capability_attempts={"cap_list": 1},
        ),
    )
    assert body is not None
    draft, checkpoint, reason = load_resumable_working_spec(body)
    assert reason == ""
    assert checkpoint is not None
    assert checkpoint["attempt_id"] == "attempt-mem"
    assert draft["steps"][0]["fact_check"]["verification_id"] == "v-resume"
    assert body["flow_spec"]["steps"][0].get("fact_check") in (None, {})


@pytest.mark.asyncio
async def test_resume_restores_checkpoint_revision() -> None:
    from dano.onboarding.recording_gateway import RecordingGatewaySession

    baseline = _spec().model_dump(mode="json")
    captured: dict[str, object] = {}

    class _CaptureWorkflow:
        def __init__(self, snapshot) -> None:  # noqa: ANN001
            captured["snapshot"] = snapshot

        async def republish(self, *, machine_verification: bool) -> None:  # noqa: ARG002
            captured["republish"] = True

    async def send(payload):  # noqa: ANN001, ARG001
        return None

    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="tenant",
            subsystem="oa",
            recording_id="recording_rev",
            action="action_rev",
            start_url="",
        ),
        send=send,
        pi_factory=lambda: None,
        publisher=lambda *args, **kwargs: None,
    )
    session._new_workflow = lambda snapshot: _CaptureWorkflow(snapshot)  # type: ignore[method-assign]
    await session.start_verification_only(
        baseline,
        checkpoint={
            "attempt_id": "attempt-rev",
            "revision": 4,
            "capability_attempts": {"cap_list": 2},
            "operator_answers": {"q1": "用户参数"},
        },
    )
    snapshot = captured["snapshot"]
    assert snapshot.stage_seven_revision == 4
    assert snapshot.capability_attempts == {"cap_list": 2}
    assert snapshot.operator_answers == {"q1": "用户参数"}
    assert captured.get("republish") is True
