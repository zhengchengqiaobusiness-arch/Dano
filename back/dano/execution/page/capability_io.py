"""Stage 6: capability input/output schema sync."""
from __future__ import annotations

from typing import Any
import copy
from datetime import datetime, timezone
import re
from urllib.parse import urlparse
from dano.execution.page.flow_spec_core.models import (
    CapabilityField,
    FlowCapability,
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.flow_materialization.field_contracts.option_projection import (
    _OPTION_SOURCE_KINDS,
    _enum_label_value,
    _enum_option_map_from_options,
)
from dano.execution.page.recording_facts import (
    _WRITE_METHODS,
    _looks_pagination_field,
    _recording_evidence_matches_request,
    _schema_from_response_value,
)
from dano.execution.page.flow_materialization.field_contracts.caller_ownership import (
    _external_capability_input,
    _param_exposed_to_caller,
    _param_requires_caller_input,
)
from dano.execution.page.flow_materialization.field_contracts.dynamic_array import (
    _caller_keys_for_object_rows,
    _recorded_object_rows,
    _sync_dynamic_array_memberships,
)
from dano.execution.page.flow_materialization.request_steps import (
    _infer_wire_format,
)
from dano.execution.page.flow_materialization.links import (
    _link_is_auto_generated,
    _previous_response_source_step_id,
)
from dano.execution.page.capability_refs import _capability_public_input_step_ids


def _capability_output_fields(cap: FlowCapability) -> list[CapabilityField]:
    fields: list[CapabilityField] = []
    output_props = (cap.output_schema or {}).get("properties") or {}
    required = set((cap.output_schema or {}).get("required") or [])
    for idx, mapping in enumerate(cap.output_mapping or []):
        if not isinstance(mapping, dict):
            continue
        name = _capability_output_name(mapping, idx)
        schema = output_props.get(name) if isinstance(output_props, dict) else None
        field_type = (
            str(
                schema.get("type")
                or ("unknown" if schema.get("x-dano-untyped-response") is True else "")
            )
            if isinstance(schema, dict) else ""
        )
        fields.append(CapabilityField(
            field_id=f"output:{cap.name or cap.capability_id}:{idx}:{name}",
            scope="output",
            display_name=name,
            path=name,
            key=name,
            type=field_type or ("object" if name in {"response", "raw", "detail"} else "string"),
            required=name in required,
            step_id=str(mapping.get("step_id") or ""),
            source_kind=str(mapping.get("kind") or "final_response"),
            source=dict(mapping),
            exposed_to_caller=True,
            confidence=float(cap.confidence or 0.0),
            confirmed=bool(cap.confirmed),
        ))
    if fields:
        return fields
    props = (cap.output_schema or {}).get("properties") or {}
    required = set((cap.output_schema or {}).get("required") or [])
    # ``properties`` is a JSON object, so insertion order is not part of the
    # contract. Derive the mirrored output list in a canonical order; an
    # equivalent JSON/database round-trip must not change the release hash.
    for name in sorted(props):
        schema = props[name]
        schema = schema if isinstance(schema, dict) else {}
        fields.append(CapabilityField(
            field_id=f"output:{cap.name or cap.capability_id}:{name}",
            scope="output",
            display_name=str(schema.get("title") or name),
            path=str(name),
            key=str(name),
            type=str(
                schema.get("type")
                or ("unknown" if schema.get("x-dano-untyped-response") is True else "string")
            ),
            required=name in required,
            exposed_to_caller=True,
            confidence=float(cap.confidence or 0.0),
            confirmed=bool(cap.confirmed),
        ))
    return fields


def _capability_inputs_from_top_level_schema(
    schema: dict[str, Any],
    existing: list[CapabilityField] | None = None,
) -> list[CapabilityField]:
    """Materialize aggregate capability inputs without leaking nested row fields.

    Batch request fields live under ``entries[].*``.  Mirroring those same
    ParamFields as top-level caller inputs makes the release validator demand
    both ``entries`` and every row field, producing duplicated errors after an
    otherwise unrelated type edit.
    """
    properties = dict((schema or {}).get("properties") or {})
    required = {str(name) for name in ((schema or {}).get("required") or [])}
    old_by_name = {
        str(item.key or item.path or item.display_name): item
        for item in (existing or [])
        if not item.step_id
    }
    # JSONB preserves arrays but not object-key order. Keep the explicit input
    # array authoritative so a database round trip cannot change the release.
    names = list(dict.fromkeys([
        *(
            str(item.key or item.path or item.display_name)
            for item in (existing or [])
            if str(item.key or item.path or item.display_name) in properties
        ),
        *properties,
    ]))
    out: list[CapabilityField] = []
    for name in names:
        raw = properties[name]
        field_schema = raw if isinstance(raw, dict) else {}
        previous = old_by_name.get(str(name))
        field = previous.model_copy(deep=True) if previous is not None else CapabilityField(
            field_id=f"input:{name}",
            scope="input",
            key=str(name),
            path=str(name),
            display_name=str(name),
            source_kind="user_input",
            category="user_param",
            exposed_to_caller=True,
        )
        field.scope = "input"
        field.key = str(name)
        field.path = str(name)
        field.display_name = field.display_name or str(name)
        field.type = str(field_schema.get("type") or field.type or "string")
        field.required = str(name) in required
        field.step_id = ""
        field.exposed_to_caller = True
        out.append(field)
    return out


def _schema_for_param_type(ptype: str) -> dict[str, Any]:
    t = (ptype or "string").lower()
    if t in {"number", "integer"}:
        return {"type": "number"}
    if t == "boolean":
        return {"type": "boolean"}
    if t == "date":
        return {"type": "string", "format": "date"}
    if t == "datetime":
        return {"type": "string", "format": "date-time"}
    if t == "object":
        return {"type": "object"}
    if t == "file":
        return {"type": "string", "format": "binary"}
    if t in {"file-list", "files"}:
        return {"type": "array", "items": {"type": "string", "format": "binary"}}
    if t in {"list-enum", "array"}:
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def _business_type_for_param(param: ParamField) -> str:
    ptype = (param.type or "string").lower()
    if ptype in {"textarea", "rich_text"} or (
        ptype in {"string", "text"}
        and any(
            str(item.get("control_kind") or "").lower() == "textarea"
            for item in (param.evidence or [])
            if isinstance(item, dict)
        )
    ):
        return "textarea"
    if ptype == "list-enum":
        return "multi_enum"
    if ptype == "enum" or param.source_kind in _OPTION_SOURCE_KINDS:
        return "single_enum"
    return {
        "datetime": "datetime",
        "date": "date",
        "number": "number",
        "integer": "number",
        "boolean": "boolean",
        "array": "array",
        "object": "object",
        "file": "file",
        "file-list": "file_list",
        "files": "file_list",
    }.get(ptype, "text")


_NO_SCHEMA_DEFAULT = object()


def _schema_default_for_param(param: ParamField) -> Any:
    """Return the recorded, type-correct prompt default without inventing one.

    Defaults on normal business fields are question-card prefills.  Pagination
    is marked separately as safe to apply when omitted.  Enum request samples
    are wire values, so expose the matching human label when the evidence map
    proves one instead of leaking an internal code as the default.
    """
    # Hydrated, selected-row, and computed values are produced afresh for each
    # invocation. A value observed while editing one record is evidence of the
    # wire shape, never a reusable caller default.
    if param.source_kind in {
        "previous_response", "selected_option_field", "computed",
    }:
        return _NO_SCHEMA_DEFAULT
    # ``value`` is the sample captured in this particular recording. It proves
    # transport shape for replay, but it is not evidence of a page default.
    value = param.default_value
    if value is None and _looks_pagination_field(param.key, param.path):
        value = param.value
    if value in (None, ""):
        return _NO_SCHEMA_DEFAULT

    if param.type in {"enum", "list-enum"}:
        value_map = dict(param.enum_value_map or _enum_option_map_from_options(param.enum_options))
        if param.type == "enum":
            label = next(
                (str(name) for name, wire in value_map.items() if str(wire) == str(value)),
                None,
            )
            if label:
                return label
            option_labels = [
                str(pair[0])
                for item in (param.enum_options or [])
                if (pair := _enum_label_value(item)) is not None
            ]
            if str(value) in option_labels:
                return str(value)
            # The recording contains an internal code but no evidence-backed
            # label for it.  Do not prefill a user-facing question with that
            # code and do not guess a label by option order.
            return _NO_SCHEMA_DEFAULT
        elif isinstance(value, list):
            reverse = {str(wire): str(name) for name, wire in value_map.items()}
            if all(str(item) in reverse for item in value):
                return [reverse[str(item)] for item in value]
            return _NO_SCHEMA_DEFAULT
        return _NO_SCHEMA_DEFAULT

    if param.type in {"number", "integer"}:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value) if param.type == "integer" else value
        text = str(value).strip()
        try:
            if param.type == "integer":
                return int(text) if re.fullmatch(r"-?\d+", text) else _NO_SCHEMA_DEFAULT
            return int(text) if re.fullmatch(r"-?\d+", text) else float(text)
        except (TypeError, ValueError):
            return _NO_SCHEMA_DEFAULT
    if param.type == "boolean":
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
        return _NO_SCHEMA_DEFAULT
    if param.type in {"date", "datetime"}:
        text = str(value).strip()
        date_pattern = r"\d{4}-\d{2}-\d{2}"
        datetime_pattern = r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?"
        if param.type == "date" and re.fullmatch(date_pattern, text):
            return text
        if param.type == "datetime" and re.fullmatch(datetime_pattern, text):
            return text
        if re.fullmatch(r"\d{10}|\d{13}", text):
            seconds = int(text) / (1000 if len(text) == 13 else 1)
            observed = datetime.fromtimestamp(seconds, tz=timezone.utc)
            return observed.strftime("%Y-%m-%d" if param.type == "date" else "%Y-%m-%d %H:%M:%S")
        return _NO_SCHEMA_DEFAULT
    if param.type in {"array", "list-enum"} and not isinstance(value, list):
        return _NO_SCHEMA_DEFAULT
    if param.type == "object" and not isinstance(value, dict):
        return _NO_SCHEMA_DEFAULT
    return value if not isinstance(value, str) else value.strip()


def _apply_param_schema_default(prop: dict[str, Any], param: ParamField) -> None:
    default = _schema_default_for_param(param)
    if default is _NO_SCHEMA_DEFAULT:
        return
    prop["default"] = default
    # Only pagination is safe for the invocation layer to apply silently.
    # Other defaults exist for ask_user_question prefill and user review.
    if _looks_pagination_field(param.key, param.path):
        prop["x-dano-apply-default"] = True


def _dynamic_array_item_params(
    params: list[ParamField],
    aggregate: ParamField,
    capability_step_ids: set[str] | None,
) -> list[ParamField]:
    container = str((aggregate.source or {}).get("array_container_path") or aggregate.path or "")
    out: list[ParamField] = []
    for param in params:
        source = dict(param.source or {})
        if (
            source.get("array_item_member") is not True
            or str(source.get("array_container_path") or "") != container
            or not _param_exposed_to_caller(param, capability_step_ids)
        ):
            continue
        item = param.model_copy(deep=True)
        item.path = str(source.get("array_item_path") or param.path)
        item.key = str(source.get("array_item_key") or param.key or item.path)
        item.required = bool(source.get("array_item_required"))
        item.source = {
            key: value for key, value in source.items()
            if not key.startswith("array_item_") and key != "array_container_path"
        }
        out.append(item)
    return out


def _is_dynamic_array_input(param: ParamField) -> bool:
    source = param.source or {}
    return bool(
        str(source.get("kind") or "") == "dynamic_structure_input"
        and str(source.get("structure_kind") or "") == "array_object"
    )


def _recorded_item_json_type(value: Any) -> str:
    """Use the recorded Python type. Do not treat digit strings as numbers."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _object_array_item_schema_from_value(
    param: ParamField,
    *,
    include_required_state: bool,
) -> dict[str, Any] | None:
    """Build array<object> items from a recorded object list when member params are absent."""
    rows = _recorded_object_rows(param.value)
    if not rows:
        return None
    caller_keys = _caller_keys_for_object_rows(rows)
    if not caller_keys:
        return None
    item_params: list[ParamField] = []
    for key in caller_keys:
        sample = next((row.get(key) for row in rows if key in row), None)
        required = all(key in row and row.get(key) not in (None, "") for row in rows)
        item_params.append(ParamField(
            path=key,
            key=key,
            label=key,
            value=copy.deepcopy(sample),
            type=_recorded_item_json_type(sample),
            required=required,
            category="user_param",
            source_kind="user_input",
            exposed_to_user=True,
        ))
    return _capability_input_schema(
        item_params,
        None,
        include_required_state=include_required_state,
    )


def _required_state_for_param(
    param: ParamField,
    capability_step_ids: set[str] | None,
) -> str:
    if _param_requires_caller_input(param, capability_step_ids):
        return "required"
    observed = str((param.source or {}).get("required_state") or "").lower()
    return "optional" if observed == "optional" else "unknown"


def _merge_required_states(left: str, right: str) -> str:
    if "required" in {left, right}:
        return "required"
    if "unknown" in {left, right}:
        return "unknown"
    return "optional"


def _schema_emits_required_state(schema: dict[str, Any] | None) -> bool:
    """Keep frozen pre-tristate contracts byte-compatible when reloaded."""
    if not isinstance(schema, dict) or not (schema.get("properties") or {}):
        return True
    stack = [schema]
    while stack:
        current = stack.pop()
        if "x-dano-required-state" in current:
            return True
        properties = current.get("properties") or {}
        if isinstance(properties, dict):
            stack.extend(item for item in properties.values() if isinstance(item, dict))
        items = current.get("items")
        if isinstance(items, dict):
            stack.append(items)
    return False


def _capability_input_schema(
    params: list[ParamField],
    capability_step_ids: set[str] | None = None,
    *,
    include_required_state: bool = True,
) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    dynamic_containers = {
        str((param.source or {}).get("array_container_path") or param.path or "")
        for param in params
        if _is_dynamic_array_input(param)
    }
    for p in params:
        if str((p.source or {}).get("array_container_path") or "") in dynamic_containers and (
            p.source or {}
        ).get("array_item_member") is True:
            continue
        if not _param_exposed_to_caller(p, capability_step_ids):
            continue
        key = p.key or p.path
        if key in props:
            existing = props[key]
            if include_required_state:
                existing["x-dano-required-state"] = _merge_required_states(
                    str(existing.get("x-dano-required-state") or "unknown"),
                    _required_state_for_param(p, capability_step_ids),
                )
            candidate_business = _business_type_for_param(p)
            candidate_wire = p.wire_type or _infer_type_from_value(p.value) or "string"
            if (
                existing.get("x-dano-business-type") != candidate_business
                or existing.get("x-dano-wire-type") != candidate_wire
            ):
                existing.setdefault("x-dano-conflicts", []).append({
                    "path": p.path,
                    "business_type": candidate_business,
                    "wire_type": candidate_wire,
                })
            elif existing.get("x-flow-path") != p.path:
                paths = existing.setdefault("x-flow-paths", [existing.get("x-flow-path")])
                if p.path not in paths:
                    paths.append(p.path)
            if _param_requires_caller_input(p, capability_step_ids) and key not in required:
                required.append(key)
            continue
        props[key] = _schema_for_param_type(p.type)
        props[key]["x-flow-path"] = p.path
        props[key]["x-dano-business-type"] = _business_type_for_param(p)
        props[key]["x-dano-wire-type"] = p.wire_type or _infer_type_from_value(p.value) or "string"
        if include_required_state:
            props[key]["x-dano-required-state"] = _required_state_for_param(
                p, capability_step_ids,
            )
        wire_format = p.wire_format or _infer_wire_format(p.value)
        if wire_format:
            props[key]["x-dano-wire-format"] = wire_format
        if p.label:
            props[key]["label"] = p.label
            props[key].setdefault("title", p.label)
        if p.description or p.reason:
            props[key]["description"] = p.description or p.reason
        if (
            _external_capability_input(p, capability_step_ids)
            or p.source_kind == "external_capability_input"
        ):
            props[key]["x-dano-external-source"] = {
                "step_id": str(
                    (p.source or {}).get("source_step_id")
                    or _previous_response_source_step_id(p)
                ),
                "response_path": str(
                    (p.source or {}).get("response_path")
                    or (p.source or {}).get("path")
                    or ""
                ),
            }
        option_source = (
            p.source
            if p.source_kind == "api_option"
            else (p.source or {}).get("option_source")
        )
        if isinstance(option_source, dict) and option_source:
            props[key]["x-dano-option-source"] = copy.deepcopy(option_source)
            if option_source.get("source_url") or option_source.get("endpoint") or option_source.get("url"):
                props[key]["x-options-source"] = True
                props[key]["x-options-source-meta"] = dict(option_source)
                if option_source.get("children_key") or option_source.get("childrenField"):
                    props[key]["x-dano-tree"] = True
        _apply_param_schema_default(props[key], p)
        if _is_dynamic_array_input(p):
            item_params = _dynamic_array_item_params(params, p, capability_step_ids)
            if item_params:
                props[key]["items"] = _capability_input_schema(
                    item_params,
                    capability_step_ids,
                    include_required_state=include_required_state,
                )
            else:
                recorded_items = _object_array_item_schema_from_value(
                    p, include_required_state=include_required_state,
                )
                if recorded_items is not None:
                    props[key]["items"] = recorded_items
            props[key]["minItems"] = 1
        elif (p.type or "").lower() in {"array", "list-enum"}:
            recorded_items = _object_array_item_schema_from_value(
                p, include_required_state=include_required_state,
            )
            if recorded_items is not None:
                props[key]["items"] = recorded_items
                props[key]["minItems"] = 1
        grounded_constraints = next((
            item for item in (p.evidence or [])
            if isinstance(item, dict)
            and str(item.get("source") or "") == "recorder_dom"
            and any(name in item for name in ("minimum", "maximum"))
        ), {})
        for constraint in ("minimum", "maximum"):
            value = grounded_constraints.get(constraint)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                props[key][constraint] = value
        type_is_enum = p.type in {"enum", "list-enum"}
        selectable = type_is_enum or p.source_kind in _OPTION_SOURCE_KINDS
        dynamic_options = bool(props[key].get("x-options-source")) or (
            p.source_kind == "api_option"
            and bool(
                (p.source or {}).get("source_url")
                or (p.source or {}).get("endpoint")
                or (p.source or {}).get("url")
            )
        )
        enum_confirmed = (p.source or {}).get("enum_confirmed")
        incomplete_page_enum = (
            selectable
            and p.source_kind == "page_enum"
            and enum_confirmed is False
        )
        if type_is_enum:
            if p.type == "list-enum":
                props[key].setdefault("items", {})["format"] = "name-ref"
            else:
                props[key]["format"] = "name-ref"
        if dynamic_options:
            props[key]["x-options-source"] = True
            if isinstance(p.source, dict) and p.source and not props[key].get("x-options-source-meta"):
                props[key]["x-options-source-meta"] = dict(p.source)
        if incomplete_page_enum:
            props[key]["x-options-incomplete"] = True
        if selectable and p.enum_options:
            # API-backed people/department/dictionary choices are a recording-time
            # snapshot, not a stable caller constraint. Keep the snapshot only as
            # evidence and require a live lookup at invocation time.
            props[key]["x-options-snapshot" if (dynamic_options or incomplete_page_enum) else "x-options"] = list(p.enum_options)
            labels: list[str] = []
            wire_values: list[Any] = []
            for option in p.enum_options:
                pair = _enum_label_value(option)
                if pair:
                    labels.append(str(pair[0]))
                    if pair[1] not in (None, ""):
                        wire_values.append(pair[1])
                elif option not in (None, ""):
                    labels.append(str(option))
            if labels and not dynamic_options and not incomplete_page_enum:
                enum_values = labels if type_is_enum else (wire_values or labels)
                if p.type == "list-enum":
                    props[key].setdefault("items", {})["enum"] = enum_values
                else:
                    props[key]["enum"] = enum_values
        if selectable and p.enum_value_map:
            props[key]["x-enum-value-map"] = dict(p.enum_value_map)
        if _param_requires_caller_input(p, capability_step_ids):
            required.append(key)
    return {"type": "object", "properties": props, "required": required}


def _output_field_is_transport_only(name: Any, schema: dict[str, Any]) -> bool:
    """Keep business response fields visible while suppressing transport identities."""
    role = str((schema or {}).get("x-dano-identifier-role") or _identifier_role_for_field(name))
    if role in {"record", "process_instance"}:
        return True
    normalized = re.sub(r"[^a-z0-9]+", "", str(name or "").casefold())
    return normalized in {
        "billtype", "processdefkey", "processdefinitionkey",
        "tenantid", "userid", "deptid", "departmentid",
        "organizationid", "orgid", "creatorid", "updaterid",
        "deleted", "creator", "updater",
    }


def _schema_node_at_path(schema: dict[str, Any] | None, path: str) -> dict[str, Any] | None:
    """Resolve object/array schema paths such as ``records[].processInstanceId``."""
    raw = str(path or "").strip()
    if not raw:
        return None
    parts = [part for part in re.split(r"\.|\[\]", raw) if part]
    node: Any = schema or {}
    for part in parts:
        if not isinstance(node, dict):
            return None
        while node.get("type") == "array" and isinstance(node.get("items"), dict):
            node = node["items"]
        properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        if part not in properties:
            return None
        node = properties[part]
    return node if isinstance(node, dict) else None


def _apply_output_presentation_evidence(
    output_schema: dict[str, Any],
    evidence: list[dict[str, Any]] | None,
    *,
    sample_output: Any = None,
    input_schema: dict[str, Any] | None = None,
    field_labels: dict[str, str] | None = None,
) -> None:
    """Project recorded table headers into a query result schema.

    The page is authoritative for labels, order and visibility.  This function
    deliberately does not translate transport field names or invent business
    labels when the recorder did not observe a matching table column.
    """
    row_properties: dict[str, Any] = {}
    for field_schema in (output_schema.get("properties") or {}).values():
        if not isinstance(field_schema, dict) or field_schema.get("type") != "array":
            continue
        candidate = ((field_schema.get("items") or {}).get("properties") or {})
        if candidate:
            row_properties = candidate
            break
    if not row_properties:
        return

    for name, field_schema in row_properties.items():
        if not isinstance(field_schema, dict):
            continue
        label = str((field_labels or {}).get(name) or "").strip()
        if label and not (field_schema.get("title") or field_schema.get("label")):
            field_schema["title"] = label
        if _output_field_is_transport_only(name, field_schema):
            field_schema["x-dano-display"] = False

    groups: dict[str, list[dict[str, Any]]] = {}
    for item in evidence or []:
        if not isinstance(item, dict) or (
            item.get("kind") != "table_column"
            and item.get("control_kind") != "table_column"
        ):
            continue
        groups.setdefault(str(item.get("table_id") or "table"), []).append(item)

    def normalized(value: Any) -> str:
        return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)

    sample_rows: list[dict[str, Any]] = []

    def find_rows(value: Any) -> None:
        nonlocal sample_rows
        if sample_rows:
            return
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value[:5]):
            sample_rows = value[:5]
            return
        if isinstance(value, dict):
            for nested in value.values():
                find_rows(nested)
                if sample_rows:
                    return

    find_rows(sample_output)
    enum_labels: dict[str, dict[str, str]] = {}
    for input_field in ((input_schema or {}).get("properties") or {}).values():
        if not isinstance(input_field, dict):
            continue
        output_name = str(input_field.get("x-flow-path") or "").split(".")[-1]
        if output_name not in row_properties:
            continue
        labels: dict[str, str] = {}
        for label, wire_value in (input_field.get("x-enum-value-map") or {}).items():
            labels[str(wire_value)] = str(label)
        for option_key in ("x-options", "x-options-snapshot"):
            for option in input_field.get(option_key) or []:
                if isinstance(option, dict) and option.get("label") not in (None, ""):
                    wire_value = option.get("value", option.get("id"))
                    if wire_value not in (None, ""):
                        labels[str(wire_value)] = str(option["label"])
        if labels:
            enum_labels[output_name] = labels

    best: tuple[int, int, list[tuple[str, dict[str, Any]]], list[dict[str, Any]]] | None = None
    for columns in groups.values():
        matched: list[tuple[str, dict[str, Any]]] = []
        used: set[str] = set()
        direct_matches = 0
        sample_matches = 0
        for column in sorted(columns, key=lambda item: int(item.get("display_order") or 0)):
            aliases = {
                normalized(alias)
                for alias in [
                    column.get("field"),
                    column.get("key"),
                    *(column.get("field_aliases") or []),
                ]
                if normalized(alias)
            }
            candidates = [
                name for name in row_properties
                if name not in used and normalized(name) in aliases
            ]
            direct = len(candidates) == 1
            if not direct and sample_rows:
                visible_values = {
                    normalized(value)
                    for value in (column.get("sample_values") or [])
                    if normalized(value)
                }
                visible_epochs = {
                    int(value)
                    for value in (column.get("sample_epoch_ms") or [])
                    if isinstance(value, (int, float))
                }
                candidates = []
                for name in row_properties:
                    if name in used:
                        continue
                    raw_values = [
                        row.get(name) for row in sample_rows
                        if row.get(name) not in (None, "")
                    ]
                    rendered = {normalized(value) for value in raw_values if normalized(value)}
                    rendered.update(
                        normalized(enum_labels.get(name, {}).get(str(value)))
                        for value in raw_values
                        if enum_labels.get(name, {}).get(str(value))
                    )
                    epoch_match = any(
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and int(value if abs(value) >= 100000000000 else value * 1000) in visible_epochs
                        for value in raw_values
                    )
                    if rendered.intersection(visible_values) or epoch_match:
                        candidates.append(name)
            if len(candidates) != 1:
                continue
            used.add(candidates[0])
            matched.append((candidates[0], column))
            if direct:
                direct_matches += 1
            else:
                sample_matches += 1
        score = direct_matches * 100 + sample_matches
        if best is None or score > best[0]:
            best = (score, direct_matches, matched, columns)
    if best is None or best[0] == 0:
        return

    _score, _direct_matches, matched, _columns = best
    visible_fields = {name for name, _column in matched}
    for name, column in matched:
        field_schema = row_properties[name]
        label = str(column.get("label") or "").strip()
        if label:
            field_schema["title"] = label
        field_schema["x-dano-display"] = True
        field_schema["x-dano-display-order"] = int(column.get("display_order") or 0)
        if (
            field_schema.get("type") in {"integer", "number"}
            and column.get("value_kind") == "datetime"
        ):
            field_schema["x-dano-value-format"] = "epoch-auto"

    for name, field_schema in row_properties.items():
        if (
            name not in visible_fields
            and isinstance(field_schema, dict)
            and _output_field_is_transport_only(name, field_schema)
        ):
            field_schema["x-dano-display"] = False


def _capability_output_samples(
    capability: FlowCapability,
    step_by_id: dict[str, FlowStep],
) -> dict[str, Any]:
    samples: dict[str, Any] = {}
    for index, mapping in enumerate(capability.output_mapping or []):
        if not isinstance(mapping, dict):
            continue
        step = step_by_id.get(str(mapping.get("step_id") or ""))
        if step is None or step.response_json is None:
            continue
        path = str(mapping.get("response_path") or mapping.get("path") or "response")
        value = step.response_json
        if path not in {"", "response", "$", "."}:
            candidate = _flow_path_lookup(step.response_json, path)
            if candidate is _FLOW_PATH_MISSING:
                continue
            value = candidate
        samples[_capability_output_name(mapping, index)] = value
    if samples:
        return samples
    steps = [
        step_by_id[step_id]
        for step_id in _capability_scoped_step_ids(capability)
        if step_id in step_by_id
    ]
    response = next(
        (step.response_json for step in reversed(steps) if step.response_json is not None),
        None,
    )
    return response if isinstance(response, dict) else {}


def _sync_capability_io_schemas(spec: FlowSpec) -> FlowSpec:
    """让 capability 的输入输出 schema 始终跟当前字段/响应保持一致。"""
    if not spec.capabilities:
        return spec

    _apply_mechanical_field_contracts(spec)
    # Capability compilation happens after live semantic edits. Apply only the
    # dependencies safe for execution so a confirmed response chain wins over
    # an unsupported caller-input guess. Keep non-executable selector evidence
    # long enough to derive cross-capability relations below.
    invalidated_link_ids = {
        link.link_id for link in spec.links
        if (link.meta or {}).get("unverified_reason")
    }
    if invalidated_link_ids:
        spec.links = [
            link for link in spec.links
            if not (
                link.link_id in invalidated_link_ids
                and _link_is_auto_generated(link)
            )
        ]
        for step in spec.steps:
            for param in step.params:
                if (
                    param.source_kind == "previous_response"
                    and str((param.source or {}).get("link_id") or "") in invalidated_link_ids
                ):
                    _reset_param_source(
                        param,
                        reason="上游依赖已重定向，字段已恢复为调用输入",
                    )
    _apply_link_sources(spec.steps, executable_flow_links(spec))
    # Link materialization restores origin/defaults, then the field owner makes
    # the final caller-ownership decision. In particular, hydration must not
    # turn document IDs, row IDs or audit fields back into public inputs.
    if int((spec.meta or {}).get("stage_1_6_contract_version") or 0) >= 2:
        _apply_mechanical_field_contracts(spec)
        _sync_dynamic_array_memberships(spec)
    _normalize_capability_references(spec)
    _normalize_actionable_placeholder_param_names(spec)

    def overlay_human_titles(derived: dict[str, Any], previous: dict[str, Any], name: str) -> dict[str, Any]:
        """Keep capability-authored titles when rebuild only has machine keys."""
        out = dict(derived)
        previous_title = str(previous.get("title") or previous.get("label") or "").strip()
        derived_title = str(out.get("title") or out.get("label") or "").strip()
        if previous_title and (not derived_title or derived_title == name):
            out["title"] = previous_title
            if not out.get("label") or out.get("label") == name:
                out["label"] = previous_title
        derived_items = out.get("items")
        previous_items = previous.get("items")
        if isinstance(derived_items, dict) and isinstance(previous_items, dict):
            merged_items = overlay_human_titles(derived_items, previous_items, "items")
            derived_props = merged_items.get("properties")
            previous_props = previous_items.get("properties")
            if isinstance(derived_props, dict) and isinstance(previous_props, dict):
                merged_items["properties"] = {
                    key: overlay_human_titles(value, previous_props.get(key) or {}, key)
                    if isinstance(value, dict) else value
                    for key, value in derived_props.items()
                }
            out["items"] = merged_items
        return out

    def reconcile_schema(derived: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        """当前有效字段是契约真相；仅保留仍存在字段上的人工说明等扩展。"""
        derived = dict(derived or {"type": "object", "properties": {}, "required": []})
        current = dict(current or {})
        merged = {
            key: value for key, value in current.items()
            if key not in {"properties", "required"}
        }
        merged.update({
            key: value for key, value in derived.items()
            if key not in {"properties", "required"}
        })
        current_props = dict(current.get("properties") or {})
        props: dict[str, Any] = {}
        for name, field_schema in dict(derived.get("properties") or {}).items():
            previous = current_props.get(name)
            if isinstance(previous, dict) and isinstance(field_schema, dict):
                # Type/source keywords are fully derived from ParamField. Keeping
                # old enum/format/x-options values here makes a scalar edit export
                # as a dropdown again. Only human-facing annotations survive a
                # rebuild.
                annotations = {
                    key: value for key, value in previous.items()
                    if key in {
                        "title", "description", "examples", "deprecated",
                        "x-dano-capability-owned", "x-dano-operator-owned",
                    }
                    and key not in field_schema
                    and previous.get("x-dano-derived-from-query") is not True
                }
                props[name] = overlay_human_titles({**annotations, **field_schema}, previous, name)
            else:
                props[name] = field_schema
        for name, field_schema in current_props.items():
            if (
                name not in props
                and isinstance(field_schema, dict)
                and field_schema.get("x-dano-capability-owned") is True
                and field_schema.get("x-dano-operator-owned") is True
            ):
                props[name] = dict(field_schema)
        merged["properties"] = props
        required = [
            str(name) for name in (derived.get("required") or [])
            if str(name) in props
        ]
        for name in current.get("required") or []:
            previous = current_props.get(str(name))
            if (
                str(name) in props
                and isinstance(previous, dict)
                and previous.get("x-dano-capability-owned") is True
                and str(name) not in required
            ):
                required.append(str(name))
        merged["required"] = list(dict.fromkeys(required))
        return merged

    by_id = {s.step_id: s for s in spec.steps}

    def entity_route_terms(step: FlowStep) -> set[str]:
        operation_terms = {
            "get", "list", "page", "query", "search", "detail", "create",
            "save", "draft", "submit", "update", "edit", "delete", "cancel",
            "withdraw", "approve", "reject", "process", "start",
        }
        return {
            term for term in re.split(
                r"[^a-z0-9一-鿿]+",
                urlparse(str(step.path or step.url or "")).path.casefold(),
            )
            if len(term) > 1
            and term not in _CAPABILITY_PATH_PREFIXES
            and term not in operation_terms
        }

    for cap in spec.capabilities:
        if cap.kind == "query_status":
            option_source_ids = _option_source_step_ids(spec)
            memberships = {ref.step_id: ref for ref in (cap.request_refs or []) if ref.step_id}
            allowed_step_ids = {
                sid for sid in (cap.step_ids or [])
                if (
                    bool(memberships.get(sid) and memberships[sid].origin in {"manual", "user"} and memberships[sid].usage in {"execute", "preflight", "fact_check"})
                    or (
                        (sid not in option_source_ids or (sid in by_id and _is_business_query_step(by_id[sid])))
                        and (
                            sid not in by_id
                            or ((by_id[sid].source_meta or {}).get("role") or by_id[sid].semantic_role or "") != "read_option"
                            or _is_business_query_step(by_id[sid])
                        )
                    )
                )
            }
            for step_id in set(cap.step_ids) - allowed_step_ids:
                cap.nodes = _remove_capability_step_nodes(cap.nodes or [], step_id)
            _sync_capability_order(spec, cap)
        cap.nodes = _sanitize_capability_nodes(spec, cap)
        cap_steps = [by_id[sid] for sid in (cap.step_ids or []) if sid in by_id]
        if not cap_steps:
            continue
        public_input_step_ids = set(_capability_public_input_step_ids(cap, by_id))
        input_steps = [
            step for step in cap_steps if step.step_id in public_input_step_ids
        ]
        label_steps = list(cap_steps)
        if cap.kind == "query_status":
            query_route_terms = set().union(*(entity_route_terms(step) for step in cap_steps))
            label_steps.extend(
                step for step in spec.steps
                if step not in label_steps
                and _capability_operation_kind(step) in {
                    "create", "save_draft", "submit", "update",
                }
                and query_route_terms.intersection(entity_route_terms(step))
            )
        recorded_label_candidates: dict[str, set[str]] = {}
        for step in label_steps:
            for param in step.params or []:
                wire_name = str(param.path or "").replace("[]", "").split(".")[-1]
                label = str(param.label or param.key or "").strip()
                if (
                    wire_name
                    and label
                    and re.sub(r"[\W_]+", "", label.casefold(), flags=re.UNICODE)
                    != re.sub(r"[\W_]+", "", wire_name.casefold(), flags=re.UNICODE)
                ):
                    recorded_label_candidates.setdefault(wire_name, set()).add(label)
        recorded_field_labels = {
            name: next(iter(labels))
            for name, labels in recorded_label_candidates.items()
            if len(labels) == 1
        }
        _disambiguate_capability_param_keys(input_steps)
        params = [p for st in input_steps for p in (st.params or [])]
        include_required_state = _schema_emits_required_state(cap.input_schema)
        derived_input = _capability_input_schema(
            params,
            set(cap.step_ids or []),
            include_required_state=include_required_state,
        )
        derived_input = _expand_response_key_map_inputs(spec, cap, derived_input)
        if _capability_is_batch(spec, cap):
            derived_input = _batch_capability_input_schema(
                input_steps,
                include_required_state=include_required_state,
            )
        cap.input_schema = reconcile_schema(derived_input, cap.input_schema or {})
        if cap.kind == "query_status":
            cap.output_mapping = _query_output_mappings(cap_steps)
        mapped_output_props: dict[str, Any] = {}
        mapped_output_samples: dict[str, Any] = {}
        for mapping_idx, mapping in enumerate(cap.output_mapping or []):
            if not isinstance(mapping, dict):
                continue
            source_step = by_id.get(str(mapping.get("step_id") or ""))
            if source_step is None or source_step.response_json is None:
                continue
            response_path = str(mapping.get("response_path") or mapping.get("path") or "response")
            mapped_value = source_step.response_json
            if response_path not in {"", "response", "$", "."}:
                candidate = _flow_path_lookup(source_step.response_json, response_path)
                if candidate is not _FLOW_PATH_MISSING:
                    mapped_value = candidate
            output_name = _capability_output_name(mapping, mapping_idx)
            mapped_output_props[output_name] = _schema_from_response_value(mapped_value)
            mapped_output_samples[output_name] = mapped_value
        if mapped_output_props:
            cap.output_schema = reconcile_schema({
                "type": "object",
                "properties": mapped_output_props,
                "required": list(mapped_output_props),
            }, cap.output_schema or {})
        else:
            last_response = next((st.response_json for st in reversed(cap_steps) if st.response_json is not None), None)
            if last_response is not None:
                cap.output_schema = reconcile_schema(_schema_from_response_value(last_response), cap.output_schema or {})
            elif cap.output_mapping:
                # A write endpoint may legitimately return no captured JSON
                # body.  Its declared final-response mapping is still enough to
                # build a stable public output contract; leaving an unrelated
                # stale schema here caused a late onboarding-only failure.
                existing_fields = {
                    field.key or field.path: field
                    for field in (cap.outputs or [])
                    if field.key or field.path
                }
                fallback_props: dict[str, Any] = {}
                for mapping_idx, mapping in enumerate(cap.output_mapping or []):
                    if not isinstance(mapping, dict):
                        continue
                    name = _capability_output_name(mapping, mapping_idx)
                    field = existing_fields.get(name)
                    mapping_kind = str(mapping.get("kind") or "")
                    response_path = str(mapping.get("response_path") or mapping.get("path") or "")
                    is_full_response = bool(
                        mapping_kind == "final_response"
                        and response_path in {"", "response", "$", "."}
                    )
                    if is_full_response:
                        # No captured response means its JSON type is unknown.
                        # Declaring ``object`` with no properties fabricates a
                        # contract and made callers assume fields that were never
                        # observed. Keep a valid unconstrained JSON Schema with
                        # explicit provenance until a real response is recorded.
                        fallback_props[name] = {
                            "description": "接口原始响应；录制未捕获可推导的响应结构",
                            "x-dano-untyped-response": True,
                        }
                    else:
                        fallback_props[name] = _schema_for_param_type(
                            field.type if field is not None else (
                                "object" if name in {"response", "raw", "detail"} else "string"
                            )
                        )
                if fallback_props:
                    cap.output_schema = reconcile_schema({
                        "type": "object",
                        "properties": fallback_props,
                        "required": [],
                    }, cap.output_schema or {})
        if cap.kind == "query_status":
            table_evidence = [
                item for item in (spec.meta.get("field_evidence") or [])
                if isinstance(item, dict)
                and (
                    item.get("kind") == "table_column"
                    or item.get("control_kind") == "table_column"
                )
                and any(
                    _recording_evidence_matches_request(step.source_meta or {}, item)
                    for step in cap_steps
                )
            ]
            sample_output = mapped_output_samples or next(
                (step.response_json for step in reversed(cap_steps) if step.response_json is not None),
                None,
            )
            _apply_output_presentation_evidence(
                cap.output_schema,
                table_evidence,
                sample_output=sample_output,
                input_schema=cap.input_schema,
                field_labels=recorded_field_labels,
            )
    _ground_recorded_identifier_relations(spec, by_id)
    return sync_capability_scoped_views(spec)


def _capability_output_name(mapping: dict[str, Any], index: int) -> str:
    for key in ("field", "name", "output", "target", "key"):
        value = str(mapping.get(key) or "").strip()
        if value:
            return value.split(".")[-1]
    path = str(mapping.get("response_path") or mapping.get("path") or "").strip()
    if path and path not in {"response", "$", "."}:
        return path.replace("[]", "").split(".")[-1] or f"output_{index + 1}"
    return f"output_{index + 1}"


def _query_output_mappings(steps: list[FlowStep]) -> list[dict[str, Any]]:
    used: set[str] = set()
    mappings: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, 1):
        raw = step.name or (step.path or step.url).split("?", 1)[0].rsplit("/", 1)[-1] or f"query_{idx}"
        base = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower() or f"query_{idx}"
        if base.isdigit() or not re.search(r"[a-zA-Z_]", base):
            base = f"query_{idx}"
        name = base
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        response = step.response_json
        semantic_paths: list[tuple[str, str]] = []
        if isinstance(response, dict):
            container = response
            prefix = ""
            for wrapper in ("data", "result"):
                if isinstance(response.get(wrapper), dict):
                    container = response[wrapper]
                    prefix = f"{wrapper}."
                    break
            for field_name in list(container)[:20]:
                if not prefix and str(field_name).casefold() in {"code", "message", "msg", "success"}:
                    continue
                output_name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(field_name)).strip("_")
                if output_name.casefold() in {"list", "rows", "records"}:
                    output_name = "records"
                if len(container) == 1 and output_name.casefold() in {"value", "result", "data"}:
                    output_name = name
                if not output_name:
                    output_name = f"output_{len(semantic_paths) + 1}"
                semantic_paths.append((f"{prefix}{field_name}", output_name))
        if semantic_paths:
            for path, output_name in semantic_paths:
                mapping = {
                    "kind": "step_response",
                    "name": output_name,
                    "step_id": step.step_id,
                    "response_path": path,
                }
                # A response field has one stable public name. If several query
                # stages expose it, the later stage is the final observed result.
                previous_idx = next((
                    i for i, item in enumerate(mappings)
                    if item.get("name") == output_name
                ), -1)
                if previous_idx >= 0:
                    mappings[previous_idx] = mapping
                else:
                    mappings.append(mapping)
                used.add(output_name)
        else:
            used.add(name)
            mappings.append({
                "kind": "step_response",
                "name": name,
                "step_id": step.step_id,
                "response_path": "response",
            })
    return mappings


def _schema_path_exists(schema: dict[str, Any] | None, path: str, key: str = "") -> bool:
    """Check aggregate paths such as entries[].sealId against JSON Schema."""
    return _schema_node_at_path(schema, str(path or key or "")) is not None


def _sync_capability_output_after_step_removal(cap: FlowCapability) -> None:
    valid_step_ids = set(cap.step_ids or [])
    stale_mappings = [
        dict(mapping)
        for mapping in (cap.output_mapping or [])
        if isinstance(mapping, dict)
        and str(mapping.get("step_id") or "")
        and str(mapping.get("step_id") or "") not in valid_step_ids
    ]
    cap.output_mapping = [
        dict(mapping)
        for mapping in (cap.output_mapping or [])
        if isinstance(mapping, dict)
        and (
            not str(mapping.get("step_id") or "")
            or str(mapping.get("step_id") or "") in valid_step_ids
        )
    ]
    if valid_step_ids and not cap.output_mapping:
        final_step_id = cap.step_ids[-1]
        replacement = stale_mappings[0] if stale_mappings else {
            "kind": "final_response",
            "name": "result",
            "response_path": "response",
        }
        replacement["step_id"] = final_step_id
        cap.output_mapping = [replacement]
    if cap.step_ids and not any(
        node.get("type") == "return"
        for node in _iter_capability_nodes(cap.nodes or [])
    ):
        cap.nodes.append({
            "id": "return_final",
            "type": "return",
            "from": cap.step_ids[-1],
            "path": "response",
        })


def _capability_schema_field_type(schema: dict[str, Any], field: str) -> str:
    item = _schema_node_at_path(schema, field)
    if isinstance(item, dict):
        return str(item.get("type") or "")
    return ""


def _batch_capability_input_schema(
    steps: list[FlowStep],
    *,
    include_required_state: bool = True,
) -> dict[str, Any]:
    """批量能力只把逐条字段放进 entries，能力级共享字段保留在顶层。"""
    item_params: list[ParamField] = []
    shared_params: list[ParamField] = []
    write_user_params: list[ParamField] = []
    for step in steps:
        is_write = (step.method or "").upper() in _WRITE_METHODS
        array_body = is_write and _step_body_is_array(step)
        for param in step.params or []:
            if not _param_exposed_to_caller(param):
                continue
            if is_write:
                write_user_params.append(param)
            if is_write and (array_body or "[" in str(param.path or "")):
                item_params.append(param)
            else:
                shared_params.append(param)

    # 某些接口只通过 URL/名称体现 batch，body 快照不是标准 JSON。此时写接口业务字段
    # 仍应作为每条明细，而不是错误地要求调用方在顶层重复提交。
    if not item_params and write_user_params:
        item_params = list(write_user_params)
        write_ids = {id(param) for param in write_user_params}
        shared_params = [param for param in shared_params if id(param) not in write_ids]

    item_schema = _capability_input_schema(
        item_params, include_required_state=include_required_state,
    )
    shared_schema = _capability_input_schema(
        shared_params, include_required_state=include_required_state,
    )
    properties = dict(shared_schema.get("properties") or {})
    properties["entries"] = {
        "type": "array",
        "minItems": 1,
        "description": "批量提交明细；每个元素使用同一套业务字段",
        "items": item_schema,
    }
    required = list(dict.fromkeys([*(shared_schema.get("required") or []), "entries"]))
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _capability_schema_array_item_props(schema: dict[str, Any], field_name: str) -> tuple[set[str], set[str]]:
    props = (schema or {}).get("properties") or {}
    item = props.get(field_name) if isinstance(props, dict) else None
    if not isinstance(item, dict):
        return set(), set()
    items = item.get("items") if isinstance(item.get("items"), dict) else {}
    item_props = (items or {}).get("properties") or {}
    required = (items or {}).get("required") or []
    return set(item_props.keys()) if isinstance(item_props, dict) else set(), set(str(x) for x in required)


def _capability_schema_field(field: CapabilityField) -> dict[str, Any]:
    schema = _schema_for_param_type(field.type or "string")
    schema["x-dano-capability-owned"] = True
    schema["x-dano-operator-owned"] = bool(field.locked)
    if field.wire_format:
        schema["x-dano-wire-format"] = field.wire_format
    if field.enum_options:
        schema["x-options"] = list(field.enum_options)
        labels: list[Any] = []
        for option in field.enum_options:
            pair = _enum_label_value(option)
            if pair:
                labels.append(pair[1] if pair[1] not in (None, "") else pair[0])
            elif option not in (None, ""):
                labels.append(option)
        if labels and not any(isinstance(item, (dict, list)) for item in labels):
            schema["enum"] = labels
    if field.enum_value_map:
        schema["x-enum-value-map"] = dict(field.enum_value_map)
    if field.display_name:
        schema["title"] = field.display_name
    return schema

_PENDING_FLOW_SPEC_HELPERS = {'_CAPABILITY_PATH_PREFIXES': 'dano.execution.page.capability_kinds', '_FLOW_PATH_MISSING': 'dano.execution.page.flow_spec_core.normalization', '_apply_link_sources': 'dano.execution.page.flow_spec_core.controlled_edits', '_apply_mechanical_field_contracts': 'dano.execution.page.flow_materialization.builder', '_capability_is_batch': 'dano.execution.page.capability_contracts', '_capability_operation_kind': 'dano.execution.page.capability_kinds', '_capability_scoped_step_ids': 'dano.execution.page.capability_refs', '_disambiguate_capability_param_keys': 'dano.execution.page.capability_contracts', '_expand_response_key_map_inputs': 'dano.execution.page.capability_refs', '_flow_path_lookup': 'dano.execution.page.flow_spec_core.normalization', '_ground_recorded_identifier_relations': 'dano.execution.page.capability_identity', '_identifier_role_for_field': 'dano.execution.page.capability_identity', '_infer_type_from_value': 'dano.execution.page.flow_spec_core.normalization', '_is_business_query_step': 'dano.execution.page.capability_contracts', '_iter_capability_nodes': 'dano.execution.page.capability_nodes', '_normalize_actionable_placeholder_param_names': 'dano.execution.page.capability_contracts', '_normalize_capability_references': 'dano.execution.page.capability_nodes', '_option_source_step_ids': 'dano.execution.page.capability_refs', '_remove_capability_step_nodes': 'dano.execution.page.capability_nodes', '_reset_param_source': 'dano.execution.page.flow_spec_core.controlled_edits', '_sanitize_capability_nodes': 'dano.execution.page.capability_nodes', '_step_body_is_array': 'dano.execution.page.capability_contracts', '_sync_capability_order': 'dano.execution.page.capability_orchestration', 'executable_flow_links': 'dano.execution.page.capability_views', 'sync_capability_scoped_views': 'dano.execution.page.capability_orchestration'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
