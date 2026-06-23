"""无 API 页面执行(流程8)。

运行期由 `PageActionRuntime` 解释 `PageScriptBody`,经 `PageDriver` 驱动浏览器:
指纹校验(漂移检测)→ 逐步执行 + 元素断言 → 提交前确认 → 二态 + 截图证据。

驱动二实现:`FakePageDriver`(离线测试,零浏览器依赖)/ `PlaywrightPageDriver`(真实,惰性导入)。
`build_page_runtime()` 在 playwright 缺失或未开启时返回 None —— 此时行为与接入 Playwright 前完全一致
(页面 Skill 走 `_run_page` 的「页面运行时未装配」分支),不影响任何现有 API 路径。
"""

from __future__ import annotations

from dano.execution.page.driver import FakePageDriver, PageDriver
from dano.execution.page.runtime import PageActionRuntime, build_page_runtime
from dano.execution.page.scout import scout_dom, to_recorded_steps

__all__ = ["FakePageDriver", "PageDriver", "PageActionRuntime", "build_page_runtime",
           "scout_dom", "to_recorded_steps"]
