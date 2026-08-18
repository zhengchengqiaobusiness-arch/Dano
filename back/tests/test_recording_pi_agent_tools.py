"""Recording core uses Pi submissions and deterministic gates only."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest
import dano.agent_tools.tools as agent_tools_module

from dano.agent_tools.tools import (
    _recording_release_snapshot_matches,
    ToolError,
    ask_recording_operator,
    execute_recording_write_with_verify,
    perturb_recording_replay,
    get_recording_delta,
    get_recording_state,
    get_validation_report,
    publish_asset,
    submit_recording_plan,
    submit_recording_repair,
    verify_recording_dependency,
)
from dano.shared.enums import AssetType, ValidationStatus
from dano.execution.page import flow_spec as flow_module
from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestUsage,
    ensure_flow_version,
    flow_spec_fingerprint,
    prepare_flow_release_candidate,
)
from dano.execution.page.verification_log import record_verification
from dano.onboarding.recording_pi import RecordingPiSession
from dano.onboarding.page_onboard import run_request_onboarding


def _call_nodes(step_ids: list[str]) -> list[dict]:
    return [
        {"id": f"call_{index}", "type": "call", "step_id": step_id}
        for index, step_id in enumerate(step_ids)
    ]


def _spec() -> FlowSpec:
    spec = FlowSpec(
        flow_id="recording-test",
        title="提交申请",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            body_source='{"title":"demo"}',
            params=[ParamField(
                path="title",
                key="title",
                label="标题",
                value="demo",
                category="user_param",
                source_kind="user_input",
                exposed_to_user=True,
            )],
        )],
    )
    spec = ensure_flow_version(spec, "recorded", reason="test")
    fingerprint = flow_spec_fingerprint(spec)
    spec.meta = {
        **(spec.meta or {}),
        "release_candidate": {
            "protocol": "dano.recording_release.v1",
            "release_id": f"test-{fingerprint}",
            "flow_fingerprint": fingerprint,
        },
    }
    return spec


def test_flow_fingerprint_is_stable_after_frozen_snapshot_revalidation() -> None:
    spec = _spec()
    ref = flow_module.CapabilityRequestRef(
        request_id="request-submit",
        step_id="submit",
        method="POST",
        path="/api/submit",
    )
    # Old clients can round-trip this field as extra data. We keep it in
    # metadata so frozen/reloaded snapshots stay structurally consistent.
    ref.__pydantic_extra__ = {"pinned": True}
    spec.capabilities = [flow_module.FlowCapability(
        capability_id="submit-capability",
        name="submit",
        title="提交申请",
        request_refs=[ref],
        nodes=_call_nodes(["submit"]),
    )]

    frozen = FlowSpec.model_validate(spec.model_dump(mode="json", exclude_none=True))

    assert frozen.capabilities[0].request_refs[0].model_dump().get("pinned") is True
    assert flow_spec_fingerprint(spec) == flow_spec_fingerprint(frozen)


def test_recording_publish_snapshot_must_match_frozen_machine_candidate() -> None:
    spec = _spec()
    assert _recording_release_snapshot_matches(
        SimpleNamespace(current_flow_spec=lambda: spec),
        None,
    ) == (False, "录制发布草案不存在")
    snapshot = spec.model_dump(mode="json")
    draft = SimpleNamespace(body={"api_request": {"_release_snapshot": {
        "flow_fingerprint": flow_spec_fingerprint(spec),
        "flow_spec": snapshot,
    }}})
    session = SimpleNamespace(current_flow_spec=lambda: spec)

    assert _recording_release_snapshot_matches(session, draft) == (True, "ok")

    draft.body["api_request"]["_release_snapshot"]["flow_fingerprint"] = "changed"
    passed, reason = _recording_release_snapshot_matches(session, draft)
    assert passed is False
    assert "不一致" in reason


@pytest.mark.asyncio
async def test_recording_machine_validated_publish_does_not_require_review_runs(monkeypatch) -> None:
    spec = _spec()
    draft_id = uuid4()
    draft = SimpleNamespace(
        asset_draft_id=draft_id,
        asset_type=AssetType.PAGE_SCRIPT,
        body={"api_request": {"_release_snapshot": {
            "flow_fingerprint": flow_spec_fingerprint(spec),
            "flow_spec": spec.model_dump(mode="json"),
        }}},
        tenant="tenant-pi",
        subsystem="reimburse",
        asset_key="recorded_submit",
        content_hash="hash",
    )

    class DraftStore:
        async def verify_publishable(self, _draft_id, _validation_ids):
            return True, "ok"

        async def get_draft(self, _draft_id):
            return draft

        async def verify_reviewed(self, *_args):
            raise AssertionError("recording machine publish must not require review evidence")

    class Repository:
        async def create(self, _envelope):
            return SimpleNamespace(asset_id=uuid4(), version=1)

        async def set_status(self, _asset_id, _status):
            return None

    session = SimpleNamespace(current_flow_spec=lambda: spec)
    monkeypatch.setattr(agent_tools_module, "_ds", DraftStore())
    monkeypatch.setattr(agent_tools_module, "_repo", Repository())
    monkeypatch.setattr(agent_tools_module, "validate_asset_body", lambda *_args: None)
    monkeypatch.setattr(
        "dano.onboarding.recording_pi.active_recording_session",
        lambda run_id: session if run_id == "run-machine-publish" else None,
    )

    result = await publish_asset("run-machine-publish", {
        "asset_draft_id": str(draft_id),
        "validation_run_ids": [str(uuid4())],
        "review_run_ids": [],
        "recording_machine_validated": True,
    })

    assert result["published"] is True


@pytest.mark.asyncio
async def test_direct_recording_export_skips_validation_and_keeps_release_binding(monkeypatch) -> None:
    spec = _spec()
    draft_id = uuid4()
    draft = SimpleNamespace(
        asset_draft_id=draft_id,
        asset_type=AssetType.PAGE_SCRIPT,
        body={"api_request": {"_release_snapshot": {
            "flow_fingerprint": flow_spec_fingerprint(spec),
            "flow_spec": spec.model_dump(mode="json"),
        }}},
        tenant="tenant-pi",
        subsystem="reimburse",
        asset_key="recorded_submit",
        content_hash="hash",
    )
    created = []

    class DraftStore:
        async def verify_publishable(self, *_args):
            raise AssertionError("direct export must skip machine validation")

        async def get_draft(self, _draft_id):
            return draft

        async def verify_reviewed(self, *_args):
            raise AssertionError("direct recording export must not require model review")

    class Repository:
        async def create(self, envelope):  # noqa: ANN001
            created.append(envelope)
            return SimpleNamespace(asset_id=uuid4(), version=1)

        async def set_status(self, _asset_id, _status):
            return None

    session = SimpleNamespace(current_flow_spec=lambda: spec)
    monkeypatch.setattr(agent_tools_module, "_ds", DraftStore())
    monkeypatch.setattr(agent_tools_module, "_repo", Repository())
    monkeypatch.setattr(agent_tools_module, "validate_asset_body", lambda *_args: None)
    monkeypatch.setattr(
        "dano.onboarding.recording_pi.active_recording_session",
        lambda run_id: session if run_id == "run-direct-export" else None,
    )

    result = await publish_asset("run-direct-export", {
        "asset_draft_id": str(draft_id),
        "validation_run_ids": [],
        "review_run_ids": [],
        "recording_release_candidate": True,
        "recording_machine_validated": False,
        "recording_direct_export": True,
    })

    assert result["published"] is True
    assert created[0].validation_status == ValidationStatus.DRAFT
    assert created[0].confidence == 0.7


def test_flow_fingerprint_ignores_output_schema_property_insertion_order() -> None:
    """JSON object order must not change a frozen release contract."""

    def with_property_order(names: list[str]) -> FlowSpec:
        spec = _spec()
        spec.capabilities = [FlowCapability(
            capability_id="submit-capability",
            name="submit",
            title="提交申请",
            nodes=_call_nodes(["submit"]),
            output_schema={
                "type": "object",
                "properties": {name: {"type": "string"} for name in names},
            },
        )]
        return spec

    first = with_property_order(["code", "data", "msg"])
    second = with_property_order(["msg", "code", "data"])

    assert flow_spec_fingerprint(first) == flow_spec_fingerprint(second)


def test_recording_release_survives_jsonb_object_key_reordering() -> None:
    """PostgreSQL jsonb object order must not reorder explicit capability inputs."""

    spec = _spec()
    approval_fields = [
        flow_module.CapabilityField(
            field_id=f"input:{name}",
            scope="input",
            key=name,
            path=name,
            display_name=name,
            source_kind="user_input",
            category="user_param",
            exposed_to_caller=True,
            required=True,
        )
        for name in ("领导审批", "人力审批")
    ]
    spec.capabilities = [FlowCapability(
        capability_id="submit-capability",
        name="submit_request",
        title="提交申请",
        kind="submit",
        nodes=_call_nodes(["submit"]),
        inputs=[
            flow_module.CapabilityField(
                field_id="input:title",
                scope="input",
                key="title",
                path="title",
                display_name="标题",
                source_kind="user_input",
                category="user_param",
                exposed_to_caller=True,
            ),
            *approval_fields,
        ],
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "领导审批": {
                    "type": "array",
                    "x-dano-capability-owned": True,
                    "x-dano-operator-owned": True,
                },
                "人力审批": {
                    "type": "array",
                    "x-dano-capability-owned": True,
                    "x-dano-operator-owned": True,
                },
            },
            "required": ["领导审批", "人力审批"],
        },
    )]
    frozen, release = prepare_flow_release_candidate(spec)

    def jsonb_order(value):  # noqa: ANN001, ANN202 - emulate jsonb object ordering
        if isinstance(value, dict):
            return {key: jsonb_order(value[key]) for key in sorted(value)}
        if isinstance(value, list):
            return [jsonb_order(item) for item in value]
        return value

    persisted = jsonb_order(flow_module.flow_spec_release_payload(frozen))
    draft = SimpleNamespace(body={"api_request": {"_release_snapshot": {
        **release,
        "flow_spec": persisted,
    }}})

    assert _recording_release_snapshot_matches(
        SimpleNamespace(current_flow_spec=lambda: frozen),
        draft,
    ) == (True, "ok")
    assert [field.key for field in FlowSpec.model_validate(persisted).capabilities[0].inputs] == [
        "title", "领导审批", "人力审批",
    ]


def test_manual_edit_then_release_reviews_the_exact_persisted_snapshot() -> None:
    spec = _spec()
    spec.capabilities = [flow_module.FlowCapability(
        capability_id="submit-capability",
        name="submit_request",
        title="提交申请",
        kind="submit",
        nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        confirmed=True,
    )]
    edited = flow_module.apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "submit",
        "param_path": "title",
        "field": "key",
        "value": "申请标题",
    }])

    frozen, release = prepare_flow_release_candidate(edited)
    persisted = frozen.model_dump(mode="json", exclude_none=True)
    reconstructed = FlowSpec.model_validate(persisted)

    assert frozen.steps[0].params[0].key == "申请标题"
    assert flow_spec_fingerprint(frozen) == release["flow_fingerprint"]
    assert flow_spec_fingerprint(reconstructed) == release["flow_fingerprint"]


def test_release_fingerprint_treats_missing_and_explicit_none_evidence_as_equal() -> None:
    with_none = _spec()
    with_none.request_facts.requests = [RequestFact(
        request_id="request-submit",
        request_index=1,
        method="POST",
        path="/api/submit",
    )]
    with_none.request_facts.analysis = {
        "request-submit": flow_module.RequestAnalysis.model_validate({
            "request_id": "request-submit",
            "role": "business_write",
            "post_data": None,
            "response_json": None,
            "response_status": None,
        }),
    }
    without_none = with_none.model_copy(deep=True)
    without_none.request_facts.analysis = {
        "request-submit": flow_module.RequestAnalysis(
            request_id="request-submit",
            role="business_write",
        ),
    }

    assert flow_spec_fingerprint(with_none) == flow_spec_fingerprint(without_none)
    prepared, release = prepare_flow_release_candidate(with_none)
    reconstructed = FlowSpec.model_validate(
        flow_module.flow_spec_release_payload(prepared)
    )
    assert flow_spec_fingerprint(reconstructed) == release["flow_fingerprint"]


class _LiveEvidenceRecorder:
    def __init__(self):
        self.calls = {
            "requests": 0,
            "events": 0,
            "fields": 0,
            "enums": 0,
        }

    def captured_all_requests(self):
        self.calls["requests"] += 1
        return [{
            "request_id": "req-submit",
            "sequence": 1,
            "timestamp": 200,
            "method": "POST",
            "url": "https://example.test/leave/submit",
            "post_data": {"reason": "recorded"},
            "page_id": "page-1",
            "frame_id": "main",
            "trigger_action_id": "action-submit",
            "page_context": {"path": "/leave"},
        }]

    def recorded_page_events(self):
        self.calls["events"] += 1
        return [{
            "event_id": "event-reason",
            "action_id": "action-submit",
            "kind": "fill",
        }]

    def recorded_field_evidence(self):
        self.calls["fields"] += 1
        return [{
            "evidence_id": "field-reason",
            "event_id": "event-reason",
            "action_id": "action-submit",
            "label": "请假原因",
            "field_aliases": ["reason"],
            "control_kind": "textarea",
            "required": True,
            "required_observed": True,
            "page_id": "page-1",
            "frame_id": "main",
            "page_context": {"path": "/leave"},
        }]

    def recorded_page_enum_options(self):
        self.calls["enums"] += 1
        return {}


@pytest.mark.asyncio
async def test_live_page_evidence_is_available_before_field_ops_are_submitted() -> None:
    from dano.execution.page.recording_live import (
        apply_recording_agent_edit,
        merge_live_agent_state,
    )

    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "a" * 32,
    )
    session.bind_flow_spec(ensure_flow_version(FlowSpec(), "recorded", reason="test"))
    recorder = _LiveEvidenceRecorder()
    session.bind_live_recording(recorder)

    await session.refresh_live_evidence()
    current = session.current_flow_spec()
    [field] = current.request_facts.field_evidence

    assert current.request_facts.page_events[0]["event_id"] == "event-reason"
    assert field["binding_status"] == "bound"
    assert (field["request_id"], field["wire_path"]) == ("req-submit", "body.reason")

    result = apply_recording_agent_edit(current, {
        "op": "rename_field",
        "step_id": "req-submit",
        "path": "body.reason",
        "label": "请假原因",
        "reason": "页面字段标签",
        "evidence_refs": ["event-reason"],
    })

    assert result["status"] == "deferred"
    assert current.meta["recording_agent_ops"][0]["wire_path"] == "body.reason"

    required_result = apply_recording_agent_edit(current, {
        "op": "set_param_required",
        "step_id": "req-submit",
        "path": "body.reason",
        "required": True,
        "reason": "页面必填标记",
        "evidence_refs": ["event-reason"],
    })
    assert required_result["status"] == "deferred"

    finalized = current.model_copy(deep=True)
    finalized.meta = {
        key: value for key, value in (finalized.meta or {}).items()
        if key != "recording_agent_ops"
    }
    finalized.steps = [FlowStep(
        step_id="submit",
        method="POST",
        path="/leave/submit",
        source_meta={"request_id": "req-submit"},
        params=[ParamField(
            path="reason",
            key="reason",
            value="recorded",
            required=False,
        )],
    )]

    merged = merge_live_agent_state(current, finalized)
    field = merged.steps[0].params[0]
    assert (field.key, field.label) == ("请假原因", "请假原因")
    assert field.required is True
    assert field.source["required_state"] == "required"

    await session.refresh_live_evidence()
    assert recorder.calls == {
        "requests": 2,
        "events": 2,
        "fields": 2,
        "enums": 2,
    }


def test_recorded_request_sample_is_not_published_as_a_caller_default() -> None:
    param = ParamField(
        path="reason",
        key="请假原因",
        value="本次录制临时填写的内容",
        default_value=None,
        category="user_param",
        source_kind="user_input",
        exposed_to_user=True,
    )

    assert flow_module._schema_default_for_param(param) is flow_module._NO_SCHEMA_DEFAULT

    param.default_value = "页面真实初始值"
    assert flow_module._schema_default_for_param(param) == "页面真实初始值"


class _Session:
    def __init__(self, recording_id: str = "rec-1") -> None:
        self.recording_id = recording_id
        self.spec = _spec()
        self.last_submission_kind = ""
        self.received_submission = None
        self.received_delta = None

    def bind_flow_spec(self, spec):
        self.spec = spec.model_copy(deep=True)
        self.last_submission_kind = ""

    def current_flow_spec(self):
        return self.spec.model_copy(deep=True)

    async def get_recording_state(self):
        return flow_module.recording_agent_state(self.spec)

    async def get_recording_delta(self, since_seq=0, *, limit=25):
        self.received_delta = (since_seq, limit)
        return {"since_seq": since_seq, "next_seq": since_seq, "has_more": False, "requests": []}

    async def get_validation_report(self):
        return flow_module.recording_agent_validation(self.spec)

    async def apply_submission(self, submission, *, mode, base_flow_version):
        current = int((self.spec.meta or {}).get("current_version") or 0)
        if base_flow_version != current:
            raise RuntimeError(f"录制版本冲突: base={base_flow_version}, current={current}")
        self.received_submission = submission
        self.spec = await flow_module.apply_recording_agent_submission(
            self.spec, submission=submission, mode=mode,
        )
        self.last_submission_kind = mode
        return flow_module.recording_agent_validation(self.spec)

    async def accept_unchanged_plan(self, *, base_flow_version, warning):
        current = int((self.spec.meta or {}).get("current_version") or 0)
        if base_flow_version != current:
            raise RuntimeError("录制版本冲突")
        self.last_submission_kind = "plan"
        self.last_submission_warning = warning
        return {
            **flow_module.recording_agent_validation(self.spec),
            "accepted": True,
            "unchanged": True,
            "warning": warning,
        }

def _bind(monkeypatch, *, recording_id: str = "rec-1") -> _Session:
    session = _Session(recording_id)
    monkeypatch.setattr(
        "dano.onboarding.recording_pi.active_recording_session",
        lambda _run_id: session,
    )
    return session


def test_recording_delta_tool_validates_and_forwards_page_limit(monkeypatch):
    session = _bind(monkeypatch)

    result = asyncio.run(get_recording_delta("run-recording", {"since_seq": 7, "limit": 12}))
    assert result["since_seq"] == 7
    assert session.received_delta == (7, 12)

    with pytest.raises(ToolError, match="limit"):
        asyncio.run(get_recording_delta("run-recording", {"since_seq": 0, "limit": 51}))


@pytest.mark.asyncio
async def test_perturb_replay_rejects_request_id_keyed_overrides_before_execution(monkeypatch):
    called = False

    async def fake_replay(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    async def fake_auth(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("dano.execution.page.replay.perturb_replay", fake_replay)
    monkeypatch.setattr(agent_tools_module, "_recording_session", lambda *_args: object())
    monkeypatch.setattr(agent_tools_module, "_find_captured_requests", lambda *_args: [{}])
    monkeypatch.setattr(agent_tools_module, "_recording_auth_headers", fake_auth)

    with pytest.raises(ToolError, match="url_path|query|body|headers"):
        await perturb_recording_replay("run-recording", {
            "chain_request_ids": ["req_97"],
            "perturb": {"req_97": {"query": {"id": "changed"}}},
        })

    assert called is False


@pytest.mark.asyncio
async def test_verify_dependency_resolves_stale_link_without_retry_loop(monkeypatch):
    session = SimpleNamespace(
        current_flow_spec=lambda: FlowSpec(flow_id="recording-test", title="test"),
        _live_recorder=None,
    )

    async def fake_auth(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(agent_tools_module, "_recording_session", lambda *_args: session)
    monkeypatch.setattr(agent_tools_module, "_captured_recording_requests", lambda *_args: [])
    monkeypatch.setattr(agent_tools_module, "_recording_auth_headers", fake_auth)

    result = await verify_recording_dependency("run-recording", {"link_id": "stale-link"})

    assert result == {
        "ok": False,
        "status": "stale_link",
        "link_id": "stale-link",
        "refresh_required": True,
        "next_tool": "get_validation_report",
        "verification_ids": [],
    }


@pytest.mark.asyncio
async def test_write_verification_executes_each_step_only_once(monkeypatch):
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "d" * 32,
    )
    spec = _spec()
    spec.steps[0].source_meta = {"request_id": "req-write"}
    session.bind_flow_spec(spec)
    calls = 0

    async def fake_execute(*_args, **kwargs):
        nonlocal calls
        calls += 1
        verification_id = record_verification(
            kind="write_execute",
            subject={
                "write_step_id": kwargs["write_step_id"],
                "verify_request_id": "req-verify",
                "assertion": kwargs["assertion"],
            },
            status="passed",
            evidence={"passed": True, "write": {}, "verify": {}, "assertion": {}},
        )
        return {
            "ok": True,
            "verification_id": verification_id,
            "verification_ids": [verification_id],
        }

    async def fake_auth(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("dano.execution.page.replay.execute_write_with_verify", fake_execute)
    monkeypatch.setattr(agent_tools_module, "_recording_session", lambda *_args: session)
    monkeypatch.setattr(
        agent_tools_module,
        "_find_captured_requests",
        lambda *_args: [
            {"request_id": "req-write", "method": "POST"},
            {"request_id": "req-verify", "method": "GET"},
        ],
    )
    monkeypatch.setattr(agent_tools_module, "_recording_auth_headers", fake_auth)
    params = {
        "write_step_id": "submit",
        "inputs": {"title": "demo"},
        "verify_request_id": "req-verify",
        "assertion": {"path": "data.id", "operator": "exists"},
    }

    first = await execute_recording_write_with_verify("run-recording", params)
    second = await execute_recording_write_with_verify("run-recording", params)

    assert calls == 1
    assert second["duplicate"] is True
    assert second["verification_id"] == first["verification_id"]


@pytest.mark.asyncio
async def test_successful_write_can_retry_readback_without_repeating_write(monkeypatch):
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "9" * 32,
    )
    spec = _spec()
    spec.steps[0].source_meta = {"request_id": "req-write"}
    session.bind_flow_spec(spec)
    write_calls = 0
    readback_calls = 0

    async def fake_execute(*_args, **kwargs):
        nonlocal write_calls
        write_calls += 1
        verification_id = record_verification(
            kind="write_execute",
            subject={
                "write_step_id": kwargs["write_step_id"],
                "write_request_id": "req-write",
                "verify_request_id": "req-filtered",
                "assertion": kwargs["assertion"],
            },
            status="failed",
            evidence={
                "passed": False,
                "write": {
                    "ok": True,
                    "application_ok": True,
                    "verification_status": "passed",
                    "response": {"data": "created-id"},
                },
                "verify": {"response": {"data": {"list": []}}},
                "assertion": {"passed": False},
            },
        )
        return {
            "ok": False,
            "write": {
                "ok": True,
                "application_ok": True,
                "verification_status": "passed",
                "response": {"data": "created-id"},
            },
            "verify": {"response": {"data": {"list": []}}},
            "assertion": {"passed": False},
            "verification_id": verification_id,
            "verification_ids": [verification_id],
        }

    async def fake_verify_existing(*_args, **kwargs):
        nonlocal readback_calls
        readback_calls += 1
        verification_id = record_verification(
            kind="write_execute",
            subject={
                "write_step_id": kwargs["write_step_id"],
                "write_request_id": "req-write",
                "verify_request_id": "req-unfiltered",
                "assertion": kwargs["assertion"],
            },
            status="passed",
            evidence={
                "passed": True,
                "write": kwargs["previous_write"],
                "verify": {"response": {"data": {"list": [{"id": "created-id"}]}}},
                "assertion": {"passed": True},
            },
        )
        return {
            "ok": True,
            "write": kwargs["previous_write"],
            "verify": {"response": {"data": {"list": [{"id": "created-id"}]}}},
            "assertion": {"passed": True},
            "verification_id": verification_id,
            "verification_ids": [verification_id],
        }

    async def fake_auth(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("dano.execution.page.replay.execute_write_with_verify", fake_execute)
    monkeypatch.setattr(
        "dano.execution.page.replay.verify_existing_write",
        fake_verify_existing,
        raising=False,
    )
    monkeypatch.setattr(agent_tools_module, "_recording_session", lambda *_args: session)
    monkeypatch.setattr(
        agent_tools_module,
        "_find_captured_requests",
        lambda *_args: [
            {"request_id": "req-write", "method": "POST"},
            {"request_id": "req-unfiltered", "method": "GET"},
        ],
    )
    monkeypatch.setattr(agent_tools_module, "_recording_auth_headers", fake_auth)

    first = await execute_recording_write_with_verify("run-recording", {
        "write_step_id": "submit",
        "verify_request_id": "req-filtered",
        "assertion": {"path": "data.list", "verify_records_min_count": 1},
    })
    second = await execute_recording_write_with_verify("run-recording", {
        "write_step_id": "submit",
        "verify_request_id": "req-unfiltered",
        "assertion": {"path": "data.list.0.id", "operator": "equals", "value": "created-id"},
    })

    assert first["ok"] is False
    assert second["ok"] is True
    assert second["write_executed"] is False
    assert second["readback_retried"] is True
    assert write_calls == 1
    assert readback_calls == 1


@pytest.mark.asyncio
async def test_write_verification_defaults_to_captured_request_body(monkeypatch):
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "b" * 32,
    )
    spec = _spec()
    spec.steps[0].source_meta = {"request_id": "req-write"}
    session.bind_flow_spec(spec)
    observed_inputs = None

    async def fake_execute(*_args, **kwargs):
        nonlocal observed_inputs
        observed_inputs = kwargs["inputs"]
        verification_id = record_verification(
            kind="write_execute",
            subject={"write_step_id": kwargs["write_step_id"]},
            status="passed",
            evidence={"passed": True},
        )
        return {
            "ok": True,
            "verification_id": verification_id,
            "verification_ids": [verification_id],
        }

    async def fake_auth(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("dano.execution.page.replay.execute_write_with_verify", fake_execute)
    monkeypatch.setattr(agent_tools_module, "_recording_session", lambda *_args: session)
    monkeypatch.setattr(
        agent_tools_module,
        "_find_captured_requests",
        lambda *_args: [
            {"request_id": "req-write", "method": "POST", "post_data": '{"title":"recorded"}'},
            {"request_id": "req-verify", "method": "GET"},
        ],
    )
    monkeypatch.setattr(agent_tools_module, "_recording_auth_headers", fake_auth)

    result = await execute_recording_write_with_verify("run-recording", {
        "write_step_id": "submit",
        "verify_request_id": "req-verify",
        "assertion": {"path": "data.id", "operator": "exists"},
    })

    assert result["ok"] is True
    assert observed_inputs == {}


@pytest.mark.asyncio
async def test_failed_write_verification_is_not_reexecuted(monkeypatch):
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "e" * 32,
    )
    spec = _spec()
    spec.steps[0].source_meta = {"request_id": "req-write"}
    session.bind_flow_spec(spec)
    calls = 0

    async def fake_execute(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("read-back failed after write")

    async def fake_auth(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("dano.execution.page.replay.execute_write_with_verify", fake_execute)
    monkeypatch.setattr(agent_tools_module, "_recording_session", lambda *_args: session)
    monkeypatch.setattr(
        agent_tools_module,
        "_find_captured_requests",
        lambda *_args: [
            {"request_id": "req-write", "method": "POST"},
            {"request_id": "req-verify", "method": "GET"},
        ],
    )
    monkeypatch.setattr(agent_tools_module, "_recording_auth_headers", fake_auth)
    params = {
        "write_step_id": "submit",
        "inputs": {"title": "demo"},
        "verify_request_id": "req-verify",
        "assertion": {"path": "data.id", "operator": "exists"},
    }

    with pytest.raises(RuntimeError, match="read-back failed"):
        await execute_recording_write_with_verify("run-recording", params)
    with pytest.raises(ToolError, match="禁止重复执行"):
        await execute_recording_write_with_verify("run-recording", params)

    assert calls == 1


@pytest.mark.asyncio
async def test_business_rejection_before_write_can_retry_with_corrected_inputs(monkeypatch):
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "a" * 32,
    )
    spec = _spec()
    spec.steps[0].source_meta = {"request_id": "req-write"}
    session.bind_flow_spec(spec)
    calls = 0

    async def fake_execute(*_args, **kwargs):
        nonlocal calls
        calls += 1
        passed = calls == 2
        verification_id = record_verification(
            kind="write_execute",
            subject={"write_step_id": kwargs["write_step_id"]},
            status="passed" if passed else "failed",
            evidence={"passed": passed},
        )
        return {
            "ok": passed,
            "write": {
                "ok": passed,
                "application_ok": passed,
                "verification_status": "passed" if passed else "failed",
            },
            "verify": {} if passed else None,
            "assertion": {} if passed else None,
            "verification_id": verification_id,
            "verification_ids": [verification_id],
        }

    async def fake_auth(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("dano.execution.page.replay.execute_write_with_verify", fake_execute)
    monkeypatch.setattr(agent_tools_module, "_recording_session", lambda *_args: session)
    monkeypatch.setattr(
        agent_tools_module,
        "_find_captured_requests",
        lambda *_args: [
            {"request_id": "req-write", "method": "POST"},
            {"request_id": "req-verify", "method": "GET"},
        ],
    )
    monkeypatch.setattr(agent_tools_module, "_recording_auth_headers", fake_auth)
    params = {
        "write_step_id": "submit",
        "inputs": {"title": "corrected"},
        "verify_request_id": "req-verify",
        "assertion": {"path": "data.id", "operator": "exists"},
    }

    first = await execute_recording_write_with_verify("run-recording", params)
    second = await execute_recording_write_with_verify("run-recording", params)
    third = await execute_recording_write_with_verify("run-recording", params)

    assert first["ok"] is False
    assert second["ok"] is True
    assert second["duplicate"] is False
    assert third["duplicate"] is True
    assert calls == 2


@pytest.mark.asyncio
async def test_invalid_assertion_does_not_consume_write_verification(monkeypatch):
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "f" * 32,
    )
    spec = _spec()
    spec.steps[0].source_meta = {"request_id": "req-write"}
    session.bind_flow_spec(spec)
    calls = 0

    async def fake_execute(*_args, **kwargs):
        nonlocal calls
        calls += 1
        verification_id = record_verification(
            kind="write_execute",
            subject={"write_step_id": kwargs["write_step_id"]},
            status="passed",
            evidence={"passed": True},
        )
        return {
            "ok": True,
            "verification_id": verification_id,
            "verification_ids": [verification_id],
        }

    async def fake_auth(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("dano.execution.page.replay.execute_write_with_verify", fake_execute)
    monkeypatch.setattr(agent_tools_module, "_recording_session", lambda *_args: session)
    monkeypatch.setattr(
        agent_tools_module,
        "_find_captured_requests",
        lambda *_args: [
            {"request_id": "req-write", "method": "POST"},
            {"request_id": "req-verify", "method": "GET"},
        ],
    )
    monkeypatch.setattr(agent_tools_module, "_recording_auth_headers", fake_auth)
    params = {
        "write_step_id": "submit",
        "inputs": {"title": "demo"},
        "verify_request_id": "req-verify",
        "assertion": {
            "collection_path": "data.list",
            "verify_records_min_count": 1,
        },
    }

    with pytest.raises(ToolError, match="collection assertion requires a non-empty where"):
        await execute_recording_write_with_verify("run-recording", params)
    assert (session.current_flow_spec().meta or {}).get("write_verification_attempts") is None

    params["assertion"] = {"verify_records_min_count": 1}
    result = await execute_recording_write_with_verify("run-recording", params)

    assert result["ok"] is True
    assert calls == 1


def test_recording_core_has_no_direct_llm_conversation_or_cache_path():
    source = inspect.getsource(flow_module)
    for forbidden in (
        "class _SemanticConversation",
        "complete_json_messages(",
        "llm_client.complete_json(",
        '"recording_pi_loop"',
        '"application_cache_hit"',
        '"model_cached_tokens"',
    ):
        assert forbidden not in source
    signature = inspect.signature(flow_module.apply_recording_agent_submission)
    assert "llm_client" not in signature.parameters
    assert "model" not in signature.parameters
    assert "submission" in signature.parameters


def test_recording_agent_state_omits_raw_dom_mutation_noise() -> None:
    spec = _spec()
    spec.request_facts.page_events = [
        {
            "event_id": "action-1",
            "kind": "action",
            "op": "fill",
            "field": "申请标题",
            "required": True,
        },
        {
            "event_id": "dom-1",
            "kind": "dom_effect",
            "changes": [
                {"sequence": index, "type": "childList", "added": 1, "removed": 1}
                for index in range(100)
            ],
        },
    ]

    state = flow_module.recording_agent_state(spec)

    assert [item["kind"] for item in state["facts"]["page_events"]] == ["action"]


def test_pi_tools_read_and_apply_plan_without_changing_request_facts(monkeypatch):
    session = _bind(monkeypatch)
    state = asyncio.run(get_recording_state("run-recording", {"recording_id": "rec-1"}))
    assert state["flow_version"] == 1
    before_facts = session.spec.request_facts.model_dump(mode="json")

    result = asyncio.run(submit_recording_plan("run-recording", {
        "recording_id": "rec-1",
        "base_flow_version": 1,
        "plan": {
            "semantic_plan": {
                "business_understanding": {"intent": "提交申请"},
                "capabilities": [{
                    "name": "submit_application", "title": "提交申请",
                    "kind": "submit", "anchor_step_id": "submit",
                    "request_refs": [{"step_id": "submit", "usage": "execute"}],
                }],
                "unresolved_items": [],
            },
            "ops": [],
        },
    }))
    assert result["flow_version"] > 1
    assert "report" not in result
    assert "repair_context" not in result
    assert result["capability_plan_complete"] is True
    assert result["submission_complete"] is True
    assert session.spec.request_facts.model_dump(mode="json") == before_facts
    validation = asyncio.run(get_validation_report("run-recording", {"recording_id": "rec-1"}))
    assert validation["flow_version"] == result["flow_version"]
    assert "report" in validation and "repair_context" in validation


def test_pi_plan_applies_live_param_source_operation(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-live-op")
    session.spec.steps[0].source_meta = {"request_id": "req-submit"}
    session.spec.request_facts.requests = [RequestFact(
        request_id="req-submit", request_index=1, method="POST", path="/api/submit",
    )]

    result = asyncio.run(submit_recording_plan("run-live-op", {
        "recording_id": "rec-live-op",
        "base_flow_version": 1,
        "plan": {
            "semantic_plan": {
                "business_understanding": {},
                "capabilities": [],
                "unresolved_items": [],
            },
            "ops": [{
                "op": "set_param_source",
                "request_id": "req-submit",
                "wire_path": "body.title",
                "source_kind": "context",
                "context_key": "page.title",
                "reason": "该值由当前页面上下文提供",
                "evidence_refs": ["req-submit"],
            }],
        },
    }))

    assert result["flow_version"] > 1
    assert result["flow_version"] == int(session.spec.meta["current_version"])
    param = next(item for item in session.spec.steps[0].params if item.path == "title")
    assert param.source_kind == "page_context"
    assert param.exposed_to_user is False
    assert result["op_results"] == [{
        "index": 0,
        "op": "set_param_source",
        "status": "applied",
        "requested_target": {
            "request_id": "req-submit",
            "wire_path": "body.title",
        },
        "resolved_target": {
            "step_id": "submit",
            "stored_path": "title",
            "wire_path": "body.title",
        },
        "reason": "",
        "flow_version_before": 1,
        "flow_version_after": result["flow_version"],
    }]
    assert result["all_applied"] is True
    assert result["must_retry"] == []
    assert result["unresolved_targets"] == []


def test_pi_plan_reports_rejected_field_operation_instead_of_silent_success(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-op-result")
    session.spec.steps[0].source_meta = {"request_id": "req-submit"}
    session.spec.request_facts.requests = [RequestFact(
        request_id="req-submit", request_index=1, method="POST", path="/api/submit",
    )]

    result = asyncio.run(submit_recording_plan("run-op-result", {
        "base_flow_version": 1,
        "plan": {
            "semantic_plan": {},
            "ops": [
                {
                    "op": "set_param_source",
                    "request_id": "req-missing",
                    "wire_path": "body.title",
                    "source_kind": "context",
                    "context_key": "page.title",
                    "reason": "错误的请求标识",
                },
                {
                    "op": "set_param_source",
                    "request_id": "req-submit",
                    "wire_path": "body.title",
                    "source_kind": "context",
                    "context_key": "page.title",
                    "reason": "页面上下文自动提供",
                },
            ],
        },
    }))

    assert [item["status"] for item in result["op_results"]] == ["rejected", "applied"]
    assert "target not found" in result["op_results"][0]["reason"]
    assert result["all_applied"] is False
    assert result["must_retry"] == [0]
    assert result["unresolved_targets"] == [{
        "request_id": "req-missing",
        "wire_path": "body.title",
    }]
    assert not any(
        item.get("kind") == "param_source" and "req-missing" in item.get("text", "")
        for item in (session.spec.meta or {}).get("agent_insights") or []
    )
    assert session.spec.steps[0].params[0].source_kind == "page_context"


def test_pi_repair_returns_the_same_complete_operation_results(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-repair-results")

    result = asyncio.run(submit_recording_repair("run-repair-results", {
        "base_flow_version": 1,
        "operations": [{
            "op": "add_pitfall",
            "text": "提交前必须先读取当前流程定义",
            "evidence_ref": "request-submit",
        }],
    }))

    assert result["all_applied"] is True
    assert result["must_retry"] == []
    assert result["unresolved_targets"] == []
    assert result["op_results"] == [{
        "index": 0,
        "op": "add_pitfall",
        "status": "applied",
        "requested_target": {},
        "resolved_target": {},
        "reason": "",
        "flow_version_before": 1,
        "flow_version_after": result["flow_version"],
    }]
    assert any(
        item.get("text") == "提交前必须先读取当前流程定义"
        for item in (session.spec.meta or {}).get("pitfalls") or []
    )



def test_pi_plan_rejects_legacy_flow_spec_wrapper(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-legacy-plan")

    with pytest.raises(ToolError, match="禁止提交 flow_spec"):
        asyncio.run(submit_recording_plan("run-legacy-plan", {
            "recording_id": "rec-legacy-plan",
            "base_flow_version": 1,
            "plan": {"flow_spec": {
                "title": "截图识别的申请流程",
                "capabilities": [{"title": "截图能力"}],
            }},
        }))

    assert int((session.spec.meta or {}).get("current_version") or 0) == 1


def test_pi_plan_rejects_observed_legacy_model_variant(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-model-variant")

    with pytest.raises(ToolError, match="禁止或未知字段"):
        asyncio.run(submit_recording_plan("run-model-variant", {
            "recording_id": "rec-model-variant",
            "base_flow_version": 1,
            "plan": {
                "semantic_plan": {
                    "business_understanding": {"summary": "提交申请并返回处理结果"},
                    "field_semantics": [{"step_id": "submit", "wire_path": "body.title"}],
                },
                "ops": [],
            },
        }))
    assert int((session.spec.meta or {}).get("current_version") or 0) == 1


def test_typed_recording_plan_accepts_declared_session_key() -> None:
    agent_tools_module._validate_typed_recording_operations([
        {
            "op": "set_param_source",
            "step_id": "submit",
            "wire_path": "body.operatorId",
            "source_kind": "session",
            "session_key": "current_user.id",
            "reason": "登录用户身份由会话提供",
            "evidence_refs": ["req-login"],
        }
    ], label="plan.ops")



def test_pi_plan_allows_backend_to_refresh_derived_request_usage(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-derived-usage")
    session.spec.steps[0].source_meta = {"request_id": "request-1"}
    session.spec.request_facts.requests = [RequestFact(
        request_id="request-1",
        request_index=0,
        method="POST",
        url="https://example.invalid/api/submit",
        path="/api/submit",
        post_data={"title": "demo"},
    )]
    session.spec.request_facts.usage = {
        "request-1": RequestUsage(request_id="request-1", state="captured"),
    }
    immutable_request_before = session.spec.request_facts.requests[0].model_dump(
        mode="json",
        include=set(RequestFact.model_fields),
    )

    result = asyncio.run(submit_recording_plan("run-derived-usage", {
        "recording_id": "rec-derived-usage",
        "base_flow_version": 1,
        "plan": {
            "semantic_plan": {
                "business_understanding": {},
                "capabilities": [],
                "unresolved_items": [],
            },
            "ops": [],
        },
    }))

    assert result["flow_version"] > 1
    assert session.spec.request_facts.requests[0].model_dump(
        mode="json",
        include=set(RequestFact.model_fields),
    ) == immutable_request_before
    usage = session.spec.request_facts.usage["request-1"]
    assert usage.state == "materialized"
    assert usage.materialized_step_id == "submit"


def test_pi_repair_rejects_stale_version_and_non_whitelisted_operation(monkeypatch):
    _bind(monkeypatch, recording_id="rec-repair")
    with pytest.raises(ToolError, match="版本冲突"):
        asyncio.run(submit_recording_repair("run-repair", {
            "recording_id": "rec-repair",
            "base_flow_version": 0,
            "operations": [],
        }))
    with pytest.raises(ToolError, match="不允许|not allowed|确定性准入|强类型契约"):
        asyncio.run(submit_recording_repair("run-repair", {
            "recording_id": "rec-repair",
            "base_flow_version": 1,
            "operations": [{"op": "replace_request_facts", "requests": []}],
        }))


def test_pi_tools_reject_unknown_params_and_bool_version(monkeypatch):
    _bind(monkeypatch, recording_id="rec-strict")
    with pytest.raises(ToolError, match="未知参数"):
        asyncio.run(get_recording_state("run-strict", {
            "recording_id": "rec-strict", "messages": [],
        }))
    with pytest.raises(ToolError, match="base_flow_version 必须是整数"):
        asyncio.run(submit_recording_repair("run-strict", {
            "recording_id": "rec-strict",
            "base_flow_version": True,
            "operations": [],
        }))
@pytest.mark.parametrize("mode", ["plan", "repair"])
def test_fact_violation_rolls_back_entire_recording_session(monkeypatch, mode):
    session = _bind(monkeypatch, recording_id=f"rec-atomic-{mode}")
    before_spec = session.spec.model_dump(mode="json")
    session.last_submission_kind = "checkpoint"

    async def _corrupt(_submission, *, mode, base_flow_version):
        assert base_flow_version == 1
        session.spec.request_facts.option_sources.append({"tampered": mode})
        session.spec.title = "polluted"
        session.last_submission_kind = mode
        return {"flow_version": 999}

    session.apply_submission = _corrupt
    params = {
        "recording_id": f"rec-atomic-{mode}",
        "base_flow_version": 1,
    }
    if mode == "plan":
        params["plan"] = {
            "semantic_plan": {
                "business_understanding": {},
                "capabilities": [],
                "unresolved_items": [],
            },
            "ops": [],
        }
        call = submit_recording_plan
    else:
        params["operations"] = []
        call = submit_recording_repair
    with pytest.raises(ToolError, match="不得修改原始 request facts"):
        asyncio.run(call(f"run-atomic-{mode}", params))
    assert session.spec.model_dump(mode="json") == before_spec
    assert session.last_submission_kind == "checkpoint"


@pytest.mark.parametrize("mode", ["plan", "repair"])
def test_server_side_field_evidence_rebinding_is_not_a_fact_violation(monkeypatch, mode):
    """sync_flow_spec_models deliberately re-evaluates unresolved DOM bindings
    against saved bodies; that server-derived rewrite must not reject the
    submission as if the model had tampered with raw request facts."""
    session = _bind(monkeypatch, recording_id=f"rec-rebind-{mode}")
    session.spec.request_facts.field_evidence = [
        {"evidence_id": "ev-1", "binding_status": "unbound", "wire_path": ""},
    ]

    async def _rebind(_submission, *, mode, base_flow_version):
        assert base_flow_version == 1
        session.spec.request_facts.field_evidence = [
            {"evidence_id": "ev-1", "binding_status": "bound", "wire_path": "body.reason"},
        ]
        session.last_submission_kind = mode
        return {"flow_version": 2, "op_results": [], "all_applied": True, "must_retry": []}

    session.apply_submission = _rebind
    params = {
        "recording_id": f"rec-rebind-{mode}",
        "base_flow_version": 1,
    }
    if mode == "plan":
        params["plan"] = {
            "semantic_plan": {
                "business_understanding": {},
                "capabilities": [],
                "unresolved_items": [],
            },
            "ops": [],
        }
        call = submit_recording_plan
    else:
        params["operations"] = []
        call = submit_recording_repair
    result = asyncio.run(call(f"run-rebind-{mode}", params))
    assert result["all_applied"] is True
    assert session.spec.request_facts.field_evidence[0]["binding_status"] == "bound"


@pytest.mark.parametrize(
    ("method", "path", "param_name"),
    [
        ("POST", "/api/submit", "reason"),
        ("DELETE", "/admin-api/bpm/process-instance/cancel-by-start-user", "reason"),
        ("DELETE", "/admin-api/bpm/process-instance/cancel-by-start-user", "请输入撤回原因"),
    ],
    ids=["ordinary-submit", "recorded-withdraw", "recorded-advisory-placeholder"],
)
def test_page_onboard_active_recording_bypasses_board_precheck_and_model_helpers(
    monkeypatch, method, path, param_name,
):
    from dano.agent_tools import tools as tool_module

    calls: list[str] = []

    async def _save(_run_id, _params):
        calls.append("save")
        return {"asset_draft_id": str(uuid4())}

    async def _self_check(_run_id, _params):
        calls.append("self_check")
        return {
            "passed": True,
            "mode": "self_check",
            "structured_output": {},
            "validation_run_ids": [str(uuid4())],
        }

    async def _review(*_args, **_kwargs):
        raise AssertionError("recording publish must not run a final model review")

    async def _publish(_run_id, params):
        calls.append("publish")
        assert params["recording_machine_validated"] is True
        assert params["review_run_ids"] == []
        return {"published": True, "asset_id": str(uuid4()), "version": 1}

    async def _forbidden_auto_goal(*_args, **_kwargs):
        raise AssertionError("active recording must not call ReviewBoard goal helper")

    recording_session = object()
    monkeypatch.setattr(
        "dano.onboarding.recording_pi.active_recording_session",
        lambda run_id: recording_session if run_id == "run-pi-publish" else None,
    )
    monkeypatch.setattr("dano.onboarding.page_onboard._auto_goal", _forbidden_auto_goal)
    monkeypatch.setattr(tool_module, "_review_board", None)
    monkeypatch.setattr(tool_module, "_fix_proposer", None)
    monkeypatch.setattr(tool_module, "save_draft", _save)
    monkeypatch.setattr(tool_module, "self_check_recording", _self_check)
    monkeypatch.setattr(tool_module, "request_review", _review)
    monkeypatch.setattr(tool_module, "publish_asset", _publish)

    result = asyncio.run(run_request_onboarding(
        tenant="tenant-pi",
        subsystem="reimburse",
        action="recorded_submit",
        title="提交申请",
        api_request={
            "method": method,
            "url": f"https://example.invalid{path}",
            "path": path,
            "body_template": {param_name: "{{" + param_name + "}}"},
            "params": [param_name],
            "field_types": {param_name: "string"},
            "success_rule": {"field": "code", "ok_values": [0]},
        },
        sample_inputs={param_name: "demo"},
        required=[param_name],
        run_id="run-pi-publish",
        allow_repair=True,
        recording_pi_required=True,
    ))
    assert result["ok"] is True
    assert calls == ["save", "self_check", "publish"]
    if method == "DELETE":
        assert result["stage"] == "publish"
        assert result["status"] != "rejected"
        assert result["request_role"]["semanticRole"] == "destructive"
    if param_name.startswith("请输入"):
        assert any("占位" in warning for warning in result.get("warnings") or [])


def test_page_onboard_direct_export_skips_compile_and_machine_validation(monkeypatch):
    from dano.agent_tools import tools as tool_module

    calls: list[str] = []

    async def _save(_run_id, _params):
        calls.append("save")
        return {"asset_draft_id": str(uuid4())}

    async def _publish(_run_id, params):
        calls.append("publish")
        assert params["recording_direct_export"] is True
        assert params["recording_machine_validated"] is False
        assert params["validation_run_ids"] == []
        return {"published": True, "asset_id": str(uuid4()), "version": 1}

    async def _forbidden(*_args, **_kwargs):
        raise AssertionError("direct export must not compile, verify, review, or repair")

    recording_session = object()
    monkeypatch.setattr(
        "dano.onboarding.recording_pi.active_recording_session",
        lambda run_id: recording_session if run_id == "run-direct-page-export" else None,
    )
    monkeypatch.setattr(tool_module, "save_draft", _save)
    monkeypatch.setattr(tool_module, "publish_asset", _publish)
    monkeypatch.setattr(tool_module, "self_check_recording", _forbidden)
    monkeypatch.setattr(tool_module, "request_review", _forbidden)

    result = asyncio.run(run_request_onboarding(
        tenant="tenant-pi",
        subsystem="reimburse",
        action="recorded_submit",
        api_request=_recording_api_request(),
        sample_inputs={"reason": "demo"},
        required=["reason"],
        run_id="run-direct-page-export",
        recording_pi_required=True,
        recording_machine_validated=False,
        direct_recording_export=True,
    ))

    assert result["ok"] is True
    assert result["status"] == "published"
    assert calls == ["save", "publish"]


def test_page_onboard_surfaces_nested_workflow_self_check_failure(monkeypatch):
    from dano.agent_tools import tools as tool_module

    session = object()

    async def _save(_run_id, _params):
        return {"asset_draft_id": str(uuid4())}

    async def _self_check(_run_id, _params):
        violation = "第3步:参数 `审批人` 进不了最终请求体"
        return {
            "passed": False,
            "mode": "self_check",
            "structured_output": {
                "ok": False,
                "failed_step": 2,
                "step_result": {"ok": False, "self_check": [violation]},
                "step_results": [
                    {"ok": True, "self_check": []},
                    {"ok": True, "self_check": []},
                    {"ok": False, "self_check": [violation]},
                ],
            },
            "validation_run_ids": [str(uuid4())],
        }

    monkeypatch.setattr(
        "dano.onboarding.recording_pi.active_recording_session",
        lambda run_id: session if run_id == "run-nested-self-check" else None,
    )
    monkeypatch.setattr(tool_module, "save_draft", _save)
    monkeypatch.setattr(tool_module, "self_check_recording", _self_check)

    result = asyncio.run(run_request_onboarding(
        tenant="tenant-pi",
        subsystem="reimburse",
        action="recorded_submit",
        api_request={
            "steps": [{
                "method": "POST",
                "url": "https://example.invalid/api/submit",
                "path": "/api/submit",
                "body_template": {"审批人": "{{审批人}}"},
                "params": ["审批人"],
                "field_types": {"审批人": "string"},
            }],
            "params": ["审批人"],
            "success_rule": {"field": "code", "ok_values": [0]},
        },
        sample_inputs={"审批人": "demo"},
        required=["审批人"],
        run_id="run-nested-self-check",
        recording_pi_required=True,
    ))

    assert result["ok"] is False
    assert result["stage"] == "validate"
    assert result["clarifications"] == ["第3步:参数 `审批人` 进不了最终请求体"]
    assert "参数没全填上" not in result["reason"]


def _recording_api_request() -> dict:
    return {
        "method": "POST",
        "url": "https://example.invalid/api/submit",
        "path": "/api/submit",
        "body_template": {"reason": "{{reason}}"},
        "params": ["reason"],
        "field_types": {"reason": "string"},
        "success_rule": {"field": "code", "ok_values": [0]},
    }


def test_page_onboard_required_recording_session_missing_fails_before_any_model(monkeypatch):
    from dano.agent_tools import tools as tool_module

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("required recording path must fail before model/tool work")

    monkeypatch.setattr("dano.onboarding.recording_pi.active_recording_session", lambda _run_id: None)
    monkeypatch.setattr("dano.onboarding.page_onboard._auto_goal", forbidden)
    monkeypatch.setattr(tool_module, "save_draft", forbidden)
    monkeypatch.setattr(tool_module, "request_review", forbidden)

    with pytest.raises(RuntimeError, match="要求 Pi AgentSession"):
        asyncio.run(run_request_onboarding(
            tenant="tenant-pi",
            subsystem="reimburse",
            action="recorded_submit",
            api_request=_recording_api_request(),
            run_id="run-missing-pi",
            recording_pi_required=True,
        ))


def test_page_onboard_required_recording_session_loss_never_falls_back(monkeypatch):
    from dano.agent_tools import tools as tool_module

    session = object()
    state = {"session": session}
    calls: list[str] = []

    async def save_then_drop(_run_id, _params):
        calls.append("save")
        state["session"] = None
        return {"asset_draft_id": str(uuid4())}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("lost recording session must not call model/review/repair")

    class ForbiddenBoard:
        async def review(self, **_kwargs):
            raise AssertionError("lost recording session must not call ReviewBoard")

    monkeypatch.setattr(
        "dano.onboarding.recording_pi.active_recording_session",
        lambda _run_id: state["session"],
    )
    monkeypatch.setattr("dano.onboarding.page_onboard._auto_goal", forbidden)
    monkeypatch.setattr(tool_module, "_review_board", ForbiddenBoard())
    monkeypatch.setattr(tool_module, "_fix_proposer", forbidden)
    monkeypatch.setattr(tool_module, "save_draft", save_then_drop)
    monkeypatch.setattr(tool_module, "self_check_recording", forbidden)
    monkeypatch.setattr(tool_module, "request_review", forbidden)

    with pytest.raises(RuntimeError, match="已丢失或被替换"):
        asyncio.run(run_request_onboarding(
            tenant="tenant-pi",
            subsystem="reimburse",
            action="recorded_submit",
            api_request=_recording_api_request(),
            run_id="run-lost-pi",
            allow_repair=True,
            recording_pi_required=True,
        ))
    assert calls == ["save"]






















def test_transport_allows_incremental_semantic_keys():
    agent_tools_module._require_complete_submitted_semantic_keys({
        "_submitted_semantic_keys": [
            "business_understanding", "capabilities",
        ],
    })

    agent_tools_module._require_complete_submitted_semantic_keys({
        "_submitted_semantic_keys": [
            "business_understanding", "request_roles", "field_semantics",
            "capabilities", "capability_relations", "unresolved_items",
        ],
    })

    agent_tools_module._require_complete_submitted_semantic_keys({
        "_submitted_semantic_keys": [
            "business_understanding", "request_roles", "field_semantics",
            "capabilities",
        ],
        "semantic_plan": {"field_semantics": [{
            "step_id": "submit", "wire_path": "title",
        }]},
    })


def test_real_pi_harmless_schema_drift_is_canonicalized_before_validation():
    plan = {
        "semantic_plan": {
            "business_understanding": {
                "summary": "提交申请",
                "risk_level": "low",
            },
            "capabilities": [{
                "name": "submit_request",
                "title": "提交申请",
                "kind": "submit",
                "anchor_step_id": "req-submit",
            }],
            "unresolved_items": [],
        },
        "ops": [{
            "op": "set_request_role",
            "request_id": "req-submit",
            "role": "business_write",
            "reason": "提交业务表单",
            "evidence": ["req-submit"],
        }],
    }

    normalized = agent_tools_module._canonicalize_recording_plan_aliases(plan)
    agent_tools_module._validate_strict_recording_plan(normalized)

    assert "risk_level" not in normalized["semantic_plan"]["business_understanding"]
    assert normalized["semantic_plan"]["capabilities"][0]["request_refs"] == [{
        "step_id": "req-submit",
        "usage": "execute",
    }]
    assert normalized["ops"][0]["evidence_refs"] == ["req-submit"]
    assert "evidence" not in normalized["ops"][0]


def test_real_pi_unknown_contract_mutation_is_still_rejected():
    plan = {
        "semantic_plan": {
            "business_understanding": {"summary": "提交申请"},
            "capabilities": [],
            "unresolved_items": [],
        },
        "ops": [{
            "op": "set_request_role",
            "request_id": "req-submit",
            "role": "business_write",
            "reason": "提交业务表单",
            "evidence_refs": ["req-submit"],
            "force_verified": True,
        }],
    }

    with pytest.raises(ToolError, match="force_verified"):
        agent_tools_module._validate_strict_recording_plan(plan)


@pytest.mark.asyncio
async def test_recording_replay_auth_prefers_fresh_captured_headers(monkeypatch):
    async def stale_runtime_headers(_tenant, _subsystem):
        return {
            "Authorization": "Bearer stale-runtime-token",
            "Tenant-Id": "stale-tenant",
        }

    monkeypatch.setattr(
        "dano.infra.token_store.get_token_headers",
        stale_runtime_headers,
    )
    session = SimpleNamespace(tenant="tenant-a", subsystem="system-a")

    headers = await agent_tools_module._recording_auth_headers(session, [{
        "headers": {
            "authorization": "Bearer fresh-captured-token",
            "tenant-id": "fresh-tenant",
        },
    }])

    assert headers == {
        "Authorization": "Bearer fresh-captured-token",
        "Tenant-Id": "fresh-tenant",
    }
    assert len({name.casefold() for name in headers}) == len(headers)


def test_field_semantics_cannot_bypass_typed_field_operations(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-field-overlay")
    with pytest.raises(ToolError, match="field_semantics"):
        asyncio.run(submit_recording_plan("run-field-overlay", {
            "recording_id": "rec-field-overlay",
            "base_flow_version": 1,
            "plan": {
                "semantic_plan": {
                    "business_understanding": {"summary": "提交申请"},
                    "field_semantics": [{
                        "step_id": "submit", "wire_path": "body.title",
                        "public_name": "申请标题",
                    }],
                },
                "ops": [],
            },
        }))
    assert session.spec.steps[0].params[0].label != "申请标题"


def test_recovered_legacy_screenshot_payload_is_rejected_instead_of_partially_applied(
    monkeypatch,
):
    session = _bind(monkeypatch, recording_id="rec-long-overlay")
    with pytest.raises(ToolError, match="field_semantics"):
        asyncio.run(submit_recording_plan("run-long-overlay", {
            "base_flow_version": 1,
            "plan": {
                "semantic_plan": {
                    "business_understanding": {"summary": "提交申请"},
                    "field_semantics": [{
                        "step_id": "submit", "wire_path": "body.title",
                        "public_name": "申请标题",
                    }],
                },
                "ops": [],
            },
        }))
    assert session.spec.steps[0].params[0].label != "申请标题"


def test_recording_tool_still_rejects_an_explicit_cross_session_identity(monkeypatch):
    _bind(monkeypatch, recording_id="rec-owned-by-run")

    with pytest.raises(ToolError, match="recording_id 与当前录制会话不匹配"):
        asyncio.run(get_recording_state("run-owned-by-run", {
            "recording_id": "rec-from-another-run",
        }))


def test_internal_recording_identity_question_never_reaches_operator(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-owned-by-run")

    async def fail_if_operator_called(**_kwargs):
        raise AssertionError("internal recording identity question reached the operator")

    session.ask_operator = fail_if_operator_called
    result = asyncio.run(ask_recording_operator("run-owned-by-run", {
        "text": "请提供当前 recording_id 和 flow_version",
        "options": ["请提供 recording_id"],
    }))

    assert result == {
        "answered": True,
        "answer": "recording_id 和 flow_version 由服务端管理；调用录制工具时省略这些字段。",
        "reason": "server_owned_recording_context",
    }


def test_length_truncated_plan_finishes_without_retry_loop(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-truncated-plan")

    result = asyncio.run(submit_recording_plan("run-truncated-plan", {
        "base_flow_version": 1,
        "submission_error": "model_output_truncated_missing_plan",
    }))

    assert result["accepted"] is True
    assert result["unchanged"] is True
    assert session.last_submission_kind == "plan"
    assert "结构化计划在模型输出上限前未完成" in result["warning"]
























































def test_explicit_read_option_cannot_become_public_query_capability():
    option_step = FlowStep(
        step_id="people", method="GET", path="/api/hr/user/page",
        response_json={"data": {"list": [{"id": 1, "name": "甲"}]}},
        source_meta={"role": "read_option"},
    )

    assert flow_module._planned_capability_has_public_anchor(
        FlowSpec(steps=[option_step]), "query_status", ["people"],
    ) is False

