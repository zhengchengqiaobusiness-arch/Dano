"""页面登录态(storageState)持久化:录制时真人登一次 → 存盘 → 回放/运行期复用。

不依赖系统怎么存 token —— storageState 是整个浏览器登录态(cookie+localStorage)的快照,
任何登录方式(cookie/localStorage/验证码/RSA)都覆盖。⚠ 含凭证,目录应 gitignore;会过期需重录刷新。
按 (tenant, subsystem) 分文件。
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DIR = Path(__file__).resolve().parents[3] / ".dano-sessions"   # back/.dano-sessions


def session_file(tenant: str, subsystem: str) -> Path:
    return _DIR / f"{tenant}__{subsystem.replace('/', '_')}.json"


def save_session(tenant: str, subsystem: str, state: dict | None) -> str | None:
    if not state:
        return None
    try:
        _DIR.mkdir(exist_ok=True)
        p = session_file(tenant, subsystem)
        p.write_text(json.dumps(state), encoding="utf-8")
        log.info("page_session.saved", tenant=tenant, subsystem=subsystem, path=str(p))
        return str(p)
    except Exception as e:  # noqa: BLE001
        log.warning("page_session.save_failed", error=str(e))
        return None


def session_path_if_exists(tenant: str, subsystem: str) -> str | None:
    """运行期取该子系统的登录态文件路径(Playwright storage_state 直接吃路径);没有返回 None。"""
    p = session_file(tenant, subsystem)
    return str(p) if p.exists() else None
