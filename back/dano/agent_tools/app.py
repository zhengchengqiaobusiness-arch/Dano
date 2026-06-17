"""pi 工具回调路由(仅本机 + 按 run 校验临时令牌 + 工具白名单)。

挂在网关同进程同事件循环,pi 经 /_agent/tools/{name} 回调,共用网关 PG 池(无跨循环问题)。
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from dano.agent_tools import runs
from dano.agent_tools.tools import TOOLS, ToolError

agent_tools_router = APIRouter()


@agent_tools_router.post("/_agent/tools/{name}")
async def call_tool(name: str, request: Request,
                    x_agent_token: str | None = Header(default=None)) -> dict:
    body = await request.json()
    run_id = body.get("run_id")
    if not runs.is_valid(run_id, x_agent_token):
        raise HTTPException(status_code=401, detail="bad_token_or_run")
    if name not in TOOLS:
        raise HTTPException(status_code=404, detail="tool_not_allowed")
    try:
        return await TOOLS[name](run_id, body.get("params") or {})
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# 兼容旧接口:固定令牌的独立 app(Phase 2 测试用)
def make_agent_app(token: str, run_id: str):  # noqa: ANN201
    from fastapi import FastAPI
    runs.register(run_id, token)
    app = FastAPI(docs_url=None, redoc_url=None)
    app.include_router(agent_tools_router)
    return app
