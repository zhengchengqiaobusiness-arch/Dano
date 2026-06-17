"""动作执行器:把连接器规格 + 入参 + 凭证,变成一次真实(或沙箱)调用。

接口化(ActionExecutor Protocol):
- HttpActionExecutor:真实 HTTP 调用(httpx),按 field_bindings 映射入参、按鉴权注入凭证。
- FakeActionExecutor:原型/测试用,按 action 返回可配置响应,驱动断言与事实核查逻辑。

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


class HttpActionExecutor:
    """真实 HTTP 执行(A 公司侧 Worker)。endpoint 基址由环境画像/配置提供。"""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def _auth_headers(self, connector: dict, credentials: dict) -> dict[str, str]:
        kind = connector.get("auth_kind")
        if kind == "token":
            tok = (credentials.get("token") or "").strip()
            return {"Authorization": f"Bearer {tok}"} if tok else {}
        if kind == "sso":
            return {"Cookie": credentials.get("session", "")}
        return {}

    async def execute(
        self,
        connector: dict[str, Any],
        inputs: dict[str, Any],
        credentials: dict[str, str],
        *,
        idempotency_key: str | None = None,
    ) -> ActionResponse:
        import httpx

        url = self._base_url + connector["endpoint"]
        method = connector.get("method", "POST").upper()
        headers = self._auth_headers(connector, credentials)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        from dano.infra.http import tls_verify
        async with httpx.AsyncClient(timeout=30, verify=tls_verify()) as client:
            if method == "GET":
                resp = await client.get(url, params=inputs, headers=headers)
            else:
                resp = await client.request(method, url, json=inputs, headers=headers)
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = {"raw": resp.text}
        log.info("action.http", action=connector.get("action"), status=resp.status_code)
        return ActionResponse(http=resp.status_code, body=body)


class FakeActionExecutor:
    """原型/测试执行器。

    responses: {action: (http, body)} 预设;未配置的动作默认 200 + 通用单号。
    failures: 标记为失败(返回 5xx)的动作集合。
    记录调用历史,供事实核查测试断言。
    """

    def __init__(
        self,
        responses: dict[str, tuple[int, dict]] | None = None,
        failures: set[str] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._failures = failures or set()
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        connector: dict[str, Any],
        inputs: dict[str, Any],
        credentials: dict[str, str],
        *,
        idempotency_key: str | None = None,
    ) -> ActionResponse:
        action = connector.get("action", "")
        self.calls.append({"action": action, "inputs": dict(inputs), "key": idempotency_key})
        if action in self._failures:
            return ActionResponse(http=500, body={"error": f"{action} failed"})
        if action in self._responses:
            http, body = self._responses[action]
            return ActionResponse(http=http, body=body)
        return ActionResponse(http=200, body={"request_id": "RT-0001", "status": "已提交"})


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
