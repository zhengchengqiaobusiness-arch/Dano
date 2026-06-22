"""集中配置(**唯一来源 = 本文件**)。**不读 .env**;在这里改默认值即生效。

进程环境变量(DANO_ 前缀)仍可临时覆盖,但日常配置全在本文件改。
只保留实际被引用的配置项;Redis/Temporal/旧 LLM(openai/anthropic)等未接依赖已移除,需要时再加。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 不配 env_file → 不读 .env;config.py 默认值为准(DANO_ 前缀的进程环境变量仍可覆盖)
    model_config = SettingsConfigDict(env_prefix="DANO_", extra="ignore")

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
    pi_api_key: str = "sk-gsgpzoimegwgeiscfxfjhtwfegifngjvejwjfatuoxrzytmn"                                   # = DANO_PI_API_KEY(编码 + 评审复用)
    pi_base_url: str = "https://api.siliconflow.cn/v1"     # = DANO_PI_BASE_URL(openai_text_spawn 评审/分类用)
    pi_model: str = "moonshotai/Kimi-K2.7-Code"            # = DANO_PI_MODEL(评审/分类的 OpenAI 兼容模型)
    # pi agent(run_pi.mjs)的 provider 名:配了 pi_base_url 时,run_pi.mjs 会注册一个 **OpenAI 兼容** provider
    # (用 pi_base_url + pi_api_key + pi_model,api=openai-completions),SiliconFlow 这类直接可用;留空=用 "openai-compat"。
    # 仅当不配 pi_base_url 时才退回 pi 内置 provider(那时这里填内置名,如 deepseek)。
    pi_provider: str = ""                          # = DANO_PI_PROVIDER(留空即可,走 OpenAI 兼容)

    # ── 调用期 OA 凭证(运行期 invoke 取它打目标系统;键 = 租户/系统key,如 "1/oa")──
    # 在此填(或留空:接入时页面 token 会临时写进进程内存)。生产应走 Vault(见上)。
    runtime_credentials: dict = Field(default_factory=dict, description='如 {"1/oa": {"token": "..."}}')

    # ── 三模型评审委员会(发布前硬闸门;强制 distinct(model_id)=3,改模型名即可)──
    review_enabled: bool = True
    review_model_acceptance: str = "zai-org/GLM-5.2"   # 成果验收:是否真满足业务意图
    review_model_security: str = "Pro/moonshotai/Kimi-K2.6"            # 漏洞检测:注入/越权/密钥/SSRF/PII
    review_model_compliance: str = "deepseek-ai/DeepSeek-V4-Flash"     # 合规审核:沙箱/测试凭证/风险/确认
    review_timeout_s: float = 60.0
    review_max_retries: int = 2
    review_retry_backoff_s: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
