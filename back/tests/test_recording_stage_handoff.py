"""Stage 2–6 handoff contracts: identity, roles, evidence ids, model state."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from dano.execution.page.flow_spec import (
    _preread_dedupe_key,
    recording_agent_state,
    to_flow_spec,
)
from dano.execution.page.recording_field_evidence import _evidence_id, bind_field_evidence
from dano.execution.page.recording_live import compact_model_payload, recording_delta
from dano.onboarding.recording_gateway import _spec_fields


PAGE = {
    "url": "http://example.test/app/docs",
    "path": "/app/docs",
    "document_title": "单据",
}


def _req(
    request_id: str,
    *,
    method: str,
    url: str,
    sequence: int,
    role: str,
    keep: bool = True,
    body: dict | None = None,
    response: dict | None = None,
    action: str = "",
) -> dict:
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    return {
        "request_id": request_id,
        "sequence": sequence,
        "method": method,
        "url": url,
        "query": query,
        "post_data": None if body is None else json.dumps(body, ensure_ascii=False),
        "response_status": 200,
        "response_json": response if response is not None else {"code": 0, "data": True},
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
        "trigger_page_context": PAGE,
        "trigger_action_id": action,
        "trigger_transaction_id": action,
        "_request_role": {"role": role, "keep": keep, "confidence": 0.95},
    }


def test_preread_dedupe_keeps_same_path_with_different_record_ids() -> None:
    first = _req(
        "req_88",
        method="GET",
        url="http://example.test/admin-api/doc/get?id=37",
        sequence=8,
        role="business_get",
    )
    second = _req(
        "req_93",
        method="GET",
        url="http://example.test/admin-api/doc/get?id=36",
        sequence=12,
        role="business_get",
    )
    assert _preread_dedupe_key(first) != _preread_dedupe_key(second)


def test_to_flow_spec_materializes_distinct_record_identity_gets() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_88",
                method="GET",
                url="http://example.test/admin-api/doc/get?id=37",
                sequence=8,
                role="business_get",
                response={"code": 0, "data": {"id": 37, "remark": "other"}},
            ),
            _req(
                "req_93",
                method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=12,
                role="business_get",
                response={"code": 0, "data": {"id": 36, "remark": "edit"}},
            ),
            _req(
                "req_update",
                method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=13,
                role="business_write",
                action="act_edit",
                body={"id": 36, "remark": "edit"},
            ),
        ],
        page_context=PAGE,
    )
    request_ids = {
        str((step.source_meta or {}).get("request_id") or "")
        for step in spec.steps
    }
    assert {"req_88", "req_93", "req_update"} <= request_ids


def test_request_role_overrides_keep_detail_get_as_business_read() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_detail",
                method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=3,
                role="read_context",
                keep=False,
                response={"code": 0, "data": {"id": 36}},
            ),
            _req(
                "req_update",
                method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=4,
                role="business_write",
                action="act_edit",
                body={"id": 36, "remark": "x"},
            ),
        ],
        page_context=PAGE,
        request_role_overrides={
            "req_detail": {
                "role": "business_get",
                "keep": True,
                "reason": "edit hydration",
                "confidence": 0.9,
            }
        },
    )
    request_ids = {
        str((step.source_meta or {}).get("request_id") or "")
        for step in spec.steps
    }
    assert "req_detail" in request_ids


def test_evidence_id_is_stable_when_list_order_changes() -> None:
    first = {
        "label": "备注",
        "field": "remark",
        "field_aliases": ["remark"],
        "value": "213213",
        "op": "fill",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "in_dialog": True,
        "page_context": PAGE,
    }
    second = {
        "label": "优惠率（%）",
        "field": "discountPercent",
        "field_aliases": ["discountPercent"],
        "value": "1110",
        "op": "fill",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "in_dialog": True,
        "page_context": PAGE,
    }
    assert _evidence_id(first, 0) == _evidence_id(first, 9)
    forward = bind_field_evidence([], [], [first, second])
    reverse = bind_field_evidence([], [], [second, first])
    assert {item["evidence_id"] for item in forward} == {item["evidence_id"] for item in reverse}


def test_recording_state_keeps_newest_field_evidence() -> None:
    evidence = [
        {
            "evidence_id": f"field-evidence-old-{index:02d}",
            "label": f"旧字段{index}",
            "field": f"old_{index}",
            "value": str(index),
            "binding_status": "unbound",
        }
        for index in range(45)
    ]
    evidence.append({
        "evidence_id": "field-evidence-dialog-remark",
        "label": "备注",
        "field": "remark",
        "value": "213213",
        "binding_status": "bound",
        "request_id": "req_update",
        "wire_path": "body.remark",
    })
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_update",
                method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=1,
                role="business_write",
                action="act_edit",
                body={"id": 1, "remark": "213213"},
            ),
        ],
        field_evidence=evidence,
        page_context=PAGE,
    )
    state = recording_agent_state(spec)
    labels = [
        str(item.get("label") or "")
        for item in (state.get("facts") or {}).get("field_evidence") or []
        if isinstance(item, dict)
    ]
    assert "备注" in labels


def test_recording_delta_includes_related_field_evidence() -> None:
    requests = [
        _req(
            "req_update",
            method="PUT",
            url="http://example.test/admin-api/doc/update",
            sequence=1,
            role="business_write",
            action="act_edit",
            body={"id": 1, "remark": "keep"},
        ),
    ]

    class _Recorder:
        def captured_all_requests(self):
            return requests

        def recorded_page_events(self):
            return []

        def recorded_field_evidence(self):
            return [{
                "label": "备注",
                "field": "remark",
                "field_aliases": ["remark"],
                "value": "keep",
                "op": "fill",
                "request_id": "req_update",
                "page_id": "page_1",
                "frame_id": "frame_1",
                "in_dialog": True,
                "page_context": PAGE,
            }]

    delta = recording_delta(_Recorder(), since_seq=0, limit=10)
    assert any(
        isinstance(item, dict) and item.get("label") == "备注"
        for item in delta.get("field_evidence") or []
    )


def test_compact_model_payload_can_keep_list_tail() -> None:
    compacted = compact_model_payload(list(range(47)), max_items=40, list_keep="tail")
    values = [item for item in compacted if not isinstance(item, dict)]
    assert values[0] == 7
    assert values[-1] == 46


def test_recording_delta_can_page_compact_history_before_live_floor() -> None:
    requests = [
        _req(
            f"req_{index}",
            method="GET",
            url=f"http://example.test/admin-api/doc/page?pageNo={index}",
            sequence=index,
            role="read_context",
            keep=False,
        )
        for index in range(6)
    ]

    class _Recorder:
        def captured_all_requests(self):
            return requests

        def recorded_page_events(self):
            return []

        def recorded_field_evidence(self):
            return []

    delta = recording_delta(
        _Recorder(), since_seq=0, limit=4, stop_before=5, compact=True,
    )
    assert delta["compact_history"] is True
    assert len(delta["requests"]) == 4
    assert "response_json" not in delta["requests"][0]
    assert delta["next_seq"] == 4
    assert delta["has_more"] is True


def test_spec_fields_count_semantic_plan_before_materialization() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_update",
                method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=1,
                role="business_write",
                action="act_edit",
                body={"id": 1},
            ),
        ],
        page_context=PAGE,
    )
    spec.capabilities = []
    spec.meta = {
        **(spec.meta or {}),
        "capability_model": {
            "semantic_plan": {
                "capabilities": [
                    {"name": "edit_doc", "title": "编辑单据"},
                    {"name": "approve_doc", "title": "审批单据"},
                ]
            }
        },
    }
    fields = _spec_fields(spec)
    assert fields["capability_count"] == 2
    assert fields["capability_names"] == ["edit_doc", "approve_doc"]


def test_compact_keeps_same_path_with_different_record_query_values() -> None:
    from dano.execution.page.flow_spec import _compact_repeated_endpoint_observations

    first = {
        "request_id": "req_36",
        "method": "GET",
        "path": "/admin-api/doc/get",
        "role": "read_context",
        "keep": False,
        "query": {"id": "36"},
        "query_paths": ["id"],
    }
    second = {
        "request_id": "req_37",
        "method": "GET",
        "path": "/admin-api/doc/get",
        "role": "read_context",
        "keep": False,
        "query": {"id": "37"},
        "query_paths": ["id"],
    }
    compacted = _compact_repeated_endpoint_observations([first, second])
    request_ids = {str(item.get("request_id") or "") for item in compacted}
    assert request_ids == {"req_36", "req_37"}


def test_evidence_id_is_stable_when_recorded_value_changes() -> None:
    first = {
        "label": "数量",
        "field": "count",
        "field_aliases": ["count"],
        "value": "1",
        "op": "fill",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "in_dialog": True,
        "page_context": PAGE,
        "action_id": "act_fill",
        "event_id": "ev_1",
    }
    second = {
        **first,
        "value": "3",
    }
    assert _evidence_id(first) == _evidence_id(second)
    other_action = {
        **first,
        "value": "3",
        "action_id": "act_fill_2",
        "event_id": "ev_2",
    }
    assert _evidence_id(first) != _evidence_id(other_action)
