"""Stagehand 驱动的页面自主操作(替代手搓的 scout/observe/act/pick/locator 整套)。

为什么用框架不手搓:Stagehand(browserbase)是成熟的 AI 浏览器操作框架,自带
无障碍/DOM 感知、自定义控件(下拉/日期/级联)机械、按意图操作、**每步返回稳定 selector**、
auto-cache 自愈。我们只接住它录到的动作,结晶成确定性 PageScriptBody 用 PageActionRuntime 回放。

架构(本地模式,无需 Browserbase 云):
  Stagehand(server=local) 内嵌 server + 本地 Chromium(实测本环境可起)
  → session.execute(goal)  自主操作页面达成目标(它做感知+控件机械+规划)
  → session.replay()       取回录到的动作序列(method/parameters/result,含 selector)
  → 映射成 [{op, locator, value, ...}]  →  draft_page_script 结晶  →  审核  →  发布

需:OPENAI_API_KEY(config.stagehand_api_key / 复用 pi_api_key)。缺则诚实 OperateError,不静默。
登录态:本地走 user_data_dir(持久 Chrome 配置);Dano 的 storage_state→cookies 注入待真机联调。
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

# Stagehand DataPageAction.method → PageAction.op(driver 支持的语义操作)
_METHOD_TO_OP = {
    "click": "click", "fill": "fill", "type": "fill", "press": "fill",
    "selectoption": "select", "select_option": "select", "select": "select",
    "check": "click", "uncheck": "click", "setinputfiles": "upload", "upload": "upload",
    "goto": "goto", "navigate": "goto",
}
# 提交语义(method=click 且文本/选择器含这些)→ 标记为 submit 步
_SUBMIT_HINT = ("提交", "保存", "确定", "确认", "submit", "save")


class OperateError(RuntimeError):
    """页面自主操作失败(框架不可用 / 无 key / 操作未达成)。诚实抛出,不静默发空 skill。"""


def _selector_to_locator(sel: str | None) -> str | None:
    """Stagehand 返回的 CSS/XPath selector → driver locator 语法。"""
    if not sel:
        return None
    s = sel.strip()
    if s.startswith(("xpath=", "//", "(")):                  # XPath
        return "xpath=" + s[len("xpath="):] if s.startswith("xpath=") else "xpath=" + s
    return "css=" + s                                        # 默认当 CSS


def _action_to_step(act: dict) -> dict | None:
    """一条 Stagehand replay 动作 → PageScriptBody 步骤草案(op/locator/value)。

    act: {method, parameters:{...}, result:{selector,...}, ...}。无可执行 op 则返回 None。
    """
    method = str(act.get("method") or "").lower().replace("_", "")
    op = _METHOD_TO_OP.get(method)
    if not op:
        return None
    params = act.get("parameters") or {}
    result = act.get("result") or {}
    selector = result.get("selector") or params.get("selector")
    locator = _selector_to_locator(selector)
    # 值:fill 取 text/value;select 取 value/option;upload 取 path
    value = (params.get("text") or params.get("value") or params.get("option")
             or params.get("path") or params.get("files"))
    if isinstance(value, list):
        value = value[0] if value else None
    step = {"op": op, "locator": locator, "value": (str(value) if value is not None else None)}
    # 提交识别:click 到提交类按钮
    blob = f"{selector or ''} {value or ''} {act.get('instruction') or ''}".lower()
    if op == "click" and any(h in blob for h in _SUBMIT_HINT):
        step["op"] = "submit"
    return step


def replay_to_steps(replay_data: dict) -> list[dict]:
    """Stagehand replay 响应(data.pages[].actions[])→ 有序步骤草案列表。"""
    steps: list[dict] = []
    for page in (replay_data.get("pages") or []):
        for act in (page.get("actions") or []):
            st = _action_to_step(act)
            if st and st.get("locator"):                    # 只收能定位的动作
                steps.append(st)
    return steps


async def _prime_profile(storage_state: "str | dict", start_url: str) -> str:
    """用 Playwright 把 storage_state(cookie+localStorage)烤进一个临时 Chrome 配置目录(userDataDir),
    供 Stagehand 本地复用登录态(它不收 storageState,只认 userDataDir)。返回目录路径,调用方负责清理。"""
    import json
    import tempfile

    from playwright.async_api import async_playwright
    data = storage_state
    if isinstance(storage_state, str):
        with open(storage_state, encoding="utf-8") as f:
            data = json.load(f)
    user_dir = tempfile.mkdtemp(prefix="dano-sh-profile-")
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(user_dir, headless=True)
    try:
        cookies = data.get("cookies") or []
        if cookies:
            await ctx.add_cookies(cookies)                    # cookie 持久进 profile
        for origin in (data.get("origins") or []):            # localStorage:到对应 origin 页里 setItem
            ls = origin.get("localStorage") or []
            if not ls:
                continue
            page = await ctx.new_page()
            try:
                await page.goto(origin.get("origin") or start_url, wait_until="domcontentloaded", timeout=15000)
                for it in ls:
                    await page.evaluate("([k,v])=>{try{localStorage.setItem(k,v);}catch(e){}}",
                                        [it.get("name"), it.get("value")])
            except Exception:  # noqa: BLE001
                pass
            finally:
                await page.close()
    finally:
        await ctx.close()                                     # close 时 profile 落盘
        await pw.stop()
    return user_dir


async def operate_page(
    *, start_url: str, goal: str, api_key: str, model: str = "openai/gpt-4o",
    headless: bool = True, user_data_dir: str | None = None,
    storage_state: "str | dict | None" = None, max_steps: int = 30,
) -> dict:
    """用 Stagehand 在真实页面上自主达成 goal,返回 {steps, final_url, completed, raw}。

    steps:可直接喂 draft_page_script 的 RecordedStep 形态(op/locator/value)。缺 key/框架/未达成 → OperateError。
    """
    if not api_key:
        raise OperateError("缺 OpenAI key(配置 stagehand_api_key / pi_api_key);Stagehand 无 key 跑不了。")
    try:
        from stagehand import AsyncStagehand
    except Exception as e:  # noqa: BLE001
        raise OperateError(f"未安装 stagehand:{e}") from e

    # 登录态:Stagehand 本地不收 storageState,只认 userDataDir(持久 Chrome 配置)/cdpUrl。
    # 故把 Dano 的 storage_state(cookie+localStorage)**烤进一个临时配置目录**,再交给 Stagehand。
    primed_dir: str | None = None
    if storage_state and not user_data_dir:
        try:
            primed_dir = await _prime_profile(storage_state, start_url)
            user_data_dir = primed_dir
        except Exception as e:  # noqa: BLE001
            log.warning("stagehand.prime_profile_failed", error=str(e))   # 烤失败 → 无登录态(后续多半撞登录墙)

    browser: dict = {"type": "local", "launchOptions": {"headless": headless}}
    if user_data_dir:
        browser["userDataDir"] = user_data_dir                # ← userDataDir 在 browser 下,不在 launchOptions
    sh = AsyncStagehand(server="local", model_api_key=api_key, local_headless=headless)
    try:
        session = await sh.sessions.start(model_name=model, browser=browser)
        await session.navigate(url=start_url)
        # 自主操作(Stagehand agent 做感知+控件机械+规划)。execute 需 agent_config + execute_options。
        exe = await session.execute(
            agent_config={"model": model},
            execute_options={"instruction": goal, "max_steps": float(max_steps)})
        exe_d = exe.to_dict() if hasattr(exe, "to_dict") else dict(exe)
        completed = bool((exe_d.get("data") or {}).get("result", {}) and
                         any(a.get("taskCompleted") for a in
                             (((exe_d.get("data") or {}).get("result") or {}).get("actions") or [])))
        # 取回录到的动作 → 结晶素材
        rep = await session.replay()
        rep_d = rep.to_dict() if hasattr(rep, "to_dict") else dict(rep)
        steps = replay_to_steps((rep_d.get("data") or rep_d))
        final_url = ""
        try:
            final_url = (rep_d.get("data") or {}).get("pages", [{}])[-1].get("url", "")
        except Exception:  # noqa: BLE001
            pass
        log.info("stagehand.operate", goal=goal[:60], steps=len(steps), completed=completed, final_url=final_url)
        if not steps:
            raise OperateError("Stagehand 未录到任何可定位的操作(页面没填成 / selector 缺失)。")
        await session.end()
        return {"steps": steps, "final_url": final_url, "completed": completed, "raw": rep_d}
    finally:
        try:
            await sh.close()
        except Exception:  # noqa: BLE001
            pass
        if primed_dir:                                        # 清理临时配置目录
            import shutil
            shutil.rmtree(primed_dir, ignore_errors=True)
