"""Stage 6: option source must match a unique request, not the first same path."""

from __future__ import annotations

from urllib.parse import urlparse

from dano.execution.page.capability_compiler import (
    _option_source_request_ids,
    _step_matching_request,
)
from dano.execution.page.flow_spec import (
    FlowLink,
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
)


def _spec(*steps: FlowStep, facts: list[RequestFact] | None = None, links: list[FlowLink] | None = None) -> FlowSpec:
    spec = FlowSpec(tenant="t", subsystem="oa")
    spec.steps = list(steps)
    if facts:
        spec.request_facts.requests = list(facts)
    if links:
        spec.links = list(links)
    return spec


def _get(step_id: str, url: str, request_id: str, **meta) -> FlowStep:
    parsed = urlparse(url)
    return FlowStep(
        step_id=step_id,
        method="GET",
        url=url,
        path=parsed.path or url,
        source_meta={"request_id": request_id, **meta},
    )


def _write(source: dict) -> FlowStep:
    return FlowStep(
        step_id="step_write",
        method="POST",
        url="http://a.test/doc/create",
        path="/doc/create",
        source_meta={"request_id": "req_write"},
        params=[
            ParamField(
                path="typeId",
                key="typeId",
                source_kind="api_option",
                source=source,
            )
        ],
    )


def test_same_path_different_host_does_not_pick_first() -> None:
    spec = _spec(
        _get("step_b", "http://b.test/api/options", "req_b", page_id="p1", frame_id="f1"),
        _get("step_a", "http://a.test/api/options", "req_a", page_id="p1", frame_id="f1"),
        _write({"source_url": "http://a.test/api/options", "kind": "api_option"}),
    )
    ids = _option_source_request_ids(spec, [spec.steps[-1]], {})
    assert ids == ["req_a"]


def test_same_path_get_and_post_stay_unresolved() -> None:
    spec = _spec(
        FlowStep(
            step_id="step_get",
            method="GET",
            url="http://a.test/api/options",
            path="/api/options",
            source_meta={"request_id": "req_get", "page_id": "p1", "frame_id": "f1"},
        ),
        FlowStep(
            step_id="step_post",
            method="POST",
            url="http://a.test/api/options",
            path="/api/options",
            source_meta={"request_id": "req_post", "page_id": "p1", "frame_id": "f1"},
        ),
        _write({"source_url": "/api/options", "kind": "api_option"}),
    )
    ids = _option_source_request_ids(spec, [spec.steps[-1]], {})
    assert "req_get" not in ids
    assert "req_post" not in ids


def test_same_path_different_transaction_does_not_pick_first() -> None:
    spec = _spec(
        _get(
            "step_tx1", "http://a.test/api/options", "req_tx1",
            page_id="p1", frame_id="f1", trigger_transaction_id="tx_1",
        ),
        _get(
            "step_tx2", "http://a.test/api/options", "req_tx2",
            page_id="p1", frame_id="f1", trigger_transaction_id="tx_2",
        ),
        _write({
            "source_url": "http://a.test/api/options",
            "kind": "api_option",
            "transaction_id": "tx_2",
        }),
    )
    ids = _option_source_request_ids(spec, [spec.steps[-1]], {})
    assert ids == ["req_tx2"]


def test_request_id_wins_over_path() -> None:
    spec = _spec(
        _get("step_a", "http://a.test/api/options", "req_a"),
        _get("step_b", "http://a.test/api/options", "req_b"),
        _write({
            "source_url": "http://a.test/api/options",
            "source_request_id": "req_b",
            "kind": "api_option",
        }),
    )
    ids = _option_source_request_ids(spec, [spec.steps[-1]], {})
    assert ids == ["req_b"]


def test_unique_composite_identity_matches() -> None:
    spec = _spec(
        _get(
            "step_opt", "http://a.test/api/options?type=leave", "req_opt",
            page_id="p1", frame_id="f1", trigger_transaction_id="tx_create",
        ),
        _write({
            "source_url": "http://a.test/api/options?type=leave",
            "kind": "api_option",
            "page_id": "p1",
            "frame_id": "f1",
            "transaction_id": "tx_create",
        }),
    )
    ids = _option_source_request_ids(spec, [spec.steps[-1]], {})
    assert ids == ["req_opt"]


def test_multiple_equal_candidates_do_not_pick_first() -> None:
    spec = _spec(
        _get("step_1", "http://a.test/api/options", "req_1", page_id="p1", frame_id="f1"),
        _get("step_2", "http://a.test/api/options", "req_2", page_id="p1", frame_id="f1"),
        _write({"source_url": "http://a.test/api/options", "kind": "api_option"}),
    )
    ids = _option_source_request_ids(spec, [spec.steps[-1]], {})
    assert ids == []


def test_confirmed_flow_link_option_source_is_used() -> None:
    write = _write({"kind": "api_option", "source_url": "http://a.test/api/options"})
    spec = _spec(
        _get("step_wrong", "http://b.test/api/options", "req_wrong"),
        _get("step_opt", "http://a.test/api/options", "req_opt"),
        write,
        links=[FlowLink(
            source_step_id="step_opt",
            target_step_id="step_write",
            confirmed=True,
            value_binding={"option_source": {"source_request_id": "req_opt"}},
        )],
    )
    ids = _option_source_request_ids(spec, [write], {})
    assert "req_opt" in ids


def test_step_matching_request_does_not_use_first_same_path() -> None:
    spec = _spec(
        _get("step_b", "http://b.test/api/options", "req_b"),
        _get("step_a", "http://a.test/api/options", "req_a"),
        facts=[
            RequestFact(
                request_id="req_missing_step",
                method="GET",
                url="http://a.test/api/options",
                path="/api/options",
                page_id="p1",
                frame_id="f1",
            ),
        ],
    )
    by_request = {"req_a": spec.steps[1], "req_b": spec.steps[0]}
    by_step = {step.step_id: step for step in spec.steps}
    matched = _step_matching_request(spec, "req_missing_step", by_request, by_step)
    assert matched is spec.steps[1]
    assert matched is not spec.steps[0]
