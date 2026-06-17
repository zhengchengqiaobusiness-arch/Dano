"""能力库共用类型。"""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class VerifyResult(BaseModel):
    """自验证(硬关卡)结果。passed=False 即不可出库。"""
    passed: bool
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
