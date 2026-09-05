"""Stage 5 contracts for caller-owned repeating request rows."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from dano.execution.page.flow_spec_core.models import FlowSpec, FlowStep, ParamField, SelectBinding
from dano.execution.page.flow_spec_core.normalization import _infer_type_from_value
from dano.execution.page.request_capture import _parse_body


_ARRAY_OCCURRENCE_RE = re.compile(r"^(?P<container>.+?)\[(?P<index>\d+)\](?:\.|$)")
_UUID_LIKE_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ROW_SYSTEM_LEAVES = frozenset({
    "xrowkey", "x_row_key", "rowkey", "row_key",
    "sort", "index", "seq", "order",
    "itemtype", "rowtype", "linetype",
})


def _looks_row_system_leaf(key: str) -> bool:
    """Row mechanics (type code / order / client row key), not caller-owned cells."""
    leaf = re.sub(r"[^a-z0-9]+", "", str(key or "").casefold())
    return leaf in _ROW_SYSTEM_LEAVES or leaf.endswith("rowkey")


def _recorded_object_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        return []
    if any(item is not None and not isinstance(item, dict) for item in value):
        return []
    return [item for item in value if isinstance(item, dict)]


def _object_array_keys(rows: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for row in rows:
        for key in row:
            name = str(key)
            if name and name not in keys:
                keys.append(name)
    return keys


def _caller_keys_for_object_rows(
    rows: list[dict[str, Any]],
    item_params: list[ParamField] | None = None,
) -> list[str]:
    keys = _object_array_keys(rows)
    public = {
        str(param.key or "")
        for param in (item_params or [])
        if param.exposed_to_user and str(param.key or "")
    }
    if public:
        return [key for key in keys if key in public]
    return [key for key in keys if not _looks_row_system_leaf(key)]


def _presence_signature(row: dict[str, Any], caller_keys: list[str]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            key,
            "present" if row.get(key) not in (None, "") else "absent",
        )
        for key in caller_keys
    )


def _simplify_presence_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(cases) < 2:
        return []
    keys = [str(name) for name in (cases[0].get("when") or {})]
    distinguishing = [
        key
        for key in keys
        if len({str((case.get("when") or {}).get(key)) for case in cases}) > 1
    ]
    if not distinguishing:
        return []
    return [
        {
            "when": {key: (case.get("when") or {}).get(key) for key in distinguishing},
            "value": case.get("value"),
        }
        for case in cases
    ]


def _presence_cases(
    rows: list[dict[str, Any]],
    key: str,
    caller_keys: list[str],
) -> list[dict[str, Any]]:
    if not caller_keys:
        return []
    grouped: dict[tuple[tuple[str, str], ...], list[Any]] = {}
    for row in rows:
        grouped.setdefault(_presence_signature(row, caller_keys), []).append(row.get(key))
    cases: list[dict[str, Any]] = []
    for signature, values in grouped.items():
        encoded = {json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) for value in values}
        if len(encoded) != 1:
            return []
        cases.append({
            "when": {name: state for name, state in signature},
            "value": values[0],
        })
    return _simplify_presence_cases(cases)


def _infer_array_item_system_rules(
    rows: list[dict[str, Any]],
    caller_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Derive per-row system fields from recorded rows. No page-specific codes."""
    if not rows:
        return []
    keys = _object_array_keys(rows)
    owned = list(caller_keys) if caller_keys is not None else _caller_keys_for_object_rows(rows)
    rules: list[dict[str, Any]] = []
    for key in keys:
        if key in owned:
            continue
        values = [row.get(key) for row in rows]
        if values and all(isinstance(value, str) and _UUID_LIKE_RE.match(value) for value in values):
            rules.append({"key": key, "strategy": "uuid"})
            continue
        if all(value == index or value == str(index) for index, value in enumerate(values)):
            rules.append({"key": key, "strategy": "index"})
            continue
        if owned and _is_index_within_presence(rows, key, owned):
            rules.append({"key": key, "strategy": "index_within_presence"})
            continue
        if (
            _looks_row_system_leaf(key)
            and len(values) == len(set(map(_stable_cell, values)))
            and not all(isinstance(value, (int, float)) and not isinstance(value, bool) and value in {0, 1, 2} for value in values)
        ):
            rules.append({"key": key, "strategy": "uuid"})
            continue
        encoded = {json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) for value in values}
        if len(encoded) == 1:
            rules.append({"key": key, "strategy": "constant", "value": values[0]})
            continue
        cases = _presence_cases(rows, key, owned)
        if cases:
            rules.append({"key": key, "strategy": "caller_presence", "cases": cases})
    return rules


def _stable_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _is_index_within_presence(
    rows: list[dict[str, Any]],
    key: str,
    caller_keys: list[str],
) -> bool:
    grouped: dict[tuple[tuple[str, str], ...], list[Any]] = {}
    for row in rows:
        grouped.setdefault(_presence_signature(row, caller_keys), []).append(row.get(key))
    if len(grouped) < 2:
        return False
    saw_increment = False
    for values in grouped.values():
        if not all(value == index or value == str(index) for index, value in enumerate(values)):
            return False
        if len(values) >= 2:
            saw_increment = True
    return saw_increment


def _promote_recorded_object_array_params(step: FlowStep) -> None:
    """Turn a lone ``body.items = [{...}, ...]`` param into an array<object> contract."""
    for param in list(step.params or []):
        source = dict(param.source or {})
        rows = _recorded_object_rows(param.value)
        if not rows:
            continue
        container = str(
            source.get("array_container_path")
            or str(param.path or "").removeprefix("body.")
        ).strip()
        if not container or "[" in container:
            continue
        existing_members = [
            item
            for item in (step.params or [])
            if item is not param and _array_container_for_path(item.path) == container
        ]
        param.type = "array"
        param.wire_type = param.wire_type or "array"
        param.source = {
            **source,
            "kind": "dynamic_structure_input",
            "structure_kind": "array_object",
            "array_container_path": container,
        }
        if param.category != "user_param":
            param.category = "user_param"
            param.exposed_to_user = True
            param.editable = True
        occupied = {str(item.key or "") for item in existing_members if item.key}
        caller_keys = _caller_keys_for_object_rows(rows, existing_members)
        for key in _object_array_keys(rows):
            if key in occupied:
                continue
            sample = next((row.get(key) for row in rows if key in row), None)
            caller = key in caller_keys
            member = ParamField(
                path=f"body.{container}[0].{key}",
                key=key,
                label=key,
                value=copy.deepcopy(sample),
                type=_infer_type_from_value(sample) or "string",
                wire_type=_infer_type_from_value(sample) or "string",
                required=caller and any(key in row and row.get(key) not in (None, "") for row in rows),
                confidence=float(param.confidence or 0.8),
                confidence_tier="auto",
                name_source="structure",
                category="user_param" if caller else "runtime_var",
                source_kind="user_input" if caller else "system_generated",
                source={
                    "kind": "dynamic_structure_leaf",
                    "array_container_path": container,
                    "array_item_path": key,
                    "array_item_key": key,
                    "array_item_required": caller,
                    "array_item_member": True,
                    "array_item_public": caller,
                    "schema_identity_path": f"{container}[].{key}",
                },
                editable=caller,
                exposed_to_user=caller,
                reason=(
                    "重复行内由调用方填写的单元格"
                    if caller
                    else "重复行的系统字段：由录制行结构在运行期补齐，不进入调用方 schema"
                ),
            )
            step.params.append(member)


def _value_at_path(value: Any, path: str) -> Any:
    current = value
    for token in [part for part in str(path or "").split(".") if part]:
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def _array_container_for_path(path: str) -> str:
    match = _ARRAY_OCCURRENCE_RE.match(str(path or "").removeprefix("body."))
    return str(match.group("container") or "") if match else ""


def _array_item_relative_path(path: str, container: str) -> str:
    text = str(path or "").removeprefix("body.")
    prefix = re.compile(rf"^{re.escape(container)}\[\d+\]\.?")
    return prefix.sub("", text, count=1)


def _array_public_key(container: str, occupied: set[str]) -> str:
    leaf = container.rsplit(".", 1)[-1]
    candidate = re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]+", "_", leaf).strip("_") or "items"
    if candidate not in occupied:
        return candidate
    contextual = re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]+", "_", container).strip("_")
    return contextual or candidate


def _sync_dynamic_array_memberships(spec: FlowSpec) -> None:
    """Keep array membership metadata orthogonal to changing field ownership."""
    for step in spec.steps or []:
        aggregates = [
            param for param in step.params or []
            if str((param.source or {}).get("kind") or "") == "dynamic_structure_input"
            and str((param.source or {}).get("structure_kind") or "") == "array_object"
        ]
        for aggregate in aggregates:
            container = str(
                (aggregate.source or {}).get("array_container_path")
                or aggregate.path
                or ""
            )
            item_params = [
                param for param in step.params or []
                if param is not aggregate
                and _array_container_for_path(param.path) == container
            ]
            for param in item_params:
                item_path = _array_item_relative_path(param.path, container)
                param.source = {
                    **(param.source or {}),
                    "array_container_path": container,
                    "array_item_path": item_path,
                    "array_item_key": str(param.key or param.path),
                    "array_item_required": bool(param.required),
                    "array_item_member": True,
                    "array_item_public": bool(
                        param.category == "user_param" and param.exposed_to_user
                    ),
                    "schema_identity_path": f"{container}[].{item_path}",
                }
            aggregate.source = {
                **(aggregate.source or {}),
                "item_paths": [
                    str((param.source or {}).get("array_item_path") or "")
                    for param in item_params
                ],
            }


def _dynamic_selector_binding(
    step: FlowStep,
    selector: ParamField,
    container: str,
) -> SelectBinding | None:
    relative_selector = _array_item_relative_path(selector.path, container)
    exact = next((
        binding for binding in (step.selects or [])
        if _array_container_for_path(binding.path) == container
        and _array_item_relative_path(binding.path, container) == relative_selector
    ), None)
    if exact is not None:
        return exact
    return next((
        binding for binding in (step.selects or [])
        if binding.multi
        and str(binding.path or "").removeprefix("body.") == container
        and str(binding.label_subkey or "") == relative_selector
    ), None)


def _normalize_dynamic_array_selects(step: FlowStep) -> None:
    """Rebuild executable row selectors from their nested field contracts.

    Persisted drafts can contain a stale aggregate binding inferred from the
    recorded row as one opaque value.  The nested selector is the authority:
    callers provide each row, the selector resolves its chosen record, and
    only selected-row projections are injected into that same row.
    """
    aggregates = [
        param for param in step.params or []
        if str((param.source or {}).get("kind") or "") == "dynamic_structure_input"
        and str((param.source or {}).get("structure_kind") or "") == "array_object"
    ]
    replacements: list[SelectBinding] = []
    consumed: set[int] = set()
    rebuilt_containers: set[str] = set()
    for aggregate in aggregates:
        container = str(
            (aggregate.source or {}).get("array_container_path")
            or aggregate.path
            or ""
        ).removeprefix("body.")
        if not container:
            continue
        item_params = [
            param for param in step.params or []
            if param is not aggregate and _array_container_for_path(param.path) == container
        ]
        for selector in item_params:
            selector_source = dict(selector.source or {})
            nested_option = selector_source.get("option_source")
            has_option_contract = bool(
                selector.source_kind in {"api_option", "form_option", "page_enum", "static_enum", "manual_enum"}
                or isinstance(nested_option, dict)
            )
            if not has_option_contract:
                continue
            binding = _dynamic_selector_binding(step, selector, container)
            if binding is None:
                continue
            relative_selector = _array_item_relative_path(selector.path, container)
            projections: dict[str, str] = {}
            element_template: dict[str, Any] = {
                relative_selector: {"item_key": binding.value_key},
            }
            for target in item_params:
                source = dict(target.source or {})
                if (
                    target.source_kind != "selected_option_field"
                    or _array_item_relative_path(target.path, container) == relative_selector
                    or str(source.get("selector_path") or "") != str(selector.path or "")
                    or not source.get("response_path")
                ):
                    continue
                relative_target = _array_item_relative_path(target.path, container)
                response_path = str(source["response_path"])
                element_template[relative_target] = {"item_key": response_path}
                projections[f"{container}[*].{relative_target}"] = response_path
            item = binding.model_copy(deep=True)
            item.param = aggregate.key
            item.path = container
            item.multi = True
            item.label_subkey = relative_selector
            item.element_template = element_template
            item.field_projections = projections
            item.id_path = None
            item.id_tokens = None
            replacements.append(item)
            consumed.add(id(binding))
            rebuilt_containers.add(container)

    if not replacements:
        return
    preserved = [
        binding for binding in (step.selects or [])
        if id(binding) not in consumed
        and not (
            binding.multi
            and str(binding.path or "").removeprefix("body.") in rebuilt_containers
            and binding.actor not in {"manual", "user"}
        )
    ]
    step.selects = [*preserved, *replacements]


def _materialize_dynamic_array_inputs(spec: FlowSpec) -> None:
    """Collapse exposed ``rows[n].field`` leaves into one executable array input."""
    for step in spec.steps or []:
        _promote_recorded_object_array_params(step)
        if any(
            str((param.source or {}).get("kind") or "") == "dynamic_structure_input"
            and str((param.source or {}).get("structure_kind") or "") == "array_object"
            for param in step.params or []
        ):
            _normalize_dynamic_array_selects(step)
            continue
        body = _parse_body(step.body_source)
        if not isinstance(body, dict):
            continue
        groups: dict[str, list[ParamField]] = {}
        for param in step.params or []:
            container = _array_container_for_path(param.path)
            if container:
                groups.setdefault(container, []).append(param)
        occupied = {
            str(param.key or param.path or "")
            for param in step.params or []
            if not _array_container_for_path(param.path)
        }
        for container, item_params in groups.items():
            caller_params = [
                param for param in item_params
                if param.category == "user_param" and param.exposed_to_user
            ]
            recorded_rows = _value_at_path(body, container)
            if not caller_params or not isinstance(recorded_rows, list):
                continue
            public_key = _array_public_key(container, occupied)
            occupied.add(public_key)
            aggregate = ParamField(
                path=container,
                key=public_key,
                label=container.rsplit(".", 1)[-1],
                value=copy.deepcopy(recorded_rows),
                type="array",
                wire_type="array",
                required=any(param.required for param in caller_params),
                confidence=min((param.confidence for param in caller_params), default=0.8),
                confidence_tier="auto",
                name_source="structure",
                category="user_param",
                source_kind="user_input",
                source={
                    "kind": "dynamic_structure_input",
                    "structure_kind": "array_object",
                    "array_container_path": container,
                    "required_state": (
                        "required" if any(param.required for param in caller_params) else "unknown"
                    ),
                },
                editable=True,
                exposed_to_user=True,
                reason="重复请求结构由调用方按 array<object> 提供；occurrence 索引不进入 schema identity",
                evidence=[{
                    "kind": "dynamic_array_structure",
                    "container_path": container,
                    "occurrence_paths": [param.path for param in item_params],
                }],
            )
            caller_param_ids = {id(param) for param in caller_params}
            for param in item_params:
                item_path = _array_item_relative_path(param.path, container)
                param.source = {
                    **(param.source or {}),
                    "array_container_path": container,
                    "array_item_path": item_path,
                    "array_item_key": str(param.key or param.path),
                    "array_item_required": bool(param.required),
                    "array_item_member": True,
                    "array_item_public": id(param) in caller_param_ids,
                    "schema_identity_path": f"{container}[].{item_path}",
                }
            aggregate.source["item_paths"] = [
                str((param.source or {}).get("array_item_path") or "")
                for param in item_params
            ]
            step.params.append(aggregate)

            rewritten_selects = []
            for binding in step.selects or []:
                binding_container = _array_container_for_path(binding.path)
                if binding_container != container:
                    rewritten_selects.append(binding)
                    continue
                relative_selector = _array_item_relative_path(binding.path, container)
                projections = {
                    _array_item_relative_path(param.path, container): str(
                        (param.source or {}).get("response_path") or ""
                    )
                    for param in item_params
                    if param.source_kind == "selected_option_field"
                    and str(
                        (param.source or {}).get("selector_path") or ""
                    ) == str(binding.path or "")
                    and str((param.source or {}).get("response_path") or "")
                }
                item = binding.model_copy(deep=True)
                item.param = public_key
                item.path = container
                item.multi = True
                item.label_subkey = relative_selector
                item.element_template = {
                    relative_selector: {"item_key": binding.value_key},
                    **{
                        target: {"item_key": source}
                        for target, source in projections.items()
                    },
                }
                item.field_projections = projections
                item.id_path = None
                item.id_tokens = None
                rewritten_selects.append(item)
            step.selects = rewritten_selects
        _normalize_dynamic_array_selects(step)
    _sync_dynamic_array_memberships(spec)
