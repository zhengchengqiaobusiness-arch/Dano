"""页面脚本运行时(流程8 解释器)。

`_run_page` 调用契约:`run(task_id, script, fields, confirm=callable[dict]->bool) -> ExecResult`,
其中 structured_output 用 'drift' / 'cancelled' 两个键告诉编排器走 DRIFT / CANCELLED 分支。

二态铁律:页面执行只有跑通 / 跑不通;DOM「提交成功」≠业务事实(铁律③),故页面写默认 L3,
运行期遇 submit 必先经 confirm 卡片;最终是否 COMPLETED 由编排器的 closure(事实核查)决定。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from uuid import UUID

import structlog

from dano.execution.page.driver import PageDriver
from dano.shared.asset_bodies import PageAction, PageScriptBody
from dano.shared.enums import FailureClass, Outcome
from dano.shared.models import AssertionResult, Evidence, ExecResult

log = structlog.get_logger(__name__)


def _resolve_value(action: PageAction, fields: dict) -> str | None:
    """字段绑定:value_from 优先('const:'/'field:'),否则回退旧的字面 value。"""
    src = action.value_from
    if src:
        kind, _, rest = src.partition(":")
        if kind == "const":
            return rest
        if kind == "field":
            v = fields.get(rest)
            return str(v) if v is not None else None
    return action.value


class PageActionRuntime:
    """通用页面解释器。driver_factory: 可调用,返回(或 await 出)一个 PageDriver。"""

    def __init__(self, driver_factory: Callable[[], PageDriver], *,
                 timeout_s: float = 120.0, max_concurrency: int = 0) -> None:
        self._driver_factory = driver_factory
        self._timeout_s = timeout_s   # 单次运行总超时(防页面卡死/元素永久等待)
        # 并发上限(>0 时):同时最多 N 个浏览器在跑,防资源耗尽;0=不限(Fake 测试不需要)
        self._sem = asyncio.Semaphore(max_concurrency) if max_concurrency and max_concurrency > 0 else None

    async def _make_driver(self, storage_state=None) -> PageDriver:  # noqa: ANN001
        # 给了登录态且工厂支持(运行期复用录制会话)→ factory(storage_state);否则按原样 factory()
        res = self._driver_factory(storage_state) if storage_state is not None else self._driver_factory()
        return await res if inspect.isawaitable(res) else res

    async def run(self, task_id: UUID, script: PageScriptBody, fields: dict, *,
                  confirm: Callable[[dict], bool], storage_state=None) -> ExecResult:  # noqa: ANN001
        """超时 + 并发上限护栏外壳;真正执行在 _execute。驱动无论成败/超时都被关闭回收。

        storage_state:运行期复用录制保存的登录态(Playwright storageState 路径/字典),免被挡登录。
        """
        if self._sem is None:
            return await self._guarded(task_id, script, fields, confirm=confirm, storage_state=storage_state)
        async with self._sem:                       # 限流:不超过 max_concurrency 个浏览器并发
            return await self._guarded(task_id, script, fields, confirm=confirm, storage_state=storage_state)

    async def _guarded(self, task_id: UUID, script: PageScriptBody, fields: dict, *,
                       confirm: Callable[[dict], bool], storage_state=None) -> ExecResult:  # noqa: ANN001
        driver = await self._make_driver(storage_state)
        try:
            return await asyncio.wait_for(
                self._execute(driver, task_id, script, fields, confirm=confirm),
                timeout=self._timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            log.warning("page.timeout", task=str(task_id), timeout_s=self._timeout_s)
            return ExecResult(task_id=task_id, outcome=Outcome.FAILED,
                              failure_class=FailureClass.NETWORK,
                              structured_output={"timeout": True, "timeout_s": self._timeout_s})
        finally:
            try:
                await driver.close()                # 回收:超时/异常也释放浏览器,杜绝僵尸
            except Exception:  # noqa: BLE001
                pass

    async def _execute(self, driver: PageDriver, task_id: UUID, script: PageScriptBody, fields: dict, *,
                       confirm: Callable[[dict], bool]) -> ExecResult:
        shots: list[str] = []
        results: list[AssertionResult] = []
        if script.start_url:
            await driver.open(script.start_url)

        # 0. 登录墙检测(通用):被重定向到登录页 → 登录态无效/过期,清晰报错而非对着登录页瞎填
        lw = getattr(driver, "login_wall", None)
        if lw is not None and await lw():
            log.warning("page.at_login", task=str(task_id))
            return ExecResult(task_id=task_id, outcome=Outcome.FAILED, failure_class=FailureClass.LOGIN,
                              structured_output={"at_login": True})

        # 1. 指纹校验 → 漂移(执行前结构变了,中止,转流程11)
        actual_fp = await driver.fingerprint()
        if script.dom_fingerprint and actual_fp != script.dom_fingerprint:
            log.warning("page.drift", expected=script.dom_fingerprint, actual=actual_fp)
            return ExecResult(
                task_id=task_id, outcome=Outcome.FAILED, failure_class=FailureClass.PAGE_FIELD,
                evidence=Evidence(dom_snapshots=[actual_fp]),
                structured_output={"drift": True, "expected_fingerprint": script.dom_fingerprint,
                                   "actual_fingerprint": actual_fp},
            )

        # 2. 逐步执行 + 提交前确认 + 逐步元素断言
        submitted = False
        for i, action in enumerate(script.actions):
            if action.op == "submit":
                if not confirm(dict(fields)):
                    log.info("page.cancelled", step=i)
                    return ExecResult(task_id=task_id, outcome=Outcome.FAILED,
                                      assertion_results=results,
                                      structured_output={"cancelled": True})
                submitted = True
            ok, detail = await self._do(driver, action, fields)
            results.append(AssertionResult(name=f"step{i}:{action.op}", passed=ok, detail=detail))
            if not ok and not action.optional:
                shots.append(await driver.screenshot(f"fail_step{i}"))
                return ExecResult(
                    task_id=task_id, outcome=Outcome.FAILED, failure_class=FailureClass.PAGE_FIELD,
                    assertion_results=results,
                    evidence=Evidence(request_body=dict(fields), response_body={"failed_step": i},
                                      screenshots=shots),
                    structured_output={"failed_step": i, "op": action.op, "locator": action.locator},
                )

        # 3. 成功标志(二态判据)。无成功标志(如 dry 回放)→ marker_ok=None(未校验,不谎报通过),
        #    诚实暴露:此时 submitted 多为 False,证据不会出现「未提交却成功」的自相矛盾。
        marker_ok: bool | None = None
        if script.success_marker:
            marker_ok = await driver.visible(script.success_marker)
            results.append(AssertionResult(name="success_marker", passed=marker_ok,
                                           detail=script.success_marker))
        shots.append(await driver.screenshot("final"))

        output = {"submitted": submitted, "success_marker": marker_ok, **driver.captured()}
        outcome = Outcome.PASSED if marker_ok is not False else Outcome.FAILED
        return ExecResult(
            task_id=task_id, outcome=outcome, assertion_results=results,
            evidence=Evidence(request_body=dict(fields), response_body=output, screenshots=shots),
            structured_output=output,
        )

    async def _do(self, driver: PageDriver, action: PageAction, fields: dict) -> tuple[bool, str]:
        op, loc = action.op, action.locator
        value = _resolve_value(action, fields)
        if op == "goto":
            await driver.open(value or loc or "")
            ok = True
        elif op == "fill":
            ok = await driver.fill(loc or "", value or "")
        elif op == "select":
            ok = await driver.select(loc or "", value or "")
        elif op == "pick":
            ok = await driver.pick(loc or "", value or "")   # 选择型控件:点开→按值选/输
        elif op == "upload":
            ok = await driver.upload(loc or "", value or "")
        elif op in ("click", "submit"):
            ok = await driver.click(loc) if loc else True
        elif op == "wait":
            ok = await driver.wait(loc)
        elif op == "verify":
            ok = await driver.visible(loc) if loc else True
        else:
            return False, f"未知页面操作 op={op}"
        # 逐步元素断言:执行后该元素须可见(H27 修复:wait/verify op 本身已做可见性检查,跳过避免重复 + Evidence 矛盾)
        if ok and action.assert_visible and loc and op not in ("wait", "verify"):
            ok = await driver.visible(loc)
            if not ok:
                return False, f"断言失败:{loc} 不可见"
        return ok, "" if ok else f"操作未命中元素:{loc}"


def build_page_runtime() -> PageActionRuntime | None:
    """装配真实页面运行时(供网关注入 Orchestrator)。

    默认返回 None —— 即与接入 Playwright 前完全一致(页面 Skill 走「页面运行时未装配」),不影响现有 API。
    仅当 `DANO_PAGE_RUNTIME=1` 且 playwright 可导入时,才构造真实 Playwright 运行时。
    注意:真实驱动的 base_url/storageState 取自配置;脚本 start_url 建议为绝对 URL。
    """
    from dano.config import get_settings
    s = get_settings()
    if not s.page_runtime:
        return None
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:  # noqa: BLE001
        log.warning("page.runtime.disabled", reason="playwright 未安装")
        return None

    from dano.execution.page.pool import get_browser_pool

    headless = s.browser_headless
    base_url = s.page_base_url
    storage = s.page_storage_state or None
    timeout_s = s.page_timeout_s       # 单次运行总超时
    pool_size = s.browser_pool_size    # 并发上限(信号量)+ 复用同一浏览器

    pool = get_browser_pool(headless=headless)

    async def factory(storage_state=None):  # noqa: ANN001,ANN202 —— 池取隔离 context;运行期可传录制登录态覆盖
        return await pool.new_driver(base_url=base_url, storage_state=storage_state or storage)

    log.info("page.runtime.enabled", headless=headless, base_url=bool(base_url),
             timeout_s=timeout_s, pool_size=pool_size)
    return PageActionRuntime(factory, timeout_s=timeout_s, max_concurrency=pool_size)
