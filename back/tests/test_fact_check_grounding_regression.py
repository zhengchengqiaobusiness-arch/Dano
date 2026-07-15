"""Regression coverage for recorded fact-check ownership and causality.

The recorder must not turn an arbitrary value collision in network history into
proof that a write operation succeeded.  These cases deliberately use generic
paths and payloads so the policy remains portable across OA implementations.
"""

from __future__ import annotations

import json

from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowStep,
    ParamField,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    _merge_flow_read_sources,
    prepare_flow_spec_for_publish,
    to_flow_spec,
)
from dano.execution.page.request_capture import (
    _select_api_request_for_capability,
    suggest_fact_check,
)


def _captured_request(
    index: int,
    method: str,
    path: str,
    role: str,
    *,
    response=None,
    body=None,
    transaction: str = "",
) -> dict:
    request = {
        "index": index,
        "sequence": index,
        "method": method,
        "url": path,
        "path": path,
        "response_json": response,
        "post_data": json.dumps(body) if body is not None else None,
        "content_type": "application/json",
        "_request_role": {
            "role": role,
            "keep": role in {"business_get", "business_write", "submit_anchor"},
            "confidence": 0.99,
            "reason": "test evidence",
        },
    }
    if transaction:
        request.update({
            "trigger_transaction_id": transaction,
            "trigger_action_id": transaction,
            "trigger_op": "click",
        })
    return request


def test_fact_check_rejects_option_source_value_collision():
    """A matching username in an option endpoint is not post-write evidence."""
    reads = [{
        "url": "/directory/users/online-status",
        "role": "read_option",
        "sequence": 4,
        "json": {"data": [{"username": "same-recorded-text"}]},
    }]

    assert suggest_fact_check({"reason": "same-recorded-text"}, reads) is None


def test_flow_read_merge_keeps_causal_metadata_from_captured_projection():
    """The lightweight response read must not erase the request's causal anchors."""
    payload = {"data": {"list": [{"reason": "new-record-value"}]}}
    explicit_reads = [{
        "url": "/requests/page",
        "json": payload,
        "request_id": "req-21",
        "request_index": 21,
        "sequence": 21,
        "page_id": "page-1",
        "frame_id": "frame-1",
    }]
    captured_requests = [{
        "url": "/requests/page",
        "response_json": payload,
        "request_id": "req-21",
        "index": 21,
        "page_id": "page-1",
        "frame_id": "frame-1",
        "trigger_action_id": "action-1",
        "trigger_transaction_id": "transaction-1",
    }]

    merged = _merge_flow_read_sources(
        explicit_reads,
        captured_requests,
        [{"role": "business_get"}],
    )

    assert len(merged) == 1
    assert merged[0]["role"] == "business_get"
    assert merged[0]["request_id"] == "req-21"
    assert merged[0]["request_index"] == 21
    assert merged[0]["sequence"] == 21
    assert merged[0]["trigger_action_id"] == "action-1"
    assert merged[0]["trigger_transaction_id"] == "transaction-1"


def test_fact_check_rejects_business_read_that_precedes_the_write():
    """Even a business list cannot prove a later write when it was read first."""
    spec = to_flow_spec(
        [
            _captured_request(
                10,
                "GET",
                "/requests/page",
                "business_get",
                response={"data": {"list": [{"reason": "recorded-value"}]}},
            ),
            _captured_request(
                11,
                "POST",
                "/requests/create",
                "business_write",
                response={"code": 0},
                body={"reason": "recorded-value"},
                transaction="create-1",
            ),
        ],
        samples={"reason": "recorded-value"},
    )

    write = next(step for step in spec.steps if step.method == "POST")
    assert write.fact_check is None


def test_fact_check_belongs_to_the_write_not_the_followup_read():
    """A valid post-write read verifies its owning write capability."""
    spec = to_flow_spec(
        [
            _captured_request(
                20,
                "POST",
                "/requests/create",
                "business_write",
                response={"code": 0},
                body={"reason": "new-record-value"},
                transaction="create-2",
            ),
            _captured_request(
                21,
                "GET",
                "/requests/page",
                "business_get",
                response={"data": {"list": [{"reason": "new-record-value"}]}},
                transaction="create-2",
            ),
        ],
        samples={"reason": "new-record-value"},
    )

    write = next(step for step in spec.steps if step.method == "POST")
    read = next(step for step in spec.steps if step.method == "GET")
    assert write.fact_check is not None
    assert write.fact_check["endpoint"] == "/requests/page"
    assert write.fact_check["match_field"] == "reason"
    assert write.fact_check["param"] == "reason"
    assert read.fact_check is None


def test_publish_prunes_legacy_fact_check_from_prewrite_option_request():
    """Frozen recordings need deterministic cleanup, not only new inference fixes."""
    write = FlowStep(
        step_id="withdraw",
        method="DELETE",
        path="/workflow/instances/cancel",
        source_meta={"request_id": "write-9", "sequence": 9},
        params=[ParamField(path="reason", key="reason", value="same-recorded-text")],
        fact_check={
            "endpoint": "/directory/users/online-status",
            "match_field": "username",
            "param": "reason",
        },
    )
    facts = RequestFacts(
        requests=[
            RequestFact(
                request_id="read-5",
                sequence=5,
                method="GET",
                path="/directory/users/online-status",
                response_json={"data": [{"username": "same-recorded-text"}]},
            ),
            RequestFact(
                request_id="write-9",
                sequence=9,
                method="DELETE",
                path="/workflow/instances/cancel",
                post_data=json.dumps({"reason": "same-recorded-text"}),
            ),
        ],
        analysis={
            "read-5": RequestAnalysis(
                request_id="read-5",
                role="read_option",
                keep=False,
                confidence=0.99,
            ),
            "write-9": RequestAnalysis(
                request_id="write-9",
                role="business_write",
                keep=True,
                confidence=0.99,
            ),
        },
    )

    prepared = prepare_flow_spec_for_publish(FlowSpec(steps=[write], request_facts=facts))

    assert prepared.steps[0].fact_check is None


def test_capability_selection_promotes_its_write_step_fact_check():
    """The runtime reads top-level fact_check, so scoped step evidence must reach it."""
    fact_check = {
        "endpoint": "/requests/page",
        "match_field": "reason",
        "param": "reason",
    }
    workflow = {
        "steps": [
            {"step_id": "query", "method": "GET", "url": "/requests/page"},
            {
                "step_id": "write",
                "method": "POST",
                "url": "/requests/create",
                "fact_check": fact_check,
            },
        ],
        "capabilities": [
            {"name": "query_requests", "kind": "query_status", "step_ids": ["query"]},
            {"name": "create_request", "kind": "submit", "step_ids": ["write"]},
        ],
    }

    selected_write, _cap, error = _select_api_request_for_capability(workflow, "create_request")
    selected_read, _read_cap, read_error = _select_api_request_for_capability(workflow, "query_requests")

    assert error == ""
    assert read_error == ""
    assert selected_write["fact_check"] == fact_check
    assert "fact_check" not in selected_read
