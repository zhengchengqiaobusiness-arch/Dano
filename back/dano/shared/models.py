"""跨模块公共模型:资产信封、任务简报、执行结果、生成报告。

这些是模块间的接口契约。改它们 = 改合同,需谨慎。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from dano.shared.asset_bodies import Assertions
from dano.shared.enums import (
    AssetType,
    FailureClass,
    Outcome,
    RiskLevel,
    Subsystem,
    ValidationStatus,
)


# ─────────────────────── 作用域 ───────────────────────
class Scope(BaseModel):
    """资产作用域 = 租户 + 系统实例。命中消费时按作用域匹配。"""

    tenant: str = "a-corp"
    subsystem: Subsystem


# ─────────────────────── 生成报告(文档第九节)───────────────────────
class GenerationReport(BaseModel):
    """每份资产自带的生成报告,作为审计与回溯依据。

    回答:它从哪来、凭什么可信、谁确认的、能不能回滚。
    """

    source_materials: list[str] = Field(default_factory=list, description="用了哪些源材料")
    source_fingerprints: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    capabilities_used: list[str] = Field(default_factory=list, description="侦察/沙箱/写回等")
    verification_evidence: dict[str, Any] = Field(default_factory=dict, description="自验证结果与证据")
    confirmed_by: str | None = Field(default=None, description="低置信项的人工确认人")


# ─────────────────────── 资产元数据信封 ───────────────────────
class AssetEnvelope(BaseModel):
    """所有资产共用的信封。body 按 asset_type 解释为 asset_bodies 中的某个模型。"""

    asset_id: UUID | None = None
    asset_type: AssetType
    scope: Scope
    asset_key: str = Field(
        default="default",
        description="作用域内逻辑标识:连接器=动作名,其余=类型。版本号按它独立递增",
    )
    version: int
    source_fingerprint: str
    validation_status: ValidationStatus = ValidationStatus.DRAFT
    confidence: float = Field(default=0.0, ge=0, le=1)
    human_confirmed: bool = False
    generation_report: GenerationReport = Field(default_factory=GenerationReport)
    body: dict[str, Any] = Field(description="JSONB,解释为五类资产体之一")
    created_at: datetime | None = None


# ─────────────────────── 任务简报(控制层 → 执行层)───────────────────────
class TaskBrief(BaseModel):
    """主智能体下发给子智能体 harness 的唯一载荷。

    这是四重隔离里「上下文隔离」的物理边界——子智能体只看得到这个。
    """

    task_id: UUID
    tenant: str = "a-corp"
    subsystem: Subsystem
    skill_id: str = Field(description="命中的动作 Skill(1 Skill = 1 action)")
    action: str
    fields: dict[str, Any] = Field(default_factory=dict, description="已由流程5映射的字段值")
    tool_whitelist: list[str] = Field(default_factory=list, description="工具隔离:只注入本子系统连接器工具")
    skill_mount: list[str] = Field(default_factory=list, description="skill 隔离")
    mcp_grants: list[str] = Field(default_factory=list, description="MCP 隔离:只连授权 server")
    credential_refs: dict[str, str] = Field(default_factory=dict, description="vault:// 引用,绝不给明文")
    assertions: Assertions = Field(default_factory=Assertions)
    risk_level: RiskLevel = RiskLevel.L1


# ─────────────────────── 执行结果(执行层 → 控制层)───────────────────────
class AssertionResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class Evidence(BaseModel):
    """断言/事实核查的证据。"""

    request_body: dict[str, Any] | None = None
    response_body: dict[str, Any] | None = None
    screenshots: list[str] = Field(default_factory=list, description="截图引用")
    dom_snapshots: list[str] = Field(default_factory=list)


class ExecResult(BaseModel):
    task_id: UUID
    outcome: Outcome
    assertion_results: list[AssertionResult] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)
    structured_output: dict[str, Any] = Field(default_factory=dict, description="单号/状态等")
    failure_class: FailureClass | None = None
