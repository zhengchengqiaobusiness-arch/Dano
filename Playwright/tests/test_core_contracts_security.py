from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from dano_recording.capture_store import CaptureStore
from dano_recording.domain.enums import (
    EnumEvidence,
    EnumSourceQuery,
    MappingCoverage,
    SnapshotCoverage,
    SnapshotCoverageKind,
)
from dano_recording.domain.fields import (
    AxisDecision,
    AxisOrigin,
    ConditionExpr,
    ConditionOperator,
    FieldDimension,
    RequiredContract,
    RequiredState,
    SourceBinding,
    SourceBindingKind,
)
from dano_recording.field_registry import (
    AxisDecisionConflict,
    BindingDirection,
    BindingRole,
    FieldAlias,
    FieldAliasKind,
    FieldRegistry,
    FieldRegistryError,
    FieldWireBinding,
)
from dano_recording.flow_migration import FlowMigrator, MigrationIssueKind
from dano_recording.value_evidence import (
    ValueEvidence,
    ValueEvidenceFactory,
    ValueRetention,
    ValueSensitivity,
    contains_plaintext,
    scoped_value_hmac,
)


class Vault:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def store_secret(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return f"secret-ref-{len(self.calls)}"


def test_scoped_hmac_is_equal_only_inside_same_tenant_lineage_and_type() -> None:
    secret = b"server-secret-for-tests"
    base = scoped_value_hmac(
        secret,
        tenant_scope="tenant-a",
        recording_lineage="lineage-a",
        value_type="user_id",
        value=8,
    )
    assert base == scoped_value_hmac(
        secret,
        tenant_scope="tenant-a",
        recording_lineage="lineage-a",
        value_type="user_id",
        value=8,
    )
    assert base != scoped_value_hmac(
        secret,
        tenant_scope="tenant-b",
        recording_lineage="lineage-a",
        value_type="user_id",
        value=8,
    )
    assert base != scoped_value_hmac(
        secret,
        tenant_scope="tenant-a",
        recording_lineage="lineage-b",
        value_type="user_id",
        value=8,
    )
    assert base != scoped_value_hmac(
        secret,
        tenant_scope="tenant-a",
        recording_lineage="lineage-a",
        value_type="project_id",
        value=8,
    )


def test_credential_plaintext_only_reaches_vault_and_never_model_serialization() -> None:
    vault = Vault()
    factory = ValueEvidenceFactory(
        server_secret=b"server-secret-for-tests",
        credential_vault=vault,
    )
    plaintext = "Bearer top-secret-token"
    evidence = factory.capture(
        tenant_scope="tenant-a",
        recording_lineage="lineage-a",
        field_name="authorization",
        value=plaintext,
    )
    assert vault.calls[0]["plaintext"] == plaintext.encode()
    assert evidence.value_ref == "secret-ref-1"
    assert evidence.redacted_sample is None
    assert evidence.runtime_resolver == "credential_store.resolve:secret-ref-1"
    assert not contains_plaintext(evidence.model_dump(mode="json"), plaintext)
    assert plaintext not in repr(factory)
    with pytest.raises(ValueError, match="persistent"):
        factory.capture(
            tenant_scope="tenant-a",
            recording_lineage="lineage-a",
            field_name="token",
            value="another-secret",
            retention=ValueRetention.PERSISTENT,
        )
    with pytest.raises(RuntimeError, match="encrypted credential vault"):
        ValueEvidenceFactory(server_secret=b"server-secret").capture(
            tenant_scope="tenant-a",
            recording_lineage="lineage-a",
            field_name="cookie",
            value="secret-cookie",
        )


def test_identity_evidence_keeps_only_resolver_and_scoped_fingerprint() -> None:
    factory = ValueEvidenceFactory(server_secret=b"server-secret-for-tests")
    evidence = factory.capture(
        tenant_scope="tenant-a",
        recording_lineage="lineage-a",
        field_name="creatorId",
        value="user-007",
    )
    dumped = evidence.model_dump(mode="json")
    assert evidence.runtime_resolver == "runtime_context.current_user.id"
    assert evidence.scoped_hmac.startswith("hmac-sha256:")
    assert not contains_plaintext(dumped, "user-007")
    with pytest.raises(ValueError, match="plaintext sample"):
        ValueEvidence(
            sensitivity=ValueSensitivity.IDENTITY,
            value_type="user_id",
            value_length=8,
            scoped_hmac=evidence.scoped_hmac,
            runtime_resolver="runtime_context.current_user.id",
            redacted_sample="user-007",
            retention=ValueRetention.PERSISTENT,
        )


def test_capture_store_deduplicates_definitions_but_retains_every_call() -> None:
    lineage = uuid4()
    store = CaptureStore(
        tenant_scope="tenant-a",
        recording_id="recording-a",
        lineage_id=lineage,
    )
    start = datetime.now(timezone.utc)
    first = store.record_network_call(
        method="post",
        url_or_path="https://example.test/projects/42?token=secret",
        page_id="page-a",
        started_at=start,
        finished_at=start + timedelta(milliseconds=20),
        status=201,
        request_schema={"type": "object", "properties": {"name": {"type": "string"}}},
        response_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
        initiator={"url": "https://example.test/?token=secret"},
    )
    second = store.record_network_call(
        method="POST",
        url_or_path="https://example.test/projects/99",
        page_id="page-a",
        started_at=start + timedelta(seconds=1),
        finished_at=start + timedelta(seconds=1, milliseconds=20),
        status=201,
        request_schema={"type": "object", "properties": {"name": {"type": "string"}}},
        response_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
    )
    assert first.request_definition_id == second.request_definition_id
    assert len(store.snapshot().request_definitions) == 1
    assert len(store.snapshot().observations) == 2
    assert len(store.list_unbound_business_requests()) == 2
    store.bind_observation(first.observation_id)
    assert [item.observation_id for item in store.list_unbound_business_requests()] == [
        second.observation_id
    ]

    script = store.record_script(
        url="https://example.test/app.js",
        content="const resolver = () => 1;",
        analysis={"api_functions": ["resolver"]},
    )
    store.record_script(
        url="https://cdn.example.test/app.js",
        content="const resolver = () => 1;",
    )
    snapshot = store.snapshot().model_dump(mode="json")
    assert len(snapshot["scripts"]) == 1
    assert "const resolver" not in json.dumps(snapshot)
    assert store.get_script_content(script.content_hash).startswith(b"const resolver")
    assert "secret" not in json.dumps(snapshot["observations"])

    safe_definition = store.register_request_definition(
        method="POST",
        url_or_path="/session",
        request_schema={
            "type": "object",
            "properties": {
                "token": {
                    "type": "string",
                    "example": "schema-credential-plaintext",
                    "enum": ["schema-credential-plaintext"],
                }
            },
        },
        response_schema={"type": "object", "example": {"token": "response-secret"}},
    )
    serialized_definition = json.dumps(safe_definition.model_dump(mode="json"))
    assert "schema-credential-plaintext" not in serialized_definition
    assert "response-secret" not in serialized_definition
    assert safe_definition.request_schema["properties"]["token"]["type"] == "string"
    restored = CaptureStore.from_snapshot(store.snapshot())
    assert restored.snapshot() == store.snapshot()
    next_generation = restored.next_generation()
    assert next_generation.capture_generation == 1
    assert next_generation.snapshot().observations == ()


def test_field_registry_keeps_uuid_across_alias_changes_and_supports_many_bindings() -> None:
    lineage = uuid4()
    registry = FieldRegistry(lineage)
    original = registry.register_field(
        aliases=[
            FieldAlias(
                kind=FieldAliasKind.LEGACY_ID,
                value="step-0.body.approver",
                context="lineage",
            )
        ]
    )
    renamed = registry.register_field(
        field_uuid=original.field_uuid,
        aliases=[
            FieldAlias(
                kind=FieldAliasKind.WIRE_PATH,
                value="payload.approverId",
                context="submit-request",
            )
        ],
    )
    assert renamed.field_uuid == original.field_uuid

    for path in ("body.approverId", "body.audit.approverId"):
        registry.add_wire_binding(
            FieldWireBinding(
                field_uuid=original.field_uuid,
                request_definition_id=uuid4(),
                step_uuid=uuid4(),
                direction=BindingDirection.INPUT,
                wire_path=path,
                wire_tokens=tuple(path.split(".")),
                binding_role=BindingRole.RUNTIME_SOURCE,
            )
        )
    assert len(registry.get_field(original.field_uuid).wire_binding_ids) == 2

    # Same label on query/submit pages remains two fields because alias context
    # is part of identity.
    query = registry.register_field(
        aliases=[
            FieldAlias(
                kind=FieldAliasKind.BUSINESS_NAME,
                value="审批人",
                context="query-form",
            )
        ]
    )
    submit = registry.register_field(
        aliases=[
            FieldAlias(
                kind=FieldAliasKind.BUSINESS_NAME,
                value="审批人",
                context="submit-form",
            )
        ]
    )
    assert query.field_uuid != submit.field_uuid


def test_field_registry_merges_observations_only_for_identical_wire_binding() -> None:
    lineage = uuid4()
    registry = FieldRegistry(lineage)
    field = registry.register_field()
    binding_id = uuid4()
    request_definition_id = uuid4()
    step_uuid = uuid4()
    base = FieldWireBinding(
        binding_id=binding_id,
        field_uuid=field.field_uuid,
        request_definition_id=request_definition_id,
        observation_ids=("page-2",),
        step_uuid=step_uuid,
        direction=BindingDirection.INPUT,
        wire_path="body.filter.status",
        wire_tokens=("body", "filter", "status"),
        binding_role=BindingRole.RUNTIME_SOURCE,
    )

    registry.add_wire_binding(base)
    registry.add_wire_binding(
        base.model_copy(update={"observation_ids": ("page-1", "page-2")})
    )
    stored = next(
        item for item in registry.snapshot().bindings if item.binding_id == binding_id
    )
    assert stored.observation_ids == ("page-1", "page-2")

    with pytest.raises(FieldRegistryError, match="is immutable"):
        registry.add_wire_binding(
            base.model_copy(
                update={
                    "observation_ids": ("page-3",),
                    "wire_path": "body.filter.owner",
                    "wire_tokens": ("body", "filter", "owner"),
                }
            )
        )


def test_manual_override_is_per_axis_and_clear_restores_only_that_axis() -> None:
    registry = FieldRegistry(uuid4())
    field = registry.register_field()
    registry.apply_axis_decision(
        field.field_uuid,
        AxisDecision(
            axis=FieldDimension.DISPLAY_NAME,
            value="old-name",
            origin=AxisOrigin.DETERMINISTIC,
            decided_at_revision=1,
        ),
    )
    registry.apply_axis_decision(
        field.field_uuid,
        AxisDecision(
            axis=FieldDimension.SOURCE_BINDING,
            value=SourceBinding(kind=SourceBindingKind.CALLER),
            origin=AxisOrigin.DETERMINISTIC,
            decided_at_revision=1,
        ),
    )
    registry.apply_axis_decision(
        field.field_uuid,
        AxisDecision(
            axis=FieldDimension.DISPLAY_NAME,
            value="manual-name",
            origin=AxisOrigin.MANUAL,
            decided_at_revision=2,
        ),
    )
    registry.apply_axis_decision(
        field.field_uuid,
        AxisDecision(
            axis=FieldDimension.SOURCE_BINDING,
            value=SourceBinding(
                kind=SourceBindingKind.RUNTIME_CONTEXT,
                runtime_resolver="runtime_context.current_user.id",
            ),
            origin=AxisOrigin.DETERMINISTIC,
            decided_at_revision=3,
        ),
    )
    with pytest.raises(AxisDecisionConflict, match="manual override"):
        registry.apply_axis_decision(
            field.field_uuid,
            AxisDecision(
                axis=FieldDimension.DISPLAY_NAME,
                value="pi-name",
                origin=AxisOrigin.PI,
                decided_at_revision=3,
            ),
        )
    with pytest.raises(AxisDecisionConflict, match="stale"):
        registry.apply_axis_decision(
            field.field_uuid,
            AxisDecision(
                axis=FieldDimension.SOURCE_BINDING,
                value=SourceBinding(kind=SourceBindingKind.CALLER),
                origin=AxisOrigin.DETERMINISTIC,
                decided_at_revision=2,
            ),
        )
    restored = registry.clear_manual_override(
        field.field_uuid,
        FieldDimension.DISPLAY_NAME,
        revision=4,
    )
    assert restored.decisions[FieldDimension.DISPLAY_NAME].value == "old-name"
    assert not restored.decisions[FieldDimension.DISPLAY_NAME].manual_override
    assert (
        restored.decisions[FieldDimension.SOURCE_BINDING].value.runtime_resolver
        == "runtime_context.current_user.id"
    )
    round_trip = FieldRegistry.from_snapshot(registry.snapshot())
    assert round_trip.snapshot() == registry.snapshot()


def test_weaker_agreement_does_not_downgrade_grounded_axis_origin() -> None:
    registry = FieldRegistry(uuid4())
    field = registry.register_field()
    observed = AxisDecision(
        axis=FieldDimension.BUSINESS_TYPE,
        value="date",
        origin=AxisOrigin.OBSERVED,
        evidence_ids=("native-date-control",),
        decided_at_revision=1,
    )
    registry.apply_axis_decision(field.field_uuid, observed)
    registry.apply_axis_decision(
        field.field_uuid,
        AxisDecision(
            axis=FieldDimension.BUSINESS_TYPE,
            value="date",
            origin=AxisOrigin.PI,
            evidence_ids=("pi-turn",),
            decided_at_revision=2,
        ),
    )
    effective = registry.get_field(field.field_uuid).decisions[FieldDimension.BUSINESS_TYPE]
    assert effective.origin is AxisOrigin.OBSERVED
    assert effective.evidence_ids == ("native-date-control",)


def test_required_contract_axes_and_conditions_are_independent() -> None:
    condition = ConditionExpr(
        operator=ConditionOperator.EQUALS,
        field_uuid="mode-field",
        value="advanced",
    )
    contract = RequiredContract(
        wire_required=RequiredState.TRUE,
        caller_required=RequiredState.FALSE,
        wire_condition=condition,
    )
    assert contract.wire_is_required({"mode-field": "advanced"}) is True
    assert contract.wire_is_required({"mode-field": "simple"}) is False
    assert contract.caller_is_required({"mode-field": "advanced"}) is False


def test_enum_evidence_does_not_overclaim_snapshot_coverage() -> None:
    with pytest.raises(ValueError, match="static_domain"):
        EnumEvidence(
            snapshot_coverage=SnapshotCoverage(
                kind=SnapshotCoverageKind.VISIBLE_WINDOW,
                observed_count=10,
            ),
            mapping_coverage=MappingCoverage.STATIC_DOMAIN,
        )
    query = EnumSourceQuery(
        request_definition_id=str(uuid4()),
        method="get",
        request_template={"query": {"keyword": "{{label}}"}},
        label_path="records[].label",
        value_path="records[].id",
        exact_lookup=True,
    )
    evidence = EnumEvidence(
        mapping_coverage=MappingCoverage.RUNTIME_RESOLVABLE,
        source_query=query,
    )
    assert evidence.source_query.method == "GET"
    with pytest.raises(ValueError, match="plaintext"):
        EnumSourceQuery(
            request_definition_id=str(uuid4()),
            method="GET",
            request_template={
                "headers": [{"name": "Authorization", "value": "Bearer raw-secret"}]
            },
            label_path="records[].label",
            value_path="records[].id",
            exact_lookup=True,
        )


def test_flow_migration_removes_batch_lock_and_plaintext_contracts() -> None:
    vault = Vault()
    factory = ValueEvidenceFactory(
        server_secret=b"server-secret-for-tests",
        credential_vault=vault,
    )
    lineage = uuid4()
    migrator = FlowMigrator(
        lineage_id=lineage,
        value_evidence_factory=factory,
        tenant_scope="tenant-a",
    )
    legacy = {
        "revision": 7,
        "capabilities": [
            {
                "kind": "submit_batch",
                "fields": [
                    {
                        "field_id": "submit:0:body.approverId",
                        "request_id": "submit-request",
                        "wire_path": "body.approverId",
                        "name": "审批人",
                        "type": "string",
                        "required": True,
                        "source_kind": "user_input",
                        "locked": True,
                        "manual_edit": {"name": True},
                        "mapping_complete": True,
                        "enum_confirmed": True,
                        "options": [{"label": "张三", "value": "8"}],
                    }
                ],
            }
        ],
        "request_template": {
            "authorization": "Bearer migration-secret",
            "creatorId": "user-007",
        },
    }
    result = migrator.migrate(legacy)
    dumped = (
        result.snapshot.model_dump()
        if hasattr(result.snapshot, "model_dump")
        else result.snapshot
    )
    serialized = json.dumps(dumped, ensure_ascii=False, default=str)
    assert result.snapshot["capabilities"][0]["kind"] == "submit"
    field = result.snapshot["capabilities"][0]["fields"][0]
    assert "locked" not in field
    assert field["axis_decisions"]["display_name"]["manual_override"] is True
    assert field["axis_decisions"]["source_binding"]["manual_override"] is False
    assert field["required_contract"]["wire_required"] == "true"
    assert field["required_contract"]["caller_required"] == "true"
    assert field["enum_evidence"]["mapping_coverage"] == "observed_set"
    assert field["enum_evidence"]["mapping_coverage"] != "static_domain"
    assert "migration-secret" not in serialized
    assert "user-007" not in serialized
    assert result.snapshot["request_template"]["authorization"]["secret_ref"]
    assert (
        result.snapshot["request_template"]["creatorId"]["runtime_resolver"]
        == "runtime_context.current_user.id"
    )
    assert len(result.value_evidence) == 2
    second = migrator.migrate(dict(result.snapshot))
    assert second.changed is False
    assert second.snapshot == result.snapshot


def test_flow_migration_keeps_unknowns_and_emits_specific_contract_fault() -> None:
    migrator = FlowMigrator(lineage_id=uuid4())
    result = migrator.migrate(
        {
            "revision": 1,
            "fields": [
                {
                    "field_contract_id": "body.gslx",
                    "wire_path": "body.gslx",
                    "required": True,
                    "source_kind": "unknown",
                }
            ],
            "token": "plaintext-without-vault",
        }
    )
    codes = {item.code for item in result.issues if item.kind is MigrationIssueKind.CONTRACT_FAULT}
    assert codes == {
        "secret_runtime_resolver_missing",
        "wire_required_without_provider",
    }
    field = result.snapshot["fields"][0]
    assert field["required_contract"]["caller_required"] == "unknown"
    assert "plaintext-without-vault" not in json.dumps(result.snapshot, default=str)


def test_flow_migration_secures_header_pairs_json_bodies_and_url_query_secrets() -> None:
    vault = Vault()
    migrator = FlowMigrator(
        lineage_id=uuid4(),
        tenant_scope="tenant-a",
        value_evidence_factory=ValueEvidenceFactory(
            server_secret=b"server-secret-for-tests",
            credential_vault=vault,
        ),
    )
    result = migrator.migrate(
        {
            "headers": [
                {"name": "Authorization", "value": "Bearer raw-header-secret"},
                {"name": "Cookie", "value": "session=raw-cookie-secret"},
            ],
            "url": "https://example.test/items?token=raw-query-secret&page=1",
            "request_body": json.dumps(
                {"tenantId": "tenant-plain-id", "tokens": ["raw-a", "raw-b"]}
            ),
        }
    )
    serialized = json.dumps(result.snapshot, ensure_ascii=False, default=str)
    for plaintext in (
        "raw-header-secret",
        "raw-cookie-secret",
        "raw-query-secret",
        "tenant-plain-id",
        "raw-a",
        "raw-b",
    ):
        assert plaintext not in serialized
    assert "credential_store.resolve" in serialized
    assert "runtime_context.current_tenant.id" in serialized
    assert len(vault.calls) == 5
