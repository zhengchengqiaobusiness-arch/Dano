"""Compatibility projection for the existing PageRecorder workbench.

This is a one-way view of recording-v3 models.  It does not instantiate or
import the legacy FlowSpec and must never be accepted back as authoritative
state; edits are persisted as revisioned decision commands.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from dano_recording.compiler.models import RecordingCompilation
from dano_recording.compiler.pipeline import RecordingContractProjection
from dano_recording.domain.capabilities import Capability, CapabilityRisk
from dano_recording.domain.fields import (
    EffectiveFieldContract,
    FieldDimension,
    RequiredState,
    SourceBindingKind,
    ValueProviderKind,
)
from dano_recording.domain.operations import RequestDisposition
from dano_recording.domain.recording import RecordingSession
from dano_recording.domain.relations import RelationType
from dano_recording.value_evidence import (
    ValueEvidence,
    ValueSensitivity,
    safe_value_from_evidence,
)


def _step_id(request_id: str) -> str:
    return f"step_{hashlib.sha256(request_id.encode()).hexdigest()[:16]}"


def _sample_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _query_object(items: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for key, value in items:
        grouped[key].append(value)
    return {
        key: values[0] if len(values) == 1 else values
        for key, values in grouped.items()
    }


def _content_type(headers: Mapping[str, str]) -> str:
    return next(
        (str(value) for key, value in headers.items() if key.lower() == "content-type"),
        "",
    )


def _field_category(field: EffectiveFieldContract) -> str:
    kind = field.value_provider.kind
    if kind is ValueProviderKind.USER_INPUT:
        return "user_param"
    if kind is ValueProviderKind.CONSTANT:
        return "system_const"
    return "runtime_var"


def _provider_projection(source: Any) -> dict[str, Any] | None:
    if source is None:
        return None
    kind = source.kind
    if kind is SourceBindingKind.CALLER:
        return {"kind": "caller"}
    if kind is SourceBindingKind.DEFAULT:
        return {"kind": "default", "value": source.value}
    if kind is SourceBindingKind.CONSTANT:
        return {"kind": "constant", "value": source.value}
    if kind is SourceBindingKind.RUNTIME_CONTEXT:
        return {
            "kind": "runtime_context",
            "runtime_resolver": source.runtime_resolver,
        }
    if kind in {
        SourceBindingKind.PREVIOUS_RESPONSE,
        SourceBindingKind.DEPENDENCY_RESPONSE,
    }:
        return {
            "kind": "dependency_response",
            "request_definition_id": source.request_definition_id or source.request_id,
            "response_path": source.response_path,
        }
    if kind is SourceBindingKind.DERIVED:
        return {"kind": "derived", "expression": source.expression}
    return None


def _legacy_source_projection(
    source: Any,
    fallback: EffectiveFieldContract,
) -> tuple[str, dict[str, Any], str]:
    if source is None:
        provider = fallback.value_provider.model_dump(mode="json", exclude_none=True)
        return fallback.value_provider.kind.value, provider, _field_category(fallback)
    if source.kind is SourceBindingKind.CALLER:
        return "user_input", {"kind": "user_input"}, "user_param"
    if source.kind in {
        SourceBindingKind.PREVIOUS_RESPONSE,
        SourceBindingKind.DEPENDENCY_RESPONSE,
    }:
        return (
            "previous_response",
            {
                "kind": "previous_response",
                "source_request_id": source.request_definition_id or source.request_id,
                "source_path": source.response_path,
            },
            "runtime_var",
        )
    if source.kind is SourceBindingKind.RUNTIME_CONTEXT:
        kind = (
            "request_header"
            if "request_headers" in str(source.runtime_resolver or "")
            else "page_context"
        )
        return (
            kind,
            {
                "kind": kind,
                "expression": source.runtime_resolver,
                "runtime_resolver": source.runtime_resolver,
            },
            "runtime_var",
        )
    if source.kind in {SourceBindingKind.CONSTANT, SourceBindingKind.DEFAULT}:
        return (
            "constant",
            {"kind": "constant", "constant": source.value},
            "system_const",
        )
    if source.kind is SourceBindingKind.DERIVED:
        return (
            "computed",
            {"kind": "computed", "expression": source.expression},
            "runtime_var",
        )
    return "unresolved", {"kind": "unresolved"}, "runtime_var"


def _field_row(
    field: EffectiveFieldContract,
    *,
    step_id: str,
    contracts: RecordingContractProjection | None = None,
) -> dict[str, Any]:
    choice_options = (
        [option.model_dump(mode="json") for option in field.choice_contract.options]
        if field.choice_contract is not None else None
    )
    sample = field.wire_schema.sample
    field_uuid = contracts.field_uuids.get(field.field_contract_id) if contracts else None
    canonical = next(
        (
            item
            for item in contracts.field_registry.fields
            if str(item.field_uuid) == field_uuid
        ),
        None,
    ) if contracts and field_uuid else None
    binding_ids = set(canonical.wire_binding_ids) if canonical else set()
    bindings = [
        item
        for item in (contracts.field_registry.bindings if contracts else ())
        if item.binding_id in binding_ids
    ]
    decisions = dict(canonical.decisions) if canonical else {}
    display_decision = decisions.get(FieldDimension.DISPLAY_NAME)
    business_decision = decisions.get(FieldDimension.BUSINESS_TYPE)
    classification = decisions.get(FieldDimension.CLASSIFICATION)
    source_decision = decisions.get(FieldDimension.SOURCE_BINDING)
    caller_required = decisions.get(FieldDimension.CALLER_REQUIRED)
    wire_required = decisions.get(FieldDimension.WIRE_REQUIRED)
    exposure = decisions.get(FieldDimension.EXPOSURE)
    enum_binding = decisions.get(FieldDimension.ENUM_BINDING)
    default_decision = decisions.get(FieldDimension.DEFAULT_VALUE)
    conditions_decision = decisions.get(FieldDimension.REQUIRED_CONDITIONS)
    source_binding = source_decision.value if source_decision is not None else None
    if (
        enum_binding is not None
        and isinstance(enum_binding.value, Mapping)
        and enum_binding.value.get("static_values_retained") is False
    ):
        choice_options = None
    classification_value = str(classification.value) if classification else ""
    safe_sample = sample
    if classification_value == ValueSensitivity.IDENTITY.value:
        safe_sample = (
            "{{" + str(source_binding.runtime_resolver) + "}}"
            if source_binding is not None and source_binding.runtime_resolver
            else {"redacted": True, "kind": "identity"}
        )
    elif classification_value == ValueSensitivity.PII.value:
        safe_sample = "[REDACTED:PII]"
    elif classification_value == ValueSensitivity.CREDENTIAL.value:
        safe_sample = None
    source_kind, source_projection, category = _legacy_source_projection(
        source_binding,
        field,
    )
    conditions = conditions_decision.value if conditions_decision else {}
    if not isinstance(conditions, Mapping):
        conditions = {}
    resolved_name = str(display_decision.value) if display_decision else field.name
    resolved_business_type = (
        str(business_decision.value) if business_decision else field.business_type
    )
    exposed = bool(exposure.value) if exposure else field.exposed
    wire_state = (
        RequiredState(wire_required.value)
        if wire_required is not None
        else (RequiredState.TRUE if field.required else RequiredState.FALSE)
    )
    caller_state = (
        RequiredState(caller_required.value)
        if caller_required is not None
        else (RequiredState.TRUE if field.required and exposed else RequiredState.FALSE)
    )
    row = {
        # ``field_contract_id`` remains as a compatibility alias.  All new UI,
        # Pi and edit targets use the permanent UUID.
        "field_id": field_uuid or field.field_contract_id,
        "field_uuid": field_uuid,
        "field_contract_id": field.field_contract_id,
        "lineage_id": str(contracts.field_registry.lineage_id) if contracts else None,
        "aliases": [item.model_dump(mode="json") for item in canonical.aliases]
        if canonical else [],
        "scope": "request",
        "location": field.location.value,
        "display_name": resolved_name,
        "path": field.wire_path,
        "key": resolved_name,
        "label": resolved_name,
        "value": _sample_text(safe_sample),
        "sample_value": safe_sample,
        "default_value": (
            None
            if classification_value in {
                ValueSensitivity.CREDENTIAL.value,
                ValueSensitivity.IDENTITY.value,
                ValueSensitivity.PII.value,
            }
            else (default_decision.value if default_decision else None)
        ),
        "type": resolved_business_type,
        "business_type": resolved_business_type,
        "wire_type": field.wire_schema.type,
        "required": caller_state is RequiredState.TRUE,
        "wire_required": wire_state.value,
        "caller_required": caller_state.value,
        "request_id": field.request_id,
        "step_id": step_id,
        "step_uuid": contracts.step_uuids.get(field.request_id) if contracts else None,
        "category": category,
        "source_kind": source_kind,
        "source": source_projection,
        "value_provider": source_projection,
        "source_binding": source_binding.model_dump(mode="json", exclude_none=True)
        if source_binding is not None else None,
        "exposed_to_user": exposed,
        "exposed_to_caller": exposed,
        "classification": classification_value or None,
        "enum_options": choice_options,
        "enum_binding": enum_binding.value if enum_binding else None,
        "name_source": display_decision.origin.value
        if display_decision else field.origins[FieldDimension.NAME].value,
        "editable": True,
        "confirmed": not bool(field.unresolved_dimensions),
        "locked": False,
        "wire_bindings": [item.model_dump(mode="json") for item in bindings],
        "axis_decisions": {
            axis.value: decision.model_dump(mode="json")
            for axis, decision in decisions.items()
        },
        "required_contract": {
            "wire_required": wire_state.value,
            "caller_required": caller_state.value,
            "wire_condition": deepcopy(conditions.get("wire_condition")),
            "caller_condition": deepcopy(conditions.get("caller_condition")),
            "provider": _provider_projection(source_binding),
        },
        "evidence": list(field.choice_contract.evidence_ids)
            if field.choice_contract is not None else [],
    }
    return row


def _json_schema_for_field(field: EffectiveFieldContract) -> dict[str, Any]:
    type_name = field.business_type.lower()
    schema_type = {
        "integer": "integer",
        "int": "integer",
        "number": "number",
        "float": "number",
        "boolean": "boolean",
        "bool": "boolean",
        "array": "array",
        "list": "array",
        "object": "object",
    }.get(type_name, "string")
    schema: dict[str, Any] = {
        "type": schema_type,
        "x-dano-wire-type": field.wire_schema.type,
        "title": field.name,
    }
    if field.choice_contract is not None and field.choice_contract.options:
        schema["enum"] = [option.value for option in field.choice_contract.options]
        schema["x-dano-enum-labels"] = [option.label for option in field.choice_contract.options]
    return schema


def _input_schema(fields: tuple[EffectiveFieldContract, ...]) -> dict[str, Any]:
    exposed = tuple(field for field in fields if field.exposed)
    return {
        "type": "object",
        "properties": {field.name: _json_schema_for_field(field) for field in exposed},
        "required": [field.name for field in exposed if field.required],
        "additionalProperties": False,
    }


def _input_schema_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exposed = [row for row in rows if row.get("exposed_to_caller")]
    properties: dict[str, Any] = {}
    required: list[str] = []
    for row in exposed:
        name = str(row.get("display_name") or row.get("key") or row.get("path") or "")
        if not name:
            continue
        type_name = str(row.get("business_type") or row.get("type") or "string").lower()
        properties[name] = {
            "type": {
                "int": "integer",
                "integer": "integer",
                "float": "number",
                "number": "number",
                "bool": "boolean",
                "boolean": "boolean",
                "array": "array",
                "object": "object",
            }.get(type_name, "string"),
            "x-dano-field-uuid": row.get("field_uuid"),
            "x-dano-wire-type": row.get("wire_type") or "any",
        }
        if row.get("caller_required") == RequiredState.TRUE.value or row.get("required") is True:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _risk_value(risk: CapabilityRisk) -> int:
    return int(risk.value[1:])


def _session_values(
    compilation: RecordingCompilation,
    session_metadata: RecordingSession | Mapping[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(session_metadata, RecordingSession):
        values = session_metadata.model_dump(mode="json")
        values.update(session_metadata.metadata)
    else:
        values = dict(session_metadata or {})
        nested = values.get("metadata")
        if isinstance(nested, Mapping):
            values.update(nested)
    scoped_tenant = str(values.get("tenant") or compilation.tenant)
    scoped_recording = str(values.get("recording_id") or compilation.recording_id)
    if scoped_tenant != compilation.tenant or scoped_recording != compilation.recording_id:
        raise ValueError("session metadata scope does not match compilation")
    return values


def _observation_evidence(
    contracts: RecordingContractProjection | None,
    request_id: str,
) -> tuple[tuple[ValueEvidence, ...], tuple[ValueEvidence, ...]]:
    if contracts is None:
        return (), ()
    observation_id = contracts.observation_ids.get(request_id)
    observation = next(
        (
            item for item in contracts.capture_store.observations
            if item.observation_id == observation_id
        ),
        None,
    )
    if observation is None:
        return (), ()
    return observation.request_values, observation.response_values


def _field_runtime_value(row: Mapping[str, Any]) -> Any:
    source = row.get("source_binding")
    source = source if isinstance(source, Mapping) else {}
    kind = str(source.get("kind") or "")
    name = str(row.get("display_name") or row.get("key") or row.get("path") or "value")
    if kind == SourceBindingKind.RUNTIME_CONTEXT.value and source.get("runtime_resolver"):
        return "{{" + str(source["runtime_resolver"]) + "}}"
    if kind in {
        SourceBindingKind.PREVIOUS_RESPONSE.value,
        SourceBindingKind.DEPENDENCY_RESPONSE.value,
    }:
        request_ref = str(source.get("request_definition_id") or source.get("request_id") or "")
        response_path = str(source.get("response_path") or "")
        return "{{responses." + request_ref + "." + response_path + "}}"
    if kind in {SourceBindingKind.CONSTANT.value, SourceBindingKind.DEFAULT.value}:
        return deepcopy(source.get("value"))
    if kind == SourceBindingKind.DERIVED.value and source.get("expression"):
        return "{{derived." + str(source["expression"]) + "}}"
    classification = str(row.get("classification") or "").casefold()
    if classification == ValueSensitivity.CREDENTIAL.value:
        return "[REDACTED:CREDENTIAL]"
    return "{{inputs." + name + "}}"


def _assign_wire_value(container: Any, path: str, value: Any) -> Any:
    if not isinstance(container, dict):
        return container
    parts = [part for part in path.split(".") if part]
    if not parts:
        return value
    cursor: dict[str, Any] = container
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value
    return container


def _safe_request_projection(
    request: Any,
    rows: list[dict[str, Any]],
    contracts: RecordingContractProjection | None,
) -> dict[str, Any]:
    request_evidence, response_evidence = _observation_evidence(
        contracts, request.request_id
    )
    query = safe_value_from_evidence(
        _query_object(request.query),
        request_evidence,
        root_path="query",
    )
    headers = safe_value_from_evidence(
        dict(request.headers),
        request_evidence,
        root_path="header",
    )
    body = (
        safe_value_from_evidence(
            request.body,
            request_evidence,
            root_path="body",
        )
        if request.body_present else None
    )
    response = (
        safe_value_from_evidence(
            request.response_body,
            response_evidence,
            root_path="response",
        )
        if request.response_body is not None else None
    )
    for row in rows:
        location = str(row.get("location") or "")
        path = str(row.get("path") or row.get("wire_path") or "")
        runtime_value = _field_runtime_value(row)
        if location == "query" and isinstance(query, dict):
            query[path] = runtime_value
        elif location in {"body", "form"}:
            body = _assign_wire_value(body, path, runtime_value)
        elif location == "header" and isinstance(headers, dict):
            header_key = next(
                (key for key in headers if str(key).casefold() == path.casefold()),
                path,
            )
            headers[header_key] = runtime_value
    return {
        "query": query,
        "headers": headers,
        "body": body,
        "response": response,
    }


def _url_with_safe_query(url: str, query: Mapping[str, Any]) -> str:
    parts = urlsplit(url)
    pairs: list[tuple[str, Any]] = []
    for key, value in query.items():
        if isinstance(value, list):
            pairs.extend((str(key), item) for item in value)
        else:
            pairs.append((str(key), value))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(pairs, doseq=True), parts.fragment)
    )


def compilation_to_workbench(
    compilation: RecordingCompilation,
    session_metadata: RecordingSession | Mapping[str, Any] | None = None,
    contracts: RecordingContractProjection | None = None,
) -> dict[str, Any]:
    """Project a compilation into the current workbench's ``full_spec`` shape."""

    session = _session_values(compilation, session_metadata)
    request_by_id = {request.request_id: request for request in compilation.requests}
    analysis_by_id = {
        analysis.request_id: analysis for analysis in compilation.request_analyses
    }
    fields_by_request: dict[str, list[EffectiveFieldContract]] = defaultdict(list)
    for field in compilation.fields:
        fields_by_request[field.request_id].append(field)
    capability_by_request: dict[str, Capability] = {}
    for capability in compilation.capabilities:
        for request_id in capability.request_ids:
            capability_by_request[request_id] = capability

    step_ids = {
        request.request_id: _step_id(request.request_id)
        for request in compilation.requests if request.capability_eligible
    }
    steps: list[dict[str, Any]] = []
    for request in compilation.requests:
        if not request.capability_eligible:
            continue
        step_id = step_ids[request.request_id]
        capability = capability_by_request.get(request.request_id)
        fields = tuple(fields_by_request.get(request.request_id, ()))
        params = [
            _field_row(field, step_id=step_id, contracts=contracts)
            for field in fields
        ]
        safe = _safe_request_projection(request, params, contracts)
        steps.append({
            "step_id": step_id,
            "step_uuid": contracts.step_uuids.get(request.request_id) if contracts else None,
            "request_definition_id": contracts.request_definition_ids.get(request.request_id)
            if contracts else None,
            "observation_id": contracts.observation_ids.get(request.request_id)
            if contracts else None,
            "request_id": request.request_id,
            "name": capability.title if capability is not None else f"{request.method} {request.path}",
            "method": request.method,
            "url": _url_with_safe_query(request.url, safe["query"]),
            "path": request.path,
            "risk_level": capability.risk_level.value if capability is not None else "L1",
            "params": params,
            "selects": [],
            "identity": [],
            "semantic_role": request.disposition.value,
            "source_meta": {
                "role": request.disposition.value,
                "reason": request.disposition_reason,
                "recording_engine": "playwright_v3",
            },
            "content_type": _content_type(request.headers),
            "body_source": _sample_text(safe["body"]) if request.body_present else "",
            # Runtime templates remain typed.  In particular, bodyless writes and
            # query-only commands are represented instead of disappearing.
            "query": safe["query"],
            "query_template": deepcopy(safe["query"]),
            "body": safe["body"] if request.body_present else None,
            "body_template": deepcopy(safe["body"]) if request.body_present else None,
            "headers": safe["headers"],
            "requires_human_confirm": bool(
                capability is not None and capability.explicit_confirmation
            ),
            "sample_inputs": {
                str(row.get("display_name") or row.get("key")): _sample_text(
                    row.get("sample_value")
                )
                for row in params
                if row.get("exposed_to_caller")
                and str(row.get("classification") or "")
                not in {
                    ValueSensitivity.CREDENTIAL.value,
                    ValueSensitivity.IDENTITY.value,
                    ValueSensitivity.PII.value,
                }
            },
            "response_json": safe["response"],
            "response_schema": request.response_schema,
        })

    capabilities: list[dict[str, Any]] = []
    for capability in compilation.capabilities:
        requests = tuple(request_by_id[request_id] for request_id in capability.request_ids)
        scoped_fields = tuple(
            field for field in compilation.fields
            if field.field_contract_id in capability.field_contract_ids
        )
        field_rows = [
            _field_row(field, step_id=step_ids[field.request_id], contracts=contracts)
            for field in scoped_fields if field.request_id in step_ids
        ]
        capabilities.append({
            "capability_id": capability.capability_id,
            "capability_uuid": contracts.capability_uuids.get(capability.capability_id)
            if contracts else None,
            "name": capability.name,
            "operation": capability.operation or capability.name,
            "title": capability.title,
            "intent": capability.title,
            "kind": "workflow" if len(requests) > 1 else "operation",
            "request_refs": [
                {
                    "request_id": request.request_id,
                    "request_index": index,
                    "step_id": step_ids[request.request_id],
                    "step_uuid": contracts.step_uuids.get(request.request_id)
                    if contracts else None,
                    "role": request.disposition.value,
                    "method": request.method,
                    "path": request.path,
                    "sequence": request.sequence,
                    "confidence": analysis_by_id[request.request_id].confidence,
                    "reason": request.disposition_reason,
                    "usage": "execute",
                    "origin": "deterministic",
                    "confirmed": True,
                }
                for index, request in enumerate(requests)
            ],
            "step_ids": [step_ids[request.request_id] for request in requests],
            "step_uuids": [
                contracts.step_uuids[request.request_id]
                for request in requests
                if contracts and request.request_id in contracts.step_uuids
            ],
            "fields": field_rows,
            "inputs": [row for row in field_rows if row["exposed_to_caller"]],
            "request_fields": field_rows,
            "internal_fields": [row for row in field_rows if not row["exposed_to_caller"]],
            "computed_fields": [],
            "outputs": [],
            "dependencies": [],
            "nodes": [
                {
                    "type": "call",
                    "step_id": step_ids[request.request_id],
                    "step_uuid": contracts.step_uuids.get(request.request_id)
                    if contracts else None,
                }
                for request in requests
            ],
            "input_schema": _input_schema_from_rows(field_rows)
            if contracts else _input_schema(scoped_fields),
            "output_schema": next(
                (request.response_schema for request in reversed(requests) if request.response_schema),
                {"type": "object"},
            ),
            "output_mapping": [],
            "preconditions": [],
            "confirmed": not capability.provisional,
            "confidence": 1.0,
            "requires_human_confirm": capability.explicit_confirmation,
            "evidence": [{"transaction_id": capability.transaction_id}],
            "status": "provisional" if capability.provisional else "confirmed",
            "locked": False,
            "updated_by": capability.origin,
            "risk_level": capability.risk_level.value,
            "execution_enabled": capability.execution_enabled,
        })

    capability_name = {
        capability.capability_id: capability.name for capability in compilation.capabilities
    }
    relations = [{
        "relation_id": relation.relation_id,
        "type": relation.relation_type.value,
        "mode": "caller" if relation.relation_type in {
            RelationType.CALLER_SELECTION,
            RelationType.CALLER_DECISION,
            RelationType.EXTERNAL_TRANSFORM,
        } else "automatic",
        "from_capability": capability_name.get(
            relation.from_capability_id, relation.from_capability_id
        ),
        "from_output": relation.from_path or "",
        "to_capability": capability_name.get(
            relation.to_capability_id, relation.to_capability_id
        ),
        "to_input": relation.to_path or "",
        "transform_owner": "caller" if relation.relation_type is not RelationType.DATA_FLOW else "skill",
        "requires_user_confirmation": not relation.confirmed,
        "confidence": relation.confidence,
        "confirmed": relation.confirmed,
        "reason": "; ".join(relation.evidence),
    } for relation in compilation.relations]

    request_rows: list[dict[str, Any]] = []
    for index, request in enumerate(compilation.requests):
        analysis = analysis_by_id[request.request_id]
        capability = capability_by_request.get(request.request_id)
        request_fields = [
            _field_row(field, step_id=step_ids.get(request.request_id) or _step_id(request.request_id), contracts=contracts)
            for field in fields_by_request.get(request.request_id, ())
        ]
        safe = _safe_request_projection(request, request_fields, contracts)
        request_rows.append({
            "request_index": index,
            "request_id": request.request_id,
            "request_definition_id": contracts.request_definition_ids.get(request.request_id)
            if contracts else None,
            "observation_id": contracts.observation_ids.get(request.request_id)
            if contracts else None,
            "method": request.method,
            "url": _url_with_safe_query(request.url, safe["query"]),
            "path": request.path,
            "role": request.disposition.value,
            "disposition": request.disposition.value,
            "keep": request.capability_eligible,
            "reason": request.disposition_reason,
            "confidence": analysis.confidence,
            "response_status": request.response_status,
            "response_json": safe["response"],
            "response_schema": request.response_schema,
            "sequence": request.sequence,
            "state": request.disposition.value,
            "materialized_step_id": step_ids.get(request.request_id),
            "used_by_capabilities": [capability.name] if capability is not None else [],
            "headers": safe["headers"],
            "post_data": safe["body"],
            "content_type": _content_type(request.headers),
            "query": safe["query"],
        })

    risk = max(
        (capability.risk_level for capability in compilation.capabilities),
        key=_risk_value,
        default=CapabilityRisk.L1,
    )
    revision = int(session.get("current_revision", compilation.source_revision) or 0)
    title = str(
        session.get("title")
        or (compilation.capabilities[0].title if compilation.capabilities else "录制草稿")
    )
    selected = [row for row in request_rows if row["keep"]]
    candidate_reads = [
        row for row in request_rows
        if row["role"] in {
            RequestDisposition.OPTION_SOURCE.value,
            RequestDisposition.SUPPORTING.value,
            RequestDisposition.REVIEW_CANDIDATE.value,
        }
    ]
    filtered = [row for row in request_rows if not row["keep"]]

    global_target_uuid = str(
        uuid5(
            contracts.field_registry.lineage_id if contracts else NAMESPACE_URL,
            f"review-global:{compilation.tenant}:{compilation.recording_id}",
        )
    )

    def review_item(issue: Any) -> dict[str, Any]:
        target_kind = "global"
        target_uuid = global_target_uuid
        target: dict[str, Any] = {
            "kind": target_kind,
            "target_uuid": target_uuid,
        }
        if contracts and issue.field_contract_id:
            field_uuid = contracts.field_uuids.get(issue.field_contract_id)
            if field_uuid:
                target_kind = "field"
                target_uuid = field_uuid
                target = {
                    "kind": target_kind,
                    "target_uuid": target_uuid,
                    "field_uuid": field_uuid,
                    "field_contract_id": issue.field_contract_id,
                }
        elif contracts and issue.capability_id:
            capability_uuid = contracts.capability_uuids.get(issue.capability_id)
            if capability_uuid:
                target_kind = "capability"
                target_uuid = capability_uuid
                target = {
                    "kind": target_kind,
                    "target_uuid": target_uuid,
                    "capability_uuid": capability_uuid,
                    "capability_id": issue.capability_id,
                }
        elif contracts and issue.request_id:
            step_uuid = contracts.step_uuids.get(issue.request_id)
            request_definition_id = contracts.request_definition_ids.get(issue.request_id)
            if step_uuid:
                target_kind = "step"
                target_uuid = step_uuid
                target = {
                    "kind": target_kind,
                    "target_uuid": target_uuid,
                    "step_uuid": step_uuid,
                    "request_definition_id": request_definition_id,
                    "request_id": issue.request_id,
                }
        fingerprint_payload = {
            "code": issue.code,
            "severity": issue.severity.value,
            "target_kind": target_kind,
            "target_uuid": target_uuid,
        }
        fingerprint = "sha256:" + hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        issue_id = str(
            uuid5(UUID(target_uuid), f"review:{issue.code}:{fingerprint}")
        )
        return {
            "id": issue_id,
            "issue_id": issue_id,
            "fingerprint": fingerprint,
            "revision": revision,
            "target_uuid": target_uuid,
            "type": issue.code,
            "severity": issue.severity.value,
            "title": issue.code.replace("_", " "),
            "reason": issue.message,
            "resolved": False,
            "target": target,
        }

    return {
        "flow_id": compilation.recording_id,
        "recording_id": compilation.recording_id,
        "tenant": compilation.tenant,
        "lineage_id": str(contracts.field_registry.lineage_id) if contracts else None,
        "recording_contract_version": 1 if contracts else 0,
        "capture_generation": contracts.capture_store.capture_generation
        if contracts else 0,
        "capture_store": contracts.capture_store.model_dump(mode="json")
        if contracts else None,
        "field_registry": contracts.field_registry.model_dump(mode="json")
        if contracts else None,
        "evidence_graph_summary": contracts.graph_summary() if contracts else None,
        "subsystem": str(session.get("subsystem") or ""),
        "title": title,
        "business_description": str(session.get("business_description") or ""),
        "risk_level": risk.value,
        "schema_version": "recording-v3.1",
        "revision": revision,
        "transactions": [
            {
                "transaction_uuid": transaction.transaction_id,
                "transaction_id": transaction.transaction_id,
                "action_id": transaction.action_id,
                "action_label": transaction.action_label,
                "request_ids": list(transaction.request_ids),
                "first_sequence": transaction.first_sequence,
                "last_sequence": transaction.last_sequence,
            }
            for transaction in compilation.transactions
        ],
        "steps": steps,
        # Only proven automatic data-flow relations become step links.  Caller
        # selection/decision remains a capability relation, never fake sequence.
        "links": [
            {
                "link_id": relation.relation_id,
                "source_step_id": step_ids[relation.from_request_id],
                "source_path": relation.from_path or "",
                "target_step_id": step_ids[relation.to_request_id],
                "target_path": relation.to_path or "",
                "confirmed": relation.confirmed,
                "confidence": relation.confidence,
                "reason": "; ".join(relation.evidence),
            }
            for relation in compilation.relations
            if relation.relation_type is RelationType.DATA_FLOW
            and relation.from_request_id in step_ids
            and relation.to_request_id in step_ids
        ],
        "capabilities": capabilities,
        "capability_relations": relations,
        "review_items": [
            review_item(issue) for issue in compilation.validation.issues
        ],
        "request_facts": {
            "requests": request_rows,
            "diagnostics": [],
            "page_events": [],
            "option_sources": [
                row for row in request_rows
                if row["role"] == RequestDisposition.OPTION_SOURCE.value
            ],
            "analysis": {
                row["request_id"]: {
                    "method": row["method"],
                    "path": row["path"],
                    "role": row["role"],
                    "keep": row["keep"],
                    "reason": row["reason"],
                    "confidence": row["confidence"],
                    "disposition": row["disposition"],
                }
                for row in request_rows
            },
            "usage": {
                row["request_id"]: {
                    "request_id": row["request_id"],
                    "materialized_step_id": row["materialized_step_id"],
                    "state": row["state"],
                    "used_by_capabilities": row["used_by_capabilities"],
                }
                for row in request_rows
            },
        },
        "meta": {
            "recording_engine": "playwright_v3",
            "content_hash": compilation.content_hash,
            "lineage_id": str(contracts.field_registry.lineage_id) if contracts else None,
            "capture_generation": contracts.capture_store.capture_generation
            if contracts else 0,
            "raw_javascript_in_pi": False,
            "request_roles": [
                {
                    "index": row["request_index"],
                    "method": row["method"],
                    "path": row["path"],
                    "role": row["role"],
                    "keep": row["keep"],
                    "reason": row["reason"],
                    "confidence": row["confidence"],
                }
                for row in request_rows
            ],
            "capability_model": {
                "status": "deterministic_ready",
                "source": "recording-v3-compiler",
                "generated_count": len(capabilities),
            },
            "request_graph": {
                "all_requests": request_rows,
                "selected_steps": selected,
                "candidate_reads": candidate_reads,
                "filtered_requests": filtered,
            },
            "versions": [{
                "version": revision,
                "action": "compile",
                "reason": "recording-v3 deterministic compilation",
                "created_at": compilation.compiled_at.isoformat(),
                "summary": {
                    "requests": len(request_rows),
                    "capabilities": len(capabilities),
                    "passed": compilation.validation.passed,
                },
            }],
            "current_version": revision,
        },
    }
