import time

import pytest
from fastapi.testclient import TestClient

from dano.auth.totp import totp_code
from dano.gateway.app import app

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_tenant(client, tenant):
    resp = client.post("/tenants", json={
        "tenant": tenant, "username": tenant, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["api_key"]


def _bind_totp(client, key):
    """绑定两步验证。

    激活故意用上一个时间步的码(容忍窗口内):同一个 TOTP 码用过一次就作废,
    否则紧接着的登录会被防重放拒掉 —— 真实使用中用户会等下一个码。
    """
    secret = client.post("/auth/totp/setup", headers={"X-Tenant-Key": key}).json()["secret"]
    resp = client.post("/auth/totp/activate", headers={"X-Tenant-Key": key},
                       json={"code": totp_code(secret, now=time.time() - 30)})
    assert resp.status_code == 200, resp.text
    return secret, resp.json()["backup_codes"]


def test_建租户拒绝弱密码(client):
    resp = client.post("/tenants", json={
        "tenant": "weak", "username": "weak", "password": "short"})
    assert resp.status_code == 400
    assert "至少 12 位" in resp.json()["detail"]


def test_建租户不填密码仍可创建(client):
    resp = client.post("/tenants", json={"tenant": "nopass"})
    assert resp.status_code == 200
    assert resp.json()["api_key"].startswith("dk_")


def test_未绑定时单步登录(client):
    key = _make_tenant(client, "solo")
    resp = client.post("/auth/login", json={"username": "solo", "password": PASSWORD})
    assert resp.status_code == 200
    assert resp.json()["api_key"] == key


def test_密码错误返回401(client):
    _make_tenant(client, "badpass")
    resp = client.post("/auth/login", json={
        "username": "badpass", "password": "definitely-wrong-here"})
    assert resp.status_code == 401
    assert "用户名或密码错误" in resp.json()["detail"]


def test_两步登录完整链路(client):
    key = _make_tenant(client, "twofa")
    setup = client.post("/auth/totp/setup", headers={"X-Tenant-Key": key}).json()
    assert setup["qr_svg_data_uri"].startswith("data:image/svg+xml;base64,")
    secret = setup["secret"]

    activate = client.post("/auth/totp/activate", headers={"X-Tenant-Key": key},
                           json={"code": totp_code(secret, now=time.time() - 30)})
    assert activate.status_code == 200
    assert len(activate.json()["backup_codes"]) == 10

    first = client.post("/auth/login", json={"username": "twofa", "password": PASSWORD}).json()
    assert first["need_totp"] is True and "api_key" not in first

    second = client.post("/auth/login/totp", json={
        "challenge": first["challenge"], "code": totp_code(secret)})
    assert second.status_code == 200
    assert second.json()["api_key"] == key


def test_备用码可完成第二步(client):
    key = _make_tenant(client, "bkcode")
    _, codes = _bind_totp(client, key)
    first = client.post("/auth/login", json={"username": "bkcode", "password": PASSWORD}).json()
    resp = client.post("/auth/login/totp", json={
        "challenge": first["challenge"], "code": codes[0]})
    assert resp.status_code == 200 and resp.json()["api_key"] == key


def test_绑定后改密必须带验证码(client):
    key = _make_tenant(client, "chpw")
    secret, _ = _bind_totp(client, key)

    resp = client.post("/auth/change-password", headers={"X-Tenant-Key": key},
                       json={"old_password": PASSWORD, "new_password": "another-good-password"})
    assert resp.status_code == 401

    resp = client.post("/auth/change-password", headers={"X-Tenant-Key": key},
                       json={"old_password": PASSWORD, "new_password": "another-good-password",
                             "code": totp_code(secret)})
    assert resp.status_code == 200


def test_改密拒绝弱密码(client):
    key = _make_tenant(client, "weakpw")
    resp = client.post("/auth/change-password", headers={"X-Tenant-Key": key},
                       json={"old_password": PASSWORD, "new_password": "short12"})
    assert resp.status_code == 400


def test_重复setup返回409(client):
    key = _make_tenant(client, "dup")
    _bind_totp(client, key)
    assert client.post("/auth/totp/setup", headers={"X-Tenant-Key": key}).status_code == 409


def test_解绑后回到单步登录(client):
    key = _make_tenant(client, "unbind")
    secret, _ = _bind_totp(client, key)
    resp = client.post("/auth/totp/disable", headers={"X-Tenant-Key": key},
                       json={"password": PASSWORD, "code": totp_code(secret)})
    assert resp.status_code == 200
    login = client.post("/auth/login", json={"username": "unbind", "password": PASSWORD}).json()
    assert login["api_key"] == key


def test_重新生成备用码(client):
    key = _make_tenant(client, "regen")
    secret, old = _bind_totp(client, key)
    resp = client.post("/auth/totp/backup-codes", headers={"X-Tenant-Key": key},
                       json={"password": PASSWORD, "code": totp_code(secret)})
    assert resp.status_code == 200
    new = resp.json()["backup_codes"]
    assert len(new) == 10 and set(new) != set(old)


def test_totp接口需要鉴权(client):
    assert client.post("/auth/totp/setup").status_code == 401
    assert client.post("/auth/totp/activate", json={"code": "123456"}).status_code == 401


def test_同一个码不能既激活又登录(client):
    key = _make_tenant(client, "replay")
    secret = client.post("/auth/totp/setup", headers={"X-Tenant-Key": key}).json()["secret"]
    code = totp_code(secret)
    assert client.post("/auth/totp/activate", headers={"X-Tenant-Key": key},
                       json={"code": code}).status_code == 200

    first = client.post("/auth/login", json={"username": "replay", "password": PASSWORD}).json()
    resp = client.post("/auth/login/totp", json={"challenge": first["challenge"], "code": code})
    assert resp.status_code == 401     # 防重放:该码已被激活步用掉
