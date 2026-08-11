"""Strong response-to-later-request value-link candidates."""
from __future__ import annotations

import json
import re
from collections import Counter
from urllib.parse import parse_qsl, unquote, urlparse


_BORING_LINK_VALUES = frozenset({
    "0", "1", "true", "false", "null", "none", "undefined", "success", "ok",
    "get", "post", "put", "patch", "delete", "application/json",
})
_SECRET_HINTS = ("authorization", "cookie", "token", "jwt", "secret", "password", "session", "credential")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9_-]{8,}$")


def _is_sensitive(path: str) -> bool:
    lowered = path.casefold()
    return any(hint in lowered for hint in _SECRET_HINTS)


def _is_timestamp(value: str) -> bool:
    if not value.isdigit() or len(value) not in {10, 13}:
        return False
    seconds = int(value) / (1000 if len(value) == 13 else 1)
    return 946684800 <= seconds <= 4102444800


def _is_strong_value(raw: object, path: str) -> bool:
    if raw is None or isinstance(raw, bool) or _is_sensitive(path):
        return False
    value = str(raw).strip()
    lowered = value.casefold()
    if not value or lowered in _BORING_LINK_VALUES or _is_timestamp(value):
        return False
    if value.isdigit():
        return len(value) >= 6
    if _UUID_RE.fullmatch(value):
        return True
    return bool(
        len(value) >= 8
        and _OPAQUE_RE.fullmatch(value)
        and any(ch.isalpha() for ch in value)
        and any(ch.isdigit() for ch in value)
    )


def _leaves(node: object, path: str = "") -> list[tuple[str, object]]:
    if isinstance(node, dict):
        return [
            item
            for key, value in node.items()
            for item in _leaves(value, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(node, list):
        return [
            item
            for index, value in enumerate(node)
            for item in _leaves(value, f"{path}[{index}]")
        ]
    return [(path, node)] if path else []


def _body(request: dict) -> object:
    raw = request.get("post_data")
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        pairs = parse_qsl(raw, keep_blank_values=True)
        return dict(pairs) if pairs else None


def _input_leaves(request: dict) -> list[tuple[str, object]]:
    parsed = urlparse(str(request.get("url") or ""))
    out: list[tuple[str, object]] = []
    for index, segment in enumerate(part for part in parsed.path.split("/") if part):
        out.append((f"url_path[{index}]", unquote(segment)))
    query = request.get("query")
    if not isinstance(query, dict):
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    out.extend((f"query.{path}", value) for path, value in _leaves(query))
    out.extend((f"body.{path}", value) for path, value in _leaves(_body(request)))
    out.extend((f"headers.{path}", value) for path, value in _leaves(request.get("headers") or {}))
    return out


def _sequence(request: dict, fallback: int) -> tuple[float, int]:
    for key in ("sequence", "index", "request_index", "timestamp"):
        value = request.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value), fallback
        if isinstance(value, str) and value.isdigit():
            return float(value), fallback
    return float(fallback), fallback


def _same_value(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if type(left) is type(right):
        return left == right
    if isinstance(left, (int, float, str)) and isinstance(right, (int, float, str)):
        left_text = str(left).strip()
        right_text = str(right).strip()
        return left_text == right_text and left_text.replace(".", "", 1).isdigit()
    return False


def discover_value_links(all_requests: list[dict]) -> list[dict]:
    """Return strong response-value links into any later request input."""
    ordered = sorted(
        ((request, position) for position, request in enumerate(all_requests or []) if isinstance(request, dict)),
        key=lambda item: _sequence(item[0], item[1]),
    )
    target_values: list[dict[str, list[tuple[str, object]]]] = []
    for target, _ in ordered:
        by_value: dict[str, list[tuple[str, object]]] = {}
        for target_path, target_raw in _input_leaves(target):
            if _is_strong_value(target_raw, target_path):
                by_value.setdefault(str(target_raw).strip(), []).append((target_path, target_raw))
        target_values.append(by_value)

    candidates: list[dict] = []
    for source_index, (source, _) in enumerate(ordered):
        source_id = str(source.get("request_id") or f"req_{source_index}")
        response = source.get("response_json")
        if response is None:
            continue
        source_values = [
            (path, raw, str(raw).strip())
            for path, raw in _leaves(response)
            if _is_strong_value(raw, f"response.{path}")
        ]
        for target_index in range(source_index + 1, len(ordered)):
            target, _ = ordered[target_index]
            target_id = str(target.get("request_id") or f"req_{target_index}")
            for source_path, source_raw, value in source_values:
                for target_path, target_raw in target_values[target_index].get(value, []):
                    if _same_value(source_raw, target_raw):
                        candidates.append({
                            "source_request_id": source_id,
                            "source_path": f"response.{source_path}",
                            "target_request_id": target_id,
                            "target_path": target_path,
                            "value_sample": value,
                        })
    counts = Counter(
        (item["source_request_id"], item["source_path"], item["target_request_id"], item["target_path"])
        for item in candidates
    )
    unique: dict[tuple, dict] = {}
    for item in candidates:
        key = (item["source_request_id"], item["source_path"], item["target_request_id"], item["target_path"])
        unique.setdefault(key, {**item, "occurrences": counts[key]})
    return list(unique.values())
