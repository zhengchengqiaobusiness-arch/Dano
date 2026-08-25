"""认证瞬态状态存储:登录失败计数、TOTP 防重放游标、两步登录 challenge。

两套实现接口一致 —— InMemoryAuthStore 用于 dev/DB 不可用时的降级,
PgAuthStore 用于生产;由 gateway 的 lifespan 按 DB 可用性装配,
与 registry 的 InMemory/Pg 切换是同一套路数。

这里的状态全是瞬态的:整体清空只会让限流计数归零,不影响任何业务数据。
"""

from __future__ import annotations

from dataclasses import dataclass

from dano.auth.throttle import CLEARED, ThrottleState


@dataclass
class ChallengeRecord:
    """一条两步登录 challenge 的当前状态。"""

    tenant: str
    username: str
    totp_failures: int
    expires_at: float
    consumed: bool


class InMemoryAuthStore:
    """进程内实现。多实例部署时限流不共享,仅供 dev 与 DB 降级使用。"""

    def __init__(self) -> None:
        self._throttle: dict[str, ThrottleState] = {}
        self._totp_step: dict[str, int] = {}
        self._challenges: dict[str, ChallengeRecord] = {}

    async def get_throttle(self, username: str) -> ThrottleState:
        return self._throttle.get(username, CLEARED)

    async def set_throttle(self, username: str, state: ThrottleState) -> None:
        self._throttle[username] = state

    async def get_last_totp_step(self, username: str) -> int | None:
        return self._totp_step.get(username)

    async def set_last_totp_step(self, username: str, step: int) -> None:
        self._totp_step[username] = step

    async def create_challenge(self, challenge_hash: str, tenant: str, username: str,
                               expires_at: float) -> None:
        self._challenges[challenge_hash] = ChallengeRecord(
            tenant=tenant, username=username, totp_failures=0,
            expires_at=expires_at, consumed=False)

    async def get_challenge(self, challenge_hash: str) -> ChallengeRecord | None:
        return self._challenges.get(challenge_hash)

    async def bump_challenge_failure(self, challenge_hash: str) -> int:
        rec = self._challenges.get(challenge_hash)
        if rec is None:
            return 0
        rec.totp_failures += 1
        return rec.totp_failures

    async def consume_challenge(self, challenge_hash: str) -> bool:
        """标记为已用;已经用过或不存在则返回 False —— 用一次即焚。"""
        rec = self._challenges.get(challenge_hash)
        if rec is None or rec.consumed:
            return False
        rec.consumed = True
        return True

    async def drop_challenges_for(self, username: str) -> None:
        for key in [k for k, v in self._challenges.items() if v.username == username]:
            del self._challenges[key]


class PgAuthStore:
    """PostgreSQL 实现。无状态,依赖全局连接池。"""

    async def get_throttle(self, username: str) -> ThrottleState:
        row = await self._fetchrow(
            "SELECT fail_count, locked_until FROM auth_failures WHERE username=$1", username)
        if row is None:
            return CLEARED
        return ThrottleState(fail_count=row["fail_count"], locked_until=row["locked_until"])

    async def set_throttle(self, username: str, state: ThrottleState) -> None:
        await self._execute(
            "INSERT INTO auth_failures (username, fail_count, locked_until, updated_at) "
            "VALUES ($1,$2,$3, now()) "
            "ON CONFLICT (username) DO UPDATE SET fail_count=EXCLUDED.fail_count, "
            "locked_until=EXCLUDED.locked_until, updated_at=now()",
            username, state.fail_count, state.locked_until)

    async def get_last_totp_step(self, username: str) -> int | None:
        row = await self._fetchrow(
            "SELECT last_totp_step FROM auth_failures WHERE username=$1", username)
        return None if row is None else row["last_totp_step"]

    async def set_last_totp_step(self, username: str, step: int) -> None:
        await self._execute(
            "INSERT INTO auth_failures (username, last_totp_step, updated_at) "
            "VALUES ($1,$2, now()) "
            "ON CONFLICT (username) DO UPDATE SET last_totp_step=EXCLUDED.last_totp_step, "
            "updated_at=now()",
            username, step)

    async def create_challenge(self, challenge_hash: str, tenant: str, username: str,
                               expires_at: float) -> None:
        await self._execute(
            "INSERT INTO auth_challenges (challenge_hash, tenant, username, expires_at) "
            "VALUES ($1,$2,$3,$4) ON CONFLICT (challenge_hash) DO NOTHING",
            challenge_hash, tenant, username, expires_at)

    async def get_challenge(self, challenge_hash: str) -> ChallengeRecord | None:
        row = await self._fetchrow(
            "SELECT tenant, username, totp_failures, expires_at, consumed "
            "FROM auth_challenges WHERE challenge_hash=$1", challenge_hash)
        if row is None:
            return None
        return ChallengeRecord(tenant=row["tenant"], username=row["username"],
                               totp_failures=row["totp_failures"],
                               expires_at=row["expires_at"], consumed=row["consumed"])

    async def bump_challenge_failure(self, challenge_hash: str) -> int:
        row = await self._fetchrow(
            "UPDATE auth_challenges SET totp_failures = totp_failures + 1 "
            "WHERE challenge_hash=$1 RETURNING totp_failures", challenge_hash)
        return 0 if row is None else row["totp_failures"]

    async def consume_challenge(self, challenge_hash: str) -> bool:
        """原子地标记为已用:并发下只有一个请求能拿到 True。"""
        row = await self._fetchrow(
            "UPDATE auth_challenges SET consumed = TRUE "
            "WHERE challenge_hash=$1 AND consumed = FALSE RETURNING challenge_hash",
            challenge_hash)
        return row is not None

    async def drop_challenges_for(self, username: str) -> None:
        await self._execute("DELETE FROM auth_challenges WHERE username=$1", username)

    @staticmethod
    async def _execute(sql: str, *args) -> None:
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            await conn.execute(sql, *args)

    @staticmethod
    async def _fetchrow(sql: str, *args):
        from dano.infra.db import get_pool

        async with get_pool().acquire() as conn:
            return await conn.fetchrow(sql, *args)
