"""通用事实核查引擎(流程9·一等公民):回查确认副作用真的生效,不信接口字面成功。

谁都能声明 FactCheckSpec(连接器/复合流程/adapter),由本引擎统一执行:
按模板渲染回查端点+参数 → 调用 → 对响应跑 assert_expr;副作用多为异步,故轮询若干次再判失败,
避免「其实成功了只是查太早」的假阴性。这是把请假那次手写核查抽象成系统能力。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import structlog

from dano.shared.asset_bodies import FactCheckSpec
from dano.shared.expr import safe_eval

log = structlog.get_logger(__name__)

# call(method, path, body|None) -> (http_status, response_body)
Caller = Callable[[str, str, dict | None], Awaitable[tuple[int, dict[str, Any]]]]

_PLACE_RE = re.compile(r"\{(\w+)\}")


def _render(template: str, context: dict[str, Any]) -> str:
    """把 '{name}' 用 context[name] 替换(缺失替空,不抛错)。"""
    return _PLACE_RE.sub(lambda m: str(context.get(m.group(1), "")), template)


async def run_fact_check(spec: FactCheckSpec, *, context: dict[str, Any],
                         call: Caller) -> tuple[bool, dict[str, Any]]:
    """执行一次事实核查。context 供端点/参数模板与表达式取值(常为入参+执行输出的合并)。

    返回 (是否确认生效, 证据)。轮询 spec.retries 次,每次间隔 spec.backoff_s。
    """
    method = (spec.method or "GET").upper()
    last: dict[str, Any] = {}
    ok = False
    attempts = 0
    for attempt in range(max(1, spec.retries)):
        attempts = attempt + 1
        path = _render(spec.endpoint, context)
        params = {k: _render(v, context) for k, v in (spec.params_template or {}).items()}
        if method == "GET":
            if params:
                path = path + ("&" if "?" in path else "?") + urlencode(params)
            http, body = await call(method, path, None)
        else:
            http, body = await call(method, path, params or None)
        last = {"http": http, "response": body}
        try:
            ok = bool(safe_eval(spec.assert_expr, {"response": body, "http": http}))
        except Exception as e:  # noqa: BLE001
            last["eval_error"] = str(e)
            ok = False
        if ok:
            break
        if attempt < spec.retries - 1:
            await asyncio.sleep(spec.backoff_s)
    evidence = {"assert_expr": spec.assert_expr, "attempts": attempts, "passed": ok, **last}
    log.info("fact_check.run", assert_expr=spec.assert_expr, attempts=attempts, passed=ok)
    return ok, evidence
