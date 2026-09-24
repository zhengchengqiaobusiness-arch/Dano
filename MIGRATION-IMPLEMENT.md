# 施工说明：playwright_CABP

发给 Cursor 执行。做法以同目录 `EXECUTE-MIGRATION.md` 为准：Pi 按 Playwright Skill 点页面，按 derive-client 为这一站写客户端，把读命令跑通后才算 Skill。本文件只补「文件怎么建、函数怎么签、消息长什么样」。不要再设计别的架构。

不要改 `E:\python\try\Dano` 里的任何文件，包括 `Pi_check` 和 `skillfrontend`。新界面写在本目录 `web/`。能新写的模块都新写。只有 Pi SDK、Playwright、两份 Skill 原文、已有的 `doc/` 四份指南不重写。

Node `>=22`。端口 `18081`。数据目录 `data/`。

---

## 1. 包与目录

在 `E:\python\try\playwright_CABP` 执行：

```bash
npm install playwright@1.62.1 @mariozechner/pi-coding-agent@0.73.1 @sinclair/typebox@0.34.52 ws@8.18.3
npx playwright install chromium
npx --yes playwright-cli install --skills
```

`package.json`：

```json
{
  "name": "playwright-cabp",
  "private": true,
  "type": "module",
  "engines": { "node": ">=22" },
  "scripts": {
    "start": "node --env-file=.env src/server.mjs",
    "test": "node --test test/*.test.mjs",
    "check": "node --check src/server.mjs && npm test"
  },
  "dependencies": {
    "playwright": "1.62.1",
    "@mariozechner/pi-coding-agent": "0.73.1",
    "@sinclair/typebox": "0.34.52",
    "ws": "^8.18.3"
  }
}
```

`.gitignore` 追加 `node_modules/`、`data/`、`test/tmp/`。不要忽略 `skill/`。不要提交 `.env`。

目录：

```text
skill/playwright-cli/SKILL.md      playwright-cli install --skills 的原文，含 references
skill/derive-client/SKILL.md       见第 2 节，原文
src/paths.mjs
src/server.mjs                     HTTP
src/ws-bridge.mjs                  /onboarding/page/record
src/session.mjs                    一场录制
src/browser/session.mjs
src/browser/snapshot.mjs
src/browser/actions.mjs
src/browser/network.mjs
src/evidence/store.mjs
src/agent/pi-model.mjs             新写，行为见第 3 节
src/agent/openai-stream-compat.mjs 新写，行为见第 3 节
src/agent/pi-session.mjs
src/agent/tools.mjs
src/agent/prompt.md
src/skillpack/files.mjs            白名单写入
src/skillpack/verify.mjs
src/skillpack/catalog.mjs
src/auth-vault.mjs
src/session-store.mjs
src/token-store.mjs
web/                               新界面，三步，不改 Dano 前端
test/fixtures/iframe-form.html
test/*.test.mjs
doc/                               已有四份指南，不改语义，不打进消费者包
```

`src/paths.mjs`：

```js
export function dataRoot()
export function recordingDir(id)
export function skillDir(skillId)
export function tokenDir()
export function sessionDir()
```

`dataRoot()` = `process.env.CABP_DATA` 或包根下 `data`。录制号必须是 `rec_` 加 32 位十六进制，与 `Pi_check/src/evidence-store.mjs` 里 `rec_${randomUUID().replaceAll("-", "")}` 相同。前端 `exportRecordingSkill` 拒绝不是 `rec_` 开头的 id。

---

## 2. 两份 Skill 原文

`skill/playwright-cli/SKILL.md` 来自 `playwright-cli install --skills` 的产物。测试要求文件含 `snapshot` 和 `click`，且超过 40 行。禁止手写缩写。

`skill/derive-client/SKILL.md` 取自：

`https://raw.githubusercontent.com/vercel-labs/agent-browser/main/skill-data/derive-client/SKILL.md`

保存原文。测试要求含 “HAR” 或 “record”，且超过 40 行。取不到该 URL 时停止施工并报告，不要自己编一份。

---

## 3. 新写的运行时，不拷文件

不要把 Dano 的 `.mjs` 拷进本目录。下面这些行为新写，因为换一套就会接不上现有的 Pi 凭证或图像工具。

### 3.1 模型

`src/agent/pi-model.mjs` 新写 `readPiModelEnv`、`applyPiModelConfig`。读这些环境变量，不要增加 `CABP_MODEL_*`：

`DANO_PI_API_KEY` 或 `PI_API_KEY` 或 `ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY`；`DANO_PI_BASE_URL` 或 `PI_BASE_URL`；`DANO_PI_MODEL` 或 `PI_MODEL`；`DANO_PI_PROVIDER` 或 `PI_PROVIDER`。provider 为空时：只有 `ANTHROPIC_API_KEY` 则为 `anthropic`，只有 `OPENAI_API_KEY` 则为 `openai`，否则 `openai-compat`。

`applyPiModelConfig`：有 key 就 `authStorage.setRuntimeApiKey(provider, apiKey)`。同时有 `baseUrl`、`apiKey`、`modelId` 时 `modelRegistry.registerProvider(provider, { name, baseUrl, api: "openai-completions", apiKey, models: [{ id, name, reasoning: false, input: ["text", "image"], cost 全 0, contextWindow 默认 198000, maxTokens 默认 32768 }] })`。窗口和 maxTokens 分别读 `DANO_PI_CONTEXT_WINDOW` / `PI_CONTEXT_WINDOW`、`DANO_PI_MAX_TOKENS` / `PI_MAX_TOKENS`。然后 `modelRegistry.find(provider, modelId)`。找不到就抛错，写明 provider、model、key 是否设置。返回 `{ model, provider, modelId, keySet, baseUrl }`。

### 3.2 流补丁

`src/agent/openai-stream-compat.mjs` 新写，不要从 Dano 拷。`installOpenAIToolCallStreamCompatibility({ baseUrl, onRepair })` 包一层 `globalThis.fetch`。只处理 URL 以该 `baseUrl` 开头、路径含 `/chat/completions`、响应是 `text/event-stream` 且状态成功的请求。若 SSE 里已有完整的 tool call 名字和 JSON 参数，但在 `[DONE]` 之前没有 `finish_reason`，就在 `[DONE]` 前插入一条 `finish_reason: "tool_calls"`。已有 `finish_reason`、有 `error`、或参数 JSON 不完整时不改正文。

### 3.3 登录态、头、token

都新写。

`src/session-store.mjs`：`loadStorageState(targetUrl)`、`saveStorageState(targetUrl, state)`。文件是 `data/sessions/<host>.json`，键是 URL 的 origin，不是录制号。

`src/auth-vault.mjs`：`usableAuthHeaders` 丢掉空值、`[sealed:` 开头、含 `****` 的头。`hasCredentialHeaders` 表示还剩可用头。`canonicalHeaderName` 统一大小写。`asAuthorization` 把裸 token 收成 `Bearer ...`，已有 Bearer 不重复加。`readAuthVault` / `writeAuthVault` 读写 `data/recordings/<id>/auth-vault.json`。不要从登录响应或页面存储里猜 token。

`src/token-store.mjs`：`data/tokens/<tenant>__<subsystem>.json`。`readTokenRecord`、`writeTokenRecord`、`maskHeaders`（保留前 4 后 4，短于 8 个字符则 `****`）、`writeAuthLocalFile(packageDir, headers)` 只写该目录的 `config/auth.local.json`。

### 3.4 工具包装

`src/agent/tools.mjs` 里新写包装，不要拷 `pi-tools.mjs`。`defineTool` 来自 `@mariozechner/pi-coding-agent`。参数用 TypeBox：string、boolean、integer 按类型；object 和 array 收成宽松对象，执行前若值是 JSON 字符串再 `JSON.parse`。

工具返回 `{ __image: true, data, mimeType, as_image: true }` 时，给 Pi 的结果是：

```js
{
  content: [
    { type: "text", text: JSON.stringify({ image_in_conversation: true }) },
    { type: "image", mimeType, data }
  ]
}
```

其它结果是 `{ content: [{ type: "text", text: JSON.stringify(payload) }], details: payload }`。失败直接抛出。不要吞掉错误去提示「交能力」或「交结果」。

---

## 4. 浏览器

`src/browser/session.mjs`

```js
export async function openBrowser({ recordingId, url, storageState, viewport })
export function getPage(recordingId)
export async function closeBrowser(recordingId)
export async function captureFrame(recordingId) // { data: base64jpeg, width, height }
```

`chromium.launch({ headless: false })`。`newContext({ storageState, viewport: viewport || { width: 1440, height: 900 } })`。`storageState` 为 null 时不传该字段。

`captureFrame` 使用 `page.screenshot({ type: "jpeg", quality: 60 })`，`data` 是不含 `data:image` 前缀的 base64。前端 `queueFrame` 会自己加上 `data:image/jpeg;base64,`。

`src/browser/snapshot.mjs`

```js
export async function takeSnapshot(recordingId)
// { epoch, text, refs }
```

遍历 `page.frames()`。主 frame 的 `frame_id` 为 `f0`。其余 frame 排序键是 `parent._guid 或 url + url`，同一页面重复调用序号不变。每个 frame：`frame.locator("body").ariaSnapshot()`。给可交互节点编号 `e1`…，对外 ref 为 `` `${frameId}:${localRef}` ``。同一 accessible name 出现两次，两条都保留，文本中带上最近的 heading 或 region。把 ref 映射到该 epoch 的 Locator，存在 session 上。导航（`framenavigated`）时 epoch 加 1 并清空映射。

`src/browser/actions.mjs`

```js
export async function runAction(recordingId, input)
```

`input.action` 只许：`open`、`snapshot`、`click`、`fill`、`fill_fields`、`press`、`select`、`upload`、`screenshot`。

`open` 的 url 的 origin 必须等于本场入口 origin，否则 `{ ok: false, error: "origin_denied", snapshot }`，不调用 `goto`。

`click`、`fill`、`press`、`select`、`upload` 只用当前 epoch 的 Locator。映射里没有该 ref：不点击，返回 `{ ok: false, error: "stale_ref", snapshot }`，snapshot 是刚取的新快照。不自动重试。

`fill` 后 `inputValue()`。与 `text` 不一致则 `{ ok: false, error: "value_not_applied", snapshot }`。contenteditable 改为读 `innerText`，须包含 `text`。日期、树、滑块同样读 `inputValue` 或 `aria-valuenow`。`fill_fields` 是 `[{ ref, text }]`，任一项失败则整次失败并带上该项 ref。`upload` 用 `setInputFiles`。`select` 用 `selectOption`。

没有 `choose`。下拉由 Pi 分两次调用：`click` 打开，再对返回快照里的选项 ref `click`。

`screenshot` 返回 `{ ok: true, __image: true, as_image: true, mimeType: "image/png", data }`，`data` 为 base64。

禁止 `page.evaluate` 写入 Vue 或 React 的状态。

`src/browser/network.mjs`

监听 context 的 request/response。只保留 `resourceType` 为 `xhr` 或 `fetch`。URL 匹配 `/sockjs|websocket|favicon\.ico/i` 的丢掉。

```js
{
  id: "req_1",
  method, url, path, query, post_data,
  status, response_body, body_missing,
  started_at, ended_at, action_id
}
```

正文写入 `data/recordings/<id>/blobs/<req_id>.json`。响应体没拿到时 `body_missing: true`，`response_body` 为 null。`list({ after_id, action_id })` 只返回 `id, method, path, status, body_bytes, body_missing, action_id`。

`action_id`：`runAction` 开始时生成 `act_<n>` 并传给 network。该动作返回前结束、且请求 frame 等于动作 frame 的 xhr/fetch 写上这个 `action_id`。超过 1500ms 才结束的请求不写 `action_id`。

---

## 5. 证据与目标

`src/evidence/store.mjs`

```js
export async function appendEvidence(recordingId, record)
export async function listEvidence(recordingId, { after_id, kinds, limit })
export async function getEvidence(recordingId, evidenceId)
```

`data/recordings/<id>/evidence.jsonl` 每行一个索引，不含大正文。正文在 `blobs/`。种类：`snapshot`、`action`、`network`、`screenshot`、`asset`、`verify`。`getEvidence` 把 blob 读全返回。没有截断。

`data/recordings/<id>/goal.json` 在 `start` 时写入，之后只允许 Pi 通过工具 `append_goal` 追加空着的键，不能改写已有非空字符串。键固定为：

`page_url`、`goal_text`、`capabilities`、`modes`、`caller_inputs`、`field_scope`、`exclusions`、`order`、`final_action`、`success`。

`start` 只填 `page_url`（来自 `start_url`）和 `goal_text`。其余 `""`。禁止把 `goal_text` 改成 `query` 或 `create`。

---

## 6. Pi 会话

`src/agent/pi-session.mjs` 导出 `startRecordingPi({ recordingId, tools })`。

顺序：

1. `AuthStorage.create(data/pi-agent/auth.json)`。
2. `ModelRegistry.create(authStorage, data/pi-agent/models.json)`。
3. `applyPiModelConfig(authStorage, modelRegistry)`。
4. `installOpenAIToolCallStreamCompatibility({ baseUrl: resolved.baseUrl })`。
5. `new DefaultResourceLoader({ cwd, agentDir, systemPromptOverride })`。`systemPromptOverride` 返回四段拼接：`src/agent/prompt.md`、`skill/playwright-cli/SKILL.md`、`skill/derive-client/SKILL.md`、`goal.json` 的 JSON。不要读 Dano 的四个 Skill。
6. `createAgentSession({ cwd: data/recordings/<id>/pi-cwd, agentDir: data/pi-agent, authStorage, modelRegistry, model, noTools: "builtin", tools: 第 7 节的名字数组, customTools, resourceLoader, sessionManager: SessionManager.inMemory() })`。`tools` 数组与 `customTools` 的 name 一致。
7. `session.prompt(goal_text + "\n" + page_url)`。

`assist` 工具返回后，会话对象上 `paused = true`，不要再 `prompt`。对外 `status` 用已有的 `waiting_operator`，并带上 `assist.reason`。进行中的 `status` 只用 `recording` 与 `waiting_operator`。不要发 `assist`。结束状态见第 11 节，由 `web/` 留在预览页。WebSocket 收到 `steer` 或 `pi_message` 时，若上一轮 `prompt` 的 Promise 已结束，再 `session.prompt("人已继续，从当前页面接着做")`。Promise 未结束则把这句话排队，结束后再发。不要 `dispose` 再新建。

`abort`、`stop_pi`、`cancel`：`session.dispose()`，`saveStorageState(start_url, await context.storageState())`，状态 `stopped`。

不要调用 `streamingBehavior: "steer"` 的定时器。

`src/agent/prompt.md` 不超过 80 行，只写 `EXECUTE-MIGRATION.md` 第 1 节的 8 步，外加该文件第 4 节的三句。不要写具体网站的按钮或 path。

---

## 7. 工具

实现在 `src/agent/tools.mjs`，用第 3.4 节的包装交给 Pi。host 方法名与下表一致。参数用 JSON Schema，`toTypeBox` 会把 object 和 array 收成宽松对象，所以 `fill_fields` 的 `fields` 在实现里再校验每一项都有字符串 `ref` 和 `text`。

| name | 参数 | 行为 |
| --- | --- | --- |
| `browser_open` | `url` | `runAction` open |
| `browser_snapshot` | 无 | `takeSnapshot`，并 `appendEvidence` kind snapshot |
| `browser_act` | `action, ref, text, fields, key, file_path` | `runAction`。成功或失败都写入 kind action |
| `browser_screenshot` | `ref` 可空 | 必须 `as_image: true` 传给包装器，返回 `__image` |
| `network_list` | `after_id, action_id` 可空 | 索引 |
| `network_get` | `id` | 全文。`body_missing` 原样返回 |
| `evidence_get` | `id` | 全文 |
| `read_page_asset` | `url` | 仅本场已捕获、与入口同源、content-type 含 javascript 的响应体 |
| `read_guide` | `name` | 只许读 `doc/` 下已有的四个文件名。工具说明写明：仅当本场是 JSON、鉴权在头里、且有选项接口时才读。不把文件复制进 Skill 包 |
| `assist` | `reason` | 置 `paused`，WebSocket snapshot 的 `assist.reason` 为该字符串，`human_can_click` 为 true |
| `append_goal` | `key, text` | 只填充 goal.json 里当前为 `""` 的键 |
| `write_skill_file` | `relative_path, contents` | 第 8 节白名单 |
| `read_skill_file` | `relative_path` | 读已写文件 |
| `run_skill_command` | `argv` 字符串数组 | 第 9 节 |
| `verify_skill` | 无 | 先写两个 config 文件，再跑 `verifySkill`。通过后把状态设为第 9 节的结果，并停止再 `prompt` |

没有 `submit_recording_capability`，没有 `submit_recording_result`。

`browser_act` 的 description 写明：ref 来自最近一次 snapshot，格式 `fN:eN`；失败使用返回的新快照；不要用旧 ref；同名不要请求程序挑第一个；下拉分两次 click。

---

## 8. Skill 文件

根目录 `data/skills/<skillId>/`。`skillId = `${subsystem}.${recordingId}``。subsystem 来自 start，空则 `app`。只保留 `[A-Za-z0-9_-]`。`recordingId` 已含 `rec_`。

| 相对路径 | 谁写 |
| --- | --- |
| `SKILL.md` | Pi |
| `scripts/client.py` | Pi |
| `references/api.md` | Pi |
| `config/runtime.json` | `verify_skill` 在校验前写入，只写 `config/` |
| `config/auth.local.json` | 同上 |

`write_skill_file` 拒绝 `config/` 和这三个路径以外的文件，返回 `{ ok: false, error: "frozen_file" }`。

`config/runtime.json`：

```json
{ "tenant": "", "subsystem": "app", "base_url": "" }
```

`base_url` 取入口 URL 的 origin。没有则 `""`。

`config/auth.local.json`：`{ "headers": { } }`。头来自 `usableAuthHeaders`：先本场 `auth-vault`，没有再用 `readTokenRecord(tenant, subsystem)`。值仍像 `[sealed:` 或 `****` 的丢掉。

`SKILL.md` 第一段是这一份 Skill 做什么（来自目标原文）。后面是命令。每条读命令单独一行，且能在 `scripts/client.py` 里找到同名入口。写命令的那一行含 `--confirm`。手册不得含字面 token。

`references/api.md` 用下面小标题，校验按标题解析，不要换标题：

```text
## 字段
- page_name:
- caller_name:
- request_path:
- data_type:
- control:
- source:
- required_kind:
- format:
- group:
- evidence_ids:
- constant_reason:

## 绑定
- from:
- to:
- locate:
- transform:
- unique:
- on_mismatch:
- evidence_ids:

## 未解决
- field:
- reason:
- evidence_ids:

## 已验证读命令
python scripts/client.py <子命令> ...
```

`source` 只许 `caller`、`current_user`、`now`、`previous_response`、`other_api`、`constant`、`unknown`。`required_kind` 只许 `page`、`caller_all`、`server_verified`、`server_unknown`。`on_mismatch` 只许 `stop` 或 `ask`。`unknown` 的字段名必须出现在「未解决」，且不出现在 `SKILL.md` 的命令说明里。`constant` 的 `constant_reason` 非空，且理由文本不是「录到的就是这个」。

---

## 9. 跑命令与校验

`run_skill_command`：`argv[0]` 必须是 `python` 或 `python3`，`argv[1]` 必须是 `scripts/client.py`。`spawn` 的 `cwd` 是该 Skill 目录。环境变量加上 `CABP_AUTH_FILE` 指向 `config/auth.local.json`。超时 30 秒。退出码 0 才把证据记为 `{ kind: "verify", ok: true, argv, stdout, stderr }`。非 0 记 `ok: false`。不把录制响应写进 stdout 来冒充成功。

`src/skillpack/verify.mjs` 导出 `verifySkill(skillDir, recordingId)`。

| code | 判定 |
| --- | --- |
| `missing_file` | 第 8 节五个路径缺一 |
| `secret_in_source` | `SKILL.md`、`scripts/client.py`、`references/api.md` 匹配 `/Bearer\s+[A-Za-z0-9._\-]+/`、`password` 后接赋值、或 `cookie` 后接长令牌。`auth.local.json` 不参与这条 |
| `command_not_run` | `SKILL.md` 里以 `python scripts/client.py` 或 `python3 scripts/client.py` 开头、且不含 `--confirm` 的每一行，都要有一条 `ok: true` 的 `verify` 证据，`argv` 用空格连起来与该行相同（忽略首尾空白）。这些行也必须出现在 `references/api.md` 的「已验证读命令」里 |
| `evidence_missing` | `evidence_ids` 里的 id 在本场 evidence 中不存在 |
| `unknown_in_command` | 「未解决」的 `field` 作为整词出现在 `SKILL.md` |
| `recorded_literal_default` | `scripts/client.py` 函数默认参数里，长度不少于 8 的字符串，与某条请求 JSON 的字符串值全等，且 `references/api.md` 里没有同名字段同时满足 `source: constant` 与非空 `constant_reason` |
| `handbook_rewritten` | pack 前后 `SKILL.md` 与 `scripts/client.py` 的 sha256 变化。pack 只许写 `config/` |

有 `usableAuthHeaders` 且 `errors` 为空：会话 `status = "skill_ready"`。`errors` 为空但没有可用头：`skill_written_needs_auth`。有 errors：`verify_failed`。读命令退出码对应 HTTP 401：额外 code `auth_expired`，状态不是 `skill_ready`。

`verify_skill` 在校验前写入两个 config 文件。发现 `handbook_rewritten` 则失败。通过后不再 `prompt`。

---

## 10. 目录接口

`src/skillpack/catalog.mjs` 写入 `data/skill-catalog.json`，项的字段与 `skillManifestFromExport` 对齐，供 `Skills.tsx` 的 `SkillManifest`：

```js
{
  name: skillId,
  skill_id: skillId,
  tenant, subsystem, action: recordingId,
  title, description, integration: "page",
  risk_level: "L1",
  parameters: { type: "object", properties: {}, additionalProperties: true },
  recording_id: recordingId,
  result_id: recordingId,
  export_path: skillDir,
  source: "playwright_cabp",
  created_at, updated_at, version: 1, frozen: false
}
```

`title` 取 `goal.json` 的 `goal_text` 前 80 字，空则用 `skillId`。

`GET /v1/skills` 返回 `{ items, total, page, page_size }`。无分页参数时 `page` 为 1，`page_size` 为全部。每项必须有 `name` 和 `parameters` 对象。

`GET /v1/skills/:id` 返回手册前 200 行、`runtime.json`，不返回 `auth.local.json`。

`POST /v1/settings/token` 的 body 是 `{ tenant, subsystem, headers }`。写入后，把目录里同一 `subsystem` 的已导出包的 `config/auth.local.json` 更新为这些头。响应里的头走 `maskHeaders`，并带 `updated_packages`。

`GET /v1/settings/token` 查询参数带 `tenant`、`subsystem`。只返回是否存在和 mask。

`POST /v1/pi-recordings/:id/export-skill`：`:id` 必须 `startsWith("rec_")`。状态不是 `skill_ready` 时返回 `{ status: "not_ready", verify }`。是则把 Skill 目录复制到 body `out_dir`（没有 `out_dir` 就复制到 `data/export/<skillId>`），返回 `{ status: "exported", skill_id, export_path }`。不读 body.`draft`。

`GET /api/health` 返回 `{ ok: true, service: "playwright-cabp" }`。

不要注册 `POST /api/recordings`。

CORS：`http://127.0.0.1:5173` 与 `http://localhost:5173`。

---

## 11. WebSocket

`src/ws-bridge.mjs` 在 HTTP server 的 `upgrade` 上挂 `ws`。路径只接受 `/onboarding/page/record`，其它 upgrade 销毁 socket。

收到 JSON：

| type | 字段 | 行为 |
| --- | --- | --- |
| `ping` | | `{ type: "pong" }` |
| `start` | `start_url`、`goal_text`、`title`、`tenant`、`subsystem`、`storage_state`、`viewport` | 生成 `rec_...`，记下 `tenant`。写 goal，`loadStorageState(start_url)`，`openBrowser`，立刻推一帧，再 `startRecordingPi`。`resume_action`、`machine_verification`、`base_url` 忽略。`runtime.json` 的 `base_url` 用 `start_url` 的 origin |
| `input` | `event.kind` 为 `pointer_move`、`pointer_down`、`pointer_up`、`scroll`、`text`、`key` | 坐标与 `browser-capture.mjs` 的 `#applyInputNow` 相同：`x`、`y` 是有限数就用，否则 `nx * viewport.width`、`ny * viewport.height`。`pointer_move` 至少间隔 50ms。`scroll` 用 `dx`、`dy`。`text` 用 `keyboard.type`，`key` 用 `keyboard.press`。不认识的 kind 回 `{ type: "input_error", detail }`。这些事件不写入 Skill |
| `steer` 或 `pi_message` | `text` 可空 | 解除 pause 并 `prompt` |
| `abort`、`stop_pi`、`cancel` | | 第 6 节的停止 |

发出：

`snapshot` 的形状与 `frontend-bridge.mjs` 的 `snapshot()` 一致，前端只认 `incoming.snapshot`：

```js
{
  type: "snapshot",
  snapshot: {
    run_id: recordingId,
    action: "",
    title: "",
    revision: 1,
    status: "recording",
    progress: { step: "capturing", label: "" },
    capture_frozen: false,
    draft: null,
    error: "",
    assist: { reason: "" },
    human_can_click: true,
    skill_dir: "",
    verify: null
  }
}
```

`revision` 每发一次加 1。进行中只用 `recording` 与 `waiting_operator`。结束时才用 `skill_ready`、`skill_written_needs_auth`、`verify_failed`、`stopped`，并填 `skill_dir` 和 `verify`。不要发 `type: "recording_result_saved"`。

`frame`：

```js
{ type: "frame", seq: 1, data: "<jpeg base64>", width: 1440, height: 900 }
```

约每 400ms 一帧；`input` 之后 100ms 再补一帧。截帧失败不要编造画面。

`thought`：`{ type: "thought", kind: "text", text }`。不要把 token 放进 text。

`error`：`{ type: "error", detail }`。

---

## 12. 新界面

写在 `playwright_CABP/web/`，静态文件由 `src/server.mjs` 在 `/` 提供。不要改 Dano 的 `skillfrontend`。

三步，没有能力结果，没有「产出 Skill」按钮：

1. 录制准备：`start_url`、`goal_text`、`title`、`tenant`、`subsystem`。
2. 页面录制：WebSocket 连 `/onboarding/page/record`。`snapshot` 只读 `message.snapshot`。`frame.data` 显示为 `data:image/jpeg;base64,` 加正文。人在预览上的点击按第 11 节的 `input` 发送，坐标用相对视口的 `nx`、`ny`。继续发送 `{ type: "steer", text }`。`waiting_operator` 时显示 `assist.reason`，预览不切走。`skill_ready`、`skill_written_needs_auth`、`verify_failed`、`stopped` 仍停在这一页，并显示 `skill_dir`。
3. Skill 目录：`GET /v1/skills`。需要 token 时在这一页提交 `{ tenant, subsystem, headers }` 到 `POST /v1/settings/token`。保存后服务把同一 subsystem 已导出目录里的 `config/auth.local.json` 更新掉，响应里的头是 mask。导出调用 `POST /v1/pi-recordings/<run_id>/export-skill`，`run_id` 以 `rec_` 开头，body 可带 `out_dir`。

`GET /v1/settings/token` 只返回 mask 过的头、`has_token`、`tenant`、`subsystem`。`GET /export/directory` 与 `PUT /export/directory` 读写 `{ out_dir }`，文件是 `data/export-dir.json`。

---

## 13. 测试

全部在 `playwright_CABP` 里，`npm test`。

`test/skills-present.test.mjs`：两份 SKILL.md 存在、超过 40 行；playwright 那份含 `snapshot` 与 `click`；derive-client 那份含 `HAR` 或 `record`。

`test/browser-ref.test.mjs` 与 `test/fixtures/iframe-form.html`：外层按钮文案「提交」且 id `outer`；iframe 内两个按钮文案都是「提交」，id 为 `inner-a`、`inner-b`，只有 `inner-b` 执行 `fetch("/api/save")`。断言：过期 epoch 的 ref 不增加点击计数；点 `f0` 上的「提交」不产生 save；点中 iframe 内第二个才有 `POST /api/save`。`fill` 一个 readonly 输入，返回 `value_not_applied`。

`test/evidence-store.test.mjs`：写入 50KB 正文，按 id 读回长度相同。没有 body 的记录 `body_missing === true`。

`test/goal.test.mjs`：`start` 消息的 `goal_text` 与 `goal.json` 全等，不是 `query`。

`test/verify.test.mjs`：分别造出第 9 节每个 code。`recorded_literal_default` 用一条请求 JSON 里的 12 字符字符串，写进 `client.py` 的默认参数，且 `constant_reason` 为空。长度为 1 的 `"1"` 不得触发这条。

`test/api-session.test.mjs`：起 HTTP 与 WebSocket，发 `start`（带 `tenant`），收到 `type: "frame"` 且 `data` 非空，收到的 `snapshot.run_id` 以 `rec_` 开头。此测试不调用真实模型，`startRecordingPi` 换成注入的假对象。假对象的 `prompt` 依次调用 host 的 `browser_snapshot`、点中 `inner-b` 的 `browser_act`、`network_get`、`write_skill_file` 三件、`run_skill_command`（读命令退出码 0）、`verify_skill`。断言 `SKILL.md` 与写入字节相同。不提供 token 时最终 snapshot `status` 为 `skill_written_needs_auth`。`src/agent/pi-session.mjs` 源码含 `createAgentSession` 与 `session.prompt`。

`test/export-id.test.mjs`：`POST /v1/pi-recordings/not-rec/export-skill` 返回 400。`rec_` 但状态不是 `skill_ready` 返回 `not_ready`。

`test/web-steps.test.mjs` 读 `web/` 的页面源码：含「录制准备」「页面录制」「Skill 目录」，不含「能力结果」「产出 Skill」。

---

## 14. 顺序

1. 第 1 到 3 节，health 测试与 Skill 原文测试。
2. 第 4 到 5 节，browser-ref、evidence、goal。
3. 第 8 到 9 节，verify 的每个 code。
4. 第 6、7、11 节，api-session 假 Pi。
5. 第 10、12 节，export-id 与前端测试。

某一页录错时，只查三处：是不是用了过期 ref，证据是不是没按 id 读全，读命令是不是没跑就写进了 `SKILL.md`。不要往 `prompt.md` 加这个页面的按钮名或 path。
