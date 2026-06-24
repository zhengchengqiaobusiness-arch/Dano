"""GenerationLoop(不变层):goal 模式代码生成的迭代闭环 + 闸门。

一轮 = 编码(coder)→ 测试(sandbox_test_adapter,隔离 runner + success_rule)→
  漏洞校验(vuln_scan,静态扫描)→ 审核(request_review,三模型:验收/安全/合规)→
  全过则发布(publish_asset 不可伪造闸门);任一关 fail 则把 reasons 回灌下一轮(驳回重写)。
有界预算,耗尽即失败——**不存在一次生成直接发布**。

M2 范围:编码→测试→漏洞校验→审核→发布;事实核查一等公民(M3)在此循环里加挂。
"""

from __future__ import annotations

import structlog

from dano.generation.artifacts import GenerationResult, GoalBrief, IterationRecord
from dano.generation.coder import Coder

log = structlog.get_logger(__name__)


class GenerationLoop:
    def __init__(self, coder: Coder, *, planner=None, lifecycle=None, on_event=None,  # noqa: ANN001
                 max_plan_attempts: int = 2) -> None:
        self.coder = coder
        self.planner = planner                                # 给定则用 LLM 拆解 + 方案错时重拆(v2-M3)
        self.max_plan_attempts = max_plan_attempts            # 重拆方案次数上限(有界,防不收敛)
        self.lifecycle = lifecycle                            # 给定则发布后登记到生命周期(已发布)
        self.on_event = on_event                              # 进度回调(dict);用于接入向导实时进度

    async def _initial_plan(self, goal: GoalBrief, strategy):  # noqa: ANN001
        """有 planner 走 LLM 拆解(失败安全回退确定性 decompose);否则用策略 decompose。"""
        if self.planner is not None:
            try:
                return await self.planner.plan(goal, strategy)
            except Exception as e:  # noqa: BLE001 - 方案不合规/LLM 失败 → 回退确定性策略,不崩
                log.warning("generation.plan_fallback", flow=goal.flow, error=str(e))
        plan = strategy.decompose(goal)
        if not plan.evidence and goal.evidence:   # 兜底也带上已采集的证据,coder 才有 grounding(不致瞎写)
            plan.evidence = dict(goal.evidence)
        return plan

    def _emit(self, **ev) -> None:                            # noqa: ANN003
        if self.on_event is not None:
            try:
                self.on_event(ev)
            except Exception:  # noqa: BLE001 - 进度回调不应拖垮生成
                pass

    async def run(self, goal: GoalBrief, strategy) -> GenerationResult:  # noqa: ANN001
        from dano.agent_tools import materials, tools as T

        mat = materials.get(goal.run_id, goal.system_instance_id)
        plan = await self._initial_plan(goal, strategy)        # 拆解 + 定方案(LLM 或确定性)
        ov = getattr(goal, "plan_overrides", None)
        if ov:                                                 # 契约合成:grounded 覆盖 LLM 猜测(成败判定 + 字段映射)
            from dano.shared.asset_bodies import FactCheckSpec
            if "success_rule" in ov:
                plan.success_rule = ov["success_rule"]
            if "fact_check" in ov:
                plan.fact_check = FactCheckSpec.model_validate(ov["fact_check"]) if ov["fact_check"] else None
            if ov.get("user_fields"):
                plan.user_fields = list(ov["user_fields"])
            if ov.get("required_fields"):
                plan.required_fields = list(ov["required_fields"])
            if ov.get("field_docs"):
                plan.field_docs = dict(ov["field_docs"])
            if ov.get("field_types"):
                plan.field_types = dict(ov["field_types"])   # 信源类型直通,契约层不再靠关键词猜数值
            log.info("gen.contract_override", flow=goal.flow, success_rule=plan.success_rule,
                     fact_check=bool(plan.fact_check), user_fields=plan.user_fields)
        log.info("gen.start", flow=goal.flow, business=getattr(goal, "business", ""),
                 strategy=plan.strategy, max_iters=goal.budget.max_iters,
                 planner=bool(self.planner), endpoints=len((goal.evidence or {}).get("actions", [])),
                 plan_steps=len(plan.steps or []), success_rule=plan.success_rule)
        feedback: list[str] = []
        plan_attempts = 0
        iters: list[IterationRecord] = []
        result: GenerationResult | None = None

        for i in range(goal.budget.max_iters):
            log.info("gen.iter", flow=goal.flow, iter=i, strategy=plan.strategy, fixing=bool(feedback))
            self._emit(type="coding", flow=goal.flow, iter=i, strategy=plan.strategy,
                       fixing=bool(feedback))
            body = await self.coder.generate(plan=plan, feedback=feedback)   # 编码 / 修复
            if body.get("_no_source"):       # 模型没返回内容(API Key 失效/限流/模型名错):重写无用,立刻停,报真因
                reason = ("模型未返回内容:可能 API Key 失效 / 触发限流 / 模型名错——"
                          "请到「运行配置」检查 Key 与模型名(已停止重试,避免空烧预算)")
                log.warning("gen.coder_unavailable", flow=goal.flow, iter=i)
                self._emit(type="rejected", flow=goal.flow, iter=i, reasons=[reason])
                iters.append(IterationRecord(index=i, passed=False, reasons=[reason]))
                result = GenerationResult(ok=False, flow=goal.flow, asset_id=None,
                                          iterations=iters, reason=reason)
                break
            if getattr(goal, "business", ""):                 # 展开模式:打业务标签,供导出归组成剧本 skill
                body.setdefault("business", goal.business)
            if getattr(goal, "title", "") and not body.get("title"):   # 中文标题,供目录/剧本展示
                body["title"] = goal.title
            # 风险等级随真实方法:全 GET 只读 → L1(否则三模型会以"GET 只读应 L1"反复驳回,读操作永远上不了架)
            if goal.actions and all((a.get("method") or "GET").upper() == "GET" for a in goal.actions):
                body["risk_level"] = "L1"
            src = body.get("source", "") or ""
            self._emit(type="coded", flow=goal.flow, iter=i,
                       lines=src.count("\n") + 1 if src else 0)
            d = await T.draft_adapter(goal.run_id,
                                      {"system_instance_id": goal.system_instance_id, **body})
            did = d["asset_draft_id"]
            ok, reasons, val_ids, review_ids, kind = await self._gates(T, goal, did, i)

            if ok:
                pub = await T.publish_asset(goal.run_id, {       # 发布闸门(不可伪造,回 PG 重读)
                    "asset_draft_id": did,
                    "validation_run_ids": val_ids, "review_run_ids": review_ids})
                if pub.get("published"):
                    iters.append(IterationRecord(index=i, passed=True, reasons=[], asset_draft_id=did))
                    log.info("generation.published", flow=goal.flow, iter=i,
                             asset_id=pub["asset_id"], rejections=i)
                    self._emit(type="published", flow=goal.flow, iter=i, asset_id=pub["asset_id"])
                    if self.lifecycle is not None and mat is not None:   # 登记到生命周期(已发布)
                        from dano.shared.enums import Subsystem
                        await self.lifecycle.register_published(
                            f"{mat.subsystem}.{goal.flow}", Subsystem(mat.subsystem),
                            goal.flow, pub.get("version", 1))
                    result = GenerationResult(ok=True, flow=goal.flow,
                                              asset_id=pub["asset_id"], iterations=iters)
                    break
                reasons = [pub.get("reason", "发布失败")]          # 闸门驳回也回灌

            iters.append(IterationRecord(index=i, passed=False, reasons=reasons, asset_draft_id=did))
            # 双层回灌:方案被事实核查证伪(kind=plan)且有重拆预算 → 重拆方案;否则回灌重写代码
            if kind == "plan" and self.planner is not None and plan_attempts < self.max_plan_attempts:
                plan_attempts += 1
                log.info("generation.replanned", flow=goal.flow, iter=i, attempt=plan_attempts)
                self._emit(type="replanned", flow=goal.flow, iter=i, attempt=plan_attempts, reasons=reasons)
                try:
                    plan = await self.planner.replan(goal, strategy, plan, reasons)
                    feedback = []                              # 新方案 → 代码反馈清零
                except Exception as e:  # noqa: BLE001 - 重拆失败 → 退回重写代码
                    log.warning("generation.replan_failed", flow=goal.flow, error=str(e))
                    feedback = reasons or ["未通过验收"]
            else:
                feedback = reasons or ["未通过验收"]
                log.info("generation.rejected", flow=goal.flow, iter=i, reasons=feedback)
                self._emit(type="rejected", flow=goal.flow, iter=i, reasons=feedback)

        if result is None:
            log.warning("generation.exhausted", flow=goal.flow, iters=len(iters))
            self._emit(type="exhausted", flow=goal.flow)
            result = GenerationResult(ok=False, flow=goal.flow, asset_id=None,
                                      iterations=iters, reason="耗尽预算仍未通过")
        await self._persist(goal, strategy, result)            # 可追溯(尽力而为)
        return result

    @staticmethod
    async def _persist(goal: GoalBrief, strategy, result: GenerationResult) -> None:  # noqa: ANN001
        """落 generation_runs(审计自动生成);取不到租户作用域则跳过,绝不拖垮生成。"""
        from dano.agent_tools import materials
        from dano.generation.store import save_generation_run
        mat = materials.get(goal.run_id, goal.system_instance_id)
        if mat is None:
            return
        await save_generation_run(result, run_id=goal.run_id, tenant=mat.tenant,
                                  subsystem=mat.subsystem, strategy=getattr(strategy, "name", None))

    async def _gates(self, T, goal: GoalBrief, did: str, i: int = 0) -> tuple[bool, list[str], list[str], list[str], str]:  # noqa: ANN001
        """顺序过闸:测试 → 漏洞校验 → 审核。任一失败即短路返回 reasons(不再往下)。

        返回 (是否全过, 驳回原因, validation_run_ids, review_run_ids, 失败类型)。
        失败类型 kind:`plan`=代码能跑但**事实核查证伪**(疑似空操作→拆解/契约错,该重拆方案);
        其余=`code`(运行异常/成败规则/漏洞/评审/发布→重写代码即可)。
        """
        # ① 测试(隔离 runner + 成败规则 + 事实核查)
        self._emit(type="testing", flow=goal.flow, iter=i)
        test = await T.sandbox_test_adapter(
            goal.run_id, {"asset_draft_id": did, "test_input": goal.test_input})
        if not test["passed"]:
            reasons = test.get("reasons") or ["未通过沙箱测试"]
            kind = "plan" if any("事实核查未过" in r for r in reasons) else "code"
            log.warning("gen.gate.sandbox", flow=goal.flow, iter=i, passed=False, kind=kind,
                        reasons=reasons, output=str(test.get("output"))[:300])
            self._emit(type="gate", flow=goal.flow, iter=i, gate="沙箱真跑",
                       passed=False, detail="; ".join(reasons))
            return False, reasons, [], [], kind
        log.info("gen.gate.sandbox", flow=goal.flow, iter=i, passed=True)
        self._emit(type="gate", flow=goal.flow, iter=i, gate="沙箱真跑", passed=True)
        val_ids = list(test["validation_run_ids"])

        # ② 漏洞校验(静态扫描)
        vuln = await T.vuln_scan(goal.run_id, {"asset_draft_id": did})
        val_ids += vuln["validation_run_ids"]
        if not vuln["passed"]:
            log.warning("gen.gate.vuln", flow=goal.flow, iter=i, passed=False, findings=vuln["findings"])
            self._emit(type="gate", flow=goal.flow, iter=i, gate="漏洞校验",
                       passed=False, detail="; ".join(vuln["findings"]))
            return False, [f"漏洞校验未过: {x}" for x in vuln["findings"]], val_ids, [], "code"
        log.info("gen.gate.vuln", flow=goal.flow, iter=i, passed=True)
        self._emit(type="gate", flow=goal.flow, iter=i, gate="漏洞校验", passed=True)

        # ③ 审核(三模型:成果验收 / 漏洞检测 / 合规审核)
        self._emit(type="reviewing", flow=goal.flow, iter=i)
        rev = await T.request_review(goal.run_id, {"asset_draft_id": did})
        for v in rev.get("verdicts", []):
            (log.info if v["passed"] else log.warning)(
                "gen.gate.review", flow=goal.flow, iter=i, role=v["role"], model=v["model"],
                passed=v["passed"], reasons=v.get("reasons") or [])
            self._emit(type="verdict", flow=goal.flow, iter=i, role=v["role"], model=v["model"],
                       passed=v["passed"], detail="" if v["passed"] else "; ".join(v.get("reasons") or []))
        if not rev["all_passed"]:
            bad = [f"{v['role']}({v['model']})驳回: {v['reasons']}"
                   for v in rev.get("verdicts", []) if not v["passed"]]
            return False, bad or ["三模型评审未通过"], val_ids, rev.get("review_run_ids", []), "code"
        log.info("gen.gate.review", flow=goal.flow, iter=i, passed=True, all_passed=True)
        return True, [], val_ids, rev["review_run_ids"], "code"
