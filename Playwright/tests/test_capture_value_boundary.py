from __future__ import annotations

import json
from uuid import uuid4

import pytest

from dano_recording.capture.ledger import FactLedger
from dano_recording.capture.input_dispatcher import InputDispatcher
from dano_recording.capture.network_observer import NetworkObserver, NetworkObserverConfig
from dano_recording.capture.response_collector import ResponseCollector
from dano_recording.evidence.dom_controls import DOMControlCollector
from dano_recording.evidence.runtime_components import RuntimeComponentCollector
from dano_recording.value_evidence import (
    ValueEvidenceFactory,
    ValueSensitivity,
    contains_plaintext,
)


class _Request:
    method = "POST"
    url = "https://example.test/api/items?userId=42&email=alice%40example.test"
    post_data = json.dumps({
        "creatorId": 42,
        "email": "alice@example.test",
        "title": "visible business value",
    })
    headers = {"content-type": "application/json"}
    resource_type = "fetch"
    frame = None

    @staticmethod
    def is_navigation_request() -> bool:
        return False


class _Response:
    status = 200
    status_text = "OK"
    url = "https://example.test/api/items"
    request = _Request()

    @staticmethod
    async def all_headers():
        return {"content-type": "application/json"}

    @staticmethod
    async def body():
        return json.dumps({
            "userId": 42,
            "email": "alice@example.test",
            "result": "visible business value",
        }).encode()


def _factory() -> ValueEvidenceFactory:
    return ValueEvidenceFactory(server_secret=b"capture-boundary-secret")


@pytest.mark.parametrize(
    ("field_name", "value", "expected"),
    [
        ("authorization", "Bearer secret-token", ValueSensitivity.CREDENTIAL),
        ("access_token", "secret-token", ValueSensitivity.CREDENTIAL),
        ("cookie", "sid=secret", ValueSensitivity.CREDENTIAL),
        ("email", "alice@example.test", ValueSensitivity.PII),
        ("phone_number", "+86 13800138000", ValueSensitivity.PII),
        ("user_id", "user-7", ValueSensitivity.IDENTITY),
        ("tenantId", "tenant-7", ValueSensitivity.IDENTITY),
        ("employee_id", "employee-7", ValueSensitivity.IDENTITY),
        ("approver_id", "approver-7", ValueSensitivity.IDENTITY),
    ],
)
def test_value_evidence_classifies_credentials_pii_and_identity_independently(
    field_name: str,
    value: str,
    expected: ValueSensitivity,
) -> None:
    assert _factory().classify(field_name=field_name, value=value) is expected


def test_network_observer_generates_evidence_before_identity_and_pii_leave_boundary() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    observer = NetworkObserver(
        ledger,
        config=NetworkObserverConfig(safe_record=False),
        value_evidence_factory=_factory(),
        recording_lineage=str(uuid4()),
    )

    fact = observer.record_request(_Request())
    payload = fact.model_dump(mode="json")
    assert not contains_plaintext(payload, "alice@example.test")
    assert "42" not in fact.url
    assert fact.request_body["creatorId"] == "{{runtime_context.current_user.id}}"
    assert contains_plaintext(payload, "visible business value")
    evidence = fact.payload["request_value_evidence"]
    assert {item["value_path"] for item in evidence} >= {
        "query.userId",
        "query.email",
        "body.creatorId",
        "body.email",
    }
    assert all(item["scoped_hmac"].startswith("hmac-sha256:") for item in evidence)


def test_observed_input_action_converts_raw_pii_to_evidence_before_fact_append() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    dispatcher = InputDispatcher(
        ledger,
        value_evidence_factory=_factory(),
        recording_lineage=str(uuid4()),
    )
    fact = dispatcher.record_observed(
        action_type="input",
        page_id="page-1",
        frame_id="frame-1",
        locator="input[name=email]",
        details={
            "event": "input",
            "name": "email",
            "inputType": "email",
            "value": "alice@example.test",
        },
    )
    payload = fact.model_dump(mode="json")
    assert not contains_plaintext(payload, "alice@example.test")
    assert fact.payload["details"]["value"] == "[REDACTED:PII]"
    evidence = fact.payload["details"]["value_evidence"]
    assert evidence[0]["value_path"] == "action.value"
    assert evidence[0]["sensitivity"] == "pii"


def test_dom_initial_pii_is_evidence_and_never_plaintext_control_or_fact() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    collector = DOMControlCollector(
        ledger,
        value_evidence_factory=_factory(),
        recording_lineage=str(uuid4()),
    )
    controls = collector.from_snapshot(
        [{
            "selector": "#email",
            "tag": "input",
            "type": "email",
            "name": "email",
            "formId": "profile-form",
            "readonly": True,
            "initialValue": "alice@example.test",
        }],
        page_id="page-1",
        frame_id="frame-1",
    )
    assert controls[0].initial_value == "[REDACTED:PII]"
    assert controls[0].readonly is True
    assert controls[0].form_id == "profile-form"
    assert controls[0].initial_value_evidence[0].sensitivity.value == "pii"
    assert not contains_plaintext(ledger.snapshot()[0].model_dump(mode="json"), "alice@example.test")


def test_person_option_collections_drop_rows_and_mark_snapshot_non_static() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    collector = DOMControlCollector(ledger)
    controls = collector.from_snapshot(
        [{
            "selector": "#approver",
            "tag": "select",
            "name": "approver",
            "label": "审批人",
            "options": [
                {"label": "Alice Zhang", "value": "user-7"},
                {"label": "alice@example.test", "value": "user-8"},
            ],
        }],
        page_id="page-1",
        frame_id="frame-1",
    )
    control = controls[0]
    assert control.options_sensitive is True
    assert control.option_count == 2
    assert control.options == ()
    assert control.options_truncated is True
    serialized = json.dumps(
        ledger.snapshot()[0].model_dump(mode="json"),
        ensure_ascii=False,
    )
    assert "Alice Zhang" not in serialized
    assert "alice@example.test" not in serialized
    assert "user-7" not in serialized


def test_runtime_person_options_are_omitted_but_business_enum_is_retained() -> None:
    collector = RuntimeComponentCollector()
    clues = collector.from_snapshot([
        {
            "framework": "react",
            "component_name": "ApproverSelect",
            "property_path": "form.approverOptions",
            "options": [
                {"name": "Alice Zhang", "id": "user-7"},
                {"name": "Bob Li", "id": "user-8"},
            ],
        },
        {
            "framework": "react",
            "component_name": "StatusSelect",
            "property_path": "form.statusOptions",
            "options": [
                {"label": "Open", "value": "open"},
                {"label": "Closed", "value": "closed"},
            ],
        },
    ])
    identity, business = clues
    assert identity.options_sensitive is True
    assert identity.option_count == 2
    assert identity.options == ()
    assert business.options_sensitive is False
    assert [(item.label, item.value) for item in business.options] == [
        ("Open", "open"),
        ("Closed", "closed"),
    ]


@pytest.mark.asyncio
async def test_response_collector_persists_only_safe_structure_and_original_value_evidence() -> None:
    ledger = FactLedger(tenant="tenant-a", recording_id="recording-a")
    collector = ResponseCollector(
        ledger,
        value_evidence_factory=_factory(),
        recording_lineage=str(uuid4()),
    )
    fact = await collector.collect(_Response(), request_id="request-1")
    payload = fact.model_dump(mode="json")
    assert not contains_plaintext(payload, "alice@example.test")
    assert contains_plaintext(payload, "visible business value")
    evidence = fact.payload["response_value_evidence"]
    assert {item["value_path"] for item in evidence} >= {
        "response.userId",
        "response.email",
    }
    assert fact.payload["body"]["userId"] == "{{runtime_context.current_user.id}}"
    assert fact.payload["body"]["email"] == "[REDACTED:PII]"
