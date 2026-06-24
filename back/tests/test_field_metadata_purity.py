"""字段元数据保真(补 test_get_action_schema 未覆盖的边角):

- 锁不定模板(tid 空/不匹配)时**绝不跨模板串台**(只保留各变体一致字段);
- tid 带/不带 `_template` 后缀都能锁定本业务那一支;
- 显式声明 string 的字段即便描述含「预算」也不得被判 number(关键词不越权)。
"""
from __future__ import annotations

from dano.agent_tools import tools
from dano.capabilities.oa_templates import RuoYiFlowableTemplate
from dano.shared.std_fields import is_numeric_field


def _variant(reason_desc: str) -> dict:
    return {"type": "object", "properties": {"flowTask": {"type": "object", "properties": {
        "variables": {"type": "object", "required": ["amount"], "properties": {
            "reason": {"type": "string", "description": reason_desc},
            "amount": {"type": "number", "description": "采购金额(元)"}}}}}}}


_SPEC = {
    "paths": {"/biz/flow/submit": {"post": {"requestBody": {"content": {"application/json": {
        "schema": {"oneOf": [{"$ref": "#/components/schemas/Submit_purchase_template"},
                             {"$ref": "#/components/schemas/Submit_leavecancel_template"}]}}}}}}},
    "components": {"schemas": {"AjaxResult": {},
                              "Submit_purchase_template": _variant("采购事由"),
                              "Submit_leavecancel_template": _variant("销假说明")}},
}


def test_pin_tolerates_template_suffix_and_captures_required():
    t = RuoYiFlowableTemplate()
    for tid in ("purchase_template", "purchase"):     # 带/不带后缀都锁定本业务
        lv = tools._submit_leaf_fields(_SPEC, t, tid)
        assert lv["reason"]["description"] == "采购事由"       # 决不串成「销假说明」
        assert lv["amount"]["required"] is True               # 捕获 schema 必填标记
        assert lv["reason"]["required"] is False              # 可选忠实


def test_unpinned_never_cross_contaminates():
    # tid 空 → 冲突描述字段宁缺毋错(绝不把销假模板的「销假说明」安给采购的 reason)
    lv = tools._submit_leaf_fields(_SPEC, RuoYiFlowableTemplate(), "")
    assert (lv.get("reason") or {}).get("description") != "销假说明"
    assert "reason" not in lv                                 # 描述冲突 → 不给


def test_declared_string_not_flipped_to_number_by_keyword():
    assert is_numeric_field("title", "预算标题", declared_type="string") is False
    assert is_numeric_field("amount", "预算金额", declared_type="number") is True
    assert is_numeric_field("amount", "预算金额", declared_type=None) is True   # 无声明仍走启发


# ── 整表信封防泄漏:formData 这类序列化串绝不作用户参数,应拆成业务叶子 ──────────────
def test_form_envelope_decomposed_to_leaves():
    from dano.shared.asset_bodies import WorkflowStep
    steps = [WorkflowStep(kind="call", action="submit",
                          inputs={"formData": "field:formData",
                                  "flowTask.taskId": "step:start.data.taskId"})]
    leaves = {"amount": {"path": "flowTask.variables.amount", "type": "number", "required": False},
              "title": {"path": "flowTask.variables.title", "type": "string", "required": False},
              "templateId": {"path": "flowTask.templateId"}}   # 流程内部 → 排除
    uf = tools._decompose_form_envelopes(steps, ["formData"], leaves)
    assert uf == ["amount", "title"]                          # 信封换成业务叶子
    assert "templateId" not in uf                             # 流程内部不当用户字段
    ins = steps[0].inputs
    assert "field:formData" not in ins.values()              # 信封映射已删
    assert ins["flowTask.variables.amount"] == "field:amount"   # 逐叶子映射到真实嵌套路径
    assert ins["flowTask.taskId"] == "step:start.data.taskId"   # 其它映射不动


def test_form_envelope_stripped_when_no_leaves():
    from dano.shared.asset_bodies import WorkflowStep
    steps = [WorkflowStep(kind="call", action="submit", inputs={"formData": "field:formData"})]
    uf = tools._decompose_form_envelopes(steps, ["formData"], {})   # 无叶子(无 dialect)
    assert uf == []                                          # 拆不出 → 至少剔除信封,不暴露黑盒
    assert "field:formData" not in steps[0].inputs.values()


def test_manifest_strips_form_envelope():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem
    sk = SkillSpec(skill_id="A-OA.x", subsystem=Subsystem("A-OA"), action="x", risk_level=RiskLevel.L3,
                   is_workflow=True, required_fields=[], optional_fields=["formData", "amount"],
                   field_docs={"amount": "金额"})
    props = to_manifest(sk).parameters["properties"]
    assert "formData" not in props and "amount" in props    # 信封不进契约,业务字段保留
