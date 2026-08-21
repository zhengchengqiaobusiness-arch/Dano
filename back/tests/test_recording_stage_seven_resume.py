"""Stage-seven checkpoint persistence, resume, and reconnect attach."""

from __future__ import annotations

import asyncio

import pytest

from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowSpec,
    FlowStep,
)
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


def test_resume_uses_saved_spec_when_baseline_fingerprint_changes() -> None:
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
    if baseline_fingerprint(body["flow_spec"]) == body["stage_seven"]["baseline_fingerprint"]:
        pytest.skip("title is not part of baseline fingerprint")
    draft, checkpoint, reason = load_resumable_working_spec(body)
    assert reason == ""
    assert checkpoint is None
    assert draft["title"] == "changed-stage-six"


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


@pytest.mark.asyncio
async def test_cancel_persists_stage_seven_checkpoint() -> None:
    from dano.onboarding.recording_workflow import (
        PipelineCheck,
        RecordingWorkflow,
        SelfHealingPipeline,
        WorkflowSnapshot,
        WorkflowStatus,
    )

    saved: list[dict] = []

    async def persist(checkpoint):  # noqa: ANN001
        saved.append(dict(checkpoint))

    class _HangRuntime:
        async def prepare(self, seed, context):  # noqa: ANN001, ARG002
            return dict(seed.draft or {})

        async def check(self, draft, context):  # noqa: ANN001, ARG002
            await asyncio.sleep(30)
            return PipelineCheck(draft=draft, issues=())

        async def repair(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("repair must not run after cancel")

        async def publish(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("publish must not run after cancel")

    spec = _spec()
    workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="recording_cancel",
            action="action_cancel",
            status=WorkflowStatus.EDITABLE,
            draft=spec.model_dump(mode="json"),
            stage_seven_attempt_id="attempt-cancel",
        ),
        SelfHealingPipeline(_HangRuntime()),
        persist_stage_seven=persist,
        stage_six_baseline=spec.model_dump(mode="json"),
    )
    await workflow.republish(machine_verification=True)
    await asyncio.sleep(0.05)
    await workflow.cancel()
    await workflow.wait()
    assert saved
    assert saved[-1]["status"] == "cancelled"
    assert saved[-1]["working_flow_spec"]["capabilities"]


@pytest.mark.asyncio
async def test_add_verifications_consumes_evidence_and_persists() -> None:
    from dano.execution.page.verification_log import record_verification
    from dano.onboarding.recording_pi import RecordingPiSession

    spec = _spec()
    spec.meta = {"stage_seven": {"attempt_id": "attempt-ev"}}
    persisted: list[object] = []

    async def on_evidence(working):  # noqa: ANN001
        persisted.append(working)

    session = RecordingPiSession(
        tenant="tenant",
        subsystem="oa",
        recording_id="recording_" + ("b" * 32),
        resume_history=False,
    )
    session.bind_flow_spec(spec)
    session.bind_stage_seven_evidence_sink(baseline=spec, on_evidence=on_evidence)
    verification_id = record_verification(
        kind="dependency_execute",
        subject={"link_id": "link_missing"},
        status="passed",
        evidence={"ok": True},
    )
    added = await session.add_verifications([verification_id])
    assert added
    assert persisted
    log = list((persisted[0].meta or {}).get("verification_log") or [])
    assert any(item.get("verification_id") == verification_id for item in log)


@pytest.mark.asyncio
async def test_execute_write_skips_passed_and_unknown_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    from dano.agent_tools import tools as agent_tools
    from dano.execution.page.flow_spec import ParamField

    spec = FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_edit",
                method="PUT",
                path="/erp/sale-order/update",
                source_meta={"request_id": "req_update"},
                params=[ParamField(path="query.remark", key="remark", value="x")],
            ),
        ],
    )
    executed: list[int] = []

    class _Session:
        def current_flow_spec(self) -> FlowSpec:
            return spec

    def fake_session(run_id, params):  # noqa: ANN001, ARG001
        return _Session()

    async def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        executed.append(1)
        raise AssertionError("write must not be re-executed")

    monkeypatch.setattr(agent_tools, "_recording_session", fake_session)
    monkeypatch.setattr("dano.execution.page.replay.execute_write_with_verify", boom)
    spec.meta = {
        "verification_log": [
            {
                "kind": "write_execute",
                "status": "passed",
                "verification_id": "v-write",
                "subject": {"write_step_id": "step_edit"},
                "evidence": {"write": {"ok": True}, "verify": {"ok": True}},
            }
        ],
    }
    passed = await agent_tools.execute_recording_write_with_verify(
        "run",
        {
            "write_step_id": "step_edit",
            "verify_request_id": "req_get",
            "assertion": {"operator": "exists", "path": "id"},
        },
    )
    assert passed["duplicate"] is True
    assert passed["write_executed"] is False
    assert executed == []

    spec.meta = {
        "verification_log": [
            {
                "kind": "write_execute",
                "status": "inconclusive",
                "verification_id": "v-unknown",
                "subject": {
                    "write_step_id": "step_edit",
                    "outcome": "write_outcome_unknown",
                },
            }
        ],
    }
    unknown = await agent_tools.execute_recording_write_with_verify(
        "run",
        {
            "write_step_id": "step_edit",
            "verify_request_id": "req_get",
            "assertion": {"operator": "exists", "path": "id"},
        },
    )
    assert unknown["ok"] is False
    assert unknown["status"] == "write_outcome_unknown"
    assert executed == []
