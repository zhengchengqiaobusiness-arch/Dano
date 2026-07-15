"""Build only evidence-backed cross-capability relations."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Iterable

from dano_recording.domain.capabilities import Capability
from dano_recording.domain.operations import CompiledRequest
from dano_recording.domain.relations import CapabilityRelation, RelationType

if TYPE_CHECKING:
    from dano_recording.evidence_graph import EvidenceGraph


def _scalars(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _scalars(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value[:100]):
            yield from _scalars(child, f"{prefix}[{index}]")
    elif value is not None and not isinstance(value, bool):
        yield prefix, value


def _input_scalars(request: CompiledRequest) -> Iterable[tuple[str, Any]]:
    for key, value in request.query:
        yield f"query.{key}", value
    if request.body_present:
        yield from ((f"body.{path}", value) for path, value in _scalars(request.body))


def _useful(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return value not in {0, 1}
    return len(str(value).strip()) >= 3


def build_relations(
    capabilities: tuple[Capability, ...],
    requests: tuple[CompiledRequest, ...],
    *,
    evidence_graph: EvidenceGraph | None = None,
) -> tuple[CapabilityRelation, ...]:
    # A repeated scalar is not a relation.  It can identify the precise paths
    # only after the evidence graph has already proved request dependency via
    # response -> observed control mutation -> wire binding.
    if evidence_graph is None:
        return ()
    request_by_id = {request.request_id: request for request in requests}
    result: list[CapabilityRelation] = []
    for source_index, source_capability in enumerate(capabilities):
        source_requests = tuple(request_by_id[item] for item in source_capability.request_ids)
        source_values: dict[str, tuple[str, Any]] = {}
        for source_request in source_requests:
            for path, value in _scalars(source_request.response_body):
                if _useful(value):
                    source_values.setdefault(str(value), (source_request.request_id, path))
        if not source_values:
            continue
        for target_capability in capabilities[source_index + 1:]:
            for target_request_id in target_capability.request_ids:
                proven_sources = {
                    source_request.request_id
                    for source_request in source_requests
                    if evidence_graph.has_request_dependency(
                        source_request.request_id, target_request_id
                    )
                }
                if not proven_sources:
                    continue
                target_request = request_by_id[target_request_id]
                matched = next((
                    (target_path, source_values[str(value)])
                    for target_path, value in _input_scalars(target_request)
                    if _useful(value)
                    and str(value) in source_values
                    and source_values[str(value)][0] in proven_sources
                ), None)
                if matched is None:
                    continue
                target_path, (source_request_id, source_path) = matched
                seed = (
                    f"{source_capability.capability_id}:{target_capability.capability_id}:"
                    f"{source_request_id}:{source_path}:{target_request_id}:{target_path}"
                )
                result.append(CapabilityRelation(
                    relation_id=f"rel_{hashlib.sha256(seed.encode()).hexdigest()[:16]}",
                    relation_type=RelationType.CALLER_SELECTION,
                    from_capability_id=source_capability.capability_id,
                    to_capability_id=target_capability.capability_id,
                    from_request_id=source_request_id,
                    to_request_id=target_request_id,
                    from_path=f"response.{source_path}",
                    to_path=target_path,
                    confidence=1.0,
                    evidence=(
                        "evidence graph proved response-control-wire dependency",
                        "exact scoped value identified source and target paths",
                    ),
                ))
                break
    return tuple(result)
