"""从解析出的动作确定性地构造连接器规格(声明式资产体)。

定位:pi 负责"编排/决策"(选哪些动作、验证、失败重试),Python 负责"把声明式资产体建对"。
端口自 backend 的连接器生成器:鉴权库中选、字段绑定标准化、风险分级、断言集(含模板成败规则)。
"""

from __future__ import annotations

from dano.capabilities import auth_adapters
from dano.capabilities.doc_parser import ActionSpec
from dano.shared.asset_bodies import (
    Assertion,
    Assertions,
    ConnectorBody,
    FailureHandling,
    FieldBinding,
)
from dano.shared.enums import RiskLevel
from dano.shared.std_fields import ALL_STD_FIELDS, is_flow_internal

# 写方法 → 运行期需确认(L3);GET 只读 → L1。风险按 HTTP 方法判,不靠动作名关键词
# (避免 start_leave_flow 这类写操作因名字不含关键词被误判成 L1)。
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_HTTP_2XX = Assertion(name="http_2xx", expr="http >= 200 and http < 300")


def _risk_for(method: str) -> RiskLevel:
    return RiskLevel.L3 if method.upper() in _WRITE_METHODS else RiskLevel.L1


def _bind_field(param: str, *, required: bool = True) -> FieldBinding | None:
    """把接口入参绑定到平台标准字段。

    标准词典是**语义增强**(跨系统对齐别名/类型/描述),不是**准入门槛**:
    - 命中标准字段 → 映射到平台标准 key(多系统同义字段对齐);
    - 命不中 → 用字段**自身名**做 identity 绑定(param == platform_std),如实保留。
    绝不丢弃业务字段——否则该字段既不进对外契约,运行期 harness 也不会把它发出去
    (任何非请假/工单/报销词典内的业务必现字段丢失)。
    流程内部句柄(templateId/taskId 等由运行期注入)不绑定、不暴露成用户参数。
    """
    if is_flow_internal(param):
        return None
    pl = param.lower()
    for std in ALL_STD_FIELDS:
        if pl == std.key.lower() or pl in {a.lower() for a in std.aliases}:
            return FieldBinding(param=param, platform_std=std.key, required=required)
    return FieldBinding(param=param, platform_std=param, required=required)


def _build_assertions(*, risk: RiskLevel, success_rule: str | None) -> Assertions:
    """连接器断言。成败判据**只来自三个数据源**,代码里不写死任何业务/系统字面量:

    ① success_rule —— 系统约定(dialect.success_rule() 或 LLM 探得的成功约定,如 RuoYi code==200);
    ② 业务前置(余额≥申请天数这类领域规则)→ 由**声明式工作流的 preconditions/business_rules** 承载
       (grounded、可回查),不在连接器层按字段名嗅探硬塞;
    ③ 都没有 → 仅 HTTP 2xx。

    pre 只保留与具体业务无关的通用闸门:鉴权通过、(写操作)必填完整。
    """
    pre = [Assertion(name="auth_ok", expr="auth_passed == true")]
    if risk == RiskLevel.L3:
        pre.append(Assertion(name="fields_complete", expr="fields_complete == true"))
    if success_rule:
        post = [Assertion(name="success", expr=success_rule), _HTTP_2XX]
    else:
        post = [_HTTP_2XX]
    return Assertions(pre=pre, post=post)


def build_connector_body(action: ActionSpec, *, tenant: str, subsystem: str,
                         success_rule: str | None = None, auth_hint: str = "",
                         as_step: bool = False, business: str = "",
                         internal: bool = False,
                         fact_check_query: str | None = None,
                         fact_check_expr: str | None = None) -> ConnectorBody:
    adapter = auth_adapters.select_adapter(auth_hint)
    required_set = set(action.required_in)
    bindings = [b for p in action.params_in
                if (b := _bind_field(p, required=p in required_set)) is not None]
    field_docs = {b.platform_std: action.field_docs[b.param]
                  for b in bindings if b.param in action.field_docs}
    sys_key = subsystem.split("-")[-1].lower()
    risk = _risk_for(action.method)
    # 写方法不自动重试(无幂等键时重试会重复提交);读可重试
    is_write = action.method.upper() in _WRITE_METHODS
    failure = FailureHandling(max_retries=0) if is_write else FailureHandling(max_retries=2)
    # 步骤连接器、或显式标 internal 的前置查询(开表单/查模板/查余额)→ 内部:不发现/导出/直调
    visibility = "internal" if (as_step or internal) else "catalog"
    return ConnectorBody(
        endpoint=action.endpoint, method=action.method, auth_kind=adapter.kind,
        auth_ref=f"vault://{tenant}/{sys_key}", action=action.name,
        title=action.summary, field_bindings=bindings, field_docs=field_docs,
        risk_level=risk, failure_handling=failure, workflow_step=as_step,
        business=business, visibility=visibility,
        assertions=_build_assertions(risk=risk, success_rule=success_rule),
        fact_check_query=fact_check_query, fact_check_expr=fact_check_expr,
    )
