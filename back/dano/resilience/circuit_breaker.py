"""流程10:同类失败计数 + 达阈值熔断。

计数器接口化(CounterStore):离线/测试用 InMemoryCounter,生产用 RedisCounter(骨架)。
按 (skill_id, failure_class) 计数;达阈值 → 触发熔断(由调用方暂停 Skill,进流程12)。
"""

from __future__ import annotations

from typing import Protocol

import structlog

log = structlog.get_logger(__name__)


class CounterStore(Protocol):
    async def incr(self, key: str) -> int: ...
    async def reset_prefix(self, prefix: str) -> None: ...


class InMemoryCounter:
    def __init__(self) -> None:
        self._c: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self._c[key] = self._c.get(key, 0) + 1
        return self._c[key]

    async def reset_prefix(self, prefix: str) -> None:
        for k in [k for k in self._c if k.startswith(prefix)]:
            self._c.pop(k, None)


class PgFailureCounter:
    """失败计数的 PostgreSQL 持久化(CounterStore;跨进程重启留存)。无状态,依赖全局连接池。"""

    async def incr(self, key: str) -> int:
        from dano.infra.db import get_pool
        async with get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO failure_counts (counter_key, count) VALUES ($1, 1)
                   ON CONFLICT (counter_key) DO UPDATE
                       SET count = failure_counts.count + 1, updated_at = now()
                   RETURNING count""",
                key,
            )
        return int(row["count"])

    async def reset_prefix(self, prefix: str) -> None:
        from dano.infra.db import get_pool
        # 转义 LIKE 通配符(skill_id 可能含 '_'),用 ESCAPE 显式声明转义符
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        async with get_pool().acquire() as conn:
            await conn.execute(
                "DELETE FROM failure_counts WHERE counter_key LIKE $1 ESCAPE '\\'", like)


class RedisCounter:  # pragma: no cover - 需 Redis
    """生产计数器(骨架):INCR + 滑动窗口 TTL。"""

    def __init__(self, client) -> None:  # noqa: ANN001
        self._r = client

    async def incr(self, key: str) -> int:
        n = await self._r.incr(f"dano:fail:{key}")
        await self._r.expire(f"dano:fail:{key}", 3600)
        return int(n)

    async def reset_prefix(self, prefix: str) -> None:
        async for k in self._r.scan_iter(f"dano:fail:{prefix}*"):
            await self._r.delete(k)


class CircuitBreaker:
    def __init__(self, counter: CounterStore | None = None, *, threshold: int = 3) -> None:
        self.counter = counter or InMemoryCounter()
        self.threshold = threshold

    async def record_failure(self, skill_id: str, failure_class: str) -> tuple[int, bool]:
        """记一次同类失败,返回(当前计数, 是否达阈值需熔断)。"""
        count = await self.counter.incr(f"{skill_id}:{failure_class}")
        tripped = count >= self.threshold
        log.info("circuit.record", skill_id=skill_id, fc=failure_class, count=count, tripped=tripped)
        return count, tripped

    async def reset(self, skill_id: str) -> None:
        """成功后清零该 Skill 的失败计数。"""
        await self.counter.reset_prefix(f"{skill_id}:")
