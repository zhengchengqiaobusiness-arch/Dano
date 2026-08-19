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

