"""Protocol and safety regression tests for generated Agent Skill wrappers."""

from __future__ import annotations

import json
import sys

import pytest

from dano.catalog.manifest import to_manifest
from dano.export.agent_skills import _SUBMIT_PS1, _dano_call_py
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem


def _write_runtime_namespace() -> dict:
    skill = SkillSpec(
        skill_id="A-OA.withdraw_request",
        subsystem=Subsystem.OA,
        action="withdraw_request",
        title="撤回申请",
        risk_level=RiskLevel.L3,
        api_request={"fact_check": {"endpoint": "/request/page"}},
        capabilities=[{
            "name": "submit",
            "kind": "submit",
            "title": "撤回申请",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"result": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "title": "记录ID",
                            "x-dano-identifier-role": "record",
                        },
                        "processInstanceId": {
                            "type": "string",
                            "title": "流程实例ID",
                            "x-dano-identifier-role": "process_instance",
                        },
                        "billCode": {
                            "type": "string",
                            "title": "单据编号",
                            "x-dano-identifier-role": "business_document",
                        },
                    },
                }},
                "required": ["result"],
            },
        }],
    )
    manifest = to_manifest(skill)
    # Isolate the generated-wrapper verification path from flow inference: this
    # contract explicitly requires a post-write fact check.
    manifest.capabilities[0]["validation_requirements"]["verification_required"] = True
    manifest.capabilities[0]["requires_confirmation"] = True
    namespace = {"__name__": "generated_test"}
    exec(compile(_dano_call_py(manifest), "<generated-dano-call>", "exec"), namespace)  # noqa: S102
    return namespace


@pytest.mark.parametrize("raw_confirm", ["true", "false", 1, 0, None])
def test_generated_runtime_rejects_non_boolean_confirmation(
    monkeypatch, capsys, raw_confirm,
):
    namespace = _write_runtime_namespace()
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")
    calls: list[object] = []
    monkeypatch.setattr(
        namespace["urllib"].request,
        "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py",
        "--json",
        json.dumps({"capability": "submit", "input": {"id": "42"}, "confirm": raw_confirm}),
    ])

    with pytest.raises(SystemExit) as exc:
        namespace["main"]()

    assert exc.value.code == 2
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["status"] == "failed"
    assert "confirm 必须是 JSON 布尔值" in result["reason"]
    assert calls == []


def test_generated_runtime_boolean_false_keeps_confirmation_gate_closed(monkeypatch, capsys):
    namespace = _write_runtime_namespace()
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")
    calls: list[object] = []
    monkeypatch.setattr(
        namespace["urllib"].request,
        "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py",
        "--json",
        json.dumps({"capability": "submit", "input": {"id": "42"}, "confirm": False}),
    ])

    namespace["main"]()

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["status"] == "need_confirm"
    assert calls == []


def test_generated_runtime_sends_only_capability_endpoint_contract(monkeypatch, capsys):
    namespace = _write_runtime_namespace()
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")
    captured: dict = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "state": "completed",
                "audit": {
                    "fact_check": {"passed": True},
                    "api": {
                        "method": "POST",
                        "url": "https://oa.example.test/requests/42",
                    },
                },
                "exec_result": {"structured_output": {"result": {
                    "id": "record-42",
                    "processInstanceId": "process-42",
                    "billCode": "QJD-42",
                }}},
            }).encode()

    def _urlopen(request, **_kwargs):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        return _Response()

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", _urlopen)
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py",
        "--json",
        json.dumps({"capability": "submit", "input": {"id": "42"}, "confirm": True}),
    ])

    namespace["main"]()

    assert captured == {
        "url": "http://dano.test/v1/skills/A-OA.withdraw_request/capabilities/submit/invoke",
        "payload": {
            "protocol": "dano.capability_call.v1",
            "input": {"id": "42"},
            "confirm": True,
        },
    }
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["status"] == "succeeded"
    assert result["request_url"] == "https://oa.example.test/requests/42"
    assert result["request_link"] == {
        "label": "查看原始请求",
        "url": "https://oa.example.test/requests/42",
        "target": "_blank",
        "rel": "noopener noreferrer",
    }
    assert result["business_identifiers"] == {
        "record_id": {"label": "记录ID", "value": "record-42"},
        "process_instance_id": {"label": "流程实例ID", "value": "process-42"},
        "document_number": {"label": "单据编号", "value": "QJD-42"},
    }


def test_generated_runtime_finds_original_url_in_workflow_final_step():
    namespace = _write_runtime_namespace()

    assert namespace["_original_request_link"]({
        "step_results": [
            {"method": "GET", "url": "https://oa.example.test/options"},
            {"method": "POST", "url": "https://oa.example.test/requests"},
        ],
        "final": {"method": "POST", "url": "https://oa.example.test/requests"},
    }) == {
        "label": "查看原始请求",
        "url": "https://oa.example.test/requests",
        "target": "_blank",
        "rel": "noopener noreferrer",
    }


def test_generated_runtime_keeps_identifier_meanings_separate():
    namespace = _write_runtime_namespace()

    assert namespace["_business_identifiers"]({
        "data": {
            "opaqueA": "record-42",
            "opaqueB": "workflow-42",
            "opaqueC": "ERP-42",
        },
    }, {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "properties": {
                    "opaqueA": {"x-dano-identifier-role": "record", "title": "记录键"},
                    "opaqueB": {
                        "x-dano-identifier-role": "process_instance",
                        "title": "流程键",
                    },
                    "opaqueC": {
                        "x-dano-identifier-role": "business_document",
                        "title": "工单编号",
                    },
                },
            },
        },
    }) == {
        "record_id": {"label": "记录键", "value": "record-42"},
        "process_instance_id": {"label": "流程键", "value": "workflow-42"},
        "document_number": {"label": "工单编号", "value": "ERP-42"},
    }
    assert namespace["_business_identifiers"](
        {"billCode": "looks-like-a-number"},
        {"type": "object", "properties": {"billCode": {"type": "string"}}},
    ) == {}


def test_generated_runtime_returns_recorded_business_page_when_audit_has_no_url(
    monkeypatch, capsys,
):
    skill = SkillSpec(
        skill_id="A-OA.leave_request",
        subsystem=Subsystem.OA,
        action="leave_request",
        title="请假申请",
        risk_level=RiskLevel.L3,
        has_api=False,
        api_request={
            "goal": {
                "evidence": [{
                    "trigger_page_context": {
                        "url": "https://oa.example.test/oa/duty/leave",
                    },
                }],
            },
        },
        capabilities=[{
            "name": "query_leave",
            "kind": "query_status",
            "title": "查询请假申请",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "output_schema": {
                "type": "object",
                "properties": {"records": {"type": "array"}},
                "required": ["records"],
            },
        }],
    )
    manifest = to_manifest(skill)
    namespace = {"__name__": "generated_page_link_test"}
    exec(compile(_dano_call_py(manifest), "<generated-page-link>", "exec"), namespace)  # noqa: S102
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
                "audit": {},
                "exec_result": {"structured_output": {"records": []}},
            }).encode()

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr(sys, "argv", ["dano_call.py", "--json", "{}"])

    namespace["main"]()

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["request_url"] == "https://oa.example.test/oa/duty/leave"
    assert result["request_link"] == {
        "label": "打开原系统页面",
        "url": "https://oa.example.test/oa/duty/leave",
        "target": "_blank",
        "rel": "noopener noreferrer",
    }


def test_generated_runtime_unwraps_normalized_capability_output_before_schema_check(
    monkeypatch, capsys,
):
    namespace = _write_runtime_namespace()
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")
    business_output = {"result": {"code": 0, "data": "request-42"}}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "state": "completed",
                "audit": {"fact_check": {"passed": True}},
                "exec_result": {"structured_output": {
                    "ok": True,
                    "skill_id": "A-OA.withdraw_request",
                    "capability": "submit",
                    "output": business_output,
                    "response": business_output,
                    "structured_output": business_output,
                    "status": "succeeded",
                }},
            }).encode()

    monkeypatch.setattr(
        namespace["urllib"].request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(),
    )
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py",
        "--json",
        json.dumps({"capability": "submit", "input": {"id": "42"}, "confirm": True}),
    ])

    namespace["main"]()

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["status"] == "succeeded"
    assert result["output"] == business_output


@pytest.mark.parametrize("invalid_capability", [["submit"], {"name": "submit"}, 1, True, None, ""])
def test_generated_runtime_invalid_capability_type_ends_with_json(
    monkeypatch, capsys, invalid_capability,
):
    namespace = _write_runtime_namespace()
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py",
        "--json",
        json.dumps({"capability": invalid_capability, "input": {"id": "42"}, "confirm": True}),
    ])

    with pytest.raises(SystemExit) as exc:
        namespace["main"]()

    assert exc.value.code == 2
    last_line = capsys.readouterr().out.strip().splitlines()[-1]
    result = json.loads(last_line)
    assert result["status"] == "failed"
    assert result["reason"] == "capability 必须是非空字符串"


@pytest.mark.parametrize("fact_check", [
    {"api": {"raw": {"fact_check_passed": "false"}}},
    {"fact_check": {"passed": "false"}},
])
def test_generated_runtime_never_treats_string_false_as_fact_check_passed(
    monkeypatch, capsys, fact_check,
):
    namespace = _write_runtime_namespace()
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "state": "completed",
                "audit": fact_check,
                "exec_result": {"structured_output": {"result": {}}},
            }).encode()

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *args, **kwargs: _Response())
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py",
        "--json",
        json.dumps({"capability": "submit", "input": {"id": "42"}, "confirm": True}),
    ])

    with pytest.raises(SystemExit) as exc:
        namespace["main"]()

    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["status"] == "failed"
    assert "事实核查" in result["reason"]


def test_generated_powershell_wrapper_propagates_python_exit_code():
    assert _SUBMIT_PS1.rstrip().splitlines()[-1] == "exit $LASTEXITCODE"


def test_generated_runtime_only_applies_defaults_marked_safe():
    namespace = _write_runtime_namespace()
    contract = {
        "parameters": {
            "type": "object",
            "properties": {
                "pageNo": {"type": "integer", "default": 1, "x-dano-apply-default": True},
                "pageSize": {"type": "integer", "default": 10, "x-dano-apply-default": True},
                "id": {"type": "string", "default": "recorded-id"},
            },
        },
    }

    assert namespace["_apply_safe_defaults"]({}, contract) == {"pageNo": 1, "pageSize": 10}
    assert namespace["_apply_safe_defaults"]({"pageNo": 3}, contract) == {"pageNo": 3, "pageSize": 10}


def test_generated_runtime_failed_state_uses_nonzero_exit(monkeypatch, capsys):
    namespace = _write_runtime_namespace()
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"state": "failed", "message": "business rejected"}).encode()

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *args, **kwargs: _Response())
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py",
        "--json",
        json.dumps({"capability": "submit", "input": {"id": "42"}, "confirm": True}),
    ])

    with pytest.raises(SystemExit) as exc:
        namespace["main"]()

    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {
        "status": "failed",
        "state": "failed",
        "reason": "business rejected",
        "fact_check": None,
    }


@pytest.mark.parametrize("allow_partial", ["false", "true", 1, 0, None])
def test_generated_runtime_partial_permission_requires_real_boolean_true(
    monkeypatch, capsys, allow_partial,
):
    namespace = _write_runtime_namespace()
    namespace["CAPABILITIES"]["submit"]["validation_requirements"][
        "allow_partial_success"
    ] = allow_partial
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "state": "partial_success",
                "audit": {"fact_check": {"passed": True}},
                "exec_result": {"structured_output": {"result": {}}},
            }).encode()

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *args, **kwargs: _Response())
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py", "--json",
        json.dumps({"capability": "submit", "input": {"id": "42"}, "confirm": True}),
    ])

    with pytest.raises(SystemExit) as exc:
        namespace["main"]()

    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["status"] == "failed"
    assert "不允许部分成功" in result["reason"]


def test_generated_runtime_partial_success_cannot_bypass_required_fact_check(monkeypatch, capsys):
    namespace = _write_runtime_namespace()
    namespace["CAPABILITIES"]["submit"]["validation_requirements"][
        "allow_partial_success"
    ] = True
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "state": "partial_success",
                "audit": {"fact_check": {"passed": False}},
                "exec_result": {"structured_output": {"result": {}}},
            }).encode()

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *args, **kwargs: _Response())
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py", "--json",
        json.dumps({"capability": "submit", "input": {"id": "42"}, "confirm": True}),
    ])

    with pytest.raises(SystemExit) as exc:
        namespace["main"]()

    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["status"] == "failed"
    assert "事实核查" in result["reason"]
