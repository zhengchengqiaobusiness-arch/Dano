---
name: control-in-app-browser
description: Drive the in-app Playwright browser while the human can still click the same preview. Use when recording capabilities, clicking the live page, or handling login/captcha assist without locking the preview.
---

# Control In App Browser

PI 自动点页面，人同时也可以点预览。同一只浏览器、同一路画面、同一条证据。动作队列串行，通道不关。

主力：`control_in_app_browser` → `open_page` / `snapshot` / `click|fill|select|fill_fields` → `submit_recording_capability`。

协助：`action=assist`。不要锁预览，不要丢弃人的点击。
自动点击失败只停自动点，人手点预览和停录分析仍按原链路走。
