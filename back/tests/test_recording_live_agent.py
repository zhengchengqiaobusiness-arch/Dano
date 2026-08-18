from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import time

import pytest

from dano.execution.page.flow_spec import (
    _auto_confirm_ready_capabilities,
    _dedupe_preread_candidates,
    _query_operation_key,
    FlowCapability,
    FlowSpec,
    FlowLink,
    FlowStep,
    ParamField,
    RequestAnalysis,
    RequestFact,
    RequestFacts,
    apply_flow_edits,
    apply_recording_agent_submission,
    flow_spec_to_api_request,
    recording_agent_state,
    recording_agent_validation,
    validate_flow_spec,
)
from dano.execution.page.recording_live import (
    _constrain_semantic_plan_to_goal,
    LiveNotebook,
    apply_recording_agent_edit,
    merge_live_agent_state,
    recording_delta,
)
from dano.onboarding.recording_pi import RecordingPiSession


def test_preread_dedupe_preserves_same_endpoint_for_distinct_user_commands() -> None:
    requests = [
        {
            "request_id": "req-edit",
            "method": "GET",
            "url": "https://example.test/api/records/get?id=42",
            "query": {"id": "42"},
            "trigger_op": "click",
            "trigger_transaction_id": "txn-edit",
            "trigger_locator": "text=编辑",
            "sequence": 1,
        },
        {
            "request_id": "req-detail",
            "method": "GET",
            "url": "https://example.test/api/records/get?id=42",
            "query": {"id": "42"},
            "trigger_op": "click",
            "trigger_transaction_id": "txn-detail",
            "trigger_locator": "text=详情",
            "sequence": 2,
        },
        {
            "request_id": "req-progress",
            "method": "GET",
            "url": "https://example.test/api/records/get?id=42",
            "query": {"id": "42"},
            "trigger_op": "click",
            "trigger_transaction_id": "txn-progress",
            "trigger_locator": "text=进度",
            "sequence": 3,
        },
    ]

    assert [
        item["request_id"] for item in _dedupe_preread_candidates(requests)
    ] == ["req-edit", "req-detail", "req-progress"]


def test_query_operation_groups_all_reads_from_one_visible_command() -> None:
    shared_meta = {
        "trigger_op": "click",
        "trigger_transaction_id": "txn-progress",
        "trigger_locator": "text=进度",
        "causality_confidence": "high",
    }
    steps = [
        FlowStep(
            step_id="record", method="GET", path="/api/records/get?id=42",
            source_meta=shared_meta,
        ),
        FlowStep(
            step_id="tasks", method="GET", path="/api/workflow/tasks?recordId=42",
            source_meta=shared_meta,
        ),
        FlowStep(
            step_id="users", method="GET", path="/api/users/simple-list",
            source_meta=shared_meta,
        ),
    ]

    assert len({_query_operation_key(step) for step in steps}) == 1


def test_goal_constraint_never_relabels_an_unrelated_capability_by_position() -> None:
    spec = FlowSpec(meta={
        "recording_goal_text": (
            "目的：查看详情并提交记录\n"
            "预期产出能力数量：2\n"
            "能力1：查看记录详情\n"
            "能力2：提交记录"
        ),
    })
    plan = {
        "business_understanding": {"business_name": "记录"},
        "capabilities": [{
            "name": "submit_record",
            "title": "提交记录",
            "intent": "提交记录",
            "kind": "submit",
            "anchor_step_id": "submit-step",
            "request_refs": [{"step_id": "submit-step", "usage": "execute"}],
        }],
    }

    constrained = _constrain_semantic_plan_to_goal(spec, plan)

    assert len(constrained["capabilities"]) == 1
    assert constrained["capabilities"][0]["title"] == "提交记录"
    assert constrained["capabilities"][0]["anchor_step_id"] == "submit-step"


def test_finalize_rejects_a_stale_plan_instead_of_relabeling_goal_slots() -> None:
    goal_text = (
        "目的：提交、查看和编辑申请\n"
        "预期产出能力数量：3\n"
        "能力1：提交申请\n"
        "能力2：查看申请详情\n"
        "能力3：编辑申请"
    )
    live = FlowSpec(meta={
        "recording_goal_text": goal_text,
        "capability_model": {
            "semantic_plan": {
                "capabilities": [{
                    "name": "wrong_detail",
                    "title": "查看申请详情",
                    "intent": "查看申请详情",
                    "kind": "submit",
                    "anchor_step_id": "old-edit",
                    "request_refs": [{"step_id": "old-edit", "usage": "execute"}],
                }],
            },
        },
    })
    finalized = FlowSpec(
        title="申请",
        goal={"capabilities": ["提交申请", "查看申请详情", "编辑申请"]},
        meta={"recording_goal_text": goal_text},
        steps=[
            FlowStep(
                step_id="edit", method="POST", path="/records/submit",
                params=[ParamField(path="id", key="id", value="record-42")],
                source_meta={
                    "request_id": "req-edit", "request_index": 1,
                    "role": "business_write", "trigger_op": "click",
                    "trigger_locator": "text=提交",
                    "trigger_transaction_id": "txn-edit",
                },
            ),
            FlowStep(
                step_id="detail", method="GET", path="/records/get?id=record-42",
                source_meta={
                    "request_id": "req-detail", "request_index": 2,
                    "role": "business_get", "trigger_op": "click",
                    "trigger_locator": "text=详情",
                    "trigger_transaction_id": "txn-detail",
                },
            ),
            FlowStep(
                step_id="submit", method="POST", path="/records/submit",
                source_meta={
                    "request_id": "req-submit", "request_index": 3,
                    "role": "business_write", "trigger_op": "click",
                    "trigger_locator": "text=提交",
                    "trigger_transaction_id": "txn-submit",
                },
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-edit", request_index=1, method="POST", path="/records/submit"),
            RequestFact(request_id="req-detail", request_index=2, method="GET", path="/records/get"),
            RequestFact(request_id="req-submit", request_index=3, method="POST", path="/records/submit"),
        ]),
    )

    merged = merge_live_agent_state(live, finalized)

    assert merged.capabilities == []
    assert merged.meta["recording_goal_contract"]["satisfied"] is False


def test_entity_hydration_read_can_also_anchor_requested_detail_capability() -> None:
    goal_text = (
        "目的：查看并编辑申请\n"
        "预期产出能力数量：2\n"
        "能力1：查看申请详情\n"
        "能力2：编辑申请"
    )
    live = FlowSpec(meta={"recording_goal_text": goal_text})
    finalized = FlowSpec(
        title="申请",
        goal={"capabilities": ["查看申请详情", "编辑申请"]},
        meta={"recording_goal_text": goal_text},
        steps=[
            FlowStep(
                step_id="detail", method="GET", path="/records/get?id=record-42",
                params=[ParamField(path="query.id", key="id", value="record-42")],
                response_json={"data": {"id": "record-42", "reason": "leave"}},
                source_meta={
                    "request_id": "req-detail", "request_index": 1,
                    "role": "business_get", "trigger_op": "click",
                    "trigger_locator": "text=编辑", "trigger_transaction_id": "txn-edit-open",
                    "record_hydration_for_write_ids": ["edit"],
                },
            ),
            FlowStep(
                step_id="edit", method="POST", path="/records/update",
                params=[
                    ParamField(path="body.id", key="id", value="record-42"),
                    ParamField(
                        path="body.reason", key="reason", value="updated",
                        source_kind="user_input", exposed_to_user=True,
                    ),
                ],
                source_meta={
                    "request_id": "req-edit", "request_index": 2,
                    "role": "business_write", "trigger_op": "click",
                    "trigger_locator": "text=提交", "trigger_transaction_id": "txn-edit-submit",
                },
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-detail", request_index=1, method="GET", path="/records/get"),
            RequestFact(request_id="req-edit", request_index=2, method="POST", path="/records/update"),
        ]),
    )

    merged = merge_live_agent_state(live, finalized)

    assert [cap.title for cap in merged.capabilities] == ["查看申请详情", "编辑申请"]
    assert merged.capabilities[0].kind == "inspect"
    assert [
        next(ref.step_id for ref in cap.request_refs if ref.usage == "execute")
        for cap in merged.capabilities
    ] == ["detail", "edit"]


@pytest.mark.asyncio
async def test_cancelling_recording_prompt_also_cancels_the_sidecar_turn(monkeypatch):
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "d" * 32,
    )
    session._proc = object()
    calls = []

    async def fake_command(command_type, **_kwargs):
        calls.append(command_type)
        if command_type == "prompt":
            await asyncio.Event().wait()
        return {"status": "cancelled"}

    monkeypatch.setattr(session, "_command", fake_command)
    task = asyncio.create_task(session.prompt("analyze"))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == ["prompt", "cancel"]


def test_live_notebook_preserves_pending_questions_for_final_analysis() -> None:
    shadow = FlowSpec(meta={
        "live_pending_questions": [{
            "question_id": "live-question-1",
            "text": "字段含义存在冲突",
            "options": ["A", "B"],
            "context_ref": "field:1",
        }],
    })

    notebook = LiveNotebook.from_shadow(shadow)
    merged = notebook.apply_to(FlowSpec())

    assert notebook.pending_questions[0]["question_id"] == "live-question-1"
    assert merged.meta["live_pending_questions"][0]["context_ref"] == "field:1"


def test_live_notebook_preserves_operator_recording_goal_for_final_boundary() -> None:
    goal_text = (
        "目的：查询并编辑记录\n"
        "预期产出能力数量：2\n"
        "能力1：查询记录\n"
        "能力2：编辑记录"
    )
    notebook = LiveNotebook.from_shadow(FlowSpec(meta={"recording_goal_text": goal_text}))

    assert notebook.meta["recording_goal_text"] == goal_text


def test_live_notebook_can_merge_an_underfilled_recording_goal() -> None:
    goal_text = (
        "目的：查询并提交记录\n"
        "预期产出能力数量：2\n"
        "能力1：查询记录\n"
        "能力2：提交记录"
    )
    shadow = FlowSpec(
        meta={
            "recording_goal_text": goal_text,
            "capability_model": {
                "semantic_plan": {
                    "capabilities": [{
                        "name": "query_record",
                        "title": "查询记录",
                        "intent": "查询记录",
                        "kind": "query_status",
                        "anchor_step_id": "live-query",
                        "request_refs": [{
                            "step_id": "live-query",
                            "usage": "execute",
                        }],
                    }],
                },
            },
        },
        steps=[FlowStep(
            step_id="live-query",
            method="GET",
            path="/records/page",
            source_meta={
                "request_id": "req-query",
                "role": "business_get",
                "trigger_op": "click",
                "trigger_locator": "text=查询",
                "trigger_transaction_id": "txn-query",
            },
        )],
    )
    finalized = FlowSpec(
        title="记录",
        meta={"recording_goal_text": goal_text},
        steps=[FlowStep(
            step_id="final-query",
            method="GET",
            path="/records/page",
            source_meta={
                "request_id": "req-query",
                "role": "business_get",
                "trigger_op": "click",
                "trigger_locator": "text=查询",
                "trigger_transaction_id": "txn-query",
            },
        )],
    )

    notebook = LiveNotebook.from_shadow(shadow)
    merged = notebook.apply_to(finalized)

    assert notebook.step_request_ids == {"live-query": "req-query"}
    assert [cap.title for cap in merged.capabilities] == ["查询记录"]


@pytest.mark.asyncio
async def test_recording_state_projection_does_not_block_browser_event_loop(monkeypatch):
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "e" * 32,
    )
    session.bind_flow_spec(_flow())

    def slow_projection(_spec):
        time.sleep(0.08)
        return {"flow_version": 0}

    monkeypatch.setattr(
        "dano.execution.page.flow_spec.recording_agent_state",
        slow_projection,
    )
    operation = asyncio.create_task(session.get_recording_state())
    started = time.monotonic()
    await asyncio.sleep(0.01)
    tick_elapsed = time.monotonic() - started
    await operation

    assert tick_elapsed < 0.04


@pytest.mark.asyncio
async def test_recording_plan_compilation_does_not_block_browser_event_loop(monkeypatch):
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "f" * 32,
    )
    session.bind_flow_spec(_flow())

    async def slow_compilation(spec, **_kwargs):
        time.sleep(0.08)
        return spec.model_copy(deep=True)

    monkeypatch.setattr(
        "dano.execution.page.flow_spec.apply_recording_agent_submission",
        slow_compilation,
    )
    operation = asyncio.create_task(session.apply_submission(
        {"ops": []}, mode="plan", base_flow_version=0,
    ))
    started = time.monotonic()
    await asyncio.sleep(0.01)
    tick_elapsed = time.monotonic() - started
    await operation

    assert tick_elapsed < 0.04


@pytest.mark.asyncio
async def test_live_evidence_binding_does_not_block_browser_event_loop(monkeypatch):
    session = RecordingPiSession(
        tenant="tenant",
        subsystem="system",
        recording_id="recording_" + "a" * 32,
    )
    session.bind_flow_spec(_flow())
    session.bind_live_recording(_Recorder())

    def slow_binding(*_args, **_kwargs):
        time.sleep(0.08)
        return []

    monkeypatch.setattr(
        "dano.execution.page.recording_field_identity.bind_field_evidence",
        slow_binding,
    )
    operation = asyncio.create_task(session.refresh_live_evidence())
    started = time.monotonic()
    await asyncio.sleep(0.01)
    tick_elapsed = time.monotonic() - started
    await operation

    assert tick_elapsed < 0.04


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
        request_facts=RequestFacts(
            requests=[
                RequestFact(
                    request_id="req-detail",
                    request_index=1,
                    sequence=1,
                    method="GET",
                    path="/items/detail",
                    url="https://example.test/items/detail",
                    response_json={"data": {"jobId": "JOB-998877"}},
                ),
                RequestFact(
                    request_id="req-submit",
                    request_index=2,
                    sequence=2,
                    method="POST",
                    path="/items/update",
                    url="https://example.test/items/update",
                    post_data={"jobId": "JOB-998877"},
                ),
            ],
            field_evidence=[{
                "event_id": "evt-job",
                "request_id": "req-submit",
                "path": "jobId",
                "field_aliases": ["jobId"],
                "label": "任务编号",
                "required": False,
                "control_kind": "text",
            }],
        ),
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
            "role": "business_write",
            "reason": "紧随保存点击且携带表单值",
            "evidence_refs": ["request:req-submit", "event:save-click"],
        },
        {
            "op": "set_param_source",
            "step_id": "submit",
            "path": "jobId",
            "source_kind": "response_binding",
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
    assert updated.request_facts.analysis["req-submit"].role == "business_write"
    param = updated.steps[1].params[0]
    # chained compiles into the executable previous_response contract and keeps
    # the agent taxonomy in evidence.
    assert param.source_kind == "previous_response"
    assert param.source["step_id"] == "detail"
    assert param.source["response_path"] == "data.jobId"
    assert param.source["origin_request_id"] == "req-detail"
    assert any(item.get("source_kind") == "response_binding" for item in param.evidence)
    assert len(updated.links) == 1
    assert updated.links[0].meta == {
        "verified": False,
        "actor": "agent",
        "captured_value_match": True,
        "unverified_reason": "依赖提案已更新，需要重新执行 dependency_execute 验证",
    }
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
            "role": "business_write",
            "reason": "紧随保存操作",
            "evidence": "request:req-submit",
        },
        {
            "op": "set_param_source",
            "step_id": "submit",
            "wire_path": "jobId",
            "source_kind": "response_binding",
            "origin_request_id": "req-detail",
            "origin_path": "response.data.jobId",
            "reason": "详情响应值被提交请求复用",
        },
    ])

    assert updated.request_facts.analysis["req-submit"].evidence["evidence_refs"] == ["request:req-submit"]
    assert updated.steps[1].params[0].source_kind == "previous_response"


def test_live_field_semantics_resolve_request_id_and_cover_source_required_and_name():
    updated = apply_flow_edits(_flow(), [
        {
            "op": "set_param_source",
            "step_id": "req-submit",
            "path": "jobId",
            "source_kind": "context",
            "context_key": "jobId",
            "reason": "页面上下文自动提供",
        },
        {
            "op": "set_param_required",
            "step_id": "req-submit",
            "path": "jobId",
            "required": False,
            "reason": "页面允许不填写该筛选条件",
            "evidence_refs": ["request:req-submit", "control:jobId"],
        },
        {
            "op": "rename_field",
            "step_id": "req-submit",
            "path": "jobId",
            "label": "任务编号",
            "reason": "页面控件标签为任务编号",
            "evidence_refs": ["request:req-submit", "control:jobId"],
        },
    ])

    param = updated.steps[1].params[0]
    assert param.source_kind == "page_context"
    assert param.exposed_to_user is False
    assert param.required is False
    assert param.source["required_state"] == "optional"
    assert param.key == "任务编号"
    assert param.label == "任务编号"
    assert {item.get("kind") for item in param.evidence} >= {
        "param_source", "param_required", "field_name",
    }


def test_param_type_is_grounded_by_the_exact_cited_control_and_preserves_wire_type():
    spec = _flow()
    param = spec.steps[1].params[0]
    param.type = "number"
    param.wire_type = "string"
    spec.request_facts.field_evidence.append({
        "event_id": "evt-job-id",
        "evidence_id": "evt-job-id",
        "field": "任务编号",
        "label": "任务编号",
        "value": param.value,
        "op": "fill",
        "editable": True,
        "control_kind": "text",
        "binding_status": "bound",
        "request_id": "req-submit",
        "wire_path": "body.jobId",
    })

    updated = apply_flow_edits(spec, [{
        "op": "set_param_type",
        "request_id": "req-submit",
        "wire_path": "body.jobId",
        "business_type": "string",
        "reason": "页面中是文本输入框",
        "evidence_refs": ["evt-job-id"],
    }])

    actual = updated.steps[1].params[0]
    assert actual.type == "string"
    assert actual.wire_type == "string"
    assert any(item.get("kind") == "param_type" for item in actual.evidence)


def test_recording_plan_accepts_the_supported_param_type_operation():
    spec = _flow()
    param = spec.steps[1].params[0]
    param.type = "number"
    param.wire_type = "string"
    spec.request_facts.field_evidence[0].update({
        "evidence_id": "evt-job",
        "binding_status": "bound",
        "wire_path": "body.jobId",
        "editable": True,
    })

    updated = asyncio.run(apply_recording_agent_submission(
        spec,
        submission={
            "semantic_plan": {
                "business_understanding": {"summary": "更新记录"},
                "capabilities": [{
                    "name": "update_item",
                    "title": "更新记录",
                    "kind": "submit",
                    "anchor_step_id": "submit",
                    "request_refs": [{"step_id": "submit", "usage": "execute"}],
                }],
                "unresolved_items": [],
            },
            "ops": [{
                "op": "set_param_type",
                "request_id": "req-submit",
                "wire_path": "body.jobId",
                "business_type": "string",
                "reason": "页面中是文本输入框",
                "evidence_refs": ["evt-job"],
            }],
        },
        mode="plan",
    ))

    assert updated.steps[1].params[0].type == "string"
    result = updated.meta["recording_agent_session"]["op_results"][0]
    assert result["status"] == "applied"
    assert result["requested_target"] == {
        "request_id": "req-submit",
        "wire_path": "body.jobId",
    }


def test_recording_plan_keeps_one_search_capability_when_page_navigation_is_context():
    def search_param(path: str, key: str) -> ParamField:
        return ParamField(
            path=path,
            key=key,
            label="搜索关键词",
            value="唐",
            type="string",
            wire_type="string",
            category="user_param",
            source_kind="user_input",
            exposed_to_user=True,
            required=False,
        )

    spec = FlowSpec(
        flow_id="recorded-search",
        steps=[
            FlowStep(
                step_id="ajax-search",
                method="GET",
                path="/shiwen2017/ajaxSearchSoD.aspx?valuekey=唐",
                params=[search_param("query.keyword", "keyword")],
                semantic_role="business_get",
                source_meta={
                    "request_id": "req-ajax",
                    "role": "business_get",
                    "trigger_op": "fill",
                    "trigger_locator": "input[name=keyword]",
                },
            ),
            FlowStep(
                step_id="search-page",
                method="GET",
                path="/search?value=唐",
                params=[search_param("query.value", "value")],
                semantic_role="business_get",
                source_meta={
                    "request_id": "req-page",
                    "role": "business_get",
                    "trigger_op": "fill",
                    "trigger_locator": "input[name=keyword]",
                },
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(
                request_id="req-ajax", request_index=1, method="GET",
                path="/shiwen2017/ajaxSearchSoD.aspx?valuekey=唐",
            ),
            RequestFact(
                request_id="req-page", request_index=2, method="GET",
                path="/search?value=唐",
            ),
        ]),
    )

    updated = asyncio.run(apply_recording_agent_submission(
        spec,
        submission={
            "semantic_plan": {
                "business_understanding": {"summary": "按关键词搜索内容"},
                "capabilities": [{
                    "name": "search_content",
                    "title": "搜索内容",
                    "kind": "query",
                    "anchor_step_id": "ajax-search",
                    "request_refs": [
                        {"step_id": "ajax-search", "usage": "execute"},
                        {"step_id": "search-page", "usage": "fact_check"},
                    ],
                }],
                "unresolved_items": [],
            },
            "ops": [{
                "op": "set_request_role",
                "request_id": "req-page",
                "role": "context",
                "reason": "页面跳转只负责展示同一次搜索结果",
                "evidence_refs": ["req-page"],
            }],
        },
        mode="plan",
    ))

    assert [capability.name for capability in updated.capabilities] == ["search_content"]
    assert updated.meta["capability_generation"]["status"] == "ready"


def test_param_type_rejects_a_model_type_that_contradicts_the_cited_control():
    spec = _flow()
    spec.request_facts.field_evidence.append({
        "event_id": "evt-job-id",
        "evidence_id": "evt-job-id",
        "field": "任务编号",
        "label": "任务编号",
        "op": "fill",
        "editable": True,
        "control_kind": "text",
        "binding_status": "bound",
        "request_id": "req-submit",
        "wire_path": "body.jobId",
    })

    with pytest.raises(ValueError, match="contradicts field_evidence"):
        apply_flow_edits(spec, [{
            "op": "set_param_type",
            "request_id": "req-submit",
            "wire_path": "body.jobId",
            "business_type": "number",
            "reason": "模型猜测为数字",
            "evidence_refs": ["evt-job-id"],
        }])


def test_live_field_semantics_resolve_request_id_and_qualified_body_path():
    updated = apply_flow_edits(_flow(), [{
        "op": "set_param_source",
        "step_id": "req-submit",
        "wire_path": "body.jobId",
        "source_kind": "context",
        "context_key": "jobId",
        "reason": "页面上下文自动提供",
    }])

    assert updated.steps[1].params[0].source_kind == "page_context"


def test_cited_unbound_page_control_grounds_all_field_axes_and_public_short_code():
    spec = _flow()
    spec.steps[0].params = [ParamField(
        path="query.referenceCode",
        key="referenceCode",
        value="1",
    )]
    spec.request_facts.field_evidence.append({
        "event_id": "evt-reference-code",
        "evidence_id": "evt-reference-code",
        "field": "业务单号",
        "label": "业务单号",
        "value": "1",
        "op": "fill",
        "required_observed": False,
        "editable": True,
        "disabled": False,
        "read_only": False,
        "control_kind": "text",
        "binding_status": "unbound",
        "binding_reason": "the page control has no transport alias",
    })

    updated = apply_flow_edits(spec, [
        {
            "op": "set_param_source",
            "request_id": "req-detail",
            "wire_path": "query.referenceCode",
            "source_kind": "caller_input",
            "reason": "操作人填写了业务单号",
            "evidence_refs": ["evt-reference-code"],
        },
        {
            "op": "rename_field",
            "request_id": "req-detail",
            "wire_path": "query.referenceCode",
            "label": "业务单号",
            "reason": "页面标签明确显示业务单号",
            "evidence_refs": ["evt-reference-code"],
        },
        {
            "op": "set_param_required",
            "request_id": "req-detail",
            "wire_path": "query.referenceCode",
            "required": False,
            "reason": "页面控件没有必填标记",
            "evidence_refs": ["evt-reference-code"],
        },
    ])

    param = updated.steps[0].params[0]
    assert param.label == "业务单号"
    assert param.source["required_state"] == "optional"
    assert any(
        item.get("kind") == "page_control"
        and item.get("evidence_id") == "evt-reference-code"
        and item.get("editable") is True
        for item in param.evidence
    )

    updated.capabilities = [FlowCapability(
        name="query_records",
        kind="query",
        nodes=[{"id": "query", "type": "call", "step_id": "detail"}],
        confirmed=True,
    )]
    from dano.execution.page.flow_spec import sync_capability_scoped_views, validate_flow_spec

    updated = sync_capability_scoped_views(updated)
    report_text = json.dumps(validate_flow_spec(updated), ensure_ascii=False)
    assert "capability_internal_field_exposed" not in report_text


def test_model_cannot_replace_an_interacted_readonly_inner_select_with_constant():
    spec = _flow()
    spec.steps[0].params = [ParamField(
        path="query.type", key="type", value="2",
        category="user_param", source_kind="form_option",
        exposed_to_user=True,
    )]
    spec.request_facts.field_evidence.append({
        "event_id": "evt-type-select",
        "evidence_id": "evt-type-select",
        "request_id": "req-detail",
        "wire_path": "query.type",
        "label": "请假类型",
        "value": "2",
        "op": "select",
        "control_kind": "select",
        "disabled": False,
        # Element/Ant style selects render a readonly inner input even though
        # the outer widget is user-editable.
        "read_only": True,
        "binding_status": "bound",
    })

    with pytest.raises(ValueError, match="contradicts cited editable page control"):
        apply_flow_edits(spec, [{
            "op": "set_param_source",
            "request_id": "req-detail",
            "wire_path": "query.type",
            "source_kind": "constant",
            "reason": "录制值为 2，模型误判为常量",
            "evidence_refs": ["evt-type-select"],
        }])


def test_edit_hydration_is_an_upstream_default_with_caller_override():
    spec = _flow()
    field = spec.steps[1].params[0]
    field.category = "user_param"
    field.source_kind = "user_input"
    field.exposed_to_user = True
    field.editable = True
    spec.request_facts.field_evidence[0].update({
        "evidence_id": "evt-job",
        "wire_path": "body.jobId",
        "op": "fill",
        "editable": True,
        "recorded_user_input": True,
        "binding_status": "bound",
    })

    updated = apply_flow_edits(spec, [
        {
            "op": "set_param_source",
            "request_id": "req-submit",
            "wire_path": "body.jobId",
            "source_kind": "response_binding",
            "origin_request_id": "req-detail",
            "origin_path": "data.jobId",
            "reason": "打开编辑页时由详情响应初始化，保存前仍允许用户修改",
            "evidence_refs": ["evt-job", "req-detail"],
        },
        {
            "op": "rename_field",
            "request_id": "req-submit",
            "wire_path": "body.jobId",
            "label": "任务编号",
            "reason": "页面控件标签为任务编号",
            "evidence_refs": ["evt-job"],
        },
        {
            "op": "set_param_required",
            "request_id": "req-submit",
            "wire_path": "body.jobId",
            "required": False,
            "reason": "页面控件没有必填标记",
            "evidence_refs": ["evt-job"],
        },
    ])

    param = updated.steps[1].params[0]
    assert param.source_kind == "previous_response"
    assert param.source["allow_caller_override"] is True
    assert param.category == "user_param"
    assert param.exposed_to_user is True
    assert param.editable is True
    assert (param.label, param.required) == ("任务编号", False)
    assert param.source["required_state"] == "optional"

def test_cited_control_with_a_different_value_does_not_ground_public_source():
    spec = _flow()
    spec.steps[0].params = [ParamField(
        path="query.referenceCode", key="referenceCode", value="A-1",
    )]
    spec.request_facts.field_evidence.append({
        "event_id": "evt-other-field",
        "evidence_id": "evt-other-field",
        "field": "其他字段",
        "label": "其他字段",
        "value": "B-2",
        "op": "fill",
        "editable": True,
        "control_kind": "text",
        "binding_status": "unbound",
    })

    updated = apply_flow_edits(spec, [{
        "op": "set_param_source",
        "request_id": "req-detail",
        "wire_path": "query.referenceCode",
        "source_kind": "caller_input",
        "reason": "错误地引用了另一个页面控件",
        "evidence_refs": ["evt-other-field"],
    }])

    assert not any(
        item.get("kind") == "page_control"
        for item in updated.steps[0].params[0].evidence
    )


def test_source_reclassification_preserves_the_independent_required_axis():
    spec = _flow()
    spec.steps[1].params[0].required = True
    spec.steps[1].params[0].source = {"required_state": "required"}
    updated = apply_flow_edits(spec, [{
        "op": "set_param_source",
        "step_id": "req-submit",
        "path": "jobId",
        "source_kind": "caller_input",
        "reason": "调用方填写",
    }])

    param = updated.steps[1].params[0]
    assert param.required is True
    assert param.source["required_state"] == "required"


def test_live_field_semantics_reject_wrong_transport_namespace():
    with pytest.raises(ValueError, match="target.*not found"):
        apply_flow_edits(_flow(), [{
            "op": "set_param_source",
            "step_id": "req-submit",
            "wire_path": "query.jobId",
            "source_kind": "context",
            "context_key": "jobId",
            "reason": "错误地声明为查询参数",
        }])


def test_live_field_semantics_reject_unknown_target_instead_of_reporting_success():
    with pytest.raises(ValueError, match="target.*not found"):
        apply_flow_edits(_flow(), [{
            "op": "set_param_source",
            "step_id": "req-missing",
            "path": "jobId",
            "source_kind": "context",
            "context_key": "jobId",
            "reason": "页面上下文自动提供",
        }])


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
            "source_kind": "context",
            "context_key": "pageNo",
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
    {"op": "set_request_role", "request_id": "req-submit", "role": "business_write", "reason": "x"},
    {"op": "set_param_source", "step_id": "submit", "path": "jobId", "source_kind": "other", "reason": "x"},
    {
        "op": "propose_dependency", "source_request_id": "req-detail", "source_path": "data.jobId",
        "target_request_id": "req-submit", "target_path": "jobId",
    },
])
def test_live_agent_ops_reject_missing_or_invalid_evidence(operation):
    with pytest.raises(ValueError):
        apply_flow_edits(_flow(), [operation])


def test_param_source_constant_compiles_to_system_const_and_requires_recorded_value():
    spec = _flow()
    spec.steps[1].params.append(ParamField(path="billType", key="billType", value="oa_duty_leave"))
    spec.steps[1].params.append(ParamField(path="emptyFlag", key="emptyFlag", value=None))

    updated = apply_flow_edits(spec, [{
        "op": "set_param_source",
        "step_id": "submit",
        "path": "billType",
        "source_kind": "constant",
        "reason": "录制值固定的单据类型常量",
    }])
    param = next(item for item in updated.steps[1].params if item.path == "billType")
    assert param.source_kind == "constant"
    assert param.category == "system_const"
    assert param.exposed_to_user is False

    constant_only = FlowSpec(
        flow_id="constant-only",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            path="/leave/submit",
            url="https://example.test/leave/submit",
            body_source='{"billType":"oa_duty_leave"}',
            params=[param.model_copy(deep=True)],
        )],
    )
    api_request, errors = flow_spec_to_api_request(constant_only)
    assert errors == []
    assert api_request is not None
    assert api_request["body_template"]["billType"] == "oa_duty_leave"
    assert "billType" not in api_request["params"]

    with pytest.raises(ValueError, match="requires a recorded value"):
        apply_flow_edits(spec, [{
            "op": "set_param_source",
            "step_id": "submit",
            "path": "emptyFlag",
            "source_kind": "constant",
            "reason": "假设为常量",
        }])


def test_param_source_session_header_rejected_on_body_paths():
    spec = _flow()
    spec.steps[1].params.append(ParamField(path="billType", key="billType", value="oa_duty_leave"))
    with pytest.raises(ValueError, match="requires session_key"):
        apply_flow_edits(spec, [{
            "op": "set_param_source",
            "step_id": "submit",
            "path": "billType",
            "source_kind": "session",
            "reason": "固定值，误归为会话头",
        }])


@pytest.mark.parametrize(("strategy", "value", "internal_kind"), [
    ("uuid", "29cba714-c6ce-4747-baec-e2c5d37d6868", "system_generated"),
    ("random_string", "591f22581bf8300bf467392c04dde9a7", "system_generated"),
    ("random_number", 591225, "system_generated"),
    ("now_ms", 1782891442000, "system_time"),
    ("now_s", 1782891442, "system_time"),
    ("now_iso", "2026-08-15T09:30:00+00:00", "system_time"),
    ("now_date", "2026-08-15", "system_time"),
])
def test_generated_param_source_compiles_to_runtime_injection(
    strategy, value, internal_kind,  # noqa: ANN001
):
    spec = _flow()
    spec.steps[1].params.append(ParamField(
        path="body.runtimeValue",
        key="runtimeValue",
        value=value,
    ))

    updated = apply_flow_edits(spec, [{
        "op": "set_param_source",
        "step_id": "submit",
        "wire_path": "body.runtimeValue",
        "source_kind": "generated",
        "strategy": strategy,
        "reason": "录制证据表明该值由页面运行时生成",
    }])

    param = next(item for item in updated.steps[1].params if item.key == "runtimeValue")
    assert param.source_kind == internal_kind
    assert param.source["strategy"] == strategy
    assert param.category == "runtime_var"
    assert param.exposed_to_user is False


def test_param_source_page_context_pagination_is_caller_override_with_recorded_default():
    spec = _flow()
    spec.steps[0].params = [ParamField(path="query.pageNo", key="pageNo", value=1)]
    updated = apply_flow_edits(spec, [{
        "op": "set_param_source",
        "step_id": "detail",
        "path": "query.pageNo",
        "source_kind": "context",
        "context_key": "pageNo",
        "reason": "录制默认分页，未被操作人修改",
    }])
    param = updated.steps[0].params[0]
    assert param.source_kind == "page_context"
    assert param.source["context_key"] == "pageNo"
    assert param.source["default_value"] == 1
    assert param.source["caller_override"] is True
    assert param.category == "user_param"
    assert param.exposed_to_user is True
    assert param.required is False

    from dano.execution.page.flow_spec import _field_source_configuration_advice

    assert _field_source_configuration_advice(param) is None
    schema = updated.model_copy(deep=True)
    schema.capabilities = [FlowCapability(
        name="query", kind="query", nodes=[{"id": "call", "type": "call", "step_id": "detail"}],
    )]
    api_request, errors = flow_spec_to_api_request(schema)
    assert errors == []
    assert api_request is not None
    assert "pageNo" in api_request["params"]
    assert api_request["sample_inputs"]["pageNo"] == 1


def test_param_source_computed_requires_executable_strategy():
    spec = _flow()
    spec.steps[1].params.extend([
        ParamField(path="startTime", key="开始时间", value=1785945600000),
        ParamField(path="endTime", key="结束时间", value=1786032000000),
    ])
    spec.steps[1].params.append(ParamField(
        path="processVariablesStr", key="流程变量", value='{"day":1}',
    ))

    spec = apply_flow_edits(spec, [
        {
            "op": "set_param_source", "step_id": "submit", "path": "startTime",
            "source_kind": "caller_input", "reason": "调用方提供开始时间",
        },
        {
            "op": "set_param_source", "step_id": "submit", "path": "endTime",
            "source_kind": "caller_input", "reason": "调用方提供结束时间",
        },
    ])

    with pytest.raises(ValueError, match="date_span_days_json"):
        apply_flow_edits(spec, [{
            "op": "set_param_source",
            "step_id": "submit",
            "path": "processVariablesStr",
            "source_kind": "computed",
            "reason": "天数由起止时间推导",
        }])

    updated = apply_flow_edits(spec, [{
        "op": "set_param_source",
        "step_id": "submit",
        "path": "processVariablesStr",
        "source_kind": "computed",
        "strategy": "date_span_days_json",
        "start_field": "开始时间",
        "end_field": "结束时间",
        "reason": "天数由起止时间推导",
    }])
    param = next(item for item in updated.steps[1].params if item.path == "processVariablesStr")
    assert param.source_kind == "computed"
    assert param.source["start_field"] == "开始时间"
    assert param.source["sample_verified"] is True


def test_param_source_computed_accepts_public_wire_paths_from_another_capability_step():
    spec = FlowSpec(
        flow_id="computed-preflight",
        steps=[
            FlowStep(
                step_id="approval-detail",
                method="GET",
                path="/approval-detail?processVariablesStr=%7B%22day%22%3A1%7D",
                params=[ParamField(
                    path="processVariablesStr",
                    key="processVariablesStr",
                    value='{"day":1}',
                )],
            ),
            FlowStep(
                step_id="submit",
                method="POST",
                path="/submit",
                params=[
                    ParamField(path="startTime", key="startTime", value=1785945600000),
                    ParamField(path="endTime", key="endTime", value=1786032000000),
                ],
            ),
        ],
        capabilities=[FlowCapability(
            name="submit_request",
            kind="submit",
            step_ids=["approval-detail", "submit"],
        )],
    )
    spec = apply_flow_edits(spec, [
        {
            "op": "set_param_source", "step_id": "submit", "path": "body.startTime",
            "source_kind": "caller_input", "reason": "调用方提供开始时间",
        },
        {
            "op": "set_param_source", "step_id": "submit", "path": "body.endTime",
            "source_kind": "caller_input", "reason": "调用方提供结束时间",
        },
    ])

    updated = apply_flow_edits(spec, [{
        "op": "set_param_source",
        "step_id": "approval-detail",
        "path": "query.processVariablesStr",
        "source_kind": "computed",
        "strategy": "date_span_days_json",
        "start_field": "body.startTime",
        "end_field": "body.endTime",
        "output_key": "day",
        "reason": "预请求天数由提交字段的起止时间计算",
    }])

    param = updated.steps[0].params[0]
    assert param.source_kind == "computed"
    assert param.source["sample_verified"] is True


@pytest.mark.asyncio
async def test_computed_body_field_renders_and_executes_from_caller_dates():
    from dano.execution.page.request_capture import execute_api_request

    spec = FlowSpec(
        flow_id="computed-body",
        steps=[FlowStep(
            step_id="detail",
            method="POST",
            url="https://example.test/get-approval-detail",
            path="/get-approval-detail",
            body_source=json.dumps({
                "startTime": 1785945600000,
                "endTime": 1786032000000,
                "processVariablesStr": '{"day":1}',
            }),
            params=[
                ParamField(path="startTime", key="开始时间", value=1785945600000, type="datetime"),
                ParamField(path="endTime", key="结束时间", value=1786032000000, type="datetime"),
                ParamField(path="processVariablesStr", key="流程变量", value='{"day":1}'),
            ],
        )],
    )
    spec = apply_flow_edits(spec, [
        {
            "op": "set_param_source", "step_id": "detail", "path": "startTime",
            "source_kind": "caller_input", "reason": "调用方提供开始时间",
        },
        {
            "op": "set_param_source", "step_id": "detail", "path": "endTime",
            "source_kind": "caller_input", "reason": "调用方提供结束时间",
        },
    ])
    updated = apply_flow_edits(spec, [{
        "op": "set_param_source",
        "step_id": "detail",
        "path": "processVariablesStr",
        "source_kind": "computed",
        "strategy": "date_span_days_json",
        "start_field": "开始时间",
        "end_field": "结束时间",
        "reason": "流程天数由起止日期计算",
    }])
    api_request, errors = flow_spec_to_api_request(updated)
    assert errors == []
    assert api_request is not None

    out = await execute_api_request(api_request, {
        "开始时间": "2026-08-06 00:00:00",
        "结束时间": "2026-08-07 00:00:00",
    }, send=False)
    assert out["ok"] is True
    assert out["body"]["processVariablesStr"] == '{"day":1}'
    assert isinstance(out["body"]["startTime"], int)


def test_param_source_chained_requires_known_origin_and_creates_draft_link():
    spec = _flow()
    with pytest.raises(ValueError, match="not part of the recorded facts"):
        apply_flow_edits(spec, [{
            "op": "set_param_source",
            "step_id": "submit",
            "path": "jobId",
            "source_kind": "response_binding",
            "origin_request_id": "req-nowhere",
            "origin_path": "data.jobId",
            "reason": "臆造的上游",
        }])

    updated = apply_flow_edits(_flow(), [{
        "op": "set_param_source",
        "step_id": "submit",
        "path": "jobId",
        "source_kind": "response_binding",
        "origin_request_id": "req-detail",
        "origin_path": "response.data.jobId",
        "reason": "详情响应值被提交复用",
    }])
    assert len(updated.links) == 1
    assert updated.links[0].confirmed is False
    assert updated.links[0].source_step_id == "detail"
    assert updated.links[0].target_path == "jobId"


def test_field_axis_ops_reject_ungrounded_evidence_refs():
    with pytest.raises(ValueError, match="evidence_refs must cite recorded facts"):
        apply_flow_edits(_flow(), [{
            "op": "set_param_required",
            "step_id": "submit",
            "path": "jobId",
            "required": True,
            "reason": "臆断必填",
            "evidence_refs": ["直觉判断", "行业常识"],
        }])


def test_field_axis_ops_reject_conclusions_that_contradict_recorded_control():
    spec = _flow()
    with pytest.raises(ValueError, match="required.*contradicts field_evidence"):
        apply_flow_edits(spec, [{
            "op": "set_param_required",
            "step_id": "submit",
            "path": "jobId",
            "required": True,
            "reason": "模型猜测为必填",
            "evidence_refs": ["evt-job"],
        }])
    with pytest.raises(ValueError, match="label.*contradicts field_evidence"):
        apply_flow_edits(spec, [{
            "op": "rename_field",
            "step_id": "submit",
            "path": "jobId",
            "label": "页",
            "reason": "错误地把分页标签配给业务字段",
            "evidence_refs": ["evt-job"],
        }])


@pytest.mark.asyncio
async def test_field_grounding_rejection_is_returned_in_per_op_status():
    updated = await apply_recording_agent_submission(_flow(), submission={
        "ops": [{
            "op": "rename_field",
            "step_id": "submit",
            "path": "jobId",
            "label": "页",
            "reason": "错误标签",
            "evidence_refs": ["evt-job"],
        }],
    }, mode="plan")

    assert updated.steps[1].params[0].key == "jobId"
    assert updated.meta["recording_agent_session"]["op_results"] == [{
        "index": 0,
        "op": "rename_field",
        "status": "rejected",
        "requested_target": {
            "request_id": "req-submit",
            "wire_path": "body.jobId",
        },
        "resolved_target": {
            "step_id": "submit",
            "stored_path": "jobId",
            "wire_path": "body.jobId",
        },
        "reason": "label '页' contradicts field_evidence for jobId: observed=['任务编号']",
        "flow_version_before": 0,
        "flow_version_after": int(updated.meta["current_version"]),
    }]


def test_enum_binding_requires_exact_recorded_label_value_mapping():
    spec = _flow()
    spec.steps[0].params = [ParamField(path="query.type", key="请假类型", value="2")]
    spec.request_facts.field_evidence.append({
        "event_id": "evt-type",
        "request_id": "req-detail",
        "path": "query.type",
        "field_aliases": ["type"],
        "label": "请假类型",
        "required": False,
        "control_kind": "select",
    })
    spec.request_facts.option_sources.append({
        "kind": "page_enum_options",
        "options": {
            "请假类型": {
                "field_key": "请假类型",
                "field_aliases": ["type"],
                "dict_type": "oa_duty_leave_type",
                "mapping_complete": True,
                "options": [
                    {"label": "病假", "value": 1},
                    {"label": "事假", "value": 2},
                    {"label": "婚假", "value": 3},
                ],
            },
        },
    })

    with pytest.raises(ValueError, match="contradicts recorded dictionary"):
        apply_flow_edits(spec, [{
            "op": "set_param_enum",
            "step_id": "detail",
            "path": "query.type",
            "dictionary_source": "oa_duty_leave_type",
            "options": [
                {"label": "事假", "value": 1},
                {"label": "病假", "value": 2},
                {"label": "婚假", "value": 3},
            ],
            "reason": "错误的标签值映射",
            "evidence_refs": ["evt-type"],
        }])

    updated = apply_flow_edits(spec, [{
        "op": "set_param_enum",
        "step_id": "detail",
        "path": "query.type",
        "dictionary_source": "oa_duty_leave_type",
        "options": [
            {"label": "病假", "value": 1},
            {"label": "事假", "value": 2},
            {"label": "婚假", "value": 3},
        ],
        "reason": "页面字典与请求值一致",
        "evidence_refs": ["evt-type"],
    }])
    param = updated.steps[0].params[0]
    assert param.type == "enum"
    assert param.source_kind == "page_enum"
    assert param.enum_value_map == {"病假": 1, "事假": 2, "婚假": 3}


def test_enum_binding_accepts_complete_option_alias_when_control_binding_is_unbound():
    spec = _flow()
    spec.steps[0].params = [ParamField(
        path="query.type", key="type", value="3", wire_type="string",
    )]
    spec.request_facts.field_evidence.append({
        "event_id": "evt-query-type",
        "label": "请假类型",
        "field_aliases": [],
        "control_kind": "select",
        "binding_status": "unbound",
        "page_context": {"path": "/leave"},
    })
    spec.request_facts.option_sources.append({
        "kind": "page_enum_options",
        "options": {
            "请假类型": {
                "field_key": "请假类型",
                "field_aliases": ["type"],
                "dict_type": "oa_duty_leave_type",
                "control_kind": "select",
                "mapping_complete": True,
                "page_context": {"path": "/leave/create"},
                "options": [
                    {"label": "病假", "value": "1"},
                    {"label": "事假", "value": "2"},
                    {"label": "婚假", "value": "3"},
                ],
            },
        },
    })

    with pytest.raises(ValueError, match="no matching select field_evidence"):
        apply_flow_edits(spec, [{
            "op": "set_param_enum",
            "step_id": "detail",
            "path": "query.type",
            "dictionary_source": "unrelated_type",
            "options": [
                {"label": "病假", "value": 1},
                {"label": "事假", "value": 2},
                {"label": "婚假", "value": 3},
            ],
            "reason": "错误字典不能借用同名控件",
            "evidence_refs": ["evt-query-type"],
        }])

    updated = apply_flow_edits(spec, [{
        "op": "set_param_enum",
        "step_id": "detail",
        "path": "query.type",
        "dictionary_source": "oa_duty_leave_type",
        "options": [
            {"label": "病假", "value": 1},
            {"label": "事假", "value": 2},
            {"label": "婚假", "value": 3},
        ],
        "reason": "同一字典在筛选页和创建页复用",
        "evidence_refs": ["evt-query-type"],
    }])

    param = updated.steps[0].params[0]
    assert param.type == "enum"
    assert param.source["dictionary_source"] == "oa_duty_leave_type"
    assert param.enum_value_map == {"病假": "1", "事假": "2", "婚假": "3"}

    from dano.execution.page.flow_spec import validate_flow_spec

    updated.capabilities = [FlowCapability(
        name="query_records",
        kind="query",
        nodes=[{"id": "query", "type": "call", "step_id": "detail"}],
        confirmed=True,
    )]
    report_text = json.dumps(validate_flow_spec(updated), ensure_ascii=False)
    assert "capability_internal_field_exposed" not in report_text


@pytest.mark.asyncio
async def test_verified_enum_repair_refreshes_machine_owned_capability_contract():
    spec = _flow()
    spec.steps[0].params = [ParamField(
        path="query.type",
        key="type",
        value="2",
        type="enum",
        wire_type="number",
        category="user_param",
        source_kind="form_option",
        exposed_to_user=True,
    )]
    spec.request_facts.field_evidence.append({
        "event_id": "evt-type",
        "request_id": "req-detail",
        "path": "query.type",
        "field_aliases": ["type"],
        "label": "请假类型",
        "required": False,
        "control_kind": "select",
    })
    spec.request_facts.option_sources.append({
        "kind": "page_enum_options",
        "options": {
            "请假类型": {
                "field_key": "请假类型",
                "field_aliases": ["type"],
                "dict_type": "leave_type",
                "mapping_complete": True,
                "options": [
                    {"label": "病假", "value": 1},
                    {"label": "事假", "value": 2},
                ],
            },
        },
    })
    spec.capabilities = [FlowCapability(
        name="query_records",
        kind="query",
        nodes=[{"id": "query", "type": "call", "step_id": "detail"}],
        confidence=0.95,
    )]
    spec.meta = {**(spec.meta or {}), "verification_run": {"complete": True}}
    spec = _auto_confirm_ready_capabilities(spec)
    spec.meta["verification_run"] = {"complete": False}
    old_confirmation_hash = spec.capabilities[0].confirmation_hash

    updated = await apply_recording_agent_submission(spec, mode="repair", submission={
        "ops": [{
            "op": "set_param_enum",
            "step_id": "detail",
            "path": "query.type",
            "dictionary_source": "leave_type",
            "options": [
                {"label": "病假", "value": 1},
                {"label": "事假", "value": 2},
            ],
            "reason": "页面控件与字典快照一致",
            "evidence_refs": ["evt-type"],
        }],
    })

    op_result = updated.meta["recording_agent_session"]["op_results"][0]
    assert op_result["status"] == "applied", json.dumps(op_result, ensure_ascii=False)
    assert updated.steps[0].params[0].enum_value_map == {"病假": 1, "事假": 2}
    assert updated.capabilities[0].confirmation_hash != old_confirmation_hash
    after_validation = validate_flow_spec(updated)
    assert not any(
        "缺少可执行枚举选项" in item
        for item in after_validation["capability_validation"]["errors"]
    )


def test_finalize_replays_deferred_enum_from_complete_unbound_snapshot():
    finalized = _flow()
    finalized.steps[0].params = [ParamField(
        path="query.type", key="type", value="3", wire_type="string",
    )]
    finalized.request_facts.field_evidence.append({
        "event_id": "evt-query-type",
        "label": "请假类型",
        "field_aliases": [],
        "control_kind": "select",
        "binding_status": "unbound",
    })
    finalized.request_facts.option_sources.append({
        "kind": "page_enum_options",
        "options": {
            "请假类型": {
                "field_key": "请假类型",
                "field_aliases": ["type"],
                "dict_type": "leave_type",
                "mapping_complete": True,
                "options": [
                    {"label": "年假", "value": "1"},
                    {"label": "事假", "value": "2"},
                    {"label": "病假", "value": "3"},
                ],
            },
        },
    })
    live = FlowSpec(
        request_facts=finalized.request_facts.model_copy(deep=True),
        meta={"live_request_ids": ["req-detail"]},
    )
    live = apply_flow_edits(live, [{
        "op": "set_param_enum",
        "request_id": "req-detail",
        "wire_path": "query.type",
        "dictionary_source": "leave_type",
        "options": [
            {"label": "年假", "value": 1},
            {"label": "事假", "value": 2},
            {"label": "病假", "value": 3},
        ],
        "reason": "录制中的完整字典快照",
        "evidence_refs": ["evt-query-type"],
    }])

    merged = merge_live_agent_state(live, finalized)

    param = merged.steps[0].params[0]
    assert param.type == "enum"
    assert param.enum_value_map == {"年假": "1", "事假": "2", "病假": "3"}
    assert "unresolved_live_agent_ops" not in merged.meta


def test_recording_delta_preserves_action_kind_and_does_not_repeat_unrelated_events():
    request = {
        "request_id": "req-search",
        "sequence": 1,
        "method": "POST",
        "url": "https://x/api/search",
        "post_data": {"keyword": "合同"},
        "role": "business_get",
        "keep": True,
        "trigger_action_id": "action-search",
        "trigger_transaction_id": "txn-search",
        "trigger_op": "click",
        "trigger_locator": "button[name=查询]",
    }
    events = [{"event_id": "unrelated", "action_id": "other", "op": "fill"}]

    first = recording_delta(
        None,
        since_seq=0,
        captured_requests=[request],
        page_events=events,
    )
    exhausted = recording_delta(
        None,
        since_seq=1,
        captured_requests=[request],
        page_events=events,
    )

    assert first["requests"][0]["trigger_op"] == "click"
    assert first["requests"][0]["trigger_locator"] == "button[name=查询]"
    assert exhausted["page_events"] == []


def test_recording_agent_state_keeps_late_business_request_visible():
    spec = _flow()
    spec.steps = []
    spec.capabilities = []
    spec.request_facts = RequestFacts(
        requests=[
            RequestFact(
                request_id=f"req-{index}",
                request_index=index,
                sequence=index,
                method="POST" if index == 149 else "GET",
                path="/api/final-command" if index == 149 else f"/assets/context/{index}",
            )
            for index in range(150)
        ],
        analysis={
            **{
                f"req-{index}": RequestAnalysis(
                    request_id=f"req-{index}", role="read_context", keep=False,
                    reason="background context", confidence=0.5,
                )
                for index in range(149)
            },
            "req-149": RequestAnalysis(
                request_id="req-149", role="business_write", keep=True,
                reason="final command", confidence=0.95,
            ),
        },
    )

    state = recording_agent_state(spec)
    visible = {
        item.get("request_id")
        for item in state["facts"]["captured_requests"]
        if isinstance(item, dict)
    }

    assert "req-149" in visible


def test_field_grounding_still_rejects_unrecorded_refs():
    with pytest.raises(ValueError, match="evidence_refs must cite recorded facts"):
        apply_flow_edits(_flow(), [{
            "op": "rename_field",
            "step_id": "submit",
            "path": "jobId",
            "label": "任务编号",
            "reason": "臆造名称",
            "evidence_refs": ["猜测"],
        }])


def test_live_field_operation_rejects_response_path_and_reports_request_paths():
    spec = FlowSpec(request_facts=RequestFacts(requests=[RequestFact(
        request_id="req-query",
        method="GET",
        path="/records",
        query={"status": ["1"]},
        query_paths=["query.status"],
    )]))

    with pytest.raises(ValueError, match=r"response path.*query\.status"):
        apply_flow_edits(spec, [{
            "op": "set_param_enum",
            "request_id": "req-query",
            "wire_path": "response.data.list[].status",
            "dictionary_source": "record_status",
            "options": [
                {"label": "待处理", "value": 1},
                {"label": "已完成", "value": 2},
            ],
            "reason": "错误地把响应字段当请求字段",
            "evidence_refs": ["req-query"],
        }])


def test_set_goal_merges_axes_instead_of_wiping_them():
    spec = _flow()
    spec.goal = {
        "intent": "旧意图",
        "forbidden_actions": ["调用当前录制范围外的接口"],
        "success_criteria": ["读回一致"],
    }
    updated = apply_flow_edits(spec, [{
        "op": "set_goal",
        "goal": {
            "intent": "更新指定记录",
            "forbidden_actions": [],
            "evidence": [{"source": "goal_text"}],
        },
    }])
    assert updated.goal["intent"] == "更新指定记录"
    assert updated.goal["forbidden_actions"] == ["调用当前录制范围外的接口"]
    assert updated.goal["success_criteria"] == ["读回一致"]


def test_ensure_recorded_goal_fills_empty_axes_for_publish_gate():
    from dano.execution.page.flow_spec import ensure_recorded_goal

    spec = _flow()
    spec.goal = {
        "intent": "更新指定记录",
        "forbidden_actions": [],
        "success_criteria": [],
        "output_expectation": [],
        "evidence": [{"source": "goal_text"}],
    }
    ensured = ensure_recorded_goal(spec)
    assert ensured.goal["forbidden_actions"]
    assert ensured.goal["success_criteria"]


def test_structure_dependency_targets_container_and_stays_out_of_value_injection():
    spec = _flow()
    spec.steps[0].response_json = {
        "data": {"activityNodes": [{"id": "Activity_09dlq0g"}]},
    }
    spec.steps[1].params.append(ParamField(
        path="startUserSelectAssignees.Activity_09dlq0g[0]",
        key="审批人",
        value=160,
    ))
    updated = apply_flow_edits(spec, [{
        "op": "propose_dependency",
        "kind": "structure",
        "source_request_id": "req-detail",
        "source_path": "data.activityNodes[*].id",
        "target_request_id": "req-submit",
        "target_path": "startUserSelectAssignees",
        "reason": "审批节点 ID 决定 assignees 的键结构",
        "evidence": {"observed_keys": ["Activity_09dlq0g", "Activity_0ag2wyz"]},
    }])
    link = updated.links[0]
    assert link.kind == "structure"
    assert "kind" not in link.meta
    assert link.confirmed is False

    updated.steps[1].body_source = json.dumps({
        "startUserSelectAssignees": {"Activity_09dlq0g": [160]},
    })
    api_request, errors = flow_spec_to_api_request(updated)
    assert errors == []
    assert api_request is not None
    assert api_request["steps"][1]["structure_links"] == [{
        "link_id": link.link_id,
        "target_path": "startUserSelectAssignees",
        "target_tokens": None,
        "source_step": 0,
        "source_path": "data.activityNodes[*].id",
        "source_tokens": None,
        "mode": "response_keys",
    }]

    with pytest.raises(ValueError, match="does not match any recorded param"):
        apply_flow_edits(spec, [{
            "op": "propose_dependency",
            "kind": "structure",
            "source_request_id": "req-detail",
            "source_path": "data.activityNodes[*].id",
            "target_request_id": "req-submit",
            "target_path": "noSuchContainer",
            "reason": "容器不存在",
            "evidence": {"observed_keys": ["Activity_09dlq0g"]},
        }])


@pytest.mark.asyncio
async def test_response_key_map_exposes_stable_label_map_and_uses_latest_node_ids():
    from dano.execution.page.flow_spec import _capability_input_schema
    from dano.execution.page.request_capture import execute_api_workflow

    spec = _flow()
    spec.steps[0].response_json = {
        "data": {"activityNodes": [
            {"id": "Event_start", "name": "发起人"},
            {"id": "Activity_recorded_leader", "name": "领导审批"},
            {"id": "Activity_recorded_hr", "name": "HR审批"},
            {"id": "Event_end", "name": "结束"},
        ]},
    }
    spec.steps[1].body_source = json.dumps({
        "jobId": "J1",
        "startUserSelectAssignees": {
            "Activity_recorded_leader": [160, 161],
            "Activity_recorded_hr": [159],
        },
    })
    spec.steps[1].params.extend([
        ParamField(
            path="startUserSelectAssignees.Activity_recorded_leader[0]",
            key="领导审批人", value=160,
        ),
        ParamField(
            path="startUserSelectAssignees.Activity_recorded_hr[0]",
            key="HR审批人", value=159,
        ),
    ])
    dependency_op = {
        "op": "propose_dependency",
        "kind": "response_key_map",
        "source_request_id": "req-detail",
        "source_collection_path": "data.activityNodes",
        "source_key_path": "id",
        "source_label_path": "name",
        "target_request_id": "req-submit",
        "target_container_path": "body.startUserSelectAssignees",
        "value_binding": {
            "kind": "caller_map_by_label",
            "input_field": "approvers",
            "option_source": {
                "capability": "list_approval_users",
                "value_path": "id",
                "label_path": "nickname",
            },
        },
        "reason": "最新审批节点的名称决定调用方映射，节点 ID 决定请求键",
        "evidence": {"request_ids": ["req-detail", "req-submit"]},
    }
    spec.capabilities = [FlowCapability(
        name="submit_item",
        kind="submit",
        nodes=[
            {"id": "read_detail", "type": "call", "step_id": "detail"},
            {"id": "write_item", "type": "call", "step_id": "submit"},
        ],
        confidence=0.95,
    )]
    spec.meta = {**(spec.meta or {}), "verification_run": {"complete": True}}
    spec = _auto_confirm_ready_capabilities(spec)
    spec.meta["verification_run"] = {"complete": False}

    updated = await apply_recording_agent_submission(
        spec,
        mode="repair",
        submission={"ops": [dependency_op]},
    )

    op_result = updated.meta["recording_agent_session"]["op_results"][0]
    assert op_result["status"] == "applied", json.dumps(op_result, ensure_ascii=False)

    from dano.execution.page.recording_live import recording_agent_evidence_issues

    assert recording_agent_evidence_issues(updated) == []

    link = updated.links[0]
    assert link.kind == "response_key_map"
    assert link.source_collection_path == "data.activityNodes"
    assert link.target_container_path == "startUserSelectAssignees"
    assert link.value_binding["required_labels"] == ["领导审批", "HR审批"]
    assert link.value_binding["ignored_labels"] == ["发起人", "结束"]
    public = next(param for param in updated.steps[1].params if param.key == "approvers")
    assert public.value == {"领导审批": [160, 161], "HR审批": [159]}
    assert public.type == "object"
    assert all(
        not param.exposed_to_user
        for param in updated.steps[1].params
        if "Activity_recorded" in param.path
    )

    updated = apply_flow_edits(updated, [{
        "op": "set_param_source",
        "request_id": "req-submit",
        "wire_path": "body.startUserSelectAssignees",
        "source_kind": "response_binding",
        "origin_request_id": "req-detail",
        "origin_path": "response.data.activityNodes",
        "reason": "较弱的后续结论只看到了节点响应",
    }])
    public = next(param for param in updated.steps[1].params if param.key == "approvers")
    assert public.source["kind"] == "dynamic_structure_input"
    assert public.exposed_to_user is True

    # Existing drafts created before response_key_map reasons were persisted
    # remain valid only while their matching dynamic contract still exists.
    legacy = updated.model_copy(deep=True)
    legacy_public = next(param for param in legacy.steps[1].params if param.key == "approvers")
    next(item for item in legacy_public.evidence if item.get("source") == "response_key_map").pop("reason")
    assert recording_agent_evidence_issues(legacy) == []
    legacy.links = []
    assert any(item["kind"] == "param_source" for item in recording_agent_evidence_issues(legacy))

    schema = _capability_input_schema([public])
    assert schema["properties"]["approvers"]["x-dano-option-source"] == {
        "capability": "list_approval_users",
        "value_path": "id",
        "label_path": "nickname",
    }

    api_request, errors = flow_spec_to_api_request(updated)
    assert errors == []
    assert api_request is not None
    assert api_request["steps"][1]["sample_inputs"]["approvers"] == {
        "领导审批": [160, 161],
        "HR审批": [159],
    }
    api_request["steps"][0]["response_json"] = {
        "data": {"activityNodes": [
            {"id": "Event_runtime_start", "name": "发起人"},
            {"id": "Activity_runtime_leader", "name": "领导审批"},
            {"id": "Activity_runtime_hr", "name": "HR审批"},
            {"id": "Event_runtime_end", "name": "结束"},
        ]},
    }
    out = await execute_api_workflow(api_request, {
        "approvers": {"领导审批": [200, 202], "HR审批": [201]},
    }, send=False)
    assert out["ok"] is True
    assert out["final"]["body"]["startUserSelectAssignees"] == {
        "Activity_runtime_leader": [200, 202],
        "Activity_runtime_hr": [201],
    }
    assert "Activity_recorded" not in json.dumps(out["final"]["body"], ensure_ascii=False)

    rejected = await execute_api_workflow(api_request, {
        "approvers": {"领导审批": [200]},
    }, send=False)
    assert rejected["ok"] is False
    assert any("审批节点与调用方输入不一致" in issue for issue in rejected["step_result"]["self_check"])


def test_wire_format_is_inferred_and_survives_model_sync():
    from dano.execution.page.flow_spec import (
        _capability_input_schema,
        _infer_wire_format,
        sync_flow_spec_models,
    )

    assert _infer_wire_format(1785945600000) == "epoch_ms"
    assert _infer_wire_format("1785945600000") == "epoch_ms"
    assert _infer_wire_format(1785945600) == "epoch_s"
    assert _infer_wire_format("2026-08-06 00:00:00") == "datetime_text"
    assert _infer_wire_format("2026-08-06") == "date_text"
    assert _infer_wire_format(160) == ""
    assert _infer_wire_format(True) == ""

    spec = _flow()
    spec.steps[1].params.append(ParamField(
        path="startTime", key="开始时间", value=1785945600000, type="datetime",
    ))
    synced = sync_flow_spec_models(spec)
    param = next(item for item in synced.steps[1].params if item.path == "startTime")
    assert param.wire_format == "epoch_ms"
    input_schema = _capability_input_schema([param])
    assert input_schema["properties"]["开始时间"]["x-dano-wire-format"] == "epoch_ms"


@pytest.mark.asyncio
async def test_wire_format_is_executed_and_invalid_input_fails_before_request(monkeypatch):
    from dano.execution.page.request_capture import execute_api_request
    from dano.execution.page.wire_format import WireFormatError

    calls = 0

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("invalid wire input must fail before HTTP")

    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: Client())
    api_request = {
        "method": "POST",
        "url": "https://example.test/submit",
        "body_template": {"startTime": "{{startTime}}"},
        "params": ["startTime"],
        "field_types": {"startTime": "datetime"},
        "wire_formats": {"startTime": "epoch_ms"},
        "sample_inputs": {"startTime": 1785945600000},
    }

    dry = await execute_api_request(api_request, {"startTime": "2026-08-06T00:00:00+08:00"}, send=False)
    assert dry["body"]["startTime"] == 1785945600000
    with pytest.raises(WireFormatError, match="invalid date/time input"):
        await execute_api_request(api_request, {"startTime": "not-a-date"}, send=True)
    assert calls == 0




def test_agent_required_conclusion_survives_query_audit():
    from dano.execution.page.flow_spec import sync_flow_spec_models

    spec = _flow()
    spec.steps[0].params = [ParamField(path="query.type", key="type", value="1")]
    spec.request_facts.field_evidence.append({
        "event_id": "evt-type-required",
        "request_id": "req-detail",
        "path": "query.type",
        "field_aliases": ["type"],
        "label": "类型",
        "required": True,
    })
    updated = apply_flow_edits(spec, [{
        "op": "set_param_required",
        "step_id": "detail",
        "path": "query.type",
        "required": True,
        "reason": "页面 required 标记",
        "evidence_refs": ["evt-type-required"],
    }])
    synced = sync_flow_spec_models(updated)
    assert synced.steps[0].params[0].required is True


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
    assert merged.request_facts.analysis["req-submit"].role == "business_write"
    assert len(merged.links) == 1
    assert merged.links[0].source_step_id == "detail"
    assert merged.links[0].target_step_id == "submit"


def test_finalize_merge_materializes_capabilities_from_live_goal_and_request_roles():
    live = FlowSpec(
        flow_id="live-boundaries",
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-query", method="GET", path="/records/page"),
            RequestFact(request_id="req-submit", method="POST", path="/records/submit"),
            RequestFact(request_id="req-withdraw", method="DELETE", path="/records/withdraw"),
            RequestFact(request_id="req-delete", method="DELETE", path="/records/delete"),
        ]),
    )
    live = apply_flow_edits(live, [
        {
            "op": "set_goal",
            "goal": {
                "intent": "查询、提交、撤回和删除记录",
                "capabilities": [
                    "query_records", "submit_record", "withdraw_record", "delete_record",
                ],
                "required_inputs": [],
                "success_criteria": ["能够查询、提交、撤回和删除"],
                "evidence": [{"source": "recording"}],
            },
        },
        {
            "op": "set_request_role",
            "request_id": "req-query",
            "role": "business_read",
            "reason": "页面查询动作返回业务记录",
            "evidence_refs": ["request:req-query"],
        },
        {
            "op": "set_request_role",
            "request_id": "req-submit",
            "role": "business_write",
            "reason": "页面提交动作写入业务记录",
            "evidence_refs": ["request:req-submit"],
        },
        {
            "op": "set_request_role",
            "request_id": "req-withdraw",
            "role": "business_write",
            "reason": "页面撤回动作修改业务记录",
            "evidence_refs": ["request:req-withdraw"],
        },
        {
            "op": "set_request_role",
            "request_id": "req-delete",
            "role": "business_write",
            "reason": "页面删除动作移除业务记录",
            "evidence_refs": ["request:req-delete"],
        },
    ])
    finalized = FlowSpec(
        flow_id="materialized-boundaries",
        title="记录",
        steps=[
            FlowStep(
                step_id="query-step",
                method="GET",
                path="/records/page",
                source_meta={"request_id": "req-query", "request_index": 1},
                response_json={"data": {"list": []}},
            ),
            FlowStep(
                step_id="submit-step",
                method="POST",
                path="/records/submit",
                source_meta={"request_id": "req-submit", "request_index": 2},
            ),
            FlowStep(
                step_id="withdraw-step",
                method="DELETE",
                path="/records/withdraw",
                source_meta={"request_id": "req-withdraw", "request_index": 3},
            ),
            FlowStep(
                step_id="delete-step",
                method="DELETE",
                path="/records/delete",
                source_meta={"request_id": "req-delete", "request_index": 4},
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(
                request_id="req-query", request_index=1,
                method="GET", path="/records/page",
                response_json={"data": {"list": []}},
            ),
            RequestFact(
                request_id="req-submit", request_index=2,
                method="POST", path="/records/submit",
            ),
            RequestFact(
                request_id="req-withdraw", request_index=3,
                method="DELETE", path="/records/withdraw",
            ),
            RequestFact(
                request_id="req-delete", request_index=4,
                method="DELETE", path="/records/delete",
            ),
        ]),
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.name for capability in merged.capabilities] == [
        "query_records", "submit_record", "withdraw_record", "delete_record",
    ]
    assert [capability.kind for capability in merged.capabilities] == [
        "query_status", "submit", "withdraw", "delete",
    ]
    assert [
        next(
            ref.step_id for ref in capability.request_refs
            if ref.usage == "execute"
        )
        for capability in merged.capabilities
    ] == ["query-step", "submit-step", "withdraw-step", "delete-step"]


def test_finalize_merge_obeys_explicit_recording_goal_capability_count():
    live = FlowSpec(
        flow_id="goal-boundaries",
        meta={
            "recording_goal_text": (
                "目的：查询并提交记录\n"
                "预期产出能力数量：2\n"
                "能力1：查询记录\n"
                "能力2：提交记录"
            ),
        },
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-query", method="GET", path="/records/page"),
            RequestFact(request_id="req-submit", method="POST", path="/records/submit"),
            RequestFact(request_id="req-delete", method="DELETE", path="/records/delete"),
        ]),
    )
    live = apply_flow_edits(live, [
        {
            "op": "set_goal",
            "goal": {
                "intent": "查询、提交和删除记录",
                "capabilities": ["query_records", "submit_record", "delete_record"],
                "success_criteria": ["完成目标操作"],
                "evidence": [{"source": "goal_text"}],
            },
        },
        *[
            {
                "op": "set_request_role",
                "request_id": request_id,
                "role": role,
                "reason": "录制动作对应公开业务操作",
                "evidence_refs": [f"request:{request_id}"],
            }
            for request_id, role in (
                ("req-query", "business_read"),
                ("req-submit", "business_write"),
                ("req-delete", "business_write"),
            )
        ],
    ])
    live.meta["capability_model"] = {
        "status": "ready",
        "semantic_plan": {
            "business_understanding": {"business_name": "记录"},
            "capabilities": [
                {
                    "name": name,
                    "title": name,
                    "intent": name,
                    "kind": kind,
                    "anchor_step_id": request_id,
                    "request_refs": [{"step_id": request_id, "usage": "execute"}],
                }
                for name, kind, request_id in (
                    ("query_records", "query_status", "req-query"),
                    ("submit_record", "submit", "req-submit"),
                    ("delete_record", "delete", "req-delete"),
                )
            ],
            "unresolved_items": [],
        },
    }
    finalized = FlowSpec(
        flow_id="goal-boundaries-final",
        title="记录",
        steps=[
            FlowStep(
                step_id="query-step", method="GET", path="/records/page",
                source_meta={"request_id": "req-query", "request_index": 1},
                response_json={"data": {"list": []}},
            ),
            FlowStep(
                step_id="submit-step", method="POST", path="/records/submit",
                source_meta={"request_id": "req-submit", "request_index": 2},
            ),
            FlowStep(
                step_id="delete-step", method="DELETE", path="/records/delete",
                source_meta={"request_id": "req-delete", "request_index": 3},
            ),
        ],
        request_facts=live.request_facts,
    )

    merged = merge_live_agent_state(live, finalized)

    assert len(merged.capabilities) == 2
    assert [capability.kind for capability in merged.capabilities] == ["query_status", "submit"]
    assert [capability.title for capability in merged.capabilities] == ["查询记录", "提交记录"]
    assert [item["name"] for item in merged.meta["recording_goal_contract"]["capabilities"]] == [
        "查询记录", "提交记录",
    ]


def test_goal_boundary_does_not_supplement_a_stale_live_plan():
    live = FlowSpec(
        flow_id="goal-missing-live-capability",
        meta={
            "recording_goal_text": (
                "目的：查询并提交记录\n"
                "预期产出能力数量：2\n"
                "能力1：查询记录\n"
                "能力2：提交记录"
            ),
            "capability_model": {
                "status": "ready",
                "semantic_plan": {
                    "business_understanding": {"business_name": "记录"},
                    "capabilities": [{
                        "name": "submit_record",
                        "title": "提交记录",
                        "intent": "提交记录",
                        "kind": "submit",
                        "anchor_step_id": "req-submit",
                        "request_refs": [{"step_id": "req-submit", "usage": "execute"}],
                    }],
                    "unresolved_items": [],
                },
            },
        },
    )
    finalized = FlowSpec(
        flow_id="goal-missing-live-capability-final",
        title="记录",
        steps=[
            FlowStep(
                step_id="query-step", method="GET", path="/records/page",
                source_meta={"request_id": "req-query", "request_index": 1},
                response_json={"data": {"list": []}},
            ),
            FlowStep(
                step_id="submit-step", method="POST", path="/records/submit",
                source_meta={"request_id": "req-submit", "request_index": 2},
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(
                request_id="req-query", request_index=1,
                method="GET", path="/records/page",
                response_json={"data": {"list": []}},
            ),
            RequestFact(
                request_id="req-submit", request_index=2,
                method="POST", path="/records/submit",
            ),
        ]),
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.kind for capability in merged.capabilities] == ["submit"]
    assert merged.meta["recording_goal_contract"]["satisfied"] is False
    assert not any(
        item.get("op") == "enforce_recording_goal"
        for item in merged.meta.get("unresolved_live_agent_ops", [])
    )


def test_finalize_never_invents_missing_capabilities_from_goal_slots() -> None:
    live = FlowSpec(meta={
        "recording_goal_text": (
            "预期产出能力数量：3\n"
            "能力1：查询记录\n"
            "能力2：提交记录\n"
            "能力3：删除记录"
        ),
        "capability_model": {
            "status": "ready",
            "semantic_plan": {
                "business_understanding": {"business_name": "记录"},
                "capabilities": [{
                    "name": "query_records",
                    "title": "查询记录",
                    "intent": "查询记录",
                    "kind": "query_status",
                    "anchor_step_id": "req-query",
                    "request_refs": [{"step_id": "req-query", "usage": "execute"}],
                }],
                "unresolved_items": [{
                    "type": "capability_anchor",
                    "title": "其余动作尚未完成分析",
                }],
            },
        },
    })
    finalized = FlowSpec(
        title="记录",
        steps=[
            FlowStep(
                step_id="query-step", method="GET", path="/records/page",
                source_meta={"request_id": "req-query", "role": "business_get"},
            ),
            FlowStep(
                step_id="submit-step", method="POST", path="/records/submit",
                source_meta={"request_id": "req-submit", "role": "business_write"},
            ),
            FlowStep(
                step_id="delete-step", method="DELETE", path="/records/delete",
                source_meta={"request_id": "req-delete", "role": "business_write"},
            ),
        ],
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.title for capability in merged.capabilities] == ["查询记录"]
    assert merged.meta["recording_goal_contract"]["materialized_count"] == 1
    assert merged.meta["recording_goal_contract"]["satisfied"] is False


def test_finalized_leave_shape_keeps_all_eight_recorded_business_actions_distinct() -> None:
    titles_and_contracts = [
        ("query_leave", "查询请假申请", "query_status", "req-query"),
        ("save_leave_draft", "保存请假草稿", "save_draft", "req-draft"),
        ("submit_leave", "提交请假申请", "submit", "req-submit"),
        ("inspect_leave", "查看申请详情", "inspect", "req-detail"),
        ("edit_save_leave", "编辑草稿,草稿保存", "update", "req-edit-save"),
        ("withdraw_leave", "撤回请假申请", "withdraw", "req-withdraw"),
        ("delete_leave", "删除请假申请", "delete", "req-delete"),
        ("edit_submit_leave", "编辑草稿,提交保存", "update", "req-edit-submit"),
    ]
    goal_text = "\n".join([
        "预期产出能力数量：8",
        *(f"能力{index}：{title}" for index, (_name, title, _kind, _request) in enumerate(
            titles_and_contracts, start=1,
        )),
    ])
    live = FlowSpec(meta={
        "recording_goal_text": goal_text,
        "capability_model": {
            "status": "ready",
            "semantic_plan": {
                "business_understanding": {"business_name": "请假申请"},
                "capabilities": [
                    {
                        "name": name,
                        "title": title,
                        "kind": kind,
                        "anchor_step_id": request_id,
                        "request_refs": [{"step_id": request_id, "usage": "execute"}],
                    }
                    for name, title, kind, request_id in titles_and_contracts
                ],
                "unresolved_items": [],
            },
        },
    })

    def action_meta(request_id: str, action: str, index: int, *, role: str) -> dict:
        return {
            "request_id": request_id,
            "request_index": index,
            "sequence": index,
            "role": role,
            "trigger_op": "click",
            "trigger_locator": f"text={action}",
            "trigger_transaction_id": f"txn-{request_id}",
            "causality_confidence": "high",
        }

    caller_reason = ParamField(
        path="reason", key="reason", label="请假原因", value="修改后的原因",
        category="user_param", source_kind="previous_response",
        source={
            "kind": "previous_response", "step_id": "detail-step",
            "response_path": "data.reason", "allow_caller_override": True,
        },
        required=True, editable=True, exposed_to_user=True,
        evidence=[{
            "kind": "page_control", "control_kind": "text",
            "editable": True, "interacted": True,
        }],
    )
    internal_status = ParamField(
        path="processStatus", key="processStatus", label="流程状态", value=0,
        category="runtime_var", source_kind="previous_response",
        source={"kind": "previous_response", "step_id": "detail-step", "response_path": "data.processStatus"},
        required=False, editable=False, exposed_to_user=False,
    )
    steps = [
        FlowStep(
            step_id="query-step", method="GET", path="/admin-api/oa/duty-leave/page",
            response_json={"data": {"list": [{"id": 42}]}},
            source_meta=action_meta("req-query", "搜索", 1, role="business_get"),
        ),
        FlowStep(
            step_id="draft-step", method="POST", path="/admin-api/oa/duty-leave/create",
            source_meta=action_meta("req-draft", "保存草稿", 2, role="business_write"),
        ),
        FlowStep(
            step_id="submit-step", method="POST", path="/admin-api/oa/duty-leave/submit-process",
            source_meta=action_meta("req-submit", "提交", 3, role="business_write"),
        ),
        FlowStep(
            step_id="detail-step", method="GET", path="/admin-api/oa/duty-leave/get?id=42",
            params=[ParamField(path="query.id", key="id", value=42)],
            response_json={"data": {"id": 42, "reason": "原原因", "processStatus": 0}},
            source_meta=action_meta("req-detail", "查看详情", 4, role="business_get"),
        ),
        FlowStep(
            step_id="edit-save-step", method="POST", path="/admin-api/oa/duty-leave/update",
            params=[
                ParamField(path="id", key="id", value=42, exposed_to_user=False),
                caller_reason.model_copy(deep=True),
                internal_status.model_copy(deep=True),
            ],
            source_meta={
                **action_meta("req-edit-save", "保存草稿", 5, role="business_write"),
                "trigger_page_context": {"url": "https://example.test/leave/edit?id=42"},
            },
        ),
        FlowStep(
            step_id="withdraw-step", method="DELETE", path="/admin-api/bpm/process-instance/cancel-by-start-user",
            params=[ParamField(path="id", key="记录ID", label="记录ID", value=42, required=True)],
            source_meta=action_meta("req-withdraw", "撤回", 6, role="business_write"),
        ),
        FlowStep(
            step_id="delete-step", method="DELETE", path="/admin-api/oa/duty-leave/delete?id=42",
            params=[ParamField(path="query.id", key="记录ID", label="记录ID", value=42, required=True)],
            source_meta=action_meta("req-delete", "删除", 7, role="business_write"),
        ),
        FlowStep(
            step_id="edit-detail-step", method="GET", path="/admin-api/oa/duty-leave/get?id=43",
            params=[ParamField(path="query.id", key="id", value=43)],
            response_json={"data": {"id": 43, "reason": "旧原因", "processStatus": 0}},
            source_meta={"request_id": "req-edit-detail", "request_index": 8, "sequence": 8, "role": "read_context"},
        ),
        FlowStep(
            step_id="edit-submit-step", method="POST", path="/admin-api/oa/duty-leave/submit-process",
            params=[
                ParamField(path="id", key="id", value=43, exposed_to_user=False),
                caller_reason.model_copy(deep=True, update={
                    "source": {
                        "kind": "previous_response", "step_id": "edit-detail-step",
                        "response_path": "data.reason", "allow_caller_override": True,
                    },
                }),
            ],
            source_meta={
                **action_meta("req-edit-submit", "提交", 9, role="business_write"),
                "trigger_page_context": {"url": "https://example.test/leave/edit?id=43"},
            },
        ),
    ]
    finalized = FlowSpec(
        title="请假申请",
        meta={"recording_goal_text": goal_text},
        steps=steps,
        links=[
            FlowLink(
                source_step_id="detail-step", source_path="data.reason",
                target_step_id="edit-save-step", target_path="reason",
                confirmed=True, confidence=0.99,
                evidence={"kind": "record_hydration", "match_count": 3, "identity_paths": ["data.id"]},
            ),
            FlowLink(
                source_step_id="edit-detail-step", source_path="data.reason",
                target_step_id="edit-submit-step", target_path="reason",
                confirmed=True, confidence=0.99,
                evidence={"kind": "record_hydration", "match_count": 3, "identity_paths": ["data.id"]},
            ),
        ],
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.title for capability in merged.capabilities] == [
        title for _name, title, _kind, _request in titles_and_contracts
    ]
    assert [
        next(ref.step_id for ref in capability.request_refs if ref.usage == "execute")
        for capability in merged.capabilities
    ] == [
        "query-step", "draft-step", "submit-step", "detail-step",
        "edit-save-step", "withdraw-step", "delete-step", "edit-submit-step",
    ]
    assert merged.meta["recording_goal_contract"]["satisfied"] is True
    assert len({
        next(ref.step_id for ref in capability.request_refs if ref.usage == "execute")
        for capability in merged.capabilities
    }) == 8

    edit_save = next(step for step in merged.steps if step.step_id == "edit-save-step")
    by_path = {param.path: param for param in edit_save.params}
    assert (by_path["reason"].label, by_path["reason"].required) == ("请假原因", True)
    assert by_path["reason"].source_kind == "previous_response"
    assert by_path["reason"].source["allow_caller_override"] is True
    assert by_path["reason"].exposed_to_user is True
    assert by_path["processStatus"].exposed_to_user is False


def test_goal_boundary_keeps_strong_filtered_query_outside_write_preflight() -> None:
    live = FlowSpec(meta={
        "recording_goal_text": (
            "目的：查询并提交记录\n"
            "预期产出能力数量：2\n"
            "能力1：查询记录\n"
            "能力2：提交记录"
        ),
    })
    finalized = FlowSpec(
        title="记录",
        steps=[
            FlowStep(
                step_id="filtered-query", method="GET",
                path="/records/page?pageNo=1&status=active&reason=test",
                response_json={"data": {"list": [{"id": "record-1"}], "total": 1}},
                source_meta={
                    "request_id": "req-query", "role": "read_context",
                    "trigger_op": "control_open", "trigger_locator": "label=状态",
                    "trigger_transaction_id": "txn-filter",
                    "control_preflight_for_write": True,
                    "causality_confidence": "high",
                },
            ),
            FlowStep(
                step_id="submit", method="POST", path="/records/submit",
                source_meta={
                    "request_id": "req-submit", "role": "business_write",
                    "trigger_op": "click", "trigger_locator": "text=提交",
                    "trigger_transaction_id": "txn-submit",
                },
            ),
        ],
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.title for capability in merged.capabilities] == [
        "查询记录", "提交记录",
    ]
    assert [capability.kind for capability in merged.capabilities] == [
        "query_status", "submit",
    ]
    assert merged.meta["recording_goal_contract"]["satisfied"] is True


def test_one_read_command_returns_entity_result_without_parallel_context() -> None:
    live = FlowSpec(meta={
        "recording_goal_text": (
            "目的：查看记录详情\n"
            "预期产出能力数量：1\n"
            "能力1：查看记录详情"
        ),
    })
    shared_meta = {
        "role": "business_get", "trigger_op": "click",
        "trigger_locator": "text=查看", "trigger_transaction_id": "txn-view",
        "causality_confidence": "high",
        "trigger_page_context": {"url": "https://example.test/records"},
    }
    finalized = FlowSpec(
        title="记录",
        steps=[
            FlowStep(
                step_id="comments", method="GET", path="/workflow/model?id=record-1",
                params=[ParamField(path="query.id", key="id", value="record-1")],
                response_json={"data": {"id": "record-1", "nodes": []}},
                source_meta={
                    **shared_meta, "request_id": "req-comments", "role": "read_context",
                },
            ),
            FlowStep(
                step_id="entity", method="GET", path="/records/get?id=record-1",
                params=[ParamField(path="query.id", key="id", value="record-1")],
                response_json={"data": {"id": "record-1", "reason": "captured"}},
                source_meta={**shared_meta, "request_id": "req-entity"},
            ),
        ],
    )

    merged = merge_live_agent_state(live, finalized)

    assert len(merged.capabilities) == 1
    capability = merged.capabilities[0]
    assert next(
        ref.step_id for ref in capability.request_refs if ref.usage == "execute"
    ) == "entity"
    assert capability.step_ids == ["entity"]
    assert [(ref.step_id, ref.usage) for ref in capability.request_refs] == [
        ("entity", "execute"),
    ]


def test_numbered_recording_goal_does_not_invent_omitted_live_boundaries():
    goal_text = (
        "目的：管理记录\n"
        "预期产出能力数量：4\n"
        "1\t查询记录\t按条件查询记录\n"
        "2\t保存记录草稿\t新建并保存草稿\n"
        "3\t撤回记录\t撤回正在处理的记录\n"
        "4\t删除记录\t删除允许删除的记录"
    )
    live = FlowSpec(
        meta={
            "recording_goal_text": goal_text,
            "capability_model": {
                "status": "ready",
                "semantic_plan": {
                    "business_understanding": {"business_name": "记录"},
                    "capabilities": [{
                        "name": "query_status",
                        "title": "query_status",
                        "intent": "query_status",
                        "kind": "query_status",
                        "anchor_step_id": "req-query",
                        "request_refs": [{
                            "step_id": "req-query",
                            "usage": "execute",
                        }],
                    }],
                    "unresolved_items": [],
                },
            },
        },
    )
    finalized = FlowSpec(
        title="记录",
        goal={"capabilities": ["query_status"]},
        steps=[
            FlowStep(
                step_id="query-step", method="GET", path="/records/page",
                source_meta={
                    "request_id": "req-query", "role": "business_get",
                    "trigger_op": "click", "trigger_locator": "text=查询",
                    "trigger_transaction_id": "txn-query",
                },
            ),
            FlowStep(
                step_id="draft-step", method="POST", path="/records/create",
                source_meta={
                    "request_id": "req-draft", "role": "business_write",
                    "trigger_op": "submit", "trigger_locator": "text=保存草稿",
                    "trigger_transaction_id": "txn-draft",
                },
            ),
            FlowStep(
                step_id="withdraw-step", method="DELETE", path="/records/withdraw",
                source_meta={
                    "request_id": "req-withdraw", "role": "business_write",
                    "trigger_op": "click", "trigger_locator": "text=撤回",
                    "trigger_transaction_id": "txn-withdraw",
                },
            ),
            FlowStep(
                step_id="delete-step", method="DELETE", path="/records/delete",
                source_meta={
                    "request_id": "req-delete", "role": "business_write",
                    "trigger_op": "click", "trigger_locator": "text=删除",
                    "trigger_transaction_id": "txn-delete",
                },
            ),
        ],
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.title for capability in merged.capabilities] == ["查询记录"]
    assert [capability.kind for capability in merged.capabilities] == ["query_status"]
    assert merged.meta["recording_goal_contract"]["satisfied"] is False


def test_pi_normalized_natural_language_goal_becomes_strong_final_boundary():
    live = FlowSpec(meta={
        "recording_goal_text": "请把查询记录、保存草稿和提交记录分别做成可调用能力。",
    })
    apply_recording_agent_edit(live, {
        "op": "set_goal",
        "goal": {
            "intent": "将查询记录、保存草稿和提交记录沉淀为可调用能力",
            "capabilities": ["查询记录", "保存记录草稿", "提交记录"],
            "success_criteria": ["三个目标操作均形成独立能力"],
            "evidence": [{"source": "goal_text", "ref": "recording_goal_text"}],
        },
    })
    finalized = FlowSpec(
        title="记录",
        steps=[
            FlowStep(
                step_id="query-step", method="GET", path="/records/page",
                source_meta={
                    "request_id": "req-query", "role": "business_get",
                    "trigger_op": "click", "trigger_locator": "text=查询",
                    "trigger_transaction_id": "txn-query",
                },
            ),
            FlowStep(
                step_id="draft-step", method="POST", path="/records/create",
                source_meta={
                    "request_id": "req-draft", "role": "business_write",
                    "trigger_op": "click", "trigger_locator": "text=保存草稿",
                    "trigger_transaction_id": "txn-draft",
                },
            ),
            FlowStep(
                step_id="submit-step", method="POST", path="/records/submit",
                source_meta={
                    "request_id": "req-submit", "role": "business_write",
                    "trigger_op": "click", "trigger_locator": "text=提交",
                    "trigger_transaction_id": "txn-submit",
                },
            ),
        ],
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.title for capability in merged.capabilities] == [
        "查询记录", "保存记录草稿", "提交记录",
    ]
    assert merged.meta["recording_goal_contract"] == {
        "source": "pi_normalized_goal",
        "expected_count": 3,
        "capabilities": [
            {"ordinal": 1, "name": "查询记录"},
            {"ordinal": 2, "name": "保存记录草稿"},
            {"ordinal": 3, "name": "提交记录"},
        ],
        "materialized_count": 3,
        "satisfied": True,
    }


def test_goal_kind_without_a_unique_anchor_is_not_bound_to_the_first_request():
    live = FlowSpec(
        flow_id="goal-no-matching-anchor",
        meta={
            "recording_goal_text": (
                "目的：审批记录\n"
                "预期产出能力数量：1\n"
                "能力1：审批记录"
            ),
        },
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-query", method="GET", path="/records/page"),
            RequestFact(request_id="req-submit", method="POST", path="/records/submit"),
        ]),
    )
    live = apply_flow_edits(live, [
        {
            "op": "set_request_role",
            "request_id": "req-query",
            "role": "business_read",
            "reason": "录制中读取业务记录",
            "evidence_refs": ["request:req-query"],
        },
        {
            "op": "set_request_role",
            "request_id": "req-submit",
            "role": "business_write",
            "reason": "录制中提交业务记录",
            "evidence_refs": ["request:req-submit"],
        },
    ])
    finalized = FlowSpec(
        flow_id="goal-no-matching-anchor-final",
        title="记录",
        steps=[
            FlowStep(
                step_id="query-step", method="GET", path="/records/page",
                source_meta={"request_id": "req-query", "request_index": 1},
                response_json={"data": {"list": []}},
            ),
            FlowStep(
                step_id="submit-step", method="POST", path="/records/submit",
                source_meta={"request_id": "req-submit", "request_index": 2},
            ),
        ],
        request_facts=live.request_facts,
    )

    merged = merge_live_agent_state(live, finalized)

    assert merged.capabilities == []
    assert merged.meta["recording_goal_contract"]["satisfied"] is False
    assert not any(
        item.get("op") == "enforce_recording_goal"
        for item in merged.meta.get("unresolved_live_agent_ops", [])
    )


def test_goal_slots_with_same_http_kind_use_live_semantic_evidence() -> None:
    live = FlowSpec(
        meta={
            "recording_goal_text": (
                "目的：新建和编辑申请\n"
                "预期产出能力数量：2\n"
                "能力1：新建申请\n"
                "能力2：编辑申请"
            ),
            "recording_agent_ops": [
                {
                    "op": "set_request_role",
                    "request_id": "req-create",
                    "role": "business_write",
                    "reason": "新建申请表单完成后提交的写请求",
                    "evidence_refs": ["request:req-create"],
                },
                {
                    "op": "set_request_role",
                    "request_id": "req-edit",
                    "role": "business_write",
                    "reason": "编辑申请表单完成后提交的写请求",
                    "evidence_refs": ["request:req-edit"],
                },
            ],
        },
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-create", request_index=1, method="POST", path="/records/submit"),
            RequestFact(request_id="req-edit", request_index=2, method="POST", path="/records/submit"),
        ]),
    )
    finalized = FlowSpec(
        title="申请",
        steps=[
                FlowStep(
                    step_id="create-step", method="POST", path="/records/submit",
                    source_meta={
                        "request_id": "req-create", "request_index": 1,
                        "trigger_op": "click", "trigger_transaction_id": "txn-create",
                    },
                ),
                FlowStep(
                    step_id="edit-step", method="POST", path="/records/submit",
                    source_meta={
                        "request_id": "req-edit", "request_index": 2,
                        "trigger_op": "click", "trigger_transaction_id": "txn-edit",
                    },
                ),
        ],
        request_facts=live.request_facts,
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.title for capability in merged.capabilities] == ["新建申请", "编辑申请"]
    assert [
        next(ref.step_id for ref in capability.request_refs if ref.usage == "execute")
        for capability in merged.capabilities
    ] == ["create-step", "edit-step"]


def test_goal_slots_use_record_identity_when_pi_has_not_named_duplicate_submit_actions() -> None:
    live = FlowSpec(meta={
        "recording_goal_text": (
            "目的：提交和编辑申请\n"
            "预期产出能力数量：2\n"
            "能力1：提交申请\n"
            "能力2：编辑申请"
        ),
    })
    finalized = FlowSpec(
        title="申请",
        steps=[
            FlowStep(
                step_id="edit-step", method="POST", path="/records/submit",
                params=[ParamField(path="id", key="id", value="record-42")],
                source_meta={
                    "request_id": "req-edit", "request_index": 1,
                    "trigger_op": "click", "trigger_transaction_id": "txn-edit",
                },
            ),
            FlowStep(
                step_id="create-step", method="POST", path="/records/submit",
                source_meta={
                    "request_id": "req-create", "request_index": 2,
                    "trigger_op": "click", "trigger_transaction_id": "txn-create",
                },
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-edit", request_index=1, method="POST", path="/records/submit"),
            RequestFact(request_id="req-create", request_index=2, method="POST", path="/records/submit"),
        ]),
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.title for capability in merged.capabilities] == ["提交申请", "编辑申请"]
    assert [
        next(ref.step_id for ref in capability.request_refs if ref.usage == "execute")
        for capability in merged.capabilities
    ] == ["create-step", "edit-step"]


def test_goal_slots_distinguish_detail_and_progress_by_recorded_action_text() -> None:
    live = FlowSpec(meta={
        "recording_goal_text": (
            "目的：查看详情和进度\n"
            "预期产出能力数量：2\n"
            "能力1：查看申请详情\n"
            "能力2：查看审批进度"
        ),
    })
    finalized = FlowSpec(
        title="申请",
        steps=[
            FlowStep(
                step_id="progress-step", method="GET", path="/records/status",
                response_json={"data": {"nodes": []}},
                source_meta={
                    "request_id": "req-progress", "request_index": 1,
                    "role": "business_get", "trigger_op": "click",
                    "trigger_locator": "text=进度", "trigger_transaction_id": "txn-progress",
                },
            ),
            FlowStep(
                step_id="detail-step", method="GET", path="/records/42",
                response_json={"data": {"id": 42}},
                source_meta={
                    "request_id": "req-detail", "request_index": 2,
                    "role": "business_get", "trigger_op": "click",
                    "trigger_locator": "text=详情", "trigger_transaction_id": "txn-detail",
                },
            ),
        ],
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-progress", request_index=1, method="GET", path="/records/status"),
            RequestFact(request_id="req-detail", request_index=2, method="GET", path="/records/42"),
        ]),
    )

    merged = merge_live_agent_state(live, finalized)

    assert [capability.title for capability in merged.capabilities] == ["查看申请详情", "查看审批进度"]
    assert [
        next(ref.step_id for ref in capability.request_refs if ref.usage == "execute")
        for capability in merged.capabilities
    ] == ["detail-step", "progress-step"]


def test_live_notebook_carries_only_replayable_hypotheses_into_finalized_facts():
    shadow = _flow()
    shadow.meta = {
        "recording_agent_ops": [
            _agent_ops()[0],
            {"op": "confirm_dependency", "link_id": "unsafe-live-verdict"},
        ],
        "agent_insights": [{"kind": "goal", "summary": "更新指定记录"}],
        "verification_log": [{"kind": "dependency_execute", "passed": True}],
        "current_version": 99,
    }

    notebook = LiveNotebook.from_shadow(shadow)

    assert [item["op"] for item in notebook.meta["recording_agent_ops"]] == ["set_goal"]
    assert "verification_log" not in notebook.meta
    assert "current_version" not in notebook.meta

    finalized = _flow()
    finalized.meta = {"verification_log": [{"kind": "final", "passed": False}]}
    merged = notebook.apply_to(finalized)
    assert merged.goal["intent"] == "更新指定记录"
    assert merged.meta["verification_log"] == [{"kind": "final", "passed": False}]


@pytest.mark.parametrize(("submitted", "canonical"), [
    ("query_list", "business_get"),
    ("query", "business_get"),
    ("business_filter_query", "business_get"),
    ("business_read", "business_get"),
    ("preflight", "read_context"),
    ("option_source", "read_option"),
    ("submit", "business_write"),
    ("business_mutation", "business_write"),
])
def test_live_request_role_aliases_are_canonical_before_materialization(
    submitted: str,
    canonical: str,
):
    live = FlowSpec(
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-detail", method="GET", path="/items/detail"),
        ]),
    )

    live = apply_flow_edits(live, [{
        "op": "set_request_role",
        "request_id": "req-detail",
        "role": submitted,
        "reason": "录制事实支持该请求用途",
        "evidence_refs": ["req-detail"],
    }])

    assert live.request_facts.analysis["req-detail"].role == canonical


def test_unknown_live_request_role_is_rejected_instead_of_silently_disappearing():
    live = FlowSpec(
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-detail", method="GET", path="/items/detail"),
        ]),
    )

    with pytest.raises(ValueError, match="unsupported request role"):
        apply_flow_edits(live, [{
            "op": "set_request_role",
            "request_id": "req-detail",
            "role": "invented_role",
            "reason": "模型临时创造的角色",
            "evidence_refs": ["req-detail"],
        }])


def test_finalize_merge_materializes_deferred_request_id_field_semantics():
    live = FlowSpec(
        flow_id="early-fields",
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-submit", request_index=2, method="POST", path="/items/update"),
        ]),
    )
    live = apply_flow_edits(live, [
        {
            "op": "set_param_source",
            "step_id": "req-submit",
            "path": "jobId",
            "source_kind": "context",
            "context_key": "jobId",
            "reason": "录制页面上下文自动提供",
        },
        {
            "op": "set_param_required",
            "step_id": "req-submit",
            "path": "jobId",
            "required": False,
            "reason": "页面控件没有必填标记",
            "evidence_refs": ["request:req-submit", "control:jobId"],
        },
        {
            "op": "rename_field",
            "step_id": "req-submit",
            "path": "jobId",
            "label": "任务编号",
            "reason": "页面控件标签为任务编号",
            "evidence_refs": ["request:req-submit", "control:jobId"],
        },
    ])
    stored_ops = (live.meta or {})["recording_agent_ops"]
    assert all(item["request_id"] == "req-submit" for item in stored_ops)
    assert all(item["wire_path"] == "body.jobId" for item in stored_ops)
    assert all(item["field_ref"] == {
        "request_id": "req-submit", "wire_path": "body.jobId",
    } for item in stored_ops)

    merged = merge_live_agent_state(live, _flow())
    param = merged.steps[1].params[0]
    assert param.source_kind == "page_context"
    assert param.required is False
    assert param.key == "任务编号"
    assert not (merged.meta or {}).get("unresolved_live_agent_ops")


def test_finalize_merge_retargets_deferred_field_op_to_unique_equivalent_request():
    live = FlowSpec(
        request_facts=RequestFacts(requests=[
            RequestFact(
                request_id="req-observed", request_index=7, method="GET",
                path="/records/page", query={"status": ["1"]},
                query_paths=["query.status"],
            ),
        ]),
    )
    live = apply_flow_edits(live, [{
        "op": "set_param_source",
        "request_id": "req-observed",
        "wire_path": "query.status",
        "source_kind": "caller_input",
        "reason": "查询筛选控件由操作人提供",
    }])
    finalized = FlowSpec(
        steps=[FlowStep(
            step_id="query-records", method="GET", path="/records/page",
            source_meta={"request_id": "req-materialized", "request_index": 8},
            params=[ParamField(path="query.status", key="status", value="1")],
        )],
        request_facts=RequestFacts(requests=[
            RequestFact(
                request_id="req-observed", request_index=7, method="GET",
                path="/records/page", query={"status": ["1"]},
                query_paths=["query.status"],
            ),
            RequestFact(
                request_id="req-materialized", request_index=8, method="GET",
                path="/records/page", query={"status": ["1"]},
                query_paths=["query.status"],
            ),
        ]),
    )

    merged = merge_live_agent_state(live, finalized)

    assert merged.steps[0].params[0].source_kind == "user_input"
    assert not (merged.meta or {}).get("unresolved_live_agent_ops")


def test_finalize_merge_uses_closest_equivalent_dependency_source():
    facts = RequestFacts(requests=[
        RequestFact(
            request_id="req-detail-old", request_index=1, sequence=1,
            method="GET", path="/workflow/detail", page_id="page-1",
            response_json={"data": {"processId": "P-1"}},
        ),
        RequestFact(
            request_id="req-detail-new", request_index=9, sequence=9,
            method="GET", path="/workflow/detail", page_id="page-1",
            response_json={"data": {"processId": "P-1"}},
        ),
        RequestFact(
            request_id="req-submit", request_index=10, sequence=10,
            method="POST", path="/workflow/submit", page_id="page-1",
        ),
    ])
    steps = [
        FlowStep(
            step_id="detail-old", method="GET", path="/workflow/detail",
            source_meta={"request_id": "req-detail-old"},
            response_json={"data": {"processId": "P-1"}},
        ),
        FlowStep(
            step_id="detail-new", method="GET", path="/workflow/detail",
            source_meta={"request_id": "req-detail-new"},
            response_json={"data": {"processId": "P-1"}},
        ),
        FlowStep(
            step_id="submit", method="POST", path="/workflow/submit",
            source_meta={"request_id": "req-submit"},
            params=[ParamField(path="body.processId", key="processId", value="P-1")],
        ),
    ]
    live = FlowSpec(steps=[steps[0].model_copy(deep=True), steps[2].model_copy(deep=True)], request_facts=facts)
    live = apply_flow_edits(live, [{
        "op": "propose_dependency",
        "kind": "value",
        "source_request_id": "req-detail-old",
        "source_path": "response.data.processId",
        "target_request_id": "req-submit",
        "target_path": "body.processId",
        "reason": "详情响应提供提交所需流程编号",
        "evidence": {"captured": True},
    }])
    finalized = FlowSpec(steps=steps, request_facts=facts)

    merged = merge_live_agent_state(live, finalized)

    assert len(merged.links) == 1
    assert merged.links[0].source_step_id == "detail-new"


def test_finalize_rejects_weak_literal_collision_and_restores_strong_id_chain():
    process_id = "workflow:15:80988d17-962a-11f1-937a-0a4095592b97"
    facts = RequestFacts(requests=[
        RequestFact(
            request_id="req-definition", request_index=1, sequence=1,
            method="GET", path="/workflow/definition", trigger_action_id="open",
            response_json={"data": {"id": process_id, "key": "workflow_key"}},
        ),
        RequestFact(
            request_id="req-create", request_index=2, sequence=2,
            method="POST", path="/records/create", trigger_action_id="save",
            post_data=json.dumps({"processDefKey": "workflow_key"}),
        ),
        RequestFact(
            request_id="req-progress", request_index=3, sequence=3,
            method="GET", path="/workflow/progress", trigger_action_id="progress",
            response_json={"data": {"processDefinitionId": process_id}},
        ),
        RequestFact(
            request_id="req-detail", request_index=4, sequence=4,
            method="GET", path="/workflow/detail", trigger_action_id="submit",
            url=f"/workflow/detail?processDefinitionId={process_id}",
            query={"processDefinitionId": [process_id]},
        ),
    ])
    steps = [
        FlowStep(
            step_id="definition", method="GET", path="/workflow/definition",
            source_meta={"request_id": "req-definition"},
            response_json={"data": {"id": process_id, "key": "workflow_key"}},
        ),
        FlowStep(
            step_id="create", method="POST", path="/records/create",
            source_meta={"request_id": "req-create"},
            params=[ParamField(
                path="processDefKey", key="processDefKey", value="workflow_key",
                category="system_const", source_kind="constant",
                source={"kind": "heuristic"}, exposed_to_user=False,
            )],
        ),
        FlowStep(
            step_id="progress", method="GET", path="/workflow/progress",
            source_meta={"request_id": "req-progress"},
            response_json={"data": {"processDefinitionId": process_id}},
        ),
        FlowStep(
            step_id="detail", method="GET", path="/workflow/detail",
            source_meta={"request_id": "req-detail"},
            params=[ParamField(
                path="query.processDefinitionId", key="processDefinitionId",
                value=process_id, category="system_const", source_kind="constant",
                source={"kind": "heuristic"}, exposed_to_user=False,
            )],
        ),
    ]
    live = FlowSpec(steps=deepcopy(steps), request_facts=facts)
    live = apply_flow_edits(live, [{
        "op": "propose_dependency",
        "kind": "value",
        "source_request_id": "req-definition",
        "source_path": "response.data.key",
        "target_request_id": "req-create",
        "target_path": "body.processDefKey",
        "reason": "相同 key 被误判为运行期依赖",
        "evidence": {"captured": True},
    }])

    merged = merge_live_agent_state(
        live,
        FlowSpec(steps=deepcopy(steps), request_facts=facts),
    )

    assert len(merged.links) == 1
    link = merged.links[0]
    assert link.source_step_id == "definition"
    assert link.source_path == "data.id"
    assert link.target_step_id == "detail"
    assert link.target_path == "query.processDefinitionId"
    assert link.confirmed is True
    create_param = merged.steps[1].params[0]
    detail_param = merged.steps[3].params[0]
    assert create_param.source_kind == "constant"
    assert detail_param.source_kind == "previous_response"


def test_caller_input_keeps_captured_option_source_contract():
    spec = _flow()
    spec.steps[0].params = [ParamField(
        path="query.type", key="type", value="2",
        category="user_param", source_kind="form_option",
        source={"kind": "form_option", "enum_source": "dom"},
        exposed_to_user=True,
    )]
    spec.request_facts.field_evidence = [{
        "event_id": "event-type",
        "request_id": "req-detail",
        "wire_path": "query.type",
        "binding_status": "bound",
        "control_kind": "select",
        "label": "类型",
        "field_aliases": ["type"],
    }]
    spec.request_facts.option_sources = [{
        "kind": "page_enum_options",
        "options": {
            "类型": {
                "field_key": "类型",
                "field_aliases": ["type"],
                "mapping_complete": True,
                "options": [{"label": "业务类型", "value": "2"}],
            },
        },
    }]

    outcome = apply_recording_agent_edit(spec, {
        "op": "set_param_source",
        "request_id": "req-detail",
        "wire_path": "query.type",
        "source_kind": "caller_input",
        "reason": "调用方选择页面捕获的业务选项",
    })

    assert outcome["status"] == "applied"
    param = spec.steps[0].params[0]
    assert param.source_kind == "form_option"
    assert param.source["enum_source"] == "dom"
    assert param.exposed_to_user is True


def test_finalize_preserves_grounded_range_start_and_names_missing_end():
    finalized = FlowSpec(steps=[FlowStep(
        step_id="query",
        method="GET",
        path="/records/page",
        params=[
            ParamField(
                path="query.createTime[0]", key="开始日期", label="开始日期",
                value="2026-08-07 00:00:00", type="date", wire_type="string",
                category="user_param", source_kind="user_input",
                exposed_to_user=True,
            ),
            ParamField(
                path="query.createTime[1]", key="createTime[1]", label="createTime[1]",
                value="2026-08-08 23:59:59", type="datetime", wire_type="string",
                category="user_param", source_kind="user_input",
                exposed_to_user=True,
            ),
        ],
    )])

    merged = merge_live_agent_state(FlowSpec(), finalized)

    assert [param.key for param in merged.steps[0].params] == ["开始日期", "结束日期"]
    changes = (merged.meta or {}).get("indexed_range_changes") or []
    assert [item["role"] for item in changes] == ["range_start", "range_end"]


def test_finalize_discards_model_source_hypothesis_rejected_by_page_fact():
    finalized = _flow()
    finalized.steps[0].params = [ParamField(
        path="query.type", key="请假类型", value="2", type="enum",
        wire_type="string", category="user_param", source_kind="page_enum",
        source={"kind": "page_enum", "dictionary_source": "dom"},
        enum_options=[
            {"label": "病假", "value": "1"},
            {"label": "事假", "value": "2"},
        ],
        enum_value_map={"病假": "1", "事假": "2"},
        exposed_to_user=True,
        evidence=[{
            "source": "recorder_dom",
            "control_kind": "select",
            "editable": True,
            "disabled": False,
            "read_only": False,
            "options": [
                {"label": "病假", "value": "1"},
                {"label": "事假", "value": "2"},
            ],
        }],
    )]
    finalized.request_facts.field_evidence.append({
        "event_id": "evt-type-select",
        "evidence_id": "evt-type-select",
        "request_id": "req-detail",
        "wire_path": "query.type",
        "label": "请假类型",
        "value": "2",
        "op": "select",
        "control_kind": "select",
        "disabled": False,
        "read_only": True,
        "binding_status": "bound",
    })
    live = FlowSpec(meta={"recording_agent_ops": [{
        "op": "set_param_source",
        "request_id": "req-detail",
        "wire_path": "query.type",
        "source_kind": "constant",
        "reason": "模型把本次选择值误判成固定常量",
        "evidence_refs": ["evt-type-select"],
    }]})

    merged = merge_live_agent_state(live, finalized)

    param = merged.steps[0].params[0]
    assert param.source_kind == "page_enum"
    assert not (merged.meta or {}).get("unresolved_live_agent_ops")
    discarded = (merged.meta or {}).get("discarded_live_agent_hypotheses") or []
    assert len(discarded) == 1
    assert discarded[0]["op"] == "set_param_source"


def test_retrying_identical_deferred_field_op_remains_retryable_not_duplicate():
    live = FlowSpec(request_facts=RequestFacts(requests=[
        RequestFact(request_id="req-submit", method="POST", path="/records/submit"),
    ]))
    edit = {
        "op": "set_param_source",
        "request_id": "req-submit",
        "wire_path": "body.reason",
        "source_kind": "caller_input",
        "reason": "输入控件由操作人填写",
    }

    first = apply_flow_edits(live, [edit])
    retried = apply_recording_agent_edit(first, edit)

    assert retried["status"] == "deferred"
    assert retried["reason"] != "duplicate operation"
    assert len(first.meta["recording_agent_ops"]) == 1


def test_repeating_an_applied_agent_conclusion_is_idempotent_success():
    spec = _flow()
    edit = {
        "op": "set_param_source",
        "request_id": "req-submit",
        "wire_path": "body.jobId",
        "source_kind": "context",
        "context_key": "jobId",
        "reason": "页面上下文自动提供",
    }

    applied = apply_flow_edits(spec, [edit])
    repeated = apply_recording_agent_edit(applied, edit)

    assert repeated == {
        "status": "applied",
        "reason": "operation already applied",
    }
    assert len(applied.meta["recording_agent_ops"]) == 1


@pytest.mark.asyncio
async def test_deferred_live_field_conclusion_is_staged_without_requiring_resubmission():
    spec = FlowSpec(
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-submit", method="POST", path="/records/submit"),
        ]),
        meta={"current_version": 1},
    )

    updated = await apply_recording_agent_submission(spec, submission={
        "semantic_plan": {},
        "ops": [{
            "op": "set_param_source",
            "request_id": "req-submit",
            "wire_path": "body.reason",
            "source_kind": "caller_input",
            "reason": "输入控件由操作人填写",
        }],
    })
    validation = recording_agent_validation(updated)

    assert validation["op_results"][0]["status"] == "deferred"
    assert validation["must_retry"] == []
    assert validation["submission_complete"] is True


def test_finalize_merge_turns_still_unresolved_deferred_field_op_into_rejection():
    live = FlowSpec(
        flow_id="early-unresolved",
        request_facts=RequestFacts(requests=[
            RequestFact(request_id="req-lost", request_index=2, method="POST", path="/lost"),
        ]),
    )
    live = apply_flow_edits(live, [{
        "op": "set_param_source",
        "request_id": "req-lost",
        "wire_path": "body.jobId",
        "source_kind": "context",
        "context_key": "jobId",
        "reason": "页面上下文",
    }])

    merged = merge_live_agent_state(live, _flow())

    assert merged.meta["unresolved_live_agent_ops"] == [{
        "op": "set_param_source",
        "status": "rejected",
        "requested_target": {
            "request_id": "req-lost", "wire_path": "body.jobId",
        },
        "reason": "field target not found: req-lost:body.jobId",
    }]
    assert not any(
        "req-lost" in item.get("text", "")
        for item in (merged.meta or {}).get("agent_insights") or []
    )


@pytest.mark.asyncio
async def test_live_field_op_survives_rejected_semantic_proposal_and_reports_each_result():
    spec = _flow()
    spec.capabilities = [FlowCapability(
        name="submit",
        title="提交",
        kind="submit",
        nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
    )]

    updated = await apply_recording_agent_submission(spec, submission={
        "ops": [
            {
                "op": "set_condition",
                "capability": "submit",
                "node": {
                    "id": "bad_entries_condition",
                    "condition": "input.entries.length > 0",
                    "then": [{"id": "call_submit", "type": "call", "step_id": "submit"}],
                },
            },
            {
                "op": "set_param_source",
                "step_id": "req-submit",
                "path": "jobId",
                "source_kind": "context",
                "context_key": "jobId",
                "reason": "页面上下文自动提供",
            },
        ],
    }, mode="plan")

    assert updated.meta["capability_model"]["proposal_gate"]["accepted"] is False
    assert updated.steps[1].params[0].source_kind == "page_context"
    results = updated.meta["recording_agent_session"]["op_results"]
    assert [item["status"] for item in results] == ["rolled_back", "applied"]


@pytest.mark.asyncio
async def test_helper_read_capability_does_not_reject_grounded_business_capabilities():
    spec = _flow()
    spec.steps[0].semantic_role = "read_context"
    spec.steps[0].source_meta = {
        **spec.steps[0].source_meta,
        "role": "read_context",
    }
    spec.steps[1].semantic_role = "business_write"
    spec.steps[1].source_meta = {
        **spec.steps[1].source_meta,
        "role": "business_write",
    }
    spec.steps[1].params[0].label = "任务编号"
    spec.steps[1].params[0].type = "string"
    spec.steps[1].params[0].category = "user_param"
    spec.steps[1].params[0].source_kind = "user_input"
    spec.steps[1].params[0].required = False

    updated = await apply_recording_agent_submission(spec, submission={
        "semantic_plan": {
            "business_understanding": {"summary": "读取上下文后更新记录"},
            "capabilities": [
                {
                    "name": "update_record",
                    "title": "更新记录",
                    "kind": "submit",
                    "anchor_step_id": "submit",
                    "request_refs": [{"step_id": "submit", "usage": "execute"}],
                },
                {
                    "name": "inspect_internal_context",
                    "title": "读取内部上下文",
                    "kind": "inspect",
                    "anchor_step_id": "detail",
                    "request_refs": [{"step_id": "detail", "usage": "execute"}],
                },
            ],
            "unresolved_items": [],
        },
        "ops": [],
    })

    assert [cap.name for cap in updated.capabilities] == ["update_record"]
    assert updated.meta["capability_model"]["proposal_gate"]["accepted"] is True
    assert updated.meta["capability_model"]["ignored_non_public_capabilities"] == [
        "inspect_internal_context",
    ]


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


def test_recording_delta_projects_dynamic_request_key_candidates():
    class Recorder:
        def captured_all_requests(self):
            return [
                {
                    "request_id": "req-detail", "sequence": 1,
                    "response_json": {"data": {"nodes": [
                        {"id": "Node_leader", "name": "领导"},
                        {"id": "Node_hr", "name": "人事"},
                    ]}},
                },
                {
                    "request_id": "req-submit", "sequence": 2, "method": "POST",
                    "post_data": {"assignees": {"Node_leader": [1], "Node_hr": [2]}},
                },
            ]

        def recorded_page_events(self):
            return []

    candidate = recording_delta(Recorder(), since_seq=0)["heuristic_candidates"]["response_key_maps"][0]
    assert candidate["source_request_id"] == "req-detail"
    assert candidate["target_container_path"] == "body.assignees"


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
    # A single live batch shares one model turn with state and the previous
    # submission receipt.  Per-branch truncation is not enough: the whole
    # delta must stay small enough to avoid timing out the Pi request.
    assert len(json.dumps(first, ensure_ascii=False)) < 80_000

    seen = []
    cursor = 0
    while True:
        page = recording_delta(recorder, since_seq=cursor, limit=10)
        seen.extend(item["request_id"] for item in page["requests"])
        cursor = page["next_seq"]
        if not page["has_more"]:
            break
    assert seen == [f"req-{index}" for index in range(61)]


def test_recording_delta_summarizes_unanchored_background_payloads():
    requests = [{
        "request_id": "req-background",
        "sequence": 0,
        "method": "POST",
        "url": "https://collector.invalid/common",
        "role": "read_context",
        "keep": False,
        "post_data": {"events": [{"payload": "x" * 20_000}]},
        "response_json": {"settings": {f"field-{index}": "y" * 100 for index in range(100)}},
    }]

    projected = recording_delta(
        None, since_seq=0, captured_requests=requests, page_events=[],
    )["requests"][0]

    assert projected["request_id"] == "req-background"
    assert "post_data" not in projected
    assert "response_json" not in projected


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


def test_recording_state_collapses_repeated_background_endpoint_observations():
    spec = _flow()
    repeated_schema = {
        "type": "object",
        "properties": {
            f"field_{index}": {"type": "string", "description": "x" * 200}
            for index in range(20)
        },
    }
    spec.request_facts.requests = [
        RequestFact(
            request_id=f"req-option-{index}",
            request_index=index,
            method="GET",
            path="/approval/detail",
            role="read_option",
            keep=False,
            response_schema=repeated_schema,
        )
        for index in range(50)
    ]
    spec.request_facts.option_sources = [
        {
            "kind": "api_response",
            "request_id": f"req-option-{index}",
            "method": "GET",
            "path": "/approval/detail",
            "sequence": index,
            "query_paths": ["query.processDefinitionId"],
            "response_schema": repeated_schema,
        }
        for index in range(50)
    ]

    state = recording_agent_state(spec)
    requests = [
        item for item in state["facts"]["captured_requests"]
        if item.get("path") == "/approval/detail"
    ]
    sources = [
        item for item in state["facts"]["option_sources"]
        if item.get("path") == "/approval/detail"
    ]

    assert len(requests) == 1
    assert requests[0]["observation_count"] == 50
    assert len(sources) == 1
    assert sources[0]["observation_count"] == 50
    assert len(json.dumps(state, ensure_ascii=False)) < 80_000


def test_recording_state_projects_canonical_transport_qualified_field_paths():
    state = recording_agent_state(_flow())
    params = {
        step["step_id"]: step["params"]
        for step in state["facts"]["steps"]
    }

    assert params["submit"][0]["path"] == "jobId"
    assert params["submit"][0]["wire_path"] == "body.jobId"


def test_recording_state_and_validation_are_bounded_for_large_realistic_capture():
    spec = _flow()
    spec.request_facts.requests = [
        RequestFact(
            request_id=f"req-{index}",
            request_index=index,
            method="GET",
            path=f"/items/{index}",
            response_json={
                "list": [
                    {f"field_{field}": "x" * 200 for field in range(20)}
                    for _row in range(10)
                ],
            },
        )
        for index in range(120)
    ]
    spec.request_facts.field_evidence = [
        {
            "request_id": f"req-{index}",
            "path": f"query.field_{index}",
            "visible_label": "业务字段" + ("很长" * 100),
            "evidence": [{"text": "页面证据" * 100}],
        }
        for index in range(120)
    ]

    state = recording_agent_state(spec)
    validation = recording_agent_validation(spec)

    assert len(json.dumps(state, ensure_ascii=False)) < 120_000
    assert len(json.dumps(validation, ensure_ascii=False)) < 120_000


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
    await session.get_recording_delta(1, limit=10)
    answer = await session.ask_operator(text="选择？", options=["选项A"])
    assert answer == {"answered": True, "answer": "选项A"}
    assert questions[0]["text"] == "选择？"

    prompts = []

    async def fake_prompt(
        text,
        *,
        timeout_s=0,
        prompt_mode="workflow",
        analysis_phase="",
    ):
        prompts.append({
            "text": text,
            "timeout_s": timeout_s,
            "prompt_mode": prompt_mode,
            "analysis_phase": analysis_phase,
        })
        return {"status": "submitted"}

    session.prompt = fake_prompt
    for reason, phase in (
        ("recording_started", "base_state_analysis"),
        ("business_request", "request_batch"),
        ("request_batch", "request_batch"),
        ("submit_candidate", "request_batch"),
        ("final_request_tail", "final_request_tail"),
    ):
        result = await session.notify_live_batch({"reason": reason, "since_seq": 2})
        assert result["status"] == "submitted"
        prompt = prompts[-1]
        assert prompt["prompt_mode"] == "recording_analysis"
        assert prompt["analysis_phase"] == phase
        assert f"analysis_phase={phase}" in prompt["text"]
        assert "since_seq=2" in prompt["text"]
        assert "submit_recording_plan" in prompt["text"]
        assert "完整能力集合" in prompt["text"]

    assert all(item["timeout_s"] is None for item in prompts)
    assert all("caller_input/constant/session" not in item["text"] for item in prompts)
