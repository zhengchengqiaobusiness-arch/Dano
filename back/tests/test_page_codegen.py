"""方式A:Playwright codegen 录制脚本解析(离线,Python + JS 两种输出)。"""
from __future__ import annotations

from dano.execution.page.codegen_import import parse_playwright_codegen

_PY = """
def run(playwright):
    page.goto("https://oa.example.com/reimburse/new")
    page.get_by_label("金额").click()
    page.get_by_label("金额").fill("100")
    page.get_by_label("类别").select_option("差旅")
    page.get_by_role("button", name="提交").click()
"""

_JS = """
await page.goto('https://oa.example.com/reimburse/new');
await page.getByLabel('金额').fill('100');
await page.getByLabel('类别').selectOption('差旅');
await page.getByRole('button', { name: '提交' }).click();
"""


def _check(steps, start_url, samples):
    assert start_url == "https://oa.example.com/reimburse/new"
    ops = [(s.op, s.locator) for s in steps]
    assert ("goto", None) in ops
    assert ("fill", "label=金额") in ops
    assert ("select", "label=类别") in ops
    assert ("submit", "role=button[name=提交]") in ops          # 提交按钮识别为 submit 步
    assert not any(s.op == "click" for s in steps)              # 输入框聚焦点击噪声已滤除
    assert samples.get("amount") == "100"                       # 金额→标准字段 amount,样例对齐
    assert samples.get("类别") == "差旅"                          # 未命中标准字段 → 原样 key,仍与建体一致


def test_parse_python_codegen() -> None:
    _check(*parse_playwright_codegen(_PY))


def test_parse_js_codegen() -> None:
    _check(*parse_playwright_codegen(_JS))


def test_parse_garbage_is_empty() -> None:
    steps, url, samples = parse_playwright_codegen("这不是脚本\nrandom text\nconsole.log(1)")
    assert steps == [] and url == "" and samples == {}


async def test_imported_steps_build_and_run() -> None:
    """解析 → page_builder 建体 → FakePageDriver 真跑 → PASSED(证明导入产物可执行)。"""
    from uuid import uuid4

    from dano.agent_tools.page_builder import build_page_script
    from dano.execution.page import FakePageDriver, PageActionRuntime
    from dano.shared.enums import Outcome

    steps, start_url, samples = parse_playwright_codegen(_PY)
    body = build_page_script(steps, action="submit_reimburse", dom_fingerprint="",
                             start_url=start_url, success_marker="text=保存成功")
    assert body.risk_level.value == "L3"            # 含提交步 → 写页面
    fake = FakePageDriver(fingerprint="x")
    res = await PageActionRuntime(lambda: fake).run(uuid4(), body, samples, confirm=lambda f: True)
    assert res.outcome == Outcome.PASSED
    assert ("fill", "label=金额", "100") in fake.ops
