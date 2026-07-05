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
    enum_options: list[str] | None = None
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
    options: list[str] | None = None
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
})
_STEP_ALLOWED_FIELDS = frozenset({
    "selects", "identity", "params", "sample_inputs",
    "source_meta", "semantic_role", "success_rule", "fact_check",
    "response_json", "notes",
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


class FlowSpec(BaseModel):
    flow_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    tenant: str = ""
    subsystem: str = ""
    title: str = ""
    business_description: str = ""
    steps: list[FlowStep] = Field(default_factory=list)
    links: list[FlowLink] = Field(default_factory=list)
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
    s = str(value if value is not None else "").strip()
    if not s:
        return False
    if s.isdigit() and len(s) in (10, 13):
        return True
    if _UUID_LITERAL_RE.match(s):
        return True
    if _SESSION_LITERAL_RE.match(s) and re.search(r"\d{4,}", s):
        return True
    return False


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

    if _looks_session_specific_value(value) and value not in _sample_value_set(samples):
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

    # GET 请求：从 URL query string 提参
    if method == "GET" or body is None:
        list_paths: list[str] = []
        selects_raw: list[dict] = []
        iden_raw: list[dict] = []
        flat_fields = _params_from_get_query(req)
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
            enum_options=list(select_meta.options or []) if select_meta and select_meta.options else None,
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


def _strip_body_prefix(path: str) -> str:
    return path[len("body."):] if path.startswith("body.") else path


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
            p.source_kind = "unknown"
            p.source = {}
            p.editable = False
            p.exposed_to_user = False
            p.reason = "该字段曾绑定上游响应，但对应 link 已删除或目标已改变，需要重新确认来源"
            p.need_human_confirm = True
            p.confidence_tier = "stale_link"
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
    tenant: str = "",
    subsystem: str = "",
) -> FlowSpec:
    """收敛：把 record_ws 现有产物 → FlowSpec（包含 GET 业务请求）。"""
    reads = reads or []
    samples = samples or {}
    required_labels = required_labels or set()
    page_enum_options = page_enum_options or {}

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
        return ensure_flow_version(refresh_review_items(FlowSpec(
            tenant=tenant,
            subsystem=subsystem,
            title="(未捕获到业务请求)",
            meta={
                "captured_total": len(captured_requests),
                "captured_write_candidates": 0,
                "reads_count": len(flow_reads),
                "request_roles": request_roles,
                "note": "录制未抓到任何业务写请求或业务 GET；用户可能未点提交，或页面是纯 GET 表单",
            },
        )), "recorded", reason="录制生成空 FlowSpec")

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

    return ensure_flow_version(refresh_review_items(FlowSpec(
        tenant=tenant,
        subsystem=subsystem,
        title=title,
        business_description="",
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
            "schema_version": 1,
        },
    )), "recorded", reason="录制生成 FlowSpec 初版")


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


def _title_without_step_suffix(title: str) -> str:
    text = str(title or "").strip()
    text = re.sub(r"\s*[\(（]\s*\d+\s*步\s*[\)）]\s*$", "", text)
    return text.strip()


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
                severity="medium",
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
        "step_count": len(spec.steps),
        "link_count": len(spec.links),
        "review_count": len(spec.review_items),
        "current_version": spec.meta.get("current_version"),
        "risk_level": spec.risk_level,
        "schema_version": spec.schema_version,
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
    if param.source_kind == "unknown":
        return (
            f"字段 `{param.path}` 是运行期变量，当前来源 `{param.source_kind}` 还不能执行；"
            "请在字段页绑定上游响应/请求头/接口候选，或改为用户参数"
        )
    if not param.source:
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


def validate_flow_spec(spec: FlowSpec) -> dict:
    from dano.execution.page.repair_ops import collect_repair_findings

    errors: list[str] = []
    warnings: list[str] = []
    review_items = refresh_review_items(spec.model_copy(deep=True)).review_items
    api_request, build_errors = flow_spec_to_api_request(spec)
    errors.extend(build_errors)
    if not flow_spec_user_params(spec):
        warnings.append("FlowSpec 没有 user_param，发布后的 Skill 不会要求用户输入参数")
    for st in spec.steps:
        for p in st.params:
            if p.category == "runtime_var" and p.source_kind == "unknown":
                warnings.append(f"字段 `{p.path}` 被判为 runtime_var，但来源仍需确认")
            if p.category == "system_const" and p.exposed_to_user:
                warnings.append(f"字段 `{p.path}` 是 system_const，但仍暴露给用户")
    for lk in spec.links:
        if not lk.confirmed:
            warnings.append(f"链接 `{lk.link_id}` 尚未人工确认")
    if not any((st.success_rule for st in spec.steps)):
        warnings.append("未识别到明确 success_rule，运行期只能使用通用成功判断")
    self_check_errors: list[str] = []
    if api_request is not None:
        self_check_errors = self_check(api_request)
        errors.extend(self_check_errors)
        repair_findings = collect_repair_findings(api_request)
        session_errors = [f.get("detail", "") for f in repair_findings if f.get("kind") == "session_constant"]
        errors.extend([x for x in session_errors if x])
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
    data = refresh_review_items(spec.model_copy(deep=True)).model_dump()
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


def apply_flow_edits(spec: FlowSpec, edits: list[dict[str, Any]]) -> FlowSpec:
    """应用编辑列表，返回新 FlowSpec（深拷贝）。"""
    if not edits:
        return refresh_review_items(spec.model_copy(deep=True))

    new_spec = spec.model_copy(deep=True)
    bulk_review_resolutions: list[tuple[set, set, bool]] = []

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
    return append_flow_version(
        refresh_review_items(new_spec),
        "flow_edit",
        reason=actions[:200],
        actor="user",
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
    current = refresh_review_items(spec.model_copy(deep=True))
    lines: list[str] = [
        "# 业务流程说明",
        "",
        "## 1. 业务目的",
        _llm_purpose(current, llm_client) or _default_purpose(current),
        "",
        "## 2. 用户需要提供的参数",
    ]

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
