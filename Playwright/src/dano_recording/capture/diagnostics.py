"""Browser diagnostic capture with mandatory redaction."""

from __future__ import annotations

import inspect
from typing import Any

from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.capture.tasks import TaskSupervisor
from dano_recording.domain.facts import FactKind, RecordingFact


class DiagnosticsObserver:
    def __init__(self, ledger: FactLedger, *, redaction: RedactionPolicy | None = None) -> None:
        self.ledger = ledger
        self.redaction = redaction or RedactionPolicy()
        self.tasks = TaskSupervisor(self._task_error)
        self._listeners: list[tuple[Any, str, Any]] = []
        self._paused = False

    def attach(self, page: Any, *, page_id: str) -> None:
        self._listen(page, "console", lambda message: self.console(message, page_id=page_id))
        self._listen(page, "pageerror", lambda error: self.page_error(error, page_id=page_id))
        self._listen(page, "crash", lambda *_: self.emit("page_crash", page_id=page_id))
        self._listen(
            page,
            "dialog",
            lambda dialog: self._handle_dialog(dialog, page_id=page_id),
        )

    def _listen(self, emitter: Any, event: str, handler: Any) -> None:
        emitter.on(event, handler)
        self._listeners.append((emitter, event, handler))

    def emit(
        self,
        diagnostic_type: str,
        *,
        page_id: str | None = None,
        **payload: Any,
    ) -> RecordingFact | None:
        if self._paused:
            return None
        clean = self.redaction.redact_value({"type": diagnostic_type, **payload})
        return self.ledger.emit(
            RecordingFact,
            kind=FactKind.DIAGNOSTIC,
            page_id=page_id,
            payload=clean,
            redacted=True,
        )

    def console(self, message: Any, *, page_id: str) -> RecordingFact | None:
        location = getattr(message, "location", None)
        return self.emit(
            "console",
            page_id=page_id,
            level=str(getattr(message, "type", "log")),
            message=str(getattr(message, "text", message)),
            location=location if isinstance(location, dict) else {},
        )

    def page_error(self, error: Any, *, page_id: str) -> RecordingFact | None:
        return self.emit(
            "pageerror",
            page_id=page_id,
            message=str(error),
            name=type(error).__name__,
        )

    def dialog(self, dialog: Any, *, page_id: str) -> RecordingFact | None:
        return self.emit(
            "dialog",
            page_id=page_id,
            dialog_type=str(getattr(dialog, "type", "unknown")),
            message=str(getattr(dialog, "message", "")),
            default_value=str(getattr(dialog, "default_value", "")),
        )

    def _handle_dialog(self, dialog: Any, *, page_id: str) -> RecordingFact | None:
        # Once a listener exists Playwright no longer auto-dismisses dialogs.
        # Schedule the safe dismissal before persistence, so a full ledger
        # cannot leave the page deadlocked.
        self.tasks.create(self._dismiss_dialog(dialog))
        return self.dialog(dialog, page_id=page_id)

    @staticmethod
    async def _dismiss_dialog(dialog: Any) -> None:
        result = dialog.dismiss()
        if inspect.isawaitable(result):
            await result

    def _task_error(self, error: BaseException) -> None:
        try:
            self.emit(
                "diagnostic_task_error",
                error_type=type(error).__name__,
                message=str(error),
            )
        except Exception:
            # Capture may already be failed/full; task completion must still be
            # consumed so asyncio never reports an unhandled exception.
            pass

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    async def close(self) -> None:
        listeners, self._listeners = self._listeners, []
        for emitter, event, handler in listeners:
            remove = getattr(emitter, "remove_listener", None)
            if remove is not None:
                try:
                    remove(event, handler)
                except Exception:
                    pass
        drained = await self.tasks.drain(timeout=5.0)
        await self.tasks.close(cancel=not drained)
