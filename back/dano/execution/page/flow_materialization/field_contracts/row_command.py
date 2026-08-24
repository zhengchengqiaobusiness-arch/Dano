"""Stage 5: row-command identity and fixed literal contracts."""
from __future__ import annotations

from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _param_has_manual_contract,
    _param_source_agent_classified,
)
from dano.execution.page.flow_materialization.field_contracts.record_identity import (
    _param_is_document_record_identity,
)
from dano.execution.page.flow_materialization.field_contracts.edit_form import (
    _step_is_record_edit_form,
)


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
            if (
                param.locked
                or _param_has_manual_contract(param)
                or _param_source_agent_classified(param)
            ):
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
