"""Phase A3 验收:运行期凭证解析(真实 Vault 优先 / dev 回退 env / require_vault fail-closed)。

纯单元:monkeypatch Vault,不需 PG/key/真实 Vault。
"""

from __future__ import annotations

import os

import pytest

from dano.config import get_settings
from dano.infra import credentials as C

_ENV_KEYS = ("DANO_VAULT_TOKEN", "DANO_REQUIRE_VAULT", "DANO_RUNTIME_CREDENTIALS")


@pytest.fixture(autouse=True)
def _isolate_env():
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    get_settings.cache_clear()
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


class FakeVault:
    def __init__(self, secret=None, fail=False):
        self.secret = secret or {}
        self.fail = fail

    def read_secret(self, ref):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("vault down")
        return self.secret


_REFS = {"primary": "vault://demo/oa"}


def test_env_fallback_when_no_vault():
    os.environ["DANO_RUNTIME_CREDENTIALS"] = '{"demo/oa": {"token": "env-tok"}}'
    get_settings.cache_clear()
    assert C.resolve_credentials(_REFS) == {"token": "env-tok"}


def test_vault_preferred_when_configured(monkeypatch):
    os.environ["DANO_VAULT_TOKEN"] = "root"
    os.environ["DANO_RUNTIME_CREDENTIALS"] = '{"demo/oa": {"token": "env-tok"}}'  # 应被忽略
    get_settings.cache_clear()
    monkeypatch.setattr(C, "_get_vault", lambda: FakeVault({"token": "vault-tok"}))
    assert C.resolve_credentials(_REFS) == {"token": "vault-tok"}


def test_require_vault_fails_closed(monkeypatch):
    os.environ["DANO_VAULT_TOKEN"] = "root"
    os.environ["DANO_REQUIRE_VAULT"] = "1"
    os.environ["DANO_RUNTIME_CREDENTIALS"] = '{"demo/oa": {"token": "env-tok"}}'
    get_settings.cache_clear()
    monkeypatch.setattr(C, "_get_vault", lambda: FakeVault(fail=True))
    with pytest.raises(RuntimeError, match="require_vault"):
        C.resolve_credentials(_REFS)


def test_vault_failure_falls_back_to_env_when_not_required(monkeypatch):
    os.environ["DANO_VAULT_TOKEN"] = "root"          # 配了但非强制
    os.environ["DANO_RUNTIME_CREDENTIALS"] = '{"demo/oa": {"token": "env-tok"}}'
    get_settings.cache_clear()
    monkeypatch.setattr(C, "_get_vault", lambda: FakeVault(fail=True))
    assert C.resolve_credentials(_REFS) == {"token": "env-tok"}
