from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient
import pytest

import dano.gateway.app as gateway_app
from dano.recording_v3 import ensure_recording_package


ensure_recording_package()

from dano_recording.bootstrap import RecordingApplication  # noqa: E402
from dano_recording.persistence.postgres import AsyncpgRecordingRepository  # noqa: E402


class _Connection:
    def __init__(self, *, migration_ready: bool = True, error: Exception | None = None) -> None:
        self.migration_ready = migration_ready
        self.error = error
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, query: str, *args: Any) -> bool:
        self.queries.append((query, args))
        if self.error is not None:
            raise self.error
        return self.migration_ready


class _Pool:
    def __init__(
        self,
        *,
        migration_ready: bool = True,
        error: Exception | None = None,
    ) -> None:
        self.connection = _Connection(migration_ready=migration_ready, error=error)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


async def _get(path: str, monkeypatch: pytest.MonkeyPatch, service: Any):
    monkeypatch.setattr(gateway_app, "_recording_v3_service", service)
    transport = ASGITransport(app=gateway_app.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_recording_v3_health_is_unavailable_after_durable_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingApplication(persistent_repository_required=True)
    await service.start()
    try:
        response = await _get("/recording-v3/health", monkeypatch, service)
    finally:
        await service.close()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "ready": False}


@pytest.mark.asyncio
async def test_recording_v3_health_requires_a_started_recording_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AsyncpgRecordingRepository(_Pool())  # type: ignore[arg-type]
    service = RecordingApplication(
        repository=repository,
        persistent_repository_required=True,
    )

    response = await _get("/recording-v3/health", monkeypatch, service)

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "ready": False}


@pytest.mark.asyncio
async def test_recording_v3_health_fails_closed_when_postgres_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "postgresql://operator:do-not-expose@database/dano"
    pool = _Pool(error=RuntimeError(secret))
    service = RecordingApplication(persistent_repository_required=True)
    await service.start(
        repository=AsyncpgRecordingRepository(pool),  # type: ignore[arg-type]
    )
    try:
        response = await _get("/recording-v3/health", monkeypatch, service)
    finally:
        await service.close()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "ready": False}
    assert secret not in response.text


@pytest.mark.asyncio
async def test_recording_v3_health_fails_closed_when_migration_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _Pool(migration_ready=False)
    service = RecordingApplication(persistent_repository_required=True)
    await service.start(
        repository=AsyncpgRecordingRepository(pool),  # type: ignore[arg-type]
    )
    try:
        response = await _get("/recording-v3/health", monkeypatch, service)
    finally:
        await service.close()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "ready": False}


@pytest.mark.asyncio
async def test_recording_v3_health_reports_ready_for_live_postgres_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _Pool()
    service = RecordingApplication(persistent_repository_required=True)
    await service.start(
        repository=AsyncpgRecordingRepository(pool),  # type: ignore[arg-type]
    )
    try:
        response = await _get("/recording-v3/health", monkeypatch, service)
    finally:
        await service.close()

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "ready": True}
    assert len(pool.connection.queries) == 1
    query, args = pool.connection.queries[0]
    assert "schema_migrations" in query
    assert "to_regclass" in query
    assert args[0] == "014_recording_v3.sql"
    assert set(args[1]) == {
        "recording_sessions",
        "recording_facts",
        "recording_revisions",
        "recording_operations",
        "recording_pi_sessions",
        "recording_pi_events",
        "recording_artifacts",
    }


@pytest.mark.asyncio
async def test_generic_health_contract_remains_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingApplication(persistent_repository_required=True)
    await service.start()
    try:
        response = await _get("/health", monkeypatch, service)
    finally:
        await service.close()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
