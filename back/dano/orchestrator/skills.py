"""Skill 注册:从已发布连接器资产派生动作 Skill,并支持意图匹配。

Skill = Action 强约束(文档6.1节六):每个动作 Skill 有且仅有一个 action。
运行期只消费 published 连接器(命中消费)。事实核查策略(重查哪个动作 + 比对表达式)
**优先随连接器资产体走**(接入期 grounded 写入 fact_check_query/expr);ACTION_META 仅作
A 公司原型 demo 的兜底增强,不再是通用连接器的唯一来源(否则只有 5 个 demo 动作有事实核查)。
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from dano.assets.store import AssetStore
from dano.execution.connectors.executor import system_key_for
from dano.orchestrator.types import Intent, SkillSpec
from dano.shared.enums import AssetType, RiskLevel, Subsystem
from dano.shared.models import Scope

log = structlog.get_logger(__name__)


class ActionMeta(BaseModel):
    keywords: list[str]
    fact_check_query: str | None = None
    fact_check_expr: str | None = None
    required_fields: list[str] = Field(default_factory=list)


# 动作元数据(关键词用于意图匹配,事实核查用于流程9 重查比对)
ACTION_META: dict[str, ActionMeta] = {
    "query_balance": ActionMeta(keywords=["余额", "假期余额", "还有几天假", "balance"]),
    "create_leave": ActionMeta(
        keywords=["请假", "休假", "请个假", "leave"],
        fact_check_query="query_balance",
        # 重查余额按申请天数减少 + 返回单号非空
        fact_check_expr="after.balance == before.balance - fields.days and response.request_id != null",
    ),
    "query_approval": ActionMeta(keywords=["审批状态", "审批进度", "批了吗", "approval"]),
    "create_ticket": ActionMeta(
        keywords=["工单", "报修", "IT工单", "ticket"],
        fact_check_query="query_ticket",
        fact_check_expr="response.ticket_id != null",
    ),
    "query_ticket": ActionMeta(keywords=["工单进度", "工单状态", "ticket status"]),
    # 无 API 报销(页面辅助,流程8)
    "create_reimburse_draft": ActionMeta(
        keywords=["报销", "报销草稿", "贴发票", "reimburse"],
        fact_check_expr="response.draft_id != null and response.amount == fields.amount",
        required_fields=["amount", "category"],
    ),
}

# 无 API 子系统的页面动作(一个无 API 系统当前一个页面动作)
_PAGE_ACTION_BY_SUBSYSTEM: dict[Subsystem, str] = {
    Subsystem.REIMBURSE: "create_reimburse_draft",
}


class SkillRegistry:
    def __init__(self, skills: list[SkillSpec]) -> None:
        self.skills = skills

    @classmethod
    async def from_store(
        cls, store: AssetStore, *, tenant: str, subsystems: list[Subsystem]
    ) -> SkillRegistry:
        from dano.shared.asset_bodies import WorkflowSkillBody, asset_internal

        skills: list[SkillSpec] = []
        for sub in subsystems:
            scope = Scope(tenant=tenant, subsystem=sub)
            # 复合流程 Skill(阶段2):从已发布 WORKFLOW 资产派生;其步骤动作隐藏(不单独暴露)
            hidden_actions: set[str] = set()
            for env in await store.list_published(AssetType.WORKFLOW, scope):
                body = WorkflowSkillBody.model_validate(env.body)
                req = list(body.required_fields)
                opt = [f for f in body.user_fields if f not in req]
                skills.append(
                    SkillSpec(
                        skill_id=f"{sub.value}.{body.action}",
                        subsystem=sub,
                        action=body.action,
                        risk_level=body.risk_level,
                        title=body.title,
                        field_docs=dict(body.field_docs),
                        field_types=dict(getattr(body, "field_types", {}) or {}),
                        field_mappings=list(getattr(body, "field_mappings", []) or []),
                        goal=dict(getattr(body, "goal", {}) or {}),
                        business=getattr(body, "business", "") or "",
                        business_meta=dict(getattr(body, "business_meta", {}) or {}),
                        has_api=True,
                        is_workflow=True,
                        workflow_asset_id=env.asset_id,
                        workflow_steps=[s.model_dump() for s in body.steps],
                        workflow_success_rule=body.success_rule,
                        workflow_preconditions=[i.model_dump() for i in body.preconditions],
                        workflow_invariants=[i.model_dump() for i in body.invariants],
                        workflow_preview=body.preview,
                        required_fields=req,
                        optional_fields=opt,
                        keywords=[w for w in (body.action, body.title) if w],
                    )
                )
                hidden_actions.update(s.action for s in body.steps)
            # 有 API:从已发布连接器派生(被复合流程消费的步骤动作隐藏)
            for env in await store.list_published(AssetType.CONNECTOR, scope):
                action = env.body.get("action", env.asset_key)
                # 步骤连接器 / internal 前置查询:永不单独露出(即便其复合流程未发布也不污染目录)
                if action in hidden_actions or asset_internal(env.body):
                    continue
                meta = ACTION_META.get(action, ActionMeta(keywords=[action]))
                # 事实核查优先取**资产体**(随资产走,接入期可 grounded 写入);ACTION_META 仅作原型 demo 兜底
                fc_query = env.body.get("fact_check_query") or meta.fact_check_query
                fc_expr = env.body.get("fact_check_expr") or meta.fact_check_expr
                bindings = env.body.get("field_bindings", [])
                # 必填/可选按连接器绑定的 required 拆分(缺省 True,兼容旧资产)
                req = [b["platform_std"] for b in bindings if b.get("required", True)]
                opt = [b["platform_std"] for b in bindings if not b.get("required", True)]
                skills.append(
                    SkillSpec(
                        skill_id=f"{sub.value}.{action}",
                        subsystem=sub,
                        action=action,
                        risk_level=RiskLevel(env.body.get("risk_level", "L1")),
                        title=env.body.get("title", ""),
                        business=env.body.get("business", "") or "",
                        field_docs=dict(env.body.get("field_docs", {})),
                        field_types=dict(env.body.get("field_types", {}) or {}),
                        has_api=True,
                        connector_asset_id=env.asset_id,
                        required_fields=req,
                        optional_fields=opt,
                        keywords=meta.keywords,
                        fact_check_query=fc_query,
                        fact_check_expr=fc_expr,
                    )
                )
            # 代码适配器(goal 模式生成):从已发布 ADAPTER 资产派生;调用时隔离 runner 执行 source
            for env in await store.list_published(AssetType.ADAPTER, scope):
                b = env.body
                action = b.get("action", env.asset_key)
                req = list(b.get("required_fields", []))
                opt = [f for f in b.get("user_fields", []) if f not in req]
                skills.append(
                    SkillSpec(
                        skill_id=f"{sub.value}.{action}",
                        subsystem=sub,
                        action=action,
                        risk_level=RiskLevel(b.get("risk_level", "L3")),
                        title=b.get("title", ""),
                        business=b.get("business", ""),
                        business_meta=dict(b.get("business_meta", {})),
                        field_docs=dict(b.get("field_docs", {})),
                        field_types=dict(b.get("field_types", {}) or {}),
                        has_api=True,
                        is_adapter=True,
                        adapter_asset_id=env.asset_id,
                        adapter_source=b.get("source", ""),
                        adapter_entry=b.get("entry", "run"),
                        adapter_success_rule=b.get("success_rule"),
                        adapter_fact_check=b.get("fact_check"),
                        adapter_consts=dict(b.get("consts", {})),
                        required_fields=req,
                        optional_fields=opt,
                        keywords=[w for w in (action, b.get("title", "")) if w],
                    )
                )
            # 无 API:从已发布页面脚本派生(流程8)
            for env in await store.list_published(AssetType.PAGE_SCRIPT, scope):
                body = env.body or {}
                body_action = (body.get("action") or "").strip()
                if body_action:
                    # 加厚后的页面脚本:动作/字段/风险/标题来自资产体本身
                    req = list(body.get("required_fields") or [])
                    user = list(body.get("user_fields") or [])
                    opt = [f for f in (user + list(body.get("optional_fields") or []))
                           if f not in req]
                    opt = list(dict.fromkeys(opt))
                    skills.append(
                        SkillSpec(
                            skill_id=f"{sub.value}.{body_action}",
                            subsystem=sub,
                            action=body_action,
                            risk_level=RiskLevel(body.get("risk_level", "L3")),
                            title=body.get("title", ""),
                            field_docs=dict(body.get("field_docs", {})),
                            field_types=dict(body.get("field_types", {}) or {}),
                            has_api=False,
                            page_asset_id=env.asset_id,
                            page_start_url=body.get("start_url", ""),
                            page_success_marker=body.get("success_marker"),
                            page_steps=list(body.get("actions") or []),
                            required_fields=req,
                            optional_fields=opt,
                            keywords=[w for w in (body_action, body.get("title", "")) if w],
                        )
                    )
                else:
                    # 旧页面脚本(仅 actions+dom_fingerprint,无 action 字段):原型子系统沿用已知映射;
                    # 任意其它系统派生中性动作名 `submit_<系统key>`,**不臆造**成报销动作。
                    action = _PAGE_ACTION_BY_SUBSYSTEM.get(sub) or f"submit_{system_key_for(sub)}"
                    meta = ACTION_META.get(action, ActionMeta(keywords=[action]))
                    skills.append(
                        SkillSpec(
                            skill_id=f"{sub.value}.{action}",
                            subsystem=sub,
                            action=action,
                            risk_level=RiskLevel.L2,   # 报销草稿 L2
                            has_api=False,
                            page_asset_id=env.asset_id,
                            required_fields=meta.required_fields,
                            keywords=meta.keywords,
                            fact_check_expr=meta.fact_check_expr,
                        )
                    )
        log.info("skills.registered", count=len(skills), skills=[s.skill_id for s in skills])
        return cls(skills)

    def match(self, intent: Intent) -> SkillSpec | None:
        """按关键词命中动作 Skill。命中关键词最多者优先。"""
        text = intent.action_hint.lower()
        best: SkillSpec | None = None
        best_score = 0
        for s in self.skills:
            score = sum(1 for kw in s.keywords if kw.lower() in text)
            if score > best_score:
                best, best_score = s, score
        return best if best_score > 0 else None

    def by_action(self, subsystem: Subsystem, action: str) -> SkillSpec | None:
        return next(
            (s for s in self.skills if s.subsystem == subsystem and s.action == action), None
        )
