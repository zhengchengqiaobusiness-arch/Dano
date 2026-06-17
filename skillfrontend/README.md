# skillfrontend — Dano Skill 管理后台

管理员/实施人员用的后台,管理 Skill 全生命周期(阶段一接入生成 + 阶段三运维)。
与"客户使用的调用前端"分开:那个给员工自然语言办事,这个给管理员管 skill。

技术栈:Vite + React + TypeScript + Ant Design。

## P0 已实现(纯用现有网关 API,零后端改动)

- 租户:`POST /tenants` 建/进入,api_key 存 localStorage。
- Skill 目录:`GET /v1/skills` 列表(名称/类型/风险/需确认/操作)。
- Skill 详情:`GET /v1/skills/{id}` 参数 schema + `GET /v1/tools` 的 function-calling 工具定义。
- 测试调用:`POST /v1/skills/{id}/invoke`(按 input JSON + confirm)→ 显示 state/输出/事实核查。

## 运行

先起后端网关(默认 :8000):
```bash
cd ../back
uvicorn dano.gateway.app:app --host 0.0.0.0 --port 8000
```
再起前端(dev 自动把 /v1、/tenants 等代理到 :8000):
```bash
cd skillfrontend
npm install
npm run dev          # 打开 http://localhost:5173
# 网关不在 :8000 时:  DANO_GATEWAY=http://host:port npm run dev
```

构建产物:`npm run build` → `dist/`(生产用 nginx 静态托管,反代网关)。

## 后续(待做)

- P1 接入向导(阶段一):导 swagger → 预览选类别 → 声明写流程 → 生成(需后端 onboarding 异步+进度)。
- P2 运维(阶段三):lifecycle 状态/暂停/恢复/自愈 + 生成追溯/评审(需后端补 source/generation-runs/suspend 等薄端点)。
