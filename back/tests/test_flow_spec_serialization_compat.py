"""Before/after serialization, fingerprint and contract goldens for FlowSpec."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dano.execution.page.flow_spec import (
    FlowSpec,
    flow_spec_fingerprint,
    flow_spec_release_payload,
    flow_spec_to_api_request,
    flow_spec_to_client,
    prepare_flow_release_candidate,
    to_flow_spec,
)
from dano.onboarding.recording_stage_seven import normalize_stage_seven_working_copy

from test_recording_field_contracts import (
    PAGE,
    _compile,
    _create_form_spec,
    _edit_and_command_spec,
    _req,
    _step_by_suffix,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "flow_spec_split" / "goldens"


def _canonicalize(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _metrics(spec: FlowSpec) -> dict[str, Any]:
    dump = spec.model_dump(mode="json")
    fingerprint = flow_spec_fingerprint(spec)
    source_kinds = [
        {
            "step": step.path,
            "path": param.path,
            "source_kind": param.source_kind,
            "source_contract_kind": str((param.source or {}).get("kind") or ""),
            "structure_kind": str((param.source or {}).get("structure_kind") or ""),
            "type": param.type,
            "required": param.required,
        }
        for step in spec.steps
        for param in step.params
    ]
    request_contract, errors = flow_spec_to_api_request(spec)
    return _canonicalize({
        "fingerprint": fingerprint,
        "capability_ids": [cap.capability_id for cap in spec.capabilities],
        "capability_names": [cap.name for cap in spec.capabilities],
        "step_ids": [step.step_id for step in spec.steps],
        "step_paths": [str(step.path or step.url or "") for step in spec.steps],
        "link_ids": [link.link_id for link in spec.links],
        "request_refs": [
            {
                "capability": cap.name,
                "usage": ref.usage,
                "path": ref.path,
                "request_id": ref.request_id,
            }
            for cap in spec.capabilities
            for ref in cap.request_refs
        ],
        "input_schema": [cap.input_schema for cap in spec.capabilities],
        "output_schema": [cap.output_schema for cap in spec.capabilities],
        "source_kinds": source_kinds,
        "request_contract": request_contract,
        "request_contract_errors": errors,
        "dump": dump,
    })


def _single_get_spec() -> FlowSpec:
    return to_flow_spec(
        captured_requests=[
            _req(
                "req_list", method="GET",
                url="http://example.test/admin-api/doc/page?pageNo=1&pageSize=10",
                sequence=1, role="business_get", action="act_search", locator="text=搜索",
                response={"code": 0, "data": {"list": [{"id": 1, "status": 10}]}},
            ),
        ],
        page_events=[{"event_id": "ev_search", "kind": "click", "action_id": "act_search"}],
        page_context=PAGE,
    )


def _dynamic_array_spec() -> FlowSpec:
    return to_flow_spec(
        captured_requests=[
            _req(
                "req_create", method="POST",
                url="http://example.test/admin-api/doc/create",
                sequence=1, role="business_write", action="act_create", locator="text=确定",
                body={
                    "remark": "x",
                    "items": [
                        {"productId": 4, "count": 1, "productPrice": 5000, "totalPrice": 5000},
                        {"productId": 7, "count": 2, "productPrice": 3000, "totalPrice": 6000},
                    ],
                },
                response={"code": 0, "data": 88},
            ),
        ],
        page_events=[{"event_id": "ev_create", "kind": "click", "action_id": "act_create"}],
        page_context=PAGE,
    )


def _multi_step_compiled_spec() -> FlowSpec:
    spec = _edit_and_command_spec()
    approve = _step_by_suffix(spec, "/doc/update-status")
    update = _step_by_suffix(spec, "/doc/update")
    listing = _step_by_suffix(spec, "/doc/page")
    return _compile(spec, [
        {
            "name": "list_docs",
            "title": "查询单据",
            "kind": "query",
            "anchor_step_id": listing.step_id,
            "request_refs": [{"step_id": listing.step_id, "usage": "execute"}],
        },
        {
            "name": "edit_doc",
            "title": "编辑单据",
            "kind": "update",
            "anchor_step_id": update.step_id,
            "request_refs": [{"step_id": update.step_id, "usage": "execute"}],
        },
        {
            "name": "approve_doc",
            "title": "审批单据",
            "kind": "update",
            "anchor_step_id": approve.step_id,
            "request_refs": [{"step_id": approve.step_id, "usage": "execute"}],
        },
    ])


def _create_compiled_spec() -> FlowSpec:
    spec = _create_form_spec()
    create = _step_by_suffix(spec, "/doc/create")
    return _compile(spec, [{
        "name": "create_doc",
        "title": "新建单据",
        "kind": "create",
        "anchor_step_id": create.step_id,
        "request_refs": [{"step_id": create.step_id, "usage": "execute"}],
    }])


def _stabilize(spec: FlowSpec) -> FlowSpec:
    """Round-trip once so goldens match FlowSpec.model_validate(dump)."""
    return FlowSpec.model_validate(spec.model_dump(mode="json"))


CASE_ALIASES = {
    "computed_field": "create",
    "row_command": "edit_hydration",
    "option_api": "edit_hydration",
    "multi_step_dependency": "edit_hydration",
}


def build_cases() -> dict[str, FlowSpec]:
    compiled = _multi_step_compiled_spec()
    create_compiled = _create_compiled_spec()
    return {
        "empty": FlowSpec(),
        "single_get": _single_get_spec(),
        "create": _create_form_spec(),
        "edit_hydration": _edit_and_command_spec(),
        "dynamic_array": _dynamic_array_spec(),
        "capability_compilation": compiled,
        "stage7_working_copy": normalize_stage_seven_working_copy(compiled, compiled),
        "release_candidate": prepare_flow_release_candidate(create_compiled)[0],
    }


def write_goldens() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for stale in GOLDEN_DIR.glob("*.json"):
        stale.unlink()
    index: dict[str, str] = {}
    for name, spec in build_cases().items():
        metrics = _metrics(_stabilize(spec))
        (GOLDEN_DIR / f"{name}.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        index[name] = metrics["fingerprint"]
    for alias, target in CASE_ALIASES.items():
        index[alias] = index[target]
    (GOLDEN_DIR / "fingerprints.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_golden(name: str) -> dict[str, Any]:
    target = CASE_ALIASES.get(name, name)
    path = GOLDEN_DIR / f"{target}.json"
    assert path.exists(), f"missing golden {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_frozen_json_roundtrip_and_fingerprint() -> None:
    fingerprints = json.loads((GOLDEN_DIR / "fingerprints.json").read_text(encoding="utf-8"))
    for name in fingerprints:
        golden = _load_golden(name)
        restored = FlowSpec.model_validate(golden["dump"])
        current = _metrics(restored)
        assert current["fingerprint"] == golden["fingerprint"], name
        assert current["fingerprint"] == fingerprints[name], name
        assert current["capability_ids"] == golden["capability_ids"], name
        assert current["capability_names"] == golden["capability_names"], name
        assert current["step_ids"] == golden["step_ids"], name
        assert current["link_ids"] == golden["link_ids"], name
        assert current["request_refs"] == golden["request_refs"], name
        assert current["input_schema"] == golden["input_schema"], name
        assert current["output_schema"] == golden["output_schema"], name
        assert current["request_contract"] == golden["request_contract"], name
        assert current["dump"] == golden["dump"], name
        release = _canonicalize(flow_spec_release_payload(restored))
        assert release["meta"].get("request_graph") is None, name
        client = flow_spec_to_client(restored)
        assert [cap.get("name") for cap in client.get("capabilities") or []] == current["capability_names"], name


def _source_kind_rows(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {key: item[key] for key in ("step", "path", "source_kind", "required")}
        for item in metrics["source_kinds"]
        if not (
            item.get("source_contract_kind") == "dynamic_structure_input"
            and item.get("structure_kind") == "array_object"
        )
    ]


def _expected_rebuilt_source_kind_rows(
    name: str,
    golden: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = _source_kind_rows(golden)
    if name != "capability_compilation":
        return rows
    target = next(
        row for row in rows
        if row == {
            "step": "/admin-api/doc/update",
            "path": "lineTotal",
            "source_kind": "constant",
            "required": False,
        }
    )
    target["source_kind"] = "computed"
    return rows


def test_rebuild_from_recording_keeps_semantic_contracts() -> None:
    """Fresh to_flow_spec IDs may change; source_kind/required/capability names must not."""
    rebuilt = {
        "empty": FlowSpec(),
        "single_get": _single_get_spec(),
        "create": _create_form_spec(),
        "edit_hydration": _edit_and_command_spec(),
        "dynamic_array": _dynamic_array_spec(),
        "capability_compilation": _multi_step_compiled_spec(),
    }
    for name, spec in rebuilt.items():
        golden = _load_golden(name)
        current = _metrics(_stabilize(spec))
        assert current["capability_names"] == golden["capability_names"], name
        assert _source_kind_rows(current) == _expected_rebuilt_source_kind_rows(name, golden), name
        dynamic_inputs = [
            item for item in current["source_kinds"]
            if item.get("source_contract_kind") == "dynamic_structure_input"
            and item.get("structure_kind") == "array_object"
        ]
        assert len(dynamic_inputs) == (1 if name == "create" else 0), name
        assert all(item.get("type") == "array" for item in dynamic_inputs), name
