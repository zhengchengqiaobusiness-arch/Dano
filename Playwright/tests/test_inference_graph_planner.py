from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from dano_recording.capability_planner import (
    BusinessTerminalKind,
    CapabilityPlanningHint,
    normalize_capability_operation,
    plan_capabilities,
)
from dano_recording.capture_store import NetworkObservation
from dano_recording.domain.enums import ChoiceOption
from dano_recording.domain.facts import ActionTransaction
from dano_recording.domain.operations import CompiledRequest, RequestDisposition
from dano_recording.enum_resolver import (
    EnumEvidence,
    EnumInputMode,
    EnumResolutionError,
    EnumRuntimeContract,
    EnumRuntimeResolver,
    EnumSourceQuery,
    MappingCoverage,
    PaginationContract,
    SnapshotCoverage,
    SnapshotCoverageKind,
    SourceScope,
    enum_contract_fault,
)
from dano_recording.evidence_graph import (
    EvidenceEdgeKind,
    EvidenceGraphBuilder,
    EvidenceNode,
    EvidenceNodeKind,
    RequestObservationEvidence,
    TransactionEvidence,
    ValueSensitivity,
    associate_transactions,
    make_value_evidence,
)
from dano_recording.field_inference import (
    FieldInferenceEvidence,
    FieldSourceKind,
    FieldTypeOrigin,
    ProviderBinding,
    ProviderKind,
    TruthValue,
    infer_field,
)
from dano_recording.field_registry import FieldAlias, FieldAliasKind, FieldRegistry


def test_scoped_hmac_keeps_short_values_but_is_scope_and_type_isolated() -> None:
    kwargs = {
        "server_secret": b"unit-test-server-secret",
        "tenant_scope": "tenant-a",
        "recording_lineage": "lineage-a",
        "value_type": "status_code",
    }
    first = make_value_evidence(8, **kwargs)
    repeated = make_value_evidence(8, **kwargs)
    other_scope = make_value_evidence(8, **{**kwargs, "tenant_scope": "tenant-b"})
    string_value = make_value_evidence("8", **kwargs)

    assert first.scoped_hmac == repeated.scoped_hmac
    assert first.scoped_hmac != other_scope.scoped_hmac
    assert first.scoped_hmac != string_value.scoped_hmac
    assert first.redacted_sample == 8

    class Vault:
        def store_secret(self, **_kwargs) -> str:
            return "vault://recording/token"

    credential = make_value_evidence(
        "secret-token",
        **kwargs,
        sensitivity=ValueSensitivity.CREDENTIAL,
        runtime_resolver="credential_store.auth_token",
        credential_vault=Vault(),
    )
    assert credential.redacted_sample is None
    assert "secret-token" not in repr(credential)


def test_value_equality_supports_but_never_creates_transaction_causality() -> None:
    now = datetime.now(timezone.utc)
    transaction = TransactionEvidence(
        transaction_id="txn-submit",
        action_node_id="action-submit",
        action_id="submit",
        page_id="page-a",
        frame_id="main",
        started_at=now,
        finished_at=now + timedelta(seconds=2),
        submit_fingerprints=("short-value-8",),
    )
    equality_only = RequestObservationEvidence(
        request_id="request-unrelated",
        request_node_id="request-node-unrelated",
        page_id="page-a",
        frame_id="main",
        started_at=now + timedelta(seconds=1),
        input_fingerprints=("short-value-8",),
    )
    assert associate_transactions((transaction,), (equality_only,)) == ()
    initiator_only = equality_only.model_copy(
        update={"initiator": "https://app.test/submit.js", "input_fingerprints": ()}
    )
    transaction_with_initiator = transaction.model_copy(
        update={
            "page_id": None,
            "frame_id": None,
            "started_at": None,
            "finished_at": None,
            "initiators": ("https://app.test/submit.js",),
        }
    )
    assert associate_transactions((transaction_with_initiator,), (initiator_only,)) == ()

    attributed = equality_only.model_copy(
        update={
            "request_id": "request-submit",
            "request_node_id": "request-node-submit",
            "action_id": "submit",
        }
    )
    matches = associate_transactions((transaction,), (attributed,))
    assert len(matches) == 1
    assert matches[0].causal_anchors == ("action_id",)
    assert any(item.startswith("scoped_value:") for item in matches[0].supporting_evidence)

    builder = EvidenceGraphBuilder()
    builder.add_node(EvidenceNode(node_id="action-submit", kind=EvidenceNodeKind.ACTION))
    builder.add_node(
        EvidenceNode(
            node_id="request-node-submit",
            kind=EvidenceNodeKind.REQUEST_OBSERVATION,
            payload={"request_id": "request-submit"},
        )
    )
    builder.correlate_transactions((transaction,), (attributed,))
    edge = builder.build().edges[0]
    assert edge.kind is EvidenceEdgeKind.ACTION_TRIGGERED_REQUEST
    assert edge.causal is True


def test_capture_store_observation_projects_all_scoped_fingerprints() -> None:
    now = datetime.now(timezone.utc)
    short_value = make_value_evidence(
        0,
        server_secret=b"unit-test-server-secret",
        tenant_scope="tenant-a",
        recording_lineage="lineage-a",
        value_type="status",
    )
    observation = NetworkObservation(
        observation_id="observation-1",
        request_definition_id=UUID("00000000-0000-4000-8000-000000000201"),
        page_id="page-a",
        frame_id="main",
        action_id="search",
        started_at=now,
        finished_at=now + timedelta(milliseconds=20),
        initiator={"stack": {"callFrames": [{"url": "https://app.test/search.js"}]}},
        request_schema={},
        response_schema={},
        request_values=(short_value,),
        status=200,
    )
    builder = EvidenceGraphBuilder()
    projection = builder.add_network_observation(observation)
    graph = builder.build()
    assert projection.input_fingerprints == (short_value.scoped_hmac,)
    assert projection.initiator == "https://app.test/search.js"
    assert graph.node("request:observation-1") is not None


def test_evidence_graph_traces_response_to_submit_dependency() -> None:
    builder = EvidenceGraphBuilder()
    builder.add_node(
        EvidenceNode(
            node_id="remaining-hours",
            kind=EvidenceNodeKind.RESPONSE_FIELD,
            payload={"request_id": "prepare", "path": "data.remaining"},
        )
    )
    builder.add_node(
        EvidenceNode(
            node_id="submit-hours",
            kind=EvidenceNodeKind.SUBMIT_FIELD,
            payload={"request_id": "submit", "path": "hours"},
        )
    )
    builder.link(
        EvidenceEdgeKind.RESPONSE_BOUND_TO_WIRE,
        "remaining-hours",
        "submit-hours",
        evidence_ids=("observation-prepare", "observation-submit"),
    )
    graph = builder.build()
    assert graph.request_dependencies("submit") == ("prepare",)
    assert graph.has_request_dependency("prepare", "submit")


def test_scoped_value_equality_alone_is_not_a_request_dependency() -> None:
    builder = EvidenceGraphBuilder()
    builder.add_node(
        EvidenceNode(
            node_id="response-short-value",
            kind=EvidenceNodeKind.RESPONSE_FIELD,
            payload={"request_id": "prepare"},
        )
    )
    builder.add_node(
        EvidenceNode(
            node_id="submit-short-value",
            kind=EvidenceNodeKind.SUBMIT_FIELD,
            payload={"request_id": "submit"},
        )
    )
    edge = builder.link(
        EvidenceEdgeKind.VALUES_EQUAL_IN_SCOPE,
        "response-short-value",
        "submit-short-value",
        evidence_ids=("hmac-short-8",),
    )
    graph = builder.build()
    assert edge.causal is False
    assert graph.request_dependencies("submit") == ()


def test_field_inference_has_one_order_and_separate_value_semantics() -> None:
    evidence = FieldInferenceEvidence(
        field_uuid=UUID("00000000-0000-4000-8000-000000000101"),
        request_id="submit",
        wire_path="data.remainingGs",
        wire_name="remainingGs",
        location="body",
        native_control_type="number",
        aria_role="textbox",
        user_action_type="fill",
        user_changed=True,
        user_action_value=8,
        wire_schema_type="string",
        js_config_type="integer",
        pi_type="string",
        exact_response_provider=ProviderBinding(
            kind=ProviderKind.DEPENDENCY_RESPONSE,
            request_definition_id="query-remaining",
            response_path="data.remainingGs",
        ),
        sample_value=8,
        sample_observed=True,
        page_initial_value=0,
        page_initial_observed=True,
        wire_required=TruthValue.TRUE,
        evidence_ids={
            "exact_response": ("edge-response-submit",),
            "user_action": ("action-fill-hours",),
        },
    )
    field = infer_field(evidence)

    assert field.business_type == "number"
    assert field.type_origin is FieldTypeOrigin.NATIVE_CONTROL
    assert field.source_origin is FieldSourceKind.PREVIOUS_RESPONSE
    assert field.sample_value == 8
    assert field.default_value == 0
    assert field.runtime_value is not None
    assert field.runtime_value.kind is ProviderKind.DEPENDENCY_RESPONSE
    assert field.required.wire_required is TruthValue.TRUE
    assert field.required.caller_required is TruthValue.FALSE


def test_snapshot_is_not_user_input_and_internal_fields_are_hidden_by_default() -> None:
    snapshot = FieldInferenceEvidence(
        field_uuid=UUID("00000000-0000-4000-8000-000000000102"),
        request_id="submit",
        wire_path="sfbt",
        wire_name="sfbt",
        location="body",
        wire_schema_type="boolean",
        sample_value=False,
        sample_observed=True,
        page_initial_value=False,
        page_initial_observed=True,
        wire_required=TruthValue.TRUE,
    )
    field = infer_field(snapshot)
    assert field.source_origin is FieldSourceKind.PAGE_DEFAULT
    assert field.exposed is False
    assert field.required.caller_required is TruthValue.FALSE
    assert field.runtime_value is None

    caller_field = infer_field(
        snapshot.model_copy(
            update={
                "field_uuid": UUID("00000000-0000-4000-8000-000000000103"),
                "user_changed": True,
                "user_action_type": "check",
                "page_initial_observed": False,
            }
        )
    )
    assert caller_field.source_origin is FieldSourceKind.USER_INPUT
    assert caller_field.exposed is True
    assert caller_field.required.caller_required is TruthValue.TRUE

    contract_owned = infer_field(
        snapshot.model_copy(
            update={
                "field_uuid": UUID("00000000-0000-4000-8000-000000000104"),
                "page_initial_observed": False,
                "caller_must_supply": True,
                "evidence_ids": {"caller_contract": ("wire-contract",)},
            }
        )
    )
    assert contract_owned.source_origin is FieldSourceKind.USER_INPUT
    assert contract_owned.exposed is True


def test_field_uuid_is_stable_across_names_paths_and_order_but_not_surfaces() -> None:
    lineage = uuid4()
    registry = FieldRegistry(lineage)
    original = registry.register_field(
        aliases=(
            FieldAlias(
                kind=FieldAliasKind.CONTROL,
                value="approver-control",
                context="query-form",
            ),
        )
    )
    renamed = registry.register_field(
        field_uuid=original.field_uuid,
        aliases=(
            FieldAlias(
                kind=FieldAliasKind.BUSINESS_NAME,
                value="审批负责人",
                context="query-form",
            ),
            FieldAlias(
                kind=FieldAliasKind.WIRE_PATH,
                value="filters.approverCode",
                context="query-request-v2",
            ),
        ),
    )
    submit = registry.register_field(
        aliases=(
            FieldAlias(
                kind=FieldAliasKind.CONTROL,
                value="approver-control",
                context="submit-form",
            ),
        )
    )
    assert original.field_uuid == renamed.field_uuid
    assert original.field_uuid != submit.field_uuid


def _static_enum(*, coverage: MappingCoverage) -> EnumRuntimeContract:
    return EnumRuntimeContract(
        evidence=EnumEvidence(
            selected_pair_verified=True,
            observed_mapping_complete=coverage is MappingCoverage.STATIC_DOMAIN,
            snapshot_coverage=SnapshotCoverage(
                kind=SnapshotCoverageKind.NATIVE_LOADED,
                observed_count=2,
                truncated=False,
            ),
            mapping_coverage=coverage,
            source_scope=SourceScope(tenant="tenant-a", current_user="user-a"),
            evidence_ids=("native-select-status",),
        ),
        options=(
            ChoiceOption(label="待审批", value=0),
            ChoiceOption(label="已通过", value=1),
        ),
    )


@pytest.mark.asyncio
async def test_enum_static_domain_and_caller_wire_contract() -> None:
    complete = _static_enum(coverage=MappingCoverage.STATIC_DOMAIN)
    assert enum_contract_fault(complete) is None
    resolved = await EnumRuntimeResolver().resolve(complete, "已通过")
    assert resolved.wire_value == 1

    selected_only = _static_enum(coverage=MappingCoverage.SELECTED_ONLY)
    assert enum_contract_fault(selected_only) is not None
    direct = await EnumRuntimeResolver().resolve(
        EnumEvidence(),
        8,
        input_mode=EnumInputMode.WIRE_VALUE,
    )
    assert direct.wire_value == 8
    assert direct.matched_by == "caller_wire_value"
    with pytest.raises(EnumResolutionError) as exc_info:
        await EnumRuntimeResolver().resolve(
            complete,
            8,
            input_mode=EnumInputMode.WIRE_VALUE,
        )
    assert exc_info.value.code == "enum_wire_value_not_allowed"


@pytest.mark.asyncio
async def test_enum_runtime_search_and_pagination_resolve_unique_label() -> None:
    requests: list[dict] = []

    async def fetcher(request: dict):
        requests.append(request)
        page = request["query"]["page"]
        assert request["query"]["keyword"] == "Bob"
        return {
            "data": {
                "items": (
                    [{"display": "Alice", "uid": 0}]
                    if page == 1
                    else [{"display": "Bob", "uid": 8}]
                ),
                "hasMore": page == 1,
            }
        }

    evidence = EnumEvidence(
        snapshot_coverage=SnapshotCoverage(
            kind=SnapshotCoverageKind.API_PAGE,
            observed_count=1,
            truncated=True,
        ),
        mapping_coverage=MappingCoverage.RUNTIME_RESOLVABLE,
        source_scope=SourceScope(
            tenant="tenant-a",
            current_user="user-a",
            permission_scope=("team-a",),
            query_filters={"active": True},
        ),
        source_query=EnumSourceQuery(
            request_definition_id="request-user-options",
            method="GET",
            request_template={"url": "/api/users", "query": {"active": True}},
            label_path="display",
            value_path="uid",
            exact_lookup=True,
            search_param="query.keyword",
            pagination=PaginationContract(
                page_param="query.page",
                page_size_param="query.pageSize",
                page_size=1,
                records_path="data.items",
            ),
        ),
        evidence_ids=("option-request",),
    )
    result = await EnumRuntimeResolver(fetcher).resolve(evidence, "Bob")
    assert result.wire_value == 8
    assert result.pages_fetched == 2
    assert result.matched_by == "runtime_label"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_enum_runtime_rejects_ambiguous_labels() -> None:
    async def fetcher(request: dict):
        page = request["query"]["page"]
        return {
            "items": [] if page > 2 else [{"label": "Same", "value": page}],
            "hasMore": page == 1,
        }

    evidence = EnumEvidence(
        mapping_coverage=MappingCoverage.RUNTIME_RESOLVABLE,
        source_query=EnumSourceQuery(
            request_definition_id="ambiguous",
            method="GET",
            request_template={"query": {}},
            label_path="label",
            value_path="value",
            exact_lookup=False,
            pagination=PaginationContract(
                page_param="query.page",
                page_size=1,
                records_path="items",
            ),
        ),
    )
    with pytest.raises(EnumResolutionError) as exc_info:
        await EnumRuntimeResolver(fetcher).resolve(evidence, "Same")
    assert exc_info.value.code == "ambiguous_enum_label"


def _request(
    request_id: str,
    sequence: int,
    *,
    method: str,
    path: str,
    disposition: RequestDisposition,
    body=None,
    response_schema=None,
) -> CompiledRequest:
    return CompiledRequest(
        tenant="tenant-a",
        recording_id="recording-a",
        request_id=request_id,
        transaction_id="txn-submit",
        sequence=sequence,
        method=method,
        url=f"https://example.test{path}",
        path=path,
        body=body,
        body_present=body is not None,
        response_schema=response_schema,
        disposition=disposition,
        disposition_reason="fixture",
        capability_eligible=disposition
        in {RequestDisposition.MATERIALIZED, RequestDisposition.REVIEW_CANDIDATE},
    )


def test_capability_planner_keeps_only_proven_dependency_and_normalises_batch() -> None:
    transaction = ActionTransaction(
        transaction_id="txn-submit",
        tenant="tenant-a",
        recording_id="recording-a",
        action_id="submit",
        action_label="批量提交",
        request_ids=("identity", "telemetry", "prepare", "unrelated", "submit"),
        first_sequence=1,
        last_sequence=5,
    )
    requests = (
        _request(
            "identity",
            1,
            method="GET",
            path="/api/current-user",
            disposition=RequestDisposition.IDENTITY,
        ),
        _request(
            "telemetry",
            2,
            method="GET",
            path="/api/telemetry",
            disposition=RequestDisposition.MATERIALIZED,
        ),
        _request(
            "prepare",
            3,
            method="GET",
            path="/api/remaining",
            disposition=RequestDisposition.SUPPORTING,
        ),
        _request(
            "unrelated",
            4,
            method="GET",
            path="/api/project/detail",
            disposition=RequestDisposition.REVIEW_CANDIDATE,
        ),
        _request(
            "submit",
            5,
            method="POST",
            path="/api/timesheets/batch-submit",
            disposition=RequestDisposition.MATERIALIZED,
            body=[{"hours": 8}],
        ),
    )
    builder = EvidenceGraphBuilder()
    builder.add_node(
        EvidenceNode(
            node_id="prepare-value",
            kind=EvidenceNodeKind.RESPONSE_FIELD,
            payload={"request_id": "prepare"},
        )
    )
    builder.add_node(
        EvidenceNode(
            node_id="submit-value",
            kind=EvidenceNodeKind.SUBMIT_FIELD,
            payload={"request_id": "submit"},
        )
    )
    builder.link(
        EvidenceEdgeKind.RESPONSE_BOUND_TO_WIRE,
        "prepare-value",
        "submit-value",
    )

    plan = plan_capabilities(
        (transaction,),
        requests,
        evidence_graph=builder.build(),
    )
    assert len(plan.capabilities) == 1
    capability = plan.capabilities[0]
    assert capability.name == "submit"
    assert capability.title == "提交"
    assert capability.request_ids == ("prepare", "submit")
    assert "identity" in plan.ignored_request_ids
    assert "telemetry" in plan.ignored_request_ids
    assert "unrelated" in plan.unbound_business_requests
    assert "batch" not in repr(plan).casefold()
    assert "批量" not in repr(plan)
    assert normalize_capability_operation("submit_batch") == "submit"


def test_capability_planner_splits_only_independently_usable_results() -> None:
    transaction = ActionTransaction(
        transaction_id="txn-submit",
        tenant="tenant-a",
        recording_id="recording-a",
        action_id="command",
        action_label="保存并审批",
        request_ids=("save", "approve"),
        first_sequence=1,
        last_sequence=2,
    )
    requests = (
        _request(
            "save",
            1,
            method="POST",
            path="/api/save",
            disposition=RequestDisposition.MATERIALIZED,
        ),
        _request(
            "approve",
            2,
            method="POST",
            path="/api/approve",
            disposition=RequestDisposition.MATERIALIZED,
        ),
    )
    unsplit = plan_capabilities((transaction,), requests)
    assert [item.request_ids for item in unsplit.capabilities] == [("approve",)]

    split = plan_capabilities(
        (transaction,),
        requests,
        hints=(
            CapabilityPlanningHint(
                request_id="save",
                terminal_kind=BusinessTerminalKind.RECORD_CHANGE,
                business_result=True,
                independently_triggerable=True,
                caller_usable=True,
                operation="submit_batch",
            ),
            CapabilityPlanningHint(
                request_id="approve",
                terminal_kind=BusinessTerminalKind.STATE_CHANGE,
                business_result=True,
                independently_triggerable=True,
                caller_usable=True,
                operation="approve",
            ),
        ),
    )
    assert [item.name for item in split.capabilities] == ["submit", "approve"]
    assert [item.request_ids for item in split.capabilities] == [("save",), ("approve",)]
    assert all("batch" not in item.name for item in split.capabilities)
