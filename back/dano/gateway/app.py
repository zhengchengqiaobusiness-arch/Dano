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
from pathlib import Path
import shutil
import uuid

import structlog
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from dano.assets.repository import AssetRepository
from dano.catalog.manifest import build_function_tools, build_manifests, skill_id_of
from dano.execution.connectors.auth import AuthManager
from dano.execution.connectors.executor import RealActionExecutor, SystemEndpoint, system_key_for
from dano.execution.harness.harness import Harness
from dano.orchestrator.orchestrator import Orchestrator
from dano.orchestrator.capability_runtime import CapabilityInvokePayload
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


def _page_semantic_client(*required_methods: str):
    """复用发布评审的 LLM client；缺少对应能力时返回 None，让调用方走确定性兜底。"""
    try:
        from dano.agent_tools import tools as agent_tools
        board = agent_tools._review_board
        client = getattr(board, "client", None) if board is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("page.semantic_client_unavailable", error=str(exc))
        return None
    if client is None:
        return None
    if any(not hasattr(client, method) for method in required_methods):
        return None
    return client


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


_RECENT_RECORDING_ACTIONS: dict[str, None] = {}
_MAX_RECENT_RECORDING_ACTIONS = 4096


def _new_recording_action() -> str:
    """Return a process-unique action compatible with the public action-name grammar."""
    while True:
        action = f"action_{uuid.uuid4().hex}"
        if action not in _RECENT_RECORDING_ACTIONS:
            break
    if len(_RECENT_RECORDING_ACTIONS) >= _MAX_RECENT_RECORDING_ACTIONS:
        _RECENT_RECORDING_ACTIONS.pop(next(iter(_RECENT_RECORDING_ACTIONS)), None)
    _RECENT_RECORDING_ACTIONS[action] = None
    return action


class _WebSocketSendQueue:
    """Serialize writes; reliable controls queue, while screenshots coalesce latest-only."""

    _FRAME_ITEM = object()

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._failure: BaseException | None = None
        self._background: set[asyncio.Task] = set()
        self._latest_frame: dict | None = None
        self._frame_enqueued = False
        self._writer = asyncio.create_task(self._run())

    async def send_json(self, message: dict) -> None:
        if self._closed:
            if self._failure is not None:
                raise self._failure
            raise RuntimeError("websocket sender is closed")
        acknowledged = asyncio.get_running_loop().create_future()
        await self._queue.put((message, acknowledged))
        await acknowledged

    def send_background(self, message: dict) -> None:
        """Enqueue a synchronous recorder callback without leaking task failures."""
        if self._closed:
            return
        task = asyncio.create_task(self.send_json(message))
        self._background.add(task)
        task.add_done_callback(self._background_done)

    def send_latest_frame(self, message: dict) -> bool:
        """Keep at most one unsent screenshot and return without waiting for network I/O."""
        if self._closed:
            return False
        self._latest_frame = message
        if not self._frame_enqueued:
            self._frame_enqueued = True
            self._queue.put_nowait(self._FRAME_ITEM)
        return True

    def _background_done(self, task: asyncio.Task) -> None:
        self._background.discard(task)
        try:
            task.result()
        except (Exception, asyncio.CancelledError):
            pass

    async def _run(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    self._closed = True
                    return
                if item is self._FRAME_ITEM:
                    message = self._latest_frame
                    self._latest_frame = None
                    self._frame_enqueued = False
                    acknowledged = None
                    if message is None:
                        continue
                else:
                    message, acknowledged = item
                try:
                    await self._ws.send_json(message)
                except BaseException as exc:
                    self._failure = exc
                    self._closed = True
                    if acknowledged is not None and not acknowledged.done():
                        acknowledged.set_exception(exc)
                    self._reject_pending(exc)
                    return
                else:
                    if acknowledged is not None and not acknowledged.done():
                        acknowledged.set_result(None)
        except asyncio.CancelledError as exc:
            self._failure = exc
            self._closed = True
            self._reject_pending(exc)
            raise

    def _reject_pending(self, exc: BaseException) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item is None:
                continue
            if item is self._FRAME_ITEM:
                self._latest_frame = None
                self._frame_enqueued = False
                continue
            _, acknowledged = item
            if not acknowledged.done():
                acknowledged.set_exception(exc)

    async def close(self) -> None:
        if self._background:
            await asyncio.gather(*tuple(self._background), return_exceptions=True)
        if not self._writer.done():
            await self._queue.put(None)
        await asyncio.gather(self._writer, return_exceptions=True)


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
    subs = await _tenant_subsystems(tenant)            # 发现该租户的真实系统(任意系统,不写死)
    endpoints = await _load_endpoints(tenant, subs)
    executor = RealActionExecutor(endpoints=endpoints, auth_manager=AuthManager())
    registry = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=subs)
    harness = Harness(action_executor=executor, resolve_credentials=_resolve_creds)
    return Orchestrator(registry=registry, store=repo, harness=harness,
                        action_executor=executor, resolve_credentials=_resolve_creds,
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


# ── 运行期 token(抓请求路径):录制自动抓 → 存 PG(表 runtime_token),可查/可刷新;过期前端换一下即可,免重录 ──
class TokenUpsertReq(BaseModel):
    tenant: str
    subsystem: str
    headers: dict[str, str] | None = None     # 整组鉴权头(优先);或下面 token 三件套只更一个头
    token: str | None = None
    header_name: str = "Authorization"
    token_prefix: str = "Bearer "


@app.get("/settings/token")
async def get_runtime_token(tenant: str, subsystem: str, reveal: bool = False) -> dict:
    """查某 (tenant, subsystem) 运行期用的鉴权头(token)。默认打码;reveal=true 明文(管理用)。"""
    from dano.infra.token_store import get_token, mask_headers
    rec = await get_token(tenant, subsystem)
    if not rec:
        return {"tenant": tenant, "subsystem": subsystem, "has_token": False, "headers": {}}
    headers = rec.get("headers") or {}
    return {"tenant": tenant, "subsystem": subsystem, "has_token": bool(headers),
            "headers": headers if reveal else mask_headers(headers),
            "source": rec.get("source"), "updated_at": rec.get("updated_at")}


@app.put("/settings/token")
async def put_runtime_token(req: TokenUpsertReq) -> dict:
    """更新/刷新某 (tenant, subsystem) 的运行期 token(过期时换一份,免重录)。
    传 headers 用整组;或只传 token(+header_name/token_prefix)更一个头 —— 都会与已存的合并
    (可只换 Authorization,保留 Tenant-Id 等)。"""
    from dano.infra.token_store import get_token_headers, mask_headers, save_token
    headers = {k: v for k, v in (req.headers or {}).items() if v}
    if not headers and req.token:
        headers[req.header_name] = f"{req.token_prefix}{req.token}"
    if not headers:
        raise HTTPException(status_code=400, detail="需提供 headers 或 token")
    merged = {**(await get_token_headers(req.tenant, req.subsystem)), **headers}
    rec = await save_token(req.tenant, req.subsystem, merged, source="manual")
    if not rec:
        raise HTTPException(status_code=500, detail="token 保存失败(DB 不可用?)")
    return {"ok": True, "tenant": req.tenant, "subsystem": req.subsystem,
            "headers": mask_headers(merged), "updated_at": rec.get("updated_at")}


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
    flows: list[dict] = []         # 写/复合流程声明 [{flow, actions?, test_input}]


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
                           flows=req.flows, lifecycle=_lifecycle)
    await _auto_export(req.tenant)
    return report.model_dump()


async def _request_fields_msg(chosen: dict, candidates: list[dict], samples: dict,
                              reads: list[dict] | None = None, storage: dict | None = None,
                              required_labels: set | None = None, page_enum_options: dict | None = None) -> dict:
    """构造 request_fields 消息:字段表(含 type/required)+ 候选请求 + select(Q2)+ identity(Q1)。"""
    from dano.execution.page.request_capture import (apply_page_enum_options, page_enum_selects, flatten_body,
                                                     looks_internal_param_name, suggest_assignee_names,
                                                     suggest_identity, suggest_list_selects,
                                                     suggest_select_names, suggest_selects,
                                                     suggest_workflow_steps)

    def _path(u: str) -> str:
        i = u.find("//")
        return u[u.find("/", i + 2):] if i >= 0 and u.find("/", i + 2) >= 0 else u
    cand_list = [{"idx": i, "method": (c.get("method") or "POST").upper(), "path": _path(c.get("url") or "")}
                 for i, c in enumerate(candidates)]
    pd = chosen.get("post_data")
    # 列表多选(participants[]=选了多个人)先识别 → 折叠成**一个**列表参数,逐元素叶子不再单独冒出来
    list_selects = suggest_list_selects(pd, reads or [], samples)
    list_paths = [s["path"] for s in list_selects]
    fields = flatten_body(pd, samples, required_labels, collapse_paths=list_paths)
    # 传 samples:用录制选中的显示名消歧/确认(大字典里短码也能精确绑对那项);跳过已被列表多选接管的数组下逐元素叶子
    selects = suggest_selects(pd, reads or [], samples, skip_paths=list_paths, fields=fields) + list_selects
    # 页面枚举地面真值:用录制时下拉里真实可见的选项覆盖候选快照(治"加班类型绑到 222 项全量字典");
    #   并为"只在 DOM 有选项、没绑上网络源"的纯枚举字段补一个无来源 enum(agent 传名字即原样提交)。
    apply_page_enum_options(selects, page_enum_options, post_data=pd, fields=fields)
    selects += page_enum_selects(pd, page_enum_options, {s["path"] for s in selects}, fields=fields)
    select_by_path = {s.get("path"): s for s in selects if s.get("path")}
    for f in fields:
        sel = select_by_path.get(f.get("path"))
        if not sel:
            continue
        f["type"] = "list-enum" if sel.get("multi") else "enum"
        if sel.get("options"):
            f["enum_options"] = list(sel.get("options") or [])
        if sel.get("option_map"):
            f["enum_value_map"] = dict(sel.get("option_map") or {})
        f["source_kind"] = "page_enum" if sel.get("enum_source") == "dom" else "api_option"
    # select/选人字段:用录制选项标签当默认参数名(经候选列表桥接),避免漏内部 key(Activity_xxx/嵌套键)
    sel_names = suggest_select_names(selects, samples)
    for f in fields:
        if f.get("path") in sel_names:
            f["suggest_name"] = sel_names[f["path"]]
    # BPMN/Flowable「发起人自选审批人」字段:名字仍是内部节点 ID(Activity_xxx)→ 用流程定义里的节点名
    #   (领导审批/人力审批)或"审批人N"覆盖(确定性,不靠 LLM 猜不透明 ID;DOM/样例已确信的名字不动)。
    assignee_names = suggest_assignee_names(pd, reads or [], samples)
    for f in fields:
        nm = f.get("suggest_name") or ""
        if f.get("path") in assignee_names and (nm == f.get("key") or looks_internal_param_name(nm)):
            f["suggest_name"] = assignee_names[f["path"]]
            f["name_source"] = "assignee"
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
            "identity": suggest_identity(pd, storage, samples)}   # 字段=当前用户/会话值(运行期重取;排除用户填值/平凡撞值)


async def _enhance_flow_field_names(spec):  # noqa: ANN001, ANN202
    """Run at most one bounded naming call for unresolved machine fields."""
    try:
        from dano.agent_tools import tools as tools_module
        from dano.execution.page.flow_spec import (
            apply_llm_field_names,
            llm_field_name_candidates,
        )
        from dano.review.board import suggest_field_names_llm

        board = tools_module._review_board
        fields = llm_field_name_candidates(spec)
        if board is None or not fields:
            return spec
        names = await suggest_field_names_llm(
            board.client,
            (getattr(board, "models", None) or {}).get("acceptance"),
            action=spec.title or (spec.steps[-1].path if spec.steps else ""),
            fields=fields,
        )
        return apply_llm_field_names(spec, names)
    except Exception as exc:  # noqa: BLE001 - naming is non-blocking
        log.warning("flow_spec.field_naming_failed", error=str(exc))
        return spec


def _frontend_recording_field_metadata(raw_steps: list[dict]) -> tuple[dict, set[str], dict]:
    """把前端编辑后的录制步骤投影成样例、必填字段和页面枚举。"""
    from dano.execution.page.recorder import assign_step_field_keys, has_recorded_value

    keymap = assign_step_field_keys(raw_steps)
    samples = {
        keymap[i]: raw_steps[i].get("value", "")
        for i in keymap
        if raw_steps[i].get("op") in ("fill", "select", "pick")
        and has_recorded_value(raw_steps[i])
    }
    required_labels = {
        keymap[i]
        for i in keymap
        if raw_steps[i].get("required")
        and raw_steps[i].get("op") in ("fill", "select", "pick")
    }
    page_enum_options: dict = {}
    last_field_idx: int | None = None
    for i, step in enumerate(raw_steps):
        if i in keymap:
            last_field_idx = i
        if step.get("op") not in ("pick", "select") or not step.get("options"):
            continue
        owner_idx = i if i in keymap else last_field_idx
        field_key = keymap.get(owner_idx, "") if owner_idx is not None else ""
        if not field_key:
            continue
        selected = str(step.get("value", "") or "").strip()
        if selected and field_key not in samples:
            samples[field_key] = selected
        entry = {"options": list(step["options"]), "field_key": field_key, "selected": selected}
        if selected and selected not in page_enum_options:
            page_enum_options[selected] = entry
        if field_key not in page_enum_options:
            page_enum_options[field_key] = entry
    return samples, required_labels, page_enum_options


# ── 方式B:网页内录制(WebSocket:截屏流出 + 输入回传入 + 实时步骤 + 录完发布)──
@app.websocket("/onboarding/page/record")
async def record_ws(ws: WebSocket) -> None:
    """客户在网页里操作我们托管的浏览器,免安装/免命令行。协议见前端 PageRecorder。"""
    await ws.accept()
    sender = _WebSocketSendQueue(ws)
    sess = None
    llm_budget_token = None
    session_action = ""
    try:
        init = await ws.receive_json()
        if init.get("type") != "start" or not init.get("start_url"):
            await sender.send_json({"type": "error", "detail": "首帧须为 {type:'start', start_url, ...}"})
            return
        session_action = _new_recording_action()
        from dano.config import get_settings as _recording_settings
        from dano.infra.llm_control import begin_llm_budget
        llm_budget_token = begin_llm_budget(_recording_settings().llm_session_token_budget)
        from dano.execution.page.recorder import RecordSession
        def on_step(step: dict) -> None:
            sender.send_background({"type": "step", "step": step})

        def on_request(r: dict) -> None:                  # 诊断:抓到的写请求实时推给前端
            sender.send_background({"type": "request", "request": r})

        sess = RecordSession(on_step=on_step, on_request=on_request,
                             intercept_submit=init.get("intercept", True),
                             capture_reads=init.get("capture_reads", True))
        await sess.start(init["start_url"], base_url=init.get("base_url", ""),
                         storage_state=init.get("storage_state") or None,
                         token=init.get("token") or None)   # 贴 token → 预置登录态,免在画面里登录

        async def on_frame(frame: dict) -> None:
            sender.send_latest_frame({"type": "frame", **frame})

        await sess.start_screencast(on_frame)
        await sender.send_json({"type": "started", "action": session_action})

        pending_req: dict | None = None       # 抓到的提交请求,等用户勾完字段再发布
        pending_candidates: list[dict] = []    # 所有 JSON 写请求(候选),供用户手选用哪个
        pending_all_caps: list[dict] = []      # RequestGraph 全量事实,用于无写请求/手选请求重建 FlowSpec
        pending_flow_spec = None               # Step A/B/C/D:可编辑的完整 FlowSpec
        pending_samples: dict = {}             # 录制时填的样例值(选别的请求时重算参数建议)
        pending_reads: list[dict] = []         # 抓到的列表读响应(select 候选源)
        pending_storage: dict | None = None    # 登录态(认 identity 字段)
        pending_required: set = set()          # 录制时表单 * 必填的字段标签
        pending_page_enum_options: dict = {}         # 录制时下拉里真实可见的选项 {选中显示值: [选项文字]}(枚举地面真值)
        pending_page_events: list[dict] = []          # 动作→DOM 变化→请求的脱敏 Observer 时间线
        applied_flow_operations: dict[str, dict] = {}  # flow_update 幂等回执(operation_id → response)
        costly_operation_results: dict[str, dict] = {}
        recording_mode = "intercepted_submit" if init.get("intercept", True) else "real_submit"

        def _costly_key(message: dict) -> str:
            operation_id = str(message.get("operation_id") or "")
            return f"{message.get('type')}:{operation_id}" if operation_id else ""

        async def _replay_costly(message: dict) -> bool:
            key = _costly_key(message)
            if key and key in costly_operation_results:
                await sender.send_json({**costly_operation_results[key], "duplicate": True})
                return True
            return False

        def _remember_costly(message: dict, response: dict) -> None:
            key = _costly_key(message)
            if not key:
                return
            if len(costly_operation_results) >= 128:
                costly_operation_results.pop(next(iter(costly_operation_results)), None)
            costly_operation_results[key] = response

        def _restore_hidden_flow_spec_fields(raw_spec: dict) -> dict:
            if pending_flow_spec is None:
                return raw_spec
            old_by_id = {s.step_id: s for s in pending_flow_spec.steps}
            for step in raw_spec.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                old = old_by_id.get(str(step.get("step_id") or ""))
                if old is None:
                    continue
                # Client receives only a bounded response sample.  Raw response
                # evidence remains authoritative on the server and must never
                # be overwritten by that projection on a round-trip edit.
                if old.response_json is not None:
                    step["response_json"] = old.response_json
                if not step.get("body_source"):
                    step["body_source"] = step.get("backup_body_source") or old.body_source
                headers = step.get("headers")
                if (not headers) or all(v == "***" for v in (headers or {}).values()):
                    step["headers"] = old.headers
                old_selects = {
                    (select.path or "", select.param or ""): select
                    for select in old.selects
                }
                for select in step.get("selects") or []:
                    if not isinstance(select, dict):
                        continue
                    old_select = old_selects.get((str(select.get("path") or ""), str(select.get("param") or "")))
                    if old_select is None:
                        continue
                    if not select.get("source_body"):
                        select["source_body"] = old_select.source_body
                    source_headers = select.get("source_headers") or {}
                    if (not source_headers) or all(value == "***" for value in source_headers.values()):
                        select["source_headers"] = old_select.source_headers
                old_identity = {i.path: i for i in old.identity}
                for idn in step.get("identity") or []:
                    if isinstance(idn, dict) and idn.get("value") == "***":
                        old_idn = old_identity.get(str(idn.get("path") or ""))
                        if old_idn is not None:
                            idn["value"] = old_idn.value
            return raw_spec

        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "input":
                event = msg.get("event") or {}
                try:
                    input_result = await sess.dispatch_input(event)
                except Exception as exc:  # noqa: BLE001 - one bad browser event must not end the session
                    await sender.send_json({
                        "type": "input_error",
                        "detail": str(exc) or exc.__class__.__name__,
                        "event": event,
                        "kind": event.get("kind"),
                        "recoverable": True,
                        "error_type": exc.__class__.__name__,
                    })
                    continue
                if isinstance(input_result, dict) and not input_result.get("ok", True):
                    await sender.send_json({
                        "type": "input_error",
                        "detail": str(input_result.get("error") or "浏览器输入事件执行失败"),
                        "event": event,
                        "kind": input_result.get("kind") or event.get("kind"),
                        "recoverable": bool(input_result.get("recoverable", True)),
                        "error_type": input_result.get("error_type") or "InputDispatchError",
                    })
            elif t == "reset":
                await sess.flush_recording()
                sess.reset()                          # 登录后:丢弃登录步骤,只录业务流程
                await sender.send_json({"type": "reset_ok"})
            elif t == "finalize":
                if await _replay_costly(msg):
                    continue
                before_flush_steps = sess.recorded_raw_steps()
                await sess.flush_recording()
                observed_required_labels = await sess.observed_required_labels()
                observed_page_context = await sess.observed_page_context()
                after_flush_steps = sess.recorded_raw_steps()
                flushed_tail: list[dict] = []
                if len(after_flush_steps) > len(before_flush_steps):
                    flushed_tail = after_flush_steps[len(before_flush_steps):]
                elif before_flush_steps and after_flush_steps and before_flush_steps[-1] != after_flush_steps[-1]:
                    flushed_tail = [after_flush_steps[-1]]
                raw = msg.get("steps")
                if raw is not None:           # 前端编辑后的步骤(删了噪声/重复/调序)→ 以它为准
                    for s in flushed_tail:
                        if s.get("op") not in ("fill", "select", "pick"):
                            continue
                        replaced = False
                        for idx, cur in enumerate(raw):
                            if cur.get("op") == s.get("op") and cur.get("locator") == s.get("locator"):
                                raw[idx] = s
                                replaced = True
                                break
                        if not replaced:
                            raw.append(s)
                    steps = [{"op": s["op"], "locator": s.get("locator"), "field": (s.get("field") or None)}
                             for s in raw]
                    # 字段 key 保持与录制样例、必填标记一致。
                    samples, required_labels, page_enum_options = _frontend_recording_field_metadata(raw)
                    required_labels.update(observed_required_labels)
                else:
                    steps, samples = sess.recorded_steps()
                    required_labels = sess.recorded_required_labels()
                    required_labels.update(observed_required_labels)
                    page_options_by_field = sess.recorded_page_enum_options()  # {字段key: {options, field_key, selected}}
                    # 枚举地面真值:既按「选中显示值」也对到「字段 key」,使 page_enum_selects 在 body leaf
                    # 不出现 label 但出现内部英文名时也能命中(治"请假类型=病假 → body.leaveType=2"漏识别)。
                    page_enum_options = {}
                    for field_key, raw_entry in (page_options_by_field or {}).items():
                        opts = raw_entry.get("options") if isinstance(raw_entry, dict) else raw_entry
                        if not opts:
                            continue
                        label = str((raw_entry.get("selected") if isinstance(raw_entry, dict) else None)
                                    or samples.get(field_key, "") or "").strip()
                        entry = {"options": list(opts), "field_key": field_key, "selected": label}
                        if label and label not in page_enum_options:
                            page_enum_options[label] = entry
                        if field_key and field_key not in page_enum_options:
                            page_enum_options[field_key] = entry
                # Submit-time form evidence survives modal teardown and fills
                # untouched/compound controls (for example a two-input date
                # range) into the same sample map used for body-field matching.
                for field_key, value in sess.recorded_form_samples().items():
                    samples.setdefault(field_key, value)
                sub = init.get("subsystem", "A-报销")
                login_state = await sess.storage_state()   # 录制会话(已真人登录)的登录态快照

                # 抓请求路径优先:列出所有 JSON 写请求(候选),默认选最像提交的那个,生成 FlowSpec 工作台。
                # 发布只从 FlowSpec 出口走,避免字段勾选表和工作台两套口径。
                from dano.execution.page.request_capture import (flatten_body, json_write_requests,
                                                                 looks_like_auth_write, pick_submit_request)
                all_caps = (sess.captured_all_requests()
                            if hasattr(sess, "captured_all_requests") else sess.captured_requests())
                cands = [c for c in json_write_requests(all_caps)
                         if flatten_body(c.get("post_data"))                       # 有可勾字段的
                         and not looks_like_auth_write(c.get("url") or "", c.get("post_data"))]  # 排除登录/鉴权写
                pending_all_caps = list(all_caps or [])
                pending_candidates = cands
                pending_samples = samples
                pending_reads = sess.captured_reads()       # select 候选源(选领导)
                pending_storage = login_state               # identity 字段识别
                pending_required = required_labels          # 表单 * 必填
                pending_page_enum_options = page_enum_options           # 下拉枚举地面真值
                pending_page_events = sess.recorded_page_events()
                log.info("record.finalize", captured=len(all_caps), cands=len(cands), steps=len(steps),
                         captured_urls=[((c.get("method") or ""), (c.get("url") or "")[:140]) for c in all_caps][:25])
                if not cands and not all_caps:
                    # 一条写请求都没抓到 → 多半是**没点「提交」**或刚重连过会话(新浏览器没有旧请求)→ 明确引导重点提交,
                    # 不再发布页面回放脚本；现场还在，重新点击一次真实提交即可抓请求。
                    await sender.send_json({"type": "result", "action": session_action,
                        "parsed_steps": len(steps), "report": {"ok": False,
                        "reason": "没抓到任何提交接口请求 —— 拦截模式下**点一次「提交」**才会抓到那条请求。"
                                  "若刚重连过会话/浏览器,请在画面里**重新点一次「提交」**(现场还在),然后再发布。"}})
                    continue
                if cands:
                    chosen = pick_submit_request(cands, samples) or cands[-1]
                    pending_req = chosen
                    request_fields = await _request_fields_msg(
                        chosen, cands, samples, pending_reads,
                        pending_storage, pending_required, page_enum_options,
                    )
                    await sender.send_json({**request_fields, "action": session_action})
                    # Step A: 灰度附带下发 flow_spec 摘要 + 完整 spec;前端暂不消费,零回归。
                    # 同时把 spec 存到 pending_flow_spec 供后续 flow_update / step_naming / 业务说明编辑。
                    try:
                        from dano.execution.page.flow_spec import (
                            to_flow_spec,
                        )
                        pending_flow_spec = to_flow_spec(
                            captured_requests=all_caps,
                            reads=pending_reads,
                            samples=pending_samples,
                            storage_state=pending_storage,
                            required_labels=pending_required,
                            page_enum_options=pending_page_enum_options,
                            page_context=observed_page_context,
                            recording_mode=recording_mode,
                            diagnostics=sess.captured_diagnostics(),
                            page_events=pending_page_events,
                            tenant=init.get("tenant", ""),
                            subsystem=init.get("subsystem", ""),
                        )
                        pending_flow_spec = await _enhance_flow_field_names(pending_flow_spec)
                        from dano.execution.page.flow_spec import (
                            flow_spec_to_client,
                            flow_spec_to_summary,
                            validate_flow_spec,
                        )
                        response = {
                            "type": "flow_spec",
                            "action": session_action,
                            "operation": "finalize",
                            "operation_id": msg.get("operation_id"),
                            "flow_spec": flow_spec_to_summary(pending_flow_spec),
                            "full_spec": flow_spec_to_client(pending_flow_spec),
                            "check_report": validate_flow_spec(pending_flow_spec),
                        }
                        _remember_costly(msg, response)
                        await sender.send_json(response)
                    except Exception as _fs_err:  # noqa: BLE001
                        log.warning("flow_spec.emit_failed", error=str(_fs_err))
                        await sender.send_json({"type": "result", "action": session_action,
                                            "report": {"ok": False, "stage": "flow_spec_build",
                                                       "reason": f"FlowSpec 生成失败:{_fs_err}"},
                                            "parsed_steps": 0})
                    continue

                # 录制 V2:没有 JSON 写请求时仍然下发 RequestGraph/GET FlowSpec 工作台，
                # 让用户可以从已捕获读接口编排 query/list_options 能力。
                try:
                    from dano.execution.page.flow_spec import (
                        flow_spec_to_client,
                        flow_spec_to_summary,
                        to_flow_spec,
                        validate_flow_spec,
                    )
                    pending_flow_spec = to_flow_spec(
                        captured_requests=all_caps,
                        reads=pending_reads,
                        samples=pending_samples,
                        storage_state=pending_storage,
                        required_labels=pending_required,
                        page_enum_options=pending_page_enum_options,
                        page_context=observed_page_context,
                        recording_mode=recording_mode,
                        diagnostics=sess.captured_diagnostics(),
                        page_events=pending_page_events,
                        tenant=init.get("tenant", ""),
                        subsystem=init.get("subsystem", ""),
                    )
                    response = {
                        "type": "flow_spec",
                        "action": session_action,
                        "operation": "finalize",
                        "operation_id": msg.get("operation_id"),
                        "flow_spec": flow_spec_to_summary(pending_flow_spec),
                        "full_spec": flow_spec_to_client(pending_flow_spec),
                        "check_report": validate_flow_spec(pending_flow_spec),
                    }
                    _remember_costly(msg, response)
                    await sender.send_json(response)
                except Exception as _fs_err:  # noqa: BLE001
                    log.warning("flow_spec.read_only_emit_failed", error=str(_fs_err))
                    await sender.send_json({"type": "result", "action": session_action,
                                        "report": {"ok": False,
                                                   "stage": "capture_request",
                                                   "reason": "没有抓到可发布的 JSON 提交请求，且 GET/读请求 FlowSpec 生成失败:"
                                                             f"{_fs_err}"},
                                        "parsed_steps": 0})
                continue
            elif t == "choose_request":
                # 用户在候选里手选用哪个写请求(噪声误判/多写请求时)→ 重发该请求的字段表
                idx = msg.get("idx", 0)
                if pending_candidates and 0 <= idx < len(pending_candidates):
                    pending_req = pending_candidates[idx]
                    request_fields = await _request_fields_msg(
                        pending_req, pending_candidates, pending_samples,
                        pending_reads, pending_storage, pending_required,
                        pending_page_enum_options,
                    )
                    await sender.send_json({**request_fields, "action": session_action})
                    try:
                        from dano.execution.page.flow_spec import (
                            flow_spec_to_client,
                            flow_spec_to_summary,
                            to_flow_spec,
                            validate_flow_spec,
                        )
                        # 录制 V2:手选写请求必须同步到 FlowSpec。保留所有非写请求事实，
                        # 只把写请求候选收敛为当前 chosen，避免发布仍用自动候选。
                        chosen = pending_req
                        selected_caps = [
                            r for r in (pending_all_caps or [])
                            if r == chosen or r not in pending_candidates
                        ]
                        pending_flow_spec = to_flow_spec(
                            captured_requests=selected_caps,
                            reads=pending_reads,
                            samples=pending_samples,
                            storage_state=pending_storage,
                            required_labels=pending_required,
                            page_enum_options=pending_page_enum_options,
                            page_context=observed_page_context,
                            recording_mode=recording_mode,
                            diagnostics=sess.captured_diagnostics(),
                            page_events=pending_page_events,
                            tenant=init.get("tenant", ""),
                            subsystem=init.get("subsystem", ""),
                        )
                        pending_flow_spec = await _enhance_flow_field_names(pending_flow_spec)
                        await sender.send_json({
                            "type": "flow_spec_updated",
                            "flow_spec": flow_spec_to_summary(pending_flow_spec),
                            "full_spec": flow_spec_to_client(pending_flow_spec),
                            "check_report": validate_flow_spec(pending_flow_spec),
                        })
                    except Exception as e:  # noqa: BLE001
                        await sender.send_json({"type": "error", "detail": f"choose_request flow_spec failed: {e}"})
            # Step B: 前端编辑 FlowSpec → 应用编辑,返回新 spec
            elif t == "flow_update":
                if pending_flow_spec is None:
                    await sender.send_json({"type": "error", "detail": "no flow_spec loaded"})
                    continue
                edits = msg.get("edits") or []
                operation_id = str(msg.get("operation_id") or "")
                if operation_id and operation_id in applied_flow_operations:
                    await sender.send_json({**applied_flow_operations[operation_id], "duplicate": True})
                    continue
                try:
                    from dano.execution.page.flow_spec import (
                        apply_flow_edits,
                        flow_spec_to_client,
                        flow_spec_to_summary,
                        validate_flow_spec,
                    )
                    pending_flow_spec = apply_flow_edits(pending_flow_spec, edits)
                    response = {
                        "type": "flow_spec_updated",
                        "operation": "flow_update",
                        "operation_id": operation_id,
                        "flow_spec": flow_spec_to_summary(pending_flow_spec),
                        "full_spec": flow_spec_to_client(pending_flow_spec),
                        "check_report": validate_flow_spec(pending_flow_spec),
                    }
                    if operation_id:
                        if len(applied_flow_operations) >= 256:
                            applied_flow_operations.pop(next(iter(applied_flow_operations)), None)
                        applied_flow_operations[operation_id] = response
                    await sender.send_json(response)
                except Exception as e:  # noqa: BLE001
                    # 前端会先做乐观更新。失败时必须回传服务端权威版本，否则页面与
                    # pending_flow_spec 分叉，下一次发布会发生指纹冲突或使用旧字段。
                    await sender.send_json({
                        "type": "error",
                        "detail": f"flow_update failed: {e}",
                        "operation": "flow_update",
                        "operation_id": operation_id,
                        "full_spec": flow_spec_to_client(pending_flow_spec),
                        "check_report": validate_flow_spec(pending_flow_spec),
                    })
            elif t == "flow_replace":
                operation_id = str(msg.get("operation_id") or "")
                if operation_id and operation_id in applied_flow_operations:
                    await sender.send_json({**applied_flow_operations[operation_id], "duplicate": True})
                    continue
                try:
                    from dano.execution.page.flow_spec import (
                        FlowSpec,
                        append_flow_version,
                        flow_spec_to_client,
                        flow_spec_to_summary,
                        refresh_review_items,
                        validate_flow_spec,
                    )
                    raw_spec = msg.get("flow_spec")
                    if not isinstance(raw_spec, dict):
                        await sender.send_json({"type": "error", "detail": "flow_replace missing flow_spec object"})
                        continue
                    raw_spec = _restore_hidden_flow_spec_fields(raw_spec)
                    pending_flow_spec = append_flow_version(
                        refresh_review_items(FlowSpec.model_validate(raw_spec)),
                        "json_replace",
                        reason="前端 JSON 编辑回写",
                        actor="user",
                    )
                    response = {
                        "type": "flow_spec_updated",
                        "operation": "flow_replace",
                        "operation_id": operation_id,
                        "flow_spec": flow_spec_to_summary(pending_flow_spec),
                        "full_spec": flow_spec_to_client(pending_flow_spec),
                        "check_report": validate_flow_spec(pending_flow_spec),
                    }
                    if operation_id:
                        if len(applied_flow_operations) >= 256:
                            applied_flow_operations.pop(next(iter(applied_flow_operations)), None)
                        applied_flow_operations[operation_id] = response
                    await sender.send_json(response)
                except Exception as e:  # noqa: BLE001
                    await sender.send_json({
                        "type": "error", "detail": f"flow_replace failed: {e}",
                        "operation": "flow_replace", "operation_id": operation_id,
                    })
            # Bug 修复: 前端收到 "step not found" 时主动刷新 spec
            elif t == "refresh_flow_spec":
                if pending_flow_spec is None:
                    await sender.send_json({"type": "error", "detail": "no flow_spec loaded"})
                    continue
                from dano.execution.page.flow_spec import flow_spec_to_client, flow_spec_to_summary, validate_flow_spec
                await sender.send_json({
                    "type": "flow_spec",
                    "flow_spec": flow_spec_to_summary(pending_flow_spec),
                    "full_spec": flow_spec_to_client(pending_flow_spec),
                    "check_report": validate_flow_spec(pending_flow_spec),
                })
            # 能力编排:LLM 生成对外可调用能力草案；失败则由 flow_spec 确定性规则兜底。
            elif t == "orchestrate_flow":
                if await _replay_costly(msg):
                    continue
                if pending_flow_spec is None:
                    await sender.send_json({"type": "error", "detail": "no flow_spec loaded"})
                    continue
                try:
                    from dano.config import get_settings
                    from dano.execution.page.flow_spec import (
                        FlowSpec,
                        flow_spec_to_client,
                        flow_spec_to_summary,
                        flow_operation_report,
                        refresh_review_items,
                        run_recording_pi_loop,
                        validate_flow_spec,
                    )
                    raw_spec = msg.get("flow_spec")
                    if isinstance(raw_spec, dict):
                        raw_spec = _restore_hidden_flow_spec_fields(raw_spec)
                        pending_flow_spec = refresh_review_items(FlowSpec.model_validate(raw_spec))
                    before_operation = pending_flow_spec.model_copy(deep=True)
                    force_replan = bool(msg.get("force_replan"))
                    pending_flow_spec = await run_recording_pi_loop(
                        pending_flow_spec,
                        llm_client=_page_semantic_client("complete_json"),
                        model=get_settings().pi_model,
                        mode="plan",
                        force_replan=force_replan,
                    )
                    operation = "replan" if force_replan else "plan"
                    response = {
                        "type": "flow_spec_updated",
                        "operation": operation,
                        "operation_id": msg.get("operation_id"),
                        "flow_spec": flow_spec_to_summary(pending_flow_spec),
                        "full_spec": flow_spec_to_client(pending_flow_spec),
                        "check_report": validate_flow_spec(pending_flow_spec),
                        "operation_report": flow_operation_report(
                            before_operation, pending_flow_spec, operation=operation,
                        ),
                    }
                    _remember_costly(msg, response)
                    await sender.send_json(response)
                except Exception as e:  # noqa: BLE001
                    await sender.send_json({"type": "error", "detail": f"orchestrate_flow failed: {e}"})
            # 一键修正:确定性补齐 + LLM 受限 patch；后端应用后重跑校验。
            elif t == "auto_fix_flow":
                if await _replay_costly(msg):
                    continue
                if pending_flow_spec is None:
                    await sender.send_json({"type": "error", "detail": "no flow_spec loaded"})
                    continue
                try:
                    from dano.config import get_settings
                    from dano.execution.page.flow_spec import (
                        flow_spec_to_client,
                        flow_spec_to_summary,
                        flow_operation_report,
                        run_recording_pi_loop,
                        validate_flow_spec,
                    )
                    before_operation = pending_flow_spec.model_copy(deep=True)
                    pending_flow_spec = await run_recording_pi_loop(
                        pending_flow_spec,
                        llm_client=_page_semantic_client("complete_json"),
                        model=get_settings().pi_model,
                        mode="repair",
                    )
                    response = {
                        "type": "flow_spec_updated",
                        "operation": "repair",
                        "operation_id": msg.get("operation_id"),
                        "flow_spec": flow_spec_to_summary(pending_flow_spec),
                        "full_spec": flow_spec_to_client(pending_flow_spec),
                        "check_report": validate_flow_spec(pending_flow_spec),
                        "operation_report": flow_operation_report(
                            before_operation, pending_flow_spec, operation="repair",
                        ),
                    }
                    _remember_costly(msg, response)
                    await sender.send_json(response)
                except Exception as e:  # noqa: BLE001
                    await sender.send_json({"type": "error", "detail": f"auto_fix_flow failed: {e}"})
            # Step D2: LLM 给每个 step 起业务名
            elif t == "step_naming":
                if pending_flow_spec is None:
                    await sender.send_json({"type": "error", "detail": "no flow_spec loaded"})
                    continue
                try:
                    from dano.execution.page.flow_spec import (
                        flow_spec_to_client,
                        flow_spec_to_summary,
                        rename_steps_with_llm,
                        validate_flow_spec,
                    )
                    pending_flow_spec = rename_steps_with_llm(
                        pending_flow_spec,
                        llm_client=_page_semantic_client("name_step"),
                    )
                    await sender.send_json({
                        "type": "step_names",
                        "flow_spec": flow_spec_to_summary(pending_flow_spec),
                        "full_spec": flow_spec_to_client(pending_flow_spec),
                        "check_report": validate_flow_spec(pending_flow_spec),
                    })
                except Exception as e:  # noqa: BLE001
                    await sender.send_json({"type": "error", "detail": f"step_naming failed: {e}"})
            # Step D3: LLM 生成业务说明
            elif t == "business_description":
                if pending_flow_spec is None:
                    await sender.send_json({"type": "error", "detail": "no flow_spec loaded"})
                    continue
                try:
                    from dano.execution.page.flow_spec import (
                        append_flow_version,
                        flow_spec_to_client,
                        flow_spec_to_summary,
                        render_business_description,
                        validate_flow_spec,
                    )
                    desc = render_business_description(
                        pending_flow_spec,
                        llm_client=_page_semantic_client("summarize_flow"),
                    )
                    pending_flow_spec.business_description = desc
                    pending_flow_spec = append_flow_version(
                        pending_flow_spec,
                        "business_description",
                        reason="生成结构化业务说明",
                    )
                    await sender.send_json({
                        "type": "business_description",
                        "description": desc,
                        "flow_spec": flow_spec_to_summary(pending_flow_spec),
                        "full_spec": flow_spec_to_client(pending_flow_spec),
                        "check_report": validate_flow_spec(pending_flow_spec),
                    })
                except Exception as e:  # noqa: BLE001
                    await sender.send_json({"type": "error", "detail": f"business_description failed: {e}"})
            # Step D5: 前端上报 console 错误
            elif t == "console_log_upload":
                entries = msg.get("entries") or []
                if isinstance(entries, list):
                    from dano.execution.page.console_monitor import (
                        ConsoleEntry, filter_errors, is_relevant_error, summarize_console_logs,
                    )
                    parsed = [ConsoleEntry.from_dict(e) for e in entries if isinstance(e, dict)]
                    errors = filter_errors(parsed)
                    relevant = [e for e in errors if is_relevant_error(e.type, e.text)]
                    summary = summarize_console_logs(parsed)
                    if relevant:
                        log.warning("frontend.console_errors",
                                    count=len(relevant),
                                    tenant=init.get("tenant", ""),
                                    subsystem=init.get("subsystem", ""),
                                    sample=relevant[0].text[:200])
                    else:
                        log.info("frontend.console_logs",
                                 total=summary["total"],
                                 errors=summary["errors"],
                                 warnings=summary["warnings"])
            elif t == "publish_request":
                if await _replay_costly(msg):
                    continue
                requested_action = str(msg.get("action") or "")
                if requested_action and requested_action != session_action:
                    log.info(
                        "recording.client_action_overridden",
                        requested_action=requested_action,
                        action=session_action,
                    )
                # FlowSpec 工作台是录制发布唯一入口：步骤、字段、依赖、说明都以同一份可编辑 spec 为准。
                if pending_flow_spec is None:
                    await sender.send_json({"type": "result",
                                        "report": {"ok": False, "stage": "flow_spec_missing",
                                                   "reason": "没有可发布的 FlowSpec；请先停止并分析请求，生成 FlowSpec 后再发布。"}})
                    continue
                try:
                    from dano.execution.page.flow_spec import (
                        FlowSpec,
                        flow_spec_to_client,
                        flow_spec_required_params,
                        flow_spec_fingerprint,
                        flow_spec_to_api_request,
                        flow_spec_to_summary,
                        prepare_flow_release_candidate,
                        validate_flow_spec,
                    )
                    raw_spec = msg.get("flow_spec")
                    expected_fingerprint = str(msg.get("expected_fingerprint") or "")
                    current_fingerprint = flow_spec_fingerprint(pending_flow_spec)
                    if expected_fingerprint and expected_fingerprint != current_fingerprint:
                        await sender.send_json({
                            "type": "result",
                            "report": {
                                "ok": False,
                                "stage": "flow_spec_conflict",
                                "reason": "工作台版本已变化，请使用最新版本重新发布",
                                "expected_fingerprint": expected_fingerprint,
                                "current_fingerprint": current_fingerprint,
                                "full_spec": flow_spec_to_client(pending_flow_spec),
                            },
                        })
                        continue
                    if isinstance(raw_spec, dict):
                        raw_spec = _restore_hidden_flow_spec_fields(raw_spec)
                        pending_flow_spec = FlowSpec.model_validate(raw_spec)
                    # 发布只校验并编译工作台当前版本。Planner/Repair 必须由用户显式点击
                    # “生成/优化能力”触发，禁止在发布阶段静默恢复已删除步骤或改写人工字段。
                    if not pending_flow_spec.capabilities:
                        await sender.send_json({
                            "type": "result",
                            "report": {
                                "ok": False,
                                "stage": "capability_missing",
                                "reason": "尚未生成业务能力；请先点击“生成/优化能力”并确认能力后再发布",
                            },
                        })
                        continue
                    pending_flow_spec, release_candidate = prepare_flow_release_candidate(pending_flow_spec)
                    check_report = validate_flow_spec(pending_flow_spec)
                    if not check_report.get("passed"):
                        await sender.send_json({
                            "type": "result",
                            "report": {
                                "ok": False,
                                "stage": "flow_spec_validate",
                                "reason": "FlowSpec 发布前校验未通过",
                                "clarifications": check_report.get("errors") or [],
                                "check_report": check_report,
                                "full_spec": flow_spec_to_client(pending_flow_spec),
                            },
                        })
                        continue
                    apir, build_errors = flow_spec_to_api_request(pending_flow_spec)
                    if build_errors or not apir:
                        await sender.send_json({
                            "type": "result",
                            "report": {
                                "ok": False,
                                "stage": "flow_spec_build",
                                "reason": "FlowSpec 无法转换成可执行请求",
                                "clarifications": build_errors,
                                "check_report": check_report,
                                "full_spec": flow_spec_to_client(pending_flow_spec),
                            },
                        })
                        continue
                    apir["_flow_spec"] = flow_spec_to_summary(pending_flow_spec)
                    apir["_release_snapshot"] = {
                        **release_candidate,
                        "flow_spec": pending_flow_spec.model_dump(exclude_none=True),
                    }
                    apir["recording_mode"] = recording_mode
                    required = flow_spec_required_params(pending_flow_spec)
                    last_params = apir.get("params") or ((apir.get("steps") or [{}])[-1].get("params") or [])
                except Exception as e:  # noqa: BLE001
                    await sender.send_json({"type": "result",
                                        "report": {"ok": False, "stage": "flow_spec_build",
                                                   "reason": f"FlowSpec 发布构造失败:{e}"}})
                    continue

                sub = init.get("subsystem", "A-报销")
                login_state = await sess.storage_state()
                from dano.execution.page.sessions import save_session
                from dano.onboarding.page_onboard import run_request_onboarding
                save_session(init["tenant"], sub, login_state)
                from dano.infra.token_store import headers_from_api_request, save_token
                _tok_headers = headers_from_api_request(apir)
                if _tok_headers:
                    await save_token(init["tenant"], sub, _tok_headers, source="recording")
                sample_in = apir.get("sample_inputs") or ((apir.get("steps") or [{}])[-1].get("sample_inputs") or {})
                rep = await run_request_onboarding(
                    tenant=init["tenant"], subsystem=sub, action=session_action,
                    title=msg.get("title", ""), api_request=apir, sample_inputs=sample_in,
                    required=required,
                    goal=msg.get("goal") or pending_flow_spec.goal,
                    deploy=init.get("deploy"), storage_state=login_state,
                    allow_repair=False)
                if rep.get("ok"):
                    try:
                        skill_id = rep.get("skill_id") or f"{sub}.{session_action}"
                        version = await _latest_skill_version(
                            init["tenant"], Subsystem(sub), session_action, {"integration": "page"},
                        )
                        await _lifecycle.register_published(skill_id, Subsystem(sub), session_action, version)
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            "recording.lifecycle_register_failed",
                            error=str(e), subsystem=sub, action=session_action,
                        )
                    await _auto_export(init["tenant"])
                response = {"type": "result", "operation": "publish", "action": session_action,
                            "operation_id": msg.get("operation_id"),
                            "report": {**rep, "check_report": check_report,
                                       "full_spec": flow_spec_to_client(pending_flow_spec),
                                       "release": release_candidate,
                                       "recording_mode": recording_mode},
                            "parsed_steps": len(last_params), "via": "flow_spec",
                            "recording_mode": recording_mode,
                            "workflow_steps": len(apir.get("steps") or []) or None}
                _remember_costly(msg, response)
                await sender.send_json(response)
            elif t == "stop":
                await sess.flush_recording()
                # Acknowledge the stop before closing from the server side.  If the
                # browser closes immediately after sending ``stop``, Vite can still
                # be forwarding queued frames and writes into an upstream FIN.
                await sender.send_json({"type": "stopped"})
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        try:
            await sender.send_json({"type": "error", "detail": str(e)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        if sess is not None:
            await sess.stop()
        await sender.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


async def _auto_export(tenant: str) -> None:
    """接入后自动导出该租户已上架 skill(无需手动点)。

    目录:**页面配过的(持久化)> DANO_EXPORT_DIR > 仓库默认** —— 与手动导出落同一处。
    best-effort:导出失败不影响接入结果。
    """
    try:
        from dano.export.agent_skills import write_skills
        out = _current_export_dir()
        written = await write_skills(tenant, out, exclude_skill_ids=await _frozen_skill_ids())
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
                deploy=req.deploy, credentials=req.credentials,
                include_tags=req.include_tags, business_rules=req.business_rules, holidays=req.holidays,
                flows=req.flows, progress=_progress, lifecycle=_lifecycle)
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


def _default_export_dir() -> str:
    return str(Path(__file__).resolve().parents[3] / "export" / "agent-skills")


def _current_export_dir() -> str:
    from dano.execution.page.sessions import get_export_dir
    return get_export_dir(_default_export_dir())


def _known_export_dirs() -> list[str]:
    from dano.execution.page.sessions import get_export_dirs
    return get_export_dirs(_default_export_dir())


def _export_slugs_for_manifest(m: dict) -> set[str]:
    from dano.export.agent_skills import _slug
    slugs = {_slug(str(m.get("name") or ""))}
    business = str(m.get("business") or "").strip()
    subsystem = str(m.get("subsystem") or "").strip()
    if business and subsystem:
        slugs.add(_slug(f"{subsystem}.{business}"))
        slugs.add("dano-oa-index")
    return {s for s in slugs if s}


def _cleanup_export_folders(out_dir: str, slugs: set[str]) -> list[str]:
    """清理已导出的 skill 文件夹。只删 out_dir 下的精确 slug 目录。"""
    base = Path(out_dir).expanduser().resolve()
    removed: list[str] = []
    for slug in sorted(slugs):
        target = (base / slug).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            log.warning("export.cleanup_refused", base=str(base), target=str(target))
            continue
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(str(target))
            log.info("export.folder_removed", folder=str(target))
    return removed


def _cleanup_known_export_folders(slugs: set[str]) -> list[str]:
    removed: list[str] = []
    seen: set[str] = set()
    for out_dir in _known_export_dirs():
        for folder in _cleanup_export_folders(out_dir, slugs):
            if folder not in seen:
                removed.append(folder)
                seen.add(folder)
    return removed


def _asset_type_for_manifest(manifest: dict | None) -> AssetType:
    integration = str((manifest or {}).get("integration") or "").lower()
    if integration == "workflow":
        return AssetType.WORKFLOW
    if integration == "api":
        return AssetType.CONNECTOR
    return AssetType.PAGE_SCRIPT


async def _latest_skill_version(tenant: str, subsystem: Subsystem, action: str, manifest: dict | None = None) -> int:
    versions = await repo.list_versions(_asset_type_for_manifest(manifest), Scope(tenant=tenant, subsystem=subsystem), action)
    return versions[0].version if versions else 1


async def _apply_lifecycle_state(skills: list) -> list:
    rows = {r.skill_id: r for r in await _lifecycle.store.all()}
    for s in skills:
        rec = rows.get(s.skill_id)
        if rec:
            s.lifecycle_state = rec.state.value
            s.frozen = rec.state == SkillState.SUSPENDED
    return skills


async def _frozen_skill_ids() -> set[str]:
    return {r.skill_id for r in await _lifecycle.store.all() if r.state == SkillState.SUSPENDED}


async def _manifests_for_tenant(tenant: str) -> list[dict]:
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=await _tenant_subsystems(tenant))
    await _apply_lifecycle_state(reg.skills)
    return [m.model_dump() for m in build_manifests(reg.skills)]


# ── 契约目录(租户隔离)──
@app.get("/v1/skills")
async def list_skills(x_tenant_key: str | None = Header(default=None)) -> list[dict]:
    tenant = await _auth_tenant(x_tenant_key)
    return await _manifests_for_tenant(tenant)


@app.get("/v1/skills/{skill_id}")
async def get_skill(skill_id: str, x_tenant_key: str | None = Header(default=None)) -> dict:
    tenant = await _auth_tenant(x_tenant_key)
    m = next((x for x in await _manifests_for_tenant(tenant) if x["name"] == skill_id), None)
    if m is None:
        raise HTTPException(status_code=404, detail=f"本公司无此 Skill: {skill_id}")
    return m


@app.delete("/v1/skills/{skill_id}")
async def delete_skill(skill_id: str, x_tenant_key: str | None = Header(default=None)) -> dict:
    """删除本租户的某个 skill:删 PG 资产各版本 + 生命周期记录 + 已导出文件夹。"""
    tenant = await _auth_tenant(x_tenant_key)
    sub_str, _, action = skill_id.partition(".")
    if not action:
        raise HTTPException(status_code=400, detail="skill_id 应为 {subsystem}.{action}")
    manifests = await _manifests_for_tenant(tenant)
    manifest = next((m for m in manifests if m["name"] == skill_id), None)
    subsystem = Subsystem(sub_str)            # 系统标识开放:任意系统皆合法(不存在则下面按 0 行返回 404)
    removed = _cleanup_known_export_folders(_export_slugs_for_manifest(manifest or {"name": skill_id}))
    rows = await repo.delete_by_action(Scope(tenant=tenant, subsystem=subsystem), action)
    lifecycle_rows = await _lifecycle.store.delete(skill_id)
    if rows == 0:
        raise HTTPException(status_code=404, detail=f"本公司无此 Skill: {skill_id}")
    return {"deleted": rows, "lifecycle_deleted": lifecycle_rows, "skill_id": skill_id, "removed_folders": removed}


@app.post("/v1/skills/{skill_id}/freeze")
async def freeze_skill(skill_id: str, x_tenant_key: str | None = Header(default=None)) -> dict:
    """冻结本租户 skill:只清理导出文件夹,保留资产库;后续导出/工具列表跳过该 skill。"""
    tenant = await _auth_tenant(x_tenant_key)
    sub_str, _, action = skill_id.partition(".")
    if not action:
        raise HTTPException(status_code=400, detail="skill_id 应为 {subsystem}.{action}")
    manifests = await _manifests_for_tenant(tenant)
    manifest = next((m for m in manifests if m["name"] == skill_id), None)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"本公司无此 Skill: {skill_id}")
    subsystem = Subsystem(sub_str)
    rec = await _lifecycle.store.get(skill_id)
    if rec is None:
        version = await _latest_skill_version(tenant, subsystem, action, manifest)
        rec = await _lifecycle.register_published(skill_id, subsystem, action, version)
    if rec.state != SkillState.SUSPENDED:
        rec = await _lifecycle.suspend(skill_id)
    removed = _cleanup_known_export_folders(_export_slugs_for_manifest(manifest))
    return {"skill_id": skill_id, "state": rec.state.value if rec else SkillState.SUSPENDED.value,
            "removed_folders": removed}


@app.post("/v1/skills/{skill_id}/resume")
async def resume_skill(skill_id: str, x_tenant_key: str | None = Header(default=None)) -> dict:
    """恢复冻结的 skill:只恢复生命周期状态;不自动重建导出文件夹,下次导出时会重新写出。"""
    tenant = await _auth_tenant(x_tenant_key)
    sub_str, _, action = skill_id.partition(".")
    if not action:
        raise HTTPException(status_code=400, detail="skill_id 应为 {subsystem}.{action}")
    manifests = await _manifests_for_tenant(tenant)
    if not any(m["name"] == skill_id for m in manifests):
        raise HTTPException(status_code=404, detail=f"本公司无此 Skill: {skill_id}")
    subsystem = Subsystem(sub_str)
    rec = await _lifecycle.store.get(skill_id)
    if rec is None:
        manifest = next((m for m in manifests if m["name"] == skill_id), None)
        version = await _latest_skill_version(tenant, subsystem, action, manifest)
        rec = await _lifecycle.register_published(skill_id, subsystem, action, version)
    elif rec.state == SkillState.SUSPENDED:
        rec = await _lifecycle.resume_no_change(skill_id)
    return {"skill_id": skill_id, "state": rec.state.value}


# ── 瘦执行(前端只给 skill_id + input;endpoint/凭证/断言后端取)──
class InvokeReq(BaseModel):
    input: dict | None = Field(default_factory=dict)
    arguments: dict | str | None = Field(default_factory=dict)
    idempotency_key: str | None = None
    confirm: bool = False
    capability: str | None = None
    dry_run: bool = False
    metadata: dict = Field(default_factory=dict)
    protocol: str = "dano.capability_call.v1"


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


def _payload_dict(value) -> dict:  # noqa: ANN001
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        import json as _json
        try:
            loaded = _json.loads(value or "{}")
        except _json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"arguments 非合法 JSON: {e}") from e
        if not isinstance(loaded, dict):
            raise HTTPException(status_code=400, detail="arguments 必须是 JSON object")
        return loaded
    raise HTTPException(status_code=400, detail="payload 必须是 object")


def _normalize_skill_call(req, *, capability: str | None = None) -> dict:  # noqa: ANN001
    args = _payload_dict(getattr(req, "arguments", None))
    input_obj = getattr(req, "input", None)
    if input_obj is not None:
        args.update(_payload_dict(input_obj))
    cap = capability or getattr(req, "capability", None)
    if cap:
        args["__capability"] = cap
    if getattr(req, "dry_run", False):
        args["__dry_run"] = True
    return args


@app.post("/v1/skills/{skill_id}/invoke")
async def invoke_skill(skill_id: str, req: InvokeReq,
                       x_tenant_key: str | None = Header(default=None)) -> dict:
    tenant = await _auth_tenant(x_tenant_key)
    args = _normalize_skill_call(req)
    return await _invoke(tenant, skill_id, args, req.confirm)


@app.post("/v1/skills/{skill_id}/capabilities/{capability}/invoke")
async def invoke_skill_capability(skill_id: str, capability: str, req: CapabilityInvokePayload,
                                  x_tenant_key: str | None = Header(default=None)) -> dict:
    """按 Skill 内的指定 capability 调用。

    这是 P3 的显式能力调用入口；旧 `/invoke` + body.capability 继续兼容。
    """
    if req.capability and req.capability != capability:
        raise HTTPException(status_code=422, detail="body capability must match path capability")
    tenant = await _auth_tenant(x_tenant_key)
    args = _normalize_skill_call(req, capability=capability)
    return await _invoke(tenant, skill_id, args, req.confirm)


# ── function-calling 工具(给聊天端 LLM:① 列工具喂给 LLM ② 执行 LLM 的工具调用)──
@app.get("/v1/tools")
async def list_tools(x_tenant_key: str | None = Header(default=None)) -> list[dict]:
    """导出本租户 Skill 为 OpenAI function-calling tools 数组,聊天端直接喂给 LLM。"""
    tenant = await _auth_tenant(x_tenant_key)
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=await _tenant_subsystems(tenant))
    await _apply_lifecycle_state(reg.skills)
    return build_function_tools([s for s in reg.skills if not s.frozen])


class ToolCallReq(BaseModel):
    name: str                       # 工具名(= skill_id 的点转 __,如 A-OA__submit_leave)
    capability: str | None = None   # 新调用协议:一个 Skill 内的业务能力键(query_status/submit_batch...)
    input: dict | None = None       # 新调用协议:input 优先,arguments 兼容
    arguments: dict | str = Field(default_factory=dict)  # LLM 产出的参数(对象或 JSON 字符串都行)
    confirm: bool = False
    dry_run: bool = False


@app.post("/v1/tools/call")
async def call_tool(req: ToolCallReq, x_tenant_key: str | None = Header(default=None)) -> dict:
    """执行一次 LLM 工具调用:name→skill_id、arguments→input,走与 /invoke 同一受控链路。"""
    tenant = await _auth_tenant(x_tenant_key)
    args = _normalize_skill_call(req)
    return await _invoke(tenant, skill_id_of(req.name), args, req.confirm)


class ToolOptionsReq(BaseModel):
    name: str                       # 工具名(= skill_id 点转 __)
    field: str                      # 要列可选项的**参数名**(选择型字段)
    capability: str | None = None   # 多能力 Skill 必须限定字段所属能力


@app.post("/v1/tools/options")
async def tool_options(req: ToolOptionsReq, x_tenant_key: str | None = Header(default=None)) -> dict:
    """**实时**列出某选择型字段的当前可选项(问题1:把接口放进 skill,选字段时直接调来源接口拉真实选项)。
    skill 不持目标系统凭证 → 经 Dano 用运行期登录态调来源接口,返回 {field, options:[{label,value}], count}。"""
    tenant = await _auth_tenant(x_tenant_key)
    skill_id = skill_id_of(req.name)
    sub_str, _, action = skill_id.partition(".")
    if not action:
        raise HTTPException(status_code=400, detail="name 应能解析为 {subsystem}.{action}")
    orch = await _orchestrator(tenant)
    return await orch.list_field_options(
        Subsystem(sub_str), action, req.field, capability=req.capability or "", tenant=tenant,
    )


class ExportSkillsReq(BaseModel):
    out_dir: str                    # 目标目录(通常是 pi 仓库的 .agents/skills),后端本地写入


@app.post("/export/agent-skills")
async def export_agent_skills_ep(req: ExportSkillsReq,
                                 x_tenant_key: str | None = Header(default=None)) -> dict:
    """把本租户已上架 Skill 导出为 pi 文件式 skill(.agents/skills/<name>/),写入 out_dir。

    后端与目标目录同机时直接写文件,免敲命令。真执行仍在 Dano 侧；导出的脚本调用能力级 invoke 端点。
    """
    tenant = await _auth_tenant(x_tenant_key)
    from dano.execution.page.sessions import save_export_dir
    from dano.export.agent_skills import write_skills
    out = req.out_dir
    frozen = await _frozen_skill_ids()
    frozen_manifests = [m for m in await _manifests_for_tenant(tenant) if m["name"] in frozen]
    try:
        removed = []
        for m in frozen_manifests:
            removed.extend(_cleanup_export_folders(out, _export_slugs_for_manifest(m)))
        written = await write_skills(tenant, out, exclude_skill_ids=frozen)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"写入目录失败:{e}") from e
    save_export_dir(out)                                 # 记住此目录 → 录完自动发布落同一处
    return {"out_dir": out, "count": len(written), "written": written, "removed_frozen_folders": removed}


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
