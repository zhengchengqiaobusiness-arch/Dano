from __future__ import annotations

from typing import Any

import pytest

from dano_recording.domain._base import utc_now
from dano_recording.domain.operations import OperationStatus, RecordingOperation
from dano_recording.domain.pi import PiRole, PiSessionMetadata
from dano_recording.domain.revisions import RecordingArtifact
from dano_recording.persistence.postgres import AsyncpgRecordingRepository
from dano_recording.persistence.repository import (
    OperationConflict,
    RevisionConflict,
    TenantIsolationError,
)


def _row(operation: RecordingOperation) -> dict[str, Any]:
    return {
        "tenant": operation.tenant,
        "operation_id": operation.operation_id,
        "recording_id": operation.recording_id,
        "kind": operation.kind,
        "request_hash": operation.request_hash,
        "status": operation.status.value,
        "result": operation.result,
        "error": operation.error,
        "created_at": operation.created_at,
        "updated_at": operation.updated_at,
    }


class _Connection:
    def __init__(self, concurrent: RecordingOperation) -> None:
        self.started = _row(concurrent.model_copy(update={
            "status": OperationStatus.STARTED,
            "result": None,
            "updated_at": utc_now(),
        }))
        self.concurrent = _row(concurrent)
        self.selects = 0
        self.cas_sql = ""

    async def fetchrow(self, sql: str, *_args: Any) -> Any:
        normalized = " ".join(sql.split())
        if normalized.startswith("UPDATE recording_operations"):
            self.cas_sql = normalized
            return None
        if normalized.startswith("SELECT * FROM recording_operations"):
            self.selects += 1
            return self.started if self.selects == 1 else self.concurrent
        raise AssertionError(normalized)


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _PersistenceConnection:
    def __init__(
        self,
        *,
        pi_row: dict[str, Any] | None = None,
        artifact_row: dict[str, Any] | None = None,
        revision_exists: bool = True,
    ) -> None:
        self.pi_row = pi_row
        self.artifact_row = artifact_row
        self.revision_exists = revision_exists
        self.insert_attempted = False

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def fetchrow(self, sql: str, *_args: Any) -> Any:
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT * FROM recording_sessions"):
            return {"tenant": "tenant-a", "recording_id": "recording-a", "current_revision": 1}
        if normalized.startswith("INSERT INTO recording_pi_sessions"):
            self.insert_attempted = True
            return None
        if normalized.startswith("SELECT * FROM recording_pi_sessions"):
            return self.pi_row
        if normalized.startswith("INSERT INTO recording_artifacts"):
            self.insert_attempted = True
            return None
        if normalized.startswith("SELECT * FROM recording_artifacts WHERE artifact_id"):
            return None
        if normalized.startswith("SELECT * FROM recording_artifacts"):
            return self.artifact_row
        raise AssertionError(normalized)

    async def fetchval(self, sql: str, *_args: Any) -> Any:
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT 1 FROM recording_revisions"):
            return 1 if self.revision_exists else None
        if normalized.startswith("SELECT tenant FROM recording_sessions"):
            return "tenant-a"
        raise AssertionError(normalized)


@pytest.mark.asyncio
async def test_postgres_completion_cas_accepts_only_identical_concurrent_result() -> None:
    operation = RecordingOperation(
        tenant="tenant-a",
        operation_id="operation-race",
        recording_id="recording-a",
        kind="publish_request",
        request_hash="hash-a",
        status=OperationStatus.COMPLETED,
        result={"asset_id": "asset-a"},
    )
    identical_connection = _Connection(operation)
    identical = AsyncpgRecordingRepository(_Pool(identical_connection))  # type: ignore[arg-type]

    replay = await identical.complete_operation(
        "tenant-a", operation.operation_id, result={"asset_id": "asset-a"},
    )

    assert replay.result == {"asset_id": "asset-a"}
    assert "status=$6" in identical_connection.cas_sql

    conflicting_connection = _Connection(operation)
    conflicting = AsyncpgRecordingRepository(_Pool(conflicting_connection))  # type: ignore[arg-type]
    with pytest.raises(OperationConflict):
        await conflicting.complete_operation(
            "tenant-a", operation.operation_id, result={"asset_id": "asset-b"},
        )


@pytest.mark.asyncio
async def test_postgres_pi_session_id_cannot_cross_tenant_scope() -> None:
    session = PiSessionMetadata(
        tenant="tenant-a",
        recording_id="recording-a",
        pi_session_id="pi-global",
        role=PiRole.PLANNER,
        model_id="model-a",
    )
    row = {
        **session.model_dump(mode="python"),
        "tenant": "tenant-b",
        "recording_id": "recording-b",
        "role": session.role.value,
        "status": session.status.value,
    }
    connection = _PersistenceConnection(pi_row=row)
    repository = AsyncpgRecordingRepository(_Pool(connection))  # type: ignore[arg-type]

    with pytest.raises(TenantIsolationError):
        await repository.save_pi_session(session)


@pytest.mark.asyncio
async def test_postgres_artifact_natural_replay_and_revision_reference() -> None:
    artifact = RecordingArtifact(
        artifact_id="artifact-retry",
        tenant="tenant-a",
        recording_id="recording-a",
        revision=1,
        kind="published_page_script",
        content_hash="sha256:release",
        storage_ref="asset-a",
        metadata={"version": 1},
    )
    existing = artifact.model_copy(update={"artifact_id": "artifact-original"})
    row = existing.model_dump(mode="python")
    connection = _PersistenceConnection(artifact_row=row)
    repository = AsyncpgRecordingRepository(_Pool(connection))  # type: ignore[arg-type]

    assert await repository.save_artifact(artifact) == existing

    orphan_connection = _PersistenceConnection(revision_exists=False)
    orphan_repository = AsyncpgRecordingRepository(  # type: ignore[arg-type]
        _Pool(orphan_connection)
    )
    with pytest.raises(RevisionConflict):
        await orphan_repository.save_artifact(artifact)
    assert orphan_connection.insert_attempted is False
