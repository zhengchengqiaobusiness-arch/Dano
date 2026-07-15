"""Shared credential resolver. Plain credentials never enter a Pi projection."""

from __future__ import annotations


def resolve_credential_headers(refs: dict[str, str]) -> dict[str, str]:
    from dano.infra.credentials import resolve_credentials

    return {str(key): str(value) for key, value in resolve_credentials(refs).items()}
