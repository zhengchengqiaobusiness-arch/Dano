"""Recording start must tell the UI it is opening the page before navigation finishes."""

from __future__ import annotations

import asyncio

import pytest

from dano.onboarding.recording_gateway import RecordingGatewaySession, RecordingSessionConfig
from dano.onboarding.recording_workflow import WorkflowStatus


class _SlowRecordSession:
    def __init__(self, **_kwargs) -> None:
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()
        self.screencast = False

    async def start(self, *_args, **_kwargs) -> None:
        self.started.set()
        await self.proceed.wait()

    async def start_screencast(self, _callback) -> None:
        self.screencast = True

    async def stop(self) -> None:
        return None

    def captured_all_requests(self) -> list:
        return []


async def _unused(*_args, **_kwargs):  # noqa: ANN002, ANN003
    raise AssertionError("not used")


@pytest.mark.asyncio
async def test_start_marks_recording_before_page_navigation(monkeypatch) -> None:
    snapshots: list[str] = []

    async def send(payload: dict) -> None:
        if payload.get("type") == "snapshot":
            snapshots.append(str((payload.get("snapshot") or {}).get("status") or ""))

    monkeypatch.setattr(
        "dano.onboarding.recording_gateway.RecordSession",
        _SlowRecordSession,
    )
    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="tenant",
            subsystem="oa",
            recording_id="recording_open",
            action="action_open",
            start_url="http://example.test/app",
        ),
        send=send,
        pi_factory=_unused,
        publisher=_unused,
    )
    task = asyncio.create_task(session.start())

    async def _capture_ready() -> None:
        while session.capture is None:
            await asyncio.sleep(0)
        await session.capture.started.wait()

    await asyncio.wait_for(_capture_ready(), timeout=2)
    assert session.workflow is not None
    assert session.workflow.snapshot.status == WorkflowStatus.RECORDING
    assert session.workflow.snapshot.progress.label == "正在打开业务页面"
    assert "recording" in snapshots
    assert session.capture.screencast is False

    session.capture.proceed.set()
    await asyncio.wait_for(task, timeout=2)
    assert session.capture.screencast is True
    assert session.workflow.snapshot.status == WorkflowStatus.RECORDING
    live = getattr(session, "_live_task", None)
    if live is not None:
        live.cancel()
