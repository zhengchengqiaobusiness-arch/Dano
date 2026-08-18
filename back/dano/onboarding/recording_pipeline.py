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
PlanCapabilities = Callable[[Draft, bool, PipelineContext], Awaitable[Draft]]
CheckDraft = Callable[
    [Draft, PipelineContext],
    Awaitable[tuple[Draft, tuple[WorkflowIssue, ...]]],
]
RepairDraft = Callable[
    [Draft, tuple[WorkflowIssue, ...], dict[str, str], PipelineContext],
    Awaitable[Draft],
]
PublishDraft = Callable[[Draft, PipelineContext], Awaitable[dict[str, Any]]]


def _incomplete_live_analysis_reasons(draft: Draft) -> list[str]:
    """Return explicit live-analysis blockers without inventing a legacy gate."""
    meta = draft.get("meta") if isinstance(draft, dict) else None
    if not isinstance(meta, dict):
        return []
    reasons: list[str] = []
    generation = meta.get("capability_generation")
    if isinstance(generation, dict) and generation.get("initial_completed") is False:
        reasons.append("能力计划未完成")
    model = meta.get("capability_model")
    if isinstance(model, dict) and str(model.get("status") or "") in {
        "needs_review", "awaiting_materialization", "incomplete", "failed",
    }:
        reasons.append("能力边界仍待分析")
    goal = meta.get("recording_goal_contract")
    if isinstance(goal, dict) and goal.get("satisfied") is False:
        expected = int(goal.get("expected_count") or 0)
        actual = int(goal.get("materialized_count") or 0)
        reasons.append(f"目标能力数量未满足（需要 {expected}，当前 {actual}）")
    unresolved = meta.get("unresolved_live_agent_ops")
    if isinstance(unresolved, list) and unresolved:
        reasons.append(f"仍有 {len(unresolved)} 个字段或关联结论未完成")
    return list(dict.fromkeys(reasons))


@dataclass(frozen=True)
class RecordingPipelineServices:
    materialize_recording: PrepareRecording
    plan_capabilities: PlanCapabilities
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
            # With machine verification disabled, the live notebook is the
            # authoritative semantic plan.  Do not start a second/final Pi
            # planning pass: direct export must use exactly what real-time
            # capture and analysis already materialized.
            if not seed.machine_verification:
                blockers = _incomplete_live_analysis_reasons(draft)
                if blockers:
                    raise RuntimeError("实时分析未完成，已保留草稿：" + "；".join(blockers))
                return draft
        elif seed.kind == "edited_spec":
            if seed.draft is None:
                raise ValueError("edited_spec requires a draft")
            # A human-edited draft is already the authoritative capability
            # boundary. Republish must validate it, never ask Pi to divide it
            # into a different set of capabilities again.
            return dict(seed.draft)
        else:
            raise ValueError(f"unsupported recording pipeline seed: {seed.kind}")

        context.ensure_active()
        await context.progress(WorkflowStep.ANALYZING, "正在规划完整业务能力", 0)
        planned = await self.services.plan_capabilities(
            draft,
            seed.use_live_notebook,
            context,
        )
        context.remember_draft(planned)
        return planned

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
