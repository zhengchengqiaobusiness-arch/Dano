# Control In App Browser

PI 是操作者，也是唯一语义权威。人同时也可以点预览。

你们共用同一只 Playwright 浏览器、同一路预览画面、同一条证据。底层用动作队列串行，同一时刻只执行一个鼠标动作。两条通道一直开着。不要锁死预览，不要丢弃人的点击。

## 主力循环

1. `control_in_app_browser` `action=open_page` 打开目标页。
2. `action=snapshot` 一次，读 `controls` / `actions` 里的 **selector**（`placeholder=` / `label=` / `role=button[name=]`）。不要死盯 `c1`/`a1`。
3. 按 selector `click` / `fill` / `choose`。下拉必须 `choose(selector, 可见选项原文)`，一次选中。不要 click 后再 snapshot 再点选项。
4. 不要每个字段都 snapshot，不要 `include_screenshot`。要看画面用 `action=screenshot`。
5. 人点过的看 `snapshot.recentUserActions`。不要停下来等人。
6. `network_since` 或 `read_request_shape` 看真实请求。
7. 立刻 `submit_recording_capability` 交这一项。人点出的动作也要交。
8. 目标做完 `submit_recording_result({final:true, use_draft:true})`。

## 协助不是排他接管

登录、验证码、确认写入：`action=assist`，`reason` 写清楚要人做什么。预览始终可以点。不要把协助做成 takeover，不要让程序丢掉人的 `applyInput`。

## 禁止

- 不要锁预览、不要 `pointer-events: none` 盖住画布。
- 自动点击失败、空转或超时：只停自动点，不要结束录制，不要丢掉人的 `applyInput`。
- 不要等人全部点完再读几百条证据拼超大 JSON。
- 不要本地推断能力。字段合同仍按 `RECORDING_CAPABILITY.md`。
