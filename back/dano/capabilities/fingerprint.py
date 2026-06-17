"""指纹器:对源材料/页面结构生成稳定指纹。

用途:① 资产绑定源指纹入库;② 保障期(流程11)判断文档/页面是否改版的基线。
确定性:同样内容 → 同样指纹,与字段顺序无关(对 dict 做规范化)。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint(content: Any) -> str:
    """生成 sha256 指纹。dict/list 规范化排序后哈希,保证稳定。"""
    if isinstance(content, (dict, list)):
        normalized = json.dumps(content, ensure_ascii=False, sort_keys=True)
    else:
        normalized = str(content)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:32]}"


def fingerprint_materials(materials: list[Any]) -> str:
    """对一组材料整体打指纹。"""
    parts = sorted(fingerprint(m) for m in materials)
    return fingerprint(parts)
