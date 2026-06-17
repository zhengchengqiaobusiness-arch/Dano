"""对真实 OA(新 swagger /tool/swagger)端到端创建一条请假,并做事实核查(流程9)。

只需 env:DANO_OA_TOKEN(Bearer)。选填:DANO_OA_BASE_URL(默认真实 prod-api)。
会对真实 OA 产生一条测试请假;token 仅经 env,不落文件。

这是「真实契约 + 事实核查」的可复现证明:不看接口返回的『操作成功』,以申请节点是否
真的完成(apply.completed)为准。submit 是空操作时,本脚本会判定失败。
"""

from __future__ import annotations

import asyncio
import os
import ssl
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

    import httpx

    from dano.capabilities.ruoyi_leave import RuoYiLeaveDriver

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # 自签/测试证书

    async def call(method: str, path: str, body=None):
        async with httpx.AsyncClient(timeout=30, verify=False) as c:
            headers = {"Authorization": f"Bearer {token}"}
            if method == "GET":
                r = await c.get(base + path, headers=headers)
            else:
                r = await c.request(method, base + path, json=body, headers=headers)
        try:
            return r.status_code, r.json()
        except Exception:  # noqa: BLE001
            return r.status_code, {"raw": r.text}

    driver = RuoYiLeaveDriver(call)

    _log("=" * 64)
    _log(f"真实 OA = {base}")
    _log("发现契约 → 创建请假 → 事实核查(以 apply.completed 为准)")
    _log("=" * 64)

    stamp = datetime.now().strftime("%H%M%S")
    values = {
        "title": f"自动化验证-年假1天-{stamp}",
        "leaveType": "annual",
        "leaveDays": 1,
        "reason": "Dano 端到端真实契约验证",
    }
    _log(f"① 提交请假:{values['title']}(年假 1 天)")
    res = await driver.create_leave(values)

    _log(f"  startFlow → procInsId={res.proc_ins_id} taskId={res.task_id} deployId={res.deploy_id}")
    _log(f"  form/save → businessId={res.business_id}")
    _log(f"  flow/submit 返回 → {res.submit_ack.get('msg')} (code={res.submit_ack.get('code')})")
    _log("")
    _log("② 事实核查(流程9):")
    nodes_state = {n.get("key"): n.get("completed") for n in res.node_data}
    _log(f"  flowXmlAndNode 节点状态 = {nodes_state}")
    _log(f"  apply.completed = {res.apply_completed}")
    _log("")

    if res.real:
        _log("✅ 已验证:请假**真实提交成功**(申请节点已完成,已流转到部门经理审批)。")
        _log("   证据可在 OA 复查:")
        _log("     - 我起草的: GET /workflow/draft/list (这条会出现)")
        _log("     - 流程历史: GET /flowable/monitor/listHistoryProcess")
        _log(f"     - 本实例:   GET /workflow/handle/flowXmlAndNode?procInsId={res.proc_ins_id}&deployId={res.deploy_id}")
    else:
        _log("❌ 事实核查未通过:submit 返回了『操作成功』但申请节点仍卡在 apply —")
        _log("   即空操作,系统判定**失败**,拒绝当作成功/拒绝发布。")
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
