"""Action correlation context for network transaction boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from time import monotonic
from typing import Iterator


_current_action: ContextVar[str | None] = ContextVar("dano_recording_action_id", default=None)


def current_action_id() -> str | None:
    return _current_action.get()


class ActionTracker:
    """Shared action window for browser events delivered in separate tasks."""

    def __init__(self, *, grace_seconds: float = 1.0) -> None:
        if grace_seconds < 0:
            raise ValueError("grace_seconds cannot be negative")
        self.grace_seconds = grace_seconds
        self._active: str | None = None
        self._last: str | None = None
        self._last_finished_at = 0.0

    def current(self) -> str | None:
        if self._active is not None:
            return self._active
        if self._last is not None and monotonic() - self._last_finished_at <= self.grace_seconds:
            return self._last
        return None

    @contextmanager
    def scope(self, action_id: str) -> Iterator[None]:
        previous = self._active
        self._active = action_id
        try:
            yield
        finally:
            self._active = previous
            self._last = action_id
            self._last_finished_at = monotonic()


@dataclass(slots=True)
class ActionScope:
    action_id: str
    _token: Token[str | None] | None = None

    def __enter__(self) -> "ActionScope":
        self._token = _current_action.set(self.action_id)
        return self

    def __exit__(self, *_: object) -> None:
        if self._token is not None:
            _current_action.reset(self._token)
            self._token = None


@contextmanager
def action_scope(action_id: str) -> Iterator[None]:
    token = _current_action.set(action_id)
    try:
        yield
    finally:
        _current_action.reset(token)
