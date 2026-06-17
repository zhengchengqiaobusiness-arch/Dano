"""Phase 2 验收:能力工具 + /_agent/tools/* 服务。

需 PostgreSQL(DANO_PG_DSN→dano_back);沙箱路径另起 ruoyi_mock_server(:9002)。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

BACK = Path(__file__).resolve().parent.parent
_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")
_MIGRATED = False


@pytest.fixture(autouse=True)
async def _pg():
    global _MIGRATED
    os.environ["DANO_PG_DSN"] = _DSN
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    if not _MIGRATED:
        await run_migrations(); _MIGRATED = True
    yield
    await close_pool()


def _wait_port(port: int, timeout: float = 15.0) -> bool:
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
        proc.terminate()
        pytest.skip("ruoyi_mock_server 未起来")
    yield "http://localhost:9002"
    proc.terminate()


def _register(run_id, *, with_deploy=False):
    from dano.agent_tools import materials
    spec = yaml.safe_load((BACK / "examples" / "ruoyi_oa.yaml").read_text(encoding="utf-8"))
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="ph2", system_instance_id="A-OA", subsystem="A-OA",
        openapi=spec,
        deploy={"base_url": "http://localhost:9002", "auth": {"kind": "token"}} if with_deploy else None,
        credentials={"token": "ruoyi-mock-token-xyz"},
    ))


# ── 工具:parse_spec 智能抽离 ──
async def test_parse_spec_filters_infra():
    from dano.agent_tools.tools import parse_spec
    _register("p2-parse")
    out = await parse_spec("p2-parse", {"system_instance_id": "A-OA"})
    names = {a["name"] for a in out["actions"]}
    assert "get_captcha" not in names and "login" not in names and "get_user_info" not in names
    assert {"start_leave_flow", "submit_flow_task", "list_todo"} <= names
    assert out["template"] == "ruoyi-flowable"


# ── 工具:save_draft + schema 校验 ──
async def test_save_draft_and_schema():
    from dano.agent_tools.tools import save_draft
    from dano.schemas import SchemaError
    _register("p2-draft")
    body = {"endpoint": "/workflow/todo/list", "method": "GET", "auth_kind": "token",
            "auth_ref": "vault://ph2/oa", "action": "list_todo"}
    r = await save_draft("p2-draft", {"system_instance_id": "A-OA", "asset_type": "connector",
                                      "asset_key": "list_todo", "body": body})
    assert r["asset_draft_id"] and r["content_hash"].startswith("sha256:")
    with pytest.raises(SchemaError):
        await save_draft("p2-draft", {"system_instance_id": "A-OA", "asset_type": "connector",
                                      "asset_key": "x", "body": {"foo": "bar"}})


# ── 发布闸门:无证据→拒 ──
async def test_publish_rejects_without_evidence():
    from dano.agent_tools.tools import publish_asset, save_draft
    _register("p2-gate")
    body = {"endpoint": "/x", "method": "GET", "auth_kind": "token",
            "auth_ref": "vault://ph2/oa", "action": "q"}
    d = await save_draft("p2-gate", {"system_instance_id": "A-OA", "asset_type": "connector",
                                     "asset_key": "q", "body": body})
    out = await publish_asset("p2-gate", {"asset_draft_id": d["asset_draft_id"], "validation_run_ids": []})
    assert out["published"] is False and "sandbox" in out["reason"]


class _PassBoard:
    """三审全过的 fake 评审委员会(3 个不同 model_id),不烧 key。"""
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        from dano.review.board import ReviewVerdict
        return [ReviewVerdict(role=r, model_id=m, passed=True, reasons=[])
                for r, m in (("acceptance", "fake-a"), ("security", "fake-b"), ("compliance", "fake-c"))]


# ── 全路径:沙箱试跑(真打 mock)→ 三模型评审 → 发布 ──
async def test_sandbox_then_publish(mock_oa):
    from dano.agent_tools import tools as T
    from dano.agent_tools.tools import publish_asset, request_review, sandbox_test, save_draft
    _register("p2-full", with_deploy=True)
    body = {"endpoint": "/workflow/todo/list", "method": "GET", "auth_kind": "token",
            "auth_ref": "vault://ph2/oa", "action": "list_todo"}
    d = await save_draft("p2-full", {"system_instance_id": "A-OA", "asset_type": "connector",
                                     "asset_key": "list_todo", "body": body})
    st = await sandbox_test("p2-full", {"asset_draft_id": d["asset_draft_id"]})
    assert st["connect_passed"] and st["sandbox_passed"], st
    T.set_review_board(_PassBoard())
    try:
        rev = await request_review("p2-full", {"asset_draft_id": d["asset_draft_id"]})
        assert rev["all_passed"], rev
        pub = await publish_asset("p2-full", {"asset_draft_id": d["asset_draft_id"],
            "validation_run_ids": st["validation_run_ids"], "review_run_ids": rev["review_run_ids"]})
        assert pub["published"] is True and pub["asset_id"]
    finally:
        T.set_review_board(None)


# ── /_agent/tools/* 服务安全 ──
async def test_tool_app_security():
    import httpx
    from dano.agent_tools.app import make_agent_app
    app = make_agent_app(token="T", run_id="p2-app")
    _register("p2-app")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # 坏令牌 401
        assert (await c.post("/_agent/tools/parse_spec", json={"run_id": "p2-app"},
                             headers={"X-Agent-Token": "bad"})).status_code == 401
        # 未白名单工具 404
        assert (await c.post("/_agent/tools/rm_rf", json={"run_id": "p2-app"},
                             headers={"X-Agent-Token": "T"})).status_code == 404
        # run_id 不符(令牌+run 合并校验)→ 401
        assert (await c.post("/_agent/tools/parse_spec", json={"run_id": "other"},
                             headers={"X-Agent-Token": "T"})).status_code == 401
        # 正常 200
        ok = await c.post("/_agent/tools/parse_spec", json={"run_id": "p2-app",
                          "params": {"system_instance_id": "A-OA"}}, headers={"X-Agent-Token": "T"})
        assert ok.status_code == 200 and ok.json()["count"] > 0
