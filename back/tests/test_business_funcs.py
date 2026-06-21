"""Phase 2 · 切片2.1:审计函数库 + safe_eval 函数白名单(纯离线)。"""
from __future__ import annotations

import pytest

from dano.shared import business_funcs as bf
from dano.shared.expr import ExprError, safe_eval


# ── 审计函数本身 ──
def test_days_between_inclusive():
    assert bf.days_between("2026-06-01", "2026-06-03") == 3


def test_business_days_skips_weekend():
    # 2026-06-01 周一 ~ 2026-06-07 周日 → 工作日 5(周六日扣掉)
    assert bf.business_days("2026-06-01", "2026-06-07") == 5


def test_business_days_skips_holidays():
    # 同上,扣一个工作日节假日(周三 06-03)→ 4
    assert bf.business_days("2026-06-01", "2026-06-07", ["2026-06-03"]) == 4


def test_sum_and_coalesce():
    assert bf.sum_([10, 20, 30]) == 60
    assert bf.sum_(1, 2, 3) == 6
    assert bf.coalesce(None, "", "x") == "x"
    assert bf.coalesce(None, "") is None


# ── safe_eval 调用审计函数(compute 表达式形态) ──
def test_safe_eval_calls_business_days():
    ctx = {"startDate": "2026-06-01", "endDate": "2026-06-05"}
    assert safe_eval("business_days(startDate, endDate)", ctx) == 5


def test_safe_eval_calls_with_kwarg_list():
    ctx = {"a": "2026-06-01", "b": "2026-06-07", "hol": ["2026-06-03"]}
    assert safe_eval("business_days(a, b, holidays=hol)", ctx) == 4


def test_safe_eval_precondition_with_compute():
    # 前置不变量形态:余额 >= 派生天数
    ctx = {"balance": 4, "s": "2026-06-01", "e": "2026-06-05"}
    assert safe_eval("balance >= business_days(s, e)", ctx) is False  # 4 < 5
    ctx["balance"] = 5
    assert safe_eval("balance >= business_days(s, e)", ctx) is True


# ── 白名单防越权 ──
def test_unknown_function_rejected():
    with pytest.raises(ExprError):
        safe_eval("__import__('os')", {})


def test_attribute_call_rejected():
    # 禁 obj.method() —— 防 response.pop() 之类
    with pytest.raises(ExprError):
        safe_eval("response.get('x')", {"response": {"x": 1}})


def test_existing_behavior_preserved():
    # 无函数调用的旧用法(断言/制度条件)行为不变
    assert safe_eval("response.code == 200", {"response": {"code": 200}}) is True
    assert safe_eval("amount > 1000 and has_invoice", {"amount": 2000, "has_invoice": True}) is True
    assert safe_eval("x != null", {"x": None}) is False
