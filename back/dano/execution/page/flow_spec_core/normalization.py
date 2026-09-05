"""Pure path/type/schema normalization without business inference."""
from __future__ import annotations

from typing import Any
import re
from dano.execution.page.request_capture import (
    looks_internal_param_name,
)


def _infer_type_from_value(value: Any) -> str:
    if value in (None, ""):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    text = str(value)
    if text.lower() in ("true", "false"):
        return "boolean"
    if re.match(r"^\d{4}-\d{2}-\d{2}T", text):
        return "datetime"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return "date"
    try:
        float(text)
        return "number"
    except (ValueError, TypeError):
        pass
    return "string"


def _norm_field_name(key: str, path: str = "") -> str:
    return re.sub(r"[^a-z0-9]+", "", f"{key}.{path}".lower())


def _sample_value_set(samples: dict | None) -> set[str]:
    return {str(v) for v in (samples or {}).values() if v not in (None, "")}


def _strip_body_prefix(path: str) -> str:
    return path[len("body."):] if path.startswith("body.") else path


_FLOW_PATH_MISSING = object()


def _flow_path_tokens(path) -> list:
    if isinstance(path, (list, tuple)):
        return list(path)
    out: list = []
    for seg in str(path or "").split("."):
        bits = seg.split("[")
        if bits[0]:
            out.append(bits[0])
        for idx in bits[1:]:
            try:
                out.append(int(idx.rstrip("]")))
            except ValueError:
                out.append(idx.rstrip("]"))
    return out


def _flow_path_lookup(node, path):
    cur = node
    for key in _flow_path_tokens(path):
        try:
            cur = cur[key]
        except Exception:  # noqa: BLE001
            return _FLOW_PATH_MISSING
    return cur


def _flow_path_set(node, path, value) -> bool:  # noqa: ANN001
    tokens = _flow_path_tokens(path)
    if not tokens:
        return False
    current = node
    for token in tokens[:-1]:
        try:
            current = current[token]
        except Exception:  # noqa: BLE001
            return False
    try:
        current[tokens[-1]] = value
    except Exception:  # noqa: BLE001
        return False
    return True


def _flow_path_assign(node, path, value) -> bool:  # noqa: ANN001
    """Set a path, creating missing dict/list parents. Unlike `_flow_path_set`."""
    tokens = _flow_path_tokens(path)
    if not tokens:
        return False
    if isinstance(tokens[0], int):
        if not isinstance(node, list):
            return False
    elif not isinstance(node, dict):
        return False
    current = node
    for index, token in enumerate(tokens[:-1]):
        nxt = tokens[index + 1]
        want_list = isinstance(nxt, int)
        placeholder: list | dict = [] if want_list else {}
        if isinstance(token, int):
            if not isinstance(current, list):
                return False
            while len(current) <= token:
                current.append(None)
            existing = current[token]
            if existing is None or (
                want_list and not isinstance(existing, list)
            ) or (
                not want_list and not isinstance(existing, dict)
            ):
                current[token] = placeholder
            current = current[token]
            continue
        if not isinstance(current, dict):
            return False
        existing = current.get(token, _FLOW_PATH_MISSING)
        if existing is _FLOW_PATH_MISSING or existing is None or (
            want_list and not isinstance(existing, list)
        ) or (
            not want_list and not isinstance(existing, dict)
        ):
            current[token] = placeholder
        current = current[token]
    last = tokens[-1]
    if isinstance(last, int):
        if not isinstance(current, list):
            return False
        while len(current) <= last:
            current.append(None)
        current[last] = value
        return True
    if not isinstance(current, dict):
        return False
    current[last] = value
    return True


def _clean_path_prefix(path: str, prefix: str) -> str:
    if not path:
        return ""
    return path[len(prefix):] if path.startswith(prefix) else path


def _looks_internal(name: str) -> bool:
    return looks_internal_param_name(name) if name else False
