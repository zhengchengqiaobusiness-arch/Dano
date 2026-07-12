import pytest

from dano.execution.page.repair_ops import (
    apply_deterministic_repairs,
    collect_capability_findings,
    collect_repair_findings,
)
from dano.onboarding.repair import run_repair_loop


def _capability(name, kind="submit", *, inputs=None, outputs=None, batch=False):
    return {
        "name": name,
        "capability_id": f"cap-{name}",
        "kind": kind,
        "step_ids": ["submit"],
        "compiled_step_ids": ["submit"],
        "inputs": inputs or [],
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": outputs or {}},
        "execution_contract": {
            "call_order": [{"step_id": "submit", "method": "POST", "path": "/submit"}],
            "batch": {"enabled": batch, "items_field": "entries"},
            "return": [{"kind": "final_response", "step_id": "submit", "response_path": "response"}],
        },
    }


def test_capability_findings_cover_fields_outputs_relations_batch_and_goal():
    query = _capability("query_status", outputs={"count": {"type": "number"}})
    batch = _capability("submit_batch", "submit_batch", inputs=[{
        "key": "entries", "type": "array", "required": True, "exposed_to_caller": True,
    }])
    batch["input_schema"] = {
        "type": "object", "properties": {}, "required": ["ghost"],
    }
    batch["execution_contract"]["batch"]["enabled"] = False
    request = {
        "capabilities": [query, batch],
        "capability_relations": [{
            "relation_id": "bad-type", "from_capability": "query_status", "from_output": "count",
            "to_capability": "submit_batch", "to_input": "entries", "confirmed": True,
        }],
        "goal": {
            "required_inputs": ["missing_business_field"],
            "output_expectation": ["receipt_id"],
            "capabilities": ["gone"],
        },
    }

    kinds = {finding["kind"] for finding in collect_capability_findings(request)}

    assert {
        "capability_required_field_missing",
        "capability_input_schema_missing",
        "capability_batch_disabled",
        "capability_batch_entries_missing",
        "capability_relation_type_mismatch",
        "goal_capability_missing",
        "goal_required_input_missing",
        "goal_output_missing",
    } <= kinds


def test_goal_natural_language_output_expectation_is_not_treated_as_schema_path():
    request = {
        "capabilities": [_capability("submit", outputs={"result": {"type": "object"}})],
        "goal": {"output_expectation": ["返回审批结果并说明是否成功"]},
    }

    findings = collect_capability_findings(request)

    assert not any(item["kind"] == "goal_output_missing" for item in findings)


def test_caller_decision_relation_does_not_require_field_mapping():
    request = {
        "capabilities": [_capability("query_status"), _capability("submit")],
        "capability_relations": [{
            "relation_id": "decision",
            "type": "caller_decision",
            "mode": "caller_decision",
            "from_capability": "query_status",
            "to_capability": "submit",
            "confirmed": True,
        }],
    }

    assert not collect_capability_findings(request)
    repaired, _ = apply_deterministic_repairs(request)
    assert repaired["capability_relations"][0]["relation_id"] == "decision"


def test_deterministic_repairs_complete_contract_and_keep_confirmed_relation_issue():
    query = _capability("query_status", outputs={"count": {"type": "number"}})
    batch = _capability("submit_batch", "submit_batch", inputs=[{
        "key": "entries", "type": "array", "required": True, "exposed_to_caller": True,
    }])
    batch["input_schema"]["required"] = ["ghost"]
    batch["execution_contract"]["batch"]["enabled"] = False
    batch.pop("output_mapping", None)
    request = {
        "capabilities": [query, batch],
        "capability_relations": [
            {"relation_id": "dangling", "from_capability": "gone", "to_capability": "submit_batch"},
            {"relation_id": "confirmed-bad", "from_capability": "query_status", "from_output": "count",
             "to_capability": "submit_batch", "to_input": "entries", "confirmed": True},
        ],
        "capability_graph": {"relations": []},
        "goal": {"capabilities": ["gone", "query_status"]},
    }

    repaired, applied = apply_deterministic_repairs(request)
    repaired_batch = repaired["capabilities"][1]

    assert repaired_batch["input_schema"]["required"] == ["entries"]
    assert repaired_batch["input_schema"]["properties"]["entries"]["type"] == "array"
    assert repaired_batch["execution_contract"]["batch"]["enabled"] is True
    assert repaired_batch["output_mapping"][0]["step_id"] == "submit"
    assert [r["relation_id"] for r in repaired["capability_relations"]] == ["confirmed-bad"]
    assert repaired["capability_graph"]["relations"] == repaired["capability_relations"]
    assert repaired["goal"]["capabilities"] == ["query_status", "submit_batch"]
    assert applied
    assert {f["kind"] for f in collect_repair_findings(repaired)} == {
        "capability_batch_item_schema_missing",
        "capability_relation_type_mismatch",
    }


@pytest.mark.asyncio
async def test_repair_loop_runs_deterministic_repairs_before_proposer():
    capability = _capability("submit_batch", "submit_batch")
    capability["input_schema"] = {"type": "object", "properties": {
        "entries": {"type": "array", "items": {
            "type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"],
        }},
    }, "required": ["entries"]}
    request = {
        "capabilities": [capability],
        "goal": {"capabilities": ["stale"]},
    }
    calls = []

    async def proposer(*args):
        calls.append(args)
        return []

    repaired, rounds, history, remaining = await run_repair_loop(request, proposer)

    assert rounds == 0
    assert calls == []
    assert remaining == []
    assert repaired["goal"]["capabilities"] == ["submit_batch"]
    assert history[0]["deterministic"]
