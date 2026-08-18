from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from dano.assets.drafts import AssetDraft, DraftStore
from dano.onboarding.recording_results import (
    persist_stage_six_result,
    recording_result_asset_key,
    recording_result_summary,
    stage_six_result_body,
)
from dano.onboarding.recording_workflow import (
    PipelineCheck,
    PipelineContext,
    PipelineSeed,
    SelfHealingPipeline,
    WorkflowIssue,
    WorkflowStatus,
    _draft_fingerprint,
)
from dano.shared.enums import AssetType, Subsystem
from dano.shared.models import Scope


async def _progress(*_args) -> None:  # noqa: ANN002
    return None


async def _no_ask(_question) -> str:  # noqa: ANN001
    raise AssertionError("operator must not be asked")


def _context(**kwargs) -> PipelineContext:  # noqa: ANN003
    return PipelineContext(
        progress=kwargs.get("progress", _progress),
        ask_operator=kwargs.get("ask_operator", _no_ask),
        cancelled=lambda: False,
        persist_stage_six=kwargs.get("persist_stage_six"),
    )


def _stage_six_draft() -> dict:
    return {
        "title": "请假",
        "capabilities": [{"capability_id": "cap_submit", "name": "submit"}],
        "request_facts": {"requests": [{"request_id": "req_1"}, {"request_id": "req_2"}]},
    }


class FakeDraftStore:
    def __init__(self) -> None:
        self.rows: list[AssetDraft] = []

    async def save_draft(self, **kwargs):  # noqa: ANN003
        body = dict(kwargs["body"])
        draft = AssetDraft(
            asset_draft_id=uuid4(),
            run_id=kwargs["run_id"],
            tenant=kwargs["scope"].tenant,
            subsystem=kwargs["scope"].subsystem,
            asset_type=kwargs["asset_type"],
            asset_key=kwargs["asset_key"],
            body=body,
            content_hash="sha256:test",
            created_at=datetime.now(timezone.utc),
        )
        self.rows.append(draft)
        return draft

    async def get_draft(self, asset_draft_id):  # noqa: ANN001
        return next((row for row in self.rows if row.asset_draft_id == asset_draft_id), None)


@pytest.mark.asyncio
async def test_saved_fingerprint_matches_stage_six_draft() -> None:
    draft = _stage_six_draft()
    store = FakeDraftStore()
    saved = await persist_stage_six_result(
        store,  # type: ignore[arg-type]
        run_id="recording_1",
        scope=Scope(tenant="tenant", subsystem=Subsystem("oa")),
        action="action_1",
        title="请假申请",
        goal="提交请假",
        draft=draft,
    )

    assert saved.asset_key == recording_result_asset_key("action_1")
    assert saved.asset_type == AssetType.PAGE_SCRIPT
    assert saved.body["flow_spec"] is draft or saved.body["flow_spec"] == draft
    assert saved.body["fingerprint"] == _draft_fingerprint(draft)
    assert saved.body["capability_count"] == 1
    assert saved.body["request_count"] == 2
    assert saved.body["flow_spec"]["capabilities"][0]["capability_id"] == "cap_submit"


@pytest.mark.asyncio
async def test_verification_off_and_on_both_persist() -> None:
    saved: list[dict] = []

    async def persist(draft):  # noqa: ANN001
        saved.append(dict(draft))

    class OffRuntime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return _stage_six_draft()

        async def check(self, draft, context):  # noqa: ANN001
            raise AssertionError("verification off must not check")

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("verification off must not repair")

        async def publish(self, draft, context):  # noqa: ANN001
            return {"ok": True}

    off = await SelfHealingPipeline(OffRuntime()).run(
        PipelineSeed(kind="recording", machine_verification=False),
        _context(persist_stage_six=persist),
    )
    assert off.status == WorkflowStatus.PUBLISHED
    assert saved[-1]["capabilities"][0]["capability_id"] == "cap_submit"

    class OnRuntime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return _stage_six_draft()

        async def check(self, draft, context):  # noqa: ANN001
            return PipelineCheck(draft=draft, issues=())

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("no issues")

        async def publish(self, draft, context):  # noqa: ANN001
            return {"ok": True}

    on = await SelfHealingPipeline(OnRuntime()).run(
        PipelineSeed(kind="recording", machine_verification=True),
        _context(persist_stage_six=persist),
    )
    assert on.status == WorkflowStatus.PUBLISHED
    assert len(saved) == 2
    assert saved[0] == saved[1]


@pytest.mark.asyncio
async def test_stage_seven_failure_keeps_saved_stage_six() -> None:
    saved: list[dict] = []

    async def persist(draft):  # noqa: ANN001
        saved.append(dict(draft))

    issue = WorkflowIssue(issue_id="blocked", code="ext", message="外部阻断", resolver="external_blocked")

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return _stage_six_draft()

        async def check(self, draft, context):  # noqa: ANN001
            return PipelineCheck(draft={"capabilities": []}, issues=(issue,))

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("blocked issues must not repair")

        async def publish(self, draft, context):  # noqa: ANN001
            raise AssertionError("must not publish")

    outcome = await SelfHealingPipeline(Runtime()).run(
        PipelineSeed(kind="recording", machine_verification=True),
        _context(persist_stage_six=persist),
    )

    assert outcome.status == WorkflowStatus.EDITABLE
    assert saved[0]["capabilities"][0]["capability_id"] == "cap_submit"
    assert saved[0] != outcome.draft


@pytest.mark.asyncio
async def test_edited_spec_resume_does_not_persist_again() -> None:
    saved: list[dict] = []

    async def persist(draft):  # noqa: ANN001
        saved.append(draft)

    class Runtime:
        async def prepare(self, seed, context):  # noqa: ANN001
            return seed.draft

        async def check(self, draft, context):  # noqa: ANN001
            return PipelineCheck(draft=draft, issues=())

        async def repair(self, draft, issues, operator_answers, context):  # noqa: ANN001
            raise AssertionError("no repair")

        async def publish(self, draft, context):  # noqa: ANN001
            return {"ok": True}

    outcome = await SelfHealingPipeline(Runtime()).run(
        PipelineSeed(kind="edited_spec", draft=_stage_six_draft(), machine_verification=True),
        _context(persist_stage_six=persist),
    )

    assert outcome.status == WorkflowStatus.PUBLISHED
    assert saved == []


def test_result_summary_omits_full_flow_spec() -> None:
    body = stage_six_result_body(
        action="action_1",
        title="请假申请",
        goal="提交请假",
        tenant="tenant",
        subsystem="oa",
        draft=_stage_six_draft(),
    )
    draft = AssetDraft(
        asset_draft_id=uuid4(),
        run_id="r1",
        tenant="tenant",
        subsystem=Subsystem("oa"),
        asset_type=AssetType.PAGE_SCRIPT,
        asset_key=recording_result_asset_key("action_1"),
        body=body,
        content_hash="sha256:test",
        created_at=datetime.now(timezone.utc),
    )
    summary = recording_result_summary(draft)
    assert "flow_spec" not in summary
    assert summary["title"] == "请假申请"
    assert summary["capability_count"] == 1
    assert summary["action"] == "action_1"


@pytest.mark.asyncio
async def test_delete_recording_result_only_matches_result_keys(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Conn:
        async def fetchrow(self, sql, *args):  # noqa: ANN001, ANN002
            captured["sql"] = sql
            captured["args"] = args
            return None

    class Pool:
        def acquire(self):
            return _Acquire(Conn())

    monkeypatch.setattr("dano.assets.drafts.get_pool", lambda: Pool())
    result_id = uuid4()
    deleted = await DraftStore().delete_recording_result(result_id, tenant="tenant")
    assert deleted is False
    assert "recording-result:%" in captured["args"]
    assert result_id in captured["args"]
    assert "tenant" in captured["args"]


class _Acquire:
    def __init__(self, conn) -> None:  # noqa: ANN001
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args) -> bool:  # noqa: ANN002
        return False
