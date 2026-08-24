"""Stage 5: page_default, page_rule, and date-range companions."""
from __future__ import annotations

from typing import Any
import re
from dano.execution.page.flow_spec_core.models import (
    FlowSpec,
    ParamField,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _looks_audit_system_leaf,
    _looks_runtime_field,
    _looks_system_const_field,
    _param_control_is_readonly,
    _param_has_manual_contract,
)
from dano.execution.page.flow_materialization.field_contracts.edit_form import (
    _editable_required_state,
    _looks_display_echo_field,
)
from dano.execution.page.recording_facts import (
    _looks_pagination_field,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _param_exposed_to_caller,
    _param_has_editable_control_evidence,
)
from dano.execution.page.flow_materialization.field_contracts.row_command import (
    _param_has_command_local_control,
    _step_is_row_command,
)
from dano.execution.page.flow_materialization.field_contracts.record_identity import (
    _param_is_document_record_identity,
)


def _mark_auto_fill_caller_override(
    param: ParamField, reason: str, *, refresh_required: bool,
) -> None:
    required_state = _editable_required_state(param) if refresh_required else "optional"
    param.category = "user_param"
    param.exposed_to_user = True
    param.editable = True
    param.required = required_state == "required"
    param.need_human_confirm = False
    param.source = {
        **(param.source or {}),
        "allow_caller_override": True,
        "required_state": required_state,
    }
    if reason and "可修改" not in (param.reason or ""):
        param.reason = f"{param.reason}；{reason}" if param.reason else reason


def _apply_page_rule_caller_override(spec: FlowSpec) -> None:
    """Keep auto-fill origin, but follow the page: editable means caller may change it.

    Origin (how the page produced the value) and ownership (who may supply it)
    are separate. Selected-row echoes and computed totals stay system-owned
    unless an editable control proves the page lets the operator overwrite
    them. Merely failing to observe readonly/disabled is not edit evidence.
    """
    strict_edit_evidence = int((spec.meta or {}).get("stage_1_6_contract_version") or 0) >= 2
    for step in spec.steps or []:
        if _step_is_row_command(step):
            continue
        for param in step.params or []:
            if param.locked or _param_has_manual_contract(param):
                continue
            if param.source_kind == "page_rule":
                source = dict(param.source or {})
                formula = source.get("formula")
                rule = dict(formula) if isinstance(formula, dict) else source
                kind = str(rule.get("strategy") or rule.get("kind") or "")
                if kind in {
                    "date_range_end", "date_span_days", "date_span_days_json",
                    "product", "sum", "difference", "percent_of",
                    "remainder_after_percent",
                }:
                    source["formula"] = {
                        key: value for key, value in rule.items()
                        if key not in {"formula", "path"}
                    }
                    source["executable"] = True
                    param.source = source
                else:
                    param.source_kind = "unknown"
                    param.source = {
                        "kind": "unresolved",
                        "candidate_kind": "page_rule",
                        "path": param.path,
                        "executable": False,
                        "reason": "frontend_formula_not_observed",
                    }
                    param.need_human_confirm = True
                    param.reason = "页面只读值疑似由前端生成，但录制证据没有公式，不能标记为可执行页面规则"
            if _looks_pagination_field(param.key, param.path):
                continue
            has_editable_control = _param_has_editable_control_evidence(param)
            if (
                (
                    _looks_runtime_field(param.key, param.path)
                    or _looks_system_const_field(param.key, param.path)
                )
                and not has_editable_control
            ):
                continue
            if (
                _param_is_document_record_identity(param)
                and param.source_kind != "user_input"
            ):
                continue
            if (
                _looks_audit_system_leaf(param.key, param.path)
                and not has_editable_control
                and not _param_has_command_local_control(step, param)
            ):
                continue
            if _param_control_is_readonly(param):
                continue
            if (
                param.source_kind == "selected_option_field"
                and (
                    has_editable_control
                    or not strict_edit_evidence
                )
            ):
                _mark_auto_fill_caller_override(
                    param, "所选记录自动带入，页面允许修改",
                    refresh_required=strict_edit_evidence,
                )
                continue
            if _looks_display_echo_field(step, param) and not has_editable_control:
                continue
            if (
                param.source_kind == "computed"
                and has_editable_control
            ):
                _mark_auto_fill_caller_override(
                    param, "页面自动计算，但仍允许调用方修改",
                    refresh_required=strict_edit_evidence,
                )


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
            output_format = (
                "epoch_ms" if re.fullmatch(r"-?\d{13}", end_text)
                else "epoch_s" if re.fullmatch(r"-?\d{10}", end_text)
                else "%Y-%m-%d 23:59:59" if re.search(r"23:59:59$", end_text)
                else "%Y-%m-%d 23:59"
            )
            end.source = {
                "kind": "date_range_end",
                "start_field": start.key,
                "path": end.path,
                "output_format": output_format,
                "formula": {
                    "kind": "date_range_end",
                    "start_field": start.key,
                    "output_format": output_format,
                },
                "executable": True,
            }
            end.exposed_to_user = False
            end.editable = False
            end.required = False
            end.need_human_confirm = False
            end.reason = "查询区间结束时刻由开始日期按当天结束补齐，不作为独立调用方输入"
