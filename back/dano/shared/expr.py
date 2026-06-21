"""安全表达式求值器(白名单 AST)。

共用于:
- 制度规则用例求值(流程4):如 "amount <= 1000 and has_invoice"
- 断言引擎(流程7/9,M2):如 "response.request_id != null"

只允许比较 / 布尔 / 成员 / 算术,变量从 context 取值。禁止函数调用、属性访问以外的危险节点,
避免 eval 注入。属性访问仅支持 context 内对象的点取(如 response.request_id)。
"""

from __future__ import annotations

import ast
import operator
from typing import Any

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}

_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


class ExprError(ValueError):
    """表达式非法或求值失败。"""


def _default_funcs() -> dict[str, Any]:
    """默认审计函数集(DSL v2 派生计算用)。延迟导入避免顶层循环依赖。"""
    from dano.shared.business_funcs import FUNCS
    return FUNCS


def safe_eval(expr: str, context: dict[str, Any], *, funcs: dict[str, Any] | None = None) -> Any:
    """在受限白名单下对 expr 求值。context 提供变量。

    支持 null 字面量(映射为 None)以贴近断言写法 "x != null"。
    funcs:允许调用的具名函数白名单(默认 business_funcs.FUNCS);函数调用只准调白名单内的,
    且 func 必须是裸名字(禁 obj.method() 等属性调用),防注入。
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"表达式语法错误: {expr}") from e
    return _eval(tree.body, context, _default_funcs() if funcs is None else funcs)


def _eval(node: ast.AST, ctx: dict[str, Any], funcs: dict[str, Any]) -> Any:  # noqa: C901
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id == "null":
            return None
        if node.id == "true":
            return True
        if node.id == "false":
            return False
        if node.id not in ctx:
            raise ExprError(f"未知变量: {node.id}")
        return ctx[node.id]
    if isinstance(node, ast.Attribute):
        # 仅支持对 context 内对象/字典的点取:response.request_id
        base = _eval(node.value, ctx, funcs)
        if isinstance(base, dict):
            return base.get(node.attr)
        return getattr(base, node.attr, None)
    if isinstance(node, ast.Subscript):
        base = _eval(node.value, ctx, funcs)
        key = _eval(node.slice, ctx, funcs)
        try:
            return base[key]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(node, ast.Call):
        # 只准调用白名单里的**裸名字**函数(禁 obj.method()、禁 */** 解包)
        if not isinstance(node.func, ast.Name):
            raise ExprError("只允许调用具名审计函数(禁属性调用)")
        fn = funcs.get(node.func.id)
        if fn is None:
            raise ExprError(f"未知/不允许的函数: {node.func.id}")
        if any(isinstance(a, ast.Starred) for a in node.args) or any(kw.arg is None for kw in node.keywords):
            raise ExprError("不支持 * / ** 解包")
        args = [_eval(a, ctx, funcs) for a in node.args]
        kwargs = {kw.arg: _eval(kw.value, ctx, funcs) for kw in node.keywords}
        try:
            return fn(*args, **kwargs)
        except ExprError:
            raise
        except Exception as e:  # noqa: BLE001 - 函数内部错统一包成表达式错
            raise ExprError(f"函数调用失败 {node.func.id}: {e}") from e
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, ctx, funcs) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(vals)
        return any(vals)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, ctx, funcs)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, ctx, funcs)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left, ctx, funcs), _eval(node.right, ctx, funcs))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx, funcs)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator, ctx, funcs)
            if type(op) not in _CMP_OPS:
                raise ExprError(f"不支持的比较运算: {type(op).__name__}")
            if not _CMP_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, ctx, funcs) for e in node.elts]
    raise ExprError(f"不支持的表达式节点: {type(node).__name__}")
