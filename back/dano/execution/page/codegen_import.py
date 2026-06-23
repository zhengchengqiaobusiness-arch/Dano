"""方式A:把 Playwright codegen 录制脚本(Python / JS)导入成页面步骤。

用户在自己浏览器 `playwright codegen <url>` 走一遍流程(含登录、多步、停在提交),把生成脚本贴进来。
本模块解析常见的**语义定位 + 动作**(绝不用坐标);填写值视为**样例** → 转成字段参数 + sample_inputs
(让 Skill 可换值复用,而非写死)。不认的行跳过(诚实容错)。产出交 page_builder 建体。

支持:page.goto;get_by_label/getByLabel、get_by_placeholder/getByPlaceholder、get_by_text/getByText、
get_by_role/getByRole(含 name)、get_by_test_id/getByTestId、locator;动作 fill / select_option(selectOption) /
set_input_files(setInputFiles) / click / check。提交按钮(role=button 且文本含提交线索)→ submit 步。
"""

from __future__ import annotations

import re

from dano.agent_tools.page_builder import RecordedStep, _std_key

_SUBMIT_HINTS = ("提交", "保存", "确定", "确认", "申请", "发起", "submit", "save", "ok", "confirm")
_LINE_RE = re.compile(r"^page\.(\w+)\((.*?)\)\.(\w+)\((.*)\)\s*;?\s*$")
_GOTO_RE = re.compile(r"""^page\.goto\(\s*['"]([^'"]+)['"]""")


def _q(s: str) -> str | None:
    m = re.search(r"""['"]([^'"]*)['"]""", s)
    return m.group(1) if m else None


def _role_name(args: str) -> tuple[str | None, str | None]:
    """get_by_role 参数:Python `"button", name="提交"` 或 JS `'button', { name: '提交' }`。"""
    role = _q(args)
    nm = re.search(r"""name\s*[:=]\s*['"]([^'"]*)['"]""", args)
    return role, (nm.group(1) if nm else None)


def _semantic_locator(method: str, args: str) -> str | None:
    m = method.lower().replace("_", "")
    if m == "getbylabel":
        v = _q(args); return f"label={v}" if v else None
    if m == "getbyplaceholder":
        v = _q(args); return f"placeholder={v}" if v else None
    if m == "getbytext":
        v = _q(args); return f"text={v}" if v else None
    if m == "getbytestid":
        v = _q(args); return f"css=[data-testid={v}]" if v else None
    if m == "getbyrole":
        role, nm = _role_name(args)
        if not role:
            return None
        return f"role={role}[name={nm}]" if nm else f"role={role}"
    if m == "locator":
        v = _q(args); return f"css={v}" if v else None
    return None


def _field_hint(locator: str) -> str:
    """从语义定位推一个可读字段名(再经 _std_key 对齐标准字段)。"""
    kind, _, rest = locator.partition("=")
    if kind == "role":
        m = re.search(r"\[name=(.*)\]", rest)
        return m.group(1) if m else rest
    if kind == "css":
        m = re.search(r"\[name=([^\]]+)\]", rest)
        return m.group(1) if m else rest.lstrip("#.")
    return rest   # label / placeholder / text 直接用文本


def parse_playwright_codegen(script: str) -> tuple[list[RecordedStep], str, dict]:
    """解析 codegen 脚本 → (步骤, start_url, sample_inputs)。sample_inputs 按标准字段 key 对齐 page_builder。"""
    steps: list[RecordedStep] = []
    start_url = ""
    samples: dict[str, str] = {}
    for raw in script.splitlines():
        line = raw.strip()
        if line.startswith("await "):
            line = line[6:].strip()
        if not line.startswith("page."):
            continue
        g = _GOTO_RE.match(line)
        if g:
            if not start_url:
                start_url = g.group(1)
            steps.append(RecordedStep(op="goto", value=g.group(1)))
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        loc_method, loc_args, act, act_args = m.groups()
        locator = _semantic_locator(loc_method, loc_args)
        if locator is None:
            continue
        a = act.lower().replace("_", "")
        if a == "fill":
            field = _field_hint(locator)
            steps.append(RecordedStep(op="fill", locator=locator, field=field))
            if field:
                samples[_std_key(field)] = _q(act_args) or ""
        elif a == "selectoption":
            field = _field_hint(locator)
            steps.append(RecordedStep(op="select", locator=locator, field=field))
            if field:
                samples[_std_key(field)] = _q(act_args) or ""
        elif a == "setinputfiles":
            steps.append(RecordedStep(op="upload", locator=locator, field=_field_hint(locator)))
        elif a in ("click", "check"):
            if locator.split("=", 1)[0] in ("label", "placeholder"):
                continue   # 对输入框的点击=聚焦噪声(fill 自带聚焦),跳过;保留按钮/链接/标签页点击
            is_submit = locator.startswith("role=button") and any(
                h in locator.lower() for h in _SUBMIT_HINTS)
            steps.append(RecordedStep(op="submit" if is_submit else "click", locator=locator))
        # press / hover / 其它动作:忽略
    return steps, start_url, samples
