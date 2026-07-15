"""One-way migration from legacy FlowSpec field contracts to recording V3."""

from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
import json
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid5

from pydantic import model_validator

from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.domain._base import FrozenModel, freeze_json
from dano_recording.domain.enums import (
    EnumEvidence,
    MappingCoverage,
    SnapshotCoverage,
    SnapshotCoverageKind,
    SourceScope,
)
from dano_recording.domain.fields import (
    AxisDecision,
    AxisOrigin,
    FieldDimension,
    ProviderBinding,
    ProviderKind,
    RequiredContract,
    RequiredState,
    SourceBinding,
    SourceBindingKind,
)
from dano_recording.field_registry import (
    FieldAlias,
    FieldAliasKind,
    FieldRegistry,
    FieldRegistrySnapshot,
)
from dano_recording.value_evidence import ValueEvidence, ValueEvidenceFactory


MIGRATION_VERSION = 1


class MigrationIssueKind(StrEnum):
    ADVISORY = "advisory"
    CONTRACT_FAULT = "contract_fault"


class MigrationIssue(FrozenModel):
    issue_id: str
    kind: MigrationIssueKind
    code: str
    path: str
    message: str
    field_uuid: UUID | None = None


class FlowMigrationResult(FrozenModel):
    snapshot: dict[str, Any]
    registry: FieldRegistrySnapshot
    value_evidence: tuple[ValueEvidence, ...] = ()
    issues: tuple[MigrationIssue, ...] = ()
    changed: bool = True

    @model_validator(mode="after")
    def _freeze_snapshot(self) -> "FlowMigrationResult":
        object.__setattr__(self, "snapshot", freeze_json(self.snapshot))
        return self


_AXIS_ALIASES: dict[str, FieldDimension] = {
    "name": FieldDimension.DISPLAY_NAME,
    "display_name": FieldDimension.DISPLAY_NAME,
    "label": FieldDimension.DISPLAY_NAME,
    "business_type": FieldDimension.BUSINESS_TYPE,
    "type": FieldDimension.BUSINESS_TYPE,
    "classification": FieldDimension.CLASSIFICATION,
    "source": FieldDimension.SOURCE_BINDING,
    "source_kind": FieldDimension.SOURCE_BINDING,
    "source_binding": FieldDimension.SOURCE_BINDING,
    "default": FieldDimension.DEFAULT_VALUE,
    "default_value": FieldDimension.DEFAULT_VALUE,
    "caller_required": FieldDimension.CALLER_REQUIRED,
    "wire_required": FieldDimension.WIRE_REQUIRED,
    "required": FieldDimension.CALLER_REQUIRED,
    "required_conditions": FieldDimension.REQUIRED_CONDITIONS,
    "exposed": FieldDimension.EXPOSURE,
    "exposed_to_caller": FieldDimension.EXPOSURE,
    "enum": FieldDimension.ENUM_BINDING,
    "choice_contract": FieldDimension.ENUM_BINDING,
    "enum_binding": FieldDimension.ENUM_BINDING,
}

_USER_ID_KEYS = {
    "userid",
    "user_id",
    "creatorid",
    "creator_id",
    "ownerid",
    "owner_id",
    "operatorid",
    "operator_id",
    "employeeid",
    "employee_id",
}
_TENANT_ID_KEYS = {"tenantid", "tenant_id"}
_CREDENTIAL_COLLECTION_KEYS = {
    "credentials",
    "cookies",
    "secrets",
    "tokens",
    "auth_headers",
}


def _required_state(value: Any) -> RequiredState:
    if value is True or str(value).lower() == "true":
        return RequiredState.TRUE
    if value is False or str(value).lower() == "false":
        return RequiredState.FALSE
    return RequiredState.UNKNOWN


def _is_field_dict(value: dict[str, Any]) -> bool:
    identity = "field_uuid" in value or "field_id" in value or "field_contract_id" in value
    contract = any(
        key in value
        for key in (
            "wire_path",
            "path",
            "wire_name",
            "source_kind",
            "required",
            "classification",
            "mapping_complete",
        )
    )
    return identity and contract


def _source_binding(field: dict[str, Any]) -> SourceBinding:
    existing = field.get("source_binding")
    if isinstance(existing, dict):
        try:
            return SourceBinding.model_validate(existing)
        except ValueError:
            pass
    source_kind = str(field.get("source_kind") or field.get("source") or "unknown").lower()
    request_ref = field.get("source_request_id") or field.get("request_definition_id")
    response_path = field.get("source_path") or field.get("response_path")
    runtime_resolver = field.get("runtime_resolver")
    if source_kind in {"user_input", "caller", "caller_input"}:
        return SourceBinding(kind=SourceBindingKind.CALLER)
    if source_kind in {"previous_response", "dependency_response", "response"}:
        if request_ref and response_path:
            return SourceBinding(
                kind=SourceBindingKind.PREVIOUS_RESPONSE,
                request_id=str(request_ref),
                response_path=str(response_path),
            )
        return SourceBinding(kind=SourceBindingKind.UNKNOWN)
    if source_kind in {"page_context", "runtime_context", "request_header", "system"}:
        if runtime_resolver:
            return SourceBinding(
                kind=SourceBindingKind.RUNTIME_CONTEXT,
                runtime_resolver=str(runtime_resolver),
            )
        return SourceBinding(kind=SourceBindingKind.UNKNOWN)
    if source_kind == "constant" and field.get("constant") is not None:
        return SourceBinding(kind=SourceBindingKind.CONSTANT, value=field["constant"])
    if source_kind in {"computed", "derived"} and field.get("expression"):
        return SourceBinding(
            kind=SourceBindingKind.DERIVED,
            expression=str(field["expression"]),
        )
    if field.get("default_value") is not None:
        return SourceBinding(kind=SourceBindingKind.DEFAULT, value=field["default_value"])
    return SourceBinding(kind=SourceBindingKind.UNKNOWN)


def _provider_for(source: SourceBinding, field: dict[str, Any]) -> ProviderBinding | None:
    if source.kind is SourceBindingKind.CALLER:
        return ProviderBinding(kind=ProviderKind.CALLER)
    if source.kind in {
        SourceBindingKind.PREVIOUS_RESPONSE,
        SourceBindingKind.DEPENDENCY_RESPONSE,
    }:
        request_ref = source.request_definition_id or source.request_id
        if request_ref and source.response_path:
            return ProviderBinding(
                kind=ProviderKind.DEPENDENCY_RESPONSE,
                request_definition_id=str(request_ref),
                response_path=source.response_path,
            )
    if source.kind is SourceBindingKind.RUNTIME_CONTEXT and source.runtime_resolver:
        return ProviderBinding(
            kind=ProviderKind.RUNTIME_CONTEXT,
            runtime_resolver=source.runtime_resolver,
        )
    if source.kind is SourceBindingKind.CONSTANT and source.value is not None:
        return ProviderBinding(kind=ProviderKind.CONSTANT, value=source.value)
    if source.kind is SourceBindingKind.DEFAULT:
        return ProviderBinding(kind=ProviderKind.DEFAULT, value=source.value)
    if source.kind is SourceBindingKind.DERIVED and source.expression:
        return ProviderBinding(kind=ProviderKind.DERIVED, expression=source.expression)
    if field.get("default_value") is not None:
        return ProviderBinding(kind=ProviderKind.DEFAULT, value=field["default_value"])
    return None


def _manual_axes(field: dict[str, Any]) -> set[FieldDimension]:
    result: set[FieldDimension] = set()
    manual = field.get("manual_edit")
    if isinstance(manual, dict):
        result.update(
            _AXIS_ALIASES[str(key)]
            for key, enabled in manual.items()
            if enabled and str(key) in _AXIS_ALIASES
        )
    elif isinstance(manual, list | tuple | set):
        result.update(
            _AXIS_ALIASES[str(key)] for key in manual if str(key) in _AXIS_ALIASES
        )
    elif isinstance(manual, str) and manual in _AXIS_ALIASES:
        result.add(_AXIS_ALIASES[manual])
    evidence = field.get("evidence") or ()
    if isinstance(evidence, dict):
        evidence = (evidence,)
    for item in evidence if isinstance(evidence, list | tuple) else ():
        if not isinstance(item, dict) or item.get("kind") != "manual_edit":
            continue
        axis = str(item.get("axis") or item.get("field") or item.get("dimension") or "")
        if axis in _AXIS_ALIASES:
            result.add(_AXIS_ALIASES[axis])
    for item in field.get("evidence_ids") or ():
        text = str(item)
        if text.startswith("manual_edit:"):
            axis = text.partition(":")[2]
            if axis in _AXIS_ALIASES:
                result.add(_AXIS_ALIASES[axis])
    return result


class FlowMigrator:
    def __init__(
        self,
        *,
        lineage_id: UUID | str,
        registry: FieldRegistry | None = None,
        value_evidence_factory: ValueEvidenceFactory | None = None,
        tenant_scope: str = "migration",
        redaction: RedactionPolicy | None = None,
    ) -> None:
        self.lineage_id = UUID(str(lineage_id))
        self.registry = registry or FieldRegistry(self.lineage_id)
        if self.registry.lineage_id != self.lineage_id:
            raise ValueError("field registry belongs to a different lineage")
        self.value_evidence_factory = value_evidence_factory
        self.tenant_scope = tenant_scope
        self.redaction = redaction or RedactionPolicy()
        self._issues: list[MigrationIssue] = []
        self._value_evidence: list[ValueEvidence] = []

    def _issue(
        self,
        *,
        kind: MigrationIssueKind,
        code: str,
        path: str,
        message: str,
        field_uuid: UUID | None = None,
    ) -> None:
        identity = uuid5(self.lineage_id, f"issue:{kind.value}:{code}:{path}:{field_uuid or ''}")
        self._issues.append(
            MigrationIssue(
                issue_id=str(identity),
                kind=kind,
                code=code,
                path=path,
                message=message,
                field_uuid=field_uuid,
            )
        )

    def _decision(
        self,
        *,
        field_uuid: UUID,
        axis: FieldDimension,
        value: Any,
        manual: bool,
        evidence_ids: Iterable[str],
        revision: int,
    ) -> AxisDecision:
        decision_id = uuid5(
            self.lineage_id,
            f"decision:{field_uuid}:{axis.value}:{revision}:{repr(value)}:{manual}",
        )
        return AxisDecision(
            decision_id=str(decision_id),
            axis=axis,
            value=value,
            origin=AxisOrigin.MANUAL if manual else AxisOrigin.DETERMINISTIC,
            evidence_ids=tuple(str(item) for item in evidence_ids),
            confidence=None,
            decided_at_revision=revision,
            manual_override=manual,
        )

    def _migrate_field(self, field: dict[str, Any], *, path: str, revision: int) -> dict[str, Any]:
        old_id = str(field.get("field_contract_id") or field.get("field_id") or "").strip()
        request_context = str(
            field.get("request_definition_id")
            or field.get("request_id")
            or field.get("step_uuid")
            or field.get("step_id")
            or path.rpartition(".")[0]
        )
        wire_path = str(field.get("wire_path") or field.get("path") or field.get("wire_name") or "")
        aliases: list[FieldAlias] = []
        if old_id:
            aliases.append(
                FieldAlias(
                    kind=FieldAliasKind.LEGACY_ID,
                    value=old_id,
                    context="lineage",
                    introduced_at_revision=revision,
                )
            )
        if wire_path:
            aliases.append(
                FieldAlias(
                    kind=FieldAliasKind.WIRE_PATH,
                    value=wire_path,
                    context=request_context,
                    introduced_at_revision=revision,
                )
            )
        requested_uuid = field.get("field_uuid")
        if requested_uuid is None:
            stable_name = old_id or f"{request_context}:{wire_path}"
            requested_uuid = uuid5(self.lineage_id, f"legacy-field:{stable_name}")
        canonical = self.registry.register_field(
            field_uuid=requested_uuid,
            aliases=aliases,
        )
        field_uuid = canonical.field_uuid
        source = _source_binding(field)
        manual_axes = _manual_axes(field)
        evidence_ids = tuple(str(item) for item in field.get("evidence_ids") or ())

        axis_values: dict[FieldDimension, Any] = {}
        if field.get("display_name") is not None or field.get("name") is not None:
            axis_values[FieldDimension.DISPLAY_NAME] = field.get("display_name", field.get("name"))
        if field.get("business_type") is not None or field.get("type") is not None:
            axis_values[FieldDimension.BUSINESS_TYPE] = field.get(
                "business_type", field.get("type")
            )
        if field.get("classification") is not None:
            axis_values[FieldDimension.CLASSIFICATION] = field["classification"]
        axis_values[FieldDimension.SOURCE_BINDING] = source
        if "default_value" in field:
            axis_values[FieldDimension.DEFAULT_VALUE] = field.get("default_value")
        if "exposed_to_caller" in field or "exposed" in field:
            axis_values[FieldDimension.EXPOSURE] = bool(
                field.get("exposed_to_caller", field.get("exposed"))
            )

        wire_required = _required_state(field.get("wire_required", field.get("required")))
        explicit_caller = field.get("caller_required")
        if explicit_caller is not None:
            caller_required = _required_state(explicit_caller)
        elif wire_required is RequiredState.FALSE:
            caller_required = RequiredState.FALSE
        elif source.kind is SourceBindingKind.CALLER:
            caller_required = wire_required
        elif source.kind in {
            SourceBindingKind.RUNTIME_CONTEXT,
            SourceBindingKind.PREVIOUS_RESPONSE,
            SourceBindingKind.DEPENDENCY_RESPONSE,
            SourceBindingKind.CONSTANT,
            SourceBindingKind.DEFAULT,
            SourceBindingKind.DERIVED,
        }:
            caller_required = RequiredState.FALSE
        else:
            caller_required = RequiredState.UNKNOWN
        provider = _provider_for(source, field)
        required_contract = RequiredContract(
            wire_required=wire_required,
            caller_required=caller_required,
            provider=provider,
        )
        axis_values[FieldDimension.WIRE_REQUIRED] = wire_required
        axis_values[FieldDimension.CALLER_REQUIRED] = caller_required
        if wire_required is RequiredState.TRUE and provider is None:
            self._issue(
                kind=MigrationIssueKind.CONTRACT_FAULT,
                code="wire_required_without_provider",
                path=path,
                message="接口必填字段缺少调用方、默认值或运行时 provider。",
                field_uuid=field_uuid,
            )

        options = field.get("options")
        if options is None and isinstance(field.get("choice_contract"), dict):
            options = field["choice_contract"].get("options")
        observed_count = len(options) if isinstance(options, list | tuple) else 0
        mapping_complete = bool(field.get("mapping_complete", False))
        enum_confirmed = bool(field.get("enum_confirmed", False))
        if mapping_complete or enum_confirmed or options:
            mapping_coverage = (
                MappingCoverage.OBSERVED_SET
                if mapping_complete or observed_count > 1
                else MappingCoverage.SELECTED_ONLY
            )
            enum_evidence = EnumEvidence(
                selected_pair_verified=enum_confirmed,
                observed_mapping_complete=mapping_complete,
                snapshot_coverage=SnapshotCoverage(
                    kind=SnapshotCoverageKind.UNKNOWN,
                    observed_count=observed_count,
                    truncated=False,
                ),
                # A legacy complete flag never proves a system-wide static domain.
                mapping_coverage=mapping_coverage,
                source_scope=SourceScope(),
                evidence_ids=evidence_ids,
            )
            axis_values[FieldDimension.ENUM_BINDING] = enum_evidence
            field["enum_evidence"] = enum_evidence.model_dump(mode="json")

        decisions: dict[str, Any] = {}
        for axis, value in axis_values.items():
            decision = self._decision(
                field_uuid=field_uuid,
                axis=axis,
                value=value,
                manual=axis in manual_axes,
                evidence_ids=evidence_ids,
                revision=revision,
            )
            current = self.registry.get_field(field_uuid).decisions.get(axis)
            # A newer legacy projection may omit manual_edit metadata.  The
            # registry remains authoritative and preserves the earlier
            # per-axis manual decision instead of treating omission as unlock.
            if current is not None and current.manual_override and not decision.manual_override:
                decisions[axis.value] = current.model_dump(mode="json")
                continue
            self.registry.apply_axis_decision(field_uuid, decision)
            decisions[axis.value] = decision.model_dump(mode="json")

        if field.pop("locked", False) and not manual_axes:
            self._issue(
                kind=MigrationIssueKind.ADVISORY,
                code="legacy_lock_without_axis_evidence",
                path=path,
                message="旧字段锁缺少 manual_edit 轴证据，已移除且未猜测人工覆盖范围。",
                field_uuid=field_uuid,
            )
        field.pop("manual_edit", None)
        field.pop("mapping_complete", None)
        field.pop("enum_confirmed", None)
        field["field_uuid"] = str(field_uuid)
        field["lineage_id"] = str(self.lineage_id)
        field["aliases"] = [item.model_dump(mode="json") for item in canonical.aliases]
        field["axis_decisions"] = decisions
        field["required_contract"] = required_contract.model_dump(mode="json")
        return field

    @staticmethod
    def _already_resolved(value: Any) -> bool:
        if isinstance(value, dict):
            return bool(value.get("secret_ref") or value.get("runtime_resolver"))
        if not isinstance(value, str):
            return False
        return value.startswith(("{{", "[REDACTED", "credential_store.", "secret://"))

    def _secure_leaf(self, key: str, value: Any, *, path: str) -> Any:
        normalized = key.strip().lower().replace("-", "_")
        compact = normalized.replace("_", "")
        if self._already_resolved(value):
            return value
        sensitive_key = (
            self.redaction.is_sensitive_key(normalized)
            or normalized in _CREDENTIAL_COLLECTION_KEYS
        )
        if sensitive_key and isinstance(value, list | tuple):
            return [
                self._secure_leaf(key, item, path=f"{path}[{index}]")
                for index, item in enumerate(value)
            ]
        if sensitive_key and isinstance(value, str | bytes | int | float):
            if self.value_evidence_factory is None:
                self._issue(
                    kind=MigrationIssueKind.CONTRACT_FAULT,
                    code="secret_runtime_resolver_missing",
                    path=path,
                    message="旧凭证明文已删除，但没有可用的加密凭证库 resolver。",
                )
                return "[REDACTED:MIGRATION_REQUIRES_CREDENTIAL_VAULT]"
            evidence = self.value_evidence_factory.capture(
                tenant_scope=self.tenant_scope,
                recording_lineage=str(self.lineage_id),
                value=value,
                field_name=key,
            )
            self._value_evidence.append(evidence)
            return {
                "secret_ref": evidence.value_ref,
                "runtime_resolver": evidence.runtime_resolver,
                "evidence_id": evidence.evidence_id,
            }
        resolver = None
        if normalized in _USER_ID_KEYS or compact in _USER_ID_KEYS:
            resolver = "runtime_context.current_user.id"
        elif normalized in _TENANT_ID_KEYS or compact in _TENANT_ID_KEYS:
            resolver = "runtime_context.current_tenant.id"
        if resolver is not None and isinstance(value, str | int):
            evidence_id = None
            scoped_hmac = None
            if self.value_evidence_factory is not None:
                evidence = self.value_evidence_factory.capture(
                    tenant_scope=self.tenant_scope,
                    recording_lineage=str(self.lineage_id),
                    value=value,
                    field_name=key,
                    runtime_resolver=resolver,
                )
                self._value_evidence.append(evidence)
                evidence_id = evidence.evidence_id
                scoped_hmac = evidence.scoped_hmac
            return {
                "runtime_resolver": resolver,
                "scoped_hmac": scoped_hmac,
                "evidence_id": evidence_id,
            }
        if isinstance(value, str):
            return self.redaction.redact_text(value)
        return value

    def _secure_url(self, value: str, *, path: str) -> str:
        try:
            parts = urlsplit(value)
            if not parts.query:
                return self.redaction.redact_url(value)
            query: list[tuple[str, str]] = []
            for key, raw in parse_qsl(parts.query, keep_blank_values=True):
                secured = self._secure_leaf(key, raw, path=f"{path}.query.{key}")
                if isinstance(secured, dict):
                    resolver = secured.get("runtime_resolver")
                    rendered = f"{{{{{resolver}}}}}" if resolver else "[REDACTED]"
                else:
                    rendered = str(secured)
                query.append((key, rendered))
            # User info can contain basic-auth credentials and is never part of
            # an executable request definition.
            host = parts.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if parts.port:
                host = f"{host}:{parts.port}"
            return urlunsplit(
                (parts.scheme, host, parts.path, urlencode(query, doseq=True), "")
            )
        except (TypeError, ValueError):
            self._issue(
                kind=MigrationIssueKind.ADVISORY,
                code="invalid_legacy_url",
                path=path,
                message="旧请求 URL 无法解析，已按失效 URL 脱敏。",
            )
            return "[INVALID_URL]"

    def _walk(self, value: Any, *, path: str, revision: int) -> Any:
        if isinstance(value, str):
            return "submit" if value == "submit_batch" else value
        if isinstance(value, list):
            return [
                self._walk(item, path=f"{path}[{index}]", revision=revision)
                for index, item in enumerate(value)
            ]
        if not isinstance(value, dict):
            return value
        migrated: dict[str, Any] = {}
        pair_name = value.get("name")
        if pair_name is None:
            pair_name = value.get("key")
        for raw_key, raw_value in value.items():
            key = "submit" if str(raw_key) == "submit_batch" else str(raw_key)
            child_path = f"{path}.{key}" if path else key
            security_key = (
                str(pair_name)
                if pair_name is not None and key.lower() in {"value", "header_value"}
                else key
            )
            if (
                key.lower() in {"body", "post_data", "request_body"}
                and isinstance(raw_value, str)
                and raw_value.lstrip().startswith(("{", "["))
            ):
                try:
                    parsed_body = json.loads(raw_value)
                except json.JSONDecodeError:
                    parsed_body = None
                if parsed_body is not None:
                    secured_body = self._walk(
                        parsed_body,
                        path=child_path,
                        revision=revision,
                    )
                    migrated[key] = json.dumps(
                        secured_body,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    continue
            if key.lower() in {"url", "uri", "href"} and isinstance(raw_value, str):
                secured = self._secure_url(raw_value, path=child_path)
            else:
                secured = self._secure_leaf(security_key, raw_value, path=child_path)
            if key in migrated and str(raw_key) == "submit_batch":
                self._issue(
                    kind=MigrationIssueKind.ADVISORY,
                    code="submit_batch_key_collision",
                    path=child_path,
                    message="旧 submit_batch 键与 submit 键冲突，保留显式 submit 值。",
                )
                continue
            migrated[key] = self._walk(secured, path=child_path, revision=revision)
        if _is_field_dict(migrated):
            migrated = self._migrate_field(migrated, path=path, revision=revision)
        return migrated

    def migrate(self, snapshot: dict[str, Any]) -> FlowMigrationResult:
        source = deepcopy(snapshot)
        existing_version = int(source.get("recording_contract_version") or 0)
        if existing_version >= MIGRATION_VERSION and source.get("field_registry"):
            registry_snapshot = FieldRegistrySnapshot.model_validate(source["field_registry"])
            return FlowMigrationResult(
                snapshot=source,
                registry=registry_snapshot,
                value_evidence=tuple(
                    ValueEvidence.model_validate(item)
                    for item in source.get("value_evidence") or ()
                ),
                issues=tuple(
                    MigrationIssue.model_validate(item)
                    for item in source.get("migration_issues") or ()
                ),
                changed=False,
            )

        self._issues = []
        self._value_evidence = []
        revision = int(source.get("revision") or source.get("current_revision") or 0)
        migrated = self._walk(source, path="$", revision=revision)
        migrated["recording_contract_version"] = MIGRATION_VERSION
        migrated["lineage_id"] = str(self.lineage_id)
        registry_snapshot = self.registry.snapshot()
        migrated["field_registry"] = registry_snapshot.model_dump(mode="json")
        migrated["value_evidence"] = [
            item.model_dump(mode="json") for item in self._value_evidence
        ]
        migrated["migration_issues"] = [item.model_dump(mode="json") for item in self._issues]
        return FlowMigrationResult(
            snapshot=migrated,
            registry=registry_snapshot,
            value_evidence=tuple(self._value_evidence),
            issues=tuple(self._issues),
            changed=True,
        )


__all__ = [
    "FlowMigrationResult",
    "FlowMigrator",
    "MIGRATION_VERSION",
    "MigrationIssue",
    "MigrationIssueKind",
]
