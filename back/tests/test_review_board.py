from __future__ import annotations

import pytest

from dano.review.board import _completion_json, _review_projection


def test_completion_json_reads_segmented_content() -> None:
    payload = {
        "choices": [{"message": {"content": [
            {"type": "text", "text": '{"passed": true,'},
            {"type": "text", "text": '"reasons": []}'},
        ]}}],
    }

    assert _completion_json(payload) == {"passed": True, "reasons": []}


def test_completion_json_reads_tool_arguments_and_reasoning_fallback() -> None:
    tool_payload = {"choices": [{"message": {"content": "", "tool_calls": [{
        "function": {"arguments": '{"passed": true, "reasons": []}'},
    }]}}]}
    reasoning_payload = {"choices": [{"message": {
        "content": "",
        "reasoning_content": '核对完成。最终结果：{"passed": true, "reasons": []}',
    }}]}

    assert _completion_json(tool_payload)["passed"] is True
    assert _completion_json(reasoning_payload)["passed"] is True


def test_completion_json_rejects_truly_empty_response() -> None:
    with pytest.raises(ValueError, match="空响应"):
        _completion_json({"choices": [{"message": {"content": ""}}]})


def test_review_projection_removes_recorder_snapshots_but_keeps_contract() -> None:
    projected = _review_projection({
        "api_request": {
            "method": "POST",
            "path": "/submit",
            "params": ["类型"],
            "_release_snapshot": {"flow_spec": {"huge": "x" * 10000}},
            "_flow_spec": {"request_facts": {"responses": ["x" * 10000]}},
            "steps": [{"step_id": "submit", "response_json": {"data": [1, 2, 3]}}],
        },
    })

    api_request = projected["api_request"]
    assert api_request["method"] == "POST"
    assert api_request["params"] == ["类型"]
    assert "_release_snapshot" not in api_request
    assert "_flow_spec" not in api_request
    assert "response_json" not in api_request["steps"][0]
