"""Stage 2–6 handoff contracts: identity, roles, evidence ids, model state."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from dano.execution.page.recording_facts import _preread_dedupe_key
from dano.execution.page.flow_spec import (
    CapabilityRequestRef,
    FlowCapability,
    flow_spec_capability_contracts,
    recording_agent_state,
    to_flow_spec,
)
from dano.execution.page.recording_field_evidence import _evidence_id, bind_field_evidence
from dano.execution.page.recording_live import compact_model_payload, recording_delta
from dano.onboarding.recording_gateway import _spec_fields


PAGE = {
    "url": "http://example.test/app/docs",
    "path": "/app/docs",
    "document_title": "单据",
}


def _req(
    request_id: str,
    *,
    method: str,
    url: str,
    sequence: int,
    role: str,
    keep: bool = True,
    body: dict | None = None,
    response: dict | None = None,
    action: str = "",
) -> dict:
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    return {
        "request_id": request_id,
        "sequence": sequence,
        "method": method,
        "url": url,
        "query": query,
        "post_data": None if body is None else json.dumps(body, ensure_ascii=False),
        "response_status": 200,
        "response_json": response if response is not None else {"code": 0, "data": True},
        "page_id": "page_1",
        "frame_id": "frame_1",
        "page_context": PAGE,
        "trigger_page_context": PAGE,
        "trigger_action_id": action,
        "trigger_transaction_id": action,
        "_request_role": {"role": role, "keep": keep, "confidence": 0.95},
    }


def test_preread_dedupe_keeps_same_path_with_different_record_ids() -> None:
    first = _req(
        "req_88",
        method="GET",
        url="http://example.test/admin-api/doc/get?id=37",
        sequence=8,
        role="business_get",
    )
    second = _req(
        "req_93",
        method="GET",
        url="http://example.test/admin-api/doc/get?id=36",
        sequence=12,
        role="business_get",
    )
    assert _preread_dedupe_key(first) != _preread_dedupe_key(second)


def test_to_flow_spec_materializes_distinct_record_identity_gets() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_88",
                method="GET",
                url="http://example.test/admin-api/doc/get?id=37",
                sequence=8,
                role="business_get",
                response={"code": 0, "data": {"id": 37, "remark": "other"}},
            ),
            _req(
                "req_93",
                method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=12,
                role="business_get",
                response={"code": 0, "data": {"id": 36, "remark": "edit"}},
            ),
            _req(
                "req_update",
                method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=13,
                role="business_write",
                action="act_edit",
                body={"id": 36, "remark": "edit"},
            ),
        ],
        page_context=PAGE,
    )
    request_ids = {
        str((step.source_meta or {}).get("request_id") or "")
        for step in spec.steps
    }
    assert {"req_88", "req_93", "req_update"} <= request_ids


def test_request_role_overrides_keep_detail_get_as_business_read() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_detail",
                method="GET",
                url="http://example.test/admin-api/doc/get?id=36",
                sequence=3,
                role="read_context",
                keep=False,
                response={"code": 0, "data": {"id": 36}},
            ),
            _req(
                "req_update",
                method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=4,
                role="business_write",
                action="act_edit",
                body={"id": 36, "remark": "x"},
            ),
        ],
        page_context=PAGE,
        request_role_overrides={
            "req_detail": {
                "role": "business_get",
                "keep": True,
                "reason": "edit hydration",
                "confidence": 0.9,
            }
        },
    )
    request_ids = {
        str((step.source_meta or {}).get("request_id") or "")
        for step in spec.steps
    }
    assert "req_detail" in request_ids


def test_evidence_id_is_stable_when_list_order_changes() -> None:
    first = {
        "label": "备注",
        "field": "remark",
        "field_aliases": ["remark"],
        "value": "213213",
        "op": "fill",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "in_dialog": True,
        "page_context": PAGE,
    }
    second = {
        "label": "优惠率（%）",
        "field": "discountPercent",
        "field_aliases": ["discountPercent"],
        "value": "1110",
        "op": "fill",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "in_dialog": True,
        "page_context": PAGE,
    }
    assert _evidence_id(first, 0) == _evidence_id(first, 9)
    forward = bind_field_evidence([], [], [first, second])
    reverse = bind_field_evidence([], [], [second, first])
    assert {item["evidence_id"] for item in forward} == {item["evidence_id"] for item in reverse}


def test_recording_state_keeps_newest_field_evidence() -> None:
    evidence = [
        {
            "evidence_id": f"field-evidence-old-{index:02d}",
            "label": f"旧字段{index}",
            "field": f"old_{index}",
            "value": str(index),
            "binding_status": "unbound",
        }
        for index in range(45)
    ]
    evidence.append({
        "evidence_id": "field-evidence-dialog-remark",
        "label": "备注",
        "field": "remark",
        "value": "213213",
        "binding_status": "bound",
        "request_id": "req_update",
        "wire_path": "body.remark",
    })
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_update",
                method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=1,
                role="business_write",
                action="act_edit",
                body={"id": 1, "remark": "213213"},
            ),
        ],
        field_evidence=evidence,
        page_context=PAGE,
    )
    state = recording_agent_state(spec)
    labels = [
        str(item.get("label") or "")
        for item in (state.get("facts") or {}).get("field_evidence") or []
        if isinstance(item, dict)
    ]
    assert "备注" in labels


def test_recording_state_does_not_run_release_preparation(monkeypatch) -> None:
    """A read-only Pi poll must not rebuild the release contract."""
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_query",
                method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1",
                sequence=1,
                role="business_get",
            ),
        ],
        page_context=PAGE,
    )
    spec.capabilities = [FlowCapability(
        name="query_docs",
        title="查询单据",
        kind="query",
        step_ids=[spec.steps[0].step_id],
        request_refs=[CapabilityRequestRef(
            request_id="req_query",
            step_id=spec.steps[0].step_id,
            usage="execute",
        )],
        nodes=[{
            "id": "call_query",
            "type": "call",
            "step_id": spec.steps[0].step_id,
            "request_id": "req_query",
            "usage": "execute",
        }],
    )]

    def _unexpected_release_preparation(*_args, **_kwargs):
        raise AssertionError("recording state triggered release preparation")

    monkeypatch.setattr(
        "dano.execution.page.flow_spec_core.request_contract.prepare_flow_spec_for_publish",
        _unexpected_release_preparation,
    )
    monkeypatch.setattr(
        "dano.execution.page.flow_spec_validate.prepare_flow_spec_for_publish",
        _unexpected_release_preparation,
    )

    def _unexpected_release_contracts(*_args, **_kwargs):
        raise AssertionError("recording state built release capability contracts")

    monkeypatch.setattr(
        "dano.execution.page.flow_spec_core.request_contract.flow_spec_capability_contracts",
        _unexpected_release_contracts,
    )

    state = recording_agent_state(spec)
    assert state["projection"]["bounded"] is True


def test_capability_contract_projection_normalizes_once(monkeypatch) -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_query",
                method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1",
                sequence=1,
                role="business_get",
            ),
        ],
        page_context=PAGE,
    )
    spec.capabilities = [
        FlowCapability(
            name=name,
            title=title,
            kind="query",
            step_ids=[spec.steps[0].step_id],
        )
        for name, title in (("query_docs", "查询单据"), ("export_docs", "导出单据"))
    ]
    calls = 0

    def _count_sync(current):
        nonlocal calls
        calls += 1
        return current

    monkeypatch.setattr(
        "dano.execution.page.capability_views.sync_flow_spec_models",
        _count_sync,
    )

    contracts = flow_spec_capability_contracts(spec)
    assert len(contracts) == 2
    assert calls == 1


def test_recording_state_reuses_its_validation_report(monkeypatch) -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_query",
                method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1",
                sequence=1,
                role="business_get",
            ),
        ],
        page_context=PAGE,
    )
    calls = 0

    def _validation(_spec, **_kwargs):
        nonlocal calls
        calls += 1
        return {"passed": True, "errors": [], "warnings": []}

    monkeypatch.setattr(
        "dano.execution.page.recording_analysis_state.validate_flow_spec",
        _validation,
    )
    monkeypatch.setattr(
        "dano.execution.page.capability_orchestration.validate_flow_spec",
        _validation,
    )

    recording_agent_state(spec)
    assert calls == 1


def test_recording_state_does_not_rebuild_review_items(monkeypatch) -> None:
    from dano.execution.page import recording_analysis_state as state_module

    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_query",
                method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1",
                sequence=1,
                role="business_get",
            ),
        ],
        page_context=PAGE,
    )

    def _unexpected_review_refresh(_spec, **_kwargs):
        raise AssertionError("recording state rebuilt review items")

    monkeypatch.setattr(
        state_module,
        "refresh_review_items",
        _unexpected_review_refresh,
        raising=False,
    )

    state = recording_agent_state(spec)
    assert state["projection"]["bounded"] is True


def test_prepared_capability_validation_does_not_resync_schemas(monkeypatch) -> None:
    from dano.execution.page.capability_validation import _capability_validation_report

    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_query",
                method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1",
                sequence=1,
                role="business_get",
            ),
        ],
        page_context=PAGE,
    )

    def _unexpected_schema_sync(_spec):
        raise AssertionError("prepared capability validation resynced schemas")

    monkeypatch.setattr(
        "dano.execution.page.capability_validation._sync_capability_io_schemas",
        _unexpected_schema_sync,
    )

    report = _capability_validation_report(spec, prepared=True)
    assert report["passed"] is False


def test_dry_run_reuses_a_compiled_request(monkeypatch) -> None:
    from dano.execution.page.flow_spec_core.request_contract import dry_run_flow_spec

    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_query",
                method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1",
                sequence=1,
                role="business_get",
            ),
        ],
        page_context=PAGE,
    )
    compiled = {
        "method": "GET",
        "url": "http://example.test/admin-api/doc/page",
        "path": "/admin-api/doc/page",
        "query_template": {"pageNo": "1"},
        "params": [],
        "sample_inputs": {},
    }

    def _unexpected_compile(*_args, **_kwargs):
        raise AssertionError("dry run compiled the same request twice")

    monkeypatch.setattr(
        "dano.execution.page.flow_spec_core.request_contract.flow_spec_to_api_request",
        _unexpected_compile,
    )

    result = dry_run_flow_spec(
        spec,
        _prepared=True,
        _compiled=(compiled, []),
    )
    assert result["ok"] is True


def test_semantic_candidate_gate_reuses_validation_dry_run(monkeypatch) -> None:
    from dano.execution.page.capability_semantic import _semantic_candidate_gate
    from dano.execution.page.flow_spec import FlowSpec

    reports = iter([
        {
            "errors": [],
            "warnings": [],
            "capability_validation": {},
            "dry_run": {"ok": True, "missing_params": []},
        },
        {
            "errors": [],
            "warnings": [],
            "capability_validation": {},
            "dry_run": {"ok": True, "missing_params": []},
        },
    ])
    monkeypatch.setattr(
        "dano.execution.page.capability_semantic.validate_flow_spec",
        lambda _spec: next(reports),
    )

    def _unexpected_dry_run(_spec):
        raise AssertionError("candidate gate repeated validation dry-run")

    monkeypatch.setattr(
        "dano.execution.page.capability_semantic.dry_run_flow_spec",
        _unexpected_dry_run,
    )

    accepted, audit = _semantic_candidate_gate(FlowSpec(), FlowSpec())

    assert accepted is True
    assert audit["before_dry_ok"] is True
    assert audit["after_dry_ok"] is True


async def test_plan_without_indexed_range_changes_skips_candidate_gate(monkeypatch) -> None:
    import dano.execution.page.recording_agent_contract as contract
    from dano.execution.page.flow_spec import FlowSpec

    spec = FlowSpec(meta={"current_version": 1})
    monkeypatch.setattr(
        contract,
        "_apply_grounded_indexed_range_names",
        lambda current: (current, []),
    )

    def _unexpected_gate(*_args, **_kwargs):
        raise AssertionError("unchanged indexed ranges ran the quality gate")

    monkeypatch.setattr(contract, "_semantic_candidate_gate", _unexpected_gate)

    async def _orchestrate(current, **_kwargs):
        current.meta = {
            **(current.meta or {}),
            "capability_orchestration_audit": {
                "after_errors": 0,
                "after_warnings": 0,
            },
        }
        return current

    monkeypatch.setattr(contract, "orchestrate_flow_capabilities", _orchestrate)
    monkeypatch.setattr(contract, "validate_flow_spec", lambda _spec: {
        "passed": True,
        "errors": [],
        "warnings": [],
    })

    await contract.apply_recording_agent_submission(
        spec,
        submission={"ops": []},
        mode="plan",
    )


async def test_plan_reuses_orchestration_validation_summary(monkeypatch) -> None:
    import dano.execution.page.recording_agent_contract as contract
    from dano.execution.page.flow_spec import FlowSpec

    spec = FlowSpec(meta={"current_version": 1})
    monkeypatch.setattr(
        contract,
        "_apply_grounded_indexed_range_names",
        lambda current: (current, []),
    )
    monkeypatch.setattr(
        contract,
        "_semantic_candidate_gate",
        lambda *_args, **_kwargs: (True, {"accepted": True, "reasons": []}),
    )

    async def _orchestrate(current, **_kwargs):
        current.meta = {
            **(current.meta or {}),
            "capability_orchestration_audit": {
                "after_errors": 0,
                "after_warnings": 2,
            },
        }
        return current

    monkeypatch.setattr(contract, "orchestrate_flow_capabilities", _orchestrate)

    def _unexpected_validation(_spec):
        raise AssertionError("plan submission repeated orchestration validation")

    monkeypatch.setattr(contract, "validate_flow_spec", _unexpected_validation)

    updated = await contract.apply_recording_agent_submission(
        spec,
        submission={"ops": []},
        mode="plan",
    )

    rounds = ((updated.meta or {}).get("recording_agent_session") or {}).get("rounds") or []
    assert rounds == [{
        "round": 1,
        "stage": "planner",
        "passed": True,
        "errors": 0,
        "warnings": 2,
    }]


def test_capability_confirmation_hashes_prepare_flow_once(monkeypatch) -> None:
    from dano.execution.page.capability_repair import _auto_confirm_ready_capabilities
    from dano.execution.page.flow_spec import FlowCapability, FlowSpec

    spec = FlowSpec(capabilities=[
        FlowCapability(name="query_docs", confidence=0.9),
        FlowCapability(name="create_doc", confidence=0.9),
        FlowCapability(name="delete_doc", confidence=0.9),
    ])
    prepare_calls = 0

    def _prepare(current):
        nonlocal prepare_calls
        prepare_calls += 1
        return current

    monkeypatch.setattr(
        "dano.execution.page.flow_release.prepare_flow_spec_for_publish",
        _prepare,
    )
    hash_calls: list[tuple[str, bool]] = []

    def _confirmation_hash(_spec, capability, *, prepared=False):
        hash_calls.append((capability.name, prepared))
        return f"hash:{capability.name}"

    monkeypatch.setattr(
        "dano.execution.page.capability_repair._capability_confirmation_hash",
        _confirmation_hash,
    )

    updated = _auto_confirm_ready_capabilities(spec)

    assert prepare_calls == 1
    assert hash_calls == [
        ("query_docs", True),
        ("create_doc", True),
        ("delete_doc", True),
    ]
    assert [cap.confirmation_hash for cap in updated.capabilities] == [
        "hash:query_docs",
        "hash:create_doc",
        "hash:delete_doc",
    ]


async def test_plan_does_not_repeat_post_orchestration_sync(monkeypatch) -> None:
    import dano.execution.page.recording_agent_contract as contract
    from dano.execution.page.flow_spec import FlowSpec

    spec = FlowSpec(meta={"current_version": 1})
    monkeypatch.setattr(
        contract,
        "_apply_grounded_indexed_range_names",
        lambda current: (current, []),
    )

    async def _orchestrate(current, **_kwargs):
        current.meta = {
            **(current.meta or {}),
            "capability_orchestration_audit": {
                "after_errors": 0,
                "after_warnings": 0,
            },
        }
        return current

    monkeypatch.setattr(contract, "orchestrate_flow_capabilities", _orchestrate)
    sync_calls = 0
    confirm_calls = 0

    def _sync(current):
        nonlocal sync_calls
        sync_calls += 1
        return current

    def _confirm(current):
        nonlocal confirm_calls
        confirm_calls += 1
        return current

    monkeypatch.setattr(contract, "sync_flow_spec_models", _sync)
    monkeypatch.setattr(contract, "_auto_confirm_ready_capabilities", _confirm)

    await contract.apply_recording_agent_submission(
        spec,
        submission={"ops": []},
        mode="plan",
    )

    assert sync_calls == 1
    assert confirm_calls == 1


def test_grounded_indexed_range_naming_is_idempotent() -> None:
    from dano.execution.page.flow_materialization.field_contracts.common import (
        _apply_grounded_indexed_range_names,
    )
    from dano.execution.page.flow_spec import FlowSpec, FlowStep, ParamField

    spec = FlowSpec(steps=[FlowStep(
        step_id="query_docs",
        method="GET",
        path="/docs/page",
        params=[
            ParamField(
                path="query.createTime[0]",
                key="createTime[0]",
                type="date",
                category="user_param",
                source_kind="caller_input",
                exposed_to_user=True,
            ),
            ParamField(
                path="query.createTime[1]",
                key="createTime[1]",
                type="date",
                category="user_param",
                source_kind="caller_input",
                exposed_to_user=True,
            ),
        ],
    )])

    named, first_changes = _apply_grounded_indexed_range_names(spec)
    evidence_counts = [len(param.evidence) for param in named.steps[0].params]
    repeated, second_changes = _apply_grounded_indexed_range_names(named)

    assert len(first_changes) == 2
    assert second_changes == []
    assert [param.key for param in repeated.steps[0].params] == [
        "查询开始时间",
        "查询结束时间",
    ]
    assert [len(param.evidence) for param in repeated.steps[0].params] == evidence_counts


def test_semantic_plan_exact_request_ids_materialize_before_compilation() -> None:
    from dano.execution.page.flow_materialization.builder import (
        _materialize_semantic_plan_request_refs,
    )

    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_existing",
                method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1",
                sequence=1,
                role="business_get",
            ),
            _req(
                "req_new_command",
                method="DELETE",
                url="http://example.test/admin-api/doc/delete?ids=7",
                sequence=2,
                role="support",
                keep=False,
                action="act_delete",
            ),
        ],
        page_context=PAGE,
    )
    assert not any(
        (step.source_meta or {}).get("request_id") == "req_new_command"
        for step in spec.steps
    )
    plan = {
        "capabilities": [{
            "name": "delete_doc",
            "title": "删除单据",
            "kind": "delete",
            "anchor_step_id": "req_new_command",
            "request_refs": [{"step_id": "req_new_command", "usage": "execute"}],
        }],
    }

    assert _materialize_semantic_plan_request_refs(spec, plan) is True
    promoted = next(
        step for step in spec.steps
        if (step.source_meta or {}).get("request_id") == "req_new_command"
    )
    capability = plan["capabilities"][0]
    assert capability["anchor_step_id"] == promoted.step_id
    assert capability["request_refs"][0] == {
        "request_id": "req_new_command",
        "step_id": promoted.step_id,
        "usage": "execute",
    }


async def test_incremental_plan_compiles_new_exact_request_anchor() -> None:
    from dano.execution.page.flow_spec import orchestrate_flow_capabilities

    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_existing",
                method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1",
                sequence=1,
                role="business_get",
            ),
            _req(
                "req_new_command",
                method="DELETE",
                url="http://example.test/admin-api/doc/delete?ids=7",
                sequence=2,
                role="support",
                keep=False,
                action="act_delete",
            ),
        ],
        page_context=PAGE,
    )
    spec.request_facts.analysis["req_new_command"].role = "business_write"
    spec.request_facts.analysis["req_new_command"].keep = True
    existing_step_id = spec.steps[0].step_id
    plan = {
        "business_understanding": {
            "business_name": "单据管理",
            "summary": "查询和删除单据",
        },
        "capabilities": [
            {
                "name": "query_docs",
                "title": "查询单据",
                "kind": "query",
                "anchor_step_id": existing_step_id,
                "request_refs": [{"step_id": existing_step_id, "usage": "execute"}],
            },
            {
                "name": "delete_doc",
                "title": "删除单据",
                "kind": "delete",
                "anchor_step_id": "req_new_command",
                "request_refs": [{"step_id": "req_new_command", "usage": "execute"}],
            },
        ],
        "unresolved_items": [],
    }

    updated = await orchestrate_flow_capabilities(
        spec,
        submission={"semantic_plan": plan, "ops": []},
        generation_mode="initial",
    )

    assert {capability.name for capability in updated.capabilities} == {
        "query_docs",
        "delete_doc",
    }


def test_recording_delta_includes_related_field_evidence() -> None:
    requests = [
        _req(
            "req_update",
            method="PUT",
            url="http://example.test/admin-api/doc/update",
            sequence=1,
            role="business_write",
            action="act_edit",
            body={"id": 1, "remark": "keep"},
        ),
    ]

    class _Recorder:
        def captured_all_requests(self):
            return requests

        def recorded_page_events(self):
            return []

        def recorded_field_evidence(self):
            return [{
                "label": "备注",
                "field": "remark",
                "field_aliases": ["remark"],
                "value": "keep",
                "op": "fill",
                "request_id": "req_update",
                "page_id": "page_1",
                "frame_id": "frame_1",
                "in_dialog": True,
                "page_context": PAGE,
            }]

    delta = recording_delta(_Recorder(), since_seq=0, limit=10)
    assert any(
        isinstance(item, dict) and item.get("label") == "备注"
        for item in delta.get("field_evidence") or []
    )


def test_compact_model_payload_can_keep_list_tail() -> None:
    compacted = compact_model_payload(list(range(47)), max_items=40, list_keep="tail")
    values = [item for item in compacted if not isinstance(item, dict)]
    assert values[0] == 7
    assert values[-1] == 46


def test_recording_delta_can_page_compact_history_before_live_floor() -> None:
    requests = [
        _req(
            f"req_{index}",
            method="GET",
            url=f"http://example.test/admin-api/doc/page?pageNo={index}",
            sequence=index,
            role="read_context",
            keep=False,
        )
        for index in range(6)
    ]

    class _Recorder:
        def captured_all_requests(self):
            return requests

        def recorded_page_events(self):
            return []

        def recorded_field_evidence(self):
            return []

    delta = recording_delta(
        _Recorder(), since_seq=0, limit=4, stop_before=5, compact=True,
    )
    assert delta["compact_history"] is True
    assert len(delta["requests"]) == 4
    assert "response_json" not in delta["requests"][0]
    assert delta["next_seq"] == 4
    assert delta["has_more"] is True


def test_spec_fields_count_semantic_plan_before_materialization() -> None:
    spec = to_flow_spec(
        captured_requests=[
            _req(
                "req_update",
                method="PUT",
                url="http://example.test/admin-api/doc/update",
                sequence=1,
                role="business_write",
                action="act_edit",
                body={"id": 1},
            ),
        ],
        page_context=PAGE,
    )
    spec.capabilities = []
    spec.meta = {
        **(spec.meta or {}),
        "capability_model": {
            "semantic_plan": {
                "capabilities": [
                    {"name": "edit_doc", "title": "编辑单据"},
                    {"name": "approve_doc", "title": "审批单据"},
                ]
            }
        },
    }
    fields = _spec_fields(spec)
    assert fields["capability_count"] == 2
    assert fields["capability_names"] == ["edit_doc", "approve_doc"]


def test_compact_keeps_same_path_with_different_record_query_values() -> None:
    from dano.execution.page.recording_facts import _compact_repeated_endpoint_observations

    first = {
        "request_id": "req_36",
        "method": "GET",
        "path": "/admin-api/doc/get",
        "role": "read_context",
        "keep": False,
        "query": {"id": "36"},
        "query_paths": ["id"],
    }
    second = {
        "request_id": "req_37",
        "method": "GET",
        "path": "/admin-api/doc/get",
        "role": "read_context",
        "keep": False,
        "query": {"id": "37"},
        "query_paths": ["id"],
    }
    compacted = _compact_repeated_endpoint_observations([first, second])
    request_ids = {str(item.get("request_id") or "") for item in compacted}
    assert request_ids == {"req_36", "req_37"}


def test_evidence_id_is_stable_when_recorded_value_changes() -> None:
    first = {
        "label": "数量",
        "field": "count",
        "field_aliases": ["count"],
        "value": "1",
        "op": "fill",
        "page_id": "page_1",
        "frame_id": "frame_1",
        "in_dialog": True,
        "page_context": PAGE,
        "action_id": "act_fill",
        "event_id": "ev_1",
    }
    second = {
        **first,
        "value": "3",
    }
    assert _evidence_id(first) == _evidence_id(second)
    other_action = {
        **first,
        "value": "3",
        "action_id": "act_fill_2",
        "event_id": "ev_2",
    }
    assert _evidence_id(first) != _evidence_id(other_action)
