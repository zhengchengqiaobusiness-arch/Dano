from __future__ import annotations

import json

import pytest
from aiohttp import web

from dano.execution.page import value_tracing
from dano.execution.page.replay import perturb_replay, replay_request, verify_dependency
from dano.execution.page.value_tracing import discover_response_key_maps, discover_value_links
from dano.execution.page.verification_log import (
    _clear_verifications_for_tests,
    find_verification,
    get_verification,
    record_verification,
)
from dano.agent_tools.tools import TOOLS
from dano.execution.page.flow_spec import FlowLink, FlowSpec, FlowStep, RequestFact, RequestFacts
from dano.execution.page.recording_live import dependency_link_signature
from dano.onboarding.recording_pi import RecordingPiSession


@pytest.fixture(autouse=True)
def _clean_verification_log():
    _clear_verifications_for_tests()
    yield
    _clear_verifications_for_tests()


@pytest.fixture
async def replay_server():
    state = {"id": "123456"}

    async def set_state(request):
        payload = await request.json()
        state["id"] = str(payload["id"])
        return web.json_response({
            "data": {"id": state["id"]},
            "echo": request.headers.get("Authorization"),
            "accessToken": "server-secret-token-value",
        })

    async def get_state(_request):
        return web.json_response({"data": {"id": state["id"]}})

    async def http_failure(_request):
        return web.json_response({"code": 400, "message": "bad request"}, status=400)

    async def business_failure(_request):
        return web.json_response({"code": 500, "message": "business rejected"})

    async def approval_detail(_request):
        return web.json_response({"data": {"activityNodes": [
            {"id": "Activity_runtime_leader", "name": "领导审批"},
            {"id": "Activity_runtime_hr", "name": "HR审批"},
        ]}})

    async def submit_approval(request):
        return web.json_response({"code": 0, "data": await request.json()})

    app = web.Application()
    app.router.add_post("/state", set_state)
    app.router.add_get("/state", get_state)
    app.router.add_get("/http-failure", http_failure)
    app.router.add_post("/http-failure", http_failure)
    app.router.add_get("/business-failure", business_failure)
    app.router.add_get("/approval-detail", approval_detail)
    app.router.add_post("/submit-approval", submit_approval)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_replay_request_uses_existing_executor_and_redacts_credentials(replay_server):
    request = {
        "request_id": "req-write",
        "method": "POST",
        "url": f"{replay_server}/state",
        "headers": {"content-type": "application/json"},
        "content_type": "application/json",
        "post_data": '{"id":"123456"}',
        "response_json": {"data": {"id": "123456"}},
    }
    result = await replay_request(
        request,
        overrides={"body": {"id": "654321"}},
        auth_headers={"Authorization": "Bearer live-secret-token"},
    )

    assert result["ok"] is True
    assert result["response"]["data"]["id"] == "654321"
    assert result["response"]["accessToken"] == "***"
    assert "live-secret-token" not in json.dumps(result)
    record = get_verification(result["verification_id"])
    assert record["kind"] == "write_execute"
    assert record["status"] == "passed"
    assert record["subject"]["request_id"] == "req-write"


@pytest.mark.asyncio
async def test_link_candidate_to_perturb_replay_integration(replay_server):
    chain = [
        {
            "request_id": "req-source",
            "sequence": 1,
            "method": "POST",
            "url": f"{replay_server}/state",
            "content_type": "application/json",
            "post_data": '{"id":"123456"}',
            "response_json": {"data": {"id": "123456"}},
        },
        {
            "request_id": "req-readback",
            "sequence": 2,
            "method": "GET",
            "url": f"{replay_server}/state?id=123456",
            "query": {"id": "123456"},
            "response_json": {"data": {"id": "123456"}},
        },
    ]
    candidates = discover_value_links(chain)
    assert any(
        item["source_request_id"] == "req-source"
        and item["target_request_id"] == "req-readback"
        and item["target_path"] == "query.id"
        for item in candidates
    )

    result = await perturb_replay(chain, perturb={"body": {"id": "654321"}}, auth_headers={})
    assert result["replays"][1]["response"]["data"]["id"] == "654321"
    assert any(item["request_id"] == "req-readback" and item["path"] == "response.data.id" for item in result["linked_paths"])
    assert get_verification(result["verification_id"])["kind"] == "perturb_link"


def test_discover_value_links_covers_inputs_and_filters_weak_or_sensitive_values():
    source = {
        "request_id": "read",
        "sequence": 1,
        "response_json": {
            "data": {
                "internalId": "JOB-998877",
                "numericId": "123456",
                "timestamp": "1786364000000",
                "accessToken": "TOKEN-998877",
                "enum": "2",
            }
        },
    }
    target = {
        "request_id": "write",
        "sequence": 2,
        "url": "https://example.test/jobs/JOB-998877?item=123456",
        "query": {"item": "123456", "when": "1786364000000"},
        "headers": {"X-Job": "JOB-998877", "Authorization": "TOKEN-998877"},
        "post_data": {"jobId": "JOB-998877", "kind": "2"},
    }
    links = discover_value_links([source, target])
    paths = {item["target_path"] for item in links}
    assert {"url_path[1]", "query.item", "body.jobId", "headers.X-Job"} <= paths
    assert "query.when" not in paths
    assert "headers.Authorization" not in paths
    assert all("TOKEN-998877" not in json.dumps(item) for item in links)


def test_discover_value_links_scans_each_request_inputs_once(monkeypatch):
    original = value_tracing._input_leaves
    calls = 0

    def counted(request):
        nonlocal calls
        calls += 1
        return original(request)

    monkeypatch.setattr(value_tracing, "_input_leaves", counted)
    requests = [
        {
            "request_id": f"req-{index}",
            "sequence": index,
            "url": "https://example.test/items",
            "query": ({"source": f"JOB-{index - 1:03d}-998877"} if index else {}),
            "response_json": {"jobId": f"JOB-{index:03d}-998877"},
        }
        for index in range(20)
    ]

    links = value_tracing.discover_value_links(requests)

    assert len(links) == 19
    assert calls <= len(requests)


def test_discover_response_key_maps_requires_exact_unique_response_keys():
    requests = [
        {
            "request_id": "req-detail",
            "sequence": 1,
            "response_json": {"data": {"activityNodes": [
                {"id": "Activity_leader", "name": "领导审批"},
                {"id": "Activity_hr", "name": "人力审批"},
            ]}},
        },
        {
            "request_id": "req-submit",
            "sequence": 2,
            "post_data": {"startUserSelectAssignees": {
                "Activity_leader": [501],
                "Activity_hr": [502],
            }},
        },
    ]

    assert discover_response_key_maps(requests) == [{
        "kind": "response_key_map",
        "source_request_id": "req-detail",
        "source_collection_path": "data.activityNodes",
        "source_key_path": "id",
        "source_label_path": "name",
        "target_request_id": "req-submit",
        "target_container_path": "body.startUserSelectAssignees",
        "recorded_key_count": 2,
        "confidence": 0.99,
    }]

    changed = json.loads(json.dumps(requests))
    changed[1]["post_data"]["startUserSelectAssignees"] = {"Activity_stale": [501]}
    assert discover_response_key_maps(changed) == []


def test_discover_response_key_maps_accepts_the_ordered_used_subset_of_response_rows():
    requests = [
        {
            "request_id": "req-detail",
            "sequence": 1,
            "response_json": {"data": {"activityNodes": [
                {"id": "Event_start", "name": "发起人"},
                {"id": "Activity_leader", "name": "领导审批"},
                {"id": "Activity_hr", "name": "人力审批"},
                {"id": "Event_end", "name": "结束"},
            ]}},
        },
        {
            "request_id": "req-submit",
            "sequence": 2,
            "post_data": {"assignees": {
                "Activity_leader": [501],
                "Activity_hr": [502],
            }},
        },
    ]

    [candidate] = discover_response_key_maps(requests)

    assert candidate["source_request_id"] == "req-detail"
    assert candidate["target_container_path"] == "body.assignees"
    assert candidate["recorded_key_count"] == 2


def test_verification_ids_are_executor_generated_and_defensively_copied():
    verification_id = record_verification(
        kind="enum_snapshot",
        subject={"request_id": "req-1"},
        status="passed",
        evidence={"options": ["A"]},
    )
    first = get_verification(verification_id)
    first["evidence"]["options"].append("B")
    assert get_verification(verification_id)["evidence"]["options"] == ["A"]
    with pytest.raises(ValueError):
        record_verification(kind="invented", subject={}, status="passed", evidence={})


def test_legacy_verification_without_status_is_inconclusive():
    record = find_verification("legacy", [{
        "verification_id": "legacy",
        "kind": "verify_read",
        "subject": {},
        "evidence": {"passed": True},
    }])

    assert record["status"] == "inconclusive"
    assert "no explicit status" in record["failure_reason"]


def test_new_verification_requires_an_explicit_status():
    with pytest.raises(TypeError, match="status"):
        record_verification(kind="enum_snapshot", subject={}, evidence={})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "expected_reason"),
    [("/http-failure", "HTTP 400"), ("/business-failure", "application result")],
)
async def test_replay_http_or_business_failure_never_mints_passed_evidence(
    replay_server, path, expected_reason,
):
    result = await replay_request(
        {"request_id": "req-fail", "method": "GET", "url": replay_server + path},
        auth_headers={},
    )

    assert result["ok"] is False
    record = get_verification(result["verification_id"])
    assert record["status"] == "failed"
    assert expected_reason in record["failure_reason"]


def _dependency_spec(target_url: str) -> tuple[FlowSpec, list[dict]]:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="source",
                method="GET",
                path="/state",
                source_meta={"request_id": "req-source"},
            ),
            FlowStep(
                step_id="target",
                method="POST",
                path="/state",
                source_meta={"request_id": "req-target"},
            ),
        ],
        links=[FlowLink(
            link_id="link-state-id",
            source_step_id="source",
            source_path="response.data.id",
            target_step_id="target",
            target_path="body.id",
            evidence={"source_request_id": "req-source", "target_request_id": "req-target"},
            meta={"kind": "value", "verified": False},
        )],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-source", method="GET", path="/state"),
            RequestFact(request_id="req-target", method="POST", path="/state"),
        ]),
    )
    captured = [
        {"request_id": "req-source", "method": "GET", "url": target_url.rsplit("/", 1)[0] + "/state"},
        {
            "request_id": "req-target",
            "method": "POST",
            "url": target_url,
            "content_type": "application/json",
            "post_data": '{"id":"recorded-id"}',
        },
    ]
    return spec, captured


@pytest.mark.asyncio
async def test_verify_dependency_uses_flowspec_paths_and_executor_signature(replay_server):
    spec, captured = _dependency_spec(replay_server + "/state")

    result = await verify_dependency(
        spec,
        "link-state-id",
        captured,
        auth_headers={},
    )

    assert result["ok"] is True
    assert result["status"] == "passed"
    record = get_verification(result["verification_id"])
    assert record["kind"] == "dependency_execute"
    assert record["status"] == "passed"
    assert record["subject"] == {
        "link_id": "link-state-id",
        "signature": dependency_link_signature(spec.links[0]),
        "kind": "value",
        "source_request_id": "req-source",
        "target_request_id": "req-target",
    }
    assert record["evidence"]["injection_equal"] is True


@pytest.mark.asyncio
async def test_verify_dependency_target_failure_never_confirms_evidence(replay_server):
    spec, captured = _dependency_spec(replay_server + "/http-failure")

    result = await verify_dependency(
        spec,
        "link-state-id",
        captured,
        auth_headers={},
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    record = get_verification(result["verification_id"])
    assert record["status"] == "failed"
    assert "HTTP 400" in record["failure_reason"]


@pytest.mark.asyncio
async def test_verify_dependency_executes_response_key_map_from_recorded_slots(replay_server):
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="detail", method="GET", path="/approval-detail",
                source_meta={"request_id": "req-detail"},
            ),
            FlowStep(
                step_id="submit", method="POST", path="/submit-approval",
                source_meta={"request_id": "req-submit"},
            ),
        ],
        links=[FlowLink(
            link_id="approval-map",
            kind="response_key_map",
            source_step_id="detail",
            source_path="data.activityNodes",
            source_collection_path="data.activityNodes",
            source_key_path="id",
            source_label_path="name",
            target_step_id="submit",
            target_path="startUserSelectAssignees",
            target_container_path="startUserSelectAssignees",
            value_binding={
                "kind": "caller_map_by_label",
                "input_field": "approvers",
                "value_shape": "single_item_list",
            },
            evidence={"source_request_id": "req-detail", "target_request_id": "req-submit"},
        )],
    )
    captured = [
        {"request_id": "req-detail", "method": "GET", "url": replay_server + "/approval-detail"},
        {
            "request_id": "req-submit", "method": "POST",
            "url": replay_server + "/submit-approval", "content_type": "application/json",
            "post_data": json.dumps({"startUserSelectAssignees": {
                "Activity_recorded_leader": [160],
                "Activity_recorded_hr": [159],
            }}),
        },
    ]

    result = await verify_dependency(spec, "approval-map", captured, auth_headers={})

    assert result["status"] == "passed"
    record = get_verification(result["verification_id"])
    assert record["status"] == "passed"
    assert record["evidence"]["source_labels"] == ["领导审批", "HR审批"]
    assert record["evidence"]["caller_map"] == {"领导审批": 160, "HR审批": 159}
    assert record["evidence"]["target"]["response"]["data"]["startUserSelectAssignees"] == {
        "Activity_runtime_leader": [160],
        "Activity_runtime_hr": [159],
    }


@pytest.mark.asyncio
async def test_recording_session_persists_executor_verification_in_flow_spec():
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="subsystem",
        recording_id="recording_" + "a" * 32,
    )
    session.bind_flow_spec(FlowSpec(flow_id="flow"))
    verification_id = record_verification(
        kind="verify_read",
        subject={"request_id": "read"},
        status="passed",
        evidence={"Authorization": "Bearer never-return-this-token"},
    )
    await session.add_verifications([verification_id])
    stored = session.current_flow_spec().meta["verification_log"][0]
    assert stored["verification_id"] == verification_id
    assert stored["evidence"]["Authorization"] == "***"


def test_recording_execution_tools_are_registered():
    assert {
        "replay_request", "perturb_replay", "verify_dependency",
        "list_link_candidates", "get_verification",
    } <= TOOLS.keys()
