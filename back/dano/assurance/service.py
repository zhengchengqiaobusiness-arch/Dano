"""阶段三 保障期:失败处理/熔断(流程10)+ 漂移自愈(流程11,pi 驱动)+ 生命周期(流程12)。

- report_failure:收标准失败事件 → 分类 → 计数 → 达阈值暂停 Skill;漂移/字段变更建议自愈。
- self_heal:pi 重新生成受影响连接器(新版本,旧版保留可回滚)→ 恢复 Skill 到「已发布」。
失败来源不限(瘦 /invoke / 前端编排 / 客户 Worker / 定时指纹 / 人工),统一标准事件接入。
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from dano.resilience import classifier
from dano.resilience.circuit_breaker import CounterStore, InMemoryCounter
from dano.lifecycle.state_machine import SkillLifecycle
from dano.shared.enums import FailureClass, RecoveryAction, SkillState, Subsystem

log = structlog.get_logger(__name__)

# 失败事件 failure_type → FailureClass
_FT_MAP = {
    "field_changed": FailureClass.PAGE_FIELD, "page_changed": FailureClass.PAGE_FIELD,
    "schema_changed": FailureClass.PAGE_FIELD, "login": FailureClass.LOGIN,
    "network": FailureClass.NETWORK, "permission": FailureClass.PERMISSION,
    "param": FailureClass.PARAM, "config": FailureClass.CONFIG, "system": FailureClass.SYSTEM,
}


class FailureEvent(BaseModel):
    tenant_id: str
    system_instance_id: str = ""
    skill_id: str
    skill_version: str = ""
    failure_type: str
    execution_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    occurred_at: str = ""


class FailureDecision(BaseModel):
    skill_id: str
    recovery_action: str
    failure_count: int
    suspended: bool
    self_heal_recommended: bool


async def report_failure(event: FailureEvent, *, lifecycle: SkillLifecycle,
                         breaker: CounterStore | None = None, threshold: int = 3) -> FailureDecision:
    breaker = breaker or InMemoryCounter()
    fc = _FT_MAP.get(event.failure_type, FailureClass.SYSTEM)
    action = classifier.classify(fc)
    count = await breaker.incr(f"fail:{event.skill_id}")
    suspended = False
    if count >= threshold:
        await lifecycle.suspend(event.skill_id)          # 达阈值 → 异常暂停(流程10→12)
        suspended = True
    log.info("assurance.report_failure", skill=event.skill_id, fc=fc.value,
             action=action.value, count=count, suspended=suspended)
    return FailureDecision(
        skill_id=event.skill_id, recovery_action=action.value, failure_count=count,
        suspended=suspended, self_heal_recommended=(action == RecoveryAction.REGENERATE))


async def self_heal(*, tenant: str, subsystem: str, openapi: dict, deploy: dict,
                    credentials: dict, lifecycle: SkillLifecycle,
                    actions: list[str] | None = None, incremental: bool = True,
                    timeout_s: float = 240.0):
    """漂移自愈(流程11)。

    incremental=True(默认):**只重生成受影响动作**(显式 actions,否则取当前暂停的 Skill),
    确定性 draft→sandbox→三模型评审→publish 出新版本(旧版 append-only 保留可回滚)→恢复到「已发布」。
    incremental=False:回退全量重跑 onboard(pi 重生成全部),用于结构大改/不确定影响面。
    """
    if not incremental:
        return await _full_reheal(tenant=tenant, subsystem=subsystem, openapi=openapi,
                                  deploy=deploy, credentials=credentials, lifecycle=lifecycle,
                                  timeout_s=timeout_s)

    from uuid import uuid4

    from dano.agent_tools import materials
    sid = subsystem
    run_id = f"heal-{uuid4().hex[:8]}"
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant=tenant, system_instance_id=sid, subsystem=subsystem,
        openapi=openapi, deploy=deploy, credentials=credentials))
    try:
        targets = actions
        if targets is None:        # 未指定 → 受影响 = 当前该子系统暂停的 Skill
            recs = await lifecycle.store.all()
            targets = [r.action for r in recs
                       if r.state == SkillState.SUSPENDED and r.subsystem.value == subsystem]
        recovered, failed = [], []
        for action in targets:
            try:
                # 页面型(有已发布 PAGE_SCRIPT)→ 重新侦察补丁;否则连接器重生成
                if await _is_page_action(tenant, subsystem, action):
                    ok = await _reheal_page(run_id, sid, subsystem, action, tenant, lifecycle)
                else:
                    ok = await _reheal_connector(run_id, sid, subsystem, action, lifecycle)
            except Exception as e:  # noqa: BLE001
                log.warning("self_heal.action_failed", action=action, error=str(e))
                ok = False
            (recovered if ok else failed).append(f"{subsystem}.{action}")
        log.info("assurance.self_heal", tenant=tenant, mode="incremental",
                 recovered=recovered, failed=failed)
        return {"status": "completed", "mode": "incremental",
                "recovered": recovered, "failed": failed}
    finally:
        materials.clear_run(run_id)


async def _reheal_connector(run_id: str, sid: str, subsystem: str, action: str,
                            lifecycle: SkillLifecycle) -> bool:
    """重生成单个连接器(新版本)并恢复其 Skill。沙箱/评审/发布闸门一道不少。"""
    from dano.agent_tools import tools as T
    d = await T.draft_connector(run_id, {"system_instance_id": sid, "action": action})
    st = await T.sandbox_test(run_id, {"asset_draft_id": d["asset_draft_id"]})
    if not (st.get("connect_passed") and st.get("sandbox_passed")):
        return False
    rev = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
    if not rev.get("all_passed"):
        return False
    pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
        "validation_run_ids": st["validation_run_ids"], "review_run_ids": rev["review_run_ids"]})
    if not pub.get("published"):
        return False
    skill_id = f"{subsystem}.{action}"
    rec = await lifecycle.store.get(skill_id)
    if rec and rec.state == SkillState.SUSPENDED:        # 暂停态 → 恢复到已发布(新版本)
        await lifecycle.recover_to_published(skill_id, (rec.asset_version or 1) + 1)
    return True


async def _is_page_action(tenant: str, subsystem: str, action: str) -> bool:
    """该动作是否页面型(有已发布 PAGE_SCRIPT 资产)。决定走页面自愈还是连接器自愈。"""
    from dano.assets.repository import AssetRepository
    from dano.shared.enums import AssetType
    from dano.shared.models import Scope
    env = await AssetRepository().get_published(
        AssetType.PAGE_SCRIPT, Scope(tenant=tenant, subsystem=Subsystem(subsystem)), asset_key=action)
    return env is not None


def page_heal_strategy(body: dict) -> str:
    """页面漂移自愈策略路由(纯函数):

    - "agent":页面直驱(operate-page)结晶的 skill(资产带 goal.intent)→ Agent **自主重驱动**重结晶
      (页面真改版时,一次性侦察拼不回多步轨迹,得让 Agent 凭目标重新自己跑通)。
    - "scout":老的 onboard-page / 无目标页面脚本 → 重新侦察补丁(单表单足够)。
    """
    if (body.get("goal") or {}).get("intent"):
        return "agent"
    return "scout"


async def _reheal_page(run_id: str, sid: str, subsystem: str, action: str,
                       tenant: str, lifecycle: SkillLifecycle) -> bool:
    """页面漂移自愈(流程11 页面版):按策略分流——

    带 goal 的页面直驱 skill → `_reheal_page_agent`(Agent 自主重驱动);否则 **重新侦察**补丁。
    两条都走"发布新版本(旧版保留可回滚)→ 恢复 Skill 到已发布"的同一闸门。
    """
    from dano.agent_tools import tools as T
    from dano.assets.repository import AssetRepository
    from dano.shared.enums import AssetType
    from dano.shared.models import Scope
    env = await AssetRepository().get_published(
        AssetType.PAGE_SCRIPT, Scope(tenant=tenant, subsystem=Subsystem(subsystem)), asset_key=action)
    if env is None:
        return False
    body = env.body
    if page_heal_strategy(body) == "agent":              # 页面直驱:Agent 自主重驱动重结晶
        return await _reheal_page_agent(subsystem, action, tenant, body, lifecycle)
    start_url = body.get("start_url", "")
    sc = await T.scout_page(run_id, {"system_instance_id": sid, "start_url": start_url})
    if not sc.get("suggested_steps"):
        return False
    d = await T.draft_page_script(run_id, {
        "system_instance_id": sid, "action": action, "steps": sc["suggested_steps"],
        "dom_fingerprint": sc["dom_fingerprint"], "start_url": start_url,
        "success_marker": body.get("success_marker"), "title": body.get("title", "")})
    rp = await T.sandbox_replay(run_id, {"asset_draft_id": d["asset_draft_id"]})
    if not rp.get("passed"):
        return False
    review_ids: list[str] = []
    if d.get("needs_review"):
        rev = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
        if not rev.get("all_passed"):
            return False
        review_ids = rev["review_run_ids"]
    pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
        "validation_run_ids": rp["validation_run_ids"], "review_run_ids": review_ids})
    if not pub.get("published"):
        return False
    skill_id = f"{subsystem}.{action}"
    rec = await lifecycle.store.get(skill_id)
    if rec and rec.state == SkillState.SUSPENDED:
        await lifecycle.recover_to_published(skill_id, (rec.asset_version or 1) + 1)
    return True


async def _reheal_page_agent(subsystem: str, action: str, tenant: str, body: dict,
                             lifecycle: SkillLifecycle) -> bool:
    """页面直驱自愈:Agent 凭原 goal **自主重驱动**真实页面 → 重结晶新版本(operate-page 全套闸门:
    回放 + 三模型评审 + 发布硬闸门)→ 旧版 append-only 保留可回滚 → 恢复 Skill 到「已发布」。

    复用 M2 的 `run_page_agent_onboarding`(spawn pi operate-page);权威结果 = PG 新发布的 PAGE_SCRIPT。
    需 Node + pi LLM + 浏览器 + 测试登录态;不可用/重驱动失败 → False(旧版不动,安全)。
    """
    from dano.onboarding.page_onboard import run_page_agent_onboarding
    goal = (body.get("goal") or {}).get("intent", "")
    start_url = body.get("start_url", "")
    if not (goal and start_url):
        return False
    report = await run_page_agent_onboarding(
        tenant=tenant, subsystem=subsystem, start_url=start_url, goal=goal)
    if action not in (report.get("published_skills") or []):     # 重驱动没把这条业务重新跑通发布 → 自愈失败
        return False
    skill_id = f"{subsystem}.{action}"
    rec = await lifecycle.store.get(skill_id)
    if rec and rec.state == SkillState.SUSPENDED:                 # 灰度:新版发布通过后才把 Skill 恢复到已发布
        await lifecycle.recover_to_published(skill_id, (rec.asset_version or 1) + 1)
    log.info("assurance.reheal_page_agent", subsystem=subsystem, action=action, goal=goal[:60])
    return True


async def _full_reheal(*, tenant: str, subsystem: str, openapi: dict, deploy: dict,
                       credentials: dict, lifecycle: SkillLifecycle, timeout_s: float):
    """全量回退:pi 重新生成全部连接器(新版本)→ 恢复所有暂停的 Skill。"""
    from dano.onboarding import onboard
    report = await onboard(tenant=tenant, subsystem=subsystem, system_instance_id=subsystem,
                           openapi=openapi, deploy=deploy, credentials=credentials,
                           lifecycle=lifecycle, timeout_s=timeout_s)
    recovered: list[str] = []
    for action in report.published_skills:
        skill_id = f"{subsystem}.{action}"
        rec = await lifecycle.store.get(skill_id)
        if rec and rec.state == SkillState.SUSPENDED:
            await lifecycle.recover_to_published(skill_id, (rec.asset_version or 1) + 1)
            recovered.append(skill_id)
    log.info("assurance.self_heal", tenant=tenant, mode="full",
             recovered=recovered, status=report.status)
    return {"status": report.status, "mode": "full",
            "republished": report.published_skills, "recovered": recovered}
