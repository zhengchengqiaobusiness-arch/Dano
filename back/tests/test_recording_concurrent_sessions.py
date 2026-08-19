"""Stage 1: concurrent recordings must not abort another live capture."""

from __future__ import annotations

import pytest

from dano.onboarding.recording_gateway import (
    RecordingGatewaySession,
    RecordingSessionConfig,
    RecordingSessionRegistry,
)
from dano.onboarding.recording_workflow import WorkflowSnapshot, WorkflowStatus


class FakeCapture:
    def __init__(self) -> None:
        self.stopped = False
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stopped = True
        self.stop_calls += 1


class FakeWorkflow:
    def __init__(self, *, action: str, status: WorkflowStatus) -> None:
        self.snapshot = WorkflowSnapshot(run_id=f"run_{action}", action=action, status=status)


def _config(action: str, tenant: str = "tenant-a") -> RecordingSessionConfig:
    return RecordingSessionConfig(
        tenant=tenant,
        subsystem="oa",
        recording_id=f"rec_{action}",
        action=action,
        start_url="http://example.test/app",
    )


async def _unused(*_args, **_kwargs):  # noqa: ANN002, ANN003
    raise AssertionError("not used")


async def _silent_send(_payload: dict) -> None:
    return None


@pytest.fixture
def start_spy(monkeypatch):
    started: list[str] = []

    async def fake_start(self: RecordingGatewaySession) -> None:
        if self.capture is not None:
            return
        started.append(self.config.action)
        self.capture = FakeCapture()
        self.workflow = FakeWorkflow(action=self.config.action, status=WorkflowStatus.RECORDING)

    monkeypatch.setattr(RecordingGatewaySession, "start", fake_start)
    return started


async def test_same_tenant_can_record_two_actions_without_stopping_first_capture(start_spy) -> None:
    registry = RecordingSessionRegistry()
    session_a, created_a = await registry.attach_or_create(
        config=_config("action_a"),
        send=_silent_send,
        pi_factory=_unused,
        publisher=_unused,
    )
    session_b, created_b = await registry.attach_or_create(
        config=_config("action_b"),
        send=_silent_send,
        pi_factory=_unused,
        publisher=_unused,
    )

    assert created_a is True
    assert created_b is True
    assert start_spy == ["action_a", "action_b"]
    assert session_a is not session_b
    assert session_a.workflow.snapshot.status == WorkflowStatus.RECORDING
    assert session_b.workflow.snapshot.status == WorkflowStatus.RECORDING
    assert session_a.capture is not None
    assert session_a.capture.stopped is False
    assert session_a.capture.stop_calls == 0
    assert "action_a" in registry._sessions
    assert "action_b" in registry._sessions


async def test_same_action_reconnect_does_not_create_second_browser(start_spy) -> None:
    registry = RecordingSessionRegistry()
    first, created_first = await registry.attach_or_create(
        config=_config("action_a"),
        send=_silent_send,
        pi_factory=_unused,
        publisher=_unused,
    )
    first_capture = first.capture
    second, created_second = await registry.attach_or_create(
        config=_config("action_a"),
        send=_silent_send,
        pi_factory=_unused,
        publisher=_unused,
    )

    assert created_first is True
    assert created_second is False
    assert first is second
    assert start_spy == ["action_a"]
    assert second.capture is first_capture
    assert first_capture.stopped is False


async def test_new_recording_releases_only_terminal_leftover_capture(start_spy) -> None:
    registry = RecordingSessionRegistry()
    leftover = RecordingGatewaySession(
        config=_config("action_done"),
        send=_silent_send,
        pi_factory=_unused,
        publisher=_unused,
    )
    leftover.capture = FakeCapture()
    leftover.workflow = FakeWorkflow(action="action_done", status=WorkflowStatus.EDITABLE)
    registry._sessions["action_done"] = leftover

    live = RecordingGatewaySession(
        config=_config("action_live"),
        send=_silent_send,
        pi_factory=_unused,
        publisher=_unused,
    )
    live.capture = FakeCapture()
    live.workflow = FakeWorkflow(action="action_live", status=WorkflowStatus.RECORDING)
    registry._sessions["action_live"] = live

    await registry.attach_or_create(
        config=_config("action_new"),
        send=_silent_send,
        pi_factory=_unused,
        publisher=_unused,
    )

    assert leftover.capture is None
    assert live.capture is not None
    assert live.capture.stopped is False
    assert "action_done" in registry._sessions
    assert "action_live" in registry._sessions
    assert registry._sessions["action_live"].workflow.snapshot.status == WorkflowStatus.RECORDING


async def test_terminal_snapshot_still_releases_capture() -> None:
    session = RecordingGatewaySession(
        config=_config("action_a"),
        send=_silent_send,
        pi_factory=_unused,
        publisher=_unused,
    )
    capture = FakeCapture()
    session.capture = capture
    session.workflow = FakeWorkflow(action="action_a", status=WorkflowStatus.RECORDING)

    await session._on_snapshot(WorkflowSnapshot(
        run_id="run_action_a",
        action="action_a",
        status=WorkflowStatus.EDITABLE,
    ))

    assert capture.stopped is True
    assert session.capture is None
