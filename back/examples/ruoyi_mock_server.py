"""RuoYi-Flowable 风格的真实 mock 服务器(真 HTTP + Token + AjaxResult + 工作流)。

用途:展示智能抽离(阶段0-4)成果时当**真实接入目标**,不碰你的生产 RuoYi。
与 examples/onboarding/ruoyi_oa.yaml 的接口一一对应,返回 RuoYi 的 AjaxResult({code,...})。

启动:python -m examples.ruoyi_mock_server   (监听 http://localhost:9002)
鉴权:Authorization: Bearer ruoyi-mock-token-xyz(接入/调用时由 credentials.token 提供)
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="RuoYi Mock OA", version="1.0.0")

_TOKEN = "ruoyi-mock-token-xyz"
_state = {"seq": 0, "instances": {}}


def _auth(authorization: str | None) -> None:
    if authorization != f"Bearer {_TOKEN}":
        raise HTTPException(status_code=401, detail="missing/invalid token")


def _ok(**data) -> dict:
    return {"code": 200, "msg": "操作成功", **data}


# ── 基础设施(会被智能抽离过滤掉,不生成 Skill;此处仅保证存在)──
@app.get("/captchaImage")
async def captcha() -> dict:
    return {"code": 200, "uuid": "u-1", "img": "base64...", "captchaEnabled": True}


class LoginReq(BaseModel):
    username: str | None = None
    password: str | None = None
    code: str | None = None
    uuid: str | None = None


@app.post("/login")
async def login(req: LoginReq | None = None) -> dict:
    return {"code": 200, "token": _TOKEN}


@app.get("/getInfo")
async def get_info(authorization: str | None = Header(default=None)) -> dict:
    _auth(authorization)
    return {"code": 200, "user": {"userName": "superAdmin"}, "roles": ["admin"]}


# ── 查询类(生成查询 Skill)──
@app.get("/template/template/newStart")
async def new_start(authorization: str | None = Header(default=None)) -> dict:
    _auth(authorization)
    return _ok(data=[{"id": "leave_template", "name": "请假申请", "defKey": "demo_leave"}])


@app.get("/workflow/todo/list")
async def todo_list(authorization: str | None = Header(default=None)) -> dict:
    _auth(authorization)
    return {"code": 200, "total": 0, "rows": []}


@app.get("/workflow/done/list")
async def done_list(authorization: str | None = Header(default=None)) -> dict:
    _auth(authorization)
    return {"code": 200, "total": 0, "rows": []}


@app.get("/flowable/definition/list")
async def definition_list(authorization: str | None = Header(default=None)) -> dict:
    _auth(authorization)
    return {"code": 200, "total": 1, "rows": [{"defKey": "demo_leave", "name": "请假申请"}]}


# ── 业务动作(start_leave_flow / submit_flow_task 会被复合成 submit_leave)──
@app.post("/workflow/handle/startFlow")
async def start_flow(body: dict | None = None,
                     authorization: str | None = Header(default=None)) -> dict:
    """发起流程:返回发起人当前任务(taskId 等),供下一步 submit 使用。"""
    _auth(authorization)
    _state["seq"] += 1
    task_id = str(34 + _state["seq"])
    proc_ins = str(27 + _state["seq"])
    _state["instances"][task_id] = {
        "procInsId": proc_ins, "status": "发起申请", "saved": False, "applied": False}
    _state["instances"][proc_ins] = _state["instances"][task_id]   # 也按 procInsId 索引
    return _ok(data={
        "taskId": task_id, "taskName": "发起申请", "taskDefKey": "apply",
        "assignee": "superAdmin", "procInsId": proc_ins, "executionId": proc_ins,
        "deployId": "282", "procDefId": "demo_leave:2:284", "procDefName": "请假申请",
    })


@app.get("/biz/form/info")
async def form_info(templateId: str = "", businessId: str = "",
                    authorization: str | None = Header(default=None)) -> dict:
    """动态表单结构(真实系统返回 vform schema;此处给最小可用 fields)。"""
    _auth(authorization)
    conf = {"formData": {"fields": [
        {"__vModel__": "title", "__config__": {"label": "请假标题", "tag": "el-input"}},
        {"__vModel__": "leaveType", "__config__": {"label": "请假类型", "tag": "el-select"}},
        {"__vModel__": "leaveDays", "__config__": {"label": "请假天数", "tag": "el-input-number"}},
        {"__vModel__": "reason", "__config__": {"label": "请假事由", "tag": "el-input"}},
    ], "formRef": "elForm", "formModel": "formData"}}
    import json as _json
    return _ok(data={"formData": _json.dumps(conf, ensure_ascii=False)})


@app.post("/biz/form/save")
async def form_save(body: dict | None = None,
                    authorization: str | None = Header(default=None)) -> dict:
    """保存业务表单:标记该实例已存表单,返回 businessId(submit 推进的前提)。"""
    _auth(authorization)
    data = body or {}
    # 真实契约:taskId 在 formData 内层(driver);也兼容顶层(通用编排步骤直传)
    inner = data.get("formData") if isinstance(data.get("formData"), dict) else {}
    task_id = inner.get("taskId") or data.get("taskId")
    inst = _state["instances"].get(task_id)
    if inst is not None:
        inst["saved"] = True
    biz_id = f"BIZ-{task_id}"
    return _ok(data=biz_id)


@app.post("/biz/flow/submit")
async def submit(body: dict | None = None,
                 authorization: str | None = Header(default=None)) -> dict:
    """提交/审批:推进流程。

    忠于真实 RuoYi:**任何**调用都回 200『操作成功』;但只有「先存过表单(有 businessId)」
    的实例,申请节点才真的完成(applied=True)。空 body / 未存表单 = 空操作(applied 不变)。
    """
    _auth(authorization)
    data = body or {}
    flow_task = data.get("flowTask", {})
    task_id = flow_task.get("taskId")
    inst = _state["instances"].get(task_id)
    if inst is not None and inst.get("saved") and flow_task.get("businessId"):
        inst["status"] = "已提交,待部门经理审批"
        inst["applied"] = True
    return _ok(data={"taskId": task_id})


@app.get("/workflow/handle/flowXmlAndNode")
async def flow_xml_and_node(procInsId: str = "", deployId: str = "",
                            authorization: str | None = Header(default=None)) -> dict:
    """事实核查端点:返回节点状态。apply.completed 反映该实例是否真的提交了。"""
    _auth(authorization)
    inst = _state["instances"].get(procInsId)
    applied = bool(inst and inst.get("applied"))
    nodes = [{"key": "start", "completed": True}, {"key": "apply", "completed": applied}]
    if applied:
        nodes.append({"key": "dept_approve", "completed": False})
    return _ok(data={"nodeData": nodes})


@app.post("/flowable/definition/save")
async def save_definition(body: dict | None = None,
                          authorization: str | None = Header(default=None)) -> dict:
    _auth(authorization)
    return _ok()


if __name__ == "__main__":
    print("RuoYi mock OA on http://localhost:9002  (Bearer ruoyi-mock-token-xyz)")
    uvicorn.run(app, host="127.0.0.1", port=9002, log_level="warning")
