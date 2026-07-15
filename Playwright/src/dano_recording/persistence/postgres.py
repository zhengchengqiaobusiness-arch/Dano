"""asyncpg implementation of the recording repository.

The pool is injected by ``integrations.dano`` so this package does not create a
second database configuration or connection lifecycle.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, TYPE_CHECKING

from dano_recording.compiler.fingerprint import content_hash
from dano_recording.domain.facts import ActionFact, RecordingFact, RequestFact
from dano_recording.domain.operations import OperationStatus, RecordingOperation
from dano_recording.domain.pi import PiEvent, PiSessionMetadata, PiUsage
from dano_recording.domain.recording import RecordingSession, RecordingStatus
from dano_recording.domain.revisions import RecordingArtifact, RecordingRevision
from dano_recording.persistence.repository import (
    ImmutableFactConflict,
    OperationConflict,
    RecordingAlreadyExists,
    RecordingNotFound,
    RevisionConflict,
    TenantIsolationError,
    UNSET,
)

if TYPE_CHECKING:
    import asyncpg


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _decoded(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _fact_from_data(data: Any) -> RecordingFact:
    raw = _decoded(data)
    if "request_id" in raw:
        return RequestFact.model_validate(raw)
    if "action_type" in raw:
        return ActionFact.model_validate(raw)
    return RecordingFact.model_validate(raw)


def _session_from_row(row: Any) -> RecordingSession:
    return RecordingSession(
        tenant=row["tenant"],
        recording_id=row["recording_id"],
        status=row["status"],
        base_url=row["base_url"],
        current_revision=row["current_revision"],
        browser_lease_until=row["browser_lease_until"],
        resume_token_hash=row["resume_token_hash"],
        metadata=_decoded(row["metadata"]) or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _revision_from_row(row: Any) -> RecordingRevision:
    return RecordingRevision(
        tenant=row["tenant"],
        recording_id=row["recording_id"],
        revision=row["revision"],
        parent_revision=row["parent_revision"],
        content_hash=row["content_hash"],
        snapshot=_decoded(row["snapshot"]),
        actor=row["actor"],
        created_at=row["created_at"],
    )


def _operation_from_row(row: Any) -> RecordingOperation:
    return RecordingOperation(
        tenant=row["tenant"],
        operation_id=row["operation_id"],
        recording_id=row["recording_id"],
        kind=row["kind"],
        request_hash=row["request_hash"],
        status=row["status"],
        result=_decoded(row["result"]),
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _pi_session_from_row(row: Any) -> PiSessionMetadata:
    return PiSessionMetadata(
        tenant=row["tenant"],
        recording_id=row["recording_id"],
        pi_session_id=row["pi_session_id"],
        role=row["role"],
        model_id=row["model_id"],
        status=row["status"],
        last_revision=row["last_revision"],
        metadata=_decoded(row["metadata"]) or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _pi_event_from_row(row: Any) -> PiEvent:
    usage = _decoded(row["usage"])
    return PiEvent(
        event_id=row["event_id"],
        tenant=row["tenant"],
        recording_id=row["recording_id"],
        pi_session_id=row["pi_session_id"],
        event_type=row["event_type"],
        turn_index=row["turn_index"],
        payload=_decoded(row["payload"]) or {},
        usage=PiUsage.model_validate(usage) if usage is not None else None,
        occurred_at=row["occurred_at"],
    )


def _artifact_from_row(row: Any) -> RecordingArtifact:
    return RecordingArtifact(
        artifact_id=row["artifact_id"],
        tenant=row["tenant"],
        recording_id=row["recording_id"],
        revision=row["revision"],
        kind=row["kind"],
        content_hash=row["content_hash"],
        storage_ref=row["storage_ref"],
        metadata=_decoded(row["metadata"]) or {},
        created_at=row["created_at"],
    )


class AsyncpgRecordingRepository:
    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def _owner(self, conn: Any, recording_id: str) -> str | None:
        return await conn.fetchval(
            "SELECT tenant FROM recording_sessions WHERE recording_id=$1",
            recording_id,
        )

    async def _require_scope(self, conn: Any, tenant: str, recording_id: str) -> Any:
        row = await conn.fetchrow(
            "SELECT * FROM recording_sessions WHERE tenant=$1 AND recording_id=$2",
            tenant,
            recording_id,
        )
        if row is not None:
            return row
        owner = await self._owner(conn, recording_id)
        if owner is not None and owner != tenant:
            raise TenantIsolationError(f"recording {recording_id} belongs to another tenant")
        raise RecordingNotFound(f"recording {tenant}/{recording_id} was not found")

    async def create_session(self, session: RecordingSession) -> RecordingSession:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO recording_sessions(
                    tenant, recording_id, status, base_url, current_revision,
                    browser_lease_until, resume_token_hash, metadata, created_at, updated_at
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10)
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                session.tenant,
                session.recording_id,
                session.status.value,
                session.base_url,
                session.current_revision,
                session.browser_lease_until,
                session.resume_token_hash,
                _json(session.metadata),
                session.created_at,
                session.updated_at,
            )
            if row is None:
                owner = await self._owner(conn, session.recording_id)
                if owner is not None and owner != session.tenant:
                    raise TenantIsolationError(
                        f"recording {session.recording_id} belongs to another tenant"
                    )
                raise RecordingAlreadyExists(f"recording {session.recording_id} already exists")
            return _session_from_row(row)

    async def get_session(self, tenant: str, recording_id: str) -> RecordingSession:
        async with self._pool.acquire() as conn:
            return _session_from_row(await self._require_scope(conn, tenant, recording_id))

    async def update_session(
        self,
        tenant: str,
        recording_id: str,
        *,
        status: RecordingStatus | str | None = None,
        browser_lease_until: datetime | None | object = UNSET,
        metadata: dict[str, Any] | None = None,
    ) -> RecordingSession:
        """Update resumable state while preserving repository-owned revision."""

        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT * FROM recording_sessions
                WHERE tenant=$1 AND recording_id=$2
                FOR UPDATE
                """,
                tenant,
                recording_id,
            )
            if row is None:
                await self._require_scope(conn, tenant, recording_id)
                raise AssertionError("unreachable")
            current = _session_from_row(row)
            next_status = RecordingStatus(status) if status is not None else current.status
            next_lease = (
                current.browser_lease_until
                if browser_lease_until is UNSET
                else browser_lease_until
            )
            next_metadata = (
                current.metadata
                if metadata is None
                else {**current.metadata, **metadata}
            )
            updated = await conn.fetchrow(
                """
                UPDATE recording_sessions
                SET status=$3, browser_lease_until=$4, metadata=$5::jsonb, updated_at=now()
                WHERE tenant=$1 AND recording_id=$2
                RETURNING *
                """,
                tenant,
                recording_id,
                next_status.value,
                next_lease,
                _json(next_metadata),
            )
            return _session_from_row(updated)

    async def append_facts(
        self,
        tenant: str,
        recording_id: str,
        facts: tuple[RecordingFact, ...],
    ) -> int:
        async with self._pool.acquire() as conn, conn.transaction():
            await self._require_scope(conn, tenant, recording_id)
            inserted = 0
            for fact in facts:
                if fact.tenant != tenant or fact.recording_id != recording_id:
                    raise TenantIsolationError(
                        f"fact {fact.fact_id} is outside {tenant}/{recording_id}"
                    )
                fact_hash = content_hash(fact)
                existing = await conn.fetchrow(
                    "SELECT tenant, recording_id, content_hash FROM recording_facts WHERE fact_id=$1",
                    fact.fact_id,
                )
                if existing is not None:
                    if (
                        existing["tenant"] != tenant
                        or existing["recording_id"] != recording_id
                        or existing["content_hash"] != fact_hash
                    ):
                        raise ImmutableFactConflict(f"fact {fact.fact_id} cannot be rewritten")
                    continue
                sequence_owner = await conn.fetchval(
                    """
                    SELECT fact_id FROM recording_facts
                    WHERE tenant=$1 AND recording_id=$2 AND sequence=$3
                    """,
                    tenant,
                    recording_id,
                    fact.sequence,
                )
                if sequence_owner is not None:
                    raise ImmutableFactConflict(
                        f"sequence {fact.sequence} is already owned by fact {sequence_owner}"
                    )
                inserted_fact_id = await conn.fetchval(
                    """
                    INSERT INTO recording_facts(
                        tenant, recording_id, fact_id, sequence, kind, observed_at,
                        action_id, page_id, data, content_hash
                    ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
                    ON CONFLICT DO NOTHING
                    RETURNING fact_id
                    """,
                    tenant,
                    recording_id,
                    fact.fact_id,
                    fact.sequence,
                    fact.kind.value,
                    fact.observed_at,
                    fact.action_id,
                    fact.page_id,
                    _json(fact),
                    fact_hash,
                )
                if inserted_fact_id is None:
                    # Close the race between the pre-check and INSERT.  An
                    # identical fact is an idempotent replay; a sequence or
                    # content collision is an immutable-fact violation.
                    concurrent = await conn.fetchrow(
                        """
                        SELECT tenant, recording_id, fact_id, sequence, content_hash
                        FROM recording_facts
                        WHERE fact_id=$1 OR (
                            tenant=$2 AND recording_id=$3 AND sequence=$4
                        )
                        """,
                        fact.fact_id,
                        tenant,
                        recording_id,
                        fact.sequence,
                    )
                    if concurrent is not None and (
                        concurrent["fact_id"] == fact.fact_id
                        and concurrent["tenant"] == tenant
                        and concurrent["recording_id"] == recording_id
                        and concurrent["content_hash"] == fact_hash
                    ):
                        continue
                    raise ImmutableFactConflict(
                        f"fact {fact.fact_id} or sequence {fact.sequence} cannot be rewritten"
                    )
                inserted += 1
            return inserted

    async def list_facts(self, tenant: str, recording_id: str) -> tuple[RecordingFact, ...]:
        async with self._pool.acquire() as conn:
            await self._require_scope(conn, tenant, recording_id)
            rows = await conn.fetch(
                """
                SELECT data FROM recording_facts
                WHERE tenant=$1 AND recording_id=$2
                ORDER BY sequence, fact_id
                """,
                tenant,
                recording_id,
            )
            return tuple(_fact_from_data(row["data"]) for row in rows)

    async def commit_revision(
        self,
        tenant: str,
        recording_id: str,
        *,
        expected_revision: int,
        snapshot: dict[str, Any],
        actor: str,
    ) -> RecordingRevision:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT * FROM recording_sessions
                WHERE tenant=$1 AND recording_id=$2
                FOR UPDATE
                """,
                tenant,
                recording_id,
            )
            if row is None:
                await self._require_scope(conn, tenant, recording_id)
                raise AssertionError("unreachable")
            actual = int(row["current_revision"])
            if actual != expected_revision:
                raise RevisionConflict(expected=expected_revision, actual=actual)
            revision_number = actual + 1
            snapshot_hash = content_hash(snapshot)
            revision_row = await conn.fetchrow(
                """
                INSERT INTO recording_revisions(
                    tenant, recording_id, revision, parent_revision,
                    content_hash, snapshot, actor
                ) VALUES($1,$2,$3,$4,$5,$6::jsonb,$7)
                RETURNING *
                """,
                tenant,
                recording_id,
                revision_number,
                actual,
                snapshot_hash,
                _json(snapshot),
                actor,
            )
            await conn.execute(
                """
                UPDATE recording_sessions
                SET current_revision=$3, updated_at=now()
                WHERE tenant=$1 AND recording_id=$2 AND current_revision=$4
                """,
                tenant,
                recording_id,
                revision_number,
                actual,
            )
            return _revision_from_row(revision_row)

    async def get_revision(
        self,
        tenant: str,
        recording_id: str,
        revision: int | None = None,
    ) -> RecordingRevision | None:
        async with self._pool.acquire() as conn:
            session = await self._require_scope(conn, tenant, recording_id)
            number = int(session["current_revision"]) if revision is None else revision
            row = await conn.fetchrow(
                """
                SELECT * FROM recording_revisions
                WHERE tenant=$1 AND recording_id=$2 AND revision=$3
                """,
                tenant,
                recording_id,
                number,
            )
            return _revision_from_row(row) if row is not None else None

    async def register_operation(
        self,
        operation: RecordingOperation,
    ) -> tuple[RecordingOperation, bool]:
        async with self._pool.acquire() as conn, conn.transaction():
            await self._require_scope(conn, operation.tenant, operation.recording_id)
            row = await conn.fetchrow(
                """
                INSERT INTO recording_operations(
                    operation_id, tenant, recording_id, kind, request_hash,
                    status, result, error, created_at, updated_at
                ) VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10)
                ON CONFLICT (operation_id) DO NOTHING
                RETURNING *
                """,
                operation.operation_id,
                operation.tenant,
                operation.recording_id,
                operation.kind,
                operation.request_hash,
                operation.status.value,
                _json(operation.result) if operation.result is not None else None,
                operation.error,
                operation.created_at,
                operation.updated_at,
            )
            created = row is not None
            if row is None:
                row = await conn.fetchrow(
                    "SELECT * FROM recording_operations WHERE operation_id=$1",
                    operation.operation_id,
                )
                existing = _operation_from_row(row)
                if existing.tenant != operation.tenant:
                    raise TenantIsolationError(
                        f"operation {operation.operation_id} belongs to another tenant"
                    )
                if (
                    existing.recording_id != operation.recording_id
                    or existing.kind != operation.kind
                    or existing.request_hash != operation.request_hash
                ):
                    raise OperationConflict(
                        f"operation {operation.operation_id} was reused with a different request"
                    )
            return _operation_from_row(row), created

    async def complete_operation(
        self,
        tenant: str,
        operation_id: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> RecordingOperation:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM recording_operations WHERE operation_id=$1",
                operation_id,
            )
            if row is None:
                raise OperationConflict(f"operation {operation_id} was not registered")
            if row["tenant"] != tenant:
                raise TenantIsolationError(f"operation {operation_id} belongs to another tenant")
            status = OperationStatus.FAILED if error else OperationStatus.COMPLETED
            existing = _operation_from_row(row)
            if existing.status is not OperationStatus.STARTED:
                if existing.status is status and existing.result == result and existing.error == error:
                    return existing
                raise OperationConflict(
                    f"operation {operation_id} is already terminal and cannot be rewritten"
                )
            updated = await conn.fetchrow(
                """
                UPDATE recording_operations
                SET status=$2, result=$3::jsonb, error=$4, updated_at=now()
                WHERE operation_id=$1 AND tenant=$5 AND status=$6
                RETURNING *
                """,
                operation_id,
                status.value,
                _json(result) if result is not None else None,
                error,
                tenant,
                OperationStatus.STARTED.value,
            )
            if updated is None:
                # Another worker won the terminal transition after our read.
                # Identical completion is an idempotent replay; a different
                # outcome must never overwrite it.
                concurrent_row = await conn.fetchrow(
                    "SELECT * FROM recording_operations WHERE operation_id=$1",
                    operation_id,
                )
                concurrent = _operation_from_row(concurrent_row)
                if (
                    concurrent.status is status
                    and concurrent.result == result
                    and concurrent.error == error
                ):
                    return concurrent
                raise OperationConflict(
                    f"operation {operation_id} is already terminal and cannot be rewritten"
                )
            return _operation_from_row(updated)

    async def save_pi_session(self, session: PiSessionMetadata) -> PiSessionMetadata:
        async with self._pool.acquire() as conn, conn.transaction():
            await self._require_scope(conn, session.tenant, session.recording_id)
            row = await conn.fetchrow(
                """
                INSERT INTO recording_pi_sessions(
                    tenant, recording_id, pi_session_id, role, model_id, status,
                    last_revision, metadata, created_at, updated_at
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10)
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                session.tenant,
                session.recording_id,
                session.pi_session_id,
                session.role.value,
                session.model_id,
                session.status.value,
                session.last_revision,
                _json(session.metadata),
                session.created_at,
                session.updated_at,
            )
            if row is not None:
                return _pi_session_from_row(row)
            existing_row = await conn.fetchrow(
                "SELECT * FROM recording_pi_sessions WHERE pi_session_id=$1",
                session.pi_session_id,
            )
            existing = _pi_session_from_row(existing_row)
            if existing.tenant != session.tenant:
                raise TenantIsolationError(
                    f"Pi session {session.pi_session_id} belongs to another tenant"
                )
            if (
                existing.recording_id != session.recording_id
                or existing.role != session.role
                or existing.model_id != session.model_id
            ):
                raise ImmutableFactConflict(
                    f"Pi session {session.pi_session_id} identity cannot be rewritten"
                )
            row = await conn.fetchrow(
                """
                UPDATE recording_pi_sessions
                SET status=$2, last_revision=$3, metadata=$4::jsonb, updated_at=$5
                WHERE pi_session_id=$1 AND tenant=$6 AND recording_id=$7
                  AND role=$8 AND model_id=$9
                RETURNING *
                """,
                session.pi_session_id,
                session.status.value,
                session.last_revision,
                _json(session.metadata),
                session.updated_at,
                session.tenant,
                session.recording_id,
                session.role.value,
                session.model_id,
            )
            if row is None:
                raise ImmutableFactConflict(
                    f"Pi session {session.pi_session_id} changed concurrently"
                )
            return _pi_session_from_row(row)

    async def list_pi_sessions(
        self,
        tenant: str,
        recording_id: str,
    ) -> tuple[PiSessionMetadata, ...]:
        async with self._pool.acquire() as conn:
            await self._require_scope(conn, tenant, recording_id)
            rows = await conn.fetch(
                """
                SELECT * FROM recording_pi_sessions
                WHERE tenant=$1 AND recording_id=$2
                ORDER BY role, pi_session_id
                """,
                tenant,
                recording_id,
            )
            return tuple(_pi_session_from_row(row) for row in rows)

    async def append_pi_event(self, event: PiEvent) -> None:
        async with self._pool.acquire() as conn:
            await self._require_scope(conn, event.tenant, event.recording_id)
            owner = await conn.fetchrow(
                """
                SELECT tenant, recording_id FROM recording_pi_sessions
                WHERE pi_session_id=$1
                """,
                event.pi_session_id,
            )
            if owner is None:
                raise RecordingNotFound(
                    f"Pi session {event.pi_session_id} was not found"
                )
            if owner["tenant"] != event.tenant or owner["recording_id"] != event.recording_id:
                raise TenantIsolationError(
                    f"Pi session {event.pi_session_id} belongs to another recording scope"
                )
            inserted = await conn.fetchval(
                """
                INSERT INTO recording_pi_events(
                    event_id, tenant, recording_id, pi_session_id, event_type,
                    turn_index, payload, usage, occurred_at
                ) VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
                """,
                event.event_id,
                event.tenant,
                event.recording_id,
                event.pi_session_id,
                event.event_type,
                event.turn_index,
                _json(event.payload),
                _json(event.usage) if event.usage is not None else None,
                event.occurred_at,
            )
            if inserted is not None:
                return
            row = await conn.fetchrow(
                "SELECT * FROM recording_pi_events WHERE event_id=$1",
                event.event_id,
            )
            existing = _pi_event_from_row(row)
            if existing.tenant != event.tenant:
                raise TenantIsolationError(
                    f"Pi event {event.event_id} belongs to another tenant"
                )
            if existing != event:
                raise ImmutableFactConflict(
                    f"Pi event {event.event_id} cannot be rewritten"
                )

    async def list_pi_events(
        self,
        tenant: str,
        recording_id: str,
        *,
        pi_session_id: str | None = None,
    ) -> tuple[PiEvent, ...]:
        async with self._pool.acquire() as conn:
            await self._require_scope(conn, tenant, recording_id)
            rows = await conn.fetch(
                """
                SELECT * FROM recording_pi_events
                WHERE tenant=$1 AND recording_id=$2
                  AND ($3::text IS NULL OR pi_session_id=$3)
                ORDER BY occurred_at, event_id
                """,
                tenant,
                recording_id,
                pi_session_id,
            )
            return tuple(_pi_event_from_row(row) for row in rows)

    async def save_artifact(self, artifact: RecordingArtifact) -> RecordingArtifact:
        async with self._pool.acquire() as conn, conn.transaction():
            session = await self._require_scope(conn, artifact.tenant, artifact.recording_id)
            if artifact.revision > 0:
                revision_exists = await conn.fetchval(
                    """
                    SELECT 1 FROM recording_revisions
                    WHERE tenant=$1 AND recording_id=$2 AND revision=$3
                    """,
                    artifact.tenant,
                    artifact.recording_id,
                    artifact.revision,
                )
                if revision_exists is None:
                    raise RevisionConflict(
                        expected=artifact.revision,
                        actual=int(session["current_revision"]),
                    )
            row = await conn.fetchrow(
                """
                INSERT INTO recording_artifacts(
                    artifact_id, tenant, recording_id, revision, kind,
                    content_hash, storage_ref, metadata, created_at
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                artifact.artifact_id,
                artifact.tenant,
                artifact.recording_id,
                artifact.revision,
                artifact.kind,
                artifact.content_hash,
                artifact.storage_ref,
                _json(artifact.metadata),
                artifact.created_at,
            )
            if row is None:
                existing = await conn.fetchrow(
                    "SELECT * FROM recording_artifacts WHERE artifact_id=$1",
                    artifact.artifact_id,
                )
                if existing is not None:
                    if existing["tenant"] != artifact.tenant:
                        raise TenantIsolationError(
                            f"artifact {artifact.artifact_id} belongs to another tenant"
                        )
                    if _artifact_from_row(existing) != artifact:
                        raise ImmutableFactConflict(
                            f"artifact {artifact.artifact_id} cannot be rewritten"
                        )
                else:
                    existing = await conn.fetchrow(
                        """
                        SELECT * FROM recording_artifacts
                        WHERE tenant=$1 AND recording_id=$2 AND revision=$3
                          AND kind=$4 AND content_hash=$5
                        """,
                        artifact.tenant,
                        artifact.recording_id,
                        artifact.revision,
                        artifact.kind,
                        artifact.content_hash,
                    )
                    if existing is None:
                        raise ImmutableFactConflict(
                            "artifact insert conflicted without a matching immutable row"
                        )
                    natural = _artifact_from_row(existing)
                    if (
                        natural.storage_ref != artifact.storage_ref
                        or natural.metadata != artifact.metadata
                    ):
                        raise ImmutableFactConflict(
                            "artifact natural identity cannot be rewritten with different metadata"
                        )
                row = existing
            return _artifact_from_row(row)

    async def list_artifacts(
        self,
        tenant: str,
        recording_id: str,
        *,
        revision: int | None = None,
        kind: str | None = None,
    ) -> tuple[RecordingArtifact, ...]:
        async with self._pool.acquire() as conn:
            await self._require_scope(conn, tenant, recording_id)
            rows = await conn.fetch(
                """
                SELECT * FROM recording_artifacts
                WHERE tenant=$1 AND recording_id=$2
                  AND ($3::integer IS NULL OR revision=$3)
                  AND ($4::text IS NULL OR kind=$4)
                ORDER BY revision, kind, artifact_id
                """,
                tenant,
                recording_id,
                revision,
                kind,
            )
            return tuple(_artifact_from_row(row) for row in rows)
