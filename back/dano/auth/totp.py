"""RFC 6238 TOTP(HMAC-SHA1, 30 秒步长, 6 位),标准库实现,与 Google Authenticator 兼容。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

_STEP = 30
_DIGITS = 6


def generate_secret(*, nbytes: int = 20) -> str:
    """生成无填充 Base32 密钥(默认 160 bit,与 RFC 推荐一致)。"""
    return base64.b32encode(secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    padded = (secret or "").strip().replace(" ", "").upper()
    padded += "=" * (-len(padded) % 8)
    return base64.b32decode(padded, casefold=True)


def _code_at(secret_bytes: bytes, counter: int, digits: int) -> str:
    digest = hmac.new(secret_bytes, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFF_FFFF
    return str(truncated % (10 ** digits)).zfill(digits)


def totp_code(secret: str, *, now: float | None = None, step: int = _STEP,
              digits: int = _DIGITS) -> str:
    """算出指定时刻的 TOTP 码。"""
    moment = time.time() if now is None else now
    return _code_at(_decode_secret(secret), int(moment) // step, digits)


def verify_code(secret: str, code: str, *, now: float | None = None, step: int = _STEP,
                digits: int = _DIGITS, window: int = 1,
                last_step: int | None = None) -> int | None:
    """校验 TOTP 码,命中返回时间步序号,否则 None。

    window=1 表示容忍前后各一个时间步;last_step 是上次成功使用过的时间步,
    小于等于它的一律拒绝 —— 同一个码不能用第二次。
    """
    candidate = (code or "").strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != digits:
        return None
    try:
        secret_bytes = _decode_secret(secret)
    except (ValueError, TypeError, base64.binascii.Error):
        return None
    moment = time.time() if now is None else now
    current = int(moment) // step
    for delta in range(-window, window + 1):
        counter = current + delta
        if last_step is not None and counter <= last_step:
            continue
        if hmac.compare_digest(_code_at(secret_bytes, counter, digits), candidate):
            return counter
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str = "Dano") -> str:
    """生成 Authenticator 扫码用的 otpauth:// URI。"""
    label = quote(f"{issuer}:{account}", safe="")
    params = urlencode({"secret": secret, "issuer": issuer,
                        "algorithm": "SHA1", "digits": _DIGITS, "period": _STEP})
    return f"otpauth://totp/{label}?{params}"
