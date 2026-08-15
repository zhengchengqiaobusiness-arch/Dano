"""Transport-independent owner for one page-recording browser session."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dano.execution.page.flow_spec import (
    FlowSpec,
    apply_client_flow_patch,
    ensure_flow_version,
    flow_spec_fingerprint,
    flow_spec_to_client,
    to_flow_spec,
    validate_flow_spec,
)
from dano.execution.page.recorder import RecordSession
from dano.execution.page.recording_field_identity import bind_field_evidence
from dano.execution.page.recording_live import LiveNotebook
from dano.execution.page.sessions import save_session
from dano.onboarding.recording_pipeline import CanonicalRecordingRuntime
from dano.onboarding.recording_runtime import ProductionRecordingServices, Publisher
from dano.onboarding.recording_workflow import (
    PipelineContext,
    RecordingWorkflow,
    SelfHealingPipeline,
    WorkflowQuestion,
    WorkflowSnapshot,
    WorkflowStatus,
)


SendMessage = Callable[[dict[str, Any]], Awaitable[None]]
PiFactory = Callable[[bool], Awaitable[Any]]


def _workflow_snapshot_path(action: str) -> Path:
    return Path(__file__).resolve().parents[2] / ".dano-recording-workflows" / f"{action}.json"


def _project_page_enums(recorded: dict[str, Any], samples: dict[str, Any]) -> dict[str, Any]:
    """Preserve browser enum evidence without business-specific assumptions."""
    projected: dict[str, Any] = {}
    for storage_key, raw_entry in (recorded or {}).items():
        options = raw_entry.get("options") if isinstance(raw_entry, dict) else raw_entry
        if not options:
            continue
        raw = raw_entry if isinstance(raw_entry, dict) else {}
        field_key = str(raw.get("field_key") or storage_key)
        selected = str(raw.get("selected") or samples.get(field_key, "") or "").strip()
        entry = {
            "options": list(options),
            "field_key": field_key,
            "field_aliases": list(raw.get("field_aliases") or []),
            "selected": selected,
            "selected_label": str(raw.get("selected_label") or selected),
            "selected_value": raw.get("selected_value"),
            "page_id": str(raw.get("page_id") or ""),
            "frame_id": str(raw.get("frame_id") or ""),
            "page_context": dict(raw.get("page_context") or {}),
            "control_kind": str(raw.get("control_kind") or "select"),
            "enum_source": str(raw.get("enum_source") or "dom"),
            "script_url": str(raw.get("script_url") or ""),
            "source_url": str(raw.get("source_url") or ""),
            "dict_type": str(raw.get("dict_type") or ""),
            "mapping_complete": bool(raw.get("mapping_complete")),
            "mapping_conflict": bool(raw.get("mapping_conflict")),
            "truncated": bool(raw.get("truncated") or raw.get("snapshot_truncated")),
            "snapshot_truncated": bool(
                raw.get("snapshot_truncated") or raw.get("truncated")
            ),
            "action_id": str(raw.get("action_id") or raw.get("trigger_action_id") or ""),
            "transaction_id": str(
                raw.get("transaction_id") or raw.get("trigger_transaction_id") or ""
            ),
            "observed_at": raw.get("observed_at"),
        }
        existing = projected.get(str(storage_key))
        if isinstance(existing, dict):
            by_label = {
                str(option.get("label") if isinstance(option, dict) else option): option
                for option in [*(existing.get("options") or []), *entry["options"]]
                if str(option.get("label") if isinstance(option, dict) else option)
            }
            entry["options"] = list(by_label.values())
            entry["field_aliases"] = list(dict.fromkeys([
                *list(existing.get("field_aliases") or []),
                *entry["field_aliases"],
            ]))
            entry["selected"] = selected or str(existing.get("selected") or "")
        projected[str(storage_key)] = entry
    return projected


@dataclass(frozen=True)
class RecordingSessionConfig:
    tenant: str
    subsystem: str
    recording_id: str
    action: str
    start_url: str
    goal_text: str = ""
    base_url: str = ""
    token: str = ""
    storage_state: dict[str, Any] | None = None


@dataclass
class RecordingGatewaySession:
    """Own capture, live notebook and the one authoritative workflow task."""

    config: RecordingSessionConfig
    send: SendMessage | None
    pi_factory: PiFactory
    publisher: Publisher
    capture: RecordSession | None = field(default=None, init=False)
    workflow: RecordingWorkflow | None = field(default=None, init=False)
    _pi: Any = field(default=None, init=False, repr=False)
    _live_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _live_pending_reason: str = field(default="", init=False)
    _last_live_count: int = field(default=0, init=False)
    _live_notebook: LiveNotebook | None = field(default=None, init=False, repr=False)
    _capture_frozen: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)

    async def attach(self, send: SendMessage) -> None:
        """Attach one transport without taking ownership of the backend task."""

        self.send = send
        await self._emit_snapshot()

    def detach(self, send: SendMessage) -> None:
        if self.send is send:
            self.send = None

    async def start(self) -> None:
        if self.capture is not None:
            await self._emit_snapshot()
            return
        self.capture = RecordSession(
            on_request=self._on_request,
            on_capture_count=self._on_capture_count,
            intercept_submit=False,
            capture_reads=True,
            tenant=self.config.tenant,
        )
        services = ProductionRecordingServices(
            recording_id=self.config.recording_id,
            materializer=self._materialize,
            pi_provider=self._ensure_pi,
            publisher=self.publisher,
        )
        runtime = CanonicalRecordingRuntime(services.pipeline_services())
        self.workflow = RecordingWorkflow(
            WorkflowSnapshot(
                run_id=self.config.recording_id,
                action=self.config.action,
            ),
            SelfHealingPipeline(runtime),
            listener=self._on_snapshot,
            cancel_listener=self._cancel_analysis,
            snapshot_path=_workflow_snapshot_path(self.config.action),
        )
        await self.capture.start(
            self.config.start_url,
            base_url=self.config.base_url,
            storage_state=self.config.storage_state,
            token=self.config.token or None,
        )
        await self.workflow.start()
        await self.capture.start_screencast(self._on_frame)
        self._schedule_live("recording_started")

    async def dispatch(self, message: dict[str, Any]) -> None:
        if self.workflow is None or self.capture is None:
            raise RuntimeError("recording session has not started")
        command = str(message.get("type") or "")
        if command == "input":
            result = await self.capture.dispatch_input(message.get("event") or {})
            if isinstance(result, dict) and not result.get("ok", True):
                await self._send({
                    "type": "input_error",
                    "detail": str(result.get("error") or "浏览器输入事件执行失败"),
                    "recoverable": bool(result.get("recoverable", True)),
                })
            return
        if command == "finish":
            await self.workflow.set_title(str(message.get("title") or ""))
            await self.workflow.finish(
                machine_verification=message.get("machine_verification") is True,
            )
            return
        if command == "republish":
            await self.workflow.set_title(str(message.get("title") or self.workflow.snapshot.title))
            await self.workflow.republish(
                machine_verification=message.get("machine_verification") is True,
            )
            return
        if command == "patch_draft":
            if self.workflow.snapshot.draft is None:
                raise ValueError("没有可修改的能力草稿")
            spec = FlowSpec.model_validate(self.workflow.snapshot.draft)
            updated = apply_client_flow_patch(
                spec,
                list(message.get("edits") or []),
                expected_fingerprint=str(message.get("expected_fingerprint") or ""),
            )
            await self.workflow.patch_draft(
                updated.model_dump(mode="json"),
                expected_revision=int(message.get("expected_revision") or -1),
            )
            return
        if command == "answer":
            await self.workflow.answer(
                str(message.get("question_id") or ""),
                str(message.get("answer") or ""),
            )
            return
        if command == "cancel":
            await self.workflow.cancel()
            return
        if command == "ping":
            await self._emit_snapshot()
            return
        raise ValueError(f"unsupported recording command: {command}")

    async def close(self) -> None:
        """Destroy the backend-owned run during application shutdown only."""

        if self._closed:
            return
        self._closed = True
        if self._live_task is not None and not self._live_task.done():
            self._live_task.cancel()
            await asyncio.gather(self._live_task, return_exceptions=True)
        if self.workflow is not None and self.workflow.snapshot.status not in {
            WorkflowStatus.IDLE,
            WorkflowStatus.PUBLISHED,
            WorkflowStatus.EDITABLE,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.FAILED,
        }:
            await self.workflow.cancel()
        if self._pi is not None:
            await self._pi.close()
            self._pi = None
        if self.capture is not None:
            await self.capture.stop()
            self.capture = None

    async def _cancel_analysis(self) -> None:
        self._live_pending_reason = ""
        if self._live_task is not None and not self._live_task.done():
            self._live_task.cancel()
            await asyncio.gather(self._live_task, return_exceptions=True)
        if self._pi is not None:
            await self._pi.close()
            self._pi = None

    async def _materialize(
        self,
        use_live_notebook: bool,
        context: PipelineContext,
    ) -> FlowSpec:
        context.ensure_active()
        if self.capture is None:
            raise RuntimeError("recording capture is unavailable")
        await self._freeze_capture()
        required_labels = self.capture.recorded_required_labels()
        required_labels.update(await self.capture.observed_required_labels())
        page_context = await self.capture.observed_page_context()
        _steps, samples = self.capture.recorded_steps()
        for key, value in self.capture.recorded_form_samples().items():
            samples.setdefault(key, value)
        all_requests = self.capture.captured_all_requests()
        if not all_requests:
            raise ValueError("未捕获到业务接口请求，请在页面完成目标操作后重试")
        reads = self.capture.captured_reads()
        page_events = self.capture.recorded_page_events()
        page_enums = _project_page_enums(
            self.capture.recorded_page_enum_options(),
            samples,
        )
        field_evidence = bind_field_evidence(
            all_requests,
            page_events,
            self.capture.recorded_field_evidence(),
            page_enum_options=page_enums,
        )
        storage = await self.capture.storage_state()
        save_session(self.config.tenant, self.config.subsystem, storage)
        spec = to_flow_spec(
            captured_requests=all_requests,
            reads=reads,
            samples=samples,
            storage_state=storage,
            required_labels=required_labels,
            page_enum_options=page_enums,
            field_evidence=field_evidence,
            page_context=page_context,
            recording_mode="real_submit",
            diagnostics=self.capture.captured_diagnostics(),
            page_events=page_events,
            tenant=self.config.tenant,
            subsystem=self.config.subsystem,
        )
        if use_live_notebook and self._live_notebook is not None:
            spec = self._live_notebook.apply_to(spec)
        return spec

    async def _freeze_capture(self) -> None:
        if self._capture_frozen or self.capture is None:
            return
        await self.capture.flush_recording()
        self.capture.pause_recording()
        if self._live_pending_reason and (
            self._live_task is None or self._live_task.done()
        ):
            self._live_task = asyncio.create_task(self._drain_live())
        if self._live_task is not None and not self._live_task.done():
            # Pause new browser facts first, then drain every already queued
            # real-time batch.  Direct export is allowed to use those live
            # conclusions, but must never start a separate final Pi plan.
            await asyncio.gather(self._live_task, return_exceptions=True)
        self._capture_frozen = True
        self._capture_live_notebook()
        if self._pi is not None:
            self._pi.bind_live_recording(
                self.capture,
                goal_text=self.config.goal_text,
                operator_asker=self._ask_operator,
            )

    async def _ensure_pi(self, fresh: bool) -> Any:
        if fresh and self._pi is not None:
            await self._pi.close()
            self._pi = None
        if self._pi is None:
            self._pi = await self.pi_factory(fresh)
            self._pi.bind_live_recording(
                self.capture,
                goal_text=self.config.goal_text,
                operator_asker=(
                    self._ask_operator if self._capture_frozen else self._record_live_question
                ),
            )
            bind_submission_listener = getattr(self._pi, "bind_submission_listener", None)
            if callable(bind_submission_listener):
                bind_submission_listener(self._on_live_submission_accepted)
            if self.workflow is not None and self.workflow.snapshot.draft is not None:
                self._pi.bind_flow_spec(FlowSpec.model_validate(self.workflow.snapshot.draft))
            elif self._pi.flow_spec is None:
                self._pi.bind_flow_spec(ensure_flow_version(
                    FlowSpec(
                        tenant=self.config.tenant,
                        subsystem=self.config.subsystem,
                        meta={"recording_goal_text": self.config.goal_text},
                    ),
                    "recording_started",
                    reason="实时录制会话开始",
                ))
        return self._pi

    def _on_live_submission_accepted(self, spec: FlowSpec, mode: str) -> None:
        """Expose accepted live conclusions immediately, before the Pi turn completes."""

        if mode != "plan" or self._capture_frozen or self._closed:
            return
        notebook = LiveNotebook.from_shadow(spec)
        self._live_notebook = notebook
        if self.workflow is None:
            return
        task = asyncio.create_task(self.workflow.update_live_insights(notebook.insights))
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)

    async def _record_live_question(
        self,
        *,
        text: str,
        options: list[str],
        context_ref: str = "",
    ) -> dict[str, Any]:
        """Persist a live hypothesis without entering the final operator state."""

        if self._pi is None or self._pi.flow_spec is None:
            return {"answered": False, "reason": "deferred_until_final_analysis"}
        spec = self._pi.current_flow_spec()
        meta = dict(spec.meta or {})
        question_id = "live-question:" + hashlib.sha256(
            json.dumps(
                {"text": text, "options": options, "context_ref": context_ref},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:20]
        questions = [
            dict(item)
            for item in meta.get("live_pending_questions") or []
            if isinstance(item, dict) and str(item.get("question_id") or "") != question_id
        ]
        questions.append({
            "question_id": question_id,
            "text": str(text),
            "options": [str(value) for value in options],
            "context_ref": str(context_ref or ""),
        })
        insights = [
            dict(item) for item in meta.get("agent_insights") or [] if isinstance(item, dict)
        ]
        insights.append({
            "kind": "pending_question",
            "text": f"待最终分析复核：{text}",
            "refs": [str(context_ref)] if context_ref else [],
        })
        spec.meta = {
            **meta,
            "live_pending_questions": questions[-50:],
            "agent_insights": insights[-100:],
        }
        self._pi.bind_flow_spec(spec)
        self._capture_live_notebook()
        return {"answered": False, "reason": "deferred_until_final_analysis"}

    async def _ask_operator(
        self,
        *,
        text: str,
        options: list[str],
        context_ref: str = "",
    ) -> dict[str, Any]:
        if self.workflow is None:
            raise RuntimeError("recording workflow is unavailable")
        issue_id = str(context_ref or "").strip() or (
            "operator_"
            + hashlib.sha256(
                json.dumps(
                    {"text": text, "options": options},
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:20]
        )
        question = WorkflowQuestion(
            question_id=f"question:{issue_id}",
            issue_id=issue_id,
            text=str(text),
            options=[str(value) for value in options],
            context_ref=str(context_ref or ""),
        )
        answer = await self.workflow.ask_operator_question(question)
        return {
            "answered": True,
            "answer": answer,
            "question_id": question.question_id,
            "issue_id": issue_id,
        }

    def _on_request(self, request: dict[str, Any]) -> None:
        try:
            from dano.execution.page.request_capture import classify_request_role

            if classify_request_role(request).get("semanticRole") == "workflow_submit":
                self._schedule_live("submit_candidate")
        except Exception:  # noqa: BLE001 - capture must not fail on advisory analysis
            return

    def _on_capture_count(self, count: int) -> None:
        if self.workflow is not None and (count == 1 or count % 5 == 0):
            asyncio.create_task(self.workflow.update_recording(request_count=count))
        if count - self._last_live_count >= 15:
            self._schedule_live("request_batch")

    async def _on_frame(self, frame: dict[str, Any]) -> None:
        await self._send({"type": "frame", **frame})

    def _schedule_live(self, reason: str) -> None:
        if self._capture_frozen or self._closed:
            return
        self._live_pending_reason = self._live_pending_reason or reason
        if self._live_task is None or self._live_task.done():
            self._live_task = asyncio.create_task(self._drain_live())

    async def _drain_live(self) -> None:
        while self._live_pending_reason and not self._capture_frozen:
            reason = self._live_pending_reason
            self._live_pending_reason = ""
            try:
                pi = await self._ensure_pi(False)
                await pi.notify_live_batch({
                    "reason": reason,
                    "since_seq": self._last_live_count,
                })
                if self.capture is None:
                    return
                self._capture_live_notebook()
                self._last_live_count = len(self.capture.captured_all_requests())
                if self.workflow is not None:
                    insights = self._live_notebook.insights if self._live_notebook else []
                    await self.workflow.update_recording(
                        request_count=self._last_live_count,
                        insights=insights[-100:],
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep capture responsive
                self._capture_live_notebook()
                if self.workflow is not None:
                    insights = self._live_notebook.insights if self._live_notebook else []
                    insights.append({
                        "kind": "analysis_error",
                        "text": f"本轮实时分析未完成，后续批次将继续：{exc}",
                        "refs": [],
                    })
                    request_count = (
                        len(self.capture.captured_all_requests())
                        if self.capture is not None
                        else self._last_live_count
                    )
                    await self.workflow.update_recording(
                        request_count=request_count,
                        insights=insights[-100:],
                    )
                return

    def _capture_live_notebook(self) -> None:
        if self._pi is None or self._pi.flow_spec is None:
            return
        self._live_notebook = LiveNotebook.from_shadow(self._pi.current_flow_spec())

    async def _on_snapshot(self, snapshot: WorkflowSnapshot) -> None:
        await self._emit_snapshot(snapshot)

    async def _emit_snapshot(self, snapshot: WorkflowSnapshot | None = None) -> None:
        if self.workflow is None:
            return
        current = snapshot or self.workflow.snapshot
        payload = current.model_dump(mode="json")
        if current.draft is not None:
            spec = FlowSpec.model_validate(current.draft)
            payload["draft"] = flow_spec_to_client(spec)
            payload["draft_fingerprint"] = flow_spec_fingerprint(spec)
            payload["check_report"] = validate_flow_spec(spec)
        await self._send({"type": "snapshot", "snapshot": payload})
        if current.question is not None:
            await self._send({
                "type": "question",
                "question": current.question.model_dump(mode="json"),
                "revision": current.revision,
            })

    async def _send(self, payload: dict[str, Any]) -> None:
        sender = self.send
        if sender is None:
            return
        try:
            await sender(payload)
        except Exception:  # noqa: BLE001 - transport loss must not stop the workflow
            if self.send is sender:
                self.send = None

    def _send_background(self, payload: dict[str, Any]) -> None:
        task = asyncio.create_task(self._send(payload))
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)


@dataclass
class RecordingSessionRegistry:
    """Keep one backend-owned recording session per action across socket reconnects."""

    _sessions: dict[str, RecordingGatewaySession] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def attach_or_create(
        self,
        *,
        config: RecordingSessionConfig,
        send: SendMessage,
        pi_factory: PiFactory,
        publisher: Publisher,
    ) -> tuple[RecordingGatewaySession, bool]:
        async with self._lock:
            session = self._sessions.get(config.action)
            created = session is None
            if session is None:
                session = RecordingGatewaySession(
                    config=config,
                    send=send,
                    pi_factory=pi_factory,
                    publisher=publisher,
                )
                self._sessions[config.action] = session
            elif (
                session.config.tenant != config.tenant
                or session.config.subsystem != config.subsystem
            ):
                raise ValueError("录制 action 不属于当前租户或业务系统")
        if created:
            try:
                await session.start()
            except Exception:
                async with self._lock:
                    self._sessions.pop(config.action, None)
                await session.close()
                raise
        else:
            await session.attach(send)
        return session, created

    def detach(self, action: str, send: SendMessage) -> None:
        session = self._sessions.get(action)
        if session is not None:
            session.detach(send)

    async def close(self) -> None:
        async with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)
