"""Dano 网关(阶段一+三对外面)。

- 接入:POST /onboarding(pi 自主生成 → 发布)
- 契约:GET /v1/skills(标准 function-calling 契约,租户隔离)/ GET /v1/skills/{id}
- 瘦执行:POST /v1/skills/{id}/invoke(前端只给 skill_id+input;后端取资产/凭证/断言执行)
- 资产:GET /assets/published
后端不做 NL 意图/多智能体编排(阶段二交前端)。凭证经 Vault/env,平台只存引用。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dano.assets.repository import AssetRepository
from dano.catalog.manifest import build_function_tools, build_manifests, skill_id_of
from dano.execution.connectors.auth import AuthManager
from dano.execution.connectors.executor import RealActionExecutor, SystemEndpoint, system_key_for
from dano.execution.harness.harness import Harness
from dano.orchestrator.orchestrator import Orchestrator
from dano.orchestrator.skills import SkillRegistry
from dano.registry import InMemoryRegistry, PgRegistry, TenantRecord
from dano.shared.asset_bodies import EnvProfileBody
from dano.shared.enums import AssetType, Subsystem
from dano.shared.models import Scope

from dano.lifecycle.state_machine import SkillLifecycle
from dano.resilience.circuit_breaker import InMemoryCounter
from dano.shared.enums import SkillState

log = structlog.get_logger(__name__)
# 三件套只是**原型常量**(空租户兜底);真实系统由 _tenant_subsystems 从该租户已发布资产里发现,不写死。
_PROTOTYPE_SUBSYSTEMS = [Subsystem.OA, Subsystem.TICKET, Subsystem.REIMBURSE]


async def _tenant_subsystems(tenant: str) -> list[Subsystem]:
    """该租户**实际拥有**的系统实例(发现式,支持任意系统);发现为空(尚无发布)才退回原型常量兜底。"""
    try:
        subs = await repo.distinct_subsystems(tenant)
    except Exception as e:  # noqa: BLE001 —— DB 异常时不致整体 500,退原型
        log.warning("tenant_subsystems.discover_failed", tenant=tenant, error=str(e))
        subs = []
    return subs or _PROTOTYPE_SUBSYSTEMS
_registry = InMemoryRegistry()       # DB 就绪换 PgRegistry(lifespan)
_lifecycle = SkillLifecycle()        # 流程12 Skill 生命周期(进程内;可换 PgSkillStore)
_breaker = InMemoryCounter()         # 流程10 失败计数/熔断


@asynccontextmanager
async def lifespan(app: FastAPI):
    from dano.infra.db import close_pool, init_pool, run_migrations
    from dano.infra.logging import configure_logging
    configure_logging()                    # **先配日志**:否则后台看不到任何记录
    log.info("gateway.starting")
    global _registry, _lifecycle, _breaker
    try:
        await init_pool()
        await run_migrations()
        _registry = PgRegistry()
        # 生命周期/失败计数落 PG:重启后 Skill 状态、暂停态、失败计数不丢(否则已熔断 Skill 复活)
        from dano.lifecycle.pg_store import PgSkillStore
        from dano.resilience.circuit_breaker import PgFailureCounter
        _lifecycle = SkillLifecycle(PgSkillStore())
        _breaker = PgFailureCounter()
        log.info("gateway.db_ready")
    except Exception as e:  # noqa: BLE001
        log.warning("gateway.db_unavailable", error=str(e))
    try:                                   # 注入三模型评审 client(发布硬闸门 + 录制语义顾问复用同一 client)
        from dano.agent_tools.tools import set_review_board
        from dano.review.board import ReviewBoard
        set_review_board(ReviewBoard.from_settings())
    except Exception as e:  # noqa: BLE001
        log.warning("gateway.review_board_unavailable", error=str(e))
    yield
    from dano.execution.page.pool import shutdown_browser_pool
    await shutdown_browser_pool()      # 释放常驻浏览器(页面运行时池)
    await close_pool()


app = FastAPI(title="Dano Back", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
repo = AssetRepository()


# ── 凭证解析:配了 Vault 走真实 Vault,否则 dev 回退 config.py 的 runtime_credentials + 进程内表 ──
def _resolve_creds(refs: dict[str, str]) -> dict[str, str]:
    from dano.infra.credentials import resolve_credentials
    return resolve_credentials(refs)


async def _load_endpoints(tenant: str, subs: list[Subsystem]) -> dict[str, SystemEndpoint]:
    endpoints: dict[str, SystemEndpoint] = {}
    for sub in subs:
        env = await repo.get_published(AssetType.ENV_PROFILE, Scope(tenant=tenant, subsystem=sub),
                                       asset_key=AssetType.ENV_PROFILE.value)
        if env is None:
            continue
        body = EnvProfileBody.model_validate(env.body)
        if body.base_url:
            endpoints[system_key_for(sub)] = SystemEndpoint(base_url=body.base_url, auth=body.auth)
    return endpoints


async def _load_holidays(tenant: str, subs: list[Subsystem]) -> list[str]:
    """汇总该租户各系统 env_profile 里登记的日历源(供复合流程 compute 的 business_days)。"""
    out: list[str] = []
    for sub in subs:
        env = await repo.get_published(AssetType.ENV_PROFILE, Scope(tenant=tenant, subsystem=sub),
                                       asset_key=AssetType.ENV_PROFILE.value)
        if env:
            out += list((env.body or {}).get("holidays") or [])
    return sorted(set(out))


async def _orchestrator(tenant: str) -> Orchestrator:
    from dano.execution.page import build_page_runtime

    subs = await _tenant_subsystems(tenant)            # 发现该租户的真实系统(任意系统,不写死)
    endpoints = await _load_endpoints(tenant, subs)
    executor = RealActionExecutor(endpoints=endpoints, auth_manager=AuthManager())
    registry = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=subs)
    harness = Harness(action_executor=executor, resolve_credentials=_resolve_creds)
    return Orchestrator(registry=registry, store=repo, harness=harness,
                        action_executor=executor, resolve_credentials=_resolve_creds,
                        page_runtime=build_page_runtime(),
                        holidays=await _load_holidays(tenant, subs))


async def _auth_tenant(x_tenant_key: str | None) -> str:
    if not x_tenant_key:
        raise HTTPException(status_code=401, detail="缺少 X-Tenant-Key")
    rec = await _registry.get_tenant_by_key(x_tenant_key)
    if rec is None:
        raise HTTPException(status_code=401, detail="X-Tenant-Key 无效")
    return rec.tenant


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── 运行配置全部走 config.py(不再有前端运行配置页 / 写入端点);仅保留只读 LLM 自检 ──
@app.get("/settings/llm-test")
async def llm_test() -> dict:
    """用 config.py 的 LLM 配置真打一发,返回真实 HTTP 状态——定位生成失败是
    401(key 错)/400(模型名错)/429(限流),不必再猜。不回显 key 值。"""
    import time

    import httpx

    from dano.config import get_settings
    s = get_settings()
    key = (s.pi_api_key or "").strip()
    if not key:
        return {"ok": False, "reason": "no_key", "detail": "config.py 未配 pi_api_key"}
    base = s.pi_base_url.rstrip("/")
    url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    payload = {"model": s.pi_model, "temperature": 0, "max_tokens": 8,
               "messages": [{"role": "user", "content": "ping"}]}
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(url, json=payload,
                             headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "network_error", "detail": repr(e),
                "base_url": s.pi_base_url, "model": s.pi_model}
    dur = round(time.monotonic() - t0, 2)
    ok = r.status_code < 400
    content_len = 0
    if ok:
        try:
            content_len = len((r.json()["choices"][0]["message"]["content"] or ""))
        except Exception:  # noqa: BLE001
            content_len = -1
    return {"ok": ok, "status": r.status_code, "dur_s": dur, "model": s.pi_model,
            "base_url": s.pi_base_url, "key_tail": key[-4:], "content_len": content_len,
            "body": ("" if ok else r.text[:400])}


# ── 租户 ──
class TenantCreate(BaseModel):
    tenant: str
    display_name: str = ""


@app.post("/tenants")
async def create_tenant(req: TenantCreate) -> dict:
    rec = await _registry.create_tenant(TenantRecord(**req.model_dump()))
    return rec.model_dump()


# ── 接入(pi 自主生成)──
class OnboardReq(BaseModel):
    tenant: str
    subsystem: str = "A-OA"
    openapi: dict
    deploy: dict
    credentials: dict[str, str] = {}
    policy_text: str = ""          # 制度文件原文(可选,仅旧声明式路径)
    business_rules: list[dict] = []   # 人工业务规则(阈值/审批链)→ pi grounding 分支/前置
    holidays: list[str] = []          # 日历源(法定节假日)→ env_profile,运行期注入 business_days
    include_tags: list[str] = []   # 类别白名单(空=全部业务动作;超大 swagger 先圈范围)
    flows: list[dict] = []         # 写/复合流程声明 [{flow, actions?, test_input}](codegen 主路径用)
    use_codegen: bool = False      # 默认单一 pi 路径(产声明式 DSL v2);True=已退役 codegen 逃生舱
    max_read_flows: int | None = None   # 自动生成的只读 adapter 上限(None=全部;大 swagger 建议设小)


class PreviewReq(BaseModel):
    openapi: dict
    subsystem: str = "A-OA"


@app.post("/onboarding/preview")
async def onboarding_preview(req: PreviewReq) -> dict:
    """接入前预览:按 tag 返回类别清单与动作数(过滤基础设施),供企业勾选要哪些类别。

    只解析、不 spawn pi、不碰凭证;超大 swagger 据此先圈定范围再接入。
    """
    from dano.capabilities import doc_parser, endpoint_classifier, oa_templates
    spec = req.openapi or {}
    template = oa_templates.match_template(spec)
    extra = template.infrastructure_patterns() if template else ()
    categories: dict[str, int] = {}
    actions: list[dict] = []
    total = 0
    for a in doc_parser.parse_openapi(spec):
        if endpoint_classifier.classify(a, extra_infra=extra) == endpoint_classifier.INFRASTRUCTURE:
            continue
        total += 1
        tags = list(a.tags or ["(未分类)"])
        for t in tags:
            categories[t] = categories.get(t, 0) + 1
        actions.append({"name": a.name, "method": a.method, "endpoint": a.endpoint,
                        "tags": tags, "summary": a.summary or "",
                        "required": list(a.required_in or [])})
    return {"template": template.name if template else None,
            "business_action_count": total,
            "categories": [{"tag": k, "count": v} for k, v in
                           sorted(categories.items(), key=lambda kv: -kv[1])],
            "actions": actions}


class DiscoverReq(BaseModel):
    openapi: dict
    subsystem: str = "A-OA"
    include_tags: list[str] = []


@app.post("/onboarding/discover-flows")
async def onboarding_discover(req: DiscoverReq) -> dict:
    """平台自动「找出合适的流程」(图二步骤2-3):返回复合/连接器流程提案,供前端确认后生成。

    只解析 + 套模板知识,不 spawn pi、不碰凭证。前端据此勾选/微调测试输入,再发 /onboarding/start。
    """
    from dano.onboarding.discovery import discover_flows
    return {"flows": discover_flows(req.openapi or {}, req.include_tags)}


class ListTemplatesReq(BaseModel):
    base_url: str
    token: str = ""


@app.post("/onboarding/list-templates")
async def list_templates(req: ListTemplatesReq) -> dict:
    """查询目标 OA 真实的**流程模板清单**(业务场景:请假/报销/出差…),作为可选「业务模板」。

    系统特定(查哪个端点、怎么解析)全在 dialect:网关只遍历已注册方言、试其 template_list_paths,
    用 parse_template_list 解析——**主流程零系统字面量**(换框架只改 oa_templates.py)。
    """
    import httpx

    from dano.capabilities import oa_templates
    from dano.infra.http import tls_verify
    base = req.base_url.rstrip("/")
    tok = (req.token or "").strip()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    auth_fail = False
    async with httpx.AsyncClient(timeout=40, verify=tls_verify()) as c:
        for dialect in oa_templates.all_templates():
            for path in dialect.template_list_paths():
                try:
                    r = await c.get(base + (path if path.startswith("/") else "/" + path), headers=headers)
                    j = r.json()
                except Exception:  # noqa: BLE001 - 换下一个端点/方言
                    continue
                rows = dialect.parse_template_list(j)
                if rows:
                    return {"templates": rows}
                if isinstance(j, dict) and j.get("code") not in (None, 200, 0):
                    auth_fail = True
    hint = "token 可能已失效(body.code 非 200)" if auth_fail else "该 OA 无模板配置或方言不支持"
    raise HTTPException(status_code=502, detail=f"未查到流程模板:{hint}")


class TemplateFormReq(BaseModel):
    base_url: str
    token: str = ""
    template_id: str


@app.post("/onboarding/template-form")
async def template_form(req: TemplateFormReq) -> dict:
    """查某业务模板的**动态表单字段清单**,供前端预填 values 骨架。抽不出就返回空,让用户手填——不臆造。

    探针路径与表单解析都来自 dialect(form_probe_path + parse_form_fields),网关不写系统端点字面量。
    """
    import httpx

    from dano.capabilities import oa_templates
    from dano.infra.http import tls_verify
    base = req.base_url.rstrip("/")
    tok = (req.token or "").strip()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    async with httpx.AsyncClient(timeout=40, verify=tls_verify()) as c:
        for dialect in oa_templates.all_templates():
            path = dialect.form_probe_path(req.template_id)
            if not path:
                continue
            try:
                r = await c.get(base + (path if path.startswith("/") else "/" + path), headers=headers)
                j = r.json()
            except Exception:  # noqa: BLE001 - 换下一个方言
                continue
            fields = dialect.parse_form_fields(j)
            if fields or (isinstance(j, dict) and j.get("code") in (None, 200, 0)):
                return {"fields": fields}   # 取到了(可能为空:结构特殊,让用户手填)
    raise HTTPException(status_code=502, detail="取表单失败:token 是否有效 / 模板是否存在?")


# ── v2-M1 理解流程:证据采集(静态 + 只读真探针)──
class UnderstandReq(BaseModel):
    openapi: dict
    base_url: str = ""
    token: str = ""
    template_id: str = ""
    include_tags: list[str] = []


@app.post("/onboarding/understand-flow")
async def understand_flow(req: UnderstandReq) -> dict:
    """v2-M1:采集一条/一组流程的结构化证据(静态 swagger + 只读运行时探针),供后续画像/LLM 拆解。

    只读、不臆造、凭证不进证据。给了 base_url+token 才做真探针(表单字段 + 样例出参结构),否则纯静态。
    """
    from dano.onboarding.evidence import collect_evidence, make_http_probe
    probe = make_http_probe(req.base_url, req.token) if (req.base_url and req.token) else None
    ev = await collect_evidence(req.openapi or {}, include_tags=req.include_tags,
                                template_id=req.template_id, probe=probe)
    return ev.model_dump()


class FetchSwaggerReq(BaseModel):
    url: str = ""                  # swagger 文档完整地址(手动导入:直接写地址)
    base_url: str = ""             # 备用:base_url + path 拼接
    token: str = ""
    path: str = "/v3/api-docs"


@app.post("/onboarding/fetch-swagger")
async def fetch_swagger(req: FetchSwaggerReq) -> dict:
    """按你给的 swagger 地址代取 OpenAPI(浏览器跨域+自签证书拉不了,由后端代取)。

    手动导入的两种方式之一:直接写 swagger 地址(url),后端代取;另一种是前端上传 .json 文件(无需本端点)。
    """
    import httpx
    from dano.infra.http import tls_verify
    url = (req.url or "").strip() or (req.base_url.rstrip("/") + req.path)
    if not url:
        raise HTTPException(status_code=400, detail="请提供 swagger 地址(url)或 base_url")
    tok = (req.token or "").strip()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    try:
        async with httpx.AsyncClient(timeout=40, verify=tls_verify()) as c:
            r = await c.get(url, headers=headers)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"拉取 swagger 失败: {e}") from e
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"拉取 swagger HTTP {r.status_code}")
    try:
        return r.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"swagger 非 JSON: {e}") from e


@app.post("/onboarding")
async def onboarding(req: OnboardReq) -> dict:
    from dano.onboarding import onboard
    report = await onboard(tenant=req.tenant, subsystem=req.subsystem, openapi=req.openapi,
                           deploy=req.deploy, credentials=req.credentials,
                           policy_text=req.policy_text, include_tags=req.include_tags,
                           business_rules=req.business_rules, holidays=req.holidays,
                           flows=req.flows, use_codegen=req.use_codegen,
                           max_read_flows=req.max_read_flows, lifecycle=_lifecycle)
    await _auto_export(req.tenant)
    return report.model_dump()


# ── 页面型系统接入(流程8,无 API):确定性侦察→建体→回放→发布,不走 pi/LLM ──
class PageScoutReq(BaseModel):
    tenant: str
    subsystem: str = "A-报销"
    start_url: str
    deploy: dict = {}
    credentials: dict[str, str] = {}
    headless: bool = True


@app.post("/onboarding/page/scout")
async def onboarding_page_scout(req: PageScoutReq) -> dict:
    """仅侦察页面:返回候选字段 / 提交按钮 / 建议步骤 / 结构指纹(供向导预览,无副作用)。"""
    from dano.onboarding.page_onboard import scout_page_only
    try:
        return await scout_page_only(
            tenant=req.tenant, subsystem=req.subsystem, start_url=req.start_url,
            deploy=req.deploy, credentials=req.credentials, headless=req.headless)
    except Exception as e:  # noqa: BLE001 —— 浏览器/页面打不开 → 友好报错
        raise HTTPException(status_code=502, detail=f"侦察页面失败: {e}") from e


class PageOnboardReq(BaseModel):
    tenant: str
    subsystem: str = "A-报销"
    start_url: str                       # 表单页地址(绝对 URL,或相对 deploy.base_url)
    action: str                          # 派生 Skill 名,如 submit_reimburse
    title: str = ""
    success_marker: str | None = None    # 成功标志元素/文本(语义定位),如 "text=保存成功"
    deploy: dict = {}                    # {base_url} 等
    credentials: dict[str, str] = {}     # 测试登录态(如 {storage_state: <path>})
    sample_inputs: dict = {}             # 回放用字段测试值
    headless: bool = True
    steps: list[dict] = []               # 前端改过字段映射的步骤(空=后端自动侦察)
    dom_fingerprint: str = ""            # 与 steps 配套的结构指纹(向导从 scout 取)


@app.post("/onboarding/page")
async def onboarding_page(req: PageOnboardReq) -> dict:
    """页面型系统接入:真实浏览器侦察 + 确定性建体 + 沙箱回放 + 发布闸门。写页面默认 dry 回放 + 需评审。"""
    from dano.onboarding.page_onboard import run_page_onboarding
    report = await run_page_onboarding(
        tenant=req.tenant, subsystem=req.subsystem, start_url=req.start_url, action=req.action,
        title=req.title, success_marker=req.success_marker, deploy=req.deploy,
        credentials=req.credentials, sample_inputs=req.sample_inputs, headless=req.headless,
        steps=req.steps or None, dom_fingerprint=req.dom_fingerprint or None)
    if report.get("ok"):
        await _auto_export(req.tenant)
    return report


class PageImportReq(BaseModel):
    tenant: str
    subsystem: str = "A-报销"
    codegen: str                         # Playwright codegen 脚本(Python/JS)
    action: str
    title: str = ""
    success_marker: str | None = None
    start_url: str = ""                  # 覆盖脚本里的 page.goto(可选)
    deploy: dict = {}
    credentials: dict[str, str] = {}
    sample_inputs: dict = {}             # 覆盖/补充录制里的样例值


@app.post("/onboarding/page/import")
async def onboarding_page_import(req: PageImportReq) -> dict:
    """方式A:导入 Playwright codegen 录制 → 解析步骤 → 建体 → 回放 → 发布(录制无指纹基线,跳过漂移)。"""
    from dano.execution.page.codegen_import import parse_playwright_codegen
    from dano.onboarding.page_onboard import run_page_onboarding
    steps, parsed_url, samples = parse_playwright_codegen(req.codegen)
    if not steps:
        raise HTTPException(status_code=400, detail="未能从录制脚本解析出步骤(请贴 Playwright codegen 的 Python/JS 输出)")
    start_url = req.start_url or parsed_url
    if not start_url:
        raise HTTPException(status_code=400, detail="录制脚本无 page.goto,请填 start_url")
    sample_inputs = {**samples, **(req.sample_inputs or {})}
    report = await run_page_onboarding(
        tenant=req.tenant, subsystem=req.subsystem, start_url=start_url, action=req.action,
        title=req.title, success_marker=req.success_marker, deploy=req.deploy,
        credentials=req.credentials, sample_inputs=sample_inputs,
        steps=[s.model_dump() for s in steps], dom_fingerprint="")
    if report.get("ok"):
        await _auto_export(req.tenant)
    return {**report, "parsed_steps": len(steps), "sample_inputs": sample_inputs}


async def _request_fields_msg(chosen: dict, candidates: list[dict], samples: dict,
                              reads: list[dict] | None = None, storage: dict | None = None,
                              required_labels: set | None = None) -> dict:
    """构造 request_fields 消息:字段表(含 type/required)+ 候选请求 + select(Q2)+ identity(Q1)。"""
    from dano.execution.page.request_capture import (flatten_body, suggest_identity, suggest_select_names,
                                                     suggest_selects, suggest_workflow_steps)

    def _path(u: str) -> str:
        i = u.find("//")
        return u[u.find("/", i + 2):] if i >= 0 and u.find("/", i + 2) >= 0 else u
    cand_list = [{"idx": i, "method": (c.get("method") or "POST").upper(), "path": _path(c.get("url") or "")}
                 for i, c in enumerate(candidates)]
    pd = chosen.get("post_data")
    fields = flatten_body(pd, samples, required_labels)
    # 传 samples:用录制选中的显示名消歧/确认(大字典里短码也能精确绑对那项)
    selects = suggest_selects(pd, reads or [], samples)
    # select/选人字段:用录制选项标签当默认参数名(经候选列表桥接),避免漏内部 key(Activity_xxx/嵌套键)
    sel_names = suggest_select_names(selects, samples)
    for f in fields:
        if f.get("path") in sel_names:
            f["suggest_name"] = sel_names[f["path"]]
    # LLM 字段语义增强(最佳努力):只给"确定性没把握(名字仍=原始 key)"的字段补中文名;确信的不覆盖,失败不影响。
    try:
        from dano.agent_tools import tools as _T
        from dano.execution.page.request_capture import merge_llm_field_names
        from dano.review.board import suggest_field_names_llm
        _board = _T._review_board
        if _board is not None:
            _names = await suggest_field_names_llm(
                _board.client, (getattr(_board, "models", None) or {}).get("acceptance"),
                action=_path(chosen.get("url") or ""), fields=fields)
            fields = merge_llm_field_names(fields, _names)
    except Exception:  # noqa: BLE001
        pass
    return {"type": "request_fields",
            "method": (chosen.get("method") or "POST").upper(), "url": chosen.get("url"),
            "fields": fields,
            "candidates": cand_list, "chosen_idx": candidates.index(chosen) if chosen in candidates else 0,
            "suggested_steps": suggest_workflow_steps(candidates, samples),   # 自动建议哪几条组成业务流程(前端预勾)
            "selects": selects,
            "identity": suggest_identity(pd, storage)}         # 字段=当前用户/会话值(运行期重取)


# ── 方式B:网页内录制(WebSocket:截屏流出 + 输入回传入 + 实时步骤 + 录完发布)──
@app.websocket("/onboarding/page/record")
async def record_ws(ws: WebSocket) -> None:
    """客户在网页里操作我们托管的浏览器,免安装/免命令行。协议见前端 PageRecorder。"""
    await ws.accept()
    sess = None
    try:
        init = await ws.receive_json()
        if init.get("type") != "start" or not init.get("start_url"):
            await ws.send_json({"type": "error", "detail": "首帧须为 {type:'start', start_url, ...}"})
            return
        from dano.execution.page.recorder import RecordSession
        loop = asyncio.get_event_loop()

        def on_step(step: dict) -> None:
            try:
                loop.create_task(ws.send_json({"type": "step", "step": step}))
            except Exception:  # noqa: BLE001
                pass

        def on_request(r: dict) -> None:                  # 诊断:抓到的写请求实时推给前端
            try:
                loop.create_task(ws.send_json({"type": "request", "request": r}))
            except Exception:  # noqa: BLE001
                pass

        sess = RecordSession(on_step=on_step, on_request=on_request,
                             intercept_submit=init.get("intercept", True),
                             capture_reads=init.get("capture_reads", True))
        await sess.start(init["start_url"], base_url=init.get("base_url", ""),
                         storage_state=init.get("storage_state") or None,
                         token=init.get("token") or None)   # 贴 token → 预置登录态,免在画面里登录

        async def on_frame(data: str) -> None:
            try:
                await ws.send_json({"type": "frame", "data": data})
            except Exception:  # noqa: BLE001
                pass

        await sess.start_screencast(on_frame)
        await ws.send_json({"type": "started"})

        pending_req: dict | None = None       # 抓到的提交请求,等用户勾完字段再发布
        pending_candidates: list[dict] = []    # 所有 JSON 写请求(候选),供用户手选用哪个
        pending_samples: dict = {}             # 录制时填的样例值(选别的请求时重算参数建议)
        pending_reads: list[dict] = []         # 抓到的列表读响应(select 候选源)
        pending_storage: dict | None = None    # 登录态(认 identity 字段)
        pending_required: set = set()          # 录制时表单 * 必填的字段标签
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "input":
                await sess.dispatch_input(msg.get("event") or {})
            elif t == "reset":
                sess.reset()                          # 登录后:丢弃登录步骤,只录业务流程
                await ws.send_json({"type": "reset_ok"})
            elif t == "finalize":
                raw = msg.get("steps")
                if raw is not None:           # 前端编辑后的步骤(删了噪声/重复/调序)→ 以它为准
                    from dano.agent_tools.page_builder import RecordedStep, assign_field_keys
                    steps = [RecordedStep(op=s["op"], locator=s.get("locator"),
                                          field=(s.get("field") or None)) for s in raw]
                    # 字段 key 与 build_page_script 同算法分配(同序),samples/required 与脚本参数一致(P1#6)
                    fb_idx = [i for i, s in enumerate(raw) if s.get("field")]
                    keymap = dict(zip(fb_idx, assign_field_keys([raw[i]["field"] for i in fb_idx])))
                    samples = {keymap[i]: raw[i].get("value", "") for i in fb_idx
                               if raw[i].get("op") in ("fill", "select", "pick") and raw[i].get("value")}
                    required_labels = {keymap[i] for i in fb_idx
                                       if raw[i].get("required") and raw[i].get("op") in ("fill", "select", "pick")}
                else:
                    steps, samples = sess.recorded_steps()
                    required_labels = sess.recorded_required_labels()
                sub = init.get("subsystem", "A-报销")
                login_state = await sess.storage_state()   # 录制会话(已真人登录)的登录态快照

                # ★ 抓请求路径优先:列出所有 JSON 写请求(候选),默认选最像提交的那个,把它请求体拍平给
                #   前端勾字段。用户也可在候选里手选别的(应对噪声误判 / 多写请求)。勾完发 publish_request 才建 Skill。
                from dano.execution.page.request_capture import (flatten_body, json_write_requests,
                                                                 looks_like_auth_write, pick_submit_request)
                cands = [c for c in json_write_requests(sess.captured_requests())
                         if flatten_body(c.get("post_data"))                       # 有可勾字段的
                         and not looks_like_auth_write(c.get("url") or "", c.get("post_data"))]  # 排除登录/鉴权写
                if cands:
                    pending_candidates = cands
                    pending_samples = samples
                    pending_reads = sess.captured_reads()       # select 候选源(选领导)
                    pending_storage = login_state               # identity 字段识别
                    pending_required = required_labels          # 表单 * 必填
                    chosen = pick_submit_request(cands, samples) or cands[-1]
                    pending_req = chosen
                    await ws.send_json(await _request_fields_msg(chosen, cands, samples, pending_reads,
                                                                 pending_storage, pending_required))
                    continue

                # 兜底:没抓到 JSON 提交请求 → 老的 DOM 回放路径
                if not steps:
                    await ws.send_json({"type": "result",
                                        "report": {"ok": False, "reason": "没抓到提交请求,也没录到可用步骤;"
                                                   "请确认是否点了「提交」,或换「逐步确认」方式"}, "parsed_steps": 0})
                    continue
                from dano.onboarding.page_onboard import run_page_onboarding
                deploy = init.get("deploy") or ({"base_url": init["base_url"]} if init.get("base_url") else {})
                creds = dict(init.get("credentials") or {})
                if init.get("token"):
                    creds["token"] = init["token"]
                if login_state:
                    creds["storage_state"] = login_state   # 录制登录态 → 回放浏览器带着它,不再被挡登录
                report = await run_page_onboarding(
                    tenant=init["tenant"], subsystem=sub,
                    start_url=init["start_url"], action=msg["action"], title=msg.get("title", ""),
                    success_marker=msg.get("success_marker") or None, deploy=deploy,
                    credentials=creds,
                    sample_inputs={**samples, **(msg.get("sample_inputs") or {})},
                    steps=[s.model_dump() for s in steps], dom_fingerprint="")
                from dano.execution.page.sessions import save_session
                saved = save_session(init["tenant"], sub, login_state)   # 存盘供运行期复用
                if report.get("ok"):
                    await _auto_export(init["tenant"])
                await ws.send_json({"type": "result",
                                    "report": {**report, **({"session_saved": saved} if saved else {})},
                                    "parsed_steps": len(steps)})
                # 不 break:发布后会话保留,用户可删步骤重发或继续录;由 stop / 断连结束
            elif t == "choose_request":
                # 用户在候选里手选用哪个写请求(噪声误判/多写请求时)→ 重发该请求的字段表
                idx = msg.get("idx", 0)
                if pending_candidates and 0 <= idx < len(pending_candidates):
                    pending_req = pending_candidates[idx]
                    await ws.send_json(await _request_fields_msg(pending_req, pending_candidates, pending_samples,
                                                                 pending_reads, pending_storage, pending_required))
            elif t == "publish_request":
                # 用户在字段表里勾了哪些是参数、起了名 → 用真实提交请求建 Skill(任意 OA 通用)
                if pending_req is None:
                    await ws.send_json({"type": "result",
                                        "report": {"ok": False, "reason": "没有待发布的提交请求;先点「停止并发布」抓请求"}})
                    continue
                param_map = {k: v.strip() for k, v in (msg.get("param_map") or {}).items() if v and v.strip()}
                from dano.execution.page.request_capture import (auto_required_fields, build_api_request,
                                                                 build_api_workflow, infer_success_rule,
                                                                 suggest_fact_check, suggest_workflow_steps)
                sels = msg.get("selects") or []         # Q2 选领导:名字→ID
                idens = msg.get("identity") or []        # Q1 当前用户:运行期重取
                fc = suggest_fact_check(pending_samples, pending_reads)   # 回查源(录到"我的记录"列表才有)
                sr = infer_success_rule(pending_reads)   # 学这套系统自己的"业务成功"约定(不挑系统,见 P0#2)
                # 多步:用户勾了哪几条(step_idxs,有序);**没勾则自动判流程**(提交锚点+数据依赖,丢噪声)
                step_idxs = [i for i in (msg.get("step_idxs") or []) if 0 <= i < len(pending_candidates)]
                if not step_idxs:
                    step_idxs = suggest_workflow_steps(pending_candidates, pending_samples)   # 自动建议流程步
                if len(step_idxs) > 1:
                    writes = [pending_candidates[i] for i in step_idxs]
                    apir = build_api_workflow(writes, param_map=param_map, selects=sels, identity=idens,
                                              typed=pending_samples)
                    last_params = (apir.get("steps") or [{}])[-1].get("params") or []
                else:
                    apir = build_api_request(pending_req, param_map, selects=sels, identity=idens,
                                             typed=pending_samples)
                    last_params = (apir or {}).get("params") or []
                if apir and fc:
                    apir["fact_check"] = fc            # 提交后回查记录确认真生效(grounded)
                if apir and sr:                        # 学到的成功约定:落到资产(工作流则落最后一步=提交那步)
                    apir["success_rule"] = sr
                    if apir.get("steps"):
                        apir["steps"][-1]["success_rule"] = sr
                if not apir or not last_params:
                    await ws.send_json({"type": "result",
                                        "report": {"ok": False, "reason": "至少勾选一个字段作为参数(给它起个参数名)"}})
                    continue
                # 必填**自动判定**(免手动勾选,默认全部必填;表单抓到 * 区分时据 * 降级可选)。
                # 以提交那条请求体为锚(用户填值在此),经 param_map 桥到参数名;多步早期步的参数默认必填。
                auto_required = auto_required_fields(
                    pending_req.get("post_data"), pending_samples, param_map,
                    form_required_labels=pending_required, params=last_params)
                sub = init.get("subsystem", "A-报销")
                login_state = await sess.storage_state()
                from dano.execution.page.sessions import save_session
                from dano.onboarding.page_onboard import run_request_onboarding
                save_session(init["tenant"], sub, login_state)   # 运行期发请求带登录态
                # 单请求取自身 sample_inputs;工作流取最后一步的(dry 校验用)
                sample_in = apir.get("sample_inputs") or ((apir.get("steps") or [{}])[-1].get("sample_inputs") or {})
                rep = await run_request_onboarding(
                    tenant=init["tenant"], subsystem=sub, action=msg["action"],
                    title=msg.get("title", ""), api_request=apir, sample_inputs=sample_in,
                    required=auto_required,    # 自动判定:默认全部必填,表单 * 区分时降级可选(免手动勾选)
                    goal=msg.get("goal"),            # 一般为 None → run_request_onboarding 内 _auto_goal 自动提炼(一键发布)
                    deploy=init.get("deploy"), storage_state=login_state)  # 可逆沙箱+登录态 → 可活体真跑升 verified;P2
                if rep.get("ok"):
                    await _auto_export(init["tenant"])
                await ws.send_json({"type": "result", "report": rep,
                                    "parsed_steps": len(last_params), "via": "request",
                                    "workflow_steps": len(apir.get("steps") or []) or None})
            elif t == "stop":
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        try:
            await ws.send_json({"type": "error", "detail": str(e)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        if sess is not None:
            await sess.stop()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


class PagePiReq(BaseModel):
    tenant: str
    subsystem: str = "A-报销"
    start_url: str
    action_hint: str = ""
    deploy: dict = {}
    credentials: dict[str, str] = {}
    timeout_s: float = 600.0


@app.post("/onboarding/page/pi")
async def onboarding_page_pi(req: PagePiReq) -> dict:
    """pi 自主驱动的页面接入:spawn Node sidecar,pi 按 onboard-page 技能自己侦察→建体→回放→评审→发布。"""
    from dano.onboarding.page_onboard import run_page_onboarding_pi
    report = await run_page_onboarding_pi(
        tenant=req.tenant, subsystem=req.subsystem, start_url=req.start_url,
        action_hint=req.action_hint, deploy=req.deploy, credentials=req.credentials,
        timeout_s=req.timeout_s)
    if report.get("published_skills"):
        await _auto_export(req.tenant)
    return report


async def _auto_export(tenant: str) -> None:
    """接入后自动导出该租户已上架 skill(无需手动点)。

    目录:**页面配过的(持久化)> DANO_EXPORT_DIR > 仓库默认** —— 与手动导出落同一处。
    best-effort:导出失败不影响接入结果。
    """
    try:
        from pathlib import Path

        from dano.execution.page.sessions import get_export_dir
        from dano.export.agent_skills import write_skills
        out = get_export_dir(str(Path(__file__).resolve().parents[3] / "export" / "agent-skills"))
        written = await write_skills(tenant, out)
        log.info("onboard.auto_export", tenant=tenant, out=out, count=len(written))
    except Exception as e:  # noqa: BLE001
        log.warning("onboard.auto_export_failed", error=str(e))


# ── 异步接入(接入向导:启动后台生成 + 轮询进度,避免几分钟同步阻塞/超时)──
_onboard_jobs: dict[str, dict] = {}


@app.post("/onboarding/start")
async def onboarding_start(req: OnboardReq) -> dict:
    import asyncio
    from uuid import uuid4
    from dano.onboarding import onboard
    job_id = uuid4().hex[:12]
    job = {"job_id": job_id, "status": "running", "events": [], "report": None, "error": None}
    _onboard_jobs[job_id] = job

    def _progress(ev: dict) -> None:
        import time
        job["events"].append({"ts": time.time(), **ev})

    async def _run() -> None:
        try:
            rep = await onboard(
                tenant=req.tenant, subsystem=req.subsystem, openapi=req.openapi,
                deploy=req.deploy, credentials=req.credentials, policy_text=req.policy_text,
                include_tags=req.include_tags, business_rules=req.business_rules, holidays=req.holidays,
                flows=req.flows, use_codegen=req.use_codegen,
                max_read_flows=req.max_read_flows, progress=_progress, lifecycle=_lifecycle)
            job["report"] = rep.model_dump()
            job["status"] = "completed"
            await _auto_export(req.tenant)             # 接入完成即自动导出 skill-creator 包
        except Exception as e:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = str(e)
            log.warning("onboard.job_failed", job=job_id, error=str(e))

    asyncio.create_task(_run())
    return {"job_id": job_id}


@app.get("/onboarding/jobs/{job_id}")
async def onboarding_job(job_id: str) -> dict:
    """轮询接入进度:status(running/completed/failed)+ events(plan/flow_start/rejected/published/...)+ report。"""
    job = _onboard_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job 不存在")
    return job


# ── 契约目录(租户隔离)──
@app.get("/v1/skills")
async def list_skills(x_tenant_key: str | None = Header(default=None)) -> list[dict]:
    tenant = await _auth_tenant(x_tenant_key)
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=await _tenant_subsystems(tenant))
    return [m.model_dump() for m in build_manifests(reg.skills)]


@app.get("/v1/skills/{skill_id}")
async def get_skill(skill_id: str, x_tenant_key: str | None = Header(default=None)) -> dict:
    tenant = await _auth_tenant(x_tenant_key)
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=await _tenant_subsystems(tenant))
    m = next((x for x in build_manifests(reg.skills) if x.name == skill_id), None)
    if m is None:
        raise HTTPException(status_code=404, detail=f"本公司无此 Skill: {skill_id}")
    return m.model_dump()


@app.delete("/v1/skills/{skill_id}")
async def delete_skill(skill_id: str, x_tenant_key: str | None = Header(default=None)) -> dict:
    """删除本租户的某个 skill(删 PG 资产各版本)。便于测试重来;按租户隔离,不碰别家。"""
    tenant = await _auth_tenant(x_tenant_key)
    sub_str, _, action = skill_id.partition(".")
    if not action:
        raise HTTPException(status_code=400, detail="skill_id 应为 {subsystem}.{action}")
    subsystem = Subsystem(sub_str)            # 系统标识开放:任意系统皆合法(不存在则下面按 0 行返回 404)
    rows = await repo.delete_by_action(Scope(tenant=tenant, subsystem=subsystem), action)
    if rows == 0:
        raise HTTPException(status_code=404, detail=f"本公司无此 Skill: {skill_id}")
    return {"deleted": rows, "skill_id": skill_id}


# ── 瘦执行(前端只给 skill_id + input;endpoint/凭证/断言后端取)──
class InvokeReq(BaseModel):
    input: dict = {}
    idempotency_key: str | None = None
    confirm: bool = False


async def _invoke(tenant: str, skill_id: str, input_: dict, confirm: bool) -> dict:
    """统一受控调用入口:skill_id→子系统/动作→风险闸门→隔离执行→事实核查。"""
    sub_str, _, action = skill_id.partition(".")
    if not action:
        raise HTTPException(status_code=400, detail="skill_id 应为 {subsystem}.{action}")
    subsystem = Subsystem(sub_str)            # 系统标识开放:任意系统皆合法(无对应 Skill 时编排按能力缺口处理)
    # 流程12:异常暂停的 Skill 不可调用(保障期闸门)
    rec = await _lifecycle.store.get(skill_id)
    if rec and rec.state == SkillState.SUSPENDED:
        raise HTTPException(status_code=409, detail=f"Skill 异常暂停中,已转保障期: {skill_id}")
    orch = await _orchestrator(tenant)
    outcome = await orch.invoke_skill(subsystem, action, input_, tenant=tenant, confirm=confirm)
    return outcome.model_dump(mode="json")


@app.post("/v1/skills/{skill_id}/invoke")
async def invoke_skill(skill_id: str, req: InvokeReq,
                       x_tenant_key: str | None = Header(default=None)) -> dict:
    tenant = await _auth_tenant(x_tenant_key)
    return await _invoke(tenant, skill_id, req.input, req.confirm)


# ── function-calling 工具(给聊天端 LLM:① 列工具喂给 LLM ② 执行 LLM 的工具调用)──
@app.get("/v1/tools")
async def list_tools(x_tenant_key: str | None = Header(default=None)) -> list[dict]:
    """导出本租户 Skill 为 OpenAI function-calling tools 数组,聊天端直接喂给 LLM。"""
    tenant = await _auth_tenant(x_tenant_key)
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=await _tenant_subsystems(tenant))
    return build_function_tools(reg.skills)


class ToolCallReq(BaseModel):
    name: str                       # 工具名(= skill_id 的点转 __,如 A-OA__submit_leave)
    arguments: dict | str = {}      # LLM 产出的参数(对象或 JSON 字符串都行)
    confirm: bool = False


@app.post("/v1/tools/call")
async def call_tool(req: ToolCallReq, x_tenant_key: str | None = Header(default=None)) -> dict:
    """执行一次 LLM 工具调用:name→skill_id、arguments→input,走与 /invoke 同一受控链路。"""
    tenant = await _auth_tenant(x_tenant_key)
    args = req.arguments
    if isinstance(args, str):
        import json as _json
        try:
            args = _json.loads(args or "{}")
        except _json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"arguments 非合法 JSON: {e}") from e
    return await _invoke(tenant, skill_id_of(req.name), args, req.confirm)


class ExportSkillsReq(BaseModel):
    out_dir: str                    # 目标目录(通常是 pi 仓库的 .agents/skills),后端本地写入


@app.post("/export/agent-skills")
async def export_agent_skills_ep(req: ExportSkillsReq,
                                 x_tenant_key: str | None = Header(default=None)) -> dict:
    """把本租户已上架 Skill 导出为 pi 文件式 skill(.agents/skills/<name>/),写入 out_dir。

    后端与目标目录同机时直接写文件,免敲命令。真执行仍在 Dano 侧;导出的脚本用 curl 调 /v1/tools/call。
    """
    tenant = await _auth_tenant(x_tenant_key)
    from dano.execution.page.sessions import save_export_dir
    from dano.export.agent_skills import write_skills
    out = req.out_dir
    try:
        written = await write_skills(tenant, out)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"写入目录失败:{e}") from e
    save_export_dir(out)                                 # 记住此目录 → 录完自动发布落同一处
    return {"out_dir": out, "count": len(written), "written": written}


@app.get("/assets/published")
async def list_published(asset_type: AssetType, subsystem: Subsystem, tenant: str) -> list[dict]:
    return [e.model_dump(mode="json")
            for e in await repo.list_published(asset_type, Scope(tenant=tenant, subsystem=subsystem))]


# ── 阶段三 保障期 ──
@app.get("/lifecycle/skills")
async def lifecycle_skills() -> list[dict]:
    return [{"skill_id": r.skill_id, "action": r.action, "state": r.state.value,
             "asset_version": r.asset_version, "history": r.history}
            for r in await _lifecycle.store.all()]


@app.post("/assurance/report-failure")
async def report_failure_route(event: dict) -> dict:
    from dano.assurance.service import FailureEvent, report_failure
    d = await report_failure(FailureEvent.model_validate(event), lifecycle=_lifecycle, breaker=_breaker)
    return d.model_dump()


class SelfHealReq(BaseModel):
    tenant: str
    subsystem: str = "A-OA"
    openapi: dict
    deploy: dict
    credentials: dict[str, str] = {}
    actions: list[str] | None = None      # 指定受影响动作;省略=自动取当前暂停的 Skill
    incremental: bool = True              # 默认增量;置 false 回退全量重跑


@app.post("/assurance/self-heal")
async def self_heal_route(req: SelfHealReq) -> dict:
    from dano.assurance.service import self_heal
    out = await self_heal(tenant=req.tenant, subsystem=req.subsystem, openapi=req.openapi,
                          deploy=req.deploy, credentials=req.credentials, lifecycle=_lifecycle,
                          actions=req.actions, incremental=req.incremental)
    for sid in out.get("recovered", []):       # 自愈成功后清零失败计数
        await _breaker.reset_prefix(f"fail:{sid}")
    return out
