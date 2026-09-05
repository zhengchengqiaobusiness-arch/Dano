from uuid import uuid4

import pytest

from dano.execution.page.flow_spec_core.models import FlowCapability, FlowSpec, FlowStep, ParamField
from dano.onboarding.skill_generation.export import (
    SkillExportError,
    _incomplete_export_reasons,
    export_recording_skill,
)
from dano.onboarding.skill_generation.export_view import list_unconfirmed_write_fields
from dano.onboarding.skill_generation.models import SkillGenerationRequest


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


def test_unconfirmed_write_fields_are_listed_not_guessed() -> None:
    spec = _spec_with_unknown_write()
    assert list_unconfirmed_write_fields(spec) == ["保存:标题"]


@pytest.mark.asyncio
async def test_export_blocks_pi_unresolved() -> None:
    spec = FlowSpec(
        title="未闭合",
        capabilities=[
            FlowCapability(
                capability_id="cap_search",
                name="搜索",
                title="搜索",
                kind="query",
                request_refs=[{"step_id": "step_search", "usage": "execute"}],
            )
        ],
        steps=[FlowStep(step_id="step_search", name="搜索", method="GET", path="/api/page")],
    )
    with pytest.raises(SkillExportError, match="阻止导出残缺 Skill"):
        await export_recording_skill(
            result_id=uuid4(),
            body={"flow_spec": spec.model_dump(mode="json"), "unresolved": ["删除已点过但缺少能力"]},
            tenant="test",
            request=SkillGenerationRequest(
                title="未闭合",
                business_description="搜索并删除",
                out_dir="out",
            ),
        )


def test_export_ignores_field_source_unresolved() -> None:
    spec = _spec_with_unknown_write()
    spec.steps[0].params[0].value = "录制原值"
    reasons = _incomplete_export_reasons(
        {
            "flow_spec": spec.model_dump(mode="json"),
            "unresolved": ["工作项进度默认值来源未确认", "筛选条上下拉的完整枚举未展开"],
        },
        spec,
    )
    assert reasons == []


def test_export_keeps_unknown_write_as_recorded_literal() -> None:
    spec = _spec_with_unknown_write()
    spec.steps[0].params[0].value = "录制原值"
    reasons = _incomplete_export_reasons({"flow_spec": spec.model_dump(mode="json")}, spec)
    assert reasons == []
    param = spec.steps[0].params[0]
    assert param.source_kind == "constant"
    assert param.exposed_to_user is False
    assert param.default_value == "录制原值"
    assert (param.source or {}).get("kind") == "recorded_literal"
