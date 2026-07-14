"""导出文件夹名(_slug):中文动作名也要唯一,不能塌成同一目录互相覆盖。"""
from __future__ import annotations

import json

import pytest

from dano.catalog.manifest import to_manifest
from dano.export.agent_skills import (
    _PROTOTYPE_SUBSYSTEMS,
    _slug,
    _tenant_subsystems,
    _write_business_skill,
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
    readme = (folder / "references" / "README.md").read_text(encoding="utf-8")

    assert contract == manifest.model_dump(mode="json")
    assert len(contract["capabilities"]) == 2
    assert contract["capability_relations"][0]["automatic"] is False
    assert contract["capabilities"][1]["input_schema"]["properties"]["entries"]["type"] == "array"
    assert "CONTRACT.json" in readme
    assert "## 执行与判定" not in readme

    bundle = _write_business_skill(tmp_path, "A-OA", "日报", [manifest])
    bundle_contract = json.loads((bundle / "references" / "CONTRACT.json").read_text(encoding="utf-8"))
    assert bundle_contract["protocol"] == "dano.skill_bundle.v1"
    assert bundle_contract["skills"] == [manifest.model_dump(mode="json")]


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
