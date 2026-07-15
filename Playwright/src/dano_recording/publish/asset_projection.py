"""Project one frozen workbench revision into a safe V3 PageScript asset."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.executability import check_executability

from .candidate import ReleaseCandidate

_SECRET_KEYS = {
    "authorization", "cookie", "password", "passwd", "secret", "clientsecret",
    "token", "accesstoken", "refreshtoken", "apikey", "storage_state", "storagestate",
}
_RAW_SCRIPT_KEYS = {
    "scriptsource", "rawjavascript", "javascript_source", "javascriptsource",
    "sourcescontent", "source_map_content", "sourcemapcontent", "scripttext",
}
_DROP_HEADERS = {
    "authorization", "cookie", "proxy-authorization", "x-api-key", "x-auth-token",
    "host", "content-length", "connection", "origin", "referer", "transfer-encoding",
}
_CREDENTIAL_HEADERS = {
    "authorization", "cookie", "proxy-authorization", "x-api-key", "x-auth-token",
}
_SENSITIVE_HEADER = re.compile(
    r"(?:auth|cookie|credential|password|secret|session|token|csrf|xsrf|"
    r"user[-_]?id|employee|creator|owner|email|phone|tenant[-_]?id)",
    re.IGNORECASE,
)
_WRITES = {"POST", "PUT", "PATCH", "DELETE"}
_RISK_ORDER = {f"L{value}": value for value in range(1, 6)}
_REFERENCE = re.compile(r"^\s*\{\{[^{}]+\}\}\s*$")
_OMIT = object()
_USER_IDENTITY = re.compile(
    r"(?:^|_)(?:user|creator|owner|operator|employee)(?:_|$).*id$|"
    r"^(?:user|creator|owner|operator|employee)id$",
    re.IGNORECASE,
)
_TENANT_IDENTITY = re.compile(r"(?:^|_)tenant(?:_|$).*id$|^tenantid$", re.IGNORECASE)
_PUBLISH_REDACTION = RedactionPolicy()
_HUMAN_TEXT_KEYS = {
    "title", "description", "help", "message", "detail", "note", "reason",
    "reasons", "label", "goal", "intent", "successcriteria", "fielddocs",
    "reviewadvice",
}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _is_reference(value: Any) -> bool:
    return isinstance(value, str) and (
        bool(_REFERENCE.match(value))
        or value.startswith(("vault://", "secret://", "env://"))
    )


def _decision_value(row: dict[str, Any], axis: str) -> Any:
    """Read the effective value of one V3 field axis.

    ``axis_decisions`` is the authoritative projection of FieldRegistry.  The
    older flat keys remain fallbacks only so an unmigrated revision can still
    be published as ``published_unverified`` instead of being silently
    rewritten with different semantics.
    """

    decisions = row.get("axis_decisions") or row.get("decisions") or {}
    if not isinstance(decisions, dict):
        return None
    decision = decisions.get(axis)
    if isinstance(decision, dict) and "value" in decision:
        return deepcopy(decision["value"])
    return deepcopy(decision) if decision is not None else None


def _required_state(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _canonical_provider(row: dict[str, Any]) -> dict[str, Any]:
    """Prefer SourceBinding/RequiredContract over the legacy provider view."""

    source = _decision_value(row, "source_binding")
    if not isinstance(source, dict):
        source = deepcopy(row.get("source_binding") or {})
    required = row.get("required_contract") or {}
    if not isinstance(required, dict):
        required = {}
    if not source:
        source = deepcopy(required.get("provider") or {})
    if not source:
        source = deepcopy(row.get("value_provider") or row.get("source") or {})
    if not isinstance(source, dict):
        source = {}
    kind = str(source.get("kind") or row.get("source_kind") or "").strip().lower()
    aliases = {
        "caller": "caller",
        "caller_input": "caller",
        "user_input": "caller",
        "default": "default",
        "constant": "constant",
        "page_context": "runtime_context",
        "request_header": "runtime_context",
        "runtime_context": "runtime_context",
        "previous_response": "dependency_response",
        "dependency_response": "dependency_response",
        "computed": "derived",
        "derived": "derived",
        "option_source": "option_source",
        "unresolved": "unresolved",
        "unknown": "unresolved",
    }
    source["kind"] = aliases.get(kind, kind or "unresolved")
    if source["kind"] in {"constant", "default"} and "value" not in source:
        if "constant" in source:
            source["value"] = deepcopy(source.get("constant"))
        elif "default_value" in row:
            source["value"] = deepcopy(row.get("default_value"))
    if source["kind"] == "dependency_response":
        source.setdefault(
            "request_definition_id",
            source.get("source_request_id") or source.get("request_id"),
        )
        source.setdefault("response_path", source.get("source_path"))
    return source


def _sanitize_publish_text(
    node: Any,
    *,
    path: str = "",
    human_text: bool = False,
) -> Any:
    """Redact PII in human text while rejecting embedded credentials."""

    if isinstance(node, dict):
        return {
            str(key): _sanitize_publish_text(
                value,
                path=f"{path}{key}.",
                human_text=human_text or _key(key) in _HUMAN_TEXT_KEYS,
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [
            _sanitize_publish_text(
                value,
                path=f"{path}{index}.",
                human_text=human_text,
            )
            for index, value in enumerate(node)
        ]
    if isinstance(node, str) and human_text:
        if _PUBLISH_REDACTION.contains_credential_text(node):
            raise ValueError(f"published recording contains credential text at {path.rstrip('.')}")
        return _PUBLISH_REDACTION.redact_text(node)
    return node


def _assert_no_secrets(node: Any, path: str = "") -> None:
    """Reject embedded credentials and all raw JavaScript/source-map payloads."""

    if isinstance(node, dict):
        for key, value in node.items():
            normalized = _key(key)
            if normalized in _RAW_SCRIPT_KEYS:
                raise ValueError(f"published recording contains raw script at {path + str(key)}")
            if normalized in _SECRET_KEYS and value not in (None, "", {}, []) and not _is_reference(value):
                raise ValueError(f"published recording contains secret material at {path + str(key)}")
            _assert_no_secrets(value, f"{path}{key}.")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_no_secrets(value, f"{path}{index}.")
    elif isinstance(node, str):
        if _PUBLISH_REDACTION.contains_credential_text(node):
            raise ValueError(f"published recording contains credential text at {path.rstrip('.')}")
        if _PUBLISH_REDACTION.redact_text(node) != node:
            raise ValueError(f"published recording contains unsanitized PII at {path.rstrip('.')}")


def _request_facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    raw: Any = snapshot.get("request_facts")
    if raw is None:
        raw = (snapshot.get("facts") or {}).get("requests") if isinstance(snapshot.get("facts"), dict) else []
    if isinstance(raw, dict):
        raw = raw.get("requests") or []
    if not isinstance(raw, list):
        raise ValueError("request_facts must be a list or an object containing requests")
    return [dict(item) for item in raw if isinstance(item, dict)]


def _candidate_fields(snapshot: dict[str, Any], draft: dict[str, Any]) -> Iterable[tuple[dict[str, Any], str]]:
    for source in (
        snapshot.get("effective_fields"), draft.get("effective_fields"), draft.get("fields"),
    ):
        for item in source if isinstance(source, list) else []:
            if isinstance(item, dict):
                yield item, str(item.get("step_id") or "")
    for step in draft.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or "")
        for item in step.get("params") or []:
            if isinstance(item, dict):
                yield item, step_id
    for capability in draft.get("capabilities") or []:
        if not isinstance(capability, dict):
            continue
        for key in ("fields", "inputs", "request_fields", "internal_fields", "computed_fields"):
            for item in capability.get(key) or []:
                if isinstance(item, dict):
                    yield item, str(item.get("step_id") or "")


def _infer_location(row: dict[str, Any], step: dict[str, Any] | None) -> str:
    explicit = str(row.get("location") or "").lower()
    if explicit in {"path", "query", "body", "form", "header"}:
        return explicit
    path = str(row.get("path") or row.get("wire_path") or "").removeprefix("body.")
    query = (step or {}).get("query_template") or (step or {}).get("query") or {}
    in_query = isinstance(query, dict) and path in query
    body = (step or {}).get("body_template", (step or {}).get("body"))

    def contains(value: Any, parts: list[str]) -> bool:
        if not parts:
            return True
        if isinstance(value, dict) and parts[0] in value:
            return contains(value[parts[0]], parts[1:])
        return False

    in_body = contains(body, [part for part in path.split(".") if part])
    if in_query == in_body:
        raise ValueError(f"field location is ambiguous for {path or row.get('key')}")
    return "query" if in_query else "body"


def _normalize_field(
    row: dict[str, Any], *, step_id: str, step: dict[str, Any] | None,
) -> dict[str, Any]:
    if step is None:
        raise ValueError("frozen V3 field cannot resolve its canonical runtime step")
    canonical_step_uuid = str(step.get("step_uuid") or "")
    if not canonical_step_uuid:
        raise ValueError("frozen V3 field step lacks canonical step_uuid")
    declared_step_uuid = str(row.get("step_uuid") or "")
    if declared_step_uuid and declared_step_uuid != canonical_step_uuid:
        raise ValueError("field/step canonical identity mismatch")
    canonical_request_definition_id = str(step.get("request_definition_id") or "")
    if not canonical_request_definition_id:
        raise ValueError(
            "frozen V3 field step lacks canonical request_definition_id"
        )
    declared_request_definition_id = str(row.get("request_definition_id") or "")
    if (
        declared_request_definition_id
        and declared_request_definition_id != canonical_request_definition_id
    ):
        raise ValueError("field/request definition identity mismatch")
    display_name = _decision_value(row, "display_name")
    name = str(
        display_name or row.get("public_name") or row.get("name") or row.get("key")
        or row.get("label") or row.get("display_name") or ""
    ).strip()
    if not name:
        raise ValueError("field contract lacks a public name")
    classification = _decision_value(row, "classification")
    classification = classification if classification is not None else row.get("classification")
    wire_path = str(row.get("wire_path") or row.get("path") or row.get("key") or name)
    wire_path = wire_path.removeprefix("body.")
    category = str(row.get("category") or "")
    source = _canonical_provider(row)
    source_kind = str(source.get("kind") or "unresolved")
    sensitive_constant_removed = False
    if source_kind == "constant" and str(classification or "").lower() in {
        "identity", "pii", "credential", "secret",
    }:
        resolver = None
        if _TENANT_IDENTITY.search(name):
            resolver = "runtime_context.current_tenant.id"
        elif _USER_IDENTITY.search(name):
            resolver = "runtime_context.current_user.id"
        if resolver:
            source = {"kind": "runtime_context", "runtime_resolver": resolver}
            source_kind = "runtime_context"
        else:
            # Keep the revision publishable for repair, but make it
            # deterministically unverified and erase the captured identity/PII.
            source = {"kind": "unresolved"}
            source_kind = "unresolved"
            sensitive_constant_removed = True
    if (
        source_kind == "constant"
        and (_TENANT_IDENTITY.search(name) or _USER_IDENTITY.search(name))
    ):
        resolver = (
            "runtime_context.current_tenant.id"
            if _TENANT_IDENTITY.search(name)
            else "runtime_context.current_user.id"
        )
        source = {"kind": "runtime_context", "runtime_resolver": resolver}
        source_kind = "runtime_context"
    explicit_source = any(
        isinstance(row.get(key), dict)
        for key in ("source_binding", "value_provider", "source")
    )
    if (
        source_kind == "unresolved"
        and category == "system_const"
        and not sensitive_constant_removed
        and not explicit_source
    ):
        source = {"kind": "constant", "value": deepcopy(row.get("value"))}
        source_kind = "constant"
    constant = source.get("value", source.get("constant"))
    if _key(name) in _SECRET_KEYS and source_kind == "constant" and constant not in (None, ""):
        raise ValueError(f"constant credential field cannot be published: {name}")
    choice = deepcopy(row.get("choice_contract") or {})
    if not isinstance(choice, dict):
        choice = {}
    enum_binding = _decision_value(row, "enum_binding")
    if enum_binding is None:
        enum_binding = deepcopy(row.get("enum_binding") or row.get("enum_evidence"))
    if isinstance(enum_binding, dict):
        # Keep evidence and coverage separate from the observed option sample;
        # runtime resolution consumes this contract directly.
        choice["enum_evidence"] = deepcopy(enum_binding)
        for key in (
            "mapping_coverage", "selected_pair_verified", "observed_mapping_complete",
            "snapshot_coverage", "source_scope", "source_query", "evidence_ids",
        ):
            if key in enum_binding:
                choice.setdefault(key, deepcopy(enum_binding[key]))
    options = deepcopy(
        choice.get("typed_options") or choice.get("options") or row.get("enum_options") or []
    )
    if options:
        choice["typed_options"] = options
    has_enum_evidence = isinstance(choice.get("enum_evidence"), dict) or any(
        key in choice
        for key in (
            "mapping_coverage", "selected_pair_verified", "observed_mapping_complete",
            "snapshot_coverage", "source_scope", "source_query", "evidence_ids",
        )
    )
    if (choice or source_kind == "option_source") and not has_enum_evidence:
        # V3 never publishes a compatibility/legacy enum contract.  Older
        # capture shapes are migrated to explicit unknown evidence so the
        # executability gate can require repair rather than overclaiming that
        # an observed option sample is a complete domain.
        evidence = {
            "selected_pair_verified": False,
            "observed_mapping_complete": False,
            "mapping_coverage": "unknown",
            "snapshot_coverage": {
                "kind": "unknown",
                "observed_count": len(options),
                "truncated": bool(options),
            },
            "source_scope": {},
            "evidence_ids": [],
        }
        choice["enum_evidence"] = evidence
        choice["mapping_coverage"] = "unknown"
    location = _infer_location(row, step)
    exposure = _decision_value(row, "exposure")
    exposed = bool(
        exposure if exposure is not None else row.get(
            "exposed", row.get(
                "exposed_to_caller", row.get(
                    "exposed_to_user", source_kind in {"caller", "option_source"},
                ),
            ),
        )
    )
    if location == "header":
        header_name = wire_path.lower()
        if header_name in _CREDENTIAL_HEADERS:
            source = {
                "kind": "credential_store",
                "runtime_resolver": f"credential_headers.{header_name}",
            }
            source_kind = "credential_store"
            exposed = False
            classification = classification or "credential"
        elif header_name in _DROP_HEADERS:
            source = {"kind": "transport_context"}
            source_kind = "transport_context"
            exposed = False
        else:
            safe_recorded_headers = _safe_headers((step or {}).get("headers"))
            recorded = next(
                (
                    value for key, value in safe_recorded_headers.items()
                    if str(key).lower() == header_name
                ),
                None,
            )
            if recorded is not None:
                source = {"kind": "constant", "value": recorded}
                source_kind = "constant"
                exposed = False
    required_contract = deepcopy(row.get("required_contract") or {})
    if not isinstance(required_contract, dict):
        required_contract = {}
    wire_required_axis = _decision_value(row, "wire_required")
    caller_required_axis = _decision_value(row, "caller_required")
    wire_required = _required_state(
        wire_required_axis if wire_required_axis is not None else required_contract.get(
            "wire_required", row.get("wire_required", row.get("required_by_wire")),
        )
    )
    caller_required = _required_state(
        caller_required_axis if caller_required_axis is not None else required_contract.get(
            "caller_required", row.get("caller_required", row.get("required")),
        )
    )
    required_contract.update({
        "wire_required": "unknown" if wire_required is None else str(wire_required).lower(),
        "caller_required": "unknown" if caller_required is None else str(caller_required).lower(),
        "provider": deepcopy(source),
    })
    field_uuid = str(row.get("field_uuid") or "")
    if not field_uuid:
        raise ValueError("frozen V3 field lacks canonical field_uuid")
    legacy_id = str(row.get("field_contract_id") or row.get("field_id") or "")
    stable_id = field_uuid
    business_type = _decision_value(row, "business_type")
    return {
        "field_uuid": field_uuid,
        "field_contract_id": stable_id,
        "legacy_field_contract_id": legacy_id if legacy_id and legacy_id != stable_id else "",
        "lineage_id": str(row.get("lineage_id") or ""),
        "aliases": deepcopy(row.get("aliases") or []),
        "axis_decisions": deepcopy(row.get("axis_decisions") or row.get("decisions") or {}),
        "request_definition_id": canonical_request_definition_id,
        # Observation/request ids are aliases and provenance only.  They must
        # never be promoted into the immutable request-definition identity.
        "request_id": str(row.get("request_id") or step.get("request_id") or ""),
        "step_id": step_id,
        "step_uuid": canonical_step_uuid,
        "public_name": name,
        "description": str(row.get("description") or row.get("help") or row.get("display_name") or ""),
        "location": location,
        "wire_path": wire_path,
        "wire_name": str(row.get("wire_name") or wire_path.rsplit(".", 1)[-1]),
        "wire_type": str(row.get("wire_type") or (row.get("wire_schema") or {}).get("type") or "any"),
        "business_type": str(business_type or row.get("business_type") or row.get("type") or "string"),
        "classification": classification if classification is not None else row.get("classification"),
        "sensitive_constant_removed": sensitive_constant_removed,
        "required": caller_required is True,
        "wire_required": "unknown" if wire_required is None else str(wire_required).lower(),
        "caller_required": "unknown" if caller_required is None else str(caller_required).lower(),
        "required_contract": required_contract,
        "exposed": exposed,
        "value_provider": source,
        "source_binding": deepcopy(source),
        "choice_contract": choice or None,
        "confirmed": bool(row.get("confirmed", True)),
        "origins": deepcopy(row.get("origins") or {}),
    }


def _field_contracts(snapshot: dict[str, Any], draft: dict[str, Any]) -> list[dict[str, Any]]:
    source_steps = [item for item in draft.get("steps") or [] if isinstance(item, dict)]
    steps_by_id = {
        str(item.get("step_id") or item.get("operation_id") or ""): item
        for item in source_steps
    }
    steps_by_uuid = {
        str(item.get("step_uuid") or ""): item for item in source_steps
        if item.get("step_uuid")
    }
    steps_by_request = {
        str(request_id): item for item in source_steps
        for request_id in (item.get("request_definition_id"), item.get("request_id"))
        if request_id
    }
    if len(steps_by_id) != len(source_steps):
        raise ValueError("frozen V3 runtime steps require unique non-empty step_id aliases")
    if len(steps_by_uuid) != len(source_steps):
        raise ValueError("frozen V3 runtime steps require unique canonical step_uuid")
    definitions = [
        str(item.get("request_definition_id") or "") for item in source_steps
    ]
    if any(not value for value in definitions) or len(set(definitions)) != len(definitions):
        raise ValueError(
            "frozen V3 runtime steps require unique canonical request_definition_id"
        )
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row, inherited_step_id in _candidate_fields(snapshot, draft):
        raw_step_id = str(row.get("step_id") or inherited_step_id)
        raw_step_uuid = str(row.get("step_uuid") or "")
        raw_request_id = str(row.get("request_definition_id") or row.get("request_id") or "")
        step = (
            steps_by_id.get(raw_step_id)
            or steps_by_uuid.get(raw_step_uuid)
            or steps_by_request.get(raw_request_id)
        )
        if step is None:
            # Captured/effective evidence may describe supporting requests that
            # are intentionally absent from the materialized runtime graph.
            # Such evidence is not a FieldWireBinding and must not be emitted.
            # Rows attached to a declared runtime step still fail closed.
            if not inherited_step_id and not raw_step_id and not raw_step_uuid:
                continue
            raise ValueError("frozen V3 field cannot resolve its canonical runtime step")
        if raw_step_id and str(step.get("step_id") or "") != raw_step_id:
            raise ValueError("field/step id alias mismatch")
        if raw_step_uuid and str(step.get("step_uuid") or "") != raw_step_uuid:
            raise ValueError("field/step canonical identity mismatch")
        declared_definition = str(row.get("request_definition_id") or "")
        if (
            declared_definition
            and str(step.get("request_definition_id") or "") != declared_definition
        ):
            raise ValueError("field/request definition identity mismatch")
        step_id = str((step or {}).get("step_id") or raw_step_id or raw_step_uuid)
        normalized = _normalize_field(row, step_id=step_id, step=step)
        field_identity = str(
            normalized["field_uuid"] or normalized["field_contract_id"] or "anonymous"
        )
        binding_identity = str(
            normalized.get("step_uuid") or normalized.get("request_definition_id")
            or normalized.get("step_id") or ""
        )
        identity = (
            field_identity,
            binding_identity,
            str(normalized["location"]),
            str(normalized["wire_path"]),
        )
        if identity in result:
            existing = result[identity]
            for key, value in normalized.items():
                if existing.get(key) in (None, "", {}, []) and value not in (None, "", {}, []):
                    existing[key] = value
        else:
            result[identity] = normalized
    return list(result.values())


def _template_for_field(
    field: dict[str, Any],
    request_to_step: dict[str, dict[str, Any]],
) -> Any:
    if field.get("sensitive_constant_removed"):
        return None
    provider = field.get("source_binding") or field["value_provider"]
    kind = str(provider.get("kind") or "unresolved").lower()
    if kind in {"constant", "default"}:
        return deepcopy(provider.get("value", provider.get("constant")))
    if kind in {"previous_response", "dependency_response"}:
        request_definition_id = str(provider.get("request_definition_id") or "")
        if not request_definition_id:
            raise ValueError(
                f"dependency-response field lacks canonical request_definition_id: "
                f"{field['public_name']}"
            )
        source = request_to_step.get(request_definition_id)
        if source is None:
            raise ValueError(
                f"dependency-response field references an unknown request definition: "
                f"{field['public_name']}"
            )
        source_step = str(source.get("step_uuid") or "")
        if not source_step:
            raise ValueError(
                f"dependency-response source lacks canonical step_uuid: {field['public_name']}"
            )
        declared_step_uuid = str(provider.get("source_step_uuid") or "")
        declared_step_id = str(provider.get("source_step_id") or "")
        if declared_step_uuid and declared_step_uuid != source_step:
            raise ValueError(
                f"dependency source identity mismatch: {field['public_name']}"
            )
        if declared_step_id and declared_step_id not in {
            source_step,
            str(source.get("step_id") or ""),
        }:
            raise ValueError(
                f"dependency source identity mismatch: {field['public_name']}"
            )
        source_path = str(provider.get("response_path") or provider.get("source_path") or "")
        if not source_step or not source_path:
            raise ValueError(f"previous-response field lacks source evidence: {field['public_name']}")
        return f"{{{{steps.{source_step}.{source_path}}}}}"
    if kind == "runtime_context":
        resolver = str(provider.get("runtime_resolver") or "").strip()
        if resolver.startswith("runtime_context."):
            resolver = "runtime." + resolver.removeprefix("runtime_context.")
        elif resolver.startswith("runtime."):
            pass
        else:
            raise ValueError(
                f"runtime-context field lacks a scoped resolver: {field['public_name']}"
            )
        if not re.fullmatch(r"[\w.\[\]-]+", resolver):
            raise ValueError(f"unsafe runtime resolver for {field['public_name']}")
        return f"{{{{{resolver}}}}}"
    expression = str(provider.get("expression") or "")
    if expression.startswith("runtime_context."):
        expression = "runtime." + expression.removeprefix("runtime_context.")
    if (
        kind in {"computed", "derived"}
        and expression.startswith(("input.", "steps.", "runtime.", "fields."))
        and re.fullmatch(r"[\w.\[\]-]+", expression)
    ):
        return f"{{{{{expression}}}}}"
    # Option-source fields still receive the caller's selected typed value.  The
    # choice contract separately records how the available values are obtained.
    if kind == "unresolved" and not field.get("exposed"):
        return _OMIT
    if kind in {"caller", "user_input", "option_source", "unresolved"} or field.get("exposed"):
        name = str(field["public_name"])
        if re.fullmatch(r"(?:[^\W\d]|_)[\w-]*", name):
            expression = f"input.{name}"
        else:
            expression = f"input[{json.dumps(name, ensure_ascii=False)}]"
        return f"{{{{{expression}}}}}"
    raise ValueError(f"unsupported runtime value provider for {field['public_name']}: {kind}")


_WIRE_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _wire_tokens(path: str) -> list[str | int]:
    raw = path.removeprefix("body.").strip()
    if raw == "$":
        return []
    if raw.startswith("$."):
        raw = raw[2:]
    elif raw.startswith("$["):
        raw = raw[1:]
    tokens: list[str | int] = []
    cursor = 0
    for match in _WIRE_TOKEN.finditer(raw):
        skipped = raw[cursor:match.start()]
        if skipped not in {"", "."}:
            raise ValueError(f"invalid field wire path: {path}")
        name, index = match.groups()
        tokens.append(int(index) if index is not None else str(name))
        cursor = match.end()
    if not tokens or raw[cursor:] not in {"", "."}:
        raise ValueError(f"invalid field wire path: {path}")
    return tokens


def _set_path(target: Any, path: str, value: Any) -> Any:
    """Set an object/array wire path and return the possibly replaced root."""

    tokens = _wire_tokens(path)
    if not tokens:
        if target not in (None, {}, []) and target != value:
            raise ValueError(f"field paths collide at {path}")
        return deepcopy(value)

    def assign(node: Any, remaining: list[str | int]) -> Any:
        if not remaining:
            if node is not None and node != value:
                raise ValueError(f"field paths collide at {path}")
            return deepcopy(value)
        token = remaining[0]
        if isinstance(token, int):
            if node is None:
                node = []
            if not isinstance(node, list):
                raise ValueError(f"field paths collide at {path}")
            while len(node) <= token:
                node.append(None)
            node[token] = assign(node[token], remaining[1:])
            return node
        if node is None:
            node = {}
        if not isinstance(node, dict):
            raise ValueError(f"field paths collide at {path}")
        node[token] = assign(node.get(token), remaining[1:])
        return node

    return assign(target, tokens)


def _sanitize_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.username or parts.password:
        raise ValueError("published request URL contains credentials")
    # Query parameters are compiled from field contracts exactly once.
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _safe_start_url(value: str) -> str:
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.username or parts.password:
        raise ValueError("start_url contains credentials")
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        if _key(key) in _SECRET_KEYS and val:
            raise ValueError(f"start_url contains secret query parameter: {key}")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _safe_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in headers.items()
        if str(key).lower() not in _DROP_HEADERS
        and not _SENSITIVE_HEADER.search(str(key))
        and value is not None
    }


def _safe_credential_ref(value: Any) -> str:
    reference = str(value or "").strip()
    if not reference:
        return ""
    if reference.startswith((
        "vault://", "secret://", "credential-store://", "credential_store.resolve:",
    )):
        return reference
    raise ValueError("credential_ref must be an opaque credential-store reference")


def _compile_steps(
    draft: dict[str, Any], api_request: dict[str, Any], fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_steps = list(draft.get("steps") or api_request.get("steps") or [])
    if not source_steps and api_request.get("method"):
        source_steps = [api_request]
    if not source_steps:
        raise ValueError("frozen revision has no materialized operations")
    by_step: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        by_step.setdefault(str(field.get("step_id") or ""), []).append(field)
    request_to_step = {
        str(step.get("request_definition_id")): step
        for step in source_steps
        if isinstance(step, dict) and step.get("request_definition_id")
    }
    result: list[dict[str, Any]] = []
    for raw in source_steps:
        if not isinstance(raw, dict):
            raise ValueError("runtime step must be an object")
        step_uuid = str(raw.get("step_uuid") or "")
        if not step_uuid:
            raise ValueError("frozen V3 runtime step lacks canonical step_uuid")
        step_id = str(raw.get("step_id") or raw.get("operation_id") or step_uuid)
        if not step_id:
            raise ValueError("runtime step lacks both canonical UUID and stable step id")
        request_definition_id = str(raw.get("request_definition_id") or "")
        if not request_definition_id:
            raise ValueError(
                f"step {step_id} lacks canonical request_definition_id"
            )
        method = str(raw.get("method") or "GET").upper()
        url = str(raw.get("url") or raw.get("path") or "")
        if not url:
            raise ValueError(f"step {step_id} lacks a URL/path")
        query: dict[str, Any] = {}
        body: Any = None
        body_fields = 0
        headers = _safe_headers(raw.get("headers"))
        required_credential_headers: list[str] = []
        path_or_url = _sanitize_url(url)
        normalized_fields: list[dict[str, Any]] = []
        for field in by_step.get(step_id, []):
            location = field["location"]
            wire_path = field["wire_path"]
            if location == "header" and wire_path.lower() in _DROP_HEADERS:
                field["wire_template_omitted"] = True
                if wire_path.lower() in _CREDENTIAL_HEADERS:
                    field["credential_header_binding"] = True
                    required_credential_headers.append(wire_path)
                normalized_fields.append(deepcopy(field))
                continue
            template = _template_for_field(field, request_to_step)
            if template is _OMIT:
                field["wire_template_omitted"] = True
                normalized_fields.append(deepcopy(field))
                continue
            if location == "query":
                query[wire_path] = template
            elif location in {"body", "form"}:
                body = _set_path(body, wire_path, template)
                body_fields += 1
            elif location == "header":
                if wire_path.lower() in _DROP_HEADERS:
                    raise ValueError(f"credential header must use Dano runtime credentials: {wire_path}")
                headers[wire_path] = str(template)
            elif location == "path":
                marker = "{" + wire_path + "}"
                sample = raw.get("sample_inputs", {}).get(field["public_name"])
                if marker in path_or_url:
                    path_or_url = path_or_url.replace(marker, str(template))
                elif sample not in (None, "") and str(sample) in path_or_url:
                    path_or_url = path_or_url.replace(str(sample), str(template), 1)
                else:
                    raise ValueError(f"path field cannot be grounded in step URL: {wire_path}")
            normalized_fields.append(deepcopy(field))
        raw_body = raw.get("body_template", raw.get("body"))
        if raw_body is None:
            body_template: Any = None
        elif body_fields:
            body_template = body
        elif raw_body in ({}, []):
            body_template = deepcopy(raw_body)
        else:
            raise ValueError(f"step {step_id} contains an uncontracted literal body")
        step: dict[str, Any] = {
            "step_id": step_id,
            # Missing canonical identity remains missing.  It is a repairable
            # contract fault, never synthesized from an index or display name.
            "step_uuid": step_uuid,
            "request_definition_id": str(
                request_definition_id
            ),
            "request_id": str(raw.get("request_id") or ""),
            "method": method,
            "url": path_or_url,
            "path": urlsplit(path_or_url).path or str(raw.get("path") or ""),
            "query_template": query,
            "body_template": body_template,
            "content_type": str(raw.get("content_type") or "application/json"),
            "headers": headers,
            "required_credential_headers": list(dict.fromkeys(required_credential_headers)),
            "fields": normalized_fields,
            "response_schema": deepcopy(raw.get("response_schema") or {}),
        }
        if isinstance(raw.get("success_rule"), dict):
            step["success_rule"] = deepcopy(raw["success_rule"])
        if method in _WRITES:
            step["requires_confirmation"] = True
            step["risk_level"] = str(raw.get("risk_level") or "L3")
        result.append(step)
    return result


def _choice_contract(
    field: dict[str, Any],
    request_to_step: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    choice = deepcopy(field.get("choice_contract") or {})
    provider = field.get("value_provider") or {}
    if not choice and provider.get("kind") != "option_source":
        return None
    choice["public_name"] = field["public_name"]
    source_query = choice.get("source_query") or {}
    source_query = source_query if isinstance(source_query, dict) else {}
    source_request_id = str(
        provider.get("request_definition_id")
        or source_query.get("request_definition_id")
        or choice.get("source_request_id") or ""
    )
    if source_request_id:
        source = request_to_step.get(source_request_id)
        if source is None:
            raise ValueError(
                f"dynamic enum references an unknown canonical request definition: "
                f"{field['public_name']}"
            )
        source_step_id = str(source.get("step_id") or "")
        source_step_uuid = str(source.get("step_uuid") or "")
        if not source_step_uuid:
            raise ValueError(
                f"dynamic enum source lacks canonical step_uuid: {field['public_name']}"
            )
        declared_step_id = str(
            provider.get("source_step_id") or choice.get("source_step_id") or ""
        )
        declared_step_uuid = str(
            provider.get("source_step_uuid") or choice.get("source_step_uuid") or ""
        )
        if (
            (declared_step_id and declared_step_id not in {source_step_id, source_step_uuid})
            or (declared_step_uuid and declared_step_uuid != source_step_uuid)
        ):
            raise ValueError(f"enum source identity mismatch: {field['public_name']}")
        choice["source_step_id"] = source_step_id
        choice["source_step_uuid"] = source_step_uuid
        choice["source_request_id"] = source_request_id
    elif source_query:
        raise ValueError(
            f"dynamic enum source lacks canonical request_definition_id: "
            f"{field['public_name']}"
        )
    return choice


def _schema_type_for_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _typed_enum_schema(values: list[Any]) -> dict[str, Any]:
    unique: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            unique.append(deepcopy(value))
    grouped: dict[str, list[Any]] = {}
    for value in unique:
        grouped.setdefault(_schema_type_for_value(value), []).append(value)
    alternatives = [
        {"type": kind, "enum": members} for kind, members in grouped.items()
    ]
    if len(alternatives) == 1:
        return alternatives[0]
    return {"anyOf": alternatives}


def _input_schema(fields: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    seen_fields: set[tuple[str, str]] = set()
    field_names = {
        str(field.get("field_uuid") or field.get("field_contract_id") or ""):
        str(field.get("public_name") or "")
        for field in fields
        if (field.get("field_uuid") or field.get("field_contract_id"))
        and field.get("public_name")
    }
    conditional_contracts: list[dict[str, Any]] = []
    seen_required_contracts: set[tuple[str, str, str, str]] = set()
    for field in fields:
        if not field.get("exposed"):
            continue
        public_name = str(field["public_name"])
        public_identity = str(field.get("field_uuid") or field.get("field_contract_id") or "")
        identity = (public_identity, public_name)
        if identity in seen_fields:
            duplicate_required = field.get("required_contract") or {}
            if (
                field.get("required")
                and not (
                    isinstance(duplicate_required, dict)
                    and duplicate_required.get("caller_condition") is not None
                )
                and public_name not in required
            ):
                required.append(public_name)
            continue
        seen_fields.add(identity)
        kind = str(field.get("business_type") or "string").lower()
        schema_type = {
            "int": "integer", "integer": "integer", "float": "number", "number": "number",
            "bool": "boolean", "boolean": "boolean", "array": "array", "list": "array",
            "object": "object",
        }.get(kind, "string")
        schema: dict[str, Any] = {
            "type": schema_type,
            "title": public_name,
            "description": field.get("description") or "",
            "x-dano-wire-type": field.get("wire_type") or "any",
        }
        choice = field.get("choice_contract") or {}
        options = choice.get("typed_options") or []
        coverage = str(
            choice.get("mapping_coverage")
            or (choice.get("enum_evidence") or {}).get("mapping_coverage")
            or ""
        )
        # A recording snapshot is not a complete enum domain.  Only a proven
        # static domain may become a hard JSON-Schema enum.
        if options and coverage == "static_domain":
            labels = [item.get("label") for item in options if isinstance(item, dict)]
            values = [item.get("value") for item in options if isinstance(item, dict)]
            input_mode = str(choice.get("input_mode") or "label_or_value")
            if input_mode == "label":
                allowed = _typed_enum_schema(labels)
            elif input_mode == "wire_value":
                allowed = _typed_enum_schema(values)
            else:
                # Use anyOf so a label equal to its wire value is not rejected
                # as an ambiguous oneOf match before the enum resolver runs.
                allowed = {
                    "anyOf": [_typed_enum_schema(labels), _typed_enum_schema(values)]
                }
            schema.pop("type", None)
            schema.update(allowed)
        properties[public_name] = schema
        required_contract = field.get("required_contract") or {}
        caller_condition = (
            required_contract.get("caller_condition")
            if isinstance(required_contract, dict) else None
        )
        if field.get("required") and caller_condition is None and public_name not in required:
            required.append(public_name)
        if isinstance(required_contract, dict):
            wire_condition = required_contract.get("wire_condition")
            caller_condition = required_contract.get("caller_condition")
            provider = required_contract.get("provider") or field.get("source_binding") or {}
            provider_kind = str(provider.get("kind") or "") if isinstance(provider, dict) else ""
            contract_identity = (
                str(field.get("field_uuid") or field.get("field_contract_id") or ""),
                str(field.get("step_uuid") or field.get("step_id") or ""),
                str(field.get("location") or ""),
                str(field.get("wire_path") or ""),
            )
            if contract_identity not in seen_required_contracts and (
                wire_condition is not None
                or caller_condition is not None
                or str(required_contract.get("wire_required") or "") == "true"
            ):
                seen_required_contracts.add(contract_identity)
                conditional_contracts.append({
                    "field_uuid": contract_identity[0],
                    "public_name": public_name,
                    "provider_kind": provider_kind,
                    "wire_required": str(required_contract.get("wire_required") or "unknown"),
                    "caller_required": str(required_contract.get("caller_required") or "unknown"),
                    "wire_condition": deepcopy(wire_condition),
                    "caller_condition": deepcopy(caller_condition),
                })
    # A CanonicalField may bind to multiple wire paths.  Its public property is
    # emitted once above, while every binding keeps its independent wire
    # required condition here.
    for field in fields:
        if not field.get("exposed"):
            continue
        required_contract = field.get("required_contract") or {}
        if not isinstance(required_contract, dict):
            continue
        contract_identity = (
            str(field.get("field_uuid") or field.get("field_contract_id") or ""),
            str(field.get("step_uuid") or field.get("step_id") or ""),
            str(field.get("location") or ""),
            str(field.get("wire_path") or ""),
        )
        wire_condition = required_contract.get("wire_condition")
        caller_condition = required_contract.get("caller_condition")
        if contract_identity in seen_required_contracts or not (
            wire_condition is not None
            or caller_condition is not None
            or str(required_contract.get("wire_required") or "") == "true"
        ):
            continue
        provider = required_contract.get("provider") or field.get("source_binding") or {}
        seen_required_contracts.add(contract_identity)
        conditional_contracts.append({
            "field_uuid": contract_identity[0],
            "public_name": str(field.get("public_name") or ""),
            "provider_kind": str(provider.get("kind") or "")
            if isinstance(provider, dict) else "",
            "wire_required": str(required_contract.get("wire_required") or "unknown"),
            "caller_required": str(required_contract.get("caller_required") or "unknown"),
            "wire_condition": deepcopy(wire_condition),
            "caller_condition": deepcopy(caller_condition),
        })
    result = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    if conditional_contracts:
        result["x-dano-required-contracts"] = conditional_contracts
        result["x-dano-field-names"] = field_names
    return result


_RECORD_COLLECTIONS = {"records", "items", "results"}
_RECORD_ID_CANDIDATES = (
    "id", "recordId", "record_id", "uuid", "applicationId", "application_id",
)


def _annotate_read_record_identity(
    schema: dict[str, Any],
    capability: dict[str, Any],
    relations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach identity metadata only to fields that really exist in the response.

    The annotation never invents an ID property.  Executability validates the
    resulting marker, so an unstructured collection or an ungrounded identity
    keeps the revision unverified.
    """
    output = deepcopy(schema or {})
    properties = output.get("properties") if isinstance(output.get("properties"), dict) else {}
    aliases = {
        str(capability.get(key) or "")
        for key in ("name", "kind", "capability_id", "capability_uuid")
    } - {""}
    for output_name, collection in properties.items():
        if output_name not in _RECORD_COLLECTIONS or not isinstance(collection, dict):
            continue
        if collection.get("type") != "array":
            continue
        items = collection.get("items") if isinstance(collection.get("items"), dict) else {}
        item_properties = (
            items.get("properties") if isinstance(items.get("properties"), dict) else {}
        )
        if not item_properties:
            continue
        existing = str(collection.get("x-record-id-field") or "").strip()
        if existing:
            # Preserve an explicit invalid marker so executability can report
            # the contract fault instead of silently changing published meaning.
            continue
        grounded: set[str] = set()
        pattern = re.compile(
            rf"^{re.escape(str(output_name))}\[\]\.([A-Za-z_][A-Za-z0-9_]*)$"
        )
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            if str(relation.get("from_capability") or "") not in aliases:
                continue
            match = pattern.fullmatch(str(relation.get("from_output") or ""))
            if match and match.group(1) in item_properties:
                grounded.add(match.group(1))
        record_id = next(iter(grounded)) if len(grounded) == 1 else ""
        if not record_id:
            record_id = next((
                name for name in _RECORD_ID_CANDIDATES
                if name in item_properties
                and str((item_properties.get(name) or {}).get("type") or "")
                in {"string", "integer", "number"}
            ), "")
        if record_id:
            collection["x-record-id-field"] = record_id
    return output


def _compile_capabilities(
    source: list[dict[str, Any]], steps: list[dict[str, Any]], fields: list[dict[str, Any]],
    relations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    relations = [item for item in relations or [] if isinstance(item, dict)]
    steps_by_id = {str(step["step_id"]): step for step in steps}
    steps_by_uuid = {
        str(step.get("step_uuid")): step for step in steps if step.get("step_uuid")
    }
    request_to_step = {
        str(step.get("request_definition_id")): step
        for step in steps if step.get("request_definition_id")
    }
    fields_by_step: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        fields_by_step.setdefault(str(field.get("step_id") or ""), []).append(field)
    result: list[dict[str, Any]] = []
    for raw in source:
        if not isinstance(raw, dict):
            continue
        capability_uuid = str(raw.get("capability_uuid") or "")
        if not capability_uuid:
            raise ValueError("frozen V3 capability lacks canonical capability_uuid")
        refs = [dict(item) for item in raw.get("request_refs") or [] if isinstance(item, dict)]
        declared_step_uuids = [
            str(value) for value in raw.get("step_uuids") or [] if str(value)
        ]
        if not declared_step_uuids:
            raise ValueError("frozen V3 capability lacks canonical step_uuids")
        missing_step_uuids = [
            value for value in declared_step_uuids if value not in steps_by_uuid
        ]
        if missing_step_uuids:
            raise ValueError(
                f"frozen V3 capability references missing step_uuid: {missing_step_uuids}"
            )
        declared_step_ids = [str(value) for value in raw.get("step_ids") or [] if str(value)]
        resolved_steps = [steps_by_uuid[value] for value in declared_step_uuids]
        if declared_step_ids and len(declared_step_ids) != len(declared_step_uuids):
            raise ValueError("frozen V3 capability step_uuid/step_id lengths differ")
        for index, step_id in enumerate(declared_step_ids):
            if steps_by_id.get(step_id) is not resolved_steps[index]:
                raise ValueError("frozen V3 capability step_uuid/step_id references differ")
        for item in refs:
            ref_uuid = str(item.get("step_uuid") or "")
            ref_id = str(item.get("step_id") or "")
            if not ref_uuid:
                raise ValueError("frozen V3 request_ref lacks canonical step_uuid")
            if ref_uuid not in steps_by_uuid:
                raise ValueError(f"frozen V3 request_ref references missing step_uuid: {ref_uuid}")
            if ref_id and steps_by_id.get(ref_id) is not steps_by_uuid[ref_uuid]:
                raise ValueError("frozen V3 request_ref step_uuid/step_id references differ")
        executable = [str(step["step_id"]) for step in resolved_steps]
        executable_uuids = declared_step_uuids
        scoped_fields = [field for step_id in executable for field in fields_by_step.get(step_id, [])]
        choice_contracts = [
            contract for field in scoped_fields
            if (contract := _choice_contract(field, request_to_step)) is not None
        ]
        clean_refs = [{
            key: deepcopy(item[key]) for key in (
                "request_id", "request_definition_id", "step_id", "step_uuid", "usage",
                "role", "sequence", "confirmed", "pinned", "origin",
            ) if key in item
        } for item in refs if item.get("step_uuid") or item.get("step_id")]
        for contract in choice_contracts:
            source_step = steps_by_id.get(str(contract.get("source_step_id") or ""))
            if source_step is not None and source_step.get("step_uuid"):
                contract.setdefault("source_step_uuid", str(source_step["step_uuid"]))
        risk = str(raw.get("risk_level") or "L1").upper()
        if any(step["method"] in _WRITES for step in steps if step["step_id"] in executable):
            risk = max((risk, "L3"), key=lambda value: _RISK_ORDER.get(value, 0))
        raw_kind = str(raw.get("kind") or ("workflow" if len(executable) > 1 else "operation"))
        kind = "submit" if raw_kind == "submit_batch" else raw_kind
        raw_name = str(raw.get("name") or raw_kind or "unnamed_capability")
        name = "submit" if raw_name == "submit_batch" else raw_name
        read_only = bool(resolved_steps) and all(
            str(step.get("method") or "").upper() in {"GET", "HEAD"}
            for step in resolved_steps
        )
        output_schema = deepcopy(raw.get("output_schema") or {})
        if read_only:
            output_schema = _annotate_read_record_identity(
                output_schema,
                {
                    **raw,
                    "name": name,
                    "kind": kind,
                    "capability_uuid": capability_uuid,
                },
                relations,
            )
        policy_flags = [
            raw.get(flag) for flag in ("requires_confirmation", "requires_human_confirm")
            if flag in raw
        ]
        requires_confirmation = (
            any(not isinstance(value, bool) for value in policy_flags)
            or any(value is True for value in policy_flags)
            or risk in {"L3", "L4", "L5"}
        )
        execution_enabled = (
            True if "execution_enabled" not in raw
            else raw.get("execution_enabled") is True
        )
        result.append({
            "capability_id": str(raw.get("capability_id") or capability_uuid),
            "capability_uuid": capability_uuid,
            "name": name,
            "title": str(raw.get("title") or raw.get("intent") or raw.get("name") or ""),
            "kind": kind,
            "step_ids": executable,
            "step_uuids": executable_uuids,
            "request_refs": clean_refs,
            "fields": deepcopy(scoped_fields),
            "choice_contracts": choice_contracts,
            "input_schema": _input_schema(scoped_fields),
            "output_schema": output_schema,
            "read_only": read_only,
            "risk_level": risk,
            "requires_confirmation": requires_confirmation,
            "execution_enabled": execution_enabled,
            "confirmed": raw.get("confirmed") is True,
        })
    return result


def project_asset(snapshot: dict[str, Any], *, revision: int) -> ReleaseCandidate:
    frozen = deepcopy(snapshot)
    draft = deepcopy(frozen.get("draft") or frozen)
    raw_api = deepcopy(
        frozen.get("compiled_api_request")
        or draft.get("compiled_api_request")
        or draft.get("api_request")
        or {}
    )
    captured_fields = _field_contracts(frozen, draft)
    steps = _compile_steps(draft, raw_api, captured_fields)
    runtime_step_ids = {str(step["step_id"]) for step in steps}
    fields = [
        field for field in captured_fields
        if str(field.get("step_id") or "") in runtime_step_ids
    ]
    raw_capabilities = list(draft.get("capabilities") or raw_api.get("capabilities") or [])
    raw_relations = list(draft.get("capability_relations") or draft.get("relations") or [])
    capabilities = _compile_capabilities(raw_capabilities, steps, fields, raw_relations)
    start_url = _safe_start_url(str(frozen.get("start_url") or draft.get("start_url") or ""))
    origin_parts = urlsplit(start_url)
    recorded_origin = str(raw_api.get("recorded_origin") or (
        f"{origin_parts.scheme}://{origin_parts.netloc}" if origin_parts.netloc else ""
    ))
    if not recorded_origin:
        absolute = next((urlsplit(step["url"]) for step in steps if urlsplit(step["url"]).netloc), None)
        if absolute is not None:
            recorded_origin = f"{absolute.scheme}://{absolute.netloc}"
    api_request = {
        "recording_engine": "playwright_v3",
        "recorded_origin": recorded_origin,
        "allow_http": (
            raw_api.get("allow_http") is True
            or urlsplit(recorded_origin).scheme == "http"
        ),
        "timeout_s": min(max(float(raw_api.get("timeout_s") or 60), 1), 300),
        "steps": steps,
        "field_contracts": deepcopy(fields),
        "capabilities": capabilities,
        "capability_relations": deepcopy(raw_relations),
        "revision": revision,
    }
    exposed = [item for item in fields if item.get("exposed")]
    user_fields = list(dict.fromkeys(str(item["public_name"]) for item in exposed))
    required = list(dict.fromkeys(
        str(item["public_name"])
        for item in exposed
        if item.get("required") and not (
            isinstance(item.get("required_contract"), dict)
            and item["required_contract"].get("caller_condition") is not None
        )
    ))
    conditional_required = list(dict.fromkeys(
        str(item["public_name"])
        for item in exposed
        if isinstance(item.get("required_contract"), dict)
        and item["required_contract"].get("caller_condition") is not None
    ))
    risk = max(
        (str(cap.get("risk_level") or "L1") for cap in capabilities),
        key=lambda value: _RISK_ORDER.get(value, 0),
        default="L1",
    )
    if any(step["method"] in _WRITES for step in steps):
        risk = max((risk, "L3"), key=lambda value: _RISK_ORDER.get(value, 0))
    raw_action = str(draft.get("action") or frozen.get("action") or "recorded_skill")
    action = "submit" if raw_action == "submit_batch" else raw_action
    body = {
        "recording_engine": "playwright_v3",
        "actions": [],
        "action": action,
        "title": str(draft.get("title") or frozen.get("title") or "录制能力"),
        "start_url": start_url,
        "user_fields": user_fields,
        "required_fields": required,
        "conditional_required_fields": conditional_required,
        "optional_fields": [value for value in user_fields if value not in required],
        "field_docs": {item["public_name"]: str(item.get("description") or "") for item in exposed},
        "field_types": {item["public_name"]: str(item.get("business_type") or "string") for item in exposed},
        "risk_level": risk,
        "recording_mode": str(frozen.get("recording_mode") or draft.get("recording_mode") or "unknown"),
        "verification_status": "unverified",
        "verification_basis": "recording_v3_executability_contract",
        "api_request": api_request,
        "capabilities": capabilities,
        "goal": deepcopy(draft.get("goal") or {}),
        "credential_ref": _safe_credential_ref(
            frozen.get("credential_ref") or draft.get("credential_ref") or ""
        ),
    }
    executability = check_executability(frozen, body)
    verified = executability["executability_status"] == "verified"
    body.update({
        "verification_status": "verified" if verified else "unverified",
        "publication_status": "published_verified" if verified else "published_unverified",
        "direct_call_enabled": verified,
        "contract_faults": deepcopy(executability["contract_faults"]),
        "contract_fault_count": executability["contract_fault_count"],
    })
    api_request.update({
        "verification_status": body["verification_status"],
        "direct_call_enabled": verified,
        "contract_faults": deepcopy(executability["contract_faults"]),
    })
    for capability in capabilities:
        capability["execution_enabled"] = (
            capability.get("execution_enabled") is True and verified
        )
    body = _sanitize_publish_text(body)
    _assert_no_secrets(body)
    identity = {
        "recording_id": frozen.get("recording_id") or draft.get("recording_id") or "",
        "tenant": frozen.get("tenant") or draft.get("tenant") or "",
        "subsystem": frozen.get("subsystem") or draft.get("subsystem") or "",
        "action": body["action"],
        "revision": revision,
        "body": body,
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    return ReleaseCandidate(
        recording_id=str(identity["recording_id"]),
        tenant=str(identity["tenant"]),
        subsystem=str(identity["subsystem"]),
        action=body["action"],
        revision=revision,
        content_hash=digest,
        body=body,
    )
