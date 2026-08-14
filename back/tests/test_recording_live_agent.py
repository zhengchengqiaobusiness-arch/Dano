from __future__ import annotations

import asyncio
import json
import time

import pytest

from dano.execution.page.flow_spec import (
    _auto_confirm_ready_capabilities,
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
    apply_recording_agent_edit,
    live_request_role_overrides,
    merge_live_agent_state,
    recording_delta,
)
from dano.onboarding.recording_pi import RecordingPiSession


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
                RequestFact(request_id="req-detail", request_index=1, sequence=1, method="GET", path="/items/detail"),
                RequestFact(request_id="req-submit", request_index=2, sequence=2, method="POST", path="/items/update"),
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
    # chained compiles into the executable previous_response contract and keeps
    # the agent taxonomy in evidence.
    assert param.source_kind == "previous_response"
    assert param.source["step_id"] == "detail"
    assert param.source["response_path"] == "data.jobId"
    assert param.source["origin_request_id"] == "req-detail"
    assert any(item.get("source_kind") == "chained" for item in param.evidence)
    assert len(updated.links) == 1
    assert updated.links[0].meta == {
        "verified": False,
        "actor": "agent",
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
    assert updated.steps[1].params[0].source_kind == "previous_response"


def test_live_field_semantics_resolve_request_id_and_cover_source_required_and_name():
    updated = apply_flow_edits(_flow(), [
        {
            "op": "set_param_source",
            "step_id": "req-submit",
            "path": "jobId",
            "source_kind": "page_context",
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
                "role": "read_context",
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
        "source_kind": "page_context",
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
            "source_kind": "user_input",
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
        "source_kind": "user_input",
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
        "source_kind": "user_input",
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
            "source_kind": "page_context",
            "reason": "错误地声明为查询参数",
        }])


def test_live_field_semantics_reject_unknown_target_instead_of_reporting_success():
    with pytest.raises(ValueError, match="target.*not found"):
        apply_flow_edits(_flow(), [{
            "op": "set_param_source",
            "step_id": "req-missing",
            "path": "jobId",
            "source_kind": "page_context",
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
    with pytest.raises(ValueError, match="only applies to header params"):
        apply_flow_edits(spec, [{
            "op": "set_param_source",
            "step_id": "submit",
            "path": "billType",
            "source_kind": "session_header",
            "reason": "固定值，误归为会话头",
        }])


def test_param_source_page_context_pagination_is_caller_override_with_recorded_default():
    spec = _flow()
    spec.steps[0].params = [ParamField(path="query.pageNo", key="pageNo", value=1)]
    updated = apply_flow_edits(spec, [{
        "op": "set_param_source",
        "step_id": "detail",
        "path": "query.pageNo",
        "source_kind": "page_context",
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
            "source_kind": "user_input", "reason": "调用方提供开始时间",
        },
        {
            "op": "set_param_source", "step_id": "submit", "path": "endTime",
            "source_kind": "user_input", "reason": "调用方提供结束时间",
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
            "source_kind": "user_input", "reason": "调用方提供开始时间",
        },
        {
            "op": "set_param_source", "step_id": "submit", "path": "body.endTime",
            "source_kind": "user_input", "reason": "调用方提供结束时间",
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
            "source_kind": "user_input", "reason": "调用方提供开始时间",
        },
        {
            "op": "set_param_source", "step_id": "detail", "path": "endTime",
            "source_kind": "user_input", "reason": "调用方提供结束时间",
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
            "source_kind": "chained",
            "origin_request_id": "req-nowhere",
            "origin_path": "data.jobId",
            "reason": "臆造的上游",
        }])

    updated = apply_flow_edits(_flow(), [{
        "op": "set_param_source",
        "step_id": "submit",
        "path": "jobId",
        "source_kind": "chained",
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
            "Activity_recorded_leader": [160],
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

    link = updated.links[0]
    assert link.kind == "response_key_map"
    assert link.source_collection_path == "data.activityNodes"
    assert link.target_container_path == "startUserSelectAssignees"
    assert link.value_binding["required_labels"] == ["领导审批", "HR审批"]
    assert link.value_binding["ignored_labels"] == ["发起人", "结束"]
    public = next(param for param in updated.steps[1].params if param.key == "approvers")
    assert public.value == {"领导审批": 160, "HR审批": 159}
    assert public.type == "object"
    assert all(
        not param.exposed_to_user
        for param in updated.steps[1].params
        if "Activity_recorded" in param.path
    )
    schema = _capability_input_schema([public])
    assert schema["properties"]["approvers"]["x-dano-option-source"] == {
        "capability": "list_approval_users",
        "value_path": "id",
        "label_path": "nickname",
    }

    api_request, errors = flow_spec_to_api_request(updated)
    assert errors == []
    assert api_request is not None
    api_request["steps"][0]["response_json"] = {
        "data": {"activityNodes": [
            {"id": "Event_runtime_start", "name": "发起人"},
            {"id": "Activity_runtime_leader", "name": "领导审批"},
            {"id": "Activity_runtime_hr", "name": "HR审批"},
            {"id": "Event_runtime_end", "name": "结束"},
        ]},
    }
    out = await execute_api_workflow(api_request, {
        "approvers": {"领导审批": 200, "HR审批": 201},
    }, send=False)
    assert out["ok"] is True
    assert out["final"]["body"]["startUserSelectAssignees"] == {
        "Activity_runtime_leader": [200],
        "Activity_runtime_hr": [201],
    }
    assert "Activity_recorded" not in json.dumps(out["final"]["body"], ensure_ascii=False)

    rejected = await execute_api_workflow(api_request, {
        "approvers": {"领导审批": 200},
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
    assert merged.request_facts.analysis["req-submit"].role == "submit_anchor"
    assert len(merged.links) == 1
    assert merged.links[0].source_step_id == "detail"
    assert merged.links[0].target_step_id == "submit"


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
    assert live_request_role_overrides(live)["req-detail"]["role"] == canonical


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
            "source_kind": "page_context",
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
        "source_kind": "user_input",
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


def test_retrying_identical_deferred_field_op_remains_retryable_not_duplicate():
    live = FlowSpec(request_facts=RequestFacts(requests=[
        RequestFact(request_id="req-submit", method="POST", path="/records/submit"),
    ]))
    edit = {
        "op": "set_param_source",
        "request_id": "req-submit",
        "wire_path": "body.reason",
        "source_kind": "user_input",
        "reason": "输入控件由操作人填写",
    }

    first = apply_flow_edits(live, [edit])
    retried = apply_recording_agent_edit(first, edit)

    assert retried["status"] == "deferred"
    assert retried["reason"] != "duplicate operation"
    assert len(first.meta["recording_agent_ops"]) == 1


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
        "source_kind": "page_context",
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
                "source_kind": "page_context",
                "reason": "页面上下文自动提供",
            },
        ],
    }, mode="plan")

    assert updated.meta["capability_model"]["proposal_gate"]["accepted"] is False
    assert updated.steps[1].params[0].source_kind == "page_context"
    results = updated.meta["recording_agent_session"]["op_results"]
    assert [item["status"] for item in results] == ["rolled_back", "applied"]


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
    assert session.recording_delta_cursor() == 1
    await session.get_recording_delta(1, limit=10)
    assert session.recording_delta_cursor() == 2
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
    assert "plan.semantic_plan.capabilities" in prompts[0][0]
    assert "request_refs" in prompts[0][0]
