"""集中配置。通过环境变量或 .env 注入(DANO_ 前缀;见 .env.example)。

只保留实际被引用的配置项;Redis/Temporal/旧 LLM(openai/anthropic)等未接的依赖已移除,
需要时再加回。env 解析 extra='ignore',旧 .env 里的多余键不会报错。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="DANO_", extra="ignore"
    )

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
    pi_base_url: str = "https://api.siliconflow.cn/v1"     # = DANO_PI_BASE_URL
    pi_model: str = "moonshotai/Kimi-K2.7-Code"            # = DANO_PI_MODEL(PiCoder 编码用)

    # ── 三模型评审委员会(发布前硬闸门;强制 distinct(model_id)=3,改模型名即可)──
    review_enabled: bool = True
    review_model_acceptance: str = "deepseek-ai/DeepSeek-V4-Pro"   # 成果验收:是否真满足业务意图
    review_model_security: str = "Pro/zai-org/GLM-5.1"            # 漏洞检测:注入/越权/密钥/SSRF/PII
    review_model_compliance: str = "deepseek-ai/DeepSeek-V4-Flash"     # 合规审核:沙箱/测试凭证/风险/确认
    review_timeout_s: float = 60.0
    review_max_retries: int = 2
    review_retry_backoff_s: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
