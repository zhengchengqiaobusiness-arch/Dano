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


async def _load_holidays(tenant: str) -> list[str]:
    """汇总该租户各子系统 env_profile 里登记的日历源(供复合流程 compute 的 business_days)。"""
    out: list[str] = []
    for sub in ALL_SUBSYSTEMS:
        env = await repo.get_published(AssetType.ENV_PROFILE, Scope(tenant=tenant, subsystem=sub),
                                       asset_key=AssetType.ENV_PROFILE.value)
        if env:
            out += list((env.body or {}).get("holidays") or [])
    return sorted(set(out))


async def _orchestrator(tenant: str) -> Orchestrator:
    endpoints = await _load_endpoints(tenant)
    executor = RealActionExecutor(endpoints=endpoints, auth_manager=AuthManager())
    registry = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=ALL_SUBSYSTEMS)
    harness = Harness(action_executor=executor, resolve_credentials=_resolve_creds)
    return Orchestrator(registry=registry, store=repo, harness=harness,
                        action_executor=executor, resolve_credentials=_resolve_creds,
                        holidays=await _load_holidays(tenant))


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


@app.get("/settings/llm-test")
async def llm_test() -> dict:
    """用网关**当前进程的实时配置**真打一发 LLM,返回真实 HTTP 状态——定位生成失败到底是
    401(key 错)还是 400(模型名错)还是 429(限流),不必再猜。不回显 key 值。"""
    import time

    import httpx

    from dano.config import get_settings
    s = get_settings()
    key = (s.pi_api_key or "").strip()
    if not key:
        return {"ok": False, "reason": "no_key", "detail": "网关进程没有 API Key(页面未提交或重启后未重发)"}
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

    这就是"自动去匹配查询":各家 OA 模板不同,导入后查它自己的 /template/template/list,
    把真实模板当菜单给用户选,而不是拿 swagger 原始 tag 当类别、也不写死 templateId。
    """
    import httpx

    from dano.infra.http import tls_verify
    base = req.base_url.rstrip("/")
    tok = (req.token or "").strip()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    last_code = None
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=40, verify=tls_verify()) as c:
        for path in ("/template/template/list?pageNum=1&pageSize=500", "/template/template/select"):
            try:
                r = await c.get(base + path, headers=headers)
                j = r.json()
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(j, dict):
                continue
            last_code = j.get("code", last_code)
            if j.get("code") not in (None, 200, 0):     # RuoYi:HTTP200 + body.code(401 未授权等)
                continue
            rows = j.get("rows") or j.get("data") or []
            if isinstance(rows, dict):
                rows = rows.get("records") or rows.get("list") or []
            for it in rows if isinstance(rows, list) else []:
                if not isinstance(it, dict):
                    continue
                tid = it.get("id") or it.get("templateId") or it.get("defKey")
                if tid is None:
                    continue
                out.append({"templateId": str(tid),
                            "name": it.get("name") or it.get("templateName") or str(tid),
                            "type": it.get("typeName") or it.get("type") or "",
                            "defKey": it.get("defKey") or "",
                            "enableFlag": str(it.get("enableFlag", ""))})
            if out:
                break
    seen: set[str] = set()
    uniq = [t for t in out if not (t["templateId"] in seen or seen.add(t["templateId"]))]
    if not uniq:
        hint = "token 可能已失效(body.code=401)" if last_code not in (None, 200, 0) else "该 OA 无模板配置或路径不同"
        raise HTTPException(status_code=502, detail=f"未查到流程模板:{hint}")
    return {"templates": uniq}


class TemplateFormReq(BaseModel):
    base_url: str
    token: str = ""
    template_id: str


def _walk_form_fields(node: object, out: list[dict]) -> None:
    """递归遍历表单设计器结构,凡带字段模型(__vModel__/vModel)的控件都收为一个字段。"""
    if isinstance(node, dict):
        vm = node.get("__vModel__") or node.get("vModel")
        if isinstance(vm, str) and vm:
            cfg = node.get("__config__") if isinstance(node.get("__config__"), dict) else {}
            label = cfg.get("label") or node.get("label") or vm
            out.append({"key": vm, "label": str(label),
                        "type": str(cfg.get("tag") or node.get("tag") or "")})
        for v in node.values():
            _walk_form_fields(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_form_fields(v, out)


@app.post("/onboarding/template-form")
async def template_form(req: TemplateFormReq) -> dict:
    """查某业务模板的**动态表单字段清单**(请假要 title/reason、报销要别的…),供前端预填 values 骨架。

    走 /biz/form/info?templateId=…;data.formData 是 JSON 串,内含表单设计器结构,从中抽出每个字段。
    抽不出(结构特殊)就返回空,让用户手填——不臆造字段。
    """
    import json

    import httpx

    from dano.infra.http import tls_verify
    base = req.base_url.rstrip("/")
    tok = (req.token or "").strip()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    try:
        async with httpx.AsyncClient(timeout=40, verify=tls_verify()) as c:
            r = await c.get(base + "/biz/form/info",
                            params={"businessId": "", "templateId": req.template_id}, headers=headers)
        j = r.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"取表单失败:{e}") from e
    if not isinstance(j, dict) or j.get("code") not in (None, 200, 0):
        code = j.get("code") if isinstance(j, dict) else "?"
        raise HTTPException(status_code=502, detail=f"取表单失败:body.code={code}(token 是否有效?)")
    data = j.get("data") if isinstance(j.get("data"), dict) else {}
    raw = data.get("formData")
    conf: object = raw
    if isinstance(raw, str):
        try:
            conf = json.loads(raw)
        except Exception:  # noqa: BLE001
            conf = {}
    schema = conf.get("formData") if isinstance(conf, dict) and "formData" in conf else conf
    fields: list[dict] = []
    _walk_form_fields(schema, fields)
    seen: set[str] = set()
    uniq = [f for f in fields if not (f["key"] in seen or seen.add(f["key"]))]
    return {"fields": uniq}


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


async def _auto_export(tenant: str) -> None:
    """接入后自动导出该租户已上架 skill 为 skill-creator 包(无需手动点导出)。

    目录:Linux 用 DANO_EXPORT_DIR(沿用之前目录);Windows/缺省 = 仓库相对 Dano/export/agent-skills。
    best-effort:导出失败不影响接入结果。
    """
    try:
        import os
        from pathlib import Path
        from dano.export.agent_skills import write_skills
        out = os.environ.get("DANO_EXPORT_DIR") or str(Path(__file__).resolve().parents[3] / "export" / "agent-skills")
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


@app.delete("/v1/skills/{skill_id}")
async def delete_skill(skill_id: str, x_tenant_key: str | None = Header(default=None)) -> dict:
    """删除本租户的某个 skill(删 PG 资产各版本)。便于测试重来;按租户隔离,不碰别家。"""
    tenant = await _auth_tenant(x_tenant_key)
    sub_str, _, action = skill_id.partition(".")
    if not action:
        raise HTTPException(status_code=400, detail="skill_id 应为 {subsystem}.{action}")
    try:
        subsystem = Subsystem(sub_str)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"未知子系统: {sub_str}") from e
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


class ExportSkillsReq(BaseModel):
    out_dir: str                    # 目标目录(通常是 pi 仓库的 .agents/skills),后端本地写入


@app.post("/export/agent-skills")
async def export_agent_skills_ep(req: ExportSkillsReq,
                                 x_tenant_key: str | None = Header(default=None)) -> dict:
    """把本租户已上架 Skill 导出为 pi 文件式 skill(.agents/skills/<name>/),写入 out_dir。

    后端与目标目录同机时直接写文件,免敲命令。真执行仍在 Dano 侧;导出的脚本用 curl 调 /v1/tools/call。
    """
    tenant = await _auth_tenant(x_tenant_key)
    from dano.export.agent_skills import write_skills
    try:
        written = await write_skills(tenant, req.out_dir)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"写入目录失败:{e}") from e
    return {"out_dir": req.out_dir, "count": len(written), "written": written}


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
