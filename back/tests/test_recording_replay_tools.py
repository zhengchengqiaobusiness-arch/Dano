from __future__ import annotations

import json

import pytest
from aiohttp import web

from dano.execution.page import value_tracing
from dano.execution.page.replay import perturb_replay, replay_request
from dano.execution.page.value_tracing import discover_value_links
from dano.execution.page.verification_log import (
    _clear_verifications_for_tests,
    get_verification,
    record_verification,
)
from dano.agent_tools.tools import TOOLS
from dano.execution.page.flow_spec import FlowSpec
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

    app = web.Application()
    app.router.add_post("/state", set_state)
    app.router.add_get("/state", get_state)
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


def test_verification_ids_are_executor_generated_and_defensively_copied():
    verification_id = record_verification(
        kind="enum_snapshot",
        subject={"request_id": "req-1"},
        evidence={"options": ["A"]},
    )
    first = get_verification(verification_id)
    first["evidence"]["options"].append("B")
    assert get_verification(verification_id)["evidence"]["options"] == ["A"]
    with pytest.raises(ValueError):
        record_verification(kind="invented", subject={}, evidence={})


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
        evidence={"Authorization": "Bearer never-return-this-token"},
    )
    await session.add_verifications([verification_id])
    stored = session.current_flow_spec().meta["verification_log"][0]
    assert stored["verification_id"] == verification_id
    assert stored["evidence"]["Authorization"] == "***"


def test_recording_execution_tools_are_registered():
    assert {"replay_request", "perturb_replay", "list_link_candidates", "get_verification"} <= TOOLS.keys()
