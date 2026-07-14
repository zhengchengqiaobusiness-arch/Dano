"""Recording core uses Pi submissions and deterministic gates only."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest

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
    ensure_flow_version,
    flow_spec_fingerprint,
)
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


class _Session:
    def __init__(self, recording_id: str = "rec-1") -> None:
        self.recording_id = recording_id
        self.spec = _spec()
        self.last_review = {}
        self.last_submission_kind = ""

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
    assert session.spec.request_facts.model_dump(mode="json") == before_facts

    validation = asyncio.run(get_validation_report("run-recording", {"recording_id": "rec-1"}))
    assert validation["flow_version"] == result["flow_version"]
    assert "report" in validation and "repair_context" in validation


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
        params["plan"] = {"semantic_plan": {}, "ops": []}
        call = submit_recording_plan
    else:
        params["operations"] = []
        call = submit_recording_repair
    with pytest.raises(ToolError, match="不得修改原始 request facts"):
        asyncio.run(call(f"run-atomic-{mode}", params))
    assert session.spec.model_dump(mode="json") == before_spec
    assert session.last_submission_kind == "review"
    assert session.last_review == {"sentinel": "preserve"}


def test_page_onboard_active_recording_bypasses_board_precheck_and_model_helpers(monkeypatch):
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
            "method": "POST",
            "url": "https://example.invalid/api/submit",
            "path": "/api/submit",
            "body_template": {"reason": "{{reason}}"},
            "params": ["reason"],
            "field_types": {"reason": "string"},
            "success_rule": {"field": "code", "ok_values": [0]},
        },
        sample_inputs={"reason": "demo"},
        required=["reason"],
        run_id="run-pi-publish",
        allow_repair=True,
        recording_pi_required=True,
    ))
    assert result["ok"] is True
    assert calls == ["save", "self_check", "pi_review", "publish"]


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
