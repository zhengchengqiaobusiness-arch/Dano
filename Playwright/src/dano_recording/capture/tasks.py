"""Lifecycle-bound asyncio task supervision for capture callbacks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class TaskSupervisor:
    """Own background work created by synchronous Playwright event callbacks."""

    def __init__(self, on_error: Callable[[BaseException], None] | None = None) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._on_error = on_error
        self._closed = False

    @property
    def pending_count(self) -> int:
        return sum(not task.done() for task in self._tasks)

    def create(self, awaitable: Awaitable[Any]) -> asyncio.Task[Any]:
        if self._closed:
            if hasattr(awaitable, "close"):
                awaitable.close()  # type: ignore[union-attr]
            raise RuntimeError("task supervisor is closed")
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None and self._on_error is not None:
            # Error reporting is secondary to consuming the task exception.
            # A full/failing fact ledger must not turn this callback into an
            # unhandled event-loop exception.
            try:
                self._on_error(error)
            except Exception:
                pass

    async def drain(self, *, timeout: float | None = None) -> bool:
        """Wait for owned work, returning false when a bounded drain times out."""

        pending = tuple(task for task in self._tasks if not task.done())
        if not pending:
            return True
        if timeout is None:
            await asyncio.gather(*pending, return_exceptions=True)
            return True
        if timeout <= 0:
            raise ValueError("drain timeout must be positive")
        _, unfinished = await asyncio.wait(pending, timeout=timeout)
        return not unfinished

    async def cancel_pending(self) -> None:
        """Cancel unfinished work without closing the reusable supervisor."""

        pending = tuple(task for task in self._tasks if not task.done())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def close(self, *, cancel: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        pending = tuple(task for task in self._tasks if not task.done())
        if cancel:
            for task in pending:
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
