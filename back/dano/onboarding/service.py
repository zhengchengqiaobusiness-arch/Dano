"""阶段一接入服务:模板分流 → spawn pi 自主生成 → 收已发布资产 → 接入报告。

架构:本服务在网关同一事件循环里临时起 pi 工具服务(uvicorn task),spawn pi(Node Sidecar)。
pi 经 /_agent/tools/* 回调进**本进程同循环**(共用 PG 池,无跨循环问题)。pi 编排生成,
Python 控发布闸门。凭证只在 materials(进程内),不进 LLM。
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from dano.agent_tools import materials, runs
from dano.assets.repository import AssetRepository
from dano.shared.asset_bodies import WorkflowSkillBody
from dano.shared.enums import AssetType, Subsystem
from dano.shared.models import Scope

log = structlog.get_logger(__name__)
BACK_DIR = Path(__file__).resolve().parent.parent.parent      # .../back
_OS_ENV_WHITELIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "windir", "ComSpec",
    "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS", "OS", "HOMEDRIVE", "HOMEPATH",
)
_PI_ENV = ("DANO_PI_API_KEY", "DANO_PI_BASE_URL", "DANO_PI_MODEL", "DANO_PI_PROVIDER")


class OnboardingReport(BaseModel):
    tenant: str
    system_instance_id: str
    run_id: str
    status: str                                   # completed / failed
    published_skills: list[str] = Field(default_factory=list)   # 已发布连接器动作
    pi_final_text: str = ""
    error: str | None = None


async def _start_tool_server() -> tuple:
    """同循环内起 pi 工具服务(uvicorn task),返回 (server, server_task, port)。"""
    import uvicorn
    from fastapi import FastAPI

    from dano.agent_tools.app import agent_tools_router
    app = FastAPI(docs_url=None, redoc_url=None)
    app.include_router(agent_tools_router)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, task, port


async def _spawn_pi(*, run_id: str, token: str, port: int, prompt: str,
                    context: dict, timeout_s: float) -> dict:
    """spawn Node Sidecar,送 start_run,读 JSONL 直到 run_completed。env 白名单。"""
    env = {k: os.environ[k] for k in _OS_ENV_WHITELIST if k in os.environ}
    env.update({k: os.environ[k] for k in _PI_ENV if k in os.environ})
    env.update({"DANO_AGENT_TOKEN": token, "DANO_AGENT_BASE_URL": f"http://127.0.0.1:{port}",
                "DANO_AGENT_RUN_ID": run_id, "PI_STUB": "0"})
    proc = await asyncio.create_subprocess_exec(
        "node", str(BACK_DIR / "agent" / "run_pi.mjs"), cwd=str(BACK_DIR), env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    start = json.dumps({"type": "start_run", "run_id": run_id, "prompt": prompt,
                        "context": context, "budget": {"timeout_s": int(timeout_s)}}) + "\n"
    proc.stdin.write(start.encode()); await proc.stdin.drain()
    completed: dict = {}
    try:
        async def _read():
            nonlocal completed
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode().strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "run_completed":
                    completed = ev
                    return
        await asyncio.wait_for(_read(), timeout=timeout_s)
    except asyncio.TimeoutError:
        completed = {"status": "failed", "error": "timeout"}
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    return completed


async def _publish_env_profile(run_id: str, sid: str, deploy: dict) -> None:
    """确定性发布环境画像(base_url+auth 来自 deploy),走同一草案→验证→发布闸门。"""
    from dano.agent_tools import tools as T
    from dano.shared.asset_bodies import AuthConfig, EnvProfileBody
    body = EnvProfileBody(
        deploy=deploy.get("deploy", "saas"), worker_location="平台托管", intranet_access="public",
        account_type=deploy.get("account_type", "test"),
        base_url=deploy.get("base_url", ""), auth=AuthConfig.model_validate(deploy.get("auth", {})),
    ).model_dump()
    d = await T.save_draft(run_id, {"system_instance_id": sid, "asset_type": "env_profile",
                                    "asset_key": "env_profile", "body": body})
    h = await T.health_check(run_id, {"asset_draft_id": d["asset_draft_id"]})
    await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
                                   "validation_run_ids": h["validation_run_ids"]})


async def _onboard_codegen(run_id: str, sid: str, flows: list[dict], coder,  # noqa: ANN001
                           max_read_flows: int | None, progress=None) -> dict:
    """主路径:goal 模式自动生成代码 adapter。

    读流程(GET)自动逐个生成只读 adapter(数量受 max_read_flows 限制,None=全部);
    写/复合流程按 flows=[{flow, actions?, test_input}] 声明(写操作需测试输入才能沙箱)。
    __base_url__ 由 deploy 自动注入到测试输入(沙箱时 adapter 需要),声明里只给业务字段。
    每条流程跑一遍 GenerationLoop(编码→测试→漏洞→审核→事实核查→发布)。
    """
    from dano.agent_tools import materials, tools as T
    from dano.generation import GenerationLoop, GoalBrief, PiCoder
    from dano.generation.strategies import select_strategy

    mat = materials.get(run_id, sid)
    base_url = (mat.deploy or {}).get("base_url", "") if mat else ""
    loop = GenerationLoop(coder or PiCoder(), on_event=progress)
    parsed = await T.parse_spec(run_id, {"system_instance_id": sid})
    actions = parsed.get("actions", [])
    by_name = {a["name"]: a for a in actions}
    goals: list[GoalBrief] = []
    declared: set[str] = set()
    for f in flows:                                       # 写/复合流程:调用方声明 + 测试输入
        fa = [by_name[n] for n in (f.get("actions") or []) if n in by_name] or actions
        ti = {**(f.get("test_input") or {}), "__base_url__": base_url}
        goals.append(GoalBrief(run_id=run_id, system_instance_id=sid, flow=f["flow"],
                               actions=fa, test_input=ti))
        declared.update(f.get("actions") or [])
    reads = 0
    for a in actions:                                     # 读流程:未被声明的 GET 动作各成一个只读 adapter
        if (a.get("method") or "GET").upper() != "GET" or a["name"] in declared:
            continue
        if max_read_flows is not None and reads >= max_read_flows:
            break
        reads += 1
        goals.append(GoalBrief(run_id=run_id, system_instance_id=sid, flow=a["name"],
                               actions=[a], test_input={"__base_url__": base_url}))
    if progress:
        progress({"type": "plan", "flows": [g.flow for g in goals]})
    oks = 0
    for idx, g in enumerate(goals):
        if progress:
            progress({"type": "flow_start", "flow": g.flow, "index": idx, "total": len(goals)})
        r = await loop.run(g, select_strategy(g.actions))
        oks += 1 if r.ok else 0
        log.info("onboard.codegen.flow", flow=g.flow, ok=r.ok, rejections=r.rejections)
        if progress:
            progress({"type": "flow_done", "flow": g.flow, "ok": r.ok,
                      "rejections": r.rejections, "asset_id": r.asset_id})
    return {"status": "completed",
            "final_text": f"goal 模式代码生成:{oks}/{len(goals)} 个流程发布"}


async def _onboard_legacy(run_id: str, sid: str, token: str, *, discover_workflows: bool,
                          policy_text: str, timeout_s: float) -> dict:
    """旧声明式接入:起工具服务 + spawn pi 建连接器/复合流程/制度。返回 completed。"""
    server, task, port = await _start_tool_server()
    prompt = (
        f"接入系统实例 {sid}。步骤:\n"
        f"1) 调 parse_spec(system_instance_id={sid}) 拿业务动作清单。\n"
        f"2) 对清单里**每一个**动作:依次调 draft_connector(system_instance_id={sid}, action=动作名)拿 asset_draft_id;"
        f"再调 sandbox_test(asset_draft_id=该id)拿 validation_run_ids;"
        f"若 connect_passed 且 sandbox_passed 为真,调 request_review(asset_draft_id=该id)跑三模型评审拿 review_run_ids 与 all_passed;"
        f"仅当 all_passed 为真,调 publish_asset(asset_draft_id=该id, validation_run_ids=沙箱的, review_run_ids=评审的)。"
        f"若评审未过,按 verdicts 的 reasons 处理,过不了就跳过该动作。\n"
        f"3) 全部处理完,用一句话总结发布了哪些动作。"
    )
    # goal 模式:连接器发布后,让 pi 自主发现多步业务流程并编排成复合 Skill
    discover_prompt = (
        f"在系统实例 {sid} 已发布的连接器之上,**发现多步业务流程**并编排成复合 Skill:\n"
        f"1) 调 parse_spec({sid}) 看动作清单,重点看每个动作的 params_out(出参)和 tags(阶段)。\n"
        f"2) 找需要**串联**的动作——信号:某动作出参(如 taskId/procInsId/procDefId)正是另一动作所需的入参;"
        f"或 tags 表明先后阶段(发起→提交→审批)。\n"
        f"3) 对发现的流程,先用 get_action_schema 看清各步请求体**嵌套结构与示例**,再用 draft_workflow 编排:"
        f"steps 的 inputs 用 'step:前一步动作.出参点路径'(如 step:start_leave_flow.data.taskId)串联、"
        f"'const:值' 填固定项、'field:名' 暴露给用户;user_fields/required_fields 给用户要填的业务字段。\n"
        f"4) 调 sandbox_test_workflow(asset_draft_id, test_input={{业务字段示例值}}) 整条验证;"
        f"passed 为真后,调 request_review(asset_draft_id) 跑三模型评审拿 review_run_ids 与 all_passed;"
        f"仅当 all_passed 为真才调 publish_asset(asset_draft_id, validation_run_ids, review_run_ids)。"
        f"评审未过则按 verdicts 的 reasons 调整步骤/映射后重测重审。\n"
        f"若没有可串联的多步流程,直接说明,不要强行编排。"
    )
    # 流程4:有制度文件则抽规则 → 用例验证 → 发布(制度免三模型评审,review_run_ids 传空)
    policy_prompt = (
        f"为系统实例 {sid} 抽取并发布制度规则:\n"
        f"1) 调 get_policy_doc({sid}) 拿制度原文;若为空,直接说明无制度,**不要**强行编造。\n"
        f"2) 把制度抽成声明式规则,调 draft_policy(system_instance_id={sid}, rules=[每条 "
        f"{{rule_id, description, condition(对输入字段的布尔表达式,如 'days > 15' 或 'amount > 1000'), "
        f"effect(放行|拦截|转审批)}}]).\n"
        f"3) 配关键用例覆盖每条规则边界,调 test_policy_cases(asset_draft_id, cases=[每条 "
        f"{{fields:{{字段:值}}, expect:放行|拦截|转审批}}]);passed 为真才调 "
        f"publish_asset(asset_draft_id, validation_run_ids=用例返回的, review_run_ids=[]).\n"
        f"4) 用例不过按 trace 修规则表达式后重试。"
    )
    try:
        completed = await _spawn_pi(run_id=run_id, token=token, port=port, prompt=prompt,
                                    context={"system_instance_id": sid}, timeout_s=timeout_s)
        if discover_workflows:
            await _spawn_pi(run_id=run_id, token=token, port=port, prompt=discover_prompt,
                            context={"system_instance_id": sid}, timeout_s=timeout_s)
        if policy_text:
            await _spawn_pi(run_id=run_id, token=token, port=port, prompt=policy_prompt,
                            context={"system_instance_id": sid}, timeout_s=timeout_s)
        return completed
    finally:
        server.should_exit = True
        await task


async def onboard(*, tenant: str, subsystem: str, openapi: dict, deploy: dict,
                  credentials: dict, system_instance_id: str | None = None,
                  lifecycle=None, discover_workflows: bool = True,
                  policy_text: str = "", include_tags: list[str] | None = None,
                  flows: list[dict] | None = None, coder=None,  # noqa: ANN001
                  use_codegen: bool = True, max_read_flows: int | None = None,
                  progress=None, timeout_s: float = 180.0) -> OnboardingReport:  # noqa: ANN001
    """接入一个系统实例(阶段一)。前置:PG 池已就绪。

    主路径 use_codegen=True(默认):goal 模式**自动生成代码** adapter——读流程(GET)自动生成,
      写/复合流程按 flows=[{flow, actions?, test_input}] 声明(写需测试输入才能沙箱)。
    use_codegen=False:旧声明式(pi 建连接器/复合流程/制度;policy_text/discover_workflows 仅此路径生效)。
    include_tags 圈定类别;lifecycle 给定则登记已发布 Skill 到「已发布」。
    """
    sid = system_instance_id or subsystem
    run_id = f"onb-{uuid4().hex[:8]}"
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant=tenant, system_instance_id=sid, subsystem=subsystem,
        openapi=openapi, deploy=deploy, credentials=credentials, policy_text=policy_text,
        include_tags=include_tags or []))
    token = secrets.token_hex(16)
    runs.register(run_id, token)
    # 先确定性发布环境画像(运行期 invoke 取 base_url+auth 用),走同一发布闸门
    await _publish_env_profile(run_id, sid, deploy)
    try:
        if use_codegen:
            completed = await _onboard_codegen(run_id, sid, flows or [], coder, max_read_flows, progress)
        else:
            completed = await _onboard_legacy(run_id, sid, token, discover_workflows=discover_workflows,
                                              policy_text=policy_text, timeout_s=timeout_s)
    finally:
        runs.unregister(run_id)
        materials.clear_run(run_id)

    # 收已发布(连接器 + 复合流程 + 代码 adapter;权威来源 = PG)。隐藏复合流程的步骤动作。
    repo = AssetRepository()
    scope = Scope(tenant=tenant, subsystem=Subsystem(subsystem))
    connectors = await repo.list_published(AssetType.CONNECTOR, scope)
    workflows = await repo.list_published(AssetType.WORKFLOW, scope)
    adapters = await repo.list_published(AssetType.ADAPTER, scope)
    hidden = {s.action for e in workflows for s in WorkflowSkillBody.model_validate(e.body).steps}
    visible = [e for e in (workflows + adapters + connectors)
               if e.body.get("action", e.asset_key) not in hidden]
    skills = sorted({e.body.get("action", e.asset_key) for e in visible})
    # §5:登记已发布 Skill 到生命周期(停在「已发布」)
    if lifecycle is not None:
        for e in visible:
            action = e.body.get("action", e.asset_key)
            await lifecycle.register_published(f"{subsystem}.{action}", Subsystem(subsystem), action, e.version)
    status = completed.get("status", "failed")
    log.info("onboard.done", tenant=tenant, system=sid, status=status, published=len(skills))
    return OnboardingReport(
        tenant=tenant, system_instance_id=sid, run_id=run_id,
        status=status, published_skills=skills,
        pi_final_text=completed.get("final_text", ""), error=completed.get("error"))
