"""业务:出差申请(submit_travel)—— 与请假**区分开**的独立业务定义。

与请假同走 RuoYi 3 步契约(发起→存表单→提交),但**业务字段不同**:
出差是「目的地 + 起止日期 + 天数 + 交通 + 预算 + 事由」,且带**日期区间**(请假只有天数)。
模板默认 travel_template;真实 templateId / 表单字段以接入时从 OA 解析为准(本模块给业务形状与样例)。

注意:若该企业的出差表单含**行程明细(子表)/附件**,扁平 valData 合并可能覆盖不全——
那种会在事实核查处暴露并转 LLM 拆解(profiler 的 file_upload/复杂表单分支),不在本确定性配方内强行处理。
"""

from __future__ import annotations

from dano.capabilities.business.base import RUOYI_SUCCESS_RULE
from dano.shared.asset_bodies import WorkflowSkillBody
from dano.shared.enums import RiskLevel

TEMPLATE_ID = "travel_template"       # 默认/样例;真实以 OA 解析为准
SAMPLE = {"templateId": TEMPLATE_ID,
          "values": {"title": "测试出差", "destination": "上海", "startDate": "2026-07-01",
                     "endDate": "2026-07-03", "days": 3, "transport": "高铁",
                     "budget": 2000, "reason": "测试出差"}}


def recipe() -> WorkflowSkillBody:
    return WorkflowSkillBody(
        action="submit_travel",
        title="提交出差申请",
        user_fields=["title", "destination", "startDate", "endDate", "days", "transport", "budget", "reason"],
        field_docs={
            "title": "申请标题,如「张三赴上海出差」",
            "destination": "出差目的地(城市/单位)",
            "startDate": "出发日期(YYYY-MM-DD)",
            "endDate": "返回日期(YYYY-MM-DD)",
            "days": "出差天数",
            "transport": "交通方式(高铁/飞机/汽车等)",
            "budget": "预算金额(元)",
            "reason": "出差事由",
        },
        required_fields=["title", "destination", "startDate", "endDate", "days", "reason"],
        risk_level=RiskLevel.L3,
        success_rule=RUOYI_SUCCESS_RULE,
        steps=[],   # **不预设步骤**:真实调用代码由 LLM 据证据 + 真报错从零生成(保证泛化)
    )
