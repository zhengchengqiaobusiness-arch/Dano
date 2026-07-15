"""One long-running Node Pi sidecar with many persistent AgentSessions."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
from typing import Any, Awaitable, Callable
from uuid import uuid4

import structlog

log = structlog.get_logger(__name__)

ToolHandler = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class PiUnavailable(RuntimeError):
    """The Pi runtime is unavailable. V3 must never fall back to a Python model."""


class PiSidecarClient:
    def __init__(
        self,
        *,
        script_path: Path,
        tool_handler: ToolHandler,
        event_handler: EventHandler | None = None,
        env: dict[str, str] | None = None,
        request_timeout_s: float = 600.0,
    ) -> None:
        self.script_path = script_path.resolve()
        self.tool_handler = tool_handler
        self.event_handler = event_handler
        self.extra_env = dict(env or {})
        self.request_timeout_s = request_timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._session_specs: dict[str, dict[str, str]] = {}
        self._closed = False

    @property
    def running(self) -> bool:
        return bool(self._proc and self._proc.returncode is None)

    @property
    def model_id(self) -> str:
        """Server-owned model identity used in immutable review evidence."""

        return str(
            self.extra_env.get("DANO_PI_MODEL")
            or os.environ.get("DANO_PI_MODEL")
            or "recording-pi"
        )

    async def start(self) -> None:
        async with self._start_lock:
            if self.running:
                return
            if self._closed:
                raise PiUnavailable("Pi sidecar client has been closed")
            node = shutil.which("node")
            if not node:
                raise PiUnavailable("Node.js not found; recording Pi is unavailable")
            if not self.script_path.is_file():
                raise PiUnavailable(f"Pi sidecar missing: {self.script_path}")
            await self._stop_process(permanent=False)
            allowed = {
                "PATH", "Path", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP",
                "HOME", "USERPROFILE", "NODE_OPTIONS",
                "DANO_PI_API_KEY", "DANO_PI_BASE_URL", "DANO_PI_PROVIDER", "DANO_PI_MODEL",
                "DANO_PI_SESSION_DIR", "PI_STUB",
            }
            child_env = {k: v for k, v in os.environ.items() if k in allowed}
            child_env.update(self.extra_env)
            proc = await asyncio.create_subprocess_exec(
                node,
                str(self.script_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
                cwd=str(self.script_path.parent),
            )
            self._proc = proc
            self._reader_task = asyncio.create_task(
                self._read_stdout(proc), name="recording-pi-stdout"
            )
            self._stderr_task = asyncio.create_task(
                self._read_stderr(proc), name="recording-pi-stderr"
            )
            try:
                hello = await self._request_once("ping", {}, timeout_s=30)
                if not hello.get("ok"):
                    raise PiUnavailable(hello.get("error") or "Pi sidecar failed its health check")
                # A restarted process is empty.  Recover every planner/reviewer
                # session before allowing the interrupted command to continue.
                for spec in tuple(self._session_specs.values()):
                    opened = await self._request_once("open_session", spec)
                    spec["session_path"] = str(opened.get("session_path") or spec["session_path"])
            except Exception:
                await self._stop_process(permanent=False)
                raise

    async def close(self) -> None:
        await self._stop_process(permanent=True)

    async def _stop_process(self, *, permanent: bool) -> None:
        if permanent:
            self._closed = True
        if self.running:
            try:
                await self._request_once("shutdown", {}, timeout_s=5)
            except Exception:  # noqa: BLE001
                pass
        proc = self._proc
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        for task in (self._reader_task, self._stderr_task):
            if task and task is not asyncio.current_task():
                task.cancel()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(PiUnavailable("Pi sidecar stopped"))
        self._pending.clear()
        self._proc = None
        self._reader_task = None
        self._stderr_task = None

    async def open_session(
        self,
        *,
        session_id: str,
        recording_id: str,
        role: str,
        session_path: str = "",
    ) -> dict[str, Any]:
        spec = {
            "session_id": session_id,
            "recording_id": recording_id,
            "role": role,
            "session_path": session_path,
        }
        self._session_specs[session_id] = spec
        result = await self._request(
            "open_session",
            spec,
        )
        recovered_path = str(result.get("session_path") or session_path)
        self._session_specs[session_id]["session_path"] = recovered_path
        return result

    async def prompt(self, *, session_id: str, prompt: str, revision: int) -> dict[str, Any]:
        result = await self._request(
            "prompt",
            {"session_id": session_id, "prompt": prompt, "revision": revision},
        )
        spec = self._session_specs.get(session_id)
        if spec is not None and result.get("session_path"):
            spec["session_path"] = str(result["session_path"])
        return result

    async def cancel(self, session_id: str) -> dict[str, Any]:
        return await self._request("cancel", {"session_id": session_id}, timeout_s=10)

    async def close_session(self, session_id: str) -> dict[str, Any]:
        result = await self._request("close_session", {"session_id": session_id}, timeout_s=10)
        self._session_specs.pop(session_id, None)
        return result

    async def _request(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        if not self.running:
            await self.start()
        try:
            return await self._request_once(command, payload, timeout_s=timeout_s)
        except PiUnavailable as exc:
            retryable_disconnect = command == "prompt" and any(
                marker in str(exc).lower()
                for marker in ("exited unexpectedly", "sidecar is not running", "stdin is closed")
            )
            if not retryable_disconnect or self._closed:
                raise
            await self.start()
            # The same expected revision and active coordinator turn are reused.
            # If the first process committed before dying, its second tool call
            # is rejected and repository optimistic locking prevents a duplicate.
            return await self._request_once(command, payload, timeout_s=timeout_s)

    async def _request_once(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        if not self.running or not self._proc or not self._proc.stdin:
            raise PiUnavailable("Pi sidecar is not running")
        request_id = str(uuid4())
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future
        await self._send({"type": "command", "command": command, "request_id": request_id, **payload})
        try:
            result = await asyncio.wait_for(
                future,
                timeout=self.request_timeout_s if timeout_s is None else timeout_s,
            )
        except TimeoutError as exc:
            raise PiUnavailable(f"Pi command timed out: {command}") from exc
        finally:
            self._pending.pop(request_id, None)
        if not result.get("ok", False):
            raise PiUnavailable(result.get("error") or f"Pi command failed: {command}")
        return result

    async def _send(self, message: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise PiUnavailable("Pi sidecar stdin is closed")
        data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()

    async def _read_stdout(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout
        try:
            while raw := await proc.stdout.readline():
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("recording_pi.invalid_json", line=raw[:200].decode(errors="replace"))
                    continue
                kind = message.get("type")
                if kind == "response":
                    future = self._pending.get(str(message.get("request_id") or ""))
                    if future and not future.done():
                        future.set_result(message)
                elif kind == "tool_request":
                    asyncio.create_task(self._handle_tool_request(message))
                elif kind == "event" and self.event_handler:
                    await self.event_handler(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("recording_pi.stdout_failed", error=str(exc))
        finally:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1)
                except Exception:  # noqa: BLE001
                    pass
            if not self._closed and self._proc is proc:
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(PiUnavailable("Pi sidecar exited unexpectedly"))

    async def _handle_tool_request(self, message: dict[str, Any]) -> None:
        call_id = str(message.get("call_id") or "")
        try:
            result = await self.tool_handler(
                str(message.get("session_id") or ""),
                str(message.get("tool") or ""),
                dict(message.get("params") or {}),
            )
            await self._send({"type": "tool_result", "call_id": call_id, "ok": True, "result": result})
        except Exception as exc:  # noqa: BLE001
            await self._send({"type": "tool_result", "call_id": call_id, "ok": False, "error": str(exc)})

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr
        try:
            while raw := await proc.stderr.readline():
                log.info("recording_pi.sidecar", detail=raw.decode(errors="replace").rstrip()[:2000])
        except asyncio.CancelledError:
            raise
