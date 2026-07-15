"""Recording boundary credentials.

Tenant authentication remains owned by Dano.  This module issues short-lived,
single-use WebSocket tickets after that authentication has succeeded and hashes
longer-lived resume tokens before persistence.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, expected_hash: str | None) -> bool:
    return bool(expected_hash) and hmac.compare_digest(hash_token(token), expected_hash)


@dataclass(frozen=True, slots=True)
class TicketGrant:
    tenant: str
    recording_id: str
    subject: str
    expires_at: datetime


class TicketError(PermissionError):
    pass


class WebSocketTicketManager:
    def __init__(self, *, ttl_seconds: float = 45.0, max_tickets: int = 10_000) -> None:
        if ttl_seconds <= 0 or max_tickets < 1:
            raise ValueError("ticket TTL and capacity must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_tickets = max_tickets
        self._tickets: dict[str, TicketGrant] = {}
        self._lock = asyncio.Lock()

    async def issue(self, *, tenant: str, recording_id: str, subject: str = "") -> tuple[str, datetime]:
        now = datetime.now(timezone.utc)
        async with self._lock:
            self._tickets = {key: value for key, value in self._tickets.items() if value.expires_at > now}
            if len(self._tickets) >= self.max_tickets:
                raise TicketError("WebSocket ticket capacity reached")
            token = secrets.token_urlsafe(32)
            expires_at = now + timedelta(seconds=self.ttl_seconds)
            self._tickets[hash_token(token)] = TicketGrant(
                tenant=tenant,
                recording_id=recording_id,
                subject=subject,
                expires_at=expires_at,
            )
            return token, expires_at

    async def consume(self, token: str, *, recording_id: str) -> TicketGrant:
        digest = hash_token(token)
        now = datetime.now(timezone.utc)
        async with self._lock:
            grant = self._tickets.pop(digest, None)
        if grant is None:
            raise TicketError("WebSocket ticket is invalid or already used")
        if grant.expires_at <= now:
            raise TicketError("WebSocket ticket has expired")
        if not hmac.compare_digest(grant.recording_id, recording_id):
            raise TicketError("WebSocket ticket belongs to another recording")
        return grant


__all__ = ["TicketError", "TicketGrant", "WebSocketTicketManager", "hash_token", "token_matches"]
