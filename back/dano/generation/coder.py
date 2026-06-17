"""生成器(创造步骤:拆解/编码/修复)。

Coder 是可注入接口:真实路径 = pi 的 goal 会话(M5 接入改写时与 onboarding 的 pi spawn 对接);
测试路径 = 注入 Fake(确定性 buggy→fixed),用来验证「循环 + 闸门 + 驳回重写」本身。

codegen_prompt 是给 goal 会话的统一目标提示;驳回时把 reasons 回灌进同一目标续跑(非一次成型)。
"""

from __future__ import annotations

from typing import Protocol

from dano.shared.asset_bodies import PlanBody


class Coder(Protocol):
    async def generate(self, *, plan: PlanBody, feedback: list[str]) -> dict:
        """产出一份 AdapterBody(dict);feedback 为上一轮闸门的驳回原因,须据此修复。"""
        ...


def codegen_prompt(plan: PlanBody, feedback: list[str], code_skeleton: str) -> str:
    """goal 模式编码/修复提示。feedback 非空 = 上一轮被驳回,必须按因修复后重产。"""
    fb = ("\n\n上一轮被驳回,**本轮必须修复**:\n- " + "\n- ".join(feedback)) if feedback else ""
    return (
        f"目标:为业务流程「{plan.flow}」编写可执行适配器(Python)。\n"
        f"硬约束:入口函数 run(inputs: dict, creds: dict) -> dict;"
        f"凭证从 creds 取(如 creds['token']),**任何密钥都不得写进源码**。\n"
        f"步骤/契约:{plan.steps}\n成败规则(成功判定):{plan.success_rule}\n"
        f"参考骨架:\n{code_skeleton}\n"
        f"流程:调 draft_adapter 存代码 → 调 sandbox_test_adapter 自测 → "
        f"测不过按返回 reasons 修复重测 → 通过后调 publish_asset(附沙箱 validation_run_ids)。"
        f"{fb}"
    )
