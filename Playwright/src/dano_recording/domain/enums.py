"""Choice and enumeration evidence models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from dano_recording.domain._base import FrozenModel, freeze_json


_SECRET_KEYS = {
    "authorization",
    "auth",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "secret",
    "secrets",
    "session",
    "token",
    "tokens",
    "access_token",
    "refresh_token",
    "api_key",
}


def _is_runtime_reference(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("secret_ref") or value.get("runtime_resolver"))
    return isinstance(value, str) and value.startswith(
        ("{{", "$", "[REDACTED", "credential_store.", "runtime_context.", "secret://")
    )


def _assert_no_secret_literal(value: Any, *, inherited_key: str | None = None) -> None:
    if _is_runtime_reference(value):
        return
    if isinstance(value, dict):
        pair_name = value.get("name", value.get("key"))
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            effective = (
                str(pair_name).strip().lower().replace("-", "_")
                if pair_name is not None and key in {"value", "header_value"}
                else key
            )
            _assert_no_secret_literal(item, inherited_key=effective)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _assert_no_secret_literal(item, inherited_key=inherited_key)
        return
    sensitive_key = bool(
        inherited_key
        and (
            inherited_key in _SECRET_KEYS
            or inherited_key.endswith(
                ("_password", "_secret", "_token", "_cookie", "_api_key")
            )
        )
    )
    if sensitive_key and value is not None:
        raise ValueError(
            f"request_template contains plaintext for sensitive key {inherited_key!r}"
        )


class ChoiceEvidenceSource(StrEnum):
    WIRE_SELECTION = "wire_selection"
    OPTION_ENDPOINT = "option_endpoint"
    RUNTIME_COMPONENT = "runtime_component"
    NATIVE_SELECT = "native_select"
    DOM_OVERLAY = "dom_overlay"
    SOURCEMAP = "sourcemap"
    SCRIPT_STATIC = "script_static"
    PI_SUGGESTION = "pi_suggestion"


class EvidenceCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class SnapshotCoverageKind(StrEnum):
    NATIVE_LOADED = "native_loaded"
    VISIBLE_WINDOW = "visible_window"
    API_PAGE = "api_page"
    LOADED_PAGES = "loaded_pages"
    STATIC_BUNDLE = "static_bundle"
    UNKNOWN = "unknown"


class MappingCoverage(StrEnum):
    SELECTED_ONLY = "selected_only"
    OBSERVED_SET = "observed_set"
    STATIC_DOMAIN = "static_domain"
    RUNTIME_RESOLVABLE = "runtime_resolvable"
    UNKNOWN = "unknown"


class SnapshotCoverage(FrozenModel):
    kind: SnapshotCoverageKind = SnapshotCoverageKind.UNKNOWN
    observed_count: int = Field(default=0, ge=0)
    truncated: bool = False


class SourceScope(FrozenModel):
    """The access/query scope in which enum evidence was observed."""

    tenant: str | None = None
    current_user: str | None = None
    permission_scope: tuple[str, ...] = ()
    query_filters: dict[str, Any] = Field(default_factory=dict)
    captured_page: str | None = None

    @model_validator(mode="after")
    def _freeze_filters(self) -> "SourceScope":
        object.__setattr__(self, "query_filters", freeze_json(self.query_filters))
        return self


class PaginationContract(FrozenModel):
    page_param: str | None = None
    page_size_param: str | None = None
    cursor_param: str | None = None
    next_cursor_path: str | None = None
    records_path: str | None = None
    start_page: int = 1
    page_size: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_mode(self) -> "PaginationContract":
        numbered = bool(self.page_param)
        cursor = bool(self.cursor_param and self.next_cursor_path)
        if not numbered and not cursor:
            raise ValueError("pagination requires numbered pages or a cursor contract")
        return self


class EnumSourceQuery(FrozenModel):
    request_definition_id: str
    method: str
    request_template: dict[str, Any]
    label_path: str
    value_path: str
    exact_lookup: bool
    search_param: str | None = None
    pagination: PaginationContract | None = None

    @field_validator("method")
    @classmethod
    def _method_upper(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("method must not be blank")
        return value

    @model_validator(mode="after")
    def _freeze_template(self) -> "EnumSourceQuery":
        _assert_no_secret_literal(self.request_template)
        object.__setattr__(self, "request_template", freeze_json(self.request_template))
        if not self.exact_lookup and not self.search_param and self.pagination is None:
            raise ValueError(
                "a non-exact enum query requires search_param or pagination"
            )
        return self


class EnumEvidence(FrozenModel):
    """Separates pair verification from claims about domain coverage."""

    selected_pair_verified: bool = False
    observed_mapping_complete: bool = False
    snapshot_coverage: SnapshotCoverage = Field(default_factory=SnapshotCoverage)
    mapping_coverage: MappingCoverage = MappingCoverage.UNKNOWN
    source_scope: SourceScope = Field(default_factory=SourceScope)
    source_query: EnumSourceQuery | None = None
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_coverage_claim(self) -> "EnumEvidence":
        if (
            self.mapping_coverage is MappingCoverage.RUNTIME_RESOLVABLE
            and self.source_query is None
        ):
            raise ValueError("runtime_resolvable coverage requires source_query")
        if self.mapping_coverage is MappingCoverage.STATIC_DOMAIN:
            if self.snapshot_coverage.kind not in {
                SnapshotCoverageKind.NATIVE_LOADED,
                SnapshotCoverageKind.STATIC_BUNDLE,
            } or self.snapshot_coverage.truncated:
                raise ValueError("static_domain requires a complete native/static snapshot")
            if not self.observed_mapping_complete or self.snapshot_coverage.observed_count == 0:
                raise ValueError(
                    "static_domain requires non-empty, observed-complete mapping evidence"
                )
        return self


class ChoiceOption(FrozenModel):
    label: str
    value: Any
    disabled: bool = False


class ChoiceEvidence(FrozenModel):
    evidence_id: str
    field_contract_id: str
    source_kind: ChoiceEvidenceSource
    options: tuple[ChoiceOption, ...] = ()
    control_id: str | None = None
    request_id: str | None = None
    wire_path: str | None = None
    script_url: str | None = None
    script_hash: str | None = None
    symbol_path: str | None = None
    completeness: EvidenceCompleteness = EvidenceCompleteness.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    proofs: tuple[str, ...] = ()


class ChoiceContract(FrozenModel):
    multiple: bool = False
    options: tuple[ChoiceOption, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    completeness: EvidenceCompleteness = EvidenceCompleteness.UNKNOWN
    enum_evidence: EnumEvidence | None = None
