"""页面登录态(storageState)持久化:录制时真人登一次 → 存盘 → 回放/运行期复用。

主文件始终保持 Playwright ``storage_state`` 兼容(cookie+localStorage)；Playwright
未覆盖的 sessionStorage 单独保存在 sidecar，并由 :func:`load_session_state`
合并回录制器使用的扩展状态。⚠ 含凭证,目录应 gitignore;会过期需重录刷新。
按 (tenant, subsystem) 分文件。
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DIR = Path(__file__).resolve().parents[3] / ".dano-sessions"   # back/.dano-sessions
SESSION_STORAGE_STATE_KEY = "_dano_session_storage"


def session_file(tenant: str, subsystem: str) -> Path:
    return _DIR / f"{tenant}__{subsystem.replace('/', '_')}.json"


def session_storage_file(tenant: str, subsystem: str) -> Path:
    """扩展 sessionStorage sidecar；主文件仍可直接交给 Playwright。"""
    return session_file(tenant, subsystem).with_suffix(".session-storage.json")


def save_session(tenant: str, subsystem: str, state: dict | None) -> str | None:
    if not state:
        return None
    try:
        _DIR.mkdir(exist_ok=True)
        p = session_file(tenant, subsystem)
        # 自定义根键不是 Playwright storage_state schema 的一部分，不能写进
        # 运行期直接传给 BrowserContext 的主文件。
        playwright_state = dict(state)
        session_storage = playwright_state.pop(SESSION_STORAGE_STATE_KEY, None)
        p.write_text(json.dumps(playwright_state), encoding="utf-8")
        sidecar = session_storage_file(tenant, subsystem)
        if session_storage:
            sidecar.write_text(json.dumps(session_storage), encoding="utf-8")
        elif sidecar.exists():
            # 新快照明确没有 sessionStorage 时删除旧 sidecar，避免复用过期 token。
            sidecar.unlink()
        log.info("page_session.saved", tenant=tenant, subsystem=subsystem, path=str(p))
        return str(p)
    except Exception as e:  # noqa: BLE001
        log.warning("page_session.save_failed", error=str(e))
        return None


def load_session_state(tenant: str, subsystem: str) -> dict | None:
    """读取主 Playwright 状态，并在存在时合并 sessionStorage sidecar。

    旧安装只有主文件时仍返回原结构；sidecar 损坏不会让可用的 cookie /
    localStorage 登录态一并失效。
    """
    p = session_file(tenant, subsystem)
    if not p.exists():
        return None
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("page session root must be an object")
    except Exception as e:  # noqa: BLE001
        log.warning("page_session.load_failed", error=str(e), path=str(p))
        return None

    sidecar = session_storage_file(tenant, subsystem)
    if sidecar.exists():
        try:
            session_storage = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(session_storage, dict) and session_storage:
                state[SESSION_STORAGE_STATE_KEY] = session_storage
        except Exception as e:  # noqa: BLE001
            log.warning("page_session.session_storage_load_failed", error=str(e), path=str(sidecar))
    return state


def session_path_if_exists(tenant: str, subsystem: str) -> str | None:
    """运行期取该子系统的登录态文件路径(Playwright storage_state 直接吃路径);没有返回 None。"""
    p = session_file(tenant, subsystem)
    return str(p) if p.exists() else None


# ── 导出目录:页面配一次 → 持久化 → 自动发布(录完)复用同一目录,二者一致 ──
_EXPORT_CONF = _DIR / ".export-dir"
_EXPORT_HISTORY_CONF = _DIR / ".export-dirs"


def save_export_dir(path: str) -> None:
    """记住页面配置的导出目录,供自动发布复用(与手动导出落同一处)。"""
    try:
        _DIR.mkdir(exist_ok=True)
        cleaned = path.strip()
        if not cleaned:
            return
        _EXPORT_CONF.write_text(cleaned, encoding="utf-8")
        old = []
        if _EXPORT_HISTORY_CONF.exists():
            old = [x.strip() for x in _EXPORT_HISTORY_CONF.read_text(encoding="utf-8").splitlines() if x.strip()]
        merged = []
        for item in [cleaned, *old]:
            if item not in merged:
                merged.append(item)
        _EXPORT_HISTORY_CONF.write_text("\n".join(merged[:20]), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("export_dir.save_failed", error=str(e))


def get_export_dir(default: str) -> str:
    """导出目录优先级:页面配过的(持久化)> DANO_EXPORT_DIR 环境变量 > 传入默认。"""
    import os
    try:
        if _EXPORT_CONF.exists():
            v = _EXPORT_CONF.read_text(encoding="utf-8").strip()
            if v:
                return v
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("DANO_EXPORT_DIR") or default


def get_export_dirs(default: str) -> list[str]:
    """返回需要清理的所有已知导出目录:当前目录、历史目录、环境变量、默认目录。"""
    import os
    out: list[str] = []
    for item in [get_export_dir(default), os.environ.get("DANO_EXPORT_DIR"), default]:
        if item and item not in out:
            out.append(item)
    try:
        if _EXPORT_HISTORY_CONF.exists():
            for item in _EXPORT_HISTORY_CONF.read_text(encoding="utf-8").splitlines():
                item = item.strip()
                if item and item not in out:
                    out.append(item)
    except Exception:  # noqa: BLE001
        pass
    return out
