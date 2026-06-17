"""流程11 触发:漂移检测。

源指纹变化(接口文档)/ 页面结构指纹变化,即判定资产漂移,触发自愈。
只处理「资产变了」这一类;普通登录/权限/参数错误不进入。
"""

from __future__ import annotations

import structlog

from dano.capabilities.fingerprint import fingerprint_materials

log = structlog.get_logger(__name__)


class DriftDetector:
    @staticmethod
    def changed(baseline_fp: str, current_fp: str) -> bool:
        drifted = baseline_fp != current_fp
        if drifted:
            log.warning("drift.detected", baseline=baseline_fp, current=current_fp)
        return drifted

    @staticmethod
    def changed_materials(baseline_fp: str, materials: list) -> bool:
        """重新对材料打指纹,与基线比对。"""
        current = fingerprint_materials([m.model_dump() if hasattr(m, "model_dump") else m
                                         for m in materials])
        return DriftDetector.changed(baseline_fp, current)
