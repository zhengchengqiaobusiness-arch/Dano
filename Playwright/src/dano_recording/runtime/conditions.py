"""Bounded evaluator for RequiredContract conditions (never uses eval)."""

from __future__ import annotations

from typing import Any, Mapping


_LEAF = {"equals", "not_equals", "in", "not_in", "exists"}
_LOGICAL = {"and", "or", "not"}


def condition_value(
    condition: Mapping[str, Any],
    values: Mapping[str, Any],
    field_names: Mapping[str, str],
) -> bool:
    if not isinstance(condition, Mapping):
        raise ValueError("required condition must be an object")
    operator = str(condition.get("operator") or "").lower()
    if operator in _LOGICAL:
        operands = condition.get("operands")
        if not isinstance(operands, (list, tuple)) or not operands:
            raise ValueError("logical required condition needs operands")
        resolved = [condition_value(item, values, field_names) for item in operands]
        if operator == "not":
            if len(resolved) != 1:
                raise ValueError("not required condition needs exactly one operand")
            return not resolved[0]
        return all(resolved) if operator == "and" else any(resolved)
    if operator not in _LEAF:
        raise ValueError(f"unsupported required-condition operator: {operator or '<missing>'}")
    field_uuid = str(condition.get("field_uuid") or "")
    public_name = str(field_names.get(field_uuid) or condition.get("public_name") or "")
    if not field_uuid or not public_name:
        raise ValueError("required condition references an unknown field")
    present = public_name in values
    actual = values.get(public_name)
    expected = condition.get("value")
    if operator == "exists":
        return present and actual is not None
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    if not isinstance(expected, (list, tuple, set, frozenset)):
        raise ValueError(f"{operator} required condition needs an array value")
    if operator == "in":
        return actual in expected
    return actual not in expected


__all__ = ["condition_value"]
