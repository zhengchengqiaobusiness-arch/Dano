from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from dano_recording.api.decision_commands import apply_edits, apply_replacement
from dano_recording.api.protocol import CreateRecordingRequest
from dano_recording.bootstrap import RecordingApplication, _pi_human_context
from dano_recording.capture_store import CaptureStore
from dano_recording.compiler.client_projection import compilation_to_workbench
from dano_recording.compiler.pipeline import (
    compile_recording,
    integrate_compilation_contracts,
)
from dano_recording.compiler.models import CompilationIssue, IssueSeverity
from dano_recording.domain.facts import ActionFact, FactKind, RecordingFact, RequestFact
from dano_recording.domain.fields import FieldDimension, SourceBindingKind
from dano_recording.domain.fields import DecisionOrigin, FieldProposal
from dano_recording.domain.enums import (
    ChoiceContract,
    ChoiceOption,
    EnumEvidence,
    EnumSourceQuery,
    MappingCoverage,
)
from dano_recording.enum_resolver import EnumRuntimeResolver
from dano_recording.runtime.option_resolver import enum_runtime_contract
from dano_recording.field_registry import FieldRegistry
from dano_recording.evidence_graph import EvidenceEdgeKind, EvidenceNodeKind
from dano_recording.evidence.dom_controls import DOMControl
from dano_recording.value_evidence import ValueEvidenceFactory
from dano_recording.publish.asset_projection import project_asset


TENANT = "tenant-a"
RECORDING = "recording-a"


def _action(sequence: int, action_id: str, label: str = "提交") -> ActionFact:
    return ActionFact(
        tenant=TENANT,
        recording_id=RECORDING,
        sequence=sequence,
        action_id=action_id,
        action_type="click",
        label=label,
        payload={
            "evidence_origin": "server_dispatched",
            "causal_eligible": True,
        },
    )


def _contracts(facts, *, lineage=None, store=None, registry=None, generation=0):
    lineage = lineage or uuid4()
    store = store or CaptureStore(
        tenant_scope=TENANT,
        recording_id=RECORDING,
        lineage_id=lineage,
        capture_generation=generation,
    )
    registry = registry or FieldRegistry(lineage)
    compilation = compile_recording(
        tenant=TENANT,
        recording_id=RECORDING,
        facts=facts,
    )
    contracts = integrate_compilation_contracts(
        compilation,
        facts=facts,
        capture_store=store,
        field_registry=registry,
        value_evidence_factory=ValueEvidenceFactory(server_secret=b"integration-secret"),
    )
    return compilation, contracts, store, registry


def test_capture_store_deduplicates_definitions_retains_observations_and_hides_secrets() -> None:
    facts = (
        _action(0, "first"),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=1,
            action_id="first",
            request_id="request-1",
            method="POST",
            url="https://oa.example/api/items/41",
            request_headers={"authorization": "Bearer super-secret"},
            request_body={"creatorId": 7, "title": "first"},
            response_status=200,
            response_body={"ok": True},
        ),
        _action(2, "second"),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=3,
            action_id="second",
            request_id="request-2",
            method="POST",
            url="https://oa.example/api/items/42",
            request_headers={"authorization": "Bearer super-secret"},
            request_body={"creatorId": 8, "title": "second"},
            response_status=200,
            response_body={"ok": True},
        ),
    )
    compilation, contracts, store, registry = _contracts(facts)
    snapshot = contracts.capture_store
    assert len(snapshot.request_definitions) == 1
    assert {item.observation_id for item in snapshot.observations} == {
        "request-1",
        "request-2",
    }
    assert "super-secret" not in json.dumps(
        snapshot.model_dump(mode="json"), ensure_ascii=False
    )

    creator_ids = [
        fact.field_contract_id
        for fact in compilation.field_facts
        if fact.wire_name == "creatorId"
    ]
    assert len({contracts.field_uuids[item] for item in creator_ids}) == 1
    canonical = registry.get_field(contracts.field_uuids[creator_ids[0]])
    source = canonical.decisions[FieldDimension.SOURCE_BINDING].value
    assert source.kind is SourceBindingKind.RUNTIME_CONTEXT
    assert source.runtime_resolver == "runtime_context.current_user.id"
    assert canonical.decisions[FieldDimension.EXPOSURE].value is False

    # Re-analysis is idempotent: identities and bindings do not multiply.
    again = integrate_compilation_contracts(
        compilation,
        facts=facts,
        capture_store=store,
        field_registry=registry,
        value_evidence_factory=ValueEvidenceFactory(server_secret=b"integration-secret"),
    )
    assert again.field_uuids == contracts.field_uuids
    assert again.step_uuids == contracts.step_uuids
    assert again.capability_uuids == contracts.capability_uuids
    assert again.field_registry == contracts.field_registry


def test_same_action_requests_are_not_chained_without_causal_value_binding() -> None:
    facts = (
        _action(0, "submit"),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=1,
            action_id="submit",
            request_id="prepare",
            method="GET",
            url="https://oa.example/api/prepare",
            response_status=200,
            response_body={"remaining": 8},
        ),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=2,
            action_id="submit",
            request_id="submit",
            method="POST",
            url="https://oa.example/api/submit",
            request_body={"hours": 7},
        ),
    )
    preliminary, contracts, store, _registry = _contracts(facts)
    compilation = compile_recording(
        tenant=TENANT,
        recording_id=RECORDING,
        facts=facts,
        evidence_graph=contracts.evidence_graph,
    )

    capability = next(item for item in compilation.capabilities if "submit" in item.request_ids)
    assert capability.request_ids == ("submit",)
    assert any(
        issue.code == "unbound_business_request" and issue.request_id == "prepare"
        for issue in compilation.validation.issues
    )
    assert {item.observation_id for item in store.snapshot().observations} == {
        "prepare",
        "submit",
    }
    assert preliminary.requests


def test_review_issue_identity_uses_canonical_target_not_list_position() -> None:
    facts = (
        _action(0, "submit"),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=1,
            action_id="submit",
            request_id="submit",
            method="POST",
            url="https://oa.example/api/submit",
            request_body={"hours": 8},
        ),
    )
    compilation, contracts, _store, _registry = _contracts(facts)
    issues = (
        CompilationIssue(
            code="first_review",
            message="first",
            severity=IssueSeverity.WARNING,
            request_id="submit",
        ),
        CompilationIssue(
            code="second_review",
            message="second",
            severity=IssueSeverity.INFO,
            request_id="submit",
        ),
    )
    first = compilation_to_workbench(
        compilation.model_copy(
            update={"validation": compilation.validation.model_copy(update={"issues": issues})}
        ),
        contracts=contracts,
    )["review_items"]
    reordered = compilation_to_workbench(
        compilation.model_copy(
            update={
                "validation": compilation.validation.model_copy(
                    update={"issues": tuple(reversed(issues))}
                )
            }
        ),
        contracts=contracts,
    )["review_items"]

    assert {item["type"]: item["issue_id"] for item in first} == {
        item["type"]: item["issue_id"] for item in reordered
    }
    for item in first:
        UUID(item["issue_id"])
        assert item["target_uuid"] == contracts.step_uuids["submit"]
        assert item["target"]["step_uuid"] == contracts.step_uuids["submit"]
        assert item["fingerprint"].startswith("sha256:")
        assert item["revision"] == compilation.source_revision


def test_pi_free_text_projection_redacts_pii_credentials_and_identity_assignments() -> None:
    projected = _pi_human_context({
        "goal": {
            "summary": "联系 alice@example.test，user_id=user-7",
            "token": "top-secret-token",
        },
        "action": "删除 alice@example.test 的申请，Authorization: Bearer action-secret",
        "title": "审批 alice@example.test 电话 13800138000",
        "business_description": "Authorization: Bearer secret-value; user_id=user-7",
    })
    serialized = json.dumps(projected, ensure_ascii=False)
    for plaintext in (
        "alice@example.test",
        "13800138000",
        "top-secret-token",
        "secret-value",
        "action-secret",
        "user-7",
    ):
        assert plaintext not in serialized
    assert "审批" in projected["title"]
    assert "删除" in projected["action"]


def _identity_enum_facts() -> tuple[RecordingFact, ...]:
    return (
        _action(0, "submit"),
        RecordingFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=1,
            kind=FactKind.DOM_CONTROL,
            page_id="page-1",
            payload={
                "control_id": "control-approver",
                "frame_id": "frame-main",
                "form_id": "approval-form",
                "selector": "#approver",
                "tag": "select",
                "name": "approver",
                "options": [],
                "options_sensitive": True,
                "option_count": 2,
                "options_truncated": True,
            },
        ),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=2,
            action_id="submit",
            request_id="submit",
            method="POST",
            url="https://oa.example/api/approvals",
            page_id="page-1",
            request_body={"approver": "user-7"},
        ),
    )


def test_identity_enum_without_grounded_query_is_unknown_and_non_static() -> None:
    compilation, contracts, _store, registry = _contracts(_identity_enum_facts())
    field = next(item for item in compilation.field_facts if item.wire_name == "approver")
    canonical = registry.get_field(contracts.field_uuids[field.field_contract_id])
    binding = canonical.decisions[FieldDimension.ENUM_BINDING].value
    assert binding["mapping_coverage"] == "unknown"
    assert binding["static_values_retained"] is False
    assert binding["snapshot_coverage"]["truncated"] is True
    assert binding["contract_fault"]["code"] == "identity_enum_source_query_missing"


@pytest.mark.asyncio
async def test_identity_enum_keeps_only_grounded_query_and_resolves_live() -> None:
    facts = _identity_enum_facts()
    base = compile_recording(tenant=TENANT, recording_id=RECORDING, facts=facts)
    field = next(item for item in base.field_facts if item.wire_name == "approver")
    source_query = EnumSourceQuery(
        request_definition_id="66666666-6666-4666-8666-666666666666",
        method="GET",
        request_template={
            "url": "https://oa.example/api/users",
            "query": {"q": ""},
        },
        label_path="name",
        value_path="id",
        exact_lookup=False,
        search_param="query.q",
    )
    evidence = EnumEvidence(
        mapping_coverage=MappingCoverage.RUNTIME_RESOLVABLE,
        source_query=source_query,
        evidence_ids=("option-endpoint-proof",),
    )
    proposal = FieldProposal(
        field_contract_id=field.field_contract_id,
        origin=DecisionOrigin.DETERMINISTIC,
        values={
            FieldDimension.CHOICE_CONTRACT: ChoiceContract(
                options=(
                    ChoiceOption(label="Alice Zhang", value="user-7"),
                    ChoiceOption(label="alice@example.test", value="user-8"),
                ),
                evidence_ids=("option-endpoint-proof",),
                enum_evidence=evidence,
            )
        },
        confidence=1.0,
    )
    lineage = uuid4()
    store = CaptureStore(
        tenant_scope=TENANT,
        recording_id=RECORDING,
        lineage_id=lineage,
    )
    registry = FieldRegistry(lineage)
    compilation = compile_recording(
        tenant=TENANT,
        recording_id=RECORDING,
        facts=facts,
        proposals=(proposal,),
    )
    contracts = integrate_compilation_contracts(
        compilation,
        facts=facts,
        capture_store=store,
        field_registry=registry,
        value_evidence_factory=ValueEvidenceFactory(
            server_secret=b"integration-secret"
        ),
    )
    spec = compilation_to_workbench(compilation, contracts=contracts)
    serialized = json.dumps(spec, ensure_ascii=False)
    assert "Alice Zhang" not in serialized
    assert "alice@example.test" not in serialized
    param = next(
        item for item in spec["steps"][0]["params"] if item["path"] == "approver"
    )
    assert param["enum_options"] is None
    assert param["enum_binding"]["mapping_coverage"] == "runtime_resolvable"
    assert param["enum_binding"]["source_query"]["search_param"] == "query.q"

    runtime_contract = enum_runtime_contract(param["enum_binding"])
    assert runtime_contract is not None
    requests: list[dict] = []

    async def fetcher(request: dict) -> list[dict]:
        requests.append(request)
        return [{"name": "Alice Zhang", "id": "user-7"}]

    resolved = await EnumRuntimeResolver(fetcher).resolve(
        runtime_contract,
        "Alice Zhang",
    )
    assert resolved.wire_value == "user-7"
    assert requests[0]["query"]["q"] == "Alice Zhang"


def test_unique_exact_response_binding_builds_real_graph_and_back_slices_short_value() -> None:
    lineage = uuid4()
    factory = ValueEvidenceFactory(server_secret=b"integration-secret")
    mutation_value = factory.capture(
        tenant_scope=TENANT,
        recording_lineage=str(lineage),
        value=8,
        field_name="hours",
        value_path="control.value",
    )
    facts = (
        _action(0, "submit"),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=1,
            action_id="submit",
            request_id="prepare",
            method="GET",
            url="https://oa.example/api/prepare",
            page_id="page-1",
            response_status=200,
            response_body={"remaining": 8},
        ),
        RecordingFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=2,
            kind=FactKind.DOM_CONTROL,
            page_id="page-1",
            payload={
                "control_id": "control-hours",
                "frame_id": "frame-main",
                "form_id": "timesheet-form",
                "selector": "#hours",
                "tag": "input",
                "input_type": "number",
                "name": "hours",
            },
        ),
        RecordingFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=3,
            kind=FactKind.DOM_MUTATION,
            page_id="page-1",
            action_id="submit",
            payload={
                "phase": "after_response",
                "request_id": "prepare",
                "frame_id": "frame-main",
                "mutations": [
                    {
                        "evidence_origin": "server_snapshot",
                        "causal_eligible": True,
                        "selector": "#hours",
                        "name": "hours",
                        "mutation_type": "property_snapshot",
                        "value_evidence": [
                            mutation_value.model_dump(mode="json", exclude_none=True)
                        ],
                    }
                ],
            },
        ),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=4,
            action_id="submit",
            request_id="submit",
            method="POST",
            url="https://oa.example/api/submit",
            page_id="page-1",
            request_body={"hours": 8},
        ),
    )
    weak_payload = dict(facts[3].payload)
    weak_payload["mutations"] = [
        {
            **dict(weak_payload["mutations"][0]),
            "evidence_origin": "page_observed",
            "causal_eligible": False,
        }
    ]
    weak_facts = list(facts)
    weak_facts[3] = facts[3].model_copy(
        update={"payload": weak_payload},
        deep=True,
    )
    _, weak_contracts, _, _ = _contracts(tuple(weak_facts), lineage=lineage)
    assert not any(
        edge.kind is EvidenceEdgeKind.RESPONSE_POPULATED_CONTROL
        for edge in weak_contracts.evidence_graph.edges
    )

    preliminary, contracts, _store, _registry = _contracts(
        facts,
        lineage=lineage,
    )
    graph = contracts.evidence_graph

    assert any(node.kind is EvidenceNodeKind.RESPONSE_FIELD for node in graph.nodes)
    assert any(node.kind is EvidenceNodeKind.SUBMIT_FIELD for node in graph.nodes)
    assert any(node.kind is EvidenceNodeKind.DOM_MUTATION for node in graph.nodes)
    assert any(
        edge.kind is EvidenceEdgeKind.VALUES_EQUAL_IN_SCOPE and edge.causal is False
        for edge in graph.edges
    )
    assert any(
        edge.kind is EvidenceEdgeKind.RESPONSE_POPULATED_CONTROL and edge.causal is True
        for edge in graph.edges
    )
    assert any(
        edge.kind is EvidenceEdgeKind.CONTROL_BOUND_TO_WIRE and edge.causal is True
        for edge in graph.edges
    )
    assert graph.request_dependencies("submit") == ("prepare",)

    compilation = compile_recording(
        tenant=TENANT,
        recording_id=RECORDING,
        facts=facts,
        evidence_graph=graph,
    )
    capability = next(item for item in compilation.capabilities if "submit" in item.request_ids)
    assert capability.request_ids == ("prepare", "submit")


def test_auth_identity_and_transport_headers_project_to_distinct_runtime_contracts() -> None:
    facts = (
        _action(0, "submit"),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=1,
            action_id="submit",
            request_id="submit",
            method="POST",
            url="https://oa.example/api/submit",
            request_headers={
                "Authorization": "[REDACTED]",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-User-Id": "{{runtime_context.current_user.id}}",
                "X-Business-Region": "north",
            },
            request_body={"title": "demo"},
        ),
    )
    compilation, contracts, _store, _registry = _contracts(facts)
    spec = compilation_to_workbench(compilation, contracts=contracts)
    step = spec["steps"][0]
    params = {item["path"].casefold(): item for item in step["params"]}

    assert "content-type" not in params
    assert "accept" not in params
    assert step["headers"]["Content-Type"] == "application/json"
    assert step["headers"]["Accept"] == "application/json"
    authorization = params["authorization"]
    assert authorization["classification"] == "credential"
    assert authorization["exposed_to_caller"] is False
    assert authorization["source_binding"] == {
        "kind": "runtime_context",
        "runtime_resolver": "credential_headers.Authorization",
    }
    assert step["headers"]["Authorization"] == "{{credential_headers.Authorization}}"
    user_header = params["x-user-id"]
    assert user_header["source_binding"]["runtime_resolver"] == (
        "runtime_context.current_user.id"
    )
    assert params["x-business-region"]["source_binding"]["kind"] == "unknown"

    asset = project_asset(spec, revision=spec["revision"]).body
    published_step = asset["api_request"]["steps"][0]
    assert published_step["required_credential_headers"] == ["Authorization"]
    assert "Authorization" not in published_step["headers"]
    assert published_step["headers"]["Content-Type"] == "application/json"


def test_single_inference_consumes_dom_type_user_change_and_safe_page_default() -> None:
    lineage = uuid4()
    factory = ValueEvidenceFactory(server_secret=b"field-inference-evidence-secret")
    _, date_evidence = factory.capture_tree(
        tenant_scope=TENANT,
        recording_lineage=str(lineage),
        value="2026-07-15",
        root_path="action.value",
        field_name="dueDate",
    )
    facts = (
        ActionFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=0,
            action_id="fill-date",
            action_type="change",
            label="日期",
            page_id="page-1",
            payload={
                "evidence_origin": "server_dispatched",
                "causal_eligible": True,
                "frame_id": "frame-main",
                "details": {
                    "name": "dueDate",
                    "selector": "#due-date",
                    "value": "2026-07-15",
                    "value_evidence": [
                        item.model_dump(mode="json", exclude_none=True)
                        for item in date_evidence
                    ],
                },
            },
        ),
        _action(1, "submit"),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=2,
            action_id="submit",
            request_id="submit",
            method="POST",
            url="https://oa.example/api/submit",
            request_body={
                "dueDate": "2026-07-15",
                "hours": 8,
                "status": "open",
            },
        ),
        RecordingFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=3,
            kind=FactKind.DOM_CONTROL,
            page_id="page-1",
            payload={
                "control_id": "control-date",
                "frame_id": "frame-main",
                "form_id": "timesheet-form",
                "selector": "#due-date",
                "tag": "input",
                "input_type": "date",
                "name": "dueDate",
                "readonly": False,
                "initial_value": None,
                "initial_value_observed": True,
            },
        ),
        RecordingFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=4,
            kind=FactKind.DOM_CONTROL,
            page_id="page-1",
            payload={
                "control_id": "control-hours",
                "frame_id": "frame-main",
                "form_id": "timesheet-form",
                "selector": "#hours",
                "tag": "input",
                "input_type": "number",
                "name": "hours",
                "readonly": False,
                "initial_value": 8,
                "initial_value_observed": True,
            },
        ),
        RecordingFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=5,
            kind=FactKind.DOM_CONTROL,
            page_id="page-1",
            payload={
                "control_id": "control-status",
                "frame_id": "frame-main",
                "form_id": "timesheet-form",
                "selector": "#status",
                "tag": "select",
                "input_type": "select",
                "role": "combobox",
                "name": "status",
                "readonly": False,
                "initial_value": "open",
                "initial_value_observed": True,
                "options": [
                    {"label": "Open", "value": "open", "disabled": False},
                    {"label": "Closed", "value": "closed", "disabled": False},
                ],
            },
        ),
    )
    store = CaptureStore(
        tenant_scope=TENANT,
        recording_id=RECORDING,
        lineage_id=lineage,
    )
    registry = FieldRegistry(lineage)
    compilation = compile_recording(
        tenant=TENANT,
        recording_id=RECORDING,
        facts=facts,
    )
    contracts = integrate_compilation_contracts(
        compilation,
        facts=facts,
        capture_store=store,
        field_registry=registry,
        value_evidence_factory=factory,
    )

    decisions = {}
    for field in compilation.field_facts:
        canonical = registry.get_field(contracts.field_uuids[field.field_contract_id])
        decisions[field.wire_name] = canonical.decisions
    assert decisions["dueDate"][FieldDimension.BUSINESS_TYPE].value == "date"
    assert decisions["dueDate"][FieldDimension.SOURCE_BINDING].value.kind is SourceBindingKind.CALLER
    assert decisions["hours"][FieldDimension.BUSINESS_TYPE].value == "number"
    assert decisions["hours"][FieldDimension.SOURCE_BINDING].value.kind is SourceBindingKind.DEFAULT
    assert decisions["hours"][FieldDimension.DEFAULT_VALUE].value == 8
    assert decisions["status"][FieldDimension.BUSINESS_TYPE].value == "enum"


def test_dom_control_preserves_field_uuid_across_path_change_without_merging_query_submit() -> None:
    lineage = uuid4()
    control = RecordingFact(
        tenant=TENANT,
        recording_id=RECORDING,
        sequence=1,
        kind=FactKind.DOM_CONTROL,
        page_id="page-1",
        payload={
            "control_id": "control-stable",
            "frame_id": "frame-main",
            "selector": "#approval",
            "name": "approval",
            "tag": "input",
        },
    )
    first_facts = (
        _action(0, "save"),
        control,
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=2,
            action_id="save",
            request_id="old-request",
            method="POST",
            url="https://oa.example/api/old",
            request_body={"approval": "yes"},
        ),
    )
    first, first_contracts, store, registry = _contracts(first_facts, lineage=lineage)
    old_field = next(item for item in first.field_facts if item.wire_name == "approval")
    old_uuid = first_contracts.field_uuids[old_field.field_contract_id]

    second_facts = (
        _action(10, "save"),
        control.model_copy(update={"sequence": 11}),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=12,
            action_id="save",
            request_id="new-request",
            method="POST",
            url="https://oa.example/api/new-contract",
            request_body={"wrapper": {"approval": "yes"}},
        ),
    )
    second_store = store.next_generation()
    second, second_contracts, _, _ = _contracts(
        second_facts,
        lineage=lineage,
        store=second_store,
        registry=registry,
    )
    new_field = next(item for item in second.field_facts if item.wire_name == "approval")
    assert second_contracts.field_uuids[new_field.field_contract_id] == old_uuid

    ambiguous_facts = (
        _action(20, "mixed"),
        control.model_copy(update={"sequence": 21}),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=22,
            action_id="mixed",
            request_id="query",
            method="GET",
            url="https://oa.example/api/query?approval=yes",
        ),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=23,
            action_id="mixed",
            request_id="submit",
            method="POST",
            url="https://oa.example/api/submit",
            request_body={"approval": "yes"},
        ),
    )
    ambiguous, ambiguous_contracts, _, _ = _contracts(ambiguous_facts)
    fields = [item for item in ambiguous.field_facts if item.wire_name == "approval"]
    assert len(fields) == 2
    assert len({ambiguous_contracts.field_uuids[item.field_contract_id] for item in fields}) == 2


def test_manual_axis_updates_registry_reanalysis_preserves_it_and_clear_restores_auto() -> None:
    facts = (
        _action(0, "query", "查询"),
        RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=1,
            action_id="query",
            request_id="query-request",
            method="GET",
            url="https://oa.example/api/items?status=open",
            response_body={"records": []},
        ),
    )
    compilation, contracts, store, _ = _contracts(facts)
    spec = compilation_to_workbench(compilation, contracts=contracts)
    step = spec["steps"][0]
    param = step["params"][0]
    original_source = param["source_binding"]
    edited = apply_edits(
        spec,
        [{
            "op": "update",
            "step_uuid": step["step_uuid"],
            "field_uuid": param["field_uuid"],
            "field": "display_name",
            "value": "人工状态",
        }],
    )
    edited_param = edited["steps"][0]["params"][0]
    assert edited_param["axis_decisions"]["display_name"]["manual_override"] is True
    assert edited_param["source_binding"] == original_source

    restored_registry = FieldRegistry.from_snapshot(edited["field_registry"])
    recompiled = integrate_compilation_contracts(
        compilation,
        facts=facts,
        capture_store=CaptureStore.from_snapshot(store.snapshot()),
        field_registry=restored_registry,
        value_evidence_factory=ValueEvidenceFactory(server_secret=b"integration-secret"),
    )
    projected = compilation_to_workbench(compilation, contracts=recompiled)
    assert projected["steps"][0]["params"][0]["display_name"] == "人工状态"
    assert projected["steps"][0]["params"][0]["source_binding"] == original_source

    cleared = apply_edits(
        edited,
        [{
            "op": "clear_field_axis",
            "step_uuid": step["step_uuid"],
            "field_uuid": param["field_uuid"],
            "axis": "display_name",
        }],
    )
    cleared_param = cleared["steps"][0]["params"][0]
    assert cleared_param["display_name"] == param["display_name"]
    assert cleared_param["axis_decisions"]["display_name"]["manual_override"] is False
    assert cleared_param["source_binding"] == original_source


def test_json_replacement_cannot_replace_server_contracts_or_evidence() -> None:
    current = {
        "tenant": TENANT,
        "recording_id": RECORDING,
        "revision": 2,
        "steps": [],
        "links": [],
        "capabilities": [],
        "request_facts": {"requests": [{
            "request_id": "request-proof",
            "disposition": "review_candidate",
        }]},
        "lineage_id": str(uuid4()),
        "capture_store": {"safe": True},
        "field_registry": {"safe": True},
        "field_evidence": [{"evidence_id": "proof"}],
        "evidence_graph_summary": {"node_count": 3},
        "meta": {"recording_engine": "playwright_v3"},
    }
    replacement = {
        **current,
        "capture_store": {"attacker": True},
        "field_registry": {"attacker": True},
        "field_evidence": [],
        "evidence_graph_summary": {"node_count": 0},
    }
    result = apply_replacement(current, replacement)
    assert result["capture_store"] == current["capture_store"]
    assert result["field_registry"] == current["field_registry"]
    assert result["field_evidence"] == current["field_evidence"]
    assert result["evidence_graph_summary"] == current["evidence_graph_summary"]


def test_v3_capability_edits_use_uuid_after_reorder_and_rename() -> None:
    first_uuid = "11111111-1111-4111-8111-111111111111"
    second_uuid = "22222222-2222-4222-8222-222222222222"
    spec = {
        "tenant": TENANT,
        "recording_id": RECORDING,
        "recording_contract_version": 1,
        "revision": 1,
        "steps": [],
        "links": [],
        "request_facts": {"requests": []},
        "meta": {},
        "capabilities": [
            {
                "capability_uuid": first_uuid,
                "capability_id": "legacy-first",
                "name": "same-name",
                "step_ids": [],
            },
            {
                "capability_uuid": second_uuid,
                "capability_id": "legacy-second",
                "name": "same-name",
                "step_ids": [],
            },
        ],
    }
    reordered = apply_edits(
        spec,
        [{
            "op": "reorder_capabilities",
            "capability_refs": [second_uuid, first_uuid],
        }],
    )
    edited = apply_edits(
        reordered,
        [{
            "op": "update_capability",
            "capability_uuid": first_uuid,
            "field": "name",
            "value": "renamed-first",
        }],
    )
    by_uuid = {item["capability_uuid"]: item for item in edited["capabilities"]}
    assert by_uuid[first_uuid]["name"] == "renamed-first"
    assert by_uuid[second_uuid]["name"] == "same-name"
    with pytest.raises(Exception, match="capability_uuid"):
        apply_edits(
            edited,
            [{
                "op": "update_capability",
                "capability_ref": "same-name",
                "field": "title",
                "value": "wrong target",
            }],
        )


def test_v3_step_and_field_edits_remain_uuid_targeted_after_redraw_and_reorder() -> None:
    step_a_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    step_b_uuid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    field_uuid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    spec = {
        "tenant": TENANT,
        "recording_id": RECORDING,
        "recording_contract_version": 1,
        "revision": 1,
        "links": [],
        "request_facts": {"requests": []},
        "meta": {},
        "capabilities": [],
        "steps": [
            {
                "step_id": "mutable-a",
                "step_uuid": step_a_uuid,
                "request_id": "request-a",
                "name": "redrawn",
                "method": "GET",
                "path": "/a",
                "params": [{
                    "field_uuid": field_uuid,
                    "field_id": field_uuid,
                    "path": "old.path",
                    "display_name": "Old",
                }],
            },
            {
                "step_id": "mutable-b",
                "step_uuid": step_b_uuid,
                "request_id": "request-b",
                "name": "other",
                "method": "GET",
                "path": "/b",
                "params": [],
            },
        ],
    }
    reordered = apply_edits(
        spec,
        [{
            "op": "reorder_steps",
            "step_uuids": [step_b_uuid, step_a_uuid],
        }],
    )
    # Simulate a redraw changing every mutable locator while permanent UUIDs stay.
    reordered["steps"][1]["step_id"] = "redrawn-step-id"
    reordered["steps"][1]["params"][0]["path"] = "new.path"
    edited = apply_edits(
        reordered,
        [{
            "op": "update",
            "step_uuid": step_a_uuid,
            "field_uuid": field_uuid,
            "field": "display_name",
            "value": "Permanent target",
        }],
    )
    target = next(item for item in edited["steps"] if item["step_uuid"] == step_a_uuid)
    assert target["params"][0]["display_name"] == "Permanent target"


def test_script_artifact_snapshot_is_complete_index_without_raw_source() -> None:
    lineage = uuid4()
    store = CaptureStore(
        tenant_scope=TENANT,
        recording_id=RECORDING,
        lineage_id=lineage,
    )
    source = b"const privateImplementation = 'never-send-raw-js';"
    artifact = store.record_script(
        url="https://oa.example/app.js",
        content=source,
        page_id="page-1",
        truncated=True,
        evidence_ids=("script-proof",),
        artifact_ref="recording-artifact:javascript_source:sha256:opaque",
        metadata={"source_map_url": "https://oa.example/app.js.map"},
        analysis={"call_count": 1},
    )
    payload = store.snapshot().model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "never-send-raw-js" not in serialized
    assert payload["scripts"][0]["page_ids"] == ["page-1"]
    assert payload["scripts"][0]["truncated"] is True
    assert payload["scripts"][0]["evidence_ids"] == ["script-proof"]
    restored = CaptureStore.from_snapshot(payload)
    restored.restore_script_content(artifact.content_hash, source)
    assert restored.get_script_content(artifact.content_hash) == source


@pytest.mark.asyncio
async def test_bootstrap_registers_correlated_dom_control_as_registry_evidence(tmp_path) -> None:
    service = RecordingApplication(
        pi_env={"PI_STUB": "1"},
        artifact_root=tmp_path,
        evidence_hmac_secret=b"bootstrap-integration-secret",
    )
    await service.start()
    created = await service.create_session(
        TENANT,
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://oa.example/app",
            base_url="https://oa.example",
        ),
    )
    live = await service._get_live(TENANT, created.recording_id)  # noqa: SLF001
    live.ledger.emit(
        ActionFact,
        action_id="save",
        action_type="click",
        label="提交",
    )
    live.ledger.emit(
        RequestFact,
        action_id="save",
        request_id="request-save",
        method="POST",
        url="https://oa.example/api/save",
        request_body={"approval": "yes"},
    )
    await service._drain_facts(live)  # noqa: SLF001
    facts = await service.repository.list_facts(TENANT, created.recording_id)
    compilation = compile_recording(
        tenant=TENANT,
        recording_id=created.recording_id,
        facts=facts,
    )
    integrate_compilation_contracts(
        compilation,
        facts=facts,
        capture_store=live.capture_store,
        field_registry=live.field_registry,
        value_evidence_factory=service.value_evidence_factory,
    )

    class Capture:
        @staticmethod
        def page_id(_page):
            return "page-1"

        @staticmethod
        def attach_page(_page):
            return "page-1"

        @staticmethod
        def frame_id(_frame):
            return "frame-main"

    class Runtime:
        @staticmethod
        async def collect_page_evidence(_page):
            return {
                "controls": (
                    DOMControl(
                        control_id="control-approval",
                        page_id="page-1",
                        frame_id="frame-main",
                        selector="#approval",
                        tag="input",
                        name="approval",
                        label="审批",
                    ),
                ),
                "runtime_components": (),
            }

        @staticmethod
        async def close():
            return None

    live.page = object()
    live.capture = Capture()  # type: ignore[assignment]
    live.runtime = Runtime()  # type: ignore[assignment]
    await service._collect_evidence(live, compilation)  # noqa: SLF001
    registry = live.field_registry.snapshot()
    assert len(registry.controls) == 1
    assert registry.controls[0].control_locator["selector"] == "#approval"
    assert registry.controls[0].field_uuid in {
        item.field_uuid for item in registry.fields
    }
    await service.close()


@pytest.mark.asyncio
async def test_recapture_request_selection_only_uses_current_generation(tmp_path) -> None:
    service = RecordingApplication(
        pi_env={"PI_STUB": "1"},
        artifact_root=tmp_path,
        evidence_hmac_secret=b"recapture-selection-secret",
    )
    await service.start()
    created = await service.create_session(
        TENANT,
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://oa.example/app",
            base_url="https://oa.example",
        ),
    )
    live = await service._get_live(TENANT, created.recording_id)  # noqa: SLF001
    live.ledger.emit(
        RequestFact,
        request_id="old-generation",
        method="POST",
        url="https://oa.example/api/old",
    )
    await service._recapture_command(TENANT, created.recording_id)  # noqa: SLF001
    live.ledger.emit(
        RequestFact,
        request_id="current-generation",
        method="POST",
        url="https://oa.example/api/current",
    )
    await service._choose_request(TENANT, created.recording_id, 0)  # noqa: SLF001
    session = await service.repository.get_session(TENANT, created.recording_id)
    assert session.metadata["chosen_request_id"] == "current-generation"
    assert {fact.request_id for fact in live.ledger.snapshot() if isinstance(fact, RequestFact)} == {
        "old-generation",
        "current-generation",
    }
    await service.close()


@pytest.mark.asyncio
async def test_restart_after_recapture_does_not_reproject_old_facts_into_new_store(tmp_path) -> None:
    service = RecordingApplication(
        pi_env={"PI_STUB": "1"},
        artifact_root=tmp_path,
        evidence_hmac_secret=b"recapture-restart-secret",
    )
    await service.start()
    created = await service.create_session(
        TENANT,
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://oa.example/app",
            base_url="https://oa.example",
        ),
    )
    live = await service._get_live(TENANT, created.recording_id)  # noqa: SLF001
    live.ledger.emit(
        RequestFact,
        request_id="old-generation",
        method="POST",
        url="https://oa.example/api/old",
    )
    await service._drain_facts(live)  # noqa: SLF001
    await service._recapture_command(TENANT, created.recording_id)  # noqa: SLF001
    live.ledger.emit(
        RequestFact,
        request_id="current-generation",
        method="POST",
        url="https://oa.example/api/current",
    )
    await service._drain_facts(live)  # noqa: SLF001

    service.live.pop((TENANT, created.recording_id))
    restored = await service._get_live(TENANT, created.recording_id)  # noqa: SLF001
    records = restored.capture_store.snapshot().records
    assert any(item.payload.get("request_id") == "current-generation" for item in records)
    assert all(item.payload.get("request_id") != "old-generation" for item in records)
    persisted = await service.repository.list_facts(TENANT, created.recording_id)
    assert {fact.request_id for fact in persisted if isinstance(fact, RequestFact)} == {
        "old-generation",
        "current-generation",
    }
    await service.close()


@pytest.mark.asyncio
async def test_browser_text_event_and_action_fact_never_expose_raw_pii(tmp_path) -> None:
    service = RecordingApplication(
        pi_env={"PI_STUB": "1"},
        artifact_root=tmp_path,
        evidence_hmac_secret=b"browser-input-boundary-secret",
    )
    await service.start()
    created = await service.create_session(
        TENANT,
        CreateRecordingRequest(
            subsystem="oa",
            start_url="https://oa.example/app",
            base_url="https://oa.example",
        ),
    )
    live = await service._get_live(TENANT, created.recording_id)  # noqa: SLF001

    class Keyboard:
        inserted = ""

        async def insert_text(self, value):
            self.inserted = value

    class Page:
        keyboard = Keyboard()

        @staticmethod
        async def evaluate(_script):
            return {"name": "email", "input_type": "email"}

    live.started = True
    live.capture_active = True
    live.page = Page()  # type: ignore[assignment]
    await service._dispatch_input(  # noqa: SLF001
        TENANT,
        created.recording_id,
        {"kind": "text", "text": "alice@example.test"},
    )
    events = service.events.history(TENANT, created.recording_id)
    step_event = next(item for item in reversed(events) if item.get("type") == "step")
    serialized_event = json.dumps(step_event, ensure_ascii=False)
    assert "alice@example.test" not in serialized_event
    assert step_event["step"]["value"] == "[REDACTED:PII]"
    action = next(
        fact for fact in reversed(live.ledger.snapshot()) if isinstance(fact, ActionFact)
    )
    serialized_fact = json.dumps(action.model_dump(mode="json"), ensure_ascii=False)
    assert "alice@example.test" not in serialized_fact
    assert action.payload["evidence_origin"] == "server_dispatched"
    assert action.payload["causal_eligible"] is True
    evidence = action.payload["details"]["value_evidence"]
    assert evidence[0]["sensitivity"] == "pii"
    assert live.page.keyboard.inserted == "alice@example.test"  # type: ignore[union-attr]
    await service.close()
