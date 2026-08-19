"""Repair op quality gate: mark_unverified is not rolled back with unrelated ops."""

from __future__ import annotations

import pytest

from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowStep,
    ParamField,
    apply_recording_agent_submission,
)


def _spec() -> FlowSpec:
    return FlowSpec(
        tenant="tenant",
        subsystem="oa",
        steps=[
            FlowStep(
                step_id="step_edit",
                method="PUT",
                path="/erp/sale-order/update",
                params=[
                    ParamField(path="query.remark", key="remark", value="", source_kind="unknown"),
                    ParamField(path="query.note", key="note", value="keep", source_kind="user_input"),
                ],
            ),
        ],
    )


def _op_results(spec: FlowSpec) -> list[dict]:
    return list(((spec.meta or {}).get("recording_agent_session") or {}).get("op_results") or [])


@pytest.mark.asyncio
async def test_mark_unverified_skips_quality_gate() -> None:
    spec = await apply_recording_agent_submission(
        _spec(),
        submission={
            "ops": [{
                "op": "mark_unverified",
                "target_kind": "write_verify",
                "target_id": "step_edit",
                "reason": "无法证明提交已生效",
            }],
        },
        mode="repair",
    )
    results = _op_results(spec)
    assert results[0]["op"] == "mark_unverified"
    assert results[0]["status"] == "applied"
    assert any(
        item.get("target_kind") == "write_verify" and item.get("target_id") == "step_edit"
        for item in (spec.meta or {}).get("unverified") or []
    )


@pytest.mark.asyncio
async def test_rejected_constant_includes_allowed_values() -> None:
    spec = await apply_recording_agent_submission(
        _spec(),
        submission={
            "ops": [{
                "op": "set_param_source",
                "step_id": "step_edit",
                "path": "query.remark",
                "source_kind": "constant",
                "reason": "没有录制值不能当常量",
                "evidence_refs": ["step_edit"],
            }],
        },
        mode="repair",
    )
    result = _op_results(spec)[0]
    assert result["status"] == "rejected"
    assert result["allowed_values"]["field"] == "source_kind"
    assert "caller_input" in result["allowed_values"]["allowed"]
    assert "user_input" not in result["allowed_values"]["allowed"]
    assert "use caller_input" in result["reason"]


@pytest.mark.asyncio
async def test_illegal_constant_rejects_only_that_op() -> None:
    spec = await apply_recording_agent_submission(
        _spec(),
        submission={
            "ops": [
                {
                    "op": "set_param_source",
                    "step_id": "step_edit",
                    "path": "query.remark",
                    "source_kind": "constant",
                    "reason": "空值常量",
                    "evidence_refs": ["step_edit"],
                },
                {
                    "op": "set_param_source",
                    "step_id": "step_edit",
                    "path": "query.note",
                    "source_kind": "caller_input",
                    "reason": "调用方提供",
                    "evidence_refs": ["step_edit"],
                },
            ],
        },
        mode="repair",
    )
    results = {
        str((item.get("requested_target") or {}).get("wire_path") or item.get("op")): item
        for item in _op_results(spec)
    }
    assert results["query.remark"]["status"] == "rejected"
    assert results["query.note"]["status"] == "applied"
    versions = {item["flow_version_after"] for item in _op_results(spec)}
    assert len(versions) == 1
    note = next(param for param in spec.steps[0].params if param.path == "query.note")
    assert note.source_kind == "user_input"
    remark = next(param for param in spec.steps[0].params if param.path == "query.remark")
    assert remark.source_kind != "constant"
