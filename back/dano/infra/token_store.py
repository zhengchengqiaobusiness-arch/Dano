"""页面 token 凭证(落 Postgres,表 runtime_token):录制时抓到的鉴权头(Authorization / Tenant-Id /
satoken…)单独存一份,运行期 invoke 时覆盖焊进资产里的旧 token → token 过期只需前端 PUT 换一份,免重录。

与 sessions.py(storage_state 整登录态,落文件)互补、解耦:
- storage_state:浏览器整登录态(cookie+localStorage),DOM 回放路径用;过期只能重录。
- token_store:**单独可查、可更新**的一组鉴权头,抓请求路径(api_request)运行期用;落库 → 重启不丢,过期换一下即可。

DB 不可用(连接池未初始化,如离线/直调)时:save 跳过、get 返回空(不抛),不阻断主流程。
"""

from __future__ import annotations

import json

import structlog

log = structlog.get_logger(__name__)

# 头部 key 像"机密"的(查询接口默认打码)。Tenant-Id / clientId 等非机密照常可见。
_SECRET_HINTS = ("authorization", "token", "satoken", "cookie", "secret", "password", "session", "ticket", "credential")


# ───────────────────────── 纯函数(无 DB,可离线测试) ─────────────────────────
def _is_secret_key(k: str) -> bool:
    kl = (k or "").lower()
    return any(h in kl for h in _SECRET_HINTS)


def _mask(v: str) -> str:
    """打码:保留鉴权方案前缀(Bearer/Basic)与首尾各 4 位,中间星号 —— 既能辨认是哪条 token,又不泄全值。"""
    s = str(v or "")
    if not s:
        return s
    scheme, val = "", s
    parts = s.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() in ("bearer", "basic", "token"):
        scheme, val = parts[0] + " ", parts[1]
    if len(val) <= 8:
        return scheme + ("*" * len(val))
    return scheme + val[:4] + "*" * (len(val) - 8) + val[-4:]


def mask_headers(headers: dict | None) -> dict:
    """把机密头的值打码(供查询接口默认返回);非机密头(Tenant-Id 等)原样。"""
    return {k: (_mask(v) if _is_secret_key(k) else v) for k, v in (headers or {}).items()}


def headers_from_api_request(api_request: dict | None) -> dict:
    """从已建好的 api_request 取该录制抓到的鉴权头(单请求取顶层,多步取最后一步=提交那步)。"""
    if not api_request:
        return {}
    h = api_request.get("auth_headers")
    if not h and api_request.get("steps"):
        h = (api_request["steps"][-1] or {}).get("auth_headers")
    return dict(h or {})


def merge_auth_headers(api_request: dict, override: dict | None) -> dict:
    """把新存的鉴权头覆盖进 api_request 的 auth_headers(顶层 + 多步工作流**每一步**,因每个请求都需带 token)。
    返回新 dict,不改原对象;override 为空则原样返回拷贝。"""
    if not override:
        return dict(api_request)
    out = {**api_request, "auth_headers": {**(api_request.get("auth_headers") or {}), **override}}
    steps = api_request.get("steps")
    if steps:
        out["steps"] = [{**(s or {}), "auth_headers": {**((s or {}).get("auth_headers") or {}), **override}}
                        for s in steps]
    return out


# ───────────────────────── 存储(Postgres,表 runtime_token) ─────────────────────────
def _pool_or_none():  # noqa: ANN202 —— asyncpg.Pool | None
    """拿连接池;未初始化(DB 不可用/离线直调)返回 None,调用方据此降级不抛。"""
    try:
        from dano.infra.db import get_pool
        return get_pool()
    except Exception:  # noqa: BLE001
        return None


def _row_to_rec(row) -> dict:  # noqa: ANN001
    h = row["headers"]
    if isinstance(h, str):                       # asyncpg 默认把 JSONB 返成字符串
        try:
            h = json.loads(h)
        except Exception:  # noqa: BLE001
            h = {}
    upd = row["updated_at"]
    return {"tenant": row["tenant"], "subsystem": row["subsystem"], "headers": dict(h or {}),
            "source": row["source"], "updated_at": upd.isoformat() if hasattr(upd, "isoformat") else upd}


async def save_token(tenant: str, subsystem: str, headers: dict | None, *, source: str = "recording") -> dict | None:
    """存/更一组运行期鉴权头(空值丢弃)。source: recording(录制自动抓)/ manual(前端手动刷新)。
    无有效头或 DB 不可用 → 返回 None(不抛)。"""
    headers = {k: v for k, v in (headers or {}).items() if v}
    if not headers:
        return None
    pool = _pool_or_none()
    if pool is None:
        log.warning("token_store.no_pool_skip_save", tenant=tenant, subsystem=subsystem)
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO runtime_token (tenant, subsystem, headers, source, updated_at)
                VALUES ($1, $2, $3::jsonb, $4, now())
                ON CONFLICT (tenant, subsystem) DO UPDATE SET
                    headers = EXCLUDED.headers, source = EXCLUDED.source, updated_at = now()
                RETURNING tenant, subsystem, headers, source, updated_at
                """,
                tenant, subsystem, json.dumps(headers, ensure_ascii=False), source)
        log.info("token_store.saved", tenant=tenant, subsystem=subsystem, source=source, keys=sorted(headers))
        return _row_to_rec(row)
    except Exception as e:  # noqa: BLE001
        log.warning("token_store.save_failed", tenant=tenant, subsystem=subsystem, error=str(e))
        return None


async def get_token(tenant: str, subsystem: str) -> dict | None:
    """取该 (tenant, subsystem) 的 token 记录(含 headers/source/updated_at);没有/DB 不可用返回 None。"""
    pool = _pool_or_none()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT tenant, subsystem, headers, source, updated_at FROM runtime_token "
                "WHERE tenant = $1 AND subsystem = $2", tenant, subsystem)
        return _row_to_rec(row) if row else None
    except Exception as e:  # noqa: BLE001
        log.warning("token_store.read_failed", tenant=tenant, subsystem=subsystem, error=str(e))
        return None


async def get_token_headers(tenant: str, subsystem: str) -> dict:
    """运行期取该 (tenant, subsystem) 的鉴权头;没有返回 {}。"""
    rec = await get_token(tenant, subsystem)
    return dict(rec.get("headers") or {}) if rec else {}
