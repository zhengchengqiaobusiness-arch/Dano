"""租户后台用户名/密码登录与修改密码测试。

覆盖:创建租户带初始账号、登录成功/失败、未设密码租户拒绝登录、
改密码(成功/原密码错误/未认证)。
"""

from __future__ import annotations

import httpx
import pytest_asyncio

import dano.gateway.app as gateway_module
from dano.gateway.app import app
from dano.infra.passwords import hash_password, verify_password
from dano.registry import InMemoryRegistry


@pytest_asyncio.fixture
async def client(monkeypatch):
    registry = InMemoryRegistry()
    monkeypatch.setattr(gateway_module, "_registry", registry)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def create_tenant_with_login(
    client, *, tenant="acme", username="acme", password="secret1234"
) -> httpx.Response:
    return await client.post(
        "/tenants",
        json={"tenant": tenant, "username": username, "password": password},
    )


async def test_password_hash_roundtrip():
    stored = hash_password("s3cret!")
    assert stored.startswith("scrypt$")
    assert verify_password("s3cret!", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("s3cret!", "not-a-hash")


async def test_create_tenant_with_password(client):
    resp = await create_tenant_with_login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant"] == "acme"
    assert body["api_key"]
    assert body["username"] == "acme"
    assert body["password_hash"]  # 只存哈希,不存明文


async def test_login_success_returns_api_key(client):
    await create_tenant_with_login(client)
    resp = await client.post(
        "/auth/login", json={"username": "acme", "password": "secret1234"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant"] == "acme"
    assert body["api_key"]


async def test_login_wrong_password(client):
    await create_tenant_with_login(client)
    resp = await client.post(
        "/auth/login", json={"username": "acme", "password": "wrong-pass"}
    )
    assert resp.status_code == 401


async def test_login_unknown_user(client):
    resp = await client.post(
        "/auth/login", json={"username": "ghost", "password": "whatever123"}
    )
    assert resp.status_code == 401


async def test_tenant_without_password_cannot_login(client):
    resp = await client.post("/tenants", json={"tenant": "bare"})
    assert resp.status_code == 200
    login_resp = await client.post(
        "/auth/login", json={"username": "bare", "password": "anything123"}
    )
    assert login_resp.status_code == 401


async def test_change_password(client):
    await create_tenant_with_login(client)
    login_resp = await client.post(
        "/auth/login", json={"username": "acme", "password": "secret1234"}
    )
    api_key = login_resp.json()["api_key"]

    change = await client.post(
        "/auth/change-password",
        headers={"X-Tenant-Key": api_key},
        json={"old_password": "secret1234", "new_password": "newpass5678"},
    )
    assert change.status_code == 200

    old_login = await client.post(
        "/auth/login", json={"username": "acme", "password": "secret1234"}
    )
    assert old_login.status_code == 401
    new_login = await client.post(
        "/auth/login", json={"username": "acme", "password": "newpass5678"}
    )
    assert new_login.status_code == 200


async def test_change_password_rejects_wrong_old_password(client):
    await create_tenant_with_login(client)
    login_resp = await client.post(
        "/auth/login", json={"username": "acme", "password": "secret1234"}
    )
    api_key = login_resp.json()["api_key"]

    resp = await client.post(
        "/auth/change-password",
        headers={"X-Tenant-Key": api_key},
        json={"old_password": "wrong-old", "new_password": "newpass5678"},
    )
    assert resp.status_code == 401


async def test_change_password_requires_auth(client):
    resp = await client.post(
        "/auth/change-password",
        json={"old_password": "x", "new_password": "newpass5678"},
    )
    assert resp.status_code == 401


async def test_change_password_requires_min_length(client):
    await create_tenant_with_login(client)
    login_resp = await client.post(
        "/auth/login", json={"username": "acme", "password": "secret1234"}
    )
    api_key = login_resp.json()["api_key"]

    resp = await client.post(
        "/auth/change-password",
        headers={"X-Tenant-Key": api_key},
        json={"old_password": "secret1234", "new_password": "short"},
    )
    assert resp.status_code == 400
