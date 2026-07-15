"""Repository contract shared by in-memory tests and asyncpg production storage."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from dano_recording.domain.facts import RecordingFact
from dano_recording.domain.operations import RecordingOperation
from dano_recording.domain.pi import PiEvent, PiSessionMetadata
from dano_recording.domain.recording import RecordingSession, RecordingStatus
from dano_recording.domain.revisions import RecordingArtifact, RecordingRevision


class RecordingRepositoryError(RuntimeError):
    pass


class RecordingAlreadyExists(RecordingRepositoryError):
    pass


class RecordingNotFound(RecordingRepositoryError):
    pass


class TenantIsolationError(RecordingRepositoryError):
    pass


class ImmutableFactConflict(RecordingRepositoryError):
    pass


class RevisionConflict(RecordingRepositoryError):
    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(f"revision conflict: expected {expected}, actual {actual}")
        self.expected = expected
        self.actual = actual


class OperationConflict(RecordingRepositoryError):
    pass


class _Unset:
    __slots__ = ()


UNSET = _Unset()


class RecordingRepository(Protocol):
    async def create_session(self, session: RecordingSession) -> RecordingSession: ...

    async def get_session(self, tenant: str, recording_id: str) -> RecordingSession: ...

    async def update_session(
        self,
        tenant: str,
        recording_id: str,
        *,
        status: RecordingStatus | str | None = None,
        browser_lease_until: datetime | None | _Unset = UNSET,
        metadata: dict[str, Any] | None = None,
    ) -> RecordingSession: ...

    async def append_facts(
        self, tenant: str, recording_id: str, facts: tuple[RecordingFact, ...]
    ) -> int: ...

    async def list_facts(self, tenant: str, recording_id: str) -> tuple[RecordingFact, ...]: ...

    async def commit_revision(
        self,
        tenant: str,
        recording_id: str,
        *,
        expected_revision: int,
        snapshot: dict[str, Any],
        actor: str,
    ) -> RecordingRevision: ...

    async def get_revision(
        self, tenant: str, recording_id: str, revision: int | None = None
    ) -> RecordingRevision | None: ...

    async def register_operation(
        self, operation: RecordingOperation
    ) -> tuple[RecordingOperation, bool]: ...

    async def complete_operation(
        self,
        tenant: str,
        operation_id: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> RecordingOperation: ...

    async def save_pi_session(self, session: PiSessionMetadata) -> PiSessionMetadata: ...

    async def list_pi_sessions(
        self, tenant: str, recording_id: str
    ) -> tuple[PiSessionMetadata, ...]: ...

    async def append_pi_event(self, event: PiEvent) -> None: ...

    async def list_pi_events(
        self, tenant: str, recording_id: str, *, pi_session_id: str | None = None
    ) -> tuple[PiEvent, ...]: ...

    async def save_artifact(self, artifact: RecordingArtifact) -> RecordingArtifact: ...

    async def list_artifacts(
        self,
        tenant: str,
        recording_id: str,
        *,
        revision: int | None = None,
        kind: str | None = None,
    ) -> tuple[RecordingArtifact, ...]: ...
