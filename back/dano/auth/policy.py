"""后台登录密码强度策略:长度 + 弱密码黑名单 + 与身份标识雷同检查。

按 NIST SP 800-63B 现行建议,只卡长度与已知弱口令,不强制大小写/符号组合
(组合规则实际会驱使用户选 Passw0rd! 这类可猜密码)。
"""

from __future__ import annotations

# 覆盖公开泄露榜单里最常见的一批;命中即拒,不做模糊匹配。
_WEAK: frozenset[str] = frozenset({
    "password", "passw0rd", "password1", "password12", "password123",
    "password1234", "12345678", "123456789", "1234567890", "123456789012",
    "qwertyuiop", "administrator", "letmein", "welcome", "iloveyou",
    "abc123456", "adminadmin", "changeme", "dano123456", "1qaz2wsx3edc",
})


class PasswordPolicyError(ValueError):
    """密码不符合策略;str(e) 直接作为面向用户的提示。"""


def validate_password(
    password: str,
    *,
    username: str = "",
    tenant: str = "",
    min_length: int = 12,
) -> None:
    """校验密码强度,不合规抛 PasswordPolicyError。"""
    if len(password) < min_length:
        raise PasswordPolicyError(f"密码至少 {min_length} 位")
    folded = password.casefold()
    if folded in _WEAK:
        raise PasswordPolicyError("密码过于常见,请换一个")
    for label, value in (("用户名", username), ("租户名", tenant)):
        candidate = (value or "").strip().casefold()
        if candidate and candidate in folded:
            raise PasswordPolicyError(f"密码不能包含{label}")
