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
from dano_recording.capture.runtime import CaptureRuntime
from dano_recording.compiler.pipeline import prepare_recording_materials
from dano_recording.persistence.repository import OperationConflict
from dano_recording.domain.facts import ActionFact, FactKind, RecordingFact, RequestFact
from dano_recording.domain.recording import RecordingStatus
from dano_recording.executability import _fields, check_executability


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
async def test_finalize_freeze_retains_runtime_and_only_admits_explicit_dom_evidence(
    tmp_path,
) -> None:
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
    live = await service._get_live("tenant-a", created.recording_id)

    class Drains:
        async def drain(self, **_kwargs):
            return True

    class Scripts:
        _tasks = Drains()
        scripts = ()

    class Capture:
        @staticmethod
        def page_id(_page):
            return "page-a"

        @staticmethod
        def attach_page(_page):
            return "page-a"

    class Runtime:
        def __init__(self) -> None:
            self.tasks = Drains()
            self.network = type("Network", (), {"tasks": Drains()})()
            self.scripts = Scripts()
            self.browser = Capture()
            self.paused = 0
            self.resumed = 0
            self.closed = 0

        async def pause(self) -> None:
            self.paused += 1

        async def resume(self, _context) -> None:
            self.resumed += 1

        async def close(self) -> None:
            self.closed += 1

        async def collect_page_evidence(self, _page):
            live.ledger.emit(
                RecordingFact,
                kind=FactKind.DOM_CONTROL,
                page_id="page-a",
                payload={"type": "control", "selector": "#approval"},
            )
            return {"controls": (), "runtime_components": ()}

    runtime = Runtime()
    live.runtime = runtime  # type: ignore[assignment]
    live.capture = runtime.browser  # type: ignore[assignment]
    live.network = runtime.network  # type: ignore[assignment]
    live.scripts = runtime.scripts  # type: ignore[assignment]
    live.page = object()
    live.capture_active = True
    live.ledger.emit(
        RequestFact,
        request_id="before-freeze",
        method="GET",
        url="https://example.com/api/before",
    )
    await service._freeze_capture(live)
    boundary = live.capture_end_sequence
    assert boundary is not None
    assert live.runtime is runtime
    assert live.capture is runtime.browser
    assert runtime.paused == 1
    assert runtime.closed == 0

    live.ledger.emit(
        RequestFact,
        request_id="late-background",
        method="GET",
        url="https://example.com/api/late",
    )
    preliminary = prepare_recording_materials(
        tenant="tenant-a",
        recording_id=created.recording_id,
        facts=await service._capture_generation_facts(live),
    )
    await service._collect_evidence(live, preliminary)
    await service._drain_facts(live)
    frozen = await service._capture_generation_facts(live)
    assert [fact.request_id for fact in frozen if isinstance(fact, RequestFact)] == [
        "before-freeze"
    ]
    assert any(fact.kind is FactKind.DOM_CONTROL for fact in frozen)
    capture_records = live.capture_store.snapshot().records
    assert [
        record.payload.get("request_id")
        for record in capture_records
        if record.payload.get("request_id")
    ] == ["before-freeze"]
    assert any(record.payload.get("selector") == "#approval" for record in capture_records)

    class Page:
        @staticmethod
        async def screenshot(**_kwargs):
            await asyncio.Event().wait()

    live.started = True
    live.context = object()
    live.page = Page()  # type: ignore[assignment]
    await service._resume_capture(live)
    assert live.runtime is runtime
    assert live.network is runtime.network
    assert runtime.resumed == 1
    resumed_session = await service.repository.get_session(
        "tenant-a", created.recording_id
    )
    assert resumed_session.metadata.get("capture_end_sequence") is None
    assert resumed_session.metadata.get("analysis_fact_ids") == []
    await service.close()


@pytest.mark.asyncio
async def test_frozen_generation_boundary_survives_live_rehydration(tmp_path) -> None:
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
    live = await service._get_live("tenant-a", created.recording_id)

    class Runtime:
        async def pause(self) -> None:
            return None

        async def collect_page_evidence(self, _page):
            live.ledger.emit(
                RecordingFact,
                kind=FactKind.DOM_CONTROL,
                page_id="page-a",
                payload={"type": "control", "selector": "#approval"},
            )
            return {"controls": (), "runtime_components": ()}

    class Capture:
        @staticmethod
        def page_id(_page):
            return "page-a"

        @staticmethod
        def attach_page(_page):
            return "page-a"

    runtime = Runtime()
    live.runtime = runtime  # type: ignore[assignment]
    live.capture = Capture()  # type: ignore[assignment]
    live.page = object()
    live.capture_active = True
    live.ledger.emit(
        RequestFact,
        request_id="before-freeze",
        method="GET",
        url="https://example.com/api/before",
    )
    await service._freeze_capture(live)
    boundary = live.capture_end_sequence
    live.ledger.emit(
        RequestFact,
        request_id="late-background",
        method="GET",
        url="https://example.com/api/late",
    )
    preliminary = prepare_recording_materials(
        tenant="tenant-a",
        recording_id=created.recording_id,
        facts=await service._capture_generation_facts(live),
    )
    await service._collect_evidence(live, preliminary)
    await service._drain_facts(live)

    service.live.pop(("tenant-a", created.recording_id))
    restored = await service._get_live("tenant-a", created.recording_id)
    restored_facts = await service._capture_generation_facts(restored)

    assert restored.capture_end_sequence == boundary
    assert [
        fact.request_id for fact in restored_facts if isinstance(fact, RequestFact)
    ] == ["before-freeze"]
    assert any(fact.kind is FactKind.DOM_CONTROL for fact in restored_facts)
    assert "late-background" not in {
        record.payload.get("request_id")
        for record in restored.capture_store.snapshot().records
    }
    await service.close()


@pytest.mark.asyncio
async def test_recapture_does_not_project_previous_generation_scripts(tmp_path) -> None:
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
    live = await service._get_live("tenant-a", created.recording_id)
    runtime = CaptureRuntime(live.ledger)
    await runtime.scripts.add(
        script_id="old-script",
        target_id="old-page",
        url="https://example.com/old.js",
        source='const staleEnum = ["OLD_ONLY"]',
    )
    await runtime.pause()
    live.runtime = runtime
    live.capture = runtime.browser
    live.network = runtime.network
    live.scripts = runtime.scripts
    await service._drain_facts(live)

    await service._recapture_command("tenant-a", created.recording_id)
    compilation = prepare_recording_materials(
        tenant="tenant-a",
        recording_id=created.recording_id,
        facts=await service._capture_generation_facts(live),
    )
    await service._collect_evidence(live, compilation)

    assert runtime.scripts.scripts == ()
    assert live.capture_store.snapshot().scripts == ()
    await service.close()


@pytest.mark.asyncio
async def test_pi_state_exposes_stable_flow_target_for_semantic_operations(tmp_path) -> None:
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
    live = await service._get_live("tenant-a", created.recording_id)

    first = (await service._pi_state(created.recording_id))["pi_projection"]
    second = (await service._pi_state(created.recording_id))["pi_projection"]

    assert first["target_uuid"] == str(live.lineage_id)
    assert first["flow_target"] == {
        "kind": "flow",
        "target_uuid": str(live.lineage_id),
    }
    assert second["target_uuid"] == first["target_uuid"]
    assert "action" in first
    await service.close()


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
async def test_finalize_submit_then_refresh_commits_canonical_contract_and_starts_pi(
    tmp_path,
) -> None:
    service = RecordingApplication(pi_env={"PI_STUB": "1"}, artifact_root=tmp_path)
    await service.start()
    created = await service.create_session(
        "tenant-a",
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://example.com/leave",
            base_url="https://example.com",
            recording_mode="record_only",
        ),
    )
    recording_id = created.recording_id
    events: list[dict] = []
    pi_starts: list[tuple[str, str, int]] = []

    async def send(value: dict) -> None:
        events.append(value)

    async def capture_initial_pi(tenant: str, target: str, revision: int) -> None:
        pi_starts.append((tenant, target, revision))

    service._run_initial_pi = capture_initial_pi  # type: ignore[method-assign]
    await service.attach_socket("tenant-a", recording_id, send)
    live = await service._get_live("tenant-a", recording_id)
    live.ledger.emit(
        ActionFact,
        action_id="submit-action",
        action_type="click",
        label="提交",
        locator="#submit",
        payload={
            "causal_eligible": True,
            "evidence_origin": "server_dispatched",
        },
    )
    live.ledger.emit(
        RequestFact,
        action_id="submit-action",
        request_id="submit-process",
        method="POST",
        url="https://example.com/admin-api/oa/duty-leave/submit-process",
        request_body=None,
        request_body_present=False,
        response_status=200,
        response_body={"success": True},
    )
    live.ledger.emit(
        RequestFact,
        action_id="submit-action",
        request_id="refresh-page",
        method="GET",
        url="https://example.com/admin-api/oa/duty-leave/page?pageNo=1",
        response_status=200,
        response_body={"list": [], "total": 1},
    )
    await service._drain_facts(live)

    await service.handle_message(
        "tenant-a",
        recording_id,
        {
            "type": "finalize",
            "expected_revision": 0,
            "operation_id": "finalize-submit-refresh",
            "action": "submit_leave",
            "title": "提交请假",
        },
        send,
    )
    await service.wait_for_analysis("tenant-a", recording_id)
    await asyncio.sleep(0)

    revision = await service.repository.get_revision("tenant-a", recording_id)
    assert revision is not None
    assert revision.revision == 1
    snapshot = revision.snapshot
    steps = {step["request_id"]: step for step in snapshot["steps"]}
    assert set(steps) == {"submit-process", "refresh-page"}
    assert all(step["step_uuid"] for step in steps.values())
    matching_capabilities = [
        item
        for item in snapshot["capabilities"]
        if any(
            ref["request_id"] == "submit-process"
            for ref in item["request_refs"]
        )
    ]
    assert len(matching_capabilities) == 1, [
        (
            item.get("operation"),
            [ref.get("request_id") for ref in item.get("request_refs") or []],
            item.get("risk_level"),
        )
        for item in snapshot["capabilities"]
    ]
    capability = matching_capabilities[0]
    assert capability["capability_uuid"]
    assert capability["operation"] == "submit"
    assert capability["risk_level"] == "L3"
    assert capability["requires_human_confirm"] is True
    assert steps["submit-process"]["step_uuid"] in capability["step_uuids"]
    assert all(ref["step_uuid"] for ref in capability["request_refs"])
    assert all(item["capability_uuid"] for item in snapshot["capabilities"])
    assert {
        step_uuid
        for item in snapshot["capabilities"]
        for step_uuid in item["step_uuids"]
    } == {step["step_uuid"] for step in steps.values()}
    report = check_executability(snapshot)
    assert report["contract_faults"] == [], [
        {
            key: field.get(key)
            for key in (
                "field_uuid", "step_id", "step_uuid", "path", "wire_path",
                "source_binding", "value_provider", "required_contract",
                "axis_decisions",
            )
        }
        for field in _fields(snapshot, {})
    ]
    assert pi_starts == [("tenant-a", recording_id, 1)]
    final_event = next(item for item in events if item.get("type") == "flow_spec")
    assert final_event["full_spec"]["meta"].get("preview") is not True
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
    first_preview = next(
        item for item in events if item.get("type") == "deterministic_flow"
    )
    assert "check_report" not in first_preview
    assert first_preview["full_spec"]["capabilities"]
    assert all(
        capability["status"] == "provisional"
        for capability in first_preview["full_spec"]["capabilities"]
    )
    assert first_preview["full_spec"]["capabilities"][0]["request_refs"]

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
