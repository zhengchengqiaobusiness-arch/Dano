"""Canonical recording analysis pipeline.

This module owns the order of expensive recording work.  Browser capture,
model prompting and persistence are injected as services, which keeps the
workflow deterministic and lets the gateway remain a transport adapter.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dano.onboarding.recording_workflow import (
    PipelineCheck,
    PipelineContext,
    PipelineSeed,
    WorkflowIssue,
    WorkflowStep,
)


Draft = dict[str, Any]
PrepareRecording = Callable[[bool, PipelineContext], Awaitable[Draft]]
CheckDraft = Callable[
    [Draft, PipelineContext],
    Awaitable[tuple[Draft, tuple[WorkflowIssue, ...]]],
]
RepairDraft = Callable[
    [Draft, tuple[WorkflowIssue, ...], dict[str, str], PipelineContext],
    Awaitable[Draft],
]
PublishDraft = Callable[[Draft, PipelineContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RecordingPipelineServices:
    materialize_recording: PrepareRecording
    verify: CheckDraft
    repair: RepairDraft
    publish: PublishDraft


@dataclass
class CanonicalRecordingRuntime:
    """Run both first publication and republish through one ordered contract."""

    services: RecordingPipelineServices

    async def prepare(self, seed: PipelineSeed, context: PipelineContext) -> Draft:
        context.ensure_active()
        if seed.kind == "recording":
            await context.progress(WorkflowStep.FREEZING, "正在冻结并汇总录制事实", 0)
            draft = await self.services.materialize_recording(
                seed.use_live_notebook,
                context,
            )
            context.remember_draft(draft)
            return draft
        if seed.kind == "edited_spec":
            if seed.draft is None:
                raise ValueError("edited_spec requires a draft")
            return dict(seed.draft)
        raise ValueError(f"unsupported recording pipeline seed: {seed.kind}")

    async def check(self, draft: Draft, context: PipelineContext) -> PipelineCheck:
        context.ensure_active()
        await context.progress(WorkflowStep.COMPILING, "正在编译能力调用结构", 0)
        context.ensure_active()
        await context.progress(WorkflowStep.VERIFYING, "正在验证接口、字段和依赖", 0)
        verified, verification_issues = await self.services.verify(draft, context)
        return PipelineCheck(draft=verified, issues=verification_issues)

    async def repair(
        self,
        draft: Draft,
        issues: tuple[WorkflowIssue, ...],
        operator_answers: dict[str, str],
        context: PipelineContext,
    ) -> Draft:
        context.ensure_active()
        return await self.services.repair(
            draft,
            issues,
            operator_answers,
            context,
        )

    async def publish(self, draft: Draft, context: PipelineContext) -> dict[str, Any]:
        context.ensure_active()
        await context.progress(WorkflowStep.PUBLISHING, "正在原子冻结全部能力", 0)
        return await self.services.publish(draft, context)
