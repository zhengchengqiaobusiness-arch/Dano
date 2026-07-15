"""FastAPI installer for recording-v3."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from dano_recording.api.sessions import TenantResolver, session_router
from dano_recording.api.websocket import websocket_router
from dano_recording.bootstrap import RecordingApplication


async def _reject_tenant(_key: str | None) -> str:
    raise HTTPException(status_code=401, detail="tenant resolver is not configured")


def install_recording_v3(
    app: FastAPI,
    *,
    tenant_resolver: TenantResolver | None = None,
    service: RecordingApplication | None = None,
    **application_options: Any,
) -> RecordingApplication:
    if getattr(app.state, "recording_v3", None) is not None:
        raise RuntimeError("recording-v3 is already installed")
    service = service or RecordingApplication(**application_options)
    app.include_router(session_router(service, tenant_resolver or _reject_tenant))
    app.include_router(websocket_router(service))
    app.state.recording_v3 = service
    return service


__all__ = ["install_recording_v3"]
