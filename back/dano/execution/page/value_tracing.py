"""Strong response-to-later-request value and structure candidates."""
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
    # Workflow engines commonly use colon-delimited compound IDs such as
    # ``processKey:version:uuid``. Each segment must still be opaque; allowing
    # arbitrary punctuation would revive free-text value collisions.
    opaque_parts = value.split(":")
    ordinary_opaque = bool(len(opaque_parts) == 1 and _OPAQUE_RE.fullmatch(value))
    compound_opaque = bool(
        len(opaque_parts) >= 3
        and all(re.fullmatch(r"[A-Za-z0-9_-]+", part or "") for part in opaque_parts)
        and any(ch.isalpha() for ch in opaque_parts[0])
        and any(_UUID_RE.fullmatch(part) or _OPAQUE_RE.fullmatch(part) for part in opaque_parts[1:])
    )
    opaque_shape = bool(
        len(value) >= 8
        and (ordinary_opaque or compound_opaque)
        and any(ch.isalpha() for ch in value)
        and any(ch.isdigit() for ch in value)
    )
    return opaque_shape


def is_strong_runtime_value(raw: object, path: str = "") -> bool:
    """Return whether an exact captured value is safe to treat as runtime data.

    This deliberately excludes ordinary business literals such as workflow
    keys, statuses and labels.  Equality with one of those values is not proof
    that a later request depends on the earlier response.
    """
    return _is_strong_value(raw, path or "response.value")


def _is_workflow_route_value(raw: object, source_path: str, target_path: str) -> bool:
    """Allow short stable workflow IDs only when both path names say ID/key."""
    if raw is None or isinstance(raw, bool):
        return False
    value = str(raw).strip()
    if len(value) < 4 or value.casefold() in _BORING_LINK_VALUES:
        return False
    source_leaf = re.sub(r"[^a-z0-9]+", "", source_path.casefold().split(".")[-1])
    target_leaf = re.sub(r"[^a-z0-9]+", "", target_path.casefold().split(".")[-1])
    return source_leaf in {"id", "key", "code"} and (
        target_leaf.endswith("id") or target_leaf.endswith("key") or target_leaf.endswith("code")
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
    # ``RequestFact.query`` stores every wire key as a value list. A one-item
    # list is transport bookkeeping, not an array-valued field; keep the
    # canonical target identity ``query.key`` instead of inventing ``[0]``.
    for key, value in query.items():
        if isinstance(value, list) and len(value) == 1:
            out.append((f"query.{key}", value[0]))
        else:
            out.extend((f"query.{path}", item) for path, item in _leaves({key: value}))
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


def _nested_nodes(node: object, path: str = "") -> list[tuple[str, object]]:
    """Return nested containers with stable dotted paths (excluding the root)."""
    out: list[tuple[str, object]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(value, (dict, list)):
                out.append((child, value))
                out.extend(_nested_nodes(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = f"{path}[{index}]" if path else f"[{index}]"
            if isinstance(value, (dict, list)):
                out.append((child, value))
                out.extend(_nested_nodes(value, child))
    return out


def _list_collections(node: object, path: str = "") -> list[tuple[str, list]]:
    """Collect response row arrays without expanding every row into the index."""
    out: list[tuple[str, list]] = []
    if not isinstance(node, dict):
        return out
    for key, value in node.items():
        child = f"{path}.{key}" if path else str(key)
        if isinstance(value, list):
            if value and all(isinstance(row, dict) for row in value):
                out.append((child, value))
            continue
        if isinstance(value, dict):
            out.extend(_list_collections(value, child))
    return out


def _field_rank(field: str, *, label: bool) -> tuple[int, str]:
    normalized = re.sub(r"[^a-z0-9]+", "", field.casefold())
    hints = (
        ("name", "label", "title", "caption", "text", "description")
        if label else
        ("id", "key", "code", "value")
    )
    try:
        return hints.index(normalized), normalized
    except ValueError:
        return len(hints), normalized


def discover_response_key_maps(all_requests: list[dict]) -> list[dict]:
    """Find exact ``response rows -> later request object keys`` contracts.

    The match is deliberately structural: every recorded target key must equal
    one unique key from one upstream response collection, in the same order.
    It is therefore a candidate for model/executor verification, never an
    automatically confirmed dependency.
    """
    ordered = sorted(
        ((request, position) for position, request in enumerate(all_requests or []) if isinstance(request, dict)),
        key=lambda item: _sequence(item[0], item[1]),
    )
    candidates: list[dict] = []
    for source_index, (source, _) in enumerate(ordered):
        response = source.get("response_json")
        if response is None:
            continue
        collections = _list_collections(response)
        if not collections:
            continue
        source_id = str(source.get("request_id") or f"req_{source_index}")
        for collection_path, rows in collections:
            common_fields = set(rows[0])
            for row in rows[1:]:
                common_fields.intersection_update(row)
            scalar_fields = [
                field for field in common_fields
                if not _is_sensitive(field)
                and all(row.get(field) not in (None, "") and not isinstance(row.get(field), (dict, list, bool)) for row in rows)
            ]
            key_fields = sorted(scalar_fields, key=lambda field: _field_rank(field, label=False))
            label_fields = sorted(
                [
                    field for field in scalar_fields
                    if all(isinstance(row.get(field), str) and str(row.get(field)).strip() for row in rows)
                ],
                key=lambda field: _field_rank(field, label=True),
            )
            for target_index in range(source_index + 1, len(ordered)):
                target, _ = ordered[target_index]
                target_id = str(target.get("request_id") or f"req_{target_index}")
                body = _body(target)
                for container_path, container in _nested_nodes(body):
                    if not isinstance(container, dict) or not container:
                        continue
                    target_keys = [str(key) for key in container]
                    for key_field in key_fields:
                        source_keys = [str(row[key_field]) for row in rows]
                        if len(set(source_keys)) != len(source_keys):
                            continue
                        matched_positions = [
                            source_keys.index(key) for key in target_keys if key in source_keys
                        ]
                        if (
                            len(matched_positions) != len(target_keys)
                            or matched_positions != sorted(matched_positions)
                        ):
                            continue
                        label_field = next((
                            field for field in label_fields
                            if field != key_field
                            and len({str(row[field]) for row in rows}) == len(rows)
                        ), "")
                        if not label_field:
                            continue
                        candidates.append({
                            "kind": "response_key_map",
                            "source_request_id": source_id,
                            "source_collection_path": collection_path,
                            "source_key_path": key_field,
                            "source_label_path": label_field,
                            "target_request_id": target_id,
                            "target_container_path": f"body.{container_path}",
                            "recorded_key_count": len(target_keys),
                            "confidence": 0.99,
                        })
                        break
    unique: dict[tuple[str, ...], dict] = {}
    for item in candidates:
        signature = tuple(str(item.get(key) or "") for key in (
            "source_request_id", "source_collection_path", "source_key_path",
            "source_label_path", "target_request_id", "target_container_path",
        ))
        unique.setdefault(signature, item)
    return list(unique.values())


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


def discover_workflow_value_links(all_requests: list[dict]) -> list[dict]:
    """Find strong links plus short ID/key workflow routing links."""
    candidates = list(discover_value_links(all_requests))
    ordered = sorted(
        ((request, position) for position, request in enumerate(all_requests or []) if isinstance(request, dict)),
        key=lambda item: _sequence(item[0], item[1]),
    )
    input_leaves = [_input_leaves(request) for request, _ in ordered]
    for source_index, (source, _) in enumerate(ordered):
        response = source.get("response_json")
        if response is None:
            continue
        source_id = str(source.get("request_id") or f"req_{source_index}")
        for source_path, source_raw in _leaves(response):
            for target_index in range(source_index + 1, len(ordered)):
                target, _ = ordered[target_index]
                target_id = str(target.get("request_id") or f"req_{target_index}")
                for target_path, target_raw in input_leaves[target_index]:
                    if (
                        _same_value(source_raw, target_raw)
                        and _is_workflow_route_value(source_raw, source_path, target_path)
                    ):
                        candidates.append({
                            "source_request_id": source_id,
                            "source_path": f"response.{source_path}",
                            "target_request_id": target_id,
                            "target_path": target_path,
                            "value_sample": str(source_raw)[:128],
                            "occurrences": 1,
                        })
        # Scalar lookup endpoints (stock counts, balances, quotas, sequence
        # numbers) legitimately return a short number in a generic `data`
        # leaf.  Bind it only when route semantics and one later wire leaf
        # agree inside the same page/causal scope.
        scalar_leaves = [
            (path, raw) for path, raw in _leaves(response)
            if raw not in (None, "") and not isinstance(raw, (dict, list, bool))
        ]
        payload_scalar_leaves = [
            (path, raw) for path, raw in scalar_leaves
            if str(path or "").casefold()
            not in {"code", "msg", "message", "status", "success"}
        ]
        if len(payload_scalar_leaves) == 1:
            # Common API envelopes add transport metadata beside one business
            # scalar (for example ``{code, msg, data: stockCount}``). Those
            # metadata leaves must not stop the unique-scalar workflow link.
            scalar_leaves = payload_scalar_leaves
        if len(scalar_leaves) == 1:
            source_path, source_raw = scalar_leaves[0]

            def semantic_tokens(value: str) -> set[str]:
                spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
                return {
                    token for token in re.findall(r"[a-z0-9]+", spaced.casefold())
                    if token not in {"api", "v1", "v2", "get", "post", "put", "query", "body", "response"}
                }

            route_tokens = semantic_tokens(str(source.get("url") or source.get("path") or ""))
            scalar_matches: list[dict] = []
            for target_index in range(source_index + 1, len(ordered)):
                target, _ = ordered[target_index]
                same_page = bool(
                    str(source.get("page_id") or "")
                    and str(source.get("page_id") or "") == str(target.get("page_id") or "")
                    and str(source.get("frame_id") or "") == str(target.get("frame_id") or "")
                )
                same_cause = bool(
                    (
                        source.get("trigger_transaction_id")
                        and source.get("trigger_transaction_id") == target.get("trigger_transaction_id")
                    )
                    or (
                        source.get("trigger_action_id")
                        and source.get("trigger_action_id") == target.get("trigger_action_id")
                    )
                )
                if not (same_page or same_cause):
                    continue
                matching_inputs = [
                    target_path for target_path, target_raw in input_leaves[target_index]
                    if _same_value(source_raw, target_raw)
                    and len(route_tokens & semantic_tokens(target_path)) >= 2
                ]
                if len(matching_inputs) == 1:
                    scalar_matches.append({
                        "source_request_id": source_id,
                        "source_path": f"response.{source_path}",
                        "target_request_id": str(target.get("request_id") or f"req_{target_index}"),
                        "target_path": matching_inputs[0],
                        "value_sample": str(source_raw)[:128],
                        "occurrences": 1,
                        "evidence_kind": "unique_scalar_semantic_projection",
                        "target_order": target_index,
                    })
            if scalar_matches:
                candidates.append(min(scalar_matches, key=lambda item: int(item["target_order"])))
    unique: dict[tuple[str, str, str, str], dict] = {}
    for item in candidates:
        item.pop("target_order", None)
        signature = tuple(str(item.get(key) or "") for key in (
            "source_request_id", "source_path", "target_request_id", "target_path",
        ))
        unique.setdefault(signature, item)
    return list(unique.values())
