"""子智能体 harness:包住子智能体的运行容器 + 守卫(流程6.1 / 流程7)。

强制四重隔离:
- 上下文隔离:只收 TaskBrief(看不到完整对话/别的子智能体)。
- 工具隔离:只允许 brief.tool_whitelist 内的连接器工具。
- skill / MCP 隔离:由 brief.skill_mount / mcp_grants 限定(M2 仅校验工具)。
凭证以 vault:// 引用注入,经 resolve_credentials 取值,平台侧不持明文。

执行 = 前置断言 → 调连接器 → 后置断言,二态产出 ExecResult。
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from dano.execution.assertion.engine import AssertionEngine
from dano.execution.connectors.executor import ActionExecutor
from dano.shared.asset_bodies import ConnectorBody
from dano.shared.enums import FailureClass, Outcome, Subsystem
from dano.shared.models import Evidence, ExecResult, TaskBrief

log = structlog.get_logger(__name__)

CredentialResolver = Callable[[dict[str, str]], dict[str, str]]


def tool_name_for(subsystem: Subsystem, action: str) -> str:
    """连接器工具名约定,用于工具隔离白名单校验。"""
    return f"connector:{subsystem.value}:{action}"


def _noop_resolver(refs: dict[str, str]) -> dict[str, str]:
    """默认凭证解析器(测试/原型):不取真实明文,返回占位。"""
    return {k: f"resolved::{v}" for k, v in refs.items()}


class Harness:
    def __init__(
        self,
        *,
        action_executor: ActionExecutor,
        assertion_engine: AssertionEngine | None = None,
        resolve_credentials: CredentialResolver = _noop_resolver,
    ) -> None:
        self.executor = action_executor
        self.assertions = assertion_engine or AssertionEngine()
        self.resolve = resolve_credentials

    def _build_inputs(self, connector: ConnectorBody, fields: dict) -> dict:
        """按 field_bindings 把平台标准字段值映射成连接器入参。"""
        inputs: dict = {}
        for b in connector.field_bindings:
            if b.platform_std in fields:
                inputs[b.param] = fields[b.platform_std]
        return inputs

    async def run(
        self, brief: TaskBrief, connector_body: dict, *, pre_facts: dict | None = None
    ) -> ExecResult:
        """带 OTel span 的执行入口(span 在未配置 tracing 时为 no-op)。"""
        from dano.observability.tracing import span

        with span("harness.run", action=brief.action, subsystem=brief.subsystem.value,
                  task_id=str(brief.task_id)):
            return await self._run_inner(brief, connector_body, pre_facts=pre_facts)

    async def _run_inner(
        self, brief: TaskBrief, connector_body: dict, *, pre_facts: dict | None = None
    ) -> ExecResult:
        connector = ConnectorBody.model_validate(connector_body)

        # ── 四重隔离守卫 ──
        def _deny(reason: str) -> ExecResult:
            log.error("harness.isolation_denied", action=brief.action, reason=reason)
            return ExecResult(
                task_id=brief.task_id, outcome=Outcome.FAILED,
                failure_class=FailureClass.PERMISSION,
                evidence=Evidence(), structured_output={"error": reason},
            )

        # ① 工具隔离
        expected_tool = tool_name_for(brief.subsystem, brief.action)
        if expected_tool not in brief.tool_whitelist:
            return _deny("工具不在白名单(越权拦截)")
        # ② skill 隔离:只能调用挂载在本子智能体的 skill
        if brief.skill_mount and brief.skill_id not in brief.skill_mount:
            return _deny(f"skill {brief.skill_id} 未挂载(skill 隔离)")
        # ③ MCP 隔离:动作所需 MCP 必须在授权清单内
        missing_mcp = [m for m in connector.required_mcp if m not in brief.mcp_grants]
        if missing_mcp:
            return _deny(f"MCP 未授权: {missing_mcp}(MCP 隔离)")

        # ── 凭证引用注入(不持明文)──
        creds = self.resolve(brief.credential_refs)

        # ── 前置断言 ──
        # 仅必填绑定参与完整性判定;可选参数缺省不阻断(契约 required 与此一致)
        required = [b.platform_std for b in connector.field_bindings if b.required]
        fields_complete = all(k in brief.fields for k in required)
        pre_ctx = {
            **brief.fields,
            **(pre_facts or {}),
            "auth_passed": bool(creds),
            "fields_complete": fields_complete,
        }
        pre_results = self.assertions.evaluate_phase(connector.assertions.pre, pre_ctx)
        if not all(r.passed for r in pre_results):
            fc = FailureClass.PARAM if fields_complete else FailureClass.PARAM
            if not creds:
                fc = FailureClass.LOGIN
            log.warning("harness.pre_assert_failed", failed=[r.name for r in pre_results if not r.passed])
            return ExecResult(
                task_id=brief.task_id, outcome=Outcome.FAILED, failure_class=fc,
                assertion_results=pre_results, evidence=Evidence(),
            )

        # ── 执行(确定性:按连接器规格调用)──
        inputs = self._build_inputs(connector, brief.fields)
        idem = f"{brief.task_id}:{brief.action}"
        resp = await self.executor.execute(
            connector.model_dump(), inputs, creds, idempotency_key=idem
        )

        # ── 后置断言 ──
        post_ctx = {"response": resp.body, "http": resp.http}
        post_results = self.assertions.evaluate_phase(connector.assertions.post, post_ctx)
        results = pre_results + post_results
        outcome = Outcome.PASSED if all(r.passed for r in results) else Outcome.FAILED

        evidence = Evidence(request_body=inputs, response_body=resp.body)
        failure_class = None
        if outcome == Outcome.FAILED:
            failure_class = FailureClass.SYSTEM if resp.http >= 500 else FailureClass.PARAM

        log.info(
            "harness.executed",
            action=brief.action, subsystem=brief.subsystem.value,
            outcome=outcome.value, http=resp.http,
        )
        return ExecResult(
            task_id=brief.task_id, outcome=outcome, assertion_results=results,
            evidence=evidence, structured_output=resp.body, failure_class=failure_class,
        )
