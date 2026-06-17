"""真实 OA(新 swagger /tool/swagger)请假 Skill 全流程:发现契约 → 真实创建 → 事实核查 → 三模型评审。

env 必填:DANO_OA_TOKEN(Bearer)。
env 选填:DANO_PI_API_KEY(开三模型评审,缺省则跳过评审,仅做真实创建+事实核查)、
         DANO_OA_BASE_URL(默认真实 prod-api)。
会对真实 OA 产生一条测试请假;token / key 仅经 env,不落文件。

与旧版的区别:用 dano.capabilities.ruoyi_leave 的**已验证真实契约**(startFlow→form/save→submit)
+ 流程9 事实核查(apply.completed)。submit 是空操作时,本脚本判失败、不当作成功、不送评审。
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


def _log(m: str) -> None:
    print(m, flush=True)


async def main() -> None:
    token = os.environ.get("DANO_OA_TOKEN")
    if not token:
        sys.exit("❌ 需 DANO_OA_TOKEN(真实 OA Bearer token)")
    base = os.environ.get("DANO_OA_BASE_URL", _BASE).rstrip("/")
    have_key = bool(os.environ.get("DANO_PI_API_KEY"))
    os.environ["DANO_INSECURE_TLS"] = "1"                      # 自签/测试证书
    os.environ.setdefault("DANO_PI_BASE_URL", "https://api.deepseek.com")

    import httpx

    from dano.capabilities.oa_templates import RuoYiFlowableTemplate
    from dano.capabilities.ruoyi_leave import RuoYiLeaveDriver
    from dano.config import get_settings
    get_settings.cache_clear()

    async def call(method, path, body=None):
        async with httpx.AsyncClient(timeout=30, verify=False) as c:
            h = {"Authorization": f"Bearer {token}"}
            r = await (c.get(base + path, headers=h) if method == "GET"
                       else c.request(method, base + path, json=body, headers=h))
        try:
            return r.status_code, r.json()
        except Exception:  # noqa: BLE001
            return r.status_code, {"raw": r.text}

    _log("=" * 64)
    _log(f"真实 OA = {base}")
    _log("发现契约 → 真实创建请假 → 事实核查(流程9) → 三模型评审")
    _log("=" * 64)

    # ① 真实创建 + 事实核查
    stamp = datetime.now().strftime("%H%M%S")
    values = {"title": f"请假Skill验证-{stamp}", "leaveType": "annual",
              "leaveDays": 1, "reason": "Dano 全流程真实验证"}
    _log(f"① 真实创建请假:{values['title']}")
    res = await RuoYiLeaveDriver(call).create_leave(values)
    _log(f"   startFlow procInsId={res.proc_ins_id} taskId={res.task_id} | "
         f"form/save businessId={res.business_id} | submit={res.submit_ack.get('msg')}")
    nodes_str = {n.get("key"): n.get("completed") for n in res.node_data}
    _log(f"② 事实核查:apply.completed={res.apply_completed} nodes={nodes_str}")
    if not res.real:
        _log("❌ 事实核查未通过(submit 空操作),判失败、不送评审、不发布。")
        sys.exit(2)
    _log("✅ 请假真实提交成功(已流转到部门经理审批);可在 OA /workflow/draft/list 复查。")

    # ② 三模型评审(成果验收 / 漏洞检测 / 合规审核,3 个不同模型)
    recipe = RuoYiFlowableTemplate().workflows()[0]            # submit_leave 复合 Skill
    body = recipe.model_dump()
    evidence = [{
        "kind": "sandbox", "passed": True,
        "fact_check": "flowXmlAndNode apply.completed=True(申请节点已完成)",
        "proc_ins_id": res.proc_ins_id, "business_id": res.business_id,
        "node_data": res.node_data,
    }]

    if not have_key:
        _log("\n⚠ 未设置 DANO_PI_API_KEY:跳过三模型评审。")
        _log("  已完成『发现契约+真实创建+事实核查』;设 key 后本脚本会续跑三模型评审。")
        return

    from dano.review.board import ReviewBoard
    _log("\n③ 三模型评审(asset=workflow/submit_leave,附事实核查证据):")
    board = ReviewBoard.from_settings()
    verdicts = await board.review(asset_type="workflow", asset_key=recipe.action,
                                  body=body, evidence=evidence)
    all_passed = all(v.passed for v in verdicts)
    for v in verdicts:
        _log(f"   {v.role:11}({v.model_id}) = {'通过' if v.passed else '驳回'}"
             + ("" if v.passed else f" 理由:{v.reasons}"))
    models = {v.model_id for v in verdicts}
    _log(f"   三模型互异 = {len(models) == 3} ({sorted(models)})")
    _log("\n" + ("✅ 全流程通过:真实创建+事实核查+三模型评审皆过 → 可发布。"
                 if all_passed else "❌ 评审未全过 → 不发布。"))


if __name__ == "__main__":
    asyncio.run(main())
