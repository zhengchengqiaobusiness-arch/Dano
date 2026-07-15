from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from dano_recording.analysis.field_resolver import extract_field_facts
from dano_recording.domain.operations import CompiledRequest, RequestDisposition
from dano_recording.executability import check_executability
from dano_recording.publish.asset_projection import project_asset
from dano_recording.publish.review import ReviewCollector
from dano_recording.publish.service import RecordingPublishService, _deterministic_validation
from dano_recording.runtime import (
    execute_recording_capability,
    execute_recording_workflow,
    list_recording_field_options,
)
from dano_recording.runtime.request_builder import build_request, render
from dano_recording.runtime.safety import RuntimePolicy


class _Response:
    def __init__(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = str(body)

    def json(self) -> Any:
        return self._body


class _Sender:
    def __init__(self, responses: dict[str, _Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        self.calls.append((method, url, kwargs))
        return self.responses[urlsplit(url).path]


def _snapshot() -> dict[str, Any]:
    return {
        "recording_id": "rec-7",
        "tenant": "tenant-a",
        "subsystem": "A-OA",
        "revision": 7,
        "start_url": "https://oa.example/workbench",
        "recording_mode": "record_only",
        "action": "submit_report",
        "title": "提交报告",
        "steps": [{
            "step_id": "submit",
            "step_uuid": "11111111-1111-4111-8111-111111111111",
            "request_definition_id": "33333333-3333-4333-8333-333333333333",
            "request_id": "req-submit",
            "method": "POST",
            "url": "https://oa.example/api/report?dry=recorded-sample",
            "path": "/api/report",
            "headers": {
                "Content-Type": "application/json",
                "Authorization": "Bearer captured-secret",
                "X-Trace": "recording-v3",
            },
            "query_template": {"dry": "recorded-sample"},
            "body_template": {"report": {"text": "recorded private text", "notify": True}},
            "params": [
                {
                    "field_uuid": "fld-dry",
                    "field_id": "fld-dry",
                    "request_id": "req-submit",
                    "location": "query",
                    "path": "dry",
                    "key": "dry_run",
                    "type": "boolean",
                    "wire_type": "string",
                    "required": False,
                    "exposed_to_caller": True,
                    "source_kind": "user_input",
                    "source": {"kind": "user_input"},
                    "value": "recorded-sample",
                },
                {
                    "field_uuid": "fld-text",
                    "field_id": "fld-text",
                    "request_id": "req-submit",
                    "location": "body",
                    "path": "report.text",
                    "key": "report_text",
                    "description": "报告正文",
                    "type": "string",
                    "wire_type": "string",
                    "required": True,
                    "exposed_to_caller": True,
                    "source_kind": "user_input",
                    "source": {"kind": "user_input"},
                    "value": "recorded private text",
                    "enum_options": [
                        {"label": "日报", "value": "daily"},
                        {"label": "周报", "value": "weekly"},
                    ],
                    "enum_binding": {
                        "selected_pair_verified": True,
                        "observed_mapping_complete": True,
                        "mapping_coverage": "static_domain",
                        "snapshot_coverage": {
                            "kind": "native_loaded",
                            "observed_count": 2,
                            "truncated": False,
                        },
                        "source_scope": {},
                        "evidence_ids": ["native-select-proof"],
                    },
                },
                {
                    "field_uuid": "fld-notify",
                    "field_id": "fld-notify",
                    "request_id": "req-submit",
                    "location": "body",
                    "path": "report.notify",
                    "key": "notify",
                    "type": "boolean",
                    "wire_type": "boolean",
                    "required": False,
                    "exposed_to_caller": False,
                    "category": "system_const",
                    "source_kind": "constant",
                    "source": {"kind": "constant", "constant": True},
                    "value": True,
                },
            ],
            "response_json": {"private": "must not be published"},
            "response_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
            },
            "requires_human_confirm": True,
            "risk_level": "L3",
        }],
        "capabilities": [{
            "capability_id": "cap-submit",
            "capability_uuid": "22222222-2222-4222-8222-222222222222",
            "name": "submit_report",
            "title": "提交报告",
            "kind": "operation",
            "step_ids": ["submit"],
            "step_uuids": ["11111111-1111-4111-8111-111111111111"],
            "request_refs": [{
                "request_id": "req-submit",
                "step_id": "submit",
                "step_uuid": "11111111-1111-4111-8111-111111111111",
                "usage": "execute",
            }],
            "risk_level": "L3",
            "requires_human_confirm": True,
            "execution_enabled": True,
            "output_schema": {"type": "object"},
        }],
        "request_facts": {"requests": [{
            "request_definition_id": "33333333-3333-4333-8333-333333333333",
            "request_id": "req-submit",
            "method": "POST",
            "path": "/api/report",
            "disposition": "materialized",
        }]},
    }


def test_projection_compiles_complete_fields_and_never_publishes_samples_or_credentials() -> None:
    candidate = project_asset(_snapshot(), revision=7)

    assert candidate.body["recording_engine"] == "playwright_v3"
    api = candidate.body["api_request"]
    assert api["recording_engine"] == "playwright_v3"
    step = api["steps"][0]
    assert step["url"] == "https://oa.example/api/report"
    assert step["query_template"] == {"dry": "{{input.dry_run}}"}
    assert step["body_template"] == {
        "report": {"text": "{{input.report_text}}", "notify": True}
    }
    assert "Authorization" not in step["headers"]
    assert step["headers"]["X-Trace"] == "recording-v3"
    assert "response_json" not in step
    assert candidate.body["user_fields"] == ["dry_run", "report_text"]
    assert candidate.body["required_fields"] == ["report_text"]
    assert candidate.body["field_docs"]["report_text"] == "报告正文"
    assert candidate.body["risk_level"] == "L3"
    contracts = api["field_contracts"]
    assert {item["field_contract_id"] for item in contracts} == {
        "fld-dry", "fld-text", "fld-notify"
    }
    assert next(item for item in contracts if item["field_contract_id"] == "fld-text")[
        "choice_contract"
    ]["typed_options"][0] == {"label": "日报", "value": "daily"}
    encoded = str(candidate.body)
    assert "recorded private text" not in encoded
    assert "captured-secret" not in encoded
    assert "must not be published" not in encoded


def test_projection_does_not_coerce_string_policy_flags_to_true() -> None:
    snapshot = _snapshot()
    snapshot["compiled_api_request"] = {"allow_http": "true"}
    snapshot["capabilities"][0]["execution_enabled"] = "true"
    snapshot["capabilities"][0]["confirmed"] = "true"

    candidate = project_asset(snapshot, revision=7)
    capability = candidate.body["api_request"]["capabilities"][0]

    assert candidate.body["api_request"]["allow_http"] is False
    assert capability["execution_enabled"] is False
    assert capability["confirmed"] is False


def test_projection_requires_canonical_field_step_and_request_definition_identity() -> None:
    candidate = project_asset(_snapshot(), revision=7)
    binding = candidate.body["api_request"]["field_contracts"][0]
    assert binding["step_uuid"] == "11111111-1111-4111-8111-111111111111"
    assert binding["request_definition_id"] == "33333333-3333-4333-8333-333333333333"
    assert binding["request_id"] == "req-submit"

    missing_step_uuid = _snapshot()
    missing_step_uuid["steps"][0].pop("step_uuid")
    with pytest.raises(ValueError, match="canonical step_uuid"):
        project_asset(missing_step_uuid, revision=7)

    missing_definition = _snapshot()
    missing_definition["steps"][0].pop("request_definition_id")
    with pytest.raises(ValueError, match="canonical request_definition_id"):
        project_asset(missing_definition, revision=7)

    mismatched_step = _snapshot()
    mismatched_step["steps"][0]["params"][0]["step_uuid"] = "other-step-uuid"
    with pytest.raises(ValueError, match="field/step canonical identity mismatch"):
        project_asset(mismatched_step, revision=7)

    mismatched_definition = _snapshot()
    mismatched_definition["steps"][0]["params"][0][
        "request_definition_id"
    ] = "other-request-definition"
    with pytest.raises(ValueError, match="field/request definition identity mismatch"):
        project_asset(mismatched_definition, revision=7)


def _dependency_snapshot() -> dict[str, Any]:
    snapshot = _snapshot()
    source = {
        "step_id": "lookup",
        "step_uuid": "44444444-4444-4444-8444-444444444444",
        "request_definition_id": "55555555-5555-4555-8555-555555555555",
        "request_id": "req-lookup",
        "method": "GET",
        "url": "https://oa.example/api/lookup",
        "path": "/api/lookup",
        "headers": {},
        "params": [],
        "response_schema": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                }
            },
        },
        "risk_level": "L1",
    }
    snapshot["steps"].insert(0, source)
    text_field = snapshot["steps"][1]["params"][1]
    text_field.update({
        "required": False,
        "exposed_to_caller": False,
        "source_kind": "dependency_response",
        "source": {
            "kind": "dependency_response",
            "request_definition_id": source["request_definition_id"],
            "response_path": "data.id",
        },
    })
    capability = snapshot["capabilities"][0]
    capability["step_ids"] = ["lookup", "submit"]
    capability["step_uuids"] = [source["step_uuid"], snapshot["steps"][1]["step_uuid"]]
    capability["request_refs"].insert(0, {
        "request_id": source["request_id"],
        "request_definition_id": source["request_definition_id"],
        "step_id": source["step_id"],
        "step_uuid": source["step_uuid"],
        "usage": "execute",
    })
    snapshot["request_facts"]["requests"].insert(0, {
        "request_definition_id": source["request_definition_id"],
        "request_id": source["request_id"],
        "method": "GET",
        "path": "/api/lookup",
        "disposition": "materialized",
    })
    return snapshot


def test_dependency_provider_projects_canonical_source_uuid_and_rejects_alias_conflicts() -> None:
    snapshot = _dependency_snapshot()

    candidate = project_asset(snapshot, revision=7)
    submit = next(
        step for step in candidate.body["api_request"]["steps"]
        if step["step_id"] == "submit"
    )
    assert submit["body_template"]["report"]["text"] == (
        "{{steps.44444444-4444-4444-8444-444444444444.data.id}}"
    )

    conflicting = _dependency_snapshot()
    conflicting["steps"][1]["params"][1]["source"]["source_step_id"] = "submit"
    with pytest.raises(ValueError, match="dependency source identity mismatch"):
        project_asset(conflicting, revision=7)


def test_executability_rejects_dependency_provider_without_canonical_request_identity() -> None:
    snapshot = _dependency_snapshot()
    provider = snapshot["steps"][1]["params"][1]["source"]
    provider.pop("request_definition_id")
    provider["source_step_id"] = "lookup"

    report = check_executability(snapshot)

    assert any(
        item["code"] == "dependency_source_identity_missing"
        for item in report["contract_faults"]
    )


def _dynamic_enum_snapshot() -> dict[str, Any]:
    snapshot = _dependency_snapshot()
    lookup, submit = snapshot["steps"]
    field = submit["params"][1]
    field.update({
        "source_kind": "user_input",
        "source": {"kind": "user_input"},
        "exposed_to_caller": True,
        "enum_options": [],
        "enum_binding": {
            "selected_pair_verified": False,
            "observed_mapping_complete": False,
            "mapping_coverage": "runtime_resolvable",
            "snapshot_coverage": {
                "kind": "unknown",
                "observed_count": 0,
                "truncated": False,
            },
            "source_scope": {},
            "evidence_ids": ["enum-request-proof"],
            "source_query": {
                "request_definition_id": lookup["request_definition_id"],
                "method": "GET",
                "request_template": {},
                "label_path": "data.label",
                "value_path": "data.id",
            },
        },
    })
    capability = snapshot["capabilities"][0]
    capability["step_ids"] = [submit["step_id"]]
    capability["step_uuids"] = [submit["step_uuid"]]
    capability["request_refs"][0]["usage"] = "option_source"
    return snapshot


def test_dynamic_enum_projection_binds_source_query_to_canonical_step_uuid() -> None:
    snapshot = _dynamic_enum_snapshot()

    candidate = project_asset(snapshot, revision=7)
    contract = candidate.body["api_request"]["capabilities"][0][
        "choice_contracts"
    ][0]
    assert contract["source_step_id"] == "lookup"
    assert contract["source_step_uuid"] == "44444444-4444-4444-8444-444444444444"

    conflicting = _dynamic_enum_snapshot()
    conflicting["steps"][1]["params"][1]["source"]["source_step_id"] = "submit"
    with pytest.raises(ValueError, match="enum source identity mismatch"):
        project_asset(conflicting, revision=7)


def _read_collection_snapshot(item_properties: dict[str, Any]) -> dict[str, Any]:
    snapshot = _snapshot()
    snapshot["steps"][0].update({
        "method": "GET",
        "risk_level": "L1",
        "requires_human_confirm": False,
    })
    capability = snapshot["capabilities"][0]
    capability.update({
        "name": "query_records",
        "kind": "operation",
        "risk_level": "L1",
        "requires_human_confirm": False,
        "output_schema": {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": item_properties,
                    },
                },
            },
        },
    })
    return snapshot


def test_read_only_record_collection_publishes_only_a_real_stable_id_marker() -> None:
    candidate = project_asset(
        _read_collection_snapshot({
            "id": {"type": "string"},
            "name": {"type": "string"},
        }),
        revision=7,
    )
    capability = candidate.body["api_request"]["capabilities"][0]

    assert capability["read_only"] is True
    assert capability["output_schema"]["properties"]["records"][
        "x-record-id-field"
    ] == "id"
    assert not any(
        item["code"].startswith("query_record_")
        for item in candidate.body["contract_faults"]
    )


def test_read_only_record_identity_may_be_grounded_by_capability_relation() -> None:
    snapshot = _read_collection_snapshot({
        "caseKey": {"type": "string"},
        "status": {"type": "string"},
    })
    snapshot["capability_relations"] = [{
        "type": "record_reference",
        "from_capability": "query_records",
        "from_output": "records[].caseKey",
        "to_capability": "submit_case",
        "to_input": "caseKey",
    }]

    candidate = project_asset(snapshot, revision=7)
    records = candidate.body["api_request"]["capabilities"][0]["output_schema"][
        "properties"
    ]["records"]

    assert records["x-record-id-field"] == "caseKey"


def test_read_only_record_collection_without_stable_id_is_not_verified() -> None:
    candidate = project_asset(
        _read_collection_snapshot({"status": {"type": "string"}}),
        revision=7,
    )

    assert candidate.body["verification_status"] == "unverified"
    assert candidate.body["direct_call_enabled"] is False
    assert "query_record_identity_missing" in {
        item["code"] for item in candidate.body["contract_faults"]
    }


def test_read_only_record_collection_without_item_shape_is_not_verified() -> None:
    candidate = project_asset(_read_collection_snapshot({}), revision=7)

    assert candidate.body["verification_status"] == "unverified"
    assert "query_record_schema_missing" in {
        item["code"] for item in candidate.body["contract_faults"]
    }


def test_projection_preserves_query_only_bodyless_write() -> None:
    snapshot = _snapshot()
    step = snapshot["steps"][0]
    step["body_template"] = None
    step["params"] = [step["params"][0]]

    candidate = project_asset(snapshot, revision=7)
    compiled = candidate.body["api_request"]["steps"][0]

    assert compiled["method"] == "POST"
    assert compiled["body_template"] is None
    assert compiled["query_template"] == {"dry": "{{input.dry_run}}"}
    assert compiled["requires_confirmation"] is True


def test_projection_does_not_expose_fields_from_non_runtime_captured_requests() -> None:
    snapshot = _snapshot()
    snapshot["effective_fields"] = [{
        "field_uuid": "fld-option-filter-uuid",
        "field_contract_id": "fld-option-filter",
        "request_id": "req-options",
        "location": "query",
        "wire_path": "keyword",
        "wire_name": "keyword",
        "wire_schema": {"type": "string"},
        "name": "option_keyword",
        "business_type": "string",
        "value_provider": {"kind": "user_input"},
        "required": False,
        "exposed": True,
    }]

    candidate = project_asset(snapshot, revision=7)

    assert "option_keyword" not in candidate.body["user_fields"]
    assert all(
        item["field_contract_id"] != "fld-option-filter"
        for item in candidate.body["api_request"]["field_contracts"]
    )


def test_template_lookup_preserves_public_names_with_dots_spaces_and_brackets() -> None:
    value = render(
        '{{input["report.text ] label"]}}',
        {"input": {"report.text ] label": "kept"}},
    )

    assert value == "kept"


def test_runtime_origin_binding_rejects_https_downgrade_and_port_change() -> None:
    policy = RuntimePolicy(recorded_origin="https://oa.example", allow_http=True)

    with pytest.raises(ValueError, match="cannot change origin"):
        policy.resolve_url("https://oa.example", "http://oa.example/command")
    with pytest.raises(ValueError, match="cannot change origin"):
        policy.resolve_url("https://oa.example", "https://oa.example:444/command")
    assert policy.resolve_url("https://oa.example", "https://oa.example:443/command").endswith("/command")


def test_runtime_path_fields_are_percent_encoded_not_route_injection() -> None:
    request = build_request(
        {"step_id": "detail", "method": "GET", "url": "https://oa.example/items/{{input.id}}"},
        fields={"id": "../admin?delete=true"}, outputs={}, base_url="https://oa.example",
        policy=RuntimePolicy(recorded_origin="https://oa.example"),
    )

    assert request.url == "https://oa.example/items/..%2Fadmin%3Fdelete%3Dtrue"


def test_projection_rejects_uncontracted_literal_body_and_secret_start_url() -> None:
    snapshot = _snapshot()
    snapshot["steps"][0]["params"] = [snapshot["steps"][0]["params"][0]]
    with pytest.raises(ValueError, match="uncontracted literal body"):
        project_asset(snapshot, revision=7)

    snapshot = _snapshot()
    snapshot["start_url"] = "https://oa.example/workbench?access_token=plain-secret"
    with pytest.raises(ValueError, match="secret query parameter"):
        project_asset(snapshot, revision=7)


def test_publish_boundary_redacts_human_pii_and_rejects_credential_text() -> None:
    snapshot = _snapshot()
    snapshot["title"] = "提交 alice@example.com 的报告，联系电话 +86 13800138000"
    snapshot["goal"] = {
        "intent": "notify owner@example.com",
        "success_criteria": ["电话 13900139000 已通知"],
    }
    snapshot["steps"][0]["params"][1]["description"] = "联系人 pii@example.com"

    candidate = project_asset(snapshot, revision=7)
    encoded = str(candidate.body)

    for plaintext in (
        "alice@example.com", "13800138000", "owner@example.com",
        "13900139000", "pii@example.com",
    ):
        assert plaintext not in encoded
    assert "[REDACTED]" in encoded

    credential = _snapshot()
    credential["title"] = "failure access_token=top-secret-token"
    with pytest.raises(ValueError, match="credential text"):
        project_asset(credential, revision=7)

    unsafe_path = _snapshot()
    unsafe_path["steps"][0]["url"] = "https://oa.example/users/alice@example.com/report"
    with pytest.raises(ValueError, match="unsanitized PII"):
        project_asset(unsafe_path, revision=7)


def test_validation_reads_request_fact_object_not_dictionary_keys() -> None:
    snapshot = _snapshot()
    candidate = project_asset(snapshot, revision=7)

    report = _deterministic_validation(snapshot, candidate.body)

    assert report["passed"] is True
    assert report["captured_requests"] == 1
    assert report["materialized_requests"] == 1


async def test_runtime_requires_confirmation_and_keeps_bodyless_query_command() -> None:
    sender = _Sender({"/command": _Response(204, None, {"Set-Cookie": "rotated=secret", "X-Id": "1"})})
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [{
            "step_id": "command",
            "step_uuid": "step-command-uuid",
            "method": "POST",
            "url": "https://oa.example/command",
            "query_template": {"id": "{{input.id}}"},
            "body_template": None,
            "headers": {"Authorization": "Bearer asset-secret", "X-Trace": "v3"},
        }],
        "capabilities": [{
            "capability_uuid": "cap-command-uuid",
            "name": "command",
            "step_ids": ["command"],
            "step_uuids": ["step-command-uuid"],
            "risk_level": "L3",
            "execution_enabled": True,
        }],
    }

    blocked = await execute_recording_capability(
        api, {"id": 42}, capability="command", confirm=False,
        base_url="https://oa.example", sender=sender,
    )
    assert blocked["stage"] == "confirmation_required"
    assert sender.calls == []

    result = await execute_recording_capability(
        api, {"id": 42}, capability="command", confirm=True,
        base_url="https://oa.example", sender=sender,
        credential_headers={"Authorization": "Bearer trusted"},
    )
    assert result["ok"] is True
    method, url, kwargs = sender.calls[0]
    assert method == "POST"
    assert parse_qs(urlsplit(url).query) == {"id": ["42"]}
    assert "json" not in kwargs and "data" not in kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer trusted"
    assert result["results"][0]["headers"] == {"X-Id": "1"}


async def test_runtime_resolves_live_options_and_multistep_outputs() -> None:
    sender = _Sender({
        "/types": _Response(200, [{"label": "日报", "value": 2}]),
        "/create": _Response(200, {"data": {"id": "report-9"}}),
        "/confirm": _Response(200, {"confirmed": True}),
    })
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [
                {
                    "step_id": "types",
                    "step_uuid": "step-types-uuid",
                "request_definition_id": "request-types",
                "method": "GET",
                "url": "https://oa.example/types",
            },
                {
                    "step_id": "create", "step_uuid": "step-create-uuid",
                    "method": "POST", "url": "https://oa.example/create",
                "body_template": {"type": "{{input.report_type}}"},
            },
                {
                    "step_id": "confirm", "step_uuid": "step-confirm-uuid",
                    "method": "POST", "url": "https://oa.example/confirm",
                "body_template": {"id": "{{steps.create.data.id}}"},
                "success_rule": {"path": "confirmed", "equals": True},
            },
        ],
        "capabilities": [{
            "capability_uuid": "cap-submit-report-uuid",
            "name": "submit_report",
            "step_ids": ["create", "confirm"],
            "step_uuids": ["step-create-uuid", "step-confirm-uuid"],
            "request_refs": [{
                "step_id": "types", "step_uuid": "step-types-uuid",
                "usage": "option_source",
            }],
            "choice_contracts": [{
                "public_name": "report_type",
                "source_step_id": "types",
                "source_step_uuid": "step-types-uuid",
                "value_path": "value",
                "label_path": "label",
                "enum_evidence": {
                    "mapping_coverage": "runtime_resolvable",
                    "snapshot_coverage": {
                        "kind": "unknown", "observed_count": 0, "truncated": False,
                    },
                    "source_scope": {},
                    "source_query": {
                        "request_definition_id": "request-types",
                        "method": "GET",
                        "request_template": {},
                        "label_path": "label",
                        "value_path": "value",
                        "exact_lookup": True,
                    },
                },
            }],
            "risk_level": "L3",
            "execution_enabled": True,
        }],
    }

    result = await execute_recording_capability(
        api, {"report_type": "日报"}, capability="submit_report", confirm=True,
        base_url="https://oa.example", sender=sender,
    )

    assert result["ok"] is True
    assert [urlsplit(call[1]).path for call in sender.calls] == ["/types", "/create", "/confirm"]
    assert sender.calls[1][2]["json"] == {"type": 2}
    assert sender.calls[2][2]["json"] == {"id": "report-9"}
    assert result["option_sources"] == [{
        "field": "report_type",
        "source_step_id": "types",
        "count": 1,
        "pages_fetched": 1,
        "matched_by": ["runtime_label"],
    }]

    second_sender = _Sender(sender.responses)
    unknown = await execute_recording_capability(
        api, {"report_type": "月报"}, capability="submit_report", confirm=True,
        base_url="https://oa.example", sender=second_sender,
    )
    assert unknown["stage"] == "invalid_input"
    assert [urlsplit(call[1]).path for call in second_sender.calls] == ["/types"]


async def test_runtime_dry_run_uses_schema_examples_for_dependent_steps() -> None:
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [
                {
                    "step_id": "first", "step_uuid": "step-first-uuid",
                    "method": "GET", "url": "https://oa.example/first",
                "response_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
            },
                {
                    "step_id": "second", "step_uuid": "step-second-uuid",
                    "method": "POST", "url": "https://oa.example/second",
                "body_template": {"id": "{{steps.first.id}}"},
            },
        ],
    }

    result = await execute_recording_workflow(
        api, {}, base_url="https://oa.example", send=False,
    )

    assert result["ok"] is True
    assert result["results"][0]["body"] == {"id": "example"}
    assert all(item["dry_run"] for item in result["results"])


async def test_runtime_validates_capability_input_and_output_without_legacy_helpers() -> None:
    sender = _Sender({"/query": _Response(200, {"count": "not-an-integer"})})
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [{
            "step_id": "query", "step_uuid": "step-query-uuid",
            "method": "GET", "url": "https://oa.example/query",
        }],
        "capabilities": [{
            "capability_uuid": "cap-query-uuid",
            "name": "query",
            "step_ids": ["query"],
            "step_uuids": ["step-query-uuid"],
            "input_schema": {
                "type": "object", "properties": {"month": {"type": "string"}},
                "required": ["month"], "additionalProperties": False,
            },
            "output_schema": {
                "type": "object", "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
        }],
    }

    invalid_input = await execute_recording_capability(
        api, {}, capability="query", confirm=False,
        base_url="https://oa.example", sender=sender,
    )
    assert invalid_input["stage"] == "invalid_input"
    assert sender.calls == []

    invalid_output = await execute_recording_capability(
        api, {"month": "2026-07"}, capability="query", confirm=False,
        base_url="https://oa.example", sender=sender,
    )
    assert invalid_output["stage"] == "invalid_output"
    assert invalid_output["output_issues"] == ["output.count must be integer"]


def test_review_collector_requires_three_distinct_sidecar_sessions() -> None:
    collector = ReviewCollector()
    collector.begin("rec", 3, "sha256:value")
    for role in ("acceptance", "security", "compliance"):
        collector.submit_active(
            recording_id="rec", revision=3, role=role,
            verdict={"passed": True, "pi_session_id": "same-session"},
        )

    with pytest.raises(ValueError, match="three isolated sessions"):
        collector.collect("rec", 3, "sha256:value")


class _Writer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"published": True, "asset_id": "asset-1", "version": 1}


async def test_publish_freezes_exact_revision_and_binds_isolated_reviews() -> None:
    snapshot = _snapshot()
    collector = ReviewCollector()
    writer = _Writer()

    async def provider(recording_id: str, revision: int) -> dict[str, Any]:
        assert (recording_id, revision) == ("rec-7", 7)
        return deepcopy(snapshot)

    async def reviews(recording_id: str, revision: int) -> list[dict[str, Any]]:
        for role in ("acceptance", "security", "compliance"):
            collector.submit_active(
                recording_id=recording_id, revision=revision, role=role,
                verdict={"passed": True, "pi_session_id": f"session-{role}", "model_id": "spoofed"},
            )
        return []

    service = RecordingPublishService(
        snapshot_provider=provider,
        review_runner=reviews,
        review_collector=collector,
        asset_writer=writer,
    )

    result = await service.publish("rec-7", 7)

    assert result["published"] is True
    assert result["revision"] == 7
    assert len(writer.calls) == 1
    submitted = writer.calls[0]["reviews"]
    assert {item["pi_session_id"] for item in submitted} == {
        "session-acceptance", "session-security", "session-compliance",
    }
    assert all(item["model_id"] == "recording-pi" for item in submitted)


async def test_publish_rechecks_frozen_revision_after_review() -> None:
    snapshots = [_snapshot(), _snapshot()]
    snapshots[1]["title"] = "mutated after review"
    collector = ReviewCollector()
    writer = _Writer()

    async def provider(_recording_id: str, _revision: int) -> dict[str, Any]:
        return deepcopy(snapshots.pop(0))

    async def reviews(recording_id: str, revision: int) -> list[dict[str, Any]]:
        for role in ("acceptance", "security", "compliance"):
            collector.submit_active(
                recording_id=recording_id, revision=revision, role=role,
                verdict={"passed": True, "pi_session_id": f"session-{role}"},
            )
        return []

    service = RecordingPublishService(
        snapshot_provider=provider,
        review_runner=reviews,
        review_collector=collector,
        asset_writer=writer,
    )

    result = await service.publish("rec-7", 7)

    assert result["published"] is False
    assert result["stage"] == "freeze"
    assert writer.calls == []


async def test_one_canonical_field_keeps_every_wire_binding_but_one_public_input() -> None:
    snapshot = {
        "recording_id": "rec-bindings",
        "tenant": "tenant-a",
        "revision": 1,
        "start_url": "https://oa.example/workbench",
        "steps": [
            {
                "step_id": "create",
                "step_uuid": "step-create",
                "request_definition_id": "request-create",
                "method": "POST",
                "url": "https://oa.example/create",
                "body": {"owner": {"id": "captured-user"}},
                "params": [{
                    "field_uuid": "canonical-owner",
                    "field_contract_id": "legacy-owner-create",
                    "step_uuid": "step-create",
                    "request_definition_id": "request-create",
                    "location": "body",
                    "wire_path": "owner.id",
                    "public_name": "owner_id",
                    "business_type": "string",
                    "wire_type": "string",
                    "required": True,
                    "exposed_to_caller": True,
                    "source_binding": {"kind": "caller"},
                }],
                "risk_level": "L3",
                "requires_confirmation": True,
            },
            {
                "step_id": "notify",
                "step_uuid": "step-notify",
                "request_definition_id": "request-notify",
                "method": "POST",
                "url": "https://oa.example/notify",
                "body": None,
                "params": [{
                    "field_uuid": "canonical-owner",
                    "field_contract_id": "legacy-owner-notify",
                    "step_uuid": "step-notify",
                    "request_definition_id": "request-notify",
                    "location": "query",
                    "wire_path": "ownerId",
                    "public_name": "owner_id",
                    "business_type": "string",
                    "wire_type": "string",
                    "required": True,
                    "exposed_to_caller": True,
                    "source_binding": {"kind": "caller"},
                }],
                "risk_level": "L3",
                "requires_confirmation": True,
            },
        ],
        "capabilities": [{
            "capability_id": "cap-submit",
            "capability_uuid": "cap-submit-uuid",
            "name": "submit",
            "kind": "workflow",
            "step_ids": ["create", "notify"],
            "step_uuids": ["step-create", "step-notify"],
            "risk_level": "L3",
            "requires_confirmation": True,
        }],
        "request_facts": {"requests": [
            {"request_definition_id": "request-create", "disposition": "materialized"},
            {"request_definition_id": "request-notify", "disposition": "materialized"},
        ]},
    }

    candidate = project_asset(snapshot, revision=1)
    api = candidate.body["api_request"]
    assert candidate.body["verification_status"] == "verified", candidate.body["contract_faults"]
    bindings = [
        item for item in api["field_contracts"]
        if item["field_uuid"] == "canonical-owner"
    ]
    assert {
        (item["step_uuid"], item["location"], item["wire_path"])
        for item in bindings
    } == {
        ("step-create", "body", "owner.id"),
        ("step-notify", "query", "ownerId"),
    }
    assert candidate.body["user_fields"] == ["owner_id"]
    assert api["capabilities"][0]["input_schema"]["required"] == ["owner_id"]
    assert list(api["capabilities"][0]["input_schema"]["properties"]) == ["owner_id"]

    sender = _Sender({
        "/create": _Response(200, {"ok": True}),
        "/notify": _Response(200, {"ok": True}),
    })
    result = await execute_recording_capability(
        api, {"owner_id": "user-9"}, capability="submit", confirm=True,
        base_url="https://oa.example", sender=sender,
    )
    assert result["ok"] is True
    assert sender.calls[0][2]["json"] == {"owner": {"id": "user-9"}}
    assert parse_qs(urlsplit(sender.calls[1][1]).query) == {"ownerId": ["user-9"]}


async def test_root_array_body_and_array_tokens_preserve_wire_shape() -> None:
    snapshot = {
        "recording_id": "rec-array",
        "tenant": "tenant-a",
        "revision": 1,
        "start_url": "https://oa.example/workbench",
        "action": "submit_batch",
        "steps": [{
            "step_id": "submit-array",
            "step_uuid": "step-submit-array",
            "request_definition_id": "request-array",
            "method": "POST",
            "url": "https://oa.example/array",
            "body": [{"id": "captured"}],
            "params": [{
                "field_uuid": "field-records",
                "location": "body",
                "wire_path": "$",
                "public_name": "records",
                "business_type": "array",
                "wire_type": "array",
                "required": True,
                "exposed_to_caller": True,
                "source_binding": {"kind": "caller"},
            }],
            "risk_level": "L3",
            "requires_confirmation": True,
        }],
        "capabilities": [{
            "capability_id": "cap-array",
            "capability_uuid": "cap-array-uuid",
            "name": "submit_batch",
            "kind": "submit_batch",
            "step_ids": ["submit-array"],
            "step_uuids": ["step-submit-array"],
            "risk_level": "L3",
            "requires_confirmation": True,
        }],
        "request_facts": {"requests": [{
            "request_definition_id": "request-array", "disposition": "materialized",
        }]},
    }
    candidate = project_asset(snapshot, revision=1)
    api = candidate.body["api_request"]
    assert candidate.body["action"] == "submit"
    assert api["capabilities"][0]["name"] == "submit"
    assert api["capabilities"][0]["kind"] == "submit"
    assert api["steps"][0]["body_template"] == "{{input.records}}"

    sender = _Sender({"/array": _Response(200, {"ok": True})})
    payload = [{"id": "row-1"}, {"id": "row-2"}]
    result = await execute_recording_capability(
        api, {"records": payload}, capability="submit", confirm=True,
        base_url="https://oa.example", sender=sender,
    )
    assert result["ok"] is True
    assert sender.calls[0][2]["json"] == payload

    nested = deepcopy(snapshot)
    nested["recording_id"] = "rec-nested-array"
    nested["steps"][0]["body"] = {"items": [{"id": "a"}, {"id": "b"}]}
    nested["steps"][0]["params"] = [
        {
            "field_uuid": "field-first",
            "location": "body",
            "wire_path": "items[0].id",
            "public_name": "first_id",
            "business_type": "string",
            "wire_type": "string",
            "required": True,
            "exposed_to_caller": True,
            "source_binding": {"kind": "caller"},
        },
        {
            "field_uuid": "field-second",
            "location": "body",
            "wire_path": "items[1].id",
            "public_name": "second_id",
            "business_type": "string",
            "wire_type": "string",
            "required": True,
            "exposed_to_caller": True,
            "source_binding": {"kind": "caller"},
        },
    ]
    nested_candidate = project_asset(nested, revision=1)
    assert nested_candidate.body["api_request"]["steps"][0]["body_template"] == {
        "items": [{"id": "{{input.first_id}}"}, {"id": "{{input.second_id}}"}],
    }


def test_extract_field_facts_uses_root_binding_for_array_and_scalar_bodies() -> None:
    base = {
        "tenant": "tenant-a",
        "recording_id": "rec-root",
        "transaction_id": "tx-1",
        "sequence": 1,
        "method": "POST",
        "url": "https://oa.example/root",
        "path": "/root",
        "headers": {"Content-Type": "application/json"},
        "body_present": True,
        "disposition": RequestDisposition.MATERIALIZED,
        "disposition_reason": "test",
    }
    array_fact = extract_field_facts((
        CompiledRequest(request_id="request-array", body=[1, 2], **base),
    ))
    scalar_fact = extract_field_facts((
        CompiledRequest(request_id="request-scalar", body=7, **base),
    ))
    assert [
        (item.wire_path, item.wire_schema.type) for item in array_fact
        if item.location.value == "body"
    ] == [("$", "array")]
    assert [
        (item.wire_path, item.wire_schema.type) for item in scalar_fact
        if item.location.value == "body"
    ] == [("$", "integer")]


async def test_static_enum_schema_accepts_label_then_sends_wire_value() -> None:
    snapshot = _snapshot()
    field = snapshot["steps"][0]["params"][1]
    field["type"] = "enum"
    field["enum_binding"] = {
        "selected_pair_verified": True,
        "observed_mapping_complete": True,
        "mapping_coverage": "static_domain",
        "snapshot_coverage": {
            "kind": "native_loaded", "observed_count": 2, "truncated": False,
        },
        "source_scope": {},
        "evidence_ids": ["enum-proof"],
    }
    field["choice_contract"] = {
        "input_mode": "label_or_value",
        "typed_options": deepcopy(field["enum_options"]),
    }
    candidate = project_asset(snapshot, revision=7)
    api = candidate.body["api_request"]
    assert candidate.body["verification_status"] == "verified"
    report_schema = api["capabilities"][0]["input_schema"]["properties"]["report_text"]
    assert "anyOf" in report_schema

    sender = _Sender({"/api/report": _Response(200, {"id": "report-1"})})
    result = await execute_recording_capability(
        api, {"report_text": "日报", "dry_run": False},
        capability="submit_report", confirm=True,
        base_url="https://oa.example", sender=sender,
    )
    assert result["ok"] is True
    assert sender.calls[0][2]["json"]["report"]["text"] == "daily"


def test_identity_constant_is_erased_and_unsupported_user_resolver_fails_closed() -> None:
    snapshot = _snapshot()
    field = snapshot["steps"][0]["params"][2]
    field.update({
        "field_uuid": "field-creator",
        "field_id": "field-creator",
        "key": "creatorId",
        "path": "report.creatorId",
        "wire_path": "report.creatorId",
        "classification": "identity",
        "source": {"kind": "constant", "constant": "captured-user-7788"},
        "source_kind": "constant",
        "value": "captured-user-7788",
    })
    snapshot["steps"][0]["body_template"] = {
        "report": {"text": "recorded private text", "creatorId": "captured-user-7788"},
    }

    candidate = project_asset(snapshot, revision=7)

    assert candidate.body["verification_status"] == "unverified"
    assert candidate.body["direct_call_enabled"] is False
    assert "captured-user-7788" not in str(candidate.body)
    assert any(
        item["code"] == "runtime_resolver_unavailable"
        for item in candidate.body["contract_faults"]
    )


async def test_runtime_missing_verification_contract_never_calls_sender() -> None:
    sender = _Sender({"/write": _Response(200, {"ok": True})})
    api = {
        "recording_engine": "playwright_v3",
        "recorded_origin": "https://oa.example",
        "steps": [{
            "step_id": "write", "method": "POST", "url": "https://oa.example/write",
            "body_template": {"value": "{{input.value}}"},
        }],
        "capabilities": [{
            "name": "write", "step_ids": ["write"], "risk_level": "L3",
            "execution_enabled": True,
        }],
    }

    result = await execute_recording_capability(
        api, {"value": "x"}, capability="write", confirm=True,
        base_url="https://oa.example", sender=sender,
    )

    assert result["stage"] == "unverified_contract"
    assert sender.calls == []


async def test_runtime_rejects_literal_special_ips_and_dns_rebinding() -> None:
    with pytest.raises(ValueError, match="dangerous local or special"):
        RuntimePolicy(
            recorded_origin="http://169.254.169.254",
            allow_http=True,
            allow_private_networks=True,
        ).resolve_url("http://169.254.169.254", "/latest/meta-data")

    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [{
            "step_id": "read", "step_uuid": "step-read-uuid",
            "method": "GET", "url": "https://oa.example/read",
        }],
    }
    rebound_sender = _Sender({"/read": _Response(200, {"ok": True})})

    async def rebound(_hostname: str, _port: int) -> list[str]:
        return ["127.0.0.1", "169.254.169.254"]

    blocked = await execute_recording_workflow(
        api, {}, base_url="https://oa.example", sender=rebound_sender,
        send=True, allow_private_networks=True, address_resolver=rebound,
    )
    assert blocked["stage"] == "unsafe_target"
    assert rebound_sender.calls == []

    private_sender = _Sender({"/read": _Response(200, {"ok": True})})

    async def enterprise(_hostname: str, _port: int) -> list[str]:
        return ["10.20.30.40"]

    denied = await execute_recording_workflow(
        api, {}, base_url="https://oa.example", sender=private_sender,
        send=True, allow_private_networks=False, address_resolver=enterprise,
    )
    assert denied["stage"] == "unsafe_target"
    assert private_sender.calls == []

    allowed = await execute_recording_workflow(
        api, {}, base_url="https://oa.example", sender=private_sender,
        send=True, allow_private_networks=True, address_resolver=enterprise,
    )
    assert allowed["ok"] is True
    assert len(private_sender.calls) == 1


async def test_owned_runtime_pins_the_validated_address_without_a_second_dns_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The address checked by the SSRF guard must be the address connected to."""

    from dano_recording.runtime import workflow_executor

    calls: list[tuple[str, int]] = []

    async def resolver(hostname: str, port: int) -> list[str]:
        calls.append((hostname, port))
        if len(calls) == 1:
            return ["93.184.216.34"]
        # A vulnerable validate-then-resolve implementation would consume this
        # rebinding answer while opening the socket.
        return ["169.254.169.254"]

    class OwnedClient:
        last: "OwnedClient | None" = None

        def __init__(self, **_kwargs: Any) -> None:
            self.requests: list[tuple[str, str, dict[str, Any]]] = []
            self.closed = False
            OwnedClient.last = self

        async def request(self, method: str, url: str, **kwargs: Any) -> _Response:
            self.requests.append((method, url, kwargs))
            return _Response(200, {"ok": True})

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(workflow_executor.httpx, "AsyncClient", OwnedClient)
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [{
            "step_id": "read",
            "step_uuid": "step-read-owned-uuid",
            "method": "GET",
            "url": "https://oa.example:443/read?q=1",
            "success_rule": {"status_codes": [200]},
        }],
    }

    result = await execute_recording_workflow(
        api,
        {},
        base_url="https://oa.example",
        address_resolver=resolver,
    )

    assert result["ok"] is True
    assert calls == [("oa.example", 443)]
    assert OwnedClient.last is not None
    assert OwnedClient.last.closed is True
    _method, dispatch_url, kwargs = OwnedClient.last.requests[0]
    assert urlsplit(dispatch_url).hostname == "93.184.216.34"
    assert kwargs["headers"]["Host"] == "oa.example"
    assert kwargs["extensions"]["sni_hostname"] == "oa.example"


async def test_caller_and_wire_required_conditions_are_independent_and_safe() -> None:
    snapshot = {
        "recording_id": "rec-conditions",
        "tenant": "tenant-a",
        "revision": 1,
        "start_url": "https://oa.example/workbench",
        "steps": [{
            "step_id": "conditional",
            "step_uuid": "step-conditional",
            "request_definition_id": "request-conditional",
            "method": "POST",
            "url": "https://oa.example/conditional",
            "body": {"mode": "captured", "detail": "captured-private"},
            "params": [
                {
                    "field_uuid": "field-mode",
                    "location": "body",
                    "wire_path": "mode",
                    "public_name": "mode",
                    "business_type": "string",
                    "wire_type": "string",
                    "exposed_to_caller": True,
                    "source_binding": {"kind": "caller"},
                    "required_contract": {
                        "wire_required": "true",
                        "caller_required": "true",
                        "provider": {"kind": "caller"},
                    },
                },
                {
                    "field_uuid": "field-detail",
                    "location": "body",
                    "wire_path": "detail",
                    "public_name": "detail",
                    "business_type": "string",
                    "wire_type": "string",
                    "exposed_to_caller": True,
                    "source_binding": {"kind": "caller"},
                    "required_contract": {
                        "wire_required": "true",
                        "caller_required": "true",
                        "wire_condition": {
                            "operator": "equals", "field_uuid": "field-mode", "value": "send",
                        },
                        "caller_condition": {
                            "operator": "equals", "field_uuid": "field-mode", "value": "advanced",
                        },
                        "provider": {"kind": "caller"},
                    },
                },
            ],
            "risk_level": "L3",
            "requires_confirmation": True,
        }],
        "capabilities": [{
            "capability_id": "cap-conditional",
            "capability_uuid": "cap-conditional-uuid",
            "name": "conditional",
            "step_ids": ["conditional"],
            "step_uuids": ["step-conditional"],
            "risk_level": "L3",
            "requires_confirmation": True,
        }],
        "request_facts": {"requests": [{
            "request_definition_id": "request-conditional", "disposition": "materialized",
        }]},
    }
    candidate = project_asset(snapshot, revision=1)
    api = candidate.body["api_request"]
    assert candidate.body["verification_status"] == "verified", candidate.body["contract_faults"]
    schema = api["capabilities"][0]["input_schema"]
    assert schema["required"] == ["mode"]
    assert candidate.body["conditional_required_fields"] == ["detail"]

    sender = _Sender({"/conditional": _Response(200, {"ok": True})})
    optional = await execute_recording_capability(
        api, {"mode": "basic"}, capability="conditional", confirm=True,
        base_url="https://oa.example", sender=sender,
    )
    assert optional["ok"] is True
    assert sender.calls[0][2]["json"] == {"mode": "basic"}

    caller_missing = await execute_recording_capability(
        api, {"mode": "advanced"}, capability="conditional", confirm=True,
        base_url="https://oa.example", sender=sender,
    )
    assert caller_missing["stage"] == "invalid_input"

    wire_missing = await execute_recording_capability(
        api, {"mode": "send"}, capability="conditional", confirm=True,
        base_url="https://oa.example", sender=sender,
    )
    assert wire_missing["stage"] == "invalid_input"

    supplied = await execute_recording_capability(
        api, {"mode": "send", "detail": "safe"}, capability="conditional", confirm=True,
        base_url="https://oa.example", sender=sender,
    )
    assert supplied["ok"] is True
    assert sender.calls[-1][2]["json"] == {"mode": "send", "detail": "safe"}


def test_unresolved_internal_binding_is_omitted_only_when_explicitly_optional() -> None:
    snapshot = _snapshot()
    internal = snapshot["steps"][0]["params"][2]
    internal.update({
        "field_uuid": "field-spr",
        "key": "spr",
        "path": "report.spr",
        "wire_path": "report.spr",
        "source": {"kind": "unresolved"},
        "source_binding": {"kind": "unresolved"},
        "source_kind": "unresolved",
        "exposed_to_caller": False,
        "required_contract": {
            "wire_required": "false",
            "caller_required": "false",
            "provider": {"kind": "unresolved"},
        },
    })
    snapshot["steps"][0]["body_template"] = {
        "report": {"text": "recorded private text", "spr": "captured-internal"},
    }

    optional = project_asset(snapshot, revision=7)
    assert optional.body["verification_status"] == "verified", optional.body["contract_faults"]
    assert optional.body["api_request"]["steps"][0]["body_template"] == {
        "report": {"text": "{{input.report_text}}"},
    }
    assert "captured-internal" not in str(optional.body)

    unknown = deepcopy(snapshot)
    unknown["steps"][0]["params"][2]["required_contract"]["wire_required"] = "unknown"
    candidate = project_asset(unknown, revision=7)
    assert candidate.body["verification_status"] == "unverified"
    assert any(
        item["code"] == "wire_binding_provider_unavailable"
        for item in candidate.body["contract_faults"]
    )


async def test_captured_auth_headers_are_replaced_by_trusted_runtime_credentials() -> None:
    snapshot = _snapshot()
    step = snapshot["steps"][0]
    step["headers"].update({
        "Authorization": "Bearer captured-auth-secret",
        "Cookie": "session=captured-cookie-secret",
    })
    step["params"].extend([
        {
            "field_uuid": "field-authorization",
            "location": "header",
            "wire_path": "Authorization",
            "public_name": "Authorization",
            "business_type": "string",
            "wire_type": "string",
            "classification": "credential",
            "exposed_to_caller": False,
            "source_binding": {
                "kind": "runtime_context",
                "runtime_resolver": "runtime_context.request_headers.Authorization",
            },
            "required_contract": {
                "wire_required": "true",
                "caller_required": "false",
                "provider": {
                    "kind": "runtime_context",
                    "runtime_resolver": "runtime_context.request_headers.Authorization",
                },
            },
        },
        {
            "field_uuid": "field-cookie",
            "location": "header",
            "wire_path": "Cookie",
            "public_name": "Cookie",
            "business_type": "string",
            "wire_type": "string",
            "classification": "credential",
            "exposed_to_caller": False,
            "source_binding": {
                "kind": "runtime_context",
                "runtime_resolver": "runtime_context.request_headers.Cookie",
            },
            "required_contract": {
                "wire_required": "true",
                "caller_required": "false",
                "provider": {
                    "kind": "runtime_context",
                    "runtime_resolver": "runtime_context.request_headers.Cookie",
                },
            },
        },
    ])

    candidate = project_asset(snapshot, revision=7)
    api = candidate.body["api_request"]
    assert candidate.body["verification_status"] == "verified", candidate.body["contract_faults"]
    compiled_step = api["steps"][0]
    assert compiled_step["required_credential_headers"] == ["Authorization", "Cookie"]
    assert "Authorization" not in compiled_step["headers"]
    assert "Cookie" not in compiled_step["headers"]
    encoded = str(candidate.body)
    assert "captured-auth-secret" not in encoded
    assert "captured-cookie-secret" not in encoded

    sender = _Sender({"/api/report": _Response(200, {"id": "report-1"})})
    missing = await execute_recording_capability(
        api, {"dry_run": False, "report_text": "日报"},
        capability="submit_report", confirm=True,
        base_url="https://oa.example", sender=sender,
    )
    assert missing["stage"] == "credential_required"
    assert sender.calls == []

    result = await execute_recording_capability(
        api, {"dry_run": False, "report_text": "日报"},
        capability="submit_report", confirm=True,
        base_url="https://oa.example", sender=sender,
        credential_headers={
            "Authorization": "Bearer trusted-runtime",
            "Cookie": "session=trusted-runtime",
        },
    )
    assert result["ok"] is True
    assert sender.calls[0][2]["headers"]["Authorization"] == "Bearer trusted-runtime"
    assert sender.calls[0][2]["headers"]["Cookie"] == "session=trusted-runtime"


async def test_runtime_selects_renamed_capability_by_stable_uuid_first() -> None:
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [
            {
                "step_id": "stable", "step_uuid": "step-stable-uuid",
                "method": "GET", "url": "https://oa.example/stable",
            },
            {
                "step_id": "other", "step_uuid": "step-other-uuid",
                "method": "GET", "url": "https://oa.example/other",
            },
        ],
        "capabilities": [
            {
                "capability_uuid": "capability-stable-uuid",
                "capability_id": "legacy-stable",
                "name": "renamed_query",
                    "kind": "query",
                    "step_ids": ["stable"],
                    "step_uuids": ["step-stable-uuid"],
            },
            {
                "capability_uuid": "capability-other-uuid",
                "capability_id": "other",
                "name": "capability-stable-uuid",
                    "kind": "query",
                    "step_ids": ["other"],
                    "step_uuids": ["step-other-uuid"],
            },
        ],
    }
    sender = _Sender({
        "/stable": _Response(200, {"selected": "stable"}),
        "/other": _Response(200, {"selected": "other"}),
    })

    result = await execute_recording_capability(
        api, {}, capability="capability-stable-uuid", confirm=False,
        base_url="https://oa.example", sender=sender,
    )

    assert result["ok"] is True
    assert result["output"] == {"selected": "stable"}
    assert [urlsplit(call[1]).path for call in sender.calls] == ["/stable"]


async def test_evidence_backed_enum_exact_lookup_maps_label_before_submit() -> None:
    sender = _Sender({
        "/types": _Response(200, [{"label": "日报", "id": 2}]),
        "/submit": _Response(200, {"created": True}),
    })
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [
                {
                    "step_id": "types",
                    "step_uuid": "step-types-exact-uuid",
                "request_definition_id": "request-types",
                "method": "GET",
                "url": "https://oa.example/types",
                "query_template": {},
            },
                {
                    "step_id": "submit",
                    "step_uuid": "step-submit-exact-uuid",
                "request_definition_id": "request-submit",
                "method": "POST",
                "url": "https://oa.example/submit",
                "body_template": {"type": "{{input.report_type}}"},
            },
        ],
        "capabilities": [{
            "capability_uuid": "cap-submit",
            "name": "submit",
            "step_ids": ["submit"],
            "step_uuids": ["step-submit-exact-uuid"],
            "request_refs": [
                {
                    "step_id": "types", "step_uuid": "step-types-exact-uuid",
                    "usage": "option_source",
                },
                {
                    "step_id": "submit", "step_uuid": "step-submit-exact-uuid",
                    "usage": "execute",
                },
            ],
            "risk_level": "L3",
            "requires_confirmation": True,
            "input_schema": {
                "type": "object",
                "properties": {"report_type": {"type": "string"}},
                "required": ["report_type"],
                "additionalProperties": False,
            },
            "choice_contracts": [{
                "public_name": "report_type",
                "source_step_id": "types",
                "source_step_uuid": "step-types-exact-uuid",
                "input_mode": "label_or_value",
                "typed_options": [],
                "enum_evidence": {
                    "selected_pair_verified": False,
                    "observed_mapping_complete": False,
                    "mapping_coverage": "runtime_resolvable",
                    "snapshot_coverage": {
                        "kind": "unknown", "observed_count": 0, "truncated": False,
                    },
                    "source_scope": {},
                    "evidence_ids": ["enum-request-proof"],
                    "source_query": {
                        "request_definition_id": "request-types",
                        "method": "GET",
                        "request_template": {
                            "request_definition_id": "request-types",
                            "method": "GET",
                            "query_template": {"keyword": "{{label}}"},
                        },
                        "label_path": "label",
                        "value_path": "id",
                        "exact_lookup": True,
                        "search_param": "query_template.keyword",
                    },
                },
            }],
        }],
    }

    result = await execute_recording_capability(
        api, {"report_type": "日报"}, capability="cap-submit", confirm=True,
        base_url="https://oa.example", sender=sender,
    )

    assert result["ok"] is True
    assert [urlsplit(call[1]).path for call in sender.calls] == ["/types", "/submit"]
    assert parse_qs(urlsplit(sender.calls[0][1]).query) == {"keyword": ["日报"]}
    assert sender.calls[1][2]["json"] == {"type": 2}
    assert result["option_sources"][0]["matched_by"] == ["runtime_label"]


async def test_v3_option_listing_uses_verified_read_only_runtime_chain() -> None:
    sender = _Sender({
        "/types": _Response(200, {"data": [{"name": "日报", "code": "daily"}]}),
    })
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [{
            "step_id": "types",
            "step_uuid": "step-types-uuid",
            "request_definition_id": "request-types",
            "method": "GET",
            "url": "https://oa.example/types",
        }],
        "capabilities": [{
            "capability_uuid": "cap-submit",
            "name": "submit",
            "step_ids": [],
            "request_refs": [{
                "step_id": "types",
                "step_uuid": "step-types-uuid",
                "usage": "option_source",
            }],
            "choice_contracts": [{
                "public_name": "report_type",
                "source_step_id": "types",
                "source_step_uuid": "step-types-uuid",
                "options_path": "data",
                "label_path": "name",
                "value_path": "code",
                "enum_evidence": {
                    "mapping_coverage": "runtime_resolvable",
                    "snapshot_coverage": {
                        "kind": "unknown", "observed_count": 0, "truncated": False,
                    },
                    "source_scope": {},
                    "source_query": {
                        "request_definition_id": "request-types",
                        "method": "GET",
                        "request_template": {},
                        "label_path": "name",
                        "value_path": "code",
                        "exact_lookup": True,
                    },
                },
            }],
        }],
    }

    result = await list_recording_field_options(
        api,
        "report_type",
        capability="cap-submit",
        base_url="https://oa.example",
        sender=sender,
    )

    assert result == {
        "field": "report_type",
        "options": [{"label": "日报", "value": "daily"}],
        "count": 1,
        "note": "选项来自 V3 实时证据接口",
        "capability": "cap-submit",
    }
    assert [urlsplit(call[1]).path for call in sender.calls] == ["/types"]

    blocked = await list_recording_field_options(
        {**api, "verification_status": "unverified", "direct_call_enabled": False},
        "report_type",
        capability="cap-submit",
        base_url="https://oa.example",
        sender=sender,
    )
    assert blocked["stage"] == "unverified_contract"
    assert len(sender.calls) == 1


def test_partial_enum_sample_is_a_contract_fault_not_a_verified_static_domain() -> None:
    snapshot = _snapshot()
    field = snapshot["steps"][0]["params"][1]
    field["enum_binding"] = {
        "selected_pair_verified": True,
        "observed_mapping_complete": False,
        "mapping_coverage": "observed_set",
        "snapshot_coverage": {
            "kind": "visible_window", "observed_count": 2, "truncated": True,
        },
        "source_scope": {},
        "evidence_ids": ["visible-window-only"],
    }

    candidate = project_asset(snapshot, revision=7)
    report = check_executability(snapshot, candidate.body)

    assert candidate.body["verification_status"] == "unverified"
    assert candidate.body["direct_call_enabled"] is False
    assert any(
        item["code"] == "enum_label_not_resolvable"
        for item in candidate.body["contract_faults"]
    )
    assert any(
        item["code"] == "partial_enum_coverage"
        for item in report["advisories"]
    )


async def test_v3_runtime_rejects_legacy_and_partial_enum_compatibility_fallbacks() -> None:
    sender = _Sender({"/submit": _Response(200, {"ok": True})})
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [{
            "step_id": "submit",
            "step_uuid": "step-submit-uuid",
            "method": "POST",
            "url": "https://oa.example/submit",
            "body_template": {"type": "{{input.report_type}}"},
        }],
        "capabilities": [{
            "capability_uuid": "cap-submit-uuid",
            "name": "submit",
            "step_ids": ["submit"],
            "step_uuids": ["step-submit-uuid"],
            "risk_level": "L3",
            "requires_confirmation": True,
            "choice_contracts": [{
                "public_name": "report_type",
                "typed_options": [{"label": "日报", "value": "daily"}],
            }],
        }],
    }

    legacy = await execute_recording_capability(
        api,
        {"report_type": "日报"},
        capability="cap-submit-uuid",
        confirm=True,
        base_url="https://oa.example",
        sender=sender,
    )
    assert legacy["stage"] == "invalid_contract"
    assert "legacy choice contracts" in legacy["detail"]
    assert sender.calls == []

    partial = deepcopy(api)
    partial["capabilities"][0]["choice_contracts"][0]["enum_evidence"] = {
        "mapping_coverage": "observed_set",
        "snapshot_coverage": {
            "kind": "visible_window", "observed_count": 1, "truncated": True,
        },
        "source_scope": {},
    }
    rejected = await execute_recording_capability(
        partial,
        {"report_type": "日报"},
        capability="cap-submit-uuid",
        confirm=True,
        base_url="https://oa.example",
        sender=sender,
    )
    assert rejected["stage"] == "invalid_contract"
    assert sender.calls == []


async def test_runtime_and_executability_resolve_steps_uuid_first_and_fail_on_mismatch() -> None:
    steps = [
        {
            "step_id": "renamed-display-id",
            "step_uuid": "step-stable-uuid",
            "method": "GET",
            "url": "https://oa.example/stable",
        },
        {
            "step_id": "old-display-id",
            "step_uuid": "step-other-uuid",
            "method": "GET",
            "url": "https://oa.example/wrong",
        },
    ]
    cap = {
        "capability_uuid": "cap-stable-uuid",
        "name": "query",
        "step_uuids": ["step-stable-uuid"],
        "confirmed": True,
    }
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": list(reversed(steps)),
        "capabilities": [cap],
    }
    sender = _Sender({"/stable": _Response(200, {"selected": "stable"})})

    result = await execute_recording_capability(
        api,
        {},
        capability="cap-stable-uuid",
        confirm=True,
        base_url="https://oa.example",
        sender=sender,
    )
    assert result["ok"] is True
    assert [urlsplit(call[1]).path for call in sender.calls] == ["/stable"]

    report = check_executability({
        "revision": 1,
        "steps": steps,
        "capabilities": [
            {**cap, "step_ids": ["renamed-display-id"]},
            {
                "capability_uuid": "cap-other-uuid",
                "name": "other",
                "step_uuids": ["step-other-uuid"],
                "step_ids": ["old-display-id"],
                "confirmed": True,
            },
        ],
    })
    assert report["contract_faults"] == []

    broken = deepcopy(api)
    broken["capabilities"][0]["step_uuids"] = ["missing-step-uuid"]
    blocked = await execute_recording_capability(
        broken,
        {},
        capability="cap-stable-uuid",
        confirm=True,
        base_url="https://oa.example",
        sender=sender,
    )
    assert blocked["stage"] == "invalid_contract"
    assert len(sender.calls) == 1
    broken_report = check_executability({
        "revision": 1,
        "steps": steps,
        "capabilities": [{**cap, "step_uuids": ["missing-step-uuid"]}],
    })
    assert any(
        item["code"] == "capability_step_uuid_missing"
        for item in broken_report["contract_faults"]
    )

    missing_runtime_identity = await execute_recording_workflow(
        {
            "recording_engine": "playwright_v3",
            "verification_status": "verified",
            "direct_call_enabled": True,
            "recorded_origin": "https://oa.example",
            "steps": [{
                "step_id": "legacy-only",
                "method": "GET",
                "url": "https://oa.example/stable",
            }],
        },
        {},
        base_url="https://oa.example",
        sender=sender,
    )
    assert missing_runtime_identity["stage"] == "invalid_contract"
    assert missing_runtime_identity["results"] == []
    assert "step_1" not in str(missing_runtime_identity)

    legacy_only_capability = deepcopy(api)
    legacy_only_capability["capabilities"][0]["step_ids"] = ["renamed-display-id"]
    legacy_only_capability["capabilities"][0].pop("step_uuids")
    legacy_blocked = await execute_recording_capability(
        legacy_only_capability,
        {},
        capability="cap-stable-uuid",
        confirm=True,
        base_url="https://oa.example",
        sender=sender,
    )
    assert legacy_blocked["stage"] == "invalid_contract"
    assert "canonical step_uuids" in legacy_blocked["detail"]
    legacy_report = check_executability({
        "revision": 1,
        "steps": steps,
        "capabilities": [{**cap, "step_ids": ["renamed-display-id"], "step_uuids": []}],
    })
    assert {
        "missing_capability_step_uuids",
        "capability_missing_endpoint",
    }.issubset({item["code"] for item in legacy_report["contract_faults"]})


async def test_dynamic_enum_runtime_requires_canonical_source_step_uuid() -> None:
    api = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://oa.example",
        "steps": [
            {
                "step_id": "types",
                "step_uuid": "step-types-uuid",
                "request_definition_id": "request-types",
                "method": "GET",
                "url": "https://oa.example/types",
            },
            {
                "step_id": "submit",
                "step_uuid": "step-submit-uuid",
                "method": "POST",
                "url": "https://oa.example/submit",
                "body_template": {"type": "{{input.report_type}}"},
            },
        ],
        "capabilities": [{
            "capability_uuid": "cap-submit-uuid",
            "name": "submit",
            "step_ids": ["submit"],
            "step_uuids": ["step-submit-uuid"],
            "request_refs": [{
                "step_id": "types",
                "step_uuid": "step-types-uuid",
                "usage": "option_source",
            }],
            "risk_level": "L3",
            "requires_confirmation": True,
            "input_schema": {
                "type": "object",
                "properties": {"report_type": {"type": "string"}},
                "required": ["report_type"],
            },
            "choice_contracts": [{
                "public_name": "report_type",
                "source_step_id": "types",
                "enum_evidence": {
                    "mapping_coverage": "runtime_resolvable",
                    "snapshot_coverage": {
                        "kind": "unknown", "observed_count": 0, "truncated": False,
                    },
                    "source_scope": {},
                    "source_query": {
                        "request_definition_id": "request-types",
                        "method": "GET",
                        "request_template": {},
                        "label_path": "label",
                        "value_path": "id",
                        "exact_lookup": True,
                    },
                },
            }],
        }],
    }
    sender = _Sender({
        "/types": _Response(200, [{"label": "日报", "id": 2}]),
        "/submit": _Response(200, {"created": True}),
    })

    listed = await list_recording_field_options(
        api,
        "report_type",
        capability="cap-submit-uuid",
        base_url="https://oa.example",
        sender=sender,
    )
    invoked = await execute_recording_capability(
        api,
        {"report_type": "日报"},
        capability="cap-submit-uuid",
        confirm=True,
        base_url="https://oa.example",
        sender=sender,
    )

    assert listed["stage"] == "invalid_contract"
    assert invoked["stage"] == "invalid_contract"
    assert "source_step_uuid" in listed["note"]
    assert "source_step_uuid" in invoked["detail"]
    assert sender.calls == []
