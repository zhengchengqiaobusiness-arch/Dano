"""LLM cost controls: conservative budgets, persistent cache and singleflight."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterator

import structlog

log = structlog.get_logger(__name__)


class LLMBudgetExceeded(RuntimeError):
    """Raised before a provider request when a hard input budget is exceeded."""


def estimate_text_tokens(text: str) -> int:
    """Conservative tokenizer-independent estimate for mixed Chinese/JSON."""
    ascii_count = sum(1 for char in text if ord(char) < 128)
    non_ascii_count = len(text) - ascii_count
    return max(1, (ascii_count + 3) // 4 + non_ascii_count)


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    total = 3
    for message in messages:
        total += 4 + estimate_text_tokens(str(message.get("content") or ""))
    return total


@dataclass
class LLMBudget:
    limit: int
    reserved: int = 0

    def reserve(self, tokens: int, *, purpose: str) -> None:
        if tokens > self.limit or self.reserved + tokens > self.limit:
            raise LLMBudgetExceeded(
                f"LLM 输入预算不足: purpose={purpose}, request={tokens}, "
                f"used={self.reserved}, limit={self.limit}"
            )
        self.reserved += tokens


_BUDGET: ContextVar[LLMBudget | None] = ContextVar("dano_llm_budget", default=None)


@contextmanager
def llm_budget_scope(limit: int) -> Iterator[LLMBudget]:
    budget = LLMBudget(max(1, int(limit)))
    token = _BUDGET.set(budget)
    try:
        yield budget
    finally:
        _BUDGET.reset(token)


def begin_llm_budget(limit: int):  # noqa: ANN201 - opaque ContextVar token
    """Begin a budget that may span an existing long-lived async handler."""
    return _BUDGET.set(LLMBudget(max(1, int(limit))))


def end_llm_budget(token) -> None:  # noqa: ANN001
    if token is not None:
        _BUDGET.reset(token)


def reserve_llm_tokens(tokens: int, *, purpose: str, per_request_limit: int) -> None:
    if tokens > per_request_limit:
        raise LLMBudgetExceeded(
            f"LLM 单次输入超限: purpose={purpose}, estimated={tokens}, limit={per_request_limit}"
        )
    budget = _BUDGET.get()
    if budget is not None:
        budget.reserve(tokens, purpose=purpose)


def canonical_cache_key(*, model: str, messages: list[dict[str, Any]], version: str) -> str:
    raw = json.dumps(
        {"version": version, "model": model, "messages": messages},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_MEMORY_CACHE: "OrderedDict[str, tuple[float, dict[str, Any], dict[str, Any]]]" = OrderedDict()
_MEMORY_CAP = 2048
_LOCKS: dict[str, asyncio.Lock] = {}


def _pool_or_none():  # noqa: ANN202
    try:
        from dano.infra.db import get_pool
        return get_pool()
    except Exception:  # noqa: BLE001
        return None


async def _cache_get(key: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    now = time.time()
    memory = _MEMORY_CACHE.get(key)
    if memory is not None:
        expires, response, usage = memory
        if expires > now:
            _MEMORY_CACHE.move_to_end(key)
            return dict(response), dict(usage)
        _MEMORY_CACHE.pop(key, None)

    pool = _pool_or_none()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE llm_response_cache
                SET hit_count = hit_count + 1, last_hit_at = now()
                WHERE cache_key = $1 AND expires_at > now()
                RETURNING response, prompt_tokens, output_tokens,
                          EXTRACT(EPOCH FROM expires_at) AS expires_epoch
                """,
                key,
            )
        if row is None:
            return None
        response = row["response"]
        if isinstance(response, str):
            response = json.loads(response)
        usage = {
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["output_tokens"] or 0),
        }
        _remember(key, float(row["expires_epoch"]), dict(response or {}), usage)
        return dict(response or {}), usage
    except Exception as exc:  # noqa: BLE001 - cache outage must not break generation
        log.warning("llm.cache_read_failed", error=str(exc))
        return None


def _remember(key: str, expires: float, response: dict[str, Any], usage: dict[str, Any]) -> None:
    _MEMORY_CACHE[key] = (expires, dict(response), dict(usage))
    _MEMORY_CACHE.move_to_end(key)
    while len(_MEMORY_CACHE) > _MEMORY_CAP:
        _MEMORY_CACHE.popitem(last=False)


async def _cache_put(
    key: str,
    *,
    model: str,
    purpose: str,
    response: dict[str, Any],
    usage: dict[str, Any],
    ttl_s: int,
) -> None:
    expires = time.time() + max(60, ttl_s)
    _remember(key, expires, response, usage)
    pool = _pool_or_none()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_response_cache
                    (cache_key, model, purpose, response, prompt_tokens, output_tokens, expires_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, to_timestamp($7))
                ON CONFLICT (cache_key) DO UPDATE SET
                    response = EXCLUDED.response,
                    prompt_tokens = EXCLUDED.prompt_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    expires_at = EXCLUDED.expires_at,
                    created_at = now()
                """,
                key,
                model,
                purpose,
                json.dumps(response, ensure_ascii=False),
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                expires,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("llm.cache_write_failed", error=str(exc))


async def cached_singleflight(
    key: str,
    *,
    model: str,
    purpose: str,
    ttl_s: int,
    producer: Callable[[], Awaitable[tuple[dict[str, Any], dict[str, Any]]]],
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    cached = await _cache_get(key)
    if cached is not None:
        response, usage = cached
        return response, {**usage, "application_cache_hit": True}, True

    lock = _LOCKS.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            cached = await _cache_get(key)
            if cached is not None:
                response, usage = cached
                return response, {**usage, "application_cache_hit": True}, True
            response, usage = await producer()
            await _cache_put(
                key,
                model=model,
                purpose=purpose,
                response=response,
                usage=usage,
                ttl_s=ttl_s,
            )
            return response, {**usage, "application_cache_hit": False}, False
    finally:
        if not lock.locked():
            _LOCKS.pop(key, None)


def clear_memory_llm_cache() -> None:
    """Test/support hook; persistent cache is intentionally untouched."""
    _MEMORY_CACHE.clear()
