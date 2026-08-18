"""Production services for the canonical recording pipeline."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dano.execution.page.flow_spec import (
    FlowSpec,
    auto_fix_flow_spec,
    prepare_flow_release_candidate,
)
from dano.onboarding.recording_pipeline import RecordingPipelineServices
from dano.onboarding.recording_release import (
    ReleaseIssue,
    evaluate_recording_release,
)
from dano.onboarding.recording_verify import (
    finalize_verification_state,
)
from dano.onboarding.recording_workflow import (
    PipelineContext,
    RepairReport,
    WorkflowIssue,
)


PiProvider = Callable[[bool], Awaitable[Any]]
Materializer = Callable[[bool, PipelineContext], Awaitable[FlowSpec]]
Publisher = Callable[[FlowSpec, dict[str, Any], PipelineContext], Awaitable[dict[str, Any]]]
_PROTOCOL_ATTEMPTS = 3
_SUBMISSION_TOOLS = {
    "plan": "submit_recording_plan",
    "repair": "submit_recording_repair",
}


async def _submit_with_protocol_recovery(
    pi: Any,
    *,
    prompt: str,
    accepted_kinds: set[str],
    context: PipelineContext,
) -> None:
    """Keep schema mistakes inside the current operation instead of aborting the run."""

    next_prompt = prompt
    last_error: Exception | None = None
    for attempt in range(1, _PROTOCOL_ATTEMPTS + 1):
        current_flow_spec = getattr(pi, "current_flow_spec", None)
        bind_flow_spec = getattr(pi, "bind_flow_spec", None)
        current = current_flow_spec() if callable(current_flow_spec) else None
        before = current.model_copy(deep=True) if isinstance(current, FlowSpec) else current
        try:
            await pi.prompt(next_prompt)
            context.ensure_active()
            if pi.last_submission_kind in accepted_kinds:
                return
            last_error = RuntimeError(
                "Pi 未通过规定工具提交结果：" + ", ".join(sorted(accepted_kinds))
            )
        except Exception as exc:  # noqa: BLE001 - retry only within this bounded operation
            last_error = exc
        if before is not None and callable(bind_flow_spec):
            bind_flow_spec(before)
        if attempt < _PROTOCOL_ATTEMPTS:
            expected_tools = [
                _SUBMISSION_TOOLS[kind]
                for kind in sorted(accepted_kinds)
                if kind in _SUBMISSION_TOOLS
            ]
            tool_instruction = (
                f"只调用 {expected_tools[0]} 提交当前结果；不得改用其他提交工具。"
                if len(expected_tools) == 1
                else "只调用以下允许的提交工具之一：" + "、".join(expected_tools) + "。"
            )
            next_prompt = (
                "上一次工具提交未完成当前阶段。继续当前任务，不要询问业务用户。"
                f"上一次失败原因：{last_error}。"
                "先读取最新 recording state，只使用工具 schema 声明的字段；"
                "不要增加 evidence、risk_level 或其他未声明字段，也不要提交空 request_refs。"
                + tool_instruction
            )
    raise RuntimeError(f"Pi 连续 {_PROTOCOL_ATTEMPTS} 次未提交有效结构") from last_error


def _workflow_issue(issue: ReleaseIssue, spec: FlowSpec | None = None) -> WorkflowIssue:
    field_label = ""
    if spec is not None and (issue.step_id or issue.field_id or issue.wire_path):
        step = next((item for item in spec.steps if item.step_id == issue.step_id), None)
        param = next((
            item for item in (step.params if step is not None else [])
            if (
                (issue.field_id and str(item.field_id or "") == issue.field_id)
                or (issue.wire_path and str(item.path or "") == issue.wire_path)
            )
        ), None)
        if param is not None:
            field_label = str(param.label or param.key or param.path or "")
    message = issue.message
    if issue.check_code == "required_axis_unconfirmed" and field_label:
        message = f"请确认“{field_label}”在提交申请时是否必须填写。"
    return WorkflowIssue(
        issue_id=issue.issue_id,
        code=issue.check_code,
        message=message,
        resolver=issue.resolver,
        target={
            key: value
            for key, value in {
                "capability_id": issue.capability_id,
                "step_id": issue.step_id,
                "field_id": issue.field_id,
                "wire_path": issue.wire_path,
                "field_label": field_label,
            }.items()
            if value
        },
        evidence=[{"ref": value} for value in issue.evidence_refs],
        allowed_operations=list(issue.suggested_operations),
    )


def _apply_operator_answer(spec: FlowSpec, issue: WorkflowIssue, answer: str) -> bool:
    normalized = answer.strip()
    if issue.code != "required_axis_unconfirmed":
        return False
    if normalized in {"必填", "必须", "required", "yes", "是"}:
        required = True
    elif normalized in {"可选", "选填", "optional", "no", "否"}:
        required = False
    else:
        return False
    step_id = str(issue.target.get("step_id") or "")
    field_id = str(issue.target.get("field_id") or "")
    wire_path = str(issue.target.get("wire_path") or "")
    for step in spec.steps:
        if step_id and step.step_id != step_id:
            continue
        for param in step.params:
            matched = (
                (field_id and str(param.field_id or "") == field_id)
                or (wire_path and str(param.path or "") == wire_path)
            )
            if not matched:
                continue
            param.required = required
            param.source = {
                **(param.source or {}),
                "required_state": "required" if required else "optional",
            }
            return True
    return False


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
            verify=self.verify,
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

    async def verify(
        self,
        draft: dict[str, Any],
        context: PipelineContext,
    ) -> tuple[dict[str, Any], tuple[WorkflowIssue, ...]]:
        context.ensure_active()
        spec = FlowSpec.model_validate(draft)
        spec, report = finalize_verification_state(
            spec,
            rounds=0,
            max_rounds=0,
        )
        if report["todos"]:
            return spec.model_dump(mode="json"), tuple(
                _todo_issue(todo) for todo in report["todos"]
            )
        decision = evaluate_recording_release(spec)
        issues = tuple(
            _workflow_issue(issue, spec)
            for capability in decision.capabilities
            for issue in capability.issues
        )
        return spec.model_dump(mode="json"), issues

    async def repair(
        self,
        draft: dict[str, Any],
        issues: tuple[WorkflowIssue, ...],
        operator_answers: dict[str, str],
        context: PipelineContext,
    ) -> dict[str, Any]:
        context.ensure_active()
        report = RepairReport()
        spec = FlowSpec.model_validate(draft)
        kept_capabilities = list(spec.capabilities)
        try:
            spec = await auto_fix_flow_spec(
                spec,
                repair_ops=[],
                max_rounds=1,
                expand_requests=False,
            )
            report.applied.append("deterministic_fix")
        except Exception:  # noqa: BLE001 - one failed fix must not stop remaining issues
            report.rejected.append("deterministic_fix")

        remaining: list[WorkflowIssue] = []
        for issue in issues:
            answer = str(operator_answers.get(issue.issue_id) or "").strip()
            if not answer or issue.resolver != "operator":
                remaining.append(issue)
                continue
            try:
                if _apply_operator_answer(spec, issue, answer):
                    report.applied.append(issue.issue_id)
                    report.resolved.append(issue.issue_id)
                else:
                    report.rejected.append(issue.issue_id)
                    remaining.append(issue)
            except Exception:  # noqa: BLE001
                report.rejected.append(issue.issue_id)
                remaining.append(issue)

        if remaining:
            try:
                pi = await self.pi_provider(False)
                pi.bind_flow_spec(spec)
                payload = [issue.model_dump(mode="json") for issue in remaining]
                await _submit_with_protocol_recovery(
                    pi,
                    prompt=(
                    "继续同一录制的修复闭环。只按结构化 issue 的 resolver、target、evidence 和 "
                    "allowed_operations 处理剩余问题；不得重建 FlowSpec、清空能力或重新划分能力。"
                    "machine_repair 提交 FlowSpec 修复，collect_evidence 使用回放/页面/字典/依赖工具补证。"
                    "完成后调用 submit_recording_repair。issues="
                    + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    ),
                    accepted_kinds={"repair"},
                    context=context,
                )
                repaired = pi.current_flow_spec()
                if kept_capabilities and not repaired.capabilities:
                    report.rejected.append("pi_cleared_capabilities")
                    report.still_pending.extend(issue.issue_id for issue in remaining)
                else:
                    spec = repaired
                    report.applied.extend(issue.issue_id for issue in remaining)
            except Exception:  # noqa: BLE001 - protocol errors keep the current draft
                report.still_pending.extend(issue.issue_id for issue in remaining)
        else:
            report.skipped.extend(
                issue.issue_id for issue in issues if issue.issue_id not in report.applied
            )

        context.last_repair_report = report
        if kept_capabilities and not spec.capabilities:
            spec.capabilities = kept_capabilities
        return spec.model_dump(mode="json")

    async def publish(
        self,
        draft: dict[str, Any],
        context: PipelineContext,
    ) -> dict[str, Any]:
        context.ensure_active()
        spec = FlowSpec.model_validate(draft)
        if context.machine_verification:
            decision = evaluate_recording_release(spec)
            if decision.callable_spec is None:
                raise RuntimeError("发布边界前能力契约已变化")
            publishable_spec = decision.callable_spec
        else:
            publishable_spec = spec
        verification = {
            "enabled": context.machine_verification,
            "status": "passed" if context.machine_verification else "skipped_by_operator",
        }
        publishable_spec.meta = {
            **(publishable_spec.meta or {}),
            "machine_verification": verification,
        }
        release_spec, candidate = prepare_flow_release_candidate(publishable_spec)
        candidate["machine_verification"] = verification
        context.remember_draft(release_spec.model_dump(mode="json"))
        # Publishing is deterministic after the repair/verification loop has
        # no remaining issues.  Bind the exact frozen candidate to the active
        # session for the database boundary fingerprint check; no model call is
        # made here.
        pi = await self.pi_provider(False)
        pi.bind_flow_spec(release_spec)
        return await self.publisher(release_spec, candidate, context)
