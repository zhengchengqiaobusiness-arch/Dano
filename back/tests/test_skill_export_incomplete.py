import json
from pathlib import Path
from uuid import uuid4

import pytest

from dano.execution.page.flow_materialization.builder import apply_recorded_unknown_policy
from dano.execution.page.flow_spec_core.models import FlowCapability, FlowSpec, FlowStep, ParamField
from dano.export.skill_package.renderer import package_slug
from dano.onboarding.skill_generation.export import (
    SkillExportError,
    export_recording_skill,
)
from dano.onboarding.skill_generation.export_view import list_unconfirmed_write_fields
from dano.onboarding.skill_generation.models import SkillGenerationRequest
from dano.onboarding.skill_generation.planner import propose_deterministic_plan
from tests.skill4_draft import MIN_FLOW_PY, write_skill4_draft


def _spec_with_unknown_write() -> FlowSpec:
    return FlowSpec(
        title="残缺示例",
        capabilities=[
            FlowCapability(
                capability_id="cap_save",
                name="保存",
                title="保存",
                kind="update",
                step_ids=["step_save"],
                request_refs=[{"step_id": "step_save", "usage": "execute"}],
            )
        ],
        steps=[
            FlowStep(
                step_id="step_save",
                name="保存",
                method="POST",
                path="/api/save",
                params=[
                    ParamField(
                        key="title",
                        path="body.title",
                        label="标题",
                        source_kind="unknown",
                        exposed_to_user=False,
                    )
                ],
            )
        ],
    )


def _query_spec() -> FlowSpec:
    return FlowSpec(
        title="查询日报",
        capabilities=[
            FlowCapability(
                capability_id="cap_search",
                name="搜索",
                title="搜索",
                kind="query",
                step_ids=["step_search"],
                request_refs=[{"step_id": "step_search", "usage": "execute"}],
            )
        ],
        steps=[
            FlowStep(
                step_id="step_search",
                name="搜索",
                method="GET",
                path="/api/page",
                url="https://example.test/api/page",
            )
        ],
    )


def test_unconfirmed_write_fields_are_listed_not_guessed() -> None:
    spec = _spec_with_unknown_write()
    assert list_unconfirmed_write_fields(spec) == ["保存:标题"]


@pytest.mark.asyncio
async def test_export_does_not_block_on_pi_unresolved(tmp_path: Path) -> None:
    spec = _query_spec()
    persisted: dict = {}
    action = "action_export_unresolved"

    async def proposer(current_spec, current_request, verified, source_fingerprint):  # noqa: ANN001
        return propose_deterministic_plan(
            current_spec, current_request, verified, source_fingerprint,
        )

    async def publish(**_kwargs):  # noqa: ANN003
        return {"ok": True, "asset_version": 1, "asset_id": "asset-1", "action": action}

    async def persist(next_body: dict) -> None:
        persisted.update(next_body)

    artifacts = write_skill4_draft(tmp_path / "artifacts")
    outcome = await export_recording_skill(
        result_id=uuid4(),
        body={
            "flow_spec": spec.model_dump(mode="json"),
            "action": action,
            "subsystem": "oa",
            "title": "查询日报列表、新增日报并提交",
            "skill_artifacts_dir": str(artifacts),
            "unresolved": [
                "列表页在用户未显式点击「查询」按钮的情况下自动加载了默认数据。",
                "POST /admin-api/oa/work-report/submit 请求体中未找到与提交意见对应的字段 key。",
            ],
        },
        tenant="test",
        request=SkillGenerationRequest(
            title="查询日报列表、新增日报并提交",
            business_description="搜索日报。",
            out_dir=str(tmp_path),
        ),
        proposer=proposer,
        publish=publish,
        persist=persist,
    )

    assert outcome.status == "exported", outcome.errors
    assert outcome.errors == []
    assert (tmp_path / package_slug(outcome.skill_id)).is_dir()
    assert persisted.get("skill_export_status") == "exported"


@pytest.mark.asyncio
async def test_export_refuses_without_skill4_artifacts(tmp_path: Path) -> None:
    spec = _query_spec()

    async def proposer(current_spec, current_request, verified, source_fingerprint):  # noqa: ANN001
        return propose_deterministic_plan(
            current_spec, current_request, verified, source_fingerprint,
        )

    async def publish(**_kwargs):  # noqa: ANN003
        return {"ok": True, "asset_version": 1, "asset_id": "asset-1"}

    with pytest.raises(SkillExportError) as caught:
        await export_recording_skill(
            result_id=uuid4(),
            body={
                "flow_spec": spec.model_dump(mode="json"),
                "action": "action_export_no_skill4",
                "subsystem": "oa",
                "title": "查询日报",
            },
            tenant="test",
            request=SkillGenerationRequest(
                title="查询日报",
                business_description="搜索日报。",
                out_dir=str(tmp_path),
            ),
            proposer=proposer,
            publish=publish,
        )
    assert caught.value.status_code == 409
    assert "没有 Skill 4 产物" in caught.value.detail


def test_export_keeps_unknown_write_unresolved() -> None:
    spec = _spec_with_unknown_write()
    spec.steps[0].params[0].value = "录制原值"
    apply_recorded_unknown_policy(spec)
    param = spec.steps[0].params[0]
    assert param.source_kind == "unknown"
    assert (param.source or {}).get("kind") == "unresolved"


@pytest.mark.asyncio
async def test_export_blocks_unresolved_write_capability(tmp_path: Path) -> None:
    spec = _spec_with_unknown_write()

    async def proposer(current_spec, current_request, verified, source_fingerprint):  # noqa: ANN001
        return propose_deterministic_plan(
            current_spec, current_request, verified, source_fingerprint,
        )

    async def publish(**_kwargs):  # noqa: ANN003
        return {"ok": True, "asset_version": 1, "asset_id": "asset-1", "action": "action_export_write"}

    with pytest.raises(SkillExportError) as caught:
        await export_recording_skill(
            result_id=uuid4(),
            body={
                "flow_spec": spec.model_dump(mode="json"),
                "action": "action_export_write",
                "subsystem": "oa",
                "title": "保存",
                "unresolved": [{"capability_id": "cap_save", "kind": "write"}],
            },
            tenant="test",
            request=SkillGenerationRequest(
                title="保存",
                business_description="保存一条记录。",
                out_dir=str(tmp_path),
            ),
            proposer=proposer,
            publish=publish,
        )
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_export_packs_skill4_artifacts(tmp_path: Path, monkeypatch) -> None:
    spec = _query_spec()
    skill_md = "---\nname: demo\ndescription: 查询日报\n---\n\n# demo\nkept-handbook\n"
    search_py = "print('ok')\n"
    artifacts = write_skill4_draft(
        tmp_path / "artifacts",
        **{"SKILL.md": skill_md, "scripts/search.py": search_py, "scripts/flow.py": MIN_FLOW_PY},
    )

    async def fake_headers(tenant: str, subsystem: str, **_kwargs):  # noqa: ANN003
        assert tenant == "test"
        assert subsystem == "oa"
        return {"Authorization": "Bearer packed-local-token"}

    monkeypatch.setattr("dano.infra.token_store.get_token_headers", fake_headers)

    async def proposer(current_spec, current_request, verified, source_fingerprint):  # noqa: ANN001
        return propose_deterministic_plan(
            current_spec, current_request, verified, source_fingerprint,
        )

    async def publish(**_kwargs):  # noqa: ANN003
        return {"ok": True, "asset_version": 1, "asset_id": "asset-1", "action": "action_export_pack"}

    outcome = await export_recording_skill(
        result_id=uuid4(),
        body={
            "flow_spec": spec.model_dump(mode="json"),
            "action": "action_export_pack",
            "subsystem": "oa",
            "title": "查询日报",
            "skill_artifacts_dir": str(artifacts),
        },
        tenant="test",
        request=SkillGenerationRequest(
            title="查询日报",
            business_description="搜索日报。",
            out_dir=str(tmp_path / "out"),
        ),
        proposer=proposer,
        publish=publish,
    )
    assert outcome.status == "exported", outcome.errors
    packed = tmp_path / "out" / package_slug(outcome.skill_id)
    assert (packed / "SKILL.md").read_text(encoding="utf-8") == skill_md
    assert (packed / "scripts" / "search.py").read_text(encoding="utf-8") == search_py
    assert (packed / "scripts" / "flow.py").read_text(encoding="utf-8") == MIN_FLOW_PY
    assert (packed / "scripts" / "client.py").is_file()
    assert (packed / "scripts" / "wire_format.py").is_file()
    runtime = json.loads((packed / "config" / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["base_url"] == "https://example.test"
    assert runtime["tenant"] == "test"
    assert runtime["subsystem"] == "oa"
    auth = json.loads((packed / "config" / "auth.local.json").read_text(encoding="utf-8"))
    assert auth["headers"]["Authorization"] == "Bearer packed-local-token"
    assert not (packed / "references" / "generator-guides").exists()
