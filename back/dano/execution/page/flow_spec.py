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
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from urllib.parse import urlparse, parse_qs

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
    value: str = ""
    type: str = "string"  # string/number/boolean/datetime/date/array/object/list-enum
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
    llm_suggestions: list[dict[str, Any]] = Field(default_factory=list)


class FlowCapability(BaseModel):
    """对外前端可调用的业务能力层。

    FlowStep/FlowLink 仍描述真实接口执行；Capability 描述外部调用方看到的业务动作。
    """

    name: str = ""
    title: str = ""
    intent: str = ""
    kind: str = "submit"  # query_status / list_options / validate_batch / submit_batch / submit
    step_ids: list[str] = Field(default_factory=list)
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
    review_items: list[ReviewItem] = Field(default_factory=list)
    goal: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "L3"
    meta: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1


# ─────────── Step A: 收敛函数 ───────────
def _infer_type_from_value(value: str) -> str:
    if not value:
        return "string"
    if value.lower() in ("true", "false"):
        return "boolean"
    if re.match(r"^\d{4}-\d{2}-\d{2}T", value):
        return "datetime"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return "date"
    try:
        float(value)
        return "number"
    except ValueError:
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
    return suggest_select_names(selects, samples)


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
        "processdefinitionkey", "processdefinitionid", "billtype", "formtype",
        "flowtype", "businesstype", "templateid", "template_id", "formid",
        "menuid", "appid", "appname", "status", "flowstatus",
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

    if path in select_paths:
        source_kind = _select_source_kind((select_by_path or {}).get(path))
        return {
            "category": "user_param",
            "source_kind": source_kind,
            "source": {"kind": source_kind, "path": path},
            "editable": True,
            "exposed_to_user": True,
            "reason": _select_source_reason(source_kind),
            "need_human_confirm": False,
        }

    if path in select_id_paths:
        source_kind = _select_source_kind((select_by_id_path or {}).get(path))
        return {
            "category": "runtime_var",
            "source_kind": source_kind,
            "source": {"kind": "select_id", "path": path, "option_kind": source_kind},
            "editable": False,
            "exposed_to_user": False,
            "reason": _select_source_reason(source_kind, id_field=True),
            "need_human_confirm": False,
        }

    if method == "GET" and path.startswith("query."):
        return {
            "category": "system_const",
            "source_kind": "page_context",
            "source": {"kind": "query", "path": path},
            "editable": True,
            "exposed_to_user": False,
            "reason": "该字段来自 GET 查询参数，通常由当前页面/应用上下文提供，默认不暴露给普通调用者",
            "need_human_confirm": True,
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
            "need_human_confirm": True,
        }

    if _looks_page_context_field(key, path) and value not in _sample_value_set(samples):
        return {
            "category": "system_const",
            "source_kind": "page_context",
            "source": {"kind": "page_context", "path": path},
            "editable": True,
            "exposed_to_user": False,
            "reason": "字段名像部门/组织/租户等页面上下文，默认隐藏；跨部门复用时需绑定页面上下文或前置接口来源",
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
                out.append({"label": label, "value": (om or {}).get(label, label)} if om else label)
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


def _enum_option_map_from_options(options: list[Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for opt in options or []:
        pair = _enum_label_value(opt)
        if pair:
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

    # GET 请求：从 URL query string 提参,同时对 query 也跑 select 检测
    # (治"参数来源接口没识别":接口型 query 参数如 keyword=xxx / status=xxx 应该被识别为接口选择字段)
    if method == "GET" or body is None:
        list_paths: list[str] = []
        iden_raw: list[dict] = []
        flat_fields = _params_from_get_query(req)
        # select/选人:在 query 参数名上做下拉检测,与 POST body 同套算法
        selects_raw = _detect_query_selects(req, samples, reads or [], page_enum_options)
    else:
        # 列表多选先识别
        list_selects = suggest_list_selects(pd, reads or [], samples)
        list_paths = [s["path"] for s in list_selects]

        # 字段拍平
        flat_fields = flatten_body(pd, samples, required_labels, collapse_paths=list_paths)

        # select/选人
        selects_raw = suggest_selects(pd, reads or [], samples, skip_paths=list_paths, fields=flat_fields) + list_selects
        apply_page_enum_options(selects_raw, page_enum_options, post_data=pd, fields=flat_fields)
        selects_raw += page_enum_selects(pd, page_enum_options, {s.get("path", "") for s in selects_raw}, fields=flat_fields)

        # identity(运行期重取)
        iden_raw = suggest_identity(pd, storage_state, samples)

    # select 字段配中文名
    sel_names = _select_name_for_step(selects_raw, samples)

    # BPMN 审批人命名兜底
    assignee_names = suggest_assignee_names(pd, reads or [], samples)

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
        ptype = f.get("type") or "string"
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

        params.append(ParamField(
            path=path,
            key=nm,
            label=nm,
            value=str(f.get("value") or ""),
            type=ptype,
            required=bool(f.get("required")),
            confidence=float(f.get("confidence") or 0.0),
            confidence_tier=f.get("confidence_tier") or "auto",
            name_source=ns,
            # **系统化**:同时投递 label 列表 + label→value 反查表,确保前端能渲染 + 运行期能做 name→ID 解析。
            enum_options=_enum_options_for_param(select_meta),
            enum_value_map=_enum_value_map_for_param(select_meta),
            category=source_guess["category"],
            source_kind=source_guess["source_kind"],
            source=source_guess["source"],
            editable=bool(source_guess["editable"]),
            exposed_to_user=bool(source_guess["exposed_to_user"]),
            default_value=f.get("value"),
            reason=source_guess["reason"],
            need_human_confirm=bool(source_guess["need_human_confirm"]),
        ))

    # 补回 select 元数据的 param 字段
    path2key = {p.path: p.key for p in params}
    for sb, sraw in zip(selects_meta, selects_raw):
        sb.param = path2key.get(sraw.get("path", ""), "")

    # sample_inputs
    sample_inputs = {p.key: p.value for p in params if p.value}

    # source_meta
    source_meta = {
        "method": method,
        "url": req.get("url") or "",
        "headers_count": len(req.get("headers") or {}),
        "captured_at": req.get("captured_at"),
        "response_status": req.get("response_status"),
        "request_index": req.get("index"),
        "role": request_role.get("role", ""),
        "keep": request_role.get("keep"),
        "keep_reason": request_role.get("keep_reason") or request_role.get("reason", ""),
        "filter_reason": request_role.get("filter_reason", ""),
        "confidence": request_role.get("confidence"),
        "evidence": request_role.get("evidence"),
    }

    full_url = req.get("url") or ""
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


def _params_from_get_query(req: dict) -> list[dict]:
    """GET 请求：从 URL query string 提参。"""
    url = req.get("url") or ""
    if "?" not in url:
        return []
    try:
        u = urlparse(url)
        qs = parse_qs(u.query, keep_blank_values=True)
    except Exception:
        return []
    out: list[dict] = []
    for k, vals in qs.items():
        v = vals[0] if vals else ""
        out.append({
            "path": f"query.{k}",
            "key": k,
            "value": v,
            "type": "string",
            "required": True,
            "confidence": 0.7,
            "confidence_tier": "auto",
            "name_source": "auto",
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
    flat = _params_from_get_query(req)
    if not flat:
        return []
    syn_body: dict[str, Any] = {f.get("key"): f.get("value") for f in flat if f.get("key")}
    syn_pd = json.dumps(syn_body, ensure_ascii=False)
    selects_raw = suggest_selects(syn_pd, reads or [], samples, skip_paths=[], fields=flat) + []
    apply_page_enum_options(selects_raw, page_enum_options, post_data=syn_pd, fields=flat)
    selects_raw += page_enum_selects(syn_pd, page_enum_options,
                                     {s.get("path", "") for s in selects_raw}, fields=flat)

    # 第二道:value 形态兜底(suggest_selects 当 value 与 label 不挂钩时容易漏)——
    # query 值若与 reads 候选列表里某 value 字段精准相等,就挂上 select 标记。
    hits_paths = {s.get("path") for s in selects_raw}
    for f in flat:
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
        if list_items is not None or segs & _OPTION_SEGS:
            count = len(list_items or [])
            return _role_row(req, role="read_option", keep=False,
                             reason=f"读接口返回候选列表/枚举源({count}项)，作为字段来源，不进入主流程",
                             confidence=0.9, semantic=semantic)
        if response_ref:
            return _role_row(req, role="business_get", keep=True,
                             reason="GET 响应值被后续业务请求引用，作为前置步骤保留",
                             confidence=0.96, semantic=semantic, evidence=response_ref)
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


def _dedupe_preread_candidates(preread_cands: list[dict]) -> list[dict]:
    """同一路径的前置读请求反复触发时，只保留最后一次录制结果。"""
    latest_by_path: dict[tuple[str, str], Any] = {}
    for req in preread_cands:
        latest_by_path[_preread_dedupe_key(req)] = _request_role_key(req)
    return [
        req for req in preread_cands
        if latest_by_path.get(_preread_dedupe_key(req)) == _request_role_key(req)
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
            p.editable = False
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


def _sync_link_sources(steps: list[FlowStep], links: list[FlowLink]) -> None:
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

    def add(url: str, payload: Any) -> None:
        if payload is None:
            return
        key = (url or "", json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)[:500])
        if key in seen:
            return
        seen.add(key)
        out.append({"url": url or "", "json": payload})

    for r in explicit_reads or []:
        add(r.get("url") or "", r.get("json", r.get("response_json")))
    for req, role in zip(captured_requests or [], request_roles or []):
        if role.get("role") not in {"read_option", "read_context", "business_get"}:
            continue
        add(req.get("url") or "", req.get("response_json", req.get("json")))
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

    request_roles = [classify_network_request(r, captured_requests, samples) for r in captured_requests]
    role_by_key = {_request_role_key(r): role for r, role in zip(captured_requests, request_roles)}
    flow_reads = _merge_flow_read_sources(reads, captured_requests, request_roles)

    # 1) 业务写请求
    write_cands = [
        c for c in json_write_requests(captured_requests)
        if (role_by_key.get(_request_role_key(c), {}).get("keep")
            and role_by_key.get(_request_role_key(c), {}).get("role") in {"submit_anchor", "business_write"})
    ]

    # 2) 前置读请求：GET 或 POST 查询，只在响应被后续业务请求引用时进入主流程。
    preread_cands = [
        r for r in captured_requests
        if (role_by_key.get(_request_role_key(r), {}).get("keep")
            and role_by_key.get(_request_role_key(r), {}).get("role") in {"business_get", "read_context"})
    ]
    preread_before_dedupe = len(preread_cands)
    preread_cands = _dedupe_preread_candidates(preread_cands)

    if not write_cands and not preread_cands:
        empty_spec = FlowSpec(
            tenant=tenant,
            subsystem=subsystem,
            title="(未捕获到业务请求)",
            recording_mode=recording_mode,
            diagnostics=diagnostics,
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
                "request_graph": _build_request_graph(captured_requests, request_roles, set()),
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
    selected_preread_keys = {_request_role_key(r) for r in preread_cands}
    selected_keys = selected_write_keys | selected_preread_keys
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
                link_objs.append(FlowLink(
                    source_step_id=idx_to_step_id[src_pos],
                    source_path=lk.get("source_path", ""),
                    source_tokens=lk.get("source_tokens"),
                    target_step_id=idx_to_step_id[tgt_pos],
                    target_path=lk.get("target_path", ""),
                    target_tokens=lk.get("target_tokens"),
                    param_name=None,
                    confirmed=False,
                    confidence=0.85,
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


def _capability_input_schema(params: list[ParamField]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in params:
        if p.category != "user_param" or not p.exposed_to_user:
            continue
        key = p.key or p.path
        props[key] = _schema_for_param_type(p.type)
        if p.enum_options:
            props[key]["x-options"] = list(p.enum_options)
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


def ensure_recorded_goal(spec: FlowSpec) -> FlowSpec:
    if spec.goal:
        return spec
    spec.goal = _recorded_goal_from_parts(spec.title, spec.steps, spec.risk_level)
    return spec


def _sync_capability_io_schemas(spec: FlowSpec) -> FlowSpec:
    """让 capability 的输入输出 schema 始终跟当前字段/响应保持一致。"""
    if not spec.capabilities:
        return spec
    by_id = {s.step_id: s for s in spec.steps}
    for cap in spec.capabilities:
        cap_steps = [by_id[sid] for sid in (cap.step_ids or []) if sid in by_id]
        if not cap_steps:
            continue
        params = [p for st in cap_steps for p in (st.params or [])]
        cap.input_schema = _capability_input_schema(params)
        if _capability_is_batch(spec, cap):
            item_schema = _capability_input_schema(params)
            props = dict(cap.input_schema.get("properties") or {})
            props.setdefault("entries", {
                "type": "array",
                "description": "批量提交明细；每个元素使用同一套业务字段",
                "items": item_schema,
            })
            cap.input_schema = {
                "type": "object",
                "properties": props,
                "required": list(cap.input_schema.get("required") or []),
            }
        last_response = next((st.response_json for st in reversed(cap_steps) if st.response_json is not None), None)
        if last_response is not None:
            cap.output_schema = _schema_from_response_value(last_response)
    return spec


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
    text = f"{step.name} {step.path} {step.url}".lower()
    if any(x in text for x in ("batch", "list", "pclist", "批量")):
        return True
    try:
        body = _parse_body(step.body_source)
    except Exception:
        body = None
    if isinstance(body, list):
        return True
    return any(str(p.path).startswith("[0]") or "[0]" in str(p.path) for p in step.params)


def _default_capability_nodes(steps: list[FlowStep], *, kind: str) -> list[dict[str, Any]]:
    if not steps:
        return []
    if kind == "submit_batch" and any(_looks_batch_step(s) for s in steps):
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


def suggest_flow_capabilities(spec: FlowSpec) -> list[FlowCapability]:
    """从真实录制步骤生成最小业务能力层。"""
    caps: list[FlowCapability] = []
    read_steps = [s for s in spec.steps if not _is_write_step(s)]
    write_steps = [s for s in spec.steps if _is_write_step(s)]
    all_params = [p for s in spec.steps for p in s.params]

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
            confidence=0.72,
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
        input_schema = _capability_input_schema(all_params)
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
            step_ids=[s.step_id for s in spec.steps],
            nodes=_default_capability_nodes(spec.steps, kind=kind),
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


def _read_status_steps(spec: FlowSpec) -> list[FlowStep]:
    status_hint = re.compile(r"(status|state|progress|history|detail|approval|process|instance|task|todo)", re.I)
    out: list[FlowStep] = []
    for st in spec.steps:
        if (st.method or "").upper() in _WRITE_METHODS:
            continue
        role = (st.source_meta or {}).get("role") or st.semantic_role or ""
        path = st.path or st.url or st.name
        if role in {"business_get", "read_context"} or status_hint.search(path or ""):
            out.append(st)
    return out


def _request_graph_entries(spec: FlowSpec, roles: set[str]) -> list[dict[str, Any]]:
    graph = (spec.meta or {}).get("request_graph") or {}
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
    all_params = [p for st in spec.steps for p in st.params]
    write_steps = _write_steps(spec)
    if write_steps:
        kind = "submit_batch"
        caps.append(FlowCapability(
            name=kind,
            title="批量提交业务申请",
            intent="调用方提供业务字段；Skill 按已纳入接口顺序执行前置查询、依赖注入和最终提交，并返回最后写接口结果。",
            kind=kind,
            step_ids=_capability_step_ids(spec.steps),
            nodes=_default_capability_nodes(spec.steps, kind=kind),
            input_schema=_json_schema_for_params(all_params),
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "results": {"type": "array", "items": {"type": "object"}},
                },
            },
            output_mapping=[{
                "kind": "final_response",
                "step_id": write_steps[-1].step_id,
                "response_path": "response",
            }],
            confirmed=False,
            confidence=0.72,
            requires_human_confirm=True,
            status="draft",
            evidence=[{
                "kind": "write_steps",
                "step_ids": _capability_step_ids(write_steps),
                "paths": [s.path or s.url for s in write_steps],
            }],
            caller_responsibilities=["提供 input_schema 中的业务字段", "按需确认批量条数和幂等策略"],
            skill_responsibilities=["按 FlowStep 顺序执行请求", "注入 links/system_values/runtime_var", "返回每条提交的成功状态"],
        ))
        return caps

    status_steps = _read_status_steps(spec)
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
            output_mapping=[{
                "kind": "candidate_response",
                "step_id": status_steps[-1].step_id if status_steps else "",
                "response_path": "response",
            }],
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


def _with_default_capabilities(spec: FlowSpec) -> FlowSpec:
    if spec.capabilities:
        return spec
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
    return spec


_FLOW_ORCHESTRATE_SYSTEM = """你是企业 OA/API 录制结果的 Skill 编排器。
只输出 JSON，不要输出解释。
目标：根据真实捕获请求生成外部前端可调用的业务能力列表。
要求：
- 每个能力必须引用已存在 step_id，不能编造接口。
- 不要把候选接口当成稳定步骤，除非它已经在 steps 中。
- 如果已有能力编排，请在已有能力基础上补充/优化，不要重新设计一套无关能力。
- 如果流程包含写接口，默认只输出一个 submit 或 submit_batch 主能力；前置 GET 应作为该能力步骤链的一部分，不要单独拆 query_status/list_options。
- 读能力只查询并返回结果；写能力可以包含前置查询 + 写入步骤。
- 批量填报/日报/明细数组场景优先生成 submit_batch。
- output_mapping 默认指向最后一个步骤 response。
JSON 形态：
{"abilities":[{"name":"","title":"","intent":"","kind":"query_status|list_options|validate_batch|submit_batch|submit","step_ids":[],"nodes":[{"id":"","type":"call|map|filter|condition|foreach|select|return","step_id":""}],"input_schema":{},"output_schema":{},"output_mapping":[],"preconditions":[],"caller_responsibilities":[],"skill_responsibilities":[],"confidence":0.0,"requires_human_confirm":true}]}
"""


def _capability_from_llm(raw: dict[str, Any], step_ids: set[str], used_names: set[str]) -> FlowCapability | None:
    if not isinstance(raw, dict):
        return None
    allowed_kinds = {"query_status", "list_options", "validate_batch", "submit_batch", "submit"}
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
    if kind != "list_options" and not selected_steps:
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
    graph = (spec.meta or {}).get("request_graph") or {}
    return {
        "title": spec.title,
        "business_description": spec.business_description,
        "existing_capabilities": [
            {
                "name": cap.name,
                "title": cap.title,
                "intent": cap.intent,
                "kind": cap.kind,
                "step_ids": list(cap.step_ids or []),
                "confirmed": cap.confirmed,
                "requires_human_confirm": cap.requires_human_confirm,
            }
            for cap in spec.capabilities
        ],
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
            for r in (graph.get("all_requests") or [])[:80]
        ],
    }


def _merge_capability_lists(existing: list[FlowCapability], generated: list[FlowCapability]) -> list[FlowCapability]:
    """把新生成能力合并到已有能力上，避免每次“生成编排”覆盖人工编辑。"""
    if not existing:
        return generated
    out = [cap.model_copy(deep=True) for cap in existing]
    by_name = {cap.name: cap for cap in out if cap.name}
    for cap in generated:
        cur = by_name.get(cap.name)
        if cur is None:
            out.append(cap)
            if cap.name:
                by_name[cap.name] = cap
            continue
        for sid in cap.step_ids:
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
            elif node_type in {"foreach", "condition", "filter", "select", "map"}:
                for child_key in ("children", "then", "else"):
                    if isinstance(copied.get(child_key), list):
                        copied[child_key] = clean_nodes(copied[child_key], fallback_step_ids)
            elif node_type == "return":
                ref = str(copied.get("from") or copied.get("source") or "")
                if ref and ref not in step_ids and ref not in node_ids:
                    if fallback_step_ids:
                        copied["from"] = fallback_step_ids[-1]
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
        if not cap.step_ids:
            cap.step_ids = _capability_call_step_ids_from_nodes(cap.nodes or [])
        if cap.step_ids:
            _sync_capability_order(spec, cap)
    return spec


def _sync_capability_order(spec: FlowSpec, cap: FlowCapability) -> None:
    order = {s.step_id: i for i, s in enumerate(spec.steps)}
    seen: set[str] = set()
    cap.step_ids = sorted(
        [sid for sid in cap.step_ids if not (sid in seen or seen.add(sid))],
        key=lambda sid: order.get(sid, 10_000),
    )
    if any(isinstance(n, dict) and n.get("type") != "call" for n in (cap.nodes or [])):
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
        for key in ("steps", "then", "otherwise"):
            child = node.get(key)
            if isinstance(child, list):
                out.extend(_iter_capability_nodes([n for n in child if isinstance(n, dict)]))
    return out


def _capability_call_step_ids_from_nodes(nodes: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for node in _iter_capability_nodes(nodes):
        sid = str(node.get("step_id") or "")
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def _capability_is_batch(spec: FlowSpec, cap: FlowCapability) -> bool:
    by_id = {s.step_id: s for s in spec.steps}
    cap_steps = [by_id[sid] for sid in _capability_node_step_ids(cap) if sid in by_id]
    return cap.kind == "submit_batch" or any(_looks_batch_step(st) for st in cap_steps)


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
    return {
        "protocol": "dano.capability_plan.v1",
        "name": cap.name,
        "kind": cap.kind,
        "nodes": [dict(n) for n in (cap.nodes or [])],
        "call_order": calls,
        "batch": {
            "enabled": _capability_is_batch(spec, cap),
            "items_field": "entries",
            "mode": "repeat_selected_workflow",
            "merge_base_input": True,
        },
        "return": cap.output_mapping or [{
            "kind": "final_response",
            "step_id": final_step,
            "response_path": "response",
        }],
    }


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
    """生成能力编排。

    LLM 只负责产出 ability 编排；所有 step 引用和 schema 都由确定性校验兜底。
    重复点击时按已有 capabilities 增量合并，保留人工编辑和确认状态。
    """
    current = spec.model_copy(deep=True)
    existing = list(current.capabilities or [])
    fallback = _merge_capability_lists(existing, build_default_flow_capabilities(current))
    caps: list[FlowCapability] = []
    source = "deterministic"
    reason = ""

    if llm_client is not None and model:
        try:
            out = await llm_client.complete_json(
                model=model,
                system=_FLOW_ORCHESTRATE_SYSTEM,
                user="【FlowSpec 编排上下文】\n" + json.dumps(_orchestration_context(current), ensure_ascii=False),
                timeout_s=timeout_s,
            )
            raw_abilities = out.get("abilities") if isinstance(out, dict) else None
            if isinstance(raw_abilities, list):
                step_ids = {s.step_id for s in current.steps}
                used: set[str] = set()
                for raw in raw_abilities:
                    cap = _capability_from_llm(raw, step_ids, used)
                    if cap is not None:
                        caps.append(cap)
            if caps:
                source = "llm"
        except Exception as exc:  # noqa: BLE001 - 编排失败降级为确定性生成
            reason = str(exc)[:240]

    if not caps:
        caps = fallback
        source = "deterministic"
    else:
        if not existing and _write_steps(current):
            primary = next((cap for cap in caps if cap.kind in {"submit_batch", "submit"}), None)
            if primary is not None:
                primary.kind = "submit_batch"
                primary.name = "submit_batch"
                if not primary.step_ids:
                    primary.step_ids = _capability_step_ids(current.steps)
                caps = [primary]
        caps = _merge_capability_lists(existing, caps)

    current.capabilities = caps
    _normalize_capability_references(current)
    for cap in current.capabilities:
        _sync_capability_order(current, cap)
    current.meta = {
        **(current.meta or {}),
        "capability_model": {
            "status": "ready",
            "source": source,
            "generated_count": len(caps),
            "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
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


def _capability_validation_report(spec: FlowSpec) -> dict[str, Any]:
    spec = _sync_capability_io_schemas(spec.model_copy(deep=True))
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
    if spec.steps and not caps:
        warnings.append("FlowSpec 未生成业务能力编排，前端只能按底层接口展示")
        return {
            "passed": False,
            "errors": errors,
            "warnings": warnings,
            "capabilities": [],
            "checked_requests": checked_requests,
            "checked_manual_requests": checked_manual_requests,
            "unused_high_confidence_requests": high_conf_unused,
        }

    allowed_kinds = {"query_status", "list_options", "validate_batch", "submit_batch", "submit"}
    allowed_nodes = {"call", "map", "filter", "condition", "foreach", "select", "return"}
    seen_names: set[str] = set()
    for cap in caps:
        label = cap.name or cap.kind or "<unnamed>"
        cap_errors: list[str] = []
        cap_warnings: list[str] = []
        if not cap.name:
            cap_errors.append("Capability 缺少 name")
        elif cap.name in seen_names:
            cap_errors.append(f"Capability `{cap.name}` 重名")
        seen_names.add(cap.name)

        if cap.kind not in allowed_kinds:
            cap_errors.append(f"Capability `{label}` kind `{cap.kind}` 不在允许范围内")

        node_step_ids = _capability_node_step_ids(cap)
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

        input_props = ((cap.input_schema or {}).get("properties") or {})
        has_return_node = any(isinstance(n, dict) and n.get("type") == "return" for n in _iter_capability_nodes(cap.nodes or []))
        for node in _iter_capability_nodes(cap.nodes or []):
            if not isinstance(node, dict):
                cap_errors.append(f"Capability `{label}` 包含非法节点")
                continue
            node_type = str(node.get("type") or "")
            node_id = str(node.get("id") or node_type or "<node>")
            if node_type not in allowed_nodes:
                cap_errors.append(f"Capability `{label}` 节点 `{node_id}` 类型 `{node_type}` 不支持")
            if node_type == "call" and str(node.get("step_id") or "") not in step_by_id:
                cap_errors.append(f"Capability `{label}` call 节点 `{node_id}` 未绑定有效接口步骤")
            if node_type == "foreach":
                items = str(node.get("items") or "")
                if not items:
                    cap_errors.append(f"Capability `{label}` foreach 节点 `{node_id}` 缺少 items 数组来源")
                elif items.startswith("input."):
                    field = items.split(".", 1)[1]
                    schema = input_props.get(field) or {}
                    if field not in input_props:
                        cap_errors.append(f"Capability `{label}` foreach 节点 `{node_id}` 引用的输入 `{field}` 不存在")
                    elif schema.get("type") != "array":
                        cap_errors.append(f"Capability `{label}` foreach 节点 `{node_id}` 的输入 `{field}` 不是数组")
                if not isinstance(node.get("steps"), list) and not any(
                    isinstance(n, dict) and n.get("type") == "call" for n in _iter_capability_nodes([node])
                ):
                    cap_warnings.append(f"Capability `{label}` foreach 节点 `{node_id}` 没有子步骤，运行期将退化为重复执行能力闭包")
            if node_type == "map" and (not node.get("source") or not node.get("target")):
                cap_errors.append(f"Capability `{label}` map 节点 `{node_id}` 缺少 source 或 target")
            if node_type == "map":
                source = str(node.get("source") or "")
                if source.startswith("input."):
                    field = source.split(".", 1)[1].split(".", 1)[0]
                    if field not in input_props:
                        cap_errors.append(f"Capability `{label}` map 节点 `{node_id}` 引用的输入 `{field}` 不存在")
            if node_type == "return" and not (node.get("value") or node.get("from") or node.get("path")):
                cap_errors.append(f"Capability `{label}` return 节点 `{node_id}` 缺少返回来源")
            if node_type == "return" and node.get("from"):
                ref = str(node.get("from") or "")
                if ref and ref not in step_by_id and not ref.startswith(("input.", "var.", "node.")):
                    cap_errors.append(f"Capability `{label}` return 节点 `{node_id}` 引用的来源 `{ref}` 不存在")
        if cap.confirmed and cap.nodes and not has_return_node:
            cap_warnings.append(f"Capability `{label}` 已确认但没有 return 节点，外部调用只能拿到底层原始响应")

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
    dedup_checked = list({r["request_key"]: r for r in checked_requests}.values())
    dedup_manual = list({r["request_key"]: r for r in checked_manual_requests}.values())
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "capabilities": capability_reports,
        "checked_requests": dedup_checked,
        "checked_manual_requests": dedup_manual,
        "unused_high_confidence_requests": high_conf_unused,
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
    step_ids = {s.step_id for s in spec.steps}
    steps_by_id = {s.step_id: s for s in spec.steps}

    for st in spec.steps:
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
                title=f"修复断开的接口依赖 {lk.link_id}",
                target=target,
                current_guess="invalid_link",
                suggested_action="fix_or_remove_link",
                reason="该 link 指向不存在的步骤，执行计划无法可靠生成",
                confidence=lk.confidence,
            ))
            continue

        source_step = steps_by_id.get(lk.source_step_id)
        target_step = steps_by_id.get(lk.target_step_id)
        source_path = lk.source_tokens or lk.source_path
        if source_step and source_step.response_json is not None and _flow_path_lookup(source_step.response_json, source_path) is _FLOW_PATH_MISSING:
            items.append(_review_item(
                "link_source_missing",
                severity="high",
                title=f"修复接口依赖来源 {lk.source_path}",
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
                title=f"修复接口依赖目标 {lk.target_path}",
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
                title=f"确认接口依赖 {lk.source_path} -> {lk.target_path}",
                target=target,
                current_guess="previous_response",
                suggested_action="confirm_link",
                reason=lk.reason or "该 link 由响应值与请求值匹配自动生成，需要人工确认",
                confidence=lk.confidence,
            ))

    for role in spec.meta.get("request_roles") or []:
        if role.get("keep") and role.get("role") in {"business_get", "read_context"}:
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
                confidence=float(role.get("confidence") or 0.0),
            ))

    if spec.steps and not flow_spec_user_params(spec):
        items.append(_review_item(
            "no_user_param",
            severity="low",
            title="确认 Skill 是否不需要用户输入",
            target={"kind": "flow", "flow_id": spec.flow_id},
            current_guess="no_user_param",
            suggested_action="confirm_or_expose_param",
            reason="当前 FlowSpec 没有 user_param，发布后的 Skill 不会要求用户填写业务参数",
        ))

    if spec.steps and not any((st.success_rule for st in spec.steps)):
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


def refresh_review_items(spec: FlowSpec) -> FlowSpec:
    """重建 review_items，并保留同 id 项的已解决状态。"""
    old_resolved = {item.id: item.resolved for item in spec.review_items}
    old_suggestions = {item.id: list(item.llm_suggestions or []) for item in spec.review_items}
    spec.review_items = build_review_items(spec)
    for item in spec.review_items:
        if item.id in old_resolved:
            item.resolved = old_resolved[item.id]
        if item.id in old_suggestions:
            item.llm_suggestions = old_suggestions[item.id]
    return spec


def _response_candidate_paths(spec: FlowSpec, target_step: FlowStep, param: ParamField) -> list[dict[str, Any]]:
    """给 LLM 的 grounded 候选:只给路径/字段名,不把录制值发给模型。"""
    value = str(param.value or "").strip()
    if not value:
        return []
    step_index = {s.step_id: i for i, s in enumerate(spec.steps)}
    target_idx = step_index.get(target_step.step_id, 0)
    out: list[dict[str, Any]] = []
    for source_step in spec.steps:
        if step_index.get(source_step.step_id, 0) >= target_idx:
            continue
        if source_step.response_json is None:
            continue
        for path, _tokens, leaf_value, _raw in _leaf_paths(source_step.response_json):
            if str(leaf_value) == value:
                out.append({
                    "source_step_id": source_step.step_id,
                    "source_step_name": source_step.name,
                    "source_method": source_step.method,
                    "source_path": path,
                })
    return out[:20]


def _llm_review_targets(spec: FlowSpec) -> list[dict[str, Any]]:
    steps_by_id = {s.step_id: s for s in spec.steps}
    params_by_step_path = {(s.step_id, p.path): p for s in spec.steps for p in s.params}
    targets: list[dict[str, Any]] = []
    for item in spec.review_items:
        if item.resolved:
            continue
        if item.type not in {"runtime_var_source", "runtime_var_missing_source", "field_category"}:
            continue
        tgt = item.target or {}
        step_id = str(tgt.get("step_id") or "")
        path = str(tgt.get("path") or "")
        step = steps_by_id.get(step_id)
        param = params_by_step_path.get((step_id, path))
        if step is None or param is None:
            continue
        targets.append({
            "review_id": item.id,
            "review_type": item.type,
            "severity": item.severity,
            "title": item.title,
            "reason": item.reason,
            "target": {
                "step_id": step.step_id,
                "step_name": step.name,
                "step_method": step.method,
                "step_path": step.path,
                "param_path": param.path,
                "param_key": param.key,
                "param_type": param.type,
                "current_guess": f"{param.category}/{param.source_kind}",
            },
            "candidate_response_sources": _response_candidate_paths(spec, step, param),
            "allowed_source_kinds": ["previous_response", "current_user", "system_time", "page_context", "request_header", "unknown"],
        })
    return targets


_FLOW_RECOMMEND_SYSTEM = (
    "你是 FlowSpec 字段来源推荐助手。系统已经先做了规则自动匹配;你只处理规则无法确定的 review item。"
    "你只能基于输入里的步骤、字段名、路径、候选响应路径做推荐,禁止编造不存在的 source_step_id/source_path。"
    "不要输出最终修改后的 FlowSpec,只输出建议。高风险或低置信度仍需人工确认。"
    "输出 JSON 对象:{\"suggestions\":[{"
    "\"review_id\":\"输入中的 review_id\","
    "\"action\":\"bind_previous_response|set_runtime_source|ask_human\","
    "\"confidence\":0到1,"
    "\"reason\":\"简短中文原因\","
    "\"source_step_id\":\"当 action=bind_previous_response 时必填且必须来自候选\","
    "\"source_path\":\"当 action=bind_previous_response 时必填且必须来自候选\","
    "\"source_kind\":\"当 action=set_runtime_source 时填 current_user/system_time/page_context/request_header/unknown\""
    "}]}。"
)


def _valid_llm_suggestion(raw: dict, targets: dict[str, dict]) -> dict[str, Any] | None:
    review_id = str(raw.get("review_id") or "")
    target = targets.get(review_id)
    if not target:
        return None
    action = str(raw.get("action") or "")
    if action not in {"bind_previous_response", "set_runtime_source", "ask_human"}:
        return None
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(raw.get("reason") or "").strip()[:300]
    suggestion: dict[str, Any] = {
        "action": action,
        "confidence": confidence,
        "reason": reason or "LLM 给出的辅助建议，需人工确认后生效",
    }
    if action == "bind_previous_response":
        source_step_id = str(raw.get("source_step_id") or "")
        source_path = str(raw.get("source_path") or "")
        allowed = {
            (str(c.get("source_step_id") or ""), str(c.get("source_path") or ""))
            for c in (target.get("candidate_response_sources") or [])
        }
        if (source_step_id, source_path) not in allowed:
            return None
        suggestion.update({
            "source_step_id": source_step_id,
            "source_path": source_path,
            "target_step_id": target["target"]["step_id"],
            "target_path": target["target"]["param_path"],
        })
    elif action == "set_runtime_source":
        source_kind = str(raw.get("source_kind") or "")
        if source_kind not in {"current_user", "system_time", "page_context", "request_header", "unknown"}:
            return None
        suggestion.update({
            "source_kind": source_kind,
            "target_step_id": target["target"]["step_id"],
            "target_path": target["target"]["param_path"],
        })
    else:
        suggestion.update({
            "target_step_id": target["target"]["step_id"],
            "target_path": target["target"]["param_path"],
        })
    return suggestion


async def add_llm_review_recommendations(
    spec: FlowSpec,
    *,
    llm_client: Any | None = None,
    model: str | None = None,
    timeout_s: float = 45.0,
) -> FlowSpec:
    """第二层:LLM 只给 unresolved review item 增加建议,不直接修改字段/依赖。"""
    current = refresh_review_items(spec.model_copy(deep=True))
    targets = _llm_review_targets(current)
    if not targets:
        return current
    target_by_id = {t["review_id"]: t for t in targets}

    if llm_client is None or not model:
        current.meta = {
            **(current.meta or {}),
            "llm_recommendations": {
                "status": "unavailable",
                "target_count": len(targets),
                "reason": "LLM client/model 未配置",
            },
        }
        return current

    payload = {
        "flow": {
            "title": current.title,
            "risk_level": current.risk_level,
            "steps": [
                {"step_id": s.step_id, "name": s.name, "method": s.method, "path": s.path}
                for s in current.steps
            ],
        },
        "review_targets": targets,
    }
    try:
        out = await llm_client.complete_json(
            model=model,
            system=_FLOW_RECOMMEND_SYSTEM,
            user="【FlowSpec 待推荐项】\n" + json.dumps(payload, ensure_ascii=False),
            timeout_s=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - 推荐层失败不能影响规则层和人工确认
        current.meta = {
            **(current.meta or {}),
            "llm_recommendations": {
                "status": "failed",
                "target_count": len(targets),
                "reason": str(exc)[:200],
            },
        }
        return current

    raw_suggestions = out.get("suggestions") if isinstance(out, dict) else None
    if not isinstance(raw_suggestions, list):
        raw_suggestions = []
    suggestions_by_review: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_suggestions:
        if not isinstance(raw, dict):
            continue
        valid = _valid_llm_suggestion(raw, target_by_id)
        if valid is None:
            continue
        suggestions_by_review.setdefault(str(raw.get("review_id") or ""), []).append(valid)

    for item in current.review_items:
        if item.id in suggestions_by_review:
            ranked = sorted(suggestions_by_review[item.id], key=lambda s: float(s.get("confidence") or 0.0), reverse=True)
            item.llm_suggestions = ranked[:3]

    current.meta = {
        **(current.meta or {}),
        "llm_recommendations": {
            "status": "ok",
            "target_count": len(targets),
            "suggestion_count": sum(len(v) for v in suggestions_by_review.values()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    return append_flow_version(current, "llm_recommendations", reason="刷新 LLM 辅助推荐")


def _flow_fingerprint(spec: FlowSpec) -> str:
    payload = spec.model_dump(exclude={"meta", "review_items"})
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def append_flow_version(
    spec: FlowSpec,
    action: str,
    *,
    reason: str = "",
    actor: str = "system",
) -> FlowSpec:
    """在 FlowSpec.meta 中追加轻量版本记录。"""
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
    if param.source_kind in {"api_option", "page_enum", "static_enum", "manual_enum", "form_option"}:
        return None
    if param.source_kind == "current_user":
        return None
    if param.source_kind == "page_context" and param.source:
        return None
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


def _query_key_from_param(param: ParamField) -> str:
    if param.path.startswith("query."):
        return param.path[len("query."):]
    return param.key


def _flow_step_query_template(step: FlowStep) -> tuple[dict[str, Any], list[str], dict[str, Any], dict[str, str]]:
    query_template: dict[str, Any] = {}
    params: list[str] = []
    samples: dict[str, Any] = {}
    field_types: dict[str, str] = {}
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
            query_template[query_key] = p.value
        else:
            query_template[query_key] = p.value
    return query_template, params, samples, field_types


def flow_spec_user_params(spec: FlowSpec) -> list[str]:
    names: list[str] = []
    for st in spec.steps:
        for name in _step_param_map(st).values():
            if name not in names:
                names.append(name)
    return names


def flow_spec_required_params(spec: FlowSpec) -> list[str]:
    names: list[str] = []
    for st in spec.steps:
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
            query_template, params, samples, field_types = _flow_step_query_template(step)
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
    apir["step_id"] = step.step_id
    apir["step_name"] = step.name
    if step.success_rule:
        apir["success_rule"] = step.success_rule
    if step.fact_check:
        apir["fact_check"] = step.fact_check
    return apir, errors


def flow_spec_to_api_request(spec: FlowSpec) -> tuple[dict | None, list[str]]:
    """把编辑后的 FlowSpec 转成 run_request_onboarding 可消费的 api_request。

    支持有 body 的写请求，也支持无 body 的 GET 前置步骤(query_template)。
    """
    if not spec.steps:
        return None, ["FlowSpec 没有任何步骤，不能发布"]
    spec = _sync_capability_io_schemas(spec.model_copy(deep=True))

    built_steps: list[dict] = []
    step_id_to_index: dict[str, int] = {}
    errors: list[str] = []
    for st in spec.steps:
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
        out["capability_protocol"] = "dano.capability_plan.v1"
        out["workflow_nodes"] = {
            c.name: _capability_execution_contract(spec, c)
            for c in caps
            if c.name
        }
    out["_flow_spec"] = flow_spec_to_summary(spec)
    return out, []


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


def validate_flow_spec(spec: FlowSpec) -> dict:
    from dano.execution.page.repair_ops import collect_repair_findings

    errors: list[str] = []
    warnings: list[str] = []
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
    api_request, build_errors = flow_spec_to_api_request(spec)
    errors.extend(build_errors)
    if not flow_spec_user_params(spec):
        warnings.append("FlowSpec 没有 user_param，发布后的 Skill 不会要求用户输入参数")
    for st in spec.steps:
        select_by_path = {s.path: s for s in st.selects if s.path}
        select_by_param = {s.param: s for s in st.selects if s.param}
        for p in st.params:
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
        if not lk.confirmed:
            errors.append(f"链接 `{lk.link_id}` 尚未人工确认")
    if not any((st.success_rule for st in spec.steps)):
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
        for st in spec.steps:
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
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
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
    """给前端展示的 FlowSpec：保留编辑需要的信息，隐藏鉴权头和原始请求体。

    H20 修复:body_source 不再清空,而是备份到 backup_body_source;前端可见备份,
    编辑时优先使用客户端编辑的 body_source(若有),否则用备份;避免 build_api_request 拿不到 body。
    """
    client_spec = spec.model_copy(deep=True)
    _normalize_capability_references(client_spec)
    data = refresh_review_items(_sync_capability_io_schemas(client_spec)).model_dump()
    request_graph = ((data.get("meta") or {}).get("request_graph") or {})
    for bucket in ("all_requests", "candidate_reads", "selected_steps", "filtered_requests"):
        for req in request_graph.get(bucket) or []:
            if req.get("headers"):
                req["headers"] = {k: "***" for k in (req.get("headers") or {})}
            if req.get("post_data") is not None:
                req["post_data"] = ""
            if req.get("response_json") is not None:
                req["response_json"] = _client_redact_sensitive(req.get("response_json"))
    for st in data.get("steps") or []:
        st["headers"] = {k: "***" for k in (st.get("headers") or {})}
        if st.get("body_source"):
            st["backup_body_source"] = st["body_source"]   # H20:保留原始 body 备份
            st["body_source"] = ""                         # 编辑面板默认空,用户/前端可显式填回
        if st.get("response_json") is not None:
            st["response_json"] = _client_redact_sensitive(st.get("response_json"))
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


def _find_param(step: FlowStep, param_path: str) -> ParamField:
    for param in step.params:
        if param.path == param_path:
            return param
    raise ValueError(f"param not found: {param_path} in step {step.step_id}")


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
    graph = (spec.meta or {}).get("request_graph") or {}
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
    rg = dict((spec.meta or {}).get("request_graph") or {})
    selected = list(rg.get("selected_steps") or [])
    sig = _request_graph_signature(entry)
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
        category = _classify_field_category(key, key, str(value))
        source_kind = "constant" if category == "system_const" else "user_input"
        step.params.append(ParamField(
            path=path,
            key=key,
            label=key,
            value=str(value),
            type=_param_type_from_value(value),
            required=False,
            category=category,
            source_kind=source_kind,
            source={"kind": source_kind, "path": key, "from": "query"},
            exposed_to_user=category == "user_param",
            editable=True,
            reason="从捕获接口 query 参数生成，可在字段页修改分类和来源",
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


def _rejected_dependency_sigs(spec: FlowSpec) -> set[str]:
    meta = spec.meta or {}
    return {str(x.get("sig") or x) for x in (meta.get("rejected_dependencies") or [])}


def _record_rejected_dependency(spec: FlowSpec, link: FlowLink) -> None:
    sig = _dependency_sig(link.source_step_id, link.source_path, link.target_step_id, link.target_path)
    rejected = list((spec.meta or {}).get("rejected_dependencies") or [])
    if not any(str(x.get("sig") or x) == sig for x in rejected):
        rejected.append({
            "sig": sig,
            "source_step_id": link.source_step_id,
            "source_path": link.source_path,
            "target_step_id": link.target_step_id,
            "target_path": link.target_path,
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
                if not ranked or ranked[0][0] <= 0:
                    continue
                if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
                    continue
                _score, source, source_path = ranked[0]
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
                confidence=0.93,
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
    for step in spec.steps:
        meta = step.source_meta or {}
        if request_id and str(meta.get("request_id") or "") == request_id:
            existing = step
            break
        if request_index is not None and meta.get("request_index") == request_index:
            existing = step
            break
    if existing is None and not request_id and request_index is None:
        existing = next((
            s for s in spec.steps
            if ((s.method or "").upper(), _request_path({"url": s.path or s.url})) == _request_graph_signature(entry)
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
        page_enum_options={},
        step_index=len(spec.steps),
    )
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


_CAPABILITY_ALLOWED_FIELDS = frozenset({
    "name", "title", "intent", "kind", "step_ids", "input_schema", "output_schema",
    "output_mapping", "preconditions", "confirmed", "confidence",
    "requires_human_confirm", "evidence", "caller_responsibilities", "skill_responsibilities",
    "nodes", "status", "locked", "updated_by",
})


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
            new_spec.capabilities = _merge_capability_lists(existing, build_default_flow_capabilities(new_spec))
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
            new_spec.capabilities.append(cap)
            continue

        if op == "remove_capability":
            idx = _find_capability_index(new_spec, edit)
            new_spec.capabilities.pop(idx)
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
            setattr(cap, field, value)
            if field == "confirmed" and value:
                cap.requires_human_confirm = False
                cap.status = "confirmed"
            elif field != "updated_by":
                cap.updated_by = "user"
            if field in {"step_ids", "nodes"}:
                _sync_capability_order(new_spec, cap)
            continue

        if op == "add_capability_step":
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
            _find_step(new_spec, step_id)
            if step_id not in cap.step_ids:
                cap.step_ids.append(step_id)
            if not any(n.get("type") == "call" and n.get("step_id") == step_id for n in (cap.nodes or [])):
                cap.nodes.append({"id": f"call_{len(cap.nodes or []) + 1}", "type": "call", "step_id": step_id})
            _sync_capability_order(new_spec, cap)
            continue

        if op == "remove_capability_step":
            idx = _find_capability_index(new_spec, edit)
            step_id = str(edit.get("step_id") or "")
            new_spec.capabilities[idx].step_ids = [sid for sid in new_spec.capabilities[idx].step_ids if sid != step_id]
            new_spec.capabilities[idx].nodes = [
                n for n in (new_spec.capabilities[idx].nodes or [])
                if not (n.get("type") == "call" and n.get("step_id") == step_id)
            ]
            _sync_capability_order(new_spec, new_spec.capabilities[idx])
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
                continue

            if op == "remove":
                link = _find_link(new_spec, link_id)
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

            if not field:
                raise ValueError("update edit missing field")

            if param_path:
                # 参数级编辑
                param = _find_param(step, param_path)
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
                        if lk.target_step_id == step.step_id and _clean_path_prefix(lk.target_path, "body.") == old_path:
                            lk.target_path = new_path
                elif field == "value":
                    param.value = str(value)
                    step.sample_inputs[param.key] = param.value
                elif field == "type":
                    param.type = str(value)
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
            continue

        elif op == "reset_param_source":
            param_path = edit.get("param_path")
            if not param_path:
                raise ValueError("reset_param_source missing param_path")
            param = _find_param(step, param_path)
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
            param = _find_param(step, param_path)
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
允许操作:
- {"op":"promote_request","request_id":"...","request_index":1}
- {"op":"rename_field","step_id":"...","path":"...","label":"请假类型"}
- {"op":"bind_response_source","target_step":"...","target_path":"...","source_step":"...","source_path":"..."}
- {"op":"mark_field_as_system_var","step_id":"...","path":"..."}
- {"op":"mark_field_as_identity","step_id":"...","path":"...","source":"current_user"}
- {"op":"create_capability","name":"...","title":"...","kind":"query_status|list_options|validate_batch|submit_batch|submit","step_ids":[...],"nodes":[...]}
- {"op":"reorder_capability_steps","capability":"...","step_ids":[...]}
拿不准就不要改。"""


def _flow_autofix_context(spec: FlowSpec, report: dict[str, Any]) -> dict[str, Any]:
    graph = (spec.meta or {}).get("request_graph") or {}
    return {
        "title": spec.title,
        "goal": spec.goal,
        "errors": list(report.get("errors") or [])[:40],
        "warnings": list(report.get("warnings") or [])[:40],
        "capability_validation": report.get("capability_validation") or {},
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
                    }
                    for p in (st.params or [])[:60]
                ],
                "response_paths": [p for p, *_ in (_leaf_paths(st.response_json)[:80] if st.response_json is not None else [])],
            }
            for st in spec.steps
        ],
        "capabilities": [cap.model_dump(exclude_none=True) for cap in spec.capabilities],
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
    }


def _autofix_ops_to_edits(spec: FlowSpec, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    cap_by_name = {c.name: idx for idx, c in enumerate(spec.capabilities or []) if c.name}
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "")
        if kind == "promote_request":
            edits.append({
                "op": "add_request_step",
                "request_id": str(op.get("request_id") or ""),
                "request_index": op.get("request_index"),
            })
        elif kind == "rename_field":
            step_id = str(op.get("step_id") or "")
            path = str(op.get("path") or "")
            label = str(op.get("label") or "").strip()
            if step_id and path and label:
                edits.append({"op": "update", "step_id": step_id, "param_path": path, "field": "key", "value": label})
        elif kind == "bind_response_source":
            source_step = str(op.get("source_step") or "")
            target_step = str(op.get("target_step") or "")
            source_path = str(op.get("source_path") or "")
            target_path = str(op.get("target_path") or "")
            if source_step and target_step and source_path and target_path:
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
        elif kind == "mark_field_as_system_var":
            step_id = str(op.get("step_id") or "")
            path = str(op.get("path") or "")
            if step_id and path:
                edits.extend([
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "category", "value": "runtime_var"},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "source_kind", "value": "unknown"},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "exposed_to_user", "value": False},
                ])
        elif kind == "mark_field_as_identity":
            step_id = str(op.get("step_id") or "")
            path = str(op.get("path") or "")
            source = str(op.get("source") or "current_user")
            if step_id and path:
                edits.extend([
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "category", "value": "runtime_var"},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "source_kind", "value": source},
                    {"op": "update", "step_id": step_id, "param_path": path, "field": "exposed_to_user", "value": False},
                ])
        elif kind == "create_capability":
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
    return edits


def _auto_fix_target_capability_name(spec: FlowSpec) -> str:
    caps = list(spec.capabilities or build_default_flow_capabilities(spec))
    for kind in ("submit_batch", "submit", "query_status", "list_options", "validate_batch"):
        cap = next((c for c in caps if c.kind == kind and c.name), None)
        if cap is not None:
            return cap.name
    return caps[0].name if caps else "submit_batch"


async def auto_fix_flow_spec(
    spec: FlowSpec,
    *,
    llm_client: Any | None = None,
    model: str | None = None,
    timeout_s: float = 60.0,
    max_rounds: int = 3,
) -> FlowSpec:
    """一键修正：确定性补齐 + 可选 LLM 受限 patch + 重新校验。"""
    current = spec.model_copy(deep=True)
    _normalize_capability_references(current)
    history: list[dict[str, Any]] = []
    for round_idx in range(max_rounds):
        report = validate_flow_spec(current)
        edits: list[dict[str, Any]] = []
        if not current.capabilities and current.steps:
            edits.append({"op": "generate_capabilities"})
        cap_report = report.get("capability_validation") or {}
        for item in cap_report.get("unused_high_confidence_requests") or []:
            role = item.get("role") or ""
            if role not in {"submit_anchor", "business_write", "business_get", "read_context"}:
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
                "capability_name": _auto_fix_target_capability_name(current),
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
                    edits.extend(_autofix_ops_to_edits(current, raw_ops))
            except Exception:  # noqa: BLE001
                pass
        if not edits:
            history.append({"round": round_idx, "applied": 0, "remaining_errors": len(report.get("errors") or [])})
            break
        before = _flow_fingerprint(current)
        current = apply_flow_edits(current, edits)
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
    return append_flow_version(refresh_review_items(_sync_capability_io_schemas(current)), "auto_fix", reason="一键自动修正")


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
