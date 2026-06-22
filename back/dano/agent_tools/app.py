"""pi 工具回调路由(仅本机 + 按 run 校验临时令牌 + 工具白名单)。

挂在网关同进程同事件循环,pi 经 /_agent/tools/{name} 回调,共用网关 PG 池(无跨循环问题)。
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from dano.agent_tools import progress, runs
from dano.agent_tools.tools import TOOLS, ToolError

log = structlog.get_logger(__name__)
agent_tools_router = APIRouter()


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
    log.info("agent_tool.call", run_id=run_id, tool=name,
             action=params.get("action"), system=params.get("system_instance_id"))
    progress.emit(run_id, {"type": "tool_call", "tool": name, "action": params.get("action")})
    t0 = time.monotonic()
    try:
        out = await TOOLS[name](run_id, params)
    except ToolError as e:
        log.warning("agent_tool.rejected", run_id=run_id, tool=name, reason=str(e)[:300])
        progress.emit(run_id, {"type": "tool_error", "tool": name, "error": str(e)})
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 - 记真因再抛(便于排查),令牌/参数错已在上面拦
        log.exception("agent_tool.error", run_id=run_id, tool=name)
        progress.emit(run_id, {"type": "tool_error", "tool": name, "error": repr(e)})
        raise
    dur = round(time.monotonic() - t0, 2)
    summary = _summary(name, out)
    log.info("agent_tool.done", run_id=run_id, tool=name, dur_s=dur, **summary)
    progress.emit(run_id, {"type": "tool_done", "tool": name, "dur_s": dur, "summary": summary})
    return out


# 兼容旧接口:固定令牌的独立 app(Phase 2 测试用)
def make_agent_app(token: str, run_id: str):  # noqa: ANN201
    from fastapi import FastAPI
    runs.register(run_id, token)
    app = FastAPI(docs_url=None, redoc_url=None)
    app.include_router(agent_tools_router)
    return app
