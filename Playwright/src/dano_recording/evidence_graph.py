"""Evidence-only graph and multi-signal transaction correlation.

The graph records facts and their relationships.  It deliberately does not
assign business names or field semantics.  Scoped value equality is retained
for every scalar, including short values such as ``0``, ``1`` and ``8``, but a
value match can never create a causal edge by itself.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Iterable

from pydantic import Field, model_validator

from dano_recording.domain._base import FrozenModel, freeze_json
from dano_recording.value_evidence import (
    CredentialVault,
    ValueEvidence,
    ValueEvidenceFactory,
    ValueRetention,
    ValueSensitivity,
)

if TYPE_CHECKING:
    from dano_recording.capture_store import NetworkObservation


class EvidenceNodeKind(StrEnum):
    PAGE = "page"
    FORM = "form"
    CONTROL = "control"
    ACTION = "action"
    REQUEST_OBSERVATION = "request_observation"
    RESPONSE_FIELD = "response_field"
    DOM_MUTATION = "dom_mutation"
    SUBMIT_FIELD = "submit_field"
    VALUE_EVIDENCE = "value_evidence"


class EvidenceEdgeKind(StrEnum):
    ACTION_CHANGED_CONTROL = "action_changed_control"
    ACTION_TRIGGERED_REQUEST = "action_triggered_request"
    REQUEST_RETURNED_FIELD = "request_returned_field"
    RESPONSE_POPULATED_CONTROL = "response_populated_control"
    CONTROL_BOUND_TO_WIRE = "control_bound_to_wire"
    RESPONSE_BOUND_TO_WIRE = "response_bound_to_wire"
    VALUES_EQUAL_IN_SCOPE = "values_equal_in_scope"


def make_value_evidence(
    value: Any,
    *,
    server_secret: bytes | str,
    tenant_scope: str,
    recording_lineage: str,
    value_type: str,
    sensitivity: ValueSensitivity | None = None,
    runtime_resolver: str | None = None,
    retention: ValueRetention | None = None,
    credential_vault: CredentialVault | None = None,
) -> ValueEvidence:
    """Delegate value handling to the package's single security boundary."""

    secret = server_secret.encode("utf-8") if isinstance(server_secret, str) else server_secret
    return ValueEvidenceFactory(
        server_secret=secret,
        credential_vault=credential_vault,
    ).capture(
        tenant_scope=tenant_scope,
        recording_lineage=recording_lineage,
        value=value,
        value_type=value_type,
        sensitivity=sensitivity,
        runtime_resolver=runtime_resolver,
        retention=retention,
    )


class EvidenceNode(FrozenModel):
    node_id: str
    kind: EvidenceNodeKind
    page_id: str | None = None
    frame_id: str | None = None
    action_id: str | None = None
    transaction_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _freeze_payload(self) -> "EvidenceNode":
        if not self.node_id.strip():
            raise ValueError("node_id is required")
        object.__setattr__(self, "payload", freeze_json(self.payload))
        return self


class EvidenceEdge(FrozenModel):
    edge_id: str
    kind: EvidenceEdgeKind
    source_id: str
    target_id: str
    evidence_ids: tuple[str, ...] = ()
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    causal: bool = False
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _causality_contract(self) -> "EvidenceEdge":
        if self.kind is EvidenceEdgeKind.VALUES_EQUAL_IN_SCOPE and self.causal:
            raise ValueError("scoped value equality is not causal evidence")
        if self.kind is EvidenceEdgeKind.ACTION_TRIGGERED_REQUEST and not self.causal:
            raise ValueError("action-triggered request edges require causal anchors")
        return self


class TransactionEvidence(FrozenModel):
    transaction_id: str
    action_node_id: str
    action_id: str | None = None
    page_id: str | None = None
    frame_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    initiators: tuple[str, ...] = ()
    changed_control_ids: tuple[str, ...] = ()
    submit_fingerprints: tuple[str, ...] = ()


class RequestObservationEvidence(FrozenModel):
    request_id: str
    request_node_id: str
    observation_id: str | None = None
    request_definition_id: str | None = None
    action_id: str | None = None
    page_id: str | None = None
    frame_id: str | None = None
    started_at: datetime | None = None
    initiator: str | None = None
    changed_control_ids: tuple[str, ...] = ()
    input_fingerprints: tuple[str, ...] = ()

    @classmethod
    def from_network_observation(
        cls,
        observation: "NetworkObservation",
        *,
        request_id: str | None = None,
        request_node_id: str | None = None,
        changed_control_ids: Iterable[str] = (),
    ) -> "RequestObservationEvidence":
        initiator = observation.initiator
        initiator_url = str(initiator.get("url") or "") if isinstance(initiator, dict) else ""
        if not initiator_url and isinstance(initiator, dict):
            stack = initiator.get("stack")
            frames = stack.get("callFrames") if isinstance(stack, dict) else None
            if isinstance(frames, (list, tuple)) and frames and isinstance(frames[0], dict):
                initiator_url = str(frames[0].get("url") or "")
        observation_id = observation.observation_id
        return cls(
            request_id=request_id or observation_id,
            request_node_id=request_node_id or f"request:{observation_id}",
            observation_id=observation_id,
            request_definition_id=str(observation.request_definition_id),
            action_id=observation.action_id,
            page_id=observation.page_id,
            frame_id=observation.frame_id,
            started_at=observation.started_at,
            initiator=initiator_url or None,
            changed_control_ids=tuple(changed_control_ids),
            input_fingerprints=tuple(
                item.scoped_hmac
                for item in observation.request_values
                if item.scoped_hmac is not None
            ),
        )


class TransactionAssociation(FrozenModel):
    transaction_id: str
    request_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    causal_anchors: tuple[str, ...]
    supporting_evidence: tuple[str, ...] = ()


def _normalise_initiator(value: str) -> str:
    return value.strip().casefold().split("?", 1)[0]


def associate_transactions(
    transactions: Iterable[TransactionEvidence],
    requests: Iterable[RequestObservationEvidence],
) -> tuple[TransactionAssociation, ...]:
    """Associate requests using causal anchors plus contextual support.

    Time/page proximity and scoped fingerprints increase confidence only after
    an action, initiator, or changed-control anchor exists.  This prevents the
    old global-equality and temporal-window shortcuts.
    """

    transactions = tuple(transactions)
    associations: list[TransactionAssociation] = []
    for request in requests:
        candidates: list[TransactionAssociation] = []
        for transaction in transactions:
            if (
                request.action_id
                and transaction.action_id
                and request.action_id != transaction.action_id
            ):
                continue
            anchors: list[str] = []
            support: list[str] = []
            score = 0.0
            if request.action_id and request.action_id == transaction.action_id:
                anchors.append("action_id")
                score += 0.65
            request_initiator = _normalise_initiator(request.initiator or "")
            transaction_initiators = {
                _normalise_initiator(value) for value in transaction.initiators if value.strip()
            }
            if request_initiator and request_initiator in transaction_initiators:
                anchors.append("initiator")
                score += 0.20
            changed = set(request.changed_control_ids) & set(transaction.changed_control_ids)
            if changed:
                anchors.append("changed_control")
                support.extend(f"control:{control_id}" for control_id in sorted(changed))
                score += 0.25

            same_page = bool(
                request.page_id
                and transaction.page_id
                and request.page_id == transaction.page_id
            )
            same_frame = bool(
                request.frame_id
                and transaction.frame_id
                and request.frame_id == transaction.frame_id
            )
            in_window = bool(
                request.started_at
                and transaction.started_at
                and transaction.finished_at
                and transaction.started_at <= request.started_at <= transaction.finished_at
            )
            if same_page:
                support.append("page")
                score += 0.04
            if same_frame:
                support.append("frame")
                score += 0.03
            if in_window:
                support.append("time_window")
                score += 0.05

            equal_values = set(request.input_fingerprints) & set(
                transaction.submit_fingerprints
            )
            if equal_values:
                support.extend(f"scoped_value:{value}" for value in sorted(equal_values))
                score += 0.03

            # Equality, page/frame, time, and a shared initiator are contextual
            # evidence.  A direct action/control anchor is sufficient; an
            # initiator requires an independent page/frame/time signal.
            direct_anchor = bool({"action_id", "changed_control"} & set(anchors))
            composite_anchor = "initiator" in anchors and bool(
                {"page", "frame", "time_window"} & set(support)
            )
            if not direct_anchor and not composite_anchor:
                continue
            candidates.append(
                TransactionAssociation(
                    transaction_id=transaction.transaction_id,
                    request_id=request.request_id,
                    confidence=min(score, 1.0),
                    causal_anchors=tuple(anchors),
                    supporting_evidence=tuple(support),
                )
            )
        if not candidates:
            continue
        candidates.sort(
            key=lambda item: (
                item.confidence,
                len(item.causal_anchors),
                item.transaction_id,
            ),
            reverse=True,
        )
        best = candidates[0]
        # An exact tie is ambiguous unless one candidate has the explicit
        # action anchor and the other does not.
        if len(candidates) > 1 and candidates[1].confidence == best.confidence:
            best_has_action = "action_id" in best.causal_anchors
            other_has_action = "action_id" in candidates[1].causal_anchors
            if best_has_action == other_has_action:
                continue
        associations.append(best)
    return tuple(associations)


_DEPENDENCY_EDGE_KINDS = frozenset(
    {
        EvidenceEdgeKind.RESPONSE_POPULATED_CONTROL,
        EvidenceEdgeKind.CONTROL_BOUND_TO_WIRE,
        EvidenceEdgeKind.RESPONSE_BOUND_TO_WIRE,
    }
)


class EvidenceGraph(FrozenModel):
    nodes: tuple[EvidenceNode, ...] = ()
    edges: tuple[EvidenceEdge, ...] = ()

    @model_validator(mode="after")
    def _validate_graph(self) -> "EvidenceGraph":
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("evidence graph contains duplicate node ids")
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("evidence graph contains duplicate edge ids")
        known = set(node_ids)
        for edge in self.edges:
            if edge.source_id not in known or edge.target_id not in known:
                raise ValueError(f"edge {edge.edge_id} references an unknown node")
        return self

    def node(self, node_id: str) -> EvidenceNode | None:
        return next((node for node in self.nodes if node.node_id == node_id), None)

    def edges_of_kind(self, kind: EvidenceEdgeKind) -> tuple[EvidenceEdge, ...]:
        return tuple(edge for edge in self.edges if edge.kind is kind)

    def request_dependencies(self, target_request_id: str) -> tuple[str, ...]:
        """Return response-producing requests proven to feed a target request.

        ``VALUES_EQUAL_IN_SCOPE`` is intentionally excluded.  Equality can
        support a direct response/wire binding, but can never become the
        binding by itself.
        """

        by_id = {node.node_id: node for node in self.nodes}
        targets = {
            node.node_id
            for node in self.nodes
            if str(node.payload.get("request_id") or "") == target_request_id
        }
        incoming: dict[str, list[EvidenceEdge]] = {}
        for edge in self.edges:
            if edge.kind in _DEPENDENCY_EDGE_KINDS:
                incoming.setdefault(edge.target_id, []).append(edge)
        queue = deque(targets)
        seen = set(targets)
        dependencies: list[str] = []
        while queue:
            node_id = queue.popleft()
            for edge in incoming.get(node_id, ()):
                if edge.source_id in seen:
                    continue
                seen.add(edge.source_id)
                queue.append(edge.source_id)
                source = by_id[edge.source_id]
                source_request = str(source.payload.get("request_id") or "")
                if source_request and source_request != target_request_id:
                    dependencies.append(source_request)
        return tuple(dict.fromkeys(dependencies))

    def has_request_dependency(self, source_request_id: str, target_request_id: str) -> bool:
        return source_request_id in self.request_dependencies(target_request_id)


_ALLOWED_ENDPOINTS: dict[EvidenceEdgeKind, tuple[set[EvidenceNodeKind], set[EvidenceNodeKind]]] = {
    EvidenceEdgeKind.ACTION_CHANGED_CONTROL: (
        {EvidenceNodeKind.ACTION},
        {EvidenceNodeKind.CONTROL},
    ),
    EvidenceEdgeKind.ACTION_TRIGGERED_REQUEST: (
        {EvidenceNodeKind.ACTION},
        {EvidenceNodeKind.REQUEST_OBSERVATION},
    ),
    EvidenceEdgeKind.REQUEST_RETURNED_FIELD: (
        {EvidenceNodeKind.REQUEST_OBSERVATION},
        {EvidenceNodeKind.RESPONSE_FIELD},
    ),
    EvidenceEdgeKind.RESPONSE_POPULATED_CONTROL: (
        {EvidenceNodeKind.RESPONSE_FIELD},
        {EvidenceNodeKind.CONTROL},
    ),
    EvidenceEdgeKind.CONTROL_BOUND_TO_WIRE: (
        {EvidenceNodeKind.CONTROL},
        {EvidenceNodeKind.SUBMIT_FIELD},
    ),
    EvidenceEdgeKind.RESPONSE_BOUND_TO_WIRE: (
        {EvidenceNodeKind.RESPONSE_FIELD},
        {EvidenceNodeKind.SUBMIT_FIELD},
    ),
    EvidenceEdgeKind.VALUES_EQUAL_IN_SCOPE: (
        {
            EvidenceNodeKind.VALUE_EVIDENCE,
            EvidenceNodeKind.RESPONSE_FIELD,
            EvidenceNodeKind.SUBMIT_FIELD,
            EvidenceNodeKind.CONTROL,
        },
        {
            EvidenceNodeKind.VALUE_EVIDENCE,
            EvidenceNodeKind.RESPONSE_FIELD,
            EvidenceNodeKind.SUBMIT_FIELD,
            EvidenceNodeKind.CONTROL,
        },
    ),
}


class EvidenceGraphBuilder:
    def __init__(self) -> None:
        self._nodes: dict[str, EvidenceNode] = {}
        self._edges: dict[str, EvidenceEdge] = {}

    def add_node(self, node: EvidenceNode) -> EvidenceNode:
        current = self._nodes.get(node.node_id)
        if current is not None and current != node:
            raise ValueError(f"node {node.node_id} already exists with different evidence")
        self._nodes[node.node_id] = node
        return node

    def add_network_observation(
        self,
        observation: "NetworkObservation",
        *,
        request_id: str | None = None,
        request_node_id: str | None = None,
        changed_control_ids: Iterable[str] = (),
    ) -> RequestObservationEvidence:
        projection = RequestObservationEvidence.from_network_observation(
            observation,
            request_id=request_id,
            request_node_id=request_node_id,
            changed_control_ids=changed_control_ids,
        )
        self.add_node(
            EvidenceNode(
                node_id=projection.request_node_id,
                kind=EvidenceNodeKind.REQUEST_OBSERVATION,
                page_id=projection.page_id,
                frame_id=projection.frame_id,
                action_id=projection.action_id,
                payload={
                    "request_id": projection.request_id,
                    "observation_id": projection.observation_id,
                    "request_definition_id": projection.request_definition_id,
                },
            )
        )
        return projection

    def link(
        self,
        kind: EvidenceEdgeKind,
        source_id: str,
        target_id: str,
        *,
        evidence_ids: Iterable[str] = (),
        confidence: float = 1.0,
        causal: bool | None = None,
        reasons: Iterable[str] = (),
    ) -> EvidenceEdge:
        source = self._nodes.get(source_id)
        target = self._nodes.get(target_id)
        if source is None or target is None:
            raise ValueError("both edge endpoints must exist before linking")
        allowed_sources, allowed_targets = _ALLOWED_ENDPOINTS[kind]
        if source.kind not in allowed_sources or target.kind not in allowed_targets:
            raise ValueError(
                f"invalid {kind.value} endpoints: {source.kind.value} -> {target.kind.value}"
            )
        if causal is None:
            causal = kind is not EvidenceEdgeKind.VALUES_EQUAL_IN_SCOPE
        payload = {
            "kind": kind.value,
            "source": source_id,
            "target": target_id,
            "evidence": sorted(set(evidence_ids)),
            "reasons": list(reasons),
        }
        edge_id = "edge_" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:24]
        edge = EvidenceEdge(
            edge_id=edge_id,
            kind=kind,
            source_id=source_id,
            target_id=target_id,
            evidence_ids=tuple(payload["evidence"]),
            confidence=confidence,
            causal=causal,
            reasons=tuple(reasons),
        )
        current = self._edges.get(edge_id)
        if current is not None and current != edge:
            raise ValueError(f"edge {edge_id} already exists with different evidence")
        self._edges[edge_id] = edge
        return edge

    def correlate_transactions(
        self,
        transactions: Iterable[TransactionEvidence],
        requests: Iterable[RequestObservationEvidence],
    ) -> tuple[TransactionAssociation, ...]:
        transactions = tuple(transactions)
        requests = tuple(requests)
        transaction_by_id = {item.transaction_id: item for item in transactions}
        request_by_id = {item.request_id: item for item in requests}
        associations = associate_transactions(transactions, requests)
        for association in associations:
            transaction = transaction_by_id[association.transaction_id]
            request = request_by_id[association.request_id]
            self.link(
                EvidenceEdgeKind.ACTION_TRIGGERED_REQUEST,
                transaction.action_node_id,
                request.request_node_id,
                confidence=association.confidence,
                causal=True,
                reasons=association.causal_anchors + association.supporting_evidence,
            )
        return associations

    def build(self) -> EvidenceGraph:
        return EvidenceGraph(
            nodes=tuple(self._nodes.values()),
            edges=tuple(self._edges.values()),
        )


__all__ = [
    "EvidenceEdge",
    "EvidenceEdgeKind",
    "EvidenceGraph",
    "EvidenceGraphBuilder",
    "EvidenceNode",
    "EvidenceNodeKind",
    "RequestObservationEvidence",
    "TransactionAssociation",
    "TransactionEvidence",
    "ValueEvidence",
    "ValueRetention",
    "ValueSensitivity",
    "associate_transactions",
    "make_value_evidence",
]
