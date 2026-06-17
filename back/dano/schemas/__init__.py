"""pi 产物落库前的结构校验:绝不把自由文本/半成品直接入库(REWRITE_PLAN Phase 1)。"""

from dano.schemas.validate import SchemaError, validate_asset_body

__all__ = ["SchemaError", "validate_asset_body"]
