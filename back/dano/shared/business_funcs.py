"""审计函数库(DSL v2 派生计算的唯一可用函数集)。

纪律:`compute`/前置/不变量 的表达式经 safe_eval 求值,**只准调用本注册表里的函数**——
不是让 LLM 写任意代码,而是从一组有限、可审计、已单测的函数里选。新增能力 = 在此加一个
纯函数 + 单测,再登记进 FUNCS;主流程不碰。全部纯函数、零副作用、零 I/O。
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta


# ── 日期 ──
def _to_date(x: object) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, str):
        return datetime.strptime(x.strip()[:10], "%Y-%m-%d").date()
    raise ValueError(f"不是日期: {x!r}")


def days_between(start: object, end: object) -> int:
    """起止日之间的**自然日**数(含首尾):2026-06-01~2026-06-03 → 3。"""
    a, b = _to_date(start), _to_date(end)
    return (b - a).days + 1


def business_days(start: object, end: object, holidays: object = None) -> int:
    """起止日之间的**工作日**数(含首尾,扣周末 + 给定节假日)。holidays=日期串列表,可空。"""
    a, b = _to_date(start), _to_date(end)
    if b < a:
        return 0
    hol: set[date] = set()
    for h in (holidays or []):
        try:
            hol.add(_to_date(h))
        except (ValueError, TypeError):
            continue
    n, d = 0, a
    while d <= b:
        if d.weekday() < 5 and d not in hol:
            n += 1
        d += timedelta(days=1)
    return n


def today() -> str:
    return date.today().isoformat()


# ── 数值 ──
def _num(x: object) -> float | int:
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, str):
        t = x.strip()
        return float(t) if ("." in t or "e" in t.lower()) else int(t)
    raise ValueError(f"不是数字: {x!r}")


def _seq(args: tuple) -> list:
    """支持 sum_(a,b,c) 与 sum_([a,b,c]) 两种写法。"""
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return list(args[0])
    return list(args)


def to_number(x: object) -> float | int:
    return _num(x)


def sum_(*args: object) -> float | int:
    return sum(_num(x) for x in _seq(args))


def min_(*args: object) -> object:
    s = _seq(args)
    return min(s) if s else None


def max_(*args: object) -> object:
    s = _seq(args)
    return max(s) if s else None


def round_(x: object, ndigits: object = 0) -> float | int:
    r = round(_num(x), int(ndigits))
    return int(r) if int(ndigits) == 0 else r


def abs_(x: object) -> float | int:
    return abs(_num(x))


def ceil_(x: object) -> int:
    return math.ceil(_num(x))


def floor_(x: object) -> int:
    return math.floor(_num(x))


# ── 其它 ──
def len_(x: object) -> int:
    return len(x) if hasattr(x, "__len__") else 0


def coalesce(*args: object) -> object:
    """返回第一个非空(非 None / 非空串)值;全空 → None。"""
    for a in args:
        if a not in (None, ""):
            return a
    return None


def contains(s: object, sub: object) -> bool:
    return str(sub) in str(s or "")


def lower_(s: object) -> str:
    return str(s or "").lower()


def upper_(s: object) -> str:
    return str(s or "").upper()


# 审计白名单:safe_eval 只准调用这里的函数(键 = 表达式里的函数名)
FUNCS = {
    "business_days": business_days, "days_between": days_between, "today": today,
    "to_number": to_number, "sum_": sum_, "min_": min_, "max_": max_, "round_": round_,
    "abs_": abs_, "ceil_": ceil_, "floor_": floor_, "len_": len_,
    "coalesce": coalesce, "contains": contains, "lower_": lower_, "upper_": upper_,
}
