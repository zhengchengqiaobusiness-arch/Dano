"""Bounded autonomous verification for finalized page recordings."""
from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
from copy import deepcopy
from typing import Any, Awaitable, Callable

from dano.execution.page.value_tracing import discover_response_key_maps, discover_value_links


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
PromptRunner = Callable[[Awaitable[Any]], Awaitable[Any]]
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# One slow-model verify round needs: context reads, a real write with read-back
# (execute_write_with_verify), and the repair submission. 180s for the whole
# phase starved the executor before the write tool ever ran, so every publish
# ended partially verified. The phase budget stays bounded but realistic, and
# each round gets its own slice so one slow turn cannot consume every retry.
_FINAL_ANALYSIS_TIMEOUT_S = 900.0
_VERIFY_ROUND_TIMEOUT_S = 360.0
_FINAL_ANALYSIS_TIMEOUT_MESSAGE = "录制最终分析超过总时限，当前草稿已保留"


def _unverified_targets(spec) -> set[tuple[str, str]]:  # noqa: ANN001
    return {
        (str(item.get("target_kind") or ""), str(item.get("target_id") or ""))
        for item in (spec.meta or {}).get("unverified") or []
        if isinstance(item, dict)
    }


def _request_step_id(spec, request_id: str) -> str:  # noqa: ANN001
    for step in spec.steps:
        if str((step.source_meta or {}).get("request_id") or "") == request_id:
            return step.step_id
    usage = (spec.request_facts.usage or {}).get(request_id)
    return str(usage.materialized_step_id or "") if usage is not None else ""


def _candidate_link_id(candidate: dict[str, Any]) -> str:
    signature = "\n".join(str(candidate.get(key) or "") for key in (
        "source_request_id", "source_path", "source_collection_path", "source_key_path",
        "source_label_path", "target_request_id", "target_path", "target_container_path",
    ))
    return f"candidate-{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:12]}"


def _dependency_candidate_todos(spec, skipped: set[tuple[str, str]]) -> list[dict[str, Any]]:  # noqa: ANN001
    """Promote strong captured value links when no agent-authored link exists yet."""
    rows = [fact.model_dump(mode="json") for fact in spec.request_facts.requests]
    todos: list[dict[str, Any]] = []
    candidates = [
        *discover_value_links(rows),
        *discover_response_key_maps(rows),
    ]
    for candidate in candidates:
        source_request_id = str(candidate.get("source_request_id") or "")
        target_request_id = str(candidate.get("target_request_id") or "")
        source_step_id = _request_step_id(spec, source_request_id)
        target_step_id = _request_step_id(spec, target_request_id)
        if not source_step_id or not target_step_id:
            continue
        reported_source_path = str(candidate.get("source_path") or candidate.get("source_collection_path") or "")
        reported_target_path = str(candidate.get("target_path") or candidate.get("target_container_path") or "")
        source_path = reported_source_path.removeprefix("response.")
        target_path = reported_target_path.removeprefix("request.")
        dependency_kind = str(candidate.get("kind") or "value")
        if any(
            link.source_step_id == source_step_id
            and str(link.source_path or "").removeprefix("response.") == source_path
            and link.target_step_id == target_step_id
            and str(link.target_path or "").removeprefix("request.") == target_path
            and str(link.kind or "value") == dependency_kind
            for link in spec.links
        ):
            continue
        link_id = _candidate_link_id(candidate)
        if ("dependency_candidate", link_id) in skipped:
            continue
        todo = {
            "kind": "dependency_candidate",
            "dependency_kind": dependency_kind,
            "target_id": link_id,
            "link_id": link_id,
            "source_step_id": source_step_id,
            "source_request_id": source_request_id,
            "source_path": reported_source_path,
            "target_step_id": target_step_id,
            "target_request_id": target_request_id,
            "target_path": reported_target_path,
            "chain_request_ids": [source_request_id, target_request_id],
            "value_sample": str(candidate.get("value_sample") or "")[:128],
            "occurrences": int(candidate.get("occurrences") or 1),
            "confidence": 0.9,
            "suggested_tool": "submit_recording_repair",
            "completion_ops": ["propose_dependency", "verify_dependency", "confirm_dependency"],
        }
        if dependency_kind == "response_key_map":
            todo.update({
                "source_collection_path": str(candidate.get("source_collection_path") or ""),
                "source_key_path": str(candidate.get("source_key_path") or ""),
                "source_label_path": str(candidate.get("source_label_path") or ""),
                "target_container_path": str(candidate.get("target_container_path") or ""),
                "recorded_key_count": int(candidate.get("recorded_key_count") or 0),
            })
        todos.append(todo)
    return todos


def verification_todos(spec) -> list[dict[str, Any]]:  # noqa: ANN001
    """Return the deterministic dependency/write/enum work queue."""
    skipped = _unverified_targets(spec)
    todos: list[dict[str, Any]] = []
    for link in spec.links:
        if ("dependency", link.link_id) in skipped or (link.meta or {}).get("verified") is True:
            continue
        todos.append({
            "kind": "dependency",
            "dependency_kind": str(link.kind or "value"),
            "target_id": link.link_id,
            "source_step_id": link.source_step_id,
            "source_request_id": str((link.evidence or {}).get("source_request_id") or ""),
            "source_path": link.source_path,
            "target_step_id": link.target_step_id,
            "target_request_id": str((link.evidence or {}).get("target_request_id") or ""),
            "target_path": link.target_path,
            "suggested_tool": "verify_dependency",
            "completion_op": "confirm_dependency",
        })
    todos.extend(_dependency_candidate_todos(spec, skipped))
    for step in spec.steps:
        if (step.method or "").upper() not in _WRITE_METHODS:
            continue
        if ("write_verify", step.step_id) in skipped:
            continue
        fact_check = step.fact_check or {}
        if fact_check.get("verified") is True and fact_check.get("verification_id"):
            continue
        todos.append({
            "kind": "write_verify",
            "target_id": step.step_id,
            "write_request_id": str((step.source_meta or {}).get("request_id") or ""),
            "candidate_read_request_ids": [
                fact.request_id
                for fact in spec.request_facts.requests
                if (fact.method or "GET").upper() in {"GET", "HEAD", "POST"}
                and fact.request_id != str((step.source_meta or {}).get("request_id") or "")
            ][:25],
            "suggested_tool": "execute_write_with_verify",
            "completion_op": "bind_verify_read",
        })
    for step in spec.steps:
        bindings = {
            binding.path or binding.id_path: binding
            for binding in step.selects
            if binding.path or binding.id_path
        }
        enum_paths = {
            param.path
            for param in step.params
            if param.type in {"enum", "list-enum"}
            and param.source_kind != "api_option"
            and not param.enum_options
        }
        enum_paths.update(bindings)
        for path in sorted(enum_paths):
            binding = bindings.get(path)
            verification_id = str(binding.verification_id or "") if binding is not None else ""
            binding_incomplete = bool(
                binding is not None
                and (
                    binding.enum_confirmed is not True
                    or (binding.count and len(binding.options or []) < binding.count)
                )
            )
            param = next((item for item in step.params if item.path == path), None)
            param_incomplete = bool(
                param is not None
                and param.type in {"enum", "list-enum"}
                and param.source_kind != "api_option"
                and not param.enum_options
            )
            target_id = f"{step.step_id}:{path}"
            if (
                not verification_id
                and (binding_incomplete or param_incomplete)
                and ("enum", target_id) not in skipped
            ):
                completion_op = "attach_enum_options" if binding is not None else "set_param_enum"
                todos.append({
                    "kind": "enum",
                    "target_id": target_id,
                    "step_id": step.step_id,
                    "path": path,
                    "source_request_id": str(binding.source_request_id or "") if binding is not None else "",
                    "known_count": len(binding.options or []) if binding is not None else len(param.enum_options or []),
                    "expected_count": binding.count if binding is not None else 0,
                    "suggested_tools": (
                        ["browser_snapshot", "browser_click", "replay_request"]
                        if binding is not None else
                        ["get_recording_state", "submit_recording_repair"]
                    ),
                    "completion_op": completion_op,
                })
    return todos


def _release_issue_todos(spec, existing: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:  # noqa: ANN001
    """Project machine-release blockers into the existing verification queue.

    Release evaluation starts only after a capability has a materialized call
    graph.  Earlier recording drafts deliberately remain governed by the
    capture/semantic-plan todos instead of producing noisy anchor failures.
    """
    if not any(cap.nodes and cap.request_refs for cap in spec.capabilities):
        return [], []

    from dano.execution.page.flow_spec import flow_spec_fingerprint
    from dano.onboarding.recording_release import evaluate_recording_release

    decision = evaluate_recording_release(spec)
    issues = [
        issue.to_dict()
        for capability in decision.capabilities
        for issue in capability.issues
    ]
    current_fingerprint = flow_spec_fingerprint(spec)
    for issue in (spec.meta or {}).get("release_feedback_issues") or []:
        if not isinstance(issue, dict):
            continue
        bound_fingerprint = str(issue.get("flow_fingerprint") or "")
        if bound_fingerprint and bound_fingerprint != current_fingerprint:
            continue
        issues.append(dict(issue))

    existing_kinds = {str(item.get("kind") or "") for item in existing}
    covered_codes: set[str] = set()
    if "dependency" in existing_kinds:
        covered_codes.update({"dependency_verification_missing", "dependency_verification_stale"})
    if "write_verify" in existing_kinds:
        covered_codes.add("write_readback_missing")
    if "enum" in existing_kinds:
        covered_codes.add("enum_options_unverified")

    unique_issues: dict[str, dict[str, Any]] = {}
    for issue in issues:
        issue_id = str(issue.get("issue_id") or "")
        if issue_id:
            unique_issues[issue_id] = issue
    todos = [
        {
            "kind": "release_issue",
            "target_id": issue_id,
            "issue_id": issue_id,
            "check_code": str(issue.get("check_code") or ""),
            "capability_id": str(issue.get("capability_id") or ""),
            "step_id": str(issue.get("step_id") or ""),
            "field_id": str(issue.get("field_id") or ""),
            "wire_path": str(issue.get("wire_path") or ""),
            "resolver": str(issue.get("resolver") or "machine_repair"),
            "evidence_refs": list(issue.get("evidence_refs") or []),
            "suggested_operations": list(issue.get("suggested_operations") or []),
            "message": str(issue.get("message") or ""),
            "suggested_tool": (
                "submit_recording_repair"
                if str(issue.get("resolver") or "") == "machine_repair"
                else "collect_evidence"
                if str(issue.get("resolver") or "") == "collect_evidence"
                else "ask_operator"
                if str(issue.get("resolver") or "") == "operator"
                else "report_external_blocker"
            ),
        }
        for issue_id, issue in unique_issues.items()
        if str(issue.get("check_code") or "") not in covered_codes
    ]
    return list(unique_issues.values()), todos


def verification_report(spec) -> dict[str, Any]:  # noqa: ANN001
    todos = verification_todos(spec)
    release_issues, release_todos = _release_issue_todos(spec, todos)
    todos.extend(release_todos)
    unverified = [
        dict(item)
        for item in (spec.meta or {}).get("unverified") or []
        if isinstance(item, dict)
    ]
    confirmed_links = sum(1 for link in spec.links if (link.meta or {}).get("verified") is True)
    writes = [step for step in spec.steps if (step.method or "").upper() in _WRITE_METHODS]
    verified_writes = sum(
        1 for step in writes
        if (step.fact_check or {}).get("verified") is True and (step.fact_check or {}).get("verification_id")
    )
    return {
        "complete": not todos,
        "all_verified": not todos and not unverified,
        "todos": todos,
        "release_issues": release_issues,
        "unverified": unverified,
        "confirmed_links": confirmed_links,
        "link_count": len(spec.links),
        "verify_coverage": verified_writes,
        "write_count": len(writes),
    }


def require_verification_complete(spec, *, skip_verify: bool = False) -> dict[str, Any]:  # noqa: ANN001
    report = verification_report(spec)
    run = dict((spec.meta or {}).get("verification_run") or {})
    if not skip_verify and (not run.get("complete") or report["todos"]):
        raise ValueError("录制验证阶段尚未完成")
    return report


async def _emit(progress: ProgressCallback | None, payload: dict[str, Any]) -> None:
    if progress is None:
        return
    result = progress(payload)
    if inspect.isawaitable(result):
        await result


async def _prompt_before_deadline(
    session,
    prompt: str,
    *,
    deadline: float,
    prompt_runner: PromptRunner | None,
) -> Any:  # noqa: ANN001
    """Run one Pi turn inside the given deadline.

    The deadline is the round budget; it deliberately overrides the session's
    default per-command timeout because a verify turn (real write + read-back
    + submission) legitimately outlasts an ordinary command.
    """
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise asyncio.TimeoutError(_FINAL_ANALYSIS_TIMEOUT_MESSAGE)
    operation = session.prompt(prompt, timeout_s=remaining)
    awaited = prompt_runner(operation) if prompt_runner is not None else operation
    return await asyncio.wait_for(awaited, timeout=remaining)


def _progress(stage: str, detail: str, report: dict[str, Any], *, round_number: int = 0) -> dict[str, Any]:
    return {
        "stage": stage,
        "detail": detail,
        "round": round_number,
        "pending": len(report["todos"]),
        "confirmed_links": report["confirmed_links"],
        "verify_coverage": report["verify_coverage"],
        "write_count": report["write_count"],
    }


def finalize_verification_state(
    spec,
    *,
    rounds: int,
    max_rounds: int,
    errors: list[str] | None = None,
    stop_reason: str = "",
):  # noqa: ANN001, ANN202
    """Checkpoint verification without converting blockers into publishable state."""
    from dano.execution.page.flow_spec import _auto_confirm_ready_capabilities

    current = _consume_dependency_executor_evidence(spec)
    report = verification_report(current)
    final_report = report
    resolvers = {
        str(item.get("resolver") or "")
        for item in final_report.get("release_issues") or []
        if isinstance(item, dict)
    }
    status = "completed" if final_report["all_verified"] else (
        "waiting_for_operator" if "operator" in resolvers else
        "external_blocked" if stop_reason == "external_blocked" or "external_blocked" in resolvers else
        stop_reason or "pending"
    )
    current.meta = {
        **(current.meta or {}),
        "verification_run": {
            "complete": final_report["complete"],
            "all_verified": final_report["all_verified"],
            "status": status,
            "stop_reason": stop_reason,
            "rounds": rounds,
            "max_rounds": max_rounds,
            "errors": list(errors or []),
            "summary": {key: final_report[key] for key in (
                "confirmed_links", "link_count", "verify_coverage", "write_count",
            )},
        },
    }
    if final_report["all_verified"]:
        current = _auto_confirm_ready_capabilities(current)
    return current, final_report


def _verification_state_fingerprint(spec, report: dict[str, Any]) -> str:  # noqa: ANN001
    """Fingerprint executable FlowSpec state plus the unresolved problem set."""
    from dano.execution.page.flow_spec import flow_spec_fingerprint

    problems = sorted(
        (
            str(item.get("kind") or ""),
            str(item.get("target_id") or ""),
            str(item.get("check_code") or ""),
            str(item.get("resolver") or ""),
        )
        for item in report.get("todos") or []
        if isinstance(item, dict)
    )
    raw = json.dumps(
        {"flow": flow_spec_fingerprint(spec), "problems": problems},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _consume_write_executor_evidence(spec):  # noqa: ANN001, ANN202
    """Bind passed write read-back executions even when the model turn died.

    ``execute_write_with_verify`` is the authoritative executor: once its
    verification record passed, the conclusion must not be lost because the Pi
    turn timed out before submitting ``bind_verify_read``. The op below reuses
    the exact executed subject (step, read request, assertion), so the guarded
    apply path still validates everything.
    """
    from dano.execution.page.flow_spec import apply_flow_edits

    current = spec
    latest_by_step: dict[str, dict[str, Any]] = {}
    for record in list((spec.meta or {}).get("verification_log") or []):
        if not isinstance(record, dict) or record.get("kind") != "write_execute":
            continue
        step_id = str((record.get("subject") or {}).get("write_step_id") or "")
        if step_id:
            latest_by_step[step_id] = record
    for step_id, record in latest_by_step.items():
        if record.get("status") != "passed" or not str(record.get("verification_id") or ""):
            continue
        step = next((item for item in current.steps if item.step_id == step_id), None)
        if step is None:
            continue
        fact_check = step.fact_check or {}
        if fact_check.get("verified") is True and fact_check.get("verification_id"):
            continue
        subject = record.get("subject") or {}
        assertion = subject.get("assertion")
        read_request_id = str(subject.get("verify_request_id") or "")
        if not isinstance(assertion, dict) or not assertion or not read_request_id:
            continue
        try:
            current = apply_flow_edits(current, [{
                "op": "bind_verify_read",
                "write_step_id": step_id,
                "read_request_id": read_request_id,
                "verification_id": str(record.get("verification_id") or ""),
                "assertion": deepcopy(assertion),
                "actor": "agent",
            }])
        except ValueError:
            # The guarded op rejects evidence that no longer matches the
            # current draft; such a write stays pending for the next round.
            continue
    return current


def _consume_dependency_executor_evidence(spec):  # noqa: ANN001, ANN202
    """Apply the latest current-signature dependency execution result."""
    from dano.execution.page.flow_spec import apply_flow_edits

    current = spec.model_copy(deep=True)
    # Executor evidence is authoritative even when the Pi turn times out after
    # the tool returns but before it can submit the follow-up operation. Keep
    # only the latest record for the current link signature: a pass confirms
    # the hypothesis; a deterministic failure rejects an unlocked model
    # proposal instead of retrying and publishing the known-bad dependency.
    from dano.execution.page.recording_live import dependency_link_signature

    latest_by_link: dict[str, dict[str, Any]] = {}
    links_by_id = {link.link_id: link for link in current.links}
    for record in list((current.meta or {}).get("verification_log") or []):
        if not isinstance(record, dict) or record.get("kind") != "dependency_execute":
            continue
        subject = record.get("subject") or {}
        link_id = str(subject.get("link_id") or "")
        link = links_by_id.get(link_id)
        if link is None or str(subject.get("signature") or "") != dependency_link_signature(link):
            continue
        latest_by_link[link_id] = record
    for link_id, record in latest_by_link.items():
        verification_id = str(record.get("verification_id") or "")
        if record.get("status") == "failed":
            link = next((item for item in current.links if item.link_id == link_id), None)
            actor = str(((link.meta if link else {}) or {}).get("actor") or ((link.evidence if link else {}) or {}).get("actor") or "")
            if link is not None and actor == "agent" and not link.locked and not link.confirmed:
                current = apply_flow_edits(current, [{"op": "reject_dependency", "link_id": link_id}])
                current.meta = {
                    **(current.meta or {}),
                    "unverified": [
                        item for item in (current.meta or {}).get("unverified") or []
                        if not (
                            isinstance(item, dict)
                            and str(item.get("target_id") or "") == link_id
                            and str(item.get("target_kind") or "").startswith("dependency")
                        )
                    ],
                }
            continue
        if record.get("status") != "passed" or not verification_id:
            continue
        link = next((item for item in current.links if item.link_id == link_id), None)
        if (
            link is not None
            and link.confirmed
            and (link.meta or {}).get("verified") is True
            and str((link.meta or {}).get("verification_id") or "") == verification_id
        ):
            continue
        try:
            current = apply_flow_edits(current, [{
                "op": "confirm_dependency",
                "link_id": link_id,
                "verification_id": verification_id,
            }])
        except ValueError:
            # The guarded op rejects stale evidence whose link signature no
            # longer matches the current draft; such a link must stay pending.
            continue

    current = _consume_write_executor_evidence(current)

    # Capability membership is compiled from the verified dependency graph,
    # not from the model's proposed request_refs.  Dependency verification can
    # therefore expand the executable closure after the original capability
    # plan was compiled.  Refresh that same stored plan here so verified
    # preflight steps do not remain orphaned until another planning turn.
    capability_model = dict((current.meta or {}).get("capability_model") or {})
    semantic_plan = capability_model.get("semantic_plan")
    if isinstance(semantic_plan, dict) and semantic_plan.get("capabilities"):
        from dano.execution.page.capability_compiler import compile_capabilities

        compilation = compile_capabilities(current, semantic_plan)
        if not compilation.errors:
            current = compilation.spec
    return current


async def run_recording_verification(
    session,
    *,
    progress: ProgressCallback | None = None,
    prompt_runner: PromptRunner | None = None,
    max_rounds: int = 5,
    timeout_s: float = _FINAL_ANALYSIS_TIMEOUT_S,
) -> dict[str, Any]:  # noqa: ANN001
    """Run one evidence/repair loop until verified, blocked, or out of time."""
    errors: list[str] = []
    rounds = 0
    unchanged_attempts = 0
    stop_reason = ""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.01, float(timeout_s))
    while True:
        before_settle = session.current_flow_spec()
        current = _consume_dependency_executor_evidence(before_settle)
        if current.model_dump(mode="json") != before_settle.model_dump(mode="json"):
            session.bind_flow_spec(current)
        report = verification_report(current)
        if report["complete"]:
            stop_reason = "completed"
            break
        if deadline - loop.time() <= 0:
            errors.append(_FINAL_ANALYSIS_TIMEOUT_MESSAGE)
            stop_reason = "timeout"
            break
        round_number = rounds + 1
        rounds = round_number
        await _emit(progress, _progress(
            "analyzing",
            f"第 {round_number} 轮：处理 {len(report['todos'])} 个验证或发布待办",
            report,
            round_number=round_number,
        ))
        prompt = (
            "进入录后自主验证与发布自愈闭环。verification_todos/todos 已完整列在下方，"
            "直接逐项执行对应工具；"
            "仅在确实缺少上下文时才读取 get_recording_state 或 get_validation_report。"
            "已提议的依赖只用 verify_dependency(link_id) 验证，写步骤用 execute_write_with_verify，"
            "枚举待办按 completion_op 处理：attach_enum_options 先用 browser_* 补采并使用工具返回的"
            " verification_id；set_param_enum 必须使用录制状态中已有的 field_evidence 和完整字典 label/value。"
            "最后调用 submit_recording_repair 提交对应操作以及 confirm_dependency、bind_verify_read。"
            "dependency_candidate 是后端从真实请求值链发现的高置信候选：先单独提交"
            " propose_dependency；读取刷新后的验证报告后调用 verify_dependency(link_id)，"
            "再用它返回的 verification_id 提交 confirm_dependency。dependency_kind=response_key_map 时必须按"
            "待办给出的 collection/key/label/container 路径提交 response_key_map，并使用"
            " value_binding.kind=caller_map_by_label，不能退化成固定动态键。"
            "release_issue 必须只按 resolver 和 suggested_operations 处理：machine_repair 使用现有修复操作，"
            "collect_evidence 使用回放、依赖、页面或字典工具补证；operator 只能调用 ask_operator，"
            "external_blocked 不得猜测。禁止解析 message 中文文本决定修复。"
            "本轮不要提交 mark_unverified，也不得通过降低闸门标准绕过问题。todos="
            + json.dumps(report["todos"], ensure_ascii=False, separators=(",", ":"))
        )
        before_fingerprint = _verification_state_fingerprint(current, report)
        round_deadline = min(deadline, loop.time() + _VERIFY_ROUND_TIMEOUT_S)
        try:
            await _prompt_before_deadline(
                session,
                prompt,
                deadline=round_deadline,
                prompt_runner=prompt_runner,
            )
        except asyncio.TimeoutError:
            errors.append(f"第 {round_number} 轮验证超时，已取消本轮")
        except Exception as exc:  # noqa: BLE001 - failures remain explicit blocked state
            errors.append(str(exc)[:500])
            if (
                "no Pi model or credentials" in str(exc)
                or "DANO_PI_API_KEY" in str(exc)
            ):
                stop_reason = "external_blocked"
                break
        before_settle = session.current_flow_spec()
        settled = _consume_dependency_executor_evidence(before_settle)
        if settled.model_dump(mode="json") != before_settle.model_dump(mode="json"):
            session.bind_flow_spec(settled)
        updated = verification_report(settled)
        await _emit(progress, _progress(
            "validating",
            f"第 {round_number} 轮完成，复查验证证据",
            updated,
            round_number=round_number,
        ))
        after_fingerprint = _verification_state_fingerprint(settled, updated)
        if after_fingerprint == before_fingerprint:
            unchanged_attempts += 1
        else:
            unchanged_attempts = 0
        if unchanged_attempts >= 3:
            stop_reason = "no_progress"
            break

    current, final_report = finalize_verification_state(
        session.current_flow_spec(),
        rounds=rounds,
        max_rounds=int(max_rounds),
        errors=errors,
        stop_reason=stop_reason,
    )
    session.bind_flow_spec(current)
    run_state = dict((current.meta or {}).get("verification_run") or {})
    await _emit(progress, _progress(
        str(run_state.get("status") or "pending"),
        (
            "验证和发布闸门全部通过，准备最终审核"
            if final_report["all_verified"]
            else "验证已暂停，当前草稿和未决问题已保留"
        ),
        final_report,
        round_number=rounds,
    ))
    return {
        **final_report,
        "rounds": rounds,
        "errors": errors,
        "stop_reason": stop_reason,
        "status": run_state.get("status") or "pending",
    }
