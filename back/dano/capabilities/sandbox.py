"""沙箱执行器:Agent 用测试账号**亲自验证自己刚生成的东西能不能跑通**(自验证硬关卡)。

红线:一切实测只在沙箱 / 测试账号,绝不在生产环境创建真实请假、提交真实报销。

接口化(SandboxExecutor Protocol),原型期与测试用 FakeSandbox。
真实实现(M2,A 公司侧)走 vault:// 取测试凭证、对测试环境真跑。
"""

from __future__ import annotations

from typing import Any, Protocol

from dano.capabilities.types import VerifyResult


class SandboxExecutor(Protocol):
    """五类资产自验证所需的沙箱能力。"""

    async def connection_test(self, connector_body: dict[str, Any]) -> VerifyResult:
        """连接测试:认证能否通过(连接器双关之一)。"""
        ...

    async def run_action(
        self, connector_body: dict[str, Any], inputs: dict[str, Any]
    ) -> VerifyResult:
        """沙箱业务动作:测试账号真跑一个动作(连接器双关之二)。"""
        ...

    async def write_read_back(
        self, subsystem: str, field: str, value: Any
    ) -> VerifyResult:
        """写回实测(字段映射):写入值 → 读回 → 比对。"""
        ...

    async def health_check(self, env_profile: dict[str, Any]) -> VerifyResult:
        """健康检查(环境画像):连接探测 + 凭证有效 + 最小权限校验。"""
        ...


class FakeSandbox:
    """原型/测试用沙箱。可配置每类校验的成败,用于驱动硬关卡逻辑。

    默认全部通过;通过 fail_* 集合注入失败场景,验证「不过关退回重生成」。
    """

    def __init__(
        self,
        *,
        fail_connection: bool = False,
        fail_actions: set[str] | None = None,
        mismatch_fields: set[str] | None = None,
        fail_health: bool = False,
    ) -> None:
        self.fail_connection = fail_connection
        self.fail_actions = fail_actions or set()
        self.mismatch_fields = mismatch_fields or set()
        self.fail_health = fail_health

    async def connection_test(self, connector_body: dict[str, Any]) -> VerifyResult:
        if self.fail_connection:
            return VerifyResult(passed=False, detail="认证失败(连接测试不通过)")
        return VerifyResult(passed=True, detail="认证通过", evidence={"auth": connector_body.get("auth_kind")})

    async def run_action(
        self, connector_body: dict[str, Any], inputs: dict[str, Any]
    ) -> VerifyResult:
        action = connector_body.get("action", "")
        if action in self.fail_actions:
            return VerifyResult(passed=False, detail=f"沙箱动作 {action} 失败")
        # 模拟一个成功返回(带单号),供后置断言核对
        return VerifyResult(
            passed=True,
            detail=f"沙箱动作 {action} 成功",
            evidence={"response": {"request_id": "SBX-0001", "status": "已提交"}, "http": 200},
        )

    async def write_read_back(
        self, subsystem: str, field: str, value: Any
    ) -> VerifyResult:
        if field in self.mismatch_fields:
            return VerifyResult(passed=False, detail=f"字段 {field} 写回不一致")
        return VerifyResult(passed=True, detail=f"字段 {field} 写回一致", evidence={"wrote": value, "read": value})

    async def health_check(self, env_profile: dict[str, Any]) -> VerifyResult:
        if self.fail_health:
            return VerifyResult(passed=False, detail="健康检查失败")
        # 凭证有效性校验(流程5):撤销 / 过期 即不通过
        pol = env_profile.get("credential_policy", {}) or {}
        if pol.get("revoked"):
            return VerifyResult(passed=False, detail="凭证已撤销")
        exp = pol.get("expires_at")
        if exp:
            from datetime import datetime
            try:
                if datetime.fromisoformat(exp) < datetime.now():
                    return VerifyResult(passed=False, detail=f"凭证已过期({exp})")
            except ValueError:
                pass
        return VerifyResult(passed=True, detail="健康检查通过(凭证有效)")


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


class MultiSystemSandbox:
    """跨子系统路由的真实沙箱(接入多系统时用)。

    工厂用一个 sandbox 跑所有子系统,但每个系统的 base_url/鉴权/测试凭证各不相同。
    本类按调用上下文路由到对应系统的 RealSandbox:
    - connection_test / run_action:从连接器 auth_ref(vault://tenant/<key>)取系统 key。
    - write_read_back:入参直接带 subsystem。
    - health_check:从环境画像 base_url 反查系统 key。
    """

    def __init__(
        self,
        by_key: dict[str, RealSandbox],
        *,
        base_url_to_key: dict[str, str] | None = None,
    ) -> None:
        self._by_key = by_key
        self._url2key = base_url_to_key or {}

    def _sb(self, key: str) -> RealSandbox:
        sb = self._by_key.get(key)
        if sb is None:
            raise RuntimeError(f"无该系统的测试沙箱(接入材料/凭证缺失): {key}")
        return sb

    def _route_connector(self, connector_body: dict[str, Any]) -> RealSandbox:
        from dano.infra.vault import parse_ref

        _, key = parse_ref(connector_body["auth_ref"])
        return self._sb(key)

    async def connection_test(self, connector_body: dict[str, Any]) -> VerifyResult:
        return await self._route_connector(connector_body).connection_test(connector_body)

    async def run_action(
        self, connector_body: dict[str, Any], inputs: dict[str, Any]
    ) -> VerifyResult:
        return await self._route_connector(connector_body).run_action(connector_body, inputs)

    async def write_read_back(
        self, subsystem: str, field: str, value: Any
    ) -> VerifyResult:
        from dano.execution.connectors.executor import system_key_for
        from dano.shared.enums import Subsystem

        return await self._sb(system_key_for(Subsystem(subsystem))).write_read_back(
            subsystem, field, value
        )

    async def health_check(self, env_profile: dict[str, Any]) -> VerifyResult:
        key = self._url2key.get(env_profile.get("base_url", ""))
        if key is None:
            return VerifyResult(passed=False, detail="环境画像 base_url 未匹配到任何接入系统")
        return await self._sb(key).health_check(env_profile)
