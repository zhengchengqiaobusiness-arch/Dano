"""动作执行器:把连接器规格 + 入参 + 凭证变成真实调用。

幂等:写动作带 idempotency_key(任务ID + 动作签名),由调用方传入,重试不重复创建。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from pydantic import BaseModel

from dano.shared.asset_bodies import AuthConfig
from dano.shared.enums import Subsystem

if TYPE_CHECKING:
    import httpx

    from dano.execution.connectors.auth import AuthManager

log = structlog.get_logger(__name__)


def system_key_for(subsystem: Subsystem) -> str:
    """系统 key(与连接器 auth_ref 的 vault path 段一致)。A-OA → 'oa'。"""
    return subsystem.value.split("-")[-1].lower()


class ActionResponse(BaseModel):
    http: int
    body: dict[str, Any]


class ActionExecutor(Protocol):
    async def execute(
        self,
        connector: dict[str, Any],
        inputs: dict[str, Any],
        credentials: dict[str, str],
        *,
        idempotency_key: str | None = None,
    ) -> ActionResponse: ...


class SystemEndpoint(BaseModel):
    """一个子系统的运行时接入信息(来自环境画像资产)。"""

    base_url: str
    auth: AuthConfig


ClientFactory = Callable[[], "httpx.AsyncClient"]


class RealActionExecutor:
    """真实 HTTP 执行器:打真实企业系统 API。

    - base_url + 鉴权配置来自环境画像(endpoints,按系统 key 索引)。
    - 凭证(credentials)由调用方经 Vault 取得后传入,鉴权握手交 AuthManager(缓存/刷新)。
    - 系统 key 从连接器 auth_ref(vault://tenant/<key>)解析,自洽路由到对应 endpoint。
    client_factory 可注入(测试用 httpx.MockTransport),默认真实 AsyncClient。
    """

    def __init__(
        self,
        *,
        endpoints: dict[str, SystemEndpoint],
        auth_manager: "AuthManager | None" = None,
        client_factory: ClientFactory | None = None,
        timeout: float = 30.0,
    ) -> None:
        from dano.execution.connectors.auth import AuthManager

        self._endpoints = endpoints
        self._auth = auth_manager or AuthManager()
        self._timeout = timeout
        self._client_factory = client_factory

    def _client(self) -> "httpx.AsyncClient":
        import httpx

        if self._client_factory is not None:
            return self._client_factory()
        from dano.infra.http import tls_verify
        return httpx.AsyncClient(timeout=self._timeout, verify=tls_verify())

    def _system_key(self, connector: dict[str, Any]) -> str:
        from dano.infra.vault import parse_ref

        _, name = parse_ref(connector["auth_ref"])
        return name

    async def execute(
        self,
        connector: dict[str, Any],
        inputs: dict[str, Any],
        credentials: dict[str, str],
        *,
        idempotency_key: str | None = None,
    ) -> ActionResponse:
        key = self._system_key(connector)
        if key not in self._endpoints:
            raise RuntimeError(f"未配置系统接入信息(环境画像缺失): {key}")
        ep = self._endpoints[key]
        method = connector.get("method", "POST").upper()
        url = ep.base_url + connector["endpoint"]

        async with self._client() as client:
            headers = await self._auth.get_headers(
                key, base_url=ep.base_url, config=ep.auth, credentials=credentials, client=client
            )
            headers = dict(headers)
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            if method == "GET":
                resp = await client.get(url, params=inputs, headers=headers)
            else:
                resp = await client.request(method, url, json=inputs, headers=headers)

        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = {"raw": resp.text}
        log.info("action.real", action=connector.get("action"), system=key, status=resp.status_code)
        return ActionResponse(http=resp.status_code, body=body)
