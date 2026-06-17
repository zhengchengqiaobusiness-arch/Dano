"""M5 真跑:goal 模式**代码自动生成**一个请假 Skill(真实 pi 编码 + 闸门 + 事实核查)。

env 必填:DANO_OA_TOKEN(OA Bearer)、DANO_PI_API_KEY(pi 写代码 + 三模型评审)。
env 选填:DANO_OA_BASE_URL(默认真实 prod-api)、DANO_PG_DSN。
会对真实 OA 产生一条测试请假;token/key 仅经 env,不落文件。

流程:GenerationLoop(PiCoder) 用 workflow_bpmn 策略 → pi 按已沉淀的请假契约写 run() →
  隔离 runner 真跑(创建请假)→ 事实核查回查 → 三模型评审 → 发布。任一关 fail 带 reasons 回灌重写。
注:workflow_bpmn 的事实核查端点为提案,本次真跑用于校准(若回查口径不对,据结果调整策略)。
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

BACK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACK))

_BASE = "https://u858758-netf-d87bf18d.westd.seetacloud.com:8443/prod-api"


def _log(m): print(m, flush=True)


async def main() -> None:
    if not os.environ.get("DANO_OA_TOKEN"):
        sys.exit("❌ 需 DANO_OA_TOKEN")
    if not os.environ.get("DANO_PI_API_KEY"):
        sys.exit("❌ 需 DANO_PI_API_KEY(pi 写代码 + 三模型评审)")
    base = os.environ.get("DANO_OA_BASE_URL", _BASE).rstrip("/")
    os.environ["DANO_INSECURE_TLS"] = "1"
    os.environ["DANO_PG_DSN"] = os.environ.get(
        "DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")
    os.environ.setdefault("DANO_PI_BASE_URL", "https://api.deepseek.com")
    from dano.config import get_settings
    get_settings.cache_clear()

    from dano.agent_tools import materials, tools as T
    from dano.generation import GenerationLoop, GoalBrief, PiCoder
    from dano.generation.strategies import get_strategy
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations

    await init_pool(); await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='codegen-oa' AND asset_type='adapter'")

    run_id = "codegen-leave"
    stamp = datetime.now().strftime("%H%M%S")
    values = {"title": f"代码生成请假-{stamp}", "leaveType": "annual", "leaveDays": 1, "reason": "M5 真跑"}
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="codegen-oa", system_instance_id="A-OA", subsystem="A-OA",
        openapi={}, deploy={"base_url": base, "auth": {"kind": "token"}},
        credentials={"token": os.environ["DANO_OA_TOKEN"]}))

    goal = GoalBrief(
        run_id=run_id, system_instance_id="A-OA", flow="submit_leave",
        actions=[{"name": "submit_flow_task", "method": "POST", "endpoint": "/biz/flow/submit"}],
        test_input={"__base_url__": base, "templateId": "leave_template", "values": values})

    _log("=" * 64)
    _log(f"真实 OA = {base}  |  goal 模式代码自动生成:请假")
    _log("=" * 64)
    strategy = get_strategy("workflow_bpmn")
    result = await GenerationLoop(PiCoder()).run(goal, strategy)

    _log(f"\n结果:ok={result.ok} 驳回轮数={result.rejections} asset_id={result.asset_id}")
    for it in result.iterations:
        _log(f"  迭代{it.index}: {'通过' if it.passed else '驳回'} {('· ' + '; '.join(it.reasons)) if it.reasons else ''}")
    if not result.ok:
        _log(f"  未通过原因:{result.reason}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
