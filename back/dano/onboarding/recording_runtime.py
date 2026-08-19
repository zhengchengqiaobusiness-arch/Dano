"""Production services for the canonical recording pipeline."""

from __future__ import annotations

import asyncio
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
from dano.onboarding.recording_stage_seven import (
    AUTH_BLOCKED_MESSAGE,
    NETWORK_BLOCKED_MESSAGE,
    STAGE_SEVEN_PROTOCOL,
    STAGE_SIX_CONTRACT_CHANGED,
    StageSevenPreflightStatus,
    StageSevenStatus,
    StageSixContractChanged,
    assert_stage_six_contract_preserved,
    build_stage_seven_scope,
    checkpoint_dict,
    classify_preflight_error,
    compute_publishable,
    callable_covers_stage_six,
    evaluate_stage_seven_verdict,
    new_attempt_id,
    normalize_stage_seven_working_copy,
    preflight_status_of,
    redact_external_blocker_text,
    working_fingerprint,
)
from dano.onboarding.recording_verify import (
    FLOW_GROUP_KEY,
    assign_unassigned_internal_steps,
    finalize_verification_state,
    machine_repair_disposition,
    select_preflight_probe,
    replay_auth_failed,
    verification_report,
)
from dano.onboarding.recording_workflow import (
    PipelineContext,
    RepairReport,
    WorkflowActivity,
    WorkflowCancelled,
    WorkflowIssue,
    WorkflowStep,
    _capability_feedback_label,
    _capability_intro_label,
    _discovery_thought,
    _issue_detail_label,
    _operator_question,
)


PiProvider = Callable[[bool], Awaitable[Any]]
Materializer = Callable[[bool, PipelineContext], Awaitable[FlowSpec]]
Publisher = Callable[[FlowSpec, dict[str, Any], PipelineContext], Awaitable[dict[str, Any]]]
ReplayExecutor = Callable[[dict[str, Any], FlowSpec], Awaitable[dict[str, Any]]]
TokenRefresher = Callable[..., Awaitable[dict[str, Any]]]
_CAPABILITY_REPAIR_BUDGET = 2
PREFLIGHT_TIMEOUT_S = 8.0
PREFLIGHT_PROGRESS_LABEL = "正在检查回放登录态"
PREFLIGHT_REFRESH_LABEL = "登录态已过期，正在自动刷新凭证"
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
    allowed_operations = [
        str(value)
        for value in (
            todo.get("suggested_operations")
            or todo.get("completion_ops")
            or [todo.get("completion_op") or todo.get("suggested_tool")]
        )
        if value
    ]
    if resolver == "machine_repair" and machine_repair_disposition(code) != "skill":
        allowed_operations = []
    return WorkflowIssue(
        issue_id=issue_id or f"{code}:{step_id or wire_path or 'draft'}",
        code=code,
        message=str(todo.get("message") or _default_todo_message(todo, code)),
        resolver=resolver,
        target=target,
        evidence=[dict(todo)],
        allowed_operations=allowed_operations,
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


def _open_issue_tokens(spec: FlowSpec) -> tuple[set[str], set[tuple[str, str, str]]]:
    """Compute the currently open verification todos once per draft state."""
    report = verification_report(spec)
    todos = [item for item in report.get("todos") or [] if isinstance(item, dict)]
    ids = {
        str(item.get("issue_id") or item.get("target_id") or "")
        for item in todos
    }
    identities = {_issue_field_identity(_todo_issue(item, spec)) for item in todos}
    return ids, identities


def _issue_still_open(spec: FlowSpec, issue: WorkflowIssue) -> bool:
    ids, identities = _open_issue_tokens(spec)
    return issue.issue_id in ids or _issue_field_identity(issue) in identities


def _issue_capability_id(spec: FlowSpec, issue: WorkflowIssue) -> str:
    """Resolve which stage-six capability owns an issue, or "" for flow-level."""
    explicit = str(issue.target.get("capability_id") or "")
    if explicit:
        return explicit
    step_id = str(issue.target.get("step_id") or "")
    if not step_id:
        return ""
    for capability in spec.capabilities:
        if step_id in {str(item) for item in capability.step_ids}:
            return capability.capability_id
        if any(
            isinstance(node, dict) and str(node.get("step_id") or "") == step_id
            for node in capability.nodes
        ):
            return capability.capability_id
    return ""


def _session_level_issue(issue: WorkflowIssue) -> bool:
    if issue.code == "replay_auth":
        return True
    return str(issue.target.get("kind") or "") == "session"


def _stamp_issue_capability(spec: FlowSpec, issue: WorkflowIssue) -> WorkflowIssue:
    capability_id = _issue_capability_id(spec, issue)
    if not capability_id or str(issue.target.get("capability_id") or "") == capability_id:
        return issue
    return issue.model_copy(update={"target": {**issue.target, "capability_id": capability_id}})


def _group_issues_by_capability(
    spec: FlowSpec,
    issues: tuple[WorkflowIssue, ...],
) -> list[tuple[Any, tuple[WorkflowIssue, ...]]]:
    """Order repair work by the stage-six capability list; flow-level work last."""
    known = {capability.capability_id for capability in spec.capabilities}
    grouped: dict[str, list[WorkflowIssue]] = {}
    for issue in issues:
        capability_id = _issue_capability_id(spec, issue)
        if capability_id not in known:
            capability_id = ""
        grouped.setdefault(capability_id, []).append(issue)
    ordered: list[tuple[Any, tuple[WorkflowIssue, ...]]] = []
    for capability in spec.capabilities:
        members = grouped.pop(capability.capability_id, None)
        if members:
            ordered.append((capability, tuple(members)))
    leftovers = [
        issue
        for members in grouped.values()
        for issue in members
        if not _session_level_issue(issue)
    ]
    if leftovers:
        ordered.append((None, tuple(leftovers)))
    return ordered


def _capability_brief(spec: FlowSpec, capability) -> dict[str, Any]:  # noqa: ANN001
    """Project the stage-six contract of one capability for the repair prompt."""
    steps_by_id = {step.step_id: step for step in spec.steps}
    member_ids = [str(item) for item in capability.step_ids if item]
    members = [
        {
            "step_id": step_id,
            "method": steps_by_id[step_id].method,
            "path": steps_by_id[step_id].path,
        }
        for step_id in member_ids
        if step_id in steps_by_id
    ]
    anchor = next(
        (
            {
                key: node.get(key)
                for key in ("step_id", "request_id", "method", "path")
                if node.get(key)
            }
            for node in capability.nodes
            if isinstance(node, dict) and str(node.get("usage") or "") == "execute"
        ),
        {},
    )
    member_set = set(member_ids)
    dependencies = [
        {
            "link_id": link.link_id,
            "source_step_id": link.source_step_id,
            "source_path": link.source_path,
            "target_step_id": link.target_step_id,
            "target_path": link.target_path,
            "confirmed": bool(link.confirmed),
        }
        for link in spec.links
        if link.source_step_id in member_set or link.target_step_id in member_set
    ]
    return {
        "capability_id": capability.capability_id,
        "name": capability.name,
        "title": capability.title,
        "kind": capability.kind,
        "anchor": anchor,
        "steps": members,
        "dependencies": dependencies[:20],
    }


def _capability_repair_prompt(
    *,
    capability,  # noqa: ANN001
    brief: dict[str, Any],
    issues: tuple[WorkflowIssue, ...],
    index: int,
    total: int,
) -> str:
    payload = [issue.model_dump(mode="json") for issue in issues]
    if capability is not None:
        scope = (
            f"本轮只处理能力「{capability.title or capability.name}」（{capability.name}）"
            f"的问题，这是第 {index}/{total} 组；"
            "其他能力的问题会在后续组单独处理，不要顺手处理。"
        )
        anchor_text = (
            "该能力在阶段六编译出的契约（执行锚点、成员步骤、相关依赖）如下，"
            "修复必须与它衔接，不得脱离这些事实：capability="
            + json.dumps(brief, ensure_ascii=False, separators=(",", ":"))
        )
    else:
        scope = f"本轮处理不属于单个能力的整体流程问题，这是第 {index}/{total} 组。"
        anchor_text = ""
    return (
        "继续同一录制的修复闭环。" + scope
        + "先用中文逐句说出：发现了什么不对、我觉得应该怎样处理、准备调用哪个工具、为什么。"
        "不要只重复 issue 代码。"
        "先调用 get_validation_report 读取当前待办；不要一上来就调用 get_recording_state。"
        + anchor_text
        + "只按结构化 issue 的 resolver、target、evidence 和 allowed_operations 处理本组问题；"
        "不得重建 FlowSpec、清空能力或重新划分能力。"
        "write_verify 用 execute_write_with_verify，读请求必须来自 issue 里已有的 "
        "candidate_read_request_ids 或 validation report；不要打开浏览器重录。"
        "machine_repair 提交 FlowSpec 修复，collect_evidence 使用回放/字典/依赖工具补证。"
        "完成后调用 submit_recording_repair 一次性提交本组全部修复。issues="
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _dispatchable_issue(issue: WorkflowIssue) -> bool:
    if issue.resolver == "machine_repair":
        return machine_repair_disposition(issue.code) == "skill"
    return issue.resolver in {"collect_evidence", "operator"}


def _record_capability_verification(
    spec: FlowSpec,
    group_key: str,
    *,
    status: str,
    resolved: list[str],
    pending: list[str],
    reason: str,
) -> None:
    existing = dict((spec.meta or {}).get("capability_verification") or {})
    existing[group_key] = {
        "status": status,
        "resolved": list(resolved),
        "pending": list(pending),
        "reason": reason,
    }
    spec.meta = {**(spec.meta or {}), "capability_verification": existing}


def _external_preflight_issue(
    status: StageSevenPreflightStatus,
    preflight: dict[str, Any],
) -> WorkflowIssue:
    if status == StageSevenPreflightStatus.BLOCKED_AUTH:
        message = AUTH_BLOCKED_MESSAGE
        code = "replay_auth"
    else:
        message = NETWORK_BLOCKED_MESSAGE
        code = "replay_network"
    error = redact_external_blocker_text(str(preflight.get("error") or ""))
    if error and error not in message:
        message = f"{message}（{error}）"
    return WorkflowIssue(
        issue_id=f"preflight:{status.value}",
        code=code,
        message=message,
        resolver="external_blocked",
        target={"kind": "session", "preflight": status.value},
    )


async def _persist_stage_seven_if_needed(
    context: PipelineContext,
    baseline: FlowSpec,
    spec: FlowSpec,
    verdict,
    preflight: dict[str, Any],
) -> None:
    saver = context.persist_stage_seven
    if saver is None:
        return
    checkpoint = checkpoint_dict(
        attempt_id=context.stage_seven_attempt_id or new_attempt_id(),
        revision=context.stage_seven_revision,
        status=verdict.status if verdict is not None else StageSevenStatus.RUNNING,
        baseline=baseline,
        working=spec,
        verdict=verdict,
        preflight=preflight,
        capability_attempts=dict(context.capability_rounds),
        operator_answers=dict(context.operator_answers),
    )
    try:
        await saver(checkpoint)
        context.stage_seven_revision = int(checkpoint.get("revision") or 0)
    except Exception:  # noqa: BLE001 - checkpoint must not abort verification
        return


def _preflight_from_spec(spec: FlowSpec) -> dict[str, Any]:
    return dict(((spec.meta or {}).get("verification_run") or {}).get("preflight") or {})


async def _persist_working_copy(
    context: PipelineContext,
    baseline: FlowSpec,
    spec: FlowSpec,
) -> None:
    await _persist_stage_seven_if_needed(
        context,
        baseline,
        spec,
        context.stage_seven_verdict,
        _preflight_from_spec(spec),
    )


def _issues_from_spec(spec: FlowSpec) -> tuple[WorkflowIssue, ...]:
    report = verification_report(spec)
    issues = _dedupe_workflow_issues(tuple(
        _stamp_issue_capability(spec, _todo_issue(todo, spec))
        for todo in report.get("todos") or []
    ))
    if issues:
        return issues
    decision = evaluate_recording_release(spec)
    return _dedupe_workflow_issues(tuple(
        _stamp_issue_capability(spec, _workflow_issue(issue, spec))
        for capability in decision.capabilities
        for issue in capability.issues
    ))


async def _default_replay_executor(request: dict[str, Any], spec: FlowSpec) -> dict[str, Any]:
    from dano.execution.page.replay import replay_request
    from dano.execution.page.request_capture import extract_auth_headers
    from dano.execution.page.sessions import load_session_state
    from dano.infra.token_store import get_token_headers, runtime_replay_auth

    captured = extract_auth_headers(request.get("headers"))
    runtime = await get_token_headers(spec.tenant, spec.subsystem)
    headers, storage_state = runtime_replay_auth(
        captured,
        runtime,
        load_session_state(spec.tenant, spec.subsystem),
    )
    return await replay_request(
        request,
        auth_headers=headers,
        storage_state=storage_state,
    )


@dataclass
class ProductionRecordingServices:
    """Bridge stable FlowSpec/Pi modules into the one workflow runtime."""

    recording_id: str
    materializer: Materializer
    pi_provider: PiProvider
    publisher: Publisher
    replay_executor: ReplayExecutor | None = None
    token_refresher: TokenRefresher | None = None

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
        await context.progress(WorkflowStep.VERIFYING, PREFLIGHT_PROGRESS_LABEL, 0)
        await context.record(WorkflowActivity(
            step=WorkflowStep.VERIFYING,
            round=0,
            status="running",
            label=PREFLIGHT_PROGRESS_LABEL,
        ))
        baseline = FlowSpec.model_validate(context.stage_six_baseline or draft)
        spec = FlowSpec.model_validate(draft)
        if not context.stage_seven_attempt_id:
            context.stage_seven_attempt_id = new_attempt_id()
        spec = normalize_stage_seven_working_copy(baseline, spec)
        spec, _report = finalize_verification_state(
            spec,
            rounds=context.current_round,
            max_rounds=0,
        )
        spec = assign_unassigned_internal_steps(spec)
        contract_changed = False
        try:
            assert_stage_six_contract_preserved(baseline, spec)
        except StageSixContractChanged:
            spec = baseline.model_copy(deep=True)
            spec.meta = {
                **(spec.meta or {}),
                "stage_seven_contract_changed": True,
            }
            contract_changed = True
        preflight = await self._preflight_replay_health(spec, context)
        preflight_status = preflight_status_of(preflight)
        if preflight_status == StageSevenPreflightStatus.BLOCKED_AUTH:
            await context.progress(WorkflowStep.VERIFYING, AUTH_BLOCKED_MESSAGE, 0)
        elif preflight_status == StageSevenPreflightStatus.BLOCKED_NETWORK:
            await context.progress(WorkflowStep.VERIFYING, NETWORK_BLOCKED_MESSAGE, 0)
        elif preflight_status == StageSevenPreflightStatus.REFRESHED:
            await context.progress(WorkflowStep.VERIFYING, "凭证已刷新，开始检查能力", 0)
        elif preflight_status != StageSevenPreflightStatus.NOT_APPLICABLE:
            await context.progress(WorkflowStep.VERIFYING, "回放登录态正常，开始检查能力", 0)
        verification_run = dict((spec.meta or {}).get("verification_run") or {})
        verification_run["preflight"] = preflight
        spec.meta = {
            **(spec.meta or {}),
            "verification_run": verification_run,
            "stage_seven": {
                **dict((spec.meta or {}).get("stage_seven") or {}),
                "protocol": STAGE_SEVEN_PROTOCOL,
                "attempt_id": context.stage_seven_attempt_id,
                "revision": context.stage_seven_revision,
            },
        }
        issues = list(_issues_from_spec(spec))
        if contract_changed:
            contract_issue = WorkflowIssue(
                issue_id=STAGE_SIX_CONTRACT_CHANGED,
                code=STAGE_SIX_CONTRACT_CHANGED,
                message="阶段 7 改变了阶段 6 公开能力契约，已回滚该修补并阻塞发布",
                resolver="machine_repair",
            )
            issues.insert(0, contract_issue)
        if preflight_status in {
            StageSevenPreflightStatus.BLOCKED_AUTH,
            StageSevenPreflightStatus.BLOCKED_NETWORK,
        }:
            blocked = _external_preflight_issue(preflight_status, preflight)
            if not any(issue.issue_id == blocked.issue_id for issue in issues):
                issues.insert(0, blocked)
            await context.record(WorkflowActivity(
                step=WorkflowStep.VERIFYING,
                round=0,
                status="blocked",
                label=blocked.message,
            ))
        report = verification_report(spec)
        scope = build_stage_seven_scope(spec, baseline=baseline)
        decision = evaluate_recording_release(spec)
        verdict = evaluate_stage_seven_verdict(
            baseline=baseline,
            working=spec,
            scope=scope,
            verification_report=report,
            preflight=preflight,
            release=decision,
            attempt_id=context.stage_seven_attempt_id,
            revision=context.stage_seven_revision,
            waiting_operator=any(issue.resolver == "operator" for issue in issues),
            budget_or_stall=context.budget_exhausted or contract_changed,
            running=True,
            extra_issues=(
                [{
                    "issue_id": STAGE_SIX_CONTRACT_CHANGED,
                    "check_code": STAGE_SIX_CONTRACT_CHANGED,
                    "message": "阶段 7 改变了阶段 6 公开能力契约，已回滚该修补并阻塞发布",
                    "resolver": "machine_repair",
                }]
                if contract_changed else None
            ),
        )
        context.stage_seven_verdict = verdict
        spec.meta = {
            **(spec.meta or {}),
            "stage_seven": {
                **dict((spec.meta or {}).get("stage_seven") or {}),
                **verdict.to_dict(),
            },
        }
        await _persist_stage_seven_if_needed(context, baseline, spec, verdict, preflight)
        return spec.model_dump(mode="json"), tuple(issues)

    async def _refresh_runtime_token(self, tenant: str, subsystem: str) -> dict[str, Any]:
        refresher = self.token_refresher
        if refresher is None:
            from dano.infra.token_refresh import refresh_one
            refresher = refresh_one
        try:
            result = dict(await refresher(tenant, subsystem, force=True) or {})
        except Exception as exc:  # noqa: BLE001 - keep the original auth failure
            return {"ok": False, "status": "error", "reason": type(exc).__name__}
        if result.get("status") == "refreshed":
            return result
        return {
            **result,
            "ok": False,
            "status": str(result.get("status") or result.get("reason") or "failed"),
        }

    async def _run_preflight_probe(
        self,
        probe: dict[str, Any],
        spec: FlowSpec,
    ) -> dict[str, Any]:
        executor = self.replay_executor or _default_replay_executor
        try:
            result = await asyncio.wait_for(executor(probe, spec), timeout=PREFLIGHT_TIMEOUT_S)
        except TimeoutError:
            return {
                "ok": False,
                "auth_failed": False,
                "blocked_network": True,
                "status": StageSevenPreflightStatus.BLOCKED_NETWORK.value,
                "error": "TimeoutError",
                "path": str(probe.get("path") or probe.get("url") or ""),
            }
        except Exception as exc:  # noqa: BLE001 - classify network failures as blockers
            status = classify_preflight_error(exc)
            return {
                "ok": False,
                "auth_failed": False,
                "blocked_network": status == StageSevenPreflightStatus.BLOCKED_NETWORK,
                "status": status.value,
                "error": type(exc).__name__,
                "path": str(probe.get("path") or probe.get("url") or ""),
            }
        status = result.get("status")
        body = result.get("response") if "response" in result else result.get("body")
        failed = replay_auth_failed(status, body)
        return {
            "ok": not failed,
            "auth_failed": failed,
            "status": (
                StageSevenPreflightStatus.BLOCKED_AUTH.value
                if failed
                else StageSevenPreflightStatus.HEALTHY.value
            ),
            "http_status": status,
            "path": str(probe.get("path") or probe.get("url") or ""),
            "request_id": str(probe.get("request_id") or ""),
        }

    async def _preflight_replay_health(
        self,
        spec: FlowSpec,
        context: PipelineContext,
    ) -> dict[str, Any]:
        probe = select_preflight_probe(spec)
        if probe is None:
            return {"skipped": True, "ok": True, "status": StageSevenPreflightStatus.NOT_APPLICABLE.value}
        first = await self._run_preflight_probe(probe, spec)
        if first.get("blocked_network") or first.get("error"):
            return {
                **first,
                "status": StageSevenPreflightStatus.BLOCKED_NETWORK.value,
                "blocked_network": True,
                "ok": False,
            }
        if first.get("skipped") or not first.get("auth_failed"):
            return {
                **first,
                "status": first.get("status") or StageSevenPreflightStatus.HEALTHY.value,
            }
        await context.progress(WorkflowStep.VERIFYING, PREFLIGHT_REFRESH_LABEL, 0)
        await context.record(WorkflowActivity(
            step=WorkflowStep.VERIFYING,
            round=0,
            status="running",
            label=PREFLIGHT_REFRESH_LABEL,
        ))
        refresh = await self._refresh_runtime_token(spec.tenant, spec.subsystem)
        if refresh.get("status") != "refreshed":
            return {
                **first,
                "ok": False,
                "auth_failed": True,
                "blocked_auth": True,
                "status": StageSevenPreflightStatus.BLOCKED_AUTH.value,
                "refreshed": False,
                "refresh_status": refresh.get("status") or refresh.get("reason") or "failed",
            }
        await context.record(WorkflowActivity(
            step=WorkflowStep.VERIFYING,
            round=0,
            status="running",
            label="凭证已刷新，正在重新检查登录态",
        ))
        retry = await self._run_preflight_probe(probe, spec)
        if retry.get("blocked_network") or retry.get("error"):
            return {
                **retry,
                "ok": False,
                "blocked_network": True,
                "status": StageSevenPreflightStatus.BLOCKED_NETWORK.value,
                "refreshed": True,
                "refresh_status": "refreshed",
            }
        if retry.get("auth_failed"):
            return {
                **retry,
                "ok": False,
                "auth_failed": True,
                "blocked_auth": True,
                "status": StageSevenPreflightStatus.BLOCKED_AUTH.value,
                "refreshed": True,
                "refresh_status": "refreshed",
            }
        return {
            **retry,
            "ok": True,
            "refreshed": True,
            "refresh_status": "refreshed",
            "status": StageSevenPreflightStatus.REFRESHED.value,
        }

    async def repair(
        self,
        draft: dict[str, Any],
        issues: tuple[WorkflowIssue, ...],
        operator_answers: dict[str, str],
        context: PipelineContext,
    ) -> dict[str, Any]:
        context.ensure_active()
        report = RepairReport()
        baseline = FlowSpec.model_validate(context.stage_six_baseline or draft)
        spec = normalize_stage_seven_working_copy(baseline, FlowSpec.model_validate(draft))
        kept_capabilities = list(spec.capabilities)
        try:
            before_fix = flow_spec_fingerprint(spec)
            spec = await auto_fix_flow_spec(
                spec,
                repair_ops=[],
                max_rounds=1,
                expand_requests=False,
            )
            spec = normalize_stage_seven_working_copy(baseline, spec)
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
        open_ids, open_identities = _open_issue_tokens(spec)
        for issue in remaining:
            if issue.issue_id in open_ids or _issue_field_identity(issue) in open_identities:
                still_open.append(issue)
                continue
            report.applied.append(issue.issue_id)
            report.resolved.append(issue.issue_id)
        remaining = still_open
        if remaining:
            try:
                spec = await self._repair_capability_groups(
                    spec,
                    tuple(remaining),
                    kept_capabilities,
                    report,
                    context,
                    baseline=baseline,
                    operator_answers=operator_answers,
                )
            except WorkflowCancelled:
                raise
            except Exception:  # noqa: BLE001 - protocol errors keep the current draft
                report.still_pending.extend(issue.issue_id for issue in remaining)
        else:
            report.skipped.extend(
                issue.issue_id for issue in issues if issue.issue_id not in report.applied
            )

        context.last_repair_report = report
        if kept_capabilities and not spec.capabilities:
            spec.capabilities = kept_capabilities
        context.operator_answers.update(operator_answers)
        spec = normalize_stage_seven_working_copy(baseline, spec)
        await _persist_working_copy(context, baseline, spec)
        return spec.model_dump(mode="json")

    async def _repair_capability_groups(
        self,
        spec: FlowSpec,
        remaining: tuple[WorkflowIssue, ...],
        kept_capabilities: list[Any],
        report: RepairReport,
        context: PipelineContext,
        *,
        baseline: FlowSpec,
        operator_answers: dict[str, str] | None = None,
    ) -> FlowSpec:
        """List each capability's problems, apply that group's edits, then feedback.

        The next capability starts only after the current group's feedback.
        """
        answers = dict(operator_answers or {})
        groups = _group_issues_by_capability(spec, remaining)
        total = len(groups)
        pi: Any = None
        for index, (capability, group_issues) in enumerate(groups, start=1):
            context.ensure_active()
            group_key = (
                capability.capability_id
                if capability is not None
                else FLOW_GROUP_KEY
            )
            title = (
                str(capability.title or capability.name)
                if capability is not None
                else "整体流程"
            )
            cap_target = {"capability_id": group_key, "capability_title": title}
            last = index == total
            await context.progress(
                WorkflowStep.RESOLVING,
                f"正在处理能力「{title}」（{index}/{total}）",
                context.current_round,
            )
            await context.record(WorkflowActivity(
                step=WorkflowStep.RESOLVING,
                round=context.current_round,
                status="running",
                label=_capability_intro_label(
                    title, index=index, total=total, issue_count=len(group_issues),
                ),
                target=cap_target,
            ))
            for issue_index, issue in enumerate(group_issues, start=1):
                await context.record(WorkflowActivity(
                    step=WorkflowStep.RESOLVING,
                    round=context.current_round,
                    status="pending",
                    label=_issue_detail_label(
                        issue, index=issue_index, total=len(group_issues),
                    ),
                    issue_id=issue.issue_id,
                    code=issue.code,
                    target={**issue.target, **cap_target},
                ))
            dispatched = int((context.capability_rounds or {}).get(group_key) or 0)
            if dispatched >= _CAPABILITY_REPAIR_BUDGET:
                context.budget_exhausted = True
                report.still_pending.extend(issue.issue_id for issue in group_issues)
                _record_capability_verification(
                    spec,
                    group_key,
                    status="incomplete",
                    resolved=[],
                    pending=[issue.issue_id for issue in group_issues],
                    reason="能力修复轮次已用尽",
                )
                await context.record(WorkflowActivity(
                    step=WorkflowStep.RESOLVING,
                    round=context.current_round,
                    status="blocked",
                    label=f"能力「{title}」已用尽 {_CAPABILITY_REPAIR_BUDGET} 轮修复预算，问题仍保留",
                    target=cap_target,
                ))
                await context.record(WorkflowActivity(
                    step=WorkflowStep.RESOLVING,
                    round=context.current_round,
                    status="blocked",
                    label=_capability_feedback_label(
                        title=title,
                        resolved=(),
                        pending=group_issues,
                        last=last,
                    ),
                    target=cap_target,
                ))
                await _persist_working_copy(context, baseline, spec)
                continue
            context.capability_rounds[group_key] = dispatched + 1
            remaining_group: list[WorkflowIssue] = []
            operator_resolved: list[WorkflowIssue] = []
            for issue in group_issues:
                if issue.resolver != "operator":
                    remaining_group.append(issue)
                    continue
                answer = str(answers.get(issue.issue_id) or "").strip()
                if not answer:
                    await context.record(WorkflowActivity(
                        step=WorkflowStep.RESOLVING,
                        round=context.current_round,
                        status="waiting_operator",
                        label=_discovery_thought(issue) + " 需要你确认后我才能继续。",
                        issue_id=issue.issue_id,
                        code=issue.code,
                        target={**issue.target, **cap_target},
                    ))
                    await _persist_working_copy(context, baseline, spec)
                    answer = str(await context.ask_operator(_operator_question(issue)) or "").strip()
                    answers[issue.issue_id] = answer
                applied = False
                if answer:
                    try:
                        applied = _apply_operator_answer(spec, issue, answer)
                    except Exception:  # noqa: BLE001
                        applied = False
                if applied:
                    report.applied.append(issue.issue_id)
                    report.resolved.append(issue.issue_id)
                    operator_resolved.append(issue)
                else:
                    if answer:
                        report.rejected.append(issue.issue_id)
                    remaining_group.append(issue)
            if operator_resolved:
                spec = normalize_stage_seven_working_copy(baseline, spec)
                await _persist_working_copy(context, baseline, spec)
            pi_issues = tuple(issue for issue in remaining_group if _dispatchable_issue(issue))
            before_pi = flow_spec_fingerprint(spec)
            pre_pi_spec = spec.model_copy(deep=True)
            if pi_issues:
                if pi is None:
                    pi = await self.pi_provider(False)
                bind_sink = getattr(pi, "bind_stage_seven_evidence_sink", None)
                if callable(bind_sink):
                    bind_sink(
                        baseline=baseline,
                        on_evidence=lambda working: _persist_working_copy(
                            context,
                            baseline,
                            working,
                        ),
                    )
                pi.bind_flow_spec(spec)
                await context.record(WorkflowActivity(
                    step=WorkflowStep.RESOLVING,
                    round=context.current_round,
                    status="running",
                    label=f"开始修改能力「{title}」",
                    target=cap_target,
                ))
                try:
                    await _submit_with_protocol_recovery(
                        pi,
                        prompt=_capability_repair_prompt(
                            capability=capability,
                            brief=(
                                _capability_brief(spec, capability)
                                if capability is not None
                                else {}
                            ),
                            issues=pi_issues,
                            index=index,
                            total=total,
                        ),
                        accepted_kinds={"repair"},
                        context=context,
                    )
                except WorkflowCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - one capability must not sink the rest
                    harvested = None
                    try:
                        harvested = pi.current_flow_spec()
                    except Exception:  # noqa: BLE001 - keep the pre-Pi spec if harvest fails
                        harvested = None
                    if harvested is not None:
                        current = normalize_stage_seven_working_copy(baseline, harvested)
                        try:
                            assert_stage_six_contract_preserved(baseline, current)
                            spec = current
                            pi.bind_flow_spec(spec)
                        except StageSixContractChanged:
                            spec = pre_pi_spec
                            pi.bind_flow_spec(spec)
                    await _persist_working_copy(context, baseline, spec)
                    report.still_pending.extend(issue.issue_id for issue in remaining_group)
                    _record_capability_verification(
                        spec,
                        group_key,
                        status="blocked",
                        resolved=[issue.issue_id for issue in operator_resolved],
                        pending=[issue.issue_id for issue in remaining_group],
                        reason=str(exc),
                    )
                    await context.record(WorkflowActivity(
                        step=WorkflowStep.RESOLVING,
                        round=context.current_round,
                        status="running",
                        label=f"能力「{title}」本组修复未落地：{exc}",
                        target=cap_target,
                    ))
                    await context.record(WorkflowActivity(
                        step=WorkflowStep.RESOLVING,
                        round=context.current_round,
                        status="blocked",
                        label=_capability_feedback_label(
                            title=title,
                            resolved=tuple(operator_resolved),
                            pending=tuple(remaining_group),
                            last=last,
                        ),
                        target=cap_target,
                    ))
                    continue
                repaired = pi.current_flow_spec()
                if kept_capabilities and not repaired.capabilities:
                    report.rejected.append("pi_cleared_capabilities")
                    report.still_pending.extend(issue.issue_id for issue in remaining_group)
                    pi.bind_flow_spec(spec)
                    await _persist_working_copy(context, baseline, spec)
                    await context.record(WorkflowActivity(
                        step=WorkflowStep.RESOLVING,
                        round=context.current_round,
                        status="running",
                        label=f"能力「{title}」的提交清空了能力集合，已回退本组修改",
                        target=cap_target,
                    ))
                    await context.record(WorkflowActivity(
                        step=WorkflowStep.RESOLVING,
                        round=context.current_round,
                        status="blocked",
                        label=_capability_feedback_label(
                            title=title,
                            resolved=tuple(operator_resolved),
                            pending=tuple(remaining_group),
                            last=last,
                        ),
                        target=cap_target,
                    ))
                    continue
                spec = normalize_stage_seven_working_copy(baseline, repaired)
                try:
                    assert_stage_six_contract_preserved(baseline, spec)
                except StageSixContractChanged:
                    spec = pre_pi_spec
                    pi.bind_flow_spec(spec)
                    report.rejected.append(STAGE_SIX_CONTRACT_CHANGED)
                    report.still_pending.extend(issue.issue_id for issue in remaining_group)
                    _record_capability_verification(
                        spec,
                        group_key,
                        status="incomplete",
                        resolved=[issue.issue_id for issue in operator_resolved],
                        pending=[issue.issue_id for issue in remaining_group],
                        reason="阶段 7 改变了阶段 6 公开能力契约，已回滚",
                    )
                    await _persist_working_copy(context, baseline, spec)
                    await context.record(WorkflowActivity(
                        step=WorkflowStep.RESOLVING,
                        round=context.current_round,
                        status="blocked",
                        label=f"能力「{title}」的修补改变了阶段 6 公开契约，已回滚",
                        target=cap_target,
                    ))
                    continue
                pi.bind_flow_spec(spec)
                await _persist_working_copy(context, baseline, spec)
            changed = flow_spec_fingerprint(spec) != before_pi
            open_ids, open_identities = _open_issue_tokens(spec)
            resolved_issues: list[WorkflowIssue] = list(operator_resolved)
            pending_issues: list[WorkflowIssue] = []
            resolved_ids = {issue.issue_id for issue in resolved_issues}
            for issue in group_issues:
                if issue.issue_id in resolved_ids:
                    continue
                if issue.issue_id in open_ids or _issue_field_identity(issue) in open_identities:
                    report.still_pending.append(issue.issue_id)
                    pending_issues.append(issue)
                    continue
                report.applied.append(issue.issue_id)
                report.resolved.append(issue.issue_id)
                resolved_issues.append(issue)
                resolved_ids.add(issue.issue_id)
            group_name = capability.name if capability is not None else "flow"
            if pi_issues and changed and not pending_issues:
                report.applied.append(f"capability_repair:{group_name}")
            elif pi_issues and not changed and pending_issues:
                report.skipped.append(f"pi_repair:{group_name}")
            if not pending_issues:
                verification_status = "verified"
                verification_reason = ""
            elif resolved_issues:
                verification_status = "partially_verified"
                verification_reason = "本组仍有未关闭问题"
            else:
                verification_status = "blocked"
                verification_reason = "本组问题仍未关闭"
            _record_capability_verification(
                spec,
                group_key,
                status=verification_status,
                resolved=[issue.issue_id for issue in resolved_issues],
                pending=[issue.issue_id for issue in pending_issues],
                reason=verification_reason,
            )
            await context.record(WorkflowActivity(
                step=WorkflowStep.RESOLVING,
                round=context.current_round,
                status="resolved" if not pending_issues else "running",
                label=_capability_feedback_label(
                    title=title,
                    resolved=tuple(resolved_issues),
                    pending=tuple(pending_issues),
                    last=last,
                ),
                target=cap_target,
            ))
            spec = normalize_stage_seven_working_copy(baseline, spec)
            await _persist_working_copy(context, baseline, spec)
        return spec

    async def publish(
        self,
        draft: dict[str, Any],
        context: PipelineContext,
    ) -> dict[str, Any]:
        context.ensure_active()
        spec = FlowSpec.model_validate(draft)
        if context.machine_verification:
            baseline = FlowSpec.model_validate(context.stage_six_baseline or draft)
            spec = normalize_stage_seven_working_copy(baseline, spec)
            try:
                assert_stage_six_contract_preserved(baseline, spec)
            except StageSixContractChanged as exc:
                raise RuntimeError("发布前阶段 6 公开能力契约已变化") from exc
            report = verification_report(spec)
            scope = build_stage_seven_scope(spec, baseline=baseline)
            decision = evaluate_recording_release(spec)
            checked_fp = (
                str(context.stage_seven_verdict.working_fingerprint)
                if context.stage_seven_verdict is not None
                else working_fingerprint(spec)
            )
            rechecked_fp = working_fingerprint(spec)
            verdict = evaluate_stage_seven_verdict(
                baseline=baseline,
                working=spec,
                scope=scope,
                verification_report=report,
                preflight=dict(((spec.meta or {}).get("verification_run") or {}).get("preflight") or {}),
                release=decision,
                attempt_id=context.stage_seven_attempt_id,
                revision=context.stage_seven_revision,
            )
            publishable = compute_publishable(
                status=verdict.status,
                all_verified=verdict.all_verified,
                unverified=verdict.unverified,
                preflight=preflight_status_of(verdict.preflight),
                release_status=decision.status,
                callable_spec=decision.callable_spec,
                baseline=baseline,
                working=spec,
                working_fp=checked_fp,
                rechecked_fp=rechecked_fp,
            )
            if not publishable or decision.callable_spec is None:
                raise RuntimeError("发布边界前机器验证未全部通过")
            publishable_spec = decision.callable_spec
            try:
                assert_stage_six_contract_preserved(baseline, publishable_spec)
            except StageSixContractChanged as exc:
                raise RuntimeError("发布前阶段 6 公开能力契约已变化") from exc
            if not callable_covers_stage_six(baseline, publishable_spec):
                raise RuntimeError("发布候选未覆盖阶段 6 的全部能力")
            verification_status = "passed"
        else:
            publishable_spec = spec
            verification_status = "skipped_by_operator"
        verification = {
            "enabled": context.machine_verification,
            "status": verification_status,
            "capability_verification": dict((spec.meta or {}).get("capability_verification") or {}),
        }
        publishable_spec.meta = {
            **(publishable_spec.meta or {}),
            "machine_verification": verification,
        }
        release_spec, candidate = prepare_flow_release_candidate(publishable_spec)
        candidate["machine_verification"] = verification
        context.remember_draft(release_spec.model_dump(mode="json"))
        bind_flow_spec = None
        try:
            pi = await self.pi_provider(False)
            bind_flow_spec = getattr(pi, "bind_flow_spec", None)
        except Exception:  # noqa: BLE001 - Pi unavailability must not block evidence-backed publish
            pi = None
        if callable(bind_flow_spec):
            bind_flow_spec(release_spec)
        released = await self.publisher(release_spec, candidate, context)
        if context.machine_verification:
            await _persist_stage_seven_if_needed(
                context,
                baseline,
                spec,
                context.stage_seven_verdict,
                dict(((spec.meta or {}).get("verification_run") or {}).get("preflight") or {}),
            )
        return released
