from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dano.execution.page.capability_compiler import compile_capabilities
from dano.execution.page.flow_spec import (
    FlowSpec,
    ParamField,
    apply_flow_edits,
    apply_recording_agent_submission,
    flow_spec_to_api_request,
    to_flow_spec,
)
from dano.execution.page.recording_field_identity import FieldRef, FieldReferenceError, resolve_field_ref
from dano.execution.page.recording_live import (
    dependency_link_signature,
    live_request_role_overrides,
    merge_live_agent_state,
)
from dano.execution.page.request_capture import execute_api_workflow
from dano.execution.page.verification_log import find_verification, record_verification
from dano.onboarding.recording_pi import RecordingPiError, RecordingPiSession
from dano.onboarding.recording_release import evaluate_recording_release


FIXTURE = Path(__file__).parent / "fixtures" / "recording_real" / "dianshi_leave_20260811"
STABLE_STEP_IDS = {
    "req_76": "step-query",
    "req_96": "step-definition",
    "req_98": "step-approval",
    "req_116": "step-submit",
}


def _load(name: str) -> dict:
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def _captured_spec() -> FlowSpec:
    fields = _load("field_evidence.json")
    spec = to_flow_spec(
        _load("request_facts.json")["requests"],
        field_evidence=fields["items"],
        page_enum_options=fields["page_enum_options"],
        page_events=_load("page_events.json")["events"],
        recording_mode="real_submit",
        tenant="sanitized-tenant",
        subsystem="A-OA",
    )
    old_to_new: dict[str, str] = {}
    for step in spec.steps:
        request_id = str((step.source_meta or {}).get("request_id") or "")
        old_to_new[step.step_id] = STABLE_STEP_IDS[request_id]
        step.step_id = STABLE_STEP_IDS[request_id]
    for link in spec.links:
        link.source_step_id = old_to_new.get(link.source_step_id, link.source_step_id)
        link.target_step_id = old_to_new.get(link.target_step_id, link.target_step_id)
    return spec


def _apply_real_agent_submission() -> tuple[FlowSpec, dict]:
    submission = _load("agent_submission.json")
    spec = asyncio.run(apply_recording_agent_submission(
        _captured_spec(),
        submission={
            "semantic_plan": submission["semantic_plan"],
            "ops": submission["ops"],
        },
        mode="plan",
    ))
    return spec, submission


def _attach_executor_evidence(spec: FlowSpec) -> FlowSpec:
    operations: list[dict] = []
    verification_log: list[dict] = []
    for link in spec.links:
        verification_id = record_verification(
            kind="dependency_execute",
            subject={
                "link_id": link.link_id,
                "signature": dependency_link_signature(link),
                "kind": link.kind,
                "source_request_id": str((link.evidence or {}).get("source_request_id") or ""),
                "target_request_id": str((link.evidence or {}).get("target_request_id") or ""),
            },
            status="passed",
            evidence={"injection_equal": True},
        )
        operations.append({
            "op": "confirm_dependency",
            "link_id": link.link_id,
            "verification_id": verification_id,
        })
        verification_log.append(find_verification(verification_id))
    assertion = {
        "collection_path": "data.list",
        "where": {"reason": {"equals_input": "reason"}},
        "min_matches": 1,
    }
    write_id = record_verification(
        kind="write_execute",
        subject={
            "write_step_id": "step-submit",
            "write_request_id": "req_116",
            "verify_request_id": "req_117",
            "assertion": assertion,
        },
        status="passed",
        evidence={"passed": True, "readback_total": 1},
    )
    operations.append({
        "op": "bind_verify_read",
        "write_step_id": "step-submit",
        "read_request_id": "req_117",
        "assertion": assertion,
        "verification_id": write_id,
    })
    verification_log.append(find_verification(write_id))
    spec.meta = {**(spec.meta or {}), "verification_log": verification_log}
    return apply_flow_edits(spec, operations)


def test_sanitized_real_trace_contains_all_required_artifacts_and_no_credentials():
    required = {
        "request_facts.json", "page_events.json", "field_evidence.json",
        "agent_submission.json", "replay_results.json", "expected_contract.json",
    }
    assert {path.name for path in FIXTURE.iterdir()} == required
    # expected_contract.json intentionally names forbidden secret fields; scan
    # only captured/replayed payloads for actual credential material.
    lowered = "\n".join(
        path.read_text(encoding="utf-8")
        for path in FIXTURE.iterdir()
        if path.name != "expected_contract.json"
    ).casefold()
    for secret in ("authorization\"", "cookie\"", "password\"", "bearer ", "eyj"):
        assert secret not in lowered


def test_real_trace_agent_ops_are_all_applied_and_body_field_identity_is_canonical():
    spec, _submission = _apply_real_agent_submission()
    results = spec.meta["recording_agent_session"]["op_results"]

    assert len(results) == len(_load("agent_submission.json")["ops"])
    assert {item["status"] for item in results} == {"applied"}
    resolved = resolve_field_ref(spec, FieldRef(request_id="req_116", wire_path="body.type"))
    assert resolved.step_id == "step-submit"
    assert resolved.stored_path == "type"

    query = next(step for step in spec.steps if step.step_id == "step-query")
    query_params = {param.path: param for param in query.params}
    assert query_params["query.reason"].key == "原因"
    assert query_params["query.reason"].key != "页"
    assert query_params["query.type"].enum_value_map == {"病假": 1, "事假": 2, "婚假": 3}
    assert query_params["query.processStatus"].enum_value_map["已取消"] == 4
    assert query_params["query.createTime[0]"].wire_format == "datetime_text"
    assert query_params["query.createTime[1]"].wire_format == "datetime_text"

    submit = next(step for step in spec.steps if step.step_id == "step-submit")
    submit_params = {param.path: param for param in submit.params}
    assert submit_params["billType"].source_kind == "constant"
    assert submit_params["billType"].value == "oa_duty_leave"
    assert submit_params["processDefKey"].source_kind == "constant"
    assert submit_params["startTime"].required is True
    assert submit_params["startTime"].wire_format == "epoch_ms"
    assert submit_params["endTime"].required is True
    assert submit_params["endTime"].wire_format == "epoch_ms"


def test_machine_structure_evidence_materializes_dynamic_preflight_chain():
    requests = _load("request_facts.json")["requests"]
    for request in requests:
        if request["request_id"] in {"req_96", "req_98"}:
            request["role"] = "noise"
            request["keep"] = False
    spec = to_flow_spec(
        requests,
        recording_mode="real_submit",
    )

    materialized = {
        str((step.source_meta or {}).get("request_id") or "")
        for step in spec.steps
    }
    assert {"req_96", "req_98", "req_116"} <= materialized


def test_agent_role_override_is_applied_before_step_materialization():
    requests = _load("request_facts.json")["requests"]
    query = next(request for request in requests if request["request_id"] == "req_76")
    query["role"] = "noise"
    query["keep"] = False

    spec = to_flow_spec(
        requests,
        recording_mode="real_submit",
        request_role_overrides={
            "req_76": {
                "role": "business_get",
                "keep": True,
                "reason": "Pi 依据筛选操作与请求参数判定为业务查询",
                "confidence": 0.99,
                "actor": "agent",
                "evidence": {"actor": "agent", "evidence_refs": ["req_76"]},
            },
        },
    )

    step = next(item for item in spec.steps if (item.source_meta or {}).get("request_id") == "req_76")
    assert step.semantic_role == "query"
    assert spec.request_facts.analysis["req_76"].role == "business_get"
    assert spec.request_facts.analysis["req_76"].evidence["actor"] == "agent"


def test_finalize_recompiles_live_capability_plan_from_request_ids():
    """A completed live plan must survive the request-id to step-id boundary."""
    live = FlowSpec(meta={
        "recording_agent_ops": [
            {
                "op": "set_request_role",
                "request_id": "req_76",
                "role": "query_list",
                "reason": "筛选动作触发业务列表查询",
                "evidence_refs": ["req_76"],
                "actor": "agent",
            },
            {
                "op": "set_request_role",
                "request_id": "req_96",
                "role": "preflight",
                "reason": "写请求前读取流程定义",
                "evidence_refs": ["req_96"],
                "actor": "agent",
            },
            {
                "op": "set_request_role",
                "request_id": "req_98",
                "role": "preflight",
                "reason": "写请求前读取审批结构",
                "evidence_refs": ["req_98"],
                "actor": "agent",
            },
            {
                "op": "set_request_role",
                "request_id": "req_116",
                "role": "business_write",
                "reason": "用户提交动作触发业务写请求",
                "evidence_refs": ["req_116"],
                "actor": "agent",
            },
        ],
        "capability_model": {
            "semantic_plan": {
                "business_understanding": {"intent": "查询并提交业务记录"},
                "capabilities": [
                    {
                        "name": "query_records",
                        "title": "查询业务记录",
                        "kind": "query",
                        "anchor_step_id": "req_76",
                        "request_refs": [{"step_id": "req_76", "usage": "execute"}],
                    },
                    {
                        "name": "submit_record",
                        "title": "提交业务记录",
                        "kind": "submit",
                        "anchor_step_id": "req_116",
                        "request_refs": [{"step_id": "req_116", "usage": "execute"}],
                    },
                ],
                "unresolved_items": [],
            },
        },
    })
    requests = _load("request_facts.json")["requests"]
    finalized = to_flow_spec(
        requests,
        recording_mode="real_submit",
        request_role_overrides=live_request_role_overrides(live),
    )

    merged = merge_live_agent_state(live, finalized)

    materialized = {
        str((step.source_meta or {}).get("request_id") or "")
        for step in merged.steps
    }
    assert {"req_76", "req_96", "req_98", "req_116"} <= materialized
    assert [cap.name for cap in merged.capabilities] == [
        "query_records", "submit_record",
    ]
    assert {
        cap.name: next(
            ref.request_id for ref in cap.request_refs if ref.usage == "execute"
        )
        for cap in merged.capabilities
    } == {
        "query_records": "req_76",
        "submit_record": "req_116",
    }


def test_accepted_pre_materialization_plan_survives_finalize_with_monotonic_version():
    requests = _load("request_facts.json")["requests"]
    captured = to_flow_spec(requests, recording_mode="real_submit")
    live = FlowSpec(
        request_facts=captured.request_facts.model_copy(deep=True),
        meta={
            "live_request_ids": [item.request_id for item in captured.request_facts.requests],
            "versions": [{"version": 1}],
            "current_version": 1,
        },
    )
    live = asyncio.run(apply_recording_agent_submission(
        live,
        submission={
            "semantic_plan": {
                "business_understanding": {"intent": "查询并提交业务记录"},
                "capabilities": [
                    {
                        "name": "query_records",
                        "title": "查询业务记录",
                        "kind": "query",
                        "anchor_step_id": "req_76",
                        "request_refs": [{"step_id": "req_76", "usage": "execute"}],
                    },
                    {
                        "name": "submit_record",
                        "title": "提交业务记录",
                        "kind": "submit",
                        "anchor_step_id": "req_116",
                        "request_refs": [{"step_id": "req_116", "usage": "execute"}],
                    },
                ],
                "unresolved_items": [],
            },
            "ops": [
                {
                    "op": "set_request_role",
                    "request_id": "req_76",
                    "role": "query_list",
                    "reason": "筛选动作触发业务列表查询",
                    "evidence_refs": ["req_76"],
                },
                {
                    "op": "set_request_role",
                    "request_id": "req_116",
                    "role": "business_write",
                    "reason": "用户提交动作触发业务写请求",
                    "evidence_refs": ["req_116"],
                },
            ],
        },
        mode="plan",
    ))
    accepted_version = int(live.meta["current_version"])
    finalized = to_flow_spec(
        requests,
        recording_mode="real_submit",
        request_role_overrides=live_request_role_overrides(live),
    )

    merged = merge_live_agent_state(live, finalized)

    assert [cap.name for cap in merged.capabilities] == [
        "query_records", "submit_record",
    ]
    assert int(merged.meta["current_version"]) > accepted_version


def test_finalize_keeps_existing_capabilities_when_live_plan_anchor_is_unresolved():
    finalized = _captured_spec()
    original_names = [cap.name for cap in finalized.capabilities]
    live = FlowSpec(meta={
        "capability_model": {
            "semantic_plan": {
                "capabilities": [{
                    "name": "missing_record",
                    "title": "缺失请求",
                    "kind": "query",
                    "anchor_step_id": "req-not-captured",
                    "request_refs": [{"step_id": "req-not-captured", "usage": "execute"}],
                }],
            },
        },
    })

    merged = merge_live_agent_state(live, finalized)

    assert [cap.name for cap in merged.capabilities] == original_names
    assert merged.meta["unresolved_live_agent_ops"] == [{
        "op": "compile_capabilities",
        "status": "rejected",
        "requested_target": {"anchor_step_ids": ["req-not-captured"]},
        "reason": "live capability anchors were not materialized at finalize",
    }]


def test_real_trace_compiles_only_verified_graph_and_releases_both_capabilities():
    spec, submission = _apply_real_agent_submission()
    spec = _attach_executor_evidence(spec)
    compilation = compile_capabilities(spec, submission["semantic_plan"])
    assert compilation.errors == []
    compiled = compilation.spec
    by_name = {cap.name: cap for cap in compiled.capabilities}

    query = by_name["query_leave"]
    submit = by_name["submit_leave"]
    assert query.step_ids == ["step-query"]
    assert submit.step_ids == ["step-definition", "step-approval", "step-submit"]
    # The model proposed the unrelated list query as a submit preflight; the
    # compiler must derive membership from verified links and discard it.
    assert "step-query" not in submit.step_ids
    assert [(ref.step_id, ref.usage) for ref in submit.request_refs if ref.step_id] == [
        ("step-definition", "preflight"),
        ("step-approval", "preflight"),
        ("step-submit", "execute"),
    ]
    assert any(
        ref.request_id == "req_111" and ref.usage == "option_source"
        for ref in submit.request_refs
    )

    api_request, api_errors = flow_spec_to_api_request(compiled)
    assert api_errors == []
    api_capabilities = {item["name"]: item for item in api_request["capabilities"]}
    query_schema = api_capabilities["query_leave"]["input_schema"]["properties"]
    assert query_schema["pageNo"]["default"] == 1
    assert query_schema["pageNo"]["minimum"] == 1
    assert query_schema["pageSize"]["default"] == 10
    assert query_schema["pageSize"]["minimum"] == 1
    assert query_schema["pageSize"]["maximum"] == 100

    approval = next(step for step in compiled.steps if step.step_id == "step-approval")
    computed = next(param for param in approval.params if param.path == "query.processVariablesStr")
    assert computed.source["strategy"] == "date_span_days_json"
    assert computed.source["sample_verified"] is True

    submit_schema = api_capabilities["submit_leave"]["input_schema"]["properties"]
    assert "approvers" in submit_schema
    assert not any(name.startswith("Activity_") for name in submit_schema)
    assert submit_schema["startTime"]["x-dano-wire-format"] == "epoch_ms"
    assert submit_schema["endTime"]["x-dano-wire-format"] == "epoch_ms"

    decision = evaluate_recording_release(compiled)
    assert decision.status == "ready", decision.to_dict()
    assert [cap.name for cap in decision.callable_spec.capabilities] == [
        "query_leave", "submit_leave",
    ]


def test_real_replay_failure_truth_is_fail_closed():
    results = _load("replay_results.json")["results"]
    by_target = {item["target"]: item for item in results}
    assert by_target["http-400-must-fail"]["status"] == "failed"
    assert by_target["missing-status-must-be-inconclusive"]["status"] == "inconclusive"
    assert by_target["dynamic-node-count-change"]["status"] == "failed"
    for item in results:
        verification_id = record_verification(
            kind=item["kind"],
            subject={"fixture_target": item["target"]},
            status=item["status"],
            evidence=item.get("evidence") or {},
            failure_reason=item.get("failure_reason") or "",
        )
        assert find_verification(verification_id)["status"] == item["status"]


def test_real_contract_rebuilds_changed_approval_nodes_with_offline_executor():
    spec, submission = _apply_real_agent_submission()
    compiled = compile_capabilities(
        _attach_executor_evidence(spec), submission["semantic_plan"],
    ).spec
    api_request, errors = flow_spec_to_api_request(compiled)
    assert errors == []
    approval = next(step for step in api_request["steps"] if step["step_id"] == "step-approval")
    approval["response_json"] = {
        "data": {"activityNodes": [
            {"id": "Activity_latest_leader", "name": "领导审批"},
            {"id": "Activity_latest_hr", "name": "人力审批"},
        ]},
    }
    inputs = {
        "请假类型": "事假",
        "reason": "DANO_REAL_TRACE_SANITIZED",
        "startTime": "2026-08-05 16:00:00",
        "endTime": "2026-08-06 16:00:00",
        "approvers": {"领导审批": 701, "人力审批": 702},
    }
    result = asyncio.run(execute_api_workflow(api_request, inputs, send=False))
    assert result["ok"] is True
    final_body = next(
        step["body"] for step in result["step_results"]
        if step.get("method") == "POST"
    )
    assert final_body["startUserSelectAssignees"] == {
        "Activity_latest_leader": [701],
        "Activity_latest_hr": [702],
    }
    assert "Activity_recorded" not in json.dumps(final_body, ensure_ascii=False)

    approval["response_json"]["data"]["activityNodes"].append(
        {"id": "Activity_latest_finance", "name": "财务审批"},
    )
    rejected = asyncio.run(execute_api_workflow(api_request, inputs, send=False))
    assert rejected["ok"] is False
    assert any(
        "审批节点与调用方输入不一致" in issue
        for issue in rejected["step_result"]["self_check"]
    )


@pytest.mark.asyncio
async def test_runtime_fact_check_uses_the_same_strict_assertion_contract(monkeypatch):
    from dano.execution.page import request_capture

    async def fake_items(*_args, **_kwargs):
        return ([{"reason": "mine"}, {"reason": "other"}], 2, False)

    monkeypatch.setattr(request_capture, "_fact_check_items", fake_items)
    fact_check = {
        "endpoint": "/api/records?pageNo=1&pageSize=10",
        "assertion": {
            "collection_path": "data.list",
            "where": {"reason": {"equals_input": "reason"}},
            "min_matches": 1,
        },
        "retries": 1,
    }
    passed = await request_capture._grounded_recheck(
        fact_check, {"reason": "mine"}, base_url="http://example.test",
        storage_state=None, token_key=None, verify=False, auth_headers=None,
    )
    failed = await request_capture._grounded_recheck(
        fact_check, {"reason": "missing"}, base_url="http://example.test",
        storage_state=None, token_key=None, verify=False, auth_headers=None,
    )
    batch = await request_capture._grounded_recheck_many(
        fact_check, [{"reason": "mine"}, {"reason": "missing"}],
        base_url="http://example.test", storage_state=None, token_key=None,
        verify=False, auth_headers=None,
    )
    assert passed == (True, "")
    assert failed[0] is False
    assert [item[0] for item in batch] == [True, False]


def test_rejected_real_trace_op_has_no_applied_insight_and_repair_reports_every_op():
    spec = _captured_spec()
    before = list((spec.meta or {}).get("agent_insights") or [])
    repaired = asyncio.run(apply_recording_agent_submission(
        spec,
        submission={"ops": [
            {
                "op": "rename_field", "request_id": "req_76",
                "wire_path": "query.reason", "label": "页",
                "reason": "故意与页面证据冲突", "evidence_refs": ["fe-query-reason"],
            },
            {
                "op": "set_param_required", "request_id": "req_76",
                "wire_path": "query.pageNo", "required": False,
                "reason": "分页可选", "evidence_refs": ["fe-query-page"],
            },
        ]},
        mode="repair",
    ))
    results = repaired.meta["recording_agent_session"]["op_results"]
    assert [item["status"] for item in results] == ["rejected", "applied"]
    assert len(results) == 2
    added = list((repaired.meta or {}).get("agent_insights") or [])[len(before):]
    assert not any("业务名称为 页" in item.get("text", "") for item in added)


def test_ambiguous_same_name_field_is_rejected_instead_of_leaf_matched():
    spec = _captured_spec()
    submit = next(step for step in spec.steps if step.step_id == "step-submit")
    submit.params.append(ParamField(path="type", key="duplicate", value=2))
    with pytest.raises(FieldReferenceError, match="ambiguous"):
        resolve_field_ref(spec, FieldRef(request_id="req_116", wire_path="body.type"))


def test_machine_failure_from_real_contract_cannot_be_overridden_by_three_true_reviews():
    spec, submission = _apply_real_agent_submission()
    compiled = compile_capabilities(spec, submission["semantic_plan"]).spec
    submit = next(cap for cap in compiled.capabilities if cap.name == "submit_leave")
    compiled.capabilities = [submit]
    machine = evaluate_recording_release(compiled)
    assert machine.machine_passed is False
    session = RecordingPiSession(
        tenant="tenant-a", subsystem="A-OA",
        recording_id="recording_22222222222222222222222222222222",
    )
    session.last_submission_kind = "review"
    session.last_review = {
        "base_flow_version": 1,
        "all_passed": True,
        "verdicts": [
            {"role": role, "passed": True, "reasons": [], "model_id": session.model_id}
            for role in ("acceptance", "security", "compliance")
        ],
    }
    with pytest.raises(RecordingPiError, match="模型审核不能覆盖"):
        session.require_publish_review(
            flow_version=1,
            flow_fingerprint="ignored",
            machine_decision=machine,
        )


def test_non_ruoyi_daily_report_contract_still_compiles_without_leave_assumptions():
    raw = json.loads(
        (Path(__file__).parent / "fixtures" / "recording_v3" / "daily_report_flow_spec.json")
        .read_text(encoding="utf-8")
    )
    spec = FlowSpec.model_validate(raw)
    api_request, errors = flow_spec_to_api_request(spec)
    assert errors == []
    assert api_request is not None
    assert [step["path"] for step in api_request["steps"]] == [
        "/api/daily/missing", "/api/daily/submit",
    ]
    assert "entries" in api_request["capabilities"][0]["input_schema"]["properties"]
    assert "oa_duty_leave" not in json.dumps(api_request, ensure_ascii=False)
