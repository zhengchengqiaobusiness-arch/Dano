"""统一日志配置。

**关键**:代码里到处 `structlog.get_logger(__name__)`,但全仓**没有任何 `structlog.configure()`** ——
未配置时 structlog 行为不确定、在服务器/容器下常常**什么都看不到**。本模块提供幂等的 `configure_logging()`,
应用启动(gateway lifespan)与离线入口都调一次:带**时间戳 + 级别 + 上下文(run_id 等)+ 异常 traceback**,
统一渲染到 stdout,便于"每个节点可见、报错可快速定位"。级别取 DANO_LOG_LEVEL,默认 INFO。
"""
from __future__ import annotations

import logging
import os
import sys

import structlog

from dano.infra import run_logging

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    """配置 structlog(**幂等**)。无此调用 → 后台看不到任何记录。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    name = (level or os.environ.get("DANO_LOG_LEVEL") or "INFO").upper()
    lvl = getattr(logging, name, logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=lvl)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _persist_structlog,
            _render_console,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )
    _CONFIGURED = True


def _persist_structlog(_logger: object, _method: str, event_dict: dict) -> dict:
    try:
        run_logging.persist_structlog_event(dict(event_dict))
    except Exception:  # noqa: BLE001 - logging must never raise into business
        pass
    return event_dict


def _render_console(_logger: object, _method: str, event_dict: dict) -> str:
    level = str(event_dict.get("level") or "info").lower()
    threshold = (os.environ.get("DANO_LOG_LEVEL") or "INFO").lower()
    if run_logging.LEVEL_RANKS.get(level, 20) < run_logging.LEVEL_RANKS.get(threshold, 20):
        raise structlog.DropEvent
    record = {
        "timestamp": event_dict.get("timestamp"),
        "level": level,
        "event": event_dict.get("event"),
        "stage": event_dict.get("stage") or "",
        "status": event_dict.get("status") or "progress",
        "summary": event_dict.get("summary") or event_dict.get("event") or "",
        "details": {
            key: value
            for key, value in event_dict.items()
            if key not in {
                "timestamp", "level", "event", "stage", "status", "summary",
                "exception", "exc_info",
            }
        },
        "error": event_dict.get("error"),
        "next_action": event_dict.get("next_action") or "",
        "run_id": event_dict.get("run_id"),
    }
    stamp = str(event_dict.get("timestamp") or "")
    if " " in stamp:
        stamp = stamp.split(" ")[-1][:8]
    elif "T" in stamp:
        stamp = stamp.split("T", 1)[1][:8]
    label = run_logging._stage_label(record)
    extras = run_logging._headline_extras(record)
    line = f"{stamp} {level.upper():<5} [{label}] {record['summary']}"
    if extras:
        line = f"{line} | {extras}"
    return line
