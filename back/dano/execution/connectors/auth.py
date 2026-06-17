"""运行时鉴权握手层(库中选,不自造)。

平台预置两类适配器:Token(Bearer)与 SSO(表单登录拿 session cookie)。
企业特定的 endpoint/字段名通过环境画像里的 AuthConfig 注入,**不写死某家**。

AuthManager 负责缓存 + 到期刷新,按系统 key 隔离(一个子系统的凭证不外溢到另一个)。
凭证(credentials)由调用方经 Vault 取得,本层不接触明文存储。
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod

import httpx
import structlog
from pydantic import BaseModel

from dano.shared.asset_bodies import AuthConfig
from dano.shared.enums import AuthKind

log = structlog.get_logger(__name__)


class AuthContext(BaseModel):
    headers: dict[str, str]
    expires_at: float | None = None  # None = 不过期(如长期 session)

    def valid(self) -> bool:
        # 提前 30s 视为过期,避免边界请求带着将失效的凭证
        return self.expires_at is None or time.time() < self.expires_at - 30


class AuthProvider(ABC):
    @abstractmethod
    async def authenticate(
        self, *, base_url: str, config: AuthConfig, credentials: dict[str, str], client: httpx.AsyncClient
    ) -> AuthContext: ...


class TokenAuthProvider(AuthProvider):
    async def authenticate(self, *, base_url, config, credentials, client) -> AuthContext:
        token = credentials.get("token")
        expires_at: float | None = None
        if not token and config.token_path and credentials.get("apikey"):
            # 用 apikey 换 token
            resp = await client.post(
                base_url + config.token_path, json={"apikey": credentials["apikey"]}
            )
            resp.raise_for_status()
            token = resp.json().get(config.token_field)
            expires_at = time.time() + config.token_ttl_seconds
        if not token:
            raise RuntimeError("Token 鉴权失败:凭证既无 token 也无可换取的 apikey")
        return AuthContext(
            headers={config.token_header: f"{config.token_prefix}{token}"}, expires_at=expires_at
        )


class SsoAuthProvider(AuthProvider):
    async def authenticate(self, *, base_url, config, credentials, client) -> AuthContext:
        session = credentials.get("session")
        if not session and config.login_path:
            resp = await client.post(
                base_url + config.login_path,
                data={
                    config.username_field: credentials.get("username", ""),
                    config.password_field: credentials.get("password", ""),
                },
            )
            resp.raise_for_status()
            # 优先从 Set-Cookie 取 session;退而取响应体里的 session 字段
            session = resp.headers.get("set-cookie") or resp.json().get("session", "")
        if not session:
            raise RuntimeError("SSO 鉴权失败:凭证既无 session 也无可登录的 username/password")
        return AuthContext(headers={config.session_cookie_header: session})


class AuthManager:
    """鉴权上下文缓存 + 刷新,按系统 key 隔离。"""

    def __init__(self) -> None:
        self._providers: dict[AuthKind, AuthProvider] = {
            AuthKind.TOKEN: TokenAuthProvider(),
            AuthKind.SSO: SsoAuthProvider(),
        }
        self._cache: dict[str, AuthContext] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def get_headers(
        self,
        system_key: str,
        *,
        base_url: str,
        config: AuthConfig,
        credentials: dict[str, str],
        client: httpx.AsyncClient,
    ) -> dict[str, str]:
        ctx = self._cache.get(system_key)
        if ctx and ctx.valid():
            return ctx.headers
        async with self._lock(system_key):
            ctx = self._cache.get(system_key)  # 双检
            if ctx and ctx.valid():
                return ctx.headers
            provider = self._providers[config.kind]
            ctx = await provider.authenticate(
                base_url=base_url, config=config, credentials=credentials, client=client
            )
            self._cache[system_key] = ctx
            log.info("auth.refreshed", system=system_key, kind=config.kind.value)
            return ctx.headers

    def invalidate(self, system_key: str) -> None:
        self._cache.pop(system_key, None)
