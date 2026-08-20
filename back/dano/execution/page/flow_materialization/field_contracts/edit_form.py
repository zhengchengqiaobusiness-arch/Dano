"""Stage 5: edit hydration and readonly/display contracts."""
from __future__ import annotations

from typing import Any
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.recording_facts import (
    _field_leaf_token,
    _recording_evidence_matches_scope,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _looks_audit_system_leaf,
    _param_control_is_readonly,
    _param_group_prefix,
    _param_has_manual_contract,
    _param_source_agent_classified,
)
from dano.execution.page.flow_materialization.field_contracts.record_identity import (
    _looks_row_identity_leaf,
    _param_is_document_record_identity,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_has_editable_control_evidence,
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
    strict_edit_evidence = int((spec.meta or {}).get("stage_1_6_contract_version") or 0) >= 2
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
                and not _param_has_editable_control_evidence(param)
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
                if strict_edit_evidence and not _param_has_editable_control_evidence(param):
                    _mark_system_hydrated_field(
                        param,
                        "该字段来自详情响应，但没有可编辑控件证据，保留为上游回填字段",
                    )
                    continue
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

_PENDING_FLOW_SPEC_HELPERS = {'_is_write_step': 'dano.execution.page.capability_kinds', '_param_has_command_local_control': 'dano.execution.page.flow_materialization.field_contracts.row_command', '_strip_body_prefix': 'dano.execution.page.flow_spec_core.normalization'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
