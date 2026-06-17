"""Phase 5 验收:对外契约(/v1/skills)+ 瘦执行(/invoke)+ 租户鉴权/隔离 + CORS。

种子法(无需 key):直接发布 env_profile+连接器,测契约/鉴权/隔离/CORS/invoke(真打 mock)。
e2e(需 key):onboard() 自主生成 → 契约 → invoke。
PG + mock(:9002)门控。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml

BACK = Path(__file__).resolve().parent.parent
_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")


@pytest.fixture(autouse=True)
async def _pg():
    os.environ["DANO_PG_DSN"] = _DSN
    os.environ["DANO_RUNTIME_CREDENTIALS"] = '{"ph5/oa": {"token": "ruoyi-mock-token-xyz"}}'
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='ph5'")
    yield
    await close_pool()


def _wait_port(port, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


@pytest.fixture(scope="session")
def mock_oa():
    proc = subprocess.Popen([sys.executable, "-m", "examples.ruoyi_mock_server"],
                            cwd=str(BACK), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not _wait_port(9002):
        proc.terminate(); pytest.skip("ruoyi_mock_server 未起来")
    yield "http://localhost:9002"
    proc.terminate()


async def _seed_ph5():
    """直接发布 env_profile + 一个查询连接器(list_todo),模拟接入产物。"""
    from dano.assets.repository import AssetRepository
    from dano.agent_tools.connector_builder import build_connector_body
    from dano.capabilities import doc_parser, oa_templates
    from dano.shared.asset_bodies import AuthConfig, EnvProfileBody
    from dano.shared.enums import AssetType, Subsystem, ValidationStatus
    from dano.shared.models import AssetEnvelope, Scope

    repo = AssetRepository()
    scope = Scope(tenant="ph5", subsystem=Subsystem.OA)

    async def _pub(asset_type, key, body):
        env = await repo.create(AssetEnvelope(asset_type=asset_type, scope=scope, asset_key=key,
            version=0, source_fingerprint="seed", validation_status=ValidationStatus.VERIFIED,
            confidence=0.95, body=body))
        await repo.set_status(env.asset_id, ValidationStatus.PUBLISHED)

    await _pub(AssetType.ENV_PROFILE, "env_profile", EnvProfileBody(
        deploy="saas", worker_location="平台托管", intranet_access="public", account_type="test",
        base_url="http://localhost:9002", auth=AuthConfig(kind="token")).model_dump())

    spec = yaml.safe_load((BACK / "examples" / "ruoyi_oa.yaml").read_text(encoding="utf-8"))
    tmpl = oa_templates.match_template(spec)
    action = next(a for a in doc_parser.parse_openapi(spec) if a.name == "list_todo")
    body = build_connector_body(action, tenant="ph5", subsystem="A-OA",
                                success_rule=tmpl.success_rule() if tmpl else None)
    await _pub(AssetType.CONNECTOR, "list_todo", body.model_dump())


def _client():
    import dano.gateway.app as gw
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://t")


async def _tenant_key(c, tenant="ph5"):
    return (await c.post("/tenants", json={"tenant": tenant})).json()["api_key"]


async def test_catalog_auth_and_isolation():
    await _seed_ph5()
    c = _client()
    try:
        key = await _tenant_key(c)
        other_key = await _tenant_key(c, "ph5-other")
        skills = (await c.get("/v1/skills", headers={"X-Tenant-Key": key})).json()
        assert any(m["name"] == "A-OA.list_todo" for m in skills)
        assert (await c.get("/v1/skills")).status_code == 401               # 无 key
        assert (await c.get("/v1/skills", headers={"X-Tenant-Key": other_key})).json() == []  # 别家
        assert (await c.get("/v1/skills/A-OA.list_todo", headers={"X-Tenant-Key": key})).status_code == 200
        assert (await c.get("/v1/skills/A-OA.ghost", headers={"X-Tenant-Key": key})).status_code == 404
    finally:
        await c.aclose()


async def test_cors_preflight():
    c = _client()
    try:
        r = await c.options("/v1/skills", headers={"Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "X-Tenant-Key"})
        assert r.headers.get("access-control-allow-origin") == "*"
    finally:
        await c.aclose()


async def test_thin_invoke_real(mock_oa):
    await _seed_ph5()
    c = _client()
    try:
        key = await _tenant_key(c)
        r = await c.post("/v1/skills/A-OA.list_todo/invoke", headers={"X-Tenant-Key": key}, json={"input": {}})
        d = r.json()
        assert d["state"] == "completed", d
        assert d["exec_result"]["structured_output"].get("code") == 200
    finally:
        await c.aclose()


@pytest.mark.skipif(not os.environ.get("DANO_PI_API_KEY"), reason="需 DANO_PI_API_KEY")
async def test_e2e_onboard_then_invoke(mock_oa):
    """端到端:pi 自主接入 → 契约 → 瘦 invoke。"""
    from dano.onboarding import onboard
    spec = yaml.safe_load((BACK / "examples" / "ruoyi_oa.yaml").read_text(encoding="utf-8"))
    report = await onboard(tenant="ph5", subsystem="A-OA", system_instance_id="A-OA", openapi=spec,
                           deploy={"base_url": "http://localhost:9002", "auth": {"kind": "token"}},
                           credentials={"token": "ruoyi-mock-token-xyz"}, timeout_s=240.0)
    assert report.status == "completed" and "list_todo" in report.published_skills
    c = _client()
    try:
        key = await _tenant_key(c)
        skills = (await c.get("/v1/skills", headers={"X-Tenant-Key": key})).json()
        assert any(m["name"] == "A-OA.list_todo" for m in skills)
        r = await c.post("/v1/skills/A-OA.list_todo/invoke", headers={"X-Tenant-Key": key}, json={"input": {}})
        assert r.json()["state"] == "completed"
    finally:
        await c.aclose()
