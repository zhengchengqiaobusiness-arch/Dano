"""集中配置。通过环境变量或 .env 注入(见 .env.example)。"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="DANO_", extra="ignore"
    )

    # ── 运行环境 ──
    env: str = Field(default="dev", description="dev / staging / prod")
    log_level: str = "INFO"
    insecure_tls: bool = Field(
        default=False, description="关闭 TLS 证书校验(仅自签/测试环境;生产保持 False)"
    )

    # ── PostgreSQL(资产库)──
    pg_dsn: str = Field(
        default="postgresql://postgres:111111@localhost:5432/dano",
        description="asyncpg DSN",
    )
    pg_pool_min: int = 1
    pg_pool_max: int = 10

    # ── Redis(失败计数/确认卡片状态)──
    redis_url: str = "redis://localhost:6379/0"

    # ── Temporal(持久化工作流)──
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "dano-orchestrator"

    # ── Vault(凭证引用,平台不持明文)──
    vault_addr: str = "http://localhost:8200"
    vault_token: str = Field(default="", description="开发用 root token;生产走 AppRole/K8s auth")

    # ── LLM(pi coding 生成 / 意图 / 审判)──
    # 默认走 OpenAI 兼容接口:base_url 可指向 OpenAI 官方 / DeepSeek / Qwen / 自建网关,
    # 模型名走配置随时切换;provider=anthropic 时改用 Anthropic 原生 SDK。
    llm_provider: str = "openai"                  # openai | anthropic
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"   # 换网关只改这里
    anthropic_api_key: str = ""                   # provider=anthropic 时使用
    model_generate: str = "gpt-4o"                # 生成/审判:强模型
    model_runtime: str = "gpt-4o-mini"            # 意图/执行:快模型

    # ── 三模型评审委员会(接入期发布前硬闸门:成果验收 / 漏洞检测 / 合规审核)──
    # 复用 pi 的 OpenAI 兼容凭证(DANO_PI_API_KEY + DANO_PI_BASE_URL);三审各用不同模型,
    # 发布闸门强制 distinct(model_id)=3。后期改模型名即可,无需改代码。
    review_enabled: bool = True
    # 三审各用**不同模型**(SiliconFlow);发布闸门强制 distinct(model_id)=3。改模型名即可,无需改代码。
    review_model_acceptance: str = "deepseek-ai/DeepSeek-V4-Pro"   # 成果验收:是否真满足业务意图
    review_model_security: str = "Pro/zai-org/GLM-5.1"            # 漏洞检测:注入/越权/密钥/SSRF/PII
    review_model_compliance: str = "deepseek-ai/DeepSeek-V3.2"     # 合规审核:沙箱/测试凭证/风险分级/确认
    review_timeout_s: float = 60.0                         # 单次评审 LLM 调用超时
    review_max_retries: int = 2                            # 瞬时错误(超时/5xx/限流)重试次数
    review_retry_backoff_s: float = 1.0                    # 指数退避基数(1s,2s,...)
    pi_api_key: str = ""                                   # = DANO_PI_API_KEY(评审 + 编码复用)
    pi_base_url: str = "https://api.siliconflow.cn/v1"     # = DANO_PI_BASE_URL(OpenAI 兼容)
    pi_model: str = "deepseek-ai/DeepSeek-V3.2"            # = DANO_PI_MODEL(PiCoder 编码用)

    # ── 企业微信接入 ──
    wechat_token: str = ""                        # 企微回调验签 Token

    # ── 运行期凭证(无 Vault 部署用;JSON:{"a-corp/oa":{"apikey":"..."}})──
    runtime_credentials: str = ""

    # ── 各依赖是否强制连接(M0 允许跳过未就绪的外部依赖)──
    require_temporal: bool = False
    require_vault: bool = False
    require_redis: bool = False
    require_pg: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
