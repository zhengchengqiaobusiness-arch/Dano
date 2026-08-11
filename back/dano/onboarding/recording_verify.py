"""Bounded autonomous verification for finalized page recordings."""
from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
PromptRunner = Callable[[Awaitable[Any]], Awaitable[Any]]
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _unverified_targets(spec) -> set[tuple[str, str]]:  # noqa: ANN001
    return {
        (str(item.get("target_kind") or ""), str(item.get("target_id") or ""))
        for item in (spec.meta or {}).get("unverified") or []
        if isinstance(item, dict)
    }


def verification_todos(spec) -> list[dict[str, Any]]:  # noqa: ANN001
    """Return the deterministic dependency/write/enum work queue."""
    skipped = _unverified_targets(spec)
    todos: list[dict[str, Any]] = []
    for link in spec.links:
        if ("dependency", link.link_id) in skipped or (link.meta or {}).get("verified") is True:
            continue
        todos.append({
            "kind": "dependency",
            "target_id": link.link_id,
            "source_step_id": link.source_step_id,
            "source_request_id": str((link.evidence or {}).get("source_request_id") or ""),
            "source_path": link.source_path,
            "target_step_id": link.target_step_id,
            "target_request_id": str((link.evidence or {}).get("target_request_id") or ""),
            "target_path": link.target_path,
            "suggested_tool": "perturb_replay",
            "completion_op": "confirm_dependency",
        })
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
            operation = session.prompt(prompt, timeout_s=None)
            await (prompt_runner(operation) if prompt_runner is not None else operation)
        except Exception as exc:  # noqa: BLE001 - renderer has a deterministic fallback
            errors.append(str(exc)[:500])
            if "no Pi model or credentials" in str(exc) or "DANO_PI_API_KEY" in str(exc):
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
) -> dict[str, Any]:  # noqa: ANN001
    """Run at most five agent turns, then publish with explicit unverified annotations."""
    errors: list[str] = []
    rounds = 0
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
            " verification_todos。依赖用 perturb_replay，写步骤用 execute_write_with_verify，"
            "枚举/分支缺口用 browser_* 补采。只能使用工具返回的 verification_id，最后调用"
            " submit_recording_repair 提交 confirm_dependency、bind_verify_read、attach_enum_options。"
            "本轮不要提交 mark_unverified；重试耗尽由后端统一处理。todos="
            + json.dumps(report["todos"], ensure_ascii=False, separators=(",", ":"))
        )
        try:
            operation = session.prompt(prompt, timeout_s=None)
            await (prompt_runner(operation) if prompt_runner is not None else operation)
        except Exception as exc:  # noqa: BLE001 - failures become explicit unverified output
            errors.append(str(exc)[:500])
            if "no Pi model or credentials" in str(exc) or "DANO_PI_API_KEY" in str(exc):
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
    skill_docs = await generate_skill_documents(session, prompt_runner=prompt_runner)
    await _emit(progress, _progress(
        "completed",
        "验证全部通过，准备自动发布" if final_report["all_verified"] else "验证结束，未决项已标注 unverified，准备发布",
        final_report,
        round_number=rounds,
    ))
    return {**final_report, "rounds": rounds, "errors": errors, "skill_docs": skill_docs}
