"""Dano 后端配置结构；所有配置值统一来自进程环境或 ``back/.env``。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = str(Path(__file__).resolve().parents[1] / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DANO_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    insecure_tls: bool

    pg_dsn: str
    pg_pool_min: int
    pg_pool_max: int

    vault_addr: str
    vault_token: str
    require_vault: bool

    pi_api_key: str
    pi_base_url: str
    pi_model: str
    pi_provider: str

    runtime_credentials: dict
    token_refresh_sources: dict
    token_refresh_key: str

    review_enabled: bool
    review_model_acceptance: str
    review_model_security: str
    review_model_compliance: str
    review_timeout_s: float
    review_max_retries: int
    review_retry_backoff_s: float

    llm_max_input_tokens: int
    llm_max_output_tokens: int
    llm_cache_ttl_s: int

    skill_reference_root: str
    skill_reference_dir: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
