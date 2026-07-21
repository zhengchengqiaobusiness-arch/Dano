from __future__ import annotations

import asyncio
import base64
import inspect
import re
from types import SimpleNamespace

import pytest

from dano.agent_tools import tools as agent_tools_module
from dano.execution.page.flow_spec import FlowSpec, FlowStep, ParamField
from dano.gateway import app as gateway


def test_analysis_screenshots_are_validated_and_reduced_to_pi_images() -> None:
    raw = b"\x89PNG\r\n\x1a\n" + b"screenshot-content"
    screenshots = gateway._normalize_analysis_screenshots([{
        "name": "form-screen.png",
        "mime_type": "image/png",
        "data": base64.b64encode(raw).decode("ascii"),
    }])

    assert screenshots == [{
        "name": "form-screen.png",
        "type": "image",
        "data": base64.b64encode(raw).decode("ascii"),
        "mimeType": "image/png",
        "byte_size": len(raw),
    }]
    assert gateway._pi_analysis_images(screenshots) == [{
        "type": "image",
        "data": base64.b64encode(raw).decode("ascii"),
        "mimeType": "image/png",
    }]
    assert "semantic evidence" in gateway._analysis_screenshot_guidance(screenshots)
    assert "strong references, not an admission gate" in gateway._analysis_screenshot_guidance(screenshots)
    protocol = gateway._recording_plan_protocol_guidance(has_screenshots=True)
    assert "field_semantics" in protocol and "step_id" in protocol and "wire_path" in protocol
    assert "control_kind" in protocol
    assert "user_param/user_input" in protocol
    assert "capability_relations" in protocol
    assert "from_output" in protocol and "to_input" in protocol
    assert "non-blocking unresolved item" in protocol
    assert "Never submit flow_spec" in protocol



def test_pi_image_delivery_count_is_reported_without_blocking_the_plan() -> None:
    assert gateway._verified_pi_image_count({"image_count": 2}, 2) == 2
    assert gateway._verified_pi_image_count({}, 0) == 0
    assert gateway._verified_pi_image_count({"image_count": 1}, 2) == 1


def test_analysis_screenshots_reject_spoofed_or_excess_images() -> None:
    jpeg = base64.b64encode(b"\xff\xd8\xffcontent").decode("ascii")
    with pytest.raises(ValueError, match="does not match"):
        gateway._normalize_analysis_screenshots([{
            "mime_type": "image/png", "data": jpeg,
        }])

    with pytest.raises(ValueError, match="at most 4"):
        gateway._normalize_analysis_screenshots([
            {"mime_type": "image/jpeg", "data": jpeg} for _ in range(5)
        ])


def test_no_analysis_screenshot_keeps_original_fact_based_path() -> None:
    assert gateway._normalize_analysis_screenshots(None) == []
    assert gateway._analysis_screenshot_guidance([]) == ""
    assert "screenshot-derived" not in gateway._recording_plan_protocol_guidance(has_screenshots=False)


def test_orchestrate_flow_logs_real_request_boundary_and_failure() -> None:
    source = inspect.getsource(gateway.record_ws)
    branch_start = source.index('elif t == "orchestrate_flow":')
    branch_end = source.index('elif t == "auto_fix_flow":', branch_start)
    branch = source[branch_start:branch_end]

    assert '"recording.operation_started"' in branch
    assert '"recording.operation_completed"' in branch
    assert '"recording.operation_failed"' in branch
    disconnect_guard = branch.index("except WebSocketDisconnect:")
    failure_handler = branch.index("except Exception as e:")
    assert disconnect_guard < failure_handler
    assert "raise" in branch[disconnect_guard:failure_handler]
    assert "_remember_costly(msg, error_response)" in branch
    assert '"operation_id": operation_id' in branch
    assert '"status": "rejected"' in branch
    assert "原配置保持不变" in branch
    assert "orchestrate_flow_capabilities" in branch
    assert "if not before_operation.capabilities:" in branch
    assert "needs_pi = bool(before_operation.capabilities or analysis_screenshots)" in branch
    assert "if needs_pi:" in branch
    assert 'generation_mode="initial"' in branch
    assert "timeout_s=3000" in branch
    assert "pending_flow_spec or before_operation" in branch
    assert '"operation_warning": str(e)' in branch
    assert '"type": "flow_spec"' in branch
    fallback = branch[branch.index("pending_flow_spec = await orchestrate_flow_capabilities"):]
    assert fallback.index("except WebSocketDisconnect:") < fallback.index("except Exception as fallback_error:")


def test_every_recording_pi_button_has_a_finite_timeout() -> None:
    source = inspect.getsource(gateway.record_ws)

    assert "timeout_s=0" not in source
    assert "timeout_s=3000" in source


@pytest.mark.parametrize(
    ("changed", "accepted", "expected_status"),
    [
        (True, True, "applied"),
        (False, True, "no_change"),
        (True, False, "rejected"),
    ],
)
def test_analysis_application_report_is_persistable_and_explicit(
    changed: bool,
    accepted: bool,
    expected_status: str,
) -> None:
    before = SimpleNamespace(
        capabilities=[object()],
        steps=[SimpleNamespace(params=[object()])],
    )
    after = SimpleNamespace(
        capabilities=[object(), object()],
        steps=[SimpleNamespace(
            step_id="submit",
            params=[
                SimpleNamespace(path="reason", label="原因", key="reason", locked=False),
                SimpleNamespace(path="remark", label="备注", key="remark", locked=False),
            ],
        )],
        meta={
            "capability_model": {
                "semantic_plan": {
                    "field_semantics": [{
                        "step_id": "submit", "wire_path": "reason",
                        "evidence": [{"source": "screenshot"}],
                    }, {
                        "step_id": "submit", "wire_path": "remark",
                        "evidence": [{"source": "screenshot"}],
                    }],
                    "unresolved_items": [],
                },
                "semantic_coverage": {"complete": True},
            },
            "capability_generation": {"last_mode": "optimize"},
        },
    )
    report = gateway._analysis_application_report(
        before=before,
        after=after,
        operation_report={
            "changed": changed,
            "summary": "analysis complete",
            "changes": {"capabilities": int(changed), "fields": int(changed)},
            "field_changes": ([{"step_id": "submit", "path": "reason", "name": "原因", "axes": {"type": {"before": "string", "after": "enum"}}}] if changed else []),
            "change_details": (["能力「提交申请」：名称已修改"] if changed else []),
            "proposal_gate": {"accepted": accepted, "reasons": []},
        },
        screenshots=[{"name": "form.png"}],
        delivered_image_count=1,
        operation_id="plan-1",
    )

    assert report["status"] == expected_status
    assert report["screenshot_count"] == report["model_image_count"] == 1
    assert report["capability_count_before"] == 1
    assert report["capability_count_after"] == 2
    assert report["field_count_before"] == 1
    assert report["field_count_after"] == 2
    assert report["operation_id"] == "plan-1"
    assert len(report["field_changes"]) == int(changed)
    assert len(report["change_details"]) == int(changed)


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


def test_recording_resume_state_is_scoped_reused_and_bounded(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(gateway, "_MAX_RECORDING_RESUME_STATES", 2)
    gateway._RECORDING_RESUME_STATES.clear()
    first_key = ("tenant-a", "system-a", f"recording_{'1' * 32}")
    second_key = ("tenant-a", "system-a", f"recording_{'2' * 32}")
    third_key = ("tenant-b", "system-a", f"recording_{'3' * 32}")

    first = gateway._recording_resume_state(first_key)
    first["flow_spec"] = "authoritative"
    assert gateway._recording_resume_state(first_key)["flow_spec"] == "authoritative"
    gateway._recording_resume_state(second_key)
    # Touching first makes second the least recently used entry.
    gateway._recording_resume_state(first_key)
    gateway._recording_resume_state(third_key)

    assert first_key in gateway._RECORDING_RESUME_STATES
    assert second_key not in gateway._RECORDING_RESUME_STATES
    assert third_key in gateway._RECORDING_RESUME_STATES


def test_recording_storage_cache_does_not_replace_authenticated_state_with_login_page() -> None:
    state: dict = {}
    authenticated = {
        "cookies": [{"name": "sid", "value": "secret"}],
        "origins": [{
            "origin": "https://oa.example.test",
            "localStorage": [
                {"name": "ACCESS_TOKEN", "value": "secret"},
                {"name": "tenantId", "value": "1"},
            ],
        }],
    }
    login_page = {
        "cookies": [],
        "origins": [{
            "origin": "https://oa.example.test",
            "localStorage": [{"name": "tenantId", "value": "1"}],
        }],
    }

    gateway._remember_recording_storage(state, authenticated)
    gateway._remember_recording_storage(state, login_page)

    assert state["storage_state"] == authenticated

@pytest.mark.asyncio
async def test_recording_connection_lease_waits_for_previous_owner_cleanup() -> None:
    claim = getattr(gateway, "_claim_recording_connection", None)
    release = getattr(gateway, "_release_recording_connection", None)
    assert claim is not None, "recording connection handoff is not implemented"
    assert release is not None, "recording connection release is not implemented"

    key = ("tenant-a", "A-OA", f"recording_{'b' * 32}")
    gateway._ACTIVE_RECORDING_CONNECTIONS.clear()
    old_started = asyncio.Event()
    old_cleaned = asyncio.Event()
    release_old = asyncio.Event()

    async def old_handler() -> None:
        lease = await claim(key)
        old_started.set()
        try:
            await release_old.wait()
        finally:
            old_cleaned.set()
            release(key, lease)

    old_task = asyncio.create_task(old_handler())
    await old_started.wait()

    replacement_task = asyncio.create_task(claim(key))
    await asyncio.sleep(0)

    assert not replacement_task.done()
    assert not old_task.cancelled()
    release_old.set()
    await old_task
    replacement = await replacement_task

    assert old_cleaned.is_set()
    assert gateway._ACTIVE_RECORDING_CONNECTIONS[key] is replacement
    release(key, replacement)
    assert key not in gateway._ACTIVE_RECORDING_CONNECTIONS


@pytest.mark.asyncio
async def test_cancelled_connection_waiter_never_replaces_or_cancels_owner() -> None:
    key = ("tenant-a", "A-OA", f"recording_{'d' * 32}")
    gateway._ACTIVE_RECORDING_CONNECTIONS.clear()
    current_task = asyncio.current_task()
    assert current_task is not None
    original = gateway._RecordingConnectionLease(
        task=current_task, released=asyncio.Event(),
    )
    gateway._ACTIVE_RECORDING_CONNECTIONS[key] = original

    second_task = asyncio.create_task(gateway._claim_recording_connection(key))
    await asyncio.sleep(0)
    assert gateway._ACTIVE_RECORDING_CONNECTIONS[key] is original

    second_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second_task
    assert gateway._ACTIVE_RECORDING_CONNECTIONS[key] is original

    gateway._release_recording_connection(key, original)
    assert key not in gateway._ACTIVE_RECORDING_CONNECTIONS


@pytest.mark.asyncio
async def test_recording_connection_lease_old_release_cannot_remove_replacement() -> None:
    claim = getattr(gateway, "_claim_recording_connection", None)
    release = getattr(gateway, "_release_recording_connection", None)
    assert claim is not None, "recording connection handoff is not implemented"
    assert release is not None, "recording connection release is not implemented"

    key = ("tenant-a", "A-OA", f"recording_{'c' * 32}")
    gateway._ACTIVE_RECORDING_CONNECTIONS.clear()
    current_task = asyncio.current_task()
    assert current_task is not None
    old = gateway._RecordingConnectionLease(task=current_task, released=asyncio.Event())
    replacement = gateway._RecordingConnectionLease(task=current_task, released=asyncio.Event())
    gateway._ACTIVE_RECORDING_CONNECTIONS[key] = replacement

    release(key, old)

    assert old.released.is_set()
    assert gateway._ACTIVE_RECORDING_CONNECTIONS[key] is replacement
    release(key, replacement)


@pytest.mark.asyncio
async def test_recording_pi_candidate_failed_start_is_closed_and_not_reused() -> None:
    start_candidate = getattr(gateway, "_start_recording_pi_candidate", None)
    assert start_candidate is not None, "transactional Pi startup is not implemented"

    class Candidate:
        def __init__(self, error: Exception | None = None) -> None:
            self.error = error
            self.started = 0
            self.closed = 0

        async def start(self):  # noqa: ANN201
            self.started += 1
            if self.error is not None:
                raise self.error
            return self

        async def close(self) -> None:
            self.closed += 1

    failed = Candidate(RuntimeError("scope busy"))
    healthy = Candidate()

    with pytest.raises(RuntimeError, match="scope busy"):
        await start_candidate(lambda: failed)
    result = await start_candidate(lambda: healthy)

    assert failed.started == 1
    assert failed.closed == 1
    assert healthy.started == 1
    assert healthy.closed == 0
    assert result is healthy


def test_recording_storage_cache_accepts_richer_checkpoint() -> None:
    state = {
        "storage_state": {
            "cookies": [],
            "origins": [{"origin": "https://oa.example.test", "localStorage": []}],
        },
    }
    authenticated = {
        "cookies": [{"name": "sid", "value": "secret"}],
        "origins": [{
            "origin": "https://oa.example.test",
            "localStorage": [{"name": "ACCESS_TOKEN", "value": "secret"}],
        }],
    }

    gateway._remember_recording_storage(state, authenticated)

    assert state["storage_state"] == authenticated


class _FakeWebSocket(_ConcurrentWriteProbe):
    def __init__(self, incoming: list[dict]) -> None:
        super().__init__()
        self.incoming = list(incoming)
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None
        self.close_reason = ""

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict:
        if not self.incoming:
            raise gateway.WebSocketDisconnect(code=1000)
        return self.incoming.pop(0)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason


@pytest.mark.asyncio
async def test_record_ws_started_action_is_unique_and_input_errors_are_recoverable(monkeypatch) -> None:  # noqa: ANN001
    import dano.execution.page.recorder as recorder_module

    sessions = []

    class FakeRecordSession:
        def __init__(self, on_request, **_kwargs) -> None:  # noqa: ANN001
            self.on_request = on_request
            self.events: list[dict] = []
            self.stopped = False
            self.paused = False
            sessions.append(self)

        async def start(self, *_args, **_kwargs) -> None:
            return None

        async def start_screencast(self, on_frame) -> None:  # noqa: ANN001
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

        def pause_recording(self) -> None:
            self.paused = True

        async def storage_state(self) -> dict:
            return {}

        async def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(recorder_module, "RecordSession", FakeRecordSession)
    gateway._RECENT_RECORDING_ACTIONS.clear()

    resume_id = f"recording_{'a' * 32}"

    def incoming(resume_action: str = "") -> list[dict]:
        return [
            {"type": "start", "start_url": "https://example.test", "tenant": "tenant-a",
             "pi_recording_id": resume_id, **({"resume_action": resume_action} if resume_action else {})},
            {"type": "input", "event": {"kind": "pointer_move", "nx": 0.1, "ny": 0.2}},
            {"type": "input", "event": {"kind": "dblclick", "nx": 0.3, "ny": 0.4}},
            {"type": "input", "event": {"kind": "pointer_up", "nx": 0.5, "ny": 0.6}},
            {"type": "ping", "at": 123456},
            {"type": "stop"},
            {"type": "ping", "at": 654321},
        ]

    first_ws = _FakeWebSocket(incoming())
    second_ws = _FakeWebSocket(incoming())
    await gateway.record_ws(first_ws)
    await gateway.record_ws(second_ws)

    first_started = next(message for message in first_ws.messages if message["type"] == "started")
    second_started = next(message for message in second_ws.messages if message["type"] == "started")
    resumed_ws = _FakeWebSocket(incoming(first_started["action"]))
    await gateway.record_ws(resumed_ws)
    resumed_started = next(message for message in resumed_ws.messages if message["type"] == "started")
    assert re.fullmatch(r"action_[0-9a-f]{32}", first_started["action"])
    assert first_started["action"] != second_started["action"]
    assert resumed_started["action"] == first_started["action"]
    assert first_started["pi_recording_id"] == resume_id
    assert second_started["pi_recording_id"] == resume_id

    errors = [message for message in first_ws.messages if message["type"] == "input_error"]
    assert [error["kind"] for error in errors] == ["pointer_move", "dblclick"]
    assert errors[0]["event"] == {"kind": "pointer_move", "nx": 0.1, "ny": 0.2}
    assert errors[0]["error_type"] == "TargetClosedError"
    assert errors[1]["detail"] == "transient input failure"
    assert all(error["recoverable"] for error in errors)
    assert sessions[0].events[-1]["kind"] == "pointer_up"
    assert {"type": "pong", "at": 123456} in first_ws.messages
    assert {"type": "stopped", "connection_retained": True} in first_ws.messages
    assert {"type": "stopped", "connection_retained": True} in second_ws.messages
    assert {"type": "pong", "at": 654321} in first_ws.messages
    assert all(session.paused for session in sessions)
    assert all(session.stopped for session in sessions)
    assert first_ws.accepted and first_ws.closed
    assert first_ws.close_code == 1000
    assert first_ws.max_active_writes == 1


def test_recording_gateway_has_one_pi_path_and_no_direct_llm_fallback() -> None:
    from dano.onboarding.page_onboard import run_request_onboarding

    source = inspect.getsource(gateway.record_ws)
    assert source.count("RecordingPiSession(") == 1
    assert "_page_semantic_client" not in source
    assert "OpenAICompatClient" not in source
    assert "run_recording_pi_loop" not in source
    assert "begin_llm_budget" not in source
    assert "submit_recording_review" in source
    assert "run_id=pi_session.run_id" in source
    assert "recording_pi_required=True" in source
    assert "run_id" in inspect.signature(run_request_onboarding).parameters
    assert "recording_pi_required" in inspect.signature(run_request_onboarding).parameters
    assert "未切换" not in source  # errors are surfaced; no hidden alternate model branch


def test_recording_gateway_builds_enum_evidence_once_per_finalize() -> None:
    source = inspect.getsource(gateway.record_ws)

    assert source.count("recorded_page_enum_options()") == 1
    assert "recorded_page_options = sess.recorded_page_enum_options()" in source
    assert "_project_recorded_page_enum_options(" in source


@pytest.mark.asyncio
async def test_recording_operation_keepalive_sends_progress_until_completion() -> None:
    class Sender:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        async def send_json(self, message: dict) -> None:
            self.messages.append(message)

    sender = Sender()
    async with gateway._recording_operation_keepalive(
        sender, operation="plan", operation_id="plan-1", interval=0.01,
    ):
        await asyncio.sleep(0.035)

    sent = len(sender.messages)
    assert sent >= 2
    assert all(message["type"] == "operation_progress" for message in sender.messages)
    assert all(message["operation_id"] == "plan-1" for message in sender.messages)
    await asyncio.sleep(0.02)
    assert len(sender.messages) == sent


@pytest.mark.asyncio
async def test_recording_operation_keepalive_does_not_cancel_live_work_on_disconnect() -> None:
    class DisconnectedSender:
        async def send_json(self, _message: dict) -> None:
            raise gateway.WebSocketDisconnect(code=1006)

    completed = False
    async with gateway._recording_operation_keepalive(
        DisconnectedSender(), operation="plan", operation_id="plan-disconnected",
        interval=0.01,
    ):
        await asyncio.sleep(0.03)
        completed = True

    assert completed is True


@pytest.mark.asyncio
async def test_long_operation_drains_page_input_without_cancelling_on_disconnect() -> None:
    incoming: asyncio.Queue = asyncio.Queue()
    release_operation = asyncio.Event()
    all_inputs_handled = asyncio.Event()
    handled: list[int] = []

    async def operation() -> str:
        await release_operation.wait()
        return "completed"

    async def handle_live(message: dict) -> bool:
        if message.get("type") != "input":
            return False
        handled.append(int(message["index"]))
        if len(handled) == 40:
            all_inputs_handled.set()
        return True

    waiting = asyncio.create_task(
        gateway._await_operation_while_draining_recording_input(
            operation(), incoming, handle_live,
        )
    )
    for index in range(40):
        await incoming.put({"type": "input", "index": index})
    await incoming.put({"type": "flow_update", "operation_id": "edit-1"})
    await incoming.put(gateway.WebSocketDisconnect(code=1006))

    await asyncio.wait_for(all_inputs_handled.wait(), timeout=0.5)
    assert handled == list(range(40))
    for _ in range(20):
        if incoming.empty():
            break
        await asyncio.sleep(0)
    assert incoming.empty()
    assert not waiting.done()

    release_operation.set()
    result, deferred = await asyncio.wait_for(waiting, timeout=0.5)

    assert result == "completed"
    assert deferred[0] == {"type": "flow_update", "operation_id": "edit-1"}
    assert isinstance(deferred[1], gateway.WebSocketDisconnect)


@pytest.mark.asyncio
async def test_long_operation_preserves_deferred_messages_when_model_fails() -> None:
    incoming: asyncio.Queue = asyncio.Queue()
    release_operation = asyncio.Event()
    deferred: list[object] = []

    async def operation() -> None:
        await release_operation.wait()
        raise RuntimeError("model failed")

    waiting = asyncio.create_task(
        gateway._await_operation_while_draining_recording_input(
            operation(), incoming, lambda _message: asyncio.sleep(0, result=False), deferred,
        )
    )
    update = {"type": "flow_update", "operation_id": "edit-1"}
    await incoming.put(update)
    for _ in range(20):
        if incoming.empty():
            break
        await asyncio.sleep(0)
    release_operation.set()

    with pytest.raises(RuntimeError, match="model failed"):
        await waiting
    assert deferred == [update]


@pytest.mark.asyncio
async def test_deferred_message_is_an_ordering_barrier_for_later_page_input() -> None:
    incoming: asyncio.Queue = asyncio.Queue()
    release_operation = asyncio.Event()
    handled: list[dict] = []

    async def operation() -> str:
        await release_operation.wait()
        return "completed"

    async def handle_live(message: dict) -> bool:
        handled.append(message)
        return message.get("type") == "input"

    waiting = asyncio.create_task(
        gateway._await_operation_while_draining_recording_input(
            operation(), incoming, handle_live,
        )
    )
    reset = {"type": "reset"}
    later_input = {"type": "input", "event": {"kind": "click"}}
    await incoming.put(reset)
    await incoming.put(later_input)
    for _ in range(20):
        if incoming.empty():
            break
        await asyncio.sleep(0)
    release_operation.set()

    result, deferred = await waiting

    assert result == "completed"
    assert handled == [reset]
    assert deferred == [reset, later_input]


@pytest.mark.asyncio
async def test_websocket_send_queue_normalizes_write_failure_as_disconnect() -> None:
    class DisconnectedSocket:
        async def send_json(self, _message: dict) -> None:
            raise RuntimeError("transport closed")

    sender = gateway._WebSocketSendQueue(DisconnectedSocket())
    with pytest.raises(gateway.WebSocketDisconnect):
        await sender.send_json({"type": "operation_progress"})
    await sender.close()


@pytest.mark.asyncio
async def test_reconnect_waits_without_cancelling_previous_transport_owner() -> None:
    key = ("tenant", "subsystem", "recording_test")
    ready = asyncio.Event()
    finish = asyncio.Event()

    async def owner() -> None:
        lease = await gateway._claim_recording_connection(key)
        ready.set()
        try:
            await finish.wait()
        finally:
            gateway._release_recording_connection(key, lease)

    previous = asyncio.create_task(owner())
    await ready.wait()

    replacement_task = asyncio.create_task(gateway._claim_recording_connection(key))
    await asyncio.sleep(0)
    assert not replacement_task.done()
    assert not previous.cancelled()
    finish.set()
    await previous
    replacement = await asyncio.wait_for(replacement_task, timeout=0.5)
    gateway._release_recording_connection(key, replacement)


def test_finalize_projection_preserves_recorded_enum_fact_metadata() -> None:
    raw = {
        "requestType": {
            "field_key": "requestType",
            "field_aliases": ["申请类型"],
            "options": [
                {"label": "病假", "value": "2"},
                {"label": "事假", "value": "3"},
            ],
            "selected": "病假",
            "selected_label": "病假",
            "selected_value": "2",
            "mapping_complete": False,
            "mapping_conflict": True,
            "truncated": True,
            "action_id": "action-select-request-type",
            "transaction_id": "page-1|main|action-select-request-type",
            "observed_at": 1784563200000,
        },
    }

    projected = gateway._project_recorded_page_enum_options(raw, samples={})
    fact = projected["requestType"]

    assert fact["selected_label"] == "病假"
    assert fact["selected_value"] == "2"
    assert fact["mapping_complete"] is False
    assert fact["mapping_conflict"] is True
    assert fact["truncated"] is True
    assert fact["action_id"] == "action-select-request-type"
    assert fact["transaction_id"] == "page-1|main|action-select-request-type"
    assert fact["observed_at"] == 1784563200000


def test_analysis_report_exposes_initial_kind_and_actionable_issue_details() -> None:
    before = FlowSpec(
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            path="/api/submit",
            params=[
                ParamField(path="reason", key="原因", value="leave"),
                ParamField(path="days", key="天数", value="2", locked=True),
            ],
        )],
    )
    after = before.model_copy(deep=True)
    after.meta = {
        "capability_generation": {"initial_completed": True, "last_mode": "initial"},
        "capability_model": {
            "semantic_coverage": {"complete": False},
            "semantic_plan": {
                "field_semantics": [{
                    "step_id": "submit",
                    "wire_path": "reason",
                    "axis_status": {"name": "locked"},
                    "evidence": [{"source": "screenshot", "axis": "name"}],
                }],
                "unresolved_items": [{
                    "kind": "field_axis",
                    "step_id": "submit",
                    "path": "days",
                    "axis": "required",
                    "reason": "required marker not visible",
                }, {
                    "kind": "field_axis", "step_id": "submit", "path": "days",
                    "axis": "required", "reason": "required marker not visible",
                }, {
                    "kind": "capability_relation", "relation_id": "rel-1",
                    "status": "rejected", "reason": "conflict with recorded order",
                }],
            },
        },
    }

    report = gateway._analysis_application_report(
        before=before,
        after=after,
        operation_report={
            "changed": True,
            "summary": "initial analysis",
            "changes": {"fields": 1},
            "field_changes": [],
            "proposal_gate": {"accepted": True},
        },
        screenshots=[{"name": "form.png"}],
        delivered_image_count=1,
        operation_id="initial-1",
    )

    assert report["analysis_kind"] == "initial"
    assert report["unmatched_fields"] == []
    assert report["unmatched_field_count"] == 0
    assert report["unresolved_items"][0]["axis"] == "required"
    assert report["unresolved_field_count"] == len(report["unresolved_items"])
    assert report["locked_field_count"] == 2
    assert len(report["locked_items"]) == 2
    assert report["rejected_field_count"] == 1
    assert len(report["rejected_items"]) == 1
    assert "field" in report["issue_groups"]


def test_analysis_report_only_requires_review_for_real_unapplied_work() -> None:
    before = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/submit",
        params=[ParamField(path="reason", key="原因")],
    )])

    def report_for(issue: dict, *, changed: bool = False) -> dict:
        after = before.model_copy(deep=True)
        after.meta = {"capability_model": {"semantic_plan": {
            "field_semantics": [], "unresolved_items": [issue],
        }}}
        return gateway._analysis_application_report(
            before=before, after=after,
            operation_report={
                "changed": changed, "changes": {}, "field_changes": [],
                "proposal_gate": {"accepted": True},
            },
            screenshots=[{"name": "form.png"}], delivered_image_count=1,
            operation_id="review-status",
        )

    advisory = report_for({
        "kind": "field", "step_id": "submit", "path": "reason",
        "reason": "control is outside the supplied screenshot",
    })
    assert advisory["status"] == "no_change"

    unmatched = report_for({
        "kind": "unmatched_field", "status": "unmatched", "blocking": False,
        "reason": "visible control has no unique recorded field match",
    })
    assert unmatched["status"] == "needs_review"
    assert unmatched["unmatched_field_count"] == 1

    blocking = report_for({
        "kind": "field", "step_id": "submit", "path": "reason",
        "blocking": True, "reason": "contradicts recorded API facts",
    })
    assert blocking["status"] == "needs_review"


def test_normalized_unmatched_screenshot_field_reaches_application_report() -> None:
    before = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/submit",
        params=[ParamField(path="reason", key="原因")],
    )])
    normalized = agent_tools_module._normalize_recording_plan_submission({
        "_analysis_screenshot_count": 1,
        "semantic_plan": {
            "business_understanding": {"summary": "提交"},
            "request_roles": [],
            "field_semantics": [{
                "public_name": "截图中的未知字段", "business_type": "string",
                "category": "user_param", "source_kind": "user_input",
                "confidence": 0.95,
                "evidence": [{
                    "source": "screenshot", "visible_label": "截图中的未知字段",
                    "control_kind": "text", "editable": True,
                }],
            }],
            "capabilities": [], "capability_relations": [], "unresolved_items": [],
        },
        "ops": [],
    }, before)
    after = before.model_copy(deep=True)
    after.meta = {"capability_model": {"semantic_plan": normalized["semantic_plan"]}}

    report = gateway._analysis_application_report(
        before=before, after=after,
        operation_report={
            "changed": True, "changes": {"flow": 1}, "field_changes": [],
            "proposal_gate": {"accepted": True},
        },
        screenshots=[{"name": "form.png"}], delivered_image_count=1,
        operation_id="plan-unmatched",
    )

    assert report["status"] == "needs_review"
    assert report["unmatched_field_count"] == 1
    assert report["unmatched_fields"][0]["kind"] == "unmatched_field"


def test_analysis_without_screenshots_does_not_report_field_matching_gaps() -> None:
    before = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/submit",
        params=[ParamField(path="useInfo", key="使用描述")],
    )])
    after = before.model_copy(deep=True)
    after.meta = {
        "capability_generation": {"initial_completed": True, "last_mode": "initial"},
        "capability_model": {
            "semantic_plan": {"field_semantics": [], "unresolved_items": []},
        },
    }

    report = gateway._analysis_application_report(
        before=before,
        after=after,
        operation_report={
            "changed": True, "summary": "initial analysis",
            "changes": {"fields": 1}, "field_changes": [],
            "proposal_gate": {"accepted": True},
        },
        screenshots=[], delivered_image_count=0, operation_id="initial-plain",
    )

    assert report["analysis_kind"] == "initial"
    assert report["matched_field_count"] == 0
    assert report["unmatched_field_count"] == 0
    assert report["unmatched_fields"] == []


def test_analysis_kind_uses_completed_operation_history_not_generation_metadata() -> None:
    before = FlowSpec(steps=[])
    after = before.model_copy(deep=True)
    after.meta = {"capability_generation": {"last_mode": "optimize"}}

    first = gateway._analysis_application_report(
        before=before, after=after,
        operation_report={"changed": False, "changes": {}, "field_changes": []},
        screenshots=[], delivered_image_count=0, operation_id="first",
    )
    assert first["analysis_kind"] == "initial"

    before.meta = {"last_analysis_application": first}
    second = gateway._analysis_application_report(
        before=before, after=after,
        operation_report={"changed": False, "changes": {}, "field_changes": []},
        screenshots=[], delivered_image_count=0, operation_id="second",
    )
    assert second["analysis_kind"] == "incremental"


def test_analysis_report_ignores_malformed_axis_status_instead_of_failing_operation() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/submit",
        params=[ParamField(path="reason", key="原因")],
    )])
    spec.meta = {
        "capability_model": {"semantic_plan": {
            "field_semantics": [{
                "step_id": "submit", "wire_path": "reason",
                "axis_status": ["name", "type"],
            }],
            "unresolved_items": [],
        }},
    }

    report = gateway._analysis_application_report(
        before=spec, after=spec,
        operation_report={"changed": False, "changes": {}, "field_changes": []},
        screenshots=[], delivered_image_count=0, operation_id="plan-malformed-axis",
    )

    assert report["status"] == "no_change"
    assert report["locked_field_count"] == 0
