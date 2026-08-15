"""Single authoritative state model for the recording workflow.

The gateway and the browser UI observe this module through ``WorkflowSnapshot``.
Implementation details such as Pi prompts, verification rounds and publishing are
represented by ``progress.step`` rather than separate externally visible states.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


class WorkflowStatus(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    WAITING_OPERATOR = "waiting_operator"
    EDITABLE = "editable"
    PUBLISHED = "published"
    CANCELLED = "cancelled"
    FAILED = "failed"


class WorkflowStep(StrEnum):
    READY = "ready"
    CAPTURING = "capturing"
    FREEZING = "freezing"
    MATERIALIZING = "materializing"
    ANALYZING = "analyzing"
    RESOLVING = "resolving"
    COMPILING = "compiling"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    PUBLISHING = "publishing"
    EXPORTING = "exporting"
    COMPLETE = "complete"


class WorkflowProgress(BaseModel):
    step: WorkflowStep = WorkflowStep.READY
    label: str = ""
    round: int = Field(default=0, ge=0)
    request_count: int = Field(default=0, ge=0)


class WorkflowActivity(BaseModel):
    sequence: int = Field(default=0, ge=0)
    step: WorkflowStep
    round: int = Field(default=0, ge=0)
    status: str
    label: str
    issue_id: str = ""
    code: str = ""
    target: dict[str, str] = Field(default_factory=dict)


class WorkflowIssue(BaseModel):
    issue_id: str
    code: str
    message: str
    severity: str = "blocking"
    resolver: str = "external_blocked"
    target: dict[str, str] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    allowed_operations: list[str] = Field(default_factory=list)


class WorkflowQuestion(BaseModel):
    question_id: str
    issue_id: str
    text: str
    options: list[str] = Field(default_factory=list)
    context_ref: str = ""


class WorkflowSnapshot(BaseModel):
    run_id: str
    action: str
    title: str = ""
    revision: int = Field(default=0, ge=0)
    status: WorkflowStatus = WorkflowStatus.IDLE
    progress: WorkflowProgress = Field(default_factory=WorkflowProgress)
    capture_frozen: bool = False
    draft: dict[str, Any] | None = None
    issues: list[WorkflowIssue] = Field(default_factory=list)
    insights: list[dict[str, Any]] = Field(default_factory=list)
    activity: list[WorkflowActivity] = Field(default_factory=list)
    question: WorkflowQuestion | None = None
    release: dict[str, Any] | None = None
    error: str = ""


CANONICAL_RECORDING_COMMANDS = frozenset({
    "start", "input", "finish", "patch_draft", "republish", "answer", "cancel", "ping",
})


_ALLOWED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.IDLE: frozenset({WorkflowStatus.RECORDING, WorkflowStatus.FAILED}),
    WorkflowStatus.RECORDING: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.PROCESSING: frozenset({
        WorkflowStatus.WAITING_OPERATOR, WorkflowStatus.EDITABLE,
        WorkflowStatus.PUBLISHED, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.WAITING_OPERATOR: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.EDITABLE: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.PUBLISHED: frozenset({
        WorkflowStatus.EDITABLE, WorkflowStatus.PROCESSING, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.CANCELLED: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.FAILED: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.CANCELLED,
    }),
}


def transition_snapshot(
    snapshot: WorkflowSnapshot,
    status: WorkflowStatus,
    *,
    progress: WorkflowProgress | None = None,
    **changes: Any,
) -> WorkflowSnapshot:
    """Return the next authoritative snapshot or reject an impossible transition."""

    if status != snapshot.status and status not in _ALLOWED_TRANSITIONS[snapshot.status]:
        raise ValueError(f"invalid recording workflow transition: {snapshot.status} -> {status}")
    payload = {
        **snapshot.model_dump(mode="python"),
        **changes,
        "status": status,
        "revision": snapshot.revision + 1,
    }
    if progress is not None:
        payload["progress"] = progress
    if status == WorkflowStatus.WAITING_OPERATOR and not payload.get("question"):
        raise ValueError("waiting_operator requires a question")
    if status == WorkflowStatus.PUBLISHED and not payload.get("release"):
        raise ValueError("published requires a release")
    if status in {
        WorkflowStatus.PROCESSING,
        WorkflowStatus.WAITING_OPERATOR,
        WorkflowStatus.EDITABLE,
        WorkflowStatus.PUBLISHED,
    }:
        payload["capture_frozen"] = True
    if status != WorkflowStatus.WAITING_OPERATOR:
        payload["question"] = None
    return WorkflowSnapshot.model_validate(payload)


class WorkflowCancelled(Exception):
    """Internal cooperative cancellation signal."""


@dataclass(frozen=True)
class PipelineSeed:
    kind: str
    draft: dict[str, Any] | None = None
    use_live_notebook: bool = False


@dataclass(frozen=True)
class PipelineOutcome:
    status: WorkflowStatus
    draft: dict[str, Any] | None = None
    issues: tuple[WorkflowIssue, ...] = ()
    release: dict[str, Any] | None = None
    error: str = ""


@dataclass
class PipelineContext:
    progress: Callable[[WorkflowStep, str, int], Awaitable[None]]
    ask_operator: Callable[[WorkflowQuestion], Awaitable[str]]
    cancelled: Callable[[], bool]
    activity: Callable[[WorkflowActivity], Awaitable[None]] | None = None
    latest_draft: dict[str, Any] | None = None

    def ensure_active(self) -> None:
        if self.cancelled():
            raise WorkflowCancelled

    def remember_draft(self, draft: dict[str, Any]) -> None:
        self.latest_draft = draft

    async def record(self, activity: WorkflowActivity) -> None:
        if self.activity is not None:
            await self.activity(activity)


class WorkflowPipeline(Protocol):
    async def run(self, seed: PipelineSeed, context: PipelineContext) -> PipelineOutcome: ...


@dataclass(frozen=True)
class PipelineCheck:
    draft: dict[str, Any]
    issues: tuple[WorkflowIssue, ...] = ()


class PipelineRuntime(Protocol):
    async def prepare(self, seed: PipelineSeed, context: PipelineContext) -> dict[str, Any]: ...

    async def check(self, draft: dict[str, Any], context: PipelineContext) -> PipelineCheck: ...

    async def repair(
        self,
        draft: dict[str, Any],
        issues: tuple[WorkflowIssue, ...],
        operator_answers: dict[str, str],
        context: PipelineContext,
    ) -> dict[str, Any]: ...

    async def publish(
        self,
        draft: dict[str, Any],
        context: PipelineContext,
    ) -> dict[str, Any]: ...


@dataclass
class SelfHealingPipeline:
    """Bounded resolve/check loop shared by first publication and republish."""

    runtime: PipelineRuntime
    max_rounds: int = 5
    max_unchanged_rounds: int = 2
    operation_timeout_s: float = 1800.0
    overall_timeout_s: float = 10800.0

    async def run(self, seed: PipelineSeed, context: PipelineContext) -> PipelineOutcome:
        if seed.draft is not None:
            context.remember_draft(seed.draft)
        try:
            async with asyncio.timeout(self.overall_timeout_s):
                return await self._run(seed, context)
        except _OperationTimeout:
            return PipelineOutcome(
                status=WorkflowStatus.FAILED,
                draft=context.latest_draft,
                error=f"录制处理步骤超过 {int(self.operation_timeout_s)} 秒时间预算",
            )
        except TimeoutError:
            return PipelineOutcome(
                status=WorkflowStatus.FAILED,
                draft=context.latest_draft,
                error=f"录制处理超过 {int(self.overall_timeout_s)} 秒总时间预算",
            )

    async def _run(self, seed: PipelineSeed, context: PipelineContext) -> PipelineOutcome:
        context.ensure_active()
        await context.progress(WorkflowStep.MATERIALIZING, "正在生成权威事实草稿", 0)
        draft = await self._bounded(self.runtime.prepare(seed, context))
        context.remember_draft(draft)
        unchanged = 0
        previous_issues: tuple[str, ...] | None = None
        previous_issue_map: dict[str, WorkflowIssue] = {}

        for round_number in range(1, self.max_rounds + 1):
            context.ensure_active()
            await context.progress(WorkflowStep.VERIFYING, "正在检查和验证能力", round_number)
            checked = await self._bounded(self.runtime.check(draft, context))
            draft = checked.draft
            context.remember_draft(draft)
            if not checked.issues:
                for issue in previous_issue_map.values():
                    await context.record(_issue_activity(
                        issue,
                        step=WorkflowStep.VERIFYING,
                        round_number=round_number,
                        status="resolved",
                        label=f"已解决：{issue.message}",
                    ))
                await context.progress(WorkflowStep.PUBLISHING, "正在原子发布能力", round_number)
                release = await self._bounded(self.runtime.publish(draft, context))
                return PipelineOutcome(
                    status=WorkflowStatus.PUBLISHED,
                    draft=draft,
                    release=release,
                )

            current_issue_map = {issue.issue_id: issue for issue in checked.issues}
            for issue_id, issue in current_issue_map.items():
                if issue_id not in previous_issue_map:
                    await context.record(_issue_activity(
                        issue,
                        step=WorkflowStep.VERIFYING,
                        round_number=round_number,
                        status="pending",
                        label=issue.message,
                    ))
            for issue_id, issue in previous_issue_map.items():
                if issue_id not in current_issue_map:
                    await context.record(_issue_activity(
                        issue,
                        step=WorkflowStep.VERIFYING,
                        round_number=round_number,
                        status="resolved",
                        label=f"已解决：{issue.message}",
                    ))
            previous_issue_map = current_issue_map

            external = tuple(
                issue for issue in checked.issues if issue.resolver == "external_blocked"
            )
            if external:
                for issue in external:
                    await context.record(_issue_activity(
                        issue,
                        step=WorkflowStep.RESOLVING,
                        round_number=round_number,
                        status="blocked",
                        label=issue.message,
                    ))
                return PipelineOutcome(
                    status=WorkflowStatus.EDITABLE,
                    draft=draft,
                    issues=external,
                )

            current_issues = _issue_signature(checked.issues)
            unchanged = unchanged + 1 if current_issues == previous_issues else 0
            previous_issues = current_issues
            if unchanged >= self.max_unchanged_rounds:
                for issue in checked.issues:
                    await context.record(_issue_activity(
                        issue,
                        step=WorkflowStep.RESOLVING,
                        round_number=round_number,
                        status="blocked",
                        label=f"连续验证未取得进展：{issue.message}",
                    ))
                return PipelineOutcome(
                    status=WorkflowStatus.EDITABLE,
                    draft=draft,
                    issues=checked.issues,
                    error="自动处理连续没有产生有效变化",
                )

            answers: dict[str, str] = {}
            for issue in checked.issues:
                if issue.resolver != "operator":
                    continue
                await context.record(_issue_activity(
                    issue,
                    step=WorkflowStep.RESOLVING,
                    round_number=round_number,
                    status="waiting_operator",
                    label=issue.message,
                ))
                answer = await context.ask_operator(WorkflowQuestion(
                    question_id=f"question:{issue.issue_id}",
                    issue_id=issue.issue_id,
                    text=issue.message,
                    options=[],
                    context_ref=issue.issue_id,
                ))
                answers[issue.issue_id] = answer

            await context.progress(WorkflowStep.RESOLVING, "正在解决验证问题", round_number)
            for issue in checked.issues:
                if issue.resolver == "operator":
                    continue
                await context.record(_issue_activity(
                    issue,
                    step=WorkflowStep.RESOLVING,
                    round_number=round_number,
                    status="running",
                    label=_issue_resolution_label(issue),
                ))
            repaired = await self._bounded(self.runtime.repair(
                draft,
                checked.issues,
                answers,
                context,
            ))
            draft = repaired
            context.remember_draft(draft)

        final = await self._bounded(self.runtime.check(draft, context))
        context.remember_draft(final.draft)
        return PipelineOutcome(
            status=WorkflowStatus.EDITABLE,
            draft=final.draft,
            issues=final.issues,
            error=f"自动处理达到 {self.max_rounds} 轮上限",
        )

    async def _bounded(self, operation: Awaitable[Any]) -> Any:
        try:
            async with asyncio.timeout(self.operation_timeout_s):
                return await operation
        except TimeoutError as exc:
            raise _OperationTimeout from exc


class _OperationTimeout(Exception):
    """Distinguish a bounded stage timeout from the whole-run deadline."""


def _issue_signature(issues: tuple[WorkflowIssue, ...]) -> tuple[str, ...]:
    """Track unresolved work, not incidental FlowSpec normalization churn."""

    return tuple(sorted(
        json.dumps(
            {
                "issue_id": issue.issue_id,
                "code": issue.code,
                "resolver": issue.resolver,
                "target": issue.target,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for issue in issues
    ))


def _issue_activity(
    issue: WorkflowIssue,
    *,
    step: WorkflowStep,
    round_number: int,
    status: str,
    label: str,
) -> WorkflowActivity:
    return WorkflowActivity(
        step=step,
        round=round_number,
        status=status,
        label=label,
        issue_id=issue.issue_id,
        code=issue.code,
        target=issue.target,
    )


def _issue_resolution_label(issue: WorkflowIssue) -> str:
    if issue.resolver == "collect_evidence":
        return f"正在自动补充验证证据：{issue.message}"
    if issue.resolver == "machine_repair":
        return f"正在自动修复能力契约：{issue.message}"
    return f"正在处理：{issue.message}"


SnapshotListener = Callable[[WorkflowSnapshot], Awaitable[None] | None]
CancelListener = Callable[[], Awaitable[None] | None]


@dataclass
class RecordingWorkflow:
    """Own one recording run and serialize every expensive operation.

    The module is deliberately unaware of WebSockets and browsers.  Callers
    submit commands and observe authoritative snapshots; one injected pipeline
    owns analysis, verification, review, publication and export.
    """

    snapshot: WorkflowSnapshot
    pipeline: WorkflowPipeline
    listener: SnapshotListener | None = None
    cancel_listener: CancelListener | None = None
    snapshot_path: Path | None = None
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _cancelled: bool = field(default=False, init=False, repr=False)
    _answer: asyncio.Future[str] | None = field(default=None, init=False, repr=False)

    async def start(self) -> WorkflowSnapshot:
        if self.snapshot.status == WorkflowStatus.IDLE:
            await self._set(
                WorkflowStatus.RECORDING,
                progress=WorkflowProgress(step=WorkflowStep.CAPTURING, label="正在录制页面操作"),
            )
        return self.snapshot

    async def finish(self) -> WorkflowSnapshot:
        if self._active():
            return self.snapshot
        if self.snapshot.status != WorkflowStatus.RECORDING:
            return self.snapshot
        await self._launch(PipelineSeed(kind="recording", use_live_notebook=True))
        return self.snapshot

    async def update_recording(
        self,
        *,
        request_count: int,
        insights: list[dict[str, Any]] | None = None,
    ) -> WorkflowSnapshot:
        if self.snapshot.status != WorkflowStatus.RECORDING:
            return self.snapshot
        changes: dict[str, Any] = {}
        if insights is not None:
            changes["insights"] = insights
        await self._set(
            WorkflowStatus.RECORDING,
            progress=WorkflowProgress(
                step=WorkflowStep.CAPTURING,
                label=f"已捕获 {request_count} 个请求",
                request_count=request_count,
            ),
            **changes,
        )
        return self.snapshot

    async def set_title(self, title: str) -> WorkflowSnapshot:
        if title == self.snapshot.title:
            return self.snapshot
        await self._set(self.snapshot.status, title=title)
        return self.snapshot

    async def ask_operator_question(self, question: WorkflowQuestion) -> str:
        """Let the active Pi tool use the same persisted workflow question."""
        if not self._active():
            raise ValueError("operator questions require an active analysis")
        return await self._ask_operator(question)

    async def republish(self) -> WorkflowSnapshot:
        if self._active():
            return self.snapshot
        # A published draft can only be republished after patch_draft moves it
        # back to editable.  Failed/cancelled runs have a newer authoritative
        # revision and must remain retryable even when the draft is unchanged.
        if self.snapshot.status == WorkflowStatus.PUBLISHED:
            return self.snapshot
        if self.snapshot.status not in {
            WorkflowStatus.EDITABLE,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.FAILED,
        }:
            raise ValueError(f"cannot republish recording in state {self.snapshot.status}")
        if self.snapshot.draft is None:
            raise ValueError("cannot republish without a draft")
        await self._launch(PipelineSeed(
            kind="edited_spec",
            draft=self.snapshot.draft,
            use_live_notebook=False,
        ))
        return self.snapshot

    async def patch_draft(
        self,
        draft: dict[str, Any],
        *,
        expected_revision: int,
    ) -> WorkflowSnapshot:
        if self._active():
            raise ValueError("cannot edit the draft while processing")
        if expected_revision != self.snapshot.revision:
            raise ValueError(
                f"recording workflow revision conflict: expected {expected_revision}, "
                f"current {self.snapshot.revision}"
            )
        if self.snapshot.status not in {WorkflowStatus.EDITABLE, WorkflowStatus.PUBLISHED}:
            raise ValueError(f"cannot edit recording in state {self.snapshot.status}")
        await self._set(
            WorkflowStatus.EDITABLE,
            draft=draft,
            release=None,
            issues=[],
            error="",
            progress=self._next_progress(WorkflowStep.READY, "修改已保存"),
        )
        return self.snapshot

    async def answer(self, question_id: str, answer: str) -> WorkflowSnapshot:
        question = self.snapshot.question
        if (
            self.snapshot.status != WorkflowStatus.WAITING_OPERATOR
            or question is None
            or question.question_id != question_id
            or self._answer is None
            or self._answer.done()
        ):
            raise ValueError("operator question is no longer active")
        self._answer.set_result(answer)
        return self.snapshot

    async def cancel(self) -> WorkflowSnapshot:
        self._cancelled = True
        if self.cancel_listener is not None:
            cancelled = self.cancel_listener()
            if isinstance(cancelled, Awaitable):
                await cancelled
        if self._answer is not None and not self._answer.done():
            self._answer.cancel()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self.snapshot.status not in {
            WorkflowStatus.PUBLISHED,
            WorkflowStatus.CANCELLED,
        }:
            await self._set(
                WorkflowStatus.CANCELLED,
                progress=self._next_progress(WorkflowStep.READY, "当前分析已终止，草稿已保留"),
                error="",
            )
        return self.snapshot

    async def wait(self) -> WorkflowSnapshot:
        task = self._task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return self.snapshot

    def _active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _launch(self, seed: PipelineSeed) -> None:
        self._cancelled = False
        await self._set(
            WorkflowStatus.PROCESSING,
            progress=self._next_progress(WorkflowStep.FREEZING, "正在冻结录制事实"),
            issues=[],
            release=None,
            error="",
        )
        self._task = asyncio.create_task(self._run(seed), name=f"recording-workflow:{self.snapshot.run_id}")

    async def _run(self, seed: PipelineSeed) -> None:
        context = PipelineContext(
            progress=self._progress,
            ask_operator=self._ask_operator,
            cancelled=lambda: self._cancelled,
            activity=self._record_activity,
        )
        try:
            outcome = await self.pipeline.run(seed, context)
            context.ensure_active()
            if outcome.status not in {
                WorkflowStatus.EDITABLE,
                WorkflowStatus.PUBLISHED,
                WorkflowStatus.FAILED,
            }:
                raise ValueError(f"pipeline returned non-terminal state {outcome.status}")
            await self._set(
                outcome.status,
                draft=(outcome.draft if outcome.draft is not None else self.snapshot.draft),
                issues=list(outcome.issues),
                release=outcome.release,
                error=outcome.error,
                progress=WorkflowProgress(
                    step=(
                        WorkflowStep.COMPLETE
                        if outcome.status == WorkflowStatus.PUBLISHED
                        else WorkflowStep.READY
                    ),
                    label=("发布完成" if outcome.status == WorkflowStatus.PUBLISHED else "处理已结束"),
                    request_count=self.snapshot.progress.request_count,
                ),
            )
        except (asyncio.CancelledError, WorkflowCancelled):
            return
        except Exception as exc:  # noqa: BLE001 - the authoritative draft must survive
            if not self._cancelled:
                await self._set(
                    WorkflowStatus.FAILED,
                    draft=(
                        context.latest_draft
                        if context.latest_draft is not None
                        else self.snapshot.draft
                    ),
                    error=str(exc),
                    progress=self._next_progress(WorkflowStep.READY, "处理失败，草稿已保留"),
                )
        finally:
            self._answer = None

    async def _progress(self, step: WorkflowStep, label: str, round_number: int = 0) -> None:
        if self._cancelled:
            raise WorkflowCancelled
        await self._set(
            WorkflowStatus.PROCESSING,
            progress=self._next_progress(step, label, round_number),
        )

    async def _ask_operator(self, question: WorkflowQuestion) -> str:
        if self._cancelled:
            raise WorkflowCancelled
        loop = asyncio.get_running_loop()
        self._answer = loop.create_future()
        await self._set(
            WorkflowStatus.WAITING_OPERATOR,
            question=question,
            progress=self._next_progress(WorkflowStep.RESOLVING, "等待操作人确认"),
        )
        try:
            answer = await self._answer
        except asyncio.CancelledError as exc:
            raise WorkflowCancelled from exc
        await self._set(
            WorkflowStatus.PROCESSING,
            progress=self._next_progress(WorkflowStep.RESOLVING, "已收到回答，继续处理"),
        )
        return answer

    async def _record_activity(self, activity: WorkflowActivity) -> None:
        if self._cancelled:
            raise WorkflowCancelled
        entries = list(self.snapshot.activity)
        entries.append(activity.model_copy(update={"sequence": (entries[-1].sequence + 1) if entries else 1}))
        await self._set(self.snapshot.status, activity=entries[-100:])

    def _next_progress(
        self,
        step: WorkflowStep,
        label: str,
        round_number: int = 0,
    ) -> WorkflowProgress:
        return WorkflowProgress(
            step=step,
            label=label,
            round=round_number,
            request_count=self.snapshot.progress.request_count,
        )

    async def _set(
        self,
        status: WorkflowStatus,
        *,
        progress: WorkflowProgress | None = None,
        **changes: Any,
    ) -> None:
        self.snapshot = transition_snapshot(self.snapshot, status, progress=progress, **changes)
        self._persist_snapshot()
        if self.listener is not None:
            emitted = self.listener(self.snapshot)
            if isinstance(emitted, Awaitable):
                await emitted

    def _persist_snapshot(self) -> None:
        """Persist the latest authority without coupling task lifetime to a socket."""

        if self.snapshot_path is None:
            return
        path = Path(self.snapshot_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(path.suffix + ".tmp")
        pending.write_text(
            self.snapshot.model_dump_json(indent=2),
            encoding="utf-8",
        )
        pending.replace(path)
