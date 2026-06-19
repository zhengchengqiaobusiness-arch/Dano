"""工作流类策略(若依-Flowable 等 BPMN 审批流):请假/报销/采购等"发起→存表单→提交"。

把本次逆向+实测确认的请假契约沉淀为领域知识,作为 pi 编码的拆解结论与骨架:
  startFlow(templateId) → biz/form/info 取动态表单 → biz/form/save(双层 {formData,valData})得 businessId
  → biz/flow/submit(operateType=200, flowTask{...businessId})
成败不看接口"操作成功",以**事实核查**为准:回查该实例是否真的流转过申请节点。
(具体回查端点/参数在 M5 真实系统跑通时校准——策略给出提案,测试+事实核查闭环确认或驱动纠正。)
"""

from __future__ import annotations

from dano.generation.artifacts import GoalBrief
from dano.shared.asset_bodies import FactCheckSpec, PlanBody

# 运行期注入的内部字段(非用户业务字段),不计入 user_fields/required_fields
_RESERVED = {"__base_url__", "__templateId__"}


def _user_fields(test_input: dict) -> list[str]:
    return [k for k in test_input if k not in _RESERVED]


def _consts(test_input: dict) -> dict:
    """从测试输入里抽运行期常量(如 __templateId__),发布后由 invoke 注入(同 __base_url__)。"""
    return {k: test_input[k] for k in ("__templateId__",) if k in test_input}


def _field_docs(goal: GoalBrief, fields: list[str]) -> dict[str, str]:
    """用证据里的表单字段标签补字段描述(供前端/导出);无证据则空,manifest 回退字段名。"""
    ev = goal.evidence or {}
    labels = {f.get("key"): f.get("label") for f in (ev.get("form_fields") or [])}
    return {k: labels[k] for k in fields if labels.get(k) and labels[k] != k}


class WorkflowBpmnStrategy:
    name = "workflow_bpmn"

    def matches(self, actions: list[dict]) -> bool:
        blob = " ".join((a.get("endpoint", "") + " " + a.get("name", "")) for a in actions).lower()
        return ("/biz/flow/submit" in blob or "startflow" in blob.replace("/", "")
                or ("start" in blob and "submit" in blob) or "flowtask" in blob)

    def decompose(self, goal: GoalBrief) -> PlanBody:
        return PlanBody(
            flow=goal.flow, strategy=self.name,
            steps=[
                "startFlow(templateId) → taskId/procInsId/executionId/deployId/procDefId(停在 apply)",
                "biz/form/info 取动态表单结构 → 填值后 biz/form/save(双层 {formData:结构, valData:值})→ businessId",
                "biz/flow/submit(operateType=200, flowTask{...,businessId})",
            ],
            contract={
                "start_endpoint": "/workflow/handle/startFlow",
                "form_info_endpoint": "/biz/form/info",
                "form_save_endpoint": "/biz/form/save",
                "submit_endpoint": "/biz/flow/submit",
                "operate_type_submit": "200",
                "form_save_note": "formData 内层须为 JSON 串 {formData:表单结构, valData:用户填的值};缺 valData 会转换失败",
            },
            user_fields=_user_fields(goal.test_input),
            required_fields=_user_fields(goal.test_input),
            field_docs=_field_docs(goal, _user_fields(goal.test_input)),
            consts=_consts(goal.test_input),
            success_rule="response.code == null or response.code == 200",
            fact_check=FactCheckSpec(
                endpoint="/flowable/monitor/listProcess?procInstId={procInsId}&pageNum=1&pageSize=5",
                method="GET", assert_expr="response.total > 0", retries=5, backoff_s=0.8),
        )

    def code_skeleton(self, plan: PlanBody) -> str:
        # 纯文本骨架(给 pi 的领域知识),端点写实;真正契约细节以 contract 为准。
        return (
            "import httpx, json\n"
            "def run(inputs, creds):\n"
            "    base = inputs['__base_url__'].rstrip('/')\n"
            "    h = {'Authorization': 'Bearer ' + creds['token']}   # 凭证仅来自 creds,不得入码\n"
            "    tid = inputs['__templateId__']   # 模板由发布时常量注入,非用户字段\n"
            "    values = {k: v for k, v in inputs.items() if not (k.startswith('__') and k.endswith('__'))}\n"
            "    with httpx.Client(timeout=30, verify=False) as c:\n"
            "        # 1) 发起 → 拿 taskId/procInsId/executionId/deployId/procDefId\n"
            "        d = c.post(base + '/workflow/handle/startFlow', json={'templateId': tid}, headers=h).json()['data']\n"
            "        # 2) 取动态表单结构 → 双层 {formData:结构, valData:值} 存表单 → businessId\n"
            "        conf = json.loads(c.get(base + '/biz/form/info', params={'businessId': '', 'templateId': tid},\n"
            "                                headers=h).json()['data']['formData'])['formData']\n"
            "        inner = {'id': None, 'title': values.get('title'), 'todoId': None, 'taskId': d['taskId'],\n"
            "                 'templateId': tid, 'procInstId': d['procInsId'],\n"
            "                 'formData': json.dumps({'formData': conf, 'valData': values}, ensure_ascii=False)}\n"
            "        biz = c.post(base + '/biz/form/save', json={'bizId': None, 'templateId': tid, 'formData': inner},\n"
            "                     headers=h).json()['data']\n"
            "        # 3) 提交(operateType=200)\n"
            "        flow_task = {'taskId': d['taskId'], 'procInsId': d['procInsId'], 'executionId': d['executionId'],\n"
            "                     'deployId': d['deployId'], 'defId': d['procDefId'], 'procDefId': d['procDefId'],\n"
            "                     'taskDefKey': 'apply', 'businessId': biz, 'templateId': tid,\n"
            "                     'title': values.get('title'), 'variables': {}}\n"
            "        ack = c.post(base + '/biz/flow/submit',\n"
            "                     json={'operateType': '200', 'flowTask': flow_task}, headers=h).json()\n"
            "    return {'code': ack.get('code'), 'procInsId': d['procInsId'], 'deployId': d['deployId']}\n"
        )
