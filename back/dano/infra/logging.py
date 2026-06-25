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
            structlog.contextvars.merge_contextvars,      # 把 bind_contextvars 的 run_id/action 带进每条日志
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,          # exc_info=True → 打 traceback,便于定位报错
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True
