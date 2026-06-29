"""M2 页面直驱:可观测回查(数据是否变的二态)+ 实时 DOM 选项。全离线,FakePageDriver,零浏览器。

对应 doc/PAGE_NATIVE_AGENT.md §6.3 / §7.5 / §9。承重点:回查是二态权威——数据没按预期变即"跑不通"。
"""

from __future__ import annotations

from uuid import uuid4

from dano.execution.page.driver import FakePageDriver
from dano.execution.page.readback import ReadbackView, snapshot_observable, verify_readback
from dano.execution.page.runtime import PageActionRuntime, _option_locator_for
from dano.shared.asset_bodies import PageAction, PageScriptBody
from dano.shared.enums import Outcome, RiskLevel

ROW = "css=.el-table__row"


# ───────────────────────── 回查引擎(snapshot + verify)─────────────────────────

async def test_readback_passes_when_data_changed():
    """提交后多一条含提交值的新记录 → 回查通过。"""
    drv = FakePageDriver(rows=["张三 2026-06-01 年假"])
    view = ReadbackView(verify_url="/list", row_locator=ROW, expect_contains=["事假", "2026-07-01"])
    before = await snapshot_observable(drv, view)
    assert before["count"] == 1
    drv.rows.append("李四 2026-07-01 事假")                    # 模拟提交副作用:列表多了我这条
    ok, ev = await verify_readback(drv, before=before, view=view, retries=1)
    assert ok is True and ev["passed"] is True
    assert ev["before_count"] == 1 and ev["after_count"] == 2 and ev["matched"] is True


async def test_readback_fails_when_data_unchanged():
    """计数没涨 → 不谎报,判未通过(治"DOM 提示成功但其实没建单")。"""
    drv = FakePageDriver(rows=["张三 2026-06-01 年假"])
    view = ReadbackView(verify_url="/list", row_locator=ROW, expect_contains=["事假"])
    before = await snapshot_observable(drv, view)
    ok, ev = await verify_readback(drv, before=before, view=view, retries=1)
    assert ok is False and ev["grew"] is False


async def test_readback_fails_when_new_row_value_mismatch():
    """新增了一行但不含我的提交值 → 不算我的成功(数据变了但不是我要的样子)。"""
    drv = FakePageDriver(rows=["张三 年假"])
    view = ReadbackView(verify_url="/list", row_locator=ROW, expect_contains=["事假", "2026-07-01"])
    before = await snapshot_observable(drv, view)
    drv.rows.append("王五 病假 2026-08-09")                    # 别人的单,不含我的值
    ok, ev = await verify_readback(drv, before=before, view=view, retries=1)
    assert ok is False and ev["grew"] is True and ev["matched"] is False


async def test_readback_no_expectation_counts_growth_only():
    """未声明 expect_contains → 仅以计数增长判定。"""
    drv = FakePageDriver(rows=[])
    view = ReadbackView(verify_url="/l", row_locator=ROW)
    before = await snapshot_observable(drv, view)
    drv.rows.append("某条")
    ok, ev = await verify_readback(drv, before=before, view=view, retries=1)
    assert ok is True and ev["matched"] is True


# ───────────────────────── 运行期集成:回查是二态权威 ─────────────────────────

def _leave_script(readback: dict | None) -> PageScriptBody:
    return PageScriptBody(
        actions=[
            PageAction(op="pick", locator="label=请假类型", value_from="field:leaveType"),
            PageAction(op="submit", locator="role=button[name=提交]"),
        ],
        dom_fingerprint="fp-v1", action="submit_leave", start_url="/leave",
        success_marker="text=提交成功", risk_level=RiskLevel.L3,
        readback=readback or {},
    )


async def test_runtime_readback_authoritative_pass():
    """有 readback:提交触发新行 + 含提交值 → PASSED 且 readback_passed。"""
    view = {"verify_url": "/my-leaves", "row_locator": ROW, "expect_contains": ["事假"]}
    drv = FakePageDriver(rows=["旧单 年假"], submit_adds_row="新单 事假 2026-07-01")

    res = await PageActionRuntime(lambda: drv).run(
        uuid4(), _leave_script(view), {"leaveType": "事假"}, confirm=lambda f: True)
    assert res.outcome == Outcome.PASSED
    assert res.structured_output["readback_passed"] is True
    assert res.structured_output["readback"]["after_count"] == 2


async def test_runtime_readback_authoritative_fail():
    """有 readback 但提交没真改数据(submit_adds_row=None)→ 即便点了提交也判 FAILED(不谎报)。"""
    view = {"verify_url": "/my-leaves", "row_locator": ROW, "expect_contains": ["事假"]}
    drv = FakePageDriver(rows=["旧单 年假"])                    # 提交不追加行

    res = await PageActionRuntime(lambda: drv).run(
        uuid4(), _leave_script(view), {"leaveType": "事假"}, confirm=lambda f: True)
    assert res.outcome == Outcome.FAILED
    assert res.structured_output["readback_passed"] is False


async def test_runtime_without_readback_unchanged_behavior():
    """空 readback → 行为与从前完全一致(成功标志判二态,不跑回查)。"""
    drv = FakePageDriver(visible=["text=提交成功"])             # 成功标志可见
    res = await PageActionRuntime(lambda: drv).run(
        uuid4(), _leave_script(None), {"leaveType": "事假"}, confirm=lambda f: True)
    assert res.outcome == Outcome.PASSED
    assert "readback" not in res.structured_output


# ───────────────────────── 实时 DOM 选项(Q4/Q6)─────────────────────────

def test_option_locator_resolution():
    script = _leave_script(None)
    assert _option_locator_for(script, "leaveType") == "label=请假类型"
    assert _option_locator_for(script, "不存在") is None


async def test_runtime_list_options_reads_live_dom():
    """实时选项:从活 DOM 读 leaveType 的候选,不调任何接口。"""
    opts = [{"label": "事假", "value": "事假"}, {"label": "病假", "value": "病假"},
            {"label": "年假", "value": "年假"}]
    drv = FakePageDriver(options=opts)
    out = await PageActionRuntime(lambda: drv).list_options(_leave_script(None), "leaveType")
    assert out["count"] == 3 and out["options"][0]["label"] == "事假"
    assert ("list_options", "label=请假类型") in drv.ops      # 真去 DOM 读了该字段


async def test_runtime_list_options_non_select_field():
    """非选择型字段 → options=[] + note,不开销。"""
    out = await PageActionRuntime(lambda: FakePageDriver()).list_options(_leave_script(None), "reason")
    assert out["count"] == 0 and out["options"] == []


# ───────────────────────── dry 评审豁免:页面型 acceptance 不因 dry 误杀 ─────────────────────────

def test_dry_mode_reason_covers_page_native_phrasings():
    """页面直驱默认 dry(submitted=false)→ acceptance 常以"未真实提交"否决,须识别为 dry 误判剔除;
    但 risk_level / 字段映射等真实缺陷不可误删。"""
    from dano.onboarding.repair import is_dry_mode_reason
    assert is_dry_mode_reason("因 submitted=false 无法确认业务是否达成")
    assert is_dry_mode_reason("dry 模式未真实提交,无法验证")
    assert is_dry_mode_reason("该操作未真正提交到系统")
    assert is_dry_mode_reason("self_check 仅构造未真发")
    assert not is_dry_mode_reason("risk_level 偏高,建议人工确认")      # 真实关切,不剔除
    assert not is_dry_mode_reason("字段映射错误:请假类型绑成了部门")  # 真实缺陷,不剔除
