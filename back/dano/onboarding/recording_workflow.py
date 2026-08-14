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
    revision: int = Field(default=0, ge=0)
    status: WorkflowStatus = WorkflowStatus.IDLE
    progress: WorkflowProgress = Field(default_factory=WorkflowProgress)
    capture_frozen: bool = False
    draft: dict[str, Any] | None = None
    issues: list[WorkflowIssue] = Field(default_factory=list)
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

    def ensure_active(self) -> None:
        if self.cancelled():
            raise WorkflowCancelled


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

    async def run(self, seed: PipelineSeed, context: PipelineContext) -> PipelineOutcome:
        context.ensure_active()
        await context.progress(WorkflowStep.MATERIALIZING, "正在生成权威事实草稿", 0)
        draft = await self.runtime.prepare(seed, context)
        unchanged = 0
        previous = _stable_payload(draft)

        for round_number in range(1, self.max_rounds + 1):
            context.ensure_active()
            await context.progress(WorkflowStep.VERIFYING, "正在检查和验证能力", round_number)
            checked = await self.runtime.check(draft, context)
            draft = checked.draft
            if not checked.issues:
                await context.progress(WorkflowStep.PUBLISHING, "正在原子发布能力", round_number)
                release = await self.runtime.publish(draft, context)
                return PipelineOutcome(
                    status=WorkflowStatus.PUBLISHED,
                    draft=draft,
                    release=release,
                )

            external = tuple(
                issue for issue in checked.issues if issue.resolver == "external_blocked"
            )
            if external:
                return PipelineOutcome(
                    status=WorkflowStatus.EDITABLE,
                    draft=draft,
                    issues=external,
                )

            answers: dict[str, str] = {}
            for issue in checked.issues:
                if issue.resolver != "operator":
                    continue
                answer = await context.ask_operator(WorkflowQuestion(
                    question_id=f"question:{issue.issue_id}",
                    issue_id=issue.issue_id,
                    text=issue.message,
                    options=[],
                    context_ref=issue.issue_id,
                ))
                answers[issue.issue_id] = answer

            await context.progress(WorkflowStep.RESOLVING, "正在解决验证问题", round_number)
            repaired = await self.runtime.repair(
                draft,
                checked.issues,
                answers,
                context,
            )
            current = _stable_payload(repaired)
            unchanged = unchanged + 1 if current == previous else 0
            draft = repaired
            previous = current
            if unchanged >= self.max_unchanged_rounds:
                return PipelineOutcome(
                    status=WorkflowStatus.EDITABLE,
                    draft=draft,
                    issues=checked.issues,
                    error="自动处理连续没有产生有效变化",
                )

        final = await self.runtime.check(draft, context)
        return PipelineOutcome(
            status=WorkflowStatus.EDITABLE,
            draft=final.draft,
            issues=final.issues,
            error=f"自动处理达到 {self.max_rounds} 轮上限",
        )


def _stable_payload(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


SnapshotListener = Callable[[WorkflowSnapshot], Awaitable[None] | None]


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

    async def republish(self) -> WorkflowSnapshot:
        if self._active():
            return self.snapshot
        if self.snapshot.status not in {
            WorkflowStatus.EDITABLE,
            WorkflowStatus.PUBLISHED,
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
            progress=WorkflowProgress(step=WorkflowStep.READY, label="修改已保存"),
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
                progress=WorkflowProgress(step=WorkflowStep.READY, label="当前分析已终止，草稿已保留"),
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
            progress=WorkflowProgress(step=WorkflowStep.FREEZING, label="正在冻结录制事实"),
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
                draft=outcome.draft,
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
                ),
            )
        except (asyncio.CancelledError, WorkflowCancelled):
            return
        except Exception as exc:  # noqa: BLE001 - the authoritative draft must survive
            if not self._cancelled:
                await self._set(
                    WorkflowStatus.FAILED,
                    error=str(exc),
                    progress=WorkflowProgress(step=WorkflowStep.READY, label="处理失败，草稿已保留"),
                )
        finally:
            self._answer = None

    async def _progress(self, step: WorkflowStep, label: str, round_number: int = 0) -> None:
        if self._cancelled:
            raise WorkflowCancelled
        await self._set(
            WorkflowStatus.PROCESSING,
            progress=WorkflowProgress(step=step, label=label, round=round_number),
        )

    async def _ask_operator(self, question: WorkflowQuestion) -> str:
        if self._cancelled:
            raise WorkflowCancelled
        loop = asyncio.get_running_loop()
        self._answer = loop.create_future()
        await self._set(
            WorkflowStatus.WAITING_OPERATOR,
            question=question,
            progress=WorkflowProgress(step=WorkflowStep.RESOLVING, label="等待操作人确认"),
        )
        try:
            answer = await self._answer
        except asyncio.CancelledError as exc:
            raise WorkflowCancelled from exc
        await self._set(
            WorkflowStatus.PROCESSING,
            progress=WorkflowProgress(step=WorkflowStep.RESOLVING, label="已收到回答，继续处理"),
        )
        return answer

    async def _set(
        self,
        status: WorkflowStatus,
        *,
        progress: WorkflowProgress | None = None,
        **changes: Any,
    ) -> None:
        self.snapshot = transition_snapshot(self.snapshot, status, progress=progress, **changes)
        if self.listener is not None:
            emitted = self.listener(self.snapshot)
            if isinstance(emitted, Awaitable):
                await emitted
