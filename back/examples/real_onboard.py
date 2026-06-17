"""真实接入一键脚本:填 OA 信息 + swagger → 跑完整阶段一 → 列出生成的 Skill → 真实调用(只读)。

走的就是前端那条路:POST /onboarding → GET /v1/skills → POST /v1/skills/{id}/invoke。
全程真打你的 OA(只用你给的测试账号/token),连接器与复合流程都要过:
  沙箱验证(sandbox/test 凭证) + 三模型评审(成果验收 deepseek-reasoner / 漏洞检测 deepseek-v4-pro /
  合规审核 deepseek-v4-flash) + 不可伪造发布闸门。

──────────────────────── 用前准备 ────────────────────────
1) PostgreSQL 起着,库存在(下方 PG_DSN;默认 dano_back)。
2) 评审 + pi 的 key 经环境变量注入(绝不写进文件):
       PowerShell:  $env:DANO_PI_API_KEY='sk-你的key'
3) Node 装好,且 agent 依赖已装:  cd back/agent ; npm install
4) 填好下方 CONFIG,然后:        python -m examples.real_onboard

只读动作会被自动试调一次;写/删动作只列出,不自动执行(需你显式 confirm)。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

# ──────────────────────────── CONFIG:改这里 ────────────────────────────
CONFIG = {
    # 你的 OA
    "base_url": "http://localhost:9002",         # 例:https://oa.yourcompany.com
    "swagger_path": "examples/ruoyi_oa.yaml",     # swagger/openapi 文件(.yaml/.yml/.json)

    # 鉴权(二选一):
    #   A) 直接给 token(最简单,推荐):填 token,username/password 留空。
    #   B) 给账号密码:脚本先 POST {base_url}{login_path} 换 token(适用 RuoYi /login 返回 {token})。
    "token": "ruoyi-mock-token-xyz",
    "username": "",
    "password": "",
    "login_path": "/login",                       # 账号密码登录端点(仅 token 为空时用)
    "login_token_field": "token",                 # 登录响应里 token 的字段名

    # token 在请求头怎么带(RuoYi 默认即可)
    "token_header": "Authorization",
    "token_prefix": "Bearer ",

    # 作用域
    "tenant": "demo",
    "subsystem": "A-OA",                          # 必须是 A-OA / A-工单 / A-报销 之一

    # 基础设施
    "pg_dsn": os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back"),
    "onboard_timeout_s": 300.0,                   # 每个 pi 阶段的超时(端点多/评审慢可调大)
}
# ────────────────────────────────────────────────────────────────────────

BACK = Path(__file__).resolve().parent.parent


def _log(msg: str) -> None:
    print(msg, flush=True)


def _load_spec(path: str) -> dict:
    p = (BACK / path) if not Path(path).is_absolute() else Path(path)
    if not p.exists():
        sys.exit(f"❌ swagger 文件不存在: {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml
        return yaml.safe_load(text)
    return json.loads(text)


async def _resolve_token() -> str:
    """token 优先级:env DANO_OA_TOKEN > CONFIG.token > 账号密码登录换。token 不落文件。"""
    tok = os.environ.get("DANO_OA_TOKEN") or CONFIG["token"]
    if tok:
        return tok
    if not (CONFIG["username"] and CONFIG["password"]):
        sys.exit("❌ 既没填 token,也没填 username/password,无法鉴权。")
    url = CONFIG["base_url"].rstrip("/") + CONFIG["login_path"]
    _log(f"→ 登录换 token: POST {url}")
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(url, json={"username": CONFIG["username"], "password": CONFIG["password"]})
        r.raise_for_status()
        data = r.json()
    token = data.get(CONFIG["login_token_field"])
    if not token:
        sys.exit(f"❌ 登录未拿到 token(响应:{data});若你的 OA 需要验证码,请直接填 token。")
    _log("✓ 登录成功,已拿到 token")
    return token


def _preflight() -> None:
    if not os.environ.get("DANO_PI_API_KEY"):
        sys.exit("❌ 未设置 DANO_PI_API_KEY(pi 生成 + 三模型评审都要用)。"
                 "PowerShell: $env:DANO_PI_API_KEY='sk-...'")
    import shutil
    if shutil.which("node") is None:
        sys.exit("❌ 未找到 node。请安装 Node 后重试。")
    if not (BACK / "agent" / "node_modules").exists():
        sys.exit("❌ agent 依赖未安装。请先: cd back/agent ; npm install")


async def main() -> None:
    # env 覆盖(便于真实接入,不把地址/凭证写进文件)
    CONFIG["base_url"] = os.environ.get("DANO_OA_BASE_URL", CONFIG["base_url"])
    CONFIG["swagger_path"] = os.environ.get("DANO_OA_SWAGGER", CONFIG["swagger_path"])
    CONFIG["tenant"] = os.environ.get("DANO_OA_TENANT", CONFIG["tenant"])
    _preflight()
    os.environ["DANO_PG_DSN"] = CONFIG["pg_dsn"]
    os.environ.setdefault("DANO_PI_BASE_URL", "https://api.deepseek.com")

    from dano.config import get_settings
    get_settings.cache_clear()

    spec = _load_spec(CONFIG["swagger_path"])
    token = await _resolve_token()

    # ── 初始化 PG + 网关单例(与 /onboarding、/v1/skills 同一套)──
    from dano.infra.db import close_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"❌ 连接 PostgreSQL 失败({CONFIG['pg_dsn']}): {e}")
    await run_migrations()

    import dano.gateway.app as gw
    from dano.registry import PgRegistry, TenantRecord
    gw._registry = PgRegistry()

    tenant = CONFIG["tenant"]
    subsystem = CONFIG["subsystem"]

    # 租户 key 进程内解析:已有就复用(避免重复建租户拿到不被持久化的新 key → 401)
    existing = await gw._registry.get_tenant(tenant)
    if existing:
        tenant_key = existing.api_key
    else:
        _rec = TenantRecord(tenant=tenant, display_name=tenant)
        await gw._registry.create_tenant(_rec)
        tenant_key = _rec.api_key
    deploy = {
        "base_url": CONFIG["base_url"], "deploy": "saas", "account_type": "test",
        "auth": {"kind": "token", "token_header": CONFIG["token_header"],
                 "token_prefix": CONFIG["token_prefix"]},
    }
    credentials = {"token": token}

    _log("\n========== 阶段一:接入(真打 OA + pi 生成 + 三模型评审 + 发布闸门)==========")
    _log(f"租户={tenant}  子系统={subsystem}  OA={CONFIG['base_url']}")
    _log("（pi 会逐动作 draft→sandbox→三模型评审→publish,再 goal 模式发现复合流程,过程见下方日志）\n")

    from dano.onboarding import onboard
    report = await onboard(
        tenant=tenant, subsystem=subsystem, openapi=spec, deploy=deploy,
        credentials=credentials, system_instance_id=subsystem,
        lifecycle=gw._lifecycle, discover_workflows=True,
        timeout_s=CONFIG["onboard_timeout_s"],
    )
    _log("\n---------- 接入报告 ----------")
    _log(f"状态: {report.status}")
    _log(f"已发布 Skill: {report.published_skills}")
    if report.pi_final_text:
        _log(f"pi 总结: {report.pi_final_text}")
    if report.error:
        _log(f"错误: {report.error}")

    # ── 设置运行期凭证(给 invoke 用):键 = 已发布连接器 auth_ref 去掉 vault:// ──
    from dano.assets.repository import AssetRepository
    from dano.shared.enums import AssetType, Subsystem
    from dano.shared.models import Scope
    repo = AssetRepository()
    scope = Scope(tenant=tenant, subsystem=Subsystem(subsystem))
    conns = await repo.list_published(AssetType.CONNECTOR, scope)
    cred_table: dict[str, dict] = {}
    for e in conns:
        ref = e.body.get("auth_ref", "")
        path = ref[len("vault://"):] if ref.startswith("vault://") else ref
        if path:
            cred_table[path] = {"token": token}
    os.environ["DANO_RUNTIME_CREDENTIALS"] = json.dumps(cred_table)

    # ── 列出生成的 Skill(前端契约,GET /v1/skills)──
    _log("\n========== 生成的 Skill(前端 GET /v1/skills 看到的契约)==========")
    transport = httpx.ASGITransport(app=gw.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://local") as c:
        key = tenant_key
        skills = (await c.get("/v1/skills", headers={"X-Tenant-Key": key})).json()
        if not skills:
            _log("（没有已发布 Skill;看上面的接入报告/错误)")
        for m in skills:
            integ = m.get("integration", "api")
            params = m.get("parameters", {}) or m.get("input_schema", {})
            req_fields = params.get("required", []) if isinstance(params, dict) else []
            _log(f"\n• {m['name']}  [{integ}]  {m.get('title','')}")
            _log(f"    必填字段: {req_fields}")
            _log(f"    契约: {json.dumps(m, ensure_ascii=False)[:400]}")

        # ── 真实调用:只对只读(GET)连接器自动试调,写/删不碰生产 ──
        _log("\n========== 真实调用(只读 Skill 自动试调一次)==========")
        get_actions = {e.body.get("action") for e in conns if e.body.get("method", "").upper() == "GET"}
        target = next((m for m in skills
                       if m.get("integration", "api") == "api"
                       and m["name"].split(".", 1)[-1] in get_actions), None)
        if target is None:
            _log("（没有只读 Skill 可安全试调;写操作请你自己带 confirm 调 "
                 "POST /v1/skills/{id}/invoke）")
        else:
            sid = target["name"]
            _log(f"→ POST /v1/skills/{sid}/invoke  (input={{}})")
            r = await c.post(f"/v1/skills/{sid}/invoke", headers={"X-Tenant-Key": key},
                             json={"input": {}, "confirm": False})
            _log(f"← {r.status_code}: {json.dumps(r.json(), ensure_ascii=False)[:600]}")

    await close_pool()
    _log("\n✓ 完成。")


if __name__ == "__main__":
    asyncio.run(main())
