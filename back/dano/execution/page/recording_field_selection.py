"""Select one coherent control contract from repeated field observations."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any


_INTERACTION_OPS = {"fill", "select", "pick", "toggle", "upload"}
_KNOWN_CONTROL_KINDS = {
    "checkbox",
    "combobox",
    "contenteditable",
    "date",
    "datetime",
    "file",
    "number",
    "radio",
    "search",
    "select",
    "switch",
    "text",
    "textarea",
    "time",
}


def _normalized_identifier(value: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).casefold()


def _evidence_surface(item: dict[str, Any]) -> str:
    if item.get("in_dialog") is True:
        return "dialog"
    if item.get("in_dialog") is False:
        return "page"
    return str(item.get("surface") or "").strip().lower()


def _evidence_rank(item: dict[str, Any], wire_path: str) -> tuple[int, int, int, int, int]:
    relative = str(wire_path or "").removeprefix("request.")
    relative = relative.split(".", 1)[1] if "." in relative else relative
    full_identity = _normalized_identifier(relative)
    leaf_identity = _normalized_identifier(relative.rsplit(".", 1)[-1])
    aliases = {
        _normalized_identifier(value)
        for value in (item.get("field_aliases") or [])
        if _normalized_identifier(value)
    }
    alias_rank = 2 if full_identity in aliases else 1 if leaf_identity in aliases else 0
    surface = _evidence_surface(item)
    container = str(wire_path or "").removeprefix("request.").split(".", 1)[0]
    surface_rank = int(
        (container == "query" and surface in {"page", "list", "filter"})
        or (
            container == "body"
            and surface in {"dialog", "drawer", "popover", "table-inline"}
        )
    )
    interacted = int(str(item.get("op") or "").strip().lower() in _INTERACTION_OPS)
    labeled = int(bool(str(item.get("label") or item.get("field") or "").strip()))
    known_kind = int(
        str(item.get("control_kind") or item.get("input_type") or "").strip().lower()
        in _KNOWN_CONTROL_KINDS
    )
    return alias_rank, surface_rank, interacted, labeled, known_kind


def _contract_signature(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("control_kind") or item.get("input_type") or "").strip().lower(),
        _normalized_identifier(item.get("label") or item.get("field")),
    )


def select_field_contract_evidence(
    items: list[dict[str, Any]],
    wire_path: str,
) -> dict[str, Any] | None:
    """Return the strongest coherent evidence, or ``None`` on a real tie.

    Exact structural aliases outrank controls that were attached to the same
    wire value only by coincidence. Surface and interaction refine that match;
    they never override a conflicting structural identity.
    """
    expected_path = str(wire_path or "").removeprefix("request.")
    candidates = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        bound_path = str(raw.get("wire_path") or "").removeprefix("request.")
        if bound_path and bound_path != expected_path:
            continue
        candidates.append(raw)
    if not candidates:
        return None
    best_rank = max(_evidence_rank(item, expected_path) for item in candidates)
    strongest = [
        item for item in candidates
        if _evidence_rank(item, expected_path) == best_rank
    ]
    if len({_contract_signature(item) for item in strongest}) != 1:
        return None
    chosen = max(
        strongest,
        key=lambda item: (
            str(item.get("observed_at") or item.get("timestamp") or ""),
            str(item.get("occurrence_id") or item.get("evidence_id") or ""),
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
        ),
    )
    result = deepcopy(chosen)
    required = {
        item.get("required_observed")
        for item in strongest
        if isinstance(item.get("required_observed"), bool)
    }
    if len(required) == 1:
        result["required_observed"] = next(iter(required))
    elif len(required) > 1:
        result["required_observed"] = None
        result["required_state"] = "unknown"
    return result
