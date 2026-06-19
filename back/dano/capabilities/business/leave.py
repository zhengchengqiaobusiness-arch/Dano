"""业务:请假(submit_leave)—— **仅业务元数据**(标题/字段/默认模板/样例)。

注意:**没有手写适配器源码**。请假和所有业务一样,代码 100% 由 LLM 从证据 + 真报错迭代生成
(见 generation/planner.py + coder.py)。本文件只提供"这是什么业务、典型字段"的元数据,
供发现菜单/字段提示用;真实 templateId 与表单字段在接入时按各企业 OA 解析,保证泛化。
"""

from __future__ import annotations

from dano.capabilities.business.base import RUOYI_SUCCESS_RULE
from dano.shared.asset_bodies import WorkflowSkillBody
from dano.shared.enums import RiskLevel

TEMPLATE_ID = "leave_template"        # 默认/样例;真实以 OA 解析为准
SAMPLE = {"templateId": TEMPLATE_ID,
          "values": {"title": "测试请假", "leaveType": "annual", "startDate": "2026-07-01",
                     "endDate": "2026-07-02", "leaveDays": 1, "reason": "测试"}}


def recipe() -> WorkflowSkillBody:
    """请假业务画像(字段/标题);仅供发现菜单与字段提示,**不含可执行代码**。"""
    return WorkflowSkillBody(
        action="submit_leave",
        title="提交请假申请",
        user_fields=["title", "leaveType", "startDate", "endDate", "leaveDays", "reason"],
        field_docs={
            "title": "申请标题,如「张三的年假申请」",
            "leaveType": "请假类型(annual年假/personal事假/sick病假/comp调休)",
            "startDate": "开始日期(YYYY-MM-DD)",
            "endDate": "结束日期(YYYY-MM-DD)",
            "leaveDays": "请假天数",
            "reason": "请假事由",
        },
        required_fields=["title", "leaveType", "leaveDays", "reason"],
        risk_level=RiskLevel.L3,
        success_rule=RUOYI_SUCCESS_RULE,
        steps=[],   # **不预设步骤**:真实调用代码由 LLM 据证据 + 真报错从零生成(保证泛化)
    )
