"""Cross-system acceptance tests for recorded capability contracts.

The assertions deliberately cross subsystem boundaries: captured request facts
must remain attached to a capability, then survive manifest and agent-script
generation without changing the caller-visible schema.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from dano.catalog.manifest import to_manifest
from dano.execution.page.flow_spec import (
    FlowSpec,
    capability_spec_to_api_request,
    migrate_v2_flow_spec_to_capability_spec,
)
from dano.export.agent_skills import (
    _DIAGNOSE_PS1,
    _SUBMIT_PS1,
    _dano_call_py,
    _op_ps1,
    _skill_md,
    _export_contract_errors,
)
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
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return FlowSpec.model_validate(raw)


def _export_chain(spec: FlowSpec):
    capability_spec = migrate_v2_flow_spec_to_capability_spec(spec)
    api_request, errors = capability_spec_to_api_request(capability_spec)
    assert errors == []
    assert api_request is not None

    capabilities = [cap.model_dump(mode="json") for cap in capability_spec.capabilities]
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
    return capability_spec, api_request, manifest, namespace


@pytest.mark.parametrize(
    ("scenario", "expected_kind", "expected_fields"),
    [
        ("leave_flow_spec.json", "submit", {"请假类型", "原因"}),
        ("daily_report_flow_spec.json", "submit_batch", {"month", "entries"}),
        ("ordinary_form", "submit", {"employee_name", "mobileNo", "follow_up_at"}),
    ],
    ids=["leave-approval", "daily-report-batch", "ordinary-form"],
)
def test_request_facts_to_generated_script_contract_stays_aligned(
    scenario: str,
    expected_kind: str,
    expected_fields: set[str],
):
    capability_spec, api_request, manifest, script = _export_chain(_load_scenario(scenario))

    fact_ids = {fact.request_id for fact in capability_spec.request_facts.requests}
    materialized = {
        request_id
        for request_id, usage in capability_spec.request_facts.usage.items()
        if usage.state == "materialized" and usage.used_by_capabilities
    }
    contract_refs = {
        ref["request_id"]
        for contract in api_request["capability_contracts"]
        for ref in contract["request_refs"]
    }
    assert fact_ids == materialized == contract_refs

    capability = next(cap for cap in capability_spec.capabilities if cap.kind == expected_kind)
    manifest_capability = next(cap for cap in manifest.capabilities if cap["kind"] == expected_kind)
    script_capability = script["CAPABILITIES"][manifest_capability["name"]]

    capability_schema = capability.input_schema
    assert set(capability_schema["properties"]) == expected_fields
    assert set(manifest_capability["parameters"]["properties"]) == expected_fields
    assert script_capability["parameters"] == manifest_capability["parameters"]
    assert set(script_capability["fields"]) == expected_fields
    assert set(script_capability["required"]) == set(capability_schema.get("required") or [])

    if expected_kind == "submit_batch":
        item_fields = manifest_capability["parameters"]["properties"]["entries"]["items"]["properties"]
        assert set(item_fields) == {"date", "content", "project"}
    if scenario == "leave_flow_spec.json":
        assert "processDefinitionId" not in expected_fields
        assert manifest_capability["parameters"]["properties"]["请假类型"]["format"] == "name-ref"


def test_generated_cli_only_accepts_literal_boolean_strings():
    _spec, _request, _manifest, script = _export_chain(_load_scenario("ordinary_form"))
    contract = {
        "parameters": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
        }
    }

    assert script["_coerce_cli_values"]({"enabled": "true"}, contract)["enabled"] is True
    assert script["_coerce_cli_values"]({"enabled": "FALSE"}, contract)["enabled"] is False
    assert script["_coerce_cli_values"]({"enabled": True}, contract)["enabled"] is True
    for invalid in ("yes", "no", "1", "0", "random"):
        with pytest.raises(ValueError, match="true/false"):
            script["_coerce_cli_values"]({"enabled": invalid}, contract)


def test_generated_powershell_wrappers_propagate_python_exit_status(tmp_path: Path):
    assert _SUBMIT_PS1.rstrip().endswith("exit $LASTEXITCODE")
    assert _op_ps1("submit").rstrip().endswith("exit $LASTEXITCODE")
    assert _DIAGNOSE_PS1.rstrip().endswith("exit $LASTEXITCODE")

    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")
    (tmp_path / "dano_call.py").write_text(
        "import sys\nsys.exit(7)\n", encoding="utf-8"
    )
    wrapper = tmp_path / "submit.ps1"
    wrapper.write_text(_SUBMIT_PS1, encoding="utf-8")

    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(wrapper)],
        cwd=tmp_path,
        check=False,
    )

    assert completed.returncode == 7


def _query_withdraw_manifest():
    return to_manifest(SkillSpec(
        skill_id="A-OA.hotel_apply",
        subsystem=Subsystem.OA,
        action="hotel_apply",
        title="酒店申请",
        risk_level=RiskLevel.L3,
        capability_relations=[{
            "type": "record_reference",
            "from_capability": "query_hotel_apply",
            "from_output": "records[].id",
            "to_capability": "withdraw_hotel_apply",
            "to_input": "id",
        }],
        capabilities=[
            {
                "name": "query_hotel_apply",
                "kind": "query",
                "title": "查询酒店申请记录",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pageNo": {"type": "integer", "default": 1},
                        "pageSize": {"type": "integer", "default": 10},
                        "recordedId": {"type": "string", "default": "captured-id"},
                    },
                    "required": ["pageNo"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "records": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "hotelName": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
            {
                "name": "withdraw_hotel_apply",
                "kind": "submit",
                "title": "撤回酒店申请",
                "requires_human_confirm": True,
                "input_schema": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"success": {"type": "boolean"}},
                    "required": ["success"],
                },
            },
        ],
    ))


def test_manifest_marks_only_safe_pagination_defaults_and_grounded_record_identity():
    manifest = _query_withdraw_manifest()
    query = next(cap for cap in manifest.capabilities if cap["name"] == "query_hotel_apply")
    props = query["parameters"]["properties"]

    assert props["pageNo"]["x-dano-apply-default"] is True
    assert props["pageSize"]["x-dano-apply-default"] is True
    assert "pageNo" not in query["parameters"]["required"]
    assert "x-dano-apply-default" not in props["recordedId"]
    records = query["output_schema"]["properties"]["records"]
    assert records["x-record-id-field"] == "id"
    assert set(records["items"]["properties"]) == {"id", "hotelName"}
    assert _export_contract_errors(manifest) == []

    unsafe = deepcopy(manifest)
    unsafe.capabilities[0]["output_schema"]["properties"]["records"] = {
        "type": "array",
        "items": {"type": "object", "properties": {"status": {"type": "string"}}},
    }
    assert any(
        "x-record-id-field" in error
        for error in _export_contract_errors(unsafe)
    )


def test_v3_array_submit_remains_one_submit_capability_through_export():
    """V3 arrays are ordinary typed inputs, not a synthetic submit_batch ability."""
    input_schema = {
        "type": "object",
        "properties": {
            "month": {"type": "string"},
            "entries": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "format": "date"},
                        "hours": {"type": "number"},
                    },
                    "required": ["date", "hours"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["month", "entries"],
        "additionalProperties": False,
    }
    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.v3_work_hours",
        subsystem=Subsystem.OA,
        action="v3_work_hours",
        title="提交工时",
        risk_level=RiskLevel.L3,
        api_request={"recording_engine": "playwright_v3"},
        call_metadata={"recording_engine": "playwright_v3"},
        capabilities=[{
            "name": "submit_work_hours",
            "kind": "submit",
            "title": "提交工时",
            "input_schema": deepcopy(input_schema),
            "output_schema": {
                "type": "object",
                "properties": {"success": {"type": "boolean"}},
                "required": ["success"],
            },
        }],
    ))

    assert [cap["kind"] for cap in manifest.capabilities] == ["submit"]
    assert manifest.capabilities[0]["parameters"] == input_schema
    assert _export_contract_errors(manifest) == []
    namespace = {"__name__": "generated_v3_array_submit"}
    exec(compile(_dano_call_py(manifest), "<generated-v3-array-submit>", "exec"), namespace)  # noqa: S102
    assert set(namespace["CAPABILITIES"]) == {"submit_work_hours"}
    assert namespace["CAPABILITIES"]["submit_work_hours"]["kind"] == "submit"
    assert namespace["CAPABILITIES"]["submit_work_hours"]["parameters"] == input_schema


@pytest.mark.parametrize(
    ("risk", "flags"),
    [
        (RiskLevel.L3, {"read_only": "false", "requires_confirmation": False}),
        (RiskLevel.L1, {"requires_confirmation": "false"}),
    ],
)
def test_manifest_policy_flags_never_use_string_truthiness(risk, flags):
    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.strict_policy",
        subsystem=Subsystem.OA,
        action="strict_policy",
        title="严格策略提交",
        risk_level=risk,
        capabilities=[{
            "name": "submit_policy",
            "kind": "submit",
            "input_schema": {"type": "object", "properties": {}},
            **flags,
        }],
    ))

    capability = manifest.capabilities[0]
    assert capability["read_only"] is False
    assert capability["requires_confirmation"] is True


def test_generated_runtime_applies_only_marked_defaults_and_explicit_values_win():
    source = _dano_call_py(_query_withdraw_manifest())
    namespace = {"__name__": "generated_defaults_test"}
    exec(compile(source, "<generated-dano-call>", "exec"), namespace)  # noqa: S102
    contract = namespace["CAPABILITIES"]["query_hotel_apply"]

    assert namespace["_apply_safe_defaults"]({}, contract) == {
        "pageNo": 1,
        "pageSize": 10,
    }
    assert namespace["_apply_safe_defaults"]({"pageNo": 3}, contract) == {
        "pageNo": 3,
        "pageSize": 10,
    }
    assert "recordedId" not in namespace["_apply_safe_defaults"]({}, contract)


@pytest.mark.parametrize("raw_confirm", ["true", "false", 1, 0, None])
def test_generated_runtime_rejects_non_boolean_envelope_confirmation(
    monkeypatch, capsys, raw_confirm,
):
    namespace = {"__name__": "generated_confirm_test"}
    exec(
        compile(_dano_call_py(_query_withdraw_manifest()), "<generated-dano-call>", "exec"),
        namespace,
    )  # noqa: S102
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")
    monkeypatch.setattr("sys.argv", [
        "dano_call.py",
        "--json",
        json.dumps({
            "capability": "withdraw_hotel_apply",
            "input": {"id": "42"},
            "confirm": raw_confirm,
        }),
    ])

    with pytest.raises(SystemExit) as exc:
        namespace["main"]()

    assert exc.value.code == 2
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "confirm 必须是 JSON 布尔值" in result["reason"]


def test_skill_frontmatter_is_scoped_to_published_capabilities_only():
    manifest = _query_withdraw_manifest()
    markdown = _skill_md(manifest, "dano-a-oa-hotel-apply")

    assert "相关 A-OA 操作" not in markdown
    assert "query_hotel_apply" in markdown
    assert "withdraw_hotel_apply" in markdown
    assert "查询酒店申请记录" in markdown
    assert "撤回酒店申请" in markdown

    interaction = manifest.call_protocol["interaction_protocol"]
    assert interaction["source_contract"] == "back/doc/dano-tool-call-contract.md"
    assert interaction["max_calls_per_assistant_response"] == 1
    assert interaction["multi_field_collection"]["mode"] == "questions_array"
    assert interaction["non_confirmation_default"]["string_must_be_non_empty"] is True
    assert interaction["confirmation"]["allowed_keys"] == ["question", "confirm"]
    assert interaction["result_statuses"] == ["answered", "cancelled"]
    assert interaction["cancel_behavior"].startswith("stop_current_workflow")
    assert interaction["validation_error_behavior"].startswith("retry_silently")
    assert all("interaction_protocol" in cap["call_protocol"] for cap in manifest.capabilities)
    assert (Path(__file__).parents[1] / "doc" / "dano-tool-call-contract.md").is_file()


def test_repository_tool_call_contract_preserves_original_prompt_schema_and_results():
    contract = (Path(__file__).parents[1] / "doc" / "dano-tool-call-contract.md").read_text(
        encoding="utf-8",
    )

    assert "## description" in contract
    assert "## promptSnippet" in contract
    assert "## promptGuidelines" in contract
    assert "Call ask_user_question at most once per assistant response" in contract
    assert '"choices"' in contract
    assert '"input_type"' in contract
    assert '"data_source"' in contract
    assert '"multipleSelect"' in contract
    assert '"defaultValue"' in contract
    assert "A single question object is also accepted and normalized to an array" in contract
    assert '"const": "answered"' in contract
    assert '"const": "cancelled"' in contract
    assert '"additionalProperties": { "$ref": "#/$defs/answer" }' in contract


@pytest.mark.parametrize("fact_check", [
    {"fact_check": {"passed": "false"}},
    {"api": {"raw": {"fact_check_passed": "false"}}},
])
def test_generated_runtime_requires_literal_true_fact_check(
    monkeypatch, capsys, fact_check,
):
    manifest = _query_withdraw_manifest()
    withdraw = next(
        cap for cap in manifest.capabilities if cap["name"] == "withdraw_hotel_apply"
    )
    withdraw["validation_requirements"]["verification_required"] = True
    namespace = {"__name__": "generated_fact_test"}
    exec(
        compile(_dano_call_py(manifest), "<generated-dano-call>", "exec"),
        namespace,
    )  # noqa: S102
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "state": "completed",
                "audit": fact_check,
                "exec_result": {"structured_output": {"success": True}},
            }).encode()

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *args, **kwargs: _Response())
    monkeypatch.setattr("sys.argv", [
        "dano_call.py", "--json", json.dumps({
            "capability": "withdraw_hotel_apply",
            "input": {"id": "42"},
            "confirm": True,
        }),
    ])

    with pytest.raises(SystemExit) as exc:
        namespace["main"]()

    assert exc.value.code == 1
    assert "事实核查" in json.loads(capsys.readouterr().out.strip())["reason"]


def test_generated_runtime_failed_terminal_state_exits_nonzero(monkeypatch, capsys):
    namespace = {"__name__": "generated_failed_test"}
    exec(
        compile(_dano_call_py(_query_withdraw_manifest()), "<generated-dano-call>", "exec"),
        namespace,
    )  # noqa: S102
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"state": "failed", "message": "business rejected"}).encode()

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *args, **kwargs: _Response())
    monkeypatch.setattr("sys.argv", [
        "dano_call.py", "--json", json.dumps({
            "capability": "withdraw_hotel_apply",
            "input": {"id": "42"},
            "confirm": True,
        }),
    ])

    with pytest.raises(SystemExit) as exc:
        namespace["main"]()

    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out.strip())
    assert result["status"] == "failed"
    assert result["reason"] == "business rejected"


def test_work_hours_internal_field_names_do_not_leak_into_public_batch_contract():
    spec = _load_scenario("work_hours_flow_spec.json")
    _capability_spec, _api_request, manifest, script = _export_chain(spec)

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
