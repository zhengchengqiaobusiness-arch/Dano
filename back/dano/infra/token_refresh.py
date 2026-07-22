"""从可配置登录接口刷新运行期 Token。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from dano.config import get_settings
from dano.infra.credentials import resolve_credentials
from dano.infra.http import tls_verify
from dano.infra.token_store import _pool_or_none, get_token, update_token_headers

log = structlog.get_logger(__name__)
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_.-]+)}}")


def _sources_for(tenant: str, subsystem: str) -> list[dict]:
    configured = (get_settings().token_refresh_sources or {}).get(f"{tenant}/{subsystem}") or []
    if isinstance(configured, dict):
        configured = [configured]
    return [dict(item) for item in configured if isinstance(item, dict) and item.get("enabled", True)]


def _render(value: Any, credentials: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _render(item, credentials) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, credentials) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in credentials:
            raise ValueError(f"凭据缺少字段:{key}")
        return str(credentials[key])

    return _PLACEHOLDER.sub(replace, value)


def _json_path(value: Any, path: str) -> Any:
    current = value
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
            continue
        return None
    return current


def _is_due(record: dict | None, interval_seconds: int, now: datetime) -> bool:
    if not record or not record.get("updated_at"):
        return True
    try:
        updated = datetime.fromisoformat(str(record["updated_at"]).replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return (now - updated.astimezone(timezone.utc)).total_seconds() >= interval_seconds
    except (TypeError, ValueError):
        return True


def _safe_error(exc: Exception) -> str:
    """网络异常可能包含带凭据的 URL；日志和接口只保留安全分类。"""
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return "登录接口超时"
    if isinstance(exc, httpx.RequestError):
        return f"登录接口网络错误:{type(exc).__name__}"
    return type(exc).__name__


async def _request_source(
    source: dict,
    tenant: str,
    subsystem: str,
    client: httpx.AsyncClient,
) -> dict[str, str]:
    source_type = str(source.get("type") or "password_http")
    if source_type not in {"password_http", "http"}:
        raise ValueError(f"不支持的 token 来源:{source_type}")

    credential_ref = str(source.get("credentials_ref") or f"vault://{tenant}/{subsystem}-login")
    credentials = resolve_credentials({"token_refresh": credential_ref})
    url = _render(source.get("url") or "", credentials)
    if not url:
        raise ValueError("token 来源缺少 url")
    if urlparse(url).scheme != "https" and not source.get("allow_insecure_http"):
        raise ValueError("登录接口必须使用 HTTPS；可信内网 HTTP 需显式配置 allow_insecure_http=true")

    method = str(source.get("method") or "POST").upper()
    headers = _render(source.get("headers") or {}, credentials)
    query = _render(source.get("query") or {}, credentials)
    body = _render(source.get("body") or {}, credentials)
    request_kwargs: dict[str, Any] = {"headers": headers, "params": query}
    if str(source.get("encoding") or "json").lower() == "form":
        request_kwargs["data"] = body
    else:
        request_kwargs["json"] = body

    response = await client.request(method, url, **request_kwargs)
    if response.status_code >= 400:
        raise ValueError(f"登录接口 HTTP {response.status_code}")

    cookie_header = "; ".join(f"{name}={value}" for name, value in response.cookies.items())
    token_header = str(source.get("token_header") or "")
    if token_header:
        token = response.headers.get(token_header)
    elif source.get("token_path") or not cookie_header:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("登录接口未返回 JSON") from exc
        token = _json_path(payload, str(source.get("token_path") or "data.accessToken"))
    else:
        token = None
    token_valid = not isinstance(token, bool) and isinstance(token, (str, int, float)) and bool(str(token).strip())
    if not token_valid and not cookie_header:
        raise ValueError(f"登录响应缺少 token:{source.get('token_path') or token_header}")

    fresh: dict[str, str] = {}
    header_name = str(source.get("header_name") or "Authorization")
    token_prefix = str(source.get("token_prefix") if "token_prefix" in source else "Bearer ")
    if token_valid:
        fresh[header_name] = f"{token_prefix}{str(token).strip()}"
    if cookie_header:
        fresh["Cookie"] = cookie_header
    return fresh


async def _verify_source(
    source: dict,
    fresh_headers: dict[str, str],
    existing_headers: dict[str, str],
    tenant: str,
    subsystem: str,
    client: httpx.AsyncClient,
) -> None:
    """用新 Token 调受保护接口；验证通过前绝不覆盖当前可用 Token。"""
    if source.get("allow_unverified_token"):
        return
    credential_ref = str(source.get("credentials_ref") or f"vault://{tenant}/{subsystem}-login")
    credentials = resolve_credentials({"token_refresh": credential_ref})
    url = _render(source.get("verify_url") or "", credentials)
    if not url:
        raise ValueError("token 来源缺少 verify_url；如确实无法验证需显式配置 allow_unverified_token=true")
    if urlparse(url).scheme != "https" and not source.get("allow_insecure_http"):
        raise ValueError("token 验证接口必须使用 HTTPS；可信内网 HTTP 需显式允许")
    headers = {
        **existing_headers,
        **_render(source.get("verify_headers") or {}, credentials),
        **fresh_headers,
    }
    response = await client.request(
        str(source.get("verify_method") or "GET").upper(),
        url,
        headers=headers,
        params=_render(source.get("verify_query") or {}, credentials),
    )
    if response.status_code >= 400:
        raise ValueError(f"新 token 验证失败:HTTP {response.status_code}")
    success_path = str(source.get("verify_success_path") or "")
    if success_path:
        try:
            actual = _json_path(response.json(), success_path)
        except ValueError as exc:
            raise ValueError("token 验证接口未返回 JSON") from exc
        accepted = source.get("verify_success_values", [0, 200, True])
        if actual not in accepted:
            raise ValueError(f"新 token 验证失败:{success_path}")


@asynccontextmanager
async def _refresh_lock(tenant: str, subsystem: str):
    """PostgreSQL 会话锁保证多进程只刷新一次；无连接池时让保存路径自行失败。"""
    pool = _pool_or_none()
    if pool is None:
        yield True, None
        return
    key = f"runtime-token:{tenant}/{subsystem}"
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT pg_advisory_lock(hashtext($1))", key)
        try:
            yield True, conn
        finally:
            await conn.fetchval("SELECT pg_advisory_unlock(hashtext($1))", key)


async def refresh_one(
    tenant: str,
    subsystem: str,
    *,
    force: bool = False,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> dict:
    """按顺序尝试该系统配置的登录来源；成功才覆盖旧 Token。"""
    sources = _sources_for(tenant, subsystem)
    if not sources:
        return {"ok": False, "tenant": tenant, "subsystem": subsystem, "reason": "not_configured"}

    async with _refresh_lock(tenant, subsystem) as lock:
        locked, conn = lock
        connection = {"_conn": conn} if conn is not None else {}
        record = await get_token(tenant, subsystem, **connection)
        interval = max(60, int(sources[0].get("interval_seconds") or 1800))
        if not force and not _is_due(record, interval, now or datetime.now(timezone.utc)):
            return {"ok": True, "tenant": tenant, "subsystem": subsystem, "status": "not_due"}

        own_client = client is None
        if client is None:
            timeout = max(1.0, float(sources[0].get("timeout_seconds") or 20))
            client = httpx.AsyncClient(timeout=timeout, verify=tls_verify(), trust_env=False)
        errors: list[str] = []
        try:
            for index, source in enumerate(sources):
                try:
                    fresh = await _request_source(source, tenant, subsystem, client)
                    await _verify_source(
                        source,
                        fresh,
                        dict((record or {}).get("headers") or {}),
                        tenant,
                        subsystem,
                        client,
                    )
                    rec = await update_token_headers(
                        tenant,
                        subsystem,
                        fresh,
                        source=f"scheduled:{source.get('type') or 'password_http'}",
                        **connection,
                    )
                    if not rec:
                        raise ValueError("token 保存失败")
                    log.info("token_refresh.succeeded", tenant=tenant, subsystem=subsystem, source_index=index)
                    return {
                        "ok": True,
                        "tenant": tenant,
                        "subsystem": subsystem,
                        "status": "refreshed",
                        "updated_at": rec.get("updated_at"),
                    }
                except Exception as exc:  # noqa: BLE001 - 尝试下一可配置来源
                    error = _safe_error(exc)
                    errors.append(error)
                    log.warning(
                        "token_refresh.source_failed",
                        tenant=tenant,
                        subsystem=subsystem,
                        source_index=index,
                        error=error,
                    )
        finally:
            if own_client:
                await client.aclose()

    return {
        "ok": False,
        "tenant": tenant,
        "subsystem": subsystem,
        "reason": "all_sources_failed",
        "errors": errors,
    }


async def refresh_due(*, force: bool = False) -> dict:
    """刷新配置中的全部系统；单个失败不阻断其他系统。"""
    async def run(key: str) -> dict:
        tenant, separator, subsystem = key.partition("/")
        if not separator or not tenant or not subsystem:
            return {"ok": False, "source": key, "reason": "invalid_source_key"}
        return await refresh_one(tenant, subsystem, force=force)

    keys = sorted((get_settings().token_refresh_sources or {}).keys())
    results = list(await asyncio.gather(*(run(key) for key in keys)))
    return {
        "ok": bool(results) and all(item.get("ok") for item in results),
        "total": len(results),
        "refreshed": sum(item.get("status") == "refreshed" for item in results),
        "failed": sum(not item.get("ok") for item in results),
        "results": results,
    }


def is_auth_failure(result: dict | None) -> bool:
    """只识别明确鉴权失败信号，避免把普通业务失败误当成 Token 失效。"""
    if not isinstance(result, dict):
        return False
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else result
    step = raw.get("step_result") if isinstance(raw.get("step_result"), dict) else {}
    final = raw.get("final") if isinstance(raw.get("final"), dict) else {}
    for item in (result, raw, step, final):
        if item.get("status") == 401 or item.get("status_code") == 401:
            return True
        response = item.get("response")
        if isinstance(response, dict) and response.get("code") == 401:
            return True
    detail = " ".join(str(item.get("detail") or "") for item in (result, raw, step, final)).lower()
    return any(marker in detail for marker in ("unauthorized", "token expired", "登录失效", "未登录"))
