"""每 run 的接入材料登记(进程内)。

为什么:pi 经 LLM 调工具,不该把整份 OpenAPI/凭证当参数传(又大又泄密)。
做法:onboarding 启动 pi 前,把材料按 (run_id, system_instance_id) 登记于此;
工具按 run_id 取材料。run 结束时清理。凭证只在此进程内存,不进 LLM 上下文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MaterialContext:
    run_id: str
    tenant: str
    system_instance_id: str          # 如 a-oa
    subsystem: str                   # 如 A-OA
    openapi: dict[str, Any] | None = None
    deploy: dict[str, Any] | None = None
    credentials: dict[str, str] = field(default_factory=dict)   # 测试账号(不进 LLM)
    policy_text: str = ""                                       # 制度文件原文(流程4 抽规则用)
    include_tags: list[str] = field(default_factory=list)       # 类别白名单(空=全部业务动作)
    business_rules: list[dict] = field(default_factory=list)    # 人工提供的业务规则(阈值/审批链)→ 供 pi grounding 分支/前置
    holidays: list[str] = field(default_factory=list)           # 日历源:法定节假日(供 compute business_days)
    selected_flows: list[dict] = field(default_factory=list)    # 人工选的业务(templateId + 测试值)→ pi 聚焦发现 + sandbox cases


_REGISTRY: dict[tuple[str, str], MaterialContext] = {}


def register(ctx: MaterialContext) -> None:
    _REGISTRY[(ctx.run_id, ctx.system_instance_id)] = ctx


def get(run_id: str, system_instance_id: str) -> MaterialContext | None:
    return _REGISTRY.get((run_id, system_instance_id))


def clear_run(run_id: str) -> None:
    for k in [k for k in _REGISTRY if k[0] == run_id]:
        _REGISTRY.pop(k, None)
