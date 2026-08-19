"""Stage 5: record identity and row identity constraints."""
from __future__ import annotations

from typing import Any
from dano.execution.page.flow_spec_core.models import (
    FlowStep,
    ParamField,
)
from dano.execution.page.recording_facts import (
    _field_leaf_token,
    _is_document_record_identity_path,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _is_missing_wire_placeholder,
)


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


def _looks_row_identity_leaf(key: str, path: str) -> bool:
    return "[" in str(path or "") and _field_leaf_token(key, path) in {"id", "ids"}
