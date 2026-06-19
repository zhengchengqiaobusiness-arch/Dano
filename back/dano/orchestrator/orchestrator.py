"""主智能体编排(流程6 状态机)—— 纯逻辑,依赖可注入。

主智能体只编排、不直接执行;任一闸门/断言不过即停;终态只有确定的几种。
与 Temporal 解耦:本类是可离线测试的业务逻辑,workflow.py 只做持久化薄包装。
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

import structlog

from dano.execution.connectors.executor import ActionExecutor
from dano.execution.harness.harness import Harness, tool_name_for
from dano.assets.store import AssetStore
from dano.orchestrator.gate import GateAction, PolicyGate
from dano.orchestrator.skills import SkillRegistry
from dano.orchestrator.types import SkillSpec, TaskOutcome
from dano.shared.asset_bodies import (
    Assertions,
    ConnectorBody,
    PageScriptBody,
    PolicyRuleBody,
)
from dano.shared.enums import (
    AssetType,
    Outcome,
    RecoveryAction,
    RiskLevel,
    Subsystem,
    TaskState,
)
from dano.shared.expr import safe_eval
from dano.shared.models import AssertionResult, Evidence, ExecResult, Scope, TaskBrief
from dano.verification.closure import VerificationClosure

log = structlog.get_logger(__name__)


# ─────────────────────── 复合流程入参解析(阶段2)───────────────────────
def _set_path(obj: dict, path: str, value) -> None:  # noqa: ANN001
    """按点路径写入嵌套 dict:_set_path(b, 'flowTask.taskId', v)。"""
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _get_path(obj, path: str):  # noqa: ANN001
    """按点路径读取嵌套 dict:_get_path(resp, 'data.taskId')。缺失返回 None。"""
    cur = obj
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _resolve_source(source: str, user_fields: dict, step_outputs: dict):  # noqa: ANN001
    """解析来源表达式:const:/field:/step:。"""
    kind, _, rest = source.partition(":")
    if kind == "const":
        return rest
    if kind == "field":
        return user_fields.get(rest)
    if kind == "step":
        action, _, path = rest.partition(".")
        return _get_path(step_outputs.get(action, {}), path)
    return None


def _resolve_step_inputs(mapping: dict, user_fields: dict, step_outputs: dict) -> dict:
    """把一步的 {目标路径: 来源} 映射拼成请求体。"""
    body: dict = {}
    for target_path, source in mapping.items():
        _set_path(body, target_path, _resolve_source(source, user_fields, step_outputs))
    return body

# 确认卡片回调:给定 skill+字段,返回用户是否确认(L3)。默认不确认(安全)。
ConfirmHandler = Callable[[SkillSpec, dict], bool]
CredentialResolver = Callable[[dict[str, str]], dict[str, str]]


def _default_confirm(skill: SkillSpec, fields: dict) -> bool:
    return False


def _noop_resolver(refs: dict[str, str]) -> dict[str, str]:
    return {k: f"resolved::{v}" for k, v in refs.items()}


class Orchestrator:
    def __init__(
        self,
        *,
        registry: SkillRegistry,
        store: AssetStore,
        harness: Harness,
        action_executor: ActionExecutor,
        closure: VerificationClosure | None = None,
        gate: PolicyGate | None = None,
        resolve_credentials: CredentialResolver = _noop_resolver,
        page_runtime=None,     # PageActionRuntime(可选,无 API 页面执行)
        failure_handler=None,  # FailureHandler(可选,流程10;Phase 4 接)
        heal_queue=None,       # 漂移自愈触发队列(流程11;Phase 4 接)
    ) -> None:
        self.registry = registry
        self.store = store
        self.harness = harness
        self.executor = action_executor
        self.closure = closure or VerificationClosure()
        self.gate = gate or PolicyGate()
        self.resolve = resolve_credentials
        self.page_runtime = page_runtime
        self.failure_handler = failure_handler
        self.heal_queue = heal_queue

    async def _connector_body(self, asset_id: UUID) -> ConnectorBody:
        env = await self.store.get(asset_id)
        assert env is not None, "连接器资产不存在"
        return ConnectorBody.model_validate(env.body)

    async def _enqueue_heal(self, skill, reason: str) -> None:  # noqa: ANN001
        from dano.resilience.queue import HealRequest

        await self.heal_queue.enqueue(HealRequest(
            skill_id=skill.skill_id, subsystem=skill.subsystem.value,
            action=skill.action, reason=reason))

    async def _load_policy(self, scope: Scope) -> PolicyRuleBody | None:
        env = await self.store.get_published(
            AssetType.POLICY_RULE, scope, asset_key=AssetType.POLICY_RULE.value
        )
        return PolicyRuleBody.model_validate(env.body) if env else None

    async def _snapshot(
        self, subsystem: Subsystem, query_action: str | None, fields: dict, creds: dict
    ) -> dict:
        """重查取快照(事实核查用)。无查询动作 → 空。"""
        if not query_action:
            return {}
        qskill = self.registry.by_action(subsystem, query_action)
        if qskill is None:
            return {}
        qbody = await self._connector_body(qskill.connector_asset_id)
        inputs = {b.param: fields[b.platform_std] for b in qbody.field_bindings if b.platform_std in fields}
        resp = await self.executor.execute(qbody.model_dump(), inputs, creds)
        return resp.body

    # 注:NL 意图分析 + 多智能体路由(原 handle())已移除——阶段二编排交前端。
    # 后端只保留可信瘦执行入口 invoke_skill(前端给 skill_id+字段,后端取资产/凭证/断言执行)。

    async def invoke_skill(
        self,
        subsystem: Subsystem,
        action: str,
        fields: dict,
        *,
        tenant: str = "a-corp",
        confirm: bool = False,
    ) -> TaskOutcome:
        """结构化直调一个动作 Skill(前端 / Skill 网关用)。

        跳过自然语言意图分析(动作+字段已给定),但**保留全部受控管道**:
        完整性校验 → 制度+风险闸门 → harness 四重隔离+断言 → 事实核查。
        与 handle() 共用 _run_api/_run_page,确保直调与编排同一条安全链路。
        """
        from dano.orchestrator.types import Intent

        task_id = uuid4()
        skill = self.registry.by_action(subsystem, action)
        if skill is None:
            return TaskOutcome(task_id=task_id, state=TaskState.CAPABILITY_GAP,
                               message=f"未知动作 Skill: {subsystem.value}.{action}")

        intent = Intent(kind="action", action_hint=action, fields=dict(fields))
        missing = [k for k in skill.required_fields if k not in fields]
        if missing:
            return TaskOutcome(task_id=task_id, state=TaskState.NEEDS_INPUT, skill_id=skill.skill_id,
                               message=f"缺必填字段: {missing}", audit={"missing": missing})

        scope = Scope(tenant=tenant, subsystem=skill.subsystem)
        policy = await self._load_policy(scope)
        decision = self.gate.decide(
            risk_level=RiskLevel(skill.risk_level), fields=intent.fields, policy=policy
        )
        confirm_fn = lambda s, f: confirm  # noqa: E731
        if decision.action == GateAction.REJECT:
            return TaskOutcome(task_id=task_id, state=TaskState.REJECTED, skill_id=skill.skill_id,
                               message=decision.reason)
        if decision.action == GateAction.CONFIRM and not confirm:
            return TaskOutcome(task_id=task_id, state=TaskState.CANCELLED, skill_id=skill.skill_id,
                               message="需用户确认(confirm=true)")

        if skill.is_adapter:
            return await self._run_adapter(task_id, tenant, skill, intent)
        if skill.is_workflow:
            return await self._run_workflow(task_id, tenant, skill, intent)
        if skill.has_api:
            return await self._run_api(task_id, tenant, skill, intent, confirm=confirm_fn)
        return await self._run_page(task_id, skill, intent, confirm=confirm_fn)

    async def _run_adapter(self, task_id, tenant, skill, intent) -> TaskOutcome:  # noqa: ANN001
        """代码适配器 Skill(goal 模式生成):隔离 runner 执行 source,过成败规则 + 事实核查。

        凭证运行期注入(不进源码);base_url 取自已发布环境画像;事实核查回查确认真生效。
        """
        from dano.execution.adapter import AdapterRunner
        from dano.execution.connectors.executor import system_key_for
        scope = Scope(tenant=tenant, subsystem=skill.subsystem)
        ep = await self.store.get_published(AssetType.ENV_PROFILE, scope, asset_key="env_profile")
        base_url = ((ep.body.get("base_url") if ep else "") or "")
        resolved = self.resolve({"token": f"vault://{tenant}/{system_key_for(skill.subsystem)}"})
        creds = {"token": resolved.get("token") or next(iter(resolved.values()), "")}
        # 注入运行期内部量:base_url + 发布时常量(如 __templateId__);用户只传业务字段
        inputs = {**intent.fields, "__base_url__": base_url, **(skill.adapter_consts or {})}

        res = await AdapterRunner().run(source=skill.adapter_source, inputs=inputs,
                                        credentials=creds, entry=skill.adapter_entry)
        ok, detail = res.ok, (res.error or "")
        if ok and skill.adapter_success_rule:
            try:
                ok = bool(safe_eval(skill.adapter_success_rule, {"response": res.output, "http": 200}))
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                detail = f"未满足 success_rule={skill.adapter_success_rule!r}"
        fc_ev = None
        if ok and skill.adapter_fact_check:
            from dano.execution.fact_check import run_fact_check
            from dano.shared.asset_bodies import FactCheckSpec
            spec = FactCheckSpec.model_validate(skill.adapter_fact_check)
            ctx = {**intent.fields, **(res.output if isinstance(res.output, dict) else {})}
            ok, fc_ev = await run_fact_check(spec, context=ctx,
                                             call=self._http_caller(base_url, creds))
            if not ok:
                detail = f"事实核查未过(疑似空操作): {spec.assert_expr}"
        out = res.output if isinstance(res.output, dict) else {"value": res.output}
        er = ExecResult(task_id=task_id, outcome=Outcome.PASSED if ok else Outcome.FAILED,
                        evidence=Evidence(request_body=inputs, response_body=out),
                        structured_output=out)
        log.info("adapter.invoke", skill=skill.skill_id, ok=ok)
        return TaskOutcome(
            task_id=task_id, state=TaskState.COMPLETED if ok else TaskState.FAILED,
            skill_id=skill.skill_id, exec_result=er,
            message="adapter 完成 + 事实核查通过" if ok else f"adapter 跑不通 → 流程10:{detail}",
            audit={"output": res.output, "fact_check": fc_ev, "intent": intent.action_hint})

    @staticmethod
    def _http_caller(base_url: str, creds: dict):  # noqa: ANN205
        """事实核查回查用的 call(method, path, body)->(http, json)。"""
        base = base_url.rstrip("/")

        async def call(method: str, path: str, body=None):  # noqa: ANN001
            import httpx
            from dano.infra.http import tls_verify
            tok = (creds.get("token") or "").strip()
            async with httpx.AsyncClient(timeout=30, verify=tls_verify()) as c:
                h = {"Authorization": f"Bearer {tok}"} if tok else {}
                if method.upper() == "GET":
                    r = await c.get(base + path, headers=h)
                else:
                    r = await c.request(method, base + path, json=body, headers=h)
            try:
                return r.status_code, r.json()
            except Exception:  # noqa: BLE001
                return r.status_code, {"raw": r.text}

        return call

    async def _run_workflow(self, task_id, tenant, skill, intent) -> TaskOutcome:  # noqa: ANN001
        """复合流程 Skill(阶段2):按 steps 顺序跑连接器,前一步输出串给后一步。

        每步执行后按成败规则判定;任一步跑不通即整体失败。绝不为某家写 if/else——
        步骤与映射来自已发布的 WORKFLOW 资产(声明式),执行层是通用解释器。
        """
        scope = Scope(tenant=tenant, subsystem=skill.subsystem)
        user_fields = dict(intent.fields)
        rule = skill.workflow_success_rule
        step_outputs: dict[str, dict] = {}
        assertion_results: list[AssertionResult] = []
        trace: list[dict] = []   # 每步 请求/响应 留痕(诊断真实系统用)
        creds: dict | None = None
        last_body: dict = {}

        for step in skill.workflow_steps:
            action = step["action"]
            env = await self.store.get_published(AssetType.CONNECTOR, scope, asset_key=action)
            if env is None:
                return TaskOutcome(task_id=task_id, state=TaskState.CAPABILITY_GAP, skill_id=skill.skill_id,
                                   message=f"复合流程缺少步骤连接器: {action}")
            connector = ConnectorBody.model_validate(env.body)
            if creds is None:
                creds = self.resolve({"primary": connector.auth_ref})
            body = _resolve_step_inputs(step.get("inputs", {}), user_fields, step_outputs)
            try:
                resp = await self.executor.execute(connector.model_dump(), body, creds)
            except Exception as e:  # noqa: BLE001
                return TaskOutcome(task_id=task_id, state=TaskState.FAILED, skill_id=skill.skill_id,
                                   message=f"流程步骤 {action} 异常: {e}",
                                   audit={"failed_step": action, "trace": trace})
            ok = 200 <= resp.http < 300
            if ok and rule:
                try:
                    ok = bool(safe_eval(rule, {"response": resp.body, "http": resp.http}))
                except Exception:  # noqa: BLE001
                    ok = False
            assertion_results.append(AssertionResult(name=f"step:{action}", passed=ok,
                                                     detail=f"HTTP {resp.http}"))
            step_outputs[action] = resp.body
            last_body = resp.body
            trace.append({"action": action, "method": connector.method, "endpoint": connector.endpoint,
                          "request": body, "http": resp.http, "response": resp.body, "ok": ok})
            log.info("workflow.step", skill=skill.skill_id, step=action, http=resp.http, ok=ok)
            if not ok:
                er = ExecResult(task_id=task_id, outcome=Outcome.FAILED,
                                assertion_results=assertion_results,
                                evidence=Evidence(request_body=body, response_body=resp.body))
                return TaskOutcome(task_id=task_id, state=TaskState.FAILED, skill_id=skill.skill_id,
                                   exec_result=er, message=f"流程步骤 {action} 跑不通 → 流程10",
                                   audit={"failed_step": action, "trace": trace})

        er = ExecResult(task_id=task_id, outcome=Outcome.PASSED, assertion_results=assertion_results,
                        evidence=Evidence(response_body=last_body), structured_output=last_body)
        return TaskOutcome(task_id=task_id, state=TaskState.COMPLETED, skill_id=skill.skill_id,
                           exec_result=er, message=f"流程完成({len(skill.workflow_steps)} 步全通过)",
                           audit={"trace": trace, "intent": intent.action_hint})

    async def _run_api(self, task_id, tenant, skill, intent, *, confirm) -> TaskOutcome:  # noqa: ANN001
        connector = await self._connector_body(skill.connector_asset_id)
        creds = self.resolve({"primary": connector.auth_ref})
        before = await self._snapshot(skill.subsystem, skill.fact_check_query, intent.fields, creds)

        brief = TaskBrief(
            task_id=task_id, tenant=tenant, subsystem=skill.subsystem,
            skill_id=skill.skill_id, action=skill.action, fields=intent.fields,
            tool_whitelist=[tool_name_for(skill.subsystem, skill.action)],
            skill_mount=[skill.skill_id],
            credential_refs={"primary": connector.auth_ref},
            assertions=Assertions.model_validate(connector.assertions.model_dump()),
            risk_level=RiskLevel(skill.risk_level),
        )

        # 执行(失败 → 流程10:分类/熔断/限次受控重试)
        attempt = 1
        while True:
            exec_result = await self.harness.run(brief, connector.model_dump(), pre_facts=before)
            if exec_result.outcome != Outcome.FAILED or self.failure_handler is None:
                break
            decision = await self.failure_handler.handle(
                skill.skill_id, exec_result, attempt=attempt)
            if decision.should_retry:
                attempt += 1
                continue
            # 页面/字段变更 → 自动触发流程11 自愈(入队)
            if decision.action == RecoveryAction.REGENERATE and self.heal_queue is not None:
                await self._enqueue_heal(skill, decision.reason)
            return TaskOutcome(task_id=task_id, state=TaskState.FAILED, skill_id=skill.skill_id,
                               exec_result=exec_result, message=f"执行跑不通 → 流程10:{decision.reason}",
                               audit={"recovery": decision.model_dump(mode="json"), "attempts": attempt})
        if exec_result.outcome == Outcome.FAILED:
            return TaskOutcome(task_id=task_id, state=TaskState.FAILED, skill_id=skill.skill_id,
                               exec_result=exec_result, message="执行跑不通 → 流程10")
        if self.failure_handler is not None:
            await self.failure_handler.on_success(skill.skill_id)  # 成功清零失败计数

        after = await self._snapshot(skill.subsystem, skill.fact_check_query, intent.fields, creds)
        closure = await self.closure.verify(
            exec_result, fact_expr=skill.fact_check_expr,
            before=before, after=after, fields=intent.fields,
            intent=intent.action_hint, action=skill.action,
            risk_level=RiskLevel(skill.risk_level),
        )
        return TaskOutcome(
            task_id=task_id, state=closure.state, skill_id=skill.skill_id,
            exec_result=exec_result, message=closure.detail,
            audit={"before": before, "after": after, "intent": intent.action_hint},
        )

    async def _run_page(self, task_id, skill, intent, *, confirm) -> TaskOutcome:  # noqa: ANN001
        """无 API 页面辅助执行(流程8)。"""
        if self.page_runtime is None:
            return TaskOutcome(task_id=task_id, state=TaskState.TRANSFER_HUMAN,
                               skill_id=skill.skill_id, message="页面运行时未装配")
        env = await self.store.get(skill.page_asset_id)
        assert env is not None, "页面脚本资产不存在"
        script = PageScriptBody.model_validate(env.body)

        exec_result = await self.page_runtime.run(
            task_id, script, intent.fields, confirm=lambda f: confirm(skill, f)
        )
        # 漂移 → 转流程11;取消 → CANCELLED
        if exec_result.structured_output.get("drift"):
            if self.heal_queue is not None:
                await self._enqueue_heal(skill, "页面指纹漂移")  # 自动触发流程11
            return TaskOutcome(task_id=task_id, state=TaskState.DRIFT, skill_id=skill.skill_id,
                               exec_result=exec_result, message="页面指纹漂移 → 流程11 自愈,本次中止")
        if exec_result.structured_output.get("cancelled"):
            return TaskOutcome(task_id=task_id, state=TaskState.CANCELLED, skill_id=skill.skill_id,
                               message="用户取消(提交前预览)")
        if exec_result.outcome == Outcome.FAILED:
            return TaskOutcome(task_id=task_id, state=TaskState.FAILED, skill_id=skill.skill_id,
                               exec_result=exec_result, message="页面执行跑不通 → 流程10")

        closure = await self.closure.verify(
            exec_result, fact_expr=skill.fact_check_expr,
            before={}, after={}, fields=intent.fields,
            intent=intent.action_hint, action=skill.action,
            risk_level=RiskLevel(skill.risk_level),
        )
        return TaskOutcome(
            task_id=task_id, state=closure.state, skill_id=skill.skill_id,
            exec_result=exec_result, message=closure.detail,
            audit={"draft": exec_result.structured_output, "intent": intent.action_hint},
        )
