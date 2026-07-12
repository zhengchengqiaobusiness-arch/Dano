"""方式B 录制核心:注入式语义动作捕获(真浏览器,缺浏览器自动 skip)。

不测 WebSocket/截屏(实时管线,手动/前端验);测最有价值、可自动化的部分——
用户操作(经真实 DOM 事件)是否被转成正确的语义步骤 + 样例值。
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright")

from dano.execution.page.driver import PlaywrightPageDriver
from dano.execution.page.recorder import RecordSession, _RECORDER_JS


def test_recorder_key_safety_policy() -> None:
    from dano.execution.page.recorder import _safe_recorder_key

    for key in ["Escape", "Delete", "Shift+Tab", "Control+A", "Meta+Z", "Control+Enter", "Control+Backspace"]:
        assert _safe_recorder_key(key)
    for key in ["Alt+F4", "F5", "F12", "Control+R", "Control+W", "Alt+Delete", "Control+Shift+I"]:
        assert not _safe_recorder_key(key)


def test_same_endpoint_responses_attach_by_request_identity_index() -> None:
    sess = RecordSession()
    first = sess._record_all("GET", "https://example.test/api/items?id=1")
    second = sess._record_all("GET", "https://example.test/api/items?id=1")

    # 较早请求后返回时，不能因为“最近一条”策略贴到第二个请求上。
    assert sess._attach_response(
        url="https://example.test/api/items?id=1", method="GET",
        response_json={"request": "first"}, status=200,
        content_type="application/json", request_index=first,
    )
    assert sess._attach_response(
        url="https://example.test/api/items?id=1", method="GET",
        response_json={"request": "second"}, status=200,
        content_type="application/json", request_index=second,
    )

    assert sess.all_requests[first]["response_json"] == {"request": "first"}
    assert sess.all_requests[second]["response_json"] == {"request": "second"}


def test_recorded_page_enum_options_attach_popup_pick_to_previous_field() -> None:
    sess = RecordSession()
    sess.steps = [
        {"op": "click", "locator": "label=类型", "field": "类型", "value": ""},
        {"op": "pick", "locator": "text=病假", "value": "病假", "options": ["病假", "事假", "婚假"]},
    ]

    enums = sess.recorded_page_enum_options()

    assert enums["类型"]["selected"] == "病假"
    assert enums["类型"]["options"] == ["病假", "事假", "婚假"]


def test_popup_pick_preserves_options_until_selected_value_is_recorded() -> None:
    """点击弹层项时不得清空刚抓到的候选，未知 value 也不能伪装成 label。"""
    assert "pollPick(activeTrigger, false)" in _RECORDER_JS
    assert "pollPick(trig, true)" in _RECORDER_JS
    assert "if (resetOptions) lastPickOptions = []" in _RECORDER_JS
    assert "return label;" not in _RECORDER_JS


_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form>
  <label for="amt">金额</label><input id="amt" name="amount" type="text">
  <label for="cat">类别</label>
  <select id="cat" name="category"><option value="">--</option><option value="差旅">差旅</option></select>
  <button type="button" id="sub" onclick="document.getElementById('ok').style.display='block'">提交</button>
</form><div id="ok" style="display:none">保存成功</div></body></html>"""


async def _chromium_available() -> bool:
    try:
        d, _ = await PlaywrightPageDriver.launch(headless=True)
        await d.close()
        return True
    except Exception:  # noqa: BLE001
        return False


async def test_record_session_captures_semantic_steps(tmp_path) -> None:  # noqa: ANN001
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")

    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        # 模拟用户在录制页里操作(真实 DOM 事件 → 注入录制器捕获语义步骤)
        await sess.page.get_by_label("金额").fill("100")
        await sess.page.get_by_label("类别").select_option("差旅")
        await sess.page.get_by_role("button", name="提交").click()
        await sess.page.wait_for_timeout(300)          # 等 expose_binding 回传完成

        steps, samples = sess.recorded_steps()
        ops = [(s["op"], s["locator"]) for s in steps]
        assert ("fill", "label=金额") in ops
        assert ("select", "label=类别") in ops
        assert ("submit", "role=button[name=提交]") in ops
        assert samples.get("amount") == "100"          # 金额→标准字段 amount,值作样例
        assert samples.get("类别") == "差旅"
    finally:
        await sess.stop()


_BIG = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<input id="big" name="amount" style="position:fixed;top:0;left:0;width:1280px;height:300px">
<button style="position:fixed;top:400px;left:0;width:1280px;height:200px">提交</button>
</body></html>"""


async def test_dispatch_input_relays_and_captures(tmp_path) -> None:  # noqa: ANN001
    """输入回传全链路:归一坐标点击 focus → 键盘打字 fill → 点提交 → 语义步骤被捕获。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "big.html"
    page.write_text(_BIG, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.2})    # 命中大输入框
        await sess.dispatch_input({"kind": "text", "text": "差旅费100"})       # 含中文 CJK,验 insert_text
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.65})   # 命中提交按钮
        await sess.page.wait_for_timeout(300)
        steps, samples = sess.recorded_steps()
    finally:
        await sess.stop()
    ops = [s["op"] for s in steps]
    assert "fill" in ops and "submit" in ops
    assert samples.get("amount") == "差旅费100"          # 中文经回传被正确填入并捕获


_LOGIN = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form>
  <input id="u" name="username" placeholder="账号">
  <input id="p" name="password" type="password" placeholder="密码">
  <button type="button">登录</button>
</form></body></html>"""


async def test_password_never_recorded_and_reset(tmp_path) -> None:  # noqa: ANN001
    """安全:密码框(type=password)绝不被录;reset 清空登录步骤。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "login.html"
    page.write_text(_LOGIN, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        await sess.page.get_by_placeholder("账号").fill("admin")
        await sess.page.get_by_placeholder("密码").fill("secret123")
        await sess.page.wait_for_timeout(300)
        steps, samples = sess.recorded_steps()
        # 账号被录,密码与其值绝不出现
        assert any((s.get("field") or "") == "账号" for s in steps)
        assert not any("password" in (s["locator"] or "") or (s.get("field") or "") == "password" for s in steps)
        assert "secret123" not in str(samples)
        # reset 清空(登录后只录业务)
        sess.reset()
        assert sess.recorded_steps()[0] == []
    finally:
        await sess.stop()


_CARDS = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="card" style="cursor:pointer;position:fixed;top:0;left:0;width:1280px;height:220px">出差申请</div>
<div class="el-menu-item" style="cursor:pointer;position:fixed;top:300px;left:0;width:1280px;height:200px">我的</div>
</body></html>"""


async def test_captures_card_and_menu_clicks(tmp_path) -> None:  # noqa: ANN001
    """卡片 <div>(cursor:pointer)与菜单 <li>(el-menu-item)的点击也要捕获(按可见文本定位)。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "cards.html"
    page.write_text(_CARDS, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.1})   # 卡片 出差申请
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.4})   # 菜单 我的
        await sess.page.wait_for_timeout(300)
        steps, _ = sess.recorded_steps()
    finally:
        await sess.stop()
    locs = [s["locator"] for s in steps]
    assert "text=出差申请" in locs
    assert "text=我的" in locs


_GENERIC = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div role="button" aria-label="发起出差" style="cursor:pointer;position:fixed;top:0;left:0;width:1280px;height:150px">x</div>
<a href="#d" style="position:fixed;top:200px;left:0;width:1280px;height:100px">详情</a>
<input aria-label="采购金额" style="position:fixed;top:350px;left:0;width:1280px;height:100px">
<div data-testid="reimburse-card" style="cursor:pointer;position:fixed;top:500px;left:0;width:1280px;height:100px">报销</div>
</body></html>"""


async def test_general_semantics_framework_agnostic(tmp_path) -> None:  # noqa: ANN001
    """泛化:不靠任何框架 class —— ARIA role+aria-label 自定义按钮 / 链接 / aria-label 输入 / data-testid 卡片。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "generic.html"
    page.write_text(_GENERIC, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.05})   # role=button div(发起→submit)
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.30})   # 链接 详情
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.50})   # aria-label 输入框
        await sess.dispatch_input({"kind": "text", "text": "888"})
        await sess.dispatch_input({"kind": "click", "nx": 0.5, "ny": 0.65})   # data-testid 卡片
        await sess.page.wait_for_timeout(300)
        steps, samples = sess.recorded_steps()
    finally:
        await sess.stop()
    pairs = [(s["op"], s["locator"]) for s in steps]
    assert ("submit", "role=button[name=发起出差]") in pairs       # 自定义 ARIA 按钮 + 提交语义
    assert ("click", "role=link[name=详情]") in pairs               # 隐式 link role
    assert ("click", 'css=[data-testid="reimburse-card"]') in pairs  # testid 最高优先
    assert ("fill", "role=textbox[name=采购金额]") in pairs          # aria-label 表单字段
    assert samples.get("采购金额") == "888"


async def test_record_session_storage_state_snapshot(tmp_path) -> None:  # noqa: ANN001
    """录制会话可抓登录态快照(storageState dict:cookies+origins)→ 回放/运行复用。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "form.html"
    page.write_text(_HTML, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        state = await sess.storage_state()
    finally:
        await sess.stop()
    assert isinstance(state, dict) and "cookies" in state and "origins" in state


_PICKER = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="trig" aria-haspopup="listbox" style="cursor:pointer;border:1px solid #ccc;width:300px">
  <label for="dp">请假类型</label><input id="dp" readonly placeholder="请选择" style="width:200px">
</div>
<div id="pop" role="listbox" style="display:none"><div id="opt" style="cursor:pointer">事假</div></div>
<script>
  document.getElementById('trig').onclick=function(){document.getElementById('pop').style.display='block';};
  document.getElementById('opt').onclick=function(){document.getElementById('dp').value='事假';document.getElementById('pop').style.display='none';};
</script></body></html>"""


async def test_picker_recorded_as_pick_param_not_clicks(tmp_path) -> None:  # noqa: ANN001
    """选择型控件(触发框 aria-haspopup + role=listbox 弹层):录成一个 pick 参数步,而非写死的选项点击。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "picker.html"
    page.write_text(_PICKER, encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(page.as_uri())
        await sess.page.click("#trig")          # 打开弹层(触发框,不单独记)
        await sess.page.click("#opt")           # 选「事假」(弹层内,不记点击)
        await sess.page.wait_for_timeout(400)   # 等延时读触发框最终值
        steps, samples = sess.recorded_steps()
    finally:
        await sess.stop()
    ops = [(s["op"], s["locator"]) for s in steps]
    assert ("pick", "label=请假类型") in ops            # 录成 pick 参数步
    assert not any(o == "click" and "事假" in (loc or "") for o, loc in ops)   # 没把「事假」录成写死点击
    assert samples.get("请假类型") == "事假"             # 选中值作样例


_OPENER = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<button id="open" onclick="window.open(NEWURL,'_blank')">打开新页</button>
</body></html>"""

_NEWPAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form><label for="amt">金额</label><input id="amt" name="amount" type="text"></form>
</body></html>"""


async def test_follows_new_tab_and_records_on_it(tmp_path) -> None:  # noqa: ANN001
    """多页 bug 修复:用户点开新标签页/新窗口(window.open / target=_blank)→ 录制会话**跟随**到新页,
    且新页上的操作经 context 级绑定照样被录到(旧实现只挂 self.page,新页既不录又不截屏=打不开)。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    new = tmp_path / "new.html"
    new.write_text(_NEWPAGE, encoding="utf-8")
    opener = tmp_path / "opener.html"
    opener.write_text(_OPENER.replace("NEWURL", repr(new.as_uri())), encoding="utf-8")
    sess = RecordSession()
    try:
        await sess.start(opener.as_uri())
        first = sess.page
        await sess.page.get_by_role("button", name="打开新页").click()
        await sess.page.wait_for_timeout(600)              # 等新页打开 + 跟随切换
        assert sess.page is not first                       # 活动页已切到新标签页
        await sess.page.get_by_label("金额").fill("100")    # 新页上的输入也要被录到
        await sess.flush_recording()
        steps, samples = sess.recorded_steps()
    finally:
        await sess.stop()
    assert ("fill", "label=金额") in [(s["op"], s["locator"]) for s in steps]
    assert samples.get("amount") == "100"


async def test_multipage_handlers_safe_during_teardown() -> None:
    """治 TargetClosedError:会话拆除中(_closing)迟到的 page close / 新页事件不得在已关 context 上
    new_cdp_session 抛错 —— 确定性:_closing 置位后这些 handler 全部安全返回(无浏览器即可验)。"""
    sess = RecordSession()
    sess._closing = True
    sess._on_frame = lambda d: None        # noqa: E731 —— 截屏已"开"过,验切页不会重开
    # 以下在 _closing 下都应安全返回(不触发 new_cdp_session、不抛)
    await sess._open_screencast()
    await sess._restart_screencast()
    await sess._on_page_close(object())
    await sess._on_new_page(object())
    assert sess._cdp is None


async def test_token_auth_sets_login_cookie() -> None:
    """贴 token → 预置登录态:Admin-Token cookie 注入 context(免在画面里登录)。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    from playwright.async_api import async_playwright

    from dano.execution.page.driver import apply_token_auth
    pw = await async_playwright().start()
    b = await pw.chromium.launch(headless=True)
    ctx = await b.new_context()
    try:
        await apply_token_auth(ctx, token="tok123", url="https://oa.example.com:8443/prod-api")
        cookies = await ctx.cookies()
        hit = [c for c in cookies if c["name"] == "Admin-Token" and c["value"] == "tok123"]
        assert hit and hit[0]["domain"].endswith("oa.example.com")
    finally:
        await ctx.close(); await b.close(); await pw.stop()


# ── P0-1 真实浏览器集成:验证 all_requests / diagnostics 在真浏览器链路里真能抓到 ──
_HTML_FETCH = """<!doctype html><html><head></head><body>
<button id="g">go</button>
<script>
document.getElementById('g').onclick = async () => {
  await fetch('/api/list?appId=auto&appName=auto');
  document.title = 'GET_DONE';
};
</script>
</body></html>"""

_HTML_THROW = """<!doctype html><html><head></head><body>
<button id="bad">bad</button>
<script>
console.error('init-warning');
// 顶层 throw:Playwright context 级 pageerror 事件必触发
window.addEventListener('error', function (e) { console.log('caught:' + e.message); });
throw new Error('boom-from-page');
</script>
</body></html>"""


async def test_real_browser_all_requests_captures_get(tmp_path) -> None:  # noqa: ANN001
    """真实浏览器:fetch GET 应进 all_requests,且 query 字段被自动解析。

    不依赖远端服务(发同源 fetch 经 service worker / 静态 server 都易跨域踩坑);用 file:// 起一个
    内置 server 起 1 个 GET 接口验。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    from aiohttp import web
    page = tmp_path / "fetch.html"
    page.write_text(_HTML_FETCH, encoding="utf-8")

    async def handler(req):
        return web.json_response({"rows": []})
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]    # noqa: SLF001 —— aiohttp 没暴露取端口 API
    # 改写 HTML 让 fetch 走真端口
    html = _HTML_FETCH.replace("/api/list", f"http://127.0.0.1:{port}/api/list")
    page.write_text(html, encoding="utf-8")

    sess = RecordSession(intercept_submit=False, capture_reads=False)
    try:
        await sess.start(page.as_uri())
        await sess.page.get_by_role("button", name="go").click()
        try:
            await sess.page.wait_for_function("document.title === 'GET_DONE'", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        await sess.page.wait_for_timeout(500)
    finally:
        await runner.cleanup()
        await sess.stop()
    cap = sess.captured_all_requests()
    methods = [r["method"] for r in cap]
    assert "GET" in methods, f"GET 应进 all_requests,实际 {methods}"
    # 不重复记录(治 P0-1 重构前的 _record_all 双重记录 bug)
    target = [r for r in cap if "/api/list" in r["url"]]
    assert len(target) == 1, f"同一 GET 在 all_requests 中只能占一行,实际 {len(target)}"
    # query 自动解析(治"看不到 GET 携带什么参数")
    assert target[0]["query"] == {"appId": ["auto"], "appName": ["auto"]}, \
        f"query 应被解析,实际 {target[0]['query']}"


async def test_real_browser_diagnostics_captures_console_and_pageerror(tmp_path) -> None:  # noqa: ANN001
    """真实浏览器:console.error 与 throw 抛出的 pageerror 都应进 diagnostics。"""
    if not await _chromium_available():
        pytest.skip("chromium 未安装")
    page = tmp_path / "throw.html"
    page.write_text(_HTML_THROW, encoding="utf-8")
    sess = RecordSession(intercept_submit=False, capture_reads=False)
    try:
        await sess.start(page.as_uri())
        # 等 init-warning console 与 setTimeout throw 落地
        await sess.page.wait_for_timeout(500)
    finally:
        await sess.stop()
    types = [d["type"] for d in sess.captured_diagnostics()]
    assert "console" in types, f"console 事件应进 diagnostics,实际 {types}"
    assert "pageerror" in types, f"pageerror 应进 diagnostics,实际 {types}"
    # pageerror.message 含原异常文案
    page_errors = [d for d in sess.captured_diagnostics() if d["type"] == "pageerror"]
    assert any("boom-from-page" in d["message"] for d in page_errors), page_errors

