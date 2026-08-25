"""两步登录的 challenge 令牌:明文只出现在响应与前端内存,库里只存 sha256。"""

from __future__ import annotations

import hashlib
import secrets


def new_challenge() -> tuple[str, str]:
    """生成 (明文令牌, sha256 十六进制)。"""
    token = secrets.token_urlsafe(32)
    return token, hash_challenge(token)


def hash_challenge(token: str) -> str:
    """令牌的 sha256 十六进制,作为存储主键。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()
