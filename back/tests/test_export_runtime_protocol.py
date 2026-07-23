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
                "properties": {"result": {"type": "object"}},
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
                "audit": {"fact_check": {"passed": True}},
                "exec_result": {"structured_output": {"result": {}}},
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
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["status"] == "succeeded"


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
