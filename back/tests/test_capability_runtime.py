from __future__ import annotations

from uuid import uuid4

from dano.orchestrator.orchestrator import Orchestrator
from dano.orchestrator.capability_runtime import (
    CapabilityInvokePayload,
    capability_missing_fields,
    capability_requires_confirmation,
    find_capability,
    payload_fields,
)
from dano.orchestrator.skills import SkillRegistry
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem, TaskState


def _skill() -> SkillSpec:
    return SkillSpec(
        skill_id="A-OA.submit_form",
        subsystem=Subsystem.OA,
        action="submit_form",
        risk_level=RiskLevel.L3,
        has_api=False,
        capabilities=[
            {
                "name": "query_status",
                "kind": "query_status",
                "input_schema": {
                    "type": "object",
                    "properties": {"month": {"type": "string"}},
                    "required": ["month"],
                },
            },
            {
                "name": "submit_batch",
                "kind": "submit_batch",
                "input_schema": {
                    "type": "object",
                    "properties": {"entries": {"type": "array"}},
                    "required": ["entries"],
                },
            },
        ],
    )


def test_payload_fields_merges_arguments_then_input_and_marks_capability():
    payload = CapabilityInvokePayload(arguments={"month": "2026-05", "x": 1}, input={"x": 2})

    assert payload_fields(payload, "query_status") == {
        "month": "2026-05",
        "x": 2,
        "__capability": "query_status",
    }


def test_find_capability_and_required_fields():
    cap = find_capability(_skill(), "query_status")

    assert cap is not None
    assert capability_missing_fields(cap, {"month": "2026-05"}) == []
    assert capability_missing_fields(cap, {}) == ["month"]


def test_read_capability_does_not_require_confirmation_but_write_does():
    skill = _skill()
    query = find_capability(skill, "query_status")
    submit = find_capability(skill, "submit_batch")

    assert capability_requires_confirmation(skill, query) is False
    assert capability_requires_confirmation(skill, submit) is True


class _Env:
    def __init__(self, body: dict) -> None:
        self.body = body


class _Store:
    def __init__(self, asset_id, body: dict) -> None:  # noqa: ANN001
        self.asset_id = asset_id
        self.env = _Env(body)

    async def get(self, asset_id):  # noqa: ANN001
        return self.env if asset_id == self.asset_id else None

    async def get_published(self, *args, **kwargs):  # noqa: ANN001
        return None


async def test_orchestrator_capability_invoke_bypasses_whole_skill_required_fields():
    asset_id = uuid4()
    skill = _skill()
    skill.recording_asset_id = asset_id
    skill.required_fields = ["entries", "reason"]
    skill.api_request = {}
    store = _Store(asset_id, {
        "api_request": {
            "steps": [{"step_id": "query", "method": "GET", "url": "http://x/api/status", "path": "/api/status"}],
            "capabilities": [{"name": "query_status", "kind": "query_status", "step_ids": ["query"]}],
        },
    })
    orch = Orchestrator(
        registry=SkillRegistry([skill]),
        store=store,
        harness=object(),
        action_executor=object(),
    )

    out = await orch.invoke_skill(
        Subsystem.OA,
        "submit_form",
        {"__capability": "query_status", "__dry_run": True, "month": "2026-05"},
        tenant="t",
    )

    assert out.state == TaskState.COMPLETED
    assert out.audit["api"]["dry_run"] is True
    assert out.audit["api"]["api_shape"]["step_count"] == 1
