"""Phase 3 端到端:pi 自主接入(parse_spec→draft_connector→sandbox_test→publish_asset)。

需:PostgreSQL(dano_back)+ DANO_PI_API_KEY(真调 DeepSeek)+ ruoyi_mock_server(:9002)。
缺任一则跳过。
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


@pytest.fixture
async def _pg():
    if not os.environ.get("DANO_PI_API_KEY"):
        pytest.skip("未设 DANO_PI_API_KEY,跳过 Phase 3 端到端")
    os.environ["DANO_PG_DSN"] = _DSN
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    # 清掉本测试租户旧资产,保证幂等
    from dano.infra.db import get_pool
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='ph3'")
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


async def test_pi_autonomous_onboarding(_pg, mock_oa):
    from dano.onboarding import onboard
    spec = yaml.safe_load((BACK / "examples" / "ruoyi_oa.yaml").read_text(encoding="utf-8"))
    report = await onboard(
        tenant="ph3", subsystem="A-OA", system_instance_id="A-OA",
        openapi=spec,
        deploy={"base_url": "http://localhost:9002", "auth": {"kind": "token"}},
        credentials={"token": "ruoyi-mock-token-xyz"},
        timeout_s=240.0,
    )
    assert report.status == "completed", report.error
    # pi 应自主发布出若干业务连接器(至少查询类 list_todo)
    assert "list_todo" in report.published_skills, report.published_skills
    assert len(report.published_skills) >= 3, report.published_skills
