"""导出文件夹名(_slug):中文动作名也要唯一,不能塌成同一目录互相覆盖。"""
from __future__ import annotations

import pytest

from dano.export.agent_skills import _PROTOTYPE_SUBSYSTEMS, _slug, _tenant_subsystems
from dano.shared.enums import Subsystem


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
