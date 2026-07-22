"""Recording core uses Pi submissions and deterministic gates only."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest
import dano.agent_tools.tools as agent_tools_module

from dano.agent_tools.tools import (
    ToolError,
    get_recording_state,
    get_validation_report,
    request_review,
    submit_recording_plan,
    submit_recording_repair,
    submit_recording_review,
)
from dano.shared.enums import AssetType
from dano.execution.page import flow_spec as flow_module
from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowStep,
    ParamField,
    RequestFact,
    RequestUsage,
    ensure_flow_version,
    flow_spec_fingerprint,
    prepare_flow_release_candidate,
)
from dano.onboarding.page_onboard import run_request_onboarding


def _call_nodes(step_ids: list[str]) -> list[dict]:
    return [
        {"id": f"call_{index}", "type": "call", "step_id": step_id}
        for index, step_id in enumerate(step_ids)
    ]


def _spec() -> FlowSpec:
    spec = FlowSpec(
        flow_id="recording-test",
        title="提交申请",
        steps=[FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            body_source='{"title":"demo"}',
            params=[ParamField(
                path="title",
                key="title",
                label="标题",
                value="demo",
                category="user_param",
                source_kind="user_input",
                exposed_to_user=True,
            )],
        )],
    )
    spec = ensure_flow_version(spec, "recorded", reason="test")
    fingerprint = flow_spec_fingerprint(spec)
    spec.meta = {
        **(spec.meta or {}),
        "release_candidate": {
            "protocol": "dano.recording_release.v1",
            "release_id": f"test-{fingerprint}",
            "flow_fingerprint": fingerprint,
        },
    }
    return spec


def test_flow_fingerprint_is_stable_after_frozen_snapshot_revalidation() -> None:
    spec = _spec()
    ref = flow_module.CapabilityRequestRef(
        request_id="request-submit",
        step_id="submit",
        method="POST",
        path="/api/submit",
    )
    # Old clients can round-trip this field as extra data. We keep it in
    # metadata so frozen/reloaded snapshots stay structurally consistent.
    ref.__pydantic_extra__ = {"pinned": True}
    spec.capabilities = [flow_module.FlowCapability(
        capability_id="submit-capability",
        name="submit",
        title="提交申请",
        request_refs=[ref],
        nodes=_call_nodes(["submit"]),
    )]

    frozen = FlowSpec.model_validate(spec.model_dump(mode="json", exclude_none=True))

    assert frozen.capabilities[0].request_refs[0].model_dump().get("pinned") is True
    assert flow_spec_fingerprint(spec) == flow_spec_fingerprint(frozen)


def test_manual_edit_then_release_reviews_the_exact_persisted_snapshot() -> None:
    spec = _spec()
    spec.capabilities = [flow_module.FlowCapability(
        capability_id="submit-capability",
        name="submit_request",
        title="提交申请",
        kind="submit",
        nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
        confirmed=True,
    )]
    edited = flow_module.apply_flow_edits(spec, [{
        "op": "update",
        "step_id": "submit",
        "param_path": "title",
        "field": "key",
        "value": "申请标题",
    }])

    frozen, release = prepare_flow_release_candidate(edited)
    persisted = frozen.model_dump(mode="json", exclude_none=True)
    reconstructed = FlowSpec.model_validate(persisted)

    assert frozen.steps[0].params[0].key == "申请标题"
    assert flow_spec_fingerprint(frozen) == release["flow_fingerprint"]
    assert flow_spec_fingerprint(reconstructed) == release["flow_fingerprint"]


def test_release_fingerprint_treats_missing_and_explicit_none_evidence_as_equal() -> None:
    with_none = _spec()
    with_none.request_facts.requests = [RequestFact(
        request_id="request-submit",
        request_index=1,
        method="POST",
        path="/api/submit",
    )]
    with_none.request_facts.analysis = {
        "request-submit": flow_module.RequestAnalysis.model_validate({
            "request_id": "request-submit",
            "role": "business_write",
            "post_data": None,
            "response_json": None,
            "response_status": None,
        }),
    }
    without_none = with_none.model_copy(deep=True)
    without_none.request_facts.analysis = {
        "request-submit": flow_module.RequestAnalysis(
            request_id="request-submit",
            role="business_write",
        ),
    }

    assert flow_spec_fingerprint(with_none) == flow_spec_fingerprint(without_none)
    prepared, release = prepare_flow_release_candidate(with_none)
    reconstructed = FlowSpec.model_validate(
        flow_module.flow_spec_release_payload(prepared)
    )
    assert flow_spec_fingerprint(reconstructed) == release["flow_fingerprint"]


class _Session:
    def __init__(self, recording_id: str = "rec-1") -> None:
        self.recording_id = recording_id
        self.spec = _spec()
        self.last_review = {}
        self.last_submission_kind = ""
        self.analysis_image_count = 0
        self.received_submission = None

    def bind_flow_spec(self, spec):
        self.spec = spec.model_copy(deep=True)
        self.last_review = {}
        self.last_submission_kind = ""

    def current_flow_spec(self):
        return self.spec.model_copy(deep=True)

    async def get_recording_state(self):
        return flow_module.recording_agent_state(self.spec)

    async def get_validation_report(self):
        return flow_module.recording_agent_validation(self.spec)

    async def apply_submission(self, submission, *, mode, base_flow_version):
        current = int((self.spec.meta or {}).get("current_version") or 0)
        if base_flow_version != current:
            raise RuntimeError(f"录制版本冲突: base={base_flow_version}, current={current}")
        self.received_submission = submission
        self.spec = await flow_module.apply_recording_agent_submission(
            self.spec, submission=submission, mode=mode,
        )
        self.last_submission_kind = mode
        self.last_review = {}
        return flow_module.recording_agent_validation(self.spec)

    async def accept_unchanged_plan(self, *, base_flow_version, warning):
        current = int((self.spec.meta or {}).get("current_version") or 0)
        if base_flow_version != current:
            raise RuntimeError("录制版本冲突")
        self.last_submission_kind = "plan"
        self.last_submission_warning = warning
        return {
            **flow_module.recording_agent_validation(self.spec),
            "accepted": True,
            "unchanged": True,
            "warning": warning,
        }

    async def submit_review(self, review, *, base_flow_version):
        current = int((self.spec.meta or {}).get("current_version") or 0)
        if base_flow_version != current:
            raise RuntimeError("录制版本冲突")
        self.last_review = dict(review)
        self.last_submission_kind = "review"
        return {"accepted": True, "flow_version": current}


def _bind(monkeypatch, *, recording_id: str = "rec-1") -> _Session:
    session = _Session(recording_id)
    monkeypatch.setattr(
        "dano.onboarding.recording_pi.active_recording_session",
        lambda _run_id: session,
    )
    return session


def test_recording_core_has_no_direct_llm_conversation_or_cache_path():
    source = inspect.getsource(flow_module)
    for forbidden in (
        "class _SemanticConversation",
        "complete_json_messages(",
        "llm_client.complete_json(",
        '"recording_pi_loop"',
        '"application_cache_hit"',
        '"model_cached_tokens"',
    ):
        assert forbidden not in source
    signature = inspect.signature(flow_module.apply_recording_agent_submission)
    assert "llm_client" not in signature.parameters
    assert "model" not in signature.parameters
    assert "submission" in signature.parameters


def test_recording_agent_state_omits_raw_dom_mutation_noise() -> None:
    spec = _spec()
    spec.request_facts.page_events = [
        {
            "event_id": "action-1",
            "kind": "action",
            "op": "fill",
            "field": "申请标题",
            "required": True,
        },
        {
            "event_id": "dom-1",
            "kind": "dom_effect",
            "changes": [
                {"sequence": index, "type": "childList", "added": 1, "removed": 1}
                for index in range(100)
            ],
        },
    ]

    state = flow_module.recording_agent_state(spec)

    assert [item["kind"] for item in state["facts"]["page_events"]] == ["action"]


def test_pi_tools_read_and_apply_plan_without_changing_request_facts(monkeypatch):
    session = _bind(monkeypatch)
    session.analysis_image_count = 2
    state = asyncio.run(get_recording_state("run-recording", {"recording_id": "rec-1"}))
    assert state["flow_version"] == 1
    before_facts = session.spec.request_facts.model_dump(mode="json")

    result = asyncio.run(submit_recording_plan("run-recording", {
        "recording_id": "rec-1",
        "base_flow_version": 1,
        "plan": {
            "submission_id": "plan-1",
            "semantic_plan": {
                "business_understanding": {"intent": "提交申请"},
                "request_roles": [{
                    "step_id": "submit", "role": "submit_anchor", "name": "提交申请",
                    "title": "提交申请", "reason": "真实 POST 请求",
                }],
                "field_semantics": [{
                    "step_id": "submit", "wire_path": "title", "public_name": "申请标题",
                    "business_type": "string", "category": "user_param",
                    "source_kind": "user_input", "confidence": 0.95,
                    "evidence": [{
                        "source": "screenshot", "screenshot_name": "form.png",
                        "visible_label": "申请标题", "control_kind": "text",
                        "editable": True,
                    }],
                }],
                "capabilities": [{
                    "name": "submit_application", "title": "提交申请",
                    "intent": "提交申请", "kind": "submit", "step_ids": ["submit"],
                }],
                "capability_relations": [],
                "unresolved_items": [],
            },
            "ops": [{"op": "rename_step", "step_id": "submit", "name": "提交申请"}],
        },
    }))
    assert result["flow_version"] > 1
    assert session.received_submission["_analysis_screenshot_count"] == 2
    assert session.spec.request_facts.model_dump(mode="json") == before_facts

    validation = asyncio.run(get_validation_report("run-recording", {"recording_id": "rec-1"}))
    assert validation["flow_version"] == result["flow_version"]
    assert "report" in validation and "repair_context" in validation



def test_pi_plan_rejects_legacy_flow_spec_wrapper(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-legacy-plan")

    with pytest.raises(ToolError, match="禁止提交 flow_spec"):
        asyncio.run(submit_recording_plan("run-legacy-plan", {
            "recording_id": "rec-legacy-plan",
            "base_flow_version": 1,
            "plan": {"flow_spec": {
                "title": "截图识别的申请流程",
                "capabilities": [{"title": "截图能力"}],
            }},
        }))

    assert int((session.spec.meta or {}).get("current_version") or 0) == 1


def test_pi_plan_normalizes_observed_model_variant_on_first_submission(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-model-variant")

    result = asyncio.run(submit_recording_plan("run-model-variant", {
        "recording_id": "rec-model-variant",
        "base_flow_version": 1,
        "plan": {
            "semantic_plan": {
                "business_understanding": "提交申请并返回处理结果",
                "request_roles": ["submit_anchor"],
                "field_semantics": [{
                    "step_id": "submit",
                    "wire_path": "title",
                    "public_name": "截图中的申请标题",
                    "business_type": "string",
                    "category": "runtime_var",
                    "source_kind": "previous_response",
                    "confidence": 0.95,
                    "evidence": "截图标签与请求字段同名",
                }],
                "capabilities": [{
                    "capability_id": "cap_submit_application",
                    "title": "发起申请",
                    "description": "提交页面中的申请表单",
                    "primary_step_id": "submit",
                    "entry_step_id": "submit",
                    "depends_on_step_ids": [],
                }],
                "capability_relations": [{
                    "source_capability_id": "cap_submit_application",
                    "target_capability_id": "cap_submit_application",
                    "relation_type": "depends_on",
                }],
                "unresolved_items": [],
            },
            "ops": "",
        },
    }))

    assert result["flow_version"] > 1
    assert session.spec.capabilities
    assert session.spec.steps[0].params[0].category == "user_param"
    assert session.spec.steps[0].params[0].source_kind == "user_input"



def test_pi_plan_allows_backend_to_refresh_derived_request_usage(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-derived-usage")
    session.spec.steps[0].source_meta = {"request_id": "request-1"}
    session.spec.request_facts.requests = [RequestFact(
        request_id="request-1",
        request_index=0,
        method="POST",
        url="https://example.invalid/api/submit",
        path="/api/submit",
        post_data={"title": "demo"},
    )]
    session.spec.request_facts.usage = {
        "request-1": RequestUsage(request_id="request-1", state="captured"),
    }
    immutable_request_before = session.spec.request_facts.requests[0].model_dump(
        mode="json",
        include=set(RequestFact.model_fields),
    )

    result = asyncio.run(submit_recording_plan("run-derived-usage", {
        "recording_id": "rec-derived-usage",
        "base_flow_version": 1,
        "plan": {
            "semantic_plan": {
                "business_understanding": {},
                "request_roles": [],
                "field_semantics": [],
                "capabilities": [],
                "capability_relations": [],
                "unresolved_items": [],
            },
            "ops": [],
        },
    }))

    assert result["flow_version"] > 1
    assert session.spec.request_facts.requests[0].model_dump(
        mode="json",
        include=set(RequestFact.model_fields),
    ) == immutable_request_before
    usage = session.spec.request_facts.usage["request-1"]
    assert usage.state == "materialized"
    assert usage.materialized_step_id == "submit"


def test_pi_repair_rejects_stale_version_and_non_whitelisted_operation(monkeypatch):
    _bind(monkeypatch, recording_id="rec-repair")
    with pytest.raises(ToolError, match="版本冲突"):
        asyncio.run(submit_recording_repair("run-repair", {
            "recording_id": "rec-repair",
            "base_flow_version": 0,
            "operations": [],
        }))
    with pytest.raises(ToolError, match="不允许|not allowed|确定性准入"):
        asyncio.run(submit_recording_repair("run-repair", {
            "recording_id": "rec-repair",
            "base_flow_version": 1,
            "operations": [{"op": "replace_request_facts", "requests": []}],
        }))


def test_pi_review_is_strict_and_persisted_in_recording_state(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-review")
    review = asyncio.run(submit_recording_review("run-review", {
        "recording_id": "rec-review",
        "base_flow_version": 1,
        "review": {
            role: {"passed": True, "reasons": []}
            for role in ("acceptance", "security", "compliance")
        },
    }))
    assert review["accepted"] is True
    assert session.last_review["all_passed"] is True
    with pytest.raises(ToolError, match="review.security"):
        asyncio.run(submit_recording_review("run-review", {
            "recording_id": "rec-review",
            "base_flow_version": 1,
            "review": {"acceptance": {"passed": True, "reasons": []}},
        }))


def test_pi_tools_reject_unknown_params_bool_version_and_malformed_review(monkeypatch):
    _bind(monkeypatch, recording_id="rec-strict")
    with pytest.raises(ToolError, match="未知参数"):
        asyncio.run(get_recording_state("run-strict", {
            "recording_id": "rec-strict", "messages": [],
        }))
    with pytest.raises(ToolError, match="base_flow_version 必须是整数"):
        asyncio.run(submit_recording_repair("run-strict", {
            "recording_id": "rec-strict",
            "base_flow_version": True,
            "operations": [],
        }))
    with pytest.raises(ToolError, match="blocking_reasons"):
        asyncio.run(submit_recording_review("run-strict", {
            "recording_id": "rec-strict",
            "base_flow_version": 1,
            "review": {
                **{
                    role: {"passed": True, "reasons": []}
                    for role in ("acceptance", "security", "compliance")
                },
                "blocking_reasons": "not-a-list",
            },
        }))


class _ReviewStore:
    def __init__(self, spec: FlowSpec | None = None) -> None:
        self.draft_id = uuid4()
        self.content_hash = f"sha256:{uuid4().hex}"
        self.recorded: list[dict] = []
        self.spec = (spec or _spec()).model_copy(deep=True)

    async def get_draft(self, draft_id):
        if draft_id != self.draft_id:
            return None
        return SimpleNamespace(
            asset_draft_id=draft_id,
            asset_type=AssetType.PAGE_SCRIPT,
            asset_key="recorded-submit",
            content_hash=self.content_hash,
            body={"api_request": {
                "method": "POST",
                "_release_snapshot": {
                    "flow_fingerprint": flow_spec_fingerprint(self.spec),
                    "flow_spec": self.spec.model_dump(exclude_none=True),
                },
            }},
        )

    async def list_validations(self, _draft_id):
        return []

    async def record_review(self, **kwargs):
        self.recorded.append(dict(kwargs))
        return SimpleNamespace(review_run_id=uuid4())


def test_active_recording_review_uses_only_pi_three_roles_and_never_board(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-review-only")
    session.last_review = {
        "base_flow_version": 1,
        "flow_fingerprint": flow_spec_fingerprint(session.spec),
        "blocking_reasons": [],
        "verdicts": [
            {"role": role, "passed": True, "reasons": [], "model_id": "pi-session"}
            for role in ("acceptance", "security", "compliance")
        ],
    }
    store = _ReviewStore(session.spec)
    monkeypatch.setattr("dano.agent_tools.tools._ds", store)

    class _ForbiddenBoard:
        async def review(self, **_kwargs):
            raise AssertionError("active recording review must not call ReviewBoard")

    monkeypatch.setattr("dano.agent_tools.tools._review_board", _ForbiddenBoard())
    result = asyncio.run(request_review("run-review-only", {
        "asset_draft_id": str(store.draft_id),
    }))
    assert result["source"] == "pi_agent_session"
    assert result["all_passed"] is True
    assert {item["role"] for item in store.recorded} == {
        "acceptance", "security", "compliance",
    }
    assert session.last_review["draft_id"] == str(store.draft_id)
    assert session.last_review["draft_content_hash"] == store.content_hash


def test_pi_review_cannot_be_reused_for_another_draft(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-review-bound")
    session.last_review = {
        "base_flow_version": 1,
        "flow_fingerprint": flow_spec_fingerprint(session.spec),
        "blocking_reasons": [],
        "verdicts": [
            {"role": role, "passed": True, "reasons": [], "model_id": "pi-session"}
            for role in ("acceptance", "security", "compliance")
        ],
    }
    store = _ReviewStore(session.spec)
    monkeypatch.setattr("dano.agent_tools.tools._ds", store)
    asyncio.run(request_review("run-review-bound", {
        "asset_draft_id": str(store.draft_id),
    }))
    first_draft = store.draft_id
    store.draft_id = uuid4()
    store.content_hash = f"sha256:{uuid4().hex}"
    with pytest.raises(ToolError, match="禁止跨草案复用"):
        asyncio.run(request_review("run-review-bound", {
            "asset_draft_id": str(store.draft_id),
        }))
    assert session.last_review["draft_id"] == str(first_draft)
    assert len(store.recorded) == 3


@pytest.mark.parametrize("review, error", [
    ({}, "缺少 Pi AgentSession"),
    ({
        "base_flow_version": 0,
        "verdicts": [
            {"role": role, "passed": True, "reasons": []}
            for role in ("acceptance", "security", "compliance")
        ],
    }, "已过期"),
    ({
        "base_flow_version": 1,
        "verdicts": [
            {"role": "acceptance", "passed": True, "reasons": []},
            {"role": "acceptance", "passed": True, "reasons": []},
            {"role": "security", "passed": True, "reasons": []},
        ],
    }, "未完整覆盖"),
])
def test_active_recording_review_missing_stale_or_duplicate_hard_fails(monkeypatch, review, error):
    session = _bind(monkeypatch, recording_id="rec-review-bad")
    session.last_review = review
    store = _ReviewStore(session.spec)
    monkeypatch.setattr("dano.agent_tools.tools._ds", store)
    with pytest.raises(ToolError, match=error):
        asyncio.run(request_review("run-review-bad", {
            "asset_draft_id": str(store.draft_id),
        }))
    assert store.recorded == []


def test_pi_review_with_blocking_reasons_hard_fails_before_session_write(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-review-blocked")
    with pytest.raises(ToolError, match="blocking_reasons 非空"):
        asyncio.run(submit_recording_review("run-review-blocked", {
            "recording_id": "rec-review-blocked",
            "base_flow_version": 1,
            "review": {
                **{
                    role: {"passed": True, "reasons": []}
                    for role in ("acceptance", "security", "compliance")
                },
                "blocking_reasons": ["仍有越权风险"],
            },
        }))
    assert session.last_review == {}
    assert session.last_submission_kind == ""


@pytest.mark.parametrize("mode", ["plan", "repair"])
def test_fact_violation_rolls_back_entire_recording_session(monkeypatch, mode):
    session = _bind(monkeypatch, recording_id=f"rec-atomic-{mode}")
    before_spec = session.spec.model_dump(mode="json")
    session.last_submission_kind = "review"
    session.last_review = {"sentinel": "preserve"}

    async def _corrupt(_submission, *, mode, base_flow_version):
        assert base_flow_version == 1
        session.spec.request_facts.option_sources.append({"tampered": mode})
        session.spec.title = "polluted"
        session.last_submission_kind = mode
        session.last_review = {}
        return {"flow_version": 999}

    session.apply_submission = _corrupt
    params = {
        "recording_id": f"rec-atomic-{mode}",
        "base_flow_version": 1,
    }
    if mode == "plan":
        params["plan"] = {
            "semantic_plan": {
                "business_understanding": {},
                "request_roles": [],
                "field_semantics": [],
                "capabilities": [],
                "capability_relations": [],
                "unresolved_items": [],
            },
            "ops": [],
        }
        call = submit_recording_plan
    else:
        params["operations"] = []
        call = submit_recording_repair
    with pytest.raises(ToolError, match="不得修改原始 request facts"):
        asyncio.run(call(f"run-atomic-{mode}", params))
    assert session.spec.model_dump(mode="json") == before_spec
    assert session.last_submission_kind == "review"
    assert session.last_review == {"sentinel": "preserve"}


@pytest.mark.parametrize(
    ("method", "path", "param_name"),
    [
        ("POST", "/api/submit", "reason"),
        ("DELETE", "/admin-api/bpm/process-instance/cancel-by-start-user", "reason"),
        ("DELETE", "/admin-api/bpm/process-instance/cancel-by-start-user", "请输入撤回原因"),
    ],
    ids=["ordinary-submit", "recorded-withdraw", "recorded-advisory-placeholder"],
)
def test_page_onboard_active_recording_bypasses_board_precheck_and_model_helpers(
    monkeypatch, method, path, param_name,
):
    from dano.agent_tools import tools as tool_module

    calls: list[str] = []

    async def _save(_run_id, _params):
        calls.append("save")
        return {"asset_draft_id": str(uuid4())}

    async def _self_check(_run_id, _params):
        calls.append("self_check")
        return {
            "passed": True,
            "mode": "self_check",
            "structured_output": {},
            "validation_run_ids": [str(uuid4())],
        }

    async def _review(_run_id, _params):
        calls.append("pi_review")
        return {
            "all_passed": True,
            "verdicts": [],
            "review_run_ids": [str(uuid4()), str(uuid4()), str(uuid4())],
            "source": "pi_agent_session",
        }

    async def _publish(_run_id, _params):
        calls.append("publish")
        return {"published": True, "asset_id": str(uuid4()), "version": 1}

    async def _forbidden_auto_goal(*_args, **_kwargs):
        raise AssertionError("active recording must not call ReviewBoard goal helper")

    recording_session = object()
    monkeypatch.setattr(
        "dano.onboarding.recording_pi.active_recording_session",
        lambda run_id: recording_session if run_id == "run-pi-publish" else None,
    )
    monkeypatch.setattr("dano.onboarding.page_onboard._auto_goal", _forbidden_auto_goal)
    monkeypatch.setattr(tool_module, "_review_board", None)
    monkeypatch.setattr(tool_module, "_fix_proposer", None)
    monkeypatch.setattr(tool_module, "save_draft", _save)
    monkeypatch.setattr(tool_module, "self_check_recording", _self_check)
    monkeypatch.setattr(tool_module, "request_review", _review)
    monkeypatch.setattr(tool_module, "publish_asset", _publish)

    result = asyncio.run(run_request_onboarding(
        tenant="tenant-pi",
        subsystem="reimburse",
        action="recorded_submit",
        title="提交申请",
        api_request={
            "method": method,
            "url": f"https://example.invalid{path}",
            "path": path,
            "body_template": {param_name: "{{" + param_name + "}}"},
            "params": [param_name],
            "field_types": {param_name: "string"},
            "success_rule": {"field": "code", "ok_values": [0]},
        },
        sample_inputs={param_name: "demo"},
        required=[param_name],
        run_id="run-pi-publish",
        allow_repair=True,
        recording_pi_required=True,
    ))
    assert result["ok"] is True
    assert calls == ["save", "self_check", "pi_review", "publish"]
    if method == "DELETE":
        assert result["stage"] == "publish"
        assert result["status"] != "rejected"
        assert result["request_role"]["semanticRole"] == "destructive"
    if param_name.startswith("请输入"):
        assert any("占位" in warning for warning in result.get("warnings") or [])


def _recording_api_request() -> dict:
    return {
        "method": "POST",
        "url": "https://example.invalid/api/submit",
        "path": "/api/submit",
        "body_template": {"reason": "{{reason}}"},
        "params": ["reason"],
        "field_types": {"reason": "string"},
        "success_rule": {"field": "code", "ok_values": [0]},
    }


def test_page_onboard_required_recording_session_missing_fails_before_any_model(monkeypatch):
    from dano.agent_tools import tools as tool_module

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("required recording path must fail before model/tool work")

    monkeypatch.setattr("dano.onboarding.recording_pi.active_recording_session", lambda _run_id: None)
    monkeypatch.setattr("dano.onboarding.page_onboard._auto_goal", forbidden)
    monkeypatch.setattr(tool_module, "save_draft", forbidden)
    monkeypatch.setattr(tool_module, "request_review", forbidden)

    with pytest.raises(RuntimeError, match="要求 Pi AgentSession"):
        asyncio.run(run_request_onboarding(
            tenant="tenant-pi",
            subsystem="reimburse",
            action="recorded_submit",
            api_request=_recording_api_request(),
            run_id="run-missing-pi",
            recording_pi_required=True,
        ))


def test_page_onboard_required_recording_session_loss_never_falls_back(monkeypatch):
    from dano.agent_tools import tools as tool_module

    session = object()
    state = {"session": session}
    calls: list[str] = []

    async def save_then_drop(_run_id, _params):
        calls.append("save")
        state["session"] = None
        return {"asset_draft_id": str(uuid4())}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("lost recording session must not call model/review/repair")

    class ForbiddenBoard:
        async def review(self, **_kwargs):
            raise AssertionError("lost recording session must not call ReviewBoard")

    monkeypatch.setattr(
        "dano.onboarding.recording_pi.active_recording_session",
        lambda _run_id: state["session"],
    )
    monkeypatch.setattr("dano.onboarding.page_onboard._auto_goal", forbidden)
    monkeypatch.setattr(tool_module, "_review_board", ForbiddenBoard())
    monkeypatch.setattr(tool_module, "_fix_proposer", forbidden)
    monkeypatch.setattr(tool_module, "save_draft", save_then_drop)
    monkeypatch.setattr(tool_module, "self_check_recording", forbidden)
    monkeypatch.setattr(tool_module, "request_review", forbidden)

    with pytest.raises(RuntimeError, match="已丢失或被替换"):
        asyncio.run(run_request_onboarding(
            tenant="tenant-pi",
            subsystem="reimburse",
            action="recorded_submit",
            api_request=_recording_api_request(),
            run_id="run-lost-pi",
            allow_repair=True,
            recording_pi_required=True,
        ))
    assert calls == ["save"]


def test_observed_five_interface_plan_keeps_only_business_anchors():
    page = FlowStep(
        step_id="page",
        method="GET",
        path="/admin-api/oa/seal-apply/page?pageNo=1&pageSize=10",
        source_meta={"role": "business_get"},
        params=[ParamField(
            path="query.pageNo", key="pageNo", value="1",
            category="user_param", source_kind="user_input", exposed_to_user=True,
        )],
        response_json={"data": {"list": [{"sealId": "seal-1"}], "total": 1}},
    )
    definition = FlowStep(
        step_id="definition",
        method="GET",
        path="/admin-api/bpm/process-definition/get?key=oa_seal_apply",
        source_meta={"role": "business_get", "control_preflight_for_write": True},
        response_json={"data": {"id": "process-1"}},
    )
    approval = FlowStep(
        step_id="approval",
        method="GET",
        path="/admin-api/bpm/process-instance/get-approval-detail",
        source_meta={"role": "business_get", "control_preflight_for_write": True},
        params=[ParamField(
            path="query.processDefinitionId", key="processDefinitionId",
            value="process-1", category="runtime_var",
            source_kind="previous_response", exposed_to_user=False,
        )],
        response_json={"data": {"nodes": []}},
    )
    seal = ParamField(
        path="sealId",
        key="sealId",
        value="seal-1",
        category="runtime_var",
        source_kind="previous_response",
        source={
            "step_id": "page",
            "response_path": "data.list[0].sealId",
            "link_id": "bad-seal-link",
        },
        exposed_to_user=False,
    )
    submit = FlowStep(
        step_id="submit",
        method="POST",
        path="/admin-api/oa/seal-apply/submit-process",
        source_meta={"role": "submit_anchor"},
        params=[seal],
        response_json={"code": 0},
    )
    options = FlowStep(
        step_id="options",
        method="GET",
        path="/admin-api/bd/seal/simple-list?status=0",
        source_meta={"role": "business_get"},
        response_json={
            "data": [
                {"id": "seal-1", "name": "Company Seal"},
                {"id": "seal-2", "name": "Finance Seal"},
            ],
        },
    )
    spec = FlowSpec(
        flow_id="observed-five-interface",
        steps=[page, definition, approval, submit, options],
        links=[
            flow_module.FlowLink(
                source_step_id="definition",
                source_path="data.id",
                target_step_id="approval",
                target_path="query.processDefinitionId",
                confirmed=True,
                confidence=0.97,
            ),
            flow_module.FlowLink(
                link_id="bad-seal-link",
                source_step_id="page",
                source_path="data.list[0].sealId",
                target_step_id="submit",
                target_path="sealId",
                reason="值匹配自动关联",
                evidence={"kind": "value_match"},
                confidence=0.85,
            ),
        ],
    )
    raw_plan = {
        "semantic_plan": {
            "business_understanding": "Submit a seal application",
            "request_roles": [{"role_id": "submit_anchor"}],
            "field_semantics": [{
                "step_id": "submit",
                "wire_path": "sealId",
                "public_name": "Seal",
                "business_type": "string",
                "category": "runtime_var",
                "source_kind": "previous_response",
                "confidence": "high",
                "evidence": "The model guessed data.list[0].sealId",
            }],
            "capabilities": [
                {"capability_id": "cap_submit", "title": "Submit Application", "anchor_step_id": "submit"},
                {"capability_id": "cap_page", "title": "Query Applications", "anchor_step_id": "page"},
                {"capability_id": "cap_definition", "title": "Get Process Definition", "anchor_step_id": "definition"},
                {"capability_id": "cap_approval", "title": "Get Approval Detail", "anchor_step_id": "approval"},
                {"capability_id": "cap_options", "title": "List Seals", "anchor_step_id": "options"},
            ],
            "capability_relations": [],
            "unresolved_items": [],
        },
        "ops": "",
    }

    normalized = agent_tools_module._normalize_recording_plan_submission(raw_plan, spec)
    capabilities = normalized["semantic_plan"]["capabilities"]

    assert {item["name"] for item in capabilities} == {"cap_submit", "cap_page"}
    submit_capability = next(item for item in capabilities if item["name"] == "cap_submit")
    assert submit_capability["kind"] == "submit"
    assert submit_capability["step_ids"] == ["definition", "approval", "submit"]
    query_capability = next(item for item in capabilities if item["name"] == "cap_page")
    assert query_capability["step_ids"] == ["page"]
    field = normalized["semantic_plan"]["field_semantics"][0]
    assert field["category"] == "runtime_var"
    assert field["source_kind"] == "previous_response"
    assert field["confidence"] == 0.95

    spec.capabilities = [flow_module.FlowCapability(
        name="submit_create",
        title="Old Incorrect Combined Capability",
        kind="submit",
        nodes=[
            {"id": "call_1", "type": "call", "step_id": step_id}
            for step_id in ("page", "definition", "approval", "submit")
        ],
        updated_by="planner",
    )]
    spec.meta = {"capability_model": {"source": "pi_agent_patch", "status": "ready"}}

    repaired = asyncio.run(flow_module.orchestrate_flow_capabilities(
        spec,
        submission=normalized,
        generation_mode="optimize",
    ))

    assert {cap.kind for cap in repaired.capabilities} == {"query_status", "submit"}
    repaired_submit = next(cap for cap in repaired.capabilities if cap.kind == "submit")
    repaired_query = next(cap for cap in repaired.capabilities if cap.kind == "query_status")
    assert repaired_query.step_ids == ["page"]
    assert repaired_submit.step_ids == ["definition", "approval", "submit"]
    repaired_seal = next(param for param in repaired.steps[3].params if param.path == "sealId")
    assert repaired_seal.source_kind == "api_option"
    option_ref = next(ref for ref in repaired_submit.request_refs if ref.step_id == "options")
    assert option_ref.usage == "option_source"

def test_recording_plan_normalizes_labeled_step_ids_from_real_agent_output():
    spec = FlowSpec(steps=[
        FlowStep(
            step_id="0f576fe00bfe",
            method="POST",
            path="/admin-api/oa/seal-apply/submit-process",
            source_meta={"role": "submit_anchor"},
        ),
        FlowStep(
            step_id="10caab0f4afe",
            method="GET",
            path="/admin-api/oa/seal-apply/page",
            source_meta={"role": "business_get"},
        ),
    ])
    raw_plan = {
        "semantic_plan": {
            "business_understanding": {
                "summary": "Submit and query seal applications",
            },
            "request_roles": [],
            "field_semantics": [],
            "capabilities": [
                {
                    "capability_id": "submit_seal_application",
                    "title": "Submit seal application",
                    "steps": ["step_id>0f576fe00bfe"],
                },
                {
                    "capability_id": "query_seal_applications",
                    "title": "Query seal applications",
                    "steps": ["step_id=10caab0f4afe"],
                },
                {
                    "capability_id": "invented_capability",
                    "title": "Invented capability",
                    "steps": ["step_id=not-recorded"],
                },
            ],
            "capability_relations": [],
            "unresolved_items": [],
        },
        "ops": [],
    }

    normalized = agent_tools_module._normalize_recording_plan_submission(
        raw_plan, spec,
    )
    capabilities = {
        item["name"]: item
        for item in normalized["semantic_plan"]["capabilities"]
    }

    assert set(capabilities) == {
        "submit_seal_application", "query_seal_applications",
    }
    assert capabilities["submit_seal_application"]["kind"] == "submit"
    assert capabilities["submit_seal_application"]["step_ids"] == ["0f576fe00bfe"]
    assert capabilities["query_seal_applications"]["kind"] == "query_status"
    assert capabilities["query_seal_applications"]["step_ids"] == ["10caab0f4afe"]


def test_internal_only_capability_is_ignored_without_rejecting_the_plan():
    spec = FlowSpec(steps=[FlowStep(
        step_id="options", method="GET", path="/api/users/options",
        source_meta={"role": "read_option"},
        response_json={"data": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]},
    )])
    raw_plan = {"semantic_plan": {
        "business_understanding": {"summary": "User options"},
        "request_roles": [], "field_semantics": [],
        "capabilities": [{
            "capability_id": "list_users", "title": "List users",
            "anchor_step_id": "options",
        }],
        "capability_relations": [], "unresolved_items": [],
    }, "ops": []}

    normalized = agent_tools_module._normalize_recording_plan_submission(raw_plan, spec)

    assert normalized["semantic_plan"]["capabilities"] == []
    assert normalized["semantic_plan"]["unresolved_items"][0]["type"] == "internal_or_unmatched_capability"


def test_recording_plan_rejects_semantic_fields_outside_semantic_plan():
    malformed = {
        "semantic_plan": {"business_understanding": "发起请假申请单"},
        "field_semantics": [],
        "capabilities": [],
        "capability_relations": [],
        "unresolved_items": [],
    }

    with pytest.raises(ToolError, match="必须位于 plan.semantic_plan 内"):
        agent_tools_module._normalize_recording_plan_submission(malformed, FlowSpec())


def test_recording_plan_requires_complete_semantic_contract():
    incomplete = {
        "semantic_plan": {
            "business_understanding": "发起请假申请单",
            "request_roles": [],
        },
    }

    with pytest.raises(ToolError, match="缺少必填字段"):
        agent_tools_module._normalize_recording_plan_submission(incomplete, FlowSpec())

def test_screenshot_normalization_replaces_stale_axes_for_all_control_types():
    controls = [
        ("title", "text", {}, "string"),
        ("amount", "number", {}, "number"),
        ("useDate", "date", {}, "date"),
        ("approved", "switch", {}, "boolean"),
        ("status", "select", {"options": [
            {"label": "Open", "value": "open"},
            {"label": "Closed", "value": "closed"},
        ]}, "enum"),
        ("tags", "checkbox", {"options": [
            {"label": "A", "value": "a"},
            {"label": "B", "value": "b"},
        ]}, "list-enum"),
        ("files", "upload", {"multiple": True}, "array"),
    ]
    params = [
        ParamField(
            path=path,
            key=path,
            label=path,
            value="stale",
            type="string",
            wire_type="string",
            category="runtime_var",
            source_kind="current_user",
            exposed_to_user=False,
        )
        for path, _kind, _extra, _expected in controls
    ]
    spec = FlowSpec(steps=[
        FlowStep(
            step_id="submit",
            method="POST",
            path="/api/generic/submit",
            params=params,
        ),
    ])
    raw_plan = {
        "_analysis_screenshot_count": 1,
        "semantic_plan": {
            "business_understanding": {"summary": "Generic form submission"},
            "request_roles": [{
                "step_id": "submit",
                "role": "business_write",
                "name": "Submit form",
                "reason": "Recorded submit request",
            }],
            "field_semantics": [
                {
                    "step_id": "submit",
                    "wire_path": path,
                    "public_name": f"Visible {path}",
                    "business_type": "string",
                    "category": "user_param",
                    "source_kind": "user_input",
                    "confidence": 0.99,
                    "evidence": [{
                        "source": "screenshot",
                        "screenshot_name": "form.png",
                        "control_kind": kind,
                        "editable": True,
                        **extra,
                    }],
                }
                for path, kind, extra, _expected in controls
            ],
            "capabilities": [{
                "name": "submit_generic",
                "title": "Submit form",
                "intent": "Submit visible form fields",
                "kind": "submit",
                "step_ids": ["submit"],
            }],
            "capability_relations": [],
            "unresolved_items": [],
        },
        "ops": [],
    }

    normalized = agent_tools_module._normalize_recording_plan_submission(raw_plan, spec)
    by_path = {
        item["wire_path"]: item
        for item in normalized["semantic_plan"]["field_semantics"]
    }

    for path, _kind, _extra, expected_type in controls:
        assert by_path[path]["business_type"] == expected_type
        assert by_path[path]["category"] == "user_param"
        assert by_path[path]["source_kind"] == (
            "current_user" if expected_type in {"enum", "list-enum"} else "user_input"
        )
        assert by_path[path]["evidence"][0]["source"] == "screenshot"


def test_image_free_normalization_does_not_relabel_a_model_axis_as_grounded():
    param = ParamField(
        path="ownerId", key="ownerId", type="string", wire_type="string",
        category="runtime_var", source_kind="current_user",
    )
    spec = FlowSpec(steps=[
        FlowStep(step_id="submit", method="POST", path="/api/task", params=[param]),
    ])
    raw_plan = {"semantic_plan": {
        "business_understanding": {"summary": "Task"},
        "request_roles": [],
        "field_semantics": [{
            "step_id": "submit", "wire_path": "ownerId", "public_name": "Owner",
            "business_type": "enum", "category": "user_param", "source_kind": "user_input",
            "confidence": 0.99, "evidence": "Model-only guess",
        }],
        "capabilities": [],
        "capability_relations": [],
        "unresolved_items": [],
    }, "ops": []}

    field = agent_tools_module._normalize_recording_plan_submission(
        raw_plan, spec,
    )["semantic_plan"]["field_semantics"][0]
    assert (field["category"], field["source_kind"]) == ("user_param", "user_input")
    assert field["evidence"] == [{"source": "pi_analysis", "detail": "Model-only guess"}]


def test_screenshot_normalization_preserves_unresolved_axes_and_canonicalizes_evidence():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/task",
        params=[ParamField(
            path="ownerId", key="审批人", label="审批人", type="enum",
            default_value=148, required=True, category="user_param", source_kind="api_option",
        )],
    )])
    raw_plan = {"_analysis_screenshot_count": 1, "semantic_plan": {
        "business_understanding": {"summary": "提交审批"},
        "request_roles": [{
            "step_id": "submit", "role": "business_write",
            "name": "提交审批", "reason": "录制的提交请求",
        }],
        "field_semantics": [{
            "step_id": "submit", "wire_path": "ownerId", "public_name": "审批人",
            "business_type": "enum", "category": "user_param", "source_kind": "user_input",
            "default_value": 148, "required": True, "confidence": 0.95,
            "axis_status": {
                "path": "grounded", "name": "image_matched", "default_value": "grounded",
                "type": "image_matched", "category": "grounded", "source": "unresolved",
                "required": "image_matched",
            },
            "evidence": [{
                "source": "screenshot", "screenshot_name": "form.png",
                "visible_label": "审批人", "control_kind": "select", "editable": True,
                "supported_axis": ["name", "type", "required"],
            }, {
                "source": "recorder_facts",
                "support_axis": ["path", "default_value", "category", "source"],
            }],
        }],
        "capabilities": [{
            "name": "submit_task", "title": "提交审批", "intent": "提交审批",
            "kind": "submit", "step_ids": ["submit"],
        }],
        "capability_relations": [],
        "unresolved_items": [{"kind": "options_not_visible", "severity": "low", "blocking": False}],
    }, "ops": []}

    normalized = agent_tools_module._normalize_recording_plan_submission(
        raw_plan, spec,
    )
    field = normalized["semantic_plan"]["field_semantics"][0]

    assert field["evidence"][0]["axes"] == ["name", "type", "required"]
    assert field["source_kind"] == "api_option"
    assert field["axis_status"]["source"] == "preserved_fact"
    preserved = next(
        item for item in field["evidence"]
        if item.get("source") == "recorded_flow_spec"
        and item.get("kind") == "preserved_fact"
    )
    assert {"path", "default_value", "category", "source"}.issubset(
        preserved["axes"]
    )
    assert sum(
        item.get("canonical_screenshot_control") is True
        for item in field["evidence"]
    ) == 1
    assert flow_module._semantic_plan_coverage(spec, normalized)["complete"] is True


def test_r2_plan_normalization_rejects_ambiguous_normalized_wire_paths():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        params=[
            ParamField(path="id", key="根编号"),
            ParamField(path="body.id", key="请求体编号"),
        ],
    )])
    raw_plan = {"semantic_plan": {
        "business_understanding": {},
        "request_roles": [],
        "field_semantics": [{
            "step_id": "submit",
            "wire_path": "id",
            "public_name": "编号",
            "business_type": "string",
            "source_kind": "user_input",
            "confidence": 0.9,
        }],
        "capabilities": [],
        "capability_relations": [],
        "unresolved_items": [],
    }, "ops": []}

    semantic = agent_tools_module._normalize_recording_plan_submission(
        raw_plan, spec,
    )["semantic_plan"]
    assert semantic["field_semantics"] == []
    assert semantic["unresolved_items"][0]["kind"] == "unmatched_field"
    assert semantic["unresolved_items"][0]["step_id"] == "submit"
    assert semantic["unresolved_items"][0]["wire_path"] == "id"
    assert semantic["unresolved_items"][0]["reason"] == "字段引用不存在或不唯一"


def test_transport_filled_semantic_keys_are_rejected_for_agent_retry():
    with pytest.raises(ToolError, match="未实际提交完整字段.*field_semantics"):
        agent_tools_module._require_complete_submitted_semantic_keys({
            "_submitted_semantic_keys": [
                "business_understanding", "capabilities",
            ],
        })

    agent_tools_module._require_complete_submitted_semantic_keys({
        "_submitted_semantic_keys": [
            "business_understanding", "request_roles", "field_semantics",
            "capabilities", "capability_relations", "unresolved_items",
        ],
    })

    agent_tools_module._require_complete_submitted_semantic_keys({
        "_submitted_semantic_keys": [
            "business_understanding", "request_roles", "field_semantics",
            "capabilities",
        ],
        "semantic_plan": {"field_semantics": [{
            "step_id": "submit", "wire_path": "title",
        }]},
    }, allow_screenshot_field_overlay=True)


def test_screenshot_field_overlay_survives_invalid_capability_batch(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-field-overlay")
    session.analysis_image_count = 1
    result = asyncio.run(submit_recording_plan("run-field-overlay", {
        "recording_id": "rec-field-overlay",
        "base_flow_version": 1,
        "plan": {
            "_submitted_semantic_keys": [
                "business_understanding", "request_roles", "field_semantics",
                "capabilities", "capability_relations", "unresolved_items",
            ],
            "semantic_plan": {
                "business_understanding": {"summary": "提交申请"},
                "request_roles": [],
                "field_semantics": [{
                    "step_id": "submit",
                    "wire_path": "title",
                    "public_name": "申请标题",
                    "business_type": "string",
                    "category": "user_param",
                    "source_kind": "user_input",
                    "confidence": 0.95,
                    "evidence": [{
                        "source": "screenshot",
                        "visible_label": "申请标题",
                        "control_kind": "text",
                        "editable": True,
                    }],
                }],
                "capabilities": [{
                    "name": "submit_application",
                    "kind": "submit",
                    "step_ids": ["submit"],
                }],
                "capability_relations": [],
                "unresolved_items": [],
            },
            # Reproduces a non-field planner failure in the same tool call.
            "ops": "invalid",
        },
    }))

    assert result["partial_field_overlay"] is True
    assert "已保留可验证的截图字段修正" in result["warning"]
    assert session.spec.steps[0].params[0].label == "申请标题"
    assert session.last_submission_kind == "plan"


def test_recovered_long_screenshot_payload_applies_fields_with_missing_empty_keys(
    monkeypatch,
):
    session = _bind(monkeypatch, recording_id="rec-long-overlay")
    session.analysis_image_count = 1
    result = asyncio.run(submit_recording_plan("run-long-overlay", {
        # The active run already owns this identity.  Real multimodal model
        # calls can omit it and must not spend another full inference retrying.
        "base_flow_version": 1,
        "plan": {
            # The JS boundary recovered these real keys from the tool-call
            # outer object; two trailing empty arrays were truncated.
            "_submitted_semantic_keys": [
                "business_understanding", "request_roles", "field_semantics",
                "capabilities",
            ],
            "semantic_plan": {
                "business_understanding": {"summary": "提交申请"},
                "request_roles": [],
                "field_semantics": [{
                    "step_id": "submit",
                    "wire_path": "title",
                    "public_name": "申请标题",
                    "business_type": "string",
                    "category": "user_param",
                    "source_kind": "user_input",
                    "confidence": 0.95,
                    "evidence": [{
                        "source": "screenshot",
                        "visible_label": "申请标题",
                        "control_kind": "text",
                        "editable": True,
                    }],
                }],
                "capabilities": [{
                    "name": "submit_application",
                    "kind": "submit",
                    "step_ids": ["submit"],
                }],
                "capability_relations": [],
                "unresolved_items": [],
            },
            "ops": [],
        },
    }))

    assert result["flow_version"] > 1
    assert session.spec.steps[0].params[0].label == "申请标题"


def test_recording_tool_still_rejects_an_explicit_cross_session_identity(monkeypatch):
    _bind(monkeypatch, recording_id="rec-owned-by-run")

    with pytest.raises(ToolError, match="recording_id 与当前录制会话不匹配"):
        asyncio.run(get_recording_state("run-owned-by-run", {
            "recording_id": "rec-from-another-run",
        }))


def test_length_truncated_screenshot_plan_finishes_without_retry_loop(monkeypatch):
    session = _bind(monkeypatch, recording_id="rec-truncated-plan")
    session.analysis_image_count = 2

    result = asyncio.run(submit_recording_plan("run-truncated-plan", {
        "base_flow_version": 1,
        "submission_error": "model_output_truncated_missing_plan",
    }))

    assert result["accepted"] is True
    assert result["unchanged"] is True
    assert session.last_submission_kind == "plan"
    assert "结构化计划在模型输出上限前未完成" in result["warning"]


def test_r2_plan_normalization_does_not_fill_missing_semantic_axes_from_old_values():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        params=[ParamField(path="title", key="旧标题", type="string")],
    )])
    raw_plan = {"semantic_plan": {
        "business_understanding": {},
        "request_roles": [],
        "field_semantics": [{"step_id": "submit", "wire_path": "title"}],
        "capabilities": [],
        "capability_relations": [],
        "unresolved_items": [],
    }, "ops": []}

    field = agent_tools_module._normalize_recording_plan_submission(
        raw_plan, spec,
    )["semantic_plan"]["field_semantics"][0]
    assert "public_name" not in field
    assert "business_type" not in field
    assert field["confidence"] == 0.0


def test_screenshot_plan_keeps_grounded_field_when_image_has_no_field_evidence():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit",
        method="POST",
        path="/api/task",
        source_meta={"role": "business_write"},
        params=[ParamField(path="title", key="标题")],
    )])
    raw_plan = {
        "_analysis_screenshot_count": 1,
        "semantic_plan": {
            "business_understanding": {"summary": "Submit task"},
            "request_roles": [{
                "step_id": "submit", "role": "business_write",
                "name": "Submit task", "reason": "recorded request",
            }],
            "field_semantics": [{
                "step_id": "submit", "wire_path": "title",
                "public_name": "标题", "business_type": "string",
                "category": "user_param", "source_kind": "user_input",
                "confidence": 0.99, "evidence": [{"source": "pi_analysis"}],
            }],
            "capabilities": [{
                "name": "submit_task", "title": "Submit task",
                "intent": "Submit task", "kind": "submit", "step_ids": ["submit"],
            }],
            "capability_relations": [],
            "unresolved_items": [],
        },
        "ops": [],
    }

    field = agent_tools_module._normalize_recording_plan_submission(
        raw_plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert (field["step_id"], field["wire_path"]) == ("submit", "title")
    assert field["evidence"][0] == {"source": "pi_analysis"}
    assert field["evidence"][-1]["source"] == "recorded_flow_spec"


def test_no_screenshot_plan_keeps_existing_compatibility_for_empty_semantic_lists():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/task",
        source_meta={"role": "business_write"},
        params=[ParamField(path="title", key="标题")],
    )])
    raw_plan = {"semantic_plan": {
        "business_understanding": {"summary": "Submit task"},
        "request_roles": [], "field_semantics": [], "capabilities": [],
        "capability_relations": [], "unresolved_items": [],
    }, "ops": []}

    normalized = agent_tools_module._normalize_recording_plan_submission(raw_plan, spec)

    assert normalized["semantic_plan"]["request_roles"][0]["step_id"] == "submit"


def test_legacy_primary_step_plan_keeps_grounded_query_and_submit_boundaries():
    query = FlowStep(
        step_id="query", method="GET", path="/api/applications/page?pageNo=1&pageSize=10",
        source_meta={"role": "read_context", "control_preflight_for_write": True},
        response_json={"data": {"list": [{"id": "one", "status": 1}], "total": 1}},
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/api/applications/submit",
        source_meta={"role": "business_write"},
    )
    raw_plan = {
        "semantic_plan": {
            "business_understanding": "Submit and query applications",
            "request_roles": [],
            "field_semantics": [],
            "capabilities": [
                {
                    "capability_id": "submit_application",
                    "category": "business_write",
                    "primary_step": "submit",
                    "precondition_steps": [],
                    "post_steps": ["query"],
                },
                {
                    "capability_id": "query_applications",
                    "category": "business_query",
                    "primary_step": "query",
                },
            ],
            "capability_relations": [],
            "unresolved_items": [],
        },
        "ops": [],
    }

    normalized = agent_tools_module._normalize_recording_plan_submission(
        raw_plan, FlowSpec(steps=[query, submit]),
    )
    by_name = {
        item["name"]: item
        for item in normalized["semantic_plan"]["capabilities"]
    }

    assert set(by_name) == {"query_applications", "submit_application"}
    assert by_name["query_applications"]["kind"] == "query_status"
    assert by_name["query_applications"]["step_ids"] == ["query"]
    assert by_name["submit_application"]["kind"] == "submit"
    assert by_name["submit_application"]["step_ids"] == ["submit"]


def test_capability_request_step_ids_are_normalized_to_grounded_boundaries():
    spec = FlowSpec(steps=[
        FlowStep(
            step_id="query", method="GET", path="/api/applications/page?status=1",
            source_meta={"role": "business_get"},
        ),
        FlowStep(
            step_id="submit", method="POST", path="/api/applications/submit",
            source_meta={"role": "business_write"},
        ),
    ])
    raw_plan = {"semantic_plan": {
        "business_understanding": {"summary": "Query and submit applications"},
        "request_roles": [], "field_semantics": [],
        "capabilities": [
            {
                "capability_id": "query_applications",
                "capability_type": "business_get",
                "request_step_ids": ["query"],
            },
            {
                "capability_id": "submit_application",
                "capability_type": "business_write",
                "request_step_ids": ["submit"],
            },
        ],
        "capability_relations": [], "unresolved_items": [],
    }, "ops": []}

    normalized = agent_tools_module._normalize_recording_plan_submission(raw_plan, spec)
    by_name = {
        item["name"]: item
        for item in normalized["semantic_plan"]["capabilities"]
    }

    assert by_name["query_applications"]["step_ids"] == ["query"]
    assert by_name["submit_application"]["step_ids"] == ["submit"]


def test_semicolon_record_field_semantics_are_normalized_instead_of_dropped():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/applications/submit",
        source_meta={"role": "business_write"},
        params=[ParamField(
            path="useInfo", key="useInfo", value="1", type="string",
            category="user_param", source_kind="user_input",
        )],
    )])
    raw_plan = {"semantic_plan": {
        "business_understanding": "Submit an application",
        "request_roles": [
            "step_id=submit;role=business_write;name=Submit application",
        ],
        "field_semantics": [
            "step_id=submit;wire_path=useInfo;public_name=Usage description;"
            "business_type=string;category=user_param;source_kind=user_input;"
            "confidence=0.95;evidence=recorded textarea",
        ],
        "capabilities": [{
            "name": "submit_application", "title": "Submit application",
            "kind": "submit", "primary_step": "submit",
        }],
        "capability_relations": [], "unresolved_items": [],
    }, "ops": []}

    normalized = agent_tools_module._normalize_recording_plan_submission(raw_plan, spec)
    field = normalized["semantic_plan"]["field_semantics"][0]

    assert field["step_id"] == "submit"
    assert field["wire_path"] == "useInfo"
    assert field["public_name"] == "Usage description"
    assert field["business_type"] == "string"
    assert field["confidence"] == 0.95


def _screenshot_match_plan(field_semantics: list[dict]) -> dict:
    return {
        "_analysis_screenshot_count": 1,
        "semantic_plan": {
            "business_understanding": {"summary": "Submit request"},
            "request_roles": [{
                "step_id": "submit", "role": "business_write",
                "name": "Submit request", "reason": "recorded request",
            }],
            "field_semantics": field_semantics,
            "capabilities": [{
                "name": "submit_request", "title": "Submit request",
                "intent": "Submit request", "kind": "submit", "step_ids": ["submit"],
            }],
            "capability_relations": [],
            "unresolved_items": [],
        },
        "ops": [],
    }


def test_screenshot_field_without_model_wire_identity_matches_unique_recorded_label():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        source_meta={"role": "business_write"},
        params=[
            ParamField(path="title", key="申请标题", label="申请标题"),
            ParamField(path="remark", key="备注", label="备注"),
        ],
    )])
    plan = _screenshot_match_plan([{
        "public_name": "备注", "business_type": "string",
        "category": "user_param", "source_kind": "user_input",
        "required": False, "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "备注", "control_kind": "textarea", "editable": True,
        }],
    }])

    normalized = agent_tools_module._normalize_recording_plan_submission(plan, spec)
    field = normalized["semantic_plan"]["field_semantics"][0]

    assert (field["step_id"], field["wire_path"]) == ("submit", "remark")
    assert field["match"]["status"] == "confirmed"
    assert "label_exact" in field["match"]["reasons"]


def test_screenshot_field_does_not_guess_between_duplicate_recorded_labels():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        source_meta={"role": "business_write"},
        params=[
            ParamField(path="applicant.remark", key="备注", label="备注"),
            ParamField(path="review.remark", key="备注", label="备注"),
        ],
    )])
    plan = _screenshot_match_plan([{
        "public_name": "备注", "business_type": "string",
        "category": "user_param", "source_kind": "user_input",
        "required": False, "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "备注", "control_kind": "textarea", "editable": True,
        }],
    }])

    semantic = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]

    assert semantic["field_semantics"] == []
    assert semantic["unresolved_items"][0]["kind"] == "unmatched_field"


def test_exact_recorded_reference_survives_same_label_on_another_step() -> None:
    spec = FlowSpec(steps=[
        FlowStep(
            step_id="query", method="GET", path="/api/records",
            params=[ParamField(path="query.status", key="流程状态", label="流程状态")],
        ),
        FlowStep(
            step_id="submit", method="POST", path="/api/request",
            params=[ParamField(path="status", key="流程状态", label="流程状态")],
        ),
    ])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "status", "public_name": "流程状态",
        "business_type": "string", "category": "user_param",
        "source_kind": "user_input", "confidence": 0.99,
        "evidence": [{
            "source": "screenshot", "visible_label": "流程状态",
            "control_kind": "text", "editable": True,
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert (field["step_id"], field["wire_path"]) == ("submit", "status")
    assert field["match"]["reasons"] == ["exact_recorded_reference"]


def test_screenshot_field_can_match_unique_recorded_value_and_control_type():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        source_meta={"role": "business_write"},
        params=[
            ParamField(
                path="quantity", key="unknownQuantity", value=7,
                type="enum", wire_type="number", source_kind="api_option",
            ),
            ParamField(path="title", key="unknownTitle", value="demo", wire_type="string"),
        ],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "wrong.path", "public_name": "数量",
        "business_type": "number", "category": "user_param",
        "source_kind": "user_input", "required": True, "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "数量", "visible_value": 7,
            "control_kind": "number", "editable": True,
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert (field["step_id"], field["wire_path"]) == ("submit", "quantity")
    assert {"recorded_value", "control_type"}.issubset(field["match"]["reasons"])


def test_screenshot_strong_evidence_corrects_an_existing_but_wrong_wire_hint():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        source_meta={"role": "business_write"},
        params=[
            ParamField(path="wrongCount", key="错误数量", value=0, wire_type="number"),
            ParamField(path="roomCount", key="roomCount", value=7, wire_type="number"),
        ],
    )], meta={"field_evidence": [{
        "field_aliases": ["roomCount"], "label": "房间数量", "control_kind": "number",
    }]})
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "wrongCount", "public_name": "房间数量",
        "business_type": "number", "category": "user_param",
        "source_kind": "user_input", "required": True, "confidence": 0.61,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "房间数量", "visible_value": 7,
            "control_kind": "number", "editable": True,
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert (field["step_id"], field["wire_path"]) == ("submit", "roomCount")
    assert field["confidence"] >= 0.8
    assert {"label_exact", "recorded_value", "control_type"}.issubset(field["match"]["reasons"])


def test_unique_recorded_values_can_correct_swapped_stale_field_names() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[
            ParamField(path="x1", key="Beta", label="Beta", value=10, wire_type="number"),
            ParamField(path="x2", key="Alpha", label="Alpha", value=20, wire_type="number"),
        ],
    )])
    plan = _screenshot_match_plan([
        {
            "public_name": label, "business_type": "number",
            "category": "user_param", "source_kind": "user_input", "confidence": 0.99,
            "evidence": [{
                "source": "screenshot", "visible_label": label,
                "visible_value": value, "control_kind": "number", "editable": True,
            }],
        }
        for label, value in (("Alpha", 10), ("Beta", 20))
    ])

    fields = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"]

    assert [(field["wire_path"], field["public_name"]) for field in fields] == [
        ("x1", "Alpha"), ("x2", "Beta"),
    ]
    assert all("unique_recorded_value" in field["match"]["reasons"] for field in fields)


def test_screenshot_ambiguous_value_does_not_discard_an_exact_recorded_reference():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[
            ParamField(path="roomCount", key="roomCount", value=1, wire_type="number"),
            ParamField(path="userCount", key="userCount", value=1, wire_type="number"),
        ],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "roomCount", "public_name": "入住人数",
        "business_type": "number", "category": "user_param",
        "source_kind": "user_input", "confidence": 0.99,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "入住人数", "visible_value": 1,
            "control_kind": "number", "editable": True,
        }],
    }])

    semantic = agent_tools_module._normalize_recording_plan_submission(plan, spec)["semantic_plan"]

    assert semantic["field_semantics"][0]["wire_path"] == "roomCount"
    assert semantic["field_semantics"][0]["match"]["reasons"] == ["exact_recorded_reference"]


def test_screenshot_field_does_not_guess_from_duplicate_values():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[
            ParamField(path="firstCount", key="firstCount", value=1, wire_type="number"),
            ParamField(path="secondCount", key="secondCount", value=1, wire_type="number"),
        ],
    )])
    plan = _screenshot_match_plan([{
        "public_name": "数量", "business_type": "number", "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "数量", "visible_value": 1,
            "control_kind": "number", "editable": True,
        }],
    }])

    semantic = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]

    assert semantic["field_semantics"] == []
    assert semantic["unresolved_items"][0]["kind"] == "unmatched_field"


def test_screenshot_choice_without_wire_mapping_does_not_create_empty_page_enum():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="status", key="流程状态", type="string",
            category="user_param", source_kind="unknown",
        )],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "status", "public_name": "流程状态",
        "business_type": "enum", "category": "user_param",
        "source_kind": "page_enum", "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "流程状态", "control_kind": "select", "editable": True,
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert field["business_type"] == "enum"
    assert field["source_kind"] == "unknown"
    assert field["axis_status"]["type"] == "image_matched"
    assert field["axis_status"]["source"] == "preserved_fact"
    assert any(
        item.get("kind") == "enum_mapping" and item.get("blocking") is False
        for item in agent_tools_module._normalize_recording_plan_submission(
            plan, spec,
        )["semantic_plan"]["unresolved_items"]
    )


@pytest.mark.parametrize(("multiple", "expected"), [(False, "enum"), (True, "list-enum")])
def test_screenshot_picker_proves_choice_type_but_not_screenshot_wire_values(
    multiple: bool, expected: str,
):
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="ownerId", key="负责人", value="u1", type="string",
            category="user_param", source_kind="user_input",
        )],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "ownerId", "public_name": "负责人",
        "business_type": "string", "category": "user_param",
        "source_kind": "page_enum", "confidence": 0.99,
        "evidence": [{
            "source": "screenshot", "visible_label": "负责人",
            "control_kind": "picker", "editable": True, "multiple": multiple,
            "options": [
                {"label": "甲", "value": "u1"},
                {"label": "乙", "value": "u2"},
            ],
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert field["business_type"] == expected
    assert field["source_kind"] == "user_input"
    assert "enum_options" not in field
    assert any(
        item.get("kind") == "enum_mapping"
        for item in agent_tools_module._normalize_recording_plan_submission(
            plan, spec,
        )["semantic_plan"]["unresolved_items"]
    )


@pytest.mark.parametrize(
    ("axes", "expected_category", "expected_source"),
    [
        (["source"], "runtime_var", "user_input"),
        (["category"], "user_param", "current_user"),
    ],
)
def test_screenshot_category_and_source_axes_are_independent(
    axes: list[str], expected_category: str, expected_source: str,
):
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="remark", key="备注", type="string",
            category="runtime_var", source_kind="current_user",
        )],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "remark", "public_name": "备注",
        "business_type": "string", "category": "user_param",
        "source_kind": "user_input", "confidence": 0.99,
        "evidence": [{
            "source": "screenshot", "visible_label": "备注",
            "control_kind": "textarea", "editable": True, "axes": axes,
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert field["category"] == expected_category
    assert field["source_kind"] == expected_source


def test_screenshot_current_value_change_does_not_invalidate_exact_recorded_field():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(path="title", key="标题", value="旧标题", type="string")],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "title", "public_name": "标题",
        "business_type": "string", "category": "user_param",
        "source_kind": "user_input", "confidence": 0.99,
        "evidence": [{
            "source": "screenshot", "control_kind": "text", "editable": True,
            "visible_value": "本次新标题", "axes": ["type", "source"],
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert field["wire_path"] == "title"
    assert field["default_value"] is None


def test_screenshot_placeholder_names_fall_back_to_unique_recorded_paths():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[
            ParamField(path="participants[0].userId", key="userId", label="userId"),
            ParamField(path="reviewers[0].userId", key="userId", label="userId"),
        ],
    )])
    plan = _screenshot_match_plan([
        {
            "step_id": "submit", "wire_path": path, "public_name": "-",
            "business_type": "string", "category": "user_param",
            "source_kind": "user_input", "confidence": 0.99,
            "evidence": [{
                "source": "screenshot", "control_kind": "text", "editable": True,
                "axes": ["type", "source"],
            }],
        }
        for path in ("participants[0].userId", "reviewers[0].userId")
    ])

    fields = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"]

    assert [field["public_name"] for field in fields] == [
        "participants[0].userId", "reviewers[0].userId",
    ]


def test_multiple_screenshot_controls_merge_independently_of_upload_order():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="status", key="流程状态", value="pending", type="enum",
            category="user_param", source_kind="page_enum",
            enum_options=[
                {"label": "待处理", "value": "pending"},
                {"label": "已完成", "value": "done"},
            ],
            enum_value_map={"待处理": "pending", "已完成": "done"},
        )],
    )])
    readonly = {
        "source": "screenshot", "screenshot_name": "list.png",
        "visible_label": "流程状态", "control_kind": "text", "read_only": True,
        "axes": ["name"],
    }
    editable = {
        "source": "screenshot", "screenshot_name": "form.png",
        "visible_label": "流程状态", "control_kind": "select", "editable": True,
        "required": True, "axes": ["type", "source", "required"],
        "options": [
            {"label": "待处理", "value": "pending"},
            {"label": "已完成", "value": "done"},
        ],
    }

    outputs = []
    for evidence in ([readonly, editable], [editable, readonly]):
        plan = _screenshot_match_plan([{
            "step_id": "submit", "wire_path": "status", "public_name": "流程状态",
            "business_type": "enum", "category": "user_param",
            "source_kind": "page_enum", "confidence": 0.99,
            "evidence": evidence,
        }])
        field = agent_tools_module._normalize_recording_plan_submission(
            plan, spec,
        )["semantic_plan"]["field_semantics"][0]
        outputs.append((
            field["business_type"], field["source_kind"], field["required"],
            field.get("enum_options"), field["axis_status"],
        ))

    assert outputs[0] == outputs[1]
    assert outputs[0][:3] == ("enum", "page_enum", True)


def test_implicit_all_axes_is_not_narrowed_by_another_screenshot() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="amount", key="amount", value=1, type="string",
            category="runtime_var", source_kind="current_user",
        )],
    )])
    all_axes = {
        "source": "screenshot", "visible_label": "金额",
        "control_kind": "number", "editable": True,
    }
    name_only = {
        "source": "screenshot", "visible_label": "金额",
        "control_kind": "number", "editable": True, "axes": ["name"],
    }

    outputs = []
    for evidence in ([all_axes, name_only], [name_only, all_axes]):
        plan = _screenshot_match_plan([{
            "step_id": "submit", "wire_path": "amount", "public_name": "金额",
            "business_type": "number", "category": "user_param",
            "source_kind": "user_input", "confidence": 0.99, "evidence": evidence,
        }])
        field = agent_tools_module._normalize_recording_plan_submission(
            plan, spec,
        )["semantic_plan"]["field_semantics"][0]
        outputs.append((field["business_type"], field["category"], field["source_kind"]))

    assert outputs == [("number", "user_param", "user_input")] * 2


def test_name_only_screenshot_cannot_change_other_field_axes() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="amount", key="amount", label="amount", value="1",
            type="string", wire_type="string", required=False,
            category="runtime_var", source_kind="current_user",
        )],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "amount", "public_name": "金额",
        "business_type": "number", "category": "user_param",
        "source_kind": "user_input", "required": True, "confidence": 0.99,
        "axis_status": {
            "path": "image_matched", "name": "image_matched",
            "default_value": "image_matched", "type": "image_matched",
            "category": "image_matched", "source": "image_matched",
            "required": "image_matched",
        },
        "evidence": [{
            "source": "screenshot", "visible_label": "金额",
            "control_kind": "number", "editable": True, "axes": ["name"],
        }],
    }])

    normalized = agent_tools_module._normalize_recording_plan_submission(plan, spec)
    field = normalized["semantic_plan"]["field_semantics"][0]
    assert (
        field["public_name"], field["business_type"], field["category"],
        field["source_kind"], field["required"],
    ) == ("金额", "string", "runtime_var", "current_user", False)

    optimized = asyncio.run(flow_module.orchestrate_flow_capabilities(
        spec, submission=normalized, generation_mode="optimize",
    ))
    final = optimized.steps[0].params[0]
    assert (
        final.label, final.type, final.category, final.source_kind, final.required,
    ) == ("金额", "string", "runtime_var", "current_user", False)


def test_screenshot_run_cannot_change_field_without_screenshot_evidence() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="amount", key="原名称", label="原名称", value="1",
            type="string", required=False, category="runtime_var",
            source_kind="current_user",
        )],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "amount", "public_name": "伪造名称",
        "business_type": "number", "category": "user_param",
        "source_kind": "user_input", "required": True, "confidence": 0.99,
        "axis_status": {
            axis: "image_matched"
            for axis in (
                "path", "name", "default_value", "type",
                "category", "source", "required",
            )
        },
        "evidence": [{"source": "pi_analysis", "detail": "model claim only"}],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert (
        field["public_name"], field["business_type"], field["category"],
        field["source_kind"], field["required"],
    ) == ("原名称", "string", "runtime_var", "current_user", False)


@pytest.mark.parametrize(
    ("sequence", "expected_type", "expected_control"),
    [
        (("text", "select"), "enum", "select"),
        (("select", "text"), "string", "text"),
    ],
)
def test_later_screenshot_replaces_derived_evidence_from_prior_analysis(
    sequence: tuple[str, str],
    expected_type: str,
    expected_control: str,
) -> None:
    current = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="value", key="值", label="值", value="a",
            type="string", wire_type="string", category="user_param",
            source_kind="user_input",
        )],
    )])

    prior_screenshot_evidence: list[dict] = []
    for control_kind in sequence:
        business_type = "enum" if control_kind == "select" else "string"
        raw_control = {
            "source": "screenshot", "visible_label": "值",
            "control_kind": control_kind, "editable": True, "axes": ["type"],
        }
        plan = _screenshot_match_plan([{
            "step_id": "submit", "wire_path": "value", "public_name": "值",
            "business_type": business_type, "category": "user_param",
            "source_kind": "user_input", "confidence": 0.99,
            "evidence": [*prior_screenshot_evidence, raw_control],
        }])
        normalized = agent_tools_module._normalize_recording_plan_submission(plan, current)
        current = asyncio.run(flow_module.orchestrate_flow_capabilities(
            current, submission=normalized, generation_mode="optimize",
        ))
        prior_screenshot_evidence = [
            dict(item) for item in current.steps[0].params[0].evidence
            if str(item.get("source") or "").lower() == "screenshot"
        ]

    field = current.steps[0].params[0]
    canonicals = [
        item for item in field.evidence
        if item.get("canonical_screenshot_control") is True
    ]
    assert field.type == expected_type
    assert len(canonicals) == 1
    assert canonicals[0]["control_kind"] == expected_control


def test_equal_strength_conflicting_control_types_preserve_recorded_type() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(path="value", key="值", value="1", type="string")],
    )])
    controls = [
        {"source": "screenshot", "visible_label": "值", "control_kind": "text", "editable": True},
        {"source": "screenshot", "visible_label": "值", "control_kind": "number", "editable": True},
    ]
    final_contracts = []
    for evidence in (controls, list(reversed(controls))):
        plan = _screenshot_match_plan([{
            "step_id": "submit", "wire_path": "value", "public_name": "值",
            "business_type": "number", "category": "user_param",
            "source_kind": "user_input", "confidence": 0.99,
            "evidence": evidence,
        }])
        normalized = agent_tools_module._normalize_recording_plan_submission(plan, spec)
        semantic = normalized["semantic_plan"]
        field = semantic["field_semantics"][0]
        assert field["business_type"] == "string"
        assert field["axis_status"]["type"] == "preserved_fact"
        assert any(
            item.get("kind") == "control_type_conflict"
            for item in semantic["unresolved_items"]
        )
        optimized = asyncio.run(flow_module.orchestrate_flow_capabilities(
            spec, submission=normalized, generation_mode="optimize",
        ))
        final = optimized.steps[0].params[0]
        final_contracts.append((final.type, final.source_kind))

    assert final_contracts == [("string", "unknown")] * 2


def test_explicit_business_role_wins_over_option_source_membership_heuristic():
    query = FlowStep(
        step_id="query", method="GET", path="/api/users/page",
        response_json={"data": {"list": [{"id": 1, "name": "甲"}]}},
        source_meta={"role": "business_get"},
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="ownerId", key="负责人", value=1, source_kind="api_option",
            source={"source_step_id": "query", "source_url": "/api/users/page"},
        )],
        source_meta={"role": "business_write"},
    )
    spec = FlowSpec(steps=[query, submit])
    raw_plan = {"semantic_plan": {
        "business_understanding": {"summary": "查询并提交"},
        "request_roles": [], "field_semantics": [],
        "capabilities": [{
            "name": "query_users", "title": "查询用户", "intent": "查询用户",
            "kind": "query_status", "step_ids": ["query"],
        }],
        "capability_relations": [], "unresolved_items": [],
    }, "ops": []}

    normalized = agent_tools_module._normalize_recording_plan_submission(raw_plan, spec)
    capability = normalized["semantic_plan"]["capabilities"][0]

    assert capability["request_refs"][0]["usage"] == "execute"
    assert flow_module._planned_capability_has_public_anchor(
        spec, "query_status", ["query"],
    ) is True


def test_business_get_can_be_execute_and_grounded_option_source_per_capability():
    query = FlowStep(
        step_id="query", method="GET", path="/api/users/page",
        response_json={"data": {"list": [{"id": 1, "name": "甲"}]}},
        source_meta={"role": "business_get"},
    )
    submit = FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="ownerId", key="负责人", value=1, source_kind="api_option",
            source={"source_step_id": "query", "source_url": "/api/users/page"},
        )],
        source_meta={"role": "business_write"},
    )
    spec = FlowSpec(steps=[query, submit])
    raw_plan = {"semantic_plan": {
        "business_understanding": {"summary": "查询并提交"},
        "request_roles": [], "field_semantics": [],
        "capabilities": [{
            "name": "query_users", "title": "查询用户", "intent": "查询用户",
            "kind": "query_status",
            "request_refs": [{"step_id": "query", "usage": "execute"}],
        }, {
            "name": "submit_request", "title": "提交申请", "intent": "提交申请",
            "kind": "submit",
            "request_refs": [
                {"step_id": "query", "usage": "option_source"},
                {"step_id": "submit", "usage": "execute"},
            ],
        }],
        "capability_relations": [], "unresolved_items": [],
    }, "ops": []}

    normalized = agent_tools_module._normalize_recording_plan_submission(raw_plan, spec)
    capabilities = {
        item["name"]: {
            (ref["step_id"], ref["usage"])
            for ref in item["request_refs"]
        }
        for item in normalized["semantic_plan"]["capabilities"]
    }

    assert capabilities["query_users"] == {("query", "execute")}
    assert capabilities["submit_request"] == {
        ("query", "option_source"), ("submit", "execute"),
    }


def test_explicit_read_option_cannot_become_public_query_capability():
    option_step = FlowStep(
        step_id="people", method="GET", path="/api/hr/user/page",
        response_json={"data": {"list": [{"id": 1, "name": "甲"}]}},
        source_meta={"role": "read_option"},
    )

    assert flow_module._planned_capability_has_public_anchor(
        FlowSpec(steps=[option_step]), "query_status", ["people"],
    ) is False


@pytest.mark.parametrize(
    ("required_evidence", "expected", "status"),
    [
        ({"required": False}, True, "preserved_fact"),
        ({
            "required": False,
            "required_convention_confirmed": True,
            "label_region_complete": True,
        }, False, "image_matched"),
    ],
)
def test_screenshot_optional_requires_complete_required_convention(
    required_evidence, expected, status,
):
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(path="remark", key="备注", required=True)],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "remark", "public_name": "备注",
        "business_type": "string", "category": "user_param",
        "source_kind": "user_input", "required": False, "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "备注", "control_kind": "text", "editable": True,
            **required_evidence,
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert field["required"] is expected
    assert field["axis_status"]["required"] == status


def test_explicit_screenshot_required_marker_overrides_model_false() -> None:
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(path="title", key="标题", required=False)],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "title", "public_name": "标题",
        "business_type": "string", "category": "user_param",
        "source_kind": "user_input", "required": False, "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "visible_label": "标题",
            "control_kind": "text", "editable": True, "required": True,
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert field["required"] is True
    assert field["axis_status"]["required"] == "image_matched"


def test_screenshot_placeholder_names_preserve_unique_recorded_keys():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[
            ParamField(path="description", key="description", type="enum"),
            ParamField(path="remark", key="remark", type="enum"),
        ],
    )])
    fields = [{
        "step_id": "submit", "wire_path": path, "public_name": "-",
        "business_type": "string", "category": "user_param",
        "source_kind": "user_input", "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "-", "control_kind": "text", "editable": True,
        }],
    } for path in ("description", "remark")]

    normalized = agent_tools_module._normalize_recording_plan_submission(
        _screenshot_match_plan(fields), spec,
    )["semantic_plan"]["field_semantics"]

    assert [field["public_name"] for field in normalized] == ["description", "remark"]
    assert all(field["business_type"] == "string" for field in normalized)
    assert all(field["source_kind"] == "user_input" for field in normalized)


def test_screenshot_without_name_evidence_preserves_existing_display_name():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(path="reasonDes", key="reasonDes", label="事项描述")],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "reasonDes", "public_name": "猜测名称",
        "business_type": "textarea", "category": "user_param",
        "source_kind": "user_input", "confidence": 0.98,
        "axis_status": {"type": "image_matched"},
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "control_kind": "textarea", "editable": True, "axes": ["type"],
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert field["public_name"] == "事项描述"
    assert field["business_type"] == "string"


def test_screenshot_visible_value_never_overwrites_recorded_default():
    spec = FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        params=[ParamField(
            path="roomCount", key="房间数量", value=1, default_value=1,
            type="number", wire_type="number",
        )],
    )])
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "roomCount", "public_name": "房间数量",
        "default_value": 99, "business_type": "number", "category": "user_param",
        "source_kind": "user_input", "required": True, "confidence": 0.98,
        "axis_status": {"default_value": "image_matched"},
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "房间数量", "visible_value": 99,
            "control_kind": "number", "editable": True,
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert field["default_value"] == 1
    assert field["axis_status"]["default_value"] == "preserved_fact"


@pytest.mark.parametrize("existing_capabilities", [False, True])
@pytest.mark.asyncio
async def test_partial_screenshot_updates_initial_and_existing_capability_plans(
    monkeypatch, existing_capabilities,
):
    session = _bind(monkeypatch)
    session.analysis_image_count = 1
    session.spec = ensure_flow_version(FlowSpec(
        steps=[FlowStep(
            step_id="submit", method="POST", path="/api/request",
            source_meta={"role": "business_write"},
            params=[
                ParamField(
                    path="quantity", key="unknownQuantity", value=7,
                    type="enum", wire_type="number", category="user_param",
                    source_kind="api_option",
                    source={"kind": "api_option", "source_url": "/api/unrelated/options"},
                    enum_options=[{"label": "unrelated", "value": 7}],
                    evidence=[{
                        "source": "recorder_dom", "control_kind": "number",
                        "editable": True, "disabled": False, "read_only": False,
                    }],
                ),
                ParamField(path="untouched", key="untouched", value="same"),
            ],
        )],
        capabilities=[flow_module.FlowCapability(
            capability_id="existing", name="existing", title="Existing",
            kind="submit", nodes=[{"id": "call-submit", "type": "call", "step_id": "submit"}],
        )] if existing_capabilities else [],
    ), "recorded", reason="test")
    plan = _screenshot_match_plan([{
        "step_id": "submit", "wire_path": "wrong.path", "public_name": "数量",
        "business_type": "number", "category": "user_param",
        "source_kind": "user_input", "required": True, "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "数量", "visible_value": 7,
            "control_kind": "number", "editable": True,
        }],
    }, {
        "public_name": "无法匹配", "business_type": "string",
        "category": "user_param", "source_kind": "user_input", "confidence": 0.8,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "无法匹配", "control_kind": "text", "editable": True,
        }],
    }])

    await submit_recording_plan("run-1", {
        "recording_id": session.recording_id,
        "base_flow_version": int(session.spec.meta["current_version"]),
        "plan": plan,
    })

    quantity, untouched = session.spec.steps[0].params
    assert (quantity.key, quantity.type, quantity.source_kind) == ("数量", "number", "user_input")
    assert not quantity.enum_options
    assert (untouched.key, untouched.value) == ("untouched", "same")
    assert session.spec.capabilities
    assert session.last_submission_kind == "plan"


def test_screenshot_field_uses_recorder_alias_label_from_flow_metadata():
    spec = FlowSpec(
        steps=[FlowStep(
            step_id="submit", method="POST", path="/api/hotel/apply",
            source_meta={"role": "business_write"},
            params=[ParamField(path="roomCount", key="roomCount", label="roomCount")],
        )],
        meta={"field_evidence": [{
            "field_aliases": ["roomCount"],
            "label": "房间数量",
            "control_kind": "number",
        }]},
    )
    plan = _screenshot_match_plan([{
        "public_name": "房间数量", "business_type": "number",
        "category": "user_param", "source_kind": "user_input",
        "required": True, "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "hotel.png",
            "visible_label": "房间数量", "control_kind": "number", "editable": True,
        }],
    }])

    field = agent_tools_module._normalize_recording_plan_submission(
        plan, spec,
    )["semantic_plan"]["field_semantics"][0]

    assert (field["step_id"], field["wire_path"]) == ("submit", "roomCount")
    assert field["public_name"] == "房间数量"


@pytest.mark.asyncio
async def test_unmatched_screenshot_field_does_not_block_capability_plan(monkeypatch):
    session = _bind(monkeypatch)
    session.analysis_image_count = 1
    session.spec = ensure_flow_version(FlowSpec(steps=[FlowStep(
        step_id="submit", method="POST", path="/api/request",
        source_meta={"role": "business_write"},
        params=[
            ParamField(path="applicant.remark", key="备注", label="备注"),
            ParamField(path="review.remark", key="备注", label="备注"),
        ],
    )]), "recorded", reason="test")
    plan = _screenshot_match_plan([{
        "public_name": "备注", "business_type": "string",
        "category": "user_param", "source_kind": "user_input",
        "required": False, "confidence": 0.98,
        "evidence": [{
            "source": "screenshot", "screenshot_name": "form.png",
            "visible_label": "备注", "control_kind": "textarea", "editable": True,
        }],
    }])

    result = await submit_recording_plan("run-1", {
        "recording_id": session.recording_id,
        "base_flow_version": int(session.spec.meta["current_version"]),
        "plan": plan,
    })

    assert result["flow_version"] == int(session.spec.meta["current_version"])
    assert session.spec.capabilities
    assert session.received_submission["semantic_plan"]["field_semantics"] == []
    assert session.received_submission["semantic_plan"]["unresolved_items"]
    assert session.last_submission_kind == "plan"


@pytest.mark.asyncio
async def test_invalid_screenshot_plan_finishes_without_model_retry(monkeypatch):
    session = _bind(monkeypatch)
    session.analysis_image_count = 1

    result = await submit_recording_plan("run-1", {
        "recording_id": session.recording_id,
        "base_flow_version": int(session.spec.meta["current_version"]),
        "plan": {"semantic_plan": {"business_understanding": {}}},
    })

    assert result["accepted"] is True
    assert result["unchanged"] is True
    assert "当前配置未修改" in result["warning"]
    assert session.last_submission_kind == "plan"


def test_capability_memberships_use_recorded_internal_roles_not_model_execute_labels():
    spec = FlowSpec(steps=[
        FlowStep(
            step_id="options", method="GET", path="/api/seals/options",
            source_meta={"role": "read_option"},
        ),
        FlowStep(
            step_id="definition", method="GET", path="/api/process-definition",
            source_meta={"role": "process_definition", "control_preflight_for_write": True},
        ),
        FlowStep(
            step_id="submit", method="POST", path="/api/seal/submit",
            source_meta={"role": "business_write"},
            params=[ParamField(path="sealId", key="公章")],
        ),
    ])
    raw_plan = {"semantic_plan": {
        "business_understanding": {"summary": "Submit seal request"},
        "request_roles": [],
        "field_semantics": [],
        "capabilities": [{
            "name": "submit_seal", "title": "Submit seal request",
            "intent": "Submit seal request", "kind": "submit",
            "request_refs": [
                {"step_id": "options", "usage": "execute"},
                {"step_id": "definition", "usage": "execute"},
                {"step_id": "submit", "usage": "preflight"},
            ],
        }],
        "capability_relations": [], "unresolved_items": [],
    }, "ops": []}

    normalized = agent_tools_module._normalize_recording_plan_submission(raw_plan, spec)
    capability = normalized["semantic_plan"]["capabilities"][0]
    usages = {item["step_id"]: item["usage"] for item in capability["request_refs"]}

    assert usages == {
        "options": "option_source",
        "definition": "preflight",
        "submit": "execute",
    }
    assert capability["step_ids"] == ["definition", "submit"]


@pytest.mark.parametrize(
    ("method", "requested_kind", "expected_kind"),
    [
        ("POST", "query_status", "submit"),
        ("GET", "submit", "query_status"),
    ],
)
def test_capability_kind_cannot_contradict_recorded_execute_method(
    method: str,
    requested_kind: str,
    expected_kind: str,
):
    role = "business_write" if method == "POST" else "business_get"
    path = "/api/task/submit" if method == "POST" else "/api/task/page"
    response_json = None if method == "POST" else {"data": {"list": [], "total": 0}}
    spec = FlowSpec(steps=[FlowStep(
        step_id="anchor", method=method, path=path,
        source_meta={"role": role}, response_json=response_json,
    )])
    raw_plan = {"semantic_plan": {
        "business_understanding": {"summary": "Task"},
        "request_roles": [], "field_semantics": [],
        "capabilities": [{
            "name": "task", "title": "Task", "intent": "Task",
            "kind": requested_kind, "step_ids": ["anchor"],
        }],
        "capability_relations": [], "unresolved_items": [],
    }, "ops": []}

    normalized = agent_tools_module._normalize_recording_plan_submission(raw_plan, spec)

    assert normalized["semantic_plan"]["capabilities"][0]["kind"] == expected_kind
