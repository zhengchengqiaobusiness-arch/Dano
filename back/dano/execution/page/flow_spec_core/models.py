"""Shared FlowSpec pydantic models. No stage inference lives here."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


_SYNC_HOOK: Callable[["FlowSpec"], "FlowSpec"] | None = None


def register_sync_flow_spec_models(hook: Callable[["FlowSpec"], "FlowSpec"]) -> None:
    """Install the materialized-spec sync used by FlowSpec.model_validate."""
    global _SYNC_HOOK
    _SYNC_HOOK = hook


class ParamField(BaseModel):
    field_id: str = ""
    path: str
    key: str
    label: str = ""
    value: Any = ""
    type: str = "string"  # string/number/boolean/datetime/date/array/object/list-enum
    wire_type: str = ""  # immutable request-leaf transport type before business projection
    required: bool = True
    confidence: float = 0.0
    confidence_tier: str = "auto"
    name_source: str = "auto"
    description: str | None = None
    # **enum_options 形态:list[str] | list[dict{label,value}] | list[tuple[label,value]] 兼容** ——
    # 同时承载 label 给前端显示, 也承载真实提交值(value)做 name→ID 解析。
    # 系统化关键改动, 不绑具体业务(字典下拉、原生 <select>、自定义 div 都生效)。
    enum_options: list[Any] | None = None
    # 当枚举带 value 时 {label: value}, 运行期 name→ID 用(发布后渲染 + playbook 静态枚举都用同一份)。
    enum_value_map: dict[str, Any] | None = None
    # Step D: 三类字段分类
    # user_param: 用户参数(每次调用可能变,让 agent 传)
    # system_const: 系统常量(流程定义 ID/表单类型/固定状态码,不能让 agent 改)
    # runtime_var: 运行期变量(录制时有值,但不能冻结,运行期自动填)
    category: str = "user_param"  # user_param / system_const / runtime_var
    source_kind: str = "unknown"   # user_input / previous_response / current_user / storage / cookie / page_context / system_time / constant / page_default / page_rule / api_option / page_enum / static_enum / manual_enum / form_option / selected_option_field / computed / unknown
    # 线上取值格式（与业务类型正交）：epoch_ms / epoch_s / datetime_text / date_text / ""。
    # 从录制样例值推断，写进输入 schema，调用方据此传对格式（例如 datetime 业务类型但线上要毫秒时间戳）。
    wire_format: str = ""
    source: dict[str, Any] = Field(default_factory=dict)
    editable: bool = True
    exposed_to_user: bool = True
    default_value: Any = None
    reason: str = ""
    need_human_confirm: bool = False
    locked: bool = False
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SelectBinding(BaseModel):
    param: str = ""
    path: str = ""
    source_url: str = ""
    source_method: str = "GET"
    source_headers: dict[str, Any] = Field(default_factory=dict)
    source_body: Any = None
    source_content_type: str = ""
    source_role: str = ""
    source_request_id: str = ""
    value_key: str = ""
    label_key: str = ""
    category_key: str | None = None
    category_value: str | None = None
    multi: bool = False
    element_template: dict[str, Any] | None = None
    label_subkey: str | None = None
    count: int = 0
    options: list[Any] | None = None
    option_map: dict[str, Any] | None = None
    enum_source: str | None = None
    enum_confirmed: bool | None = None
    id_path: str | None = None
    id_tokens: list[str | int] | None = None
    field_projections: dict[str, str] = Field(default_factory=dict)  # target request path -> selected item response path
    actor: str = "heuristic"
    confidence: float = 0.0
    verification_id: str = ""


class IdentityBinding(BaseModel):
    path: str
    source: str  # localStorage:userInfo.userId / cookie:JSESSIONID
    tokens: list[str | int] | None = None
    value: str | None = None


class SystemValue(BaseModel):
    path: str
    tokens: list[str | int] | None = None
    kind: str = "now_ms"


class FlowStep(BaseModel):
    step_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    method: str = "POST"
    url: str = ""
    path: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    content_type: str = "application/json"
    body_source: str = ""
    body_template: Any = None
    params: list[ParamField] = Field(default_factory=list)
    selects: list[SelectBinding] = Field(default_factory=list)
    identity: list[IdentityBinding] = Field(default_factory=list)
    system_values: list[SystemValue] = Field(default_factory=list)
    success_rule: dict[str, Any] | None = None
    response_json: Any = None
    risk_level: str = "L3"
    semantic_role: str = ""
    source_meta: dict[str, Any] = Field(default_factory=dict)
    fact_check: dict[str, Any] | None = None
    # Recorded samples preserve the request's wire type.  Enum IDs, counts and
    # booleans are valid JSON scalars and must not be forced into strings merely
    # to make a release snapshot re-validate.
    sample_inputs: dict[str, Any] = Field(default_factory=dict)


class FlowLink(BaseModel):
    link_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    source_step_id: str = ""
    source_path: str = ""
    source_tokens: list[str | int] | None = None
    target_step_id: str = ""
    target_path: str = ""
    target_tokens: list[str | int] | None = None
    kind: str = "value"
    source_collection_path: str = ""
    source_key_path: str = ""
    source_label_path: str = ""
    target_container_path: str = ""
    value_binding: dict[str, Any] = Field(default_factory=dict)
    param_name: str | None = None
    confirmed: bool = False
    confidence: float = 0.0
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    locked: bool = False


class RequestFact(BaseModel):
    """一次真实捕获请求的不可变事实。

    role/confidence/usage 会随着规则和人工编辑变化，拆到 RequestAnalysis/
    RequestUsage；这里尽量只放录制时看到的证据。
    """

    model_config = ConfigDict(extra="allow")

    request_id: str = ""
    request_index: Any = None
    page_id: str | None = None
    frame_id: str | None = None
    sequence: Any = None
    method: str = ""
    url: str = ""
    path: str = ""
    query: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    content_type: str = ""
    post_data: Any = None
    response_status: Any = None
    response_json: Any = None
    response_kind: str = ""
    response_text: str | None = None
    response_empty: bool = False
    response_size: Any = None
    response_schema: dict[str, Any] = Field(default_factory=dict)
    timestamp: Any = None


class RequestAnalysis(BaseModel):
    """可重算的请求分析结果。"""

    model_config = ConfigDict(extra="allow")

    request_id: str = ""
    role: str = ""
    semantic_roles: list[str] = Field(default_factory=list)
    keep: bool = False
    reason: str = ""
    confidence: float = 0.0
    evidence: dict[str, Any] = Field(default_factory=dict)
    bucket: str = ""
    filter_reason: str = ""


class RequestUsage(BaseModel):
    """请求被能力/步骤使用的派生索引。"""

    model_config = ConfigDict(extra="allow")

    request_id: str = ""
    materialized_step_id: str = ""
    state: str = "captured"
    used_by_capabilities: list[str] = Field(default_factory=list)
    capability_memberships: list[dict[str, Any]] = Field(default_factory=list)


class RequestFacts(BaseModel):
    """录制请求事实库；请求捕获、分析与使用状态的唯一权威来源。"""

    model_config = ConfigDict(extra="allow")

    protocol: str = "dano.request_facts.v1"
    requests: list[RequestFact] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    page_events: list[dict[str, Any]] = Field(default_factory=list)
    option_sources: list[dict[str, Any]] = Field(default_factory=list)
    analysis: dict[str, RequestAnalysis] = Field(default_factory=dict)
    usage: dict[str, RequestUsage] = Field(default_factory=dict)


class CapabilityRequestRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str = ""
    request_index: Any = None
    step_id: str = ""
    role: str = ""
    method: str = ""
    path: str = ""
    sequence: Any = None
    confidence: float = 0.0
    reason: str = ""
    usage: str = "execute"  # execute / option_source / fact_check / preflight
    origin: str = "planner"  # planner / manual / repair / migration
    confirmed: bool = False


class CapabilityField(BaseModel):
    model_config = ConfigDict(extra="allow")

    field_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    scope: str = "input"  # input / request_field / internal / computed / output
    display_name: str = ""
    path: str = ""
    key: str = ""
    type: str = "string"
    wire_type: str = ""
    wire_format: str = ""
    required: bool = False
    request_id: str = ""
    request_index: Any = None
    step_id: str = ""
    source_kind: str = "unknown"
    source: dict[str, Any] = Field(default_factory=dict)
    category: str = "user_param"
    enum_options: list[Any] | None = None
    enum_value_map: dict[str, Any] | None = None
    exposed_to_caller: bool = True
    confidence: float = 0.0
    confirmed: bool = False
    locked: bool = False
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class CapabilityDependency(BaseModel):
    model_config = ConfigDict(extra="allow")

    dependency_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: str = "response_to_request"
    source: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    confirmed: bool = False
    locked: bool = False
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class CapabilityRelation(BaseModel):
    model_config = ConfigDict(extra="allow")

    relation_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: str = "suggested_call_chain"
    from_capability: str = ""
    from_output: str = ""
    to_capability: str = ""
    to_input: str = ""
    requires_user_confirmation: bool = True
    confidence: float = 0.0
    confirmed: bool = False
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    mode: str = "external_transform"
    transform_owner: str = "caller"
    cardinality: str = "many_to_many"
    required: bool = False
    source_selector: str = ""
    target_path: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class ReviewItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: str = ""
    severity: str = "medium"  # high / medium / low
    title: str = ""
    target: dict[str, Any] = Field(default_factory=dict)
    current_guess: str = ""
    suggested_action: str = ""
    reason: str = ""
    resolved: bool = False
    confidence: float = 0.0
    # Review items are operator guidance, not deterministic publish gates.
    # Keep this explicit in the wire contract so clients do not have to infer
    # blocking behaviour from severity (an unresolved warning may still be
    # high-visibility while remaining safe to dismiss).
    blocking: bool = False
    ignorable: bool = True


class FlowCapability(BaseModel):
    """对外前端可调用的业务能力层。

    FlowStep/FlowLink 仍描述真实接口执行；Capability 描述外部调用方看到的业务动作。
    """

    name: str = ""
    title: str = ""
    intent: str = ""
    kind: str = "submit"  # query/export/create/update/save_draft/submit/withdraw/delete/...
    capability_id: str = Field(default_factory=lambda: f"cap_{uuid.uuid4().hex}")
    request_refs: list[CapabilityRequestRef] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)

    inputs: list[CapabilityField] = Field(default_factory=list)
    request_fields: list[CapabilityField] = Field(default_factory=list)
    internal_fields: list[CapabilityField] = Field(default_factory=list)
    computed_fields: list[CapabilityField] = Field(default_factory=list)
    outputs: list[CapabilityField] = Field(default_factory=list)
    dependencies: list[CapabilityDependency] = Field(default_factory=list)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    output_mapping: list[dict[str, Any]] = Field(default_factory=list)
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    confirmed: bool = False
    confidence: float = 0.0
    requires_human_confirm: bool = False
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    caller_responsibilities: list[str] = Field(default_factory=list)
    skill_responsibilities: list[str] = Field(default_factory=list)
    status: str = "draft"  # draft / ready / confirmed
    locked: bool = False
    updated_by: str = "planner"  # planner / user / repair
    # Hash of the executable contract that was reviewed when ``confirmed`` was
    # set. It is deliberately derived from steps/fields/nodes instead of being a
    # second source of truth. Any semantic edit clears confirmation.
    confirmation_hash: str = ""


class RecordedGoal(BaseModel):
    """录制后沉淀的业务目标，供 Planner/Validator/Repair/说明生成共用。"""

    intent: str = ""
    required_inputs: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    output_expectation: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    risk_level: str = "L3"
    capabilities: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class FlowSpec(BaseModel):
    flow_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    tenant: str = ""
    subsystem: str = ""
    title: str = ""
    business_description: str = ""
    recording_mode: str = "unknown"
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[FlowStep] = Field(default_factory=list)
    links: list[FlowLink] = Field(default_factory=list)
    capabilities: list[FlowCapability] = Field(default_factory=list)
    request_facts: RequestFacts = Field(default_factory=RequestFacts)
    capability_relations: list[CapabilityRelation] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    goal: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "L3"
    meta: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1

    @model_validator(mode="after")
    def _sync_derived_models(self) -> "FlowSpec":
        hook = _SYNC_HOOK
        if hook is None:
            return self
        return hook(self)


class FlowSpecConflictError(ValueError):
    """The client attempted to patch a superseded authoritative draft."""

    def __init__(self, expected_fingerprint: str, current_fingerprint: str) -> None:
        super().__init__("flow_spec fingerprint conflict")
        self.expected_fingerprint = expected_fingerprint
        self.current_fingerprint = current_fingerprint
