"""End-to-end deterministic recording compiler and contract integration.

The compiler remains a pure projection of immutable facts.  The integration
step below is the single bridge into the lineage-scoped CaptureStore and
FieldRegistry; keeping it here prevents bootstrap, Pi and publishing from
inventing competing request or field identities.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from typing import Any
from uuid import UUID, uuid5

from dano_recording.analysis.field_resolver import (
    extract_field_facts,
    materialize_field_contracts,
)
from dano_recording.analysis.materializer import infer_json_schema, materialize_requests
from dano_recording.analysis.relation_builder import build_relations
from dano_recording.analysis.request_classifier import classify_requests
from dano_recording.analysis.request_lifecycle import correlate_request_lifecycle
from dano_recording.analysis.transaction_segmenter import segment_transactions
from dano_recording.compiler.fingerprint import content_hash
from dano_recording.compiler.models import RecordingCompilation
from dano_recording.compiler.validator import validate_compilation
from dano_recording.capture_store import CaptureStore, CaptureStoreSnapshot
from dano_recording.capability_planner import plan_capabilities
from dano_recording.domain.facts import ActionFact, FactKind, RecordingFact, RequestFact
from dano_recording.domain.enums import (
    EnumEvidence,
    MappingCoverage,
    SnapshotCoverage,
    SnapshotCoverageKind,
)
from dano_recording.domain.fields import (
    AxisDecision,
    AxisOrigin,
    FieldDecision,
    FieldDimension,
    FieldLocation,
    FieldProposal,
    ProviderKind,
    ProviderBinding,
    RequiredState,
    SourceBinding,
    SourceBindingKind,
)
from dano_recording.evidence_graph import (
    EvidenceGraph,
    EvidenceGraphBuilder,
    EvidenceEdgeKind,
    EvidenceNode,
    EvidenceNodeKind,
    RequestObservationEvidence,
    TransactionEvidence,
)
from dano_recording.field_inference import FieldInferenceEvidence, infer_field
from dano_recording.field_registry import (
    AxisDecisionConflict,
    BindingDirection,
    BindingRole,
    ControlEvidence,
    FieldAlias,
    FieldAliasKind,
    FieldRegistry,
    FieldRegistrySnapshot,
    FieldWireBinding,
)
from dano_recording.value_evidence import ValueEvidence, ValueEvidenceFactory
from dano_recording.value_evidence import ValueSensitivity
from dano_recording.header_contracts import trusted_header_resolver


@dataclass(frozen=True, slots=True)
class RecordingContractProjection:
    """Safe, lineage-scoped contracts produced for one compilation."""

    capture_store: CaptureStoreSnapshot
    field_registry: FieldRegistrySnapshot
    evidence_graph: EvidenceGraph
    request_definition_ids: dict[str, str]
    observation_ids: dict[str, str]
    field_uuids: dict[str, str]
    step_uuids: dict[str, str]
    capability_uuids: dict[str, str]

    def graph_summary(self) -> dict[str, Any]:
        node_kinds: dict[str, int] = defaultdict(int)
        edge_kinds: dict[str, int] = defaultdict(int)
        for node in self.evidence_graph.nodes:
            node_kinds[node.kind.value] += 1
        for edge in self.evidence_graph.edges:
            edge_kinds[edge.kind.value] += 1
        return {
            "node_count": len(self.evidence_graph.nodes),
            "edge_count": len(self.evidence_graph.edges),
            "node_kinds": dict(sorted(node_kinds.items())),
            "edge_kinds": dict(sorted(edge_kinds.items())),
            "request_dependencies": {
                str(node.payload["request_id"]): list(
                    self.evidence_graph.request_dependencies(
                        str(node.payload["request_id"])
                    )
                )
                for node in self.evidence_graph.nodes
                if node.kind is EvidenceNodeKind.REQUEST_OBSERVATION
                and node.payload.get("request_id")
            },
            "raw_javascript_included": False,
        }


def _schema_without_sample(value: Any) -> dict[str, Any]:
    schema = infer_json_schema(value)
    return schema if isinstance(schema, dict) else {}


def _request_schema(compilation: RecordingCompilation, request_id: str) -> dict[str, Any]:
    """Build a location-aware schema; observed values never become defaults."""

    locations: dict[str, dict[str, Any]] = defaultdict(dict)
    required: dict[str, list[str]] = defaultdict(list)
    for field in compilation.field_facts:
        if field.request_id != request_id:
            continue
        schema = field.wire_schema.model_dump(
            mode="json",
            exclude={"sample"},
            exclude_none=True,
        )
        locations[field.location.value][field.wire_path] = schema
        if field.required_by_wire:
            required[field.location.value].append(field.wire_path)
    properties: dict[str, Any] = {}
    for location, fields in sorted(locations.items()):
        item: dict[str, Any] = {
            "type": "object",
            "properties": dict(sorted(fields.items())),
        }
        if required.get(location):
            item["required"] = sorted(set(required[location]))
        properties[location] = item
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }


def _iter_scalar_values(value: Any, *, path: str = "$") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            yield from _iter_scalar_values(item, path=f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            yield from _iter_scalar_values(item, path=f"{path}[{index}]")
        return
    name = path.rsplit(".", 1)[-1].split("[", 1)[0]
    yield path, name, value


def _is_redacted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("[REDACTED")


def _capture_values(
    factory: ValueEvidenceFactory,
    *,
    tenant: str,
    lineage_id: UUID,
    request_id: str,
    direction: str,
    values: Iterable[tuple[str, str, Any]],
) -> tuple[ValueEvidence, ...]:
    output: list[ValueEvidence] = []
    for index, (path, field_name, value) in enumerate(values):
        # Redaction happens before immutable facts are persisted.  A redaction
        # marker is not a value and must never be vaulted or equality-matched.
        if _is_redacted(value):
            continue
        evidence_id = str(
            uuid5(
                lineage_id,
                f"value:{request_id}:{direction}:{path}:{index}",
            )
        )
        try:
            output.append(
                factory.capture(
                    tenant_scope=tenant,
                    recording_lineage=str(lineage_id),
                    value=value,
                    field_name=field_name,
                    evidence_id=evidence_id,
                    value_path=path,
                )
            )
        except RuntimeError as exc:
            # Credentials require a real vault.  If capture only contains a
            # credential-shaped value and no vault is configured, omission is
            # the only safe projection; plaintext is never downgraded to a
            # business sample.
            if "credential" not in str(exc).lower():
                raise
    return tuple(output)


def _request_values(request: Any) -> Iterable[tuple[str, str, Any]]:
    for key, value in request.query:
        yield f"query.{key}", key, value
    for key, value in request.headers.items():
        yield f"header.{key}", str(key), value
    if request.body_present:
        yield from _iter_scalar_values(request.body, path="body")


def _binding_role(source: SourceBinding) -> BindingRole:
    if source.kind in {SourceBindingKind.CALLER, SourceBindingKind.DEFAULT}:
        return BindingRole.CALLER_INPUT
    if source.kind is SourceBindingKind.CONSTANT:
        return BindingRole.CONSTANT
    return BindingRole.RUNTIME_SOURCE


def _wire_tokens(path: str) -> tuple[str | int, ...]:
    output: list[str | int] = []
    for part in path.replace("]", "").replace("[", ".").split("."):
        if not part:
            continue
        output.append(int(part) if part.isdigit() else part)
    return tuple(output)


def _field_value_path(field: Any) -> str:
    prefix = {
        FieldLocation.QUERY: "query.",
        FieldLocation.HEADER: "header.",
        FieldLocation.BODY: "body.",
        FieldLocation.FORM: "body.",
        FieldLocation.PATH: "path.",
    }[field.location]
    return prefix + field.wire_path


def _action_anchor(
    action_id: str | None,
    action_facts_by_id: Mapping[str, ActionFact],
) -> str:
    fact = action_facts_by_id.get(str(action_id or ""))
    if fact is None:
        return "unattributed"
    details = fact.payload.get("details") or {}
    stable_details = {
        key: details.get(key)
        for key in ("selector", "name", "tag", "inputType", "input_type")
        if isinstance(details, Mapping) and details.get(key) not in {None, ""}
    }
    return json.dumps(
        {
            "action_type": fact.action_type,
            "locator": fact.locator,
            "control": stable_details,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def _capability_uuid_map(
    compilation: RecordingCompilation,
    *,
    facts: Iterable[RecordingFact],
    capture_store: CaptureStore,
    request_definition_ids: Mapping[str, str],
) -> dict[str, str]:
    action_facts_by_id = {
        fact.action_id: fact for fact in facts if isinstance(fact, ActionFact)
    }
    transaction_by_id = {
        transaction.transaction_id: transaction
        for transaction in compilation.transactions
    }
    request_by_identity = {
        request.request_id: request for request in compilation.requests
    }
    result: dict[str, str] = {}
    for capability in compilation.capabilities:
        members = [
            request_by_identity[item]
            for item in capability.request_ids
            if item in request_by_identity
        ]
        writes = [
            request
            for request in members
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}
        ]
        terminal_members = writes or members
        terminal_definitions = sorted(
            {
                request_definition_ids[request.request_id]
                for request in terminal_members
            }
        )
        transaction = transaction_by_id.get(capability.transaction_id)
        seed = json.dumps(
            {
                "action": _action_anchor(
                    transaction.action_id if transaction else None,
                    action_facts_by_id,
                ),
                "terminal_request_definitions": terminal_definitions,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        result[capability.capability_id] = str(
            uuid5(capture_store.lineage_id, f"capability:{seed}")
        )
    return result


def _apply_axis(
    registry: FieldRegistry,
    *,
    field_uuid: UUID,
    axis: FieldDimension,
    value: Any,
    evidence_ids: tuple[str, ...],
    revision: int,
) -> None:
    current = registry.get_field(field_uuid).decisions.get(axis)
    if current is not None and current.manual_override:
        return
    if current is not None and current.value == value and current.origin is not AxisOrigin.PI:
        return
    decision = AxisDecision(
        decision_id=str(
            uuid5(field_uuid, f"axis:{axis.value}:{revision}:{repr(value)}")
        ),
        axis=axis,
        value=value,
        origin=AxisOrigin.DETERMINISTIC,
        evidence_ids=evidence_ids,
        confidence=1.0,
        decided_at_revision=revision,
        manual_override=False,
    )
    try:
        registry.apply_axis_decision(field_uuid, decision)
    except AxisDecisionConflict:
        # Observed/manual ownership is stronger than a compiler refinement.
        return


def integrate_compilation_contracts(
    compilation: RecordingCompilation,
    *,
    facts: Iterable[RecordingFact],
    capture_store: CaptureStore,
    field_registry: FieldRegistry,
    value_evidence_factory: ValueEvidenceFactory,
) -> RecordingContractProjection:
    """Connect one deterministic compilation to its permanent contracts.

    Request definitions are deduplicated by CaptureStore while the immutable
    request fact id is used as the observation id, so repeated calls are all
    retained.  Re-analysis is idempotent and true re-capture uses a new store
    generation without replacing the lineage-scoped FieldRegistry.
    """

    if capture_store.tenant_scope != compilation.tenant:
        raise ValueError("capture store tenant does not match compilation")
    if capture_store.recording_id != compilation.recording_id:
        raise ValueError("capture store recording does not match compilation")
    if capture_store.lineage_id != field_registry.lineage_id:
        raise ValueError("capture store and field registry lineage differ")

    fact_rows = tuple(facts)
    request_facts = {
        fact.request_id: fact for fact in fact_rows if isinstance(fact, RequestFact)
    }
    response_times: dict[str, datetime] = {}
    response_evidence_by_request: dict[str, tuple[ValueEvidence, ...]] = {}
    for fact in fact_rows:
        if fact.kind is FactKind.RESPONSE:
            request_id = str(fact.payload.get("request_id") or "")
            if request_id:
                response_times[request_id] = fact.observed_at
                raw_evidence = fact.payload.get("response_value_evidence") or ()
                response_evidence_by_request[request_id] = tuple(
                    ValueEvidence.model_validate(item)
                    for item in raw_evidence
                    if isinstance(item, dict)
                )

    existing_observations = {
        item.observation_id: item for item in capture_store.snapshot().observations
    }
    request_definition_ids: dict[str, str] = {}
    observation_ids: dict[str, str] = {}
    observation_models: dict[str, Any] = {}
    for request in compilation.requests:
        request_fact = request_facts.get(request.request_id)
        started = request_fact.observed_at if request_fact else datetime.now(timezone.utc)
        finished = response_times.get(request.request_id, started)
        schema = _request_schema(compilation, request.request_id)
        response_schema = request.response_schema or _schema_without_sample(
            request.response_body
        )
        fact_evidence = (
            request_fact.payload.get("request_value_evidence") or ()
            if request_fact is not None
            else ()
        )
        request_values = tuple(
            ValueEvidence.model_validate(item)
            for item in fact_evidence
            if isinstance(item, dict)
        ) or _capture_values(
            value_evidence_factory,
            tenant=compilation.tenant,
            lineage_id=capture_store.lineage_id,
            request_id=request.request_id,
            direction="request",
            values=_request_values(request),
        )
        response_values = response_evidence_by_request.get(request.request_id, ()) or _capture_values(
            value_evidence_factory,
            tenant=compilation.tenant,
            lineage_id=capture_store.lineage_id,
            request_id=request.request_id,
            direction="response",
            values=_iter_scalar_values(request.response_body, path="response")
            if request.response_body is not None
            else (),
        )
        observation = existing_observations.get(request.request_id)
        if observation is None:
            observation = capture_store.record_network_call(
                method=request.method,
                url_or_path=request.url,
                page_id=(request_fact.page_id if request_fact else None) or "page:unknown",
                frame_id=str(request_fact.payload.get("frame_id") or "") or None
                if request_fact
                else None,
                action_id=request_fact.action_id if request_fact else None,
                started_at=started,
                finished_at=max(started, finished),
                status=request.response_status or 0,
                request_schema=schema,
                response_schema=response_schema,
                request_values=request_values,
                response_values=response_values,
                initiator=dict(request_fact.payload) if request_fact else {},
                business_request=request.resource_type in {"fetch", "xhr"}
                if hasattr(request, "resource_type")
                else True,
                observation_id=request.request_id,
            )
        request_definition_ids[request.request_id] = str(
            observation.request_definition_id
        )
        observation_ids[request.request_id] = observation.observation_id
        observation_models[request.request_id] = observation

    for capability in compilation.capabilities:
        for request_id in capability.request_ids:
            observation_id = observation_ids.get(request_id)
            if observation_id is not None:
                capture_store.bind_observation(observation_id)

    field_uuids: dict[str, str] = {}
    action_facts_by_id = {
        fact.action_id: fact
        for fact in fact_rows
        if isinstance(fact, ActionFact)
    }

    step_uuids: dict[str, str] = {}
    for request in compilation.requests:
        observation = observation_models[request.request_id]
        seed = (
            f"step:{observation.request_definition_id}:"
            f"{_action_anchor(observation.action_id, action_facts_by_id)}"
        )
        step_uuids[request.request_id] = str(uuid5(capture_store.lineage_id, seed))

    capability_uuids = _capability_uuid_map(
        compilation,
        facts=fact_rows,
        capture_store=capture_store,
        request_definition_ids=request_definition_ids,
    )
    effective_by_id = {field.field_contract_id: field for field in compilation.fields}
    field_name_counts: dict[str, int] = defaultdict(int)
    for item in compilation.field_facts:
        field_name_counts[item.wire_name.casefold()] += 1
    controls_by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    control_facts_by_name: dict[str, list[RecordingFact]] = defaultdict(list)
    action_facts_by_name: dict[str, list[ActionFact]] = defaultdict(list)
    for fact in fact_rows:
        if fact.kind is FactKind.DOM_CONTROL:
            control_id = str(fact.payload.get("control_id") or "")
            control_name = str(fact.payload.get("name") or "").casefold()
            selector = str(fact.payload.get("selector") or "")
            if control_id and control_name:
                controls_by_name[control_name].append((control_id, selector))
                control_facts_by_name[control_name].append(fact)
        if isinstance(fact, ActionFact):
            details = fact.payload.get("details") or {}
            if isinstance(details, Mapping):
                action_name = str(
                    details.get("name")
                    or details.get("field_name")
                    or details.get("fieldName")
                    or ""
                ).casefold()
                if action_name:
                    action_facts_by_name[action_name].append(fact)
    compiled_request_by_id = {
        item.request_id: item for item in compilation.requests
    }
    registry_controls = {
        item.evidence_id: item for item in field_registry.snapshot().controls
    }
    for field_fact in compilation.field_facts:
        definition_text = request_definition_ids.get(field_fact.request_id)
        if not definition_text:
            continue
        definition_id = UUID(definition_text)
        context = f"request-definition:{definition_id}:{field_fact.location.value}"
        wire_alias = FieldAlias(
            kind=FieldAliasKind.WIRE_PATH,
            value=field_fact.wire_path,
            context=context,
            introduced_at_revision=compilation.source_revision,
        )
        legacy_alias = FieldAlias(
            kind=FieldAliasKind.LEGACY_ID,
            value=field_fact.field_contract_id,
            context="lineage",
            introduced_at_revision=compilation.source_revision,
        )
        business_alias = FieldAlias(
            kind=FieldAliasKind.BUSINESS_NAME,
            value=field_fact.wire_name,
            context=context,
            introduced_at_revision=compilation.source_revision,
        )
        control_aliases: list[FieldAlias] = []
        matched_controls = controls_by_name.get(field_fact.wire_name.casefold(), [])
        if field_name_counts[field_fact.wire_name.casefold()] == 1 and len(matched_controls) == 1:
            control_id, selector = matched_controls[0]
            control_aliases.append(
                FieldAlias(
                    kind=FieldAliasKind.CONTROL,
                    value=control_id,
                    context="lineage-control",
                    introduced_at_revision=compilation.source_revision,
                )
            )
            if selector:
                control_aliases.append(
                    FieldAlias(
                        kind=FieldAliasKind.EXTERNAL,
                        value=selector,
                        context="dom-selector",
                        introduced_at_revision=compilation.source_revision,
                    )
                )
        canonical = next(
            (
                field_registry.resolve_alias(alias)
                for alias in control_aliases
                if field_registry.resolve_alias(alias) is not None
            ),
            None,
        )
        if canonical is None:
            canonical = field_registry.resolve_alias(wire_alias)
        if canonical is None:
            canonical = field_registry.resolve_alias(legacy_alias)
        canonical = field_registry.register_field(
            field_uuid=canonical.field_uuid if canonical else None,
            aliases=(*control_aliases, wire_alias, legacy_alias, business_alias),
        )
        field_uuid = canonical.field_uuid
        field_uuids[field_fact.field_contract_id] = str(field_uuid)
        effective = effective_by_id[field_fact.field_contract_id]
        step_uuid = UUID(step_uuids[field_fact.request_id])
        binding_id = uuid5(
            field_uuid,
            f"binding:{definition_id}:{step_uuid}:{field_fact.location.value}:{field_fact.wire_path}",
        )
        sample_observed = bool(field_fact.observed_values)
        sample_value = field_fact.observed_values[0] if sample_observed else None
        sensitivity = value_evidence_factory.classify(
            field_name=field_fact.wire_name,
            value=sample_value,
        )
        caller_location = field_fact.location in {
            FieldLocation.PATH,
            FieldLocation.QUERY,
            FieldLocation.BODY,
            FieldLocation.FORM,
        }
        normalized_name = field_fact.wire_name.casefold().replace("-", "_")
        identity_resolver = None
        if sensitivity is ValueSensitivity.IDENTITY:
            identity_resolver = (
                "runtime_context.current_tenant.id"
                if "tenant" in normalized_name
                else "runtime_context.current_user.id"
            )
        header_resolver = (
            trusted_header_resolver(field_fact.wire_name)
            if field_fact.location is FieldLocation.HEADER
            else None
        )
        protected_internal = sensitivity in {
            ValueSensitivity.CREDENTIAL,
            ValueSensitivity.IDENTITY,
        }
        control_fact = next(
            iter(control_facts_by_name.get(field_fact.wire_name.casefold(), ())),
            None,
        ) if (
            field_name_counts[field_fact.wire_name.casefold()] == 1
            and len(control_facts_by_name.get(field_fact.wire_name.casefold(), ())) == 1
        ) else None
        if control_fact is not None:
            control_payload = control_fact.payload
            control_id = str(control_payload.get("control_id") or "")
            control_evidence_id = f"dom:{control_id}:{canonical.field_uuid}"
            if control_id and control_evidence_id not in registry_controls:
                initial_evidence = tuple(
                    ValueEvidence.model_validate(item)
                    for item in control_payload.get("initial_value_evidence") or ()
                    if isinstance(item, Mapping)
                )
                registered = ControlEvidence(
                    evidence_id=control_evidence_id,
                    field_uuid=canonical.field_uuid,
                    page_id=control_fact.page_id or "page:unknown",
                    frame_id=str(control_payload.get("frame_id") or "frame:main"),
                    form_id=str(control_payload.get("form_id") or "document"),
                    control_locator={
                        "control_id": control_id,
                        "selector": str(control_payload.get("selector") or ""),
                        "tag": str(control_payload.get("tag") or ""),
                        "name": str(control_payload.get("name") or ""),
                    },
                    label=str(control_payload.get("label") or "") or None,
                    role=str(
                        control_payload.get("role")
                        or control_payload.get("input_type")
                        or ""
                    ) or None,
                    native_control_type=str(
                        control_payload.get("input_type")
                        or control_payload.get("tag")
                        or ""
                    ) or None,
                    aria_role=str(control_payload.get("role") or "") or None,
                    readonly=bool(control_payload.get("readonly")),
                    disabled=bool(control_payload.get("disabled")),
                    required=(
                        bool(control_payload.get("required"))
                        if "required" in control_payload
                        else None
                    ),
                    initial_value=control_payload.get("initial_value"),
                    initial_value_observed=bool(
                        control_payload.get("initial_value_observed")
                    ),
                    initial_value_evidence=initial_evidence,
                    options_sensitive=bool(control_payload.get("options_sensitive")),
                    option_count=int(control_payload.get("option_count") or 0),
                    option_runtime_resolver=(
                        str(control_payload.get("option_runtime_resolver") or "")
                        or None
                    ),
                )
                canonical = field_registry.add_control_evidence(registered)
                registry_controls[control_evidence_id] = registered
        registered_control = next(
            (
                registry_controls[evidence_id]
                for evidence_id in canonical.control_evidence_ids
                if evidence_id in registry_controls
            ),
            None,
        )
        control_payload: Mapping[str, Any] = (
            control_fact.payload if control_fact is not None else {}
        )
        native_control_type = str(
            (registered_control.native_control_type if registered_control else None)
            or control_payload.get("input_type")
            or ("select" if control_payload.get("tag") == "select" else "")
            or ("textarea" if control_payload.get("tag") == "textarea" else "")
        ) or None
        aria_role = str(
            (registered_control.aria_role if registered_control else None)
            or control_payload.get("role")
            or ""
        ) or None
        component_role = (
            registered_control.component_role if registered_control else None
        )
        initial_observed = bool(
            (registered_control.initial_value_observed if registered_control else False)
            or control_payload.get("initial_value_observed")
        )
        initial_value = (
            registered_control.initial_value
            if registered_control and registered_control.initial_value_observed
            else control_payload.get("initial_value")
        )
        if sensitivity in {
            ValueSensitivity.CREDENTIAL,
            ValueSensitivity.IDENTITY,
            ValueSensitivity.PII,
        }:
            # Sensitive defaults are useful only as boundary-created evidence,
            # never as plaintext/default constants.
            initial_value = None
            initial_observed = False

        value_path = _field_value_path(field_fact)
        observation = observation_models.get(field_fact.request_id)
        request_evidence = tuple(
            item
            for item in (observation.request_values if observation else ())
            if item.value_path == value_path
        )
        request_hmacs = {
            item.scoped_hmac for item in request_evidence if item.scoped_hmac
        }
        matching_actions: list[tuple[ActionFact, ValueEvidence]] = []
        for action in action_facts_by_name.get(field_fact.wire_name.casefold(), ()):
            if not (
                action.payload.get("causal_eligible") is True
                and action.payload.get("evidence_origin") == "server_dispatched"
            ):
                continue
            details = action.payload.get("details") or {}
            if not isinstance(details, Mapping):
                continue
            for raw in details.get("value_evidence") or ():
                if not isinstance(raw, Mapping):
                    continue
                action_evidence = ValueEvidence.model_validate(raw)
                if action_evidence.scoped_hmac in request_hmacs:
                    matching_actions.append((action, action_evidence))
        user_changed = len(matching_actions) == 1
        user_action_type = matching_actions[0][0].action_type if user_changed else None
        user_action_value = (
            matching_actions[0][1].redacted_sample if user_changed else None
        )

        exact_candidates: dict[tuple[str, str, str], ValueEvidence] = {}
        target_request = compiled_request_by_id[field_fact.request_id]
        if registered_control is not None:
            control_selector = str(
                registered_control.control_locator.get("selector") or ""
            )
            control_name = str(
                registered_control.control_locator.get("name") or ""
            )
            for mutation_fact in fact_rows:
                if (
                    mutation_fact.kind is not FactKind.DOM_MUTATION
                    or mutation_fact.sequence >= target_request.sequence
                    or str(mutation_fact.payload.get("phase") or "")
                    != "after_response"
                ):
                    continue
                source_request_id = str(
                    mutation_fact.payload.get("request_id") or ""
                )
                source_request = compiled_request_by_id.get(source_request_id)
                if source_request is None or source_request.sequence >= mutation_fact.sequence:
                    continue
                source_observation = observation_models.get(source_request_id)
                for raw_mutation in mutation_fact.payload.get("mutations") or ():
                    if not isinstance(raw_mutation, Mapping):
                        continue
                    if not (
                        raw_mutation.get("causal_eligible") is True
                        and raw_mutation.get("evidence_origin") == "server_snapshot"
                    ):
                        continue
                    selector = str(raw_mutation.get("selector") or "")
                    name = str(raw_mutation.get("name") or "")
                    if not (
                        (control_selector and selector == control_selector)
                        or (control_name and name == control_name)
                    ):
                        continue
                    mutation_values = tuple(
                        ValueEvidence.model_validate(item)
                        for item in raw_mutation.get("value_evidence") or ()
                        if isinstance(item, Mapping)
                    )
                    mutation_hmacs = {
                        item.scoped_hmac for item in mutation_values if item.scoped_hmac
                    }
                    for target_evidence in request_evidence:
                        if (
                            not target_evidence.scoped_hmac
                            or target_evidence.scoped_hmac not in mutation_hmacs
                            or target_evidence.sensitivity
                            not in {ValueSensitivity.BUSINESS, ValueSensitivity.NONE}
                        ):
                            continue
                        for source_evidence in (
                            source_observation.response_values
                            if source_observation else ()
                        ):
                            if (
                                source_evidence.scoped_hmac
                                == target_evidence.scoped_hmac
                                and source_evidence.sensitivity
                                in {ValueSensitivity.BUSINESS, ValueSensitivity.NONE}
                                and source_evidence.value_path
                            ):
                                exact_candidates[
                                    (
                                        source_request_id,
                                        source_evidence.value_path,
                                        source_evidence.evidence_id,
                                    )
                                ] = target_evidence
        exact_response_provider = None
        exact_evidence_ids: tuple[str, ...] = ()
        if len(exact_candidates) == 1:
            (source_request_id, response_path, source_evidence_id), target_evidence = next(
                iter(exact_candidates.items())
            )
            exact_response_provider = ProviderBinding(
                kind=ProviderKind.DEPENDENCY_RESPONSE,
                request_definition_id=request_definition_ids[source_request_id],
                response_path=response_path.removeprefix("response."),
            )
            exact_evidence_ids = (source_evidence_id, target_evidence.evidence_id)

        control_present = control_fact is not None or registered_control is not None
        control_readonly = bool(
            (registered_control.readonly if registered_control else False)
            or control_payload.get("readonly")
        )
        inferred = infer_field(
            FieldInferenceEvidence(
                field_uuid=field_uuid,
                request_id=field_fact.request_id,
                wire_path=field_fact.wire_path,
                wire_name=field_fact.wire_name,
                location=field_fact.location.value,
                native_control_type=native_control_type,
                aria_role=aria_role,
                component_role=component_role,
                user_action_type=user_action_type,
                user_changed=user_changed,
                wire_schema_type=field_fact.wire_schema.type,
                exact_response_provider=exact_response_provider,
                runtime_resolver=identity_resolver or header_resolver,
                sample_value=sample_value,
                sample_observed=sample_observed,
                page_initial_value=initial_value,
                page_initial_observed=initial_observed,
                user_action_value=user_action_value,
                wire_required=(
                    RequiredState.TRUE
                    if field_fact.required_by_wire
                    else RequiredState.FALSE
                ),
                caller_required=RequiredState.UNKNOWN,
                internal=field_fact.location is FieldLocation.HEADER or protected_internal,
                caller_must_supply=caller_location
                and not protected_internal
                and exact_response_provider is None
                and (
                    user_changed
                    or not control_present
                    or (not initial_observed and not control_readonly)
                ),
                # A uniquely bound control provides stronger source evidence:
                # real change => caller, captured initial value => default.
                # Readonly controls never become caller inputs by presence.
                classification=sensitivity.value,
                evidence_ids={
                    "wire_schema": field_fact.evidence_ids,
                    "caller_contract": field_fact.evidence_ids,
                    "sample": field_fact.evidence_ids,
                    "native_control": (
                        (control_fact.fact_id,) if control_fact is not None else ()
                    ),
                    "user_action": tuple(
                        item.evidence_id for _, item in matching_actions
                    ),
                    "page_default": tuple(
                        item.evidence_id
                        for item in (
                            registered_control.initial_value_evidence
                            if registered_control else ()
                        )
                    ),
                    "exact_response": exact_evidence_ids,
                },
            )
        )
        source = inferred.source_binding
        # Wire-binding role is derived only after the canonical field
        # inference result exists.  The earlier wire materializer never owns
        # source semantics.
        field_registry.add_wire_binding(
            FieldWireBinding(
                binding_id=binding_id,
                field_uuid=field_uuid,
                request_definition_id=definition_id,
                observation_ids=(observation_ids[field_fact.request_id],),
                step_uuid=step_uuid,
                direction=BindingDirection.INPUT,
                wire_path=field_fact.wire_path,
                wire_tokens=_wire_tokens(field_fact.wire_path),
                binding_role=_binding_role(source),
            )
        )
        axes: dict[FieldDimension, Any] = {
            FieldDimension.DISPLAY_NAME: effective.name,
            FieldDimension.BUSINESS_TYPE: inferred.business_type,
            FieldDimension.CLASSIFICATION: inferred.classification,
            FieldDimension.SOURCE_BINDING: source,
            FieldDimension.CALLER_REQUIRED: inferred.required.caller_required,
            FieldDimension.WIRE_REQUIRED: inferred.required.wire_required,
            FieldDimension.EXPOSURE: inferred.exposed,
        }
        if inferred.default_observed:
            axes[FieldDimension.DEFAULT_VALUE] = inferred.default_value
        if inferred.required.wire_condition or inferred.required.caller_condition:
            axes[FieldDimension.REQUIRED_CONDITIONS] = {
                "wire_condition": inferred.required.wire_condition.model_dump(mode="json")
                if inferred.required.wire_condition else None,
                "caller_condition": inferred.required.caller_condition.model_dump(mode="json")
                if inferred.required.caller_condition else None,
            }
        if registered_control is not None and registered_control.options_sensitive:
            grounded_enum: EnumEvidence | None = None
            if (
                effective.choice_contract is not None
                and effective.choice_contract.enum_evidence is not None
            ):
                candidate = effective.choice_contract.enum_evidence
                if candidate.source_query is not None:
                    grounded_enum = candidate
            existing_enum = canonical.decisions.get(FieldDimension.ENUM_BINDING)
            if grounded_enum is None and existing_enum is not None:
                raw_enum = existing_enum.value
                if isinstance(raw_enum, Mapping):
                    nested = raw_enum.get("enum_evidence")
                    if isinstance(nested, Mapping):
                        raw_enum = nested
                    try:
                        candidate = EnumEvidence.model_validate(raw_enum)
                    except (TypeError, ValueError):
                        candidate = None
                    if candidate is not None and candidate.source_query is not None:
                        grounded_enum = candidate
            if grounded_enum is not None:
                axes[FieldDimension.ENUM_BINDING] = {
                    **grounded_enum.model_dump(mode="json", exclude_none=True),
                    "mapping_coverage": MappingCoverage.RUNTIME_RESOLVABLE.value,
                    "static_values_retained": False,
                    "identity_options": True,
                }
            else:
                unknown_enum = EnumEvidence(
                    mapping_coverage=MappingCoverage.UNKNOWN,
                    snapshot_coverage=SnapshotCoverage(
                        kind=SnapshotCoverageKind.UNKNOWN,
                        observed_count=registered_control.option_count,
                        truncated=True,
                    ),
                    evidence_ids=(registered_control.evidence_id,),
                )
                axes[FieldDimension.ENUM_BINDING] = {
                    **unknown_enum.model_dump(mode="json", exclude_none=True),
                    "static_values_retained": False,
                    "identity_options": True,
                    "contract_fault": {
                        "code": "identity_enum_source_query_missing",
                        "message": (
                            "identity/person options require a grounded runtime "
                            "EnumSourceQuery"
                        ),
                    },
                }
        elif effective.choice_contract is not None:
            axes[FieldDimension.ENUM_BINDING] = effective.choice_contract.model_dump(
                mode="json"
            )
        for axis, value in axes.items():
            _apply_axis(
                field_registry,
                field_uuid=field_uuid,
                axis=axis,
                value=value,
                evidence_ids=field_fact.evidence_ids,
                revision=compilation.source_revision,
            )

    graph_builder = EvidenceGraphBuilder()
    transaction_evidence: list[TransactionEvidence] = []
    request_evidence: list[RequestObservationEvidence] = []
    facts_by_sequence = {fact.sequence: fact for fact in fact_rows}
    registry_snapshot = field_registry.snapshot()
    graph_controls = tuple(registry_snapshot.controls)

    def controls_for_action(fact: ActionFact | None) -> tuple[str, ...]:
        if fact is None or fact.action_type not in {
            "fill", "type", "input", "change", "select_option", "check", "uncheck"
        }:
            return ()
        details = fact.payload.get("details") or {}
        if not isinstance(details, Mapping):
            return ()
        selector = str(fact.locator or details.get("selector") or "")
        name = str(details.get("name") or details.get("field_name") or "")
        candidates = []
        for control in graph_controls:
            locator = control.control_locator
            if fact.page_id and control.page_id != fact.page_id:
                continue
            selector_match = bool(
                selector and selector == str(locator.get("selector") or "")
            )
            name_match = bool(name and name == str(locator.get("name") or ""))
            if selector_match or name_match:
                candidates.append(control.evidence_id)
        # Ambiguous DOM names/selectors are not causal evidence.
        return tuple(candidates) if len(candidates) == 1 else ()

    changed_controls_by_action: dict[str, tuple[str, ...]] = {}
    action_node_by_action_id: dict[str, str] = {}
    for transaction in compilation.transactions:
        action_fact = next(
            (
                fact
                for fact in fact_rows
                if isinstance(fact, ActionFact)
                and fact.action_id == transaction.action_id
            ),
            None,
        )
        action_node_id = f"action:{transaction.transaction_id}"
        changed_control_ids = controls_for_action(action_fact)
        if transaction.action_id:
            changed_controls_by_action[transaction.action_id] = changed_control_ids
            action_node_by_action_id[transaction.action_id] = action_node_id
        graph_builder.add_node(
            EvidenceNode(
                node_id=action_node_id,
                kind=EvidenceNodeKind.ACTION,
                page_id=action_fact.page_id if action_fact else None,
                action_id=transaction.action_id,
                transaction_id=transaction.transaction_id,
                payload={"label": transaction.action_label},
            )
        )
        started_fact = facts_by_sequence.get(transaction.first_sequence)
        finished_fact = facts_by_sequence.get(transaction.last_sequence)
        transaction_evidence.append(
            TransactionEvidence(
                transaction_id=transaction.transaction_id,
                action_node_id=action_node_id,
                action_id=transaction.action_id,
                page_id=action_fact.page_id if action_fact else None,
                started_at=started_fact.observed_at if started_fact else None,
                finished_at=finished_fact.observed_at if finished_fact else None,
                changed_control_ids=changed_control_ids,
            )
        )
    for request in compilation.requests:
        observation = observation_models[request.request_id]
        request_evidence.append(
            graph_builder.add_network_observation(
                observation,
                request_id=request.request_id,
                changed_control_ids=changed_controls_by_action.get(
                    observation.action_id or "", ()
                ),
            )
        )
    graph_builder.correlate_transactions(transaction_evidence, request_evidence)

    request_node_ids = {
        item.request_id: item.request_node_id for item in request_evidence
    }
    field_uuid_by_wire: dict[tuple[str, str], str] = {}
    for field_fact in compilation.field_facts:
        prefix = {
            FieldLocation.QUERY: "query.",
            FieldLocation.HEADER: "header.",
            FieldLocation.BODY: "body.",
            FieldLocation.FORM: "body.",
            FieldLocation.PATH: "path.",
        }[field_fact.location]
        field_uuid = field_uuids.get(field_fact.field_contract_id)
        if field_uuid:
            field_uuid_by_wire[(field_fact.request_id, prefix + field_fact.wire_path)] = field_uuid

    response_nodes_by_hmac: dict[str, list[tuple[ValueEvidence, EvidenceNode]]] = defaultdict(list)
    submit_nodes_by_hmac: dict[str, list[tuple[ValueEvidence, EvidenceNode]]] = defaultdict(list)
    submit_nodes_by_field: dict[str, list[EvidenceNode]] = defaultdict(list)
    for request in compilation.requests:
        observation = observation_models[request.request_id]
        request_node_id = request_node_ids[request.request_id]
        for evidence in observation.response_values:
            node = graph_builder.add_node(
                EvidenceNode(
                    node_id=f"response-field:{evidence.evidence_id}",
                    kind=EvidenceNodeKind.RESPONSE_FIELD,
                    page_id=observation.page_id,
                    frame_id=observation.frame_id,
                    action_id=observation.action_id,
                    transaction_id=request.transaction_id,
                    payload={
                        "request_id": request.request_id,
                        "value_path": evidence.value_path,
                        "field_name": evidence.field_name,
                        "evidence_id": evidence.evidence_id,
                        "sensitivity": evidence.sensitivity.value,
                    },
                )
            )
            graph_builder.link(
                EvidenceEdgeKind.REQUEST_RETURNED_FIELD,
                request_node_id,
                node.node_id,
                evidence_ids=(evidence.evidence_id,),
                reasons=("captured_response_field",),
            )
            if evidence.scoped_hmac:
                response_nodes_by_hmac[evidence.scoped_hmac].append((evidence, node))
        for evidence in observation.request_values:
            field_uuid = field_uuid_by_wire.get(
                (request.request_id, str(evidence.value_path or ""))
            )
            node = graph_builder.add_node(
                EvidenceNode(
                    node_id=f"submit-field:{evidence.evidence_id}",
                    kind=EvidenceNodeKind.SUBMIT_FIELD,
                    page_id=observation.page_id,
                    frame_id=observation.frame_id,
                    action_id=observation.action_id,
                    transaction_id=request.transaction_id,
                    payload={
                        "request_id": request.request_id,
                        "value_path": evidence.value_path,
                        "field_name": evidence.field_name,
                        "field_uuid": field_uuid,
                        "evidence_id": evidence.evidence_id,
                        "sensitivity": evidence.sensitivity.value,
                    },
                )
            )
            if field_uuid:
                submit_nodes_by_field[field_uuid].append(node)
            if evidence.scoped_hmac:
                submit_nodes_by_hmac[evidence.scoped_hmac].append((evidence, node))

    # Equality is useful corroboration for every scalar (including 0/1/8), but
    # remains explicitly non-causal.  Causality is established below only by
    # an observed response-phase DOM mutation on a permanently bound control.
    for fingerprint in sorted(set(response_nodes_by_hmac) & set(submit_nodes_by_hmac)):
        responses = response_nodes_by_hmac[fingerprint]
        submits = submit_nodes_by_hmac[fingerprint]
        for response_evidence, response_node in responses:
            for submit_evidence, submit_node in submits:
                graph_builder.link(
                    EvidenceEdgeKind.VALUES_EQUAL_IN_SCOPE,
                    response_node.node_id,
                    submit_node.node_id,
                    evidence_ids=(response_evidence.evidence_id, submit_evidence.evidence_id),
                    causal=False,
                    reasons=("scoped_hmac_equality",),
                )
    controls_by_evidence = {
        item.evidence_id: item for item in registry_snapshot.controls
    }
    control_node_ids: dict[str, str] = {}
    for canonical in registry_snapshot.fields:
        for evidence_id in canonical.control_evidence_ids:
            control = controls_by_evidence.get(evidence_id)
            if control is None:
                continue
            control_node = graph_builder.add_node(
                EvidenceNode(
                    node_id=f"control:{control.evidence_id}",
                    kind=EvidenceNodeKind.CONTROL,
                    page_id=control.page_id,
                    frame_id=control.frame_id,
                    payload={
                        "field_uuid": str(control.field_uuid),
                        "control_evidence_id": control.evidence_id,
                        "form_id": control.form_id,
                    },
                )
            )
            control_node_ids[control.evidence_id] = control_node.node_id
            for action_id, changed_control_ids in changed_controls_by_action.items():
                if control.evidence_id not in changed_control_ids:
                    continue
                action_node_id = action_node_by_action_id.get(action_id)
                if action_node_id:
                    graph_builder.link(
                        EvidenceEdgeKind.ACTION_CHANGED_CONTROL,
                        action_node_id,
                        control_node.node_id,
                        evidence_ids=(control.evidence_id,),
                        causal=True,
                        reasons=("captured_control_change_action",),
                    )
            for submit_node in submit_nodes_by_field.get(str(canonical.field_uuid), ()):
                graph_builder.link(
                    EvidenceEdgeKind.CONTROL_BOUND_TO_WIRE,
                    control_node.node_id,
                    submit_node.node_id,
                    evidence_ids=(control.evidence_id,),
                    causal=True,
                    reasons=("permanent_field_wire_binding",),
                )

    # A response/control edge requires three independent anchors: the drain is
    # explicitly tied to that response fact, the mutated control resolves
    # uniquely to permanent control evidence, and the boundary HMAC matches a
    # field returned by that same response.  A later snapshot is never enough.
    for mutation_fact in fact_rows:
        if mutation_fact.kind is not FactKind.DOM_MUTATION:
            continue
        if str(mutation_fact.payload.get("phase") or "") != "after_response":
            continue
        source_request_id = str(mutation_fact.payload.get("request_id") or "")
        source_observation = observation_models.get(source_request_id)
        if source_observation is None:
            continue
        source_by_hmac = {
            item.scoped_hmac: item
            for item in source_observation.response_values
            if item.scoped_hmac
        }
        for index, raw_mutation in enumerate(mutation_fact.payload.get("mutations") or ()):
            if not isinstance(raw_mutation, Mapping):
                continue
            if not (
                raw_mutation.get("causal_eligible") is True
                and raw_mutation.get("evidence_origin") == "server_snapshot"
            ):
                continue
            selector = str(raw_mutation.get("selector") or "")
            name = str(raw_mutation.get("name") or "")
            control_candidates = []
            for control in graph_controls:
                locator = control.control_locator
                if mutation_fact.page_id and control.page_id != mutation_fact.page_id:
                    continue
                if (
                    selector and selector == str(locator.get("selector") or "")
                ) or (
                    name and name == str(locator.get("name") or "")
                ):
                    control_candidates.append(control)
            if len(control_candidates) != 1:
                continue
            control = control_candidates[0]
            control_node_id = control_node_ids.get(control.evidence_id)
            if not control_node_id:
                continue
            mutation_node = graph_builder.add_node(
                EvidenceNode(
                    node_id=f"dom-mutation:{mutation_fact.fact_id}:{index}",
                    kind=EvidenceNodeKind.DOM_MUTATION,
                    page_id=mutation_fact.page_id,
                    frame_id=str(mutation_fact.payload.get("frame_id") or "") or None,
                    action_id=mutation_fact.action_id,
                    payload={
                        "fact_id": mutation_fact.fact_id,
                        "request_id": source_request_id,
                        "selector": selector,
                        "name": name,
                        "mutation_type": raw_mutation.get("mutation_type"),
                        "control_evidence_id": control.evidence_id,
                    },
                )
            )
            for raw_value_evidence in raw_mutation.get("value_evidence") or ():
                if not isinstance(raw_value_evidence, Mapping):
                    continue
                mutation_value = ValueEvidence.model_validate(raw_value_evidence)
                response_value = source_by_hmac.get(mutation_value.scoped_hmac)
                if response_value is None:
                    continue
                response_node_candidates = response_nodes_by_hmac.get(
                    mutation_value.scoped_hmac or "", ()
                )
                response_node = next(
                    (
                        node
                        for evidence, node in response_node_candidates
                        if evidence.evidence_id == response_value.evidence_id
                    ),
                    None,
                )
                if response_node is None:
                    continue
                graph_builder.link(
                    EvidenceEdgeKind.RESPONSE_POPULATED_CONTROL,
                    response_node.node_id,
                    control_node_id,
                    evidence_ids=(
                        mutation_fact.fact_id,
                        mutation_node.node_id,
                        response_value.evidence_id,
                        mutation_value.evidence_id,
                        control.evidence_id,
                    ),
                    causal=True,
                    reasons=(
                        "response_scoped_mutation_drain",
                        "unique_permanent_control",
                        "boundary_hmac_match",
                    ),
                )
    graph = graph_builder.build()
    return RecordingContractProjection(
        capture_store=capture_store.snapshot(),
        field_registry=field_registry.snapshot(),
        evidence_graph=graph,
        request_definition_ids=request_definition_ids,
        observation_ids=observation_ids,
        field_uuids=field_uuids,
        step_uuids=step_uuids,
        capability_uuids=capability_uuids,
    )


def prepare_recording_materials(
    *,
    tenant: str,
    recording_id: str,
    facts: Iterable[RecordingFact],
    proposals: Iterable[FieldProposal] = (),
    decisions: Iterable[FieldDecision] = (),
    source_revision: int = 0,
) -> RecordingCompilation:
    """Build immutable wire materials without semantic inference or planning.

    Finalization needs the request/field ledger to construct the evidence
    graph before capability planning.  Using this explicit preparation stage
    avoids running ``infer_field`` or ``plan_capabilities`` on a throw-away
    compilation.
    """

    facts = tuple(facts)
    for fact in facts:
        if fact.tenant != tenant or fact.recording_id != recording_id:
            raise ValueError(
                f"fact {fact.fact_id} belongs to {fact.tenant}/{fact.recording_id}, "
                f"not {tenant}/{recording_id}"
            )
    transactions = segment_transactions(facts)
    requests = correlate_request_lifecycle(facts)
    analyses = classify_requests(requests)
    compiled_requests = materialize_requests(requests, analyses, transactions)
    field_facts = extract_field_facts(compiled_requests)
    fields = materialize_field_contracts(field_facts, proposals, decisions)
    validation = validate_compilation(requests, analyses, compiled_requests, ())
    payload = {
        "protocol": "dano.recording-v3.compilation.v1",
        "tenant": tenant,
        "recording_id": recording_id,
        "source_revision": source_revision,
        "transactions": transactions,
        "request_analyses": analyses,
        "requests": compiled_requests,
        "field_facts": field_facts,
        "fields": fields,
        "capabilities": (),
        "relations": (),
        "validation": validation,
    }
    return RecordingCompilation(**payload, content_hash=content_hash(payload))


def complete_contract_projection(
    compilation: RecordingCompilation,
    *,
    facts: Iterable[RecordingFact],
    capture_store: CaptureStore,
    contracts: RecordingContractProjection,
) -> RecordingContractProjection:
    """Attach the one final capability plan without rerunning inference."""

    fact_rows = tuple(facts)
    expected_requests = {request.request_id for request in compilation.requests}
    if expected_requests != set(contracts.request_definition_ids):
        raise ValueError("final compilation requests differ from contract materials")
    if capture_store.lineage_id != contracts.field_registry.lineage_id:
        raise ValueError("capture store and contract lineage differ")
    for capability in compilation.capabilities:
        for request_id in capability.request_ids:
            observation_id = contracts.observation_ids.get(request_id)
            if observation_id is None:
                raise ValueError(
                    f"capability references request without observation: {request_id}"
                )
            capture_store.bind_observation(observation_id)
    return replace(
        contracts,
        capture_store=capture_store.snapshot(),
        capability_uuids=_capability_uuid_map(
            compilation,
            facts=fact_rows,
            capture_store=capture_store,
            request_definition_ids=contracts.request_definition_ids,
        ),
    )


def compile_recording(
    *,
    tenant: str,
    recording_id: str,
    facts: Iterable[RecordingFact],
    proposals: Iterable[FieldProposal] = (),
    decisions: Iterable[FieldDecision] = (),
    source_revision: int = 0,
    evidence_graph: EvidenceGraph | None = None,
) -> RecordingCompilation:
    """Compile immutable facts into a complete, editable draft skeleton.

    This function never invokes a model and has no dependency on the legacy
    recorder/compiler.  Scope mismatches fail closed before any projection.
    """

    facts = tuple(facts)
    for fact in facts:
        if fact.tenant != tenant or fact.recording_id != recording_id:
            raise ValueError(
                f"fact {fact.fact_id} belongs to {fact.tenant}/{fact.recording_id}, "
                f"not {tenant}/{recording_id}"
            )
    transactions = segment_transactions(facts)
    requests = correlate_request_lifecycle(facts)
    analyses = classify_requests(requests)
    compiled_requests = materialize_requests(requests, analyses, transactions)
    field_facts = extract_field_facts(compiled_requests)
    fields = materialize_field_contracts(field_facts, proposals, decisions)
    # CapabilityPlanner is the only public-capability selector.  The legacy
    # capability_builder module is no longer part of the V3 production path.
    capability_plan = plan_capabilities(
        transactions,
        compiled_requests,
        fields,
        evidence_graph=evidence_graph,
    )
    transaction_by_id = {
        transaction.transaction_id: transaction for transaction in transactions
    }
    capabilities = []
    for capability in capability_plan.capabilities:
        transaction = transaction_by_id[capability.transaction_id]
        business_name = transaction.action_label.strip()
        if "submit_batch" in business_name.casefold() or "批量" in business_name:
            business_name = capability.name
        capabilities.append(
            capability.model_copy(
                update={
                    "name": business_name or capability.name,
                },
                deep=True,
            )
        )
    capabilities = tuple(capabilities)
    relations = build_relations(
        capabilities,
        compiled_requests,
        evidence_graph=evidence_graph,
    )
    validation = validate_compilation(requests, analyses, compiled_requests, capabilities)
    payload = {
        "protocol": "dano.recording-v3.compilation.v1",
        "tenant": tenant,
        "recording_id": recording_id,
        "source_revision": source_revision,
        "transactions": transactions,
        "request_analyses": analyses,
        "requests": compiled_requests,
        "field_facts": field_facts,
        "fields": fields,
        "capabilities": capabilities,
        "relations": relations,
        "validation": validation,
    }
    return RecordingCompilation(
        **payload,
        content_hash=content_hash(payload),
    )
