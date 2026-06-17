"""Phase 4 验收:保障期(流程10 熔断暂停 / 流程11 pi 自愈 / 流程12 生命周期)。

种子法(无需 key):报失败达阈值→暂停;暂停 Skill 被 invoke 拒;恢复;生命周期查询。
e2e(需 key):pi 接入→报失败暂停→invoke 409→pi 自愈→invoke 恢复。
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
    os.environ["DANO_RUNTIME_CREDENTIALS"] = '{"ph4/oa": {"token": "ruoyi-mock-token-xyz"}}'
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='ph4'")
    # 复位网关单例
    import dano.gateway.app as gw
    from dano.lifecycle.state_machine import SkillLifecycle
    from dano.resilience.circuit_breaker import InMemoryCounter
    gw._lifecycle = SkillLifecycle(); gw._breaker = InMemoryCounter()
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


async def _seed_ph4():
    from dano.agent_tools.connector_builder import build_connector_body
    from dano.assets.repository import AssetRepository
    from dano.capabilities import doc_parser, oa_templates
    from dano.shared.asset_bodies import AuthConfig, EnvProfileBody
    from dano.shared.enums import AssetType, Subsystem, ValidationStatus
    from dano.shared.models import AssetEnvelope, Scope
    repo = AssetRepository(); scope = Scope(tenant="ph4", subsystem=Subsystem.OA)

    async def _pub(at, key, body):
        e = await repo.create(AssetEnvelope(asset_type=at, scope=scope, asset_key=key, version=0,
            source_fingerprint="seed", validation_status=ValidationStatus.VERIFIED, confidence=0.9, body=body))
        await repo.set_status(e.asset_id, ValidationStatus.PUBLISHED)

    await _pub(AssetType.ENV_PROFILE, "env_profile", EnvProfileBody(deploy="saas",
        worker_location="平台托管", intranet_access="public", account_type="test",
        base_url="http://localhost:9002", auth=AuthConfig(kind="token")).model_dump())
    spec = yaml.safe_load((BACK / "examples" / "ruoyi_oa.yaml").read_text(encoding="utf-8"))
    tmpl = oa_templates.match_template(spec)
    action = next(a for a in doc_parser.parse_openapi(spec) if a.name == "list_todo")
    body = build_connector_body(action, tenant="ph4", subsystem="A-OA",
                                success_rule=tmpl.success_rule() if tmpl else None)
    await _pub(AssetType.CONNECTOR, "list_todo", body.model_dump())


def _client():
    import dano.gateway.app as gw
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://t")


# ── 流程10:报失败达阈值 → 暂停(流程12)──
async def test_report_failure_suspends_at_threshold():
    from dano.assurance.service import FailureEvent, report_failure
    from dano.lifecycle.state_machine import SkillLifecycle
    from dano.resilience.circuit_breaker import InMemoryCounter
    from dano.shared.enums import SkillState, Subsystem
    lc = SkillLifecycle(); br = InMemoryCounter()
    await lc.register_published("A-OA.list_todo", Subsystem.OA, "list_todo", 1)
    ev = FailureEvent(tenant_id="ph4", skill_id="A-OA.list_todo", failure_type="field_changed")
    d1 = await report_failure(ev, lifecycle=lc, breaker=br, threshold=3)
    d2 = await report_failure(ev, lifecycle=lc, breaker=br, threshold=3)
    d3 = await report_failure(ev, lifecycle=lc, breaker=br, threshold=3)
    assert not d1.suspended and not d2.suspended and d3.suspended
    assert d3.self_heal_recommended                      # field_changed → 建议自愈
    assert (await lc.store.get("A-OA.list_todo")).state == SkillState.SUSPENDED


# ── 流程12:暂停的 Skill 被 invoke 拒绝(409)──
async def test_suspended_skill_rejected_by_invoke(mock_oa):
    import dano.gateway.app as gw
    from dano.shared.enums import Subsystem
    await _seed_ph4()
    await gw._lifecycle.register_published("A-OA.list_todo", Subsystem.OA, "list_todo", 1)
    c = _client()
    try:
        key = (await c.post("/tenants", json={"tenant": "ph4"})).json()["api_key"]
        # 未暂停:可调
        assert (await c.post("/v1/skills/A-OA.list_todo/invoke", headers={"X-Tenant-Key": key},
                             json={"input": {}})).json()["state"] == "completed"
        # 暂停后:409
        await gw._lifecycle.suspend("A-OA.list_todo")
        r = await c.post("/v1/skills/A-OA.list_todo/invoke", headers={"X-Tenant-Key": key}, json={"input": {}})
        assert r.status_code == 409
    finally:
        await c.aclose()


# ── 流程11/12:恢复到「已发布」(版本+1,旧版可回滚)──
async def test_recover_to_published():
    from dano.lifecycle.state_machine import SkillLifecycle
    from dano.shared.enums import SkillState, Subsystem
    lc = SkillLifecycle()
    await lc.register_published("A-OA.list_todo", Subsystem.OA, "list_todo", 1)
    await lc.suspend("A-OA.list_todo")
    rec = await lc.recover_to_published("A-OA.list_todo", 2)
    assert rec.state == SkillState.PUBLISHED and rec.asset_version == 2


# ── 生命周期查询端点 ──
async def test_lifecycle_skills_endpoint():
    import dano.gateway.app as gw
    from dano.shared.enums import Subsystem
    await gw._lifecycle.register_published("A-OA.list_todo", Subsystem.OA, "list_todo", 1)
    c = _client()
    try:
        rows = (await c.get("/lifecycle/skills")).json()
        assert any(r["skill_id"] == "A-OA.list_todo" and r["state"] == "已发布" for r in rows)
    finally:
        await c.aclose()


# ── e2e:pi 接入 → 报失败暂停 → invoke 409 → pi 自愈 → 恢复可调 ──
@pytest.mark.skipif(not os.environ.get("DANO_PI_API_KEY"), reason="需 DANO_PI_API_KEY")
async def test_e2e_failure_then_self_heal(mock_oa):
    import dano.gateway.app as gw
    spec = yaml.safe_load((BACK / "examples" / "ruoyi_oa.yaml").read_text(encoding="utf-8"))
    deploy = {"base_url": "http://localhost:9002", "auth": {"kind": "token"}}
    creds = {"token": "ruoyi-mock-token-xyz"}
    c = _client()
    try:
        key = (await c.post("/tenants", json={"tenant": "ph4"})).json()["api_key"]
        # 接入
        rep = (await c.post("/onboarding", json={"tenant": "ph4", "subsystem": "A-OA",
               "openapi": spec, "deploy": deploy, "credentials": creds})).json()
        assert rep["status"] == "completed" and "list_todo" in rep["published_skills"]
        # 报失败 ×3 → 暂停
        for _ in range(3):
            await c.post("/assurance/report-failure", json={"tenant_id": "ph4",
                "skill_id": "A-OA.list_todo", "failure_type": "field_changed"})
        assert (await c.post("/v1/skills/A-OA.list_todo/invoke", headers={"X-Tenant-Key": key},
                             json={"input": {}})).status_code == 409
        # 自愈 → 恢复可调
        out = (await c.post("/assurance/self-heal", json={"tenant": "ph4", "subsystem": "A-OA",
               "openapi": spec, "deploy": deploy, "credentials": creds})).json()
        assert "A-OA.list_todo" in out["recovered"]
        assert (await c.post("/v1/skills/A-OA.list_todo/invoke", headers={"X-Tenant-Key": key},
                             json={"input": {}})).json()["state"] == "completed"
    finally:
        await c.aclose()
