"""集中配置。**密钥/凭证绝不写在本文件**,放 `back/.env`(已 gitignore,绝不入库);非密钥默认值在本文件改即生效。

读取优先级:进程环境变量(DANO_ 前缀)> `back/.env` > 本文件默认值。
.env 里用 DANO_ 前缀(如 `DANO_PI_API_KEY=...`);模板见 `back/.env.example`(入库,只放占位符)。
只保留实际被引用的配置项;Redis/Temporal/旧 LLM(openai/anthropic)等未接依赖已移除,需要时再加。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 固定在后端根目录(back/.env),按本文件位置定位 → 不管从哪个 CWD 启动(uvicorn/docker/pytest)都能读到;
# 仍 gitignore、绝不入库。密钥只放这,不写进源码。
_ENV_FILE = str(Path(__file__).resolve().parents[1] / ".env")


class Settings(BaseSettings):
    # 读 back/.env(密钥放这);优先级:进程环境变量 > .env > 下面的默认值
    model_config = SettingsConfigDict(env_prefix="DANO_", env_file=_ENV_FILE,
                                      env_file_encoding="utf-8", extra="ignore")

    # ── TLS ──
    insecure_tls: bool = Field(
        default=False, description="关闭 TLS 证书校验(仅自签/测试环境;生产保持 False)")

    # ── PostgreSQL(资产库)──
    pg_dsn: str = Field(
        default="postgresql://postgres:111111@localhost:5432/dano_back", description="asyncpg DSN")
    pg_pool_min: int = 1
    pg_pool_max: int = 10

    # ── Vault(凭证引用,平台不持明文)──
    vault_addr: str = "http://localhost:8200"
    vault_token: str = Field(default="", description="开发用 root token;生产走 AppRole/K8s auth")
    require_vault: bool = False     # true=必须从 Vault 取,失败即报错(fail-closed,不回退 env)

    # ── LLM(pi 编码 + 三模型评审,OpenAI 兼容)──
    # ⚠ 密钥放 back/.env 的 DANO_PI_API_KEY,**勿写进本文件**(本文件入库=明文泄露)。默认空 → 必须由 .env/环境提供。
    pi_api_key: str = Field(default="", description="= DANO_PI_API_KEY;密钥只放 back/.env,勿硬编码")
    pi_base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"     # = DANO_PI_BASE_URL(非密钥,可 .env 覆盖)
    pi_model: str = "mimo-v2.5-pro"            # = DANO_PI_MODEL(评审/分类的 OpenAI 兼容模型)
    # pi agent(run_pi.mjs)的 provider 名:配了 pi_base_url 时,run_pi.mjs 会注册一个 **OpenAI 兼容** provider
    # (用 pi_base_url + pi_api_key + pi_model,api=openai-completions),SiliconFlow 这类直接可用;留空=用 "openai-compat"。
    # 仅当不配 pi_base_url 时才退回 pi 内置 provider(那时这里填内置名,如 deepseek)。
    pi_provider: str = ""                          # = DANO_PI_PROVIDER(留空即可,走 OpenAI 兼容)

    # ── 调用期 OA 凭证(运行期 invoke 取它打目标系统;键 = 租户/系统key,如 "1/oa")──
    # 在此填(或留空:接入时页面 token 会临时写进进程内存)。生产应走 Vault(见上)。
    runtime_credentials: dict = Field(default_factory=dict, description='如 {"1/oa": {"token": "..."}}')

    # ── 三模型评审委员会(发布前硬闸门;强制 distinct(model_id)=3,改模型名即可)──
    review_enabled: bool = True
    review_model_acceptance: str = "mimo-v2.5-pro"   # 成果验收:是否真满足业务意图
    review_model_security: str = "mimo-v2.5-pro"            # 漏洞检测:注入/越权/密钥/SSRF/PII
    review_model_compliance: str = "mimo-v2.5-pro"     # 合规审核:沙箱/测试凭证/风险/确认
    review_timeout_s: float = 240.0   # 单模型评审超时:OpenAI 兼容共享端点(SiliconFlow)拥塞时单次可达 ~180s,给足余量
    review_max_retries: int = 2
    review_retry_backoff_s: float = 1.0

    # ── 页面型 Skill(流程8,无 API · Playwright);env 名保持 DANO_ 前缀直读不变 ──
    page_runtime: bool = False           # = DANO_PAGE_RUNTIME:运行期 invoke 页面 Skill 需开(否则「页面运行时未装配」);接入侦察/回放不依赖它
    browser_headless: bool = True        # = DANO_BROWSER_HEADLESS:浏览器无头(调试可设 False 看界面)
    browser_pool_size: int = 2           # = DANO_BROWSER_POOL_SIZE:运行期并发浏览器上限(信号量)
    page_timeout_s: float = 120.0        # = DANO_PAGE_TIMEOUT_S:单次页面运行总超时(防卡死)
    page_write_probe: bool = False       # = DANO_PAGE_WRITE_PROBE:写页面沙箱回放是否真点提交(默认 dry,不真建单)
    page_base_url: str = ""              # = DANO_PAGE_BASE_URL:运行期相对 start_url 的拼接基址
    page_storage_state: str = ""         # = DANO_PAGE_STORAGE_STATE:登录态(Playwright storageState JSON 路径)


@lru_cache
def get_settings() -> Settings:
    return Settings()
