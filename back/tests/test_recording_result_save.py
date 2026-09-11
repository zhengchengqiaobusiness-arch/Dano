from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dano.execution.page.flow_spec_core.models import FlowSpecConflictError
from dano.onboarding.pi_check_sidecar import RecordingBridgeContext, pi_result_storage_body
from dano.onboarding.recording_results import apply_recording_result_edits
from dano.onboarding.recording_workflow import _draft_fingerprint

RECORDING_ID = "rec_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _pi_contract() -> dict:
    return {
        "title": "查询日报",
        "business_understanding": {"summary": "先查再写"},
        "unresolved": [{"item": "详情页数字链"}],
        "capabilities": [
            {
                "capability_id": "cap_query",
                "name": "查询应填未填",
                "title": "查询应填未填",
                "intent": "按周期查统计",
                "kind": "query",
                "step_ids": ["step_stats"],
                "request_refs": [
                    {"step_id": "step_stats", "usage": "execute", "method": "GET", "path": "/stats"},
                ],
            },
        ],
        "steps": [
            {
                "step_id": "step_stats",
                "method": "GET",
                "path": "/stats",
                "params": [
                    {
                        "key": "year",
                        "path": "query.year",
                        "label": "年",
                        "type": "number",
                        "source_kind": "user_input",
                    },
                ],
            },
        ],
    }


def _pi_body(contract: dict | None = None) -> dict:
    draft = contract or _pi_contract()
    return pi_result_storage_body(
        action="action_1",
        title="查询日报",
        goal="产出能力",
        tenant="admin",
        subsystem="oa",
        draft=draft,
        request_count=1,
        recording_id=RECORDING_ID,
    )


def test_pi_capability_page_can_save_with_recording_id_fingerprint() -> None:
    body = _pi_body()
    result = apply_recording_result_edits(
        body,
        [{
            "op": "update_capability",
            "actor": "user",
            "capability_id": "cap_query",
            "capability_name": "查询应填未填",
            "field": "title",
            "value": "查询应填 / 未填数量",
        }],
        expected_fingerprint=RECORDING_ID,
    )

    spec = result["flow_spec"]
    assert spec["capabilities"][0]["title"] == "查询应填 / 未填数量"
    assert spec["business_understanding"] == {"summary": "先查再写"}
    assert spec["unresolved"] == [{"item": "详情页数字链"}]
    assert result["fingerprint"] == _draft_fingerprint(spec)
    assert result["fingerprint"] != RECORDING_ID


def test_pi_capability_page_can_edit_param_without_flow_spec_roundtrip() -> None:
    body = _pi_body()
    result = apply_recording_result_edits(
        body,
        [{
            "op": "update",
            "actor": "user",
            "step_id": "step_stats",
            "param_path": "query.year",
            "field": "label",
            "value": "统计年份",
        }],
        expected_fingerprint=_draft_fingerprint(body["flow_spec"]),
    )

    param = result["flow_spec"]["steps"][0]["params"][0]
    assert param["label"] == "统计年份"
    assert param["key"] == "year"
    assert result["flow_spec"]["business_understanding"]["summary"] == "先查再写"


@pytest.mark.asyncio
async def test_snapshot_replaces_recording_id_fingerprint_with_content_hash() -> None:
    draft = {"title": "查询", "capabilities": [{"name": "search_docs"}]}
    context = RecordingBridgeContext()
    rewritten = await context.rewrite_upstream(json.dumps({
        "type": "snapshot",
        "snapshot": {
            "run_id": "rec_abc",
            "draft": draft,
            "draft_fingerprint": "rec_abc",
            "progress": {"label": "PI 已提交 1 项能力"},
        },
    }))
    payload = json.loads(rewritten)
    assert payload["snapshot"]["draft_fingerprint"] == _draft_fingerprint(draft)
    assert payload["snapshot"]["draft_fingerprint"] != "rec_abc"


@pytest.mark.asyncio
async def test_recording_result_saved_includes_content_fingerprint() -> None:
    draft = {"capabilities": [{"name": "search_docs"}], "title": "查询"}
    saved_id = uuid4()

    async def fake_detail(_result_id: str) -> dict:
        return {"id": "recording_abc", "draft": draft, "request_count": 2}

    async def fake_persist(**_kwargs):
        return SimpleNamespace(
            asset_draft_id=saved_id,
            asset_key="recording-result:action_1",
            created_at=None,
            body={
                "action": "action_1",
                "title": "查询",
                "goal": {"text": "产出能力"},
                "capability_count": 1,
                "request_count": 2,
                "published": False,
                "created_at": "2026-09-04T00:00:00+00:00",
            },
        )

    context = RecordingBridgeContext(
        tenant="admin",
        subsystem="oa",
        title="查询",
        goal="产出能力",
        action="action_1",
        persist=fake_persist,
        fetch_detail=fake_detail,
    )
    rewritten = await context.rewrite_upstream(
        '{"type":"recording_result_saved","result":{"id":"recording_abc","action":"action_1","request_count":2}}'
    )
    payload = json.loads(rewritten)
    assert payload["result"]["id"] == str(saved_id)
    assert payload["result"]["draft_fingerprint"] == _draft_fingerprint(draft)


def test_pi_capability_page_rejects_stale_fingerprint() -> None:
    body = _pi_body()
    with pytest.raises(FlowSpecConflictError):
        apply_recording_result_edits(
            body,
            [{
                "op": "update_capability",
                "actor": "user",
                "capability_id": "cap_query",
                "capability_name": "查询应填未填",
                "field": "title",
                "value": "新标题",
            }],
            expected_fingerprint="stale-fingerprint",
        )
