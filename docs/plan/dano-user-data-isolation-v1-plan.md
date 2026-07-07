# Dano 用户数据隔离第一版计划

## 背景

目标是在不修改 `pi-coding-agent` 多用户模型的前提下，由 Dano 负责用户、会话和 Runtime Workspace 归属。

已确认约束：

- 一个 Dano 部署对应一个公司，第一版不引入 `tenant_id`。
- Pi 只接收一个 Runtime Workspace，不需要知道 `user_id`、`session_id` 或 `workspace_id`。
- Pi 全局 agent 配置目录可通过 `PI_CODING_AGENT_DIR` 指定；`settings.json`、`SYSTEM.md` 和 Heimdall 全局配置都可以放在该目录。
- 当前 Dano 使用 `@josephyoung/pi-heimdall@0.2.12`；Heimdall 会读取全局 `getAgentDir()/heimdall.json`，并与项目级 `.pi/heimdall.json` deep merge。第一版不需要给每个 Runtime Workspace 放 `.pi/heimdall.json`。

## 目标

第一版只做运行目录和 Runtime Workspace 隔离基础，不引入完整用户数据库。

- 容器内运行根从 `/tmp/dano` 迁到 `/opt/dano/runtime-data`。
- 宿主 `/opt/dano/runtime-data` 挂载到容器 `/opt/dano/runtime-data`。
- 默认 Pi/Dano runtime 配置写入全局 agent dir：

```text
/opt/dano/runtime-data/.pi/agent/
  SYSTEM.md
  settings.json
  heimdall.json
```

- Runtime Workspace 使用不可猜测 ID：

```text
/opt/dano/runtime-data/workspaces/ws_<random>/
```

- 第一版复用现有 Dano session 文件作为会话身份，`chatId` 暂时等于当前 Dano `sessionId`。Dano session 绑定到一个 Runtime Workspace：

```text
sessionId -> workspaceId -> workspacePath
```

第一版没有真实登录用户时，`userId` 使用 `anonymous` 兜底；后续 token 接入后再把 session 查询和恢复路径绑定到真实 userId。

## 非目标

第一版不做：

- 不新增 `users`、`chats`、`workspaces` 数据库表。
- 不引入 Postgres/SQLite 作为 Dano P0 会话存储。
- 不做 SaaS 多租户、项目共享、多人协作、workspace 转移。
- 不把上传文件先拆到 `users/<user_id>/uploads` 再 copy/link 到 workspace。
- 不迁移历史 `/tmp/dano` 数据。
- 不给每个 workspace 写默认 `.pi/SYSTEM.md`、`.pi/settings.json`、`.pi/heimdall.json`。
- 不新增独立 `workspace.json` 旁路元数据文件；第一版必须复用现有 session header / session path 恢复能力。

## 目标目录

宿主和容器内保持同构路径：

```text
/opt/dano/runtime-data/
  .pi/
    agent/
      SYSTEM.md
      settings.json
      heimdall.json

  users/
    <user_id>/
      memory/
      preferences/
      uploads/        # 后续阶段启用，第一版不接上传流

  workspaces/
    ws_<random>/
      uploads/
      ...
```

Pi 只看到 Runtime Workspace：

```text
/opt/dano/runtime-data/workspaces/ws_<random>
```

## 实施步骤

### 1. 运行根配置

新增或统一一个运行根读取逻辑：

```text
DANO_RUNTIME_DIR=/opt/dano/runtime-data
```

生产默认值改为 `/opt/dano/runtime-data`。本地开发和测试必须通过 `DANO_RUNTIME_DIR` 指到 `/private/tmp/...` 或其他可写临时目录，不能默认写宿主 `/opt`。

`DANO_DEFAULT_WORKSPACE_PATH` / `DANO_DEFAULT_WORKSPACE` 在第一版废弃，不再作为新会话 workspace 选择器。新会话 Runtime Workspace 由 Dano 在 `DANO_RUNTIME_DIR/workspaces/` 下生成。

需要更新：

- `Dockerfile`
- `docker-compose.yml`
- `deploy/docker-entrypoint.sh`
- `scripts/deploy-release.mjs`
- `scripts/deploy-compose.mjs`
- `deploy/README.md`
- `apps/dano/src/main.ts`
- 相关测试

### 2. 全局 agent dir 初始化

`deploy/docker-entrypoint.sh` 不写死路径，从运行根推导：

```sh
runtime_root="${DANO_RUNTIME_DIR:-/opt/dano/runtime-data}"
agent_dir="${PI_CODING_AGENT_DIR:-$runtime_root/.pi/agent}"
export PI_CODING_AGENT_DIR="$agent_dir"
```

入口脚本把 `deploy/runtime-defaults/` 里的文件复制到 `$PI_CODING_AGENT_DIR`，只复制缺失文件，不覆盖用户修改。

`apps/dano/src/main.ts` 的初始化逻辑同步调整：

- 从初始化 `<workspace>/.pi` 改为初始化 `$PI_CODING_AGENT_DIR`。
- 保留 `SYSTEM.md`、`settings.json`、`heimdall.json` 三个默认文件。
- 保留 Heimdall `userNamespace` 默认迁移逻辑，但目标改为 `$PI_CODING_AGENT_DIR/heimdall.json`。

### 3. Runtime Workspace 生成

新建默认会话时，不再使用运行根本身作为 workspace。

第一版生成：

```text
/opt/dano/runtime-data/workspaces/ws_<crypto.randomUUID()>
```

要求：

- workspace ID 不包含 userId/chatId。
- workspace path 必须 resolve 后仍在 `runtimeRoot/workspaces/` 下。
- 继续让 Pi session cwd 指向该 workspace path。
- 上传仍写入 workspace 内 `uploads/`，保持当前 Pi 项目文件引用行为。
- 侧边栏当前已隐藏 workspace 展示；如果后续恢复侧边栏，不得把 `ws_<random>` 当用户可读名称展示，应使用 session title 或显式 display name。

### 4. 会话绑定

第一版不建数据库，也不新增 `workspace.json`。归属绑定以现有 Dano session 为准：

```text
sessionId -> Runtime Workspace cwd
```

恢复已有会话时，先按现有 session 文件能力恢复 cwd。后续第二版再把 `sessionId/chatId -> workspaceId` 做成正式索引或数据库。

### 5. 路径守卫

新增共享路径守卫，所有 workspace 文件访问复用同一个判断：

```text
assertInsideWorkspace(targetPath, workspacePath)
```

第一版至少覆盖：

- 上传写入路径。
- workspace file preview。
- workspace entry list/read。
- 删除 workspace 的未来入口预留。

要求防：

- `../`
- 绝对路径逃逸
- path separator 边界绕过
- symlink 逃逸：第一版采用最小策略，不支持 workspace 内 symlink 作为文件访问入口；存在文件读取用 `realpath` 校验，新文件写入校验父目录 `realpath`。

## 测试计划

最小验证：

1. 全新 runtime-data 启动后生成：

```text
/opt/dano/runtime-data/.pi/agent/SYSTEM.md
/opt/dano/runtime-data/.pi/agent/settings.json
/opt/dano/runtime-data/.pi/agent/heimdall.json
```

2. 重启不覆盖手改过的 `SYSTEM.md/settings.json/heimdall.json`。
3. Dano 进程内 `PI_CODING_AGENT_DIR` 指向 `.pi/agent`。
4. 新会话 Runtime Workspace 在 `runtime-data/workspaces/ws_<random>`，不在运行根本身。
5. 新 Runtime Workspace 不包含默认 `.pi` 配置文件。
6. 上传仍写入当前 workspace 的 `uploads/`，prompt 中仍以项目文件引用传给 Pi。
7. `workspace-files/preview` 不能读取 workspace 外文件。
8. Heimdall 仍读取全局 `heimdall.json` 并生效。
9. `DANO_DEFAULT_WORKSPACE_PATH` / `DANO_DEFAULT_WORKSPACE` 不再决定新会话 Runtime Workspace。

命令层验证：

```bash
pnpm exec vitest run apps/dano/src/__tests__/main.test.ts
pnpm exec vitest run apps/dano/src/bridge/__tests__/server.test.ts
pnpm exec vitest run apps/dano/src/bridge/__tests__/bridge-rpc-adapter.test.ts
pnpm run check:type
```

部署验证：

```bash
podman build -t localhost/dano-runtime-layout-v1 .
DANO_RUNTIME_DIR=/private/tmp/dano-runtime-layout-v1/runtime-data \
  DANO_COMPOSE=podman \
  node scripts/deploy-compose.mjs up
node scripts/smoke-dano-deploy.mjs
node scripts/deploy-compose.mjs down
```

## 后续阶段

第二版再做：

- 从 backend token/JWT 获取真实 `userId`。
- `chatId -> workspaceId` 正式索引。
- 所有 chat/session 查询按 `currentUser.id` 校验。
- 用户长期数据目录 `users/<user_id>/`。
- 用户原始上传池 `users/<user_id>/uploads/`。
- 删除聊天时停止 runtime、删除 workspace、保留或清理用户原始上传。
