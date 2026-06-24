"""Phase A3:上架目录只露业务 skill,复合流程的步骤接口隐藏(纯离线,fake store)。"""
from __future__ import annotations

from uuid import uuid4

from dano.orchestrator.skills import SkillRegistry
from dano.shared.asset_bodies import WorkflowSkillBody, WorkflowStep
from dano.shared.enums import AssetType, Subsystem


class _Env:
    def __init__(self, body: dict, asset_key: str) -> None:
        self.body, self.asset_key, self.asset_id, self.version = body, asset_key, uuid4(), 1


def _conn_env(action: str, *, workflow_step: bool = False,
              visibility: str = "catalog", business: str = "") -> _Env:
    return _Env({"action": action, "field_bindings": [], "risk_level": "L1",
                 "workflow_step": workflow_step, "visibility": visibility,
                 "business": business}, action)


class _Store:
    def __init__(self, by_type: dict) -> None:
        self.by_type = by_type

    async def list_published(self, asset_type, scope):  # noqa: ANN001
        return self.by_type.get(asset_type, [])


async def test_workflow_steps_hidden_only_business_skill_shown():
    wf = WorkflowSkillBody(
        action="submit_leave", title="提交请假",
        steps=[WorkflowStep(action="start_leave_flow", inputs={"templateId": "const:t"}),
               WorkflowStep(action="submit_flow_task", inputs={"taskId": "step:start_leave_flow.data.taskId"})],
        user_fields=["leaveDays"], required_fields=["leaveDays"])
    store = _Store({
        AssetType.WORKFLOW: [_Env(wf.model_dump(), "submit_leave")],
        # 两个步骤连接器(应隐藏)+ 一个独立查询连接器(应可见)
        AssetType.CONNECTOR: [_conn_env("start_leave_flow"), _conn_env("submit_flow_task"),
                              _conn_env("query_balance")],
    })
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    actions = {s.action for s in reg.skills}

    assert "submit_leave" in actions                      # 业务 skill 露出
    assert "query_balance" in actions                     # 独立查询露出
    assert "start_leave_flow" not in actions              # 步骤接口隐藏
    assert "submit_flow_task" not in actions              # 步骤接口隐藏

    sl = next(s for s in reg.skills if s.action == "submit_leave")
    assert sl.is_workflow is True


async def test_connector_fact_check_from_body_not_gated_by_action_name():
    """P3:事实核查随**连接器资产体**走 —— 通用动作也能带核查;ACTION_META 退为原型 demo 兜底。"""
    store = _Store({AssetType.CONNECTOR: [
        _Env({"action": "create_customer", "field_bindings": [], "risk_level": "L3",
              "fact_check_query": "query_customer", "fact_check_expr": "response.id != null"},
             "create_customer"),
        _Env({"action": "create_order", "field_bindings": [], "risk_level": "L3"}, "create_order"),
        _Env({"action": "create_leave", "field_bindings": [], "risk_level": "L3"}, "create_leave"),
    ]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem("B-CRM")])
    by = {s.action: s for s in reg.skills}
    # 资产体声明 → 通用连接器(非 demo 动作名)也有事实核查
    assert by["create_customer"].fact_check_query == "query_customer"
    assert by["create_customer"].fact_check_expr == "response.id != null"
    # 通用连接器无声明、非 demo 动作 → 无核查(诚实,不臆造)
    assert by["create_order"].fact_check_query is None
    assert by["create_order"].fact_check_expr is None
    # 原型 demo 动作未在体里声明 → ACTION_META 兜底(向后兼容)
    assert by["create_leave"].fact_check_query == "query_balance"


async def test_workflow_step_connector_never_exposed():
    # 即便某 workflow_step 连接器没有任何复合流程引用(孤儿),也绝不单独露出,不污染目录
    store = _Store({AssetType.CONNECTOR: [_conn_env("query_balance"),
                                          _conn_env("orphan_submit_step", workflow_step=True)]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    actions = {s.action for s in reg.skills}
    assert "query_balance" in actions
    assert "orphan_submit_step" not in actions


# ── 契约层:剔除注入字段 + 数值类型保真(选项 B 治本)──────────────────────────
def test_manifest_strips_flow_internal_and_types_numbers():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(
        skill_id="A-OA.submit_demo_purchase", subsystem=Subsystem.OA, action="submit_demo_purchase",
        risk_level=RiskLevel.L3, title="采购申请提交", is_workflow=True,
        field_docs={"amount": "采购金额(元)", "quantity": "采购数量"},
        required_fields=["title", "quantity", "amount", "reason", "templateId", "procInsId"],
        optional_fields=[])
    props = to_manifest(sk).parameters["properties"]
    assert "templateId" not in props and "procInsId" not in props   # 注入字段被剔除
    assert props["amount"]["type"] == "number"
    assert props["quantity"]["type"] == "number"
    assert props["reason"]["type"] == "string"


def test_field_types_override_wins_over_heuristic():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(skill_id="A-OA.x", subsystem=Subsystem.OA, action="x", risk_level=RiskLevel.L1,
                   field_types={"code": "string", "qty": "integer"},
                   required_fields=["code", "qty"], optional_fields=[])
    props = to_manifest(sk).parameters["properties"]
    assert props["code"]["type"] == "string"     # 信源声明 string,压过名字启发式
    assert props["qty"]["type"] == "integer"


def test_manifest_preserves_select_and_datetime_semantics():
    """选择型(enum)/日期(datetime)字段的语义不被塌成裸 string:
    enum → type=string + format=name-ref + 描述提示「传名字→ID」;datetime → format=date-time。
    (修真实导出里 领导/人力 显示成 string、日期丢类型的缺陷。)"""
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _ptype, _select_fields
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(skill_id="A-OA.submit_form", subsystem=Subsystem.OA, action="submit_form",
                   risk_level=RiskLevel.L3,
                   field_types={"领导": "enum", "人力": "enum", "startTime": "datetime", "请假类型": "number"},
                   required_fields=["领导", "人力", "startTime", "请假类型"], optional_fields=[])
    props = to_manifest(sk).parameters["properties"]
    # 选择型:不再是裸 string,带 name-ref 标记 + 「传名字→ID」提示
    assert props["领导"]["type"] == "string" and props["领导"]["format"] == "name-ref"
    assert "查内部 ID" in props["领导"]["description"] and "勿直接传 ID" in props["领导"]["description"]
    # 日期:带 date-time format
    assert props["startTime"]["format"] == "date-time"
    # 导出层「类型」列还原成语义类型,不再显示 string
    assert _ptype("领导", props, set()) == "枚举·名字→ID"
    assert _ptype("startTime", props, set()) == "datetime"
    assert _ptype("请假类型", props, {"请假类型"}) == "number"
    assert _select_fields(props) == ["领导", "人力"]


def test_ruoyi_parses_approval_chain_from_prose():
    from dano.capabilities.oa_templates import match_template
    spec = {
        "paths": {"/workflow/handle/startFlow": {"post": {"description":
            "目录:\n| 流程 | templateId | 审批链 |\n|---|---|---|\n"
            "| 采购申请 | `purchase_template` | 发起人填表 → 直属主管(动态·部门负责人) → "
            "〔金额>5000 时〕行政审批 → 〔金额>30000 时〕总经理审批 → 系统自动记账 → 结束 |\n"}}},
        "components": {"schemas": {"AjaxResult": {}}},
    }
    meta = match_template(spec).parse_approval_chain(spec, "purchase_template")
    assert meta["flow"] == "采购申请"
    steps = [c["step"] for c in meta["approvalChain"]]
    assert "直属主管" in steps and "发起人填表" not in steps and "结束" not in steps
    assert {"field": "amount", "gt": 5000, "adds": "行政审批"} in meta["thresholds"]
    assert {"field": "amount", "gt": 30000, "adds": "总经理审批"} in meta["thresholds"]
    assert any(c.get("condition") == "amount>5000" for c in meta["approvalChain"])  # 金额>5000 不被切坏


async def test_internal_connector_hidden_catalog_visible():
    # 前置查询(visibility=internal)不进目录;普通查询(默认 catalog)正常露出
    store = _Store({AssetType.CONNECTOR: [
        _conn_env("query_my_todo"),                                  # 独立用户级查询 → 露出
        _conn_env("get_biz_form_info", visibility="internal", business="请假"),  # 前置查询 → 隐藏
    ]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    actions = {s.action for s in reg.skills}
    assert "query_my_todo" in actions
    assert "get_biz_form_info" not in actions          # internal 前置查询不泄漏成平级 skill


async def test_connector_carries_business_tag():
    store = _Store({AssetType.CONNECTOR: [_conn_env("query_leave_status", business="请假")]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    sk = next(s for s in reg.skills if s.action == "query_leave_status")
    assert sk.business == "请假"                        # 连接器也带 business,导出可归进同一本剧本


# ── WS4:系统特定(模板清单/表单解析)归 dialect,网关零字面量 ──
def test_ruoyi_dialect_parses_template_list_and_form():
    import json as _json
    from dano.capabilities.oa_templates import RuoYiFlowableTemplate, all_templates
    t = RuoYiFlowableTemplate()
    assert t.template_list_paths()                       # RuoYi 提供模板清单端点
    rows = t.parse_template_list({"code": 200, "rows": [
        {"id": "leave_template", "name": "请假申请", "typeName": "人事", "defKey": "leave", "enableFlag": "0"}]})
    assert rows == [{"templateId": "leave_template", "name": "请假申请", "type": "人事",
                     "defKey": "leave", "enableFlag": "0"}]
    assert t.parse_template_list({"code": 401}) == []    # 鉴权失败 → 空(网关据此提示 token 失效)
    designer = _json.dumps({"formData": {"list": [
        {"__vModel__": "leaveType", "__config__": {"label": "请假类型", "tag": "el-select"}},
        {"__vModel__": "reason", "__config__": {"label": "事由", "tag": "el-input"}}]}})
    fields = t.parse_form_fields({"code": 200, "data": {"formData": designer}})
    assert {f["key"] for f in fields} == {"leaveType", "reason"}
    assert any(d.name == "ruoyi-flowable" for d in all_templates())


def test_form_field_types_from_el_controls():
    # WS6:动态表单控件 = 字段类型的权威信源(比按名字猜更准,且能识别枚举)
    import json as _json
    from dano.capabilities.oa_templates import RuoYiFlowableTemplate
    designer = _json.dumps({"list": [
        {"__vModel__": "leaveType", "__config__": {"label": "请假类型", "tag": "el-select"}},
        {"__vModel__": "leaveDays", "__config__": {"label": "天数", "tag": "el-input-number"}},
        {"__vModel__": "startDate", "__config__": {"label": "开始", "tag": "el-date-picker"}},
        {"__vModel__": "agree", "__config__": {"label": "同意", "tag": "el-switch"}},
        {"__vModel__": "reason", "__config__": {"label": "事由", "tag": "el-input"}}]})
    fs = {f["key"]: f for f in RuoYiFlowableTemplate().parse_form_fields(
        {"code": 200, "data": {"formData": designer}})}
    assert fs["leaveDays"]["json_type"] == "number"
    assert fs["leaveType"]["json_type"] == "string" and fs["leaveType"]["enum"] is True
    assert fs["startDate"]["json_type"] == "string" and fs["startDate"]["enum"] is False
    assert fs["agree"]["json_type"] == "boolean"
    assert fs["reason"]["json_type"] == "string"


def test_base_dialect_no_system_literals():
    from dano.capabilities.oa_templates import OATemplate
    # 通用基类不携带任何系统端点(子类才有)→ 主流程对未知框架不会瞎打端点
    class _Bare(OATemplate):
        def matches(self, spec):  # noqa: ANN001
            return True
    b = _Bare()
    assert b.template_list_paths() == ()
    assert b.parse_template_list({"code": 200, "rows": [{"id": "x"}]}) == []


# ── WS3:复合流程动态发现(零硬编码业务配方)──
def test_discover_flows_composites_are_dynamic_not_hardcoded():
    from dano.onboarding.discovery import discover_flows
    spec = {
        "paths": {
            "/workflow/handle/startFlow": {"post": {"summary": "发起", "description":
                "目录:\n| 流程 | templateId | 审批链 |\n|---|---|---|\n"
                "| 采购申请 | `purchase_template` | 发起人填表 → 直属主管 → 〔金额>5000 时〕行政审批 → 结束 |\n"}},
            "/biz/flow/submit": {"post": {"summary": "提交"}},
        },
        "components": {"schemas": {"AjaxResult": {},
            "StartFlowReq": {"properties": {"templateId": {"enum": ["purchase_template", "custom_xyz_template"]}}}}},
    }
    flows = discover_flows(spec)
    comp = {f["flow"]: f for f in flows if f["kind"] == "composite"}
    # 来自 spec 的 templateId 枚举,而非写死的请假/出差
    assert set(comp) == {"submit_purchase", "submit_custom_xyz"}
    assert "submit_leave" not in comp and "submit_travel" not in comp     # 旧硬编码配方已删
    assert comp["submit_purchase"]["business_meta"].get("approvalChain")  # 审批链动态解析进提案
    assert comp["submit_custom_xyz"]["title"] == "custom_xyz_template"    # 非标模板也能动态发现


def test_discover_flows_bare_crud_no_composite():
    from dano.onboarding.discovery import discover_flows
    bare = {"paths": {"/users/list": {"get": {"summary": "用户列表"}}}, "components": {"schemas": {}}}
    flows = discover_flows(bare)
    assert not any(f["kind"] == "composite" for f in flows)               # 无模板 → 不强造复合流程


# ── P1·WS5:结构化 Goal(据材料动态生成)+ forbiddenSteps grounding ──
_GOAL_SPEC = {
    "paths": {
        "/workflow/handle/startFlow": {"post": {"summary": "发起", "description":
            "| 流程 | templateId | 审批链 |\n|---|---|---|\n"
            "| 采购申请 | `purchase_template` | 发起人填表 → 直属主管 → 〔金额>5000 时〕行政审批 → 结束 |\n"}},
        "/biz/flow/submit": {"post": {"summary": "提交"}},
        "/workflow/handle/admin/terminate": {"post": {"summary": "终止流程"}},
        "/workflow/handle/reject": {"post": {"summary": "驳回"}},
    },
    "components": {"schemas": {"AjaxResult": {},
        "StartFlowReq": {"properties": {"templateId": {"enum": ["purchase_template"]}}}}},
}


def test_build_goal_is_dynamic_and_marks_forbidden():
    from dano.capabilities.oa_templates import RuoYiFlowableTemplate
    from dano.onboarding.goal import build_goal
    steps = ["post_workflow_handle_startFlow", "post_biz_flow_submit"]
    g = build_goal(_GOAL_SPEC, RuoYiFlowableTemplate(), template_id="purchase_template",
                   business="采购申请", title="采购申请提交",
                   required_inputs=["amount"], optional_inputs=["comment"], candidate_steps=steps)
    assert g.selected_template == "purchase_template"
    assert g.candidate_steps == steps
    assert "当前流程已进入有效审批节点" in g.success_criteria      # 有审批链 → 派生该成功标准
    # 危险动作进 forbidden;提交链的正常步骤不进
    assert any("terminate" in f or "reject" in f for f in g.forbidden_steps)
    assert "post_biz_flow_submit" not in g.forbidden_steps
    assert "post_workflow_handle_startFlow" not in g.forbidden_steps


def test_goal_grounding_rejects_forbidden_step():
    from dano.capabilities.oa_templates import RuoYiFlowableTemplate
    from dano.onboarding.goal import build_goal, goal_grounding
    g = build_goal(_GOAL_SPEC, RuoYiFlowableTemplate(), template_id="purchase_template",
                   business="采购申请", candidate_steps=["post_biz_flow_submit"])
    assert goal_grounding(g, ["post_workflow_handle_startFlow", "post_biz_flow_submit"]) == []  # 干净
    bad = goal_grounding(g, ["post_workflow_handle_reject"])                                     # 编入驳回他人
    assert bad and "forbiddenSteps" in bad[0]


def test_forbidden_actions_excludes_normal_submit():
    from dano.onboarding.goal import forbidden_actions
    forb = forbidden_actions(_GOAL_SPEC)
    assert "post_biz_flow_submit" not in forb and "post_workflow_handle_startFlow" not in forb
    assert any("terminate" in f for f in forb)


def test_playbook_surfaces_goal_and_field_mappings():
    from dano.catalog.manifest import SkillManifest
    from dano.generation.playbook import build_playbook
    from dano.generation.playbook_writer import render_playbook_md
    m = SkillManifest(
        name="A-OA.submit_purchase", subsystem="A-OA", action="submit_purchase",
        title="采购申请提交", description="采购申请提交(A-OA)", integration="workflow",
        risk_level="L3", requires_confirmation=True, business="采购申请",
        goal={"intent": "创建并提交采购申请", "success_criteria": ["业务单据已创建", "审批流程已发起"],
              "forbidden_steps": ["post_workflow_handle_reject"]},
        field_mappings=[{"standard_field": "amount", "target_field": "amount",
                         "target_location": "flowTask.variables.amount", "target_type": "number",
                         "source": {"type": "openapi", "path": "/biz/flow/submit",
                                    "schema_ref": "Submit_purchase_template.flowTask.variables.amount"}}],
        parameters={"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]})
    md = render_playbook_md(build_playbook("A-OA", "采购申请", [m]), "dano-a-oa")
    assert "## 目标(Goal)" in md and "创建并提交采购申请" in md and "业务单据已创建" in md
    assert "## 字段映射(可追溯)" in md
    assert "flowTask.variables.amount" in md                                  # 目标点路径
    assert "Submit_purchase_template.flowTask.variables.amount" in md         # 来源 schema_ref


async def test_workflow_skill_carries_business_meta_to_manifest():
    from dano.catalog.manifest import to_manifest
    wf = WorkflowSkillBody(
        action="submit_purchase", title="采购申请提交",
        steps=[WorkflowStep(action="start_flow", inputs={"templateId": "const:purchase_template"})],
        user_fields=["amount"], required_fields=["amount"],
        business="采购申请",
        business_meta={"approvalChain": [{"step": "直属主管"}], "thresholds": []})
    store = _Store({AssetType.WORKFLOW: [_Env(wf.model_dump(), "submit_purchase")],
                    AssetType.CONNECTOR: [_conn_env("start_flow")]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    sk = next(s for s in reg.skills if s.action == "submit_purchase")
    assert sk.business_meta.get("approvalChain")          # workflow 也带出审批链(原先被丢)
    assert to_manifest(sk).business_meta.get("approvalChain")
