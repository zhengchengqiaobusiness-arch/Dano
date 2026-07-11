"""运行期编排公共类型。"""

from __future__ import annotations

from typing import Any, Literal
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from dano.shared.enums import RiskLevel, Subsystem, TaskState
from dano.shared.models import ExecResult


class Intent(BaseModel):
    """主智能体意图分析结果(LLM 产出)。"""

    kind: Literal["action", "ask"] = "action"
    action_hint: str = Field(description="动作意图描述,如 '创建请假'")
    fields: dict[str, Any] = Field(default_factory=dict, description="从原话抽取的字段值")


class SkillSpec(BaseModel):
    """动作 Skill(1 Skill = 1 action)。从已发布连接器或录制 V2 资产派生。"""

    skill_id: str
    capability: str = ""                                       # 对外能力键;空则兼容退回 skill_id
    capability_meta: dict[str, Any] = Field(default_factory=dict)  # 能力分组/别名/协议草案等扩展元数据
    capabilities: list[dict[str, Any]] = Field(default_factory=list)  # 一个 Skill 内可调用的能力列表
    capability_relations: list[dict[str, Any]] = Field(default_factory=list)  # 能力间建议数据流；调用方显式编排
    subsystem: Subsystem
    action: str
    risk_level: RiskLevel
    title: str = ""                                             # 人类可读标题(阶段4)
    field_docs: dict[str, str] = Field(default_factory=dict)    # 字段→语义描述(阶段4)
    field_types: dict[str, str] = Field(default_factory=dict)   # 字段→JSON 类型(信源 schema;缺则按语义判定)
    call_metadata: dict[str, Any] = Field(default_factory=dict)  # 调用侧元数据(录制/验证状态等,不进 JSON Schema)
    created_at: datetime | None = None                           # 资产产出时间(最新 published 版本)
    lifecycle_state: str = ""                                     # 生命周期状态(异常暂停=冻结)
    frozen: bool = False                                          # 冻结后保留库、不导出/不调用
    field_mappings: list[dict] = Field(default_factory=list)    # 可追溯字段映射(§16:目标点路径+来源 schema_ref)
    goal: dict = Field(default_factory=dict)                    # 结构化业务目标(意图/成功判据/禁止步,§2)
    has_api: bool = True
    connector_asset_id: UUID | None = None   # 有 API
    recording_asset_id: UUID | None = None    # 录制 V2 资产
    api_request: dict = Field(default_factory=dict)        # 参数化后的提交请求/多步工作流(steps/success_rule/fact_check)
    recording_mode: str = ""                               # 录制提交模式:real_submit/intercepted_submit/unknown
    verification_status: str = ""                          # 调用契约验证等级
    verification_basis: str = ""                            # 验证证据来源:fact_check_configured/success_rule_configured/structure_only
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

    business: str = ""
    business_meta: dict = Field(default_factory=dict)           # 业务规则(x-flow)→ 导出剧本的前置/错误/确认段


class CapabilityCallEnvelope(BaseModel):
    """导出脚本/Agent 调用 Dano 的 JSON 调用协议草案。

    兼容旧 function-calling 载荷:旧侧只传 name+arguments;新侧可传 capability+input。
    """

    model_config = ConfigDict(extra="forbid")

    protocol: str = "dano.capability_call.v1"
    name: str | None = None                # 旧工具名,如 A-OA__submit_leave
    capability: str | None = None          # 新能力键;服务端未支持时仍可按 name 兼容执行
    arguments: dict[str, Any] | str | None = None
    input: dict[str, Any] | None = None
    confirm: bool = False
    idempotency_key: str | None = None

    def effective_arguments(self) -> dict[str, Any]:
        """返回对编排器实际可执行的参数对象,input 优先,arguments 兼容。"""
        if self.input is not None:
            return dict(self.input)
        if self.arguments is None:
            return {}
        if isinstance(self.arguments, dict):
            return dict(self.arguments)
        import json as _json
        return _json.loads(self.arguments or "{}")


class TaskOutcome(BaseModel):
    """一次任务终态(流程6 产出)+ 审计。"""

    task_id: UUID
    state: TaskState
    message: str = ""
    skill_id: str | None = None
    exec_result: ExecResult | None = None
    audit: dict[str, Any] = Field(default_factory=dict)
