from __future__ import annotations

import pytest

from dano.onboarding.recording_thoughts import ThoughtBridge, thought_from_agent_event


def test_thought_from_text_delta() -> None:
    payload = thought_from_agent_event({
        "event": "message_update",
        "delta_type": "text_delta",
        "delta": "发现写操作没有回读",
    })
    assert payload == {"kind": "text", "text": "发现写操作没有回读"}


def test_thought_from_tool_start() -> None:
    payload = thought_from_agent_event({
        "event": "tool_execution_start",
        "toolName": "get_validation_report",
        "tool_args": "{\n  \"x\": 1\n}",
    })
    assert payload is not None
    assert payload["kind"] == "tool"
    assert payload["phase"] == "start"
    assert payload["tool"] == "get_validation_report"
    assert payload["args"] == "{\n  \"x\": 1\n}"
    assert "get_validation_report" in payload["text"]


def test_thought_from_tool_end_keeps_json() -> None:
    payload = thought_from_agent_event({
        "event": "tool_execution_end",
        "toolName": "get_validation_report",
        "success": True,
        "tool_result": "{\n  \"issues\": []\n}",
    })
    assert payload == {
        "kind": "tool",
        "phase": "end",
        "text": "get_validation_report 调用成功",
        "tool": "get_validation_report",
        "ok": True,
        "result": "{\n  \"issues\": []\n}",
    }


@pytest.mark.asyncio
async def test_thought_bridge_coalesces_text_deltas() -> None:
    sent: list[dict] = []

    async def send(payload):  # noqa: ANN001
        sent.append(payload)

    bridge = ThoughtBridge(send, flush_s=0.01)
    await bridge.push({"delta_type": "text_delta", "delta": "先看"})
    await bridge.push({"delta_type": "text_delta", "delta": "回读"})
    await bridge.flush()
    assert sent == [{"type": "thought", "kind": "text", "text": "先看回读"}]


@pytest.mark.asyncio
async def test_thought_bridge_flushes_before_tool() -> None:
    sent: list[dict] = []

    async def send(payload):  # noqa: ANN001
        sent.append(payload)

    bridge = ThoughtBridge(send, flush_s=1)
    await bridge.push({"delta_type": "text_delta", "delta": "准备补证"})
    await bridge.push({"event": "tool_execution_start", "toolName": "replay_request"})
    assert sent[0]["text"] == "准备补证"
    assert sent[1]["kind"] == "tool"
    assert sent[1]["type"] == "thought"
