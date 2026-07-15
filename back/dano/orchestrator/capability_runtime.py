"""Runtime adapter for invoking one capability inside a recorded Skill."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

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
    confirm: StrictBool = False
    dry_run: StrictBool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    name: str | None = None
    capability: str | None = None
    protocol: Literal["dano.capability_call.v1"] = "dano.capability_call.v1"

    model_config = ConfigDict(extra="forbid")

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
        parsed = json.loads(value or "{}")
        if isinstance(parsed, dict):
            return dict(parsed)
    raise ValueError("capability input and arguments must be JSON objects")


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
    raw_matches = [
        dict(cap) for cap in list(getattr(skill, "capabilities", []) or [])
        if isinstance(cap, dict) and target in _capability_names(cap)
    ]
    raw_match = raw_matches[0] if len(raw_matches) == 1 else None
    try:
        manifest = to_manifest(skill)
        matches = [
            cap for cap in list(getattr(manifest, "capabilities", []) or [])
            if isinstance(cap, dict) and target in _capability_names(cap)
        ]
        if len(matches) != 1:
            return None
        return {**(raw_match or {}), **dict(matches[0])}
    except Exception:  # noqa: BLE001
        return raw_match


def capability_relation_context(skill, capability: str) -> dict[str, Any]:  # noqa: ANN001
    """Return relation metadata without turning a relation into an execution plan."""
    try:
        relations = list(getattr(to_manifest(skill), "capability_relations", []) or [])
    except Exception:  # noqa: BLE001
        relations = list(getattr(skill, "capability_relations", []) or [])
    incoming: list[dict[str, Any]] = []
    outgoing: list[dict[str, Any]] = []
    for raw in relations:
        if not isinstance(raw, dict):
            continue
        relation = dict(raw)
        relation_type = str(relation.get("mode") or relation.get("type") or "suggested_call_chain")
        relation["type"] = relation_type
        relation["automatic"] = False
        relation["transform_owner"] = "caller" if relation_type == "external_transform" else relation.get("transform_owner")
        if relation.get("to_capability") == capability:
            incoming.append(relation)
        if relation.get("from_capability") == capability:
            outgoing.append(relation)
    return {
        "incoming": incoming,
        "outgoing": outgoing,
        "automatic": False,
        "requires_external_transform": any(r["type"] == "external_transform" for r in incoming),
    }


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
    alternatives = schema.get("oneOf") or schema.get("anyOf")
    if isinstance(alternatives, list) and alternatives:
        matches = [
            not schema_issues(value, candidate, path)
            for candidate in alternatives if isinstance(candidate, dict)
        ]
        valid = sum(bool(match) for match in matches)
        if valid == 0 or (schema.get("oneOf") and valid != 1):
            return [f"Field `{path}` does not match the allowed schema alternatives"]
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
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(set(encoded)) != len(encoded):
                issues.append(f"Field `{path}` must not contain duplicate items")
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
    elif expected == "null" and value is not None:
        issues.append(f"Field `{path}` must be null")
    if isinstance(value, str):
        if schema.get("minLength") is not None and len(value) < int(schema["minLength"]):
            issues.append(f"Field `{path}` must contain at least {schema['minLength']} character(s)")
        if schema.get("maxLength") is not None and len(value) > int(schema["maxLength"]):
            issues.append(f"Field `{path}` must contain at most {schema['maxLength']} character(s)")
        if schema.get("format") == "date":
            try:
                from datetime import date
                date.fromisoformat(value)
            except ValueError:
                issues.append(f"Field `{path}` must be a valid ISO date")
        elif schema.get("format") == "date-time":
            try:
                from datetime import datetime
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                issues.append(f"Field `{path}` must be a valid ISO date-time")
    if isinstance(value, list) and schema.get("maxItems") is not None and len(value) > int(schema["maxItems"]):
        issues.append(f"Field `{path}` must contain at most {schema['maxItems']} item(s)")
    if "const" in schema and value != schema["const"]:
        issues.append(f"Field `{path}` must equal: {schema['const']!r}")
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
    confirmation_flags = [
        cap.get(name) for name in ("requires_confirmation", "requires_human_confirm")
        if name in cap
    ]
    # Persisted confirmation metadata is a security boundary. Invalid scalar
    # values (for example the string "false") never waive confirmation.
    if any(not isinstance(value, bool) for value in confirmation_flags):
        return True
    if any(value is True for value in confirmation_flags):
        return True
    if cap.get("readonly") is True or cap.get("read_only") is True:
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
    relation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = result
    for key in ("structured_output", "output", "response", "final"):
        if key in result and result[key] is not None:
            output = result[key]
            break
    response = result.get("response", output)
    structured = result.get("structured_output", output)
    fact_items = list(result.get("fact_check_items") or [])
    passed_facts = sum(1 for item in fact_items if isinstance(item, dict) and item.get("passed") is True)
    failed_facts = sum(1 for item in fact_items if isinstance(item, dict) and item.get("passed") is False)
    success_count = int(result.get("success_count") or 0)
    failed_count = int(result.get("failed_count") or 0)
    total = int(result.get("total") or (success_count + failed_count))
    invalid_boolean_fields = [
        name for name in ("ok", "blocked", "batch")
        if name in result and not isinstance(result.get(name), bool)
    ]
    result_ok = result.get("ok") is True and not invalid_boolean_fields
    # A malformed explicit blocked marker is untrusted downstream data. Treat
    # it as blocked rather than silently turning it into permission to succeed.
    result_blocked = result.get("blocked") is True or "blocked" in invalid_boolean_fields
    if result_blocked:
        status = "blocked"
    elif (success_count and failed_count) or (passed_facts and failed_facts):
        status = "partial_success"
    elif result_ok:
        status = "succeeded"
    else:
        status = "failed"
    fact_check_passed = result.get("fact_check_passed")
    if fact_check_passed is True or (passed_facts and not failed_facts):
        verification_status = "verified"
    elif passed_facts and failed_facts:
        verification_status = "partially_verified"
    elif fact_check_passed is False or failed_facts:
        verification_status = "unverified"
    else:
        verification_status = "not_checked"
    normalized = {
        "ok": result_ok,
        "skill_id": skill_id,
        "capability": capability,
        "output": output,
        "response": response,
        "structured_output": structured,
        "raw": result,
        "blocked": result_blocked,
        "detail": result.get("detail", ""),
        "status": status,
        "source_status": result.get("status"),
        "batch": result.get("batch") is True,
        "total": total,
        "success_count": success_count,
        "failed_count": failed_count,
        "failed_items": list(result.get("failed_items") or []),
        "results": list(result.get("results") or []),
        "fact_check_passed": fact_check_passed,
        "fact_check_note": result.get("fact_check_note"),
        "fact_check_items": fact_items,
        "verification_status": verification_status,
        "relations": relation_context or {"incoming": [], "outgoing": [], "automatic": False},
    }
    if invalid_boolean_fields:
        normalized.update({
            "ok": False,
            "stage": "invalid_result_contract",
            "status": "blocked" if "blocked" in invalid_boolean_fields else "failed",
            "detail": (
                "结果布尔字段必须是 JSON true/false: "
                + ", ".join(invalid_boolean_fields)
            ),
        })
    if normalized["ok"] and output_schema:
        issues = schema_issues(output, output_schema, "output")
        if issues:
            normalized.update({
                "ok": False,
                "blocked": True,
                "stage": "invalid_output",
                "status": "failed",
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
    credential_headers: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
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

    relation_context = capability_relation_context(skill, capability)
    try:
        fields = payload_fields(payload, capability)
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "blocked": True,
            "stage": "invalid_input",
            "detail": str(exc),
            "capability": capability,
            "input_issues": [str(exc)],
            "relations": relation_context,
        }
    missing = capability_missing_fields(cap, fields)
    if missing:
        return {
            "ok": False,
            "blocked": True,
            "stage": "missing_input",
            "detail": f"Missing required capability fields: {missing}",
            "capability": capability,
            "missing": missing,
            "relations": relation_context,
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
            "relations": relation_context,
        }

    if capability_requires_confirmation(skill, cap) and not payload.confirm:
        return {
            "ok": False,
            "blocked": True,
            "stage": "confirmation_required",
            "detail": f"Capability `{capability}` requires confirm=true",
            "capability": capability,
            "requires_confirmation": True,
            "relations": relation_context,
        }

    api_request = dict(api_request or getattr(skill, "api_request", None) or {})
    if not api_request:
        return {
            "ok": False,
            "blocked": True,
            "stage": "missing_api_request",
            "detail": f"Skill `{getattr(skill, 'skill_id', '')}` has no executable api_request",
            "capability": capability,
            "relations": relation_context,
        }

    if api_request.get("recording_engine") == "playwright_v3":
        metadata = dict(getattr(skill, "call_metadata", {}) or {})
        authoritative_verified = (
            metadata.get("recording_engine") == "playwright_v3"
            and str(metadata.get("verification_status") or "") == "verified"
            and str(metadata.get("publication_status") or "") == "published_verified"
            and metadata.get("direct_call_enabled") is True
            and metadata.get("contract_integrity", True) is True
        )
        nested_verified = (
            str(api_request.get("verification_status") or "") == "verified"
            and api_request.get("direct_call_enabled") is True
        )
        if not (authoritative_verified and nested_verified):
            api_request.update({
                "verification_status": "unverified",
                "publication_status": "published_unverified",
                "direct_call_enabled": False,
            })

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
            "relations": relation_context,
        }

    if api_request.get("recording_engine") == "playwright_v3":
        from dano.recording_v3 import execute_v3_capability

        out = await execute_v3_capability(
            api_request=api_request,
            fields=fields,
            capability=capability,
            confirm=payload.confirm,
            dry_run=payload.dry_run,
            base_url=base_url,
            storage_state=storage_state,
            credential_headers=credential_headers,
            runtime_context=runtime_context,
        )
    else:
        # Existing recording assets retain the legacy runtime unchanged.
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
        relation_context=relation_context,
    )
