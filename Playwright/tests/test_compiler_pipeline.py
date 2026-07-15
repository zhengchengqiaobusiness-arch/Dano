from __future__ import annotations

from dano_recording.compiler.pipeline import compile_recording
from dano_recording.domain.capabilities import CapabilityRisk
from dano_recording.domain.facts import ActionFact, FactKind, RecordingFact, RequestFact
from dano_recording.domain.operations import RequestDisposition


TENANT = "tenant-a"
RECORDING = "recording-a"


def action(sequence: int, action_id: str, label: str) -> ActionFact:
    return ActionFact(
        tenant=TENANT,
        recording_id=RECORDING,
        sequence=sequence,
        action_id=action_id,
        action_type="click",
        label=label,
        payload={
            "evidence_origin": "server_dispatched",
            "causal_eligible": True,
        },
    )


def request(
    sequence: int,
    request_id: str,
    method: str,
    url: str,
    action_id: str,
    *,
    body=None,
    body_present: bool = False,
    response_body=None,
) -> RequestFact:
    return RequestFact(
        tenant=TENANT,
        recording_id=RECORDING,
        sequence=sequence,
        request_id=request_id,
        method=method,
        url=url,
        action_id=action_id,
        request_body=body,
        request_body_present=body_present,
        response_body=response_body,
    )


def test_pipeline_is_lossless_for_bodyless_commands_and_repeated_query() -> None:
    facts = (
        action(1, "search", "查询申请"),
        request(
            2,
            "query-request",
            "GET",
            "https://oa.example/api/applications?status=pending&status=review&owner=me",
            "search",
            response_body={"records": [{"id": "application-42"}]},
        ),
        action(3, "submit", "提交申请"),
        request(
            4,
            "prepare-request",
            "GET",
            "https://oa.example/api/forms/current",
            "submit",
            response_body={"id": "form-9"},
        ),
        request(
            5,
            "submit-request",
            "POST",
            "https://oa.example/api/applications/submit",
            "submit",
        ),
        action(6, "withdraw", "撤回申请"),
        request(
            7,
            "withdraw-request",
            "DELETE",
            "https://oa.example/api/applications/application-42",
            "withdraw",
        ),
        action(8, "options", "打开类型下拉"),
        request(
            9,
            "option-request",
            "GET",
            "https://oa.example/api/catalog/types",
            "options",
        ),
        RecordingFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=10,
            kind=FactKind.RESPONSE,
            action_id="options",
            payload={
                "request_id": "option-request",
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body_present": True,
                "body": [{"label": "差旅", "value": 2}],
            },
        ),
    )

    compilation = compile_recording(
        tenant=TENANT,
        recording_id=RECORDING,
        facts=facts,
    )

    assert compilation.validation.passed
    assert {item.request_id for item in compilation.requests} == {
        "query-request",
        "prepare-request",
        "submit-request",
        "withdraw-request",
        "option-request",
    }
    dispositions = {
        item.request_id: item.disposition for item in compilation.request_analyses
    }
    assert dispositions["query-request"] is RequestDisposition.MATERIALIZED
    assert dispositions["submit-request"] is RequestDisposition.MATERIALIZED
    assert dispositions["withdraw-request"] is RequestDisposition.MATERIALIZED
    assert dispositions["option-request"] is RequestDisposition.OPTION_SOURCE
    option_request = next(
        item for item in compilation.requests if item.request_id == "option-request"
    )
    assert option_request.response_body == [{"label": "差旅", "value": 2}]
    assert option_request.response_schema == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "value": {"type": "integer"},
            },
        },
    }

    query = next(item for item in compilation.requests if item.request_id == "query-request")
    assert query.query == (
        ("status", "pending"),
        ("status", "review"),
        ("owner", "me"),
    )
    for request_id in ("submit-request", "withdraw-request"):
        compiled = next(item for item in compilation.requests if item.request_id == request_id)
        assert compiled.body is None
        assert compiled.body_present is False

    # Temporal adjacency under one click is not causal evidence.  The GET is
    # retained losslessly, but it is not silently promoted into the mutating
    # capability without a response/control/wire dependency chain.
    submit_capability = next(cap for cap in compilation.capabilities if cap.name == "提交申请")
    assert submit_capability.request_ids == ("submit-request",)
    assert any(
        issue.code == "unbound_business_request"
        and issue.request_id == "prepare-request"
        for issue in compilation.validation.issues
    )
    assert submit_capability.risk_level is CapabilityRisk.L3
    assert submit_capability.explicit_confirmation is True

    withdraw = next(cap for cap in compilation.capabilities if cap.name == "撤回申请")
    assert withdraw.request_ids == ("withdraw-request",)
    assert withdraw.risk_level is CapabilityRisk.L4
    assert withdraw.execution_enabled is False
    assert withdraw.explicit_confirmation is True
    assert "option-request" not in {
        request_id for cap in compilation.capabilities for request_id in cap.request_ids
    }


def test_same_endpoint_from_different_actions_stays_separate() -> None:
    facts = (
        action(1, "approve", "同意"),
        request(2, "approve-request", "POST", "https://oa/api/task/decision", "approve"),
        action(3, "reject", "驳回"),
        request(4, "reject-request", "POST", "https://oa/api/task/decision", "reject"),
    )
    compilation = compile_recording(tenant=TENANT, recording_id=RECORDING, facts=facts)
    assert len(compilation.capabilities) == 2
    assert {cap.request_ids for cap in compilation.capabilities} == {
        ("approve-request",),
        ("reject-request",),
    }


def test_exact_response_input_match_without_control_causality_creates_no_relation() -> None:
    facts = (
        action(1, "query", "查询"),
        request(
            2,
            "query-request",
            "GET",
            "https://oa/api/tasks?mine=true",
            "query",
        ),
        RecordingFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=3,
            kind=FactKind.RESPONSE,
            action_id="query",
            payload={
                "request_id": "query-request",
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body_present": True,
                "body": {"records": [{"id": "task-123"}]},
            },
        ),
        action(4, "detail", "查看详情"),
        request(
            5,
            "detail-request",
            "GET",
            "https://oa/api/tasks/detail?id=task-123",
            "detail",
        ),
    )
    compilation = compile_recording(tenant=TENANT, recording_id=RECORDING, facts=facts)
    assert compilation.relations == ()


def test_scope_mismatch_fails_closed() -> None:
    foreign = request(1, "foreign", "POST", "https://oa/api/do", "do")
    foreign = foreign.model_copy(update={"tenant": "tenant-b"})
    try:
        compile_recording(tenant=TENANT, recording_id=RECORDING, facts=(foreign,))
    except ValueError as exc:
        assert "not tenant-a/recording-a" in str(exc)
    else:
        raise AssertionError("scope mismatch must fail")
