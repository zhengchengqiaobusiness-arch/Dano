from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowSpecConflictError,
    FlowStep,
    IdentityBinding,
    ParamField,
    RequestFact,
    RequestFacts,
    SelectBinding,
    apply_client_flow_patch,
    flow_spec_fingerprint,
    flow_spec_to_client,
)
from dano.gateway import app as gateway
from dano.onboarding.recording_workflow import (
    CANONICAL_RECORDING_COMMANDS,
    WorkflowSnapshot,
    WorkflowStatus,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PAGE_RECORDER = _REPO_ROOT / "skillfrontend" / "src" / "components" / "PageRecorder.tsx"


def _authoritative_spec() -> FlowSpec:
    return FlowSpec(
        flow_id="authoritative",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            path="/api/submit",
            headers={"Authorization": "Bearer server-token", "X-Tenant": "tenant-secret"},
            body_source='{"reason":"private body"}',
            response_json={
                "rows": [{"id": index, "name": f"employee-{index}"} for index in range(100)],
                "password": "response-secret",
            },
            params=[ParamField(path="reason", key="原因", value="事假")],
            selects=[SelectBinding(
                path="reason",
                param="原因",
                source_url="/api/options",
                source_headers={"Authorization": "Bearer option-token"},
                source_body='{"tenant":"private"}',
                value_key="id",
                label_key="name",
            )],
            identity=[IdentityBinding(
                path="userId", source="localStorage.userId", value="user-secret",
            )],
        )],
        request_facts=RequestFacts(requests=[RequestFact(
            request_id="request-1",
            request_index=0,
            method="POST",
            url="https://example.test/api/submit",
            headers={"Authorization": "Bearer fact-token"},
            post_data='{"reason":"private fact body"}',
            response_json={"token": "fact-response-secret", "ok": True},
        )]),
    )


def test_client_projection_is_bounded_and_contains_no_authoritative_secrets() -> None:
    spec = _authoritative_spec()
    client = flow_spec_to_client(spec)
    serialized = repr(client)

    for secret in (
        "server-token", "tenant-secret", "option-token", "user-secret",
        "fact-token", "fact-response-secret", "response-secret",
        "private fact body", "private body",
    ):
        assert secret not in serialized
    assert client["steps"][0]["headers"] == {"Authorization": "***", "X-Tenant": "***"}
    assert client["steps"][0]["body_source"] == ""
    assert client["steps"][0]["selects"][0]["source_headers"] == {"Authorization": "***"}
    assert client["steps"][0]["identity"][0]["value"] == "***"
    assert client["request_facts"]["requests"][0]["post_data"] == ""
    assert client["meta"]["current_fingerprint"] == flow_spec_fingerprint(spec)


@pytest.mark.parametrize("fixture_name", [
    "daily_report_flow_spec.json",
    "leave_flow_spec.json",
    "multi_capability_flow_spec.json",
    "multi_enum_flow_spec.json",
    "promoted_request_flow_spec.json",
    "work_hours_flow_spec.json",
])
def test_complex_projection_fingerprint_can_edit_authoritative_spec(fixture_name: str) -> None:
    fixture = Path(__file__).parent / "fixtures" / "recording_v3" / fixture_name
    spec = FlowSpec.model_validate(json.loads(fixture.read_text(encoding="utf-8")))
    fingerprint = flow_spec_to_client(spec)["meta"]["current_fingerprint"]

    updated = apply_client_flow_patch(
        spec,
        [{"op": "update_flow", "field": "title", "value": "业务操作"}],
        expected_fingerprint=fingerprint,
    )

    assert updated.title == "业务操作"


def test_client_patch_requires_current_fingerprint_and_preserves_server_facts() -> None:
    spec = _authoritative_spec()
    fingerprint = flow_spec_fingerprint(spec)

    with pytest.raises(ValueError, match="expected_fingerprint is required"):
        apply_client_flow_patch(
            spec,
            [{"op": "update", "step_id": "submit", "field": "name", "value": "missing"}],
            expected_fingerprint="",
        )

    updated = apply_client_flow_patch(
        spec,
        [{"op": "update", "step_id": "submit", "field": "name", "value": "提交申请"}],
        expected_fingerprint=fingerprint,
    )
    assert updated.steps[0].name == "提交申请"
    assert updated.steps[0].headers == spec.steps[0].headers
    assert updated.steps[0].body_source == spec.steps[0].body_source
    assert updated.steps[0].response_json == spec.steps[0].response_json

    execution_updated = apply_client_flow_patch(
        updated,
        [{
            "op": "update", "step_id": "submit", "param_path": "reason",
            "field": "required", "value": False,
        }],
        expected_fingerprint=fingerprint,
    )
    with pytest.raises(FlowSpecConflictError):
        apply_client_flow_patch(
            execution_updated,
            [{"op": "update", "step_id": "submit", "field": "name", "value": "stale"}],
            expected_fingerprint=fingerprint,
        )


def test_client_projection_and_patch_use_only_public_source_taxonomy() -> None:
    spec = _authoritative_spec()
    client = flow_spec_to_client(spec)
    assert client["steps"][0]["params"][0]["source_kind"] == "caller_input"

    updated = apply_client_flow_patch(
        spec,
        [{
            "op": "update",
            "step_id": "submit",
            "param_path": "reason",
            "field": "source_kind",
            "value": "constant",
        }],
        expected_fingerprint=flow_spec_fingerprint(spec),
    )
    assert updated.steps[0].params[0].source_kind == "constant"
    assert updated.steps[0].params[0].exposed_to_user is False
    assert flow_spec_to_client(updated)["steps"][0]["params"][0]["source_kind"] == "constant"


@pytest.mark.parametrize("field", [
    "headers", "body_source", "response_json", "identity", "params", "source_meta",
])
def test_client_patch_rejects_server_owned_step_fields(field: str) -> None:
    spec = _authoritative_spec()
    with pytest.raises(ValueError, match="server-owned step field"):
        apply_client_flow_patch(
            spec,
            [{"op": "update", "step_id": "submit", "field": field, "value": {}}],
            expected_fingerprint=flow_spec_fingerprint(spec),
        )


def test_frontend_and_gateway_use_only_canonical_snapshot_protocol() -> None:
    gateway_source = inspect.getsource(gateway.record_ws)
    frontend = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert "RecordingSessionRegistry" in gateway_source
    assert 'incoming.type === "snapshot"' in frontend
    assert 'type: "finish"' in frontend
    assert 'type: "patch_draft"' in frontend
    assert 'type: "republish"' in frontend
    assert 'type: "answer"' in frontend
    assert 'type: "cancel"' in frontend
    assert "expected_fingerprint: current.draft_fingerprint" in frontend
    for retired in (
        "flow_replace", "request_fields", "orchestrate_flow", "auto_fix_flow",
        "publish_request", "refresh_flow_spec", "analysis_terminated",
    ):
        assert retired not in gateway_source
        assert retired not in frontend


def test_frontend_waits_for_authoritative_terminal_snapshot_before_result_stage() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")
    stage = source[source.index("function pageStage"):source.index("function recorderWebSocketUrl")]
    receiver = source[source.index("function receiveSnapshot"):source.index("function openRecordingSocket")]

    assert 'if (status === "idle") return 0' in stage
    assert '["recording", "processing", "waiting_operator"].includes(status)' in stage
    assert "return 2" in stage
    assert "setVisibleStage" not in source
    assert "setSnapshot(next)" in receiver
    assert 'incoming.type === "request"' not in source


def test_frontend_has_one_finish_and_one_republish_entrypoint() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert source.count('send({ type: "finish"') == 1
    assert source.count('send({ type: "republish"') == 2  # direct and after saved patch
    assert source.count('send({ type: "cancel"') == 1
    assert "停止并分析请求" in source
    assert "修改后再次发布" in source
    assert "重新分析" not in source
    assert "重新验证并发布" not in source
    assert "finishRequestedRef.current" in source
    assert 'loading={finishRequested}' in source


def test_operator_question_and_cancel_share_the_authoritative_workflow() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert 'next.status === "waiting_operator"' in source
    assert "setAssistantOpen(true)" in source
    assert 'question_id: question.question_id' in source
    assert 'send({ type: "cancel" })' in source


def test_snapshot_protocol_has_no_legacy_public_states_or_commands() -> None:
    fields = set(WorkflowSnapshot.model_fields)

    assert fields == {
        "run_id", "action", "title", "revision", "status", "progress",
        "capture_frozen", "draft", "issues", "insights", "question", "release", "error",
    }
    assert CANONICAL_RECORDING_COMMANDS == {
        "start", "input", "finish", "patch_draft", "republish", "answer", "cancel", "ping",
    }
    assert {status.value for status in WorkflowStatus} == {
        "idle", "recording", "processing", "waiting_operator",
        "editable", "published", "cancelled", "failed",
    }
