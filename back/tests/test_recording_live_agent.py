from __future__ import annotations

import asyncio
import json
import time

import pytest

from dano.execution.page.flow_spec import (
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
)
from dano.execution.page.recording_live import merge_live_agent_state, recording_delta
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
    assert param.key == "任务编号"
    assert param.label == "任务编号"
    assert {item.get("kind") for item in param.evidence} >= {
        "param_source", "param_required", "field_name",
    }


def test_live_field_semantics_resolve_request_id_and_qualified_body_path():
    updated = apply_flow_edits(_flow(), [{
        "op": "set_param_source",
        "step_id": "req-submit",
        "wire_path": "body.jobId",
        "source_kind": "page_context",
        "reason": "页面上下文自动提供",
    }])

    assert updated.steps[1].params[0].source_kind == "page_context"


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
    spec.steps[1].params.append(ParamField(path="day", key="day", value=14))

    with pytest.raises(ValueError, match="date_span_days_json"):
        apply_flow_edits(spec, [{
            "op": "set_param_source",
            "step_id": "submit",
            "path": "day",
            "source_kind": "computed",
            "reason": "天数由起止时间推导",
        }])

    updated = apply_flow_edits(spec, [{
        "op": "set_param_source",
        "step_id": "submit",
        "path": "day",
        "source_kind": "computed",
        "strategy": "date_span_days_json",
        "start_field": "开始时间",
        "end_field": "结束时间",
        "reason": "天数由起止时间推导",
    }])
    param = next(item for item in updated.steps[1].params if item.path == "day")
    assert param.source_kind == "computed"
    assert param.source["start_field"] == "开始时间"


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
        "status": "skipped",
        "target": "submit:jobId",
        "reason": "label '页' contradicts field_evidence for jobId: observed=['任务编号']",
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
    with pytest.raises(ValueError, match="evidence_refs must cite recorded facts"):
        apply_flow_edits(_flow(), [{
            "op": "rename_field",
            "step_id": "submit",
            "path": "jobId",
            "label": "任务编号",
            "reason": "臆造名称",
            "evidence_refs": ["猜测"],
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
    assert link.meta["kind"] == "structure"
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


def test_semantic_plan_cannot_bypass_grounded_name_required_or_enum_ops():
    from dano.execution.page.flow_spec import _semantic_plan_to_ops

    spec = _flow()
    spec.steps[1].params[0].required = False
    spec.capabilities = [FlowCapability(
        name="submit", kind="submit", nodes=[{"id": "call", "type": "call", "step_id": "submit"}],
    )]
    ops = _semantic_plan_to_ops(spec, {
        "semantic_plan": {
            "field_semantics": [{
                "step_id": "submit",
                "wire_path": "jobId",
                "public_name": "页",
                "required": True,
                "enum_options": [{"label": "事假", "value": 1}],
                "confidence": 0.99,
                "evidence": [{"source": "model_reasoning"}],
            }],
        },
    })

    field_ops = [item for item in ops if item.get("op") in {"rename_field", "upsert_input_field"}]
    assert all(item.get("label") != "页" for item in field_ops)
    for operation in field_ops:
        field = operation.get("field") or {}
        assert field.get("key") != "页"
        assert field.get("required") is not True
        assert "enum_options" not in field


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

    merged = merge_live_agent_state(live, _flow())
    param = merged.steps[1].params[0]
    assert param.source_kind == "page_context"
    assert param.required is False
    assert param.key == "任务编号"
    assert not (merged.meta or {}).get("unresolved_live_agent_ops")


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
