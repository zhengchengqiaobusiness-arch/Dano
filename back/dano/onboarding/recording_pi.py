"""Python owner for one recording-only Pi AgentSession sidecar.

The recording gateway talks to Pi exclusively through this JSONL bridge.  The
bridge owns one long-lived Node process and one Pi session for the lifetime of
the browser recording websocket; prompt history, retries and compaction remain
inside Pi rather than being reconstructed in Python.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import uuid4

import structlog

from dano.agent_tools import materials, runs

log = structlog.get_logger(__name__)
BACK_DIR = Path(__file__).resolve().parent.parent.parent
_OS_ENV_WHITELIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "windir", "ComSpec",
    "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS", "OS", "HOMEDRIVE", "HOMEPATH",
)
_PI_ENV = (
    "DANO_PI_API_KEY",
    "DANO_PI_BASE_URL",
    "DANO_PI_MODEL",
    "DANO_PI_PROVIDER",
    "DANO_RECORDING_PI_MAX_SUBMISSION_ATTEMPTS",
)
_ACTIVE_RECORDING_SESSIONS: dict[str, "RecordingPiSession"] = {}
_ACTIVE_RECORDING_SCOPES: dict[str, "RecordingPiSession"] = {}
_OPAQUE_RECORDING_ID = re.compile(r"recording_[0-9a-f]{32}\Z")


def active_recording_session(run_id: str) -> "RecordingPiSession | None":
    return _ACTIVE_RECORDING_SESSIONS.get(run_id)


class RecordingPiError(RuntimeError):
    """The recording Pi runtime failed or returned an invalid protocol event."""


def _acquire_scope_file_lock(path: Path) -> BinaryIO:
    """Hold a cross-process lock for one persisted Pi JSONL scope."""
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        handle.close()
        raise RecordingPiError("同一录制 Pi Session 已在另一个网关进程中使用") from exc
    return handle


def _release_scope_file_lock(handle: BinaryIO | None) -> None:
    if handle is None:
        return
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        handle.close()


async def _start_tool_server() -> tuple[Any, asyncio.Task, int]:
    """Start the authenticated Dano tool router on an ephemeral loopback port."""
    import uvicorn
    from fastapi import FastAPI

    from dano.agent_tools.app import agent_tools_router

    app = FastAPI(docs_url=None, redoc_url=None)
    app.include_router(agent_tools_router)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", lifespan="off",
    ))
    task = asyncio.create_task(server.serve(), name="recording-pi-tool-server")
    while not server.started:
        if task.done():
            await task
            raise RecordingPiError("录制 Pi 工具服务启动失败")
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, task, port


class RecordingPiSession:
    """One long-lived Pi process/session bound to one recording websocket."""

    def __init__(
        self,
        *,
        tenant: str,
        subsystem: str,
        recording_id: str,
        session_root: str | Path | None = None,
        timeout_s: float = 180.0,
        on_submission_accepted: Callable[[Any, str], None] | None = None,
    ) -> None:
        if not _OPAQUE_RECORDING_ID.fullmatch(recording_id):
            raise ValueError("recording_id 必须是服务端签发的 opaque recording token")
        self.tenant = tenant
        self.subsystem = subsystem
        self.recording_id = recording_id
        self.run_id = f"recording-{uuid4().hex}"
        self.token = secrets.token_hex(16)
        self.timeout_s = timeout_s
        self.session_id: str | None = None
        # session_file is deliberately server-owned.  Callers (and therefore
        # browser payloads) can only present the opaque recording_id.
        self.session_file: str | None = None
        self.resumed = False
        configured_root = os.environ.get("DANO_RECORDING_PI_SESSION_DIR")
        self._session_root = Path(session_root or configured_root or (BACK_DIR / ".dano" / "recording-pi-sessions"))
        self._scope = hashlib.sha256(
            f"{self.tenant}\0{self.subsystem}\0{self.recording_id}".encode("utf-8")
        ).hexdigest()
        self._scope_reserved = False
        self._scope_file_lock: BinaryIO | None = None
        self._session_dir: str | None = None
        self._server: Any = None
        self._server_task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._prompt_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._closed = False
        self.flow_spec: Any = None
        self._analysis_images: list[dict[str, str]] = []
        self._active_analysis_image_count = 0
        self.last_submission_kind = ""
        self.last_submission_warning = ""
        self.last_review: dict[str, Any] = {}
        self._on_submission_accepted = on_submission_accepted

    async def start(self) -> "RecordingPiSession":
        if self._proc is not None:
            return self
        if self._closed:
            raise RecordingPiError("录制 Pi Session 已关闭")

        active = _ACTIVE_RECORDING_SCOPES.get(self._scope)
        if active is not None and active is not self:
            raise RecordingPiError("同一录制 Pi Session 已在另一个连接中使用")
        # Reserve synchronously before the first await so two reconnects can
        # never open and append to the same Pi JSONL concurrently.
        _ACTIVE_RECORDING_SCOPES[self._scope] = self
        self._scope_reserved = True

        try:
            session_dir = (self._session_root / self._scope[:32]).resolve()
            session_dir.mkdir(parents=True, exist_ok=True)
            self._session_dir = str(session_dir)
            self._scope_file_lock = _acquire_scope_file_lock(session_dir / ".pi-session.lock")
            self._server, self._server_task, port = await _start_tool_server()
            runs.register(self.run_id, self.token)
            materials.register(materials.MaterialContext(
                run_id=self.run_id,
                tenant=self.tenant,
                system_instance_id=self.subsystem,
                subsystem=self.subsystem,
            ))
            # On reconnect (including after a gateway restart), discover the
            # persisted Pi JSONL inside the tenant-scoped server directory.
            # Resolve every candidate and reject symlinks/path escapes before
            # handing it to SessionManager.open.
            candidates: list[Path] = []
            for candidate in session_dir.glob("*.jsonl"):
                resolved = candidate.resolve()
                if resolved.parent == session_dir and resolved.is_file():
                    candidates.append(resolved)
            candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
            if candidates:
                self.session_file = str(candidates[0])
                self.resumed = True
            self._proc = await asyncio.create_subprocess_exec(
                "node",
                str(BACK_DIR / "agent" / "run_recording_pi.mjs"),
                cwd=str(BACK_DIR),
                env=self._environment(port),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._stdout_task = asyncio.create_task(self._read_stdout(), name=f"{self.run_id}-stdout")
            self._stderr_task = asyncio.create_task(self._read_stderr(), name=f"{self.run_id}-stderr")
            event = await self._command(
                "start_session",
                session_file=self.session_file,
                session_dir=self._session_dir,
                session_id=self.recording_id,
            )
            self.session_id = str(event.get("session_id") or "") or None
            self.session_file = str(event.get("session_file") or "") or self.session_file
            self.resumed = bool(event.get("resumed", self.resumed))
            if not self.session_id or not self.session_file:
                raise RecordingPiError("Pi 未返回有效的 session_id/session_file")
            _ACTIVE_RECORDING_SESSIONS[self.run_id] = self
            return self
        except BaseException:
            await self.close()
            raise

    def _environment(self, port: int) -> dict[str, str]:
        from dano.config import get_settings

        settings = get_settings()
        env = {key: os.environ[key] for key in _OS_ENV_WHITELIST if key in os.environ}
        env.update({
            "DANO_PI_API_KEY": settings.pi_api_key or "",
            "DANO_PI_BASE_URL": settings.pi_base_url or "",
            "DANO_PI_MODEL": settings.pi_model or "",
            "DANO_PI_PROVIDER": settings.pi_provider or "",
        })
        env.update({key: os.environ[key] for key in _PI_ENV if key in os.environ})
        env.update({
            "DANO_AGENT_TOKEN": self.token,
            "DANO_AGENT_BASE_URL": f"http://127.0.0.1:{port}",
            "DANO_AGENT_RUN_ID": self.run_id,
        })
        return env

    async def prompt(self, text: str, *, timeout_s: float | None = 0) -> dict[str, Any]:
        """Append one turn to the same Pi session; no Python message history exists."""
        if not text.strip():
            raise ValueError("Pi prompt must not be empty")
        if self._proc is None:
            raise RecordingPiError("录制 Pi Session 尚未启动")
        async with self._prompt_lock:
            try:
                images = [dict(image) for image in self._analysis_images]
                self._analysis_images = []
                self._active_analysis_image_count = len(images)
                event = await self._command(
                    "prompt", timeout_s=timeout_s, text=text,
                    images=images,
                )
                # A terminal tool submission has already been persisted by the
                # Python bridge. It is authoritative even if an older sidecar
                # also reports a late limiter/cancel status in the same event.
                if event.get("accepted_submission"):
                    event["status"] = "submitted"
                    event.pop("error", None)
                    return event
                if event.get("status") == "submission_limit":
                    raise RecordingPiError(
                        "录制 Pi 在同一任务中连续提交被拒，已停止本轮以避免无效 Token 消耗；"
                        "请基于最新状态重新发起操作"
                    )
                return event
            except asyncio.TimeoutError as exc:
                try:
                    await self._command("cancel", timeout_s=min(self.timeout_s, 10.0))
                except BaseException as cancel_exc:  # noqa: BLE001
                    raise RecordingPiError(
                        "录制 Pi 操作超时且取消确认失败；会话不可继续使用"
                    ) from cancel_exc
                raise RecordingPiError("录制 Pi 操作超时，已取消；未切换到其他模型链路") from exc
            finally:
                self._active_analysis_image_count = 0

    def bind_flow_spec(self, spec: Any) -> None:
        """Bind the websocket's authoritative FlowSpec before a Pi turn."""
        self.flow_spec = spec.model_copy(deep=True)
        self._analysis_images = []
        self.last_submission_kind = ""
        self.last_submission_warning = ""
        # A review is evidence for one exact FlowSpec version. Any subsequent
        # bind invalidates it, including a user edit that happens to reuse the
        # same websocket and Pi conversation.
        self.last_review = {}


    def bind_analysis_images(self, images: list[dict] | None) -> None:
        """Bind validated screenshot evidence for the next Pi prompt."""
        normalized: list[dict[str, str]] = []
        for image in images or []:
            if not isinstance(image, dict):
                raise ValueError("Pi analysis image must be an object")
            data = str(image.get("data") or "")
            mime_type = str(image.get("mimeType") or "")
            if image.get("type") != "image" or not data or not mime_type.startswith("image/"):
                raise ValueError("Pi analysis image is invalid")
            normalized.append({"type": "image", "data": data, "mimeType": mime_type})
        self._analysis_images = normalized

    @property
    def analysis_image_count(self) -> int:
        return self._active_analysis_image_count or len(self._analysis_images)

    def current_flow_spec(self) -> Any:

        if self.flow_spec is None:
            raise RecordingPiError("录制 FlowSpec 尚未绑定到 Pi Session")
        return self.flow_spec.model_copy(deep=True)

    async def get_recording_state(self) -> dict[str, Any]:
        from dano.execution.page.flow_spec import recording_agent_state

        async with self._state_lock:
            return recording_agent_state(self.current_flow_spec())

    async def get_validation_report(self) -> dict[str, Any]:
        from dano.execution.page.flow_spec import recording_agent_validation

        async with self._state_lock:
            return recording_agent_validation(self.current_flow_spec())

    async def apply_submission(
        self,
        submission: dict[str, Any],
        *,
        mode: str,
        base_flow_version: int,
    ) -> dict[str, Any]:
        from dano.execution.page.flow_spec import (
            apply_recording_agent_submission,
            recording_agent_validation,
        )

        async with self._state_lock:
            current = self.current_flow_spec()
            actual_version = int((current.meta or {}).get("current_version") or 0)
            if int(base_flow_version) != actual_version:
                raise RecordingPiError(
                    f"录制版本冲突: base={base_flow_version}, current={actual_version}; 请重新读取状态"
                )
            updated = await apply_recording_agent_submission(
                current,
                submission=submission,
                mode=mode,
            )
            self.flow_spec = updated
            self.last_submission_kind = mode
            # A plan/repair changes the authoritative contract. Any review
            # submitted earlier in the same or a previous Pi turn is stale.
            self.last_review = {}
            if self._on_submission_accepted is not None:
                # The gateway checkpoint is part of accepting the tool result,
                # not a best-effort action after the Pi prompt response.
                self._on_submission_accepted(updated.model_copy(deep=True), mode)
            return recording_agent_validation(updated)

    async def accept_unchanged_plan(
        self,
        *,
        base_flow_version: int,
        warning: str,
    ) -> dict[str, Any]:
        """Finish a screenshot turn that has no safely grounded field edits."""
        from dano.execution.page.flow_spec import recording_agent_validation

        async with self._state_lock:
            current = self.current_flow_spec()
            actual_version = int((current.meta or {}).get("current_version") or 0)
            if int(base_flow_version) != actual_version:
                raise RecordingPiError(
                    f"录制版本冲突: base={base_flow_version}, current={actual_version}; 请重新读取状态"
                )
            self.last_submission_kind = "plan"
            self.last_submission_warning = warning
            self.last_review = {}
            return {
                **recording_agent_validation(current),
                "accepted": True,
                "unchanged": True,
                "warning": warning,
            }

    async def submit_review(self, review: dict[str, Any], *, base_flow_version: int) -> dict[str, Any]:
        async with self._state_lock:
            current = self.current_flow_spec()
            actual_version = int((current.meta or {}).get("current_version") or 0)
            if int(base_flow_version) != actual_version:
                raise RecordingPiError(
                    f"录制版本冲突: base={base_flow_version}, current={actual_version}; 请重新读取状态"
                )
            candidate = dict(review or {})
            if self.last_submission_kind == "review" and self.last_review:
                if candidate == self.last_review:
                    return {"accepted": True, "flow_version": actual_version, "replayed": True}
                raise RecordingPiError("当前 FlowSpec 版本的发布审核已提交，拒绝被后续结论覆盖")
            self.last_review = candidate
            self.last_submission_kind = "review"
            return {"accepted": True, "flow_version": actual_version, "replayed": False}

    def require_publish_review(
        self,
        *,
        flow_version: int,
        flow_fingerprint: str,
    ) -> dict[str, Any]:
        """Validate review evidence against the exact bound release contract."""
        from dano.execution.page.flow_spec import flow_spec_fingerprint

        if self.last_submission_kind != "review" or not self.last_review:
            raise RecordingPiError("Pi 未通过 submit_recording_review 提交发布审核")
        current = self.current_flow_spec()
        current_version = int((current.meta or {}).get("current_version") or 0)
        if current_version != int(flow_version):
            raise RecordingPiError("Pi 发布审核与当前 FlowSpec 版本不一致")
        if flow_spec_fingerprint(current) != flow_fingerprint:
            raise RecordingPiError("Pi 发布审核对应的 FlowSpec 内容已变化")
        review = dict(self.last_review)
        if int(review.get("base_flow_version") or -1) != current_version:
            raise RecordingPiError("Pi 发布审核已过期")
        verdicts = list(review.get("verdicts") or [])
        expected_roles = {"acceptance", "security", "compliance"}
        roles = [str(item.get("role") or "") for item in verdicts if isinstance(item, dict)]
        if len(verdicts) != 3 or len(roles) != 3 or set(roles) != expected_roles:
            raise RecordingPiError("Pi 发布审核缺少 acceptance/security/compliance 三角色结论")
        if any(not isinstance(item.get("passed"), bool) for item in verdicts):
            raise RecordingPiError("Pi 发布审核包含无效的 passed 结论")
        blocking_reasons = review.get("blocking_reasons") or []
        if (
            not isinstance(blocking_reasons, list)
            or any(not isinstance(reason, str) for reason in blocking_reasons)
        ):
            raise RecordingPiError("Pi 发布审核包含无效的 blocking_reasons")
        if blocking_reasons:
            raise RecordingPiError("Pi 发布审核仍有阻断项: " + "; ".join(blocking_reasons))
        all_passed = all(bool(item["passed"]) for item in verdicts)
        if review.get("all_passed") is not all_passed:
            raise RecordingPiError("Pi 发布审核汇总结论与角色结论不一致")
        if not all_passed:
            reasons = [
                reason
                for item in verdicts if not item["passed"]
                for reason in (item.get("reasons") or [])
            ]
            raise RecordingPiError("Pi 发布审核未通过: " + "; ".join(map(str, reasons or ["未知原因"])))
        return review

    @property
    def descriptor(self) -> dict[str, str | bool | None]:
        """Public opaque resume data; never expose server filesystem paths/tokens."""
        return {
            "recording_id": self.recording_id,
            "session_id": self.session_id,
            "resumed": self.resumed,
        }

    async def _command(self, command_type: str, *, timeout_s: float | None = None, **payload: Any) -> dict[str, Any]:
        request_id = uuid4().hex
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._send({"type": command_type, "request_id": request_id, **payload})
            timeout = self.timeout_s if timeout_s is None else timeout_s
            return await future if timeout <= 0 else await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def _send(self, command: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            raise RecordingPiError("录制 Pi 进程不可用")
        proc.stdin.write((json.dumps(command, ensure_ascii=False) + "\n").encode())
        await proc.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            async for raw in self._proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("recording_pi.stdout_invalid", run_id=self.run_id, line=line[:300])
                    continue
                event_type = event.get("type")
                if (
                    event_type == "agent_event"
                    and (event.get("stop_reason") == "error" or event.get("error"))
                ):
                    log.error(
                        "recording_pi.agent_error",
                        run_id=self.run_id,
                        agent_event=str(event.get("event") or "unknown"),
                        error=str(event.get("error") or "provider returned an error")[:2000],
                    )
                request_id = str(event.get("request_id") or "")
                future = self._pending.get(request_id)
                if future is None or future.done():
                    continue
                if event_type in ("session_started", "prompt_completed", "session_closed"):
                    future.set_result(event)
                elif event_type == "agent_event" and event.get("event") == "cancelled":
                    future.set_result(event)
                elif event_type == "runtime_error":
                    future.set_exception(RecordingPiError(str(event.get("error") or "Pi runtime error")))
        finally:
            error = RecordingPiError("录制 Pi 进程已结束")
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(error)

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        async for raw in self._proc.stderr:
            line = raw.decode(errors="replace").rstrip()
            if line:
                log.info("recording_pi.stderr", run_id=self.run_id, line=line[:1000])

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _ACTIVE_RECORDING_SESSIONS.pop(self.run_id, None)
        try:
            proc = self._proc
            if proc is not None and proc.returncode is None:
                try:
                    await self._command("close", timeout_s=min(self.timeout_s, 10.0))
                except BaseException:  # noqa: BLE001 - cleanup must continue after a dead sidecar
                    pass
                if proc.stdin is not None:
                    proc.stdin.close()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            for task in (self._stdout_task, self._stderr_task):
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except BaseException:  # noqa: BLE001
                        pass
            self._proc = None
            if self._server is not None:
                self._server.should_exit = True
            if self._server_task is not None:
                try:
                    await self._server_task
                except BaseException:  # noqa: BLE001
                    pass
        finally:
            runs.unregister(self.run_id)
            materials.clear_run(self.run_id)
            _release_scope_file_lock(self._scope_file_lock)
            self._scope_file_lock = None
            if self._scope_reserved and _ACTIVE_RECORDING_SCOPES.get(self._scope) is self:
                _ACTIVE_RECORDING_SCOPES.pop(self._scope, None)
            self._scope_reserved = False
