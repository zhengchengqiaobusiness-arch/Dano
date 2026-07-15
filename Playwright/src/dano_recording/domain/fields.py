"""Field facts, proposals, per-dimension decisions, and effective contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable

from pydantic import Field, model_validator

from dano_recording.domain._base import FrozenModel, freeze_json, new_id, utc_now
from dano_recording.domain.enums import ChoiceContract


class FieldLocation(StrEnum):
    PATH = "path"
    QUERY = "query"
    BODY = "body"
    FORM = "form"
    HEADER = "header"


class FieldDimension(StrEnum):
    # V3 canonical axes.  The legacy names below remain during revision
    # migration so existing drafts can still be rendered and republished.
    DISPLAY_NAME = "display_name"
    NAME = "name"
    BUSINESS_TYPE = "business_type"
    CLASSIFICATION = "classification"
    SOURCE_BINDING = "source_binding"
    DEFAULT_VALUE = "default_value"
    CALLER_REQUIRED = "caller_required"
    WIRE_REQUIRED = "wire_required"
    REQUIRED_CONDITIONS = "required_conditions"
    EXPOSURE = "exposure"
    ENUM_BINDING = "enum_binding"
    VALUE_PROVIDER = "value_provider"
    CHOICE_CONTRACT = "choice_contract"
    REQUIRED = "required"
    EXPOSED = "exposed"


class DecisionOrigin(StrEnum):
    USER = "user"
    DETERMINISTIC = "deterministic"
    PI = "pi"
    HEURISTIC = "heuristic"
    UNRESOLVED = "unresolved"


class ValueProviderKind(StrEnum):
    USER_INPUT = "user_input"
    PREVIOUS_RESPONSE = "previous_response"
    OPTION_SOURCE = "option_source"
    PAGE_CONTEXT = "page_context"
    REQUEST_HEADER = "request_header"
    CONSTANT = "constant"
    COMPUTED = "computed"
    UNRESOLVED = "unresolved"


class AxisOrigin(StrEnum):
    OBSERVED = "observed"
    DETERMINISTIC = "deterministic"
    PI = "pi"
    MANUAL = "manual"


class RequiredState(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class ProviderKind(StrEnum):
    CALLER = "caller"
    DEFAULT = "default"
    CONSTANT = "constant"
    RUNTIME_CONTEXT = "runtime_context"
    DEPENDENCY_RESPONSE = "dependency_response"
    DERIVED = "derived"


class SourceBindingKind(StrEnum):
    CALLER = "caller"
    DEFAULT = "default"
    CONSTANT = "constant"
    RUNTIME_CONTEXT = "runtime_context"
    DEPENDENCY_RESPONSE = "dependency_response"
    PREVIOUS_RESPONSE = "previous_response"
    DERIVED = "derived"
    UNKNOWN = "unknown"


class ConditionOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    NOT_IN = "not_in"
    EXISTS = "exists"
    AND = "and"
    OR = "or"
    NOT = "not"


class ConditionExpr(FrozenModel):
    """Serializable condition used by wire and caller required contracts."""

    operator: ConditionOperator
    field_uuid: str | None = None
    value: Any | None = None
    operands: tuple["ConditionExpr", ...] = ()

    @model_validator(mode="after")
    def _validate_shape(self) -> "ConditionExpr":
        object.__setattr__(self, "value", freeze_json(self.value))
        logical = self.operator in {
            ConditionOperator.AND,
            ConditionOperator.OR,
            ConditionOperator.NOT,
        }
        if logical:
            if self.field_uuid is not None:
                raise ValueError("logical conditions cannot reference a field directly")
            if not self.operands:
                raise ValueError("logical conditions require operands")
            if self.operator is ConditionOperator.NOT and len(self.operands) != 1:
                raise ValueError("not conditions require exactly one operand")
        elif not self.field_uuid:
            raise ValueError("leaf conditions require field_uuid")
        return self

    def evaluate(self, values: dict[str, Any]) -> bool:
        if self.operator is ConditionOperator.AND:
            return all(item.evaluate(values) for item in self.operands)
        if self.operator is ConditionOperator.OR:
            return any(item.evaluate(values) for item in self.operands)
        if self.operator is ConditionOperator.NOT:
            return not self.operands[0].evaluate(values)
        present = self.field_uuid in values
        actual = values.get(self.field_uuid or "")
        if self.operator is ConditionOperator.EXISTS:
            return present and actual is not None
        if self.operator is ConditionOperator.EQUALS:
            return actual == self.value
        if self.operator is ConditionOperator.NOT_EQUALS:
            return actual != self.value
        if self.operator is ConditionOperator.IN:
            return actual in (self.value or ())
        if self.operator is ConditionOperator.NOT_IN:
            return actual not in (self.value or ())
        return False


class ProviderBinding(FrozenModel):
    kind: ProviderKind
    runtime_resolver: str | None = None
    request_definition_id: str | None = None
    response_path: str | None = None
    value: Any | None = None
    expression: str | None = None

    @model_validator(mode="after")
    def _validate_provider(self) -> "ProviderBinding":
        object.__setattr__(self, "value", freeze_json(self.value))
        if self.kind is ProviderKind.RUNTIME_CONTEXT and not self.runtime_resolver:
            raise ValueError("runtime_context provider requires runtime_resolver")
        if self.kind is ProviderKind.DEPENDENCY_RESPONSE:
            if not self.request_definition_id or not self.response_path:
                raise ValueError(
                    "dependency_response provider requires request_definition_id and response_path"
                )
        if self.kind is ProviderKind.CONSTANT and self.value is None:
            raise ValueError("constant provider requires value")
        if self.kind is ProviderKind.DERIVED and not self.expression:
            raise ValueError("derived provider requires expression")
        return self


class SourceBinding(FrozenModel):
    """Atomic source decision; its components are never revised separately."""

    kind: SourceBindingKind
    request_definition_id: str | None = None
    request_id: str | None = None
    response_path: str | None = None
    runtime_resolver: str | None = None
    value: Any | None = None
    expression: str | None = None

    @model_validator(mode="after")
    def _validate_source(self) -> "SourceBinding":
        object.__setattr__(self, "value", freeze_json(self.value))
        if self.kind in {
            SourceBindingKind.PREVIOUS_RESPONSE,
            SourceBindingKind.DEPENDENCY_RESPONSE,
        }:
            if not (self.request_definition_id or self.request_id) or not self.response_path:
                raise ValueError("response source requires a request reference and response_path")
        if self.kind is SourceBindingKind.RUNTIME_CONTEXT and not self.runtime_resolver:
            raise ValueError("runtime_context source requires runtime_resolver")
        if self.kind is SourceBindingKind.CONSTANT and self.value is None:
            raise ValueError("constant source requires value")
        if self.kind is SourceBindingKind.DERIVED and not self.expression:
            raise ValueError("derived source requires expression")
        return self


class RequiredContract(FrozenModel):
    wire_required: RequiredState = RequiredState.UNKNOWN
    caller_required: RequiredState = RequiredState.UNKNOWN
    wire_condition: ConditionExpr | None = None
    caller_condition: ConditionExpr | None = None
    provider: ProviderBinding | None = None

    def wire_is_required(self, values: dict[str, Any] | None = None) -> bool | None:
        if self.wire_required is RequiredState.UNKNOWN:
            return None
        if self.wire_required is RequiredState.FALSE:
            return False
        return self.wire_condition.evaluate(values or {}) if self.wire_condition else True

    def caller_is_required(self, values: dict[str, Any] | None = None) -> bool | None:
        if self.caller_required is RequiredState.UNKNOWN:
            return None
        if self.caller_required is RequiredState.FALSE:
            return False
        return self.caller_condition.evaluate(values or {}) if self.caller_condition else True


class AxisDecision(FrozenModel):
    """Ownership of one field axis at one revision."""

    decision_id: str = Field(default_factory=new_id)
    axis: FieldDimension
    value: Any
    origin: AxisOrigin
    evidence_ids: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    decided_at_revision: int = Field(ge=0)
    manual_override: bool = False
    supersedes: str | None = None

    @model_validator(mode="after")
    def _validate_decision(self) -> "AxisDecision":
        object.__setattr__(self, "value", freeze_json(self.value))
        if self.origin is AxisOrigin.MANUAL and not self.manual_override:
            object.__setattr__(self, "manual_override", True)
        if self.origin is not AxisOrigin.MANUAL and self.manual_override:
            raise ValueError("only manual decisions may set manual_override")
        if self.axis is FieldDimension.SOURCE_BINDING and not isinstance(
            self.value, SourceBinding
        ):
            object.__setattr__(self, "value", SourceBinding.model_validate(self.value))
        if self.axis in {FieldDimension.CALLER_REQUIRED, FieldDimension.WIRE_REQUIRED}:
            object.__setattr__(self, "value", RequiredState(self.value))
        return self


class WireSchema(FrozenModel):
    type: str = "any"
    nullable: bool = False
    items_type: str | None = None
    sample: Any | None = None


class ValueProvider(FrozenModel):
    kind: ValueProviderKind
    source_request_id: str | None = None
    source_path: str | None = None
    expression: str | None = None
    constant: Any | None = None


class FieldFact(FrozenModel):
    field_contract_id: str
    tenant: str
    recording_id: str
    request_id: str
    location: FieldLocation
    wire_path: str
    wire_name: str
    wire_schema: WireSchema
    observed_values: tuple[Any, ...] = ()
    required_by_wire: bool = False
    evidence_ids: tuple[str, ...] = ()


class FieldProposal(FrozenModel):
    proposal_id: str = Field(default_factory=new_id)
    field_contract_id: str
    origin: DecisionOrigin
    values: dict[FieldDimension, Any]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class FieldDecision(FrozenModel):
    decision_id: str = Field(default_factory=new_id)
    field_contract_id: str
    dimension: FieldDimension
    value: Any
    origin: DecisionOrigin
    actor: str
    revision: int = Field(ge=0)
    decided_at: datetime = Field(default_factory=utc_now)


class EffectiveFieldContract(FrozenModel):
    field_contract_id: str
    request_id: str
    location: FieldLocation
    wire_path: str
    wire_name: str
    wire_schema: WireSchema
    name: str
    business_type: str
    value_provider: ValueProvider
    choice_contract: ChoiceContract | None = None
    required: bool = False
    exposed: bool = True
    origins: dict[FieldDimension, DecisionOrigin]
    unresolved_dimensions: tuple[FieldDimension, ...] = ()


_ORIGIN_PRIORITY = {
    DecisionOrigin.UNRESOLVED: 0,
    DecisionOrigin.HEURISTIC: 1,
    DecisionOrigin.PI: 2,
    DecisionOrigin.DETERMINISTIC: 3,
    DecisionOrigin.USER: 4,
}


def _default_dimensions(fact: FieldFact) -> dict[FieldDimension, tuple[Any, DecisionOrigin]]:
    return {
        # Wire name/type remain immutable on ``FieldFact``.  Their use as the
        # public business contract is only a fallback, so a grounded Pi
        # proposal may improve it while a user decision still wins forever.
        FieldDimension.NAME: (fact.wire_name, DecisionOrigin.HEURISTIC),
        FieldDimension.BUSINESS_TYPE: (fact.wire_schema.type, DecisionOrigin.HEURISTIC),
        FieldDimension.VALUE_PROVIDER: (
            ValueProvider(kind=ValueProviderKind.USER_INPUT),
            DecisionOrigin.HEURISTIC,
        ),
        FieldDimension.CHOICE_CONTRACT: (None, DecisionOrigin.UNRESOLVED),
        FieldDimension.REQUIRED: (fact.required_by_wire, DecisionOrigin.DETERMINISTIC),
        FieldDimension.EXPOSED: (True, DecisionOrigin.HEURISTIC),
    }


def resolve_field_contract(
    fact: FieldFact,
    proposals: Iterable[FieldProposal] = (),
    decisions: Iterable[FieldDecision] = (),
) -> EffectiveFieldContract:
    """Resolve each dimension independently.

    Priority is user > deterministic > Pi > heuristic > unresolved.  Ties are
    resolved by revision/time, so a later Pi turn can refine an older Pi value
    but can never overwrite a user decision on that dimension.
    """

    selected = _default_dimensions(fact)
    selected_rank: dict[FieldDimension, tuple[int, int, float]] = {
        dimension: (_ORIGIN_PRIORITY[origin], -1, 0.0)
        for dimension, (_, origin) in selected.items()
    }

    for proposal in proposals:
        if proposal.field_contract_id != fact.field_contract_id:
            continue
        for dimension, value in proposal.values.items():
            rank = (_ORIGIN_PRIORITY[proposal.origin], -1, proposal.created_at.timestamp())
            if rank > selected_rank[dimension]:
                selected[dimension] = (value, proposal.origin)
                selected_rank[dimension] = rank

    for decision in decisions:
        if decision.field_contract_id != fact.field_contract_id:
            continue
        rank = (
            _ORIGIN_PRIORITY[decision.origin],
            decision.revision,
            decision.decided_at.timestamp(),
        )
        if rank > selected_rank[decision.dimension]:
            selected[decision.dimension] = (decision.value, decision.origin)
            selected_rank[decision.dimension] = rank

    unresolved = tuple(
        dimension
        for dimension, (value, origin) in selected.items()
        if origin is DecisionOrigin.UNRESOLVED or value is None
    )
    values = {dimension: pair[0] for dimension, pair in selected.items()}
    origins = {dimension: pair[1] for dimension, pair in selected.items()}
    provider = values[FieldDimension.VALUE_PROVIDER]
    if not isinstance(provider, ValueProvider):
        provider = ValueProvider.model_validate(provider)
    choice = values[FieldDimension.CHOICE_CONTRACT]
    if choice is not None and not isinstance(choice, ChoiceContract):
        choice = ChoiceContract.model_validate(choice)
    return EffectiveFieldContract(
        field_contract_id=fact.field_contract_id,
        request_id=fact.request_id,
        location=fact.location,
        wire_path=fact.wire_path,
        wire_name=fact.wire_name,
        wire_schema=fact.wire_schema,
        name=str(values[FieldDimension.NAME]),
        business_type=str(values[FieldDimension.BUSINESS_TYPE]),
        value_provider=provider,
        choice_contract=choice,
        required=bool(values[FieldDimension.REQUIRED]),
        exposed=bool(values[FieldDimension.EXPOSED]),
        origins=origins,
        unresolved_dimensions=unresolved,
    )
