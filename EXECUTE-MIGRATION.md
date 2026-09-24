# 执行稿：按 Cursor 产出 Skill 的方式做录制

本文件只定做法。具体文件、函数和消息以同目录 `MIGRATION-IMPLEMENT.md` 为准。依据是：用户给出一个网页和要产出的内容，Cursor 实际会打开页面、按快照点、看请求、写一份 Skill、再把 Skill 里的命令跑一遍。产品把这几步固定下来。不要再设计能力信封、共用执行器或第二套页面协议。不要改 `E:\python\try\Dano` 的任何文件。新代码都写在 `playwright_CABP`，能新写的就新写。

开工目录：`E:\python\try\playwright_CABP`  
只读来源：`E:\python\try\Dano`  
任务书：`C:\Users\19419\Desktop\Skill 生成目标.md`

下文与 Dano 源码的函数签名、WebSocket 消息名冲突时，以被引用的源码为准。第 8 节点名丢掉的行为以本文件为准。

Node `>=22`。

---

## 1. Cursor 接到这个任务时实际怎么做

用户给出入口 URL，以及这段 Skill 要能做什么。目标原文整段留下，不缩成 `query` + `create`。

然后只做这一圈，做完才写文件：

1. 打开 Playwright Skill，按它操作，不另写一套选择器语言。顺序是：打开页面、`snapshot`、只用这张快照里的 ref 去 `click` / `fill` / `press`、导航或弹层后再 `snapshot`。ref 失效就重新 snapshot，不重试旧 ref，同名控件不取第一个。
2. 快照说不清（自定义下拉、日期、画布）时再 `screenshot`。图要作为图像进入这一轮，而不是只留下文件路径。
3. 点完去看网络。先列出这次动作附近的 xhr/fetch，再打开某一条的方法、URL、正文和响应。对不上就改一个字段再点一次，看哪个键变了。不用字段名像、值相等、排除法认定绑定。
4. 登录或验证码挡住时停下来，人在同一个浏览器里处理，然后从当前页接着做。不新开浏览器，不自己猜 token。
5. 页面和请求仍对不上时，才读这个网站已经加载的前端脚本，并和刚看到的请求核对。不读用户别的项目源码。
6. 写一份 Skill，形状与已经交过的点狮请假 Skill 相同：`SKILL.md` 写清何时用、调用方要提供什么、系统自己填什么、命令、写操作必须确认、401 就停。`scripts/` 里一个 Python 文件负责打这些接口。`references/api.md` 记下每个字段的四件事（是什么、调用方给什么、请求值从哪来、依据是哪次点击和哪条请求）、四种必填、绑定怎么定位、对不上就停止。录到的值只作例子，不写进下次的默认参数。
7. 把 `SKILL.md` 里的读命令真的跑一遍。失败就改脚本再跑。写命令只在用户这次目标要求写入、并且页面上已经发生过那次请求时，才把该命令标成已验证。不为了验证再提交一笔用户没要的业务。
8. 跑通的命令留在 `SKILL.md`。没跑通的写在 `references/api.md` 的未解决里，不出现在可执行命令中。

换一个网页就重新走 1 到 8。不把上一站的按钮名、path、字段来源带到下一站。

这一圈不能保证每个字段都认对。能保证的只有：点的时候服从当前快照；写进命令的读接口是跑通过的；没跑通的没有被写成能执行。

---

## 2. 产品里谁扮演 Cursor

Pi 扮演上面的操作者。运行时仍是 `@mariozechner/pi-coding-agent` 的 `createAgentSession`，然后 `session.prompt`。凭证在本目录新写，环境变量仍是 `DANO_PI_API_KEY` / `PI_API_KEY`、`DANO_PI_BASE_URL` / `PI_BASE_URL`、`DANO_PI_MODEL` / `PI_MODEL`、`DANO_PI_PROVIDER` / `PI_PROVIDER`。不要写裸 `chat/completions` 当驱动，不要新造一套模型变量。流补丁只在网关漏了 `finish_reason` 时补上，那不是驱动。

程序扮演终端和文件系统：提供这一只浏览器、把快照和请求按 id 存下来、把截图以图像消息送回 Pi、执行 Pi 写出的脚本、保存 token。程序不决定字段含义，不改 Pi 写好的 `SKILL.md` 和 `scripts/`。

停用 Dano 的 Skill 1–3、监控 PI、Skill 4、`submit_recording_capability`、`submit_recording_result`。不要加载那四份提示词。

用户路径三步：录制准备、页面录制、Skill 目录。没有能力结果页。

---

## 3. Pi 要加载的方法

两份全文放进仓库，禁止缩写成摘要。

Playwright Skill：执行 `npx --yes playwright-cli install --skills`，把安装结果放到 `skill/playwright-cli/SKILL.md`（含它的 references）。Pi 点击时服从这份 Skill 的 snapshot → ref → 再 snapshot。

derive-client：原样保存  
`https://github.com/vercel-labs/agent-browser/blob/main/skill-data/derive-client/SKILL.md`  
到 `skill/derive-client/SKILL.md`。Pi 写 `scripts/` 时服从它：按录到的请求写这个站自己的客户端，再真实调用读接口。

`systemPromptOverride` 只拼四段，顺序固定：

1. `src/agent/prompt.md`（第 4 节）
2. Playwright Skill 全文
3. derive-client 全文
4. 本场目标原文

`doc/` 里现有的四份 OA 指南不自动加载。只有本场请求是 JSON、鉴权在头里、并且确实有选项接口时，Pi 用工具读其中相关的一份，把约定写进这一份 Skill。对不上就不套，也不把它们复制进消费者包。

工具若需要再开一个浏览器，改为作用在第 5 节这一只已经开着的浏览器上。人和预览必须还在原画面。

---

## 4. `src/agent/prompt.md`

用中文写，不超过 80 行，内容只许是第 1 节那 8 步。不要出现某个系统的按钮名或 path。最后加三句：

- 每个可执行命令都要在 `references/api.md` 里指向证据 id。没有证据 id 的命令不要写进 `SKILL.md`。
- 同一 path 若新增和修改的请求条件不同，写成两个命令。顺序写在手册里；只有存在绑定才把上一步的值传入下一步。前一步失败就停，保留已完成的结果。
- 写完先跑读命令。失败就改脚本。通过后停止。

---

## 5. 浏览器与证据

依赖：`playwright@1.62.1`、`@mariozechner/pi-coding-agent@0.73.1`、`@sinclair/typebox@0.34.52`、`ws@8.18.3`。`npx playwright install chromium`。

一只有头 Chromium，一个 context，视口 1440×900。`loadStorageState(start_url)` 有则恢复。键是 origin，函数新写为 `loadStorageState(targetUrl)`、`saveStorageState(targetUrl, state)`，文件按 host 放在 `data/sessions/`。不要从登录响应猜 token，不要按录制号存登录态。

快照用 `frame.locator("body").ariaSnapshot()`。主 frame `f0`，其余按 url 与父序号为 `f1`、`f2`。ref 形如 `f0:e12`。同一名字两个控件就两个 ref。epoch 在导航或 DOM 大改后加 1，旧 ref 返回 `stale_ref` 和新快照，点击次数不变。

动作只接受当前 epoch 的 ref：`open`、`snapshot`、`click`、`fill`、`fill_fields`、`press`、`select`、`upload`、`screenshot`。`fill_fields` 每项自带 ref。填完读回值，日期、树、滑块读 `inputValue` 或 `aria-valuenow`，不一致返回 `value_not_applied`。下拉是两次动作：打开，再点新快照里的选项 ref。离开入口 origin 返回 `origin_denied`。不自动重试，不设三次失败上限。禁止用 `page.evaluate` 改框架内部状态来造请求。

网络只留 xhr/fetch，丢掉匹配 `/sockjs|websocket|favicon\.ico/i` 的 URL。正文按 id 存全文。取不到则 `body_missing: true`，不能当成成功。`action_id` 只标在该动作开始到新快照返回、最多 1500ms、且由该 frame 发起的请求上。其余只是候选。

截图工具返回 `{ __image: true, as_image: true, mimeType, data }`。包装在本目录新写：满足该标记时，工具结果里要有 `{ type: "image", mimeType, data }`。失败直接抛出。

证据种类：`snapshot`、`action`、`network`、`screenshot`、`asset`、`verify`。索引与正文分开。`get` 按 id 返回全文。默认上下文只带目标、最近一张快照、最近 30 条索引。

`goal.json` 保存 `start` 的原文，键为 `page_url`、`goal_text`、`capabilities`、`modes`、`caller_inputs`、`field_scope`、`exclusions`、`order`、`final_action`、`success`。现在前端只发 URL 和目标，其余为空字符串。Pi 补全时写入原句，不压缩。

鉴权新写：可用头丢掉空值、`[sealed:` 和 `****`；token 文件是 `data/tokens/<tenant>__<subsystem>.json`。不要从登录响应猜 token。

Pi 启动只取 `AuthStorage.create`、`ModelRegistry.create`、`applyPiModelConfig`、紧接着的 `installOpenAIToolCallStreamCompatibility`、`DefaultResourceLoader`、`SessionManager.inMemory()`、`noTools: "builtin"`、`customTools`、`systemPromptOverride`。不要整段粘贴 `createLivePiSession`。`agentDir` 为 `data/pi-agent`，`cwd` 为 `data/recordings/<id>/pi-cwd`。

工具名：`browser_open`、`browser_snapshot`、`browser_act`、`browser_screenshot`、`network_list`、`network_get`、`evidence_get`、`read_page_asset`、`read_guide`、`assist`、`append_goal`、`write_skill_file`、`read_skill_file`、`run_skill_command`、`verify_skill`。`read_guide` 只读 `doc/` 里已有的四份指南，且不打进消费者包。`append_goal` 只填 `goal.json` 里仍为空的键。`run_skill_command` 在包目录里执行 Pi 指定的读命令，把 stdout、stderr、退出码存成 `verify` 证据。

`start` 之后立刻 `session.prompt(目标原文 + URL)`。`assist` 后停止 prompt，直到 WebSocket 的 `steer` 或 `pi_message`。上一轮 prompt 没结束不要再调。`abort` / `stop_pi` / `cancel` 时 `dispose` 并 `saveStorageState`。

---

## 6. 产出的 Skill

目录 `data/skills/<subsystem>.<recording_id>/`。subsystem 来自 start，缺省 `app`。

```text
SKILL.md                  Pi 写。何时用、命令、调用方提供什么、确认、401 停、哪些命令已跑通
scripts/client.py         Pi 写。这个网站的客户端
references/api.md         Pi 写。字段四件事、必填种类、绑定、证据 id、未解决
config/runtime.json       程序写。tenant、subsystem、base_url。无密钥
config/auth.local.json    程序写。{ "headers": {} }
```

`write_skill_file` 只能写前三个。`SKILL.md` 的命令必须能在 `scripts/client.py` 里找到对应入口。写操作的命令行带确认开关。选项在运行时请求接口，不把录到的 label 写死。

`references/api.md` 里每个要执行的字段有：`page_name`、`caller_name`、`request_path`、`data_type`、`control`、`source`、`required_kind`、`format`、`group`、`evidence_ids`。`source` 只许是 `caller`、`current_user`、`now`、`previous_response`、`other_api`、`constant`、`unknown`。`required_kind` 只许是 `page`、`caller_all`、`server_verified`、`server_unknown`。`unknown` 放在「未解决」一节，且 `SKILL.md` 没有用它的命令。`constant` 要写原因，原因不能是「录到的就是这个」。

绑定要写从哪到哪、如何定位、如何转换、是否必须唯一、不匹配时停止还是停问、证据 id。

程序侧检查，失败就不是 `skill_ready`：

| code | 条件 |
| --- | --- |
| `missing_file` | 缺上面五个路径 |
| `secret_in_source` | 手册、脚本、references 含 token、password、cookie 值 |
| `command_not_run` | `SKILL.md` 里的读命令没有成功的 `verify` 证据 |
| `evidence_missing` | 字段或绑定引用的证据 id 不存在 |
| `unknown_in_command` | 未解决字段出现在可执行命令里 |
| `recorded_literal_default` | 脚本默认值与某条请求里该键全等，且没有常量原因 |
| `handbook_rewritten` | pack 之后 `SKILL.md` 或 `scripts/client.py` 与 Pi 写入的内容不一致 |

有鉴权头且上述通过：`skill_ready`。通过但没有头：`skill_written_needs_auth`。读命令的 401 记为 `auth_expired`，不用录制正文顶替。

`POST /v1/pi-recordings/:id/export-skill` 只在 `skill_ready` 时把目录复制到 `out_dir`。忽略 body 里的 `draft`。不要再生成一份手册。

---

## 7. 和现有前端

WebSocket `/onboarding/page/record`。

| 收到 | 做 |
| --- | --- |
| `start` | 使用现有字段 `start_url`、`goal_text`、`title`、`subsystem`、`storage_state`、`viewport`。写目标，恢复登录态，开浏览器，启动 Pi |
| `input` | 人的操作打到当前页 |
| `steer`、`pi_message` | 同一个 Pi 再 prompt |
| `abort`、`stop_pi`、`cancel` | 停止并保存 storageState |
| `ping` | `pong` |

发出 `snapshot`、`frame`、`thought`、`error`、`input_error`。结束时 snapshot 带 `status`、`skill_dir`。不要发 `recording_result_saved`。

HTTP：`GET /api/health`；`GET /v1/skills` 每项含 `name`、`skill_id`、`title`、`subsystem`、`description`、`parameters`（对象）、`recording_id`、`export_path`、`source: "playwright_cabp"`；`GET /v1/skills/:id` 返回手册前 200 行且不含头；`POST /v1/settings/token` 的 body 是 `{ tenant, subsystem, headers }`；`GET /v1/settings/token` 只返回 mask；`POST /v1/pi-recordings/:id/export-skill` 见上。不要实现 `POST /api/recordings`。端口 `18081`。

界面在 `playwright_CABP/web/` 新写，三步：录制准备、页面录制、Skill 目录。没有能力结果页。WebSocket 仍是 `/onboarding/page/record`，消息名仍是 `start`、`input`、`steer`、`snapshot`、`frame`。不要改 `E:\python\try\Dano` 的任何文件。

---

## 8. 不要搬的代码

`visible-controls.mjs`、`browser-capture.mjs`、`browser-snapshot.mjs`、`result-gate.mjs`、`result-merge.mjs`、`contract-materialize.mjs`、`templates/runtime.py`、`templates/flow.py`、四个录制 Skill、监控 PI、催交能力的定时器、按登录响应猜 token、`data-pi-ref`、按 label 取第一个。

---

## 9. 施工顺序

每段 `npm test` 通过再继续。

1. 安装依赖，放入两份 Skill 原文。health 返回 `playwright-cabp`。测试确认两份文件不是摘要：Playwright 那份含 snapshot 与 click，derive-client 那份含录请求再写客户端。
2. fixture：外层一个「提交」，iframe 里两个「提交」，只有 iframe 内第二个发 `POST /api/save`。过期 ref 不点；点外层不发 save；点中第二个才有 POST；填完读回失败是 `value_not_applied`。
3. `start` 后目标原文不变，证据按 id 能读全文，无正文则 `body_missing`，并收到 `frame`。
4. 假 Pi 走完第 1 节：snapshot、点中第二个提交、读那条请求、写入三件套、`run_skill_command` 跑读命令。`SKILL.md` 与写入内容一致。没跑的命令不能使状态变成 `skill_ready`。无 token 时为 `skill_written_needs_auth`。`pi-session.mjs` 含 `createAgentSession` 与 `session.prompt`。
5. 在 `web/` 写三步界面。预览仍是 `frame`，不出现能力结果。不改 Dano。

某个页面录错时，不要往提示词里加这个按钮或这条 path。先看是不是没用最新快照的 ref，是不是没把请求按 id 读全，是不是命令没跑就写进了手册。
