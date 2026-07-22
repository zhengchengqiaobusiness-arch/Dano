"""Runtime token HTTP contract regression tests."""

from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from dano.gateway import app as gateway
from dano.infra import token_refresh, token_store


def test_runtime_token_update_uses_post_and_removes_put() -> None:
    methods = {
        method
        for route in gateway.app.routes
        if getattr(route, "path", None) == "/v1/settings/token"
        for method in (getattr(route, "methods", None) or set())
    }

    assert {"GET", "POST"} <= methods
    assert "PUT" not in methods
    assert not any(
        getattr(route, "path", None) == "/settings/token" for route in gateway.app.routes
    )


class _Registry:
    async def get_tenant_by_key(self, key):
        return SimpleNamespace(tenant="aaa") if key == "valid" else None


async def test_token_read_requires_matching_tenant_and_masks_by_default(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "_registry", _Registry())

    async def get_token(_tenant, _subsystem):
        return {
            "headers": {
                "Authorization": "Bearer stale-value",
                "authorization": "Bearer secret-value",
                "Tenant-Id": "1",
            },
            "source": "manual",
            "updated_at": "2026-07-22T00:00:00+00:00",
        }

    monkeypatch.setattr(token_store, "get_token", get_token)
    result = await gateway.get_runtime_token("aaa", "A-OA", x_tenant_key="valid")
    assert result["headers"]["Authorization"] != "Bearer secret-value"
    assert list(result["headers"]).count("Authorization") == 1
    assert "authorization" not in result["headers"]
    assert result["headers"]["Tenant-Id"] == "1"

    with pytest.raises(HTTPException) as exc:
        await gateway.get_runtime_token("other", "A-OA", x_tenant_key="valid")
    assert exc.value.status_code == 403


async def test_manual_update_is_tenant_scoped_and_uses_shared_store(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "_registry", _Registry())
    saved = {}

    async def update(tenant, subsystem, headers, *, source):
        saved.update(tenant=tenant, subsystem=subsystem, headers=headers, source=source)
        return {"headers": headers, "updated_at": "2026-07-22T00:00:00+00:00"}

    monkeypatch.setattr(token_store, "update_token_headers", update)
    result = await gateway.post_runtime_token(
        gateway.TokenUpsertReq(tenant="aaa", subsystem="A-OA", token="new"),
        x_tenant_key="valid",
    )
    assert result["ok"] is True
    assert saved == {
        "tenant": "aaa",
        "subsystem": "A-OA",
        "headers": {"Authorization": "Bearer new"},
        "source": "manual",
    }

    with pytest.raises(HTTPException) as exc:
        await gateway.post_runtime_token(
            gateway.TokenUpsertReq(tenant="other", subsystem="A-OA", token="new"),
            x_tenant_key="valid",
        )
    assert exc.value.status_code == 403


async def test_internal_refresh_rejects_bad_key_and_reports_job_failure(monkeypatch) -> None:
    from dano import config

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(token_refresh_key="secret"))

    with pytest.raises(HTTPException) as exc:
        await gateway.refresh_runtime_tokens(gateway.TokenRefreshRunReq(), x_dano_refresh_key="bad")
    assert exc.value.status_code == 401

    async def failed(*_args, **_kwargs):
        return {"ok": False, "reason": "all_sources_failed"}

    monkeypatch.setattr(token_refresh, "refresh_one", failed)
    with pytest.raises(HTTPException) as exc:
        await gateway.refresh_runtime_tokens(
            gateway.TokenRefreshRunReq(tenant="aaa", subsystem="A-OA", force=True),
            x_dano_refresh_key="secret",
        )
    assert exc.value.status_code == 502
