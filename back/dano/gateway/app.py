"""Dano 网关(阶段一+三对外面)。

- 接入:POST /onboarding(pi 自主生成 → 发布)
- 契约:GET /v1/skills(标准 function-calling 契约,租户隔离)
- 瘦执行:POST /v1/skills/{id}/invoke(前端只给 skill_id+input;后端取资产/凭证/断言执行)
- 资产:GET /assets/published
后端不做 NL 意图/多智能体编排(阶段二交前端)。凭证经 Vault/env,平台只存引用。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import time
from typing import Literal
from urllib.parse import urlsplit
import uuid

import structlog
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from dano.assets.repository import AssetRepository
from dano.business_packs import business_subsystems, default_subsystem
from dano.catalog.manifest import build_function_tools, build_manifests, skill_id_of
from dano.execution.connectors.auth import AuthManager
from dano.execution.connectors.executor import RealActionExecutor, SystemEndpoint, system_key_for
from dano.execution.harness.harness import Harness
from dano.infra.passwords import hash_password, verify_password
from dano.orchestrator.orchestrator import Orchestrator
from dano.orchestrator.capability_runtime import CapabilityInvokePayload
from dano.orchestrator.skills import SkillRegistry
from dano.registry import InMemoryRegistry, PgRegistry, TenantRecord
from dano.shared.asset_bodies import EnvProfileBody
from dano.shared.enums import AssetType, Subsystem
from dano.shared.models import Scope
from dano.infra.run_logging import (
    bind_run_context,
    current_run_notes,
    emit_run_event,
    emit_run_exception,
    log_root,
    note_run_fact,
)

from dano.lifecycle.state_machine import SkillLifecycle
from dano.lifecycle.outbox import InMemoryLifecycleOutboxStore, LifecycleRegistrationReconciler
from dano.resilience.circuit_breaker import InMemoryCounter
from dano.shared.enums import SkillState

log = structlog.get_logger(__name__)


async def _tenant_subsystems(tenant: str) -> list[Subsystem]:
    """该租户**实际拥有**的系统实例(发现式,支持任意系统);发现为空(尚无发布)才退回原型常量兜底。"""
    try:
        subs = await repo.distinct_subsystems(tenant)
    except Exception as e:  # noqa: BLE001 —— DB 异常时仍可读取可选配置
        log.warning("tenant_subsystems.discover_failed", tenant=tenant, error=str(e))
        subs = []
    return subs or [Subsystem(value) for value in business_subsystems(tenant)]


def _effective_subsystem(tenant: str, configured: object = "") -> str:
    return str(configured or default_subsystem(tenant))


def _recording_subsystem(tenant: str, configured: object, start_url: str) -> str:
    """Resolve a stable system identity without exposing a redundant UI field."""
    subsystem = _effective_subsystem(tenant, configured)
    if subsystem:
        return subsystem
    parsed = urlsplit(str(start_url or ""))
    host = str(parsed.hostname or "")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("业务网址端口格式无效") from exc
    authority = f"{host}-{port}" if port is not None else host
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", authority.casefold())).strip("-")
    if not slug:
        raise ValueError("无法从业务网址识别系统标识")
    return slug


_registry = InMemoryRegistry()       # DB 就绪换 PgRegistry(lifespan)
_lifecycle = SkillLifecycle()        # 流程12 Skill 生命周期(进程内;可换 PgSkillStore)
_lifecycle_reconciler = LifecycleRegistrationReconciler(
    _lifecycle,
    InMemoryLifecycleOutboxStore(),
)
_breaker = InMemoryCounter()         # 流程10 失败计数/熔断
_recording_session_registry = None


_RECENT_RECORDING_ACTIONS: dict[str, None] = {}
_MAX_RECENT_RECORDING_ACTIONS = 4096

















async def _start_recording_pi_candidate(factory):  # noqa: ANN001, ANN201
    """Start a disposable candidate and publish it only after success."""
    candidate = factory()
    try:
        await candidate.start()
    except BaseException:
        try:
            await candidate.close()
        except BaseException as close_error:  # noqa: BLE001
            log.warning("recording_pi.failed_candidate_close", error=str(close_error))
        raise
    return candidate














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
    """Serialize WebSocket writes."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._failure: BaseException | None = None
        self._writer = asyncio.create_task(self._run())

    async def send_json(self, message: dict) -> None:
        if self._closed:
            if self._failure is not None:
                raise self._failure
            raise RuntimeError("websocket sender is closed")
        acknowledged = asyncio.get_running_loop().create_future()
        await self._queue.put((message, acknowledged))
        await acknowledged

    async def _run(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    self._closed = True
                    return
                message, acknowledged = item
                try:
                    await self._ws.send_json(message)
                except BaseException as exc:
                    failure = (
                        exc if isinstance(exc, WebSocketDisconnect)
                        else WebSocketDisconnect(code=1006)
                    )
                    self._failure = failure
                    self._closed = True
                    if acknowledged is not None and not acknowledged.done():
                        acknowledged.set_exception(failure)
                    self._reject_pending(failure)
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
            _, acknowledged = item
            if not acknowledged.done():
                acknowledged.set_exception(exc)

    async def close(self) -> None:
        if not self._writer.done():
            await self._queue.put(None)
        await asyncio.gather(self._writer, return_exceptions=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from dano.infra.db import close_pool, init_pool, run_migrations
    from dano.infra.logging import configure_logging
    configure_logging()                    # **先配日志**:否则后台看不到任何记录
    log.info("gateway.starting")
    global _registry, _lifecycle, _lifecycle_reconciler, _breaker
    db_status = "unavailable"
    try:
        await init_pool()
        await run_migrations()
        _registry = PgRegistry()
        # 生命周期/失败计数落 PG:重启后 Skill 状态、暂停态、失败计数不丢(否则已熔断 Skill 复活)
        from dano.lifecycle.pg_store import PgSkillStore
        from dano.lifecycle.pg_outbox import PgLifecycleOutboxStore
        from dano.resilience.circuit_breaker import PgFailureCounter
        _lifecycle = SkillLifecycle(PgSkillStore())
        _lifecycle_reconciler = LifecycleRegistrationReconciler(
            _lifecycle,
            PgLifecycleOutboxStore(),
        )
        _breaker = PgFailureCounter()
        reconcile_result = await _lifecycle_reconciler.reconcile()
        if reconcile_result["completed"] or reconcile_result["pending"]:
            log.info("lifecycle.startup_reconciled", **reconcile_result)
        log.info("gateway.db_ready")
        db_status = "ready"
    except Exception as e:  # noqa: BLE001
        log.warning("gateway.db_unavailable", error=str(e))
    try:                                   # 注入三模型评审 client(发布硬闸门 + 录制语义顾问复用同一 client)
        from dano.agent_tools.tools import set_review_board
        from dano.review.board import ReviewBoard
        set_review_board(ReviewBoard.from_settings())
    except Exception as e:  # noqa: BLE001
        log.warning("gateway.review_board_unavailable", error=str(e))
    emit_run_event(
        "gateway.ready",
        stage="system",
        status="succeeded" if db_status == "ready" else "warning",
        summary="Dano 网关启动完成",
        details={
            "pid": os.getpid(),
            "db": db_status,
            "address": f"127.0.0.1:{os.environ.get('DANO_BACKEND_PORT') or os.environ.get('DANO_GATEWAY_PORT') or '8077'}",
        },
    )
    yield
    if _recording_session_registry is not None:
        await _recording_session_registry.close()
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
    tenant: str = Field(min_length=1)
    subsystem: str = Field(min_length=1)
    headers: dict[str, str] | None = None     # 整组鉴权头(优先);或下面 token 三件套只更一个头
    token: str | None = None
    header_name: str = Field(default="Authorization", min_length=1)
    token_prefix: str = "Bearer "


@app.get("/v1/settings/token")
async def get_runtime_token(
    tenant: str,
    subsystem: str,
    x_tenant_key: str | None = Header(default=None),
) -> dict:
    """查某 (tenant, subsystem) 运行期用的鉴权头；始终打码。"""
    from dano.infra.token_store import get_token, mask_headers
    authenticated_tenant = await _auth_tenant(x_tenant_key)
    if authenticated_tenant != tenant:
        raise HTTPException(status_code=403, detail="不能读取其他租户的 token")
    rec = await get_token(tenant, subsystem)
    if not rec:
        return {"tenant": tenant, "subsystem": subsystem, "has_token": False, "headers": {}}
    headers = rec.get("headers") or {}
    return {"tenant": tenant, "subsystem": subsystem, "has_token": bool(headers),
            "headers": mask_headers(headers),
            "source": rec.get("source"), "updated_at": rec.get("updated_at")}


@app.get("/v1/settings/token/raw")
async def get_runtime_token_raw(
    tenant: str,
    subsystem: str,
    x_tenant_key: str | None = Header(default=None),
) -> dict:
    """Internal self-contained-package fallback; authenticated and never masked."""
    from dano.infra.token_store import get_token, normalize_headers

    authenticated_tenant = await _auth_tenant(x_tenant_key)
    if authenticated_tenant != tenant:
        raise HTTPException(status_code=403, detail="不能读取其他租户的 token")
    rec = await get_token(tenant, subsystem)
    headers = normalize_headers((rec or {}).get("headers") or {})
    return {
        "tenant": tenant,
        "subsystem": subsystem,
        "has_token": bool(headers),
        "headers": headers,
        "source": (rec or {}).get("source"),
        "updated_at": (rec or {}).get("updated_at"),
    }


@app.post("/v1/settings/token")
async def post_runtime_token(
    req: TokenUpsertReq,
    x_tenant_key: str | None = Header(default=None),
) -> dict:
    """更新/刷新某 (tenant, subsystem) 的运行期 token(过期时换一份,免重录)。
    传 headers 用整组;或只传 token(+header_name/token_prefix)更一个头 —— 都会与已存的合并
    (可只换 Authorization,保留 Tenant-Id 等)。"""
    from dano.infra.token_store import mask_headers, update_token_headers
    authenticated_tenant = await _auth_tenant(x_tenant_key)
    if authenticated_tenant != req.tenant:
        raise HTTPException(status_code=403, detail="不能修改其他租户的 token")
    headers = {k: v for k, v in (req.headers or {}).items() if v}
    if not headers and req.token:
        headers[req.header_name] = f"{req.token_prefix}{req.token}"
    if not headers:
        raise HTTPException(status_code=400, detail="需提供 headers 或 token")
    rec = await update_token_headers(req.tenant, req.subsystem, headers, source="manual")
    if not rec:
        raise HTTPException(status_code=500, detail="token 保存失败(DB 不可用?)")
    return {"ok": True, "tenant": req.tenant, "subsystem": req.subsystem,
            "headers": mask_headers(rec.get("headers") or {}), "updated_at": rec.get("updated_at")}


class TokenRefreshRunReq(BaseModel):
    tenant: str | None = None
    subsystem: str | None = None
    force: bool = False


@app.post("/internal/runtime-tokens/refresh-due")
async def refresh_runtime_tokens(
    req: TokenRefreshRunReq,
    x_dano_refresh_key: str | None = Header(default=None),
) -> dict:
    """供 Linux systemd timer 调用；可刷新全部到期来源或指定一个系统。"""
    import secrets

    from dano.config import get_settings
    from dano.infra.token_refresh import refresh_due, refresh_one

    expected = get_settings().token_refresh_key
    if not expected:
        raise HTTPException(status_code=503, detail="未配置 DANO_TOKEN_REFRESH_KEY")
    if not x_dano_refresh_key or not secrets.compare_digest(x_dano_refresh_key, expected):
        raise HTTPException(status_code=401, detail="刷新密钥无效")
    if bool(req.tenant) != bool(req.subsystem):
        raise HTTPException(status_code=400, detail="tenant 与 subsystem 必须同时提供")
    if req.tenant and req.subsystem:
        result = await refresh_one(req.tenant, req.subsystem, force=req.force)
    else:
        result = await refresh_due(force=req.force)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


# ── 租户 ──
class TenantCreate(BaseModel):
    tenant: str
    display_name: str = ""
    username: str = ""    # 后台登录账号名,空=取租户名
    password: str = ""    # 初始密码,空=暂不启用密码登录(仅 api_key)


@app.post("/tenants")
async def create_tenant(req: TenantCreate) -> dict:
    payload = req.model_dump()
    username = (payload.get("username") or req.tenant).strip()
    password = (payload.get("password") or "").strip()
    if username and password:
        payload["username"] = username
        payload["password_hash"] = hash_password(password)
    rec = await _registry.create_tenant(TenantRecord(**payload))
    return rec.model_dump()


# ── 后台登录(每租户一个用户名/密码账号)──
class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
async def auth_login(req: LoginRequest) -> dict:
    """用户名+密码登录;成功返回该租户 api_key(前端沿用 X-Tenant-Key 访问)。"""
    username = req.username.strip()
    rec = await _registry.get_tenant_by_username(username)
    if rec is None or not rec.password_hash:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not verify_password(req.password, rec.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"tenant": rec.tenant, "api_key": rec.api_key}


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@app.post("/auth/change-password")
async def auth_change_password(
    req: ChangePasswordRequest,
    x_tenant_key: str | None = Header(default=None),
) -> dict:
    """已登录租户修改自己的密码;需携带 X-Tenant-Key 确认身份。"""
    tenant = await _auth_tenant(x_tenant_key)
    rec = await _registry.get_tenant_by_key(x_tenant_key)
    if rec is None or not rec.password_hash:
        raise HTTPException(status_code=403, detail="该租户未启用密码登录")
    if not verify_password(req.old_password, rec.password_hash):
        raise HTTPException(status_code=401, detail="原密码错误")
    new_password = req.new_password.strip()
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    await _registry.update_tenant_password(tenant, hash_password(new_password))
    return {"status": "ok"}


# ── 接入(pi 自主生成)──
class OnboardReq(BaseModel):
    tenant: str
    subsystem: str = ""
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
    tenant: str = ""
    subsystem: str = ""


@app.post("/onboarding/preview")
async def onboarding_preview(req: PreviewReq) -> dict:
    """接入前预览:按 tag 返回类别清单与动作数(过滤基础设施),供企业勾选要哪些类别。

    只解析、不 spawn pi、不碰凭证;超大 swagger 据此先圈定范围再接入。
    """
    from dano.capabilities import doc_parser, endpoint_classifier, oa_templates
    spec = req.openapi or {}
    template = oa_templates.match_template(spec, tenant=req.tenant)
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
    tenant: str = ""
    subsystem: str = ""
    include_tags: list[str] = []


@app.post("/onboarding/discover-flows")
async def onboarding_discover(req: DiscoverReq) -> dict:
    """平台自动「找出合适的流程」(图二步骤2-3):返回复合/连接器流程提案,供前端确认后生成。

    只解析 + 套模板知识,不 spawn pi、不碰凭证。前端据此勾选/微调测试输入,再发 /onboarding/start。
    """
    from dano.onboarding.discovery import discover_flows
    return {"flows": discover_flows(req.openapi or {}, req.include_tags, tenant=req.tenant)}


class ListTemplatesReq(BaseModel):
    tenant: str = ""
    base_url: str
    token: str = ""


@app.post("/onboarding/list-templates")
async def list_templates(req: ListTemplatesReq) -> dict:
    """查询目标系统真实的流程模板清单，作为可选业务模板。

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
        for dialect in oa_templates.all_templates(req.tenant):
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
    tenant: str = ""
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
        for dialect in oa_templates.all_templates(req.tenant):
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
    tenant: str = ""
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
                                template_id=req.template_id, probe=probe, tenant=req.tenant)
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
    subsystem = _effective_subsystem(req.tenant, req.subsystem)
    report = await onboard(tenant=req.tenant, subsystem=subsystem, openapi=req.openapi,
                           deploy=req.deploy, credentials=req.credentials,
                           policy_text=req.policy_text, include_tags=req.include_tags,
                           business_rules=req.business_rules, holidays=req.holidays,
                           flows=req.flows, lifecycle=_lifecycle,
                           lifecycle_reconciler=_lifecycle_reconciler)
    await _auto_export(req.tenant)
    return report.model_dump()


# ── 方式B:网页内录制(WebSocket:截屏流出 + 输入回传入 + 实时步骤 + 录完发布)──



async def _publish_canonical_recording(
    *,
    tenant: str,
    subsystem: str,
    action: str,
    title: str,
    goal: dict,
    deploy: str | None,
    storage_state: dict | None,
    run_id: str,
    release_flow_spec,
    release_candidate: dict,
    context,
) -> dict:
    """Freeze and export one complete recording release through one boundary."""
    started = time.monotonic()
    capabilities = list(getattr(release_flow_spec, "capabilities", None) or [])
    capability_names = [
        str(getattr(item, "name", None) or getattr(item, "id", None) or "")
        for item in capabilities
        if getattr(item, "name", None) or getattr(item, "id", None)
    ]
    flow_version = int(dict(getattr(release_flow_spec, "meta", None) or {}).get("current_version") or 0)
    bind_run_context(run_id=run_id, action=action, tenant=tenant, subsystem=subsystem)
    note_run_fact(
        capability_count=len(capabilities),
        capability_names=capability_names,
        flow_version=flow_version,
        skill_id=f"{subsystem}.{action}",
    )
    emit_run_event(
        "recording.publish.started",
        stage="publish",
        status="started",
        summary="开始发布录制资产",
        skill_id=f"{subsystem}.{action}",
        details={
            "skill_id": f"{subsystem}.{action}",
            "capabilities": len(capabilities),
            "capability_names": capability_names,
            "flow_version": flow_version,
        },
    )
    from dano.execution.page.flow_spec import (
        flow_spec_release_payload,
        flow_spec_required_params,
        flow_spec_to_api_request,
        flow_spec_to_summary,
        validate_flow_spec,
    )
    from dano.execution.page.sessions import save_session
    from dano.infra.token_store import headers_from_api_request, save_token
    from dano.onboarding.page_onboard import run_request_onboarding

    machine_verification_enabled = bool(
        (release_candidate.get("machine_verification") or {}).get("enabled", True)
    )
    if machine_verification_enabled:
        check_report = validate_flow_spec(release_flow_spec)
        if not check_report.get("passed"):
            raise ValueError(
                "FlowSpec 发布前校验未通过："
                + "；".join(check_report.get("errors") or [])
            )
    api_request, build_errors = flow_spec_to_api_request(release_flow_spec)
    if build_errors or not api_request:
        raise ValueError("FlowSpec 无法转换成可执行请求：" + "；".join(build_errors or []))
    api_request["_flow_spec"] = flow_spec_to_summary(release_flow_spec)
    api_request["_release_snapshot"] = {
        **release_candidate,
        "flow_spec": flow_spec_release_payload(release_flow_spec),
    }
    api_request["recording_mode"] = "real_submit"
    required = flow_spec_required_params(release_flow_spec)
    sample_inputs = api_request.get("sample_inputs") or (
        (api_request.get("steps") or [{}])[-1].get("sample_inputs") or {}
    )
    save_session(tenant, subsystem, storage_state)
    token_headers = headers_from_api_request(api_request)
    if token_headers:
        await save_token(tenant, subsystem, token_headers, source="recording")
    report = await run_request_onboarding(
        tenant=tenant,
        subsystem=subsystem,
        action=action,
        title=title,
        api_request=api_request,
        sample_inputs=sample_inputs,
        required=required,
        goal=goal,
        deploy=deploy,
        storage_state=storage_state,
        allow_repair=False,
        run_id=run_id,
        recording_pi_required=True,
        recording_machine_validated=machine_verification_enabled,
        direct_recording_export=not machine_verification_enabled,
    )
    bind_run_context(
        run_id=run_id,
        action=action,
        tenant=tenant,
        subsystem=subsystem,
        skill_id=str(report.get("skill_id") or f"{subsystem}.{action}"),
        asset_id=report.get("asset_id"),
    )
    if not report.get("ok"):
        _emit_run_summary(
            run_id=run_id,
            asset_status="failed",
            package_status="skipped",
            started=started,
            failed_stage="publish",
            root_cause=str(report.get("reason") or "录制发布失败"),
        )
        raise RuntimeError(str(report.get("reason") or "录制发布失败"))
    skill_id = str(report.get("skill_id") or f"{subsystem}.{action}")
    version = int(report.get("asset_version") or 0)
    if not version:
        version = await _latest_skill_version(
            tenant,
            Subsystem(subsystem),
            action,
            {"integration": "page"},
        )
    emit_run_event(
        "recording.publish.asset_succeeded",
        stage="publish",
        status="succeeded",
        summary="资产发布成功",
        skill_id=skill_id,
        asset_id=report.get("asset_id"),
        details={
            "asset_id": report.get("asset_id"),
            "asset_version": version,
            "lifecycle": "published",
            "capability_count": len(capabilities),
            "flow_version": flow_version,
        },
    )
    lifecycle = await _lifecycle_reconciler.register_or_defer(
        skill_id=skill_id,
        subsystem=Subsystem(subsystem),
        action=action,
        asset_version=version,
    )
    emit_run_event(
        "recording.lifecycle.completed",
        stage="lifecycle",
        status="succeeded",
        summary="生命周期登记成功",
        skill_id=skill_id,
        details=dict(lifecycle),
    )
    context.ensure_active()
    from dano.onboarding.recording_workflow import WorkflowStep
    await context.progress(WorkflowStep.EXPORTING, "正在导出当前动作的 Skill", 0)
    emit_run_event(
        "recording.export.started",
        stage="export",
        status="started",
        summary="开始导出 Skill 包",
        skill_id=skill_id,
    )
    try:
        await _auto_export(tenant, skill_ids={skill_id}, strict=True)
    except Exception as exc:
        notes = current_run_notes()
        emit_run_exception(
            "recording.export.completed",
            exc,
            stage="export",
            summary="Skill 包导出失败",
            skill_id=skill_id,
            details={"exported_count": notes.get("exported_count", 0), "output_directory": notes.get("export_directory")},
            next_action="检查该 asset version 对应的发布能力契约和 release identity",
        )
        _emit_run_summary(
            run_id=run_id,
            asset_status="published",
            package_status="failed",
            started=started,
            failed_stage="export",
            root_cause=notes.get("root_cause") or type(exc).__name__,
            skill_id=skill_id,
        )
        raise
    notes = current_run_notes()
    package_status = str(notes.get("skill_package_status") or "succeeded")
    exported_count = int(notes.get("exported_count") or 0)
    if package_status == "failed" or exported_count <= 0:
        emit_run_event(
            "recording.export.completed",
            stage="export",
            status="failed",
            level="error",
            summary="Skill 包导出失败",
            skill_id=skill_id,
            details={
                "exported_count": exported_count,
                "output_directory": notes.get("export_directory"),
                "canonical_contract_present": notes.get("canonical_contract_present"),
                "code": notes.get("root_cause") or "REQUESTED_SKILL_PACKAGE_NOT_WRITTEN",
            },
            error={
                "code": notes.get("root_cause") or "REQUESTED_SKILL_PACKAGE_NOT_WRITTEN",
                "type": "ExportError",
                "message": "requested skill package was not written",
                "cause": "当前发布资产没有可供包生成器读取的规范能力契约"
                if notes.get("root_cause") == "CANONICAL_CAPABILITY_CONTRACT_MISSING"
                else "指定 Skill 未写出包",
                "traceback": None,
                "artifact_refs": [f"skill_id:{skill_id}"],
            },
            next_action="检查该 asset version 对应的发布能力契约和 release identity",
        )
        _emit_run_summary(
            run_id=run_id,
            asset_status="published",
            package_status="failed",
            started=started,
            failed_stage="export",
            root_cause=notes.get("root_cause") or "REQUESTED_SKILL_PACKAGE_NOT_WRITTEN",
            skill_id=skill_id,
        )
    else:
        emit_run_event(
            "recording.export.completed",
            stage="export",
            status="succeeded",
            summary="Skill 包导出成功",
            skill_id=skill_id,
            details={"exported_count": exported_count, "output_directory": notes.get("export_directory")},
        )
        _emit_run_summary(
            run_id=run_id,
            asset_status="published",
            package_status="succeeded",
            started=started,
            skill_id=skill_id,
        )
    return {
        **report,
        **lifecycle,
        "skill_id": skill_id,
        "asset_version": version,
        "release": release_candidate,
        "capability_count": len(release_flow_spec.capabilities or []),
    }


@app.websocket("/onboarding/page/record")
async def record_ws(ws: WebSocket) -> None:
    """Thin transport for the canonical recording workflow."""
    from dano.onboarding.recording_gateway import (
        RecordingSessionRegistry,
        RecordingSessionConfig,
    )
    from dano.onboarding.recording_pi import RecordingPiSession

    await ws.accept()
    sender = _WebSocketSendQueue(ws)
    send_message = sender.send_json
    session = None
    action = ""
    try:
        init = await ws.receive_json()
        command = str(init.get("type") or "")
        resume_draft: dict | None = None
        resume_title = ""
        resume_result_id = None
        if command == "resume_verification":
            from dano.assets.drafts import DraftStore
            from dano.onboarding.recording_results import is_recording_result_key

            result_id = str(init.get("result_id") or "").strip()
            tenant = str(init.get("tenant") or "")
            try:
                saved = await DraftStore().get_draft(uuid.UUID(result_id)) if result_id else None
            except ValueError:
                saved = None
            if not result_id:
                detail = "继续分析缺少 result_id"
            elif saved is None:
                detail = "录制结果不存在或已删除"
            elif saved.tenant != tenant:
                detail = "录制结果不属于当前租户"
            elif not is_recording_result_key(saved.asset_key):
                detail = "该草稿不是录制结果，不能继续分析"
            elif not isinstance(saved.body.get("flow_spec"), dict):
                detail = "录制结果没有完整 FlowSpec，请重新录制"
            else:
                detail = ""
            if detail:
                await sender.send_json({"type": "error", "detail": detail})
                return
            resume_draft = saved.body["flow_spec"]
            resume_title = str(saved.body.get("title") or "")
            resume_result_id = saved.asset_draft_id
            action = str(saved.body.get("action") or "")
            subsystem = saved.subsystem.value
            # A new Pi scope avoids colliding with the original recording lock.
            recording_id = f"recording_{uuid.uuid4().hex}"
            resume_goal = saved.body.get("goal") if isinstance(saved.body.get("goal"), dict) else {}
            init["goal_text"] = str(
                init.get("goal_text") or resume_goal.get("text") or resume_goal.get("intent") or ""
            )
            init["start_url"] = ""
        elif command != "start" or not init.get("start_url"):
            await sender.send_json({
                "type": "error",
                "detail": "首帧须为 {type:'start', start_url, ...} 或 {type:'resume_verification', result_id}",
            })
            return
        else:
            tenant = str(init.get("tenant") or "")
            subsystem = _recording_subsystem(
                tenant,
                init.get("subsystem"),
                str(init["start_url"]),
            )
            requested_action = str(init.get("resume_action") or "")
            action = (
                requested_action
                if re.fullmatch(r"action_[0-9a-f]{32}", requested_action)
                else _new_recording_action()
            )
            requested_recording_id = str(init.get("pi_recording_id") or "")
            recording_id = (
                requested_recording_id
                if re.fullmatch(r"recording_[0-9a-f]{32}", requested_recording_id)
                else f"recording_{uuid.uuid4().hex}"
            )
        bind_run_context(
            recording_id=recording_id,
            action=action,
            tenant=tenant,
            subsystem=subsystem,
        )
        holder: dict[str, object] = {}

        async def pi_factory(fresh: bool):  # noqa: ANN202
            return await _start_recording_pi_candidate(lambda: RecordingPiSession(
                tenant=tenant,
                subsystem=subsystem,
                recording_id=recording_id,
                resume_history=not fresh,
            ))

        async def publisher(release_spec, candidate, context):  # noqa: ANN001, ANN202
            context.ensure_active()
            current = holder["session"]
            capture = current.capture
            storage_state = await capture.storage_state() if capture is not None else None
            workflow = current.workflow
            return await _publish_canonical_recording(
                tenant=tenant,
                subsystem=subsystem,
                action=action,
                title=(
                    (workflow.snapshot.title if workflow is not None else "")
                    or str(getattr(release_spec, "title", "") or "")
                ),
                goal=(
                    dict((release_spec.goal or {}))
                    if release_spec is not None else {}
                ),
                deploy=init.get("deploy"),
                storage_state=storage_state,
                run_id=str(getattr(current._pi, "run_id", "")),
                release_flow_spec=release_spec,
                release_candidate=candidate,
                context=context,
            )

        global _recording_session_registry
        if _recording_session_registry is None:
            _recording_session_registry = RecordingSessionRegistry()
        session_config = RecordingSessionConfig(
            tenant=tenant,
            subsystem=subsystem,
            recording_id=recording_id,
            action=action,
            start_url=str(init.get("start_url") or ""),
            goal_text=str(init.get("goal_text") or ""),
            base_url=str(init.get("base_url") or ""),
            token=str(init.get("token") or ""),
            storage_state=init.get("storage_state") or None,
            analysis_mode=init.get("machine_verification") is True,
        )
        if resume_draft is not None:
            session = await _recording_session_registry.attach_or_resume(
                config=session_config,
                send=send_message,
                pi_factory=pi_factory,
                publisher=publisher,
                draft=resume_draft,
                title=resume_title,
                result_id=resume_result_id,
                restart=init.get("restart") is True,
            )
        else:
            session, _created = await _recording_session_registry.attach_or_create(
                config=session_config,
                send=send_message,
                pi_factory=pi_factory,
                publisher=publisher,
            )
        holder["session"] = session
        init_title = str(init.get("title") or "").strip()
        if init_title and session.workflow is not None:
            await session.workflow.set_title(init_title)
        pi_run_id = str(getattr(getattr(session, "_pi", None), "run_id", "") or "")
        if pi_run_id:
            bind_run_context(run_id=pi_run_id)
        while True:
            try:
                message = await ws.receive_json()
            except RuntimeError as exc:
                if str(exc) != "WebSocket is not connected. Need to call \"accept\" first.":
                    raise
                raise WebSocketDisconnect(code=1006) from exc
            try:
                await session.dispatch(message)
            except ValueError as exc:
                await sender.send_json({"type": "error", "detail": str(exc)})
    except WebSocketDisconnect:
        log.info("recording.websocket_disconnected", action=(session.config.action if session else ""))
    except Exception as exc:  # noqa: BLE001
        log.exception("recording.websocket_failed", error=str(exc))
        try:
            await sender.send_json({"type": "error", "detail": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        if session is not None and _recording_session_registry is not None:
            _recording_session_registry.detach(action, send_message)
        await sender.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


def _emit_run_summary(
    *,
    run_id: str,
    asset_status: str,
    package_status: str,
    started: float,
    failed_stage: str = "",
    root_cause: str = "",
    skill_id: str = "",
) -> None:
    notes = current_run_notes()
    duration_ms = int((time.monotonic() - started) * 1000)
    result = (
        "succeeded"
        if asset_status == "published" and package_status == "succeeded"
        else "partial_failure"
        if asset_status == "published"
        else "failed"
        if asset_status == "failed"
        else "cancelled"
    )
    day = datetime.now().strftime("%Y-%m-%d")
    emit_run_event(
        "recording.run.summary",
        stage="end",
        status="succeeded" if result == "succeeded" else "failed",
        level="info" if result == "succeeded" else "error",
        summary=(
            "本次运行成功"
            if result == "succeeded"
            else "本次运行部分失败"
            if result == "partial_failure"
            else "本次运行失败"
        ),
        skill_id=skill_id or notes.get("skill_id"),
        duration_ms=duration_ms,
        details={
            "request_count": notes.get("request_count"),
            "page_event_count": notes.get("page_event_count"),
            "field_evidence_count": notes.get("field_evidence_count"),
            "bound_count": (notes.get("field_binding_stats") or {}).get("bound") if isinstance(notes.get("field_binding_stats"), dict) else notes.get("bound_count"),
            "ambiguous_count": (notes.get("field_binding_stats") or {}).get("ambiguous") if isinstance(notes.get("field_binding_stats"), dict) else notes.get("ambiguous_count"),
            "unbound_count": (notes.get("field_binding_stats") or {}).get("unbound") if isinstance(notes.get("field_binding_stats"), dict) else notes.get("unbound_count"),
            "capability_count": notes.get("capability_count"),
            "capability_names": notes.get("capability_names"),
            "unresolved_count": notes.get("unresolved_count"),
            "asset_status": asset_status,
            "skill_package_status": package_status,
            "exported_count": notes.get("exported_count", 0),
            "export_directory": notes.get("export_directory"),
            "failed_stage": failed_stage or notes.get("failed_stage"),
            "root_cause": root_cause or notes.get("root_cause"),
            "total_duration": _human_duration(duration_ms),
            "full_log": str(log_root() / "runs" / day / f"{run_id}.jsonl"),
            "result": result,
        },
    )


def _human_duration(duration_ms: int) -> str:
    if duration_ms >= 60_000:
        minutes, rest = divmod(duration_ms, 60_000)
        seconds = rest / 1000
        return f"{minutes}m{seconds:.0f}s"
    if duration_ms >= 1000:
        return f"{duration_ms / 1000:.1f}s"
    return f"{duration_ms}ms"


async def _auto_export(
    tenant: str,
    *,
    mode: Literal["proxy", "package", "both"] = "package",
    skill_ids: set[str] | None = None,
    strict: bool = False,
) -> None:
    """接入后自动导出该租户已上架 skill(无需手动点)。

    目录固定为仓库根 export/（可用 DANO_EXPORT_DIR 覆盖），不再套 agent-skills。
    普通接入保持 best-effort；录制原子发布传 strict=True，导出失败不得伪装成 published。
    """
    try:
        from dano.export.agent_skills import write_exports
        out = _current_export_dir()
        written = await write_exports(
            tenant,
            out,
            mode=mode,
            exclude_skill_ids=await _frozen_skill_ids(),
            skill_ids=skill_ids,
        )
        missing: list[str] = []
        if skill_ids:
            from dano.export.skill_package.renderer import package_slug

            written_set = set(written)
            missing = [
                skill_id
                for skill_id in skill_ids
                if package_slug(skill_id) not in written_set and skill_id not in written_set
            ]
        failed = bool(skill_ids) and (not written or missing)
        note_run_fact(
            exported_count=len(written),
            export_directory=out,
            skill_package_status="failed" if failed else "succeeded",
            root_cause="REQUESTED_SKILL_PACKAGE_NOT_WRITTEN" if failed and not current_run_notes().get("root_cause") else current_run_notes().get("root_cause"),
        )
        if failed:
            emit_run_event(
                "recording.export.completed",
                stage="export",
                status="failed",
                level="error",
                summary="指定 Skill 包未写出",
                details={
                    "exported_count": len(written),
                    "output_directory": out,
                    "missing_skill_ids": missing,
                    "requested_skill_ids": sorted(skill_ids),
                },
                error={
                    "code": current_run_notes().get("root_cause") or "REQUESTED_SKILL_PACKAGE_NOT_WRITTEN",
                    "type": "ExportError",
                    "message": "requested skill package was not written",
                    "cause": "指定 skill_ids 但 written 中没有对应 Skill",
                    "traceback": None,
                    "artifact_refs": [f"skill_id:{item}" for item in missing],
                },
            )
        log.info(
            "onboard.auto_export",
            tenant=tenant,
            out=out,
            mode=mode,
            count=len(written),
            skill_ids=sorted(skill_ids) if skill_ids is not None else None,
            status="failed" if failed else "succeeded",
        )
    except Exception as e:  # noqa: BLE001
        note_run_fact(
            skill_package_status="failed",
            exported_count=0,
            root_cause=type(e).__name__,
            failed_stage="export",
        )
        log.warning("onboard.auto_export_failed", error=str(e))
        if strict:
            raise


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
            subsystem = _effective_subsystem(req.tenant, req.subsystem)
            rep = await onboard(
                tenant=req.tenant, subsystem=subsystem, openapi=req.openapi,
                deploy=req.deploy, credentials=req.credentials,
                include_tags=req.include_tags, business_rules=req.business_rules, holidays=req.holidays,
                flows=req.flows, progress=_progress, lifecycle=_lifecycle,
                lifecycle_reconciler=_lifecycle_reconciler)
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
    return str(Path(__file__).resolve().parents[3] / "export")


def _current_export_dir() -> str:
    from dano.execution.page.sessions import get_export_dir

    raw = get_export_dir(_default_export_dir())
    p = Path(raw)
    if p.name.lower() == "agent-skills":
        return str(p.parent)
    return raw


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
@app.get("/v1/recording-results")
async def list_recording_results(
    subsystem: str = "",
    x_tenant_key: str | None = Header(default=None),
) -> list[dict]:
    tenant = await _auth_tenant(x_tenant_key)
    from dano.assets.drafts import DraftStore
    from dano.onboarding.recording_results import recording_result_summary

    store = DraftStore()
    if subsystem:
        drafts = await store.list_recording_results(tenant=tenant, subsystem=subsystem)
    else:
        drafts = []
        for item in await _tenant_subsystems(tenant):
            drafts.extend(await store.list_recording_results(tenant=tenant, subsystem=item.value))
        drafts.sort(
            key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
    return [recording_result_summary(item) for item in drafts]


@app.get("/v1/recording-results/{result_id}")
async def get_recording_result(
    result_id: str,
    x_tenant_key: str | None = Header(default=None),
) -> dict:
    tenant = await _auth_tenant(x_tenant_key)
    from dano.assets.drafts import DraftStore
    from dano.onboarding.recording_results import is_recording_result_key, recording_result_detail

    try:
        parsed = uuid.UUID(result_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="无效的录制结果 ID") from exc
    saved = await DraftStore().get_draft(parsed)
    if (
        saved is None
        or saved.tenant != tenant
        or not is_recording_result_key(saved.asset_key)
    ):
        raise HTTPException(status_code=404, detail="录制结果不存在")
    return recording_result_detail(saved)


@app.delete("/v1/recording-results/{result_id}")
async def delete_recording_result(
    result_id: str,
    x_tenant_key: str | None = Header(default=None),
) -> dict:
    tenant = await _auth_tenant(x_tenant_key)
    from dano.assets.drafts import DraftStore

    try:
        parsed = uuid.UUID(result_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="无效的录制结果 ID") from exc
    deleted = await DraftStore().delete_recording_result(parsed, tenant=tenant)
    if not deleted:
        raise HTTPException(status_code=404, detail="录制结果不存在")
    return {"deleted": True, "id": result_id}


@app.get("/v1/skills")
async def list_skills(x_tenant_key: str | None = Header(default=None)) -> list[dict]:
    tenant = await _auth_tenant(x_tenant_key)
    return await _manifests_for_tenant(tenant)


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
    model_config = ConfigDict(extra="forbid")

    input: dict = Field(default_factory=dict)
    confirm: bool = False
    dry_run: bool = False
    protocol: Literal["dano.capability_call.v1"] = "dano.capability_call.v1"


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


def _skill_call_input(input_: dict, *, capability: str | None = None, dry_run: bool = False) -> dict:
    args = dict(input_)
    if capability:
        args["__capability"] = capability
    if dry_run:
        args["__dry_run"] = True
    return args


@app.post("/v1/skills/{skill_id}/invoke")
async def invoke_skill(skill_id: str, req: InvokeReq,
                       x_tenant_key: str | None = Header(default=None)) -> dict:
    tenant = await _auth_tenant(x_tenant_key)
    args = _skill_call_input(req.input, dry_run=req.dry_run)
    return await _invoke(tenant, skill_id, args, req.confirm)


@app.post("/v1/skills/{skill_id}/capabilities/{capability}/invoke")
async def invoke_skill_capability(skill_id: str, capability: str, req: CapabilityInvokePayload,
                                  x_tenant_key: str | None = Header(default=None)) -> dict:
    """按 Skill 内的指定 capability 调用。"""
    tenant = await _auth_tenant(x_tenant_key)
    args = _skill_call_input(req.input, capability=capability, dry_run=req.dry_run)
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
    model_config = ConfigDict(extra="forbid")

    name: str                       # 工具名(= skill_id 的点转 __)
    capability: str | None = None   # 一个 Skill 内的业务能力键(query_status/submit_batch...)
    input: dict = Field(default_factory=dict)
    confirm: bool = False
    dry_run: bool = False


@app.post("/v1/tools/call")
async def call_tool(req: ToolCallReq, x_tenant_key: str | None = Header(default=None)) -> dict:
    """执行一次 LLM 工具调用:name→skill_id，走与 /invoke 同一受控链路。"""
    tenant = await _auth_tenant(x_tenant_key)
    args = _skill_call_input(req.input, capability=req.capability, dry_run=req.dry_run)
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
    out_dir: str = ""               # 空则写入仓库 export/;不要再套 agent-skills 等额外目录
    mode: Literal["proxy", "package", "both"] = "package"


@app.post("/export/agent-skills")
async def export_agent_skills_ep(req: ExportSkillsReq,
                                 x_tenant_key: str | None = Header(default=None)) -> dict:
    """把本租户已上架 Skill 导出为文件式 skill，写入 out_dir。

    后端与目标目录同机时直接写文件。真执行仍在 Dano 侧；导出的脚本调用能力级 invoke 端点。
    """
    tenant = await _auth_tenant(x_tenant_key)
    from dano.execution.page.sessions import save_export_dir
    from dano.export.agent_skills import write_exports
    from dano.export.skill_package.renderer import package_slug
    out = str(req.out_dir or "").strip() or _current_export_dir()
    frozen = await _frozen_skill_ids()
    frozen_manifests = [m for m in await _manifests_for_tenant(tenant) if m["name"] in frozen]
    try:
        removed = []
        for m in frozen_manifests:
            removed.extend(_cleanup_export_folders(
                out,
                [*_export_slugs_for_manifest(m), package_slug(m["name"])],
            ))
        written = await write_exports(
            tenant,
            out,
            mode=req.mode,
            exclude_skill_ids=frozen,
        )
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"写入目录失败:{e}") from e
    save_export_dir(out)                                 # 记住此目录 → 录完自动发布落同一处
    return {
        "out_dir": out,
        "mode": req.mode,
        "count": len(written),
        "written": written,
        "removed_frozen_folders": removed,
    }


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


@app.post("/lifecycle/reconcile")
async def reconcile_lifecycle_registrations() -> dict:
    """Retry lifecycle indexing for assets that were already published."""
    return await _lifecycle_reconciler.reconcile()


@app.post("/assurance/report-failure")
async def report_failure_route(event: dict) -> dict:
    from dano.assurance.service import FailureEvent, report_failure
    d = await report_failure(FailureEvent.model_validate(event), lifecycle=_lifecycle, breaker=_breaker)
    return d.model_dump()


class SelfHealReq(BaseModel):
    tenant: str
    subsystem: str = ""
    openapi: dict
    deploy: dict
    credentials: dict[str, str] = {}
    actions: list[str] | None = None      # 指定受影响动作;省略=自动取当前暂停的 Skill
    incremental: bool = True              # 默认增量;置 false 回退全量重跑


@app.post("/assurance/self-heal")
async def self_heal_route(req: SelfHealReq) -> dict:
    from dano.assurance.service import self_heal
    subsystem = _effective_subsystem(req.tenant, req.subsystem)
    out = await self_heal(tenant=req.tenant, subsystem=subsystem, openapi=req.openapi,
                          deploy=req.deploy, credentials=req.credentials, lifecycle=_lifecycle,
                          actions=req.actions, incremental=req.incremental)
    for sid in out.get("recovered", []):       # 自愈成功后清零失败计数
        await _breaker.reset_prefix(f"fail:{sid}")
    return out
