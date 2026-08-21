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
        param.required = required_state == "required"
        param.source = {**(param.source or {}), "required_state": required_state}
    elif _param_has_local_required_marker(param):
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
    refresh_required = int(
        (spec.meta or {}).get("stage_1_6_contract_version") or 0
    ) >= 2
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
