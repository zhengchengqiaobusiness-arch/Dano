"""Single authoritative state model for the recording workflow.

The gateway and the browser UI observe this module through ``WorkflowSnapshot``.
Implementation details such as Pi prompts, verification rounds and publishing are
represented by ``progress.step`` rather than separate externally visible states.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from dano.infra.run_logging import emit_run_event, note_run_fact


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
        WorkflowStatus.PROCESSING, WorkflowStatus.EDITABLE, WorkflowStatus.FAILED,
    }),
    WorkflowStatus.FAILED: frozenset({
        WorkflowStatus.PROCESSING, WorkflowStatus.EDITABLE, WorkflowStatus.CANCELLED,
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
    machine_verification: bool = False


@dataclass(frozen=True)
class PipelineOutcome:
    status: WorkflowStatus
    draft: dict[str, Any] | None = None
    issues: tuple[WorkflowIssue, ...] = ()
    release: dict[str, Any] | None = None
    error: str = ""


@dataclass
class RepairReport:
    applied: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    still_pending: list[str] = field(default_factory=list)


@dataclass
class PipelineContext:
    progress: Callable[[WorkflowStep, str, int], Awaitable[None]]
    ask_operator: Callable[[WorkflowQuestion], Awaitable[str]]
    cancelled: Callable[[], bool]
    activity: Callable[[WorkflowActivity], Awaitable[None]] | None = None
    persist_stage_six: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    latest_draft: dict[str, Any] | None = None
    machine_verification: bool = False
    last_repair_report: RepairReport | None = None
    current_round: int = 0

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
        context.machine_verification = seed.machine_verification
        context.ensure_active()
        if seed.kind == "edited_spec":
            await context.progress(
                WorkflowStep.VERIFYING if seed.machine_verification else WorkflowStep.MATERIALIZING,
                "正在开始机器验证" if seed.machine_verification else "正在加载已保存草稿",
                0,
            )
        else:
            await context.progress(WorkflowStep.MATERIALIZING, "正在生成权威事实草稿", 0)
        draft = await self._bounded(self.runtime.prepare(seed, context))
        context.remember_draft(draft)
        if seed.kind == "recording" and context.persist_stage_six is not None:
            await context.persist_stage_six(draft)
            if seed.machine_verification:
                await context.progress(
                    WorkflowStep.VERIFYING,
                    "第 1～6 阶段已完成，开始机器验证",
                    0,
                )
        if not seed.machine_verification:
            if not list(draft.get("capabilities") or []):
                return PipelineOutcome(
                    status=WorkflowStatus.EDITABLE,
                    draft=context.latest_draft or draft,
                    error="尚未生成可发布能力，阶段六结果已保存，可继续分析",
                )
            emit_run_event(
                "recording.verification.skipped",
                stage="verification",
                status="skipped",
                summary="verification skipped",
                details={"machine_verification": False},
            )
            await context.progress(
                WorkflowStep.PUBLISHING,
                "机器验证已关闭，正在直接导出当前 Skill",
                0,
            )
            release = await self._bounded(self.runtime.publish(draft, context))
            return PipelineOutcome(
                status=WorkflowStatus.PUBLISHED,
                draft=context.latest_draft or draft,
                release=release,
            )
        unchanged = 0
        previous_fingerprint = ""
        previous_issues: tuple[str, ...] | None = None
        previous_unresolved = 0
        previous_issue_map: dict[str, WorkflowIssue] = {}
        last_applied = False
        round_number = 0

        while True:
            context.ensure_active()
            round_number += 1
            context.current_round = round_number
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
                        label=_resolved_thought(issue),
                    ))
                await context.progress(WorkflowStep.PUBLISHING, "正在原子发布能力", round_number)
                release = await self._bounded(self.runtime.publish(draft, context))
                return PipelineOutcome(
                    status=WorkflowStatus.PUBLISHED,
                    draft=context.latest_draft or draft,
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
                        label=_discovery_thought(issue),
                    ))
            for issue_id, issue in previous_issue_map.items():
                if issue_id not in current_issue_map:
                    await context.record(_issue_activity(
                        issue,
                        step=WorkflowStep.VERIFYING,
                        round_number=round_number,
                        status="resolved",
                        label=_resolved_thought(issue),
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
            current_fingerprint = _draft_fingerprint(draft)
            unresolved = len(checked.issues)
            stalled = (
                previous_issues is not None
                and current_fingerprint == previous_fingerprint
                and current_issues == previous_issues
                and not last_applied
                and unresolved >= previous_unresolved
            )
            unchanged = unchanged + 1 if stalled else 0
            previous_issues = current_issues
            previous_fingerprint = current_fingerprint
            previous_unresolved = unresolved
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
                    label=_discovery_thought(issue) + " 需要你确认后我才能继续。",
                ))
                answer = await context.ask_operator(_operator_question(issue))
                answers[issue.issue_id] = answer

            await context.progress(WorkflowStep.RESOLVING, "正在解决验证问题", round_number)
            machine_issues = tuple(
                issue for issue in checked.issues if issue.resolver != "operator"
            )
            if machine_issues:
                await context.record(WorkflowActivity(
                    step=WorkflowStep.RESOLVING,
                    round=round_number,
                    status="running",
                    label=_plan_thought(machine_issues),
                ))
            repaired = await self._bounded(self.runtime.repair(
                draft,
                checked.issues,
                answers,
                context,
            ))
            draft = repaired
            context.remember_draft(draft)
            report = context.last_repair_report
            last_applied = bool(report and report.applied)
            await context.record(WorkflowActivity(
                step=WorkflowStep.RESOLVING,
                round=round_number,
                status="running",
                label=_result_thought(report, applied=last_applied, remaining=len(checked.issues)),
            ))

    async def _bounded(self, operation: Awaitable[Any]) -> Any:
        try:
            async with asyncio.timeout(self.operation_timeout_s):
                return await operation
        except TimeoutError as exc:
            raise _OperationTimeout from exc


class _OperationTimeout(Exception):
    """Distinguish a bounded stage timeout from the whole-run deadline."""


def _draft_fingerprint(draft: dict[str, Any] | None) -> str:
    payload = json.dumps(draft or {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _issue_subject(issue: WorkflowIssue) -> str:
    label = str(issue.target.get("field_label") or "").strip()
    path = str(
        issue.target.get("wire_path")
        or issue.target.get("path")
        or ""
    ).removeprefix("body.").removeprefix("query.")
    step = str(issue.target.get("step_id") or "").strip()
    if label and path and label != path and not path.endswith(label):
        field = f"{label} / {path}"
    else:
        field = path or label or str(issue.target.get("target_id") or "")
    if step and field and step not in field:
        field = f"{step} {field}"
    if field:
        return f"「{field}」"
    return ""


def _discovery_thought(issue: WorkflowIssue) -> str:
    subject = _issue_subject(issue)
    message = str(issue.message or "").strip()
    code = str(issue.code or "")
    if code == "write_verify" or message in {"待处理：write_verify", "待处理:write_verify"}:
        return "发现了问题：写操作还没有回读校验，现在不能证明这次提交真的生效。"
    if code == "enum" or message.startswith("待处理：enum"):
        target = subject or "某个选项字段"
        return f"发现了问题：{target} 的枚举选项还不完整，调用时可能选不到正确值。"
    if "来源为 unknown" in message:
        return f"发现了问题：{message.rstrip('。')}，提交时不知道该从哪里取值。"
    if "同时填入" in message or "路径歧义" in message:
        return f"发现了问题：{message.rstrip('。')}。一个参数不该同时改多处。"
    if code == "required_axis_unconfirmed":
        target = subject or "该字段"
        return f"发现了问题：还不能确定{target}在提交时是必填还是可选。"
    if message.startswith("待处理："):
        return f"发现了问题：{message.removeprefix('待处理：')} 还没完成。"
    return f"发现了问题：{message}"


def _plan_thought(issues: tuple[WorkflowIssue, ...]) -> str:
    evidence = sum(1 for issue in issues if issue.resolver == "collect_evidence")
    repair = sum(1 for issue in issues if issue.resolver == "machine_repair")
    actions: list[str] = []
    if evidence:
        actions.append(f"补 {evidence} 项验证证据")
    if repair:
        actions.append(f"修 {repair} 处能力契约")
    action = "，".join(actions) or "继续处理剩余问题"
    samples = "；".join(
        (_issue_subject(issue) or issue.code or issue.message)[:40]
        for issue in issues[:3]
    )
    more = f" 等 {len(issues)} 项" if len(issues) > 3 else ""
    return (
        f"我觉得应该这样处理：本轮先{action}。"
        f"具体包括 {samples}{more}。"
        "准备按能力分组，一个能力一个能力地定向修复，不重新划分能力。"
    )


def _result_thought(report: RepairReport | None, *, applied: bool, remaining: int) -> str:
    if report is None:
        return "本轮结果：修复步骤已结束，接下来会再检查一遍这些问题是否还在。"
    bits: list[str] = []
    if "deterministic_fix" in report.applied:
        bits.append("确定性修复改动了 FlowSpec")
    elif "deterministic_fix" in report.skipped:
        bits.append("确定性修复没有可自动改的地方")
    if report.resolved:
        bits.append(f"已按你的确认写回 {len(report.resolved)} 项")
    if report.applied and any(item != "deterministic_fix" for item in report.applied):
        bits.append("已尝试处理剩余验证问题")
    if report.still_pending:
        bits.append(f"还有 {len(report.still_pending)} 项没有落地")
    if not bits:
        bits.append("本轮没有产生可确认的修复")
    next_step = (
        "接下来会再检查一遍，看这些问题是否消失。"
        if applied
        else "FlowSpec 没有有效变化，若下一轮仍无进展就会停下来交给你改。"
    )
    return f"本轮结果：{'；'.join(bits)}。还剩 {remaining} 个问题。{next_step}"


def _resolved_thought(issue: WorkflowIssue) -> str:
    subject = _issue_subject(issue)
    if subject:
        return f"已经处理好了：{subject} 对应的验证问题这轮检查已经消失。"
    return f"已经处理好了：{issue.message}"


def _operator_question(issue: WorkflowIssue) -> WorkflowQuestion:
    if issue.code == "required_axis_unconfirmed":
        field = str(
            issue.target.get("field_label")
            or issue.target.get("wire_path")
            or "该字段"
        ).removeprefix("body.").removeprefix("query.")
        return WorkflowQuestion(
            question_id=f"question:{issue.issue_id}",
            issue_id=issue.issue_id,
            text=f'请确认"{field}"在提交申请时是否必须填写。\n请输入"必填"或"可选"。',
            options=["必填", "可选"],
            context_ref=issue.issue_id,
        )
    if issue.code == "field_source_unknown":
        field = str(
            issue.target.get("wire_path")
            or issue.target.get("field_id")
            or "该字段"
        ).removeprefix("body.").removeprefix("query.")
        label = str(issue.target.get("field_label") or "")
        display = f'"{label}"({field})' if label else f'"{field}"'
        return WorkflowQuestion(
            question_id=f"question:{issue.issue_id}",
            issue_id=issue.issue_id,
            text=(
                f"字段 {display} 没有可信的来源证据。"
                "请确认：这个字段是调用 Skill 时由用户填写的参数，"
                "还是应使用录制时捕获的固定值？"
                "请输入「用户参数」或「固定值」。"
            ),
            options=["用户参数", "固定值"],
            context_ref=issue.issue_id,
        )
    return WorkflowQuestion(
        question_id=f"question:{issue.issue_id}",
        issue_id=issue.issue_id,
        text=f"请确认以下业务规则：{issue.message}",
        options=[],
        context_ref=issue.issue_id,
    )


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
    persist_stage_six: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _cancelled: bool = field(default=False, init=False, repr=False)
    _answer: asyncio.Future[str] | None = field(default=None, init=False, repr=False)
    _latest_draft: dict[str, Any] | None = field(default=None, init=False, repr=False)

    async def start(self) -> WorkflowSnapshot:
        if self.snapshot.status == WorkflowStatus.IDLE:
            await self._set(
                WorkflowStatus.RECORDING,
                progress=WorkflowProgress(step=WorkflowStep.CAPTURING, label="正在录制页面操作"),
            )
        return self.snapshot

    async def finish(self, *, machine_verification: bool = False) -> WorkflowSnapshot:
        if self._active():
            return self.snapshot
        if self.snapshot.status != WorkflowStatus.RECORDING:
            return self.snapshot
        await self._launch(PipelineSeed(
            kind="recording",
            use_live_notebook=True,
            machine_verification=machine_verification,
        ))
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

    async def update_live_insights(self, insights: list[dict[str, Any]]) -> WorkflowSnapshot:
        """Checkpoint bounded Pi conclusions without changing workflow progress."""

        if self.snapshot.status == WorkflowStatus.IDLE:
            return self.snapshot
        await self._set(self.snapshot.status, insights=insights[-100:])
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

    async def republish(self, *, machine_verification: bool = False) -> WorkflowSnapshot:
        if self._active():
            return self.snapshot
        # Direct publish (no machine verification) stays published until the
        # operator edits.  Stage 7 resume must be allowed from a published
        # draft: move it back to editable, then run the verify/repair loop.
        if self.snapshot.status == WorkflowStatus.PUBLISHED:
            if not machine_verification or self.snapshot.draft is None:
                return self.snapshot
            await self._set(
                WorkflowStatus.EDITABLE,
                draft=self.snapshot.draft,
                release=None,
                issues=[],
                error="",
                progress=self._next_progress(WorkflowStep.READY, "开始机器验证"),
            )
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
            machine_verification=machine_verification,
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
        if self.snapshot.status not in {
            WorkflowStatus.EDITABLE,
            WorkflowStatus.PUBLISHED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }:
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
        if self.snapshot.status not in {
            WorkflowStatus.PUBLISHED,
            WorkflowStatus.CANCELLED,
        }:
            await self._set(
                WorkflowStatus.CANCELLED,
                draft=(
                    self._latest_draft
                    if self._latest_draft is not None
                    else self.snapshot.draft
                ),
                progress=self._next_progress(WorkflowStep.READY, "当前分析已终止，草稿已保留"),
                error="",
            )
        await self._stop_running_work()
        return self.snapshot

    async def _stop_running_work(self) -> None:
        if self.cancel_listener is not None:
            try:
                cancelled = self.cancel_listener()
                if isinstance(cancelled, Awaitable):
                    await asyncio.wait_for(cancelled, timeout=8.0)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - UI already left the run
                pass
        if self._answer is not None and not self._answer.done():
            self._answer.cancel()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(self._task, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                pass

    async def wait(self) -> WorkflowSnapshot:
        task = self._task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return self.snapshot

    def _active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _launch(self, seed: PipelineSeed) -> None:
        self._cancelled = False
        if seed.kind == "edited_spec" and seed.machine_verification:
            progress = self._next_progress(WorkflowStep.VERIFYING, "正在开始机器验证")
        else:
            progress = self._next_progress(WorkflowStep.FREEZING, "正在完成尾部分析并冻结录制事实")
        await self._set(
            WorkflowStatus.PROCESSING,
            progress=progress,
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
            persist_stage_six=self.persist_stage_six,
        )
        remember = context.remember_draft

        def remember_latest(draft: dict[str, Any]) -> None:
            remember(draft)
            self._latest_draft = draft

        context.remember_draft = remember_latest  # type: ignore[method-assign]
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
        changes: dict[str, Any] = {}
        if self._latest_draft is not None:
            changes["draft"] = self._latest_draft
        await self._set(
            WorkflowStatus.PROCESSING,
            progress=self._next_progress(step, label, round_number),
            **changes,
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
        if self._cancelled and status not in {
            WorkflowStatus.CANCELLED,
            WorkflowStatus.PUBLISHED,
        }:
            return
        previous = self.snapshot
        self.snapshot = transition_snapshot(self.snapshot, status, progress=progress, **changes)
        current = self.snapshot
        if previous.status != current.status or previous.progress.step != current.progress.step:
            spec = current.draft
            capability_count = 0
            if isinstance(spec, dict):
                capability_count = len(spec.get("capabilities") or [])
            emit_run_event(
                "recording.workflow.transition",
                stage="workflow",
                status="progress",
                summary=current.progress.label or f"{previous.status.value} → {current.status.value}",
                run_id=current.run_id,
                action=current.action,
                details={
                    "status_from": previous.status.value,
                    "status_to": current.status.value,
                    "step_from": previous.progress.step.value,
                    "step_to": current.progress.step.value,
                    "label": current.progress.label,
                    "revision": current.revision,
                    "request_count": current.progress.request_count,
                    "issue_count": len(current.issues),
                    "capability_count": capability_count,
                },
            )
            note_run_fact(
                request_count=current.progress.request_count,
                issue_count=len(current.issues),
                capability_count=capability_count,
            )
        if (
            current.status in {
                WorkflowStatus.PUBLISHED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
            }
            and previous.status != current.status
        ):
            result = {
                WorkflowStatus.PUBLISHED: "succeeded",
                WorkflowStatus.FAILED: "failed",
                WorkflowStatus.CANCELLED: "cancelled",
            }[current.status]
            emit_run_event(
                "recording.run.completed",
                stage="end",
                status=result,
                summary=(
                    "录制运行已取消"
                    if current.status == WorkflowStatus.CANCELLED
                    else "录制运行失败"
                    if current.status == WorkflowStatus.FAILED
                    else "录制工作流已结束"
                ),
                run_id=current.run_id,
                action=current.action,
                details={
                    "result": result,
                    "error": current.error,
                    "request_count": current.progress.request_count,
                    "issue_count": len(current.issues),
                },
            )
        if self.listener is not None:
            emitted = self.listener(self.snapshot)
            if isinstance(emitted, Awaitable):
                await emitted
