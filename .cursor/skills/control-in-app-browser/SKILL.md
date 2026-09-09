---
name: control-in-app-browser
description: Drive the in-app Playwright browser while the human can still click the same preview. Use when recording capabilities, clicking the live page, or handling login/captcha assist without locking the preview.
---

# Control In App Browser

PI 自动点页面，人同时也可以点预览。同一只浏览器、同一路画面、同一条证据。动作队列串行，通道不关。

录制入口是 `Pi_check/skill`，不是本文件。

主力：`control_in_app_browser` → `open_page` / `snapshot` → 按目标写上当前页字段 → 再点该动作自己的查询/保存 → `submit_recording_capability`。

下拉用 `choose(selector, 可见选项原文)` 一次选中。打开弹层后再 snapshot 一次。`readonly` 表示整个控件锁死，不是下拉内部展示框。不要每个字段都 snapshot，不要把截图塞进 JSON。

首屏自动请求不是已经做完。空表不要点保存。没有搜索/查询文案时，点已经出现的树或列表节点。不要点没文案的 aN。协助：`action=assist`。协助后停手。不要锁预览，不要丢弃人的点击。
自动点击失败只停自动点，人手点预览和停录分析仍按原链路走。
