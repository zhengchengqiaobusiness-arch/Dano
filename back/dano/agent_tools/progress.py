"""每 run 的进度事件分发(进程内):pi 工具回调 / 接入各步 → 推给该 run 的订阅者(接入向导 job)。

与 logging 互补:log 落后端日志便于事后排查;progress 事件推前端**实时展示流程**。
按 run_id 注册/注销;无订阅者时 emit 静默丢弃(绝不影响主流程)。
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

log = structlog.get_logger(__name__)

_SINKS: dict[str, Callable[[dict], None]] = {}


def register(run_id: str, sink: Callable[[dict], None]) -> None:
    _SINKS[run_id] = sink


def unregister(run_id: str) -> None:
    _SINKS.pop(run_id, None)


def emit(run_id: str, event: dict) -> None:
    """把一条进度事件推给该 run 的订阅者(若有)。绝不抛错。"""
    sink = _SINKS.get(run_id)
    if sink is None:
        return
    try:
        sink(event)
    except Exception as e:  # noqa: BLE001 - 进度回调不应拖垮主流程
        log.warning("progress.sink_error", run_id=run_id, error=str(e))
