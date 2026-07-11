"""Runtime adapter for invoking one capability inside a recorded Skill."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dano.catalog.manifest import to_manifest
from dano.execution.page.request_capture import execute_api
from dano.shared.enums import RiskLevel


READ_ONLY_CAPABILITY_KINDS = {
    "query",
    "query_status",
    "list_options",
    "validate",
    "validate_batch",
    "preview",
    "inspect",
}


class CapabilityInvokePayload(BaseModel):
    """Normalized external capability invocation payload."""

    input: dict[str, Any] | None = Field(default_factory=dict)
    arguments: dict[str, Any] | str | None = Field(default_factory=dict)
    confirm: bool = False
    dry_run: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    protocol: str = "dano.capability_call.v1"

    def effective_arguments(self) -> dict[str, Any]:
        return _dict_from(self.arguments)

    def effective_input(self) -> dict[str, Any]:
        return _dict_from(self.input)


def _dict_from(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        return dict(json.loads(value or "{}"))
    return {}


def payload_fields(payload: CapabilityInvokePayload, capability: str) -> dict[str, Any]:
    fields = payload.effective_arguments()
    fields.update(payload.effective_input())
    fields["__capability"] = capability
    return fields


def _capability_names(cap: dict[str, Any]) -> set[str]:
    return {
        str(cap.get("name") or "").strip(),
        str(cap.get("kind") or "").strip(),
        str(cap.get("capability_id") or "").strip(),
    } - {""}


def find_capability(skill, capability: str) -> dict[str, Any] | None:  # noqa: ANN001
    target = str(capability or "").strip()
    if not target:
        return None
    raw_match = None
    for cap in list(getattr(skill, "capabilities", []) or []):
        if isinstance(cap, dict) and target in _capability_names(cap):
            raw_match = dict(cap)
            break
    try:
        manifest = to_manifest(skill)
        for cap in list(getattr(manifest, "capabilities", []) or []):
            if isinstance(cap, dict) and target in _capability_names(cap):
                return {**(raw_match or {}), **dict(cap)}
        return None
    except Exception:  # noqa: BLE001
        return raw_match


def capability_missing_fields(cap: dict[str, Any] | None, fields: dict[str, Any]) -> list[str]:
    schema = {}
    if isinstance(cap, dict):
        schema = cap.get("parameters") or cap.get("input_schema") or {}
    required = list(schema.get("required") or []) if isinstance(schema, dict) else []
    return [str(k) for k in required if k not in fields or fields.get(k) in (None, "")]


def schema_issues(value: Any, schema: dict[str, Any] | None, path: str = "input") -> list[str]:
    """Validate the JSON-Schema subset emitted by Dano contracts."""
    if not isinstance(schema, dict):
        return []
    issues: list[str] = []
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            return [f"Field `{path}` must be an object"]
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        missing = [
            str(name) for name in (schema.get("required") or [])
            if name not in value or value.get(name) in (None, "")
        ]
        if missing:
            issues.append(f"Field `{path}` missing required fields: {missing}")
        if schema.get("additionalProperties") is False:
            extra = sorted(str(name) for name in value if name not in properties)
            if extra:
                issues.append(f"Field `{path}` has unexpected fields: {extra}")
        for name, child_schema in properties.items():
            if name in value and value[name] is not None and isinstance(child_schema, dict):
                issues.extend(schema_issues(value[name], child_schema, f"{path}.{name}"))
    elif expected == "array":
        if not isinstance(value, list):
            return [f"Field `{path}` must be an array"]
        if schema.get("minItems") is not None and len(value) < int(schema["minItems"]):
            issues.append(f"Field `{path}` must contain at least {schema['minItems']} item(s)")
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        for index, item in enumerate(value):
            issues.extend(schema_issues(item, item_schema, f"{path}[{index}]"))
    elif expected == "string" and not isinstance(value, str):
        issues.append(f"Field `{path}` must be a string")
    elif expected == "boolean" and not isinstance(value, bool):
        issues.append(f"Field `{path}` must be a boolean")
    elif expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        issues.append(f"Field `{path}` must be an integer")
    elif expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        issues.append(f"Field `{path}` must be a number")
    if schema.get("enum") and value not in schema["enum"]:
        issues.append(f"Field `{path}` must be one of: {schema['enum']}")
    return issues


def capability_input_issues(cap: dict[str, Any] | None, fields: dict[str, Any]) -> list[str]:
    """Validate the complete capability boundary, including every batch entry."""
    if not isinstance(cap, dict):
        return []
    schema = cap.get("parameters") or cap.get("input_schema") or {}
    public_fields = {name: value for name, value in fields.items() if not str(name).startswith("__")}
    issues = schema_issues(public_fields, schema, "input")
    issues = [issue.replace("Field `input.", "Field `") for issue in issues]
    kind = str(cap.get("kind") or cap.get("name") or "")
    if kind == "submit_batch":
        entries = public_fields.get("entries")
        if not isinstance(entries, list):
            marker = "Field `input.entries` must be an array"
            if marker not in issues:
                issues.append(marker)
    return issues


def capability_requires_confirmation(skill, cap: dict[str, Any] | None) -> bool:  # noqa: ANN001
    if not isinstance(cap, dict):
        return False
    if bool(cap.get("requires_confirmation")) or bool(cap.get("requires_human_confirm")):
        return True
    if bool(cap.get("readonly")) or bool(cap.get("read_only")):
        return False
    kind = str(cap.get("kind") or cap.get("name") or "").strip()
    if kind in READ_ONLY_CAPABILITY_KINDS:
        return False
    try:
        return RiskLevel(skill.risk_level) in {RiskLevel.L3, RiskLevel.L4, RiskLevel.L5}
    except Exception:  # noqa: BLE001
        return False


def normalize_capability_result(
    result: dict[str, Any],
    capability: str,
    *,
    skill_id: str = "",
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = result
    for key in ("structured_output", "output", "response", "final"):
        if key in result and result[key] is not None:
            output = result[key]
            break
    response = result.get("response", output)
    structured = result.get("structured_output", output)
    normalized = {
        "ok": bool(result.get("ok")),
        "skill_id": skill_id,
        "capability": capability,
        "output": output,
        "response": response,
        "structured_output": structured,
        "raw": result,
        "blocked": bool(result.get("blocked", False)),
        "detail": result.get("detail", ""),
        "status": result.get("status"),
        "fact_check_passed": result.get("fact_check_passed"),
        "fact_check_note": result.get("fact_check_note"),
    }
    if normalized["ok"] and output_schema:
        issues = schema_issues(output, output_schema, "output")
        if issues:
            normalized.update({
                "ok": False,
                "blocked": True,
                "stage": "invalid_output",
                "detail": "；".join(issues),
                "output_issues": issues,
            })
    return normalized


async def invoke_skill_capability(
    *,
    skill,
    capability: str,
    payload: CapabilityInvokePayload,
    api_request: dict[str, Any] | None = None,
    base_url: str = "",
    storage_state: dict[str, Any] | None = None,
    token_key: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Validate and execute one named capability through execute_api."""

    cap = find_capability(skill, capability)
    if cap is None:
        return {
            "ok": False,
            "blocked": True,
            "stage": "capability_not_found",
            "detail": f"Unknown capability: {capability}",
            "capability": capability,
        }

    fields = payload_fields(payload, capability)
    missing = capability_missing_fields(cap, fields)
    if missing:
        return {
            "ok": False,
            "blocked": True,
            "stage": "missing_input",
            "detail": f"Missing required capability fields: {missing}",
            "capability": capability,
            "missing": missing,
        }

    input_issues = capability_input_issues(cap, fields)
    if input_issues:
        return {
            "ok": False,
            "blocked": True,
            "stage": "invalid_input",
            "detail": "；".join(input_issues),
            "capability": capability,
            "input_issues": input_issues,
        }

    if capability_requires_confirmation(skill, cap) and not payload.confirm:
        return {
            "ok": False,
            "blocked": True,
            "stage": "confirmation_required",
            "detail": f"Capability `{capability}` requires confirm=true",
            "capability": capability,
            "requires_confirmation": True,
        }

    api_request = dict(api_request or getattr(skill, "api_request", None) or {})
    if not api_request:
        return {
            "ok": False,
            "blocked": True,
            "stage": "missing_api_request",
            "detail": f"Skill `{getattr(skill, 'skill_id', '')}` has no executable api_request",
            "capability": capability,
        }

    if payload.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "capability": capability,
            "fields": fields,
            "api_shape": {
                "has_steps": bool(api_request.get("steps")),
                "step_count": len(api_request.get("steps") or []),
                "capabilities": [
                    c.get("name") or c.get("kind")
                    for c in (api_request.get("capabilities") or [])
                    if isinstance(c, dict)
                ],
            },
        }

    out = await execute_api(
        api_request,
        fields,
        base_url=base_url,
        storage_state=storage_state,
        token_key=token_key,
        verify=verify,
        send=True,
    )
    return normalize_capability_result(
        out,
        capability,
        skill_id=getattr(skill, "skill_id", ""),
        output_schema=cap.get("output_schema") or {},
    )
    model_config = ConfigDict(extra="forbid")
