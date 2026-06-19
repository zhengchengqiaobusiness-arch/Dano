"""M5 收尾验收:onboard 主路径切到 goal-loop(代码自动生成)。

确定性(PG + 注入 FakeCoder + 注入 fake 评审,无 LLM/网络):
- onboard(use_codegen=True) 对一个含 1 个 GET 业务动作的 swagger →
  自动为该读流程跑 goal-loop(编码→沙箱→漏洞→审核→发布)→ 产出 ADAPTER Skill;
- 报告 published_skills 含该动作;目录里它是 integration=adapter。
"""

from __future__ import annotations

import os

import pytest

BACK_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")

_SPEC = {
    "openapi": "3.0.0", "info": {"title": "t", "version": "1"},
    "paths": {
        "/leave/list": {"get": {"operationId": "leave_list", "summary": "请假列表",
                                "tags": ["请假"]}},
        "/login": {"post": {"operationId": "login", "summary": "登录"}},   # 基础设施 → 过滤
    },
}


@pytest.fixture(autouse=True)
async def _pg():
    os.environ["DANO_PG_DSN"] = BACK_DSN
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='ob6'")
    yield
    await close_pool()


class _PassBoard:
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        from dano.review.board import ReviewVerdict
        return [ReviewVerdict(r, m, True, []) for r, m in
                (("acceptance", "fa"), ("security", "fb"), ("compliance", "fc"))]


class _Coder:
    """读流程:产一个跑通的只读 adapter(返回 code 200)。"""
    async def generate(self, *, plan, feedback):  # noqa: ANN001
        return {"action": plan.flow, "strategy": plan.strategy,
                "source": "def run(inputs, creds):\n    return {'code': 200, 'rows': []}\n",
                "entry": "run", "success_rule": plan.success_rule}


async def test_onboard_codegen_publishes_adapter_skill():
    from dano.agent_tools import tools as T
    from dano.catalog.manifest import build_manifests
    from dano.onboarding import onboard
    from dano.orchestrator.skills import SkillRegistry
    from dano.assets.repository import AssetRepository
    from dano.shared.enums import Subsystem

    T.set_review_board(_PassBoard())
    try:
        report = await onboard(
            tenant="ob6", subsystem="A-OA", openapi=_SPEC,
            deploy={"base_url": "http://x", "auth": {"kind": "token"}},
            credentials={"token": "t"}, use_codegen=True, coder=_Coder())
    finally:
        T.set_review_board(None)

    assert report.status == "completed"
    assert "leave_list" in report.published_skills, report.published_skills

    # 目录:作为 integration=adapter 暴露
    reg = await SkillRegistry.from_store(AssetRepository(), tenant="ob6", subsystems=[Subsystem.OA])
    man = {m.name: m for m in build_manifests(reg.skills)}
    assert "A-OA.leave_list" in man and man["A-OA.leave_list"].integration == "adapter"
    assert man["A-OA.leave_list"].risk_level == "L1"   # GET 只读 → L1(否则三模型驳回上不了架)


async def test_onboard_codegen_emits_progress():
    """接入向导用的进度回调:plan / flow_start / published / flow_done 事件齐全。"""
    from dano.agent_tools import tools as T
    from dano.onboarding import onboard

    events: list[dict] = []
    T.set_review_board(_PassBoard())
    try:
        await onboard(tenant="ob6", subsystem="A-OA", openapi=_SPEC,
                      deploy={"base_url": "http://x", "auth": {"kind": "token"}},
                      credentials={"token": "t"}, use_codegen=True, coder=_Coder(),
                      progress=lambda ev: events.append(ev))
    finally:
        T.set_review_board(None)

    types = [e["type"] for e in events]
    assert "plan" in types and "flow_start" in types and "flow_done" in types
    assert any(e["type"] == "published" for e in events)
