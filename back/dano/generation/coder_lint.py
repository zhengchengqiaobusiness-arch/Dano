"""编码契约静态校验(goal 模式):对生成的适配器源码做**确定性、跨系统通用**的契约检查。

与 [vuln.py] 分工:vuln=安全(危险调用 / 硬编码密钥);本模块=**可执行契约**——
入口签名、不把异常吞进返回值、用到的库要 import。三类都是「任何企业、任何系统都成立」的硬错;
命中即把**具体原因**回灌编码器作下一轮修复依据,把原先散在提示词里的祈使规则变成机器强制 + 精确反馈。

**刻意不做**会因系统而异的检查——典型如「标识不得拼进 URL 路径」:RESTful 系统 /users/{id} 恰恰正确,
硬判会误驳正确代码、空烧生成预算。那类留在提示词里按证据 grounding。本模块只判普适硬错,**零误报优先**。

入口契约取自 runner 引导(execution/adapter/runner.py):子进程 `_fn(inputs, creds)` **同步调用、不 await**
→ 故 run 必须是 def(非 async)且至少 2 参数。
"""

from __future__ import annotations

import ast

# codegen 常用且最易漏 import 的库;只查这些,避免把局部变量误判成"没 import 的库"(零误报优先)。
_KNOWN_LIBS = {
    "httpx", "requests", "json", "re", "base64", "hashlib", "hmac",
    "time", "datetime", "uuid", "urllib", "math", "random", "decimal",
}
# 适配器内部哨兵键:出现在返回 dict 里 = 把异常吞进了返回值(提示词明令禁止;真实 API 不会有此字段)。
_SWALLOW_KEYS = {"_adapter_error", "_error"}


def scan_source(source: str) -> list[str]:
    """返回违反编码契约的问题(中文);空列表 = 通过。语法错误交给 vuln,这里静默返回 []。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []                                   # 语法错由 vuln.scan_source 报,避免重复
    findings: list[str] = []
    findings += _check_entry(tree)
    findings += _check_swallow(tree)
    findings += _check_missing_imports(tree)
    seen: set[str] = set()                          # 去重保序
    return [f for f in findings if not (f in seen or seen.add(f))]


def _check_entry(tree: ast.Module) -> list[str]:
    """必须有模块级 def run(inputs, creds)(>=2 参数、非 async)——否则 runner 调不起来。"""
    sync_runs, async_runs = [], []
    for node in tree.body:                          # 只看模块级,内层同名 run 不算入口
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            sync_runs.append(node)
        elif isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
            async_runs.append(node)
    if not sync_runs and not async_runs:
        return ["缺入口函数:必须有模块级 def run(inputs, creds) -> dict"]
    if not sync_runs and async_runs:
        return ["入口函数 run 不能是 async —— runner 同步调用、不会 await(请改成 def run(inputs, creds))"]
    fn = sync_runs[0]
    nargs = len(fn.args.posonlyargs) + len(fn.args.args)
    if nargs < 2:
        return ["入口函数 run 必须接收两个参数 (inputs, creds);凭证只从 creds 取"]
    return []


def _check_swallow(tree: ast.Module) -> list[str]:
    """禁止 return 一个带 _adapter_error/_error 哨兵键的 dict(把异常吞进返回值)。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and k.value in _SWALLOW_KEYS:
                    return ["禁止把异常吞进返回值(return {'_adapter_error': ...}):"
                            "要么让异常真实抛出,要么返回目标系统的真实响应——吞错会被判失败"]
    return []


def _check_missing_imports(tree: ast.Module) -> list[str]:
    """已知库以 `lib.xxx` 使用,但全模块既没 import 也没绑定该名 → 运行期必 NameError。"""
    bound = _bound_names(tree)
    used: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and isinstance(node.value.ctx, ast.Load)):
            root = node.value.id
            if root in _KNOWN_LIBS and root not in bound:
                used.add(root)
    return [f"用到了 {lib} 但没 import {lib}(顶部加 `import {lib}`)" for lib in sorted(used)]


def _bound_names(tree: ast.Module) -> set[str]:
    """收集模块内**任何方式**绑定到的名字(import / 赋值 / 函数/类名 / 形参 / for / with as / 推导 / except as)。

    偏向「多收」——宁可漏报一个真缺的 import,也绝不把已绑定名误判成缺 import(零误报优先)。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names
