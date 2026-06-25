"""剧本(playbook)渲染泛化门禁:IR + 确定性渲染器**零框架字面量**,LLM 路黑话回退。

保证多业务/多系统/不同公司通用:剧本只携带 grounded 的**本系统数据**(模板 id/审批节点/字段路径/
真实动作名),渲染器/IR 自身不写死任何 OA/工作流引擎专有词(procInsId/OA token/post_workflow_handle…)。
"""
from __future__ import annotations

from dano.catalog.manifest import to_manifest
from dano.generation.playbook import build_playbook
from dano.generation.playbook_writer import framework_leaks, render_playbook_md
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem

# 渲染器/IR **不得自己写死**的框架专有词(grounded 数据里的不算)
_BLACKTALK = ["procInsId", "procDefId", "taskId", "OA token", "OA 调用凭证",
              "post_workflow_handle", "startFlow", "AjaxResult"]


def _purchase_manifest():
    sk = SkillSpec(skill_id="A-OA.submit_purchase", subsystem=Subsystem("A-OA"),
                   action="submit_purchase", risk_level=RiskLevel.L3, is_workflow=True, has_api=True,
                   title="采购申请", field_docs={"amount": "采购金额(元)"}, field_types={"amount": "number"},
                   required_fields=["amount"], optional_fields=["reason"],
                   business_meta={"approvalChain": [{"step": "直属主管"}, {"step": "行政审批"}]},
                   goal={"success_criteria": ["业务单据已创建"], "forbidden_steps": ["delete_document_ids"]},
                   workflow_invariants=[{"check": "response.code==200", "evidence": {"query_action": "q"}}])
    return to_manifest(sk)


def test_deterministic_playbook_has_no_framework_blacktalk():
    spec = build_playbook("A-OA", "submit_purchase", [_purchase_manifest()],
                          template_id="purchase_template")
    md = render_playbook_md(spec, "dano-test")
    leaks = [w for w in _BLACKTALK if w in md]
    assert not leaks, f"确定性剧本渲染器框架黑话泄漏: {leaks}"


def test_ir_recovery_needs_are_neutral():
    """生命周期恢复段的"所需标识"用中立概念,不写某框架字段名(procInsId/taskId)。"""
    sk = SkillSpec(skill_id="A-OA.cancel", subsystem=Subsystem("A-OA"), action="cancel",
                   risk_level=RiskLevel.L3, is_workflow=True, has_api=True, title="撤销")
    spec = build_playbook("A-OA", "cancel", [to_manifest(sk)])
    needs = " ".join(r.get("needs", "") for r in spec.recovery)
    assert "procInsId" not in needs and "taskId" not in needs


def test_framework_leaks_gate_passes_grounded_blocks_invented():
    spec_json = '{"field_mappings":[{"target_location":"flowTask.variables.amount"}]}'
    # 规格里有 flowTask → grounded,放行
    assert framework_leaks("映射到 flowTask.variables.amount", spec_json) == []
    # 规格里没有 procInsId → LLM 凭空发明,拦下(回退确定性版)
    assert "procinsid" in framework_leaks("回查 procInsId 出现在列表", spec_json)
