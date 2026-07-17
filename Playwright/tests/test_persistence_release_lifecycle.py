from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dano_recording.api.protocol import CreateRecordingRequest
from dano_recording.bootstrap import RecordingApplication
from dano_recording.domain.operations import OperationStatus
from dano_recording.domain.recording import RecordingStatus
from dano_recording.persistence import InMemoryRecordingRepository


TENANT = "tenant-persistence-audit"


def _publishable_snapshot(recording_id: str) -> dict:
    return {
        "tenant": TENANT,
        "recording_id": recording_id,
        "revision": 1,
        "subsystem": "oa",
        "action": "list_items",
        "title": "Original",
        "start_url": "https://oa.example/app",
        "steps": [{
            "step_id": "step-a",
            "request_id": "request-a",
            "method": "GET",
            "path": "/items",
            "params": [],
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


async def _prepare_publish_service(tmp_path, repository=None):
    service = RecordingApplication(
        repository=repository,
        pi_env={"PI_STUB": "1"},
        artifact_root=tmp_path,
        evidence_hmac_secret=b"publish-restart-persistence-secret",
    )
    await service.start()
    created = await service.create_session(
        TENANT,
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://oa.example/app",
            base_url="https://oa.example",
        ),
    )
    await service._commit_snapshot(  # noqa: SLF001
        TENANT,
        created.recording_id,
        expected_revision=0,
        snapshot=_publishable_snapshot(created.recording_id),
        actor="test",
    )
    return service, created


def test_postgres_migration_enforces_durable_identity_and_revision_links() -> None:
    migration = (
        Path(__file__).resolve().parents[2] / "back" / "migrations" / "014_recording_v3.sql"
    ).read_text(encoding="utf-8")

    assert "UNIQUE (recording_id)" in migration
    assert "operation_id        TEXT        PRIMARY KEY" in migration
    assert "recording_operations_transition" in migration
    assert "terminal recording operation cannot be rewritten" in migration
    assert "recording_pi_sessions_identity" in migration
    assert "recording_artifact_revision_exists" in migration
    assert "recording_facts_immutable" in migration
    assert "recording_revisions_immutable" in migration
    assert "recording_pi_events_immutable" in migration
    assert "recording_artifacts_immutable" in migration


@pytest.mark.asyncio
async def test_recapture_persists_generation_and_reset_boundary_atomically(tmp_path) -> None:
    service = RecordingApplication(
        pi_env={"PI_STUB": "1"},
        artifact_root=tmp_path,
        evidence_hmac_secret=b"recapture-atomic-checkpoint-secret",
    )
    await service.start()
    created = await service.create_session(
        TENANT,
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://oa.example/app",
            base_url="https://oa.example",
        ),
    )
    live = await service._get_live(TENANT, created.recording_id)  # noqa: SLF001
    original_store = live.capture_store
    original_evidence = live.evidence
    original_reset_sequence = live.reset_sequence
    original_update = service.repository.update_session
    checkpoints: list[dict[str, int]] = []

    async def fail_checkpoint(tenant, recording_id, **kwargs):
        metadata = dict(kwargs.get("metadata") or {})
        if "capture_generation" in metadata:
            checkpoints.append(metadata)
            raise RuntimeError("checkpoint store unavailable")
        return await original_update(tenant, recording_id, **kwargs)

    service.repository.update_session = fail_checkpoint  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="checkpoint store unavailable"):
        await service._recapture_command(TENANT, created.recording_id)  # noqa: SLF001

    assert checkpoints == [{
        "capture_generation": 1,
        "reset_sequence": live.ledger.next_sequence,
        "capture_end_sequence": None,
        "analysis_fact_ids": [],
    }]
    assert live.capture_store is original_store
    assert live.capture_generation == 0
    assert live.reset_sequence == original_reset_sequence
    assert live.evidence is original_evidence

    service.repository.update_session = original_update  # type: ignore[method-assign]
    await service._recapture_command(TENANT, created.recording_id)  # noqa: SLF001
    session = await service.repository.get_session(TENANT, created.recording_id)
    assert session.metadata["capture_generation"] == 1
    assert session.metadata["reset_sequence"] == live.reset_sequence
    assert live.capture_generation == 1
    assert live.capture_store is not original_store
    await service.close()


@pytest.mark.asyncio
async def test_new_process_resumes_orphaned_started_publish_operation(tmp_path) -> None:
    repository = InMemoryRecordingRepository()
    first, created = await _prepare_publish_service(tmp_path / "first", repository)
    message = {
        "type": "publish_request",
        "operation_id": "publish-hard-crash",
        "expected_revision": 1,
        "action": "list_items",
        "title": "Original",
    }
    operation, replay = await first._begin_operation(  # noqa: SLF001
        TENANT,
        created.recording_id,
        message,
        kind="publish_request",
    )
    assert replay is None
    await repository.update_session(
        TENANT, created.recording_id, status=RecordingStatus.REVIEWING
    )
    await first.close()

    second = RecordingApplication(
        repository=repository,
        pi_env={"PI_STUB": "1"},
        artifact_root=tmp_path / "second",
        evidence_hmac_secret=b"publish-restart-persistence-secret",
    )

    class RecoveredPublisher:
        calls = 0

        async def publish(self, recording_id: str, revision: int) -> dict:
            self.calls += 1
            return {
                "published": True,
                "recovered": True,
                "recording_id": recording_id,
                "revision": revision,
                "asset_id": "asset-recovered",
                "version": 4,
                "content_hash": "sha256:release",
                "skill_id": "oa.list_items",
            }

    publisher = RecoveredPublisher()
    second.publisher = publisher  # type: ignore[assignment]
    await second.start()
    await second.resume_session(
        TENANT, created.recording_id, created.resume_token or ""
    )

    async def no_pi_sessions(*_args, **_kwargs) -> None:
        return None

    second._ensure_pi_sessions = no_pi_sessions  # type: ignore[method-assign]  # noqa: SLF001
    await second._publish_command(TENANT, created.recording_id, message)  # noqa: SLF001

    stored, was_created = await repository.register_operation(operation)
    assert was_created is False
    assert stored.status is OperationStatus.COMPLETED
    assert stored.result is not None
    assert stored.result["report"]["published"] is True
    assert stored.result["report"]["recovered"] is True
    assert publisher.calls == 1
    assert (
        await repository.get_session(TENANT, created.recording_id)
    ).status is RecordingStatus.PUBLISHED
    await second.close()


@pytest.mark.asyncio
async def test_active_publish_operation_is_not_reclaimed_in_same_process(tmp_path) -> None:
    service, created = await _prepare_publish_service(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingPublisher:
        calls = 0

        async def publish(self, recording_id: str, revision: int) -> dict:
            self.calls += 1
            entered.set()
            await release.wait()
            return {
                "published": True,
                "recording_id": recording_id,
                "revision": revision,
                "asset_id": "asset-one",
                "version": 1,
                "content_hash": "sha256:release",
                "skill_id": "oa.list_items",
            }

    publisher = BlockingPublisher()
    service.publisher = publisher  # type: ignore[assignment]

    async def no_pi_sessions(*_args, **_kwargs) -> None:
        return None

    service._ensure_pi_sessions = no_pi_sessions  # type: ignore[method-assign]  # noqa: SLF001
    message = {
        "type": "publish_request",
        "operation_id": "publish-active",
        "expected_revision": 1,
        "action": "list_items",
        "title": "Original",
    }
    first = asyncio.create_task(
        service._publish_command(TENANT, created.recording_id, message)  # noqa: SLF001
    )
    await entered.wait()
    await service._publish_command(TENANT, created.recording_id, message)  # noqa: SLF001

    assert publisher.calls == 1
    assert (
        await service.repository.get_session(TENANT, created.recording_id)
    ).status is RecordingStatus.REVIEWING
    assert message["operation_id"] in service._active_publish_operations  # noqa: SLF001

    release.set()
    await first
    assert message["operation_id"] not in service._active_publish_operations  # noqa: SLF001
    assert (
        await service.repository.get_session(TENANT, created.recording_id)
    ).status is RecordingStatus.PUBLISHED
    assert publisher.calls == 1
    await service.close()
