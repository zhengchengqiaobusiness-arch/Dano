from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from dano.onboarding.recording_results import (
    recording_display_title,
    recording_result_summary,
    skill_request_count,
)

GOAL = "请将我接下来在页面中实际完成的每项业务操作分别生成一个可调用能力。"


def test_history_title_uses_capabilities_not_recording_goal() -> None:
    draft = {
        "title": GOAL,
        "capabilities": [
            {"capability_id": "search", "name": "search", "title": "查询记录"},
            {"capability_id": "submit", "name": "submit", "title": "提交记录"},
        ],
        "steps": [
            {"step_id": "s1", "method": "GET", "path": "/api/page"},
            {"step_id": "s2", "method": "POST", "path": "/api/save"},
        ],
    }
    assert recording_display_title(user_title=GOAL, draft=draft, goal=GOAL) == "查询记录、提交记录"
    assert skill_request_count(draft) == 2


def test_history_summary_rejects_evidence_dump_as_request_count() -> None:
    saved = SimpleNamespace(
        asset_draft_id=uuid4(),
        asset_key="recording-result:action_1",
        created_at=None,
        body={
            "action": "action_1",
            "title": GOAL,
            "goal": {"text": GOAL},
            "capability_count": 2,
            "request_count": 955,
            "flow_spec": {
                "capabilities": [
                    {"title": "查询记录"},
                    {"title": "提交记录"},
                ],
                "steps": [
                    {"step_id": "s1", "path": "/api/page"},
                    {"step_id": "s2", "path": "/api/save"},
                ],
            },
        },
    )
    summary = recording_result_summary(saved)
    assert summary["title"] == "查询记录、提交记录"
    assert summary["request_count"] == 2
    assert summary["capability_count"] == 2


def test_operator_title_still_wins_when_it_is_a_real_name() -> None:
    draft = {
        "capabilities": [{"title": "查询记录"}, {"title": "提交记录"}],
        "steps": [{"step_id": "s1"}],
    }
    assert recording_display_title(user_title="请假申请", draft=draft, goal=GOAL) == "请假申请"
