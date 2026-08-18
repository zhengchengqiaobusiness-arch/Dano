from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import FlowSpec
from dano.onboarding.recording_pi import RecordingPiSession


class _Recorder:
    def captured_all_requests(self) -> list[dict]:
        return [
            {
                "request_id": "req_86",
                "sequence": 86,
                "method": "GET",
                "url": "http://example.test/admin-api/erp/sale-order/page?pageNo=1&pageSize=10",
                "path": "/admin-api/erp/sale-order/page",
                "query": {"pageNo": "1", "pageSize": "10"},
                "response_status": 200,
            },
            {
                "request_id": "req_87",
                "sequence": 87,
                "method": "GET",
                "url": "http://example.test/admin-api/erp/sale-order/export-excel",
                "path": "/admin-api/erp/sale-order/export-excel",
                "response_status": 200,
            },
        ]

    def recorded_page_events(self) -> list[dict]:
        return []

    def recorded_field_evidence(self) -> list[dict]:
        return []

    def recorded_page_enum_options(self) -> dict:
        return {}


class _MutableRecorder(_Recorder):
    def __init__(self) -> None:
        self.requests = super().captured_all_requests()

    def captured_all_requests(self) -> list[dict]:
        return self.requests


@pytest.mark.asyncio
async def test_refresh_live_evidence_publishes_captured_requests_to_flow_spec() -> None:
    session = RecordingPiSession(
        tenant="tenant-1",
        subsystem="sales",
        recording_id="recording_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    session.bind_flow_spec(FlowSpec())
    session.bind_live_recording(_Recorder(), goal_text="录制销售订单页面的实际操作")

    await session.refresh_live_evidence()

    facts = session.current_flow_spec().request_facts.requests
    assert [fact.request_id for fact in facts] == ["req_86", "req_87"]
    assert facts[0].path == "/admin-api/erp/sale-order/page"


@pytest.mark.asyncio
async def test_refresh_live_evidence_updates_a_completed_response_without_a_new_request() -> None:
    recorder = _MutableRecorder()
    session = RecordingPiSession(
        tenant="tenant-1",
        subsystem="sales",
        recording_id="recording_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    session.bind_flow_spec(FlowSpec())
    session.bind_live_recording(recorder)
    await session.refresh_live_evidence()

    recorder.requests[0]["response_json"] = {"data": {"list": [{"id": 42}]}}
    await session.refresh_live_evidence()

    facts = session.current_flow_spec().request_facts.requests
    assert facts[0].response_json == {"data": {"list": [{"id": 42}]}}
