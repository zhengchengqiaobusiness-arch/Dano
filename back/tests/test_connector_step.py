"""复合步骤连接器:as_step 标记 workflow_step(发布闸门放宽 + 目录隐藏)。纯离线。"""
from __future__ import annotations

from dano.agent_tools.connector_builder import build_connector_body
from dano.capabilities.doc_parser import ActionSpec


def test_as_step_marks_workflow_step():
    a = ActionSpec(name="submit_x", method="POST", endpoint="/biz/x")
    assert build_connector_body(a, tenant="t", subsystem="A-OA", as_step=True).workflow_step is True
    assert build_connector_body(a, tenant="t", subsystem="A-OA").workflow_step is False


def test_non_standard_fields_are_bound_not_dropped():
    """P1:不在标准词典里的业务字段必须如实保留(identity 绑定),否则字段既不进契约、运行期也不发出。"""
    a = ActionSpec(name="create_customer", method="POST", endpoint="/crm/customer",
                   params_in=["companyName", "contactPhone", "industry", "templateId"],
                   required_in=["companyName", "contactPhone"],
                   field_docs={"companyName": "客户公司名"})
    body = build_connector_body(a, tenant="acme", subsystem="B-CRM")
    binds = {b.param: (b.platform_std, b.required) for b in body.field_bindings}
    # 业务字段以自身名 identity 绑定,必填/可选如实区分
    assert binds["companyName"] == ("companyName", True)
    assert binds["contactPhone"] == ("contactPhone", True)
    assert binds["industry"] == ("industry", False)
    # 流程内部句柄(运行期注入)不绑定、不暴露成用户参数
    assert "templateId" not in binds
    # 业务字段的语义描述也随之保留
    assert body.field_docs.get("companyName") == "客户公司名"


def test_assertions_carry_no_business_literals():
    """P2:连接器断言不得写死任何业务/系统字面量(请假余额、中文状态、单号字段)。

    成败判据只来自 success_rule(系统约定)或 HTTP 2xx;领域前置归声明式工作流的 preconditions。
    """
    a = ActionSpec(name="create_leave", method="POST", endpoint="/oa/leave",
                   params_in=["days", "reason"], required_in=["days"])
    # 即便绑定里有 days(请假天数),也不得自动塞 balance>=days
    no_rule = build_connector_body(a, tenant="a", subsystem="A-OA")
    exprs = [x.expr for x in no_rule.assertions.pre + no_rule.assertions.post]
    blob = " ".join(exprs)
    assert "balance" not in blob and "request_id" not in blob
    assert "已提交" not in blob and "待审批" not in blob
    # 无系统约定 → 后置只剩通用 HTTP 2xx
    assert [x.name for x in no_rule.assertions.post] == ["http_2xx"]
    # 有系统约定(dialect.success_rule)→ 后置 = 该约定 + HTTP 2xx
    with_rule = build_connector_body(a, tenant="a", subsystem="A-OA",
                                     success_rule="response.code == 200")
    assert [x.name for x in with_rule.assertions.post] == ["success", "http_2xx"]


def test_standard_dictionary_still_enriches():
    """标准词典退为增强而非门槛:命中的别名仍对齐到平台标准 key(跨系统同义字段统一)。"""
    a = ActionSpec(name="create_leave", method="POST", endpoint="/oa/leave",
                   params_in=["duration", "reason"], required_in=["duration"])
    binds = {b.param: b.platform_std for b in
             build_connector_body(a, tenant="a", subsystem="A-OA").field_bindings}
    assert binds["duration"] == "days"   # duration 是 days 的别名 → 对齐
    assert binds["reason"] == "reason"
