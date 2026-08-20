"""Stage 6: option source must match a unique request, not the first same path."""

from __future__ import annotations

import json
from urllib.parse import urlparse

import pytest

from dano.execution.page.capability_compiler import (
    _option_source_request_ids,
    _step_matching_request,
)
from dano.execution.page.flow_spec import (
    apply_flow_edits,
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


def test_unique_path_candidate_with_conflicting_transaction_stays_unresolved() -> None:
    spec = _spec(
        _get(
            "step_other_tx", "http://random.invalid/v9/choices", "req_other_tx",
            page_id="page_a", frame_id="frame_a", trigger_transaction_id="tx_other",
        ),
        _write({
            "source_url": "http://random.invalid/v9/choices",
            "source_method": "GET",
            "page_id": "page_a",
            "frame_id": "frame_a",
            "transaction_id": "tx_expected",
            "kind": "api_option",
        }),
    )
    assert _option_source_request_ids(spec, [spec.steps[-1]], {}) == []


def test_unique_path_candidate_with_conflicting_query_signature_stays_unresolved() -> None:
    spec = _spec(
        _get(
            "step_other_query", "http://random.invalid/v9/choices?scope=other", "req_other_query",
            page_id="page_a", frame_id="frame_a", trigger_transaction_id="tx_a",
        ),
        _write({
            "source_url": "http://random.invalid/v9/choices?scope=expected",
            "source_method": "GET",
            "page_id": "page_a",
            "frame_id": "frame_a",
            "transaction_id": "tx_a",
            "kind": "api_option",
        }),
    )
    assert _option_source_request_ids(spec, [spec.steps[-1]], {}) == []


def test_request_id_does_not_override_a_conflicting_host_constraint() -> None:
    spec = _spec(
        _get("step_a", "http://a.invalid/v9/choices", "req_a"),
        _write({
            "source_request_id": "req_a",
            "source_url": "http://b.invalid/v9/choices",
            "source_method": "GET",
            "kind": "api_option",
        }),
    )
    assert _option_source_request_ids(spec, [spec.steps[-1]], {}) == []


def test_body_signature_matches_independent_of_json_serialization() -> None:
    option_step = FlowStep(
        step_id="step_body",
        method="POST",
        url="http://random.invalid/v9/choices",
        path="/v9/choices",
        body_source=json.dumps({"scope": "expected", "active": True}),
        source_meta={"request_id": "req_body"},
    )
    spec = _spec(
        option_step,
        _write({
            "source_url": "http://random.invalid/v9/choices",
            "source_method": "POST",
            "source_body": {"active": True, "scope": "expected"},
            "kind": "api_option",
        }),
    )
    assert _option_source_request_ids(spec, [spec.steps[-1]], {}) == ["req_body"]


def test_unique_path_candidate_with_conflicting_body_signature_stays_unresolved() -> None:
    option_step = FlowStep(
        step_id="step_body",
        method="POST",
        url="http://random.invalid/v9/choices",
        path="/v9/choices",
        body_source=json.dumps({"scope": "other"}),
        source_meta={"request_id": "req_body"},
    )
    spec = _spec(
        option_step,
        _write({
            "source_url": "http://random.invalid/v9/choices",
            "source_method": "POST",
            "source_body": {"scope": "expected"},
            "kind": "api_option",
        }),
    )
    assert _option_source_request_ids(spec, [spec.steps[-1]], {}) == []


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
        _get("step_b", "http://b.test/api/options", "req_b", page_id="p1", frame_id="f1"),
        _get("step_a", "http://a.test/api/options", "req_a", page_id="p1", frame_id="f1"),
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


def test_public_option_binding_preserves_exact_request_identity() -> None:
    shared_url = "http://random.invalid/v9/choices"
    target = _write({"kind": "unknown"})
    target.params[0].source_kind = "unknown"
    spec = _spec(
        _get("step_first", shared_url, "req_first", trigger_transaction_id="tx_first"),
        _get("step_later", shared_url, "req_later", trigger_transaction_id="tx_later"),
        target,
        facts=[
            RequestFact(
                request_id="req_first",
                method="GET",
                url=shared_url,
                path="/v9/choices",
                response_json={"items": [{"id": 1, "label": "A"}]},
                sequence=1,
                transaction_id="tx_first",
            ),
            RequestFact(
                request_id="req_later",
                method="GET",
                url=shared_url,
                path="/v9/choices",
                response_json={"items": [{"id": 2, "label": "B"}]},
                sequence=2,
                transaction_id="tx_later",
            ),
        ],
    )

    edited = apply_flow_edits(spec, [{
        "op": "bind_option_source",
        "target_step_id": "step_write",
        "target_path": "typeId",
        "source_request_id": "req_first",
        "source_url": shared_url,
        "actor": "user",
    }])

    param = edited.steps[-1].params[0]
    assert (param.source or {}).get("source_request_id") == "req_first"
    assert edited.steps[-1].selects[0].source_request_id == "req_first"


def test_public_option_binding_rejects_request_owned_by_another_control() -> None:
    shared_url = "http://random.invalid/v9/choices"
    target = _write({"kind": "unknown"})
    target.params[0].source_kind = "unknown"
    target.params[0].evidence = [{
        "kind": "page_control",
        "binding_status": "bound",
        "source_request_ids": ["req_owned"],
        "page_id": "page_target",
        "surface": "drawer",
    }]
    spec = _spec(
        _get("step_owned", shared_url, "req_owned", trigger_transaction_id="tx_target"),
        _get("step_other", shared_url, "req_other", trigger_transaction_id="tx_other"),
        target,
        facts=[
            RequestFact(
                request_id="req_owned", method="GET", url=shared_url,
                path="/v9/choices", response_json={"items": []},
                transaction_id="tx_target",
            ),
            RequestFact(
                request_id="req_other", method="GET", url=shared_url,
                path="/v9/choices", response_json={"items": []},
                transaction_id="tx_other",
            ),
        ],
    )

    with pytest.raises(ValueError, match="does not own target field"):
        apply_flow_edits(spec, [{
            "op": "bind_option_source",
            "target_step_id": "step_write",
            "target_path": "typeId",
            "source_request_id": "req_other",
            "source_url": shared_url,
            "actor": "user",
        }])
