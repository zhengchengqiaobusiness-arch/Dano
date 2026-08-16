from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
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
from dano.export.skill_package.renderer import (
    _CLIENT_TEMPLATE,
    _verified_links,
    package_slug,
    render_skill_package,
)
from dano.export.skill_package.validator import validate_skill_package
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem


_LINK_VERIFICATION = "550e8400-e29b-41d4-a716-446655440000"
_WRITE_VERIFICATION = "550e8400-e29b-41d4-a716-446655440001"


def test_export_includes_recording_grounded_dependencies_without_replay_ids() -> None:
    spec = FlowSpec(
        steps=[
            FlowStep(step_id="definition", method="GET", path="/definition"),
            FlowStep(step_id="approval", method="GET", path="/approval"),
            FlowStep(step_id="submit", method="POST", path="/submit"),
        ],
        links=[
            FlowLink(
                link_id="definition-to-approval",
                source_step_id="definition", source_path="data.id",
                target_step_id="approval", target_path="query.definitionId",
                confirmed=True, meta={"captured_value_match": True},
            ),
            FlowLink(
                link_id="approval-to-submit",
                source_step_id="approval", source_path="data.nodes",
                target_step_id="submit", target_path="assignees",
                kind="response_key_map", confidence=0.99,
                meta={"captured_structure_match": True},
            ),
            FlowLink(
                link_id="unproven",
                source_step_id="definition", source_path="data.name",
                target_step_id="submit", target_path="reason",
            ),
        ],
    )

    exported = _verified_links(spec, ["definition", "approval", "submit"])

    assert [link["link_id"] for link in exported] == [
        "definition-to-approval", "approval-to-submit",
    ]
    assert exported[0]["verification_id"] == ""


def test_package_slug_keeps_long_action_ids_distinct() -> None:
    shared = "very-long-business-system-name-" * 3

    first = package_slug(f"{shared}.action_{'a' * 32}")
    second = package_slug(f"{shared}.action_{'b' * 32}")

    assert first != second


def test_capability_script_name_cannot_shadow_python_stdlib(tmp_path) -> None:
    skill = _recording_skill("https://example.invalid")
    skill.api_request["capabilities"][0]["name"] = "inspect"
    skill.api_request["capabilities"][0]["title"] = "Inspect item"

    package = tmp_path / render_skill_package(skill, str(tmp_path), tenant="tenant-a")
    contract = json.loads((package / "references" / "CONTRACT.json").read_text(encoding="utf-8"))
    inspect_capability = next(item for item in contract["capabilities"] if item["name"] == "inspect")

    assert inspect_capability["script"] != "scripts/inspect.py"
    assert validate_skill_package(package) == {"ok": True, "issues": []}


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


class _AlternateBusinessApi(BaseHTTPRequestHandler):
    records = [{"recordKey": "R1", "label": "seed"}]

    def _send(self, payload):  # noqa: ANN001
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        self._send({"success": True, "items": list(self.records)})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.records.insert(0, payload)
        self._send({"success": True, "item": payload})

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
                    "status": "passed",
                    "evidence": {"passed": True},
                },
                {
                    "verification_id": _WRITE_VERIFICATION,
                    "kind": "write_execute",
                    "status": "passed",
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


def _alternate_recording_skill(origin: str) -> SkillSpec:
    base = _recording_skill(origin)
    spec = FlowSpec.model_validate(base.api_request["_release_snapshot"]["flow_spec"])
    spec.flow_id = "alternate-records"
    spec.title = "Alternate records"
    query, create = spec.steps
    query.name = "List records"
    query.url = f"{origin}/records"
    query.path = "/records"
    query.success_rule = {"field": "success", "ok_values": [True]}
    query.response_json = {"success": True, "items": [{"recordKey": "R1", "label": "seed"}]}
    create.name = "Add record"
    create.url = f"{origin}/records"
    create.path = "/records"
    create.body_source = json.dumps({"recordKey": "R1", "label": "recorded"})
    create.body_template = {"recordKey": "R1", "label": "{{label}}"}
    create.params = [
        ParamField(
            path="recordKey", key="recordKey", value="R1",
            category="runtime_var", source_kind="previous_response",
            source={"kind": "previous_response", "step_id": "query", "response_path": "items.0.recordKey"},
            exposed_to_user=False,
        ),
        ParamField(path="label", key="label", value="recorded", category="user_param", source_kind="user_input"),
    ]
    create.success_rule = {"field": "success", "ok_values": [True]}
    create.fact_check = {
        "endpoint": "/records",
        "assertion": {"path": "items.0.label", "equals_input": "label"},
        "verification_id": _WRITE_VERIFICATION,
        "verified": True,
    }
    spec.links[0].source_path = "items.0.recordKey"
    spec.links[0].target_path = "recordKey"
    query_capability, create_capability = spec.capabilities
    query_capability.name = "list_records"
    query_capability.title = "List records"
    query_capability.nodes[0]["path"] = "/records"
    create_capability.name = "add_record"
    create_capability.title = "Add record"
    create_capability.nodes[0]["path"] = "/records"
    create_capability.nodes[1]["path"] = "/records"
    create_capability.input_schema = {
        "type": "object",
        "properties": {"label": {"type": "string", "description": "Record label"}},
        "required": ["label"],
    }
    spec.goal["intent"] = "List and add records"
    api_request, errors = flow_spec_to_api_request(spec)
    assert api_request is not None and errors == []
    api_request["_release_snapshot"] = {
        "protocol": "dano.recording_release.v1",
        "flow_spec": spec.model_dump(mode="json"),
    }
    return SkillSpec(
        skill_id="alternate.records",
        subsystem=Subsystem("alternate-admin"),
        action="records",
        risk_level=RiskLevel.L3,
        title="Alternate records",
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
        contract = json.loads((package / "references" / "CONTRACT.json").read_text(encoding="utf-8"))
        assert {item["name"] for item in contract["capabilities"]} == {"query_items", "create_item"}
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
        from dano.execution.page import wire_format as wire_format_module

        assert (scripts / "wire_format.py").read_text(encoding="utf-8") == Path(
            wire_format_module.__file__
        ).read_text(encoding="utf-8")
        assert _run(scripts / "query_items.py", env=env)["ok"] is True
        unconfirmed = _run(scripts / "create_item.py", "--name", "created", env=env)
        assert unconfirmed["ok"] is False
        assert unconfirmed["status"] == "need_confirm"
        assert _run(
            scripts / "create_item.py", "--name", "created", "--confirm", env=env,
        )["ok"] is True
        verified = _run(scripts / "verify_create_item.py", "--name", "created", env=env)
        assert verified == {"capability": "create_item", "ok": True, "issues": [], "checks": 1}

        consumer = Path(__file__).resolve().parents[2] / "consumer-poc" / "consumer.py"
        listed = subprocess.run(
            [sys.executable, str(consumer), "list", str(package)],
            env=env, capture_output=True, text=True, timeout=20, check=False,
        )
        assert listed.returncode == 0
        assert json.loads(listed.stdout)["ok"] is True
        consumed = subprocess.run(
            [
                sys.executable, str(consumer), "run", str(package), "create_item",
                "--input-json", json.dumps({"name": "consumer-created"}), "--confirm",
            ],
            env=env, capture_output=True, text=True, timeout=30, check=False,
        )
        consumed_result = json.loads(consumed.stdout)
        assert consumed.returncode == 0
        assert consumed_result["execution"]["ok"] is True
        assert consumed_result["verification"]["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_self_contained_client_executes_wire_computed_and_response_key_map(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    from dano.execution.page import wire_format as wire_format_module

    (scripts / "wire_format.py").write_text(
        Path(wire_format_module.__file__).read_text(encoding="utf-8"), encoding="utf-8",
    )
    config = json.dumps({"tenant": "tenant", "subsystem": "system", "base_url": "https://example.test"})
    client_path = scripts / "client.py"
    client_path.write_text(_CLIENT_TEMPLATE.replace("__CONFIG__", repr(config)), encoding="utf-8")
    sys.path.insert(0, str(scripts))
    try:
        module_spec = importlib.util.spec_from_file_location("generated_stage5_client", client_path)
        module = importlib.util.module_from_spec(module_spec)
        assert module_spec.loader is not None
        module_spec.loader.exec_module(module)
        sent = []

        def fake_http(method, path="", **kwargs):
            sent.append({"method": method, "path": path, **kwargs})
            data = (
                {"data": {"activityNodes": [
                    {"id": "Activity_runtime_leader", "name": "领导审批"},
                    {"id": "Activity_runtime_hr", "name": "HR审批"},
                ]}}
                if path == "/approval-detail" else {"code": 0}
            )
            return {"ok": True, "status": 200, "data": data}

        module.http_json = fake_http
        plan = {
            "steps": [
                {
                    "step_id": "detail", "method": "POST", "path": "/approval-detail",
                    "body_template": {
                        "startTime": "{{startTime}}", "endTime": "{{endTime}}",
                        "processVariablesStr": "{{__days}}", "day": "{{__day_count}}",
                    },
                    "runtime_fields": [
                        {
                            "name": "__days", "kind": "date_span_days_json",
                            "start_field": "startTime", "end_field": "endTime", "output_key": "day",
                        },
                        {
                            "name": "__day_count", "kind": "date_span_days",
                            "start_field": "startTime", "end_field": "endTime",
                        },
                    ],
                    "wire_formats": {"startTime": "epoch_ms", "endTime": "epoch_ms"},
                },
                {
                    "step_id": "submit", "method": "POST", "path": "/submit",
                    "body_template": {"startUserSelectAssignees": "{{approvers}}"},
                },
            ],
            "links": [{
                "link_id": "approval-map", "kind": "response_key_map",
                "source_step": 0, "source_collection_path": "data.activityNodes",
                "source_key_path": "id", "source_label_path": "name",
                "target_step": 1, "target_container_path": "startUserSelectAssignees",
                "value_binding": {
                    "kind": "caller_map_by_label", "input_field": "approvers",
                    "value_shape": "item_list",
                    "input_fields_by_label": {
                        "领导审批": "领导审批",
                        "HR审批": "HR审批",
                    },
                },
            }],
        }

        result = module.execute_plan(plan, {
            "startTime": "2026-08-06T00:00:00+08:00",
            "endTime": "2026-08-07T00:00:00+08:00",
            "领导审批": [200, 202],
            "HR审批": 201,
        })

        assert result["ok"] is True
        assert sent[0]["body"] == {
            "startTime": 1785945600000,
            "endTime": 1786032000000,
            "processVariablesStr": '{"day":1}',
            "day": 1,
        }
        assert sent[1]["body"]["startUserSelectAssignees"] == {
            "Activity_runtime_leader": [200, 202],
            "Activity_runtime_hr": [201],
        }
    finally:
        sys.path.remove(str(scripts))


def test_self_contained_client_omits_absent_optional_request_fields(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    from dano.execution.page import wire_format as wire_format_module

    (scripts / "wire_format.py").write_text(
        Path(wire_format_module.__file__).read_text(encoding="utf-8"), encoding="utf-8",
    )
    config = json.dumps({"tenant": "tenant", "subsystem": "system", "base_url": "https://example.test"})
    client_path = scripts / "client.py"
    client_path.write_text(_CLIENT_TEMPLATE.replace("__CONFIG__", repr(config)), encoding="utf-8")
    sys.path.insert(0, str(scripts))
    try:
        module_spec = importlib.util.spec_from_file_location("generated_optional_client", client_path)
        module = importlib.util.module_from_spec(module_spec)
        assert module_spec.loader is not None
        module_spec.loader.exec_module(module)
        sent = []

        def fake_http(method, path="", **kwargs):
            sent.append({"method": method, "path": path, **kwargs})
            return {"ok": True, "status": 200, "data": {"code": 0}}

        module.http_json = fake_http
        result = module.execute_plan({
            "steps": [{
                "step_id": "query",
                "method": "GET",
                "path": "/items",
                "query_template": {
                    "pageNo": "{{pageNo}}",
                    "type": "{{type}}",
                    "createTime[0]": "{{startTime}}",
                },
            }],
            "links": [],
        }, {"pageNo": 1})

        assert result["ok"] is True
        assert sent[0]["query"] == {"pageNo": 1}
    finally:
        sys.path.remove(str(scripts))


def test_self_contained_client_resolves_live_api_options_once_and_projects_fields(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    from dano.execution.page import wire_format as wire_format_module

    (scripts / "wire_format.py").write_text(
        Path(wire_format_module.__file__).read_text(encoding="utf-8"), encoding="utf-8",
    )
    config = json.dumps({"tenant": "tenant", "subsystem": "system", "base_url": "https://example.test"})
    client_path = scripts / "client.py"
    client_path.write_text(_CLIENT_TEMPLATE.replace("__CONFIG__", repr(config)), encoding="utf-8")
    sys.path.insert(0, str(scripts))
    try:
        module_spec = importlib.util.spec_from_file_location("generated_live_option_client", client_path)
        module = importlib.util.module_from_spec(module_spec)
        assert module_spec.loader is not None
        module_spec.loader.exec_module(module)
        sent = []

        def fake_http(method, path="", **kwargs):
            sent.append({"method": method, "path": path, **kwargs})
            if kwargs.get("url") == "https://example.test/applications/page":
                return {
                    "ok": True,
                    "status": 200,
                    "data": {"data": {"list": [
                        {"id": "entity-1", "billCode": "REQ-001", "processStatus": "running"},
                        {"id": "entity-2", "billCode": "REQ-002", "processStatus": "draft"},
                    ]}},
                }
            return {"ok": True, "status": 200, "data": {"code": 0}}

        module.http_json = fake_http
        source = {
            "source_url": "https://example.test/applications/page",
            "source_method": "GET",
            "value_key": "id",
            "label_key": "billCode",
        }
        result = module.execute_plan({
            "steps": [{
                "step_id": "update", "method": "POST", "path": "/applications/update",
                "body_template": {"id": "{{application}}", "status": "captured"},
                "selects": [{
                    **source,
                    "param": "application", "path": "id",
                    "field_projections": {"status": "processStatus"},
                }, {
                    **source,
                    "param": "applicationCopy", "path": "copyId",
                }],
            }],
            "links": [],
        }, {"application": "REQ-002", "applicationCopy": "REQ-001"})

        assert result["ok"] is True
        assert [item["method"] for item in sent] == ["GET", "POST"]
        assert sent[1]["body"] == {
            "id": "entity-2",
            "status": "draft",
        }
    finally:
        sys.path.remove(str(scripts))


def test_skill_package_preserves_live_option_source_contract(tmp_path):
    skill = _recording_skill("https://example.invalid")
    query = skill.api_request["steps"][0]
    query["selects"] = [{
        "param": "status",
        "path": "query.status",
        "source_url": "https://example.invalid/status/options",
        "source_method": "GET",
        "value_key": "value",
        "label_key": "label",
        "option_map": {"进行中": "running"},
    }]
    capability = skill.api_request["capabilities"][0]
    capability["input_schema"]["properties"]["status"] = {
        "type": "string",
        "format": "name-ref",
        "x-dano-option-source": {
            "source_url": "https://example.invalid/status/options",
            "source_method": "GET",
            "value_key": "value",
            "label_key": "label",
        },
        "x-options-snapshot": [{"label": "进行中", "value": "running"}],
        "x-enum-value-map": {"进行中": "running"},
    }

    package = tmp_path / render_skill_package(skill, str(tmp_path), tenant="tenant-a")
    contract = json.loads((package / "references" / "CONTRACT.json").read_text(encoding="utf-8"))
    plan = next(item for item in contract["capabilities"] if item["name"] == capability["name"])
    live_source = plan["input_schema"]["properties"]["status"]["x-dano-option-source"]

    assert live_source["source_url"] == "https://example.invalid/status/options"
    script = (package / plan["script"]).read_text(encoding="utf-8")
    assert '"source_url": "https://example.invalid/status/options"' in script
    assert '"value_key": "value"' in script
    options_md = (package / "references" / "OPTIONS.md").read_text(encoding="utf-8")
    assert "Live source: `GET https://example.invalid/status/options`" in options_md
    assert "not a fixed enum" in options_md


def test_unrelated_system_package_runs_without_tenant_pack_or_code_changes(tmp_path):
    _AlternateBusinessApi.records = [{"recordKey": "R1", "label": "seed"}]
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AlternateBusinessApi)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        skill = _alternate_recording_skill(origin)
        package = tmp_path / render_skill_package(skill, str(tmp_path), tenant="tenant-two")
        assert validate_skill_package(package) == {"ok": True, "issues": []}
        contract = json.loads((package / "references" / "CONTRACT.json").read_text(encoding="utf-8"))
        assert {item["name"] for item in contract["capabilities"]} == {"list_records", "add_record"}
        scripts = package / "scripts"
        env = {**os.environ, "DANO_AUTH_HEADERS": "{}"}
        assert _run(scripts / "list_records.py", env=env)["ok"] is True
        assert _run(
            scripts / "add_record.py", "--label", "second-system", "--confirm", env=env,
        )["ok"] is True
        verified = _run(scripts / "verify_add_record.py", "--label", "second-system", env=env)
        assert verified == {"capability": "add_record", "ok": True, "issues": [], "checks": 1}
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
    skill_md = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert "## Transport" in skill_md
    assert "ask_user_question" in skill_md
    assert "questions[]" in skill_md
    assert "references/CONTRACT.json" in skill_md
    assert "写能力" in skill_md and "--confirm" in skill_md
    assert "验证" in skill_md
    assert "## List output" in skill_md
    assert "## Identifier fields" in skill_md
    assert "## Fixed result presentation" in skill_md
    assert "## Security" in skill_md
    assert "## Limitations" in skill_md
    assert (folder / "references" / "CAPABILITIES.md").is_file()
    assert (folder / "references" / "OPTIONS.md").is_file()
    assert (folder / "scripts" / "format_list.py").is_file()


def test_skill_package_never_expands_empty_capability_to_all_steps(tmp_path):
    skill = _recording_skill("https://example.invalid")
    skill.api_request["capabilities"][0]["step_ids"] = []
    skill.api_request["capabilities"][0]["compiled_step_ids"] = []
    skill.api_request["capabilities"][0]["nodes"] = []

    with pytest.raises(ValueError, match="does not reference any compiled request step"):
        render_skill_package(skill, str(tmp_path), tenant="tenant-a")


def test_valid_model_docs_cannot_replace_deterministic_operational_rules(tmp_path):
    skill = _recording_skill("https://example.invalid")
    release = skill.api_request["_release_snapshot"]["flow_spec"]
    release["meta"]["skill_docs"] = {
        "skill_md": """---
name: minimal
description: minimal but structurally valid
---
## Transport
direct
## Preconditions
ready
## Steps
1. run
   Done when: done
## Branch exit
stop
## Pitfalls
- none
""",
        "reference_md": f"""# Reference
## API chain
- `query_items`: GET /items; verification_id: {_LINK_VERIFICATION}
- `create_item`: GET /items -> POST /items; verification_id: {_WRITE_VERIFICATION}
## Business hard rules
- keep facts
## Fallback browser steps
1. use visible labels
""",
    }

    folder = tmp_path / render_skill_package(skill, str(tmp_path), tenant="tenant-a")
    skill_md = (folder / "SKILL.md").read_text(encoding="utf-8")

    assert "ask_user_question" in skill_md
    assert "questions[]" in skill_md
    assert "references/CONTRACT.json" in skill_md
    assert "--confirm" in skill_md
    assert "minimal but structurally valid" not in skill_md


def test_self_contained_script_enforces_full_input_schema(tmp_path):
    skill = _recording_skill("https://example.invalid")
    create = next(
        cap for cap in skill.api_request["capabilities"]
        if cap["name"] == "create_item"
    )
    create["input_schema"]["properties"]["name"].update({
        "minLength": 3,
        "pattern": "^[A-Z]",
    })
    folder = tmp_path / render_skill_package(skill, str(tmp_path), tenant="tenant-a")

    completed = subprocess.run(
        [
            sys.executable,
            str(folder / "scripts" / "create_item.py"),
            "--name", "ab", "--confirm",
        ],
        cwd=folder / "scripts",
        env={**os.environ, "DANO_AUTH_HEADERS": "{}"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode != 0
    assert "too short" in completed.stderr


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
async def test_export_mode_defaults_to_self_contained_package(monkeypatch, tmp_path):
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

    assert await exports.write_exports("tenant-a", str(tmp_path)) == ["package-folder"]
    assert calls == ["package"]


@pytest.mark.asyncio
async def test_export_mode_forwards_an_exact_skill_selection(monkeypatch, tmp_path):
    import dano.export.agent_skills as exports
    import dano.export.skill_package.renderer as packages

    calls = []

    async def proxy(*_args, **kwargs):
        calls.append(("proxy", kwargs))
        return ["proxy-folder"]

    async def package(*_args, **kwargs):
        calls.append(("package", kwargs))
        return ["package-folder"]

    monkeypatch.setattr(exports, "write_skills", proxy)
    monkeypatch.setattr(packages, "write_skill_packages", package)

    result = await exports.write_exports(
        "tenant-a",
        str(tmp_path),
        mode="both",
        skill_ids={"system.action_unique"},
    )

    assert result == ["proxy-folder", "package-folder"]
    assert calls == [
        ("proxy", {
            "exclude_skill_ids": set(),
            "skill_ids": {"system.action_unique"},
        }),
        ("package", {"skill_ids": ["system.action_unique"]}),
    ]


@pytest.mark.asyncio
async def test_export_mode_rejects_unknown_value(tmp_path):
    from dano.export.agent_skills import write_exports

    with pytest.raises(ValueError, match="proxy/package/both"):
        await write_exports("tenant-a", str(tmp_path), mode="unknown")
