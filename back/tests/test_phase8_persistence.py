"""Phase A1 验收:生命周期 + 失败计数持久化(重启不丢)。

确定性(只需 PG,无需 key/mock):
- 生命周期跨实例留存:lc1 登记+暂停 → 新建 PgSkillStore(模拟重启)读回仍 SUSPENDED;恢复后版本+1 留存。
- 失败计数跨实例留存:incr 累加跨新实例;reset_prefix 清零;'_' 通配符正确转义(不误清相邻 key)。
PG(dano_back)门控。
"""

from __future__ import annotations

import os

import pytest

_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")


@pytest.fixture(autouse=True)
async def _pg():
    os.environ["DANO_PG_DSN"] = _DSN
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM skill_lifecycle WHERE skill_id LIKE 'ph8%'")
        await c.execute("DELETE FROM failure_counts WHERE counter_key LIKE 'fail:ph8%'")
    yield
    await close_pool()


async def test_lifecycle_persists_across_instances():
    from dano.lifecycle.pg_store import PgSkillStore
    from dano.lifecycle.state_machine import SkillLifecycle
    from dano.shared.enums import SkillState, Subsystem

    lc1 = SkillLifecycle(PgSkillStore())
    await lc1.register_published("ph8.create_leave", Subsystem.OA, "create_leave", version=1)
    await lc1.suspend("ph8.create_leave")

    # 模拟进程重启:全新 store 实例读回(同一 PG)
    store2 = PgSkillStore()
    rec = await store2.get("ph8.create_leave")
    assert rec is not None, "重启后丢了 Skill 记录"
    assert rec.state == SkillState.SUSPENDED, rec.state
    assert rec.asset_version == 1

    # 恢复发布(版本 +1),再换实例读回
    lc2 = SkillLifecycle(store2)
    await lc2.recover_to_published("ph8.create_leave", 2)
    rec2 = await PgSkillStore().get("ph8.create_leave")
    assert rec2.state == SkillState.PUBLISHED and rec2.asset_version == 2, rec2


async def test_failure_count_persists_and_resets():
    from dano.resilience.circuit_breaker import PgFailureCounter

    c1 = PgFailureCounter()
    assert await c1.incr("fail:ph8.create_leave") == 1
    assert await c1.incr("fail:ph8.create_leave") == 2

    # 重启:新实例继续累加(持久化),达阈值语义不丢
    assert await PgFailureCounter().incr("fail:ph8.create_leave") == 3

    # 自愈成功清零
    await PgFailureCounter().reset_prefix("fail:ph8.create_leave")
    assert await PgFailureCounter().incr("fail:ph8.create_leave") == 1


async def test_reset_prefix_escapes_underscore():
    """'_' 是 LIKE 通配符:reset 'fail:ph8.a_b' 不得误清 'fail:ph8.axb'。"""
    from dano.resilience.circuit_breaker import PgFailureCounter
    c = PgFailureCounter()
    await c.incr("fail:ph8.a_b")
    await c.incr("fail:ph8.axb")
    await c.reset_prefix("fail:ph8.a_b")
    assert await PgFailureCounter().incr("fail:ph8.a_b") == 1     # 被清零后重新计为 1
    assert await PgFailureCounter().incr("fail:ph8.axb") == 2     # 未被误清,仍累加到 2
