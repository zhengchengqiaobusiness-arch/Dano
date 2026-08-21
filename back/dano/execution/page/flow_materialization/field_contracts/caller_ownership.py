"""Stage 5: caller ownership, exposure, and editable override."""
from __future__ import annotations

from dano.execution.page.flow_spec_core.models import (
    ParamField,
)
from dano.execution.page.recording_facts import (
    _looks_pagination_field,
)
from dano.execution.page.flow_materialization.links import (
    _previous_response_source_step_id,
)


def _field_has_unlocked_editable_control(field: dict | None) -> bool:
    """True when a page control can still accept caller input.

    Selected-row projections must not hide an editable number/text/date just
    because the captured value also appears on the chosen option. Locked
    siblings such as barcode or stock remain projectable.
    """
    if not isinstance(field, dict):
        return False
    kind = str(field.get("control_kind") or field.get("input_type") or "unknown").lower()
    disabled = field.get("control_disabled", field.get("disabled"))
    read_only = field.get("control_read_only", field.get("read_only"))
    if disabled is True:
        return False
    if kind in {"select", "combobox"}:
        return True
    if read_only is True:
        return False
    return kind in {
        "text", "search", "textarea", "contenteditable", "number", "range",
        "date", "datetime", "datetime-local", "time", "checkbox", "radio",
        "switch", "spinbutton", "file", "upload", "email", "url",
    }


def _param_has_editable_control_evidence(param: ParamField | None) -> bool:
    if param is None:
        return False
    for item in param.evidence or []:
        if not isinstance(item, dict) or item.get("kind") != "page_control":
            continue
        if item.get("interacted") and not bool(
            item.get("disabled", item.get("control_disabled", False))
        ):
            return True
        if _field_has_unlocked_editable_control(item):
            return True
    return False


def _param_was_caller_typed(param: ParamField) -> bool:
    for item in param.evidence or []:
        if isinstance(item, dict) and item.get("kind") == "page_control" and item.get("interacted"):
            return True
    return False


_RUNTIME_SUPPLIED_SOURCE_KINDS = frozenset({
    "previous_response", "current_user", "storage", "cookie", "page_context",
    "request_header", "system_time", "system_generated", "computed",
    "constant", "page_rule", "loop_item", "selected_option_field",
    "dynamic_structure",
})


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
