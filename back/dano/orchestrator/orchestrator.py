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


def _resolve_source(source: str, ctx: dict):  # noqa: ANN001
    """解析来源表达式:const:/field:/step:/var:/item:/select:。ctx={fields,vars,steps,item}。"""
    kind, _, rest = source.partition(":")
    if kind == "const":
        return rest
    if kind == "field":
        return ctx.get("fields", {}).get(rest)
    if kind in ("var", "select"):                 # compute 产出 / select 绑定都落在 vars
        return ctx.get("vars", {}).get(rest)
    if kind == "step":
        action, _, path = rest.partition(".")
        return _get_path(ctx.get("steps", {}).get(action, {}), path)
    if kind == "item":
        item = ctx.get("item")
        return _get_path(item, rest) if rest else item
    return None


def _resolve_step_inputs(mapping: dict, ctx: dict) -> dict:
    """把一步的 {目标路径: 来源} 映射拼成请求体。ctx={fields,vars,steps,item}。"""
    body: dict = {}
    for target_path, source in mapping.items():
        _set_path(body, target_path, _resolve_source(source, ctx))
    return body


def _expr_ctx(ctx: dict, response: object = None) -> dict:
    """组装表达式求值上下文(compute/branch/不变量用):业务字段 + 派生变量 + 当前项 + 回查响应。"""
    d: dict = {**ctx.get("fields", {}), **ctx.get("vars", {})}
    if ctx.get("item") is not None:
        d["item"] = ctx["item"]
    d["response"] = response if response is not None else {}
    return d


def _candidate_id(c: object):  # noqa: ANN001
    """从候选项里取标识:优先 id,否则第一个值(消歧选中后绑给后续步)。"""
    if isinstance(c, dict):
        if "id" in c:
            return c["id"]
        return next(iter(c.values()), None)
    return c


class _StepFailed(Exception):
    def __init__(self, reason: str, action: str | None = None) -> None:
        super().__init__(reason)
        self.reason, self.action = reason, action


class _CapabilityGap(Exception):
    def __init__(self, action: str) -> None:
        super().__init__(action)
        self.action = action


class _NeedSelect(Exception):
    def __init__(self, bind: str, candidates: list, label_template: str | None) -> None:
        super().__init__(bind)
        self.bind, self.candidates, self.label_template = bind, candidates, label_template

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
        holidays: list[str] | None = None,   # 日历源:注入复合流程 compute 的 business_days(节假日)
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
        self.holidays = list(holidays or [])

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
        return await self._run_page(task_id, skill, intent, confirm=confirm_fn, tenant=tenant)

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
        """复合流程 Skill(DSL v2):前置不变量 → 解释器执行 steps(call/compute/branch/foreach/select)
        → 业务不变量。步骤/映射/规则全来自已发布 WORKFLOW 资产(声明式),执行层是通用解释器,绝不为某家写 if/else。
        """
        scope = Scope(tenant=tenant, subsystem=skill.subsystem)
        # vars 预置 holidays(日历源):compute 的 business_days(start,end,holidays) 可直接引用
        ctx: dict = {"fields": dict(intent.fields), "vars": {"holidays": self.holidays},
                     "steps": {}, "item": None}
        state: dict = {"creds": None, "rule": skill.workflow_success_rule, "scope": scope,
                       "trace": [], "assertions": [], "last_body": {}, "connectors": {},
                       "branches": []}    # 记录走过的分支臂(branch_id, 真/假),供沙箱分支覆盖统计

        # ① 前置不变量(办理前校验:不过则拒、绝不写)
        for inv in (skill.workflow_preconditions or []):
            ok, detail = await self._check_invariant(inv, ctx, state)
            if not ok:
                return TaskOutcome(task_id=task_id, state=TaskState.REJECTED, skill_id=skill.skill_id,
                                   message=f"前置校验不通过:{detail}", audit={"trace": state["trace"]})

        # ② 解释器执行步骤
        try:
            await self._exec_steps(skill.workflow_steps, ctx, state)
        except _CapabilityGap as e:
            return TaskOutcome(task_id=task_id, state=TaskState.CAPABILITY_GAP, skill_id=skill.skill_id,
                               message=f"复合流程缺少步骤连接器: {e.action}")
        except _NeedSelect as e:
            return TaskOutcome(task_id=task_id, state=TaskState.NEEDS_SELECT, skill_id=skill.skill_id,
                               message=f"需从候选中选择 {e.bind}",
                               audit={"select": {"bind": e.bind, "candidates": e.candidates,
                                                 "label_template": e.label_template}, "trace": state["trace"]})
        except _StepFailed as e:
            er = ExecResult(task_id=task_id, outcome=Outcome.FAILED, assertion_results=state["assertions"],
                            evidence=Evidence(response_body=state["last_body"]))
            return TaskOutcome(task_id=task_id, state=TaskState.FAILED, skill_id=skill.skill_id,
                               exec_result=er, message=f"{e.reason} → 流程10",
                               audit={"failed_step": e.action, "trace": state["trace"],
                                      "branches": state["branches"]})

        # ③ 业务不变量(办理后正确性:回查证实,不只看字面 200)
        for inv in (skill.workflow_invariants or []):
            ok, detail = await self._check_invariant(inv, ctx, state)
            if not ok:
                er = ExecResult(task_id=task_id, outcome=Outcome.FAILED, assertion_results=state["assertions"],
                                evidence=Evidence(response_body=state["last_body"]))
                return TaskOutcome(task_id=task_id, state=TaskState.FAILED, skill_id=skill.skill_id,
                                   exec_result=er, message=f"业务不变量不通过:{detail}",
                                   audit={"trace": state["trace"]})

        er = ExecResult(task_id=task_id, outcome=Outcome.PASSED, assertion_results=state["assertions"],
                        evidence=Evidence(response_body=state["last_body"]), structured_output=state["last_body"])
        return TaskOutcome(task_id=task_id, state=TaskState.COMPLETED, skill_id=skill.skill_id,
                           exec_result=er, message="流程完成(全步骤 + 不变量通过)",
                           audit={"trace": state["trace"], "vars": ctx["vars"],
                                  "branches": state["branches"], "intent": intent.action_hint})

    async def _exec_steps(self, steps: list, ctx: dict, state: dict, prefix: str = "") -> None:  # noqa: ANN001
        """递归解释器:按序执行 DSL v2 节点。失败/缺连接器/需选择 → 抛异常,由 _run_workflow 转终态。

        prefix:节点在树中的稳定路径(供分支覆盖统计;与 dsl_grounding.branch_ids 同一编号约定)。
        """
        for i, step in enumerate(steps):
            sid = f"{prefix}{i}"
            kind = step.get("kind", "call")
            if kind == "compute":
                for name, expr in (step.get("outputs") or {}).items():
                    try:
                        ctx["vars"][name] = safe_eval(expr, _expr_ctx(ctx))
                    except Exception as e:  # noqa: BLE001
                        raise _StepFailed(f"派生计算 {name} 失败: {e}", "compute") from e
                log.info("workflow.compute", outputs=list((step.get("outputs") or {}).keys()))
            elif kind == "branch":
                try:
                    cond = bool(safe_eval(step["condition"], _expr_ctx(ctx)))
                except Exception as e:  # noqa: BLE001
                    raise _StepFailed(f"分支条件求值失败: {e}", "branch") from e
                state["branches"].append([sid, cond])      # 记录该分支走了哪一臂(覆盖统计用)
                log.info("workflow.branch", condition=step["condition"], taken=cond)
                arm = step.get("then") if cond else (step.get("otherwise") or [])
                await self._exec_steps(arm, ctx, state, f"{sid}.{'t' if cond else 'f'}.")
            elif kind == "foreach":
                items = _resolve_source(step["over"], ctx)
                items = items if isinstance(items, list) else []
                log.info("workflow.foreach", over=step["over"], n=len(items))
                for it in items:
                    ctx["item"] = it
                    await self._exec_steps(step.get("steps") or [], ctx, state, f"{sid}.s.")
                ctx["item"] = None
            elif kind == "select":
                await self._exec_select(step, ctx, state)
            else:
                await self._exec_call(step, ctx, state)

    async def _load_connector(self, action: str | None, state: dict) -> ConnectorBody:  # noqa: ANN001
        if not action:
            raise _StepFailed("调用步缺 action", None)
        cache = state["connectors"]
        if action in cache:
            return cache[action]
        env = await self.store.get_published(AssetType.CONNECTOR, state["scope"], asset_key=action)
        if env is None:
            raise _CapabilityGap(action)
        c = ConnectorBody.model_validate(env.body)
        if state["creds"] is None:
            state["creds"] = self.resolve({"primary": c.auth_ref})
        cache[action] = c
        return c

    async def _exec_call(self, step: dict, ctx: dict, state: dict) -> None:  # noqa: ANN001
        action = step.get("action")
        connector = await self._load_connector(action, state)
        body = _resolve_step_inputs(step.get("inputs") or {}, ctx)
        try:
            resp = await self.executor.execute(connector.model_dump(), body, state["creds"])
        except Exception as e:  # noqa: BLE001
            raise _StepFailed(f"流程步骤 {action} 异常: {e}", action) from e
        ok = 200 <= resp.http < 300
        if ok and state["rule"]:
            try:
                ok = bool(safe_eval(state["rule"], {"response": resp.body, "http": resp.http}))
            except Exception:  # noqa: BLE001
                ok = False
        state["assertions"].append(AssertionResult(name=f"step:{action}", passed=ok, detail=f"HTTP {resp.http}"))
        ctx["steps"][action] = resp.body
        state["last_body"] = resp.body
        state["trace"].append({"action": action, "method": connector.method, "endpoint": connector.endpoint,
                               "request": body, "http": resp.http, "response": resp.body, "ok": ok})
        log.info("workflow.step", step=action, http=resp.http, ok=ok)
        if not ok:
            raise _StepFailed(f"流程步骤 {action} 跑不通", action)

    async def _run_query(self, action: str, params: dict, ctx: dict, state: dict) -> dict:  # noqa: ANN001
        """回查/取候选用的只读调用(不计入断言/成败,只取响应体)。"""
        connector = await self._load_connector(action, state)
        body = _resolve_step_inputs(params or {}, ctx)
        resp = await self.executor.execute(connector.model_dump(), body, state["creds"])
        return resp.body if isinstance(resp.body, dict) else {"value": resp.body}

    async def _exec_select(self, step: dict, ctx: dict, state: dict) -> None:  # noqa: ANN001
        """消歧:从某查询候选里选一个绑给后续步。用户预选(input 给了 bind)或唯一候选 → 自动;
        否则抛 _NeedSelect(上层暂以 NEEDS_INPUT 返回候选,Phase 5 接 NEEDS_SELECT)。"""
        bind = step["bind"]
        pre = ctx["fields"].get(bind)
        if pre not in (None, ""):
            ctx["vars"][bind] = pre
            return
        body = await self._run_query(step["from_action"], {}, ctx, state)
        lp = step.get("list_path")
        candidates = _get_path(body, lp) if lp else body
        candidates = candidates if isinstance(candidates, list) else []
        if len(candidates) == 1:
            ctx["vars"][bind] = _candidate_id(candidates[0])
            return
        raise _NeedSelect(bind, candidates, step.get("label_template"))

    async def _check_invariant(self, inv: dict, ctx: dict, state: dict) -> tuple[bool, str]:  # noqa: ANN001
        """求值一条不变量。给了 evidence 则先回查真实系统(response=回查体);否则 response=末步响应体。"""
        ev = inv.get("evidence") or {}
        if ev.get("query_action"):
            try:
                resp = await self._run_query(ev["query_action"], ev.get("params") or {}, ctx, state)
            except _CapabilityGap as e:
                return False, f"回查动作未发布: {e.action}"
            except Exception as e:  # noqa: BLE001
                return False, f"回查失败: {e}"
        else:
            resp = state["last_body"]
        try:
            ok = bool(safe_eval(inv["check"], _expr_ctx(ctx, response=resp)))
        except Exception as e:  # noqa: BLE001
            return False, f"表达式求值失败: {e}"
        return ok, (inv.get("message") or inv.get("check") or "")

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

    async def list_field_options(self, subsystem: Subsystem, action: str, field: str,
                                 *, tenant: str = "") -> dict:
        """**实时**列出某选择型字段的当前可选项 —— 直接调它的来源接口,带运行期登录态(与 invoke 同一套配置)。
        问题1:把接口放进 skill。选字段前先拉真实选项,agent 从中选,不凭空猜、不靠过时快照。失败 → options=[]。"""
        import json as _json

        from dano.execution.page.request_capture import fetch_field_options
        from dano.execution.page.sessions import session_path_if_exists
        from dano.infra.http import tls_verify
        from dano.infra.token_store import get_token_headers, merge_auth_headers
        skill = self.registry.by_action(subsystem, action)
        if skill is None or not getattr(skill, "page_asset_id", None):
            return {"field": field, "options": [], "count": 0, "note": "未知动作 / 非页面型 skill"}
        env = await self.store.get(skill.page_asset_id)
        apir = (env.body or {}).get("api_request") if env else None
        if not apir:
            return {"field": field, "options": [], "count": 0, "note": "该 skill 无接口请求"}
        scope = Scope(tenant=tenant, subsystem=skill.subsystem)
        ep = await self.store.get_published(AssetType.ENV_PROFILE, scope, asset_key="env_profile")
        base_url = ((ep.body.get("base_url") if ep else "") or "")
        storage = None
        sp = session_path_if_exists(tenant, skill.subsystem.value)
        if sp:
            try:
                storage = _json.loads(open(sp, encoding="utf-8").read())
            except Exception:  # noqa: BLE001
                pass
        override = await get_token_headers(tenant, skill.subsystem.value)   # 运行期最新鉴权头(治焊死旧 token 过期)
        if override:
            apir = merge_auth_headers(apir, override)
        return await fetch_field_options(apir, field, base_url=base_url, storage_state=storage,
                                         verify=tls_verify())

    async def _run_page(self, task_id, skill, intent, *, confirm, tenant="") -> TaskOutcome:  # noqa: ANN001
        """无 API 页面辅助执行(流程8)。有 api_request(抓请求路径)则直接发请求,不开浏览器。"""
        env = await self.store.get(skill.page_asset_id)
        assert env is not None, "页面脚本资产不存在"

        # 抓请求路径:带登录态直接发 SPA 内部接口(参数填回 body_template)。已过 L3 确认闸门。
        if (env.body or {}).get("api_request"):
            import json as _json

            from dano.execution.page.request_capture import execute_api   # 单请求或多步工作流(Q3)自动分派
            from dano.execution.page.sessions import session_path_if_exists
            from dano.infra.http import tls_verify
            scope = Scope(tenant=tenant, subsystem=skill.subsystem)
            ep = await self.store.get_published(AssetType.ENV_PROFILE, scope, asset_key="env_profile")
            base_url = ((ep.body.get("base_url") if ep else "") or "")
            storage = None
            sp = session_path_if_exists(tenant, skill.subsystem.value)
            if sp:
                try:
                    storage = _json.loads(open(sp, encoding="utf-8").read())
                except Exception:  # noqa: BLE001
                    pass
            # 运行期真鉴权:用 token_store(PG)里最新存的鉴权头覆盖**焊进资产的旧 token**(录制那刻的会过期)。
            # token 过期时前端 PUT /settings/token 换一份即可恢复,无需重录整条流程(治本 401)。
            from dano.infra.token_store import get_token_headers, merge_auth_headers
            api_request = env.body["api_request"]
            override = await get_token_headers(tenant, skill.subsystem.value)
            if override:
                api_request = merge_auth_headers(api_request, override)
            out = await execute_api(api_request, dict(intent.fields),
                                    base_url=base_url, storage_state=storage,
                                    send=True, verify=tls_verify())
            ok = bool(out.get("ok"))
            er = ExecResult(task_id=task_id, outcome=Outcome.PASSED if ok else Outcome.FAILED,
                            evidence=Evidence(request_body=dict(intent.fields),
                                              response_body=out.get("response")),
                            structured_output=out)
            # 不信 HTTP 200:业务码失败(out.business_ok=False)也判 FAILED,把业务原因带出来
            fail_reason = out.get("detail") or out.get("response")
            return TaskOutcome(
                task_id=task_id, state=TaskState.COMPLETED if ok else TaskState.FAILED,
                skill_id=skill.skill_id, exec_result=er,
                message=(f"已提交(HTTP {out.get('status')})" if ok
                         else f"提交未生效(HTTP {out.get('status')}):{fail_reason}"),
                audit={"api": out})

        if self.page_runtime is None:
            return TaskOutcome(task_id=task_id, state=TaskState.TRANSFER_HUMAN,
                               skill_id=skill.skill_id, message="页面运行时未装配")
        script = PageScriptBody.model_validate(env.body)

        # 复用录制时保存的登录态(该子系统有则带上,免运行期被挡登录)
        from dano.execution.page.sessions import session_path_if_exists
        storage = session_path_if_exists(tenant, skill.subsystem.value)
        exec_result = await self.page_runtime.run(
            task_id, script, intent.fields, confirm=lambda f: confirm(skill, f), storage_state=storage
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
