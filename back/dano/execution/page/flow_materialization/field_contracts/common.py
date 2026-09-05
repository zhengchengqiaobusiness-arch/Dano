"""Stage 5: shared field-contract helpers without scenario rules."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
    SelectBinding,
)
from dano.execution.page.request_capture import (
    looks_internal_param_name,
)
from dano.execution.page.recording_facts import (
    _SCREENSHOT_OPTION_CONTROL_KINDS,
    _caller_filter_key,
    _field_leaf_token,
    _is_document_record_identity_path,
    _looks_pagination_field,
)


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


_MISSING_WIRE_PLACEHOLDERS = {
    "undefined", "null", "none", "nan", "[object object]",
}


def _is_missing_wire_placeholder(value: Any) -> bool:
    """Return whether a captured textual value represents no wire value."""
    return isinstance(value, str) and value.strip().casefold() in _MISSING_WIRE_PLACEHOLDERS


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
    if has_control and control_kind == "file" and not control_locked:
        return {
            "category": "user_param",
            "source_kind": "user_input",
            "source": {
                "kind": "file_input",
                "path": path,
                "filename": str(field.get("filename") or ""),
                "mime_type": str(field.get("mime_type") or ""),
                "multiple": bool(field.get("multiple")),
                "file_count": int(field.get("file_count") or 0),
            },
            "editable": True,
            "exposed_to_user": True,
            "reason": "页面文件控件由调用方提供文件；只保留安全元数据，不复用录制机器路径",
            "need_human_confirm": False,
        }
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
                "source_kind": "unknown",
                "source": {
                    "kind": "unresolved",
                    "candidate_kind": "page_rule",
                    "path": path,
                    "control_kind": control_kind,
                    "executable": False,
                    "reason": "frontend_formula_not_observed",
                },
                "editable": False,
                "exposed_to_user": False,
                "reason": "页面只读或禁用值疑似由前端生成，但录制证据没有公式，不能标记为可执行页面规则",
                "need_human_confirm": True,
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


def _append_reason_detail(reason: str, detail: str | None) -> str:
    reason = str(reason or "")
    if not detail:
        return reason
    if detail in reason:
        return reason
    return f"{reason}；{detail}" if reason else detail


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


def _param_type_agent_classified(param: ParamField) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("actor") == "agent"
        and item.get("kind") == "param_type"
        and str(item.get("business_type") or "") == str(param.type or "")
        for item in (param.evidence or [])
    )


def _param_enum_agent_classified(param: ParamField) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("actor") == "agent"
        and item.get("kind") == "enum_options"
        for item in (param.evidence or [])
    )


def _param_has_full_lock(param: ParamField) -> bool:
    return bool(param.locked)


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
                if (
                    not _param_axis_manually_edited(
                        param, "category", "exposed_to_user", "editable",
                    )
                    and param.category not in {"runtime_var", "system_const"}
                    and param.source_kind != "selected_option_field"
                    and not (
                        param.source_kind == "previous_response"
                        and (param.source or {}).get("allow_caller_override") is False
                    )
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
        and (
            (item.get("field_aliases") or [])
            or str(item.get("binding_status") or "") == "bound"
        )
        and public_name
        and not looks_internal_param_name(public_name)
        for item in (param.evidence or [])
    )


_PARALLEL_FORM_OPERATION_SEGMENTS = frozenset({
    "create", "add", "insert", "update", "edit", "modify", "save",
})


def _parallel_form_schema_path(param: ParamField) -> str:
    source = param.source or {}
    path = str(source.get("schema_identity_path") or param.path or "")
    path = path.removeprefix("request.")
    for prefix in ("body.", "query.", "path."):
        path = path.removeprefix(prefix)
    return re.sub(r"\[(?:\d+|\*)\]", "[]", path)


def _parallel_form_resource(step: FlowStep) -> str:
    path = str(step.path or step.url or "").split("?", 1)[0].rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if segments and segments[-1].casefold() in _PARALLEL_FORM_OPERATION_SEGMENTS:
        segments.pop()
    return "/".join(segments).casefold()


def _parallel_form_paths(step: FlowStep) -> set[str]:
    return {
        _parallel_form_schema_path(param)
        for param in step.params
        if _parallel_form_schema_path(param)
    }


def _parallel_form_steps_equivalent(left: FlowStep, right: FlowStep) -> bool:
    left_meta = left.source_meta or {}
    right_meta = right.source_meta or {}
    left_page = str(left_meta.get("page_id") or "")
    right_page = str(right_meta.get("page_id") or "")
    if not left_page or left_page != right_page:
        return False
    if str(left_meta.get("frame_id") or "") != str(right_meta.get("frame_id") or ""):
        return False
    if _parallel_form_resource(left) != _parallel_form_resource(right):
        return False
    left_paths = _parallel_form_paths(left)
    right_paths = _parallel_form_paths(right)
    shared = left_paths & right_paths
    return bool(
        len(shared) >= 3
        and len(shared) / min(len(left_paths), len(right_paths)) >= 0.75
    )


def _propagate_grounded_parallel_field_controls(spec: FlowSpec) -> int:
    """Recover a missing choice contract from the equivalent sibling form."""
    write_steps = [
        step for step in spec.steps
        if str(step.method or "GET").upper() not in {"GET", "HEAD", "OPTIONS"}
    ]
    changed = 0
    for target_step in write_steps:
        peers = [
            step for step in write_steps
            if step is not target_step
            and _parallel_form_steps_equivalent(target_step, step)
        ]
        for target in target_step.params:
            if target.locked or _param_has_manual_contract(target):
                continue
            if any(
                isinstance(item, dict) and item.get("kind") == "page_control"
                for item in target.evidence or []
            ):
                continue
            schema_path = _parallel_form_schema_path(target)
            donors = [
                (peer, param, control)
                for peer in peers
                for param in peer.params
                if _parallel_form_schema_path(param) == schema_path
                for control in param.evidence or []
                if isinstance(control, dict)
                and control.get("kind") == "page_control"
                and control.get("source") == "recorder_dom"
                and str(control.get("control_kind") or "").casefold()
                in _SCREENSHOT_OPTION_CONTROL_KINDS
            ]
            signatures = {
                (
                    str(control.get("control_kind") or "").casefold(),
                    bool(control.get("editable")),
                    bool(control.get("disabled")),
                    bool(control.get("read_only")),
                    control.get("required_observed"),
                )
                for _peer, _param, control in donors
            }
            if len(signatures) != 1:
                continue
            donor_step, donor, control = donors[-1]
            projected = {
                key: deepcopy(copy_value)
                for key, copy_value in control.items()
                if key not in {
                    "evidence_id", "occurrence_id", "field_identity_id",
                    "request_path", "request_id", "step_id", "interacted",
                }
            }
            projected.update({
                "kind": "page_control",
                "source": "recorded_parallel_form",
                "request_path": target.path,
                "binding_status": "parallel_contract",
                "interacted": False,
                "parallel_source_step_id": donor_step.step_id,
                "parallel_source_path": donor.path,
                "parallel_source_evidence_id": str(control.get("evidence_id") or ""),
            })
            target.evidence = [*(target.evidence or []), projected]
            if control.get("required_observed") is True:
                target.evidence.append({
                    "kind": "page_required",
                    "source": "recorded_parallel_form",
                    "request_path": target.path,
                    "binding_status": "parallel_contract",
                    "parallel_source_step_id": donor_step.step_id,
                })
            changed += 1
    return changed


def _propagate_grounded_parallel_field_names(spec: FlowSpec) -> int:
    """Share a recorder-grounded name across equivalent create/edit forms."""
    write_steps = [
        step for step in spec.steps
        if str(step.method or "GET").upper() not in {"GET", "HEAD", "OPTIONS"}
    ]
    changed = 0
    for target_step in write_steps:
        peers = [
            step for step in write_steps
            if step is not target_step
            and _parallel_form_steps_equivalent(target_step, step)
        ]
        if not peers:
            continue
        for target in target_step.params:
            if (
                _param_field_manually_edited(target, "label")
                or _param_has_grounded_public_name(target)
            ):
                continue
            current_name = str(target.label or target.key or "").strip()
            path_leaf = str(target.path or "").split(".")[-1]
            if current_name not in {"", str(target.key or ""), path_leaf}:
                continue
            schema_path = _parallel_form_schema_path(target)
            donors = [
                (peer, param)
                for peer in peers
                for param in peer.params
                if _parallel_form_schema_path(param) == schema_path
                and _param_has_grounded_public_name(param)
                and str(param.label or "").strip()
            ]
            labels = {str(param.label or "").strip() for _, param in donors}
            if len(labels) != 1:
                continue
            donor_step, donor = donors[-1]
            label = labels.pop()
            if label == current_name:
                continue
            target.label = label
            target.name_source = "recorded_parallel_field"
            target.confidence = max(float(target.confidence or 0.0), 0.95)
            target.confidence_tier = "linked"
            target.evidence = [*(target.evidence or []), {
                "kind": "parallel_field_name",
                "source": "recorder_dom",
                "step_id": donor_step.step_id,
                "wire_path": donor.path,
                "schema_identity_path": schema_path,
                "label": label,
            }]
            changed += 1
    return changed


def _param_has_grounded_type(param: ParamField) -> bool:
    """Return whether evidence grounds the business type, not its wire shape."""
    if (
        _param_has_full_lock(param)
        or _param_field_manually_edited(param, "type")
        or _param_type_agent_classified(param)
    ):
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

    Existing request params are authoritative. A response leaf describes
    output, never proof that a same-named query input exists.
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
    return None


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


def _param_group_prefix(path: str) -> str:
    text = str(path or "")
    if text.startswith("body."):
        text = text[5:]
    return re.sub(r"(?:\.[^.\[\]]+|\[\d+\])$", "", text) if ("." in text or "[" in text) else ""


def _param_control_kinds(param: ParamField) -> set[str]:
    return {
        str(item.get("control_kind") or "").lower()
        for item in (param.evidence or [])
        if isinstance(item, dict) and item.get("kind") == "page_control"
    }


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


_AUDIT_TIME_LEAVES = frozenset({
    "createtime", "updatetime", "createdat", "updatedat",
})
_AUDIT_ACTOR_LEAVES = frozenset({
    "creator", "updater", "modifier", "createby", "updateby",
    "creatorname", "updatername", "createdby", "updatedby",
    "creatorid", "createbyid", "ownerid", "ownername",
})


def _looks_audit_time_leaf(key: str, path: str) -> bool:
    leaf = _field_leaf_token(key, path)
    return leaf in _AUDIT_TIME_LEAVES or leaf.endswith(
        ("createtime", "updatetime", "createdat", "updatedat")
    )


def _looks_audit_actor_leaf(key: str, path: str) -> bool:
    return _field_leaf_token(key, path) in _AUDIT_ACTOR_LEAVES


def _looks_audit_system_leaf(key: str, path: str) -> bool:
    return _looks_audit_time_leaf(key, path) or _looks_audit_actor_leaf(key, path)


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
                if param.key == old_key:
                    continue
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

_PENDING_FLOW_SPEC_HELPERS = {'_ENUM_PARAM_TYPES': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_ENUM_SOURCE_KINDS': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_computed_formula_is_complete': 'dano.execution.page.flow_materialization.field_contracts.computed', '_date_like_epoch_seconds': 'dano.execution.page.flow_materialization.field_contracts.computed', '_infer_type_from_value': 'dano.execution.page.flow_spec_core.normalization', '_norm_field_name': 'dano.execution.page.flow_spec_core.normalization', '_param_exposed_to_caller': 'dano.execution.page.flow_materialization.field_contracts.caller_ownership', '_param_has_option_control_evidence': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_param_has_page_required_evidence': 'dano.execution.page.flow_materialization.field_contracts.required', '_param_required_agent_classified': 'dano.execution.page.flow_materialization.field_contracts.required', '_record_identity_is_caller_owned': 'dano.execution.page.flow_materialization.field_contracts.record_identity', '_refresh_param_enum_description': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_rename_param_public_key': 'dano.execution.page.flow_spec_core.controlled_edits', '_sample_value_set': 'dano.execution.page.flow_spec_core.normalization', '_select_has_executable_options': 'dano.execution.page.flow_release', '_select_source_kind': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_select_source_reason': 'dano.execution.page.flow_materialization.field_contracts.option_projection', '_strip_option_descriptions': 'dano.execution.page.flow_materialization.field_contracts.option_projection'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
