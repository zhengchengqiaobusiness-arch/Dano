"""httpx 客户端共用配置。"""

from __future__ import annotations

from dano.config import get_settings


def tls_verify() -> bool:
    """是否校验 TLS 证书。DANO_INSECURE_TLS=1 时关闭(仅自签/测试环境用,生产勿开)。"""
    return not get_settings().insecure_tls
