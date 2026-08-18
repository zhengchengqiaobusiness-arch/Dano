"""Production services for the canonical recording pipeline."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dano.execution.page.flow_spec import (
    FlowSpec,
    auto_fix_flow_spec,
    flow_spec_fingerprint,
    prepare_flow_release_candidate,
)
from dano.onboarding.recording_pipeline import RecordingPipelineServices
from dano.onboarding.recording_release import (
    ReleaseIssue,
    evaluate_recording_release,
)
from dano.onboarding.recording_verify import (
    apply_recorded_evidence_fixes,
    finalize_verification_state,
    verification_report,
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
    if issue.code == "field_source_unknown":
        return _apply_source_answer(spec, issue, normalized)
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


def _apply_source_answer(spec: FlowSpec, issue: WorkflowIssue, answer: str) -> bool:
    """Apply an operator answer for a field_source_unknown issue.

    The operator can declare:
    - "用户参数" / "user_input" / "user" → expose as a user-supplied parameter
    - "固定值" / "constant" / "const"   → freeze the recorded value as a constant
    """
    if answer in {"用户参数", "user_input", "user", "用户填写", "是"}:
        new_source_kind = "user_input"
        exposed = True
        editable = True
    elif answer in {"固定值", "constant", "const", "常量", "否"}:
        new_source_kind = "constant"
        exposed = False
        editable = True
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
            param.source_kind = new_source_kind
            param.exposed_to_user = exposed
            param.editable = editable
            param.source = {
                **(param.source or {}),
                "kind": new_source_kind,
                "operator_confirmed": True,
            }
            return True
    return False


def _default_todo_message(todo: dict[str, Any], code: str) -> str:
    if code == "write_verify":
        target = str(todo.get("target_id") or todo.get("write_request_id") or "写操作")
        return f"写操作 `{target}` 还没有回读校验，不能证明提交已生效"
    if code == "enum":
        path = str(todo.get("path") or todo.get("target_id") or "选项字段")
        return f"字段 `{path}` 的枚举选项还不完整"
    if code == "dependency":
        path = str(todo.get("target_path") or todo.get("target_id") or "依赖字段")
        return f"字段 `{path}` 的取值依赖还没有验证"
    return f"待处理：{code}"


def _todo_issue(todo: dict[str, Any], spec: FlowSpec | None = None) -> WorkflowIssue:
    resolver = str(todo.get("resolver") or "collect_evidence")
    if resolver not in {"machine_repair", "collect_evidence", "operator", "external_blocked"}:
        resolver = "collect_evidence"
    issue_id = str(todo.get("issue_id") or todo.get("target_id") or "")
    code = str(todo.get("check_code") or todo.get("kind") or "verification_pending")
    field_label = str(todo.get("field_label") or "")
    wire_path = str(todo.get("wire_path") or todo.get("path") or "")
    step_id = str(todo.get("step_id") or todo.get("target_step_id") or "")
    if spec is not None and not field_label and (step_id or wire_path):
        step = next((item for item in spec.steps if item.step_id == step_id), None)
        param = next((
            item for item in (step.params if step is not None else [])
            if str(item.path or "") == wire_path or str(item.field_id or "") == str(todo.get("field_id") or "")
        ), None)
        if param is not None:
            field_label = str(param.label or param.key or param.path or "")
    target = {
        key: str(todo.get(key) or "")
        for key in ("capability_id", "step_id", "field_id", "wire_path", "target_id", "path")
        if todo.get(key)
    }
    if wire_path:
        target["wire_path"] = wire_path
    if field_label:
        target["field_label"] = field_label
    if step_id:
        target["step_id"] = step_id
    return WorkflowIssue(
        issue_id=issue_id or f"{code}:{step_id or wire_path or 'draft'}",
        code=code,
        message=str(todo.get("message") or _default_todo_message(todo, code)),
        resolver=resolver,
        target=target,
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


def _issue_field_identity(issue: WorkflowIssue) -> tuple[str, str, str]:
    family = "enum" if issue.code in {"enum", "enum_options_unverified"} else issue.code
    if family == "field_source_unknown":
        family = "source"
    return (
        family,
        str(issue.target.get("step_id") or ""),
        str(issue.target.get("wire_path") or issue.target.get("path") or "").removeprefix("body."),
    )


def _dedupe_workflow_issues(issues: tuple[WorkflowIssue, ...]) -> tuple[WorkflowIssue, ...]:
    """Keep one issue per field/problem so the same leaf cannot tell two stories."""
    kept: dict[tuple[str, str, str], WorkflowIssue] = {}
    order: list[tuple[str, str, str]] = []
    for issue in issues:
        key = _issue_field_identity(issue)
        if key[0] == "source":
            enum_key = ("enum", key[1], key[2])
            if enum_key in kept:
                continue
        if key in kept:
            continue
        if key[0] == "enum":
            source_key = ("source", key[1], key[2])
            kept.pop(source_key, None)
            if source_key in order:
                order.remove(source_key)
        kept[key] = issue
        order.append(key)
    return tuple(kept[key] for key in order if key in kept)


_EVIDENCE_RESOLVABLE_CODES = frozenset({
    "field_source_unknown",
    "enum",
    "enum_options_unverified",
    "required_axis_unconfirmed",
})


def _issue_still_open(spec: FlowSpec, issue: WorkflowIssue) -> bool:
    report = verification_report(spec)
    todos = [item for item in report.get("todos") or [] if isinstance(item, dict)]
    tokens = {
        str(item.get("issue_id") or item.get("target_id") or "")
        for item in todos
    }
    if issue.issue_id in tokens:
        return True
    identity = _issue_field_identity(issue)
    if any(_issue_field_identity(_todo_issue(item, spec)) == identity for item in todos):
        return True
    return issue.code not in _EVIDENCE_RESOLVABLE_CODES


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
        spec = apply_recorded_evidence_fixes(FlowSpec.model_validate(draft))
        spec, report = finalize_verification_state(
            spec,
            rounds=0,
            max_rounds=0,
        )
        if report["todos"]:
            return spec.model_dump(mode="json"), _dedupe_workflow_issues(tuple(
                _todo_issue(todo, spec) for todo in report["todos"]
            ))
        decision = evaluate_recording_release(spec)
        issues = _dedupe_workflow_issues(tuple(
            _workflow_issue(issue, spec)
            for capability in decision.capabilities
            for issue in capability.issues
        ))
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
        spec = apply_recorded_evidence_fixes(FlowSpec.model_validate(draft))
        kept_capabilities = list(spec.capabilities)
        try:
            before_fix = flow_spec_fingerprint(spec)
            spec = await auto_fix_flow_spec(
                spec,
                repair_ops=[],
                max_rounds=1,
                expand_requests=False,
            )
            spec = apply_recorded_evidence_fixes(spec)
            if flow_spec_fingerprint(spec) != before_fix:
                report.applied.append("deterministic_fix")
            else:
                report.skipped.append("deterministic_fix")
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

        still_open: list[WorkflowIssue] = []
        for issue in remaining:
            if _issue_still_open(spec, issue):
                still_open.append(issue)
                continue
            report.applied.append(issue.issue_id)
            report.resolved.append(issue.issue_id)
        remaining = still_open

        if remaining:
            try:
                pi = await self.pi_provider(False)
                pi.bind_flow_spec(spec)
                before_pi = flow_spec_fingerprint(spec)
                payload = [issue.model_dump(mode="json") for issue in remaining]
                await _submit_with_protocol_recovery(
                    pi,
                    prompt=(
                    "继续同一录制的修复闭环。先用中文逐句说出：发现了什么不对、我觉得应该怎样处理、"
                    "准备调用哪个工具、为什么。不要只重复 issue 代码。"
                    "先调用 get_validation_report 读取当前待办；不要一上来就调用 get_recording_state。"
                    "只按结构化 issue 的 resolver、target、evidence 和 "
                    "allowed_operations 处理剩余问题；不得重建 FlowSpec、清空能力或重新划分能力。"
                    "write_verify 用 execute_write_with_verify，读请求必须来自 issue 里已有的 "
                    "candidate_read_request_ids 或 validation report；不要打开浏览器重录。"
                    "machine_repair 提交 FlowSpec 修复，collect_evidence 使用回放/字典/依赖工具补证。"
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
                elif flow_spec_fingerprint(repaired) == before_pi:
                    report.skipped.append("pi_repair")
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
