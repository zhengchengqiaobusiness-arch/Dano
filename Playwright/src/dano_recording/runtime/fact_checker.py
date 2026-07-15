"""Small deterministic success/fact checks; no eval and no model calls."""

from __future__ import annotations

import re
from typing import Any

from .conditions import condition_value
from .request_builder import lookup


def schema_issues(value: Any, schema: dict[str, Any] | None, path: str = "value") -> list[str]:
    """Validate the bounded JSON-Schema subset emitted by the V3 compiler."""

    if not isinstance(schema, dict) or not schema:
        return []
    alternatives = schema.get("oneOf") or schema.get("anyOf")
    if isinstance(alternatives, list) and alternatives:
        matches = [
            not schema_issues(value, item, path)
            for item in alternatives if isinstance(item, dict)
        ]
        required_matches = 1 if schema.get("oneOf") else max(1, sum(bool(item) for item in matches))
        if not any(matches) or (schema.get("oneOf") and sum(bool(item) for item in matches) != required_matches):
            return [f"{path} does not match its allowed schema"]
        return []
    if "enum" in schema and value not in (schema.get("enum") or []):
        return [f"{path} is not an allowed value"]
    expected = schema.get("type")
    if isinstance(expected, list):
        if any(not schema_issues(value, {**schema, "type": item}, path) for item in expected):
            return []
        return [f"{path} does not match any allowed type"]
    if expected == "object":
        if not isinstance(value, dict):
            return [f"{path} must be an object"]
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        issues = [
            f"{path}.{name} is required"
            for name in schema.get("required") or []
            if name not in value or value.get(name) in (None, "")
        ]
        field_names = schema.get("x-dano-field-names") or {}
        contracts = schema.get("x-dano-required-contracts") or []
        for contract in contracts if isinstance(contracts, list) else []:
            if not isinstance(contract, dict):
                issues.append(f"{path} has an invalid required contract")
                continue
            public_name = str(contract.get("public_name") or "")
            missing = public_name not in value or value.get(public_name) in (None, "")
            if not public_name or not missing:
                continue
            try:
                caller_required = str(contract.get("caller_required") or "unknown") == "true"
                caller_condition = contract.get("caller_condition")
                if caller_required and caller_condition is not None:
                    caller_required = condition_value(caller_condition, value, field_names)
                wire_required = str(contract.get("wire_required") or "unknown") == "true"
                wire_condition = contract.get("wire_condition")
                if wire_required and wire_condition is not None:
                    wire_required = condition_value(wire_condition, value, field_names)
            except ValueError as exc:
                issues.append(f"{path} has an invalid required contract: {exc}")
                continue
            provider_kind = str(contract.get("provider_kind") or "").lower()
            caller_wire_provider = provider_kind in {
                "caller", "caller_input", "user_input", "option_source",
            }
            if (caller_required or (wire_required and caller_wire_provider)) and (
                f"{path}.{public_name} is required" not in issues
            ):
                issues.append(f"{path}.{public_name} is required")
        if schema.get("additionalProperties") is False:
            issues.extend(
                f"{path}.{name} is not allowed" for name in value if name not in properties
            )
        for name, child in properties.items():
            if name in value and isinstance(child, dict):
                issues.extend(schema_issues(value[name], child, f"{path}.{name}"))
        return issues
    if expected == "array":
        if not isinstance(value, list):
            return [f"{path} must be an array"]
        issues: list[str] = []
        if schema.get("minItems") is not None and len(value) < int(schema["minItems"]):
            issues.append(f"{path} contains too few items")
        if schema.get("maxItems") is not None and len(value) > int(schema["maxItems"]):
            issues.append(f"{path} contains too many items")
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        for index, item in enumerate(value):
            issues.extend(schema_issues(item, item_schema, f"{path}[{index}]"))
        return issues
    checks = {
        "string": lambda item: isinstance(item, str),
        "boolean": lambda item: isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if expected in checks and not checks[expected](value):
        return [f"{path} must be {expected}"]
    return []


def response_success(status: int, payload: Any, rule: dict | None = None) -> tuple[bool, str]:
    rule = rule or {}
    allowed_statuses = rule.get("status_codes")
    if allowed_statuses is not None:
        try:
            accepted = status in {int(value) for value in allowed_statuses}
        except (TypeError, ValueError) as exc:
            raise ValueError("success_rule.status_codes must contain integers") from exc
    else:
        minimum = int(rule.get("status_min", 200))
        maximum = int(rule.get("status_max", 299))
        accepted = minimum <= status <= maximum
    if not accepted:
        return False, f"HTTP {status}"
    nested = rule.get("all")
    if isinstance(nested, list):
        for item in nested:
            passed, reason = response_success(status, payload, item if isinstance(item, dict) else {})
            if not passed:
                return False, reason
        return True, "all_success_rules"
    alternatives = rule.get("any")
    if isinstance(alternatives, list) and alternatives:
        reasons: list[str] = []
        for item in alternatives:
            passed, reason = response_success(status, payload, item if isinstance(item, dict) else {})
            if passed:
                return True, "any_success_rule"
            reasons.append(reason)
        return False, "; ".join(reasons)
    path = str(rule.get("path") or "")
    if not path:
        return True, "http_2xx"
    try:
        actual = lookup({"response": payload}, path if path.startswith("response") else f"response.{path}")
    except KeyError:
        if rule.get("exists") is False:
            return True, "success_path_absent"
        return False, f"success evidence path missing: {path}"
    if rule.get("exists") is False:
        return False, f"success evidence path unexpectedly exists: {path}"
    if "equals" in rule and actual != rule["equals"]:
        return False, f"success evidence mismatch: {path}"
    if "not_equals" in rule and actual == rule["not_equals"]:
        return False, f"success evidence forbidden value: {path}"
    if "in" in rule and actual not in (rule.get("in") or []):
        return False, f"success evidence value is not allowed: {path}"
    if rule.get("non_empty") and actual in (None, "", [], {}):
        return False, f"success evidence empty: {path}"
    if "matches" in rule:
        pattern = str(rule["matches"])
        if len(pattern) > 512 or re.search(pattern, str(actual)) is None:
            return False, f"success evidence pattern mismatch: {path}"
    return True, "success_rule"
