"""M6 验收:生成的 adapter Skill 能上目录 + 被调用(隔离 runner 执行 + 成败规则)。

确定性(PG,纯计算 adapter,无网络/无 LLM):
- 发布一个 ADAPTER → SkillRegistry 暴露为 integration=adapter;
- Orchestrator.invoke_skill 路由到隔离 runner 跑 source,过 success_rule → COMPLETED;
- source 跑挂/不满足成败规则 → FAILED(调用闸门不放水)。
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

BACK_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")


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
        await c.execute("DELETE FROM assets WHERE tenant='m6'")
    yield
    await close_pool()


async def _publish_adapter(source: str, success_rule: str | None):
    from dano.assets.repository import AssetRepository
    from dano.shared.enums import AssetType, Subsystem, ValidationStatus
    from dano.shared.models import AssetEnvelope, Scope
    repo = AssetRepository()
    body = {"action": "submit_leave", "title": "提交请假", "strategy": "workflow_bpmn",
            "source": source, "entry": "run", "success_rule": success_rule,
            "user_fields": ["title"], "required_fields": ["title"], "risk_level": "L3"}
    e = await repo.create(AssetEnvelope(
        asset_type=AssetType.ADAPTER, scope=Scope(tenant="m6", subsystem=Subsystem.OA),
        asset_key="submit_leave", version=0, source_fingerprint="t",
        validation_status=ValidationStatus.VERIFIED, confidence=0.95, body=body))
    await repo.set_status(e.asset_id, ValidationStatus.PUBLISHED)


def _orchestrator():
    from dano.execution.connectors.executor import FakeActionExecutor
    from dano.execution.harness.harness import Harness
    from dano.orchestrator.orchestrator import Orchestrator
    from dano.orchestrator.skills import SkillRegistry
    return SkillRegistry, Orchestrator, Harness, FakeActionExecutor


async def test_adapter_listed_and_invoked_completed():
    from dano.catalog.manifest import build_manifests
    from dano.assets.repository import AssetRepository
    from dano.shared.enums import Subsystem, TaskState

    SkillRegistry, Orchestrator, Harness, FakeExec = _orchestrator()
    await _publish_adapter(
        "def run(inputs, creds):\n    return {'code': 200, 'echo': inputs.get('title')}\n",
        "response.code == 200")

    reg = await SkillRegistry.from_store(AssetRepository(), tenant="m6", subsystems=[Subsystem.OA])
    # 目录:adapter 作为 integration=adapter 暴露
    man = {m.name: m for m in build_manifests(reg.skills)}
    assert "A-OA.submit_leave" in man and man["A-OA.submit_leave"].integration == "adapter"

    orch = Orchestrator(registry=reg, store=AssetRepository(),
                        harness=Harness(action_executor=FakeExec()), action_executor=FakeExec())
    out = await orch.invoke_skill(
        Subsystem.OA, "submit_leave", {"title": "张三的年假"}, tenant="m6", confirm=True)
    assert out.state == TaskState.COMPLETED, out.message
    assert out.exec_result.structured_output.get("echo") == "张三的年假"


async def test_adapter_invoke_failed_when_rule_unmet():
    from dano.assets.repository import AssetRepository
    from dano.shared.enums import Subsystem, TaskState

    SkillRegistry, Orchestrator, Harness, FakeExec = _orchestrator()
    await _publish_adapter(
        "def run(inputs, creds):\n    return {'code': 500}\n", "response.code == 200")
    reg = await SkillRegistry.from_store(AssetRepository(), tenant="m6", subsystems=[Subsystem.OA])
    orch = Orchestrator(registry=reg, store=AssetRepository(),
                        harness=Harness(action_executor=FakeExec()), action_executor=FakeExec())
    out = await orch.invoke_skill(
        Subsystem.OA, "submit_leave", {"title": "x"}, tenant="m6", confirm=True)
    assert out.state == TaskState.FAILED          # 成败规则不满足 → 调用判失败
