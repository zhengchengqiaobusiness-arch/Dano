"""落地版完整流程:真实导入 swagger → 预览选类别 → onboard 代码自动生成 → 真实调用。

与 run_full_demo 的区别:这里**真的从 OA 拉 swagger**、走生产入口 onboard(use_codegen),
读流程自动发现、写流程(请假)按 flows 声明。三模型评审走 config 里配的 SiliconFlow 三模型。

key 填在下方 _LOCAL["DANO_PI_API_KEY"]。直接 python examples/run_real_onboard.py 即可(任意目录)。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

BACK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACK))
_BASE = "https://u858758-netf-d87bf18d.westd.seetacloud.com:8443/prod-api"

# ── 本地一键配置(⚠ 明文密钥,勿提交 git;env 可覆盖)──
_LOCAL = {
    "DANO_OA_TOKEN":
    "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJzdXBlckFkbWluIiwibG9naW5fdXNlcl9rZXkiOiI3NzRmMWIzMC1iMTlkLTQ2YmMtOWUwYi1mZWNkZjkyMmYxYTYifQ.A2FACLYNomQBfnvpxfhgR7qh6-QZWTamZYJ-6UnqpZUctjGJLsNWAdTLar7nRM13f0l7xpZ0PKFlNGVsSRECSQ",
    "DANO_PI_API_KEY": "",                              # ← 填你的 SiliconFlow key(sk-...)
    "DANO_PI_BASE_URL": "https://api.siliconflow.cn/v1",
    "DANO_PI_MODEL": "deepseek-ai/DeepSeek-V3.2",
    "DANO_PG_DSN": "postgresql://postgres:111111@localhost:5432/dano_back",
}

# ── 接入参数(你要导入哪些类别、生成哪些写流程)──
SWAGGER_PATH = "/v3/api-docs"                          # OA 的 OpenAPI 文档路径
CHOSEN_TAGS = ["工作流-表单提交"]                       # ② 选类别(请假提交相关都在这个 tag 下)
MAX_READ_FLOWS = 2                                      # 自动生成的只读 adapter 上限(省时省钱)
WRITE_FLOWS = [                                        # 写流程:请假(只给业务字段,__base_url__ 自动注入)
    {"flow": "submit_leave",
     "test_input": {"templateId": "leave_template",
                    "values": {"title": "落地演示请假", "leaveType": "annual",
                               "leaveDays": 1, "reason": "落地端到端"}}},
]


def _log(m): print(m, flush=True)


async def main() -> None:
    os.chdir(BACK)
    for k, v in _LOCAL.items():
        os.environ.setdefault(k, v)
    os.environ["DANO_INSECURE_TLS"] = "1"
    token = os.environ["DANO_OA_TOKEN"]
    base = os.environ["DANO_PI_BASE_URL"]  # noqa: F841 - 触发 env 就绪
    oa_base = _BASE
    if not os.environ.get("DANO_PI_API_KEY"):
        sys.exit("❌ 请先在 _LOCAL['DANO_PI_API_KEY'] 填 SiliconFlow key")
    from dano.config import get_settings
    get_settings.cache_clear()

    import httpx

    # ① 真实导入 swagger(从 OA 拉 OpenAPI)
    _log("=" * 70)
    _log(f"① 导入 swagger: {oa_base}{SWAGGER_PATH}")
    async with httpx.AsyncClient(timeout=60, verify=False) as c:
        r = await c.get(oa_base + SWAGGER_PATH, headers={"Authorization": f"Bearer {token}"})
    swagger = r.json()
    _log(f"   拿到 swagger:{len(swagger.get('paths', {}))} 个 path")

    # ② 预览类别(按 tag 统计业务动作,过滤基础设施)
    from dano.capabilities import doc_parser, endpoint_classifier, oa_templates
    tmpl = oa_templates.match_template(swagger)
    extra = tmpl.infrastructure_patterns() if tmpl else ()
    cats: dict[str, int] = {}
    for a in doc_parser.parse_openapi(swagger):
        if endpoint_classifier.classify(a, extra_infra=extra) == endpoint_classifier.INFRASTRUCTURE:
            continue
        for t in (a.tags or ["(未分类)"]):
            cats[t] = cats.get(t, 0) + 1
    _log("② 类别预览(tag → 业务动作数,取前 12):")
    for t, n in sorted(cats.items(), key=lambda kv: -kv[1])[:12]:
        mark = " ←选中" if t in CHOSEN_TAGS else ""
        _log(f"     {n:3}  {t}{mark}")
    _log(f"   本次选类别:{CHOSEN_TAGS}  写流程声明:{[f['flow'] for f in WRITE_FLOWS]}  只读上限:{MAX_READ_FLOWS}")

    # ③–⑥ onboard:代码自动生成(读流程自动 + 写流程声明)→ 闸门 → 发布 → 上目录
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    from dano.lifecycle.state_machine import SkillLifecycle
    await init_pool(); await run_migrations()
    tenant = "real-onb"
    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM assets WHERE tenant=$1", tenant)

    from dano.onboarding import onboard
    _log("\n③–⑥ onboard(代码自动生成,真三模型评审走 SiliconFlow)…(较慢,请稍候)")
    lifecycle = SkillLifecycle()
    report = await onboard(
        tenant=tenant, subsystem="A-OA", openapi=swagger,
        deploy={"base_url": oa_base, "auth": {"kind": "token"}},
        credentials={"token": token},
        include_tags=CHOSEN_TAGS, flows=WRITE_FLOWS, max_read_flows=MAX_READ_FLOWS,
        use_codegen=True, lifecycle=lifecycle)
    _log(f"   onboard 完成:status={report.status}")
    _log(f"   已发布 Skill:{report.published_skills}")
    _log(f"   {report.pi_final_text}")

    # 看产物
    from dano.assets.repository import AssetRepository
    from dano.shared.enums import AssetType, Subsystem
    from dano.shared.models import Scope
    repo = AssetRepository()
    pubs = await repo.list_published(AssetType.ADAPTER, Scope(tenant=tenant, subsystem=Subsystem.OA))
    _log("\n【代码存哪】PG assets 表 body.source(按租户):")
    for e in pubs:
        _log(f"     skill={e.asset_key} v{e.version} asset_id={e.asset_id}")

    # ⑦ 真实调用 submit_leave(若已发布)
    if "submit_leave" not in report.published_skills:
        _log("\n⚠ submit_leave 未发布(可能某关被驳回),跳过调用。"); await close_pool(); return
    _log("\n⑦ 真实调用 A-OA.submit_leave(再创建一条请假)")
    from dano.catalog.manifest import build_manifests
    from dano.execution.connectors.executor import FakeActionExecutor
    from dano.execution.harness.harness import Harness
    from dano.orchestrator.orchestrator import Orchestrator
    from dano.orchestrator.skills import SkillRegistry
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=[Subsystem.OA])
    _log(f"   目录:{[(m.name, m.integration) for m in build_manifests(reg.skills)]}")
    orch = Orchestrator(registry=reg, store=repo,
                        harness=Harness(action_executor=FakeActionExecutor()),
                        action_executor=FakeActionExecutor(),
                        resolve_credentials=lambda refs: {"token": token})
    stamp = datetime.now().strftime("%H%M%S")
    out = await orch.invoke_skill(
        Subsystem.OA, "submit_leave",
        {"templateId": "leave_template",
         "values": {"title": f"落地调用请假-{stamp}", "leaveType": "personal",
                    "leaveDays": 2, "reason": "落地调用"}},
        tenant=tenant, confirm=True)
    _log(f"   调用 state={out.state.value}  {out.message}")
    _log(f"   返回={out.exec_result.structured_output if out.exec_result else None}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
