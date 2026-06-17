"""漏洞校验:对生成代码做确定性静态扫描(goal 模式「漏洞校验」关卡的离线基线)。

与三模型评审里的『漏洞检测』分工:本扫描是**确定性、零成本**的硬基线(危险调用/命令注入/
硬编码密钥),稳定可复现;评审委员会的 security 角色做**语义级**深审(SSRF/越权/逻辑)。
两者都接进 GenerationLoop;任一发现就驳回重写。
"""

from __future__ import annotations

import ast
import re

# 可执行任意代码的内置(按名)
_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}
# 危险的属性调用 (模块, 方法)
_DANGEROUS_ATTR = {
    ("os", "system"), ("os", "popen"),
    ("subprocess", "call"), ("subprocess", "run"), ("subprocess", "Popen"),
    ("pickle", "loads"), ("pickle", "load"),
    ("marshal", "loads"),
}
# 源码内硬编码凭证(源码必须零凭证,运行期注入)
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-_\.]{16,}")
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key)\b\s*[:=]\s*['\"][^'\"]{6,}['\"]")


def scan_source(source: str) -> list[str]:
    """返回发现的高危问题(中文);空列表=通过。"""
    findings: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"源码语法错误,无法解析: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in _DANGEROUS_CALLS:
                findings.append(f"危险调用 {f.id}()(可执行任意代码)")
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                pair = (f.value.id, f.attr)
                if pair in _DANGEROUS_ATTR:
                    findings.append(f"危险调用 {pair[0]}.{pair[1]}()")
            for kw in getattr(node, "keywords", []):
                if (kw.arg == "shell" and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True):
                    findings.append("subprocess 使用 shell=True(命令注入风险)")

    if _BEARER_RE.search(source):
        findings.append("源码内疑似硬编码 Bearer 令牌(凭证必须运行期注入,不得入码)")
    if _SECRET_ASSIGN_RE.search(source):
        findings.append("源码内疑似硬编码密钥/口令(凭证必须运行期注入,不得入码)")

    # 去重保序
    seen: set[str] = set()
    return [f for f in findings if not (f in seen or seen.add(f))]
