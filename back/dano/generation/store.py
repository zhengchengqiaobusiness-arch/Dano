"""生成运行的可追溯持久化(尽力而为:观测失败不影响生成本身)。"""

from __future__ import annotations

import json
from uuid import UUID

import structlog

from dano.generation.artifacts import GenerationResult

log = structlog.get_logger(__name__)


async def save_generation_run(result: GenerationResult, *, run_id: str, tenant: str,
                              subsystem: str, strategy: str | None) -> None:
    """落一条 generation_runs(含每轮迭代证据)。best-effort:出错只告警,不抛。"""
    iters = [{"index": it.index, "passed": it.passed, "reasons": it.reasons,
              "asset_draft_id": it.asset_draft_id} for it in result.iterations]
    try:
        from dano.infra.db import get_pool
        async with get_pool().acquire() as conn:
            await conn.execute(
                """INSERT INTO generation_runs
                   (run_id, tenant, subsystem, flow, strategy, ok, asset_id, rejections, iterations, reason)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                run_id, tenant, subsystem, result.flow, strategy, result.ok,
                UUID(result.asset_id) if result.asset_id else None,
                result.rejections, json.dumps(iters, ensure_ascii=False), result.reason)
    except Exception as e:  # noqa: BLE001 - 观测尽力而为,绝不拖垮生成
        log.warning("generation_run.persist_failed", flow=result.flow, error=str(e))
