"""Frozen request/response helpers. Skill 4 must not rewrite this file."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def as_text(value: Any) -> str:
    return "" if value is None else str(value)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    return text in {"1", "true", "yes", "y", "on"}


def format_datetime(value: Any, pattern: str = "%Y-%m-%d %H:%M:%S") -> str:
    if isinstance(value, datetime):
        return value.strftime(pattern)
    text = as_text(value).strip()
    return text


def pick(mapping: dict | None, *keys: str, default: Any = None) -> Any:
    data = mapping or {}
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default
