from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest

from dano.execution.page.flow_spec import (
    FlowCapability,
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    flow_spec_to_api_request,
)
from dano.export.skill_package.renderer import package_slug, render_skill_package
from dano.export.skill_package.validator import validate_skill_package
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem


_LINK_VERIFICATION = "550e8400-e29b-41d4-a716-446655440000"
_WRITE_VERIFICATION = "550e8400-e29b-41d4-a716-446655440001"


class _BusinessApi(BaseHTTPRequestHandler):
    items = [{"recordId": "record-1", "name": "seed"}]

    def _send(self, payload):  # noqa: ANN001
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        assert self.headers.get("Authorization") == "Bearer runtime-only-value"
        self._send({"code": 0, "data": list(self.items)})

    def do_POST(self):  # noqa: N802
        assert self.headers.get("Authorization") == "Bearer runtime-only-value"
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.items.insert(0, payload)
        self._send({"code": 0, "data": payload})

    def log_message(self, *_args):  # noqa: ANN002
        return


def _recording_skill(origin: str) -> SkillSpec:
    query = FlowStep(
        step_id="query",
        name="Query items",
        method="GET",
        url=f"{origin}/items",
        path="/items",
        success_rule={"field": "code", "ok_values": [0]},
        response_json={"code": 0, "data": [{"recordId": "record-1", "name": "seed"}]},
    )
    create = FlowStep(
        step_id="create",
        name="Create item",
        method="POST",
        url=f"{origin}/items",
        path="/items",
        body_source=json.dumps({"recordId": "record-1", "name": "recorded"}),
        body_template={"recordId": "record-1", "name": "{{name}}"},
        params=[
            ParamField(
                path="recordId", key="recordId", value="record-1",
                category="runtime_var", source_kind="previous_response",
                source={"kind": "previous_response", "step_id": "query", "response_path": "data.0.recordId"},
                exposed_to_user=False,
            ),
            ParamField(path="name", key="name", value="recorded", category="user_param", source_kind="user_input"),
        ],
        success_rule={"field": "code", "ok_values": [0]},
        fact_check={
            "endpoint": "/items",
            "assertion": {"path": "data.0.name", "equals_input": "name"},
            "verification_id": _WRITE_VERIFICATION,
            "verified": True,
        },
    )
    link = FlowLink(
        link_id="query-to-create",
        source_step_id="query",
        source_path="data.0.recordId",
        target_step_id="create",
        target_path="recordId",
        confirmed=True,
        meta={"verified": True, "actor": "agent", "verification_id": _LINK_VERIFICATION},
    )
    spec = FlowSpec(
        title="Items",
        steps=[query, create],
        links=[link],
        capabilities=[
            FlowCapability(
                capability_id="cap-query", name="query_items", title="Query items", kind="query",
                step_ids=["query"], confirmed=True, status="confirmed",
                nodes=[
                    {"id": "call_1", "type": "call", "step_id": "query", "method": "GET", "path": "/items"},
                ],
                input_schema={"type": "object", "properties": {}},
            ),
            FlowCapability(
                capability_id="cap-create", name="create_item", title="Create item", kind="create",
                step_ids=["query", "create"], confirmed=True, status="confirmed",
                nodes=[
                    {"id": "call_1", "type": "call", "step_id": "query", "method": "GET", "path": "/items"},
                    {"id": "call_2", "type": "call", "step_id": "create", "method": "POST", "path": "/items"},
                ],
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "Item name"}},
                    "required": ["name"],
                },
            ),
        ],
        goal={
            "intent": "Query and create items",
            "success_criteria": ["read-back contains the new name"],
            "forbidden_actions": ["Do not delete records"],
            "risk_level": "L3",
        },
        meta={
            "verification_run": {"complete": True},
            "verification_log": [
                {
                    "verification_id": _LINK_VERIFICATION,
                    "kind": "perturb_link",
                    "evidence": {"passed": True},
                },
                {
                    "verification_id": _WRITE_VERIFICATION,
                    "kind": "write_execute",
                    "evidence": {"passed": True},
                },
            ],
        },
    )
    api_request, errors = flow_spec_to_api_request(spec)
    assert api_request is not None and errors == []
    api_request["_release_snapshot"] = {
        "protocol": "dano.recording_release.v1",
        "flow_spec": spec.model_dump(mode="json"),
    }
    return SkillSpec(
        skill_id="demo.items",
        subsystem=Subsystem("demo"),
        action="items",
        risk_level=RiskLevel.L3,
        title="Items",
        has_api=False,
        api_request=api_request,
    )


def _run(script: Path, *args: str, env: dict[str, str]) -> dict:
    runner = json.loads(os.environ.get("DANO_PACKAGE_TEST_COMMAND", "[]"))
    command = [*runner, str(script), *args] if runner else [sys.executable, str(script), *args]
    completed = subprocess.run(
        command,
        cwd=script.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_self_contained_package_executes_query_write_and_readback_without_dano(tmp_path):
    _BusinessApi.items = [{"recordId": "record-1", "name": "seed"}]
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BusinessApi)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        skill = _recording_skill(origin)
        folder_name = render_skill_package(skill, str(tmp_path), tenant="tenant-a")
        package = tmp_path / folder_name
        assert folder_name == package_slug(skill.skill_id)
        assert validate_skill_package(package) == {"ok": True, "issues": []}
        assert _LINK_VERIFICATION in (package / "reference.md").read_text(encoding="utf-8")
        all_text = "\n".join(
            path.read_text(encoding="utf-8") for path in package.rglob("*") if path.is_file()
        )
        assert "runtime-only-value" not in all_text

        env = {
            **os.environ,
            "DANO_AUTH_HEADERS": json.dumps({"Authorization": "Bearer runtime-only-value"}),
        }
        scripts = package / "scripts"
        assert _run(scripts / "query_items.py", env=env)["ok"] is True
        assert _run(scripts / "create_item.py", "--name", "created", env=env)["ok"] is True
        verified = _run(scripts / "verify_create_item.py", "--name", "created", env=env)
        assert verified == {"capability": "create_item", "ok": True, "issues": [], "checks": 1}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_invalid_model_docs_fall_back_to_complete_deterministic_docs(tmp_path):
    skill = _recording_skill("https://example.invalid")
    release = skill.api_request["_release_snapshot"]["flow_spec"]
    release["meta"]["skill_docs"] = {"skill_md": "bad", "reference_md": "bad"}
    folder = tmp_path / render_skill_package(skill, str(tmp_path), tenant="tenant-a")
    assert validate_skill_package(folder)["ok"] is True
    assert "## Transport" in (folder / "SKILL.md").read_text(encoding="utf-8")


def test_unverified_write_verifier_fails_closed_and_reference_marks_it(tmp_path):
    skill = _recording_skill("https://example.invalid")
    release = skill.api_request["_release_snapshot"]["flow_spec"]
    release["steps"][1]["fact_check"] = {}
    release["meta"]["verification_log"] = [release["meta"]["verification_log"][0]]
    folder = tmp_path / render_skill_package(skill, str(tmp_path), tenant="tenant-a")
    reference = (folder / "reference.md").read_text(encoding="utf-8")
    assert "create_item" in reference
    assert "unverified write read-back" in reference

    completed = subprocess.run(
        [sys.executable, str(folder / "scripts" / "verify_create_item.py"), "--name", "created"],
        cwd=folder / "scripts",
        env={**os.environ, "DANO_AUTH_HEADERS": "{}"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert completed.returncode == 1
    assert result["ok"] is False
    assert result["issues"][0]["verification_id"] == "unverified"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected", "result"),
    [
        ("proxy", ["proxy"], ["proxy-folder"]),
        ("package", ["package"], ["package-folder"]),
        ("both", ["proxy", "package"], ["proxy-folder", "package-folder"]),
    ],
)
async def test_export_mode_dispatches_exact_requested_shapes(monkeypatch, tmp_path, mode, expected, result):
    import dano.export.agent_skills as exports
    import dano.export.skill_package.renderer as packages

    calls = []

    async def proxy(*_args, **_kwargs):
        calls.append("proxy")
        return ["proxy-folder"]

    async def package(*_args, **_kwargs):
        calls.append("package")
        return ["package-folder"]

    monkeypatch.setattr(exports, "write_skills", proxy)
    monkeypatch.setattr(packages, "write_skill_packages", package)
    assert await exports.write_exports("tenant-a", str(tmp_path), mode=mode) == result
    assert calls == expected


@pytest.mark.asyncio
async def test_export_mode_rejects_unknown_value(tmp_path):
    from dano.export.agent_skills import write_exports

    with pytest.raises(ValueError, match="proxy/package/both"):
        await write_exports("tenant-a", str(tmp_path), mode="unknown")
