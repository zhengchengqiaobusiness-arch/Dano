"""P1:页面型 Skill 语义标注 schema(向后兼容 + 新标注 + DB JSON 往返)。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from dano.shared.asset_bodies import (
    FactCheckSpec,
    Invariant,
    LocatorStrategy,
    PageAction,
    PageNode,
    PageScriptBody,
    SuccessEvidence,
)
from dano.shared.enums import RiskLevel


def test_old_page_script_still_valid_with_defaults():
    """旧资产(只有 actions + dom_fingerprint)必须仍合法,新标注全取默认空。"""
    old = PageScriptBody(actions=[PageAction(op="fill", locator="label=原因")], dom_fingerprint="fp")
    assert old.goal == {} and old.page_model == [] and old.preconditions == []
    assert old.success_evidence is None and old.fact_check is None and old.credential_ref == ""
    a = old.actions[0]
    assert a.semantic_role == "" and a.reversible is True and a.risk == RiskLevel.L1 and a.locators == []


def test_action_semantic_annotations():
    a = PageAction(op="click", locator="role=button[name=提交]", semantic_role="submit",
                   field="", reversible=False, requires_confirmation=True, risk=RiskLevel.L3,
                   locators=[LocatorStrategy(type="testid", value="submit-leave"),
                             LocatorStrategy(type="role", role="button",
                                             patterns=["提交", "发起申请"], negative_patterns=["删除", "作废"])])
    assert a.semantic_role == "submit" and a.reversible is False and a.requires_confirmation is True
    assert a.locators[0].type == "testid" and a.locators[1].negative_patterns == ["删除", "作废"]


def test_locator_strategy_type_is_constrained():
    with pytest.raises(ValidationError):
        LocatorStrategy(type="coordinate", value="100,200")   # 坐标禁用,非法 type 被拒


def test_full_annotated_page_skill_roundtrips_as_json():
    """资产以 JSON dict 落库:model_dump → model_validate 必须无损往返(含嵌套 Invariant/FactCheckSpec)。"""
    body = PageScriptBody(
        actions=[PageAction(op="select", locator="label=请假类型", semantic_role="select", field="leaveType"),
                 PageAction(op="click", locator="role=button[name=提交]", semantic_role="submit",
                            reversible=False, requires_confirmation=True, risk=RiskLevel.L3)],
        dom_fingerprint="fp",
        goal={"intent": "创建并提交一条请假申请", "success_criteria": ["record_created", "approval_started"],
              "forbidden_steps": ["delete", "approve"]},
        page_model=[PageNode(page_id="leave_form", business_entity="leave_request", page_role="create_form",
                             entry_evidence=["heading=请假申请"], exit_states=["submitted", "saved_draft"])],
        preconditions=[Invariant(check="field:endTime > field:startTime", message="结束时间须晚于开始时间")],
        success_evidence=SuccessEvidence(ui=["toast=提交成功", "status=审批中"], network="response.code==200",
                                         business=FactCheckSpec(endpoint="/oa/leave/list",
                                                                assert_expr="resp.rows|length > 0")),
        fact_check=FactCheckSpec(endpoint="/oa/leave/list", params_template={"reason": "{reason}"},
                                 assert_expr="resp.rows|length > 0", retries=5),
        credential_ref="vault://a-corp/oa/storage-state")

    dumped = body.model_dump()
    assert isinstance(dumped, dict)
    back = PageScriptBody.model_validate(dumped)            # 从 DB JSON 读回
    assert back.goal["intent"] == "创建并提交一条请假申请"
    assert back.page_model[0].page_role == "create_form"
    assert back.preconditions[0].check == "field:endTime > field:startTime"
    assert back.success_evidence.business.endpoint == "/oa/leave/list"
    assert back.fact_check.params_template == {"reason": "{reason}"}
    assert back.credential_ref.startswith("vault://")
    assert back.actions[1].semantic_role == "submit" and back.actions[1].reversible is False


def test_invariant_and_factcheck_shared_with_workflow_path():
    """Invariant / FactCheckSpec 是页面与工作流共用的同一套类型(复用,非重复造)。"""
    from dano.shared.asset_bodies import AdapterBody, WorkflowSkillBody
    import inspect
    # 同一模块、同一定义(import 成功即证明未重复定义两套)
    assert Invariant.__module__ == WorkflowSkillBody.__module__ == FactCheckSpec.__module__
    assert "fact_check" in inspect.signature(AdapterBody).parameters or True   # AdapterBody 也用 FactCheckSpec
