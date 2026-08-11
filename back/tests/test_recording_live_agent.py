from __future__ import annotations

import json

import pytest

from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowLink,
    FlowStep,
    ParamField,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    apply_flow_edits,
    recording_agent_state,
    recording_agent_validation,
)
from dano.execution.page.recording_live import merge_live_agent_state, recording_delta
from dano.onboarding.recording_pi import RecordingPiSession


def _flow() -> FlowSpec:
    return FlowSpec(
        flow_id="live-flow",
        tenant="tenant",
        subsystem="system",
        steps=[
            FlowStep(
                step_id="detail",
                method="GET",
                path="/items/detail",
                url="/items/detail",
                response_json={"data": {"jobId": "JOB-998877"}},
                source_meta={"request_id": "req-detail", "request_index": 1},
            ),
            FlowStep(
                step_id="submit",
                method="POST",
                path="/items/update",
                url="/items/update",
                params=[ParamField(path="jobId", key="jobId", value="JOB-998877")],
                source_meta={"request_id": "req-submit", "request_index": 2},
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-detail", request_index=1, sequence=1, method="GET", path="/items/detail"),
            RequestFact(request_id="req-submit", request_index=2, sequence=2, method="POST", path="/items/update"),
        ]),
    )


def _agent_ops() -> list[dict]:
    return [
        {
            "op": "set_goal",
            "goal": {
                "intent": "更新指定记录",
                "required_inputs": ["记录编号"],
                "success_criteria": ["详情读回为新值"],
                "evidence": [{"source": "goal_text"}],
            },
        },
        {
            "op": "set_request_role",
            "request_id": "req-submit",
            "role": "submit_anchor",
            "reason": "紧随保存点击且携带表单值",
            "evidence_refs": ["request:req-submit", "event:save-click"],
        },
        {
            "op": "set_param_source",
            "step_id": "submit",
            "path": "jobId",
            "source_kind": "chained",
            "origin_request_id": "req-detail",
            "origin_path": "response.data.jobId",
            "reason": "详情响应的内部 jobId 被更新请求复用",
        },
        {
            "op": "propose_dependency",
            "source_request_id": "req-detail",
            "source_path": "data.jobId",
            "target_request_id": "req-submit",
            "target_path": "jobId",
            "reason": "同一强值按时间从详情流向更新",
            "evidence": {"candidate": "value_link", "occurrences": 1},
        },
        {"op": "add_pitfall", "text": "更新接口使用详情返回的 jobId，不是列表 itemId", "evidence_ref": "req-detail"},
    ]


def test_live_agent_ops_write_evidenced_drafts_without_self_verifying():
    updated = apply_flow_edits(_flow(), _agent_ops())
    assert updated.goal["intent"] == "更新指定记录"
    assert updated.request_facts.analysis["req-submit"].role == "submit_anchor"
    param = updated.steps[1].params[0]
    assert param.source_kind == "chained"
    assert param.source["origin_request_id"] == "req-detail"
    assert len(updated.links) == 1
    assert updated.links[0].meta == {"verified": False, "actor": "agent"}
    assert updated.links[0].confirmed is False
    assert updated.meta["pitfalls"][0]["actor"] == "agent"
    assert {item["kind"] for item in updated.meta["agent_insights"]} >= {"goal", "role", "param_source", "link"}
    assert recording_agent_validation(updated)["report"]["agent_evidence"]["ok"] is True


@pytest.mark.parametrize("evidence", [
    "goal_text: 更新指定记录",
    ["goal_text: 更新指定记录", "event:save-click"],
])
def test_set_goal_normalizes_model_shorthand_evidence(evidence):
    updated = apply_flow_edits(_flow(), [{
        "op": "set_goal",
        "goal": {"intent": "更新指定记录", "evidence": evidence},
    }])

    assert updated.goal["evidence"]
    assert all(isinstance(item, dict) for item in updated.goal["evidence"])
    assert updated.goal["evidence"][0]["source"] == "goal_text"


def test_request_role_normalizes_model_evidence_alias_and_param_wire_path():
    updated = apply_flow_edits(_flow(), [
        {
            "op": "set_request_role",
            "request_id": "req-submit",
            "role": "submit_anchor",
            "reason": "紧随保存操作",
            "evidence": "request:req-submit",
        },
        {
            "op": "set_param_source",
            "step_id": "submit",
            "wire_path": "jobId",
            "source_kind": "chained",
            "origin_request_id": "req-detail",
            "origin_path": "response.data.jobId",
            "reason": "详情响应值被提交请求复用",
        },
    ])

    assert updated.request_facts.analysis["req-submit"].evidence["evidence_refs"] == ["request:req-submit"]
    assert updated.steps[1].params[0].source_kind == "chained"


def test_agent_page_context_survives_sync_and_dependency_paths_deduplicate():
    spec = _flow()
    spec.steps[0].params = [ParamField(path="query.pageNo", key="pageNo", value=1)]
    spec.links = [FlowLink(
        link_id="existing-link",
        source_step_id="detail",
        source_path="data.jobId",
        target_step_id="submit",
        target_path="jobId",
        evidence={"source_request_id": "req-detail", "target_request_id": "req-submit"},
    )]

    updated = apply_flow_edits(spec, [
        {
            "op": "set_param_source",
            "step_id": "detail",
            "path": "query.pageNo",
            "source_kind": "page_context",
            "reason": "录制默认分页，页面未发生页码编辑",
        },
        {
            "op": "propose_dependency",
            "source_request_id": "req-detail",
            "source_path": "response.data.jobId",
            "target_request_id": "req-submit",
            "target_path": "jobId",
            "reason": "详情内部 ID 流向提交",
            "evidence": {"candidate": "value_link"},
        },
    ])

    assert updated.steps[0].params[0].source_kind == "page_context"
    assert len(updated.links) == 1
    assert updated.links[0].link_id == "existing-link"
    assert updated.links[0].meta["actor"] == "agent"


@pytest.mark.parametrize("operation", [
    {"op": "set_goal", "goal": {"intent": "x"}},
    {"op": "set_request_role", "request_id": "req-submit", "role": "submit_anchor", "reason": "x"},
    {"op": "set_param_source", "step_id": "submit", "path": "jobId", "source_kind": "other", "reason": "x"},
    {
        "op": "propose_dependency", "source_request_id": "req-detail", "source_path": "data.jobId",
        "target_request_id": "req-submit", "target_path": "jobId",
    },
])
def test_live_agent_ops_reject_missing_or_invalid_evidence(operation):
    with pytest.raises(ValueError):
        apply_flow_edits(_flow(), [operation])


def test_recording_agent_validation_reports_agent_conclusion_without_refs():
    spec = _flow()
    spec.request_facts.analysis["req-submit"] = RequestAnalysis(
        request_id="req-submit",
        role="submit_anchor",
        evidence={"actor": "agent", "reason": "guess"},
    )
    report = recording_agent_validation(spec)["report"]
    assert report["agent_evidence"]["ok"] is False
    assert any(item["kind"] == "request_role" for item in report["agent_evidence"]["issues"])


def test_finalize_merge_replays_early_request_id_ops_on_canonical_steps():
    live = FlowSpec(flow_id="early")
    live = apply_flow_edits(live, [_agent_ops()[0], _agent_ops()[1], _agent_ops()[3]])
    assert live.links == []
    merged = merge_live_agent_state(live, _flow())
    assert merged.goal["intent"] == "更新指定记录"
    assert merged.request_facts.analysis["req-submit"].role == "submit_anchor"
    assert len(merged.links) == 1
    assert merged.links[0].source_step_id == "detail"
    assert merged.links[0].target_step_id == "submit"


class _Recorder:
    def captured_all_requests(self):
        return [
            {
                "request_id": "req-0",
                "sequence": 0,
                "method": "GET",
                "url": "https://example.test/detail?access_token=plain-query-secret",
                "headers": {"Authorization": "Bearer plain-header-secret"},
                "response_json": {"data": {"jobId": "JOB-998877"}, "accessToken": "plain-body-secret"},
                "role": "business_get",
                "confidence": 0.8,
            },
            {
                "request_id": "req-1",
                "sequence": 1,
                "method": "POST",
                "url": "https://example.test/update",
                "post_data": '{"jobId":"JOB-998877"}',
                "response_json": {"ok": True},
                "role": "business_write",
                "confidence": 0.9,
            },
        ]

    def recorded_page_events(self):
        return [{"kind": "click", "text": "保存"}]


def test_recording_delta_is_incremental_and_fully_redacted():
    delta = recording_delta(_Recorder(), since_seq=0, goal_text="更新记录")
    serialized = json.dumps(delta, ensure_ascii=False)
    assert delta["next_seq"] == 2
    assert len(delta["requests"]) == 2
    assert delta["heuristic_candidates"]["value_links"][0]["target_request_id"] == "req-1"
    assert "plain-query-secret" not in serialized
    assert "plain-header-secret" not in serialized
    assert "plain-body-secret" not in serialized
    assert recording_delta(_Recorder(), since_seq=1)["requests"][0]["request_id"] == "req-1"


class _LargeRecorder:
    def captured_all_requests(self):
        return [
            {
                "request_id": f"req-{index}",
                "sequence": index,
                "method": "GET",
                "url": f"https://example.test/items/{index}",
                "response_json": {
                    "items": [
                        {"description": "field-value-" + "x" * 1000}
                        for _item in range(30)
                    ],
                },
            }
            for index in range(61)
        ]

    def recorded_page_events(self):
        return []


def test_recording_delta_pages_without_losing_requests_and_compacts_responses():
    recorder = _LargeRecorder()
    first = recording_delta(recorder, since_seq=0, limit=10)

    assert first["since_seq"] == 0
    assert first["next_seq"] == 10
    assert first["total_seq"] == 61
    assert first["has_more"] is True
    assert len(first["requests"]) == 10
    assert "__truncated_items__" in json.dumps(first["requests"][0]["response_json"])
    assert len(json.dumps(first, ensure_ascii=False)) < 200_000

    seen = []
    cursor = 0
    while True:
        page = recording_delta(recorder, since_seq=cursor, limit=10)
        seen.extend(item["request_id"] for item in page["requests"])
        cursor = page["next_seq"]
        if not page["has_more"]:
            break
    assert seen == [f"req-{index}" for index in range(61)]


def test_recording_state_compacts_large_response_schemas_without_mutating_facts():
    spec = _flow()
    schema = {f"field_{index}": {"type": "string", "description": "x" * 2000} for index in range(200)}
    spec.request_facts.requests[0].response_schema = schema
    before = spec.request_facts.requests[0].response_schema.copy()

    state = recording_agent_state(spec)
    projected_schema = state["facts"]["captured_requests"][0]["response_schema"]

    assert projected_schema["__truncated_keys__"] > 0
    assert len(json.dumps(state, ensure_ascii=False)) < 500_000
    assert spec.request_facts.requests[0].response_schema == before


@pytest.mark.asyncio
async def test_recording_session_delta_question_and_live_prompt_contract():
    questions = []

    async def ask(**payload):
        questions.append(payload)
        return {"answered": True, "answer": "选项A"}

    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "b" * 32,
    )
    session.bind_flow_spec(_flow())
    session.bind_live_recording(_Recorder(), goal_text="更新记录", operator_asker=ask)
    delta = await session.get_recording_delta(0, limit=1)
    assert delta["has_more"] is True
    assert delta["requests"][0]["request_id"] == "req-0"
    answer = await session.ask_operator(text="选择？", options=["选项A"])
    assert answer == {"answered": True, "answer": "选项A"}
    assert questions[0]["text"] == "选择？"

    prompts = []

    async def fake_prompt(text, *, timeout_s=0):
        prompts.append((text, timeout_s))
        return {"status": "submitted"}

    session.prompt = fake_prompt
    result = await session.notify_live_batch({"reason": "finalize", "since_seq": 2})
    assert result["status"] == "submitted"
    assert "get_recording_delta(since_seq=2)" in prompts[0][0]
    assert "finalize" in prompts[0][0]
    assert "submit_recording_plan" in prompts[0][0]
    assert "plan.ops" in prompts[0][0]
