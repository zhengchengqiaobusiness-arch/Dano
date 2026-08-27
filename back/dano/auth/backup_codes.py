"""TOTP 备用码:10 位随机字符(约 50 bit 熵),一次性核销。

用 sha256 而非 scrypt:备用码是高熵随机串,不存在字典攻击面,
慢哈希只会让每次登录多付 10 倍 scrypt 成本。密码用慢哈希、高熵凭证用快哈希。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# 去掉 O/I/L/0/1 等易混字符,便于用户手抄
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_LENGTH = 10
_GROUP = 5


def generate_codes(count: int = 10) -> list[str]:
    """生成 count 个互不相同的备用码,形如 A7K2M-9PQR3。"""
    codes: list[str] = []
    seen: set[str] = set()
    while len(codes) < count:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
        if raw in seen:
            continue
        seen.add(raw)
        codes.append(f"{raw[:_GROUP]}-{raw[_GROUP:]}")
    return codes


def normalize(code: str) -> str:
    """去掉分隔符与空格并转大写,便于用户随意输入。"""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def hash_code(code: str) -> str:
    """归一化后的 sha256 十六进制,入库只存这个。"""
    return hashlib.sha256(normalize(code).encode("ascii")).hexdigest()


def consume(code: str, hashes: list[str]) -> list[str] | None:
    """命中则返回去掉该码后的剩余哈希列表;未命中返回 None。"""
    target = hash_code(code)
    for index, stored in enumerate(hashes):
        if hmac.compare_digest(stored, target):
            return hashes[:index] + hashes[index + 1:]
    return None
