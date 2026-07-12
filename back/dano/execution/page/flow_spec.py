"""Step A+B+C+D: FlowSpec 完整实现。

- Step A: 收敛函数 to_flow_spec（包含 GET 业务请求）
- Step B: 编辑函数 apply_flow_edits（字段/参数/链接/重排）
- Step C: 链接编辑支持
- Step D: GET 表单手选 + LLM 命名 + 业务说明
"""

from __future__ import annotations

import re
import uuid
import json
import copy
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from urllib.parse import urlparse, parse_qs, urlencode

# 复用 request_capture 的纯函数
from dano.execution.page.request_capture import (
    _is_const_value,
    _leaf_paths,
    _parse_body,
    _is_system_timestamp,
    auto_required_fields,
    as_list_payload,
    apply_page_enum_options,
    build_api_request,
    classify_request_role,
    discover_step_links,
    page_enum_selects,
    extract_auth_headers,
    flatten_body,
    infer_success_rule,
    json_write_requests,
    looks_dangerous_write,
    looks_internal_param_name,
    looks_like_auth_write,
    looks_like_read_request,
    pick_submit_request,
    self_check,
    substitute,
    suggest_assignee_names,
    suggest_fact_check,
    suggest_identity,
    suggest_list_selects,
    suggest_select_names,
    suggest_selects,
    suggest_workflow_steps,
)


# ─────────── 数据模型 ───────────
class ParamField(BaseModel):
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
    source_kind: str = "unknown"   # user_input / previous_response / current_user / storage / cookie / page_context / system_time / constant / api_option / page_enum / static_enum / manual_enum / form_option / unknown
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


class IdentityBinding(BaseModel):
    path: str
    source: str  # localStorage:userInfo.userId / cookie:JSESSIONID
    tokens: list[str | int] | None = None
    value: str | None = None


# H19 修复:显式白名单(替代 hasattr 兜底,防止越权改关键字段)
_PARAM_ALLOWED_FIELDS = frozenset({
    "category", "source_kind", "source", "label",
    "reason", "confidence", "name_source", "enum_options",
    "enum_value_map", "locked", "evidence", "description",
})
_STEP_ALLOWED_FIELDS = frozenset({
    "selects", "identity", "params", "sample_inputs",
    "source_meta", "semantic_role", "success_rule", "fact_check",
    "response_json", "notes",
})

_PUBLISH_BLOCKING_REVIEW_TYPES = frozenset({
    "dangerous_step",
    "runtime_var_missing_source",
    "system_const_exposed",
    "broken_link",
    "link_source_missing",
    "link_target_missing",
    "link_confirmation",
})


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
    sample_inputs: dict[str, str] = Field(default_factory=dict)


class FlowLink(BaseModel):
    link_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    source_step_id: str = ""
    source_path: str = ""
    source_tokens: list[str | int] | None = None
    target_step_id: str = ""
    target_path: str = ""
    target_tokens: list[str | int] | None = None
    param_name: str | None = None
    confirmed: bool = False
    confidence: float = 0.0
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
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
    """录制请求事实库。

    P0 阶段仍会同步旧 meta.request_graph，避免打断旧前端和发布链路。
    """

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
    pinned: bool = False
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


class FlowCapability(BaseModel):
    """对外前端可调用的业务能力层。

    FlowStep/FlowLink 仍描述真实接口执行；Capability 描述外部调用方看到的业务动作。
    """

    name: str = ""
    title: str = ""
    intent: str = ""
    kind: str = "submit"  # query_status / list_options / validate_batch / submit_batch / submit
    capability_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    request_refs: list[CapabilityRequestRef] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)
    fields: list[CapabilityField] = Field(default_factory=list)
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

    @model_validator(mode="before")
    @classmethod
    def _migrate_request_facts_input(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        meta = dict(payload.get("meta") or {})
        graph = meta.get("request_graph") or {}
        raw_facts = payload.get("request_facts")
        if graph and not _raw_request_facts_has_requests(raw_facts):
            payload["request_facts"] = _request_facts_from_graph(
                graph,
                diagnostics=list(payload.get("diagnostics") or meta.get("diagnostics") or []),
            ).model_dump()
        elif raw_facts is not None and _raw_request_facts_has_requests(raw_facts) and not graph:
            try:
                meta["request_graph"] = _request_graph_from_request_facts(RequestFacts.model_validate(raw_facts))
                payload["meta"] = meta
            except Exception:
                pass
        return payload

    @model_validator(mode="after")
    def _sync_derived_models(self) -> "FlowSpec":
        return sync_flow_spec_models(self, prefer_request_facts=True)


# ─────────── Step A: 收敛函数 ───────────
def _infer_type_from_value(value: Any) -> str:
    if value in (None, ""):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    text = str(value)
    if text.lower() in ("true", "false"):
        return "boolean"
    if re.match(r"^\d{4}-\d{2}-\d{2}T", text):
        return "datetime"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return "date"
    try:
        float(text)
        return "number"
    except (ValueError, TypeError):
        pass
    return "string"


def _default_step_name(req: dict) -> str:
    url = req.get("url") or req.get("path") or ""
    method = (req.get("method") or "POST").upper()
    try:
        path = urlparse(url).path if url.startswith("http") else url
    except Exception:
        path = url
    segs = [s for s in (path or "").split("/") if s]
    last = segs[-1] if segs else ""
    if not last:
        return f"{method}_未命名"
    last = last.split("?")[0].rsplit(".", 1)[0]
    return f"{method}_{last}"


def _path_from_url(url: str, base_url: str = "") -> str:
    if base_url and url.startswith(base_url):
        return url[len(base_url):] or "/"
    if url.startswith("http"):
        u = urlparse(url)
        return (u.path or "/") + (("?" + u.query) if u.query else "")
    return url or "/"


def _select_name_for_step(selects: list[dict], samples: dict) -> dict[str, str]:
    out = suggest_select_names(selects, samples)
    for s in selects or []:
        path = str(s.get("path") or "")
        field_key = str(s.get("field_key") or "").strip()
        if not path or not field_key:
            continue
        if looks_internal_param_name(field_key):
            continue
        out[path] = field_key
    return out


def _norm_field_name(key: str, path: str = "") -> str:
    return re.sub(r"[^a-z0-9]+", "", f"{key}.{path}".lower())


def _sample_value_set(samples: dict | None) -> set[str]:
    return {str(v) for v in (samples or {}).values() if v not in (None, "")}


def _looks_current_user_field(key: str, path: str) -> bool:
    k = _norm_field_name(key, path)
    return any(x in k for x in (
        "userid", "user_id", "currentuser", "currentuserid", "applicantid",
        "applicantuserid", "creatorid", "createuserid", "ownerid", "operatorid",
    ))


def _looks_runtime_field(key: str, path: str) -> bool:
    k = _norm_field_name(key, path)
    return any(x in k for x in (
        "taskid", "draftid", "instanceid", "processinstanceid", "conversationid",
        "conversation_id", "sessionid", "nonce", "uuid", "token", "accesstoken",
        "refreshtoken", "appcode", "wybs",
    ))


_SESSION_LITERAL_RE = re.compile(r"^[A-Za-z]{2,}[-_]\d{4,}")
_UUID_LITERAL_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-")


def _looks_session_specific_value(value: Any) -> bool:
    """值像「一次性会话字面值 / 运行期 ID / uuid / 雪花 ID」等不能固化的字面值。
    关键:不绑定具体业务字段名,只看值本身特征 + 适当弱化兜底:
    - 13 位纯数字:`*`可能是* 当前毫秒时间戳,也可能是用户填的请假起止时间。只在「key 也是运行期字面」
      (heuristic 上 `_looks_runtime_field`) 时才当会话值;否则当**用户输入**(user_input)。
    - uuid / session literal (BB-12345):无论 key 是什么,百分百不能固化。
    通用,不挑系统。"""
    s = str(value if value is not None else "").strip()
    if not s:
        return False
    if s.isdigit() and len(s) == 13:
        # 13 位毫秒时间戳—— 常是 startTime/endTime/createTime 类用户填的时间字段;
        # 仅当 caller 拿具体 key/path 进一步问询时才升级为 session literal。
        # (caller 选用 _looks_session_literal_after_key_check 进一步收紧)
        return True
    if s.isdigit() and len(s) == 10:
        return True  # 10 位秒时间戳 / 一律当会话值
    if _UUID_LITERAL_RE.match(s):
        return True
    if _SESSION_LITERAL_RE.match(s) and re.search(r"\d{4,}", s):
        return True
    return False


def _looks_session_literal_after_key_check(value: Any, key: str, path: str) -> bool:
    """加固:`_looks_session_specific_value` 通过后,再按 key/path 形态判定。
    用以治「startTime=1783440000000 等用户填时间字段被错当 session_literal」。
    通用,不绑具体字段名——只看启发式:
    - 如果 key/path 形态像「具体时间字段」(`start* / end* / create* / begin* / time* / date*` → datetime),就不升级
    - 如果 key/path 像「运行期 ID」(`*id` / `*token` / `*code`),才升级
    - 否则保守不升级,让 caller 用 user_input / system_const 兜底
    """
    if not _looks_session_specific_value(value):
        return False
    s = str(value).strip()
    is_digit13 = s.isdigit() and len(s) == 13
    if not is_digit13:
        return True  # 10 位秒/uuid/session literal 仍按 session_literal 处理
    norm = _norm_field_name(key, path)
    # 用户填的具体时间字段名——不当 session_literal
    if any(x in norm for x in ("start", "end", "begin", "expire", "deadline",
                                  "createdate", "applydate", "leavedate", "begindate",
                                  "starttime", "endtime", "startdate", "enddate")):
        return False
    # datetime 字段名 → 当 datetime,不当 session literal
    if any(x in norm for x in ("time", "date", "day")) and not any(x in norm for x in ("id", "key", "code", "token")):
        return False
    return True


def _looks_token_field(key: str, path: str) -> bool:
    k = _norm_field_name(key, path)
    return any(x in k for x in ("token", "accesstoken", "refreshtoken", "authorization", "satoken"))


def _header_value_matches_token(field_value: str, header_value: str) -> bool:
    fv = str(field_value or "").strip()
    hv = str(header_value or "").strip()
    if not fv or not hv:
        return False
    if hv == fv:
        return True
    low = hv.lower()
    if low.startswith("bearer ") and hv[7:].strip() == fv:
        return True
    return False


def _request_header_source_for_token(key: str, path: str, value: str, request_headers: dict | None) -> dict[str, Any] | None:
    if not _looks_token_field(key, path):
        return None
    for header, header_value in (request_headers or {}).items():
        if _header_value_matches_token(value, str(header_value)):
            return {"kind": "request_header", "header": str(header), "path": path}
    return None


def _looks_system_const_field(key: str, path: str) -> bool:
    k = _norm_field_name(key, path)
    return any(x in k for x in (
        "processdefinitionkey", "processdefinitionid", "processdefkey", "processdefid", "billtype", "formtype",
        "flowtype", "businesstype", "templateid", "template_id", "formid",
        "menuid", "appid", "appname", "activityid", "startnodeid", "bpmnnodeid",
    ))


def _looks_page_context_field(key: str, path: str) -> bool:
    k = _norm_field_name(key, path)
    raw_key = re.sub(r"[^a-z0-9]+", "", str(key or "").lower())
    raw_path = re.sub(r"[^a-z0-9]+", "", str(path or "").split(".")[-1].lower())
    exact = {
        "bmid", "bmmc", "ssbmid", "ssbmmc", "deptid", "deptname", "departmentid",
        "departmentname", "orgid", "orgname", "organid", "organname", "unitid",
        "unitname", "companyid", "companyname", "tenantid", "tenantname",
    }
    if raw_key in exact or raw_path in exact:
        return True
    return any(x in k for x in (
        "department", "dept", "organization", "org", "tenant", "company",
        "bumen", "jigou", "danwei", "deptcode", "orgcode", "unitcode",
    ))


_OPTION_SOURCE_KINDS = {"api_option", "page_enum", "static_enum", "manual_enum", "form_option"}


def _looks_pagination_field(key: str, path: str) -> bool:
    raw = re.sub(r"[^a-z0-9]+", "", f"{key}.{path}".lower())
    return raw.endswith(("pageno", "pagenum", "pagesize", "pageindex", "currentpage", "limit", "offset"))


def _looks_user_entered_business_field(key: str, path: str) -> bool:
    """字段名像调用方/最终用户填写的业务内容时，不允许值驱动自动改成上游响应。

    这类字段经常与列表查询响应中的旧记录值相同，例如申请标题、备注、使用说明、日期。
    如果仅靠 value match 自动绑定，会把“查询已有记录”误当成“提交字段来源”。
    """
    norm = _norm_field_name(key, path)
    if not norm:
        return False
    if any(x in norm for x in (
        "title", "name", "reason", "remark", "memo", "note", "desc", "description",
        "content", "info", "message", "comment", "summary", "subject", "purpose",
        "date", "time", "day", "start", "end", "begin", "back", "return",
        "applytitle", "useinfo", "gznr", "sbyy", "qjyy",
    )):
        if not any(x in norm for x in ("id", "key", "code", "token", "instance", "task", "process")):
            return True
    return False


def _is_option_source_url(url: str) -> bool:
    path = _request_path({"url": url}).lower()
    segs = {s for s in re.split(r"[^a-z0-9]+", path) if s}
    if segs & {"dict", "dictionary", "option", "options", "select", "simple", "simplelist", "tree", "candidate", "candidates"}:
        return True
    if path.endswith(("/list", "/simple-list", "/tree", "/select", "/options", "/candidates")):
        return True
    last = path.rsplit("/", 1)[-1]
    if re.search(r"(?:^|[-_])(?:get|query|select)?[a-z0-9]*(?:list|tree|options?|candidates?)(?:by|$|[-_])", last):
        return True
    return False


def _read_is_option_source(read: dict) -> bool:
    role = str(read.get("role") or read.get("request_role") or "")
    url = str(read.get("url") or read.get("path") or "")
    path = _request_path({"url": url}).lower()
    is_known_option_path = _is_option_source_url(url) or any(
        seg in path for seg in ("/user/", "/dept/", "/department/", "/role/", "/post/", "/employee/", "/person/")
    )
    if path.endswith("/page") or "/page?" in str(url).lower():
        if not is_known_option_path:
            return False
    payload = read.get("json", read.get("response_json"))
    has_list_payload = bool(as_list_payload(payload))
    if role == "explicit_read_option":
        return has_list_payload
    if role == "read_option":
        return has_list_payload
    if role in {"business_get", "read_context"}:
        # 业务记录列表不是字段枚举。角色分类是事实库结论，不能再因“响应是数组”
        # 二次降格成选项源，否则日报日期/审批记录会被错误绑定到提交字段。
        return False
    if not _is_option_source_url(url):
        return False
    return has_list_payload


def _option_candidate_reads(reads: list[dict] | None) -> list[dict]:
    return [r for r in (reads or []) if isinstance(r, dict) and _read_is_option_source(r)]


def _select_source_kind(sel: SelectBinding | None) -> str:
    if sel is None:
        return "static_enum"
    if sel.enum_source == "dom":
        return "page_enum"
    if sel.enum_source == "manual":
        return "manual_enum"
    if sel.source_url:
        return "api_option"
    if sel.options:
        return "static_enum"
    return "static_enum"


def _select_source_reason(kind: str, *, id_field: bool = False) -> str:
    if id_field:
        return "该字段是选择项对应的内部 ID，运行期随用户选择自动写入，不暴露给用户手填"
    if kind == "api_option":
        return "该字段来自接口候选源，运行期从接口获取真实候选"
    if kind == "page_enum":
        return "该字段来自录制页面真实下拉快照，属于页面固定枚举"
    if kind == "manual_enum":
        return "该字段来自人工维护的枚举候选"
    if kind == "static_enum":
        return "该字段来自固定枚举候选"
    return "该字段来自选择型字段"


def _param_source_guess(
    *,
    field: dict,
    path: str,
    key: str,
    method: str,
    identity_paths: set[str],
    system_paths: set[str],
    select_paths: set[str],
    select_id_paths: set[str],
    select_by_path: dict[str, SelectBinding] | None = None,
    select_by_id_path: dict[str, SelectBinding] | None = None,
    samples: dict,
    request_headers: dict | None = None,
) -> dict[str, Any]:
    value = str(field.get("value") or "")

    header_source = _request_header_source_for_token(key, path, value, request_headers)
    if header_source:
        return {
            "category": "runtime_var",
            "source_kind": "request_header",
            "source": header_source,
            "editable": False,
            "exposed_to_user": False,
            "reason": f"该 token 字段与请求头 `{header_source['header']}` 一致，运行期从请求头读取，不使用录制旧值",
            "need_human_confirm": False,
        }

    if path in identity_paths:
        return {
            "category": "runtime_var",
            "source_kind": "current_user",
            "source": {"kind": "identity", "path": path},
            "editable": False,
            "exposed_to_user": False,
            "reason": "该字段与当前登录用户/会话值匹配，运行期从登录态重新读取，不能使用录制者旧值",
            "need_human_confirm": False,
        }

    if path in system_paths:
        return {
            "category": "runtime_var",
            "source_kind": "system_time",
            "source": {"kind": "system_time", "path": path},
            "editable": False,
            "exposed_to_user": False,
            "reason": "该字段是系统时间戳，运行期使用当前时间生成",
            "need_human_confirm": False,
        }

    if _looks_pagination_field(key, path):
        return {
            "category": "system_const",
            "source_kind": "constant",
            "source": {"kind": "pagination", "path": path},
            "editable": True,
            "exposed_to_user": False,
            "reason": "分页参数由 Skill 内部按默认分页提交，不作为普通业务字段暴露",
            "need_human_confirm": False,
        }

    if path in select_paths:
        select_binding = (select_by_path or {}).get(path)
        source_kind = _select_source_kind(select_binding)
        return {
            "category": "user_param",
            "source_kind": source_kind,
            "source": {
                "kind": source_kind,
                "path": path,
                **({
                    "source_url": select_binding.source_url,
                    "source_request_id": select_binding.source_request_id,
                    "value_key": select_binding.value_key,
                    "label_key": select_binding.label_key,
                } if select_binding is not None and select_binding.source_url else {}),
            },
            "editable": True,
            "exposed_to_user": True,
            "reason": _select_source_reason(source_kind),
            "need_human_confirm": False,
        }

    if path in select_id_paths:
        select_binding = (select_by_id_path or {}).get(path)
        source_kind = _select_source_kind(select_binding)
        return {
            "category": "runtime_var",
            "source_kind": source_kind,
            "source": {
                "kind": "select_id", "path": path, "option_kind": source_kind,
                **({
                    "source_url": select_binding.source_url,
                    "source_request_id": select_binding.source_request_id,
                    "value_key": select_binding.value_key,
                    "label_key": select_binding.label_key,
                } if select_binding is not None and select_binding.source_url else {}),
            },
            "editable": False,
            "exposed_to_user": False,
            "reason": _select_source_reason(source_kind, id_field=True),
            "need_human_confirm": False,
        }

    if method == "GET" and path.startswith("query."):
        if _looks_system_const_field(key, path) or _is_const_value(value):
            return {
                "category": "system_const",
                "source_kind": "constant",
                "source": {"kind": "query_constant", "path": path},
                "editable": True,
                "exposed_to_user": False,
                "reason": "该 GET 查询参数是稳定流程键、节点键或内部标识，默认作为接口常量；若有上游依赖会自动改为运行期来源",
                "need_human_confirm": False,
            }
        if _looks_page_context_field(key, path):
            context_key = key or path.split(".")[-1]
            return {
                "category": "runtime_var",
                "source_kind": "page_context",
                "source": {"kind": "page_context", "context_key": context_key, "path": path},
                "editable": True,
                "exposed_to_user": False,
                "reason": "该查询字段来自显式调用上下文；运行期按 context_key 注入，不使用录制旧值",
                "need_human_confirm": True,
            }
        return {
            "category": "user_param",
            "source_kind": "user_input",
            "source": {"kind": "sample", "path": path},
            "editable": True,
            "exposed_to_user": True,
            "reason": "该字段是业务查询条件，默认作为能力调用参数；若由前置接口提供，可再绑定上游响应",
            "need_human_confirm": False,
        }

    # 录制期间由用户真实填写/选择并出现在 samples 中，是字段归属的强事实。
    # 它必须优先于 *Id/*Type 等命名启发式；否则不同系统的业务字段只因内部
    # 命名像 ID/状态码就会被错误改成运行期变量或系统常量。
    if value not in (None, "") and value in _sample_value_set(samples):
        return {
            "category": "user_param",
            "source_kind": "user_input",
            "source": {"kind": "sample", "path": path, "recorded": True},
            "editable": True,
            "exposed_to_user": True,
            "reason": "该值由用户在录制页面真实填写，调用 Skill 时作为用户参数",
            "need_human_confirm": False,
        }

    if value == "" and value not in _sample_value_set(samples):
        return {
            "category": "system_const",
            "source_kind": "constant",
            "source": {"kind": "empty_field", "path": path},
            "editable": True,
            "exposed_to_user": False,
            "reason": "该字段录制值为空且未匹配到用户输入，默认保留为空值结构，不暴露给用户手填",
            "need_human_confirm": False,
        }

    if _looks_current_user_field(key, path):
        return {
            "category": "runtime_var",
            "source_kind": "current_user",
            "source": {"kind": "heuristic", "path": path},
            "editable": False,
            "exposed_to_user": False,
            "reason": "字段名像当前用户标识，运行期应从当前登录态获取，需确认具体来源",
            "need_human_confirm": True,
        }

    if _looks_runtime_field(key, path):
        return {
            "category": "runtime_var",
            "source_kind": "unknown",
            "source": {"kind": "heuristic", "path": path},
            "editable": False,
            "exposed_to_user": False,
            "reason": "字段名像 taskId/conversation_id/token/appCode 等运行期变量，不能直接固化录制值",
            "need_human_confirm": True,
        }

    if _looks_system_const_field(key, path):
        return {
            "category": "system_const",
            "source_kind": "constant",
            "source": {"kind": "heuristic", "path": path},
            "editable": True,
            "exposed_to_user": False,
            "reason": "字段名像流程定义、表单类型、应用 ID 或固定状态，默认作为系统常量",
            "need_human_confirm": False,
        }

    if _looks_page_context_field(key, path) and value not in _sample_value_set(samples):
        context_key = key or path.split(".")[-1]
        return {
            "category": "runtime_var",
            "source_kind": "page_context",
            "source": {"kind": "page_context", "context_key": context_key, "path": path},
            "editable": True,
            "exposed_to_user": False,
            "reason": "字段名像部门/组织/租户等调用上下文；运行期需按 context_key 注入或改绑上游响应",
            "need_human_confirm": True,
        }

    # 系统化:datetime 字段(用户填的具体时间)即使值是 13 位毫秒,也不当 session_literal。
    # 同时若字段名像「具体时间字段」(start* / end* 等),放行 user_input。
    if _looks_session_literal_after_key_check(value, key, path) and value not in _sample_value_set(samples):
        # 系统化:datetime/具体时间字段 → 当 user_input,不是 session_literal;
        # 只有真正像 ID/uuid 的「session 字面」才升级 runtime_var。
        # caller 已经用 _looks_session_literal_after_key_check 二次把关,
        # 这里如果过了那关且字段名是时间类的,转 user_input。
        if any(x in _norm_field_name(key, path) for x in ("start", "end", "begin", "createdate",
                                                              "applydate", "leavedate", "begindate",
                                                              "starttime", "endtime", "startdate", "enddate")):
            return {
                "category": "user_param",
                "source_kind": "user_input",
                "source": {"kind": "sample", "path": path},
                "editable": True,
                "exposed_to_user": True,
                "reason": "字段名像具体时间字段（startTime/endTime 等），录到的 13 位毫秒是用户亲手填的时间，调用 Skill 时由用户填写",
                "need_human_confirm": False,
            }
        return {
            "category": "runtime_var",
            "source_kind": "unknown",
            "source": {"kind": "session_literal", "path": path},
            "editable": False,
            "exposed_to_user": False,
            "reason": "该值像一次性会话值/运行期 ID，不能直接固化录制值；需要绑定上游响应、页面上下文或改为用户参数",
            "need_human_confirm": True,
        }

    raw_leaf = re.sub(r"[^a-z0-9]+", "", str(path or key).split(".")[-1].lower())
    if raw_leaf.endswith("id") and _is_const_value(value):
        return {
            "category": "runtime_var",
            "source_kind": "unknown",
            "source": {"kind": "selected_entity_id", "path": path},
            "editable": True,
            "exposed_to_user": False,
            "reason": "该字段像用户选择项对应的内部 ID；必须绑定页面/API 候选或明确改为系统常量，不能直接把录制 ID 暴露给调用方",
            "need_human_confirm": True,
        }

    if field.get("suggest_param") or value in _sample_value_set(samples):
        return {
            "category": "user_param",
            "source_kind": "user_input",
            "source": {"kind": "sample", "path": path},
            "editable": True,
            "exposed_to_user": True,
            "reason": "该值与用户录制时填写的表单值匹配，调用 Skill 时应作为用户参数",
            "need_human_confirm": False,
        }

    if not field.get("suggest_param") and (value == "" or _is_const_value(value)):
        return {
            "category": "system_const",
            "source_kind": "constant",
            "source": {"kind": "recorded_constant", "path": path},
            "editable": True,
            "exposed_to_user": False,
            "reason": "该字段未匹配到用户输入，且为空值或内部 ID/固定标识形态，默认作为系统常量隐藏",
            "need_human_confirm": True,
        }

    return {
        "category": "user_param",
        "source_kind": "unknown",
        "source": {},
        "editable": True,
        "exposed_to_user": True,
        "reason": "未识别到自动来源，默认作为用户参数暴露，后续可人工调整",
        "need_human_confirm": False,
    }


def _enum_options_for_param(sb) -> list | None:
    """把 SelectBinding 序列化成前端 + 运行期都好读的 enum_options:
    - 若有 option_map(label→value)→ 返回 [{label, value}] 字典列表(前端 DataList/Playbook 都能用)
    - 若只有 label → 返回 labels 字符串列表(向后兼容,前端显示用)
    - 没有枚举 → None
    通用,不绑具体业务。
    """
    if sb is None:
        return None
    om = sb.option_map if isinstance(sb.option_map, dict) else None
    opts = list(sb.options or [])
    out = []
    for o in opts:
        if isinstance(o, dict):
            label = str(o.get("label") or o.get("text") or o.get("name") or o.get("value") or "").strip()
            if label:
                out.append({"label": label, "value": (om or {}).get(label, o.get("value", label))})
        else:
            label = str(o or "").strip()
            if label:
                out.append({"label": label, "value": om[label]} if om and label in om else ({"label": label, "value": None} if om else label))
    if om:
        return out or None
    if opts:
        return out or None
    return None


def _enum_value_map_for_param(sb) -> dict | None:
    """label → value 映射;前端隐藏 prompt + 运行期 API 用同一份。"""
    if sb is None:
        return None
    om = sb.option_map if isinstance(sb.option_map, dict) else None
    if om:
        return dict(om)
    derived = _enum_option_map_from_options(list(sb.options or []))
    if derived and any(str(k) != str(v) for k, v in derived.items()):
        return derived
    return None


def _enum_label_value(opt) -> tuple[str, Any] | None:
    """兼容 list[str] 与 list[{label,value}],统一抽取调用侧显示名和真实提交值。"""
    if isinstance(opt, dict):
        label = str(opt.get("label") or opt.get("text") or opt.get("name") or opt.get("value") or "").strip()
        if not label:
            return None
        return label, opt.get("value", label)
    label = str(opt or "").strip()
    if not label:
        return None
    return label, label


def _enum_options_description(kind: str, options: list[Any] | None, value_map: dict[str, Any] | None = None) -> str | None:
    if not options:
        return None
    title = "页面枚举选项" if kind == "page_enum" else "枚举选项"
    if kind == "api_option":
        title = "接口候选选项"
    elif kind == "manual_enum":
        title = "手工枚举选项"
    elif kind == "static_enum":
        title = "固定枚举选项"
    elif kind == "form_option":
        title = "表单枚举选项"
    parts: list[str] = []
    seen: set[str] = set()
    for opt in options:
        pair = _enum_label_value(opt)
        if pair is None:
            continue
        label, value = pair
        if value_map and label in value_map:
            value = value_map[label]
        text = label if str(label) == str(value) else f"{label}={value}"
        if text in seen:
            continue
        seen.add(text)
        parts.append(text)
    if not parts:
        return None
    return f"{title}：{'、'.join(parts)}"


def _append_reason_detail(reason: str, detail: str | None) -> str:
    reason = str(reason or "")
    if not detail:
        return reason
    if detail in reason:
        return reason
    return f"{reason}；{detail}" if reason else detail


def _upsert_option_description(reason: str, detail: str | None) -> str:
    """Replace an older option snapshot from the same source instead of appending it."""
    reason = str(reason or "")
    if not detail:
        return reason
    prefix = detail.split("：", 1)[0]
    parts = [
        part for part in reason.split("；")
        if part.strip() and not part.strip().startswith(f"{prefix}：")
    ]
    parts.append(detail)
    return "；".join(parts)


_OPTION_DESCRIPTION_PREFIXES = (
    "页面枚举选项：", "接口候选选项：", "手工枚举选项：", "固定枚举选项：", "表单枚举选项：", "枚举选项：",
)


def _strip_option_descriptions(text: str | None) -> str:
    return "；".join(
        part for part in str(text or "").split("；")
        if part.strip() and not part.strip().startswith(_OPTION_DESCRIPTION_PREFIXES)
    )


def _enum_option_map_from_options(options: list[Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for opt in options or []:
        pair = _enum_label_value(opt)
        if pair and pair[1] is not None:
            out[pair[0]] = pair[1]
    return out


def _build_step_from_capture(
    req: dict,
    *,
    reads: list[dict],
    samples: dict,
    storage_state: dict | None,
    required_labels: set,
    page_enum_options: dict,
    step_index: int,
) -> FlowStep:
    method = (req.get("method") or "POST").upper()
    pd = req.get("post_data")
    body = _parse_body(pd)

    # 风险 + 语义角色
    role = classify_request_role(req)
    request_role = req.get("_request_role") or {}
    risk = request_role.get("risk_level") or role.get("riskLevel", "L3")

    def has_real_enum_source(sb: SelectBinding) -> bool:
        return bool(sb.options) or bool(sb.source_url and sb.value_key and sb.label_key)

    option_reads = _option_candidate_reads(reads or [])
    grounded_samples = dict(samples or {})
    for picked, raw_options in (page_enum_options or {}).items():
        if not isinstance(raw_options, dict):
            continue
        field_key = str(raw_options.get("field_key") or "").strip()
        selected = next((
            str(raw_options.get(key))
            for key in ("selected", "selected_label", "label", "value")
            if raw_options.get(key) not in (None, "")
        ), str(picked or ""))
        if field_key and selected:
            grounded_samples.setdefault(field_key, selected)

    # GET 请求：从 URL query string 提参,同时对 query 也跑 select 检测
    # (治"参数来源接口没识别":接口型 query 参数如 keyword=xxx / status=xxx 应该被识别为接口选择字段)
    if method == "GET" or body is None:
        list_paths: list[str] = []
        iden_raw: list[dict] = []
        flat_fields = _params_from_get_query(req, grounded_samples)
        # select/选人:在 query 参数名上做下拉检测,与 POST body 同套算法
        selects_raw = _detect_query_selects(req, grounded_samples, option_reads, page_enum_options)
    else:
        # 列表多选先识别
        list_selects = suggest_list_selects(pd, option_reads, grounded_samples)
        list_paths = [s["path"] for s in list_selects]

        # 字段拍平
        flat_fields = flatten_body(pd, samples, required_labels, collapse_paths=list_paths)

        # select/选人
        selects_raw = suggest_selects(pd, option_reads, grounded_samples, skip_paths=list_paths, fields=flat_fields) + list_selects
        apply_page_enum_options(selects_raw, page_enum_options, post_data=pd, fields=flat_fields)
        selects_raw += page_enum_selects(pd, page_enum_options, {s.get("path", "") for s in selects_raw}, fields=flat_fields)

        # identity(运行期重取)
        iden_raw = suggest_identity(pd, storage_state, samples)

    # select 字段配中文名
    sel_names = _select_name_for_step(selects_raw, samples)

    # BPMN 审批人命名兜底
    assignee_names = suggest_assignee_names(pd, option_reads, samples)

    # select 元数据
    selects_meta: list[SelectBinding] = []
    for s in selects_raw:
        selects_meta.append(SelectBinding(
            param="",
            path=s.get("path", ""),
            source_url=s.get("source_url", ""),
            value_key=s.get("value_key", ""),
            label_key=s.get("label_key", ""),
            category_key=s.get("category_key"),
            category_value=s.get("category_value"),
            multi=bool(s.get("multi")),
            element_template=s.get("element_template"),
            label_subkey=s.get("label_subkey"),
            count=int(s.get("count") or 0),
            options=list(s.get("options") or []),
            option_map=dict(s.get("option_map") or {}) or None,
            enum_source=s.get("enum_source"),
            enum_confirmed=s.get("enum_confirmed"),
            id_path=s.get("id_path"),
            id_tokens=s.get("id_tokens"),
        ))

    # identity
    identity_meta = [
        IdentityBinding(
            path=i.get("path", ""),
            source=i.get("source", ""),
            tokens=i.get("tokens"),
            value=i.get("value"),
        )
        for i in iden_raw
    ]
    identity_paths = {i.path for i in identity_meta if i.path}

    # system_values
    sys_values: list[SystemValue] = []
    if body is not None:
        for path, tokens, _sv, raw in _leaf_paths(body):
            key = path.split(".")[-1].split("[")[0]
            if _is_system_timestamp(key, raw):
                kind = "now_ms" if len(str(raw)) == 13 else "now_s"
                sys_values.append(SystemValue(path=path, tokens=tokens, kind=kind))
    system_paths = {sv.path for sv in sys_values}
    select_paths = {s.path for s in selects_meta if s.path and has_real_enum_source(s)}
    select_id_paths = {s.id_path for s in selects_meta if s.id_path and has_real_enum_source(s)}
    select_by_path = {s.path: s for s in selects_meta if s.path and has_real_enum_source(s)}
    select_by_id_path = {s.id_path: s for s in selects_meta if s.id_path and has_real_enum_source(s)}

    # success_rule
    sr = None
    if req.get("response_json") is not None:
        sr = infer_success_rule([{"json": req.get("response_json")}])

    # params
    params: list[ParamField] = []
    for f in flat_fields:
        path = f.get("path", "")
        wire_type = f.get("wire_type") or f.get("type") or _infer_type_from_value(f.get("value")) or "string"
        ptype = wire_type
        if path in list_paths:
            ptype = "list-enum"
        elif path in select_paths:
            ptype = "enum"
        select_meta = select_by_path.get(path)

        # 字段中文名优先级
        nm = f.get("suggest_name") or f.get("key") or ""
        if path in sel_names:
            nm = sel_names[path]
            ns = "sample"
        elif path in assignee_names and (nm == f.get("key") or _looks_internal(nm)):
            nm = assignee_names[path]
            ns = "assignee"
        else:
            ns = f.get("name_source") or "auto"

        source_guess = _param_source_guess(
            field=f,
            path=path,
            key=nm,
            method=method,
            identity_paths=identity_paths,
            system_paths=system_paths,
            select_paths=select_paths,
            select_id_paths=select_id_paths,
            select_by_path=select_by_path,
            select_by_id_path=select_by_id_path,
            samples=samples,
            request_headers=req.get("headers") or {},
        )
        enum_options = _enum_options_for_param(select_meta)
        enum_value_map = _enum_value_map_for_param(select_meta)
        if select_meta is not None and select_meta.enum_source == "dom" and enum_options:
            option_labels = {
                str(pair[0]) for option in enum_options
                if (pair := _enum_label_value(option)) is not None
            }
            submitted_is_label = str(f.get("value") or "") in option_labels
            mapped_labels = {str(key) for key in (enum_value_map or {})}
            if not submitted_is_label and not option_labels.issubset(mapped_labels):
                # Keep every captured label as evidence/description, but do not
                # pretend unseen numeric/short-code values follow DOM order.
                select_meta.enum_confirmed = False
        enum_description = _enum_options_description(source_guess["source_kind"], enum_options, enum_value_map)
        evidence = []
        if enum_description and source_guess["source_kind"] in _OPTION_SOURCE_KINDS:
            evidence.append({
                "kind": "enum_options",
                "source_kind": source_guess["source_kind"],
                "option_count": len(enum_options or []),
                "options": enum_options or [],
                "option_map": enum_value_map or {},
            })

        params.append(ParamField(
            path=path,
            key=nm,
            label=nm,
            value=str(f.get("value") or ""),
            type=ptype,
            wire_type=wire_type,
            required=bool(f.get("required")),
            confidence=float(f.get("confidence") or 0.0),
            confidence_tier=f.get("confidence_tier") or "auto",
            name_source=ns,
            # **系统化**:同时投递 label 列表 + label→value 反查表,确保前端能渲染 + 运行期能做 name→ID 解析。
            enum_options=enum_options,
            enum_value_map=enum_value_map,
            category=source_guess["category"],
            source_kind=source_guess["source_kind"],
            source={
                **source_guess["source"],
                **({
                    "enum_source": select_meta.enum_source,
                    "enum_confirmed": select_meta.enum_confirmed,
                } if select_meta is not None else {}),
            },
            editable=bool(source_guess["editable"]),
            exposed_to_user=bool(source_guess["exposed_to_user"]),
            default_value=f.get("value"),
            reason=_append_reason_detail(source_guess["reason"], enum_description),
            description=enum_description,
            need_human_confirm=bool(
                source_guess["need_human_confirm"]
                or (
                    source_guess["source_kind"] == "page_enum"
                    and select_meta is not None
                    and select_meta.enum_confirmed is False
                )
            ),
            evidence=evidence,
        ))

    # 补回 select 元数据的 param 字段
    path2key = {p.path: p.key for p in params}
    for sb, sraw in zip(selects_meta, selects_raw):
        sb.param = path2key.get(sraw.get("path", ""), "")

    # sample_inputs
    sample_inputs = {p.key: p.value for p in params if p.value}

    # source_meta
    full_url = _request_url_with_query(req)
    source_meta = {
        "method": method,
        "url": full_url,
        "query": dict(req.get("query") or _request_query_values(req)),
        "headers_count": len(req.get("headers") or {}),
        "captured_at": req.get("captured_at"),
        "response_status": req.get("response_status"),
        "request_index": req.get("index"),
        "request_id": str(req.get("request_id") or req.get("id") or req.get("index") or ""),
        "role": request_role.get("role", ""),
        "keep": request_role.get("keep"),
        "keep_reason": request_role.get("keep_reason") or request_role.get("reason", ""),
        "filter_reason": request_role.get("filter_reason", ""),
        "confidence": request_role.get("confidence"),
        "evidence": request_role.get("evidence"),
    }

    path = _path_from_url(full_url)

    return FlowStep(
        name=_default_step_name(req),
        method=method,
        url=full_url,
        path=path,
        headers=extract_auth_headers(req.get("headers")),
        content_type=req.get("content_type") or "application/json",
        body_source=pd or "",
        body_template=None,
        params=params,
        selects=selects_meta,
        identity=identity_meta,
        system_values=sys_values,
        success_rule=sr,
        response_json=req.get("response_json"),
        risk_level=risk,
        semantic_role=request_role.get("semantic_role") or role.get("semanticRole", ""),
        source_meta=source_meta,
        sample_inputs=sample_inputs,
    )


def _query_param_type(key: str, value: Any) -> str:
    text = str(value or "").strip()
    key_text = str(key or "").lower()
    if re.search(r"(?:date|time|day|日期|时间)", key_text):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return "date"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ t]\d{2}:\d{2}(?::\d{2})?)?", text, re.I):
            return "datetime"
    if text.lower() in {"true", "false"}:
        return "boolean"
    if re.fullmatch(r"-?(?:\d+|\d+\.\d+)", text) and not re.search(
        r"(?:id|code|key|type|status|no|number)", key_text,
    ):
        return "number"
    return "string"


def _query_param_label(key: str, value: Any, samples: dict | None = None) -> str:
    exact = [
        str(label) for label, sample in (samples or {}).items()
        if sample not in (None, "") and str(sample).strip() == str(value or "").strip()
    ]
    return exact[0] if len(exact) == 1 else str(key or "")


def _request_query_values(req: dict) -> dict[str, list[Any]]:
    raw = req.get("query")
    if isinstance(raw, dict) and raw:
        return {
            str(key): list(value) if isinstance(value, list) else [value]
            for key, value in raw.items()
        }
    try:
        return parse_qs(urlparse(str(req.get("url") or req.get("path") or "")).query, keep_blank_values=True)
    except Exception:  # noqa: BLE001
        return {}


def _request_url_with_query(req: dict) -> str:
    url = str(req.get("url") or req.get("path") or "")
    if "?" in url or not (query := _request_query_values(req)):
        return url
    return f"{url}?{urlencode(query, doseq=True)}"


def _params_from_get_query(req: dict, samples: dict | None = None) -> list[dict]:
    """GET 请求：从 URL query string 提参。"""
    qs = _request_query_values(req)
    if not qs:
        return []
    out: list[dict] = []
    for k, vals in qs.items():
        v = (vals or [""])[0]
        label = _query_param_label(k, v, samples)
        out.append({
            "path": f"query.{k}",
            "key": label,
            "value": v,
            "type": _query_param_type(k, v),
            "required": True,
            "confidence": 0.9 if label != k else 0.75,
            "confidence_tier": "grounded" if label != k else "auto",
            "name_source": "sample" if label != k else "auto",
        })
    return out


# 一个 query 路径(如 query.status)上的下拉值若在 reads 候选列表里有命中,就被识别为 select
def _detect_query_selects(req: dict, samples: dict | None,
                          reads: list[dict], page_enum_options: dict | None) -> list[dict]:
    """GET 请求的 query 参数本身也可能是某接口的下拉/枚举字段(典型如 /system/user/page?status=active)。
    把 query 视为扁平 key=值 结构,与 reads 候选做名→label 桥接、同上也试 DOM 选项。
    把命中路径重写为 `query.<key>` 以与 _params_from_get_query 的 path 对齐。通用,不挑系统。

    关键差异:接口型 select 既可能按 label 提交(显示名),也可能按 value 提交(状态码)。所以这里除
    suggest_selects 之外,还做一道 value-形态匹配置信信号 —— 当 query 值与 reads 候选的某
    「value/字典值字段」精准相等,即便没有 label 佐证,也以低置信度挂上 enum 标记,前端会把它
    当作低置信度 enum 项处理。"""
    flat = _params_from_get_query(req, samples)
    if not flat:
        return []
    selectable_flat = [
        field for field in flat
        if not _looks_pagination_field(str(field.get("key") or ""), str(field.get("path") or ""))
    ]
    if not selectable_flat:
        return []
    syn_body: dict[str, Any] = {f.get("key"): f.get("value") for f in selectable_flat if f.get("key")}
    syn_pd = json.dumps(syn_body, ensure_ascii=False)
    selects_raw = suggest_selects(syn_pd, reads or [], samples, skip_paths=[], fields=selectable_flat) + []
    apply_page_enum_options(selects_raw, page_enum_options, post_data=syn_pd, fields=selectable_flat)
    selects_raw += page_enum_selects(syn_pd, page_enum_options,
                                     {s.get("path", "") for s in selects_raw}, fields=selectable_flat)

    # 第二道:value 形态兜底(suggest_selects 当 value 与 label 不挂钩时容易漏)——
    # query 值若与 reads 候选列表里某 value 字段精准相等,就挂上 select 标记。
    hits_paths = {s.get("path") for s in selects_raw}
    for f in selectable_flat:
        k = f.get("key") or ""
        v = str(f.get("value") or "")
        if not k or not v or f"query.{k}" in hits_paths:
            continue
        if _looks_runtime_var_key(k):  # taskId/uuid 这类不要凑数
            continue
        match = _match_query_field_to_reads(k, v, reads or [])
        if match is None:
            continue
        # 找到则挂 enum/api 标记
        sel = {
            "path": f"query.{k}",
            "source_url": match["source_url"],
            "value_key": match.get("value_key", "value"),
            "label_key": match.get("label_key", "label"),
            "count": match.get("count", 0),
            "options": match.get("options", []),
            "option_map": match.get("option_map", {}),
            "enum_source": "api",
            "enum_confirmed": False,
            "confidence": 0.6,
            "value": v,
            "label": v,
        }
        selects_raw.append(sel)
        hits_paths.add(sel["path"])

    # 重写 path 为 query.<key>,保持与 _params_from_get_query 的输出对齐
    for s in selects_raw or []:
        leaf_key = (s.get("path") or "").split(".")[-1].split("[")[0]
        if leaf_key and (s.get("path") or "").startswith("query.") is False:
            new_path = f"query.{leaf_key}"
            s["path"] = new_path
            if isinstance(s.get("id_path"), str) and s["id_path"]:
                id_leaf = s["id_path"].split(".")[-1].split("[")[0]
                if id_leaf:
                    s["id_path"] = f"query.{id_leaf}"
    return selects_raw


def _looks_runtime_var_key(key: str) -> bool:
    """query 字段名看起来像 token/taskId/uuid/随机码,则不当 enum 候选"""
    import re as _re
    if not key:
        return False
    k = key.lower()
    return bool(_re.search(r"(taskid|conversationid|sessionid|uuid|traceid|nonce|appcode|token|accesstoken|refreshtoken|"
                            r"instanceid|procinstance|wybs)$", k)) or bool(_re.fullmatch(r"[a-z0-9]{20,}", k))


def _match_query_field_to_reads(key: str, value: str, reads: list[dict]) -> dict | None:
    """对所有 reads 候选接口,尝试把 query 值 value 匹配到某候选的 value 字段 → 返回 {source_url, options,
    count, value_key, label_key, option_map}。命中要求:候选列表项 value/valueCode 字段与 query 值相等。
    """
    out: dict | None = None
    seen_options: set[str] = set()
    options: list[dict] = []
    for r in reads:
        url = r.get("url") or ""
        items = None
        raw_json = r.get("json") if isinstance(r.get("json"), (dict, list)) else None
        if raw_json is None:
            continue
        # 拿到 list 形态
        if isinstance(raw_json, list):
            items = raw_json
        elif isinstance(raw_json, dict):
            for cand_key in ("data", "rows", "list", "items", "result"):
                v = raw_json.get(cand_key)
                if isinstance(v, list) and v:
                    items = v
                    break
        if not items or not isinstance(items[0], dict):
            continue
        # 找 value 字段(key 名 hit 里 value/value/code/dictValue 这类)
        value_keys_to_try = ["value", "valueCode", "dictValue", "id", "code"]
        label_keys_to_try = ["label", "labelName", "dictLabel", "name", "text", "title"]
        hit_vk = hit_lk = None
        for vk_try in value_keys_to_try:
            for it in items[:50]:
                if isinstance(it.get(vk_try), str) and it.get(vk_try) == value:
                    hit_vk = vk_try
                    break
            if hit_vk:
                break
        if not hit_vk:
            # 不挑字段名,直接找一个字符串字段与 value 相等的
            for it in items[:50]:
                for kk, vv in it.items():
                    if isinstance(vv, str) and vv == value and not _looks_runtime_var_key(kk):
                        hit_vk = kk
                        break
                if hit_vk:
                    break
        if not hit_vk:
            continue
        # 找 label 字段
        for lk_try in label_keys_to_try:
            sample = items[0]
            if isinstance(sample.get(lk_try), str) and lk_try != hit_vk:
                hit_lk = lk_try
                break
        if not hit_lk:
            for kk in items[0].keys():
                if kk != hit_vk and isinstance(items[0].get(kk), str):
                    hit_lk = kk
                    break

        for it in items[:200]:
            lv = str(it.get(hit_vk, ""))
            ll = str(it.get(hit_lk, "")) if hit_lk else ""
            if not lv or lv in seen_options:
                continue
            seen_options.add(lv)
            options.append({"label": ll or lv, "value": lv})
        if options and out is None:
            out = {"source_url": url, "options": [o["label"] for o in options[:50]],
                   "count": len(options), "value_key": hit_vk, "label_key": hit_lk or "label",
                   "option_map": {o["label"]: o["value"] for o in options[:50]}}
    return out


def _is_business_get(r: dict) -> bool:
    """判断是否是业务型 GET 请求（响应被后续步骤引用）。"""
    if (r.get("method") or "").upper() != "GET":
        return False
    if not r.get("response_json"):
        return False
    rj = r.get("response_json")
    if isinstance(rj, list):
        return False
    if isinstance(rj, dict):
        if isinstance(rj.get("data"), list):
            return False
    return True


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_NOISE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".map", ".woff", ".woff2", ".ico")
_NOISE_SEGS = {"heartbeat", "metrics", "metric", "track", "trace", "analytics", "log", "logs", "beacon", "ping"}
_OPTION_SEGS = {"list", "options", "option", "dict", "select", "candidates", "tree", "users", "roles"}
_WRITE_HINT_SEGS = {
    "submit", "save", "create", "update", "send", "apply", "start", "commit",
    "confirm", "approve", "complete", "finish",
}
_BORING_LINK_VALUES = {"", "0", "1", "true", "false", "200", "ok", "success", "none", "null"}


def _request_path(req: dict) -> str:
    url = req.get("url") or req.get("path") or ""
    try:
        return urlparse(url).path if str(url).startswith("http") else str(url).split("?", 1)[0]
    except Exception:
        return str(url or "")


def _request_segments(req: dict) -> set[str]:
    return {s.lower() for s in re.split(r"[^a-zA-Z0-9]+", _request_path(req)) if s}


def _request_values(req: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    body = _parse_body(req.get("post_data"))
    if body is not None:
        for path, _tokens, sv, _raw in _leaf_paths(body):
            out.append((path, str(sv)))
    query = dict(req.get("query") or {})
    if not query:
        try:
            query = {k: vals[0] if vals else "" for k, vals in parse_qs(urlparse(req.get("url") or "").query).items()}
        except Exception:
            query = {}
    for k, v in query.items():
        if isinstance(v, list):
            for i, item in enumerate(v):
                out.append((f"query.{k}[{i}]", str(item)))
        else:
            out.append((f"query.{k}", str(v)))
    return out


def _response_values(req: dict) -> list[tuple[str, str]]:
    data = req.get("response_json")
    if data is None:
        return []
    try:
        return [(path, str(sv)) for path, _tokens, sv, _raw in _leaf_paths(data)]
    except Exception:
        return []


def _trace_pos(req: dict, trace: list[dict]) -> int:
    for i, item in enumerate(trace):
        if item is req:
            return i
    idx = req.get("index")
    if idx is not None:
        for i, item in enumerate(trace):
            if item.get("index") == idx:
                return i
    return -1


def _useful_link_value(value: str) -> bool:
    v = str(value or "").strip().lower()
    return bool(v and v not in _BORING_LINK_VALUES and len(v) >= 3)


def _response_referenced_later(req: dict, trace: list[dict]) -> dict | None:
    pos = _trace_pos(req, trace)
    if pos < 0:
        return None
    response_values = [(p, v) for p, v in _response_values(req) if _useful_link_value(v)]
    if not response_values:
        return None
    # H23 修复:把后续 trace 的值提前合并到一个 map(保留每个 value 第一次出现的 target + value),
    # 避免每 later 重算;O(N+M) 比原 O(N²) 快一个量级。**source_path 必须保留 response 那一端的字段路径**(消费方依赖),
    # 所以 path 在命中时再从 response_values 里取。
    pool_values: dict[str, dict] = {}      # value → {target_url, target_method}
    for later in trace[pos + 1:]:
        for _p, v in _request_values(later):
            if _useful_link_value(v) and v not in pool_values:
                pool_values[v] = {
                    "target_url": later.get("url") or "",
                    "target_method": (later.get("method") or "").upper(),
                }
    for path, value in response_values:
        hit = pool_values.get(value)
        if hit is not None:
            return {"source_path": path, "value": value,
                    "target_url": hit["target_url"], "target_method": hit["target_method"]}
    return None


def _sample_hit_count(req: dict, samples: dict | None) -> int:
    values = {v for _p, v in _request_values(req)}
    return sum(1 for v in (samples or {}).values() if v not in (None, "") and str(v) in values)


def _is_noise_request(req: dict) -> bool:
    path = _request_path(req).lower()
    if path.endswith(_NOISE_EXTS):
        return True
    return bool(_request_segments(req) & _NOISE_SEGS)


def _looks_graphql_request(req: dict) -> bool:
    url = str(req.get("url") or req.get("path") or "").lower()
    if "graphql" in url:
        return True
    payload = req.get("post_data")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:  # noqa: BLE001
            payload = {}
    if not isinstance(payload, dict):
        return False
    query = str(payload.get("query") or "").lstrip()
    return query.startswith(("query", "mutation", "subscription")) or query.startswith("{")


def _role_row(req: dict, *, role: str, keep: bool, reason: str, confidence: float,
              semantic: dict | None = None, evidence: dict | None = None) -> dict:
    url = req.get("url") or ""
    row = {
        "index": req.get("index"),
        "method": (req.get("method") or "").upper(),
        "url": url,
        "path": _path_from_url(url),
        "role": role,
        "keep": keep,
        "reason": reason,
        "keep_reason": reason if keep else "",
        "filter_reason": "" if keep else reason,
        "confidence": confidence,
    }
    if semantic:
        row.update({
            "semantic_role": semantic.get("semanticRole", ""),
            "side_effect": semantic.get("sideEffect", ""),
            "risk_level": semantic.get("riskLevel", ""),
        })
    if evidence:
        row["evidence"] = evidence
    return row


def _list_payload_is_business_records(req: dict, items: list[dict] | list[Any]) -> bool:
    sample = next((item for item in items[:5] if isinstance(item, dict)), None)
    keys = {
        re.sub(r"[^a-z0-9]+", "", str(key).lower())
        for key in (sample or {}).keys()
    }
    strong_business_keys = {
        "date", "day", "startdate", "enddate", "applydate", "reportdate",
        "content", "workcontent", "reason", "remark", "description", "hours",
        "approvestatus", "approvalstatus", "projectid", "projectname", "filled", "missing",
    }
    segs = _request_segments(req)
    business_segments = {
        "report", "daily", "workhour", "worktime", "apply", "approval", "leave",
        "reimburse", "expense", "order", "record", "detail", "history", "task",
    }
    option_segments = {
        "dict", "dictionary", "option", "options", "select", "candidate", "candidates",
        "tree", "simple", "simplelist", "user", "users", "dept", "department", "role", "roles", "employee",
    }
    if keys & strong_business_keys:
        return True
    return bool(segs & business_segments) and not bool(segs & option_segments)


def classify_network_request(req: dict, trace: list[dict] | None = None,
                             samples: dict | None = None) -> dict:
    """给网络请求打角色、保留决策和原因。

    这里不修改原始请求，只产出解释性事实。后续 FlowSpec 用 keep=true 的请求建主流程，
    所有请求的判定都会进入 meta.request_roles 供人工核对。
    """
    trace = trace or [req]
    method = (req.get("method") or "GET").upper()
    semantic = classify_request_role(req)
    url = req.get("url") or ""

    if _is_noise_request(req):
        return _role_row(req, role="noise", keep=False,
                         reason="静态资源、心跳或埋点请求，不进入业务流程",
                         confidence=0.98, semantic=semantic)

    ct = (req.get("content_type") or (req.get("headers") or {}).get("content-type") or "").lower()
    if ct.startswith("multipart/") or _request_segments(req) & {"upload", "file", "files", "attachment", "attachments"}:
        return _role_row(req, role="unsupported_upload", keep=False,
                         reason="文件/附件上传请求已放行真发；当前 FlowSpec 暂不自动复用 multipart 文件内容",
                         confidence=0.96, semantic=semantic)

    if _looks_graphql_request(req):
        return _role_row(req, role="unsupported_graphql", keep=False,
                         reason="GraphQL 请求可能包含多操作与动态 selection set；当前 FlowSpec 暂不自动复用",
                         confidence=0.92, semantic=semantic)

    if looks_like_auth_write(url, req.get("post_data")):
        return _role_row(req, role="auth", keep=False,
                         reason="登录/鉴权/令牌刷新请求，只作为身份来源，不进入业务流程",
                         confidence=0.96, semantic=semantic)

    response_ref = _response_referenced_later(req, trace)
    list_items = as_list_payload(req.get("response_json"))
    segs = _request_segments(req)

    if method not in _WRITE_METHODS:
        if list_items is not None and _list_payload_is_business_records(req, list_items):
            return _role_row(req, role="business_get", keep=True,
                             reason="列表响应包含日期/状态/业务记录字段，作为独立查询能力候选",
                             confidence=0.93, semantic=semantic)
        if list_items is not None or segs & _OPTION_SEGS:
            count = len(list_items or [])
            return _role_row(req, role="read_option", keep=False,
                             reason=f"读接口返回候选列表/枚举源({count}项)，作为字段来源，不进入主流程",
                             confidence=0.9, semantic=semantic)
        if response_ref:
            return _role_row(req, role="business_get", keep=True,
                             reason="GET 响应值被后续业务请求引用，作为前置步骤保留",
                             confidence=0.96, semantic=semantic, evidence=response_ref)
        if (
            segs & {"page", "list", "search", "query", "records", "history", "detail"}
            and segs & {"report", "daily", "workhour", "worktime", "apply", "approval", "leave", "reimburse", "expense", "order", "record", "task"}
            and not segs & _OPTION_SEGS
        ):
            return _role_row(req, role="business_get", keep=True,
                             reason="业务分页/搜索接口即使当前结果为空，也保留为独立查询能力",
                             confidence=0.9, semantic=semantic)
        return _role_row(req, role="read_context", keep=False,
                         reason="普通读接口，未发现后续业务请求依赖，默认不进入主流程",
                         confidence=0.68, semantic=semantic)

    if semantic.get("semanticRole") == "destructive":
        return _role_row(req, role="business_write", keep=True,
                         reason="危险写请求，保留事实并交给发布层/人工审核拦截",
                         confidence=0.98, semantic=semantic)

    if looks_like_read_request(url, req.get("post_data")):
        if response_ref:
            return _role_row(req, role="read_context", keep=True,
                             reason="POST 查询响应被后续业务请求引用，作为前置上下文步骤保留",
                             confidence=0.88, semantic=semantic, evidence=response_ref)
        if list_items is not None and _list_payload_is_business_records(req, list_items):
            return _role_row(req, role="business_get", keep=True,
                             reason="POST 查询返回业务记录列表，作为独立查询能力候选",
                             confidence=0.93, semantic=semantic)
        if list_items is not None or segs & _OPTION_SEGS:
            count = len(list_items or [])
            return _role_row(req, role="read_option", keep=False,
                             reason=f"POST 查询返回候选列表/枚举源({count}项)，作为字段来源，不进入主流程",
                             confidence=0.9, semantic=semantic)
        return _role_row(req, role="read_context", keep=False,
                         reason="POST 查询/搜索类接口，未发现被后续步骤依赖，默认不进入主流程",
                         confidence=0.72, semantic=semantic)

    sample_hits = _sample_hit_count(req, samples)
    body = _parse_body(req.get("post_data"))
    if sample_hits > 0 or segs & _WRITE_HINT_SEGS:
        role = "submit_anchor" if sample_hits > 0 else "business_write"
        reason = ("请求体包含用户录制输入值，判定为提交锚点"
                  if sample_hits > 0 else "写请求路径命中业务提交/保存/发送语义，保留为业务步骤")
        evidence = {"sample_hits": sample_hits} if sample_hits > 0 else None
        return _role_row(req, role=role, keep=True, reason=reason,
                         confidence=0.93 if sample_hits > 0 else 0.86,
                         semantic=semantic, evidence=evidence)

    if body is not None or semantic.get("sideEffect") == "write":
        return _role_row(req, role="business_write", keep=True,
                         reason="非查询写请求，保守保留为业务步骤",
                         confidence=0.78, semantic=semantic)

    return _role_row(req, role="read_context", keep=False,
                     reason="缺少可解析请求体且未发现业务依赖，默认过滤",
                     confidence=0.55, semantic=semantic)


def _request_role_key(req: dict) -> Any:
    return req.get("index") if req.get("index") is not None else id(req)


def _preread_dedupe_key(req: dict) -> tuple[str, str]:
    return ((req.get("method") or "GET").upper(), _request_path(req))


def _business_filter_count(req: dict) -> int:
    """Count caller-meaningful filters without treating pagination as business input."""
    query = req.get("query")
    if not isinstance(query, dict):
        try:
            query = parse_qs(urlparse(str(req.get("url") or "")).query, keep_blank_values=True)
        except Exception:  # noqa: BLE001
            query = {}
    return sum(
        1 for key, value in (query or {}).items()
        if not _looks_pagination_field(str(key), f"query.{key}")
        and any(str(item).strip() for item in (value if isinstance(value, list) else [value]))
    )


def _preread_candidate_score(req: dict) -> tuple[int, int, int, float]:
    """Prefer the searched request over an initial/refresh request on the same endpoint."""
    business_filters = _business_filter_count(req)
    query_size = len(req.get("query") or _params_from_get_query(req))
    sequence = _request_sequence_value(req.get("sequence", req.get("index"))) or 0.0
    return (
        business_filters,
        query_size,
        1 if req.get("response_json", req.get("json")) is not None else 0,
        sequence,
    )


def _dedupe_preread_candidates(preread_cands: list[dict]) -> list[dict]:
    """同一路径反复触发时保留业务条件最完整的一次，序号仅作为同分兜底。"""
    best_by_path: dict[tuple[str, str], dict] = {}
    for req in preread_cands:
        key = _preread_dedupe_key(req)
        current = best_by_path.get(key)
        if current is None or _preread_candidate_score(req) >= _preread_candidate_score(current):
            best_by_path[key] = req
    return [
        req for req in preread_cands
        if best_by_path.get(_preread_dedupe_key(req)) is req
    ]


def _attach_request_role(req: dict, role: dict) -> dict:
    out = dict(req)
    out["_request_role"] = role
    return out


def _request_graph_entry(req: dict, role: dict, *, include_payload: bool = False) -> dict[str, Any]:
    """给工作台展示的请求事实条目。

    request_graph 是不可变的捕获事实库；能力/步骤只引用这些 request_index/request_id。
    """
    request_index = req.get("index")
    response_json = req.get("response_json", req.get("json"))
    out = {
        "request_index": request_index,
        "request_id": str(req.get("request_id") or req.get("id") or request_index or uuid.uuid4().hex[:8]),
        "page_id": req.get("page_id") or req.get("pageId"),
        "frame_id": req.get("frame_id") or req.get("frameId"),
        "sequence": req.get("sequence", request_index),
        "method": (req.get("method") or "").upper(),
        "url": req.get("url") or "",
        "path": _request_path(req),
        "role": role.get("role") or "",
        "keep": bool(role.get("keep")),
        "reason": role.get("reason") or role.get("keep_reason") or role.get("filter_reason") or "",
        "confidence": float(role.get("confidence") or 0.0),
        "evidence": role.get("evidence") or {},
        "state": "captured",
        "materialized_step_id": req.get("materialized_step_id"),
    }
    if include_payload:
        out.update({
            "headers": dict(req.get("headers") or {}),
            "query": dict(req.get("query") or {}),
            "content_type": req.get("content_type") or "",
            "post_data": req.get("post_data"),
            "response_status": req.get("response_status", req.get("status")),
            "response_json": response_json,
            "response_schema": _schema_from_response_value(response_json) if response_json is not None else {},
        })
    return out


def _request_graph_signature(req: dict) -> tuple[str, str]:
    return ((req.get("method") or "GET").upper(), _request_path(req))


def _build_request_graph(
    captured_requests: list[dict],
    request_roles: list[dict],
    selected_keys: set[Any],
) -> dict[str, list[dict[str, Any]]]:
    all_requests: list[dict[str, Any]] = []
    selected_steps: list[dict[str, Any]] = []
    candidate_reads: list[dict[str, Any]] = []
    filtered_requests: list[dict[str, Any]] = []
    selected_signatures: set[tuple[str, str]] = set()
    for req, role in zip(captured_requests or [], request_roles or []):
        key = _request_role_key(req)
        role_name = role.get("role") or ""
        all_requests.append(_request_graph_entry(req, role, include_payload=True))
        if key in selected_keys:
            selected_steps.append(_request_graph_entry(req, role, include_payload=True))
            selected_signatures.add(_request_graph_signature(req))
            continue
        if role_name in {"read_option", "read_context", "business_get"} and req.get("response_json", req.get("json")) is not None:
            if _request_graph_signature(req) in selected_signatures:
                filtered_requests.append(_request_graph_entry(req, role, include_payload=True))
                continue
            candidate_reads.append(_request_graph_entry(req, role, include_payload=True))
            continue
        filtered_requests.append(_request_graph_entry(req, role, include_payload=True))
    return {
        "all_requests": all_requests,
        "selected_steps": selected_steps,
        "candidate_reads": candidate_reads,
        "filtered_requests": filtered_requests,
    }


def _request_fact_key(entry: dict[str, Any]) -> str:
    request_id = str(entry.get("request_id") or "").strip()
    if request_id:
        return request_id
    request_index = entry.get("request_index")
    if request_index is not None:
        return f"idx:{request_index}"
    raw = json.dumps({
        "method": (entry.get("method") or "").upper(),
        "path": entry.get("path") or entry.get("url") or "",
        "sequence": entry.get("sequence"),
    }, ensure_ascii=False, sort_keys=True, default=str)
    return "sig:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _request_fact_from_graph_entry(entry: dict[str, Any]) -> RequestFact:
    rid = _request_fact_key(entry)
    payload = dict(entry)
    payload.update({
        "request_id": rid,
        "request_index": entry.get("request_index"),
        "page_id": entry.get("page_id"),
        "frame_id": entry.get("frame_id"),
        "sequence": entry.get("sequence", entry.get("request_index")),
        "method": (entry.get("method") or "").upper(),
        "url": entry.get("url") or "",
        "path": entry.get("path") or entry.get("url") or "",
        "query": dict(entry.get("query") or {}),
        "headers": dict(entry.get("headers") or {}),
        "content_type": entry.get("content_type") or "",
        "post_data": entry.get("post_data"),
        "response_status": entry.get("response_status"),
        "response_json": entry.get("response_json"),
        "response_schema": dict(entry.get("response_schema") or {}),
        "timestamp": entry.get("timestamp") or entry.get("captured_at"),
    })
    return RequestFact.model_validate(payload)


def _request_analysis_from_graph_entry(entry: dict[str, Any], bucket: str) -> RequestAnalysis:
    payload = dict(entry)
    role = str(entry.get("role") or "")
    semantic_roles = [str(value) for value in (entry.get("semantic_roles") or []) if str(value)]
    role_semantic = {
        "business_get": "business_query",
        "read_option": "option_source",
        "read_context": "context_read",
        "submit_anchor": "business_write",
        "business_write": "business_write",
    }.get(role)
    if role_semantic and role_semantic not in semantic_roles:
        semantic_roles.append(role_semantic)
    payload.update({
        "request_id": _request_fact_key(entry),
        "role": role,
        "semantic_roles": semantic_roles,
        "keep": bool(entry.get("keep")),
        "reason": entry.get("reason") or "",
        "confidence": float(entry.get("confidence") or 0.0),
        "evidence": dict(entry.get("evidence") or {}),
        "bucket": bucket,
        "filter_reason": entry.get("filter_reason") or "",
    })
    return RequestAnalysis.model_validate(payload)


def _is_api_like_graph_entry(entry: dict[str, Any]) -> bool:
    path = _request_path(entry).lower()
    if not path:
        return False
    if re.search(r"\.(?:css|js|mjs|map|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|eot|html?|txt|xml)$", path):
        return False
    role = str(entry.get("role") or "")
    if role in {"noise", "auth"}:
        return False
    if role in {"submit_anchor", "business_write", "business_get", "read_context", "read_option"}:
        return True
    if entry.get("response_json") is not None:
        return True
    return bool(re.search(r"^/?(?:api|admin-api|appgateway|gsgl|oa|bpm|system|workflow|process|v1|v2)\b", path))


def _option_sources_from_page_enum_options(page_enum_options: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not page_enum_options:
        return []
    return [{"kind": "page_enum_options", "options": page_enum_options}]


def _page_enum_options_from_request_facts(request_facts: RequestFacts | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for source in (request_facts.option_sources if request_facts else []) or []:
        if not isinstance(source, dict):
            continue
        if source.get("kind") == "page_enum_options" and isinstance(source.get("options"), dict):
            out.update(source.get("options") or {})
    return out


def _request_facts_from_graph(
    graph: dict[str, Any],
    *,
    diagnostics: list[dict[str, Any]] | None = None,
    page_enum_options: dict[str, Any] | None = None,
) -> RequestFacts:
    facts_by_id: dict[str, RequestFact] = {}
    analysis: dict[str, RequestAnalysis] = {}
    usage: dict[str, RequestUsage] = {}
    bucket_rank = {
        "all_requests": 0,
        "filtered_requests": 1,
        "candidate_reads": 2,
        "selected_steps": 3,
    }
    for bucket in ("all_requests", "filtered_requests", "candidate_reads", "selected_steps"):
        for entry in graph.get(bucket) or []:
            if not isinstance(entry, dict):
                continue
            if not _is_api_like_graph_entry(entry):
                continue
            rid = _request_fact_key(entry)
            fact = _request_fact_from_graph_entry(entry)
            prev = facts_by_id.get(rid)
            # 优先保留带 payload/schema 的事实；没有则用后出现的更具体条目补齐。
            if prev is None or (fact.response_json is not None and prev.response_json is None):
                facts_by_id[rid] = fact
            ana = _request_analysis_from_graph_entry(entry, bucket)
            prev_ana = analysis.get(rid)
            if prev_ana is None or bucket_rank.get(bucket, 0) >= bucket_rank.get(prev_ana.bucket, 0):
                analysis[rid] = ana
            materialized_step_id = str(entry.get("materialized_step_id") or "")
            state = entry.get("state") or ("materialized" if materialized_step_id else "captured")
            prev_usage = usage.get(rid) or RequestUsage(request_id=rid)
            if materialized_step_id:
                prev_usage.materialized_step_id = materialized_step_id
                prev_usage.state = "materialized"
            elif bucket == "selected_steps" and prev_usage.state == "captured":
                prev_usage.state = state
            usage[rid] = prev_usage
    requests = sorted(
        facts_by_id.values(),
        key=lambda f: (
            _request_sequence_value(f.sequence if f.sequence is not None else f.request_index) is None,
            _request_sequence_value(f.sequence if f.sequence is not None else f.request_index) or 0,
        ),
    )
    return RequestFacts(
        requests=requests,
        diagnostics=list(diagnostics or []),
        option_sources=_option_sources_from_page_enum_options(page_enum_options),
        analysis=analysis,
        usage=usage,
    )


def _graph_entry_from_request_fact(
    fact: RequestFact,
    analysis: RequestAnalysis | None = None,
    usage: RequestUsage | None = None,
) -> dict[str, Any]:
    out = {
        "request_index": fact.request_index,
        "request_id": fact.request_id,
        "page_id": fact.page_id,
        "frame_id": fact.frame_id,
        "sequence": fact.sequence,
        "method": fact.method,
        "url": fact.url,
        "path": fact.path,
        "role": analysis.role if analysis else "",
        "keep": bool(analysis.keep) if analysis else False,
        "reason": analysis.reason if analysis else "",
        "confidence": float(analysis.confidence) if analysis else 0.0,
        "evidence": dict(analysis.evidence or {}) if analysis else {},
        "state": usage.state if usage else "captured",
        "materialized_step_id": usage.materialized_step_id if usage else "",
        "headers": dict(fact.headers or {}),
        "query": dict(fact.query or {}),
        "content_type": fact.content_type,
        "post_data": fact.post_data,
        "response_status": fact.response_status,
        "response_json": fact.response_json,
        "response_schema": dict(fact.response_schema or {}),
    }
    return out


def _request_graph_from_request_facts(request_facts: RequestFacts) -> dict[str, list[dict[str, Any]]]:
    graph = {
        "all_requests": [],
        "selected_steps": [],
        "candidate_reads": [],
        "filtered_requests": [],
    }
    for fact in request_facts.requests or []:
        rid = fact.request_id or _request_fact_key(fact.model_dump())
        analysis = request_facts.analysis.get(rid)
        usage = request_facts.usage.get(rid)
        entry = _graph_entry_from_request_fact(fact, analysis, usage)
        graph["all_requests"].append(entry)
        bucket = (analysis.bucket if analysis else "") or ""
        if usage and (usage.materialized_step_id or usage.state == "materialized"):
            graph["selected_steps"].append(entry)
        elif bucket == "selected_steps":
            graph["selected_steps"].append(entry)
        elif bucket == "candidate_reads":
            graph["candidate_reads"].append(entry)
        elif bucket == "filtered_requests":
            graph["filtered_requests"].append(entry)
        elif (analysis and analysis.role in {"read_option", "read_context", "business_get"} and analysis.keep):
            graph["candidate_reads"].append(entry)
        else:
            graph["filtered_requests"].append(entry)
    return graph


def _raw_request_facts_has_requests(raw_facts: Any) -> bool:
    if isinstance(raw_facts, RequestFacts):
        return bool(raw_facts.requests)
    if isinstance(raw_facts, dict):
        return bool(raw_facts.get("requests"))
    return False


def _request_graph_has_entries(graph: Any) -> bool:
    if not isinstance(graph, dict):
        return False
    return any(graph.get(bucket) for bucket in ("all_requests", "selected_steps", "candidate_reads", "filtered_requests"))


def ensure_request_facts(spec: FlowSpec, *, prefer: str = "request_facts") -> FlowSpec:
    """同步 P0 request_facts 与旧 meta.request_graph，保持双轨兼容。

    prefer="meta" 用于旧编辑路径刚更新 meta.request_graph 后的反向同步；
    默认让一等 request_facts 回写 legacy graph。
    """
    meta = dict(spec.meta or {})
    graph = meta.get("request_graph") or {}
    has_graph = _request_graph_has_entries(graph)
    has_facts = bool(spec.request_facts.requests)
    old_option_sources = list(spec.request_facts.option_sources or [])
    if prefer == "meta":
        if has_graph:
            spec.request_facts = _request_facts_from_graph(graph, diagnostics=spec.diagnostics)
            if old_option_sources and not spec.request_facts.option_sources:
                spec.request_facts.option_sources = old_option_sources
        elif has_facts:
            meta["request_graph"] = _request_graph_from_request_facts(spec.request_facts)
            spec.meta = meta
        return spec

    if has_facts:
        meta["request_graph"] = _request_graph_from_request_facts(spec.request_facts)
        spec.meta = meta
    elif has_graph:
        spec.request_facts = _request_facts_from_graph(graph, diagnostics=spec.diagnostics)
        if old_option_sources and not spec.request_facts.option_sources:
            spec.request_facts.option_sources = old_option_sources
    return spec


def _request_graph_for_spec(spec: FlowSpec) -> dict[str, Any]:
    ensure_request_facts(spec, prefer="meta")
    return (spec.meta or {}).get("request_graph") or {}


def _capability_scoped_node_step_ids(nodes: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        sid = str(node.get("step_id") or "").strip()
        if sid and sid not in ids:
            ids.append(sid)
        for child_key in ("children", "steps", "then", "else", "otherwise"):
            child = node.get(child_key)
            if isinstance(child, list):
                for child_sid in _capability_scoped_node_step_ids([n for n in child if isinstance(n, dict)]):
                    if child_sid not in ids:
                        ids.append(child_sid)
    return ids


def _capability_scoped_step_ids(cap: FlowCapability) -> list[str]:
    ids: list[str] = []
    for sid in list(cap.step_ids or []) + _capability_scoped_node_step_ids(cap.nodes or []):
        sid = str(sid or "").strip()
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def _step_request_fact_for_capability(spec: FlowSpec, step: FlowStep) -> RequestFact | None:
    rid = str((step.source_meta or {}).get("request_id") or "").strip()
    if rid:
        found = next((f for f in spec.request_facts.requests if f.request_id == rid), None)
        if found is not None:
            return found
    request_index = (step.source_meta or {}).get("request_index")
    if request_index is not None:
        found = next((f for f in spec.request_facts.requests if f.request_index == request_index), None)
        if found is not None:
            return found
    method = (step.method or "").upper()
    path = _request_path({"url": step.path or step.url})
    return next(
        (
            f for f in spec.request_facts.requests
            if (f.method or "").upper() == method and _request_path({"url": f.path or f.url}) == path
        ),
        None,
    )


def _capability_request_ref_from_step(
    spec: FlowSpec,
    step: FlowStep,
    existing: CapabilityRequestRef | None = None,
) -> CapabilityRequestRef:
    fact = _step_request_fact_for_capability(spec, step)
    rid = fact.request_id if fact else str((step.source_meta or {}).get("request_id") or "")
    analysis = spec.request_facts.analysis.get(rid) if rid else None
    return CapabilityRequestRef(
        request_id=rid,
        request_index=fact.request_index if fact else (step.source_meta or {}).get("request_index"),
        step_id=step.step_id,
        role=(analysis.role if analysis else "") or (step.source_meta or {}).get("role") or step.semantic_role or "",
        method=(step.method or "").upper(),
        path=step.path or step.url,
        sequence=fact.sequence if fact else (step.source_meta or {}).get("sequence", (step.source_meta or {}).get("request_index")),
        confidence=float((analysis.confidence if analysis else None) or (step.source_meta or {}).get("confidence") or 0.0),
        reason=(analysis.reason if analysis else "") or (step.source_meta or {}).get("keep_reason") or "",
        usage=existing.usage if existing else str((step.source_meta or {}).get("capability_usage") or "execute"),
        origin=existing.origin if existing else str((step.source_meta or {}).get("membership_origin") or "planner"),
        pinned=bool(existing.pinned) if existing else bool((step.source_meta or {}).get("membership_pinned")),
        confirmed=bool(existing.confirmed) if existing else False,
    )


def _capability_field_from_param(
    step: FlowStep,
    param: ParamField,
    *,
    scope: str,
    request_id: str = "",
) -> CapabilityField:
    exposed = bool(param.exposed_to_user and param.category == "user_param")
    return CapabilityField(
        field_id=f"{scope}:{step.step_id}:{param.path}",
        scope=scope,
        display_name=param.label or param.key or param.path,
        path=param.path,
        key=param.key,
        type=param.type,
        wire_type=param.wire_type or _infer_type_from_value(param.value),
        required=bool(param.required),
        request_id=request_id,
        request_index=(step.source_meta or {}).get("request_index"),
        step_id=step.step_id,
        source_kind=param.source_kind,
        source=dict(param.source or {}),
        category=param.category,
        enum_options=list(param.enum_options) if param.enum_options else None,
        enum_value_map=dict(param.enum_value_map) if param.enum_value_map else None,
        exposed_to_caller=exposed if scope != "request_field" else bool(param.exposed_to_user),
        confidence=float(param.confidence or 0.0),
        confirmed=bool(param.locked or not param.need_human_confirm),
        locked=bool(param.locked),
        evidence=list(param.evidence or []),
    )


def _capability_dependency_from_link(link: FlowLink) -> CapabilityDependency:
    dependency_id = link.link_id or hashlib.sha1(
        "|".join([link.source_step_id, link.source_path, link.target_step_id, link.target_path]).encode("utf-8")
    ).hexdigest()[:12]
    return CapabilityDependency(
        dependency_id=dependency_id,
        type="response_to_request",
        source={
            "step_id": link.source_step_id,
            "path": link.source_path,
            "tokens": link.source_tokens,
        },
        target={
            "step_id": link.target_step_id,
            "path": link.target_path,
            "tokens": link.target_tokens,
            "param_name": link.param_name,
        },
        confidence=float(link.confidence or 0.0),
        confirmed=bool(link.confirmed),
        locked=bool(link.locked),
        reason=link.reason,
        evidence=dict(link.evidence or {}),
    )


def _capability_output_fields(cap: FlowCapability) -> list[CapabilityField]:
    fields: list[CapabilityField] = []
    output_props = (cap.output_schema or {}).get("properties") or {}
    required = set((cap.output_schema or {}).get("required") or [])
    for idx, mapping in enumerate(cap.output_mapping or []):
        if not isinstance(mapping, dict):
            continue
        name = _capability_output_name(mapping, idx)
        schema = output_props.get(name) if isinstance(output_props, dict) else None
        field_type = str(schema.get("type") or "") if isinstance(schema, dict) else ""
        fields.append(CapabilityField(
            field_id=f"output:{cap.name or cap.capability_id}:{idx}:{name}",
            scope="output",
            display_name=name,
            path=name,
            key=name,
            type=field_type or ("object" if name in {"response", "raw", "detail"} else "string"),
            required=name in required,
            step_id=str(mapping.get("step_id") or ""),
            source_kind=str(mapping.get("kind") or "final_response"),
            source=dict(mapping),
            exposed_to_caller=True,
            confidence=float(cap.confidence or 0.0),
            confirmed=bool(cap.confirmed),
        ))
    if fields:
        return fields
    props = (cap.output_schema or {}).get("properties") or {}
    required = set((cap.output_schema or {}).get("required") or [])
    for name, schema in props.items():
        schema = schema if isinstance(schema, dict) else {}
        fields.append(CapabilityField(
            field_id=f"output:{cap.name or cap.capability_id}:{name}",
            scope="output",
            display_name=str(schema.get("title") or name),
            path=str(name),
            key=str(name),
            type=str(schema.get("type") or "string"),
            required=name in required,
            exposed_to_caller=True,
            confidence=float(cap.confidence or 0.0),
            confirmed=bool(cap.confirmed),
        ))
    return fields


def _capability_views_locked(cap: FlowCapability) -> bool:
    fields = [
        *(cap.inputs or []),
        *(cap.request_fields or []),
        *(cap.internal_fields or []),
        *(cap.computed_fields or []),
        *(cap.outputs or []),
    ]
    deps = list(cap.dependencies or [])
    return any(getattr(f, "locked", False) for f in fields) or any(getattr(d, "locked", False) for d in deps)


def _capability_field_merge_key(field: CapabilityField) -> tuple[str, str, str, str]:
    return (
        field.scope or "",
        field.step_id or "",
        _strip_body_prefix(field.path or ""),
        field.key or field.display_name or field.field_id or "",
    )


def _merge_capability_scoped_fields(
    derived: list[CapabilityField],
    existing: list[CapabilityField],
) -> list[CapabilityField]:
    """Merge derived fields with user-locked capability scoped edits.

    P2 keeps steps/links as executable truth, but capability scoped fields must not
    lose user/LLM corrections. Locked existing fields override matching derived
    entries; custom locked fields that no longer match a step remain visible.
    """
    out = [item.model_copy(deep=True) for item in derived]
    by_key = {_capability_field_merge_key(item): idx for idx, item in enumerate(out)}
    by_id = {item.field_id: idx for idx, item in enumerate(out) if item.field_id}
    for item in existing or []:
        if not item.locked:
            continue
        copied = item.model_copy(deep=True)
        idx = by_id.get(copied.field_id)
        if idx is None:
            idx = by_key.get(_capability_field_merge_key(copied))
        if idx is None:
            out.append(copied)
            by_key[_capability_field_merge_key(copied)] = len(out) - 1
            if copied.field_id:
                by_id[copied.field_id] = len(out) - 1
        else:
            # Step params are the executable source of truth. Capability request
            # fields are derived views, never independently authoritative. Manual
            # edits are persisted on ParamField, so even an older locked mirror
            # must be replaced here.
            out[idx] = copied
    return out


def _capability_dependency_merge_key(dep: CapabilityDependency) -> tuple[str, str, str, str]:
    source = dep.source or {}
    target = dep.target or {}
    return (
        str(source.get("step_id") or ""),
        _strip_body_prefix(str(source.get("path") or "")),
        str(target.get("step_id") or ""),
        _strip_body_prefix(str(target.get("path") or "")),
    )


def _merge_capability_scoped_dependencies(
    derived: list[CapabilityDependency],
    existing: list[CapabilityDependency],
) -> list[CapabilityDependency]:
    out = [item.model_copy(deep=True) for item in derived]
    by_key = {_capability_dependency_merge_key(item): idx for idx, item in enumerate(out)}
    by_id = {item.dependency_id: idx for idx, item in enumerate(out) if item.dependency_id}
    for item in existing or []:
        if not item.locked:
            continue
        copied = item.model_copy(deep=True)
        idx = by_id.get(copied.dependency_id)
        if idx is None:
            idx = by_key.get(_capability_dependency_merge_key(copied))
        if idx is None:
            out.append(copied)
            by_key[_capability_dependency_merge_key(copied)] = len(out) - 1
            if copied.dependency_id:
                by_id[copied.dependency_id] = len(out) - 1
        else:
            out[idx] = copied
    return out


def sync_capability_scoped_views(spec: FlowSpec) -> FlowSpec:
    """从旧 steps/links/step_ids 派生能力内字段/依赖视图。"""
    ensure_request_facts(spec)
    if not spec.capabilities:
        return spec
    by_step = {s.step_id: s for s in spec.steps}
    used_by_request: dict[str, list[str]] = {}
    materialized_by_request: dict[str, str] = {}
    memberships_by_request: dict[str, list[dict[str, Any]]] = {}
    for cap in spec.capabilities:
        previous_refs = {ref.step_id: ref for ref in (cap.request_refs or []) if ref.step_id}
        auxiliary_refs = [
            ref for ref in (cap.request_refs or [])
            if ref.usage == "option_source" and ref.step_id not in _capability_scoped_step_ids(cap)
        ]
        cap_step_ids = [
            sid for sid in _capability_scoped_step_ids(cap)
            if sid in by_step and _capability_step_allowed(spec, cap, by_step[sid])
        ]
        cap.step_ids = cap_step_ids
        step_objs = [by_step[sid] for sid in cap_step_ids]
        cap.request_refs = [
            _capability_request_ref_from_step(spec, st, previous_refs.get(st.step_id))
            for st in step_objs
        ] + auxiliary_refs
        cap_name = cap.name or cap.capability_id
        for ref in cap.request_refs:
            if ref.request_id and cap_name:
                used_by_request.setdefault(ref.request_id, [])
                if cap_name not in used_by_request[ref.request_id]:
                    used_by_request[ref.request_id].append(cap_name)
                if ref.step_id:
                    materialized_by_request[ref.request_id] = ref.step_id
                memberships_by_request.setdefault(ref.request_id, []).append({
                    "capability": cap_name,
                    "step_id": ref.step_id,
                    "usage": ref.usage,
                    "origin": ref.origin,
                    "pinned": ref.pinned,
                    "confirmed": ref.confirmed,
                })
        inputs: dict[str, CapabilityField] = {}
        request_fields: list[CapabilityField] = []
        internal_fields: list[CapabilityField] = []
        old_all_fields = list(cap.fields or [])
        old_inputs = list(cap.inputs or [])
        old_request_fields = list(cap.request_fields or [])
        old_internal_fields = list(cap.internal_fields or [])
        old_computed_fields = list(cap.computed_fields or [])
        old_outputs = list(cap.outputs or [])
        old_dependencies = list(cap.dependencies or [])
        for old_field in old_all_fields:
            if old_field.scope == "input":
                old_inputs.append(old_field)
            elif old_field.scope == "request_field":
                old_request_fields.append(old_field)
            elif old_field.scope == "internal":
                old_internal_fields.append(old_field)
            elif old_field.scope == "output":
                old_outputs.append(old_field)
            else:
                old_computed_fields.append(old_field)
        request_id_by_step = {ref.step_id: ref.request_id for ref in cap.request_refs}
        for st in step_objs:
            request_id = request_id_by_step.get(st.step_id, "")
            for param in st.params:
                request_fields.append(_capability_field_from_param(st, param, scope="request_field", request_id=request_id))
                if param.category == "user_param" and param.exposed_to_user:
                    key = param.key or param.label or param.path
                    inputs.setdefault(key, _capability_field_from_param(st, param, scope="input", request_id=request_id))
                else:
                    internal_fields.append(_capability_field_from_param(st, param, scope="internal", request_id=request_id))
        # steps/params 是请求字段的唯一真相；能力自身的聚合输入（例如批量 entries）
        # 可以独立存在。任何绑定到 step_id 的能力字段都是派生镜像，不能回写或
        # 覆盖 ParamField，即使旧镜像曾被 locked/confirmed。
        valid_old_inputs = [
            item for item in old_inputs
            if not item.step_id
            and _schema_path_exists(cap.input_schema, item.path, item.key)
        ]
        cap.inputs = _merge_capability_scoped_fields(list(inputs.values()), valid_old_inputs)
        cap.request_fields = request_fields
        cap.internal_fields = internal_fields
        cap.computed_fields = [item.model_copy(deep=True) for item in old_computed_fields]
        derived_dependencies = [
            _capability_dependency_from_link(link)
            for link in spec.links
            if link.source_step_id in cap_step_ids and link.target_step_id in cap_step_ids
        ]
        valid_old_dependencies = [
            item for item in old_dependencies
            if str((item.target or {}).get("step_id") or "") in cap_step_ids
            and _capability_step_param_exists(
                by_step.get(str((item.target or {}).get("step_id") or "")),
                str((item.target or {}).get("path") or ""),
            )
            and (
                bool(str((item.source or {}).get("request_id") or ""))
                or (
                    str((item.source or {}).get("step_id") or "") in cap_step_ids
                    and _capability_response_path_exists(
                        by_step.get(str((item.source or {}).get("step_id") or "")),
                        str((item.source or {}).get("path") or ""),
                    )
                )
            )
        ]
        cap.dependencies = _merge_capability_scoped_dependencies(
            derived_dependencies, valid_old_dependencies,
        )
        derived_outputs = _capability_output_fields(cap)
        valid_old_outputs = [
            item for item in old_outputs
            if _schema_path_exists(cap.output_schema, item.path, item.key)
            and (
                not item.step_id
                or (
                item.step_id in cap_step_ids
                and _capability_response_path_exists(by_step.get(item.step_id), item.path or item.key)
                )
            )
        ]
        cap.outputs = _merge_capability_scoped_fields(derived_outputs, valid_old_outputs)
        cap.fields = [
            *(cap.inputs or []),
            *(cap.request_fields or []),
            *(cap.internal_fields or []),
            *(cap.computed_fields or []),
            *(cap.outputs or []),
        ]
    for fact in spec.request_facts.requests or []:
        request_id = fact.request_id or ""
        if not request_id:
            continue
        usage = spec.request_facts.usage.get(request_id) or RequestUsage(request_id=request_id)
        usage.used_by_capabilities = list(used_by_request.get(request_id) or [])
        usage.capability_memberships = list(memberships_by_request.get(request_id) or [])
        if materialized_by_request.get(request_id):
            usage.materialized_step_id = materialized_by_request[request_id]
            usage.state = "materialized"
        elif usage.materialized_step_id and any(s.step_id == usage.materialized_step_id for s in spec.steps):
            usage.state = "materialized"
        else:
            usage.materialized_step_id = ""
            usage.state = "captured"
        spec.request_facts.usage[request_id] = usage
    spec.meta = {
        **(spec.meta or {}),
        "capability_scoped_view": {
            "status": "derived",
            "source": "steps+links+request_facts",
            "capability_count": len(spec.capabilities),
        },
    }
    return spec


def _upgrade_materialized_query_facts(spec: FlowSpec) -> None:
    """Replace an initial pagination request with the richer searched instance."""
    pinned_steps = {
        ref.step_id
        for cap in (spec.capabilities or [])
        for ref in (cap.request_refs or [])
        if ref.step_id and ref.pinned
    }
    for step in spec.steps:
        if (step.method or "GET").upper() not in {"GET", "HEAD"} or step.step_id in pinned_steps:
            continue
        if any(
            _param_has_manual_contract(param)
            for param in (step.params or [])
            if str(param.path or "").startswith("query.")
        ):
            continue
        current = {
            "method": step.method,
            "url": step.url or step.path,
            "query": dict((step.source_meta or {}).get("query") or {}),
            "index": (step.source_meta or {}).get("request_index"),
        }
        current_path = _request_path(current)
        candidates: list[tuple[RequestFact, RequestAnalysis | None, dict[str, Any]]] = []
        for fact in spec.request_facts.requests or []:
            raw = fact.model_dump(exclude_none=True)
            if (fact.method or "GET").upper() != (step.method or "GET").upper():
                continue
            if _request_path(raw) != current_path:
                continue
            analysis = spec.request_facts.analysis.get(fact.request_id or "")
            if analysis is not None and analysis.role not in {"business_get", "read_context"}:
                continue
            candidates.append((fact, analysis, raw))
        if not candidates:
            continue
        fact, analysis, best = max(candidates, key=lambda item: _preread_candidate_score(item[2]))
        if _business_filter_count(best) <= _business_filter_count(current):
            continue
        step.url = _request_url_with_query(best)
        step.path = _path_from_url(step.url)
        step.response_json = fact.response_json
        if fact.headers:
            step.headers = extract_auth_headers(fact.headers)
        step.params = [
            param for param in (step.params or [])
            if not str(param.path or "").startswith("query.")
        ]
        for usage in spec.request_facts.usage.values():
            if usage.materialized_step_id == step.step_id:
                usage.materialized_step_id = ""
                usage.state = "captured"
        step.source_meta = {
            **(step.source_meta or {}),
            "url": step.url,
            "query": dict(fact.query or {}),
            "request_id": fact.request_id,
            "request_index": fact.request_index,
            "response_status": fact.response_status,
            "role": analysis.role if analysis else (step.source_meta or {}).get("role"),
            "confidence": analysis.confidence if analysis else (step.source_meta or {}).get("confidence"),
            "query_fact_upgraded": True,
        }


def sync_flow_spec_models(spec: FlowSpec, *, prefer_request_facts: bool = True) -> FlowSpec:
    ensure_request_facts(spec, prefer="request_facts" if prefer_request_facts else "meta")
    _upgrade_materialized_query_facts(spec)
    # FlowStep 已经是可编辑/可编排接口的物化事实；usage 不能等到能力绑定后才更新，
    # 否则初次分析会把已进入字段页的查询接口仍标成 captured。
    for step in spec.steps:
        if (step.method or "GET").upper() in {"GET", "HEAD"}:
            # Legacy/imported specs may only carry query values in the URL. Put
            # them into ParamField first so request compilation, capability input
            # schemas and scoped field views all read the same executable truth.
            query_url = step.url if "?" in str(step.url or "") else step.path
            _append_query_params_to_step(step, query_url or step.url)
        _sync_step_option_contracts(spec, step)
        _audit_step_param_contracts(step)
        valid_param_paths = {param.path for param in step.params if param.path}
        for select in step.selects or []:
            if select.id_path and select.id_path not in valid_param_paths:
                select.id_path = None
                select.id_tokens = None
        request_id = str((step.source_meta or {}).get("request_id") or "")
        if not request_id:
            continue
        usage = spec.request_facts.usage.get(request_id) or RequestUsage(request_id=request_id)
        usage.state = "materialized"
        usage.materialized_step_id = step.step_id
        spec.request_facts.usage[request_id] = usage
    return sync_capability_scoped_views(spec)


def _param_has_manual_contract(param: ParamField) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("source") == "manual_edit"
        and item.get("field") in {
            "type", "category", "source_kind", "source", "enum_options", "enum_value_map",
        }
        for item in (param.evidence or [])
    )


def _semantic_recorded_type(param: ParamField) -> str:
    text = " ".join(str(value or "") for value in (param.path, param.key, param.label)).lower()
    value = str(param.value or param.default_value or "").strip()
    if re.search(r"(?:date|time|day|日期|时间)", text):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return "date"
        if re.fullmatch(r"\d{10}|\d{13}|\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}(?::\d{2})?", value, re.I):
            return "datetime"
    return param.type or param.wire_type or _infer_type_from_value(value)


def _audit_step_param_contracts(step: FlowStep) -> None:
    """Conservatively repair only contradictory generated field contracts."""
    display_paths = {
        _strip_body_prefix(binding.path)
        for binding in (step.selects or [])
        if binding.path and _select_has_executable_options(binding)
    }
    id_paths = {
        _strip_body_prefix(binding.id_path)
        for binding in (step.selects or [])
        if binding.id_path and _select_has_executable_options(binding)
    }
    for param in step.params or []:
        if _param_has_manual_contract(param):
            continue
        normalized_path = _strip_body_prefix(param.path or "")
        if _looks_pagination_field(param.key, param.path):
            param.type = _infer_type_from_value(param.value)
            param.category = "system_const"
            param.source_kind = "constant"
            param.source = {"kind": "pagination", "path": param.path}
            param.exposed_to_user = False
            param.editable = True
            param.need_human_confirm = False
            param.enum_options = None
            param.enum_value_map = None
            param.description = _strip_option_descriptions(param.description) or None
            param.reason = "分页参数由 Skill 内部按默认分页提交，不作为普通业务字段暴露"
            continue
        option_contract = bool(param.enum_options or param.enum_value_map or normalized_path in display_paths)
        if normalized_path in id_paths and normalized_path not in display_paths:
            continue
        if param.type in _ENUM_PARAM_TYPES or param.source_kind in _ENUM_SOURCE_KINDS:
            if not option_contract and param.source_kind not in _ENUM_SOURCE_KINDS:
                param.type = param.wire_type or _infer_type_from_value(param.value)
                param.enum_options = None
                param.enum_value_map = None
                if param.category == "user_param":
                    param.source_kind = "user_input"
                    param.source = {"kind": "sample", "path": param.path}
                    param.exposed_to_user = True
                    param.editable = True
                param.description = _strip_option_descriptions(param.description) or None
                param.reason = _strip_option_descriptions(param.reason)
            else:
                param.category = "user_param"
                param.exposed_to_user = True
                param.editable = True
                _refresh_param_enum_description(param)
        elif param.category == "user_param" and param.source_kind == "user_input":
            semantic_type = _semantic_recorded_type(param)
            if semantic_type in {"date", "datetime"}:
                param.type = semantic_type


def _api_option_binding_is_trustworthy(binding: SelectBinding) -> bool:
    if not binding.source_url:
        return False
    label_key = str(binding.label_key or "").strip().lower()
    value_key = str(binding.value_key or "").strip().lower()
    if label_key and value_key and label_key != value_key:
        return True
    path = _request_path({"url": binding.source_url}).lower()
    if re.search(r"(?:^|/)(?:dict|dictionary|options?|candidates?|simple-list|tree|users?|departments?|roles?|employees?)(?:/|$)", path):
        return True
    pairs = [pair for pair in (_enum_label_value(item) for item in (binding.options or [])) if pair]
    return any(str(label).strip() != str(value).strip() for label, value in pairs)


def _page_enum_contract_for_param(
    spec: FlowSpec,
    step: FlowStep,
    param: ParamField,
    binding: SelectBinding,
) -> tuple[list[Any], dict[str, Any]] | None:
    page_options = _page_enum_options_from_request_facts(spec.request_facts)
    keys = [
        binding.path, binding.id_path, param.path, param.key, param.label,
        _strip_body_prefix(binding.path or ""), _strip_body_prefix(param.path or ""),
    ]
    for key in [str(value or "") for value in keys if str(value or "")]:
        raw = page_options.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            options = list(raw.get("options") or raw.get("values") or [])
            value_map = dict(raw.get("option_map") or raw.get("value_map") or {})
        elif isinstance(raw, list):
            options = list(raw)
            value_map = {
                str(item.get("label")): item.get("value")
                for item in options
                if isinstance(item, dict) and item.get("label") not in (None, "") and "value" in item and item.get("value") is not None
            }
        else:
            continue
        if options:
            return options, value_map
    return None


def _sync_step_option_contracts(spec: FlowSpec, step: FlowStep) -> None:
    """Project executable select bindings back onto their request parameters.

    SelectBinding is the grounded evidence for page/API choices.  Keeping only
    the ParamField as ``user_input`` loses label-to-value mapping and the source
    request when capabilities are rebuilt.
    """
    step.selects = [
        binding for binding in (step.selects or [])
        if not _looks_pagination_field(
            str(binding.param or ""), str(binding.path or binding.id_path or ""),
        )
    ]
    for param in step.params or []:
        if param.type in _ENUM_PARAM_TYPES or param.source_kind in _ENUM_SOURCE_KINDS:
            continue
        param.enum_options = None
        param.enum_value_map = None
        param.description = _strip_option_descriptions(param.description) or None
        param.reason = _strip_option_descriptions(param.reason)
    for binding in step.selects or []:
        _hydrate_select_source_contract(spec, binding)
        # Paired controls commonly have both ``name`` and ``id`` leaves.  The
        # caller-facing option contract belongs to the display/name path; the ID
        # remains a runtime-derived request field.  Only use id_path when there is
        # no separate display path in the request.
        param = next((
            item for item in (step.params or [])
            if binding.path and _strip_body_prefix(item.path) == _strip_body_prefix(binding.path)
        ), None)
        if param is None:
            param = next((
                item for item in (step.params or [])
                if binding.param and binding.param in {item.key, item.label}
            ), None)
        if param is None:
            param = next((
                item for item in (step.params or [])
                if binding.id_path and _strip_body_prefix(item.path) == _strip_body_prefix(binding.id_path)
            ), None)
        if param is None or not _select_has_executable_options(binding):
            continue
        # 人工修改过数据契约后，SelectBinding 只能作为历史证据，不能在每次
        # sync 时把类型/分类/来源自动改回录制推断值。
        if any(
            isinstance(item, dict)
            and item.get("source") == "manual_edit"
            and item.get("field") in {
                "type", "category", "source_kind", "source", "enum_options", "enum_value_map",
            }
            for item in (param.evidence or [])
        ):
            continue
        page_contract = _page_enum_contract_for_param(spec, step, param, binding)
        trusted_api = _api_option_binding_is_trustworthy(binding)
        source_kind = "page_enum" if page_contract else ("api_option" if trusted_api else ("page_enum" if not binding.source_url else "unknown"))
        options = list(page_contract[0]) if page_contract else _enum_options_for_param(binding)
        option_map = dict(page_contract[1]) if page_contract else (_enum_value_map_for_param(binding) or {})
        if page_contract:
            page_labels = {
                str(pair[0]) for item in page_contract[0]
                if (pair := _enum_label_value(item)) is not None
            }
            option_map.update({
                str(label): value for label, value in (_enum_value_map_for_param(binding) or {}).items()
                if str(label) in page_labels and value is not None
            })
        param.type = "list-enum" if binding.multi else "enum"
        param.category = "user_param"
        param.source_kind = source_kind
        param.exposed_to_user = True
        param.editable = True
        param.enum_options = list(options or param.enum_options or []) or None
        param.enum_value_map = dict(option_map or param.enum_value_map or {}) or None
        param.source = {
            **dict(param.source or {}),
            "kind": source_kind,
            "source_url": binding.source_url if source_kind == "api_option" else None,
            "source_method": binding.source_method,
            "source_request_id": binding.source_request_id,
            "value_key": binding.value_key,
            "label_key": binding.label_key,
            "id_path": binding.id_path or binding.path or param.path,
            "enum_source": "dom" if source_kind == "page_enum" else (binding.enum_source or ("api" if trusted_api else "unknown")),
            "enum_confirmed": (
                len(option_map) == len(options or [])
                if page_contract
                else (binding.enum_confirmed if trusted_api or not binding.source_url else False)
            ),
        }
        param.need_human_confirm = bool(
            source_kind == "unknown" or (source_kind == "page_enum" and (param.source or {}).get("enum_confirmed") is False)
        )
        source_reason = (
            "候选来自录制捕获的只读接口；调用方传显示值，运行期按当前接口结果映射真实值"
            if source_kind == "api_option"
            else (
                "候选来自页面真实下拉；调用方传显示值，运行期按录制的 label/value 映射真实值"
                if source_kind == "page_enum"
                else "候选接口缺少可信的 label/value 证据，不能作为已确认枚举来源"
            )
        )
        option_description = _enum_options_description(source_kind, param.enum_options, param.enum_value_map)
        param.description = _upsert_option_description(param.description, option_description)
        param.reason = _upsert_option_description(param.reason or source_reason, option_description)


def _strip_body_prefix(path: str) -> str:
    return path[len("body."):] if path.startswith("body.") else path


def _reset_param_source(param: ParamField, *, reason: str | None = None) -> None:
    """把字段从运行期/接口来源恢复成普通用户输入，供删除依赖/重置来源使用。"""
    param.category = "user_param"
    param.source_kind = "user_input"
    param.source = {"kind": "sample", "path": param.path}
    param.editable = True
    param.exposed_to_user = True
    param.need_human_confirm = False
    param.confidence_tier = "manual"
    param.reason = reason or "已取消运行期/接口来源绑定，改为调用 Skill 时由用户填写"


_ENUM_PARAM_TYPES = frozenset({"enum", "list-enum"})
_ENUM_SOURCE_KINDS = frozenset({
    "api_option", "page_enum", "static_enum", "manual_enum", "form_option",
})


def _param_has_manual_description(param: ParamField) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("source") == "manual_edit"
        and item.get("field") == "description"
        for item in (param.evidence or [])
    )


def _transition_param_type(spec: FlowSpec, step: FlowStep, param: ParamField, value: Any) -> None:
    """Atomically transition a field's data contract.

    Enum metadata is executable state, not decoration. Leaving an enum type must
    remove every stale option contract in the same edit so a later schema sync or
    export cannot resurrect the old dropdown.
    """
    old_type = str(param.type or "string")
    new_type = str(value or "string")
    param.type = new_type
    if old_type in _ENUM_PARAM_TYPES and new_type not in _ENUM_PARAM_TYPES:
        param.enum_options = None
        param.enum_value_map = None
        param.description = _strip_option_descriptions(param.description) or None
        param.reason = _strip_option_descriptions(param.reason)
        if param.source_kind in _ENUM_SOURCE_KINDS:
            if param.category == "user_param":
                param.source_kind = "user_input"
                param.source = {"kind": "sample", "path": param.path}
                param.exposed_to_user = True
                param.editable = True
                param.need_human_confirm = False
                param.reason = "字段已改为普通输入，不再使用旧枚举候选"
            else:
                param.source_kind = "unknown"
                param.source = {}
                param.need_human_confirm = True
                param.reason = "字段类型已变化，需要重新确认运行期来源"
        step.selects = [
            binding for binding in (step.selects or [])
            if not (
                _strip_body_prefix(binding.path or "") == _strip_body_prefix(param.path)
                or (binding.param and binding.param in {param.key, param.label})
                or (binding.id_path and _strip_body_prefix(binding.id_path) == _strip_body_prefix(param.path))
            )
        ]
    elif new_type in _ENUM_PARAM_TYPES and old_type not in _ENUM_PARAM_TYPES:
        # Entering enum mode never invents options. The user/planner must bind a
        # DOM/API/manual fact source before confirmation.
        if param.source_kind not in _ENUM_SOURCE_KINDS:
            param.source_kind = "unknown"
            param.source = {}
            param.need_human_confirm = True
            param.reason = "枚举字段尚未绑定可信的页面、接口或人工候选来源"


def _invalidate_capabilities_for_steps(spec: FlowSpec, step_ids: set[str]) -> None:
    if not step_ids:
        return
    for cap in spec.capabilities or []:
        if not (set(_capability_node_step_ids(cap)) & step_ids):
            continue
        _invalidate_capability_contract(cap)


def _invalidate_capability_contract(cap: FlowCapability) -> None:
    cap.confirmed = False
    cap.confirmation_hash = ""
    cap.status = "draft"
    cap.requires_human_confirm = True


def _apply_capability_field_to_param(spec: FlowSpec, raw: dict[str, Any], *, scope: str) -> bool:
    """Persist a step-bound capability field edit on its canonical ParamField."""
    step_id = str(raw.get("step_id") or "")
    path = str(raw.get("path") or raw.get("key") or "")
    if not step_id or not path:
        return False
    step = next((item for item in spec.steps if item.step_id == step_id), None)
    if step is None:
        return False
    try:
        param = _find_param(
            step, path,
            param_key=str(raw.get("key") or ""),
            param_label=str(raw.get("display_name") or ""),
        )
    except ValueError:
        return False
    if raw.get("key"):
        param.key = str(raw["key"])
        param.label = str(raw.get("display_name") or raw["key"])
    if raw.get("display_name"):
        param.label = str(raw["display_name"])
    if raw.get("type"):
        _transition_param_type(spec, step, param, raw["type"])
    if "required" in raw:
        param.required = bool(raw["required"])
    if raw.get("source_kind"):
        param.source_kind = str(raw["source_kind"])
    if isinstance(raw.get("source"), dict):
        param.source = dict(raw["source"])
    if "exposed_to_caller" in raw:
        param.exposed_to_user = bool(raw["exposed_to_caller"])
    if scope == "input":
        param.category = "user_param"
        param.exposed_to_user = True
    elif scope == "internal":
        param.category = "system_const" if param.source_kind == "constant" else "runtime_var"
        param.exposed_to_user = False
    param.locked = bool(raw.get("locked", True))
    if "confirmed" in raw:
        param.need_human_confirm = not bool(raw.get("confirmed"))
    param.evidence.append({"source": "capability_field_edit", "scope": scope})
    return True


def _capability_confirmation_hash(spec: FlowSpec, cap: FlowCapability) -> str:
    by_id = {step.step_id: step for step in spec.steps}
    payload = {
        "capability": cap.model_dump(exclude={
            "confirmed", "confirmation_hash", "status", "requires_human_confirm",
            "confidence", "updated_by",
        }),
        "steps": [
            by_id[sid].model_dump()
            for sid in _capability_node_step_ids(cap)
            if sid in by_id
        ],
        "links": [
            link.model_dump()
            for link in spec.links
            if link.source_step_id in set(_capability_node_step_ids(cap))
            and link.target_step_id in set(_capability_node_step_ids(cap))
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _remove_param_incoming_links(spec: FlowSpec, step: FlowStep, param: ParamField) -> None:
    """人工把字段改离上游响应时，依赖与字段来源必须在同一事务内解除。"""
    removed = [
        link for link in spec.links
        if link.target_step_id == step.step_id
        and _strip_body_prefix(link.target_path) == _strip_body_prefix(param.path)
    ]
    for link in removed:
        _record_rejected_dependency(spec, link)
    if removed:
        removed_ids = {link.link_id for link in removed}
        spec.links = [link for link in spec.links if link.link_id not in removed_ids]


def _apply_link_sources(steps: list[FlowStep], links: list[FlowLink]) -> None:
    by_id = {s.step_id: s for s in steps}
    for lk in links:
        target = by_id.get(lk.target_step_id)
        source = by_id.get(lk.source_step_id)
        if target is None or source is None:
            continue
        target_path = _strip_body_prefix(lk.target_path)
        for p in target.params:
            if p.path != target_path:
                continue
            if not _auto_dependency_link_allowed(p, lk.source_path, lk):
                continue
            p.category = "runtime_var"
            p.source_kind = "previous_response"
            p.source = {
                "kind": "previous_response",
                "step_id": source.step_id,
                "step_name": source.name,
                "response_path": lk.source_path,
                "target_path": target_path,
                "link_id": lk.link_id,
            }
            # 运行期绑定不等于只读；用户仍可在工作台解除或改写来源。
            p.editable = True
            p.exposed_to_user = False
            p.reason = (
                f"该字段由上一步 `{source.name or source.path}` 的响应 `{lk.source_path}` 提供，"
                "运行期自动注入，不能使用录制旧值"
            )
            p.need_human_confirm = not bool(lk.confirmed)
            p.confidence = max(float(p.confidence or 0.0), float(lk.confidence or 0.0))
            p.confidence_tier = "linked"
            if p.key in target.sample_inputs:
                target.sample_inputs.pop(p.key, None)
            break


def _link_is_auto_generated(lk: FlowLink) -> bool:
    reason = str(lk.reason or "")
    evidence = lk.evidence if isinstance(lk.evidence, dict) else {}
    return (
        not getattr(lk, "locked", False)
        and (
            "自动" in reason
            or "值" in reason
            or "匹配" in reason
            or evidence.get("kind") == "value_match"
            or evidence.get("auto_rebuilt") is True
        )
    )


def _auto_dependency_target_allowed(param: ParamField | None) -> bool:
    if param is None:
        return False
    if param.source_kind in _OPTION_SOURCE_KINDS:
        return False
    if param.type in {"enum", "list-enum"}:
        return False
    if param.enum_options:
        return False
    if _looks_pagination_field(param.key, param.path):
        return False
    if _looks_system_const_field(param.key, param.path):
        return False
    if param.category in {"system_const"}:
        return False
    if param.source_kind in {"constant", "page_context", "system_time", "system_generated", "computed", "current_user"}:
        return False
    return True


def _auto_dependency_link_allowed(param: ParamField | None, source_path: str, lk: FlowLink | None = None) -> bool:
    if lk is not None and not _link_is_auto_generated(lk):
        return True
    if param is not None and lk is not None and lk.confirmed and float(lk.confidence or 0.0) >= 0.95:
        source_leaf = re.sub(r"[^a-z0-9]+", "", str(source_path or "").split(".")[-1].lower())
        target_leaf = re.sub(r"[^a-z0-9]+", "", str(param.path or param.key or "").split(".")[-1].lower())
        # 完整事实库已证明该真实值只来自一个响应端点时，允许通用 id -> *Id
        # 注入（典型为 data.id -> query.processDefinitionId）。这比字段名模糊匹配强，
        # 同时仍拒绝 title/date/status 等常见值造成的假关联。
        if source_leaf == "id" and target_leaf.endswith("id"):
            return True
    if not _auto_dependency_target_allowed(param):
        return False
    if param is None:
        return False
    if param.category == "user_param" or param.source_kind == "user_input" or _looks_user_entered_business_field(param.key, param.path):
        if "[" in str(source_path or ""):
            return False
        return _dependency_match_score(param, source_path) >= 12
    return True


def _prune_unsafe_auto_links(steps: list[FlowStep], links: list[FlowLink]) -> None:
    by_id = {s.step_id: s for s in steps}
    kept: list[FlowLink] = []
    for lk in links:
        if not _link_is_auto_generated(lk):
            kept.append(lk)
            continue
        target = by_id.get(lk.target_step_id)
        target_path = _strip_body_prefix(lk.target_path)
        param = next((p for p in (target.params if target else []) if p.path == target_path), None)
        if _auto_dependency_link_allowed(param, lk.source_path, lk):
            kept.append(lk)
    links[:] = kept


def _sync_link_sources(steps: list[FlowStep], links: list[FlowLink]) -> None:
    _prune_unsafe_auto_links(steps, links)
    valid_targets = {
        (lk.link_id, lk.target_step_id, _strip_body_prefix(lk.target_path))
        for lk in links
    }
    for st in steps:
        for p in st.params:
            if p.source_kind != "previous_response":
                continue
            link_id = p.source.get("link_id")
            if (link_id, st.step_id, p.path) in valid_targets:
                continue
            _reset_param_source(p, reason="上游依赖已删除或目标已改变，字段已恢复为用户输入")
    _apply_link_sources(steps, links)


def _merge_flow_read_sources(explicit_reads: list[dict], captured_requests: list[dict], request_roles: list[dict]) -> list[dict]:
    """把录制全量请求里的读响应也作为字段候选源。

    recorder 现在会把 GET/POST 查询放进 captured_requests；字段下拉/选人绑定不能只依赖旧 reads 通道。
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(url: str, payload: Any, *, role: str = "") -> None:
        if payload is None:
            return
        key = (url or "", json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)[:500])
        if key in seen:
            return
        seen.add(key)
        out.append({"url": url or "", "json": payload, "role": role or ""})

    for r in explicit_reads or []:
        add(
            r.get("url") or "",
            r.get("json", r.get("response_json")),
        role=str(r.get("role") or r.get("request_role") or "explicit_read_option"),
        )
    for req, role in zip(captured_requests or [], request_roles or []):
        if role.get("role") not in {"read_option", "read_context", "business_get"}:
            continue
        add(
            req.get("url") or "",
            req.get("response_json", req.get("json")),
            role=str(role.get("role") or ""),
        )
    return out


def to_flow_spec(
    captured_requests: list[dict],
    *,
    reads: list[dict] | None = None,
    samples: dict | None = None,
    storage_state: dict | None = None,
    required_labels: set | None = None,
    page_enum_options: dict | None = None,
    recording_mode: str = "",
    diagnostics: list[dict] | None = None,
    tenant: str = "",
    subsystem: str = "",
) -> FlowSpec:
    """收敛：把 record_ws 现有产物 → FlowSpec（包含 GET 业务请求）。"""
    reads = reads or []
    samples = samples or {}
    required_labels = required_labels or set()
    page_enum_options = page_enum_options or {}
    diagnostics = diagnostics or []
    recording_mode = recording_mode or "unknown"

    request_roles = []
    for request in captured_requests:
        recorded = request.get("_request_role") if isinstance(request.get("_request_role"), dict) else None
        if recorded is None and request.get("role"):
            recorded = {
                "role": request.get("role"),
                "keep": request.get("keep"),
                "reason": request.get("reason") or request.get("keep_reason") or "",
                "confidence": request.get("confidence") or 0.0,
                "evidence": request.get("evidence") or {},
            }
        request_roles.append(recorded or classify_network_request(request, captured_requests, samples))
    role_by_key = {_request_role_key(r): role for r, role in zip(captured_requests, request_roles)}
    flow_reads = _merge_flow_read_sources(reads, captured_requests, request_roles)

    # 1) 业务写请求
    write_cands = [
        c for c in json_write_requests(captured_requests)
        if (role_by_key.get(_request_role_key(c), {}).get("keep")
            and role_by_key.get(_request_role_key(c), {}).get("role") in {"submit_anchor", "business_write"})
    ]

    # 2) 前置读候选：business_get 直接进入候选；存在写锚点时，把 read_context
    # 也交给后续数据/控制依赖闭包判断。这里不再用 keep 先删掉事实，否则审批详情
    # 这类“响应不直接进入 POST”的控制前置永远没有机会被识别。
    preread_cands = [
        r for r in captured_requests
        if (
            role_by_key.get(_request_role_key(r), {}).get("role") == "business_get"
            or (
                bool(write_cands)
                and role_by_key.get(_request_role_key(r), {}).get("role") == "read_context"
            )
        )
    ]
    preread_before_dedupe = len(preread_cands)
    preread_cands = _dedupe_preread_candidates(preread_cands)

    if not write_cands and not preread_cands:
        request_graph = _build_request_graph(captured_requests, request_roles, set())
        empty_spec = FlowSpec(
            tenant=tenant,
            subsystem=subsystem,
            title="(未捕获到业务请求)",
            recording_mode=recording_mode,
            diagnostics=diagnostics,
            request_facts=_request_facts_from_graph(
                request_graph,
                diagnostics=diagnostics,
                page_enum_options=page_enum_options,
            ),
            goal=RecordedGoal(
                intent="录制业务请求",
                required_inputs=[],
                success_criteria=["重新录制后捕获至少一个业务 GET 或写请求"],
                output_expectation=["生成可编辑 FlowSpec"],
                forbidden_actions=["删除", "作废", "撤销", "终止", "驳回"],
                risk_level="L1",
                capabilities=[],
            ).model_dump(),
            meta={
                "captured_total": len(captured_requests),
                "captured_write_candidates": 0,
                "reads_count": len(flow_reads),
                "request_roles": request_roles,
                "request_graph": request_graph,
                "recording_mode": recording_mode,
                "diagnostics": diagnostics,
                "note": "录制未抓到任何业务写请求或业务 GET；用户可能未点提交，或页面是纯 GET 表单",
            },
        )
        return ensure_flow_version(refresh_review_items(empty_spec), "recorded", reason="录制生成空 FlowSpec")

    # 3) 自动建议流程步
    write_idxs = suggest_workflow_steps(write_cands, samples) if write_cands else []
    if not write_idxs:
        if write_cands:
            submit = pick_submit_request(write_cands, samples)
            if submit is not None:
                write_idxs = [write_cands.index(submit)]
        if not write_idxs and write_cands:
            write_idxs = [len(write_cands) - 1]

    selected_write_keys = {_request_role_key(write_cands[i]) for i in write_idxs if 0 <= i < len(write_cands)}
    preread_keys = {_request_role_key(r) for r in preread_cands}
    potential_keys = selected_write_keys | preread_keys
    potential_steps = [r for r in captured_requests if _request_role_key(r) in potential_keys]
    # 停止并分析时一次性物化：写请求依赖闭包 + 高置信独立业务查询。
    # 之后 Planner 只负责能力分组/契约修复，不能反复从 RequestFacts 偷偷扩张接口。
    required_positions = {
        idx for idx, req in enumerate(potential_steps)
        if _request_role_key(req) in selected_write_keys
    }

    def workflow_context_values(request: dict) -> set[str]:
        values: set[str] = set()
        for field_path, raw_value in _request_values(request):
            value = str(raw_value or "").strip()
            norm_path = re.sub(r"[^a-z0-9]+", "", str(field_path or "").lower())
            if len(value) < 6 or value.lower() in _BORING_LINK_VALUES:
                continue
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ t].*)?", value):
                continue
            if any(token in norm_path for token in (
                "process", "definition", "flow", "billtype", "formtype",
                "businesstype", "template", "appkey", "processdefkey",
            )) or ((request.get("method") or "").upper() == "GET" and norm_path.endswith("key")):
                values.add(value)
        return values

    write_context = {
        value
        for idx, request in enumerate(potential_steps)
        if idx in required_positions
        for value in workflow_context_values(request)
    }

    def same_workflow_context(left: str, right: str) -> bool:
        if left == right:
            return True
        shorter, longer = sorted((left, right), key=len)
        if len(shorter) < 6 or not longer.startswith(shorter):
            return False
        return longer[len(shorter):len(shorter) + 1] in {":", "/", "-", "_"}

    for idx, request in enumerate(potential_steps):
        if _request_role_key(request) not in preread_keys:
            continue
        if any(
            same_workflow_context(candidate, write_value)
            for candidate in workflow_context_values(request)
            for write_value in write_context
        ):
            required_positions.add(idx)
    try:
        potential_links = discover_step_links(potential_steps)
    except Exception:
        potential_links = []
    changed = True
    while changed:
        changed = False
        for link in potential_links:
            source_pos = link.get("source_step")
            target_pos = link.get("target_step")
            if target_pos in required_positions and source_pos not in required_positions:
                required_positions.add(source_pos)
                changed = True
            # 控制前置链：某个已选 workflow GET 的响应驱动后续 workflow GET query
            # 时，二者共同属于写能力前置闭包，即使后者响应不直接进入 POST body。
            if (
                source_pos in required_positions
                and target_pos not in required_positions
                and isinstance(target_pos, int)
                and 0 <= target_pos < len(potential_steps)
                and _request_role_key(potential_steps[target_pos]) in preread_keys
            ):
                required_positions.add(target_pos)
                changed = True
    selected_preread_keys = {
        _request_role_key(potential_steps[idx])
        for idx in required_positions
        if 0 <= idx < len(potential_steps) and _request_role_key(potential_steps[idx]) in preread_keys
    }
    independent_business_keys = {
        _request_role_key(request)
        for request in preread_cands
        if str((role_by_key.get(_request_role_key(request)) or {}).get("role") or "") == "business_get"
        and float((role_by_key.get(_request_role_key(request)) or {}).get("confidence") or 0.0) >= 0.9
    }
    selected_keys = selected_write_keys | selected_preread_keys | independent_business_keys
    request_graph = _build_request_graph(captured_requests, request_roles, selected_keys)
    cands = [r for r in captured_requests if _request_role_key(r) in selected_keys]

    # 4) 每条 → FlowStep
    step_objs: list[FlowStep] = []
    idx_to_step_id: dict[int, str] = {}
    for pos, req in enumerate(cands):
        request_role = role_by_key.get(_request_role_key(req), {})
        st = _build_step_from_capture(
            _attach_request_role(req, request_role),
            reads=flow_reads,
            samples=samples,
            storage_state=storage_state,
            required_labels=required_labels,
            page_enum_options=page_enum_options,
            step_index=pos,
        )
        if _request_role_key(req) in selected_preread_keys:
            st.source_meta = {
                **(st.source_meta or {}),
                "control_preflight_for_write": True,
            }
        step_objs.append(st)
        idx_to_step_id[pos] = st.step_id

    # 5) 多步 link（自动值驱动）
    link_objs: list[FlowLink] = []
    if len(step_objs) > 1:
        try:
            raw_links = discover_step_links(cands)
            for lk in raw_links:
                src_pos, tgt_pos = lk.get("source_step"), lk.get("target_step")
                if src_pos not in idx_to_step_id or tgt_pos not in idx_to_step_id:
                    continue
                target_step = step_objs[tgt_pos]
                target_path = _strip_body_prefix(str(lk.get("target_path", "")))
                target_param = next((p for p in target_step.params if p.path == target_path), None)
                target_value = str(target_param.value if target_param is not None else "")
                matching_sources: set[tuple[str, str, str]] = set()
                # 唯一性必须以完整请求事实库为准，不能只看已物化步骤；两个候选 GET
                # 返回同一 ID 时，即使去重后只保留一个步骤，也仍属于歧义证据。
                for candidate_request in captured_requests:
                    response_payload = candidate_request.get("response_json")
                    if response_payload is None:
                        continue
                    for response_path, _tokens, scalar, _raw in _leaf_paths(response_payload):
                        if target_value and str(scalar) == target_value:
                            matching_sources.add((
                                str(candidate_request.get("method") or "GET").upper(),
                                _request_path(candidate_request),
                                response_path,
                            ))
                strong_unique_match = (
                    len(target_value) >= 4
                    and len(matching_sources) == 1
                    and target_value.lower() not in {"true", "false", "null", "none", "success"}
                )
                source_leaf = re.sub(
                    r"[^a-z0-9]+", "", str(lk.get("source_path") or "").split(".")[-1].lower()
                )
                target_leaf = re.sub(
                    r"[^a-z0-9]+", "", str(target_path or "").split(".")[-1].lower()
                )
                strong_id_dependency = strong_unique_match and source_leaf == "id" and target_leaf.endswith("id")
                if not strong_id_dependency and not _auto_dependency_link_allowed(
                    target_param, str(lk.get("source_path") or ""),
                ):
                    continue
                link_objs.append(FlowLink(
                    source_step_id=idx_to_step_id[src_pos],
                    source_path=lk.get("source_path", ""),
                    source_tokens=lk.get("source_tokens"),
                    target_step_id=idx_to_step_id[tgt_pos],
                    target_path=lk.get("target_path", ""),
                    target_tokens=lk.get("target_tokens"),
                    param_name=None,
                    confirmed=strong_unique_match,
                    confidence=0.96 if strong_unique_match else 0.85,
                    reason="上游响应值与下游请求字段值一致，判定为运行期依赖",
                    evidence={
                        "source_step": src_pos,
                        "target_step": tgt_pos,
                        "source_path": lk.get("source_path", ""),
                        "target_path": lk.get("target_path", ""),
                    },
                ))
        except Exception:
            link_objs = []
    _sync_link_sources(step_objs, link_objs)

    # 6) 流程整体风险
    overall = "L1"
    for st in step_objs:
        rl = st.risk_level
        if rl == "L4":
            overall = "L4"
            break
        if rl == "L3" and overall != "L4":
            overall = "L3"

    # 7) fact_check
    fc = suggest_fact_check(samples, flow_reads)
    if step_objs and fc:
        step_objs[-1].fact_check = fc

    # 8) title
    title = _derive_title(step_objs)

    spec = FlowSpec(
        tenant=tenant,
        subsystem=subsystem,
        title=title,
        business_description="",
        recording_mode=recording_mode,
        diagnostics=diagnostics,
        steps=step_objs,
        links=link_objs,
        goal={},
        risk_level=overall,
        request_facts=_request_facts_from_graph(
            request_graph,
            diagnostics=diagnostics,
            page_enum_options=page_enum_options,
        ),
        meta={
            "captured_total": len(captured_requests),
            "captured_write_candidates": len(write_cands),
            "captured_business_gets": len([r for r in request_roles if r.get("role") == "business_get"]),
            "captured_preread_candidates_before_dedupe": preread_before_dedupe,
            "captured_preread_candidates": len(preread_cands),
            "captured_workflow_steps": len(step_objs),
            "reads_count": len(flow_reads),
            "request_roles": request_roles,
            "request_graph": request_graph,
            "recording_mode": recording_mode,
            "diagnostics": diagnostics,
            "schema_version": 1,
        },
    )
    _infer_computed_runtime_fields(spec)
    return ensure_flow_version(refresh_review_items(ensure_recorded_goal(spec)), "recorded", reason="录制生成 FlowSpec 初版")


def _derive_title(steps: list[FlowStep]) -> str:
    if not steps:
        return ""
    first = next((s for s in reversed(steps) if (s.method or "").upper() not in {"GET", "HEAD", "OPTIONS"}), steps[-1])
    try:
        url = first.url or first.path
        path = urlparse(url).path if url.startswith("http") else url
    except Exception:
        path = first.path
    segs = [s for s in (path or "").split("/") if s]
    last = segs[-1].split("?")[0] if segs else ""
    if not last:
        return first.name or "(未命名)"
    if len(steps) > 1:
        return f"{last} 流程({len(steps)} 步)"
    return last


def _date_like_epoch_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
        return number / 1000.0 if abs(number) >= 10**11 else number
    except (TypeError, ValueError):
        pass
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _infer_computed_runtime_fields(spec: FlowSpec) -> None:
    """Hide serialized date-span query variables when their sample proves the formula."""
    params = [param for step in spec.steps for param in (step.params or [])]
    def leaf_name(param: ParamField) -> str:
        raw = param.key or str(param.path or "").split(".")[-1]
        return re.sub(r"[^a-z0-9]+", "", str(raw).lower())

    start = next((param for param in params if re.fullmatch(r"(?:start|begin)(?:time|date)?", leaf_name(param))), None)
    end = next((param for param in params if re.fullmatch(r"(?:end|finish|back)(?:time|date)?", leaf_name(param))), None)
    if start is None or end is None:
        return
    start_seconds = _date_like_epoch_seconds(start.value)
    end_seconds = _date_like_epoch_seconds(end.value)
    if start_seconds is None or end_seconds is None:
        return
    observed_days = int(round(abs(end_seconds - start_seconds) / 86400.0))
    for step in spec.steps:
        for param in step.params or []:
            if param.locked or not str(param.path or "").startswith("query."):
                continue
            key_norm = leaf_name(param)
            if not re.search(r"(process)?variables?(str)?$|context(json|str)?$", key_norm):
                continue
            try:
                payload = json.loads(str(param.value or ""))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(payload, dict) or len(payload) != 1:
                continue
            output_key, sample_value = next(iter(payload.items()))
            if str(output_key).lower() not in {"day", "days", "duration", "durationdays"}:
                continue
            try:
                if int(sample_value) != observed_days:
                    continue
            except (TypeError, ValueError):
                continue
            param.category = "runtime_var"
            param.source_kind = "computed"
            param.source = {
                "kind": "computed",
                "strategy": "date_span_days_json",
                "start_field": start.key,
                "end_field": end.key,
                "output_key": str(output_key),
                "path": param.path,
            }
            param.exposed_to_user = False
            param.need_human_confirm = False
            param.reason = f"录制样例证明该字段由 `{start.key}` 与 `{end.key}` 的日期跨度生成，运行期自动计算"
            step.sample_inputs.pop(param.key, None)


def _schema_for_param_type(ptype: str) -> dict[str, Any]:
    t = (ptype or "string").lower()
    if t in {"number", "integer"}:
        return {"type": "number"}
    if t == "boolean":
        return {"type": "boolean"}
    if t == "date":
        return {"type": "string", "format": "date"}
    if t == "datetime":
        return {"type": "string", "format": "date-time"}
    if t in {"list-enum", "array"}:
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def _business_type_for_param(param: ParamField) -> str:
    ptype = (param.type or "string").lower()
    if ptype == "list-enum":
        return "multi_enum"
    if ptype == "enum" or param.source_kind in _OPTION_SOURCE_KINDS:
        return "single_enum"
    return {
        "datetime": "datetime",
        "date": "date",
        "number": "number",
        "integer": "number",
        "boolean": "boolean",
        "array": "array",
        "object": "object",
    }.get(ptype, "text")


def _capability_input_schema(params: list[ParamField]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in params:
        if p.category != "user_param" or not p.exposed_to_user:
            continue
        key = p.key or p.path
        if key in props:
            existing = props[key]
            candidate_business = _business_type_for_param(p)
            candidate_wire = p.wire_type or _infer_type_from_value(p.value) or "string"
            if (
                existing.get("x-dano-business-type") != candidate_business
                or existing.get("x-dano-wire-type") != candidate_wire
                or existing.get("x-flow-path") != p.path
            ):
                existing.setdefault("x-dano-conflicts", []).append({
                    "path": p.path,
                    "business_type": candidate_business,
                    "wire_type": candidate_wire,
                })
            if p.required and key not in required:
                required.append(key)
            continue
        props[key] = _schema_for_param_type(p.type)
        props[key]["x-flow-path"] = p.path
        props[key]["x-dano-business-type"] = _business_type_for_param(p)
        props[key]["x-dano-wire-type"] = p.wire_type or _infer_type_from_value(p.value) or "string"
        if p.label:
            props[key]["label"] = p.label
        if p.description or p.reason:
            props[key]["description"] = p.description or p.reason
        dynamic_options = p.source_kind == "api_option"
        enum_confirmed = (p.source or {}).get("enum_confirmed")
        incomplete_page_enum = p.source_kind == "page_enum" and enum_confirmed is False
        if p.type in {"enum", "list-enum"} or p.source_kind in _OPTION_SOURCE_KINDS:
            if p.type == "list-enum":
                props[key].setdefault("items", {})["format"] = "name-ref"
            else:
                props[key]["format"] = "name-ref"
        if dynamic_options:
            props[key]["x-options-source"] = True
            props[key]["x-options-source-meta"] = dict(p.source or {})
        if incomplete_page_enum:
            props[key]["x-options-incomplete"] = True
        if p.enum_options:
            # API-backed people/department/dictionary choices are a recording-time
            # snapshot, not a stable caller constraint. Keep the snapshot only as
            # evidence and require a live lookup at invocation time.
            props[key]["x-options-snapshot" if (dynamic_options or incomplete_page_enum) else "x-options"] = list(p.enum_options)
            labels: list[str] = []
            for option in p.enum_options:
                pair = _enum_label_value(option)
                if pair:
                    labels.append(str(pair[0]))
                elif option not in (None, ""):
                    labels.append(str(option))
            if labels and not dynamic_options and not incomplete_page_enum:
                if p.type == "list-enum":
                    props[key].setdefault("items", {})["enum"] = labels
                else:
                    props[key]["enum"] = labels
        if p.enum_value_map:
            props[key]["x-enum-value-map"] = dict(p.enum_value_map)
        if p.required:
            required.append(key)
    return {"type": "object", "properties": props, "required": required}


def _schema_from_response_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"type": "number"}
    if isinstance(value, list):
        return {"type": "array", "items": _schema_from_response_value(value[0]) if value else {}}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {
                str(k): _schema_from_response_value(v)
                for k, v in list(value.items())[:80]
            },
        }
    return {"type": "string"}


def _recorded_goal_from_parts(title: str, steps: list[FlowStep], risk_level: str) -> dict[str, Any]:
    write_steps = [s for s in steps if _is_write_step(s)]
    read_steps = [s for s in steps if not _is_write_step(s)]
    params: list[str] = []
    for st in steps:
        for p in st.params:
            if p.category == "user_param" and p.exposed_to_user and p.key and p.key not in params:
                params.append(p.key)
    capabilities: list[str] = []
    if read_steps:
        capabilities.append("query_status")
    if any(st.selects or any(p.enum_options for p in st.params) for st in steps):
        capabilities.append("list_options")
    if write_steps:
        capabilities.append("submit_batch" if any(_looks_batch_step(s) for s in write_steps) else "submit")
    intent = title or (write_steps[-1].name if write_steps else (read_steps[-1].name if read_steps else "录制业务流程"))
    goal = RecordedGoal(
        intent=intent,
        required_inputs=params,
        success_criteria=[
            "所有必填业务字段都有确定来源",
            "提交接口返回成功规则通过" if write_steps else "查询接口返回可解析结果",
            "已纳入能力闭包的接口按依赖顺序执行",
        ],
        output_expectation=[
            "返回所调用能力的最终响应",
            "批量提交时返回 success_count、failed_items 和每条结果" if any(_looks_batch_step(s) for s in write_steps) else "返回执行状态和原始响应",
        ],
        forbidden_actions=["删除", "作废", "撤销", "终止", "驳回"],
        risk_level=risk_level or "L3",
        capabilities=capabilities,
        evidence=[_step_evidence(s) for s in steps[:20]],
    )
    return goal.model_dump(exclude_none=True)


def _recorded_user_param_names(steps: list[FlowStep]) -> list[str]:
    params: list[str] = []
    for st in steps:
        for p in st.params:
            if p.category == "user_param" and p.exposed_to_user and p.key and p.key not in params:
                params.append(p.key)
    return params


def ensure_recorded_goal(spec: FlowSpec) -> FlowSpec:
    active_step_ids = _active_capability_step_ids(spec)
    goal_steps = [
        step for step in spec.steps
        if active_step_ids is None or step.step_id in active_step_ids
    ]
    fresh = _recorded_goal_from_parts(spec.title, goal_steps, spec.risk_level)
    if not spec.goal:
        spec.goal = fresh
        return spec
    goal = dict(spec.goal or {})
    # 字段改名/分类/暴露状态会改变最终 Skill 参数。Goal 的 required_inputs 必须跟当前
    # FlowSpec 保持一致，否则发布层会把旧字段名误判成“LLM 臆造字段”并阻断。
    current_inputs = _recorded_user_param_names(goal_steps)
    goal["required_inputs"] = current_inputs
    goal.setdefault("intent", fresh.get("intent") or spec.title)
    goal.setdefault("success_criteria", fresh.get("success_criteria") or [])
    goal.setdefault("output_expectation", fresh.get("output_expectation") or [])
    goal.setdefault("forbidden_actions", fresh.get("forbidden_actions") or [])
    goal.setdefault("risk_level", fresh.get("risk_level") or spec.risk_level or "L3")
    actual_capabilities = [
        str(cap.name or cap.capability_id)
        for cap in (spec.capabilities or [])
        if str(cap.name or cap.capability_id)
    ]
    goal["capabilities"] = actual_capabilities if spec.capabilities else (fresh.get("capabilities") or [])
    goal["evidence"] = fresh.get("evidence") or goal.get("evidence") or []
    spec.goal = goal
    return spec


def _normalize_generated_capability_semantics(spec: FlowSpec, cap: FlowCapability) -> None:
    """Align Planner capabilities with the recorded request evidence before validation."""
    public_names = {"query_status", "list_options", "validate_batch", "submit_batch", "submit"}
    if cap.name in public_names and cap.kind in public_names and cap.name != cap.kind:
        cap.name = cap.kind
        if cap.kind == "submit" and "批量" in str(cap.title or ""):
            cap.title = str(cap.title).replace("批量", "", 1) or "提交"
        elif cap.kind == "submit_batch" and "批量" not in str(cap.title or ""):
            cap.title = "批量" + (str(cap.title or "提交"))
    duplicate_generated_name = bool(re.fullmatch(r"submit_batch\d+", str(cap.name or "")))
    needs_batch_audit = cap.kind in {"submit_batch", "validate_batch"}
    if cap.locked or (not cap.evidence and not duplicate_generated_name and not needs_batch_audit):
        return
    by_id = {step.step_id: step for step in spec.steps}
    steps = [by_id[sid] for sid in (cap.step_ids or []) if sid in by_id]
    if not steps:
        return
    writes = [step for step in steps if _is_write_step(step)]
    if cap.kind in {"submit", "submit_batch", "validate_batch"} and writes:
        actual_batch = _write_contract_is_batch(spec, writes, cap)
        if cap.kind == "submit_batch" and not actual_batch:
            cap.kind = "submit"
            if re.fullmatch(r"submit_batch\d*", str(cap.name or "")):
                cap.name = "submit"
            if "批量提交" in str(cap.title or ""):
                cap.title = str(cap.title).replace("批量提交", "提交")
            cap.intent = "调用方提供业务字段；Skill 按能力内接口顺序执行前置查询、依赖注入和最终提交。"
    if cap.kind == "query_status":
        status_ids = {step.step_id for step in _read_status_steps(spec)}
        cap.step_ids = [step.step_id for step in steps if step.step_id in status_ids]
    elif cap.kind == "list_options":
        # 下拉来源属于字段执行细节，不自动暴露成独立业务能力。
        cap.step_ids = []


def _canonicalize_public_capability_identities(spec: FlowSpec) -> FlowSpec:
    """Atomically align public names and every cross-capability reference."""
    public_names = {"query_status", "list_options", "validate_batch", "submit_batch", "submit"}
    renamed: dict[str, str] = {}
    for cap in spec.capabilities or []:
        old_name = str(cap.name or "")
        kind = str(cap.kind or "")
        stale_standard_alias = old_name in public_names and old_name != kind
        stale_generated_alias = bool(
            kind in public_names
            and re.fullmatch(r"(?:query_status|list_options|validate_batch|submit_batch|submit)\d*", old_name)
        )
        if kind in public_names and (stale_standard_alias or stale_generated_alias or not old_name):
            cap.name = kind
            if old_name and old_name != kind:
                renamed[old_name] = kind
    if not renamed:
        return spec
    for relation in spec.capability_relations or []:
        relation.from_capability = renamed.get(relation.from_capability, relation.from_capability)
        relation.to_capability = renamed.get(relation.to_capability, relation.to_capability)
    if isinstance(spec.goal, dict):
        spec.goal["capabilities"] = list(dict.fromkeys(
            renamed.get(str(name), str(name)) for name in (spec.goal.get("capabilities") or []) if str(name)
        ))
    return spec


def _repair_generated_capability_contracts(spec: FlowSpec) -> FlowSpec:
    """Deterministically repair only Planner-generated capability contracts."""
    _infer_computed_runtime_fields(spec)
    _repair_versioned_workflow_id_links(spec)
    _normalize_stable_workflow_constants(spec)
    rebuild_flow_dependencies(spec)
    by_id = {step.step_id: step for step in spec.steps}
    renamed: dict[str, str] = {}
    for cap in spec.capabilities or []:
        old_name = cap.name
        was_generated_duplicate = bool(re.fullmatch(r"submit_batch\d+", str(cap.name or "")))
        needed_batch_audit = cap.kind in {"submit_batch", "validate_batch"}
        _normalize_generated_capability_semantics(spec, cap)
        if old_name and cap.name and old_name != cap.name:
            renamed[old_name] = cap.name
        if cap.locked or (not cap.evidence and not was_generated_duplicate and not needed_batch_audit):
            continue
        cap.nodes = _sanitize_capability_nodes(spec, cap)
        cap.nodes = [
            node for node in (cap.nodes or [])
            if not (
                isinstance(node, dict)
                and node.get("type") == "condition"
                and not any(
                    isinstance(node.get(key), list) and node.get(key)
                    for key in ("then", "else", "otherwise", "children", "steps")
                )
            )
        ]
        cap_step_ids = set(cap.step_ids or [])
        valid_mapping: list[dict[str, Any]] = []
        for mapping in cap.output_mapping or []:
            if not isinstance(mapping, dict):
                continue
            step_id = str(mapping.get("step_id") or mapping.get("from") or "")
            path = str(mapping.get("response_path") or mapping.get("path") or mapping.get("field") or "response")
            if step_id not in cap_step_ids or not _capability_response_path_exists(by_id.get(step_id), path):
                continue
            valid_mapping.append(dict(mapping))
        if cap.kind == "query_status" and cap_step_ids:
            query_steps = [by_id[sid] for sid in cap.step_ids if sid in by_id]
            semantic_mapping = _query_output_mappings(query_steps)
            if any(str(item.get("response_path") or "") not in {"", "response"} for item in semantic_mapping):
                valid_mapping = semantic_mapping
        if not valid_mapping and cap_step_ids:
            final = next((step for step in reversed(spec.steps) if step.step_id in cap_step_ids), None)
            if final is not None:
                valid_mapping = [{
                    "kind": "final_response",
                    "name": "result",
                    "step_id": final.step_id,
                    "response_path": "response",
                }]
        cap.output_mapping = valid_mapping
    if renamed:
        for relation in spec.capability_relations or []:
            relation.from_capability = renamed.get(relation.from_capability, relation.from_capability)
            relation.to_capability = renamed.get(relation.to_capability, relation.to_capability)
    _canonicalize_public_capability_identities(spec)
    spec = _prune_empty_capabilities(spec)
    valid_refs = {
        ref
        for cap in spec.capabilities or []
        for ref in (str(cap.name or ""), str(cap.capability_id or ""))
        if ref
    }
    cap_by_ref = {
        ref: cap
        for cap in spec.capabilities or []
        for ref in (str(cap.name or ""), str(cap.capability_id or ""))
        if ref
    }
    spec.capability_relations = [
        relation
        for relation in (spec.capability_relations or [])
        if relation.from_capability in valid_refs
        and relation.to_capability in valid_refs
        and not (
            relation.to_input in {"entries", "items"}
            and (cap_by_ref.get(relation.to_capability) is not None)
            and cap_by_ref[relation.to_capability].kind not in {"submit_batch", "validate_batch"}
        )
    ]
    return spec


def _sync_capability_io_schemas(spec: FlowSpec) -> FlowSpec:
    """让 capability 的输入输出 schema 始终跟当前字段/响应保持一致。"""
    _repair_versioned_workflow_id_links(spec)
    _normalize_stable_workflow_constants(spec)
    if not spec.capabilities:
        return spec

    def reconcile_schema(derived: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        """当前有效字段是契约真相；仅保留仍存在字段上的人工说明等扩展。"""
        derived = dict(derived or {"type": "object", "properties": {}, "required": []})
        current = dict(current or {})
        merged = {
            key: value for key, value in current.items()
            if key not in {"properties", "required"}
        }
        merged.update({
            key: value for key, value in derived.items()
            if key not in {"properties", "required"}
        })
        current_props = dict(current.get("properties") or {})
        props: dict[str, Any] = {}
        for name, field_schema in dict(derived.get("properties") or {}).items():
            previous = current_props.get(name)
            if isinstance(previous, dict) and isinstance(field_schema, dict):
                # Type/source keywords are fully derived from ParamField. Keeping
                # old enum/format/x-options values here makes a scalar edit export
                # as a dropdown again. Only human-facing annotations survive a
                # rebuild.
                annotations = {
                    key: value for key, value in previous.items()
                    if key in {"title", "description", "examples", "deprecated"}
                    and key not in field_schema
                }
                props[name] = {**annotations, **field_schema}
            else:
                props[name] = field_schema
        merged["properties"] = props
        required = [
            str(name) for name in (derived.get("required") or [])
            if str(name) in props
        ]
        merged["required"] = list(dict.fromkeys(required))
        return merged

    by_id = {s.step_id: s for s in spec.steps}
    for cap in spec.capabilities:
        if cap.kind == "query_status":
            option_source_ids = _option_source_step_ids(spec)
            memberships = {ref.step_id: ref for ref in (cap.request_refs or []) if ref.step_id}
            cap.step_ids = [
                sid for sid in (cap.step_ids or [])
                if (
                    bool(memberships.get(sid) and memberships[sid].pinned and memberships[sid].usage in {"execute", "preflight", "fact_check"})
                    or (
                        (sid not in option_source_ids or (sid in by_id and _is_business_query_step(by_id[sid])))
                        and (
                            sid not in by_id
                            or ((by_id[sid].source_meta or {}).get("role") or by_id[sid].semantic_role or "") != "read_option"
                            or _is_business_query_step(by_id[sid])
                        )
                    )
                )
            ]
        cap.nodes = _sanitize_capability_nodes(spec, cap)
        cap_steps = [by_id[sid] for sid in (cap.step_ids or []) if sid in by_id]
        if not cap_steps:
            continue
        params = [p for st in cap_steps for p in (st.params or [])]
        derived_input = _capability_input_schema(params)
        if _capability_is_batch(spec, cap):
            derived_input = _batch_capability_input_schema(cap_steps)
        cap.input_schema = reconcile_schema(derived_input, cap.input_schema or {})
        if cap.kind == "query_status":
            cap.output_mapping = _query_output_mappings(cap_steps)
        mapped_output_props: dict[str, Any] = {}
        for mapping_idx, mapping in enumerate(cap.output_mapping or []):
            if not isinstance(mapping, dict):
                continue
            source_step = by_id.get(str(mapping.get("step_id") or ""))
            if source_step is None or source_step.response_json is None:
                continue
            response_path = str(mapping.get("response_path") or mapping.get("path") or "response")
            mapped_value = source_step.response_json
            if response_path not in {"", "response", "$", "."}:
                candidate = _flow_path_lookup(source_step.response_json, response_path)
                if candidate is not _FLOW_PATH_MISSING:
                    mapped_value = candidate
            mapped_output_props[_capability_output_name(mapping, mapping_idx)] = _schema_from_response_value(mapped_value)
        if mapped_output_props:
            cap.output_schema = reconcile_schema({
                "type": "object",
                "properties": mapped_output_props,
                "required": list(mapped_output_props),
            }, cap.output_schema or {})
        else:
            last_response = next((st.response_json for st in reversed(cap_steps) if st.response_json is not None), None)
            if last_response is not None:
                cap.output_schema = reconcile_schema(_schema_from_response_value(last_response), cap.output_schema or {})
    return sync_capability_scoped_views(spec)


def _sanitize_capability_nodes(spec: FlowSpec, cap: FlowCapability) -> list[dict[str, Any]]:
    """Remove deterministically stale planner nodes before exposing validation warnings."""
    by_id = {step.step_id: step for step in spec.steps}
    cap_step_ids = set(cap.step_ids or [])
    is_batch = _capability_is_batch(spec, cap)

    def clean(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in nodes or []:
            if not isinstance(raw, dict):
                continue
            node = dict(raw)
            node_type = str(node.get("type") or "")
            if node_type == "foreach" and not is_batch:
                # Query/list abilities must never retain a batch loop inferred from a URL containing list/batch.
                children = node.get("steps") if isinstance(node.get("steps"), list) else []
                out.extend(clean(children))
                continue
            for child_key in ("children", "steps", "then", "else", "otherwise"):
                if isinstance(node.get(child_key), list):
                    node[child_key] = clean(node[child_key])
            if node_type == "call":
                if str(node.get("step_id") or "") not in cap_step_ids:
                    continue
            elif node_type == "map":
                source = str(node.get("source") or "")
                target = str(node.get("target") or "")
                if not source or not target:
                    continue
                if not is_batch and source.startswith(("item.", "loop.", "input.entries")):
                    continue
                if "." in target and not target.startswith(("var.", "computed.", "loop.", "item.", "node.", "input.")):
                    step_id, path = target.split(".", 1)
                    if step_id in by_id and not _capability_step_param_exists(by_id[step_id], path):
                        continue
            elif node_type == "return":
                ref = str(node.get("from") or node.get("source") or "")
                if ref and ref not in cap_step_ids and not ref.startswith(("input.", "var.", "node.")):
                    node_ids = {str(item.get("id") or "") for item in _iter_capability_nodes(out)}
                    if ref not in node_ids:
                        continue
            out.append(node)
        return out

    cleaned = clean(cap.nodes or [])
    cap.nodes = cleaned
    _sync_capability_order(spec, cap)
    return cap.nodes


def _step_evidence(step: FlowStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "name": step.name,
        "method": (step.method or "").upper(),
        "path": step.path or step.url,
        "role": (step.source_meta or {}).get("role") or step.semantic_role,
    }


def _is_write_step(step: FlowStep) -> bool:
    return (step.method or "").upper() not in {"GET", "HEAD", "OPTIONS"}


def _looks_batch_step(step: FlowStep) -> bool:
    meta = step.source_meta or {}
    if any(bool(meta.get(key)) for key in ("batch", "is_batch", "batch_intent", "repeated_submission")):
        return True
    text = f"{step.name} {step.path} {step.url}".lower()
    if any(x in text for x in ("batch", "bulk", "pclist", "批量")):
        return True
    try:
        body = _parse_body(step.body_source)
    except Exception:
        body = None
    # A large class of enterprise APIs wraps a single form object in ``[{...}]``.
    # Array shape or ``[0].field`` paths alone are therefore not evidence of a
    # caller-visible batch contract. Multiple recorded rows are grounded evidence;
    # a single row remains a normal submit unless URL/metadata says otherwise.
    return isinstance(body, list) and len(body) > 1


_REPEATED_DATE_FIELD_RE = re.compile(
    r"(?:^|[.\[_\s-])(?:date|day|reportdate|workdate|填报日期|日报日期|工作日期)(?:$|[.\]_\s-])",
    re.I,
)
_REPEATED_CONTENT_FIELD_RE = re.compile(
    r"(?:content|workcontent|reportcontent|工作内容|日报内容|日志内容)", re.I,
)


def _query_implies_repeated_submit(spec: FlowSpec, write_steps: list[FlowStep]) -> bool:
    """Ground foreach intent in a query result plus one-day business fields.

    This recognizes the common query-missing-days -> caller confirmation/content
    split -> repeated single-row submit workflow without treating leave ranges or
    approval-person arrays as batch business rows.
    """
    has_missing_dates = any(
        any(
            re.search(r"(?:missing|unfilled|未填|待填).*(?:date|day|日期|天)", path, re.I)
            or (
                re.search(r"(?:missing|unfilled|未填|待填)", path, re.I)
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or ""))
            )
            for path, _tokens, value, _raw in _leaf_paths(step.response_json)
        )
        for step in spec.steps
        if (step.method or "GET").upper() == "GET"
    )
    if not has_missing_dates:
        return False
    texts = [
        " ".join(str(value or "") for value in (param.path, param.key, param.label, param.description))
        for step in write_steps for param in step.params
    ]
    return any(_REPEATED_DATE_FIELD_RE.search(text) for text in texts) and any(
        _REPEATED_CONTENT_FIELD_RE.search(text) for text in texts
    )


_ROUTING_FIELD_RE = re.compile(
    r"(?:approv|assignee|reviewer|audit|leader|manager|hr|cc|copy|审批|审核|领导|人力|抄送|经办)",
    re.I,
)


def _is_routing_or_approval_param(param: ParamField) -> bool:
    """Return true for workflow routing fields, not repeated business rows."""
    text = " ".join(str(value or "") for value in (
        param.path, param.key, param.label, param.description,
    ))
    return bool(_ROUTING_FIELD_RE.search(text))


def _capability_has_explicit_batch_intent(cap: FlowCapability) -> bool:
    """Only preserve a caller-visible batch contract when it has grounded intent."""
    if any(
        isinstance(item, dict)
        and any(bool(item.get(key)) for key in ("batch", "batch_intent", "repeated_submission"))
        for item in (cap.evidence or [])
    ):
        return True
    # A user-authored/locked foreach over input.entries is an explicit reusable
    # batch design. Planner-generated loops alone are not evidence.
    has_entries_loop = any(
        node.get("type") == "foreach"
        and str(node.get("items") or "") in {"input.entries", "entries"}
        for node in _iter_capability_nodes(cap.nodes or [])
    )
    if has_entries_loop and (cap.updated_by == "user" or cap.locked):
        return True
    if any(
        (field.key or field.path) in {"entries", "items"}
        and (field.confirmed or field.locked or cap.updated_by == "user")
        for field in (cap.inputs or [])
    ):
        return True
    # Planner-created foreach/schema is a proposal, not evidence. It may only
    # become public batch behavior through recorded request shape/query evidence
    # or an explicit operator edit handled above.
    return False


def _write_contract_is_batch(
    spec: FlowSpec,
    write_steps: list[FlowStep],
    cap: FlowCapability | None = None,
) -> bool:
    """Return the single reproducible submit/submit_batch decision."""
    return bool(
        any(_looks_batch_step(step) for step in write_steps)
        or _query_implies_repeated_submit(spec, write_steps)
        or (cap is not None and _capability_has_explicit_batch_intent(cap))
    )


def _default_capability_nodes(
    steps: list[FlowStep], *, kind: str, force_batch: bool = False,
) -> list[dict[str, Any]]:
    if not steps:
        return []
    if kind == "submit_batch" and (force_batch or any(_looks_batch_step(s) for s in steps)):
        read_steps = [s for s in steps[:-1] if not _is_write_step(s)]
        final = steps[-1]
        nodes = [
            {
                "id": f"call_{idx}",
                "type": "call",
                "step_id": st.step_id,
                "method": st.method,
                "path": st.path or st.url,
            }
            for idx, st in enumerate(read_steps, 1)
        ]
        nodes.append({
            "id": "foreach_entries",
            "type": "foreach",
            "items": "input.entries",
            "as": "item",
            "steps": [{
                "id": "call_submit_each",
                "type": "call",
                "step_id": final.step_id,
                "method": final.method,
                "path": final.path or final.url,
            }],
        })
        nodes.append({"id": "return_batch_result", "type": "return", "value": "batch_result"})
        return nodes
    return _capability_call_nodes(steps)


def _legacy_suggest_flow_capabilities(spec: FlowSpec) -> list[FlowCapability]:
    """从真实录制步骤生成最小业务能力层。"""
    caps: list[FlowCapability] = []
    read_steps = [s for s in spec.steps if not _is_write_step(s)]
    write_steps = [s for s in spec.steps if _is_write_step(s)]

    if read_steps:
        caps.append(FlowCapability(
            name="query_status",
            title="查询状态",
            intent="查询业务对象当前状态、已存在记录或可继续处理范围；输出字段需人工确认映射。",
            kind="query_status",
            step_ids=[s.step_id for s in read_steps],
            nodes=_default_capability_nodes(read_steps, kind="query_status"),
            input_schema=_capability_input_schema([p for s in read_steps for p in s.params]),
            output_schema={
                "type": "object",
                "properties": {
                    "filled_dates": {"type": "array", "items": {"type": "string", "format": "date"}},
                    "missing_dates": {"type": "array", "items": {"type": "string", "format": "date"}},
                    "can_submit_dates": {"type": "array", "items": {"type": "string", "format": "date"}},
                    "summary": {"type": "string"},
                },
            },
            confirmed=False,
            confidence=0.9 if kind == "submit_batch" else 0.95,
            requires_human_confirm=True,
            evidence=[_step_evidence(s) for s in read_steps],
            caller_responsibilities=["根据结构化查询结果与最终用户确认下一步"],
            skill_responsibilities=["执行真实查询接口并返回原始响应/结构化映射结果"],
        ))

    option_fields: list[str] = []
    option_step_ids: list[str] = []
    for s in spec.steps:
        for sel in s.selects:
            if sel.param:
                option_fields.append(sel.param)
                option_step_ids.append(s.step_id)
    if option_fields:
        fields = list(dict.fromkeys(option_fields))
        caps.append(FlowCapability(
            name="list_options",
            title="查询实时选项",
            intent="按字段名返回当前可选项，供外部前端展示显示名而不是内部 ID。",
            kind="list_options",
            step_ids=list(dict.fromkeys(option_step_ids)),
            nodes=_default_capability_nodes([s for s in spec.steps if s.step_id in set(option_step_ids)], kind="list_options"),
            input_schema={"type": "object", "properties": {"field": {"type": "string", "enum": fields}}, "required": ["field"]},
            output_schema={"type": "object", "properties": {"options": {"type": "array"}, "count": {"type": "number"}}},
            confirmed=True,
            confidence=0.95,
            evidence=[{"field": f} for f in fields],
            caller_responsibilities=["选择前调用该能力获取实时显示名候选"],
            skill_responsibilities=["调用真实选项接口或返回录制确认的页面枚举"],
        ))

    if write_steps:
        batch = any(_looks_batch_step(s) for s in write_steps)
        kind = "submit_batch" if batch else "submit"
        submit_steps = _submit_capability_steps(spec)
        submit_params = [p for s in submit_steps for p in s.params]
        input_schema = _capability_input_schema(submit_params)
        if batch:
            input_schema = dict(input_schema)
            input_schema.setdefault("properties", {})["items"] = {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
                "description": "外部前端拆分后的批量明细；运行时按已确认数组模板映射提交。",
            }
        caps.append(FlowCapability(
            name=kind,
            title="批量提交" if batch else "提交",
            intent="按已确认字段映射执行真实写入接口。",
            kind=kind,
            step_ids=[s.step_id for s in submit_steps],
            nodes=_default_capability_nodes(submit_steps, kind=kind, force_batch=batch),
            input_schema=input_schema,
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "submitted": {"type": "array"},
                    "failed": {"type": "array"},
                    "skipped": {"type": "array"},
                    "raw": {"type": "object"},
                },
            },
            preconditions=[{"check": "confirm == true", "message": "写操作必须由调用方确认后执行"}],
            confirmed=True,
            confidence=0.9 if batch else 0.95,
            evidence=[_step_evidence(s) for s in write_steps],
            caller_responsibilities=["负责最终用户对话、确认、内容拆分，并传入业务字段"],
            skill_responsibilities=["解析选项/内部 ID、构造请求、执行提交并返回结构化结果"],
        ))
    return caps


def ensure_flow_capabilities(spec: FlowSpec) -> FlowSpec:
    return _with_default_capabilities(spec)


def _title_without_step_suffix(title: str) -> str:
    text = str(title or "").strip()
    text = re.sub(r"\s*[\(（]\s*\d+\s*步\s*[\)）]\s*$", "", text)
    return text.strip()


def _json_schema_for_params(params: list[ParamField]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for p in params:
        if p.category != "user_param" or not p.exposed_to_user:
            continue
        name = (p.key or p.path or "").strip()
        if not name or name in properties:
            continue
        typ = p.type or "string"
        schema_type = {
            "number": "number",
            "boolean": "boolean",
            "array": "array",
            "object": "object",
            "enum": "string",
            "list-enum": "array",
        }.get(typ, "string")
        prop: dict[str, Any] = {
            "type": schema_type,
            "title": p.label or name,
            "x-flow-path": p.path,
            "x-source-kind": p.source_kind,
            "x-dano-business-type": _business_type_for_param(p),
            "x-dano-wire-type": p.wire_type or _infer_type_from_value(p.value) or "string",
        }
        if p.description:
            prop["description"] = p.description
        if p.type in {"enum", "list-enum"} and p.enum_options:
            labels = []
            for opt in p.enum_options:
                pair = _enum_label_value(opt)
                if pair:
                    labels.append(pair[0])
                elif isinstance(opt, str):
                    labels.append(opt)
            if labels:
                if p.type == "list-enum":
                    prop["items"] = {"type": "string", "enum": labels}
                else:
                    prop["enum"] = labels
        properties[name] = prop
        if p.required:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _capability_output_name(mapping: dict[str, Any], index: int) -> str:
    for key in ("field", "name", "output", "target", "key"):
        value = str(mapping.get(key) or "").strip()
        if value:
            return value.split(".")[-1]
    path = str(mapping.get("response_path") or mapping.get("path") or "").strip()
    if path and path not in {"response", "$", "."}:
        return path.replace("[]", "").split(".")[-1] or f"output_{index + 1}"
    return f"output_{index + 1}"


def _query_output_mappings(steps: list[FlowStep]) -> list[dict[str, Any]]:
    used: set[str] = set()
    mappings: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, 1):
        raw = step.name or (step.path or step.url).split("?", 1)[0].rsplit("/", 1)[-1] or f"query_{idx}"
        base = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower() or f"query_{idx}"
        if base.isdigit() or not re.search(r"[a-zA-Z_]", base):
            base = f"query_{idx}"
        name = base
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        response = step.response_json
        semantic_paths: list[tuple[str, str]] = []
        if isinstance(response, dict):
            for path, output_name in (
                ("data.filled_dates", "filled_dates"), ("filled_dates", "filled_dates"),
                ("data.missing_dates", "missing_dates"), ("missing_dates", "missing_dates"),
                ("data.list", "records"), ("data.records", "records"),
                ("data.rows", "records"), ("list", "records"), ("rows", "records"),
                ("data.total", "total"), ("total", "total"),
            ):
                if _flow_path_lookup(response, path) is not _FLOW_PATH_MISSING:
                    semantic_paths.append((path, output_name))
        if semantic_paths:
            for path, output_name in semantic_paths:
                mapping = {
                    "kind": "step_response",
                    "name": output_name,
                    "step_id": step.step_id,
                    "response_path": path,
                }
                # A semantic capability output has one stable public name. If
                # several query stages expose it, the later stage is the final
                # business result instead of creating missing_dates_2/_3 noise.
                previous_idx = next((
                    i for i, item in enumerate(mappings)
                    if item.get("name") == output_name
                ), -1)
                if previous_idx >= 0:
                    mappings[previous_idx] = mapping
                else:
                    mappings.append(mapping)
                used.add(output_name)
        else:
            used.add(name)
            mappings.append({
                "kind": "step_response",
                "name": name,
                "step_id": step.step_id,
                "response_path": "response",
            })
    return mappings


def _flow_capability_id(kind: str, seed: str = "") -> str:
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", f"{kind}_{seed}".strip("_")).strip("_").lower()
    return raw[:64] or kind


def _capability_step_ids(steps: list[FlowStep]) -> list[str]:
    return [s.step_id for s in steps if s.step_id]


def _capability_call_nodes(steps: list[FlowStep]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, 1):
        nodes.append({
            "id": f"call_{idx}",
            "type": "call",
            "step_id": step.step_id,
            "method": step.method,
            "path": step.path or step.url,
        })
    if steps:
        nodes.append({
            "id": "return_final",
            "type": "return",
            "from": steps[-1].step_id,
            "path": "response",
        })
    return nodes


def _write_steps(spec: FlowSpec) -> list[FlowStep]:
    return [s for s in spec.steps if (s.method or "").upper() in _WRITE_METHODS]


def _option_source_step_ids(spec: FlowSpec) -> set[str]:
    ids: set[str] = set()
    urls: set[str] = set()
    request_ids: set[str] = set()
    for step in spec.steps:
        for param in step.params:
            if param.source_kind != "api_option":
                continue
            source = param.source or {}
            if source.get("source_step_id"):
                ids.add(str(source["source_step_id"]))
            if source.get("source_url"):
                urls.add(_request_path({"url": str(source["source_url"])}))
            if source.get("source_request_id"):
                request_ids.add(str(source["source_request_id"]))
        for select in step.selects:
            if select.source_url:
                urls.add(_request_path({"url": select.source_url}))
            if select.source_request_id:
                request_ids.add(str(select.source_request_id))
    for step in spec.steps:
        if step.step_id in ids:
            continue
        if str((step.source_meta or {}).get("request_id") or "") in request_ids:
            ids.add(step.step_id)
            continue
        if _request_path({"url": step.path or step.url}) in urls:
            ids.add(step.step_id)
    return ids


def _business_query_evidence_score(step: FlowStep) -> int:
    if (step.method or "GET").upper() not in {"GET", "HEAD"}:
        return -100
    path = _request_path({"url": step.path or step.url}).lower()
    if re.search(
        r"(?:process-definition|approval-detail|form-config|permissions?|tenant|current-user|auth|dict(?:ionary)?|options?|simple-list|departments?|roles?)",
        path,
    ) or re.search(r"(?:^|/)(?:system|im)/users?(?:/|$)", path):
        return -10
    score = 0
    role = str((step.source_meta or {}).get("role") or step.semantic_role or "")
    if role == "business_get":
        score += 2
    if re.search(r"(?:^|/)(?:page|list|search|query|history|records?|status|statistics|detail)(?:/|$|\?)", path):
        score += 2
    response = step.response_json
    if isinstance(response, list):
        score += 4
    if isinstance(response, dict):
        for candidate in ("data.list", "data.records", "data.rows", "data.items", "list", "records", "rows", "items"):
            value = _flow_path_lookup(response, candidate)
            if isinstance(value, list):
                score += 4
                break
        if any(_flow_path_lookup(response, candidate) is not _FLOW_PATH_MISSING for candidate in ("data.total", "total", "count")):
            score += 1
    return score


def _is_business_query_step(step: FlowStep) -> bool:
    return _business_query_evidence_score(step) >= 3


def _read_status_steps(spec: FlowSpec) -> list[FlowStep]:
    out: list[FlowStep] = []
    for st in spec.steps:
        if (st.method or "").upper() in _WRITE_METHODS:
            continue
        if _is_business_query_step(st):
            out.append(st)
    return out


def _ordered_steps_by_ids(spec: FlowSpec, ids: set[str]) -> list[FlowStep]:
    return [st for st in spec.steps if st.step_id in ids]


def _dependency_closure_step_ids(spec: FlowSpec, target_ids: set[str]) -> set[str]:
    keep = set(target_ids)
    changed = True
    while changed:
        changed = False
        for link in spec.links or []:
            if link.target_step_id in keep and link.source_step_id and link.source_step_id not in keep:
                keep.add(link.source_step_id)
                changed = True
    return keep


def _submit_capability_steps(spec: FlowSpec) -> list[FlowStep]:
    write_ids = {st.step_id for st in _write_steps(spec) if st.step_id}
    if not write_ids:
        return []
    preflight_ids = {
        st.step_id for st in spec.steps
        if bool((st.source_meta or {}).get("control_preflight_for_write"))
    }
    return _ordered_steps_by_ids(
        spec,
        _dependency_closure_step_ids(spec, write_ids | preflight_ids),
    )


def _schema_path_exists(schema: dict[str, Any] | None, path: str, key: str = "") -> bool:
    """Check aggregate paths such as entries[].sealId against JSON Schema."""
    raw = str(path or key or "").strip()
    if not raw:
        return False
    parts = [part for part in re.split(r"\.|\[\]", raw) if part]
    node: Any = schema or {}
    for part in parts:
        if not isinstance(node, dict):
            return False
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        if part in props:
            node = props[part]
            continue
        if node.get("type") == "array" and isinstance(node.get("items"), dict):
            node = node["items"]
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            if part in props:
                node = props[part]
                continue
        return False
    return True


def _normalize_stable_workflow_constants(spec: FlowSpec) -> None:
    """Prevent stable workflow keys from becoming random runtime values.

    BPMN activity IDs, process keys, form types and similar routing constants are
    semantic identifiers. They may come from a confirmed upstream response, but
    they must never be regenerated as UUID/random values merely because an edit or
    repair classified them as runtime variables.
    """
    linked_targets = {
        (link.target_step_id, _strip_body_prefix(link.target_path))
        for link in spec.links
        if link.confirmed
    }
    for step in spec.steps:
        for param in step.params:
            if not _looks_system_const_field(param.key, param.path):
                continue
            target = (step.step_id, _strip_body_prefix(param.path))
            if target in linked_targets or param.source_kind == "previous_response":
                continue
            if param.value in (None, ""):
                continue
            if param.category == "system_const" and param.source_kind == "constant":
                # Keep an explicitly exposed constant visible to validation; do
                # not silently hide a real configuration error.
                continue
            if param.source_kind in _OPTION_SOURCE_KINDS or param.source_kind == "user_input":
                # `form.type` and similar business enums can contain the token
                # "formtype" after normalization; select evidence is stronger
                # than the identifier-name heuristic.
                continue
            previous = {"category": param.category, "source_kind": param.source_kind, "source": dict(param.source or {})}
            param.category = "system_const"
            param.source_kind = "constant"
            param.source = {"kind": "constant", "path": param.path, "semantic": "workflow_identifier"}
            param.exposed_to_user = False
            param.editable = True
            param.need_human_confirm = False
            param.reason = "流程定义、节点或表单标识使用录制确认的稳定值，不得在运行期随机生成"
            param.evidence.append({
                "kind": "stable_workflow_constant_repair",
                "value": param.value,
                "previous": previous,
            })


def _repair_versioned_workflow_id_links(spec: FlowSpec) -> int:
    """Restore exact process-definition response links removed by a bad edit.

    Versioned processDefinitionId values must be queried at runtime. Unlike
    processDefKey/activityId/billType, freezing them makes a Skill expire after a
    workflow deployment. The repair is intentionally narrow: one earlier process
    definition endpoint must return the exact recorded ID at an ``*.id`` path.
    """
    repaired = 0
    matched_any = False
    for target_index, target in enumerate(spec.steps):
        for param in target.params:
            leaf = re.sub(r"[^a-z0-9]+", "", str(param.path or param.key or "").split(".")[-1].lower())
            if leaf not in {"processdefinitionid", "processdefid"} or param.value in (None, ""):
                continue
            matches: list[tuple[FlowStep, str]] = []
            for source in spec.steps[:target_index]:
                source_path_text = str(source.path or source.url or "").lower()
                if "process-definition" not in source_path_text and "processdefinition" not in source_path_text:
                    continue
                for response_path, _tokens, leaf_value, _raw in _leaf_paths(source.response_json):
                    if response_path.split(".")[-1].lower() == "id" and str(leaf_value) == str(param.value):
                        matches.append((source, response_path))
            if len(matches) != 1:
                continue
            source, response_path = matches[0]
            matched_any = True
            incoming = [
                link for link in spec.links
                if link.target_step_id == target.step_id
                and _strip_body_prefix(link.target_path) == _strip_body_prefix(param.path)
            ]
            exact = next((
                link for link in incoming
                if link.source_step_id == source.step_id and link.source_path == response_path
            ), None)
            if exact is None:
                spec.links = [link for link in spec.links if link not in incoming]
                exact = FlowLink(
                    source_step_id=source.step_id,
                    source_path=response_path,
                    target_step_id=target.step_id,
                    target_path=param.path,
                    param_name=param.key,
                )
                spec.links.append(exact)
                repaired += 1
            exact.confirmed = True
            exact.confidence = 1.0
            exact.locked = True
            exact.reason = "流程定义查询返回的版本 ID 与提交字段录制值精确一致，运行期必须重新查询"
            exact.evidence = {
                "kind": "workflow_definition_exact_id",
                "source_endpoint": source.path or source.url,
                "recorded_value_match": True,
            }
            param.locked = False
    if matched_any:
        _apply_link_sources(spec.steps, spec.links)
    return repaired


def _capability_step_allowed(spec: FlowSpec, cap: FlowCapability, step: FlowStep) -> bool:
    role = (step.source_meta or {}).get("role") or step.semantic_role or ""
    kind = (cap.kind or "").strip()
    membership = next((ref for ref in (cap.request_refs or []) if ref.step_id == step.step_id), None)
    # Explicit user membership is authoritative. Request role describes evidence,
    # not whether the same request may execute inside a capability.
    if membership and membership.pinned and membership.usage in {"execute", "preflight", "fact_check"}:
        return True
    if step.step_id in set(cap.step_ids or []) and (
        cap.updated_by == "user" or cap.locked or cap.confirmed or not role
    ):
        return True
    if kind == "query_status" and _is_business_query_step(step):
        return True
    if kind == "query_status" and (
        role == "read_option" or step.step_id in _option_source_step_ids(spec)
    ):
        return False
    method = (step.method or "GET").upper()
    if method in _WRITE_METHODS:
        return True
    if kind in {"submit", "submit_batch", "validate_batch"}:
        closure_ids = {st.step_id for st in _submit_capability_steps(spec)}
        return step.step_id in closure_ids
    if kind == "query_status":
        status_ids = {st.step_id for st in _read_status_steps(spec)}
        return role != "read_option" and step.step_id in status_ids
    if kind == "list_options":
        return role == "read_option" or bool(step.selects)
    return role not in {"read_option", "read_context"}


def _add_step_id_to_capability(spec: FlowSpec, cap: FlowCapability, step_id: str) -> None:
    if not step_id or step_id in cap.step_ids:
        return
    order = {s.step_id: i for i, s in enumerate(spec.steps)}
    new_order = order.get(step_id, 10_000)
    for idx, sid in enumerate(cap.step_ids or []):
        if order.get(sid, 10_000) > new_order:
            cap.step_ids.insert(idx, step_id)
            return
    cap.step_ids.append(step_id)


def _set_capability_request_membership(
    spec: FlowSpec,
    cap: FlowCapability,
    step: FlowStep,
    *,
    usage: str,
    origin: str,
    pinned: bool,
) -> CapabilityRequestRef:
    current = next((ref for ref in (cap.request_refs or []) if ref.step_id == step.step_id), None)
    ref = _capability_request_ref_from_step(spec, step, current)
    ref.usage = usage if usage in {"execute", "option_source", "fact_check", "preflight"} else "execute"
    ref.origin = origin or "manual"
    ref.pinned = bool(pinned)
    ref.confirmed = bool(pinned)
    cap.request_refs = [item for item in (cap.request_refs or []) if item.step_id != step.step_id]
    cap.request_refs.append(ref)
    return ref


def _request_graph_entries(spec: FlowSpec, roles: set[str]) -> list[dict[str, Any]]:
    graph = _request_graph_for_spec(spec)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, Any]] = set()
    for bucket in ("selected_steps", "candidate_reads", "all_requests"):
        for entry in graph.get(bucket) or []:
            if (entry.get("role") or "") not in roles:
                continue
            sig = (entry.get("method") or "", entry.get("path") or entry.get("url") or "", entry.get("request_index"))
            if sig in seen:
                continue
            seen.add(sig)
            out.append({**entry, "bucket": bucket})
    return out


def _option_field_names(spec: FlowSpec) -> list[str]:
    names: list[str] = []
    for st in spec.steps:
        for p in st.params:
            if p.type not in {"enum", "list-enum"} and p.source_kind not in _OPTION_SOURCE_KINDS:
                continue
            name = (p.key or p.label or p.path or "").strip()
            if name and name not in names:
                names.append(name)
        for sel in st.selects:
            name = (sel.param or sel.path or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def build_default_flow_capabilities(spec: FlowSpec) -> list[FlowCapability]:
    """从 FlowSpec 的 steps/request_graph 生成默认业务能力编排。

    这些能力只是对外调用层的候选描述，不改变真实执行计划；发布执行仍以 steps/links 为准。
    """
    caps: list[FlowCapability] = []
    status_steps = _read_status_steps(spec)
    write_steps = _write_steps(spec)
    submit_steps = _submit_capability_steps(spec) if write_steps else []
    submit_step_ids = {s.step_id for s in submit_steps}
    independent_status_steps = [s for s in status_steps if s.step_id not in submit_step_ids]
    if independent_status_steps:
        query_confidence = min(
            float((step.source_meta or {}).get("confidence") or 0.68)
            for step in independent_status_steps
        )
        caps.append(FlowCapability(
            name="query_status",
            title="查询流程状态",
            intent="查询流程、审批或上下文详情，用于判断业务当前状态，并把结果返回给调用方决定下一步。",
            kind="query_status",
            step_ids=_capability_step_ids(independent_status_steps),
            nodes=_default_capability_nodes(independent_status_steps, kind="query_status"),
            input_schema=_json_schema_for_params([p for st in independent_status_steps for p in st.params]),
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "detail": {"type": "object"},
                    "raw": {"type": "object"},
                },
            },
            output_mapping=_query_output_mappings(independent_status_steps),
            confirmed=False,
            confidence=query_confidence,
            requires_human_confirm=True,
            status="draft",
            evidence=[
                {"kind": "read_step", "step_id": s.step_id, "method": s.method, "path": s.path or s.url}
                for s in independent_status_steps
            ],
            caller_responsibilities=["根据查询结果与最终用户确认是否继续提交或批量填报"],
            skill_responsibilities=["执行真实查询接口并返回原始响应/结构化摘要"],
        ))

    if write_steps:
        inferred_repeated_submit = _query_implies_repeated_submit(spec, write_steps)
        kind = "submit_batch" if _write_contract_is_batch(spec, write_steps) else "submit"
        submit_input_schema = (
            _batch_capability_input_schema(submit_steps)
            if kind == "submit_batch"
            else _json_schema_for_params([p for st in submit_steps for p in st.params])
        )
        caps.append(FlowCapability(
            name=kind,
            title="批量提交业务申请" if kind == "submit_batch" else "提交业务申请",
            intent="调用方提供业务字段；Skill 按已纳入接口顺序执行前置查询、依赖注入和最终提交，并返回最后写接口结果。",
            kind=kind,
            step_ids=_capability_step_ids(submit_steps),
            nodes=_default_capability_nodes(submit_steps, kind=kind, force_batch=inferred_repeated_submit),
            input_schema=submit_input_schema,
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "results": {"type": "array", "items": {"type": "object"}},
                },
            },
            output_mapping=([{
                "kind": "batch_result",
                "name": name,
                "response_path": name,
            } for name in ("total", "success_count", "failed_count", "results", "failed_items")]
            if kind == "submit_batch" else [{
                "kind": "final_response",
                "name": "result",
                "step_id": write_steps[-1].step_id,
                "response_path": "response",
            }]),
            confirmed=False,
            confidence=0.9 if kind == "submit_batch" else 0.95,
            requires_human_confirm=True,
            status="draft",
            evidence=[{
                "kind": "write_steps",
                "step_ids": _capability_step_ids(write_steps),
                "paths": [s.path or s.url for s in write_steps],
                "batch_intent": inferred_repeated_submit,
                "repeated_submission": inferred_repeated_submit,
            }],
            caller_responsibilities=["提供 input_schema 中的业务字段", "确认写操作后调用"],
            skill_responsibilities=["按 FlowStep 顺序执行请求", "注入 links/system_values/runtime_var", "返回每条提交的成功状态"],
        ))
        return caps

    status_graph = _request_graph_entries(spec, {"business_get", "read_context"})
    if status_steps or status_graph:
        status_params = [p for st in status_steps for p in st.params]
        caps.append(FlowCapability(
            name="query_status",
            title="查询流程状态",
            intent="查询流程、审批或上下文详情，用于判断业务当前状态。",
            kind="query_status",
            step_ids=_capability_step_ids(status_steps),
            nodes=_default_capability_nodes(status_steps, kind="query_status"),
            input_schema=_json_schema_for_params(status_params),
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "detail": {"type": "object"},
                    "raw": {"type": "object"},
                },
            },
            output_mapping=_query_output_mappings(status_steps),
            confirmed=False,
            confidence=0.58 if status_steps else 0.42,
            requires_human_confirm=True,
            status="draft",
            evidence=[
                *[
                    {"kind": "read_step", "step_id": s.step_id, "method": s.method, "path": s.path or s.url}
                    for s in status_steps
                ],
                *[
                    {
                        "kind": "request_graph",
                        "request_index": e.get("request_index"),
                        "method": e.get("method"),
                        "path": e.get("path") or e.get("url"),
                        "role": e.get("role"),
                        "bucket": e.get("bucket"),
                    }
                    for e in status_graph
                ],
            ],
            caller_responsibilities=["提供查询所需的业务标识或筛选条件"],
            skill_responsibilities=["执行状态/详情查询接口", "把原始响应整理成状态摘要"],
        ))

    return caps


_FIELD_MAPPED_CAPABILITY_RELATIONS = {"external_transform", "data_mapping", "field_mapping"}


def _capability_relation_requires_fields(relation: CapabilityRelation) -> bool:
    relation_kind = str(relation.mode or relation.type or "").strip().lower()
    return relation_kind in _FIELD_MAPPED_CAPABILITY_RELATIONS


def _normalize_capability_relation_semantics(relation: CapabilityRelation) -> CapabilityRelation:
    """Resolve legacy type/mode defaults from the actual relation contract."""
    has_from = bool(str(relation.from_output or "").strip())
    has_to = bool(str(relation.to_input or "").strip())
    if not has_from and not has_to:
        relation.type = "caller_decision"
        relation.mode = "caller_decision"
        relation.transform_owner = "caller"
        relation.required = False
        relation.requires_user_confirmation = True
        relation.input_schema = {}
        relation.output_schema = {}
        relation.source_selector = ""
        relation.target_path = ""
    return relation


def _ensure_external_transform_relations(spec: FlowSpec) -> FlowSpec:
    """Describe grounded caller-owned capability cooperation without auto-running it."""
    spec.capability_relations = [
        _normalize_capability_relation_semantics(relation)
        for relation in (spec.capability_relations or [])
    ]
    capability_by_ref = {
        ref: cap
        for cap in spec.capabilities
        for ref in (cap.name, cap.capability_id)
        if ref
    }
    def relation_is_valid(relation: CapabilityRelation) -> bool:
        source = capability_by_ref.get(relation.from_capability)
        target = capability_by_ref.get(relation.to_capability)
        relation_kind = str(relation.mode or relation.type or "").strip().lower()
        if (
            relation_kind == "external_transform"
            and source is not None
            and target is not None
            and source.kind == "query_status"
            and target.kind == "submit"
            and relation.to_input == "entries"
        ):
            return False
        if (relation.evidence or {}).get("kind") != "typed_capability_contract":
            return True
        if source is None or target is None:
            return False
        if not _capability_relation_requires_fields(relation):
            return True
        return bool(
            relation.from_output
            and relation.to_input
            and relation.from_output in ((source.output_schema or {}).get("properties") or {})
            and relation.to_input in ((target.input_schema or {}).get("properties") or {})
        )

    spec.capability_relations = [
        relation for relation in spec.capability_relations if relation_is_valid(relation)
    ]
    queries = [cap for cap in spec.capabilities if cap.kind == "query_status"]
    batches = [cap for cap in spec.capabilities if cap.kind == "submit_batch"]
    for query in queries:
        output_props = dict((query.output_schema or {}).get("properties") or {})
        if "missing_dates" not in output_props:
            continue
        for batch in batches:
            input_props = dict((batch.input_schema or {}).get("properties") or {})
            entries_schema = input_props.get("entries")
            if not isinstance(entries_schema, dict) or entries_schema.get("type") != "array":
                continue
            if any(
                relation.from_capability in {query.name, query.capability_id}
                and relation.from_output == "missing_dates"
                and relation.to_capability in {batch.name, batch.capability_id}
                and relation.to_input == "entries"
                for relation in spec.capability_relations
            ):
                continue
            spec.capability_relations.append(CapabilityRelation(
                type="external_transform",
                mode="external_transform",
                transform_owner="caller",
                from_capability=query.name or query.capability_id,
                from_output="missing_dates",
                to_capability=batch.name or batch.capability_id,
                to_input="entries",
                source_selector="missing_dates",
                target_path="entries[].date",
                cardinality="many_to_many",
                required=False,
                requires_user_confirmation=True,
                confirmed=True,
                confidence=0.98,
                reason="调用方读取未填写日期、向用户确认并生成 entries 后，再显式调用批量提交能力",
                input_schema=output_props.get("missing_dates") or {},
                output_schema=entries_schema,
                evidence={"kind": "typed_capability_contract", "automatic_execution": False},
            ))

    # A single query + single ordinary submit is a caller-controlled conversation
    # boundary, not an implicit data mapping.  This captures workflows such as
    # "query existing records, ask the user, then explicitly submit".
    submits = [cap for cap in spec.capabilities if cap.kind == "submit"]
    if len(queries) == 1 and len(submits) == 1:
        query, submit = queries[0], submits[0]
        query_ref = query.name or query.capability_id
        submit_ref = submit.name or submit.capability_id
        already_related = any(
            relation.from_capability in {query.name, query.capability_id}
            and relation.to_capability in {submit.name, submit.capability_id}
            for relation in spec.capability_relations
        )
        if query_ref and submit_ref and not already_related:
            spec.capability_relations.append(CapabilityRelation(
                type="caller_decision",
                mode="caller_decision",
                transform_owner="caller",
                from_capability=query_ref,
                to_capability=submit_ref,
                cardinality="one_to_one",
                required=False,
                requires_user_confirmation=True,
                confirmed=True,
                confidence=0.9,
                reason="调用方先读取查询结果，再结合用户意图决定是否显式调用提交能力",
                evidence={"kind": "typed_capability_contract", "automatic_execution": False},
            ))
    deduped_relations: list[CapabilityRelation] = []
    seen_relations: set[tuple[str, str, str, str, str]] = set()
    for relation in spec.capability_relations:
        identity = (
            relation.from_capability, relation.from_output,
            relation.to_capability, relation.to_input,
            str(relation.mode or relation.type or ""),
        )
        if identity in seen_relations:
            continue
        seen_relations.add(identity)
        deduped_relations.append(relation)
    spec.capability_relations = deduped_relations
    return spec


def suggest_flow_capabilities(spec: FlowSpec) -> list[FlowCapability]:
    """兼容旧调用方，统一走当前能力构建规则，避免两套 Planner 语义漂移。"""
    return build_default_flow_capabilities(spec)


def _with_default_capabilities(spec: FlowSpec) -> FlowSpec:
    if spec.capabilities:
        return sync_flow_spec_models(spec, prefer_request_facts=False)
    spec.capabilities = build_default_flow_capabilities(spec)
    if spec.capabilities:
        spec.meta = {
            **(spec.meta or {}),
            "capability_model": {
                "status": "draft",
                "source": "request_graph+steps",
                "generated_count": len(spec.capabilities),
            },
        }
    return sync_flow_spec_models(spec, prefer_request_facts=False)


_FLOW_ORCHESTRATE_SYSTEM = """你是企业 OA/API 录制结果的 Skill 编排器。
只输出 JSON，不要输出解释。
目标：根据真实捕获请求和当前能力编排，输出能力级增量 patch ops。
要求：
- 优先输出 {"ops":[...]}，不要整份覆盖 capabilities。
- 每个 op 必须指向已有 capability/step/request/path，不能编造接口。
- 如果已有能力编排，请在已有能力基础上补充/优化，不要重新设计一套无关能力。
- 如果上下文包含 removed_capabilities 或 removed_capability_steps，必须尊重用户删除记录，不要自动恢复。
- 如果流程包含独立查询阶段和写入阶段，可以拆成 query_status/validate_batch + submit 或 submit_batch 多个能力；真正只服务于写入的前置 GET 才放进写能力步骤链。
- 不要把纯选项/字典接口单独拆成能力，它们只属于字段候选来源或业务能力的内部步骤。
- 不能创建没有真实 call 接口的能力。
- 当前上下文如果标记 scope_locked=true，禁止新增/删除能力或接口，只能修字段、依赖、节点语义和返回映射。
- 读能力只查询并返回结果；写能力可以包含必要前置查询 + 写入步骤。
- 批量填报/日报/明细数组场景优先生成 submit_batch。
- 批量场景必须用 foreach 节点表达循环，items 推荐 input.entries；foreach.steps 内放每条明细要执行的 call。
- 条件分支必须用 condition 节点表达，condition/check 只能引用 input.*、var.*、已执行 step_id 响应或 node.*。
- 字段转换/响应取值必须用 map 节点表达 source/target，不要靠文字说明隐藏。
- output_mapping 默认指向最后一个步骤 response。
允许 ops：
- {"op":"upsert_capability","capability":{"name":"...","title":"...","kind":"query_status|validate_batch|submit_batch|submit","intent":"..."}}
- 首次编排且 scope_locked=false 时可用 {"op":"add_request_to_capability","capability":"...","step_id":"..."}，step_id 必须已经存在于当前 FlowSpec；禁止通过 request_id/request_index 偷偷扩张接口范围。
- {"op":"upsert_input_field","capability":"...","field":{"key":"entries","type":"array","required":true}}
- {"op":"upsert_request_field","capability":"...","field":{"step_id":"...","path":"[0].date","key":"date","type":"date","source_kind":"loop_item"}}
- {"op":"bind_dependency","capability":"...","source":{"step_id":"...","path":"data.id"},"target":{"step_id":"...","path":"body.id"},"confidence":0.96}
- {"op":"set_loop_source","capability":"...","items":"input.entries"}
- {"op":"set_map","capability":"...","node":{"id":"map_date","source":"item.date","target":"submit.[0].date"}}
- {"op":"set_condition","capability":"...","node":{"id":"has_entries","condition":"input.entries.length > 0","then":[]}}
- {"op":"set_output_mapping","capability":"...","mapping":[{"kind":"final_response","step_id":"...","response_path":"response"}]}
- {"op":"set_capability_relation","from_capability":"query_status","from_output":"missing_dates","to_capability":"submit_batch","to_input":"entries","confidence":0.8}
兼容旧格式：如果你只能输出 abilities，也必须保证不覆盖已确认内容。
JSON 形态优先：
{"ops":[...]}
"""


def _capability_from_llm(raw: dict[str, Any], step_ids: set[str], used_names: set[str]) -> FlowCapability | None:
    if not isinstance(raw, dict):
        return None
    allowed_kinds = {"query_status", "validate_batch", "submit_batch", "submit"}
    kind = str(raw.get("kind") or "submit").strip()
    if kind not in allowed_kinds:
        return None
    raw_name = str(raw.get("name") or kind).strip()
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", raw_name).strip("_").lower() or kind
    if name in used_names:
        seed = 2
        base = name
        while f"{base}_{seed}" in used_names:
            seed += 1
        name = f"{base}_{seed}"
    selected_steps = [str(x) for x in (raw.get("step_ids") or []) if str(x) in step_ids]
    raw_nodes = raw.get("nodes") if isinstance(raw.get("nodes"), list) else []
    nodes: list[dict[str, Any]] = []
    node_step_ids: list[str] = []
    for node in raw_nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "").strip()
        if node_type not in {"call", "map", "filter", "condition", "foreach", "select", "return"}:
            continue
        copied = dict(node)
        sid = str(copied.get("step_id") or "")
        if sid:
            if sid not in step_ids:
                continue
            node_step_ids.append(sid)
        copied.setdefault("id", f"{node_type}_{len(nodes) + 1}")
        nodes.append(copied)
    for sid in node_step_ids:
        if sid not in selected_steps:
            selected_steps.append(sid)
    if not selected_steps:
        return None
    if not nodes:
        nodes = [{"id": f"call_{i + 1}", "type": "call", "step_id": sid} for i, sid in enumerate(selected_steps)]
        if selected_steps:
            nodes.append({"id": "return_final", "type": "return", "from": selected_steps[-1], "path": "response"})
    used_names.add(name)
    return FlowCapability(
        name=name,
        title=str(raw.get("title") or name),
        intent=str(raw.get("intent") or raw.get("description") or ""),
        kind=kind,
        step_ids=selected_steps,
        nodes=nodes,
        input_schema=raw.get("input_schema") if isinstance(raw.get("input_schema"), dict) else {},
        output_schema=raw.get("output_schema") if isinstance(raw.get("output_schema"), dict) else {},
        output_mapping=raw.get("output_mapping") if isinstance(raw.get("output_mapping"), list) else [],
        preconditions=raw.get("preconditions") if isinstance(raw.get("preconditions"), list) else [],
        confirmed=False,
        confidence=max(0.0, min(1.0, float(raw.get("confidence") or 0.75))),
        requires_human_confirm=bool(raw.get("requires_human_confirm", True)),
        evidence=raw.get("evidence") if isinstance(raw.get("evidence"), list) else [],
        caller_responsibilities=raw.get("caller_responsibilities") if isinstance(raw.get("caller_responsibilities"), list) else [],
        skill_responsibilities=raw.get("skill_responsibilities") if isinstance(raw.get("skill_responsibilities"), list) else [],
    )


def _orchestration_context(spec: FlowSpec) -> dict[str, Any]:
    graph = _request_graph_for_spec(spec)
    validation_findings: dict[str, Any] = {}
    try:
        validation = validate_flow_spec(spec)
        cap_validation = validation.get("capability_validation") or {}
        validation_findings = {
            "errors": list(validation.get("errors") or [])[:40],
            "warnings": list(validation.get("warnings") or [])[:40],
            "unused_high_confidence_requests": list(cap_validation.get("unused_high_confidence_requests") or [])[:80],
            "capability_internal": cap_validation.get("capability_internal") or {},
            "capability_relations": cap_validation.get("capability_relations") or {},
            "skill_level": cap_validation.get("skill_level") or {},
        }
    except Exception as exc:  # noqa: BLE001
        validation_findings = {"error": str(exc)[:240]}
    return {
        "title": spec.title,
        "business_description": spec.business_description,
        "validation_findings": validation_findings,
        "removed_capabilities": list((spec.meta or {}).get("removed_capabilities") or []),
        "removed_capability_steps": dict((spec.meta or {}).get("capability_removed_steps") or {}),
        "existing_capabilities": [
            {
                "name": cap.name,
                "title": cap.title,
                "intent": cap.intent,
                "kind": cap.kind,
                "step_ids": list(cap.step_ids or []),
                "nodes": list(cap.nodes or []),
                "input_schema": cap.input_schema or {},
                "output_schema": cap.output_schema or {},
                "output_mapping": list(cap.output_mapping or []),
                "fields": [
                    _capability_field_summary(field)
                    for field in [
                        *(cap.fields or []),
                        *(cap.inputs or []),
                        *(cap.request_fields or []),
                        *(cap.internal_fields or []),
                        *(cap.computed_fields or []),
                        *(cap.outputs or []),
                    ]
                ][:80],
                "dependencies": [dep.model_dump(exclude_none=True) for dep in (cap.dependencies or [])[:80]],
                "confirmed": cap.confirmed,
                "requires_human_confirm": cap.requires_human_confirm,
            }
            for cap in spec.capabilities
        ],
        # Complete compact indexes guarantee that every recorded field and
        # response path participates in planning. Detailed samples below remain
        # bounded so a single huge response cannot exhaust the model context.
        "complete_field_index": {
            st.step_id: [
                {
                    "path": p.path,
                    "key": p.key,
                    "type": p.type,
                    "category": p.category,
                    "source_kind": p.source_kind,
                    "required": bool(p.required),
                }
                for p in (st.params or [])
            ]
            for st in spec.steps
        },
        "complete_response_path_index": {
            st.step_id: [str(item[0]) for item in _leaf_paths(st.response_json)]
            if st.response_json is not None else []
            for st in spec.steps
        },
        "steps": [
            {
                "step_id": st.step_id,
                "name": st.name,
                "method": st.method,
                "path": st.path or st.url,
                "role": (st.source_meta or {}).get("role") or st.semantic_role,
                "param_count": len(st.params or []),
                "params": [
                    {
                        "path": p.path,
                        "key": p.key,
                        "type": p.type,
                        "category": p.category,
                        "source_kind": p.source_kind,
                    }
                    for p in (st.params or [])[:80]
                ],
                "response_paths": _leaf_paths(st.response_json)[:80] if st.response_json is not None else [],
            }
            for st in spec.steps
        ],
        "links": [lk.model_dump() for lk in spec.links],
        "captured_requests": [
            {
                "request_index": r.get("request_index"),
                "method": r.get("method"),
                "path": r.get("path") or r.get("url"),
                "role": r.get("role"),
                "confidence": r.get("confidence"),
                "reason": r.get("reason"),
            }
            for r in (graph.get("all_requests") or [])
        ],
    }


def _capability_step_ref_keys(spec: FlowSpec | None, step_id: str) -> set[str]:
    refs = {f"step:{step_id}"}
    if spec is not None:
        step = next((s for s in spec.steps if s.step_id == step_id), None)
        if step is not None:
            refs.add(f"sig:{_step_request_signature_key(step)}")
    return refs


def _capability_removed_step_refs(spec: FlowSpec | None, cap_name: str) -> set[str]:
    if spec is None:
        return set()
    removed = ((spec.meta or {}).get("capability_removed_steps") or {}).get(cap_name) or []
    return {str(x) for x in removed if str(x)}


def _removed_capability_names(spec: FlowSpec | None) -> set[str]:
    if spec is None:
        return set()
    return {str(x) for x in ((spec.meta or {}).get("removed_capabilities") or []) if str(x)}


def _remember_removed_capability(spec: FlowSpec, cap_name: str, cap_kind: str = "") -> None:
    if not cap_name:
        return
    meta = dict(spec.meta or {})
    removed = set(str(x) for x in (meta.get("removed_capabilities") or []))
    removed.add(cap_name)
    meta["removed_capabilities"] = sorted(removed)
    if cap_kind:
        removed_kinds = set(str(x) for x in (meta.get("removed_capability_kinds") or []))
        removed_kinds.add(_capability_kind_family(cap_kind))
        meta["removed_capability_kinds"] = sorted(removed_kinds)
    spec.meta = meta


def _forget_removed_capability(spec: FlowSpec, cap_name: str, cap_kind: str = "") -> None:
    meta = dict(spec.meta or {})
    removed = [x for x in (meta.get("removed_capabilities") or []) if str(x) != cap_name]
    meta["removed_capabilities"] = removed
    if cap_kind:
        family = _capability_kind_family(cap_kind)
        meta["removed_capability_kinds"] = [
            x for x in (meta.get("removed_capability_kinds") or []) if str(x) != family
        ]
    spec.meta = meta


def _capability_step_was_removed(spec: FlowSpec | None, cap_name: str, step_id: str) -> bool:
    removed = _capability_removed_step_refs(spec, cap_name)
    if not removed:
        return False
    return bool(_capability_step_ref_keys(spec, step_id) & removed)


def _remember_removed_capability_step(spec: FlowSpec, cap_name: str, step_id: str) -> None:
    refs = sorted(_capability_step_ref_keys(spec, step_id))
    if not refs:
        return
    meta = dict(spec.meta or {})
    removed = {k: list(v or []) for k, v in (meta.get("capability_removed_steps") or {}).items()}
    cur = set(str(x) for x in removed.get(cap_name, []))
    cur.update(refs)
    removed[cap_name] = sorted(cur)
    meta["capability_removed_steps"] = removed
    spec.meta = meta


def _forget_removed_capability_step(spec: FlowSpec, cap_name: str, step_id: str) -> None:
    meta = dict(spec.meta or {})
    removed = {k: list(v or []) for k, v in (meta.get("capability_removed_steps") or {}).items()}
    if cap_name not in removed:
        return
    refs = _capability_step_ref_keys(spec, step_id)
    removed[cap_name] = [x for x in removed[cap_name] if x not in refs]
    if not removed[cap_name]:
        removed.pop(cap_name, None)
    meta["capability_removed_steps"] = removed
    spec.meta = meta


def _capability_kind_family(kind: str) -> str:
    return "write" if kind in {"submit", "submit_batch"} else str(kind or "")


def _merge_capability_lists(
    existing: list[FlowCapability],
    generated: list[FlowCapability],
    *,
    spec: FlowSpec | None = None,
    allow_new: bool = True,
) -> list[FlowCapability]:
    """把新生成能力合并到已有能力上，避免每次“生成编排”覆盖人工编辑。"""
    removed_capabilities = _removed_capability_names(spec)
    removed_families = {
        _capability_kind_family(name)
        for name in removed_capabilities
        if name in {"submit", "submit_batch", "query_status", "list_options", "validate_batch"}
    } | {
        str(x) for x in (((spec.meta or {}).get("removed_capability_kinds") or []) if spec is not None else [])
    }
    if not existing:
        return [
            cap for cap in generated
            if cap.name not in removed_capabilities
            and _capability_kind_family(cap.kind) not in removed_families
        ]
    out = [cap.model_copy(deep=True) for cap in existing]
    by_name = {cap.name: cap for cap in out if cap.name}
    for cap in generated:
        if cap.name in removed_capabilities or _capability_kind_family(cap.kind) in removed_families:
            continue
        cur = by_name.get(cap.name)
        if cur is None:
            empty_same_family = [
                item for item in out
                if not _capability_node_step_ids(item)
                and _capability_kind_family(item.kind) == _capability_kind_family(cap.kind)
            ]
            if len(empty_same_family) == 1:
                cur = empty_same_family[0]
        if cur is None:
            if not allow_new:
                continue
            out.append(cap)
            if cap.name:
                by_name[cap.name] = cap
            continue
        for sid in cap.step_ids:
            if _capability_step_was_removed(spec, cur.name, sid):
                continue
            if sid not in cur.step_ids:
                cur.step_ids.append(sid)
        existing_node_keys = {
            (n.get("type"), n.get("step_id"), n.get("id"))
            for n in (cur.nodes or [])
            if isinstance(n, dict)
        }
        for node in cap.nodes or []:
            if not isinstance(node, dict):
                continue
            sid = str(node.get("step_id") or "")
            if sid and _capability_step_was_removed(spec, cur.name, sid):
                continue
            key = (node.get("type"), node.get("step_id"), node.get("id"))
            if key not in existing_node_keys:
                cur.nodes.append(dict(node))
                existing_node_keys.add(key)
        if not cur.input_schema:
            cur.input_schema = cap.input_schema
        if not cur.output_schema:
            cur.output_schema = cap.output_schema
        if not cur.output_mapping:
            cur.output_mapping = cap.output_mapping
        if not cur.preconditions:
            cur.preconditions = cap.preconditions
        if not cur.evidence:
            cur.evidence = cap.evidence
        if not cur.caller_responsibilities:
            cur.caller_responsibilities = cap.caller_responsibilities
        if not cur.skill_responsibilities:
            cur.skill_responsibilities = cap.skill_responsibilities
        cur.confidence = max(float(cur.confidence or 0), float(cap.confidence or 0))
        if not cur.status or cur.status == "draft":
            cur.status = cap.status or "draft"
    return out


def _active_capability_step_ids(spec: FlowSpec) -> set[str] | None:
    """返回当前对外能力实际使用的步骤。

    ``None`` 表示能力模型尚未建立，兼容旧 FlowSpec，仍按全部步骤处理；
    空集合表示能力模型已建立但当前没有能力（例如用户删除了全部能力），
    此时不能继续让已删除能力的字段、依赖和告警参与发布。
    """
    capability_model = (spec.meta or {}).get("capability_model") or {}
    if not spec.capabilities and not capability_model.get("status"):
        return None
    active: set[str] = set()
    for cap in spec.capabilities or []:
        active.update(_capability_node_step_ids(cap))
    return active


def _normalize_capability_references(spec: FlowSpec) -> FlowSpec:
    """清理能力里指向不存在步骤的历史脏引用。

    能力只能引用已经物化为 FlowStep 的 step_id。捕获请求需要先通过
    add_capability_step/promote_request_to_step 转成步骤，不能把 request_id/hash
    直接塞进 capability.step_ids 或 call node。
    """
    step_ids = {s.step_id for s in spec.steps}

    def valid_step_id(value: Any) -> str:
        sid = str(value or "")
        return sid if sid in step_ids else ""

    def clean_nodes(nodes: list[dict[str, Any]], fallback_step_ids: list[str]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        node_ids: set[str] = set()
        local_call_step_ids: list[str] = []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            node_type = str(node.get("type") or "")
            copied = dict(node)
            if node_type == "call":
                sid = valid_step_id(copied.get("step_id"))
                if not sid:
                    continue
                copied["step_id"] = sid
                if sid not in local_call_step_ids:
                    local_call_step_ids.append(sid)
            elif node_type in {"foreach", "condition", "filter", "select", "map"}:
                for child_key in ("children", "steps", "then", "else", "otherwise"):
                    if isinstance(copied.get(child_key), list):
                        copied[child_key] = clean_nodes(copied[child_key], fallback_step_ids + local_call_step_ids)
            elif node_type == "return":
                ref = str(copied.get("from") or copied.get("source") or "")
                fallback = (fallback_step_ids + local_call_step_ids)
                if not (copied.get("value") or copied.get("from") or copied.get("source") or copied.get("path")):
                    if fallback:
                        copied["from"] = fallback[-1]
                        copied.setdefault("path", "response")
                    else:
                        continue
                if ref and ref not in step_ids and ref not in node_ids:
                    if fallback:
                        copied["from"] = fallback[-1]
                        copied.setdefault("path", "response")
                    else:
                        continue
            if not copied.get("id"):
                copied["id"] = f"{node_type or 'node'}_{len(cleaned) + 1}"
            cleaned.append(copied)
            node_ids.add(str(copied.get("id") or ""))
        return cleaned

    for cap in spec.capabilities or []:
        seen: set[str] = set()
        cap.step_ids = [
            sid
            for sid in (valid_step_id(x) for x in (cap.step_ids or []))
            if sid and not (sid in seen or seen.add(sid))
        ]
        cap.nodes = clean_nodes(cap.nodes or [], cap.step_ids)
        node_step_ids = _capability_call_step_ids_from_nodes(cap.nodes or [])
        # nodes 是实际执行计划；所有 call 必须在 step_ids 中可见、可编辑。保留 node
        # 执行顺序，再追加尚未进入 nodes 的显式 step_ids。
        cap.step_ids = list(dict.fromkeys([*node_step_ids, *cap.step_ids]))
        if cap.step_ids:
            _sync_capability_order(spec, cap)
    return spec


def _remove_capability_step_nodes(nodes: list[dict[str, Any]], step_id: str) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        if node.get("type") == "call" and str(node.get("step_id") or "") == step_id:
            continue
        copied = dict(node)
        for child_key in ("children", "steps", "then", "else", "otherwise"):
            if isinstance(copied.get(child_key), list):
                copied[child_key] = _remove_capability_step_nodes(copied[child_key], step_id)
        cleaned.append(copied)
    return cleaned


def _sync_capability_order(spec: FlowSpec, cap: FlowCapability) -> None:
    seen: set[str] = set()
    cap.step_ids = [sid for sid in cap.step_ids if not (sid in seen or seen.add(sid))]
    order = {sid: idx for idx, sid in enumerate(cap.step_ids)}

    def reorder_sibling_calls(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        copied_nodes: list[dict[str, Any]] = []
        for raw in nodes or []:
            if not isinstance(raw, dict):
                continue
            copied = dict(raw)
            for child_key in ("children", "steps", "then", "else", "otherwise"):
                if isinstance(copied.get(child_key), list):
                    copied[child_key] = reorder_sibling_calls(copied[child_key])
            copied_nodes.append(copied)
        call_positions = [
            idx for idx, node in enumerate(copied_nodes)
            if node.get("type") == "call" and node.get("step_id") in order
        ]
        ordered_calls = sorted(
            (copied_nodes[idx] for idx in call_positions),
            key=lambda node: order.get(str(node.get("step_id") or ""), 10_000),
        )
        for idx, node in zip(call_positions, ordered_calls):
            copied_nodes[idx] = node
        return copied_nodes

    cap.nodes = reorder_sibling_calls(cap.nodes or [])
    if any(isinstance(n, dict) and n.get("type") not in {"call", "return"} for n in (cap.nodes or [])):
        existing_call_steps = set(_capability_call_step_ids_from_nodes(cap.nodes or []))
        missing = [sid for sid in cap.step_ids if sid not in existing_call_steps]
        if missing:
            return_nodes = [n for n in cap.nodes if isinstance(n, dict) and n.get("type") == "return"]
            body_nodes = [n for n in cap.nodes if not (isinstance(n, dict) and n.get("type") == "return")]
            body_nodes.extend({"id": f"call_{len(body_nodes) + i + 1}", "type": "call", "step_id": sid} for i, sid in enumerate(missing))
            cap.nodes = body_nodes + return_nodes
        return
    call_by_step: dict[str, dict[str, Any]] = {}
    other_nodes: list[dict[str, Any]] = []
    return_nodes: list[dict[str, Any]] = []
    for node in cap.nodes or []:
        if not isinstance(node, dict):
            continue
        if node.get("type") == "call" and node.get("step_id"):
            call_by_step.setdefault(str(node.get("step_id")), dict(node))
        elif node.get("type") == "return":
            return_nodes.append(dict(node))
        else:
            other_nodes.append(dict(node))
    ordered_calls: list[dict[str, Any]] = []
    for idx, sid in enumerate(cap.step_ids, 1):
        node = call_by_step.get(sid) or {"type": "call", "step_id": sid}
        node.setdefault("id", f"call_{idx}")
        ordered_calls.append(node)
    cap.nodes = ordered_calls + other_nodes + return_nodes


def _iter_capability_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        out.append(node)
        for key in ("steps", "then", "otherwise", "else", "children"):
            child = node.get(key)
            if isinstance(child, list):
                out.extend(_iter_capability_nodes([n for n in child if isinstance(n, dict)]))
    return out


def _capability_child_nodes(node: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        child = node.get(key)
        if isinstance(child, list):
            return [n for n in child if isinstance(n, dict)]
    return []


def _capability_call_step_ids_from_nodes(nodes: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for node in _iter_capability_nodes(nodes):
        sid = str(node.get("step_id") or "")
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def _capability_is_batch(spec: FlowSpec, cap: FlowCapability) -> bool:
    if cap.kind not in {"submit_batch", "validate_batch"}:
        return False
    by_id = {s.step_id: s for s in spec.steps}
    cap_steps = [by_id[sid] for sid in _capability_node_step_ids(cap) if sid in by_id]
    write_steps = [step for step in cap_steps if _is_write_step(step)]
    return _write_contract_is_batch(spec, write_steps, cap)


def _capability_execution_contract(spec: FlowSpec, cap: FlowCapability) -> dict[str, Any]:
    by_id = {s.step_id: s for s in spec.steps}
    call_ids = _capability_node_step_ids(cap)
    calls = [
        {
            "step_id": sid,
            "method": by_id[sid].method,
            "path": by_id[sid].path or by_id[sid].url,
            "role": (by_id[sid].source_meta or {}).get("role") or by_id[sid].semantic_role,
            "request_id": (by_id[sid].source_meta or {}).get("request_id"),
            "request_index": (by_id[sid].source_meta or {}).get("request_index"),
        }
        for sid in call_ids
        if sid in by_id
    ]
    final_step = calls[-1]["step_id"] if calls else ""
    foreach_nodes = [
        n for n in _iter_capability_nodes(cap.nodes or [])
        if isinstance(n, dict) and n.get("type") == "foreach"
    ]
    items_field = "entries"
    if foreach_nodes:
        raw_items = str(foreach_nodes[0].get("items") or "input.entries")
        if raw_items.startswith("input."):
            items_field = raw_items.split(".", 1)[1].split(".", 1)[0] or "entries"
    return {
        "protocol": "dano.capability_plan.v1",
        "name": cap.name,
        "kind": cap.kind,
        "nodes": [dict(n) for n in (cap.nodes or [])],
        "call_order": calls,
        "preconditions": [dict(p) for p in (cap.preconditions or []) if isinstance(p, dict)],
        "batch": {
            "enabled": _capability_is_batch(spec, cap),
            "items_field": items_field,
            "mode": "repeat_selected_workflow",
            "merge_base_input": True,
        },
        "return": cap.output_mapping or [{
            "kind": "final_response",
            "step_id": final_step,
            "response_path": "response",
        }],
    }


def _capability_field_summary(field: CapabilityField) -> dict[str, Any]:
    return {
        "field_id": field.field_id,
        "scope": field.scope,
        "display_name": field.display_name,
        "key": field.key,
        "path": field.path,
        "type": field.type,
        "required": bool(field.required),
        "step_id": field.step_id,
        "request_id": field.request_id,
        "request_index": field.request_index,
        "source_kind": field.source_kind,
        "exposed_to_caller": bool(field.exposed_to_caller),
        "confidence": float(field.confidence or 0.0),
        "confirmed": bool(field.confirmed),
        "locked": bool(field.locked),
    }


def _capability_dependency_summary(dep: CapabilityDependency) -> dict[str, Any]:
    return {
        "dependency_id": dep.dependency_id,
        "type": dep.type,
        "source": dict(dep.source or {}),
        "target": dict(dep.target or {}),
        "confidence": float(dep.confidence or 0.0),
        "confirmed": bool(dep.confirmed),
        "locked": bool(dep.locked),
        "reason": dep.reason,
    }


def _capability_step_summary(step: FlowStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "name": step.name,
        "method": (step.method or "").upper(),
        "path": step.path or step.url,
        "role": (step.source_meta or {}).get("role") or step.semantic_role,
        "request_id": (step.source_meta or {}).get("request_id"),
        "request_index": (step.source_meta or {}).get("request_index"),
    }


def _select_flow_capability(
    spec: FlowSpec,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> FlowCapability | None:
    cap_id = str(capability_id or "").strip()
    cap_name = str(capability_name or "").strip()
    if not cap_id and not cap_name:
        return None
    for cap in spec.capabilities or []:
        if cap_id and cap.capability_id == cap_id:
            return cap
        if cap_name and cap.name == cap_name:
            return cap
    return None


def _capability_contract_view(
    spec: FlowSpec,
    capability: FlowCapability | None = None,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> dict[str, Any]:
    """Build a capability-centric contract view for manifest/runtime consumers."""
    current = ensure_recorded_goal(_sync_capability_io_schemas(sync_flow_spec_models(
        spec.model_copy(deep=True),
        prefer_request_facts=False,
    )))
    _normalize_capability_references(current)
    cap = capability.model_copy(deep=True) if capability is not None else _select_flow_capability(
        current,
        capability_id=capability_id,
        capability_name=capability_name,
    )
    if cap is None:
        raise ValueError("capability not found")
    step_by_id = {s.step_id: s for s in current.steps}
    step_ids = [sid for sid in _capability_node_step_ids(cap) if sid in step_by_id]
    steps = [step_by_id[sid] for sid in step_ids]
    return {
        "protocol": "dano.capability_contract.v1",
        "capability_id": cap.capability_id,
        "name": cap.name,
        "title": cap.title,
        "intent": cap.intent,
        "kind": cap.kind,
        "status": cap.status,
        "confirmed": bool(cap.confirmed),
        "confidence": float(cap.confidence or 0.0),
        "requires_human_confirm": bool(cap.requires_human_confirm),
        "step_ids": step_ids,
        "steps": [_capability_step_summary(st) for st in steps],
        "request_refs": [ref.model_dump(exclude_none=True) for ref in (cap.request_refs or [])],
        "input": {
            "schema": dict(cap.input_schema or {}),
            "fields": [_capability_field_summary(f) for f in (cap.inputs or [])],
        },
        "output": {
            "schema": dict(cap.output_schema or {}),
            "fields": [_capability_field_summary(f) for f in (cap.outputs or [])],
            "mapping": [dict(m) for m in (cap.output_mapping or []) if isinstance(m, dict)],
        },
        "fields": {
            "all": [_capability_field_summary(f) for f in (cap.fields or [])],
            "request": [_capability_field_summary(f) for f in (cap.request_fields or [])],
            "internal": [_capability_field_summary(f) for f in (cap.internal_fields or [])],
            "computed": [_capability_field_summary(f) for f in (cap.computed_fields or [])],
        },
        "dependencies": [_capability_dependency_summary(dep) for dep in (cap.dependencies or [])],
        "execution_contract": _capability_execution_contract(current, cap),
        "preconditions": [dict(p) for p in (cap.preconditions or []) if isinstance(p, dict)],
        "caller_responsibilities": list(cap.caller_responsibilities or []),
        "skill_responsibilities": list(cap.skill_responsibilities or []),
    }


def _capability_contract_views(
    spec: FlowSpec,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> list[dict[str, Any]]:
    """Return capability contract summaries, optionally scoped to one capability."""
    current = ensure_recorded_goal(_sync_capability_io_schemas(sync_flow_spec_models(
        spec.model_copy(deep=True),
        prefer_request_facts=False,
    )))
    _normalize_capability_references(current)
    if capability_id or capability_name:
        cap = _select_flow_capability(current, capability_id=capability_id, capability_name=capability_name)
        if cap is None:
            return []
        return [_capability_contract_view(current, cap)]
    return [_capability_contract_view(current, cap) for cap in (current.capabilities or [])]


def _prune_empty_capabilities(spec: FlowSpec) -> FlowSpec:
    """能力必须拥有至少一个真实接口调用；枚举字段不能伪装成空业务能力。"""
    step_ids = {step.step_id for step in spec.steps}
    kept: list[FlowCapability] = []
    removed_refs: set[str] = set()
    for cap in spec.capabilities or []:
        actual = [sid for sid in _capability_node_step_ids(cap) if sid in step_ids]
        if actual:
            kept.append(cap)
            continue
        removed_refs.update({str(cap.name or ""), str(cap.capability_id or "")})
    spec.capabilities = kept
    if removed_refs:
        spec.capability_relations = [
            relation for relation in (spec.capability_relations or [])
            if str(relation.from_capability or "") not in removed_refs
            and str(relation.to_capability or "") not in removed_refs
        ]
    return spec


def _drop_superseded_baseline_capabilities(spec: FlowSpec, baseline_ids: set[str]) -> FlowSpec:
    """LLM 将通用基线完整拆成多个能力后，移除被覆盖的基线而不合并同类能力。"""
    remove_ids: set[str] = set()
    for baseline in spec.capabilities or []:
        if baseline.capability_id not in baseline_ids:
            continue
        baseline_steps = set(_capability_node_step_ids(baseline))
        if not baseline_steps:
            continue
        alternatives = [
            cap for cap in spec.capabilities or []
            if cap.capability_id not in baseline_ids
            and _capability_kind_family(cap.kind) == _capability_kind_family(baseline.kind)
            and set(_capability_node_step_ids(cap))
            and set(_capability_node_step_ids(cap)).issubset(baseline_steps)
        ]
        covered = {
            step_id
            for cap in alternatives
            for step_id in _capability_node_step_ids(cap)
        }
        if alternatives and covered == baseline_steps:
            remove_ids.add(baseline.capability_id)
    if remove_ids:
        spec.capabilities = [
            cap for cap in spec.capabilities
            if cap.capability_id not in remove_ids
        ]
    return spec


def _planner_patch_edits(
    spec: FlowSpec,
    edits: list[dict[str, Any]],
    *,
    scope_locked: bool,
) -> list[dict[str, Any]]:
    """限制 Planner 只能修当前编排，不能把捕获事实偷偷扩进能力。"""
    existing_caps = {cap.name for cap in spec.capabilities if cap.name}
    existing_steps = {step.step_id for step in spec.steps}
    step_by_id = {step.step_id: step for step in spec.steps}
    cap_by_name = {cap.name: cap for cap in spec.capabilities if cap.name}
    safe: list[dict[str, Any]] = []
    scope_ops = {
        "add_request_step", "add_candidate_step", "promote_request",
        "add_capability", "create_capability", "remove_capability",
        "remove_request_from_capability", "reject_dependency",
    }
    for raw in edits or []:
        edit = dict(raw)
        op = str(edit.get("op") or "")
        if op in scope_ops:
            continue
        if op == "add_request_to_capability":
            if scope_locked:
                continue
            # Planner 只能重组已经在字段/接口工作台物化的步骤，不能用 request_id
            # 或 request_index 从捕获事实库静默拉入新接口。
            step_id = str(edit.get("step_id") or "")
            if not step_id or step_id not in existing_steps:
                continue
        if op == "upsert_capability":
            payload = dict(edit.get("capability") or {})
            name = str(edit.get("capability_name") or edit.get("capability") or edit.get("name") or "")
            if payload:
                name = str(payload.get("name") or name)
                for key in ("step_ids", "request_refs", "nodes"):
                    payload.pop(key, None)
                edit["capability"] = payload
            if scope_locked and name not in existing_caps:
                continue
        if op == "update_capability" and str(edit.get("field") or "") in {"step_ids", "nodes", "request_refs"}:
            continue
        if op in {"add", "bind_dependency"}:
            confidence = float(edit.get("confidence") or (edit.get("link") or {}).get("confidence") or 0.0)
            if confidence < 0.95:
                continue
            if op == "add":
                link = dict(edit.get("link") or {})
                source_step_id = str(link.get("source_step_id") or "")
                source_path = str(link.get("source_path") or "")
                target_step_id = str(link.get("target_step_id") or "")
                target_path = str(link.get("target_path") or "")
                scoped_cap = None
            else:
                source = dict(edit.get("source") or {})
                target = dict(edit.get("target") or {})
                source_step_id = str(source.get("step_id") or edit.get("source_step_id") or "")
                source_path = str(source.get("path") or edit.get("source_path") or "")
                target_step_id = str(target.get("step_id") or edit.get("target_step_id") or "")
                target_path = str(target.get("path") or edit.get("target_path") or "")
                cap_name = str(edit.get("capability_name") or edit.get("capability") or "")
                scoped_cap = cap_by_name.get(cap_name)
            source_step = step_by_id.get(source_step_id)
            target_step = step_by_id.get(target_step_id)
            target_param = next((
                param for param in (target_step.params if target_step else [])
                if _strip_body_prefix(param.path) == _strip_body_prefix(target_path)
            ), None)
            if source_step is None or target_param is None:
                continue
            if not _capability_response_path_exists(source_step, source_path):
                continue
            if target_param.locked or not _auto_dependency_target_allowed(target_param):
                continue
            if target_param.category == "user_param" or target_param.source_kind == "user_input":
                continue
            if scoped_cap is not None:
                scoped_ids = set(_capability_node_step_ids(scoped_cap))
                if source_step_id not in scoped_ids or target_step_id not in scoped_ids:
                    continue
        safe.append(edit)
    return safe


def _relation_identity(relation: CapabilityRelation) -> tuple[str, str, str, str, str]:
    return (
        str(relation.relation_id or ""),
        str(relation.from_capability or ""),
        str(relation.from_output or ""),
        str(relation.to_capability or ""),
        str(relation.to_input or ""),
    )


def _link_identity(link: FlowLink) -> tuple[str, str, str, str, str]:
    return (
        str(link.link_id or ""),
        str(link.source_step_id or ""),
        str(link.source_path or ""),
        str(link.target_step_id or ""),
        str(link.target_path or ""),
    )


def _enforce_incremental_orchestration_scope(before: FlowSpec, after: FlowSpec) -> FlowSpec:
    """Hard guard for repeated Planner runs.

    Existing capabilities and their interface membership are immutable during an
    optimization click.  Fields, schemas, maps, conditions, dependencies and new
    explanatory relations may improve, while previously established links and
    relations cannot disappear.
    """
    current_by_id = {cap.capability_id: cap for cap in after.capabilities if cap.capability_id}
    current_by_name = {cap.name: cap for cap in after.capabilities if cap.name}
    guarded: list[FlowCapability] = []
    global_step_ids = {step.step_id for step in before.steps}
    for original in before.capabilities:
        current = current_by_id.get(original.capability_id) or current_by_name.get(original.name)
        if current is None:
            current = original.model_copy(deep=True)
        # Repeated optimization may enrich the contract but cannot change the
        # public API identity or membership chosen by the operator.
        current.capability_id = original.capability_id
        current.name = original.name
        current.kind = original.kind
        current.request_refs = [ref.model_copy(deep=True) for ref in (original.request_refs or [])]
        current.locked = original.locked
        current.updated_by = original.updated_by
        allowed_steps = list(_capability_node_step_ids(original))
        allowed_set = set(allowed_steps)
        current.step_ids = list(original.step_ids or allowed_steps)
        # Remove any newly introduced call to an interface outside the original
        # capability scope. Non-call orchestration nodes remain eligible.
        for step_id in global_step_ids - allowed_set:
            current.nodes = _remove_capability_step_nodes(current.nodes or [], step_id)
        current_node_ids = {
            str(node.get("id") or "")
            for node in _iter_capability_nodes(current.nodes or [])
            if isinstance(node, dict) and node.get("id")
        }
        for node in original.nodes or []:
            node_id = str(node.get("id") or "") if isinstance(node, dict) else ""
            # Preserve concrete interface calls only. Invalid map/condition/
            # foreach nodes are repairable orchestration semantics and must not
            # be resurrected by the scope guard.
            if (
                isinstance(node, dict)
                and node.get("type") == "call"
                and node_id
                and node_id not in current_node_ids
            ):
                current.nodes.append(copy.deepcopy(node))
                current_node_ids.add(node_id)
        guarded.append(current)
    after.capabilities = guarded

    existing_relations = {_relation_identity(item): item for item in after.capability_relations or []}
    for relation in before.capability_relations or []:
        identity = _relation_identity(relation)
        if identity not in existing_relations:
            after.capability_relations.append(relation.model_copy(deep=True))
            existing_relations[identity] = relation

    existing_links = {_link_identity(item): item for item in after.links or []}
    for link in before.links or []:
        identity = _link_identity(link)
        if identity not in existing_links:
            after.links.append(link.model_copy(deep=True))
            existing_links[identity] = link
    _normalize_capability_references(after)
    return after


def _normalize_incremental_write_kind(spec: FlowSpec) -> None:
    """Correct only an unsupported batch label; never alter capability scope."""
    by_id = {step.step_id: step for step in spec.steps}
    for cap in spec.capabilities or []:
        if cap.kind != "submit_batch":
            continue
        writes = [
            by_id[step_id]
            for step_id in _capability_node_step_ids(cap)
            if step_id in by_id and _is_write_step(by_id[step_id])
        ]
        if not writes or _write_contract_is_batch(spec, writes, cap):
            continue
        cap.kind = "submit"
        if re.fullmatch(r"submit_batch\d*", str(cap.name or "")):
            cap.name = "submit"
        if "批量提交" in str(cap.title or ""):
            cap.title = str(cap.title).replace("批量提交", "提交", 1)
        elif str(cap.title or "").startswith("批量"):
            cap.title = str(cap.title)[2:] or "提交"


def _capability_to_api_dict(spec: FlowSpec, cap: FlowCapability) -> dict[str, Any]:
    out = cap.model_dump(exclude_none=True)
    contract = _capability_execution_contract(spec, cap)
    out["execution_contract"] = contract
    out["workflow_nodes"] = contract["nodes"]
    out["compiled_step_ids"] = [c["step_id"] for c in contract["call_order"]]
    return out


async def orchestrate_flow_capabilities(
    spec: FlowSpec,
    *,
    llm_client: Any | None = None,
    model: str | None = None,
    timeout_s: float = 60.0,
) -> FlowSpec:
    """生成/优化能力编排。

    重复点击的语义：增量优化，不全量重生成。
    - 已有 capabilities：保留人工编辑（confirmed / locked / step_ids / fields / dependencies），
      仅由 LLM 通过 patch ops 增量修正；LLM 不可用时不动 capabilities。
    - 首次生成：调用 build_default_flow_capabilities 出 baseline，再让 LLM 优化。
    """
    original = spec.model_copy(deep=True)
    initial_report = validate_flow_spec(original)
    current = _prune_empty_capabilities(original.model_copy(deep=True))
    rebuild_flow_dependencies(current)
    had_existing = bool(current.capabilities)
    scope_baseline = current.model_copy(deep=True)
    # 首次从当前已物化步骤建立能力；再次点击时锁定能力和接口集合，只修契约。
    # 捕获事实库中的其他接口只能由用户显式加入，Planner 不得静默扩张范围。
    if not had_existing:
        current.capabilities = build_default_flow_capabilities(current)
    baseline_ids = {cap.capability_id for cap in current.capabilities} if not had_existing else set()
    current = _prune_empty_capabilities(current)
    source = "incremental" if had_existing else "deterministic"
    reason = ""

    if llm_client is not None and model:
        try:
            out = await llm_client.complete_json(
                model=model,
                system=_FLOW_ORCHESTRATE_SYSTEM,
                user="【FlowSpec 编排上下文】\n" + json.dumps({
                    **_orchestration_context(current),
                    "scope_locked": had_existing,
                }, ensure_ascii=False),
                timeout_s=timeout_s,
            )
            raw_ops = out.get("ops") if isinstance(out, dict) else None
            if isinstance(raw_ops, list):
                edits = _planner_patch_edits(
                    current,
                    _autofix_ops_to_edits(current, raw_ops),
                    scope_locked=had_existing,
                )
                if edits:
                    current = apply_flow_edits(current, [{**edit, "actor": "planner"} for edit in edits])
                    source = "llm_patch"
            raw_abilities = out.get("abilities") if isinstance(out, dict) else None
            if isinstance(raw_abilities, list) and not had_existing:
                step_ids = {step.step_id for step in current.steps}
                used = {cap.name for cap in current.capabilities if cap.name}
                generated = [
                    cap for raw in raw_abilities
                    if (cap := _capability_from_llm(raw, step_ids, used)) is not None
                ]
                current.capabilities = _merge_capability_lists(
                    list(current.capabilities or []), generated, spec=current, allow_new=True,
                )
                if generated and source != "llm_patch":
                    source = "llm"
        except Exception as exc:  # noqa: BLE001
            reason = str(exc)[:240]

    if baseline_ids:
        current = _drop_superseded_baseline_capabilities(current, baseline_ids)
    _normalize_capability_references(current)
    if not had_existing:
        current = _repair_generated_capability_contracts(current)
    # Existing public capability identities are immutable on repeated optimize.
    current = _ensure_external_transform_relations(
        _sync_capability_io_schemas(sync_flow_spec_models(current, prefer_request_facts=False))
    )
    if had_existing:
        current = _enforce_incremental_orchestration_scope(scope_baseline, current)
    caps = list(current.capabilities or [])
    final_report = validate_flow_spec(current)
    current.meta = {
        **(current.meta or {}),
        "capability_model": {
            "status": "ready",
            "source": source,
            "generated_count": len(caps),
            "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "capability_orchestration_audit": {
            "mode": "incremental" if had_existing else "initial",
            "checked_steps": len(original.steps),
            "checked_fields": sum(len(step.params or []) for step in original.steps),
            "checked_captured_requests": len(_request_graph_items(original)),
            "before_errors": len(initial_report.get("errors") or []),
            "before_warnings": len(initial_report.get("warnings") or []),
            "after_errors": len(final_report.get("errors") or []),
            "after_warnings": len(final_report.get("warnings") or []),
            "scope_locked": had_existing,
            "capability_count_before": len(scope_baseline.capabilities or []),
            "capability_count_after": len(caps),
        },
    }
    return append_flow_version(refresh_review_items(current), "orchestrate_flow", reason=f"生成能力编排: {source}")


def _effective_flow_capabilities(spec: FlowSpec) -> list[FlowCapability]:
    return list(spec.capabilities or build_default_flow_capabilities(spec))


def _capability_node_step_ids(cap: FlowCapability) -> list[str]:
    ids: list[str] = []
    for sid in cap.step_ids or []:
        if sid and sid not in ids:
            ids.append(sid)
    for node in _iter_capability_nodes(cap.nodes or []):
        sid = str(node.get("step_id") or "")
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def _step_request_key(step: FlowStep) -> str:
    meta = step.source_meta or {}
    if meta.get("request_id"):
        return f"id:{meta.get('request_id')}"
    if meta.get("request_index") is not None:
        return f"idx:{meta.get('request_index')}"
    return f"sig:{(step.method or '').upper()} {_request_path({'url': step.path or step.url})}"


def _step_request_signature_key(step: FlowStep) -> str:
    return f"{(step.method or '').upper()} {_request_path({'url': step.path or step.url})}"


def _request_graph_signature_key(entry: dict[str, Any]) -> str:
    return f"{(entry.get('method') or '').upper()} {_request_path(entry)}"


def _request_graph_key_from_entry(entry: dict[str, Any]) -> str:
    if entry.get("request_id"):
        return f"id:{entry.get('request_id')}"
    if entry.get("request_index") is not None:
        return f"idx:{entry.get('request_index')}"
    return f"sig:{(entry.get('method') or '').upper()} {_request_path(entry)}"


def _capability_ref_key(value: Any) -> str:
    return str(value or "").strip()


def _capability_request_indexes(spec: FlowSpec) -> tuple[set[str], set[str]]:
    request_ids: set[str] = set()
    request_indexes: set[str] = set()
    for fact in (spec.request_facts.requests or []):
        if fact.request_id:
            request_ids.add(str(fact.request_id))
        if fact.request_index is not None:
            request_indexes.add(str(fact.request_index))
    for item in _request_graph_items(spec):
        if item.get("request_id"):
            request_ids.add(str(item.get("request_id")))
        if item.get("request_index") is not None:
            request_indexes.add(str(item.get("request_index")))
    return request_ids, request_indexes


def _capability_schema_field_type(schema: dict[str, Any], field: str) -> str:
    props = (schema or {}).get("properties") or {}
    item = props.get(field) if isinstance(props, dict) else None
    if isinstance(item, dict):
        return str(item.get("type") or "")
    return ""


def _capability_field_type(cap: FlowCapability, field_name: str, *, direction: str) -> str:
    field_name = _capability_ref_key(field_name)
    fields = cap.outputs if direction == "output" else cap.inputs
    for field in fields or []:
        if field_name in {field.path, field.key, field.display_name, field.field_id}:
            return str(field.type or "")
    schema = cap.output_schema if direction == "output" else cap.input_schema
    schema_type = _capability_schema_field_type(schema, field_name)
    if schema_type:
        return schema_type
    if direction == "output":
        for mapping in cap.output_mapping or []:
            if not isinstance(mapping, dict):
                continue
            names = {
                str(mapping.get("name") or ""),
                str(mapping.get("field") or ""),
                str(mapping.get("response_path") or ""),
                str(mapping.get("path") or ""),
            }
            if field_name and field_name in names:
                return "object" if field_name in {"response", "raw", "detail"} else "string"
    return ""


def _capability_types_compatible(source_type: str, target_type: str) -> bool:
    source = (source_type or "unknown").lower()
    target = (target_type or "unknown").lower()
    if not source or not target or "unknown" in {source, target}:
        return True
    aliases = {
        "integer": "number",
        "float": "number",
        "double": "number",
        "enum": "string",
        "list-enum": "array",
    }
    source = aliases.get(source, source)
    target = aliases.get(target, target)
    if source == target:
        return True
    if target == "string":
        return source in {"number", "boolean", "date", "datetime"}
    if target == "object":
        return True
    return False


def _step_body_is_array(step: FlowStep) -> bool:
    raw = str(step.body_source or "").strip()
    if not raw:
        return False
    try:
        return isinstance(json.loads(raw), list)
    except Exception:  # noqa: BLE001
        return raw.startswith("[")


def _batch_capability_input_schema(steps: list[FlowStep]) -> dict[str, Any]:
    """批量能力只把逐条字段放进 entries，能力级共享字段保留在顶层。"""
    item_params: list[ParamField] = []
    shared_params: list[ParamField] = []
    write_user_params: list[ParamField] = []
    for step in steps:
        is_write = (step.method or "").upper() in _WRITE_METHODS
        array_body = is_write and _step_body_is_array(step)
        for param in step.params or []:
            if param.category != "user_param" or not param.exposed_to_user:
                continue
            if is_write:
                write_user_params.append(param)
            if is_write and (array_body or "[" in str(param.path or "")):
                item_params.append(param)
            else:
                shared_params.append(param)

    # 某些接口只通过 URL/名称体现 batch，body 快照不是标准 JSON。此时写接口业务字段
    # 仍应作为每条明细，而不是错误地要求调用方在顶层重复提交。
    if not item_params and write_user_params:
        item_params = list(write_user_params)
        write_ids = {id(param) for param in write_user_params}
        shared_params = [param for param in shared_params if id(param) not in write_ids]

    item_schema = _capability_input_schema(item_params)
    shared_schema = _capability_input_schema(shared_params)
    properties = dict(shared_schema.get("properties") or {})
    properties["entries"] = {
        "type": "array",
        "minItems": 1,
        "description": "批量提交明细；每个元素使用同一套业务字段",
        "items": item_schema,
    }
    required = list(dict.fromkeys([*(shared_schema.get("required") or []), "entries"]))
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _capability_step_param_exists(step: FlowStep | None, path: str) -> bool:
    if step is None:
        return False
    normalized = _strip_body_prefix(path)
    for param in step.params or []:
        if path in {param.path, param.key, param.label} or normalized in {param.path, param.key, param.label}:
            return True
    return False


def _capability_field_ref(field: CapabilityField) -> str:
    return field.key or field.path or field.display_name or field.field_id


def _capability_field_looks_internal(field: CapabilityField) -> bool:
    text = f"{field.path}.{field.key}.{field.display_name}"
    if not _INTERNAL_EXPOSED_PATH_RE.search(text):
        return False
    source_kind = str(field.source_kind or "")
    if (
        source_kind in _OPTION_SOURCE_KINDS
        or source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
        or bool(field.enum_options or field.enum_value_map)
    ):
        return False
    return True


def _capability_schema_array_item_props(schema: dict[str, Any], field_name: str) -> tuple[set[str], set[str]]:
    props = (schema or {}).get("properties") or {}
    item = props.get(field_name) if isinstance(props, dict) else None
    if not isinstance(item, dict):
        return set(), set()
    items = item.get("items") if isinstance(item.get("items"), dict) else {}
    item_props = (items or {}).get("properties") or {}
    required = (items or {}).get("required") or []
    return set(item_props.keys()) if isinstance(item_props, dict) else set(), set(str(x) for x in required)


def _capability_response_path_exists(step: FlowStep | None, path: str) -> bool:
    if step is None or step.response_json is None:
        return True
    normalized = _strip_body_prefix(path)
    if normalized in {"", "response", "$", "."}:
        return True
    return _flow_path_lookup(step.response_json, normalized) is not _FLOW_PATH_MISSING


def _capability_input_refs(expr: str) -> set[str]:
    refs = set(re.findall(r"\binput\.([a-zA-Z_][\w]*)", expr or ""))
    if re.fullmatch(r"[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?\s*(?:==|!=|>=|<=|>|<|in\b).+", expr or ""):
        head = re.split(r"==|!=|>=|<=|>|<|\bin\b", expr, 1)[0].strip()
        if head and not head.startswith(("var.", "node.", "response.")):
            refs.add(head.split(".", 1)[0].removeprefix("input."))
    return {ref for ref in refs if ref}


def _capability_value_ref_exists(
    ref: str,
    *,
    input_props: dict[str, Any],
    cap_node_ids: set[str],
    step_by_id: dict[str, FlowStep],
    cap_step_id_set: set[str],
) -> bool:
    value = str(ref or "").strip()
    if not value:
        return False
    if (
        (len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'})
        or re.fullmatch(r"-?\d+(?:\.\d+)?", value)
        or value.lower() in {"true", "false", "null", "none"}
        or value.startswith(("literal:", "const:", "computed:"))
    ):
        return True
    if value.startswith("input."):
        return value.split(".", 1)[1].split(".", 1)[0] in input_props
    if value.startswith(("var.", "computed.", "loop.", "item.", "const.")):
        return True
    if value.startswith("node."):
        return value.split(".", 1)[1].split(".", 1)[0] in cap_node_ids
    if "." in value:
        head, tail = value.split(".", 1)
        if head in cap_node_ids:
            return True
        if head in cap_step_id_set:
            return _capability_response_path_exists(step_by_id.get(head), tail)
    return value in input_props or value in cap_node_ids or value in cap_step_id_set


def _capability_warning(
    section: dict[str, Any],
    warnings: list[str],
    *,
    code: str,
    message: str,
    target: dict[str, Any],
) -> None:
    entry = {"code": code, "message": message, "target": target}
    section.setdefault("warnings", []).append(entry)
    warnings.append(message)


def _capability_error(
    section: dict[str, Any],
    *,
    code: str,
    message: str,
    target: dict[str, Any],
) -> None:
    section.setdefault("errors", []).append({"code": code, "message": message, "target": target})


def _capability_field_has_valid_source(
    field: CapabilityField,
    dependency_targets: set[tuple[str, str]],
) -> bool:
    if field.exposed_to_caller:
        return True
    if field.source:
        return True
    if field.source_kind and field.source_kind not in {"unknown", "user_input"}:
        return True
    return (field.step_id, _strip_body_prefix(field.path or field.key)) in dependency_targets


def _capability_param_enum_issue(param: ParamField) -> str:
    if param.type not in {"enum", "list-enum"} and param.source_kind not in _OPTION_SOURCE_KINDS:
        return ""
    if param.source_kind == "api_option":
        if (
            (param.source or {}).get("source_step_id")
            or (param.source or {}).get("source_url")
            or (param.source or {}).get("url")
        ):
            return ""
        return "动态枚举缺少可执行的实时来源接口"
    if param.source_kind == "page_enum" and (param.source or {}).get("enum_confirmed") is False:
        return "页面枚举快照不完整，必须重新展开下拉捕获全部选项或绑定真实选项接口"
    if not param.enum_options:
        return "缺少可执行枚举选项 label/value"
    if not _enum_map_covers_recorded_value(param):
        return "枚举 label/value 不能映射录制提交值"
    if _enum_options_look_value_only(param):
        return "枚举候选看起来只有内部值，缺少可展示 label"
    return ""


def _capability_validation_report(spec: FlowSpec) -> dict[str, Any]:
    spec = ensure_recorded_goal(_sync_capability_io_schemas(spec.model_copy(deep=True)))
    _normalize_capability_references(spec)
    errors: list[str] = []
    warnings: list[str] = []
    caps = list(spec.capabilities or [])
    step_by_id = {s.step_id: s for s in spec.steps}
    graph_items = _request_graph_items(spec)
    materialized_keys = {_step_request_key(s) for s in spec.steps}
    materialized_signatures = {_step_request_signature_key(s) for s in spec.steps}
    high_conf_unused = [
        {
            "request_id": item.get("request_id"),
            "request_index": item.get("request_index"),
            "method": item.get("method"),
            "path": item.get("path") or item.get("url"),
            "role": item.get("role"),
            "confidence": item.get("confidence"),
            "reason": item.get("reason"),
        }
        for item in graph_items
        if float(item.get("confidence") or 0) >= 0.9
        and (item.get("role") or "") in {"submit_anchor", "business_write", "business_get", "read_context", "read_option"}
        and _request_graph_key_from_entry(item) not in materialized_keys
        and _request_graph_signature_key(item) not in materialized_signatures
    ]
    checked_requests: list[dict[str, Any]] = []
    checked_manual_requests: list[dict[str, Any]] = []
    capability_reports: list[dict[str, Any]] = []
    capability_internal = {
        "passed": True,
        "errors": [],
        "warnings": [],
        "capabilities": [],
    }
    capability_relations = {
        "passed": True,
        "errors": [],
        "warnings": [],
        "relations": [],
    }
    skill_level = {
        "passed": True,
        "errors": [],
        "warnings": [],
        "summary": {
            "capabilities": len(caps),
            "confirmed_capabilities": len([c for c in caps if c.confirmed]),
            "relations": len(spec.capability_relations or []),
        },
    }
    if spec.steps and not caps:
        warnings.append("FlowSpec 未生成业务能力编排，前端只能按底层接口展示")
        _capability_warning(
            skill_level,
            warnings,
            code="missing_capabilities",
            message="Skill 层未生成 capability，P1 仅记录为能力编排缺口",
            target={"kind": "flow", "flow_id": spec.flow_id},
        )
        return {
            "passed": False,
            "errors": errors,
            "warnings": warnings,
            "capabilities": [],
            "checked_requests": checked_requests,
            "checked_manual_requests": checked_manual_requests,
            "unused_high_confidence_requests": high_conf_unused,
            "capability_internal": capability_internal,
            "capability_relations": capability_relations,
            "skill_level": skill_level,
        }

    allowed_kinds = {"query_status", "list_options", "validate_batch", "submit_batch", "submit"}
    allowed_nodes = {"call", "map", "filter", "condition", "foreach", "select", "return"}
    seen_names: set[str] = set()
    request_ids, request_indexes = _capability_request_indexes(spec)
    for cap in caps:
        label = cap.name or cap.kind or "<unnamed>"
        cap_errors: list[str] = []
        cap_warnings: list[str] = []
        internal_section = {
            "name": cap.name,
            "capability_id": cap.capability_id,
            "step_ids": [],
            "request_refs": [],
            "fields": [],
            "dependencies": [],
            "outputs": [],
            "warnings": [],
            "errors": [],
        }
        if not cap.name:
            cap_errors.append("Capability 缺少 name")
        elif cap.name in seen_names:
            cap_errors.append(f"Capability `{cap.name}` 重名")
        seen_names.add(cap.name)

        if cap.kind not in allowed_kinds:
            cap_errors.append(f"Capability `{label}` kind `{cap.kind}` 不在允许范围内")

        if cap.kind in {"submit_batch", "validate_batch"} and not _capability_is_batch(spec, cap):
            cap_errors.append(
                f"Capability `{label}` 被声明为批量能力，但没有批量接口事实或明确的 entries 循环设计"
            )
        if cap.kind in {"submit_batch", "validate_batch"}:
            item_props, _item_required = _capability_schema_array_item_props(cap.input_schema, "entries")
            routing_names = {
                name for name in item_props
                if _ROUTING_FIELD_RE.search(str(name or ""))
            }
            if item_props and routing_names == item_props:
                cap_errors.append(
                    f"Capability `{label}` 的 entries 只有审批/路由字段，不能把人员列表当成批量业务条目"
                )

        node_step_ids = _capability_node_step_ids(cap)
        if not node_step_ids:
            cap_errors.append(f"Capability `{label}` 没有绑定真实接口，空能力不能发布")
            _capability_error(
                internal_section,
                code="capability_empty",
                message=f"Capability `{label}` 没有绑定真实接口",
                target={"kind": "capability", "capability": label},
            )
        missing_step_ids = [sid for sid in node_step_ids if sid not in step_by_id]
        if missing_step_ids:
            msg = f"Capability `{label}` 指向不存在的步骤: {missing_step_ids}"
            if cap.confirmed:
                cap_errors.append(msg)
            else:
                cap_warnings.append(msg)

        if not cap.confirmed or cap.requires_human_confirm:
            cap_warnings.append(f"Capability `{label}` 尚未确认，需要人工确认后再作为稳定业务能力暴露")

        cap_steps = [step_by_id[sid] for sid in node_step_ids if sid in step_by_id]
        cap_step_id_set = {s.step_id for s in cap_steps}
        internal_section["step_ids"] = [
            {"step_id": sid, "exists": sid in step_by_id}
            for sid in node_step_ids
        ]
        cap_request_keys: list[str] = []
        for st in cap_steps:
            key = _step_request_key(st)
            if key not in cap_request_keys:
                cap_request_keys.append(key)
                req_item = {
                    "step_id": st.step_id,
                    "request_key": key,
                    "method": st.method,
                    "path": st.path or st.url,
                    "manual_added": bool((st.source_meta or {}).get("manual_added")),
                }
                checked_requests.append(req_item)
                if req_item["manual_added"]:
                    checked_manual_requests.append(req_item)
            for param in st.params or []:
                enum_issue = _capability_param_enum_issue(param)
                if not enum_issue:
                    continue
                msg = f"Capability `{label}` 枚举字段 `{param.key or param.path}` {enum_issue}"
                target = {
                    "kind": "capability_enum",
                    "capability": label,
                    "step_id": st.step_id,
                    "path": param.path,
                }
                if cap.confirmed:
                    cap_errors.append(msg)
                    _capability_error(internal_section, code="capability_enum_mapping_missing", message=msg, target=target)
                else:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_enum_mapping_missing",
                        message=msg,
                        target=target,
                    )

        for ref in cap.request_refs or []:
            ref_id = _capability_ref_key(ref.request_id)
            ref_index = _capability_ref_key(ref.request_index)
            step_exists = not ref.step_id or ref.step_id in cap_step_id_set
            request_exists = (
                (not ref_id and not ref_index)
                or (ref_id and ref_id in request_ids)
                or (ref_index and ref_index in request_indexes)
            )
            internal_section["request_refs"].append({
                "request_id": ref.request_id,
                "request_index": ref.request_index,
                "step_id": ref.step_id,
                "step_exists": step_exists,
                "request_exists": request_exists,
            })
            if not step_exists:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_request_ref_step_missing",
                    message=f"Capability `{label}` request_ref 指向能力闭包外步骤 `{ref.step_id}`",
                    target={"kind": "capability_request_ref", "capability": label, "step_id": ref.step_id},
                )
            if not request_exists:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_request_ref_missing",
                    message=f"Capability `{label}` request_ref `{ref_id or ref_index}` 找不到对应请求事实",
                    target={"kind": "capability_request_ref", "capability": label, "request_id": ref_id, "request_index": ref_index},
                )

        input_props = ((cap.input_schema or {}).get("properties") or {})
        dependency_targets = {
            (
                str((dep.target or {}).get("step_id") or ""),
                _strip_body_prefix(str((dep.target or {}).get("path") or "")),
            )
            for dep in cap.dependencies or []
        }
        canonical_fields = [
            *(cap.inputs or []),
            *(cap.request_fields or []),
            *(cap.internal_fields or []),
            *(cap.computed_fields or []),
            *(cap.outputs or []),
        ]
        if not canonical_fields:  # 仅兼容尚未迁移的旧资产
            canonical_fields = list(cap.fields or [])
        seen_field_entries: set[tuple[str, str, str, str]] = set()
        for field in canonical_fields:
            field_key = (field.field_id, field.scope, field.step_id, field.path or field.key)
            if field_key in seen_field_entries:
                continue
            seen_field_entries.add(field_key)
            field_name = field.key or field.path or field.display_name or field.field_id
            field_step = step_by_id.get(field.step_id or "")
            if field.step_id and field.step_id not in cap_step_id_set:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_field_step_outside_closure",
                    message=f"Capability `{label}` 字段 `{field_name}` 绑定到能力闭包外步骤 `{field.step_id}`",
                    target={"kind": "capability_field", "capability": label, "field_id": field.field_id, "step_id": field.step_id},
                )
            field_path_exists = True
            if field.scope in {"request_field", "internal"} and field.step_id:
                field_path_exists = _capability_step_param_exists(field_step, field.path or field.key)
            elif field.scope == "input" and field_name:
                field_path_exists = (
                    _schema_path_exists(cap.input_schema, field.path, field.key)
                    or field_name in input_props
                    or _capability_step_param_exists(field_step, field.path or field.key)
                )
            internal_section["fields"].append({
                "field_id": field.field_id,
                "scope": field.scope,
                "path": field.path,
                "key": field.key,
                "step_id": field.step_id,
                "path_exists": field_path_exists,
            })
            if not field_path_exists:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_field_path_missing",
                    message=f"Capability `{label}` 字段 `{field_name}` 找不到对应字段路径",
                    target={"kind": "capability_field", "capability": label, "field_id": field.field_id, "path": field.path},
                )
            if (
                field.scope in {"request_field", "internal"}
                and not _capability_field_has_valid_source(field, dependency_targets)
            ):
                msg = f"Capability `{label}` 内部字段 `{field_name}` 缺少上游响应、系统值或固定来源"
                target = {"kind": "capability_field", "capability": label, "field_id": field.field_id, "path": field.path}
                if cap.confirmed and field.required:
                    cap_errors.append(msg)
                    _capability_error(internal_section, code="capability_field_source_missing", message=msg, target=target)
                else:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_field_source_missing",
                        message=msg,
                        target=target,
                    )
            if (
                field.scope in {"input", "request_field"}
                and field.exposed_to_caller
                and _capability_field_looks_internal(field)
            ):
                msg = f"Capability `{label}` 字段 `{field_name}` 看起来是内部 ID/短码/状态码，不能直接暴露给调用方"
                target = {"kind": "capability_field", "capability": label, "field_id": field.field_id, "path": field.path}
                if cap.confirmed:
                    cap_errors.append(msg)
                    _capability_error(internal_section, code="capability_internal_field_exposed", message=msg, target=target)
                else:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_internal_field_exposed",
                        message=msg,
                        target=target,
                    )

        for dep in cap.dependencies or []:
            source = dep.source or {}
            target = dep.target or {}
            source_step_id = str(source.get("step_id") or "")
            target_step_id = str(target.get("step_id") or "")
            source_step = step_by_id.get(source_step_id)
            target_step = step_by_id.get(target_step_id)
            source_in_closure = bool(source_step_id and source_step_id in cap_step_id_set)
            target_in_closure = bool(target_step_id and target_step_id in cap_step_id_set)
            source_path = str(source.get("path") or "")
            target_path = str(target.get("path") or "")
            source_exists = _capability_response_path_exists(source_step, source_path)
            target_exists = _capability_step_param_exists(target_step, target_path)
            internal_section["dependencies"].append({
                "dependency_id": dep.dependency_id,
                "source_step_id": source_step_id,
                "target_step_id": target_step_id,
                "source_in_closure": source_in_closure,
                "target_in_closure": target_in_closure,
                "source_path_exists": source_exists,
                "target_path_exists": target_exists,
            })
            if not source_in_closure or not target_in_closure:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_dependency_outside_closure",
                    message=f"Capability `{label}` 依赖 `{dep.dependency_id}` 端点不都在能力闭包内",
                    target={"kind": "capability_dependency", "capability": label, "dependency_id": dep.dependency_id},
                )
            if not source_exists or not target_exists:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_dependency_endpoint_missing",
                    message=f"Capability `{label}` 依赖 `{dep.dependency_id}` 的 source/target 路径无法确认存在",
                    target={"kind": "capability_dependency", "capability": label, "dependency_id": dep.dependency_id},
                )

        for idx, mapping in enumerate(cap.output_mapping or []):
            output_entry = {"index": idx, "interpretable": True}
            if not isinstance(mapping, dict):
                output_entry.update({"interpretable": False, "reason": "not_object"})
                internal_section["outputs"].append(output_entry)
                msg = f"Capability `{label}` output_mapping[{idx}] 不是对象，无法解释输出"
                target = {"kind": "capability_output", "capability": label, "index": idx}
                if cap.confirmed:
                    cap_errors.append(msg)
                    _capability_error(internal_section, code="capability_output_mapping_invalid", message=msg, target=target)
                else:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_output_mapping_invalid",
                        message=msg,
                        target=target,
                    )
                continue
            out_step_id = str(mapping.get("step_id") or mapping.get("from") or "")
            out_path = str(mapping.get("response_path") or mapping.get("path") or mapping.get("field") or "")
            output_entry.update({"step_id": out_step_id, "path": out_path})
            if out_step_id and out_step_id not in cap_step_id_set:
                output_entry["interpretable"] = False
                output_entry["reason"] = "step_outside_closure"
            elif out_step_id and not _capability_response_path_exists(step_by_id.get(out_step_id), out_path):
                output_entry["interpretable"] = False
                output_entry["reason"] = "response_path_missing"
            elif not (mapping.get("kind") or out_step_id or out_path or mapping.get("name") or mapping.get("field")):
                output_entry["interpretable"] = False
                output_entry["reason"] = "missing_source"
            internal_section["outputs"].append(output_entry)
            if not output_entry["interpretable"]:
                msg = f"Capability `{label}` output_mapping[{idx}] 无法解释为能力输出"
                if cap.confirmed:
                    cap_errors.append(msg)
                    internal_section.setdefault("errors", []).append({
                        "code": "capability_output_mapping_uninterpretable",
                        "message": msg,
                        "target": {"kind": "capability_output", "capability": label, "index": idx},
                    })
                else:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_output_mapping_uninterpretable",
                        message=msg,
                        target={"kind": "capability_output", "capability": label, "index": idx},
                    )
        if not cap.output_mapping and not cap.output_schema and not any(
            isinstance(n, dict) and n.get("type") == "return" for n in _iter_capability_nodes(cap.nodes or [])
        ):
            msg = f"Capability `{label}` 缺少 output_schema/output_mapping/return 输出说明"
            target = {"kind": "capability", "capability": label}
            if cap.confirmed:
                cap_errors.append(msg)
                _capability_error(internal_section, code="capability_output_missing", message=msg, target=target)
            else:
                _capability_warning(
                    internal_section,
                    warnings,
                    code="capability_output_missing",
                    message=msg,
                    target=target,
                )

        input_props = ((cap.input_schema or {}).get("properties") or {})
        flat_nodes = _iter_capability_nodes(cap.nodes or [])
        cap_node_ids = {str(n.get("id") or "") for n in flat_nodes if isinstance(n, dict) and n.get("id")}
        return_sources = [
            f"{sid}({step_by_id[sid].method} {step_by_id[sid].path or step_by_id[sid].url})"
            for sid in node_step_ids
            if sid in step_by_id
        ]
        has_return_node = any(isinstance(n, dict) and n.get("type") == "return" for n in flat_nodes)
        for node in flat_nodes:
            if not isinstance(node, dict):
                cap_errors.append(f"Capability `{label}` 包含非法节点")
                continue
            node_type = str(node.get("type") or "")
            node_id = str(node.get("id") or node_type or "<node>")
            if node_type not in allowed_nodes:
                cap_errors.append(f"Capability `{label}` 节点 `{node_id}` 类型 `{node_type}` 不支持")
            if node_type == "call" and str(node.get("step_id") or "") not in step_by_id:
                cap_errors.append(f"Capability `{label}` call 节点 `{node_id}` 未绑定有效接口步骤")
            if node_type == "condition":
                expr = str(node.get("condition") or node.get("check") or node.get("expr") or "")
                if not expr:
                    cap_errors.append(f"Capability `{label}` condition 节点 `{node_id}` 缺少 condition/check 表达式")
                else:
                    for ref in _capability_input_refs(expr):
                        if ref not in input_props:
                            cap_errors.append(f"Capability `{label}` condition 节点 `{node_id}` 引用的输入 `{ref}` 不存在")
                if not any(isinstance(node.get(k), list) and node.get(k) for k in ("then", "steps", "children", "otherwise", "else")):
                    cap_warnings.append(f"Capability `{label}` condition 节点 `{node_id}` 没有任何分支步骤")
            if node_type == "foreach":
                items = str(node.get("items") or "")
                if not items:
                    cap_errors.append(f"Capability `{label}` foreach 节点 `{node_id}` 缺少 items 数组来源")
                elif items.startswith("input."):
                    field = items.split(".", 1)[1].split(".", 1)[0]
                    schema = input_props.get(field) or {}
                    if field not in input_props:
                        cap_errors.append(f"Capability `{label}` foreach 节点 `{node_id}` 引用的输入 `{field}` 不存在")
                    elif schema.get("type") != "array":
                        cap_errors.append(f"Capability `{label}` foreach 节点 `{node_id}` 的输入 `{field}` 不是数组")
                    item_props, _item_required = _capability_schema_array_item_props(cap.input_schema or {}, field)
                    child_step_ids = {
                        str(n.get("step_id") or "")
                        for n in _iter_capability_nodes(_capability_child_nodes(node, "steps", "children"))
                        if isinstance(n, dict) and n.get("type") == "call"
                    }
                    if child_step_ids:
                        root_inputs = set(input_props.keys())
                        for child_sid in child_step_ids:
                            child_step = step_by_id.get(child_sid)
                            for param in (child_step.params if child_step else []):
                                if not param.required or param.category != "user_param" or not param.exposed_to_user:
                                    continue
                                pname = param.key or param.path
                                item_shaped = str(param.path or "").startswith("[") or bool(child_step and _looks_batch_step(child_step))
                                if pname not in item_props and (pname not in root_inputs or item_shaped):
                                    _capability_warning(
                                        internal_section,
                                        warnings,
                                        code="capability_loop_item_field_missing",
                                        message=f"Capability `{label}` foreach `{node_id}` 的条目 schema 未覆盖必填字段 `{pname}`",
                                        target={"kind": "capability_node", "capability": label, "node_id": node_id, "field": pname},
                                    )
                if not isinstance(node.get("steps"), list) and not any(
                    isinstance(n, dict) and n.get("type") == "call" for n in _iter_capability_nodes([node])
                ):
                    cap_warnings.append(f"Capability `{label}` foreach 节点 `{node_id}` 没有子步骤，运行期将退化为重复执行能力闭包")
            if node_type == "map":
                source = str(node.get("source") or "")
                target = str(node.get("target") or "")
                if not source or not target:
                    cap_errors.append(f"Capability `{label}` map 节点 `{node_id}` 缺少 source 或 target")
                elif not _capability_value_ref_exists(
                    source,
                    input_props=input_props,
                    cap_node_ids=cap_node_ids,
                    step_by_id=step_by_id,
                    cap_step_id_set=cap_step_id_set,
                ):
                    cap_errors.append(f"Capability `{label}` map 节点 `{node_id}` 来源 `{source}` 不存在")
                elif target.startswith("input."):
                    field = target.split(".", 1)[1].split(".", 1)[0]
                    if field not in input_props:
                        cap_errors.append(f"Capability `{label}` map 节点 `{node_id}` 目标输入 `{field}` 不存在")
                elif not target.startswith(("var.", "computed.", "loop.", "item.", "node.")):
                    head = target.split(".", 1)[0]
                    if head in cap_step_id_set:
                        tail = target.split(".", 1)[1] if "." in target else ""
                        if not _capability_step_param_exists(step_by_id.get(head), tail):
                            cap_errors.append(f"Capability `{label}` map 节点 `{node_id}` 目标 `{target}` 找不到接口字段")
                    else:
                        cap_warnings.append(f"Capability `{label}` map 节点 `{node_id}` 目标 `{target}` 无法静态确认，将按计算变量处理")
            if node_type == "return" and not (node.get("value") or node.get("from") or node.get("path")):
                hint = f"，可选来源: {return_sources[-1]}" if return_sources else "，当前能力没有有效 call 步骤可返回"
                cap_errors.append(f"Capability `{label}` return 节点 `{node_id}` 缺少返回来源{hint}")
            if node_type == "return" and node.get("from"):
                ref = str(node.get("from") or "")
                if ref and ref not in step_by_id and ref not in cap_node_ids and not ref.startswith(("input.", "var.", "node.")):
                    hint = f"；可选来源: {', '.join(return_sources[-3:])}" if return_sources else "；当前能力没有有效 call 步骤"
                    cap_errors.append(f"Capability `{label}` return 节点 `{node_id}` 引用的来源 `{ref}` 不存在{hint}")
                if ref == node_id:
                    hint = f"；可选来源: {return_sources[-1]}" if return_sources else ""
                    cap_errors.append(f"Capability `{label}` return 节点 `{node_id}` 不能引用自身作为返回来源{hint}")
        for idx, pre in enumerate(cap.preconditions or []):
            if not isinstance(pre, dict):
                cap_errors.append(f"Capability `{label}` preconditions[{idx}] 不是对象")
                continue
            expr = str(pre.get("check") or pre.get("condition") or pre.get("expr") or "")
            if not expr:
                cap_errors.append(f"Capability `{label}` preconditions[{idx}] 缺少 check/condition 表达式")
                continue
            input_refs = re.findall(r"\binput\.([a-zA-Z_][\w]*)", expr)
            bare_refs = []
            if re.fullmatch(r"[a-zA-Z_][\w]*\s*(?:==|!=|>=|<=|>|<).+", expr):
                bare_refs.append(re.split(r"==|!=|>=|<=|>|<", expr, 1)[0].strip())
            for ref in [*input_refs, *bare_refs]:
                if ref and ref not in input_props:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_precondition_input_missing",
                        message=f"Capability `{label}` 前置条件引用的输入 `{ref}` 不在 input_schema 中",
                        target={"kind": "capability_precondition", "capability": label, "index": idx, "input": ref},
                    )
        if cap.confirmed and cap.nodes and not cap.output_mapping and not has_return_node:
            cap_warnings.append(f"Capability `{label}` 已确认但没有 return 节点，外部调用只能拿到底层原始响应")

        if internal_section.get("errors"):
            capability_internal.setdefault("errors", []).extend(internal_section.get("errors") or [])

        if not cap.confirmed:
            errors.extend(cap_errors)
            warnings.extend(cap_warnings)
            capability_reports.append({
                "name": cap.name,
                "kind": cap.kind,
                "confirmed": cap.confirmed,
                "step_ids": node_step_ids,
                "request_keys": cap_request_keys,
                "nodes": cap.nodes,
                "errors": cap_errors,
                "warnings": cap_warnings,
            })
            capability_internal["capabilities"].append(internal_section)
            continue

        if cap.kind in {"submit", "submit_batch"} and not any((s.method or "").upper() in _WRITE_METHODS for s in cap_steps):
            cap_errors.append(f"Capability `{label}` 已确认提交能力，但没有关联写请求步骤")
        if cap.kind == "query_status" and not (cap_steps or cap.evidence):
            cap_errors.append(f"Capability `{label}` 已确认状态查询能力，但缺少读接口步骤或 request_graph 证据")
        if cap.kind == "list_options":
            fields = (((cap.input_schema or {}).get("properties") or {}).get("field") or {}).get("enum") or []
            if not fields and not cap.evidence:
                cap_errors.append(f"Capability `{label}` 已确认候选项查询能力，但缺少字段清单或候选源证据")
        errors.extend(cap_errors)
        warnings.extend(cap_warnings)
        capability_reports.append({
            "name": cap.name,
            "kind": cap.kind,
            "confirmed": cap.confirmed,
            "step_ids": node_step_ids,
            "request_keys": cap_request_keys,
            "nodes": cap.nodes,
            "errors": cap_errors,
            "warnings": cap_warnings,
        })
        capability_internal["capabilities"].append(internal_section)
    dedup_checked = list({r["request_key"]: r for r in checked_requests}.values())
    dedup_manual = list({r["request_key"]: r for r in checked_manual_requests}.values())
    cap_by_ref: dict[str, FlowCapability] = {}
    for cap in caps:
        for key in {cap.name, cap.capability_id}:
            if key:
                cap_by_ref[str(key)] = cap
    for relation in spec.capability_relations or []:
        from_key = str(relation.from_capability or "")
        to_key = str(relation.to_capability or "")
        from_cap = cap_by_ref.get(from_key)
        to_cap = cap_by_ref.get(to_key)
        requires_fields = _capability_relation_requires_fields(relation)
        from_type = _capability_field_type(from_cap, relation.from_output, direction="output") if from_cap and requires_fields else ""
        to_type = _capability_field_type(to_cap, relation.to_input, direction="input") if to_cap and requires_fields else ""
        compatible = not requires_fields or _capability_types_compatible(from_type, to_type)
        relation_entry = {
            "relation_id": relation.relation_id,
            "type": relation.type,
            "from_capability": relation.from_capability,
            "from_output": relation.from_output,
            "from_exists": from_cap is not None,
            "from_output_type": from_type,
            "to_capability": relation.to_capability,
            "to_input": relation.to_input,
            "to_exists": to_cap is not None,
            "to_input_type": to_type,
            "type_compatible": compatible,
            "requires_field_mapping": requires_fields,
        }
        capability_relations["relations"].append(relation_entry)
        if from_cap is None or to_cap is None:
            msg = f"Capability relation `{relation.relation_id}` 指向不存在的 from/to capability"
            if relation.confirmed:
                capability_relations.setdefault("errors", []).append({
                    "code": "capability_relation_endpoint_missing",
                    "message": msg,
                    "target": {"kind": "capability_relation", "relation_id": relation.relation_id},
                })
                errors.append(msg)
            else:
                _capability_warning(
                    capability_relations,
                    warnings,
                    code="capability_relation_endpoint_missing",
                    message=msg,
                    target={"kind": "capability_relation", "relation_id": relation.relation_id},
                )
        elif requires_fields and (not from_type or not to_type):
            msg = f"Capability relation `{relation.relation_id}` 的 output/input 字段缺少可解析类型"
            if relation.confirmed:
                capability_relations.setdefault("errors", []).append({
                    "code": "capability_relation_field_missing",
                    "message": msg,
                    "target": {"kind": "capability_relation", "relation_id": relation.relation_id},
                })
                errors.append(msg)
            else:
                _capability_warning(
                    capability_relations,
                    warnings,
                    code="capability_relation_field_missing",
                    message=msg,
                    target={"kind": "capability_relation", "relation_id": relation.relation_id},
                )
        elif requires_fields and not compatible:
            msg = f"Capability relation `{relation.relation_id}` output/input 类型不兼容: {from_type} -> {to_type}"
            if relation.confirmed:
                capability_relations.setdefault("errors", []).append({
                    "code": "capability_relation_type_mismatch",
                    "message": msg,
                    "target": {"kind": "capability_relation", "relation_id": relation.relation_id},
                })
                errors.append(msg)
            else:
                _capability_warning(
                    capability_relations,
                    warnings,
                    code="capability_relation_type_mismatch",
                    message=msg,
                    target={"kind": "capability_relation", "relation_id": relation.relation_id},
                )
    if caps and not any(c.confirmed for c in caps):
        message = "Skill 尚无已确认能力；请确认至少一个能力后再发布"
        skill_level.setdefault("errors", []).append({
            "code": "no_confirmed_capability",
            "message": message,
            "target": {"kind": "flow", "flow_id": spec.flow_id},
        })
        errors.append(message)
    confirmed_caps = [c for c in caps if c.confirmed]
    strict_skill_level = bool((spec.meta or {}).get("publish_gate") or (spec.meta or {}).get("strict_skill_level"))
    if confirmed_caps:
        skill_issues: list[tuple[str, str]] = []
        if not str(spec.business_description or "").strip():
            skill_issues.append(("skill_description_missing", "Skill 缺少面向调用方的整体说明"))
        if len(confirmed_caps) > 1 and not (spec.capability_relations or (spec.meta or {}).get("default_capability_order")):
            skill_issues.append(("skill_default_call_order_missing", "Skill 有多个 confirmed capability，但缺少默认调用顺序或能力关系"))
        failure_text = " ".join([
            str((spec.meta or {}).get("failure_handling") or ""),
            str(spec.business_description or ""),
            *[str(x) for cap in confirmed_caps for x in (cap.skill_responsibilities or [])],
            *[str(x) for cap in confirmed_caps for x in (cap.preconditions or [])],
        ])
        if not re.search(r"失败|错误|异常|重试|failed|error|exception", failure_text, re.I):
            skill_issues.append(("skill_failure_handling_missing", "Skill 缺少失败处理或异常边界说明"))
        for code, message in skill_issues:
            target = {"kind": "flow", "flow_id": spec.flow_id}
            if strict_skill_level:
                entry = {"code": code, "message": message, "target": target}
                skill_level.setdefault("errors", []).append(entry)
                errors.append(message)
            else:
                _capability_warning(skill_level, warnings, code=code, message=message, target=target)
    capability_internal["passed"] = not capability_internal["errors"]
    capability_relations["passed"] = not capability_relations["errors"]
    skill_level["passed"] = not skill_level["errors"]
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "capabilities": capability_reports,
        "checked_requests": dedup_checked,
        "checked_manual_requests": dedup_manual,
        "unused_high_confidence_requests": high_conf_unused,
        "capability_internal": capability_internal,
        "capability_relations": capability_relations,
        "skill_level": skill_level,
    }


def _validate_flow_capabilities(spec: FlowSpec) -> tuple[list[str], list[str]]:
    report = _capability_validation_report(spec)
    return list(report.get("errors") or []), list(report.get("warnings") or [])


def _review_id(item_type: str, target: dict[str, Any]) -> str:
    parts = [
        item_type,
        str(target.get("step_id") or ""),
        str(target.get("path") or ""),
        str(target.get("link_id") or ""),
        str(target.get("request_index") or ""),
    ]
    raw = "|".join(parts)
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower()
    return f"review_{safe[:96]}" if safe else f"review_{item_type}"


def _review_item(
    item_type: str,
    *,
    severity: str,
    title: str,
    target: dict[str, Any],
    current_guess: str = "",
    suggested_action: str = "",
    reason: str = "",
    confidence: float = 0.0,
) -> ReviewItem:
    return ReviewItem(
        id=_review_id(item_type, target),
        type=item_type,
        severity=severity,
        title=title,
        target=target,
        current_guess=current_guess,
        suggested_action=suggested_action,
        reason=reason,
        confidence=confidence,
    )


_FLOW_PATH_MISSING = object()


def _flow_path_tokens(path) -> list:
    if isinstance(path, (list, tuple)):
        return list(path)
    out: list = []
    for seg in str(path or "").split("."):
        bits = seg.split("[")
        if bits[0]:
            out.append(bits[0])
        for idx in bits[1:]:
            try:
                out.append(int(idx.rstrip("]")))
            except ValueError:
                out.append(idx.rstrip("]"))
    return out


def _flow_path_lookup(node, path):
    cur = node
    for key in _flow_path_tokens(path):
        try:
            cur = cur[key]
        except Exception:  # noqa: BLE001
            return _FLOW_PATH_MISSING
    return cur


def build_review_items(spec: FlowSpec) -> list[ReviewItem]:
    """把 FlowSpec 中的低置信/高风险判断整理成人工确认项。"""
    items: list[ReviewItem] = []
    active_step_ids = _active_capability_step_ids(spec)
    visible_steps = [
        step for step in spec.steps
        if active_step_ids is None or step.step_id in active_step_ids
    ]
    step_ids = {s.step_id for s in visible_steps}
    steps_by_id = {s.step_id: s for s in visible_steps}
    visible_request_indexes = {
        str(step.source_meta.get("request_index"))
        for step in visible_steps
        if step.source_meta.get("request_index") is not None
    }
    visible_request_paths = {
        _request_path({"path": step.path or step.url})
        for step in visible_steps
        if step.path or step.url
    }
    confirmed_dependency_sources = {
        link.source_step_id for link in spec.links
        if link.confirmed and link.source_step_id in step_ids and link.target_step_id in step_ids
    }

    for st in visible_steps:
        if st.risk_level == "L4":
            items.append(_review_item(
                "dangerous_step",
                severity="high",
                title=f"确认高风险步骤 {st.name or st.path or st.step_id}",
                target={"kind": "step", "step_id": st.step_id, "path": st.path},
                current_guess=st.semantic_role or st.risk_level,
                suggested_action="confirm_step_risk",
                reason="该步骤被识别为高风险写操作，发布前需要人工确认是否允许生成 Skill",
                confidence=float(st.source_meta.get("confidence") or 0.0),
            ))

        for p in st.params:
            target = {
                "kind": "param",
                "step_id": st.step_id,
                "step_name": st.name,
                "path": p.path,
                "key": p.key,
            }
            guess = f"{p.category}/{p.source_kind}"

            runtime_unknown = p.category == "runtime_var" and p.source_kind == "unknown"

            if p.need_human_confirm and not runtime_unknown:
                severity = "high" if p.category == "runtime_var" and p.source_kind == "unknown" else "medium"
                items.append(_review_item(
                    "field_category",
                    severity=severity,
                    title=f"确认字段 {p.path} 的分类和来源",
                    target=target,
                    current_guess=guess,
                    suggested_action="confirm_field_source",
                    reason=p.reason or "该字段分类由规则推断，建议人工确认",
                    confidence=p.confidence,
                ))

            if runtime_unknown:
                items.append(_review_item(
                    "runtime_var_source",
                    severity="high",
                    title=f"补充字段 {p.path} 的运行期来源",
                    target=target,
                    current_guess=guess,
                    suggested_action="bind_runtime_source",
                    reason="运行期变量不能使用录制旧值；请在字段页绑定上游响应，或改为当前用户、系统时间、页面上下文",
                    confidence=p.confidence,
                ))

            if p.category == "runtime_var" and p.source_kind != "unknown" and not p.source:
                items.append(_review_item(
                    "runtime_var_missing_source",
                    severity="high",
                    title=f"补充字段 {p.path} 的 source 详情",
                    target=target,
                    current_guess=guess,
                    suggested_action="bind_runtime_source",
                    reason="字段已判为运行期变量，但缺少可执行的 source 描述",
                    confidence=p.confidence,
                ))

            if p.category == "system_const" and p.exposed_to_user:
                items.append(_review_item(
                    "system_const_exposed",
                    severity="high",
                    title=f"隐藏系统常量 {p.path}",
                    target=target,
                    current_guess=guess,
                    suggested_action="hide_system_const",
                    reason="系统常量不应作为普通 Skill 入参暴露给 agent 或最终用户",
                    confidence=p.confidence,
                ))

    for lk in spec.links:
        if active_step_ids is not None and not (
            lk.source_step_id in active_step_ids or lk.target_step_id in active_step_ids
        ):
            continue
        source_step = steps_by_id.get(lk.source_step_id)
        target_step = steps_by_id.get(lk.target_step_id)
        source_label = f"{source_step.name or source_step.path or source_step.url}" if source_step else lk.source_step_id
        target_label = f"{target_step.name or target_step.path or target_step.url}" if target_step else lk.target_step_id
        link_label = f"{source_label}.{lk.source_path} -> {target_label}.{lk.target_path}"
        target = {
            "kind": "link",
            "link_id": lk.link_id,
            "source_step_id": lk.source_step_id,
            "source_path": lk.source_path,
            "target_step_id": lk.target_step_id,
            "target_path": lk.target_path,
        }
        if lk.source_step_id not in step_ids or lk.target_step_id not in step_ids:
            items.append(_review_item(
                "broken_link",
                severity="high",
                title=f"修复断开的接口依赖 {link_label}",
                target=target,
                current_guess="invalid_link",
                suggested_action="fix_or_remove_link",
                reason="该 link 指向不存在的步骤，执行计划无法可靠生成",
                confidence=lk.confidence,
            ))
            continue

        source_path = lk.source_tokens or lk.source_path
        if source_step and source_step.response_json is not None and _flow_path_lookup(source_step.response_json, source_path) is _FLOW_PATH_MISSING:
            items.append(_review_item(
                "link_source_missing",
                severity="high",
                title=f"修复接口依赖来源 {source_label}.{lk.source_path}",
                target=target,
                current_guess="missing_source_path",
                suggested_action="fix_link_source",
                reason="该 link 的 source_path 在上游响应样例里不存在，运行期无法取到要注入的值",
                confidence=lk.confidence,
            ))

        target_path = _strip_body_prefix(lk.target_path)
        if target_step and target_path and not any(p.path == target_path or p.path == lk.target_path for p in target_step.params):
            items.append(_review_item(
                "link_target_missing",
                severity="high",
                title=f"修复接口依赖目标 {target_label}.{lk.target_path}",
                target=target,
                current_guess="missing_target_path",
                suggested_action="fix_link_target",
                reason="该 link 的 target_path 不在目标步骤字段中，运行期可能无法注入",
                confidence=lk.confidence,
            ))

        if not lk.confirmed:
            items.append(_review_item(
                "link_confirmation",
                severity="high",
                title=f"确认接口依赖 {link_label}",
                target=target,
                current_guess="previous_response",
                suggested_action="confirm_link",
                reason=lk.reason or "该 link 由响应值与请求值匹配自动生成，需要人工确认",
                confidence=lk.confidence,
            ))

    for role in spec.meta.get("request_roles") or []:
        role_index = str(role.get("index")) if role.get("index") is not None else ""
        role_path = _request_path({"path": str(role.get("path") or role.get("url") or "")})
        matched_step = next((
            step for step in visible_steps
            if (
                role_index
                and str(step.source_meta.get("request_index")) == role_index
            ) or (
                role_path
                and _request_path({"path": step.path or step.url}) == role_path
            )
        ), None)
        role_is_active = bool(
            matched_step
            or (role_index and role_index in visible_request_indexes)
            or (role_path and role_path in visible_request_paths)
        )
        confidence = float(role.get("confidence") or 0.0)
        needs_role_confirmation = bool(
            role.get("keep")
            and role.get("role") in {"business_get", "read_context"}
            and role_is_active
            and confidence < 0.9
            and not bool(matched_step and matched_step.source_meta.get("manual_added"))
            and not bool(matched_step and matched_step.source_meta.get("control_preflight_for_write"))
            and not bool(matched_step and matched_step.step_id in confirmed_dependency_sources)
        )
        if needs_role_confirmation:
            items.append(_review_item(
                "request_role",
                severity="medium",
                title=f"确认前置接口保留: {role.get('path') or role.get('url')}",
                target={
                    "kind": "request_role",
                    "request_index": role.get("index"),
                    "method": role.get("method"),
                    "path": role.get("path") or role.get("url"),
                },
                current_guess=str(role.get("role") or ""),
                suggested_action="confirm_request_role",
                reason=str(role.get("reason") or "该读接口被自动保留为流程前置步骤"),
                confidence=confidence,
            ))

    if visible_steps and not flow_spec_user_params(spec):
        items.append(_review_item(
            "no_user_param",
            severity="low",
            title="确认 Skill 是否不需要用户输入",
            target={"kind": "flow", "flow_id": spec.flow_id},
            current_guess="no_user_param",
            suggested_action="confirm_or_expose_param",
            reason="当前 FlowSpec 没有 user_param，发布后的 Skill 不会要求用户填写业务参数",
        ))

    if visible_steps and not any((st.success_rule for st in visible_steps)):
        items.append(_review_item(
            "success_rule_missing",
            severity="medium",
            title="补充成功判断规则",
            target={"kind": "flow", "flow_id": spec.flow_id},
            current_guess="missing_success_rule",
            suggested_action="add_success_rule",
            reason="未识别到明确 success_rule，运行期只能使用通用成功判断",
        ))

    deduped: dict[str, ReviewItem] = {}
    for item in items:
        existing = deduped.get(item.id)
        if existing is None or _severity_rank(item.severity) > _severity_rank(existing.severity):
            deduped[item.id] = item
    return list(deduped.values())


def _severity_rank(severity: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(severity, 0)


def _param_dedupe_key(param: ParamField) -> tuple[str, str]:
    path = _strip_body_prefix(str(param.path or "")).strip()
    key = str(param.key or param.label or "").strip()
    return (path, key if not path else "")


def _enum_sources_compatible(dst: ParamField, src: ParamField) -> bool:
    if dst.source_kind != src.source_kind:
        return False
    dst_source = dst.source or {}
    src_source = src.source or {}
    if dst.source_kind == "api_option":
        return bool(dst_source.get("source_url")) and (
            _request_path({"url": str(dst_source.get("source_url") or "")})
            == _request_path({"url": str(src_source.get("source_url") or "")})
        )
    if dst.source_kind == "page_enum":
        return bool(
            dst_source.get("enum_confirmed") is True
            and src_source.get("enum_confirmed") is True
            and (dst.key or dst.label or dst.path) == (src.key or src.label or src.path)
        )
    return dst.source_kind in {"manual_enum", "static_enum", "form_option"}


def _refresh_param_enum_description(param: ParamField) -> None:
    base_description = _strip_option_descriptions(param.description)
    base_reason = _strip_option_descriptions(param.reason)
    detail = _enum_options_description(param.source_kind, param.enum_options, param.enum_value_map)
    param.description = _upsert_option_description(base_description, detail) or None
    param.reason = _upsert_option_description(base_reason, detail)


def _merge_enum_values(dst: ParamField, src: ParamField) -> None:
    if not _enum_sources_compatible(dst, src):
        _refresh_param_enum_description(dst)
        return
    if not dst.enum_options and src.enum_options:
        dst.enum_options = list(src.enum_options)
    elif dst.enum_options and src.enum_options:
        seen = {json.dumps(x, ensure_ascii=False, sort_keys=True, default=str) for x in dst.enum_options}
        for opt in src.enum_options:
            marker = json.dumps(opt, ensure_ascii=False, sort_keys=True, default=str)
            if marker not in seen:
                dst.enum_options.append(opt)
                seen.add(marker)
    if not dst.enum_value_map and src.enum_value_map:
        dst.enum_value_map = dict(src.enum_value_map)
    elif dst.enum_value_map and src.enum_value_map:
        dst.enum_value_map = {**src.enum_value_map, **dst.enum_value_map}
    _refresh_param_enum_description(dst)


def _param_quality(param: ParamField) -> tuple[int, int, float]:
    source_score = 2 if param.source_kind not in {"", "unknown"} else 0
    if param.source_kind in {"api_option", "page_enum", "static_enum", "manual_enum", "form_option"}:
        source_score += 2
    manual_score = 1 if param.name_source in {"manual", "llm", "assignee", "sample"} else 0
    return (source_score, manual_score, float(param.confidence or 0.0))


def _dedupe_step_params(step: FlowStep) -> None:
    if not step.params:
        return
    by_key: dict[tuple[str, str], ParamField] = {}
    order: list[tuple[str, str]] = []
    for param in step.params:
        key = _param_dedupe_key(param)
        if not key[0] and not key[1]:
            key = (param.path, param.key)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = param
            order.append(key)
            continue
        keep, drop = (param, existing) if _param_quality(param) > _param_quality(existing) else (existing, param)
        _merge_enum_values(keep, drop)
        by_key[key] = keep
    step.params = [by_key[key] for key in order if key in by_key]


def refresh_review_items(spec: FlowSpec) -> FlowSpec:
    """重建 review_items，并保留同 id 项的已解决状态。

    ID 是稳定 hash(target)，所以同一字段/同一依赖在重建前后 ID 不变，
    用户的 resolved 标记会随 ID 一起被复用，告警不会因为字段重渲染而复活。
    """
    for step in spec.steps:
        _dedupe_step_params(step)
    old_resolved: dict[str, bool] = {}
    for item in spec.review_items:
        # id 已是 target 的稳定 hash；同字段前后 ID 一致，resolved 跟着保留。
        old_resolved.setdefault(item.id, item.resolved)
    spec.review_items = build_review_items(spec)
    for item in spec.review_items:
        if item.id in old_resolved:
            item.resolved = old_resolved[item.id]
    return spec


def _flow_fingerprint(spec: FlowSpec) -> str:
    payload = spec.model_dump(exclude={"meta", "review_items"})
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def flow_spec_fingerprint(spec: FlowSpec) -> str:
    return _flow_fingerprint(spec)


def append_flow_version(
    spec: FlowSpec,
    action: str,
    *,
    reason: str = "",
    actor: str = "system",
) -> FlowSpec:
    """在 FlowSpec.meta 中追加轻量版本记录。"""
    sync_flow_spec_models(spec, prefer_request_facts=False)
    meta = dict(spec.meta or {})
    versions = list(meta.get("versions") or [])
    current = max([int(v.get("version") or 0) for v in versions] or [0])
    entry = {
        "version": current + 1,
        "action": action,
        "reason": reason,
        "actor": actor,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": _flow_fingerprint(spec),
        "summary": {
            "steps": len(spec.steps),
            "links": len(spec.links),
            "capabilities": len(spec.capabilities or []),
            "user_params": len(flow_spec_user_params(spec)),
            "review_items": len(spec.review_items),
            "risk_level": spec.risk_level,
        },
    }
    versions.append(entry)
    meta["versions"] = versions[-30:]
    meta["current_version"] = entry["version"]
    spec.meta = meta
    return spec


def ensure_flow_version(spec: FlowSpec, action: str, *, reason: str = "") -> FlowSpec:
    if spec.meta.get("versions"):
        return spec
    return append_flow_version(spec, action, reason=reason)


def flow_spec_to_summary(spec: FlowSpec) -> dict:
    spec = sync_flow_spec_models(spec.model_copy(deep=True), prefer_request_facts=False)
    return {
        "flow_id": spec.flow_id,
        "title": spec.title,
        "recording_mode": spec.recording_mode,
        "diagnostic_count": len(spec.diagnostics),
        "step_count": len(spec.steps),
        "link_count": len(spec.links),
        "capability_count": len(spec.capabilities or []),
        "review_count": len(spec.review_items),
        "current_version": spec.meta.get("current_version"),
        "risk_level": spec.risk_level,
        "schema_version": spec.schema_version,
        "capabilities": [
            {
                "name": c.name,
                "title": c.title,
                "kind": c.kind,
                "step_ids": c.step_ids,
                "confirmed": c.confirmed,
                "requires_human_confirm": c.requires_human_confirm,
                "confidence": c.confidence,
            }
            for c in (spec.capabilities or [])
        ],
        "steps": [
            {
                "step_id": s.step_id,
                "name": s.name,
                "method": s.method,
                "path": s.path,
                "risk_level": s.risk_level,
                "param_count": len(s.params),
                "select_count": len(s.selects),
                "identity_count": len(s.identity),
            }
            for s in spec.steps
        ],
        "links": [
            {
                "link_id": l.link_id,
                "source_step_id": l.source_step_id,
                "source_path": l.source_path,
                "target_step_id": l.target_step_id,
                "target_path": l.target_path,
                "confirmed": l.confirmed,
                "confidence": l.confidence,
            }
            for l in spec.links
        ],
        "meta": spec.meta,
    }


# ─────────── P0-0: FlowSpec → 可发布 api_request ───────────
def _clean_path_prefix(path: str, prefix: str) -> str:
    if not path:
        return ""
    return path[len(prefix):] if path.startswith(prefix) else path


def _step_samples(step: FlowStep) -> dict:
    samples = dict(step.sample_inputs or {})
    for p in step.params:
        if p.key and p.value not in (None, ""):
            samples[p.key] = p.value
    return samples


def _step_param_map(step: FlowStep) -> dict[str, str]:
    """只把 user_param 暴露给 Skill 调用者；常量/运行期变量保留在流程内部。"""
    out: dict[str, str] = {}
    for p in step.params:
        if p.category != "user_param":
            continue
        key = (p.key or "").strip()
        if key:
            out[p.path] = key
    return out


def _runtime_param_publish_error(param: ParamField) -> str | None:
    """系统化:不要用 runtime_var/unknown 一刀切拒绝发布——启发式错误太多,
    真实场景里很多字段只是因为 samples 没传到位被误判。让"完全无来源 + 无来源 fall-back"才硬拒,
    其余的 runtime_var 字段(尤其 user_input 误导)走 warning + 前端 UI 兜底确认。"""
    if param.category != "runtime_var":
        return None
    if param.source_kind == "previous_response":
        if param.source.get("step_id") and (param.source.get("response_path") or param.source.get("path")):
            return None
        return f"字段 `{param.path}` 是上游响应变量，但缺少来源步骤或响应字段"
    if param.source_kind == "request_header":
        return None if param.source.get("header") else f"字段 `{param.path}` 是请求头变量，但缺少请求头名称"
    if param.source_kind == "system_time":
        return None
    if param.source_kind == "system_generated":
        strategy = str((param.source or {}).get("strategy") or "")
        return None if strategy in {"uuid", "random_string", "random_number"} else (
            f"字段 `{param.path}` 是系统生成值，但缺少有效生成策略"
        )
    if param.source_kind == "computed":
        source = param.source or {}
        if source.get("strategy") == "date_span_days_json" and source.get("start_field") and source.get("end_field"):
            return None
        return f"字段 `{param.path}` 是系统计算值，但缺少可执行计算规则"
    if param.source_kind in {"api_option", "page_enum", "static_enum", "manual_enum", "form_option"}:
        return None
    if param.source_kind == "current_user":
        return None
    if param.source_kind == "page_context":
        return None if param.source.get("context_key") else (
            f"字段 `{param.path}` 使用调用上下文，但缺少 context_key"
        )
    # 系统化:不再因 runtime_var/unknown 单一硬拒;允许发布,把校验交给前端 review_items + 自我检查(运行时)。
    # 用户在 UI 上能改 category 即可消除歧义;同时保留 review_items 提示(need_human_confirm)。
    # 历史:这条规则最初是为防止用户「运行时被冻死的录制值」漏掉,但实际启发式经常误判
    # (13 位毫秒/dict 字段名/审批人码),导致完全可发布的 spec 被截拦。现调整为「任何有 value 的 runtime_var 都放行」，
    # 仅在完全没有 source 字典 / 完全无可执行来源时才报。
    if param.value not in (None, "") and param.source_kind == "unknown":
        return None
    if not param.source and param.value in (None, ""):
        return f"字段 `{param.path}` 是运行期变量，但缺少可执行来源"
    return None


def _field_source_contract_error(param: ParamField) -> str | None:
    """校验类型之外的分类/来源语义，阻止不可执行的组合进入 Skill。"""
    allowed = {
        "user_param": {"unknown", "user_input", "api_option", "page_enum", "static_enum", "manual_enum", "form_option"},
        "runtime_var": {
            "unknown", "previous_response", "request_header", "current_user", "system_time", "system_generated", "computed", "page_context",
            "api_option", "page_enum", "static_enum", "manual_enum", "form_option",
        },
        "system_const": {"constant"},
    }
    category = param.category or "user_param"
    source_kind = param.source_kind or "unknown"
    if category not in allowed:
        return f"字段 `{param.path}` 分类 `{category}` 无效"
    if source_kind not in allowed[category]:
        return f"字段 `{param.path}` 的分类 `{category}` 与来源 `{source_kind}` 不兼容"
    if source_kind == "page_context" and not (param.source or {}).get("context_key"):
        return f"字段 `{param.path}` 的调用上下文缺少 context_key"
    if source_kind == "request_header" and not (param.source or {}).get("header"):
        return f"字段 `{param.path}` 的请求头来源缺少 header 名称"
    if source_kind == "system_generated" and str((param.source or {}).get("strategy") or "") not in {
        "uuid", "random_string", "random_number",
    }:
        return f"字段 `{param.path}` 的系统生成值缺少有效生成策略"
    if source_kind == "computed" and not (
        (param.source or {}).get("strategy") == "date_span_days_json"
        and (param.source or {}).get("start_field")
        and (param.source or {}).get("end_field")
    ):
        return f"字段 `{param.path}` 的系统计算值缺少可执行规则"
    if source_kind == "previous_response" and not (
        (param.source or {}).get("step_id")
        and ((param.source or {}).get("response_path") or (param.source or {}).get("path"))
    ):
        return f"字段 `{param.path}` 的上游响应来源缺少步骤或响应字段"
    return None


def _query_key_from_param(param: ParamField) -> str:
    if param.path.startswith("query."):
        return param.path[len("query."):]
    return param.key


def _flow_step_query_template(
    step: FlowStep,
) -> tuple[dict[str, Any], list[str], dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    query_template: dict[str, Any] = {}
    params: list[str] = []
    samples: dict[str, Any] = {}
    field_types: dict[str, str] = {}
    runtime_fields: list[dict[str, Any]] = []
    for p in step.params:
        if not p.path.startswith("query."):
            continue
        query_key = _query_key_from_param(p)
        if not query_key:
            continue
        if p.category == "user_param":
            name = (p.key or query_key).strip()
            if not name:
                continue
            query_template[query_key] = "{{" + name + "}}"
            if name not in params:
                params.append(name)
            if p.value not in (None, ""):
                samples[name] = p.value
            field_types[name] = p.type
        elif p.category == "runtime_var":
            # 运行期变量不是最终用户参数。GET query 里先保留录制值，若有 FlowLink 指向 query.xxx，
            # execute_api_workflow 会在运行期用上游响应覆盖；没有可靠来源时由 review_items 提醒人工确认。
            if p.source_kind in {"system_time", "system_generated", "computed"}:
                runtime_name = f"__dano_runtime_{hashlib.sha1((step.step_id + ':' + p.path).encode()).hexdigest()[:10]}"
                if p.source_kind == "computed":
                    runtime_field = {"name": runtime_name, **dict(p.source or {})}
                    strategy = str(runtime_field.get("strategy") or "")
                else:
                    strategy = ("now_date" if p.type == "date" else "now_iso") if p.source_kind == "system_time" and p.type in {"string", "date", "datetime"} else (
                        "now_ms" if p.source_kind == "system_time" else str((p.source or {}).get("strategy") or "uuid")
                    )
                    runtime_field = {"name": runtime_name, "kind": strategy}
                query_template[query_key] = "{{" + runtime_name + "}}"
                runtime_field["kind"] = strategy
                runtime_fields.append(runtime_field)
            else:
                query_template[query_key] = p.value
        else:
            query_template[query_key] = p.value
    return query_template, params, samples, field_types, runtime_fields


def flow_spec_user_params(spec: FlowSpec) -> list[str]:
    names: list[str] = []
    active_step_ids = _active_capability_step_ids(spec)
    option_source_ids = _option_source_step_ids(spec)
    for st in spec.steps:
        if active_step_ids is not None and st.step_id not in active_step_ids:
            continue
        for name in _step_param_map(st).values():
            if name not in names:
                names.append(name)
    return names


def flow_spec_required_params(spec: FlowSpec) -> list[str]:
    names: list[str] = []
    active_step_ids = _active_capability_step_ids(spec)
    for st in spec.steps:
        if active_step_ids is not None and st.step_id not in active_step_ids:
            continue
        for p in st.params:
            if p.category != "user_param" or not p.required:
                continue
            key = (p.key or "").strip()
            if key and key not in names:
                names.append(key)
    return names


def _needs_llm_field_name(param: ParamField) -> bool:
    key = (param.key or "").strip()
    if not key or param.category != "user_param" or not param.exposed_to_user:
        return False
    if (param.name_source or "auto") not in {"", "auto"}:
        return False
    if re.search(r"[\u4e00-\u9fff]", key):
        return False
    if looks_internal_param_name(key):
        return True
    last = (param.path or "").split(".")[-1].split("[")[0]
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)) and key == last


def llm_field_name_candidates(spec: FlowSpec) -> list[dict[str, str]]:
    """LLM 字段命名输入：只给机器名、类型和路径，不带录制值。"""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for st in spec.steps:
        for p in st.params:
            if not _needs_llm_field_name(p):
                continue
            item = {"key": p.key, "suggest_name": p.key, "type": p.type, "path": p.path}
            sig = (item["key"], item["path"])
            if sig not in seen:
                seen.add(sig)
                out.append(item)
    return out


def apply_llm_field_names(spec: FlowSpec, names: dict[str, str] | None) -> FlowSpec:
    """把 LLM 推荐字段名应用到 FlowSpec。

    只改仍是自动机器名的用户参数；手动名、页面标签名、审批人名和已确认来源不覆盖。
    """
    if not names:
        return spec
    new_spec = spec.model_copy(deep=True)
    changed = False
    for st in new_spec.steps:
        used = {p.key for p in st.params}
        for p in st.params:
            if not _needs_llm_field_name(p):
                continue
            proposed = str(names.get(p.key) or names.get(p.path) or "").strip()
            if not proposed or proposed == p.key or proposed in used:
                continue
            old_key = p.key
            used.discard(old_key)
            used.add(proposed)
            p.key = proposed
            p.label = proposed
            p.name_source = "llm"
            if old_key in st.sample_inputs:
                st.sample_inputs[proposed] = st.sample_inputs.pop(old_key)
            elif p.value not in (None, ""):
                st.sample_inputs[proposed] = p.value
            for sb in st.selects:
                if sb.path == p.path or sb.param == old_key:
                    sb.param = proposed
            changed = True
    if not changed:
        return spec
    return append_flow_version(
        refresh_review_items(new_spec),
        "field_naming",
        reason="LLM 补充机器字段业务名",
    )


def apply_flow_publish_selection(
    spec: FlowSpec,
    param_map: dict[str, str] | None,
    *,
    selected_scope_paths: set[str] | None = None,
) -> FlowSpec:
    """把旧字段勾选表同步到 FlowSpec。

    这让当前前端的字段勾选/重命名不会被 FlowSpec 发布路径绕过。只在传入的
    selected_scope_paths 范围内把未勾选字段降为 system_const，避免误伤其它步骤。
    """
    if not param_map and selected_scope_paths is None:
        return spec
    new_spec = spec.model_copy(deep=True)
    clean_map = {k: v.strip() for k, v in (param_map or {}).items() if v and v.strip()}
    for st in new_spec.steps:
        for p in st.params:
            if p.path in clean_map:
                old_key = p.key
                p.key = clean_map[p.path]
                p.label = p.key
                p.category = "user_param"
                p.exposed_to_user = True
                p.editable = True
                if p.source_kind in ("unknown", "constant"):
                    p.source_kind = "user_input"
                    p.source = {"kind": "publish_selection", "path": p.path}
                    p.reason = "用户在发布字段表中勾选该字段，作为 Skill 输入参数"
                    p.need_human_confirm = False
                p.name_source = "manual"
                if old_key in st.sample_inputs:
                    st.sample_inputs[p.key] = st.sample_inputs.pop(old_key)
                if p.value not in (None, ""):
                    st.sample_inputs[p.key] = p.value
                for sb in st.selects:
                    if sb.path == p.path or sb.param == old_key:
                        sb.param = p.key
            elif selected_scope_paths is not None and p.path in selected_scope_paths:
                if p.category == "user_param":
                    p.category = "system_const"
                    p.source_kind = "constant"
                    p.source = {"kind": "publish_selection", "path": p.path}
                    p.exposed_to_user = False
                    p.reason = "用户未在发布字段表中勾选该字段，作为录制结构内的固定值保留"
                    p.need_human_confirm = False
    return append_flow_version(
        refresh_review_items(new_spec),
        "publish_selection",
        reason="同步发布字段选择",
    )


def _flow_step_to_api_step(step: FlowStep) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    runtime_errors = [err for p in step.params if (err := _runtime_param_publish_error(p))]
    if runtime_errors:
        return None, runtime_errors
    if not step.body_source:
        if step.method.upper() == "GET":
            query_template, params, samples, field_types, runtime_fields = _flow_step_query_template(step)
            apir = {
                "step_id": step.step_id,
                "step_name": step.name,
                "method": "GET",
                "url": step.url or step.path,
                "path": step.path,
                "content_type": step.content_type,
                "body_template": None,
                "query_template": query_template,
                "params": params,
                "sample_inputs": samples,
                "auth_headers": extract_auth_headers(step.headers),
                "field_types": field_types,
                "selects": [],
                "identity": [],
                "system_values": [],
                "runtime_fields": runtime_fields,
            }
            if step.success_rule:
                apir["success_rule"] = step.success_rule
            if step.fact_check:
                apir["fact_check"] = step.fact_check
            if step.response_json is not None:
                apir["response_json"] = step.response_json
            return apir, errors
        errors.append(f"步骤 `{step.name or step.path or step.step_id}` 缺少请求体，当前发布器暂不支持无 body 的步骤")
        return None, errors
    req = {
        "method": step.method,
        "url": step.url or step.path,
        "post_data": step.body_source,
        "content_type": step.content_type,
        "headers": step.headers,
    }
    if step.source_meta.get("response_status") is not None:
        req["response_status"] = step.source_meta.get("response_status")
    if step.response_json is not None:
        req["response_json"] = step.response_json
    param_map = _step_param_map(step)
    current_key_by_path = {p.path: p.key for p in step.params}
    selects = []
    select_paths = set()
    for s in step.selects:
        item = s.model_dump(exclude_none=True)
        if s.path in current_key_by_path:
            item["param"] = current_key_by_path[s.path]
        selects.append(item)
        if s.path:
            select_paths.add(s.path)
    for p in step.params:
        if (
            p.category == "user_param"
            and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
            and p.enum_options
            and p.path not in select_paths
        ):
            selects.append({
                "param": p.key,
                "path": p.path,
                "source_url": "",
                "value_key": "",
                "label_key": "",
                "options": list(p.enum_options),
                "count": len(p.enum_options),
                "option_map": dict(p.enum_value_map or _enum_option_map_from_options(p.enum_options)),
                "enum_source": "manual",
                "enum_confirmed": True,
            })
            select_paths.add(p.path)
    apir = build_api_request(
        req,
        param_map,
        selects=selects,
        identity=[
            *[i.model_dump(exclude_none=True) for i in step.identity],
            *[
                {"path": p.path, "source": f"requestHeader:{p.source.get('header')}", "value": p.value}
                for p in step.params
                if p.category == "runtime_var" and p.source_kind == "request_header" and p.source.get("header")
            ],
        ],
        typed=_step_samples(step),
    )
    if apir is None:
        errors.append(f"步骤 `{step.name or step.path or step.step_id}` 请求体无法解析，不能发布为请求型 Skill")
        return None, errors
    explicit_system_values = [item.model_dump(exclude_none=True) for item in step.system_values]
    for p in step.params:
        if p.category != "runtime_var" or p.source_kind not in {"system_time", "system_generated"}:
            continue
        kind = "now_ms"
        if p.source_kind == "system_generated":
            kind = str((p.source or {}).get("strategy") or "uuid")
        elif p.type in {"string", "date", "datetime"}:
            kind = "now_date" if p.type == "date" else "now_iso"
        explicit_system_values.append({"path": _strip_body_prefix(p.path), "kind": kind})
    if explicit_system_values:
        deduped_system_values: dict[tuple[str, str], dict[str, Any]] = {}
        for item in [*(apir.get("system_values") or []), *explicit_system_values]:
            deduped_system_values[(str(item.get("path") or ""), str(item.get("kind") or ""))] = item
        apir["system_values"] = list(deduped_system_values.values())
    apir["step_id"] = step.step_id
    apir["step_name"] = step.name
    if step.success_rule:
        apir["success_rule"] = step.success_rule
    if step.fact_check:
        apir["fact_check"] = step.fact_check
    return apir, errors


def _find_capability_by_ref(spec: FlowSpec, capability: str | FlowCapability) -> FlowCapability | None:
    if isinstance(capability, FlowCapability):
        return capability
    ref = str(capability or "").strip()
    if not ref:
        return None
    for cap in spec.capabilities or []:
        if ref in {cap.name, cap.capability_id, cap.title}:
            return cap
    return None


def capability_to_flow_spec_view(
    spec: FlowSpec,
    capability: str | FlowCapability | None = None,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> FlowSpec:
    """把单个 capability 编译视图投影成旧 FlowSpec 形态。

    P1 阶段不改变旧全量发布路径；这个视图只用于按能力编译/校验。
    """
    current = ensure_recorded_goal(_sync_capability_io_schemas(sync_flow_spec_models(
        spec.model_copy(deep=True),
        prefer_request_facts=False,
    )))
    ref = capability
    if ref is None:
        ref = capability_id or capability_name or ""
    cap = _find_capability_by_ref(current, ref)
    if cap is None:
        raise ValueError(f"capability not found: {ref}")
    by_step = {s.step_id: s for s in current.steps}
    step_ids = [sid for sid in _capability_node_step_ids(cap) if sid in by_step]
    if not step_ids:
        step_ids = [sid for sid in (cap.step_ids or []) if sid in by_step]
    keep = set(step_ids)
    view = current.model_copy(deep=True)
    view.steps = [s for s in view.steps if s.step_id in keep]
    view.links = [
        lk for lk in view.links
        if lk.source_step_id in keep and lk.target_step_id in keep
    ]
    selected_cap = _find_capability_by_ref(view, cap.capability_id) or _find_capability_by_ref(view, cap.name)
    if selected_cap is None:
        selected_cap = cap.model_copy(deep=True)
    selected_cap.step_ids = [sid for sid in step_ids if sid in keep]
    selected_cap.nodes = [
        n for n in (selected_cap.nodes or [])
        if not isinstance(n, dict)
        or n.get("type") != "call"
        or str(n.get("step_id") or "") in keep
    ]
    view.capabilities = [selected_cap]
    view.capability_relations = [
        rel for rel in (view.capability_relations or [])
        if rel.from_capability in {selected_cap.name, selected_cap.capability_id}
        or rel.to_capability in {selected_cap.name, selected_cap.capability_id}
    ]
    view.meta = {
        **(view.meta or {}),
        "compiled_capability": {
            "name": selected_cap.name,
            "capability_id": selected_cap.capability_id,
            "step_ids": selected_cap.step_ids,
        },
    }
    return sync_flow_spec_models(view, prefer_request_facts=False)


def flow_spec_capability_contracts(
    spec: FlowSpec,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> list[dict[str, Any]]:
    return _capability_contract_views(
        spec,
        capability_id=capability_id,
        capability_name=capability_name,
    )


def compile_capability_to_api_request(
    spec: FlowSpec,
    capability: str | FlowCapability | None = None,
    *,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> tuple[dict | None, list[str]]:
    if capability is None and not capability_id and not capability_name:
        return flow_spec_to_api_request(spec)
    try:
        view = capability_to_flow_spec_view(
            spec,
            capability,
            capability_id=capability_id,
            capability_name=capability_name,
        )
    except ValueError as exc:
        return None, [str(exc)]
    api_request, errors = flow_spec_to_api_request(view)
    if api_request is not None:
        cap = view.capabilities[0] if view.capabilities else None
        if cap is not None:
            api_request["selected_capability"] = {
                "name": cap.name,
                "capability_id": cap.capability_id,
                "kind": cap.kind,
            }
            contracts = flow_spec_capability_contracts(view, capability_id=cap.capability_id)
            if contracts:
                api_request["compiled_capability"] = contracts[0]
    return api_request, errors


def flow_spec_to_api_request(
    spec: FlowSpec,
    *,
    capability: str | FlowCapability | None = None,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> tuple[dict | None, list[str]]:
    """把编辑后的 FlowSpec 转成 run_request_onboarding 可消费的 api_request。

    支持有 body 的写请求，也支持无 body 的 GET 前置步骤(query_template)。
    """
    if capability is not None or capability_id or capability_name:
        return compile_capability_to_api_request(
            spec,
            capability,
            capability_id=capability_id,
            capability_name=capability_name,
        )
    if not spec.steps:
        return None, ["FlowSpec 没有任何步骤，不能发布"]
    spec = prepare_flow_spec_for_publish(spec)
    active_step_ids = _active_capability_step_ids(spec)

    built_steps: list[dict] = []
    step_id_to_index: dict[str, int] = {}
    errors: list[str] = []
    for st in spec.steps:
        if active_step_ids is not None and st.step_id not in active_step_ids:
            continue
        apir, step_errors = _flow_step_to_api_step(st)
        if step_errors:
            errors.extend(step_errors)
            continue
        assert apir is not None
        step_id_to_index[st.step_id] = len(built_steps)
        built_steps.append(apir)

    if errors:
        return None, errors
    if not built_steps:
        return None, ["FlowSpec 没有可发布的请求步骤"]

    for lk in spec.links:
        if active_step_ids is not None and not (
            lk.source_step_id in active_step_ids or lk.target_step_id in active_step_ids
        ):
            continue
        if lk.source_step_id not in step_id_to_index or lk.target_step_id not in step_id_to_index:
            errors.append(f"链接 `{lk.link_id}` 指向不存在的步骤")
            continue
        target_idx = step_id_to_index[lk.target_step_id]
        source_idx = step_id_to_index[lk.source_step_id]
        if source_idx >= target_idx:
            errors.append(f"链接 `{lk.link_id}` 的来源步骤必须早于目标步骤")
            continue
        target_path = _clean_path_prefix(lk.target_path, "body.")
        source_path = _clean_path_prefix(lk.source_path, "response.")
        if not target_path or not source_path:
            errors.append(f"链接 `{lk.link_id}` 缺少 source_path 或 target_path")
            continue
        built_steps[target_idx].setdefault("links", []).append({
            "target_path": target_path,
            "target_tokens": lk.target_tokens,
            "source_step": source_idx,
            "source_path": source_path,
            "source_tokens": lk.source_tokens,
        })
    if errors:
        return None, errors

    if len(built_steps) == 1:
        out = built_steps[0]
    else:
        params = flow_spec_user_params(spec)
        samples: dict[str, Any] = {}
        field_types: dict[str, str] = {}
        for st in built_steps:
            samples.update(st.get("sample_inputs") or {})
            field_types.update(st.get("field_types") or {})
        out = {"steps": built_steps, "params": params, "sample_inputs": samples, "field_types": field_types}

    if spec.goal:
        out["goal"] = spec.goal
    caps = list(spec.capabilities or [])
    if caps:
        out["capabilities"] = [_capability_to_api_dict(spec, c) for c in caps]
        out["capability_relations"] = [relation.model_dump(exclude_none=True) for relation in spec.capability_relations]
        out["capability_graph"] = {
            "protocol": "dano.capability_graph.v1",
            "nodes": [c.name or c.capability_id for c in caps],
            "relations": [relation.model_dump(exclude_none=True) for relation in spec.capability_relations],
        }
        out["capability_contracts"] = flow_spec_capability_contracts(spec)
        out["capability_protocol"] = "dano.capability_plan.v1"
        out["workflow_nodes"] = {
            c.name: _capability_execution_contract(spec, c)
            for c in caps
            if c.name
        }
    out["_flow_spec"] = flow_spec_to_summary(spec)
    return out, []


def migrate_v1_flow_spec_to_capability_spec(spec: FlowSpec | dict[str, Any]) -> FlowSpec:
    """Explicit adapter: legacy/single-step FlowSpec -> capability-centric FlowSpec.

    V1 输入通常没有 request_facts/capabilities；这里只做确定性迁移，不调用 LLM。
    """
    current = FlowSpec.model_validate(spec) if isinstance(spec, dict) else spec.model_copy(deep=True)
    ensure_request_facts(current, prefer="request_facts")
    if not current.capabilities:
        current.capabilities = build_default_flow_capabilities(current)
    _normalize_capability_references(current)
    return ensure_recorded_goal(_ensure_external_transform_relations(
        _sync_capability_io_schemas(sync_flow_spec_models(current, prefer_request_facts=False))
    ))


def migrate_v2_flow_spec_to_capability_spec(spec: FlowSpec | dict[str, Any]) -> FlowSpec:
    """Explicit adapter: FlowSpec-centric V2 -> capability-centric V3 shape."""
    current = FlowSpec.model_validate(spec) if isinstance(spec, dict) else spec.model_copy(deep=True)
    ensure_request_facts(current, prefer="request_facts")
    if not current.capabilities:
        current.capabilities = build_default_flow_capabilities(current)
    _normalize_capability_references(current)
    current = _repair_generated_capability_contracts(current)
    for cap in current.capabilities or []:
        _sync_capability_order(current, cap)
    return ensure_recorded_goal(_ensure_external_transform_relations(
        _sync_capability_io_schemas(sync_flow_spec_models(current, prefer_request_facts=False))
    ))


def capability_spec_to_legacy_flow_spec(
    spec: FlowSpec | dict[str, Any],
    *,
    capability: str | FlowCapability | None = None,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> FlowSpec:
    """Adapter for old consumers that still expect FlowSpec.steps/links."""
    current = migrate_v2_flow_spec_to_capability_spec(spec)
    if capability is not None or capability_id or capability_name:
        return capability_to_flow_spec_view(
            current,
            capability,
            capability_id=capability_id,
            capability_name=capability_name,
        )
    return current


def capability_spec_to_api_request(
    spec: FlowSpec | dict[str, Any],
    *,
    capability: str | FlowCapability | None = None,
    capability_id: str | None = None,
    capability_name: str | None = None,
) -> tuple[dict | None, list[str]]:
    current = migrate_v2_flow_spec_to_capability_spec(spec)
    return flow_spec_to_api_request(
        current,
        capability=capability,
        capability_id=capability_id,
        capability_name=capability_name,
    )


def _canonical_api_shape(api_request: dict | None) -> dict[str, Any]:
    if not api_request:
        return {}
    steps = api_request.get("steps") or [api_request]
    compiled_ids = [
        str(st.get("step_id") or "")
        for st in steps
        if isinstance(st, dict) and st.get("step_id")
    ]
    return {
        "step_count": len(steps),
        "params": sorted(_api_params(api_request)),
        "methods": [(st.get("method") or "").upper() for st in steps],
        "paths": [st.get("path") or _request_path({"url": st.get("url") or ""}) for st in steps],
        "compiled_step_ids": compiled_ids,
        "capabilities": [
            {
                "name": cap.get("name"),
                "kind": cap.get("kind"),
                "compiled_step_ids": (
                    cap.get("compiled_step_ids")
                    if "compiled_step_ids" in cap
                    else cap.get("step_ids") or []
                ),
            }
            for cap in api_request.get("capabilities") or []
            if isinstance(cap, dict)
        ],
        "capability_protocol": api_request.get("capability_protocol") or "",
    }


def flow_spec_shadow_diff(spec: FlowSpec | dict[str, Any]) -> dict[str, Any]:
    """P0 shadow report comparing legacy full export and capability-centric exports."""
    capability_spec = migrate_v2_flow_spec_to_capability_spec(spec)
    legacy_api, legacy_errors = flow_spec_to_api_request(capability_spec)
    capability_reports: list[dict[str, Any]] = []
    for cap in capability_spec.capabilities or []:
        scoped_api, scoped_errors = capability_spec_to_api_request(capability_spec, capability_id=cap.capability_id)
        scoped_shape = _canonical_api_shape(scoped_api)
        scoped_cap_ids = {
            str(sid)
            for compiled_cap in scoped_shape.get("capabilities") or []
            for sid in (compiled_cap.get("compiled_step_ids") or [])
        }
        actual_step_ids = set(scoped_shape.get("compiled_step_ids") or []) | scoped_cap_ids
        missing_steps = [
            sid for sid in _capability_node_step_ids(cap)
            if sid not in actual_step_ids
        ]
        capability_reports.append({
            "name": cap.name,
            "capability_id": cap.capability_id,
            "kind": cap.kind,
            "errors": scoped_errors,
            "shape": scoped_shape,
            "missing_steps": missing_steps,
            "passed": not scoped_errors and not missing_steps,
        })
    canonical = flow_spec_canonical_summary(capability_spec)
    diffs: list[str] = []
    if legacy_errors:
        diffs.extend([f"legacy_export: {e}" for e in legacy_errors])
    for cap_report in capability_reports:
        if not cap_report["passed"]:
            diffs.append(f"capability `{cap_report['name']}` shadow failed")
    return {
        "protocol": "dano.recording_shadow_diff.v1",
        "passed": not diffs,
        "diffs": diffs,
        "legacy": {
            "errors": legacy_errors,
            "shape": _canonical_api_shape(legacy_api),
        },
        "capabilities": capability_reports,
        "canonical": canonical,
    }


def _api_params(api_request: dict) -> list[str]:
    names = list(api_request.get("params") or [])
    for st in api_request.get("steps") or []:
        for name in st.get("params") or []:
            if name not in names:
                names.append(name)
    return names


def _api_sample_inputs(api_request: dict) -> dict[str, Any]:
    samples = dict(api_request.get("sample_inputs") or {})
    for st in api_request.get("steps") or []:
        samples.update(st.get("sample_inputs") or {})
    return samples


def _dry_fields(api_request: dict, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    out = _api_sample_inputs(api_request)
    out.update(fields or {})
    for name in _api_params(api_request):
        out.setdefault(name, f"__DRY_{name}__")
    return out


def _dry_step_preview(step: dict, fields: dict[str, Any], index: int) -> dict:
    body = None
    query = None
    constructible = True
    error = ""
    if isinstance(step.get("body_template"), (dict, list)):
        try:
            body = substitute(step.get("body_template"), fields, step.get("sample_inputs") or {})
        except Exception as exc:  # noqa: BLE001
            constructible = False
            error = str(exc)
    if isinstance(step.get("query_template"), dict):
        try:
            query = substitute(step.get("query_template"), fields, step.get("sample_inputs") or {})
        except Exception as exc:  # noqa: BLE001
            constructible = False
            error = str(exc)
    return {
        "index": index,
        "method": step.get("method"),
        "path": step.get("path"),
        "url": step.get("url"),
        "params": list(step.get("params") or []),
        "links": list(step.get("links") or []),
        "has_body": body is not None,
        "body_preview": body,
        "has_query": query is not None,
        "query_preview": query,
        "constructible": constructible,
        "error": error,
    }


def _fact_check_report(api_request: dict | None) -> dict:
    if not api_request:
        return {"configured": False, "passed": False, "reason": "未生成 api_request"}
    fc = api_request.get("fact_check")
    if not fc:
        for st in api_request.get("steps") or []:
            if st.get("fact_check"):
                fc = st.get("fact_check")
                break
    if not fc:
        return {"configured": False, "passed": True, "reason": "未配置 fact_check，dry-run 仅做结构校验"}
    endpoint = fc.get("endpoint")
    match_field = fc.get("match_field")
    param = fc.get("param")
    missing = [name for name, value in {
        "endpoint": endpoint,
        "match_field": match_field,
        "param": param,
    }.items() if not value]
    return {
        "configured": True,
        "passed": not missing,
        "missing": missing,
        "spec": fc,
        "reason": "fact_check 配置完整" if not missing else f"fact_check 缺少 {', '.join(missing)}",
    }


def dry_run_flow_spec(spec: FlowSpec, fields: dict[str, Any] | None = None) -> dict:
    """静态 dry-run：不触网，只验证 FlowSpec 能否构造为可执行请求计划。"""
    api_request, build_errors = flow_spec_to_api_request(spec)
    if build_errors or api_request is None:
        return {
            "ok": False,
            "mode": "dry_run",
            "stage": "build",
            "build_errors": build_errors,
            "self_check": [],
            "missing_params": [],
            "request_count": 0,
            "execution_plan": [],
            "fact_check": _fact_check_report(api_request),
        }

    params = _api_params(api_request)
    samples = _api_sample_inputs(api_request)
    provided = dict(fields or {})
    missing = [
        name for name in flow_spec_required_params(spec)
        if name not in provided and name not in samples
    ]
    dry_fields = _dry_fields(api_request, fields)
    self_check_errors = self_check(api_request)
    raw_steps = api_request.get("steps") or [api_request]
    plan = [_dry_step_preview(st, dry_fields, i) for i, st in enumerate(raw_steps)]
    construct_errors = [p["error"] for p in plan if p.get("error")]
    fact = _fact_check_report(api_request)
    ok = not build_errors and not self_check_errors and not construct_errors and not missing and bool(fact.get("passed"))
    return {
        "ok": ok,
        "mode": "dry_run",
        "stage": "ok" if ok else "check",
        "build_errors": build_errors,
        "self_check": self_check_errors,
        "construct_errors": construct_errors,
        "missing_params": missing,
        "params": params,
        "required": flow_spec_required_params(spec),
        "request_count": len(raw_steps),
        "execution_plan": [
            {
                "index": p["index"],
                "method": p["method"],
                "path": p["path"],
                "params": p["params"],
                "link_count": len(p["links"]),
                "constructible": p["constructible"],
                "has_body": p["has_body"],
            }
            for p in plan
        ],
        "request_previews": plan,
        "fact_check": fact,
    }


def _diagnostic_publish_findings(spec: FlowSpec) -> tuple[list[str], list[str]]:
    """录制期诊断事实进入发布校验。

    只把能关联到已选业务步骤的 requestfailed 升级为 error；pageerror/console error
    先作为 warning，避免第三方脚本噪声误拦发布。
    """
    errors: list[str] = []
    warnings: list[str] = []
    diagnostics = list(spec.diagnostics or (spec.meta or {}).get("diagnostics") or [])
    if not diagnostics:
        return errors, warnings
    kept_request_indices = {
        st.source_meta.get("request_index")
        for st in spec.steps
        if st.source_meta.get("request_index") is not None
    }
    kept_urls = {str(st.url or "") for st in spec.steps if st.url}
    for d in diagnostics:
        kind = str(d.get("type") or "")
        msg = str(d.get("message") or "").strip()
        url = str(d.get("url") or "")
        req_idx = d.get("request_index")
        detail = msg or url or kind
        # Playwright 页面切换、录制结束或目标服务主动断开连接时，浏览器控制台常会
        # 留下 ERR_CONNECTION_CLOSED/ERR_ABORTED。若它没有关联到已纳入的业务请求，
        # 这只是录制环境噪声，不应成为 Skill 流程问题。
        benign_disconnect = bool(re.search(
            r"ERR_(?:CONNECTION_CLOSED|ABORTED|CANCELED)|Target page, context or browser has been closed",
            detail,
            re.I,
        )) and req_idx not in kept_request_indices and url not in kept_urls
        if benign_disconnect:
            continue
        if kind == "requestfailed" and (req_idx in kept_request_indices or url in kept_urls):
            errors.append(f"录制期业务请求失败: {detail[:200]}")
        elif kind == "pageerror":
            warnings.append(f"录制期页面异常: {detail[:200]}")
        elif kind == "console" and str(d.get("level") or "").lower() == "error":
            warnings.append(f"录制期控制台错误: {detail[:200]}")
    return errors, warnings


def _enum_map_covers_recorded_value(param: ParamField) -> bool:
    """枚举字段当前提交值是否能由候选 label 映射出来。

    body 存显示名时(label 本身等于 value)天然通过；body 存短码(type=2)时,必须有
    enum_value_map 或 {label,value} 能把某个显示项映射到 2,否则导出的 skill 会让前端传名字、
    运行时却提交不了真实短码。
    """
    current = str(param.value or "").strip()
    if not current:
        return True
    labels: list[str] = []
    option_values: list[Any] = []
    for opt in param.enum_options or []:
        pair = _enum_label_value(opt)
        if not pair:
            continue
        label, value = pair
        labels.append(label)
        option_values.append(value)
    if current in labels:
        return True
    mapped_values = list((param.enum_value_map or {}).values()) or option_values
    return any(str(v) == current for v in mapped_values if v not in (None, ""))


_VALUE_ONLY_LABEL_RE = re.compile(
    r"^\s*(?:[-+]?\d+(?:\.\d+)?|[0-9a-f]{8,}|[A-Za-z]{0,4}[-_]?\d{3,}|[A-Za-z0-9_-]{12,})\s*$",
    re.I,
)


def _enum_options_look_value_only(param: ParamField) -> bool:
    """候选全是 1/2/3、长 ID、短码且没有非等值映射时,说明把内部值当成了显示名。"""
    pairs = [p for p in (_enum_label_value(o) for o in (param.enum_options or [])) if p]
    if not pairs:
        return False
    labels = [label for label, _value in pairs]
    if not all(_VALUE_ONLY_LABEL_RE.match(label) for label in labels):
        return False
    value_map = dict(param.enum_value_map or _enum_option_map_from_options(param.enum_options))
    if not value_map:
        return True
    # 如果至少有一个「人类显示名 -> 内部值」的非等值映射,就不是坏枚举。
    return not any(
        label and not _VALUE_ONLY_LABEL_RE.match(label) and str(value) != str(label)
        for label, value in value_map.items()
    )


_INTERNAL_EXPOSED_PATH_RE = re.compile(
    r"(^|[.\]])[A-Za-z0-9_]*(?:id|ids|code|dm|lx|sf|flag|state|status|type)$",
    re.I,
)


def _select_has_executable_options(sel: SelectBinding | None) -> bool:
    if sel is None:
        return False
    return bool(
        (sel.source_url and (sel.value_key or sel.option_map or sel.options))
        or sel.options
        or sel.option_map
    )


def _param_looks_exposed_internal_value(param: ParamField) -> bool:
    """内部 ID/短码/空 id 不应作为普通用户输入暴露。"""
    if param.category != "user_param" or not param.exposed_to_user:
        return False
    if (
        param.source_kind in _OPTION_SOURCE_KINDS
        and bool(param.enum_value_map or param.enum_options)
        and _enum_map_covers_recorded_value(param)
    ):
        # 调用方看到的是业务 label，运行期才映射为内部 ID；这正是正确的枚举契约。
        return False
    if param.source_kind not in {"user_input", "unknown", "api_option"}:
        return False
    path_key = f"{param.path}.{param.key}"
    if not (_INTERNAL_EXPOSED_PATH_RE.search(str(param.path or "")) or _INTERNAL_EXPOSED_PATH_RE.search(str(param.key or ""))):
        return False
    value = str(param.value or "").strip()
    if value == "":
        return True
    if param.type in {"number", "boolean"} and not re.search(r"(id|code|dm|lx|sf|flag|state|status|type)", path_key, re.I):
        return False
    return bool(_VALUE_ONLY_LABEL_RE.match(value) or re.match(r"^[A-Z]{1,6}$", value))


def _publish_issue_category(message: str) -> str:
    text = str(message or "").lower()
    if any(token in text for token in (
        "console", "pageerror", "requestfailed", "诊断", "控制台", "页面异常",
        "录制期业务请求失败", "err_connection", "err_aborted",
    )):
        return "diagnostic"
    if "capability" in text or "能力" in text:
        return "capability"
    if "链接" in text or "依赖" in text or "link" in text or "source_path" in text or "target_path" in text:
        return "dependency"
    if any(token in text for token in ("字段", "枚举", "user_param", "runtime_var", "system_const", "参数", "短码")):
        return "field"
    if any(token in text for token in ("步骤", "接口", "请求", "step", "request")):
        return "interface"
    if any(token in text for token in ("dry-run", "dry_run", "success_rule", "成功判断", "fact_check", "self_check")):
        return "execution"
    return "flow"


def _publish_issue_groups(
    errors: list[str],
    warnings: list[str],
    review_items: list[ReviewItem],
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {
        key: [] for key in ("capability", "interface", "field", "dependency", "execution", "diagnostic", "flow")
    }
    seen: dict[tuple[Any, ...], tuple[str, int]] = {}
    severity_rank = {"error": 3, "high": 3, "warning": 2, "medium": 2, "low": 1}

    def semantic_key(
        category: str,
        message: str,
        target: dict[str, Any] | None,
    ) -> tuple[Any, ...]:
        target = target or {}
        if category == "dependency":
            link_id = str(target.get("link_id") or target.get("dependency_id") or "")
            if link_id:
                return (category, "link", link_id)
            endpoints = tuple(str(target.get(k) or "") for k in (
                "source_step_id", "source_path", "target_step_id", "target_path",
            ))
            if any(endpoints):
                return (category, "endpoints", *endpoints)
        if category == "field":
            field_id = str(target.get("field_id") or "")
            step_id = str(target.get("step_id") or "")
            path = str(target.get("path") or target.get("key") or "")
            if field_id or step_id or path:
                return (category, target.get("capability") or "", field_id, step_id, path)
        if category == "capability" and target.get("capability"):
            return (category, target.get("capability"), target.get("code") or message)
        return (category, message)

    def add(category: str, severity: str, message: str, *, source: str, target: dict[str, Any] | None = None) -> None:
        if not message:
            return
        key = semantic_key(category, message, target)
        existing = seen.get(key)
        blocking = severity in {"error", "high"}
        audience = "operator" if blocking or source == "review" else "internal"
        entry = {
            "severity": severity,
            "message": message,
            "source": source,
            "target": target or {},
            "blocking": blocking,
            "audience": audience,
            "actionable": audience == "operator",
            "auto_fixable": source == "validator" and not blocking,
        }
        if existing is not None:
            existing_category, existing_index = existing
            old = groups[existing_category][existing_index]
            if severity_rank.get(severity, 0) > severity_rank.get(str(old.get("severity") or ""), 0):
                groups[existing_category][existing_index] = entry
            return
        bucket = groups.setdefault(category, [])
        seen[key] = (category, len(bucket))
        bucket.append(entry)

    def validator_target(message: str) -> dict[str, Any]:
        """Recover structured locations from legacy validator messages."""
        capability_match = re.search(r"Capability\s+`([^`]+)`", message)
        field_match = re.search(r"字段\s+`([^`]+)`", message)
        link_match = re.search(r"(?:链接|link)\s+`([^`]+)`", message, re.I)
        target: dict[str, Any] = {}
        if capability_match:
            target = {"kind": "capability", "capability": capability_match.group(1)}
        if field_match:
            target = {
                **target,
                "kind": "capability_field" if capability_match else "param",
                "path": field_match.group(1),
            }
        if link_match:
            target = {"kind": "link", "link_id": link_match.group(1)}
        return target

    for item in review_items:
        if item.resolved:
            continue
        target_kind = str((item.target or {}).get("kind") or "")
        category = {
            "param": "field",
            "capability_enum": "field",
            "link": "dependency",
            "step": "interface",
            "request_role": "interface",
            "capability": "capability",
            "flow": "flow",
        }.get(target_kind, _publish_issue_category(item.title))
        add(category, item.severity or "warning", item.title, source="review", target=item.target)
    for message in errors:
        add(_publish_issue_category(message), "error", message, source="validator", target=validator_target(message))
    for message in warnings:
        add(_publish_issue_category(message), "warning", message, source="validator", target=validator_target(message))
    return {key: value for key, value in groups.items() if value}


def prepare_flow_spec_for_publish(spec: FlowSpec) -> FlowSpec:
    """Canonicalize the current workbench state without invoking Planner or LLM."""
    current = sync_flow_spec_models(spec.model_copy(deep=True), prefer_request_facts=False)
    _canonicalize_public_capability_identities(current)
    _normalize_capability_references(current)
    current = _ensure_external_transform_relations(_sync_capability_io_schemas(current))
    return ensure_recorded_goal(current)


def prepare_flow_release_candidate(spec: FlowSpec) -> tuple[FlowSpec, dict[str, Any]]:
    """Freeze the exact canonical workbench contract consumed by publish/export."""
    current = prepare_flow_spec_for_publish(spec)
    fingerprint = _flow_fingerprint(current)
    inventory = [
        {
            "capability_id": cap.capability_id,
            "name": cap.name,
            "kind": cap.kind,
            "step_ids": list(_capability_node_step_ids(cap)),
            "memberships": [
                {
                    "step_id": ref.step_id,
                    "request_id": ref.request_id,
                    "usage": ref.usage,
                    "origin": ref.origin,
                    "pinned": ref.pinned,
                }
                for ref in (cap.request_refs or [])
            ],
        }
        for cap in current.capabilities or []
    ]
    release = {
        "protocol": "dano.recording_release.v1",
        "release_id": f"{current.flow_id}-{fingerprint}",
        "flow_fingerprint": fingerprint,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "interface_inventory": inventory,
    }
    current.meta = {**(current.meta or {}), "release_candidate": release}
    return current, release


def validate_flow_spec(spec: FlowSpec) -> dict:
    from dano.execution.page.repair_ops import collect_repair_findings

    # 校验只面对规范化后的当前事实。字段、接口顺序或能力范围改变后产生的旧
    # input/map/return/link 由同步层确定性清理，不能继续作为“用户待处理”告警。
    spec = prepare_flow_spec_for_publish(spec)
    for capability in spec.capabilities or []:
        capability.nodes = _sanitize_capability_nodes(spec, capability)
    spec = _prune_empty_capabilities(spec)
    _normalize_capability_references(spec)
    errors: list[str] = []
    warnings: list[str] = []
    active_step_ids = _active_capability_step_ids(spec)
    active_steps = [
        step for step in spec.steps
        if active_step_ids is None or step.step_id in active_step_ids
    ]
    review_items = refresh_review_items(spec.model_copy(deep=True)).review_items
    blocking_reviews = [
        item for item in review_items
        if item.severity == "high" and not item.resolved and item.type in _PUBLISH_BLOCKING_REVIEW_TYPES
    ]
    errors.extend([f"发布阻断项未处理: {item.title}" for item in blocking_reviews])
    diag_errors, diag_warnings = _diagnostic_publish_findings(spec)
    errors.extend(diag_errors)
    warnings.extend(diag_warnings)
    capability_validation = _capability_validation_report(spec)
    capability_errors = list(capability_validation.get("errors") or [])
    capability_warnings = list(capability_validation.get("warnings") or [])
    errors.extend(capability_errors)
    warnings.extend(capability_warnings)
    by_step_id = {step.step_id: step for step in spec.steps}
    for capability in spec.capabilities or []:
        cap_label = capability.title or capability.name or capability.capability_id
        for ref in capability.request_refs or []:
            if ref.pinned and ref.usage in {"execute", "preflight", "fact_check"} and ref.step_id not in set(_capability_node_step_ids(capability)):
                errors.append(f"Capability `{cap_label}` 手工锁定接口 `{ref.step_id or ref.request_id}` 未进入执行计划")
        for field_name, field_schema in (capability.input_schema.get("properties") or {}).items():
            if isinstance(field_schema, dict) and field_schema.get("x-dano-conflicts"):
                errors.append(f"Capability `{cap_label}` 输入字段 `{field_name}` 在多个接口中类型或路径冲突")
        if capability.kind == "query_status":
            cap_steps = [by_step_id[sid] for sid in _capability_node_step_ids(capability) if sid in by_step_id]
            if cap_steps and not any(_is_business_query_step(step) for step in cap_steps):
                errors.append(f"Capability `{cap_label}` 没有返回业务记录/状态的查询接口，仅包含配置或前置接口")
    api_request, build_errors = flow_spec_to_api_request(spec)
    errors.extend(build_errors)
    if not flow_spec_user_params(spec):
        warnings.append("FlowSpec 没有 user_param，发布后的 Skill 不会要求用户输入参数")
    for st in active_steps:
        select_by_path = {s.path: s for s in st.selects if s.path}
        select_by_param = {s.param: s for s in st.selects if s.param}
        for p in st.params:
            enum_contract_error = _capability_param_enum_issue(p)
            if enum_contract_error:
                errors.append(f"枚举字段 `{p.key or p.path}` {enum_contract_error}")
            source_contract_error = _field_source_contract_error(p)
            if source_contract_error:
                errors.append(source_contract_error)
            if p.category == "runtime_var" and p.source_kind == "unknown":
                warnings.append(f"字段 `{p.path}` 被判为 runtime_var，但来源仍需确认")
            if p.category == "system_const" and p.exposed_to_user:
                errors.append(f"字段 `{p.path}` 是 system_const，但仍暴露给用户")
            if p.source_kind == "api_option":
                sel = select_by_path.get(p.path) or select_by_param.get(p.key)
                if not _select_has_executable_options(sel):
                    errors.append(
                        f"字段 `{p.key or p.path}` 标记为接口选项，但缺少可执行的 source_url/options/option_map，"
                        "不能发布为可调用 Skill"
                    )
                elif sel and (sel.source_method or "GET").upper() not in {"GET", "HEAD"} and sel.source_role not in {
                    "business_get", "read_context", "read_option",
                }:
                    errors.append(
                        f"字段 `{p.key or p.path}` 的接口选项源 `{sel.source_method} {sel.source_url}` "
                        "未被识别为只读接口，不能在运行期自动调用"
                    )
            has_executable_api_options = (
                p.source_kind == "api_option"
                and _select_has_executable_options(select_by_path.get(p.path) or select_by_param.get(p.key))
            )
            if not has_executable_api_options and _param_looks_exposed_internal_value(p):
                errors.append(
                    f"字段 `{p.key or p.path}` 看起来是内部 ID/短码/空标识，不能直接暴露给用户；"
                    "请改为接口枚举映射或系统常量"
                )
            if (
                p.type in {"enum", "list-enum"}
                and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
                and p.enum_options
                and not _enum_map_covers_recorded_value(p)
            ):
                errors.append(
                    f"枚举字段 `{p.key or p.path}` 当前提交值 `{p.value}` 没有完整 label→value 映射，"
                    "请补充真实选项值映射或重新录制到字典接口"
                )
            if (
                p.type in {"enum", "list-enum"}
                and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
                and p.enum_options
                and _enum_options_look_value_only(p)
            ):
                errors.append(
                    f"枚举字段 `{p.key or p.path}` 的候选看起来全是内部值/短码，"
                    "不能作为用户可选项导出；请填写 `显示名=真实值`（如 `病假=2`）或重新录制真实下拉"
                )
    for lk in spec.links:
        if active_step_ids is not None and not (
            lk.source_step_id in active_step_ids or lk.target_step_id in active_step_ids
        ):
            continue
        if not lk.confirmed:
            errors.append(f"链接 `{lk.link_id}` 尚未人工确认")
    if active_steps and not any((st.success_rule for st in active_steps)):
        warnings.append("未识别到明确 success_rule，运行期只能使用通用成功判断")
    self_check_errors: list[str] = []
    if api_request is not None:
        self_check_errors = self_check(api_request)
        errors.extend(self_check_errors)
        repair_findings = collect_repair_findings(api_request)
        # 系统化:session_constant 仅当对应字段**真的被识别为 system_const/constant** 时才算发布阻断;
        # 若字段在 spec 里被标 runtime_var/unknown → 这部分错误让前端 review_items 兜底,
        # 避免一锅端。修复者应在 dynamic_run 时再注入。
        params_by_path: dict[str, dict] = {}
        for st in active_steps:
            for p in st.params:
                params_by_path[p.path] = p.model_dump() if hasattr(p, "model_dump") else p.dict()
        session_errors: list[str] = []
        for f in repair_findings:
            if f.get("kind") != "session_constant":
                continue
            detail = f.get("detail", "")
            path = (f.get("path") or [])
            path_str = ".".join(str(p) for p in path) if isinstance(path, (list, tuple)) else str(path)
            spec_field = params_by_path.get(path_str) or {}
            if spec_field.get("category") in ("runtime_var", "system_const"):
                continue
            session_errors.append(detail)
        errors.extend(session_errors)
    dry_run = dry_run_flow_spec(spec)
    errors = list(dict.fromkeys(str(item) for item in errors if item))
    warnings = list(dict.fromkeys(str(item) for item in warnings if item))
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "issue_groups": _publish_issue_groups(errors, warnings, review_items),
        "dry_run": dry_run,
        "review_items": [item.model_dump() for item in review_items],
        "review_summary": {
            "total": len(review_items),
            "high": len([i for i in review_items if i.severity == "high"]),
            "medium": len([i for i in review_items if i.severity == "medium"]),
            "low": len([i for i in review_items if i.severity == "low"]),
        },
        "self_check": self_check_errors,
        "api_preview": {
            "workflow_steps": len(api_request.get("steps") or []) if api_request else 0,
            "method": api_request.get("method") if api_request else None,
            "path": api_request.get("path") if api_request else None,
            "params": flow_spec_user_params(spec),
            "required": flow_spec_required_params(spec),
        },
        "capability_preview": [
            {
                "name": c.name,
                "kind": c.kind,
                "step_ids": c.step_ids,
                "nodes": c.nodes,
                "confirmed": c.confirmed,
                "requires_human_confirm": c.requires_human_confirm,
                "confidence": c.confidence,
                "status": c.status,
            }
            for c in (spec.capabilities or [])
        ],
        "capability_validation": capability_validation,
    }


_CLIENT_SECRET_KEY_HINTS = (
    "authorization", "cookie", "token", "satoken", "jwt", "password", "passwd",
    "secret", "credential", "session", "ticket",
)


def _client_redact_sensitive(node, key_hint: str = ""):
    key_l = str(key_hint or "").lower()
    if key_l and any(h in key_l for h in _CLIENT_SECRET_KEY_HINTS):
        return "***"
    if isinstance(node, dict):
        return {k: _client_redact_sensitive(v, str(k)) for k, v in node.items()}
    if isinstance(node, list):
        return [_client_redact_sensitive(v, key_hint) for v in node]
    return node


def flow_spec_to_client(spec: FlowSpec) -> dict:
    """给前端展示的 FlowSpec：保留可编辑请求事实，只隐藏鉴权信息。

    ``body_source`` 是步骤请求体的唯一事实源，不能在客户端投影中清空；否则客户端
    回传当前 FlowSpec 时会把有效 POST 降级成无请求体步骤。请求体字段本来就在字段
    工作台中可见，真正需要隐藏的是认证头、身份值和响应中的敏感字段。
    """
    client_spec = sync_flow_spec_models(spec.model_copy(deep=True), prefer_request_facts=False)
    _normalize_capability_references(client_spec)
    data = refresh_review_items(_sync_capability_io_schemas(client_spec)).model_dump()
    data["meta"] = {**(data.get("meta") or {}), "current_fingerprint": _flow_fingerprint(client_spec)}
    request_graph = ((data.get("meta") or {}).get("request_graph") or {})
    for bucket in ("all_requests", "candidate_reads", "selected_steps", "filtered_requests"):
        for req in request_graph.get(bucket) or []:
            if req.get("headers"):
                req["headers"] = {k: "***" for k in (req.get("headers") or {})}
            if req.get("post_data") is not None:
                req["post_data"] = ""
            if req.get("response_json") is not None:
                req["response_json"] = _client_redact_sensitive(req.get("response_json"))
    request_facts = data.get("request_facts") or {}
    for req in request_facts.get("requests") or []:
        if req.get("headers"):
            req["headers"] = {k: "***" for k in (req.get("headers") or {})}
        if req.get("post_data") is not None:
            req["post_data"] = ""
        if req.get("response_json") is not None:
            req["response_json"] = _client_redact_sensitive(req.get("response_json"))
    for st in data.get("steps") or []:
        st["headers"] = {k: "***" for k in (st.get("headers") or {})}
        if st.get("response_json") is not None:
            st["response_json"] = _client_redact_sensitive(st.get("response_json"))
        for select in st.get("selects") or []:
            if select.get("source_headers"):
                select["source_headers"] = {k: "***" for k in (select.get("source_headers") or {})}
            if select.get("source_body") is not None:
                select["source_body"] = ""
        for idn in st.get("identity") or []:
            if idn.get("value") is not None:
                idn["value"] = "***"
    return data


# ─────────── Step B+C: 编辑函数 ───────────
def _find_step(spec: FlowSpec, step_id: str) -> FlowStep:
    for step in spec.steps:
        if step.step_id == step_id:
            return step
    available = [s.step_id for s in spec.steps]
    raise ValueError(f"step not found: {step_id} (available: {available})")


def _find_param(step: FlowStep, param_path: str, *, param_key: str = "", param_label: str = "") -> ParamField:
    needle = str(param_path or "")
    for param in step.params:
        if param.path == needle:
            return param
    stripped = _strip_body_prefix(needle)
    if stripped and stripped != needle:
        for param in step.params:
            if _strip_body_prefix(param.path) == stripped:
                return param
    hints = [str(x or "").strip() for x in (param_key, param_label)]
    hints = [x for x in hints if x]
    for hint in hints:
        for param in step.params:
            if param.key == hint or param.label == hint:
                return param
    for param in step.params:
        if param.path and (param.path.endswith(f".{needle}") or param.path.endswith(f"[{needle}]")):
            return param
    available = [f"{p.path}({p.key})" for p in step.params]
    raise ValueError(f"param not found: {param_path} in step {step.step_id}; available={available}")


def _find_link(spec: FlowSpec, link_id: str) -> FlowLink:
    for link in spec.links:
        if link.link_id == link_id:
            return link
    available = [l.link_id for l in spec.links]
    raise ValueError(f"link not found: {link_id} (available: {available})")


def _validate_link_endpoint(spec: FlowSpec, step_id: str, label: str) -> None:
    if not any(s.step_id == step_id for s in spec.steps):
        raise ValueError(f"{label} step not found: {step_id}")


def _ensure_unique_link(spec: FlowSpec, link: FlowLink) -> None:
    dup = any(
        existing.source_step_id == link.source_step_id
        and existing.target_step_id == link.target_step_id
        and existing.source_path == link.source_path
        and existing.target_path == link.target_path
        and existing.link_id != link.link_id
        for existing in spec.links
    )
    if dup:
        raise ValueError("duplicate link (same source/target/path exists)")


def _matching_link(spec: FlowSpec, link: FlowLink) -> FlowLink | None:
    for existing in spec.links:
        if (
            existing.source_step_id == link.source_step_id
            and existing.target_step_id == link.target_step_id
            and _strip_body_prefix(existing.source_path) == _strip_body_prefix(link.source_path)
            and _strip_body_prefix(existing.target_path) == _strip_body_prefix(link.target_path)
            and existing.link_id != link.link_id
        ):
            return existing
    return None


def _merge_link(existing: FlowLink, incoming: FlowLink) -> None:
    existing.confirmed = bool(existing.confirmed or incoming.confirmed)
    existing.confidence = max(float(existing.confidence or 0), float(incoming.confidence or 0))
    existing.reason = incoming.reason or existing.reason
    existing.locked = bool(getattr(existing, "locked", False) or getattr(incoming, "locked", False))
    if incoming.param_name:
        existing.param_name = incoming.param_name


def _remove_step(spec: FlowSpec, step_id: str) -> None:
    step = _find_step(spec, step_id)
    spec.steps.remove(step)
    spec.links = [
        lk for lk in spec.links
        if lk.source_step_id != step_id and lk.target_step_id != step_id
    ]
    spec.review_items = [
        item for item in spec.review_items
        if item.target.get("step_id") != step_id
        and item.target.get("source_step_id") != step_id
        and item.target.get("target_step_id") != step_id
    ]


def _step_dedupe_key(step: FlowStep) -> tuple[str, str]:
    return ((step.method or "GET").upper(), _request_path({"url": step.path or step.url}))


def _is_dedupable_read_step(step: FlowStep) -> bool:
    if (step.method or "").upper() in _WRITE_METHODS:
        return False
    role = (step.source_meta or {}).get("role") or step.semantic_role or ""
    return role in {"", "business_get", "read_context", "read_option"}


def _dedupe_flow_steps(spec: FlowSpec) -> int:
    latest_by_key: dict[tuple[str, str], str] = {}
    for step in spec.steps:
        if _is_dedupable_read_step(step):
            latest_by_key[_step_dedupe_key(step)] = step.step_id

    keep_ids: set[str] = set()
    removed_ids: set[str] = set()
    for step in spec.steps:
        if _is_dedupable_read_step(step) and latest_by_key.get(_step_dedupe_key(step)) != step.step_id:
            removed_ids.add(step.step_id)
        else:
            keep_ids.add(step.step_id)

    if not removed_ids:
        return 0

    spec.steps = [step for step in spec.steps if step.step_id in keep_ids]
    spec.links = [
        lk for lk in spec.links
        if lk.source_step_id not in removed_ids and lk.target_step_id not in removed_ids
    ]
    spec.review_items = [
        item for item in spec.review_items
        if item.target.get("step_id") not in removed_ids
        and item.target.get("source_step_id") not in removed_ids
        and item.target.get("target_step_id") not in removed_ids
    ]
    spec.meta = {
        **(spec.meta or {}),
        "deduped_step_count": int(spec.meta.get("deduped_step_count") or 0) + len(removed_ids),
    }
    return len(removed_ids)


def _request_graph_items(spec: FlowSpec) -> list[dict[str, Any]]:
    graph = _request_graph_for_spec(spec)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, Any, str, str]] = set()
    for bucket in ("all_requests", "selected_steps", "candidate_reads", "filtered_requests"):
        for item in graph.get(bucket) or []:
            request_id = str(item.get("request_id") or "")
            sig = (
                request_id,
                item.get("request_index") if not request_id else "",
                (item.get("method") or "").upper(),
                item.get("path") or item.get("url") or "",
            )
            if sig in seen:
                continue
            seen.add(sig)
            out.append(dict(item))
    return out


def _find_request_graph_item(spec: FlowSpec, *, request_index: Any = None, request_id: str = "") -> dict[str, Any] | None:
    for item in _request_graph_items(spec):
        if request_index is not None and item.get("request_index") == request_index:
            return item
        if request_id and str(item.get("request_id") or "") == request_id:
            return item
    return None


def _same_request_graph_item(item: dict[str, Any], entry: dict[str, Any]) -> bool:
    item_id = str(item.get("request_id") or "")
    entry_id = str(entry.get("request_id") or "")
    if item_id and entry_id:
        return item_id == entry_id
    if item.get("request_index") is not None and entry.get("request_index") is not None:
        return item.get("request_index") == entry.get("request_index")
    return _request_graph_signature(item) == _request_graph_signature(entry)


def _mark_request_selected(spec: FlowSpec, entry: dict[str, Any], *, materialized_step_id: str = "") -> None:
    rg = dict(_request_graph_for_spec(spec))
    selected = list(rg.get("selected_steps") or [])
    if not any(_same_request_graph_item(item, entry) for item in selected):
        selected.append({
            k: entry.get(k)
            for k in (
                "request_index", "request_id", "page_id", "frame_id", "sequence",
                "method", "url", "path", "role", "reason", "confidence", "evidence",
                "response_status", "response_schema",
            )
        })
    for item in selected:
        if _same_request_graph_item(item, entry):
            item["state"] = "materialized" if materialized_step_id else item.get("state") or "captured"
            if materialized_step_id:
                item["materialized_step_id"] = materialized_step_id
    for bucket in ("all_requests", "candidate_reads", "filtered_requests"):
        updated = []
        for item in (rg.get(bucket) or []):
            item = dict(item)
            if _same_request_graph_item(item, entry):
                item["state"] = "materialized" if materialized_step_id else item.get("state") or "captured"
                if materialized_step_id:
                    item["materialized_step_id"] = materialized_step_id
            updated.append(item)
        if bucket in rg:
            rg[bucket] = updated
    rg["selected_steps"] = selected
    rg["candidate_reads"] = [
        item for item in (rg.get("candidate_reads") or [])
        if not _same_request_graph_item(item, entry)
    ]
    spec.meta = {**(spec.meta or {}), "request_graph": rg}
    ensure_request_facts(spec, prefer="meta")


def _param_type_from_value(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    text = str(value or "")
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return "number"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "date"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?", text):
        return "datetime"
    return "string"


def _append_query_params_to_step(step: FlowStep, url: str) -> None:
    parsed = urlparse(url or "")
    query = parse_qs(parsed.query or "", keep_blank_values=True)
    if not query:
        return
    existing = {p.path for p in step.params}
    existing_keys = {p.key for p in step.params}
    for key, values in query.items():
        path = f"query.{key}"
        if not key or key in existing or path in existing or key in existing_keys:
            continue
        value = values[0] if values else ""
        source_guess = _param_source_guess(
            field={"path": path, "key": key, "value": value},
            path=path,
            key=key,
            method=(step.method or "GET").upper(),
            identity_paths=set(),
            system_paths=set(),
            select_paths=set(),
            select_id_paths=set(),
            samples=step.sample_inputs or {},
            request_headers=step.headers or {},
        )
        step.params.append(ParamField(
            path=path,
            key=key,
            label=key,
            value=str(value),
            type=_param_type_from_value(value),
            required=False,
            category=source_guess["category"],
            source_kind=source_guess["source_kind"],
            source={**source_guess["source"], "from": "query"},
            exposed_to_user=bool(source_guess["exposed_to_user"]),
            editable=bool(source_guess["editable"]),
            need_human_confirm=bool(source_guess["need_human_confirm"]),
            reason=source_guess["reason"],
        ))
        existing.add(path)
        existing_keys.add(key)


def _dependency_sig(source_step_id: str, source_path: str, target_step_id: str, target_path: str) -> str:
    raw = "|".join([source_step_id or "", source_path or "", target_step_id or "", target_path or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _dependency_match_score(param: ParamField, source_path: str) -> int:
    source_norm = _norm_field_name(source_path, source_path)
    target_tokens = [
        _norm_field_name(str(param.key or ""), param.path),
        _norm_field_name(str(param.label or ""), param.path),
        _norm_field_name(param.path.split(".")[-1], param.path),
    ]
    score = 0
    for idx, token in enumerate(t for t in target_tokens if t):
        if token and token in source_norm:
            score += 30 - idx
    if "[" not in source_path:
        score += 3
    if source_path.lower().endswith(str(param.key or "").lower()):
        score += 12
    return score


def _skip_auto_dependency_target(param: ParamField | None) -> bool:
    return not _auto_dependency_target_allowed(param)


def _rejected_dependency_sigs(spec: FlowSpec) -> set[str]:
    meta = spec.meta or {}
    return {str((x.get("sig") if isinstance(x, dict) else x) or x) for x in (meta.get("rejected_dependencies") or [])}


def _record_rejected_dependency(spec: FlowSpec, link: FlowLink) -> None:
    _record_rejected_dependency_raw(
        spec,
        source_step_id=link.source_step_id,
        source_path=link.source_path,
        target_step_id=link.target_step_id,
        target_path=link.target_path,
    )


def _record_rejected_dependency_raw(
    spec: FlowSpec,
    *,
    source_step_id: str,
    source_path: str,
    target_step_id: str,
    target_path: str,
) -> None:
    sig = _dependency_sig(source_step_id, source_path, target_step_id, target_path)
    rejected = list((spec.meta or {}).get("rejected_dependencies") or [])
    if not any(str((x.get("sig") if isinstance(x, dict) else x) or x) == sig for x in rejected):
        rejected.append({
            "sig": sig,
            "source_step_id": source_step_id,
            "source_path": source_path,
            "target_step_id": target_step_id,
            "target_path": target_path,
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        })
    spec.meta = {**(spec.meta or {}), "rejected_dependencies": rejected}


def rebuild_flow_dependencies(spec: FlowSpec) -> int:
    """基于已物化步骤重建高置信值驱动依赖。

    只追加缺失候选；不会修改原始 RequestGraph，也不会恢复用户已删除的依赖。
    """
    existing = {
        _dependency_sig(lk.source_step_id, lk.source_path, lk.target_step_id, lk.target_path)
        for lk in spec.links
    }
    rejected = _rejected_dependency_sigs(spec)
    added = 0
    for tgt_idx, target in enumerate(spec.steps):
        if not target.params:
            continue
        for param in target.params:
            if param.locked:
                continue
            target_leaf = re.sub(
                r"[^a-z0-9]+", "", str(param.path or param.key or "").split(".")[-1].lower()
            )
            internal_id_target = target_leaf.endswith("id") and not _looks_user_entered_business_field(param.key, param.path)
            if _skip_auto_dependency_target(param) and not internal_id_target:
                continue
            if param.source_kind == "previous_response" and param.source.get("step_id"):
                continue
            value = str(param.value if param.value is not None else "").strip()
            if len(value) < 4:
                continue
            matches: list[tuple[FlowStep, str]] = []
            for source in spec.steps[:tgt_idx]:
                if source.response_json is None:
                    continue
                for path, _tokens, leaf_value, _raw in _leaf_paths(source.response_json):
                    if str(leaf_value) == value:
                        matches.append((source, path))
            if len(matches) == 1:
                source, source_path = matches[0]
            else:
                ranked = sorted(
                    [(_dependency_match_score(param, path), source, path) for source, path in matches],
                    key=lambda item: item[0],
                    reverse=True,
                )
                if not ranked or ranked[0][0] < 12:
                    continue
                # 多个响应携带同一值时，字段名仅略相似不足以建立依赖；必须有明显
                # 语义优势，避免 status/id/date 等常见值在不同接口间随机串线。
                if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 8:
                    continue
                _score, source, source_path = ranked[0]
            source_leaf = re.sub(r"[^a-z0-9]+", "", str(source_path or "").split(".")[-1].lower())
            strong_internal_id = internal_id_target and source_leaf == "id" and len(matches) == 1
            if not strong_internal_id and not _auto_dependency_link_allowed(param, source_path):
                continue
            sig = _dependency_sig(source.step_id, source_path, target.step_id, param.path)
            if sig in existing or sig in rejected:
                continue
            spec.links.append(FlowLink(
                source_step_id=source.step_id,
                source_path=source_path,
                target_step_id=target.step_id,
                target_path=param.path,
                param_name=param.key,
                confirmed=True,
                confidence=0.97,
                reason="promote 后重建依赖：目标字段录制值唯一命中上游响应字段，自动确认为运行期依赖",
                evidence={"kind": "value_match", "value": value, "auto_rebuilt": True},
            ))
            existing.add(sig)
            added += 1
    if added:
        _sync_link_sources(spec.steps, spec.links)
    return added


def _request_sequence_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return None


def _step_sequence(step: FlowStep) -> float | None:
    meta = step.source_meta or {}
    return _request_sequence_value(meta.get("sequence", meta.get("request_index")))


def _entry_sequence(entry: dict[str, Any]) -> float | None:
    return _request_sequence_value(entry.get("sequence", entry.get("request_index")))


def _insert_promoted_step(spec: FlowSpec, step: FlowStep, entry: dict[str, Any]) -> None:
    """把后加入接口插回合理执行位置，而不是一律追加到最后。"""
    seq = _entry_sequence(entry)
    if seq is not None:
        for idx, existing in enumerate(spec.steps):
            existing_seq = _step_sequence(existing)
            if existing_seq is not None and existing_seq > seq:
                spec.steps.insert(idx, step)
                return

    role = str(entry.get("role") or "")
    method = (step.method or entry.get("method") or "").upper()
    if method == "GET" or role in {"business_get", "read_context", "read_option"}:
        for idx, existing in enumerate(spec.steps):
            if (existing.method or "").upper() in _WRITE_METHODS:
                spec.steps.insert(idx, step)
                return

    spec.steps.append(step)


def _add_request_step_from_graph(spec: FlowSpec, entry: dict[str, Any]) -> FlowStep:
    request_id = str(entry.get("request_id") or "")
    request_index = entry.get("request_index")
    existing = None
    entry_sig = _request_graph_signature(entry)
    for step in spec.steps:
        meta = step.source_meta or {}
        if request_id and str(meta.get("request_id") or "") == request_id:
            existing = step
            break
        if request_index is not None and meta.get("request_index") == request_index:
            existing = step
            break
        if not request_id and request_index is None and ((step.method or "").upper(), _request_path({"url": step.path or step.url})) == entry_sig:
            existing = step
            break
    if existing is None and not request_id and request_index is None:
        existing = next((
            s for s in spec.steps
            if ((s.method or "").upper(), _request_path({"url": s.path or s.url})) == entry_sig
        ), None)
    if existing is not None:
        _mark_request_selected(spec, entry, materialized_step_id=existing.step_id)
        return existing

    role = {
        "role": entry.get("role") or "read_context",
        "keep": True,
        "reason": "人工从捕获请求加入流程步骤",
        "confidence": entry.get("confidence") or 0.8,
        "evidence": entry.get("evidence") or {},
    }
    req = {
        "index": entry.get("request_index"),
        "request_id": entry.get("request_id"),
        "method": entry.get("method") or "GET",
        "url": entry.get("url") or entry.get("path") or "",
        "headers": entry.get("headers") or {},
        "content_type": entry.get("content_type") or "application/json",
        "post_data": entry.get("post_data"),
        "response_status": entry.get("response_status"),
        "response_json": entry.get("response_json"),
    }
    reads_for_candidate = [
        {"url": s.url or s.path, "json": s.response_json}
        for s in spec.steps
        if s.response_json is not None
    ]
    for item in _request_graph_items(spec):
        if item.get("response_json") is not None:
            reads_for_candidate.append({"url": item.get("url") or item.get("path") or "", "json": item.get("response_json")})
    st = _build_step_from_capture(
        _attach_request_role(req, role),
        reads=reads_for_candidate,
        samples={},
        storage_state=None,
        required_labels=set(),
        page_enum_options=_page_enum_options_from_request_facts(spec.request_facts),
        step_index=len(spec.steps),
    )
    st.path = _request_path(entry)
    _append_query_params_to_step(st, entry.get("url") or entry.get("path") or "")
    st.source_meta = {
        **(st.source_meta or {}),
        "manual_added": True,
        "request_index": entry.get("request_index"),
        "request_id": entry.get("request_id"),
        "page_id": entry.get("page_id"),
        "frame_id": entry.get("frame_id"),
        "sequence": entry.get("sequence"),
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    _insert_promoted_step(spec, st, entry)
    _mark_request_selected(spec, entry, materialized_step_id=st.step_id)
    return st


def promote_request_to_step(spec: FlowSpec, *, request_index: Any = None, request_id: str = "") -> FlowStep:
    """把 RequestGraph 事实提升为可执行 FlowStepTemplate。

    这是录制 V2 的唯一请求加入入口：手工加入、能力加入、自动修复和发布补齐都走这里。
    """
    entry = _find_request_graph_item(spec, request_index=request_index, request_id=request_id)
    if entry is None:
        raise ValueError(f"captured request not found: {request_index or request_id}")
    return _add_request_step_from_graph(spec, entry)


def _find_capability_index(spec: FlowSpec, edit: dict[str, Any]) -> int:
    if "capability_index" in edit:
        idx = int(edit.get("capability_index"))
        if 0 <= idx < len(spec.capabilities):
            return idx
        raise ValueError(f"capability index out of range: {idx}")
    name = str(edit.get("capability_name") or edit.get("name") or "")
    if name:
        for idx, cap in enumerate(spec.capabilities):
            if cap.name == name:
                return idx
    raise ValueError("capability not found")


def _find_select_binding(step: FlowStep, param: ParamField) -> SelectBinding | None:
    for sel in step.selects:
        if sel.path == param.path or sel.param == param.key or (sel.id_path and sel.id_path == param.path):
            return sel
    return None


def _bind_option_source(
    spec: FlowSpec,
    *,
    target_step_id: str,
    target_path: str,
    source_step_id: str = "",
    source_url: str = "",
    value_key: str = "",
    label_key: str = "",
    id_path: str = "",
    options: list[Any] | None = None,
    option_map: dict[str, Any] | None = None,
    multi: bool = False,
) -> None:
    step = _find_step(spec, target_step_id)
    param = _find_param(step, target_path)
    source_step = _find_step(spec, source_step_id) if source_step_id else None
    src_url = source_url or (source_step.path or source_step.url if source_step else "")
    if not src_url:
        raise ValueError("bind_option_source missing source_url/source_step")

    param.category = "user_param"
    param.source_kind = "api_option"
    param.type = "list-enum" if multi else "enum"
    param.exposed_to_user = True
    param.editable = True
    param.need_human_confirm = False
    param.source = {
        "kind": "api_option",
        "source_step_id": source_step_id,
        "source_url": src_url,
        "value_key": value_key,
        "label_key": label_key,
        "id_path": id_path or param.path,
    }
    param.reason = "字段候选来自接口选项源，调用方传显示值，运行期按 label/value 映射提交真实值"
    if options:
        param.enum_options = list(options)
    if option_map:
        param.enum_value_map = dict(option_map)
    param.evidence.append({
        "source": "option_source",
        "source_step_id": source_step_id,
        "source_url": src_url,
        "value_key": value_key,
        "label_key": label_key,
    })

    sel = _find_select_binding(step, param)
    if sel is None:
        sel = SelectBinding(param=param.key, path=param.path)
        step.selects.append(sel)
    sel.param = param.key
    sel.path = param.path
    sel.source_url = src_url
    sel.value_key = value_key or sel.value_key
    sel.label_key = label_key or sel.label_key
    sel.id_path = id_path or sel.id_path or param.path
    sel.multi = bool(multi)
    if options:
        sel.options = list(options)
        sel.count = len(options)
    if option_map:
        sel.option_map = dict(option_map)
    sel.enum_source = "api"
    sel.enum_confirmed = True
    _hydrate_select_source_contract(spec, sel)


def _set_capability_loop_source(cap: FlowCapability, items: str = "input.entries") -> None:
    items = str(items or "input.entries")
    existing_calls = (
        [n for n in cap.nodes if isinstance(n, dict)]
        if cap.nodes else
        [{"id": f"call_{idx}", "type": "call", "step_id": sid} for idx, sid in enumerate(cap.step_ids, 1)]
    )
    if not any(n.get("type") == "foreach" for n in existing_calls):
        call_nodes = [n for n in existing_calls if n.get("type") == "call"]
        cap.nodes = [{
            "id": "foreach_entries",
            "type": "foreach",
            "items": items,
            "steps": call_nodes,
        }]
    else:
        for node in _iter_capability_nodes(existing_calls):
            if node.get("type") == "foreach":
                node["items"] = items
                break
        cap.nodes = existing_calls
    cap.kind = "submit_batch" if cap.kind == "submit" else cap.kind
    cap.updated_by = "repair"


def _set_capability_return(cap: FlowCapability, mapping: list[dict[str, Any]]) -> None:
    cap.output_mapping = [dict(x) for x in mapping if isinstance(x, dict)]
    if cap.output_mapping and not any(n.get("type") == "return" for n in _iter_capability_nodes(cap.nodes or [])):
        first = cap.output_mapping[0]
        cap.nodes.append({
            "id": "return_result",
            "type": "return",
            "from": first.get("step_id") or first.get("from") or "",
            "path": first.get("response_path") or first.get("path") or "response",
        })
    cap.updated_by = "repair"


def _capability_bucket_for_scope(cap: FlowCapability, scope: str) -> list[CapabilityField]:
    if scope == "input":
        return cap.inputs
    if scope == "request_field":
        return cap.request_fields
    if scope == "internal":
        return cap.internal_fields
    if scope == "computed":
        return cap.computed_fields
    if scope == "output":
        return cap.outputs
    return cap.fields


def _field_match(a: CapabilityField, b: CapabilityField) -> bool:
    if a.field_id and b.field_id and a.field_id == b.field_id:
        return True
    if a.scope != b.scope:
        return False
    if a.step_id and b.step_id and a.step_id == b.step_id:
        if _strip_body_prefix(a.path or a.key) == _strip_body_prefix(b.path or b.key):
            return True
    if not a.step_id and not b.step_id and (a.key or a.path) and (b.key or b.path):
        return (a.key or a.path) == (b.key or b.path)
    return False


def _upsert_capability_field(cap: FlowCapability, data: dict[str, Any], *, default_scope: str) -> CapabilityField:
    raw = dict(data or {})
    raw.setdefault("scope", default_scope)
    raw.setdefault("locked", True)
    raw.setdefault("confirmed", True)
    field = CapabilityField.model_validate(raw)
    bucket = _capability_bucket_for_scope(cap, field.scope)
    for idx, existing in enumerate(bucket):
        if not _field_match(existing, field):
            continue
        merged = existing.model_dump()
        merged.update(field.model_dump(exclude_unset=True))
        bucket[idx] = CapabilityField.model_validate(merged)
        cap.updated_by = "repair"
        return bucket[idx]
    bucket.append(field)
    cap.updated_by = "repair"
    return field


def _upsert_capability_dependency(cap: FlowCapability, data: dict[str, Any]) -> CapabilityDependency:
    dep = CapabilityDependency.model_validate(dict(data or {}))
    dep_sig = (
        dep.dependency_id,
        str((dep.source or {}).get("step_id") or ""),
        str((dep.source or {}).get("path") or ""),
        str((dep.target or {}).get("step_id") or ""),
        str((dep.target or {}).get("path") or ""),
    )
    for idx, existing in enumerate(cap.dependencies or []):
        existing_sig = (
            existing.dependency_id,
            str((existing.source or {}).get("step_id") or ""),
            str((existing.source or {}).get("path") or ""),
            str((existing.target or {}).get("step_id") or ""),
            str((existing.target or {}).get("path") or ""),
        )
        if existing_sig[0] == dep_sig[0] or existing_sig[1:] == dep_sig[1:]:
            merged = existing.model_dump()
            merged.update(dep.model_dump(exclude_unset=True))
            cap.dependencies[idx] = CapabilityDependency.model_validate(merged)
            cap.updated_by = "repair"
            return cap.dependencies[idx]
    cap.dependencies.append(dep)
    cap.updated_by = "repair"
    return dep


def _upsert_global_link_from_capability_dependency(spec: FlowSpec, dep: CapabilityDependency) -> None:
    source = dep.source or {}
    target = dep.target or {}
    source_step_id = str(source.get("step_id") or "")
    target_step_id = str(target.get("step_id") or "")
    source_path = str(source.get("path") or "")
    target_path = str(target.get("path") or "")
    if not all([source_step_id, target_step_id, source_path, target_path]):
        return
    _find_step(spec, source_step_id)
    _find_step(spec, target_step_id)
    for link in spec.links:
        if (
            link.source_step_id == source_step_id
            and _strip_body_prefix(link.source_path) == _strip_body_prefix(source_path)
            and link.target_step_id == target_step_id
            and _strip_body_prefix(link.target_path) == _strip_body_prefix(target_path)
        ):
            link.confirmed = bool(dep.confirmed or link.confirmed)
            link.confidence = max(float(link.confidence or 0), float(dep.confidence or 0))
            link.reason = dep.reason or link.reason
            link.locked = bool(dep.locked or link.locked)
            return
    spec.links.append(FlowLink(
        source_step_id=source_step_id,
        source_path=source_path,
        target_step_id=target_step_id,
        target_path=target_path,
        confirmed=bool(dep.confirmed),
        confidence=float(dep.confidence or 0.75),
        reason=dep.reason or "能力级修复绑定的上游响应依赖",
        evidence=dep.evidence or {"source": "capability_dependency"},
        locked=bool(dep.locked),
    ))


def _upsert_capability_node(cap: FlowCapability, node_type: str, data: dict[str, Any]) -> dict[str, Any]:
    raw = dict(data or {})
    raw["type"] = node_type
    node_id = str(raw.get("id") or f"{node_type}_{len(cap.nodes or []) + 1}")
    raw["id"] = node_id
    for idx, node in enumerate(cap.nodes or []):
        if str(node.get("id") or "") == node_id:
            next_node = dict(node)
            next_node.update(raw)
            cap.nodes[idx] = next_node
            cap.updated_by = "repair"
            return next_node
    cap.nodes.append(raw)
    cap.updated_by = "repair"
    return raw


def _upsert_capability_relation(spec: FlowSpec, data: dict[str, Any]) -> CapabilityRelation:
    rel = _normalize_capability_relation_semantics(CapabilityRelation.model_validate(dict(data or {})))
    rel_sig = (
        rel.relation_id,
        rel.from_capability,
        rel.from_output,
        rel.to_capability,
        rel.to_input,
    )
    for idx, existing in enumerate(spec.capability_relations or []):
        existing_sig = (
            existing.relation_id,
            existing.from_capability,
            existing.from_output,
            existing.to_capability,
            existing.to_input,
        )
        if existing_sig[0] == rel_sig[0] or existing_sig[1:] == rel_sig[1:]:
            merged = existing.model_dump()
            merged.update(rel.model_dump(exclude_unset=True))
            spec.capability_relations[idx] = CapabilityRelation.model_validate(merged)
            return spec.capability_relations[idx]
    spec.capability_relations.append(rel)
    return rel


_CAPABILITY_ALLOWED_FIELDS = frozenset({
    "name", "title", "intent", "kind", "capability_id", "request_refs", "step_ids", "fields",
    "inputs", "request_fields", "internal_fields", "computed_fields", "outputs", "dependencies",
    "input_schema", "output_schema",
    "output_mapping", "preconditions", "confirmed", "confidence",
    "requires_human_confirm", "evidence", "caller_responsibilities", "skill_responsibilities",
    "nodes", "status", "locked", "updated_by",
})


def _hydrate_select_source_contract(spec: FlowSpec, binding: SelectBinding) -> None:
    """把界面选择的捕获接口补成可执行选项源，而不是只保存一个 URL。"""
    if not binding.source_url:
        return
    target_path = urlparse(binding.source_url).path.rstrip("/")
    candidates = [
        fact for fact in (spec.request_facts.requests or [])
        if (fact.url == binding.source_url)
        or (fact.path and fact.path.rstrip("/") == target_path)
        or (fact.url and urlparse(fact.url).path.rstrip("/") == target_path)
    ]
    if not candidates:
        return
    fact = next((item for item in reversed(candidates) if item.response_json is not None), candidates[-1])
    analysis = spec.request_facts.analysis.get(fact.request_id) if fact.request_id else None
    role = analysis.role if analysis is not None else ""
    safe_headers = {
        str(key): value for key, value in (fact.headers or {}).items()
        if str(key).lower() not in {
            "authorization", "cookie", "set-cookie", "x-auth-token", "x-access-token",
            "content-length", "host", "origin", "referer",
        }
    }
    binding.source_method = (fact.method or "GET").upper()
    binding.source_headers = safe_headers
    binding.source_body = fact.post_data
    binding.source_content_type = fact.content_type or ""
    binding.source_role = role
    binding.source_request_id = fact.request_id or ""


def apply_flow_edits(spec: FlowSpec, edits: list[dict[str, Any]]) -> FlowSpec:
    """应用编辑列表，返回新 FlowSpec（深拷贝）。"""
    if not edits:
        return refresh_review_items(spec.model_copy(deep=True))

    new_spec = spec.model_copy(deep=True)
    bulk_review_resolutions: list[tuple[set, set, bool]] = []
    needs_dependency_rebuild = False

    for edit in edits:
        op = edit.get("op")

        if op == "resolve_reviews":
            resolved = bool(edit.get("resolved", True))
            severities = set(edit.get("severities") or [])
            exclude_severities = set(edit.get("exclude_severities") or [])
            bulk_review_resolutions.append((severities, exclude_severities, resolved))
            generated = build_review_items(new_spec)
            old_by_id = {item.id: item for item in new_spec.review_items}
            for item in generated:
                if item.id in old_by_id:
                    item.resolved = old_by_id[item.id].resolved
                if severities and item.severity not in severities:
                    continue
                if exclude_severities and item.severity in exclude_severities:
                    continue
                item.resolved = resolved
            new_spec.review_items = generated
            continue

        if op == "resolve_review":
            item_id = str(edit.get("review_id") or "")
            if not item_id:
                raise ValueError("resolve_review missing review_id")
            found = False
            for item in new_spec.review_items:
                if item.id == item_id:
                    item.resolved = bool(edit.get("resolved", True))
                    found = True
                    break
            if not found:
                generated = build_review_items(new_spec)
                for item in generated:
                    if item.id == item_id:
                        item.resolved = bool(edit.get("resolved", True))
                        found = True
                        break
                if found:
                    new_spec.review_items = generated
            if not found:
                raise ValueError(f"review item not found: {item_id}")
            continue

        if op == "update_flow":
            field = str(edit.get("field") or "")
            value = edit.get("value")
            allowed = {"title", "business_description", "risk_level", "goal", "meta"}
            if field not in allowed:
                raise ValueError(f"unknown flow field: {field}")
            setattr(new_spec, field, value)
            continue

        if op == "dedupe_steps":
            _dedupe_flow_steps(new_spec)
            continue

        # 重排步骤
        if op == "reorder_steps":
            order = edit.get("step_ids")
            if not isinstance(order, list):
                raise ValueError("reorder_steps missing step_ids list")
            existing_ids = {s.step_id for s in new_spec.steps}
            new_order_ids = set(order)
            if existing_ids != new_order_ids or len(order) != len(new_spec.steps):
                raise ValueError(
                    f"reorder_steps must include exactly all existing step_ids; "
                    f"got {sorted(new_order_ids)}, expected {sorted(existing_ids)}"
                )
            by_id = {s.step_id: s for s in new_spec.steps}
            new_spec.steps = [by_id[sid] for sid in order]
            continue

        if op == "remove_step":
            step_id = str(edit.get("step_id") or "")
            if not step_id:
                raise ValueError("remove_step missing step_id")
            _remove_step(new_spec, step_id)
            continue

        if op in {"add_candidate_step", "add_request_step"}:
            request_index = edit.get("request_index")
            request_id = str(edit.get("request_id") or "")
            promote_request_to_step(new_spec, request_index=request_index, request_id=request_id)
            needs_dependency_rebuild = True
            continue

        if op == "generate_capabilities":
            existing = list(new_spec.capabilities or [])
            new_spec.capabilities = _merge_capability_lists(
                existing,
                build_default_flow_capabilities(new_spec),
                spec=new_spec,
                allow_new=not bool(existing),
            )
            new_spec.meta = {
                **(new_spec.meta or {}),
                "capability_model": {
                    "status": "ready",
                    "source": "deterministic",
                    "generated_count": len(new_spec.capabilities),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            }
            continue

        if op == "add_capability":
            raw = dict(edit.get("capability") or {})
            raw.setdefault("name", _flow_capability_id(str(raw.get("kind") or "submit"), str(len(new_spec.capabilities) + 1)))
            raw.setdefault("title", raw["name"])
            raw.setdefault("kind", "submit")
            try:
                cap = FlowCapability.model_validate(raw)
            except ValidationError as e:
                raise ValueError(f"invalid capability data: {e}")
            if any(c.name == cap.name for c in new_spec.capabilities):
                raise ValueError(f"duplicate capability name: {cap.name}")
            _forget_removed_capability(new_spec, cap.name, cap.kind)
            new_spec.capabilities.append(cap)
            continue

        if op == "remove_capability":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities.pop(idx)
            _remember_removed_capability(new_spec, cap.name, cap.kind)
            removed_refs = {str(cap.name or ""), str(cap.capability_id or "")}
            new_spec.capability_relations = [
                relation for relation in (new_spec.capability_relations or [])
                if str(relation.from_capability or "") not in removed_refs
                and str(relation.to_capability or "") not in removed_refs
            ]
            continue

        if op == "reorder_capabilities":
            refs = edit.get("capability_refs")
            if refs is None:
                refs = edit.get("capability_names")
            if not isinstance(refs, list):
                raise ValueError("reorder_capabilities missing capability_refs list")

            def cap_ref(cap: FlowCapability, idx: int) -> str:
                return str(cap.name or cap.capability_id or f"idx:{idx}")

            by_ref = {cap_ref(c, i): c for i, c in enumerate(new_spec.capabilities)}
            current = set(by_ref)
            requested = {str(x) for x in refs}
            if current != requested or len(refs) != len(new_spec.capabilities):
                raise ValueError(
                    f"reorder_capabilities must include exactly all capability refs; "
                    f"got {sorted(requested)}, expected {sorted(current)}"
                )
            new_spec.capabilities = [by_ref[str(ref)] for ref in refs]
            continue

        if op == "update_capability":
            idx = _find_capability_index(new_spec, edit)
            field = str(edit.get("field") or "")
            if field not in _CAPABILITY_ALLOWED_FIELDS:
                raise ValueError(f"unknown capability field: {field}")
            value = edit.get("value")
            cap = new_spec.capabilities[idx]
            if field == "name":
                value = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "")).strip("_").lower()
                if not value:
                    raise ValueError("capability name cannot be empty")
                if any(i != idx and c.name == value for i, c in enumerate(new_spec.capabilities)):
                    raise ValueError(f"duplicate capability name: {value}")
            if field in {"confirmed", "requires_human_confirm"}:
                value = bool(value)
            if field == "confidence":
                value = max(0.0, min(1.0, float(value or 0)))
            if field == "request_refs":
                value = [CapabilityRequestRef.model_validate(x) for x in (value or [])]
            if field in {"fields", "inputs", "request_fields", "internal_fields", "computed_fields", "outputs"}:
                value = [CapabilityField.model_validate(x) for x in (value or [])]
                scope_by_field = {
                    "inputs": "input", "request_fields": "request_field",
                    "internal_fields": "internal", "computed_fields": "computed",
                    "outputs": "output",
                }
                routed: list[CapabilityField] = []
                for item in value:
                    scope = scope_by_field.get(field, item.scope or "request_field")
                    if not _apply_capability_field_to_param(new_spec, item.model_dump(), scope=scope):
                        routed.append(item)
                value = routed
            if field == "dependencies":
                value = [CapabilityDependency.model_validate(x) for x in (value or [])]
            if field == "confirmed" and value:
                # Confirmation is a commit gate, not a switch that turns warnings
                # into errors after the user clicks it. Validate the candidate
                # contract first and leave state untouched on failure.
                candidate_spec = _sync_capability_io_schemas(sync_flow_spec_models(
                    new_spec.model_copy(deep=True), prefer_request_facts=False,
                ))
                candidate = candidate_spec.capabilities[idx]
                candidate.confirmed = True
                candidate.requires_human_confirm = False
                candidate.status = "confirmed"
                candidate_report = _capability_validation_report(candidate_spec)
                scoped = next((
                    item for item in (candidate_report.get("capabilities") or [])
                    if item.get("name") == candidate.name
                ), {})
                blockers = list(scoped.get("errors") or [])
                if blockers:
                    raise ValueError("能力确认失败: " + "；".join(blockers[:8]))
            setattr(cap, field, value)
            if field == "confirmed" and value:
                cap.requires_human_confirm = False
                cap.status = "confirmed"
                cap.confirmation_hash = _capability_confirmation_hash(new_spec, cap)
            elif field == "confirmed":
                cap.status = "draft"
                cap.confirmation_hash = ""
            elif field != "updated_by":
                cap.updated_by = "user"
                if field in {
                    "name", "title", "intent", "kind", "request_refs", "step_ids", "nodes",
                    "fields", "inputs", "request_fields", "internal_fields", "computed_fields",
                    "outputs", "dependencies", "input_schema", "output_schema", "output_mapping",
                    "preconditions", "caller_responsibilities", "skill_responsibilities",
                }:
                    cap.confirmed = False
                    cap.confirmation_hash = ""
                    cap.status = "draft"
                    cap.requires_human_confirm = True
            if field in {"step_ids", "nodes"}:
                _sync_capability_order(new_spec, cap)
            continue

        if op == "upsert_capability":
            raw = dict(edit.get("capability") or {})
            name = str(raw.get("name") or edit.get("capability_name") or edit.get("name") or "")
            if not name:
                raise ValueError("upsert_capability missing name")
            idx = next((i for i, c in enumerate(new_spec.capabilities) if c.name == name), -1)
            if idx < 0:
                raw.setdefault("name", name)
                raw.setdefault("title", raw["name"])
                raw.setdefault("kind", "submit")
                raw.setdefault("confidence", 0.7)
                raw.setdefault("requires_human_confirm", True)
                new_spec.capabilities.append(FlowCapability.model_validate(raw))
            else:
                cap = new_spec.capabilities[idx]
                for key, value in raw.items():
                    if key not in _CAPABILITY_ALLOWED_FIELDS:
                        continue
                    if key in {"fields", "inputs", "request_fields", "internal_fields", "computed_fields", "outputs"}:
                        value = [CapabilityField.model_validate(x) for x in (value or [])]
                    elif key == "dependencies":
                        value = [CapabilityDependency.model_validate(x) for x in (value or [])]
                    elif key == "request_refs":
                        value = [CapabilityRequestRef.model_validate(x) for x in (value or [])]
                    setattr(cap, key, value)
                cap.updated_by = "repair"
            continue

        if op in {
            "upsert_capability_field",
            "upsert_input_field",
            "upsert_request_field",
            "upsert_internal_field",
            "upsert_computed_field",
            "upsert_output_field",
        }:
            idx = _find_capability_index(new_spec, edit)
            default_scope = {
                "upsert_input_field": "input",
                "upsert_request_field": "request_field",
                "upsert_internal_field": "internal",
                "upsert_computed_field": "computed",
                "upsert_output_field": "output",
            }.get(op, str(edit.get("scope") or "request_field"))
            raw = dict(edit.get("field_data") or edit.get("field") or {})
            if "field" in edit and not isinstance(edit.get("field"), dict):
                raw["key"] = str(edit.get("field") or "")
            for alias in ("field_id", "key", "path", "step_id", "request_id", "request_index", "type", "source_kind"):
                if alias in edit and alias not in raw:
                    raw[alias] = edit.get(alias)
            if not _apply_capability_field_to_param(new_spec, raw, scope=default_scope):
                # Only capability-owned aggregate inputs/outputs are persisted on
                # FlowCapability. Step-bound fields are redirected to ParamField.
                _upsert_capability_field(new_spec.capabilities[idx], raw, default_scope=default_scope)
            new_spec.capabilities[idx].updated_by = str(edit.get("actor") or "user")
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op in {"add_request_to_capability", "add_capability_step"}:
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            step_id = str(edit.get("step_id") or "")
            if not step_id and ("request_index" in edit or edit.get("request_id")):
                step_id = promote_request_to_step(
                    new_spec,
                    request_index=edit.get("request_index"),
                    request_id=str(edit.get("request_id") or ""),
                ).step_id
                needs_dependency_rebuild = True
            step = _find_step(new_spec, step_id)
            usage = str(edit.get("usage") or "execute")
            origin = str(edit.get("origin") or edit.get("actor") or "manual")
            pinned = bool(edit.get("pinned", origin in {"manual", "user"}))
            _forget_removed_capability_step(new_spec, cap.name, step_id)
            _set_capability_request_membership(
                new_spec, cap, step, usage=usage, origin=origin, pinned=pinned,
            )
            if usage != "option_source":
                _add_step_id_to_capability(new_spec, cap, step_id)
            cap.updated_by = "user"
            _invalidate_capability_contract(cap)
            if usage != "option_source" and not any(n.get("type") == "call" and n.get("step_id") == step_id for n in (cap.nodes or [])):
                cap.nodes.append({"id": f"call_{len(cap.nodes or []) + 1}", "type": "call", "step_id": step_id})
            _sync_capability_order(new_spec, cap)
            continue

        if op in {"remove_request_from_capability", "remove_capability_step"}:
            idx = _find_capability_index(new_spec, edit)
            step_id = str(edit.get("step_id") or "")
            _remember_removed_capability_step(new_spec, new_spec.capabilities[idx].name, step_id)
            new_spec.capabilities[idx].step_ids = [sid for sid in new_spec.capabilities[idx].step_ids if sid != step_id]
            new_spec.capabilities[idx].request_refs = [
                ref for ref in new_spec.capabilities[idx].request_refs if ref.step_id != step_id
            ]
            new_spec.capabilities[idx].nodes = _remove_capability_step_nodes(
                new_spec.capabilities[idx].nodes or [], step_id,
            )
            new_spec.capabilities[idx].updated_by = "user"
            _invalidate_capability_contract(new_spec.capabilities[idx])
            _sync_capability_order(new_spec, new_spec.capabilities[idx])
            continue

        if op == "bind_dependency":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            raw = dict(edit.get("dependency") or {})
            raw.setdefault("type", edit.get("type") or "response_to_request")
            raw.setdefault("source", edit.get("source") or {
                "step_id": edit.get("source_step") or edit.get("source_step_id") or "",
                "path": edit.get("source_path") or "",
            })
            raw.setdefault("target", edit.get("target") or {
                "step_id": edit.get("target_step") or edit.get("target_step_id") or "",
                "path": edit.get("target_path") or "",
            })
            raw.setdefault("confirmed", bool(edit.get("confirmed", False)))
            raw.setdefault("locked", bool(edit.get("locked", False)))
            raw.setdefault("confidence", float(edit.get("confidence") or 0.75))
            raw.setdefault("reason", edit.get("reason") or "能力级修复绑定的依赖")
            dep = _upsert_capability_dependency(cap, raw)
            # 能力内依赖的两个端点必须同属该能力执行闭包；否则依赖视图会在下一次
            # 同步时被正确判为无效并丢弃，造成“刚绑定又消失”。
            for endpoint in (dep.source or {}, dep.target or {}):
                endpoint_step_id = str(endpoint.get("step_id") or "")
                if endpoint_step_id:
                    _find_step(new_spec, endpoint_step_id)
                    _add_step_id_to_capability(new_spec, cap, endpoint_step_id)
                    if not any(
                        n.get("type") == "call" and n.get("step_id") == endpoint_step_id
                        for n in _iter_capability_nodes(cap.nodes or [])
                        if isinstance(n, dict)
                    ):
                        cap.nodes.append({
                            "id": f"call_{len(cap.nodes or []) + 1}",
                            "type": "call",
                            "step_id": endpoint_step_id,
                        })
            _upsert_global_link_from_capability_dependency(new_spec, dep)
            _sync_capability_order(new_spec, cap)
            _invalidate_capability_contract(cap)
            continue

        if op in {"set_map", "set_condition"}:
            idx = _find_capability_index(new_spec, edit)
            node_type = "map" if op == "set_map" else "condition"
            raw = dict(edit.get("node") or {})
            if node_type == "map":
                raw.setdefault("source", edit.get("source") or "")
                raw.setdefault("target", edit.get("target") or "")
            else:
                raw.setdefault("condition", edit.get("condition") or edit.get("check") or "")
                for branch_key in ("then", "else", "steps", "children", "otherwise"):
                    if branch_key in edit and branch_key not in raw:
                        raw[branch_key] = edit[branch_key]
            if edit.get("node_id"):
                raw.setdefault("id", edit.get("node_id"))
            _upsert_capability_node(new_spec.capabilities[idx], node_type, raw)
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op == "set_output_mapping":
            idx = _find_capability_index(new_spec, edit)
            mapping = edit.get("mapping")
            if isinstance(mapping, dict):
                mapping = [mapping]
            if not isinstance(mapping, list):
                mapping = [{
                    "kind": edit.get("kind") or "final_response",
                    "step_id": edit.get("step_id") or edit.get("from") or "",
                    "response_path": edit.get("response_path") or edit.get("path") or "response",
                    "name": edit.get("name") or edit.get("field") or "",
                }]
            _set_capability_return(new_spec.capabilities[idx], mapping)
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op == "set_capability_relation":
            raw = dict(edit.get("relation") or {})
            for alias in ("type", "from_capability", "from_output", "to_capability", "to_input", "confidence", "confirmed", "reason"):
                if alias in edit and alias not in raw:
                    raw[alias] = edit.get(alias)
            raw.setdefault("requires_user_confirmation", bool(edit.get("requires_user_confirmation", True)))
            _upsert_capability_relation(new_spec, raw)
            refs = {str(raw.get("from_capability") or ""), str(raw.get("to_capability") or "")}
            for capability in new_spec.capabilities:
                if capability.name in refs or capability.capability_id in refs:
                    _invalidate_capability_contract(capability)
            continue

        if op == "bind_option_source":
            _bind_option_source(
                new_spec,
                target_step_id=str(edit.get("target_step") or edit.get("target_step_id") or edit.get("step_id") or ""),
                target_path=str(edit.get("target_path") or edit.get("param_path") or ""),
                source_step_id=str(edit.get("source_step") or edit.get("source_step_id") or ""),
                source_url=str(edit.get("source_url") or ""),
                value_key=str(edit.get("value_key") or ""),
                label_key=str(edit.get("label_key") or ""),
                id_path=str(edit.get("id_path") or ""),
                options=edit.get("options") if isinstance(edit.get("options"), list) else None,
                option_map=edit.get("option_map") if isinstance(edit.get("option_map"), dict) else None,
                multi=bool(edit.get("multi")),
            )
            _invalidate_capabilities_for_steps(new_spec, {
                str(edit.get("target_step") or edit.get("target_step_id") or edit.get("step_id") or "")
            })
            continue

        if op == "set_loop_source":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            items = str(edit.get("items") or edit.get("source") or "input.entries")
            _set_capability_loop_source(cap, items)
            cap.updated_by = str(edit.get("actor") or "user")
            _sync_capability_order(new_spec, cap)
            _invalidate_capability_contract(cap)
            continue

        if op == "set_return_mapping":
            idx = _find_capability_index(new_spec, edit)
            mapping = edit.get("mapping")
            if isinstance(mapping, dict):
                mapping = [mapping]
            if not isinstance(mapping, list):
                mapping = [{
                    "kind": edit.get("kind") or "final_response",
                    "step_id": edit.get("step_id") or edit.get("from") or "",
                    "response_path": edit.get("response_path") or edit.get("path") or "response",
                }]
            _set_capability_return(new_spec.capabilities[idx], mapping)
            new_spec.capabilities[idx].updated_by = str(edit.get("actor") or "user")
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op == "reject_dependency":
            link_id = str(edit.get("link_id") or "")
            if link_id:
                link = _find_link(new_spec, link_id)
                _record_rejected_dependency(new_spec, link)
                if link in new_spec.links:
                    new_spec.links.remove(link)
                continue
            source_step_id = str(edit.get("source_step_id") or edit.get("source_step") or "")
            source_path = str(edit.get("source_path") or "")
            target_step_id = str(edit.get("target_step_id") or edit.get("target_step") or "")
            target_path = str(edit.get("target_path") or "")
            if not all([source_step_id, source_path, target_step_id, target_path]):
                raise ValueError("reject_dependency missing link_id or source/target tuple")
            _record_rejected_dependency_raw(
                new_spec,
                source_step_id=source_step_id,
                source_path=source_path,
                target_step_id=target_step_id,
                target_path=target_path,
            )
            new_spec.links = [
                lk for lk in new_spec.links
                if _dependency_sig(lk.source_step_id, lk.source_path, lk.target_step_id, lk.target_path)
                not in _rejected_dependency_sigs(new_spec)
            ]
            continue

        # 链接编辑
        if edit.get("link_id"):
            link_id = edit["link_id"]
            if op == "update":
                link = _find_link(new_spec, link_id)
                field = edit.get("field")
                value = edit.get("value")
                if not field:
                    raise ValueError("link update missing field")
                if field == "confirmed":
                    link.confirmed = bool(value)
                elif field == "param_name":
                    link.param_name = str(value) if value is not None else None
                elif field == "source_path":
                    _validate_link_endpoint(new_spec, link.source_step_id, "source")
                    link.source_path = str(value)
                    link.source_tokens = None
                elif field == "target_path":
                    _validate_link_endpoint(new_spec, link.target_step_id, "target")
                    link.target_path = str(value)
                    link.target_tokens = None
                elif field == "source_step_id":
                    _validate_link_endpoint(new_spec, str(value), "source")
                    link.source_step_id = str(value)
                    link.source_tokens = None
                elif field == "target_step_id":
                    _validate_link_endpoint(new_spec, str(value), "target")
                    link.target_step_id = str(value)
                    link.target_tokens = None
                elif field == "link_id":                   # H19 修复:显式禁改 link_id(会被唯一性校验破坏)
                    raise ValueError("link_id is immutable")
                else:
                    # H19 修复:不再 hasattr 兜底(避免改 link_id/reason/internal 等关键字段)
                    raise ValueError(f"unknown link field: {field}")
                duplicate = _matching_link(new_spec, link)
                if duplicate is not None:
                    _merge_link(duplicate, link)
                    if link in new_spec.links:
                        new_spec.links.remove(link)
                continue

            if op == "remove":
                link = _find_link(new_spec, link_id)
                if edit.get("record_rejection", True):
                    _record_rejected_dependency(new_spec, link)
                new_spec.links.remove(link)
                continue

        # 添加链接
        if op == "add" and edit.get("link"):
            link_data = dict(edit["link"])
            link_data.setdefault("source_step_id", "")
            link_data.setdefault("target_step_id", "")
            link_data.setdefault("source_path", "")
            link_data.setdefault("target_path", "")
            _validate_link_endpoint(new_spec, link_data["source_step_id"], "source")
            _validate_link_endpoint(new_spec, link_data["target_step_id"], "target")
            try:
                new_link = FlowLink(**link_data)
            except ValidationError as e:
                raise ValueError(f"invalid link data: {e}")
            existing = _matching_link(new_spec, new_link)
            if existing is not None:
                _merge_link(existing, new_link)
                continue
            _ensure_unique_link(new_spec, new_link)
            new_spec.links.append(new_link)
            continue

        # 步骤/参数编辑
        step_id = edit.get("step_id")
        if not step_id:
            raise ValueError("edit missing step_id")

        step = _find_step(new_spec, step_id)

        if op == "update":
            param_path = edit.get("param_path")
            field = edit.get("field")
            value = edit.get("value")
            actor = str(edit.get("actor") or "user")

            if not field:
                raise ValueError("update edit missing field")

            if param_path:
                # 参数级编辑
                param = _find_param(
                    step,
                    param_path,
                    param_key=str(edit.get("param_key") or ""),
                    param_label=str(edit.get("param_label") or ""),
                )
                if field == "key":
                    old_key = param.key
                    param.key = str(value)
                    param.label = param.key
                    param.name_source = "manual"
                    param.locked = True
                    param.evidence.append({
                        "source": "manual_edit",
                        "field": "key",
                        "value": param.key,
                    })
                    if old_key in step.sample_inputs:
                        step.sample_inputs[param.key] = step.sample_inputs.pop(old_key)
                    for sb in step.selects:
                        if sb.path == param.path or sb.param == old_key:
                            sb.param = param.key
                elif field == "path":
                    old_path = param.path
                    new_path = str(value or "").strip()
                    if not new_path:
                        raise ValueError("param path cannot be empty")
                    if any(p is not param and p.path == new_path for p in step.params):
                        raise ValueError(f"duplicate param path: {new_path}")
                    param.path = new_path
                    for sb in step.selects:
                        if sb.path == old_path:
                            sb.path = new_path
                        if sb.id_path == old_path:
                            sb.id_path = new_path
                    for idn in step.identity:
                        if idn.path == old_path:
                            idn.path = new_path
                    for sv in step.system_values:
                        if sv.path == old_path:
                            sv.path = new_path
                    for lk in new_spec.links:
                        if lk.target_step_id == step.step_id and _strip_body_prefix(lk.target_path) == _strip_body_prefix(old_path):
                            lk.target_path = new_path
                    if isinstance(param.source, dict) and _strip_body_prefix(str(param.source.get("target_path") or "")) == _strip_body_prefix(old_path):
                        param.source["target_path"] = new_path
                elif field == "value":
                    param.value = str(value)
                    step.sample_inputs[param.key] = param.value
                elif field == "type":
                    _transition_param_type(new_spec, step, param, value)
                elif field == "required":
                    param.required = bool(value)
                elif field == "exposed_to_user":           # H22 修复:bool 字段显式 bool() 转换
                    param.exposed_to_user = bool(value)
                elif field == "editable":
                    param.editable = bool(value)
                elif field == "need_human_confirm":
                    param.need_human_confirm = bool(value)
                elif field in _PARAM_ALLOWED_FIELDS:
                    setattr(param, field, value)
                    if field == "category" and str(value) != "runtime_var":
                        _remove_param_incoming_links(new_spec, step, param)
                    elif field == "source_kind" and str(value) != "previous_response":
                        _remove_param_incoming_links(new_spec, step, param)
                    if (
                        actor == "user"
                        and (
                            (field == "source_kind" and str(value) not in _OPTION_SOURCE_KINDS)
                            or (field == "category" and str(value) != "user_param")
                        )
                    ):
                        step.selects = [
                            binding for binding in (step.selects or [])
                            if not (
                                _strip_body_prefix(binding.path or "") == _strip_body_prefix(param.path)
                                or _strip_body_prefix(binding.id_path or "") == _strip_body_prefix(param.path)
                                or binding.param in {param.key, param.label}
                            )
                        ]
                        param.enum_options = None
                        param.enum_value_map = None
                        param.source = {}
                        param.need_human_confirm = False
                    elif field == "source" and (
                        not isinstance(value, dict)
                        or str(value.get("kind") or "") != "previous_response"
                    ):
                        _remove_param_incoming_links(new_spec, step, param)
                    if field in {"label", "description"}:
                        param.name_source = "manual"
                        param.locked = True
                        param.evidence.append({
                            "source": "manual_edit",
                            "field": field,
                            "value": value,
                        })
                else:
                    # H19 修复:不再 hasattr 兜底(避免改 path/source_kind/internal 等关键字段)
                    raise ValueError(f"unknown param field: {field}")
                if actor == "user" and field in {
                    "key", "label", "description", "value", "type", "category", "source_kind", "source",
                    "required", "exposed_to_user", "editable", "need_human_confirm", "enum_options", "enum_value_map",
                }:
                    param.locked = True
                    param.evidence.append({
                        "source": "manual_edit",
                        "field": str(field),
                        "value": value,
                    })
                if field in {
                    "key", "path", "label", "description", "value", "type", "category", "source_kind",
                    "source", "required", "exposed_to_user", "editable", "need_human_confirm",
                    "enum_options", "enum_value_map",
                }:
                    _invalidate_capabilities_for_steps(new_spec, {step.step_id})
            else:
                # 步骤级编辑
                if field == "url":
                    step.url = str(value)
                elif field == "method":
                    step.method = str(value).upper()
                elif field == "headers":
                    step.headers = dict(value)
                elif field == "content_type":
                    step.content_type = str(value)
                elif field == "name":
                    step.name = str(value)
                elif field == "role":
                    role = str(value)
                    step.source_meta = {**(step.source_meta or {}), "role": role}
                    step.semantic_role = role
                elif field == "risk_level":
                    step.risk_level = str(value)
                elif field == "body_source":
                    step.body_source = str(value) if value is not None else ""
                elif field == "path":
                    step.path = str(value)
                    step.url = str(value)
                elif field == "step_id":                   # H19 修复:显式禁改 step_id
                    raise ValueError("step_id is immutable")
                elif field == "selects":
                    try:
                        step.selects = [SelectBinding.model_validate(x) for x in (value or [])]
                        for binding in step.selects:
                            _hydrate_select_source_contract(new_spec, binding)
                    except ValidationError as e:
                        raise ValueError(f"invalid selects data: {e}")
                elif field == "identity":
                    try:
                        step.identity = [IdentityBinding.model_validate(x) for x in (value or [])]
                    except ValidationError as e:
                        raise ValueError(f"invalid identity data: {e}")
                elif field == "params":
                    try:
                        step.params = [ParamField.model_validate(x) for x in (value or [])]
                    except ValidationError as e:
                        raise ValueError(f"invalid params data: {e}")
                elif field in _STEP_ALLOWED_FIELDS:
                    setattr(step, field, value)
                else:
                    # H19 修复:不再 hasattr 兜底
                    raise ValueError(f"unknown step field: {field}")
                if field in {
                    "url", "method", "headers", "content_type", "name", "role", "risk_level",
                    "body_source", "path", "selects", "identity", "params", "source_meta",
                    "semantic_role", "success_rule", "fact_check", "response_json",
                }:
                    _invalidate_capabilities_for_steps(new_spec, {step.step_id})
            continue

        elif op == "reset_param_source":
            param_path = edit.get("param_path")
            if not param_path:
                raise ValueError("reset_param_source missing param_path")
            param = _find_param(
                step,
                param_path,
                param_key=str(edit.get("param_key") or ""),
                param_label=str(edit.get("param_label") or ""),
            )
            target = str(edit.get("to") or "user_input")
            new_spec.links = [
                lk for lk in new_spec.links
                if not (lk.target_step_id == step.step_id and _strip_body_prefix(lk.target_path) == _strip_body_prefix(param.path))
            ]
            if target == "constant":
                param.category = "system_const"
                param.source_kind = "constant"
                param.source = {"kind": "constant", "path": param.path, "manual": True}
                param.editable = True
                param.exposed_to_user = False
                param.need_human_confirm = False
                param.reason = "已重置为系统固定值，发布后按当前录制值提交"
            else:
                _reset_param_source(param)
                step.sample_inputs[param.key] = param.value
            continue

        elif op == "add":
            param_data = edit.get("param")
            if not param_data:
                raise ValueError("add edit missing param")
            if "type" not in param_data and "value" in param_data:
                param_data["type"] = _infer_type_from_value(param_data["value"])
            try:
                new_param = ParamField(**param_data)
            except ValidationError as e:
                raise ValueError(f"invalid param data: {e}")
            step.params.append(new_param)
            if new_param.value:
                step.sample_inputs[new_param.key] = new_param.value
            continue

        elif op == "remove":
            param_path = edit.get("param_path")
            if not param_path:
                raise ValueError("remove edit missing param_path")
            param = _find_param(
                step,
                param_path,
                param_key=str(edit.get("param_key") or ""),
                param_label=str(edit.get("param_label") or ""),
            )
            step.params.remove(param)
            if param.key in step.sample_inputs:
                del step.sample_inputs[param.key]
            continue

        else:
            raise ValueError(f"unknown edit op: {op}")

    _sync_link_sources(new_spec.steps, new_spec.links)
    if needs_dependency_rebuild:
        rebuild_flow_dependencies(new_spec)
    if bulk_review_resolutions:
        generated = build_review_items(new_spec)
        old_by_id = {item.id: item for item in new_spec.review_items}
        for item in generated:
            if item.id in old_by_id:
                item.resolved = old_by_id[item.id].resolved
            for severities, exclude_severities, resolved in bulk_review_resolutions:
                if severities and item.severity not in severities:
                    continue
                if exclude_severities and item.severity in exclude_severities:
                    continue
                item.resolved = resolved
        new_spec.review_items = generated

    # 验证
    try:
        FlowSpec.model_validate(new_spec.model_dump())
    except ValidationError as e:
        raise ValueError(f"invalid spec after edits: {e}")

    actions = ",".join(str(e.get("op") or "edit") for e in edits)
    _normalize_capability_references(new_spec)
    return append_flow_version(
        refresh_review_items(_sync_capability_io_schemas(new_spec)),
        "flow_edit",
        reason=actions[:200],
        actor="user",
    )


_FLOW_AUTOFIX_SYSTEM = """你是录制型 Skill 的自动修正器。
只能输出 JSON: {"ops":[...]}。
不要输出完整 FlowSpec，不要编造 step_id/request_id/path。
证据规则：
- 字段重命名只能依据页面样例名、现有 label/evidence、请求路径和真实响应字段；证据不足保持原名。
- 绑定上游依赖必须是唯一、类型兼容且业务语义相同的 response -> request，不得因日期、标题、备注或 0/1 等常见值相同就关联。
- 绑定枚举必须使用 candidate_option_sources 的真实列表项，同时给出 value_key/label_key；内部 ID 不能直接暴露为用户输入。
- 已确认、locked 或用户修改过的字段/能力不得覆盖；removed_capabilities/removed steps 不得恢复。
允许操作:
- {"op":"promote_request","request_id":"...","request_index":1}
- {"op":"rename_field","step_id":"...","path":"...","label":"请假类型"}
- {"op":"bind_response_source","target_step":"...","target_path":"...","source_step":"...","source_path":"..."}
- {"op":"bind_option_source","target_step":"...","target_path":"...","source_step":"...","source_url":"...","value_key":"id","label_key":"name","id_path":"..."}
- {"op":"set_loop_source","capability":"...","items":"input.entries"}
- {"op":"set_return_mapping","capability":"...","mapping":[{"kind":"final_response","step_id":"...","response_path":"response"}]}
- {"op":"mark_field_as_system_var","step_id":"...","path":"..."}
- {"op":"mark_field_as_identity","step_id":"...","path":"...","source":"current_user"}
- {"op":"create_capability","name":"...","title":"...","kind":"query_status|list_options|validate_batch|submit_batch|submit","step_ids":[...],"nodes":[...]}
- {"op":"reorder_capability_steps","capability":"...","step_ids":[...]}
- {"op":"upsert_input_field","capability":"...","field":{"key":"...","type":"string|number|array|object","required":true}}
- {"op":"upsert_request_field","capability":"...","field":{"step_id":"...","path":"...","key":"...","type":"...","source_kind":"user_input|previous_response|api_option|constant"}}
- {"op":"upsert_output_field","capability":"...","field":{"key":"...","path":"...","type":"..."}}
- {"op":"bind_dependency","capability":"...","source":{"step_id":"...","path":"data.id"},"target":{"step_id":"...","path":"body.id"},"confidence":0.9}
- {"op":"set_map","capability":"...","node":{"id":"map_entries","source":"input.entries","target":"var.entries"}}
- {"op":"set_condition","capability":"...","node":{"id":"need_submit","condition":"input.entries.length > 0","then":[...]}}
- {"op":"set_output_mapping","capability":"...","mapping":[{"kind":"final_response","step_id":"...","response_path":"response"}]}
- {"op":"set_capability_relation","from_capability":"query_status","from_output":"missing_dates","to_capability":"submit_batch","to_input":"entries","confidence":0.8}
- {"op":"reject_dependency","link_id":"..."} 或 {"op":"reject_dependency","source_step":"...","source_path":"...","target_step":"...","target_path":"..."}
拿不准就不要改。"""


def _flow_autofix_context(spec: FlowSpec, report: dict[str, Any]) -> dict[str, Any]:
    graph = _request_graph_for_spec(spec)
    cap_validation = report.get("capability_validation") or {}
    option_sources: list[dict[str, Any]] = []
    for fact in (spec.request_facts.requests or []):
        if (fact.method or "").upper() != "GET":
            continue
        items = as_list_payload(fact.response_json)
        if not items:
            continue
        option_sources.append({
            "request_id": fact.request_id,
            "request_index": fact.request_index,
            "path": fact.path or fact.url,
            "sample_items": items[:20],
            "count": len(items),
        })
        if len(option_sources) >= 30:
            break
    return {
        "title": spec.title,
        "goal": spec.goal,
        "errors": list(report.get("errors") or [])[:40],
        "warnings": list(report.get("warnings") or [])[:40],
        "capability_validation": report.get("capability_validation") or {},
        "capability_findings": {
            "unused_high_confidence_requests": list(cap_validation.get("unused_high_confidence_requests") or [])[:80],
            "capability_internal": cap_validation.get("capability_internal") or {},
            "capability_relations": cap_validation.get("capability_relations") or {},
            "skill_level": cap_validation.get("skill_level") or {},
        },
        "steps": [
            {
                "step_id": st.step_id,
                "name": st.name,
                "method": st.method,
                "path": st.path or st.url,
                "params": [
                    {
                        "path": p.path,
                        "key": p.key,
                        "label": p.label,
                        "value": p.value,
                        "type": p.type,
                        "category": p.category,
                        "source_kind": p.source_kind,
                        "exposed_to_user": p.exposed_to_user,
                        "reason": p.reason,
                        "enum_options": list(p.enum_options or [])[:30],
                        "enum_value_map": dict(p.enum_value_map or {}),
                        "evidence": list(p.evidence or [])[:10],
                    }
                    for p in (st.params or [])[:60]
                ],
                "response_paths": [p for p, *_ in (_leaf_paths(st.response_json)[:80] if st.response_json is not None else [])],
                "selects": [sel.model_dump(exclude_none=True) for sel in (st.selects or [])[:20]],
            }
            for st in spec.steps
        ],
        "capabilities": [
            {
                **cap.model_dump(exclude_none=True),
                "contract": _capability_execution_contract(spec, cap),
            }
            for cap in spec.capabilities
        ],
        "request_graph": [
            {
                "request_id": r.get("request_id"),
                "request_index": r.get("request_index"),
                "method": r.get("method"),
                "path": r.get("path") or r.get("url"),
                "role": r.get("role"),
                "confidence": r.get("confidence"),
                "reason": r.get("reason"),
            }
            for r in (graph.get("all_requests") or [])[:120]
        ],
        "candidate_option_sources": option_sources,
    }


def _stable_json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _canonical_step_summary(step: FlowStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "name": step.name,
        "method": (step.method or "").upper(),
        "path": step.path or _request_path({"url": step.url}),
        "param_keys": [p.key or p.path for p in step.params],
        "param_types": {p.key or p.path: p.type for p in step.params},
        "select_count": len(step.selects or []),
        "response_hash": _stable_json_hash(step.response_json) if step.response_json is not None else "",
        "request_id": (step.source_meta or {}).get("request_id") or "",
        "request_index": (step.source_meta or {}).get("request_index"),
    }


def flow_spec_canonical_summary(spec: FlowSpec) -> dict[str, Any]:
    """Stable golden/shadow summary for recording V3 regression tests."""
    current = ensure_recorded_goal(_sync_capability_io_schemas(sync_flow_spec_models(
        spec.model_copy(deep=True),
        prefer_request_facts=False,
    )))
    request_facts = current.request_facts
    return {
        "protocol": "dano.recording_shadow.v1",
        "flow_id": current.flow_id,
        "title": current.title,
        "schema_version": current.schema_version,
        "risk_level": current.risk_level,
        "steps": [_canonical_step_summary(st) for st in current.steps],
        "links": [
            {
                "source_step_id": lk.source_step_id,
                "source_path": lk.source_path,
                "target_step_id": lk.target_step_id,
                "target_path": lk.target_path,
                "confirmed": bool(lk.confirmed),
            }
            for lk in current.links
        ],
        "request_facts": {
            "protocol": request_facts.protocol,
            "request_count": len(request_facts.requests or []),
            "analysis_count": len(request_facts.analysis or {}),
            "usage_count": len(request_facts.usage or {}),
            "requests": [
                {
                    "request_id": f.request_id,
                    "request_index": f.request_index,
                    "method": (f.method or "").upper(),
                    "path": f.path or _request_path({"url": f.url}),
                    "sequence": f.sequence,
                    "response_hash": _stable_json_hash(f.response_json) if f.response_json is not None else "",
                    "role": (request_facts.analysis.get(f.request_id or f"idx:{f.request_index}") or RequestAnalysis()).role,
                    "state": (request_facts.usage.get(f.request_id or f"idx:{f.request_index}") or RequestUsage()).state,
                }
                for f in sorted(
                    request_facts.requests or [],
                    key=lambda x: (str(x.sequence or ""), str(x.request_index or ""), x.request_id),
                )
            ],
        },
        "capabilities": [
            {
                "capability_id": cap.capability_id,
                "name": cap.name,
                "kind": cap.kind,
                "step_ids": list(cap.step_ids or []),
                "request_refs": [
                    {
                        "request_id": ref.request_id,
                        "request_index": ref.request_index,
                        "step_id": ref.step_id,
                        "role": ref.role,
                    }
                    for ref in cap.request_refs or []
                ],
                "input_keys": sorted(((cap.input_schema or {}).get("properties") or {}).keys()),
                "output_keys": sorted(((cap.output_schema or {}).get("properties") or {}).keys()),
                "field_count": len(cap.fields or []),
                "dependency_count": len(cap.dependencies or []),
                "node_types": [str(n.get("type") or "") for n in _iter_capability_nodes(cap.nodes or [])],
                "confirmed": bool(cap.confirmed),
            }
            for cap in current.capabilities or []
        ],
        "summary_hash": _stable_json_hash({
            "steps": [_canonical_step_summary(st) for st in current.steps],
            "links": [(lk.source_step_id, lk.source_path, lk.target_step_id, lk.target_path) for lk in current.links],
            "capabilities": [(cap.name, cap.kind, tuple(cap.step_ids or [])) for cap in current.capabilities or []],
            "request_ids": [f.request_id for f in request_facts.requests or []],
        }),
    }


def _autofix_ops_to_edits(
    spec: FlowSpec,
    ops: list[dict[str, Any]],
    *,
    allow_scope_changes: bool = True,
) -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    cap_by_name = {c.name: idx for idx, c in enumerate(spec.capabilities or []) if c.name}
    step_by_id = {step.step_id: step for step in spec.steps}

    def locked_param(step_id: str, path: str) -> bool:
        step = step_by_id.get(step_id)
        return bool(next((p for p in (step.params if step else []) if _strip_body_prefix(p.path) == _strip_body_prefix(path) and p.locked), None))

    for op in ops or []:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "")
        if kind == "promote_request":
            if not allow_scope_changes:
                continue
            edits.append({
                "op": "add_request_step",
                "request_id": str(op.get("request_id") or ""),
                "request_index": op.get("request_index"),
            })
        elif kind == "rename_field":
            step_id = str(op.get("step_id") or "")
            path = str(op.get("path") or "")
            label = str(op.get("label") or "").strip()
            if step_id and path and label and not locked_param(step_id, path):
                edits.append({"op": "update", "step_id": step_id, "param_path": path, "field": "key", "value": label})
        elif kind == "bind_response_source":
            source_step = str(op.get("source_step") or "")
            target_step = str(op.get("target_step") or "")
            source_path = str(op.get("source_path") or "")
            target_path = str(op.get("target_path") or "")
            if source_step and target_step and source_path and target_path and not locked_param(target_step, target_path):
                edits.append({
                    "op": "add",
                    "link": {
                        "source_step_id": source_step,
                        "source_path": source_path,
                        "target_step_id": target_step,
                        "target_path": target_path,
                        "confirmed": False,
                        "confidence": float(op.get("confidence") or 0.75),
                        "reason": str(op.get("reason") or "一键修正建议的上游响应绑定"),
                    },
                })
        elif kind == "bind_option_source":
            target_step = str(op.get("target_step") or op.get("target_step_id") or "")
            target_path = str(op.get("target_path") or op.get("path") or "")
            source_step = str(op.get("source_step") or op.get("source_step_id") or "")
            source_url = str(op.get("source_url") or "")
            if target_step and target_path and (source_step or source_url) and not locked_param(target_step, target_path):
                edits.append({
                    "op": "bind_option_source",
                    "target_step": target_step,
                    "target_path": target_path,
                    "source_step": source_step,
                    "source_url": source_url,
                    "value_key": str(op.get("value_key") or ""),
                    "label_key": str(op.get("label_key") or ""),
                    "id_path": str(op.get("id_path") or ""),
                    "options": op.get("options") if isinstance(op.get("options"), list) else None,
                    "option_map": op.get("option_map") if isinstance(op.get("option_map"), dict) else None,
                    "multi": bool(op.get("multi")),
                })
        elif kind == "set_loop_source":
            cap_name = str(op.get("capability") or op.get("name") or "")
            if cap_name in cap_by_name:
                edits.append({
                    "op": "set_loop_source",
                    "capability_index": cap_by_name[cap_name],
                    "items": str(op.get("items") or op.get("source") or "input.entries"),
                })
        elif kind == "set_return_mapping":
            cap_name = str(op.get("capability") or op.get("name") or "")
            if cap_name in cap_by_name:
                edits.append({
                    "op": "set_return_mapping",
                    "capability_index": cap_by_name[cap_name],
                    "mapping": op.get("mapping") if isinstance(op.get("mapping"), list) else op.get("mapping"),
                    "step_id": op.get("step_id"),
                    "response_path": op.get("response_path") or op.get("path"),
                })
        elif kind == "mark_field_as_system_var":
            step_id = str(op.get("step_id") or "")
            path = str(op.get("path") or "")
            if step_id and path and not locked_param(step_id, path):
                edits.extend([
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "category", "value": "runtime_var"},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "source_kind", "value": "unknown"},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "exposed_to_user", "value": False},
                ])
        elif kind == "mark_field_as_identity":
            step_id = str(op.get("step_id") or "")
            path = str(op.get("path") or "")
            source = str(op.get("source") or "current_user")
            if step_id and path and not locked_param(step_id, path):
                edits.extend([
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "category", "value": "runtime_var"},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "source_kind", "value": source},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "exposed_to_user", "value": False},
                ])
        elif kind == "create_capability":
            if not allow_scope_changes:
                continue
            if str(op.get("name") or "") in _removed_capability_names(spec):
                continue
            raw = {
                "name": op.get("name"),
                "title": op.get("title") or op.get("name"),
                "intent": op.get("intent") or "",
                "kind": op.get("kind") or "submit",
                "step_ids": op.get("step_ids") if isinstance(op.get("step_ids"), list) else [],
                "nodes": op.get("nodes") if isinstance(op.get("nodes"), list) else [],
                "confidence": float(op.get("confidence") or 0.7),
                "requires_human_confirm": True,
            }
            if raw["name"]:
                edits.append({"op": "add_capability", "capability": raw})
        elif kind == "reorder_capability_steps":
            cap_name = str(op.get("capability") or op.get("name") or "")
            step_ids = op.get("step_ids")
            if cap_name in cap_by_name and isinstance(step_ids, list):
                edits.append({
                    "op": "update_capability",
                    "capability_index": cap_by_name[cap_name],
                    "field": "step_ids",
                    "value": [str(x) for x in step_ids],
                })
        elif kind in {
            "upsert_capability",
            "upsert_capability_field",
            "upsert_input_field",
            "upsert_request_field",
            "upsert_internal_field",
            "upsert_computed_field",
            "upsert_output_field",
            "bind_dependency",
            "set_map",
            "set_condition",
            "set_output_mapping",
            "set_capability_relation",
            "add_request_to_capability",
            "remove_request_from_capability",
        }:
            if not allow_scope_changes and kind in {
                "upsert_capability", "add_request_to_capability", "remove_request_from_capability",
            }:
                continue
            cap_name = str(op.get("capability") or op.get("capability_name") or op.get("name") or "")
            edit = {k: v for k, v in op.items() if k != "op"}
            edit["op"] = kind
            if cap_name in cap_by_name:
                edit["capability_index"] = cap_by_name[cap_name]
            elif kind not in {"set_capability_relation", "upsert_capability"}:
                if not cap_name:
                    continue
                edit["capability_name"] = cap_name
            if "field" in op and isinstance(op.get("field"), dict):
                edit["field_data"] = op.get("field")
                edit.pop("field", None)
            edits.append(edit)
        elif kind == "reject_dependency":
            link_id = str(op.get("link_id") or "")
            source_step = str(op.get("source_step") or op.get("source_step_id") or "")
            source_path = str(op.get("source_path") or "")
            target_step = str(op.get("target_step") or op.get("target_step_id") or "")
            target_path = str(op.get("target_path") or "")
            if link_id or all([source_step, source_path, target_step, target_path]):
                edits.append({
                    "op": "reject_dependency",
                    "link_id": link_id,
                    "source_step": source_step,
                    "source_path": source_path,
                    "target_step": target_step,
                    "target_path": target_path,
                })
    return edits


def _auto_fix_target_capability_name(spec: FlowSpec) -> str:
    caps = list(spec.capabilities or build_default_flow_capabilities(spec))
    for kind in ("submit_batch", "submit", "query_status", "list_options", "validate_batch"):
        cap = next((c for c in caps if c.kind == kind and c.name), None)
        if cap is not None:
            return cap.name
    return caps[0].name if caps else "submit_batch"


def _capability_sequence_window(spec: FlowSpec, cap: FlowCapability) -> tuple[float | None, float | None]:
    by_id = {s.step_id: s for s in spec.steps}
    values = [
        seq for seq in (
            _step_sequence(by_id[sid])
            for sid in _capability_node_step_ids(cap)
            if sid in by_id
        )
        if seq is not None
    ]
    if not values:
        return None, None
    return min(values), max(values)


def _auto_fix_target_capability_for_request(spec: FlowSpec, item: dict[str, Any]) -> str:
    """Choose the capability that should own a newly promoted captured request."""
    caps = list(spec.capabilities or build_default_flow_capabilities(spec))
    if not caps:
        return "submit_batch"
    role = str(item.get("role") or "")
    method = str(item.get("method") or "").upper()
    seq = _entry_sequence(item)

    def cap_score(cap: FlowCapability) -> float:
        score = 0.0
        if cap.kind in {"submit_batch", "submit"}:
            if role in {"submit_anchor", "business_write"} or method in _WRITE_METHODS:
                score += 90
            elif role in {"business_get", "read_context"}:
                score += 45
            elif role == "read_option":
                score += 20
        elif cap.kind == "query_status":
            if role in {"business_get", "read_context"} and method not in _WRITE_METHODS:
                score += 75
        elif cap.kind == "list_options":
            if role == "read_option":
                score += 85
        elif cap.kind == "validate_batch":
            if role in {"business_get", "read_context"}:
                score += 55

        start, end = _capability_sequence_window(spec, cap)
        if seq is not None and start is not None and end is not None:
            if start <= seq <= end:
                score += 35
            elif seq < start:
                distance = start - seq
                score += max(0, 24 - min(distance, 24))
            else:
                distance = seq - end
                score += max(0, 16 - min(distance, 16))
        if cap.confirmed:
            score += 3
        score += float(cap.confidence or 0)
        return score

    best = max(caps, key=cap_score)
    if best.name:
        return best.name
    return _auto_fix_target_capability_name(spec)


def _deterministic_capability_repair_edits(spec: FlowSpec, report: dict[str, Any]) -> list[dict[str, Any]]:
    """P2 能力级确定性修复。

    这层只补“结构必需但可确定”的编排内容，语义判断仍交给 LLM/人工：
    - submit_batch 缺 foreach 时补 input.entries 循环；
    - 批量写接口必填字段缺 map 时补 item.<key> -> step.path；
    - 缺 output_mapping 时补最后一个 call 的 response。
    """
    edits: list[dict[str, Any]] = []
    step_by_id = {s.step_id: s for s in spec.steps}
    for cap in spec.capabilities or []:
        if not cap.name or (cap.confirmed and cap.locked):
            continue
        cap_step_ids = _capability_node_step_ids(cap)
        cap_steps = [step_by_id[sid] for sid in cap_step_ids if sid in step_by_id]
        if not cap_steps:
            continue
        flat_nodes = _iter_capability_nodes(cap.nodes or [])
        has_foreach = any(n.get("type") == "foreach" for n in flat_nodes if isinstance(n, dict))
        is_batch = _capability_is_batch(spec, cap)
        if is_batch and not has_foreach:
            edits.append({"op": "set_loop_source", "capability_name": cap.name, "items": "input.entries"})

        existing_map_targets = {
            str(n.get("target") or "")
            for n in flat_nodes
            if isinstance(n, dict) and n.get("type") == "map"
        }
        if is_batch:
            for st in cap_steps:
                if (st.method or "").upper() not in _WRITE_METHODS and not _looks_batch_step(st):
                    continue
                for param in st.params or []:
                    if not param.required:
                        continue
                    target = f"{st.step_id}.{param.path}"
                    if target in existing_map_targets:
                        continue
                    key = param.key or _strip_body_prefix(param.path).split(".")[-1].strip("[]") or "value"
                    if param.category == "runtime_var" and param.source_kind == "previous_response":
                        continue
                    edits.append({
                        "op": "set_map",
                        "capability_name": cap.name,
                        "node": {
                            "id": f"map_{re.sub(r'[^a-zA-Z0-9_]+', '_', key).strip('_') or 'field'}",
                            "source": f"item.{key}",
                            "target": target,
                        },
                    })
                    existing_map_targets.add(target)

        if not cap.output_mapping:
            final = next((st for st in reversed(cap_steps) if (st.method or "").upper() in _WRITE_METHODS), cap_steps[-1])
            edits.append({
                "op": "set_output_mapping",
                "capability_name": cap.name,
                "mapping": [{
                    "kind": "final_response",
                    "step_id": final.step_id,
                    "response_path": "response",
                    "name": "result",
                }],
            })
    return edits


async def auto_fix_flow_spec(
    spec: FlowSpec,
    *,
    llm_client: Any | None = None,
    model: str | None = None,
    timeout_s: float = 60.0,
    max_rounds: int = 3,
    expand_requests: bool = True,
    allow_scope_changes: bool | None = None,
    strict_incremental: bool = False,
) -> FlowSpec:
    """一键修正：确定性补齐 + 可选 LLM 受限 patch + 重新校验。"""
    current = spec.model_copy(deep=True)
    if allow_scope_changes is None:
        allow_scope_changes = expand_requests
    _normalize_capability_references(current)
    history: list[dict[str, Any]] = []
    for round_idx in range(max_rounds):
        report = validate_flow_spec(current)
        edits: list[dict[str, Any]] = []
        if not current.capabilities and current.steps:
            edits.append({"op": "generate_capabilities"})
        cap_report = report.get("capability_validation") or {}
        edits.extend(_deterministic_capability_repair_edits(current, report))
        for item in (cap_report.get("unused_high_confidence_requests") or []) if expand_requests else []:
            role = item.get("role") or ""
            if role not in {"submit_anchor", "business_write", "business_get", "read_context", "read_option"}:
                continue
            if not current.capabilities and not current.steps:
                edits.append({
                    "op": "add_request_step",
                    "request_id": item.get("request_id") or "",
                    "request_index": item.get("request_index"),
                })
                continue
            edits.append({
                "op": "add_capability_step",
                "capability_name": _auto_fix_target_capability_for_request(current, item),
                "request_id": item.get("request_id") or "",
                "request_index": item.get("request_index"),
            })
        if llm_client is not None and model:
            try:
                out = await llm_client.complete_json(
                    model=model,
                    system=_FLOW_AUTOFIX_SYSTEM,
                    user="【录制 FlowSpec 修复上下文】\n" + json.dumps(_flow_autofix_context(current, report), ensure_ascii=False),
                    timeout_s=timeout_s,
                )
                raw_ops = out.get("ops") if isinstance(out, dict) else None
                if isinstance(raw_ops, list):
                    llm_edits = _autofix_ops_to_edits(
                        current,
                        raw_ops,
                        allow_scope_changes=bool(allow_scope_changes),
                    )
                    if not allow_scope_changes:
                        llm_edits = _planner_patch_edits(current, llm_edits, scope_locked=True)
                    edits.extend(llm_edits)
            except Exception:  # noqa: BLE001
                pass
        if not edits:
            history.append({"round": round_idx, "applied": 0, "remaining_errors": len(report.get("errors") or [])})
            break
        before = _flow_fingerprint(current)
        current = apply_flow_edits(current, [{**edit, "actor": "repair"} for edit in edits])
        current.meta = {
            **(current.meta or {}),
            "auto_fix": {
                "round": round_idx + 1,
                "last_edits": edits[:50],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        after = _flow_fingerprint(current)
        history.append({"round": round_idx, "applied": len(edits), "changed": before != after})
        if before == after:
            break
        if validate_flow_spec(current).get("passed"):
            break
    current.meta = {**(current.meta or {}), "auto_fix_history": history}
    if allow_scope_changes or not strict_incremental:
        current = _repair_generated_capability_contracts(current)
    current = _sync_capability_io_schemas(current)
    if strict_incremental and not allow_scope_changes and spec.capabilities:
        current = _enforce_incremental_orchestration_scope(spec, current)
    return append_flow_version(refresh_review_items(current), "auto_fix", reason="一键自动修正")


def _auto_confirm_ready_capabilities(spec: FlowSpec) -> FlowSpec:
    """仅对证据完整、无待确认字段/依赖的高置信能力自动确认。"""
    _normalize_capability_references(spec)
    by_step = {step.step_id: step for step in spec.steps}
    for cap in spec.capabilities or []:
        if cap.confirmed:
            continue
        if float(cap.confidence or 0) < 0.9:
            continue
        step_ids = _capability_node_step_ids(cap)
        cap_steps = [by_step[sid] for sid in step_ids if sid in by_step]
        if not cap_steps:
            continue
        unsafe = False
        for step in cap_steps:
            for param in step.params or []:
                if param.need_human_confirm:
                    unsafe = True
                    break
                if param.category == "runtime_var" and param.source_kind == "unknown":
                    unsafe = True
                    break
                if _param_looks_exposed_internal_value(param):
                    unsafe = True
                    break
                if _capability_param_enum_issue(param):
                    unsafe = True
                    break
            if unsafe:
                break
        if unsafe:
            continue
        scoped = set(step_ids)
        if any(
            not link.confirmed
            for link in spec.links
            if link.source_step_id in scoped and link.target_step_id in scoped
        ):
            continue
        candidate_spec = spec.model_copy(deep=True)
        candidate = next((
            item for item in candidate_spec.capabilities
            if item.capability_id == cap.capability_id
        ), None)
        if candidate is None:
            continue
        candidate.confirmed = True
        candidate.requires_human_confirm = False
        candidate.status = "confirmed"
        candidate_report = _capability_validation_report(candidate_spec)
        scoped_report = next((
            item for item in (candidate_report.get("capabilities") or [])
            if item.get("name") == candidate.name
        ), {})
        if scoped_report.get("errors"):
            continue
        cap.confirmed = True
        cap.requires_human_confirm = False
        cap.status = "confirmed"
        cap.updated_by = "planner"
        cap.confirmation_hash = _capability_confirmation_hash(spec, cap)
    return spec


async def run_recording_pi_loop(
    spec: FlowSpec,
    *,
    llm_client: Any | None = None,
    model: str | None = None,
    mode: str = "plan",
    timeout_s: float = 60.0,
    max_rounds: int = 4,
) -> FlowSpec:
    """录制路径 PI 闭环：Goal → Planner → Validator → Repair → 再验证。

    这不是单次“生成编排”。它会基于 RecordedGoal、RequestGraph 和当前人工编辑，
    在有限轮次内规划、校验、修复并重新校验，直到通过或无法继续收敛。
    """
    current = ensure_recorded_goal(spec.model_copy(deep=True))
    incremental_baseline = current.model_copy(deep=True) if current.capabilities and mode == "plan" else None
    _normalize_capability_references(current)
    history: list[dict[str, Any]] = []
    run_planner = mode == "plan" or not current.capabilities

    for round_idx in range(max_rounds):
        before = _flow_fingerprint(current)
        if run_planner:
            current = await orchestrate_flow_capabilities(
                current,
                llm_client=llm_client,
                model=model,
                timeout_s=timeout_s,
            )

        report = validate_flow_spec(current)
        history.append({
            "round": round_idx + 1,
            "stage": "planner" if run_planner else "validator",
            "passed": bool(report.get("passed")),
            "errors": len(report.get("errors") or []),
            "warnings": len(report.get("warnings") or []),
        })
        # plan 模式首次生成后仍执行一轮受限 Repair：能力校验可能已通过，但字段名、
        # 枚举来源和输出映射仍可由 LLM 依据完整请求事实补强。后续轮次通过即停止，
        # 避免重复点击时无边界改写人工内容。
        needs_quality_repair = bool(
            mode == "plan"
            and round_idx == 0
            and llm_client is not None
            and model
        )
        # repair 按钮的职责是处理可修复建议，不只是处理 blocking errors。
        # 过去 passed=True 会让 warning-only 的错误编排直接提前退出，导致按钮无效果。
        needs_requested_repair = bool(
            mode == "repair"
            and round_idx == 0
            and (
                report.get("warnings")
                or report.get("review_items")
                or report.get("issue_groups")
            )
        )
        if report.get("passed") and not needs_quality_repair and not needs_requested_repair:
            break

        current = await auto_fix_flow_spec(
            current,
            llm_client=llm_client,
            model=model,
            timeout_s=timeout_s,
            max_rounds=1,
            expand_requests=False,
            allow_scope_changes=False,
            strict_incremental=incremental_baseline is not None,
        )
        fixed_report = validate_flow_spec(current)
        history.append({
            "round": round_idx + 1,
            "stage": "repair",
            "passed": bool(fixed_report.get("passed")),
            "errors": len(fixed_report.get("errors") or []),
            "warnings": len(fixed_report.get("warnings") or []),
        })
        after = _flow_fingerprint(current)
        if fixed_report.get("passed") or before == after:
            break
        run_planner = True

    current = _auto_confirm_ready_capabilities(
        _sync_capability_io_schemas(sync_flow_spec_models(current, prefer_request_facts=False))
    )
    if incremental_baseline is not None:
        current = _enforce_incremental_orchestration_scope(incremental_baseline, current)
    if not str(current.business_description or "").strip():
        current.business_description = render_business_description(current, llm_client=llm_client)
        current.meta = {
            **(current.meta or {}),
            "business_description_source": "recording_pi_loop",
        }
    current.meta = {
        **(current.meta or {}),
        "recording_pi_loop": {
            "mode": mode,
            "rounds": history,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    return append_flow_version(
        refresh_review_items(_sync_capability_io_schemas(current)),
        "recording_pi_loop",
        reason=f"录制 PI 闭环: {mode}",
        actor="planner",
    )


def _looks_internal(name: str) -> bool:
    return looks_internal_param_name(name) if name else False


def _classify_field_category(key: str, path: str, value: str) -> str:
    """Step D: 三类字段自动分类。

    优先级: runtime_var > system_const > user_param

    - runtime_var: 运行期变量 (taskId, draftId, token, createTime 等)
    - system_const: 系统常量 (billType, formType, processDefinitionKey 等)
    - user_param: 用户参数 (默认)
    """
    k = key.lower()

    # 1. runtime_var: 运行期变量 (最高优先级)
    runtime_var_patterns = [
        "taskid", "draftid", "instanceid", "processinstanceid",
        "token", "accesstoken", "refreshtoken", "jsessionid",
        "createtime", "updatetime", "submittime", "modifytime",
        "createby", "updateby", "operator",
    ]
    if k in runtime_var_patterns or any(x in k for x in runtime_var_patterns):
        return "runtime_var"

    # 2. system_const: 系统常量
    system_const_patterns = [
        "processdefinitionkey", "processdefinitionid", "billtype", "formtype",
        "flowtype", "flowstatus", "businesstype",
        "applytype", "leavetype", "reimbursetype", "expense_type",
        "template_id", "formid", "menuid",
    ]
    if k in system_const_patterns or any(x in k for x in system_const_patterns):
        return "system_const"

    # 3. user_param: 用户参数 (默认)
    return "user_param"


# ─────────── Step D: GET 表单手选 ───────────
def flow_spec_for_get_form(
    reads: list[dict],
    *,
    tenant: str = "",
    subsystem: str = "",
) -> FlowSpec:
    steps = []
    for idx, r in enumerate(reads[:5]):
        try:
            step = _build_step_from_capture(
                r, reads=[], samples={}, storage_state=None,
                required_labels=set(), page_enum_options={}, step_index=idx,
            )
            step.name = f"读#{idx+1} {step.path or '(无路径)'}"
            steps.append(step)
        except Exception:
            continue
    return ensure_flow_version(refresh_review_items(FlowSpec(
        tenant=tenant,
        subsystem=subsystem,
        title="(GET 表单待选)",
        steps=steps,
        links=[],
        meta={
            "step_d": True,
            "reads_count": len(reads),
            "note": "GET 表单没有写操作，从左侧抓到的读接口中选一条作主流程",
        },
    )), "get_form_candidates", reason="生成 GET 表单候选 FlowSpec")


def pick_manual(spec: FlowSpec, picked_step_id: str) -> FlowSpec:
    target = None
    for st in spec.steps:
        if st.step_id == picked_step_id:
            target = st
            break
    if target is None:
        raise ValueError(f"step not found: {picked_step_id}")

    new_steps = [target] + [s for s in spec.steps if s.step_id != picked_step_id]
    new_links = [lk for lk in spec.links if lk.source_step_id == picked_step_id]

    new_spec = spec.model_copy(deep=True)
    new_spec.steps = new_steps
    new_spec.links = new_links
    new_spec.title = f"{target.name or _default_step_name({'url': target.url, 'method': target.method})}(GET)"
    new_spec.risk_level = "L1"
    new_spec.meta = {**(spec.meta or {}), "picked_step_id": picked_step_id, "manual_pick": True}
    return append_flow_version(refresh_review_items(new_spec), "manual_pick", reason=f"手选步骤 {picked_step_id}", actor="user")


# ─────────── Step D: LLM 命名 + 业务说明 ───────────
def _derive_step_name(step: FlowStep) -> str:
    url = step.url or step.path
    try:
        path = urlparse(url).path if url.startswith("http") else url
    except Exception:
        path = step.path
    segs = [s for s in (path or "").split("/") if s]
    last = segs[-1].split("?")[0] if segs else ""
    method = (step.method or "POST").upper()
    if not last:
        return f"{method}_未命名"
    if step.params:
        return f"{method}_{last}(含{len(step.params)}字段)"
    return f"{method}_{last}"


def rename_steps_with_llm(spec: FlowSpec, *, llm_client: Any | None = None) -> FlowSpec:
    new_spec = spec.model_copy(deep=True)
    for i, st in enumerate(new_spec.steps):
        if llm_client is not None:
            try:
                ctx = {
                    "method": st.method,
                    "path": st.path,
                    "params": [{"key": p.key, "type": p.type} for p in st.params[:10]],
                    "selects_count": len(st.selects),
                    "system_values_count": len(st.system_values),
                    "risk_level": st.risk_level,
                    "semantic_role": st.semantic_role,
                }
                named = llm_client.name_step(ctx)
                if isinstance(named, str) and named.strip():
                    st.name = named.strip()[:60]
                    continue
            except Exception:
                pass
        st.name = _derive_step_name(st)
    return append_flow_version(refresh_review_items(new_spec), "step_naming", reason="生成或刷新步骤名称")


def _description_param_key(param: ParamField) -> str:
    return param.label or param.key or param.path


def _description_source_text(param: ParamField) -> str:
    source = param.source or {}
    kind = param.source_kind or "unknown"
    if kind == "previous_response":
        step = source.get("step_name") or source.get("step_id") or "前置步骤"
        path = source.get("response_path") or "响应字段"
        return f"来自 {step} 的 {path}"
    if kind == "current_user":
        return "运行期从当前登录态读取"
    if kind == "request_header":
        header = source.get("header") or "请求头"
        return f"运行期从请求头 {header} 读取"
    if kind == "system_time":
        return "运行期由系统时间生成"
    if kind == "system_generated":
        labels = {"uuid": "UUID", "random_string": "随机字符串", "random_number": "随机数字"}
        strategy = str(source.get("strategy") or "uuid")
        return f"运行期由系统生成 {labels.get(strategy, strategy)}"
    if kind == "computed":
        return "运行期根据其它调用参数自动计算"
    if kind == "page_context":
        return "运行期从页面/应用上下文读取"
    if kind == "api_option":
        return "来自接口候选源"
    if kind == "page_enum":
        return "来自录制页面固定下拉"
    if kind == "manual_enum":
        return "来自人工维护枚举"
    if kind == "static_enum":
        return "来自固定枚举候选"
    if kind == "form_option":
        return "来自选择型字段"
    if kind == "constant":
        return "录制流程内固定值"
    if kind == "user_input":
        return "来自用户录制输入"
    return "来源待确认"


def _description_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value)
    return text if len(text) <= 80 else f"{text[:77]}..."


def _description_rule(rule: dict[str, Any] | None) -> str:
    if not rule:
        return "使用通用 HTTP/响应成功判断"
    try:
        text = json.dumps(rule, ensure_ascii=False, default=str)
    except Exception:
        text = str(rule)
    return text if len(text) <= 160 else f"{text[:157]}..."


def _unique_params(spec: FlowSpec, category: str) -> list[tuple[FlowStep, ParamField]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[FlowStep, ParamField]] = []
    for st in spec.steps:
        for p in st.params:
            if p.category != category:
                continue
            key = (p.key or p.path, p.source_kind or "")
            if key in seen:
                continue
            seen.add(key)
            out.append((st, p))
    return out


def _llm_purpose(spec: FlowSpec, llm_client: Any | None) -> str:
    if llm_client is None:
        return ""
    try:
        ctx = {
            "title": spec.title,
            "steps": [
                {
                    "name": s.name or _derive_step_name(s),
                    "method": s.method,
                    "path": s.path,
                    "params": [{"key": p.key, "category": p.category, "source_kind": p.source_kind} for p in s.params[:20]],
                    "risk_level": s.risk_level,
                }
                for s in spec.steps
            ],
            "links": [
                {
                    "source_path": l.source_path,
                    "target_path": l.target_path,
                    "confirmed": l.confirmed,
                }
                for l in spec.links
            ],
            "risk_level": spec.risk_level,
            "review_items_count": len(spec.review_items),
        }
        desc = llm_client.summarize_flow(ctx)
        if isinstance(desc, dict):
            text = desc.get("purpose") or desc.get("summary") or desc.get("title") or ""
        else:
            text = desc if isinstance(desc, str) else ""
        text = text.strip()
        if text:
            return text[:240]
    except Exception:
        pass
    return ""


def _default_purpose(spec: FlowSpec) -> str:
    if not spec.steps:
        return "本流程未包含任何操作步骤，暂不能生成可执行 Skill。"
    title = _title_without_step_suffix(spec.title) or (spec.steps[-1].name or _derive_step_name(spec.steps[-1]))
    return (
        f"该 Skill 用于按录制得到的 {len(spec.steps)} 个步骤执行「{title}」，"
        "并在运行期重新解析用户参数、系统常量和接口依赖。"
    )


def render_business_description(spec: FlowSpec, *, llm_client: Any | None = None) -> str:
    """生成结构化业务说明。

    事实字段全部来自 FlowSpec；LLM 只允许提供业务目的文案，不覆盖参数、依赖和风险。
    """
    current = refresh_review_items(_sync_capability_io_schemas(spec.model_copy(deep=True)))
    lines: list[str] = [
        "# 业务流程说明",
        "",
        "## 1. 业务目的",
        _llm_purpose(current, llm_client) or _default_purpose(current),
        "",
        "## 对外业务能力",
    ]

    if current.capabilities:
        by_id = {s.step_id: s for s in current.steps}
        for i, cap in enumerate(current.capabilities, 1):
            kind_label = {
                "query_status": "状态查询",
                "list_options": "选项列表",
                "validate_batch": "批量校验",
                "submit_batch": "批量提交",
                "submit": "提交",
            }.get(cap.kind, cap.kind)
            status = "已确认" if cap.confirmed else "未确认"
            lines.append(f"{i}. {cap.title or cap.name}（{kind_label}，{status}）")
            if cap.intent:
                lines.append(f"   - 说明：{cap.intent}")
            cap_steps = [by_id[sid] for sid in (cap.step_ids or []) if sid in by_id]
            if cap_steps:
                chain = " -> ".join(f"{st.method} {st.path or st.url}" for st in cap_steps)
                lines.append(f"   - 接口链：`{chain}`")
            props = (cap.input_schema or {}).get("properties") or {}
            required = set((cap.input_schema or {}).get("required") or [])
            if props:
                fields = []
                for key, schema in list(props.items())[:20]:
                    typ = schema.get("type") if isinstance(schema, dict) else "string"
                    req = "必填" if key in required else "可选"
                    fields.append(f"{key}:{typ}/{req}")
                lines.append(f"   - 输入：{', '.join(fields)}")
            if cap.caller_responsibilities:
                lines.append(f"   - 调用方负责：{'；'.join(map(str, cap.caller_responsibilities))}")
            if cap.skill_responsibilities:
                lines.append(f"   - Skill 负责：{'；'.join(map(str, cap.skill_responsibilities))}")
    else:
        lines.append("- 未生成业务能力编排，请先点击“生成/优化编排”。")

    lines.extend([
        "",
        "## 2. 用户需要提供的参数",
    ]
    )

    user_params = [(s, p) for s, p in _unique_params(current, "user_param") if p.exposed_to_user]
    if user_params:
        for _st, p in user_params:
            required = "必填" if p.required else "可选"
            reason = p.reason or _description_source_text(p)
            lines.append(f"- {_description_param_key(p)}：{p.type}，{required}。{reason}")
    else:
        lines.append("- 无。当前 FlowSpec 没有暴露给用户的 user_param。")

    lines.extend(["", "## 3. 系统自动处理的变量"])
    runtime_params = _unique_params(current, "runtime_var")
    if runtime_params:
        for _st, p in runtime_params:
            lines.append(f"- {_description_param_key(p)}：{_description_source_text(p)}。")
    else:
        lines.append("- 无。")

    lines.extend(["", "## 4. 固定系统常量"])
    const_params = _unique_params(current, "system_const")
    if const_params:
        for _st, p in const_params:
            value = _description_value(p.value)
            suffix = f"，录制值 `{value}`" if value else ""
            confirm = "，需人工确认" if p.need_human_confirm else ""
            lines.append(f"- {_description_param_key(p)}：{_description_source_text(p)}{suffix}{confirm}。")
    else:
        lines.append("- 无。")

    lines.extend(["", "## 5. 执行步骤"])
    if current.steps:
        for i, st in enumerate(current.steps, 1):
            name = st.name or _derive_step_name(st)
            role = st.source_meta.get("role") or st.semantic_role or "business_step"
            lines.append(f"{i}. {name}")
            lines.append(f"   调用 `{st.method} {st.path or st.url}`，角色 `{role}`，风险等级 `{st.risk_level}`。")
    else:
        lines.append("无可执行步骤。")

    lines.extend(["", "## 6. 接口依赖关系"])
    if current.links:
        for lk in current.links:
            source = next((s for s in current.steps if s.step_id == lk.source_step_id), None)
            target = next((s for s in current.steps if s.step_id == lk.target_step_id), None)
            source_name = source.name or source.path if source else lk.source_step_id
            target_name = target.name or target.path if target else lk.target_step_id
            status = "已确认" if lk.confirmed else "待确认"
            lines.append(f"- {source_name}.response.{lk.source_path} -> {target_name}.body.{_strip_body_prefix(lk.target_path)}（{status}）。")
    else:
        lines.append("- 未发现跨接口字段依赖。")

    lines.extend(["", "## 7. 成功判断"])
    if current.steps:
        for st in current.steps:
            name = st.name or _derive_step_name(st)
            lines.append(f"- {name}：{_description_rule(st.success_rule)}。")
    else:
        lines.append("- 无。")

    lines.extend(["", "## 8. 风险与注意事项"])
    risks: list[str] = [f"整体风险等级为 `{current.risk_level}`。"]
    if any(st.risk_level == "L4" for st in current.steps):
        risks.append("存在高风险写操作，发布前必须确认操作边界。")
    if any(p.category == "runtime_var" and p.source_kind == "unknown" for st in current.steps for p in st.params):
        risks.append("存在来源未知的 runtime_var，不能直接使用录制旧值。")
    if any(p.category == "system_const" and p.exposed_to_user for st in current.steps for p in st.params):
        risks.append("存在仍暴露给用户的 system_const，需要隐藏或改分类。")
    if any(st.method == "GET" and not st.body_source for st in current.steps):
        risks.append("存在 GET 前置步骤，执行时会按 query_template 构造运行期 URL。")
    for risk in risks:
        lines.append(f"- {risk}")

    lines.extend([
        "",
        "## 8.1 失败处理",
        "- 任一步接口返回失败、响应无法解析或必需依赖取值为空时，立即停止后续写操作，并向调用方返回失败步骤、接口路径和原始错误摘要。",
        "- 写操作不做隐式重试；是否重试由调用方根据幂等性和业务确认结果决定。",
    ])

    lines.extend(["", "## 9. 需要人工确认的问题"])
    unresolved = [item for item in current.review_items if not item.resolved]
    if unresolved:
        for item in unresolved[:20]:
            target = item.target.get("path") or item.target.get("link_id") or item.target.get("step_id") or item.target.get("path")
            target_text = f" `{target}`" if target else ""
            lines.append(f"- [{item.severity}] {item.title}{target_text}：{item.reason}")
        if len(unresolved) > 20:
            lines.append(f"- 另有 {len(unresolved) - 20} 个待确认项，请在 FlowSpec 编辑器中查看。")
    else:
        lines.append("- 无。")

    return "\n".join(lines)
