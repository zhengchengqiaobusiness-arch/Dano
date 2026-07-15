from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dano.assets.memory import InMemoryAssetStore
from dano.catalog.manifest import build_function_tools
from dano.orchestrator.capability_runtime import CapabilityInvokePayload, invoke_skill_capability
from dano.orchestrator.orchestrator import Orchestrator
from dano.orchestrator.skills import SkillRegistry, _call_metadata_from_body
from dano.orchestrator.types import Intent, SkillSpec
from dano.recording_v3 import (
    _DanoCredentialVault,
    _stable_evidence_secret,
    _storage_headers,
)
from dano.shared.asset_bodies import PageScriptBody
from dano.shared.enums import AssetType, RiskLevel, Subsystem, TaskState, ValidationStatus
from dano.shared.models import AssetEnvelope, Scope


def _envelope(action: str, *, top_marker: str | None, nested_marker: str) -> AssetEnvelope:
    body: dict[str, Any] = {
        "actions": [],
        "action": action,
        "title": action,
        "risk_level": "L1",
        "revision": 3,
        "api_request": {
            "recording_engine": nested_marker,
            "method": "GET",
            "url": "https://oa.example/query",
            "capabilities": [{"name": "query_status", "step_ids": ["query"]}],
        },
        "capabilities": [{"name": "query_status", "kind": "query_status", "step_ids": ["query"]}],
    }
    if top_marker is not None:
        body["recording_engine"] = top_marker
    return AssetEnvelope(
        asset_type=AssetType.PAGE_SCRIPT,
        scope=Scope(tenant="tenant-a", subsystem=Subsystem.OA),
        asset_key=action,
        version=0,
        source_fingerprint=f"source:{action}",
        validation_status=ValidationStatus.PUBLISHED,
        confidence=1,
        body=body,
    )


def test_page_script_marker_defaults_to_legacy_and_rejects_unknown_values() -> None:
    assert PageScriptBody().recording_engine == "legacy"
    with pytest.raises(ValidationError):
        PageScriptBody(recording_engine="playwright_v3_typo")


async def test_registry_uses_only_top_level_asset_marker_for_runtime_dispatch() -> None:
    store = InMemoryAssetStore()
    legacy = await store.create(_envelope("legacy_query", top_marker=None, nested_marker="playwright_v3"))
    v3 = await store.create(_envelope("v3_query", top_marker="playwright_v3", nested_marker="legacy"))
    await store.set_status(legacy.asset_id, ValidationStatus.PUBLISHED)
    await store.set_status(v3.asset_id, ValidationStatus.PUBLISHED)

    registry = await SkillRegistry.from_store(
        store, tenant="tenant-a", subsystems=[Subsystem.OA],
    )

    legacy_skill = registry.by_action(Subsystem.OA, "legacy_query")
    v3_skill = registry.by_action(Subsystem.OA, "v3_query")
    assert legacy_skill.api_request["recording_engine"] == "legacy"
    assert legacy_skill.call_metadata["recording_engine"] == "legacy"
    assert v3_skill.api_request["recording_engine"] == "playwright_v3"
    assert v3_skill.call_metadata["recording_engine"] == "playwright_v3"


def _skill() -> SkillSpec:
    return SkillSpec(
        skill_id="A-OA.query_status",
        subsystem=Subsystem.OA,
        action="query_status",
        risk_level=RiskLevel.L1,
        has_api=False,
        capabilities=[{
            "name": "query_status",
            "kind": "query_status",
            "input_schema": {"type": "object", "properties": {}},
        }],
    )


def test_unverified_v3_asset_is_catalogued_but_not_exported_as_direct_call_tool() -> None:
    verified = _skill().model_copy(update={
        "skill_id": "A-OA.verified_query",
        "action": "verified_query",
        "call_metadata": {
            "recording_engine": "playwright_v3",
            "verification_status": "verified",
            "publication_status": "published_verified",
            "direct_call_enabled": True,
            "contract_integrity": True,
        },
    })
    unverified = _skill().model_copy(update={
        "skill_id": "A-OA.unverified_query",
        "action": "unverified_query",
        "call_metadata": {
            "recording_engine": "playwright_v3",
            "verification_status": "unverified",
            "publication_status": "published_unverified",
            "direct_call_enabled": False,
            "contract_integrity": True,
        },
    })
    tools = build_function_tools([verified, unverified])
    assert [item["function"]["name"] for item in tools] == ["A-OA__verified_query"]


async def test_capability_dispatch_is_exact_and_legacy_path_is_unchanged(monkeypatch) -> None:
    calls: list[str] = []

    async def v3(**kwargs: Any) -> dict[str, Any]:
        calls.append("v3")
        assert kwargs["credential_headers"] == {"Authorization": "Bearer trusted"}
        return {"ok": True, "output": {"engine": "v3"}, "results": []}

    async def legacy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("legacy")
        return {"ok": True, "response": {"engine": "legacy"}}

    monkeypatch.setattr("dano.recording_v3.execute_v3_capability", v3)
    monkeypatch.setattr("dano.orchestrator.capability_runtime.execute_api", legacy)
    payload = CapabilityInvokePayload(input={})

    v3_result = await invoke_skill_capability(
        skill=_skill(), capability="query_status", payload=payload,
        api_request={"recording_engine": "playwright_v3", "steps": []},
        base_url="https://oa.example", credential_headers={"Authorization": "Bearer trusted"},
    )
    legacy_result = await invoke_skill_capability(
        skill=_skill(), capability="query_status", payload=payload,
        api_request={"recording_engine": "playwright_v3_typo", "method": "GET"},
        base_url="https://oa.example",
    )

    assert calls == ["v3", "legacy"]
    assert v3_result["output"] == {"engine": "v3"}
    assert legacy_result["output"] == {"engine": "legacy"}


async def test_verified_v3_executes_real_runtime_chain_with_only_transport_stubbed(
    monkeypatch,
) -> None:
    from dano.recording_v3 import ensure_recording_package, execute_v3_capability

    ensure_recording_package()
    from dano_recording.runtime import workflow_executor

    calls: list[dict[str, Any]] = []

    async def fake_send(_sender, request, *, pinned_address=None):
        calls.append({
            "method": request.method,
            "url": request.url,
            "json": request.json_body,
            "step_uuid": request.step_uuid,
            "pinned_address": pinned_address,
        })
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": {"recordId": "R-42", "accepted": True},
        }

    monkeypatch.setattr(workflow_executor, "_send", fake_send)
    step_uuid = "11111111-1111-4111-8111-111111111111"
    capability_uuid = "22222222-2222-4222-8222-222222222222"
    api_request = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "publication_status": "published_verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://8.8.8.8",
        "steps": [{
            "step_id": "submit-record",
            "step_uuid": step_uuid,
            "request_definition_id": "33333333-3333-4333-8333-333333333333",
            "method": "POST",
            "url": "https://8.8.8.8/api/records",
            "body_template": {"title": "{{input.title}}"},
            "response_schema": {
                "type": "object",
                "properties": {
                    "recordId": {"type": "string"},
                    "accepted": {"type": "boolean"},
                },
                "required": ["recordId", "accepted"],
            },
            "risk_level": "L3",
            "requires_confirmation": True,
        }],
        "capabilities": [{
            "capability_uuid": capability_uuid,
            "name": "submit_record",
            "kind": "submit",
            "step_ids": ["submit-record"],
            "step_uuids": [step_uuid],
            "request_refs": [{
                "step_id": "submit-record",
                "step_uuid": step_uuid,
                "usage": "execute",
            }],
            "risk_level": "L3",
            "requires_confirmation": True,
            "input_schema": {
                "type": "object",
                "properties": {"title": {"type": "string", "minLength": 1}},
                "required": ["title"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "recordId": {"type": "string"},
                    "accepted": {"type": "boolean"},
                },
                "required": ["recordId", "accepted"],
            },
        }],
    }

    blocked = await execute_v3_capability(
        api_request=api_request,
        fields={"title": "真实标题"},
        capability="submit_record",
        confirm=False,
        dry_run=False,
        base_url="https://8.8.8.8",
        storage_state=None,
    )
    result = await execute_v3_capability(
        api_request=api_request,
        fields={"title": "真实标题"},
        capability="submit_record",
        confirm=True,
        dry_run=False,
        base_url="https://8.8.8.8",
        storage_state=None,
    )

    assert blocked["stage"] == "confirmation_required"
    assert result["ok"] is True
    assert result["output"] == {"recordId": "R-42", "accepted": True}
    assert calls == [{
        "method": "POST",
        "url": "https://8.8.8.8/api/records",
        "json": {"title": "真实标题"},
        "step_uuid": step_uuid,
        "pinned_address": "8.8.8.8",
    }]


async def test_verified_exported_skill_runs_from_capability_entry_to_v3_transport(
    monkeypatch,
) -> None:
    """Exercise the public Dano capability boundary; only the wire send is stubbed."""
    from dano.recording_v3 import ensure_recording_package

    ensure_recording_package()
    from dano_recording.runtime import workflow_executor

    calls: list[dict[str, Any]] = []

    async def fake_send(_sender, request, *, pinned_address=None):
        calls.append({
            "method": request.method,
            "url": request.url,
            "json": request.json_body,
            "step_uuid": request.step_uuid,
            "pinned_address": pinned_address,
        })
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": {"recordId": "R-tenant-42", "accepted": True},
        }

    monkeypatch.setattr(workflow_executor, "_send", fake_send)
    step_uuid = "41111111-1111-4111-8111-111111111111"
    capability_uuid = "42222222-2222-4222-8222-222222222222"
    request_definition_id = "43333333-3333-4333-8333-333333333333"
    input_schema = {
        "type": "object",
        "properties": {"title": {"type": "string", "minLength": 1}},
        "required": ["title"],
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "properties": {
            "recordId": {"type": "string"},
            "accepted": {"type": "boolean"},
        },
        "required": ["recordId", "accepted"],
        "additionalProperties": False,
    }
    capability = {
        "capability_uuid": capability_uuid,
        "name": "submit_tenant_record",
        "kind": "submit",
        "step_ids": ["submit-tenant-record"],
        "step_uuids": [step_uuid],
        "request_refs": [{
            "step_id": "submit-tenant-record",
            "step_uuid": step_uuid,
            "usage": "execute",
        }],
        "risk_level": "L3",
        "requires_confirmation": True,
        "input_schema": input_schema,
        "output_schema": output_schema,
    }
    api_request = {
        "recording_engine": "playwright_v3",
        "verification_status": "verified",
        "publication_status": "published_verified",
        "direct_call_enabled": True,
        "recorded_origin": "https://8.8.8.8",
        "steps": [{
            "step_id": "submit-tenant-record",
            "step_uuid": step_uuid,
            "request_definition_id": request_definition_id,
            "method": "POST",
            "url": "https://8.8.8.8/api/tenant-records",
            "body_template": {
                "title": "{{input.title}}",
                "tenantId": "{{runtime.current_tenant.id}}",
            },
            "response_schema": output_schema,
            "risk_level": "L3",
            "requires_confirmation": True,
        }],
        "capabilities": [capability],
    }
    skill = SkillSpec(
        skill_id="A-OA.submit_tenant_record",
        subsystem=Subsystem.OA,
        action="submit_tenant_record",
        title="提交租户记录",
        risk_level=RiskLevel.L3,
        api_request=api_request,
        capabilities=[capability],
        call_metadata={
            "recording_engine": "playwright_v3",
            "verification_status": "verified",
            "publication_status": "published_verified",
            "direct_call_enabled": True,
            "contract_integrity": True,
        },
    )

    blocked = await invoke_skill_capability(
        skill=skill,
        capability="submit_tenant_record",
        payload=CapabilityInvokePayload(input={"title": "租户记录"}, confirm=False),
        api_request=api_request,
        base_url="https://8.8.8.8",
        runtime_context={"current_tenant": {"id": "tenant-a"}},
    )
    result = await invoke_skill_capability(
        skill=skill,
        capability="submit_tenant_record",
        payload=CapabilityInvokePayload(input={"title": "租户记录"}, confirm=True),
        api_request=api_request,
        base_url="https://8.8.8.8",
        runtime_context={"current_tenant": {"id": "tenant-a"}},
    )

    assert blocked["stage"] == "confirmation_required"
    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert result["output"] == {"recordId": "R-tenant-42", "accepted": True}
    assert calls == [{
        "method": "POST",
        "url": "https://8.8.8.8/api/tenant-records",
        "json": {"title": "租户记录", "tenantId": "tenant-a"},
        "step_uuid": step_uuid,
        "pinned_address": "8.8.8.8",
    }]


def test_storage_credentials_are_origin_scoped() -> None:
    state = {
        "cookies": [
            {"name": "sid", "value": "right", "domain": ".example.com"},
            {"name": "evil", "value": "wrong", "domain": ".evil.test"},
            {"name": "expired", "value": "wrong", "domain": ".example.com", "expires": 1},
        ],
        "origins": [
            {"origin": "https://oa.example.com", "localStorage": [{"name": "token", "value": "abc"}]},
            {"origin": "https://evil.test", "localStorage": [{"name": "token", "value": "wrong"}]},
            {"origin": "https://oa.example.com:444", "localStorage": [{"name": "token", "value": "wrong-port"}]},
        ],
    }

    headers = _storage_headers(state, "https://oa.example.com")

    assert headers == {"Cookie": "sid=right", "Authorization": "Bearer abc"}


def test_storage_cookies_retain_host_and_path_scope() -> None:
    state = {"cookies": [
        {"name": "api", "value": "right", "domain": "oa.example", "path": "/api"},
        {"name": "admin", "value": "wrong", "domain": "oa.example", "path": "/admin"},
        {"name": "host-only", "value": "wrong", "domain": "example", "path": "/"},
        {"name": "domain", "value": "right", "domain": ".example", "path": "/"},
    ]}

    assert _storage_headers(state, "https://oa.example/api/report") == {
        "Cookie": "api=right; domain=right"
    }
    assert _storage_headers(state, "https://oa.example/public") == {
        "Cookie": "domain=right"
    }


def test_v3_top_level_contract_cannot_be_upgraded_by_nested_metadata() -> None:
    body = {
        "recording_engine": "playwright_v3",
        "verification_status": "unverified",
        "publication_status": "published_unverified",
        "direct_call_enabled": False,
        "api_request": {
            "recording_engine": "playwright_v3",
            "revision": 3,
            "verification_status": "verified",
            "direct_call_enabled": True,
        },
    }

    metadata = _call_metadata_from_body(body)
    assert metadata["verification_status"] == "unverified"
    assert metadata["publication_status"] == "published_unverified"
    assert metadata["direct_call_enabled"] is False
    assert metadata["contract_integrity"] is False


async def test_explicit_invoke_cannot_upgrade_unverified_skill_with_nested_api(monkeypatch) -> None:
    observed: dict[str, Any] = {}

    async def v3(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs["api_request"])
        return {"ok": False, "stage": "unverified_contract"}

    monkeypatch.setattr("dano.recording_v3.execute_v3_capability", v3)
    skill = _skill().model_copy(update={
        "call_metadata": {
            "recording_engine": "playwright_v3",
            "verification_status": "unverified",
            "publication_status": "published_unverified",
            "direct_call_enabled": False,
            "contract_integrity": False,
        },
    })
    await invoke_skill_capability(
        skill=skill,
        capability="query_status",
        payload=CapabilityInvokePayload(input={}),
        api_request={
            "recording_engine": "playwright_v3",
            "verification_status": "verified",
            "direct_call_enabled": True,
        },
        base_url="https://oa.example",
    )
    assert observed["verification_status"] == "unverified"
    assert observed["direct_call_enabled"] is False


async def test_default_recording_path_rejects_unverified_top_level_before_v3_execution(
    monkeypatch,
) -> None:
    executed = False

    async def v3(**_kwargs: Any) -> dict[str, Any]:
        nonlocal executed
        executed = True
        return {"ok": True}

    monkeypatch.setattr("dano.recording_v3.execute_v3_capability", v3)
    env = SimpleNamespace(body={
        "recording_engine": "playwright_v3",
        "revision": 3,
        "verification_status": "unverified",
        "publication_status": "published_unverified",
        "direct_call_enabled": False,
        "api_request": {
            "recording_engine": "playwright_v3",
            "revision": 3,
            # Nested metadata must not upgrade the authoritative top level.
            "verification_status": "verified",
            "direct_call_enabled": True,
            "capabilities": [{"name": "query_status", "step_ids": ["query"]}],
        },
    })

    class Store:
        async def get(self, _asset_id: Any) -> Any:
            return env

    orchestrator = object.__new__(Orchestrator)
    orchestrator.store = Store()
    skill = _skill().model_copy(update={"recording_asset_id": uuid4()})
    outcome = await orchestrator._run_recording(  # noqa: SLF001
        uuid4(),
        skill,
        Intent(kind="action", action_hint="query_status", fields={}),
        confirm=lambda *_args: True,
        tenant="tenant-a",
    )

    assert outcome.state == TaskState.FAILED
    assert outcome.audit["api"]["stage"] == "unverified_contract"
    assert executed is False


async def test_default_v3_recording_path_is_strict_boolean_and_tenant_scoped(
    monkeypatch,
) -> None:
    observed: dict[str, Any] = {}

    async def v3(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        # A transport/provider string must not be promoted to successful execution.
        return {"ok": "true", "response": {"recordId": "R-1"}}

    async def tokens(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"Authorization": "Bearer trusted"}

    monkeypatch.setattr("dano.recording_v3.execute_v3_capability", v3)
    monkeypatch.setattr("dano.infra.token_store.get_token_headers", tokens)
    monkeypatch.setattr("dano.execution.page.sessions.session_path_if_exists", lambda *_args: None)
    asset_id = uuid4()
    env = SimpleNamespace(body={
        "recording_engine": "playwright_v3",
        "revision": 3,
        "verification_status": "verified",
        "publication_status": "published_verified",
        "direct_call_enabled": True,
        "api_request": {
            "recording_engine": "playwright_v3",
            "revision": 3,
            "verification_status": "verified",
            "direct_call_enabled": True,
            "capabilities": [{"name": "query_status", "kind": "query_status"}],
        },
    })

    class Store:
        async def get(self, requested: Any) -> Any:
            assert requested == asset_id
            return env

        async def get_published(self, *_args: Any, **_kwargs: Any) -> Any:
            return None

    orchestrator = object.__new__(Orchestrator)
    orchestrator.store = Store()
    skill = _skill().model_copy(update={"recording_asset_id": asset_id})
    outcome = await orchestrator._run_recording(  # noqa: SLF001
        uuid4(),
        skill,
        Intent(
            kind="action",
            action_hint="query_status",
            fields={"__dry_run": "true"},
        ),
        confirm=lambda *_args: "true",
        tenant="tenant-a",
    )

    assert outcome.state == TaskState.FAILED
    assert observed["confirm"] is False
    assert observed["dry_run"] is False
    assert observed["fields"] == {}
    assert observed["runtime_context"] == {"current_tenant": {"id": "tenant-a"}}


async def test_v3_field_options_never_enter_the_legacy_resolver(monkeypatch) -> None:
    observed: dict[str, Any] = {}

    async def v3_options(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {
            "field": kwargs["field"],
            "options": [{"label": "日报", "value": "daily"}],
            "count": 1,
        }

    async def legacy_options(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("V3 options must not enter the legacy resolver")

    async def tokens(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"Authorization": "Bearer trusted"}

    monkeypatch.setattr("dano.recording_v3.list_v3_field_options", v3_options)
    monkeypatch.setattr(
        "dano.execution.page.request_capture.fetch_field_options", legacy_options,
    )
    monkeypatch.setattr("dano.infra.token_store.get_token_headers", tokens)
    monkeypatch.setattr("dano.execution.page.sessions.session_path_if_exists", lambda *_args: None)
    asset_id = uuid4()
    env = SimpleNamespace(body={
        "recording_engine": "playwright_v3",
        "revision": 3,
        "verification_status": "verified",
        "publication_status": "published_verified",
        "direct_call_enabled": True,
        "api_request": {
            "recording_engine": "playwright_v3",
            "revision": 3,
            "verification_status": "verified",
            "direct_call_enabled": True,
            "capabilities": [{"capability_uuid": "cap-submit"}],
        },
    })
    skill = _skill().model_copy(update={"recording_asset_id": asset_id})

    class Store:
        async def get(self, requested: Any) -> Any:
            assert requested == asset_id
            return env

        async def get_published(self, *_args: Any, **_kwargs: Any) -> Any:
            return None

    class Registry:
        def by_action(self, _subsystem: Any, _action: str) -> Any:
            return skill

    orchestrator = object.__new__(Orchestrator)
    orchestrator.store = Store()
    orchestrator.registry = Registry()

    result = await orchestrator.list_field_options(
        Subsystem.OA,
        "query_status",
        "report_type",
        capability="cap-submit",
        tenant="tenant-a",
    )

    assert result["options"] == [{"label": "日报", "value": "daily"}]
    assert observed["api_request"]["verification_status"] == "verified"
    assert observed["credential_headers"] == {"Authorization": "Bearer trusted"}
    assert observed["runtime_context"] == {"current_tenant": {"id": "tenant-a"}}


def test_recording_evidence_secret_is_stable_and_vault_ref_is_opaque(tmp_path) -> None:
    key_file = tmp_path / "recording-evidence.key"
    first = _stable_evidence_secret("", key_file=key_file)
    second = _stable_evidence_secret("", key_file=key_file)
    assert first == second
    assert len(first) == 32
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        _stable_evidence_secret("too-short", key_file=key_file)

    writes: list[tuple[str, dict[str, Any]]] = []

    class Vault:
        def write_secret(self, path: str, values: dict[str, Any]) -> str:
            writes.append((path, values))
            return f"vault://{path}"

    adapter = object.__new__(_DanoCredentialVault)
    adapter._client = Vault()  # type: ignore[attr-defined]  # noqa: SLF001
    ref = adapter.store_secret(
        tenant_scope="tenant-a",
        recording_lineage="lineage-a",
        value_type="string",
        plaintext=b"super-secret-value",
        retention="session",
    )
    assert ref.startswith("vault://recording-v3/")
    assert "super-secret-value" not in ref
    assert base64.b64decode(writes[0][1]["value_b64"]) == b"super-secret-value"


class _Drafts:
    def __init__(self) -> None:
        self.body: dict[str, Any] | None = None
        self.review_roles: list[str] = []
        self.review_calls: list[dict[str, Any]] = []
        self.draft_id = uuid4()
        self.content_hash = "sha256:dano-draft"

    async def save_draft(self, **kwargs: Any) -> SimpleNamespace:
        self.body = kwargs["body"]
        return SimpleNamespace(
            asset_draft_id=self.draft_id,
            content_hash=self.content_hash,
            body=kwargs["body"],
        )

    async def record_validation(self, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(validation_run_id=uuid4())

    async def record_review(self, **kwargs: Any) -> SimpleNamespace:
        self.review_roles.append(kwargs["role"])
        self.review_calls.append(kwargs)
        return SimpleNamespace(review_run_id=uuid4())

    async def verify_publishable(self, *_args: Any) -> tuple[bool, str]:
        return True, "ok"

    async def verify_reviewed(self, *_args: Any) -> tuple[bool, str]:
        return True, "ok"

    async def get_draft(self, _draft_id: Any) -> SimpleNamespace:
        return SimpleNamespace(content_hash=self.content_hash, body=self.body)


class _Assets:
    def __init__(self) -> None:
        self.created: AssetEnvelope | None = None

    async def create(self, envelope: AssetEnvelope) -> AssetEnvelope:
        self.created = envelope.model_copy(update={"asset_id": uuid4(), "version": 4})
        return self.created

    async def set_status(self, asset_id: Any, status: ValidationStatus) -> AssetEnvelope | None:
        assert self.created and asset_id == self.created.asset_id
        return self.created.model_copy(update={"validation_status": status})


class _RecoveringAssets(_Assets):
    def __init__(self, existing: AssetEnvelope) -> None:
        super().__init__()
        self.existing = existing

    async def list_versions(self, *_args: Any) -> list[AssetEnvelope]:
        return [self.existing]

    async def create(self, _envelope: AssetEnvelope) -> AssetEnvelope:
        raise AssertionError("exact frozen publication must be recovered, not duplicated")


async def test_dano_publisher_records_three_isolated_reviews_and_publishes_frozen_body() -> None:
    from dano_recording.integrations.dano.assets import DanoAssetPublisher

    publisher = object.__new__(DanoAssetPublisher)
    publisher.drafts = _Drafts()
    publisher.assets = _Assets()
    body = {
        "recording_engine": "playwright_v3",
        "revision": 3,
        "actions": [],
        "action": "query_status",
        "title": "查询状态",
        "start_url": "https://oa.example",
        "risk_level": "L1",
        "verification_status": "verified",
        "publication_status": "published_verified",
        "direct_call_enabled": True,
        "api_request": {
            "recording_engine": "playwright_v3",
            "revision": 3,
            "verification_status": "verified",
            "direct_call_enabled": True,
            "recorded_origin": "https://oa.example",
            "steps": [{"step_id": "query", "method": "GET", "url": "https://oa.example/query"}],
            "capabilities": [{"name": "query_status", "step_ids": ["query"]}],
        },
        "capabilities": [{"name": "query_status", "step_ids": ["query"]}],
    }
    reviews = [
        {"role": role, "revision": 3, "passed": True, "reasons": [], "pi_session_id": f"session-{role}", "model_id": f"pi:{role}", "content_hash": "sha256:release", "snapshot_hash": "sha256:snapshot"}
        for role in ("acceptance", "security", "compliance")
    ]

    result = await publisher.publish(
        run_id="run-v3", tenant="tenant-a", subsystem="A-OA", action="query_status",
        body=body, validation={"passed": True, "revision": 3, "content_hash": "sha256:release", "snapshot_hash": "sha256:snapshot", "executability_status": "verified", "direct_call_enabled": True}, reviews=reviews,
    )

    assert result == {
        "published": True,
        "asset_id": str(publisher.assets.created.asset_id),
        "version": 4,
        "content_hash": "sha256:dano-draft",
        "snapshot_hash": "sha256:snapshot",
        "verification_status": "verified",
        "publication_status": "published_verified",
        "contract_fault_count": 0,
    }
    assert set(publisher.drafts.review_roles) == {"acceptance", "security", "compliance"}
    assert {item["metadata"]["pi_session_id"] for item in publisher.drafts.review_calls} == {
        "session-acceptance", "session-security", "session-compliance",
    }
    assert all(item["metadata"]["release_content_hash"] == "sha256:release" for item in publisher.drafts.review_calls)
    assert publisher.assets.created.body == publisher.drafts.body == body


@pytest.mark.parametrize("invalid_passed", ["true", "false", 1, 0, None])
async def test_dano_publisher_requires_literal_true_deterministic_validation(
    invalid_passed,
) -> None:
    from dano_recording.integrations.dano.assets import DanoAssetPublisher

    publisher = object.__new__(DanoAssetPublisher)
    publisher.drafts = _Drafts()
    publisher.assets = _Assets()

    with pytest.raises(ValueError, match="failed deterministic validation"):
        await publisher.publish(
            run_id="run-v3",
            tenant="tenant-a",
            subsystem="A-OA",
            action="query_status",
            body={
                "recording_engine": "playwright_v3",
                "api_request": {"recording_engine": "playwright_v3"},
            },
            validation={"passed": invalid_passed},
            reviews=[],
        )

    assert publisher.drafts.body is None
    assert publisher.assets.created is None


async def test_dano_publisher_recovers_exact_frozen_asset_after_process_crash() -> None:
    from dano_recording.integrations.dano.assets import DanoAssetPublisher

    body = {
        "recording_engine": "playwright_v3",
        "revision": 3,
        "actions": [],
        "action": "query_status",
        "title": "查询状态",
        "start_url": "https://oa.example",
        "risk_level": "L1",
        "verification_status": "verified",
        "publication_status": "published_verified",
        "direct_call_enabled": True,
        "api_request": {
            "recording_engine": "playwright_v3",
            "revision": 3,
            "verification_status": "verified",
            "direct_call_enabled": True,
            "steps": [{"step_id": "query", "method": "GET", "url": "https://oa.example/query"}],
            "capabilities": [{"name": "query_status", "step_ids": ["query"]}],
        },
        "capabilities": [{"name": "query_status", "step_ids": ["query"]}],
    }
    existing = AssetEnvelope(
        asset_id=uuid4(),
        asset_type=AssetType.PAGE_SCRIPT,
        scope=Scope(tenant="tenant-a", subsystem=Subsystem("A-OA")),
        asset_key="query_status",
        version=7,
        source_fingerprint="sha256:dano-draft",
        validation_status=ValidationStatus.PUBLISHED,
        confidence=0.95,
        human_confirmed=True,
        body=body,
    )
    publisher = object.__new__(DanoAssetPublisher)
    publisher.drafts = _Drafts()
    publisher.assets = _RecoveringAssets(existing)
    reviews = [
        {
            "role": role,
            "revision": 3,
            "passed": True,
            "reasons": [],
            "pi_session_id": f"session-{role}",
            "model_id": f"pi:{role}",
            "content_hash": "sha256:release",
            "snapshot_hash": "sha256:snapshot",
        }
        for role in ("acceptance", "security", "compliance")
    ]

    result = await publisher.publish(
        run_id="run-restart",
        tenant="tenant-a",
        subsystem="A-OA",
        action="query_status",
        body=body,
        validation={
            "passed": True,
            "revision": 3,
            "content_hash": "sha256:release",
            "snapshot_hash": "sha256:snapshot",
            "executability_status": "verified",
            "direct_call_enabled": True,
        },
        reviews=reviews,
    )

    assert result["published"] is True
    assert result["recovered"] is True
    assert result["asset_id"] == str(existing.asset_id)
    assert result["version"] == 7
    assert publisher.assets.created is None


async def test_dano_publisher_rejects_reused_reviewer_session_before_writing() -> None:
    from dano_recording.integrations.dano.assets import DanoAssetPublisher

    publisher = object.__new__(DanoAssetPublisher)
    publisher.drafts = _Drafts()
    publisher.assets = _Assets()
    body = {
        "recording_engine": "playwright_v3",
        "revision": 3,
        "verification_status": "verified",
        "publication_status": "published_verified",
        "direct_call_enabled": True,
        "api_request": {
            "recording_engine": "playwright_v3",
            "revision": 3,
            "verification_status": "verified",
            "direct_call_enabled": True,
        },
    }
    reviews = [
        {"role": role, "revision": 3, "passed": True, "pi_session_id": "same", "content_hash": "sha256:release", "snapshot_hash": "sha256:snapshot"}
        for role in ("acceptance", "security", "compliance")
    ]

    with pytest.raises(ValueError, match="three isolated Pi sessions"):
        await publisher.publish(
            run_id="run", tenant="tenant", subsystem="A-OA", action="x",
            body=body, validation={"passed": True, "revision": 3, "content_hash": "sha256:release", "snapshot_hash": "sha256:snapshot", "executability_status": "verified", "direct_call_enabled": True}, reviews=reviews,
        )
    assert publisher.drafts.body is None


async def test_dano_publisher_rejects_revision_mismatch_before_writing() -> None:
    from dano_recording.integrations.dano.assets import DanoAssetPublisher

    publisher = object.__new__(DanoAssetPublisher)
    publisher.drafts = _Drafts()
    publisher.assets = _Assets()
    body = {
        "recording_engine": "playwright_v3",
        "revision": 3,
        "verification_status": "verified",
        "publication_status": "published_verified",
        "direct_call_enabled": True,
        "api_request": {
            "recording_engine": "playwright_v3",
            "revision": 2,
            "verification_status": "verified",
            "direct_call_enabled": True,
        },
    }
    reviews = [
        {
            "role": role,
            "revision": 3,
            "passed": True,
            "pi_session_id": f"session-{role}",
            "content_hash": "sha256:release",
            "snapshot_hash": "sha256:snapshot",
        }
        for role in ("acceptance", "security", "compliance")
    ]

    with pytest.raises(ValueError, match="frozen revision"):
        await publisher.publish(
            run_id="run",
            tenant="tenant",
            subsystem="A-OA",
            action="x",
            body=body,
            validation={
                "passed": True,
                "revision": 3,
                "content_hash": "sha256:release",
                "snapshot_hash": "sha256:snapshot",
                "executability_status": "verified",
                "direct_call_enabled": True,
            },
            reviews=reviews,
        )
    assert publisher.drafts.body is None
