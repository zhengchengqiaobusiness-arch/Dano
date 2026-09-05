from pathlib import Path
from uuid import uuid4

import pytest

from dano.execution.page.flow_materialization.builder import apply_recorded_unknown_policy
from dano.execution.page.flow_spec_core.models import FlowCapability, FlowSpec, FlowStep, ParamField
from dano.export.skill_package.renderer import package_slug
from dano.onboarding.skill_generation.export import (
    export_recording_skill,
)
from dano.onboarding.skill_generation.export_view import list_unconfirmed_write_fields
from dano.onboarding.skill_generation.models import SkillGenerationRequest
from dano.onboarding.skill_generation.planner import propose_deterministic_plan


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

    outcome = await export_recording_skill(
        result_id=uuid4(),
        body={
            "flow_spec": spec.model_dump(mode="json"),
            "action": action,
            "subsystem": "oa",
            "title": "查询日报列表、新增日报并提交",
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


def test_export_keeps_unknown_write_as_recorded_literal() -> None:
    spec = _spec_with_unknown_write()
    spec.steps[0].params[0].value = "录制原值"
    apply_recorded_unknown_policy(spec)
    param = spec.steps[0].params[0]
    assert param.source_kind == "constant"
    assert param.exposed_to_user is False
    assert param.default_value == "录制原值"
    assert (param.source or {}).get("kind") == "recorded_literal"
