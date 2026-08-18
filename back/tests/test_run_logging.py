from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from dano.infra import logging as logging_mod
from dano.infra import run_logging


def _reset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DANO_LOG_ROOT", str(tmp_path))
    monkeypatch.setenv("DANO_LOG_LEVEL", "INFO")
    logging_mod._CONFIGURED = False
    run_logging.reset_logging_state_for_tests()
    logging_mod.configure_logging("INFO")


def _records(tmp_path: Path, run_id: str = "") -> list[dict]:
    day = datetime.now().strftime("%Y-%m-%d")
    path = (
        tmp_path / "runs" / day / f"{run_id}.jsonl"
        if run_id
        else next((tmp_path / "system").glob("*.jsonl"))
    )
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_sequence_increments_and_identity_is_stable(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    run_logging.bind_run_context(
        run_id="run-a",
        recording_id="recording_1",
        action="action_1",
        tenant="111",
        subsystem="admin",
    )
    first = run_logging.emit_run_event("recording.batch.started", stage="analysis", status="started", summary="start")
    second = run_logging.emit_run_event("recording.batch.completed", stage="analysis", status="succeeded", summary="done")
    events = _records(tmp_path, "run-a")

    assert [item["sequence"] for item in events] == [1, 2]
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    for item in events:
        assert item["run_id"] == "run-a"
        assert item["recording_id"] == "recording_1"
        assert item["action"] == "action_1"
        assert item["tenant"] == "111"
        assert item["subsystem"] == "admin"
        assert item["source"]["module"]
        assert item["source"]["function"]
        assert item["source"]["line"] > 0
        assert item["timestamp"]


def test_missing_run_id_goes_to_system_file(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    run_logging.emit_run_event("gateway.ready", stage="system", status="succeeded", summary="ready")
    events = _records(tmp_path)
    assert events[0]["event"] == "gateway.ready"
    assert "run_id" not in events[0]


def test_invalid_status_normalizes_to_progress(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    run_logging.bind_run_context(run_id="run-status")
    record = run_logging.emit_run_event("x", status="", summary="blank")
    assert record["status"] == "progress"
    record = run_logging.emit_run_event("y", status="weird", summary="free text")
    assert record["status"] == "progress"


def test_association_fields_are_top_level(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    run_logging.bind_run_context(run_id="run-assoc")
    record = run_logging.emit_run_event(
        "agent_tool.done",
        stage="plan",
        status="succeeded",
        summary="计划已接受",
        call_id="call-0002",
        tool="submit_recording_plan",
        span_id="span-1",
        flow_version_before=1,
        flow_version_after=2,
        details={"capability_count": 0},
    )
    assert record["call_id"] == "call-0002"
    assert record["tool"] == "submit_recording_plan"
    assert record["span_id"] == "span-1"
    assert record["flow_version_before"] == 1
    assert record["flow_version_after"] == 2


def test_secrets_are_redacted_from_details_and_error_text(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    run_logging.bind_run_context(run_id="run-secret")
    run_logging.emit_run_event(
        "agent_tool.call",
        details={
            "Authorization": "Bearer secret-token",
            "Cookie": "sid=abc",
            "password": "hunter2",
            "storage_state": {"cookies": [{"value": "abc"}]},
            "note": "Authorization: Bearer abcdefghijklmnop",
        },
    )
    try:
        raise ValueError("password=super-secret Cookie: sid=xyz eyJhbGciOiJIUzI1NiJ9.aaaaaaa.bbbbbbbb")
    except ValueError as exc:
        run_logging.emit_run_exception("agent_tool.error", exc, stage="tool")
    events = _records(tmp_path, "run-secret")
    blob = json.dumps(events, ensure_ascii=False)
    assert "secret-token" not in blob
    assert "hunter2" not in blob
    assert "super-secret" not in blob
    assert "sid=abc" not in blob
    assert "<redacted>" in blob


def test_request_and_value_helpers_do_not_keep_raw_bodies(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    fields = run_logging.request_log_fields(
        method="POST",
        path="/oa/leave",
        status=200,
        body={"password": "hidden", "name": "n"},
        headers={"Authorization": "Bearer abc"},
    )
    locator = run_logging.value_locator(wire_path="body.password", value="hidden", source_kind="request")
    assert fields["method"] == "POST"
    assert fields["path"] == "/oa/leave"
    assert fields["body_size"] > 0
    assert "body.password" in fields["body_field_paths"] or "password" in fields["body_field_paths"]
    assert "hidden" not in json.dumps(fields)
    assert locator["value_type"] == "str"
    assert locator["value_length"] == 6
    assert "hidden" not in locator["value_hash"]


def test_log_directory_write_failure_does_not_raise(monkeypatch, tmp_path, capsys) -> None:
    blocked = tmp_path / "blocked.jsonl"
    blocked.write_text("not-a-dir", encoding="utf-8")
    monkeypatch.setenv("DANO_LOG_ROOT", str(blocked))
    monkeypatch.setenv("DANO_LOG_LEVEL", "INFO")
    logging_mod._CONFIGURED = False
    run_logging.reset_logging_state_for_tests()
    run_logging.bind_run_context(run_id="run-disk")
    run_logging.emit_run_event("recording.batch.started", summary="should not crash")
    captured = capsys.readouterr()
    assert "persist failed" in captured.err


def test_detail_visibility_stays_off_console(monkeypatch, tmp_path, capsys) -> None:
    _reset(monkeypatch, tmp_path)
    run_logging.bind_run_context(run_id="run-quiet")
    run_logging.emit_run_event(
        "agent_tool.done",
        level="debug",
        visibility="detail",
        summary="无变化轮询",
    )
    captured = capsys.readouterr()
    assert "无变化轮询" not in captured.out
    events = _records(tmp_path, "run-quiet")
    assert events[0]["summary"] == "无变化轮询"


def test_error_console_is_multiline(monkeypatch, tmp_path, capsys) -> None:
    _reset(monkeypatch, tmp_path)
    run_logging.bind_run_context(run_id="run-err", skill_id="admin.action_1")
    run_logging.emit_run_event(
        "recording.export.completed",
        stage="export",
        status="failed",
        level="error",
        summary="Skill 包导出失败",
        skill_id="admin.action_1",
        details={
            "canonical_contract_present": False,
            "output_directory": r"E:\python\try\Dano\export\agent-skills",
        },
        error={
            "code": "CANONICAL_CAPABILITY_CONTRACT_MISSING",
            "cause": "当前发布资产没有可供包生成器读取的规范能力契约",
            "traceback": "Traceback...",
        },
        next_action="检查该 asset version 对应的发布能力契约和 release identity",
    )
    captured = capsys.readouterr().out
    assert "[导出] Skill 包导出失败" in captured
    assert "code: CANONICAL_CAPABILITY_CONTRACT_MISSING" in captured
    assert "traceback: 已写入当前 run JSONL" in captured


def test_configure_logging_is_idempotent(monkeypatch) -> None:
    logging_mod._CONFIGURED = False
    logging_mod.configure_logging("INFO")
    first = logging_mod._CONFIGURED
    logging_mod.configure_logging("DEBUG")
    assert first is True
    assert logging_mod._CONFIGURED is True


def test_clearing_context_then_rebind_keeps_run_id(monkeypatch, tmp_path) -> None:
    _reset(monkeypatch, tmp_path)
    run_logging.bind_run_context(run_id="run-rebind", recording_id="rec-1", action="a", tenant="111", subsystem="s")
    run_logging.clear_run_context()
    run_logging.bind_run_context(run_id="run-rebind", recording_id="rec-1", action="a", tenant="111", subsystem="s")
    run_logging.emit_run_event("recording.lifecycle.completed", stage="lifecycle", status="succeeded")
    events = _records(tmp_path, "run-rebind")
    assert events[0]["run_id"] == "run-rebind"
    assert events[0]["recording_id"] == "rec-1"
