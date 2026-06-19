"""临时:真跑「请假」业务展开(expand_business)→ 多操作 adapter → 导出剧本 skill。
token 从环境变量 OA_TOKEN 读,不落文件。用后即删。"""
import asyncio
import os

import httpx

TOK = os.environ["OA_TOKEN"]
BASE = "https://u858758-netf-d87bf18d.westd.seetacloud.com:8443/prod-api"
SWAGGER_URL = "https://u858758-netf-d87bf18d.westd.seetacloud.com:8443/v3/api-docs"
EXPORT_DIR = "E:/python/try/Dano/export/agent-skills"
TENANT = "biz-leave"


async def main():
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    await init_pool()
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant=$1", TENANT)
    swagger = httpx.get(SWAGGER_URL, verify=False, timeout=90).json()
    from dano.onboarding import onboard

    def prog(e):
        t = e.get("type")
        if t in ("plan", "business_expanded", "flow_start", "coded", "gate", "verdict",
                 "rejected", "published", "exhausted", "flow_done"):
            print("  EV", t, {k: v for k, v in e.items() if k not in ("type", "ts", "flow")}, flush=True)

    rep = await onboard(
        tenant=TENANT, subsystem="A-OA", openapi=swagger,
        deploy={"base_url": BASE, "auth": {"kind": "token"}}, credentials={"token": TOK},
        flows=[{"flow": "submit_demo_leave", "test_input": {"templateId": "leave_template", "values": {
            "title": "请假·业务验证", "leaveType": "annual", "startDate": "2026-07-06",
            "endDate": "2026-07-07", "leaveDays": 2, "reason": "多操作业务验证"}}}],
        use_codegen=True, max_read_flows=0, progress=prog)   # expand_business 默认开
    print("=== REPORT ===", "status", rep.status, "published", rep.published_skills,
          "error", rep.error, flush=True)

    # 导出剧本 skill
    from dano.export.agent_skills import write_skills
    written = await write_skills(TENANT, EXPORT_DIR)
    print("=== EXPORTED ===", written, "-> ", EXPORT_DIR, flush=True)
    await close_pool()


asyncio.run(main())
