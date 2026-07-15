from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from dano_recording.domain._base import utc_now
from dano_recording.domain.facts import FactKind, RecordingFact
from dano_recording.domain.operations import RecordingOperation
from dano_recording.domain.pi import PiEvent, PiRole, PiSessionMetadata
from dano_recording.domain.recording import RecordingSession, RecordingStatus
from dano_recording.domain.revisions import RecordingArtifact
from dano_recording.persistence import (
    ImmutableFactConflict,
    InMemoryRecordingRepository,
    OperationConflict,
    RecordingNotFound,
    RevisionConflict,
    TenantIsolationError,
)


@pytest.fixture
async def repository() -> InMemoryRecordingRepository:
    repo = InMemoryRecordingRepository()
    await repo.create_session(RecordingSession(tenant="tenant-a", recording_id="rec-a"))
    return repo


@pytest.mark.asyncio
async def test_tenant_isolation(repository: InMemoryRecordingRepository) -> None:
    with pytest.raises(TenantIsolationError):
        await repository.get_session("tenant-b", "rec-a")


@pytest.mark.asyncio
async def test_facts_are_append_only_and_append_is_idempotent(
    repository: InMemoryRecordingRepository,
) -> None:
    fact = RecordingFact(
        fact_id="fact-a",
        tenant="tenant-a",
        recording_id="rec-a",
        sequence=1,
        kind=FactKind.PAGE,
        payload={"url": "https://example.test"},
    )
    assert await repository.append_facts("tenant-a", "rec-a", (fact,)) == 1
    assert await repository.append_facts("tenant-a", "rec-a", (fact,)) == 0
    changed = fact.model_copy(update={"payload": {"url": "https://evil.test"}})
    with pytest.raises(ImmutableFactConflict):
        await repository.append_facts("tenant-a", "rec-a", (changed,))


@pytest.mark.asyncio
async def test_revision_uses_optimistic_lock(repository: InMemoryRecordingRepository) -> None:
    first = await repository.commit_revision(
        "tenant-a",
        "rec-a",
        expected_revision=0,
        snapshot={"title": "first"},
        actor="operator",
    )
    assert first.revision == 1
    assert (await repository.get_session("tenant-a", "rec-a")).current_revision == 1
    with pytest.raises(RevisionConflict) as error:
        await repository.commit_revision(
            "tenant-a",
            "rec-a",
            expected_revision=0,
            snapshot={"title": "stale"},
            actor="planner",
        )
    assert error.value.actual == 1


@pytest.mark.asyncio
async def test_session_update_cannot_overwrite_repository_revision(
    repository: InMemoryRecordingRepository,
) -> None:
    await repository.commit_revision(
        "tenant-a",
        "rec-a",
        expected_revision=0,
        snapshot={"title": "first"},
        actor="operator",
    )
    lease = utc_now() + timedelta(minutes=2)
    updated = await repository.update_session(
        "tenant-a",
        "rec-a",
        status=RecordingStatus.RECORDING,
        browser_lease_until=lease,
        metadata={"browser": "chromium"},
    )
    assert updated.status is RecordingStatus.RECORDING
    assert updated.browser_lease_until == lease
    assert updated.metadata == {"browser": "chromium"}
    assert updated.current_revision == 1

    cleared = await repository.update_session(
        "tenant-a", "rec-a", browser_lease_until=None, metadata={"resumed": True}
    )
    assert cleared.browser_lease_until is None
    assert cleared.metadata == {"browser": "chromium", "resumed": True}
    assert cleared.current_revision == 1


@pytest.mark.asyncio
async def test_operation_id_is_globally_idempotent(
    repository: InMemoryRecordingRepository,
) -> None:
    operation = RecordingOperation(
        tenant="tenant-a",
        operation_id="operation-1",
        recording_id="rec-a",
        kind="commit_decision",
        request_hash="hash-a",
    )
    stored, created = await repository.register_operation(operation)
    assert created is True
    assert stored == operation
    replay, created = await repository.register_operation(operation)
    assert created is False
    assert replay == operation

    conflicting = operation.model_copy(update={"request_hash": "hash-b"})
    with pytest.raises(OperationConflict):
        await repository.register_operation(conflicting)

    await repository.create_session(RecordingSession(tenant="tenant-b", recording_id="rec-b"))
    cross_tenant = operation.model_copy(update={"tenant": "tenant-b", "recording_id": "rec-b"})
    with pytest.raises(TenantIsolationError):
        await repository.register_operation(cross_tenant)

    completed = await repository.complete_operation(
        "tenant-a", "operation-1", result={"revision": 1}
    )
    replayed = await repository.complete_operation(
        "tenant-a", "operation-1", result={"revision": 1}
    )
    assert replayed == completed
    with pytest.raises(OperationConflict):
        await repository.complete_operation(
            "tenant-a", "operation-1", result={"revision": 2}
        )


@pytest.mark.asyncio
async def test_concurrent_operation_completion_is_compare_and_set(
    repository: InMemoryRecordingRepository,
) -> None:
    operation = RecordingOperation(
        tenant="tenant-a",
        operation_id="operation-race",
        recording_id="rec-a",
        kind="publish_request",
        request_hash="hash-race",
    )
    await repository.register_operation(operation)

    outcomes = await asyncio.gather(
        repository.complete_operation(
            "tenant-a", operation.operation_id, result={"asset_id": "asset-a"},
        ),
        repository.complete_operation(
            "tenant-a", operation.operation_id, result={"asset_id": "asset-b"},
        ),
        return_exceptions=True,
    )

    completed = [item for item in outcomes if isinstance(item, RecordingOperation)]
    conflicts = [item for item in outcomes if isinstance(item, OperationConflict)]
    assert len(completed) == 1
    assert len(conflicts) == 1
    replay = await repository.complete_operation(
        "tenant-a", operation.operation_id, result=completed[0].result,
    )
    assert replay == completed[0]


@pytest.mark.asyncio
async def test_pi_and_artifact_lists_are_tenant_scoped(
    repository: InMemoryRecordingRepository,
) -> None:
    pi_session = PiSessionMetadata(
        tenant="tenant-a",
        recording_id="rec-a",
        pi_session_id="pi-planner",
        role=PiRole.PLANNER,
        model_id="model-a",
    )
    await repository.save_pi_session(pi_session)
    event = PiEvent(
        tenant="tenant-a",
        recording_id="rec-a",
        pi_session_id="pi-planner",
        event_type="tool_call",
        payload={"tool": "get_recording_state"},
    )
    await repository.append_pi_event(event)
    await repository.append_pi_event(event)
    with pytest.raises(ImmutableFactConflict):
        await repository.append_pi_event(
            event.model_copy(update={"payload": {"tool": "invented"}}, deep=True)
        )
    await repository.create_session(
        RecordingSession(tenant="tenant-b", recording_id="rec-b")
    )
    with pytest.raises(TenantIsolationError):
        await repository.append_pi_event(
            event.model_copy(
                update={"tenant": "tenant-b", "recording_id": "rec-b"}, deep=True,
            )
        )
    artifact = RecordingArtifact(
        artifact_id="artifact-a",
        tenant="tenant-a",
        recording_id="rec-a",
        revision=0,
        kind="screenshot",
        content_hash="hash-a",
    )
    await repository.save_artifact(artifact)
    with pytest.raises(ImmutableFactConflict):
        await repository.save_artifact(
            artifact.model_copy(update={"metadata": {"changed": True}}, deep=True)
        )

    assert await repository.list_pi_sessions("tenant-a", "rec-a") == (pi_session,)
    assert await repository.list_pi_events("tenant-a", "rec-a") == (event,)
    assert await repository.list_artifacts(
        "tenant-a", "rec-a", revision=0, kind="screenshot"
    ) == (artifact,)
    with pytest.raises(TenantIsolationError):
        await repository.list_pi_sessions("tenant-b", "rec-a")


@pytest.mark.asyncio
async def test_pi_session_identity_is_global_and_events_require_exact_scope(
    repository: InMemoryRecordingRepository,
) -> None:
    planner = PiSessionMetadata(
        tenant="tenant-a",
        recording_id="rec-a",
        pi_session_id="pi-global",
        role=PiRole.PLANNER,
        model_id="model-a",
    )
    stored = await repository.save_pi_session(planner)
    advanced = planner.model_copy(update={
        "status": "idle",
        "last_revision": 0,
        "metadata": {"resumed": True},
        "updated_at": utc_now(),
    })
    updated = await repository.save_pi_session(advanced)
    assert updated.created_at == stored.created_at
    assert updated.metadata == {"resumed": True}

    with pytest.raises(ImmutableFactConflict):
        await repository.save_pi_session(
            planner.model_copy(update={"role": PiRole.SECURITY})
        )

    await repository.create_session(
        RecordingSession(tenant="tenant-b", recording_id="rec-b")
    )
    with pytest.raises(TenantIsolationError):
        await repository.save_pi_session(
            planner.model_copy(update={"tenant": "tenant-b", "recording_id": "rec-b"})
        )
    with pytest.raises(TenantIsolationError):
        await repository.append_pi_event(PiEvent(
            tenant="tenant-b",
            recording_id="rec-b",
            pi_session_id="pi-global",
            event_type="resume",
        ))
    with pytest.raises(RecordingNotFound):
        await repository.append_pi_event(PiEvent(
            tenant="tenant-a",
            recording_id="rec-a",
            pi_session_id="pi-missing",
            event_type="resume",
        ))


@pytest.mark.asyncio
async def test_artifact_requires_real_revision_and_natural_replay_is_idempotent(
    repository: InMemoryRecordingRepository,
) -> None:
    orphan = RecordingArtifact(
        artifact_id="artifact-orphan",
        tenant="tenant-a",
        recording_id="rec-a",
        revision=3,
        kind="published_page_script",
        content_hash="sha256:release",
        storage_ref="asset-a",
    )
    with pytest.raises(RevisionConflict):
        await repository.save_artifact(orphan)

    await repository.commit_revision(
        "tenant-a",
        "rec-a",
        expected_revision=0,
        snapshot={"revision": 1},
        actor="test",
    )
    artifact = orphan.model_copy(update={"artifact_id": "artifact-a", "revision": 1})
    stored = await repository.save_artifact(artifact)
    replay = await repository.save_artifact(
        artifact.model_copy(update={"artifact_id": "artifact-retry", "created_at": utc_now()})
    )
    assert replay == stored
    assert await repository.list_artifacts("tenant-a", "rec-a") == (stored,)

    with pytest.raises(ImmutableFactConflict):
        await repository.save_artifact(
            artifact.model_copy(update={
                "artifact_id": "artifact-conflict",
                "metadata": {"version": 2},
            })
        )
