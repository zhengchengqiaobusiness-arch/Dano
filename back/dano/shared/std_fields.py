"""标准字段词典(平台一次建设·全公司共用)。

pi coding Agent 做字段映射(流程2)时,把各家系统的真实字段对齐到这些平台标准字段。
M0 先给请假/工单/报销三类动作的核心字段,后续按需扩充。
"""

from __future__ import annotations

from pydantic import BaseModel


class StdField(BaseModel):
    key: str
    label: str
    aliases: list[str]  # 已知别名,供「别名命中」高置信匹配
    description: str


# 通用字段
COMMON_FIELDS: list[StdField] = [
    StdField(key="applicant", label="申请人", aliases=["apply_user", "user", "employee", "creator"],
             description="发起动作的员工"),
    StdField(key="start_time", label="开始时间", aliases=["begin", "from", "start_date", "startTime"],
             description="动作生效起始时间"),
    StdField(key="end_time", label="结束时间", aliases=["to", "end_date", "endTime", "finish"],
             description="动作生效结束时间"),
    StdField(key="reason", label="事由", aliases=["apply_reason", "remark", "note", "desc"],
             description="申请理由/备注"),
]

# 请假(OA)
LEAVE_FIELDS: list[StdField] = [
    StdField(key="leave_type", label="假期类型", aliases=["vacation_type", "leaveCategory", "type"],
             description="年假/病假/事假等"),
    StdField(key="days", label="请假天数", aliases=["duration", "leave_days", "amount_days"],
             description="申请天数,用于与余额比对"),
]

# 工单
TICKET_FIELDS: list[StdField] = [
    StdField(key="ticket_title", label="工单标题", aliases=["title", "subject", "summary"],
             description="工单主题"),
    StdField(key="priority", label="优先级", aliases=["urgency", "level"],
             description="工单优先级"),
]

# 报销
REIMBURSE_FIELDS: list[StdField] = [
    StdField(key="amount", label="金额", aliases=["money", "total", "fee", "cost"],
             description="报销金额"),
    StdField(key="category", label="费用类别", aliases=["expense_type", "item", "project"],
             description="差旅/餐饮/办公等"),
    StdField(key="has_invoice", label="是否有发票", aliases=["invoice", "with_receipt"],
             description="制度规则判定是否需发票"),
]

ALL_STD_FIELDS: list[StdField] = (
    COMMON_FIELDS + LEAVE_FIELDS + TICKET_FIELDS + REIMBURSE_FIELDS
)

STD_FIELD_INDEX: dict[str, StdField] = {f.key: f for f in ALL_STD_FIELDS}


# ── 字段语义助手(契约/导出共用,避免各处重复猜)──────────────────────────────

# 流程内部字段:由 Dano 运行期注入(流程模板/实例/任务句柄),**绝不**作为用户参数暴露。
FLOW_INTERNAL_FIELDS: frozenset[str] = frozenset({
    "templateid", "procinsid", "procdefid", "defid", "taskid", "bizid", "procdefkey",
})

# 数值字段判定:名字命中 或 描述含金额/数量/单价等量纲词 → JSON number(审批分支按数值比较)。
_NUMERIC_NAMES: frozenset[str] = frozenset({
    "amount", "quantity", "unitprice", "price", "total", "totalamount", "count", "qty",
    "num", "number", "days", "hours", "duration", "money", "fee", "cost", "budget",
    "sum", "subtotal",
})
_NUMERIC_KEYWORDS: tuple[str, ...] = (
    "金额", "数量", "单价", "总额", "总价", "价格", "费用", "预算", "天数", "小时", "时长", "(元)", "（元）",
)


def _norm(name: str) -> str:
    return (name or "").lower().replace("_", "")


def is_flow_internal(name: str) -> bool:
    """是否为流程内部/注入字段(不进对外契约)。"""
    return _norm(name) in FLOW_INTERNAL_FIELDS


def is_numeric_field(name: str, desc: str = "", *, declared_type: str | None = None) -> bool:
    """字段是否应为 JSON 数字。优先信源 schema 声明的 number/integer,退而按名字/描述判定。"""
    if declared_type in ("number", "integer"):
        return True
    return _norm(name) in _NUMERIC_NAMES or any(w in (desc or "") for w in _NUMERIC_KEYWORDS)
