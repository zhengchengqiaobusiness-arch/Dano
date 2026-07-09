"""Runtime adapter for invoking one capability inside a recorded Skill."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

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
    except Exception:  # noqa: BLE001
        return raw_match
    return raw_match


def capability_missing_fields(cap: dict[str, Any] | None, fields: dict[str, Any]) -> list[str]:
    schema = {}
    if isinstance(cap, dict):
        schema = cap.get("parameters") or cap.get("input_schema") or {}
    required = list(schema.get("required") or []) if isinstance(schema, dict) else []
    return [str(k) for k in required if k not in fields or fields.get(k) in (None, "")]


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


def normalize_capability_result(result: dict[str, Any], capability: str, *, skill_id: str = "") -> dict[str, Any]:
    output = (
        result.get("structured_output")
        or result.get("output")
        or result.get("response")
        or result.get("final")
        or result
    )
    response = result.get("response", output)
    structured = result.get("structured_output", output)
    return {
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
    }


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
    return normalize_capability_result(out, capability, skill_id=getattr(skill, "skill_id", ""))
