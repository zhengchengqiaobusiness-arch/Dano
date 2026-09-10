# Control In App Browser

本文件只负责：页面、弹窗、frame 和控件身份；快照与截图；点击、填写、选择；动作后验证；网络证据；按 ID 读；必要时读取同源前端。  
禁止：判断业务合同、划分能力、判定调用方或系统、`submit_recording_capability` / `submit_recording_result`。  
缺口只改本文件。

PI 是操作者。人同时也可以点预览。你们共用同一只 Playwright 浏览器、同一路预览画面、同一条证据。底层用动作队列串行。两条通道一直开着。不要锁死预览，不要丢弃人的点击。

默认由你自动点、填、选。不要等人先点一遍再模仿。只有 Investigator 判定阻断时才 `assist`。

可用 `action`：`open_page` / `list_pages` / `snapshot` / `screenshot` / `click` / `fill` / `select` / `choose` / `press` / `fill_fields` / `network_since` / `assist`。同源前端用独立工具 `read_page_asset`。不要发明业务 action。

## 页面、弹窗、frame、控件

定位必须对应当前页面、弹窗、frame、控件身份。看 `controls` / `actions` 的 selector 和 `region`（filter / form / table / dialog）。只点当前业务区。

重名返回候选，禁止默认第一个。工具会回 `ambiguous` 和候选 `ref` / `region`。你再按区域点那一个。列表里多个「提交审批 / 修改 / 删除」：先搜到只剩目标那一行，再点 `region=table` 这一行的按钮。不要点列表里出现的第一个同名钮。

## 合法 selector

运行时只认 snapshot 当场广告的这些写法。优先用 `placeholder=` / `label=` / `role=`，也可以用 `ref=cN` / `ref=aN`。

- 输入：`placeholder=原文` 或 `label=原文` 或 `ref=cN`
- 按钮：`role=button[name="原文"]` 或 `text=原文`
- 勾选：`role=checkbox[name="原文"]` 或 `type=checkbox`
- 单选：`role=radio[name="原文"]` 或 `type=radio`
- 下拉、分段、单选、页签：对宿主 `choose(selector, 可见选项原文)`，点已经出现的那一项。工具回「需要可见选项原文」或 `option_not_seen`、打开后是列表不是日历：先点宿主打开下拉，再 snapshot **当前弹层**，用弹层里已经出现的选项原文 `choose`。禁止对空选项、未知车牌、未知人名再 choose。
- 树 / 列表节点：`text=原文` 或 `choose` 已经出现的可见原文（含打开后的 vue-treeselect / el-tree / ant-tree 节点）。先点开宿主再 snapshot 一次；禁止因为上一张 snapshot 没有树节点就协助。

禁止：`name=`、`#id`、`.class`、xpath、任意 CSS，以及 snapshot 里没有的字符串。不要把失败后的猜测写成新 selector。没有业务文案的 `aN`、纯数字角标、顶栏头像不要点。snapshot 若仍列出它们，忽略。

`readonly`/`disabled` 表示整个控件不能改，不是下拉内部展示框带了原生 readonly。

## 快照与截图

默认用 `snapshot` 看结构和控件。不要每个字段都 snapshot。

需要看真实画面时：`screenshot` 且 `as_image=true`。必须以 Pi 图像消息送给模型，禁止只给路径并声称看见了图。默认 `as_image=false` 只回摘要，避免每步灌图。

## 点击、填写、选择

值由 Investigator 指定。工具不自填样例，不整表自动填充。多个普通输入框：一次 `fill_fields`。每项只写该项自己的 `ref`，禁止把标题写进日期框、把日期写进标题框。

选项、分段、单选、页签、已经出现的树/列表节点用 `choose(selector, 可见原文)` 或 `text=`。不要 click 后再猜新 selector。筛选框只过滤；列表/树变了还要选已经出现的可见节点。

打开弹层、切换页签、跳进新表单、或加行后再 snapshot **一次**。认的是**当前这一页、当前这个表单**的控件，不要拿上一页列表的筛选条来填这一张表。点「新增」或点进一行之后，先核对当前 URL / 标题 / 单据名 / 单号前缀是否还是目标那一类单据。入口路径或标题已经带类型，表单却落到没有类型的通用新增页，或变成另一类单据（外出/请假/加班等）：不要在这张错表上保存、提交、删除或当编辑。回到入口再点该页自己的新增；或 `open_page` 仍带该类型的新增地址。禁止用无类型通用表或别族单据冒充目标。

加行后用新列表里 `region=table` 的 `label=表头` / `placeholder=` 写每一列；不要把整张表当成一个控件。每一种「添加××」分区都要单独加一次、写一次，每加一种分区再 snapshot 一次，好让表头和分区标题进证据。只有这一列在新 snapshot 里仍然没有可写控件时，才协助这一格。

灰框宿主（整控件 `readonly`/`disabled`）不要硬 `fill`。若同一表单项旁边有可见文案「选择 / 选人 / 选部门」，这是选人链接，不是登录阻断：`click label=字段名`（例如 `label=责任人`）打开弹层。多个同名「选择」不要默认第一个，也不要 assist；看 ambiguous 候选的 `section`，用对应字段的 `label=`。弹层打开后再 snapshot，按已经出现的人名/部门 `text=` 或勾选，再点弹层 `确定` / `确认`。禁止为选人/选部门 assist。日期、树、下拉、滑块必须验证值已真正提交到控件。支持动态新增行。

没有独立请求键的页签/折叠头（只有文案、点了不改 execute 形状）不是输入框，不要当字段去填。

## 动作后验证

`ok: true` 不等于业务前进。工具会回报 `host_value` 和随后请求摘要。

- `fill` / `choose` / `fill_fields` 之后：回显或随后请求里对应键必须出现或变化。填了请求完全没变，回 `not_applied`，**没写上**。
- `choose` 之后 `host_value` 常常是 `"on"`、空串或宿主旧值。这不是失败。以随后请求里该选项对应的业务键为准：键变了或带上了所选原文/接口值，就算选上。不要因为 `host_value=on` 就协助或重选。
- 你要 `fill`，工具就写，不会改口成下拉。回 `not_writable`：这一格写不进，只协助这一格。
- 你要 `choose`，工具点已经出现的可见原文。回 `option_not_seen`：看回报里打开后是列表还是日历。是日历时不要 `choose` 日期原文（格子不是选项）。再 snapshot **当前弹层**，填弹层里已经出现的「开始日期 / 结束日期 / 开始时间 / 结束时间」，再点弹层的「确定」。宿主框（placeholder 带「→」的区间）直接 `fill` 整段「起 - 止」常常写不上；`host_value` 仍空就不要用同一串再填宿主。点弹层「确定」后宿主仍是 `-` 或空：禁止保存，再开弹层用 placeholder 填齐四格，不要 `cN`。`range=true` 是一个控件，起止都要有。还是没有就协助这一格。
- `click` 之后：`network_since` 没有预期的查询或写请求，就是点错了。禁止再用同一条 selector 连点。点「搜索」后列表 URL/query 若与首屏相同（筛选项没进请求）：查询没发出，不要当做成。有请求还要看响应：HTTP 非成功，或 JSON `code` 不是成功、`msg` 带失败：动作没做成，回报 Investigator，不要当成功离开。禁止 `fill cN` 填单据编号或筛选框，用 `placeholder=` / `label=`。占用图先 `choose` / `placeholder=` 选资源名和日期，再点「可预约」格子；总时间段为 0 时不要乱填 `cN`，去同菜单配置页看读接口。配置页只有「保存」、没有删除钮：不要再点保存。点了列表行上的「提交 / 送审 / 删除 / 撤销 / 撤回」后若没立刻发网：先 snapshot **当前弹层**（`region=dialog`，文案常是「确认要…吗」），再点弹层里的 `role=button[name="确定"]` 或 `text=确定`。禁止改点无文案 `aN`。点错成「取消」就停，重新点那一行业务钮，不要连点同一个确定。
- `not_found`：回报附带最新无图 snapshot。只用**新列表**里的合法 selector。禁止改写成 `name=` / CSS / `#id` 再试。
- `ambiguous`：多个可见命中。按 `region` 选当前表单或当前弹层那一个。
- 同名「保存 / 确定 / 搜索 / 提交」：看 `region`，点当前表单或当前弹层那一个。

失败码分开，禁止用固定三次代替判断：`not_found` / `not_writable` / `option_not_seen` / `not_selected` / `not_applied` / `ambiguous` / `assist_hold` / `transport_idle`。业务上算哪一种失败，由 Investigator 定性。

## 取证边界

打开某一张表单：从**点开它的那一次 click** 起看 `network_since`，不要把上一页列表加载、通知未读、字典菜单算进这张表。  
当前页目标已经做完：离开这一页，不要为「再看一眼」回到上一页重查。Investigator 点名补证除外。

## 网络与按 ID 读

`network_since`（刚打开用 `after_seq=0`；进新表单用点开它之后的 seq）看动作前后请求。细节看 `list_recording_index` / `read_request_shape` / `read_evidence_item` / `read_response_blob`。`read_response_blob` 只接受 `blob_` 开头的 id，不要把 `request_id` 当 blob。

正文未取得时工具会标缺，不要推断业务成功。

## 同源前端

页面与请求对不上时，用 `read_page_asset({url})`。仅本场已加载的同源 URL。禁止读用户其他项目代码。公开前端是辅助证据，必须与实际页面或请求交叉核对。

## 协助

只在这些情况用 `action=assist`：登录、验证码、授权写入、**写不进的那一个字段**、**点了不发网的那一个提交钮**、**加行后再 snapshot，这一列仍然没有可写控件**。选人/选部门的「选择」链接和选人弹层不是协助点。`reason` 写清楚要人做什么。当前 snapshot 已经是目标业务页时，禁止再 assist 登录或验证码。预览始终可以点。`assist` 会暂停自动点击，直到用户说继续；暂停期间再 click 会被拦住，回 `assist_hold`。不要把协助做成 takeover。禁止一次 assist 把整张表交出去然后自己空转。

协助发出之后：禁止再 click / fill / choose。只读 `recentUserActions` 和 `network_since`。直到用户说继续，不要自己再点。人已经发出预期请求就停手，交给 Investigator 去调 Infer。不要整张表重做。

人已经离开当前表单（回到列表、关掉弹层、打开另一页）：禁止再点刚才那张表的保存/确认。当前 snapshot 的 `region` 已经不是那张表，就不要再点。当前页目标还没做完：禁止 `open_page` 去下一页。保存已业务成功但当前落到 404 或非该入口页：`open_page` 回该入口列表继续行操作，不要当失败定稿。登录需要用户处理时保留当前会话。

不是协助：人还没说话、你还想让人确认目标、你想等人说结束。这些都不停自动点。

## 禁止

- 不要锁预览、不要 `pointer-events: none` 盖住画布。
- 自动点击失败、空转或超时：只停自动点，不要结束录制，不要丢掉人的 `applyInput`。空转是 `transport_idle`，不是业务失败。
- 不要等人全部点完再读几百条证据拼超大 JSON。
- 不要本地推断能力，不要交能力或定稿。
- 不要改 Vue、React 或其他框架的内部业务状态来「修好页面」。
- 正常交互形不成有效请求：报告实际问题，不能制造修补后的请求再当作自然页面行为。
- 批量填写只接受指定输入，不自行决定所有业务样例。
