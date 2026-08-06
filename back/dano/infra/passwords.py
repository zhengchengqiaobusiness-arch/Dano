"""租户后台登录密码:scrypt 哈希/校验(标准库,零外部依赖)。

格式:scrypt$N$r$p$salt$hash(与 Node 端 hash-password 脚本同构,可互验)。
N/r/p 参与哈希计算,升级参数后旧哈希仍可验证。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LENGTH = 64
_SALT_LENGTH = 16


def hash_password(password: str) -> str:
    """生成 scrypt$N$r$p$salt$hash 密码哈希。"""
    salt = secrets.token_bytes(_SALT_LENGTH)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LENGTH,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            _b64(salt),
            _b64(derived),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """常数时间校验;哈希格式非法或参数异常一律返回 False。"""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = (int(parts[1]), int(parts[2]), int(parts[3]))
        salt = _unb64(parts[4])
        expected = _unb64(parts[5])
    except ValueError:
        return False
    if n <= 0 or r <= 0 or p <= 0 or not salt or not expected:
        return False
    try:
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return len(derived) == len(expected) and hmac.compare_digest(derived, expected)


def _b64(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
