from __future__ import annotations

from uuid import uuid4

from dano.orchestrator.orchestrator import Orchestrator
from dano.orchestrator.capability_runtime import (
    CapabilityInvokePayload,
    capability_input_issues,
    capability_missing_fields,
    capability_requires_confirmation,
    find_capability,
    invoke_skill_capability,
    normalize_capability_result,
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


def test_batch_capability_validates_required_fields_inside_entries():
    cap = {
        "name": "submit_batch",
        "input_schema": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {"date": {"type": "string"}, "content": {"type": "string"}},
                        "required": ["date", "content"],
                    },
                },
            },
            "required": ["entries"],
        },
    }

    assert capability_input_issues(cap, {"entries": []})
    assert capability_input_issues(cap, {"entries": [{"date": "2026-05-12"}]}) == [
        "Field `entries[0]` missing required fields: ['content']"
    ]
    assert capability_input_issues(cap, {"entries": [{"date": "2026-05-12", "content": "x"}]}) == []


def test_capability_input_rejects_extra_root_and_batch_item_fields():
    cap = find_capability(_skill(), "submit_batch")
    cap["input_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"entries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"date": {"type": "string"}},
                "required": ["date"],
            },
        }},
        "required": ["entries"],
    }
    cap.pop("parameters", None)

    issues = capability_input_issues(cap, {
        "entries": [{"date": "2026-05-12", "unexpected": True}],
        "rogue": "value",
    })

    assert any("rogue" in issue for issue in issues)
    assert any("entries[0]" in issue and "unexpected" in issue for issue in issues)


def test_pseudo_batch_alias_does_not_select_submit_capability():
    skill = _skill()
    skill.capabilities = [{"name": "submit_batch2", "kind": "submit", "title": "批量提交"}]

    assert find_capability(skill, "submit_batch2") is None
    cap = find_capability(skill, "submit")
    assert cap["name"] == cap["kind"] == "submit"
    assert cap["title"] == "提交"


def test_read_capability_does_not_require_confirmation_but_write_does():
    skill = _skill()
    query = find_capability(skill, "query_status")
    submit = find_capability(skill, "submit_batch")

    assert capability_requires_confirmation(skill, query) is False
    assert capability_requires_confirmation(skill, submit) is True


async def test_capability_invoke_rejects_invalid_batch_shape_before_execution():
    out = await invoke_skill_capability(
        skill=_skill(),
        capability="submit_batch",
        payload=CapabilityInvokePayload(input={"entries": "not-an-array"}, confirm=True),
        api_request={"steps": [{"step_id": "submit", "method": "POST", "url": "http://x/submit"}]},
    )

    assert out["ok"] is False
    assert out["stage"] == "invalid_input"
    assert "array" in out["detail"]


def test_normalized_capability_result_preserves_fact_check_state():
    out = normalize_capability_result(
        {"ok": True, "response": {"code": 0}, "fact_check_passed": True},
        "submit",
        skill_id="A-OA.submit",
    )

    assert out["fact_check_passed"] is True


def test_normalized_capability_result_rejects_output_schema_mismatch():
    out = normalize_capability_result(
        {"ok": True, "structured_output": {"count": "one"}},
        "query_status",
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    )

    assert out["ok"] is False
    assert out["stage"] == "invalid_output"
    assert "output.count" in out["detail"]


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


async def test_orchestrator_requires_explicit_capability_for_multi_capability_skill():
    skill = _skill()
    orch = Orchestrator(
        registry=SkillRegistry([skill]),
        store=_Store(uuid4(), {}),
        harness=object(),
        action_executor=object(),
    )

    out = await orch.invoke_skill(
        Subsystem.OA,
        "submit_form",
        {"month": "2026-05"},
        tenant="t",
    )

    assert out.state == TaskState.NEEDS_SELECT
    assert out.audit["capability_required"] is True
    assert set(out.audit["candidates"]) == {"query_status", "submit_batch"}
