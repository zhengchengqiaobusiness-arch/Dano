"""Append-only, tenant-scoped fact ledger used by browser capture."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import inspect
from threading import RLock
from typing import Any, TypeVar

from dano_recording.domain.facts import RecordingFact


class CaptureCapacityExceeded(RuntimeError):
    """Raised instead of silently dropping a captured fact."""


class FactSequenceError(ValueError):
    """Raised when a caller attempts to append a non-monotonic fact."""


FactT = TypeVar("FactT", bound=RecordingFact)


class FactLedger:
    """Small append-only ledger with deterministic sequence allocation.

    Persistence is normally supplied through ``on_append``.  The in-memory copy
    is bounded so a disconnected browser cannot exhaust the service.  Reaching
    the bound is a hard, visible failure: capture never pretends that a partial
    ledger is complete.
    """

    def __init__(
        self,
        *,
        tenant: str,
        recording_id: str,
        max_facts: int = 50_000,
        on_append: Callable[[RecordingFact], None] | None = None,
        initial_facts: Iterable[RecordingFact] = (),
    ) -> None:
        if not tenant.strip() or not recording_id.strip():
            raise ValueError("tenant and recording_id are required")
        if max_facts < 1:
            raise ValueError("max_facts must be positive")
        self.tenant = tenant.strip()
        self.recording_id = recording_id.strip()
        self.max_facts = max_facts
        self._on_append = on_append
        self._facts: list[RecordingFact] = []
        self._next_sequence = 0
        self._lock = RLock()
        self._failed = False
        self._notifying = False
        for fact in initial_facts:
            self.append(fact, notify=False)

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def next_sequence(self) -> int:
        with self._lock:
            return self._next_sequence

    def emit(self, fact_type: type[FactT] = RecordingFact, /, **values: Any) -> FactT:
        """Construct and append one fact using the next sequence number."""

        with self._lock:
            if self._failed:
                raise CaptureCapacityExceeded("capture ledger is already failed")
            if len(self._facts) >= self.max_facts:
                self._failed = True
                raise CaptureCapacityExceeded(
                    f"recording {self.recording_id!r} reached {self.max_facts} facts"
                )
            values.setdefault("tenant", self.tenant)
            values.setdefault("recording_id", self.recording_id)
            values.setdefault("sequence", self._next_sequence)
            fact = fact_type(**values)
            self._append_locked(fact, notify=True)
            return fact.model_copy(deep=True)

    def append(self, fact: FactT, *, notify: bool = True) -> FactT:
        """Append an already constructed fact after strict scope/sequence checks."""

        with self._lock:
            if self._failed:
                raise CaptureCapacityExceeded("capture ledger is already failed")
            if len(self._facts) >= self.max_facts:
                self._failed = True
                raise CaptureCapacityExceeded(
                    f"recording {self.recording_id!r} reached {self.max_facts} facts"
                )
            self._append_locked(fact, notify=notify)
            return fact.model_copy(deep=True)

    def _append_locked(self, fact: FactT, *, notify: bool) -> None:
        if fact.tenant != self.tenant or fact.recording_id != self.recording_id:
            raise ValueError("fact scope does not match ledger scope")
        if fact.sequence != self._next_sequence:
            raise FactSequenceError(
                f"expected sequence {self._next_sequence}, got {fact.sequence}"
            )
        stored = fact.model_copy(deep=True)
        # Persist first.  If persistence rejects the row, it must not appear in
        # the in-memory ledger either.
        if notify and self._on_append is not None:
            if self._notifying:
                raise RuntimeError("fact persistence callback cannot append reentrantly")
            self._notifying = True
            try:
                result = self._on_append(stored.model_copy(deep=True))
                if inspect.isawaitable(result):
                    closer = getattr(result, "close", None)
                    if closer is not None:
                        closer()
                    raise TypeError("FactLedger on_append must be synchronous")
            finally:
                self._notifying = False
        self._facts.append(stored)
        self._next_sequence += 1

    def snapshot(self) -> tuple[RecordingFact, ...]:
        """Return deep copies so nested payload mutation cannot change history."""

        with self._lock:
            return tuple(fact.model_copy(deep=True) for fact in self._facts)

    def __len__(self) -> int:
        with self._lock:
            return len(self._facts)
