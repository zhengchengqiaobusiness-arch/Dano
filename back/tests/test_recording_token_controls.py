from __future__ import annotations

import asyncio
import json

import pytest

from dano.execution.page.flow_spec import FlowSpec, FlowStep, _semantic_fact_snapshot
from dano.execution.page.request_capture import bounded_response_sample, normalized_leaf_paths
from dano.infra.llm_control import (
    LLMBudgetExceeded,
    cached_singleflight,
    clear_memory_llm_cache,
    estimate_message_tokens,
    llm_budget_scope,
    reserve_llm_tokens,
)
from dano.review.board import OpenAICompatClient, ReviewBoard, _build_user


def _large_response(rows: int = 1000, fields: int = 20) -> dict:
    return {
        "code": 200,
        "data": {
            "records": [
                {f"field_{column}": f"value_{row}_{column}" for column in range(fields)}
                for row in range(rows)
            ],
            "total": rows,
        },
    }


def test_large_array_paths_are_normalized_and_bounded() -> None:
    paths = normalized_leaf_paths(_large_response())

    assert len(paths) == 22
    assert "data.records[].field_0" in paths
    assert not any("[0]" in path or "[999]" in path for path in paths)


def test_semantic_snapshot_does_not_scale_with_record_count() -> None:
    small = FlowSpec(steps=[FlowStep(step_id="query", method="GET", path="/list",
                                     response_json=_large_response(rows=3))])
    large = FlowSpec(steps=[FlowStep(step_id="query", method="GET", path="/list",
                                     response_json=_large_response(rows=10_000))])

    small_payload = json.dumps(_semantic_fact_snapshot(small), ensure_ascii=False)
    large_payload = json.dumps(_semantic_fact_snapshot(large), ensure_ascii=False)

    assert len(large_payload) <= len(small_payload) + 100
    assert len(large_payload) < 10_000


def test_client_response_sample_keeps_shape_without_full_list() -> None:
    sample = bounded_response_sample(_large_response(rows=1000))
    records = sample["data"]["records"]

    assert len(records) == 4
    assert records[-1] == {"__dano_omitted_items__": 997}


def test_budget_rejects_single_request_and_session_overflow() -> None:
    with pytest.raises(LLMBudgetExceeded, match="单次输入超限"):
        reserve_llm_tokens(101, purpose="test", per_request_limit=100)

    with llm_budget_scope(150):
        reserve_llm_tokens(100, purpose="first", per_request_limit=120)
        with pytest.raises(LLMBudgetExceeded, match="输入预算不足"):
            reserve_llm_tokens(60, purpose="second", per_request_limit=120)


@pytest.mark.asyncio
async def test_cache_singleflight_calls_producer_once() -> None:
    clear_memory_llm_cache()
    calls = 0

    async def producer():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"ok": True}, {"prompt_tokens": 12, "completion_tokens": 2}

    results = await asyncio.gather(*[
        cached_singleflight(
            "same-key",
            model="fake",
            purpose="test",
            ttl_s=60,
            producer=producer,
        )
        for _ in range(5)
    ])

    assert calls == 1
    assert sum(1 for _response, _usage, hit in results if not hit) == 1


class _CombinedFakeClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete_json(self, **_kwargs):
        self.calls += 1
        return {"verdicts": {
            role: {"passed": True, "reasons": []}
            for role in ("acceptance", "security", "compliance")
        }}


@pytest.mark.asyncio
async def test_same_model_review_is_one_three_dimension_call() -> None:
    client = _CombinedFakeClient()
    board = ReviewBoard(
        client=client,
        models={role: "same" for role in ("acceptance", "security", "compliance")},
        max_retries=1,
    )

    verdicts = await board.review(
        asset_type="page_script",
        asset_key="submit",
        body={"action": "submit", "api_request": {"method": "POST", "path": "/submit"}},
    )

    assert client.calls == 1
    assert [verdict.role for verdict in verdicts] == ["acceptance", "security", "compliance"]
    assert all(verdict.passed for verdict in verdicts)


class _NonRetryableFakeClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete_json(self, **_kwargs):
        self.calls += 1
        raise ValueError("invalid structured output")


@pytest.mark.asyncio
async def test_invalid_structured_review_is_not_blindly_retried() -> None:
    client = _NonRetryableFakeClient()
    board = ReviewBoard(
        client=client,
        models={role: "same" for role in ("acceptance", "security", "compliance")},
        max_retries=3,
    )

    verdicts = await board.review(asset_type="page_script", asset_key="x", body={})

    assert client.calls == 1
    assert not any(verdict.passed for verdict in verdicts)


def test_review_dto_drops_large_responses_and_duplicate_contracts() -> None:
    capability = {
        "name": "submit",
        "kind": "submit",
        "step_ids": ["submit"],
        "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}},
    }
    body = {
        "action": "submit",
        "capabilities": [capability],
        "api_request": {
            "steps": [{"step_id": "submit", "method": "POST", "path": "/submit",
                       "response_json": _large_response()}],
            "capabilities": [capability],
            "capability_contracts": [capability] * 20,
            "_release_snapshot": {"flow_spec": {"huge": "x" * 100_000}},
        },
    }

    user = _build_user("page_script", "submit", body, [])

    assert len(user) < 10_000
    assert "value_999_19" not in user
    assert "capability_contracts" not in user


class _FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "not-json"}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 1}}


class _FakeAsyncClient:
    calls = 0

    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, *_args, **_kwargs):
        type(self).calls += 1
        return _FakeResponse()


@pytest.mark.asyncio
async def test_billable_invalid_json_is_not_sent_again(monkeypatch) -> None:
    import httpx

    clear_memory_llm_cache()
    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = OpenAICompatClient(api_key="test", base_url="https://example.invalid/v1")
    messages = [{"role": "user", "content": "return json"}]
    assert estimate_message_tokens(messages) < 100

    with pytest.raises(ValueError):
        await client.complete_json_messages(model="fake", messages=messages, timeout_s=1)

    assert _FakeAsyncClient.calls == 1
