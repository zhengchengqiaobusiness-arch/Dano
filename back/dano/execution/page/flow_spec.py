"""Step A+B+C+D: FlowSpec 完整实现。

- Step A: 收敛函数 to_flow_spec（包含 GET 业务请求）
- Step B: 编辑函数 apply_flow_edits（字段/参数/链接/重排）
- Step C: 链接编辑支持
- Step D: GET 表单手选 + Pi Agent 命名 + 业务说明
"""

from __future__ import annotations

import re
import uuid
import json
import copy
import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from urllib.parse import unquote, urlparse, parse_qs, urlencode

# 复用 request_capture 的纯函数
from dano.execution.page.request_capture import (
    _is_const_value,
    _fact_path_tokens,
    _leaf_paths,
    _parse_body,
    _is_system_timestamp,
    bounded_response_sample,
    normalized_leaf_paths,
    as_list_payload,
    apply_page_enum_options,
    build_api_request,
    classify_request_role,
    discover_step_links,
    select_dependency_source,
    page_enum_selects,
    extract_auth_headers,
    flatten_body,
    infer_success_rule,
    write_requests,
    looks_internal_param_name,
    looks_like_auth_write,
    looks_like_read_request,
    parse_recorded_request_body,
    self_check,
    substitute,
    suggest_assignee_names,
    suggest_fact_check,
    suggest_identity,
    suggest_list_selects,
    suggest_select_names,
    suggest_selects,
    _is_idlike,
    _multipart_contains_file,
    _pick_label_key,
)
from dano.execution.page.value_tracing import discover_response_key_maps, discover_workflow_value_links



from dano.execution.page.flow_spec_core.models import (
    CapabilityDependency,
    CapabilityField,
    CapabilityRelation,
    CapabilityRequestRef,
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowSpecConflictError,
    FlowStep,
    IdentityBinding,
    ParamField,
    RecordedGoal,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    RequestUsage,
    ReviewItem,
    SelectBinding,
    SystemValue,
    register_sync_flow_spec_models,
)
from dano.execution.page.flow_spec_core.serialization import flow_spec_release_payload
from dano.execution.page.flow_spec_core.fingerprints import (
    _flow_fingerprint,
    _stable_json_hash,
    flow_spec_fingerprint,
)


# ─────────── 数据模型 ───────────






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
    "system_const_exposed",
    "broken_link",
    "link_source_missing",
    "link_target_missing",
    "link_confirmation",
})
































def executable_flow_links(spec: FlowSpec) -> list[FlowLink]:
    """Return dependencies backed by replay or immutable recording evidence."""
    trusted_verification_ids = {
        str(item.get("verification_id"))
        for item in (spec.meta or {}).get("verification_log") or []
        if isinstance(item, dict)
        and item.get("status") == "passed"
        and item.get("verification_id")
    }
    by_id = {step.step_id: step for step in spec.steps}
    executable: list[FlowLink] = []
    for link in spec.links or []:
        meta = dict(link.meta or {})
        verification_id = str(
            meta.get("verification_id")
            or (link.evidence or {}).get("verification_id")
            or ""
        )
        active = meta.get("active", True) is not False
        machine_verified = bool(
            link.confirmed
            and meta.get("verified") is True
            and verification_id in trusted_verification_ids
        )
        capture_grounded = bool(
            not meta.get("unverified_reason")
            and (
                meta.get("captured_value_match") is True
                or meta.get("captured_structure_match") is True
                or meta.get("captured_record_hydration") is True
            )
        )
        if not (active and (machine_verified or capture_grounded)):
            continue
        if _link_is_auto_generated(link):
            target = by_id.get(link.target_step_id)
            target_param = (
                _resolve_param_reference(target, link.target_path)
                if target is not None else None
            )
            if (
                not _auto_link_has_grounded_contract(spec.steps, link)
                or not _auto_dependency_link_allowed(
                    target_param, link.source_path, link,
                )
            ):
                continue
        executable.append(link)
    return executable


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


_CURRENT_USER_LEAVES = frozenset({
    "currentuser", "currentuserid", "loginuser", "loginuserid",
})




def _looks_current_user_field(key: str, path: str) -> bool:
    """Only the acting-user leaf itself is a current-user heuristic.

    Substring matches such as ``assignUserId`` are ordinary choosers.
    Proven identity bindings still win earlier via ``identity_paths``.
    """
    if "[" in str(path or ""):
        return False
    return _field_leaf_token(key, path) in _CURRENT_USER_LEAVES


def _looks_runtime_field(key: str, path: str) -> bool:
    k = _norm_field_name(key, path)
    return any(x in k for x in (
        "taskid", "draftid", "instanceid", "processinstanceid", "conversationid",
        "conversation_id", "sessionid", "nonce", "uuid", "token", "accesstoken",
        "refreshtoken",
    ))


_SESSION_LITERAL_RE = re.compile(r"^[A-Za-z]{2,}[-_]\d{4,}")
_UUID_LITERAL_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-")


def _looks_session_specific_value(value: Any) -> bool:
    """值像「一次性会话字面值 / 运行期 ID / uuid / 雪花 ID」等不能固化的字面值。
    关键:不绑定具体业务字段名,只看值本身特征 + 适当弱化兜底:
    - 13 位纯数字:`*`可能是* 当前毫秒时间戳,也可能是用户填的申请起止时间。只在「key 也是运行期字面」
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
                                  "createdate", "begindate",
                                  "starttime", "endtime", "startdate", "enddate")):
        return False
    # datetime 字段名 → 当 datetime,不当 session literal
    if any(x in norm for x in ("time", "date", "day")) and not any(x in norm for x in ("id", "key", "code", "token")):
        return False
    # 条形码/扫码字段：13 位数字是物理条码而非运行期 ID；不当 session_literal
    # barcode/qrcode/scancode/eancode/upccode 等名称里虽含「code」，但代表扫描出的业务标识
    if any(x in norm for x in ("barcode", "qrcode", "scancode", "eancode", "upccode",
                                "pincode", "serialcode", "serialno", "serialnum")):
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
        "formtype", "flowtype", "businesstype", "templateid", "template_id",
        "formid", "menuid", "appid", "appname",
    ))


_PAGE_CONTEXT_LEAVES = frozenset({
    "deptid", "deptname", "departmentid",
    "departmentname", "orgid", "orgname", "organid", "organname",
    "companyid", "companyname", "tenantid", "tenantname",
})






def _record_identity_is_caller_owned(method: str, value: Any) -> bool:
    """A recorded row id is a sample, not a reusable constant."""
    if value in (None, "") or _is_missing_wire_placeholder(value):
        return False
    if str(value).strip().casefold() in {"null", "undefined"}:
        return False
    method = str(method or "").upper()
    if method == "POST" and str(value).strip() in {"0", "0.0"}:
        return False
    return method in {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _param_is_document_record_identity(param: ParamField) -> bool:
    return _is_document_record_identity_path(param.key, param.path)


def _step_has_stable_record_identity(step: FlowStep) -> bool:
    """Distinguish an edit of an existing record from a new submission."""
    for param in step.params or []:
        if not _param_is_document_record_identity(param):
            continue
        value = param.value
        if value is None or _is_missing_wire_placeholder(value):
            continue
        if str(value).strip().casefold() in {"", "null", "undefined"}:
            continue
        return True
    return False


def _looks_page_context_field(key: str, path: str) -> bool:
    """Page-context heuristics are exact leaves, never short-stem substrings.

    Tokens such as ``org`` / ``unitname`` appear inside ordinary business
    fields (``originalPrice``, line UOM). Nested array rows are never tenant
    or department context.
    """
    if "[" in str(path or ""):
        return False
    leaf = _field_leaf_token(key, path)
    if leaf in _PAGE_CONTEXT_LEAVES:
        return True
    return any(
        leaf.endswith(token)
        for token in (
            "departmentid", "departmentname", "tenantid", "tenantname",
            "companyid", "companyname", "organizationid", "organizationname",
        )
    )


_OPTION_SOURCE_KINDS = {"api_option", "page_enum", "static_enum", "manual_enum", "form_option"}




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
    payload = read.get("json", read.get("response_json"))
    has_list_payload = bool(as_list_payload(payload))
    if _read_is_entity_enrichment_lookup(read):
        return False
    if _request_has_business_query_evidence(read):
        # A business collection and an option endpoint can both return rows
        # shaped like ``{id, name}``.  A recorded search/list action with real
        # business filters owns those fields as caller-facing query inputs; its
        # result shape alone must not turn the whole request into a chooser.
        return False
    if role == "explicit_read_option":
        return has_list_payload
    if role == "read_option":
        return has_list_payload
    if has_list_payload and _choice_control_triggered(read):
        # Endpoint naming is not portable. A list response causally triggered by
        # opening/selecting a choice control is grounded option-source evidence.
        return True
    if not role and has_list_payload and _list_payload_has_reference_contract(payload):
        # Explicit read snapshots may predate causal metadata. They are only
        # candidates here; field binding still requires an exact selected wire
        # value plus control/semantic ownership and rejects collisions.
        return True
    return False


def _read_transport_can_supply_options(read: dict) -> bool:
    """Allow ordinary reads and explicitly classified POST option queries."""
    method = str(read.get("method") or "GET").upper()
    if method in {"GET", "HEAD"}:
        return True
    if method != "POST" or _read_is_entity_enrichment_lookup(read):
        return False
    role = str(read.get("role") or read.get("request_role") or "")
    return role in {"read_option", "option_source", "explicit_read_option"} or (
        _choice_control_triggered(read)
        and looks_like_read_request(
            str(read.get("url") or read.get("path") or ""),
            read.get("post_data"),
        )
    )


def _read_is_business_entity_collection(read: dict, payload: Any) -> bool:
    """Return whether a business read can resolve one caller-selected entity."""
    return bool(
        str(read.get("role") or read.get("request_role") or "") == "business_get"
        and str(read.get("method") or "GET").upper() in {"GET", "HEAD"}
        and _list_payload_has_reference_contract(payload)
        and not _read_is_entity_enrichment_lookup(read)
        and not _request_has_option_endpoint_hint(read)
        and not _request_has_reference_entity_hint(read)
    )


def _option_candidate_reads(reads: list[dict] | None) -> list[dict]:
    return [
        read for read in (reads or [])
        if isinstance(read, dict)
        and not _read_is_entity_enrichment_lookup(read)
        and (
            _read_is_option_source(read)
            or str(
                read.get("role")
                or read.get("request_role")
                or (read.get("_request_role") or {}).get("role")
                or ""
            ) in {"option", "read_option", "option_source", "explicit_read_option"}
            or _choice_control_triggered(read)
        )
    ]


def _option_source_contract_endpoint(url: str) -> str:
    """Normalize snapshot pagination while preserving semantic query scope."""
    parsed = urlparse(str(url or ""))
    endpoint = f"{parsed.netloc}{parsed.path}" if parsed.path else str(url or "")
    snapshot_keys = {
        "cursor", "keyword", "search", "searchtext", "q", "query", "_",
        "ts", "timestamp", "nonce", "cachebuster", "sort", "sortby",
        "order", "orderby", "token", "accesstoken", "auth", "authorization",
        "apikey", "signature", "sign", "sig", "session", "sessionid", "jwt",
        "traceid", "spanid",
    }
    semantic_query: list[tuple[str, Any]] = []
    for key, values in sorted(parse_qs(parsed.query, keep_blank_values=True).items()):
        normalized_key = re.sub(r"[^a-z0-9]+", "", key.casefold())
        if (
            normalized_key in snapshot_keys
            or _looks_pagination_field(key, f"query.{key}")
        ):
            continue
        semantic_query.extend((key, value) for value in values)
    if semantic_query:
        endpoint += "?" + urlencode(semantic_query, doseq=True)
    return endpoint


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


_MISSING_WIRE_PLACEHOLDERS = {
    "undefined", "null", "none", "nan", "[object object]",
}


def _is_missing_wire_placeholder(value: Any) -> bool:
    """Return whether a captured textual value represents no wire value."""
    return isinstance(value, str) and value.strip().casefold() in _MISSING_WIRE_PLACEHOLDERS


def _recorded_param_sample(value: Any) -> Any:
    """Preserve false/0; only missing values become an empty sample."""
    if value is None:
        return ""
    return value


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
    query_is_option_source: bool = False,
    query_is_business_query: bool = False,
) -> dict[str, Any]:
    raw_value = field.get("value")
    value = "" if raw_value is None else str(raw_value)

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
        default_value = field.get("visible_default")
        if default_value in (None, ""):
            default_value = field.get("raw_value", field.get("value"))
        return {
            "category": "runtime_var",
            "source_kind": "page_context",
            "source": {
                "kind": "page_context",
                "context_key": str(field.get("key") or key or path).split(".")[-1].split("[")[0],
                "path": path,
                "default_value": default_value,
                "caller_override": False,
                "required_state": "optional",
            },
            "editable": False,
            "exposed_to_user": False,
            "reason": "分页参数由运行上下文使用录制默认值自动注入，不作为业务筛选字段暴露给调用方",
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

    # A value captured from a real user interaction is stronger evidence than
    # GET/query naming heuristics.  This also prevents genuine search filters
    # on option endpoints from being hidden as constants.
    # GET filters must carry field-local interaction evidence.  A global value
    # match is unsafe because unrelated query parameters often share 0/1; it
    # previously exposed an option-source's fixed ``status=0`` merely because
    # another control recorded the same value.
    early_control_kind = str(field.get("control_kind") or "unknown").lower()
    if (
        early_control_kind in {"select", "combobox"}
        and not bool(field.get("control_disabled"))
    ):
        return {
            "category": "user_param",
            "source_kind": "form_option",
            "source": {"kind": "form_option", "path": path},
            "editable": True,
            "exposed_to_user": True,
            "reason": "页面快照证明该字段是可编辑选择控件；候选来自表单控件，不能降级为手动文本",
            "need_human_confirm": False,
        }

    recorded_user_input = bool(field.get("recorded_user_input"))
    if recorded_user_input:
        return {
            "category": "user_param",
            "source_kind": "user_input",
            "source": {"kind": "sample", "path": path, "recorded": True},
            "editable": True,
            "exposed_to_user": True,
            "reason": "该值由用户在录制页面真实填写，调用 Skill 时作为用户参数",
            "need_human_confirm": False,
        }

    control_kind = str(field.get("control_kind") or "unknown").lower()
    has_control = bool(field.get("field_aliases")) or control_kind != "unknown"
    # Custom select/combobox widgets commonly render a readonly inner input
    # while the surrounding control remains fully interactive.  ``disabled``
    # locks the widget; ``read_only`` only locks text entry and must not hide a
    # real user selection from the public capability contract.
    control_locked = bool(field.get("control_disabled")) if control_kind in {
        "select", "combobox",
    } else bool(field.get("control_disabled") or field.get("control_read_only"))
    if has_control and control_kind in {"select", "combobox"} and not control_locked:
        return {
            "category": "user_param",
            "source_kind": "form_option",
            "source": {"kind": "form_option", "path": path},
            "editable": True,
            "exposed_to_user": True,
            "reason": "页面快照证明该字段是可编辑选择控件；候选来自表单控件，不能降级为手动文本",
            "need_human_confirm": False,
        }
    if has_control and control_kind in {"text", "textarea", "number", "date", "datetime", "time", "checkbox", "radio"}:
        if control_locked:
            return {
                "category": "runtime_var",
                "source_kind": "page_rule",
                "source": {
                    "kind": "page_rule",
                    "path": path,
                    "control_kind": control_kind,
                },
                "editable": False,
                "exposed_to_user": False,
                "reason": "页面只读或禁用控件上的值由前端规则写入，运行期沿用页面结果，不作为调用方输入",
                "need_human_confirm": False,
            }
        if method == "GET" and path.startswith("query."):
            return {
                "category": "user_param",
                "source_kind": "user_input",
                "source": {"kind": "control_default", "path": path, "required_state": "unknown"},
                "editable": True,
                "exposed_to_user": True,
                "reason": "查询页上的可编辑筛选控件；调用方可省略或覆盖录制时的筛选值",
                "need_human_confirm": False,
            }
        default_value = field.get("visible_default")
        if default_value in (None, ""):
            default_value = field.get("raw_value", field.get("value"))
        return {
            "category": "user_param",
            "source_kind": "page_default",
            "source": {
                "kind": "page_default",
                "path": path,
                "default_value": default_value,
                "caller_override": True,
            },
            "editable": True,
            "exposed_to_user": True,
            "reason": "页面预填了该值，但控件可改；调用方可沿用或覆盖，不能因为录制时没改就改成系统包办",
            "need_human_confirm": False,
        }

    if (
        _is_document_record_identity_path(key, path)
        and _record_identity_is_caller_owned(method, field.get("raw_value", field.get("value")))
    ):
        return {
            "category": "user_param",
            "source_kind": "user_input",
            "source": {"kind": "selected_record_identity", "path": path, "required_state": "required"},
            "editable": True,
            "exposed_to_user": True,
            "required": True,
            "reason": "该字段是打开/更新/删除目标记录的标识；调用方必须传入要操作的记录，不能把录制时点中的 ID 当成可复用常量",
            "need_human_confirm": False,
        }

    if method == "GET" and path.startswith("query."):
        if query_is_option_source:
            return {
                "category": "runtime_var",
                "source_kind": "unknown",
                "source": {"kind": "option_query_filter", "path": path},
                "editable": False,
                "exposed_to_user": False,
                "reason": "候选接口上的查询参数没有对应可编辑控件，标注未知；请求仍按录制原样携带，不影响原接口",
                "need_human_confirm": True,
            }
        if query_is_business_query and _caller_filter_key(key, path) and (
            recorded_user_input
            or str(field.get("control_kind") or "unknown").lower() not in {"", "unknown"}
            or bool(field.get("field_aliases"))
        ):
            return {
                "category": "user_param",
                "source_kind": "user_input",
                "source": {
                    "kind": "business_query_filter",
                    "path": path,
                    "required_state": "unknown",
                },
                "editable": True,
                "exposed_to_user": True,
                "reason": "业务查询上的筛选条件由调用方提供；未看到 required 或成功省略证据时保持 unknown",
                "need_human_confirm": False,
            }
        return {
            "category": "runtime_var",
            "source_kind": "unknown",
            "source": {"kind": "unresolved_query", "path": path},
            "editable": False,
            "exposed_to_user": False,
            "reason": "该查询字段没有控件或候选证据，标注未知；请求仍按录制原样携带",
            "need_human_confirm": True,
        }

    # 录制期间由用户真实填写/选择并出现在 samples 中，是字段归属的强事实。
    # 它必须优先于 *Id/*Type 等命名启发式；否则不同系统的业务字段只因内部
    # 命名像 ID/状态码就会被错误改成运行期变量或系统常量。
    if value == "" and value not in _sample_value_set(samples):
        return {
            "category": "runtime_var",
            "source_kind": "unknown",
            "source": {"kind": "empty_field", "path": path},
            "editable": False,
            "exposed_to_user": False,
            "reason": "该字段没有可编辑控件或填写证据，标注未知；请求仍按录制空值携带",
            "need_human_confirm": True,
        }

    if (
        _is_document_record_identity_path(key, path)
        and _record_identity_is_caller_owned(method, field.get("raw_value", field.get("value")))
    ):
        return {
            "category": "user_param",
            "source_kind": "user_input",
            "source": {"kind": "selected_record_identity", "path": path, "required_state": "required"},
            "editable": True,
            "exposed_to_user": True,
            "required": True,
            "reason": "该字段是删除/更新目标记录的标识；调用方必须传入要操作的记录，不能把录制时点中的 ID 当成可复用常量",
            "need_human_confirm": False,
        }

    # 系统化:datetime 字段(用户填的具体时间)即使值是 13 位毫秒,也不当 session_literal。
    # 同时若字段名像「具体时间字段」(start* / end* 等),放行 user_input。
    if _looks_session_literal_after_key_check(value, key, path) and value not in _sample_value_set(samples):
        return {
            "category": "runtime_var",
            "source_kind": "unknown",
            "source": {"kind": "session_literal", "path": path},
            "editable": False,
            "exposed_to_user": False,
            "reason": "没有控件或上游证据证明该值的来源，标注未知；请求仍按录制原样携带",
            "need_human_confirm": True,
        }

    return {
        "category": "runtime_var",
        "source_kind": "unknown",
        "source": {"kind": "unresolved", "path": path},
        "editable": False,
        "exposed_to_user": False,
        "reason": "没有可编辑控件、上游响应或计算公式证明来源，标注未知；请求仍按录制原样携带",
        "need_human_confirm": True,
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
        pair = _enum_label_value(o)
        if pair is None:
            continue
        label, parsed_value = pair
        if om and label in om:
            out.append({"label": label, "value": om[label]})
        elif (
            isinstance(o, dict) and "value" in o and o.get("value") is not None
        ) or (isinstance(o, (list, tuple)) and len(o) >= 2 and o[1] is not None):
            out.append({"label": label, "value": parsed_value})
        else:
            out.append(label)
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
    """Normalize string/dict/list/tuple options to a display label and wire value."""
    if isinstance(opt, dict):
        raw_label = next(
            (
                opt.get(key) for key in ("label", "text", "name", "value")
                if opt.get(key) not in (None, "")
            ),
            "",
        )
        label = str(raw_label).strip()
        if not label:
            return None
        return label, opt.get("value", label)
    if isinstance(opt, (list, tuple)):
        if not opt:
            return None
        label = str(opt[0] if opt[0] is not None else "").strip()
        if not label:
            return None
        return label, opt[1] if len(opt) >= 2 else opt[0]
    label = str(opt or "").strip()
    if not label:
        return None
    return label, label


def _explicit_enum_value_map(options: list[Any] | None, value_map: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only recorded or operator-supplied label/value pairs; never invent identity pairs."""
    explicit = dict(value_map or {})
    for option in options or []:
        if not (
            (isinstance(option, dict) and "value" in option)
            or (isinstance(option, (list, tuple)) and len(option) >= 2)
        ):
            continue
        pair = _enum_label_value(option)
        if pair is not None:
            explicit.setdefault(pair[0], pair[1])
    return explicit


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


def _recorded_scalar_values_match(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


_BORING_COMPOSITE_VALUES = frozenset({
    "", "0", "1", "true", "false", "null", "none", "undefined",
})


def _composite_values_match(left: Any, right: Any) -> bool:
    if _recorded_scalar_values_match(left, right):
        return True
    if left in (None, "") or right in (None, ""):
        return False
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def _projection_path_score(source_path: str, target_path: str) -> int:
    def parts(value: str) -> list[str]:
        return [
            token.casefold()
            for token in re.findall(r"[A-Za-z]+|\d+|[\u4e00-\u9fff]+", str(value or ""))
        ]

    source_parts = parts(source_path)
    target_parts = parts(target_path)
    if not source_parts or not target_parts:
        return 0
    if source_parts[-1] == target_parts[-1]:
        return 100
    if len(source_parts) >= 2 and "".join(source_parts[-2:]) == "".join(target_parts[-1:]):
        return 90
    if target_parts[-1] == "id" and source_parts[-1] == "id":
        return 40
    if source_parts[-1] == "id" and target_parts[-1].endswith("id"):
        return 40
    return 0


def _detect_composite_entity_selects(
    fields: list[dict],
    option_reads: list[dict],
    *,
    existing_paths: set[str],
) -> list[dict]:
    """Bind a chooser from a response row when several write fields match that row.

    Modal/table pickers often have no select widget.  A single short ID is not
    enough; two or more field matches on one unique row are.
    """
    out: list[dict] = []
    claimed = set(existing_paths)
    for read in option_reads or []:
        source_url = str(read.get("url") or "").strip()
        items = as_list_payload(read.get("json", read.get("response_json")))
        if not source_url or not items or not isinstance(items[0], dict):
            continue
        row_hits: list[tuple[dict, list[tuple[dict, str]]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            matches: list[tuple[dict, str]] = []
            for field in fields:
                target_path = str(field.get("path") or "")
                control_kind = str(field.get("control_kind") or "").lower()
                if not target_path or target_path in claimed:
                    continue
                if field.get("recorded_user_input") and control_kind not in {"select", "combobox"}:
                    continue
                if (
                    _field_has_unlocked_editable_control(field)
                    and control_kind not in {"select", "combobox"}
                ):
                    continue
                raw = field.get("raw_value", field.get("value"))
                if raw in (None, ""):
                    continue
                candidates = [
                    (_projection_path_score(source_path, target_path), source_path)
                    for source_path, _tokens, _raw_value, raw_leaf in _leaf_paths(item)
                    if _composite_values_match(raw, raw_leaf)
                    and (
                        _projection_path_score(source_path, target_path) >= 75
                        or (
                            str(raw).strip().casefold() not in _BORING_COMPOSITE_VALUES
                            and _projection_path_score(source_path, target_path) >= 50
                        )
                    )
                ]
                best = max((score for score, _path in candidates), default=0)
                best_paths = [path for score, path in candidates if score == best and best]
                if len(best_paths) == 1:
                    matches.append((field, best_paths[0]))
            if len(matches) >= 2:
                row_hits.append((item, matches))
        if len(row_hits) != 1:
            continue
        selected, matches = row_hits[0]
        chooser = next(
            (
                (field, source_path)
                for field, source_path in matches
                if _is_idlike(str(field.get("path") or field.get("key") or "").split(".")[-1])
            ),
            None,
        )
        if chooser is None:
            continue
        chooser_field, value_key = chooser
        chooser_path = str(chooser_field.get("path") or "")
        label_key = _pick_label_key(selected, value_key.split(".")[-1] if "." in value_key else value_key)
        if not label_key:
            continue
        records = []
        option_map: dict[str, Any] = {}
        seen_values: set[str] = set()
        valid = True
        for item in items:
            if not isinstance(item, dict):
                continue
            label = str(item.get(label_key) or "").strip()
            raw_value = item.get(value_key if value_key in item else value_key.split(".")[-1])
            if not label or raw_value in (None, "") or label in option_map or str(raw_value) in seen_values:
                valid = False
                break
            seen_values.add(str(raw_value))
            option_map[label] = raw_value
            records.append({"label": label, "value": raw_value})
        if not valid or len(records) < 2:
            continue
        projections = {
            str(field.get("path")): source_path
            for field, source_path in matches
            if str(field.get("path")) != chooser_path
        }
        claimed.add(chooser_path)
        claimed.update(projections)
        out.append({
            "path": chooser_path,
            "source_url": source_url,
            "source_request_id": str(read.get("request_id") or read.get("id") or ""),
            "value_key": value_key.split(".")[-1],
            "label_key": label_key,
            "count": len(records),
            "options": records,
            "option_map": option_map,
            "enum_source": "api",
            "enum_confirmed": True,
            "id_path": chooser_path,
            "field_projections": projections,
        })
    return out


def _field_has_unlocked_editable_control(field: dict | None) -> bool:
    """True when a page control can still accept caller input.

    Selected-row projections must not hide an editable number/text/date just
    because the captured value also appears on the chosen option. Locked
    siblings such as barcode or stock remain projectable.
    """
    if not isinstance(field, dict):
        return False
    kind = str(field.get("control_kind") or "unknown").lower()
    if field.get("control_disabled") is True:
        return False
    if kind in {"select", "combobox"}:
        return True
    if field.get("control_read_only") is True:
        return False
    return kind in {
        "text", "textarea", "number", "date", "datetime", "time",
        "checkbox", "radio", "spinbutton",
    }


def _projection_path_parts(value: Any) -> list[str]:
    raw_parts = [part for part in re.split(r"\.|\[\d+\]", str(value or "")) if part]
    return raw_parts


def _projection_leaf_norm(value: str) -> str:
    return re.sub(r"[^\w]+", "", str(value or ""), flags=re.UNICODE).replace("_", "").casefold()


def _projection_path_tokens(value: Any) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    return [token.casefold() for token in re.findall(r"[A-Za-z]+|\d+|[\u4e00-\u9fff]+", text)]


def _projection_path_score(source_path: str, target_path: str) -> int:
    """Score a catalog leaf against a write-body sibling by structure, not vendor names.

    Exact leaves win.  A write leaf may carry an entity prefix
    (``productName`` ← ``name``, ``productId`` ← ``id``); a catalog path may
    also be more specific than the write leaf.  Quantity and total leaves are
    filtered by the caller so ``price`` cannot claim ``totalPrice``.
    """
    source_parts = _projection_path_parts(source_path)
    target_parts = _projection_path_parts(target_path)
    if not source_parts or not target_parts:
        return 0
    source_leaf_raw = source_parts[-1]
    target_leaf_raw = target_parts[-1]
    source_leaf = _projection_leaf_norm(source_leaf_raw)
    target_leaf = _projection_leaf_norm(target_leaf_raw)
    if source_leaf == target_leaf:
        return 100
    if len(source_parts) >= 2 and _projection_leaf_norm("".join(source_parts[-2:])) == target_leaf:
        return 90
    if len(target_parts) >= 2 and _projection_leaf_norm("".join(target_parts[-2:])) == source_leaf:
        return 90
    source_tokens = _projection_path_tokens(source_leaf_raw)
    target_tokens = _projection_path_tokens(target_leaf_raw)
    if source_tokens == target_tokens:
        return 100
    if (
        source_tokens
        and target_tokens
        and len(source_leaf) >= 3
        and target_leaf.endswith(source_leaf)
        and target_tokens[-len(source_tokens):] == source_tokens
    ):
        return 80
    if (
        source_tokens
        and target_tokens
        and len(target_leaf) >= 3
        and source_leaf.endswith(target_leaf)
        and source_tokens[-len(target_tokens):] == target_tokens
    ):
        return 80
    if (
        source_tokens
        and target_tokens
        and len(source_leaf) >= 3
        and target_tokens[:len(source_tokens)] == source_tokens
    ):
        return 78
    if (
        source_tokens
        and target_tokens
        and len(target_leaf) >= 3
        and source_tokens[:len(target_tokens)] == target_tokens
    ):
        return 78
    if (
        target_tokens and target_tokens[-1] == "id"
        and source_tokens and source_tokens[-1] == "id"
    ):
        return 40
    return 0


def _best_option_projection_path(
    row: dict[str, Any],
    target_path: str,
    value: Any,
    *,
    min_score: int = 75,
) -> str:
    candidates = [
        (_projection_path_score(source_path, target_path), source_path)
        for source_path, _tokens, _raw_value, raw in _leaf_paths(row)
        if _composite_values_match(value, raw)
    ]
    best_score = max((score for score, _path in candidates), default=0)
    best_paths = [
        source_path for score, source_path in candidates
        if score == best_score and score >= min_score
    ]
    return best_paths[0] if len(best_paths) == 1 else ""


def _attach_select_field_projections(
    selects: list[dict],
    fields: list[dict],
    reads: list[dict],
) -> None:
    """Map sibling request fields from the same selected option object.

    A selector often returns the chosen record plus sibling business fields.
    Projection requires both equal captured values and a unique structural path
    match; endpoint names and vendor-specific field names are never consulted.
    """
    reads_by_path = {
        _request_path({"url": str(read.get("url") or read.get("path") or "")}): read
        for read in reads or [] if isinstance(read, dict)
    }

    for select in selects or []:
        source_url = str(select.get("source_url") or "")
        value_key = str(select.get("value_key") or "")
        label_key = str(select.get("label_key") or "")
        select_path = str(select.get("path") or "")
        if not source_url or not value_key or not label_key or not select_path:
            continue
        source = reads_by_path.get(_request_path({"url": source_url}))
        items = as_list_payload((source or {}).get("json", (source or {}).get("response_json"))) or []
        selected_field = next((item for item in fields if str(item.get("path") or "") == select_path), None)
        if selected_field is None:
            continue
        matches = [
            item for item in items if isinstance(item, dict)
            and _recorded_scalar_values_match(
                selected_field.get("raw_value", selected_field.get("value")),
                item.get(value_key),
            )
        ]
        if len(matches) != 1:
            continue
        selected_item = matches[0]
        excluded = {select_path, str(select.get("id_path") or "")}
        projections: dict[str, str] = {}
        for field in fields:
            target_path = str(field.get("path") or "")
            if (
                not target_path
                or target_path in excluded
                or field.get("recorded_user_input")
                or _field_has_unlocked_editable_control(field)
                or _param_is_quantity_or_formula_leaf(str(field.get("key") or ""), target_path)
            ):
                continue
            response_path = _best_option_projection_path(
                selected_item,
                target_path,
                field.get("raw_value", field.get("value")),
            )
            if response_path:
                projections[target_path] = response_path
        if projections:
            select["field_projections"] = projections


def _build_step_from_capture(
    req: dict,
    *,
    reads: list[dict],
    samples: dict,
    storage_state: dict | None,
    required_labels: set,
    page_enum_options: dict,
    step_index: int,
    field_evidence: list[dict] | None = None,
) -> FlowStep:
    method = (req.get("method") or "POST").upper()
    pd = req.get("post_data")
    body = _parse_body(pd)
    page_enum_options = _page_enum_options_for_request(req, page_enum_options)
    field_evidence = _field_evidence_for_request(req, field_evidence)
    if field_evidence and any(
        str(item.get("required_state") or "") == "required" or item.get("required") is True
        for item in field_evidence
        if isinstance(item, dict)
    ):
        # A single SPA page/frame may host several business routes. Required
        # markers must follow the route that emitted this request instead of
        # leaking from the last form snapshot into an earlier query contract.
        required_labels = {
            str(item.get("label") or item.get("field") or "").strip()
            for item in field_evidence
            if (
                str(item.get("required_state") or "") == "required"
                or item.get("required") is True
            )
            and str(item.get("label") or item.get("field") or "").strip()
        }

    # 风险 + 语义角色
    role = classify_request_role(req)
    request_role = req.get("_request_role") or {}
    risk = request_role.get("risk_level") or role.get("risk_level", "L3")

    def has_real_enum_source(sb: SelectBinding) -> bool:
        return bool(sb.options) or bool(sb.source_url and sb.value_key and sb.label_key)

    option_reads = _option_candidate_reads([
        read for read in (reads or [])
        if _recording_evidence_matches_request(req, read)
    ])
    query_is_option_source = method == "GET" and _read_is_option_source(req)
    query_is_business_query = method == "GET" and _request_has_business_query_evidence(req)
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
        flat_fields = _params_from_get_query(
            req, grounded_samples, page_enum_options, field_evidence, required_labels,
        )
        # select/选人:在 query 参数名上做下拉检测,与 POST body 同套算法
        # Query parameters on an option-source request configure that source;
        # they are not themselves options selected from its own response.  In
        # particular ``simple-list?status=0`` must remain an internal filter,
        # must not become the chooser nor inherit an unrelated page enum.
        selects_raw = (
            [] if query_is_option_source
            else _detect_query_selects(req, grounded_samples, option_reads, page_enum_options, field_evidence)
        )
    else:
        # 列表多选先识别
        list_selects = suggest_list_selects(pd, option_reads, grounded_samples)
        list_paths = [s["path"] for s in list_selects]

        # 字段拍平
        flat_fields = flatten_body(
            pd, samples, required_labels, collapse_paths=list_paths,
            field_evidence=field_evidence,
        )

        # HTTP writes may carry a JSON/Form body and independent Query
        # parameters at the same time. Preserve both contracts.
        query_fields = _params_from_get_query(
            req, grounded_samples, page_enum_options, field_evidence, required_labels,
        )
        flat_fields.extend(query_fields)

        # select/选人
        selects_raw = suggest_selects(pd, option_reads, grounded_samples, skip_paths=list_paths, fields=flat_fields) + list_selects
        apply_page_enum_options(selects_raw, page_enum_options, post_data=pd, fields=flat_fields)
        selects_raw += page_enum_selects(pd, page_enum_options, {s.get("path", "") for s in selects_raw}, fields=flat_fields)
        # Query-string enums on GET filters stay on the list request. A later
        # write that happens to carry ``?status=`` is a command discriminator,
        # not that list dropdown.
        if method == "GET":
            selects_raw += _detect_query_selects(
                req, grounded_samples, option_reads, page_enum_options, field_evidence,
            )

        # identity(运行期重取)
        iden_raw = suggest_identity(pd, storage_state, samples)

    flat_fields.extend(_params_from_url_path(req, grounded_samples))
    _attach_select_field_projections(selects_raw, flat_fields, option_reads)
    composite_selects = _detect_composite_entity_selects(
        flat_fields,
        option_reads,
        existing_paths={str(item.get("path") or "") for item in selects_raw},
    )
    if composite_selects:
        selects_raw.extend(composite_selects)
        _attach_select_field_projections(selects_raw, flat_fields, option_reads)

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
            field_projections=dict(s.get("field_projections") or {}),
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
            if _is_system_timestamp(key, raw) and _timestamp_is_near_request(raw, req):
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
        wire_type = f.get("wire_type") or _infer_type_from_value(f.get("value")) or f.get("type") or "string"
        ptype = f.get("type") or wire_type
        if path in list_paths:
            ptype = "list-enum"
        select_meta = select_by_path.get(path)
        if path in select_paths:
            ptype = "list-enum" if select_meta is not None and select_meta.multi else "enum"

        # Wire key stays the invocation contract; the page label is display-only.
        wire_key = str(f.get("key") or "").strip()
        if not wire_key or wire_key == path:
            wire_key = re.sub(r"\[\d+\]$", "", str(path or "").rsplit(".", 1)[-1])
        business_label = str(f.get("suggest_name") or "").strip()
        nm = wire_key
        display_label = business_label or wire_key
        if _looks_pagination_field(str(f.get("key") or ""), path):
            # Pagination names are part of the public invocation contract. Keep
            # their stable wire-facing key while retaining the localized DOM
            # label separately for UI presentation.
            ns = "auto"
        elif path in sel_names:
            display_label = sel_names[path] or display_label
            ns = "sample"
        elif path in assignee_names and (not display_label or display_label == wire_key or _looks_internal(display_label)):
            display_label = assignee_names[path] or display_label
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
            query_is_option_source=query_is_option_source,
            query_is_business_query=query_is_business_query,
        )
        missing_wire_placeholder = _is_missing_wire_placeholder(f.get("value"))
        if missing_wire_placeholder:
            # Values such as ``undefined`` are evidence that the page failed to
            # supply a value, not reusable constants or caller defaults.  Keep
            # the wire field executable by exposing it as a required input.
            source_guess = {
                "category": "user_param",
                "source_kind": "user_input",
                "source": {
                    "kind": "missing_recorded_value",
                    "path": path,
                    "required_state": "required",
                },
                "editable": True,
                "exposed_to_user": True,
                "reason": "录制请求中的值为空占位符，调用时必须由调用方提供真实值",
                "need_human_confirm": False,
            }
        recorded_option_control = bool(
            ptype in _ENUM_PARAM_TYPES
            and str(f.get("control_kind") or "").lower()
            in _SCREENSHOT_OPTION_CONTROL_KINDS
            and select_meta is None
        )
        if recorded_option_control:
            source_guess = {
                **source_guess,
                "category": "user_param",
                "source_kind": "form_option",
                "source": {"kind": "form_option", "path": path, "enum_confirmed": False},
                "exposed_to_user": True,
                "editable": True,
                "need_human_confirm": True,
                "reason": "录制页面确认该字段为选择控件；候选值尚未完整展开",
            }
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
        if f.get("field_aliases") or str(f.get("control_kind") or "unknown") != "unknown":
            evidence.append({
                "kind": "page_control",
                "source": "recorder_dom",
                "field_aliases": list(f.get("field_aliases") or []),
                "control_kind": str(f.get("control_kind") or "unknown"),
                "interacted": bool(f.get("recorded_user_input")),
                "disabled": bool(f.get("control_disabled")),
                "read_only": bool(f.get("control_read_only")),
                "editable": not bool(f.get("control_disabled")) if str(
                    f.get("control_kind") or ""
                ).lower() in {"select", "combobox"} else not bool(
                    f.get("control_disabled") or f.get("control_read_only")
                ),
                "request_path": path,
                "required": (
                    True if str(f.get("required_state") or "") == "required" or f.get("required") is True
                    else False
                ),
                "binding_status": "bound",
                "surface": str(f.get("surface") or ""),
                "in_dialog": bool(f.get("in_dialog")),
                "action_id": str(f.get("action_id") or ""),
                **dict(f.get("constraints") or {}),
            })
        if (str(f.get("required_state") or "") == "required" or f.get("required") is True) and f.get("required_state_grounded"):
            # Persist the page marker as evidence instead of only persisting the
            # resulting boolean. This lets later re-analysis distinguish an
            # actually-required search control from a filter that merely had a
            # value in the recorded URL.
            evidence.append({
                "kind": "page_required",
                "source": "recorder_dom",
                "request_path": path,
                "binding_status": "bound",
            })
        if enum_description and source_guess["source_kind"] in _OPTION_SOURCE_KINDS:
            evidence.append({
                "kind": "enum_options",
                "source_kind": source_guess["source_kind"],
                "option_count": len(enum_options or []),
                "options": enum_options or [],
                "option_map": enum_value_map or {},
            })

        caller_owned = bool(
            source_guess["category"] == "user_param"
            and source_guess["exposed_to_user"]
            and source_guess["source_kind"] not in {
                "previous_response", "current_user", "storage", "cookie",
                "page_context", "request_header", "system_time",
                "system_generated", "computed", "constant", "loop_item",
            }
        )
        params.append(ParamField(
            path=path,
            key=nm,
            label=display_label,
            value="" if missing_wire_placeholder else _recorded_param_sample(f.get("value")),
            type=ptype,
            wire_type=wire_type,
            required=(
                (
                    missing_wire_placeholder
                    or str(f.get("required_state") or "unknown") == "required"
                    or bool(source_guess.get("required"))
                )
                and caller_owned
                and not _looks_pagination_field(nm, path)
            ),
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
                    "required_state": (
                        "required" if missing_wire_placeholder or bool(source_guess.get("required"))
                        else "optional" if _looks_pagination_field(nm, path)
                        else str(f.get("required_state") or "unknown")
                    ),
                } if missing_wire_placeholder or bool(source_guess.get("required")) or f.get("required_state_grounded") or (
                    f.get("control_evidence_available")
                    and source_guess["category"] == "user_param"
                    and source_guess["exposed_to_user"]
                ) else {}),
                **({
                    "enum_source": select_meta.enum_source,
                    "enum_confirmed": select_meta.enum_confirmed,
                } if select_meta is not None else {}),
            },
            editable=bool(source_guess["editable"]),
            exposed_to_user=bool(source_guess["exposed_to_user"]),
            # A submitted request value is a replay sample, not a reusable
            # caller default. Defaults require explicit page/control evidence.
            default_value=(
                None
                if missing_wire_placeholder
                else f.get("visible_default")
                if f.get("visible_default") is not None
                else f.get("raw_value", f.get("value"))
                if (
                    _looks_pagination_field(nm, path)
                    or source_guess["source_kind"] in {"constant", "page_default"}
                )
                else None
            ),
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

    # Fields carried by the selected option object are runtime projections, not
    # additional caller inputs. This covers project -> quota/team/type/approver.
    for binding in selects_meta:
        for target_path, response_path in (binding.field_projections or {}).items():
            target = next((param for param in params if param.path == target_path), None)
            if target is None or target.locked:
                continue
            if target.source_kind in {"user_input", "page_default"}:
                continue
            if _param_has_editable_control_evidence(target):
                continue
            target.category = "runtime_var"
            target.source_kind = "selected_option_field"
            target.source = {
                "kind": "selected_option_field",
                "selector_path": binding.path,
                "selector_param": binding.param,
                "source_url": binding.source_url,
                "response_path": response_path,
                "target_path": target_path,
            }
            target.exposed_to_user = False
            target.editable = False
            target.required = False
            target.need_human_confirm = False
            target.evidence.append({
                "kind": "selected_option_projection",
                "selector_path": binding.path,
                "source_url": binding.source_url,
                "response_path": response_path,
                "target_path": target_path,
            })
            target.reason = f"该字段来自选择项接口中已选记录的 `{response_path}`，运行期随选择自动写入"

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
        "page_id": req.get("page_id"),
        "frame_id": req.get("frame_id"),
        "role": request_role.get("role", ""),
        "keep": request_role.get("keep"),
        "keep_reason": request_role.get("keep_reason") or request_role.get("reason", ""),
        "filter_reason": request_role.get("filter_reason", ""),
        "confidence": request_role.get("confidence"),
        "evidence": request_role.get("evidence"),
        **{
            key: req.get(key)
            for key in _REQUEST_OBSERVER_KEYS
            if req.get(key) not in (None, "")
        },
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








def _request_url_with_query(req: dict) -> str:
    url = str(req.get("url") or req.get("path") or "")
    if "?" in url or not (query := _request_query_values(req)):
        return url
    return f"{url}?{urlencode(query, doseq=True)}"




def _params_from_url_path(req: dict, samples: dict | None = None) -> list[dict]:
    """Ground path parameters only when a URL segment matches one unique user sample."""
    parsed = urlparse(str(req.get("url") or req.get("path") or ""))
    segments = parsed.path.split("/")
    nonempty_positions = [index for index, segment in enumerate(segments) if segment]
    if not nonempty_positions:
        return []
    last_position = nonempty_positions[-1]
    sample_items = [
        (str(label), value)
        for label, value in (samples or {}).items()
        if value not in (None, "") and not _looks_pagination_field(str(label), str(label))
    ]
    out: list[dict] = []
    used_labels: set[str] = set()
    for position in nonempty_positions:
        segment = unquote(segments[position])
        matches = [
            (label, value) for label, value in sample_items
            if label not in used_labels and str(value) == segment
        ]
        if len(matches) != 1 or (position != last_position and len(segment) < 4):
            continue
        label, value = matches[0]
        used_labels.add(label)
        out.append({
            "path": f"path.{position}",
            "key": label,
            "suggest_name": label,
            "value": value,
            "raw_value": value,
            "type": _infer_type_from_value(value),
            "wire_type": _infer_type_from_value(value),
            "required": True,
            "confidence": 0.96,
            "confidence_tier": "grounded",
            "name_source": "sample",
            "recorded_user_input": True,
            "field_aliases": [label],
            "control_kind": "unknown",
        })
    return out








def _page_enum_options_for_request(req: dict, options: dict | None) -> dict:
    return {
        str(key): item for key, item in (options or {}).items()
        if not isinstance(item, dict) or _recording_evidence_matches_request(req, item)
    }


# 一个 query 路径(如 query.status)上的下拉值若在 reads 候选列表里有命中,就被识别为 select
def _detect_query_selects(req: dict, samples: dict | None,
                          reads: list[dict], page_enum_options: dict | None,
                          field_evidence: list[dict] | None = None) -> list[dict]:
    """GET 请求的 query 参数本身也可能是某接口的下拉/枚举字段(典型如 /system/user/page?status=active)。
    把 query 视为扁平 key=值 结构,与 reads 候选做名→label 桥接、同上也试 DOM 选项。
    把命中路径重写为 `query.<key>` 以与 _params_from_get_query 的 path 对齐。通用,不挑系统。

    关键差异:接口型 select 既可能按 label 提交(显示名),也可能按 value 提交(状态码)。所以这里除
    suggest_selects 之外,还做一道 value-形态匹配置信信号 —— 当 query 值与 reads 候选的某
    「value/字典值字段」精准相等,即便没有 label 佐证,也以低置信度挂上 enum 标记,前端会把它
    当作低置信度 enum 项处理。"""
    flat = _params_from_get_query(req, samples, page_enum_options, field_evidence)
    if not flat:
        return []
    selectable_flat = [
        field for field in flat
        if not _looks_pagination_field(str(field.get("key") or ""), str(field.get("path") or ""))
    ]
    if not selectable_flat:
        return []
    syn_body: dict[str, Any] = {
        str(f.get("path") or "").split(".")[-1]: f.get("value")
        for f in selectable_flat if f.get("path")
    }
    synthetic_fields = [
        {**field, "path": str(field.get("path") or "").split(".")[-1]}
        for field in selectable_flat
    ]
    syn_pd = json.dumps(syn_body, ensure_ascii=False)

    # Query filters are especially prone to accidental value collisions: one
    # request commonly contains pageNo=1, billCode=1 and status=1, while an
    # unrelated option endpoint also contains ids 1/2/3.  A recorded form value
    # proves that the caller supplied the filter; it does *not* prove that a
    # candidate API owns that field.  Build DOM enums first (their control
    # name/id is structural evidence), then allow value-based API inference only
    # for fields for which no user input was recorded.
    page_selects = page_enum_selects(
        syn_pd,
        page_enum_options,
        set(),
        fields=synthetic_fields,
    )
    page_paths = {str(item.get("path") or "") for item in page_selects}

    api_fields = [
        field for field in synthetic_fields
        if not bool(field.get("recorded_user_input"))
        and str(field.get("path") or "") not in page_paths
    ]
    api_body = {
        str(field.get("path") or "").split(".")[-1]: field.get("value")
        for field in api_fields if field.get("path")
    }
    api_pd = json.dumps(api_body, ensure_ascii=False)
    api_selects = suggest_selects(
        api_pd, reads or [], samples, skip_paths=[], fields=api_fields,
    ) if api_fields else []
    # Explicit legacy ``reads`` may not carry DOM control metadata. Preserve
    # them only when the query leaf is an exact source token and its recorded
    # wire value resolves to one unambiguous ID/display row. The existing
    # selector then enforces a complete mapping and rejects competing sources.
    legacy_selects: list[dict] = []
    for field in api_fields:
        leaf = str(field.get("path") or "").split(".")[-1]
        wire_value = field.get("value")
        if not leaf or wire_value in (None, ""):
            continue
        inferred: list[dict] = []
        for read in reads or []:
            if str(read.get("role") or "") != "explicit_read_option":
                continue
            if leaf.casefold() not in _option_binding_tokens(read.get("url") or read.get("path") or ""):
                continue
            items = as_list_payload(read.get("json", read.get("response_json"))) or []
            matched_labels = {
                str(item.get(label_key) or "").strip()
                for item in items if isinstance(item, dict)
                for value_key, value in item.items()
                if _is_idlike(str(value_key)) and str(value) == str(wire_value)
                for label_key in [_pick_label_key(item, str(value_key))]
                if label_key != value_key and str(item.get(label_key) or "").strip()
            }
            if len(matched_labels) != 1:
                continue
            grounded_field = {
                **field,
                "control_kind": "select",
                "field_aliases": [leaf],
            }
            inferred.extend(suggest_selects(
                json.dumps({leaf: wire_value}, ensure_ascii=False),
                [read],
                {leaf: next(iter(matched_labels))},
                skip_paths=[],
                fields=[grounded_field],
            ))
        fingerprints = {
            (
                str(item.get("source_url") or ""),
                str(item.get("value_key") or ""),
                str(item.get("label_key") or ""),
                json.dumps(item.get("option_map") or {}, sort_keys=True, default=str),
            )
            for item in inferred
        }
        if len(fingerprints) == 1:
            legacy_selects.append(inferred[0])
    selects_raw = [*page_selects, *api_selects, *legacy_selects]

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








READ_CAPABILITY_KINDS = frozenset({
    "query", "query_status", "list_options", "validate", "validate_batch",
    "preview", "inspect", "export",
})
WRITE_CAPABILITY_KINDS = frozenset({
    "create", "update", "save_draft", "submit", "submit_batch",
    "approve", "reject", "withdraw", "delete",
})
ALLOWED_CAPABILITY_KINDS = READ_CAPABILITY_KINDS | WRITE_CAPABILITY_KINDS










































































































def _mark_request_materialized(
    spec: FlowSpec,
    entry: dict[str, Any],
    *,
    materialized_step_id: str = "",
) -> None:
    request_id = _request_fact_key(entry)
    usage = spec.request_facts.usage.get(request_id) or RequestUsage(request_id=request_id)
    usage.state = "materialized" if materialized_step_id else usage.state or "captured"
    if materialized_step_id:
        usage.materialized_step_id = materialized_step_id
    spec.request_facts.usage[request_id] = usage

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
    for sid in _capability_scoped_node_step_ids(cap.nodes or []):
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
    *,
    extra: dict[str, Any] | None = None,
) -> CapabilityRequestRef:
    fact = _step_request_fact_for_capability(spec, step)
    rid = fact.request_id if fact else str((step.source_meta or {}).get("request_id") or "")
    analysis = spec.request_facts.analysis.get(rid) if rid else None
    derived_usage = (
        "preflight"
        if bool((step.source_meta or {}).get("control_preflight_for_write"))
        else "execute"
    )
    usage = (
        existing.usage
        if existing and existing.origin in {"manual", "user", "compiler"}
        else derived_usage
    )
    role = (analysis.role if analysis else "") or (step.source_meta or {}).get("role") or step.semantic_role or ""
    if role == "submit_anchor":
        role = "business_write"
    elif derived_usage == "preflight" and role in {"", "business_get"}:
        role = "read_context"
    ref = CapabilityRequestRef(
        request_id=rid,
        request_index=fact.request_index if fact else (step.source_meta or {}).get("request_index"),
        step_id=step.step_id,
        role=role,
        method=(step.method or "").upper(),
        path=step.path or step.url,
        sequence=fact.sequence if fact else (step.source_meta or {}).get("sequence", (step.source_meta or {}).get("request_index")),
        confidence=float((analysis.confidence if analysis else None) or (step.source_meta or {}).get("confidence") or 0.0),
        reason=(analysis.reason if analysis else "") or (step.source_meta or {}).get("keep_reason") or "",
        usage=usage,
        origin=existing.origin if existing else str((step.source_meta or {}).get("membership_origin") or "planner"),
        confirmed=bool(existing.confirmed) if existing else False,
    )
    merged_extra: dict[str, Any] = {}
    if existing and existing.__pydantic_extra__:
        merged_extra.update(existing.__pydantic_extra__ or {})
    if extra:
        merged_extra.update(extra)
    if merged_extra:
        for key, value in merged_extra.items():
            if key in {
                "request_id",
                "request_index",
                "step_id",
                "role",
                "method",
                "path",
                "sequence",
                "confidence",
                "reason",
                "usage",
                "origin",
                "confirmed",
            }:
                continue
            ref.__pydantic_extra__[key] = value
    return ref

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
        wire_format=param.wire_format or _infer_wire_format(param.value),
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
        field_type = (
            str(
                schema.get("type")
                or ("unknown" if schema.get("x-dano-untyped-response") is True else "")
            )
            if isinstance(schema, dict) else ""
        )
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
    # ``properties`` is a JSON object, so insertion order is not part of the
    # contract. Derive the mirrored output list in a canonical order; an
    # equivalent JSON/database round-trip must not change the release hash.
    for name in sorted(props):
        schema = props[name]
        schema = schema if isinstance(schema, dict) else {}
        fields.append(CapabilityField(
            field_id=f"output:{cap.name or cap.capability_id}:{name}",
            scope="output",
            display_name=str(schema.get("title") or name),
            path=str(name),
            key=str(name),
            type=str(
                schema.get("type")
                or ("unknown" if schema.get("x-dano-untyped-response") is True else "string")
            ),
            required=name in required,
            exposed_to_caller=True,
            confidence=float(cap.confidence or 0.0),
            confirmed=bool(cap.confirmed),
        ))
    return fields
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


def _capability_inputs_from_top_level_schema(
    schema: dict[str, Any],
    existing: list[CapabilityField] | None = None,
) -> list[CapabilityField]:
    """Materialize aggregate capability inputs without leaking nested row fields.

    Batch request fields live under ``entries[].*``.  Mirroring those same
    ParamFields as top-level caller inputs makes the release validator demand
    both ``entries`` and every row field, producing duplicated errors after an
    otherwise unrelated type edit.
    """
    properties = dict((schema or {}).get("properties") or {})
    required = {str(name) for name in ((schema or {}).get("required") or [])}
    old_by_name = {
        str(item.key or item.path or item.display_name): item
        for item in (existing or [])
        if not item.step_id
    }
    # JSONB preserves arrays but not object-key order. Keep the explicit input
    # array authoritative so a database round trip cannot change the release.
    names = list(dict.fromkeys([
        *(
            str(item.key or item.path or item.display_name)
            for item in (existing or [])
            if str(item.key or item.path or item.display_name) in properties
        ),
        *properties,
    ]))
    out: list[CapabilityField] = []
    for name in names:
        raw = properties[name]
        field_schema = raw if isinstance(raw, dict) else {}
        previous = old_by_name.get(str(name))
        field = previous.model_copy(deep=True) if previous is not None else CapabilityField(
            field_id=f"input:{name}",
            scope="input",
            key=str(name),
            path=str(name),
            display_name=str(name),
            source_kind="user_input",
            category="user_param",
            exposed_to_caller=True,
        )
        field.scope = "input"
        field.key = str(name)
        field.path = str(name)
        field.display_name = field.display_name or str(name)
        field.type = str(field_schema.get("type") or field.type or "string")
        field.required = str(name) in required
        field.step_id = ""
        field.exposed_to_caller = True
        out.append(field)
    return out


def sync_capability_scoped_views(spec: FlowSpec) -> FlowSpec:
    """从旧 steps/links/step_ids 派生能力内字段/依赖视图。"""
    if not spec.capabilities:
        return spec
    by_step = {s.step_id: s for s in spec.steps}
    used_by_request: dict[str, list[str]] = {}
    materialized_by_request: dict[str, str] = {}
    memberships_by_request: dict[str, list[dict[str, Any]]] = {}
    for cap in spec.capabilities:
        previous_step_ids = _capability_scoped_step_ids(cap)
        cap_step_ids = [
            sid for sid in previous_step_ids
            if sid in by_step and _capability_step_allowed(spec, cap, by_step[sid])
        ]
        # ``nodes`` are the executable plan.  Filtering only ``step_ids`` left
        # stale call nodes executable and validation still treated their fields
        # as capability inputs.  Remove every generated call rejected by the
        # scoped membership policy from the node tree as well.
        for removed_step_id in set(previous_step_ids) - set(cap_step_ids):
            cap.nodes = _remove_capability_step_nodes(cap.nodes or [], removed_step_id)
        _sync_capability_order(spec, cap)
        cap_step_ids = list(cap.step_ids)
        step_objs = [by_step[sid] for sid in cap_step_ids]
        # ``_sync_capability_order`` has already rebuilt these memberships and
        # resolved the capability-local public anchor. Rebuilding once more
        # from the stale pre-sync refs would downgrade a shared query anchor
        # back to the step-global ``control_preflight_for_write`` usage.
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
                    "confirmed": ref.confirmed,
                })
        inputs: dict[str, CapabilityField] = {}
        request_fields: list[CapabilityField] = []
        internal_fields: list[CapabilityField] = []
        capability_computed_fields = [
            item.model_copy(deep=True)
            for item in (cap.computed_fields or [])
            if not item.step_id
        ]
        previous_inputs = list(cap.inputs or [])
        old_dependencies = list(cap.dependencies or [])
        request_id_by_step = {ref.step_id: ref.request_id for ref in cap.request_refs}
        for st in step_objs:
            request_id = request_id_by_step.get(st.step_id, "")
            for param in st.params:
                request_fields.append(_capability_field_from_param(st, param, scope="request_field", request_id=request_id))
                if _param_exposed_to_caller(param, set(cap_step_ids)):
                    key = param.key or param.label or param.path
                    inputs.setdefault(key, _capability_field_from_param(st, param, scope="input", request_id=request_id))
                else:
                    internal_fields.append(_capability_field_from_param(st, param, scope="internal", request_id=request_id))
        # steps/params 是请求字段的唯一真相；能力自身的聚合输入（例如批量 entries）
        # 可以独立存在。任何绑定到 step_id 的能力字段都是派生镜像，不能回写或
        # 覆盖 ParamField，即使旧镜像曾被 locked/confirmed。
        if _capability_is_batch(spec, cap):
            cap.inputs = _capability_inputs_from_top_level_schema(
                cap.input_schema, previous_inputs,
            )
            nested_item_names = set(
                (((cap.input_schema or {}).get("properties") or {}).get("entries") or {}).get("items", {}).get("properties", {})
            )
            for field in request_fields:
                if field.step_id and field.key in nested_item_names:
                    field.exposed_to_caller = False
        else:
            cap.inputs = list(inputs.values())
            existing_names = {field.key or field.path for field in cap.inputs}
            for field in _capability_inputs_from_top_level_schema(
                cap.input_schema, previous_inputs,
            ):
                raw_schema = ((cap.input_schema or {}).get("properties") or {}).get(field.key or field.path)
                if (
                    isinstance(raw_schema, dict)
                    and raw_schema.get("x-dano-capability-owned") is True
                    and (field.key or field.path) not in existing_names
                ):
                    cap.inputs.append(field)
        cap.request_fields = request_fields
        cap.internal_fields = internal_fields
        cap.computed_fields = capability_computed_fields
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
        cap.outputs = derived_outputs
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
    manually_assigned_steps = {
        ref.step_id
        for cap in (spec.capabilities or [])
        for ref in (cap.request_refs or [])
        if ref.step_id and ref.origin in {"manual", "user"}
    }
    fact_rows = [
        fact.model_dump(exclude_none=True)
        for fact in (spec.request_facts.requests or [])
    ]
    for step in spec.steps:
        if (step.method or "GET").upper() not in {"GET", "HEAD"} or step.step_id in manually_assigned_steps:
            continue
        if any(
            _param_has_manual_contract(param)
            for param in (step.params or [])
            if str(param.path or "").startswith("query.")
        ):
            continue
        current_query = (step.source_meta or {}).get("query")
        current = {
            "method": step.method,
            "url": step.url or step.path,
            "index": (step.source_meta or {}).get("request_index"),
        }
        # An explicitly empty derived query must not mask the real query string
        # already present in the materialized URL. Doing so made this pass
        # rebuild the same request as a "richer" candidate and discard all DOM
        # names, required evidence and numeric constraints.
        if isinstance(current_query, dict) and current_query:
            current["query"] = dict(current_query)
        current_path = _request_path(current)
        candidates: list[tuple[RequestFact, RequestAnalysis | None, dict[str, Any], str]] = []
        for fact, raw in zip(spec.request_facts.requests or [], fact_rows):
            if (fact.method or "GET").upper() != (step.method or "GET").upper():
                continue
            if _request_path(raw) != current_path:
                continue
            analysis = spec.request_facts.analysis.get(fact.request_id or "")
            role = str(analysis.role if analysis is not None else raw.get("role") or "")
            if role not in {"business_get", "read_context"}:
                # Re-evaluate recordings made before business searches were
                # distinguished from option lists. The raw request fact stays
                # authoritative; only its derived role is refreshed.
                refreshed = classify_network_request(raw, trace=fact_rows)
                if refreshed.get("role") != "business_get":
                    continue
                role = "business_get"
            candidates.append((fact, analysis, raw, role))
        if not candidates:
            continue
        fact, analysis, best, best_role = max(
            candidates, key=lambda item: _preread_candidate_score(item[2]),
        )
        if _business_filter_count(best) <= _business_filter_count(current):
            continue
        step.url = _request_url_with_query(best)
        step.path = _path_from_url(step.url)
        step.response_json = fact.response_json
        if fact.headers:
            step.headers = extract_auth_headers(fact.headers)
        old_query_params = [
            param for param in (step.params or [])
            if str(param.path or "").startswith("query.")
        ]
        non_query_params = [
            param for param in (step.params or [])
            if not str(param.path or "").startswith("query.")
        ]
        grounded_request = {
            **best,
            "request_id": fact.request_id,
            "request_index": fact.request_index,
            "response_json": fact.response_json,
        }
        grounded_role = {
            "role": best_role,
            "keep": True,
            "reason": analysis.reason if analysis is not None else "",
            "confidence": analysis.confidence if analysis is not None else 0.0,
            "evidence": analysis.evidence if analysis is not None else {},
        }
        rebuilt = _build_step_from_capture(
            _attach_request_role(grounded_request, grounded_role),
            reads=[],
            samples={},
            storage_state=None,
            required_labels=set(),
            page_enum_options=_page_enum_options_from_request_facts(spec.request_facts),
            step_index=0,
            field_evidence=list(getattr(spec.request_facts, "field_evidence", []) or []),
        )
        rebuilt_query_params = [
            param for param in rebuilt.params
            if str(param.path or "").startswith("query.")
        ]
        step.params = [*non_query_params, *rebuilt_query_params]
        step.selects = [
            binding for binding in (step.selects or [])
            if not str(binding.path or binding.id_path or "").startswith("query.")
        ] + [
            binding for binding in rebuilt.selects
            if str(binding.path or binding.id_path or "").startswith("query.")
        ]
        for param in old_query_params:
            step.sample_inputs.pop(str(param.key or ""), None)
        step.sample_inputs.update({
            param.key: param.value for param in rebuilt_query_params
            if param.key and param.value not in (None, "")
        })
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
            "role": best_role or (step.source_meta or {}).get("role"),
            "confidence": analysis.confidence if analysis else (step.source_meta or {}).get("confidence"),
            "query_fact_upgraded": True,
        }


def _response_shape_evidence_score(value: Any, *, depth: int = 0) -> int:
    """Score observed response structure, not business values.

    Repeated calls to one list endpoint often capture an empty initial page and
    a populated page after the operator searches.  Both are real facts, but the
    populated response is the only one that can describe ``records.items``.
    """
    if depth > 8:
        return 0
    if isinstance(value, dict):
        return len(value) + sum(
            _response_shape_evidence_score(item, depth=depth + 1)
            for item in value.values()
        )
    if isinstance(value, list):
        if not value:
            return 0
        samples = value[:3]
        return 5 + max(_response_shape_evidence_score(item, depth=depth + 1) for item in samples)
    return 1 if value is not None else 0


def _response_list_paths(value: Any, *, path: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(value, list):
        paths.add(path or "$.")
        for item in value[:3]:
            paths.update(_response_list_paths(item, path=f"{path}[]"))
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            paths.update(_response_list_paths(item, path=child))
    return paths


def _enrich_materialized_response_shapes(spec: FlowSpec) -> None:
    """Use a richer list response from the same observed endpoint for schema.

    Repeated list queries may first return an empty collection and later expose
    its item shape. Object responses are request-specific business facts and
    must never be replaced by another action merely because the route matches.
    """
    for step in spec.steps:
        method = (step.method or "GET").upper()
        if method not in {"GET", "HEAD"}:
            continue
        path = _request_path({"url": step.path or step.url})
        current_score = _response_shape_evidence_score(step.response_json)
        current_list_paths = _response_list_paths(step.response_json)
        if not current_list_paths:
            continue
        candidates = [
            fact for fact in (spec.request_facts.requests or [])
            if (fact.method or "GET").upper() == method
            and _request_path({"url": fact.path or fact.url}) == path
            and fact.response_json is not None
            and current_list_paths.intersection(
                _response_list_paths(fact.response_json)
            )
        ]
        if not candidates:
            continue
        richest = max(candidates, key=lambda fact: _response_shape_evidence_score(fact.response_json))
        richest_score = _response_shape_evidence_score(richest.response_json)
        if richest_score <= current_score:
            continue
        step.response_json = copy.deepcopy(richest.response_json)
        step.source_meta = {
            **(step.source_meta or {}),
            "response_shape_request_id": richest.request_id,
            "response_shape_enriched": True,
        }


def _infer_wire_format(value: Any) -> str:
    """Infer the on-wire value format from a recorded sample (deterministic)."""
    if isinstance(value, bool) or value in (None, ""):
        return ""
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return ""
        if 10**12 <= number < 4 * 10**12:
            return "epoch_ms"
        if 10**9 <= number < 4 * 10**9:
            return "epoch_s"
        return ""
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", value):
            return "datetime_text"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return "date_text"
    return ""


def _param_contract_richness(param: ParamField) -> tuple[int, int, int, int, float]:
    options = list(param.enum_options or [])
    executable_options = sum(
        1 for option in options
        if isinstance(option, dict)
        and option.get("label") not in (None, "")
        and option.get("value") not in (None, "")
    )
    source_rank = {
        "api_option": 5,
        "page_enum": 4,
        "static_enum": 4,
        "manual_enum": 4,
        "form_option": 2,
        "user_input": 1,
        "constant": 1,
    }.get(str(param.source_kind or ""), 0)
    return (
        executable_options,
        len(param.enum_value_map or {}),
        source_rank,
        len(param.evidence or []),
        float(param.confidence or 0.0),
    )


def _step_contract_richness(step: FlowStep) -> tuple[int, int, int, int]:
    param_scores = [_param_contract_richness(param) for param in (step.params or [])]
    return (
        sum(score[0] + score[1] for score in param_scores),
        sum(score[2] + score[3] for score in param_scores),
        _response_shape_evidence_score(step.response_json),
        len(step.selects or []),
    )


def _merge_duplicate_step_contract(target: FlowStep, source: FlowStep) -> None:
    by_path = {str(param.path or ""): param for param in target.params if param.path}
    for source_param in source.params or []:
        path = str(source_param.path or "")
        target_param = by_path.get(path)
        if target_param is None:
            copied = source_param.model_copy(deep=True)
            target.params.append(copied)
            if path:
                by_path[path] = copied
            continue
        if _param_contract_richness(source_param) > _param_contract_richness(target_param):
            index = target.params.index(target_param)
            copied = source_param.model_copy(deep=True)
            _merge_enum_values(copied, target_param)
            target.params[index] = copied
            by_path[path] = copied
        else:
            _merge_enum_values(target_param, source_param)

    existing_selects = {
        json.dumps(binding.model_dump(exclude_none=True), ensure_ascii=False, sort_keys=True, default=str)
        for binding in (target.selects or [])
    }
    for binding in source.selects or []:
        marker = json.dumps(
            binding.model_dump(exclude_none=True), ensure_ascii=False, sort_keys=True, default=str,
        )
        if marker not in existing_selects:
            target.selects.append(binding.model_copy(deep=True))
            existing_selects.add(marker)

    if _response_shape_evidence_score(source.response_json) > _response_shape_evidence_score(target.response_json):
        target.response_json = copy.deepcopy(source.response_json)
    if not target.body_template and source.body_template:
        target.body_template = copy.deepcopy(source.body_template)
    if not target.body_source and source.body_source:
        target.body_source = source.body_source
    if not target.headers and source.headers:
        target.headers = dict(source.headers)
    target.sample_inputs = {**dict(source.sample_inputs or {}), **dict(target.sample_inputs or {})}


def _retarget_step_references(spec: FlowSpec, replacements: dict[str, str]) -> None:
    if not replacements:
        return

    def replace(value: Any) -> Any:
        return replacements.get(str(value or ""), value)

    def retarget_nodes(nodes: list[dict[str, Any]]) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            for key in ("step_id", "from", "source"):
                if key in node:
                    node[key] = replace(node.get(key))
            for child_key in ("children", "steps", "then", "else", "otherwise"):
                if isinstance(node.get(child_key), list):
                    retarget_nodes(node[child_key])

    for link in spec.links or []:
        source_step_id = replace(link.source_step_id)
        target_step_id = replace(link.target_step_id)
        if source_step_id != link.source_step_id or target_step_id != link.target_step_id:
            from dano.execution.page.recording_live import invalidate_dependency_verification

            invalidate_dependency_verification(link, "依赖步骤已重定向，需要重新验证")
        link.source_step_id = source_step_id
        link.target_step_id = target_step_id
    for item in spec.review_items or []:
        item.target = {
            key: replace(value) if key in {"step_id", "source_step_id", "target_step_id"} else value
            for key, value in (item.target or {}).items()
        }
    for capability in spec.capabilities or []:
        retarget_nodes(capability.nodes or [])
        capability.step_ids = list(dict.fromkeys(replace(step_id) for step_id in capability.step_ids or []))
        for ref in capability.request_refs or []:
            ref.step_id = replace(ref.step_id)
        for field_name in (
            "inputs", "request_fields", "internal_fields", "computed_fields", "outputs",
        ):
            for field in getattr(capability, field_name) or []:
                field.step_id = replace(field.step_id)
        for dependency in capability.dependencies or []:
            if "step_id" in (dependency.source or {}):
                dependency.source["step_id"] = replace(dependency.source.get("step_id"))
            if "step_id" in (dependency.target or {}):
                dependency.target["step_id"] = replace(dependency.target.get("step_id"))
        for mapping in capability.output_mapping or []:
            if isinstance(mapping, dict):
                for key in ("step_id", "from", "source"):
                    if key in mapping:
                        mapping[key] = replace(mapping.get(key))
        for evidence in capability.evidence or []:
            if isinstance(evidence, dict) and "anchor_step_id" in evidence:
                evidence["anchor_step_id"] = replace(evidence.get("anchor_step_id"))
    for usage in (spec.request_facts.usage or {}).values():
        usage.materialized_step_id = replace(usage.materialized_step_id)
        for membership in usage.capability_memberships or []:
            if isinstance(membership, dict) and "step_id" in membership:
                membership["step_id"] = replace(membership.get("step_id"))
    for evidence in getattr(spec.request_facts, "field_evidence", []) or []:
        if isinstance(evidence, dict) and "step_id" in evidence:
            evidence["step_id"] = replace(evidence.get("step_id"))

    capability_model = (spec.meta or {}).get("capability_model") or {}
    semantic_plan = capability_model.get("semantic_plan") if isinstance(capability_model, dict) else None
    if isinstance(semantic_plan, dict):
        for capability in semantic_plan.get("capabilities") or []:
            if not isinstance(capability, dict):
                continue
            if "anchor_step_id" in capability:
                capability["anchor_step_id"] = replace(capability.get("anchor_step_id"))
            for ref in capability.get("request_refs") or []:
                if isinstance(ref, dict) and "step_id" in ref:
                    ref["step_id"] = replace(ref.get("step_id"))


def _generated_capability_is_protected(capability: FlowCapability) -> bool:
    return bool(
        capability.locked
        or capability.updated_by == "user"
        or any(ref.origin in {"manual", "user"} for ref in capability.request_refs or [])
    )


def _collapse_duplicate_generated_capabilities(spec: FlowSpec) -> None:
    kept: list[FlowCapability] = []
    signature_index: dict[tuple[str, tuple[str, ...]], int] = {}
    for capability in spec.capabilities or []:
        signature = (
            _capability_kind_family(capability.kind),
            tuple(_capability_node_step_ids(capability)),
        )
        if not signature[1]:
            kept.append(capability)
            continue
        existing_index = signature_index.get(signature)
        if existing_index is None:
            signature_index[signature] = len(kept)
            kept.append(capability)
            continue
        existing = kept[existing_index]
        existing_protected = _generated_capability_is_protected(existing)
        incoming_protected = _generated_capability_is_protected(capability)
        if existing_protected and incoming_protected:
            kept.append(capability)
            continue
        if incoming_protected:
            kept[existing_index] = capability
            continue
        if existing_protected:
            continue
        if float(capability.confidence or 0.0) > float(existing.confidence or 0.0):
            kept[existing_index] = capability
    spec.capabilities = kept


def _canonicalize_materialized_request_identities(spec: FlowSpec) -> None:
    """One captured request identity may own only one materialized FlowStep."""
    grouped: dict[str, list[FlowStep]] = {}
    for step in spec.steps:
        meta = step.source_meta or {}
        request_id = str(meta.get("request_id") or "").strip()
        request_index = meta.get("request_index")
        identity = f"id:{request_id}" if request_id else (
            f"idx:{request_index}" if request_index is not None else ""
        )
        if identity:
            grouped.setdefault(identity, []).append(step)

    replacements: dict[str, str] = {}
    removed_ids: set[str] = set()
    for duplicates in grouped.values():
        if len(duplicates) < 2:
            continue
        canonical = max(duplicates, key=_step_contract_richness)
        for duplicate in duplicates:
            if duplicate is canonical:
                continue
            _merge_duplicate_step_contract(canonical, duplicate)
            replacements[duplicate.step_id] = canonical.step_id
            removed_ids.add(duplicate.step_id)
    if not removed_ids:
        return
    spec.steps = [step for step in spec.steps if step.step_id not in removed_ids]
    _retarget_step_references(spec, replacements)
    _collapse_duplicate_generated_capabilities(spec)
    spec.meta = {
        **(spec.meta or {}),
        "deduped_request_identity_count": (
            int((spec.meta or {}).get("deduped_request_identity_count") or 0) + len(removed_ids)
        ),
    }


def sync_flow_spec_models(spec: FlowSpec) -> FlowSpec:
    _canonicalize_materialized_request_identities(spec)
    _upgrade_materialized_query_facts(spec)
    # Upgrading an initial list request to the richer searched fact can make it
    # converge with a step that already owns that durable request identity.
    _canonicalize_materialized_request_identities(spec)
    _enrich_materialized_response_shapes(spec)
    _rebind_saved_field_evidence(spec)
    _repair_readonly_control_defaults(spec)
    _ground_saved_page_enums(spec)
    # FlowStep 已经是可编辑/可编排接口的物化事实；usage 不能等到能力绑定后才更新，
    # 否则初次分析会把已进入字段页的查询接口仍标成 captured。
    for step in spec.steps:
        for param in step.params:
            if not param.field_id:
                identity = f"{step.step_id}\0{param.path}".encode("utf-8")
                param.field_id = f"pf_{hashlib.sha256(identity).hexdigest()[:16]}"
            if not param.wire_format and param.type in {"date", "datetime", "time", "number", "string"}:
                param.wire_format = _infer_wire_format(
                    param.value if param.value not in (None, "") else param.default_value
                )
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


def _rebind_saved_field_evidence(spec: FlowSpec) -> None:
    """Re-evaluate unresolved DOM facts against the authoritative saved body.

    The client projection deliberately redacts request bodies, but the server
    draft still owns them on FlowStep. Re-analysis therefore must bind from
    server steps instead of freezing an old ``unbound`` result forever.
    """
    evidence = list(getattr(spec.request_facts, "field_evidence", []) or [])
    unresolved_indexes = [
        index
        for index, item in enumerate(evidence)
        if isinstance(item, dict)
        and (
            str(item.get("binding_status") or "") in {"unbound", "unresolved", "ambiguous"}
            # Value-only bindings are deliberately heuristic. Re-evaluate
            # them against the authoritative saved request bodies so improved
            # disambiguation can repair an older binding instead of freezing a
            # textarea value onto an unrelated paging/option request forever.
            or str(item.get("binding_method") or "").startswith("unique_value_")
        )
    ]
    if not unresolved_indexes:
        return
    from dano.execution.page.recording_field_identity import (
        bind_field_evidence,
        canonical_wire_path,
    )

    requests: list[dict[str, Any]] = []
    facts_by_id = {
        str(fact.request_id or ""): fact.model_dump(exclude_none=True)
        for fact in spec.request_facts.requests
        if str(fact.request_id or "")
    }
    for step in spec.steps:
        meta = dict(step.source_meta or {})
        request_id = str(meta.get("request_id") or "")
        if not request_id:
            continue
        analysis = spec.request_facts.analysis.get(request_id)
        fact = facts_by_id.get(request_id, {})
        method = str(step.method or fact.get("method") or "GET").upper()
        query = meta.get("query") or fact.get("query") or {}
        body = step.body_source
        # A client projection intentionally redacts request bodies.  Rebinding
        # against that empty projection would destroy previously captured
        # evidence, so only authoritative server-side request values may
        # participate in this repair pass.
        if method in {"GET", "HEAD", "OPTIONS"}:
            if not query:
                continue
        elif body in (None, "", {}, []):
            continue
        requests.append({
            **fact,
            **meta,
            "request_id": request_id,
            "method": method,
            "url": step.url or step.path,
            "post_data": body,
            "query": query,
            "role": analysis.role if analysis is not None else meta.get("role") or "",
        })
    if not requests:
        return
    unresolved = [evidence[index] for index in unresolved_indexes]
    rebound_unresolved = bind_field_evidence(
        requests,
        list(spec.request_facts.page_events or []),
        unresolved,
        page_enum_options=_page_enum_options_from_request_facts(spec.request_facts),
    )
    rebound = list(evidence)
    for index, item in zip(unresolved_indexes, rebound_unresolved, strict=True):
        rebound[index] = item
    spec.request_facts.field_evidence = rebound
    for step in spec.steps:
        request_id = str((step.source_meta or {}).get("request_id") or "")
        controls = [
            item for item in rebound
            if isinstance(item, dict)
            and item.get("binding_status") == "bound"
            and str(item.get("request_id") or "") == request_id
        ]
        for param in step.params:
            wire_path = canonical_wire_path(step, param.path)
            matches = [item for item in controls if str(item.get("wire_path") or "") == wire_path]
            if len(matches) != 1:
                continue
            control = matches[0]
            label = str(control.get("label") or control.get("field") or "").strip()
            if (
                label
                and not _param_field_manually_edited(param, "key")
                and not _param_field_manually_edited(param, "label")
                and not any(
                    isinstance(item, dict) and item.get("actor") == "agent" and item.get("kind") == "field_name"
                    for item in (param.evidence or [])
                )
            ):
                param.label = label
            control_kind = str(control.get("control_kind") or "").lower()
            if not _param_field_manually_edited(param, "type"):
                if control_kind == "date":
                    param.type = "date"
                elif control_kind in {"datetime", "time"}:
                    param.type = "datetime"
                elif control_kind == "number":
                    param.type = "number"
                elif control_kind == "textarea":
                    param.type = "string"
                elif control_kind == "text" and str(param.wire_type or "") != "number":
                    param.type = "string"
            if (
                param.source_kind in {"unknown", ""}
                and str(step.method or "").upper() in {"GET", "HEAD"}
                and str(param.path or "").startswith("query.")
                and control_kind not in {"", "unknown", "table_column"}
                and not bool(control.get("disabled") or control.get("read_only"))
                and not _param_has_manual_contract(param)
            ):
                param.category = "user_param"
                param.source_kind = "user_input"
                param.source = {
                    **(param.source or {}),
                    "kind": "control_default",
                    "path": param.path,
                    "required_state": str((param.source or {}).get("required_state") or "unknown"),
                }
                param.exposed_to_user = True
                param.editable = True
                param.need_human_confirm = False
                param.reason = "查询页上的可编辑筛选控件；调用方可省略或覆盖录制时的筛选值"
            if not any(
                isinstance(item, dict) and item.get("kind") == "page_control"
                for item in (param.evidence or [])
            ):
                param.evidence = [
                    *(param.evidence or []),
                    {
                        "kind": "page_control",
                        "source": "recorder_dom",
                        "control_kind": control_kind,
                        "interacted": str(control.get("op") or "").lower() in {"fill", "select", "pick"},
                        "request_path": param.path,
                        "binding_status": "bound",
                        "required": bool(control.get("required") or control.get("required_observed")),
                        "surface": str(control.get("surface") or ""),
                        "in_dialog": bool(control.get("in_dialog")),
                        "action_id": str(control.get("action_id") or ""),
                    },
                ]
            if isinstance(control.get("required_observed"), bool):
                param.evidence = [
                    item for item in (param.evidence or [])
                    if not (isinstance(item, dict) and item.get("kind") == "page_required")
                ]
                if control["required_observed"]:
                    param.evidence.append({
                        "kind": "page_required",
                        "source": "recorder_dom",
                        "request_path": param.path,
                        "binding_status": "bound",
                        "evidence_id": control.get("evidence_id") or "",
                    })
                if (
                    not _param_field_manually_edited(param, "required")
                    and not _param_required_agent_classified(param)
                ):
                    observed = bool(control["required_observed"])
                    param.required = bool(observed and _param_exposed_to_caller(param))
                    param.source = {
                        **(param.source or {}),
                        "required_state": "required" if observed else "optional",
                    }


def _ground_saved_page_enums(spec: FlowSpec) -> None:
    """Recover enum contracts from immutable DOM evidence.

    Older or partially inferred specs can retain RequestFacts.option_sources
    while missing the SelectBinding that projects those facts to a request
    field. Re-running optimize/sync must be able to repair that state without
    another recording. A binding is created only for a unique semantic match;
    a selected wire value is supporting evidence, never enough on its own.
    """
    page_options = _page_enum_options_from_request_facts(spec.request_facts)
    if not page_options:
        return

    def norm(value: Any) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).casefold()

    def wire_identity(value: Any) -> str:
        path = str(value or "").strip().removeprefix("request.")
        return path.removeprefix("body.").removeprefix("query.")

    def grounded_targets(raw: dict) -> list[dict[str, str]]:
        """Resolve dictionary evidence through request, control, and scope identity."""
        aliases = {
            norm(value) for value in (raw.get("field_aliases") or [])
            if norm(value)
        }
        if not aliases:
            return []
        field_name = norm(raw.get("field_key"))
        observations = [
            item for item in (raw.get("request_value_observations") or [])
            if isinstance(item, dict)
        ]
        option_pairs = [
            pair for option in (raw.get("options") or raw.get("values") or [])
            if (pair := _enum_label_value(option)) is not None
        ]
        direct: list[dict[str, str]] = []
        scoped: dict[str, list[dict[str, str]]] = {}
        for step in spec.steps:
            request_id = str((step.source_meta or {}).get("request_id") or "")
            scoped_evidence = [
                item for item in (getattr(spec.request_facts, "field_evidence", []) or [])
                if isinstance(item, dict)
                and field_name in {
                    norm(item.get("field")), norm(item.get("label")),
                }
                and _recording_evidence_matches_scope(step.source_meta or {}, item)
            ]
            matching_controls = [
                item for item in scoped_evidence
                if str(item.get("control_kind") or "").lower() == "select"
            ]
            for param in step.params or []:
                param_names = {
                    norm(param.key), norm(param.label), norm(param.path),
                    norm(wire_identity(param.path).split(".")[-1]),
                }
                if not aliases.intersection(param_names):
                    continue
                target = {
                    "step_id": step.step_id,
                    "request_id": request_id,
                    "wire_path": param.path,
                }
                if any(
                    request_id == str(item.get("request_id") or "")
                    and wire_identity(param.path) == wire_identity(item.get("wire_path"))
                    for item in observations
                ):
                    direct.append(target)
                    continue
                mapped_labels = {
                    str(label) for label, wire_value in option_pairs
                    if str(wire_value) == str(param.value)
                }
                visible_labels = {
                    str(value)
                    for item in scoped_evidence
                    for value in [
                        item.get("value"), item.get("selected"), item.get("selected_label"),
                        *(item.get("sample_values") or []),
                    ]
                    if value not in (None, "")
                }
                if matching_controls and mapped_labels.intersection(visible_labels):
                    scoped.setdefault(step.step_id, []).append(target)

        # An exact request observation owns its field directly. A route-scoped
        # control may reuse the same complete dictionary only when it identifies
        # exactly one matching request field on that step; ambiguity fails closed.
        targets = list(direct)
        targets.extend(items[0] for items in scoped.values() if len(items) == 1)
        return list({(item["step_id"], item["wire_path"]): item for item in targets}.values())

    expanded_page_options: dict[str, Any] = {}
    for raw_key, raw in page_options.items():
        targets = grounded_targets(raw) if isinstance(raw, dict) else []
        if not targets:
            expanded_page_options[str(raw_key)] = raw
            continue
        for index, target in enumerate(targets):
            expanded_page_options[f"{raw_key}@target:{index}"] = {
                **raw,
                "_grounded_target": target,
            }
    page_options = expanded_page_options

    seen: set[str] = set()
    for raw_key, raw in page_options.items():
        if isinstance(raw, dict):
            source_kind = str(raw.get("enum_source") or "dom").strip()
            mapping_complete = raw.get("mapping_complete") is True
            if (
                str(raw.get("control_kind") or "").lower() != "select"
                or source_kind not in {"dom", "script_static", "script_dictionary"}
                or (not mapping_complete and source_kind != "dom")
                or (source_kind == "script_static" and not raw.get("script_url"))
                or (
                    source_kind == "script_dictionary"
                    and (not raw.get("source_url") or not raw.get("dict_type"))
                )
            ):
                continue
            options = list(raw.get("options") or raw.get("values") or [])
            field_key = str(raw.get("field_key") or raw_key or "").strip()
            field_aliases = [
                str(value).strip() for value in (raw.get("field_aliases") or [])
                if str(value or "").strip()
            ]
            selected = str(raw.get("selected_label") or raw.get("selected") or "").strip()
            explicit_map = dict(raw.get("option_map") or raw.get("value_map") or {})
            grounded_target = raw.get("_grounded_target")
            strict_control_identity = True
        else:
            continue
        if not field_key or not options:
            continue
        option_pairs = [_enum_label_value(option) for option in options]
        if (
            any(pair is None for pair in option_pairs)
            or (mapping_complete and any(pair[1] is None for pair in option_pairs if pair))
            or len({str(pair[0]) for pair in option_pairs if pair}) != len(options)
        ):
            continue
        signature = json.dumps(
            {
                "field": field_key,
                "aliases": field_aliases,
                "selected": selected,
                "options": options,
                "grounded_target": grounded_target,
            },
            ensure_ascii=False, sort_keys=True, default=str,
        )
        if signature in seen:
            continue
        seen.add(signature)

        candidates: list[tuple[int, FlowStep, ParamField]] = []
        field_norm = norm(field_key)
        for step in spec.steps:
            for param in step.params or []:
                if isinstance(grounded_target, dict):
                    step_request_id = str((step.source_meta or {}).get("request_id") or "")
                    if not (
                        step.step_id == str(grounded_target.get("step_id") or "")
                        and step_request_id == str(grounded_target.get("request_id") or "")
                        and wire_identity(param.path) == wire_identity(grounded_target.get("wire_path"))
                    ):
                        continue
                names = [
                    param.key, param.label, param.path,
                    _strip_body_prefix(param.path or ""),
                    _strip_body_prefix(param.path or "").split(".")[-1],
                ]
                normalized_names = {norm(name) for name in names if str(name or "")}
                semantic_score = 0
                if field_aliases:
                    if any(norm(alias) in normalized_names for alias in field_aliases if norm(alias)):
                        semantic_score = 10
                elif strict_control_identity:
                    semantic_score = 0
                elif field_norm and field_norm in normalized_names:
                    semantic_score = 8
                if not semantic_score:
                    continue
                if selected and param.value not in (None, "") and str(param.value) == selected:
                    semantic_score += 2
                candidates.append((semantic_score, step, param))
        if not candidates:
            continue
        best_score = max(score for score, _step, _param in candidates)
        best = [(step, param) for score, step, param in candidates if score == best_score]
        if len(best) != 1:
            continue
        step, param = best[0]
        if any(
            isinstance(item, dict)
            and item.get("source") == "manual_edit"
            and item.get("field") in {
                "type", "category", "source_kind", "source", "enum_options", "enum_value_map",
            }
            for item in (param.evidence or [])
        ):
            # This recovery pass does not rebuild ``step.selects``.  Preserve
            # the operator-owned field contract and leave any existing binding
            # untouched; do not manufacture a new inferred enum here.
            continue

        existing_binding = next((
            item for item in (step.selects or [])
            if _strip_body_prefix(item.path or item.id_path or "") == _strip_body_prefix(param.path)
        ), None)
        if (
            existing_binding is not None
            and str(existing_binding.enum_source or "") == "api"
            and existing_binding.enum_confirmed is True
            and source_kind == "dom"
        ):
            # A recorded API label/value contract contains the actual wire
            # values. A later incomplete DOM snapshot is display evidence, not
            # authority to erase that stronger renewable source contract.
            continue
        option_map = dict(explicit_map)
        for option in options:
            # A bare string proves only a visible label, not that the backend
            # accepts the same string. Keep mappings only when the DOM exposed
            # an explicit value or when this recording proves selected→wire.
            if isinstance(option, dict) and "value" in option and option.get("value") is not None:
                label = option.get("label") if option.get("label") is not None else option.get("name")
                if label not in (None, ""):
                    option_map.setdefault(str(label), option.get("value"))
            elif isinstance(option, (list, tuple)) and len(option) >= 2 and option[1] is not None:
                option_map.setdefault(str(option[0]), option[1])
        if selected and param.value not in (None, ""):
            option_map.setdefault(selected, param.value)
        labels = [
            str(pair[0]) for option in options
            if (pair := _enum_label_value(option)) is not None
        ]
        confirmed = bool(
            mapping_complete
            and labels
            and all(label in option_map and option_map[label] is not None for label in labels)
        )

        binding = existing_binding
        if binding is None:
            binding = SelectBinding(path=param.path)
            step.selects.append(binding)
        compiled_call_key = any(
            isinstance(item, dict)
            and item.get("source") == "capability_compiler"
            and item.get("field") == "key"
            for item in (param.evidence or [])
        )
        binding.param = param.key if compiled_call_key else field_key
        binding.path = param.path
        binding.options = options
        binding.option_map = option_map or None
        if source_kind == "script_dictionary":
            binding.source_url = str(raw.get("source_url") or "")
            binding.source_method = "GET"
            binding.value_key = "value"
            binding.label_key = "label"
            binding.category_key = "dictType"
            binding.category_value = str(raw.get("dict_type") or "")
            binding.enum_source = "api"
            _hydrate_select_source_contract(spec, binding)
        elif source_kind == "script_static":
            binding.source_url = ""
            binding.value_key = ""
            binding.label_key = ""
            binding.category_key = None
            binding.category_value = None
            binding.enum_source = "script_static"
        else:
            binding.source_url = ""
            binding.value_key = ""
            binding.label_key = ""
            binding.category_key = None
            binding.category_value = None
            binding.enum_source = "dom"
        binding.enum_confirmed = confirmed

        # DOM label is stronger public naming evidence than an internal wire
        # identifier, but never overwrite an explicit/manual business label.
        path_leaf = _strip_body_prefix(param.path or "").split(".")[-1]
        if (
            not compiled_call_key
            and (param.key in {"", param.path, path_leaf} or looks_internal_param_name(param.key))
        ):
            if not any(other is not param and other.key == field_key for other in step.params):
                old_key = param.key
                param.key = field_key
                param.label = field_key
                if old_key in step.sample_inputs and field_key not in step.sample_inputs:
                    step.sample_inputs[field_key] = step.sample_inputs.pop(old_key)


def _param_has_manual_contract(param: ParamField) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("source") == "manual_edit"
        and item.get("field") in {
            "type", "category", "source_kind", "source", "enum_options", "enum_value_map",
        }
        for item in (param.evidence or [])
    )


def _param_field_manually_edited(param: ParamField, field: str) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("source") == "manual_edit"
        and (item.get("field") == field or item.get("axis") == field)
        for item in (param.evidence or [])
    )


def _param_axis_manually_edited(param: ParamField, *fields: str) -> bool:
    return any(_param_field_manually_edited(param, field) for field in fields)


def _param_source_agent_classified(param: ParamField) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("actor") == "agent"
        and item.get("kind") == "param_source"
        and item.get("source_kind") in {
            "caller_input", "constant", "session", "context", "response_binding", "computed",
            "generated",
        }
        for item in (param.evidence or [])
    )


def _param_required_agent_classified(param: ParamField) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("actor") == "agent"
        and item.get("kind") == "param_required"
        for item in (param.evidence or [])
    )


def _param_has_full_lock(param: ParamField) -> bool:
    return bool(param.locked)


def _param_has_page_required_evidence(param: ParamField) -> bool:
    """Return true only for a captured page-required marker.

    A populated query string, planner-required flag, field name, or sample value
    is not proof that a search filter is mandatory.
    """
    return any(
        isinstance(item, dict)
        and (
            (
                item.get("kind") == "page_required"
                and str(item.get("binding_status") or "") == "bound"
            )
            or (
                item.get("source") in {"recorder_dom", "page", "page_snapshot"}
                and item.get("required") is True
                and str(item.get("binding_status") or "") == "bound"
            )
        )
        for item in (param.evidence or [])
    )


def _param_has_option_control_evidence(param: ParamField) -> bool:
    return any(
        isinstance(item, dict)
        and (
            item.get("canonical_screenshot_control") is True
            or str(item.get("source") or item.get("kind") or "").lower()
            in {
                "recorder_dom", "page", "page_snapshot", "page_control",
                "screenshot", "reference_screenshot", "uploaded_screenshot",
            }
        )
        and str(item.get("control_kind") or "").lower()
        in _SCREENSHOT_OPTION_CONTROL_KINDS
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


def _param_has_interacted_temporal_control(param: ParamField) -> bool:
    """Preserve an explicitly operated date/time control over wire inference."""
    return any(
        isinstance(item, dict)
        and item.get("interacted") is True
        and str(item.get("control_kind") or "").lower() in {"date", "datetime", "time"}
        for item in (param.evidence or [])
    )


def _audit_step_param_contracts(step: FlowStep) -> None:
    """Conservatively repair only contradictory generated field contracts."""
    display_paths = {
        binding.path
        for binding in (step.selects or [])
        if binding.path and _select_has_executable_options(binding)
    }
    id_paths = {
        binding.id_path
        for binding in (step.selects or [])
        if binding.id_path and _select_has_executable_options(binding)
    }
    for param in step.params or []:
        if param.locked:
            continue
        normalized_path = param.path or ""
        if param.source_kind == "dynamic_structure":
            # Recorded dynamic-map leaves (for example BPMN Activity_* keys)
            # describe one observed process version.  They are execution
            # placeholders, never caller inputs or reusable enum bindings.
            param.category = "runtime_var"
            param.exposed_to_user = False
            param.editable = False
            param.required = False
            param.need_human_confirm = False
            if param.type in _ENUM_PARAM_TYPES:
                param.type = param.wire_type or _infer_type_from_value(param.value)
            param.enum_options = None
            param.enum_value_map = None
            continue
        required_state = str((param.source or {}).get("required_state") or "")
        if (
            not _param_field_manually_edited(param, "required")
            and _param_has_page_required_evidence(param)
            and _param_exposed_to_caller(param)
        ):
            # A bound DOM required marker is already machine-grounded evidence.
            # Skill may raise optional→required, but cannot erase a captured *.
            param.required = True
            param.source = {**(param.source or {}), "required_state": "required"}
        if (
            (step.method or "GET").upper() in {"GET", "HEAD"}
            and str(param.path or "").startswith("query.")
            and required_state != "required"
            and str((param.source or {}).get("kind") or "") != "selected_record_identity"
            and not _param_field_manually_edited(param, "required")
            and not _param_required_agent_classified(param)
        ):
            # Legacy recordings marked every populated query filter required.
            # Preserve mandatory status only when the recorder captured an
            # actual page-required marker.
            param.required = _param_has_page_required_evidence(param)
            if param.type not in {"array", "object"}:
                # HTTP query serialization is textual, including enum codes.
                # A numeric-looking sample or dictionary value must not make
                # one list filter advertise a different wire contract from
                # the same value serialized in the URL.
                param.wire_type = "string"
        if _looks_pagination_field(param.key, param.path):
            inferred_type = _infer_type_from_value(param.value)
            if (
                not _param_field_manually_edited(param, "type")
                and not _param_axis_manually_edited(
                    param, "category", "source_kind", "source", "exposed_to_user", "editable",
                )
            ):
                param.type = inferred_type
            param.wire_type = inferred_type
            if not _param_field_manually_edited(param, "required"):
                param.required = False
            if not _param_axis_manually_edited(
                param, "category", "source_kind", "source", "exposed_to_user", "editable",
            ):
                existing_source = dict(param.source or {})
                context_key = str(existing_source.get("context_key") or "")
                if not context_key:
                    context_key = str(param.key or param.path).split(".")[-1].split("[")[0]
                default_value = existing_source.get("default_value")
                if default_value in (None, ""):
                    default_value = param.default_value
                if default_value in (None, ""):
                    default_value = param.value
                param.category = "runtime_var"
                param.source_kind = "page_context"
                param.source = {
                    **existing_source,
                    "kind": "page_context",
                    "context_key": context_key,
                    "path": param.path,
                    "default_value": default_value,
                    "caller_override": False,
                    "required_state": "optional",
                }
                param.exposed_to_user = False
                param.editable = False
            if not _param_field_manually_edited(param, "need_human_confirm"):
                param.need_human_confirm = False
            if not _param_axis_manually_edited(param, "enum_options", "enum_value_map"):
                param.enum_options = None
                param.enum_value_map = None
            if not _param_field_manually_edited(param, "description"):
                param.description = _strip_option_descriptions(param.description) or None
            if not _param_field_manually_edited(param, "reason"):
                param.reason = "分页参数由运行上下文使用录制默认值自动注入，不作为业务筛选字段暴露给调用方"
            continue
        if param.source_kind == "api_option":
            # A live candidate source remains valid even when the captured
            # snapshot is empty and regardless of the field's declared type.
            if param.category == "user_param" and not _param_axis_manually_edited(
                param, "category", "exposed_to_user", "editable",
            ):
                param.exposed_to_user = True
                param.editable = True
            if not _param_field_manually_edited(param, "need_human_confirm"):
                param.need_human_confirm = False
            if param.type in _ENUM_PARAM_TYPES:
                _refresh_param_enum_description(param)
            continue
        option_contract = bool(
            param.enum_options
            or param.enum_value_map
            or normalized_path in display_paths
            or _param_has_option_control_evidence(param)
        )
        if normalized_path in id_paths and normalized_path not in display_paths:
            continue
        if param.type in _ENUM_PARAM_TYPES or param.source_kind in _ENUM_SOURCE_KINDS:
            if not option_contract:
                if (
                    not _param_field_manually_edited(param, "type")
                    and not _param_has_grounded_type(param)
                ):
                    param.type = param.wire_type or _infer_type_from_value(param.value)
                if not _param_axis_manually_edited(param, "enum_options", "enum_value_map"):
                    param.enum_options = None
                    param.enum_value_map = None
                if (
                    param.category == "user_param"
                    and not _param_field_manually_edited(param, "type")
                    and not _param_axis_manually_edited(
                        param, "source_kind", "source", "exposed_to_user", "editable",
                    )
                ):
                    param.source_kind = "user_input"
                    param.source = {"kind": "sample", "path": param.path}
                    param.exposed_to_user = True
                    param.editable = True
                if not _param_field_manually_edited(param, "description"):
                    param.description = _strip_option_descriptions(param.description) or None
                if not _param_field_manually_edited(param, "reason"):
                    param.reason = _strip_option_descriptions(param.reason)
            else:
                if not _param_axis_manually_edited(
                    param, "category", "exposed_to_user", "editable",
                ):
                    param.category = "user_param"
                    param.exposed_to_user = True
                    param.editable = True
                _refresh_param_enum_description(param)
        elif param.category == "user_param" and param.source_kind == "user_input":
            semantic_type = _semantic_recorded_type(param)
            if (
                semantic_type in {"date", "datetime"}
                and not _param_field_manually_edited(param, "type")
                and not _param_has_interacted_temporal_control(param)
                and not _param_has_grounded_type(param)
            ):
                param.type = semantic_type




def _page_enum_contract_for_param(
    spec: FlowSpec,
    step: FlowStep,
    param: ParamField,
    binding: SelectBinding,
) -> tuple[list[Any], dict[str, Any], dict[str, Any]] | None:
    """Return a page enum only when ownership and the full wire map are proven."""
    page_options = _page_enum_options_from_request_facts(spec.request_facts)
    def normalized(value: Any) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).casefold()
    param_names = {
        normalized(value) for value in (
            param.path, _strip_body_prefix(param.path or ""),
            _strip_body_prefix(param.path or "").split(".")[-1],
        ) if normalized(value)
    }
    keys = [
        binding.path, binding.id_path, param.path, param.key, param.label,
        _strip_body_prefix(binding.path or ""), _strip_body_prefix(param.path or ""),
    ]
    for key in [str(value or "") for value in keys if str(value or "")]:
        raw = page_options.get(key)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            # Legacy list/label-only snapshots are useful diagnostics but do
            # not prove control ownership or the backend wire values.
            continue
        if (
            str(raw.get("control_kind") or "").lower() != "select"
            or raw.get("mapping_complete") is not True
            or not _recording_evidence_matches_request(step.source_meta or {}, raw)
        ):
            continue
        aliases = [normalized(value) for value in (raw.get("field_aliases") or []) if normalized(value)]
        field_key = normalized(raw.get("field_key"))
        if aliases:
            if not any(alias in param_names for alias in aliases):
                continue
        elif not field_key or field_key not in param_names:
            continue
        source = str(raw.get("enum_source") or "dom").strip()
        if source == "script_dictionary":
            if not raw.get("source_url") or not raw.get("dict_type"):
                continue
        elif source == "script_static":
            if not raw.get("script_url"):
                continue
        elif source != "dom":
            continue
        options = list(raw.get("options") or raw.get("values") or [])
        if len(options) < 2:
            continue
        value_map: dict[str, Any] = {}
        valid = True
        for option in options:
            pair = _enum_label_value(option)
            if pair is None or pair[0] in value_map or pair[1] is None:
                valid = False
                break
            value_map[str(pair[0])] = pair[1]
        explicit_map = dict(raw.get("option_map") or raw.get("value_map") or {})
        if not valid or len(value_map) != len(options):
            continue
        if any(
            label in explicit_map and str(explicit_map[label]) != str(value)
            for label, value in value_map.items()
        ):
            continue
        selected_label = str(raw.get("selected_label") or raw.get("selected") or "").strip()
        selected_value = raw.get("selected_value")
        current = param.value
        wire_value = selected_value if selected_value not in (None, "") else current
        if (
            selected_value not in (None, "")
            and current not in (None, "")
            and str(selected_value) != str(current)
        ):
            continue
        if selected_label:
            if selected_label not in value_map or wire_value in (None, "") or str(value_map[selected_label]) != str(wire_value):
                continue
        elif current not in (None, ""):
            if sum(str(value) == str(current) for value in value_map.values()) != 1:
                continue
        return options, value_map, {
            "enum_source": source,
            "source_url": str(raw.get("source_url") or ""),
            "dict_type": str(raw.get("dict_type") or ""),
            "script_url": str(raw.get("script_url") or ""),
        }
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
        if param.type in _ENUM_PARAM_TYPES or param.source_kind in _ENUM_SOURCE_KINDS or param.source_kind == "api_option":
            continue
        if not _param_axis_manually_edited(param, "enum_options", "enum_value_map"):
            param.enum_options = None
            param.enum_value_map = None
        if not _param_field_manually_edited(param, "description"):
            param.description = _strip_option_descriptions(param.description) or None
        if not _param_field_manually_edited(param, "reason"):
            param.reason = _strip_option_descriptions(param.reason)
    grounded_bindings: list[SelectBinding] = []
    for binding in step.selects or []:
        _hydrate_select_source_contract(spec, binding)
        # Paired controls commonly have both ``name`` and ``id`` leaves.  The
        # caller-facing option contract belongs to the display/name path; the ID
        # remains a runtime-derived request field.  Only use id_path when there is
        # no separate display path in the request.
        param = next((
            item for item in (step.params or [])
            if binding.path and item.path == binding.path
        ), None)
        if param is None:
            param = next((
                item for item in (step.params or [])
                if binding.id_path and item.path == binding.id_path
            ), None)
        if param is None:
            continue
        if param.source_kind in {"selected_option_field", "computed"}:
            continue
        if param.locked:
            grounded_bindings.append(binding)
            continue
        type_owned = _param_field_manually_edited(param, "type")
        category_owned = _param_axis_manually_edited(
            param, "category", "exposed_to_user", "editable",
        )
        source_owned = _param_axis_manually_edited(param, "source_kind", "source")
        options_owned = _param_axis_manually_edited(param, "enum_options", "enum_value_map")
        page_contract = _page_enum_contract_for_param(spec, step, param, binding)
        if page_contract:
            page_options, page_value_map, page_meta = page_contract
            page_source = str(page_meta.get("enum_source") or "dom")
            binding.options = copy.deepcopy(page_options)
            binding.option_map = dict(page_value_map)
            binding.count = len(page_options)
            binding.enum_confirmed = True
            if page_source == "script_dictionary":
                source_changed = str(binding.source_url or "") != str(page_meta.get("source_url") or "")
                binding.source_url = str(page_meta.get("source_url") or "")
                binding.source_method = "GET"
                binding.value_key = "value"
                binding.label_key = "label"
                binding.category_key = "dictType"
                binding.category_value = str(page_meta.get("dict_type") or "")
                binding.enum_source = "api"
                if source_changed:
                    binding.source_headers = {}
                    binding.source_body = None
                    binding.source_content_type = ""
                    binding.source_role = ""
                    binding.source_request_id = ""
                _hydrate_select_source_contract(spec, binding)
            else:
                # A complete DOM/static map is self-contained.  Keeping an old
                # guessed endpoint here lets unknown labels fall through to a
                # foreign API at runtime, so clear every stale dynamic source.
                binding.source_url = ""
                binding.source_method = "GET"
                binding.source_headers = {}
                binding.source_body = None
                binding.source_content_type = ""
                binding.source_role = ""
                binding.source_request_id = ""
                binding.value_key = ""
                binding.label_key = ""
                binding.category_key = None
                binding.category_value = None
                binding.enum_source = "script_static" if page_source == "script_static" else "dom"
        source_path = _request_path({"url": binding.source_url}) if binding.source_url else ""
        captured_source = any(
            fact.response_json is not None
            and (fact.method or "GET").upper() in {"GET", "HEAD"}
            and _request_path({"url": fact.path or fact.url}) == source_path
            for fact in (spec.request_facts.requests or [])
        ) if source_path else False
        api_contract = bool(
            binding.source_url
            and binding.value_key
            and binding.label_key
            and (captured_source or binding.option_map or binding.options)
            and str(binding.enum_source or "api") == "api"
        )
        static_contract = bool(
            str(binding.enum_source or "") == "script_static"
            and (binding.option_map or binding.options)
        )
        dom_contract = bool(
            page_contract
            or (
                str(binding.enum_source or "") == "dom"
                and (binding.option_map or binding.options)
            )
        )
        manual_contract = bool(
            str(binding.enum_source or "") == "manual"
            and (binding.option_map or binding.options)
        )
        if not (api_contract or static_contract or dom_contract or manual_contract):
            # A field name, a numeric sample, or a URL without a captured
            # label/value contract is not enum evidence. Preserve the binding
            # itself so a user can finish/edit the configuration without the
            # next sync silently deleting it, but keep it unconfirmed and do
            # not project it as an executable enum contract.
            binding.enum_confirmed = False
            grounded_bindings.append(binding)
            if not type_owned and not source_owned:
                param.type = param.wire_type or _infer_type_from_value(param.value)
            if not options_owned and not source_owned:
                param.enum_options = None
                param.enum_value_map = None
            if param.category == "user_param" and not type_owned:
                if not source_owned:
                    param.source_kind = "user_input"
                    param.source = {"kind": "sample", "path": param.path}
                if not category_owned:
                    param.exposed_to_user = True
                    param.editable = True
            if not _param_field_manually_edited(param, "need_human_confirm"):
                param.need_human_confirm = False
            if not _param_field_manually_edited(param, "description"):
                param.description = _strip_option_descriptions(param.description) or None
            if not _param_field_manually_edited(param, "reason"):
                param.reason = _strip_option_descriptions(param.reason)
            continue
        source_kind = (
            # A captured option endpoint is the stronger and renewable source.
            # Its DOM snapshot remains evidence/default material, but must not
            # hide the live source from the exported contract.
            "api_option" if api_contract
            else "page_enum" if dom_contract
            else "manual_enum" if manual_contract
            else "static_enum"
        )
        grounded_bindings.append(binding)
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
        # Every grounded choice is an enum in the caller-facing business
        # contract. The recorded scalar/array transport remains independently
        # available in wire_type for request serialization.
        if not param.wire_type:
            param.wire_type = param.type
        if not type_owned:
            param.type = "list-enum" if binding.multi else "enum"
        if not category_owned:
            param.category = "user_param"
            param.exposed_to_user = True
            param.editable = True
        keep_hydration = (
            param.source_kind == "previous_response"
            and bool(
                (param.source or {}).get("link_id")
                or (param.source or {}).get("allow_caller_override")
                or (param.source or {}).get("option_source")
            )
        )
        if not source_owned and not keep_hydration:
            param.source_kind = source_kind
        if not options_owned and source_kind == "api_option":
            # The selected API is authoritative, including an empty result.
            # Never resurrect candidates captured from the previously selected
            # endpoint after a source change.
            param.enum_options = list(options or []) or None
            param.enum_value_map = dict(option_map or {}) or None
        elif not options_owned:
            param.enum_options = list(options or param.enum_options or []) or None
            param.enum_value_map = dict(option_map or param.enum_value_map or {}) or None
        if not source_owned:
            option_contract = {
                "kind": source_kind,
                "source_url": binding.source_url if source_kind == "api_option" else None,
                "source_method": binding.source_method,
                "source_request_id": binding.source_request_id,
                "value_key": binding.value_key,
                "label_key": binding.label_key,
                "category_key": binding.category_key,
                "category_value": binding.category_value,
                "id_path": binding.id_path or binding.path or param.path,
                "enum_source": (
                    "dom" if source_kind == "page_enum"
                    else "script_static" if source_kind == "static_enum"
                    else "manual" if source_kind == "manual_enum"
                    else "api"
                ),
                "enum_confirmed": (
                    len(option_map) == len(options or [])
                    if page_contract
                    else (binding.enum_confirmed if binding.enum_confirmed is not None else True)
                ),
            }
            if keep_hydration:
                param.source_kind = "previous_response"
                param.source = {
                    **dict(param.source or {}),
                    "kind": "previous_response",
                    "allow_caller_override": True,
                    "option_source": option_contract,
                }
            else:
                param.source = {**dict(param.source or {}), **option_contract}
        if not _param_field_manually_edited(param, "need_human_confirm"):
            param.need_human_confirm = bool(
                source_kind == "unknown"
                or (
                    source_kind == "page_enum"
                    and (param.source or {}).get("enum_confirmed") is False
                )
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
    step.selects = grounded_bindings


def _strip_body_prefix(path: str) -> str:
    return path[len("body."):] if path.startswith("body.") else path


def _record_param_manual_contract(param: ParamField, fields: list[str] | tuple[str, ...]) -> None:
    """Mark explicit operator-owned ParamField axes before any derived sync."""
    axis_by_field = {
        "key": "name", "label": "name", "name": "name", "display_name": "name",
        "path": "path", "value": "default_value", "default_value": "default_value",
        "type": "type", "wire_type": "path", "category": "category",
        "exposed_to_user": "category", "exposed_to_caller": "category",
        "editable": "category", "source_kind": "source", "source": "source",
        "enum_options": "source", "enum_value_map": "source",
        "required": "required",
    }
    for field in dict.fromkeys(fields):
        if not hasattr(param, field):
            continue
        param.evidence.append({
            "source": "manual_edit",
            "field": field,
            "axis": axis_by_field.get(field, field),
            "status": "locked",
            "kind": "manual_override",
            "value": getattr(param, field),
        })


def _reset_param_source(
    param: ParamField,
    *,
    reason: str | None = None,
    actor: str = "system",
) -> None:
    """把字段从运行期/接口来源恢复成普通用户输入，供删除依赖/重置来源使用。"""
    normalized_actor = str(actor or "system").strip().lower()
    if normalized_actor in _AUTOMATED_FIELD_EDIT_ACTORS and (
        param.locked
        or _param_axis_manually_edited(
            param, "category", "source_kind", "source", "editable",
            "exposed_to_user", "need_human_confirm",
        )
    ):
        return
    param.category = "user_param"
    param.source_kind = "user_input"
    param.source = {"kind": "sample", "path": param.path}
    param.editable = True
    param.exposed_to_user = True
    param.need_human_confirm = False
    param.confidence_tier = "manual"
    param.reason = reason or "已取消运行期/接口来源绑定，改为调用 Skill 时由用户填写"
    if normalized_actor == "user":
        _record_param_manual_contract(param, (
            "category", "source_kind", "source", "editable",
            "exposed_to_user", "need_human_confirm",
        ))


_ENUM_PARAM_TYPES = frozenset({"enum", "list-enum"})
_ENUM_SOURCE_KINDS = frozenset({
    "page_enum", "static_enum", "manual_enum", "form_option",
})




def _transition_param_type(param: ParamField, value: Any) -> None:
    """Apply only the explicitly edited type; never rewrite other field choices."""
    param.type = str(value or "string")


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


_AUTOMATED_FIELD_EDIT_ACTORS = frozenset({
    "planner", "repair", "auto", "autofix", "optimizer", "system",
})

_DEFAULT_RECORDED_FORBIDDEN_ACTIONS = [
    "调用当前录制范围外的接口",
    "篡改录制事实",
    "泄露认证凭证",
]
_LEGACY_RECORDED_FORBIDDEN_ACTIONS = frozenset({"删除", "作废", "撤销", "终止", "驳回"})


def _param_has_grounded_public_name(param: ParamField) -> bool:
    """Return whether recorder/operator/agent evidence already owns the public name."""
    if _param_has_full_lock(param) or param.name_source in {"manual", "sample", "assignee", "agent"}:
        return True
    public_name = str(param.label or param.key or "").strip()
    # Planner/LLM names are proposals, not recorder/operator facts. Let later
    # analyses refine them even when an earlier plan marked its own confidence
    # as grounded.
    model_owned_name = param.name_source in {"", "auto", "planner", "llm", "optimizer"}
    if (
        not model_owned_name
        and param.confidence_tier in {"grounded", "linked", "manual"}
        and public_name
        and not looks_internal_param_name(public_name)
    ):
        return True
    return any(
        isinstance(item, dict)
        and item.get("source") == "recorder_dom"
        and item.get("kind") == "page_control"
        and (item.get("field_aliases") or [])
        and public_name
        and not looks_internal_param_name(public_name)
        for item in (param.evidence or [])
    )


def _param_has_grounded_type(param: ParamField) -> bool:
    """Return whether evidence grounds the business type, not its wire shape."""
    if _param_has_full_lock(param) or _param_field_manually_edited(param, "type"):
        return True
    if str(param.type or "") in {"", "unknown"}:
        return False
    if param.type in {"enum", "list-enum"} and (
        param.enum_options or param.enum_value_map or _param_has_executable_source(param)
    ):
        return True
    screenshot = _screenshot_control_evidence({"evidence": param.evidence})
    if _screenshot_control_supports_axis(screenshot, "type"):
        return True
    return any(
        isinstance(item, dict)
        and str(item.get("source") or item.get("kind") or "").lower()
        in {"recorder_dom", "page", "page_snapshot"}
        and str(item.get("control_kind") or "unknown") != "unknown"
        for item in (param.evidence or [])
    )

_SCREENSHOT_CONTROL_KINDS = frozenset({
    "input", "text", "textarea", "number", "date", "datetime", "time",
    "select", "combobox", "cascader", "picker", "checkbox", "radio",
    "switch", "slider", "upload", "file", "tree_select", "rich_text",
})
_SCREENSHOT_INTERNAL_SOURCE_KINDS = frozenset({
    "current_user", "page_context", "system_time", "constant",
    "computed", "system_generated",
})



def _canonical_screenshot_control(raw: dict[str, Any]) -> dict[str, Any] | None:
    return next((
        item for item in reversed(raw.get("evidence") or [])
        if isinstance(item, dict) and item.get("canonical_screenshot_control") is True
    ), None)


def _screenshot_control_supports_axis(
    control: dict[str, Any] | None,
    axis: str,
) -> bool:
    if control is None:
        return False
    declared = {
        str(value).strip().lower()
        for value in (control.get("axes") or [])
        if str(value or "").strip()
    }
    aliases = {
        "name": {"name", "label", "display_name", "public_name"},
        "path": {"path", "wire_path"},
        "default": {"default", "default_value", "visible_default"},
        "type": {"type", "business_type", "control_kind"},
        "category": {"category"},
        "source": {"source", "source_kind"},
        "required": {"required", "requiredness"},
    }
    return not declared or bool(declared & aliases.get(axis, {axis}))


def _screenshot_control_evidence(raw: dict[str, Any]) -> dict[str, Any] | None:
    canonical = _canonical_screenshot_control(raw)
    if canonical is not None:
        if canonical.get("control_kind_conflict"):
            return None
        control_kind = str(canonical.get("control_kind") or "").strip().lower()
        return canonical if control_kind in _SCREENSHOT_CONTROL_KINDS else None
    for item in raw.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or item.get("kind") or "").strip().lower()
        control_kind = str(item.get("control_kind") or "").strip().lower()
        if source == "screenshot" and control_kind in _SCREENSHOT_CONTROL_KINDS:
            return item
    return None


def _grounded_control_evidence(raw: dict[str, Any]) -> dict[str, Any] | None:
    screenshot = _screenshot_control_evidence(raw)
    if screenshot is not None:
        return screenshot
    return next((
        item for item in reversed(raw.get("evidence") or [])
        if isinstance(item, dict)
        and str(item.get("source") or item.get("kind") or "").strip().lower()
        in {"recorder_dom", "page", "page_snapshot", "page_control"}
        and str(item.get("control_kind") or "").strip().lower()
        in _SCREENSHOT_CONTROL_KINDS
    ), None)


def _grounded_screenshot_query_path(
    step: FlowStep,
    raw: dict[str, Any],
) -> str | None:
    """Resolve one screenshot control to a query param without guessing.

    Existing request params are authoritative. A control missing from the
    recorded request may be added only when the same leaf occurs exactly once
    in that request's response schema; this covers untouched list filters while
    rejecting label-only screenshot inventions.
    """
    control = _screenshot_control_evidence(raw)
    if (
        control is None
        or str(step.method or "GET").upper() not in {"GET", "HEAD"}
        or control.get("editable") is False
        or control.get("disabled")
        or control.get("read_only")
    ):
        return None
    proposed = str(raw.get("path") or raw.get("wire_path") or "").strip()
    leaf = re.sub(r"^(?:query|body)\.", "", proposed, flags=re.I)
    if not leaf or "." in leaf or "[" in leaf or "]" in leaf:
        return None
    existing = [
        param.path for param in step.params
        if re.sub(r"^(?:query|body)\.", "", param.path, flags=re.I).lower()
        == leaf.lower()
    ]
    if len(existing) == 1:
        return existing[0]
    response_matches = [
        path for path in normalized_leaf_paths(step.response_json)
        if str(path).rsplit(".", 1)[-1].lower() == leaf.lower()
    ]
    return (
        f"query.{response_matches[0].rsplit('.', 1)[-1]}"
        if len(response_matches) == 1 else None
    )


def _param_has_grounded_direct_input_contract(param: ParamField) -> bool:
    """A visible editable non-choice control must not be repaired into an enum."""
    screenshot = _screenshot_control_evidence({"evidence": param.evidence})
    if screenshot is not None and (
        _screenshot_control_supports_axis(screenshot, "type")
        or _screenshot_control_supports_axis(screenshot, "source")
    ):
        control_kind = str(screenshot.get("control_kind") or "").strip().lower()
        return bool(
            control_kind not in _SCREENSHOT_OPTION_CONTROL_KINDS
            and not (control_kind == "checkbox" and screenshot.get("options"))
            and screenshot.get("editable") is not False
            and not screenshot.get("disabled")
            and not screenshot.get("read_only")
        )
    for item in param.evidence or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or item.get("kind") or "").strip().lower()
        control_kind = str(item.get("control_kind") or "").strip().lower()
        if (
            source not in {"recorder_dom", "page", "page_snapshot", "page_control"}
            or control_kind not in _SCREENSHOT_CONTROL_KINDS
        ):
            continue
        if control_kind in _SCREENSHOT_OPTION_CONTROL_KINDS:
            continue
        if control_kind == "checkbox" and item.get("options"):
            continue
        if (
            item.get("editable") is not False
            and not item.get("disabled")
            and not item.get("read_only")
        ):
            return True
    return False


def _param_has_executable_source(param: ParamField) -> bool:
    if param.source_kind == "api_option":
        return bool(param.source or param.enum_value_map or param.enum_options)
    if param.source_kind == "previous_response":
        return bool(param.source)
    return False


def _apply_capability_field_to_param(
    spec: FlowSpec,
    raw: dict[str, Any],
    *,
    scope: str,
    actor: str = "user",
) -> bool:
    """Persist a step-bound capability field edit on its canonical ParamField.

    Automated semantic plans may fill unresolved axes, but recorded DOM/API facts
    and operator edits remain authoritative. Capability views must never degrade
    the canonical field contract.
    """
    normalized_actor = str(actor or "user").strip().lower()
    automated = normalized_actor in _AUTOMATED_FIELD_EDIT_ACTORS
    step_id = str(raw.get("step_id") or "")
    path = str(
        raw.get("path") or raw.get("wire_path")
        or (raw.get("key") if not automated else "")
        or ""
    )
    if not step_id or not path:
        return False
    from dano.execution.page.recording_field_identity import FieldRef, FieldReferenceError, resolve_field_ref

    is_step_id = any(item.step_id == step_id for item in spec.steps)
    try:
        resolved = resolve_field_ref(spec, FieldRef(
            step_id=step_id if is_step_id else "",
            request_id="" if is_step_id else step_id,
            wire_path=path,
        ))
        step = resolved.step
        param = resolved.param
        path = resolved.stored_path
    except FieldReferenceError:
        step = next((item for item in spec.steps if item.step_id == step_id), None)
        param = None
    if step is None:
        return False
    if automated:
        if param is None:
            grounded_path = _grounded_screenshot_query_path(step, raw)
            if grounded_path is None:
                return False
            path = grounded_path
            raw = {**raw, "path": path}
            param = next((item for item in step.params if item.path == path), None)
            if param is None:
                control = _screenshot_control_evidence(raw)
                control_kind = str((control or {}).get("control_kind") or "").lower()
                option_control = control_kind in _SCREENSHOT_OPTION_CONTROL_KINDS
                options = (
                    raw.get("enum_options")
                    if isinstance(raw.get("enum_options"), list)
                    else (control or {}).get("options")
                )
                source_kind = "form_option" if option_control and not options else (
                    "page_enum" if option_control else "user_input"
                )
                raw = {
                    **raw,
                    "source_kind": source_kind,
                    "source": {
                        "kind": source_kind, "path": path,
                        **({"enum_confirmed": False} if option_control else {}),
                    },
                    **({"enum_options": options} if isinstance(options, list) and options else {}),
                }
                param = ParamField(
                    path=path,
                    key=str(raw.get("key") or raw.get("display_name") or path.rsplit(".", 1)[-1]),
                    label=str(raw.get("display_name") or raw.get("key") or ""),
                    value="",
                    type="string",
                    wire_type="string",
                    required=bool((control or {}).get("required") is True),
                    category="user_param",
                    source_kind="unknown",
                    default_value=None,
                    need_human_confirm=bool(option_control and not options),
                    evidence=[{
                        "source": "response_schema_match",
                        "path": next(
                            response_path
                            for response_path in normalized_leaf_paths(step.response_json)
                            if response_path.rsplit(".", 1)[-1].lower()
                            == path.rsplit(".", 1)[-1].lower()
                        ),
                    }],
                )
                step.params.append(param)
    else:
        try:
            param = _find_param(
                step, path,
                param_key=str(raw.get("key") or ""),
                param_label=str(raw.get("display_name") or ""),
            )
        except ValueError:
            return False
    if automated and _param_has_full_lock(param):
        return True

    screenshot_control = _grounded_control_evidence(raw) if automated else None
    screenshot_name_axis = _screenshot_control_supports_axis(screenshot_control, "name")
    screenshot_type_axis = _screenshot_control_supports_axis(screenshot_control, "type")
    screenshot_category_axis = _screenshot_control_supports_axis(
        screenshot_control, "category"
    )
    screenshot_source_axis = _screenshot_control_supports_axis(
        screenshot_control, "source"
    )
    screenshot_required_axis = _screenshot_control_supports_axis(
        screenshot_control, "required"
    )
    allow_name = (
        not automated
        or (
            not _param_axis_manually_edited(param, "key", "label", "name", "display_name")
            and (
                not _param_has_grounded_public_name(param)
                or screenshot_name_axis
            )
        )
    )
    screenshot_editable = bool(
        screenshot_control is not None
        and screenshot_control.get("editable") is not False
        and not screenshot_control.get("disabled")
        and not screenshot_control.get("read_only")
    )
    screenshot_control_kind = str(
        (screenshot_control or {}).get("control_kind") or ""
    ).strip().lower()
    screenshot_option_control = bool(
        screenshot_control_kind in _SCREENSHOT_OPTION_CONTROL_KINDS
        or (
            screenshot_control_kind == "checkbox"
            and (screenshot_control or {}).get("options")
        )
    )
    screenshot_direct_input = bool(
        screenshot_editable and not screenshot_option_control
    )
    screenshot_editable_input = bool(
        screenshot_editable
        and screenshot_source_axis
        and str(raw.get("source_kind") or "") == "user_input"
    )
    screenshot_page_enum = bool(
        screenshot_editable
        and screenshot_source_axis
        and screenshot_option_control
        and str(raw.get("source_kind") or "") in {
            "page_enum", "static_enum", "form_option",
        }
        and param.source_kind != "api_option"
    )
    screenshot_user_category = bool(
        screenshot_editable
        and screenshot_category_axis
        and str(raw.get("category") or "") == "user_param"
    )
    screenshot_safe_internal_source = bool(
        screenshot_control is not None
        and screenshot_source_axis
        and (
            screenshot_control.get("editable") is False
            or screenshot_control.get("disabled")
            or screenshot_control.get("read_only")
        )
        and str(raw.get("source_kind") or "") in _SCREENSHOT_INTERNAL_SOURCE_KINDS
    )
    screenshot_safe_internal_category = bool(
        screenshot_control is not None
        and screenshot_category_axis
        and (
            screenshot_control.get("editable") is False
            or screenshot_control.get("disabled")
            or screenshot_control.get("read_only")
        )
        and str(raw.get("category") or "") in {"runtime_var", "system_const"}
    )
    stale_text_option_recovery = bool(
        automated
        and str(raw.get("source_kind") or "") == "user_input"
        and str(raw.get("type") or "") in {"string", "text", "textarea"}
        and not _param_axis_manually_edited(
            param, "source_kind", "source", "category",
            "exposed_to_user", "exposed_to_caller",
        )
        and _weak_automatic_text_option_binding(param)
    )
    semantic_text_type_recovery = bool(
        automated
        and str(raw.get("type") or "") in {"string", "text", "textarea"}
        and str(param.wire_type or "") == "string"
        and str(param.type or "") not in {"string", "text", "textarea"}
        and str(raw.get("source_kind") or param.source_kind or "") == "user_input"
        and _looks_user_entered_business_field(param.key, param.path)
    )
    allow_type = (
        not automated
        or (
            not _param_field_manually_edited(param, "type")
            and (
                not _param_has_grounded_type(param)
                or screenshot_type_axis
                or stale_text_option_recovery
                or semantic_text_type_recovery
            )
        )
    )
    allow_source = (
        not automated
        or (
            not _param_axis_manually_edited(
                param, "source_kind", "source", "category",
                "exposed_to_user", "exposed_to_caller",
            )
            and (
                str(param.source_kind or "unknown") in {"", "unknown"}
                or (
                    (
                        screenshot_editable_input
                        or screenshot_page_enum
                        or screenshot_safe_internal_source
                    )
                    and not _param_has_executable_source(param)
                )
                or (
                    screenshot_editable_input
                    and screenshot_direct_input
                )
                or stale_text_option_recovery
            )
        )
    )
    # Category answers who supplies the value; source answers where option
    # values come from.  An editable select is a caller input even though its
    # choices still come from a captured API.
    allow_category = (
        not automated
        or (
            not _param_axis_manually_edited(
                param, "category", "exposed_to_user", "exposed_to_caller",
                "source_kind", "source",
            )
            and (
                str(param.category or "unknown") in {"", "unknown"}
                or screenshot_user_category
                or screenshot_safe_internal_category
            )
        )
    )

    if raw.get("key") and allow_name:
        if str(raw["key"]) != param.key:
            _rename_param_public_key(spec, step, param, str(raw["key"]), actor=normalized_actor)
        param.label = str(raw.get("display_name") or raw["key"])
    if raw.get("display_name") and allow_name:
        param.label = str(raw["display_name"])
    if raw.get("type") and allow_type:
        _transition_param_type(param, raw["type"])
    screenshot_required = bool(
        screenshot_control is not None
        and screenshot_required_axis
        and screenshot_control.get("required") is True
    )
    screenshot_optional = bool(
        screenshot_control is not None
        and screenshot_required_axis
        and screenshot_control.get("required") is False
        and screenshot_control.get("required_convention_confirmed") is True
        and screenshot_control.get("label_region_complete") is True
    )
    allow_required = not automated or (
        (screenshot_required or screenshot_optional)
        and not _param_field_manually_edited(param, "required")
    )
    if "required" in raw and allow_required:
        param.required = bool(raw["required"])
    if raw.get("source_kind") and allow_source:
        param.source_kind = str(raw["source_kind"])
    if isinstance(raw.get("source"), dict) and allow_source:
        param.source = dict(raw["source"])
    if (
        (screenshot_direct_input or stale_text_option_recovery)
        and allow_source
        and raw.get("source_kind") == "user_input"
    ):
        param.source = {"kind": "user_input", "path": param.path}
        param.enum_options = None
        param.enum_value_map = None
        step.selects = [
            binding for binding in (step.selects or [])
            if _strip_body_prefix(binding.path or "") != _strip_body_prefix(param.path or "")
        ]
    if screenshot_page_enum and allow_source:
        param.source = {"kind": str(raw.get("source_kind") or "page_enum"), "path": param.path}
        if isinstance(raw.get("enum_options"), list):
            param.enum_options = copy.deepcopy(raw["enum_options"])
            param.enum_value_map = None
    # Screenshot values are observations used for identity matching, not proof
    # of an initial default. Recorded request values may be temporary user input.
    allow_default = not automated
    if "visible_default" in raw and allow_default:
        param.default_value = copy.deepcopy(raw.get("visible_default"))
    if "exposed_to_caller" in raw and (not automated or allow_category):
        param.exposed_to_user = bool(raw["exposed_to_caller"])
    if scope == "input" and allow_category:
        param.category = "user_param"
        param.exposed_to_user = True
    elif scope == "internal" and allow_category:
        param.category = "system_const" if param.source_kind == "constant" else "runtime_var"
        param.exposed_to_user = False
    if not automated and "locked" in raw:
        param.locked = bool(raw.get("locked"))
    if "confirmed" in raw:
        param.need_human_confirm = not bool(raw.get("confirmed"))
    incoming_evidence = [
        evidence for evidence in (raw.get("evidence") or [])
        if isinstance(evidence, dict)
    ]
    if automated and any(
        evidence.get("canonical_screenshot_control") is True
        for evidence in incoming_evidence
    ):
        param.evidence = [
            evidence for evidence in (param.evidence or [])
            if not (
                isinstance(evidence, dict)
                and (
                    evidence.get("canonical_screenshot_control") is True
                    or str(evidence.get("source") or "").strip().lower()
                    in {"screenshot", "reference_screenshot", "uploaded_screenshot"}
                )
            )
        ]
    param.evidence.append({
        "source": "capability_field_edit", "scope": scope, "actor": normalized_actor,
        "applied_axes": {
            "name": bool(allow_name), "type": bool(allow_type),
            "category": bool(allow_category), "source": bool(allow_source),
            "required": bool(allow_required), "default": bool(allow_default),
        },
    })
    for evidence in incoming_evidence:
        param.evidence.append({
            **evidence,
            "source": str(evidence.get("source") or "planner_semantic_evidence"),
        })
    if normalized_actor == "user":
        manual_fields = [
            field for field in ("type", "source_kind", "source", "exposed_to_caller")
            if field in raw
        ]
        if raw.get("key"):
            manual_fields.append("key")
        if raw.get("display_name"):
            manual_fields.append("label")
        if "required" in raw:
            manual_fields.append("required")
        if scope in {"input", "internal"}:
            manual_fields.extend(["category", "exposed_to_user"])
        for field in dict.fromkeys(manual_fields):
            value = (
                param.exposed_to_user if field in {"exposed_to_caller", "exposed_to_user"}
                else getattr(param, field, None)
            )
            param.evidence.append({
                "source": "manual_edit", "field": field, "value": value,
            })
    return True

def _capability_confirmation_hash(
    spec: FlowSpec,
    cap: FlowCapability,
    *,
    prepared: bool = False,
) -> str:
    # Hash the same canonical contract shape used by validation/publish. Raw
    # editor state may still have derived fields or schemas pending sync;
    # hashing it directly made an immediate validation look stale.
    canonical = spec if prepared else prepare_flow_spec_for_publish(spec)
    canonical_cap = next(
        (
            item for item in canonical.capabilities
            if item.capability_id == cap.capability_id
        ),
        cap,
    )
    by_id = {step.step_id: step for step in canonical.steps}
    def link_contract(link: FlowLink) -> dict[str, Any]:
        # Verification state proves a dependency; it is not part of the
        # dependency's executable identity. Keep endpoints and transform shape
        # fingerprinted while allowing trusted verification to add its receipt.
        return link.model_dump(exclude={
            "confirmed", "confidence", "reason", "evidence", "meta",
        })

    capability_contract = canonical_cap.model_dump(exclude={
        "confirmed", "confirmation_hash", "status", "requires_human_confirm",
        "confidence", "updated_by",
    })
    capability_contract["dependencies"] = [
        dependency.model_dump(exclude={
            "confirmed", "confidence", "reason", "evidence", "locked",
        })
        for dependency in canonical_cap.dependencies
    ]
    payload = {
        "capability": capability_contract,
        "steps": [
            by_id[sid].model_dump()
            for sid in _capability_node_step_ids(canonical_cap)
            if sid in by_id
        ],
        "links": [
            link_contract(link)
            for link in canonical.links
            if link.source_step_id in set(_capability_node_step_ids(canonical_cap))
            and link.target_step_id in set(_capability_node_step_ids(canonical_cap))
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _remove_param_incoming_links(spec: FlowSpec, step: FlowStep, param: ParamField) -> None:
    """人工把字段改离上游响应时，依赖与字段来源必须在同一事务内解除。"""
    removed = [
        link for link in spec.links
        if link.target_step_id == step.step_id
        and _reference_targets_param(step, link.target_path, param)
    ]
    for link in removed:
        _record_rejected_dependency(spec, link)
    if removed:
        removed_ids = {link.link_id for link in removed}
        spec.links = [link for link in spec.links if link.link_id not in removed_ids]


def _apply_link_sources(steps: list[FlowStep], links: list[FlowLink]) -> None:
    by_id = {s.step_id: s for s in steps}
    for lk in links:
        if (lk.meta or {}).get("actor") == "agent" and not (lk.meta or {}).get("verified"):
            continue
        link_kind = _flow_link_kind(lk)
        target = by_id.get(lk.target_step_id)
        source = by_id.get(lk.source_step_id)
        if target is None or source is None:
            continue
        target_path = lk.target_path
        if link_kind == "response_key_map":
            # The response supplies the *keys* of this request object, not its
            # assignee values. Keep the stable label-to-value map as caller
            # input while execution translates labels to the latest keys.
            public = next((p for p in target.params if p.path == target_path), None)
            binding = lk.value_binding or {}
            input_field = str(binding.get("input_field") or "").strip()
            if public is not None and input_field:
                option_source = binding.get("option_source")
                input_fields_by_label = {
                    str(label): str(field)
                    for label, field in dict(binding.get("input_fields_by_label") or {}).items()
                    if str(label) and str(field)
                }
                if input_fields_by_label:
                    public.category = "runtime_var"
                    public.source_kind = "dynamic_structure"
                    public.source = {
                        "kind": "dynamic_structure_leaf",
                        "required_state": "internal",
                    }
                    public.required = False
                    public.editable = False
                    public.exposed_to_user = False
                    public.need_human_confirm = False
                    samples = public.value if isinstance(public.value, dict) else {}
                    target.sample_inputs.pop(input_field, None)
                    for label, field in input_fields_by_label.items():
                        if label in samples:
                            target.sample_inputs[field] = copy.deepcopy(samples[label])
                    continue
                public.key = input_field
                public.type = "object"
                public.wire_type = "object"
                public.category = "user_param"
                public.source_kind = "user_input"
                public.source = {
                    "kind": "dynamic_structure_input",
                    "required_state": "required",
                    **({"option_source": copy.deepcopy(option_source)} if isinstance(option_source, dict) else {}),
                }
                public.required = True
                public.editable = True
                public.exposed_to_user = True
                public.need_human_confirm = False
                # response_key_map exposes a stable label-to-value object.
                # Keep the canonical sample in that public form as well; the
                # executor alone translates labels to the latest response keys.
                if isinstance(public.value, dict):
                    target.sample_inputs[input_field] = copy.deepcopy(public.value)
            continue
        if link_kind == "structure":
            # A structure link controls request keys only. It is not a value
            # dependency and must not replace the request container itself.
            continue
        target_param = _resolve_param_reference(target, target_path)
        for p in [target_param] if target_param is not None else []:
            hydration = bool(
                (lk.meta or {}).get("captured_record_hydration")
                or (lk.evidence or {}).get("kind") == "record_hydration"
            )
            captured_binding_overrides_agent_input = bool(
                p.source_kind in {
                    "user_input", "page_default", "unknown", *_OPTION_SOURCE_KINDS,
                }
                and not _param_was_caller_typed(p)
                and lk.confirmed
                and float(lk.confidence or 0.0) >= 0.95
                and not (lk.meta or {}).get("unverified_reason")
                and any(
                    (lk.meta or {}).get(key) is True
                    for key in (
                        "captured_value_match",
                        "captured_structure_match",
                        "captured_record_hydration",
                    )
                )
            )
            if p.locked or _param_axis_manually_edited(
                p, "category", "source_kind", "source", "editable", "exposed_to_user",
            ) or (
                _param_source_agent_classified(p)
                and p.source_kind != "chained"
                and not captured_binding_overrides_agent_input
                and not hydration
            ):
                # 依赖连线和字段来源是独立可编辑的事实。人工已选择
                # 分类/来源后，同步层不得再用旧连线覆盖用户结果。
                continue
            if not _auto_dependency_link_allowed(p, lk.source_path, lk):
                continue
            caller_editable = (
                not _param_control_is_readonly(p)
                and (
                    _param_has_editable_control_evidence(p)
                    or hydration
                    or _param_was_caller_typed(p)
                )
            )
            option_source = dict(p.source or {}) if p.source_kind in _OPTION_SOURCE_KINDS else {}
            if not option_source and isinstance((p.source or {}).get("option_source"), dict):
                option_source = dict((p.source or {}).get("option_source") or {})
            p.category = "user_param" if caller_editable else "runtime_var"
            p.source_kind = "previous_response"
            p.source = {
                "kind": "previous_response",
                "step_id": source.step_id,
                "step_name": source.name,
                "response_path": lk.source_path,
                "target_path": target_path,
                "link_id": lk.link_id,
                "allow_caller_override": caller_editable,
                **({"option_source": option_source} if option_source else {}),
            }
            p.editable = True
            p.exposed_to_user = caller_editable
            if not caller_editable:
                p.default_value = None
                p.required = False
                p.source = {**(p.source or {}), "required_state": "optional"}
            if caller_editable:
                p.reason = (
                    f"编辑场景默认来自上一步 `{source.name or source.path}` 的响应 `{lk.source_path}`；"
                    "调用方仍可修改该字段，显式输入优先于上游默认值"
                )
            else:
                p.reason = (
                    f"该字段由上一步 `{source.name or source.path}` 的响应 `{lk.source_path}` 提供，"
                    "运行期自动注入，不能使用录制旧值"
                )
            if _link_is_auto_generated(lk) or any(
                (lk.meta or {}).get(key) is True
                for key in ("captured_value_match", "captured_structure_match")
            ):
                p.confidence = max(
                    float(p.confidence or 0.0), float(lk.confidence or 0.0),
                )
                if lk.confirmed:
                    p.need_human_confirm = False
            p.confidence_tier = "linked"
            if p.key in target.sample_inputs:
                target.sample_inputs.pop(p.key, None)
            break


def _apply_user_link_source(steps: list[FlowStep], link: FlowLink) -> None:
    """Persist a user-created UI response binding without rewriting type/category."""
    by_id = {step.step_id: step for step in steps}
    source_step = by_id.get(link.source_step_id)
    target_step = by_id.get(link.target_step_id)
    if source_step is None or target_step is None:
        return
    target_path = link.target_path
    param = _resolve_param_reference(target_step, target_path)
    if param is None:
        return
    param.source_kind = "previous_response"
    param.source = {
        "kind": "previous_response",
        "step_id": source_step.step_id,
        "step_name": source_step.name,
        "response_path": link.source_path,
        "target_path": target_path,
        "link_id": link.link_id,
    }
    param.editable = True
    if not param.exposed_to_user:
        param.default_value = None
    param.need_human_confirm = not bool(link.confirmed)
    param.reason = (
        f"该字段由用户绑定到 `{source_step.name or source_step.path or source_step.step_id}` "
        f"的响应 `{link.source_path}`"
    )
    param.confidence = max(float(param.confidence or 0.0), float(link.confidence or 0.0))
    param.confidence_tier = "manual"
    target_step.sample_inputs.pop(param.key, None)
    _record_param_manual_contract(param, ("source_kind", "source"))


def _link_is_auto_generated(lk: FlowLink) -> bool:
    reason = str(lk.reason or "")
    evidence = lk.evidence if isinstance(lk.evidence, dict) else {}
    if evidence.get("actor") == "agent" or (lk.meta or {}).get("actor") == "agent":
        return False
    return (
        not getattr(lk, "locked", False)
        and (
            "自动" in reason
            or "值" in reason
            or "匹配" in reason
            or evidence.get("kind") == "value_match"
            or evidence.get("kind") == "record_hydration"
            or evidence.get("auto_rebuilt") is True
        )
    )


def _param_has_editable_control_evidence(param: ParamField | None) -> bool:
    if param is None:
        return False
    for item in param.evidence or []:
        if not isinstance(item, dict) or item.get("kind") != "page_control":
            continue
        if item.get("interacted"):
            return True
        if item.get("editable") and not item.get("disabled") and not item.get("read_only"):
            return True
    return False


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
    if param.category in {"system_const"} and param.source_kind != "page_default":
        return False
    if param.source_kind in {"constant", "page_context", "system_time", "system_generated", "computed", "current_user"}:
        return False
    return True


def _auto_dependency_link_allowed(param: ParamField | None, source_path: str, lk: FlowLink | None = None) -> bool:
    if lk is not None and not _link_is_auto_generated(lk):
        return True
    if param is None:
        return False
    evidence = lk.evidence if lk is not None and isinstance(lk.evidence, dict) else {}
    source_leaf = re.sub(
        r"[^a-z0-9]+", "", str(source_path or "").split(".")[-1].lower(),
    )
    target_leaf = re.sub(
        r"[^a-z0-9]+", "",
        str(param.path or param.key or "").split(".")[-1].lower(),
    )
    # Picking the first row of a previous *list* is not a dependency. The same
    # record's own line items in a detail response are hydration, not a list pick.
    if "[" in str(source_path or "") and not (
        evidence.get("kind") == "record_hydration"
        and source_leaf == target_leaf
        and int(evidence.get("match_count") or 0) >= 3
    ):
        return False
    if (
        lk is not None
        and lk.confirmed
        and float(lk.confidence or 0.0) >= 0.95
        and evidence.get("kind") == "record_hydration"
        and int(evidence.get("match_count") or 0) >= 3
        and bool(evidence.get("identity_paths"))
        and source_leaf == target_leaf
    ):
        return True
    if param.category == "user_param" or param.source_kind == "user_input" or _looks_user_entered_business_field(param.key, param.path):
        # A recorded value or a similar field name cannot prove that an editable
        # business field is supplied by an earlier response.  The exception is
        # an exact field projection observed in the same action chain: edit
        # forms use that value as an overrideable default, not as a hidden
        # runtime-only field.
        if (
            lk is not None
            and lk.confirmed
            and float(lk.confidence or 0.0) >= 0.95
            and evidence.get("same_action_chain") is True
            and _param_has_editable_control_evidence(param)
            and _dependency_match_score(param, source_path) >= 40
        ):
            return True
        evidence = lk.evidence if lk is not None and isinstance(lk.evidence, dict) else {}
        captured_match = evidence.get("captured_value_match")
        source_leaf = re.sub(
            r"[^a-z0-9]+", "", str(source_path or "").split(".")[-1].lower(),
        )
        target_leaf = re.sub(
            r"[^a-z0-9]+", "",
            str(param.path or param.key or "").split(".")[-1].lower(),
        )
        if (
            lk is not None
            and lk.confirmed
            and float(lk.confidence or 0.0) >= 0.95
            and isinstance(captured_match, dict)
            and int(captured_match.get("occurrences") or 0) == 1
            and not _param_has_editable_control_evidence(param)
            and source_leaf == "id"
            and target_leaf.endswith("id")
        ):
            return True
        # Manual links have already returned above; other automatic links need
        # a real runtime contract.
        return False
    if param is not None and lk is not None and lk.confirmed and float(lk.confidence or 0.0) >= 0.95:
        source_leaf = re.sub(r"[^a-z0-9]+", "", str(source_path or "").split(".")[-1].lower())
        target_leaf = re.sub(r"[^a-z0-9]+", "", str(param.path or param.key or "").split(".")[-1].lower())
        # 完整事实库已证明该真实值只来自一个响应端点时，允许通用 id -> *Id
        # 注入（典型为 data.id -> query.processDefinitionId）。这比字段名模糊匹配强，
        # 同时仍拒绝 title/date/status 等常见值造成的假关联。
        if source_leaf == "id" and target_leaf.endswith("id"):
            return True
        if _dependency_match_score(param, source_path) >= 40 and not _param_has_editable_control_evidence(param):
            # A read-only/default-free field with an exact wire-name match is a
            # grounded response projection, including short values such as 8.
            return True
    if not _auto_dependency_target_allowed(param):
        return False
    return True


def _auto_link_has_grounded_contract(steps: list[FlowStep], link: FlowLink) -> bool:
    by_id = {step.step_id: step for step in steps}
    positions = {step.step_id: index for index, step in enumerate(steps)}
    source = by_id.get(link.source_step_id)
    target = by_id.get(link.target_step_id)
    if source is None or target is None:
        return False
    source_sequence = _step_sequence(source)
    target_sequence = _step_sequence(target)
    if source_sequence is not None and target_sequence is not None:
        if source_sequence >= target_sequence:
            return False
    elif positions[source.step_id] >= positions[target.step_id]:
        return False
    if source.response_json is None:
        return False
    source_path = str(link.source_path or "").removeprefix("response.")
    source_value = _flow_path_lookup(source.response_json, source_path)
    if source_value is _FLOW_PATH_MISSING:
        return False
    target_param = _resolve_param_reference(target, link.target_path)
    evidence = link.evidence if isinstance(link.evidence, dict) else {}
    source_leaf = re.sub(r"[^a-z0-9]+", "", source_path.split(".")[-1].casefold())
    target_leaf = re.sub(
        r"[^a-z0-9]+",
        "",
        str((target_param.path if target_param is not None else "") or (target_param.key if target_param is not None else "") or link.target_path).split(".")[-1].casefold(),
    )
    hydration_match = bool(
        evidence.get("kind") == "record_hydration"
        and not isinstance(evidence.get("captured_source_value"), (dict, list, bool))
        and not isinstance(evidence.get("captured_target_value"), (dict, list, bool))
        and str(evidence.get("captured_source_value")).strip()
        == str(evidence.get("captured_target_value")).strip()
        and str(evidence.get("captured_target_value")).strip()
        == str(target_param.value if target_param is not None else "").strip()
    )
    hydration_override = bool(
        evidence.get("kind") == "record_hydration"
        and evidence.get("value_overridden") is True
        and source_leaf == target_leaf
    )
    hydration_empty = bool(
        evidence.get("kind") == "record_hydration"
        and evidence.get("empty_projection") is True
        and source_leaf == target_leaf
    )
    if target_param is None or not (
        _recorded_scalar_values_match(source_value, target_param.value)
        or _composite_values_match(source_value, target_param.value)
        or hydration_match
        or hydration_override
        or hydration_empty
    ):
        return False
    source_action = str(evidence.get("source_action_id") or "")
    target_action = str(evidence.get("target_action_id") or "")
    source_transaction = str((source.source_meta or {}).get("trigger_transaction_id") or "")
    target_transaction = str((target.source_meta or {}).get("trigger_transaction_id") or "")
    causal = bool(
        evidence.get("same_action_chain") is True
        or (source_action and source_action == target_action)
        or (source_transaction and source_transaction == target_transaction)
        or evidence.get("kind") in {
            "response_projection", "request_dependency", "causal_transaction", "explicit_projection",
            "record_hydration",
        }
    )
    separate_observed_operations = bool(
        (source_action and target_action and source_action != target_action)
        or (
            source_transaction
            and target_transaction
            and source_transaction != target_transaction
        )
    )
    source_leaf = re.sub(r"[^a-z0-9]+", "", source_path.split(".")[-1].casefold())
    target_leaf = re.sub(
        r"[^a-z0-9]+", "", str(target_param.path or target_param.key).split(".")[-1].casefold(),
    )
    captured_match = evidence.get("captured_value_match")
    stable_identifier_projection = bool(
        link.confirmed
        and float(link.confidence or 0.0) >= 0.95
        and isinstance(captured_match, dict)
        and int(captured_match.get("occurrences") or 0) == 1
        and source_leaf == "id"
        and target_leaf.endswith("id")
    )
    if separate_observed_operations and not (causal or stable_identifier_projection):
        return False
    scalar_envelope_projection = bool(
        source_path in {"data", "result", "value"}
        and not isinstance(source_value, (dict, list))
    )
    structural_projection = bool(
        not _param_has_editable_control_evidence(target_param)
        and (
            source_leaf == target_leaf
            or (
                target_leaf.endswith("id")
                and source_leaf == "id"
            )
            or scalar_envelope_projection
        )
    )
    return causal or stable_identifier_projection or structural_projection


def _prune_unsafe_auto_links(steps: list[FlowStep], links: list[FlowLink]) -> None:
    by_id = {s.step_id: s for s in steps}
    kept: list[FlowLink] = []
    for lk in links:
        if (lk.meta or {}).get("unverified_reason") and _link_is_auto_generated(lk):
            continue
        if not _link_is_auto_generated(lk):
            kept.append(lk)
            continue
        if not _auto_link_has_grounded_contract(steps, lk):
            continue
        target = by_id.get(lk.target_step_id)
        param = _resolve_param_reference(target, lk.target_path) if target else None
        if _auto_dependency_link_allowed(param, lk.source_path, lk):
            kept.append(lk)
    links[:] = kept


def _flow_link_kind(link: FlowLink) -> str:
    return str(link.kind or "value")


def _sync_link_sources(steps: list[FlowStep], links: list[FlowLink]) -> None:
    _prune_unsafe_auto_links(steps, links)
    by_id = {step.step_id: step for step in steps}
    valid_targets = {
        (lk.link_id, lk.target_step_id, target_param.path)
        for lk in links
        if _flow_link_kind(lk) == "value"
        if (target := by_id.get(lk.target_step_id)) is not None
        if (target_param := _resolve_param_reference(target, lk.target_path)) is not None
    }
    for st in steps:
        for p in st.params:
            if p.source_kind != "previous_response":
                continue
            link_id = p.source.get("link_id")
            if not link_id:
                # An explicitly declared but incomplete response source is an
                # advisory contract problem; do not silently erase it.
                continue
            if (link_id, st.step_id, p.path) in valid_targets:
                continue
            _reset_param_source(p, reason="上游依赖已删除或目标已改变，字段已恢复为用户输入")
    _apply_link_sources(steps, links)


def _merge_flow_read_sources(explicit_reads: list[dict], captured_requests: list[dict], request_roles: list[dict]) -> list[dict]:
    """把录制全量请求里的读响应也作为字段候选源。

    recorder 现在会把 GET/POST 查询放进 captured_requests；字段下拉/选人绑定不能只依赖旧 reads 通道。
    """
    out: list[dict] = []
    merged_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def add(url: str, payload: Any, *, role: str = "", source: dict | None = None,
            sequence: int | None = None) -> None:
        if payload is None:
            return
        source = source or {}
        source_sequence = next((
            source.get(key) for key in ("sequence", "request_index", "index")
            if source.get(key) is not None
        ), sequence)
        source_request_index = next((
            source.get(key) for key in ("request_index", "index")
            if source.get(key) is not None
        ), source_sequence)
        request_id = str(source.get("request_id") or "")
        page_id = str(source.get("page_id") or "")
        frame_id = str(source.get("frame_id") or "")
        payload_fingerprint = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        # ``reads`` and ``captured_requests`` often contain two projections of
        # the same network request.  Merge only when their immutable request id
        # (or recorder sequence fallback) agrees.  Two identical GET responses
        # observed before and after a write are distinct causal events and must
        # not be collapsed merely because URL/body/page are equal.
        identity = (
            f"request:{request_id}" if request_id
            else f"sequence:{source_sequence}" if source_sequence is not None
            else page_id
        )
        key = (
            url or "",
            payload_fingerprint,
            identity,
            "" if request_id or source_sequence is not None else frame_id,
        )
        existing = merged_by_key.get(key)
        incoming = {
            "url": url or "",
            "json": payload,
            "role": role or "",
            "page_id": page_id,
            "frame_id": frame_id,
            "trigger_action_id": str(source.get("trigger_action_id") or source.get("action_id") or ""),
            "trigger_transaction_id": str(source.get("trigger_transaction_id") or ""),
            "request_id": request_id,
            "request_index": source_request_index,
            "sequence": source_sequence,
            "path": _request_path(source) if source else _request_path({"url": url}),
        }
        if existing is not None:
            # The captured-request projection carries the classifier result and
            # action/transaction anchors that the lightweight response-read
            # projection may lack.  Fill/replace metadata without duplicating
            # the response payload.
            for field in (
                "role", "page_id", "frame_id", "trigger_action_id",
                "trigger_transaction_id", "request_id", "request_index",
                "sequence", "path",
            ):
                value = incoming.get(field)
                if value not in (None, ""):
                    existing[field] = value
            return
        merged_by_key[key] = incoming
        out.append(incoming)

    for r in explicit_reads or []:
        add(
            r.get("url") or "",
            r.get("json", r.get("response_json")),
            role=str(r.get("role") or r.get("request_role") or "explicit_read_option"),
            source=r,
        )
    for sequence, (req, role) in enumerate(zip(captured_requests or [], request_roles or [])):
        payload = req.get("response_json", req.get("json"))
        is_reference_read = (
            str(req.get("method") or "GET").upper() in {"GET", "HEAD"}
            and _list_payload_has_reference_contract(payload)
        )
        if (
            role.get("role") not in {"read_option", "read_context", "business_get"}
            and not is_reference_read
        ):
            continue
        add(
            req.get("url") or "",
            payload,
            role=str(role.get("role") or ""),
            source=req,
            sequence=sequence,
        )
    return out


def _discover_record_hydration_links(
    captured_requests: list[dict[str, Any]],
    target_request_ids: set[str],
) -> list[dict[str, Any]]:
    """Find a record read whose object is copied into a later write form."""
    identity_keys = {
        "id", "recordid", "requestid", "applicationid", "businessid",
        "entityid", "itemid",
    }
    candidates_by_target: dict[str, list[dict[str, Any]]] = {}
    for target in captured_requests:
        target_id = str(target.get("request_id") or "")
        if target_id not in target_request_ids:
            continue
        target_body = _parse_body(target.get("post_data"))
        if not isinstance(target_body, dict):
            continue
        target_values = {
            path: raw
            for path, _tokens, _scalar, raw in _leaf_paths(target_body)
            if not isinstance(raw, (dict, list, bool))
        }
        if not target_values:
            continue
        for source in captured_requests:
            if (
                str(source.get("method") or "GET").upper() not in {"GET", "HEAD"}
                or not _request_precedes(source, target)
            ):
                continue
            if any(
                str(source.get(key) or "")
                and str(target.get(key) or "")
                and str(source.get(key)) != str(target.get(key))
                for key in ("page_id", "frame_id")
            ):
                continue
            response = source.get("response_json")
            if not isinstance(response, dict):
                continue
            payload = response
            prefix = ""
            for envelope in ("data", "result"):
                if isinstance(response.get(envelope), dict):
                    payload = response[envelope]
                    prefix = f"{envelope}."
                    break
            matches: list[dict[str, Any]] = []
            for path, _tokens, _scalar, raw in _leaf_paths(payload):
                if path not in target_values or isinstance(raw, (dict, list, bool)):
                    continue
                target_raw = target_values[path]
                if isinstance(target_raw, (dict, list, bool)):
                    continue
                source_empty = raw in (None, "")
                target_empty = target_raw in (None, "")
                equal = (
                    source_empty and target_empty
                ) or (
                    not source_empty
                    and not target_empty
                    and (
                        _recorded_scalar_values_match(raw, target_raw)
                        or _composite_values_match(raw, target_raw)
                    )
                )
                matches.append({
                    "source_path": f"{prefix}{path}" if path else prefix.rstrip("."),
                    "target_path": path,
                    "source_value": copy.deepcopy(raw),
                    "target_value": copy.deepcopy(target_raw),
                    "value_overridden": not equal and not (source_empty and target_empty),
                    "empty_projection": source_empty and target_empty,
                })
            identity_paths = [
                item["target_path"] for item in matches
                if re.sub(
                    r"[^a-z0-9]+", "",
                    item["target_path"].split(".")[-1].casefold(),
                ) in identity_keys
            ]
            if len(matches) < 3 or not identity_paths:
                continue
            candidates_by_target.setdefault(target_id, []).append({
                "source_request_id": str(source.get("request_id") or ""),
                "target_request_id": target_id,
                "matches": matches,
                "identity_paths": identity_paths,
                "source_order": _request_order_value(source),
            })
    selected: list[dict[str, Any]] = []
    for candidates in candidates_by_target.values():
        selected.append(max(
            candidates,
            key=lambda item: (len(item["matches"]), item["source_order"]),
        ))
    return selected


def _samples_for_captured_request(
    request: dict,
    *,
    samples: dict | None = None,
    form_samples_by_request: dict | None = None,
    form_samples_by_transaction: dict | None = None,
) -> dict:
    request_id = str(request.get("request_id") or "")
    if form_samples_by_request and request_id:
        extra = form_samples_by_request.get(request_id)
        if extra:
            return dict(extra)
    tx = _request_transaction_id(request)
    action = str(request.get("trigger_action_id") or "")
    if form_samples_by_transaction:
        extra = (
            form_samples_by_transaction.get(tx)
            or form_samples_by_transaction.get(action)
        )
        if extra:
            return dict(extra)
    return dict(samples or {})


def to_flow_spec(
    captured_requests: list[dict],
    *,
    reads: list[dict] | None = None,
    samples: dict | None = None,
    storage_state: dict | None = None,
    required_labels: set | None = None,
    page_enum_options: dict | None = None,
    field_evidence: list[dict] | None = None,
    page_context: dict | None = None,
    recording_mode: str = "",
    diagnostics: list[dict] | None = None,
    page_events: list[dict] | None = None,
    tenant: str = "",
    subsystem: str = "",
    request_role_overrides: dict[str, dict[str, Any]] | None = None,
    form_samples_by_request: dict | None = None,
    form_samples_by_transaction: dict | None = None,
) -> FlowSpec:
    """收敛：把 record_ws 现有产物 → FlowSpec（包含 GET 业务请求）。"""
    reads = reads or []
    samples = samples or {}
    required_labels = required_labels or set()
    page_enum_options = page_enum_options or {}
    page_context = page_context or {}
    diagnostics = diagnostics or []
    page_events = page_events or []
    recording_mode = recording_mode or "unknown"
    request_role_overrides = request_role_overrides or {}

    request_roles = []
    for request in captured_requests:
        if looks_like_auth_write(
            request.get("url") or request.get("path") or "",
            request.get("post_data"),
        ):
            request_roles.append(
                classify_network_request(request, captured_requests, samples)
            )
            continue
        recorded = request.get("_request_role") if isinstance(request.get("_request_role"), dict) else None
        if recorded is None and request.get("role"):
            recorded = {
                "role": request.get("role"),
                "keep": request.get("keep"),
                "reason": request.get("reason") or request.get("keep_reason") or "",
                "confidence": request.get("confidence") or 0.0,
                "evidence": request.get("evidence") or {},
            }
        request_id = str(request.get("request_id") or "")
        override = request_role_overrides.get(request_id)
        request_roles.append(dict(override) if isinstance(override, dict) else (
            recorded or classify_network_request(request, captured_requests, samples)
        ))
    # A visible user command is stronger public-boundary evidence than a
    # generic ``read_context`` classification. Preserve those reads as
    # callable business operations; background context and option traffic are
    # unchanged because they have no matching command anchor.
    for index, (request, role) in enumerate(zip(captured_requests, request_roles)):
        if (
            str((role or {}).get("role") or "") == "read_context"
            and _has_query_action_evidence(
                request.get("trigger_op"),
                " ".join(filter(None, (
                    str(request.get("trigger_action_id") or ""),
                    str(request.get("trigger_locator") or ""),
                ))),
            )
        ):
            request_roles[index] = {
                **dict(role or {}),
                "role": "business_get",
                "keep": True,
                "confidence": max(float((role or {}).get("confidence") or 0.0), 0.9),
            }
    # Bind DOM facts once, before any field projection.  All later naming,
    # required and enum logic consumes this same explicit identity result.
    observer_events_present = any(
        isinstance(event, dict) and event.get("event_id") and event.get("kind")
        for event in page_events
    )
    if observer_events_present and field_evidence:
        binding_requests = []
        for request, role in zip(captured_requests, request_roles):
            binding_request = dict(request)
            binding_request["request_id"] = _request_fact_key(_request_fact_entry(request, role))
            binding_requests.append(binding_request)
        from dano.execution.page.recording_field_identity import bind_field_evidence

        field_evidence = bind_field_evidence(
            binding_requests, page_events, field_evidence,
            page_enum_options=page_enum_options,
        )
    role_by_key = {_request_role_key(r): role for r, role in zip(captured_requests, request_roles)}
    flow_reads = _merge_flow_read_sources(reads, captured_requests, request_roles)

    # 1) 业务写请求
    write_cands = _dedupe_request_identities([
        c for c in write_requests(captured_requests)
        if (role_by_key.get(_request_role_key(c), {}).get("keep")
            and role_by_key.get(_request_role_key(c), {}).get("role") in {"submit_anchor", "business_write"})
    ])
    selected_write_request_ids = {
        str(request.get("request_id") or "") for request in write_cands if request.get("request_id")
    }
    record_hydration_candidates = _discover_record_hydration_links(
        captured_requests, selected_write_request_ids,
    )
    machine_preflight_request_ids: set[str] = set()
    machine_preflight_request_ids.update(
        str(candidate.get("source_request_id") or "")
        for candidate in record_hydration_candidates
        if candidate.get("source_request_id")
    )
    for candidate in discover_response_key_maps(captured_requests):
        if str(candidate.get("target_request_id") or "") in selected_write_request_ids:
            machine_preflight_request_ids.add(str(candidate.get("source_request_id") or ""))
    # Complete the control chain upstream: a strong response value can feed the
    # request that later produces the dynamic request-key collection.
    strong_value_candidates = discover_workflow_value_links(captured_requests)
    changed = True
    while changed:
        changed = False
        for candidate in strong_value_candidates:
            if str(candidate.get("target_request_id") or "") not in machine_preflight_request_ids:
                continue
            source_request_id = str(candidate.get("source_request_id") or "")
            if source_request_id and source_request_id not in machine_preflight_request_ids:
                machine_preflight_request_ids.add(source_request_id)
                changed = True
    # 2) 前置读候选：business_get 直接进入候选；存在写锚点时，把 read_context
    # 也交给后续数据/控制依赖闭包判断。候选源仍完整保存在 request_facts，只有
    # 进入执行闭包的读取才物化为 FlowStep。
    preread_cands = [
        r for r in captured_requests
        if (
            role_by_key.get(_request_role_key(r), {}).get("role") == "business_get"
            or (
                bool(write_cands)
                and role_by_key.get(_request_role_key(r), {}).get("role") == "read_context"
            )
            or str(r.get("request_id") or "") in machine_preflight_request_ids
        )
    ]
    preread_before_dedupe = len(preread_cands)
    preread_cands = _dedupe_preread_candidates(preread_cands)

    if not write_cands and not preread_cands:
        request_facts = _build_request_facts(
            captured_requests,
            request_roles,
            set(),
            diagnostics=diagnostics,
            page_enum_options=page_enum_options,
            page_events=page_events,
            field_evidence=field_evidence,
        )
        empty_spec = FlowSpec(
            tenant=tenant,
            subsystem=subsystem,
            title="(未捕获到业务请求)",
            recording_mode=recording_mode,
            diagnostics=diagnostics,
            request_facts=request_facts,
            goal=RecordedGoal(
                intent="录制业务请求",
                required_inputs=[],
                success_criteria=["重新录制后捕获至少一个业务 GET 或写请求"],
                output_expectation=["生成可编辑 FlowSpec"],
                forbidden_actions=list(_DEFAULT_RECORDED_FORBIDDEN_ACTIONS),
                risk_level="L1",
                capabilities=[],
            ).model_dump(),
            meta={
                "captured_total": len(captured_requests),
                "captured_write_candidates": 0,
                "reads_count": len(flow_reads),
                "request_roles": request_roles,
                "recording_mode": recording_mode,
                "diagnostics": diagnostics,
                "page_events_count": len(page_events),
                "page_context": page_context,
                "note": "录制未抓到任何业务写请求或业务 GET；用户可能未点提交，或页面是纯 GET 表单",
            },
        )
        return ensure_flow_version(refresh_review_items(empty_spec), "recorded", reason="录制生成空 FlowSpec")

    # 3) Materialize every retained business write. Action/transaction evidence
    # decides capability membership later; it must never erase a captured fact.
    write_idxs = list(range(len(write_cands)))

    selected_write_keys = {_request_role_key(write_cands[i]) for i in write_idxs if 0 <= i < len(write_cands)}
    # A command transaction is stronger boundary evidence than the request-role
    # classifier.  Frameworks often issue an auxiliary GET/POST-read from the
    # same click (permission check, detail lookup, workflow preflight, etc.).
    # Keeping only requests classified as ``business_get`` made such interfaces
    # disappear from the capability even though Observer recorded them as part
    # of the operation.  Admit JSON/XHR-like reads from the same transaction;
    # static resources, navigation and unsupported/auth traffic remain excluded.
    selected_transactions = {
        transaction
        for request in write_cands
        if _request_role_key(request) in selected_write_keys
        if (transaction := _request_transaction_id(request))
    }
    operation_reads: list[dict] = []
    post_write_read_keys: set[Any] = set()
    for request, role_info in zip(captured_requests, request_roles):
        if not selected_transactions or _request_transaction_id(request) not in selected_transactions:
            continue
        if _request_role_key(request) in selected_write_keys or bool(request.get("navigation_request")):
            continue
        resource_type = str(request.get("resource_type") or "").lower()
        if resource_type in {"document", "stylesheet", "image", "media", "font", "script"}:
            continue
        method = str(request.get("method") or "GET").upper()
        role = str((role_info or {}).get("role") or "")
        json_like = bool(
            request.get("response_json") is not None
            or "json" in str(request.get("content_type") or "").lower()
            or resource_type in {"fetch", "xhr", "xmlhttprequest"}
        )
        if method not in {"GET", "HEAD", "POST"} or not json_like:
            continue
        if role in {"auth", "unsupported_upload", "unsupported_graphql"}:
            continue
        transaction_writes = [
            write for write in write_cands
            if _request_role_key(write) in selected_write_keys
            and _request_transaction_id(write) == _request_transaction_id(request)
        ]
        if transaction_writes and not any(
            _request_precedes(request, write) for write in transaction_writes
        ):
            # A refresh/list request emitted after a write is an observation of
            # the result, not a preflight dependency of that write.
            post_write_read_keys.add(_request_role_key(request))
            continue
        operation_reads.append(request)
    if operation_reads:
        preread_cands = _dedupe_preread_candidates([*preread_cands, *operation_reads])
    preread_keys = {_request_role_key(r) for r in preread_cands}
    potential_keys = selected_write_keys | preread_keys
    potential_steps = _dedupe_request_identities([
        r for r in captured_requests if _request_role_key(r) in potential_keys
    ])
    # Build explicit preflight ownership per write request. A boolean
    # "preflight" flag cannot represent a recording containing multiple forms:
    # shared BPM endpoints would otherwise be copied into every submit ability.
    write_positions = {
        idx for idx, req in enumerate(potential_steps)
        if _request_role_key(req) in selected_write_keys
    }
    owners_by_position: dict[int, set[int]] = {
        position: {position} for position in write_positions
    }
    write_context_by_position = {
        position: _workflow_context_values_for_request(potential_steps[position])
        for position in write_positions
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
        request_context = _workflow_context_values_for_request(request)
        request_transaction = _request_transaction_id(request)
        for write_position in write_positions:
            write_request = potential_steps[write_position]
            write_transaction = _request_transaction_id(write_request)
            context_match = any(
                same_workflow_context(candidate, write_value)
                for candidate in request_context
                for write_value in write_context_by_position.get(write_position, set())
            )
            same_transaction = bool(
                request_transaction
                and request_transaction == write_transaction
            )
            # When both sides have Observer transaction IDs, they are the
            # authoritative operation boundary. Semantic context is only the
            # fallback for older/incomplete recordings.
            if (same_transaction and _request_precedes(request, write_request)) or (
                context_match and not (request_transaction and write_transaction)
                and _request_precedes(request, write_request)
            ):
                owners_by_position.setdefault(idx, set()).add(write_position)
    position_by_request_id = {
        str(request.get("request_id") or ""): index
        for index, request in enumerate(potential_steps)
        if request.get("request_id")
    }
    for candidate in record_hydration_candidates:
        source_pos = position_by_request_id.get(str(candidate.get("source_request_id") or ""))
        target_pos = position_by_request_id.get(str(candidate.get("target_request_id") or ""))
        if (
            source_pos is not None
            and target_pos in write_positions
            and _request_role_key(potential_steps[source_pos]) in preread_keys
        ):
            owners_by_position.setdefault(source_pos, set()).add(target_pos)
    # Exact response-row keys matching a later request object is machine
    # evidence that the read controls the write's request shape. Keep that
    # source in the preflight closure so Pi can propose and verify the richer
    # response_key_map contract instead of freezing recorded dynamic keys.
    for candidate in discover_response_key_maps(captured_requests):
        source_pos = position_by_request_id.get(str(candidate.get("source_request_id") or ""))
        target_pos = position_by_request_id.get(str(candidate.get("target_request_id") or ""))
        if (
            source_pos is not None
            and target_pos in write_positions
            and _request_role_key(potential_steps[source_pos]) in preread_keys
            and _request_precedes(potential_steps[source_pos], potential_steps[target_pos])
        ):
            owners_by_position.setdefault(source_pos, set()).add(target_pos)
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
            if not isinstance(source_pos, int) or not isinstance(target_pos, int):
                continue
            target_owners = owners_by_position.get(target_pos, set())
            if (
                target_owners
                and 0 <= source_pos < len(potential_steps)
                and _request_role_key(potential_steps[source_pos]) in preread_keys
            ):
                before = len(owners_by_position.get(source_pos, set()))
                owners_by_position.setdefault(source_pos, set()).update(target_owners)
                changed = changed or len(owners_by_position[source_pos]) != before
            # 控制前置链：某个已选 workflow GET 的响应驱动后续 workflow GET query
            # 时，二者共同属于写能力前置闭包，即使后者响应不直接进入 POST body。
            if (
                owners_by_position.get(source_pos)
                and isinstance(target_pos, int)
                and 0 <= target_pos < len(potential_steps)
                and _request_role_key(potential_steps[target_pos]) in preread_keys
            ):
                before = len(owners_by_position.get(target_pos, set()))
                owners_by_position.setdefault(target_pos, set()).update(owners_by_position[source_pos])
                changed = changed or len(owners_by_position[target_pos]) != before
    required_positions = set(owners_by_position)
    selected_preread_keys = {
        _request_role_key(potential_steps[idx])
        for idx in required_positions
        if 0 <= idx < len(potential_steps) and _request_role_key(potential_steps[idx]) in preread_keys
    }
    preflight_owner_request_keys = {
        _request_role_key(potential_steps[position]): {
            _request_role_key(potential_steps[owner])
            for owner in owners
            if owner in write_positions
        }
        for position, owners in owners_by_position.items()
        if position not in write_positions and owners
    }
    observer_command_anchors = any(
        _request_has_command_anchor(request) for request in captured_requests
    )
    explicitly_approved_business_keys = {
        _request_role_key(request)
        for request in captured_requests
        if (
            isinstance(request_role_overrides.get(str(request.get("request_id") or "")), dict)
            and request_role_overrides[str(request.get("request_id") or "")].get("role") == "business_get"
            and request_role_overrides[str(request.get("request_id") or "")].get("keep") is True
        )
    }
    independent_business_keys = {
        _request_role_key(request)
        for request in preread_cands
        if str((role_by_key.get(_request_role_key(request)) or {}).get("role") or "") == "business_get"
        and _request_role_key(request) not in post_write_read_keys
        and (
            _request_role_key(request) in explicitly_approved_business_keys
            # A visible click alone is not enough to publish a read as an
            # independent capability: opening a create/edit form commonly
            # loads workflow definitions, approval nodes and other support
            # data.  Query-like commands remain public below; support reads
            # are materialized only when the dependency closure proves that
            # a selected write consumes them.
            or _has_query_action_evidence(
                request.get("trigger_op"),
                " ".join(filter(None, (
                    str(request.get("trigger_action_id") or ""),
                    str(request.get("trigger_locator") or ""),
                ))),
            )
            or (
                not _request_has_command_anchor(request)
                and _request_transaction_id(request)
                and _business_filter_count(request) > 0
                and _BUSINESS_QUERY_PATH_RE.search(_request_path(request))
                and float((role_by_key.get(_request_role_key(request)) or {}).get("confidence") or 0.0) >= 0.9
            )
            or (
                not observer_command_anchors
                and float((role_by_key.get(_request_role_key(request)) or {}).get("confidence") or 0.0) >= 0.9
            )
        )
    }
    for index, (request, role_info) in enumerate(zip(captured_requests, request_roles)):
        request_key = _request_role_key(request)
        stable_role = dict(role_info or {})
        if request_key in selected_write_keys and stable_role.get("role") == "submit_anchor":
            stable_role["role"] = "business_write"
        if request_key in preflight_owner_request_keys:
            stable_role.update({
                # Option identity is orthogonal to preflight ownership.  A
                # candidate endpoint can belong to the same command while it
                # still remains an option source rather than a context read.
                "role": (
                    "read_option"
                    if stable_role.get("role") == "read_option"
                    else "read_context"
                ),
                "keep": True,
                "filter_reason": "",
                "confidence": max(float(stable_role.get("confidence") or 0.0), 0.9),
            })
        request_roles[index] = stable_role
    role_by_key = {
        _request_role_key(request): role
        for request, role in zip(captured_requests, request_roles)
    }
    fact_check_read_keys: set[Any] = set()
    for write_request in write_cands:
        write_samples = dict(samples)
        body = _parse_body(write_request.get("post_data"))
        if body is not None:
            for path, tokens, _string_value, raw_value in _leaf_paths(body):
                key = str(tokens[-1]) if tokens else path
                write_samples.setdefault(key, raw_value)
        fact_check = suggest_fact_check(
            write_samples,
            flow_reads,
            write_request=write_request,
        )
        source_request_id = str((fact_check or {}).get("source_request_id") or "")
        source_sequence = (fact_check or {}).get("source_sequence")
        source_endpoint = str((fact_check or {}).get("endpoint") or "")
        source_request = next((
            request for request in captured_requests
            if (
                source_request_id
                and str(request.get("request_id") or "") == source_request_id
            ) or (
                not source_request_id
                and source_sequence is not None
                and _request_order_value(request) == float(source_sequence)
                and str(request.get("url") or request.get("path") or "") == source_endpoint
            )
        ), None)
        if source_request is not None:
            fact_check_read_keys.add(_request_role_key(source_request))
    # A same-transaction read emitted after a write is not a preflight, but it
    # is still an executable verification step. Keep it materialized so the
    # owning write can bind fact_check without moving that assertion onto the
    # read itself.
    selected_keys = (
        selected_write_keys
        | selected_preread_keys
        | independent_business_keys
        | fact_check_read_keys
    )
    request_facts = _build_request_facts(
        captured_requests,
        request_roles,
        selected_keys,
        diagnostics=diagnostics,
        page_enum_options=page_enum_options,
        page_events=page_events,
        field_evidence=field_evidence,
    )
    cands = _dedupe_request_identities([
        r for r in captured_requests if _request_role_key(r) in selected_keys
    ])

    # 4) 每条 → FlowStep
    step_objs: list[FlowStep] = []
    idx_to_step_id: dict[int, str] = {}
    step_by_request_key: dict[Any, FlowStep] = {}
    for pos, req in enumerate(cands):
        request_role = role_by_key.get(_request_role_key(req), {})
        st = _build_step_from_capture(
            _attach_request_role(req, request_role),
            reads=flow_reads,
            samples=_samples_for_captured_request(
                req,
                samples=samples,
                form_samples_by_request=form_samples_by_request,
                form_samples_by_transaction=form_samples_by_transaction,
            ),
            storage_state=storage_state,
            required_labels=required_labels,
            page_enum_options=page_enum_options,
            field_evidence=field_evidence,
            step_index=pos,
        )
        if _request_role_key(req) in selected_preread_keys:
            st.source_meta = {
                **(st.source_meta or {}),
                "control_preflight_for_write": True,
            }
        if _request_role_key(req) in fact_check_read_keys:
            st.source_meta = {
                **(st.source_meta or {}),
                "verification_read": True,
                "actor": "heuristic",
            }
        step_objs.append(st)
        step_by_request_key[_request_role_key(req)] = st
        idx_to_step_id[pos] = st.step_id
        request_id = _request_fact_key(_request_fact_entry(req, request_role))
        usage = request_facts.usage.get(request_id) or RequestUsage(request_id=request_id)
        usage.materialized_step_id = st.step_id
        usage.state = "materialized"
        request_facts.usage[request_id] = usage
    step_id_by_request_id = {
        str((step.source_meta or {}).get("request_id") or ""): step.step_id
        for step in step_objs
        if str((step.source_meta or {}).get("request_id") or "")
    }
    for item in getattr(request_facts, "field_evidence", []) or []:
        if not isinstance(item, dict) or item.get("binding_status") != "bound":
            continue
        step_id = step_id_by_request_id.get(str(item.get("request_id") or ""))
        if step_id:
            item["step_id"] = step_id
    for request_key, owner_request_keys in preflight_owner_request_keys.items():
        step = step_by_request_key.get(request_key)
        if step is None:
            continue
        owner_step_ids = [
            owner.step_id
            for owner_key in owner_request_keys
            if (owner := step_by_request_key.get(owner_key)) is not None
        ]
        step.source_meta = {
            **(step.source_meta or {}),
            "control_preflight_for_write": bool(owner_step_ids),
            "control_preflight_for_write_ids": owner_step_ids,
        }
    hydration_owner_ids: dict[str, list[str]] = {}
    for candidate in record_hydration_candidates:
        source_request_id = str(candidate.get("source_request_id") or "")
        target_step_id = step_id_by_request_id.get(
            str(candidate.get("target_request_id") or ""),
        )
        if source_request_id and target_step_id:
            hydration_owner_ids.setdefault(source_request_id, []).append(target_step_id)
    for source_request_id, owner_step_ids in hydration_owner_ids.items():
        source_step_id = step_id_by_request_id.get(source_request_id)
        source_step = next((
            step for step in step_objs if step.step_id == source_step_id
        ), None)
        if source_step is not None:
            source_step.source_meta = {
                **(source_step.source_meta or {}),
                "record_hydration_for_write_ids": list(dict.fromkeys(owner_step_ids)),
            }
            identity_leaves = {
                re.sub(
                    r"[^a-z0-9]+", "", str(path).split(".")[-1].casefold(),
                )
                for candidate in record_hydration_candidates
                if str(candidate.get("source_request_id") or "") == source_request_id
                for path in candidate.get("identity_paths") or []
            }
            for param in source_step.params or []:
                leaf = re.sub(
                    r"[^a-z0-9]+", "",
                    str(param.path or param.key or "").split(".")[-1].casefold(),
                )
                if (
                    not str(param.path or "").startswith("query.")
                    or leaf not in identity_leaves
                    or param.locked
                    or _param_has_manual_contract(param)
                ):
                    continue
                param.category = "user_param"
                param.source_kind = "user_input"
                param.source = {
                    "kind": "selected_record_identity",
                    "path": param.path,
                    "required_state": "required",
                }
                param.required = True
                param.exposed_to_user = True
                param.editable = True
                param.need_human_confirm = False
                param.reason = "调用方提供要编辑的记录标识；详情接口据此读取其当前字段"
                source_step.sample_inputs[param.key] = param.value

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
                source_request = cands[src_pos]
                target_request = cands[tgt_pos]
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
                selected_source = select_dependency_source(
                    matching_sources,
                    target_path=target_path,
                    target_method=str(target_request.get("method") or "GET"),
                    target_route=str(
                        target_request.get("url") or target_request.get("path") or ""
                    ),
                )
                current_source = (
                    str(source_request.get("method") or "GET").upper(),
                    _request_path(source_request),
                    str(lk.get("source_path") or "").removeprefix("response."),
                )
                strong_unique_match = (
                    len(target_value) >= 4
                    and selected_source == current_source
                    and target_value.lower() not in {"true", "false", "null", "none", "success"}
                )
                source_leaf = re.sub(
                    r"[^a-z0-9]+", "", str(lk.get("source_path") or "").split(".")[-1].lower()
                )
                target_leaf = re.sub(
                    r"[^a-z0-9]+", "", str(target_path or "").split(".")[-1].lower()
                )
                strong_id_dependency = strong_unique_match and source_leaf == "id" and target_leaf.endswith("id")
                source_action = str(source_request.get("trigger_action_id") or "")
                target_action = str(target_request.get("trigger_action_id") or "")
                causal_supported = bool(target_action)
                same_action_chain = bool(source_action and source_action == target_action)
                captured_dependency_match = bool(
                    strong_unique_match
                    and (
                        same_action_chain
                        # A repeated value may still be resolved across actions
                        # when route semantics select one source from multiple
                        # candidates.  A lone equal value across two unrelated
                        # observed actions remains insufficient evidence.
                        or len(matching_sources) > 1
                    )
                )
                editable_prefill_dependency = bool(
                    target_param is not None
                    and _param_has_editable_control_evidence(target_param)
                    and strong_unique_match
                    and same_action_chain
                    and _dependency_match_score(
                        target_param, str(lk.get("source_path") or "")
                    ) >= 40
                )
                if (
                    not strong_id_dependency
                    and not editable_prefill_dependency
                    and not _auto_dependency_link_allowed(
                        target_param, str(lk.get("source_path") or ""),
                    )
                ):
                    continue
                auto_confirmed = bool(strong_unique_match and (not page_events or causal_supported))
                link_objs.append(FlowLink(
                    source_step_id=idx_to_step_id[src_pos],
                    source_path=lk.get("source_path", ""),
                    source_tokens=lk.get("source_tokens"),
                    target_step_id=idx_to_step_id[tgt_pos],
                    target_path=lk.get("target_path", ""),
                    target_tokens=lk.get("target_tokens"),
                    param_name=None,
                    confirmed=auto_confirmed,
                    confidence=(0.98 if same_action_chain and strong_unique_match
                                else 0.96 if auto_confirmed else 0.9 if strong_unique_match else 0.85),
                    reason=(
                        "同一用户操作触发的请求链中，上游响应值与下游请求字段值唯一一致，判定为运行期依赖"
                        if same_action_chain else
                        "上游响应值与下游请求字段值唯一一致，且下游请求有操作锚点，判定为运行期依赖"
                        if auto_confirmed and page_events else
                        "上游响应值与下游请求字段值一致，但缺少操作因果锚点，保留为待确认依赖"
                        if strong_unique_match and page_events else
                        "上游响应值与下游请求字段值一致，判定为运行期依赖"
                    ),
                    evidence={
                        "source_step": src_pos,
                        "target_step": tgt_pos,
                        "source_path": lk.get("source_path", ""),
                        "target_path": lk.get("target_path", ""),
                        "source_action_id": source_action,
                        "target_action_id": target_action,
                        "same_action_chain": same_action_chain,
                        "observer_available": bool(page_events),
                        **({
                            "captured_value_match": {
                                "occurrences": 1,
                                "source_identity": list(selected_source or ()),
                                "disambiguation": "route_semantics",
                                "candidate_count": len(matching_sources),
                            },
                        } if captured_dependency_match else {}),
                    },
                    meta={
                        "actor": "heuristic",
                        "verified": False,
                        **({"captured_value_match": True} if captured_dependency_match else {}),
                    },
                ))
        except Exception:
            link_objs = []
    for candidate in record_hydration_candidates:
        source_step_id = step_id_by_request_id.get(
            str(candidate.get("source_request_id") or ""),
        )
        target_step_id = step_id_by_request_id.get(
            str(candidate.get("target_request_id") or ""),
        )
        if not source_step_id or not target_step_id:
            continue
        for match in candidate.get("matches") or []:
            source_path = str(match.get("source_path") or "")
            target_path = str(match.get("target_path") or "")
            signature = (source_step_id, source_path, target_step_id, target_path)
            existing_link = next((
                link for link in link_objs
                if (
                    link.source_step_id, link.source_path,
                    link.target_step_id, link.target_path,
                ) == signature
            ), None)
            evidence = {
                "kind": "record_hydration",
                "source_request_id": candidate.get("source_request_id"),
                "target_request_id": candidate.get("target_request_id"),
                "identity_paths": list(candidate.get("identity_paths") or []),
                "match_count": len(candidate.get("matches") or []),
                "captured_source_value": copy.deepcopy(match.get("source_value")),
                "captured_target_value": copy.deepcopy(match.get("target_value")),
                "value_overridden": bool(match.get("value_overridden")),
                "empty_projection": bool(match.get("empty_projection")),
            }
            meta = {
                "actor": "heuristic",
                "captured_record_hydration": True,
            }
            if existing_link is not None:
                existing_link.confirmed = True
                existing_link.confidence = max(float(existing_link.confidence or 0.0), 0.99)
                existing_link.reason = "同一记录详情对象的多个同名字段被后续写请求原样采用"
                existing_link.evidence = evidence
                existing_link.meta = {**dict(existing_link.meta or {}), **meta}
                continue
            link_objs.append(FlowLink(
                source_step_id=source_step_id,
                source_path=source_path,
                target_step_id=target_step_id,
                target_path=target_path,
                confirmed=True,
                confidence=0.99,
                reason="同一记录详情对象的多个同名字段被后续写请求原样采用",
                evidence=evidence,
                meta=meta,
            ))
    _materialize_captured_response_key_maps(
        step_objs, link_objs, captured_requests,
    )
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

    # 7) fact_check — capability/write scoped and causally after the write.
    # A value collision in a user/dictionary endpoint is not verification, and
    # a follow-up GET step must not own the write's check merely because it is
    # last in the workflow.
    for step in step_objs:
        step.fact_check = None
        if (step.method or "").upper() not in {"POST", "PUT", "PATCH"}:
            continue
        meta = step.source_meta or {}
        step_samples = dict(step.sample_inputs or {})
        if not step_samples:
            step_samples = {
                param.key: param.value
                for param in step.params
                if param.key and param.value not in (None, "") and _param_exposed_to_caller(param)
            }
        fc = suggest_fact_check(
            step_samples,
            flow_reads,
            write_request={
                "method": step.method,
                "url": step.url or step.path,
                "sequence": meta.get("sequence", meta.get("request_index")),
                "request_index": meta.get("request_index"),
                "trigger_transaction_id": meta.get("trigger_transaction_id"),
            },
        )
        if fc:
            step.fact_check = fc

    # 8) title
    title = _derive_title(step_objs, extra_contexts=[page_context])

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
        request_facts=request_facts,
        meta={
            "captured_total": len(captured_requests),
            "captured_write_candidates": len(write_cands),
            "captured_business_gets": len([r for r in request_roles if r.get("role") == "business_get"]),
            "captured_preread_candidates_before_dedupe": preread_before_dedupe,
            "captured_preread_candidates": len(preread_cands),
            "captured_workflow_steps": len(step_objs),
            "reads_count": len(flow_reads),
            "request_roles": request_roles,
            "recording_mode": recording_mode,
            "diagnostics": diagnostics,
            "page_events_count": len(page_events),
            "page_context": page_context,
            "field_evidence": list(field_evidence or []),
            "schema_version": 1,
        },
    )
    _mark_repeated_write_observations(spec)
    # ponytail: reuse the existing grounded matcher before the first projection.
    _repair_structural_option_bindings(spec)
    _apply_mechanical_field_contracts(spec)
    return ensure_flow_version(refresh_review_items(ensure_recorded_goal(spec)), "recorded", reason="录制生成 FlowSpec 初版")


def _latest_response_key_map_candidates(
    captured_requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Use the nearest captured source for each later dynamic request object."""
    ordered = sorted(
        enumerate(captured_requests or []),
        key=lambda item: (
            _request_sequence_value(
                item[1].get("sequence", item[1].get("request_index"))
            ) is None,
            _request_sequence_value(
                item[1].get("sequence", item[1].get("request_index"))
            ) or item[0],
            item[0],
        ),
    )
    position_by_request_id = {
        str(request.get("request_id") or ""): position
        for position, (_original_index, request) in enumerate(ordered)
        if str(request.get("request_id") or "")
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in discover_response_key_maps(captured_requests):
        signature = (
            str(candidate.get("target_request_id") or ""),
            _strip_body_prefix(str(candidate.get("target_container_path") or "")),
        )
        grouped.setdefault(signature, []).append(candidate)

    selected: list[dict[str, Any]] = []
    for candidates in grouped.values():
        nearest_position = max(
            position_by_request_id.get(str(item.get("source_request_id") or ""), -1)
            for item in candidates
        )
        selected.extend(
            item for item in candidates
            if position_by_request_id.get(str(item.get("source_request_id") or ""), -1)
            == nearest_position
        )
    return selected


def _materialize_captured_response_key_maps(
    steps: list[FlowStep],
    links: list[FlowLink],
    captured_requests: list[dict[str, Any]],
) -> None:
    """Turn exact response-row/request-key matches into executable contracts."""
    by_request_id = {
        str((step.source_meta or {}).get("request_id") or ""): step
        for step in steps
        if str((step.source_meta or {}).get("request_id") or "")
    }
    for candidate in _latest_response_key_map_candidates(captured_requests):
        source_request_id = str(candidate.get("source_request_id") or "")
        target_request_id = str(candidate.get("target_request_id") or "")
        source = by_request_id.get(source_request_id)
        target = by_request_id.get(target_request_id)
        if source is None or target is None:
            continue
        source_collection_path = str(candidate.get("source_collection_path") or "")
        source_key_path = str(candidate.get("source_key_path") or "")
        source_label_path = str(candidate.get("source_label_path") or "")
        target_container_path = _strip_body_prefix(
            str(candidate.get("target_container_path") or "")
        )
        collection = _flow_path_lookup(source.response_json, source_collection_path)
        try:
            recorded_body = (
                json.loads(target.body_source)
                if isinstance(target.body_source, str)
                else copy.deepcopy(target.body_source)
            )
        except (TypeError, ValueError):
            continue
        recorded_container = _flow_path_lookup(recorded_body, target_container_path)
        if not (
            isinstance(collection, list)
            and collection
            and all(isinstance(row, dict) for row in collection)
            and isinstance(recorded_container, dict)
            and recorded_container
        ):
            continue
        valid_rows = [
            row for row in collection
            if row.get(source_key_path) not in (None, "")
            and row.get(source_label_path) not in (None, "")
        ]
        rows_by_key = {
            str(row.get(source_key_path)): row
            for row in valid_rows
        }
        recorded_keys = [str(key) for key in recorded_container]
        if (
            len(rows_by_key) != len(valid_rows)
            or any(key not in rows_by_key for key in recorded_keys)
        ):
            continue
        matched_labels = [
            str(rows_by_key[key][source_label_path]) for key in recorded_keys
        ]
        if len(set(matched_labels)) != len(matched_labels):
            continue
        recorded_values = list(recorded_container.values())
        if all(isinstance(value, list) for value in recorded_values):
            value_shape = "item_list"
        elif all(not isinstance(value, (dict, list)) for value in recorded_values):
            value_shape = "direct"
        else:
            continue

        signature = (
            source.step_id, source_collection_path,
            target.step_id, target_container_path,
        )
        existing_link = next((
            link for link in links
            if (
                link.source_step_id, link.source_path,
                link.target_step_id, link.target_path,
            ) == signature
        ), None)
        existing_binding = dict(
            existing_link.value_binding or {}
        ) if existing_link is not None else {}
        # An agent-confirmed public alias is part of the caller contract.  The
        # capture repair may enrich its labels and value shape, but must not
        # replace that alias with the transport container name.
        input_field = str(
            existing_binding.get("input_field")
            or target_container_path.rsplit(".", 1)[-1]
        )
        dynamic_prefix = target_container_path + "."
        dynamic_paths = {
            str(param.path or "")
            for param in target.params
            if _strip_body_prefix(str(param.path or "")).startswith(dynamic_prefix)
        }
        if not dynamic_paths:
            continue
        option_bindings = [
            binding for binding in target.selects
            if str(binding.path or binding.id_path or "") in dynamic_paths
        ]
        option_sources = {
            (
                str(binding.source_request_id or ""),
                str(binding.value_key or ""),
                str(binding.label_key or ""),
            )
            for binding in option_bindings
            if binding.source_request_id and binding.value_key and binding.label_key
        }
        option_source = None
        if len(option_sources) == 1:
            request_id, value_path, label_path = next(iter(option_sources))
            option_source = {
                "request_id": request_id,
                "value_path": value_path,
                "label_path": label_path,
            }

        public_sample = dict(zip(matched_labels, recorded_values, strict=True))
        for param in target.params:
            if str(param.path or "") not in dynamic_paths:
                continue
            param.category = "runtime_var"
            param.source_kind = "dynamic_structure"
            param.source = {"kind": "dynamic_structure_leaf", "actor": "heuristic"}
            param.exposed_to_user = False
            param.editable = False
            param.required = False
            param.need_human_confirm = False
            target.sample_inputs.pop(str(param.key or param.path), None)
        target.selects = [
            binding for binding in target.selects
            if str(binding.path or binding.id_path or "") not in dynamic_paths
        ]
        public = next((
            param for param in target.params
            if _strip_body_prefix(str(param.path or "")) == target_container_path
        ), None)
        if public is None:
            public = ParamField(path=target_container_path, key=input_field)
            target.params.append(public)
        public.key = input_field
        public.label = public.label or input_field
        public.value = copy.deepcopy(public_sample)
        public.type = "object"
        public.wire_type = "object"
        public.required = True
        public.category = "user_param"
        public.source_kind = "user_input"
        public.source = {
            "kind": "dynamic_structure_input",
            "actor": "heuristic",
            "required_state": "required",
            **({"option_source": option_source} if option_source else {}),
        }
        public.exposed_to_user = True
        public.editable = True
        public.need_human_confirm = False
        public.reason = "调用方按上游返回的稳定标签提供值，运行期按最新响应键组装请求"
        public.evidence = [*list(public.evidence or []), {
            "source": "response_key_map",
            "actor": "heuristic",
            "source_request_id": source_request_id,
            "target_request_id": target_request_id,
            "wire_path": f"body.{target_container_path}",
            "labels": matched_labels,
        }]
        target.sample_inputs[input_field] = copy.deepcopy(public_sample)

        value_binding = {
            "kind": "caller_map_by_label",
            "input_field": input_field,
            "input_fields_by_label": {
                label: label for label in matched_labels
            },
            "value_shape": value_shape,
            "required_labels": matched_labels,
            "ignored_labels": [
                str(row[source_label_path])
                for row in collection
                if str(row[source_label_path]) not in set(matched_labels)
            ],
            **({"option_source": option_source} if option_source else {}),
        }
        if existing_link is not None:
            existing_link.value_binding = {
                **dict(existing_link.value_binding or {}),
                **value_binding,
            }
            continue
        links.append(FlowLink(
            source_step_id=source.step_id,
            source_path=source_collection_path,
            target_step_id=target.step_id,
            target_path=target_container_path,
            kind="response_key_map",
            source_collection_path=source_collection_path,
            source_key_path=source_key_path,
            source_label_path=source_label_path,
            target_container_path=target_container_path,
            value_binding=value_binding,
            confirmed=False,
            confidence=float(candidate.get("confidence") or 0.99),
            reason="录制响应行的稳定键与后续请求对象键精确一致",
            evidence={
                "kind": "response_key_map",
                "actor": "heuristic",
                "source_request_id": source_request_id,
                "target_request_id": target_request_id,
            },
            meta={"actor": "heuristic", "captured_structure_match": True},
        ))


def _derive_title(
    steps: list[FlowStep],
    extra_contexts: list[dict[str, Any]] | None = None,
) -> str:
    if not steps:
        return ""
    # The recorder already carries the page titles that were visible when an
    # operation was clicked.  They are stronger business evidence than an API
    # action suffix (``submit-process``, ``cancel-by-start-user`` and the like).
    # Prefer that evidence before exposing a transport path as the flow title.
    contexts: list[dict[str, Any]] = [
        dict(context)
        for context in (extra_contexts or [])
        if isinstance(context, dict) and context
    ]
    for step in steps:
        meta = step.source_meta or {}
        for key in ("trigger_page_context", "page_context"):
            value = meta.get(key)
            if isinstance(value, dict) and value:
                contexts.append(dict(value))
    page_business = _page_context_business_name_from_contexts(contexts)
    if page_business:
        return page_business
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


def _timestamp_is_near_request(value: Any, request: dict[str, Any] | None) -> bool:
    """True only when a captured timestamp is the request's own 'now'.

    Edit hydration reuses the record's create/update time. Treating that as
    ``now_ms`` because the field is named createTime overwrites the upstream
    value at replay.
    """
    actual = _date_like_epoch_seconds(value)
    if actual is None or not isinstance(request, dict):
        return False
    captured = _date_like_epoch_seconds(
        request.get("timestamp") or request.get("captured_at") or request.get("observed_at")
    )
    if captured is None:
        return False
    return abs(actual - captured) <= 120.0


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


def _as_finite_number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _numbers_match(expected: float, actual: float) -> bool:
    return abs(expected - actual) <= max(0.02, abs(expected) * 1e-6)


def _param_group_prefix(path: str) -> str:
    text = str(path or "")
    if text.startswith("body."):
        text = text[5:]
    return re.sub(r"(?:\.[^.\[\]]+|\[\d+\])$", "", text) if ("." in text or "[" in text) else ""


def _param_was_caller_typed(param: ParamField) -> bool:
    for item in param.evidence or []:
        if isinstance(item, dict) and item.get("kind") == "page_control" and item.get("interacted"):
            return True
    return False


_ARITHMETIC_STRATEGIES: tuple[tuple[str, Any, bool], ...] = (
    ("product", lambda left, right: left * right, True),
    ("sum", lambda left, right: left + right, True),
    ("difference", lambda left, right: left - right, False),
    ("percent_of", lambda left, right: left * right / 100.0, False),
    ("remainder_after_percent", lambda left, right: left * (1.0 - right / 100.0), False),
)
_IDENTITY_ARITHMETIC_EPS = 1e-9
_INPUT_OPERAND_KINDS = frozenset({
    "selected_option_field", "user_input",
})
_STABLE_OPERAND_KINDS = _INPUT_OPERAND_KINDS | frozenset({
    "computed", "previous_response", "page_default", "page_rule",
})


def _param_control_kinds(param: ParamField) -> set[str]:
    return {
        str(item.get("control_kind") or "").lower()
        for item in (param.evidence or [])
        if isinstance(item, dict) and item.get("kind") == "page_control"
    }


def _looks_non_quantity_formula_leaf(key: str, path: str) -> bool:
    """IDs, codes and state discriminators can numerically coincide with money."""
    leaf = _field_leaf_token(key, path)
    if leaf in {
        "id", "ids", "code", "no", "key", "status", "state", "type", "flag",
        "creator", "updater", "modifier", "owner", "assignee", "operator",
    }:
        return True
    return leaf.endswith(("id", "ids", "code", "key", "status", "state"))


def _looks_count_formula_leaf(key: str, path: str) -> bool:
    return _field_leaf_token(key, path) in {"count", "qty", "quantity", "num"}


def _looks_total_formula_leaf(key: str, path: str) -> bool:
    leaf = _field_leaf_token(key, path)
    return any(token in leaf for token in (
        "total", "amount", "subtotal", "payable", "linetotal", "discountprice",
    ))


def _looks_unit_price_formula_leaf(key: str, path: str) -> bool:
    """Unit/catalog prices are inputs or row echoes, not formula targets."""
    if _looks_total_formula_leaf(key, path) or _looks_percent_formula_leaf(key, path):
        return False
    leaf = _field_leaf_token(key, path)
    return any(token in leaf for token in ("price", "unitprice", "taxprice", "cost"))


def _looks_percent_formula_leaf(key: str, path: str) -> bool:
    leaf = _field_leaf_token(key, path)
    return any(token in leaf for token in ("percent", "rate", "ratio"))


def _param_is_quantity_or_formula_leaf(key: str, path: str) -> bool:
    """Qty/totals/rates are typed or computed, never option-row echoes."""
    return (
        _looks_count_formula_leaf(key, path)
        or _looks_total_formula_leaf(key, path)
        or _looks_percent_formula_leaf(key, path)
    )


def _is_numeric_formula_operand(param: ParamField) -> bool:
    """Selects, enums and record IDs can numerically coincide; they are not quantities."""
    if _looks_pagination_field(param.key, param.path):
        return False
    if param.source_kind in _OPTION_SOURCE_KINDS or param.type in {"enum", "list-enum"}:
        return False
    if _param_control_kinds(param) & {"select", "combobox", "radio"}:
        return False
    if _is_document_record_identity_path(param.key, param.path):
        return False
    if _looks_non_quantity_formula_leaf(param.key, param.path):
        return False
    if param.source_kind == "previous_response":
        return _as_finite_number(param.value) is not None
    return True


def _is_stable_operand(param: ParamField) -> bool:
    if not _is_numeric_formula_operand(param):
        return False
    if "number" in _param_control_kinds(param):
        return True
    if _param_was_caller_typed(param) or param.source_kind in _STABLE_OPERAND_KINDS:
        return True
    if (
        param.source_kind in {"", "unknown", "page_default"}
        and _as_finite_number(param.value) is not None
    ):
        return True
    return False


def _param_has_page_control_evidence(param: ParamField | None) -> bool:
    if param is None:
        return False
    return any(
        isinstance(item, dict) and item.get("kind") == "page_control"
        for item in param.evidence or []
    )


def _param_control_is_readonly(param: ParamField | None) -> bool:
    if param is None:
        return False
    return any(
        isinstance(item, dict)
        and item.get("kind") == "page_control"
        and (item.get("read_only") or item.get("disabled"))
        for item in param.evidence or []
    )


def _arithmetic_target_allowed(param: ParamField) -> bool:
    """Formulas hide derived numbers, never caller filters or typed controls."""
    if param.locked or _param_was_caller_typed(param):
        return False
    if str(param.path or "").startswith("query."):
        return False
    if _looks_pagination_field(param.key, param.path):
        return False
    if _param_is_document_record_identity(param):
        return False
    if _looks_non_quantity_formula_leaf(param.key, param.path):
        return False
    if _looks_unit_price_formula_leaf(param.key, param.path):
        return False
    if param.source_kind in _OPTION_SOURCE_KINDS or param.type in {"enum", "list-enum"}:
        return False
    if _param_control_kinds(param) & {"select", "combobox", "radio"}:
        return False
    if param.source_kind in {
        "computed", "selected_option_field", "current_user",
        "system_time", "system_generated",
    }:
        return False
    if (
        _param_has_editable_control_evidence(param)
        and not _param_control_is_readonly(param)
    ):
        return False
    return True


def _is_identity_arithmetic(kind: str, left: float, right: float) -> bool:
    if kind == "product":
        return (
            abs(left - 1.0) <= _IDENTITY_ARITHMETIC_EPS
            or abs(right - 1.0) <= _IDENTITY_ARITHMETIC_EPS
            or abs(left) <= _IDENTITY_ARITHMETIC_EPS
            or abs(right) <= _IDENTITY_ARITHMETIC_EPS
        )
    if kind == "sum":
        return abs(left) <= _IDENTITY_ARITHMETIC_EPS or abs(right) <= _IDENTITY_ARITHMETIC_EPS
    if kind == "difference":
        return abs(right) <= _IDENTITY_ARITHMETIC_EPS or abs(left - right) <= _IDENTITY_ARITHMETIC_EPS
    if kind == "percent_of":
        return abs(right - 100.0) <= _IDENTITY_ARITHMETIC_EPS
    if kind == "remainder_after_percent":
        return abs(right) <= _IDENTITY_ARITHMETIC_EPS
    return False


def _operand_quality(param: ParamField) -> int:
    return {
        "computed": 4,
        "selected_option_field": 3,
        "user_input": 3,
        "form_option": 3,
        "api_option": 3,
        "page_enum": 3,
        "previous_response": 2,
        "page_default": 2,
        "page_rule": 2,
        "unknown": 1,
    }.get(param.source_kind, 0)


def _identity_product_allowed(target: ParamField, left: ParamField, right: ParamField) -> bool:
    left_number = _as_finite_number(left.value)
    one = (
        left
        if left_number is not None and abs(left_number - 1.0) <= _IDENTITY_ARITHMETIC_EPS
        else right
    )
    other = right if one is left else left
    same_group = (
        _param_group_prefix(one.path) == _param_group_prefix(target.path)
        and _param_group_prefix(other.path) == _param_group_prefix(target.path)
    )
    return (
        same_group
        and not _param_was_caller_typed(target)
        and _looks_count_formula_leaf(one.key, one.path)
        and _looks_total_formula_leaf(target.key, target.path)
        and not _looks_total_formula_leaf(other.key, other.path)
    )


def _arithmetic_match_score(
    target: ParamField,
    kind: str,
    left: ParamField,
    right: ParamField,
    identity: bool,
) -> tuple[int, int, int]:
    same = int(_param_group_prefix(left.path) == _param_group_prefix(target.path)) + int(
        _param_group_prefix(right.path) == _param_group_prefix(target.path)
    )
    return (same, _operand_quality(left) + _operand_quality(right), int(not identity))


def _pick_arithmetic_match(
    target: ParamField,
    target_number: float,
    siblings: list[tuple[ParamField, float]],
) -> tuple[str, ParamField, ParamField] | None:
    matches: list[tuple[str, ParamField, ParamField, bool]] = []
    for left, left_number in siblings:
        for right, right_number in siblings:
            if left is right:
                continue
            for kind, compute, _commutative in _ARITHMETIC_STRATEGIES:
                try:
                    actual = compute(left_number, right_number)
                except ZeroDivisionError:
                    continue
                if not _numbers_match(target_number, actual):
                    continue
                if kind in {"percent_of", "remainder_after_percent"} and not (
                    _looks_percent_formula_leaf(left.key, left.path)
                    or _looks_percent_formula_leaf(right.key, right.path)
                ):
                    continue
                identity = _is_identity_arithmetic(kind, left_number, right_number)
                if identity and not (
                    kind == "product" and _identity_product_allowed(target, left, right)
                ):
                    continue
                if not (_is_stable_operand(left) and _is_stable_operand(right)):
                    continue
                if (
                    left.source_kind == "computed"
                    and right.source_kind == "computed"
                    and kind in {"sum", "difference"}
                ):
                    continue
                if kind in {"sum", "difference"} and any(
                    _looks_percent_formula_leaf(param.key, param.path)
                    for param, _number in siblings
                ):
                    continue
                matches.append((kind, left, right, identity))
    if not matches:
        return None
    best_score = max(_arithmetic_match_score(target, *item) for item in matches)
    top = [item for item in matches if _arithmetic_match_score(target, *item) == best_score]
    kinds = {item[0] for item in top}
    if len(kinds) != 1:
        percent_top = [
            item for item in top
            if item[0] in {"percent_of", "remainder_after_percent"}
            or _looks_percent_formula_leaf(item[1].key, item[1].path)
            or _looks_percent_formula_leaf(item[2].key, item[2].path)
        ]
        percent_kinds = {item[0] for item in percent_top}
        if len(percent_kinds) != 1:
            return None
        top = percent_top
        kinds = percent_kinds
    kind = next(iter(kinds))

    def pair_key(left: ParamField, right: ParamField) -> tuple[float, float]:
        left_number = round(float(_as_finite_number(left.value) or 0.0), 8)
        right_number = round(float(_as_finite_number(right.value) or 0.0), 8)
        if kind in {"product", "sum"}:
            return (min(left_number, right_number), max(left_number, right_number))
        if kind == "percent_of":
            return (min(left_number, right_number), max(left_number, right_number))
        return (left_number, right_number)

    equivalent = {pair_key(left, right) for _kind, left, right, _identity in top}
    if len(equivalent) != 1:
        return None
    top.sort(key=lambda item: (
        -_operand_quality(item[1]),
        -_operand_quality(item[2]),
        str(item[1].path),
        str(item[2].path),
        str(item[1].key),
        str(item[2].key),
    ))
    _kind, left, right, _identity = top[0]
    if kind == "percent_of":
        left_number = abs(float(_as_finite_number(left.value) or 0.0))
        right_number = abs(float(_as_finite_number(right.value) or 0.0))
        left_is_base = (
            _operand_quality(left) > _operand_quality(right)
            or (
                _operand_quality(left) == _operand_quality(right)
                and left_number > right_number
            )
        )
        if not left_is_base:
            left, right = right, left
    return kind, left, right


def _arithmetic_operand_semantic_ok(param: ParamField, *, kind: str = "") -> bool:
    if _looks_non_quantity_formula_leaf(param.key, param.path):
        return False
    if (
        _looks_count_formula_leaf(param.key, param.path)
        or _looks_unit_price_formula_leaf(param.key, param.path)
        or _looks_percent_formula_leaf(param.key, param.path)
        or any(token in _field_leaf_token(param.key, param.path) for token in ("date", "time", "duration", "day"))
    ):
        return True
    if kind in {"percent_of", "remainder_after_percent"} and (
        _looks_total_formula_leaf(param.key, param.path)
        or "price" in _field_leaf_token(param.key, param.path)
        or "amount" in _field_leaf_token(param.key, param.path)
    ):
        return True
    return False


def _arithmetic_strong_structure(
    target: ParamField,
    left: ParamField,
    right: ParamField,
    kind: str,
) -> bool:
    """Single-sample formulas need a readonly/derived target and typed operands."""
    if _param_has_editable_control_evidence(target) and not _param_control_is_readonly(target):
        return False
    target_leaf = _field_leaf_token(target.key, target.path)
    derived = (
        _looks_total_formula_leaf(target.key, target.path)
        or _looks_percent_formula_leaf(target.key, target.path)
        or any(token in target_leaf for token in ("duration", "payable", "subtotal"))
    )
    if not derived:
        return False
    if not (
        _arithmetic_operand_semantic_ok(left, kind=kind)
        and _arithmetic_operand_semantic_ok(right, kind=kind)
    ):
        return False
    prefix = _param_group_prefix(target.path)
    if _param_group_prefix(left.path) != prefix or _param_group_prefix(right.path) != prefix:
        return False
    if kind in {"sum", "difference"} and not (
        _looks_percent_formula_leaf(left.key, left.path)
        or _looks_percent_formula_leaf(right.key, right.path)
        or _looks_total_formula_leaf(left.key, left.path)
        or _looks_total_formula_leaf(right.key, right.path)
    ):
        return False
    return True


def _infer_arithmetic_computed_fields(spec: FlowSpec) -> None:
    """Hide numeric fields that the recorded values prove are derived from siblings."""
    changed = True
    while changed:
        changed = False
        for step in spec.steps or []:
            numeric = [
                (param, number)
                for param in step.params or []
                if (number := _as_finite_number(param.value)) is not None
            ]
            ranked: list[tuple[int, int, int, ParamField, str, ParamField, ParamField]] = []
            for target, target_number in numeric:
                if not _arithmetic_target_allowed(target):
                    continue
                prefix = _param_group_prefix(target.path)
                local = [
                    (param, number)
                    for param, number in numeric
                    if param is not target and _param_group_prefix(param.path) == prefix
                ]
                picked = _pick_arithmetic_match(target, target_number, local)
                if picked is None:
                    global_siblings = [
                        (param, number) for param, number in numeric if param is not target
                    ]
                    if any(
                        _looks_percent_formula_leaf(param.key, param.path)
                        for param, _number in global_siblings
                    ) and (
                        _looks_total_formula_leaf(target.key, target.path)
                        or _looks_percent_formula_leaf(target.key, target.path)
                    ):
                        picked = _pick_arithmetic_match(target, target_number, global_siblings)
                if picked is None:
                    continue
                kind, left, right = picked
                if not _arithmetic_strong_structure(target, left, right, kind):
                    continue
                ranked.append((
                    int(kind in {"percent_of", "remainder_after_percent"}),
                    int(
                        _looks_percent_formula_leaf(left.key, left.path)
                        or _looks_percent_formula_leaf(right.key, right.path)
                    ),
                    int(_looks_total_formula_leaf(target.key, target.path)),
                    target, kind, left, right,
                ))
            if not ranked:
                continue
            ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
            target, kind, left, right = ranked[0][3:]
            target.category = "runtime_var"
            target.source_kind = "computed"
            target.source = {
                "kind": "computed",
                "strategy": kind,
                "left_field": left.key,
                "right_field": right.key,
                "result_field": target.key,
                "path": target.path,
                "sample_verified": True,
            }
            target.exposed_to_user = False
            target.editable = False
            target.required = False
            target.need_human_confirm = False
            if (
                not _param_field_manually_edited(target, "type")
                and (
                    str(target.wire_type or "") == "number"
                    or _as_finite_number(target.value) is not None
                )
            ):
                target.type = "number"
            target.reason = (
                f"录制样例证明该字段由 `{left.key}` 与 `{right.key}` 按 {kind} 计算得到，运行期自动计算"
            )
            step.sample_inputs.pop(target.key, None)
            changed = True


def _param_is_temporal(param: ParamField) -> bool:
    if str(param.type or param.wire_type or "").lower() in {"date", "datetime", "time"}:
        return True
    if any(
        isinstance(item, dict)
        and str(item.get("control_kind") or "").lower() in {"date", "datetime", "time"}
        for item in (param.evidence or [])
    ):
        return True
    text = str(param.value or "").strip()
    return bool(
        re.fullmatch(r"-?\d{10}|-?\d{13}", text)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ tT]\d{2}:\d{2}(?::\d{2})?)?", text)
    )


def _step_is_record_edit_form(step: FlowStep) -> bool:
    params = list(step.params or [])
    hydrated = [
        param for param in params
        if param.source_kind == "previous_response"
        and not _param_is_document_record_identity(param)
    ]
    if len(hydrated) >= 3:
        return True
    body_fields = [
        param for param in params
        if not str(param.path or "").startswith("query.")
        and not _param_is_document_record_identity(param)
    ]
    dialog_owned = any(_param_has_command_local_control(step, param) for param in body_fields)
    return len(hydrated) >= 2 and (len(body_fields) >= 2 or dialog_owned)


def _param_has_command_local_control(step: FlowStep, param: ParamField) -> bool:
    """True only when the control belongs to this write, not a list filter."""
    action = str((step.source_meta or {}).get("trigger_action_id") or "")
    for item in param.evidence or []:
        if not isinstance(item, dict) or item.get("kind") != "page_control":
            continue
        surface = str(item.get("surface") or "").strip().lower()
        in_dialog = item.get("in_dialog") is True or surface in {"dialog", "modal", "drawer"}
        interacted = bool(item.get("interacted")) or str(item.get("op") or "").lower() in {
            "fill", "select", "pick",
        }
        evidence_action = str(item.get("action_id") or "")
        if in_dialog:
            return True
        if interacted and (not action or not evidence_action or evidence_action == action):
            return True
    return False


def _step_is_row_command(step: FlowStep) -> bool:
    """A list-row click that mutates one record without opening an edit form."""
    if str(step.method or "").upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    if _step_is_record_edit_form(step):
        return False
    return any(_param_is_document_record_identity(param) for param in step.params or [])


def _apply_row_command_field_contracts(spec: FlowSpec) -> None:
    """Keep row-command identity caller-selected and payload literals fixed.

    List filters and dictionary APIs live on the same page as approve/reject/
    delete buttons. Their leaf names (status, type, flag) must not become the
    command's public options. The command only needs the record the caller
    selected; every other leaf without a field-local control is the button's
    recorded discriminator.
    """
    for step in spec.steps or []:
        if not _step_is_row_command(step):
            continue
        for param in step.params or []:
            if param.locked or _param_has_manual_contract(param):
                continue
            if _param_is_document_record_identity(param):
                param.category = "user_param"
                param.source_kind = "user_input"
                param.source = {
                    "kind": "selected_record_identity",
                    "path": param.path,
                    "required_state": "required",
                }
                param.required = True
                param.exposed_to_user = True
                param.editable = True
                param.need_human_confirm = False
                param.reason = (
                    "调用方选择要操作的记录；行级点击没有详情回填，"
                    "不能把列表或上游样例 ID 当成固定值"
                )
                continue
            if _param_has_command_local_control(step, param):
                continue
            if param.source_kind in {
                "computed", "current_user", "system_time", "system_generated",
                "page_context", "request_header", "session",
            }:
                continue
            param.category = "system_const"
            param.source_kind = "constant"
            param.source = {
                "kind": "command_literal",
                "path": param.path,
                "value": param.value,
            }
            param.required = False
            param.exposed_to_user = False
            param.editable = False
            param.enum_options = None
            param.enum_value_map = None
            if param.type in {"enum", "list-enum"}:
                param.type = param.wire_type or "string"
            param.need_human_confirm = False
            param.reason = (
                "行级命令随按钮提交的固定判别值，不是列表筛选或字典接口的实时选项"
            )
            step.selects = [
                binding for binding in (step.selects or [])
                if binding.path != param.path
            ]
            if param.key:
                step.sample_inputs.pop(param.key, None)


def _step_is_create_or_submit_form(step: FlowStep) -> bool:
    """A write that collected a form, not a list-row command or hydrated edit."""
    if str(step.method or "").upper() not in {"POST", "PUT", "PATCH"}:
        return False
    if _step_is_row_command(step) or _step_is_record_edit_form(step):
        return False
    body = [
        param for param in step.params or []
        if not str(param.path or "").startswith("query.")
    ]
    return len(body) >= 2


def _create_form_field_is_system_owned(step: FlowStep, param: ParamField) -> bool:
    """Proven runtime/system origins stay off the caller list."""
    if _looks_pagination_field(param.key, param.path):
        return True
    if param.source_kind in {
        "computed", "selected_option_field", "current_user",
        "system_time", "system_generated", "page_context", "page_rule",
        "request_header", "session",
    }:
        return True
    if param.source_kind == "constant" and str((param.source or {}).get("kind") or "") in {
        "command_literal", "recorded_control_default",
    }:
        return True
    if _looks_runtime_field(param.key, param.path) or _looks_system_const_field(param.key, param.path):
        return True
    if _looks_audit_system_leaf(param.key, param.path) and not _param_has_command_local_control(step, param):
        return True
    if _looks_row_identity_leaf(param.key, param.path):
        return True
    if (
        _param_is_document_record_identity(param)
        and not _record_identity_is_caller_owned(str(step.method or ""), param.value)
    ):
        return True
    if _param_control_is_readonly(param):
        return True
    return False


def _create_unknown_has_caller_evidence(param: ParamField) -> bool:
    if _param_control_kinds(param) & {"hidden"}:
        return False
    if _param_control_is_readonly(param) and not _param_was_caller_typed(param):
        return False
    if _param_has_editable_control_evidence(param) or _param_was_caller_typed(param):
        return True
    for item in param.evidence or []:
        if not isinstance(item, dict) or item.get("kind") != "page_control":
            continue
        if item.get("hidden") or item.get("disabled") or item.get("read_only"):
            continue
        control_kind = str(item.get("control_kind") or "unknown").lower()
        if control_kind in {"", "unknown", "hidden"}:
            continue
        op = str(item.get("op") or "").lower()
        if op in {"fill", "select", "pick", "toggle"} or item.get("interacted"):
            return True
        if item.get("field_aliases") or control_kind not in {"", "unknown"}:
            return True
    return False


def _mark_create_form_caller_input(param: ParamField, *, reason: str) -> None:
    param.category = "user_param"
    param.exposed_to_user = True
    param.editable = True
    param.need_human_confirm = False
    if reason:
        param.reason = reason
    if _param_has_local_required_marker(param):
        param.required = True
        param.source = {**(param.source or {}), "required_state": "required"}
    elif str((param.source or {}).get("required_state") or "") not in {"required", "optional"}:
        param.source = {**(param.source or {}), "required_state": "unknown"}


def _param_has_local_required_marker(param: ParamField) -> bool:
    if _param_has_page_required_evidence(param):
        return True
    return any(
        isinstance(item, dict)
        and item.get("kind") == "page_control"
        and item.get("required") is True
        for item in (param.evidence or [])
    )


def _apply_create_form_field_contracts(spec: FlowSpec) -> None:
    """Caller owns manual create/submit inputs; system owns proven derivations.

    Bound page controls are sufficient but not required. After formulas and
    option-row echoes are assigned, a remaining create-body unknown is a
    handwritten form value, not a system field.
    """
    caller_kinds = {
        "user_input", "form_option", "page_default", "api_option",
        "page_enum", "static_enum", "manual_enum", "caller_input",
    }
    for step in spec.steps or []:
        if not _step_is_create_or_submit_form(step):
            continue
        for param in step.params or []:
            if param.locked or _param_has_manual_contract(param):
                continue
            if _create_form_field_is_system_owned(step, param):
                continue
            if str(param.path or "").startswith("query.") and not _param_has_command_local_control(step, param):
                continue
            if param.source_kind in caller_kinds:
                _mark_create_form_caller_input(param, reason="")
                continue
            if param.source_kind not in {"", "unknown"}:
                continue
            if not _create_unknown_has_caller_evidence(param):
                continue
            chooser = bool(_param_control_kinds(param) & {"select", "combobox", "radio"})
            if chooser:
                param.source_kind = "form_option"
                param.source = {"kind": "form_option", "path": param.path}
                _mark_create_form_caller_input(
                    param,
                    reason="新建/提交表单上由调用方选择的字段",
                )
            else:
                param.source_kind = "user_input"
                param.source = {"kind": "sample", "path": param.path, "recorded": True}
                _mark_create_form_caller_input(
                    param,
                    reason="新建/提交表单上的手工输入，由调用方提供",
                )


def _option_row_match_count(row: dict[str, Any], members: list[ParamField]) -> int:
    matched = 0
    for param in members:
        if param.value in (None, ""):
            continue
        if _param_is_quantity_or_formula_leaf(param.key, param.path):
            continue
        if _best_option_projection_path(row, param.path, param.value):
            matched += 1
    return matched


def _infer_selected_option_row_fields(spec: FlowSpec) -> None:
    """Project write-body siblings from the unique captured option row they share."""
    catalogs: list[tuple[str, list[dict[str, Any]]]] = []
    for fact in spec.request_facts.requests or []:
        analysis = spec.request_facts.analysis.get(str(fact.request_id or "")) if fact.request_id else None
        read = fact.model_dump(exclude_none=True)
        read["role"] = str(analysis.role if analysis is not None else read.get("role") or "")
        if not (
            read["role"] in {"option", "read_option", "option_source", "explicit_read_option"}
            or _choice_control_triggered(read)
        ):
            continue
        rows = [item for item in (as_list_payload(fact.response_json) or []) if isinstance(item, dict)]
        if len(rows) >= 2:
            catalogs.append((str(fact.request_id or ""), rows))
    if not catalogs:
        return
    for step in spec.steps or []:
        if str(step.method or "").upper() not in {"POST", "PUT", "PATCH"}:
            continue
        groups: dict[str, list[ParamField]] = {}
        for param in step.params or []:
            groups.setdefault(_param_group_prefix(param.path), []).append(param)
        for members in groups.values():
            scored: list[tuple[int, str, dict[str, Any]]] = []
            for request_id, rows in catalogs:
                hits = [
                    (row, _option_row_match_count(row, members))
                    for row in rows
                ]
                good = [(row, count) for row, count in hits if count >= 2]
                if len(good) != 1:
                    continue
                row, count = good[0]
                scored.append((count, request_id, row))
            if not scored:
                continue
            best = max(item[0] for item in scored)
            winners = [item for item in scored if item[0] == best]
            unique_rows = {
                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
                for _count, _request_id, row in winners
            }
            if len(unique_rows) != 1:
                continue
            _count, request_id, row = winners[0]
            projected_paths: set[str] = set()
            for sibling in members:
                if sibling.locked or _param_has_manual_contract(sibling):
                    continue
                if sibling.source_kind in {
                    "computed", "current_user", "system_time", "system_generated",
                    "page_context", "request_header",
                }:
                    continue
                if (
                    _looks_page_context_field(sibling.key, sibling.path)
                    or _looks_audit_system_leaf(sibling.key, sibling.path)
                    or _field_leaf_token(sibling.key, sibling.path) in {"ownerid", "userid"}
                ):
                    continue
                sibling_leaf = _field_leaf_token(sibling.key, sibling.path)
                if sibling_leaf.endswith("id") or sibling_leaf in {"id", "ids"}:
                    if _looks_row_identity_leaf(sibling.key, sibling.path) or _param_is_document_record_identity(sibling):
                        continue
                    if not (_param_control_kinds(sibling) & {"select", "combobox", "radio"}):
                        continue
                    response_path = _best_option_projection_path(
                        row, sibling.path, sibling.value, min_score=40,
                    )
                    if (
                        response_path
                        and sibling.source_kind in {"", "unknown", "page_default"}
                        and not _param_has_manual_contract(sibling)
                    ):
                        sibling.category = "user_param"
                        sibling.source_kind = "form_option"
                        sibling.source = {
                            "kind": "form_option",
                            "path": sibling.path,
                            "source_request_id": request_id,
                            "response_path": response_path,
                        }
                        sibling.exposed_to_user = True
                        sibling.editable = True
                        sibling.need_human_confirm = False
                        sibling.reason = "所选目录行的标识由调用方选择，运行期再带出同行字段"
                    continue
                if _param_is_quantity_or_formula_leaf(sibling.key, sibling.path):
                    continue
                if (
                    _param_has_editable_control_evidence(sibling)
                    and not _param_control_is_readonly(sibling)
                    and sibling.source_kind not in {"unknown", "page_default"}
                ):
                    continue
                response_path = _best_option_projection_path(row, sibling.path, sibling.value)
                if not response_path:
                    continue
                selector = next(
                    (
                        item for item in members
                        if item is not sibling
                        and (
                            _field_leaf_token(item.key, item.path).endswith("id")
                            or _field_leaf_token(item.key, item.path) in {"id", "ids"}
                        )
                    ),
                    None,
                )
                sibling.category = "runtime_var"
                sibling.source_kind = "selected_option_field"
                sibling.source = {
                    "kind": "selected_option_field",
                    "selector_path": selector.path if selector is not None else "",
                    "selector_param": selector.key if selector is not None else "",
                    "source_request_id": request_id,
                    "response_path": response_path,
                    "target_path": sibling.path,
                }
                sibling.exposed_to_user = False
                sibling.editable = False
                sibling.required = False
                sibling.need_human_confirm = False
                sibling.reason = f"该字段来自所选记录的 `{response_path}`，运行期随选择自动写入"
                projected_paths.add(str(sibling.path or ""))
            if projected_paths:
                step.selects = [
                    binding for binding in (step.selects or [])
                    if str(binding.path or "") not in projected_paths
                    and str(binding.id_path or "") not in projected_paths
                ]


def _looks_audit_system_leaf(key: str, path: str) -> bool:
    leaf = _field_leaf_token(key, path)
    if leaf in {
        "createtime", "updatetime", "createdat", "updatedat",
        "creator", "updater", "modifier", "createby", "updateby",
        "creatorname", "updatername", "createdby", "updatedby",
    }:
        return True
    return leaf.endswith(("createtime", "updatetime", "createdat", "updatedat"))


def _looks_row_identity_leaf(key: str, path: str) -> bool:
    return "[" in str(path or "") and _field_leaf_token(key, path) in {"id", "ids"}


def _looks_catalog_attribute_leaf(key: str, path: str) -> bool:
    leaf = _field_leaf_token(key, path)
    if leaf.endswith("id") or leaf in {"id", "ids"}:
        return False
    return any(leaf.endswith(token) for token in (
        "name", "title", "label", "barcode", "unitname", "stock", "stockcount",
        "spec", "image", "img",
    ))


def _looks_display_echo_field(step: FlowStep, param: ParamField) -> bool:
    leaf = _field_leaf_token(param.key, param.path)
    stem = ""
    for suffix in ("name", "title", "label", "text"):
        if leaf.endswith(suffix) and len(leaf) > len(suffix):
            stem = leaf[: -len(suffix)]
            break
    if not stem:
        return False
    group = _param_group_prefix(param.path)
    for other in step.params or []:
        if other is param or _param_group_prefix(other.path) != group:
            continue
        other_leaf = _field_leaf_token(other.key, other.path)
        if other_leaf in {stem, f"{stem}id", f"{stem}ids"}:
            return True
    return False


def _mark_system_hydrated_field(param: ParamField, reason: str) -> None:
    param.category = "runtime_var"
    param.exposed_to_user = False
    param.editable = False
    param.required = False
    param.need_human_confirm = False
    if param.source_kind == "previous_response":
        param.source = {**(param.source or {}), "allow_caller_override": False, "required_state": "optional"}
        param.reason = reason
        return
    if param.source_kind in {"unknown", "user_input", "page_default"}:
        param.source_kind = "previous_response" if (param.source or {}).get("link_id") else param.source_kind
        param.source = {**(param.source or {}), "allow_caller_override": False, "required_state": "optional"}
        param.reason = reason


def _apply_edit_form_field_contracts(spec: FlowSpec) -> None:
    """Keep edit-form identity/audit/display echoes system-owned.

    Hydration makes most write leaves caller-overridable. The document id used
    to load the record, audit timestamps, and label echoes of a chosen *Id stay
    on the system side even when their values came from the detail GET.
    """
    for step in spec.steps or []:
        if not _step_is_record_edit_form(step):
            continue
        for param in step.params or []:
            if param.locked or _param_has_manual_contract(param) or param.source_kind == "computed":
                continue
            if _param_is_document_record_identity(param) or _looks_row_identity_leaf(param.key, param.path):
                _mark_system_hydrated_field(
                    param,
                    "该字段是记录或行项目标识，由详情接口回填，不作为调用方输入",
                )
                continue
            if _looks_audit_system_leaf(param.key, param.path) and not _param_has_command_local_control(step, param):
                _mark_system_hydrated_field(
                    param,
                    "该字段是审计/系统时间或创建人痕迹，由详情接口回填，不作为调用方输入",
                )
                continue
            if (
                _field_leaf_token(param.key, param.path) in {"status", "state"}
                and not _param_has_command_local_control(step, param)
            ):
                _mark_system_hydrated_field(
                    param,
                    "该字段是单据状态回写，编辑提交随详情带出，不是列表筛选或行级命令",
                )
                continue
            if _looks_display_echo_field(step, param) and not _param_has_command_local_control(step, param):
                _mark_system_hydrated_field(
                    param,
                    "该字段是选项显示名回写，随所选标识自动带出，不作为调用方输入",
                )
                continue
            if (
                param.source_kind == "previous_response"
                and param.value in (None, "")
                and not _param_has_command_local_control(step, param)
            ):
                _mark_system_hydrated_field(
                    param,
                    "该字段在详情与提交中均为空，随请求携带，不作为调用方输入",
                )
                continue
            if (
                param.source_kind == "previous_response"
                and not _param_control_is_readonly(param)
                and not _looks_audit_system_leaf(param.key, param.path)
            ):
                param.category = "user_param"
                param.exposed_to_user = True
                param.editable = True
                param.source = {**(param.source or {}), "allow_caller_override": True}
                if "可修改" not in (param.reason or ""):
                    param.reason = (
                        f"{param.reason}；调用方仍可修改该字段，显式输入优先于上游默认值"
                        if param.reason else
                        "编辑场景默认来自上游详情；调用方仍可修改该字段，显式输入优先于上游默认值"
                    )


def _step_role(step: FlowStep) -> str:
    return str((step.source_meta or {}).get("role") or step.semantic_role or "").casefold()


def _step_is_option_read(step: FlowStep) -> bool:
    return _step_role(step) in {
        "read_option", "option", "option_source", "explicit_read_option",
    }


def _step_is_record_detail_query(step: FlowStep) -> bool:
    """A GET that only names the record being opened, not a search form."""
    if str(step.method or "").upper() != "GET":
        return False
    filters = [
        param for param in (step.params or [])
        if str(param.path or "").startswith("query.")
        and _caller_filter_key(param.key, param.path)
    ]
    return bool(filters) and all(
        _param_is_document_record_identity(param)
        or _looks_row_identity_leaf(param.key, param.path)
        for param in filters
    )


def _step_is_business_list_query(step: FlowStep) -> bool:
    """Any non-option business GET that carries query leaves is a list/search."""
    if str(step.method or "").upper() != "GET":
        return False
    if _step_is_option_read(step) or _step_is_record_detail_query(step):
        return False
    if _step_role(step) in {"auth", "support", "context", "telemetry", "noise"}:
        return False
    return any(
        str(param.path or "").startswith("query.")
        for param in (step.params or [])
    )


def _mark_query_filter_caller(param: ParamField, *, reason: str) -> None:
    param.category = "user_param"
    param.exposed_to_user = True
    param.editable = True
    param.need_human_confirm = False
    if _param_has_local_required_marker(param):
        param.required = True
        param.source = {**(param.source or {}), "required_state": "required"}
    elif str((param.source or {}).get("required_state") or "") not in {"required", "optional"}:
        param.source = {**(param.source or {}), "required_state": "unknown"}
    if reason:
        param.reason = reason


def _apply_query_form_field_contracts(spec: FlowSpec) -> None:
    """Business list/search filters stay caller-owned on every query capability.

    Option-source leftovers and transport keys stay internal. Pagination is
    page context. A missing control binding is not proof that a search leaf is
    a system field — the query string of a list execute *is* the search form.
    """
    caller_kinds = {
        "user_input", "form_option", "page_default", "api_option",
        "page_enum", "static_enum", "manual_enum", "caller_input",
    }
    for step in spec.steps or []:
        if not _step_is_business_list_query(step):
            continue
        for param in step.params or []:
            if param.locked or _param_has_manual_contract(param):
                continue
            if not str(param.path or "").startswith("query."):
                continue
            if _looks_pagination_field(param.key, param.path):
                continue
            if not _caller_filter_key(param.key, param.path):
                continue
            if _looks_runtime_field(param.key, param.path) or _looks_system_const_field(param.key, param.path):
                continue
            if param.source_kind in {
                "page_context", "request_header", "session", "current_user",
                "computed", "page_rule", "selected_option_field",
            }:
                continue
            if param.source_kind in caller_kinds:
                if not param.exposed_to_user or param.category != "user_param":
                    _mark_query_filter_caller(param, reason="")
                continue
            if param.source_kind not in {"", "unknown"}:
                continue
            if not _create_unknown_has_caller_evidence(param):
                continue
            chooser = bool(_param_control_kinds(param) & {"select", "combobox", "radio"})
            if chooser:
                param.source_kind = "form_option"
                param.source = {"kind": "form_option", "path": param.path}
                _mark_query_filter_caller(
                    param,
                    reason="查询页上由调用方选择的筛选条件",
                )
            else:
                param.source_kind = "user_input"
                param.source = {
                    "kind": "business_query_filter",
                    "path": param.path,
                    "recorded": True,
                }
                _mark_query_filter_caller(
                    param,
                    reason="查询页上的业务筛选由调用方提供",
                )


def _mark_auto_fill_caller_override(param: ParamField, reason: str) -> None:
    param.category = "user_param"
    param.exposed_to_user = True
    param.editable = True
    param.required = False
    param.need_human_confirm = False
    param.source = {
        **(param.source or {}),
        "allow_caller_override": True,
        "required_state": "optional",
    }
    if reason and "可修改" not in (param.reason or ""):
        param.reason = f"{param.reason}；{reason}" if param.reason else reason


def _apply_page_rule_caller_override(spec: FlowSpec) -> None:
    """Keep auto-fill origin, but follow the page: editable means caller may change it.

    Origin (how the page produced the value) and ownership (who may supply it)
    are separate. Selected-row echoes default to caller-overridable. Computed
    totals stay system-owned unless a non-readonly control proves the page
    lets the operator overwrite the formula. Readonly/disabled locks the field
    on the system side.
    """
    for step in spec.steps or []:
        if _step_is_row_command(step):
            continue
        for param in step.params or []:
            if param.locked or _param_has_manual_contract(param):
                continue
            if _looks_pagination_field(param.key, param.path):
                continue
            if _looks_runtime_field(param.key, param.path) or _looks_system_const_field(param.key, param.path):
                continue
            if (
                _param_is_document_record_identity(param)
                and param.source_kind != "user_input"
            ):
                continue
            if _looks_audit_system_leaf(param.key, param.path) and not _param_has_command_local_control(step, param):
                continue
            if _param_control_is_readonly(param):
                continue
            if param.source_kind == "selected_option_field":
                _mark_auto_fill_caller_override(param, "所选记录自动带入，页面允许修改")
                continue
            if _looks_display_echo_field(step, param) and not _param_has_editable_control_evidence(param):
                continue
            if (
                param.source_kind == "computed"
                and _param_has_editable_control_evidence(param)
            ):
                _mark_auto_fill_caller_override(param, "页面自动计算，但仍允许调用方修改")


def _request_present_leaves(req: dict[str, Any]) -> set[str]:
    leaves: set[str] = set()
    query = req.get("query") or {}
    if isinstance(query, dict):
        for key, values in query.items():
            if values in (None, "", [], [""]):
                continue
            leaves.add(str(key))
    url = str(req.get("url") or "")
    if "?" in url:
        for key, values in parse_qs(url.split("?", 1)[1], keep_blank_values=True).items():
            if values in ([], [""]):
                continue
            leaves.add(str(key))
    parsed = parse_recorded_request_body(req.get("post_data"), str(req.get("content_type") or ""))
    for path in parsed.get("field_paths") or []:
        text = str(path or "").removeprefix("body.").removeprefix("query.")
        if text:
            leaves.add(text)
            leaves.add(text.rsplit(".", 1)[-1].split("[")[0])
    return {leaf for leaf in leaves if leaf}


def _successful_peer_omitted_leaves(spec: FlowSpec) -> set[tuple[str, str]]:
    groups: dict[tuple[str, str], list[set[str]]] = {}
    for fact in spec.request_facts.requests or []:
        status = int(getattr(fact, "response_status", None) or 0)
        if not (200 <= status < 400):
            continue
        method = str(getattr(fact, "method", "") or "GET").upper()
        path = _request_path({
            "url": getattr(fact, "url", "") or "",
            "path": getattr(fact, "path", "") or "",
        })
        groups.setdefault((method, path), []).append(_request_present_leaves({
            "url": getattr(fact, "url", "") or "",
            "query": getattr(fact, "query", None) or {},
            "post_data": getattr(fact, "post_data", None),
            "content_type": getattr(fact, "content_type", "") or "",
        }))
    omitted: set[tuple[str, str]] = set()
    for (_method, path), leaf_sets in groups.items():
        if len(leaf_sets) < 2:
            continue
        union: set[str] = set()
        for item in leaf_sets:
            union |= item
        for item in leaf_sets:
            for leaf in union - item:
                omitted.add((path, leaf))
    return omitted


def _apply_successful_omit_optional(spec: FlowSpec) -> None:
    omitted = _successful_peer_omitted_leaves(spec)
    if not omitted:
        return
    for step in spec.steps or []:
        path = _request_path({"url": step.path or step.url or ""})
        for param in step.params or []:
            if param.locked or _param_has_manual_contract(param):
                continue
            if str((param.source or {}).get("required_state") or "") == "required":
                continue
            if _param_has_page_required_evidence(param) or _param_has_local_required_marker(param):
                continue
            leaf = _field_leaf_token(param.key, param.path)
            if (path, leaf) not in omitted:
                continue
            param.source = {**(param.source or {}), "required_state": "optional"}
            param.required = False


def _apply_mechanical_field_contracts(spec: FlowSpec) -> None:
    """Apply the same origin/ownership rules to every capability family."""
    _infer_selected_option_row_fields(spec)
    _infer_computed_runtime_fields(spec)
    _apply_create_form_field_contracts(spec)
    _apply_edit_form_field_contracts(spec)
    _apply_row_command_field_contracts(spec)
    _apply_query_form_field_contracts(spec)
    _apply_successful_omit_optional(spec)
    _apply_page_rule_caller_override(spec)


def _query_range_index(path: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"(query\..+)\[(\d+)\]$", str(path or ""))
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def _calendar_date_text(value: Any) -> str | None:
    text = str(value if value is not None else "").strip()
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return None


def _apply_date_range_companions(spec: FlowSpec) -> None:
    """Keep a same-day query range end as a page rule when only the start was filled."""
    for step in spec.steps or []:
        grouped: dict[str, dict[int, ParamField]] = {}
        for param in step.params or []:
            parsed = _query_range_index(param.path or "")
            if parsed is None:
                continue
            grouped.setdefault(parsed[0], {})[parsed[1]] = param
        for parts in grouped.values():
            start, end = parts.get(0), parts.get(1)
            if start is None or end is None:
                continue
            start_text = str(start.value or "")
            end_text = str(end.value or "")
            start_date = _calendar_date_text(start.value)
            end_date = _calendar_date_text(end.value)
            if (
                start_date is None
                or start_date != end_date
                or not re.search(r"00:00(?::00)?$", start_text)
                or not re.search(r"23:59(?::59)?$", end_text)
            ):
                continue
            if end.locked or _param_has_manual_contract(end):
                continue
            if end.source_kind in {"user_input", "page_default", "api_option", "page_enum"} and _param_exposed_to_caller(end):
                continue
            end.category = "runtime_var"
            end.source_kind = "page_rule"
            end.source = {
                "kind": "date_range_end",
                "start_field": start.key,
                "path": end.path,
            }
            end.exposed_to_user = False
            end.editable = False
            end.required = False
            end.need_human_confirm = False
            end.reason = "查询区间结束时刻由开始日期按当天结束补齐，不作为独立调用方输入"


def _infer_computed_runtime_fields(spec: FlowSpec) -> None:
    """Hide recorded computed fields only when their samples prove the formula."""
    _apply_date_range_companions(spec)
    _infer_arithmetic_computed_fields(spec)
    def leaf_name(param: ParamField) -> str:
        raw = param.key or str(param.path or "").split(".")[-1]
        return re.sub(r"[^a-z0-9]+", "", str(raw).lower())

    date_pairs: list[tuple[FlowStep, ParamField, ParamField, int]] = []
    for step in spec.steps or []:
        temporals = [
            param for param in step.params or []
            if _param_is_temporal(param) and _date_like_epoch_seconds(param.value) is not None
        ]
        for index, left in enumerate(temporals):
            for right in temporals[index + 1:]:
                left_seconds = _date_like_epoch_seconds(left.value)
                right_seconds = _date_like_epoch_seconds(right.value)
                if left_seconds is None or right_seconds is None:
                    continue
                start, end = (left, right) if left_seconds <= right_seconds else (right, left)
                date_pairs.append((
                    step, start, end,
                    int(round(abs(right_seconds - left_seconds) / 86400.0)),
                ))
    if not date_pairs:
        return

    capability_memberships = [
        set(_capability_node_step_ids(capability))
        for capability in spec.capabilities or []
    ]

    def pair_rank(step: FlowStep, pair: tuple[FlowStep, ParamField, ParamField, int]) -> tuple[int, int, float]:
        source_step = pair[0]
        same_step = int(source_step.step_id == step.step_id)
        shared_capability = int(any(
            step.step_id in members and source_step.step_id in members
            for members in capability_memberships
        ))
        target_sequence = _step_sequence(step)
        source_sequence = _step_sequence(source_step)
        distance = (
            abs(target_sequence - source_sequence)
            if target_sequence is not None and source_sequence is not None
            else 10**9
        )
        return same_step, shared_capability, -distance

    assignments: list[tuple[FlowStep, ParamField, dict[str, Any], str]] = []
    for step in spec.steps or []:
        for param in step.params or []:
            if (
                param.locked
                or _param_has_editable_control_evidence(param)
                or _param_is_temporal(param)
                or param.source_kind in {
                    "computed", "selected_option_field", "api_option",
                    "form_option", "page_enum", "current_user",
                }
            ):
                continue
            key_norm = leaf_name(param)
            strategy = ""
            output_key = ""
            sample_value: Any = None
            if (
                str(param.path or "").startswith("query.")
                and re.search(r"(process)?variables?(str)?$|context(json|str)?$", key_norm)
            ):
                try:
                    payload = json.loads(str(param.value or ""))
                except Exception:  # noqa: BLE001
                    payload = None
                if isinstance(payload, dict) and len(payload) == 1:
                    output_key, sample_value = next(iter(payload.items()))
                    strategy = "date_span_days_json"
            elif _as_finite_number(param.value) is not None:
                sample_value = param.value
                strategy = "date_span_days"
            if not strategy:
                continue
            try:
                observed_days = int(sample_value)
            except (TypeError, ValueError):
                continue
            if observed_days < 0 or observed_days > 3660:
                continue
            named = bool(
                strategy == "date_span_days_json"
                or re.fullmatch(r"(?:day|days|duration|durationdays)", key_norm)
            )
            readonly_calc = any(
                isinstance(item, dict)
                and item.get("kind") == "page_control"
                and (item.get("read_only") or item.get("disabled"))
                for item in (param.evidence or [])
            )
            two_dates = sum(1 for item in step.params or [] if _param_is_temporal(item)) == 2
            if not (named or readonly_calc or two_dates):
                continue
            matches = [pair for pair in date_pairs if pair[3] == observed_days]
            if not matches:
                continue
            ranked = sorted(matches, key=lambda pair: pair_rank(step, pair), reverse=True)
            if len(ranked) > 1 and pair_rank(step, ranked[0]) == pair_rank(step, ranked[1]):
                continue
            _source_step, start, end, _days = ranked[0]
            assignments.append((step, param, {
                "kind": "computed",
                "strategy": strategy,
                "start_field": start.key,
                "end_field": end.key,
                "path": param.path,
                "sample_verified": True,
                "sample_days": observed_days,
                **({"output_key": str(output_key)} if output_key else {}),
            }, f"录制样例证明该字段由 `{start.key}` 与 `{end.key}` 的日期跨度生成，运行期自动计算"))

    claimed_pairs: dict[tuple[str, str, str], int] = {}
    for step, param, source, _reason in assignments:
        key = (step.step_id, str(source.get("start_field")), str(source.get("end_field")))
        claimed_pairs[key] = claimed_pairs.get(key, 0) + 1
    for step, param, source, reason in assignments:
        key = (step.step_id, str(source.get("start_field")), str(source.get("end_field")))
        if claimed_pairs.get(key, 0) != 1:
            continue
        param.category = "runtime_var"
        param.source_kind = "computed"
        param.source = source
        param.exposed_to_user = False
        param.editable = False
        param.required = False
        param.need_human_confirm = False
        param.reason = reason
        step.sample_inputs.pop(param.key, None)


def _repair_uncontrolled_write_state_fields(spec: FlowSpec) -> int:
    """Keep request-owned command state out of the caller contract."""
    repaired = 0
    for step in spec.steps or []:
        if not _is_write_step(step):
            continue
        for param in step.params or []:
            leaf = re.sub(
                r"[^a-z0-9]+", "",
                str(param.path or param.key).split(".")[-1].casefold(),
            )
            if (
                not re.fullmatch(r"(?:(?:process|workflow|approval|record))?(?:status|state)", leaf)
                or param.source_kind != "unknown"
                or param.locked
                or _param_has_manual_contract(param)
                or _param_has_editable_control_evidence(param)
                or isinstance(param.value, (dict, list))
            ):
                continue
            param.category = "system_const"
            param.source_kind = "constant"
            param.source = {
                "kind": "recorded_command_state",
                "path": param.path,
            }
            param.exposed_to_user = False
            param.editable = False
            param.need_human_confirm = False
            param.reason = "录制中没有可编辑控件证明该写入状态由用户提供，按请求自身命令状态保留"
            step.sample_inputs.pop(param.key, None)
            repaired += 1
    return repaired


def _repair_readonly_control_defaults(spec: FlowSpec) -> int:
    """Bind an aliasless locked control only to one stable write-wire field.

    A disabled value can legitimately appear in several save/submit requests.
    Requiring one request would misclassify it as caller input, while matching
    by value alone could bind unrelated fields.  Accept it only when every
    scoped occurrence of that scalar has the same canonical wire path.
    """
    repaired = 0

    def same_scalar(left: Any, right: Any) -> bool:
        if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
            return False
        return str(left).strip().casefold() == str(right).strip().casefold()

    evidence_items = [
        item for item in (getattr(spec.request_facts, "field_evidence", []) or [])
        if isinstance(item, dict)
        and item.get("value") not in (None, "")
        and item.get("editable") is False
        and (
            item.get("disabled") is True
            or (
                item.get("read_only") is True
                and str(item.get("control_kind") or "").lower()
                not in {"select", "combobox"}
            )
        )
    ]
    for evidence in evidence_items:
        candidates: list[tuple[FlowStep, ParamField, str]] = []
        for step in spec.steps or []:
            if not _is_write_step(step) or not _recording_evidence_matches_scope(
                step.source_meta or {}, evidence,
            ):
                continue
            for param in step.params or []:
                if not same_scalar(param.value, evidence.get("value")):
                    continue
                candidates.append((
                    step,
                    param,
                    _strip_body_prefix(str(param.path or param.key or "")),
                ))
        wire_paths = {path for _step, _param, path in candidates if path}
        if len(wire_paths) != 1 or not candidates:
            continue
        wire_path = next(iter(wire_paths))
        for step, param, _path in candidates:
            if (
                param.locked
                or param.source_kind in {"computed", "selected_option_field"}
                or _param_has_manual_contract(param)
                or _param_source_agent_classified(param)
                or _param_has_editable_control_evidence(param)
            ):
                continue
            param.category = "system_const"
            param.source_kind = "constant"
            param.source = {
                "kind": "recorded_control_default",
                "path": param.path,
                "wire_path": wire_path,
                "evidence_id": str(evidence.get("evidence_id") or ""),
            }
            param.exposed_to_user = False
            param.editable = False
            param.required = False
            param.need_human_confirm = False
            param.reason = "页面证据证明该控件不可编辑；录制请求在同一 wire 字段使用其默认值"
            step.sample_inputs.pop(param.key, None)
            repaired += 1
    return repaired


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
    if t == "object":
        return {"type": "object"}
    if t in {"list-enum", "array"}:
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def _business_type_for_param(param: ParamField) -> str:
    ptype = (param.type or "string").lower()
    if ptype in {"textarea", "rich_text"} or (
        ptype in {"string", "text"}
        and any(
            str(item.get("control_kind") or "").lower() == "textarea"
            for item in (param.evidence or [])
            if isinstance(item, dict)
        )
    ):
        return "textarea"
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


_RUNTIME_SUPPLIED_SOURCE_KINDS = frozenset({
    "previous_response", "current_user", "storage", "cookie", "page_context",
    "request_header", "system_time", "system_generated", "computed",
    "constant", "page_rule", "loop_item", "selected_option_field",
    "dynamic_structure",
})


def _previous_response_source_step_id(param: ParamField) -> str:
    if param.source_kind != "previous_response":
        return ""
    source = dict(param.source or {})
    return str(source.get("step_id") or source.get("source_step_id") or "")


def _external_capability_input(
    param: ParamField,
    capability_step_ids: set[str] | None,
) -> bool:
    source_step_id = _previous_response_source_step_id(param)
    return bool(
        capability_step_ids is not None
        and source_step_id
        and source_step_id not in capability_step_ids
    )


def _param_exposed_to_caller(
    param: ParamField,
    capability_step_ids: set[str] | None = None,
) -> bool:
    """Whether the caller, rather than the workflow runtime, supplies a value."""
    if _looks_pagination_field(param.key, param.path):
        return False
    if _external_capability_input(param, capability_step_ids):
        return True
    if (
        param.source_kind == "page_context"
        and bool((param.source or {}).get("caller_override"))
    ):
        return bool(param.category == "user_param" and param.exposed_to_user)
    if (
        param.source_kind in {"previous_response", "selected_option_field", "computed"}
        and bool(
            (param.source or {}).get("allow_caller_override")
            or (param.source or {}).get("caller_override")
        )
    ):
        return bool(param.category == "user_param" and param.exposed_to_user)
    return bool(
        param.category == "user_param"
        and param.exposed_to_user
        and param.source_kind not in _RUNTIME_SUPPLIED_SOURCE_KINDS
    )


def _param_requires_caller_input(
    param: ParamField,
    capability_step_ids: set[str] | None = None,
) -> bool:
    return bool(
        _external_capability_input(param, capability_step_ids)
        or (
            param.required
            and _param_exposed_to_caller(param, capability_step_ids)
        )
    )


_NO_SCHEMA_DEFAULT = object()


def _schema_default_for_param(param: ParamField) -> Any:
    """Return the recorded, type-correct prompt default without inventing one.

    Defaults on normal business fields are question-card prefills.  Pagination
    is marked separately as safe to apply when omitted.  Enum request samples
    are wire values, so expose the matching human label when the evidence map
    proves one instead of leaking an internal code as the default.
    """
    # ``value`` is the sample captured in this particular recording. It proves
    # transport shape for replay, but it is not evidence of a page default.
    value = param.default_value
    if value is None and _looks_pagination_field(param.key, param.path):
        value = param.value
    if value in (None, ""):
        return _NO_SCHEMA_DEFAULT

    if param.type in {"enum", "list-enum"}:
        value_map = dict(param.enum_value_map or _enum_option_map_from_options(param.enum_options))
        if param.type == "enum":
            label = next(
                (str(name) for name, wire in value_map.items() if str(wire) == str(value)),
                None,
            )
            if label:
                return label
            option_labels = [
                str(pair[0])
                for item in (param.enum_options or [])
                if (pair := _enum_label_value(item)) is not None
            ]
            if str(value) in option_labels:
                return str(value)
            # The recording contains an internal code but no evidence-backed
            # label for it.  Do not prefill a user-facing question with that
            # code and do not guess a label by option order.
            return _NO_SCHEMA_DEFAULT
        elif isinstance(value, list):
            reverse = {str(wire): str(name) for name, wire in value_map.items()}
            if all(str(item) in reverse for item in value):
                return [reverse[str(item)] for item in value]
            return _NO_SCHEMA_DEFAULT
        return _NO_SCHEMA_DEFAULT

    if param.type in {"number", "integer"}:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value) if param.type == "integer" else value
        text = str(value).strip()
        try:
            if param.type == "integer":
                return int(text) if re.fullmatch(r"-?\d+", text) else _NO_SCHEMA_DEFAULT
            return int(text) if re.fullmatch(r"-?\d+", text) else float(text)
        except (TypeError, ValueError):
            return _NO_SCHEMA_DEFAULT
    if param.type == "boolean":
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
        return _NO_SCHEMA_DEFAULT
    if param.type in {"date", "datetime"}:
        text = str(value).strip()
        date_pattern = r"\d{4}-\d{2}-\d{2}"
        datetime_pattern = r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?"
        if param.type == "date" and re.fullmatch(date_pattern, text):
            return text
        if param.type == "datetime" and re.fullmatch(datetime_pattern, text):
            return text
        if re.fullmatch(r"\d{10}|\d{13}", text):
            seconds = int(text) / (1000 if len(text) == 13 else 1)
            observed = datetime.fromtimestamp(seconds, tz=timezone.utc)
            return observed.strftime("%Y-%m-%d" if param.type == "date" else "%Y-%m-%d %H:%M:%S")
        return _NO_SCHEMA_DEFAULT
    if param.type in {"array", "list-enum"} and not isinstance(value, list):
        return _NO_SCHEMA_DEFAULT
    if param.type == "object" and not isinstance(value, dict):
        return _NO_SCHEMA_DEFAULT
    return value if not isinstance(value, str) else value.strip()


def _apply_param_schema_default(prop: dict[str, Any], param: ParamField) -> None:
    default = _schema_default_for_param(param)
    if default is _NO_SCHEMA_DEFAULT:
        return
    prop["default"] = default
    # Only pagination is safe for the invocation layer to apply silently.
    # Other defaults exist for ask_user_question prefill and user review.
    if _looks_pagination_field(param.key, param.path):
        prop["x-dano-apply-default"] = True


def _capability_input_schema(
    params: list[ParamField],
    capability_step_ids: set[str] | None = None,
) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in params:
        if not _param_exposed_to_caller(p, capability_step_ids):
            continue
        key = p.key or p.path
        if key in props:
            existing = props[key]
            candidate_business = _business_type_for_param(p)
            candidate_wire = p.wire_type or _infer_type_from_value(p.value) or "string"
            if (
                existing.get("x-dano-business-type") != candidate_business
                or existing.get("x-dano-wire-type") != candidate_wire
            ):
                existing.setdefault("x-dano-conflicts", []).append({
                    "path": p.path,
                    "business_type": candidate_business,
                    "wire_type": candidate_wire,
                })
            elif existing.get("x-flow-path") != p.path:
                paths = existing.setdefault("x-flow-paths", [existing.get("x-flow-path")])
                if p.path not in paths:
                    paths.append(p.path)
            if _param_requires_caller_input(p, capability_step_ids) and key not in required:
                required.append(key)
            continue
        props[key] = _schema_for_param_type(p.type)
        props[key]["x-flow-path"] = p.path
        props[key]["x-dano-business-type"] = _business_type_for_param(p)
        props[key]["x-dano-wire-type"] = p.wire_type or _infer_type_from_value(p.value) or "string"
        wire_format = p.wire_format or _infer_wire_format(p.value)
        if wire_format:
            props[key]["x-dano-wire-format"] = wire_format
        if p.label:
            props[key]["label"] = p.label
        if p.description or p.reason:
            props[key]["description"] = p.description or p.reason
        if (
            _external_capability_input(p, capability_step_ids)
            or p.source_kind == "external_capability_input"
        ):
            props[key]["x-dano-external-source"] = {
                "step_id": str(
                    (p.source or {}).get("source_step_id")
                    or _previous_response_source_step_id(p)
                ),
                "response_path": str(
                    (p.source or {}).get("response_path")
                    or (p.source or {}).get("path")
                    or ""
                ),
            }
        option_source = (
            p.source
            if p.source_kind == "api_option"
            else (p.source or {}).get("option_source")
        )
        if isinstance(option_source, dict) and option_source:
            props[key]["x-dano-option-source"] = copy.deepcopy(option_source)
        _apply_param_schema_default(props[key], p)
        grounded_constraints = next((
            item for item in (p.evidence or [])
            if isinstance(item, dict)
            and str(item.get("source") or "") == "recorder_dom"
            and any(name in item for name in ("minimum", "maximum"))
        ), {})
        for constraint in ("minimum", "maximum"):
            value = grounded_constraints.get(constraint)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                props[key][constraint] = value
        enum_input = p.type in {"enum", "list-enum"}
        dynamic_options = enum_input and p.source_kind == "api_option"
        enum_confirmed = (p.source or {}).get("enum_confirmed")
        incomplete_page_enum = enum_input and p.source_kind == "page_enum" and enum_confirmed is False
        if enum_input:
            if p.type == "list-enum":
                props[key].setdefault("items", {})["format"] = "name-ref"
            else:
                props[key]["format"] = "name-ref"
        if dynamic_options:
            props[key]["x-options-source"] = True
            props[key]["x-options-source-meta"] = dict(p.source or {})
        if incomplete_page_enum:
            props[key]["x-options-incomplete"] = True
        if enum_input and p.enum_options:
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
        if enum_input and p.enum_value_map:
            props[key]["x-enum-value-map"] = dict(p.enum_value_map)
        if _param_requires_caller_input(p, capability_step_ids):
            required.append(key)
    return {"type": "object", "properties": props, "required": required}






_IDENTIFIER_ROLE_BY_FIELD = {
    "processinstanceid": "process_instance",
    "workflowinstanceid": "process_instance",
    "flowinstanceid": "process_instance",
    "billcode": "business_document",
    "billno": "business_document",
    "documentcode": "business_document",
    "documentno": "business_document",
    "documentnumber": "business_document",
    "applicationno": "business_document",
    "applyno": "business_document",
    "recordid": "record",
    "applicationid": "record",
    "applyid": "record",
    "id": "record",
}
_IDENTIFIER_ROLE_TITLE = {
    "process_instance": "流程实例ID",
    "business_document": "业务编号",
    "record": "记录ID",
}
_IDENTIFIER_RELATION_TARGET_KINDS = {
    "inspect", "update", "approve", "reject", "withdraw", "delete",
}


def _identifier_role_for_field(name: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", str(name or "").casefold())
    return _IDENTIFIER_ROLE_BY_FIELD.get(normalized, "")


def _output_field_is_transport_only(name: Any, schema: dict[str, Any]) -> bool:
    """Keep business response fields visible while suppressing transport identities."""
    role = str((schema or {}).get("x-dano-identifier-role") or _identifier_role_for_field(name))
    if role in {"record", "process_instance"}:
        return True
    normalized = re.sub(r"[^a-z0-9]+", "", str(name or "").casefold())
    return normalized in {
        "billtype", "processdefkey", "processdefinitionkey",
        "tenantid", "userid", "deptid", "departmentid",
        "organizationid", "orgid", "creatorid", "updaterid",
        "deleted", "creator", "updater",
    }


def _schema_node_at_path(schema: dict[str, Any] | None, path: str) -> dict[str, Any] | None:
    """Resolve object/array schema paths such as ``records[].processInstanceId``."""
    raw = str(path or "").strip()
    if not raw:
        return None
    parts = [part for part in re.split(r"\.|\[\]", raw) if part]
    node: Any = schema or {}
    for part in parts:
        if not isinstance(node, dict):
            return None
        while node.get("type") == "array" and isinstance(node.get("items"), dict):
            node = node["items"]
        properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        if part not in properties:
            return None
        node = properties[part]
    return node if isinstance(node, dict) else None


def _apply_output_presentation_evidence(
    output_schema: dict[str, Any],
    evidence: list[dict[str, Any]] | None,
    *,
    sample_output: Any = None,
    input_schema: dict[str, Any] | None = None,
    field_labels: dict[str, str] | None = None,
) -> None:
    """Project recorded table headers into a query result schema.

    The page is authoritative for labels, order and visibility.  This function
    deliberately does not translate transport field names or invent business
    labels when the recorder did not observe a matching table column.
    """
    row_properties: dict[str, Any] = {}
    for field_schema in (output_schema.get("properties") or {}).values():
        if not isinstance(field_schema, dict) or field_schema.get("type") != "array":
            continue
        candidate = ((field_schema.get("items") or {}).get("properties") or {})
        if candidate:
            row_properties = candidate
            break
    if not row_properties:
        return

    for name, field_schema in row_properties.items():
        if not isinstance(field_schema, dict):
            continue
        label = str((field_labels or {}).get(name) or "").strip()
        if label and not (field_schema.get("title") or field_schema.get("label")):
            field_schema["title"] = label
        if _output_field_is_transport_only(name, field_schema):
            field_schema["x-dano-display"] = False

    groups: dict[str, list[dict[str, Any]]] = {}
    for item in evidence or []:
        if not isinstance(item, dict) or (
            item.get("kind") != "table_column"
            and item.get("control_kind") != "table_column"
        ):
            continue
        groups.setdefault(str(item.get("table_id") or "table"), []).append(item)

    def normalized(value: Any) -> str:
        return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)

    sample_rows: list[dict[str, Any]] = []

    def find_rows(value: Any) -> None:
        nonlocal sample_rows
        if sample_rows:
            return
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value[:5]):
            sample_rows = value[:5]
            return
        if isinstance(value, dict):
            for nested in value.values():
                find_rows(nested)
                if sample_rows:
                    return

    find_rows(sample_output)
    enum_labels: dict[str, dict[str, str]] = {}
    for input_field in ((input_schema or {}).get("properties") or {}).values():
        if not isinstance(input_field, dict):
            continue
        output_name = str(input_field.get("x-flow-path") or "").split(".")[-1]
        if output_name not in row_properties:
            continue
        labels: dict[str, str] = {}
        for label, wire_value in (input_field.get("x-enum-value-map") or {}).items():
            labels[str(wire_value)] = str(label)
        for option_key in ("x-options", "x-options-snapshot"):
            for option in input_field.get(option_key) or []:
                if isinstance(option, dict) and option.get("label") not in (None, ""):
                    wire_value = option.get("value", option.get("id"))
                    if wire_value not in (None, ""):
                        labels[str(wire_value)] = str(option["label"])
        if labels:
            enum_labels[output_name] = labels

    best: tuple[int, int, list[tuple[str, dict[str, Any]]], list[dict[str, Any]]] | None = None
    for columns in groups.values():
        matched: list[tuple[str, dict[str, Any]]] = []
        used: set[str] = set()
        direct_matches = 0
        sample_matches = 0
        for column in sorted(columns, key=lambda item: int(item.get("display_order") or 0)):
            aliases = {
                normalized(alias)
                for alias in [
                    column.get("field"),
                    column.get("key"),
                    *(column.get("field_aliases") or []),
                ]
                if normalized(alias)
            }
            candidates = [
                name for name in row_properties
                if name not in used and normalized(name) in aliases
            ]
            direct = len(candidates) == 1
            if not direct and sample_rows:
                visible_values = {
                    normalized(value)
                    for value in (column.get("sample_values") or [])
                    if normalized(value)
                }
                visible_epochs = {
                    int(value)
                    for value in (column.get("sample_epoch_ms") or [])
                    if isinstance(value, (int, float))
                }
                candidates = []
                for name in row_properties:
                    if name in used:
                        continue
                    raw_values = [
                        row.get(name) for row in sample_rows
                        if row.get(name) not in (None, "")
                    ]
                    rendered = {normalized(value) for value in raw_values if normalized(value)}
                    rendered.update(
                        normalized(enum_labels.get(name, {}).get(str(value)))
                        for value in raw_values
                        if enum_labels.get(name, {}).get(str(value))
                    )
                    epoch_match = any(
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and int(value if abs(value) >= 100000000000 else value * 1000) in visible_epochs
                        for value in raw_values
                    )
                    if rendered.intersection(visible_values) or epoch_match:
                        candidates.append(name)
            if len(candidates) != 1:
                continue
            used.add(candidates[0])
            matched.append((candidates[0], column))
            if direct:
                direct_matches += 1
            else:
                sample_matches += 1
        score = direct_matches * 100 + sample_matches
        if best is None or score > best[0]:
            best = (score, direct_matches, matched, columns)
    if best is None or best[0] == 0:
        return

    _score, _direct_matches, matched, _columns = best
    visible_fields = {name for name, _column in matched}
    for name, column in matched:
        field_schema = row_properties[name]
        label = str(column.get("label") or "").strip()
        if label:
            field_schema["title"] = label
        field_schema["x-dano-display"] = True
        field_schema["x-dano-display-order"] = int(column.get("display_order") or 0)
        if (
            field_schema.get("type") in {"integer", "number"}
            and column.get("value_kind") == "datetime"
        ):
            field_schema["x-dano-value-format"] = "epoch-auto"

    for name, field_schema in row_properties.items():
        if (
            name not in visible_fields
            and isinstance(field_schema, dict)
            and _output_field_is_transport_only(name, field_schema)
        ):
            field_schema["x-dano-display"] = False


def _recorded_goal_from_parts(title: str, steps: list[FlowStep], risk_level: str) -> dict[str, Any]:
    write_steps = [s for s in steps if _is_write_step(s)]
    read_steps = [s for s in steps if not _is_write_step(s)]
    params: list[str] = []
    for st in steps:
        for p in st.params:
            if _param_requires_caller_input(p) and p.key and p.key not in params:
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
        forbidden_actions=list(_DEFAULT_RECORDED_FORBIDDEN_ACTIONS),
        risk_level=risk_level or "L3",
        capabilities=capabilities,
        evidence=[_step_evidence(s) for s in steps[:20]],
    )
    return goal.model_dump(exclude_none=True)


def _recorded_user_param_names(steps: list[FlowStep]) -> list[str]:
    """Required public inputs used by RecordedGoal.required_inputs."""
    params: list[str] = []
    for st in steps:
        for p in st.params:
            if _param_requires_caller_input(p) and p.key and p.key not in params:
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
    # FlowSpec 保持一致，否则发布层会把旧字段名误判成“Agent 臆造字段”并阻断。
    current_inputs = _recorded_user_param_names(goal_steps)
    goal["required_inputs"] = current_inputs
    # Empty axes count as missing: agent set_goal payloads and legacy specs may
    # carry `[]`, and the publish completeness gate checks emptiness, not keys.
    if not goal.get("intent"):
        goal["intent"] = fresh.get("intent") or spec.title
    if not goal.get("success_criteria"):
        goal["success_criteria"] = fresh.get("success_criteria") or []
    if not goal.get("output_expectation"):
        goal["output_expectation"] = fresh.get("output_expectation") or []
    existing_forbidden = {
        str(item).strip() for item in (goal.get("forbidden_actions") or []) if str(item).strip()
    }
    if existing_forbidden == _LEGACY_RECORDED_FORBIDDEN_ACTIONS:
        # Migrate the old generic deny-list.  A recorded withdraw/delete action
        # must not produce a goal that forbids its own observed business step.
        goal["forbidden_actions"] = list(_DEFAULT_RECORDED_FORBIDDEN_ACTIONS)
    elif not existing_forbidden:
        goal["forbidden_actions"] = fresh.get("forbidden_actions") or []
    if not goal.get("risk_level"):
        goal["risk_level"] = fresh.get("risk_level") or spec.risk_level or "L3"
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
    by_id = {step.step_id: step for step in spec.steps}
    steps = [by_id[sid] for sid in (cap.step_ids or []) if sid in by_id]
    writes = [step for step in steps if _is_write_step(step)]
    public_names = set(ALLOWED_CAPABILITY_KINDS)
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
    if not steps:
        return
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
        for step_id in set(cap.step_ids) - status_ids:
            cap.nodes = _remove_capability_step_nodes(cap.nodes or [], step_id)
        _sync_capability_order(spec, cap)
    elif cap.kind == "list_options":
        # 下拉来源属于字段执行细节，不自动暴露成独立业务能力。
        cap.nodes = []
        _sync_capability_order(spec, cap)


def _canonicalize_public_capability_identities(spec: FlowSpec) -> FlowSpec:
    """Atomically align public names and every cross-capability reference."""
    public_names = set(ALLOWED_CAPABILITY_KINDS)
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
    for step in spec.steps:
        for param in step.params or []:
            source = param.source or {}
            source_capability = str(source.get("source_capability") or "")
            if source_capability in renamed:
                param.source = {
                    **source,
                    "source_capability": renamed[source_capability],
                }
    if isinstance(spec.goal, dict):
        spec.goal["capabilities"] = list(dict.fromkeys(
            renamed.get(str(name), str(name)) for name in (spec.goal.get("capabilities") or []) if str(name)
        ))
    return spec


def _repair_generated_capability_contracts(
    spec: FlowSpec,
    *,
    repair_option_bindings: bool = True,
) -> FlowSpec:
    """Deterministically repair only Planner-generated capability contracts."""
    _normalize_capability_references(spec)
    _apply_mechanical_field_contracts(spec)
    rebuild_flow_dependencies(spec)
    if repair_option_bindings:
        _repair_structural_option_bindings(spec)
    by_id = {step.step_id: step for step in spec.steps}
    renamed: dict[str, str] = {}
    for cap in spec.capabilities or []:
        old_name = cap.name
        was_generated_duplicate = bool(re.fullmatch(r"submit_batch\d+", str(cap.name or "")))
        needed_batch_audit = cap.kind in {"submit_batch", "validate_batch"}
        _normalize_generated_capability_semantics(spec, cap)
        if not cap.locked:
            for mapping in cap.output_mapping or []:
                if not isinstance(mapping, dict):
                    continue
                name = str(mapping.get("name") or "")
                if not name or re.fullmatch(r"(?:output|result)(?:_?\d+)?", name, re.I):
                    mapping["name"] = "result"
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
        for step in spec.steps:
            for param in step.params or []:
                source = param.source or {}
                source_capability = str(source.get("source_capability") or "")
                if source_capability in renamed:
                    param.source = {
                        **source,
                        "source_capability": renamed[source_capability],
                    }
    _canonicalize_public_capability_identities(spec)
    spec = _prune_empty_capabilities(spec)
    _attach_option_source_memberships(spec)
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


def _param_path_leaf(path: str) -> str:
    tokens = [token for token in re.split(r"[.\[\]/]+", _strip_body_prefix(path or "")) if token]
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", tokens[-1].lower()) if tokens else ""


def _params_can_share_caller_key(left: ParamField, right: ParamField) -> bool:
    """同名字段仅在请求叶子与类型都一致时复用一个调用参数。"""
    return bool(
        _param_path_leaf(left.path) == _param_path_leaf(right.path)
        and _business_type_for_param(left) == _business_type_for_param(right)
        and (left.wire_type or _infer_type_from_value(left.value) or "string")
        == (right.wire_type or _infer_type_from_value(right.value) or "string")
    )


def _disambiguate_capability_param_keys(steps: list[FlowStep]) -> list[dict[str, Any]]:
    """为能力闭包中的同名异义字段生成稳定 ``#2`` 别名。

    同一个业务叶子跨接口复用时保留共享输入；不同请求叶子不能继续争用同一个
    caller key，否则 schema、sample_inputs 和请求编译会互相覆盖。
    """
    entries = [(step, param) for step in steps for param in (step.params or []) if _param_exposed_to_caller(param)]
    used = {str(param.key or param.path or "").strip() for _step, param in entries if str(param.key or param.path or "").strip()}
    canonical_by_key: dict[str, ParamField] = {}
    changes: list[dict[str, Any]] = []
    # 锁定字段优先占用原名，自动字段围绕它消歧，避免覆盖人工契约。
    ordered = sorted(enumerate(entries), key=lambda item: (not bool(item[1][1].locked), item[0]))
    for _position, (step, param) in ordered:
        key = str(param.key or param.path or "").strip() or "field"
        canonical = canonical_by_key.get(key)
        if canonical is None:
            canonical_by_key[key] = param
            continue
        if _params_can_share_caller_key(canonical, param):
            continue
        if param.locked:
            # 两个互相冲突的人工锁定字段不擅自改名，仅作为生成建议展示。
            continue
        base = key
        suffix = 2
        candidate = f"{base}#{suffix}"
        while candidate in used:
            suffix += 1
            candidate = f"{base}#{suffix}"
        old_key = param.key
        param.key = candidate
        param.source = {**(param.source or {}), "original_key": old_key or base, "collision_resolved": True}
        param.evidence = [*(param.evidence or []), {
            "kind": "field_key_collision_resolved",
            "original_key": old_key or base,
            "resolved_key": candidate,
            "path": param.path,
            "step_id": step.step_id,
        }]
        used.add(candidate)
        canonical_by_key[candidate] = param
        for binding in step.selects or []:
            if binding.path and _strip_body_prefix(binding.path) == _strip_body_prefix(param.path):
                binding.param = candidate
        changes.append({
            "step_id": step.step_id,
            "path": param.path,
            "original_key": old_key or base,
            "resolved_key": candidate,
        })
    for step in steps:
        step.sample_inputs = {
            str(param.key or param.path): param.value
            for param in (step.params or [])
            if param.value not in (None, "")
            and param.source_kind != "dynamic_structure"
            and str((param.source or {}).get("kind") or "") != "dynamic_structure_leaf"
        }
    return changes


_ACTIONABLE_PLACEHOLDER_NAME_RE = re.compile(
    r"^(?:请输入|请选择|请填写|请选取|请录入)\s*[：:、，,。.!！?？-]*\s*(.+)$",
    re.I,
)


def _normalize_actionable_placeholder_param_names(spec: FlowSpec) -> list[dict[str, str]]:
    """Turn a uniquely recoverable placeholder into its business field name.

    ``请输入撤回原因`` carries enough page evidence to become ``撤回原因``;
    vague examples such as ``例如 XXX`` do not, and remain operator advice.
    Manual/locked names are never rewritten.
    """
    changes: list[dict[str, str]] = []
    for step in spec.steps:
        for param in step.params or []:
            current = str(param.key or param.label or "").strip()
            match = _ACTIONABLE_PLACEHOLDER_NAME_RE.fullmatch(current)
            if (
                not match
                or param.locked
                or param.name_source == "manual"
            ):
                continue
            business_name = re.sub(r"\s+", "", match.group(1)).strip("：:、，,。.!！?？-_ ")
            if not business_name or business_name == current:
                continue
            try:
                _rename_param_public_key(spec, step, param, business_name, actor="planner")
            except ValueError:
                # A duplicate business name is ambiguous; preserve both fields
                # and expose the normal structured warning instead.
                continue
            changes.append({
                "step_id": step.step_id,
                "path": param.path,
                "old_name": current,
                "new_name": business_name,
            })
    return changes


def _capability_output_samples(
    capability: FlowCapability,
    step_by_id: dict[str, FlowStep],
) -> dict[str, Any]:
    samples: dict[str, Any] = {}
    for index, mapping in enumerate(capability.output_mapping or []):
        if not isinstance(mapping, dict):
            continue
        step = step_by_id.get(str(mapping.get("step_id") or ""))
        if step is None or step.response_json is None:
            continue
        path = str(mapping.get("response_path") or mapping.get("path") or "response")
        value = step.response_json
        if path not in {"", "response", "$", "."}:
            candidate = _flow_path_lookup(step.response_json, path)
            if candidate is _FLOW_PATH_MISSING:
                continue
            value = candidate
        samples[_capability_output_name(mapping, index)] = value
    if samples:
        return samples
    steps = [
        step_by_id[step_id]
        for step_id in _capability_scoped_step_ids(capability)
        if step_id in step_by_id
    ]
    response = next(
        (step.response_json for step in reversed(steps) if step.response_json is not None),
        None,
    )
    return response if isinstance(response, dict) else {}


def _annotate_identifier_sources(
    schema: dict[str, Any],
    sample: Any,
    *,
    path: str = "",
) -> list[dict[str, Any]]:
    """Mark stable identifier leaves and retain their recorded values as evidence."""
    found: list[dict[str, Any]] = []
    schema_type = str(schema.get("type") or "")
    if schema_type == "array":
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        values = sample if isinstance(sample, list) else []
        item_path = f"{path}[]" if path else "[]"
        if values:
            for item in values[:80]:
                found.extend(_annotate_identifier_sources(item_schema, item, path=item_path))
        else:
            found.extend(_annotate_identifier_sources(item_schema, None, path=item_path))
        return found
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if properties:
        sample_object = sample if isinstance(sample, dict) else {}
        for name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                continue
            field_path = f"{path}.{name}" if path else str(name)
            role = _identifier_role_for_field(name)
            if role and field_schema.get("type") not in {"object", "array"}:
                field_schema["x-dano-identifier-role"] = role
                found.append({
                    "path": field_path,
                    "role": role,
                    "values": {
                        str(sample_object[name])
                        for _ in [0]
                        if name in sample_object
                        and sample_object[name] not in (None, "")
                        and not isinstance(sample_object[name], (dict, list))
                    },
                })
            found.extend(_annotate_identifier_sources(
                field_schema,
                sample_object.get(name),
                path=field_path,
            ))
    return found


def _capability_page_ids(
    spec: FlowSpec,
    capability: FlowCapability,
    step_by_id: dict[str, FlowStep],
) -> set[str]:
    return {
        page_id
        for step_id in _capability_scoped_step_ids(capability)
        if step_id in step_by_id
        if (page_id := _step_page_id_from_facts(spec, step_by_id[step_id]))
    }


def _target_input_values(
    capability: FlowCapability,
    input_name: str,
    field_schema: dict[str, Any],
    step_by_id: dict[str, FlowStep],
) -> set[str]:
    values = {
        str(field_schema["default"])
        for _ in [0]
        if field_schema.get("default") not in (None, "")
        and not isinstance(field_schema.get("default"), (dict, list))
    }
    wire_path = str(field_schema.get("x-flow-path") or "")
    for step_id in _capability_scoped_step_ids(capability):
        step = step_by_id.get(step_id)
        if step is None:
            continue
        for param in step.params or []:
            if not (
                input_name in {str(param.key or ""), str(param.label or "")}
                or (wire_path and wire_path == str(param.path or ""))
            ):
                continue
            for value in (param.value, param.default_value):
                if value not in (None, "") and not isinstance(value, (dict, list)):
                    values.add(str(value))
    return values


def _identifier_value_is_grounding_evidence(value: str) -> bool:
    text = str(value or "").strip()
    return (
        len(text) >= 6
        and text.casefold() not in _BORING_LINK_VALUES
        and not re.fullmatch(r"\d{1,5}", text)
    )


def _ground_recorded_identifier_relations(
    spec: FlowSpec,
    step_by_id: dict[str, FlowStep],
) -> FlowSpec:
    """Bind later mutations to the exact identifier field observed in a query.

    Public labels and the generic wire name ``id`` are not evidence. A relation
    is generated only when one recorded mutation value matches exactly one
    semantically named identifier field in a recorded business-query result.
    """
    generated_kind = "recorded_identifier_match"
    spec.capability_relations = [
        relation
        for relation in (spec.capability_relations or [])
        if str((relation.evidence or {}).get("kind") or "") != generated_kind
    ]

    sources: list[dict[str, Any]] = []
    for capability in spec.capabilities or []:
        if capability.kind not in {"query", "query_status", "inspect"}:
            continue
        sample = _capability_output_samples(capability, step_by_id)
        for item in _annotate_identifier_sources(capability.output_schema or {}, sample):
            if item["values"]:
                sources.append({
                    **item,
                    "capability": capability,
                    "pages": _capability_page_ids(spec, capability, step_by_id),
                })

    for target in spec.capabilities or []:
        if target.kind not in _IDENTIFIER_RELATION_TARGET_KINDS:
            continue
        target_ref = target.name or target.capability_id
        target_pages = _capability_page_ids(spec, target, step_by_id)
        for input_name, field_schema in (
            (target.input_schema or {}).get("properties") or {}
        ).items():
            if not isinstance(field_schema, dict):
                continue
            wire_leaf = _param_path_leaf(
                str(field_schema.get("x-flow-path") or input_name)
            )
            wire_role = _identifier_role_for_field(wire_leaf)
            if not wire_role:
                continue
            target_values = {
                value
                for value in _target_input_values(
                    target, str(input_name), field_schema, step_by_id,
                )
                if _identifier_value_is_grounding_evidence(value)
            }
            if not target_values:
                continue
            matches = [
                source for source in sources
                if target_values.intersection(source["values"])
                and (
                    source["capability"].name
                    or source["capability"].capability_id
                ) != target_ref
            ]
            query_matches = [
                source for source in matches
                if source["capability"].kind in {"query", "query_status"}
            ]
            if query_matches:
                # Later detail calls may echo the same ID. The selectable
                # collection remains the actual caller orchestration source.
                matches = query_matches
            if re.sub(r"[^a-z0-9]+", "", wire_leaf.casefold()) != "id":
                matches = [
                    source for source in matches
                    if source["role"] == wire_role
                ]
            same_page = [
                source for source in matches
                if target_pages and target_pages.intersection(source["pages"])
            ]
            if same_page:
                matches = same_page
            identities = {
                (
                    source["capability"].name or source["capability"].capability_id,
                    source["path"],
                    source["role"],
                )
                for source in matches
            }
            if len(identities) != 1:
                continue
            source = matches[0]
            source_ref, source_path, role = next(iter(identities))
            title = _IDENTIFIER_ROLE_TITLE[role]
            field_schema.update({
                "title": title,
                "label": title,
                "description": (
                    f"必须取自能力 `{source_ref}` 输出字段 `{source_path}`；"
                    "不得使用其他 ID、业务编号或录制样本代替。"
                ),
                "x-dano-identifier-role": role,
                "x-dano-derived-from-query": True,
                "x-dano-source-capability": source_ref,
                "x-dano-source-output": source_path,
                "x-dano-require-current-value": True,
            })
            field_schema.pop("default", None)
            field_schema.pop("x-dano-apply-default", None)
            target_wire_path = str(field_schema.get("x-flow-path") or "")
            for step_id in _capability_scoped_step_ids(target):
                target_step = step_by_id.get(step_id)
                if target_step is None:
                    continue
                for param in target_step.params or []:
                    if not (
                        str(param.key or "") == str(input_name)
                        or (target_wire_path and str(param.path or "") == target_wire_path)
                    ):
                        continue
                    if param.source_kind == "unknown":
                        param.category = "user_param"
                        param.source_kind = "user_input"
                        param.source = {
                            "kind": "capability_relation",
                            "source_capability": str(source_ref),
                            "source_output": str(source_path),
                            "target_path": str(param.path or target_wire_path),
                        }
                        param.reason = (
                            f"调用方先执行能力 `{source_ref}`，再把所选记录的"
                            f" `{source_path}` 原值传入；不是自由手填字段"
                        )
                        param.need_human_confirm = False
                    elif str((param.source or {}).get("kind") or "") == "capability_relation":
                        param.source = {
                            **(param.source or {}),
                            "source_capability": str(source_ref),
                            "source_output": str(source_path),
                            "target_path": str(param.path or target_wire_path),
                        }
            relation_identity = "|".join(
                (str(source_ref), str(source_path), str(target_ref), str(input_name))
            )
            relation = CapabilityRelation(
                relation_id="rel_" + hashlib.sha1(
                    relation_identity.encode("utf-8")
                ).hexdigest()[:12],
                type="external_transform",
                mode="external_transform",
                from_capability=str(source_ref),
                from_output=str(source_path),
                to_capability=str(target_ref),
                to_input=str(input_name),
                requires_user_confirmation=True,
                confidence=1.0,
                confirmed=True,
                reason="录制中后续操作参数与查询结果的稳定标识字段精确一致",
                evidence={
                    "kind": generated_kind,
                    "identifier_role": role,
                    "value_hash": hashlib.sha256(
                        sorted(target_values)[0].encode("utf-8")
                    ).hexdigest()[:16],
                },
                transform_owner="caller",
                cardinality="many_to_one",
                required=True,
                source_selector="$." + str(source_path).replace("[]", "[*]"),
                target_path=str(field_schema.get("x-flow-path") or input_name),
                input_schema=copy.deepcopy(field_schema),
                output_schema=copy.deepcopy(
                    _schema_node_at_path(
                        source["capability"].output_schema,
                        str(source_path),
                    ) or {}
                ),
                caller_responsibility=(
                    f"先调用 `{source_ref}` 定位用户选择的同一条业务记录，"
                    f"再把该记录的 `{source_path}` 原值传给 `{target_ref}.{input_name}`；"
                    "禁止使用同一记录的其他 ID 字段。"
                ),
            )
            already_present = any(
                (
                    existing.from_capability,
                    existing.from_output,
                    existing.to_capability,
                    existing.to_input,
                ) == (
                    relation.from_capability,
                    relation.from_output,
                    relation.to_capability,
                    relation.to_input,
                )
                for existing in (spec.capability_relations or [])
            )
            if not already_present:
                spec.capability_relations.append(relation)
    return spec


def _expand_response_key_map_inputs(
    spec: FlowSpec,
    capability: FlowCapability,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Expose one caller field per stable response label, never the wire map."""
    expanded = copy.deepcopy(schema or {"type": "object", "properties": {}, "required": []})
    properties = expanded.setdefault("properties", {})
    required = [str(name) for name in expanded.get("required") or []]
    member_ids = set(capability.step_ids or [])
    by_id = {step.step_id: step for step in spec.steps}
    reserved = set(properties)
    for link in spec.links or []:
        if (
            _flow_link_kind(link) != "response_key_map"
            or link.source_step_id not in member_ids
            or link.target_step_id not in member_ids
        ):
            continue
        binding = dict(link.value_binding or {})
        labels = [str(label) for label in binding.get("required_labels") or [] if str(label)]
        input_field = str(binding.get("input_field") or "")
        if not labels or not input_field:
            continue
        target = by_id.get(link.target_step_id)
        public = next((
            param for param in (target.params if target is not None else [])
            if _strip_body_prefix(str(param.path or ""))
            == _strip_body_prefix(str(link.target_container_path or link.target_path or ""))
        ), None)
        samples = public.value if public is not None and isinstance(public.value, dict) else {}
        properties.pop(input_field, None)
        reserved.discard(input_field)
        required = [name for name in required if name != input_field]
        field_map: dict[str, str] = {}
        existing_map = dict(binding.get("input_fields_by_label") or {})
        for label in labels:
            preferred = str(existing_map.get(label) or label)
            field_name = preferred
            if field_name in reserved:
                field_name = f"{input_field}.{label}"
            suffix = 2
            base_name = field_name
            while field_name in reserved:
                field_name = f"{base_name}_{suffix}"
                suffix += 1
            reserved.add(field_name)
            field_map[label] = field_name
            sample = samples.get(label)
            field_schema = _schema_from_response_value(sample)
            field_schema.update({
                "label": label,
                "description": f"为上游返回的“{label}”节点选择调用值",
                "x-dano-capability-owned": True,
                "x-dano-dynamic-key-map": {
                    "link_id": link.link_id,
                    "label": label,
                    "target_path": link.target_container_path or link.target_path,
                },
            })
            option_source = binding.get("option_source")
            if isinstance(option_source, dict) and option_source:
                field_schema["x-dano-option-source"] = copy.deepcopy(option_source)
                field_schema["x-options-source"] = True
            properties[field_name] = field_schema
            required.append(field_name)
        link.value_binding = {
            **binding,
            "input_fields_by_label": field_map,
        }
        if target is not None:
            target.sample_inputs.pop(input_field, None)
            for label, field_name in field_map.items():
                if label in samples:
                    target.sample_inputs[field_name] = copy.deepcopy(samples[label])
    expanded["required"] = list(dict.fromkeys(required))
    return expanded


def _sync_capability_io_schemas(spec: FlowSpec) -> FlowSpec:
    """让 capability 的输入输出 schema 始终跟当前字段/响应保持一致。"""
    if not spec.capabilities:
        return spec

    _apply_mechanical_field_contracts(spec)
    # Capability compilation happens after live semantic edits. Apply only the
    # dependencies safe for execution so a confirmed response chain wins over
    # an unsupported caller-input guess. Keep non-executable selector evidence
    # long enough to derive cross-capability relations below.
    invalidated_link_ids = {
        link.link_id for link in spec.links
        if (link.meta or {}).get("unverified_reason")
    }
    if invalidated_link_ids:
        spec.links = [
            link for link in spec.links
            if not (
                link.link_id in invalidated_link_ids
                and _link_is_auto_generated(link)
            )
        ]
        for step in spec.steps:
            for param in step.params:
                if (
                    param.source_kind == "previous_response"
                    and str((param.source or {}).get("link_id") or "") in invalidated_link_ids
                ):
                    _reset_param_source(
                        param,
                        reason="上游依赖已重定向，字段已恢复为调用输入",
                    )
    _apply_link_sources(spec.steps, executable_flow_links(spec))
    _normalize_capability_references(spec)
    _normalize_actionable_placeholder_param_names(spec)

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
                    if key in {
                        "title", "description", "examples", "deprecated",
                        "x-dano-capability-owned", "x-dano-operator-owned",
                    }
                    and key not in field_schema
                    and previous.get("x-dano-derived-from-query") is not True
                }
                props[name] = {**annotations, **field_schema}
            else:
                props[name] = field_schema
        for name, field_schema in current_props.items():
            if (
                name not in props
                and isinstance(field_schema, dict)
                and field_schema.get("x-dano-capability-owned") is True
                and field_schema.get("x-dano-operator-owned") is True
            ):
                props[name] = dict(field_schema)
        merged["properties"] = props
        required = [
            str(name) for name in (derived.get("required") or [])
            if str(name) in props
        ]
        for name in current.get("required") or []:
            previous = current_props.get(str(name))
            if (
                str(name) in props
                and isinstance(previous, dict)
                and previous.get("x-dano-capability-owned") is True
                and str(name) not in required
            ):
                required.append(str(name))
        merged["required"] = list(dict.fromkeys(required))
        return merged

    by_id = {s.step_id: s for s in spec.steps}

    def entity_route_terms(step: FlowStep) -> set[str]:
        operation_terms = {
            "get", "list", "page", "query", "search", "detail", "create",
            "save", "draft", "submit", "update", "edit", "delete", "cancel",
            "withdraw", "approve", "reject", "process", "start",
        }
        return {
            term for term in re.split(
                r"[^a-z0-9一-鿿]+",
                urlparse(str(step.path or step.url or "")).path.casefold(),
            )
            if len(term) > 1
            and term not in _CAPABILITY_PATH_PREFIXES
            and term not in operation_terms
        }

    for cap in spec.capabilities:
        if cap.kind == "query_status":
            option_source_ids = _option_source_step_ids(spec)
            memberships = {ref.step_id: ref for ref in (cap.request_refs or []) if ref.step_id}
            allowed_step_ids = {
                sid for sid in (cap.step_ids or [])
                if (
                    bool(memberships.get(sid) and memberships[sid].origin in {"manual", "user"} and memberships[sid].usage in {"execute", "preflight", "fact_check"})
                    or (
                        (sid not in option_source_ids or (sid in by_id and _is_business_query_step(by_id[sid])))
                        and (
                            sid not in by_id
                            or ((by_id[sid].source_meta or {}).get("role") or by_id[sid].semantic_role or "") != "read_option"
                            or _is_business_query_step(by_id[sid])
                        )
                    )
                )
            }
            for step_id in set(cap.step_ids) - allowed_step_ids:
                cap.nodes = _remove_capability_step_nodes(cap.nodes or [], step_id)
            _sync_capability_order(spec, cap)
        cap.nodes = _sanitize_capability_nodes(spec, cap)
        cap_steps = [by_id[sid] for sid in (cap.step_ids or []) if sid in by_id]
        if not cap_steps:
            continue
        label_steps = list(cap_steps)
        if cap.kind == "query_status":
            query_route_terms = set().union(*(entity_route_terms(step) for step in cap_steps))
            label_steps.extend(
                step for step in spec.steps
                if step not in label_steps
                and _capability_operation_kind(step) in {
                    "create", "save_draft", "submit", "update",
                }
                and query_route_terms.intersection(entity_route_terms(step))
            )
        recorded_label_candidates: dict[str, set[str]] = {}
        for step in label_steps:
            for param in step.params or []:
                wire_name = str(param.path or "").replace("[]", "").split(".")[-1]
                label = str(param.label or param.key or "").strip()
                if (
                    wire_name
                    and label
                    and re.sub(r"[\W_]+", "", label.casefold(), flags=re.UNICODE)
                    != re.sub(r"[\W_]+", "", wire_name.casefold(), flags=re.UNICODE)
                ):
                    recorded_label_candidates.setdefault(wire_name, set()).add(label)
        recorded_field_labels = {
            name: next(iter(labels))
            for name, labels in recorded_label_candidates.items()
            if len(labels) == 1
        }
        _disambiguate_capability_param_keys(cap_steps)
        params = [p for st in cap_steps for p in (st.params or [])]
        derived_input = _capability_input_schema(params, set(cap.step_ids or []))
        derived_input = _expand_response_key_map_inputs(spec, cap, derived_input)
        if _capability_is_batch(spec, cap):
            derived_input = _batch_capability_input_schema(cap_steps)
        cap.input_schema = reconcile_schema(derived_input, cap.input_schema or {})
        if cap.kind == "query_status":
            cap.output_mapping = _query_output_mappings(cap_steps)
        mapped_output_props: dict[str, Any] = {}
        mapped_output_samples: dict[str, Any] = {}
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
            output_name = _capability_output_name(mapping, mapping_idx)
            mapped_output_props[output_name] = _schema_from_response_value(mapped_value)
            mapped_output_samples[output_name] = mapped_value
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
            elif cap.output_mapping:
                # A write endpoint may legitimately return no captured JSON
                # body.  Its declared final-response mapping is still enough to
                # build a stable public output contract; leaving an unrelated
                # stale schema here caused a late onboarding-only failure.
                existing_fields = {
                    field.key or field.path: field
                    for field in (cap.outputs or [])
                    if field.key or field.path
                }
                fallback_props: dict[str, Any] = {}
                for mapping_idx, mapping in enumerate(cap.output_mapping or []):
                    if not isinstance(mapping, dict):
                        continue
                    name = _capability_output_name(mapping, mapping_idx)
                    field = existing_fields.get(name)
                    mapping_kind = str(mapping.get("kind") or "")
                    response_path = str(mapping.get("response_path") or mapping.get("path") or "")
                    is_full_response = bool(
                        mapping_kind == "final_response"
                        and response_path in {"", "response", "$", "."}
                    )
                    if is_full_response:
                        # No captured response means its JSON type is unknown.
                        # Declaring ``object`` with no properties fabricates a
                        # contract and made callers assume fields that were never
                        # observed. Keep a valid unconstrained JSON Schema with
                        # explicit provenance until a real response is recorded.
                        fallback_props[name] = {
                            "description": "接口原始响应；录制未捕获可推导的响应结构",
                            "x-dano-untyped-response": True,
                        }
                    else:
                        fallback_props[name] = _schema_for_param_type(
                            field.type if field is not None else (
                                "object" if name in {"response", "raw", "detail"} else "string"
                            )
                        )
                if fallback_props:
                    cap.output_schema = reconcile_schema({
                        "type": "object",
                        "properties": fallback_props,
                        "required": [],
                    }, cap.output_schema or {})
        if cap.kind == "query_status":
            table_evidence = [
                item for item in (spec.meta.get("field_evidence") or [])
                if isinstance(item, dict)
                and (
                    item.get("kind") == "table_column"
                    or item.get("control_kind") == "table_column"
                )
                and any(
                    _recording_evidence_matches_request(step.source_meta or {}, item)
                    for step in cap_steps
                )
            ]
            sample_output = mapped_output_samples or next(
                (step.response_json for step in reversed(cap_steps) if step.response_json is not None),
                None,
            )
            _apply_output_presentation_evidence(
                cap.output_schema,
                table_evidence,
                sample_output=sample_output,
                input_schema=cap.input_schema,
                field_labels=recorded_field_labels,
            )
    _ground_recorded_identifier_relations(spec, by_id)
    return sync_capability_scoped_views(spec)


def _sanitize_capability_nodes(spec: FlowSpec, cap: FlowCapability) -> list[dict[str, Any]]:
    """Remove deterministically stale planner nodes before exposing validation warnings."""
    by_id = {step.step_id: step for step in spec.steps}
    cap_step_ids = set(cap.step_ids or [])
    is_batch = _capability_is_batch(spec, cap)
    batch_schema = _batch_capability_input_schema(
        [by_id[step_id] for step_id in cap.step_ids if step_id in by_id]
    ) if is_batch else {}
    batch_top_inputs = set((batch_schema.get("properties") or {}).keys())
    batch_item_inputs, _batch_item_required = _capability_schema_array_item_props(batch_schema, "entries")

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
                step_id = str(node.get("step_id") or "")
                usage = str(node.get("usage") or "")
                if step_id not in cap_step_ids and not (
                    usage in {"option_source", "fact_check"}
                    and (node.get("request_id") or node.get("path"))
                ):
                    continue
            elif node_type == "map":
                source = str(node.get("source") or "")
                target = str(node.get("target") or "")
                if not source or not target:
                    has_children = any(
                        isinstance(node.get(key), list) and node.get(key)
                        for key in ("children", "steps", "then", "else", "otherwise")
                    )
                    if has_children:
                        out.append(node)
                    continue
                if is_batch and source.startswith("input."):
                    # A batch capability exposes one top-level ``entries`` array.
                    # Older Planner output addressed row fields as input.<field>;
                    # migrate that reference only when the derived item schema
                    # proves the field exists, otherwise keep it for validation.
                    suffix = source.split(".", 1)[1]
                    field = suffix.split(".", 1)[0]
                    if field not in batch_top_inputs and field in batch_item_inputs:
                        node["source"] = f"item.{suffix}"
                        source = str(node["source"])
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
    evidence = {
        "step_id": step.step_id,
        "name": step.name,
        "method": (step.method or "").upper(),
        "path": step.path or step.url,
        "role": (step.source_meta or {}).get("role") or step.semantic_role,
    }
    for key in _REQUEST_OBSERVER_KEYS:
        value = (step.source_meta or {}).get(key)
        if value not in (None, ""):
            evidence[key] = value
    return evidence


def _is_write_step(step: FlowStep) -> bool:
    meta = step.source_meta or {}
    role = str(meta.get("role") or step.semantic_role or "").strip().lower()
    if role in {"business_get", "read_context", "read_option", "option_source", "explicit_read_option"}:
        return False
    if role in {"business_write", "submit_anchor"}:
        return True
    return (step.method or "").upper() not in {"GET", "HEAD", "OPTIONS"}


def _looks_batch_step(step: FlowStep) -> bool:
    meta = step.source_meta or {}
    if any(bool(meta.get(key)) for key in ("batch", "is_batch", "batch_intent", "repeated_submission")):
        return True
    text = f"{step.name} {step.path} {step.url} {meta.get('trigger_locator') or ''}".lower()
    if any(x in text for x in ("batch", "bulk", "批量")):
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


_ROUTING_FIELD_RE = re.compile(
    r"(?:approv|assignee|reviewer|audit|leader|manager|hr|cc|copy|审批|审核|领导|人力|抄送|经办)",
    re.I,
)




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
    schema_properties = dict((cap.input_schema or {}).get("properties") or {})
    if any(
        isinstance(schema_properties.get(name), dict)
        and schema_properties[name].get("x-dano-capability-owned") is True
        and schema_properties[name].get("x-dano-operator-owned") is True
        for name in ("entries", "items")
    ):
        return True
    if any(
        (field.key or field.path) in {"entries", "items"}
        # ``confirmed`` alone is not operator evidence: Planner patch ops can
        # emit confirmed fields.  Counting that as proof lets the Planner invent
        # entries and then use its own invention to keep a false submit_batch.
        and (field.locked or cap.updated_by == "user")
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






def _title_without_step_suffix(title: str) -> str:
    text = str(title or "").strip()
    text = re.sub(r"\s*[\(（]\s*\d+\s*步\s*[\)）]\s*$", "", text)
    return text.strip()


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
            container = response
            prefix = ""
            for wrapper in ("data", "result"):
                if isinstance(response.get(wrapper), dict):
                    container = response[wrapper]
                    prefix = f"{wrapper}."
                    break
            for field_name in list(container)[:20]:
                if not prefix and str(field_name).casefold() in {"code", "message", "msg", "success"}:
                    continue
                output_name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(field_name)).strip("_")
                if output_name.casefold() in {"list", "rows", "records"}:
                    output_name = "records"
                if len(container) == 1 and output_name.casefold() in {"value", "result", "data"}:
                    output_name = name
                if not output_name:
                    output_name = f"output_{len(semantic_paths) + 1}"
                semantic_paths.append((f"{prefix}{field_name}", output_name))
        if semantic_paths:
            for path, output_name in semantic_paths:
                mapping = {
                    "kind": "step_response",
                    "name": output_name,
                    "step_id": step.step_id,
                    "response_path": path,
                }
                # A response field has one stable public name. If several query
                # stages expose it, the later stage is the final observed result.
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


def _stable_capability_id(name: str, kind: str, step_ids: list[str]) -> str:
    raw = json.dumps([name, kind, list(step_ids)], ensure_ascii=False, separators=(",", ":"))
    return f"cap_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


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


def _repeated_write_command_signature(step: FlowStep) -> tuple[Any, ...] | None:
    """Identify one reusable command without relying on vendor path names."""
    if not _is_write_step(step):
        return None
    meta = step.source_meta or {}
    trigger_op = str(meta.get("trigger_op") or "").lower()
    if trigger_op not in {"click", "submit", "select", "pick"}:
        return None
    raw_path = str(step.path or step.url or "")
    return (
        (step.method or "GET").upper(),
        urlparse(raw_path).path or raw_path.split("?", 1)[0],
        tuple(sorted(param.path for param in step.params)),
        _write_command_discriminators(step),
        str(meta.get("page_id") or meta.get("page_url") or ""),
        str(meta.get("frame_id") or meta.get("frame_url") or ""),
        _locator_action_name(str(meta.get("trigger_locator") or "")).casefold(),
    )


_WRITE_COMMAND_DISCRIMINATOR_RE = re.compile(
    r"(?:^|[_-])(?:op|operation|action|command|event|intent|mode)(?:$|[_-])",
    re.I,
)


def _write_command_discriminators(step: FlowStep) -> tuple[tuple[str, str], ...]:
    """Keep RPC-style commands distinct while ignoring record-specific values."""
    try:
        body = _parse_body(step.body_source)
    except Exception:
        body = None
    found: list[tuple[str, str]] = []

    def visit(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                normalized_key = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).casefold()
                if (
                    _WRITE_COMMAND_DISCRIMINATOR_RE.search(normalized_key)
                    and isinstance(child, (str, int, float, bool))
                ):
                    found.append((path.casefold(), str(child).casefold()))
                else:
                    visit(child, path)
        elif isinstance(value, list):
            for child in value:
                visit(child, prefix)

    visit(body)
    return tuple(sorted(set(found)))


def _mark_repeated_write_observations(spec: FlowSpec) -> None:
    """Keep repeated facts/steps for audit, but execute one reusable command."""
    representatives: dict[tuple[Any, ...], FlowStep] = {}
    for step in spec.steps:
        signature = _repeated_write_command_signature(step)
        if signature is None:
            continue
        meta = step.source_meta or {}
        representative = representatives.get(signature)
        if representative is None:
            representatives[signature] = step
            continue
        step.source_meta = {
            **meta,
            "role": "duplicate_observation",
            "duplicate_observation_of": representative.step_id,
        }
        for capability in spec.capabilities or []:
            member_ids = set(_capability_node_step_ids(capability))
            if step.step_id not in member_ids:
                continue
            if representative.step_id in member_ids:
                capability.nodes = _remove_capability_step_nodes(
                    capability.nodes, step.step_id,
                )
            else:
                def replace_duplicate(nodes: list[dict[str, Any]]) -> None:
                    for node in nodes or []:
                        if not isinstance(node, dict):
                            continue
                        for key in ("step_id", "from", "source"):
                            if str(node.get(key) or "") == step.step_id:
                                node[key] = representative.step_id
                        for child_key in ("children", "steps", "then", "else", "otherwise"):
                            if isinstance(node.get(child_key), list):
                                replace_duplicate(node[child_key])

                replace_duplicate(capability.nodes)
            _sync_capability_order(spec, capability)
        request_ids = [
            request_id
            for request_id, usage in spec.request_facts.usage.items()
            if usage.materialized_step_id == step.step_id
        ]
        for request_id in request_ids:
            analysis = spec.request_facts.analysis.get(request_id)
            if analysis is None:
                continue
            analysis.role = "duplicate_observation"
            analysis.keep = False
            analysis.reason = (
                f"同一页面命令与接口契约的重复录制，复用步骤 {representative.step_id}"
            )


def _write_steps(spec: FlowSpec) -> list[FlowStep]:
    return [
        step for step in spec.steps
        if _is_write_step(step)
        and not (step.source_meta or {}).get("duplicate_observation_of")
    ]


def _option_source_step_ids(spec: FlowSpec) -> set[str]:
    ids: set[str] = set()
    urls: set[str] = set()
    request_ids: set[str] = set()
    for step in spec.steps:
        role = str((step.source_meta or {}).get("role") or step.semantic_role or "")
        read = {
            "url": step.url or step.path,
            "path": step.path,
            "method": step.method,
            "role": role,
            "response_json": step.response_json,
            **dict(step.source_meta or {}),
        }
        if (
            not _read_is_entity_enrichment_lookup(read)
            and (
                role == "read_option"
                or _is_option_source_url(step.path or step.url)
            )
        ):
            ids.add(step.step_id)
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
    if _is_write_step(step):
        return -100
    path = _request_path({"url": step.path or step.url}).lower()
    role = str((step.source_meta or {}).get("role") or step.semantic_role or "")
    if role in {"read_option", "option_source", "explicit_read_option"}:
        return -10
    if _INTERNAL_WORKFLOW_READ_RE.search(path):
        return -10
    if role != "business_get" and (
        re.search(
            r"(?:tenant|dict(?:ionary)?|options?|simple-list|departments?|roles?)",
            path,
        )
        or re.search(r"(?:^|/)(?:system|im)/users?(?:/|$)", path)
    ):
        return -10
    # An accepted recording-agent business_get classification is already the
    # semantic evidence required by the public capability gate.  Requiring a
    # second URL/DOM heuristic made valid non-REST search endpoints disappear
    # after materialization even though Pi had explicitly approved them.
    score = 3 if role == "business_get" else 0
    if _has_query_action_evidence(
        (step.source_meta or {}).get("trigger_op"),
        (step.source_meta or {}).get("trigger_locator"),
    ):
        score += 4
    if _BUSINESS_QUERY_PATH_RE.search(path):
        score += 2
    response = step.response_json
    if isinstance(response, list):
        score += 4
    if isinstance(response, dict):
        payload = response.get("data", response)
        if (
            isinstance(payload, dict)
            and _response_identity_match_count(step) > 0
        ):
            # A GET keyed by a stable record identity that returns the same
            # entity is independently callable business evidence.  Opening an
            # edit form may be how it was captured, but that does not make the
            # read endpoint merely an internal write preflight.
            score += 2
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
        if _is_write_step(st):
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
    option_source_ids = _option_source_step_ids(spec)
    # Option endpoints are data providers for SelectBinding/ParamField.  They
    # are not ordinary calls in the submit execution plan: adding them as a
    # preflight leaks their own filters (for example simple-list.status) into
    # the public submit contract and can make capability confirmation fail on
    # an enum that has nothing to do with the write.  A source that really
    # feeds a write through an explicit FlowLink is still retained by the
    # dependency closure rooted at ``write_ids`` below.
    dependency_ids = _dependency_closure_step_ids(spec, write_ids)
    preflight_ids = {
        st.step_id for st in spec.steps
        if bool((st.source_meta or {}).get("control_preflight_for_write"))
        and st.step_id not in option_source_ids
    }
    return _ordered_steps_by_ids(
        spec,
        _dependency_closure_step_ids(spec, dependency_ids | preflight_ids),
    )


def _schema_path_exists(schema: dict[str, Any] | None, path: str, key: str = "") -> bool:
    """Check aggregate paths such as entries[].sealId against JSON Schema."""
    return _schema_node_at_path(schema, str(path or key or "")) is not None


def _capability_step_allowed(spec: FlowSpec, cap: FlowCapability, step: FlowStep) -> bool:
    role = (step.source_meta or {}).get("role") or step.semantic_role or ""
    kind = (cap.kind or "").strip()
    membership = next((ref for ref in (cap.request_refs or []) if ref.step_id == step.step_id), None)
    # Explicit user membership is authoritative. Request role describes evidence,
    # not whether the same request may execute inside a capability.
    if membership and membership.origin in {"manual", "user"} and membership.usage in {"execute", "preflight", "fact_check"}:
        return True
    if (
        membership
        and membership.origin == "compiler"
        and membership.usage in {"execute", "preflight"}
        and any(
            item.get("source") == "grounded_request_graph"
            for item in (cap.evidence or [])
            if isinstance(item, dict)
        )
    ):
        return True
    if step.step_id in set(_capability_scoped_step_ids(cap)) and (
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
    if kind in WRITE_CAPABILITY_KINDS:
        closure_ids = {st.step_id for st in _submit_capability_steps(spec)}
        return step.step_id in closure_ids
    if kind == "query_status":
        status_ids = {st.step_id for st in _read_status_steps(spec)}
        return role != "read_option" and step.step_id in status_ids
    if kind == "list_options":
        return role == "read_option" or bool(step.selects)
    return role not in {"read_option", "read_context"}


def _add_step_id_to_capability(spec: FlowSpec, cap: FlowCapability, step_id: str) -> None:
    """Insert one call node in stable captured-step order."""
    if not step_id or step_id in _capability_call_step_ids_from_nodes(cap.nodes or []):
        return
    node = {
        "id": f"call_{len(_capability_call_step_ids_from_nodes(cap.nodes or [])) + 1}",
        "type": "call",
        "step_id": step_id,
    }
    if any(
        item.get("type") not in {"call", "return"}
        for item in (cap.nodes or [])
        if isinstance(item, dict)
    ):
        return_index = next(
            (
                index for index, item in enumerate(cap.nodes)
                if isinstance(item, dict) and item.get("type") == "return"
            ),
            len(cap.nodes),
        )
        cap.nodes.insert(return_index, node)
        return

    order = {step.step_id: index for index, step in enumerate(spec.steps)}
    new_order = order.get(step_id, 10_000)
    insert_at = next(
        (
            index for index, item in enumerate(cap.nodes or [])
            if (
                isinstance(item, dict)
                and item.get("type") == "call"
                and order.get(str(item.get("step_id") or ""), 10_000) > new_order
            )
        ),
        next(
            (
                index for index, item in enumerate(cap.nodes or [])
                if isinstance(item, dict) and item.get("type") == "return"
            ),
            len(cap.nodes or []),
        ),
    )
    cap.nodes.insert(insert_at, node)


def _set_capability_request_membership(
    spec: FlowSpec,
    cap: FlowCapability,
    step: FlowStep,
    *,
    usage: str,
    origin: str,
    extra_fields: dict[str, Any] | None = None,
) -> CapabilityRequestRef:
    current = next((ref for ref in (cap.request_refs or []) if ref.step_id == step.step_id), None)
    ref = _capability_request_ref_from_step(spec, step, current, extra=extra_fields)
    ref.usage = usage if usage in {"execute", "option_source", "fact_check", "preflight"} else "execute"
    ref.origin = origin or "manual"
    ref.confirmed = ref.origin in {"manual", "user"}
    cap.request_refs = [item for item in (cap.request_refs or []) if item.step_id != step.step_id]
    cap.request_refs.append(ref)
    return ref

_CAPABILITY_PATH_PREFIXES = frozenset({
    "api", "rest", "gateway", "openapi", "v1", "v2", "v3", "oa", "system", "admin", "admin-api",
})


def _capability_business_key(step: FlowStep) -> str:
    """Return a conservative business-domain key for automatic splitting.

    Explicit recorder/planner metadata wins. Otherwise only the first stable
    resource segment is used, so action endpoints inside one resource remain a
    single capability while genuinely separate domains can be partitioned.
    """
    meta = step.source_meta or {}
    explicit = str(meta.get("capability_key") or meta.get("business_domain") or "").strip()
    if explicit:
        return _flow_capability_id("domain", explicit).removeprefix("domain_")
    path = _request_path({"url": step.path or step.url}).lower()
    segments = [
        segment for segment in path.split("/")
        if segment and segment not in _CAPABILITY_PATH_PREFIXES and not re.fullmatch(r"\d+", segment)
    ]
    domain = _flow_capability_id("domain", segments[0]).removeprefix("domain_") if segments else ""
    # Trigger evidence is useful for explaining and validating the chain, but
    # must not be a hard partition key. One business capability routinely has
    # several buttons (query/add/submit); hashing each locator fragmented it
    # into artificial capabilities and made the first split hard to recover.
    return domain


def _query_operation_key(step: FlowStep) -> str:
    """Group all requests caused by one visible query command.

    The endpoint domain is not an operation boundary: one click can trigger a
    page query plus counters/statistics, while two different buttons in the
    same domain can represent independently callable query/detail operations.
    Locator identity is therefore used only for read capabilities and only
    when Observer proves a command anchor.
    """
    business = _capability_business_key(step)
    operation_kind = _capability_operation_kind(step)
    meta = step.source_meta or {}
    action_ref = str(
        meta.get("trigger_transaction_id") or meta.get("trigger_action_id") or ""
    ).strip()
    locator = re.sub(r"\s+", "", str(meta.get("trigger_locator") or "").casefold())
    anchored = bool(
        (action_ref or locator)
        and str(meta.get("trigger_op") or "").lower() in {"click", "submit"}
        and str(meta.get("causality_confidence") or "high").lower() in {"high", "medium"}
    )
    if not anchored:
        return (
            "__".join(part for part in (business, operation_kind) if part)
            if operation_kind != "query_status"
            else business
        )
    page_scope = str(
        meta.get("page_id") or meta.get("page_url") or meta.get("document_url") or ""
    )
    action_identity = "|".join((page_scope, action_ref, locator))
    action_key = hashlib.sha1(action_identity.encode("utf-8")).hexdigest()[:10]
    return f"action_{action_key}"


def _response_identity_match_count(step: FlowStep) -> int:
    """Count request identity leaves echoed by the captured response."""
    identity_keys = {
        "id", "recordid", "requestid", "applicationid", "businessid",
        "entityid", "itemid", "instanceid", "processinstanceid",
    }
    requested: dict[str, set[str]] = {}
    for param in step.params or []:
        leaf = re.split(r"[.\[\]]+", str(param.path or param.key or ""))[-1]
        key = re.sub(r"[^a-z0-9]+", "", leaf.casefold())
        if key not in identity_keys or param.value in (None, ""):
            continue
        values = param.value if isinstance(param.value, list) else [param.value]
        requested.setdefault(key, set()).update(
            str(value) for value in values if value not in (None, "")
        )
    if not requested:
        return 0

    matches: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = re.sub(r"[^a-z0-9]+", "", str(raw_key).casefold())
                if (
                    key in requested
                    and not isinstance(child, (dict, list))
                    and str(child) in requested[key]
                ):
                    matches.add(key)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(step.response_json)
    return len(matches)


def _primary_read_operation_step(steps: list[FlowStep]) -> FlowStep:
    """Choose the business result of one recorded command, not its first helper call."""
    if not steps:
        raise ValueError("read operation requires at least one step")

    def command_result_semantic_score(step: FlowStep) -> int:
        """Prefer the response shape named by the visible command."""
        meta = step.source_meta or {}
        command = " ".join((
            str(meta.get("trigger_locator") or ""),
            str(meta.get("trigger_op") or ""),
        )).casefold()
        families = (
            (
                r"(?:进度|流程|审批|节点|progress|workflow|process|approval|timeline)",
                (
                    "progress", "workflow", "process", "approval", "task",
                    "node", "activity", "bpmn", "timeline", "comment",
                    "finished", "unfinished", "rejected", "status",
                    "进度", "流程", "审批", "任务", "节点", "意见", "状态",
                ),
            ),
            (
                r"(?:评论|意见|备注|comment|opinion|remark)",
                ("comment", "opinion", "remark", "message", "评论", "意见", "备注"),
            ),
        )
        terms = next((values for pattern, values in families if re.search(pattern, command)), ())
        if not terms:
            return 0
        observed: list[str] = [str(step.path or step.url or "").casefold()]

        def collect_keys(value: Any, depth: int = 0) -> None:
            if depth > 4:
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    observed.append(str(key).casefold())
                    collect_keys(child, depth + 1)
            elif isinstance(value, list):
                for child in value[:3]:
                    collect_keys(child, depth + 1)

        collect_keys(step.response_json)
        return sum(
            1 for term in terms
            if any(term in value for value in observed)
        )

    def page_path_overlap(step: FlowStep) -> int:
        meta = step.source_meta or {}
        page_url = str(
            (meta.get("trigger_page_context") or {}).get("url")
            or meta.get("page_url")
            or meta.get("document_url")
            or ""
        )

        def segments(value: str) -> set[str]:
            return {
                segment
                for segment in re.split(
                    r"[^a-z0-9\u4e00-\u9fff]+",
                    urlparse(value).path.casefold(),
                )
                if segment
                and segment not in _CAPABILITY_PATH_PREFIXES
                and segment not in {"get", "list", "page", "detail", "query"}
                and len(segment) > 1
            }

        return len(
            segments(page_url) & segments(str(step.path or step.url or ""))
        )

    def score(step: FlowStep) -> tuple[int, int, int, int, int, int]:
        response = step.response_json
        payload = response.get("data") if isinstance(response, dict) else response
        entity_payload = int(isinstance(payload, dict) and not any(
            isinstance(payload.get(key), list)
            for key in ("list", "records", "rows", "items")
        ))
        role = str((step.source_meta or {}).get("role") or step.semantic_role or "")
        return (
            command_result_semantic_score(step),
            _response_identity_match_count(step),
            page_path_overlap(step),
            entity_payload,
            int(role == "business_get"),
            _business_query_evidence_score(step),
        )

    return max(steps, key=score)


def _grounded_read_operation_steps(
    spec: FlowSpec, anchor: FlowStep,
) -> list[FlowStep]:
    """Return every captured read caused by the anchor's visible command."""
    operation_key = _query_operation_key(anchor)
    if not operation_key.startswith("action_"):
        return [anchor]
    members: list[FlowStep] = []
    for step in spec.steps:
        role = str((step.source_meta or {}).get("role") or step.semantic_role or "").lower()
        if (
            _is_write_step(step)
            or role in {"auth", "noise", "duplicate_observation"}
            or str((step.source_meta or {}).get("duplicate_observation_of") or "")
        ):
            continue
        if _query_operation_key(step) == operation_key:
            members.append(step)
    return members or [anchor]


def _write_operation_key(step: FlowStep) -> str:
    """Group one reusable write command, not one recorded row click.

    Per-click action/transaction ids and row wrappers in the locator are
    samples of the same business operation.  Similar commands stay distinct
    when the HTTP contract, visible action name, or command discriminators
    differ.
    """
    meta = step.source_meta or {}
    raw_path = str(step.path or step.url or "")
    identity = "|".join((
        str(meta.get("page_id") or meta.get("page_url") or meta.get("document_url") or ""),
        str(meta.get("frame_id") or meta.get("frame_url") or ""),
        str(step.method or "POST").upper(),
        urlparse(raw_path).path or raw_path.split("?", 1)[0],
        _capability_operation_kind(step),
        _locator_action_name(str(meta.get("trigger_locator") or "")).casefold(),
        ",".join(sorted(str(param.path or "") for param in step.params or [])),
        json.dumps(_write_command_discriminators(step), ensure_ascii=False),
    ))
    return f"write_{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:10]}"








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


def _capability_relation_schemas_compatible(source: dict[str, Any], target: dict[str, Any]) -> bool:
    if not _capability_types_compatible(str(source.get("type") or ""), str(target.get("type") or "")):
        return False
    if source.get("type") == target.get("type") == "array":
        source_items = source.get("items") if isinstance(source.get("items"), dict) else {}
        target_items = target.get("items") if isinstance(target.get("items"), dict) else {}
        return _capability_relation_schemas_compatible(source_items, target_items)
    return True


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
        evidence_kind = str((relation.evidence or {}).get("kind") or "").strip().lower()
        if evidence_kind in {"user_confirmed", "manual", "manual_relation"}:
            return True
        if not _capability_relation_requires_fields(relation):
            return True
        if source is None or target is None:
            return bool(relation.confirmed and evidence_kind != "typed_capability_contract")
        source_field = _schema_node_at_path(source.output_schema, relation.from_output)
        target_field = _schema_node_at_path(target.input_schema, relation.to_input)
        if not (
            relation.from_output
            and relation.to_input
            and isinstance(source_field, dict)
            and isinstance(target_field, dict)
        ):
            return bool(relation.confirmed and evidence_kind != "typed_capability_contract")
        if evidence_kind == "typed_capability_contract":
            return _capability_relation_schemas_compatible(source_field, target_field)
        # Keep an explicit, resolvable relation so the validation report can
        # surface a type mismatch instead of silently deleting user intent.
        return True

    spec.capability_relations = [
        relation for relation in spec.capability_relations if relation_is_valid(relation)
    ]
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




def _step_page_id_from_facts(spec: FlowSpec, step: FlowStep) -> str:
    meta = step.source_meta or {}
    if meta.get("page_id"):
        return str(meta["page_id"])
    request_id = str(meta.get("request_id") or "")
    request_index = meta.get("request_index")
    for fact in spec.request_facts.requests or []:
        if request_id and str(fact.request_id or "") == request_id:
            return str(fact.page_id or "")
        if request_index is not None and fact.request_index == request_index:
            return str(fact.page_id or "")
    return ""
















def _orchestration_context(spec: FlowSpec) -> dict[str, Any]:
    request_facts = _request_fact_items(spec)
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
                "request_refs": [
                    ref.model_dump(exclude_none=True)
                    for ref in (cap.request_refs or [])
                ],
                "input_schema": cap.input_schema or {},
                "output_schema": cap.output_schema or {},
                "output_mapping": list(cap.output_mapping or []),
                "fields": [
                    _capability_field_summary(field)
                    for field in [

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
            st.step_id: normalized_leaf_paths(st.response_json)
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
                        "source_kind": p.source_kind,
                    }
                    for p in (st.params or [])[:80]
                ],
                "response_paths": normalized_leaf_paths(st.response_json, max_paths=80),
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
            for r in request_facts[:120]
        ],
        "captured_request_count": len(request_facts),
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


def _retired_capability_step_ids(spec: FlowSpec | None) -> set[str]:
    if spec is None:
        return set()
    removed = (spec.meta or {}).get("capability_removed_steps") or {}
    removed_step_ids = {
        str(ref).removeprefix("step:")
        for refs in removed.values()
        for ref in (refs or [])
        if str(ref).startswith("step:")
    }
    active_step_ids = {
        step_id
        for capability in (spec.capabilities or [])
        for step_id in _capability_node_step_ids(capability)
    }
    return removed_step_ids - active_step_ids


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
    # Only the legacy single/batch submit pair is interchangeable. Draft,
    # submit, withdraw and delete are separate caller-visible operations.
    return "write" if kind in {"submit", "submit_batch"} else str(kind or "")


def _planned_capability_has_public_anchor(
    spec: FlowSpec,
    kind: str,
    planned_step_ids: list[str],
) -> bool:
    """Only user-callable business actions may create public capabilities."""
    by_id = {step.step_id: step for step in spec.steps}
    option_ids = _option_source_step_ids(spec)
    for step_id in planned_step_ids:
        step = by_id.get(step_id)
        if step is None:
            continue
        # `/list` is common to both business searches and option endpoints.
        # Strong recorded business-query evidence wins over the URL heuristic.
        if step_id in option_ids and kind in READ_CAPABILITY_KINDS:
            recorded_role = str(
                (step.source_meta or {}).get("role") or step.semantic_role or ""
            )
            if recorded_role != "business_get":
                continue
        grounded_kind = _capability_operation_kind(step)
        if kind in WRITE_CAPABILITY_KINDS and grounded_kind in WRITE_CAPABILITY_KINDS:
            return True
        if kind in READ_CAPABILITY_KINDS and grounded_kind in READ_CAPABILITY_KINDS and _is_business_query_step(step):
            return True
    return False




def _semantic_plan_coverage(spec: FlowSpec, result: dict[str, Any]) -> dict[str, Any]:
    plan = result.get("semantic_plan") or result.get("plan")
    if not isinstance(plan, dict):
        return {
            "complete": False,
            "missing": ["semantic_plan"],
            "covered_steps": 0,
            "covered_fields": 0,
        }
    step_by_id = {step.step_id: step for step in spec.steps}
    allowed_usages = {"execute", "preflight", "option_source", "fact_check"}

    capability_items = [
        item for item in (plan.get("capabilities") or []) if isinstance(item, dict)
    ]
    referenced_step_ids = {
        str(ref.get("step_id") or "")
        for capability in capability_items
        for ref in (
            capability.get("request_refs")
            if isinstance(capability.get("request_refs"), list)
            else []
        )
        if isinstance(ref, dict) and str(ref.get("step_id") or "")
    }

    # Public ability count is the number of distinct recorded business actions,
    # not the number of HTTP writes. One click can execute several preflight/
    # write requests, while two independently anchored actions must never be
    # merged merely because their URLs share a domain.
    required_fields = [
        (step.step_id, param)
        for step in spec.steps
        if step.step_id in referenced_step_ids
        for param in step.params
    ]

    def field_contract_complete(param: ParamField) -> bool:
        return bool(
            str(param.path or "").strip()
            and str(param.label or param.key or "").strip()
            and str(param.type or "").strip().lower() not in {"", "unknown"}
            and str(param.category or "").strip().lower() not in {"", "unknown"}
            and bool(str(param.source_kind or "").strip())
            and _field_source_configuration_advice(param) is None
            and isinstance(param.required, bool)
        )

    covered_fields = {
        (step_id, param.path)
        for step_id, param in required_fields
        if field_contract_complete(param)
    }
    covered_steps: set[str] = set()
    names: set[str] = set()
    anchors: set[str] = set()
    capability_contract_invalid = False
    for capability in capability_items:
        name = str(capability.get("name") or "").strip()
        title = str(capability.get("title") or "").strip()
        kind = str(capability.get("kind") or "").strip()
        anchor_step_id = str(capability.get("anchor_step_id") or "").strip()
        refs = capability.get("request_refs")
        valid_refs = bool(
            isinstance(refs, list)
            and refs
            and all(
                isinstance(ref, dict)
                and str(ref.get("step_id") or "") in step_by_id
                and str(ref.get("usage") or "") in allowed_usages
                for ref in refs
            )
        )
        anchor_ref = bool(
            valid_refs
            and any(
                str(ref.get("step_id") or "") == anchor_step_id
                and str(ref.get("usage") or "") == "execute"
                for ref in refs
            )
        )
        valid = bool(
            name and title and kind in ALLOWED_CAPABILITY_KINDS
            and anchor_step_id in step_by_id
            and valid_refs and anchor_ref
            and name not in names and anchor_step_id not in anchors
            and _planned_capability_has_public_anchor(spec, kind, [anchor_step_id])
        )
        if not valid:
            capability_contract_invalid = True
            continue
        names.add(name)
        anchors.add(anchor_step_id)
        covered_steps.add(anchor_step_id)
    missing: list[str] = []
    if any(
        _eligible_business_write_fact(item)
        and not _materialized_step_id_for_request(spec, item)
        for item in _request_fact_items(spec)
    ):
        missing.append("request_materialization")
    if len(covered_fields) != len(required_fields):
        missing.append("field_axis_contract")
    if not capability_items:
        missing.append("capabilities")
    elif capability_contract_invalid:
        missing.append("capability_contracts")
    understanding = plan.get("business_understanding")
    if not isinstance(understanding, dict) or not any(
        str(understanding.get(key) or "").strip()
        for key in ("business_name", "summary", "intent", "object", "purpose")
    ):
        missing.append("business_understanding")
    unresolved_items = plan.get("unresolved_items", [])
    if not isinstance(unresolved_items, list) or any(
        not isinstance(item, dict)
        or item.get("blocking") is True
        or str(item.get("severity") or "").strip().lower()
        in {"high", "critical", "blocker", "error"}
        for item in (unresolved_items if isinstance(unresolved_items, list) else [])
    ):
        missing.append("unresolved_blockers")
    return {
        "complete": not missing,
        "missing": missing,
        "covered_steps": len(covered_steps),
        "total_steps": len(capability_items),
        "covered_fields": len(covered_fields),
        "total_fields": len(required_fields),
    }


def _pre_materialization_semantic_plan_coverage(
    spec: FlowSpec,
    semantic_plan: dict[str, Any],
    fact_request_ids: set[str],
) -> dict[str, Any]:
    """Validate a strict live plan before request facts become FlowSteps."""
    capability_items = [
        item for item in semantic_plan.get("capabilities") or []
        if isinstance(item, dict)
    ]
    missing: list[str] = []
    names: set[str] = set()
    anchors: set[str] = set()
    for capability in capability_items:
        name = str(capability.get("name") or "").strip()
        title = str(capability.get("title") or "").strip()
        kind = str(capability.get("kind") or "").strip()
        anchor = str(capability.get("anchor_step_id") or "").strip()
        refs = capability.get("request_refs")
        execute_refs = [
            ref for ref in (refs if isinstance(refs, list) else [])
            if isinstance(ref, dict) and str(ref.get("usage") or "") == "execute"
        ]
        valid = bool(
            name and title and kind in ALLOWED_CAPABILITY_KINDS
            and anchor in fact_request_ids
            and name not in names and anchor not in anchors
            and isinstance(refs, list) and refs
            and all(
                isinstance(ref, dict)
                and str(ref.get("step_id") or "") in fact_request_ids
                and str(ref.get("usage") or "")
                in {"execute", "preflight", "option_source", "fact_check"}
                for ref in (refs or [])
            )
            and len(execute_refs) == 1
            and str(execute_refs[0].get("step_id") or "") == anchor
        )
        if not valid:
            missing.append("capability_contracts")
            break
        names.add(name)
        anchors.add(anchor)

    if not capability_items:
        missing.append("capabilities")
    from dano.execution.page.recording_live import _recording_goal_contract

    expected_count = int(_recording_goal_contract(spec).get("expected_count") or 0)
    if expected_count and len(capability_items) != expected_count:
        missing.append("goal_capability_count")
    understanding = semantic_plan.get("business_understanding")
    if not isinstance(understanding, dict) or not any(
        str(understanding.get(key) or "").strip()
        for key in ("business_name", "summary", "intent", "object", "purpose")
    ):
        missing.append("business_understanding")
    unresolved_items = semantic_plan.get("unresolved_items", [])
    if not isinstance(unresolved_items, list) or any(
        not isinstance(item, dict)
        or item.get("blocking") is True
        or str(item.get("severity") or "").strip().lower()
        in {"high", "critical", "blocker", "error"}
        for item in (unresolved_items if isinstance(unresolved_items, list) else [])
    ):
        missing.append("unresolved_blockers")
    missing = list(dict.fromkeys(missing))
    return {
        "complete": not missing,
        "missing": missing,
        "covered_steps": len(anchors),
        "total_steps": expected_count or len(capability_items),
        "covered_fields": 0,
        "total_fields": 0,
        "phase": "request_facts",
    }


def _public_capability_anchor_step_ids(spec: FlowSpec) -> list[str]:
    """Derive the complete public action set from recorder-owned facts."""
    write_groups: dict[str, list[FlowStep]] = {}
    write_steps = [
        step for step in spec.steps
        if _is_write_step(step)
        and not str((step.source_meta or {}).get("duplicate_observation_of") or "")
        and str((step.source_meta or {}).get("role") or step.semantic_role or "").lower()
        not in {"auth", "noise", "read_context", "read_option", "option_source"}
    ]
    for step in write_steps:
        write_groups.setdefault(_write_operation_key(step), []).append(step)

    anchors = [steps[-1].step_id for steps in write_groups.values() if steps]
    submit_closure = {
        step.step_id for step in _submit_capability_steps(spec)
    } if write_steps else set()
    read_groups: dict[str, list[FlowStep]] = {}
    for step in _read_status_steps(spec):
        meta = step.source_meta or {}
        independently_triggered = _has_query_action_evidence(
            meta.get("trigger_op"), meta.get("trigger_locator"),
        )
        independently_grounded = _business_query_evidence_score(step) >= 5
        if (
            meta.get("record_hydration_for_write_ids")
            and not independently_triggered
            and not independently_grounded
        ):
            continue
        if step.step_id not in submit_closure or independently_triggered or independently_grounded:
            read_groups.setdefault(_query_operation_key(step), []).append(step)
    # One visible command is one public read ability even when it fans out to
    # record, workflow, user and statistics endpoints. Its full request group
    # is attached later by the Skill-submitted semantic plan.
    anchors.extend(
        _primary_read_operation_step(steps).step_id
        for steps in read_groups.values() if steps
    )
    order = {step.step_id: index for index, step in enumerate(spec.steps)}
    return sorted(dict.fromkeys(anchors), key=lambda step_id: order.get(step_id, 10**9))




def _is_technical_business_title(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    normalized = re.sub(r"[\s_-]+", "", text.lower())
    endpoint_action = re.search(
        r"(?:^|[/_.-])(?:get|list|page|query|search|submit|save|create|update|delete|"
        r"cancel|withdraw|approve|reject|start|process)(?:[/_.-]|$)",
        text,
        re.I,
    )
    endpoint_flow_title = bool(
        re.search(r"[A-Za-z]", text)
        and (
            endpoint_action
            or re.search(r"[/_-]", text)
            or re.search(r"[a-z][A-Z]", text)
        )
        and re.search(r"流程\s*[（(]?\s*\d*\s*步?", text)
    )
    return bool(
        endpoint_flow_title
        or re.search(
            r"(?:查询|提交|执行|处理)?\s*(?:get|post|put|patch|delete|cancel|withdraw)"
            r"(?:[-_/]|[A-Z])",
            text,
            re.I,
        )
        or re.match(r"^(?:get|post|put|patch|delete)", normalized)
        or normalized in {
            "流程", "业务流程", "提交流程", "submit", "submitprocess",
            "录制业务", "录制业务流程", "提交业务申请", "查询流程状态", "未命名",
        }
        or re.fullmatch(r"(?:capability|能力)\d*", normalized)
        or re.fullmatch(r"submitprocess流程(?:\(\d+步\))?", normalized)
        or bool(re.fullmatch(r"(?:action|sk)[_-]?[0-9a-f]{8,}", text, re.I))
    )


_GENERIC_PAGE_TITLE_RE = re.compile(
    r"^(?:OA\s*)?(?:管理)?(?:平台|系统|工作台|首页|业务平台|办公平台|管理系统)$|"
    r"^(?:申请|查询|搜索|筛选|基本|详细|更多)?信息$|^(?:申请|查询|搜索)条件$|"
    r"^(?:确定|取消|关闭|新增|编辑|详情|操作|撤回成功|提交成功)$",
    re.I,
)


def _clean_page_business_candidate(value: Any) -> str:
    """Normalize one visible heading without guessing a business domain."""
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -_|—·>/»›")
    if not text:
        return ""
    # Breadcrumb containers are sometimes captured as one string.  Preserve
    # the terminal business crumb and discard navigation chrome.
    chunks = [part.strip() for part in re.split(r"\s*(?:/|>|»|›|→|\||—| - )\s*", text) if part.strip()]
    if chunks:
        text = chunks[-1]
    for prefix in ("当前位置", "系统首页", "管理首页", "工作台首页", "首页"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix):].strip(" -_|—·>/»›")
    text = re.sub(r"\s*[（(]\s*\d+\s*[）)]\s*$", "", text).strip()
    if not text or len(text) > 40 or _GENERIC_PAGE_TITLE_RE.fullmatch(text):
        return ""
    if re.search(r"(?:管理平台|管理系统|业务平台|办公平台)$", text):
        text = re.sub(r"(?:管理平台|管理系统|业务平台|办公平台)$", "", text).strip()
        if not text:
            return ""
    if _is_technical_business_title(text):
        return ""
    return text


def _page_context_business_name_from_contexts(contexts: list[dict[str, Any]]) -> str:
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for context in contexts:
        if not isinstance(context, dict):
            continue
        document_title = str(context.get("document_title") or "").strip()
        candidates = [*(context.get("visible_titles") or []), document_title]
        for position, raw in enumerate(candidates):
            text = _clean_page_business_candidate(raw)
            if not text or text in seen:
                continue
            seen.add(text)
            score = 0
            if raw == document_title:
                score += 3
            if 2 <= len(text) <= 20:
                score += 2
            if re.search(r"[\u4e00-\u9fff]", text):
                score += 1
            if re.search(r"管理|平台|系统|首页|工作台", text):
                score -= 4
            ranked.append((score, -position, text))
    best = max(ranked, default=(0, 0, ""))
    return best[2] if best[0] > 0 else ""


def _capability_text_is_placeholder(value: str, capability: FlowCapability) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    normalized = re.sub(r"[\s_-]+", "", text.casefold())
    capability_name = re.sub(r"[\s_-]+", "", str(capability.name or "").casefold())
    return bool(
        normalized == capability_name
        or re.fullmatch(r"(?:capability|能力)\d*", normalized)
        or _is_technical_business_title(text)
    )


_GENERIC_CAPABILITY_INTENT_RE = re.compile(
    r"(?:查询流程、审批或上下文详情|调用方提供业务字段|"
    r"按录制顺序调用\s*\d+\s*个真实接口|根据调用方提供的条件调用已录制查询接口|"
    r"按已纳入接口顺序执行前置查询)",
)


def _capability_intent_needs_refresh(value: str, capability: FlowCapability) -> bool:
    text = str(value or "").strip()
    return bool(
        not text
        or text == str(capability.title or "").strip()
        or _is_technical_business_title(text)
        or _GENERIC_CAPABILITY_INTENT_RE.search(text)
    )


def _locator_action_name(locator: str) -> str:
    text = str(locator or "").strip()
    match = re.search(r"\[name=([^\]]+)\]", text)
    if match:
        return match.group(1).strip(" '\"")
    text_matches = re.findall(r"(?:^|[\s>])text=([^\s>\]]+)", text)
    if text_matches:
        return text_matches[-1].strip(" '\"")
    if text.startswith("text="):
        return text[5:].strip(" '\"")
    if "=" in text:
        prefix, value = text.split("=", 1)
        if prefix.strip().lower() in {"button", "role", "label", "name"}:
            return value.strip(" '\"")
    return next((label for label in _ACTION_LABELS if label in text), "")


_ACTION_LABELS = (
    "撤回", "撤销", "作废", "取消", "删除", "驳回", "同意", "审批",
    "提交", "保存", "新增", "创建", "更新", "编辑", "导出", "查询", "搜索",
)


def _capability_operation_kind(step: FlowStep) -> str:
    """Infer a public business operation from grounded request/action evidence."""
    method = str(step.method or "GET").upper()
    meta = step.source_meta or {}
    locator = _locator_action_name(str(meta.get("trigger_locator") or "")).casefold()
    signature = " ".join((
        locator,
        str(step.name or ""),
        _request_path({"url": step.path or step.url}),
    )).casefold()
    is_query_action = _has_query_action_evidence(
        meta.get("trigger_op"),
        str(meta.get("trigger_locator") or ""),
    )
    if (
        method in {"GET", "HEAD"}
        or str(meta.get("role") or step.semantic_role or "") == "business_get"
    ):
        if re.search(r"(?:^|[/_.\s-])(?:export|download|excel)(?:$|[/_.\s-])|导出|下载", signature):
            return "export"
        if re.search(
            r"(?:detail|inspect|view|progress)|"
            r"(?:^|/)(?:get)(?:$|[/?#])|"
            r"(?:^|/)(?:list|page)-by-[^/?#]*(?:id|key)(?:$|[/?#])|"
            r"详情|查看|进度",
            signature,
        ):
            return "inspect"
        if re.search(r"(?:preview)|预览", signature):
            return "preview"
        if is_query_action:
            return "query_status"
        return "query_status"
    # Specific business verbs must win over generic edit/update markers.
    if re.search(r"(?:withdraw|revoke)|撤回|撤销", signature):
        return "withdraw"
    if re.search(r"(?:delete|remove)|删除", signature):
        return "delete"
    if re.search(r"(?:reject)|驳回", signature):
        return "reject"
    if re.search(r"(?:approve|approval|pass)|审批|同意|通过", signature):
        return "approve"
    context_url = str((meta.get("trigger_page_context") or {}).get("url") or "")
    context_ids = {
        str(value)
        for values in parse_qs(urlparse(context_url).query).values()
        for value in values
        if value not in (None, "") and not _is_missing_wire_placeholder(value)
    }
    request_ids = {
        str(param.value)
        for param in step.params or []
        if re.sub(
            r"[^a-z0-9]+", "",
            str(param.path or param.key).split(".")[-1].casefold(),
        ) == "id"
        and param.value not in (None, "")
        and not _is_missing_wire_placeholder(param.value)
    }
    editable_business_fields = [
        param for param in step.params or []
        if param.exposed_to_user
        and param.source_kind == "user_input"
        and re.sub(
            r"[^a-z0-9]+", "",
            str(param.path or param.key).split(".")[-1].casefold(),
        ) != "id"
    ]
    if context_ids & request_ids and editable_business_fields:
        # Some systems reuse a submit-looking endpoint when an existing record
        # is edited. The selected identity plus caller-edited business fields
        # is stronger operation evidence than that route name.
        return "update"
    if re.search(r"(?:submit|commit)|提交", signature):
        return "submit"
    if re.search(r"(?:draft|save-draft)|草稿|暂存", signature):
        return "save_draft"
    if re.search(r"(?:create|insert|add)|新增|创建", signature):
        return "create"
    if re.search(r"(?:update|edit|modify)|更新|编辑|保存", signature):
        return "update"
    return "submit"


_INSTANCE_TITLE_SUFFIX_RE = re.compile(
    r"\s*[\(（]\s*(?:ID|id|编号|单号|No\.?|NO)\s*[:：#]?\s*[^)）]+[\)）]\s*$"
)


def _generalize_capability_title(title: str) -> str:
    """Drop recorded row samples from public capability titles."""
    return _INSTANCE_TITLE_SUFFIX_RE.sub("", str(title or "")).strip()


def _ensure_capability_explanations(
    spec: FlowSpec,
    semantic_plan: dict[str, Any] | None = None,
) -> FlowSpec:
    """Copy Skill-authored copy onto compiled capabilities; do not invent it."""
    plan_items = [
        item for item in ((semantic_plan or {}).get("capabilities") or [])
        if isinstance(item, dict)
    ]
    plan_by_name = {
        str(item.get("name") or ""): item for item in plan_items
        if str(item.get("name") or "")
    }

    def planned_for(capability: FlowCapability) -> dict[str, Any]:
        exact = plan_by_name.get(capability.name)
        if exact is not None:
            return exact
        cap_steps = set(_capability_node_step_ids(capability))
        scored = [
            (
                len(cap_steps & {
                    str(ref.get("step_id") or "")
                    for ref in (item.get("request_refs") or [])
                    if isinstance(ref, dict)
                }),
                item,
            )
            for item in plan_items
        ]
        if not scored:
            return {}
        top_score = max(score for score, _item in scored)
        if top_score <= 0:
            return {}
        top = [item for score, item in scored if score == top_score]
        return top[0] if len(top) == 1 else {}

    for capability in spec.capabilities or []:
        if capability.locked or capability.updated_by == "user":
            continue
        planned = planned_for(capability)
        planned_title = str(planned.get("title") or "").strip()
        if _capability_text_is_placeholder(capability.title, capability):
            capability.title = (
                _generalize_capability_title(planned_title)
                if planned_title and not _capability_text_is_placeholder(planned_title, capability)
                else (capability.name or capability.kind)
            )
        else:
            capability.title = _generalize_capability_title(capability.title) or capability.title
        planned_intent = str(planned.get("intent") or planned.get("description") or "").strip()
        if _capability_intent_needs_refresh(capability.intent, capability):
            capability.intent = planned_intent or capability.title or capability.name
    return spec


def _page_context_business_name(spec: FlowSpec) -> str:
    contexts = [dict((spec.meta or {}).get("page_context") or {})]
    for step in spec.steps or []:
        meta = step.source_meta or {}
        for key in ("trigger_page_context", "page_context"):
            value = meta.get(key)
            if isinstance(value, dict) and value:
                contexts.append(dict(value))
    return _page_context_business_name_from_contexts(contexts)


def _apply_semantic_business_understanding(
    spec: FlowSpec,
    semantic_plan: dict[str, Any],
) -> FlowSpec:
    """Apply Skill-authored business identity without inventing titles."""
    understanding = semantic_plan.get("business_understanding")
    understanding = understanding if isinstance(understanding, dict) else {}
    title_source = str((spec.meta or {}).get("title_source") or "")
    model_title = _clean_page_business_candidate(
        understanding.get("business_name") or understanding.get("object") or ""
    )
    page_title = _page_context_business_name(spec)
    if title_source != "user":
        if model_title:
            spec.title = model_title
            spec.meta = {**(spec.meta or {}), "title_source": "semantic_plan"}
        elif _is_technical_business_title(spec.title) and page_title:
            spec.title = page_title
            spec.meta = {**(spec.meta or {}), "title_source": "page_context"}
    description_source = str((spec.meta or {}).get("business_description_source") or "")
    proposed_description = str(
        understanding.get("summary") or understanding.get("intent") or ""
    ).strip()
    if description_source != "user" and proposed_description:
        spec.business_description = proposed_description
        spec.meta = {**(spec.meta or {}), "business_description_source": "semantic_plan"}
    return _ensure_capability_explanations(spec, semantic_plan)


def _complete_semantic_plan_from_spec(
    spec: FlowSpec,
    proposed: dict[str, Any] | None,
) -> dict[str, Any]:
    """Persist the same strict semantic contract exposed by the Pi tool."""
    proposed_plan = copy.deepcopy(proposed) if isinstance(proposed, dict) else {}
    plan: dict[str, Any] = {}
    understanding = proposed_plan.get("business_understanding")
    if not isinstance(understanding, dict):
        understanding = {}
    business_name = str(
        _clean_page_business_candidate(understanding.get("business_name"))
        or ("" if _is_technical_business_title(spec.title) else spec.title)
        or _page_context_business_name(spec)
        or ""
    ).strip()
    if _is_technical_business_title(str(understanding.get("business_name") or "")):
        understanding["business_name"] = business_name
    else:
        understanding.setdefault("business_name", business_name)
    plan["business_understanding"] = understanding

    def strict_refs(raw_refs: Any) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        for raw in raw_refs if isinstance(raw_refs, list) else []:
            if not isinstance(raw, dict):
                continue
            step_id = str(raw.get("step_id") or "")
            usage = str(raw.get("usage") or "")
            if (
                step_id
                and any(step.step_id == step_id for step in spec.steps)
                and usage in {"execute", "preflight", "option_source", "fact_check"}
                and {"step_id": step_id, "usage": usage} not in refs
            ):
                refs.append({"step_id": step_id, "usage": usage})
        return refs

    capability_by_name: dict[str, dict[str, Any]] = {}
    for raw in proposed_plan.get("capabilities") or []:
        if not isinstance(raw, dict) or not str(raw.get("name") or ""):
            continue
        item = {
            "name": str(raw.get("name") or ""),
            "title": str(raw.get("title") or raw.get("name") or ""),
            "kind": str(raw.get("kind") or ""),
            "anchor_step_id": str(raw.get("anchor_step_id") or ""),
            "request_refs": strict_refs(raw.get("request_refs")),
        }
        capability_by_name[item["name"]] = item
    for capability in spec.capabilities or []:
        if capability.name in capability_by_name:
            continue
        refs = strict_refs([
            ref.model_dump(exclude_none=True) for ref in (capability.request_refs or [])
        ])
        candidate_ids = [
            ref["step_id"] for ref in refs if ref["usage"] == "execute"
        ] or list(_capability_node_step_ids(capability))
        anchor_step_id = next((
            step_id for step_id in reversed(candidate_ids)
            if _planned_capability_has_public_anchor(spec, capability.kind, [step_id])
        ), "")
        if not anchor_step_id:
            continue
        if not any(
            ref["step_id"] == anchor_step_id and ref["usage"] == "execute"
            for ref in refs
        ):
            refs.append({"step_id": anchor_step_id, "usage": "execute"})
        capability_by_name[capability.name] = {
            "name": capability.name,
            "title": capability.title or capability.name,
            "kind": capability.kind,
            "anchor_step_id": anchor_step_id,
            "request_refs": refs,
        }
    plan["capabilities"] = list(capability_by_name.values())
    plan["unresolved_items"] = [
        copy.deepcopy(item)
        for item in (proposed_plan.get("unresolved_items") or [])
        if isinstance(item, dict)
    ]
    return plan


def _semantic_mutable_context(spec: FlowSpec) -> dict[str, Any]:
    """Current contract delta; immutable request facts live in the prompt prefix."""
    context = _orchestration_context(spec)
    for key in (
        "complete_field_index", "complete_response_path_index", "steps",
        "links", "captured_requests",
    ):
        context.pop(key, None)
    findings = context.get("validation_findings") or {}
    context["validation_findings"] = {
        "errors": list(findings.get("errors") or [])[:30],
        "warnings": list(findings.get("warnings") or [])[:30],
        "unused_high_confidence_requests": list(findings.get("unused_high_confidence_requests") or [])[:40],
    }
    previous_model = (spec.meta or {}).get("capability_model") or {}
    previous_plan = previous_model.get("semantic_plan")
    if isinstance(previous_plan, dict) and previous_plan:
        context["accepted_semantic_plan_hash"] = _stable_json_hash(previous_plan)
    generation_state = (spec.meta or {}).get("capability_generation") or {}
    context["generation_state"] = {
        key: generation_state.get(key)
        for key in ("protocol", "initial_completed", "semantic_plan_hash", "generation_epoch", "status")
        if generation_state.get(key) not in (None, "")
    }
    return context


def _merge_capability_lists_impl(
    existing: list[FlowCapability],
    generated: list[FlowCapability],
    *,
    spec: FlowSpec | None,
    allow_new: bool,
    removed_capabilities: set[str],
    removed_families: set[str],
) -> list[FlowCapability]:
    if not existing:
        return [
            cap for cap in generated
            if cap.name not in removed_capabilities
            and _capability_kind_family(cap.kind) not in removed_families
        ]
    out = [cap.model_copy(deep=True) for cap in existing]
    by_name = {cap.name: cap for cap in out if cap.name}
    generated_family_counts: dict[str, int] = {}
    for candidate in generated:
        family = _capability_kind_family(candidate.kind)
        generated_family_counts[family] = generated_family_counts.get(family, 0) + 1
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
            family = _capability_kind_family(cap.kind)
            same_family = [
                item for item in out
                if _capability_kind_family(item.kind) == family
            ]
            generated_ids = set(_capability_node_step_ids(cap))
            overlapping = [
                item for item in same_family
                if generated_ids & set(_capability_node_step_ids(item))
            ]
            if overlapping:
                cur = max(
                    overlapping,
                    key=lambda item: len(generated_ids & set(_capability_node_step_ids(item))),
                )
            elif len(same_family) == 1 and generated_family_counts.get(family) == 1:
                # A user-renamed or legacy capability often has a nonstandard
                # name (for example capability_2).  Match the only same-family
                # draft so deterministic re-analysis can repair missing
                # interface membership without creating a duplicate ability.
                cur = same_family[0]
        if cur is None:
            if not allow_new:
                continue
            out.append(cap)
            if cap.name:
                by_name[cap.name] = cap
            continue
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
                usage = str(copied.get("usage") or "")
                if not sid:
                    if (
                        usage in {"option_source", "fact_check"}
                        and (copied.get("request_id") or copied.get("path"))
                    ):
                        cleaned.append(copied)
                        node_ids.add(str(copied.get("id") or ""))
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
        cap.nodes = clean_nodes(cap.nodes or [], [])
        legacy_refs = list(cap.request_refs or [])
        for ref in legacy_refs:
            if ref.usage == "preflight" and valid_step_id(ref.step_id):
                _add_step_id_to_capability(spec, cap, ref.step_id)
        _sync_capability_order(spec, cap)
        if not cap.locked:
            membership_by_step = {ref.step_id: ref for ref in cap.request_refs if ref.step_id}
            for legacy_ref in legacy_refs:
                current = membership_by_step.get(legacy_ref.step_id)
                if (
                    current is not None
                    and legacy_ref.usage == "preflight"
                    and legacy_ref.origin in {"manual", "user"}
                ):
                    current.usage = "preflight"
                    current.origin = legacy_ref.origin
                    current.confirmed = legacy_ref.confirmed
    return spec


def _prune_auth_materializations(spec: FlowSpec) -> None:
    """Repair plans produced before compound auth paths were recognized."""
    auth_step_ids = {
        step.step_id
        for step in spec.steps
        if looks_like_auth_write(step.url or step.path)
    }
    if auth_step_ids:
        spec.steps = [
            step for step in spec.steps if step.step_id not in auth_step_ids
        ]
        spec.links = [
            link for link in spec.links
            if link.source_step_id not in auth_step_ids
            and link.target_step_id not in auth_step_ids
        ]
        _normalize_capability_references(spec)
        spec.meta = {
            **(spec.meta or {}),
            "pruned_auth_step_count": (
                int((spec.meta or {}).get("pruned_auth_step_count") or 0)
                + len(auth_step_ids)
            ),
        }
    for fact in spec.request_facts.requests or []:
        request_id = str(fact.request_id or "")
        if not request_id or not looks_like_auth_write(
            fact.url or fact.path, fact.post_data
        ):
            continue
        analysis = spec.request_facts.analysis.get(request_id) or RequestAnalysis(
            request_id=request_id
        )
        analysis.role = "auth"
        analysis.keep = False
        analysis.reason = "登录/鉴权/令牌刷新请求，只作为身份来源，不进入业务流程"
        analysis.confidence = max(float(analysis.confidence or 0), 0.96)
        spec.request_facts.analysis[request_id] = analysis
        usage = spec.request_facts.usage.get(request_id) or RequestUsage(
            request_id=request_id
        )
        if usage.materialized_step_id in auth_step_ids:
            usage.materialized_step_id = ""
            usage.state = "captured"
            usage.used_by_capabilities = []
            usage.capability_memberships = []
        spec.request_facts.usage[request_id] = usage


def _remove_capability_step_nodes(nodes: list[dict[str, Any]], step_id: str) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        if node.get("type") == "call" and str(node.get("step_id") or "") == step_id:
            continue
        if node.get("type") == "return" and str(node.get("from") or node.get("source") or "") == step_id:
            continue
        copied = dict(node)
        for child_key in ("children", "steps", "then", "else", "otherwise"):
            if isinstance(copied.get(child_key), list):
                copied[child_key] = _remove_capability_step_nodes(copied[child_key], step_id)
        cleaned.append(copied)
    return cleaned


def _sync_capability_order(spec: FlowSpec, cap: FlowCapability) -> None:
    """Refresh derived membership views from the executable node plan."""
    by_id = {step.step_id: step for step in spec.steps}
    legacy_refs = list(cap.request_refs or [])
    cap.step_ids = [
        step_id for step_id in _capability_call_step_ids_from_nodes(cap.nodes or [])
        if step_id in by_id
    ]
    existing_memberships = {
        ref.step_id: ref for ref in (cap.request_refs or [])
        if ref.usage in {"execute", "preflight"} and ref.step_id
    }
    call_step_ids = set(cap.step_ids)

    option_source_step_ids: set[str] = set()
    option_source_request_ids: set[str] = set()
    option_source_paths: set[str] = set()

    def remember_option_source(source: dict[str, Any]) -> None:
        if not isinstance(source, dict):
            return
        source_step_id = str(source.get("source_step_id") or "")
        source_request_id = str(
            source.get("source_request_id") or source.get("request_id") or ""
        )
        source_path = _request_path({"url": str(source.get("source_url") or "")})
        if source_step_id:
            option_source_step_ids.add(source_step_id)
        if source_request_id:
            option_source_request_ids.add(source_request_id)
        if source_path:
            option_source_paths.add(source_path)

    for step_id in call_step_ids:
        step = by_id.get(step_id)
        if step is None:
            continue
        for binding in step.selects or []:
            remember_option_source({
                "source_request_id": binding.source_request_id,
                "source_url": binding.source_url,
            })
        for param in step.params or []:
            source = param.source or {}
            if param.source_kind == "api_option":
                remember_option_source(source)
            remember_option_source(source.get("option_source") or {})
    for link in spec.links or []:
        if link.target_step_id in call_step_ids:
            remember_option_source((link.value_binding or {}).get("option_source") or {})

    def keep_auxiliary_ref(ref: CapabilityRequestRef) -> bool:
        if ref.usage != "option_source" or ref.origin in {"manual", "user"}:
            return True
        return bool(
            (ref.step_id and ref.step_id in option_source_step_ids)
            or (ref.request_id and ref.request_id in option_source_request_ids)
            or (
                _request_path({"url": ref.path})
                and _request_path({"url": ref.path}) in option_source_paths
            )
        )

    auxiliary_refs = [
        ref for ref in (cap.request_refs or [])
        if (
            (
                ref.usage not in {"execute", "preflight"}
                or not ref.step_id
                # Explicit planner/manual preflight facts need not be executable
                # call nodes. Preserve those references while normalizing the one
                # public execute anchor among actual call nodes.
                or (ref.usage == "preflight" and ref.step_id not in call_step_ids)
            )
            and keep_auxiliary_ref(ref)
        )
    ]
    existing_execute_ids = [
        ref.step_id for ref in legacy_refs
        if ref.usage == "execute" and ref.step_id in call_step_ids
    ]
    evidence_anchor_ids = [
        str(item.get("anchor_step_id") or "")
        for item in (cap.evidence or [])
        if isinstance(item, dict)
        and str(item.get("anchor_step_id") or "") in call_step_ids
    ]
    return_anchor_ids = [
        str(node.get("from") or node.get("source") or "")
        for node in _iter_capability_nodes(cap.nodes or [])
        if isinstance(node, dict)
        and node.get("type") == "return"
        and str(node.get("from") or node.get("source") or "") in call_step_ids
    ]
    anchor_candidates = list(dict.fromkeys(
        existing_execute_ids or evidence_anchor_ids or return_anchor_ids
    ))
    if not anchor_candidates and len(cap.step_ids) == 1:
        anchor_candidates = list(cap.step_ids)
    anchor_step_id = anchor_candidates[0] if len(anchor_candidates) == 1 else ""
    execute_refs: list[CapabilityRequestRef] = []
    for step_id in cap.step_ids:
        ref = _capability_request_ref_from_step(
            spec, by_id[step_id], existing_memberships.get(step_id),
        )
        if anchor_step_id and not cap.locked:
            ref.usage = "execute" if step_id == anchor_step_id else "preflight"
        legacy_ref = next((item for item in legacy_refs if item.step_id == step_id), None)
        if (
            legacy_ref is not None
            and legacy_ref.usage == "preflight"
            and legacy_ref.origin in {"manual", "user"}
        ):
            ref.usage = "preflight"
            ref.origin = legacy_ref.origin
            ref.confirmed = legacy_ref.confirmed
        execute_refs.append(ref)
    cap.request_refs = _ordered_capability_request_refs(execute_refs + auxiliary_refs)


def _sync_capability_output_after_step_removal(cap: FlowCapability) -> None:
    valid_step_ids = set(cap.step_ids or [])
    stale_mappings = [
        dict(mapping)
        for mapping in (cap.output_mapping or [])
        if isinstance(mapping, dict)
        and str(mapping.get("step_id") or "")
        and str(mapping.get("step_id") or "") not in valid_step_ids
    ]
    cap.output_mapping = [
        dict(mapping)
        for mapping in (cap.output_mapping or [])
        if isinstance(mapping, dict)
        and (
            not str(mapping.get("step_id") or "")
            or str(mapping.get("step_id") or "") in valid_step_ids
        )
    ]
    if valid_step_ids and not cap.output_mapping:
        final_step_id = cap.step_ids[-1]
        replacement = stale_mappings[0] if stale_mappings else {
            "kind": "final_response",
            "name": "result",
            "response_path": "response",
        }
        replacement["step_id"] = final_step_id
        cap.output_mapping = [replacement]
    if cap.step_ids and not any(
        node.get("type") == "return"
        for node in _iter_capability_nodes(cap.nodes or [])
    ):
        cap.nodes.append({
            "id": "return_final",
            "type": "return",
            "from": cap.step_ids[-1],
            "path": "response",
        })


def _reorder_capability_call_nodes(
    nodes: list[dict[str, Any]], order: dict[str, int],
) -> list[dict[str, Any]]:
    copied_nodes: list[dict[str, Any]] = []
    for raw in nodes or []:
        if not isinstance(raw, dict):
            continue
        copied = dict(raw)
        for child_key in ("children", "steps", "then", "else", "otherwise"):
            if isinstance(copied.get(child_key), list):
                copied[child_key] = _reorder_capability_call_nodes(copied[child_key], order)
        copied_nodes.append(copied)
    call_positions = [
        index for index, node in enumerate(copied_nodes)
        if node.get("type") == "call" and str(node.get("step_id") or "") in order
    ]
    ordered_calls = sorted(
        (copied_nodes[index] for index in call_positions),
        key=lambda node: order[str(node.get("step_id") or "")],
    )
    for index, node in zip(call_positions, ordered_calls):
        copied_nodes[index] = node
    return copied_nodes

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
            "all": [
                _capability_field_summary(f)
                for f in [
                    *(cap.inputs or []),
                    *(cap.request_fields or []),
                    *(cap.internal_fields or []),
                    *(cap.computed_fields or []),
                    *(cap.outputs or []),
                ]
            ],
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


def _planner_patch_edits(
    spec: FlowSpec,
    edits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Accept only edits grounded in already materialized FlowSpec facts."""
    existing_steps = {step.step_id for step in spec.steps}
    step_by_id = {step.step_id: step for step in spec.steps}
    cap_by_name = {cap.name: cap for cap in spec.capabilities if cap.name}
    safe: list[dict[str, Any]] = []
    scope_ops = {
        "add_request_step", "add_candidate_step", "promote_request",
        "add_capability", "create_capability", "remove_capability",
        "reject_dependency",
    }
    for raw in edits or []:
        edit = dict(raw)
        op = str(edit.get("op") or "")
        if op in scope_ops:
            continue
        if op == "remove_request_from_capability":
            if not edit.get("_semantic_boundary_reconcile"):
                continue
            cap_name = str(
                edit.get("capability_name") or edit.get("capability") or ""
            )
            step_id = str(edit.get("step_id") or "")
            target = cap_by_name.get(cap_name)
            planner_managed = bool(
                target is not None
                and not target.locked
                and target.updated_by != "user"
                and not any(
                    ref.origin in {"manual", "user"}
                    for ref in (target.request_refs or [])
                )
            )
            if (
                not planner_managed
                or step_id not in set(_capability_node_step_ids(target))
            ):
                continue
        if op == "add_request_to_capability":
            # Planner 只能重组已经在字段/接口工作台物化的步骤，不能用 request_id
            # 或 request_index 从捕获事实库静默拉入新接口。
            step_id = str(edit.get("step_id") or "")
            if not step_id or step_id not in existing_steps:
                continue
            if (step_by_id[step_id].source_meta or {}).get(
                "duplicate_observation_of"
            ):
                continue
            cap_name = str(edit.get("capability_name") or edit.get("capability") or "")
            if _capability_step_was_removed(spec, cap_name, step_id):
                continue
            target_cap = cap_by_name.get(cap_name)
            current_owners = [
                cap for cap in spec.capabilities
                if step_id in set(_capability_node_step_ids(cap))
            ]
            if target_cap is not None and current_owners and target_cap not in current_owners:
                target_ids = set(_capability_node_step_ids(target_cap))
                linked = any(
                    {link.source_step_id, link.target_step_id} & {step_id}
                    and ({link.source_step_id, link.target_step_id} - {step_id}) & target_ids
                    for link in spec.links
                )
                explicit_owners = {
                    str(value) for value in (
                        (step_by_id[step_id].source_meta or {}).get("control_preflight_for_write_ids") or []
                    ) if str(value)
                }
                if not linked and not (explicit_owners & target_ids):
                    continue
        if op == "upsert_capability":
            payload = dict(edit.get("capability") or {})
            name = str(edit.get("capability_name") or edit.get("capability") or edit.get("name") or "")
            if payload:
                name = str(payload.get("name") or name)
                # Re-analysis may introduce a real new public boundary, but it
                # may not restore a capability explicitly removed by the user.
                if name in _removed_capability_names(spec):
                    continue
                for key in ("step_ids", "request_refs", "nodes"):
                    payload.pop(key, None)
                edit["capability"] = payload
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
            if "[" in source_path:
                # Planner proposals cannot turn an arbitrary collection row
                # into a scalar field dependency.
                continue
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


def _capability_to_api_dict(spec: FlowSpec, cap: FlowCapability) -> dict[str, Any]:
    out = cap.model_dump(exclude_none=True)
    contract = _capability_execution_contract(spec, cap)
    out["execution_contract"] = contract
    out["workflow_nodes"] = contract["nodes"]
    out["compiled_step_ids"] = [c["step_id"] for c in contract["call_order"]]
    return out


def _semantic_wire_hash(spec: FlowSpec) -> str:
    """Hash executable interface identity while excluding public field names."""
    payload = [
        {
            "step_id": step.step_id,
            "method": (step.method or "GET").upper(),
            "path": step.path or step.url,
            "content_type": step.content_type,
            "param_paths": sorted((param.path, param.wire_type or "") for param in step.params),
        }
        for step in spec.steps
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _only_grounded_screenshot_query_params_added(
    before: FlowSpec,
    candidate: FlowSpec,
) -> bool:
    before_steps = {step.step_id: step for step in before.steps}
    candidate_steps = {step.step_id: step for step in candidate.steps}
    if before_steps.keys() != candidate_steps.keys():
        return False
    added = 0
    for step_id, old_step in before_steps.items():
        new_step = candidate_steps[step_id]
        if (
            (old_step.method or "GET").upper() != (new_step.method or "GET").upper()
            or (old_step.path or old_step.url) != (new_step.path or new_step.url)
            or old_step.content_type != new_step.content_type
        ):
            return False
        old_params = {param.path: param for param in old_step.params}
        new_params = {param.path: param for param in new_step.params}
        if not old_params.keys() <= new_params.keys():
            return False
        if any(
            (old_params[path].wire_type or "") != (new_params[path].wire_type or "")
            for path in old_params
        ):
            return False
        for path in new_params.keys() - old_params.keys():
            param = new_params[path]
            if (
                (new_step.method or "GET").upper() not in {"GET", "HEAD"}
                or not path.startswith("query.")
                or _screenshot_control_evidence({"evidence": param.evidence}) is None
                or not any(
                    isinstance(item, dict)
                    and item.get("source") == "response_schema_match"
                    for item in param.evidence
                )
            ):
                return False
            added += 1
    return added > 0


def _semantic_candidate_gate(
    before: FlowSpec,
    candidate: FlowSpec,
    *,
    allow_screenshot_query_additions: bool = False,
    allow_grounded_wire_change: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """Admit an automatic proposal only when executable quality is monotonic."""
    before_report = validate_flow_spec(before)
    after_report = validate_flow_spec(candidate)

    def generation_findings(report: dict[str, Any], key: str) -> list[str]:
        """Validation used to police generated proposals, not operator publish."""
        capability_report = report.get("capability_validation") or {}
        return list(dict.fromkeys(str(item) for item in [
            *(report.get(key) or []),
            *(capability_report.get(key) or []),
        ] if item))

    before_error_list = generation_findings(before_report, "errors")
    after_error_list = generation_findings(after_report, "errors")

    def error_signature(message: str) -> str:
        # Public titles/field names are expected semantic improvements. Error
        # identity must not change merely because a backticked display label did.
        return re.sub(r"`[^`]+`", "`<target>`", message)

    before_errors = {error_signature(item) for item in before_error_list}
    after_errors = {error_signature(item) for item in after_error_list}
    reasons: list[str] = []
    new_error_signatures = after_errors - before_errors
    new_errors = sorted(
        item for item in after_error_list if error_signature(item) in new_error_signatures
    )
    # During the first semantic generation a generic baseline may be split into
    # several explicit capabilities.  The same expected "not confirmed" error
    # then appears once per capability; that is not a new error class and must
    # not reject an otherwise valid split.  Incremental optimization remains
    # strict because its capability scope is locked.
    if new_errors:
        reasons.append("new_validation_errors")
    before_warning_list = generation_findings(before_report, "warnings")
    after_warning_list = generation_findings(after_report, "warnings")
    if (
        _semantic_wire_hash(before) != _semantic_wire_hash(candidate)
        and not allow_grounded_wire_change
        and not (
            allow_screenshot_query_additions
            and _only_grounded_screenshot_query_params_added(before, candidate)
        )
    ):
        reasons.append("wire_contract_changed")
    before_dry = dry_run_flow_spec(before)
    after_dry = dry_run_flow_spec(candidate)
    grounded_required_fields = {
        param.key
        for step in candidate.steps
        for param in step.params
        if param.required
        and any(
            isinstance(evidence, dict)
            and (
                evidence.get("required") is True
                or (
                    evidence.get("source") == "manual_edit"
                    and evidence.get("field") == "required"
                    and evidence.get("value") is True
                )
            )
            for evidence in (param.evidence or [])
        )
    }
    missing_after = set(after_dry.get("missing_params") or [])
    required_input_only = bool(
        missing_after
        and missing_after.issubset(grounded_required_fields)
        and not after_dry.get("build_errors")
        and not after_dry.get("self_check")
        and not after_dry.get("construct_errors")
        and bool((after_dry.get("fact_check") or {}).get("passed"))
    )
    if (
        bool(before_dry.get("ok"))
        and not bool(after_dry.get("ok"))
        and not required_input_only
    ):
        reasons.append("dry_run_regressed")
    audit = {
        "accepted": not reasons,
        "reasons": reasons,
        "new_errors": new_errors[:40],
        "before_errors": len(before_error_list),
        "after_errors": len(after_error_list),
        "before_warnings": len(before_warning_list),
        "after_warnings": len(after_warning_list),
        "before_dry_ok": bool(before_dry.get("ok")),
        "after_dry_ok": bool(after_dry.get("ok")),
        "boundary_reanalysis": True,
    }
    return not reasons, audit




async def orchestrate_flow_capabilities(
    spec: FlowSpec,
    *,
    submission: dict[str, Any],
    generation_mode: str | None = None,
) -> FlowSpec:
    """Apply one structured plan submitted by the Pi recording agent.

    This is deliberately model-free. Pi owns the AgentSession and produces the
    submission; this function only compiles whitelisted operations and applies
    deterministic fact/schema/quality gates.

    Public capability boundaries have exactly one machine-owned producer:
    strict semantic plan -> verified request graph compiler.  Recorder
    heuristics remain facts/candidates and never become a publishable fallback.
    Operator-owned capabilities are preserved by the compiler.
    """
    if not isinstance(submission, dict):
        raise ValueError("recording plan submission must be an object")
    if not isinstance(submission.get("ops", []), list):
        raise ValueError("recording plan ops must be a list")
    _validate_recording_agent_ops(submission.get("ops") or [])
    original = spec.model_copy(deep=True)
    _prune_auth_materializations(original)
    _mark_repeated_write_observations(original)
    initial_report = validate_flow_spec(original)
    current = _prune_empty_capabilities(original.model_copy(deep=True))
    rebuild_flow_dependencies(current)
    _repair_structural_option_bindings(current)
    capability_model = (current.meta or {}).get("capability_model") or {}
    auto_generated_existing = bool(
        current.capabilities
        and capability_model.get("source")
        and not any(
            cap.locked
            or cap.updated_by == "user"
            or any(ref.origin in {"manual", "user"} for ref in (cap.request_refs or []))
            for cap in current.capabilities
        )
    )
    if auto_generated_existing:
        # Machine-owned definitions are reproducible only from an accepted
        # strict plan. Drop stale deterministic/legacy output before deciding
        # whether a current plan can rebuild it.
        current.capabilities = []
        current.capability_relations = []
    had_existing = bool(current.capabilities)
    initial_generation = auto_generated_existing or generation_mode == "initial" or (generation_mode is None and not had_existing)
    # Optimization is a boundary re-analysis over already materialized steps.
    # It may repair capability membership, but request IDs outside FlowSpec
    # remain unavailable to both deterministic and model planners.
    scope_baseline = current.model_copy(deep=True)
    # Do not manufacture a deterministic capability baseline here.  The
    # compiler below preserves explicit operator-owned definitions and replaces
    # every machine-owned definition from the strict plan in one pass.
    current = _prune_empty_capabilities(current)
    source = "strict_plan_pending"
    reason = ""
    semantic_plan: dict[str, Any] = {}
    semantic_coverage: dict[str, Any] = {}
    previous_model = (current.meta or {}).get("capability_model") or {}
    previous_semantic_plan = (
        previous_model.get("semantic_plan")
        if isinstance(previous_model.get("semantic_plan"), dict) else {}
    )
    incremental_review: dict[str, Any] = {}
    proposal_baseline = current.model_copy(deep=True)
    if initial_generation:
        proposal_baseline = _repair_generated_capability_contracts(
            proposal_baseline,
        )
    proposal_baseline = _ensure_external_transform_relations(
        _sync_capability_io_schemas(sync_flow_spec_models(proposal_baseline))
    )

    proposed_semantic_plan = (
        submission.get("semantic_plan") if isinstance(submission.get("semantic_plan"), dict)
        else (submission.get("plan") if isinstance(submission.get("plan"), dict) else {})
    )
    strict_semantic_submission = bool(
        isinstance(proposed_semantic_plan.get("capabilities"), list)
        and proposed_semantic_plan.get("capabilities")
        and all(
            isinstance(item, dict)
            and item.get("name")
            and item.get("kind")
            and item.get("anchor_step_id")
            and isinstance(item.get("request_refs"), list)
            and item.get("request_refs")
            for item in proposed_semantic_plan.get("capabilities") or []
        )
    )
    # Recordings created before the strict anchor/request_refs contract persist
    # the same complete boundary decision as ``step_ids``.  Treat that stored
    # representation as a full replacement during optimize so an obsolete
    # planner-owned aggregate cannot survive beside its replacement abilities.
    previous_strict_plan = bool(
        isinstance(previous_semantic_plan.get("capabilities"), list)
        and previous_semantic_plan.get("capabilities")
        and all(
            isinstance(item, dict)
            and item.get("name")
            and item.get("kind")
            and item.get("anchor_step_id")
            and isinstance(item.get("request_refs"), list)
            and item.get("request_refs")
            for item in previous_semantic_plan.get("capabilities") or []
        )
    )
    fact_request_ids = {
        str(item.get("request_id") or "")
        for item in _request_fact_items(current)
        if str(item.get("request_id") or "")
    }
    if strict_semantic_submission and previous_strict_plan:
        # Live batches are complete snapshots, but a model may accidentally
        # omit an earlier ability while focusing on new facts. Preserve each
        # still-grounded boundary unless the new plan replaces the same name or
        # the same public anchor. Explicit operator removals remain authoritative.
        proposed_semantic_plan = copy.deepcopy(proposed_semantic_plan)
        proposed_items = [
            item for item in proposed_semantic_plan.get("capabilities") or []
            if isinstance(item, dict)
        ]
        proposed_by_name = {
            str(item.get("name") or ""): item for item in proposed_items
            if str(item.get("name") or "")
        }
        proposed_by_boundary = {
            (
                _capability_kind_family(str(item.get("kind") or "")),
                str(item.get("anchor_step_id") or ""),
            ): item
            for item in proposed_items
        }
        step_ids = {step.step_id for step in current.steps}
        removed_names = _removed_capability_names(current)
        merged_items: list[dict[str, Any]] = []
        emitted_names: set[str] = set()
        for previous_item in previous_semantic_plan.get("capabilities") or []:
            previous_name = str(previous_item.get("name") or "")
            replacement = proposed_by_name.get(previous_name)
            if replacement is None:
                replacement = proposed_by_boundary.get((
                    _capability_kind_family(str(previous_item.get("kind") or "")),
                    str(previous_item.get("anchor_step_id") or ""),
                ))
            if replacement is not None:
                replacement_name = str(replacement.get("name") or "")
                if replacement_name not in emitted_names:
                    merged_items.append(replacement)
                    emitted_names.add(replacement_name)
                continue
            previous_anchor = str(previous_item.get("anchor_step_id") or "")
            previous_refs = [
                str(ref.get("step_id") or "")
                for ref in previous_item.get("request_refs") or []
                if isinstance(ref, dict)
            ]
            if current.steps:
                still_grounded = bool(
                    previous_anchor in step_ids
                    and all(ref in step_ids for ref in previous_refs)
                    and _planned_capability_has_public_anchor(
                        current,
                        str(previous_item.get("kind") or ""),
                        [previous_anchor],
                    )
                )
            else:
                still_grounded = bool(
                    previous_anchor in fact_request_ids
                    and all(ref in fact_request_ids for ref in previous_refs)
                )
            if (
                previous_name
                and previous_name not in removed_names
                and previous_name not in emitted_names
                and still_grounded
            ):
                merged_items.append(copy.deepcopy(previous_item))
                emitted_names.add(previous_name)
        for proposed_item in proposed_items:
            proposed_name = str(proposed_item.get("name") or "")
            if proposed_name not in emitted_names:
                merged_items.append(proposed_item)
                emitted_names.add(proposed_name)
        proposed_semantic_plan["capabilities"] = merged_items
    effective_semantic_plan = (
        proposed_semantic_plan
        if strict_semantic_submission
        else previous_semantic_plan if previous_strict_plan else {}
    )
    pre_materialization_candidate = bool(
        strict_semantic_submission
        and not current.steps
        and fact_request_ids
        and all(
            str(item.get("anchor_step_id") or "") in fact_request_ids
            and all(
                isinstance(ref, dict)
                and str(ref.get("step_id") or "") in fact_request_ids
                for ref in (item.get("request_refs") or [])
            )
            for item in (effective_semantic_plan.get("capabilities") or [])
            if isinstance(item, dict)
        )
    )
    pre_materialization_coverage = (
        _pre_materialization_semantic_plan_coverage(
            current, effective_semantic_plan, fact_request_ids,
        )
        if pre_materialization_candidate
        else {
            "complete": False,
            "missing": [],
            "covered_steps": 0,
            "total_steps": 0,
            "covered_fields": 0,
            "total_fields": 0,
            "phase": "request_facts",
        }
    )
    live_blocking_gaps = set(pre_materialization_coverage.get("missing") or []) & {
        "capability_contracts", "capabilities", "goal_capability_count", "unresolved_blockers",
    }
    pre_materialization_strict_plan = bool(
        pre_materialization_candidate and not live_blocking_gaps
    )
    ignored_non_public_capabilities: list[str] = []
    if strict_semantic_submission and not pre_materialization_strict_plan:
        public_capabilities = [
            item
            for item in (effective_semantic_plan.get("capabilities") or [])
            if isinstance(item, dict)
            and _planned_capability_has_public_anchor(
                current,
                str(item.get("kind") or ""),
                [str(item.get("anchor_step_id") or "")],
            )
        ]
        if public_capabilities:
            ignored_non_public_capabilities = [
                str(item.get("name") or item.get("title") or item.get("anchor_step_id") or "")
                for item in (effective_semantic_plan.get("capabilities") or [])
                if isinstance(item, dict) and item not in public_capabilities
            ]
            if ignored_non_public_capabilities:
                effective_semantic_plan = copy.deepcopy(effective_semantic_plan)
                effective_semantic_plan["capabilities"] = public_capabilities
    complete_semantic_submission = strict_semantic_submission
    preserved_human_relations: list[CapabilityRelation] = []
    if complete_semantic_submission:
        # A complete re-analysis owns the automatic relation set as well as
        # capability boundaries. Keep operator-confirmed relations, then
        # rebuild every planner suggestion from concrete endpoints below.
        preserved_human_relations = [
            relation.model_copy(deep=True)
            for relation in (original.capability_relations or [])
            if relation.confirmed
            or str((relation.evidence or {}).get("source") or "").lower()
            in {"manual", "user", "operator"}
        ]
        current.capability_relations = []
    if initial_generation or complete_semantic_submission:
        semantic_plan = effective_semantic_plan
        semantic_coverage = _semantic_plan_coverage(current, submission)
    else:
        # Ops-only submissions remain incremental and retain the last accepted
        # complete semantic blueprint.
        semantic_plan = previous_semantic_plan
        semantic_coverage = dict(previous_model.get("semantic_coverage") or {})
    if not initial_generation:
        incremental_review = {
            "reviewed_scope": submission.get("reviewed_scope") or {},
            "unresolved_items": (
                proposed_semantic_plan.get("unresolved_items")
                or submission.get("unresolved_items")
                or []
            ),
            "complete_semantic_submission": complete_semantic_submission,
        }
    # Field/source/required/enum edits are applied through the live operation
    # channel before this function. Capability membership is compiler-owned;
    # translating the semantic plan back into generic edit ops would reintroduce
    # a second producer.
    _normalize_capability_references(current)
    if initial_generation:
        current = _repair_generated_capability_contracts(
            current,
        )
    current = _ensure_external_transform_relations(
        _sync_capability_io_schemas(sync_flow_spec_models(current))
    )
    capability_compilation_audit: dict[str, Any] = {}
    capability_compilation_errors: list[str] = []
    partial_safe_compilation = False
    planned_capability_contracts = [
        item for item in (effective_semantic_plan.get("capabilities") or [])
        if isinstance(item, dict)
    ]
    strict_anchor_contract = bool(planned_capability_contracts) and all(
        item.get("name") and item.get("kind") and item.get("anchor_step_id")
        for item in planned_capability_contracts
    )
    if strict_anchor_contract and not pre_materialization_strict_plan and current.steps:
        from dano.execution.page.capability_compiler import compile_capabilities

        compilation = compile_capabilities(current, effective_semantic_plan)
        current = _ensure_external_transform_relations(
            _sync_capability_io_schemas(sync_flow_spec_models(compilation.spec))
        )
        capability_compilation_audit = dict(compilation.audit)
        capability_compilation_errors = list(compilation.errors)
        partial_safe_compilation = bool(
            compilation.capabilities and capability_compilation_errors
        )
        semantic_coverage = _semantic_plan_coverage(
            current,
            {"semantic_plan": effective_semantic_plan},
        )
        if not semantic_coverage.get("complete"):
            capability_compilation_errors.append(
                "strict semantic plan is incomplete: "
                + ", ".join(str(item) for item in semantic_coverage.get("missing") or [])
            )
        if compilation.capabilities:
            source = "verified_request_graph"
    if pre_materialization_strict_plan:
        # Live analysis runs before request facts are materialized into stable
        # FlowStep IDs. Accept and retain a fully fact-addressable strict plan,
        # but do not manufacture provisional capabilities. Finalize retargets
        # request IDs to canonical step IDs and invokes the same compiler once.
        proposal_accepted = True
        proposal_gate = {
            "accepted": True,
            "reasons": [],
            "producer": "verified_request_graph",
            "pending": "request_materialization",
        }
        source = "strict_plan_awaiting_materialization"
    elif strict_anchor_contract and not capability_compilation_errors and current.steps:
        proposal_accepted = True
        proposal_gate = {
            "accepted": True,
            "reasons": [],
            "producer": "verified_request_graph",
        }
    else:
        proposal_accepted = False
        pre_materialization_reasons = list(
            pre_materialization_coverage.get("missing") or []
        ) if pre_materialization_candidate else []
        proposal_gate = {
            "accepted": False,
            "reasons": (
                pre_materialization_reasons
                or (
                    ["capability_compilation_failed"]
                    if capability_compilation_errors
                    else ["strict_semantic_plan_required"]
                )
            ),
            "producer": "verified_request_graph",
        }
    if not proposal_accepted:
        # The compiler validates each public anchor independently. Keep its
        # safely compiled subset when another proposed boundary is malformed;
        # the incomplete generation state still forces Pi to correct the full
        # plan before automatic publishing. Rolling the whole candidate back
        # here made one bad helper/boundary erase unrelated valid abilities.
        if not partial_safe_compilation:
            # A rejected complete snapshot must not erase the last accepted
            # collection. The next Pi batch can retry from that authoritative
            # baseline while newly captured facts remain available on the spec.
            current = _prune_empty_capabilities(original.model_copy(deep=True))
        if previous_strict_plan:
            semantic_plan = previous_semantic_plan
            semantic_coverage = dict(previous_model.get("semantic_coverage") or {})
        source = (
            "strict_plan_partial"
            if partial_safe_compilation
            else "strict_plan_pending" if not strict_anchor_contract
            else "strict_plan_rejected"
        )
        reason = "自动语义 Proposal 未通过单调质量准入: " + ",".join(
            proposal_gate["reasons"]
        )
    if preserved_human_relations:
        valid_capability_refs = {
            ref
            for capability in (current.capabilities or [])
            for ref in (capability.name, capability.capability_id)
            if ref
        }
        for relation in preserved_human_relations:
            if (
                relation.from_capability in valid_capability_refs
                and relation.to_capability in valid_capability_refs
            ):
                _upsert_capability_relation(
                    current, relation.model_dump(exclude_none=True),
                )
    current = _apply_semantic_business_understanding(current, semantic_plan)
    if pre_materialization_strict_plan:
        semantic_plan = copy.deepcopy(effective_semantic_plan)
    elif strict_anchor_contract and not capability_compilation_errors:
        semantic_plan = _complete_semantic_plan_from_spec(current, semantic_plan)
    elif previous_strict_plan:
        semantic_plan = copy.deepcopy(previous_semantic_plan)
    else:
        # Keep the last fact-addressable Skill plan. Wiping capabilities here
        # made an incomplete live coverage check erase a complete submitted
        # boundary set, so stage six compiled nothing and publish crashed.
        kept_capabilities = [
            copy.deepcopy(item)
            for item in (effective_semantic_plan.get("capabilities") or [])
            if isinstance(item, dict)
        ]
        unresolved_items = [
            copy.deepcopy(item)
            for item in (semantic_plan.get("unresolved_items") or [])
            if isinstance(item, dict)
        ]
        if not kept_capabilities:
            unresolved_items.append({
                "type": "capability_plan",
                "title": "需要严格能力边界计划",
                "blocking": True,
            })
        semantic_plan = {
            "business_understanding": (
                copy.deepcopy(semantic_plan.get("business_understanding"))
                if isinstance(semantic_plan.get("business_understanding"), dict)
                else {}
            ),
            "capabilities": kept_capabilities,
            "unresolved_items": unresolved_items,
        }
    semantic_coverage = (
        pre_materialization_coverage
        if pre_materialization_strict_plan
        else _semantic_plan_coverage(current, {"semantic_plan": semantic_plan})
    )
    caps = list(current.capabilities or [])
    final_report = validate_flow_spec(current)
    final_errors = [
        *list(final_report.get("errors") or []),
        *list((final_report.get("capability_validation") or {}).get("errors") or []),
        *capability_compilation_errors,
    ]
    public_boundaries_valid = bool(caps) and all(
        _planned_capability_has_public_anchor(
            current, capability.kind, list(_capability_node_step_ids(capability)),
        )
        for capability in caps
    )
    generation_ready = bool(
        semantic_coverage.get("complete")
        and public_boundaries_valid
        and not final_errors
    )
    current.meta = {
        **(current.meta or {}),
        "capability_model": {
            "status": (
                "awaiting_materialization"
                if pre_materialization_strict_plan
                else "ready" if generation_ready else "needs_review"
            ),
            "source": source,
            "generated_count": len(caps),
            "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "semantic_plan": semantic_plan,
            "semantic_coverage": semantic_coverage,
            "last_incremental_review": incremental_review,
            "proposal_gate": proposal_gate,
            "capability_compilation": capability_compilation_audit,
            "capability_compilation_errors": capability_compilation_errors,
            "ignored_non_public_capabilities": ignored_non_public_capabilities,
        },
        "capability_orchestration_audit": {
            "mode": "initial" if initial_generation else "boundary_reanalysis",
            "checked_steps": len(original.steps),
            "checked_fields": sum(len(step.params or []) for step in original.steps),
            "checked_captured_requests": len(_request_fact_items(original)),
            "before_errors": len(initial_report.get("errors") or []),
            "before_warnings": len(initial_report.get("warnings") or []),
            "after_errors": len(final_report.get("errors") or []),
            "after_warnings": len(final_report.get("warnings") or []),
            "boundary_reanalysis": True,
            "capability_count_before": len(scope_baseline.capabilities or []),
            "capability_count_after": len(caps),
        },
    }
    return append_flow_version(refresh_review_items(current), "orchestrate_flow", reason=f"生成能力编排: {source}")




def _capability_node_step_ids(cap: FlowCapability) -> list[str]:
    return _capability_call_step_ids_from_nodes(cap.nodes or [])


def _step_request_key(step: FlowStep) -> str:
    meta = step.source_meta or {}
    if meta.get("request_id"):
        return f"id:{meta.get('request_id')}"
    if meta.get("request_index") is not None:
        return f"idx:{meta.get('request_index')}"
    return f"sig:{(step.method or '').upper()} {_request_path({'url': step.path or step.url})}"


def _step_request_signature_key(step: FlowStep) -> str:
    return f"{(step.method or '').upper()} {_request_path({'url': step.path or step.url})}"






def _eligible_business_write_fact(entry: dict[str, Any]) -> bool:
    return bool(
        entry.get("keep")
        and str(entry.get("role") or "") in {"business_write", "submit_anchor"}
        and str(entry.get("method") or "").upper() in _WRITE_METHODS
        and str(entry.get("path") or entry.get("url") or "").strip()
    )


def _materialized_step_id_for_request(spec: FlowSpec, entry: dict[str, Any]) -> str:
    """Resolve only exact request identity; duplicate paths are distinct facts."""
    step_ids = {step.step_id for step in spec.steps}
    usage_id = str(entry.get("materialized_step_id") or "")
    if usage_id in step_ids:
        return usage_id
    request_key = _request_fact_key_from_entry(entry)
    if request_key.startswith(("id:", "idx:")):
        return next(
            (step.step_id for step in spec.steps if _step_request_key(step) == request_key),
            "",
        )
    signature = _request_fact_signature_key(entry)
    matches = [
        step.step_id for step in spec.steps
        if _step_request_signature_key(step) == signature
    ]
    return matches[0] if len(matches) == 1 else ""


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
    for item in _request_fact_items(spec):
        if item.get("request_id"):
            request_ids.add(str(item.get("request_id")))
        if item.get("request_index") is not None:
            request_indexes.add(str(item.get("request_index")))
    return request_ids, request_indexes


def _capability_schema_field_type(schema: dict[str, Any], field: str) -> str:
    item = _schema_node_at_path(schema, field)
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
            if not _param_exposed_to_caller(param):
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




_MUTATING_RECORD_KINDS = frozenset({"update", "approve", "reject", "delete", "withdraw"})


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


def _capability_execute_record_selector(cap: FlowCapability, field: CapabilityField) -> bool:
    """Update/delete-family execute anchors may expose the record id/ids selector."""
    if str(cap.kind or "") not in _MUTATING_RECORD_KINDS:
        return False
    text = f"{field.path}.{field.key}"
    return bool(re.search(r"(^|[.\]])(id|ids)(\]|$)", text, re.I))


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
    if param.type not in {"enum", "list-enum"}:
        return ""
    if param.source_kind == "api_option":
        # API candidates are resolved at runtime. An empty capture snapshot (or
        # a source that is being reselected) is valid and must not block publish.
        return ""
    if not param.enum_options:
        return "缺少可执行枚举选项 label/value"
    # A DOM snapshot is display evidence, not automatically an executable wire
    # contract.  A partial snapshot is safe when the recorded request itself uses
    # the displayed label (or when a real label->value pair covers it); we simply
    # avoid hard-coding the partial list in the public schema.  If the request uses
    # a code/ID, however, the missing map remains a hard blocker.
    if (
        param.source_kind == "page_enum"
        and (param.source or {}).get("enum_confirmed") is False
        and not _incomplete_page_enum_is_executable(param)
    ):
        return "页面枚举快照不完整：只捕获到显示名称，缺少完整的真实 label→value 映射"
    if param.source_kind == "manual_enum" and not _manual_enum_mapping_complete(param):
        return "人工枚举必须为每个显示名称提供明确的真实 label→value 映射"
    if not _enum_map_covers_recorded_value(param):
        return "枚举 label/value 不能映射录制提交值"
    if _enum_options_look_value_only(param):
        return "枚举候选看起来只有内部值，缺少可展示 label"
    return ""


def _capability_param_enum_warning(param: ParamField) -> str:
    """Return non-blocking evidence quality advice for an executable enum."""
    if (
        param.type in {"enum", "list-enum"}
        and param.source_kind == "page_enum"
        and (param.source or {}).get("enum_confirmed") is False
        and param.enum_options
        and _incomplete_page_enum_is_executable(param)
        and not _enum_options_look_value_only(param)
    ):
        return "页面枚举快照可能不完整；已验证当前录制值可执行，未把候选列表作为完整约束"
    return ""


def _capability_validation_report(spec: FlowSpec, *, prepared: bool = False) -> dict[str, Any]:
    spec = ensure_recorded_goal(_sync_capability_io_schemas(spec.model_copy(deep=True)))
    _normalize_capability_references(spec)
    errors: list[str] = []
    warnings: list[str] = []
    caps = list(spec.capabilities or [])
    step_by_id = {s.step_id: s for s in spec.steps}
    request_items = _request_fact_items(spec)
    materialized_keys = {_step_request_key(s) for s in spec.steps}
    materialized_signatures = {_step_request_signature_key(s) for s in spec.steps}
    unmaterialized_business = [
        item for item in request_items
        if _eligible_business_write_fact(item)
        and not _materialized_step_id_for_request(spec, item)
    ]
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
        for item in request_items
        if float(item.get("confidence") or 0) >= 0.9
        and (item.get("role") or "") in {"submit_anchor", "business_write", "business_get", "read_context", "read_option"}
        and _request_fact_key_from_entry(item) not in materialized_keys
        and _request_fact_signature_key(item) not in materialized_signatures
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
    materialization_integrity = {
        "passed": True,
        "errors": [],
        "unmaterialized_business_requests": [],
        "unassigned_business_steps": [],
        "unassigned_materialized_steps": [],
        "duplicate_business_step_memberships": [],
    }

    def add_integrity_error(code: str, message: str, target: dict[str, Any]) -> None:
        entry = {"code": code, "message": message, "target": target}
        materialization_integrity["errors"].append(entry)
        skill_level["errors"].append(entry)
        errors.append(message)

    for item in unmaterialized_business:
        target = {
            "kind": "captured_request",
            "request_id": item.get("request_id"),
            "request_index": item.get("request_index"),
            "method": item.get("method"),
            "path": item.get("path") or item.get("url"),
        }
        materialization_integrity["unmaterialized_business_requests"].append(target)
        add_integrity_error(
            "unmaterialized_business_request",
            f"未物化业务操作：{item.get('method')} {item.get('path') or item.get('url')}",
            target,
        )

    memberships_by_step: dict[str, set[str]] = {}
    removed_capability_step_ids = _retired_capability_step_ids(spec)
    internal_step_ids = {
        str(item)
        for item in (spec.meta or {}).get("internal_step_ids") or []
        if item
    }
    for capability in caps:
        capability_name = capability.name or capability.capability_id or "<unnamed>"
        for step_id in _capability_node_step_ids(capability):
            memberships_by_step.setdefault(step_id, set()).add(capability_name)
        for request_ref in capability.request_refs or []:
            if request_ref.step_id:
                memberships_by_step.setdefault(request_ref.step_id, set()).add(capability_name)
    for item in request_items:
        role = str(item.get("role") or "")
        requires_membership = bool(
            item.get("keep")
            and role in {"business_write", "submit_anchor", "business_get", "read_context", "read_option"}
        )
        if not requires_membership:
            continue
        step_id = _materialized_step_id_for_request(spec, item)
        if not step_id or step_id in removed_capability_step_ids:
            continue
        memberships = memberships_by_step.get(step_id, set())
        target = {
            "kind": "flow_step",
            "step_id": step_id,
            "request_id": item.get("request_id"),
            "method": item.get("method"),
            "path": item.get("path") or item.get("url"),
        }
        if not memberships:
            if step_id in internal_step_ids:
                continue
            is_public_business = role in {"business_write", "submit_anchor", "business_get"}
            bucket = "unassigned_business_steps" if is_public_business else "unassigned_materialized_steps"
            materialization_integrity[bucket].append(target)
            add_integrity_error(
                "unassigned_business_step" if is_public_business else "unassigned_materialized_step",
                f"已物化步骤未归属任何能力或内部用途：{item.get('method')} {item.get('path') or item.get('url')}",
                target,
            )
        elif _eligible_business_write_fact(item) and len(set(memberships)) > 1:
            target["capabilities"] = sorted(set(memberships))
            materialization_integrity["duplicate_business_step_memberships"].append(target)
            add_integrity_error(
                "duplicate_business_step_membership",
                f"业务写步骤同时归属多个能力：{item.get('method')} {item.get('path') or item.get('url')}",
                target,
            )
    materialization_integrity["passed"] = not materialization_integrity["errors"]
    if spec.steps and not caps:
        skill_level["passed"] = not skill_level["errors"]
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
            "materialization_integrity": materialization_integrity,
        }

    allowed_kinds = ALLOWED_CAPABILITY_KINDS
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
            cap_warnings.append(f"Capability `{label}` 尚未确认，需要确认或移除后再发布")
        elif not cap.confirmation_hash:
            cap_warnings.append(f"Capability `{label}` 来自旧版确认记录；下次合同编辑后将启用版本指纹校验")
        elif cap.confirmation_hash != _capability_confirmation_hash(spec, cap, prepared=prepared):
            cap_errors.append(f"Capability `{label}` 确认后合同已变化，请复核并重新确认")

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
                target = {
                    "kind": "capability_enum",
                    "capability": label,
                    "step_id": st.step_id,
                    "path": param.path,
                }
                if enum_issue:
                    msg = f"Capability `{label}` 枚举字段 `{param.key or param.path}` {enum_issue}"
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
                enum_warning = _capability_param_enum_warning(param)
                if enum_warning:
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_enum_snapshot_incomplete",
                        message=f"Capability `{label}` 枚举字段 `{param.key or param.path}` {enum_warning}",
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
                if _capability_execute_record_selector(cap, field):
                    _capability_warning(
                        internal_section,
                        warnings,
                        code="capability_internal_field_exposed",
                        message=msg,
                        target=target,
                    )
                elif cap.confirmed:
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
            if node_type == "call":
                call_step_id = str(node.get("step_id") or "")
                call_usage = str(node.get("usage") or "")
                if call_usage == "option_source" and not call_step_id:
                    pass
                elif call_step_id not in step_by_id:
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
                                if not _param_requires_caller_input(param):
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
            cap_errors.append(f"Capability `{label}` 已确认状态查询能力，但缺少读接口步骤或 RequestFacts 证据")
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
        cardinality = str(relation.cardinality or "")
        transform_owner = str(relation.transform_owner or "")
        cardinality_valid = cardinality in {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
        transform_owner_valid = transform_owner in {"caller", "skill", "runtime"}
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
            "cardinality": cardinality,
            "cardinality_valid": cardinality_valid,
            "transform_owner": transform_owner,
            "transform_owner_valid": transform_owner_valid,
        }
        capability_relations["relations"].append(relation_entry)
        if not cardinality_valid or not transform_owner_valid:
            invalid_parts = []
            if not cardinality_valid:
                invalid_parts.append(f"cardinality={cardinality!r}")
            if not transform_owner_valid:
                invalid_parts.append(f"transform_owner={transform_owner!r}")
            msg = f"Capability relation `{relation.relation_id}` 编排契约无效: {', '.join(invalid_parts)}"
            issue = {
                "code": "capability_relation_contract_invalid",
                "message": msg,
                "target": {"kind": "capability_relation", "relation_id": relation.relation_id},
            }
            if relation.confirmed:
                capability_relations.setdefault("errors", []).append(issue)
                errors.append(msg)
            else:
                _capability_warning(
                    capability_relations, warnings,
                    code=issue["code"], message=msg, target=issue["target"],
                )
        elif from_cap is None or to_cap is None:
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
    for cap in caps:
        if cap.confirmed and not cap.requires_human_confirm:
            continue
        cap_ref = cap.name or cap.capability_id
        message = f"Capability `{cap_ref}` 是未确认的公开能力；请确认该能力或从发布范围移除"
        _capability_warning(
            skill_level,
            warnings,
            code="unconfirmed_public_capability",
            message=message,
            target={"kind": "capability", "capability": cap_ref},
        )
    confirmed_caps = [c for c in caps if c.confirmed]
    strict_skill_level = bool((spec.meta or {}).get("publish_gate") or (spec.meta or {}).get("strict_skill_level"))
    if confirmed_caps:
        skill_issues: list[tuple[str, str]] = []
        if not str(spec.business_description or "").strip():
            skill_issues.append(("skill_description_missing", "Skill 缺少面向调用方的整体说明"))
        # Multiple independent capabilities require explicit selection, not a
        # fabricated call order or relation. A relation is required only when
        # a concrete output-to-input mapping exists and is validated above.
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
        "materialization_integrity": materialization_integrity,
    }




def _review_id(item_type: str, target: dict[str, Any]) -> str:
    parts = [
        item_type,
        str(target.get("step_id") or ""),
        str(target.get("path") or ""),
        str(target.get("link_id") or ""),
        str(target.get("request_index") or ""),
        str(target.get("capability") or target.get("capability_name") or target.get("capability_id") or ""),
        str(target.get("field") or ""),
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
    blocking: bool = False,
    ignorable: bool = True,
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
        blocking=blocking,
        ignorable=ignorable,
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


def _flow_path_set(node, path, value) -> bool:  # noqa: ANN001
    tokens = _flow_path_tokens(path)
    if not tokens:
        return False
    current = node
    for token in tokens[:-1]:
        try:
            current = current[token]
        except Exception:  # noqa: BLE001
            return False
    try:
        current[tokens[-1]] = value
    except Exception:  # noqa: BLE001
        return False
    return True


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

    # 来源建议属于能力合同的编辑反馈。尚未生成能力时，字段还没有发布
    # 边界和可定位的能力锚点，不能提前制造“待处理”告警。能力存在后仍
    # 覆盖未归属步骤，但用户明确删除能力时，其原步骤已退出编辑范围。
    if spec.capabilities:
        removed_step_ids = _retired_capability_step_ids(spec)
        for st in spec.steps:
            if st.step_id in removed_step_ids:
                continue
            for p in st.params:
                target = {
                    "kind": "param",
                    "step_id": st.step_id,
                    "step_name": st.name,
                    "path": p.path,
                    "key": p.key,
                    "param_type": p.type,
                    "category": p.category,
                    "source_kind": p.source_kind or "unknown",
                }
                guess = f"{p.category}/{p.source_kind}"
                source_unknown = str(p.source_kind or "").strip().lower() in {"", "unknown"}
                source_advice = _field_source_configuration_advice(p)
                if source_unknown:
                    items.append(_review_item(
                        "field_source_unknown",
                        severity="medium",
                        title=f"字段 {p.path} 的来源尚未识别",
                        target=target,
                        current_guess=guess,
                        suggested_action="configure_or_ignore_field_source",
                        # reason=(
                        #     "系统会保留当前类型、分类和来源组合，不会自动改写或阻止保存、优化、发布；"
                        #     "可补充明确来源，或确认当前人工配置后忽略此提示"
                        # ),
                        confidence=p.confidence,
                        blocking=False,
                        ignorable=True,
                    ))
                elif source_advice:
                    items.append(_review_item(
                        "field_source_incomplete",
                        severity="medium",
                        title=f"字段 {p.path} 的来源配置不完整",
                        target=target,
                        current_guess=guess,
                        suggested_action="configure_or_ignore_field_source",
                        reason=(
                            f"{source_advice}；系统会保留当前人工配置，"
                            "该提示可忽略且不会阻止保存、优化、发布"
                        ),
                        confidence=p.confidence,
                        blocking=False,
                        ignorable=True,
                    ))

    for st in visible_steps:
        for p in st.params:
            target = {
                "kind": "param",
                "step_id": st.step_id,
                "step_name": st.name,
                "path": p.path,
                "key": p.key,
                "param_type": p.type,
                "category": p.category,
                "source_kind": p.source_kind or "unknown",
            }
            guess = f"{p.category}/{p.source_kind}"

            source_unknown = str(p.source_kind or "").strip().lower() in {"", "unknown"}

            source_advice = _field_source_configuration_advice(p)

            if p.need_human_confirm and not source_unknown and not source_advice:
                items.append(_review_item(
                    "field_category",
                    severity="medium",
                    title=f"确认字段 {p.path} 的分类和来源",
                    target=target,
                    current_guess=guess,
                    suggested_action="confirm_field_source",
                    reason=p.reason or "该字段分类由规则推断，建议人工确认",
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
        # A capability-scoped request may compile only links whose two
        # endpoints are inside that capability's verified closure.  Including
        # a half-owned pending link made its outside endpoint look like a
        # missing step and blocked an otherwise valid ability.
        if active_step_ids is not None and not (
            lk.source_step_id in active_step_ids and lk.target_step_id in active_step_ids
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
    if param.source_kind == "selected_option_field":
        source_score += 3
    elif param.source_kind in {"api_option", "page_enum", "static_enum", "manual_enum", "form_option"}:
        source_score += 2
    manual_score = 1 if param.name_source in {"manual", "llm", "planner", "assignee", "sample"} else 0
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


def refresh_review_items(spec: FlowSpec, *, prepared: bool = False) -> FlowSpec:
    """重建 review_items，并保留同 id 项的已解决状态。

    ID 是稳定 hash(target)，所以同一字段/同一依赖在重建前后 ID 不变，
    用户的 resolved 标记会随 ID 一起被复用，告警不会因为字段重渲染而复活。
    """
    for step in spec.steps:
        _dedupe_step_params(step)
    old_resolved: dict[str, bool] = {}
    legacy_source_resolved: dict[tuple[str, str, str], bool] = {}
    for item in spec.review_items:
        # id 已是 target 的稳定 hash；同字段前后 ID 一致，resolved 跟着保留。
        old_resolved.setdefault(item.id, item.resolved)
        # Preserve dismissals while migrating the two legacy runtime-only
        # source warnings to category-agnostic field source review items.
        legacy_type = {
            "runtime_var_source": "field_source_unknown",
            "runtime_var_missing_source": "field_source_incomplete",
        }.get(item.type)
        if legacy_type:
            target_key = (
                legacy_type,
                str(item.target.get("step_id") or ""),
                str(item.target.get("path") or ""),
            )
            legacy_source_resolved.setdefault(target_key, item.resolved)
    spec.review_items = _generated_review_items(spec, prepared=prepared)
    for item in spec.review_items:
        if item.id in old_resolved:
            item.resolved = old_resolved[item.id]
        elif item.type in {"field_source_unknown", "field_source_incomplete"}:
            target_key = (
                item.type,
                str(item.target.get("step_id") or ""),
                str(item.target.get("path") or ""),
            )
            if target_key in legacy_source_resolved:
                item.resolved = legacy_source_resolved[target_key]
    return spec






def append_flow_version(
    spec: FlowSpec,
    action: str,
    *,
    reason: str = "",
    actor: str = "system",
) -> FlowSpec:
    """在 FlowSpec.meta 中追加轻量版本记录。"""
    sync_flow_spec_models(spec)
    meta = dict(spec.meta or {})
    versions = list(meta.get("versions") or [])
    current = max(
        [int(meta.get("current_version") or 0)]
        + [int(v.get("version") or 0) for v in versions]
    )
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
    spec = sync_flow_spec_models(spec.model_copy(deep=True))
    summary_meta = dict(spec.meta or {})
    summary_meta.pop("request_graph", None)
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
                "link_id": link.link_id,
                "source_step_id": link.source_step_id,
                "source_path": link.source_path,
                "target_step_id": link.target_step_id,
                "target_path": link.target_path,
                "confirmed": link.confirmed,
                "confidence": link.confidence,
            }
            for link in spec.links
        ],
        "meta": summary_meta,
    }


# ─────────── P0-0: FlowSpec → 可发布 api_request ───────────
def _clean_path_prefix(path: str, prefix: str) -> str:
    if not path:
        return ""
    return path[len(prefix):] if path.startswith(prefix) else path


def _step_samples(step: FlowStep) -> dict:
    samples = dict(step.sample_inputs or {})
    for p in step.params:
        if (
            p.key
            and p.value not in (None, "")
            and p.source_kind != "dynamic_structure"
            and str((p.source or {}).get("kind") or "") != "dynamic_structure_leaf"
        ):
            samples[p.key] = p.value
    return samples


def _step_param_map(step: FlowStep) -> dict[str, str]:
    """只把 user_param 暴露给 Skill 调用者；常量/运行期变量保留在流程内部。"""
    out: dict[str, str] = {}
    for p in step.params:
        if not _param_exposed_to_caller(p):
            continue
        key = (p.key or "").strip()
        if key:
            out[p.path] = key
    return out


def _step_wire_formats(step: FlowStep) -> dict[str, str]:
    """Map stable public input names to their explicit on-wire formats."""
    return {
        str(param.key): str(param.wire_format)
        for param in step.params
        if _param_exposed_to_caller(param) and param.key and param.wire_format
    }


def _executable_identity_source(value: Any) -> bool:
    """Return whether the existing request runtime can resolve this source.

    FlowSpec also keeps advisory identity guesses (for example a body field
    named ``user_id`` whose concrete session location was not captured).  An
    advisory body path is useful evidence for Pi, but it is not a runtime
    source and must not be emitted into the executable request.
    """
    kind, separator, location = str(value or "").partition(":")
    return bool(
        separator
        and location
        and kind in {"cookie", "localStorage", "requestHeader"}
    )


def _step_runtime_identity(step: FlowStep) -> list[dict[str, Any]]:
    """Compile session-owned body fields through the existing identity runtime."""
    values = [
        item.model_dump(exclude_none=True)
        for item in step.identity
        if _executable_identity_source(item.source)
    ]
    for param in step.params:
        if param.category != "runtime_var":
            continue
        source = dict(param.source or {})
        if (
            param.source_kind == "current_user"
            and _executable_identity_source(source.get("path"))
        ):
            values.append({
                "path": _strip_body_prefix(param.path),
                "source": str(source["path"]),
                "value": param.value,
            })
        elif param.source_kind == "request_header" and source.get("header"):
            values.append({
                "path": _strip_body_prefix(param.path),
                "source": f"requestHeader:{source['header']}",
                "value": param.value,
            })
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in values:
        deduped[(str(item.get("path") or ""), str(item.get("source") or ""))] = item
    return list(deduped.values())


_COMPUTED_DATE_STRATEGIES = frozenset({"date_span_days", "date_span_days_json"})
_COMPUTED_ARITHMETIC_STRATEGIES = frozenset({
    "product", "sum", "percent_of", "remainder_after_percent", "difference",
})


def _computed_formula_is_complete(source: dict | None) -> bool:
    source = source or {}
    strategy = str(source.get("strategy") or "")
    if strategy in _COMPUTED_DATE_STRATEGIES:
        return bool(source.get("start_field") and source.get("end_field"))
    if strategy in _COMPUTED_ARITHMETIC_STRATEGIES:
        return bool(source.get("left_field") and source.get("right_field"))
    return False


def _runtime_param_publish_error(param: ParamField) -> str | None:
    """Source inference/configuration is advisory and never a publish error.

    The same field-local finding is exposed by ``build_review_items`` and
    ``_field_source_configuration_advice``.  Keeping this compatibility helper
    returning ``None`` prevents source heuristics from entering request-builder
    errors while preserving hard failures elsewhere (missing request body,
    malformed request data, absent executable steps, and so on).
    """
    return None


def _field_source_configuration_advice(param: ParamField) -> str | None:
    """仅提示明确选定的运行时来源缺少配置。

    分类和来源都是可编辑的生成结果，不存在系统级的“兼容表”，
    也不能用模型判断阻止人工组合。
    """
    source_kind = param.source_kind or "unknown"
    if source_kind == "page_context" and not (param.source or {}).get("context_key"):
        return f"字段 `{param.path}` 的调用上下文缺少 context_key"
    if source_kind == "request_header" and not (param.source or {}).get("header"):
        return f"字段 `{param.path}` 的请求头来源缺少 header 名称"
    if source_kind == "system_generated" and str((param.source or {}).get("strategy") or "") not in {
        "uuid", "random_string", "random_number",
    }:
        return f"字段 `{param.path}` 的系统生成值缺少有效生成策略"
    if source_kind == "computed" and not _computed_formula_is_complete(param.source):
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
                    strategy = str((p.source or {}).get("strategy") or "")
                    if not strategy:
                        strategy = (
                            ("now_date" if p.type == "date" else "now_iso")
                            if p.source_kind == "system_time" and p.type in {"string", "date", "datetime"}
                            else "now_ms" if p.source_kind == "system_time" else "uuid"
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


def _flow_step_url_template(
    step: FlowStep,
) -> tuple[str, list[str], dict[str, Any], dict[str, str]]:
    path_params = [param for param in step.params if param.path.startswith("path.")]
    if not path_params:
        return "", [], {}, {}
    parsed = urlparse(step.url or step.path)
    segments = parsed.path.split("/")
    names: list[str] = []
    samples: dict[str, Any] = {}
    field_types: dict[str, str] = {}
    for param in path_params:
        try:
            position = int(param.path.split(".", 1)[1])
        except (TypeError, ValueError):
            continue
        if position < 0 or position >= len(segments):
            continue
        name = str(param.key or param.label or f"path_{position}").strip()
        if not name:
            continue
        segments[position] = "{{" + name + "}}"
        names.append(name)
        if param.value not in (None, ""):
            samples[name] = param.value
        field_types[name] = param.type
    if not names:
        return "", [], {}, {}
    return parsed._replace(path="/".join(segments)).geturl(), list(dict.fromkeys(names)), samples, field_types


def flow_spec_user_params(spec: FlowSpec) -> list[str]:
    names: list[str] = []
    active_step_ids = _active_capability_step_ids(spec)
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
            if not _param_requires_caller_input(p):
                continue
            key = (p.key or "").strip()
            if key and key not in names:
                names.append(key)
    return names


def _apply_grounded_indexed_range_names(spec: FlowSpec) -> tuple[FlowSpec, list[dict[str, Any]]]:
    """Name strongly grounded two-value date ranges before capability creation.

    This is deliberately structural rather than business-specific: an indexed
    pair on one read request, with a date/time wire type and a range-like base,
    is the common query convention used by many frameworks.  Ambiguous arrays
    (multi-selects, row items, more than two values) remain model/manual work.
    """
    current = spec.model_copy(deep=True)
    changes: list[dict[str, Any]] = []
    range_base = re.compile(r"(?:time|date|range|period|begin|start|end|from|to|时间|日期|区间)", re.I)
    for step in current.steps:
        if (step.method or "GET").upper() not in {"GET", "HEAD"}:
            continue
        groups: dict[str, dict[int, ParamField]] = {}
        for param in step.params or []:
            # Public names may already be grounded independently (for example
            # the first half is named ``开始日期`` while the second half still
            # carries ``createTime[1]``).  The executable wire path is the
            # stable identity of the pair, so grouping by ``key`` loses exactly
            # the partially-grounded ranges this pass is meant to complete.
            match = re.fullmatch(r"(.+)\[(\d+)\]", str(param.path or ""))
            if not match:
                continue
            groups.setdefault(match.group(1), {})[int(match.group(2))] = param
        for base, members in groups.items():
            if set(members) != {0, 1} or not range_base.search(base):
                continue
            start, end = members[0], members[1]
            if any(
                param.locked
                or param.category != "user_param"
                or not param.exposed_to_user
                or (param.type or "").lower() not in {"date", "datetime"}
                for param in (start, end)
            ):
                continue
            start_value = _date_like_epoch_seconds(start.value)
            end_value = _date_like_epoch_seconds(end.value)
            if start_value is not None and end_value is not None and start_value > end_value:
                continue
            def grounded_public_name(param: ParamField, index: int) -> str:
                name = str(param.key or param.label or "").strip()
                raw_names = {
                    f"{base}[{index}]",
                    str(param.path or "").split(".")[-1],
                }
                return "" if not name or name in raw_names else name

            start_name = grounded_public_name(start, 0)
            end_name = grounded_public_name(end, 1)

            def paired_name(name: str, *, to_end: bool) -> str:
                replacements = (
                    (("开始", "结束"), ("起始", "结束"), ("起", "止"))
                    if to_end else
                    (("结束", "开始"), ("截止", "开始"), ("止", "起"))
                )
                for source, target in replacements:
                    if source in name:
                        return name.replace(source, target, 1)
                english = (
                    ((r"\bstart\b", "end"), (r"\bbegin\b", "end"), (r"\bfrom\b", "to"))
                    if to_end else
                    ((r"\bend\b", "start"), (r"\bto\b", "from"))
                )
                for pattern, replacement in english:
                    changed = re.sub(pattern, replacement, name, count=1, flags=re.I)
                    if changed != name:
                        return changed
                return ""

            if start_name and start_name == end_name:
                start_name, end_name = f"{start_name}开始", f"{end_name}结束"
            if not start_name and end_name:
                start_name = paired_name(end_name, to_end=False)
            if not end_name and start_name:
                end_name = paired_name(start_name, to_end=True)
            proposed = (
                start_name or "查询开始时间",
                end_name or "查询结束时间",
            )
            if any(
                other is not start and other is not end and other.key in proposed
                for other in step.params
            ):
                proposed = (f"{base}开始时间", f"{base}结束时间")
            for param, name, role in (
                (start, proposed[0], "range_start"),
                (end, proposed[1], "range_end"),
            ):
                old_key = param.key
                _rename_param_public_key(current, step, param, name, actor="planner")
                param.evidence.append({
                    "source": "indexed_range_structure",
                    "group": base,
                    "role": role,
                    "wire_path": param.path,
                })
                changes.append({
                    "step_id": step.step_id,
                    "path": param.path,
                    "old_key": old_key,
                    "new_key": name,
                    "semantic_group": base,
                    "role": role,
                })
    return current, changes


def _select_param_for_runtime(step: FlowStep, binding: SelectBinding) -> ParamField | None:
    """Return the current field contract owned by a recorded select binding."""
    if binding.path:
        matched = next((
            param for param in (step.params or [])
            if param.path == binding.path
        ), None)
        if matched is not None:
            return matched
    if binding.id_path:
        return next((
            param for param in (step.params or [])
            if param.path == binding.id_path
        ), None)
    return None


def _select_binding_is_runtime_executable(step: FlowStep, binding: SelectBinding) -> bool:
    """Execute only an explicitly confirmed binding compatible with the live field contract.

    ``step.selects`` also keeps historical recorder evidence so the workbench can
    restore or inspect it.  It must not override an operator who has changed the
    field back to ordinary text/user input, nor may an incomplete candidate be
    promoted merely because it survived in that evidence list.
    """
    if binding.enum_confirmed is not True:
        return False
    param = _select_param_for_runtime(step, binding)
    if param is None or param.category != "user_param" or not param.exposed_to_user:
        return False
    source_kind = str(param.source_kind or "")
    if source_kind not in {"api_option", *_ENUM_SOURCE_KINDS}:
        return False
    if (
        source_kind != "api_option"
        and _param_field_manually_edited(param, "type")
        and param.type not in _ENUM_PARAM_TYPES
    ):
        return False
    if source_kind == "api_option":
        configured_url = str((param.source or {}).get("source_url") or "").strip()
        if configured_url and _request_path({"url": configured_url}) != _request_path({"url": binding.source_url}):
            return False
        return bool(binding.source_url and binding.value_key and binding.label_key)

    options = list(binding.options or [])
    if not options:
        return False
    option_map = dict(binding.option_map or _enum_option_map_from_options(options))
    labels: list[str] = []
    for option in options:
        pair = _enum_label_value(option)
        if pair is None or pair[0] in labels or pair[1] is None:
            return False
        labels.append(pair[0])
    return bool(labels) and all(label in option_map and option_map[label] is not None for label in labels)


def _runtime_select_bindings(step: FlowStep) -> list[dict[str, Any]]:
    """Serialize only bindings that remain executable after workbench edits."""
    current_key_by_path = {p.path: p.key for p in (step.params or [])}
    out: list[dict[str, Any]] = []
    for binding in step.selects or []:
        if not _select_binding_is_runtime_executable(step, binding):
            continue
        item = binding.model_dump(exclude_none=True)
        for metadata_key in ("actor", "confidence", "verification_id"):
            item.pop(metadata_key, None)
        if not item.get("field_projections"):
            item.pop("field_projections", None)
        if binding.path in current_key_by_path:
            item["param"] = current_key_by_path[binding.path]
        out.append(item)
    return out


def _flow_step_to_api_step(step: FlowStep) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    runtime_errors = [err for p in step.params if (err := _runtime_param_publish_error(p))]
    if runtime_errors:
        return None, runtime_errors
    if not step.body_source:
        body_params = [
            param for param in step.params
            if not param.path.startswith(("query.", "path."))
        ]
        if body_params:
            errors.append(f"步骤 `{step.name or step.path or step.step_id}` 缺少请求体，Body 字段没有可执行落点")
            return None, errors
        if step.method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            query_template, params, samples, field_types, runtime_fields = _flow_step_query_template(step)
            url_template, path_params, path_samples, path_types = _flow_step_url_template(step)
            selects = _runtime_select_bindings(step)
            apir = {
                "step_id": step.step_id,
                "step_name": step.name,
                "method": step.method.upper(),
                "url": step.url or step.path,
                "url_template": url_template,
                "path": step.path,
                "content_type": step.content_type,
                "body_template": None,
                "query_template": query_template,
                "params": list(dict.fromkeys([*params, *path_params])),
                "sample_inputs": {**samples, **path_samples},
                "auth_headers": extract_auth_headers(step.headers),
                "field_types": {**field_types, **path_types},
                "selects": selects,
                "identity": [],
                "system_values": [],
                "runtime_fields": runtime_fields,
            }
            wire_formats = _step_wire_formats(step)
            if wire_formats:
                apir["wire_formats"] = wire_formats
            if step.success_rule:
                apir["success_rule"] = step.success_rule
            if step.fact_check:
                apir["fact_check"] = step.fact_check
            if step.response_json is not None:
                apir["response_json"] = step.response_json
            return apir, errors
        errors.append(f"步骤 `{step.name or step.path or step.step_id}` 使用了不支持的 HTTP 方法 `{step.method}`")
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
    selects = _runtime_select_bindings(step)
    select_paths = set()
    for item in selects:
        path = str(item.get("path") or "")
        if path:
            select_paths.add(path)
    for p in step.params:
        if (
            p.category == "user_param"
            and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
            and p.enum_options
            and not (
                _param_field_manually_edited(p, "type")
                and p.type not in _ENUM_PARAM_TYPES
            )
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
        identity=_step_runtime_identity(step),
        typed=_step_samples(step),
    )
    if apir is None:
        errors.append(f"步骤 `{step.name or step.path or step.step_id}` 请求体无法解析，不能发布为请求型 Skill")
        return None, errors
    body_runtime_fields: list[dict[str, Any]] = []
    for param in step.params:
        if (
            param.source_kind != "computed"
            or param.path.startswith(("query.", "path."))
        ):
            continue
        runtime_name = f"__dano_runtime_{hashlib.sha1((step.step_id + ':' + param.path).encode()).hexdigest()[:10]}"
        if not _flow_path_set(
            apir.get("body_template"),
            _strip_body_prefix(param.path),
            "{{" + runtime_name + "}}",
        ):
            errors.append(
                f"步骤 `{step.name or step.path or step.step_id}` 的计算字段 `{param.path}` 没有请求体落点"
            )
            continue
        runtime_field = {"name": runtime_name, **dict(param.source or {})}
        runtime_field["kind"] = str(runtime_field.get("strategy") or "")
        body_runtime_fields.append(runtime_field)
    query_template, query_params, query_samples, query_types, runtime_fields = _flow_step_query_template(step)
    if query_template:
        apir["query_template"] = query_template
        apir["params"] = list(dict.fromkeys([*(apir.get("params") or []), *query_params]))
        apir["sample_inputs"] = {**(apir.get("sample_inputs") or {}), **query_samples}
        apir["field_types"] = {**(apir.get("field_types") or {}), **query_types}
        apir["runtime_fields"] = [*(apir.get("runtime_fields") or []), *runtime_fields]
    if body_runtime_fields:
        apir["runtime_fields"] = [*(apir.get("runtime_fields") or []), *body_runtime_fields]
    url_template, path_params, path_samples, path_types = _flow_step_url_template(step)
    if url_template:
        apir["url_template"] = url_template
        apir["params"] = list(dict.fromkeys([*(apir.get("params") or []), *path_params]))
        apir["sample_inputs"] = {**(apir.get("sample_inputs") or {}), **path_samples}
        apir["field_types"] = {**(apir.get("field_types") or {}), **path_types}
    explicit_system_values = [item.model_dump(exclude_none=True) for item in step.system_values]
    for p in step.params:
        if p.category != "runtime_var" or p.source_kind not in {"system_time", "system_generated"}:
            continue
        kind = str((p.source or {}).get("strategy") or "")
        if not kind:
            if p.source_kind == "system_generated":
                kind = "uuid"
            else:
                kind = (
                    "now_date" if p.type == "date"
                    else "now_iso" if p.type in {"string", "datetime"}
                    else "now_ms"
                )
        explicit_system_values.append({"path": _strip_body_prefix(p.path), "kind": kind})
    if explicit_system_values:
        deduped_system_values: dict[tuple[str, str], dict[str, Any]] = {}
        for item in [*(apir.get("system_values") or []), *explicit_system_values]:
            deduped_system_values[(str(item.get("path") or ""), str(item.get("kind") or ""))] = item
        apir["system_values"] = list(deduped_system_values.values())
    apir["step_id"] = step.step_id
    apir["step_name"] = step.name
    wire_formats = _step_wire_formats(step)
    if wire_formats:
        apir["wire_formats"] = wire_formats
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
    )))
    ref = capability
    if ref is None:
        ref = capability_id or capability_name or ""
    cap = _find_capability_by_ref(current, ref)
    if cap is None:
        raise ValueError(f"capability not found: {ref}")
    by_step = {s.step_id: s for s in current.steps}
    step_ids = [sid for sid in _capability_node_step_ids(cap) if sid in by_step]
    keep = set(step_ids)
    view = current.model_copy(deep=True)
    view.steps = [s for s in view.steps if s.step_id in keep]
    for step in view.steps:
        for param in step.params:
            if not _external_capability_input(param, keep):
                continue
            source = dict(param.source or {})
            source_step_id = _previous_response_source_step_id(param)
            param.category = "user_param"
            param.source_kind = "external_capability_input"
            param.source = {
                "kind": "external_capability_input",
                "source_step_id": source_step_id,
                "response_path": str(
                    source.get("response_path") or source.get("path") or ""
                ),
            }
            param.exposed_to_user = True
            param.editable = True
            param.required = True
            param.default_value = None
            param.reason = "该能力独立调用时由调用方传入上游能力的对应输出值"
    view.links = [
        lk for lk in view.links
        if lk.source_step_id in keep and lk.target_step_id in keep
    ]
    selected_cap = _find_capability_by_ref(view, cap.capability_id) or _find_capability_by_ref(view, cap.name)
    if selected_cap is None:
        selected_cap = cap.model_copy(deep=True)
    selected_cap.nodes = [
        n for n in (selected_cap.nodes or [])
        if not isinstance(n, dict)
        or n.get("type") != "call"
        or str(n.get("step_id") or "") in keep
    ]
    _sync_capability_order(view, selected_cap)
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
    selected_cap.input_schema = _capability_input_schema(
        [
            param
            for step in view.steps
            for param in (step.params or [])
        ],
        set(selected_cap.step_ids or []),
    )
    return sync_capability_scoped_views(view)


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
    try:
        view = capability_to_flow_spec_view(
            spec,
            capability,
            capability_id=capability_id,
            capability_name=capability_name,
        )
    except ValueError as exc:
        return None, [str(exc)]
    api_request, errors = flow_spec_to_api_request(view, _prepared=True)
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
    _prepared: bool = False,
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
    if not _prepared:
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
            lk.source_step_id in active_step_ids and lk.target_step_id in active_step_ids
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
        link_kind = str(lk.kind or "value")
        if link_kind in {"structure", "response_key_map"}:
            structure_link = {
                "link_id": lk.link_id,
                "target_path": lk.target_container_path or target_path,
                "target_tokens": lk.target_tokens,
                "source_step": source_idx,
                "source_path": lk.source_collection_path or source_path,
                "source_tokens": lk.source_tokens,
                "mode": "response_key_map" if link_kind == "response_key_map" else "response_keys",
            }
            if link_kind == "response_key_map":
                structure_link.update({
                    "kind": link_kind,
                    "source_collection_path": lk.source_collection_path or source_path,
                    "source_key_path": lk.source_key_path,
                    "source_label_path": lk.source_label_path,
                    "value_binding": copy.deepcopy(lk.value_binding or {}),
                })
            built_steps[target_idx].setdefault("structure_links", []).append(structure_link)
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
        wire_formats: dict[str, str] = {}
        for st in built_steps:
            samples.update(st.get("sample_inputs") or {})
            field_types.update(st.get("field_types") or {})
            wire_formats.update(st.get("wire_formats") or {})
        out = {
            "steps": built_steps,
            "params": params,
            "sample_inputs": samples,
            "field_types": field_types,
        }
        if wire_formats:
            out["wire_formats"] = wire_formats

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
    assertion = fc.get("assertion")
    if assertion is not None:
        from dano.execution.page.replay import _validate_assertion_contract

        missing = [] if endpoint else ["endpoint"]
        assertion_error = ""
        try:
            _validate_assertion_contract(assertion)
        except ValueError as exc:
            assertion_error = str(exc)
        passed = not missing and not assertion_error
        return {
            "configured": True,
            "passed": passed,
            "missing": missing,
            "spec": fc,
            "reason": (
                "fact_check 严格断言配置完整" if passed
                else assertion_error or f"fact_check 缺少 {', '.join(missing)}"
            ),
        }
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


def dry_run_flow_spec(
    spec: FlowSpec,
    fields: dict[str, Any] | None = None,
    *,
    _prepared: bool = False,
) -> dict:
    """静态 dry-run：不触网，只验证 FlowSpec 能否构造为可执行请求计划。"""
    api_request, build_errors = flow_spec_to_api_request(spec, _prepared=_prepared)
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
    explicit = _explicit_enum_value_map(param.enum_options, param.enum_value_map)
    if param.source_kind in {"page_enum", "manual_enum"}:
        if not labels or not all(label in explicit and explicit[label] is not None for label in labels):
            return False
        mapped_values = list(explicit.values())
    else:
        mapped_values = list(explicit.values()) or option_values
    return any(str(v) == current for v in mapped_values if v not in (None, ""))


def _incomplete_page_enum_is_executable(param: ParamField) -> bool:
    """Whether a partial DOM snapshot still defines a safe captured domain.

    When the request submits display text directly, captured labels are valid
    wire values and a partial list is quality advice only. When the request uses
    an ID/code, *every displayed candidate in the snapshot* needs an explicit
    mapping; knowing only the currently selected pair is insufficient because a
    caller could choose another label and submit it as the wire value.
    """
    labels = [
        pair[0] for pair in (_enum_label_value(item) for item in (param.enum_options or []))
        if pair is not None
    ]
    if not labels:
        return False
    explicit = _explicit_enum_value_map(param.enum_options, param.enum_value_map)
    if not explicit or not all(label in explicit and explicit[label] is not None for label in labels):
        return False
    current = str(param.value or "").strip()
    return not current or any(str(value) == current for value in explicit.values())


def _manual_enum_mapping_complete(param: ParamField) -> bool:
    """Whether every manually maintained label has an explicit wire value.

    Bare strings from a client-side textarea are deliberately *not* treated as
    ``label == value``.  That identity assumption is valid only when grounded by
    a page control/request pair; accepting it for ``manual_enum`` would let a
    client turn display names into fake API values merely by toggling confirmed.
    Explicit ``{label, value}``, two-item pairs, or ``enum_value_map`` entries are
    accepted, including legitimate identity mappings intentionally entered by an
    operator.
    """
    options = list(param.enum_options or [])
    if not options:
        return False
    explicit = dict(param.enum_value_map or {})
    labels: list[str] = []
    for option in options:
        if isinstance(option, dict):
            label = option.get("label", option.get("name", option.get("text")))
            if label in (None, ""):
                return False
            labels.append(str(label))
            if "value" in option and option.get("value") is not None:
                explicit.setdefault(str(label), option.get("value"))
        elif isinstance(option, (list, tuple)) and len(option) >= 2:
            label, value = option[0], option[1]
            if label in (None, "") or value is None:
                return False
            labels.append(str(label))
            explicit.setdefault(str(label), value)
        else:
            label = str(option or "").strip()
            if not label:
                return False
            labels.append(label)
    return bool(labels) and all(label in explicit and explicit[label] is not None for label in labels)


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


def _publish_issue_groups(errors: list[str], warnings: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Expose only request-construction failures; semantic findings are suggestions."""
    entries: list[dict[str, Any]] = []
    for severity, messages in (("error", errors), ("warning", warnings)):
        for message in dict.fromkeys(str(item) for item in messages if item):
            digest = hashlib.sha1(message.encode("utf-8")).hexdigest()[:12]
            entries.append({
                "severity": severity,
                "message": message,
                "source": "request_builder",
                "target": {"kind": "flow"},
                "blocking": severity == "error",
                "audience": "operator",
                "actionable": True,
                "auto_fixable": False,
                "code": f"request_builder_{digest}",
                "issue_id": f"publish:request_builder:{digest}",
            })
    return {"execution": entries} if entries else {}


def _field_source_review_issues(review_items: list[ReviewItem]) -> list[dict[str, Any]]:
    """Project unresolved field-source advice into the operator warning list.

    This is deliberately separate from request-builder failures: an unknown
    source is useful, field-local review context, but it is not proof that the
    operator's type/category/source combination is invalid.
    """
    issues: list[dict[str, Any]] = []
    for item in review_items:
        # "Unknown" is already visible on the field card and has no concrete
        # action. Repeating a long generic explanation in the status panel only
        # creates noise. Keep only explicitly selected but incomplete sources.
        if item.type != "field_source_incomplete" or item.resolved:
            continue
        issues.append({
            "severity": "warning",
            "message": f"{item.title}：{item.reason}" if item.reason else item.title,
            "source": "review",
            "target": dict(item.target or {}),
            "blocking": False,
            "ignorable": True,
            "audience": "operator",
            "actionable": True,
            "auto_fixable": False,
            "code": item.type,
            "issue_id": f"review:{item.id}",
            "review_id": item.id,
            "suggested_action": item.suggested_action,
        })
    return issues


def _enum_mapping_issues(steps: list[FlowStep]) -> list[dict[str, Any]]:
    """Expose locatable warnings only for mappings inferred from the page.

    Manual enums are operator-authored contract advice and already remain in
    ``suggestions``. Promoting them again into ``issue_groups`` made generated
    advice look like a newly detected recording defect.
    """
    issues: list[dict[str, Any]] = []
    for step in steps:
        for param in step.params:
            if (
                param.type not in {"enum", "list-enum"}
                or not param.enum_options
                or param.source_kind != "page_enum"
            ):
                continue
            explicit = _explicit_enum_value_map(param.enum_options, param.enum_value_map)
            labels = list(dict.fromkeys(
                pair[0]
                for pair in (_enum_label_value(option) for option in param.enum_options)
                if pair is not None
            ))
            missing = [
                label for label in labels
                if label not in explicit or explicit[label] is None
            ]
            if not missing:
                continue
            path = param.path or param.key
            digest = hashlib.sha1(f"{step.step_id}:{path}".encode("utf-8")).hexdigest()[:12]
            issues.append({
                "severity": "warning",
                "message": f"枚举字段 `{param.key or path}` 存在未映射值：{'、'.join(missing)}",
                "source": "enum_mapping",
                "target": {"kind": "param", "step_id": step.step_id, "path": path},
                "blocking": False,
                "audience": "operator",
                "actionable": True,
                "auto_fixable": False,
                "code": "enum_mapping_missing",
                "issue_id": f"enum_mapping:{digest}",
            })
    return issues


def _compiled_contract_issue_groups(
    spec: FlowSpec,
    api_request: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    resolved_review_ids: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Convert late compiled-contract advice into locatable workbench issues."""
    groups: dict[str, list[dict[str, Any]]] = {}
    resolved_review_ids = resolved_review_ids or set()
    compiled_steps = list(api_request.get("steps") or [api_request])
    by_step_id = {step.step_id: step for step in spec.steps}
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        kind = str(finding.get("kind") or "compiled_contract")
        if kind in {"self_check", "session_constant"}:
            continue
        target: dict[str, Any] = {}
        group = "flow"
        step_index = finding.get("step")
        compiled_step = (
            compiled_steps[step_index]
            if isinstance(step_index, int) and 0 <= step_index < len(compiled_steps)
            else {}
        )
        step_id = str((compiled_step or {}).get("step_id") or "")
        if kind == "placeholder_name":
            param_name = str(finding.get("param") or "")
            step = by_step_id.get(step_id)
            param = next((
                item for item in (step.params if step else [])
                if param_name in {item.key, item.label, item.path}
            ), None)
            target = {
                "kind": "param",
                "step_id": step_id,
                "path": param.path if param is not None else param_name,
                "key": param.key if param is not None else param_name,
            }
            group = "field"
        elif kind.startswith("capability_"):
            cap_ref = str(finding.get("capability") or "")
            field_name = str(finding.get("field") or "")
            target = {
                "kind": "capability_output" if "output" in kind else "capability",
                "capability": cap_ref,
                **({"field": field_name} if field_name else {}),
            }
            group = "capability"
        else:
            target = {"kind": "flow"}
        review_id = _review_id(f"compiled_{kind}", target)
        if review_id in resolved_review_ids:
            continue
        issue = {
            "severity": "warning",
            "message": str(finding.get("detail") or finding.get("message") or kind),
            "source": "review",
            "target": {key: value for key, value in target.items() if value not in (None, "")},
            "blocking": False,
            "ignorable": True,
            "audience": "operator",
            "actionable": True,
            "auto_fixable": False,
            "code": kind,
            "issue_id": f"review:{review_id}",
            "review_id": review_id,
        }
        groups.setdefault(group, []).append(issue)
    return groups


def _compiled_contract_review_items(
    spec: FlowSpec,
    *,
    prepared: bool = False,
) -> list[ReviewItem]:
    """Materialize unresolved compiled-contract advice as stable ReviewItems.

    Publish validation operates on a compiled ``api_request`` while ignore state
    lives in ``FlowSpec.review_items``.  Keeping these findings in both forms is
    what makes an operator dismissal survive the next prepare/validate cycle.
    """
    if not spec.capabilities:
        return []
    api_request, build_errors = flow_spec_to_api_request(spec, _prepared=prepared)
    if api_request is None or build_errors:
        return []
    from dano.execution.page.repair_ops import collect_repair_findings

    groups = _compiled_contract_issue_groups(
        spec,
        api_request,
        collect_repair_findings(api_request),
    )
    items: list[ReviewItem] = []
    for issues in groups.values():
        for issue in issues:
            message = str(issue.get("message") or "待确认的编译契约建议")
            items.append(ReviewItem(
                id=str(issue["review_id"]),
                type=f"compiled_{issue.get('code') or 'contract'}",
                severity="medium",
                title=message,
                target=dict(issue.get("target") or {}),
                current_guess="compiled_contract",
                suggested_action="review_compiled_contract",
                reason=message,
                blocking=False,
                ignorable=True,
            ))
    return items


def _generated_review_items(spec: FlowSpec, *, prepared: bool = False) -> list[ReviewItem]:
    """Build every generated review item with one stable-ID dedupe pass."""
    generated = [
        *build_review_items(spec),
        *_compiled_contract_review_items(spec, prepared=prepared),
    ]
    deduped: dict[str, ReviewItem] = {}
    for item in generated:
        existing = deduped.get(item.id)
        if existing is None or _severity_rank(item.severity) > _severity_rank(existing.severity):
            deduped[item.id] = item
    return list(deduped.values())


def _legacy_fact_check_is_grounded(spec: FlowSpec, step: FlowStep, fact_check: dict) -> bool:
    """Revalidate persisted checks against immutable request facts."""
    if not fact_check or (step.method or "").upper() not in {"POST", "PUT", "PATCH"}:
        return False
    facts = list((spec.request_facts or RequestFacts()).requests or [])
    if not facts:
        return False
    meta = step.source_meta or {}
    write_id = str(meta.get("request_id") or "")
    write_seq = _request_sequence_value(meta.get("sequence", meta.get("request_index")))
    write_fact = next((fact for fact in facts if write_id and fact.request_id == write_id), None)
    if write_fact is None and write_seq is not None:
        write_fact = next((fact for fact in facts if _request_sequence_value(fact.sequence) == write_seq), None)
    if write_fact is None:
        return False
    write_seq = _request_sequence_value(write_fact.sequence)
    if write_seq is None:
        return False

    endpoint_path = _request_path({"url": str(fact_check.get("endpoint") or "")})
    read_facts = [
        fact for fact in facts
        if _request_path({"url": fact.url or fact.path}) == endpoint_path
        and (_request_sequence_value(fact.sequence) or -1) > write_seq
        and str(((spec.request_facts.analysis or {}).get(fact.request_id) or RequestAnalysis()).role) == "business_get"
    ]
    if len(read_facts) != 1:
        return False
    read_fact = read_facts[0]
    write_tx = str(getattr(write_fact, "trigger_transaction_id", "") or "")
    read_tx = str(getattr(read_fact, "trigger_transaction_id", "") or "")
    if not (
        (write_tx and read_tx and write_tx == read_tx)
        or (_fact_path_tokens(write_fact.url or write_fact.path) & _fact_path_tokens(read_fact.url or read_fact.path))
    ):
        return False

    param_name = str(fact_check.get("param") or "")
    param = next((item for item in step.params if item.key == param_name), None)
    value = (step.sample_inputs or {}).get(param_name)
    if value in (None, "") and param is not None:
        value = param.value
    if param is None or value in (None, ""):
        return False
    match_field = str(fact_check.get("match_field") or "")
    matches = [
        item for item in (as_list_payload(read_fact.response_json) or [])
        if isinstance(item, dict) and match_field in item and str(item.get(match_field)) == str(value)
    ]
    return len(matches) == 1


def _executor_fact_check_is_verified(spec: FlowSpec, fact_check: dict) -> bool:
    """Executor-verified checks carry the verification_id minted by the write/read replay."""
    if fact_check.get("verified") is not True:
        return False
    verification_id = str(fact_check.get("verification_id") or "")
    if not verification_id:
        return False
    log = list((spec.meta or {}).get("verification_log") or [])
    if not log:
        # The op-level guard (`bind_verify_read`) already validated the record
        # when it was applied; the log may have been dropped by projections.
        return True
    from dano.execution.page.verification_log import find_verification

    record = find_verification(verification_id, log)
    if record is None:
        return False
    return record.get("status") == "passed"


def _prune_invalid_fact_checks(spec: FlowSpec) -> None:
    for step in spec.steps:
        if not step.fact_check:
            continue
        if _executor_fact_check_is_verified(spec, step.fact_check):
            continue
        if not _legacy_fact_check_is_grounded(spec, step, step.fact_check):
            step.fact_check = None


def prepare_flow_spec_for_publish(spec: FlowSpec) -> FlowSpec:
    """Canonicalize the current workbench state without invoking the Pi Agent."""
    current = sync_flow_spec_models(spec.model_copy(deep=True))
    _repair_structural_option_bindings(current)
    _refresh_api_option_display_labels(current)
    _apply_mechanical_field_contracts(current)
    _repair_readonly_control_defaults(current)
    _repair_uncontrolled_write_state_fields(current)
    _materialize_captured_response_key_maps(
        current.steps,
        current.links,
        [fact.model_dump(exclude_none=True) for fact in current.request_facts.requests],
    )
    _sync_link_sources(current.steps, current.links)
    by_step_id = {step.step_id: step for step in current.steps}
    public_anchor_ids = set(_public_capability_anchor_step_ids(current))
    for capability in current.capabilities:
        changed = True
        while changed:
            changed = False
            member_ids = set(_capability_node_step_ids(capability))
            for link in executable_flow_links(current):
                source = by_step_id.get(link.source_step_id)
                if (
                    link.target_step_id in member_ids
                    and link.source_step_id not in member_ids
                    and link.source_step_id not in public_anchor_ids
                    and source is not None
                    and not _is_write_step(source)
                ):
                    _add_step_id_to_capability(
                        current, capability, link.source_step_id,
                    )
                    changed = link.source_step_id in set(
                        _capability_node_step_ids(capability)
                    )
        _sync_capability_order(current, capability)
    _prune_invalid_fact_checks(current)
    _canonicalize_public_capability_identities(current)
    _normalize_capability_references(current)
    current = _ensure_capability_explanations(
        current,
        ((current.meta or {}).get("capability_model") or {}).get("semantic_plan") or {},
    )
    current = _ensure_external_transform_relations(_sync_capability_io_schemas(current))
    current = ensure_recorded_goal(current)
    # Verification and canonical schema projection can add trusted, derived
    # contract details after the planner originally accepted a capability.
    # Refresh only machine-owned confirmations on that final canonical shape;
    # user-owned/locked confirmations must continue to detect later edits.
    if bool(((current.meta or {}).get("verification_run") or {}).get("complete")):
        for cap in current.capabilities or []:
            if (
                cap.confirmed
                and not cap.locked
                and cap.updated_by in {"planner", "repair", "agent", "system"}
            ):
                cap.confirmation_hash = _capability_confirmation_hash(
                    current, cap, prepared=True,
                )
    return current


def prepare_flow_release_candidate(spec: FlowSpec) -> tuple[FlowSpec, dict[str, Any]]:
    """Freeze the exact canonical workbench contract consumed by publish/export."""
    current = prepare_flow_spec_for_publish(spec)
    # The release is persisted as JSON and reconstructed before its Pi review
    # is consumed.  Freeze that exact round-tripped model *before* computing
    # the fingerprint.  Previously ``_flow_fingerprint`` normalised a private
    # copy but this function returned the pre-normalised ``current`` object;
    # after a manual workbench edit the review therefore hashed one model while
    # the draft stored another.
    current = FlowSpec.model_validate(flow_spec_release_payload(current))
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
    # Keep this invariant next to the only release-freezing function.  A future
    # schema migration or derived-model synchroniser must fail here, before Pi
    # review and draft creation, rather than surface as a misleading publish
    # error after the operator has already waited for review.
    frozen = flow_spec_release_payload(current)
    frozen_fingerprint = _flow_fingerprint(FlowSpec.model_validate(frozen))
    if frozen_fingerprint != fingerprint:
        raise ValueError(
            "FlowSpec release snapshot is not serialization-stable: "
            f"{fingerprint} != {frozen_fingerprint}"
        )
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
    suggestions: list[str] = []
    active_step_ids = _active_capability_step_ids(spec)
    active_steps = [
        step for step in spec.steps
        if active_step_ids is None or step.step_id in active_step_ids
    ]
    review_items = refresh_review_items(
        spec.model_copy(deep=True), prepared=True,
    ).review_items
    blocking_reviews = [
        item for item in review_items
        if item.severity == "high" and not item.resolved and item.type in _PUBLISH_BLOCKING_REVIEW_TYPES
    ]
    # Review items and capability/field classifications are generator output,
    # not publish policy.  Keep them available to the editor as suggestions but
    # never place them in the publish issue list: doing so made a model guess
    # look like a mandatory system rule.
    suggestions.extend([f"生成建议: {item.title}" for item in blocking_reviews])
    diag_errors, diag_warnings = _diagnostic_publish_findings(spec)
    suggestions.extend([*diag_errors, *diag_warnings])
    capability_validation = _capability_validation_report(spec, prepared=True)
    capability_errors = list(capability_validation.get("errors") or [])
    capability_warnings = list(capability_validation.get("warnings") or [])
    # Capability validation describes the executable public contract.  A
    # malformed boundary or illegal membership cannot be repaired by the
    # lower-level request builder, so it is a hard publish error.
    errors.extend(capability_errors)
    suggestions.extend(capability_warnings)
    by_step_id = {step.step_id: step for step in spec.steps}
    for capability in spec.capabilities or []:
        cap_label = capability.title or capability.name or capability.capability_id
        caller_params = [
            param
            for step_id in _capability_node_step_ids(capability)
            for param in (by_step_id.get(step_id).params if by_step_id.get(step_id) else [])
            if _param_exposed_to_caller(param)
        ]
        caller_by_key: dict[str, list[ParamField]] = {}
        for param in caller_params:
            caller_by_key.setdefault(str(param.key or param.path or ""), []).append(param)
        for field_name, duplicates in caller_by_key.items():
            if len(duplicates) > 1 and any(
                not _params_can_share_caller_key(duplicates[0], other)
                for other in duplicates[1:]
            ):
                suggestions.append(f"Capability `{cap_label}` 输入字段 `{field_name}` 同名但对应不同请求字段，建议重命名或解除锁定后自动消歧")
        for field_name, field_schema in (capability.input_schema.get("properties") or {}).items():
            if isinstance(field_schema, dict) and field_schema.get("x-dano-conflicts"):
                suggestions.append(f"Capability `{cap_label}` 输入字段 `{field_name}` 在多个接口中类型或路径冲突")
        if capability.kind == "query_status":
            cap_steps = [by_step_id[sid] for sid in _capability_node_step_ids(capability) if sid in by_step_id]
            if cap_steps and not any(_is_business_query_step(step) for step in cap_steps):
                suggestions.append(f"Capability `{cap_label}` 没有返回业务记录/状态的查询接口，仅包含配置或前置接口")
    api_request, build_errors = flow_spec_to_api_request(spec, _prepared=True)
    errors.extend(build_errors)
    if not flow_spec_user_params(spec):
        suggestions.append("FlowSpec 没有 user_param，发布后的 Skill 不会要求用户输入参数")
    for st in active_steps:
        select_by_path = {s.path: s for s in st.selects if s.path}
        select_by_param = {s.param: s for s in st.selects if s.param}
        for p in st.params:
            enum_contract_error = _capability_param_enum_issue(p)
            if enum_contract_error:
                suggestions.append(f"枚举字段 `{p.key or p.path}` {enum_contract_error}")
            enum_contract_warning = _capability_param_enum_warning(p)
            if enum_contract_warning:
                suggestions.append(f"枚举字段 `{p.key or p.path}` {enum_contract_warning}")
            source_advice = _field_source_configuration_advice(p)
            if source_advice:
                suggestions.append(source_advice)
            if p.category == "runtime_var" and p.source_kind == "unknown":
                suggestions.append(f"字段 `{p.path}` 被判为 runtime_var，但来源仍需确认")
            if p.category == "system_const" and p.exposed_to_user:
                suggestions.append(f"字段 `{p.path}` 是 system_const，但仍暴露给用户")
            if p.source_kind == "api_option":
                sel = select_by_path.get(p.path) or select_by_param.get(p.key)
                if sel and sel.source_url and (sel.source_method or "GET").upper() not in {"GET", "HEAD"} and sel.source_role not in {
                    "business_get", "read_context", "read_option",
                }:
                    suggestions.append(
                        f"字段 `{p.key or p.path}` 的接口选项源 `{sel.source_method} {sel.source_url}` "
                        "未被识别为只读接口，运行期调用可能产生副作用"
                    )
            has_executable_api_options = p.source_kind == "api_option"
            if not has_executable_api_options and _param_looks_exposed_internal_value(p):
                suggestions.append(
                    f"字段 `{p.key or p.path}` 看起来是内部 ID/短码/空标识，不能直接暴露给用户；"
                    "请改为接口枚举映射或系统常量"
                )
            if (
                p.type in {"enum", "list-enum"}
                and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
                and p.enum_options
                and not _enum_map_covers_recorded_value(p)
            ):
                suggestions.append(
                    f"枚举字段 `{p.key or p.path}` 当前提交值 `{p.value}` 没有完整 label→value 映射，"
                    "请补充真实选项值映射或重新录制到字典接口"
                )
            if (
                p.type in {"enum", "list-enum"}
                and p.source_kind in {"page_enum", "static_enum", "manual_enum", "form_option"}
                and p.enum_options
                and _enum_options_look_value_only(p)
            ):
                suggestions.append(
                    f"枚举字段 `{p.key or p.path}` 的候选看起来全是内部值/短码，"
                    "不能作为用户可选项导出；请填写 `显示名=真实值`（如 `病假=2`）或重新录制真实下拉"
                )
    for lk in spec.links:
        if active_step_ids is not None and not (
            lk.source_step_id in active_step_ids and lk.target_step_id in active_step_ids
        ):
            continue
        if not lk.confirmed:
            suggestions.append(f"链接 `{lk.link_id}` 尚未人工确认")
    if active_steps and not any((st.success_rule for st in active_steps)):
        suggestions.append("未识别到明确 success_rule，运行期只能使用通用成功判断")
    self_check_errors: list[str] = []
    compiled_issue_groups: dict[str, list[dict[str, Any]]] = {}
    if api_request is not None:
        self_check_errors = self_check(api_request)
        suggestions.extend(self_check_errors)
        repair_findings = collect_repair_findings(api_request)
        compiled_issue_groups = _compiled_contract_issue_groups(
            spec,
            api_request,
            repair_findings,
            resolved_review_ids={item.id for item in review_items if item.resolved},
        )
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
        suggestions.extend(session_errors)
    dry_run = dry_run_flow_spec(spec, _prepared=True)
    errors = list(dict.fromkeys(str(item) for item in errors if item))
    warnings = list(dict.fromkeys(str(item) for item in warnings if item))
    suggestions = list(dict.fromkeys(str(item) for item in suggestions if item))
    # Generated ReviewItems remain advisory. Field-source items are also
    # projected into the operator field-warning group for visibility, while
    # staying explicitly ignorable and outside the publish pass/fail decision.
    issue_groups = _publish_issue_groups(errors, warnings)
    field_source_issues = _field_source_review_issues(review_items)
    if field_source_issues:
        issue_groups.setdefault("field", []).extend(field_source_issues)
    enum_mapping_issues = _enum_mapping_issues(active_steps)
    if enum_mapping_issues:
        issue_groups.setdefault("field", []).extend(enum_mapping_issues)
    for group, items in compiled_issue_groups.items():
        issue_groups.setdefault(group, []).extend(items)
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "issue_groups": issue_groups,
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
    if isinstance(node, dict):
        grounded_identity = any(
            node.get(key) not in (None, "", [])
            for key in ("wire_path", "path", "field_key", "field_aliases", "key", "field")
        )
        value_hint = next((
            str(node.get(key) or "")
            for key in ("wire_path", "path", "field_key", "key", "field")
            if node.get(key) not in (None, "")
        ), key_hint)
        aliases = " ".join(str(value) for value in (node.get("field_aliases") or []))
        if aliases:
            value_hint = f"{value_hint} {aliases}".strip()
        return {
            k: _client_redact_sensitive(
                v,
                value_hint if str(k) in {
                    "value", "selected_value", "visible_value", "default", "default_value",
                } else (
                    key_hint
                    if not grounded_identity and any(h in key_l for h in _CLIENT_SECRET_KEY_HINTS)
                    else str(k)
                ),
            )
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_client_redact_sensitive(v, key_hint) for v in node]
    if key_l and any(h in key_l for h in _CLIENT_SECRET_KEY_HINTS):
        return "***"
    return node


def _client_response_projection(value: Any) -> tuple[Any, dict[str, Any]]:
    """Return a bounded UI sample plus facts describing the raw response."""
    sample = bounded_response_sample(value)
    try:
        raw_chars = len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
        sample_chars = len(json.dumps(sample, ensure_ascii=False, separators=(",", ":"), default=str))
    except Exception:  # noqa: BLE001 - projection metadata is best effort
        raw_chars = sample_chars = 0
    return sample, {
        "raw_chars": raw_chars,
        "sample_chars": sample_chars,
        "truncated": bool(raw_chars > sample_chars),
        "normalized_paths": normalized_leaf_paths(value),
    }


_PUBLIC_SOURCE_BY_INTERNAL = {
    "user_input": "caller_input",
    "api_option": "caller_input",
    "page_enum": "caller_input",
    "static_enum": "caller_input",
    "manual_enum": "caller_input",
    "form_option": "caller_input",
    "constant": "constant",
    "page_default": "caller_input",
    "page_rule": "constant",
    "request_header": "session",
    "current_user": "session",
    "storage": "session",
    "cookie": "session",
    "page_context": "context",
    "previous_response": "response_binding",
    "dynamic_structure": "response_binding",
    "selected_option_field": "computed",
    "computed": "computed",
    "system_time": "generated",
    "system_generated": "generated",
    "generated": "generated",
    "unknown": "unknown",
}

_CAPABILITY_REF_USAGE_ORDER = {
    "option_source": 0,
    "preflight": 1,
    "execute": 2,
    "fact_check": 3,
}

_PUBLIC_ROLE_BY_INTERNAL = {
    "auth": "auth",
    "noise": "support",
    "telemetry": "support",
    "unsupported_upload": "support",
    "unsupported_graphql": "support",
    "read_option": "option",
    "option_source": "option",
    "explicit_read_option": "option",
    "read_context": "context",
    "business_get": "business_read",
    "business_write": "business_write",
    "submit_anchor": "business_write",
}


def _public_source_kind(param: dict[str, Any]) -> str:
    internal = str(param.get("source_kind") or "")
    if internal in _PUBLIC_SOURCE_BY_INTERNAL:
        return _PUBLIC_SOURCE_BY_INTERNAL[internal]
    if internal == "unknown":
        return "unknown"
    return "caller_input" if param.get("exposed_to_user") else "constant"


def _ordered_capability_request_refs(refs: list[Any]) -> list[Any]:
    return sorted(
        list(refs or []),
        key=lambda ref: (
            _CAPABILITY_REF_USAGE_ORDER.get(str(getattr(ref, "usage", None) or "execute"), 9),
            getattr(ref, "sequence", None) if getattr(ref, "sequence", None) is not None else 10**9,
            str(getattr(ref, "path", "") or ""),
            str(getattr(ref, "step_id", "") or ""),
            str(getattr(ref, "request_id", "") or ""),
        ),
    )


def _public_request_role(role: Any) -> str:
    return _PUBLIC_ROLE_BY_INTERNAL.get(str(role or ""), "support")


def flow_spec_to_client(spec: FlowSpec) -> dict:
    """Return the bounded, redacted projection used by the recording workbench.

    The browser never returns this projection as an authoritative FlowSpec, so
    raw request bodies, transport headers, identities and full responses can
    remain exclusively on the server.
    """
    client_spec = sync_flow_spec_models(spec.model_copy(deep=True))
    _normalize_capability_references(client_spec)
    data = refresh_review_items(_sync_capability_io_schemas(client_spec)).model_dump()
    data["meta"] = {**(data.get("meta") or {}), "current_fingerprint": _flow_fingerprint(spec)}
    data["meta"].pop("request_graph", None)
    request_facts = data.get("request_facts") or {}
    for analysis in (request_facts.get("analysis") or {}).values():
        if isinstance(analysis, dict):
            analysis["role"] = _public_request_role(analysis.get("role"))
    for evidence_key in ("field_evidence", "option_sources", "page_events"):
        if request_facts.get(evidence_key):
            request_facts[evidence_key] = _client_redact_sensitive(request_facts[evidence_key])
    for req in request_facts.get("requests") or []:
        if req.get("headers"):
            req["headers"] = {k: "***" for k in (req.get("headers") or {})}
        if req.get("post_data") is not None:
            req["post_data"] = ""
        if req.get("response_json") is not None:
            projected, projection = _client_response_projection(req.get("response_json"))
            req["response_json"] = _client_redact_sensitive(projected)
            req["response_projection"] = projection
    for st in data.get("steps") or []:
        st["semantic_role"] = _public_request_role(st.get("semantic_role"))
        if isinstance(st.get("source_meta"), dict) and st["source_meta"].get("role"):
            st["source_meta"]["role"] = _public_request_role(st["source_meta"].get("role"))
        st["headers"] = {k: "***" for k in (st.get("headers") or {})}
        st["body_source"] = ""
        st["backup_body_source"] = ""
        for param in st.get("params") or []:
            if not isinstance(param, dict):
                continue
            # Keep the evidence-backed origin (api_option / page_enum /
            # page_default / page_rule / ...). The 7-kind public contract is
            # only a grouping of who supplies the value.
            param["public_source_kind"] = _public_source_kind(param)
            # ``category`` is an internal compatibility value derived from
            # source_kind.  Exposing both axes lets clients create impossible
            # combinations, so the workbench owns only the executable source.
            param.pop("category", None)
            if isinstance(param.get("source"), dict) and not param["source"].get("kind"):
                param["source"] = {**param["source"], "kind": param["source_kind"]}
        if st.get("response_json") is not None:
            projected, projection = _client_response_projection(st.get("response_json"))
            st["response_json"] = _client_redact_sensitive(projected)
            st["response_projection"] = projection
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


def _find_param(step: FlowStep, param_path: str, *, field_id: str = "", param_key: str = "", param_label: str = "") -> ParamField:
    stable_id = str(field_id or "")
    if stable_id:
        matched = next((param for param in step.params if param.field_id == stable_id), None)
        if matched is not None:
            return matched
    needle = str(param_path or "")
    for param in step.params:
        if param.path == needle:
            return param
    available = [f"{p.path}({p.key})" for p in step.params]
    raise ValueError(f"param not found: {param_path} in step {step.step_id}; available={available}")


def _resolve_param_reference(step: FlowStep, reference_path: str) -> ParamField | None:
    """Resolve legacy body-prefixed paths without collapsing distinct fields."""
    reference = str(reference_path or "")
    if not reference:
        return None
    exact = next((param for param in step.params if param.path == reference), None)
    if exact is not None:
        return exact
    normalized = _strip_body_prefix(reference)
    matches = [
        param for param in step.params
        if _strip_body_prefix(param.path) == normalized
    ]
    return matches[0] if len(matches) == 1 else None


def _reference_targets_param(step: FlowStep, reference_path: str, param: ParamField) -> bool:
    return _resolve_param_reference(step, reference_path) is param


def _find_link(spec: FlowSpec, link_id: str) -> FlowLink:
    for link in spec.links:
        if link.link_id == link_id:
            return link
    available = [link.link_id for link in spec.links]
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
            wire_type=_param_type_from_value(value),
            required=bool(source_guess.get("required")),
            category=source_guess["category"],
            source_kind=source_guess["source_kind"],
            source={**source_guess["source"], "from": "query"},
            exposed_to_user=bool(source_guess["exposed_to_user"]),
            editable=bool(source_guess["editable"]),
            need_human_confirm=bool(source_guess["need_human_confirm"]),
            default_value=(
                value if _looks_pagination_field(key, path) else None
            ),
            reason=source_guess["reason"],
        ))
        if value not in (None, ""):
            step.sample_inputs.setdefault(key, value)
        existing.add(path)
        existing_keys.add(key)


def _option_binding_tokens(value: Any) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]*|[\u4e00-\u9fff]+", text)
        if token
    }
    expanded = set(tokens)
    expanded.update(token[:-1] for token in tokens if token.endswith("s") and len(token) > 3)
    return expanded - {
        "id", "ids", "code", "key", "value", "values", "data", "api", "admin",
        "list", "simple", "options", "option", "query", "page", "get",
        # These words describe generic payloads/endpoints and cannot establish
        # that a caller-entered field owns an option source.
        "info", "information", "name", "title", "label", "text",
    }


def _weak_automatic_text_option_binding(param: ParamField) -> bool:
    """Identify an ungrounded automatic option binding on a text-like field.

    This intentionally does not override manual edits or a recorded select;
    callers enforce edit ownership separately.  It only marks bindings whose
    endpoint has no semantic bridge to the field after generic tokens such as
    ``info`` have been removed.
    """
    if (
        param.source_kind != "api_option"
        or not _looks_user_entered_business_field(param.key, param.path)
    ):
        return False
    option_controls = {
        "select", "combobox", "cascader", "picker", "radio", "tree_select",
    }
    if any(
        isinstance(item, dict)
        and str(item.get("control_kind") or "").strip().lower() in option_controls
        for item in (param.evidence or [])
    ):
        return False
    target_text = " ".join((str(param.path or ""), str(param.key or ""), str(param.label or "")))
    source_text = str((param.source or {}).get("source_url") or "")
    if not source_text:
        return False
    return not bool(
        (_option_binding_tokens(target_text) & _option_binding_tokens(source_text))
        or (
            _option_binding_semantic_families(target_text)
            & _option_binding_semantic_families(source_text)
        )
    )
def _option_binding_semantic_families(value: Any) -> set[str]:
    text = str(value or "").casefold()
    families: set[str] = set()
    patterns = {
        "person": r"(?:user|users|assignee|approver|reviewer|auditor|employee|member|person|people|审批|审核|人员|用户|负责人)",
        "organization": r"(?:dept|department|org|organization|division|unit|部门|组织|机构)",
        "team": r"(?:team|group|squad|团队|班组|小组)",
        "project": r"(?:project|initiative|项目)",
        "role": r"(?:role|position|post|岗位|角色|职位)",
        "customer": r"(?:customer|client|buyer|客户|购买方)",
        "supplier": r"(?:supplier|vendor|provider|供应商|供方)",
    }
    for family, pattern in patterns.items():
        if re.search(pattern, text, re.I):
            families.add(family)
    return families


def _looks_quantitative_option_target(param: ParamField) -> bool:
    """Counts and measurements are scalar inputs, not foreign-key choices."""
    text = " ".join((str(param.path or ""), str(param.key or ""), str(param.label or "")))
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).casefold()
    compact = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).casefold()
    if re.search(r"(?:id|ids|code|key)$", compact):
        return False
    numeric_wire = str(param.wire_type or param.type or "").lower() in {
        "number", "integer", "float", "decimal",
    }
    return bool(
        re.search(r"(?:^|\W)(?:count|qty|quantity|amount|total|capacity)(?:$|\W)", split)
        or (
            numeric_wire
            and re.search(r"(?:^|\W)(?:num|number)(?:$|\W)", split)
        )
        or re.search(r"(?:人数|数量|个数|金额|总数|总量|容量|次数|时长|天数)", text)
    )


def _repair_structural_option_bindings(spec: FlowSpec) -> int:
    """Recover grounded enum/reference bindings, including captured-only reads.

    Candidate selection is evidence based: an exact recorded wire value, a real
    ID/display row contract, and either control ownership, a shared semantic
    family, or an exact field token are required. Shared dictionary endpoints
    are narrowed only when the page's visible labels identify one category.
    Repeated captures of the same endpoint/contract are one source, not an
    ambiguity.
    """
    candidates: list[dict[str, Any]] = []
    materialized_request_ids: set[str] = set()
    for source in spec.steps:
        role = str((source.source_meta or {}).get("role") or source.semantic_role or "")
        request_id = str((source.source_meta or {}).get("request_id") or "")
        read = {
            "url": source.url or source.path,
            "path": source.path,
            "method": source.method,
            "role": role,
            "response_json": source.response_json,
            "post_data": source.body_source,
            **dict(source.source_meta or {}),
        }
        items = as_list_payload(source.response_json) or []
        entity_collection_source = _read_is_business_entity_collection(
            read, source.response_json,
        )
        if (
            _read_transport_can_supply_options(read)
            and items
            and all(isinstance(item, dict) for item in items)
            and not _read_is_entity_enrichment_lookup(read)
            and (
                _read_is_option_source(read)
                or entity_collection_source
                or (
                    _list_payload_has_reference_contract(source.response_json)
                    and (
                        _request_has_option_endpoint_hint(read)
                        or _request_has_reference_entity_hint(read)
                    )
                )
            )
        ):
            candidates.append({
                "source_step_id": source.step_id,
                "source_request_id": request_id,
                "source_url": source.url or source.path,
                "sequence": (source.source_meta or {}).get("sequence", (source.source_meta or {}).get("request_index")),
                "page_id": (source.source_meta or {}).get("page_id"),
                "frame_id": (source.source_meta or {}).get("frame_id"),
                "trigger_action_id": (source.source_meta or {}).get("trigger_action_id"),
                "trigger_transaction_id": (source.source_meta or {}).get("trigger_transaction_id"),
                "explicit_option_source": role in {
                    "read_option", "option_source", "explicit_read_option",
                } or _choice_control_triggered(read),
                "entity_collection_source": entity_collection_source,
                "items": [dict(item) for item in items if isinstance(item, dict)],
            })
            if request_id:
                materialized_request_ids.add(request_id)

    # Reuse an already confirmed binding as ordinary candidate evidence. The
    # existing unique matcher can then ground repeated sibling fields even when
    # their projected request rows were truncated.
    for source in spec.steps:
        for binding in source.selects or []:
            option_map = dict(binding.option_map or {})
            if not (
                binding.enum_confirmed is True
                and binding.source_url and binding.value_key and binding.label_key
                and len(option_map) >= 2
            ):
                continue
            owner = next((
                param for param in (source.params or [])
                if _strip_body_prefix(param.path or "")
                == _strip_body_prefix(binding.path or binding.id_path or "")
            ), None)
            if owner is not None and _param_has_grounded_direct_input_contract(owner):
                continue
            owner_source = dict((owner.source if owner is not None else None) or {})
            candidates.append({
                "source_step_id": str(owner_source.get("source_step_id") or ""),
                "source_request_id": str(owner_source.get("source_request_id") or ""),
                "source_url": binding.source_url,
                "sequence": (source.source_meta or {}).get("sequence"),
                "page_id": (source.source_meta or {}).get("page_id"),
                "frame_id": (source.source_meta or {}).get("frame_id"),
                "trigger_action_id": (source.source_meta or {}).get("trigger_action_id"),
                "trigger_transaction_id": (source.source_meta or {}).get("trigger_transaction_id"),
                "items": [
                    {binding.value_key: value, binding.label_key: label}
                    for label, value in option_map.items()
                ],
            })

    for fact in (spec.request_facts.requests or []):
        request_id = str(fact.request_id or "")
        if request_id and request_id in materialized_request_ids:
            continue
        analysis = spec.request_facts.analysis.get(request_id) if request_id else None
        read = fact.model_dump(exclude_none=True)
        read["role"] = str(analysis.role if analysis is not None else read.get("role") or "")
        items = as_list_payload(fact.response_json) or []
        entity_collection_source = _read_is_business_entity_collection(
            read, fact.response_json,
        )
        if (
            _read_transport_can_supply_options(read)
            and items
            and all(isinstance(item, dict) for item in items)
            and not _read_is_entity_enrichment_lookup(read)
            and (
                _read_is_option_source(read)
                or entity_collection_source
                or (
                    _list_payload_has_reference_contract(fact.response_json)
                    and (
                        _request_has_option_endpoint_hint(read)
                        or _request_has_reference_entity_hint(read)
                    )
                )
            )
        ):
            candidates.append({
                "source_step_id": "",
                "source_request_id": request_id,
                "source_url": fact.url or fact.path,
                "sequence": fact.sequence if fact.sequence is not None else fact.request_index,
                "page_id": fact.page_id,
                "frame_id": fact.frame_id,
                "trigger_action_id": getattr(fact, "trigger_action_id", None),
                "trigger_transaction_id": getattr(fact, "trigger_transaction_id", None),
                "explicit_option_source": read["role"] in {
                    "read_option", "option_source", "explicit_read_option",
                } or _choice_control_triggered(read),
                "entity_collection_source": entity_collection_source,
                "items": [dict(item) for item in items if isinstance(item, dict)],
            })

    repaired = 0

    def has_screenshot_choice(param: ParamField) -> bool:
        control = _screenshot_control_evidence({"evidence": param.evidence})
        return bool(
            control is not None
            and (
                _screenshot_control_supports_axis(control, "type")
                or _screenshot_control_supports_axis(control, "source")
            )
            and str(control.get("control_kind") or "").lower()
            in _SCREENSHOT_OPTION_CONTROL_KINDS
        )

    def has_recorded_choice(param: ParamField) -> bool:
        return has_screenshot_choice(param) or any(
            isinstance(item, dict)
            and str(item.get("source") or item.get("kind") or "").lower()
            in {"recorder_dom", "page", "page_snapshot", "page_control"}
            and str(item.get("control_kind") or "").lower()
            in _SCREENSHOT_OPTION_CONTROL_KINDS
            for item in (param.evidence or [])
        )

    for target in spec.steps:
        direct_input_paths: set[str] = set()
        for param in target.params or []:
            if (
                param.source_kind not in _OPTION_SOURCE_KINDS
                or param.locked
                or _param_has_manual_contract(param)
                or not _param_has_grounded_direct_input_contract(param)
            ):
                continue
            param.type = str(param.wire_type or "string")
            param.source_kind = "user_input"
            param.source = {"kind": "user_input", "path": param.path}
            param.enum_options = None
            param.enum_value_map = None
            _refresh_param_enum_description(param)
            direct_input_paths.add(_strip_body_prefix(param.path or ""))
            repaired += 1
        if direct_input_paths:
            target.selects = [
                binding for binding in (target.selects or [])
                if _strip_body_prefix(binding.path or binding.id_path or "") not in direct_input_paths
            ]

    page_options = _page_enum_options_from_request_facts(spec.request_facts)

    def normalized(value: Any) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).casefold()

    def option_labels(raw: dict[str, Any]) -> set[str]:
        labels = set()
        for option in list(raw.get("options") or raw.get("values") or []):
            pair = _enum_label_value(option)
            label = str(pair[0] if pair is not None else "").strip()
            if label:
                labels.add(label)
        return labels

    eligible_values = [
        str(param.value if param.value is not None else "").strip()
        for step in spec.steps
        for param in (step.params or [])
        if not param.locked
        and not _param_has_manual_contract(param)
        and not _param_has_grounded_direct_input_contract(param)
        and param.category != "system_const"
        and str(param.value if param.value is not None else "").strip()
    ]
    value_owner_counts = {value: eligible_values.count(value) for value in set(eligible_values)}

    def page_evidence_for(target: FlowStep, param: ParamField, value: str) -> list[dict[str, Any]]:
        param_names = {
            normalized(name)
            for name in (
                param.path, _strip_body_prefix(param.path or ""),
                _strip_body_prefix(param.path or "").split(".")[-1],
                param.key, param.label,
            )
            if normalized(name)
        }
        for evidence in param.evidence or []:
            if not isinstance(evidence, dict):
                continue
            param_names.update(
                normalized(str(alias).split(":", 1)[-1])
                for alias in (evidence.get("field_aliases") or [])
                if normalized(str(alias).split(":", 1)[-1])
            )
        screenshot_matches: list[dict[str, Any]] = []
        screenshot_evidence = _screenshot_control_evidence({"evidence": param.evidence})
        for evidence in [screenshot_evidence] if screenshot_evidence is not None else []:
            if str(evidence.get("control_kind") or "").lower() not in _SCREENSHOT_OPTION_CONTROL_KINDS:
                continue
            labels = option_labels({"options": evidence.get("options") or []})
            if len(labels) < 2:
                continue
            visible_value = str(evidence.get("visible_value") or "").strip()
            screenshot_matches.append({
                "raw": evidence,
                "labels": labels,
                "selected": visible_value if visible_value in labels else "",
                "semantic_match": True,
            })
        if screenshot_matches:
            return screenshot_matches
        semantic_matches: list[dict[str, Any]] = []
        fallback_matches: list[dict[str, Any]] = []
        target_meta = target.source_meta or {}
        for raw_key, raw_value in page_options.items():
            if not isinstance(raw_value, dict):
                continue
            raw = dict(raw_value)
            labels = option_labels(raw)
            if len(labels) < 2 or str(raw.get("control_kind") or "select").lower() != "select":
                continue
            if target_meta.get("page_id") and raw.get("page_id") and str(target_meta.get("page_id")) != str(raw.get("page_id")):
                continue
            raw_names = {
                normalized(str(name).split(":", 1)[-1])
                for name in (raw_key, raw.get("field_key"), *(raw.get("field_aliases") or []))
                if normalized(str(name).split(":", 1)[-1])
            }
            contract = {
                "raw": raw,
                "labels": labels,
                "selected": str(raw.get("selected_label") or raw.get("selected") or "").strip(),
                "semantic_match": bool(param_names & raw_names),
            }
            if param_names & raw_names:
                semantic_matches.append(contract)
            elif value_owner_counts.get(value) == 1 and contract["selected"]:
                fallback_matches.append(contract)
        return semantic_matches or fallback_matches

    def row_contracts(
        items: list[dict[str, Any]],
        value: str,
        page_contract: dict[str, Any] | None,
        *,
        allow_single: bool = False,
    ) -> list[dict[str, Any]]:
        matching_items = [
            item for item in items
            if any(_is_idlike(str(key)) and str(item_value) == value for key, item_value in item.items())
        ]
        value_keys = {
            str(key)
            for item in matching_items
            for key, item_value in item.items()
            if _is_idlike(str(key)) and str(item_value) == value
        }
        contracts: list[dict[str, Any]] = []
        visible_labels = set(page_contract.get("labels") or set()) if page_contract else set()
        selected_label = str(page_contract.get("selected") or "") if page_contract else ""
        for value_key in value_keys:
            label_keys = {
                _pick_label_key(item, value_key)
                for item in matching_items
                if _pick_label_key(item, value_key) != value_key
            }
            for label_key in label_keys:
                subsets: list[tuple[str | None, Any, list[dict[str, Any]]]] = [(None, None, items)]
                if page_contract:
                    scalar_keys = {
                        str(key)
                        for item in items
                        for key, raw_value in item.items()
                        if key not in {value_key, label_key}
                        and not isinstance(raw_value, (dict, list))
                    }
                    for category_key in scalar_keys:
                        category_values = {
                            item.get(category_key)
                            for item in matching_items
                            if item.get(category_key) not in (None, "")
                            and not isinstance(item.get(category_key), (dict, list))
                        }
                        for category_value in category_values:
                            subset = [
                                item for item in items
                                if str(item.get(category_key)) == str(category_value)
                            ]
                            if len(subset) >= 2:
                                subsets.append((category_key, category_value, subset))
                seen_subsets: set[str] = set()
                for category_key, category_value, subset in subsets:
                    subset_sig = json.dumps(
                        [category_key, category_value, subset],
                        ensure_ascii=False, sort_keys=True, default=str,
                    )
                    if subset_sig in seen_subsets:
                        continue
                    seen_subsets.add(subset_sig)
                    selected_rows = [item for item in subset if str(item.get(value_key)) == value]
                    if len(selected_rows) != 1:
                        continue
                    records: list[dict[str, Any]] = []
                    option_map: dict[str, Any] = {}
                    seen_values: set[str] = set()
                    valid = True
                    for item in subset:
                        label = str(item.get(label_key) or "").strip()
                        raw_value = item.get(value_key)
                        value_sig = str(raw_value)
                        if not label or raw_value in (None, "") or label in option_map or value_sig in seen_values:
                            valid = False
                            break
                        seen_values.add(value_sig)
                        option_map[label] = raw_value
                        records.append({"label": label, "value": raw_value})
                    if not valid or len(records) < (1 if allow_single else 2):
                        continue
                    if page_contract:
                        record_labels = set(option_map)
                        required_overlap = min(2, len(visible_labels))
                        if len(record_labels & visible_labels) < required_overlap:
                            continue
                        if selected_label and (
                            selected_label not in option_map
                            or str(option_map[selected_label]) != value
                        ):
                            continue
                    contracts.append({
                        "value_key": value_key,
                        "label_key": label_key,
                        "category_key": category_key,
                        "category_value": category_value,
                        "records": records,
                        "option_map": option_map,
                    })
        if page_contract:
            exact_label_contracts = [
                contract for contract in contracts
                if set(contract["option_map"]) == visible_labels
            ]
            if exact_label_contracts:
                return exact_label_contracts
        return contracts

    def source_is_grounded_for_target(
        source: dict[str, Any],
        target: FlowStep,
        page_contract: dict[str, Any] | None,
        semantic_match: bool,
    ) -> bool:
        """Require a causal, explicit, or semantic bridge; value equality is never enough."""
        if semantic_match:
            return True
        source_tx = str(source.get("trigger_transaction_id") or "")
        source_action = str(source.get("trigger_action_id") or "")
        target_meta = target.source_meta or {}
        target_tx = str(target_meta.get("trigger_transaction_id") or "")
        target_action = str(target_meta.get("trigger_action_id") or "")
        source_page = str(source.get("page_id") or "")
        source_frame = str(source.get("frame_id") or "")
        target_page = str(target_meta.get("page_id") or "")
        target_frame = str(target_meta.get("frame_id") or "")
        same_action_context = bool(
            source_action
            and source_action == target_action
            and not (source_page and target_page and source_page != target_page)
            and not (source_frame and target_frame and source_frame != target_frame)
        )
        if (source_tx and source_tx == target_tx) or same_action_context:
            return True
        if page_contract is None:
            return False
        raw = page_contract.get("raw") or {}
        source_request_id = str(source.get("source_request_id") or "")
        page_request_ids = {
            str(item) for item in (raw.get("source_request_ids") or []) if item not in (None, "")
        }
        if source_request_id and source_request_id in page_request_ids:
            return True
        source_url = str(source.get("source_url") or "")
        page_source_urls = {
            str(item) for item in (
                raw.get("source_url"),
                *(raw.get("source_urls") or []),
            ) if item not in (None, "")
        }
        if source_url and source_url in page_source_urls:
            return True
        page_tx = str(raw.get("trigger_transaction_id") or raw.get("transaction_id") or "")
        page_action = str(raw.get("trigger_action_id") or raw.get("action_id") or "")
        page_id = str(raw.get("page_id") or "")
        frame_id = str(raw.get("frame_id") or "")
        return bool(
            (source_tx and source_tx == page_tx)
            or (
                source_action
                and source_action == page_action
                and not (source_page and page_id and source_page != page_id)
                and not (source_frame and frame_id and source_frame != frame_id)
            )
        )

    for target in spec.steps:
        for param in target.params or []:
            selected_entity_target = bool(
                str((param.source or {}).get("kind") or "") == "selected_entity_id"
                and re.sub(
                    r"[^a-z0-9]+", "",
                    str(param.path or param.key).split(".")[-1].casefold(),
                ) == "id"
            )
            rebindable_option = bool(
                param.source_kind == "api_option" and has_screenshot_choice(param)
            ) or bool(
                param.source_kind == "page_enum"
                and (
                    (param.source or {}).get("enum_confirmed") is False
                    or not _incomplete_page_enum_is_executable(param)
                )
            )
            if (
                param.locked
                or param.source_kind in {"dynamic_structure", "selected_option_field"}
                or _param_has_manual_contract(param)
                or _param_has_grounded_direct_input_contract(param)
                or (
                    param.source_kind == "previous_response"
                    and isinstance((param.source or {}).get("option_source"), dict)
                )
                or (
                    param.source_kind in _OPTION_SOURCE_KINDS
                    and not rebindable_option
                )
                or _looks_pagination_field(param.key, param.path)
                or (
                    _looks_quantitative_option_target(param)
                    and not has_recorded_choice(param)
                )
                or (
                    not has_recorded_choice(param)
                    and (
                        _param_is_quantity_or_formula_leaf(param.key, param.path)
                        or _looks_unit_price_formula_leaf(param.key, param.path)
                        or _looks_display_echo_field(target, param)
                        or _looks_catalog_attribute_leaf(param.key, param.path)
                    )
                )
                or param.category == "system_const"
            ):
                continue
            value = str(param.value if param.value is not None else "").strip()
            if not value:
                continue
            target_text = " ".join((str(param.path or ""), str(param.key or ""), str(param.label or "")))
            target_tokens = _option_binding_tokens(target_text)
            target_families = _option_binding_semantic_families(target_text)
            page_contracts = page_evidence_for(target, param, value)
            matches: list[dict[str, Any]] = []
            for source in candidates:
                if source.get("entity_collection_source") is True and not selected_entity_target:
                    continue
                items = source["items"]
                source_url = str(source.get("source_url") or "")
                source_text = " ".join([
                    source_url,
                    *[str(key) for item in items[:3] for key in item.keys()],
                ])
                source_tokens = _option_binding_tokens(source_text)
                source_families = _option_binding_semantic_families(source_text)
                semantic_match = bool(
                    (target_tokens & source_tokens)
                    or (target_families & source_families)
                    or (
                        selected_entity_target
                        and source.get("entity_collection_source") is True
                    )
                )
                # Page evidence is a strong bridge when it describes this
                # source, but an unrelated open popup must not suppress a
                # valid semantic source (for example an approver directory
                # captured while the leave-type popup is still visible).
                source_page_contracts = [*page_contracts, None] if page_contracts else [None]
                for page_contract in source_page_contracts:
                    # Timing plus a matching scalar value does not identify an
                    # option owner. Require either field-local visible options
                    # or a semantic bridge to the candidate endpoint.
                    if page_contract is None and not semantic_match:
                        continue
                    if not source_is_grounded_for_target(source, target, page_contract, semantic_match):
                        continue
                    allow_single = bool(
                        source.get("explicit_option_source")
                        or (
                            value_owner_counts.get(value) == 1
                            and page_contract is not None
                        )
                    )
                    for contract in row_contracts(
                        items, value, page_contract, allow_single=allow_single,
                    ):
                        matches.append({**source, **contract})
            unique: dict[tuple[Any, ...], dict[str, Any]] = {}
            for match in matches:
                endpoint = _option_source_contract_endpoint(
                    str(match.get("source_url") or "")
                )
                selected_labels = tuple(sorted(
                    str(label)
                    for label, option_value in match["option_map"].items()
                    if str(option_value) == value
                ))
                fingerprint = (
                    endpoint,
                    match["value_key"], match["label_key"],
                    str(match.get("category_key") or ""),
                    str(match.get("category_value") or ""),
                    selected_labels,
                )
                previous = unique.get(fingerprint)

                def rank(item: dict[str, Any]) -> tuple[int, int, int, float]:
                    try:
                        sequence = float(item.get("sequence"))
                    except (TypeError, ValueError):
                        sequence = -1.0
                    return (
                        len(item.get("records") or []),
                        1 if item.get("explicit_option_source") else 0,
                        1 if item.get("source_step_id") else 0,
                        sequence,
                    )

                if previous is None or rank(match) > rank(previous):
                    unique[fingerprint] = match
            if len(unique) != 1:
                continue
            match = next(iter(unique.values()))
            multi = bool(param.type in {"array", "list-enum"} and not re.search(r"\[\d+\]$", param.path or ""))
            _bind_option_source(
                spec,
                target_step_id=target.step_id,
                target_path=param.path,
                source_step_id=str(match.get("source_step_id") or ""),
                source_url=str(match.get("source_url") or ""),
                source_request_id=str(match.get("source_request_id") or ""),
                value_key=match["value_key"],
                label_key=match["label_key"],
                category_key=match.get("category_key"),
                category_value=match.get("category_value"),
                id_path=param.path,
                options=match["records"],
                option_map=match["option_map"],
                multi=multi,
                actor="recorder",
            )
            if selected_entity_target:
                selected_rows = [
                    item for item in match["items"]
                    if str(item.get(match["value_key"])) == value
                ]
                selector = _find_select_binding(target, param)
                if len(selected_rows) == 1 and selector is not None:
                    selected_row = selected_rows[0]
                    projected_paths: set[str] = set()
                    for sibling in target.params or []:
                        if sibling is param or sibling.source_kind != "api_option":
                            continue
                        sibling_source = sibling.source or {}
                        sibling_endpoint = _option_source_contract_endpoint(
                            str(sibling_source.get("source_url") or "")
                        )
                        selected_endpoint = _option_source_contract_endpoint(
                            str(match.get("source_url") or "")
                        )
                        same_source = bool(
                            (
                                sibling_source.get("source_request_id")
                                and sibling_source.get("source_request_id")
                                == match.get("source_request_id")
                            )
                            or (
                                sibling_endpoint
                                and selected_endpoint
                                and sibling_endpoint == selected_endpoint
                            )
                        )
                        response_path = str(sibling_source.get("value_key") or "")
                        if not (
                            same_source
                            and response_path
                            and _recorded_scalar_values_match(
                                sibling.value,
                                _flow_path_lookup(selected_row, response_path),
                            )
                        ):
                            continue
                        selector.field_projections[sibling.path] = response_path
                        sibling.category = "runtime_var"
                        sibling.source_kind = "selected_option_field"
                        sibling.source = {
                            "kind": "selected_option_field",
                            "selector_path": param.path,
                            "selector_param": param.key,
                            "source_url": str(match.get("source_url") or ""),
                            "response_path": response_path,
                            "target_path": sibling.path,
                        }
                        sibling.exposed_to_user = False
                        sibling.editable = False
                        sibling.required = False
                        sibling.need_human_confirm = False
                        sibling.reason = (
                            f"该字段来自所选记录的 `{response_path}`，运行期随实体选择自动写入"
                        )
                        projected_paths.add(sibling.path)
                    for sibling in target.params or []:
                        if (
                            sibling is param
                            or sibling.path in projected_paths
                            or sibling.locked
                            or sibling.source_kind in {
                                "user_input", "page_default", "constant", "system_time",
                                "system_generated", "computed", "current_user",
                                "dynamic_structure", "selected_option_field",
                            }
                            or _param_has_editable_control_evidence(sibling)
                            or _looks_user_entered_business_field(
                                sibling.key, sibling.path,
                            )
                            or _param_is_quantity_or_formula_leaf(sibling.key, sibling.path)
                        ):
                            continue
                        response_path = _best_option_projection_path(
                            selected_row, sibling.path, sibling.value,
                        )
                        if not response_path:
                            continue
                        selector.field_projections[sibling.path] = response_path
                        sibling.category = "runtime_var"
                        sibling.source_kind = "selected_option_field"
                        sibling.source = {
                            "kind": "selected_option_field",
                            "selector_path": param.path,
                            "selector_param": param.key,
                            "source_url": str(match.get("source_url") or ""),
                            "response_path": response_path,
                            "target_path": sibling.path,
                        }
                        sibling.exposed_to_user = False
                        sibling.editable = False
                        sibling.required = False
                        sibling.need_human_confirm = False
                        sibling.reason = (
                            f"该字段来自所选记录的 `{response_path}`，运行期随实体选择自动写入"
                        )
                        projected_paths.add(sibling.path)
                    if projected_paths:
                        target.selects = [
                            binding for binding in target.selects
                            if binding is selector
                            or str(binding.path or binding.id_path or "") not in projected_paths
                        ]
            repaired += 1

    return repaired


def _refresh_api_option_display_labels(spec: FlowSpec) -> int:
    """Repair persisted live-option labels from their captured response rows."""
    snapshots: list[tuple[float, str, str, list[dict[str, Any]]]] = []
    for fact in (spec.request_facts or RequestFacts()).requests or []:
        rows = [
            dict(item) for item in (as_list_payload(fact.response_json) or [])
            if isinstance(item, dict)
        ]
        if rows:
            snapshots.append((
                _request_sequence_value(fact.sequence) or -1.0,
                str(fact.request_id or ""),
                _option_source_contract_endpoint(fact.url or fact.path),
                rows,
            ))
    for step in spec.steps or []:
        rows = [
            dict(item) for item in (as_list_payload(step.response_json) or [])
            if isinstance(item, dict)
        ]
        if rows:
            snapshots.append((
                _step_sequence(step) or -1.0,
                str((step.source_meta or {}).get("request_id") or ""),
                _option_source_contract_endpoint(step.url or step.path),
                rows,
            ))

    repaired = 0
    for step in spec.steps or []:
        for param in step.params or []:
            if (
                param.source_kind != "api_option"
                or param.locked
                or _param_has_manual_contract(param)
            ):
                continue
            source = dict(param.source or {})
            value_key = str(source.get("value_key") or "")
            current_label = str(source.get("label_key") or "")
            if not value_key or (
                current_label
                and current_label != value_key
                and not _is_idlike(current_label)
            ):
                continue
            source_request_id = str(source.get("source_request_id") or "")
            source_endpoint = _option_source_contract_endpoint(
                str(source.get("source_url") or "")
            )
            matches = [
                item for item in snapshots
                if (
                    source_request_id
                    and item[1] == source_request_id
                ) or (
                    source_endpoint
                    and item[2] == source_endpoint
                )
            ]
            if not matches:
                continue
            _sequence, _request_id, _endpoint, rows = max(matches, key=lambda item: item[0])
            selected_rows = [
                row for row in rows
                if _recorded_scalar_values_match(
                    _flow_path_lookup(row, value_key), param.value,
                )
            ]
            sample = selected_rows[0] if len(selected_rows) == 1 else rows[0]
            label_key = _pick_label_key(sample, value_key)
            if label_key == value_key or (
                _is_idlike(label_key)
                and not re.search(r"(?:code|no|number|serial)$", label_key, re.I)
            ):
                continue
            records: list[dict[str, Any]] = []
            label_values: dict[str, Any] = {}
            ambiguous = False
            for row in rows:
                value = _flow_path_lookup(row, value_key)
                label = _flow_path_lookup(row, label_key)
                if value is _FLOW_PATH_MISSING or label is _FLOW_PATH_MISSING:
                    continue
                label_text = str(label or "").strip()
                if not label_text:
                    continue
                if label_text in label_values and label_values[label_text] != value:
                    ambiguous = True
                    break
                label_values[label_text] = value
            if ambiguous or not label_values:
                continue
            records = [
                {"label": label, "value": value}
                for label, value in label_values.items()
            ]
            param.source = {**source, "label_key": label_key}
            param.enum_options = records
            param.enum_value_map = dict(label_values)
            binding = _find_select_binding(step, param)
            if binding is not None:
                binding.label_key = label_key
                binding.options = records
                binding.option_map = dict(label_values)
                binding.count = len(records)
            repaired += 1
    return repaired

def _attach_option_source_memberships(spec: FlowSpec) -> None:
    """Expose candidate-source ownership without executing it as a call node."""
    by_id = {step.step_id: step for step in spec.steps}
    by_path = {
        _request_path({"url": step.path or step.url}): step
        for step in spec.steps
    }
    facts = list((spec.request_facts or RequestFacts()).requests or [])
    facts_by_id = {str(fact.request_id or ""): fact for fact in facts if fact.request_id}
    node_ids_by_capability = {
        capability.capability_id: set(_capability_node_step_ids(capability))
        for capability in (spec.capabilities or [])
    }

    def captured_fact(source: dict[str, Any]) -> RequestFact | None:
        request_id = str(source.get("source_request_id") or "")
        if request_id and request_id in facts_by_id:
            return facts_by_id[request_id]
        source_path = _request_path({"url": str(source.get("source_url") or "")})
        if not source_path:
            return None
        # The latest duplicate capture represents the source selected most
        # recently by the operator.
        return next(
            (fact for fact in reversed(facts) if _request_path({"url": fact.path or fact.url}) == source_path),
            None,
        )

    for capability in spec.capabilities or []:
        node_ids = node_ids_by_capability.get(capability.capability_id, set())
        source_ids: set[str] = set()
        captured_source_facts: dict[str, RequestFact] = {}
        for step_id in node_ids:
            step = by_id.get(step_id)
            for param in (step.params if step else []):
                if param.source_kind != "api_option":
                    continue
                source = param.source or {}
                source_id = str(source.get("source_step_id") or "")
                if not source_id and source.get("source_url"):
                    source_step = by_path.get(_request_path({"url": str(source.get("source_url"))}))
                    source_id = source_step.step_id if source_step else ""
                if source_id in by_id and source_id not in node_ids:
                    source_ids.add(source_id)
                    continue
                fact = captured_fact(source)
                if fact is not None:
                    captured_source_facts[str(fact.request_id or fact.request_index or fact.path or fact.url)] = fact

        for source_id in source_ids:
            source_step = by_id[source_id]
            existing = next(
                (ref for ref in (capability.request_refs or []) if ref.step_id == source_id),
                None,
            )
            ref = _capability_request_ref_from_step(spec, source_step, existing)
            ref.usage = "option_source"
            ref.origin = "recorder"
            ref.confirmed = True
            capability.request_refs = [
                item for item in (capability.request_refs or []) if item.step_id != source_id
            ]
            capability.request_refs.append(ref)

        for fact in captured_source_facts.values():
            analysis = spec.request_facts.analysis.get(str(fact.request_id or ""))
            existing = next(
                (
                    ref for ref in (capability.request_refs or [])
                    if (fact.request_id and ref.request_id == fact.request_id)
                    or (
                        not ref.step_id
                        and _request_path({"url": ref.path}) == _request_path({"url": fact.path or fact.url})
                    )
                ),
                None,
            )
            ref = CapabilityRequestRef(
                request_id=str(fact.request_id or ""),
                request_index=fact.request_index,
                step_id="",
                role=(analysis.role if analysis else "") or "read_option",
                method=str(fact.method or "GET").upper(),
                path=fact.path or fact.url,
                sequence=fact.sequence,
                confidence=float((analysis.confidence if analysis else None) or 1.0),
                reason=(analysis.reason if analysis else "") or "字段候选来自录制捕获的只读接口",
                usage="option_source",
                origin="repair" if existing is None else existing.origin,
                confirmed=True,
            )
            capability.request_refs = [
                item for item in (capability.request_refs or [])
                if not (
                    (fact.request_id and item.request_id == fact.request_id)
                    or (
                        not item.step_id
                        and _request_path({"url": item.path}) == _request_path({"url": fact.path or fact.url})
                    )
                )
            ]
            capability.request_refs.append(ref)

def _dependency_sig(source_step_id: str, source_path: str, target_step_id: str, target_path: str) -> str:
    raw = "|".join([source_step_id or "", source_path or "", target_step_id or "", target_path or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _dependency_match_score(param: ParamField, source_path: str) -> int:
    def token(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    source_full = token(source_path)
    source_leaf = token(re.split(r"\.|\[\d+\]", str(source_path or ""))[-1])
    target_tokens = {
        token(param.key),
        token(param.label),
        token(re.split(r"\.|\[\d+\]", str(param.path or ""))[-1]),
    } - {""}
    score = 0
    for target in target_tokens:
        if source_leaf and target == source_leaf:
            score = max(score, 50)
        elif target == source_full:
            score = max(score, 45)
        elif len(target) >= 4 and (target in source_full or (source_leaf and source_leaf in target)):
            score = max(score, 30)
    if "[" not in source_path:
        score += 3
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

    只追加缺失候选；不会修改原始 RequestFacts，也不会恢复用户已删除的依赖。
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
            response_owned_candidate = bool(
                not _param_has_editable_control_evidence(param)
                and param.source_kind not in _OPTION_SOURCE_KINDS
                and param.source_kind not in {"user_input", "current_user", "system_time", "computed", "page_context"}
            )
            if _skip_auto_dependency_target(param) and not internal_id_target and not response_owned_candidate:
                continue
            if param.source_kind == "previous_response" and param.source.get("step_id"):
                continue
            value = str(param.value if param.value is not None else "").strip()
            if not value:
                continue
            short_value = len(value) < 4
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
            if "[" in str(source_path or ""):
                continue
            source_leaf = re.sub(r"[^a-z0-9]+", "", str(source_path or "").split(".")[-1].lower())
            semantic_score = _dependency_match_score(param, source_path)
            strong_internal_id = internal_id_target and source_leaf == "id" and len(matches) == 1
            strong_semantic_response = response_owned_candidate and semantic_score >= 40
            if short_value and not (strong_internal_id or strong_semantic_response):
                continue
            if not strong_internal_id and not strong_semantic_response and not _auto_dependency_link_allowed(param, source_path):
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
                evidence={"kind": "value_match", "value": value, "path_score": semantic_score, "auto_rebuilt": True, "actor": "heuristic"},
                meta={"actor": "heuristic", "verified": False},
            ))
            existing.add(sig)
            added += 1
    # Always prune/synchronize existing links. Previously this only ran when a
    # new dependency was added, so a bad persisted list[0] link survived every
    # later re-analysis and kept the target field hidden as previous_response.
    _sync_link_sources(spec.steps, spec.links)
    return added




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


def _add_request_step_from_fact(spec: FlowSpec, entry: dict[str, Any]) -> FlowStep:
    request_id = str(entry.get("request_id") or "")
    request_index = entry.get("request_index")
    existing = None
    entry_sig = _request_signature(entry)
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
        _mark_request_materialized(spec, entry, materialized_step_id=existing.step_id)
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
    for item in _request_fact_items(spec):
        if item.get("response_json") is not None:
            reads_for_candidate.append({"url": item.get("url") or item.get("path") or "", "json": item.get("response_json")})
    st = _build_step_from_capture(
        _attach_request_role(req, role),
        reads=reads_for_candidate,
        samples={},
        storage_state=None,
        required_labels=set(),
        page_enum_options=_page_enum_options_from_request_facts(spec.request_facts),
        field_evidence=list((spec.meta or {}).get("field_evidence") or []),
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
    _mark_request_materialized(spec, entry, materialized_step_id=st.step_id)
    return st


def promote_request_to_step(spec: FlowSpec, *, request_index: Any = None, request_id: str = "") -> FlowStep:
    """把 RequestFacts 事实提升为可执行 FlowStepTemplate。

    这是录制 V2 的唯一请求加入入口：手工加入、能力加入、自动修复和发布补齐都走这里。
    """
    entry = _find_request_fact_item(spec, request_index=request_index, request_id=request_id)
    if entry is None:
        raise ValueError(f"captured request not found: {request_index or request_id}")
    return _add_request_step_from_fact(spec, entry)


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


def _transition_capability_kind(spec: FlowSpec, cap: FlowCapability, value: Any) -> None:
    """Atomically migrate submit contracts when the operator changes the kind."""
    old_kind = str(cap.kind or "submit")
    new_kind = str(value or "submit")
    cap.kind = new_kind
    if old_kind == new_kind or {old_kind, new_kind} - {"submit", "submit_batch"}:
        return

    by_id = {step.step_id: step for step in spec.steps}
    cap_steps = [
        by_id[step_id]
        for step_id in _capability_node_step_ids(cap)
        if step_id in by_id
    ]
    if not cap_steps:
        return
    write_steps = [step for step in cap_steps if _is_write_step(step)]
    final_write = write_steps[-1] if write_steps else cap_steps[-1]

    if new_kind == "submit_batch":
        # Selecting "批量提交" is explicit operator intent. Build the complete
        # executable contract in the same edit instead of leaving kind/schema/
        # nodes in three mutually contradictory states.
        cap.evidence = [
            item for item in (cap.evidence or [])
            if not (isinstance(item, dict) and item.get("kind") == "user_capability_kind")
        ]
        cap.evidence.append({
            "kind": "user_capability_kind",
            "batch_intent": True,
            "repeated_submission": True,
            "from": old_kind,
            "to": new_kind,
        })
        cap.input_schema = _batch_capability_input_schema(cap_steps)
        cap.inputs = _capability_inputs_from_top_level_schema(cap.input_schema)
        cap.nodes = _default_capability_nodes(cap_steps, kind="submit_batch", force_batch=True)
        cap.output_schema = {
            "type": "object",
            "properties": {
                "total": {"type": "number"},
                "success_count": {"type": "number"},
                "failed_count": {"type": "number"},
                "results": {"type": "array", "items": {"type": "object"}},
                "failed_items": {"type": "array", "items": {"type": "object"}},
            },
        }
        cap.output_mapping = [
            {"kind": "batch_result", "name": name, "response_path": name}
            for name in ("total", "success_count", "failed_count", "results", "failed_items")
        ]
        if "批量" not in str(cap.title or ""):
            cap.title = "批量" + (str(cap.title or "提交业务申请"))
        return

    # Leaving batch mode must remove every entries-dependent node/schema/relation
    # in the same transaction; otherwise later field edits resurrect stale
    # has_entries/foreach validation errors.
    cap.evidence = [
        item for item in (cap.evidence or [])
        if not (
            isinstance(item, dict)
            and (
                item.get("kind") == "user_capability_kind"
                or item.get("batch_intent")
                or item.get("repeated_submission")
            )
        )
    ]
    params = [param for step in cap_steps for param in (step.params or [])]
    cap.input_schema = _capability_input_schema(params)
    cap.nodes = _default_capability_nodes(cap_steps, kind="submit")
    cap.output_mapping = [{
        "kind": "final_response",
        "name": "result",
        "step_id": final_write.step_id,
        "response_path": "response",
    }]
    cap.inputs = []
    if "批量提交" in str(cap.title or ""):
        cap.title = str(cap.title).replace("批量提交", "提交", 1)
    elif str(cap.title or "").startswith("批量"):
        cap.title = str(cap.title)[2:] or "提交业务申请"
    cap_refs = {str(cap.name or ""), str(cap.capability_id or "")}
    spec.capability_relations = [
        relation for relation in (spec.capability_relations or [])
        if not (
            str(relation.to_capability or "") in cap_refs
            and str(relation.to_input or "") in {"entries", "items"}
        )
    ]


def _find_select_binding(step: FlowStep, param: ParamField) -> SelectBinding | None:
    for sel in step.selects:
        if sel.path == param.path or (sel.id_path and sel.id_path == param.path):
            return sel
    return None


def _bind_option_source(
    spec: FlowSpec,
    *,
    target_step_id: str,
    target_path: str,
    source_step_id: str = "",
    source_url: str = "",
    source_request_id: str = "",
    value_key: str = "",
    label_key: str = "",
    category_key: str | None = None,
    category_value: Any = None,
    id_path: str = "",
    options: list[Any] | None = None,
    option_map: dict[str, Any] | None = None,
    multi: bool = False,
    actor: str = "system",
) -> None:
    step = _find_step(spec, target_step_id)
    param = _find_param(step, target_path)
    if param.source_kind in {"selected_option_field", "computed"}:
        return
    normalized_actor = str(actor or "system").strip().lower()
    automated = normalized_actor in _AUTOMATED_FIELD_EDIT_ACTORS
    if automated and (
        param.locked or _param_axis_manually_edited(param, "source_kind", "source")
    ):
        return
    source_step = _find_step(spec, source_step_id) if source_step_id else None
    src_url = source_url or (source_step.path or source_step.url if source_step else "")
    if not src_url:
        raise ValueError("bind_option_source missing source_url/source_step")

    category_owned = automated and _param_axis_manually_edited(
        param, "category", "exposed_to_user", "editable",
    )
    type_owned = automated and _param_field_manually_edited(param, "type")
    options_owned = automated and _param_axis_manually_edited(
        param, "enum_options", "enum_value_map",
    )
    keep_hydration = (
        param.source_kind == "previous_response"
        and bool((param.source or {}).get("allow_caller_override") or (param.source or {}).get("link_id"))
    )
    option_contract = {
        "kind": "api_option",
        "source_step_id": source_step_id,
        "source_request_id": source_request_id,
        "source_url": src_url,
        "value_key": value_key,
        "label_key": label_key,
        "category_key": category_key,
        "category_value": category_value,
        "id_path": id_path or param.path,
    }
    if not category_owned:
        param.category = "user_param"
    # ``type`` is the caller-facing business contract; ``wire_type`` retains
    # the recorded JSON scalar transported to the backend.
    if not param.wire_type:
        param.wire_type = param.type
    if not type_owned:
        param.type = "list-enum" if multi else "enum"
    if not category_owned:
        param.exposed_to_user = True
        param.editable = True
    param.need_human_confirm = False
    if keep_hydration:
        param.source_kind = "previous_response"
        param.source = {
            **dict(param.source or {}),
            "kind": "previous_response",
            "allow_caller_override": True,
            "option_source": option_contract,
        }
        param.reason = (
            "编辑场景默认来自上游详情，候选来自接口选项源；"
            "调用方可改，显式输入优先于上游默认值"
        )
    else:
        param.source_kind = "api_option"
        param.source = option_contract
        param.reason = "字段候选来自接口选项源，调用方传显示值，运行期按 label/value 映射提交真实值"
    if options and not options_owned:
        param.enum_options = list(options)
    if option_map and not options_owned:
        param.enum_value_map = dict(option_map)
    option_evidence = {
        "source": "option_source",
        "source_step_id": source_step_id,
        "source_request_id": source_request_id,
        "source_url": src_url,
        "value_key": value_key,
        "label_key": label_key,
        "category_key": category_key,
        "category_value": category_value,
    }
    if option_evidence not in param.evidence:
        param.evidence.append(option_evidence)

    sel = _find_select_binding(step, param)
    if sel is None:
        sel = SelectBinding(param=param.key, path=param.path)
        step.selects.append(sel)
    sel.param = param.key
    sel.path = param.path
    sel.source_url = src_url
    sel.source_request_id = source_request_id or sel.source_request_id
    sel.value_key = value_key or sel.value_key
    sel.label_key = label_key or sel.label_key
    sel.category_key = category_key
    sel.category_value = None if category_value is None else str(category_value)
    sel.id_path = id_path or sel.id_path or param.path
    sel.multi = bool(multi)
    if options:
        sel.options = list(options)
        sel.count = len(options)
    if option_map:
        sel.option_map = dict(option_map)
    sel.enum_source = "api"
    sel.enum_confirmed = True
    sel.actor = normalized_actor if normalized_actor in {"user", "agent", "planner", "repair"} else "heuristic"
    sel.confidence = max(float(sel.confidence or 0), 1.0 if normalized_actor == "user" else 0.8)
    _hydrate_select_source_contract(spec, sel)
    if normalized_actor == "user":
        manual_fields = [
            "type", "category", "source_kind", "source", "exposed_to_user",
            "editable", "need_human_confirm",
        ]
        if options:
            manual_fields.append("enum_options")
        if option_map:
            manual_fields.append("enum_value_map")
        _record_param_manual_contract(param, manual_fields)


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


def _capability_schema_field(field: CapabilityField) -> dict[str, Any]:
    schema = _schema_for_param_type(field.type or "string")
    schema["x-dano-capability-owned"] = True
    schema["x-dano-operator-owned"] = bool(field.locked)
    if field.wire_format:
        schema["x-dano-wire-format"] = field.wire_format
    if field.enum_options:
        schema["enum"] = list(field.enum_options)
    if field.enum_value_map:
        schema["x-enum-value-map"] = dict(field.enum_value_map)
    if field.display_name:
        schema["title"] = field.display_name
    return schema


def _same_capability_computed_field(a: CapabilityField, b: CapabilityField) -> bool:
    """Match the only capability fields that remain authoritative at this level."""
    if a.field_id and b.field_id and a.field_id == b.field_id:
        return True
    if a.step_id or b.step_id:
        return False
    a_name = str(a.key or a.path or "").strip()
    b_name = str(b.key or b.path or "").strip()
    return bool(a_name and b_name and a_name == b_name)


def _upsert_capability_field(
    cap: FlowCapability, data: dict[str, Any], *, default_scope: str,
) -> CapabilityField:
    raw = dict(data or {})
    raw.setdefault("scope", default_scope)
    raw.setdefault("locked", True)
    raw.setdefault("confirmed", True)
    field = CapabilityField.model_validate(raw)
    name = str(field.key or field.path or field.display_name or "").strip()
    if field.scope in {"input", "output"} and not field.step_id:
        if not name:
            raise ValueError(f"capability {field.scope} field requires a name")
        schema_name = "input_schema" if field.scope == "input" else "output_schema"
        schema = dict(getattr(cap, schema_name) or {})
        schema.setdefault("type", "object")
        properties = dict(schema.get("properties") or {})
        properties[name] = _capability_schema_field(field)
        schema["properties"] = properties
        required = [str(value) for value in (schema.get("required") or []) if str(value) in properties]
        if field.required and name not in required:
            required.append(name)
        elif not field.required:
            required = [value for value in required if value != name]
        schema["required"] = required
        setattr(cap, schema_name, schema)
        cap.updated_by = "repair"
        return field
    if field.scope != "computed" or field.step_id:
        raise ValueError(
            "request/internal fields require a canonical FlowStep ParamField; "
            "only capability-level computed fields may be persisted"
        )
    for index, existing in enumerate(cap.computed_fields or []):
        if not _same_capability_computed_field(existing, field):
            continue
        merged = existing.model_dump()
        merged.update(field.model_dump(exclude_unset=True))
        cap.computed_fields[index] = CapabilityField.model_validate(merged)
        cap.updated_by = "repair"
        return cap.computed_fields[index]
    cap.computed_fields.append(field)
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
    source_changed = bool(binding.source_request_id and binding.source_request_id != (fact.request_id or ""))
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
    binding.enum_source = "api"

    # Initial capture already applies field-specific filtering (for example a
    # shared dictionary endpoint narrowed to one dictType). Preserve that
    # grounded subset. A changed source or an empty snapshot is rehydrated.
    if not source_changed and (binding.options or binding.option_map):
        return

    # Refresh the captured candidate snapshot whenever the interface is
    # selected/reselected. Runtime execution may legitimately return no rows;
    # in that case an empty snapshot is authoritative rather than an error.
    items = as_list_payload(fact.response_json) or []
    if binding.category_key and binding.category_value is not None:
        items = [
            item for item in items
            if isinstance(item, dict)
            and str(item.get(binding.category_key)) == str(binding.category_value)
        ]
    if not items:
        binding.options = []
        binding.option_map = None
        binding.count = 0
        binding.enum_confirmed = True
        return

    first = items[0]
    if not isinstance(first, dict):
        records = [{"label": str(item), "value": item} for item in items[:200]]
        binding.options = records
        binding.option_map = {record["label"]: record["value"] for record in records}
        binding.count = len(items)
        binding.enum_confirmed = True
        return

    keys = list(first.keys())
    value_candidates = [binding.value_key, "value", "id", "code", "dictValue", "key"]
    label_candidates = [binding.label_key, "label", "name", "text", "title", "dictLabel"]
    value_key = next((key for key in value_candidates if key and key in first), "")
    label_key = next((key for key in label_candidates if key and key in first and key != value_key), "")
    if not value_key:
        value_key = next((key for key in keys if not isinstance(first.get(key), (dict, list))), "")
    if not label_key:
        label_key = next((
            key for key in keys
            if key != value_key and isinstance(first.get(key), str) and str(first.get(key) or "").strip()
        ), value_key)
    if not value_key:
        if source_changed:
            binding.options = []
            binding.option_map = None
            binding.count = 0
        return

    binding.value_key = value_key
    binding.label_key = label_key or value_key
    records: list[dict[str, Any]] = []
    option_map: dict[str, Any] = {}
    seen: set[tuple[str, str]] = set()
    for item in items[:200]:
        if not isinstance(item, dict) or value_key not in item:
            continue
        raw_value = item.get(value_key)
        raw_label = item.get(binding.label_key, raw_value)
        label = str(raw_label if raw_label not in (None, "") else raw_value)
        signature = (label, repr(raw_value))
        if not label or signature in seen:
            continue
        seen.add(signature)
        records.append({"label": label, "value": raw_value})
        option_map[label] = raw_value
    binding.options = records
    binding.option_map = option_map or None
    binding.count = len(items)
    binding.enum_confirmed = True


def _rename_param_public_key(
    spec: FlowSpec,
    step: FlowStep,
    param: ParamField,
    new_key: str,
    *,
    actor: str,
) -> None:
    """Atomically rename a caller-facing field without touching its wire path.

    ``ParamField.path`` is the executable request contract. ``key``/``label``
    are the public business name.  Keeping the mutation here prevents model
    naming, manual naming and capability-schema regeneration from drifting into
    three different representations of the same field.
    """
    proposed = str(new_key or "").strip()
    if not proposed:
        raise ValueError("field key cannot be empty")
    if proposed == param.key:
        return
    if any(other is not param and other.key == proposed for other in step.params):
        raise ValueError(f"duplicate param key: {proposed}")

    old_key = param.key
    param.key = proposed
    param.label = proposed
    if actor == "user":
        param.name_source = "manual"
        evidence_source = "manual_edit"
    else:
        # A model proposal is useful semantic evidence, not an operator lock.
        # It must remain editable and must never self-confirm its own decision.
        param.name_source = "planner" if actor == "planner" else actor
        evidence_source = f"{actor}_proposal"
    param.evidence.append({
        "source": evidence_source,
        "field": "key",
        **({"axis": "name", "status": "locked", "kind": "manual_override"} if actor == "user" else {}),
        "previous": old_key,
        "value": proposed,
    })

    if old_key in step.sample_inputs:
        step.sample_inputs[proposed] = step.sample_inputs.pop(old_key)
    elif param.value not in (None, ""):
        step.sample_inputs.setdefault(proposed, param.value)
    for binding in step.selects:
        if binding.path == param.path or binding.param == old_key:
            binding.param = proposed

    for capability in spec.capabilities or []:
        for collection_name in (
            "fields", "inputs", "request_fields", "internal_fields",
            "computed_fields", "outputs",
        ):
            for field in getattr(capability, collection_name, []) or []:
                same_wire_field = bool(
                    field.step_id == step.step_id
                    and _strip_body_prefix(field.path or "") == _strip_body_prefix(param.path)
                )
                if same_wire_field or (field.step_id == step.step_id and field.key == old_key):
                    field.key = proposed
                    if field.display_name in {"", old_key}:
                        field.display_name = proposed
        for relation in spec.capability_relations or []:
            if relation.to_capability in {capability.name, capability.capability_id} and relation.to_input == old_key:
                relation.to_input = proposed

        def rename_node_refs(nodes: list[dict[str, Any]]) -> None:
            old_ref = f"input.{old_key}"
            new_ref = f"input.{proposed}"
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                for field_name in ("source", "items", "condition", "check"):
                    value = node.get(field_name)
                    if isinstance(value, str):
                        node[field_name] = value.replace(old_ref, new_ref)
                for child_name in ("children", "steps", "then", "else", "otherwise"):
                    if isinstance(node.get(child_name), list):
                        rename_node_refs(node[child_name])

        rename_node_refs(capability.nodes or [])


def apply_flow_edits(spec: FlowSpec, edits: list[dict[str, Any]]) -> FlowSpec:
    """应用编辑列表，返回新 FlowSpec（深拷贝）。"""
    if not edits:
        return refresh_review_items(spec.model_copy(deep=True))

    new_spec = spec.model_copy(deep=True)
    bulk_review_resolutions: list[tuple[set, set, bool]] = []
    needs_dependency_rebuild = False

    for edit in edits:
        op = edit.get("op")

        from dano.execution.page.recording_live import LIVE_RECORDING_AGENT_OPS, apply_recording_agent_edit
        if op in LIVE_RECORDING_AGENT_OPS:
            apply_recording_agent_edit(new_spec, edit)
            continue

        if op == "resolve_reviews":
            resolved = bool(edit.get("resolved", True))
            severities = set(edit.get("severities") or [])
            exclude_severities = set(edit.get("exclude_severities") or [])
            bulk_review_resolutions.append((severities, exclude_severities, resolved))
            generated = _generated_review_items(new_spec)
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
                generated = _generated_review_items(new_spec)
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
            actor = str(edit.get("actor") or "user")
            if field == "title" and actor != "planner":
                new_spec.meta = {**(new_spec.meta or {}), "title_source": "user"}
            if field == "business_description" and actor != "planner":
                new_spec.meta = {
                    **(new_spec.meta or {}),
                    "business_description_source": "user",
                }
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
            raise ValueError(
                "generate_capabilities is retired; submit a strict semantic plan"
            )

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
            for step_id in _capability_node_step_ids(cap):
                _remember_removed_capability_step(new_spec, cap.name, step_id)
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
            actor = str(edit.get("actor") or "user").strip().lower()
            field = str(edit.get("field") or "")
            if field not in _CAPABILITY_ALLOWED_FIELDS:
                raise ValueError(f"unknown capability field: {field}")
            if field in {
                "step_ids", "request_refs", "fields", "inputs",
                "request_fields", "internal_fields", "outputs",
            }:
                raise ValueError(f"derived capability field is read-only: {field}")
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
            if field == "computed_fields":
                value = [CapabilityField.model_validate(x) for x in (value or [])]
                if any(item.step_id or item.scope != "computed" for item in value):
                    raise ValueError(
                        "computed_fields only accepts capability-level computed values"
                    )
            if field == "dependencies":
                value = [CapabilityDependency.model_validate(x) for x in (value or [])]
            if field == "confirmed" and value:
                # Confirmation records the operator's decision.  Capability
                # shape, field type/source and enum quality may be generated by
                # a model, so they must not veto that decision.  Executability
                # is checked later by the deterministic request compiler.
                value = True
            if field == "kind":
                _transition_capability_kind(new_spec, cap, value)
            else:
                setattr(cap, field, value)
            if field == "confirmed" and value:
                cap.requires_human_confirm = False
                cap.status = "confirmed"
                cap.confirmation_hash = _capability_confirmation_hash(new_spec, cap)
            elif field == "confirmed":
                cap.status = "draft"
                cap.confirmation_hash = ""
            elif field != "updated_by":
                cap.updated_by = actor
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
            actor = str(edit.get("actor") or "user")
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
                created = FlowCapability.model_validate(raw)
                created.updated_by = actor
                if actor == "planner":
                    created.confirmed = False
                    created.locked = False
                new_spec.capabilities.append(created)
            else:
                cap = new_spec.capabilities[idx]
                planner_protected = bool(
                    actor == "planner"
                    and (
                        cap.locked
                        or cap.updated_by == "user"
                        # Automatically accepted (>60%) planner drafts remain
                        # optimizable.  A legacy/manual confirmation without a
                        # planner provenance stays protected conservatively.
                        or (cap.confirmed and cap.updated_by != "planner")
                    )
                )
                for key, value in raw.items():
                    if key not in _CAPABILITY_ALLOWED_FIELDS:
                        continue
                    if planner_protected and key not in {"confidence"}:
                        continue
                    if key in {"fields", "inputs", "request_fields", "internal_fields", "computed_fields", "outputs"}:
                        value = [CapabilityField.model_validate(x) for x in (value or [])]
                    elif key == "dependencies":
                        value = [CapabilityDependency.model_validate(x) for x in (value or [])]
                    elif key == "request_refs":
                        value = [CapabilityRequestRef.model_validate(x) for x in (value or [])]
                    setattr(cap, key, value)
                if not planner_protected:
                    cap.updated_by = actor
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
            actor = str(edit.get("actor") or "user")
            if actor == "planner":
                # Planner output is a proposal. It cannot self-confirm/self-lock
                # a synthetic aggregate field and then use that field as proof
                # that the recorded request was batch-shaped.
                raw["locked"] = False
                raw["confirmed"] = False
                raw["evidence"] = [
                    *list(raw.get("evidence") or []),
                    {"source": "planner_proposal"},
                ]
            if "field" in edit and not isinstance(edit.get("field"), dict):
                raw["key"] = str(edit.get("field") or "")
            for alias in ("field_id", "key", "path", "step_id", "request_id", "request_index", "type", "source_kind"):
                if alias in edit and alias not in raw:
                    raw[alias] = edit.get(alias)
            if not _apply_capability_field_to_param(
                new_spec, raw, scope=default_scope, actor=actor,
            ):
                # Only capability-owned aggregate inputs/outputs are persisted on
                # FlowCapability. Step-bound fields are redirected to ParamField.
                _upsert_capability_field(new_spec.capabilities[idx], raw, default_scope=default_scope)
            new_spec.capabilities[idx].updated_by = actor
            _invalidate_capability_contract(new_spec.capabilities[idx])
            continue

        if op in {"add_request_to_capability", "add_capability_step"}:
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            actor = str(edit.get("actor") or edit.get("origin") or "user")
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
            origin = str(edit.get("origin") or actor or "manual")
            extra_fields = {
                k: v for k, v in edit.items()
                if k not in {"op", "capability_name", "capability_id", "step_id", "actor", "usage", "origin", "request_id", "request_index", "request", "source", "target"}
            }
            _forget_removed_capability_step(new_spec, cap.name, step_id)
            _set_capability_request_membership(
                new_spec, cap, step, usage=usage, origin=origin, extra_fields=extra_fields,
            )
            cap.updated_by = "planner" if actor == "planner" else "user"
            _invalidate_capability_contract(cap)
            if usage in {"execute", "preflight"}:
                _add_step_id_to_capability(new_spec, cap, step_id)
            _sync_capability_order(new_spec, cap)
            continue

        if op in {"remove_request_from_capability", "remove_capability_step"}:
            idx = _find_capability_index(new_spec, edit)
            step_id = str(edit.get("step_id") or "")
            actor = str(edit.get("actor") or edit.get("origin") or "user")
            if actor != "planner":
                _remember_removed_capability_step(
                    new_spec, new_spec.capabilities[idx].name, step_id
                )

            new_spec.capabilities[idx].request_refs = [
                ref
                for ref in new_spec.capabilities[idx].request_refs
                if ref.step_id != step_id
            ]
            new_spec.capabilities[idx].nodes = _remove_capability_step_nodes(
                new_spec.capabilities[idx].nodes or [], step_id,
            )
            new_spec.capabilities[idx].updated_by = (
                "planner" if actor == "planner" else "user"
            )
            _invalidate_capability_contract(new_spec.capabilities[idx])
            _sync_capability_order(new_spec, new_spec.capabilities[idx])
            _sync_capability_output_after_step_removal(new_spec.capabilities[idx])
            continue

        if op == "reorder_capability_steps":
            idx = _find_capability_index(new_spec, edit)
            cap = new_spec.capabilities[idx]
            requested = [str(value) for value in (edit.get("step_ids") or []) if str(value)]
            current = _capability_call_step_ids_from_nodes(cap.nodes or [])
            if len(requested) != len(current) or set(requested) != set(current):
                raise ValueError(
                    "reorder_capability_steps must contain every executable step exactly once"
                )
            cap.nodes = _reorder_capability_call_nodes(
                cap.nodes or [],
                {step_id: index for index, step_id in enumerate(requested)},
            )
            cap.updated_by = str(edit.get("actor") or "user")
            _invalidate_capability_contract(cap)
            _sync_capability_order(new_spec, cap)
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
            for alias in ("type", "from_capability", "from_output", "to_capability", "to_input", "confidence", "confirmed", "reason", "evidence"):
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
                actor=str(edit.get("actor") or "user"),
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
                identity_fields = {
                    "source_step_id", "source_path", "target_step_id", "target_path",
                }
                old_identity_value = str(getattr(link, field, "")) if field in identity_fields else ""
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
                if field in identity_fields and str(getattr(link, field, "")) != old_identity_value:
                    from dano.execution.page.recording_live import invalidate_dependency_verification

                    invalidate_dependency_verification(link, f"依赖字段 {field} 已变化，需要重新验证")
                duplicate = _matching_link(new_spec, link)
                if duplicate is not None:
                    _merge_link(duplicate, link)
                    if link in new_spec.links:
                        new_spec.links.remove(link)
                    effective_link = duplicate
                else:
                    effective_link = link
                if (
                    str(edit.get("actor") or "user").strip().lower() == "user"
                    and field == "confirmed"
                    and effective_link.confirmed
                ):
                    _apply_user_link_source(new_spec.steps, effective_link)
                continue

            if op == "remove":
                link = _find_link(new_spec, link_id)
                if edit.get("reset_target"):
                    target_step = _find_step(new_spec, link.target_step_id)
                    target_param = _find_param(target_step, link.target_path)
                    actor = str(edit.get("actor") or "user").strip().lower()
                    if actor in _AUTOMATED_FIELD_EDIT_ACTORS and (
                        target_param.locked or _param_has_manual_contract(target_param)
                    ):
                        continue
                    _reset_param_source(
                        target_param,
                        reason="依赖已由用户移除，字段已恢复为用户输入",
                        actor=actor,
                    )
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
                effective_link = existing
            else:
                _ensure_unique_link(new_spec, new_link)
                new_spec.links.append(new_link)
                effective_link = new_link
            actor = str(edit.get("actor") or "user").strip().lower()
            if actor == "user":
                _apply_user_link_source(new_spec.steps, effective_link)
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
            actor = str(edit.get("actor") or "user").strip().lower()

            if not field:
                raise ValueError("update edit missing field")

            if param_path:
                # 参数级编辑
                if actor in _AUTOMATED_FIELD_EDIT_ACTORS:
                    param = next((item for item in step.params if item.path == param_path), None)
                    if param is None:
                        continue
                    if field == "locked":
                        continue
                    protected_axes = {
                        "key": ("key", "label", "name", "display_name"),
                        "label": ("key", "label", "name", "display_name"),
                        "value": ("value", "default_value"),
                        "source_kind": (
                            "source_kind", "source", "category",
                            "exposed_to_user", "exposed_to_caller",
                        ),
                        "source": (
                            "source_kind", "source", "category",
                            "exposed_to_user", "exposed_to_caller",
                        ),
                        "category": (
                            "category", "exposed_to_user", "exposed_to_caller",
                            "source_kind", "source",
                        ),
                        "exposed_to_user": (
                            "category", "exposed_to_user", "exposed_to_caller",
                            "source_kind", "source",
                        ),
                    }.get(str(field), (str(field),))
                    if _param_has_full_lock(param) or _param_axis_manually_edited(param, *protected_axes):
                        continue
                else:
                    param = _find_param(
                        step,
                        param_path,
                        field_id=str(edit.get("field_id") or ""),
                        param_key=str(edit.get("param_key") or ""),
                        param_label=str(edit.get("param_label") or ""),
                    )
                comparable_value = value
                if field in {"required", "exposed_to_user", "editable", "need_human_confirm", "locked"}:
                    comparable_value = bool(value)
                elif field in {"key", "label", "description", "path", "type", "category", "source_kind"}:
                    comparable_value = str(value or "").strip() if field == "path" else str(value)
                elif field == "value":
                    comparable_value = str(value)
                if hasattr(param, str(field)) and getattr(param, str(field)) == comparable_value:
                    continue
                if field == "key":
                    _rename_param_public_key(new_spec, step, param, str(value), actor=actor)
                elif field == "path":
                    old_path = param.path
                    new_path = str(value or "").strip()
                    if not new_path:
                        raise ValueError("param path cannot be empty")
                    if any(p is not param and p.path == new_path for p in step.params):
                        raise ValueError(f"duplicate param path: {new_path}")
                    linked_targets = [
                        lk for lk in new_spec.links
                        if lk.target_step_id == step.step_id
                        and _reference_targets_param(step, lk.target_path, param)
                    ]
                    source_targets_param = bool(
                        isinstance(param.source, dict)
                        and _reference_targets_param(
                            step, str(param.source.get("target_path") or ""), param,
                        )
                    )
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
                    for lk in linked_targets:
                        lk.target_path = new_path
                    if isinstance(param.source, dict) and source_targets_param:
                        param.source["target_path"] = new_path
                elif field == "value":
                    param.value = str(value)
                    param.default_value = param.value
                    step.sample_inputs[param.key] = param.value
                elif field == "type":
                    _transition_param_type(param, value)
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
                else:
                    # H19 修复:不再 hasattr 兜底(避免改 path/source_kind/internal 等关键字段)
                    raise ValueError(f"unknown param field: {field}")
                if actor == "user" and field in {
                    "key", "label", "description", "value", "type", "category", "source_kind", "source",
                    "required", "exposed_to_user", "editable", "need_human_confirm", "enum_options", "enum_value_map",
                }:
                    if field != "key":
                        _record_param_manual_contract(param, (str(field),))
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
                            if (
                                actor == "user"
                                and binding.enum_confirmed is None
                                and (
                                    (
                                        binding.source_url
                                        and binding.value_key
                                        and binding.label_key
                                        and (binding.options or binding.option_map)
                                    )
                                    or (
                                        not binding.source_url
                                        and binding.options
                                        and len(_enum_option_map_from_options(binding.options))
                                        == len(binding.options)
                                    )
                                )
                            ):
                                # A complete binding explicitly saved by the
                                # operator is a confirmation, not a model guess.
                                binding.enum_confirmed = True
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
                field_id=str(edit.get("field_id") or ""),
                param_key=str(edit.get("param_key") or ""),
                param_label=str(edit.get("param_label") or ""),
            )
            target = str(edit.get("to") or "user_input")
            actor = str(edit.get("actor") or "user").strip().lower()
            if actor in _AUTOMATED_FIELD_EDIT_ACTORS and (
                param.locked or _param_has_manual_contract(param)
            ):
                continue
            new_spec.links = [
                lk for lk in new_spec.links
                if not (lk.target_step_id == step.step_id and _reference_targets_param(step, lk.target_path, param))
            ]
            if target == "constant":
                param.category = "system_const"
                param.source_kind = "constant"
                param.source = {"kind": "constant", "path": param.path, "manual": True}
                param.editable = True
                param.exposed_to_user = False
                param.need_human_confirm = False
                param.reason = "已重置为系统固定值，发布后按当前录制值提交"
                if actor == "user":
                    _record_param_manual_contract(param, (
                        "category", "source_kind", "source", "editable",
                        "exposed_to_user", "need_human_confirm",
                    ))
            else:
                _reset_param_source(param, actor=actor)
                step.sample_inputs[param.key] = param.value
            continue

        elif op == "add":
            raw_param_data = edit.get("param")
            if not isinstance(raw_param_data, dict) or not raw_param_data:
                raise ValueError("add edit missing param")
            param_data = dict(raw_param_data)
            explicit_fields = set(param_data)
            if "type" not in param_data and "value" in param_data:
                param_data["type"] = _infer_type_from_value(param_data["value"])
            try:
                new_param = ParamField(**param_data)
            except ValidationError as e:
                raise ValueError(f"invalid param data: {e}")
            actor = str(edit.get("actor") or "user").strip().lower()
            if actor == "user":
                # A field added in the workbench is already an explicit
                # operator decision. Record each supplied contract axis before
                # the final sync so enum/pagination heuristics cannot rewrite it.
                manual_fields = [field for field in (
                    "type", "category", "source_kind", "source",
                    "exposed_to_user", "editable", "required",
                    "need_human_confirm", "enum_options", "enum_value_map",
                ) if field in explicit_fields]
                _record_param_manual_contract(new_param, manual_fields)
                new_param.locked = True
            elif actor in _AUTOMATED_FIELD_EDIT_ACTORS:
                # Planner/repair payloads are proposals and cannot grant
                # themselves operator ownership through locked/manual markers.
                new_param.locked = False
                new_param.evidence = [
                    item for item in (new_param.evidence or [])
                    if not isinstance(item, dict) or item.get("source") != "manual_edit"
                ]
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
                field_id=str(edit.get("field_id") or ""),
                param_key=str(edit.get("param_key") or ""),
                param_label=str(edit.get("param_label") or ""),
            )
            # 字段删除是一个完整的契约删除：不能只移除 params，却留下指向该字段的
            # 依赖、枚举绑定或身份绑定。否则前端看似删除成功，下一轮同步/校验又会
            # 从这些残留引用中恢复旧字段，表现为“修改后无法删除”。
            _remove_param_incoming_links(new_spec, step, param)
            key_is_unique = sum(item.key == param.key for item in step.params) == 1
            label_is_unique = bool(param.label) and sum(item.label == param.label for item in step.params) == 1
            step.selects = [
                binding for binding in (step.selects or [])
                if not (
                    _reference_targets_param(step, binding.path or binding.id_path or "", param)
                    or (
                        not binding.path and not binding.id_path
                        and (
                            (key_is_unique and binding.param == param.key)
                            or (label_is_unique and binding.param == param.label)
                        )
                    )
                )
            ]
            step.identity = [
                binding for binding in (step.identity or [])
                if not _reference_targets_param(step, binding.path or "", param)
            ]
            step.params.remove(param)
            if param.key in step.sample_inputs:
                del step.sample_inputs[param.key]
            _invalidate_capabilities_for_steps(new_spec, {step.step_id})
            continue

        else:
            raise ValueError(f"unknown edit op: {op}")

    _sync_link_sources(new_spec.steps, new_spec.links)
    if needs_dependency_rebuild:
        rebuild_flow_dependencies(new_spec)
    if bulk_review_resolutions:
        generated = _generated_review_items(new_spec)
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




_CLIENT_SERVER_OWNED_STEP_FIELDS = frozenset({
    "headers", "body_source", "backup_body_source", "response_json", "response_projection",
    "identity", "params", "sample_inputs", "source_meta", "url", "path", "method", "content_type",
})
_CLIENT_SELECT_EDIT_FIELDS = frozenset({
    "param", "path", "source_url", "value_key", "label_key", "category_key", "category_value",
    "multi", "element_template", "label_subkey", "count", "options", "option_map", "enum_source",
    "enum_confirmed", "id_path", "id_tokens", "field_projections",
})


def _client_select_patch(spec: FlowSpec, edit: dict[str, Any]) -> dict[str, Any]:
    """Convert one select-binding patch into a safe step edit.

    Transport facts (headers/body/content type/request identity) never come from
    the browser. Existing values remain server-owned; a changed source is
    rehydrated from RequestFacts.
    """
    step_id = str(edit.get("step_id") or "")
    if not step_id:
        raise ValueError("upsert_select missing step_id")
    raw = edit.get("binding")
    if not isinstance(raw, dict):
        raise ValueError("upsert_select missing binding object")
    step = _find_step(spec, step_id)
    path = str(raw.get("path") or "")
    param = str(raw.get("param") or "")
    if not path and not param:
        raise ValueError("upsert_select requires path or param")
    target_param = _resolve_param_reference(step, path) if path else None
    param_name_is_unique = bool(param) and sum(item.key == param for item in step.params) == 1
    index = next((
        idx for idx, current in enumerate(step.selects)
        if (
            path
            and target_param is not None
            and _reference_targets_param(step, current.path or current.id_path or "", target_param)
        )
        or (
            param_name_is_unique
            and not current.path
            and not current.id_path
            and current.param == param
        )
    ), -1)
    existing = step.selects[index] if index >= 0 else None
    source_changed = bool(
        "source_url" in raw
        and str(raw.get("source_url") or "") != str(existing.source_url if existing else "")
    )
    merged = existing.model_dump() if existing is not None and not source_changed else {}
    for field in _CLIENT_SELECT_EDIT_FIELDS:
        if field in raw:
            merged[field] = raw[field]
    binding = SelectBinding.model_validate(merged)
    if existing is None or source_changed:
        _hydrate_select_source_contract(spec, binding)
    next_selects = list(step.selects)
    if index >= 0:
        next_selects[index] = binding
    else:
        next_selects.append(binding)
    return {
        "op": "update",
        "step_id": step_id,
        "field": "selects",
        "value": [item.model_dump() for item in next_selects],
    }


_CLIENT_SOURCE_KINDS = frozenset({
    "caller_input", "constant", "session", "context", "response_binding", "computed",
    "generated",
})

_INTERNAL_SOURCE_CONTRACT = {
    "user_input": ("user_param", True),
    "api_option": ("user_param", True),
    "page_enum": ("user_param", True),
    "static_enum": ("user_param", True),
    "manual_enum": ("user_param", True),
    "form_option": ("user_param", True),
    "constant": ("system_const", False),
    "page_default": ("user_param", True),
    "page_rule": ("runtime_var", False),
    "request_header": ("runtime_var", False),
    "current_user": ("runtime_var", False),
    "storage": ("runtime_var", False),
    "cookie": ("runtime_var", False),
    "session": ("runtime_var", False),
    "page_context": ("runtime_var", False),
    "context": ("runtime_var", False),
    "previous_response": ("runtime_var", False),
    "dynamic_structure": ("runtime_var", False),
    "selected_option_field": ("runtime_var", False),
    "computed": ("runtime_var", False),
    "generated": ("runtime_var", False),
    "system_time": ("runtime_var", False),
    "system_generated": ("runtime_var", False),
    "unknown": ("runtime_var", False),
}


def _client_source_patch(spec: FlowSpec, edit: dict[str, Any]) -> list[dict[str, Any]]:
    step = _find_step(spec, str(edit.get("step_id") or ""))
    param = _find_param(
        step,
        str(edit.get("param_path") or ""),
        field_id=str(edit.get("field_id") or ""),
        param_key=str(edit.get("param_key") or ""),
        param_label=str(edit.get("param_label") or ""),
    )
    public_kind = str(edit.get("value") or "")
    if public_kind not in _CLIENT_SOURCE_KINDS and public_kind not in _INTERNAL_SOURCE_CONTRACT:
        raise ValueError(f"unsupported parameter source: {public_kind}")
    current_kind = str(param.source_kind or "")
    current_public = _PUBLIC_SOURCE_BY_INTERNAL.get(current_kind, "")
    source = dict(param.source or {})
    if public_kind in _INTERNAL_SOURCE_CONTRACT and public_kind not in _CLIENT_SOURCE_KINDS:
        internal_kind = public_kind
        category, exposed = _INTERNAL_SOURCE_CONTRACT[public_kind]
        source = {**source, "kind": internal_kind, "path": param.path}
        base = {
            "op": "update",
            "actor": "user",
            "step_id": step.step_id,
            "param_path": param.path,
        }
        return [
            {**base, "field": "source_kind", "value": internal_kind},
            {**base, "field": "source", "value": source},
            {**base, "field": "category", "value": category},
            {**base, "field": "exposed_to_user", "value": exposed},
        ]

    if public_kind == "caller_input":
        internal_kind = current_kind if current_public == public_kind else "user_input"
        source = {**source, "kind": internal_kind, "path": param.path}
        category, exposed = "user_param", True
    elif public_kind == "constant":
        internal_kind = "constant"
        source = {"kind": "constant", "path": param.path}
        category, exposed = "system_const", False
    elif public_kind == "session":
        if current_public == public_kind:
            internal_kind = current_kind
        elif param.path.startswith("headers."):
            internal_kind = "request_header"
            source = {"kind": "request_header", "header": param.path.split(".", 1)[1]}
        else:
            internal_kind = "current_user"
            source = {"kind": "identity", "path": param.path}
        category, exposed = "runtime_var", False
    elif public_kind == "context":
        internal_kind = "page_context"
        context_key = str(source.get("context_key") or param.key or "").strip()
        if not context_key:
            raise ValueError(f"context source requires an explicit key: {param.path}")
        source = {**source, "kind": "page_context", "context_key": context_key, "path": param.path}
        category, exposed = "runtime_var", False
    elif public_kind == "response_binding":
        if current_public != public_kind:
            raise ValueError(
                f"response_binding must be configured from a recorded dependency: {param.path}"
            )
        internal_kind = current_kind
        category, exposed = "runtime_var", False
    elif public_kind == "computed":
        if current_public != public_kind:
            raise ValueError(f"computed source requires an existing executable formula: {param.path}")
        internal_kind = current_kind
        category, exposed = "runtime_var", False
    else:
        if current_public != public_kind:
            raise ValueError(f"generated source requires an existing executable strategy: {param.path}")
        internal_kind = current_kind
        category, exposed = "runtime_var", False

    base = {
        "op": "update",
        "actor": "user",
        "step_id": step.step_id,
        "param_path": param.path,
    }
    return [
        {**base, "field": "source_kind", "value": internal_kind},
        {**base, "field": "source", "value": source},
        {**base, "field": "category", "value": category},
        {**base, "field": "exposed_to_user", "value": exposed},
    ]


def apply_client_flow_patch(
    spec: FlowSpec,
    edits: list[dict[str, Any]],
    *,
    expected_fingerprint: str,
) -> FlowSpec:
    """Apply a browser patch without accepting a client-owned FlowSpec.

    The fingerprint gates concurrent edits. RequestFacts and sensitive step
    transport evidence remain authoritative on the server.
    """
    expected = str(expected_fingerprint or "")
    current = flow_spec_fingerprint(spec)
    if not expected:
        raise ValueError("expected_fingerprint is required")
    if expected != current:
        raise FlowSpecConflictError(expected, current)
    if not isinstance(edits, list) or not edits:
        raise ValueError("flow patch requires a non-empty edits list")
    if len(edits) > 200:
        raise ValueError("flow patch contains too many edits")

    safe_edits: list[dict[str, Any]] = []
    for raw_edit in edits:
        if not isinstance(raw_edit, dict):
            raise ValueError("flow patch edits must be objects")
        edit = dict(raw_edit)
        op = str(edit.get("op") or "")
        if op == "update_flow" and str(edit.get("field") or "") == "meta":
            raise ValueError("server-owned flow field: meta")
        if (
            op == "update"
            and edit.get("param_path")
            and str(edit.get("field") or "") == "category"
        ):
            raise ValueError("derived parameter field: category")
        if op in {"add_capability", "upsert_capability"}:
            raw_capability = edit.get("capability")
            if isinstance(raw_capability, dict) and (
                "step_ids" in raw_capability or "request_refs" in raw_capability
            ):
                raise ValueError("client capability membership must be expressed through nodes")
            derived_fields = {
                "fields", "inputs", "request_fields", "internal_fields", "outputs",
            }
            if isinstance(raw_capability, dict) and derived_fields.intersection(raw_capability):
                raise ValueError("client capability field projections are read-only")
        if op == "update" and not edit.get("param_path"):
            field = str(edit.get("field") or "")
            if field in _CLIENT_SERVER_OWNED_STEP_FIELDS or field == "selects":
                raise ValueError(f"server-owned step field: {field}")
        if op == "upsert_select":
            safe_edits.append(_client_select_patch(spec, edit))
        elif (
            op == "update"
            and edit.get("param_path")
            and str(edit.get("field") or "") == "source_kind"
        ):
            safe_edits.extend(_client_source_patch(spec, edit))
        else:
            safe_edits.append(edit)
    return apply_flow_edits(spec, safe_edits)

def _flow_autofix_context(spec: FlowSpec, report: dict[str, Any]) -> dict[str, Any]:
    request_facts = _request_fact_items(spec)
    cap_validation = report.get("capability_validation") or {}
    recorded_field_evidence = _client_redact_sensitive(
        copy.deepcopy((getattr(spec.request_facts, "field_evidence", []) or [])[-500:]),
    )
    recorded_option_sources = _client_redact_sensitive(
        copy.deepcopy((spec.request_facts.option_sources or [])[:120]),
    )
    recorded_page_events = _client_redact_sensitive(
        copy.deepcopy((spec.request_facts.page_events or [])[-300:]),
    )
    admitted_request_ids: set[str] = set()
    admitted_paths: set[str] = set()
    for source in spec.request_facts.option_sources or []:
        if not isinstance(source, dict):
            continue
        if source.get("kind") == "api_response":
            admitted_request_ids.add(str(source.get("request_id") or ""))
            admitted_paths.add(_request_path({"url": str(source.get("path") or "")}))
        elif source.get("kind") == "page_enum_options":
            for option in (source.get("options") or {}).values():
                if not isinstance(option, dict):
                    continue
                admitted_request_ids.update(str(value) for value in (option.get("source_request_ids") or []) if value)
                admitted_paths.add(_request_path({"url": str(option.get("source_url") or "")}))
    admitted_paths.update(
        _request_path({"url": binding.source_url})
        for step in spec.steps for binding in (step.selects or []) if binding.source_url
    )
    admitted_request_ids.discard("")
    admitted_paths.discard("")
    option_sources: list[dict[str, Any]] = []
    for fact in (spec.request_facts.requests or []):
        if (fact.method or "").upper() != "GET":
            continue
        if (
            str(fact.request_id or "") not in admitted_request_ids
            and _request_path({"url": fact.path or fact.url}) not in admitted_paths
        ):
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
        "suggestions": list(report.get("suggestions") or [])[:80],
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
                        "source_kind": p.source_kind,
                        "exposed_to_user": p.exposed_to_user,
                        "reason": p.reason,
                        "enum_options": list(p.enum_options or [])[:30],
                        "enum_value_map": dict(p.enum_value_map or {}),
                        "evidence": list(p.evidence or [])[:10],
                    }
                    for p in (st.params or [])[:60]
                ],
                "response_paths": normalized_leaf_paths(st.response_json, max_paths=80),
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
        "request_facts": [
            {
                "request_id": r.get("request_id"),
                "request_index": r.get("request_index"),
                "method": r.get("method"),
                "path": r.get("path") or r.get("url"),
                "role": r.get("role"),
                "confidence": r.get("confidence"),
                "reason": r.get("reason"),
            }
            for r in request_facts[:120]
        ],
        "recorded_field_evidence": recorded_field_evidence,
        "recorded_option_sources": recorded_option_sources,
        "page_events": recorded_page_events,
        "candidate_option_sources": option_sources,
    }


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


def _autofix_ops_to_edits(
    spec: FlowSpec,
    ops: list[dict[str, Any]],
    *,
    allow_scope_changes: bool = True,
) -> list[dict[str, Any]]:
    from dano.execution.page.recording_live import LIVE_RECORDING_AGENT_OPS

    edits: list[dict[str, Any]] = []
    cap_by_name = {c.name: idx for idx, c in enumerate(spec.capabilities or []) if c.name}
    step_by_id = {step.step_id: step for step in spec.steps}

    def locked_param(step_id: str, path: str) -> bool:
        from dano.execution.page.recording_field_identity import FieldRef, FieldReferenceError, resolve_field_ref

        try:
            param = resolve_field_ref(spec, FieldRef(
                step_id=step_id if step_id in step_by_id else "",
                request_id="" if step_id in step_by_id else step_id,
                wire_path=path,
            )).param
        except FieldReferenceError:
            param = None
        # Automatic edits use the stored request path as identity.  Treat an
        # unmatched path as unavailable instead of falling back to a name/leaf.
        return param is None or bool(param.locked)

    for op in ops or []:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "")
        if kind == "rename_step":
            step_id = str(op.get("step_id") or "")
            name = str(op.get("name") or op.get("title") or "").strip()
            if step_id in step_by_id and name:
                edits.append({"op": "update", "step_id": step_id, "field": "name", "value": name})
        elif kind == "promote_request":
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
                    "op": "reorder_capability_steps",
                    "capability_index": cap_by_name[cap_name],
                    "step_ids": [str(x) for x in step_ids],
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
        elif kind in LIVE_RECORDING_AGENT_OPS:
            edits.append({**op, "actor": "agent"})
    return edits






def _auto_fix_target_capability_name(spec: FlowSpec) -> str:
    caps = list(spec.capabilities or [])
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
    caps = list(spec.capabilities or [])
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

    这层只补“结构必需但可确定”的编排内容，语义判断仍交给 Pi/人工：
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
    repair_ops: list[dict[str, Any]],
    max_rounds: int = 3,
    expand_requests: bool = True,
    allow_scope_changes: bool | None = None,
) -> FlowSpec:
    """Apply Pi-submitted repair operations through deterministic gates."""
    if not isinstance(repair_ops, list) or any(not isinstance(op, dict) for op in repair_ops):
        raise ValueError("recording repair ops must be a list of objects")
    _validate_recording_agent_ops(repair_ops)
    current = spec.model_copy(deep=True)
    if allow_scope_changes is None:
        allow_scope_changes = expand_requests
    _normalize_capability_references(current)
    from dano.onboarding.recording_verify import assign_unassigned_internal_steps

    current = assign_unassigned_internal_steps(current)
    history: list[dict[str, Any]] = []
    for round_idx in range(max_rounds):
        report = validate_flow_spec(current)
        edits: list[dict[str, Any]] = []
        preflight_rejected_edits: list[dict[str, Any]] = []
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
            if not current.capabilities:
                continue
            edits.append({
                "op": "add_capability_step",
                "capability_name": _auto_fix_target_capability_for_request(current, item),
                "request_id": item.get("request_id") or "",
                "request_index": item.get("request_index"),
            })
        if round_idx == 0 and repair_ops:
            agent_edits: list[dict[str, Any]] = []
            for repair_op in repair_ops:
                translated = _autofix_ops_to_edits(
                    current,
                    [repair_op],
                    allow_scope_changes=bool(allow_scope_changes),
                )
                if translated:
                    agent_edits.extend(translated)
                    continue
                kind = str(repair_op.get("op") or "")
                step_id = str(repair_op.get("step_id") or repair_op.get("target_step") or "")
                path = str(repair_op.get("path") or repair_op.get("target_path") or "")
                error = (
                    f"param not found or unavailable: {step_id}:{path}"
                    if kind == "rename_field"
                    else "repair operation is not applicable to the current flow"
                )
                preflight_rejected_edits.append({
                    "op": kind,
                    "step_id": step_id,
                    "path": path,
                    "error": error,
                })
            if not allow_scope_changes:
                agent_edits = _planner_patch_edits(current, agent_edits)
            edits.extend(agent_edits)
        if not edits:
            history.append({
                "round": round_idx,
                "applied": 0,
                **({"rejected_edits": preflight_rejected_edits[:50]} if preflight_rejected_edits else {}),
                "remaining_errors": len(report.get("errors") or []),
            })
            break
        before = _flow_fingerprint(current)
        candidate = current.model_copy(deep=True)
        applied_edits: list[dict[str, Any]] = []
        rejected_edits: list[dict[str, Any]] = list(preflight_rejected_edits)
        # Pi 可能给出一个已经被前序编辑删除/改名的字段。单条坏 patch 不应让
        # 整个“自动修复”请求失败；按顺序应用，保留成功项并把拒绝原因回显。
        for edit in edits:
            try:
                candidate = apply_flow_edits(candidate, [{**edit, "actor": "repair"}])
                applied_edits.append(edit)
            except Exception as exc:  # noqa: BLE001
                rejected_edits.append({
                    "op": str(edit.get("op") or ""),
                    "step_id": str(edit.get("step_id") or ""),
                    "path": str(edit.get("param_path") or edit.get("path") or edit.get("target_path") or ""),
                    "error": str(exc)[:300],
                })
        if not applied_edits:
            history.append({
                "round": round_idx,
                "applied": 0,
                "changed": False,
                "rejected_edits": rejected_edits[:50],
                "remaining_errors": len(report.get("errors") or []),
            })
            break
        candidate.meta = {
            **(candidate.meta or {}),
            "auto_fix": {
                "round": round_idx + 1,
                "last_edits": applied_edits[:50],
                "rejected_edits": rejected_edits[:50],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        candidate = _sync_capability_io_schemas(candidate)
        if allow_scope_changes:
            # Explicit repair may promote recorded requests and needs another
            # round to finish their fields/dependencies. Preserve that existing
            # workflow; the strict semantic plan/optimization path below never
            # enables scope expansion.
            accepted, gate = True, {
                "accepted": True,
                "reasons": [],
                "scope_expansion_round": True,
            }
        else:
            accepted, gate = _semantic_candidate_gate(current, candidate)
        if not accepted:
            history.append({
                "round": round_idx,
                "applied": 0,
                "changed": False,
                "proposal_rejected": True,
                "proposal_gate": gate,
                **({"rejected_edits": rejected_edits[:50]} if rejected_edits else {}),
            })
            break
        current = candidate
        after = _flow_fingerprint(current)
        history.append({
            "round": round_idx,
            "applied": len(applied_edits),
            "changed": before != after,
            **({"rejected_edits": rejected_edits[:50]} if rejected_edits else {}),
        })
        if before == after:
            break
        if validate_flow_spec(current).get("passed"):
            break
    current.meta = {**(current.meta or {}), "auto_fix_history": history}
    current = _repair_generated_capability_contracts(current)
    current = _sync_capability_io_schemas(current)
    return append_flow_version(refresh_review_items(current), "auto_fix", reason="一键自动修正")


def _auto_confirm_ready_capabilities(
    spec: FlowSpec,
    *,
    refresh_machine_owned: bool = False,
) -> FlowSpec:
    """置信度超过 70% 的能力默认采纳，低置信能力仍可人工采纳。"""
    _normalize_capability_references(spec)
    verification_complete = bool(((spec.meta or {}).get("verification_run") or {}).get("complete"))
    for cap in spec.capabilities or []:
        if cap.confirmed:
            # Planner confirmation is automatic. Verification may append a
            # verified readback/fact_check to the same executable contract, so
            # refresh that machine-owned fingerprint after verification. User
            # confirmations remain immutable and still detect later changes.
            if (
                (verification_complete or refresh_machine_owned)
                and not cap.locked
                and cap.updated_by in {"planner", "repair", "agent", "system"}
            ):
                cap.confirmation_hash = _capability_confirmation_hash(spec, cap)
            continue
        if not (verification_complete or refresh_machine_owned) and float(cap.confidence or 0) <= 0.7:
            continue
        cap.confirmed = True
        cap.requires_human_confirm = False
        cap.status = "confirmed"
        cap.updated_by = "planner"
        cap.confirmation_hash = _capability_confirmation_hash(spec, cap)
    return spec


























def _looks_internal(name: str) -> bool:
    return looks_internal_param_name(name) if name else False




# ─────────── Step D: GET 表单手选 ───────────




# ─────────── Step D: 确定性命名 + 业务说明 ───────────
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


def _semantic_purpose(spec: FlowSpec) -> str:
    semantic_plan = ((spec.meta or {}).get("capability_model") or {}).get("semantic_plan") or {}
    understanding = semantic_plan.get("business_understanding") if isinstance(semantic_plan, dict) else None
    if isinstance(understanding, dict):
        grounded = str(
            understanding.get("intent")
            or understanding.get("purpose")
            or understanding.get("summary")
            or ""
        ).strip()
        if grounded:
            return grounded[:240]
    return ""


def _default_purpose(spec: FlowSpec) -> str:
    if not spec.steps:
        return "本流程未包含任何操作步骤，暂不能生成可执行 Skill。"
    title = _title_without_step_suffix(spec.title) or (spec.steps[-1].name or _derive_step_name(spec.steps[-1]))
    return (
        f"该 Skill 用于按录制得到的 {len(spec.steps)} 个步骤执行「{title}」，"
        "并在运行期重新解析用户参数、系统常量和接口依赖。"
    )


def render_business_description(spec: FlowSpec) -> str:
    """Generate a deterministic description from accepted FlowSpec facts."""
    current = refresh_review_items(_sync_capability_io_schemas(spec.model_copy(deep=True)))
    lines: list[str] = [
        "# 业务流程说明",
        "",
        "## 1. 业务目的",
        _semantic_purpose(current) or _default_purpose(current),
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


from dano.execution.page.recording_facts import (
    _BORING_LINK_VALUES,
    _BUSINESS_QUERY_PATH_RE,
    _INTERNAL_WORKFLOW_READ_RE,
    _NOISE_EXTS,
    _NOISE_SEGS,
    _OPTION_SEGS,
    _QUERY_ACTION_RE,
    _RECORD_IDENTITY_LEAVES,
    _REQUEST_OBSERVER_KEYS,
    _SCREENSHOT_OPTION_CONTROL_KINDS,
    _TRANSPORT_FILTER_KEYS,
    _TYPEAHEAD_PATH_RE,
    _WORKFLOW_CONTEXT_TOKENS,
    _WRITE_HINT_SEGS,
    _WRITE_METHODS,
    _api_option_source_refs,
    _attach_request_role,
    _build_request_facts,
    _business_filter_count,
    _caller_filter_key,
    _choice_control_triggered,
    _compact_repeated_endpoint_observations,
    _dedupe_preread_candidates,
    _dedupe_request_identities,
    _dependency_consumer_candidate,
    _field_evidence_for_request,
    _field_leaf_token,
    _find_request_fact_item,
    _has_query_action_evidence,
    _is_api_like_request_fact,
    _is_document_record_identity_path,
    _is_noise_request,
    _list_payload_has_conventional_option_contract,
    _list_payload_has_reference_contract,
    _list_payload_is_business_records,
    _looks_graphql_request,
    _looks_pagination_field,
    _looks_telemetry_request,
    _merge_response_schemas,
    _option_sources_from_page_enum_options,
    _page_enum_options_from_request_facts,
    _params_from_get_query,
    _path_from_url,
    _preread_candidate_score,
    _preread_dedupe_key,
    _query_param_type,
    _read_is_entity_enrichment_lookup,
    _record_identity_values_for_request,
    _recording_evidence_matches_request,
    _recording_evidence_matches_scope,
    _request_analysis_from_entry,
    _request_fact_entry,
    _request_fact_from_entry,
    _request_fact_has_record_identity,
    _request_fact_item,
    _request_fact_items,
    _request_fact_key,
    _request_fact_key_from_entry,
    _request_fact_signature_key,
    _request_has_business_query_evidence,
    _request_has_command_anchor,
    _request_has_option_endpoint_hint,
    _request_has_reference_entity_hint,
    _request_has_write_hint,
    _request_order_value,
    _request_path,
    _request_precedes,
    _request_query_values,
    _request_role_key,
    _request_segments,
    _request_sequence_value,
    _request_signature,
    _request_transaction_id,
    _request_values,
    _response_has_scalar_business_value,
    _response_referenced_later,
    _response_values,
    _role_row,
    _sample_hit_count,
    _schema_from_response_value,
    _trace_pos,
    _useful_link_value,
    _workflow_context_values_for_request,
    classify_network_request,
)


from dano.execution.page.recording_agent_contract import (
    _LIVE_PLAN_BLOCKING_GAPS,
    _META_ONLY_REPAIR_OPS,
    _RECORDING_AGENT_ALLOWED_OPS,
    _RECORDING_FIELD_OPS,
    _allowed_values_from_exc,
    _live_capability_plan_is_terminal,
    _recording_operation_result,
    _recording_requested_target,
    _recording_resolved_target,
    _semantic_fact_hash,
    _semantic_fact_snapshot,
    _validate_recording_agent_ops,
    apply_recording_agent_submission,
    recording_agent_validation,
    recording_capability_plan_complete,
)
import dano.execution.page.recording_agent_contract as _recording_agent_contract
if hasattr(_recording_agent_contract, '_bind_flow_spec_helpers'):
    _recording_agent_contract._bind_flow_spec_helpers()


from dano.execution.page.recording_analysis_state import (
    _model_visible_request_facts,
    recording_agent_state,
)
import dano.execution.page.recording_analysis_state as _recording_analysis_state
if hasattr(_recording_analysis_state, '_bind_flow_spec_helpers'):
    _recording_analysis_state._bind_flow_spec_helpers()

register_sync_flow_spec_models(sync_flow_spec_models)
