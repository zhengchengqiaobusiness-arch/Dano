"""Dano 网关(阶段一+三对外面)。

- 接入:POST /onboarding(pi 自主生成 → 发布)
- 契约:GET /v1/skills(标准 function-calling 契约,租户隔离)/ GET /v1/skills/{id}
- 瘦执行:POST /v1/skills/{id}/invoke(前端只给 skill_id+input;后端取资产/凭证/断言执行)
- 资产:GET /assets/published
后端不做 NL 意图/多智能体编排(阶段二交前端)。凭证经 Vault/env,平台只存引用。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Header, HTTPException
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
ALL_SUBSYSTEMS = [Subsystem.OA, Subsystem.TICKET, Subsystem.REIMBURSE]
_registry = InMemoryRegistry()       # DB 就绪换 PgRegistry(lifespan)
_lifecycle = SkillLifecycle()        # 流程12 Skill 生命周期(进程内;可换 PgSkillStore)
_breaker = InMemoryCounter()         # 流程10 失败计数/熔断


@asynccontextmanager
async def lifespan(app: FastAPI):
    from dano.infra.db import close_pool, init_pool, run_migrations
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
    yield
    await close_pool()


app = FastAPI(title="Dano Back", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
repo = AssetRepository()


# ── 凭证解析:配了 Vault 走真实 Vault,否则 dev 回退 DANO_RUNTIME_CREDENTIALS env 表 ──
def _resolve_creds(refs: dict[str, str]) -> dict[str, str]:
    from dano.infra.credentials import resolve_credentials
    return resolve_credentials(refs)


async def _load_endpoints(tenant: str) -> dict[str, SystemEndpoint]:
    endpoints: dict[str, SystemEndpoint] = {}
    for sub in ALL_SUBSYSTEMS:
        env = await repo.get_published(AssetType.ENV_PROFILE, Scope(tenant=tenant, subsystem=sub),
                                       asset_key=AssetType.ENV_PROFILE.value)
        if env is None:
            continue
        body = EnvProfileBody.model_validate(env.body)
        if body.base_url:
            endpoints[system_key_for(sub)] = SystemEndpoint(base_url=body.base_url, auth=body.auth)
    return endpoints


async def _orchestrator(tenant: str) -> Orchestrator:
    endpoints = await _load_endpoints(tenant)
    executor = RealActionExecutor(endpoints=endpoints, auth_manager=AuthManager())
    registry = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=ALL_SUBSYSTEMS)
    harness = Harness(action_executor=executor, resolve_credentials=_resolve_creds)
    return Orchestrator(registry=registry, store=repo, harness=harness,
                        action_executor=executor, resolve_credentials=_resolve_creds)


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


# ── 运行配置(密钥/凭证在页面里填,后端运行时存进进程环境;不落文件/bat,GET 不回明文)──
class RuntimeConfig(BaseModel):
    pi_api_key: str | None = None          # SiliconFlow / OpenAI 兼容 key(编码+评审)
    pi_base_url: str | None = None
    pi_model: str | None = None
    insecure_tls: bool | None = None       # 自签证书的目标系统置 true
    runtime_credentials: dict | None = None  # 调用期 OA 凭证:{"租户/oa": {"token": "..."}}


def _runtime_status() -> dict:
    import json as _json
    import os
    from dano.config import get_settings
    s = get_settings()
    rc = os.environ.get("DANO_RUNTIME_CREDENTIALS", "")
    try:
        rc_keys = list(_json.loads(rc).keys()) if rc else []
    except Exception:  # noqa: BLE001
        rc_keys = []
    return {"pi_key_set": bool(s.pi_api_key), "pi_base_url": s.pi_base_url, "pi_model": s.pi_model,
            "insecure_tls": s.insecure_tls, "runtime_credential_keys": rc_keys}


@app.get("/settings/runtime")
async def get_runtime() -> dict:
    return _runtime_status()


@app.post("/settings/runtime")
async def set_runtime(cfg: RuntimeConfig) -> dict:
    """页面提交密钥/凭证 → 写进后端进程环境(不落文件)。重启后需再次提交(前端会自动重发)。"""
    import json as _json
    import os
    from dano.config import get_settings
    if cfg.pi_api_key:
        os.environ["DANO_PI_API_KEY"] = cfg.pi_api_key
    if cfg.pi_base_url:
        os.environ["DANO_PI_BASE_URL"] = cfg.pi_base_url
    if cfg.pi_model:
        os.environ["DANO_PI_MODEL"] = cfg.pi_model
    if cfg.insecure_tls is not None:
        os.environ["DANO_INSECURE_TLS"] = "1" if cfg.insecure_tls else "0"
    if cfg.runtime_credentials is not None:
        os.environ["DANO_RUNTIME_CREDENTIALS"] = _json.dumps(cfg.runtime_credentials)
    get_settings.cache_clear()
    log.info("settings.runtime_updated", pi_key_set=bool(get_settings().pi_api_key))
    return _runtime_status()


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
    include_tags: list[str] = []   # 类别白名单(空=全部业务动作;超大 swagger 先圈范围)
    flows: list[dict] = []         # 写/复合流程声明 [{flow, actions?, test_input}](codegen 主路径用)
    use_codegen: bool = True       # 主路径=goal 模式代码自动生成;False=旧声明式
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
                           flows=req.flows, use_codegen=req.use_codegen,
                           max_read_flows=req.max_read_flows, lifecycle=_lifecycle)
    return report.model_dump()


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
        job["events"].append(ev)

    async def _run() -> None:
        try:
            rep = await onboard(
                tenant=req.tenant, subsystem=req.subsystem, openapi=req.openapi,
                deploy=req.deploy, credentials=req.credentials, policy_text=req.policy_text,
                include_tags=req.include_tags, flows=req.flows, use_codegen=req.use_codegen,
                max_read_flows=req.max_read_flows, progress=_progress, lifecycle=_lifecycle)
            job["report"] = rep.model_dump()
            job["status"] = "completed"
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
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=ALL_SUBSYSTEMS)
    return [m.model_dump() for m in build_manifests(reg.skills)]


@app.get("/v1/skills/{skill_id}")
async def get_skill(skill_id: str, x_tenant_key: str | None = Header(default=None)) -> dict:
    tenant = await _auth_tenant(x_tenant_key)
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=ALL_SUBSYSTEMS)
    m = next((x for x in build_manifests(reg.skills) if x.name == skill_id), None)
    if m is None:
        raise HTTPException(status_code=404, detail=f"本公司无此 Skill: {skill_id}")
    return m.model_dump()


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
    try:
        subsystem = Subsystem(sub_str)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"未知子系统: {sub_str}") from e
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
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=ALL_SUBSYSTEMS)
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
