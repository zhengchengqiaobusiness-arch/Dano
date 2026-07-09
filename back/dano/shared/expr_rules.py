"""Shared success-rule validation for Swagger and recorded capabilities."""

from __future__ import annotations

import re

_JS_ISMS = ("&&", "||", "===", "!==")
_CALL = re.compile(r"[A-Za-z_]\w*\s*\(")

EXPR_RULE_TEXT = (
    "判定表达式基于变量 response,**只能用**:属性点取(如 response.code)、下标(如 response['code'])、"
    "比较(== != > < >= <=)、and/or/not、字面量 null/true/false;"
    "**禁止**函数/方法调用(如 .get()、len())、禁止用 None(写 null)、禁止 JS 的 ===/&&/||。"
)


def expr_problem(expr: object, label: str) -> str | None:
    """Return why a success expression is invalid, or None when it is acceptable."""
    e = str(expr or "").strip()
    if not e:
        return f"{label} 为空"
    if e.lower() in ("true", "false", "1", "0"):
        return f"{label} 退化为常量({e}),必须是基于 response 的判断"
    if "response" not in e:
        return f"{label} 必须基于 response(如 response.code == 200 / response.total > 0)"
    for jsism in _JS_ISMS:
        if jsism in e:
            return f"{label} 含 JS 写法 {jsism!r},请改用 Python(and/or/not、!=)"
    if _CALL.search(e):
        return (f"{label} 不能用函数/方法调用(如 .get()/len());求值器只支持属性点取 response.code、"
                "下标 response['code']、比较、null/true/false 字面量")
    return None
