"""Phase A2:六段剧本喂 DSL IR —— 前置/不变量进 ②/⑤,剧本反映真实业务逻辑(纯离线)。"""
from __future__ import annotations

from dano.catalog.manifest import SkillManifest
from dano.generation.playbook import build_playbook
from dano.generation.playbook_writer import render_playbook_md, validate_playbook_facts


def _wf_manifest() -> SkillManifest:
    return SkillManifest(
        name="A-OA.submit_leave", subsystem="A-OA", action="submit_leave", title="提交请假",
        description="提交请假(A-OA · 流程类动作)", integration="workflow",
        risk_level="L3", requires_confirmation=True,
        parameters={"type": "object",
                    "properties": {"leaveDays": {"type": "string", "description": "天数"}},
                    "required": ["leaveDays"]})


def test_ir_preconditions_and_invariants_rendered():
    spec = build_playbook(
        "A-OA", "leave", [_wf_manifest()],
        ir_preconditions=[{"check": "response.balance >= leave_days", "message": "假期余额不足"}],
        ir_invariants=[{"check": "after.balance == before.balance - leave_days",
                        "message": "余额未按请假天数扣减"}])
    md = render_playbook_md(spec, "dano-a-oa-leave")
    assert "假期余额不足" in md          # ② 办理前(IR 前置)
    assert "余额未按请假天数扣减" in md   # ⑤ 办理后(IR 不变量)
    assert "## ⑤ 办理后" in md


def test_ir_playbook_still_grounded():
    # 喂 IR 后的剧本仍须自洽:不引用不存在的脚本/参数
    spec = build_playbook("A-OA", "leave", [_wf_manifest()],
                          ir_invariants=[{"check": "response.code == 200", "message": "未生成实例"}])
    md = render_playbook_md(spec, "dano-a-oa-leave")
    actions = {o.op for o in spec.operations}
    fields = {f["name"] for o in spec.operations for f in o.fields}
    assert validate_playbook_facts(md, actions=actions, fields=fields) == []
