import pytest

from dano.gateway import app as gateway


@pytest.mark.asyncio
async def test_gateway_lifespan_starts_without_a_database(monkeypatch) -> None:
    import dano.infra.db as db

    async def unavailable_database() -> None:
        raise RuntimeError("database unavailable")

    async def close_pool() -> None:
        pass

    monkeypatch.setattr(db, "init_pool", unavailable_database)
    monkeypatch.setattr(db, "close_pool", close_pool)

    async with gateway.app.router.lifespan_context(gateway.app):
        pass
