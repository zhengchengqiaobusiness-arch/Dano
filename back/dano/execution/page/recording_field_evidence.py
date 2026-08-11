"""Deterministically bind DOM control evidence to captured request fields."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse


def _route_identity(item: dict[str, Any]) -> str:
    for key in ("trigger_page_context", "page_context"):
        context = item.get(key)
        if not isinstance(context, dict):
            continue
        path = str(context.get("path") or "").strip()
        if path:
            return path.rstrip("/") or "/"
        url = str(context.get("url") or "").strip()
        if url:
            return urlparse(url).path.rstrip("/") or "/"
    return ""


def _same_scope(request: dict[str, Any], evidence: dict[str, Any]) -> bool:
    for key in ("page_id", "frame_id"):
        left = str(request.get(key) or request.get("pageId" if key == "page_id" else "frameId") or "")
        right = str(evidence.get(key) or "")
        if left and right and left != right:
            return False
    request_route = _route_identity(request)
    evidence_route = _route_identity(evidence)
    return not (request_route and evidence_route and request_route != evidence_route)


def _parse_body(request: dict[str, Any]) -> Any:
    raw = request.get("post_data")
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        pairs = parse_qs(raw, keep_blank_values=True)
        return pairs or None


def _leaves(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        return [
            path
            for key, child in value.items()
            for path in _leaves(child, f"{prefix}.{key}" if prefix else str(key))
        ]
    if isinstance(value, list):
        if not value:
            return [prefix] if prefix else []
        return [
            path
            for index, child in enumerate(value)
            for path in _leaves(child, f"{prefix}[{index}]")
        ]
    return [prefix] if prefix else []


def _request_fields(request: dict[str, Any]) -> list[str]:
    parsed = urlparse(str(request.get("url") or request.get("path") or ""))
    query = request.get("query")
    if not isinstance(query, dict):
        query = parse_qs(parsed.query, keep_blank_values=True)
    # Query mappings already carry the wire key (including keys such as
    # ``createTime[0]``). parse_qs wraps scalar values in a list, which is a
    # parser representation and must not invent a second ``[0]`` path segment.
    fields = [f"query.{key}" for key in query]
    fields.extend(f"body.{path}" for path in _leaves(_parse_body(request)))
    return fields


def _normalize_identifier(value: Any) -> str:
    text = str(value or "").strip()
    if ":" in text and text.split(":", 1)[0].casefold() in {
        "name", "id", "prop", "data-prop", "data-field", "data-name", "data-key", "data-path",
    }:
        text = text.split(":", 1)[1]
    text = text.removeprefix("request.").removeprefix("body.").removeprefix("query.")
    text = re.sub(r"\[(\d+)\]", r".\1", text)
    return re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff.]+", "", text).casefold().strip(".")


def _evidence_aliases(evidence: dict[str, Any]) -> set[str]:
    raw = [
        *(evidence.get("field_aliases") or []),
        evidence.get("path"),
        evidence.get("key"),
        evidence.get("field"),
    ]
    return {normalized for value in raw if (normalized := _normalize_identifier(value))}


def _field_aliases(wire_path: str) -> set[str]:
    relative = wire_path.split(".", 1)[1] if "." in wire_path else wire_path
    normalized = _normalize_identifier(relative)
    leaf = normalized.rsplit(".", 1)[-1]
    return {value for value in (normalized, leaf) if value}


def _field_match_score(aliases: set[str], wire_path: str) -> int:
    relative = wire_path.split(".", 1)[1] if "." in wire_path else wire_path
    full = _normalize_identifier(relative)
    if full and full in aliases:
        return 2
    leaf = full.rsplit(".", 1)[-1]
    return 1 if leaf and leaf in aliases else 0


def _causal_match(request: dict[str, Any], evidence: dict[str, Any]) -> bool:
    evidence_action = str(evidence.get("action_id") or "")
    evidence_transaction = str(evidence.get("transaction_id") or "")
    return bool(
        (evidence_action and evidence_action == str(request.get("trigger_action_id") or ""))
        or (
            evidence_transaction
            and evidence_transaction == str(request.get("trigger_transaction_id") or "")
        )
    )


def _timestamp(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number / 1000.0 if abs(number) >= 10**11 else number


def _evidence_id(evidence: dict[str, Any], index: int) -> str:
    existing = str(evidence.get("evidence_id") or evidence.get("event_id") or "")
    if existing:
        return existing
    payload = {
        "index": index,
        "page_id": evidence.get("page_id"),
        "frame_id": evidence.get("frame_id"),
        "route": _route_identity(evidence),
        "aliases": sorted(_evidence_aliases(evidence)),
        "label": evidence.get("label") or evidence.get("field"),
        "op": evidence.get("op"),
    }
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return f"field-evidence-{digest}"


def bind_field_evidence(
    captured_requests: list[dict[str, Any]],
    page_events: list[dict[str, Any]] | None,
    field_evidence: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return evidence annotated with an explicit bound/ambiguous/unbound result.

    Recorded values never participate in identity.  An exact structural alias
    must resolve to one field in the same page/frame/route, with an exact action
    or transaction match used only to disambiguate otherwise valid candidates.
    """
    requests = [item for item in (captured_requests or []) if isinstance(item, dict)]
    events = [item for item in (page_events or []) if isinstance(item, dict)]
    events_by_id = {
        str(item.get("event_id") or ""): item
        for item in events if str(item.get("event_id") or "")
    }
    events_by_action: dict[str, list[dict[str, Any]]] = {}
    for item in events:
        action_id = str(item.get("action_id") or "")
        if action_id:
            events_by_action.setdefault(action_id, []).append(item)
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(field_evidence or []):
        if not isinstance(raw, dict):
            continue
        evidence = deepcopy(raw)
        evidence["evidence_id"] = _evidence_id(evidence, index)
        related_event = events_by_id.get(str(evidence.get("event_id") or ""))
        if related_event is None and evidence.get("action_id"):
            action_events = events_by_action.get(str(evidence.get("action_id") or ""), [])
            related_event = action_events[-1] if action_events else None
        if related_event is not None:
            for key in ("action_id", "transaction_id", "observed_at"):
                if evidence.get(key) in (None, "") and related_event.get(key) not in (None, ""):
                    evidence[key] = related_event[key]
        aliases = _evidence_aliases(evidence)
        candidates: list[dict[str, Any]] = []
        if aliases:
            for request in requests:
                if not _same_scope(request, evidence):
                    continue
                request_id = str(request.get("request_id") or request.get("id") or request.get("index") or "")
                if not request_id:
                    continue
                for wire_path in _request_fields(request):
                    match_score = _field_match_score(aliases, wire_path)
                    if match_score:
                        request_time = _timestamp(request.get("timestamp") or request.get("captured_at"))
                        evidence_time = _timestamp(evidence.get("observed_at"))
                        temporal_match = bool(
                            request_time is not None
                            and evidence_time is not None
                            and request_time >= evidence_time
                        )
                        candidates.append({
                            "request_id": request_id,
                            "wire_path": wire_path,
                            "match_score": match_score,
                            "causal_match": _causal_match(request, evidence),
                            "temporal_match": temporal_match,
                            "time_delta": (
                                request_time - evidence_time
                                if temporal_match and request_time is not None and evidence_time is not None
                                else None
                            ),
                            "has_request_causality": bool(
                                request.get("trigger_action_id") or request.get("trigger_transaction_id")
                            ),
                        })
        if candidates:
            best_score = max(int(item["match_score"]) for item in candidates)
            candidates = [item for item in candidates if int(item["match_score"]) == best_score]
        exact_causal = [item for item in candidates if item["causal_match"]]
        ordered_causal = [
            item for item in candidates
            if item["temporal_match"]
            and item["has_request_causality"]
            and bool(evidence.get("action_id") or evidence.get("transaction_id"))
        ]
        if exact_causal:
            selected = exact_causal
            binding_method = "exact_alias_same_transaction"
        elif ordered_causal:
            nearest = min(float(item["time_delta"]) for item in ordered_causal)
            selected = [item for item in ordered_causal if float(item["time_delta"]) == nearest]
            binding_method = "exact_alias_same_scope_causal_order"
        else:
            selected = []
            binding_method = ""
        unique = {
            (item["request_id"], item["wire_path"]): item
            for item in selected
        }
        structural_unique = {
            (item["request_id"], item["wire_path"]): item
            for item in candidates
        }
        public_candidates = [
            {"request_id": request_id, "wire_path": wire_path}
            for request_id, wire_path in sorted(structural_unique)
        ]
        evidence["binding_candidates"] = public_candidates
        if "required" in evidence and isinstance(evidence.get("required"), bool):
            evidence["required_observed"] = bool(evidence["required"])
        evidence["editable"] = not bool(evidence.get("disabled") or evidence.get("read_only"))
        evidence["axes"] = [
            axis for axis, present in (
                ("name", bool(evidence.get("label") or evidence.get("field"))),
                ("required", isinstance(evidence.get("required_observed"), bool)),
                ("type", bool(evidence.get("control_kind") or evidence.get("input_type"))),
            ) if present
        ]
        evidence["event_refs"] = [
            value for key in ("event_id", "action_id", "transaction_id")
            if (value := str(evidence.get(key) or ""))
        ]
        if len(unique) == 1:
            request_id, wire_path = next(iter(unique))
            evidence.update({
                "binding_status": "bound",
                "request_id": request_id,
                "wire_path": wire_path,
                "binding_method": binding_method,
                "binding_reason": "exact control alias, page/frame scope and request causality",
            })
        else:
            evidence.pop("request_id", None)
            evidence.pop("wire_path", None)
            evidence["binding_status"] = "ambiguous" if len(structural_unique) > 1 else "unresolved" if structural_unique else "unbound"
            evidence["binding_reason"] = (
                "exact control alias matches multiple request fields"
                if len(structural_unique) > 1
                else "exact alias lacks aligned action/transaction and temporal evidence"
                if structural_unique
                else "no exact control alias matches a request field in scope"
            )
        results.append(evidence)
    return results
