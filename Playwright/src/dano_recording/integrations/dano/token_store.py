"""Shared runtime token-store adapter."""

from __future__ import annotations


async def load_runtime_token(tenant: str, subsystem: str) -> dict:
    from dano.infra.token_store import load_token

    return await load_token(tenant, subsystem)


async def save_runtime_token(tenant: str, subsystem: str, headers: dict, *, source: str) -> None:
    from dano.infra.token_store import save_token

    await save_token(tenant, subsystem, headers, source=source)
