"""Stage 5: captured response key maps and dynamic structure."""
from __future__ import annotations

from typing import Any
import copy
import json
from dano.execution.page.flow_spec_core.models import (
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
)
from dano.execution.page.recording_facts import (
    _request_path,
    _request_sequence_value,
)
from dano.execution.page.value_tracing import (
    discover_response_key_maps,
)


def _response_shape_evidence_score(value: Any, *, depth: int = 0) -> int:
    """Score observed response structure, not business values.

    Repeated calls to one list endpoint often capture an empty initial page and
    a populated page after the operator searches.  Both are real facts, but the
    populated response is the only one that can describe ``records.items``.
    """
    if depth > 8:
        return 0
    if isinstance(value, dict):
        return len(value) + sum(
            _response_shape_evidence_score(item, depth=depth + 1)
            for item in value.values()
        )
    if isinstance(value, list):
        if not value:
            return 0
        samples = value[:3]
        return 5 + max(_response_shape_evidence_score(item, depth=depth + 1) for item in samples)
    return 1 if value is not None else 0


def _response_list_paths(value: Any, *, path: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(value, list):
        paths.add(path or "$.")
        for item in value[:3]:
            paths.update(_response_list_paths(item, path=f"{path}[]"))
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            paths.update(_response_list_paths(item, path=child))
    return paths


def _enrich_materialized_response_shapes(spec: FlowSpec) -> None:
    """Use a richer list response from the same observed endpoint for schema.

    Repeated list queries may first return an empty collection and later expose
    its item shape. Object responses are request-specific business facts and
    must never be replaced by another action merely because the route matches.
    """
    for step in spec.steps:
        method = (step.method or "GET").upper()
        if method not in {"GET", "HEAD"}:
            continue
        path = _request_path({"url": step.path or step.url})
        current_score = _response_shape_evidence_score(step.response_json)
        current_list_paths = _response_list_paths(step.response_json)
        if not current_list_paths:
            continue
        candidates = [
            fact for fact in (spec.request_facts.requests or [])
            if (fact.method or "GET").upper() == method
            and _request_path({"url": fact.path or fact.url}) == path
            and fact.response_json is not None
            and current_list_paths.intersection(
                _response_list_paths(fact.response_json)
            )
        ]
        if not candidates:
            continue
        richest = max(candidates, key=lambda fact: _response_shape_evidence_score(fact.response_json))
        richest_score = _response_shape_evidence_score(richest.response_json)
        if richest_score <= current_score:
            continue
        step.response_json = copy.deepcopy(richest.response_json)
        step.source_meta = {
            **(step.source_meta or {}),
            "response_shape_request_id": richest.request_id,
            "response_shape_enriched": True,
        }


def _latest_response_key_map_candidates(
    captured_requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Use the nearest captured source for each later dynamic request object."""
    ordered = sorted(
        enumerate(captured_requests or []),
        key=lambda item: (
            _request_sequence_value(
                item[1].get("sequence", item[1].get("request_index"))
            ) is None,
            _request_sequence_value(
                item[1].get("sequence", item[1].get("request_index"))
            ) or item[0],
            item[0],
        ),
    )
    position_by_request_id = {
        str(request.get("request_id") or ""): position
        for position, (_original_index, request) in enumerate(ordered)
        if str(request.get("request_id") or "")
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in discover_response_key_maps(captured_requests):
        signature = (
            str(candidate.get("target_request_id") or ""),
            _strip_body_prefix(str(candidate.get("target_container_path") or "")),
        )
        grouped.setdefault(signature, []).append(candidate)

    selected: list[dict[str, Any]] = []
    for candidates in grouped.values():
        nearest_position = max(
            position_by_request_id.get(str(item.get("source_request_id") or ""), -1)
            for item in candidates
        )
        selected.extend(
            item for item in candidates
            if position_by_request_id.get(str(item.get("source_request_id") or ""), -1)
            == nearest_position
        )
    return selected


def _materialize_captured_response_key_maps(
    steps: list[FlowStep],
    links: list[FlowLink],
    captured_requests: list[dict[str, Any]],
) -> None:
    """Turn exact response-row/request-key matches into executable contracts."""
    by_request_id = {
        str((step.source_meta or {}).get("request_id") or ""): step
        for step in steps
        if str((step.source_meta or {}).get("request_id") or "")
    }
    for candidate in _latest_response_key_map_candidates(captured_requests):
        source_request_id = str(candidate.get("source_request_id") or "")
        target_request_id = str(candidate.get("target_request_id") or "")
        source = by_request_id.get(source_request_id)
        target = by_request_id.get(target_request_id)
        if source is None or target is None:
            continue
        source_collection_path = str(candidate.get("source_collection_path") or "")
        source_key_path = str(candidate.get("source_key_path") or "")
        source_label_path = str(candidate.get("source_label_path") or "")
        target_container_path = _strip_body_prefix(
            str(candidate.get("target_container_path") or "")
        )
        collection = _flow_path_lookup(source.response_json, source_collection_path)
        try:
            recorded_body = (
                json.loads(target.body_source)
                if isinstance(target.body_source, str)
                else copy.deepcopy(target.body_source)
            )
        except (TypeError, ValueError):
            continue
        recorded_container = _flow_path_lookup(recorded_body, target_container_path)
        if not (
            isinstance(collection, list)
            and collection
            and all(isinstance(row, dict) for row in collection)
            and isinstance(recorded_container, dict)
            and recorded_container
        ):
            continue
        valid_rows = [
            row for row in collection
            if row.get(source_key_path) not in (None, "")
            and row.get(source_label_path) not in (None, "")
        ]
        rows_by_key = {
            str(row.get(source_key_path)): row
            for row in valid_rows
        }
        recorded_keys = [str(key) for key in recorded_container]
        if (
            len(rows_by_key) != len(valid_rows)
            or any(key not in rows_by_key for key in recorded_keys)
        ):
            continue
        matched_labels = [
            str(rows_by_key[key][source_label_path]) for key in recorded_keys
        ]
        if len(set(matched_labels)) != len(matched_labels):
            continue
        recorded_values = list(recorded_container.values())
        if all(isinstance(value, list) for value in recorded_values):
            value_shape = "item_list"
        elif all(not isinstance(value, (dict, list)) for value in recorded_values):
            value_shape = "direct"
        else:
            continue

        signature = (
            source.step_id, source_collection_path,
            target.step_id, target_container_path,
        )
        existing_link = next((
            link for link in links
            if (
                link.source_step_id, link.source_path,
                link.target_step_id, link.target_path,
            ) == signature
        ), None)
        existing_binding = dict(
            existing_link.value_binding or {}
        ) if existing_link is not None else {}
        # An agent-confirmed public alias is part of the caller contract.  The
        # capture repair may enrich its labels and value shape, but must not
        # replace that alias with the transport container name.
        input_field = str(
            existing_binding.get("input_field")
            or target_container_path.rsplit(".", 1)[-1]
        )
        dynamic_prefix = target_container_path + "."
        dynamic_paths = {
            str(param.path or "")
            for param in target.params
            if _strip_body_prefix(str(param.path or "")).startswith(dynamic_prefix)
        }
        if not dynamic_paths:
            continue
        option_bindings = [
            binding for binding in target.selects
            if str(binding.path or binding.id_path or "") in dynamic_paths
        ]
        option_sources = {
            (
                str(binding.source_request_id or ""),
                str(binding.value_key or ""),
                str(binding.label_key or ""),
            )
            for binding in option_bindings
            if binding.source_request_id and binding.value_key and binding.label_key
        }
        option_source = None
        if len(option_sources) == 1:
            request_id, value_path, label_path = next(iter(option_sources))
            option_source = {
                "request_id": request_id,
                "value_path": value_path,
                "label_path": label_path,
            }

        public_sample = dict(zip(matched_labels, recorded_values, strict=True))
        for param in target.params:
            if str(param.path or "") not in dynamic_paths:
                continue
            param.category = "runtime_var"
            param.source_kind = "dynamic_structure"
            param.source = {"kind": "dynamic_structure_leaf", "actor": "heuristic"}
            param.exposed_to_user = False
            param.editable = False
            param.required = False
            param.need_human_confirm = False
            target.sample_inputs.pop(str(param.key or param.path), None)
        target.selects = [
            binding for binding in target.selects
            if str(binding.path or binding.id_path or "") not in dynamic_paths
        ]
        public = next((
            param for param in target.params
            if _strip_body_prefix(str(param.path or "")) == target_container_path
        ), None)
        if public is None:
            public = ParamField(path=target_container_path, key=input_field)
            target.params.append(public)
        public.key = input_field
        public.label = public.label or input_field
        public.value = copy.deepcopy(public_sample)
        public.type = "object"
        public.wire_type = "object"
        public.required = True
        public.category = "user_param"
        public.source_kind = "user_input"
        public.source = {
            "kind": "dynamic_structure_input",
            "actor": "heuristic",
            "required_state": "required",
            **({"option_source": option_source} if option_source else {}),
        }
        public.exposed_to_user = True
        public.editable = True
        public.need_human_confirm = False
        public.reason = "调用方按上游返回的稳定标签提供值，运行期按最新响应键组装请求"
        public.evidence = [*list(public.evidence or []), {
            "source": "response_key_map",
            "actor": "heuristic",
            "source_request_id": source_request_id,
            "target_request_id": target_request_id,
            "wire_path": f"body.{target_container_path}",
            "labels": matched_labels,
        }]
        target.sample_inputs[input_field] = copy.deepcopy(public_sample)

        value_binding = {
            "kind": "caller_map_by_label",
            "input_field": input_field,
            "input_fields_by_label": {
                label: label for label in matched_labels
            },
            "value_shape": value_shape,
            "required_labels": matched_labels,
            "ignored_labels": [
                str(row[source_label_path])
                for row in collection
                if str(row[source_label_path]) not in set(matched_labels)
            ],
            **({"option_source": option_source} if option_source else {}),
        }
        if existing_link is not None:
            existing_link.value_binding = {
                **dict(existing_link.value_binding or {}),
                **value_binding,
            }
            continue
        links.append(FlowLink(
            source_step_id=source.step_id,
            source_path=source_collection_path,
            target_step_id=target.step_id,
            target_path=target_container_path,
            kind="response_key_map",
            source_collection_path=source_collection_path,
            source_key_path=source_key_path,
            source_label_path=source_label_path,
            target_container_path=target_container_path,
            value_binding=value_binding,
            confirmed=False,
            confidence=float(candidate.get("confidence") or 0.99),
            reason="录制响应行的稳定键与后续请求对象键精确一致",
            evidence={
                "kind": "response_key_map",
                "actor": "heuristic",
                "source_request_id": source_request_id,
                "target_request_id": target_request_id,
            },
            meta={"actor": "heuristic", "captured_structure_match": True},
        ))


_PENDING_FLOW_SPEC_HELPERS = ('_flow_path_lookup', '_strip_body_prefix',)


def _bind_flow_spec_helpers() -> None:
    import dano.execution.page.flow_spec as _flow_spec
    module_globals = globals()
    for name in _PENDING_FLOW_SPEC_HELPERS:
        if hasattr(_flow_spec, name):
            module_globals[name] = getattr(_flow_spec, name)
