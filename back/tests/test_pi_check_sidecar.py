from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dano.gateway.app import record_ws
from dano.onboarding.pi_check_sidecar import (
    RecordingBridgeContext,
    load_pi_check_session_state,
    pi_result_storage_body,
    record_ws_uses_legacy_gateway,
    should_adopt_existing,
    sidecar_child_env,
    sidecar_enabled,
    sidecar_http_client,
)


def test_sidecar_receives_gateway_pi_credentials() -> None:
    settings = SimpleNamespace(
        pi_api_key="secret-key",
        pi_base_url="https://token-plan-cn.xiaomimimo.com/v1",
        pi_model="mimo-v2.5",
        pi_provider="",
    )
    env = sidecar_child_env(base={}, settings=settings)
    assert env["DANO_PI_API_KEY"] == "secret-key"
    assert env["DANO_PI_MODEL"] == "mimo-v2.5"
    assert env["DANO_PI_PROVIDER"] == "openai-compat"
    assert env["DANO_PI_BASE_URL"].endswith("/v1")


def test_orphan_health_is_not_adopted() -> None:
    assert should_adopt_existing(
        explicit_url=False,
        own_process_alive=False,
        healthy=True,
    ) is False
    assert should_adopt_existing(
        explicit_url=True,
        own_process_alive=False,
        healthy=True,
    ) is True
    assert should_adopt_existing(
        explicit_url=False,
        own_process_alive=True,
        healthy=True,
    ) is True


def test_pytest_does_not_auto_start_sidecar() -> None:
    assert sidecar_enabled() is False


def test_sidecar_http_bypasses_system_proxy() -> None:
    client = sidecar_http_client(2.0)
    assert client._trust_env is False
    source = inspect.getsource(sidecar_http_client)
    assert "trust_env=False" in source


def test_record_ws_does_not_start_legacy_gateway() -> None:
    source = inspect.getsource(record_ws)
    assert "proxy_recording_websocket" in source
    assert "RecordingSessionRegistry" not in source
    assert "RecordingPiSession" not in source
    assert "analyze-recording-evidence" not in source
    assert record_ws_uses_legacy_gateway() is False


def test_pi_result_storage_keeps_submitted_capabilities() -> None:
    draft = {
        "title": "村务归档",
        "capabilities": [{"name": "upload_document", "title": "上传文档"}],
        "steps": [{"params": [{"name": "file", "exposed_to_user": True}]}],
    }
    body = pi_result_storage_body(
        action="action_1",
        title="智慧乡村",
        goal="每项操作生成能力",
        tenant="admin",
        subsystem="oa",
        draft=draft,
        request_count=4,
    )
    assert body["flow_spec"] is draft
    assert body["capability_count"] == 1
    assert body["request_count"] == 1
    assert body["recording_backend"] == "pi_check"
    assert body["skill_export_description"] == ""


def test_pi_check_session_state_is_loaded_by_recorded_origin(tmp_path, monkeypatch) -> None:
    import dano.onboarding.pi_check_sidecar as sidecar

    monkeypatch.setattr(sidecar, "PI_CHECK_ROOT", tmp_path)
    session_dir = tmp_path / "data" / "sessions"
    session_dir.mkdir(parents=True)
    state = {"cookies": [{"name": "Admin-Token", "value": "secret"}], "origins": []}
    (session_dir / "boot.dianshixinxi.com_90.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )

    assert load_pi_check_session_state(
        "http://boot.dianshixinxi.com:90/oa/seal/sealApply?billType=seal_apply"
    ) == state


@pytest.mark.asyncio
async def test_recording_result_saved_is_rewritten_to_persisted_row() -> None:
    draft = {"capabilities": [{"name": "search_docs"}], "title": "查询"}
    saved_id = uuid4()

    async def fake_detail(_result_id: str) -> dict:
        return {"id": "recording_abc", "draft": draft, "request_count": 2}

    async def fake_persist(**kwargs):
        assert kwargs["draft"] is draft
        assert kwargs["request_count"] == 2
        assert kwargs["action"] == "action_1"
        return SimpleNamespace(
            asset_draft_id=saved_id,
            asset_key="recording-result:action_1",
            created_at=None,
            body={
                "action": "action_1",
                "title": "查询",
                "goal": {"text": "产出能力"},
                "capability_count": 1,
                "request_count": 2,
                "published": False,
                "created_at": "2026-09-04T00:00:00+00:00",
            },
        )

    context = RecordingBridgeContext(
        tenant="admin",
        subsystem="oa",
        title="查询",
        goal="产出能力",
        action="action_1",
        persist=fake_persist,
        fetch_detail=fake_detail,
    )
    rewritten = await context.rewrite_upstream(
        '{"type":"recording_result_saved","result":{"id":"recording_abc","action":"action_1","request_count":2}}'
    )
    payload = __import__("json").loads(rewritten)
    assert payload["result"]["id"] == str(saved_id)
    assert payload["result"]["capability_count"] == 1


@pytest.mark.asyncio
async def test_recording_result_saved_hands_pi_login_state_to_runtime(monkeypatch) -> None:
    import dano.onboarding.pi_check_sidecar as sidecar

    draft = {"capabilities": [{"name": "search_docs"}], "title": "查询"}
    saved_id = uuid4()
    state = {"cookies": [{"name": "Admin-Token", "value": "secret"}], "origins": []}
    handed_off: list[tuple[str, str, dict]] = []

    async def fake_detail(_result_id: str) -> dict:
        return {"id": "recording_abc", "draft": draft, "request_count": 2}

    async def fake_persist(**_kwargs):
        return SimpleNamespace(
            asset_draft_id=saved_id,
            asset_key="recording-result:action_1",
            created_at=None,
            body={
                "action": "action_1",
                "title": "查询",
                "goal": {"text": "产出能力"},
                "capability_count": 1,
                "request_count": 2,
                "published": False,
                "created_at": "2026-09-04T00:00:00+00:00",
            },
        )

    monkeypatch.setattr(sidecar, "load_pi_check_session_state", lambda _url: state)
    monkeypatch.setattr(
        sidecar,
        "save_session",
        lambda tenant, subsystem, storage: handed_off.append((tenant, subsystem, storage)),
    )
    context = RecordingBridgeContext(
        tenant="admin",
        subsystem="boot-dianshixinxi-com-90",
        title="查询",
        goal="产出能力",
        action="action_1",
        start_url="http://boot.dianshixinxi.com:90/oa/seal/sealApply",
        persist=fake_persist,
        fetch_detail=fake_detail,
    )

    await context.rewrite_upstream(
        '{"type":"recording_result_saved","result":{"id":"recording_abc","action":"action_1"}}'
    )

    assert handed_off == [("admin", "boot-dianshixinxi-com-90", state)]
