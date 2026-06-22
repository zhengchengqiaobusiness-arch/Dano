# Implementation Goal Prompt

## GOAL: 实现可验证 P0 Web LLM Chat

把当前仓库实现成基于 `references/pi-web-main/` 结构的 Dano P0 Web 应用：浏览器可访问独立 Web UI，通过服务端运行的 LLM 正常通信和多轮对话；使用 HTTP POST + EventSource/SSE 替代浏览器 WebSocket；支持 Docker + nginx 部署。

## CONTEXT: 项目路径、相关文件、当前状态

项目路径：`/Users/joseph/projects/personal/small-shop/Dano`

关键文件：

- `specs/001-llm-chat/spec.md`
- `specs/001-llm-chat/plan.md`
- `specs/001-llm-chat/research.md`
- `specs/001-llm-chat/data-model.md`
- `specs/001-llm-chat/contracts/http-sse.md`
- `specs/001-llm-chat/quickstart.md`
- `references/pi-web-main/`
- `references/dano-assistant.svg`

当前计划要求：

- 保留 `packages/bridge` + `packages/svelte`，将可运行 standalone 应用放在 `apps/dano`
- 移除 `packages/bin` Pi extension 模式
- 移除 `packages/electron` Electron 模式
- 保留服务端 LLM runtime 依赖
- LLM API key 只通过服务端环境配置：本地 `.env`，Docker env 或 Docker secrets
- 保留 web standalone 模式
- Docker 打包
- nginx 反向代理
- 浏览器访问 API 从 WebSocket 改为 HTTP POST + EventSource/SSE
- 图标使用 `references/dano-assistant.svg`

## CONSTRAINTS: 禁止修改、必须遵守、禁止行为

必须遵守：

- 先读现有文件再写代码。
- 最小改动，贴合 `pi-web-main` 现有结构。
- 保持 pnpm workspace / pi-web monorepo 风格。
- 浏览器端不得接收、保存、展示或暴露 LLM credentials、API key、runtime secrets。
- P0 只做聊天通信和对话，不做企业业务流程执行。
- 所有失败必须显式显示，不允许静默失败。
- 写操作用 `apply_patch`。
- 不提交 `yarn.lock` 变更。

禁止：

- 不创建 `/web` 目录作为主应用。
- 不保留 Pi extension 入口作为目标运行模式。
- 不保留 Electron 运行/打包模式。
- 不让浏览器直接调用 LLM。
- 不用 WebSocket 作为浏览器通信协议。
- 不实现请假、审批、报销等业务流程。
- 不做无关重构或样式大改。

## PRIORITY: 正确性、最小改动、可验证性

优先级：

1. 正确性：浏览器能通过服务端 LLM 完成真实对话。
2. 最小改动：只迁移和修改 P0 必需代码。
3. 可验证性：必须能用命令和浏览器/HTTP smoke case 验证。
4. 可维护性：保留清晰包结构和测试入口。

## PLAN: 执行步骤

1. 阅读 `plan.md`、`contracts/http-sse.md`、`quickstart.md` 和 `references/pi-web-main` 关键文件。
2. 初始化目标代码结构：
   - `apps/dano`
   - `packages/bridge`
   - `packages/svelte`
   - root `package.json`
   - `pnpm-workspace.yaml`
3. 从 `references/pi-web-main` 迁移 standalone Dano 应用和 Svelte UI 必需代码。
4. 删除目标中的 Pi extension 模式：
   - 不迁移或移除 `packages/bin`
   - 移除 root package 中 extension 注册与相关脚本
5. 删除目标中的 Electron 模式：
   - 不迁移或移除 `packages/electron`
   - 移除 Electron scripts/dependencies/workspace entries
6. 将浏览器通信改为：
   - `POST /api/clients`
   - `GET /api/clients/:id/events`
   - `POST /api/clients/:id/messages`
   - `POST /api/clients/:id/disconnect`
7. 实现 SSE 事件：
   - `conversation.ready`
   - `message.accepted`
   - `assistant.started`
   - `assistant.delta`
   - `assistant.completed`
   - `message.failed`
   - `heartbeat`
8. 更新 Svelte client：
   - 用 `EventSource` 接收事件
   - 用 `fetch` 发送命令
   - 保留消息顺序、处理中、失败、重试状态
9. 增加服务端凭据配置：
   - 本地开发读取 `.env`
   - Docker 部署读取 env vars 或 Docker secrets
   - 浏览器 UI 不提供 API key 输入
   - 浏览器响应中不注入任何 LLM secret
10. 使用 `references/dano-assistant.svg`：
   - 放入 Web public asset
   - 作为 favicon 或显式产品图标
11. 增加 Docker 打包：
    - root `Dockerfile`
    - `docker-compose.yml`
12. 增加 nginx 反向代理：
    - `deploy/nginx/default.conf`
    - SSE route 禁用 buffering
13. 增加/更新测试：
    - 空输入
    - SSE event formatting
    - message accepted/completed/failed
    - retry
    - business-action request 不执行
    - server-side credential config only
    - browser responses do not expose secrets
14. 运行验证命令并修复失败。

## DONE WHEN: 完成条件

- 浏览器能打开 Dano Web UI。
- 用户能发送第一条消息并收到服务端 LLM 回复。
- 同一会话能继续多轮对话。
- 空消息不会提交。
- LLM 失败时 UI 显示明确失败状态，可重试。
- 业务动作请求不会提交任何企业表单、审批、记录或外部流程。
- 浏览器端检查不到任何 LLM secrets。
- 修改服务端 LLM credential 配置后，服务端模型访问行为变化；浏览器端无需输入 secret。
- Docker + nginx 路径可启动并访问。
- `pnpm run check`、`pnpm run test`、`pnpm run build` 通过。

## VERIFY: 验证命令

```bash
pnpm install
pnpm run check
pnpm run test
pnpm run build
docker compose up --build -d
curl -s http://localhost/api/health
curl -s -X POST http://localhost/api/clients -H 'Content-Type: application/json' -d '{}'
curl -N http://localhost/api/clients/<clientId>/events
curl -s -X POST http://localhost/api/clients/<clientId>/messages -H 'Content-Type: application/json' -d '{"type":"command","payload":{"id":"smoke-1","type":"get_state"}}'
```

## OUTPUT: 输出要求

完成后输出：

- 修改文件列表
- 关键实现说明
- 移除的模式：Pi extension / Electron
- 浏览器通信协议：HTTP POST + EventSource/SSE
- Docker/nginx 使用方式
- 验证命令结果
- 未完成或不确定项，若有必须明确说明

## STOP RULES: 停止条件

遇到以下情况立刻停止并说明：

- 需要真实 LLM credentials，但当前环境没有。
- `pi-web-main` runtime 依赖无法安装，且确认不是代码问题。
- pnpm install 因网络/代理失败。
- 需要改动 P0 之外业务流程。
- 需要删除用户已有非本任务文件或不可确认来源的改动。
- WebSocket 无法完全移除且会影响 P0 合同。
