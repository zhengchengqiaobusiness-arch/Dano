import os

import pytest

from dano.auth.challenge import hash_challenge, new_challenge
from dano.auth.store import InMemoryAuthStore, PgAuthStore
from dano.auth.throttle import CLEARED, ThrottleState

NOW = 1_700_000_000.0


@pytest.fixture(params=["memory", "pg"])
async def store(request):
    if request.param == "memory":
        return InMemoryAuthStore()
    if not os.environ.get("DANO_PG_DSN"):
        pytest.skip("未配置 DANO_PG_DSN,跳过 Pg 契约测试")
    from dano.infra.db import init_pool, run_migrations
    await init_pool()
    await run_migrations()
    return PgAuthStore()


def test_challenge明文与哈希对应():
    token, digest = new_challenge()
    assert len(token) > 30
    assert digest == hash_challenge(token)
    assert token != digest


async def test_限流状态往返(store):
    assert await store.get_throttle("acme") == CLEARED
    await store.set_throttle("acme", ThrottleState(fail_count=3, locked_until=NOW + 60))
    state = await store.get_throttle("acme")
    assert state.fail_count == 3 and state.locked_until == NOW + 60


async def test_totp游标往返(store):
    assert await store.get_last_totp_step("cursor-user") is None
    await store.set_last_totp_step("cursor-user", 12345)
    assert await store.get_last_totp_step("cursor-user") == 12345


async def test_challenge生命周期(store):
    _, digest = new_challenge()
    await store.create_challenge(digest, "acme", "acme", NOW + 300)
    rec = await store.get_challenge(digest)
    assert rec.tenant == "acme" and rec.totp_failures == 0 and not rec.consumed

    assert await store.bump_challenge_failure(digest) == 1
    assert await store.bump_challenge_failure(digest) == 2

    assert await store.consume_challenge(digest) is True
    assert await store.consume_challenge(digest) is False
    assert (await store.get_challenge(digest)).consumed is True


async def test_按用户名清空challenge(store):
    _, first = new_challenge()
    _, second = new_challenge()
    await store.create_challenge(first, "acme", "dropme", NOW + 300)
    await store.create_challenge(second, "acme", "dropme", NOW + 300)
    await store.drop_challenges_for("dropme")
    assert await store.get_challenge(first) is None
    assert await store.get_challenge(second) is None


async def test_未知challenge返回None(store):
    assert await store.get_challenge("不存在") is None
    assert await store.consume_challenge("不存在") is False
    assert await store.bump_challenge_failure("不存在") == 0
