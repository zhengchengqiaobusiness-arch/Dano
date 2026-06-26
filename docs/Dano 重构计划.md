# Dano 应用归并与 `standalone` 清理重构计划

> 执行对象：Codex
>
> 目标仓库：`zhengchengqiaobusiness-arch/Dano`
>
> 默认分支：`main`
>
> 计划性质：结构重构，不包含产品功能、协议或部署拓扑变更

## 1. 目标

把 Dano 收敛成一个应用 workspace package：

```text
apps/dano
```

本次同时完成：

1. `packages/svelte` 迁入 `apps/dano/web`。
2. `packages/bridge` 迁入 `apps/dano/src/bridge`。
3. 浏览器/服务端一致的 wire protocol types 迁入 `apps/dano/types/protocol.ts`。
4. `apps/dano` 成为唯一 workspace package，名称继续为 `@dano/app`。
5. 删除 `packages/*` workspace pattern，不保留空 package 目录。
6. 删除 `standalone` 概念、旧脚本名、旧产物路径和旧兼容 alias。
7. 构建产物统一到：
   - `apps/dano/dist/server`
   - `apps/dano/dist/web`
8. 保持单进程、单镜像、同源 HTTP/SSE、nginx 转发和现有协议行为不变。

根 `package.json` 版本是唯一 Dano 产品版本。`apps/dano/package.json` 不保留独立 `version`。

Package 名称继续使用 `@dano/app`。不改成 `@dano/server`，因为该 package 同时拥有 web、server、types 和部署入口；不改成 `@dano/dano`，因为根 package 已经代表产品版本和仓库级脚本。

## 2. 目标结构

```text
apps/
└── dano/
    ├── package.json
    ├── tsdown.config.ts
    ├── vite.config.ts
    ├── tsconfig.server.json
    ├── tsconfig.web.json
    │
    ├── types/
    │   └── protocol.ts
    │
    ├── src/
    │   ├── main.ts
    │   ├── server.ts
    │   ├── backend.ts
    │   ├── runtime.ts
    │   ├── runtime-entry.ts
    │   ├── dev-reload.ts
    │   ├── __tests__/
    │   │
    │   └── bridge/
    │       ├── server.ts
    │       ├── bridge-event-bus.ts
    │       ├── bridge-rpc-adapter.ts
    │       ├── session-registry.ts
    │       ├── live-session.ts
    │       ├── types.ts
    │       └── __tests__/
    │
    ├── web/
    │   ├── index.html
    │   ├── public/
    │   └── src/
    │
    └── dist/
        ├── server/
        └── web/
```

`apps/dano/src/bridge` 是源码模块边界，不是 workspace package、独立服务或独立生产产物。

## 3. 非目标

本次不得做：

- 不拆分前后端为两个 workspace package。
- 不创建 `packages/protocol`。
- 不保留 `packages/bridge`、`packages/svelte` 或 symlink。
- 不新增 `build:bridge`、`verify:bridge-build` 或任何 bridge 独立构建验证命令。
- 不保留旧脚本名作为 deprecated alias。
- 不生成 `web-dist`、根 `dist/bridge` 或 `dist/bridge/standalone`。
- 不拆成两个 Docker 镜像或两个独立部署单元。
- 不增加 CORS、跨域 API base URL 或独立 API 域名。
- 不修改 HTTP 路径、请求结构、响应结构或 SSE 消息格式。
- 不修改会话状态、workspace、上传、模型、扩展 UI 或消息处理行为。
- 不替换开发热重载机制，只清理命名和路径。
- 不修改 `window.__PI_WEB_CONFIG__`、`PI_WEB_DEBUG`、`PI_WEB_SESSIONS_ROOT` 等兼容字段。
- 不做 UI 样式或交互调整。
- 不顺带升级依赖。

## 4. 领域和架构决策

已记录：

- [ADR 0001: Make Dano Bridge an internal server subsystem](./adr/0001-dano-bridge-is-internal.md) (`docs/adr/0001-dano-bridge-is-internal.md`)
- [ADR 0002: Share browser/server protocol types](./adr/0002-share-browser-server-protocol-types.md) (`docs/adr/0002-share-browser-server-protocol-types.md`)

执行时必须遵守：

- `Dano Bridge` 是 Dano server 内部 HTTP/SSE 和 RPC 子系统。
- app 组装层可以依赖 `src/bridge`。
- `src/bridge` 不得反向依赖 `main.ts`、CLI、部署或进程入口。
- browser/server 共享 wire protocol types 放在 `apps/dano/types/protocol.ts`。
- `BridgeConfig` 整体、lifecycle、client tracking 和 internal bridge events 留在 `apps/dano/src/bridge/types.ts`。
- 序列化到 `window.__PI_WEB_CONFIG__` 的 browser-observed config DTO，例如 empty state 和 quick actions，可以放入 `apps/dano/types/protocol.ts`。
- 相对 import 超过三层 `../` 时，可以使用内部 alias `@dano/types/*`，但正常代码必须用 `import type`。

## 5. 必须保持的行为不变量

1. `GET /api/health` 返回成功状态。
2. `POST /api/clients` 创建客户端并返回相对路径 `eventsUrl` 和 `messagesUrl`。
3. EventSource/SSE 连接、心跳和消息推送行为不变。
4. `POST /api/clients/:id/messages` 行为不变。
5. client disconnect 行为不变。
6. 文件上传和 preview 路径不变。
7. Node 服务继续提供前端静态文件与 SPA fallback。
8. HTML 运行时配置注入行为不变。
9. 默认 host、port、workspace、sessions root 和环境变量兼容行为不变。
10. Docker/Compose 继续使用一个 Dano app 容器和一个 nginx 容器。
11. nginx 对 `/api/`、SSE 和 `/` 的现有转发行为不变。
12. 根 `package.json` 版本仍是唯一 Dano 产品版本。

## 6. 实施步骤

### A. 建立基线

执行并记录结果：

```bash
pnpm install
pnpm run check
pnpm run test
pnpm run build
```

如果基线失败，记录原始错误，不把已有失败伪装成本次改动引入。

建立引用清单：

```bash
rg -n 'packages/svelte|@dano/svelte|packages/bridge|@dano/bridge|web-dist|dist/bridge/standalone'
rg -n 'standalone|Standalone|STANDALONE'
rg -n 'WsRpcAdapter|WsClient|ws-rpc-adapter'
rg -n 'dev:bridge:standalone|start:bridge:standalone|build:svelte|dev:svelte|build:bridge|verify:bridge-build'
```

### B. 迁移前端到 `apps/dano/web`

使用 `git mv`：

```bash
mkdir -p apps/dano/web
git mv packages/svelte/index.html apps/dano/web/index.html
git mv packages/svelte/public apps/dano/web/public
git mv packages/svelte/src apps/dano/web/src
```

迁移 `packages/svelte/vite.config.ts` 为 `apps/dano/vite.config.ts`：

- `root` 指向 `apps/dano/web`。
- `build.outDir` 指向 `apps/dano/dist/web`。
- `emptyOutDir: true` 只能清理 `dist/web`。
- 保留 Svelte plugin、Shiki shim alias、Mermaid/vendor chunk、debug flag。
- `/api` dev proxy 继续指向 `http://localhost:8080`。

迁移 `packages/svelte/tsconfig.json` 为 `apps/dano/tsconfig.web.json`，include 改为 `web/src/**/*.ts` 和 `web/src/**/*.svelte`。

### C. 迁移 bridge 到 `apps/dano/src/bridge`

使用 `git mv`：

```bash
mkdir -p apps/dano/src/bridge
git mv packages/bridge/src/* apps/dano/src/bridge/
```

删除 package 边界：

```bash
git rm packages/bridge/package.json packages/bridge/tsconfig.json packages/bridge/tsdown.config.ts packages/bridge/README.md
```

清理后不得残留 `packages/bridge` 目录。

测试同步迁移：

- `packages/bridge/src/__tests__/server.test.ts` 迁到 `apps/dano/src/bridge/__tests__/server.test.ts`，继续覆盖 HTTP/SSE transport。
- `packages/bridge/src/__tests__/ws-rpc-adapter.test.ts` 迁到 `apps/dano/src/bridge/__tests__/bridge-rpc-adapter.test.ts`。
- `apps/dano/src/__tests__/backend.test.ts` 保留为 app server lifecycle 测试，并同步重命名旧 `standalone` import 和 describe。
- 不删除 server、backend 或 bridge adapter 测试来消除迁移失败。

依赖迁移到 `apps/dano/package.json`：

- `@earendil-works/pi-coding-agent` 保留在 `dependencies`。
- `@josephyoung/pi-heimdall` 保留在 `dependencies`。
- `typebox` 加入 `dependencies`，因为 tool schema 在运行时使用。

### D. 拆出共享 protocol types

新建：

```text
apps/dano/types/protocol.ts
```

只迁入 browser/server 都需要的 wire protocol 类型，例如：

- RPC command/response/event 类型。
- transcript 类型。
- uploaded file 和 image content 类型。
- workspace/file/tree/model/thinking level 等浏览器协议类型。
- quick action、empty state 等 HTML runtime config 需要的跨边界类型。

留在 `apps/dano/src/bridge/types.ts`：

- `BridgeConfig`
- bridge lifecycle/state 类型。
- client tracking 类型。
- internal bridge events。
- server-only helper 类型。

配置内部 alias：

```text
@dano/types/* -> apps/dano/types/*
```

同步 Vite、server tsconfig、web tsconfig、根 `tsconfig.json` 和 Vitest resolution。

硬约束：

- 从 `@dano/types/*` 导入类型时必须使用 `import type`。
- 如果从 `@dano/types/*` 导入 runtime constant，必须确保 Vite、Vitest 和 server build 都有真实 runtime resolution；不得只依赖 TypeScript `paths`。
- `protocol.ts` 只允许导出 browser/server wire 类型、browser runtime config DTO，以及 JSON-safe protocol constants such as `ASK_USER_QUESTION_TOOL_NAME`；不得引入 `node:*`、Pi runtime、server class 或 Svelte。
- 不修改 wire 字段名、command type、response shape 或 SSE envelope。
- 如果抽取全部 protocol types 导致大范围 type churn 或 runtime import 风险，先只迁移 web 当前直接需要的最小类型集合，剩余类型后续 PR 整理。

### E. 合并 app package 和构建配置

`apps/dano/package.json`：

- 保留 `"name": "@dano/app"`。
- 删除 `"version"`。
- 删除 `@dano/bridge` dependency。
- 将 `packages/svelte/package.json` 的 devDependencies 全量合并到 `apps/dano/package.json`，保留版本不变；browser bundle 直接 import 的包不得遗漏。
- 删除旧脚本名。
- 推荐脚本：

```json
{
  "scripts": {
    "build": "pnpm run build:server && pnpm run build:web",
    "build:server": "tsdown",
    "build:web": "vite build",
    "check": "pnpm run check:server && pnpm run check:web",
    "check:server": "tsc -p tsconfig.server.json --noEmit",
    "check:web": "svelte-check --tsconfig ./tsconfig.web.json",
    "dev:server": "node --import jiti/register ./src/main.ts",
    "dev:web": "vite",
    "start": "node ./dist/server/main.js",
    "test": "vitest run --root ../.. --config vitest.config.ts"
  }
}
```

`apps/dano/tsdown.config.ts`：

- 输出到 `dist/server`。
- server 产品构建 entry 改为 `src/main.ts`，不要把 `src/**/*.ts` 全部作为产品入口输出。
- 不使用 `alwaysBundle @dano/bridge`，因为 bridge 已是 app 内部源码。
- `clean: true` 只能清理 `dist/server`。
- 不删除 `dist/web`。

根 `package.json`：

- `build` 只调用 `pnpm -C apps/dano build`。
- `build:server` 只调用 `pnpm -C apps/dano build:server`。
- `build:web` 只调用 `pnpm -C apps/dano build:web`。
- `start` 调用 `pnpm -C apps/dano start`。
- `dev:server` 调用 `pnpm -C apps/dano dev:server`。
- `dev:web` 调用 `pnpm -C apps/dano dev:web`。
- 删除 `build:bridge`、`verify:bridge-build`、`build:svelte`、`dev:svelte`、`dev:bridge:standalone`、`start:bridge:standalone`。
- 保留 deploy、secret、smoke、test 脚本。
- root patch version 递增一个 patch。

更新 `readDanoPackageInfo`：

- dev checkout 下向上搜索并读取仓库根 `package.json`，包名必须是 `@dano/dano`。
- Docker runtime 下读取 `/app/package-versions/package.json`。
- 不从 `apps/dano/package.json` 读取产品 version。
- 保持 `get_dano_version` 只返回根产品版本。

`pnpm-workspace.yaml`：

- 删除 `packages/*`。
- 保留 `apps/*` 和现有 build allowlist。
- 删除 `@dano/bridge`、`@dano/svelte` importer 后重新生成 `pnpm-lock.yaml`，不得保留 stale importer blocks。

根 `tsconfig.json`：

- 删除 `packages/bridge/src/**/*.ts` 和其他已删除 package 路径。
- 保留 `apps/dano/src/**/*.ts`。
- 增加 `apps/dano/types/**/*.ts`。
- 前端 Svelte 仍由 `apps/dano/tsconfig.web.json` 和 `svelte-check` 负责。

`vitest.config.ts`：

- include 更新为 `apps/dano/src/**/*.test.ts` 和 `apps/dano/web/src/**/*.test.ts`。
- 配置 `@dano/types/*` resolution。

### F. 清理命名

删除 `standalone` 术语：

| 旧名称 | 新名称 |
|---|---|
| `StandalonePackageInfo` | `DanoPackageInfo` |
| `StandaloneMainOptions` | `DanoServerOptions` |
| `readStandalonePackageInfo` | `readDanoPackageInfo` |
| `parseStandaloneMainOptions` | `parseDanoServerOptions` |
| `initializeStandaloneWorkspaceSettings` | `initializeDanoWorkspaceSettings` |
| `runStandaloneBridge` | `runDanoServer` |
| `runStandaloneMain` | `runDanoMain` |
| `StandaloneBridgeBackend` | `DanoBackend` |
| `startStandaloneBridge` | `startDanoServer` |
| `StandaloneRuntime` | `DanoRuntime` |
| `loadStandaloneRuntime` | `loadDanoRuntime` |
| `StandaloneDevReloadController` | `DanoDevReloadController` |
| `DEFAULT_STANDALONE_PORT` | `DEFAULT_DANO_PORT` |

同步清理 WebSocket 误导命名：

| 旧名称 | 新名称 |
|---|---|
| `ws-rpc-adapter.ts` | `bridge-rpc-adapter.ts` |
| `WsRpcAdapter` | `BridgeRpcAdapter` |
| `WsRpcAdapterContext` | `BridgeRpcAdapterContext` |
| `WsClient` | `BridgeClient` |

日志：

- `[pi-web]` 改为 `[dano]`。
- `pi-web standalone bridge` 改为 `Dano server`。
- `standalone runtime/bridge/sources` 改为 `Dano runtime/server/sources`。
- `WsRpcAdapter[...]` 改为 `BridgeRpcAdapter[...]`。

限制：只清理误导性的 `Ws*` 代码符号和文件名；不得修改 HTTP/SSE wire protocol、EventSource 行为、API path、SSE envelope 或 browser store 连接逻辑。

### G. 静态目录解析

删除：

```text
findNearestWebDist
resolveDefaultStaticDir 中的父目录 web-dist 搜索
```

静态目录规则：

1. 如果设置 `DANO_STATIC_DIR`，使用其绝对路径。
2. 源码入口 `apps/dano/src/main.ts` 对应 `apps/dano/dist/web`。
3. 构建入口 `apps/dano/dist/server/main.js` 对应 `apps/dano/dist/web`。
4. Docker 入口 `/app/dist/server/main.js` 对应 `/app/dist/web`。
5. 默认静态目录只有在 `index.html` 存在时才返回；不存在时返回 `undefined`，保持现有“未配置 web bundle”降级行为，不启动崩溃。

不保留旧 `web-dist` fallback。

`parseDanoServerOptions` 必须读取 `DANO_STATIC_DIR`。设置时 trim 环境变量，使用从 `cwd` 解析后的绝对路径，并跳过默认静态目录推导。

Dev reload watch：

- 只监听 `apps/dano/src` 和 `apps/dano/types`。
- 不监听 `apps/dano/web`、`apps/dano/dist` 或已删除的 `packages/bridge`。

至少增加 5 个测试：

1. source entry 解析到 `apps/dano/dist/web`。
2. built entry 解析到 `apps/dano/dist/web`。
3. Docker 风格路径解析到 `/app/dist/web`。
4. `DANO_STATIC_DIR` 覆盖默认值。
5. 目录不存在时返回现有降级结果。

### H. Docker 和部署

Dockerfile：

- 删除 `COPY packages/bridge/package.json ...`。
- 删除 `COPY packages/svelte/package.json ...`。
- `RUN pnpm run build` 只构建 `@dano/app`。
- runtime 阶段的构建产物只从 `/app/apps/dano/dist` 复制到 `/app/dist`：

```dockerfile
COPY --from=build /app/apps/dano/dist ./dist
```

- 不复制 `/app/dist`、`/app/web-dist`、`/app/packages/bridge/dist`。
- 仍需保留：
  - `/prod/dano/package.json` -> `./package.json`
  - `/prod/dano/node_modules` -> `./node_modules`
  - root `package.json` -> `./package-versions/package.json`
  - `dano.config.json`
  - `deploy/runtime-defaults`
  - `deploy/docker-entrypoint.sh`
- Docker runtime 必须继续复制 `deploy/runtime-defaults` 到 `/app/deploy/runtime-defaults`，并保持 `DANO_RUNTIME_DEFAULTS_DIR` 可覆盖。
- entrypoint 仍负责容器启动时把默认文件复制到 `$DANO_DEFAULT_WORKSPACE_PATH/.pi`，不得依赖源码 checkout 路径搜索。
- CMD 改为：

```dockerfile
CMD ["node", "./dist/server/main.js"]
```

`deploy/docker-entrypoint.sh` 默认命令同步改为：

```text
node ./dist/server/main.js
```

Dockerfile `CMD` 也必须从 `node ./dist/bridge/standalone/main.js` 改为 `node ./dist/server/main.js`。

nginx、Compose 拓扑不变。

### I. 文档和 specs

更新：

- `README.md`
- `AGENTS.md`
- `deploy/README.md`
- `.env.example`
- `specs/001-llm-chat/plan.md`
- `specs/001-llm-chat/quickstart.md`
- `specs/001-llm-chat/research.md`
- `specs/001-llm-chat/implementation-goal.md`
- 其他搜索到的维护文档

删除或改写所有当前维护文档里的以下内容，`docs/adr/**` 和本重构计划除外：

```text
packages/svelte
@dano/svelte
packages/bridge
@dano/bridge
web-dist
dist/bridge
standalone
WsRpcAdapter
WsClient
```

`.gitignore` 删除无效的 `web-dist/` 条目。确认通用 `dist/` 或 app dist 规则不提交构建产物。

## 7. 验收

### 静态检查和构建

```bash
pnpm install
pnpm run check
pnpm run test

rm -rf apps/dano/dist dist/bridge web-dist
pnpm run build
```

必须成功。

关键 server UT 必须单独跑通：

```bash
pnpm exec vitest run apps/dano/src/__tests__/main.test.ts
pnpm exec vitest run apps/dano/src/__tests__/backend.test.ts
pnpm exec vitest run apps/dano/src/bridge/__tests__/dano-version-tool.test.ts
pnpm exec vitest run apps/dano/src/bridge/__tests__/server.test.ts
pnpm exec vitest run apps/dano/src/bridge/__tests__/bridge-rpc-adapter.test.ts
```

产物检查：

```bash
test -f apps/dano/dist/server/main.js
test -f apps/dano/dist/web/index.html
test ! -e packages/svelte
test ! -e packages/bridge
test ! -e web-dist
test ! -e dist/bridge
test ! -e dist/bridge/standalone
```

### 零残留检查

零残留检查适用于当前维护源码、脚本和运行文档。ADR 和本重构计划是历史/执行决策记录，排除在零残留检查之外。

```bash
rg -n 'packages/svelte|@dano/svelte|packages/bridge|@dano/bridge|web-dist|dist/bridge/standalone' \
  --glob '!pnpm-lock.yaml' \
  --glob '!node_modules/**' \
  --glob '!docs/Dano 重构计划.md' \
  --glob '!docs/adr/**'

rg -n 'standalone|Standalone|STANDALONE' \
  --glob '!pnpm-lock.yaml' \
  --glob '!node_modules/**' \
  --glob '!apps/dano/dist/**' \
  --glob '!docs/Dano 重构计划.md' \
  --glob '!docs/adr/**'

rg -n 'WsRpcAdapter|WsClient|ws-rpc-adapter' \
  --glob '!pnpm-lock.yaml' \
  --glob '!node_modules/**' \
  --glob '!docs/Dano 重构计划.md' \
  --glob '!docs/adr/**'

rg -n 'dev:bridge:standalone|start:bridge:standalone|build:svelte|dev:svelte|build:bridge|verify:bridge-build' \
  --glob '!pnpm-lock.yaml' \
  --glob '!node_modules/**' \
  --glob '!docs/Dano 重构计划.md' \
  --glob '!docs/adr/**'
```

预期均无匹配。若某个历史说明确需保留，PR 描述必须逐项解释。

### 直接启动 smoke

```bash
pnpm run start -- --host 127.0.0.1 --port 8080
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS http://127.0.0.1:8080/ | rg -i '<html'
DANO_SMOKE_BASE_URL=http://127.0.0.1:8080 pnpm run smoke:deploy
```

预期：

- `/` 返回构建后的 Dano HTML，不是 placeholder。
- `/api/health` 返回 `{"status":"ok"}`。
- smoke 完成 client 创建、SSE、message response 和 disconnect。

### 开发模式

分别运行：

```bash
pnpm run dev:server
pnpm run dev:web
```

确认：

- Vite 从 `apps/dano/web` 启动。
- `/api` 代理到 8080。
- 页面能建立 EventSource 连接。
- 修改前端源码可热更新。
- 修改 server/bridge 源码仍触发现有 Dano dev reload 行为。

### 容器验证

有 Docker 或 Podman 时执行：

```bash
pnpm run build
docker build -t dano:refactor .
DANO_IMAGE=dano:refactor DANO_NGINX_PORT=18082 pnpm run deploy:up
curl -fsS http://127.0.0.1:18082/api/health
DANO_SMOKE_BASE_URL=http://127.0.0.1:18082 pnpm run smoke:deploy
docker run --rm dano:refactor node -e "const p=require('/app/package-versions/package.json'); if (p.name !== '@dano/dano' || !p.version) process.exit(1)"
pnpm run deploy:down
```

可选生产 release 验证：

```bash
DANO_IMAGE=dano:refactor DANO_BUILD_PARENT_DIR=/private/tmp pnpm run deploy:release
```

无容器运行时时，最终报告写明未执行容器验证。

## 8. 建议提交拆分

### Commit 1

```text
refactor(app): 归并 Dano workspace 结构
```

包含：

- `packages/svelte` -> `apps/dano/web`
- `packages/bridge` -> `apps/dano/src/bridge`
- `apps/dano/types/protocol.ts`
- app package 依赖合并
- 删除 `packages/*` workspace pattern

### Commit 2

```text
refactor(server): 清理 standalone 和 ws 命名
```

包含：

- `Standalone*` -> `Dano*`
- `WsRpcAdapter` -> `BridgeRpcAdapter`
- `WsClient` -> `BridgeClient`
- 旧脚本名删除
- static dir 解析改为确定路径
- 测试同步

### Commit 3

```text
chore(deploy): 更新 Dano 打包和文档
```

包含：

- Dockerfile
- entrypoint
- README / AGENTS / specs / deploy 文档
- `.gitignore`
- root patch version
- lockfile
- smoke 修正

## 9. PR 要求

PR 标题：

```text
refactor(dano): consolidate app layout and remove standalone package split
```

PR 描述必须写明：没有运行时协议拆分、没有独立 protocol package、没有 HTTP/SSE 行为变更；本次只做 app 内部 protocol 文件抽取，类型导入保持 type-only。

PR 描述必须包含：

1. 前端迁到 `apps/dano/web`。
2. bridge 迁到 `apps/dano/src/bridge`，不再是 workspace package。
3. protocol types 迁到 `apps/dano/types/protocol.ts`。
4. `@dano/app` 是唯一 workspace package。
5. server/web 新产物路径。
6. `standalone` 与 WebSocket 误导命名清理范围。
7. 明确没有前后端独立部署或协议拆分。
8. Docker 拓扑没有变化。
9. 完整测试命令与结果。
10. 容器 smoke 是否执行。
11. 根产品版本 patch bump。

PR base 必须是 upstream `main`。

## 10. Definition of Done

- [ ] `packages/svelte` 已删除。
- [ ] `packages/bridge` 已删除。
- [ ] `packages/*` workspace pattern 已删除。
- [ ] 前端位于 `apps/dano/web`。
- [ ] bridge 位于 `apps/dano/src/bridge`。
- [ ] protocol types 位于 `apps/dano/types/protocol.ts`。
- [ ] `apps/dano` 是唯一 `@dano/app` workspace package。
- [ ] `apps/dano/package.json` 无独立 `version`。
- [ ] root `package.json` patch version 已递增。
- [ ] `build:bridge`、`verify:bridge-build`、旧 svelte/standalone 脚本已删除。
- [ ] server 产物位于 `apps/dano/dist/server`。
- [ ] web 产物位于 `apps/dano/dist/web`。
- [ ] 无 `web-dist`、`dist/bridge`、`dist/bridge/standalone`。
- [ ] 当前维护源码、脚本和运行文档中无 `standalone` 残留，`docs/adr/**` 和本重构计划除外。
- [ ] 当前维护源码、脚本和运行文档中无 `WsRpcAdapter`、`WsClient`、`ws-rpc-adapter` 残留，`docs/adr/**` 和本重构计划除外。
- [ ] 没有新增 protocol package、CORS 或独立部署。
- [ ] HTTP/SSE 接口和前端行为未改变。
- [ ] Docker 仍是一个 app 镜像加 nginx。
- [ ] `pnpm run check` 通过。
- [ ] `pnpm run test` 通过。
- [ ] `pnpm run build` 通过。
- [ ] 直接启动 smoke 通过。
- [ ] 有容器运行时时，部署 smoke 通过。
- [ ] PR 已提交到 upstream `main`。
