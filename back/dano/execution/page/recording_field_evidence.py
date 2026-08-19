"""Deterministically bind DOM control evidence to captured request fields."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from dano.execution.page.request_capture import (
    _JSONSTR,
    parse_recorded_request_body,
)


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
    # SPA dialogs often keep the list route while the write request records the
    # form path.  Same page/frame is enough once the control is on a dialog.
    if _evidence_surface(evidence) == "dialog":
        return True
    request_route = _route_identity(request)
    evidence_route = _route_identity(evidence)
    return not (request_route and evidence_route and request_route != evidence_route)


def _request_content_type(request: dict[str, Any]) -> str:
    headers = request.get("headers") if isinstance(request.get("headers"), dict) else {}
    return str(
        request.get("content_type")
        or headers.get("content-type")
        or headers.get("Content-Type")
        or ""
    )


def _parse_body(request: dict[str, Any]) -> Any:
    parsed = parse_recorded_request_body(request.get("post_data"), _request_content_type(request))
    return parsed["value"]


def _parsed_request_body(request: dict[str, Any]) -> dict[str, Any]:
    return parse_recorded_request_body(request.get("post_data"), _request_content_type(request))


def _leaves(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        if set(value) == {_JSONSTR}:
            return _leaves(value[_JSONSTR], prefix)
        return [
            path
            for key, child in value.items()
            for path in _leaves(child, prefix if key == _JSONSTR else (f"{prefix}.{key}" if prefix else str(key)))
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
    parsed_body = _parsed_request_body(request)
    fields = [f"query.{key}" for key in query]
    fields.extend(f"body.{path}" for path in parsed_body.get("field_paths") or [])
    return list(dict.fromkeys(fields))


def _value_leaves(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        if set(value) == {_JSONSTR}:
            return _value_leaves(value[_JSONSTR], prefix)
        return [
            item
            for key, child in value.items()
            for item in _value_leaves(
                child,
                prefix if key == _JSONSTR else (f"{prefix}.{key}" if prefix else str(key)),
            )
        ]
    if isinstance(value, list):
        return [
            item
            for index, child in enumerate(value)
            for item in _value_leaves(child, f"{prefix}[{index}]")
        ]
    return [(prefix, value)] if prefix else []


def _request_field_values(request: dict[str, Any]) -> list[tuple[str, Any]]:
    parsed = urlparse(str(request.get("url") or request.get("path") or ""))
    query = request.get("query")
    if not isinstance(query, dict):
        query = parse_qs(parsed.query, keep_blank_values=True)
    values: list[tuple[str, Any]] = []
    for key, value in query.items():
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if isinstance(value, list):
            values.extend((f"query.{key}[{index}]", item) for index, item in enumerate(value))
        else:
            values.append((f"query.{key}", value))
    parsed_body = _parsed_request_body(request)
    values.extend((f"body.{path}", value) for path, value in _value_leaves(parsed_body.get("value")))
    for item in parsed_body.get("file_fields") or []:
        name = str(item.get("name") or "")
        if name:
            values.append((f"body.{name}", item.get("filename") or ""))
    return values


def _as_comparable_number(value: Any) -> float | None:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


_AUDIT_WIRE_LEAVES = {
    "createtime", "updatetime", "createdat", "updatedat",
    "creator", "updater", "modifier", "createby", "updateby",
    "creatorname", "updatername", "createdby", "updatedby",
}
_PAGINATION_WIRE_LEAVES = {
    "pageno", "page", "pagesize", "size", "offset", "limit",
    "current", "pagenum", "perpage", "pageindex",
}


def _wire_leaf_token(wire_path: str) -> str:
    leaf = re.sub(r"\[\d+\]$", "", str(wire_path or "").rsplit(".", 1)[-1])
    return re.sub(r"[^a-z0-9]+", "", leaf.casefold())


def _looks_pagination_wire_path(wire_path: str) -> bool:
    return _wire_leaf_token(wire_path) in _PAGINATION_WIRE_LEAVES


def _looks_audit_wire_path(wire_path: str) -> bool:
    leaf = _wire_leaf_token(wire_path)
    return leaf in _AUDIT_WIRE_LEAVES or leaf.endswith(
        ("createtime", "updatetime", "createdat", "updatedat")
    )


def _indexed_wire_path(wire_path: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"(.+)\[(\d+)\]$", str(wire_path or ""))
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def _control_kind(evidence: dict[str, Any]) -> str:
    return str(evidence.get("control_kind") or evidence.get("input_type") or "").strip().lower()


def _looks_temporal_control(evidence: dict[str, Any]) -> bool:
    if _control_kind(evidence) in {"date", "datetime", "time"}:
        return True
    label = str(evidence.get("label") or evidence.get("field") or "")
    return bool(re.search(r"日期|时间|date|time", label, re.I))


def _looks_range_start_label(evidence: dict[str, Any]) -> bool:
    text = str(evidence.get("label") or evidence.get("field") or "")
    return bool(re.search(
        r"开始|起始|起日|startdate|starttime|\bstart\b|\bfrom\b|\bbegin\b|\bsince\b",
        text,
        re.I,
    ))


def _looks_range_end_label(evidence: dict[str, Any]) -> bool:
    text = str(evidence.get("label") or evidence.get("field") or "")
    return bool(re.search(
        r"结束|截止|止日|enddate|endtime|\bend\b|\buntil\b|\btill\b",
        text,
        re.I,
    ))


def _time_of_day(value: Any) -> str | None:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    match = re.search(r"(\d{2}):(\d{2})(?::(\d{2}))?", text)
    if match:
        return f"{match.group(1)}:{match.group(2)}:{match.group(3) or '00'}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "00:00:00"
    if text.isdigit() and len(text) in {10, 13}:
        seconds = int(text) / (1000 if len(text) == 13 else 1)
        try:
            for offset in (0, 8):
                observed = datetime.fromtimestamp(seconds + offset * 3600, tz=timezone.utc)
                if observed.hour == observed.minute == observed.second == 0:
                    return "00:00:00"
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _is_start_of_day(value: Any) -> bool:
    return _time_of_day(value) == "00:00:00"


def _is_end_of_day(value: Any) -> bool:
    return _time_of_day(value) in {"23:59:59", "23:59:00"}


def _candidate_recorded_value(
    item: dict[str, Any],
    request_by_id: dict[str, dict[str, Any]],
) -> Any:
    if "recorded_value" in item:
        return item.get("recorded_value")
    request = request_by_id.get(str(item.get("request_id") or ""), {})
    path = str(item.get("wire_path") or "")
    for wire_path, value in _request_field_values(request):
        if wire_path == path:
            item["recorded_value"] = value
            return value
    return None


def _narrow_temporal_candidates(
    evidence: dict[str, Any],
    candidates: list[dict[str, Any]],
    request_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Split same-day date ranges and keep business dates off audit stamps.

    A date-only control value commonly matches both ``foo[0]=YYYY-MM-DD 00:00:00``
    and ``foo[1]=YYYY-MM-DD 23:59:59``, and a dialog date can match both the
    business ``orderTime`` epoch and ``createTime`` on the same calendar day.
    """
    if len(candidates) <= 1 or not _looks_temporal_control(evidence):
        return candidates
    non_audit = [
        item for item in candidates
        if not _looks_audit_wire_path(str(item.get("wire_path") or ""))
    ]
    if len(non_audit) == 1:
        return non_audit
    if non_audit:
        candidates = non_audit
    indexed: list[tuple[dict[str, Any], str, int]] = []
    for item in candidates:
        parsed = _indexed_wire_path(str(item.get("wire_path") or ""))
        if parsed is not None:
            indexed.append((item, parsed[0], parsed[1]))
    stems = {stem for _item, stem, _index in indexed}
    indexes = {index for _item, _stem, index in indexed}
    if len(stems) == 1 and {0, 1}.issubset(indexes):
        start = next(item for item, _stem, index in indexed if index == 0)
        end = next(item for item, _stem, index in indexed if index == 1)
        recorded = evidence.get("value")
        if _looks_range_end_label(evidence) or _is_end_of_day(recorded):
            return [end]
        return [start]
    if _control_kind(evidence) == "date":
        midnight = [
            item for item in candidates
            if _is_start_of_day(_candidate_recorded_value(item, request_by_id))
        ]
        if len(midnight) == 1:
            return midnight
    return candidates


def _is_distinctive_recorded_value(value: Any) -> bool:
    """Values that are safe to bind without action/timing, because they are rare."""
    text = str(value if value is not None else "").strip()
    if not text:
        return False
    number = _as_comparable_number(text)
    if number is not None:
        return abs(number) not in {0.0, 1.0}
    return text.casefold() not in {"true", "false", "yes", "no", "null", "none"} and len(text) >= 4


def _same_recorded_value(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return False
    if isinstance(left, bool) or isinstance(right, bool):
        def as_bool(value: Any) -> bool | None:
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.strip().casefold() in {"true", "false"}:
                return value.strip().casefold() == "true"
            return None

        left_bool = as_bool(left)
        right_bool = as_bool(right)
        return left_bool is not None and left_bool == right_bool
    left_text = str(left).strip()
    right_text = str(right).strip()
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    left_number = _as_comparable_number(left_text)
    right_number = _as_comparable_number(right_text)
    if left_number is not None and right_number is not None:
        return abs(left_number - right_number) <= 1e-9

    def date_keys(value: str) -> set[str]:
        matches = {
            f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            for year, month, day in re.findall(
                r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value,
            )
        }
        if matches or not value.isdigit() or len(value) not in {10, 13}:
            return matches
        seconds = int(value) / (1000 if len(value) == 13 else 1)
        try:
            # Browser controls expose local calendar dates while JSON bodies
            # often serialize an epoch.  Keep both UTC and UTC+8 dates; the
            # exact action/scope/uniqueness guards still decide identity.
            return {
                datetime.fromtimestamp(seconds + offset * 3600, tz=timezone.utc).strftime("%Y-%m-%d")
                for offset in (0, 8)
            }
        except (OverflowError, OSError, ValueError):
            return set()

    left_dates = date_keys(left_text)
    right_dates = date_keys(right_text)
    return bool(left_dates and right_dates and left_dates & right_dates)


def _enrich_enum_aliases(
    evidence: dict[str, Any], page_enum_options: dict[str, Any] | None,
) -> None:
    """Merge an exact same-control enum identity into DOM evidence."""
    label = _normalize_identifier(evidence.get("label") or evidence.get("field"))
    # ``field`` is also used by the recorder for the visible form label.  It is
    # not a structural wire alias: treating a localized label as one made us
    # skip the exact ``type`` alias already captured with the page dictionary.
    # Only identities coming from DOM/request attributes may short-circuit the
    # enum enrichment.
    if not label or _structural_evidence_aliases(evidence):
        return
    matches: list[dict[str, Any]] = []
    for name, raw in (page_enum_options or {}).items():
        if not isinstance(raw, dict) or not _same_scope(raw, evidence):
            continue
        enum_labels = {
            _normalize_identifier(name),
            _normalize_identifier(raw.get("field_key")),
            _normalize_identifier(raw.get("label")),
        }
        if label not in enum_labels:
            continue
        evidence_kind = str(evidence.get("control_kind") or "").casefold()
        enum_kind = str(raw.get("control_kind") or "").casefold()
        if evidence_kind and enum_kind and evidence_kind != enum_kind:
            continue
        matches.append(raw)
    if len(matches) != 1:
        return
    aliases = [
        str(value) for value in [
            *(matches[0].get("field_aliases") or []),
            matches[0].get("path"), matches[0].get("key"),
        ] if str(value or "").strip()
    ]
    if aliases:
        evidence["field_aliases"] = list(dict.fromkeys(aliases))
        evidence["identity_sources"] = [
            *list(evidence.get("identity_sources") or []), "page_enum_options",
        ]


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


def _structural_evidence_aliases(evidence: dict[str, Any]) -> set[str]:
    raw = [
        *(evidence.get("field_aliases") or []),
        evidence.get("path"),
        evidence.get("key"),
    ]
    return {normalized for value in raw if (normalized := _normalize_identifier(value))}


def _field_match_score(aliases: set[str], wire_path: str) -> int:
    relative = wire_path.split(".", 1)[1] if "." in wire_path else wire_path
    full = _normalize_identifier(relative)
    if full and full in aliases:
        return 2
    leaf = full.rsplit(".", 1)[-1]
    return 1 if leaf and leaf in aliases else 0


def _is_array_row_only_ambiguity(items: list[tuple[str, str]]) -> bool:
    """Return True when every candidate path is the same field in different array rows.

    e.g. [body.items[0].productBarCode, body.items[1].productBarCode] differ only in
    the numeric array index — they represent the same structural field repeated per row.
    Collapsing them to the first row is safe because the DOM evidence describes one
    interaction, not N simultaneous interactions.

    ``items`` is a list of (request_id, wire_path) tuples, matching the keys
    of the ``unique`` / ``structural_unique`` dicts.
    """
    if len(items) < 2:
        return False
    paths = [item[1] for item in items]  # wire_path is the second element
    stripped = {re.sub(r"\[\d+\]", "[*]", p) for p in paths}
    return len(stripped) == 1


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


def _request_role_name(request: dict[str, Any]) -> str:
    role = request.get("role") or request.get("request_role")
    nested = request.get("_request_role")
    if isinstance(nested, dict):
        role = role or nested.get("role")
    return str(role or "")


def _request_binding_priority(request: dict[str, Any]) -> int:
    """Prefer the business request caused by a form action over background reads."""
    role = _request_role_name(request)
    if role in {"business_write", "submit_anchor"}:
        return 3
    if role == "business_get":
        return 2
    if role in {"read_context", "read_option", "option", "option_source"}:
        return 1
    return 2 if str(request.get("method") or "GET").upper() in {"POST", "PUT", "PATCH", "DELETE"} else 1


def _wire_container(wire_path: str) -> str:
    text = str(wire_path or "")
    if text.startswith("query."):
        return "query"
    if text.startswith("body."):
        return "body"
    return ""


def _evidence_container_hint(evidence: dict[str, Any]) -> str:
    """Honor an already recorded query./body. path before request-role priority."""
    for key in ("wire_path", "path"):
        text = str(evidence.get(key) or "").strip().removeprefix("request.")
        container = _wire_container(text)
        if container:
            return container
    return ""


def _evidence_surface(evidence: dict[str, Any]) -> str:
    if evidence.get("in_dialog") is True:
        return "dialog"
    if evidence.get("in_dialog") is False:
        return "page"
    surface = str(evidence.get("surface") or "").strip().lower()
    if surface in {"dialog", "modal", "drawer"}:
        return "dialog"
    if surface in {"page", "list", "filter"}:
        return "page"
    return ""


def _narrow_candidates_by_surface(
    evidence: dict[str, Any],
    candidates: list[dict[str, Any]],
    request_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep list filters on the query request and dialog fields on the write.

    The same leaf (customerId, remark) commonly exists on both the search GET
    and the later edit PUT. Request-role priority alone steals every list-page
    label onto the write and leaves the query contract unnamed.
    """
    if not candidates:
        return candidates
    hint = _evidence_container_hint(evidence)
    if hint:
        narrowed = [
            item for item in candidates
            if _wire_container(str(item.get("wire_path") or "")) == hint
        ]
        if narrowed:
            return _prefer_one_surface_candidate(
                _narrow_temporal_candidates(evidence, narrowed, request_by_id),
            )
    surface = _evidence_surface(evidence)
    if surface == "dialog":
        narrowed = [
            item for item in candidates
            if _wire_container(str(item.get("wire_path") or "")) == "body"
            or _request_binding_priority(
                request_by_id.get(str(item.get("request_id") or ""), {}),
            ) >= 3
        ]
        if narrowed:
            return _prefer_one_surface_candidate(
                _narrow_temporal_candidates(evidence, narrowed, request_by_id),
            )
    if surface == "page":
        query_candidates = [
            item for item in candidates
            if _wire_container(str(item.get("wire_path") or "")) == "query"
        ]
        get_candidates = [
            item for item in query_candidates
            if str(
                (request_by_id.get(str(item.get("request_id") or ""), {}) or {}).get("method")
                or "GET"
            ).upper() in {"GET", "HEAD", "OPTIONS"}
        ]
        if get_candidates:
            return _prefer_one_surface_candidate(
                _narrow_temporal_candidates(evidence, get_candidates, request_by_id),
            )
        if query_candidates:
            return _prefer_one_surface_candidate(
                _narrow_temporal_candidates(evidence, query_candidates, request_by_id),
            )
    return _prefer_one_surface_candidate(
        _narrow_temporal_candidates(evidence, candidates, request_by_id),
    )


def _wire_leaf(wire_path: str) -> str:
    return re.sub(r"\[\d+\]", "", str(wire_path or "").rsplit(".", 1)[-1]).casefold()


def _prefer_one_surface_candidate(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """After surface narrowing, keep one request when the leaf is the same."""
    if len(candidates) <= 1:
        return candidates
    keys = {(item.get("request_id"), item.get("wire_path")) for item in candidates}
    if len(keys) == 1:
        return candidates
    leaves = {_wire_leaf(str(item.get("wire_path") or "")) for item in candidates}
    if len(leaves) != 1:
        return candidates
    causal = [item for item in candidates if item.get("causal_match")]
    pool = causal or candidates
    timed = [item for item in pool if item.get("time_delta") is not None]
    if timed:
        nearest = min(float(item["time_delta"]) for item in timed)
        timed = [item for item in timed if float(item["time_delta"]) == nearest]
        if len({(item.get("request_id"), item.get("wire_path")) for item in timed}) == 1:
            return timed
        pool = timed
    keys = {(item.get("request_id"), item.get("wire_path")) for item in pool}
    if len(keys) > 1:
        return pool
    return pool[-1:]


def _timestamp(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number / 1000.0 if abs(number) >= 10**11 else number


_DEBOUNCE_SLACK_S = 2.0


def _temporal_alignment(
    request_time: float | None,
    evidence_time: float | None,
) -> tuple[bool, float | None]:
    """Treat a late debounced fill as the same burst as the request it produced."""
    if request_time is None or evidence_time is None:
        return False, None
    delta = request_time - evidence_time
    if delta >= 0:
        return True, delta
    if -_DEBOUNCE_SLACK_S <= delta:
        return True, 0.0
    return False, None


def _evidence_id(evidence: dict[str, Any], index: int = 0) -> str:
    existing = str(evidence.get("evidence_id") or "")
    if existing.startswith("field-evidence-"):
        return existing
    payload = {
        "page_id": evidence.get("page_id"),
        "frame_id": evidence.get("frame_id"),
        "route": _route_identity(evidence),
        "aliases": sorted(_evidence_aliases(evidence)),
        "label": evidence.get("label") or evidence.get("field"),
        "op": evidence.get("op"),
        "surface": evidence.get("surface") or _evidence_surface(evidence),
        "in_dialog": bool(evidence.get("in_dialog")),
    }
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return f"field-evidence-{digest}"


def bind_field_evidence(
    captured_requests: list[dict[str, Any]],
    page_events: list[dict[str, Any]] | None,
    field_evidence: list[dict[str, Any]] | None,
    page_enum_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return evidence annotated with an explicit bound/ambiguous/unbound result.

    Structural aliases are authoritative.  When a framework exposes only a
    human label, a recorded value may be used as a bounded fallback only when
    it resolves to one request field in the same page/frame/route and the
    request happened after the control interaction.  Ambiguous values remain
    unbound.
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
        _enrich_enum_aliases(evidence, page_enum_options)
        related_event = events_by_id.get(str(evidence.get("event_id") or ""))
        if related_event is None and evidence.get("action_id"):
            action_events = events_by_action.get(str(evidence.get("action_id") or ""), [])
            related_event = action_events[-1] if action_events else None
        if related_event is not None:
            for key in ("action_id", "transaction_id", "observed_at"):
                if evidence.get(key) in (None, "") and related_event.get(key) not in (None, ""):
                    evidence[key] = related_event[key]
        evidence["evidence_id"] = _evidence_id(evidence, index)
        structural_aliases = _structural_evidence_aliases(evidence)
        aliases = structural_aliases
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
                        temporal_match, time_delta = _temporal_alignment(request_time, evidence_time)
                        candidates.append({
                            "request_id": request_id,
                            "wire_path": wire_path,
                            "match_score": match_score,
                            "causal_match": _causal_match(request, evidence),
                            "temporal_match": temporal_match,
                            "time_delta": time_delta,
                            "has_request_causality": bool(
                                request.get("trigger_action_id") or request.get("trigger_transaction_id")
                            ),
                        })
        if (
            not candidates
            and not structural_aliases
            and evidence.get("value") not in (None, "")
        ):
            value_candidates: list[dict[str, Any]] = []
            for request in requests:
                if not _same_scope(request, evidence):
                    continue
                request_id = str(request.get("request_id") or request.get("id") or request.get("index") or "")
                if not request_id:
                    continue
                request_time = _timestamp(request.get("timestamp") or request.get("captured_at"))
                evidence_time = _timestamp(evidence.get("observed_at"))
                causal_match = _causal_match(request, evidence)
                temporal_match, time_delta = _temporal_alignment(request_time, evidence_time)
                if not causal_match and not temporal_match:
                    continue
                for wire_path, value in _request_field_values(request):
                    if _looks_pagination_wire_path(wire_path):
                        continue
                    if _same_recorded_value(evidence.get("value"), value):
                        value_candidates.append({
                            "recorded_value": value,
                            "request_id": request_id,
                            "wire_path": wire_path,
                            "match_score": 0,
                            "causal_match": causal_match,
                            "temporal_match": temporal_match,
                            "time_delta": 0.0 if time_delta is None else time_delta,
                            "has_request_causality": bool(
                                request.get("trigger_action_id") or request.get("trigger_transaction_id")
                            ),
                            "request_priority": _request_binding_priority(request),
                        })
            # A short value such as ``1`` often appears in paging/option reads
            # caused by the control interaction itself and later in the real
            # business submission. Prefer the business request before using
            # action timing; otherwise a textarea value can be bound to an
            # unrelated helper request's pageNo merely because both equal 1.
            request_by_id = {
                str(request.get("request_id") or request.get("id") or request.get("index") or ""): request
                for request in requests
            }
            preferred_candidates = _narrow_candidates_by_surface(
                evidence, value_candidates, request_by_id,
            )
            if preferred_candidates:
                priority = max(int(item["request_priority"]) for item in preferred_candidates)
                preferred_candidates = [
                    item for item in preferred_candidates
                    if int(item["request_priority"]) == priority
                ]
            exact_value_candidates = [
                item for item in preferred_candidates if item["causal_match"]
            ]
            aligned = exact_value_candidates
            if not aligned:
                aligned = [
                    item for item in preferred_candidates
                    if item["temporal_match"] and item["has_request_causality"]
                ]
                if aligned:
                    nearest = min(float(item["time_delta"]) for item in aligned)
                    aligned = [item for item in aligned if float(item["time_delta"]) == nearest]
            if len({(item["request_id"], item["wire_path"]) for item in aligned}) == 1:
                candidates = aligned
        if (
            not candidates
            and not structural_aliases
            and _is_distinctive_recorded_value(evidence.get("value"))
        ):
            distinctive: list[dict[str, Any]] = []
            for request in requests:
                if not _same_scope(request, evidence):
                    continue
                request_id = str(request.get("request_id") or request.get("id") or request.get("index") or "")
                if not request_id:
                    continue
                for wire_path, value in _request_field_values(request):
                    if _looks_pagination_wire_path(wire_path):
                        continue
                    if not _same_recorded_value(evidence.get("value"), value):
                        continue
                    distinctive.append({
                        "recorded_value": value,
                        "request_id": request_id,
                        "wire_path": wire_path,
                        "match_score": 0,
                        "causal_match": _causal_match(request, evidence),
                        "temporal_match": False,
                        "time_delta": None,
                        "has_request_causality": bool(
                            request.get("trigger_action_id") or request.get("trigger_transaction_id")
                        ),
                        "request_priority": _request_binding_priority(request),
                    })
            request_by_id = {
                str(request.get("request_id") or request.get("id") or request.get("index") or ""): request
                for request in requests
            }
            distinctive = _narrow_candidates_by_surface(evidence, distinctive, request_by_id)
            if len({(item["request_id"], item["wire_path"]) for item in distinctive}) == 1:
                candidates = distinctive
        if (
            not candidates
            and not structural_aliases
            and _looks_temporal_control(evidence)
            and _looks_range_end_label(evidence)
            and evidence.get("value") in (None, "")
        ):
            range_ends: list[dict[str, Any]] = []
            for request in requests:
                if not _same_scope(request, evidence):
                    continue
                request_id = str(request.get("request_id") or request.get("id") or request.get("index") or "")
                if not request_id:
                    continue
                grouped: dict[str, dict[int, str]] = {}
                for wire_path, value in _request_field_values(request):
                    parsed = _indexed_wire_path(wire_path)
                    if parsed is None or not (_is_start_of_day(value) or _is_end_of_day(value) or _looks_temporal_control(evidence)):
                        continue
                    grouped.setdefault(parsed[0], {})[parsed[1]] = wire_path
                for indexes in grouped.values():
                    if 0 not in indexes or 1 not in indexes:
                        continue
                    range_ends.append({
                        "request_id": request_id,
                        "wire_path": indexes[1],
                        "match_score": 0,
                        "causal_match": _causal_match(request, evidence),
                        "temporal_match": False,
                        "time_delta": None,
                        "has_request_causality": bool(
                            request.get("trigger_action_id") or request.get("trigger_transaction_id")
                        ),
                        "request_priority": _request_binding_priority(request),
                    })
            request_by_id = {
                str(request.get("request_id") or request.get("id") or request.get("index") or ""): request
                for request in requests
            }
            range_ends = _narrow_candidates_by_surface(evidence, range_ends, request_by_id)
            if len({(item["request_id"], item["wire_path"]) for item in range_ends}) == 1:
                candidates = range_ends
        request_by_id = {
            str(request.get("request_id") or request.get("id") or request.get("index") or ""): request
            for request in requests
        }
        if candidates:
            best_score = max(int(item["match_score"]) for item in candidates)
            candidates = [item for item in candidates if int(item["match_score"]) == best_score]
            # The same leaf often appears on a list query and the later write.
            # Prefer the form surface (list vs dialog, query. vs body.) before
            # falling back to write-over-query priority for unhinted snapshots.
            candidates = _narrow_candidates_by_surface(evidence, candidates, request_by_id)
            for item in candidates:
                item["request_priority"] = _request_binding_priority(
                    request_by_id.get(str(item.get("request_id") or ""), {}),
                )
            best_priority = max(int(item["request_priority"]) for item in candidates)
            preferred = [
                item for item in candidates
                if int(item["request_priority"]) == best_priority
            ]
            if len({(item["request_id"], item["wire_path"]) for item in preferred}) == 1:
                candidates = preferred
        exact_causal = [item for item in candidates if item["causal_match"]]
        ordered_causal = [
            item for item in candidates
            if item["temporal_match"]
            and item["has_request_causality"]
            and bool(evidence.get("action_id") or evidence.get("transaction_id"))
        ]
        if exact_causal:
            selected = exact_causal
            binding_method = (
                "exact_alias_same_transaction"
                if candidates[0]["match_score"]
                else "unique_value_same_transaction"
            )
        elif ordered_causal:
            nearest = min(float(item["time_delta"]) for item in ordered_causal)
            selected = [item for item in ordered_causal if float(item["time_delta"]) == nearest]
            binding_method = (
                "exact_alias_same_scope_causal_order"
                if aliases and candidates[0]["match_score"]
                else "unique_value_same_scope_causal_order"
            )
        elif len({(item["request_id"], item["wire_path"]) for item in candidates}) == 1:
            selected = candidates
            binding_method = "exact_alias_preferred_business_request"
        else:
            selected = []
            binding_method = ""
        unique = {
            (item["request_id"], item["wire_path"]): item
            for item in selected
        }
        # When every selected candidate represents the same field name repeated
        # across array rows (e.g. body.items[0].productBarCode vs [1].productBarCode),
        # treat it as a unique structural match and bind to the first row.  A
        # DOM fill event describes one interaction, not N simultaneous writes.
        if len(unique) != 1 and _is_array_row_only_ambiguity(list(unique)):
            first_key = min(unique)
            unique = {first_key: unique[first_key]}
            if not binding_method:
                binding_method = "array_row_leaf_match"
        structural_unique = {
            (item["request_id"], item["wire_path"]): item
            for item in candidates
        }
        # Same array-row collapse for structural_unique (used for ambiguity status)
        if len(structural_unique) != 1 and _is_array_row_only_ambiguity(list(structural_unique)):
            first_key = min(structural_unique)
            structural_unique = {first_key: structural_unique[first_key]}
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
                "binding_reason": (
                    "exact control alias, page/frame scope and request causality"
                    if aliases else
                    "unique recorded value in the exact action transaction and page/frame scope"
                ),
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
