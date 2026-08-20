"""Stage 5 contracts for caller-owned repeating request rows."""
from __future__ import annotations

import copy
import re
from typing import Any

from dano.execution.page.flow_spec_core.models import FlowSpec, ParamField
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


def _materialize_dynamic_array_inputs(spec: FlowSpec) -> None:
    """Collapse exposed ``rows[n].field`` leaves into one executable array input."""
    for step in spec.steps or []:
        if any(
            str((param.source or {}).get("kind") or "") == "dynamic_structure_input"
            and str((param.source or {}).get("structure_kind") or "") == "array_object"
            for param in step.params or []
        ):
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
            for param in caller_params:
                item_path = _array_item_relative_path(param.path, container)
                param.source = {
                    **(param.source or {}),
                    "array_container_path": container,
                    "array_item_path": item_path,
                    "array_item_key": str(param.key or param.path),
                    "array_item_required": bool(param.required),
                    "array_item_public": True,
                    "schema_identity_path": f"{container}[].{item_path}",
                }
            aggregate.source["item_paths"] = [
                str((param.source or {}).get("array_item_path") or "")
                for param in caller_params
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
