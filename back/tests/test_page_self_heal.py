"""M3 页面直驱:漂移自愈(operate-page 自主重驱动)。全离线,pi 重驱动用 fake 替身。

对应 doc/PAGE_NATIVE_AGENT.md §9 / §13。承重点:
- 页面直驱 skill(带 goal)漂移 → Agent **自主重驱动**重结晶(非 scout 一次);
- 灰度:重驱动跑通发布新版本才把 Skill 恢复到已发布;跑不通 → 旧版不动(安全回滚)。
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from dano.agent_tools import materials, runs
from dano.agent_tools import tools
from dano.assets.repository import AssetRepository
from dano.assurance import service
from dano.assurance.service import page_heal_strategy
from dano.lifecycle.state_machine import InMemorySkillStore, SkillLifecycle
from dano.shared.enums import SkillState, Subsystem

_SID = Subsystem.REIMBURSE.value


# ───────────────────────── 策略路由(纯函数)─────────────────────────

def test_page_heal_strategy_agent_when_goal_present():
    assert page_heal_strategy({"goal": {"intent": "提交一张请假单"}, "start_url": "/x"}) == "agent"


def test_page_heal_strategy_scout_without_goal():
    assert page_heal_strategy({"start_url": "/x"}) == "scout"
    assert page_heal_strategy({"goal": {}}) == "scout"          # 空 goal → 老 scout 补丁路径


# ───────────────────────── 自主重驱动自愈(Stagehand 重驱动用 fake 替身)─────────────────────────

def _fake_env(goal: str = "提交一张请假单"):
    return SimpleNamespace(body={"goal": {"intent": goal}, "start_url": "http://x/leave",
                                 "action": "submit_leave"})


async def _seed_suspended(action: str) -> SkillLifecycle:
    lc = SkillLifecycle(InMemorySkillStore())
    skill_id = f"{_SID}.{action}"
    await lc.register_published(skill_id, Subsystem(_SID), action, version=1)
    await lc.suspend(skill_id)                                  # 漂移 → 暂停,等自愈
    return lc


async def test_reheal_dispatches_to_agent_redrive_and_recovers(monkeypatch):
    """带 goal 的页面直驱 skill 漂移 → _reheal_page 走 Agent 自主重驱动;重驱动跑通 → 恢复到已发布(新版本)。"""
    action = "submit_leave"

    async def fake_get_published(self, *a, **k):  # noqa: ANN001
        return _fake_env()

    async def fake_redrive(*, tenant, subsystem, start_url, goal, **k):  # noqa: ANN001
        assert goal == "提交一张请假单" and start_url == "http://x/leave"   # 凭原 goal 重驱动
        return {"published_skills": [action], "pi_status": "ok"}

    monkeypatch.setattr(AssetRepository, "get_published", fake_get_published)
    monkeypatch.setattr("dano.onboarding.page_onboard.run_page_agent_onboarding", fake_redrive)
    lc = await _seed_suspended(action)

    ok = await service._reheal_page("rid", _SID, _SID, action, "t-heal", lc)
    assert ok is True
    rec = await lc.store.get(f"{_SID}.{action}")
    assert rec.state == SkillState.PUBLISHED and rec.asset_version == 2   # 灰度恢复 + 版本递增


async def test_reheal_agent_redrive_fail_keeps_old(monkeypatch):
    """重驱动没把这条业务重新跑通发布 → 自愈失败,Skill 留暂停、旧版不动(安全)。"""
    action = "submit_leave"

    async def fake_get_published(self, *a, **k):  # noqa: ANN001
        return _fake_env()

    async def fake_redrive(**k):  # noqa: ANN001
        return {"published_skills": [], "pi_status": "failed"}   # 没跑通

    monkeypatch.setattr(AssetRepository, "get_published", fake_get_published)
    monkeypatch.setattr("dano.onboarding.page_onboard.run_page_agent_onboarding", fake_redrive)
    lc = await _seed_suspended(action)

    ok = await service._reheal_page("rid", _SID, _SID, action, "t-heal", lc)
    assert ok is False
    rec = await lc.store.get(f"{_SID}.{action}")
    assert rec.state == SkillState.SUSPENDED and rec.asset_version == 1   # 旧版不动,未误恢复
