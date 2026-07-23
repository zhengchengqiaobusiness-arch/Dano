"""导出文件夹名(_slug):中文动作名也要唯一,不能塌成同一目录互相覆盖。"""
from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest

from dano.catalog.manifest import to_manifest
from dano.export.agent_skills import (
    _PROTOTYPE_SUBSYSTEMS,
    _configured_reference_dir,
    _load_reference_markdown,
    _publish_folder,
    _slug,
    _tenant_subsystems,
    _write_business_skill,
    _write_index,
    _write_skill,
)
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem


def test_slug_english_action_readable():
    """纯英文 skill_id → 可读 kebab,不加哈希。"""
    assert _slug("A-OA.submit_leave") == "dano-a-oa-submit-leave"


def test_slug_chinese_actions_unique():
    """两个中文动作名(日报填写 / 请假)必须得到不同目录(否则导出互相覆盖,只剩一个)。"""
    a, b = _slug("A-OA.日报填写"), _slug("A-OA.请假")
    assert a != b
    assert a.startswith("dano-a-oa-") and b.startswith("dano-a-oa-")
    # 同一 skill_id 稳定(可重复导出)
    assert _slug("A-OA.日报填写") == a


def test_slug_is_portable_when_internal_id_is_long():
    slug = _slug("A-OA." + "very_long_action_name_" * 5)
    assert len(slug) <= 64
    assert slug == _slug("A-OA." + "very_long_action_name_" * 5)


def test_publish_folder_makes_generated_tree_readable_by_runtime_user(tmp_path, monkeypatch):
    """Linux 导出后会由另一个容器用户读取，目录不能保留 mkdtemp 的 0700。"""
    stage = tmp_path / ".dano-test-stage"
    target = tmp_path / "dano-test"
    nested = stage / "references"
    nested.mkdir(parents=True)
    (stage / "SKILL.md").write_text("test", encoding="utf-8")
    (nested / "CONTRACT.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "dano.export.agent_skills._validate_generated_skill",
        lambda *_args, **_kwargs: None,
    )
    permissions: dict[str, int] = {}
    monkeypatch.setattr(
        os,
        "chmod",
        lambda path, mode: permissions.__setitem__(
            str(path).replace("\\", "/").split("/")[-1],
            stat.S_IMODE(mode),
        ),
    )

    _publish_folder(stage, target, "dano-test")

    assert permissions[".dano-test-stage"] == 0o755
    assert permissions["references"] == 0o755
    assert permissions["SKILL.md"] == 0o644
    assert permissions["CONTRACT.json"] == 0o644


class _FakeRepo:
    def __init__(self, subs=None, *, raises=False):
        self._subs, self._raises = subs or [], raises

    async def distinct_subsystems(self, tenant: str):
        if self._raises:
            raise RuntimeError("no pg")
        return self._subs


@pytest.mark.asyncio
async def test_export_discovers_arbitrary_subsystems():
    """P0:导出按租户**真实系统**发现(任意系统),不限于三件套原型。"""
    repo = _FakeRepo([Subsystem("B-CRM"), Subsystem("C-门户")])
    got = await _tenant_subsystems(repo, "acme")
    assert [s.value for s in got] == ["B-CRM", "C-门户"]


@pytest.mark.asyncio
async def test_export_falls_back_to_prototypes_when_empty_or_no_db():
    """发现为空 / DB 不可用 → 退回原型常量兜底(不致导出整体失败,行为与旧版一致)。"""
    assert await _tenant_subsystems(_FakeRepo([]), "acme") == _PROTOTYPE_SUBSYSTEMS
    assert await _tenant_subsystems(_FakeRepo(raises=True), "acme") == _PROTOTYPE_SUBSYSTEMS


def test_write_skill_exports_lossless_contract_and_compact_navigation(tmp_path):
    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.export_contract",
        subsystem=Subsystem.OA,
        action="export_contract",
        risk_level=RiskLevel.L3,
        capability_relations=[{
            "from_capability": "query_status", "from_output": "dates",
            "to_capability": "submit_batch", "to_input": "entries",
        }],
        capabilities=[
            {"name": "query_status", "kind": "query_status",
             "output_schema": {"type": "object", "properties": {"dates": {"type": "array"}}}},
            {"name": "submit_batch", "kind": "submit_batch",
             "input_schema": {"type": "object", "properties": {"entries": {
                 "type": "array", "items": {"type": "object", "properties": {
                     "date": {"type": "string", "format": "date"},
                 }},
             }}}},
        ],
    ))

    folder = _write_skill(tmp_path, manifest)
    contract = json.loads((folder / "references" / "CONTRACT.json").read_text(encoding="utf-8"))
    skill_md = (folder / "SKILL.md").read_text(encoding="utf-8")
    openai_yaml = (folder / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert contract == manifest.model_dump(mode="json")
    assert len(contract["capabilities"]) == 2
    assert contract["capability_relations"][0]["automatic"] is False
    assert contract["capabilities"][1]["input_schema"]["properties"]["entries"]["type"] == "array"
    assert f'name: "{folder.name}"' in skill_md
    assert "metadata:" not in skill_md.split("---", 2)[1]
    assert f'display_name: {json.dumps(manifest.title or folder.name, ensure_ascii=False)}' in openai_yaml
    assert f"${folder.name}" in openai_yaml
    assert "ask_user_question" in skill_md
    assert not (folder / "references" / "README.md").exists()
    assert not (folder / "references" / "QUICKREF.md").exists()

    bundle = _write_business_skill(tmp_path, "A-OA", "日报", [manifest])
    bundle_contract = json.loads((bundle / "references" / "CONTRACT.json").read_text(encoding="utf-8"))
    assert bundle_contract["protocol"] == "dano.skill_bundle.v1"
    assert bundle_contract["skills"] == [manifest.model_dump(mode="json")]
    assert (bundle / "agents" / "openai.yaml").is_file()
    assert not (bundle / "references" / "README.md").exists()
    assert not (bundle / "references" / "QUICKREF.md").exists()


def test_export_uses_every_configured_markdown_as_generation_reference(tmp_path, monkeypatch):
    import dano.export.agent_skills as agent_skills

    source = tmp_path / "guidance"
    (source / "nested").mkdir(parents=True)
    (source / "contract.md").write_text(
        "ask_user_question questions default required", encoding="utf-8")
    (source / "nested" / "rules.MD").write_text(
        "dateFormat dataSource confirm cancelled validation error", encoding="utf-8")
    (source / "ignored.txt").write_text("not markdown", encoding="utf-8")
    monkeypatch.setattr(agent_skills, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        agent_skills,
        "get_settings",
        lambda: SimpleNamespace(skill_reference_dir="guidance"),
    )

    configured = _configured_reference_dir()
    references = _load_reference_markdown(configured)
    assert [path.as_posix() for path, _ in references] == ["contract.md", "nested/rules.MD"]

    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.reference_export",
        subsystem=Subsystem.OA,
        action="reference_export",
        risk_level=RiskLevel.L1,
    ))
    folder = _write_skill(tmp_path / "out", manifest, reference_docs=references)

    assert not (folder / "references" / "platform").exists()
    skill_md = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert "ask_user_question" in skill_md
    assert "多个表单" in skill_md

    legacy = folder / "references" / "platform"
    legacy.mkdir(parents=True)
    (legacy / "old.md").write_text("stale", encoding="utf-8")
    (folder / "scripts" / "__pycache__").mkdir()
    (folder / "stale.txt").write_text("stale", encoding="utf-8")
    _write_skill(tmp_path / "out", manifest, reference_docs=references)
    assert not legacy.exists()
    assert not (folder / "scripts" / "__pycache__").exists()
    assert not (folder / "stale.txt").exists()

    with pytest.raises(ValueError, match="日期格式"):
        _write_skill(tmp_path / "out", manifest, reference_docs=references[:1])

    bundle = _write_business_skill(
        tmp_path / "out", "A-OA", "reference", [manifest], reference_docs=references)
    assert not (bundle / "references" / "platform").exists()
    assert "ask_user_question" in (bundle / "SKILL.md").read_text(encoding="utf-8")

    index_slug = _write_index(
        tmp_path / "out", [{"label": "reference", "folder": bundle.name, "ops": 1}],
        reference_docs=references,
    )
    index = tmp_path / "out" / index_slug
    assert not (index / "references" / "platform").exists()
    assert (index / "agents" / "openai.yaml").is_file()


def test_linux_reference_configuration_uses_deployment_root(tmp_path, monkeypatch):
    import dano.export.agent_skills as agent_skills

    deployment_root = tmp_path / "opt" / "skillmanner" / "Dano"
    reference_dir = deployment_root / "doc"
    reference_dir.mkdir(parents=True)
    monkeypatch.setattr(agent_skills.sys, "platform", "linux")
    monkeypatch.setattr(agent_skills, "_LINUX_PROJECT_ROOT", deployment_root)

    monkeypatch.setattr(
        agent_skills,
        "get_settings",
        lambda: SimpleNamespace(skill_reference_dir="doc"),
    )
    assert _configured_reference_dir() == reference_dir.resolve()

    monkeypatch.setattr(
        agent_skills,
        "get_settings",
        lambda: SimpleNamespace(skill_reference_dir="/opt/skillmanner/Dano/doc"),
    )
    monkeypatch.setattr(
        agent_skills,
        "_LINUX_PROJECT_ROOT",
        agent_skills.Path("/opt/skillmanner/Dano"),
    )
    assert _configured_reference_dir().as_posix().endswith("/opt/skillmanner/Dano/doc")


def test_reference_configuration_rejects_absolute_missing_and_empty_directories(tmp_path, monkeypatch):
    import dano.export.agent_skills as agent_skills

    monkeypatch.setattr(agent_skills.sys, "platform", "win32")
    monkeypatch.setattr(
        agent_skills,
        "get_settings",
        lambda: SimpleNamespace(skill_reference_dir=str(tmp_path.resolve())),
    )
    with pytest.raises(ValueError, match="必须是相对"):
        _configured_reference_dir()

    monkeypatch.setattr(
        agent_skills,
        "get_settings",
        lambda: SimpleNamespace(skill_reference_dir="../outside"),
    )
    with pytest.raises(ValueError, match="不得超出"):
        _configured_reference_dir()

    with pytest.raises(FileNotFoundError, match="不存在"):
        _load_reference_markdown(tmp_path / "missing")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="没有 Markdown"):
        _load_reference_markdown(empty)


def test_non_batch_multi_capability_export_has_no_batch_or_fake_fact_check_residue(tmp_path):
    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.borrow_official_seal",
        subsystem=Subsystem.OA,
        action="borrow_official_seal",
        title="公章借阅",
        description="查询公章借阅记录并提交借阅申请",
        risk_level=RiskLevel.L3,
        capabilities=[
            {
                "name": "query_status", "kind": "query_status", "title": "查询公章借阅记录",
                "input_schema": {"type": "object", "properties": {}, "required": []},
                "output_schema": {"type": "object", "properties": {"result": {"type": "object"}}},
            },
            {
                "name": "submit", "kind": "submit", "title": "提交公章借阅申请",
                "requires_human_confirm": True,
                "inputs": [{
                    "key": "申请标题", "path": "applyTitle", "type": "string", "required": True,
                }],
                "input_schema": {
                    "type": "object",
                    "properties": {"申请标题": {"type": "string"}},
                    "required": ["申请标题"],
                },
                "output_schema": {"type": "object", "properties": {"result": {"type": "object"}}},
            },
        ],
    ))

    folder = _write_skill(tmp_path, manifest)
    skill_md = (folder / "SKILL.md").read_text(encoding="utf-8")
    contract = json.loads((folder / "references" / "CONTRACT.json").read_text(encoding="utf-8"))

    assert "批量输入按 `entries[]`" not in skill_md
    assert "`partial_success`" not in skill_md
    assert "真正的业务编排、风险闸门与事实核查" not in skill_md
    assert "业务成功规则" in skill_md
    submit = next(cap for cap in contract["capabilities"] if cap["kind"] == "submit")
    assert submit["inputs"][0]["required"] is True
    assert "page_required" not in submit["inputs"][0]
    assert "required_source" not in submit["inputs"][0]
