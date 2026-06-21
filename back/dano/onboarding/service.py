"""阶段一接入服务:模板分流 → spawn pi 自主生成 → 收已发布资产 → 接入报告。

架构:本服务在网关同一事件循环里临时起 pi 工具服务(uvicorn task),spawn pi(Node Sidecar)。
pi 经 /_agent/tools/* 回调进**本进程同循环**(共用 PG 池,无跨循环问题)。pi 编排生成,
Python 控发布闸门。凭证只在 materials(进程内),不进 LLM。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
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
_OP_CONCURRENCY = 4        # 一个业务的多操作**并发**生成上限(操作互相独立;限并发防 LLM 限流/池耗尽)

# ── 流程端点收窄(防 planner 在超大 spec 上超时 + 防兜底瞎抓第一个无关接口)──
# 业务名里的通用动词,不当关键词(否则会匹配到一堆无关端点)
_FLOW_STOPWORDS = {"submit", "demo", "create", "start", "apply", "flow", "add", "new",
                   "do", "the", "request", "process"}
# 工作流通用契约 + 查询子串(发起/表单/提交/回查):**系统特定,现由 dialect.contract_tokens() 提供**。
# 主流程不再硬编码任何端点字面量——换框架只改 dialect(capabilities/oa_templates.py),泛化主力是业务关键词。

# 通用能力 → 中文短标题(供目录/剧本展示;办理类标题取 x-flow 流程名)
_OP_TITLES = {
    "query_my_todo": "查待办", "query_my_done": "查已办", "query_in_progress": "查在途",
    "query_my_drafts": "查我发起的", "query_status": "查流程状态",
    "cancel": "撤销/取回", "urge": "催办",
}


def _op_title(op_name: str, write: bool, business_meta: dict, business: str = "") -> str:
    """给操作起中文标题:通用能力用固定短名;办理类用 x-flow 流程名;兜底用业务名。"""
    if op_name in _OP_TITLES:
        return _OP_TITLES[op_name]
    name = (business_meta or {}).get("name")
    if write and name:
        return str(name)
    if write:
        base = re.sub(r"^((submit|create|apply|demo|do)[_-]+)+", "", (business or op_name).lower())
        return f"办理{base.replace('_', '')}" if base else "办理"
    return op_name


def _flow_keywords(flow: str, template_id: str = "") -> set[str]:
    """从流程名 + templateId 提业务关键词(如 submit_demo_overtime → {overtime})。"""
    toks: set[str] = set()
    for s in (flow or "", template_id or ""):
        for t in re.split(r"[_\-/.]+", s.lower()):
            t = t.replace("template", "")
            if len(t) >= 3 and t not in _FLOW_STOPWORDS:
                toks.add(t)
    return toks


def _scope_actions_for_flow(flow: str, template_id: str, actions: list[dict], *,
                            contract_tokens: tuple[str, ...] = (), cap: int = 24) -> list[dict]:
    """把候选端点收窄到本流程相关(业务关键词命中 + 共享契约端点);一个关键词都不命中则不收窄。

    解决两件事:① planner prompt 从几百个端点缩到十几个 → 不再超时;
    ② 兜底策略不再盲取 actions[0](超大 spec 里多半是无关的第一个端点,如 /monitor/cache)。
    contract_tokens 由 dialect 提供(系统特定端点子串),主流程不写死。
    """
    kws = _flow_keywords(flow, template_id)
    if not kws:
        return actions
    hit, contract = [], []
    for a in actions:
        hay = (f"{a.get('name', '')} {a.get('endpoint', '')} {a.get('summary', '')} "
               f"{' '.join(a.get('tags', []))}").lower()
        if any(k in hay for k in kws):
            hit.append(a)
        elif any(c in hay for c in contract_tokens):
            contract.append(a)
    if not hit:                       # 关键词没命中(英文 flow 名 vs 中文 OA 描述等)→ 退到共享工作流契约端点,
        return contract[:cap] if contract else actions      # 而非整份 spec(契约端点就是工作流业务的真实机制)
    return (hit + contract)[:cap]


def _make_status_probe(base: str, token: str):
    """造一个只读 GET 探针,返回 HTTP 状态码(网络异常返回 None)。仅用于探"端点存不存在"。"""
    import httpx

    from dano.infra.http import tls_verify
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def probe(url: str) -> int | None:
        try:
            async with httpx.AsyncClient(timeout=15, verify=tls_verify()) as c:
                r = await c.get(url, headers=headers)
            return r.status_code
        except Exception:  # noqa: BLE001 - 网络问题不应误删端点
            return None
    return probe


async def _fetch_oa_spec(base_url: str, token: str) -> dict | None:
    """探 OA 自己的 OpenAPI 目录(标准发现路径)→ 拿到**真实全量端点**,供能力发现映射真实路径。

    解决:焦点导入(只含提交两步)时,通用能力(查待办/已办/撤销…)端点不在文件里,LLM 只能猜路径、
    多半猜错被探针 404 掉。改为从 OA 真目录取真实端点 → LLM 按名映射(不猜)→ 探针确认。
    标准 OpenAPI 发现路径,非业务硬编码;探不到则回退原行为。
    """
    import httpx

    from dano.infra.http import tls_verify
    if not base_url:
        return None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    base = base_url.rstrip("/")
    for path in ("/v3/api-docs", "/v2/api-docs", "/swagger.json", "/openapi.json", "/api-docs"):
        try:
            async with httpx.AsyncClient(timeout=20, verify=tls_verify()) as c:
                r = await c.get(base + path, headers=headers)
            if r.status_code == 200:
                j = r.json()
                if isinstance(j, dict) and j.get("paths"):
                    log.info("oa_spec.fetched", path=path, paths=len(j.get("paths") or {}))
                    return j
        except Exception:  # noqa: BLE001 - 探不到换下一个路径
            continue
    return None


def _spec_to_actions(spec: dict) -> list[dict]:
    """OpenAPI → 端点字典清单(name/method/endpoint/summary),供能力发现。"""
    from dano.capabilities import doc_parser
    return [{"name": a.name, "method": a.method, "endpoint": a.endpoint, "summary": a.summary or ""}
            for a in doc_parser.parse_openapi(spec)]


async def _existing_endpoints(actions: list[dict], base_url: str, token: str, *, probe_status=None) -> list[dict]:
    """剔除"文档有、服务器没有"的幽灵端点(GET 探到 **404**)。其余(405/401/500/超时…)一律保留。

    解决:未实现却写进 swagger 的接口(如 /flow/xxx/start)带着完整示例诱导模型反复撞 404、白耗轮次。
    保守:只删确定不存在(404)的;带路径参数 {id} 的不探(缺参易误判);全被判幽灵则原样返回。
    """
    if not base_url or not actions:
        return actions
    base = base_url.rstrip("/")
    probe = probe_status or _make_status_probe(base, token)
    kept, dropped = [], []
    for a in actions:
        ep = a.get("endpoint") or ""
        if not ep or "{" in ep:                       # 路径参数端点不探,直接保留
            kept.append(a)
            continue
        url = base + (ep if ep.startswith("/") else "/" + ep)
        if (await probe(url)) == 404:                 # 仅 404=路由不存在 → 幽灵,剔除
            dropped.append(ep)
        else:
            kept.append(a)
    if dropped:
        log.info("onboard.phantom_dropped", count=len(dropped), endpoints=dropped[:10])
    return kept or actions


async def _expand_business_goals(run_id: str, sid: str, flow: str, raw_ti: dict,
                                 actions: list[dict], base_url: str, *, spawn=None,
                                 contract_tokens: tuple[str, ...] = (),
                                 oa_profile=None):  # noqa: ANN001
    """把一条业务 flow 展开成「操作集」的多个 GoalBrief(剖析器产操作 → 每操作一个 goal)。

    读操作(GET)→ 只读 adapter(crud_query,确定性);写操作 → LLM。失败/无操作 → None,上层回退单 flow。
    写操作继承业务测试输入(扁平字段 + __templateId__);读操作只给 __base_url__。
    oa_profile 给定则把 OA 通用能力(查待办/已办/在途/撤销/催办…)实例化进本业务操作集。
    """
    from dano.generation import GoalBrief
    from dano.generation.business_profiler import profile_business
    from dano.generation.operation_completer import complete_operations
    tid = str(raw_ti.get("templateId") or raw_ti.get("__templateId__") or "")
    scoped = _scope_actions_for_flow(flow, tid, actions, contract_tokens=contract_tokens)
    log.info("business.expand.start", flow=flow, scoped=len(scoped),
             endpoints=[a.get("endpoint") for a in scoped][:14])
    if spawn is None:
        from functools import partial

        from dano.generation.coder import openai_text_spawn
        spawn = partial(openai_text_spawn, tag="profiler")
    ops = await profile_business(flow, scoped, spawn=spawn)
    if not ops:
        log.warning("business.expand.empty", flow=flow, note="剖析无操作 → 回退单提交")
        return None
    # P2:把 OA 共享能力实例化进本业务操作集(已确认存在的才加;合成动作并入端点池)
    actions = list(actions)
    ops, synth = complete_operations(ops, oa_profile, template_id=tid)
    if synth:
        actions = actions + list(synth.values())
        log.info("business.expand.completed", flow=flow, added=list(synth.keys()))
    log.info("business.expand.ops", flow=flow,
             ops=[{"op": o["op"], "write": o["write"], "endpoints": o["endpoints"]} for o in ops])
    by_name = {a["name"]: a for a in actions}
    bmeta = next((a["business_meta"] for a in actions if a.get("business_meta")), {})  # x-flow → 标题取流程名
    goals = []
    for op in ops:
        op_actions = [by_name[n] for n in op["endpoints"] if n in by_name]
        if not op_actions:
            continue
        if op.get("write"):                               # 写:继承业务字段 + 模板常量
            if isinstance(raw_ti.get("values"), dict):
                ti = {**raw_ti["values"], "__base_url__": base_url}
                if raw_ti.get("templateId") is not None:
                    ti["__templateId__"] = raw_ti["templateId"]
            else:
                ti = {**{k: v for k, v in raw_ti.items() if k != "templateId"}, "__base_url__": base_url}
        else:                                             # 读:只读 adapter;带 templateId 供按业务过滤(可选)
            ti = {"__base_url__": base_url}
            if tid:
                ti["__templateId__"] = tid
        goals.append(GoalBrief(run_id=run_id, system_instance_id=sid, flow=op["op"],
                               actions=op_actions, test_input=ti, business=flow,
                               title=_op_title(op["op"], bool(op.get("write")), bmeta, flow)))   # 中文标题 + 同业务归组
    return goals or None


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


async def _onboard_codegen(run_id: str, sid: str, flows: list[dict], coder,  # noqa: ANN001
                           max_read_flows: int | None, progress=None,
                           expand_business: bool = False,
                           regenerate: bool = True) -> dict:
    """**已退役(逃生舱,默认不再走)**:goal 模式自动生成代码 adapter。

    保留仅为 use_codegen=True 的应急逃生舱;真机验证 pi 路径产 DSL v2 正常后,本函数 + generation/
    的 GenerationLoop/PiCoder/strategies/business_profiler 等 codegen 模块整体物理删除。原说明:
    goal 模式自动生成代码 adapter。

    读流程(GET)自动逐个生成只读 adapter(数量受 max_read_flows 限制,None=全部);
    写/复合流程按 flows=[{flow, actions?, test_input}] 声明(写操作需测试输入才能沙箱)。
    expand_business=True:把每条写流程经业务剖析器展开成「操作集」(办理+查在途+查状态+…),
      各自生成一个 adapter(像 lanxin 那样的多操作业务,而非单提交);失败回退单 flow。
    __base_url__ 由 deploy 自动注入到测试输入(沙箱时 adapter 需要),声明里只给业务字段。
    每条流程跑一遍 GenerationLoop(编码→测试→漏洞→审核→事实核查→发布)。
    """
    from dano.agent_tools import materials, tools as T
    from dano.assets.repository import AssetRepository
    from dano.generation import Budget, GenerationLoop, GoalBrief, LlmPlanner, PiCoder
    from dano.generation.strategies import get_strategy, select_strategy
    from dano.onboarding.evidence import collect_evidence, make_http_probe
    from dano.shared.enums import AssetType, Subsystem
    from dano.shared.models import Scope

    from dano.capabilities import oa_templates
    mat = materials.get(run_id, sid)
    base_url = (mat.deploy or {}).get("base_url", "") if mat else ""
    spec = mat.openapi if mat else {}
    token = (mat.credentials or {}).get("token", "") if mat else ""
    probe = make_http_probe(base_url, token) if (base_url and token) else None
    # 系统方言:复合契约/端点收窄等系统特定知识全从 dialect 取,主流程零字面量(换框架只改 dialect)
    dialect = oa_templates.match_template(spec)
    contract_tokens = dialect.contract_tokens() if dialect else ()
    # 沉淀:已发布同名 adapter 直接复用,不重生成(模型一次跑通后,后续接入零成本)
    published_keys = ({e.asset_key for e in await AssetRepository().list_published(
        AssetType.ADAPTER, Scope(tenant=mat.tenant, subsystem=Subsystem(mat.subsystem)))} if mat else set())
    parsed = await T.parse_spec(run_id, {"system_instance_id": sid, "use_llm_classify": True})
    # LLM 识别的框架/成功约定(parse_spec 已算好)→ 喂进证据,作 planner 的成败规则 grounding
    convention = {"name": parsed.get("template"), "success_rule": parsed.get("success_rule")}
    actions = parsed.get("actions", [])
    log.info("codegen.spec", actions=len(actions), template=parsed.get("template"),
             success_rule=parsed.get("success_rule"), categories=len(parsed.get("categories") or {}),
             flows=len(flows), expand_business=expand_business)
    by_name = {a["name"]: a for a in actions}
    # P0:OA 层探一次(框架 + 通用工作流能力,LLM+探针)→ 全业务共享,供操作发现实例化
    oa_profile = None
    if expand_business and coder is None:
        from dano.generation.oa_profile import build_oa_profile
        cap_probe = None
        if base_url and token:                            # 能力探针:接收**端点路径**,内部补 base_url(只 GET)
            _raw = _make_status_probe(base_url.rstrip("/"), token)

            async def cap_probe(path: str):  # noqa: ANN202
                return await _raw(base_url.rstrip("/") + (path if path.startswith("/") else "/" + path))
        cap_actions = actions                             # 默认用导入清单;能取到 OA 真目录则用真目录(端点更全)
        if base_url and token:
            full_spec = await _fetch_oa_spec(base_url, token)
            if full_spec:
                try:
                    cap_actions = _spec_to_actions(full_spec) or actions
                except Exception as e:  # noqa: BLE001
                    log.warning("oa_spec.parse_failed", error=str(e))
        try:
            oa_profile = await build_oa_profile(
                cap_actions, framework=parsed.get("template") or "",
                success_rule=parsed.get("success_rule") or "",
                probe=cap_probe)
        except Exception as e:  # noqa: BLE001 - 探测失败不阻断,业务仍可单独发现
            log.warning("codegen.oa_profile_failed", error=str(e))
    goals: list[GoalBrief] = []
    declared: set[str] = set()
    for f in flows:                                       # 写/复合流程:调用方声明 + 测试输入
        raw_ti = f.get("test_input") or {}
        if expand_business and coder is None:             # 业务展开(仅真实路径;注入 coder 的测试不触发实时剖析)
            try:                                          # 每业务独立:一个业务展开失败不连累其它业务
                exp = await _expand_business_goals(run_id, sid, f["flow"], raw_ti, actions, base_url,
                                                   contract_tokens=contract_tokens, oa_profile=oa_profile)
            except Exception as e:  # noqa: BLE001 - 展开失败回退该业务单提交,不阻断整体接入
                log.warning("business.expand.error", flow=f["flow"], error=str(e))
                exp = None
            if exp:
                goals.extend(exp)
                declared.update(a["name"] for g in exp for a in g.actions)
                if progress:
                    progress({"type": "business_expanded", "flow": f["flow"], "ops": [g.flow for g in exp]})
                continue
        fa = [by_name[n] for n in (f.get("actions") or []) if n in by_name] or actions
        # 工作流复合 {templateId, values} → 扁平业务字段 + 常量 __templateId__(逐字段 schema / 运行期注入)
        if isinstance(raw_ti.get("values"), dict):
            ti = {**raw_ti["values"], "__base_url__": base_url}
            if raw_ti.get("templateId") is not None:
                ti["__templateId__"] = raw_ti["templateId"]
        else:
            ti = {**raw_ti, "__base_url__": base_url}
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
    log.info("codegen.goals_planned", total=len(goals), goals=[g.flow for g in goals],
             expand_business=expand_business)
    if progress:
        progress({"type": "plan", "flows": [g.flow for g in goals]})
    sem = asyncio.Semaphore(_OP_CONCURRENCY)

    async def _gen_one(idx: int, g) -> bool:              # noqa: ANN001
        """生成一个操作的 adapter(供并发调度)。失败不连累其它操作;错误带 traceback 可定位。"""
        is_read = bool(g.actions) and all((a.get("method") or "GET").upper() == "GET" for a in g.actions)
        log.info("codegen.goal.start", flow=g.flow, idx=idx, total=len(goals),
                 is_read=is_read, n_actions=len(g.actions), business=getattr(g, "business", ""))
        if not regenerate and coder is None and g.flow in published_keys:  # 沉淀复用(opt-in):已发布同名直接跳过
            log.info("codegen.goal.reused", flow=g.flow)
            if progress:
                progress({"type": "flow_start", "flow": g.flow, "index": idx, "total": len(goals), "route": "reused"})
                progress({"type": "flow_done", "flow": g.flow, "ok": True, "rejections": 0, "asset_id": None})
            return True
        async with sem:                                   # 限并发:同时最多 _OP_CONCURRENCY 个操作在跑
            try:
                tid = str(g.test_input.get("__templateId__", ""))
                if is_read:                               # 读流程:通用 crud_query(无副作用)
                    g_coder, planner, strat, src = (coder or PiCoder()), None, select_strategy(g.actions), "read"
                else:                                     # 写/复合:全模型驱动(证据 + 真报错迭代 + 双层回灌)
                    g_coder = coder or PiCoder()
                    planner = None if coder is not None else LlmPlanner()
                    strat, src = get_strategy("simple_http"), "llm"
                    g.budget = Budget(max_iters=6)
                    base = g.actions if (g.actions and len(g.actions) < len(actions)) else actions
                    scoped = _scope_actions_for_flow(g.flow, tid, base, contract_tokens=contract_tokens)
                    scoped = await _existing_endpoints(scoped, base_url, token)   # 剔除幽灵接口
                    g.actions = scoped
                    keep = {a["name"] for a in scoped}
                    g.evidence = (await collect_evidence(spec, template_id=tid, probe=probe,
                                                         convention=convention, include_names=keep)).model_dump()
                    log.info("codegen.goal.evidence", flow=g.flow, scoped=len(scoped),
                             endpoints=[a.get("endpoint") for a in scoped][:12])
                    if not tid:                           # 前端没传 templateId → 从 x-flow 业务规则兜底
                        bm = g.evidence.get("business_meta") or {}
                        tid = str(bm.get("templateId") or "")
                        if tid:
                            g.test_input["__templateId__"] = tid
                            log.info("codegen.tid_from_xflow", flow=g.flow, template_id=tid)
                    # 契约合成:dialect 现场探出该业务真实提交契约 → 注入证据 + 框架真实成功判定。
                    # 系统特定逻辑(端点/步骤/成功约定)全在 dialect,主流程零字面量。
                    if tid and base_url and token and dialect is not None:
                        contract = await dialect.discover_contract(tid, base_url, token)
                        if contract:
                            # 把证据**只留契约涉及的提交端点**(有序,最后一个 = 最终提交步),剔除中间形态。
                            submit_eps = dialect.submit_endpoints()
                            submit_step = submit_eps[-1] if submit_eps else ""
                            acts = [a for a in g.evidence.get("actions", []) if (a.get("endpoint") or "") in submit_eps]
                            for a in acts:
                                if (a.get("endpoint") or "") == submit_step:
                                    a["request_example"] = contract["submit_example"]   # 模型照真实契约填
                            if acts:
                                g.evidence["actions"] = acts
                            g.evidence["synthesized_contract"] = contract
                            cfields = contract["fields"]
                            # 探到的表单字段 → 直接当 skill 的入参(user_fields/required_fields/field_docs),
                            # 否则导出脚本 FIELDS=[],调用方不知道要填 title/amount/...(实质缺陷)。
                            g.plan_overrides = {
                                "success_rule": contract["success_rule"],   # code==200,grounded
                                "fact_check": None,   # 两步真建实例;code==200 + 真 procInsId 足证,跳过额外回查
                                "user_fields": [f["name"] for f in cfields],
                                "required_fields": [f["name"] for f in cfields if f.get("required")],
                                "field_docs": {f["name"]: (f.get("label") or f["name"]) for f in cfields},
                            }
                            log.info("codegen.contract_synth", flow=g.flow,
                                     fields=[f["name"] for f in cfields],
                                     endpoints=[a.get("endpoint") for a in acts])
                if progress:
                    progress({"type": "flow_start", "flow": g.flow, "index": idx, "total": len(goals), "route": src})
                r = await GenerationLoop(g_coder, planner=planner, on_event=progress).run(g, strat)
                log.info("codegen.goal.done", flow=g.flow, route=src, ok=r.ok,
                         rejections=r.rejections, asset_id=str(r.asset_id) if r.asset_id else None,
                         reason=getattr(r, "reason", None))
                if progress:
                    progress({"type": "flow_done", "flow": g.flow, "ok": r.ok,
                              "rejections": r.rejections, "asset_id": r.asset_id})
                return r.ok
            except Exception as e:  # noqa: BLE001 - 单操作失败不连累其它;记可定位错误(含 traceback)
                log.exception("codegen.goal.error", flow=g.flow, error=repr(e))
                if progress:
                    progress({"type": "flow_done", "flow": g.flow, "ok": False, "rejections": 0,
                              "asset_id": None, "error": str(e)})
                return False

    log.info("codegen.parallel.start", total=len(goals), concurrency=_OP_CONCURRENCY)
    results = await asyncio.gather(*(_gen_one(idx, g) for idx, g in enumerate(goals)))
    oks = sum(1 for ok in results if ok)
    log.info("codegen.parallel.done", oks=oks, total=len(goals))
    return {"status": "completed",
            "final_text": f"goal 模式代码生成:{oks}/{len(goals)} 个流程发布"}


async def _onboard_legacy(run_id: str, sid: str, token: str, *, discover_workflows: bool,
                          policy_text: str, timeout_s: float) -> dict:
    """**单一/默认接入路径**:起工具服务 + spawn pi(自主发现)建连接器(隐藏积木)+ 复合 DSL v2
    业务流程(前置/分支/计算/消歧/不变量,grounded)+ 制度。返回 completed。"""
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
        f"0) 先调 get_business_rules({sid}) 拿人工登记的业务规则(阈值/审批链)+ 日历源 holidays —— "
        f"分支 condition / 前置 / 不变量 / compute 的 business_days **必须据此 grounding**,没有就别造规则。\n"
        f"1) 调 parse_spec({sid}) 看动作清单,重点看每个动作的 params_out(出参)和 tags(阶段)。\n"
        f"2) 找需要**串联**的动作——信号:某动作出参(如 taskId/procInsId/procDefId)正是另一动作所需的入参;"
        f"或 tags 表明先后阶段(发起→提交→审批)。\n"
        f"3) 对发现的流程,先用 get_action_schema 看清各步请求体**嵌套结构与示例**,再用 draft_workflow 编排:"
        f"steps 的 inputs 用 'step:前一步动作.出参点路径'(如 step:start_leave_flow.data.taskId)串联、"
        f"'const:值' 填固定项、'field:名' 暴露给用户;user_fields/required_fields 给用户要填的业务字段。\n"
        f"3b) **若发现真实业务逻辑**(DSL v2,仅在证据支持时才加,臆造会被 grounding 拒):用 compute 步做派生计算"
        f"(只准 business_days/sum_ 等审计函数,如 leave_days=business_days(startDate,endDate))、branch 步按 condition 走不同分支、"
        f"select 步从某查询候选里选(消歧);preconditions 放办理前校验(如余额≥天数,evidence 给回查动作)、"
        f"invariants 放办理后回查证实。动作必须已发布、表达式只准用已声明字段/变量+审计函数。\n"
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


async def onboard(*, tenant: str, subsystem: str, openapi, deploy: dict,  # noqa: ANN001
                  credentials: dict, system_instance_id: str | None = None,
                  lifecycle=None, discover_workflows: bool = True,
                  policy_text: str = "", include_tags: list[str] | None = None,
                  business_rules: list[dict] | None = None,   # 人工业务规则(阈值/审批链)→ pi grounding
                  holidays: list[str] | None = None,          # 日历源 → env_profile,运行期注入 business_days
                  flows: list[dict] | None = None, coder=None,  # noqa: ANN001
                  use_codegen: bool = False, max_read_flows: int | None = None,   # 默认单一 pi 路径(codegen 已退役;True=逃生舱)
                  expand_business: bool = True,        # 默认开:一个业务 → 多操作剧本(办理+查在途+查状态…)
                  regenerate: bool = True,             # 默认开:重新接入同一业务=重新生成(覆盖旧版);关掉才复用已发布
                  progress=None, timeout_s: float = 180.0) -> OnboardingReport:  # noqa: ANN001
    """接入一个系统实例(阶段一)。前置:PG 池已就绪。

    openapi 接受**任意格式**(入口先归一化成规范 OpenAPI):OpenAPI/Swagger 字典原样透传(零 LLM);
      Postman 集合确定性转换;非结构化(HTML/Markdown/纯文本)用 LLM 抽成接口清单再合成 OpenAPI。
    **唯一/默认路径 use_codegen=False**:pi agent 自主发现并产**声明式 DSL v2 workflow**(单一事实源:
      连接器=隐藏积木 + 复合业务 Skill;前置/分支/计算/消歧/不变量,全部 grounded)。
    use_codegen=True 为**已退役的 codegen 逃生舱**(产代码 adapter;真机验 pi 路径后物理删除),日常勿用。
    expand_business=True:把每条写流程经业务剖析器展开成「操作集」(办理+查在途+查状态+撤销…),
      各操作各生成一个 adapter(lanxin 式多操作业务);剖析失败则回退该流程的单提交。
    include_tags 圈定类别;lifecycle 给定则登记已发布 Skill 到「已发布」。
    """
    sid = system_instance_id or subsystem
    run_id = f"onb-{uuid4().hex[:8]}"
    log.info("onboard.start", tenant=tenant, subsystem=subsystem, run_id=run_id,
             use_codegen=use_codegen, expand_business=expand_business,
             regenerate=regenerate, flows=len(flows or []))
    from dano.onboarding.ingest import normalize_to_spec
    spec = await normalize_to_spec(openapi)        # 入口归一化:任何格式 → 规范 OpenAPI(结构化零 LLM)
    log.info("onboard.normalized", run_id=run_id, paths=len((spec or {}).get("paths") or {}))
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant=tenant, system_instance_id=sid, subsystem=subsystem,
        openapi=spec, deploy=deploy, credentials=credentials, policy_text=policy_text,
        include_tags=include_tags or [], business_rules=business_rules or [],
        holidays=holidays or []))
    # 接入用的 OA 凭证(来自页面)落进运行期凭证库 → 运行期 invoke 才解析得到 token,
    # 否则 adapter 拼出 `Bearer `(空)→ Illegal header value。键=租户/系统key(如 abc/oa)。
    if credentials:
        from dano.execution.connectors.executor import system_key_for
        from dano.infra.credentials import set_runtime_credential
        set_runtime_credential(f"{tenant}/{system_key_for(Subsystem(subsystem))}", dict(credentials))
    token = secrets.token_hex(16)
    runs.register(run_id, token)
    # 先确定性发布环境画像(运行期 invoke 取 base_url+auth+日历源 用),走同一发布闸门
    await _publish_env_profile(run_id, sid, deploy, holidays=holidays)
    try:
        if use_codegen:        # 逃生舱(已退役):代码 adapter codegen,日常不走;真机验 pi 路径后物理删除
            completed = await _onboard_codegen(run_id, sid, flows or [], coder, max_read_flows,
                                               progress, expand_business, regenerate)
        else:                  # 默认单一路径:pi agent 自主发现 → 声明式 DSL v2 workflow
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
