"""Deterministic public-input to HTTP wire-format conversion.

This module intentionally uses only the Python standard library so the exact
same file can be copied into a self-contained exported skill package.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any


WIRE_FORMATS = frozenset({"epoch_ms", "epoch_s", "datetime_text", "date_text"})
_DEFAULT_TIMEZONE = timezone(timedelta(hours=8))


class WireFormatError(ValueError):
    """A caller value cannot be represented by the declared wire format."""


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, bool) or value in (None, ""):
        raise WireFormatError("date/time input must not be empty")
    elif isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().lstrip("-").isdigit()
    ):
        number = float(value)
        seconds = number / 1000.0 if abs(number) >= 10**11 else number
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise WireFormatError(f"invalid epoch date/time input: {value!r}") from exc
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise WireFormatError(
                f"invalid date/time input {value!r}; expected ISO 8601 text or epoch"
            ) from exc
    else:
        raise WireFormatError(f"unsupported date/time input type: {type(value).__name__}")
    return parsed.replace(tzinfo=_DEFAULT_TIMEZONE) if parsed.tzinfo is None else parsed


def convert_wire_value(value: Any, wire_format: str) -> Any:
    """Convert one public value to its explicitly declared HTTP representation."""
    target = str(wire_format or "")
    if not target:
        return value
    if target not in WIRE_FORMATS:
        raise WireFormatError(f"unsupported wire format: {target}")
    parsed = _datetime_value(value)
    if target == "epoch_ms":
        return int(parsed.timestamp() * 1000)
    if target == "epoch_s":
        return int(parsed.timestamp())
    local = parsed.astimezone(_DEFAULT_TIMEZONE)
    if target == "datetime_text":
        return local.strftime("%Y-%m-%d %H:%M:%S")
    return local.strftime("%Y-%m-%d")


def apply_wire_formats(values: dict[str, Any], formats: dict[str, str] | None) -> dict[str, Any]:
    """Return a copy with declared fields converted before request rendering."""
    output = dict(values)
    for name, wire_format in (formats or {}).items():
        if name in output:
            output[name] = convert_wire_value(output[name], str(wire_format or ""))
    return output


def date_span_days(start: Any, end: Any) -> int:
    """Evaluate the whitelisted inclusive-neutral date-span strategy."""
    start_dt = _datetime_value(start)
    end_dt = _datetime_value(end)
    return int(round(abs((end_dt.timestamp() - start_dt.timestamp()) / 86400.0)))
