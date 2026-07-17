"""Lease-backed browser sessions and page/frame/popup fact capture."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from dano_recording.capture.diagnostics import DiagnosticsObserver
from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.capture.tasks import TaskSupervisor
from dano_recording.domain._base import new_id
from dano_recording.domain.facts import FactKind, RecordingFact


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionCapacityError(RuntimeError):
    pass


class SessionNotFound(LookupError):
    pass


class InvalidResumeToken(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class BrowserLease:
    tenant: str
    recording_id: str
    expires_at: datetime
    attached: bool

    @property
    def expired(self) -> bool:
        return self.expires_at <= _utc_now()


@dataclass(slots=True)
class BrowserSession:
    tenant: str
    recording_id: str
    context: Any
    resume_token_hash: str
    lease_until: datetime
    browser: Any | None = None
    storage_state_reference: str | None = None
    attached: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    closed: bool = False

    @property
    def lease(self) -> BrowserLease:
        return BrowserLease(
            tenant=self.tenant,
            recording_id=self.recording_id,
            expires_at=self.lease_until,
            attached=self.attached,
        )

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # Context first flushes page/network activity, then the owning browser.
        seen: set[int] = set()
        errors: list[BaseException] = []
        for resource in (self.context, self.browser):
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            closer = getattr(resource, "close", None)
            if closer is None:
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise errors[0]


SessionRestorer = Callable[[str, str, str], Awaitable[BrowserSession | None]]


class BrowserSessionManager:
    """Keeps browser contexts alive across WebSocket disconnects.

    Resume tokens are compared as hashes and are never written to captured
    facts.  A restorer receives only the token hash, allowing process-level
    recovery from a secure storage-state reference without exposing credentials.
    """

    def __init__(
        self,
        *,
        lease_seconds: float = 120.0,
        max_sessions: int = 64,
        cleanup_interval: float = 15.0,
        restorer: SessionRestorer | None = None,
    ) -> None:
        if lease_seconds <= 0 or cleanup_interval <= 0 or max_sessions < 1:
            raise ValueError("lease, cleanup interval, and capacity must be positive")
        self.lease_seconds = lease_seconds
        self.max_sessions = max_sessions
        self.cleanup_interval = cleanup_interval
        self._restorer = restorer
        self._sessions: dict[tuple[str, str], BrowserSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._closed = False

    @staticmethod
    def _key(tenant: str, recording_id: str) -> tuple[str, str]:
        tenant = tenant.strip()
        recording_id = recording_id.strip()
        if not tenant or not recording_id:
            raise ValueError("tenant and recording_id are required")
        return tenant, recording_id

    def _new_deadline(self) -> datetime:
        return _utc_now() + timedelta(seconds=self.lease_seconds)

    async def create(
        self,
        *,
        tenant: str,
        recording_id: str,
        context: Any,
        browser: Any | None = None,
        resume_token: str | None = None,
        storage_state_reference: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[BrowserSession, str]:
        """Register a newly launched context and issue its resume token."""

        token = resume_token or secrets.token_urlsafe(32)
        key = self._key(tenant, recording_id)
        async with self._lock:
            if self._closed:
                raise RuntimeError("browser session manager is closed")
            await self._cleanup_expired_locked()
            if key in self._sessions:
                raise ValueError("browser session already exists")
            if len(self._sessions) >= self.max_sessions:
                raise SessionCapacityError("browser session capacity reached")
            session = BrowserSession(
                tenant=key[0],
                recording_id=key[1],
                context=context,
                browser=browser,
                resume_token_hash=_token_hash(token),
                lease_until=self._new_deadline(),
                storage_state_reference=storage_state_reference,
                metadata=dict(metadata or {}),
            )
            self._sessions[key] = session
            return session, token

    async def open(
        self,
        *,
        tenant: str,
        recording_id: str,
        resume_token: str,
    ) -> BrowserSession:
        """Attach to an existing lease or securely restore it after restart."""

        key = self._key(tenant, recording_id)
        supplied_hash = _token_hash(resume_token)
        async with self._lock:
            if self._closed:
                raise RuntimeError("browser session manager is closed")
            session = self._sessions.get(key)
            if session is not None and session.closed:
                self._sessions.pop(key, None)
                session = None
            if session is not None:
                if not hmac.compare_digest(session.resume_token_hash, supplied_hash):
                    raise InvalidResumeToken("resume token does not match recording")
                if session.lease_until <= _utc_now():
                    self._sessions.pop(key, None)
                    await session.close()
                    session = None
                else:
                    session.attached = True
                    session.lease_until = self._new_deadline()
                    return session

            if self._restorer is None:
                raise SessionNotFound("browser session lease is unavailable")
            restored = await self._restorer(key[0], key[1], supplied_hash)
            if restored is None:
                raise SessionNotFound("browser session could not be restored")
            if restored.tenant != key[0] or restored.recording_id != key[1]:
                await restored.close()
                raise ValueError("restorer returned a session for the wrong scope")
            if not hmac.compare_digest(restored.resume_token_hash, supplied_hash):
                await restored.close()
                raise InvalidResumeToken("restored session token does not match")
            if len(self._sessions) >= self.max_sessions:
                await self._cleanup_expired_locked()
            if len(self._sessions) >= self.max_sessions:
                await restored.close()
                raise SessionCapacityError("browser session capacity reached")
            restored.attached = True
            restored.closed = False
            restored.lease_until = self._new_deadline()
            self._sessions[key] = restored
            return restored

    async def renew(self, *, tenant: str, recording_id: str) -> BrowserLease:
        key = self._key(tenant, recording_id)
        async with self._lock:
            session = self._sessions.get(key)
            if session is None or session.closed:
                raise SessionNotFound("browser session not found")
            session.lease_until = self._new_deadline()
            return session.lease

    async def detach(self, *, tenant: str, recording_id: str) -> BrowserLease:
        """Release the client while preserving the browser for the lease window."""

        key = self._key(tenant, recording_id)
        async with self._lock:
            session = self._sessions.get(key)
            if session is None or session.closed:
                raise SessionNotFound("browser session not found")
            session.attached = False
            session.lease_until = self._new_deadline()
            return session.lease

    async def close_session(self, *, tenant: str, recording_id: str) -> bool:
        key = self._key(tenant, recording_id)
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session is None:
            return False
        await session.close()
        return True

    async def cleanup_expired(self) -> int:
        async with self._lock:
            return await self._cleanup_expired_locked()

    async def _cleanup_expired_locked(self) -> int:
        now = _utc_now()
        expired = [
            (key, session)
            for key, session in self._sessions.items()
            if session.closed or session.lease_until <= now
        ]
        for key, _ in expired:
            self._sessions.pop(key, None)
        if expired:
            await asyncio.gather(
                *(session.close() for _, session in expired),
                return_exceptions=True,
            )
        return len(expired)

    def start_cleanup(self) -> None:
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        if self._closed:
            raise RuntimeError("browser session manager is closed")
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.cleanup_interval)
                await self.cleanup_expired()
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            await asyncio.gather(self._cleanup_task, return_exceptions=True)
            self._cleanup_task = None
        async with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)

    async def __aenter__(self) -> "BrowserSessionManager":
        self.start_cleanup()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


# The architecture document names SessionManager.open(); retain that direct API.
SessionManager = BrowserSessionManager


class BrowserCapture:
    """Attach to a context and emit page, frame, popup, and diagnostics facts."""

    def __init__(
        self,
        ledger: FactLedger,
        *,
        redaction: RedactionPolicy | None = None,
    ) -> None:
        self.ledger = ledger
        self.redaction = redaction or RedactionPolicy()
        self.diagnostics = DiagnosticsObserver(ledger, redaction=self.redaction)
        self.tasks = TaskSupervisor(self._task_error)
        self._page_ids: dict[int, str] = {}
        self._frame_ids: dict[int, str] = {}
        self._popup_linked: set[int] = set()
        self._attached_context_ids: set[int] = set()
        self._listeners: list[tuple[Any, str, Any]] = []
        self._paused = False

    def _listen(self, emitter: Any, event: str, handler: Any) -> None:
        emitter.on(event, handler)
        self._listeners.append((emitter, event, handler))

    def attach_context(self, context: Any) -> None:
        context_key = id(context)
        if context_key in self._attached_context_ids:
            return
        self._attached_context_ids.add(context_key)
        self._listen(context, "page", self.attach_page)
        for page in tuple(getattr(context, "pages", ()) or ()):
            self.attach_page(page)

    def attach_page(self, page: Any, *, opener: Any | None = None) -> str:
        page_key = id(page)
        if page_key in self._page_ids:
            if opener is not None and page_key not in self._popup_linked:
                opener_page_id = self._page_ids.get(id(opener))
                if opener_page_id is not None:
                    self._popup_linked.add(page_key)
                    self._emit_page(
                        "popup_opened",
                        page_id=self._page_ids[page_key],
                        url=str(getattr(page, "url", "") or ""),
                        opener_page_id=opener_page_id,
                        is_popup=True,
                    )
            return self._page_ids[page_key]
        page_id = new_id()
        self._page_ids[page_key] = page_id
        opener_page_id = self._page_ids.get(id(opener)) if opener is not None else None
        if opener_page_id:
            self._popup_linked.add(page_key)
        self._emit_page(
            "popup_opened" if opener_page_id else "page_opened",
            page_id=page_id,
            url=str(getattr(page, "url", "") or ""),
            opener_page_id=opener_page_id,
            is_popup=bool(opener_page_id),
        )
        self._listen(page, "popup", lambda popup: self.attach_page(popup, opener=page))
        self._listen(
            page,
            "framenavigated",
            lambda frame: self.frame_event(frame, page_id=page_id, event="navigated"),
        )
        self._listen(
            page,
            "frameattached",
            lambda frame: self.frame_event(frame, page_id=page_id, event="attached"),
        )
        self._listen(
            page,
            "framedetached",
            lambda frame: self.frame_event(frame, page_id=page_id, event="detached"),
        )
        self._listen(
            page,
            "close",
            lambda *_: self._emit_page("page_closed", page_id=page_id),
        )
        self.diagnostics.attach(page, page_id=page_id)
        for frame in tuple(getattr(page, "frames", ()) or ()):
            self.frame_event(frame, page_id=page_id, event="present")
        return page_id

    def page_id(self, page: Any) -> str | None:
        return self._page_ids.get(id(page))

    def frame_id(self, frame: Any) -> str | None:
        return self._frame_ids.get(id(frame))

    def frame_event(
        self,
        frame: Any,
        *,
        page_id: str,
        event: str,
    ) -> RecordingFact | None:
        frame_key = id(frame)
        frame_id = self._frame_ids.setdefault(frame_key, new_id())
        parent = getattr(frame, "parent_frame", None)
        parent_id = self._frame_ids.setdefault(id(parent), new_id()) if parent is not None else None
        return self._emit_page(
            f"frame_{event}",
            page_id=page_id,
            frame_id=frame_id,
            parent_frame_id=parent_id,
            name=str(getattr(frame, "name", "") or ""),
            url=str(getattr(frame, "url", "") or ""),
            is_main_frame=parent is None,
        )

    def _emit_page(
        self,
        event: str,
        *,
        page_id: str,
        **payload: Any,
    ) -> RecordingFact | None:
        if self._paused:
            return None
        clean = self.redaction.redact_value({"event": event, **payload})
        if "url" in clean:
            clean["url"] = self.redaction.redact_url(str(clean["url"]))
        return self.ledger.emit(
            RecordingFact,
            kind=FactKind.PAGE,
            page_id=page_id,
            payload=clean,
            redacted=True,
        )

    def _task_error(self, error: BaseException) -> None:
        self.diagnostics.emit(
            "capture_task_error",
            error_type=type(error).__name__,
            message=str(error),
        )

    def pause(self) -> None:
        self._paused = True
        self.diagnostics.pause()

    def resume(self) -> None:
        self._paused = False
        self.diagnostics.resume()

    async def close(self) -> None:
        listeners, self._listeners = self._listeners, []
        for emitter, event, handler in listeners:
            remove = getattr(emitter, "remove_listener", None)
            if remove is not None:
                try:
                    remove(event, handler)
                except Exception:
                    pass
        self._attached_context_ids.clear()
        await self.tasks.close()
        await self.diagnostics.close()


class PlaywrightBrowserHandle:
    """Closes both the browser and the Playwright driver process."""

    def __init__(self, playwright: Any, browser: Any) -> None:
        self.playwright = playwright
        self.browser = browser
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.browser, name)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.browser.close()
        finally:
            await self.playwright.stop()


async def launch_persistent_context(**launch_options: Any) -> tuple[Any, Any]:
    """Lazily launch a browser and restorable context.

    ``context_options`` is passed to ``browser.new_context`` (including a
    storage-state reference); remaining options are passed to ``launch``.
    Importing this module remains safe without Playwright installed.
    """

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional installation
        raise RuntimeError("Playwright is required to launch a real browser context") from exc
    playwright = await async_playwright().start()
    browser_type_name = str(launch_options.pop("browser_type", "chromium"))
    context_options = dict(launch_options.pop("context_options", {}) or {})
    for key in ("storage_state", "locale", "timezone_id", "user_agent", "viewport"):
        if key in launch_options:
            context_options[key] = launch_options.pop(key)
    browser_type = getattr(playwright, browser_type_name)
    try:
        browser = await browser_type.launch(**launch_options)
        context = await browser.new_context(**context_options)
    except Exception:
        await playwright.stop()
        raise
    return PlaywrightBrowserHandle(playwright, browser), context
