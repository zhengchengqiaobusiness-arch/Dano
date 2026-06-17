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


def safe_eval(expr: str, context: dict[str, Any]) -> Any:
    """在受限白名单下对 expr 求值。context 提供变量。

    支持 null 字面量(映射为 None)以贴近断言写法 "x != null"。
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"表达式语法错误: {expr}") from e
    return _eval(tree.body, context)


def _eval(node: ast.AST, ctx: dict[str, Any]) -> Any:  # noqa: C901
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
        base = _eval(node.value, ctx)
        if isinstance(base, dict):
            return base.get(node.attr)
        return getattr(base, node.attr, None)
    if isinstance(node, ast.Subscript):
        base = _eval(node.value, ctx)
        key = _eval(node.slice, ctx)
        try:
            return base[key]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, ctx) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(vals)
        return any(vals)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, ctx)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, ctx)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left, ctx), _eval(node.right, ctx))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator, ctx)
            if type(op) not in _CMP_OPS:
                raise ExprError(f"不支持的比较运算: {type(op).__name__}")
            if not _CMP_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, ctx) for e in node.elts]
    raise ExprError(f"不支持的表达式节点: {type(node).__name__}")
