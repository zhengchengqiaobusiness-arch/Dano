"""pi 工具回调路由(仅本机 + 按 run 校验临时令牌 + 工具白名单)。

挂在网关同进程同事件循环,pi 经 /_agent/tools/{name} 回调,共用网关 PG 池(无跨循环问题)。
"""

from __future__ import annotations

import hashlib
import json
import time

import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from dano.agent_tools import progress, runs
from dano.agent_tools.tools import TOOLS, ToolError
from dano.infra.run_logging import emit_run_event, emit_run_exception, new_call_id, new_span_id

log = structlog.get_logger(__name__)
agent_tools_router = APIRouter()
_LAST_POLL: dict[str, str] = {}


def _summary(name: str, out: dict) -> dict:
    """从工具返回里抽一条**简短摘要**(给日志/前端流程展示,不堆全量返回)。"""
    if not isinstance(out, dict):
        return {}
    keys = ("action", "count", "passed", "published", "asset_id", "all_passed",
            "connect_passed", "sandbox_passed", "coverage_gaps", "rule_count", "steps")
    s = {k: out[k] for k in keys if k in out}
    if name == "parse_spec" and "actions" in out:
        s["business_actions"] = len(out.get("actions") or [])
    return s


def _spec_fields(spec: object | None) -> dict:
    if spec is None:
        return {
            "flow_version": 0,
            "request_count": 0,
            "page_event_count": 0,
            "field_evidence_count": 0,
            "bound_count": 0,
            "ambiguous_count": 0,
            "unbound_count": 0,
            "capability_count": 0,
            "capability_names": [],
            "unresolved_count": 0,
        }
    meta = dict(getattr(spec, "meta", None) or {})
    facts = getattr(spec, "request_facts", None)
    evidence = [item for item in list(getattr(facts, "field_evidence", None) or []) if isinstance(item, dict)]
    page_events = list(getattr(facts, "page_events", None) or [])
    live_ids = [item for item in (meta.get("live_request_ids") or []) if item]
    capabilities = list(getattr(spec, "capabilities", None) or [])
    names: list[str] = []
    for cap in capabilities:
        name = getattr(cap, "name", None)
        if name is None and isinstance(cap, dict):
            name = cap.get("name") or cap.get("id") or cap.get("capability_id")
        if name:
            names.append(str(name))
    stats = {"bound": 0, "ambiguous": 0, "unbound": 0}
    for item in evidence:
        status = str(item.get("binding_status") or "unbound")
        stats[status if status in stats else "unbound"] += 1
    unresolved = 0
    for item in list(getattr(spec, "review_items", None) or []):
        resolved = getattr(item, "resolved", None)
        if resolved is None and isinstance(item, dict):
            resolved = item.get("resolved")
        if not resolved:
            unresolved += 1
    return {
        "flow_version": int(meta.get("current_version") or 0),
        "request_count": len(live_ids),
        "page_event_count": len(page_events),
        "field_evidence_count": len(evidence),
        "bound_count": stats["bound"],
        "ambiguous_count": stats["ambiguous"],
        "unbound_count": stats["unbound"],
        "capability_count": len(capabilities),
        "capability_names": names,
        "unresolved_count": unresolved,
    }


def _current_spec(run_id: str):
    try:
        from dano.onboarding.recording_pi import active_recording_session

        session = active_recording_session(str(run_id or ""))
        if session is None or getattr(session, "flow_spec", None) is None:
            return None
        return session.current_flow_spec()
    except Exception:  # noqa: BLE001 - logging must not change tool behavior
        return None


def _plan_input_summary(params: dict, before: dict) -> dict:
    plan = params.get("plan") if isinstance(params.get("plan"), dict) else {}
    semantic = plan.get("semantic_plan") if isinstance(plan.get("semantic_plan"), dict) else {}
    capabilities = plan.get("capabilities")
    if not isinstance(capabilities, list):
        capabilities = semantic.get("capabilities") if isinstance(semantic.get("capabilities"), list) else []
    names: list[str] = []
    for cap in capabilities:
        if isinstance(cap, dict):
            name = cap.get("name") or cap.get("id") or cap.get("capability_id")
            if name:
                names.append(str(name))
        elif cap:
            names.append(str(cap))
    ops = plan.get("ops") if isinstance(plan.get("ops"), list) else plan.get("operations")
    unresolved = plan.get("unresolved_items")
    if not isinstance(unresolved, list):
        unresolved = semantic.get("unresolved_items") if isinstance(semantic.get("unresolved_items"), list) else []
    return {
        "flow_version_before": before.get("flow_version") or params.get("base_flow_version") or 0,
        "submitted_capability_count": len(capabilities),
        "submitted_capability_names": names,
        "submitted_operation_count": len(ops) if isinstance(ops, list) else 0,
        "submitted_unresolved_count": len(unresolved),
    }


def _op_counts(out: dict) -> dict[str, int]:
    results = out.get("op_results") if isinstance(out.get("op_results"), list) else []
    counts = {"applied_count": 0, "rejected_count": 0, "deferred_count": 0, "rolled_back_count": 0}
    for item in results:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        key = f"{status}_count"
        if key in counts:
            counts[key] += 1
    return counts


def _poll_unchanged(run_id: str, name: str, payload: dict) -> bool:
    key = f"{run_id}:{name}"
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    previous = _LAST_POLL.get(key)
    _LAST_POLL[key] = digest
    return previous == digest


@agent_tools_router.post("/_agent/tools/{name}")
async def call_tool(name: str, request: Request,
                    x_agent_token: str | None = Header(default=None)) -> dict:
    body = await request.json()
    run_id = body.get("run_id")
    if not runs.is_valid(run_id, x_agent_token):
        log.warning("agent_tool.bad_token", tool=name, run_id=run_id)
        raise HTTPException(status_code=401, detail="bad_token_or_run")
    if name not in TOOLS:
        log.warning("agent_tool.not_allowed", tool=name, run_id=run_id)
        raise HTTPException(status_code=404, detail="tool_not_allowed")
    params = body.get("params") or {}
    call_id = new_call_id()
    span_id = new_span_id("tool")
    before = _spec_fields(_current_spec(run_id))
    input_summary = (
        _plan_input_summary(params, before)
        if name == "submit_recording_plan"
        else {
            "since_seq": params.get("since_seq", 0),
            "limit": params.get("limit", 25),
        }
        if name == "get_recording_delta"
        else dict(before)
    )
    emit_run_event(
        "agent_tool.call",
        stage="plan" if name == "submit_recording_plan" else "tool",
        status="started",
        summary=(
            "开始提交录制计划"
            if name == "submit_recording_plan"
            else f"调用 {name}"
        ),
        level="info" if name == "submit_recording_plan" else "debug",
        visibility="console" if name == "submit_recording_plan" else "detail",
        call_id=call_id,
        span_id=span_id,
        tool=name,
        run_id=run_id,
        input_summary=input_summary,
        flow_version_before=before.get("flow_version"),
        details=input_summary,
    )
    progress.emit(run_id, {"type": "tool_call", "tool": name, "action": params.get("action")})
    t0 = time.monotonic()
    try:
        out = await TOOLS[name](run_id, params)
    except ToolError as e:
        emit_run_event(
            "agent_tool.rejected",
            stage="plan" if name == "submit_recording_plan" else "tool",
            status="warning",
            level="warning",
            summary=f"{name} 被拒绝",
            duration_ms=(time.monotonic() - t0) * 1000,
            call_id=call_id,
            span_id=span_id,
            tool=name,
            run_id=run_id,
            details={
                **input_summary,
                "reason": str(e)[:300],
                "kept_previous_plan": True,
                "accepted": False,
            },
            next_action="重新读取最新录制状态后再提交一次",
        )
        progress.emit(run_id, {"type": "tool_error", "tool": name, "error": str(e)})
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 - 记真因再抛(便于排查),令牌/参数错已在上面拦
        emit_run_exception(
            "agent_tool.error",
            e,
            stage="tool",
            summary=f"{name} 执行异常",
            call_id=call_id,
            span_id=span_id,
            tool=name,
            run_id=run_id,
        )
        progress.emit(run_id, {"type": "tool_error", "tool": name, "error": repr(e)})
        raise
    dur_ms = (time.monotonic() - t0) * 1000
    after = _spec_fields(_current_spec(run_id))
    output_summary = _tool_output_summary(name, params, out if isinstance(out, dict) else {}, before, after)
    unchanged = name in {"get_recording_state", "get_recording_delta"} and _poll_unchanged(
        str(run_id or ""), name, output_summary,
    )
    emit_run_event(
        "agent_tool.done",
        stage="plan" if name == "submit_recording_plan" else "tool",
        status="succeeded" if not (name == "submit_recording_plan" and output_summary.get("accepted") is False) else "warning",
        level="debug" if unchanged else ("warning" if name == "submit_recording_plan" and output_summary.get("accepted") is False else "info"),
        visibility="detail" if unchanged else "console",
        summary=_done_summary(name, output_summary, unchanged),
        duration_ms=dur_ms,
        call_id=call_id,
        span_id=span_id,
        tool=name,
        run_id=run_id,
        input_summary=input_summary,
        output_summary=output_summary,
        flow_version_before=output_summary.get("flow_version_before", before.get("flow_version")),
        flow_version_after=output_summary.get("flow_version_after", after.get("flow_version")),
        details=output_summary,
    )
    progress.emit(run_id, {
        "type": "tool_done",
        "tool": name,
        "dur_s": round(dur_ms / 1000, 2),
        "summary": _summary(name, out if isinstance(out, dict) else {}),
    })
    return out


def _tool_output_summary(name: str, params: dict, out: dict, before: dict, after: dict) -> dict:
    if name == "get_recording_state":
        return {
            "flow_version": out.get("flow_version", after.get("flow_version")),
            "request_count": after.get("request_count", 0),
            "page_event_count": after.get("page_event_count", 0),
            "field_evidence_count": after.get("field_evidence_count", 0),
            "bound_count": after.get("bound_count", 0),
            "ambiguous_count": after.get("ambiguous_count", 0),
            "unbound_count": after.get("unbound_count", 0),
            "capability_count": after.get("capability_count", 0),
            "unresolved_count": after.get("unresolved_count", 0),
        }
    if name == "get_recording_delta":
        requests = out.get("requests") if isinstance(out.get("requests"), list) else []
        page_events = out.get("page_events") if isinstance(out.get("page_events"), list) else []
        return {
            "since_seq": out.get("since_seq", params.get("since_seq", 0)),
            "next_seq": out.get("next_seq"),
            "has_more": out.get("has_more"),
            "new_request_count": len(requests),
            "new_page_event_count": len(page_events),
            "new_field_evidence_count": max(
                0,
                int(after.get("field_evidence_count") or 0) - int(before.get("field_evidence_count") or 0),
            ),
        }
    if name == "submit_recording_plan":
        ops = _op_counts(out)
        return {
            **_plan_input_summary(params, before),
            "accepted": bool(out.get("accepted", True)),
            "unchanged": bool(out.get("unchanged", False)),
            "flow_version_after": out.get("flow_version", after.get("flow_version")),
            "capability_plan_complete": bool(out.get("capability_plan_complete", after.get("capability_count", 0) > 0)),
            "capability_plan_received": bool(out.get("capability_plan_received")),
            "submission_complete": bool(out.get("submission_complete")),
            "submitted_capability_count": int(out.get("submitted_capability_count") or 0),
            "materialized_capability_count": int(out.get("materialized_capability_count") or 0),
            "missing_submitted_capabilities": out.get("missing_submitted_capabilities") or [],
            "missing_public_action_request_ids": out.get("missing_public_action_request_ids") or [],
            "field_axis_gap_count": len(out.get("field_axis_gaps") or []),
            **ops,
            "must_retry": out.get("must_retry") or [],
            "capability_count": after.get("capability_count", 0),
            "unresolved_count": after.get("unresolved_count", 0),
            "kept_previous_plan": bool(out.get("unchanged")),
        }
    return _summary(name, out)


def _done_summary(name: str, output_summary: dict, unchanged: bool) -> str:
    if unchanged:
        return f"{name} 无变化"
    if name == "submit_recording_plan":
        if output_summary.get("accepted") is False:
            return "录制计划被拒绝"
        if output_summary.get("unchanged"):
            return "录制计划无变化"
        if output_summary.get("submission_complete"):
            return "录制能力计划已保存"
        if output_summary.get("capability_plan_received"):
            return "录制能力计划已保存，继续补充缺失能力边界"
        return "录制分析结论已保存"
    if name == "get_recording_state":
        return "读取录制状态"
    if name == "get_recording_delta":
        return "读取录制增量"
    return f"{name} 完成"
