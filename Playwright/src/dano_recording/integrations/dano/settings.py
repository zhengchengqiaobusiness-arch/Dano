"""Read the one shared Dano settings object; no second .env or model config."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecordingSettings:
    browser_headless: bool
    page_timeout_s: float
    pi_api_key: str
    pi_base_url: str
    pi_provider: str
    pi_model: str
    review_enabled: bool
    review_models: dict[str, str]


def recording_settings() -> RecordingSettings:
    from dano.config import get_settings

    value = get_settings()
    return RecordingSettings(
        browser_headless=value.browser_headless,
        page_timeout_s=value.page_timeout_s,
        pi_api_key=value.pi_api_key,
        pi_base_url=value.pi_base_url,
        pi_provider=value.pi_provider,
        pi_model=value.pi_model,
        review_enabled=value.review_enabled,
        review_models={
            "acceptance": value.review_model_acceptance,
            "security": value.review_model_security,
            "compliance": value.review_model_compliance,
        },
    )
