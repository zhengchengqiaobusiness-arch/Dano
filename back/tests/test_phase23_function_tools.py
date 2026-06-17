"""M-补齐 验收:Skill 以 function-calling tools 暴露给前端,且工具调用可执行。

确定性(PG + 纯计算 adapter,经网关):
- GET /v1/tools 返回 OpenAI function-calling tools(name=skill_id 点转 __,带 parameters schema);
- POST /v1/tools/call {name, arguments} → 反向映射 skill_id → 受控执行 → completed。
"""

from __future__ import annotations

import os

import httpx
import pytest

BACK_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")


def test_tool_name_roundtrip():
    from dano.catalog.manifest import skill_id_of, tool_name_of
    assert tool_name_of("A-OA.submit_leave") == "A-OA__submit_leave"
    assert skill_id_of("A-OA__submit_leave") == "A-OA.submit_leave"


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
        await c.execute("DELETE FROM assets WHERE tenant='tools1'")
    import dano.gateway.app as gw
    from dano.lifecycle.state_machine import SkillLifecycle
    gw._lifecycle = SkillLifecycle()       # in-memory(无 lifespan)
    yield
    await close_pool()


async def _publish_adapter():
    from dano.assets.repository import AssetRepository
    from dano.shared.enums import AssetType, Subsystem, ValidationStatus
    from dano.shared.models import AssetEnvelope, Scope
    repo = AssetRepository()
    body = {"action": "submit_leave", "title": "提交请假", "strategy": "workflow_bpmn",
            "source": "def run(inputs, creds):\n    return {'code': 200, 'echo': inputs.get('title')}\n",
            "entry": "run", "success_rule": "response.code == 200",
            "user_fields": ["title"], "required_fields": ["title"], "risk_level": "L3"}
    e = await repo.create(AssetEnvelope(
        asset_type=AssetType.ADAPTER, scope=Scope(tenant="tools1", subsystem=Subsystem.OA),
        asset_key="submit_leave", version=0, source_fingerprint="t",
        validation_status=ValidationStatus.VERIFIED, confidence=0.95, body=body))
    await repo.set_status(e.asset_id, ValidationStatus.PUBLISHED)


def _client():
    import dano.gateway.app as gw
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://t")


async def test_tools_listed_and_called():
    await _publish_adapter()
    c = _client()
    try:
        key = (await c.post("/tenants", json={"tenant": "tools1"})).json()["api_key"]
        h = {"X-Tenant-Key": key}

        # ① 列工具:OpenAI function-calling 格式
        tools = (await c.get("/v1/tools", headers=h)).json()
        tool = next(t for t in tools if t["function"]["name"] == "A-OA__submit_leave")
        assert tool["type"] == "function"
        assert "title" in tool["function"]["parameters"]["properties"]
        assert tool["function"]["parameters"]["required"] == ["title"]

        # ② 执行工具调用(name 反映射 skill_id;arguments 可为 JSON 字符串)
        r = await c.post("/v1/tools/call", headers=h,
                         json={"name": "A-OA__submit_leave",
                               "arguments": '{"title": "张三年假"}', "confirm": True})
        out = r.json()
        assert out["state"] == "completed", out
        assert out["exec_result"]["structured_output"]["echo"] == "张三年假"
    finally:
        await c.aclose()
