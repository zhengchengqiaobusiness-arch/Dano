"""HTTP session negotiation routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import hashlib

from fastapi import APIRouter, Header, HTTPException

from dano_recording.api.protocol import (
    CreateRecordingRequest,
    ResumeRecordingRequest,
    SessionConnectionResponse,
)
from dano_recording.bootstrap import RecordingApplication, RecordingUnavailable
from dano_recording.persistence.repository import RecordingNotFound, TenantIsolationError


TenantResolver = Callable[[str | None], Awaitable[str]]


def _credential_subject(value: str | None) -> str:
    if not value:
        return "anonymous"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"tenant-credential:{digest}"


def session_router(service: RecordingApplication, tenant_resolver: TenantResolver) -> APIRouter:
    router = APIRouter(prefix="/recording-v3", tags=["recording-v3"])

    @router.post("/sessions", response_model=SessionConnectionResponse, status_code=201)
    async def create_session(
        request: CreateRecordingRequest,
        x_tenant_key: str | None = Header(default=None),
    ) -> SessionConnectionResponse:
        tenant = await tenant_resolver(x_tenant_key)
        try:
            return await service.create_session(
                tenant,
                request,
                subject=_credential_subject(x_tenant_key),
            )
        except RecordingUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/sessions/{recording_id}/resume", response_model=SessionConnectionResponse)
    async def resume_session(
        recording_id: str,
        request: ResumeRecordingRequest,
        x_tenant_key: str | None = Header(default=None),
    ) -> SessionConnectionResponse:
        tenant = await tenant_resolver(x_tenant_key)
        try:
            return await service.resume_session(
                tenant,
                recording_id,
                request.resume_token,
                subject=_credential_subject(x_tenant_key),
            )
        except RecordingUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail="录制恢复凭证无效") from exc
        except (RecordingNotFound, TenantIsolationError) as exc:
            # Do not reveal whether the identifier belongs to another tenant.
            raise HTTPException(status_code=404, detail="录制会话不存在") from exc

    return router


__all__ = ["TenantResolver", "session_router"]
