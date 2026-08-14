"""Production services for the canonical recording pipeline."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dano.execution.page.flow_spec import (
    FlowSpec,
    prepare_flow_release_candidate,
)
from dano.onboarding.recording_pipeline import RecordingPipelineServices
from dano.onboarding.recording_release import (
    ReleaseIssue,
    evaluate_recording_release,
    review_release_issues,
)
from dano.onboarding.recording_verify import (
    finalize_verification_state,
    verification_report,
)
from dano.onboarding.recording_workflow import (
    PipelineContext,
    WorkflowIssue,
)


PiProvider = Callable[[bool], Awaitable[Any]]
Materializer = Callable[[bool, PipelineContext], Awaitable[FlowSpec]]
Publisher = Callable[[FlowSpec, dict[str, Any], PipelineContext], Awaitable[dict[str, Any]]]


def _workflow_issue(issue: ReleaseIssue) -> WorkflowIssue:
    return WorkflowIssue(
        issue_id=issue.issue_id,
        code=issue.check_code,
        message=issue.message,
        resolver=issue.resolver,
        target={
            key: value
            for key, value in {
                "capability_id": issue.capability_id,
                "step_id": issue.step_id,
                "field_id": issue.field_id,
                "wire_path": issue.wire_path,
            }.items()
            if value
        },
        evidence=[{"ref": value} for value in issue.evidence_refs],
        allowed_operations=list(issue.suggested_operations),
    )


def _todo_issue(todo: dict[str, Any]) -> WorkflowIssue:
    resolver = str(todo.get("resolver") or "collect_evidence")
    if resolver not in {"machine_repair", "collect_evidence", "operator", "external_blocked"}:
        resolver = "collect_evidence"
    issue_id = str(todo.get("issue_id") or todo.get("target_id") or "")
    code = str(todo.get("check_code") or todo.get("kind") or "verification_pending")
    return WorkflowIssue(
        issue_id=issue_id or f"{code}:{todo.get('step_id') or todo.get('wire_path') or 'draft'}",
        code=code,
        message=str(todo.get("message") or f"待处理：{code}"),
        resolver=resolver,
        target={
            key: str(todo.get(key) or "")
            for key in ("capability_id", "step_id", "field_id", "wire_path", "target_id")
            if todo.get(key)
        },
        evidence=[dict(todo)],
        allowed_operations=[
            str(value)
            for value in (
                todo.get("suggested_operations")
                or todo.get("completion_ops")
                or [todo.get("completion_op") or todo.get("suggested_tool")]
            )
            if value
        ],
    )


@dataclass
class ProductionRecordingServices:
    """Bridge stable FlowSpec/Pi modules into the one workflow runtime."""

    recording_id: str
    materializer: Materializer
    pi_provider: PiProvider
    publisher: Publisher

    def pipeline_services(self) -> RecordingPipelineServices:
        return RecordingPipelineServices(
            materialize_recording=self.materialize_recording,
            plan_capabilities=self.plan_capabilities,
            verify=self.verify,
            review=self.review,
            repair=self.repair,
            publish=self.publish,
        )

    async def materialize_recording(
        self,
        use_live_notebook: bool,
        context: PipelineContext,
    ) -> dict[str, Any]:
        spec = await self.materializer(use_live_notebook, context)
        return spec.model_dump(mode="json")

    async def plan_capabilities(
        self,
        draft: dict[str, Any],
        use_live_notebook: bool,
        context: PipelineContext,
    ) -> dict[str, Any]:
        context.ensure_active()
        spec = FlowSpec.model_validate(draft)
        pi = await self.pi_provider(not use_live_notebook)
        pi.bind_flow_spec(spec)
        result = await pi.prompt(
            "为当前录制生成完整且可执行的业务能力契约。先读取录制状态，基于页面、HAR、"
            "字段证据、字典和上下游响应确定能力成员、字段七维属性和依赖编排；"
            "禁止使用固定页面、字段或接口假设。最后必须调用 submit_recording_plan，"
            "一次提交完整能力集合，不得部分发布。"
            f" recording_id={self.recording_id} use_live_notebook={str(use_live_notebook).lower()}"
        )
        context.ensure_active()
        if isinstance(result, dict) and result.get("status") == "analysis_terminated":
            context.ensure_active()
        if pi.last_submission_kind != "plan":
            raise RuntimeError("Pi 未提交完整 recording plan")
        return pi.current_flow_spec().model_dump(mode="json")

    async def verify(
        self,
        draft: dict[str, Any],
        context: PipelineContext,
    ) -> tuple[dict[str, Any], tuple[WorkflowIssue, ...]]:
        context.ensure_active()
        spec = FlowSpec.model_validate(draft)
        report = verification_report(spec)
        if report["todos"]:
            return draft, tuple(_todo_issue(todo) for todo in report["todos"])

        spec, report = finalize_verification_state(
            spec,
            rounds=0,
            max_rounds=0,
            stop_reason="completed",
        )
        decision = evaluate_recording_release(spec)
        issues = tuple(
            _workflow_issue(issue)
            for capability in decision.capabilities
            for issue in capability.issues
        )
        return spec.model_dump(mode="json"), issues

    async def review(
        self,
        draft: dict[str, Any],
        context: PipelineContext,
    ) -> tuple[dict[str, Any], tuple[WorkflowIssue, ...]]:
        context.ensure_active()
        spec = FlowSpec.model_validate(draft)
        decision = evaluate_recording_release(spec)
        if decision.callable_spec is None:
            issues = tuple(
                _workflow_issue(issue)
                for capability in decision.capabilities
                for issue in capability.issues
            )
            return draft, issues

        release_spec, candidate = prepare_flow_release_candidate(decision.callable_spec)
        pi = await self.pi_provider(True)
        pi.bind_flow_spec(release_spec)
        version = int((release_spec.meta or {}).get("current_version") or 0)
        try:
            await pi.prompt(
                "审核当前完整发布候选。必须读取录制状态和验证报告，并调用 "
                "submit_recording_review 提交 acceptance、security、compliance 三角色结论。"
                "拒绝时必须给出结构化 issues，包含 resolver、定位字段和允许的修复操作；"
                "不得解析或依赖中文错误文本。"
                f" recording_id={self.recording_id} flow_version={version}"
            )
            context.ensure_active()
            pi.require_publish_review(
                flow_version=version,
                flow_fingerprint=str(candidate["flow_fingerprint"]),
                machine_decision=decision,
            )
        except Exception:
            issues = review_release_issues(dict(getattr(pi, "last_review", None) or {}))
            if not issues:
                raise
            return draft, tuple(_workflow_issue(issue) for issue in issues)
        return release_spec.model_dump(mode="json"), ()

    async def repair(
        self,
        draft: dict[str, Any],
        issues: tuple[WorkflowIssue, ...],
        operator_answers: dict[str, str],
        context: PipelineContext,
    ) -> dict[str, Any]:
        context.ensure_active()
        pi = await self.pi_provider(False)
        pi.bind_flow_spec(FlowSpec.model_validate(draft))
        payload = [issue.model_dump(mode="json") for issue in issues]
        await pi.prompt(
            "继续同一录制的修复闭环。只按结构化 issue 的 resolver、target、evidence 和 "
            "allowed_operations 处理；machine_repair 提交 FlowSpec 修复，collect_evidence 使用"
            "回放/页面/字典/依赖工具补证，operator 使用已绑定回答。不得降低闸门、猜测事实、"
            "改写接口路径或只修复部分能力。完成后调用 submit_recording_repair。issues="
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + " operator_answers="
            + json.dumps(operator_answers, ensure_ascii=False, separators=(",", ":"))
        )
        context.ensure_active()
        if pi.last_submission_kind not in {"repair", "plan"}:
            raise RuntimeError("Pi 未提交 recording repair")
        return pi.current_flow_spec().model_dump(mode="json")

    async def publish(
        self,
        draft: dict[str, Any],
        context: PipelineContext,
    ) -> dict[str, Any]:
        context.ensure_active()
        spec = FlowSpec.model_validate(draft)
        decision = evaluate_recording_release(spec)
        if decision.callable_spec is None:
            raise RuntimeError("发布边界前能力契约已变化")
        release_spec, candidate = prepare_flow_release_candidate(decision.callable_spec)
        return await self.publisher(release_spec, candidate, context)
