"""Stable FlowSpec fingerprints. Results must match the pre-split hashes."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from dano.execution.page.flow_spec_core.models import FlowSpec


_FINGERPRINT_NON_EXECUTABLE_KEYS = frozenset({
    "title", "description", "intent", "reason", "evidence", "confidence",
    "confidence_tier", "name_source", "display_name", "field_id", "link_id",
    "relation_id", "capability_id", "locked", "updated_by", "confirmation_hash",
    "need_human_confirm", "requires_human_confirm", "step_name",
})


def _execution_fingerprint_payload(spec: FlowSpec) -> dict[str, Any]:
    """Project only state that can change compilation, execution or its gate."""
    from dano.execution.page.flow_spec_core.serialization import flow_spec_release_payload
    canonical = FlowSpec.model_validate(flow_spec_release_payload(spec))

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(item) for key, item in value.items()
                if key not in _FINGERPRINT_NON_EXECUTABLE_KEYS
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    step_rows: list[dict[str, Any]] = []
    for step in canonical.steps:
        row = step.model_dump(mode="json", exclude_none=True)
        for key in ("name", "response_json", "source_meta", "fact_check", "sample_inputs", "notes"):
            row.pop(key, None)
        for param in row.get("params") or []:
            for key in ("label", "description", "reason", "evidence"):
                param.pop(key, None)
        step_rows.append(clean(row))

    capability_refs = {
        cap.capability_id: cap.name
        for cap in canonical.capabilities
        if cap.capability_id and cap.name
    }
    capabilities: list[dict[str, Any]] = []
    for cap in canonical.capabilities:
        row = cap.model_dump(mode="json", exclude_none=True)
        for key in (
            "title", "intent", "status", "evidence", "caller_responsibilities",
            "skill_responsibilities", "confidence", "requires_human_confirm", "updated_by",
        ):
            row.pop(key, None)
        capabilities.append(clean(row))

    relations: list[dict[str, Any]] = []
    for relation in canonical.capability_relations:
        row = relation.model_dump(mode="json", exclude_none=True)
        row["from_capability"] = capability_refs.get(row.get("from_capability"), row.get("from_capability"))
        row["to_capability"] = capability_refs.get(row.get("to_capability"), row.get("to_capability"))
        relations.append(clean(row))

    return {
        "schema_version": canonical.schema_version,
        "risk_level": canonical.risk_level,
        "steps": step_rows,
        "links": [clean(link.model_dump(mode="json", exclude_none=True)) for link in canonical.links],
        "capabilities": capabilities,
        "capability_relations": relations,
    }


def _flow_fingerprint(spec: FlowSpec) -> str:
    payload = _execution_fingerprint_payload(spec)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def flow_spec_fingerprint(spec: FlowSpec) -> str:
    return _flow_fingerprint(spec)


def _stable_json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

