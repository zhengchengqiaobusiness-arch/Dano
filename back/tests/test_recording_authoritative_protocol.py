from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowSpecConflictError,
    IdentityBinding,
    ParamField,
    RequestFact,
    RequestFacts,
    SelectBinding,
    FlowStep,
    apply_client_flow_patch,
    flow_spec_fingerprint,
    flow_spec_to_client,
)
from dano.gateway import app as gateway


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
            identity=[IdentityBinding(path="userId", source="localStorage.userId", value="user-secret")],
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


def test_workbench_contract_uses_stable_field_identity_and_rolls_back_disconnects() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert 'analysis_kind?: "initial" | "incremental"' in source
    assert 'lastAnalysisEvidence?.analysis_kind !== "initial"' in source
    assert "fieldChanges.map(analysisFieldChangeText)" in source
    assert "...structuralChanges" in source
    assert "showDetailedAnalysis && visibleChangeLines.map" in source
    assert "未匹配：" not in source
    assert "截图匹配字段" not in source
    assert "lastOperationReport && !lastAnalysisEvidence" in source
    assert 'analysisNeedsReview || validationRefreshing ? "info"' in source
    assert "changeLines.slice(0, 3)" in source
    assert "展开全部修改" in source
    assert 'm.operation === "finalize"' in source
    assert "setLastAnalysisEvidence(null)" in source
    assert "m.analysis_application?.summary" in source
    assert "模型分析未完成，已保留可运行的事实基线" not in source
    assert "p.field_id" in source
    assert 'key={`${step.step_id}:param:${stripBodyPrefix(p.path || p.key)}`}' not in source
    onclose = source[source.index("ws.onclose ="):source.index("ws.onerror =")]
    assert "failQueuedFlowMutation" in onclose


def test_enum_mapping_warning_tracks_the_textarea_draft_before_blur() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")
    editor = source[source.index('<FieldControl label="枚举候选">'):source.index("</FieldControl>", source.index('<FieldControl label="枚举候选">'))]

    assert "onDraftChange={(v) =>" in editor
    assert "parseEnumOptionsText(v)" in editor
    assert "patchLocalParam(step.step_id, p" in editor
    assert "need_human_confirm: !mappingComplete" in editor
    assert 'if (local !== (value || "")) onSave(local);' in source


def test_recorded_page_operations_are_never_intercepted() -> None:
    frontend = _PAGE_RECORDER.read_text(encoding="utf-8")
    backend = inspect.getsource(gateway.record_ws)

    assert "record_only" not in frontend
    assert "intercept: false" in frontend
    assert "intercept_submit=False" in backend
    assert 'recording_mode = "real_submit"' in backend


def test_client_projection_is_bounded_and_contains_no_authoritative_secrets() -> None:
    spec = _authoritative_spec()
    client = flow_spec_to_client(spec)
    serialized = repr(client)

    for secret in (
        "server-token", "tenant-secret", "option-token", "user-secret",
        "fact-token", "fact-response-secret", "response-secret", "private fact body", "private body",
    ):
        assert secret not in serialized
    assert client["steps"][0]["headers"] == {"Authorization": "***", "X-Tenant": "***"}
    assert client["steps"][0]["body_source"] == ""
    assert client["steps"][0]["backup_body_source"] == ""
    assert client["steps"][0]["selects"][0]["source_headers"] == {"Authorization": "***"}
    assert client["steps"][0]["selects"][0]["source_body"] == ""
    assert client["steps"][0]["identity"][0]["value"] == "***"
    assert client["request_facts"]["requests"][0]["post_data"] == ""
    assert client["steps"][0]["response_projection"]["truncated"] is True
    assert client["meta"]["current_fingerprint"] == flow_spec_fingerprint(spec)


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
    assert updated.steps[0].selects[0].source_headers == spec.steps[0].selects[0].source_headers
    assert updated.steps[0].selects[0].source_body == spec.steps[0].selects[0].source_body
    assert updated.steps[0].identity[0].value == spec.steps[0].identity[0].value
    assert updated.meta["current_version"] == 1
    assert flow_spec_fingerprint(updated) == fingerprint

    execution_updated = apply_client_flow_patch(
        updated,
        [{
            "op": "update", "step_id": "submit", "param_path": "reason",
            "field": "required", "value": False,
        }],
        expected_fingerprint=fingerprint,
    )
    assert flow_spec_fingerprint(execution_updated) != fingerprint

    with pytest.raises(FlowSpecConflictError) as conflict:
        apply_client_flow_patch(
            execution_updated,
            [{"op": "update", "step_id": "submit", "field": "name", "value": "stale"}],
            expected_fingerprint=fingerprint,
        )
    assert conflict.value.current_fingerprint == flow_spec_fingerprint(execution_updated)


@pytest.mark.parametrize("field", ["headers", "body_source", "response_json", "identity", "params", "source_meta"])
def test_client_patch_rejects_server_owned_step_fields(field: str) -> None:
    spec = _authoritative_spec()
    with pytest.raises(ValueError, match="server-owned step field"):
        apply_client_flow_patch(
            spec,
            [{"op": "update", "step_id": "submit", "field": field, "value": {}}],
            expected_fingerprint=flow_spec_fingerprint(spec),
        )


def test_select_patch_updates_contract_without_accepting_hidden_transport_values() -> None:
    spec = _authoritative_spec()
    updated = apply_client_flow_patch(
        spec,
        [{
            "op": "upsert_select",
            "step_id": "submit",
            "binding": {
                "path": "reason",
                "param": "原因",
                "value_key": "code",
                "label_key": "label",
                "source_headers": {"Authorization": "attacker"},
                "source_body": "attacker",
            },
        }],
        expected_fingerprint=flow_spec_fingerprint(spec),
    )
    binding = updated.steps[0].selects[0]
    assert (binding.value_key, binding.label_key) == ("code", "label")
    assert binding.source_headers == spec.steps[0].selects[0].source_headers
    assert binding.source_body == spec.steps[0].selects[0].source_body


def test_gateway_and_frontend_use_one_versioned_server_authoritative_protocol() -> None:
    gateway_source = inspect.getsource(gateway.record_ws)
    frontend_source = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert "_restore_hidden_flow_spec_fields" not in gateway_source
    assert 'msg.get("flow_spec")' not in gateway_source
    assert '"full_spec"' not in gateway_source
    assert '"type": "flow_spec_updated"' not in gateway_source
    assert '"type": "step_names"' not in gateway_source
    assert '"type": "business_description"' not in gateway_source
    projection_source = inspect.getsource(gateway._recording_flow_projection)
    assert '"protocol_version": RECORDING_FLOW_PROTOCOL_VERSION' in projection_source

    assert "full_spec" not in frontend_source
    assert 'type: "flow_replace"' not in frontend_source
    assert "sendReplace(" not in frontend_source
    assert "flow_spec: currentSpec" not in frontend_source
    assert "protocol_version" in frontend_source
    assert "expected_fingerprint: serverFingerprintRef.current" in frontend_source

    publish_start = frontend_source.index("function performPublishRequest()")
    publish_end = frontend_source.index("function stopAll()", publish_start)
    publish_source = frontend_source[publish_start:publish_end]
    assert "expected_fingerprint:" in publish_source
    assert "flow_spec:" not in publish_source


def test_frontend_pauses_flow_loading_during_recorder_reconnect() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    helper_start = source.index("function pauseFlowOperationForReconnect()")
    helper_end = source.index("function resumeFlowOperationAfterReconnect", helper_start)
    helper_source = source[helper_start:helper_end]
    assert "setOrchestrateBusy(false)" not in helper_source
    assert "setAutoFixBusy(false)" not in helper_source
    assert "flowOperationRef.current = null" not in helper_source

    close_start = source.index("ws.onclose = (event) =>")
    close_end = source.index("};", close_start)
    assert "pauseFlowOperationForReconnect()" in source[close_start:close_end]


def test_frontend_does_not_fill_the_websocket_queue_with_application_pings() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert "heartbeatTimerRef" not in source
    assert 'type: "ping"' not in source


def test_frontend_reconnects_after_component_refresh_instead_of_staying_disconnected() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    assert "const componentMountedRef = useRef(false)" in source
    lifecycle_start = source.index("useEffect(() => {\n    componentMountedRef.current = true;")
    lifecycle_end = source.index("}, []);", lifecycle_start)
    lifecycle = source[lifecycle_start:lifecycle_end]
    assert "componentMountedRef.current = false" in lifecycle

    close_start = source.index("ws.onclose = (event) =>")
    close_end = source.index("ws.onerror =", close_start)
    close_handler = source[close_start:close_end]
    assert "intentionalCloseRef.current && !componentMountedRef.current" in close_handler
    assert "intentionalCloseRef.current = false" in close_handler
    assert "scheduleRecorderReconnect()" in close_handler


def test_frontend_capability_tabs_do_not_reserve_an_empty_spacer() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    workbench_start = source.index("function renderFlowWorkbench()")
    tabs_start = source.index("<Tabs", workbench_start)
    tabs_end = source.index("/>", tabs_start)
    tabs = source[tabs_start:tabs_end]
    assert 'tabBarStyle={{ marginBottom: 0 }}' in tabs

    composer_start = source.index("function renderCapabilityComposerPanel()")
    composer_end = source.index("function renderDescriptionPanel()", composer_start)
    composer = source[composer_start:composer_end]
    assert "analysisScreenshots.length > 0 || !!flowSpec.meta?.capability_generation" in composer
    assert 'display: analysisScreenshots.length || flowSpec.meta?.capability_generation ? undefined : "none"' not in composer


def test_recording_transport_drains_messages_during_long_model_operations() -> None:
    source = inspect.getsource(gateway.record_ws)

    assert "async def receive_pump()" in source
    assert "incoming_messages.put(await ws.receive_json())" in source
    assert "msg = incoming" in source

    launcher = (_REPO_ROOT / "start-dano.bat").read_text(encoding="utf-8")
    assert "--ws-max-queue 2048" in launcher


def test_frontend_automatically_retries_local_edit_after_server_version_conflict() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")
    helper_start = source.index("function retryFlowMutationAfterConflict")
    helper_end = source.index("function restoreAuthoritativeFlowSpec", helper_start)
    helper = source[helper_start:helper_end]

    assert 'messageData?.stage !== "flow_spec_conflict"' in helper
    assert "serverFingerprintRef.current" in helper
    assert "flowMutationQueueRef.current.unshift" in helper
    assert "flushFlowMutationQueue()" in helper
    error_start = source.index('else if (m.type === "error")')
    assert "if (retryFlowMutationAfterConflict(m)) return;" in source[error_start:error_start + 300]


def test_frontend_only_starts_flow_operation_on_connected_websocket() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")

    orchestrate_start = source.index("function orchestrateFlow()")
    orchestrate_end = source.index("function autoFixFlow()", orchestrate_start)
    orchestrate_source = source[orchestrate_start:orchestrate_end]
    assert 'connectionState !== "connected"' in orchestrate_source
    assert "reconnectedSessionNeedsCapture" in orchestrate_source
    assert "clearFlowOperation()" in orchestrate_source

    button_start = source.index('loading={orchestrateBusy || autoFixBusy}')
    button_source = source[button_start:button_start + 220]
    assert 'disabled={connectionState !== "connected"' in button_source
    assert "reconnectedSessionNeedsCapture" in button_source


def test_frontend_discards_unresumable_flow_operation_after_backend_restart() -> None:
    source = _PAGE_RECORDER.read_text(encoding="utf-8")
    reconnect_start = source.index("} else if (isReconnect && flowSpecRef.current) {")
    reconnect_end = source.index("} else {", reconnect_start)

    assert "clearFlowOperation()" in source[reconnect_start:reconnect_end]
