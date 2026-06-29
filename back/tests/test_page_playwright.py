"""M3:PlaywrightPageDriver 对真实 chromium 的冒烟验证。

仅当 playwright 包 + chromium 浏览器都可用时运行;否则整文件 skip(套件保持绿)。
用本地临时 HTML 表单页(不联网)真打:open → fill(label)→ select → click(role)→ 成功标志可见。
证明真驱动的语义定位 / 字段绑定 / 二态判定与 FakePageDriver 行为一致。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

pytest.importorskip("playwright")  # 包缺失 → 跳过

from dano.execution.page.driver import PlaywrightPageDriver
from dano.execution.page.runtime import PageActionRuntime
from dano.shared.asset_bodies import PageAction, PageScriptBody
from dano.shared.enums import Outcome, RiskLevel

_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>报销</title></head><body>
<form>
  <label for="amt">金额</label><input id="amt" name="amount" type="text">
  <label for="cat">类别</label>
  <select id="cat" name="category"><option value="">--</option><option value="差旅">差旅</option></select>
  <button type="button" id="sub" onclick="document.getElementById('ok').style.display='block'">提交</button>
</form>
<div id="ok" style="display:none">保存成功</div>
</body></html>"""


async def _chromium_available() -> bool:
    try:
        d, _ = await PlaywrightPageDriver.launch(headless=True)
        await d.close()
        return True
    except Exception:  # noqa: BLE001 —— 浏览器未安装等
        return False


async def test_real_playwright_form_submit(tmp_path) -> None:  # noqa: ANN001
    if not await _chromium_available():
        pytest.skip("chromium 未安装(python -m playwright install chromium)")

    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    url = page.as_uri()

    script = PageScriptBody(
        actions=[
            PageAction(op="fill", locator="label=金额", value_from="field:amount", assert_visible=True),
            PageAction(op="select", locator="label=类别", value_from="field:category"),
            PageAction(op="submit", locator="role=button[name=提交]"),
        ],
        dom_fingerprint="",           # 空 → 跳过漂移校验(本冒烟聚焦定位/执行)
        start_url=url,                # 运行时据此导航到表单页
        action="submit_reimburse", success_marker="text=保存成功",
        required_fields=["amount", "category"], risk_level=RiskLevel.L2,
    )

    async def factory() -> PlaywrightPageDriver:
        d, _ = await PlaywrightPageDriver.launch(headless=True)
        return d

    rt = PageActionRuntime(factory)
    res = await rt.run(uuid4(), script, {"amount": "100", "category": "差旅"}, confirm=lambda f: True)

    assert res.outcome == Outcome.PASSED
    assert res.structured_output.get("submitted") is True
    assert res.structured_output.get("success_marker") is True
    assert res.evidence.response_body is not None
    # 真截图:data:image/png;base64,... (PlaywrightPageDriver.screenshot)
    assert any(s.startswith("data:image/png") for s in res.evidence.screenshots)


async def test_real_playwright_fingerprint_stable(tmp_path) -> None:  # noqa: ANN001
    """同一页两次取指纹应一致(结构哈希),为漂移检测提供稳定基线。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    d, _ = await PlaywrightPageDriver.launch(headless=True)
    try:
        await d.open(page.as_uri())
        fp1 = await d.fingerprint()
        fp2 = await d.fingerprint()
    finally:
        await d.close()
    assert fp1 == fp2 and fp1.startswith("fp:")


async def test_browser_pool_reuses_browser(tmp_path) -> None:  # noqa: ANN001
    """M6 R3:BrowserPool 跨运行复用同一浏览器;close 只关 context、浏览器常驻;shutdown 释放。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    from dano.execution.page.pool import BrowserPool

    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    url = page.as_uri()
    pool = BrowserPool(headless=True)
    try:
        d1 = await pool.new_driver()
        await d1.open(url)
        fp1 = await d1.fingerprint()
        browser1 = pool._browser
        await d1.close()                                  # 只关 context
        assert pool._browser is browser1 and pool._browser.is_connected()  # 浏览器仍活

        d2 = await pool.new_driver()
        await d2.open(url)
        fp2 = await d2.fingerprint()
        assert pool._browser is browser1                  # 复用同一浏览器实例
        await d2.close()
        assert fp1 == fp2 and fp1.startswith("fp:")
    finally:
        await pool.shutdown()
    assert pool._browser is None                          # shutdown 释放


async def test_real_scout_to_build_to_execute(tmp_path) -> None:  # noqa: ANN001
    """接入期全链路(无 LLM):真实侦察 → 确定性建体 → 真实回放执行 → PASSED。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    from dano.agent_tools.page_builder import build_page_script
    from dano.execution.page import to_recorded_steps

    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    url = page.as_uri()

    # 1. 真实侦察:抽出字段 + 提交按钮 + 指纹
    d, _ = await PlaywrightPageDriver.launch(headless=True)
    try:
        await d.open(url)
        dom = await d.scout()
        fp = await d.fingerprint()
    finally:
        await d.close()
    names = {f["name"] for f in dom["fields"]}
    assert {"amount", "category"} <= names                  # 两个表单字段被发现
    steps, submit = to_recorded_steps(dom)
    assert submit == "css=#sub"                             # 提交按钮被识别(scout 给 id 精确定位,比 role 更稳)

    # 2. 确定性建体
    script = build_page_script(steps, action="submit_reimburse", dom_fingerprint=fp,
                               start_url=url, success_marker="text=保存成功")
    assert script.risk_level.value == "L3"                  # 含提交步 → 写页面 L3

    # 3. 真实回放执行
    async def factory() -> PlaywrightPageDriver:
        drv, _ = await PlaywrightPageDriver.launch(headless=True)
        return drv

    res = await PageActionRuntime(factory).run(
        uuid4(), script, {"amount": "88", "category": "差旅"}, confirm=lambda f: True)
    assert res.outcome.value == "passed" and res.structured_output.get("submitted") is True
