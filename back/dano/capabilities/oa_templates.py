"""OA 框架模板库(阶段1):识别常见 OA/工作流系统,套用其鉴权/基础设施/成败约定。

定位:接入一份 Swagger 时,先认出"这是哪种 OA 框架"(如 RuoYi-Flowable),自动:
- 标出该框架的基础设施接口(登录/验证码/路由等),不暴露成业务 Skill;
- 注入该框架的成败判定规则(如 RuoYi 用 HTTP200 + body.code==200,而非通用单号)。

不臆造:只用 spec 里能客观判定的特征(路径前缀 / schema 名 / tags)匹配模板。
扩展:实现 OATemplate 子类 + register_oa_template() 即可接新框架(钉钉/泛微/企微…)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import structlog

from dano.shared.asset_bodies import WorkflowSkillBody, WorkflowStep
from dano.shared.enums import RiskLevel

log = structlog.get_logger(__name__)


class OATemplate(ABC):
    """一种 OA 框架的识别模板。"""

    name: str = "generic"

    @abstractmethod
    def matches(self, spec: dict[str, Any]) -> bool:
        """该 spec 是否属于本框架(只用客观特征判定)。"""

    def success_rule(self) -> str | None:
        """运行期判定调用成败的断言表达式;None=只用 HTTP 2xx。"""
        return None

    def infrastructure_patterns(self) -> tuple[str, ...]:
        """本框架额外的基础设施关键词(动作名/路径命中 → 不暴露成业务 Skill)。"""
        return ()

    def workflows(self) -> list[WorkflowSkillBody]:
        """本框架的复合流程 Skill 配方(多步连接器编排成一个业务能力,阶段2)。

        只声明配方;接入时若配方引用的连接器动作未全部发布,则跳过该复合 Skill。
        """
        return []


class RuoYiFlowableTemplate(OATemplate):
    """RuoYi-Vue + Flowable 工作流(请假/审批等 BPMN 流程)。

    特征:路径含 /workflow/ 或 /flowable/,或 components.schemas 有 AjaxResult。
    成败:RuoYi 统一返回 HTTP 200 + body.code(200成功/500失败);列表类无 code 字段,
          故规则写成"有 code 就必须 200,没有 code 则靠 HTTP 2xx"。
    """

    name = "ruoyi-flowable"

    def matches(self, spec: dict[str, Any]) -> bool:
        paths = " ".join(spec.get("paths", {}) or {}).lower()
        schemas = (spec.get("components", {}) or {}).get("schemas", {}) or {}
        return "/workflow/" in paths or "/flowable/" in paths or "AjaxResult" in schemas

    def success_rule(self) -> str | None:
        return "response.code == null or response.code == 200"

    def infrastructure_patterns(self) -> tuple[str, ...]:
        return ("captcha", "getinfo", "getrouters", "logout")

    def workflows(self) -> list[WorkflowSkillBody]:
        """请假复合流程(已验证真实契约,见 dano.capabilities.ruoyi_leave)。

        真实契约是逆向前端 + 对真实系统实测确认的(swagger 只声明形状、不给取值),共 3 步:
          1) start_leave_flow  POST /workflow/handle/startFlow {templateId}
                → taskId/procInsId/executionId/deployId/procDefId(停在 apply 节点)
          2) save_leave_form   POST /biz/form/save(取 /biz/form/info 的动态表单结构,
                填值后以双层 {formData:结构, valData:值} 存档)→ businessId
                ※ 不能省:直接 submit 而不先 save,接口会回『操作成功』但什么都不做(空操作)。
          3) submit_flow_task  POST /biz/flow/submit {operateType:"200", flowTask:{..businessId}}

        成败不看接口的『操作成功』(RuoYi 对任何输入都回 200),而以**事实核查(流程9)**为准:
          GET /workflow/handle/flowXmlAndNode → apply.completed=True 才算真的提交。
        因表单存档需动态结构 + 双层编码,执行/核查由 RuoYiLeaveDriver 承担(非通用扁平绑定)。
        """
        return [
            WorkflowSkillBody(
                action="submit_leave",
                title="提交请假申请",
                user_fields=["title", "leaveType", "leaveDays", "reason"],
                field_docs={
                    "title": "申请标题,如「张三的年假申请」",
                    "leaveType": "请假类型(annual年假/personal事假/sick病假/comp调休)",
                    "leaveDays": "请假天数",
                    "reason": "请假事由",
                },
                required_fields=["title", "leaveType", "leaveDays", "reason"],
                risk_level=RiskLevel.L3,
                success_rule=self.success_rule(),
                steps=[
                    WorkflowStep(
                        action="start_leave_flow",
                        inputs={"templateId": "const:leave_template"},
                    ),
                    WorkflowStep(
                        action="save_leave_form",
                        inputs={
                            "templateId": "const:leave_template",
                            "taskId": "step:start_leave_flow.data.taskId",
                            "procInstId": "step:start_leave_flow.data.procInsId",
                            "title": "field:title",
                            "valData.title": "field:title",
                            "valData.leaveType": "field:leaveType",
                            "valData.leaveDays": "field:leaveDays",
                            "valData.reason": "field:reason",
                        },
                    ),
                    WorkflowStep(
                        action="submit_flow_task",
                        inputs={
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
                        },
                    ),
                ],
            )
        ]


# 注册表(靠前优先)。新增框架插到最前。
_TEMPLATES: list[OATemplate] = [RuoYiFlowableTemplate()]


def register_oa_template(template: OATemplate) -> None:
    _TEMPLATES.insert(0, template)


def match_template(spec: dict[str, Any]) -> OATemplate | None:
    """匹配 spec 所属的 OA 框架模板;无匹配返回 None(走通用规则)。"""
    if not isinstance(spec, dict):
        return None
    for t in _TEMPLATES:
        try:
            if t.matches(spec):
                log.info("oa_template.matched", template=t.name)
                return t
        except Exception:  # noqa: BLE001 - 模板匹配不应让接入崩
            continue
    return None
