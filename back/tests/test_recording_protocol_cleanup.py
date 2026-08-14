from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dano.gateway import app as gateway
from dano.onboarding.recording_gateway import RecordingGatewaySession
from dano.onboarding.recording_workflow import CANONICAL_RECORDING_COMMANDS


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PAGE_RECORDER = _REPO_ROOT / "skillfrontend" / "src" / "components" / "PageRecorder.tsx"


def test_recording_gateway_contains_one_public_route_and_one_dispatch_owner() -> None:
    app_source = inspect.getsource(gateway)
    route_source = inspect.getsource(gateway.record_ws)
    session_source = inspect.getsource(RecordingGatewaySession.dispatch)

    assert app_source.count('@app.websocket("/onboarding/page/record")') == 1
    assert "await session.dispatch(message)" in route_source
    for command in CANONICAL_RECORDING_COMMANDS - {"start"}:
        assert f'command == "{command}"' in session_source or command == "input"
    assert "unsupported recording command" in session_source


def test_retired_gateway_implementation_and_protocol_messages_are_physically_absent() -> None:
    app_source = inspect.getsource(gateway)
    frontend = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert "_retired_record_ws" not in app_source
    assert "RECORDING_FLOW_PROTOCOL_VERSION" not in app_source
    for retired in (
        "orchestrate_flow", "auto_fix_flow", "publish_request", "finalize",
        "flow_update", "flow_replace", "refresh_flow_spec", "request_fields",
        "analysis_terminated", "agent_question", "verify_progress",
    ):
        assert retired not in app_source
        assert retired not in frontend


def test_gateway_module_has_no_unreferenced_legacy_recording_helpers() -> None:
    source = inspect.getsource(gateway)
    tree = ast.parse(source)
    loads = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    ignored = {"record_ws", "_publish_canonical_recording"}
    leftovers = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name.startswith(("_recording_", "_analysis_"))
        and node.name not in loads
        and node.name not in ignored
    }

    assert leftovers == set()


def test_frontend_coalesces_high_frequency_input_and_frames() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert "latestFrameRef.current" in source
    assert "decodingFrameRef.current" in source
    assert "pointerMoveRef.current" in source
    assert "wheelRef.current" in source
    assert "window.setTimeout" in source
    assert 'inputEvent.inputType !== "deleteContentBackward"' in source
    assert 'kind: "key", key: "Backspace"' in source


def test_recording_canvas_preserves_captured_frame_aspect_ratio() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")
    recording_view = source.split("function renderRecording()", 1)[1].split(
        "function renderParamEditor", 1,
    )[0]

    assert 'aspectRatio: `${frameMeta.width} / ${frameMeta.height}`' in recording_view
    assert 'height: "calc(100vh - 245px)"' not in recording_view


def test_frontend_does_not_drive_capability_results_from_frames_or_request_counts() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")
    message_handler = source[source.index("socket.onmessage"):source.index("socket.onerror")]

    assert 'incoming.type === "snapshot"' in message_handler
    assert 'incoming.type === "frame"' in message_handler
    assert 'incoming.type === "request"' not in message_handler
    assert "setVisibleStage(2)" not in message_handler


def test_capability_edits_are_deltas_not_client_owned_flow_replacements() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")
    patcher = source[source.index("function flushDraftEdits()"):source.index("function scheduleFrameDecode")]
    republish = source[source.index("function republish()"):source.index("function normalizedPoint")]

    assert 'type: "patch_draft"' in patcher
    assert "edits," in patcher
    assert "expected_revision: current.revision" in patcher
    assert "expected_fingerprint: current.draft_fingerprint" in patcher
    assert "flow_spec" not in republish


def test_skill_export_is_only_called_at_the_publish_boundary_for_current_skill() -> None:
    publisher = inspect.getsource(gateway._publish_canonical_recording)

    assert "await _auto_export(tenant, skill_ids={skill_id}, strict=True)" in publisher
    assert "write_exports" not in inspect.getsource(gateway.record_ws)
