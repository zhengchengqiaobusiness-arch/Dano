"""Phase 6 验收:goal 模式自动发现流程 → 复合 Skill。

确定性(无需 key):seed 步骤连接器 → draft_workflow(已知配方)→ sandbox_test_workflow(真打 mock)
  → publish → 目录出 submit_leave(integration=workflow,步骤隐藏)→ invoke 复合流程。
goal e2e(需 key):onboard(discover_workflows) → pi 自主发现 leave 流程 → 复合 Skill。
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
    os.environ["DANO_RUNTIME_CREDENTIALS"] = '{"ph6/oa": {"token": "ruoyi-mock-token-xyz"}}'
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='ph6'")
    import dano.gateway.app as gw
    from dano.lifecycle.state_machine import SkillLifecycle
    gw._lifecycle = SkillLifecycle()
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


_SPEC = None

# 复合流程机制的测试夹具:RuoYi 请假 3 步链(发起→存表单→提交)。
# 生产 leave.recipe() 不再预设步骤(真实代码由 LLM 生成),本链仅用于验证 draft_workflow
# →sandbox→评审→publish→invoke 的**编排机制**(步骤折叠/隐藏 + 一次调用串联跑通)。
_LEAVE_WF_STEPS = [
    {"action": "start_leave_flow", "inputs": {"templateId": "const:leave_template"}},
    {"action": "save_leave_form", "inputs": {
        "templateId": "const:leave_template",
        "taskId": "step:start_leave_flow.data.taskId",
        "procInstId": "step:start_leave_flow.data.procInsId",
        "title": "field:title", "valData.title": "field:title",
        "valData.leaveType": "field:leaveType", "valData.leaveDays": "field:leaveDays",
        "valData.reason": "field:reason",
    }},
    {"action": "submit_flow_task", "inputs": {
        "operateType": "const:200",
        "flowTask.taskId": "step:start_leave_flow.data.taskId",
        "flowTask.procInsId": "step:start_leave_flow.data.procInsId",
        "flowTask.executionId": "step:start_leave_flow.data.executionId",
        "flowTask.deployId": "step:start_leave_flow.data.deployId",
        "flowTask.defId": "step:start_leave_flow.data.procDefId",
        "flowTask.taskDefKey": "const:apply",
        "flowTask.businessId": "step:save_leave_form.data",
        "flowTask.templateId": "const:leave_template",
        "flowTask.title": "field:title",
    }},
]


def _spec():
    global _SPEC
    if _SPEC is None:
        _SPEC = yaml.safe_load((BACK / "examples" / "ruoyi_oa.yaml").read_text(encoding="utf-8"))
    return _SPEC


async def _seed_steps_and_materials(run_id: str):
    """发布 env_profile + start_leave_flow + submit_flow_task 连接器,并登记材料。"""
    from dano.agent_tools import materials
    from dano.agent_tools.connector_builder import build_connector_body
    from dano.assets.repository import AssetRepository
    from dano.capabilities import doc_parser, oa_templates
    from dano.shared.asset_bodies import AuthConfig, EnvProfileBody
    from dano.shared.enums import AssetType, Subsystem, ValidationStatus
    from dano.shared.models import AssetEnvelope, Scope

    deploy = {"base_url": "http://localhost:9002", "auth": {"kind": "token"}}
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="ph6", system_instance_id="A-OA", subsystem="A-OA",
        openapi=_spec(), deploy=deploy, credentials={"token": "ruoyi-mock-token-xyz"}))

    repo = AssetRepository(); scope = Scope(tenant="ph6", subsystem=Subsystem.OA)
    tmpl = oa_templates.match_template(_spec())

    async def _pub(at, key, body):
        e = await repo.create(AssetEnvelope(asset_type=at, scope=scope, asset_key=key, version=0,
            source_fingerprint="seed", validation_status=ValidationStatus.VERIFIED, confidence=0.9, body=body))
        await repo.set_status(e.asset_id, ValidationStatus.PUBLISHED)

    await _pub(AssetType.ENV_PROFILE, "env_profile", EnvProfileBody(deploy="saas",
        worker_location="平台托管", intranet_access="public", account_type="test",
        base_url="http://localhost:9002", auth=AuthConfig(kind="token")).model_dump())
    for name in ("start_leave_flow", "save_leave_form", "submit_flow_task"):
        action = next(a for a in doc_parser.parse_openapi(_spec()) if a.name == name)
        body = build_connector_body(action, tenant="ph6", subsystem="A-OA",
                                    success_rule=tmpl.success_rule() if tmpl else None)
        await _pub(AssetType.CONNECTOR, name, body.model_dump())


def _client():
    import dano.gateway.app as gw
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://t")


class _PassBoard:
    """三审全过的 fake 评审委员会(3 个不同 model_id),不烧 key。"""
    async def review(self, *, asset_type, asset_key, body, evidence=None):  # noqa: ANN001
        from dano.review.board import ReviewVerdict
        return [ReviewVerdict(role=r, model_id=m, passed=True, reasons=[])
                for r, m in (("acceptance", "fake-a"), ("security", "fake-b"), ("compliance", "fake-c"))]


async def test_workflow_generation_mechanics(mock_oa):
    """生成侧确定性验证:draft_workflow→sandbox_test_workflow→三审→publish→目录+隐藏步骤+invoke。"""
    from dano.agent_tools import tools as T
    from dano.capabilities.oa_templates import RuoYiFlowableTemplate

    run_id = "p6-mech"
    await _seed_steps_and_materials(run_id)
    recipe = RuoYiFlowableTemplate().workflows()[0]          # 业务画像(submit_leave 字段/标题)
    d = await T.draft_workflow(run_id, {"system_instance_id": "A-OA", "action": recipe.action,
        "title": recipe.title, "user_fields": recipe.user_fields,
        "required_fields": recipe.required_fields,
        "steps": _LEAVE_WF_STEPS})                           # 步骤链由测试夹具提供(机制验证)
    st = await T.sandbox_test_workflow(run_id, {"asset_draft_id": d["asset_draft_id"],
        "test_input": {"leaveType": "annual", "leaveDays": 1, "title": "测试请假", "reason": "回家"}})
    assert st["passed"], st["trace"]
    T.set_review_board(_PassBoard())
    c = _client()
    try:
        rev = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
        assert rev["all_passed"], rev
        pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
            "validation_run_ids": st["validation_run_ids"], "review_run_ids": rev["review_run_ids"]})
        assert pub["published"], pub

        # 目录:出复合 submit_leave(workflow),步骤动作隐藏;invoke 跑通整条
        key = (await c.post("/tenants", json={"tenant": "ph6"})).json()["api_key"]
        skills = {m["name"]: m for m in (await c.get("/v1/skills", headers={"X-Tenant-Key": key})).json()}
        assert "A-OA.submit_leave" in skills and skills["A-OA.submit_leave"]["integration"] == "workflow"
        assert "A-OA.start_leave_flow" not in skills and "A-OA.submit_flow_task" not in skills
        assert "A-OA.save_leave_form" not in skills
        r = await c.post("/v1/skills/A-OA.submit_leave/invoke", headers={"X-Tenant-Key": key},
                         json={"input": {"leaveType": "annual", "leaveDays": 2, "title": "张三", "reason": "回家"}, "confirm": True})
        assert r.json()["state"] == "completed", r.json()
    finally:
        T.set_review_board(None)
        await c.aclose()


async def test_ruoyi_leave_driver_factcheck_over_http(mock_oa):
    """驱动 + 流程9 事实核查走真 HTTP(mock 忠实模拟「不先存表单则 submit 空操作」)。

    - 完整链路(start→form/save→submit):apply.completed=True → real=True;
    - 空操作(直接 submit 不带 businessId):接口仍回『操作成功』,但事实核查 apply.completed=False。
    """
    from dano.capabilities.ruoyi_leave import RuoYiLeaveDriver

    async def call(method, path, body=None):
        async with httpx.AsyncClient(timeout=10) as cli:
            h = {"Authorization": "Bearer ruoyi-mock-token-xyz"}
            r = await (cli.get(mock_oa + path, headers=h) if method == "GET"
                       else cli.request(method, mock_oa + path, json=body, headers=h))
        return r.status_code, r.json()

    driver = RuoYiLeaveDriver(call)
    res = await driver.create_leave(
        {"title": "司机测试-年假", "leaveType": "annual", "leaveDays": 1, "reason": "x"})
    assert res.real is True and res.apply_completed is True
    assert res.business_id.startswith("BIZ-")

    # 空操作:发起后不存表单,直接 submit → 接口『操作成功』但节点不前进
    _, sf = await call("POST", "/workflow/handle/startFlow", {"templateId": "leave_template"})
    pins = sf["data"]["procInsId"]
    tid = sf["data"]["taskId"]
    _, ack = await call("POST", "/biz/flow/submit", {"flowTask": {"taskId": tid, "businessId": None}})
    assert ack["code"] == 200                                  # 接口骗你说成功
    completed, _ = await driver.fact_check(pins, "282", retries=2, backoff_s=0.0)
    assert completed is False                                  # 事实核查戳穿空操作


@pytest.mark.skipif(not os.environ.get("DANO_PI_API_KEY"), reason="需 DANO_PI_API_KEY")
async def test_e2e_goal_discovery(mock_oa):
    """goal 模式:onboard 让 pi 自主发现 leave 流程并编排成复合 Skill。"""
    from dano.onboarding import onboard
    report = await onboard(tenant="ph6", subsystem="A-OA", system_instance_id="A-OA", openapi=_spec(),
                           deploy={"base_url": "http://localhost:9002", "auth": {"kind": "token"}},
                           credentials={"token": "ruoyi-mock-token-xyz"}, discover_workflows=True,
                           use_codegen=False, timeout_s=300.0)
    assert report.status == "completed"
    # pi 发现的复合流程出现在已发布 Skill 里(submit_leave 或其他复合名)
    from dano.assets.repository import AssetRepository
    from dano.shared.enums import AssetType, Subsystem
    from dano.shared.models import Scope
    wfs = await AssetRepository().list_published(AssetType.WORKFLOW, Scope(tenant="ph6", subsystem=Subsystem.OA))
    assert len(wfs) >= 1, f"未发现任何多步流程;published={report.published_skills}"
