"""Phase A3:上架目录只露业务 skill,复合流程的步骤接口隐藏(纯离线,fake store)。"""
from __future__ import annotations

from uuid import uuid4

from dano.orchestrator.skills import SkillRegistry
from dano.shared.asset_bodies import WorkflowSkillBody, WorkflowStep
from dano.shared.enums import AssetType, Subsystem


class _Env:
    def __init__(self, body: dict, asset_key: str) -> None:
        self.body, self.asset_key, self.asset_id, self.version = body, asset_key, uuid4(), 1


def _conn_env(action: str, *, workflow_step: bool = False) -> _Env:
    return _Env({"action": action, "field_bindings": [], "risk_level": "L1",
                 "workflow_step": workflow_step}, action)


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
