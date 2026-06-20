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
    has_api: bool = True
    connector_asset_id: UUID | None = None   # 有 API
    page_asset_id: UUID | None = None         # 无 API(页面脚本)
    required_fields: list[str] = Field(default_factory=list)   # 必填(缺则拦截)
    optional_fields: list[str] = Field(default_factory=list)   # 可选(契约暴露但不强制)
    keywords: list[str] = Field(default_factory=list)
    fact_check_query: str | None = None   # 事实核查重查哪个动作(查询类无)
    fact_check_expr: str | None = None     # 操作前后比对表达式
    # 复合流程 Skill(阶段2):多步连接器编排成一个业务能力
    is_workflow: bool = False
    workflow_asset_id: UUID | None = None
    workflow_steps: list[dict] = Field(default_factory=list)    # WorkflowStep 字典
    workflow_success_rule: str | None = None

    business: str = ""                                          # 所属业务(同业务多操作 adapter 导出归组)
    business_meta: dict = Field(default_factory=dict)           # 业务规则(x-flow)→ 导出剧本的前置/错误/确认段

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
