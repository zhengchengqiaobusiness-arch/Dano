"""PostgreSQL 连接池(asyncpg)。资产库的底层访问。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from dano.config import get_settings

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger(__name__)

_pool: "asyncpg.Pool | None" = None


async def init_pool() -> "asyncpg.Pool":
    """初始化全局连接池。应用启动时调用一次。"""
    global _pool
    import asyncpg  # 惰性导入:无 asyncpg 时网关仍可启动(仅 DB 相关接口不可用)

    if _pool is not None:
        return _pool
    settings = get_settings()
    _pool = await asyncpg.create_pool(
        dsn=settings.pg_dsn,
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
    )
    log.info("pg_pool.initialized", dsn=settings.pg_dsn.split("@")[-1])
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("PG 连接池未初始化,请先调用 init_pool()")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("pg_pool.closed")


async def run_migrations(sql_dir: str = "migrations") -> None:
    """按序执行 migrations 目录下的 .sql 文件,每个**最多执行一次**(schema_migrations 记账)。

    run-once 而非每次重放:重放会让"重建 CHECK 约束"类迁移在已有新类型数据时校验失败。
    M0 简易迁移,后续可换 alembic/atlas。
    """
    import pathlib

    pool = get_pool()
    files = sorted(pathlib.Path(sql_dir).glob("*.sql"))
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "filename TEXT PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        applied = {r["filename"] for r in await conn.fetch("SELECT filename FROM schema_migrations")}
        for f in files:
            if f.name in applied:
                continue
            log.info("migration.apply", file=f.name)
            async with conn.transaction():
                await conn.execute(f.read_text(encoding="utf-8"))
                await conn.execute(
                    "INSERT INTO schema_migrations(filename) VALUES($1) ON CONFLICT DO NOTHING", f.name)
