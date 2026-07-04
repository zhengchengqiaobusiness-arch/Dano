"""共享浏览器池(M6 R3):一个 chromium 进程常驻,每次运行只起一个隔离 context。

为什么:运行期每次 invoke 页面 Skill 都重启 chromium 要 ~1-2s;复用同一浏览器、按运行起 context
(cookie/localStorage 隔离)可省去这开销。并发上限由 PageActionRuntime 的信号量控制,本池不另限流。
运行结束 driver.close() 只关 context,浏览器常驻;进程退出 shutdown() 关浏览器。
浏览器异常断开时,下次 new_driver 自动重启一次(失败安全)。
"""

from __future__ import annotations

import asyncio

import structlog

log = structlog.get_logger(__name__)


class BrowserPool:
    """单浏览器 + 每运行一个 context。线程不安全,按单事件循环用(运行期网关进程内)。"""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> None:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return
            from playwright.async_api import async_playwright
            if self._pw is None:
                self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self._headless)
            log.info("browser_pool.launched", headless=self._headless)

    async def new_driver(self, *, base_url: str = "", storage_state: str | None = None):
        """从共享浏览器派生一个隔离 context+page,返回池化 driver(close 只关 context)。"""
        from dano.execution.page.driver import PlaywrightPageDriver
        from dano.infra.http import tls_verify

        ctx_kwargs: dict = {"ignore_https_errors": not tls_verify()}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        await self._ensure_browser()
        try:
            context = await self._browser.new_context(**ctx_kwargs)
        except Exception:  # noqa: BLE001 —— 浏览器可能已崩 → 重启一次再试(失败安全)
            log.warning("browser_pool.new_context_failed_restart")
            self._browser = None
            await self._ensure_browser()
            context = await self._browser.new_context(**ctx_kwargs)
        page = await context.new_page()
        return PlaywrightPageDriver.from_context(page, context, base_url=base_url)

    async def shutdown(self) -> None:
        async with self._lock:
            for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
                if obj is not None:
                    try:
                        await getattr(obj, meth)()
                    except Exception:  # noqa: BLE001
                        pass
            self._browser = self._pw = None
            log.info("browser_pool.shutdown")


_POOL: BrowserPool | None = None
# H28 修复:进程内单例销毁过程加锁。get_browser_pool 是同步入口,不能 await 锁;
# shutdown 期间不提前清空 _POOL,避免并发 get 看到 None 后新建第二个池。
_POOL_LOCK = asyncio.Lock()


def get_browser_pool(*, headless: bool = True) -> BrowserPool:
    """进程内单例池(运行期网关用)。"""
    global _POOL
    if _POOL is None:
        _POOL = BrowserPool(headless=headless)
    return _POOL


async def shutdown_browser_pool() -> None:
    """网关关停时调用,释放常驻浏览器。H28:关闭完成后再清空单例。"""
    global _POOL
    async with _POOL_LOCK:
        if _POOL is not None:
            await _POOL.shutdown()
            _POOL = None
