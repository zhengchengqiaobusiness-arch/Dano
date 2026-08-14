from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from dano.execution.page.flow_spec import FlowCapability, FlowSpec
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
    publish_end = source.index("function terminateAll()", publish_start)
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

    assert 'inputEvent.inputType !== "deleteContentBackward"' in source
    assert 'kind: "key", key: "Backspace"' in source
    assert "lastBackspaceKeydownAtRef" in source
    assert "onBeforeInput={onKbBeforeInput}" in source


def test_recording_workspace_waits_for_terminal_analysis_before_showing_results() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    finalize_start = source.index('if (m.operation === "finalize")')
    finalize_end = source.index("// 发布请求可能与最后一次字段更新响应交错到达", finalize_start)
    finalize_handler = source[finalize_start:finalize_end]
    assert "setWorkspaceStage(2)" not in finalize_handler
    assert 'setPhase("recording")' not in finalize_handler

    result_start = source.index('else if (m.type === "result")')
    result_end = source.index('else if (m.type === "error")', result_start)
    result_handler = source[result_start:result_end]
    assert "resultCapabilities.length > 0" in result_handler
    assert "setWorkspaceStage(2)" in result_handler

    error_start = source.index('else if (m.type === "error")', result_end)
    error_end = source.index("ws.onclose =", error_start)
    error_handler = source[error_start:error_end]
    assert 'm.operation === "plan" || m.operation === "publish"' not in error_handler


def test_empty_automatic_capability_plan_returns_a_terminal_result() -> None:
    source = inspect.getsource(gateway.record_ws)
    plan_start = source.index('elif t == "orchestrate_flow":')
    plan_end = source.index('elif t == "auto_fix_flow":', plan_start)
    plan_source = source[plan_start:plan_end]
    skipped_start = plan_source.index('"recording.auto_publish_skipped"')
    skipped_branch = plan_source[skipped_start:]

    assert '"type": "result"' in skipped_branch
    assert '"stage": "capability_plan"' in skipped_branch


def test_automatic_publish_waits_for_the_complete_capability_plan() -> None:
    incomplete = FlowSpec(
        capabilities=[FlowCapability(name="query_items", kind="query_status")],
        meta={
            "capability_generation": {
                "initial_completed": False,
                "status": "incomplete_agent_plan",
            },
        },
    )
    complete = incomplete.model_copy(deep=True)
    complete.meta["capability_generation"] = {
        "initial_completed": True,
        "status": "ready",
    }

    assert gateway._recording_capability_plan_complete(incomplete) is False
    assert gateway._recording_capability_plan_complete(complete) is True

    source = inspect.getsource(gateway.record_ws)
    finalize_start = source.index('elif t == "finalize":')
    finalize_end = source.index('elif t == "flow_update":', finalize_start)
    finalize_source = source[finalize_start:finalize_end]
    assert "_recording_capability_plan_complete(pending_flow_spec)" in finalize_source


def test_recording_publish_keeps_the_unique_recording_action() -> None:
    source = inspect.getsource(gateway.record_ws)
    finalize_start = source.index('elif t == "finalize":')
    finalize_end = source.index('elif t == "flow_update":', finalize_start)
    publish_start = source.index('elif t == "publish_request":')
    publish_end = source.index("except asyncio.CancelledError:", publish_start)

    finalize_source = source[finalize_start:finalize_end]
    publish_source = source[publish_start:publish_end]
    assert '"action": session_action' in finalize_source
    assert "publish_action = session_action" in publish_source
    assert "recorded_goal_slug" not in finalize_source
    assert "recorded_goal_slug" not in publish_source


def test_live_control_messages_have_one_dispatch_implementation() -> None:
    source = inspect.getsource(gateway.record_ws)

    assert "if await _handle_live_recording_message(msg):" in source
    for message_type in ("input", "ping", "stop", "terminate", "agent_answer"):
        assert source.count(f'message_type == "{message_type}"') == 1
        assert f't == "{message_type}"' not in source


def test_expensive_recording_operations_have_one_canonical_path() -> None:
    backend = inspect.getsource(gateway.record_ws)
    frontend = _PAGE_RECORDER.read_text(encoding="utf-8")

    for message_type in ("orchestrate_flow", "auto_fix_flow", "publish_request"):
        assert backend.count(f'elif t == "{message_type}":') == 1
    for retired in ("step_naming", "business_description"):
        assert f'elif t == "{retired}":' not in backend
        assert f'type: "{retired}"' not in frontend

    repair_start = backend.index('elif t == "auto_fix_flow":')
    repair_end = backend.index('elif t == "console_log_upload":', repair_start)
    repair_source = backend[repair_start:repair_end]
    assert "_verify_finalized_recording(" in repair_source
    assert "pi_session.prompt(" not in repair_source

    publish_start = backend.index('elif t == "publish_request":')
    publish_end = backend.index("except asyncio.CancelledError:", publish_start)
    publish_source = backend[publish_start:publish_end]
    assert publish_source.count("run_request_onboarding(") == 1
    assert '"type": "publish_request"' in backend
    assert 'type: "publish_request"' in frontend


def test_business_description_button_uses_the_unified_plan_operation() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")
    label_at = source.index('"生成整体说明"')
    button_start = source.rfind("<Button", 0, label_at)
    button_source = source[button_start:label_at]

    assert "onClick={orchestrateFlow}" in button_source
    assert "loading={orchestrateBusy}" in button_source
    assert "descBusy" not in source


def test_invoke_protocol_rejects_removed_compatibility_fields() -> None:
    assert gateway.InvokeReq(input={"month": "2026-07"}).input == {"month": "2026-07"}
    assert gateway.ToolCallReq(name="A-OA__query", input={}).input == {}

    for obsolete in ({"arguments": {}}, {"capability": "query"}, {"metadata": {}}):
        with pytest.raises(ValidationError):
            gateway.InvokeReq(input={}, **obsolete)
    with pytest.raises(ValidationError):
        gateway.ToolCallReq(name="A-OA__query", input={}, arguments={})
