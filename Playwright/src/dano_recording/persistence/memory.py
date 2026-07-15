"""Concurrency-safe in-memory recording repository."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from typing import Any

from dano_recording.compiler.fingerprint import content_hash
from dano_recording.domain.facts import RecordingFact
from dano_recording.domain.operations import OperationStatus, RecordingOperation
from dano_recording.domain.pi import PiEvent, PiSessionMetadata
from dano_recording.domain.recording import RecordingSession, RecordingStatus
from dano_recording.domain.revisions import RecordingArtifact, RecordingRevision
from dano_recording.domain._base import utc_now
from dano_recording.persistence.repository import (
    ImmutableFactConflict,
    OperationConflict,
    RecordingAlreadyExists,
    RecordingNotFound,
    RevisionConflict,
    TenantIsolationError,
    UNSET,
)


class InMemoryRecordingRepository:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[tuple[str, str], RecordingSession] = {}
        self._recording_tenants: dict[str, str] = {}
        self._facts: dict[tuple[str, str], dict[str, RecordingFact]] = {}
        self._fact_sequences: dict[tuple[str, str], dict[int, str]] = {}
        self._revisions: dict[tuple[str, str], dict[int, RecordingRevision]] = {}
        self._operations: dict[str, RecordingOperation] = {}
        self._pi_sessions: dict[tuple[str, str, str], PiSessionMetadata] = {}
        self._pi_session_index: dict[str, PiSessionMetadata] = {}
        self._pi_events: dict[tuple[str, str], list[PiEvent]] = {}
        self._pi_event_index: dict[str, PiEvent] = {}
        self._artifacts: dict[str, RecordingArtifact] = {}
        self._artifact_natural_index: dict[
            tuple[str, str, int, str, str], RecordingArtifact
        ] = {}

    def _scope_key(self, tenant: str, recording_id: str) -> tuple[str, str]:
        owner = self._recording_tenants.get(recording_id)
        if owner is not None and owner != tenant:
            raise TenantIsolationError(f"recording {recording_id} belongs to another tenant")
        key = (tenant, recording_id)
        if key not in self._sessions:
            raise RecordingNotFound(f"recording {tenant}/{recording_id} was not found")
        return key

    async def create_session(self, session: RecordingSession) -> RecordingSession:
        async with self._lock:
            owner = self._recording_tenants.get(session.recording_id)
            if owner is not None and owner != session.tenant:
                raise TenantIsolationError(
                    f"recording {session.recording_id} belongs to another tenant"
                )
            key = (session.tenant, session.recording_id)
            if key in self._sessions:
                raise RecordingAlreadyExists(f"recording {session.recording_id} already exists")
            stored = session.model_copy(deep=True)
            self._sessions[key] = stored
            self._recording_tenants[session.recording_id] = session.tenant
            self._facts[key] = {}
            self._fact_sequences[key] = {}
            self._revisions[key] = {}
            return stored.model_copy(deep=True)

    async def get_session(self, tenant: str, recording_id: str) -> RecordingSession:
        async with self._lock:
            key = self._scope_key(tenant, recording_id)
            return self._sessions[key].model_copy(deep=True)

    async def update_session(
        self,
        tenant: str,
        recording_id: str,
        *,
        status: RecordingStatus | str | None = None,
        browser_lease_until: datetime | None | object = UNSET,
        metadata: dict[str, Any] | None = None,
    ) -> RecordingSession:
        """Update resumable session state without exposing current_revision."""

        async with self._lock:
            key = self._scope_key(tenant, recording_id)
            session = self._sessions[key]
            updates: dict[str, Any] = {"updated_at": utc_now()}
            if status is not None:
                updates["status"] = RecordingStatus(status)
            if browser_lease_until is not UNSET:
                updates["browser_lease_until"] = browser_lease_until
            if metadata is not None:
                updates["metadata"] = {**session.metadata, **deepcopy(metadata)}
            self._sessions[key] = session.model_copy(update=updates, deep=True)
            return self._sessions[key].model_copy(deep=True)

    async def append_facts(
        self,
        tenant: str,
        recording_id: str,
        facts: tuple[RecordingFact, ...],
    ) -> int:
        async with self._lock:
            key = self._scope_key(tenant, recording_id)
            inserted = 0
            for fact in facts:
                if fact.tenant != tenant or fact.recording_id != recording_id:
                    raise TenantIsolationError(
                        f"fact {fact.fact_id} is outside {tenant}/{recording_id}"
                    )
                existing = self._facts[key].get(fact.fact_id)
                if existing is not None:
                    if existing != fact:
                        raise ImmutableFactConflict(f"fact {fact.fact_id} cannot be rewritten")
                    continue
                sequence_owner = self._fact_sequences[key].get(fact.sequence)
                if sequence_owner is not None:
                    raise ImmutableFactConflict(
                        f"sequence {fact.sequence} is already owned by fact {sequence_owner}"
                    )
                self._facts[key][fact.fact_id] = fact.model_copy(deep=True)
                self._fact_sequences[key][fact.sequence] = fact.fact_id
                inserted += 1
            return inserted

    async def list_facts(self, tenant: str, recording_id: str) -> tuple[RecordingFact, ...]:
        async with self._lock:
            key = self._scope_key(tenant, recording_id)
            return tuple(
                fact.model_copy(deep=True)
                for fact in sorted(
                    self._facts[key].values(),
                    key=lambda item: (item.sequence, item.fact_id),
                )
            )

    async def commit_revision(
        self,
        tenant: str,
        recording_id: str,
        *,
        expected_revision: int,
        snapshot: dict[str, Any],
        actor: str,
    ) -> RecordingRevision:
        async with self._lock:
            key = self._scope_key(tenant, recording_id)
            session = self._sessions[key]
            if session.current_revision != expected_revision:
                raise RevisionConflict(
                    expected=expected_revision,
                    actual=session.current_revision,
                )
            revision_number = expected_revision + 1
            stored_snapshot = deepcopy(snapshot)
            revision = RecordingRevision(
                tenant=tenant,
                recording_id=recording_id,
                revision=revision_number,
                parent_revision=expected_revision,
                content_hash=content_hash(stored_snapshot),
                snapshot=stored_snapshot,
                actor=actor,
            )
            self._revisions[key][revision_number] = revision
            self._sessions[key] = session.model_copy(update={
                "current_revision": revision_number,
                "updated_at": utc_now(),
            }, deep=True)
            return revision.model_copy(deep=True)

    async def get_revision(
        self,
        tenant: str,
        recording_id: str,
        revision: int | None = None,
    ) -> RecordingRevision | None:
        async with self._lock:
            key = self._scope_key(tenant, recording_id)
            number = self._sessions[key].current_revision if revision is None else revision
            stored = self._revisions[key].get(number)
            return stored.model_copy(deep=True) if stored is not None else None

    async def register_operation(
        self,
        operation: RecordingOperation,
    ) -> tuple[RecordingOperation, bool]:
        async with self._lock:
            self._scope_key(operation.tenant, operation.recording_id)
            existing = self._operations.get(operation.operation_id)
            if existing is None:
                stored = operation.model_copy(deep=True)
                self._operations[operation.operation_id] = stored
                return stored.model_copy(deep=True), True
            identity = (
                existing.tenant,
                existing.recording_id,
                existing.kind,
                existing.request_hash,
            )
            attempted = (
                operation.tenant,
                operation.recording_id,
                operation.kind,
                operation.request_hash,
            )
            if identity != attempted:
                if existing.tenant != operation.tenant:
                    raise TenantIsolationError(
                        f"operation {operation.operation_id} belongs to another tenant"
                    )
                raise OperationConflict(
                    f"operation {operation.operation_id} was reused with a different request"
                )
            return existing.model_copy(deep=True), False

    async def complete_operation(
        self,
        tenant: str,
        operation_id: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> RecordingOperation:
        async with self._lock:
            existing = self._operations.get(operation_id)
            if existing is None:
                raise OperationConflict(f"operation {operation_id} was not registered")
            if existing.tenant != tenant:
                raise TenantIsolationError(f"operation {operation_id} belongs to another tenant")
            status = OperationStatus.FAILED if error else OperationStatus.COMPLETED
            if existing.status is not OperationStatus.STARTED:
                if existing.status is status and existing.result == result and existing.error == error:
                    return existing.model_copy(deep=True)
                raise OperationConflict(
                    f"operation {operation_id} is already terminal and cannot be rewritten"
                )
            updated = existing.model_copy(update={
                "status": status,
                "result": deepcopy(result),
                "error": error,
                "updated_at": utc_now(),
            }, deep=True)
            self._operations[operation_id] = updated
            return updated.model_copy(deep=True)

    async def save_pi_session(self, session: PiSessionMetadata) -> PiSessionMetadata:
        async with self._lock:
            self._scope_key(session.tenant, session.recording_id)
            key = (session.tenant, session.recording_id, session.pi_session_id)
            existing = self._pi_session_index.get(session.pi_session_id)
            if existing is not None:
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
                stored = existing.model_copy(update={
                    "status": session.status,
                    "last_revision": session.last_revision,
                    "metadata": deepcopy(session.metadata),
                    "updated_at": session.updated_at,
                }, deep=True)
            else:
                stored = session.model_copy(deep=True)
            self._pi_sessions[key] = stored
            self._pi_session_index[session.pi_session_id] = stored
            return stored.model_copy(deep=True)

    async def list_pi_sessions(
        self,
        tenant: str,
        recording_id: str,
    ) -> tuple[PiSessionMetadata, ...]:
        async with self._lock:
            self._scope_key(tenant, recording_id)
            sessions = (
                session for (item_tenant, item_recording, _), session in self._pi_sessions.items()
                if item_tenant == tenant and item_recording == recording_id
            )
            return tuple(
                item.model_copy(deep=True)
                for item in sorted(sessions, key=lambda value: (value.role.value, value.pi_session_id))
            )

    async def append_pi_event(self, event: PiEvent) -> None:
        async with self._lock:
            key = self._scope_key(event.tenant, event.recording_id)
            owner = self._pi_session_index.get(event.pi_session_id)
            if owner is None:
                raise RecordingNotFound(
                    f"Pi session {event.pi_session_id} was not found"
                )
            if owner.tenant != event.tenant or owner.recording_id != event.recording_id:
                raise TenantIsolationError(
                    f"Pi session {event.pi_session_id} belongs to another recording scope"
                )
            existing = self._pi_event_index.get(event.event_id)
            if existing is not None:
                if existing.tenant != event.tenant:
                    raise TenantIsolationError(
                        f"Pi event {event.event_id} belongs to another tenant"
                    )
                if existing != event:
                    raise ImmutableFactConflict(
                        f"Pi event {event.event_id} cannot be rewritten"
                    )
                return
            stored = event.model_copy(deep=True)
            self._pi_events.setdefault(key, []).append(stored)
            self._pi_event_index[event.event_id] = stored

    async def list_pi_events(
        self,
        tenant: str,
        recording_id: str,
        *,
        pi_session_id: str | None = None,
    ) -> tuple[PiEvent, ...]:
        async with self._lock:
            key = self._scope_key(tenant, recording_id)
            events = (
                event for event in self._pi_events.get(key, ())
                if pi_session_id is None or event.pi_session_id == pi_session_id
            )
            return tuple(
                item.model_copy(deep=True)
                for item in sorted(events, key=lambda value: (value.occurred_at, value.event_id))
            )

    async def save_artifact(self, artifact: RecordingArtifact) -> RecordingArtifact:
        async with self._lock:
            scope = self._scope_key(artifact.tenant, artifact.recording_id)
            if artifact.revision > 0 and artifact.revision not in self._revisions[scope]:
                raise RevisionConflict(
                    expected=artifact.revision,
                    actual=self._sessions[scope].current_revision,
                )
            existing = self._artifacts.get(artifact.artifact_id)
            if existing is not None and existing != artifact:
                raise ImmutableFactConflict(f"artifact {artifact.artifact_id} cannot be rewritten")
            natural_key = (
                artifact.tenant,
                artifact.recording_id,
                artifact.revision,
                artifact.kind,
                artifact.content_hash,
            )
            natural = self._artifact_natural_index.get(natural_key)
            if natural is not None:
                if natural.storage_ref != artifact.storage_ref or natural.metadata != artifact.metadata:
                    raise ImmutableFactConflict(
                        "artifact natural identity cannot be rewritten with different metadata"
                    )
                return natural.model_copy(deep=True)
            stored = artifact.model_copy(deep=True)
            self._artifacts[artifact.artifact_id] = stored
            self._artifact_natural_index[natural_key] = stored
            return stored.model_copy(deep=True)

    async def list_artifacts(
        self,
        tenant: str,
        recording_id: str,
        *,
        revision: int | None = None,
        kind: str | None = None,
    ) -> tuple[RecordingArtifact, ...]:
        async with self._lock:
            self._scope_key(tenant, recording_id)
            artifacts = (
                artifact for artifact in self._artifacts.values()
                if artifact.tenant == tenant
                and artifact.recording_id == recording_id
                and (revision is None or artifact.revision == revision)
                and (kind is None or artifact.kind == kind)
            )
            return tuple(
                item.model_copy(deep=True)
                for item in sorted(
                    artifacts,
                    key=lambda value: (value.revision, value.kind, value.artifact_id),
                )
            )
