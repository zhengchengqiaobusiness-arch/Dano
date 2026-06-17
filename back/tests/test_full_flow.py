"""完整流程验证:输入 OA 信息 + swagger → 自动产出『一套完整流程 = 一个 Skill』。

要点(对照用户诉求):**不是一个接口一个 Skill**,而是把多步接口编排成一个面向用户的复合 Skill。
- 确定性版(随时跑,无需 key):走真实管道——连接器(draft→sandbox→三模型评审→publish)
  + 复合流程(draft_workflow→sandbox_test_workflow→评审→publish);经网关 HTTP 验证:
    · GET /v1/skills 只出 1 个复合 Skill(submit_leave, integration=workflow)
    · 其步骤接口(start_leave_flow / submit_flow_task)被隐藏,用户看不到
    · POST invoke 这一个 Skill 即驱动整条多步流程跑通(taskId 串联)
- 真模型版(需 key):POST /onboarding 让 pi 读 swagger **自动发现流程**并编排成复合 Skill。
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
_DEPLOY = {"base_url": "http://localhost:9002", "auth": {"kind": "token"}}
_CREDS = {"token": "ruoyi-mock-token-xyz"}


@pytest.fixture(autouse=True)
async def _pg():
    os.environ["DANO_PG_DSN"] = _DSN
    os.environ["DANO_RUNTIME_CREDENTIALS"] = '{"full/oa": {"token": "ruoyi-mock-token-xyz"}}'
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='full'")
    import dano.gateway.app as gw
    from dano.lifecycle.state_machine import SkillLifecycle
    gw._lifecycle = SkillLifecycle()
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


def _client():
    import dano.gateway.app as gw
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://t")


class _PassBoard:
    """三审全过的 fake 评审委员会(3 个不同 model_id),不烧 key。"""
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        from dano.review.board import ReviewVerdict
        return [ReviewVerdict(role=r, model_id=m, passed=True, reasons=[])
                for r, m in (("acceptance", "fake-a"), ("security", "fake-b"), ("compliance", "fake-c"))]


async def _publish_env_profile():
    """发布环境画像(运行期 invoke 取 base_url+auth 用)。"""
    from dano.assets.repository import AssetRepository
    from dano.shared.asset_bodies import AuthConfig, EnvProfileBody
    from dano.shared.enums import AssetType, Subsystem, ValidationStatus
    from dano.shared.models import AssetEnvelope, Scope
    repo = AssetRepository()
    e = await repo.create(AssetEnvelope(
        asset_type=AssetType.ENV_PROFILE, scope=Scope(tenant="full", subsystem=Subsystem.OA),
        asset_key="env_profile", version=0, source_fingerprint="seed",
        validation_status=ValidationStatus.VERIFIED, confidence=0.9,
        body=EnvProfileBody(deploy="saas", worker_location="平台托管", intranet_access="public",
            account_type="test", base_url="http://localhost:9002",
            auth=AuthConfig(kind="token")).model_dump()))
    await repo.set_status(e.asset_id, ValidationStatus.PUBLISHED)


async def _publish_connector(run_id: str, action: str):
    """连接器走真实管道:draft→sandbox(真打 mock)→三模型评审→publish。"""
    from dano.agent_tools import tools as T
    d = await T.draft_connector(run_id, {"system_instance_id": "A-OA", "action": action})
    st = await T.sandbox_test(run_id, {"asset_draft_id": d["asset_draft_id"]})
    assert st["connect_passed"] and st["sandbox_passed"], st
    rev = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
    pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
        "validation_run_ids": st["validation_run_ids"], "review_run_ids": rev["review_run_ids"]})
    assert pub["published"], pub


async def test_full_flow_one_workflow_skill(mock_oa):
    """完整流程(确定性):多步接口 → 编排成 1 个复合 Skill,步骤隐藏,一次调用跑通整条。"""
    from dano.agent_tools import materials, tools as T
    from dano.capabilities.oa_templates import RuoYiFlowableTemplate

    run_id = "full-mech"
    # ① 输入 OA 信息 + swagger:登记接入材料(等价 POST /onboarding 的入参)
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="full", system_instance_id="A-OA", subsystem="A-OA",
        openapi=_spec(), deploy=_DEPLOY, credentials=_CREDS))
    await _publish_env_profile()

    T.set_review_board(_PassBoard())
    c = _client()
    try:
        # ② 流程的"零件"——多步连接器(最终对用户隐藏,不单独成 Skill)
        for action in ("start_leave_flow", "save_leave_form", "submit_flow_task"):
            await _publish_connector(run_id, action)

        # ③ 根据 swagger 内容把多步编排成"一套流程 = 一个 Skill"
        recipe = RuoYiFlowableTemplate().workflows()[0]      # submit_leave = 发起→存表单→提交
        dw = await T.draft_workflow(run_id, {"system_instance_id": "A-OA", "action": recipe.action,
            "title": recipe.title, "user_fields": recipe.user_fields,
            "required_fields": recipe.required_fields,
            "steps": [{"action": s.action, "inputs": s.inputs} for s in recipe.steps]})
        stw = await T.sandbox_test_workflow(run_id, {"asset_draft_id": dw["asset_draft_id"],
            "test_input": {"leaveType": "annual", "leaveDays": 1, "title": "测试请假", "reason": "回家"}})
        assert stw["passed"], stw["trace"]
        rev = await T.request_review(run_id, {"asset_draft_id": dw["asset_draft_id"]})
        pub = await T.publish_asset(run_id, {"asset_draft_id": dw["asset_draft_id"],
            "validation_run_ids": stw["validation_run_ids"], "review_run_ids": rev["review_run_ids"]})
        assert pub["published"], pub

        # ④ 网关契约:目录只出 1 个复合 Skill,步骤接口被隐藏(不是一接口一 Skill)
        key = (await c.post("/tenants", json={"tenant": "full"})).json()["api_key"]
        skills = (await c.get("/v1/skills", headers={"X-Tenant-Key": key})).json()
        by_name = {m["name"]: m for m in skills}
        assert "A-OA.submit_leave" in by_name, by_name
        assert by_name["A-OA.submit_leave"]["integration"] == "workflow"
        assert "A-OA.start_leave_flow" not in by_name, "步骤接口不应作为独立 Skill 暴露"
        assert "A-OA.save_leave_form" not in by_name, "步骤接口不应作为独立 Skill 暴露"
        assert "A-OA.submit_flow_task" not in by_name, "步骤接口不应作为独立 Skill 暴露"
        # 业务 Skill 恰好就是这一个复合流程(零件被折叠)
        assert [m["name"] for m in skills] == ["A-OA.submit_leave"], [m["name"] for m in skills]

        # ⑤ 调用这一个 Skill → 一次调用驱动整条多步流程(发起→taskId→提交)跑通
        r = await c.post("/v1/skills/A-OA.submit_leave/invoke", headers={"X-Tenant-Key": key},
                         json={"input": {"leaveType": "annual", "leaveDays": 2, "title": "张三", "reason": "回家"},
                               "confirm": True})
        assert r.json()["state"] == "completed", r.json()
    finally:
        T.set_review_board(None)
        await c.aclose()


@pytest.mark.skipif(not os.environ.get("DANO_PI_API_KEY"), reason="需 DANO_PI_API_KEY")
async def test_full_flow_real_pi_discovers_workflow(mock_oa):
    """完整流程(真模型):POST /onboarding → pi 读 swagger 自动发现流程 → 产出复合 Skill。"""
    import dano.gateway.app as gw
    c = _client()
    try:
        key = (await c.post("/tenants", json={"tenant": "full"})).json()["api_key"]
        rep = (await c.post("/onboarding", json={"tenant": "full", "subsystem": "A-OA",
               "openapi": _spec(), "deploy": _DEPLOY, "credentials": _CREDS})).json()
        assert rep["status"] == "completed", rep

        skills = (await c.get("/v1/skills", headers={"X-Tenant-Key": key})).json()
        workflow_skills = [m for m in skills if m.get("integration") == "workflow"]
        # pi 自动发现了多步流程并编排成复合 Skill(而非只产出一堆单接口 Skill)
        assert workflow_skills, f"未发现复合流程 Skill;目录={[m['name'] for m in skills]}"
        # 复合 Skill 的步骤接口不应再作为独立 Skill 暴露(已折叠隐藏)
        names = {m["name"].split(".", 1)[-1] for m in skills}
        assert not ({"start_leave_flow", "save_leave_form", "submit_flow_task"} & names), names
    finally:
        await c.aclose()
