# Pi Business Skill Studio

把现有业务系统中的**真实页面操作 + 真实 API**沉淀为可被 AI Agent 理解、选择、组合、验证并执行的 Agent Skill。

它不是“根据页面猜接口”的生成器。它的核心是：

> **真实操作证据 → 原子能力 → 可编辑业务语义 → 证据校验 → 受策略约束的规划/执行 → 自包含 Skill 包**

## 对应目标

1. 接入业务系统并记录真实页面操作。
2. 捕获真实请求、字段、选项和返回结果。
3. 识别查询、新增、编辑、审核、删除等原子能力。
4. 通过 OpenAI 自动生成可人工修改的业务描述。
5. 使用证据门禁，避免发布没有真实成功证据的操作。
6. 根据自然语言规划执行路线，并落实：
   - 单原子调用；
   - 仅按 `approved` 数据绑定自动串联；
   - 无绑定/目标不明/结果歧义时询问；
   - 写操作执行前确认；
   - 以合同 completion 条件判断完成。
7. 导出自包含 Skill：
   - `SKILL.md`
   - 路由与组合说明
   - 输入表单/候选规则
   - `scripts/execute.mjs`
   - JSON 机器合同
   - 证据引用清单

## 技术组成

- **Pi Coding Agent 0.84.4**
  - 项目级 `.pi/extensions/`
  - 项目级 `.pi/skills/`
  - Pi SDK 嵌入模式
- **Playwright 1.62.1**
  - headed Chromium
  - UI click/change/submit 录制
  - XHR/fetch/document 请求/响应录制
- **OpenAI official Node SDK**
  - Responses API
  - Structured Outputs
  - 默认 `gpt-5.5`，可通过 `OPENAI_MODEL` 修改
- **Evidence Gate**
  - 真实 network evidence
  - 成功响应
  - 写操作 UI 关联
  - 未识别操作禁止发布

## 1. 安装

要求 Node.js 22+。

```bash
npm install
npm run browser:install
cp .env.example .env
```

设置：

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-5.5"
```

如果暂时不配置 OpenAI，也可以使用确定性的启发式分析：

```bash
npm run studio -- analyze --no-llm
```

## 2. 录制真实业务系统

```bash
npm run studio -- record \
  --url "https://your-business-system.example.com" \
  --name "customer-maintenance"
```

会打开一个真实 Chromium。你可以正常登录、点击、选择、提交。

录制内容写入：

```text
.business-skill-studio/
└── recordings/
    └── rec_xxx/
        ├── session.json
        └── events.jsonl
```

`events.jsonl` 包括：
- click/change/submit；
- 控件 label/name/type；
- select/datalist/可见 role=option 候选；
- form 快照；
- 请求 method/url/query/body；
- 响应 status/body（限大小）；
- UI 与请求的相关 evidence id。

> 默认脱敏密码、token、cookie、authorization 等敏感内容。

完成操作后按 `Ctrl+C`。

## 3. 生成原子能力

```bash
npm run studio -- analyze
```

结果：

```text
.business-skill-studio/catalog/capabilities.json
```

这是**人工可修改**的主目录。可以改 title、description，也可以审核数据绑定。

OpenAI 只允许修改业务语义和对歧义 POST 做分类；不能凭空添加没有证据的 endpoint/字段/选项。

## 4. 证据校验

```bash
npm run studio -- validate
```

只有 `validation.status = "verified"` 才能导出。

写操作额外要求：
- 存在成功真实请求；
- 能关联到真实 UI 操作；
- operation 已确定。

## 5. 自然语言规划

```bash
npm run studio -- plan "先查客户，再把已确认客户的等级改成VIP"
```

输出包括：
- steps；
- capabilityId；
- 输入；
- binding；
- 是否需要询问；
- policy errors；
- write steps。

自动串联的硬规则：

```text
binding.approved === true
```

否则不允许模型仅凭“字段看起来同名”自动传值。

可以通过 CLI 明确批准一个绑定：

```bash
npm run studio -- bind \
  --from customer-search --from-path '$.items[0].id' \
  --to customer-update --to-path '$.id' \
  --approve
```

动态候选也必须显式指定一个**已验证 query capability**：

```bash
npm run studio -- candidate-source \
  --target customer-update \
  --field '$.levelCode' \
  --source level-options \
  --value-path '$.items[*].code' \
  --label-path '$.items[*].name' \
  --approve
```

在 Pi 中对应操作会弹出确认框。

## 6. 执行能力

先给运行时认证信息。不要把生产 token 写入能力合同。

```bash
export SKILL_AUTH_HEADERS='{"Authorization":"Bearer test-token"}'
```

查询：

```bash
npm run studio -- execute \
  --capability customer-search \
  --input '{"name":"张三"}'
```

写操作必须显式确认：

```bash
npm run studio -- execute \
  --capability customer-update \
  --input '{"id":"123","level":"VIP"}' \
  --confirm-write
```

Pi Extension 会使用交互式 `ctx.ui.confirm`，因此在 Pi 里无需让模型自己“决定是否确认”。

## 7. 导出 Skill

```bash
npm run studio -- export --name crm-business
```

生成：

```text
dist/skills/crm-business/
├── SKILL.md
├── skill.contract.json
├── contracts/
│   └── capabilities.json
├── references/
│   ├── routing-and-composition.md
│   ├── forms-and-candidates.md
│   └── evidence.md
└── scripts/
    ├── execute.mjs
    └── candidates.mjs
```

导出的执行脚本只依赖 Node 内置 `fetch`，不会携带录制时的 cookie/token。

## 8. 在 Pi Agent 中使用

项目已包含：

```text
.pi/
├── settings.json
├── extensions/
│   └── business-skill-studio.ts
└── skills/
    ├── business-skill-studio/
    │   └── SKILL.md
    └── control-in-app-browser/
        └── SKILL.md
```

安装依赖后，从项目根目录启动 Pi。Pi 会在项目被信任后发现 extension + skill。

如果你全局没有安装 Pi：

```bash
npx --package=@earendil-works/pi-coding-agent@0.84.4 pi
```

使用 OpenAI API：

```bash
export OPENAI_API_KEY="..."
npx --package=@earendil-works/pi-coding-agent@0.84.4 pi \
  --provider openai \
  --model gpt-5.5
```

然后可以直接说：

```text
开始记录 https://crm.example.com ，我接下来会演示客户新增和审核。
```

Pi 可调用：
- `business_skill_record_start`
- `business_skill_record_stop`
- `business_browser_control`
- `business_skill_analyze`
- `business_skill_validate`
- `business_skill_plan`
- `business_skill_execute`
- `business_skill_approve_binding`
- `business_skill_set_dynamic_candidates`
- `business_skill_export`

也可以显式加载：

```text
/skill:business-skill-studio
```

## 9. 以 Pi SDK 嵌入项目

示例已经放在 `src/pi-agent.ts`：

```bash
export OPENAI_API_KEY="..."
npm run agent -- "读取当前能力目录并告诉我哪些能力已验证"
```

它使用：
- `ModelRuntime.create()`
- OpenAI `OPENAI_API_KEY`
- Pi 的 `getModel("openai", "gpt-5.5")`
- `createAgentSession(...)`

所以同一项目既能使用 Pi TUI，也能将 Pi Agent 嵌入你自己的 Node 服务。

## 关于 Control In App Browser

本项目**不依赖 Codex/ChatGPT 私有的 in-app browser bridge**。项目额外提供一个 Pi 可加载的 `.pi/skills/control-in-app-browser/SKILL.md` 便携适配层；底层使用项目可携带的 Playwright：

```text
Pi Agent
  ↓
business-skill-studio extension
  ↓
Playwright headed Chromium
  ↓
真实页面 + 真实网络证据
```

这样它可以放进你的 Pi Agent 项目里运行，而不是只能在 Codex 桌面端环境运行。

## 当前 MVP 边界

已经实现：
- headed browser 真实录制；
- UI + 网络证据关联；
- 请求/响应 schema 归纳；
- 原子操作分类；
- OpenAI Structured Output 业务语义优化；
- 证据发布门禁；
- 自然语言规划；
- approved-binding 策略；
- 写操作确认；
- Skill 包导出；
- Pi extension / Pi SDK 接入。

下一阶段适合增强：
- iframe/多窗口更精细的 action/request causal graph；
- GraphQL operationName 级拆分；
- multipart/form-data 文件上传识别；
- 自动生成候选 API 的 dependency graph；
- 使用浏览器登录态执行而不仅是 `SKILL_AUTH_HEADERS`；
- 人工审核 Web UI；
- capability replay sandbox / test tenant；
- 版本差异检测与 Skill 增量更新。
