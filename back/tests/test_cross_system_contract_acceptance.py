"""Cross-system acceptance tests for the active recording release pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dano.catalog.manifest import to_manifest
from dano.execution.page.flow_spec import (
    FlowSpec,
    flow_spec_to_api_request,
    prepare_flow_release_candidate,
)
from dano.export.agent_skills import _dano_call_py
from dano.orchestrator.capability_runtime import normalize_capability_result
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem


FIXTURES = Path(__file__).parent / "fixtures" / "recording_v3"


def _ordinary_form_spec() -> FlowSpec:
    return FlowSpec.model_validate({
        "flow_id": "plain-contact-form",
        "title": "提交联系人表单",
        "steps": [{
            "step_id": "save_contact",
            "name": "保存联系人",
            "method": "POST",
            "url": "/crm/forms/contact/save",
            "path": "/crm/forms/contact/save",
            "body_source": json.dumps({
                "employee_name": "王小明",
                "mobileNo": "13800000000",
                "follow_up_at": "2026-07-15T09:30:00+08:00",
            }, ensure_ascii=False),
            "body_template": {
                "employee_name": "{{employee_name}}",
                "mobileNo": "{{mobileNo}}",
                "follow_up_at": "{{follow_up_at}}",
            },
            "params": [
                {
                    "path": "employee_name", "key": "employee_name", "label": "联系人",
                    "value": "王小明", "type": "string", "required": True,
                    "category": "user_param", "source_kind": "user_input",
                },
                {
                    "path": "mobileNo", "key": "mobileNo", "label": "手机号",
                    "value": "13800000000", "type": "string", "required": True,
                    "category": "user_param", "source_kind": "user_input",
                },
                {
                    "path": "follow_up_at", "key": "follow_up_at", "label": "跟进时间",
                    "value": "2026-07-15T09:30:00+08:00", "type": "datetime",
                    "category": "user_param", "source_kind": "user_input",
                },
            ],
            "response_json": {"success": True, "result": {"contactId": "C-100"}},
            "source_meta": {
                "request_id": "req-save-contact", "request_index": 11,
                "sequence": 11, "role": "submit_anchor",
            },
        }],
        "capabilities": [{
            "name": "submit",
            "kind": "submit",
            "title": "提交联系人表单",
            "step_ids": ["save_contact"],
            "nodes": [
                {"id": "call_save", "type": "call", "step_id": "save_contact"},
                {"id": "return_save", "type": "return", "from": "save_contact", "path": "response"},
            ],
            "output_mapping": [{
                "kind": "final_response", "step_id": "save_contact", "response_path": "response",
            }],
            "confirmed": True,
        }],
        "request_facts": {
            "requests": [{
                "request_id": "req-save-contact", "request_index": 11, "sequence": 11,
                "method": "POST", "url": "/crm/forms/contact/save",
                "path": "/crm/forms/contact/save",
                "response_json": {"success": True, "result": {"contactId": "C-100"}},
            }],
        },
    })


def _load_scenario(name: str) -> FlowSpec:
    if name == "ordinary_form":
        return _ordinary_form_spec()
    return FlowSpec.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _export_chain(spec: FlowSpec):
    release, snapshot = prepare_flow_release_candidate(spec)
    api_request, errors = flow_spec_to_api_request(release)
    assert errors == []
    assert api_request is not None
    assert snapshot["flow_fingerprint"]

    capabilities = [cap.model_dump(mode="json") for cap in release.capabilities]
    skill = SkillSpec(
        skill_id=f"A-OA.{spec.flow_id.replace('-', '_')}",
        subsystem=Subsystem.OA,
        action=spec.flow_id.replace("-", "_"),
        title=spec.title,
        risk_level=RiskLevel.L3,
        has_api=False,
        api_request=api_request,
        capabilities=capabilities,
    )
    manifest = to_manifest(skill)
    namespace = {"__name__": "generated_contract_acceptance"}
    exec(compile(_dano_call_py(manifest), "<generated-dano-call>", "exec"), namespace)  # noqa: S102
    return release, api_request, manifest, namespace


@pytest.mark.parametrize(
    ("scenario", "expected_kind", "expected_fields"),
    [
        ("leave_flow_spec.json", "submit", {"请假类型", "原因"}),
        ("daily_report_flow_spec.json", "submit_batch", {"month", "entries"}),
        ("ordinary_form", "submit", {"employee_name", "mobileNo", "follow_up_at"}),
    ],
    ids=["approval", "batch", "ordinary-crm-form"],
)
def test_active_release_pipeline_keeps_public_contract_aligned(
    scenario: str,
    expected_kind: str,
    expected_fields: set[str],
):
    release, api_request, manifest, script = _export_chain(_load_scenario(scenario))

    capability = next(cap for cap in release.capabilities if cap.kind == expected_kind)
    manifest_capability = next(cap for cap in manifest.capabilities if cap["kind"] == expected_kind)
    script_capability = script["CAPABILITIES"][manifest_capability["name"]]

    assert set(capability.input_schema["properties"]) == expected_fields
    assert set(manifest_capability["parameters"]["properties"]) == expected_fields
    assert script_capability["parameters"] == manifest_capability["parameters"]
    assert set(script_capability["fields"]) == expected_fields
    assert api_request["capability_contracts"]

    if expected_kind == "submit_batch":
        item_fields = manifest_capability["parameters"]["properties"]["entries"]["items"]["properties"]
        assert set(item_fields) == {"date", "content", "project"}


def test_work_hours_internal_names_do_not_leak_into_public_batch_contract():
    _release, _api_request, manifest, script = _export_chain(_load_scenario("work_hours_flow_spec.json"))

    batch = next(cap for cap in manifest.capabilities if cap["kind"] == "submit_batch")
    item_fields = batch["parameters"]["properties"]["entries"]["items"]["properties"]
    assert set(item_fields) == {"date", "content", "hours"}
    assert not ({"sbrq", "gznr", "sbgs"} & set(item_fields))
    assert script["CAPABILITIES"]["submit_batch"]["parameters"] == batch["parameters"]


@pytest.mark.parametrize("wrapper", ["structured_output", "output", "response", "final"])
def test_runtime_normalizes_supported_response_wrappers_without_contract_drift(wrapper: str):
    payload = {"contactId": "C-100", "accepted": True}
    normalized = normalize_capability_result(
        {"ok": True, wrapper: payload},
        "submit",
        skill_id="A-OA.plain_contact_form",
        output_schema={
            "type": "object",
            "properties": {
                "contactId": {"type": "string"},
                "accepted": {"type": "boolean"},
            },
            "required": ["contactId", "accepted"],
        },
    )

    assert normalized["ok"] is True
    assert normalized["capability"] == "submit"
    assert normalized["output"] == payload
    assert normalized["raw"][wrapper] == payload
