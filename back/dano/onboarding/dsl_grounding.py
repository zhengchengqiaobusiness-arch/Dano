"""DSL v2 grounding 校验:声明式业务逻辑里每个事实都必须可追溯,LLM 不准发明。

铁律(对照泛化三红线):
- 调用/候选/回查的动作,必须是**已发布连接器**(不准臆造端点);
- 表达式(compute/branch/前置/不变量)只准用**已声明字段 + 已定义变量 + 审计函数**(不准发明标识/函数);
- 来源引用(field:/var:/step:/select:)必须指向真实存在的东西。

返回问题清单(空 = 全部 grounded)。draft_workflow 据此拒绝并把问题回给 pi 修;ground 不住的
构件应被去掉或降级,绝不让臆造逻辑进库。纯函数、无 I/O,可离线单测。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from dano.shared.asset_bodies import Invariant, WorkflowSkillBody, WorkflowStep
from dano.shared.business_funcs import FUNCS

_LITERALS = {"null", "true", "false"}
# 表达式里恒可用:回查响应体 / foreach 当前项 / 运行期注入的日历源(holidays,供 business_days)
_AMBIENT = {"response", "item", "holidays"}


def _iter_steps(steps: list[WorkflowStep]) -> Iterator[WorkflowStep]:
    """深度遍历所有节点(含 branch 分支臂、foreach 子步)。"""
    for s in steps:
        yield s
        if s.kind == "branch":
            yield from _iter_steps(s.then)
            yield from _iter_steps(s.otherwise)
        elif s.kind == "foreach":
            yield from _iter_steps(s.steps)


def _defined_vars(steps: list[WorkflowStep]) -> set[str]:
    """流程里定义出的变量名:compute 输出 + select 绑定 + foreach 当前项变量。"""
    out: set[str] = set()
    for s in _iter_steps(steps):
        if s.kind == "compute":
            out |= set(s.outputs)
        elif s.kind == "select" and s.bind:
            out.add(s.bind)
        elif s.kind == "foreach":
            out.add(s.as_var)
    return out


def _call_actions(steps: list[WorkflowStep]) -> set[str]:
    return {s.action for s in _iter_steps(steps) if s.kind == "call" and s.action}


def _expr_names_calls(expr: str) -> tuple[set[str], set[str]] | None:
    """抽表达式里的标识名 + 函数调用名;语法错返回 None。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    names: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            calls.add(node.func.id if isinstance(node.func, ast.Name) else "<非具名调用>")
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names - calls, calls          # 函数名不算自由标识


def check_grounding(
    body: WorkflowSkillBody, *, published_actions: set[str], funcs: set[str] | None = None
) -> list[str]:
    """校验一份 DSL v2 WORKFLOW 是否完全 grounded;返回问题清单(空=通过)。"""
    funcs = funcs or set(FUNCS)
    declared = set(body.user_fields) | set(body.required_fields)
    var_names = _defined_vars(body.steps)
    call_actions = _call_actions(body.steps)
    known = declared | var_names | _AMBIENT | _LITERALS
    issues: list[str] = []

    # ① 动作必须已发布(call / select 候选 / 回查)
    for s in _iter_steps(body.steps):
        if s.kind == "call" and s.action and s.action not in published_actions:
            issues.append(f"调用动作未发布: {s.action}")
        if s.kind == "select" and s.from_action and s.from_action not in published_actions:
            issues.append(f"select 候选来源未发布: {s.from_action}")
    for inv in [*body.preconditions, *body.invariants]:
        qa = (inv.evidence or {}).get("query_action") if inv.evidence else None
        if qa and qa not in published_actions:
            issues.append(f"回查动作未发布: {qa}")

    # ② 表达式只准用已声明字段/变量 + 审计函数
    def _check_expr(expr: str, where: str) -> None:
        r = _expr_names_calls(expr)
        if r is None:
            issues.append(f"{where}: 表达式语法错误 '{expr}'")
            return
        names, calls = r
        for n in sorted(names - known):
            issues.append(f"{where}: 未知标识 '{n}'(非已声明字段/变量)")
        for c in sorted(calls - funcs):
            issues.append(f"{where}: 未授权函数 '{c}'")

    for s in _iter_steps(body.steps):
        if s.kind == "compute":
            for var, expr in s.outputs.items():
                _check_expr(expr, f"compute:{var}")
        elif s.kind == "branch" and s.condition:
            _check_expr(s.condition, "branch.condition")
    for inv in body.preconditions:
        _check_expr(inv.check, "前置")
    for inv in body.invariants:
        _check_expr(inv.check, "不变量")

    # ③ 来源引用必须指向真实存在的东西
    def _check_source(src: str, where: str) -> None:
        if not isinstance(src, str):
            return
        kind, _, rest = src.partition(":")
        if kind == "const":
            return
        if kind == "field":
            if rest not in declared:
                issues.append(f"{where}: field 引用未声明字段 '{rest}'")
        elif kind in ("var", "select"):
            if rest not in var_names:
                issues.append(f"{where}: 引用未定义变量 '{rest}'")
        elif kind == "step":
            action = rest.split(".")[0]
            if action not in call_actions:
                issues.append(f"{where}: step 引用非本流程步骤 '{action}'")
        elif kind == "item":
            return
        else:
            issues.append(f"{where}: 未知来源前缀 '{kind}'")

    for s in _iter_steps(body.steps):
        if s.kind == "call":
            for tgt, src in s.inputs.items():
                _check_source(src, f"call:{s.action}.{tgt}")
        elif s.kind == "foreach" and s.over:
            _check_source(s.over, "foreach.over")

    return issues


def branch_ids(steps: list, prefix: str = "") -> list[str]:
    """静态枚举所有 branch 节点的稳定路径 id(与解释器 _exec_steps 的编号约定一致)。

    steps 可为 WorkflowStep 模型或 dict(model_dump)。供沙箱"每分支臂至少一例"覆盖检查。
    """
    out: list[str] = []
    for i, s in enumerate(steps):
        get = s.get if isinstance(s, dict) else (lambda k, d=None, _s=s: getattr(_s, k, d))
        kind = get("kind", "call") or "call"
        sid = f"{prefix}{i}"
        if kind == "branch":
            out.append(sid)
            out += branch_ids(get("then") or [], f"{sid}.t.")
            out += branch_ids(get("otherwise") or [], f"{sid}.f.")
        elif kind == "foreach":
            out += branch_ids(get("steps") or [], f"{sid}.s.")
    return out


def coverage_gaps(static_ids: list[str], observed_per_case: list[list]) -> list[dict]:
    """分支覆盖缺口:每个静态分支都须在用例集里**真假两臂都被走过**;否则报缺口。

    observed_per_case:每个用例跑出的 [[branch_id, taken_bool], ...](来自 outcome.audit['branches'])。
    返回 [{branch, missing:['then'|'otherwise', ...]}];空 = 全覆盖。
    """
    seen: dict[str, set[bool]] = {}
    for case in observed_per_case:
        for entry in case:
            bid, taken = entry[0], bool(entry[1])
            seen.setdefault(bid, set()).add(taken)
    gaps: list[dict] = []
    for bid in static_ids:
        got = seen.get(bid, set())
        missing = [arm for flag, arm in ((True, "then"), (False, "otherwise")) if flag not in got]
        if missing:
            gaps.append({"branch": bid, "missing": missing})
    return gaps


def collect_field_refs(steps: list[WorkflowStep]) -> set[str]:
    """流程里所有 field:X 引用的字段名(供 draft 自动并入 user_fields/required_fields)。"""
    out: set[str] = set()
    for s in _iter_steps(steps):
        srcs = list(s.inputs.values()) if s.kind == "call" else ([s.over] if s.kind == "foreach" else [])
        for v in srcs:
            if isinstance(v, str) and v.startswith("field:"):
                out.add(v[len("field:"):])
    return out
