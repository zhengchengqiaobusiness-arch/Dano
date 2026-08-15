from __future__ import annotations

import asyncio
import inspect
import re
import sys
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from dano.gateway import app as gateway
from dano.onboarding.recording_workflow import CANONICAL_RECORDING_COMMANDS


def test_linux_auto_export_uses_the_same_runtime_directory_as_the_ui(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    assert gateway._default_export_dir() == "/opt/dano/runtime-data/.agents/skills"


@pytest.mark.asyncio
async def test_recording_auto_export_only_writes_the_current_skill(monkeypatch, tmp_path) -> None:
    import dano.export.agent_skills as exports

    calls: list[dict[str, Any]] = []

    async def write_exports(tenant, out_dir, **kwargs):  # noqa: ANN001
        calls.append({"tenant": tenant, "out_dir": out_dir, **kwargs})
        return ["new-package"]

    async def frozen_skill_ids() -> set[str]:
        return {"system.frozen"}

    monkeypatch.setattr(exports, "write_exports", write_exports)
    monkeypatch.setattr(gateway, "_current_export_dir", lambda: str(tmp_path))
    monkeypatch.setattr(gateway, "_frozen_skill_ids", frozen_skill_ids)

    await gateway._auto_export("tenant-a", skill_ids={"system.action_unique"})

    assert calls == [{
        "tenant": "tenant-a",
        "out_dir": str(tmp_path),
        "mode": "package",
        "exclude_skill_ids": {"system.frozen"},
        "skill_ids": {"system.action_unique"},
    }]


@pytest.mark.asyncio
async def test_strict_recording_export_propagates_failure(monkeypatch) -> None:
    import dano.export.agent_skills as exports

    async def fail(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise OSError("disk full")

    monkeypatch.setattr(exports, "write_exports", fail)

    with pytest.raises(OSError, match="disk full"):
        await gateway._auto_export("tenant-a", skill_ids={"system.action"}, strict=True)


def test_recording_action_is_safe_and_process_unique() -> None:
    values = {gateway._new_recording_action() for _ in range(100)}

    assert len(values) == 100
    assert all(re.fullmatch(r"action_[0-9a-f]{32}", value) for value in values)


@pytest.mark.asyncio
async def test_pi_candidate_is_closed_when_start_fails() -> None:
    class Candidate:
        closed = False

        async def start(self) -> None:
            raise RuntimeError("start failed")

        async def close(self) -> None:
            self.closed = True

    candidate = Candidate()
    with pytest.raises(RuntimeError, match="start failed"):
        await gateway._start_recording_pi_candidate(lambda: candidate)

    assert candidate.closed is True


@pytest.mark.asyncio
async def test_send_queue_serializes_controls_and_coalesces_frames() -> None:
    class Socket:
        sent: list[dict[str, Any]] = []

        async def send_json(self, payload: dict[str, Any]) -> None:
            await asyncio.sleep(0)
            self.sent.append(payload)

    socket = Socket()
    queue = gateway._WebSocketSendQueue(socket)
    await asyncio.gather(
        queue.send_json({"type": "frame", "seq": 1}),
        queue.send_json({"type": "frame", "seq": 2}),
        queue.send_json({"type": "snapshot", "snapshot": {"revision": 1}}),
    )
    await queue.close()

    assert any(item["type"] == "snapshot" for item in socket.sent)
    frames = [item for item in socket.sent if item["type"] == "frame"]
    assert frames[-1]["seq"] == 2


class _FakeWebSocket:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = iter(messages)
        self.sent: list[dict[str, Any]] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict[str, Any]:
        try:
            return next(self.messages)
        except StopIteration as exc:
            raise WebSocketDisconnect() from exc

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_websocket_is_a_thin_transport_for_the_canonical_session(monkeypatch) -> None:
    import dano.onboarding.recording_gateway as recording_gateway

    instances = []

    class FakeSession:
        def __init__(self, *, config, send, pi_factory, publisher) -> None:  # noqa: ANN001
            self.config = config
            self.send = send
            self.pi_factory = pi_factory
            self.publisher = publisher
            self.dispatched: list[dict[str, Any]] = []
            self.closed = False
            self.capture = None
            self.workflow = None
            self._pi = None
            self.detached = False
            instances.append(self)

        async def start(self) -> None:
            await self.send({
                "type": "snapshot",
                "snapshot": {
                    "action": self.config.action,
                    "status": "recording",
                    "revision": 1,
                },
            })

        async def dispatch(self, message: dict[str, Any]) -> None:
            self.dispatched.append(message)

        async def close(self) -> None:
            self.closed = True

        async def attach(self, send) -> None:  # noqa: ANN001
            self.send = send

        def detach(self, send) -> None:  # noqa: ANN001
            self.detached = True

    monkeypatch.setattr(recording_gateway, "RecordingGatewaySession", FakeSession)
    socket = _FakeWebSocket([
        {
            "type": "start",
            "start_url": "https://example.test/page",
            "tenant": "tenant-a",
            "goal_text": "完成目标操作",
        },
        {"type": "input", "event": {"kind": "key", "key": "Enter"}},
        {"type": "finish", "title": "目标能力"},
    ])

    gateway._recording_session_registry = None
    await gateway.record_ws(socket)

    assert socket.accepted is True
    assert len(instances) == 1
    assert instances[0].dispatched == [
        {"type": "input", "event": {"kind": "key", "key": "Enter"}},
        {"type": "finish", "title": "目标能力"},
    ]
    assert instances[0].closed is False
    assert instances[0].detached is True
    assert socket.sent[0]["type"] == "snapshot"
    await gateway._recording_session_registry.close()
    gateway._recording_session_registry = None


@pytest.mark.asyncio
async def test_closed_websocket_receive_race_is_treated_as_disconnect(monkeypatch) -> None:
    import dano.onboarding.recording_gateway as recording_gateway

    class FakeSession:
        def __init__(self, *, config, send, pi_factory, publisher) -> None:  # noqa: ANN001
            self.config = config
            self.capture = None
            self.workflow = None
            self._pi = None

        async def start(self) -> None:
            return None

        async def attach(self, _send) -> None:  # noqa: ANN001
            return None

        def detach(self, _send) -> None:  # noqa: ANN001
            return None

        async def close(self) -> None:
            return None

    class ClosedSocket(_FakeWebSocket):
        async def receive_json(self) -> dict[str, Any]:
            if not self.accepted:
                raise AssertionError("socket was not accepted")
            try:
                return next(self.messages)
            except StopIteration as exc:
                raise RuntimeError(
                    'WebSocket is not connected. Need to call "accept" first.'
                ) from exc

    failures: list[str] = []
    monkeypatch.setattr(recording_gateway, "RecordingGatewaySession", FakeSession)
    monkeypatch.setattr(
        gateway.log,
        "exception",
        lambda _event, **kwargs: failures.append(str(kwargs.get("error") or "")),
    )
    socket = ClosedSocket([{
        "type": "start",
        "start_url": "https://example.test/page",
        "tenant": "tenant-a",
        "goal_text": "完成目标操作",
    }])

    gateway._recording_session_registry = None
    await gateway.record_ws(socket)

    assert failures == []
    await gateway._recording_session_registry.close()
    gateway._recording_session_registry = None


def test_gateway_registers_one_recording_route_and_no_legacy_branches() -> None:
    source = inspect.getsource(gateway.record_ws)
    app_source = inspect.getsource(gateway)

    assert app_source.count('@app.websocket("/onboarding/page/record")') == 1
    assert "RecordingSessionRegistry" in source
    assert "await session.dispatch(message)" in source
    assert "await session.close()" not in source
    assert "_retired_record_ws" not in app_source
    for retired in (
        "orchestrate_flow", "auto_fix_flow", "publish_request", "finalize",
        "flow_update", "refresh_flow_spec", "analysis_terminated",
    ):
        assert retired not in source


def test_canonical_command_set_is_small_and_explicit() -> None:
    assert CANONICAL_RECORDING_COMMANDS == {
        "start", "input", "finish", "patch_draft", "republish", "answer", "cancel", "ping",
    }
