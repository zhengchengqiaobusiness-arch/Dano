"""Lock the consumer Skill transport contract from CODEX_CONSUMER_SKILL_OUTPUT."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from dano.catalog.manifest import _ask_user_question_interaction_protocol
from dano.gateway.app import TokenUpsertReq, post_runtime_token
from dano.execution.page.flow_release import prepare_flow_release_candidate
from dano.execution.page.flow_spec import FlowSpec, flow_spec_to_api_request
from dano.export.skill_package import validate_skill_package
from dano.export.skill_package.auth_files import write_auth_local, write_auth_to_export_dir, write_runtime_json
from dano.onboarding.skill_generation.export import (
    build_export_skill_spec,
    export_published_recording_package,
    pack_skill4_artifacts,
)
from dano.onboarding.skill_generation.models import SkillGenerationRequest
from dano.onboarding.skill_generation.planner import propose_deterministic_plan
from tests.skill4_draft import MIN_FLOW_PY, write_skill4_draft

REPO = Path(__file__).resolve().parents[2]


def _issue_codes(result: dict) -> set[str]:
    return {str(item.get("code") or "") for item in result.get("issues") or []}


def test_reverse_links_do_not_block_export_compile() -> None:
    spec = FlowSpec.model_validate({
        "subsystem": "oa",
        "title": "查询汇报统计、新增工作日报",
        "capabilities": [
            {
                "capability_id": "cap_query",
                "name": "query_records",
                "title": "查询汇报统计",
                "kind": "query",
                "step_ids": ["step_query"],
                "request_refs": [{"step_id": "step_query", "usage": "execute"}],
                "input_schema": {
                    "type": "object",
                    "properties": {"keyword": {"type": "string"}},
                },
                "output_schema": {"type": "object"},
            },
            {
                "capability_id": "cap_create",
                "name": "create_record",
                "title": "新增工作日报",
                "kind": "create",
                "step_ids": ["step_create"],
                "request_refs": [{"step_id": "step_create", "usage": "execute"}],
                "input_schema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
                "output_schema": {"type": "object"},
            },
        ],
        "steps": [
            {
                "step_id": "step_create",
                "name": "新增",
                "method": "POST",
                "path": "/api/records",
                "url": "https://example.test/api/records",
                "params": [
                    {
                        "key": "title",
                        "path": "body.title",
                        "label": "标题",
                        "source_kind": "user_input",
                        "exposed_to_user": True,
                        "default_value": "示例",
                    }
                ],
            },
            {
                "step_id": "step_query",
                "name": "查询",
                "method": "GET",
                "path": "/api/records",
                "url": "https://example.test/api/records",
            },
        ],
        "links": [
            {
                "link_id": "ade88bba",
                "source_step_id": "step_create",
                "source_path": "response.data.id",
                "target_step_id": "step_query",
                "target_path": "query.id",
            },
            {
                "link_id": "98e8371d",
                "source_step_id": "step_query",
                "source_path": "response.data.id",
                "target_step_id": "step_create",
                "target_path": "body.statId",
            },
        ],
    })
    release_spec, _candidate = prepare_flow_release_candidate(spec)
    api_request, errors = flow_spec_to_api_request(
        release_spec, _prepared=True, _embed_capability_steps=True,
    )
    joined = "；".join(errors or [])
    assert "来源步骤必须早于目标步骤" not in joined
    assert api_request, errors
    request = SkillGenerationRequest(title=spec.title, business_description="查询后新增。")
    plan = propose_deterministic_plan(
        spec, request, {cap.capability_id for cap in spec.capabilities}, "fp-reverse-links",
    )
    skill = build_export_skill_spec(
        spec,
        tenant="test",
        skill_id="oa.action_reverse_links",
        title=spec.title,
        plan=plan,
    )
    assert skill.api_request.get("capabilities")


def test_validator_allows_auth_local_only_and_requires_flow_route(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    write_skill4_draft(
        pkg,
        **{
            "scripts/query_stats.py": "import json\nprint(json.dumps({'ok': True}))\n",
            "scripts/create_report.py": "import json\nprint(json.dumps({'ok': True}))\n",
            "scripts/search.py": None,
        },
    )
    (pkg / "scripts" / "search.py").unlink(missing_ok=True)
    write_runtime_json(pkg, tenant="demo", subsystem="oa", base_url="https://example.test")
    write_auth_local(pkg, {"Authorization": "Bearer only-in-local-file"})
    (pkg / "references").mkdir(exist_ok=True)
    (pkg / "references" / "CONTRACT.json").write_text(
        json.dumps({
            "capabilities": [
                {"name": "query_stats", "title": "查询汇报统计", "kind": "query"},
                {"name": "create_report", "title": "新增工作日报", "kind": "create"},
            ],
            "routes": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (pkg / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 查询汇报统计并新增工作日报\n---\n\n"
        "# demo\n\nBearer leaked-token-should-fail-here\n",
        encoding="utf-8",
    )
    result = validate_skill_package(pkg)
    codes = _issue_codes(result)
    assert "missing_flow" not in codes
    assert "missing_default_route" in codes
    assert "credential_leak" in codes

    (pkg / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 查询汇报统计并新增工作日报\n---\n\n# demo\n",
        encoding="utf-8",
    )
    (pkg / "references" / "CONTRACT.json").write_text(
        json.dumps({
            "capabilities": [
                {"name": "query_stats", "title": "查询汇报统计", "kind": "query"},
                {"name": "create_report", "title": "新增工作日报", "kind": "create"},
            ],
            "routes": [{"route_id": "query_then_create", "operation_sequence": ["query_stats", "create_report"]}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    allowed = validate_skill_package(pkg)
    assert "credential_leak" not in _issue_codes(allowed)
    assert "missing_default_route" not in _issue_codes(allowed)

    (pkg / "scripts" / "flow.py").unlink()
    missing_flow = validate_skill_package(pkg)
    assert "missing_flow" in _issue_codes(missing_flow)


def test_validator_rejects_input_forms_that_drop_datasource(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    write_skill4_draft(pkg)
    write_runtime_json(pkg, tenant="demo", subsystem="oa", base_url="")
    forms = pkg / "references" / "INPUT_FORMS.md"
    forms.parent.mkdir(parents=True, exist_ok=True)
    forms.write_text(
        "## 新增工作日报\n\n"
        "把脚本返回的 options 填进 question 后，从本次工具参数中移除该 question 的 dataSource。\n",
        encoding="utf-8",
    )
    result = validate_skill_package(pkg)
    assert "input_form_drop_datasource" in _issue_codes(result)

    forms.write_text(
        "## 新增工作日报\n\n"
        "动态字段必须保留完整 `dataSource`。禁止删除 dataSource。\n"
        "```json\n"
        "{\"id\":\"deptId\",\"dataSource\":{\"type\":\"api\",\"endpoint\":\"/api/dept\"}}\n"
        "```\n",
        encoding="utf-8",
    )
    kept = validate_skill_package(pkg)
    assert "input_form_drop_datasource" not in _issue_codes(kept)


async def test_post_runtime_token_rewrites_exported_packages(tmp_path: Path, monkeypatch) -> None:
    pkg = tmp_path / "oa-query-report"
    write_runtime_json(pkg, tenant="acme", subsystem="oa", base_url="https://example.test")
    write_auth_local(pkg, {"Authorization": "Bearer old-token"})

    async def fake_auth(_key):  # noqa: ANN001
        return "acme"

    async def fake_update(tenant, subsystem, headers, *, source, **_kwargs):  # noqa: ANN001
        return {
            "headers": headers,
            "updated_at": "2026-09-09T00:00:00Z",
            "source": source,
        }

    monkeypatch.setattr("dano.gateway.app._auth_tenant", fake_auth)
    monkeypatch.setattr("dano.infra.token_store.update_token_headers", fake_update)
    monkeypatch.setattr("dano.gateway.app._known_export_dirs", lambda: [str(tmp_path)])

    result = await post_runtime_token(
        TokenUpsertReq(tenant="acme", subsystem="oa", headers={"Authorization": "Bearer page-token"}),
        x_tenant_key="k",
    )
    assert result["ok"] is True
    auth = json.loads((pkg / "config" / "auth.local.json").read_text(encoding="utf-8"))
    assert auth["headers"]["Authorization"] == "Bearer page-token"


def test_token_save_rewrites_exported_auth_local(tmp_path: Path) -> None:
    pkg = tmp_path / "oa-query-report"
    write_runtime_json(pkg, tenant="acme", subsystem="oa", base_url="https://example.test")
    write_auth_local(pkg, {"Authorization": "Bearer old-token"})
    handbook = pkg / "SKILL.md"
    handbook.write_text("do-not-touch\n", encoding="utf-8")
    updated = write_auth_to_export_dir(
        str(tmp_path),
        tenant="acme",
        subsystem="oa",
        headers={"Authorization": "Bearer new-token"},
    )
    assert updated == [pkg.name]
    auth = json.loads((pkg / "config" / "auth.local.json").read_text(encoding="utf-8"))
    assert auth["headers"]["Authorization"] == "Bearer new-token"
    assert handbook.read_text(encoding="utf-8") == "do-not-touch\n"
    assert write_auth_to_export_dir(
        str(tmp_path / "missing"),
        tenant="acme",
        subsystem="oa",
        headers={"Authorization": "Bearer newer"},
    ) == []


def test_reexport_packs_skill4_only(tmp_path: Path) -> None:
    recording_id = "rec_skill4_reexport"
    artifacts = write_skill4_draft(
        REPO / "Pi_check" / "data" / recording_id / "skill-artifacts",
        **{"SKILL.md": "---\nname: kept\ndescription: 查询汇报统计并新增工作日报\n---\n\n# kept\n"},
    )
    try:
        skill = SimpleNamespace(
            skill_id="oa.action_reexport_skill4",
            subsystem=SimpleNamespace(value="oa"),
            api_request={
                "steps": [{"url": "https://office.example/api/page"}],
                "_release_snapshot": {
                    "flow_spec": {"meta": {"recording_id": recording_id}},
                },
            },
            call_metadata={},
        )
        slug = export_published_recording_package(
            skill,
            str(tmp_path),
            tenant="acme",
            auth_headers={"Authorization": "Bearer from-page"},
        )
        assert slug
        packed = tmp_path / slug
        assert (packed / "SKILL.md").read_text(encoding="utf-8").startswith("---\nname: kept")
        assert json.loads((packed / "config" / "runtime.json").read_text(encoding="utf-8"))["base_url"] == (
            "https://office.example"
        )
        assert json.loads((packed / "config" / "auth.local.json").read_text(encoding="utf-8"))["headers"] == {
            "Authorization": "Bearer from-page",
        }
        assert not (packed / "references" / "generator-guides").exists()
        bare = SimpleNamespace(
            skill_id="oa.action_no_draft",
            subsystem=SimpleNamespace(value="oa"),
            api_request={},
            call_metadata={},
        )
        assert export_published_recording_package(bare, str(tmp_path), tenant="acme") is None
    finally:
        import shutil

        shutil.rmtree(artifacts.parent, ignore_errors=True)


def test_query_and_create_default_route_is_listed_by_flow_help(tmp_path: Path) -> None:
    import subprocess
    import sys

    src = write_skill4_draft(
        tmp_path / "draft",
        **{
            "references/CONTRACT.json": json.dumps({
                "capabilities": [
                    {"name": "query_stats", "title": "查询汇报统计", "kind": "query"},
                    {"name": "create_report", "title": "新增工作日报", "kind": "create"},
                ],
                "routes": [{
                    "route_id": "query_then_create",
                    "name": "查询汇报统计后新增工作日报",
                    "operation_sequence": ["query_stats", "create_report"],
                }],
            }, ensure_ascii=False),
        },
    )
    slug = pack_skill4_artifacts(
        src,
        str(tmp_path / "out"),
        skill_id="oa.action_flow_help",
        tenant="acme",
        base_url="https://example.test",
        auth_headers={},
    )
    packed = tmp_path / "out" / slug
    completed = subprocess.run(
        [sys.executable, str(packed / "scripts" / "flow.py"), "--help"],
        cwd=str(packed / "scripts"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "query_then_create" in (completed.stdout + completed.stderr)
    result = validate_skill_package(packed)
    assert "missing_default_route" not in _issue_codes(result)
    assert "missing_flow" not in _issue_codes(result)


def test_pack_does_not_rewrite_skill4_handbook(tmp_path: Path) -> None:
    src = write_skill4_draft(tmp_path / "draft")
    slug = pack_skill4_artifacts(
        src,
        str(tmp_path / "out"),
        skill_id="oa.action_pack_keep",
        tenant="acme",
        base_url="https://example.test",
        auth_headers={},
    )
    packed = tmp_path / "out" / slug
    assert "kept-handbook" in (packed / "SKILL.md").read_text(encoding="utf-8")
    assert (packed / "scripts" / "flow.py").read_text(encoding="utf-8") == MIN_FLOW_PY
    assert not (packed / "references" / "generator-guides").exists()


def test_ask_user_question_guide_shapes_not_regressed() -> None:
    guide = (REPO / "doc" / "skill-generator-ask-user-question-guide.md").read_text(encoding="utf-8")
    assert "## 三种调用形状" in guide
    assert "formIds" in guide
    assert "cancelled" in guide
    assert "confirm" in guide
    proto = _ask_user_question_interaction_protocol()
    assert proto["confirmation"]["allowed_keys"] == ["formIds", "confirm"]
    assert proto["result_statuses"] == ["answered", "confirmed", "cancelled"]
    assert "dataSource" in proto["single_field_collection"]["keys"]
    assert proto["cancel_behavior"].startswith("stop_current_workflow")


def test_validator_rejects_get_session_and_unbranched_skill4_protocol(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    write_skill4_draft(
        pkg,
        **{
            "SKILL.md": (
                "---\nname: demo\ndescription: 查询汇报统计并新增工作日报\n---\n\n"
                "## 适用场景\n\n- 查统计或填日报\n\n"
                "## 选择工作流\n\n完整办理\n\n"
                "## 执行协议\n\n1. 先加载组织机构树。**Done when:** 树返回。\n\n"
                "## 按需读取资源\n\n- 读 INPUT_FORMS\n"
            ),
            "scripts/query_stats.py": (
                "import json\nfrom client import get_session\n"
                "print(json.dumps({'ok': True}))\n"
            ),
            "scripts/create_report.py": "import json\nprint(json.dumps({'ok': True}))\n",
            "scripts/search.py": None,
        },
    )
    (pkg / "scripts" / "search.py").unlink(missing_ok=True)
    write_runtime_json(pkg, tenant="demo", subsystem="oa", base_url="https://example.test")
    (pkg / "references").mkdir(exist_ok=True)
    (pkg / "references" / "CONTRACT.json").write_text(
        json.dumps({
            "routes": [
                {"route_id": "default", "operation_sequence": ["query_stats", "create_report"]},
                {"route_id": "submit_only", "operation_sequence": ["create_report"]},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    codes = _issue_codes(validate_skill_package(pkg))
    assert "forbidden_client_api" in codes
    assert "skill_section" in codes
    assert "protocol_route_branch" in codes

    (pkg / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 查询汇报统计并新增工作日报\n---\n\n"
        "## 适用场景\n\n- 查统计或填日报\n\n"
        "## 选择工作流\n\n完整办理\n\n"
        "## 执行协议\n\n"
        "路线 submit_only\n\n1. 只收集日报字段。**Done when:** 已确认。\n\n"
        "路线 default\n\n1. 先查询再交接。**Done when:** 查询完成。\n\n"
        "## 成功、失败与停止\n\n空鉴权则停止。\n\n"
        "## 按需读取资源\n\n- 读 INPUT_FORMS\n",
        encoding="utf-8",
    )
    (pkg / "scripts" / "query_stats.py").write_text(
        "from client import http_json\nimport json\nprint(json.dumps({'ok': True}))\n",
        encoding="utf-8",
    )
    allowed = _issue_codes(validate_skill_package(pkg))
    assert "forbidden_client_api" not in allowed
    assert "protocol_route_branch" not in allowed
    assert not any(
        item.get("code") == "skill_section" and "成功、失败与停止" in str(item.get("message") or "")
        for item in validate_skill_package(pkg).get("issues") or []
    )


def test_work_report_export_package_follows_consumer_contract() -> None:
    import subprocess
    import sys

    pkg = REPO / "export" / "action_f55c9e2b7fd042a1a3cc5725ab670906"
    result = validate_skill_package(pkg)
    codes = _issue_codes(result)
    assert result["ok"], result["issues"]
    assert "forbidden_client_api" not in codes
    assert "protocol_route_branch" not in codes
    handbook = (pkg / "SKILL.md").read_text(encoding="utf-8")
    assert "## 成功、失败与停止" in handbook
    assert "submit_only" in handbook
    assert "query_only" in handbook
    forms = (pkg / "references" / "INPUT_FORMS.md").read_text(encoding="utf-8")
    assert "inputType\": \"table\"" in forms or '"inputType": "table"' in forms
    assert "dataSource" in forms
    query = (pkg / "scripts" / "query_statistics.py").read_text(encoding="utf-8")
    submit = (pkg / "scripts" / "submit_report.py").read_text(encoding="utf-8")
    assert "from client import http_json" in query
    assert "from client import http_json" in submit
    assert "get_session" not in query
    assert "get_session" not in submit
    help_run = subprocess.run(
        [sys.executable, str(pkg / "scripts" / "flow.py"), "--help"],
        cwd=str(pkg / "scripts"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_run.returncode == 0, help_run.stderr
    assert "submit_only" in help_run.stdout
    verify = subprocess.run(
        [sys.executable, str(pkg / "scripts" / "verify_submit_report.py")],
        cwd=str(pkg / "scripts"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert json.loads(verify.stdout)["ok"] is True


def test_task_book_stays_out_of_doc() -> None:
    assert not (REPO / "doc" / "CODEX_CONSUMER_SKILL_OUTPUT.md").exists()
    skill_dir = REPO / "Pi_check" / "skill"
    names = sorted(path.name for path in skill_dir.glob("*.md"))
    assert names == [
        "BUILD_AND_VALIDATE_DEDICATED_SKILL.md",
        "BUSINESS_SKILL_INVESTIGATOR.md",
        "CONTROL_IN_APP_BROWSER.md",
        "INFER_BUSINESS_CONTRACT.md",
    ]
    assert "CODEX_CONSUMER_SKILL_OUTPUT.md" not in names
