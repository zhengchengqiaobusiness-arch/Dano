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
    def __init__(self, coder: Coder) -> None:
        self.coder = coder

    async def run(self, goal: GoalBrief, strategy) -> GenerationResult:  # noqa: ANN001
        from dano.agent_tools import tools as T

        plan = strategy.decompose(goal)                       # 拆解 + 定方案
        feedback: list[str] = []
        iters: list[IterationRecord] = []

        for i in range(goal.budget.max_iters):
            body = await self.coder.generate(plan=plan, feedback=feedback)   # 编码 / 修复
            d = await T.draft_adapter(goal.run_id,
                                      {"system_instance_id": goal.system_instance_id, **body})
            did = d["asset_draft_id"]
            ok, reasons, val_ids, review_ids = await self._gates(T, goal, did)

            if ok:
                pub = await T.publish_asset(goal.run_id, {       # 发布闸门(不可伪造,回 PG 重读)
                    "asset_draft_id": did,
                    "validation_run_ids": val_ids, "review_run_ids": review_ids})
                if pub.get("published"):
                    iters.append(IterationRecord(index=i, passed=True, reasons=[], asset_draft_id=did))
                    log.info("generation.published", flow=goal.flow, iter=i,
                             asset_id=pub["asset_id"], rejections=i)
                    return GenerationResult(ok=True, flow=goal.flow,
                                            asset_id=pub["asset_id"], iterations=iters)
                reasons = [pub.get("reason", "发布失败")]          # 闸门驳回也回灌

            iters.append(IterationRecord(index=i, passed=False, reasons=reasons, asset_draft_id=did))
            feedback = reasons or ["未通过验收"]
            log.info("generation.rejected", flow=goal.flow, iter=i, reasons=feedback)

        log.warning("generation.exhausted", flow=goal.flow, iters=len(iters))
        return GenerationResult(ok=False, flow=goal.flow, asset_id=None,
                                iterations=iters, reason="耗尽预算仍未通过")

    @staticmethod
    async def _gates(T, goal: GoalBrief, did: str) -> tuple[bool, list[str], list[str], list[str]]:  # noqa: ANN001
        """顺序过闸:测试 → 漏洞校验 → 审核。任一失败即短路返回 reasons(不再往下)。

        返回 (是否全过, 驳回原因, 已通过的 validation_run_ids, review_run_ids)。
        """
        # ① 测试(隔离 runner + 成败规则)
        test = await T.sandbox_test_adapter(
            goal.run_id, {"asset_draft_id": did, "test_input": goal.test_input})
        if not test["passed"]:
            return False, test.get("reasons") or ["未通过沙箱测试"], [], []
        val_ids = list(test["validation_run_ids"])

        # ② 漏洞校验(静态扫描)
        vuln = await T.vuln_scan(goal.run_id, {"asset_draft_id": did})
        val_ids += vuln["validation_run_ids"]
        if not vuln["passed"]:
            return False, [f"漏洞校验未过: {x}" for x in vuln["findings"]], val_ids, []

        # ③ 审核(三模型:成果验收 / 漏洞检测 / 合规审核)
        rev = await T.request_review(goal.run_id, {"asset_draft_id": did})
        if not rev["all_passed"]:
            bad = [f"{v['role']}({v['model']})驳回: {v['reasons']}"
                   for v in rev.get("verdicts", []) if not v["passed"]]
            return False, bad or ["三模型评审未通过"], val_ids, rev.get("review_run_ids", [])
        return True, [], val_ids, rev["review_run_ids"]
