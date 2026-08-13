from __future__ import annotations

from dano.execution.page.recording_field_identity import bind_field_evidence
from dano.execution.page.flow_spec import to_flow_spec


def _requests() -> list[dict]:
    return [
        {
            "request_id": "req-query",
            "method": "GET",
            "url": "https://example.test/leave/page?reason=1",
            "query": {"reason": "1"},
            "page_id": "page-1",
            "frame_id": "main",
            "trigger_action_id": "action-search",
            "timestamp": 200,
            "page_context": {"path": "/leave"},
        },
        {
            "request_id": "req-submit",
            "method": "POST",
            "url": "https://example.test/leave/submit",
            "post_data": '{"reason":"1","type":1}',
            "page_id": "page-1",
            "frame_id": "main",
            "trigger_action_id": "action-submit",
            "timestamp": 400,
            "page_context": {"path": "/leave"},
        },
    ]


def test_field_evidence_stays_ambiguous_when_two_requests_share_the_exact_alias() -> None:
    [bound] = bind_field_evidence(_requests(), [], [{
        "label": "原因",
        "field_aliases": ["reason"],
        "value": "1",
        "page_id": "page-1",
        "frame_id": "main",
        "page_context": {"path": "/leave"},
    }])

    assert bound["binding_status"] == "ambiguous"
    assert "request_id" not in bound
    assert "wire_path" not in bound
    assert {(item["request_id"], item["wire_path"]) for item in bound["binding_candidates"]} == {
        ("req-query", "query.reason"),
        ("req-submit", "body.reason"),
    }


def test_action_causality_disambiguates_the_same_field_alias() -> None:
    [bound] = bind_field_evidence(_requests(), [], [{
        "label": "原因",
        "field_aliases": ["reason"],
        "action_id": "action-submit",
        "page_id": "page-1",
        "frame_id": "main",
        "page_context": {"path": "/leave"},
    }])

    assert bound["binding_status"] == "bound"
    assert bound["request_id"] == "req-submit"
    assert bound["wire_path"] == "body.reason"


def test_unique_exact_alias_binds_without_using_the_recorded_value_as_identity() -> None:
    [bound] = bind_field_evidence(_requests(), [], [{
        "label": "请假类型",
        "field_aliases": ["type"],
        "value": "1",
        "action_id": "action-submit",
        "page_id": "page-1",
        "frame_id": "main",
        "page_context": {"path": "/leave"},
    }])

    assert bound["binding_status"] == "bound"
    assert (bound["request_id"], bound["wire_path"]) == ("req-submit", "body.type")


def test_equal_value_without_an_exact_alias_never_binds_a_field() -> None:
    [bound] = bind_field_evidence(_requests(), [], [{
        "label": "未知字段",
        "field_aliases": [],
        "value": "1",
        "page_id": "page-1",
        "frame_id": "main",
        "page_context": {"path": "/leave"},
    }])

    assert bound["binding_status"] == "unbound"
    assert bound["binding_candidates"] == []


def test_unique_value_with_exact_action_causality_can_bind_aliasless_control() -> None:
    requests = _requests()
    requests[1]["post_data"] = '{"reason":"unique recorded reason","type":1}'
    [bound] = bind_field_evidence(requests, [], [{
        "label": "原因",
        "field_aliases": [],
        "value": "unique recorded reason",
        "action_id": "action-submit",
        "page_id": "page-1",
        "frame_id": "main",
        "page_context": {"path": "/leave"},
    }])

    assert bound["binding_status"] == "bound"
    assert (bound["request_id"], bound["wire_path"]) == ("req-submit", "body.reason")
    assert bound["binding_method"] == "unique_value_same_transaction"


def test_unique_date_value_with_exact_action_causality_binds_epoch_wire_field() -> None:
    requests = _requests()
    requests[1]["post_data"] = '{"startTime":1785945600000,"reason":"x"}'
    [bound] = bind_field_evidence(requests, [], [{
        "label": "开始时间",
        "field_aliases": [],
        "control_kind": "date",
        "value": "2026-08-06 00:00:00",
        "action_id": "action-submit",
        "page_id": "page-1",
        "frame_id": "main",
        "page_context": {"path": "/leave"},
    }])

    assert bound["binding_status"] == "bound"
    assert (bound["request_id"], bound["wire_path"]) == ("req-submit", "body.startTime")


def test_page_enum_identity_enriches_aliasless_select_before_binding() -> None:
    [bound] = bind_field_evidence(_requests(), [], [{
        "label": "请假类型",
        "field_aliases": [],
        "control_kind": "select",
        "action_id": "action-submit",
        "page_id": "page-1",
        "frame_id": "main",
        "page_context": {"path": "/leave"},
    }], page_enum_options={
        "请假类型": {
            "field_key": "请假类型",
            "field_aliases": ["type"],
            "control_kind": "select",
            "page_id": "page-1",
            "frame_id": "main",
            "page_context": {"path": "/leave"},
            "mapping_complete": True,
            "options": [
                {"label": "病假", "value": 1},
                {"label": "事假", "value": 2},
            ],
        },
    })

    assert bound["binding_status"] == "bound"
    assert (bound["request_id"], bound["wire_path"]) == ("req-submit", "body.type")
    assert "type" in bound["field_aliases"]


def test_page_and_frame_scope_exclude_an_other_page_request() -> None:
    requests = _requests()
    requests[1]["page_id"] = "page-2"
    [bound] = bind_field_evidence(requests, [], [{
        "label": "原因",
        "field_aliases": ["reason"],
        "action_id": "action-search",
        "page_id": "page-1",
        "frame_id": "main",
        "page_context": {"path": "/leave"},
    }])

    assert bound["binding_status"] == "bound"
    assert (bound["request_id"], bound["wire_path"]) == ("req-query", "query.reason")


def test_flow_spec_persists_binding_results_and_does_not_name_by_equal_values() -> None:
    requests = _requests()
    requests[0].update({
        "role": "business_get", "keep": True, "confidence": 0.99,
        "response_json": {"list": [], "total": 0},
    })
    requests[1].update({
        "role": "business_write", "keep": True, "confidence": 0.99,
        "response_json": {"code": 0},
    })
    spec = to_flow_spec(
        captured_requests=requests,
        samples={"原因": "1", "请假类型": "1"},
        field_evidence=[
            {
                "label": "原因", "field_aliases": ["reason"], "value": "1",
                "page_id": "page-1", "frame_id": "main", "page_context": {"path": "/leave"},
            },
            {
                "label": "请假类型", "field_aliases": ["type"], "value": "1",
                "action_id": "action-submit",
                "required": False,
                "page_id": "page-1", "frame_id": "main", "page_context": {"path": "/leave"},
            },
        ],
        page_events=[{"event_id": "event-submit", "kind": "action"}],
    )

    evidence = spec.request_facts.field_evidence
    assert [item["binding_status"] for item in evidence] == ["ambiguous", "bound"]
    submit = next(step for step in spec.steps if (step.source_meta or {}).get("request_id") == "req-submit")
    fields = {param.path: param for param in submit.params}
    assert fields["type"].label == "请假类型"
    assert fields["type"].source["required_state"] == "optional"
    assert fields["reason"].label != "原因"
    assert fields["reason"].source["required_state"] == "unknown"


def test_bound_aliasless_control_projects_all_grounded_axes_to_flow_spec() -> None:
    requests = [{
        "request_id": "req-submit",
        "method": "POST",
        "url": "https://example.test/leave/submit",
        "post_data": '{"startTime":1785945600000,"reason":"回家"}',
        "page_id": "page-1",
        "frame_id": "main",
        "trigger_action_id": "action-submit",
        "timestamp": 400,
        "page_context": {"path": "/leave"},
        "role": "business_write",
        "keep": True,
        "confidence": 0.99,
        "response_json": {"code": 0},
    }]
    spec = to_flow_spec(
        captured_requests=requests,
        field_evidence=[{
            "label": "开始时间",
            "field_aliases": [],
            "control_kind": "date",
            "op": "pick",
            "value": "2026-08-06 00:00:00",
            "required": True,
            "page_id": "page-1",
            "frame_id": "main",
            "action_id": "action-submit",
            "page_context": {"path": "/leave"},
        }],
        page_events=[{"event_id": "event-submit", "kind": "action"}],
    )

    submit = spec.steps[0]
    start = next(param for param in submit.params if param.path == "startTime")
    assert start.label == "开始时间"
    assert start.type == "date"
    assert start.wire_type == "number"
    assert start.wire_format == "epoch_ms"
    assert start.category == "user_param"
    assert start.source_kind == "user_input"
    assert start.required is True
    assert start.default_value is None


def test_complete_enum_alias_binds_the_matching_required_control_without_a_selected_value() -> None:
    requests = [{
        "request_id": "req-submit",
        "method": "POST",
        "url": "https://example.test/leave/submit",
        "post_data": '{"type":2,"reason":"home"}',
        "page_id": "page-1",
        "frame_id": "main",
        "trigger_action_id": "action-submit",
        "timestamp": 400,
        "page_context": {"path": "/leave/create"},
    }]

    [bound] = bind_field_evidence(requests, [], [{
        "event_id": "event-type",
        "label": "Leave category",
        "field": "Leave category",
        "field_aliases": [],
        "control_kind": "select",
        "action_id": "action-select-type",
        "required": True,
        "value": "",
        "observed_at": 300,
        "page_id": "page-1",
        "frame_id": "main",
        "page_context": {"path": "/leave/create"},
    }], page_enum_options={
        "Leave category": {
            "field_key": "Leave category",
            "field_aliases": ["type"],
            "control_kind": "select",
            "page_id": "page-1",
            "frame_id": "main",
            "page_context": {"path": "/leave/create"},
            "mapping_complete": True,
            "options": [
                {"label": "Annual", "value": 1},
                {"label": "Personal", "value": 2},
            ],
        },
    })

    assert bound["binding_status"] == "bound"
    assert (bound["request_id"], bound["wire_path"]) == ("req-submit", "body.type")
    assert bound["required_observed"] is True


def test_unique_value_binds_a_control_filled_before_the_later_submit_action() -> None:
    requests = [{
        "request_id": "req-submit",
        "method": "POST",
        "url": "https://example.test/leave/submit",
        "post_data": '{"reason":"family matter","other":"x"}',
        "page_id": "page-1",
        "frame_id": "main",
        "trigger_action_id": "action-submit",
        "timestamp": 400,
        "page_context": {"path": "/leave/create"},
    }]

    [bound] = bind_field_evidence(requests, [], [{
        "event_id": "event-reason",
        "action_id": "action-fill-reason",
        "label": "Reason",
        "field": "Reason",
        "field_aliases": [],
        "control_kind": "textarea",
        "required": True,
        "value": "family matter",
        "observed_at": 300,
        "page_id": "page-1",
        "frame_id": "main",
        "page_context": {"path": "/leave/create"},
    }])

    assert bound["binding_status"] == "bound"
    assert (bound["request_id"], bound["wire_path"]) == ("req-submit", "body.reason")
    assert bound["binding_method"] == "unique_value_same_scope_causal_order"
