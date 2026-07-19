"""流程10:同类失败计数 + 达阈值熔断。

计数器接口化(CounterStore):离线用 InMemoryCounter，生产用 PgFailureCounter。
按 (skill_id, failure_class) 计数;达阈值 → 触发熔断(由调用方暂停 Skill,进流程12)。
"""

from __future__ import annotations

from typing import Protocol

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
