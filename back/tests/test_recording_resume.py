from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from dano.assets.drafts import AssetDraft
from dano.onboarding.recording_gateway import (
    RecordingGatewaySession,
    RecordingSessionConfig,
    RecordingSessionRegistry,
)
from dano.onboarding.recording_workflow import (
    PipelineOutcome,
    RecordingWorkflow,
    WorkflowSnapshot,
    WorkflowStatus,
)
from dano.shared.enums import AssetType, Subsystem


def _draft() -> dict:
    return {
        "title": "请假",
        "capabilities": [{"capability_id": "cap_submit", "name": "submit"}],
        "request_facts": {"requests": [{"request_id": "req_1"}]},
    }


class FakePipeline:
    def __init__(self) -> None:
        self.seeds: list[object] = []

    async def run(self, seed, context):  # noqa: ANN001
        self.seeds.append(seed)
        context.remember_draft(seed.draft or {})
        return PipelineOutcome(
            status=WorkflowStatus.PUBLISHED,
            draft=seed.draft,
            release={"ok": True},
        )


async def _send(_payload: dict) -> None:
    return None


def _config() -> RecordingSessionConfig:
    return RecordingSessionConfig(
        tenant="tenant",
        subsystem="oa",
        recording_id="recording_" + "a" * 32,
        action="action_" + "b" * 32,
        start_url="",
        goal_text="提交请假",
    )


@pytest.mark.asyncio
async def test_resume_does_not_open_browser_or_rematerialize(monkeypatch) -> None:
    pipeline = FakePipeline()
    monkeypatch.setattr(
        "dano.onboarding.recording_gateway.SelfHealingPipeline",
        lambda *_args, **_kwargs: pipeline,
    )

    async def materializer(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("继续优化不得重新物化阶段 1～6")

    session = RecordingGatewaySession(
        config=_config(),
        send=_send,
        pi_factory=lambda _fresh: (_ for _ in ()).throw(AssertionError("no live pi")),
        publisher=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("fake pipeline")),
    )
    session._materialize = materializer  # type: ignore[method-assign]
    draft = _draft()
    await session.start_verification_only(draft, title="请假申请", result_id=uuid4())
    assert session.workflow is not None
    await session.workflow.wait()

    assert session.capture is None
    assert pipeline.seeds
    assert pipeline.seeds[0].kind == "edited_spec"
    assert pipeline.seeds[0].machine_verification is True
    assert pipeline.seeds[0].draft == draft
    assert session.workflow.snapshot.draft == draft
    assert len(session.workflow.snapshot.draft["capabilities"]) == 1


@pytest.mark.asyncio
async def test_dispatch_works_without_capture() -> None:
    session = RecordingGatewaySession(
        config=_config(),
        send=_send,
        pi_factory=lambda _fresh: (_ for _ in ()).throw(AssertionError()),
        publisher=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()),
    )
    session.workflow = RecordingWorkflow(
        WorkflowSnapshot(
            run_id="r1",
            action="action_1",
            status=WorkflowStatus.EDITABLE,
            draft=_draft(),
            capture_frozen=True,
        ),
        FakePipeline(),
    )
    session.capture = None

    await session.dispatch({"type": "ping"})
    await session.dispatch({"type": "cancel"})
    with pytest.raises(ValueError, match="没有页面录制"):
        await session.dispatch({"type": "input", "event": {}})


@pytest.mark.asyncio
async def test_failed_verification_marks_saved_result_as_attempted(monkeypatch) -> None:
    result_id = uuid4()
    calls: list[dict] = []

    class Store:
        async def patch_recording_result_flags(self, saved_id, **flags):  # noqa: ANN001
            calls.append({"saved_id": saved_id, **flags})

    monkeypatch.setattr("dano.assets.drafts.DraftStore", Store)
    session = RecordingGatewaySession(
        config=_config(),
        send=None,
        pi_factory=lambda _fresh: (_ for _ in ()).throw(AssertionError()),
        publisher=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()),
    )
    session._stage_six_result_id = result_id
    session._machine_verification = True

    await session._on_snapshot(WorkflowSnapshot(
        run_id="r1",
        action="action_1",
        status=WorkflowStatus.FAILED,
        draft=_draft(),
    ))

    assert calls == [{
        "saved_id": result_id,
        "published": None,
        "machine_verification_ran": True,
    }]


@pytest.mark.asyncio
async def test_persist_stage_six_notifies_history_once(monkeypatch) -> None:
    sent: list[dict] = []
    saved_id = uuid4()

    class Store:
        async def save_draft(self, **kwargs):  # noqa: ANN003
            return AssetDraft(
                asset_draft_id=saved_id,
                run_id=kwargs["run_id"],
                tenant=kwargs["scope"].tenant,
                subsystem=kwargs["scope"].subsystem,
                asset_type=kwargs["asset_type"],
                asset_key=kwargs["asset_key"],
                body=kwargs["body"],
                content_hash="sha256:test",
                created_at=datetime.now(timezone.utc),
            )

    async def send(payload):  # noqa: ANN001
        sent.append(payload)

    monkeypatch.setattr("dano.assets.drafts.DraftStore", Store)
    session = RecordingGatewaySession(
        config=_config(),
        send=send,
        pi_factory=lambda _fresh: (_ for _ in ()).throw(AssertionError()),
        publisher=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()),
    )
    await session._persist_stage_six(_draft())

    assert session._stage_six_result_id == saved_id
    assert [item["type"] for item in sent] == ["recording_result_saved"]
    assert sent[0]["result"]["id"] == str(saved_id)
    assert sent[0]["result"]["action"] == session.config.action
    assert "flow_spec" not in sent[0]["result"]


@pytest.mark.asyncio
async def test_attach_or_resume_reuses_verification_session() -> None:
    registry = RecordingSessionRegistry()
    first = RecordingGatewaySession(
        config=_config(),
        send=_send,
        pi_factory=lambda _fresh: (_ for _ in ()).throw(AssertionError()),
        publisher=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()),
    )
    first.capture = None
    first.workflow = RecordingWorkflow(
        WorkflowSnapshot(run_id="r1", action=first.config.action, draft=_draft()),
        FakePipeline(),
    )
    registry._sessions[first.config.action] = first

    attached = await registry.attach_or_resume(
        config=_config(),
        send=_send,
        pi_factory=lambda _fresh: (_ for _ in ()).throw(AssertionError()),
        publisher=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()),
        draft=_draft(),
        title="请假",
    )
    assert attached is first


@pytest.mark.asyncio
async def test_delete_recording_result_does_not_delete_skill(monkeypatch) -> None:
    from dano.gateway import app as gateway

    monkeypatch.setattr(gateway, "_auth_tenant", AsyncMock(return_value="tenant"))

    async def delete_skill(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("删除历史结果不得删除已发布 Skill")

    monkeypatch.setattr(gateway.repo, "delete_by_action", delete_skill)

    class Store:
        async def delete_recording_result(self, result_id, *, tenant):  # noqa: ANN001
            assert tenant == "tenant"
            return True

    monkeypatch.setattr("dano.assets.drafts.DraftStore", Store)
    result = await gateway.delete_recording_result(str(uuid4()), x_tenant_key="key")
    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_list_recording_results_returns_summaries_only(monkeypatch) -> None:
    from dano.gateway import app as gateway
    from dano.onboarding.recording_results import recording_result_asset_key, stage_six_result_body

    monkeypatch.setattr(gateway, "_auth_tenant", AsyncMock(return_value="tenant"))
    body = stage_six_result_body(
        action="action_1",
        title="请假申请",
        goal="提交请假",
        tenant="tenant",
        subsystem="oa",
        draft=_draft(),
    )
    saved = AssetDraft(
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

    class Store:
        async def list_recording_results(self, *, tenant, subsystem):  # noqa: ANN001
            assert tenant == "tenant"
            assert subsystem == "oa"
            return [saved]

    monkeypatch.setattr("dano.assets.drafts.DraftStore", Store)
    rows = await gateway.list_recording_results(subsystem="oa", x_tenant_key="key")
    assert rows[0]["title"] == "请假申请"
    assert "flow_spec" not in rows[0]


def test_setup_history_does_not_autostart_recording() -> None:
    recorder = (
        Path(__file__).resolve().parents[2]
        / "skillfrontend"
        / "src"
        / "components"
        / "PageRecorder.tsx"
    ).read_text(encoding="utf-8")
    assert "历史录制结果" in recorder
    assert "继续优化" in recorder
    assert 'title: "Skill"' in recorder
    assert "产出时间" in recorder
    assert "等待确认" in recorder
    assert "回复并继续" in recorder
    assert "renderOperatorQuestion" in recorder
    assert "请将我接下来在页面中实际完成的每项业务操作分别生成一个可调用能力。" not in recorder.split("历史录制结果", 1)[1].split("function renderRecording()", 1)[0]
    assert "listRecordingResults(subsystem)" in recorder
    assert 'type: "resume_verification"' in recorder
    assert "实时验证日志" in recorder
    assert "disabled={historyLocked}" in recorder
    assert "recording_result_saved" in recorder
    assert "setInterval" not in recorder
    history_load = recorder.split("setHistoryLoading(true)", 1)[1].split("}, [tenant, subsystem]);", 1)[0]
    assert "listRecordingResults(subsystem)" in history_load
    assert "openRecordingSocket" not in history_load
    assert "new WebSocket" not in history_load
    result_view = recorder.split("function renderResult()", 1)[1]
    assert "showLiveVerificationLog ? renderVerificationLog() : (" in result_view
    assert "RESULT_STATUS_BOX_STYLE" in recorder
    assert "renderVerificationLog" in recorder
    assert "defaultActiveKey={[]}" in recorder
    assert 'title: "执行状态"' in recorder
    assert "正在发布" in recorder
    assert "historyExecutionStatus" in recorder
    assert "activityDisplay" in recorder
    assert 'label: "发现了"' in recorder
    assert 'label: "准备处理"' in recorder
