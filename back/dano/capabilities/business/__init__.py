"""业务流程库(区分开):每个业务一个模块,各自定义字段/模板/契约;请假与出差互不影响。

新增业务 = 加一个 `<name>.py`(导出 `recipe()` 与 `SAMPLE`)并在 `_MODULES` 注册即可。
共享的是执行机制(base 里的 RuoYi 3 步契约 + 成败规则),区分的是业务语义。
"""

from __future__ import annotations

from dano.capabilities.business import leave, travel
from dano.shared.asset_bodies import WorkflowSkillBody

_MODULES = [leave, travel]            # 注册:新增业务在此追加


def recipes() -> list[WorkflowSkillBody]:
    """所有业务的复合配方(供 OA 模板 workflows() / 发现菜单 / profiler 路由)。"""
    return [m.recipe() for m in _MODULES]


def sample_for(action: str) -> dict:
    """某业务的默认样例测试输入({templateId, values});未知业务返回空。"""
    for m in _MODULES:
        if m.recipe().action == action:
            return dict(m.SAMPLE)
    return {}
