"""Bounded autonomous verification for finalized page recordings."""
from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
from typing import Any, Awaitable, Callable

from dano.execution.page.value_tracing import discover_response_key_maps, discover_value_links


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
PromptRunner = Callable[[Awaitable[Any]], Awaitable[Any]]
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_FINAL_ANALYSIS_TIMEOUT_S = 180.0
_FINAL_ANALYSIS_TIMEOUT_MESSAGE = "录制最终分析超过总时限，剩余待办已标记为 unverified"


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
            "dependency_kind": str((link.meta or {}).get("kind") or "value"),
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
        for binding in step.selects:
            target_id = f"{step.step_id}:{binding.path or binding.id_path}"
            incomplete = (
                not binding.verification_id
                and (
                    binding.enum_confirmed is not True
                    or (binding.count and len(binding.options or []) < binding.count)
                )
            )
            if incomplete and ("enum", target_id) not in skipped:
                todos.append({
                    "kind": "enum",
                    "target_id": target_id,
                    "step_id": step.step_id,
                    "path": binding.path or binding.id_path,
                    "source_request_id": binding.source_request_id,
                    "known_count": len(binding.options or []),
                    "expected_count": binding.count,
                    "suggested_tools": ["browser_snapshot", "browser_click", "replay_request"],
                    "completion_op": "attach_enum_options",
                })
    return todos


def verification_report(spec) -> dict[str, Any]:  # noqa: ANN001
    todos = verification_todos(spec)
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


def recorded_goal_slug(spec) -> str:  # noqa: ANN001
    from dano.onboarding.goal import _slug

    intent = str((spec.goal or {}).get("intent") or spec.title or "goal")
    return _slug(intent).lower()[:64]


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
    """Run one Pi turn inside the single final-analysis deadline."""
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise asyncio.TimeoutError(_FINAL_ANALYSIS_TIMEOUT_MESSAGE)
    session_timeout = float(getattr(session, "timeout_s", 0) or 0)
    turn_timeout = remaining if session_timeout <= 0 else min(session_timeout, remaining)
    operation = session.prompt(prompt, timeout_s=turn_timeout)
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
):  # noqa: ANN001, ANN202
    """Turn every remaining todo into an explicit, publishable unverified record."""
    from dano.execution.page.flow_spec import (
        _auto_confirm_ready_capabilities,
        append_flow_version,
        apply_flow_edits,
    )

    current = spec.model_copy(deep=True)
    report = verification_report(current)
    if report["todos"]:
        current = apply_flow_edits(current, [
            {
                "op": "mark_unverified",
                "target_kind": item["kind"],
                "target_id": item["target_id"],
                "reason": "自主验证达到重试上限，发布时保留为 unverified",
                "actor": "agent",
            }
            for item in report["todos"]
        ])
        current = append_flow_version(current, "verification_exhausted", reason="自主验证未决项已显式标注")
    final_report = verification_report(current)
    current.meta = {
        **(current.meta or {}),
        "verification_run": {
            "complete": True,
            "all_verified": final_report["all_verified"],
            "rounds": rounds,
            "max_rounds": max_rounds,
            "errors": list(errors or []),
            "summary": {key: final_report[key] for key in (
                "confirmed_links", "link_count", "verify_coverage", "write_count",
            )},
        },
    }
    return _auto_confirm_ready_capabilities(current), final_report


async def generate_skill_documents(
    session,
    *,
    prompt_runner: PromptRunner | None = None,
    max_rounds: int = 3,
    deadline: float | None = None,
) -> dict[str, Any]:  # noqa: ANN001
    """Ask the same Pi session for package docs, bounded by deterministic validation."""
    from dano.export.skill_package import (
        flow_spec_unverified_capability_names,
        flow_spec_verification_ids,
        validate_skill_documents,
    )

    errors: list[str] = []
    issues: list[dict[str, Any]] = []
    attempts = 0
    for attempt in range(1, max(1, min(int(max_rounds), 3)) + 1):
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            errors.append(_FINAL_ANALYSIS_TIMEOUT_MESSAGE)
            break
        attempts = attempt
        suffix = (
            "上轮校验问题=" + json.dumps(issues, ensure_ascii=False, separators=(",", ":"))
            if issues else ""
        )
        prompt = (
            "验证阶段已结束。调用 get_recording_state 读取当前 FlowSpec，只基于其中事实生成自包含"
            " skill 包文档，然后调用 submit_skill_docs。skill_md 必须是完整 SKILL.md，含 YAML"
            " name/description 与 Transport、Preconditions、Steps、Branch exit、Pitfalls；Steps 的"
            "每一步都有 Done when:。reference_md 必须含 API chain、业务硬规则和 Fallback browser"
            " steps；每条 API chain 标真实 verification_id 或明确 unverified。禁止写入任何 token、"
            "cookie、密码或录制凭证。" + suffix
        )
        try:
            if deadline is None:
                operation = session.prompt(prompt, timeout_s=None)
                await (prompt_runner(operation) if prompt_runner is not None else operation)
            else:
                await _prompt_before_deadline(
                    session,
                    prompt,
                    deadline=deadline,
                    prompt_runner=prompt_runner,
                )
        except asyncio.TimeoutError:
            errors.append(_FINAL_ANALYSIS_TIMEOUT_MESSAGE)
            break
        except Exception as exc:  # noqa: BLE001 - renderer has a deterministic fallback
            errors.append(str(exc)[:500])
            if (
                "no Pi model or credentials" in str(exc)
                or "DANO_PI_API_KEY" in str(exc)
                or "超时" in str(exc)
            ):
                break
        docs = dict((session.current_flow_spec().meta or {}).get("skill_docs") or {})
        current = session.current_flow_spec()
        validation = validate_skill_documents(
            str(docs.get("skill_md") or ""),
            str(docs.get("reference_md") or ""),
            allowed_verification_ids=flow_spec_verification_ids(current),
            required_chain_names={cap.name for cap in current.capabilities if cap.name},
            required_unverified_chains=flow_spec_unverified_capability_names(current),
        )
        issues = list(validation["issues"])
        if validation["ok"]:
            break

    spec = session.current_flow_spec()
    docs = dict((spec.meta or {}).get("skill_docs") or {})
    validation = validate_skill_documents(
        str(docs.get("skill_md") or ""),
        str(docs.get("reference_md") or ""),
        allowed_verification_ids=flow_spec_verification_ids(spec),
        required_chain_names={cap.name for cap in spec.capabilities if cap.name},
        required_unverified_chains=flow_spec_unverified_capability_names(spec),
    )
    spec.meta = {
        **(spec.meta or {}),
        "skill_docs_generation": {
            "complete": True,
            "valid": validation["ok"],
            "attempts": attempts,
            "max_rounds": max(1, min(int(max_rounds), 3)),
            "issues": validation["issues"],
            "errors": errors,
            "fallback_required": not validation["ok"],
        },
    }
    session.bind_flow_spec(spec)
    return {
        "valid": validation["ok"],
        "attempts": attempts,
        "issues": validation["issues"],
        "errors": errors,
        "fallback_required": not validation["ok"],
    }


async def run_recording_verification(
    session,
    *,
    progress: ProgressCallback | None = None,
    prompt_runner: PromptRunner | None = None,
    max_rounds: int = 5,
    timeout_s: float = _FINAL_ANALYSIS_TIMEOUT_S,
) -> dict[str, Any]:  # noqa: ANN001
    """Run bounded agent turns under one deadline, then converge to publishable state."""
    errors: list[str] = []
    rounds = 0
    deadline = asyncio.get_running_loop().time() + max(0.01, float(timeout_s))
    for round_number in range(1, max(1, min(int(max_rounds), 5)) + 1):
        current = session.current_flow_spec()
        report = verification_report(current)
        if report["complete"]:
            break
        rounds = round_number
        await _emit(progress, _progress(
            "analyzing",
            f"第 {round_number} 轮：分析 {len(report['todos'])} 个验证待办",
            report,
            round_number=round_number,
        ))
        prompt = (
            "进入录后自主验证。先调用 get_recording_state 和 get_validation_report，逐项处理下面的"
            " verification_todos。已提议的依赖只用 verify_dependency(link_id) 验证，写步骤用 execute_write_with_verify，"
            "枚举/分支缺口用 browser_* 补采。只能使用工具返回的 verification_id，最后调用"
            " submit_recording_repair 提交 confirm_dependency、bind_verify_read、attach_enum_options。"
            "dependency_candidate 是后端从真实请求值链发现的高置信候选：先单独提交"
            " propose_dependency；读取刷新后的验证报告后调用 verify_dependency(link_id)，"
            "再用它返回的 verification_id 提交 confirm_dependency。dependency_kind=response_key_map 时必须按"
            "待办给出的 collection/key/label/container 路径提交 response_key_map，并使用"
            " value_binding.kind=caller_map_by_label，不能退化成固定动态键。"
            "本轮不要提交 mark_unverified；重试耗尽由后端统一处理。todos="
            + json.dumps(report["todos"], ensure_ascii=False, separators=(",", ":"))
        )
        try:
            await _prompt_before_deadline(
                session,
                prompt,
                deadline=deadline,
                prompt_runner=prompt_runner,
            )
        except asyncio.TimeoutError:
            errors.append(_FINAL_ANALYSIS_TIMEOUT_MESSAGE)
            break
        except Exception as exc:  # noqa: BLE001 - failures become explicit unverified output
            errors.append(str(exc)[:500])
            if (
                "no Pi model or credentials" in str(exc)
                or "DANO_PI_API_KEY" in str(exc)
                or "超时" in str(exc)
            ):
                break
        updated = verification_report(session.current_flow_spec())
        await _emit(progress, _progress(
            "validating",
            f"第 {round_number} 轮完成，复查验证证据",
            updated,
            round_number=round_number,
        ))

    bounded_max_rounds = max(1, min(int(max_rounds), 5))
    current, final_report = finalize_verification_state(
        session.current_flow_spec(),
        rounds=rounds,
        max_rounds=bounded_max_rounds,
        errors=errors,
    )
    session.bind_flow_spec(current)
    skill_docs = await generate_skill_documents(
        session,
        prompt_runner=prompt_runner,
        deadline=deadline,
    )
    await _emit(progress, _progress(
        "completed",
        "验证全部通过，准备自动发布" if final_report["all_verified"] else "验证结束，未决项已标注 unverified，准备发布",
        final_report,
        round_number=rounds,
    ))
    return {**final_report, "rounds": rounds, "errors": errors, "skill_docs": skill_docs}
