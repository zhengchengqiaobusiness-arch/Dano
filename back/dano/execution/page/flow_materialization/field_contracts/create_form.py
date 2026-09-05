"""Stage 5: create/submit form field ownership contracts."""
from __future__ import annotations

import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    IdentityBinding,
    ParamField,
)
from dano.execution.page.flow_spec_core.normalization import _strip_body_prefix
from dano.execution.page.flow_materialization.field_contracts.common import (
    _looks_audit_actor_leaf,
    _looks_audit_system_leaf,
    _looks_audit_time_leaf,
    _looks_page_context_field,
    _looks_runtime_field,
    _looks_system_const_field,
    _param_control_is_readonly,
    _param_control_kinds,
    _param_has_manual_contract,
)
from dano.execution.page.recording_facts import (
    _looks_pagination_field,
)
from dano.execution.page.flow_materialization.field_contracts.record_identity import (
    _looks_row_identity_leaf,
    _param_is_document_record_identity,
    _record_identity_is_caller_owned,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_has_editable_control_evidence,
    _param_was_caller_typed,
)
from dano.execution.page.flow_materialization.field_contracts.edit_form import (
    _editable_required_state,
)
from dano.execution.page.flow_materialization.field_contracts.required import (
    _param_has_local_required_marker,
)


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
    if _param_control_is_readonly(param):
        return True
    if param.source_kind == "page_default" and not (
        _param_has_editable_control_evidence(param) or _param_was_caller_typed(param)
    ):
        return True
    if _param_has_editable_control_evidence(param) or _param_was_caller_typed(param):
        # Structural page evidence outranks a field-name hint.  A field called
        # creator/status/id may still be a real editable business input.
        return False
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


def _mark_create_form_caller_input(
    param: ParamField, *, reason: str, refresh_required: bool,
) -> None:
    param.category = "user_param"
    param.exposed_to_user = True
    param.editable = True
    param.need_human_confirm = False
    if reason:
        param.reason = reason
    if refresh_required:
        required_state = _editable_required_state(param)
        param.required = required_state == "required"
        param.source = {**(param.source or {}), "required_state": required_state}
    elif _param_has_local_required_marker(param):
        param.required = True
        param.source = {**(param.source or {}), "required_state": "required"}
    elif str((param.source or {}).get("required_state") or "") not in {"required", "optional"}:
        param.source = {**(param.source or {}), "required_state": "unknown"}


def _apply_create_form_field_contracts(spec: FlowSpec) -> None:
    """Caller owns manual create/submit inputs; system owns proven derivations.

    Bound page controls are sufficient but not required. After formulas and
    option-row echoes are assigned, a remaining create-body unknown is a
    handwritten form value, not a system field.
    """
    caller_kinds = {
        "user_input", "form_option", "api_option",
        "page_enum", "static_enum", "manual_enum", "caller_input",
    }
    refresh_required = int(
        (spec.meta or {}).get("stage_1_6_contract_version") or 0
    ) >= 2
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
            option_source = (
                (param.source or {}).get("option_source")
                if isinstance((param.source or {}).get("option_source"), dict)
                else None
            )
            if param.source_kind == "previous_response" and option_source:
                previous_source = dict(param.source or {})
                required_state = str(
                    previous_source.get("required_state") or "unknown"
                )
                param.source_kind = "api_option"
                param.source = {
                    **option_source,
                    "kind": "api_option",
                    "required_state": required_state,
                    **{
                        key: previous_source[key]
                        for key in (
                            "original_key", "collision_resolved", "name_disambiguation",
                        )
                        if key in previous_source
                    },
                }
                _mark_create_form_caller_input(
                    param, reason="", refresh_required=refresh_required,
                )
                continue
            if param.source_kind == "page_default":
                if _param_has_editable_control_evidence(param) or _param_was_caller_typed(param):
                    _mark_create_form_caller_input(
                        param, reason="", refresh_required=refresh_required,
                    )
                continue
            if param.source_kind in caller_kinds:
                _mark_create_form_caller_input(
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
                _mark_create_form_caller_input(
                    param,
                    reason="新建/提交表单上由调用方选择的字段",
                    refresh_required=refresh_required,
                )
            else:
                param.source_kind = "user_input"
                param.source = {"kind": "sample", "path": param.path, "recorded": True}
                _mark_create_form_caller_input(
                    param,
                    reason="新建/提交表单上的手工输入，由调用方提供",
                    refresh_required=refresh_required,
                )
        _apply_create_form_runtime_origins(step)


def _apply_create_form_runtime_origins(step: FlowStep) -> None:
    """Login identity and system clocks stay off the caller list and off recorded literals."""
    reserved = {
        "previous_response", "computed", "selected_option_field", "api_option",
        "page_enum", "static_enum", "manual_enum", "form_option", "user_input",
        "caller_input",
    }
    seen_sources = {
        str(item.source)
        for item in (step.identity or [])
        if str(getattr(item, "source", "") or "")
    }
    for param in step.params or []:
        if param.locked or _param_has_manual_contract(param):
            continue
        if param.exposed_to_user or param.source_kind in reserved:
            continue
        if "[" in str(param.path or ""):
            continue
        if _looks_audit_time_leaf(param.key, param.path):
            param.category = "runtime_var"
            param.source_kind = "system_time"
            param.exposed_to_user = False
            param.editable = False
            param.required = False
            param.source = {**(param.source or {}), "kind": "system_time", "path": param.path}
            param.reason = "系统时间戳，运行期使用当前时间生成，不能使用录制空串或旧时刻"
            continue
        if _looks_audit_actor_leaf(param.key, param.path) or _looks_page_context_field(param.key, param.path):
            param.category = "runtime_var"
            param.source_kind = "current_user"
            param.exposed_to_user = False
            param.editable = False
            param.required = False
            source = f"current_user:{param.key or _strip_body_prefix(param.path)}"
            param.source = {
                **(param.source or {}),
                "kind": "identity",
                "path": source,
            }
            param.reason = "当前登录身份，运行期从登录态重新读取，不能使用录制者旧值或空串"
            if source not in seen_sources:
                step.identity.append(IdentityBinding(
                    path=_strip_body_prefix(param.path),
                    source=source,
                    value=None if param.value in (None, "") else str(param.value),
                ))
                seen_sources.add(source)


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

_PENDING_FLOW_SPEC_HELPERS = {'_is_write_step': 'dano.execution.page.capability_kinds', '_param_has_command_local_control': 'dano.execution.page.flow_materialization.field_contracts.row_command', '_step_is_record_edit_form': 'dano.execution.page.flow_materialization.field_contracts.edit_form', '_step_is_row_command': 'dano.execution.page.flow_materialization.field_contracts.row_command'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
