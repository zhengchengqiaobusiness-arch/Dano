"""Phase 7 验收:三模型评审委员会硬闸门(成果验收 / 漏洞检测 / 合规审核)。

确定性(注入 fake board,不烧 key):
- 连接器沙箱过但未评审 → publish 被拦(缺评审角色)。
- 某审 reject → request_review.all_passed=False,带该 review_run_ids 的 publish 被拦。
- 三审全过(3 个不同 model_id)→ publish 成功。
- 同一模型用 3 次 → verify_reviewed 拒(强制 3 个不同模型)。
e2e(需 DANO_PI_API_KEY):真起 3 个 DeepSeek 模型评一个真连接器。
PG + mock(:9002)门控。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

import pytest
import yaml

BACK = Path(__file__).resolve().parent.parent
_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")


@pytest.fixture(autouse=True)
async def _pg():
    os.environ["DANO_PG_DSN"] = _DSN
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='ph7'")
    yield
    from dano.agent_tools import tools as T
    T.set_review_board(None)
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


_SPEC = None


def _spec():
    global _SPEC
    if _SPEC is None:
        _SPEC = yaml.safe_load((BACK / "examples" / "ruoyi_oa.yaml").read_text(encoding="utf-8"))
    return _SPEC


def _verdicts(*triples):
    from dano.review.board import ReviewVerdict
    return [ReviewVerdict(role=r, model_id=m, passed=p, reasons=list(rs))
            for (r, m, p, rs) in triples]


class _PassBoard:
    """三审全过的 fake 评审委员会(3 个不同 model_id),不烧 key。"""
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        return _verdicts(("acceptance", "fake-a", True, []),
                         ("security", "fake-b", True, []),
                         ("compliance", "fake-c", True, []))


class _RejectSecurityBoard:
    """漏洞检测审驳回(其余通过)。"""
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        return _verdicts(("acceptance", "fake-a", True, []),
                         ("security", "fake-b", False, ["发现写操作缺幂等键(疑似)"]),
                         ("compliance", "fake-c", True, []))


async def _register_materials(run_id: str):
    from dano.agent_tools import materials
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="ph7", system_instance_id="A-OA", subsystem="A-OA",
        openapi=_spec(),
        deploy={"base_url": "http://localhost:9002", "auth": {"kind": "token"}},
        credentials={"token": "ruoyi-mock-token-xyz"}))


async def _draft_sandbox_connector(run_id: str, action: str = "list_startable_templates"):
    """建只读连接器草案 + 沙箱过(真打 mock)。返回 (draft, sandbox_result)。"""
    from dano.agent_tools import tools as T
    d = await T.draft_connector(run_id, {"system_instance_id": "A-OA", "action": action})
    st = await T.sandbox_test(run_id, {"asset_draft_id": d["asset_draft_id"]})
    assert st["connect_passed"] and st["sandbox_passed"], st
    return d, st


async def test_publish_blocked_without_review(mock_oa):
    """沙箱过但未评审 → 发布被三模型闸门拦。"""
    from dano.agent_tools import tools as T
    run_id = "p7-noreview"
    await _register_materials(run_id)
    d, st = await _draft_sandbox_connector(run_id)
    pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
                                         "validation_run_ids": st["validation_run_ids"]})
    assert not pub["published"], pub
    assert "评审" in pub["reason"], pub


async def test_publish_blocked_when_reviewer_rejects(mock_oa):
    """漏洞检测审驳回 → request_review.all_passed=False,带该证据 publish 仍被拦。"""
    from dano.agent_tools import tools as T
    run_id = "p7-reject"
    await _register_materials(run_id)
    d, st = await _draft_sandbox_connector(run_id)
    T.set_review_board(_RejectSecurityBoard())
    rev = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
    assert not rev["all_passed"], rev
    pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
        "validation_run_ids": st["validation_run_ids"], "review_run_ids": rev["review_run_ids"]})
    assert not pub["published"], pub
    assert "security" in pub["reason"], pub


async def test_publish_succeeds_after_three_model_review(mock_oa):
    """三审全过(3 个不同模型)→ 发布成功。"""
    from dano.agent_tools import tools as T
    run_id = "p7-pass"
    await _register_materials(run_id)
    d, st = await _draft_sandbox_connector(run_id)
    T.set_review_board(_PassBoard())
    rev = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
    assert rev["all_passed"], rev
    assert len({v["model"] for v in rev["verdicts"]}) == 3, rev
    pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
        "validation_run_ids": st["validation_run_ids"], "review_run_ids": rev["review_run_ids"]})
    assert pub["published"], pub


async def test_gate_requires_three_distinct_models(mock_oa):
    """三审齐、全过,但同一 model_id → 闸门拒(强制 3 个不同模型)。"""
    from dano.agent_tools import tools as T
    from dano.assets.drafts import DraftStore
    run_id = "p7-distinct"
    await _register_materials(run_id)
    d, _ = await _draft_sandbox_connector(run_id)
    ds = DraftStore()
    did = UUID(d["asset_draft_id"])
    ids = []
    for role in ("acceptance", "security", "compliance"):
        rr = await ds.record_review(asset_draft_id=did, role=role, model_id="same-model", passed=True)
        ids.append(rr.review_run_id)
    ok, reason = await ds.verify_reviewed(did, ids)
    assert not ok and "不同模型" in reason, reason


@pytest.mark.skipif(not os.environ.get("DANO_PI_API_KEY"), reason="需 DANO_PI_API_KEY")
async def test_e2e_three_model_review_real(mock_oa):
    """真起 3 个 DeepSeek 模型评一个干净的只读连接器:三审齐、3 个不同模型、应通过。"""
    from dano.agent_tools import tools as T
    run_id = "p7-e2e"
    await _register_materials(run_id)
    d, _ = await _draft_sandbox_connector(run_id)
    T.set_review_board(None)        # 用真实 from_settings(三个不同 DeepSeek 模型)
    rev = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
    assert len(rev["verdicts"]) == 3, rev
    assert len({v["model"] for v in rev["verdicts"]}) == 3, rev
    assert rev["all_passed"], rev      # 干净只读连接器:成果/漏洞/合规均应通过
