"""Stage 5 contracts for caller-owned repeating request rows."""
from __future__ import annotations

import copy
import re
from typing import Any

from dano.execution.page.flow_spec_core.models import FlowSpec, FlowStep, ParamField, SelectBinding
from dano.execution.page.request_capture import _parse_body


_ARRAY_OCCURRENCE_RE = re.compile(r"^(?P<container>.+?)\[(?P<index>\d+)\](?:\.|$)")


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
