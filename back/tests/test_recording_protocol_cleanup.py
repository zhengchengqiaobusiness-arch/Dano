from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from dano.gateway import app as gateway


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PAGE_RECORDER = _REPO_ROOT / "skillfrontend" / "src" / "components" / "PageRecorder.tsx"


def test_finalize_emits_flow_spec_without_legacy_request_fields_protocol() -> None:
    source = inspect.getsource(gateway.record_ws)

    assert not hasattr(gateway, "_request_fields_msg")
    assert '"type": "request_fields"' not in source
    assert source.count("pending_flow_spec = to_flow_spec(") == 1
    assert "pending_samples" not in source
    assert "pending_reads" not in source
    assert "pending_storage" not in source
    assert "pending_required" not in source
    assert "pending_page_enum_options" not in source
    assert "pending_field_evidence" not in source
    assert "pending_page_events" not in source
    assert not hasattr(gateway, "_merge_recording_step_edits")
    assert 'msg.get("steps")' not in source


def test_frontend_uses_only_flow_spec_workbench_protocol() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert 'm.type === "request_fields"' not in source
    assert "interface RecField" not in source
    assert "interface RecCand" not in source
    assert "const [fields, setFields]" not in source
    assert "function payload()" not in source
    assert "success_marker: null" not in source

    publish_start = source.index("function performPublishRequest()")
    publish_end = source.index("function stopAll()", publish_start)
    publish_source = source[publish_start:publish_end]
    for ghost_key in ("param_map", "selects:", "identity:", "step_idxs", "use_flow_spec"):
        assert ghost_key not in publish_source
    assert "operation_id: operationId" in publish_source
    assert "title: publishTitle" in publish_source
    assert "expected_fingerprint:" in publish_source
    # P5 makes the server draft authoritative; publish sends only its fingerprint.
    assert "flow_spec: currentSpec" not in publish_source

    finalize_start = source.index("function finalize()")
    finalize_end = source.index("function badAction", finalize_start)
    finalize_source = source[finalize_start:finalize_end]
    assert 'type: "finalize"' in finalize_source
    assert "steps" not in finalize_source

    error_start = source.index('else if (m.type === "error")')
    error_end = source.index("ws.onclose =", error_start)
    assert "if (!m.operation) connectionErrorRef.current = detail" in source[error_start:error_end]


def test_frontend_relays_backward_delete_without_relying_only_on_keydown() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert 'inputType === "deleteContentBackward"' in source
    assert "onBeforeInput={onKbBeforeInput}" in source


def test_invoke_protocol_rejects_removed_compatibility_fields() -> None:
    assert gateway.InvokeReq(input={"month": "2026-07"}).input == {"month": "2026-07"}
    assert gateway.ToolCallReq(name="A-OA__query", input={}).input == {}

    for obsolete in ({"arguments": {}}, {"capability": "query"}, {"metadata": {}}):
        with pytest.raises(ValidationError):
            gateway.InvokeReq(input={}, **obsolete)
    with pytest.raises(ValidationError):
        gateway.ToolCallReq(name="A-OA__query", input={}, arguments={})
