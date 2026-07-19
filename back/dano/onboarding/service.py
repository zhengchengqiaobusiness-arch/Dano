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

from dano.agent_tools import materials, progress as progress_bus, runs
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
    lifecycle_pending: list[dict] = Field(default_factory=list)
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
    """spawn Node Sidecar,送 start_run,读 JSONL 直到 run_completed。env 白名单。

    全程记日志(便于真机定位 pi 空跑/报错):spawn 配置 / pi 每个事件 / **stderr 每行** /
    最终结果(status/事件数/final_text 头/returncode)。pi 没回 run_completed 时把 stderr 尾抬进 error。
    """
    from dano.config import get_settings
    s = get_settings()
    env = {k: os.environ[k] for k in _OS_ENV_WHITELIST if k in os.environ}
    # pi agent 的 LLM 配置走 config.py(不再靠前端 /settings 写 env);真实进程环境变量可覆盖
    env.update({"DANO_PI_API_KEY": s.pi_api_key or "", "DANO_PI_BASE_URL": s.pi_base_url or "",
                "DANO_PI_MODEL": s.pi_model or "", "DANO_PI_PROVIDER": s.pi_provider or ""})
    env.update({k: os.environ[k] for k in _PI_ENV if k in os.environ})
    env.update({"DANO_AGENT_TOKEN": token, "DANO_AGENT_BASE_URL": f"http://127.0.0.1:{port}",
                "DANO_AGENT_RUN_ID": run_id, "PI_STUB": "0"})
    log.info("pi.spawn", run_id=run_id, port=port, model=s.pi_model,
             provider=s.pi_provider or "openai-compat", base_url=s.pi_base_url, key_set=bool(s.pi_api_key))
    proc = await asyncio.create_subprocess_exec(
        "node", str(BACK_DIR / "agent" / "run_pi.mjs"), cwd=str(BACK_DIR), env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    start = json.dumps({"type": "start_run", "run_id": run_id, "prompt": prompt,
                        "context": context, "budget": {"timeout_s": int(timeout_s)}}) + "\n"
    proc.stdin.write(start.encode())
    await proc.stdin.drain()
    completed: dict = {}
    stderr_buf: list[str] = []
    n_events = 0

    # pi SDK 每条消息更新都往 stderr 打一行(message_update/start/end、delta、turn/agent 边界)→ 纯噪声,不刷日志
    _noise = ("ev: message_update", "ev: message_start", "ev: message_end", "ev: text",
              "ev: delta", "ev: turn_start", "ev: turn_end", "ev: agent_start", "ev: agent_end")

    async def _read_stderr() -> None:
        assert proc.stderr
        async for raw in proc.stderr:
            s = raw.decode(errors="replace").rstrip()
            if s:
                stderr_buf.append(s)                       # 仍留缓冲,出错时抬尾巴进 error
                if not any(n in s for n in _noise):        # 路由噪声不刷 warning,只留真错/真事件
                    log.warning("pi.stderr", run_id=run_id, line=s[:500])

    async def _read_stdout() -> None:
        nonlocal completed, n_events
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                log.info("pi.stdout_raw", run_id=run_id, line=line[:300])
                continue
            n_events += 1
            if ev.get("type") == "run_completed":
                completed = ev
                return
            log.info("pi.event", run_id=run_id, ev_type=ev.get("type"),
                     detail={k: str(v)[:160] for k, v in ev.items() if k != "type"})

    stderr_task = asyncio.create_task(_read_stderr())
    try:
        await asyncio.wait_for(_read_stdout(), timeout=timeout_s)
    except asyncio.TimeoutError:
        completed = {"status": "failed", "error": "timeout"}
        log.warning("pi.timeout", run_id=run_id, timeout_s=timeout_s)
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        stderr_task.cancel()
        try:
            await stderr_task
        except BaseException:  # noqa: BLE001 - 取消/读尾异常都吞掉
            pass
    final_text = (completed.get("final_text") or "") if completed else ""
    log.info("pi.result", run_id=run_id, status=(completed.get("status") if completed else "no_completion"),
             events=n_events, tool_events=completed.get("tool_events") if completed else None,
             skills_loaded=completed.get("skills_loaded") if completed else None,
             final_chars=len(final_text), final_head=final_text[:300],
             error=completed.get("error") if completed else None,
             returncode=proc.returncode, stderr_lines=len(stderr_buf))
    if not completed:                       # pi 没回 run_completed:多半启动即崩,把 stderr 尾抬进 error
        tail = " | ".join(stderr_buf[-8:])
        log.warning("pi.no_completion", run_id=run_id, stderr_tail=tail[:1000])
        completed = {"status": "failed", "error": f"pi 未返回 run_completed;stderr 尾: {tail[:500]}"}
    return completed


async def _publish_env_profile(run_id: str, sid: str, deploy: dict,
                               holidays: list[str] | None = None) -> None:
    """确定性发布环境画像(base_url+auth+日历源 来自 deploy/onboard),走同一草案→验证→发布闸门。"""
    from dano.agent_tools import tools as T
    from dano.shared.asset_bodies import AuthConfig, EnvProfileBody
    body = EnvProfileBody(
        deploy=deploy.get("deploy", "saas"), worker_location="平台托管", intranet_access="public",
        account_type=deploy.get("account_type", "test"),
        base_url=deploy.get("base_url", ""), auth=AuthConfig.model_validate(deploy.get("auth", {})),
        holidays=list(holidays or []),
    ).model_dump()
    d = await T.save_draft(run_id, {"system_instance_id": sid, "asset_type": "env_profile",
                                    "asset_key": "env_profile", "body": body})
    h = await T.health_check(run_id, {"asset_draft_id": d["asset_draft_id"]})
    await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
                                   "validation_run_ids": h["validation_run_ids"]})


async def _run_pi_onboarding(run_id: str, sid: str, token: str, *, discover_workflows: bool,
                             policy_text: str, timeout_s: float) -> dict:
    """**单一/默认接入路径**:起工具服务 + spawn pi(自主发现)建连接器(隐藏积木)+ 复合 DSL v2
    业务流程(前置/分支/计算/消歧/不变量,grounded)+ 制度。返回 completed。

    """
    server, task, port = await _start_tool_server()
    # 复合优先:把真实业务做成**一个复合 Skill**(多步串成一个能力),步骤连接器是隐藏积木,只露业务。
    prompt = (
        f"接入系统实例 {sid}。目标:把真实业务做成**复合业务 Skill**(多步串成一个能力),步骤接口隐藏,只露业务。\n"
        f"0) 先调 get_selected_flows({sid}) 看用户**人工勾选的业务**(templateId+测试值)——只针对这些做;"
        f"再调 get_business_rules({sid}) 拿业务规则(阈值/审批链)+ 日历 holidays(分支/前置/不变量/天数计算**必须据此 grounding**,没有别造);"
        f"规则按 kind 用:**precondition**→加进 draft_workflow 的 preconditions(用已声明字段,如 amount>0);"
        f"**server_side/approval_chain**→是服务端行为(升级加签/审批链/记账),写进 preview 文案说明,**不**做客户端分支。规则非空时 draft_workflow 传 preview=true。\n"
        f"1) 调 parse_spec({sid}) 看动作清单,重点看 params_out(出参)和 tags(阶段),判断哪些要**串联**"
        f"(信号:某动作出参如 taskId/procInsId 正是另一动作入参;或 tags 表先后阶段)。\n"
        f"2) 对**每条复合流程**(需串联多步才完成,如 发起→提交):\n"
        f"   a) 先 get_action_schema(action=动作名,**用 parse_spec 返回的真实 name,别自造**)看清各步请求体嵌套结构与示例;\n"
        f"   b) 对**每个步骤动作**:draft_connector(action=动作名, **as_step=true**) → sandbox_test(asset_draft_id, **as_step=true**)"
        f" → publish_asset(asset_draft_id, validation_run_ids=连接测试的, review_run_ids=[])。"
        f"(as_step 步骤连接器:只需连得通即可发布、免单独沙箱与评审、永不单独上架;真实校验在 d 整链做。)\n"
        f"   c) draft_workflow(action=业务名如 submit_xxx, title, steps=[各步 {{action, inputs:目标路径→来源}}], user_fields/required_fields,"
        f" 证据支持时再加 compute/branch/preconditions/invariants):inputs 来源 const:常量 / field:用户字段 /"
        f" 'step:前一步动作.出参点路径'(如 step:<发起动作>.data.taskId)串联;规则取自 get_business_rules,grounding 不住别加。\n"
        f"   d) sandbox_test_workflow(asset_draft_id, cases=[用 get_selected_flows 的测试值,覆盖每个分支臂])**整条真跑**;"
        f"passed 为真后 request_review(asset_draft_id)(评的是复合流程);**仅 all_passed 为真才** "
        f"publish_asset(asset_draft_id, validation_run_ids=cases 的, review_run_ids=评审的)。不过按返回原因修正后重试,过不了跳过该业务。\n"
        f"3) 查询接口分两种,别混:\n"
        f"   - **前置/辅助查询**(某业务办理过程要用的:开表单/查模板/查字段枚举/查余额)→ draft_connector(action,"
        f" **internal=true, business=<所属业务,如 请假>**):它是该业务的**内部步骤**,免单独评审、**永不单独上架**(和步骤连接器一样隐藏)。\n"
        f"   - **真正独立的用户级查询业务**(用户会主动发起的,如查我的待办/查工单进度)→ draft_connector(action,**不传 as_step/internal**)"
        f" → sandbox_test(带 sample_inputs) → request_review → publish_asset(完整闸门)。\n"
        f"4) 一句话总结发布了哪些**业务 Skill**(只数复合业务 + 独立用户级业务,不数隐藏步骤 / 前置查询)。\n"
        f"红线:动作名用 parse_spec 的真实 name;串联来源用真实出参路径;表达式只准已声明字段/变量+审计函数;臆造会被 grounding 拒。"
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
        log.info("onboard.pi.phase", run_id=run_id, phase="compose", note="pi 复合优先:建步骤+编排+整链验证")
        progress_bus.emit(run_id, {"type": "phase", "phase": "compose", "note": "pi 复合优先:发现并编排业务流程"})
        completed = await _spawn_pi(run_id=run_id, token=token, port=port, prompt=prompt,
                                    context={"system_instance_id": sid}, timeout_s=timeout_s)
        if policy_text:
            log.info("onboard.pi.phase", run_id=run_id, phase="policy", note="pi 抽制度规则")
            progress_bus.emit(run_id, {"type": "phase", "phase": "policy", "note": "pi 抽取制度规则"})
            await _spawn_pi(run_id=run_id, token=token, port=port, prompt=policy_prompt,
                            context={"system_instance_id": sid}, timeout_s=timeout_s)
        log.info("onboard.pi.done", run_id=run_id)
        return completed
    finally:
        server.should_exit = True
        await task


async def onboard(*, tenant: str, subsystem: str, openapi, deploy: dict,  # noqa: ANN001
                  credentials: dict, system_instance_id: str | None = None,
                  lifecycle=None, lifecycle_reconciler=None, discover_workflows: bool = True,
                  policy_text: str = "", include_tags: list[str] | None = None,
                  business_rules: list[dict] | None = None,   # 人工业务规则(阈值/审批链)→ pi grounding
                  holidays: list[str] | None = None,          # 日历源 → env_profile,运行期注入 business_days
                  flows: list[dict] | None = None,
                  progress=None, timeout_s: float = 1800.0) -> OnboardingReport:  # noqa: ANN001
    """接入一个系统实例(阶段一)。前置:PG 池已就绪。

    timeout_s:单次 pi 会话预算。复合优先一条龙(全量 spec 发现 ~4min + 整链真跑 + 三模型评审,
    评审在共享端点拥塞时单模型可达 ~180s)较慢,给足 30 分钟,避免在评审重试时耗尽预算。

    openapi 接受**任意格式**(入口先归一化成规范 OpenAPI):OpenAPI/Swagger 字典原样透传(零 LLM);
      Postman 集合确定性转换;非结构化(HTML/Markdown/纯文本)用 LLM 抽成接口清单再合成 OpenAPI。
    唯一路径:pi agent 自主发现并产**声明式 DSL v2 workflow**(单一事实源:
      连接器=隐藏积木 + 复合业务 Skill;前置/分支/计算/消歧/不变量,全部 grounded)。
    include_tags 圈定类别;lifecycle 给定则登记已发布 Skill 到「已发布」。
    """
    sid = system_instance_id or subsystem
    run_id = f"onb-{uuid4().hex[:8]}"
    log.info("onboard.start", tenant=tenant, subsystem=subsystem, run_id=run_id,
             route="pi", flows=len(flows or []))
    from dano.onboarding.ingest import normalize_to_spec
    spec = await normalize_to_spec(openapi)        # 入口归一化:任何格式 → 规范 OpenAPI(结构化零 LLM)
    log.info("onboard.normalized", run_id=run_id, paths=len((spec or {}).get("paths") or {}))
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant=tenant, system_instance_id=sid, subsystem=subsystem,
        openapi=spec, deploy=deploy, credentials=credentials, policy_text=policy_text,
        include_tags=include_tags or [], business_rules=business_rules or [],
        holidays=holidays or [], selected_flows=flows or []))
    # 接入用的 OA 凭证(来自页面)落进运行期凭证库 → 运行期 invoke 才解析得到 token,
    # 键=租户/系统key(如 abc/oa)。
    if credentials:
        from dano.execution.connectors.executor import system_key_for
        from dano.infra.credentials import set_runtime_credential
        set_runtime_credential(f"{tenant}/{system_key_for(Subsystem(subsystem))}", dict(credentials))
    token = secrets.token_hex(16)
    runs.register(run_id, token)
    if progress is not None:                    # pi 工具回调 / 各步进度 → 推给接入向导 job
        progress_bus.register(run_id, progress)
    log.info("onboard.route", run_id=run_id, path="pi")
    progress_bus.emit(run_id, {"type": "phase", "phase": "env_profile", "note": "发布环境画像"})
    # 先确定性发布环境画像(运行期 invoke 取 base_url+auth+日历源 用),走同一发布闸门
    await _publish_env_profile(run_id, sid, deploy, holidays=holidays)
    log.info("onboard.env_profile_published", run_id=run_id, base_url=deploy.get("base_url", ""),
             holidays=len(holidays or []))
    try:
        completed = await _run_pi_onboarding(run_id, sid, token, discover_workflows=discover_workflows,
                                             policy_text=policy_text, timeout_s=timeout_s)
    finally:
        runs.unregister(run_id)
        progress_bus.unregister(run_id)
        materials.clear_run(run_id)

    # 收已发布(连接器 + 复合流程;权威来源 = PG)。隐藏复合流程的步骤动作。
    repo = AssetRepository()
    scope = Scope(tenant=tenant, subsystem=Subsystem(subsystem))
    connectors = await repo.list_published(AssetType.CONNECTOR, scope)
    workflows = await repo.list_published(AssetType.WORKFLOW, scope)
    from dano.shared.asset_bodies import asset_internal
    hidden = {s.action for e in workflows for s in WorkflowSkillBody.model_validate(e.body).steps}
    # 隐藏:复合流程的步骤动作 + 任何 internal 资产(步骤连接器 / 前置查询)——不上架、不登记生命周期
    visible = [e for e in (workflows + connectors)
               if e.body.get("action", e.asset_key) not in hidden and not asset_internal(e.body)]
    skills = sorted({e.body.get("action", e.asset_key) for e in visible})
    # §5:登记已发布 Skill 到生命周期(停在「已发布」)
    lifecycle_pending: list[dict] = []
    if lifecycle_reconciler is not None:
        for e in visible:
            action = e.body.get("action", e.asset_key)
            result = await lifecycle_reconciler.register_or_defer(
                skill_id=f"{subsystem}.{action}",
                subsystem=Subsystem(subsystem),
                action=action,
                asset_version=e.version,
            )
            if result.get("lifecycle_pending"):
                lifecycle_pending.append({"skill_id": f"{subsystem}.{action}", **result})
    elif lifecycle is not None:
        for e in visible:
            action = e.body.get("action", e.asset_key)
            await lifecycle.register_published(f"{subsystem}.{action}", Subsystem(subsystem), action, e.version)
    status = completed.get("status", "failed")
    log.info("onboard.done", tenant=tenant, system=sid, status=status, published=len(skills))
    return OnboardingReport(
        tenant=tenant, system_instance_id=sid, run_id=run_id,
        status=status, published_skills=skills,
        lifecycle_pending=lifecycle_pending,
        pi_final_text=completed.get("final_text", ""), error=completed.get("error"))
