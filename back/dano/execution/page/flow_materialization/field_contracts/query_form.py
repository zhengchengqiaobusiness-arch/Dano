"""Stage 5: business list query filter contracts."""
from __future__ import annotations

from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.recording_facts import (
    _caller_filter_key,
    _looks_pagination_field,
)
from dano.execution.page.flow_materialization.field_contracts.create_form import (
    _create_unknown_has_caller_evidence,
)
from dano.execution.page.flow_materialization.field_contracts.record_identity import (
    _looks_row_identity_leaf,
    _param_is_document_record_identity,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _looks_runtime_field,
    _looks_system_const_field,
    _param_control_kinds,
    _param_has_manual_contract,
)
from dano.execution.page.flow_materialization.field_contracts.edit_form import (
    _editable_required_state,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_has_editable_control_evidence,
)
from dano.execution.page.flow_materialization.field_contracts.required import (
    _param_has_local_required_marker,
)
from dano.execution.page.flow_materialization.request_steps import (
    _step_role,
)


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


def _mark_query_filter_caller(
    param: ParamField, *, reason: str, refresh_required: bool,
) -> None:
    param.category = "user_param"
    param.exposed_to_user = True
    param.editable = True
    param.need_human_confirm = False
    if refresh_required:
        required_state = _editable_required_state(param)
        if required_state == "unknown":
            # Search criteria are optional unless the page recorded an
            # explicit required marker. Presence in one captured URL only
            # proves that the operator used the filter in that run.
            required_state = "optional"
        param.required = required_state == "required"
        param.source = {**(param.source or {}), "required_state": required_state}
    elif _param_has_local_required_marker(param):
        param.required = True
        param.source = {**(param.source or {}), "required_state": "required"}
    elif str((param.source or {}).get("required_state") or "") not in {"required", "optional"}:
        param.source = {**(param.source or {}), "required_state": "unknown"}
    if reason:
        param.reason = reason


def _page_enum_has_complete_wire_mapping(param: ParamField) -> bool:
    options = list(param.enum_options or [])
    mapping = dict(param.enum_value_map or {})
    labels: list[str] = []
    for option in options:
        if isinstance(option, dict):
            label = option.get("label", option.get("name", option.get("text")))
            if label in (None, ""):
                return False
            labels.append(str(label))
            if option.get("value") is not None:
                mapping.setdefault(str(label), option.get("value"))
        elif isinstance(option, (list, tuple)) and len(option) >= 2:
            if option[0] in (None, "") or option[1] is None:
                return False
            labels.append(str(option[0]))
            mapping.setdefault(str(option[0]), option[1])
        else:
            label = str(option or "").strip()
            if not label:
                return False
            labels.append(label)
    return bool(labels) and all(
        label in mapping and mapping[label] is not None for label in labels
    )


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
    refresh_required = int(
        (spec.meta or {}).get("stage_1_6_contract_version") or 0
    ) >= 2
    for step in spec.steps or []:
        if _step_is_record_detail_query(step):
            for param in step.params or []:
                if (
                    param.locked
                    or _param_has_manual_contract(param)
                    or not str(param.path or "").startswith("query.")
                    or not (
                        _param_is_document_record_identity(param)
                        or _looks_row_identity_leaf(param.key, param.path)
                    )
                ):
                    continue
                param.category = "user_param"
                param.source_kind = "selected_record_identity"
                param.source = {
                    "kind": "selected_record_identity",
                    "path": param.path,
                    "required_state": "required",
                }
                param.required = True
                param.exposed_to_user = True
                param.editable = True
                param.need_human_confirm = False
                if str(param.label or param.key or "").strip().casefold() in {
                    "id", "ids", "recordid", "record_id",
                }:
                    param.label = "记录"
                param.reason = "调用方选择要查看的业务记录，录制样例 ID 不能固化"
            continue
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
            if (
                (
                    _looks_runtime_field(param.key, param.path)
                    or _looks_system_const_field(param.key, param.path)
                )
                and not _param_has_editable_control_evidence(param)
            ):
                continue
            if param.source_kind in {
                "page_context", "request_header", "session", "current_user",
                "computed", "page_rule", "selected_option_field",
            }:
                continue
            source = param.source or {}
            if source.get("unmapped_page_options") is True or (
                param.source_kind == "page_enum"
                and source.get("enum_confirmed") is False
                and not _page_enum_has_complete_wire_mapping(param)
            ):
                # The page control is authoritative evidence that this field
                # is a chooser even when only part of its label→wire mapping
                # was observed. Keep the captured choices as a non-binding
                # snapshot; capability I/O already submits the caller's wire
                # value without turning an incomplete snapshot into a schema
                # restriction.
                param.type = "enum"
                param.source_kind = "page_enum"
                param.source = {
                    **source,
                    "kind": "page_enum",
                    "enum_confirmed": False,
                    "required_state": str(
                        source.get("required_state") or "optional"
                    ),
                }
                param.need_human_confirm = False
                param.reason = (
                    "页面证明该字段为选择控件；已保留录制到的枚举候选，"
                    "未捕获的 label→wire 映射不作为执行限制"
                )
            if param.source_kind in caller_kinds:
                if (
                    refresh_required
                    or not param.exposed_to_user
                    or param.category != "user_param"
                ):
                    _mark_query_filter_caller(
                        param, reason="", refresh_required=refresh_required,
                    )
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
                    refresh_required=refresh_required,
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
                    refresh_required=refresh_required,
                )
