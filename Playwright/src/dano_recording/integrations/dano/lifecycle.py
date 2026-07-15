"""Lifecycle is injected by the gateway to preserve its single shared store."""

from __future__ import annotations

from typing import Awaitable, Callable

LifecycleCallback = Callable[[dict], Awaitable[None]]


async def notify_published(callback: LifecycleCallback | None, payload: dict) -> None:
    if callback:
        await callback(payload)
