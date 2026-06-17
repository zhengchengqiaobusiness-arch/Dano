"""Phase 13 验收:RuoYi-Flowable 请假「真实契约 + 事实核查(流程9)」。

确定性(纯单测,无网络/无 PG):用 FakeOA 模拟这套 OA 的真实行为——
- 任何 /biz/flow/submit 都回『操作成功』(RuoYi 特性:不可信的成功);
- 只有「先 form/save 拿到 businessId、再 submit」的实例,申请节点才真的完成;
- 直接 submit(不存表单)= 空操作,节点不前进。

据此验证驱动:
1) 契约正确:save 体是双层 {formData(结构), valData(值)};submit 带 operateType=200 + businessId。
2) 事实核查决定成败:真实提交 → apply.completed=True / real=True;
   空操作 → 接口仍『操作成功』但 apply.completed=False / real=False(系统应判失败)。
"""

from __future__ import annotations

import json

import pytest

from dano.capabilities.ruoyi_leave import OPERATE_SUBMIT, RuoYiLeaveDriver

_FORM_CONF = {
    "fields": [
        {"__vModel__": "title", "__config__": {"label": "请假标题", "tag": "el-input"}},
        {"__vModel__": "leaveType", "__config__": {"label": "请假类型", "tag": "el-select"}},
        {"__vModel__": "leaveDays", "__config__": {"label": "请假天数", "tag": "el-input-number"}},
        {"__vModel__": "reason", "__config__": {"label": "请假事由", "tag": "el-input"}},
    ],
    "formRef": "elForm", "formModel": "formData",
}


class FakeOA:
    """模拟 RuoYi-Flowable 的关键行为:submit 永远回成功;只有存过表单的实例才真的前进。"""

    def __init__(self, *, honor_save: bool = True) -> None:
        self.honor_save = honor_save          # False=模拟「直接 submit 不存表单」的空操作链路
        self._seq = 3700
        self.saved: set[str] = set()          # 已存表单(有 businessId)的 procInsId
        self.advanced: set[str] = set()        # 申请节点已完成的 procInsId
        self.calls: list[str] = []

    async def __call__(self, method, path, body=None):
        self.calls.append(f"{method} {path.split('?')[0]}")
        if path == "/workflow/handle/startFlow":
            self._seq += 10
            return 200, {"code": 200, "msg": "操作成功", "data": {
                "taskId": str(self._seq + 5), "procInsId": str(self._seq),
                "executionId": str(self._seq - 1), "deployId": "282",
                "procDefId": "demo_leave:2:284"}}
        if path.startswith("/biz/form/info"):
            return 200, {"code": 200, "data": {"formData": json.dumps({"formData": _FORM_CONF})}}
        if path == "/biz/form/save":
            pins = body["formData"]["procInstId"]
            if self.honor_save:
                self.saved.add(pins)
                return 200, {"code": 200, "msg": "操作成功", "data": f"BIZ-{pins}"}
            return 500, {"code": 500, "msg": "业务表单数据转换失败:null"}
        if path == "/biz/flow/submit":
            pins = body["flowTask"]["procInsId"]
            # RuoYi 特性:永远回成功;但只有存过表单的实例才真的推进申请节点
            if pins in self.saved and body["flowTask"].get("businessId"):
                self.advanced.add(pins)
            return 200, {"code": 200, "msg": "操作成功"}
        if path.startswith("/workflow/handle/flowXmlAndNode"):
            pins = path.split("procInsId=")[1].split("&")[0]
            done = pins in self.advanced
            nodes = [{"key": "start", "completed": True}, {"key": "apply", "completed": done}]
            if done:
                nodes.append({"key": "dept_approve", "completed": False})
            return 200, {"code": 200, "data": {"nodeData": nodes}}
        raise AssertionError(f"未预期的调用: {method} {path}")


_VALUES = {"title": "张三的年假", "leaveType": "annual", "leaveDays": 1, "reason": "回家"}


async def test_real_submit_passes_factcheck():
    oa = FakeOA(honor_save=True)
    res = await RuoYiLeaveDriver(oa).create_leave(_VALUES)
    # 事实核查通过 = 真实提交成功
    assert res.apply_completed is True
    assert res.real is True
    assert res.business_id == f"BIZ-{res.proc_ins_id}"
    # 即便 submit 接口回的是『操作成功』,也走了完整链路
    assert "POST /biz/form/save" in oa.calls
    assert "POST /biz/flow/submit" in oa.calls
    assert "GET /workflow/handle/flowXmlAndNode" in oa.calls


async def test_save_payload_is_double_nested_with_valdata():
    payload = RuoYiLeaveDriver.build_save_payload(
        template_id="leave_template", task_id="1", proc_ins_id="2",
        conf=_FORM_CONF, values=_VALUES, title="张三的年假")
    inner = json.loads(payload["formData"]["formData"])
    assert set(inner.keys()) == {"formData", "valData"}      # 双层:结构 + 值
    assert inner["valData"] == _VALUES                        # 值放 valData(缺它会 500)
    assert inner["formData"] == _FORM_CONF


async def test_submit_payload_uses_operatetype_200_and_businessid():
    p = RuoYiLeaveDriver.build_submit_payload(
        task_id="1", proc_ins_id="2", execution_id="3", deploy_id="282",
        proc_def_id="demo_leave:2:284", business_id="BIZ-2",
        template_id="leave_template", title="x")
    assert p["operateType"] == OPERATE_SUBMIT == "200"
    assert p["flowTask"]["businessId"] == "BIZ-2"
    assert p["flowTask"]["taskDefKey"] == "apply"


async def test_noop_submit_fails_factcheck_even_though_api_says_ok():
    """空操作链路:submit 仍回『操作成功』,但申请节点没前进 → real=False(系统判失败)。"""
    oa = FakeOA(honor_save=True)
    # 模拟「直接 submit 不带 businessId」(老流程的 bug 形态)
    sf = await oa("POST", "/workflow/handle/startFlow", {"templateId": "leave_template"})
    pins = sf[1]["data"]["procInsId"]
    ack = await oa("POST", "/biz/flow/submit", {"flowTask": {"procInsId": pins, "businessId": None}})
    assert ack[1]["msg"] == "操作成功"                         # 接口骗你说成功了
    completed, _ = await RuoYiLeaveDriver(oa).fact_check(pins, "282", retries=2, backoff_s=0.0)
    assert completed is False                                  # 事实核查戳穿:没真的提交
