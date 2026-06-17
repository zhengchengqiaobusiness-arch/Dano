"""鉴权适配器库:库中选,不自造(文档关键设计点·140 行)。

Agent 不臆造认证流程,而是从平台预置的适配器里**匹配选用**(OA=SSO、工单=Token)。
选错由「连接测试不通过」暴露,退回重选,而不是让没人审过的认证代码上线。
"""

from __future__ import annotations

from dano.shared.enums import AuthKind


class AuthAdapter:
    def __init__(self, kind: AuthKind, name: str, hints: list[str]) -> None:
        self.kind = kind
        self.name = name
        self.hints = hints  # 用于从鉴权说明里匹配的关键词


# 平台预置适配器(全公司共用)
_REGISTRY: list[AuthAdapter] = [
    AuthAdapter(AuthKind.SSO, "企业 SSO 单点登录", ["sso", "saml", "oauth", "单点", "cas"]),
    AuthAdapter(AuthKind.TOKEN, "API Token / Bearer", ["token", "bearer", "apikey", "api-key", "密钥"]),
]


def select_adapter(auth_hint: str) -> AuthAdapter:
    """按鉴权说明文本匹配选用。命中关键词最多者优先;无命中默认 Token。"""
    hint = (auth_hint or "").lower()
    best: AuthAdapter | None = None
    best_score = 0
    for adapter in _REGISTRY:
        score = sum(1 for kw in adapter.hints if kw in hint)
        if score > best_score:
            best, best_score = adapter, score
    return best or _REGISTRY[1]  # 默认 Token


def get_adapter(kind: AuthKind) -> AuthAdapter:
    for adapter in _REGISTRY:
        if adapter.kind == kind:
            return adapter
    raise KeyError(kind)
