"""Generic standard fields plus optional tenant-pack extensions."""

from __future__ import annotations

from pydantic import BaseModel

from dano.business_packs import standard_fields_for as packed_standard_fields


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

ALL_STD_FIELDS: list[StdField] = list(COMMON_FIELDS)

STD_FIELD_INDEX: dict[str, StdField] = {f.key: f for f in ALL_STD_FIELDS}


def standard_fields_for(tenant: str) -> list[StdField]:
    """Combine universal fields with the selected tenant's optional fields."""
    configured = [StdField.model_validate(item) for item in packed_standard_fields(tenant)]
    merged = {field.key: field for field in [*COMMON_FIELDS, *configured]}
    return list(merged.values())


# ── 字段语义助手(契约/导出共用,避免各处重复猜)──────────────────────────────

# 流程内部字段:由 Dano 运行期注入(流程模板/实例/任务句柄),**绝不**作为用户参数暴露。
FLOW_INTERNAL_FIELDS: frozenset[str] = frozenset({
    "templateid", "procinsid", "procdefid", "defid", "taskid", "bizid", "procdefkey",
})

# 整表序列化信封字段:把整张表单打包成一个串/对象的容器。
# 它**不是**业务字段,而是一堆业务字段的序列化容器——绝不能作用户参数暴露(调用方对着黑盒无从填),
# 应拆成提交 schema 的业务叶子(由 Dano 运行期组装回去)。
FORM_ENVELOPE_FIELDS: frozenset[str] = frozenset({
    "formdata", "formjson", "formmodel", "formcontent", "formfields", "formbody",
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


def is_form_envelope(name: str) -> bool:
    """是否为整表序列化信封字段(如 formData):不进对外契约,应拆成业务叶子。"""
    return _norm(name) in FORM_ENVELOPE_FIELDS


def is_numeric_field(name: str, desc: str = "", *, declared_type: str | None = None) -> bool:
    """字段是否应为 JSON 数字。**信源声明的类型最权威,两个方向都认**:
    声明 number/integer→是;声明 string/boolean/array/object→否(关键词启发式**不得越权**改写显式声明,
    否则「预算标题」这类文本字段会因描述含「预算」被误判为数字)。无声明时才退而按名字/描述启发判定。"""
    if declared_type in ("number", "integer"):
        return True
    if declared_type in ("string", "boolean", "array", "object"):
        return False
    return _norm(name) in _NUMERIC_NAMES or any(w in (desc or "") for w in _NUMERIC_KEYWORDS)
