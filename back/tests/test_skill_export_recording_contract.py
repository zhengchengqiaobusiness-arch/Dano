"""Recording export must compile a capability contract from this session's FlowSpec."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from dano.execution.page.capability_orchestration import _sync_capability_order
from dano.execution.page.flow_release import prepare_flow_release_candidate
from dano.execution.page.flow_spec import FlowSpec, flow_spec_to_api_request
from dano.execution.page.flow_spec_core.models import (
    CapabilityRequestRef,
    FlowCapability,
    FlowLink,
    FlowStep,
    ParamField,
    RequestFact,
    RequestFacts,
)
from dano.execution.page.flow_spec_core.request_contract import (
    ensure_recorded_body_source,
)
from dano.export.skill_package.renderer import package_slug, render_skill_package
from dano.onboarding.skill_generation.catalog import is_write_capability
from dano.onboarding.skill_generation.export import (
    _current_spec,
    build_export_skill_spec,
    export_recording_skill,
)
from dano.onboarding.skill_generation.models import SkillGenerationRequest
from dano.onboarding.skill_generation.planner import propose_deterministic_plan
from dano.onboarding.skill_generation.validate import validate_skill_plan


def _pi_recording_payload() -> dict:
    return {
        "subsystem": "oa",
        "title": "业务办理",
        "capabilities": [
            {
                "capability_id": "cap_query",
                "name": "query_records",
                "title": "查询记录",
                "kind": "query",
                "step_ids": ["step_query"],
                "request_refs": [{"step_id": "step_query", "usage": "execute"}],
                "input_schema": {
                    "type": "object",
                    "properties": {"keyword": {"type": "string"}},
                },
                "output_schema": {"type": "object"},
            },
            {
                "capability_id": "cap_create",
                "name": "create_record",
                "title": "新增记录",
                "kind": "create",
                "step_ids": ["step_create"],
                "request_refs": [{"step_id": "step_create", "usage": "execute"}],
                "input_schema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
                "output_schema": {"type": "object"},
            },
        ],
        "steps": [
            {
                "step_id": "step_query",
                "name": "查询",
                "method": "GET",
                "path": "/api/records",
                "url": "https://example.test/api/records",
            },
            {
                "step_id": "step_create",
                "name": "新增",
                "method": "POST",
                "path": "/api/records",
                "url": "https://example.test/api/records",
                "params": [
                    {
                        "key": "title",
                        "path": "body.title",
                        "label": "标题",
                        "source_kind": "user_input",
                        "exposed_to_user": True,
                        "default_value": "示例",
                    }
                ],
            },
        ],
    }


def _pi_query_spec() -> FlowSpec:
    payload = _pi_recording_payload()
    payload["capabilities"] = [payload["capabilities"][0]]
    payload["steps"] = [payload["steps"][0]]
    return FlowSpec.model_validate(payload)


def test_empty_nodes_do_not_wipe_declared_execute_refs() -> None:
    cap = FlowCapability.model_construct(
        capability_id="cap_query",
        name="query_records",
        title="查询记录",
        kind="query",
        step_ids=["step_query"],
        request_refs=[CapabilityRequestRef(step_id="step_query", usage="execute")],
        nodes=[],
    )
    spec = FlowSpec.model_construct(
        steps=[FlowStep(step_id="step_query", name="查询", method="GET", path="/api/records")],
        capabilities=[cap],
    )

    _sync_capability_order(spec, cap)

    assert any(ref.usage == "execute" and ref.step_id == "step_query" for ref in cap.request_refs)
    assert cap.step_ids == ["step_query"]
    assert cap.nodes == []


def test_pi_request_fact_ids_do_not_block_export_spec() -> None:
    payload = _pi_recording_payload()
    payload["request_facts"] = {
        "requests": [
            "req_729e8c50f32243359aef73b18df80fcb",
            "req_9ab8ee2ee5e94029819ecffd2bd9f9b7",
        ]
    }
    spec = _current_spec({"flow_spec": payload})
    assert [fact.request_id for fact in spec.request_facts.requests] == [
        "req_729e8c50f32243359aef73b18df80fcb",
        "req_9ab8ee2ee5e94029819ecffd2bd9f9b7",
    ]


def test_loading_recording_result_keeps_request_refs_and_compiles_contract() -> None:
    spec = FlowSpec.model_validate(_pi_recording_payload())

    for cap in spec.capabilities:
        assert any(ref.usage == "execute" and ref.step_id for ref in cap.request_refs)

    release_spec, _candidate = prepare_flow_release_candidate(spec)
    api_request, errors = flow_spec_to_api_request(
        release_spec, _prepared=True, _embed_capability_steps=True,
    )

    assert errors == []
    assert api_request is not None
    capabilities = api_request["capabilities"]
    assert len(capabilities) == 2
    create_steps = []
    for item in capabilities:
        steps = (item.get("execution_contract") or {}).get("steps") or []
        assert steps, item.get("name")
        assert all(step.get("step_id") for step in steps)
        if item.get("capability_id") == "cap_create":
            create_steps = steps
    assert create_steps
    assert any(
        step.get("body_template") == {"title": "{{title}}"}
        for step in create_steps
    )


def test_build_export_skill_spec_does_not_need_a_prior_publish() -> None:
    spec = _pi_query_spec()
    request = SkillGenerationRequest(
        title="业务办理",
        business_description="查询记录。",
        out_dir="out",
    )
    plan = propose_deterministic_plan(
        spec, request, {cap.capability_id for cap in spec.capabilities}, "fp-recording",
    )
    skill = build_export_skill_spec(
        spec, tenant="test", skill_id="oa.action_abcd1234abcd1234abcd1234abcd1234",
        title="业务办理", plan=plan,
    )
    capabilities = (skill.api_request or {}).get("capabilities") or []
    assert capabilities
    assert all((item.get("execution_contract") or {}).get("steps") for item in capabilities)


async def test_recording_without_nodes_exports_a_skill_package(tmp_path: Path) -> None:
    spec = _pi_query_spec()
    action = "action_abcd1234abcd1234abcd1234abcd1234"
    request = SkillGenerationRequest(
        title="业务办理",
        business_description="查询记录。",
        out_dir=str(tmp_path),
    )
    persisted: dict = {}

    async def proposer(current_spec, current_request, verified, source_fingerprint):  # noqa: ANN001
        return propose_deterministic_plan(
            current_spec, current_request, verified, source_fingerprint,
        )

    async def publish(**_kwargs):  # noqa: ANN003
        return {"ok": True, "asset_version": 1, "asset_id": "asset-1", "action": action}

    async def persist(next_body: dict) -> None:
        persisted.update(next_body)

    outcome = await export_recording_skill(
        result_id=UUID("22222222-2222-2222-2222-222222222222"),
        body={
            "flow_spec": spec.model_dump(mode="json"),
            "action": action,
            "subsystem": "oa",
            "title": request.title,
        },
        tenant="test",
        request=request,
        proposer=proposer,
        publish=publish,
        persist=persist,
    )

    assert outcome.status == "exported", outcome.errors
    assert outcome.errors == []
    exported = tmp_path / package_slug(outcome.skill_id)
    assert exported.is_dir()
    contract = json.loads((exported / "references" / "CONTRACT.json").read_text(encoding="utf-8"))
    assert contract.get("capabilities")
    assert persisted.get("skill_export_status") == "exported"


def test_renderer_compiles_missing_execution_contract_from_recording_view(tmp_path: Path) -> None:
    spec = _pi_query_spec()
    request = SkillGenerationRequest(
        title="业务办理",
        business_description="查询记录。",
        out_dir=str(tmp_path),
    )
    plan = propose_deterministic_plan(
        spec, request, {cap.capability_id for cap in spec.capabilities}, "fp-render",
    )
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem

    plan_payload = plan.model_dump(mode="json")
    skill = SkillSpec(
        skill_id="oa.action_abcd1234abcd1234abcd1234abcd1234",
        tenant="test",
        subsystem=Subsystem("oa"),
        action="action_abcd1234abcd1234abcd1234abcd1234",
        title="业务办理",
        risk_level=RiskLevel.L3,
        recording_asset_id=UUID(int=0),
        api_request={
            "capabilities": [
                {
                    "capability_id": "cap_query",
                    "name": "query_records",
                    "title": "查询记录",
                    "kind": "query",
                    "step_ids": ["step_query"],
                    "request_refs": [{"step_id": "step_query", "usage": "execute"}],
                    "input_schema": spec.capabilities[0].input_schema,
                }
            ],
            "steps": [
                {
                    "step_id": "step_query",
                    "method": "GET",
                    "url": "https://example.test/api/records",
                    "path": "/api/records",
                }
            ],
            "_skill_plan": plan_payload,
            "_release_snapshot": {
                "skill_plan": plan_payload,
                "flow_spec": spec.model_dump(mode="json"),
            },
        },
        call_metadata={"skill_plan": plan_payload},
    )

    slug = render_skill_package(skill, str(tmp_path), tenant="test")
    exported = tmp_path / slug
    assert (exported / "references" / "CONTRACT.json").is_file()


def _write_step_without_body(*, extra_params: list[ParamField] | None = None) -> FlowStep:
    params = [
        ParamField(
            key="title",
            path="body.title",
            label="标题",
            source_kind="user_input",
            exposed_to_user=True,
            default_value="示例",
        ),
        *(extra_params or []),
    ]
    return FlowStep(
        step_id="step_create",
        name="新增",
        method="POST",
        path="/api/records",
        url="https://example.test/api/records",
        params=params,
    )


def test_write_step_without_body_source_compiles_from_params() -> None:
    spec = FlowSpec.model_validate({
        "title": "业务办理",
        "capabilities": [
            {
                "capability_id": "cap_create",
                "name": "create_record",
                "title": "新增记录",
                "kind": "create",
                "step_ids": ["step_create"],
                "request_refs": [{"step_id": "step_create", "usage": "execute"}],
            }
        ],
        "steps": [_write_step_without_body().model_dump(mode="json")],
    })

    api_request, errors = flow_spec_to_api_request(spec, _embed_capability_steps=True)

    assert errors == []
    assert api_request is not None
    create = next(
        step
        for item in api_request["capabilities"]
        for step in (item.get("execution_contract") or {}).get("steps") or []
        if step.get("step_id") == "step_create"
    )
    assert create["body_template"] == {"title": "{{title}}"}
    assert api_request.get("sample_inputs", {}).get("title") == "示例"


def test_write_step_reconstructs_nested_body_from_params() -> None:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="step_create",
                name="新增",
                method="POST",
                path="/api/items",
                params=[
                    ParamField(
                        key="name",
                        path="body.items[0].name",
                        source_kind="user_input",
                        exposed_to_user=True,
                        value="行名",
                    ),
                    ParamField(
                        key="qty",
                        path="body.items[0].qty",
                        type="number",
                        source_kind="constant",
                        exposed_to_user=False,
                        default_value=2,
                    ),
                ],
            )
        ]
    )

    prepared = ensure_recorded_body_source(spec.model_copy(deep=True))
    body = json.loads(prepared.steps[0].body_source)
    assert body == {"items": [{"name": "行名", "qty": 2}]}

    api_request, errors = flow_spec_to_api_request(spec)
    assert errors == []
    assert api_request is not None
    assert api_request["body_template"] == {"items": [{"name": "{{name}}", "qty": 2}]}


def test_request_facts_post_data_fills_body_source_without_inventing_keys() -> None:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="step_create",
                name="新增",
                method="POST",
                path="/api/records",
                source_meta={"request_id": "req_1"},
                params=[
                    ParamField(
                        key="title",
                        path="body.title",
                        source_kind="user_input",
                        exposed_to_user=True,
                    )
                ],
            )
        ],
        request_facts=RequestFacts(
            requests=[
                RequestFact(
                    request_id="req_1",
                    method="POST",
                    path="/api/records",
                    post_data={"title": "captured", "extra": 1},
                )
            ]
        ),
    )

    prepared = ensure_recorded_body_source(spec)
    body = json.loads(prepared.steps[0].body_source)
    assert body["title"] == "captured"
    assert body["extra"] == 1


def test_body_reconstruction_does_not_invent_unmentioned_keys() -> None:
    spec = FlowSpec(steps=[_write_step_without_body()])

    prepared = ensure_recorded_body_source(spec)
    assert json.loads(prepared.steps[0].body_source) == {"title": "示例"}


def test_existing_body_source_is_kept() -> None:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="step_create",
                name="新增",
                method="POST",
                path="/api/records",
                body_source='{"title":"已有","other":9}',
                params=[
                    ParamField(
                        key="title",
                        path="body.title",
                        source_kind="user_input",
                        exposed_to_user=True,
                        default_value="示例",
                    )
                ],
            )
        ]
    )

    prepared = ensure_recorded_body_source(spec)
    assert json.loads(prepared.steps[0].body_source) == {"title": "已有", "other": 9}


def test_hidden_query_constant_is_baked_not_a_caller_placeholder() -> None:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="step_lookup",
                name="读取定义",
                method="GET",
                path="/api/definitions/get",
                params=[
                    ParamField(
                        key="key",
                        path="query.key",
                        category="user_param",
                        source_kind="constant",
                        exposed_to_user=False,
                        default_value="order_create",
                    )
                ],
            )
        ]
    )

    api_request, errors = flow_spec_to_api_request(spec)

    assert errors == []
    assert api_request is not None
    assert api_request["query_template"] == {"key": "order_create"}
    assert "key" not in (api_request.get("params") or [])


def test_previous_response_query_is_not_a_caller_placeholder() -> None:
    spec = FlowSpec(
        steps=[
            FlowStep(
                step_id="step_preview",
                method="GET",
                path="/api/preview",
                params=[
                    ParamField(
                        key="definitionId",
                        path="query.definitionId",
                        source_kind="previous_response",
                        exposed_to_user=False,
                    )
                ],
            )
        ]
    )

    api_request, errors = flow_spec_to_api_request(spec)

    assert errors == []
    assert api_request is not None
    assert "definitionId" not in (api_request.get("params") or [])
    assert "{{definitionId}}" not in json.dumps(api_request.get("query_template") or {})


def test_declared_preflight_link_is_compiled_without_verification() -> None:
    spec = FlowSpec(
        capabilities=[
            FlowCapability(
                capability_id="cap_submit",
                name="submit_record",
                title="提交记录",
                kind="command",
                request_refs=[
                    CapabilityRequestRef(step_id="step_def", usage="preflight"),
                    CapabilityRequestRef(step_id="step_preview", usage="preflight"),
                    CapabilityRequestRef(step_id="step_submit", usage="execute"),
                ],
                input_schema={
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            )
        ],
        steps=[
            FlowStep(
                step_id="step_def",
                method="GET",
                path="/api/definitions/get",
                params=[
                    ParamField(
                        key="key",
                        path="query.key",
                        source_kind="constant",
                        exposed_to_user=False,
                        default_value="record_flow",
                    )
                ],
            ),
            FlowStep(
                step_id="step_preview",
                method="GET",
                path="/api/preview",
                params=[
                    ParamField(
                        key="definitionId",
                        path="query.definitionId",
                        source_kind="previous_response",
                        exposed_to_user=False,
                    )
                ],
            ),
            FlowStep(
                step_id="step_submit",
                method="POST",
                path="/api/records/submit",
                params=[
                    ParamField(
                        key="title",
                        path="body.title",
                        source_kind="user_input",
                        exposed_to_user=True,
                    )
                ],
            ),
        ],
        links=[
            FlowLink(
                source_step_id="step_def",
                target_step_id="step_preview",
                source_path="data.id",
                target_path="query.definitionId",
            )
        ],
    )

    api_request, errors = flow_spec_to_api_request(spec, _embed_capability_steps=True)

    assert errors == []
    execution = api_request["capabilities"][0]["execution_contract"]
    lookup = next(step for step in execution["steps"] if step["step_id"] == "step_def")
    preview = next(step for step in execution["steps"] if step["step_id"] == "step_preview")
    assert lookup["query_template"] == {"key": "record_flow"}
    assert "key" not in (lookup.get("params") or [])
    assert "{{definitionId}}" not in json.dumps(preview.get("query_template") or {})
    assert execution["links"]
    assert execution["links"][0]["source_path"] == "data.id"
    assert execution["links"][0]["target_path"] == "query.definitionId"


def test_command_kind_follows_execute_http_method() -> None:
    write = FlowCapability(
        capability_id="cap_cmd",
        name="handle_record",
        title="办理记录",
        kind="command",
        request_refs=[CapabilityRequestRef(step_id="step_w", usage="execute")],
    )
    lookup = FlowCapability(
        capability_id="cap_lookup",
        name="lookup_record",
        title="核对记录",
        kind="command",
        request_refs=[CapabilityRequestRef(step_id="step_r", usage="execute")],
    )
    posted_query = FlowCapability(
        capability_id="cap_search",
        name="search_records",
        title="搜索",
        kind="query",
        request_refs=[CapabilityRequestRef(step_id="step_q", usage="execute")],
    )
    spec = FlowSpec(
        capabilities=[write, lookup, posted_query],
        steps=[
            FlowStep(step_id="step_w", method="POST", path="/api/write"),
            FlowStep(step_id="step_r", method="GET", path="/api/read"),
            FlowStep(step_id="step_q", method="POST", path="/api/search"),
        ],
    )

    assert is_write_capability(write, spec) is True
    assert is_write_capability(lookup, spec) is False
    assert is_write_capability(posted_query, spec) is False
    assert is_write_capability(write) is False


def test_command_kind_write_route_requires_confirmation(tmp_path: Path) -> None:
    payload = _pi_recording_payload()
    payload["capabilities"][1]["kind"] = "command"
    payload["capabilities"][1]["name"] = "handle_record"
    payload["capabilities"][1]["title"] = "办理记录"
    spec = FlowSpec.model_validate(payload)
    request = SkillGenerationRequest(
        title="业务办理",
        business_description="查询记录，然后办理记录。",
        out_dir=str(tmp_path),
    )
    verified = {cap.capability_id for cap in spec.capabilities}
    plan = propose_deterministic_plan(spec, request, verified, "fp-command-write")
    checked = validate_skill_plan(
        plan,
        spec,
        verified_capability_ids=verified,
        expected_fingerprint="fp-command-write",
    )
    assert checked.ok, checked.errors

    write_routes = [
        route for route in plan.routes
        if "cap_create" in route.capability_sequence
    ]
    assert write_routes
    for route in write_routes:
        assert route.requires_confirmation is True
        write_steps = [step for step in route.steps if step.capability_id == "cap_create"]
        assert write_steps
        assert all(step.confirm_before_execute for step in write_steps)

    skill = build_export_skill_spec(
        spec,
        tenant="test",
        skill_id="oa.action_abcd1234abcd1234abcd1234abcd1234",
        title="业务办理",
        plan=plan,
    )
    slug = render_skill_package(skill, str(tmp_path), tenant="test")
    contract = json.loads((tmp_path / slug / "references" / "CONTRACT.json").read_text(encoding="utf-8"))
    write_caps = [
        item for item in contract.get("capabilities") or []
        if item.get("name") in {"handle_record", "create_record"} or item.get("is_write")
    ]
    assert write_caps
    assert all(item.get("requires_confirmation") and item.get("is_write") for item in write_caps)
    for route in contract.get("routes") or []:
        sequence = list(route.get("operation_sequence") or [])
        if not any(name in sequence for name in ("handle_record", "办理记录", "create_record")):
            continue
        assert route.get("requires_confirmation") is True
        for step in route.get("steps") or []:
            if step.get("operation") in {"handle_record", "办理记录", "create_record"}:
                assert step.get("confirm_before_execute") is True
