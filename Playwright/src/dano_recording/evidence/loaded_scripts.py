"""Collect only scripts actually parsed by the current page."""

from __future__ import annotations

import hashlib
import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.capture.tasks import TaskSupervisor
from dano_recording.domain._base import new_id
from dano_recording.domain.facts import FactKind, RecordingFact


_SOURCE_MAP_RE = re.compile(
    r"(?://[#@]\s*sourceMappingURL\s*=\s*([^\s]+)|/\*[#@]\s*sourceMappingURL\s*=\s*([^*]+?)\s*\*/)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LoadedScript:
    script_id: str
    url: str
    script_hash: str
    byte_size: int
    inline: bool
    source_map_url: str | None
    target_id: str = "manual"
    source_reference: str | None = None
    source: str | None = field(default=None, repr=False, compare=False)
    truncated: bool = False

    def pi_projection(self) -> dict[str, Any]:
        map_url = "inline" if (self.source_map_url or "").startswith("data:") else self.source_map_url
        return {
            "script_id": self.script_id,
            "target_id": self.target_id,
            "url": self.url,
            "script_hash": self.script_hash,
            "byte_size": self.byte_size,
            "inline": self.inline,
            "source_map_url": map_url,
            "truncated": self.truncated,
        }


SourceStore = Callable[[str, bytes], str | Awaitable[str]]


class LoadedScriptCollector:
    """CDP ``Debugger.scriptParsed`` collector with explicit byte limits."""

    def __init__(
        self,
        ledger: FactLedger | None = None,
        *,
        source_store: SourceStore | None = None,
        redaction: RedactionPolicy | None = None,
        max_scripts: int = 2_000,
        max_script_bytes: int = 5_242_880,
        max_total_bytes: int = 52_428_800,
    ) -> None:
        if min(max_scripts, max_script_bytes, max_total_bytes) < 1:
            raise ValueError("script evidence capacities must be positive")
        self.ledger = ledger
        self.tenant = ledger.tenant if ledger is not None else None
        self.source_store = source_store
        self.redaction = redaction or RedactionPolicy()
        self.max_scripts = max_scripts
        self.max_script_bytes = max_script_bytes
        self.max_total_bytes = max_total_bytes
        self._scripts: dict[tuple[str, str], LoadedScript] = {}
        self._total_bytes = 0
        self._cdp_sessions: list[Any] = []
        self._tasks = TaskSupervisor(self._task_error)
        self._paused = False

    @property
    def scripts(self) -> tuple[LoadedScript, ...]:
        return tuple(self._scripts.values())

    async def attach_cdp(self, context: Any, page: Any, *, page_id: str | None = None) -> Any:
        """Enable Debugger events for one page using a lazily supplied context."""

        create = getattr(context, "new_cdp_session", None)
        if create is None:
            raise RuntimeError("the browser context does not expose a CDP session")
        session = create(page)
        session = await session if inspect.isawaitable(session) else session
        self._cdp_sessions.append(session)
        target_id = new_id()

        def parsed(event: dict[str, Any]) -> None:
            if self._paused:
                return
            self._tasks.create(
                self.collect_parsed_script(
                    event,
                    get_script_source=lambda script_id: session.send(
                        "Debugger.getScriptSource", {"scriptId": script_id}
                    ),
                    target_id=target_id,
                    page_id=page_id,
                )
            )

        session.on("Debugger.scriptParsed", parsed)
        enabled = session.send("Debugger.enable")
        if inspect.isawaitable(enabled):
            await enabled
        return session

    async def collect_parsed_script(
        self,
        event: dict[str, Any],
        *,
        get_script_source: Callable[[str], Any],
        target_id: str = "manual",
        page_id: str | None = None,
    ) -> LoadedScript | None:
        if self._paused:
            return None
        script_id = str(event.get("scriptId") or event.get("script_id") or "")
        key = (target_id, script_id)
        if not script_id or key in self._scripts:
            return self._scripts.get(key)
        if len(self._scripts) >= self.max_scripts:
            self._diagnostic("script_capacity", page_id=page_id, script_id=script_id)
            return None
        raw_result = get_script_source(script_id)
        raw_result = await raw_result if inspect.isawaitable(raw_result) else raw_result
        if isinstance(raw_result, dict):
            source = str(raw_result.get("scriptSource") or raw_result.get("source") or "")
        else:
            source = str(raw_result or "")
        return await self.add(
            script_id=script_id,
            target_id=target_id,
            url=str(event.get("url") or ""),
            source=source,
            source_map_url=str(event.get("sourceMapURL") or "") or None,
            page_id=page_id,
        )

    async def add(
        self,
        *,
        script_id: str,
        target_id: str = "manual",
        url: str,
        source: str,
        source_map_url: str | None = None,
        page_id: str | None = None,
    ) -> LoadedScript | None:
        key = (target_id, script_id)
        if key in self._scripts:
            return self._scripts[key]
        if len(self._scripts) >= self.max_scripts:
            self._diagnostic("script_capacity", page_id=page_id, script_id=script_id)
            return None
        raw = source.encode("utf-8", errors="replace")
        original_size = len(raw)
        if self._total_bytes + min(original_size, self.max_script_bytes) > self.max_total_bytes:
            self._diagnostic("script_total_capacity", page_id=page_id, script_id=script_id)
            return None
        truncated = original_size > self.max_script_bytes
        stored_bytes = raw[: self.max_script_bytes]
        stored_source = stored_bytes.decode("utf-8", errors="replace")
        digest = hashlib.sha256(raw).hexdigest()
        map_url = source_map_url or self.find_source_map_url(stored_source)
        reference: str | None = None
        if self.source_store is not None:
            # Storage keys are tenant-scoped even when identical third-party
            # bundles appear in different tenants.
            stored_digest = hashlib.sha256(stored_bytes).hexdigest()
            storage_key = (
                f"{self.tenant}/{digest}/{stored_digest}"
                if self.tenant
                else f"{digest}/{stored_digest}"
            )
            result = self.source_store(storage_key, stored_bytes)
            resolved = await result if inspect.isawaitable(result) else result
            if not resolved:
                raise ValueError("source store returned an empty reference")
            reference = str(resolved)
            # Once persisted by reference, do not retain raw source in the object.
            object_source: str | None = None
        else:
            object_source = stored_source
        script = LoadedScript(
            script_id=script_id,
            target_id=target_id,
            url=self.redaction.redact_url(url) if url else "",
            script_hash=digest,
            byte_size=original_size,
            inline=not bool(url),
            source_map_url=(
                map_url
                if (map_url or "").startswith("data:")
                else self.redaction.redact_url(map_url) if map_url else None
            ),
            source_reference=reference,
            source=object_source,
            truncated=truncated,
        )
        self._scripts[key] = script
        self._total_bytes += len(stored_bytes)
        if self.ledger is not None:
            self.ledger.emit(
                RecordingFact,
                kind=FactKind.SCRIPT,
                page_id=page_id,
                payload={**script.pi_projection(), "source_reference": reference},
                redacted=True,
            )
        return script

    @staticmethod
    def find_source_map_url(source: str) -> str | None:
        matches = list(_SOURCE_MAP_RE.finditer(source))
        if not matches:
            return None
        match = matches[-1]
        return (match.group(1) or match.group(2) or "").strip() or None

    def _diagnostic(self, kind: str, *, page_id: str | None, **payload: Any) -> None:
        if self.ledger is not None:
            self.ledger.emit(
                RecordingFact,
                kind=FactKind.DIAGNOSTIC,
                page_id=page_id,
                payload={"type": kind, **self.redaction.redact_value(payload)},
                redacted=True,
            )

    def _task_error(self, error: BaseException) -> None:
        try:
            self._diagnostic(
                "script_capture_error",
                page_id=None,
                error_type=type(error).__name__,
                message=str(error),
            )
        except Exception:
            pass

    async def pause(self) -> None:
        self._paused = True
        if not await self._tasks.drain(timeout=5.0):
            await self._tasks.cancel_pending()

    def resume(self) -> None:
        self._paused = False

    async def reset_generation(self) -> None:
        """Clear script evidence and detach CDP sessions at a paused boundary."""

        if not self._paused:
            raise RuntimeError("script collector must be paused before generation reset")
        await self._tasks.cancel_pending()
        await self._detach_sessions()
        self._scripts.clear()
        self._total_bytes = 0

    async def _detach_sessions(self) -> None:
        sessions, self._cdp_sessions = self._cdp_sessions, []
        for session in sessions:
            detach = getattr(session, "detach", None)
            if detach is None:
                continue
            try:
                result = detach()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001 - optional browser evidence
                self._task_error(exc)

    async def close(self) -> None:
        self._paused = True
        drained = await self._tasks.drain(timeout=5.0)
        await self._tasks.close(cancel=not drained)
        await self._detach_sessions()
