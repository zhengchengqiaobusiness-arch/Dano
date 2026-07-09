from __future__ import annotations

from dano.shared.asset_bodies import FactCheckSpec, Invariant, PageScriptBody, SuccessEvidence


def test_recorded_page_script_body_carries_api_request():
    body = PageScriptBody(
        action="submit_leave",
        title="请假申请",
        api_request={
            "method": "POST",
            "url": "/api/leave",
            "body_template": {"reason": "{{reason}}"},
        },
        actions=[],
        required_fields=["reason"],
            success_evidence=SuccessEvidence(ui=["保存成功"]),
    )

    dumped = body.model_dump()
    back = PageScriptBody.model_validate(dumped)

    assert back.actions == []
    assert back.api_request["method"] == "POST"
    assert back.required_fields == ["reason"]
    assert back.success_evidence and back.success_evidence.ui == ["保存成功"]


def test_invariant_and_factcheck_shared_with_workflow_path():
    from dano.shared.asset_bodies import WorkflowSkillBody

    assert Invariant.__module__ == WorkflowSkillBody.__module__ == FactCheckSpec.__module__
