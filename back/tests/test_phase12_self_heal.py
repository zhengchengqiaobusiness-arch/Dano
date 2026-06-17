"""Phase B2 验收:增量自愈(流程11)。

确定性(PG + mock + 注入 fake 评审,无需 key):
- 发布两个连接器 v1,只暂停其一 → self_heal(增量)只重生成暂停的那个(新版本 v2,旧版保留),
  恢复其 Skill 到「已发布」;未暂停的连接器不被触碰(仍 v1)。
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
        await c.execute("DELETE FROM assets WHERE tenant='ph12'")
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


def _spec():
    return yaml.safe_load((BACK / "examples" / "ruoyi_oa.yaml").read_text(encoding="utf-8"))


class _PassBoard:
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        from dano.review.board import ReviewVerdict
        return [ReviewVerdict(role=r, model_id=m, passed=True, reasons=[])
                for r, m in (("acceptance", "fake-a"), ("security", "fake-b"), ("compliance", "fake-c"))]


_DEPLOY = {"base_url": "http://localhost:9002", "auth": {"kind": "token"}}
_CREDS = {"token": "ruoyi-mock-token-xyz"}


async def _publish_connector(run_id: str, action: str):
    from dano.agent_tools import tools as T
    d = await T.draft_connector(run_id, {"system_instance_id": "A-OA", "action": action})
    st = await T.sandbox_test(run_id, {"asset_draft_id": d["asset_draft_id"]})
    assert st["connect_passed"] and st["sandbox_passed"], st
    rev = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
    pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
        "validation_run_ids": st["validation_run_ids"], "review_run_ids": rev["review_run_ids"]})
    assert pub["published"], pub


async def test_incremental_heals_only_suspended(mock_oa):
    from dano.agent_tools import materials, tools as T
    from dano.assets.repository import AssetRepository
    from dano.assurance.service import self_heal
    from dano.lifecycle.state_machine import SkillLifecycle
    from dano.shared.enums import AssetType, SkillState, Subsystem
    from dano.shared.models import Scope

    T.set_review_board(_PassBoard())
    run_id = "ph12-seed"
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="ph12", system_instance_id="A-OA", subsystem="A-OA",
        openapi=_spec(), deploy=_DEPLOY, credentials=_CREDS))

    # 两个连接器 v1 + 登记生命周期
    lc = SkillLifecycle()
    for action in ("list_startable_templates", "list_todo"):
        await _publish_connector(run_id, action)
        await lc.register_published(f"A-OA.{action}", Subsystem.OA, action, 1)

    # 只暂停其一(模拟该 Skill 漂移失败达阈值)
    await lc.suspend("A-OA.list_startable_templates")

    # 增量自愈(自动取暂停的)
    out = await self_heal(tenant="ph12", subsystem="A-OA", openapi=_spec(),
                          deploy=_DEPLOY, credentials=_CREDS, lifecycle=lc)
    assert out["mode"] == "incremental", out
    assert "A-OA.list_startable_templates" in out["recovered"], out
    assert "A-OA.list_todo" not in out["recovered"], out      # 未暂停 → 不碰

    # 生命周期:暂停的已恢复到已发布且版本 +1;未暂停的不变
    healed = await lc.store.get("A-OA.list_startable_templates")
    assert healed.state == SkillState.PUBLISHED and healed.asset_version == 2, healed
    untouched = await lc.store.get("A-OA.list_todo")
    assert untouched.state == SkillState.PUBLISHED and untouched.asset_version == 1, untouched

    # 资产版本:自愈的有 2 版(旧版保留可回滚),未受影响的仍 1 版
    scope = Scope(tenant="ph12", subsystem=Subsystem.OA)
    repo = AssetRepository()
    assert len(await repo.list_versions(AssetType.CONNECTOR, scope, "list_startable_templates")) >= 2
    assert len(await repo.list_versions(AssetType.CONNECTOR, scope, "list_todo")) == 1
