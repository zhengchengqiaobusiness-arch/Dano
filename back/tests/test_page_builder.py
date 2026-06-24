"""M4 确定性核心:page_builder 把录制步骤建成校验过的 PageScriptBody。

纯离线:不碰 PG / 浏览器 / LLM。并验证建出的脚本能被运行期解释器直接执行(builder→runtime 闭环)。
"""
from __future__ import annotations

from uuid import uuid4

from dano.agent_tools.page_builder import RecordedStep, assign_field_keys, build_page_script
from dano.execution.page import FakePageDriver, PageActionRuntime
from dano.shared.enums import Outcome, RiskLevel


def _reimburse_steps() -> list[RecordedStep]:
    return [
        RecordedStep(op="goto", value="/reimburse/new"),
        RecordedStep(op="fill", locator="label=金额", field="金额", doc="报销金额"),
        RecordedStep(op="select", locator="label=类别", field="费用类别"),
        RecordedStep(op="fill", locator="label=备注", field="remark", required=False),
        RecordedStep(op="submit", locator="role=button[name=提交]"),
    ]


def test_builds_bindings_and_required_split() -> None:
    body = build_page_script(_reimburse_steps(), action="submit_reimburse",
                             dom_fingerprint="fp-v1", title="提交报销",
                             success_marker="text=保存成功")
    # 中文标签/别名对齐到标准字段 key
    assert "amount" in body.required_fields        # 金额 → amount
    assert "category" in body.required_fields      # 费用类别 → category
    assert body.optional_fields == ["reason"]      # remark → reason(别名),且 required=False
    assert body.field_docs.get("amount") == "报销金额"
    # 字段绑定写进 value_from,输入步断言可见
    fill_amount = next(a for a in body.actions if a.locator == "label=金额")
    assert fill_amount.value_from == "field:amount" and fill_amount.assert_visible is True
    # 含提交步 → 写页面默认 L3
    assert body.risk_level == RiskLevel.L3
    assert body.success_marker == "text=保存成功"


def test_assign_field_keys_no_collision_when_std_collapses() -> None:
    """P1#6:多个字段塌缩到同一 std_key 时保唯一、不丢字段;无碰撞时与旧行为一致。"""
    # 无碰撞:标准对齐 + 原样保留
    assert assign_field_keys(["请假天数", "事由", "项目名称"]) == ["days", "reason", "项目名称"]
    # 碰撞:'开始时间' 与别名 'begin' 都→start_time;第二个退回原始标签保唯一
    assert assign_field_keys(["开始时间", "begin", "结束时间"]) == ["start_time", "begin", "end_time"]
    # 原始标签也重复 → 加 #n
    assert assign_field_keys(["项目名称", "项目名称"]) == ["项目名称", "项目名称#2"]


def test_build_page_script_two_fields_same_std_key_kept_distinct() -> None:
    """两个字段都对齐到同一标准 key,build_page_script 不再覆盖丢失,二者各自成参数。"""
    steps = [RecordedStep(op="fill", locator="label=开始时间", field="开始时间"),
             RecordedStep(op="fill", locator="label=生效起", field="begin"),   # 别名也→start_time
             RecordedStep(op="submit", locator="role=button[name=提交]")]
    body = build_page_script(steps, action="x", dom_fingerprint="fp")
    assert body.user_fields == ["start_time", "begin"]   # 两个字段都在,未互相覆盖
    a0 = next(a for a in body.actions if a.locator == "label=开始时间")
    a1 = next(a for a in body.actions if a.locator == "label=生效起")
    assert a0.value_from == "field:start_time" and a1.value_from == "field:begin"


def test_unknown_field_kept_and_no_submit_is_l1() -> None:
    steps = [RecordedStep(op="fill", locator="css=#kw", field="searchKeyword"),
             RecordedStep(op="click", locator="role=button[name=查询]")]
    body = build_page_script(steps, action="search_orders", dom_fingerprint="fp")
    assert body.required_fields == ["searchKeyword"]   # 非标准字段原样保留
    assert body.risk_level == RiskLevel.L1             # 无提交步 → 查询类 L1


def test_const_value_step() -> None:
    steps = [RecordedStep(op="select", locator="label=部门", value="技术部"),
             RecordedStep(op="submit", locator="role=button[name=保存]")]
    body = build_page_script(steps, action="x", dom_fingerprint="fp")
    sel = next(a for a in body.actions if a.locator == "label=部门")
    assert sel.value_from == "const:技术部"
    assert body.user_fields == []   # 常量步不暴露为参数


def test_manifest_carries_page_steps() -> None:
    """页面型 Skill 的 manifest 带上步骤/起始页/成功标志(供前端详情可视化)。"""
    from uuid import uuid4

    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import Subsystem
    spec = SkillSpec(
        skill_id="A-报销.submit_reimburse", subsystem=Subsystem.REIMBURSE,
        action="submit_reimburse", risk_level=RiskLevel.L3, has_api=False, page_asset_id=uuid4(),
        page_start_url="/r/new", page_success_marker="text=保存成功",
        page_steps=[{"op": "fill", "locator": "label=金额", "value_from": "field:amount"},
                    {"op": "submit", "locator": "role=button[name=提交]"}],
    )
    m = to_manifest(spec)
    assert m.integration == "page"
    assert m.page and m.page["start_url"] == "/r/new"
    assert m.page["success_marker"] == "text=保存成功"
    assert len(m.page["steps"]) == 2 and m.page["steps"][1]["op"] == "submit"


async def test_pick_step_is_param_and_runs() -> None:
    """选择型控件:pick 步成为 Skill 参数;运行期按参数值 pick(点开→按值选/输)。"""
    from uuid import uuid4

    from dano.execution.page import FakePageDriver, PageActionRuntime
    from dano.shared.enums import Outcome
    steps = [RecordedStep(op="pick", locator="label=请假类型", field="请假类型"),
             RecordedStep(op="submit", locator="role=button[name=提交]")]
    body = build_page_script(steps, action="submit_leave", dom_fingerprint="")
    pick = next(a for a in body.actions if a.op == "pick")
    assert pick.value_from == "field:请假类型"           # pick 绑定成参数(不是写死)
    assert "请假类型" in body.required_fields
    fake = FakePageDriver(fingerprint="fp")
    res = await PageActionRuntime(lambda: fake).run(
        uuid4(), body, {"请假类型": "事假"}, confirm=lambda f: True)
    assert res.outcome == Outcome.PASSED
    assert ("pick", "label=请假类型", "事假") in fake.ops   # 运行期按值 pick


async def test_built_script_is_executable_by_runtime() -> None:
    """builder 产物丢给 FakePageDriver 真跑一遍 → PASSED(证明建体可执行,非纸面)。"""
    body = build_page_script(_reimburse_steps(), action="submit_reimburse",
                             dom_fingerprint="fp-v1", start_url="/reimburse/new",
                             success_marker="text=保存成功")
    fake = FakePageDriver(fingerprint="fp-v1")
    rt = PageActionRuntime(lambda: fake)
    res = await rt.run(uuid4(), body, {"amount": "120", "category": "差旅", "reason": "出差"},
                       confirm=lambda f: True)
    assert res.outcome == Outcome.PASSED
    assert res.structured_output.get("submitted") is True
    assert ("fill", "label=金额", "120") in fake.ops
