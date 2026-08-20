"""Stage 3 live analysis handoff: pending batches and evidence refs."""

from __future__ import annotations

import asyncio

import pytest

from dano.execution.page.flow_spec import (
    FlowSpec,
    RequestFact,
    RequestFacts,
)
from dano.execution.page.recording_live import _require_grounded_refs
from dano.onboarding.recording_gateway import (
    RecordingGatewaySession,
    RecordingSessionConfig,
)


class _Capture:
    def captured_all_requests(self):
        return [{"request_id": "req_1", "sequence": 1}]

    def recorded_page_events(self):
        return []

    def recorded_field_evidence(self):
        return []


def _session() -> RecordingGatewaySession:
    async def unused(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("not used")

    async def send(_payload: dict) -> None:
        return None

    session = RecordingGatewaySession(
        config=RecordingSessionConfig(
            tenant="t",
            subsystem="oa",
            recording_id="rec",
            action="act",
            start_url="http://example.test",
        ),
        send=send,
        pi_factory=unused,
        publisher=unused,
    )
    session.capture = _Capture()
    session.workflow = None
    return session


async def test_pending_batch_runs_after_previous_batch_fails() -> None:
    session = _session()
    calls: list[str] = []

    class FakePi:
        flow_spec = FlowSpec(tenant="t", subsystem="oa")

        async def notify_live_batch(self, payload: dict) -> None:
            reason = str(payload.get("reason") or "")
            calls.append(reason)
            if reason == "request_batch":
                session._schedule_live("business_request")
                raise RuntimeError("batch A failed")

        def current_flow_spec(self):
            return self.flow_spec

    async def ensure(_fresh: bool = False):
        return FakePi()

    session._ensure_pi = ensure  # type: ignore[method-assign]
    session._capture_live_notebook = lambda: None  # type: ignore[method-assign]
    session._last_live_count = 0
    session._live_pending_reason = "request_batch"
    holder = asyncio.create_task(asyncio.sleep(3600))
    session._live_task = holder
    try:
        await session._drain_live()
    finally:
        holder.cancel()
    assert calls == ["request_batch", "business_request"]
    assert session._last_live_count == 1
    assert session._live_pending_reason == ""


async def test_failed_batch_does_not_advance_since_seq_when_nothing_is_pending() -> None:
    session = _session()

    class FakePi:
        flow_spec = FlowSpec(tenant="t", subsystem="oa")

        async def notify_live_batch(self, payload: dict) -> None:
            raise RuntimeError("batch failed")

    async def ensure(_fresh: bool = False):
        return FakePi()

    session._ensure_pi = ensure  # type: ignore[method-assign]
    session._capture_live_notebook = lambda: None  # type: ignore[method-assign]
    session._last_live_count = 4
    session._live_pending_reason = "request_batch"
    await session._drain_live()
    assert session._last_live_count == 4
    assert session._live_pending_reason == ""


def _spec_with_ids() -> FlowSpec:
    spec = FlowSpec(tenant="t", subsystem="oa")
    spec.request_facts = RequestFacts(
        requests=[RequestFact(request_id="req_1"), RequestFact(request_id="req_10")],
        page_events=[{"event_id": "event_123", "action_id": "action_1"}],
        field_evidence=[{"evidence_id": "field-evidence-xxx", "field": "id", "event_id": "event_123"}],
    )
    spec.steps = []
    return spec


def test_evidence_refs_require_full_id_not_substring() -> None:
    spec = _spec_with_ids()
    _require_grounded_refs(spec, "set_param_source", ["req_1"])
    _require_grounded_refs(spec, "set_param_source", ["request:req_10"])
    _require_grounded_refs(spec, "set_param_source", ["field_evidence:field-evidence-xxx"])
    with pytest.raises(ValueError):
        _require_grounded_refs(spec, "set_param_source", ["req_100"])
    with pytest.raises(ValueError):
        _require_grounded_refs(spec, "set_param_source", ["orderId"])
    with pytest.raises(ValueError):
        _require_grounded_refs(spec, "set_param_source", ["id"])


class _LiveRecorder:
    def __init__(self) -> None:
        self.requests = [{
            "request_id": "req_1",
            "sequence": 1,
            "method": "POST",
            "url": "http://example.test/save",
            "post_data": '{"title":"a"}',
            "response_status": 200,
        }]
        self.events = [{"event_id": "event_1", "action_id": "action_1"}]
        self.fields = [{
            "occurrence_id": "field-occ-1",
            "evidence_id": "field-occ-1",
            "field": "title",
            "value": "a",
            "field_aliases": ["title"],
        }]
        self.enums: dict = {}

    def captured_all_requests(self):
        return list(self.requests)

    def recorded_page_events(self):
        return list(self.events)

    def recorded_field_evidence(self):
        return list(self.fields)

    def recorded_page_enum_options(self):
        return dict(self.enums)


async def test_refresh_live_evidence_only_binds_new_occurrences(monkeypatch) -> None:
    from dano.execution.page.flow_spec import FlowSpec
    from dano.onboarding.recording_pi import RecordingPiSession

    bound_payloads: list[list[str]] = []

    def fake_bind(requests, page_events, evidence, page_enum_options=None):  # noqa: ANN001, ARG001
        ids = [
            str(item.get("occurrence_id") or item.get("evidence_id") or "")
            for item in (evidence or [])
            if isinstance(item, dict)
        ]
        bound_payloads.append(ids)
        return [
            {**item, "binding_status": "bound", "request_id": "req_1", "wire_path": "body.title"}
            for item in (evidence or [])
            if isinstance(item, dict)
        ]

    monkeypatch.setattr(
        "dano.execution.page.recording_field_identity.bind_field_evidence",
        fake_bind,
    )
    monkeypatch.setattr(
        "dano.execution.page.recording_facts._option_sources_from_page_enum_options",
        lambda *_args, **_kwargs: [],
    )
    session = RecordingPiSession(
        tenant="t",
        subsystem="oa",
        recording_id="recording_" + ("b" * 32),
        resume_history=False,
    )
    session.flow_spec = FlowSpec(tenant="t", subsystem="oa")
    recorder = _LiveRecorder()
    session._live_recorder = recorder
    await session.refresh_live_evidence()
    first_request = session.flow_spec.request_facts.requests[0]
    recorder.fields.append({
        "occurrence_id": "field-occ-2",
        "evidence_id": "field-occ-2",
        "field": "remark",
        "value": "b",
        "field_aliases": ["remark"],
    })
    await session.refresh_live_evidence()
    assert bound_payloads[0] == ["field-occ-1"]
    assert bound_payloads[1] == ["field-occ-2"]
    assert session.flow_spec.request_facts.requests[0] is first_request
    occ = {
        str(item.get("occurrence_id") or "")
        for item in session.flow_spec.request_facts.field_evidence
    }
    assert occ == {"field-occ-1", "field-occ-2"}


async def test_live_recording_analysis_is_not_cut_off_by_the_generic_pi_timeout() -> None:
    """A valid slow analysis must finish instead of making the browser terminal."""
    from dano.onboarding.recording_pi import RecordingPiSession

    session = RecordingPiSession(
        tenant="t",
        subsystem="oa",
        recording_id="recording_" + ("c" * 32),
        timeout_s=0.01,
        resume_history=False,
    )
    session.flow_spec = FlowSpec(tenant="t", subsystem="oa")
    session._proc = object()  # type: ignore[assignment]
    observed_timeouts: list[float | None] = []

    async def fake_command(command_type: str, *, timeout_s=None, **_payload):  # noqa: ANN001
        if command_type == "cancel":
            return {"status": "cancelled"}
        observed_timeouts.append(timeout_s)
        await asyncio.sleep(0.02)
        effective_timeout = session.timeout_s if timeout_s is None else timeout_s
        if effective_timeout > 0 and effective_timeout < 0.02:
            raise asyncio.TimeoutError
        return {
            "status": "submitted",
            "accepted_submission": "submit_recording_plan",
        }

    session._command = fake_command  # type: ignore[method-assign]
    result = await session.prompt(
        "analyze the current recording",
        prompt_mode="recording_analysis",
        analysis_phase="request_batch",
    )

    assert result["status"] == "submitted"
    assert observed_timeouts == [0]

