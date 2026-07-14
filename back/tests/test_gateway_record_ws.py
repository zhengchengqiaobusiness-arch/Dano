from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest

from dano.gateway import app as gateway


class _ConcurrentWriteProbe:
    def __init__(self) -> None:
        self.active_writes = 0
        self.max_active_writes = 0
        self.messages: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.active_writes += 1
        self.max_active_writes = max(self.max_active_writes, self.active_writes)
        try:
            await asyncio.sleep(0)
            self.messages.append(message)
        finally:
            self.active_writes -= 1


@pytest.mark.asyncio
async def test_websocket_send_queue_serializes_concurrent_writes_and_drains() -> None:
    ws = _ConcurrentWriteProbe()
    sender = gateway._WebSocketSendQueue(ws)

    await asyncio.gather(*(sender.send_json({"index": index}) for index in range(30)))
    await sender.close()

    assert ws.max_active_writes == 1
    assert [message["index"] for message in ws.messages] == list(range(30))
    assert sender._writer.done()


@pytest.mark.asyncio
async def test_websocket_send_queue_coalesces_frames_without_dropping_controls() -> None:
    class SlowWriteProbe(_ConcurrentWriteProbe):
        def __init__(self) -> None:
            super().__init__()
            self.write_started = asyncio.Event()
            self.release_write = asyncio.Event()

        async def send_json(self, message: dict) -> None:
            self.write_started.set()
            await self.release_write.wait()
            await super().send_json(message)

    ws = SlowWriteProbe()
    sender = gateway._WebSocketSendQueue(ws)
    first_control = asyncio.create_task(sender.send_json({"type": "control", "index": 0}))
    await ws.write_started.wait()

    for index in range(1_000):
        assert sender.send_latest_frame({"type": "frame", "index": index})
    assert sender._queue.qsize() == 1
    assert sender._latest_frame == {"type": "frame", "index": 999}
    assert not sender._background

    second_control = asyncio.create_task(sender.send_json({"type": "control", "index": 1}))
    third_control = asyncio.create_task(sender.send_json({"type": "control", "index": 2}))
    await asyncio.sleep(0)
    assert sender._queue.qsize() == 3

    for index in range(1_000, 2_000):
        sender.send_latest_frame({"type": "frame", "index": index})
    assert sender._queue.qsize() == 3
    assert sender._latest_frame == {"type": "frame", "index": 1_999}

    ws.release_write.set()
    await asyncio.gather(first_control, second_control, third_control)
    await sender.close()

    assert ws.messages == [
        {"type": "control", "index": 0},
        {"type": "frame", "index": 1_999},
        {"type": "control", "index": 1},
        {"type": "control", "index": 2},
    ]
    assert ws.max_active_writes == 1


def test_recording_action_uses_safe_uuid_and_retries_recent_collision(monkeypatch) -> None:  # noqa: ANN001
    gateway._RECENT_RECORDING_ACTIONS.clear()
    repeated = SimpleNamespace(hex="1" * 32)
    fresh = SimpleNamespace(hex="2" * 32)
    generated = iter((repeated, repeated, fresh))
    monkeypatch.setattr(gateway.uuid, "uuid4", lambda: next(generated))

    first = gateway._new_recording_action()
    second = gateway._new_recording_action()

    assert first == f"action_{'1' * 32}"
    assert second == f"action_{'2' * 32}"
    assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", first)
    assert first != second


class _FakeWebSocket(_ConcurrentWriteProbe):
    def __init__(self, incoming: list[dict]) -> None:
        super().__init__()
        self.incoming = list(incoming)
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict:
        return self.incoming.pop(0)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_record_ws_started_action_is_unique_and_input_errors_are_recoverable(monkeypatch) -> None:  # noqa: ANN001
    import dano.execution.page.recorder as recorder_module
    import dano.infra.llm_control as llm_control

    sessions = []

    class FakeRecordSession:
        def __init__(self, on_step, on_request, **_kwargs) -> None:  # noqa: ANN001
            self.on_step = on_step
            self.on_request = on_request
            self.events: list[dict] = []
            self.stopped = False
            sessions.append(self)

        async def start(self, *_args, **_kwargs) -> None:
            return None

        async def start_screencast(self, on_frame) -> None:  # noqa: ANN001
            self.on_step({"op": "click"})
            self.on_request({"method": "POST"})
            await on_frame({"image": "frame"})

        async def dispatch_input(self, event: dict) -> dict:
            self.events.append(event)
            if len(self.events) == 1:
                return {
                    "ok": False,
                    "recoverable": True,
                    "kind": event.get("kind"),
                    "error": "target navigated",
                    "error_type": "TargetClosedError",
                }
            if len(self.events) == 2:
                raise RuntimeError("transient input failure")
            return {"ok": True}

        async def flush_recording(self) -> None:
            return None

        async def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(recorder_module, "RecordSession", FakeRecordSession)
    monkeypatch.setattr(llm_control, "begin_llm_budget", lambda _budget: object())
    monkeypatch.setattr(llm_control, "end_llm_budget", lambda _token: None)
    gateway._RECENT_RECORDING_ACTIONS.clear()

    def incoming() -> list[dict]:
        return [
            {"type": "start", "start_url": "https://example.test", "tenant": "tenant-a"},
            {"type": "input", "event": {"kind": "pointer_move", "nx": 0.1, "ny": 0.2}},
            {"type": "input", "event": {"kind": "dblclick", "nx": 0.3, "ny": 0.4}},
            {"type": "input", "event": {"kind": "pointer_up", "nx": 0.5, "ny": 0.6}},
            {"type": "stop"},
        ]

    first_ws = _FakeWebSocket(incoming())
    second_ws = _FakeWebSocket(incoming())
    await gateway.record_ws(first_ws)
    await gateway.record_ws(second_ws)

    first_started = next(message for message in first_ws.messages if message["type"] == "started")
    second_started = next(message for message in second_ws.messages if message["type"] == "started")
    assert re.fullmatch(r"action_[0-9a-f]{32}", first_started["action"])
    assert first_started["action"] != second_started["action"]

    errors = [message for message in first_ws.messages if message["type"] == "input_error"]
    assert [error["kind"] for error in errors] == ["pointer_move", "dblclick"]
    assert errors[0]["event"] == {"kind": "pointer_move", "nx": 0.1, "ny": 0.2}
    assert errors[0]["error_type"] == "TargetClosedError"
    assert errors[1]["detail"] == "transient input failure"
    assert all(error["recoverable"] for error in errors)
    assert sessions[0].events[-1]["kind"] == "pointer_up"
    assert all(session.stopped for session in sessions)
    assert first_ws.accepted and first_ws.closed
    assert first_ws.max_active_writes == 1
