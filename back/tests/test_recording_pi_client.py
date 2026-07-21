from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from dano.agent_tools import materials, runs
from dano.execution.page.flow_spec import FlowSpec, FlowStep, ParamField
from dano.onboarding import recording_pi


RECORDING_ONE = f"recording_{'1' * 32}"
RECORDING_TWO = f"recording_{'2' * 32}"
RECORDING_THREE = f"recording_{'3' * 32}"
RECORDING_SAFE = f"recording_{'4' * 32}"


class _FakeServer:
    should_exit = False


class _FakeServerTask:
    def done(self) -> bool:
        return False

    def __await__(self):
        async def completed() -> None:
            return None

        return completed().__await__()


class _FakeStdin:
    def __init__(self, proc: "_FakeProcess") -> None:
        self.proc = proc
        self.commands: list[dict] = []

    def write(self, raw: bytes) -> None:
        command = json.loads(raw.decode())
        self.commands.append(command)
        request_id = command["request_id"]
        if command["type"] == "start_session":
            event = {
                "type": "session_started", "request_id": request_id,
                "session_id": "pi-session-one", "session_file": self.proc.session_file,
            }
        elif command["type"] == "prompt":
            event = {
                "type": "prompt_completed", "request_id": request_id,
                "session_id": "pi-session-one", "session_file": self.proc.session_file,
                "status": "completed", "final_text": "done",
            }
        elif command["type"] == "cancel":
            event = {"type": "agent_event", "event": "cancelled", "request_id": request_id}
        else:
            event = {
                "type": "session_closed", "request_id": request_id,
                "session_id": "pi-session-one", "session_file": self.proc.session_file,
            }
            self.proc.returncode = 0
        self.proc.stdout.feed_data((json.dumps(event) + "\n").encode())

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.proc.stdout.feed_eof()


class _FakeProcess:
    def __init__(self, session_file: str) -> None:
        self.session_file = session_file
        self.returncode = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.stdin = _FakeStdin(self)

    async def wait(self) -> int:
        await asyncio.sleep(0)
        return int(self.returncode or 0)

    def kill(self) -> None:
        self.returncode = -9
        self.stdout.feed_eof()


@pytest.mark.asyncio
async def test_recording_pi_session_reuses_one_process_and_one_session(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    server = _FakeServer()
    server_task = _FakeServerTask()
    process = _FakeProcess(str(tmp_path / "session.jsonl"))
    spawns: list[tuple] = []

    async def fake_tool_server():
        return server, server_task, 54321

    async def fake_spawn(*args, **kwargs):  # noqa: ANN002, ANN003
        spawns.append((args, kwargs))
        return process

    monkeypatch.setattr(recording_pi, "_start_tool_server", fake_tool_server)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(
        "dano.config.get_settings",
        lambda: SimpleNamespace(pi_api_key="key", pi_base_url="https://model.test", pi_model="model", pi_provider="provider"),
    )

    client = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_ONE, session_root=tmp_path,
    )
    await client.start()
    client.bind_analysis_images([
        {"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"},
    ])
    first = await client.prompt("执行规划")
    second = await client.prompt("执行修复")

    assert first["session_id"] == second["session_id"] == client.session_id == "pi-session-one"
    assert len(spawns) == 1
    assert [command["type"] for command in process.stdin.commands] == [
        "start_session", "prompt", "prompt",
    ]
    assert process.stdin.commands[1]["images"] == [
        {"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"},
    ]
    assert process.stdin.commands[2]["images"] == []
    assert runs.is_valid(client.run_id, client.token)
    assert materials.get(client.run_id, "A-OA") is not None

    await client.close()
    assert process.stdin.commands[-1]["type"] == "close"
    assert not runs.is_valid(client.run_id, client.token)
    assert materials.get(client.run_id, "A-OA") is None
    assert server.should_exit


@pytest.mark.asyncio
async def test_analysis_image_count_remains_visible_during_prompt() -> None:
    client = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_ONE,
    )
    client._proc = object()
    observed_counts: list[int] = []

    async def fake_command(command_type: str, **payload):  # noqa: ANN003, ANN202
        assert command_type == "prompt"
        observed_counts.append(client.analysis_image_count)
        return {"image_count": len(payload.get("images") or [])}

    client._command = fake_command
    client.bind_analysis_images([
        {"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"},
    ])

    result = await client.prompt("执行截图规划")

    assert result["image_count"] == 1
    assert observed_counts == [1]
    assert client.analysis_image_count == 0


@pytest.mark.asyncio
async def test_recording_pi_runtime_error_has_no_fallback(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    process = _FakeProcess(str(tmp_path / "session.jsonl"))
    original_write = process.stdin.write

    def fail_prompt(raw: bytes) -> None:
        command = json.loads(raw.decode())
        if command["type"] != "prompt":
            original_write(raw)
            return
        process.stdin.commands.append(command)
        process.stdout.feed_data((json.dumps({
            "type": "runtime_error", "request_id": command["request_id"],
            "command": "prompt", "error": "provider unavailable",
        }) + "\n").encode())

    process.stdin.write = fail_prompt

    async def fake_tool_server():
        return _FakeServer(), _FakeServerTask(), 54321

    monkeypatch.setattr(recording_pi, "_start_tool_server", fake_tool_server)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *_a, **_kw: _await(process))
    monkeypatch.setattr(
        "dano.config.get_settings",
        lambda: SimpleNamespace(pi_api_key="key", pi_base_url="", pi_model="model", pi_provider="provider"),
    )

    client = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_TWO, session_root=tmp_path,
    )
    await client.start()
    with pytest.raises(recording_pi.RecordingPiError, match="provider unavailable"):
        await client.prompt("执行规划")
    assert [command["type"] for command in process.stdin.commands].count("prompt") == 1
    await client.close()


@pytest.mark.asyncio
async def test_recording_pi_logs_provider_error_agent_events(monkeypatch) -> None:  # noqa: ANN001
    stdout = asyncio.StreamReader()
    client = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_TWO,
    )
    client._proc = SimpleNamespace(stdout=stdout)
    errors: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        recording_pi.log,
        "error",
        lambda event, **fields: errors.append((event, fields)),
    )

    task = asyncio.create_task(client._read_stdout())
    stdout.feed_data((json.dumps({
        "type": "agent_event",
        "event": "message_end",
        "request_id": "request-one",
        "stop_reason": "error",
        "error": "Stream ended without finish_reason",
    }) + "\n").encode())
    await asyncio.sleep(0)
    stdout.feed_eof()
    await task

    assert errors == [("recording_pi.agent_error", {
        "run_id": client.run_id,
        "agent_event": "message_end",
        "error": "Stream ended without finish_reason",
    })]
    client._proc = None


@pytest.mark.asyncio
async def test_recording_pi_submission_limit_is_exposed_as_hard_failure(monkeypatch) -> None:  # noqa: ANN001
    client = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_TWO,
    )
    client._proc = object()

    async def limited_command(command_type, **_kwargs):  # noqa: ANN001
        assert command_type == "prompt"
        return {
            "type": "prompt_completed",
            "status": "submission_limit",
            "error": "recording submission attempt limit exceeded",
        }

    monkeypatch.setattr(client, "_command", limited_command)
    with pytest.raises(recording_pi.RecordingPiError, match="无效 Token 消耗"):
        await client.prompt("执行规划")
    client._proc = None


@pytest.mark.asyncio
async def test_recording_pi_accepted_submission_wins_over_late_limit(monkeypatch) -> None:  # noqa: ANN001
    client = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_TWO,
    )
    client._proc = object()

    async def accepted_command(command_type, **_kwargs):  # noqa: ANN001
        assert command_type == "prompt"
        return {
            "type": "prompt_completed",
            "status": "submission_limit",
            "error": "late duplicate",
            "accepted_submission": "submit_recording_review",
        }

    monkeypatch.setattr(client, "_command", accepted_command)
    result = await client.prompt("执行发布审核")
    assert result["status"] == "submitted"
    assert result["accepted_submission"] == "submit_recording_review"
    assert "error" not in result
    client._proc = None


async def _await(value):  # noqa: ANN001, ANN201
    return value


@pytest.mark.asyncio
async def test_recording_pi_session_file_survives_close_and_is_used_for_resume(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    scope = hashlib.sha256(f"tenant-a\0A-OA\0{RECORDING_THREE}".encode()).hexdigest()[:32]
    session_file = tmp_path / scope / "session.jsonl"
    session_file.parent.mkdir()
    processes = [_FakeProcess(str(session_file)), _FakeProcess(str(session_file))]

    async def fake_tool_server():
        return _FakeServer(), _FakeServerTask(), 54321

    async def fake_spawn(*_args, **_kwargs):
        return processes.pop(0)

    monkeypatch.setattr(recording_pi, "_start_tool_server", fake_tool_server)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(
        "dano.config.get_settings",
        lambda: SimpleNamespace(pi_api_key="key", pi_base_url="", pi_model="model", pi_provider="provider"),
    )

    first = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_THREE,
        session_root=tmp_path,
    )
    await first.start()
    # The real Pi SessionManager creates this JSONL during start/prompt.
    session_file.write_text("persisted", encoding="utf-8")
    persisted = first.session_file
    first_process = first._proc
    await first.close()

    assert session_file.exists()
    assert persisted == str(session_file)
    assert "session_file" not in first.descriptor

    resumed = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_THREE,
        session_root=tmp_path,
    )
    await resumed.start()
    second_process = resumed._proc
    assert second_process is not first_process
    assert second_process.stdin.commands[0]["type"] == "start_session"
    assert second_process.stdin.commands[0]["session_file"] == str(session_file)
    await resumed.close()


@pytest.mark.asyncio
async def test_recording_pi_discovers_tenant_scoped_session_without_client_path(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    scope = hashlib.sha256(f"tenant-a\0A-OA\0{RECORDING_SAFE}".encode()).hexdigest()[:32]
    session_file = tmp_path / scope / "persisted.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text("persisted", encoding="utf-8")
    process = _FakeProcess(str(session_file))

    async def fake_tool_server():
        return _FakeServer(), _FakeServerTask(), 54321

    monkeypatch.setattr(recording_pi, "_start_tool_server", fake_tool_server)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *_a, **_kw: _await(process))
    monkeypatch.setattr(
        "dano.config.get_settings",
        lambda: SimpleNamespace(pi_api_key="key", pi_base_url="", pi_model="model", pi_provider="provider"),
    )

    client = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_SAFE, session_root=tmp_path,
    )
    await client.start()
    assert process.stdin.commands[0]["session_file"] == str(session_file)
    assert client.descriptor == {
        "recording_id": RECORDING_SAFE,
        "session_id": "pi-session-one",
        "resumed": True,
    }
    await client.close()


def test_recording_pi_rejects_non_opaque_ids_and_has_no_session_path_argument() -> None:
    import inspect

    with pytest.raises(ValueError, match="opaque recording token"):
        recording_pi.RecordingPiSession(
            tenant="tenant-a",
            subsystem="A-OA",
            recording_id="../../attacker/session.jsonl",
        )
    assert "session_file" not in inspect.signature(recording_pi.RecordingPiSession).parameters


def test_recording_pi_scope_file_lock_excludes_other_gateway_processes(tmp_path) -> None:  # noqa: ANN001
    lock_path = tmp_path / ".pi-session.lock"
    first = recording_pi._acquire_scope_file_lock(lock_path)
    try:
        with pytest.raises(recording_pi.RecordingPiError, match="另一个网关进程"):
            recording_pi._acquire_scope_file_lock(lock_path)
    finally:
        recording_pi._release_scope_file_lock(first)

    second = recording_pi._acquire_scope_file_lock(lock_path)
    recording_pi._release_scope_file_lock(second)


@pytest.mark.asyncio
async def test_recording_pi_prevents_concurrent_open_of_same_persisted_scope(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    scope = hashlib.sha256(f"tenant-a\0A-OA\0{RECORDING_ONE}".encode()).hexdigest()[:32]
    process = _FakeProcess(str(tmp_path / scope / "session.jsonl"))

    async def fake_tool_server():
        return _FakeServer(), _FakeServerTask(), 54321

    monkeypatch.setattr(recording_pi, "_start_tool_server", fake_tool_server)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *_a, **_kw: _await(process))
    monkeypatch.setattr(
        "dano.config.get_settings",
        lambda: SimpleNamespace(pi_api_key="key", pi_base_url="", pi_model="model", pi_provider="provider"),
    )

    first = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_ONE, session_root=tmp_path,
    )
    second = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_ONE, session_root=tmp_path,
    )
    await first.start()
    with pytest.raises(recording_pi.RecordingPiError, match="另一个连接"):
        await second.start()
    await second.close()
    await first.close()


class _ReviewSpec:
    def __init__(self, version: int, fingerprint: str) -> None:
        self.meta = {"current_version": version}
        self.fingerprint = fingerprint

    def model_copy(self, *, deep: bool):  # noqa: ARG002, ANN201
        return _ReviewSpec(self.meta["current_version"], self.fingerprint)


@pytest.mark.asyncio
async def test_submit_review_is_first_write_wins_and_exact_replay_is_idempotent() -> None:
    client = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_TWO,
    )
    client.flow_spec = _ReviewSpec(7, "release-fingerprint")
    first_review = {
        "base_flow_version": 7,
        "all_passed": True,
        "verdicts": [
            {"role": role, "passed": True, "reasons": []}
            for role in ("acceptance", "security", "compliance")
        ],
    }
    first = await client.submit_review(first_review, base_flow_version=7)
    replay = await client.submit_review(dict(first_review), base_flow_version=7)
    assert first["replayed"] is False
    assert replay["replayed"] is True

    changed = dict(first_review)
    changed["all_passed"] = False
    with pytest.raises(recording_pi.RecordingPiError, match="拒绝被后续结论覆盖"):
        await client.submit_review(changed, base_flow_version=7)
    assert client.last_review == first_review


def test_require_publish_review_hard_fails_missing_stale_and_rejected(monkeypatch) -> None:  # noqa: ANN001
    from dano.execution.page import flow_spec

    monkeypatch.setattr(flow_spec, "flow_spec_fingerprint", lambda spec: spec.fingerprint)
    client = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_TWO,
    )
    client.flow_spec = _ReviewSpec(7, "release-fingerprint")

    with pytest.raises(recording_pi.RecordingPiError, match="未通过 submit_recording_review"):
        client.require_publish_review(flow_version=7, flow_fingerprint="release-fingerprint")

    client.last_submission_kind = "review"
    client.last_review = {
        "base_flow_version": 6,
        "all_passed": True,
        "verdicts": [
            {"role": role, "passed": True, "reasons": []}
            for role in ("acceptance", "security", "compliance")
        ],
    }
    with pytest.raises(recording_pi.RecordingPiError, match="已过期"):
        client.require_publish_review(flow_version=7, flow_fingerprint="release-fingerprint")

    client.last_review["base_flow_version"] = 7
    client.last_review["all_passed"] = False
    client.last_review["verdicts"][1] = {
        "role": "security", "passed": False, "reasons": ["存在越权风险"],
    }
    with pytest.raises(recording_pi.RecordingPiError, match="存在越权风险"):
        client.require_publish_review(flow_version=7, flow_fingerprint="release-fingerprint")

    client.last_review["all_passed"] = True
    client.last_review["verdicts"][1] = {
        "role": "security", "passed": True, "reasons": [],
    }
    client.last_review["blocking_reasons"] = ["仍有未解决的越权风险"]
    with pytest.raises(recording_pi.RecordingPiError, match="仍有未解决的越权风险"):
        client.require_publish_review(flow_version=7, flow_fingerprint="release-fingerprint")

    client.last_review["blocking_reasons"] = []
    assert client.require_publish_review(
        flow_version=7, flow_fingerprint="release-fingerprint",
    )["all_passed"] is True


@pytest.mark.asyncio
async def test_incomplete_screenshot_coverage_does_not_undo_accepted_core_changes(
    monkeypatch,
    tmp_path,
) -> None:
    from dano.execution.page import flow_spec as flow_module

    before = FlowSpec(
        title="original",
        steps=[FlowStep(
            step_id="submit", method="POST", path="/api/submit",
            params=[ParamField(path="title", key="标题")],
        )],
        meta={"current_version": 0},
    )
    candidate = before.model_copy(deep=True)
    candidate.title = "grounded-partial-update"
    candidate.meta = {
        **candidate.meta,
        "capability_model": {
            "status": "needs_review",
            "semantic_coverage": {"complete": False, "missing": ["field_axis_contract"]},
            "proposal_gate": {"accepted": True, "reasons": []},
            "semantic_plan": {"unresolved_items": []},
        },
    }

    async def fake_apply(_current, *, submission, mode):  # noqa: ANN001, ARG001
        return candidate.model_copy(deep=True)

    monkeypatch.setattr(flow_module, "apply_recording_agent_submission", fake_apply)
    checkpoints: list[str] = []
    client = recording_pi.RecordingPiSession(
        tenant="tenant-a",
        subsystem="A-OA",
        recording_id=RECORDING_THREE,
        session_root=tmp_path,
        on_submission_accepted=lambda _spec, mode: checkpoints.append(mode),
    )
    client.bind_flow_spec(before)

    await client.apply_submission(
        {"_analysis_screenshot_count": 1}, mode="plan", base_flow_version=0,
    )

    assert client.current_flow_spec().title == "grounded-partial-update"
    assert client.last_submission_kind == "plan"
    assert checkpoints == ["plan"]


@pytest.mark.asyncio
async def test_unmatched_screenshot_plan_can_finish_without_mutating_or_checkpointing(
    tmp_path,
) -> None:
    before = FlowSpec(
        title="original",
        steps=[FlowStep(step_id="submit", method="POST", path="/api/submit")],
        meta={"current_version": 3},
    )
    checkpoints: list[str] = []
    client = recording_pi.RecordingPiSession(
        tenant="tenant-a",
        subsystem="A-OA",
        recording_id=RECORDING_THREE,
        session_root=tmp_path,
        on_submission_accepted=lambda _spec, mode: checkpoints.append(mode),
    )
    client.bind_flow_spec(before)

    result = await client.accept_unchanged_plan(
        base_flow_version=3,
        warning="截图分析未匹配到任何真实接口字段，当前配置未修改",
    )

    assert result["accepted"] is True
    assert result["unchanged"] is True
    assert client.current_flow_spec() == before
    assert client.last_submission_kind == "plan"
    assert checkpoints == []


@pytest.mark.asyncio
async def test_zero_command_timeout_waits_without_a_deadline(monkeypatch) -> None:  # noqa: ANN001
    client = recording_pi.RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA", recording_id=RECORDING_ONE,
    )
    client._proc = object()

    async def fake_send(command: dict) -> None:
        client._pending[command["request_id"]].set_result({"status": "completed"})

    async def unexpected_wait_for(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("zero timeout must not install a deadline")

    client._send = fake_send
    monkeypatch.setattr(recording_pi.asyncio, "wait_for", unexpected_wait_for)

    assert await client._command("prompt", timeout_s=0) == {"status": "completed"}
