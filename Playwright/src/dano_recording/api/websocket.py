"""Ticket-authenticated recording WebSocket route."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from dano_recording.api.auth import TicketError
from dano_recording.bootstrap import RecordingApplication, RecordingUnavailable


def websocket_router(service: RecordingApplication) -> APIRouter:
    router = APIRouter(prefix="/recording-v3", tags=["recording-v3"])

    @router.websocket("/sessions/{recording_id}/ws")
    async def recording_socket(websocket: WebSocket, recording_id: str, ticket: str = "") -> None:
        try:
            tenant = await service.consume_ticket(ticket, recording_id=recording_id)
        except RecordingUnavailable:
            await websocket.close(code=1013, reason="recording service unavailable")
            return
        except (TicketError, PermissionError, LookupError):
            await websocket.close(code=4401, reason="invalid recording ticket")
            return
        await websocket.accept()
        send_lock = asyncio.Lock()

        async def send(value: dict) -> None:
            async with send_lock:
                await websocket.send_json(value)

        await service.attach_socket(tenant, recording_id, send)
        try:
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode("utf-8")) > 8_388_608:
                    await send({
                        "type": "error",
                        "code": "message_too_large",
                        "detail": "recording message exceeds 8 MiB",
                        "retryable": False,
                    })
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await send({"type": "error", "code": "invalid_json", "detail": "invalid JSON"})
                    continue
                if not isinstance(message, dict):
                    await send({"type": "error", "code": "invalid_message", "detail": "message must be an object"})
                    continue
                if await service.handle_message(tenant, recording_id, message, send):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await service.detach_socket(tenant, recording_id, send)

    return router


__all__ = ["websocket_router"]
