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
from dano.shared.std_fields import ALL_STD_FIELDS

# 写方法 → 运行期需确认(L3);GET 只读 → L1。风险按 HTTP 方法判,不靠动作名关键词
# (避免 start_leave_flow 这类写操作因名字不含关键词被误判成 L1)。
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_HTTP_2XX = Assertion(name="http_2xx", expr="http >= 200 and http < 300")


def _risk_for(method: str) -> RiskLevel:
    return RiskLevel.L3 if method.upper() in _WRITE_METHODS else RiskLevel.L1


def _bind_field(param: str, *, required: bool = True) -> FieldBinding | None:
    pl = param.lower()
    for std in ALL_STD_FIELDS:
        if pl == std.key.lower() or pl in {a.lower() for a in std.aliases}:
            return FieldBinding(param=param, platform_std=std.key, required=required)
    return None


def _build_assertions(bindings: list[FieldBinding], *, risk: RiskLevel,
                      success_rule: str | None) -> Assertions:
    bound_std = {b.platform_std for b in bindings}
    pre = [Assertion(name="auth_ok", expr="auth_passed == true")]
    if risk == RiskLevel.L3:
        pre.append(Assertion(name="fields_complete", expr="fields_complete == true"))
        if "days" in bound_std:
            pre.append(Assertion(name="balance_enough", expr="balance >= days"))
    if success_rule:
        post = [Assertion(name="success", expr=success_rule), _HTTP_2XX]
    elif risk == RiskLevel.L3:
        post = [
            Assertion(name="has_request_id", expr="response.request_id != null"),
            Assertion(name="status_expected", expr="response.status in ['已提交','待审批']"),
            _HTTP_2XX,
        ]
    else:
        post = [_HTTP_2XX]
    return Assertions(pre=pre, post=post)


def build_connector_body(action: ActionSpec, *, tenant: str, subsystem: str,
                         success_rule: str | None = None, auth_hint: str = "") -> ConnectorBody:
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
    return ConnectorBody(
        endpoint=action.endpoint, method=action.method, auth_kind=adapter.kind,
        auth_ref=f"vault://{tenant}/{sys_key}", action=action.name,
        title=action.summary, field_bindings=bindings, field_docs=field_docs,
        risk_level=risk, failure_handling=failure,
        assertions=_build_assertions(bindings, risk=risk, success_rule=success_rule),
    )
