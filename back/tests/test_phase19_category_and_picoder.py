"""M5(可确定性验证部分):类别控制 + PiCoder 形态驱动闭环。

- parse_spec 按 include_tags 过滤(超大 swagger 圈范围);categories 统计全量;
- /onboarding/preview 按 tag 返回类别清单(只解析,不 spawn pi,不碰凭证);
- PiCoder(注入 fake spawn)按真实生成器形态驱动 GenerationLoop:首版 buggy→驳回→修复→发布。
真实 pi 端到端(需 DANO_PI_API_KEY)是 M5 的真跑部分,不在确定性测试内。
"""

from __future__ import annotations

import os

import httpx
import pytest

BACK_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")

_SPEC = {
    "openapi": "3.0.0", "info": {"title": "t", "version": "1"},
    "paths": {
        "/a/list": {"get": {"operationId": "a_list", "summary": "A 列表", "tags": ["类别A"]}},
        "/b/do": {"post": {"operationId": "b_do", "summary": "B 操作", "tags": ["类别B"]}},
        "/login": {"post": {"operationId": "login", "summary": "登录"}},   # 基础设施 → 过滤
    },
}


# ── 类别过滤(无 PG)──
async def test_parse_spec_filters_by_include_tags():
    from dano.agent_tools import materials, tools as T
    materials.register(materials.MaterialContext(
        run_id="m5-cat", tenant="m5", system_instance_id="A-OA", subsystem="A-OA",
        openapi=_SPEC, deploy={}, credentials={}))
    out = await T.parse_spec("m5-cat", {"system_instance_id": "A-OA", "include_tags": ["类别A"]})
    names = [a["name"] for a in out["actions"]]
    assert names == ["a_list"]                          # 只保留所选类别
    assert out["categories"].get("类别A") == 1 and out["categories"].get("类别B") == 1  # 统计全量
    # 不传白名单 → 全部业务动作(基础设施 login 仍被过滤)
    out_all = await T.parse_spec("m5-cat", {"system_instance_id": "A-OA"})
    assert {a["name"] for a in out_all["actions"]} == {"a_list", "b_do"}


async def test_onboarding_preview_returns_categories():
    import dano.gateway.app as gw
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://t") as c:
        r = await c.post("/onboarding/preview", json={"openapi": _SPEC})
    body = r.json()
    assert body["business_action_count"] == 2
    tags = {row["tag"]: row["count"] for row in body["categories"]}
    assert tags.get("类别A") == 1 and tags.get("类别B") == 1


# ── PiCoder 形态驱动闭环(PG)──
@pytest.fixture()
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
        await c.execute("DELETE FROM assets WHERE tenant='m5'")
    yield
    await close_pool()


class _PassBoard:
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        from dano.review.board import ReviewVerdict
        return [ReviewVerdict(r, m, True, []) for r, m in
                (("acceptance", "fa"), ("security", "fb"), ("compliance", "fc"))]


class _FakeSpawn:
    """模拟 pi:首版 buggy(code 500);prompt 含驳回反馈后产修复版(code 200)。"""
    def __init__(self): self.prompts = []
    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        code = 200 if "驳回" in prompt else 500
        return f"<ADAPTER>\ndef run(inputs, creds):\n    return {{'code': {code}}}\n</ADAPTER>"


async def test_picoder_drives_loop_reject_then_fix(_pg):
    from dano.agent_tools import materials, tools as T
    from dano.assets.repository import AssetRepository
    from dano.generation import GenerationLoop, GoalBrief, PiCoder
    from dano.generation.strategies import select_strategy
    from dano.shared.enums import AssetType, Subsystem
    from dano.shared.models import Scope

    run_id = "m5-pi"
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="m5", system_instance_id="A-OA", subsystem="A-OA",
        openapi={}, deploy={"base_url": "http://x", "auth": {"kind": "token"}},
        credentials={"token": "t"}))
    goal = GoalBrief(run_id=run_id, system_instance_id="A-OA", flow="misc_flow",
                     actions=[{"name": "do", "method": "POST", "endpoint": "/misc/do"}],
                     test_input={})
    spawn = _FakeSpawn()
    T.set_review_board(_PassBoard())
    try:
        res = await GenerationLoop(PiCoder(spawn=spawn)).run(goal, select_strategy(goal.actions))
    finally:
        T.set_review_board(None)

    assert res.ok is True and res.rejections >= 1, res
    assert res.iterations[0].passed is False and res.iterations[-1].passed is True
    assert len(spawn.prompts) >= 2 and "驳回" in spawn.prompts[1]   # 驳回原因确实回灌给 pi
    repo = AssetRepository()
    pubs = await repo.list_published(AssetType.ADAPTER, Scope(tenant="m5", subsystem=Subsystem.OA))
    assert [e.asset_key for e in pubs] == ["misc_flow"], pubs
