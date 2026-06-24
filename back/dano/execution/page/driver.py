"""页面驱动协议 + 两实现(Fake 离线 / Playwright 真实)。

定位铁律:只用语义定位(role/label/placeholder/text/css),绝不用坐标。
locator 语法(driver 自行解析):
  role=button[name=提交] / label=请假天数 / placeholder=请输入 / text=提交成功 / css=#submit / 其余按裸选择器。
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable


# 前端常见的"存 token 的键名"(cookie + localStorage):给定 raw token 但不知系统约定时,一并注入这组,
# SPA 拦截器会从它认识的那个键读到 token、其余被忽略 —— 通用,不写死单一系统(原 'Admin-Token' 只是其一)。
_COMMON_TOKEN_KEYS = ("Admin-Token", "satoken", "token", "access_token", "accessToken",
                      "Authorization", "jwt", "X-Token")


async def apply_token_auth(context, *, token: str, url: str, token_key: str | None = None) -> None:  # noqa: ANN001
    """预置登录态:把 raw token 注入 context(cookie + localStorage),免在画面里登录。必须在 goto 前调用。

    token_key 显式给定(系统已知,如来自系统画像)→ 只注入该键;否则**注入一组常见 token 键名**
    (通用,不挑系统:Admin-Token/satoken/access_token… 都塞,SPA 读它认识的那个,其余忽略)。
    """
    if not token or not url:
        return
    from urllib.parse import urlparse
    host = urlparse(url).hostname
    keys = [token_key] if token_key else list(_COMMON_TOKEN_KEYS)
    for k in keys:
        if host:
            try:
                await context.add_cookies([{"name": k, "value": token, "domain": host, "path": "/"}])
            except Exception:  # noqa: BLE001
                pass
        await context.add_init_script(
            f"try{{localStorage.setItem({json.dumps(k)},{json.dumps(token)});}}catch(e){{}}")


@runtime_checkable
class PageDriver(Protocol):
    """页面驱动接口。所有方法异步;定位失败返回 False(不抛),由运行时按 optional 决定是否判失败。"""

    async def open(self, url: str) -> None: ...
    async def fingerprint(self) -> str: ...
    async def fill(self, locator: str, value: str) -> bool: ...
    async def select(self, locator: str, value: str) -> bool: ...
    async def pick(self, locator: str, value: str) -> bool: ...
    async def click(self, locator: str) -> bool: ...
    async def upload(self, locator: str, value: str) -> bool: ...
    async def wait(self, locator: str | None) -> bool: ...
    async def visible(self, locator: str) -> bool: ...
    async def screenshot(self, label: str) -> str: ...
    def captured(self) -> dict: ...
    async def close(self) -> None: ...


class FakePageDriver:
    """离线测试用页面驱动:模拟一个有指纹、若干可见元素的页面。零浏览器依赖。

    - fingerprint:返回固定指纹(测试可传入不同值模拟改版漂移)。
    - fail_locators:这些 locator 的所有操作/可见性判 False(模拟元素找不到)。
    - visible:None=除 fail 外全可见;给定列表=仅列表内可见(模拟成功标志缺失等)。
    - captured:回放/提交后可注入的结构化产出(如 {'draft_id': 'D-1'}),并入 structured_output。
    """

    def __init__(
        self,
        *,
        fingerprint: str = "fp-v1",
        visible: list[str] | None = None,
        fail_locators: list[str] | None = None,
        captured: dict | None = None,
    ) -> None:
        self._fp = fingerprint
        self._visible: set[str] | None = None if visible is None else set(visible)
        self._fail = set(fail_locators or [])
        self._captured = dict(captured or {})
        self.ops: list[tuple] = []   # 执行序列,供测试断言

    async def open(self, url: str) -> None:
        self.ops.append(("open", url))

    async def login_wall(self) -> bool:
        return False

    async def fingerprint(self) -> str:
        return self._fp

    async def _act(self, name: str, locator: str | None, *extra) -> bool:
        self.ops.append((name, locator, *extra))
        return locator not in self._fail

    async def fill(self, locator: str, value: str) -> bool:
        return await self._act("fill", locator, value)

    async def select(self, locator: str, value: str) -> bool:
        return await self._act("select", locator, value)

    async def pick(self, locator: str, value: str) -> bool:
        return await self._act("pick", locator, value)

    async def click(self, locator: str) -> bool:
        return await self._act("click", locator)

    async def upload(self, locator: str, value: str) -> bool:
        return await self._act("upload", locator, value)

    async def wait(self, locator: str | None) -> bool:
        self.ops.append(("wait", locator))
        return locator not in self._fail if locator else True

    async def visible(self, locator: str) -> bool:
        if locator in self._fail:
            return False
        return True if self._visible is None else locator in self._visible

    async def screenshot(self, label: str) -> str:
        self.ops.append(("shot", label))
        return f"fake://{label}.png"

    def captured(self) -> dict:
        return dict(self._captured)

    async def close(self) -> None:
        self.ops.append(("close",))


class PlaywrightPageDriver:
    """真实 Playwright 驱动(M3)。playwright 惰性导入:不装则本类不可用,但模块仍可导入。

    locator 语义解析 → Playwright get_by_role/get_by_label/get_by_placeholder/get_by_text/locator。
    指纹 = 页面可交互元素(input/select/textarea/button)结构的哈希(忽略文案,抗漂移)。
    """

    def __init__(self, page, base_url: str = "") -> None:  # noqa: ANN001 —— page: playwright.async_api.Page
        self._page = page
        self._base = base_url.rstrip("/")
        self._captured: dict = {}
        self._context = None        # 池化模式下持有 context;close 只关它
        self._owns_browser = True   # True=独立启动(close 关浏览器);False=来自池(close 只关 context)

    @classmethod
    async def launch(cls, *, base_url: str = "", headless: bool = True,
                     storage_state: str | None = None, token: str | None = None,
                     token_key: str | None = None, auth_url: str = "") -> tuple["PlaywrightPageDriver", object]:
        """起浏览器/上下文/页,返回 (driver, playwright_ctx_mgr) —— 调用方负责 close 释放。

        token 给定 → 预置登录态(免登录),注入域取 auth_url 或 base_url。
        """
        from playwright.async_api import async_playwright

        from dano.infra.http import tls_verify
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=headless)
        # 自签证书(DANO_INSECURE_TLS=1)→ 忽略 https 证书错误,否则 8443 自签站点打不开。
        ctx_kwargs: dict = {"ignore_https_errors": not tls_verify()}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state    # 登录态:Playwright storageState JSON(cookie+localStorage)
        context = await browser.new_context(**ctx_kwargs)
        if token:
            await apply_token_auth(context, token=token, url=auth_url or base_url, token_key=token_key)
        page = await context.new_page()
        driver = cls(page, base_url=base_url)
        driver._pw, driver._browser, driver._context = pw, browser, context  # 供 close 释放
        return driver, pw

    @classmethod
    def from_context(cls, page, context, *, base_url: str = "") -> "PlaywrightPageDriver":  # noqa: ANN001
        """池化构造:共享浏览器派生的 context+page;close 只关 context,浏览器常驻。"""
        d = cls(page, base_url=base_url)
        d._context = context
        d._owns_browser = False
        return d

    def _resolve(self, locator: str):  # noqa: ANN202 —— 返回 playwright Locator
        kind, _, rest = (locator or "").partition("=")
        if kind == "role":
            name = None
            if "[name=" in rest:
                role, name = rest.split("[name=", 1)
                name = name.rstrip("]")
            else:
                role = rest
            return self._page.get_by_role(role, name=name) if name else self._page.get_by_role(role)
        if kind == "label":
            return self._page.get_by_label(rest)
        if kind == "placeholder":
            return self._page.get_by_placeholder(rest)
        if kind == "text":
            return self._page.get_by_text(rest)
        if kind == "css":
            return self._page.locator(rest)
        return self._page.locator(locator)

    async def open(self, url: str) -> None:
        full = url if url.startswith(("http", "file")) else f"{self._base}{url}"
        await self._page.goto(full, wait_until="domcontentloaded")
        # SPA(Vue/React 等)表单异步渲染:等网络空闲 + 出现可交互元素再返回(都带超时,绝不卡死)。
        for state in ("networkidle",):
            try:
                await self._page.wait_for_load_state(state, timeout=8000)
            except Exception:  # noqa: BLE001
                pass
        try:
            await self._page.wait_for_selector("input,select,textarea,button", timeout=8000, state="attached")
        except Exception:  # noqa: BLE001
            pass

    async def login_wall(self) -> bool:
        """通用登录墙检测:URL 命中 login/signin 路径段,或页面有可见密码框 → 多半被重定向到登录页。"""
        import re
        try:
            if re.search(r"/(login|signin|sign-in|sso)(?:[/?#]|$)", (self._page.url or ""), re.I):
                return True
            return await self._page.locator("input[type=password]").first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    async def fingerprint(self) -> str:
        import hashlib
        sig = await self._page.evaluate(
            "() => Array.from(document.querySelectorAll('input,select,textarea,button'))"
            ".map(e => e.tagName + ':' + (e.getAttribute('name')||e.getAttribute('type')||'')).join('|')"
        )
        return "fp:" + hashlib.sha256((sig or "").encode()).hexdigest()[:16]

    async def fill(self, locator: str, value: str) -> bool:
        try:
            await self._resolve(locator).first.fill(value or "")   # .first:同名多匹配不触发 strict 报错
            return True
        except Exception:  # noqa: BLE001
            return False

    async def select(self, locator: str, value: str) -> bool:
        try:
            await self._resolve(locator).first.select_option(value)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def pick(self, locator: str, value: str) -> bool:
        """选择型控件参数化(框架无关):点开触发框 → 弹层里点文本=value 的选项/日期格;
        点不到则把 value 打进触发框输入并回车(日期/可输下拉)。value 即用户要传的参数值。"""
        try:
            trig = self._resolve(locator).first
            await trig.click()                                   # 打开日期/下拉弹层
            await self._page.wait_for_timeout(350)
            v = (value or "").strip()
            if not v:
                return True
            # 1) 弹层里点完全匹配文本的选项(下拉:value=选项可见文本,如「事假」)
            try:
                opt = self._page.get_by_text(v, exact=True)
                if await opt.count() > 0 and await opt.first.is_visible():
                    await opt.first.click()
                    return True
            except Exception:  # noqa: BLE001
                pass
            # 2) 点不到 → 把值打进触发框输入并回车(日期/可输下拉:value=完整值如 2025-06-30)
            try:
                inp = trig.locator("input").first
                await inp.fill(v)
                await self._page.keyboard.press("Enter")
                return True
            except Exception:  # noqa: BLE001
                return False
        except Exception:  # noqa: BLE001
            return False

    async def click(self, locator: str) -> bool:
        try:
            await self._resolve(locator).first.click()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def upload(self, locator: str, value: str) -> bool:
        try:
            await self._resolve(locator).first.set_input_files(value)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def wait(self, locator: str | None) -> bool:
        try:
            if locator:
                await self._resolve(locator).wait_for(state="visible", timeout=10_000)
            else:
                await self._page.wait_for_load_state("networkidle")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def visible(self, locator: str) -> bool:
        try:
            return await self._resolve(locator).first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    async def screenshot(self, label: str) -> str:
        import base64
        try:
            png = await self._page.screenshot()
            return "data:image/png;base64," + base64.b64encode(png).decode()
        except Exception:  # noqa: BLE001
            return f"playwright://{label}"

    def captured(self) -> dict:
        return dict(self._captured)

    async def scout(self) -> dict:
        """抽取当前页表单语义结构(供接入期侦察)。需先 open。"""
        from dano.execution.page.scout import scout_dom
        return await scout_dom(self._page)

    async def close(self) -> None:
        if not self._owns_browser:                 # 池化:只关 context,共享浏览器常驻
            if self._context is not None:
                try:
                    await self._context.close()
                except Exception:  # noqa: BLE001
                    pass
            return
        for attr in ("_context", "_browser", "_pw"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    await (obj.stop() if attr == "_pw" else obj.close())
                except Exception:  # noqa: BLE001
                    pass
