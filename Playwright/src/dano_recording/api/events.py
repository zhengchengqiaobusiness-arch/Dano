"""Bounded, tenant-scoped WebSocket event fan-out."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


EventSender = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class _Channel:
    sequence: int = 0
    senders: set[EventSender] = field(default_factory=set)
    history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=250))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RecordingEventBroker:
    def __init__(self) -> None:
        self._channels: dict[tuple[str, str], _Channel] = defaultdict(_Channel)

    def subscribe(self, tenant: str, recording_id: str, sender: EventSender) -> None:
        self._channels[(tenant, recording_id)].senders.add(sender)

    def unsubscribe(self, tenant: str, recording_id: str, sender: EventSender) -> None:
        channel = self._channels.get((tenant, recording_id))
        if channel is not None:
            channel.senders.discard(sender)

    async def publish(self, tenant: str, recording_id: str, event: dict[str, Any]) -> dict[str, Any]:
        channel = self._channels[(tenant, recording_id)]
        async with channel.lock:
            channel.sequence += 1
            value = {**deepcopy(event), "event_seq": channel.sequence, "recording_id": recording_id}
            channel.history.append(value)
            senders = tuple(channel.senders)
        if senders:
            results = await asyncio.gather(*(sender(deepcopy(value)) for sender in senders), return_exceptions=True)
            for sender, result in zip(senders, results, strict=True):
                if isinstance(result, BaseException):
                    channel.senders.discard(sender)
        return value

    def history(self, tenant: str, recording_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        channel = self._channels.get((tenant, recording_id))
        if channel is None:
            return []
        return [deepcopy(item) for item in channel.history if int(item.get("event_seq") or 0) > after]


__all__ = ["RecordingEventBroker"]
