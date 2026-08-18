from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from dano.agent_tools.app import call_tool
from dano.agent_tools.tools import ToolError
from dano.infra import logging as logging_mod
from dano.infra import run_logging
from dano.onboarding.recording_pi import RecordingPiSession
from dano.onboarding.recording_workflow import (
    PipelineCheck,
    PipelineOutcome,
    PipelineSeed,
    RecordingWorkflow,
    SelfHealingPipeline,
    WorkflowSnapshot,
    WorkflowStatus,
    WorkflowStep,
)


@pytest.fixture
def log_env(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("DANO_LOG_ROOT", str(tmp_path))
    monkeypatch.setenv("DANO_LOG_LEVEL", "INFO")
    logging_mod._CONFIGURED = False
    run_logging.reset_logging_state_for_tests()
    logging_mod.configure_logging("INFO")
    return tmp_path


def _events(tmp_path: Path, run_id: str) -> list[dict]:
    day = datetime.now().strftime("%Y-%m-%d")
    path = tmp_path / "runs" / day / f"{run_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _field(event: dict, key: str, default=None):
    if key in event:
        return event[key]
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    return details.get(key, default)


class _FakeRequest:
    def __init__(self, body: dict) -> None:
        self._body = body

    async def json(self) -> dict:
        return self._body


def _bind(run_id: str) -> None:
    run_logging.bind_run_context(
        run_id=run_id,
        recording_id="recording_1",
        action="action_1",
        tenant="111",
        subsystem="admin",
    )


@pytest.mark.asyncio
async def test_tool_events_share_call_id(log_env, monkeypatch) -> None:
    import dano.agent_tools.app as app_mod

    async def fake_state(_run_id, _params):
        return {"flow_version": 3, "facts": {}, "current_contract": {}, "validation": {}, "projection": {}}

    monkeypatch.setattr(app_mod.runs, "is_valid", lambda *_args: True)
    monkeypatch.setitem(app_mod.TOOLS, "get_recording_state", fake_state)
    _bind("run-tool")
    await call_tool("get_recording_state", _FakeRequest({"run_id": "run-tool", "params": {}}), x_agent_token="tok")
    events = _events(log_env, "run-tool")
    call = next(item for item in events if item["event"] == "agent_tool.call")
    done = next(item for item in events if item["event"] == "agent_tool.done")
    assert _field(call, "call_id")
    assert _field(call, "call_id") == _field(done, "call_id")
    assert _field(call, "span_id") == _field(done, "span_id")
    assert _field(done, "flow_version") == 3 or _field(done, "output_summary", {}).get("flow_version") == 3


@pytest.mark.asyncio
async def test_rejected_plan_keeps_previous_and_stays_on_console(log_env, monkeypatch, capsys) -> None:
    import dano.agent_tools.app as app_mod

    async def reject(_run_id, _params):
        raise ToolError("plan rejected")

    monkeypatch.setattr(app_mod.runs, "is_valid", lambda *_args: True)
    monkeypatch.setitem(app_mod.TOOLS, "submit_recording_plan", reject)
    _bind("run-reject")
    with pytest.raises(Exception):
        await call_tool(
            "submit_recording_plan",
            _FakeRequest({"run_id": "run-reject", "params": {"base_flow_version": 1, "plan": {"capabilities": []}}}),
            x_agent_token="tok",
        )
    events = _events(log_env, "run-reject")
    rejected = next(item for item in events if item["event"] == "agent_tool.rejected")
    assert _field(rejected, "kept_previous_plan") is True
    assert rejected["level"] == "warning"
    assert "被拒绝" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_unchanged_polling_is_jsonl_only(log_env, monkeypatch, capsys) -> None:
    import dano.agent_tools.app as app_mod

    async def fake_state(_run_id, _params):
        return {"flow_version": 1, "facts": {}, "current_contract": {}, "validation": {}, "projection": {}}

    monkeypatch.setattr(app_mod.runs, "is_valid", lambda *_args: True)
    monkeypatch.setitem(app_mod.TOOLS, "get_recording_state", fake_state)
    _bind("run-poll")
    await call_tool("get_recording_state", _FakeRequest({"run_id": "run-poll", "params": {}}), x_agent_token="tok")
    capsys.readouterr()
    await call_tool("get_recording_state", _FakeRequest({"run_id": "run-poll", "params": {}}), x_agent_token="tok")
    output = capsys.readouterr().out
    events = [item for item in _events(log_env, "run-poll") if item["event"] == "agent_tool.done"]
    assert len(events) == 2
    assert events[1]["level"] == "debug"
    assert "无变化" not in output


@pytest.mark.asyncio
async def test_plan_log_contains_capability_and_version(log_env, monkeypatch) -> None:
    import dano.agent_tools.app as app_mod

    async def accept(_run_id, params):
        return {
            "flow_version": 2,
            "accepted": True,
            "unchanged": False,
            "op_results": [{"status": "applied"}, {"status": "rejected"}],
            "must_retry": [2],
            "capability_plan_complete": True,
        }

    monkeypatch.setattr(app_mod.runs, "is_valid", lambda *_args: True)
    monkeypatch.setitem(app_mod.TOOLS, "submit_recording_plan", accept)
    _bind("run-plan")
    await call_tool(
        "submit_recording_plan",
        _FakeRequest({
            "run_id": "run-plan",
            "params": {
                "base_flow_version": 1,
                "plan": {
                    "capabilities": [{"name": "query_leave"}],
                    "ops": [{"op": "set_goal"}],
                    "unresolved_items": [{"id": "u1"}],
                },
            },
        }),
        x_agent_token="tok",
    )
    done = next(item for item in _events(log_env, "run-plan") if item["event"] == "agent_tool.done")
    assert _field(done, "submitted_capability_count") == 1
    assert _field(done, "submitted_capability_names") == ["query_leave"]
    assert _field(done, "submitted_operation_count") == 1
    assert _field(done, "applied_count") == 1
    assert _field(done, "rejected_count") == 1
    assert _field(done, "flow_version_before") == 1
    assert _field(done, "flow_version_after") == 2
    assert done["status"] == "succeeded"


@pytest.mark.asyncio
async def test_batch_started_and_completed_share_span(log_env) -> None:
    from dano.onboarding.recording_gateway import RecordingGatewaySession, RecordingSessionConfig

    class Capture:
        def captured_all_requests(self) -> list[dict]:
            return [{"request_id": "r1"}, {"request_id": "r2"}]

        def recorded_page_events(self) -> list[dict]:
            return []

        def recorded_field_evidence(self) -> list[dict]:
            return []

    class Pi:
        flow_spec = None

        async def notify_live_batch(self, delta):  # noqa: ANN001
            assert delta["reason"] == "request_batch"
            return {"status": "submitted"}

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("unused")

    _bind("run-batch")
    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="111",
            subsystem="admin",
            recording_id="recording_" + "a" * 32,
            action="action_1",
            start_url="https://example.invalid",
        ),
        send=None,
        pi_factory=unused,
        publisher=unused,
    )
    session.capture = Capture()  # type: ignore[assignment]
    session._pi = Pi()
    session._last_live_count = 0
    session._live_pending_reason = "request_batch"
    await session._drain_live()
    events = _events(log_env, "run-batch")
    started = next(item for item in events if item["event"] == "recording.batch.started")
    completed = next(item for item in events if item["event"] == "recording.batch.completed")
    assert started["span_id"] == completed["span_id"]
    assert _field(started, "batch_id") == _field(completed, "batch_id")
    assert _field(completed, "next_seq") == 2
    assert "has_more" in completed or "has_more" in completed.get("details", {})


@pytest.mark.asyncio
async def test_drain_live_failure_keeps_recording_and_logs_traceback(log_env) -> None:
    from dano.onboarding.recording_gateway import RecordingGatewaySession, RecordingSessionConfig

    class Capture:
        def captured_all_requests(self) -> list[dict]:
            return [{"request_id": "r1"}]

    class Workflow:
        async def update_recording(self, **kwargs):  # noqa: ANN003
            self.kwargs = kwargs

    class Pi:
        flow_spec = None

        async def notify_live_batch(self, _delta):
            raise RuntimeError("model timeout")

    async def unused(*_args):  # noqa: ANN002
        raise AssertionError("unused")

    _bind("run-batch-fail")
    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="111",
            subsystem="admin",
            recording_id="recording_" + "b" * 32,
            action="action_1",
            start_url="https://example.invalid",
        ),
        send=None,
        pi_factory=unused,
        publisher=unused,
    )
    session.capture = Capture()  # type: ignore[assignment]
    session._pi = Pi()
    session.workflow = Workflow()  # type: ignore[assignment]
    session._live_pending_reason = "request_batch"
    await session._drain_live()
    failed = next(item for item in _events(log_env, "run-batch-fail") if item["event"] == "recording.batch.failed")
    assert failed["error"]["traceback"]
    assert session.workflow.kwargs["insights"][-1]["kind"] == "analysis_error"


@pytest.mark.asyncio
async def test_verification_skipped_does_not_start_check(log_env) -> None:
    calls: list[str] = []

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return {"capabilities": []}

        async def check(self, draft, context):  # noqa: ANN001
            calls.append("check")
            return PipelineCheck(draft=draft)

        async def repair(self, draft, issues, answers, context):  # noqa: ANN001
            calls.append("repair")
            return draft

        async def publish(self, draft, context):  # noqa: ANN001
            calls.append("publish")
            return {"skill_id": "admin.action_1"}

    _bind("run-skip")
    pipeline = SelfHealingPipeline(Runtime())
    workflow = RecordingWorkflow(
        WorkflowSnapshot(run_id="run-skip", action="action_1", status=WorkflowStatus.RECORDING),
        pipeline,
    )
    await workflow.finish(machine_verification=False)
    await workflow.wait()
    events = _events(log_env, "run-skip")
    skipped = next(item for item in events if item["event"] == "recording.verification.skipped")
    assert skipped["summary"] == "verification skipped"
    assert skipped["status"] == "skipped"
    assert calls == ["publish"]
    assert workflow.snapshot.status == WorkflowStatus.PUBLISHED


@pytest.mark.asyncio
async def test_workflow_terminal_event_is_logged(log_env) -> None:
    class Pipeline:
        async def run(self, seed, context):  # noqa: ANN001
            return PipelineOutcome(status=WorkflowStatus.FAILED, error="boom")

    _bind("run-end")
    workflow = RecordingWorkflow(
        WorkflowSnapshot(run_id="run-end", action="action_1", status=WorkflowStatus.RECORDING),
        Pipeline(),
    )
    await workflow.finish()
    await workflow.wait()
    completed = next(item for item in _events(log_env, "run-end") if item["event"] == "recording.run.completed")
    assert completed["status"] == "failed"


def test_structured_stderr_is_parsed(log_env) -> None:
    _bind("run-pi")
    session = RecordingPiSession.__new__(RecordingPiSession)
    session.run_id = "run-pi"
    session._record_stderr_line(json.dumps({
        "type": "recording_log",
        "event": "recording.skill.loaded",
        "stage": "analysis",
        "status": "succeeded",
        "skill_name": "analyze-recording-evidence",
        "analysis_phase": "base_state_analysis",
        "skill_sha256": "abc123",
        "session_id": "sid",
        "prompt": "p1",
        "turn": 1,
        "batch": "base_state_analysis",
    }))
    session._record_stderr_line("plain unknown line")
    session._record_stderr_line("FATAL provider exception boom")
    events = _events(log_env, "run-pi")
    loaded = next(item for item in events if item["event"] == "recording.skill.loaded")
    assert _field(loaded, "skill_name") == "analyze-recording-evidence"
    assert _field(loaded, "analysis_phase") == "base_state_analysis"
    unknown = [item for item in events if item["event"] == "recording.pi.stderr"]
    assert unknown[0]["level"] == "debug"
    assert unknown[1]["level"] == "warning"


def test_logging_does_not_mutate_flowspec(log_env) -> None:
    from dano.execution.page.flow_spec import FlowSpec

    spec = FlowSpec(tenant="111", subsystem="admin", meta={"current_version": 3})
    before = spec.model_dump()
    _bind("run-spec")
    run_logging.emit_run_event(
        "recording.batch.completed",
        details={"capability_count": 0},
        flow_version=3,
    )
    assert spec.model_dump() == before


def test_same_run_identity_across_recording_stages(log_env) -> None:
    _bind("run-id-1")
    run_logging.emit_run_event("recording.batch.started", stage="analysis", status="started")
    run_logging.emit_run_event("recording.freeze.completed", stage="freeze", status="succeeded")
    run_logging.emit_run_event("recording.workflow.transition", stage="workflow", status="progress")
    events = _events(log_env, "run-id-1")
    assert [item["sequence"] for item in events] == [1, 2, 3]
    for item in events:
        assert item["run_id"] == "run-id-1"
        assert item["recording_id"] == "recording_1"
        assert item["tenant"] == "111"
