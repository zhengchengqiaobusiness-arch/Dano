from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dano_recording.api.auth import TicketError, WebSocketTicketManager
from dano_recording.api.decision_commands import (
    apply_edits,
    merge_pi_submission,
    rebase_user_decisions,
)
from dano_recording.api.protocol import CreateRecordingRequest
from dano_recording.app import install_recording_v3
from dano_recording.bootstrap import RecordingApplication, RecordingUnavailable
from dano_recording.persistence.repository import OperationConflict
from dano_recording.domain.facts import ActionFact, RequestFact
from dano_recording.domain.recording import RecordingStatus


def _workbench() -> dict:
    return {
        "tenant": "tenant-a",
        "recording_id": "rec-a",
        "revision": 1,
        "title": "Original",
        "steps": [{
            "step_id": "step-a",
            "request_id": "request-a",
            "method": "GET",
            "path": "/items",
            "params": [{
                "field_id": "field-a",
                "path": "query.kind",
                "key": "kind",
                "type": "string",
            }],
        }],
        "links": [],
        "capabilities": [{
            "capability_id": "cap-a",
            "name": "list_items",
            "step_ids": ["step-a"],
            "risk_level": "L1",
        }],
        "request_facts": {"requests": [{
            "request_id": "request-a",
            "method": "GET",
            "path": "/items",
            "disposition": "materialized",
        }]},
        "meta": {"recording_engine": "playwright_v3"},
    }


@pytest.mark.asyncio
async def test_websocket_tickets_are_recording_bound_and_single_use() -> None:
    tickets = WebSocketTicketManager(ttl_seconds=10)
    token, _ = await tickets.issue(tenant="tenant-a", recording_id="rec-a")
    with pytest.raises(TicketError):
        await tickets.consume(token, recording_id="rec-b")
    # A failed cross-recording attempt still burns the credential.
    with pytest.raises(TicketError):
        await tickets.consume(token, recording_id="rec-a")


@pytest.mark.asyncio
async def test_persistent_deployment_refuses_silent_memory_repository_fallback() -> None:
    service = RecordingApplication(persistent_repository_required=True)
    await service.start()
    with pytest.raises(RecordingUnavailable, match="refusing in-memory fallback"):
        await service.create_session(
            "tenant-a",
            CreateRecordingRequest(
                subsystem="oa",
                start_url="https://example.com/app",
                base_url="https://example.com",
            ),
        )
    await service.close()


def test_user_dimension_pins_survive_pi_merge_and_recompile() -> None:
    edited = apply_edits(_workbench(), [
        {"op": "update_flow", "field": "title", "value": "Human title"},
        {
            "op": "update",
            "step_id": "step-a",
            "param_path": "query.kind",
            "field": "type",
            "value": "enum",
        },
    ])
    merged = merge_pi_submission(edited, {
        "plan": {
            "title": "Pi title",
            "steps": [{
                "step_id": "step-a",
                "params": [{"field_id": "field-a", "path": "query.kind", "type": "integer"}],
            }],
        }
    })
    assert merged["title"] == "Human title"
    assert merged["steps"][0]["params"][0]["type"] == "enum"

    deterministic = _workbench()
    deterministic["title"] = "New compiler title"
    deterministic["steps"][0]["params"][0]["type"] = "boolean"
    rebased = rebase_user_decisions(edited, deterministic)
    assert rebased["title"] == "Human title"
    assert rebased["steps"][0]["params"][0]["type"] == "enum"


def test_removing_materialized_step_keeps_request_as_reviewable_fact_across_recompile() -> None:
    removed = apply_edits(_workbench(), [{"op": "remove_step", "step_id": "step-a"}])
    assert removed["steps"] == []
    row = removed["request_facts"]["requests"][0]
    assert row["disposition"] == "review_candidate"
    assert row["materialized_step_id"] is None

    rebased = rebase_user_decisions(removed, _workbench())
    assert rebased["steps"] == []
    assert rebased["request_facts"]["requests"][0]["disposition"] == "review_candidate"


def test_http_create_resume_and_ticket_websocket_contract() -> None:
    app = FastAPI()
    service = RecordingApplication(pi_env={"PI_STUB": "1"})

    async def tenant_resolver(key: str | None) -> str:
        assert key == "tenant-key"
        return "tenant-a"

    install_recording_v3(app, service=service, tenant_resolver=tenant_resolver)
    with TestClient(app) as client:
        created = client.post(
            "/recording-v3/sessions",
            headers={"X-Tenant-Key": "tenant-key"},
            json={
                "subsystem": "oa",
                "start_url": "https://example.com/workbench",
                "base_url": "https://example.com",
                "recording_mode": "record_only",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["recording_id"]
        assert body["resume_token"]
        with client.websocket_connect(
            f"/recording-v3/sessions/{body['recording_id']}/ws?ticket={body['websocket_ticket']}"
        ) as socket:
            socket.send_json({"type": "refresh_flow_spec"})
            event = socket.receive_json()
            assert event["type"] == "started"
            assert event["current_revision"] == 0

        resumed = client.post(
            f"/recording-v3/sessions/{body['recording_id']}/resume",
            headers={"X-Tenant-Key": "tenant-key"},
            json={"resume_token": body["resume_token"]},
        )
        assert resumed.status_code == 200
        assert resumed.json()["recording_id"] == body["recording_id"]
        assert resumed.json()["resume_token"] == body["resume_token"]


@pytest.mark.asyncio
async def test_bodyless_write_is_compiled_revisioned_and_resume_snapshot_is_stable(
    tmp_path,
    monkeypatch,
) -> None:
    import dano_recording.bootstrap as recording_bootstrap

    semantic_calls = {"planner": 0, "inference": 0}
    original_compile = recording_bootstrap.compile_recording
    original_integrate = recording_bootstrap.integrate_compilation_contracts

    def counted_compile(*args, **kwargs):
        semantic_calls["planner"] += 1
        return original_compile(*args, **kwargs)

    def counted_integrate(*args, **kwargs):
        semantic_calls["inference"] += 1
        return original_integrate(*args, **kwargs)

    monkeypatch.setattr(recording_bootstrap, "compile_recording", counted_compile)
    monkeypatch.setattr(
        recording_bootstrap,
        "integrate_compilation_contracts",
        counted_integrate,
    )
    service = RecordingApplication(
        pi_env={"PI_STUB": "1"},
        artifact_root=tmp_path,
    )
    await service.start()
    created = await service.create_session(
        "tenant-a",
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://example.com/app",
            base_url="https://example.com",
            recording_mode="record_only",
        ),
    )
    recording_id = created.recording_id
    events: list[dict] = []

    async def send(value: dict) -> None:
        events.append(value)

    await service.attach_socket("tenant-a", recording_id, send)
    live = await service._get_live("tenant-a", recording_id)
    live.ledger.emit(
        ActionFact,
        action_id="action-a",
        action_type="click",
        label="delete item",
        locator="#delete",
    )
    live.ledger.emit(
        RequestFact,
        action_id="action-a",
        request_id="request-delete",
        method="DELETE",
        url="https://example.com/api/items/42?dry=true",
        request_body=None,
        request_body_present=False,
    )
    await service._drain_facts(live)
    await service.handle_message(
        "tenant-a",
        recording_id,
        {"type": "finalize", "expected_revision": 0, "operation_id": "finalize-a"},
        send,
    )
    await service.wait_for_analysis("tenant-a", recording_id)
    revision = await service.repository.get_revision("tenant-a", recording_id)
    assert revision is not None
    assert revision.revision == 1
    rows = revision.snapshot["request_facts"]["requests"]
    assert len(rows) == 1
    assert rows[0]["request_id"] == "request-delete"
    assert rows[0]["disposition"]
    assert revision.snapshot["steps"][0]["method"] == "DELETE"
    assert revision.snapshot["steps"][0]["body"] is None
    assert revision.snapshot["steps"][0]["query"] == {"dry": "{{inputs.dry}}"}

    resumed = await service.resume_session(
        "tenant-a", recording_id, created.resume_token or ""
    )
    assert resumed.current_revision == 1
    assert resumed.snapshot is not None
    assert resumed.snapshot["full_spec"]["revision"] == 1
    assert semantic_calls == {"planner": 1, "inference": 1}

    # Give the stub Pi background task a chance to finish, then close cleanly.
    await asyncio.sleep(0.05)
    await service.detach_socket("tenant-a", recording_id, send)
    await service.close()


@pytest.mark.asyncio
async def test_finalize_runs_in_background_reports_progress_and_can_be_cancelled(tmp_path) -> None:
    service = RecordingApplication(pi_env={"PI_STUB": "1"}, artifact_root=tmp_path)
    await service.start()
    created = await service.create_session(
        "tenant-a",
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://example.com/app",
            base_url="https://example.com",
        ),
    )
    recording_id = created.recording_id
    events: list[dict] = []

    async def send(value: dict) -> None:
        events.append(value)

    await service.attach_socket("tenant-a", recording_id, send)
    live = await service._get_live("tenant-a", recording_id)
    live.ledger.emit(
        RequestFact,
        request_id="request-a",
        method="GET",
        url="https://example.com/api/items",
    )
    await service._drain_facts(live)
    entered = asyncio.Event()
    release = asyncio.Event()
    original_collect = service._collect_evidence

    async def slow_collect(owner, compilation):
        entered.set()
        await release.wait()
        return await original_collect(owner, compilation)

    service._collect_evidence = slow_collect  # type: ignore[method-assign]
    await service.handle_message(
        "tenant-a",
        recording_id,
        {"type": "finalize", "operation_id": "background-a", "expected_revision": 0},
        send,
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert await service.repository.get_revision("tenant-a", recording_id) is None
    assert any(item.get("type") == "analysis_started" for item in events)
    assert any(item.get("type") == "deterministic_flow" for item in events)

    await service.handle_message(
        "tenant-a",
        recording_id,
        {"type": "get_analysis_status"},
        send,
    )
    assert events[-1]["type"] == "analysis_status"
    assert events[-1]["state"] == "running"
    await service.handle_message(
        "tenant-a",
        recording_id,
        {"type": "cancel_analysis"},
        send,
    )
    assert live.analysis_task is None
    assert live.analysis_state == "cancelled"
    assert any(item.get("type") == "analysis_cancelled" for item in events)
    release.set()
    await service.detach_socket("tenant-a", recording_id, send)
    await service.close()


@pytest.mark.asyncio
async def test_published_asset_is_not_reported_failed_when_lifecycle_sync_needs_retry(tmp_path) -> None:
    async def lifecycle(_payload: dict) -> None:
        raise RuntimeError("lifecycle store temporarily unavailable")

    service = RecordingApplication(
        pi_env={"PI_STUB": "1"},
        artifact_root=tmp_path,
        lifecycle_callback=lifecycle,
    )

    class Publisher:
        async def publish(self, recording_id: str, revision: int) -> dict:
            return {
                "published": True,
                "recording_id": recording_id,
                "revision": revision,
                "asset_id": "asset-a",
                "version": 7,
                "content_hash": "sha256:published",
                "skill_id": "oa.list_items",
            }

    service.publisher = Publisher()
    await service.start()
    created = await service.create_session(
        "tenant-a",
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://example.com/app",
            base_url="https://example.com",
        ),
    )
    recording_id = created.recording_id
    snapshot = _workbench()
    snapshot.update({
        "tenant": "tenant-a",
        "recording_id": recording_id,
        "subsystem": "oa",
        "action": "list_items",
        "start_url": "https://example.com/app",
    })
    snapshot["request_facts"]["requests"][0]["request_id"] = "request-a"
    await service._commit_snapshot(
        "tenant-a",
        recording_id,
        expected_revision=0,
        snapshot=snapshot,
        actor="test",
    )
    events: list[dict] = []

    async def send(value: dict) -> None:
        events.append(value)

    await service.attach_socket("tenant-a", recording_id, send)
    await service._publish_command(
        "tenant-a",
        recording_id,
        {
            "type": "publish_request",
            "operation_id": "publish-a",
            "expected_revision": 1,
            "action": "list_items",
            "title": "Original",
        },
    )
    session = await service.repository.get_session("tenant-a", recording_id)
    assert session.status is RecordingStatus.PUBLISHED
    assert session.metadata["lifecycle_sync_pending"] is True
    result = next(item for item in reversed(events) if item.get("type") == "result")
    assert result["report"]["ok"] is True
    assert result["report"]["lifecycle_synced"] is False
    assert "lifecycle sync pending" in result["report"]["sync_warnings"][0]
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_point", ["complete_operation", "result_event"])
async def test_asset_commit_survives_post_commit_persistence_or_delivery_failure(
    tmp_path,
    fault_point: str,
) -> None:
    service = RecordingApplication(pi_env={"PI_STUB": "1"}, artifact_root=tmp_path)

    class Publisher:
        calls = 0

        async def publish(self, recording_id: str, revision: int) -> dict:
            self.calls += 1
            return {
                "published": True,
                "recording_id": recording_id,
                "revision": revision,
                "asset_id": "asset-committed",
                "version": 9,
                "content_hash": "sha256:committed",
                "skill_id": "oa.list_items",
            }

    publisher = Publisher()
    service.publisher = publisher
    await service.start()
    created = await service.create_session(
        "tenant-a",
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://example.com/app",
            base_url="https://example.com",
        ),
    )
    recording_id = created.recording_id
    snapshot = _workbench()
    snapshot.update({
        "tenant": "tenant-a",
        "recording_id": recording_id,
        "subsystem": "oa",
        "action": "list_items",
        "start_url": "https://example.com/app",
    })
    await service._commit_snapshot(
        "tenant-a",
        recording_id,
        expected_revision=0,
        snapshot=snapshot,
        actor="test",
    )
    events: list[dict] = []

    async def send(value: dict) -> None:
        events.append(value)

    await service.attach_socket("tenant-a", recording_id, send)
    message = {
        "type": "publish_request",
        "operation_id": f"publish-{fault_point}",
        "expected_revision": 1,
        "action": "list_items",
        "title": "Original",
    }

    if fault_point == "complete_operation":
        original_complete = service.repository.complete_operation
        failed = False

        async def fail_once(tenant, operation_id, *, result=None, error=None):
            nonlocal failed
            if operation_id == message["operation_id"] and result is not None and not failed:
                failed = True
                raise RuntimeError("operation store temporarily unavailable")
            return await original_complete(
                tenant, operation_id, result=result, error=error,
            )

        service.repository.complete_operation = fail_once  # type: ignore[method-assign]
    else:
        original_publish = service.events.publish
        failed = False

        async def fail_result_once(tenant, item_recording_id, payload):
            nonlocal failed
            if (
                payload.get("type") == "result"
                and payload.get("operation") == "publish_request"
                and not failed
            ):
                failed = True
                raise RuntimeError("event delivery temporarily unavailable")
            return await original_publish(tenant, item_recording_id, payload)

        service.events.publish = fail_result_once  # type: ignore[method-assign]

    await service._publish_command("tenant-a", recording_id, message)
    session = await service.repository.get_session("tenant-a", recording_id)
    assert session.status is RecordingStatus.PUBLISHED
    assert not any(
        item.get("report", {}).get("published") is False
        for item in events if item.get("type") == "result"
    )

    # Retrying the same idempotency key replays the committed success.  It
    # never rewrites the asset result as failed or returns "in progress".
    await service._publish_command("tenant-a", recording_id, message)
    session = await service.repository.get_session("tenant-a", recording_id)
    assert session.status is RecordingStatus.PUBLISHED
    replay = next(
        item for item in reversed(events)
        if item.get("type") == "result" and item.get("operation") == "publish_request"
    )
    assert replay["report"]["ok"] is True
    assert replay["report"]["published"] is True
    assert publisher.calls == 1
    await service.close()


@pytest.mark.asyncio
async def test_fast_fact_persistence_failure_cannot_disappear_before_finalize(tmp_path) -> None:
    service = RecordingApplication(pi_env={"PI_STUB": "1"}, artifact_root=tmp_path)
    await service.start()
    created = await service.create_session(
        "tenant-a",
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://example.com/app",
            base_url="https://example.com",
        ),
    )
    repository = service.repository
    original_append = repository.append_facts

    async def fail_append(*_args, **_kwargs):
        raise RuntimeError("database write failed immediately")

    repository.append_facts = fail_append  # type: ignore[method-assign]
    live = await service._get_live("tenant-a", created.recording_id)
    live.ledger.emit(
        ActionFact,
        action_id="action-fast-failure",
        action_type="click",
        label="save",
        locator="#save",
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not live.persistence_tasks
    with pytest.raises(RuntimeError, match="database write failed immediately"):
        await service._drain_facts(live)

    repository.append_facts = original_append  # type: ignore[method-assign]
    await original_append(
        "tenant-a", created.recording_id, tuple(live.ledger.snapshot())
    )
    live.persistence_failures.clear()
    await service.close()


@pytest.mark.asyncio
async def test_idempotency_hash_includes_flow_spec_payload(tmp_path) -> None:
    service = RecordingApplication(pi_env={"PI_STUB": "1"}, artifact_root=tmp_path)
    await service.start()
    created = await service.create_session(
        "tenant-a",
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://example.com/app",
            base_url="https://example.com",
        ),
    )
    first = {
        "type": "flow_replace",
        "operation_id": "replace-same-id",
        "expected_revision": 0,
        "flow_spec": {
            "steps": [],
            "links": [],
            "capabilities": [],
            "request_facts": {"requests": []},
        },
    }
    second = {
        **first,
        "flow_spec": {
            "steps": [{"step_id": "different"}],
            "links": [],
            "capabilities": [],
            "request_facts": {"requests": []},
        },
    }
    operation, replay = await service._begin_operation(
        "tenant-a", created.recording_id, first, kind="flow_replace"
    )
    assert operation.operation_id == "replace-same-id"
    assert replay is None
    with pytest.raises(OperationConflict, match="different request"):
        await service._begin_operation(
            "tenant-a", created.recording_id, second, kind="flow_replace"
        )
    await service.close()
