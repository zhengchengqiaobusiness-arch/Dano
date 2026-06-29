"""运行期编排公共类型。"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from dano.shared.enums import RiskLevel, Subsystem, TaskState
from dano.shared.models import ExecResult


class Intent(BaseModel):
    """主智能体意图分析结果(LLM 产出)。"""

    kind: Literal["action", "ask"] = "action"
    action_hint: str = Field(description="动作意图描述,如 '创建请假'")
    fields: dict[str, Any] = Field(default_factory=dict, description="从原话抽取的字段值")


class SkillSpec(BaseModel):
    """动作 Skill(1 Skill = 1 action)。从已发布连接器(有 API)或页面脚本(无 API)派生。"""

    skill_id: str
    subsystem: Subsystem
    action: str
    risk_level: RiskLevel
    title: str = ""                                             # 人类可读标题(阶段4)
    field_docs: dict[str, str] = Field(default_factory=dict)    # 字段→语义描述(阶段4)
    field_types: dict[str, str] = Field(default_factory=dict)   # 字段→JSON 类型(信源 schema;缺则按语义判定)
    field_mappings: list[dict] = Field(default_factory=list)    # 可追溯字段映射(§16:目标点路径+来源 schema_ref)
    goal: dict = Field(default_factory=dict)                    # 结构化业务目标(意图/成功判据/禁止步,§2)
    has_api: bool = True
    connector_asset_id: UUID | None = None   # 有 API
    page_asset_id: UUID | None = None         # 无 API(页面脚本)
    page_start_url: str = ""                   # 页面脚本:入口页(详情展示)
    page_success_marker: str | None = None     # 页面脚本:成功标志
    page_steps: list[dict] = Field(default_factory=list)   # 页面脚本:动作步骤(PageAction 字典,详情时间线)
    api_request: dict = Field(default_factory=dict)        # 抓请求型页面脚本:参数化后的提交请求/多步工作流(steps/success_rule/fact_check)
    required_fields: list[str] = Field(default_factory=list)   # 必填(缺则拦截)
    optional_fields: list[str] = Field(default_factory=list)   # 可选(契约暴露但不强制)
    keywords: list[str] = Field(default_factory=list)
    fact_check_query: str | None = None   # 事实核查重查哪个动作(查询类无)
    fact_check_expr: str | None = None     # 操作前后比对表达式
    # 复合流程 Skill(阶段2):多步连接器编排成一个业务能力
    is_workflow: bool = False
    workflow_asset_id: UUID | None = None
    workflow_steps: list[dict] = Field(default_factory=list)    # WorkflowStep 字典(DSL v2 节点)
    workflow_success_rule: str | None = None
    workflow_preconditions: list[dict] = Field(default_factory=list)   # DSL v2:办理前不变量
    workflow_invariants: list[dict] = Field(default_factory=list)      # DSL v2:办理后业务正确性不变量
    workflow_preview: bool = False                                     # DSL v2:写前预览待确认(Phase 5 接)

    business: str = ""                                          # 所属业务(同业务多操作 adapter 导出归组)
    business_meta: dict = Field(default_factory=dict)           # 业务规则(x-flow)→ 导出剧本的前置/错误/确认段
    business_description: str = ""                              # P2:业务说明(手填 + AI 优化)→ 导出"业务说明"段 + 剧本叙事

    # 代码适配器(goal 模式生成):调用时由隔离 runner 执行 source
    is_adapter: bool = False
    adapter_asset_id: UUID | None = None
    adapter_source: str = ""
    adapter_entry: str = "run"
    adapter_success_rule: str | None = None
    adapter_fact_check: dict | None = None
    adapter_consts: dict = Field(default_factory=dict)   # 运行期注入的内部常量(如 __templateId__)


class TaskOutcome(BaseModel):
    """一次任务终态(流程6 产出)+ 审计。"""

    task_id: UUID
    state: TaskState
    message: str = ""
    skill_id: str | None = None
    exec_result: ExecResult | None = None
    audit: dict[str, Any] = Field(default_factory=dict)
