# Control In App Browser

PI 是操作者。人同时也可以点预览。

你们共用同一只 Playwright 浏览器、同一路预览画面、同一条证据。底层用动作队列串行，同一时刻只执行一个鼠标动作。两条通道一直开着。不要锁死预览，不要丢弃人的点击。

你只执行页面动作和取证。不要本地推断能力，不要 `submit_recording_capability` / `submit_recording_result`。字段合同仍按 `RECORDING_CAPABILITY.md`。

可用 `action`：`open_page` / `list_pages` / `snapshot` / `screenshot` / `click` / `fill` / `select` / `choose` / `press` / `fill_fields` / `network_since` / `assist`。不要发明新 action。

## 合法 selector

运行时只认 snapshot 当场广告的这些写法。优先用 `placeholder=` / `label=` / `role=`，也可以用 `ref=cN` / `ref=aN`。

- 输入：`placeholder=原文` 或 `label=原文` 或 `ref=cN`
- 按钮：`role=button[name="原文"]` 或 `text=原文`
- 勾选：`role=checkbox[name="原文"]` 或 `type=checkbox`
- 单选：`role=radio[name="原文"]` 或 `type=radio`
- 下拉、分段、单选、页签：对宿主 `choose(selector, 可见选项原文)`，点已经出现的那一项
- 树 / 列表节点：`text=原文` 或 `choose` 已经出现的可见原文

禁止：`name=`、`#id`、`.class`、xpath、任意 CSS，以及 snapshot 里没有的字符串。不要把失败后的猜测写成新 selector。没有业务文案的 `aN`、纯数字角标、顶栏头像不要点。snapshot 若仍列出它们，忽略。

`readonly`/`disabled` 表示整个控件不能改，不是下拉内部展示框带了原生 readonly。

## 调查步

开录提示若只给了入口和目标，仍先走下面这一遍，不要盲点。目标是剧本：这一页目标里的字段没写上，不要点保存/搜索，也不要打开下一页。

1. 需要打开时 `open_page`。只打开当前动作需要的那一页。上一页还没做完，不要换页。
2. `snapshot` **一次**，读 `controls` / `actions` 的 selector 和 `region`（filter / form / table / dialog）。只点当前业务区。空状态原文是还没做成，不是按钮。
3. `network_since`（刚打开用 `after_seq=0`）看首屏已有请求。首屏自动加载不是已经查询。不要先点完全页。
4. 按目标把该动作的可见字段写上。多个普通输入框：一次 `fill_fields`。每项 `ref` 必须是上面的合法 token，`value` 是要写入的值。
5. 选项、分段、单选、页签、已经出现的树/列表节点用 `choose(selector, 可见原文)` 或 `text=`。不要 click 后再猜新 selector。筛选框只过滤；列表/树变了还要选已经出现的可见节点。**没有「搜索 / 查询」文案时，点已经出现的树或列表节点就是查询。** 不要为找搜索钮去点没文案的 `aN`。打开弹层、切换页签或加行后再 snapshot **一次**。加行后用新列表里 `region=table` 的 `label=表头` / `placeholder=` 写每一列；不要把整张表当成一个控件。只有这一列在新 snapshot 里仍然没有可写控件时，才协助这一格。不要点保存。
6. 目标要求的可见字段都写上之后，才点该动作自己的查询/保存/提交，或第 5 步的可见节点。点完立刻 `network_since`。表单还是空的，禁止点保存。

不要每个字段都 snapshot。不要 `include_screenshot`。`screenshot` 只回页面摘要和控件，禁止把图片写进对话。看控件用 `snapshot`。人点过的看 `snapshot.recentUserActions`。没发出协助时不要停下来空等；**协助发出之后必须停自动点**，等用户做完或说继续。

## 停表

`ok: true` 不等于业务前进。

- `fill` / `choose` / `fill_fields` 之后：回显或随后请求里对应键必须出现或变化。填了请求完全没变，这一格失败，**没写上**。
- 你要 `fill`，工具就写，不会改口成下拉。回 `not_writable`：这一格写不进，只协助这一格。
- 你要 `choose`，工具点已经出现的可见原文。回 `option_not_seen`：看回报里打开后是列表还是日历；是日历就改 `fill` 日期值，还是没有就协助这一格。
- 工具报 `ok` 但随后保存/查询仍不带这个键：同样算没写上，不要当成已填。
- `click` 之后：`network_since` 没有预期的查询或写请求，就是点错了。禁止再用同一条 selector 连点。
- 工具返回 `not_found`：只重新 `snapshot`，只用**新列表**里的合法 selector。禁止改写成 `name=` / CSS / `#id` 再试，禁止改点没有业务文案的 `aN`。
- 同名「保存 / 确定 / 搜索 / 提交」：看 `region`，点当前表单或当前弹层那一个。点完没网，不要再点同一个 `role=button[name="保存"]`。
- 同一合法 selector 失败两次，或填了请求完全没变：`assist`，`reason` 只写**这一个控件**要人做什么。预览不锁。
- 某一格没写上：只协助这一格，**不要去点保存碰运气**。表单还是空的，禁止点保存换请求形状。
- 协助发出之后：工具会暂停自动点击。禁止再 click / fill / choose。只读 `recentUserActions` 和 `network_since`。直到用户说继续，不要自己再点。人已经发出预期请求就停手，交给识别 Skill 交能力。不要整张表重做。
- 人已经离开当前表单（回到列表、关掉弹层、打开另一页）：禁止再点刚才那张表的保存/确认。当前 snapshot 的 `region` 已经不是那张表，就不要再点。
- 当前页目标还没做完：禁止 `open_page` 去下一页。

写不进、点了不发网，属于这类失败，不要 invent selector 重试八次。

## 协助不是排他接管

只在这些情况用 `action=assist`：登录、验证码、授权写入、**写不进的那一个字段**、**点了不发网的那一个提交钮**、**加行后再 snapshot，这一列仍然没有可写控件**。`reason` 写清楚要人做什么。预览始终可以点。`assist` 会暂停自动点击，直到用户说继续；暂停期间再 click 会被拦住。不要把协助做成 takeover，不要让程序丢掉人的 `applyInput`。禁止一次 assist 把整张表交出去然后自己空转。协助后不要自己再点那一个钮。

## 禁止

- 不要锁预览、不要 `pointer-events: none` 盖住画布。
- 自动点击失败、空转或超时：只停自动点，不要结束录制，不要丢掉人的 `applyInput`。
- 不要等人全部点完再读几百条证据拼超大 JSON。
- 不要本地推断能力。字段合同仍按 `RECORDING_CAPABILITY.md`。
- 不要在本 Skill 里交能力或定稿。未接到用户结束，禁止 `submit_recording_result`。
- 不要用空表保存换请求形状。
