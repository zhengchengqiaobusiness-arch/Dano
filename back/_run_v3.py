"""v3 真跑:全模型驱动生成请假 skill(无 RuoYi 硬编码)。token 从环境变量 OA_TOKEN 读,不落文件。"""
import asyncio
import os

import httpx

TOK = os.environ["OA_TOKEN"]
BASE = "https://u858758-netf-d87bf18d.westd.seetacloud.com:8443/prod-api"


async def main():
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    await init_pool()
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='v3-leave'")
    swagger = httpx.get(BASE + "/v3/api-docs", headers={"Authorization": "Bearer " + TOK},
                        verify=False, timeout=60).json()
    from dano.onboarding import onboard

    def prog(e):
        t = e.get("type")
        if t in ("flow_start", "coded", "gate", "verdict", "published", "rejected", "replanned", "exhausted"):
            print("  EV", t, {k: v for k, v in e.items() if k not in ("type", "ts", "flow")}, flush=True)

    rep = await onboard(
        tenant="v3-leave", subsystem="A-OA", openapi=swagger,
        deploy={"base_url": BASE, "auth": {"kind": "token"}}, credentials={"token": TOK},
        flows=[{"flow": "submit_leave", "test_input": {"templateId": "leave_template", "values": {
            "title": "v3模型生成·请假", "leaveType": "annual", "startDate": "2026-07-06",
            "endDate": "2026-07-07", "leaveDays": 2, "reason": "v3 全模型驱动验证"}}}],
        use_codegen=True, max_read_flows=0, progress=prog)
    print("=== REPORT ===", "status", rep.status, "published", rep.published_skills, "error", rep.error, flush=True)
    await close_pool()


asyncio.run(main())
