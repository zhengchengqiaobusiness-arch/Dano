"""M4 端到端(确定性,无 LLM):真实浏览器 + 真实 PG 跑通页面接入全链路。

scout_page(真侦察)→ draft_page_script(确定性建体 + 存草案)→ sandbox_replay(写页面 dry 回放,记 replay 证据)
→ request_review(写页面三模型评审,注入 fake board)→ publish_asset(发布硬闸门)→ 派生页面 Skill。

PG 或 chromium 任一不可用 → 整文件 skip。用唯一 run_id/tenant + 跑后清理,PG 幂等无残留。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

pytest.importorskip("playwright")
pytest.importorskip("asyncpg")

from dano.agent_tools import materials, tools
from dano.assets.repository import AssetRepository
from dano.execution.page.driver import PlaywrightPageDriver
from dano.infra.db import close_pool, get_pool, init_pool
from dano.orchestrator.skills import SkillRegistry
from dano.shared.enums import Subsystem

_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form>
  <label for="amt">金额</label><input id="amt" name="amount" type="text">
  <label for="cat">类别</label>
  <select id="cat" name="category"><option value="">--</option><option value="差旅">差旅</option></select>
  <button type="button" id="sub" onclick="document.getElementById('ok').style.display='block'">提交</button>
</form>
<div id="ok" style="display:none">保存成功</div>
</body></html>"""


class _Verdict:
    def __init__(self, role: str) -> None:
        self.role, self.model_id, self.passed, self.reasons = role, f"fake-{role}", True, []


class _FakeBoard:
    """三模型评审 fake:三个角色各返回不同模型、全通过(测试写页面评审闸门,不烧 LLM)。"""

    async def review(self, *, asset_type, asset_key, body, evidence):  # noqa: ANN001
        return [_Verdict(r) for r in ("acceptance", "security", "compliance")]


async def _pg_ready() -> bool:
    try:
        await init_pool()
        return True
    except Exception:  # noqa: BLE001
        return False


async def _chromium_ready() -> bool:
    try:
        d, _ = await PlaywrightPageDriver.launch(headless=True)
        await d.close()
        return True
    except Exception:  # noqa: BLE001
        return False


async def test_page_onboarding_e2e(tmp_path) -> None:  # noqa: ANN001
    if not await _pg_ready():
        pytest.skip("PG 不可用")
    if not await _chromium_ready():
        await close_pool()
        pytest.skip("chromium 不可用")

    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    url = page.as_uri()
    run_id = f"page-e2e-{uuid4().hex[:8]}"
    tenant = f"page-e2e-{uuid4().hex[:8]}"
    sid = Subsystem.REIMBURSE.value   # sandbox_replay 按 draft.subsystem.value 反查材料,故 sid==subsystem

    materials.register(materials.MaterialContext(
        run_id=run_id, tenant=tenant, system_instance_id=sid, subsystem=sid, deploy={}, credentials={}))
    tools.set_review_board(_FakeBoard())
    try:
        # 1. 真实侦察
        sc = await tools.scout_page(run_id, {"system_instance_id": sid, "start_url": url})
        assert sc["submit_locator"] == "role=button[name=提交]"
        assert {"amount", "category"} <= {f["name"] for f in sc["fields"]}

        # 2. 确定性建体 + 存草案(写页面 → L3 + 需评审)
        dr = await tools.draft_page_script(run_id, {
            "system_instance_id": sid, "action": "submit_reimburse",
            "steps": sc["suggested_steps"], "dom_fingerprint": sc["dom_fingerprint"],
            "start_url": url, "success_marker": "text=保存成功", "title": "提交报销"})
        assert dr["risk_level"] == "L3" and dr["needs_review"] is True

        # 3. 沙箱回放(写页面默认 dry:不真点提交)→ 记 replay 证据
        rp = await tools.sandbox_replay(run_id, {
            "asset_draft_id": dr["asset_draft_id"],
            "sample_inputs": {"amount": "100", "category": "差旅"}})
        assert rp["passed"] is True and rp["mode"] == "dry"

        # 4. 三模型评审(写页面)
        rv = await tools.request_review(run_id, {"asset_draft_id": dr["asset_draft_id"]})
        assert rv["all_passed"] is True and len(rv["review_run_ids"]) == 3

        # 5. 发布硬闸门(后端重读 replay 证据 + 评审证据)
        pub = await tools.publish_asset(run_id, {
            "asset_draft_id": dr["asset_draft_id"],
            "validation_run_ids": rp["validation_run_ids"],
            "review_run_ids": rv["review_run_ids"]})
        assert pub["published"] is True, pub

        # 6. 派生页面 Skill(无 API,目录可见)
        reg = await SkillRegistry.from_store(AssetRepository(), tenant=tenant,
                                             subsystems=[Subsystem.REIMBURSE])
        skill = reg.by_action(Subsystem.REIMBURSE, "submit_reimburse")
        assert skill is not None and skill.has_api is False and skill.page_asset_id is not None
    finally:
        tools.set_review_board(None)
        try:
            async with get_pool().acquire() as c:
                await c.execute("DELETE FROM asset_drafts WHERE run_id=$1", run_id)  # 级联 validation/review
                await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        finally:
            materials.clear_run(run_id)
            await close_pool()


async def test_run_page_onboarding_entrypoint(tmp_path) -> None:  # noqa: ANN001
    """网关 /onboarding/page 实际调用的入口 run_page_onboarding 一把跑通(写报销页面)。"""
    if not await _pg_ready():
        pytest.skip("PG 不可用")
    if not await _chromium_ready():
        await close_pool()
        pytest.skip("chromium 不可用")
    from dano.onboarding.page_onboard import run_page_onboarding

    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    run_id = f"page-fn-{uuid4().hex[:8]}"
    tenant = f"page-fn-{uuid4().hex[:8]}"
    tools.set_review_board(_FakeBoard())
    try:
        report = await run_page_onboarding(
            tenant=tenant, subsystem=Subsystem.REIMBURSE.value, start_url=page.as_uri(),
            action="submit_reimburse", title="提交报销", success_marker="text=保存成功",
            sample_inputs={"amount": "100", "category": "差旅"}, run_id=run_id)
        assert report["ok"] is True and report["risk_level"] == "L3" and report["mode"] == "dry"
        reg = await SkillRegistry.from_store(AssetRepository(), tenant=tenant,
                                             subsystems=[Subsystem.REIMBURSE])
        assert reg.by_action(Subsystem.REIMBURSE, "submit_reimburse") is not None
    finally:
        tools.set_review_board(None)
        try:
            async with get_pool().acquire() as c:
                await c.execute("DELETE FROM asset_drafts WHERE run_id=$1", run_id)
                await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        finally:
            await close_pool()


async def test_scout_then_onboard_with_edited_steps(tmp_path) -> None:  # noqa: ANN001
    """前端向导真实路径:scout_page_only 预览 → 改字段映射(标必填)→ 带 steps+fingerprint 发布。"""
    if not await _pg_ready():
        pytest.skip("PG 不可用")
    if not await _chromium_ready():
        await close_pool()
        pytest.skip("chromium 不可用")
    from dano.onboarding.page_onboard import run_page_onboarding, scout_page_only

    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    tenant = f"page-edit-{uuid4().hex[:8]}"
    run_id = f"page-edit-{uuid4().hex[:8]}"
    sid = Subsystem.REIMBURSE.value
    tools.set_review_board(_FakeBoard())
    try:
        sc = await scout_page_only(tenant=tenant, subsystem=sid, start_url=page.as_uri())
        assert sc["dom_fingerprint"] and sc["suggested_steps"]
        # 模拟前端编辑:把字段步标为必填(scout 默认非必填),保留提交步
        steps = []
        for s in sc["suggested_steps"]:
            if s["op"] != "submit":
                s = {**s, "required": True}
            steps.append(s)
        report = await run_page_onboarding(
            tenant=tenant, subsystem=sid, start_url=page.as_uri(), action="submit_reimburse",
            title="提交报销", success_marker="text=保存成功",
            sample_inputs={"amount": "66", "category": "差旅"},
            steps=steps, dom_fingerprint=sc["dom_fingerprint"], run_id=run_id)
        assert report["ok"] is True
        reg = await SkillRegistry.from_store(AssetRepository(), tenant=tenant,
                                             subsystems=[Subsystem.REIMBURSE])
        skill = reg.by_action(Subsystem.REIMBURSE, "submit_reimburse")
        assert skill is not None and "amount" in skill.required_fields   # 编辑后的必填生效
    finally:
        tools.set_review_board(None)
        try:
            async with get_pool().acquire() as c:
                await c.execute("DELETE FROM asset_drafts WHERE run_id=$1", run_id)
                await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        finally:
            await close_pool()


async def test_page_drift_self_heal(tmp_path) -> None:  # noqa: ANN001
    """M6 漂移自愈:页面 Skill 暂停 → self_heal 重新侦察 → 发布新版本 → 恢复到已发布。"""
    if not await _pg_ready():
        pytest.skip("PG 不可用")
    if not await _chromium_ready():
        await close_pool()
        pytest.skip("chromium 不可用")
    from dano.assets.repository import AssetRepository as Repo
    from dano.assurance import service as assurance
    from dano.lifecycle.state_machine import InMemorySkillStore, SkillLifecycle
    from dano.onboarding.page_onboard import run_page_onboarding
    from dano.shared.enums import AssetType, SkillState
    from dano.shared.models import Scope

    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    tenant = f"page-heal-{uuid4().hex[:8]}"
    sid = Subsystem.REIMBURSE.value
    skill_id = f"{sid}.submit_reimburse"
    repo = Repo()
    scope = Scope(tenant=tenant, subsystem=Subsystem.REIMBURSE)
    tools.set_review_board(_FakeBoard())
    try:
        rep = await run_page_onboarding(
            tenant=tenant, subsystem=sid, start_url=page.as_uri(), action="submit_reimburse",
            title="提交报销", success_marker="text=保存成功", sample_inputs={"amount": "1"})
        assert rep["ok"] is True
        v1 = await repo.get_published(AssetType.PAGE_SCRIPT, scope, asset_key="submit_reimburse")

        lifecycle = SkillLifecycle(InMemorySkillStore())
        await lifecycle.register_published(skill_id, Subsystem.REIMBURSE, "submit_reimburse", version=1)
        await lifecycle.suspend(skill_id)
        assert (await lifecycle.store.get(skill_id)).state == SkillState.SUSPENDED

        res = await assurance.self_heal(
            tenant=tenant, subsystem=sid, openapi={}, deploy={}, credentials={},
            lifecycle=lifecycle, actions=["submit_reimburse"], incremental=True)
        assert skill_id in res["recovered"], res
        assert (await lifecycle.store.get(skill_id)).state == SkillState.PUBLISHED
        v2 = await repo.get_published(AssetType.PAGE_SCRIPT, scope, asset_key="submit_reimburse")
        assert v2 is not None and v2.version > v1.version    # 发布了新版本(旧版保留可回滚)
    finally:
        tools.set_review_board(None)
        try:
            async with get_pool().acquire() as c:
                await c.execute("DELETE FROM asset_drafts WHERE tenant=$1", tenant)
                await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        finally:
            await close_pool()


async def test_import_codegen_e2e(tmp_path) -> None:  # noqa: ANN001
    """方式A 端到端:Playwright codegen(指向本地表单)→ 解析 → 真浏览器 dry 回放 → 评审 → 发布。"""
    if not await _pg_ready():
        pytest.skip("PG 不可用")
    if not await _chromium_ready():
        await close_pool()
        pytest.skip("chromium 不可用")
    from dano.execution.page.codegen_import import parse_playwright_codegen
    from dano.onboarding.page_onboard import run_page_onboarding

    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    url = page.as_uri()
    codegen = (
        f'page.goto("{url}")\n'
        'page.get_by_label("金额").fill("100")\n'
        'page.get_by_label("类别").select_option("差旅")\n'
        'page.get_by_role("button", name="提交").click()\n'
    )
    steps, parsed_url, samples = parse_playwright_codegen(codegen)
    assert parsed_url == url and any(s.op == "submit" for s in steps)

    tenant = f"page-imp-{uuid4().hex[:8]}"
    run_id = f"page-imp-{uuid4().hex[:8]}"
    tools.set_review_board(_FakeBoard())
    try:
        report = await run_page_onboarding(
            tenant=tenant, subsystem=Subsystem.REIMBURSE.value, start_url=url,
            action="submit_reimburse", title="提交报销", success_marker="text=保存成功",
            sample_inputs=samples, steps=[s.model_dump() for s in steps],
            dom_fingerprint="", run_id=run_id)
        assert report["ok"] is True and report["mode"] == "dry", report
    finally:
        tools.set_review_board(None)
        try:
            async with get_pool().acquire() as c:
                await c.execute("DELETE FROM asset_drafts WHERE run_id=$1", run_id)
                await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        finally:
            await close_pool()
