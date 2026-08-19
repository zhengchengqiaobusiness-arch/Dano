"""Stage 5: record-detail hydration links onto later write requests."""
from __future__ import annotations

from typing import Any
import copy
import re
from dano.execution.page.request_capture import (
    _leaf_paths,
    _parse_body,
)
from dano.execution.page.recording_facts import (
    _request_order_value,
    _request_precedes,
)


def _discover_record_hydration_links(
    captured_requests: list[dict[str, Any]],
    target_request_ids: set[str],
) -> list[dict[str, Any]]:
    """Find a record read whose object is copied into a later write form."""
    identity_keys = {
        "id", "recordid", "requestid", "applicationid", "businessid",
        "entityid", "itemid",
    }
    candidates_by_target: dict[str, list[dict[str, Any]]] = {}
    for target in captured_requests:
        target_id = str(target.get("request_id") or "")
        if target_id not in target_request_ids:
            continue
        target_body = _parse_body(target.get("post_data"))
        if not isinstance(target_body, dict):
            continue
        target_values = {
            path: raw
            for path, _tokens, _scalar, raw in _leaf_paths(target_body)
            if not isinstance(raw, (dict, list, bool))
        }
        if not target_values:
            continue
        for source in captured_requests:
            if (
                str(source.get("method") or "GET").upper() not in {"GET", "HEAD"}
                or not _request_precedes(source, target)
            ):
                continue
            if any(
                str(source.get(key) or "")
                and str(target.get(key) or "")
                and str(source.get(key)) != str(target.get(key))
                for key in ("page_id", "frame_id")
            ):
                continue
            response = source.get("response_json")
            if not isinstance(response, dict):
                continue
            payload = response
            prefix = ""
            for envelope in ("data", "result"):
                if isinstance(response.get(envelope), dict):
                    payload = response[envelope]
                    prefix = f"{envelope}."
                    break
            matches: list[dict[str, Any]] = []
            for path, _tokens, _scalar, raw in _leaf_paths(payload):
                if path not in target_values or isinstance(raw, (dict, list, bool)):
                    continue
                target_raw = target_values[path]
                if isinstance(target_raw, (dict, list, bool)):
                    continue
                source_empty = raw in (None, "")
                target_empty = target_raw in (None, "")
                equal = (
                    source_empty and target_empty
                ) or (
                    not source_empty
                    and not target_empty
                    and (
                        _recorded_scalar_values_match(raw, target_raw)
                        or _composite_values_match(raw, target_raw)
                    )
                )
                matches.append({
                    "source_path": f"{prefix}{path}" if path else prefix.rstrip("."),
                    "target_path": path,
                    "source_value": copy.deepcopy(raw),
                    "target_value": copy.deepcopy(target_raw),
                    "value_overridden": not equal and not (source_empty and target_empty),
                    "empty_projection": source_empty and target_empty,
                })
            identity_paths = [
                item["target_path"] for item in matches
                if re.sub(
                    r"[^a-z0-9]+", "",
                    item["target_path"].split(".")[-1].casefold(),
                ) in identity_keys
            ]
            if len(matches) < 3 or not identity_paths:
                continue
            candidates_by_target.setdefault(target_id, []).append({
                "source_request_id": str(source.get("request_id") or ""),
                "target_request_id": target_id,
                "matches": matches,
                "identity_paths": identity_paths,
                "source_order": _request_order_value(source),
            })
    selected: list[dict[str, Any]] = []
    for candidates in candidates_by_target.values():
        selected.append(max(
            candidates,
            key=lambda item: (len(item["matches"]), item["source_order"]),
        ))
    return selected


_PENDING_FLOW_SPEC_HELPERS = ('_composite_values_match', '_recorded_scalar_values_match',)


def _bind_flow_spec_helpers() -> None:
    import sys
    _flow_spec = sys.modules.get("dano.execution.page.flow_spec")
    if _flow_spec is None or not hasattr(_flow_spec, "to_flow_spec"):
        return
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
