"""资产体结构校验:把 pi 产出的 body(dict)按资产类型校验成强类型 Pydantic 模型。

落库前必经此关:pi 给的是草稿,结构不对就拒绝(SchemaError),绝不让自由文本进库。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from dano.shared.asset_bodies import (
    AdapterBody,
    ConnectorBody,
    EnvProfileBody,
    FieldMappingBody,
    PageScriptBody,
    PlanBody,
    PolicyRuleBody,
    WorkflowSkillBody,
)
from dano.shared.enums import AssetType

_BODY_MODEL = {
    AssetType.CONNECTOR: ConnectorBody,
    AssetType.FIELD_MAPPING: FieldMappingBody,
    AssetType.POLICY_RULE: PolicyRuleBody,
    AssetType.ENV_PROFILE: EnvProfileBody,
    AssetType.PAGE_SCRIPT: PageScriptBody,
    AssetType.WORKFLOW: WorkflowSkillBody,
    AssetType.ADAPTER: AdapterBody,
}

# 方案(PlanBody)是 goal 模式中间产物,不作为独立资产类型入库,但提供校验入口供控制器使用。
PLAN_BODY_MODEL = PlanBody


class SchemaError(ValueError):
    """资产体结构不合法(pi 产物校验失败)。"""


def validate_asset_body(asset_type: AssetType, body: dict[str, Any]):
    """按资产类型校验 body,返回校验后的 Pydantic 模型;不合法抛 SchemaError。"""
    model = _BODY_MODEL.get(asset_type)
    if model is None:
        raise SchemaError(f"未知资产类型: {asset_type}")
    if not isinstance(body, dict):
        raise SchemaError(f"{asset_type.value} 的 body 应为对象,实得 {type(body).__name__}")
    try:
        return model.model_validate(body)
    except ValidationError as e:
        raise SchemaError(f"{asset_type.value} body 校验失败: {e}") from e
