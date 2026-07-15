"""Single deterministic field-inference pipeline.

The inference order is explicit and intentionally non-recursive.  A page
snapshot is a sample/default observation, never proof that the caller supplied
the value.  Sample, default and runtime bindings remain separate outputs.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Iterable
from uuid import UUID

from pydantic import Field, model_validator

from dano_recording.domain._base import FrozenModel, freeze_json
from dano_recording.domain.fields import (
    ConditionExpr,
    ProviderBinding,
    ProviderKind,
    RequiredContract,
    RequiredState,
    SourceBinding,
    SourceBindingKind,
)


# Backward-friendly name for callers of this module; the actual contract has a
# single owner in ``domain.fields``.
TruthValue = RequiredState


class FieldTypeOrigin(StrEnum):
    NATIVE_CONTROL = "native_control"
    ARIA_COMPONENT = "aria_component"
    USER_ACTION = "user_action"
    WIRE_SCHEMA = "wire_schema"
    JS_CONFIG = "js_config"
    PI = "pi"
    UNKNOWN = "unknown"


class FieldSourceKind(StrEnum):
    PREVIOUS_RESPONSE = "previous_response"
    USER_INPUT = "user_input"
    PAGE_DEFAULT = "page_default"
    RUNTIME_RESOLVER = "runtime_resolver"
    PAGE_ENUM = "page_enum"
    JS_DICTIONARY = "js_dictionary"
    PI = "pi"
    CONSTANT = "constant"
    DERIVED = "derived"
    UNKNOWN = "unknown"


class FieldInferenceEvidence(FrozenModel):
    """All grounded evidence for one permanent field identity.

    Permanent identity is supplied by :class:`FieldRegistry`; inference never
    derives it from a label, step order, request name, or wire path.
    """

    field_uuid: UUID
    request_id: str
    wire_path: str
    wire_name: str
    location: str

    native_control_type: str | None = None
    aria_role: str | None = None
    component_role: str | None = None
    user_action_type: str | None = None
    user_changed: bool = False
    wire_schema_type: str | None = None
    js_config_type: str | None = None
    pi_type: str | None = None

    exact_response_provider: ProviderBinding | None = None
    runtime_resolver: str | None = None
    page_enum_provider: ProviderBinding | None = None
    js_dictionary_provider: ProviderBinding | None = None
    pi_provider: ProviderBinding | None = None

    sample_value: Any | None = None
    sample_observed: bool = False
    page_initial_value: Any | None = None
    page_initial_observed: bool = False
    user_action_value: Any | None = None

    wire_required: TruthValue = TruthValue.UNKNOWN
    caller_required: TruthValue = TruthValue.UNKNOWN
    wire_condition: ConditionExpr | None = None
    caller_condition: ConditionExpr | None = None
    constant_value: Any | None = None
    constant_proven: bool = False
    derived_expression: str | None = None

    internal: bool = False
    caller_must_supply: bool = False
    classification: str = "business"
    evidence_ids: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_identity_and_freeze_values(self) -> "FieldInferenceEvidence":
        object.__setattr__(self, "sample_value", freeze_json(self.sample_value))
        object.__setattr__(self, "page_initial_value", freeze_json(self.page_initial_value))
        object.__setattr__(self, "user_action_value", freeze_json(self.user_action_value))
        object.__setattr__(self, "constant_value", freeze_json(self.constant_value))
        object.__setattr__(self, "evidence_ids", freeze_json(self.evidence_ids))
        return self


class InferredField(FrozenModel):
    field_uuid: UUID
    request_id: str
    wire_path: str
    wire_name: str
    location: str
    business_type: str
    type_origin: FieldTypeOrigin
    source_binding: SourceBinding
    source_origin: FieldSourceKind
    source_evidence_ids: tuple[str, ...] = ()
    sample_value: Any | None = None
    sample_observed: bool = False
    default_value: Any | None = None
    default_observed: bool = False
    runtime_value: ProviderBinding | None = None
    classification: str
    exposed: bool
    required: RequiredContract

    @model_validator(mode="after")
    def _freeze_values(self) -> "InferredField":
        object.__setattr__(self, "sample_value", freeze_json(self.sample_value))
        object.__setattr__(self, "default_value", freeze_json(self.default_value))
        return self


_NATIVE_TYPES = {
    "checkbox": "boolean",
    "radio": "enum",
    "number": "number",
    "range": "number",
    "date": "date",
    "datetime-local": "datetime",
    "time": "time",
    "email": "email",
    "url": "url",
    "file": "file",
    "select": "enum",
    "textarea": "string",
    "text": "string",
    "search": "string",
    "password": "secret",
}

_ROLE_TYPES = {
    "checkbox": "boolean",
    "switch": "boolean",
    "radio": "enum",
    "radiogroup": "enum",
    "combobox": "enum",
    "listbox": "enum",
    "spinbutton": "number",
    "slider": "number",
    "textbox": "string",
    "date-picker": "date",
    "datepicker": "date",
}

_ACTION_TYPES = {
    "check": "boolean",
    "uncheck": "boolean",
    "select": "enum",
    "select_option": "enum",
    "upload": "file",
    "set_date": "date",
}

_INTERNAL_NAMES = frozenset(
    {
        "gslx",
        "spr",
        "sfbt",
        "creatorid",
        "createdby",
        "tenantid",
        "userid",
        "orgid",
    }
)


def _normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str):
        return "string"
    return "unknown"


def _infer_type(evidence: FieldInferenceEvidence) -> tuple[str, FieldTypeOrigin]:
    native = (evidence.native_control_type or "").strip().casefold()
    if native in _NATIVE_TYPES:
        return _NATIVE_TYPES[native], FieldTypeOrigin.NATIVE_CONTROL
    role = (evidence.component_role or evidence.aria_role or "").strip().casefold()
    if role in _ROLE_TYPES:
        return _ROLE_TYPES[role], FieldTypeOrigin.ARIA_COMPONENT
    if "select" in role or "combo" in role:
        return "enum", FieldTypeOrigin.ARIA_COMPONENT
    if "date" in role:
        return "date", FieldTypeOrigin.ARIA_COMPONENT
    if evidence.user_changed:
        action = (evidence.user_action_type or "").strip().casefold()
        inferred = _ACTION_TYPES.get(action)
        if inferred:
            return inferred, FieldTypeOrigin.USER_ACTION
        if evidence.user_action_value is not None:
            return _value_type(evidence.user_action_value), FieldTypeOrigin.USER_ACTION
    if evidence.wire_schema_type:
        return evidence.wire_schema_type, FieldTypeOrigin.WIRE_SCHEMA
    if evidence.js_config_type:
        return evidence.js_config_type, FieldTypeOrigin.JS_CONFIG
    if evidence.pi_type:
        return evidence.pi_type, FieldTypeOrigin.PI
    return "unknown", FieldTypeOrigin.UNKNOWN


def _ids(evidence: FieldInferenceEvidence, key: str) -> tuple[str, ...]:
    return tuple(evidence.evidence_ids.get(key, ()))


def _binding_from_provider(provider: ProviderBinding) -> SourceBinding:
    if provider.kind is ProviderKind.CALLER:
        return SourceBinding(kind=SourceBindingKind.CALLER)
    if provider.kind is ProviderKind.DEFAULT:
        return SourceBinding(kind=SourceBindingKind.DEFAULT, value=provider.value)
    if provider.kind is ProviderKind.CONSTANT:
        return SourceBinding(kind=SourceBindingKind.CONSTANT, value=provider.value)
    if provider.kind is ProviderKind.RUNTIME_CONTEXT:
        return SourceBinding(
            kind=SourceBindingKind.RUNTIME_CONTEXT,
            runtime_resolver=provider.runtime_resolver,
        )
    if provider.kind is ProviderKind.DEPENDENCY_RESPONSE:
        return SourceBinding(
            kind=SourceBindingKind.PREVIOUS_RESPONSE,
            request_definition_id=provider.request_definition_id,
            response_path=provider.response_path,
        )
    if provider.kind is ProviderKind.DERIVED:
        return SourceBinding(
            kind=SourceBindingKind.DERIVED,
            expression=provider.expression,
        )
    raise ValueError(f"unsupported provider kind: {provider.kind}")


def _source(
    evidence: FieldInferenceEvidence,
) -> tuple[SourceBinding, ProviderBinding | None, FieldSourceKind, tuple[str, ...]]:
    if evidence.exact_response_provider is not None:
        if evidence.exact_response_provider.kind is not ProviderKind.DEPENDENCY_RESPONSE:
            raise ValueError("exact response binding must use dependency_response provider")
        provider = evidence.exact_response_provider
        return (
            _binding_from_provider(provider),
            provider,
            FieldSourceKind.PREVIOUS_RESPONSE,
            _ids(evidence, "exact_response"),
        )
    if evidence.user_changed or evidence.caller_must_supply:
        provider = ProviderBinding(kind=ProviderKind.CALLER)
        return (
            _binding_from_provider(provider),
            provider,
            FieldSourceKind.USER_INPUT,
            _ids(evidence, "user_action") + _ids(evidence, "caller_contract"),
        )
    if evidence.page_initial_observed:
        provider = ProviderBinding(
            kind=ProviderKind.DEFAULT,
            value=evidence.page_initial_value,
        )
        return (
            _binding_from_provider(provider),
            provider,
            FieldSourceKind.PAGE_DEFAULT,
            _ids(evidence, "page_default"),
        )
    if evidence.runtime_resolver:
        provider = ProviderBinding(
            kind=ProviderKind.RUNTIME_CONTEXT,
            runtime_resolver=evidence.runtime_resolver,
        )
        return (
            _binding_from_provider(provider),
            provider,
            FieldSourceKind.RUNTIME_RESOLVER,
            _ids(evidence, "runtime_resolver"),
        )
    if evidence.page_enum_provider is not None:
        provider = evidence.page_enum_provider
        return (
            _binding_from_provider(provider),
            provider,
            FieldSourceKind.PAGE_ENUM,
            _ids(evidence, "page_enum"),
        )
    if evidence.js_dictionary_provider is not None:
        provider = evidence.js_dictionary_provider
        return (
            _binding_from_provider(provider),
            provider,
            FieldSourceKind.JS_DICTIONARY,
            _ids(evidence, "js_dictionary"),
        )
    if evidence.constant_proven and evidence.constant_value is not None:
        provider = ProviderBinding(
            kind=ProviderKind.CONSTANT,
            value=evidence.constant_value,
        )
        return (
            _binding_from_provider(provider),
            provider,
            FieldSourceKind.CONSTANT,
            _ids(evidence, "constant"),
        )
    if evidence.derived_expression and _ids(evidence, "derived"):
        provider = ProviderBinding(
            kind=ProviderKind.DERIVED,
            expression=evidence.derived_expression,
        )
        return (
            _binding_from_provider(provider),
            provider,
            FieldSourceKind.DERIVED,
            _ids(evidence, "derived"),
        )
    if evidence.pi_provider is not None:
        provider = evidence.pi_provider
        return (
            _binding_from_provider(provider),
            provider,
            FieldSourceKind.PI,
            _ids(evidence, "pi"),
        )
    return (
        SourceBinding(kind=SourceBindingKind.UNKNOWN),
        None,
        FieldSourceKind.UNKNOWN,
        (),
    )


def _required_contract(
    evidence: FieldInferenceEvidence,
    provider: ProviderBinding | None,
) -> RequiredContract:
    caller_required = evidence.caller_required
    if caller_required is RequiredState.UNKNOWN:
        if evidence.wire_required is RequiredState.FALSE:
            caller_required = RequiredState.FALSE
        elif evidence.wire_required is RequiredState.TRUE:
            if provider is not None and provider.kind is ProviderKind.CALLER:
                caller_required = RequiredState.TRUE
            elif provider is not None:
                caller_required = RequiredState.FALSE
    return RequiredContract(
        wire_required=evidence.wire_required,
        caller_required=caller_required,
        wire_condition=evidence.wire_condition,
        caller_condition=evidence.caller_condition,
        provider=provider,
    )


def infer_field(evidence: FieldInferenceEvidence) -> InferredField:
    business_type, type_origin = _infer_type(evidence)
    source, provider, source_origin, source_evidence_ids = _source(evidence)
    internal = evidence.internal or _normalise_name(evidence.wire_name) in _INTERNAL_NAMES
    caller_grounded = source_origin is FieldSourceKind.USER_INPUT or evidence.caller_must_supply
    automatic_internal = source_origin in {
        FieldSourceKind.PREVIOUS_RESPONSE,
        FieldSourceKind.RUNTIME_RESOLVER,
        FieldSourceKind.CONSTANT,
        FieldSourceKind.DERIVED,
    }
    exposed = caller_grounded or (not internal and not automatic_internal)
    if internal and not caller_grounded and provider is not None and provider.kind is ProviderKind.CALLER:
        provider = None
        source = SourceBinding(kind=SourceBindingKind.UNKNOWN)
        source_origin = FieldSourceKind.UNKNOWN
        source_evidence_ids = ()
    runtime_value = (
        provider
        if provider is not None
        and provider.kind
        in {
            ProviderKind.RUNTIME_CONTEXT,
            ProviderKind.DEPENDENCY_RESPONSE,
            ProviderKind.DERIVED,
        }
        else None
    )
    return InferredField(
        field_uuid=evidence.field_uuid,
        request_id=evidence.request_id,
        wire_path=evidence.wire_path,
        wire_name=evidence.wire_name,
        location=evidence.location,
        business_type=business_type,
        type_origin=type_origin,
        source_binding=source,
        source_origin=source_origin,
        source_evidence_ids=source_evidence_ids,
        sample_value=evidence.sample_value,
        sample_observed=evidence.sample_observed,
        default_value=evidence.page_initial_value,
        default_observed=evidence.page_initial_observed,
        runtime_value=runtime_value,
        classification=evidence.classification,
        exposed=exposed,
        required=_required_contract(evidence, provider),
    )


def infer_fields(evidence: Iterable[FieldInferenceEvidence]) -> tuple[InferredField, ...]:
    """Infer each binding independently; labels never merge field identities.

    Multiple bindings may intentionally share one ``field_uuid``.  The
    canonical field registry owns that grouping; inference must not reject or
    merge those bindings based on names or paths.
    """

    return tuple(infer_field(item) for item in evidence)


__all__ = [
    "FieldInferenceEvidence",
    "FieldSourceKind",
    "FieldTypeOrigin",
    "InferredField",
    "ProviderBinding",
    "ProviderKind",
    "RequiredContract",
    "SourceBinding",
    "TruthValue",
    "infer_field",
    "infer_fields",
]
