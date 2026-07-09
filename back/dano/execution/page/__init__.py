"""录制 V2 页面驱动底座。

只保留录制/登录态复用需要的浏览器 driver。
"""

from __future__ import annotations

from dano.execution.page.driver import FakePageDriver, PageDriver

__all__ = ["FakePageDriver", "PageDriver"]
