"""RuoYi-Flowable 请假流程的「已验证真实契约」+ 事实核查(流程9)。

为什么单独成模块:这套 OA 的请假提交契约**不在 swagger 里**(swagger 只声明
CommonFlowSubmit 形状,不告诉你 operateType 取值、表单要先存、businessId 怎么来)。
契约是逆向真实前端 + 对真实系统回查后**实测确认**的,固化在此,且每一步都用
事实核查闭环验证(不靠接口返回的「操作成功」——RuoYi 对任何输入都回 200)。

真实契约(实测,2026-06-17 superAdmin / 新 swagger /tool/swagger):
  1) POST /workflow/handle/startFlow {templateId}
       → data: {taskId, procInsId, executionId, deployId, procDefId}  (停在 apply 节点)
  2) GET  /biz/form/info?businessId=&templateId=...  → 动态表单 schema(conf)
  3) POST /biz/form/save  →  返回 businessId
       body: {bizId, templateId, formData:{id,title,todoId,taskId,templateId,procInstId,
              formData: JSON.stringify({formData: conf, valData: 表单值})}}
       ※ 关键:formData 内层是 {formData(=表单结构), valData(=用户填的值)},缺 valData 会
         报「业务表单数据转换失败:null」;直接 submit 而不先 save 会「操作成功」但什么都不做。
  4) POST /biz/flow/submit {operateType:"200", flowTask:{...,businessId}}
       operateType 200=提交 / 201=驳回 / 202=退回(逆向前端 chunk 确认)。

事实核查(决定性,逐实例):
  GET /workflow/handle/flowXmlAndNode?procInsId=..&deployId=..
    → nodeData 中 apply.completed:
        True  = 申请真的提交了(已流转到 dept_approve)→ 通过
        False = 仍卡在 apply(submit 是空操作)→ 判失败,拒绝发布
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# operateType 取值(逆向真实前端 commonSubmit 调用点确认)
OPERATE_SUBMIT = "200"   # 提交/同意
OPERATE_REJECT = "201"   # 驳回
OPERATE_RETURN = "202"   # 退回


class Caller:
    """最小 HTTP 调用协议:接收 (method, path, json_body|None) 返回 (http, body)。

    用真实 token 注入鉴权头由调用方负责;这里只关心契约。便于测试时替换为 Fake。
    """

    async def __call__(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:  # pragma: no cover - 协议声明
        raise NotImplementedError


@dataclass
class LeaveResult:
    """一次请假提交的产物 + 事实核查结论。"""

    proc_ins_id: str
    task_id: str
    business_id: str
    deploy_id: str
    proc_def_id: str
    submit_ack: dict[str, Any]          # /biz/flow/submit 的原始返回(仅作记录)
    apply_completed: bool               # 事实核查:申请节点是否真的完成
    node_data: list[dict[str, Any]]     # 事实核查证据:flowXmlAndNode 节点状态
    fact_checked: bool = True

    @property
    def real(self) -> bool:
        """是否「真实提交成功」——以事实核查为准,不看接口的『操作成功』。"""
        return self.apply_completed


def _ok(body: dict[str, Any]) -> bool:
    """RuoYi 统一成败:有 code 必须 200,没 code 靠 HTTP(由调用方判)。"""
    code = body.get("code")
    return code is None or code == 200


class RuoYiLeaveDriver:
    """请假流程的已验证驱动:发现契约 → 创建请假 → 事实核查。"""

    def __init__(self, call: Caller, *, template_id: str = "leave_template",
                 deploy_id_hint: str | None = None) -> None:
        self._call = call
        self.template_id = template_id
        self._deploy_hint = deploy_id_hint

    async def fetch_form_conf(self) -> dict[str, Any]:
        """步骤2:取动态表单 schema(conf)。返回内层 {fields, formRef, formModel, ...}。"""
        http, body = await self._call(
            "GET", f"/biz/form/info?businessId=&templateId={self.template_id}", None
        )
        if not _ok(body):
            raise RuntimeError(f"取表单结构失败: {body.get('msg')}")
        return json.loads(body["data"]["formData"])["formData"]

    @staticmethod
    def build_save_payload(*, template_id: str, task_id: str, proc_ins_id: str,
                           conf: dict[str, Any], values: dict[str, Any],
                           title: str) -> dict[str, Any]:
        """步骤3 的 /biz/form/save 请求体(契约最易错处:formData 双层 + valData)。"""
        blob = {"formData": conf, "valData": values}     # 前端 parser 提交的 params 形状
        inner = {
            "id": None, "title": title, "todoId": None, "taskId": task_id,
            "templateId": template_id, "procInstId": proc_ins_id,
            "formData": json.dumps(blob, ensure_ascii=False),
        }
        return {"bizId": None, "templateId": template_id, "formData": inner}

    @staticmethod
    def build_submit_payload(*, task_id: str, proc_ins_id: str, execution_id: str,
                             deploy_id: str, proc_def_id: str, business_id: str,
                             template_id: str, title: str,
                             operate_type: str = OPERATE_SUBMIT) -> dict[str, Any]:
        """步骤4 的 /biz/flow/submit 请求体。"""
        return {
            "operateType": operate_type,
            "flowTask": {
                "taskId": task_id, "procInsId": proc_ins_id, "executionId": execution_id,
                "deployId": deploy_id, "defId": proc_def_id, "procDefId": proc_def_id,
                "taskDefKey": "apply", "businessId": business_id, "title": title,
                "comment": "", "isDraft": False, "todoId": None,
                "templateId": template_id, "variables": {},
            },
        }

    async def fact_check(self, proc_ins_id: str, deploy_id: str, *,
                         retries: int = 5, backoff_s: float = 0.8) -> tuple[bool, list[dict]]:
        """流程9 事实核查:回查申请节点是否真的完成(决定性,不信『操作成功』)。

        submit 是异步的——flowable 事务提交后申请节点才标记完成,故轮询若干次再判失败,
        避免「提交其实成功了,只是核查太早」的假阴性。
        """
        nodes: list[dict[str, Any]] = []
        completed = False
        for attempt in range(retries):
            http, body = await self._call(
                "GET",
                f"/workflow/handle/flowXmlAndNode?procInsId={proc_ins_id}&deployId={deploy_id}",
                None,
            )
            nodes = ((body.get("data") or {}).get("nodeData")) or []
            apply_node = next((n for n in nodes if n.get("key") == "apply"), None)
            completed = bool(apply_node and apply_node.get("completed"))
            if completed:
                break
            if attempt < retries - 1:
                await asyncio.sleep(backoff_s)
        log.info("ruoyi_leave.fact_check", proc_ins_id=proc_ins_id, attempts=attempt + 1,
                 apply_completed=completed, nodes={n.get("key"): n.get("completed") for n in nodes})
        return completed, nodes

    async def create_leave(self, values: dict[str, Any]) -> LeaveResult:
        """端到端真实创建一条请假并事实核查。values 至少含 title/leaveType/leaveDays/reason。

        关键:不论 submit 返回什么,最后都以 fact_check 的 apply.completed 为准。
        """
        title = str(values.get("title") or "请假申请")

        # 1) startFlow
        http, sf = await self._call(
            "POST", "/workflow/handle/startFlow", {"templateId": self.template_id}
        )
        if not _ok(sf):
            raise RuntimeError(f"startFlow 失败: {sf.get('msg')}")
        d = sf["data"]
        task_id, proc_ins, exec_id = d["taskId"], d["procInsId"], d["executionId"]
        deploy_id, proc_def = str(d.get("deployId") or self._deploy_hint or ""), d["procDefId"]

        # 2) 取表单结构 + 3) 存表单 → businessId
        conf = await self.fetch_form_conf()
        save_payload = self.build_save_payload(
            template_id=self.template_id, task_id=task_id, proc_ins_id=proc_ins,
            conf=conf, values=values, title=title,
        )
        http, sv = await self._call("POST", "/biz/form/save", save_payload)
        business_id = sv.get("data")
        if not (_ok(sv) and business_id):
            raise RuntimeError(f"存表单失败(契约不符): {sv.get('msg')}")

        # 4) submit
        submit_payload = self.build_submit_payload(
            task_id=task_id, proc_ins_id=proc_ins, execution_id=exec_id,
            deploy_id=deploy_id, proc_def_id=proc_def, business_id=business_id,
            template_id=self.template_id, title=title,
        )
        http, ack = await self._call("POST", "/biz/flow/submit", submit_payload)

        # 流程9:事实核查(决定性)
        completed, nodes = await self.fact_check(proc_ins, deploy_id)

        return LeaveResult(
            proc_ins_id=proc_ins, task_id=task_id, business_id=business_id,
            deploy_id=deploy_id, proc_def_id=proc_def, submit_ack=ack,
            apply_completed=completed, node_data=nodes,
        )
