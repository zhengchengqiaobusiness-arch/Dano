from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from dano_recording.domain._base import utc_now
from dano_recording.domain.facts import FactKind, RecordingFact
from dano_recording.domain.fields import (
    DecisionOrigin,
    FieldDecision,
    FieldDimension,
    FieldFact,
    FieldLocation,
    FieldProposal,
    ValueProvider,
    ValueProviderKind,
    WireSchema,
    resolve_field_contract,
)


def test_recording_fact_is_frozen() -> None:
    fact = RecordingFact(
        tenant="tenant-a",
        recording_id="rec-a",
        sequence=1,
        kind=FactKind.PAGE,
    )
    with pytest.raises(ValidationError):
        fact.sequence = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        fact.payload["url"] = "changed"  # type: ignore[index]


def test_field_decisions_resolve_per_dimension_and_user_is_pinned() -> None:
    fact = FieldFact(
        field_contract_id="field-1",
        tenant="tenant-a",
        recording_id="rec-a",
        request_id="request-1",
        location=FieldLocation.BODY,
        wire_path="applicantId",
        wire_name="applicantId",
        wire_schema=WireSchema(type="string", sample="u-1"),
    )
    now = utc_now()
    pi_proposal = FieldProposal(
        field_contract_id="field-1",
        origin=DecisionOrigin.PI,
        confidence=0.9,
        values={
            FieldDimension.NAME: "申请人",
            FieldDimension.BUSINESS_TYPE: "employee",
            FieldDimension.VALUE_PROVIDER: ValueProvider(
                kind=ValueProviderKind.USER_INPUT
            ),
        },
        created_at=now,
    )
    user_name = FieldDecision(
        field_contract_id="field-1",
        dimension=FieldDimension.NAME,
        value="出差申请人",
        origin=DecisionOrigin.USER,
        actor="operator",
        revision=2,
        decided_at=now,
    )
    later_pi_name = FieldDecision(
        field_contract_id="field-1",
        dimension=FieldDimension.NAME,
        value="Pi later name",
        origin=DecisionOrigin.PI,
        actor="planner",
        revision=99,
        decided_at=now + timedelta(hours=1),
    )
    user_exposed = FieldDecision(
        field_contract_id="field-1",
        dimension=FieldDimension.EXPOSED,
        value=False,
        origin=DecisionOrigin.USER,
        actor="operator",
        revision=3,
    )

    contract = resolve_field_contract(
        fact,
        proposals=(pi_proposal,),
        decisions=(user_name, later_pi_name, user_exposed),
    )

    assert contract.name == "出差申请人"
    assert contract.business_type == "employee"
    assert contract.exposed is False
    assert contract.origins[FieldDimension.NAME] is DecisionOrigin.USER
    assert contract.origins[FieldDimension.BUSINESS_TYPE] is DecisionOrigin.PI
    assert contract.origins[FieldDimension.EXPOSED] is DecisionOrigin.USER


def test_deterministic_decision_beats_pi_without_locking_other_dimensions() -> None:
    fact = FieldFact(
        field_contract_id="field-2",
        tenant="tenant-a",
        recording_id="rec-a",
        request_id="request-1",
        location=FieldLocation.QUERY,
        wire_path="page",
        wire_name="page",
        wire_schema=WireSchema(type="string", sample="1"),
    )
    deterministic_type = FieldDecision(
        field_contract_id="field-2",
        dimension=FieldDimension.BUSINESS_TYPE,
        value="integer",
        origin=DecisionOrigin.DETERMINISTIC,
        actor="wire-evidence",
        revision=1,
    )
    pi = FieldProposal(
        field_contract_id="field-2",
        origin=DecisionOrigin.PI,
        values={
            FieldDimension.NAME: "页码",
            FieldDimension.BUSINESS_TYPE: "string",
        },
    )

    contract = resolve_field_contract(fact, (pi,), (deterministic_type,))
    assert contract.name == "页码"
    assert contract.business_type == "integer"
