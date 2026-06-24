"""Phase 3:few-shot 范例的「自洽」回归测试。

核心不变量:**教给模型的范例,必须能被我们自己的解析器/校验器接受**——
否则就是在教模型写会被后续闸门驳回的东西。这里用生产同一套抽取/校验函数验证范例。

范例一律用抽象结构占位(start/submit/mine、title/amount),不绑定任何具体业务/系统,跨企业通用。
"""

from __future__ import annotations

import json

from dano.generation.business_profiler import _EXAMPLE, _PROMPT, profile_business
from dano.generation.planner import _CONTRACT, _PLAN_EXAMPLE, _expr_problem, validate_plan
from dano.shared.prompt_utils import extract_json_array


# ─────────────────────────── business_profiler ───────────────────────────
def test_business_prompt_carries_example():
    out = _PROMPT.format(business="任意业务", lines="x", example=_EXAMPLE)
    assert "startProc" in out and "submit_proc" in out and "反例" in out


def test_business_example_parses_to_single_write_chain():
    """范例经我们自己的抽取器 → 必须是「单写操作合并多端点 + 查询各自独立」。"""
    ops = extract_json_array(_EXAMPLE)
    by_op = {o["op"]: o for o in ops}
    submit = by_op["submit_proc"]
    assert submit["write"] is True
    assert submit["endpoints"] == ["startProc", "saveForm", "submitProc"]   # 三接口合一
    assert [o["op"] for o in ops if o.get("write")] == ["submit_proc"]       # 全集仅一个写操作


async def test_business_example_survives_profile_business_filter():
    """把范例当模型输出喂回 profile_business 的真实过滤逻辑,结构应原样保留。"""
    actions = [{"name": n} for n in ("startProc", "saveForm", "submitProc", "myList", "procDetail")]

    async def fake(_prompt: str) -> str:
        return _EXAMPLE
    ops = await profile_business("任意业务", actions, spawn=fake)
    submit = next(o for o in ops if o["write"])
    assert submit["endpoints"] == ["startProc", "saveForm", "submitProc"]
    assert sum(1 for o in ops if o["write"]) == 1


# ─────────────────────────── planner ───────────────────────────
def test_planner_contract_carries_example():
    assert _PLAN_EXAMPLE in _CONTRACT and "startProc" in _CONTRACT


def test_plan_example_exprs_pass_validator():
    """范例里的 success_rule / assert_expr 必须通过 `_expr_problem`(教的=校验器接受的)。"""
    ex = json.loads(_PLAN_EXAMPLE)
    assert _expr_problem(ex["success_rule"], "success_rule") is None
    assert _expr_problem(ex["fact_check"]["assert_expr"], "assert_expr") is None


def test_plan_example_passes_validate_plan_against_matching_evidence():
    """构造与范例匹配的证据,范例应整体通过 validate_plan(端点/字段/表达式全合规)。"""
    ex = json.loads(_PLAN_EXAMPLE)
    evidence = {
        "actions": [
            {"endpoint": "/proc/start", "name": "startProc"},
            {"endpoint": "/proc/submit", "name": "submitProc"},
            {"endpoint": "/proc/mine", "name": "myList"},
        ],
        "form_fields": [{"key": "title"}, {"key": "amount"}],
    }
    # 范例用端点 name(startProc…)而非 path;validate_plan 同时接受 name/endpoint,故两者都登记
    for a in evidence["actions"]:
        a.setdefault("endpoint", a["name"])
    errs = validate_plan(ex, evidence)
    assert errs == [], errs
