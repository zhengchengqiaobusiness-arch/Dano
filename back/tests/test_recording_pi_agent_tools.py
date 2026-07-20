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


def _call_nodes(step_ids: list[str]) -> list[dict]:
    return [
        {"id": f"call_{index}", "type": "call", "step_id": step_id}
        for index, step_id in enumerate(step_ids)
    ]
from dano.onboarding.page_onboard import run_request_onboarding


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
                    "business_type": "string", "source_kind": "user_input",
                    "confidence": 0.95, "evidence": ["页面标签"],
                }],
                "capabilities": [],
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
        ("status", "select", {}, "enum"),
        ("tags", "checkbox", {"options": ["a", "b"]}, "list-enum"),
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
        assert by_path[path]["source_kind"] == "user_input"
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
    assert semantic["unresolved_items"] == [{
        "type": "unmatched_field",
        "step_id": "submit",
        "wire_path": "id",
    }]


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
