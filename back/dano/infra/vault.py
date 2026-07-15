"""Vault 凭证引用解析骨架。

红线:平台侧只存 `vault://` 引用,绝不持明文。执行层 Worker 在 A 公司侧动态取凭证。
M0 提供引用解析与取值接口;实际取值在执行层(M2)按最小权限实施。
"""

from __future__ import annotations

from typing import Any

import structlog

from dano.config import get_settings

log = structlog.get_logger(__name__)

VAULT_SCHEME = "vault://"


def is_vault_ref(value: str) -> bool:
    return value.startswith(VAULT_SCHEME)


def parse_ref(ref: str) -> tuple[str, str]:
    """解析 vault://a-corp/oa → (path, name)。"""
    if not is_vault_ref(ref):
        raise ValueError(f"非法 vault 引用: {ref}")
    body = ref[len(VAULT_SCHEME):]
    path, _, name = body.partition("/")
    return path, name


class VaultClient:
    """hvac 封装。M0 为懒连接骨架,require_vault=False 时不强制建连。"""

    def __init__(self) -> None:
        self._client = None

    def _connect(self):
        import hvac

        settings = get_settings()
        client = hvac.Client(url=settings.vault_addr, token=settings.vault_token)
        if not client.is_authenticated():
            raise RuntimeError("Vault 鉴权失败")
        log.info("vault.connected", addr=settings.vault_addr)
        return client

    def read_secret(self, ref: str) -> dict[str, str]:
        """按引用读取凭证。仅执行层在受控环境调用。"""
        if self._client is None:
            self._client = self._connect()
        path, name = parse_ref(ref)
        resp = self._client.secrets.kv.v2.read_secret_version(path=f"{path}/{name}")
        return resp["data"]["data"]

    def write_secret(self, path: str, values: dict[str, Any]) -> str:
        """Write credential plaintext only to Vault and return an opaque ref."""

        normalized = path.strip().strip("/")
        if not normalized or not values:
            raise ValueError("Vault secret path and values are required")
        if self._client is None:
            self._client = self._connect()
        self._client.secrets.kv.v2.create_or_update_secret(
            path=normalized,
            secret=dict(values),
        )
        return f"{VAULT_SCHEME}{normalized}"
