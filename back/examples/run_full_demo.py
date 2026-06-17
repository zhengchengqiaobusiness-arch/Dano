"""完整演示:6 步全跑 —— goal 模式生成请假 Skill(真实 OA)→ 看代码存哪 → 真实调用。

env 必填:DANO_OA_TOKEN、DANO_PI_API_KEY。
env 选填:DANO_OA_BASE_URL(默认真实 prod-api)、DANO_PG_DSN、DANO_REVIEW_ENABLED。
说明:该 DeepSeek 端点只有 2 个模型时,三模型审核无法满足 distinct=3,请设 DANO_REVIEW_ENABLED=false
     (沙箱+漏扫+事实核查照常)。会对真实 OA 产生 2 条测试请假(生成时 1 条 + 调用时 1 条)。
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

# ─────────────────────────────────────────────────────────────────────────────
# 本地一键运行:密钥直接写死在此(免设环境变量)。
# ⚠⚠ 安全警告:这是明文密钥,切勿提交到 git、勿用于生产、用完请轮换!仅本地测试图方便。
# env 仍可覆盖(若设了同名环境变量,以 env 为准)。
# ─────────────────────────────────────────────────────────────────────────────
_LOCAL = {
    "DANO_OA_TOKEN": 
    "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJzdXBlckFkbWluIiwibG9naW5fdXNlcl9rZXkiOiI3NzRmMWIzMC1iMTlkLTQ2YmMtOWUwYi1mZWNkZjkyMmYxYTYifQ.A2FACLYNomQBfnvpxfhgR7qh6-QZWTamZYJ-6UnqpZUctjGJLsNWAdTLar7nRM13f0l7xpZ0PKFlNGVsSRECSQ",
    # ↓↓↓ 在这里填你的 SiliconFlow(硅基流动)API Key ↓↓↓
    "DANO_PI_API_KEY": "sk-gsgpzoimegwgeiscfxfjhtwfegifngjvejwjfatuoxrzytmn",
    # ↑↑↑ 形如 sk-xxxxxxxx;留空会报评审无凭证。↑↑↑
    "DANO_OA_BASE_URL": _BASE,
    # SiliconFlow:PiCoder 编码 + 三模型评审都走它(OpenAI 兼容)
    "DANO_PI_BASE_URL": "https://api.siliconflow.cn/v1",
    "DANO_PI_MODEL": "deepseek-ai/DeepSeek-V3.2",   # 编码用
    # 三模型评审默认启用且互异(见 config):验收=DeepSeek-V4-Pro · 漏洞=GLM-5.1 · 合规=DeepSeek-V3.2
    "DANO_PG_DSN": "postgresql://postgres:111111@localhost:5432/dano_back",
}


def _log(m): print(m, flush=True)


async def _publish_env_profile(tenant: str, base_url: str) -> None:
    from dano.assets.repository import AssetRepository
    from dano.shared.asset_bodies import AuthConfig, EnvProfileBody
    from dano.shared.enums import AssetType, Subsystem, ValidationStatus
    from dano.shared.models import AssetEnvelope, Scope
    repo = AssetRepository()
    e = await repo.create(AssetEnvelope(
        asset_type=AssetType.ENV_PROFILE, scope=Scope(tenant=tenant, subsystem=Subsystem.OA),
        asset_key="env_profile", version=0, source_fingerprint="demo",
        validation_status=ValidationStatus.VERIFIED, confidence=0.9,
        body=EnvProfileBody(deploy="saas", worker_location="平台托管", intranet_access="public",
            account_type="test", base_url=base_url, auth=AuthConfig(kind="token")).model_dump()))
    await repo.set_status(e.asset_id, ValidationStatus.PUBLISHED)


async def main() -> None:
    os.chdir(BACK)                                  # 任意目录都能跑(migrations 用相对路径)
    for k, v in _LOCAL.items():                     # 写死的本地配置(env 优先)
        os.environ.setdefault(k, v)
    os.environ["DANO_INSECURE_TLS"] = "1"           # 自签证书
    token = os.environ["DANO_OA_TOKEN"]
    base = os.environ["DANO_OA_BASE_URL"].rstrip("/")
    from dano.config import get_settings
    get_settings.cache_clear()

    from dano.agent_tools import materials
    from dano.generation import GenerationLoop, GoalBrief, PiCoder
    from dano.generation.strategies import get_strategy
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations

    await init_pool(); await run_migrations()
    tenant = "demo-oa"
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)

    run_id = "full-demo"
    stamp = datetime.now().strftime("%H%M%S")
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant=tenant, system_instance_id="A-OA", subsystem="A-OA",
        openapi={}, deploy={"base_url": base, "auth": {"kind": "token"}},
        credentials={"token": token}))
    await _publish_env_profile(tenant, base)

    # ── 步骤①–⑤:goal 模式生成 + 闸门 + 发布 ──
    _log("=" * 64)
    _log("【阶段一 步骤①–⑤】goal 模式生成请假代码 Skill(真实 OA)")
    _log("=" * 64)
    gen_values = {"title": f"演示生成请假-{stamp}", "leaveType": "annual", "leaveDays": 1, "reason": "完整演示-生成"}
    goal = GoalBrief(run_id=run_id, system_instance_id="A-OA", flow="submit_leave",
                     actions=[{"name": "submit_flow_task", "method": "POST", "endpoint": "/biz/flow/submit"}],
                     test_input={"__base_url__": base, "templateId": "leave_template", "values": gen_values})
    res = await GenerationLoop(PiCoder()).run(goal, get_strategy("workflow_bpmn"))
    _log(f"  生成结果: ok={res.ok}  驳回轮数={res.rejections}  asset_id={res.asset_id}")
    if not res.ok:
        for it in res.iterations:
            _log(f"    迭代{it.index}: {'通过' if it.passed else '驳回 · ' + '; '.join(it.reasons)}")
        await close_pool(); return

    # ── 看产物落在哪 ──
    async with get_pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT asset_id, version, body FROM assets WHERE tenant=$1 AND asset_type='adapter' "
            "AND validation_status='published' ORDER BY created_at DESC LIMIT 1", tenant)
    body = json.loads(row["body"])
    _log("\n【代码存哪】PG  assets 表(asset_type=adapter,按租户版本化)")
    _log(f"  asset_id={row['asset_id']}  version={row['version']}  action={body['action']}")
    _log(f"  生成的 run() 源码(body.source 前 240 字):")
    _log("  " + body["source"][:240].replace("\n", "\n  "))

    # ── 步骤⑥ + 运行期:上目录 + 真实调用 ──
    _log("\n" + "=" * 64)
    _log("【步骤⑥ + 运行期】上目录 → 真实调用(再创建一条请假)")
    _log("=" * 64)
    from dano.assets.repository import AssetRepository
    from dano.catalog.manifest import build_manifests
    from dano.execution.connectors.executor import FakeActionExecutor
    from dano.execution.harness.harness import Harness
    from dano.orchestrator.orchestrator import Orchestrator
    from dano.orchestrator.skills import SkillRegistry
    from dano.shared.enums import Subsystem
    repo = AssetRepository()
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=[Subsystem.OA])
    man = [m for m in build_manifests(reg.skills) if m.integration == "adapter"]
    _log(f"  目录条目: {[(m.name, m.integration, m.risk_level) for m in man]}")

    orch = Orchestrator(registry=reg, store=repo,
                        harness=Harness(action_executor=FakeActionExecutor()),
                        action_executor=FakeActionExecutor(),
                        resolve_credentials=lambda refs: {"token": token})
    inv_values = {"title": f"演示调用请假-{stamp}", "leaveType": "personal", "leaveDays": 2, "reason": "完整演示-调用"}
    out = await orch.invoke_skill(
        Subsystem.OA, "submit_leave",
        {"templateId": "leave_template", "values": inv_values}, tenant=tenant, confirm=True)
    _log(f"\n  调用 state={out.state.value}  message={out.message}")
    _log(f"  返回={out.exec_result.structured_output if out.exec_result else None}")
    if out.state.value == "completed":
        _log("  ✅ 调用真实创建了请假并通过事实核查;可在 OA /workflow/draft/list 复查标题含『演示调用请假』")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
