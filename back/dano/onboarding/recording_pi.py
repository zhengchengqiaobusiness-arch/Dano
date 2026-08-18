"""Python owner for a recording-only Pi AgentSession sidecar.

The recording gateway talks to Pi exclusively through this JSONL bridge.  The
bridge can resume a persisted conversation after reconnect, while independent
planning/review operations may start with an empty model context.  FlowSpec
state remains authoritative in Python rather than in Pi conversation history.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import uuid4

import structlog

from dano.agent_tools import materials, runs
from dano.infra.run_logging import (
    bind_run_context,
    emit_run_event,
    emit_run_exception,
    new_span_id,
)

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
        # One live-analysis turn covers delta reads plus a full plan
        # submission; slow model chains legitimately exceed 3 minutes, and a
        # timed-out turn silently drops that batch's conclusions.
        timeout_s: float = 360.0,
        resume_history: bool = True,
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
        self._resume_history = resume_history
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
        self._write_verification_locks: dict[str, asyncio.Lock] = {}
        self._closed = False
        self.flow_spec: Any = None
        self._live_recorder: Any = None
        self._live_evidence_marker: tuple[Any, ...] | None = None
        self._live_delta_floor = 0
        self._live_goal_text = ""
        self._operator_asker: Callable[..., Any] | None = None
        self.last_submission_kind = ""
        self.last_submission_warning = ""
        # Review provenance is server-owned.  The model may decide pass/fail,
        # but cannot claim a different identity in its tool payload.
        from dano.config import get_settings

        self.model_id = str(
            os.environ.get("DANO_PI_MODEL")
            or get_settings().pi_model
            or "deepseek-ai/DeepSeek-V3.2"
        )
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
            if self._resume_history:
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
            bind_run_context(
                run_id=self.run_id,
                recording_id=self.recording_id,
                tenant=self.tenant,
                subsystem=self.subsystem,
            )
            emit_run_event(
                "recording.pi.started",
                stage="analysis",
                status="started",
                summary="录制分析进程已启动",
                session_id=self.session_id,
                details={"resumed": self.resumed, "session_id": self.session_id},
            )
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

    async def prompt(
        self,
        text: str,
        *,
        timeout_s: float | None = None,
        prompt_mode: str = "workflow",
        analysis_phase: str = "",
    ) -> dict[str, Any]:
        """Append one turn to the same Pi session; no Python message history exists."""
        if not text.strip():
            raise ValueError("Pi prompt must not be empty")
        if self._proc is None:
            raise RecordingPiError("录制 Pi Session 尚未启动")
        async with self._prompt_lock:
            span_id = new_span_id("analysis")
            started = time.monotonic()
            if prompt_mode == "recording_analysis":
                emit_run_event(
                    "recording.analysis.started",
                    stage="analysis",
                    status="started",
                    summary="开始录制分析",
                    span_id=span_id,
                    details={"analysis_phase": analysis_phase, "prompt_mode": prompt_mode},
                )
            try:
                event = await self._command(
                    "prompt", timeout_s=timeout_s, text=text,
                    images=[],
                    prompt_mode=prompt_mode,
                    analysis_phase=analysis_phase,
                )
                # A terminal tool submission has already been persisted by the
                # Python bridge. It is authoritative even if an older sidecar
                # also reports a late limiter/cancel status in the same event.
                if event.get("accepted_submission"):
                    event["status"] = "submitted"
                    event.pop("error", None)
                    if prompt_mode == "recording_analysis":
                        emit_run_event(
                            "recording.analysis.completed",
                            stage="analysis",
                            status="succeeded",
                            summary="录制分析完成",
                            span_id=span_id,
                            duration_ms=(time.monotonic() - started) * 1000,
                            details={
                                "analysis_phase": analysis_phase,
                                "accepted_submission": event.get("accepted_submission"),
                            },
                        )
                    return event
                if event.get("status") == "submission_limit":
                    error = RecordingPiError(
                        "录制 Pi 在同一任务中连续提交被拒，已停止本轮以避免无效 Token 消耗；"
                        "请基于最新状态重新发起操作"
                    )
                    if prompt_mode == "recording_analysis":
                        emit_run_exception(
                            "recording.analysis.failed",
                            error,
                            stage="analysis",
                            span_id=span_id,
                            duration_ms=(time.monotonic() - started) * 1000,
                            details={"analysis_phase": analysis_phase, "status": "submission_limit"},
                        )
                    raise error
                if (
                    prompt_mode == "recording_analysis"
                    and event.get("status") == "missing_submission"
                ):
                    error = RecordingPiError(
                        "录制 Pi 已重试但仍未提交完整能力计划；本轮分析结果未被采纳"
                    )
                    emit_run_exception(
                        "recording.analysis.failed",
                        error,
                        stage="analysis",
                        span_id=span_id,
                        duration_ms=(time.monotonic() - started) * 1000,
                        details={"analysis_phase": analysis_phase, "status": "missing_submission"},
                    )
                    raise error
                if prompt_mode == "recording_analysis":
                    emit_run_event(
                        "recording.analysis.completed",
                        stage="analysis",
                        status="succeeded",
                        summary="录制分析完成",
                        span_id=span_id,
                        duration_ms=(time.monotonic() - started) * 1000,
                        details={"analysis_phase": analysis_phase, "status": event.get("status")},
                    )
                return event
            except asyncio.TimeoutError as exc:
                try:
                    await self._command("cancel", timeout_s=min(self.timeout_s, 10.0))
                except BaseException as cancel_exc:  # noqa: BLE001
                    raise RecordingPiError(
                        "录制 Pi 操作超时且取消确认失败；会话不可继续使用"
                    ) from cancel_exc
                if prompt_mode == "recording_analysis":
                    emit_run_exception(
                        "recording.analysis.failed",
                        RecordingPiError("录制 Pi 操作超时"),
                        stage="analysis",
                        span_id=span_id,
                        details={"analysis_phase": analysis_phase},
                    )
                raise RecordingPiError("录制 Pi 操作超时，已取消；未切换到其他模型链路") from exc
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(
                        self._command("cancel", timeout_s=min(self.timeout_s, 10.0)),
                    )
                except BaseException:  # noqa: BLE001 - preserve the caller's cancellation
                    pass
                raise

    def bind_flow_spec(self, spec: Any) -> None:
        """Bind the websocket's authoritative FlowSpec before a Pi turn."""
        self.flow_spec = spec.model_copy(deep=True)
        self._live_evidence_marker = None
        self.last_submission_kind = ""
        self.last_submission_warning = ""

    def bind_live_recording(
        self,
        recorder: Any,
        *,
        goal_text: str = "",
        operator_asker: Callable[..., Any] | None = None,
    ) -> None:
        """Bind the live recorder and websocket-backed operator question channel."""
        self._live_recorder = recorder
        self._live_evidence_marker = None
        self._live_goal_text = str(goal_text or "")
        self._operator_asker = operator_asker

    def bind_submission_listener(
        self,
        listener: Callable[[Any, str], None] | None,
    ) -> None:
        """Publish accepted live-plan checkpoints without waiting for the Pi turn."""

        self._on_submission_accepted = listener

    async def get_recording_delta(self, since_seq: int = 0, *, limit: int = 25) -> dict[str, Any]:
        from dano.execution.page.recording_live import recording_delta

        if self._live_recorder is None:
            raise RecordingPiError("实时录制事实源尚未绑定")
        # A live turn is scoped to the batch that triggered it.  The model may
        # request an older cursor after context compaction, but replaying the
        # complete recording here duplicates thousands of facts and can make
        # the final tail lose the very requests it needs to consolidate.
        since_seq = max(int(since_seq), int(self._live_delta_floor))
        captured = list(self._live_recorder.captured_all_requests() or [])
        page_events = list(self._live_recorder.recorded_page_events() or [])
        delta = recording_delta(
            self._live_recorder,
            since_seq=since_seq,
            limit=limit,
            goal_text=self._live_goal_text,
            captured_requests=captured,
            page_events=page_events,
        )
        # Keep edit grounding aligned with the exact batch just returned.
        await self.refresh_live_evidence(
            captured_requests=captured,
            page_events=page_events,
        )
        return delta

    async def refresh_live_evidence(
        self,
        *,
        captured_requests: list[dict] | None = None,
        page_events: list[dict] | None = None,
    ) -> None:
        """Publish the recorder's latest DOM facts into the bound FlowSpec.

        The request delta and field-operation gates must read one evidence
        snapshot. Previously only request ids were copied into FlowSpec, so Pi
        could cite a real event returned by ``get_recording_delta`` while the
        edit gate still considered that event unknown.
        """
        recorder = self._live_recorder
        if recorder is None or self.flow_spec is None:
            return
        capture = getattr(recorder, "captured_all_requests", None)
        read_events = getattr(recorder, "recorded_page_events", None)
        read_fields = getattr(recorder, "recorded_field_evidence", None)
        read_enums = getattr(recorder, "recorded_page_enum_options", None)
        captured = (
            list(captured_requests)
            if captured_requests is not None
            else list(capture() or []) if callable(capture) else []
        )
        page_events = (
            list(page_events)
            if page_events is not None
            else list(read_events() or []) if callable(read_events) else []
        )
        raw_fields = list(read_fields() or []) if callable(read_fields) else []
        page_enums = dict(read_enums() or {}) if callable(read_enums) else {}
        last_request = captured[-1] if captured else {}
        last_event = page_events[-1] if page_events else {}
        semantic_digest = hashlib.sha256(json.dumps(
            {"fields": raw_fields, "enums": page_enums},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")).hexdigest()
        marker = (
            len(captured),
            str(last_request.get("request_id") or ""),
            last_request.get("sequence", last_request.get("index")),
            len(page_events),
            str(last_event.get("event_id") or last_event.get("action_id") or ""),
            semantic_digest,
        )
        if marker == self._live_evidence_marker:
            return

        from dano.execution.page.flow_spec import _option_sources_from_page_enum_options
        from dano.execution.page.recording_field_identity import bind_field_evidence

        # Binding a large DOM/request fact set is CPU-heavy. It must not share
        # the gateway event-loop thread with the recorder WebSocket heartbeat.
        bound_fields = await asyncio.to_thread(
            bind_field_evidence,
            captured,
            page_events,
            raw_fields,
            page_enum_options=page_enums,
        )
        option_sources = await asyncio.to_thread(
            _option_sources_from_page_enum_options,
            page_enums,
            captured,
        )
        async with self._state_lock:
            if marker == self._live_evidence_marker:
                return
            current = self.current_flow_spec()
            current.meta = {
                **(current.meta or {}),
                "live_request_ids": [
                    str(item.get("request_id") or "")
                    for item in captured
                    if isinstance(item, dict) and item.get("request_id")
                ][-500:],
            }
            current.request_facts.page_events = deepcopy(page_events)
            current.request_facts.field_evidence = deepcopy(bound_fields)
            retained_sources = [
                deepcopy(source)
                for source in (current.request_facts.option_sources or [])
                if not (
                    isinstance(source, dict)
                    and source.get("kind") == "page_enum_options"
                )
            ]
            retained_sources.extend(option_sources)
            current.request_facts.option_sources = retained_sources
            self.flow_spec = current
            self._live_evidence_marker = marker

    async def ask_operator(
        self,
        *,
        text: str,
        options: list[str] | None = None,
        context_ref: str = "",
    ) -> dict[str, Any]:
        if self._operator_asker is None:
            return {"answered": False, "reason": "operator_channel_unavailable"}
        result = self._operator_asker(text=text, options=options or [], context_ref=context_ref)
        if hasattr(result, "__await__"):
            result = await result
        return dict(result or {"answered": False})

    def _require_live_recorder(self):  # noqa: ANN202
        if self._live_recorder is None:
            raise RecordingPiError("实时录制浏览器尚未绑定")
        return self._live_recorder

    async def browser_navigate(self, url: str) -> dict[str, Any]:
        return await self._require_live_recorder().agent_navigate(url)

    async def browser_snapshot(self) -> dict[str, Any]:
        from dano.execution.page.verification_log import record_verification

        snapshot = await self._require_live_recorder().agent_snapshot()
        verification_id = record_verification(
            kind="enum_snapshot",
            subject={"url": str(snapshot.get("url") or "")},
            status="passed",
            evidence={"snapshot": snapshot},
        )
        await self.add_verifications([verification_id])
        return {**snapshot, "verification_id": verification_id}

    async def browser_act(self, kind: str, locator: dict, value: Any = None) -> dict[str, Any]:
        return await self._require_live_recorder().agent_act(kind, locator, value)

    async def notify_live_batch(self, delta: dict) -> dict[str, Any]:
        """Ask the same Pi session to consume one triggered live batch."""
        reason = str((delta or {}).get("reason") or "request_batch")
        since_seq = max(0, int((delta or {}).get("since_seq") or 0))
        analysis_phase = (
            "base_state_analysis"
            if reason == "recording_started"
            else "final_request_tail"
            if reason == "final_request_tail"
            else "request_batch"
        )
        goal_text = self._live_goal_text.strip() or "（未提供明确录制目标）"
        previous_floor = self._live_delta_floor
        self._live_delta_floor = since_seq
        try:
            return await self.prompt(
                "执行当前录制分析任务。"
                f"analysis_phase={analysis_phase}；触发原因={reason}；since_seq={since_seq}。"
                f"当前录制目标：{goal_text}。"
                "录制目标允许使用普通自然语言，禁止要求操作人改写为固定模板。"
                "若当前目标尚未结构化，先按项目 Skill 用 set_goal 将操作人明确要求的业务动作"
                "归一化为有序 capabilities；若目标要求保留后续实际操作，则只随已观察到的独立"
                "业务动作增补，不得根据页面加载流量扩张目标。"
                "必须按当前项目 Skill 读取这一阶段要求的完整事实；读取增量时若 has_more=true，"
                "继续用 next_seq 分页直到 has_more=false。必须调用 submit_recording_plan，"
                "并在 semantic_plan.capabilities 中提交截至当前仍成立的完整能力集合，"
                "不能只提交本批新增能力，也不能因单个操作失败清空已有能力。"
                "实时阶段不读取验证报告；只有事实真正无法推导时才可用业务语言询问操作人。",
                timeout_s=None,
                prompt_mode="recording_analysis",
                analysis_phase=analysis_phase,
            )
        finally:
            self._live_delta_floor = previous_floor

    def current_flow_spec(self) -> Any:

        if self.flow_spec is None:
            raise RecordingPiError("录制 FlowSpec 尚未绑定到 Pi Session")
        return self.flow_spec.model_copy(deep=True)

    async def get_recording_state(self) -> dict[str, Any]:
        from dano.execution.page.flow_spec import recording_agent_state

        await self.refresh_live_evidence()
        async with self._state_lock:
            current = self.current_flow_spec()
            return await asyncio.to_thread(recording_agent_state, current)

    async def get_validation_report(self) -> dict[str, Any]:
        from dano.execution.page.flow_spec import recording_agent_validation

        await self.refresh_live_evidence()
        async with self._state_lock:
            current = self.current_flow_spec()
            return await asyncio.to_thread(recording_agent_validation, current)

    async def add_verifications(self, verification_ids: list[str]) -> list[dict[str, Any]]:
        """Attach executor-owned evidence to the bound FlowSpec without changing its edit version."""
        from dano.execution.page.verification_log import get_verification

        async with self._state_lock:
            current = self.current_flow_spec()
            current.meta = dict(current.meta or {})
            log = [dict(item) for item in current.meta.get("verification_log") or [] if isinstance(item, dict)]
            known = {str(item.get("verification_id") or "") for item in log}
            added: list[dict[str, Any]] = []
            for verification_id in verification_ids:
                record = get_verification(verification_id)
                if record is not None and verification_id not in known:
                    log.append(record)
                    known.add(verification_id)
                    added.append(record)
            current.meta["verification_log"] = log
            self.flow_spec = current
            return added

    def write_verification_lock(self, step_id: str) -> asyncio.Lock:
        """Serialize the one allowed real write verification for a step."""
        return self._write_verification_locks.setdefault(str(step_id), asyncio.Lock())

    async def claim_write_verification(self, step_id: str) -> dict[str, Any] | None:
        """Persist a write-attempt reservation before touching the business API."""
        async with self._state_lock:
            current = self.current_flow_spec()
            current.meta = dict(current.meta or {})
            attempts = {
                str(key): dict(value)
                for key, value in (current.meta.get("write_verification_attempts") or {}).items()
                if isinstance(value, dict)
            }
            existing = attempts.get(str(step_id))
            if existing is not None:
                # A deterministic business rejection did not mutate the
                # target system, so corrected inputs may consume the still
                # unused successful-write opportunity. Unknown failures and
                # writes that reached read-back remain one-shot.
                if existing.get("status") == "failed_before_write":
                    attempts[str(step_id)] = {"status": "running"}
                    current.meta["write_verification_attempts"] = attempts
                    self.flow_spec = current
                    return None
                return existing
            attempts[str(step_id)] = {"status": "running"}
            current.meta["write_verification_attempts"] = attempts
            self.flow_spec = current
            return None

    async def finish_write_verification(
        self,
        step_id: str,
        *,
        status: str,
        verification_id: str = "",
    ) -> None:
        async with self._state_lock:
            current = self.current_flow_spec()
            current.meta = dict(current.meta or {})
            attempts = dict(current.meta.get("write_verification_attempts") or {})
            attempts[str(step_id)] = {
                "status": str(status),
                **({"verification_id": str(verification_id)} if verification_id else {}),
            }
            current.meta["write_verification_attempts"] = attempts
            self.flow_spec = current

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
            if self._live_recorder is not None:
                captured = getattr(self._live_recorder, "captured_all_requests", None)
                if callable(captured):
                    current.meta = {
                        **(current.meta or {}),
                        "live_request_ids": [
                            str(item.get("request_id") or "")
                            for item in captured()
                            if isinstance(item, dict) and item.get("request_id")
                        ][-500:],
                    }
            actual_version = int((current.meta or {}).get("current_version") or 0)
            if int(base_flow_version) != actual_version:
                raise RecordingPiError(
                    f"录制版本冲突: base={base_flow_version}, current={actual_version}; 请重新读取状态"
                )
            semantic_plan = (
                submission.get("semantic_plan")
                if isinstance(submission.get("semantic_plan"), dict)
                else submission.get("plan") if isinstance(submission.get("plan"), dict)
                else {}
            )
            submitted_capabilities = (
                semantic_plan.get("capabilities")
                if isinstance(semantic_plan.get("capabilities"), list)
                else []
            )
            updated = await asyncio.to_thread(
                asyncio.run,
                apply_recording_agent_submission(
                    current,
                    submission=submission,
                    mode=mode,
                ),
            )
            self.flow_spec = updated
            if self._on_submission_accepted is not None:
                # The gateway checkpoint is part of accepting the tool result,
                # not a best-effort action after the Pi prompt response.
                self._on_submission_accepted(updated.model_copy(deep=True), mode)
            validation = recording_agent_validation(updated)
            # A plan can contain independently guarded field hypotheses.  A
            # rejected optional hypothesis stays visible in must_retry, but it
            # must not erase a grounded capability plan that compiled safely.
            # Repairs remain strict because their sole purpose is to resolve
            # those outstanding findings.
            submission_complete = bool(validation.get("submission_complete", True))
            if mode == "plan" and (
                submitted_capabilities or validation.get("capability_plan_complete")
            ):
                submission_complete = bool(validation.get("capability_plan_complete"))
            if mode == "repair":
                submission_complete = bool(validation.get("all_applied", True))
            validation["submission_complete"] = submission_complete
            # A partial repair is protocol-valid: preserve applied operations
            # so the outer verify/repair loop can retry only rejected ones.
            self.last_submission_kind = (
                mode if mode == "repair" or submission_complete else ""
            )
            return validation

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
            return {
                **recording_agent_validation(current),
                "accepted": True,
                "unchanged": True,
                "warning": warning,
            }

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
                event_error = str(event.get("error") or "")
                if (
                    event_type == "agent_event"
                    and (event.get("stop_reason") == "error" or event.get("error"))
                    and event_error.strip().casefold() != "request aborted"
                ):
                    log.error(
                        "recording_pi.agent_error",
                        run_id=self.run_id,
                        agent_event=str(event.get("event") or "unknown"),
                        error=(event_error or "provider returned an error")[:2000],
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
                self._record_stderr_line(line)

    def _record_stderr_line(self, line: str) -> None:
        payload = None
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("type") == "recording_log":
            event = str(payload.get("event") or "recording.pi.message")
            skill_name = payload.get("skill_name") or ""
            summary = (
                "识别 Skill 已加载"
                if event == "recording.skill.loaded"
                else "识别 Skill 已应用"
                if event == "recording.skill.applied"
                else str(payload.get("summary") or payload.get("message") or event)
            )
            emit_run_event(
                event,
                stage=str(payload.get("stage") or "analysis"),
                status=str(payload.get("status") or "progress"),
                summary=summary,
                details={
                    "skill_name": skill_name,
                    "analysis_phase": payload.get("analysis_phase"),
                    "skill_sha256": payload.get("skill_sha256"),
                    "session_id": payload.get("session_id"),
                    "prompt": payload.get("prompt"),
                    "turn": payload.get("turn"),
                    "batch": payload.get("batch"),
                    "message": payload.get("message"),
                },
            )
            return
        text = line[:1000]
        noisy = bool(re.search(r"error|fatal|exception", text, re.I))
        emit_run_event(
            "recording.pi.stderr",
            stage="analysis",
            status="warning" if noisy else "progress",
            level="warning" if noisy else "debug",
            visibility="console" if noisy else "detail",
            summary="录制分析进程未知输出",
            details={"line": text},
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        emit_run_event(
            "recording.pi.closed",
            stage="analysis",
            status="succeeded",
            summary="录制分析进程已关闭",
            run_id=self.run_id,
        )
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
