"""沙箱执行器:Agent 用测试账号**亲自验证自己刚生成的东西能不能跑通**(自验证硬关卡)。

红线:一切实测只在沙箱 / 测试账号,绝不在生产环境创建真实请假、提交真实报销。

真实实现走 vault:// 取测试凭证、对测试环境真跑。
"""

from __future__ import annotations

from typing import Any

from dano.capabilities.types import VerifyResult


class RealSandbox:
    """真实沙箱:用**测试账号**对**测试环境**跑,验证生成的资产能否跑通(自验证硬关卡)。

    红线:只在测试环境/测试账号,绝不碰生产写动作。复用执行层的真实 HTTP + 鉴权。
    - connection_test / health_check:真实鉴权握手探测。
    - run_action:真实调用一个动作(测试账号)。
    - write_read_back:字段映射写回比对——**系统特定**,通过注入 probe 回调实现;
      未注入则返回不通过并说明(诚实暴露,不蒙混入库)。
    """

    def __init__(
        self,
        *,
        system_key: str,
        endpoint: Any,                       # SystemEndpoint
        test_credentials: dict[str, str],
        auth_manager: Any | None = None,     # AuthManager
        client_factory: Any | None = None,
        write_read_probe: Any | None = None,
    ) -> None:
        from dano.execution.connectors.auth import AuthManager
        from dano.execution.connectors.executor import RealActionExecutor

        self._key = system_key
        self._endpoint = endpoint
        self._creds = test_credentials
        self._auth = auth_manager or AuthManager()
        self._client_factory = client_factory
        self._probe = write_read_probe
        self._executor = RealActionExecutor(
            endpoints={system_key: endpoint},
            auth_manager=self._auth,
            client_factory=client_factory,
        )

    def _client(self):
        import httpx

        if self._client_factory is not None:
            return self._client_factory()
        from dano.infra.http import tls_verify
        return httpx.AsyncClient(timeout=30, verify=tls_verify())

    async def _probe_auth(self) -> VerifyResult:
        try:
            async with self._client() as client:
                await self._auth.get_headers(
                    self._key, base_url=self._endpoint.base_url, config=self._endpoint.auth,
                    credentials=self._creds, client=client,
                )
            return VerifyResult(passed=True, detail="鉴权握手通过")
        except Exception as e:  # noqa: BLE001
            return VerifyResult(passed=False, detail=f"鉴权失败: {e}")

    async def connection_test(self, connector_body: dict[str, Any]) -> VerifyResult:
        return await self._probe_auth()

    async def run_action(
        self, connector_body: dict[str, Any], inputs: dict[str, Any]
    ) -> VerifyResult:
        try:
            resp = await self._executor.execute(connector_body, inputs, self._creds)
        except Exception as e:  # noqa: BLE001
            return VerifyResult(passed=False, detail=f"沙箱动作异常: {e}")
        ok = 200 <= resp.http < 300
        return VerifyResult(
            passed=ok, detail=f"沙箱动作 HTTP {resp.http}",
            evidence={"response": resp.body, "http": resp.http},
        )

    async def write_read_back(
        self, subsystem: str, field: str, value: Any
    ) -> VerifyResult:
        if self._probe is None:
            return VerifyResult(
                passed=False,
                detail=f"字段 {field} 写回验证需注入 write_read_probe(系统特定的写/读动作)",
            )
        return await self._probe(subsystem, field, value)

    async def health_check(self, env_profile: dict[str, Any]) -> VerifyResult:
        return await self._probe_auth()
