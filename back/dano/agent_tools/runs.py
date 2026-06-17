"""活跃 run 的临时令牌注册(进程内)。

工具服务据此校验:只有当前活跃 run 的令牌能调 /_agent/tools/*。run 结束即注销。
"""

from __future__ import annotations

_ACTIVE: dict[str, str] = {}     # run_id -> token


def register(run_id: str, token: str) -> None:
    _ACTIVE[run_id] = token


def is_valid(run_id: str, token: str | None) -> bool:
    return bool(token) and _ACTIVE.get(run_id) == token


def unregister(run_id: str) -> None:
    _ACTIVE.pop(run_id, None)
