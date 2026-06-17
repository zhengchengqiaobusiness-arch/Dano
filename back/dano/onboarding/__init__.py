"""阶段一接入:模板分流 → pi 自主生成(经 Sidecar)→ 发布 → 接入报告。"""

from dano.onboarding.service import OnboardingReport, onboard

__all__ = ["OnboardingReport", "onboard"]
