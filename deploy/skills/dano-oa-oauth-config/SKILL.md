---
name: dano-oa-oauth-config
description: Configure or replace Dano's production OA OAuth2 login provider during deployment, including client registration, required and conditional environment settings, safe activation, and real-browser login/logout acceptance. Use when an administrator asks an agent to set up or change OA login; configuration guidance alone does not authorize production changes.
---

# 配置 Dano 生产 OA OAuth2 登录

供系统管理员的 agent 在首次部署或更换 OA 时使用。完成标准是配置通过生产启动校验，真实浏览器完成登录、显示身份和登出；仅配置文件写入、health 成功或 token 接口成功不算完成。

## 1. 确认目标与授权

本 skill 对应的源码仓库是 [zhengchengqiaobusiness-arch/Dano](https://github.com/zhengchengqiaobusiness-arch/Dano)，发布基线为 `main` 分支（本项目 Git remote 名为 `upstream`，即 `upstream/main`）。文中的代码和配置路径均相对于该仓库根目录，不是 OA 系统的源码路径。

先阅读该仓库 `main` 分支的 [生产环境 OA OAuth2 登录配置](https://github.com/zhengchengqiaobusiness-arch/Dano/blob/main/deploy/生产环境-OA-OAuth2-登录配置.md)（本地副本：[配置文档](../../生产环境-OA-OAuth2-登录配置.md)），并核实同一分支的 `apps/dano/src/main.ts`、`.env.example` 和 `docker-compose.yml`。记录使用的 commit；只改现有部署配置时，还需对照当前运行镜像对应 commit 的解析器，不能假定旧镜像支持最新 `main` 的全部配置。文档中的旧环境路径和 HTTP 示例不是新 OA 的默认配置。

记录目标服务器、Dano HTTPS 入口、部署控制目录、当前镜像、实际 Compose project/全部文件及 overlay。现有生产部署控制目录是 `/opt/dano/deploy`；其他部署以管理员指定及实际拓扑为准。

确认以下输入后再修改：

- 管理员指定的 OA 浏览器授权页完整 URL、服务端接口及稳定服务标识。
- 创建或维护目标 OA Client 的权限、受控凭据来源和验收账号。
- 是否授权修改生产 `.env`、重建 app 容器，以及必要时恢复原配置。
- 更换 OA 后旧 Dano 登录凭证不能直接当作新 OA 凭证，安排重新登录；保留已有数据，不因更换 OA 自动删除会话或用户目录。

有效的既有配置和已授权范围直接复用。确实缺失的契约、权限或安全决策在修改前集中确认，不猜测 Client Secret、Scope、接口路径或是否接受 HTTP。

## 2. 建立目标 OA 契约

在目标 OA 登记启用的生产 confidential client，支持 `authorization_code`；需要持续登录时同时启用 `refresh_token` 并确认有效期与允许的 Scope。

登记的 Redirect URI 必须与 Dano 完全一致：生产使用受信任 HTTPS，固定路径 `/api/auth/callback`，不带尾部 `/`、query 或 fragment。Dano 入口未变时通常不需更改回调地址。

分别核实浏览器授权页和服务端 token、身份、资料、撤销接口的 method、完整 path、认证方式、headers、响应包装。授权页使用 OA 的真实 origin，支持目标环境实际使用的 Hash 路由；服务端由 Dano 容器直接访问真实 OA API。API 的 `/admin-api/` 前缀按证据保留，不套用代理路径规则。

使用 OA 文档、Client 管理页或经过脱敏的成功请求确认契约；HTTP 200 还需检查 `{code,data}` 的业务结果。身份必须稳定且唯一；资料只补充显示名/头像，不能替换已验证身份。配置资料接口时，按原文档用至少两个真实用户验证业务成功与身份一致性。

## 3. 填写配置

仅向部署控制目录 `.env` 写入实际值。下表中的每项必须明确选择；可选项不适用时清空旧值或恢复默认，避免残留上一个 OA 的契约。

| 必填变量 | 决策 |
| --- | --- |
| `DANO_OAUTH_ISSUER` | 稳定 OA 服务标识 URL，不依赖 OIDC discovery |
| `DANO_OAUTH_AUTHORIZATION_ENDPOINT` | 真实浏览器授权页完整 URL，不是 token 接口 |
| `DANO_OAUTH_TOKEN_ENDPOINT` | 换取、刷新 token 的完整 URL |
| `DANO_OAUTH_IDENTITY_ENDPOINT` | 返回稳定用户身份的完整 URL |
| `DANO_OAUTH_API_ORIGIN` | OA 业务 API origin，仅协议、主机和可选端口，不带 path/query |
| `DANO_OAUTH_CLIENT_ID` / `DANO_OAUTH_CLIENT_SECRET` | 目标 OA 生产 Client 的匹配凭据 |
| `DANO_OAUTH_SCOPE` | 目标 Client 实际允许的权限 |
| `DANO_OAUTH_REDIRECT_URI` | 与 OA 登记值逐字符一致的 Dano callback |
| `DANO_OAUTH_CREDENTIAL_KEY` / `DANO_OAUTH_CREDENTIAL_KEY_VERSION` | Dano 自有加密材料，已有完整配对必须保留 |

首次部署时，标准 release 入口初始化加密 key/version；手工 Compose 必须安全提供完整配对，key 为 32 随机字节的 base64url 编码。缺半对时停止，不覆盖已有 key；OA Secret 不能用作加密 key。

| 可选或条件必填变量 | 默认与条件 |
| --- | --- |
| `DANO_OAUTH_CLIENT_AUTH_METHOD` | 默认 `client_secret_post`；要求 HTTP Basic 时选 `client_secret_basic` |
| `DANO_OAUTH_IDENTITY_TRANSPORT` | 默认 `bearer-get`；introspection 接口选 `token-introspection` |
| `DANO_OAUTH_PROFILE_ENDPOINT` | 可选，同一用户的 Bearer GET 显示资料接口 |
| `DANO_OAUTH_PROVIDER_HEADERS_JSON` | 可选，目标 OA 必需的固定字符串 headers；不能照搬旧 tenant 值 |
| `DANO_OAUTH_SEND_STATE_TO_TOKEN_ENDPOINT` | 默认 `false`；换 code 时要求提交 callback state 才选 `true` |
| `DANO_OAUTH_REVOCATION_TRANSPORT` | 可选，`rfc7009` 或 `delete-query-basic` |
| `DANO_OAUTH_REVOCATION_ENDPOINT` | `rfc7009` 必填；`delete-query-basic` 默认 token endpoint；设置 endpoint 必须设置 transport |
| `DANO_OAUTH_ALLOW_INSECURE_AUTHORIZATION_ENDPOINT` | 默认 `false`；授权页仅 HTTP 且已获明确授权时设 `true` |
| `DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS` | 默认 `false`；服务端 OA 地址需 HTTP 且已接受明文凭据风险时设 `true` |

优先使用可信 HTTPS；HTTP opt-in 不放宽 Dano 生产 callback 的 HTTPS 要求。浏览器 origin 不得静默替换成 Dano 同源代理。新的代理、隧道、网络路径或 OA 网关变更都不属于配置 OAuth Client 的默认授权范围。

写入时保持 `.env` 权限 `0600`，使用仓库安全更新工具或受控 Secret 来源，只更新批准的 OAuth 键并保留其他变量。`scripts/deploy-env-file.mjs` 是可导入的原子更新模块，不是独立 CLI。Secret 不进入命令参数、源码、镜像层、聊天、截图或日志；不输出整个 `.env` 或解析后的 Compose config。对键值存在性和权限做脱敏检查。

## 4. 校验并生效

遵循目标部署的锁与回滚流程；本项目生产修改持有 `/var/lock/dano-production-deploy.lock`。先保存仅宿主机 root 可读的原配置及镜像/Compose 身份，再更新 `.env`。保留 runtime-data、named volumes、加密 key、TLS、nginx、`/web/` 和相邻服务。

使用实际全部 Compose 文件、overlay 和同一个 `--env-file`，在目标镜像执行 `node ./dist/server/main.js --validate-config`。路径以镜像工作目录为准；校验使用生产网络和信任链。完整发布走现有 release 入口，并按项目规则先切到 `main` 执行 `git sync-upstream`。校验失败则恢复原配置，保持原服务，不通过关闭 TLS 校验规避。

仅修改环境变量通常不需要构建镜像，但必须重建 app，不能只 `docker restart`。下面是操作形态，不是可直接粘贴的目标环境配置：

```bash
# project/env_file/compose_files 来自已核实的部署清单。
# compose_files 是含所有 -f 和对应文件路径的 shell 数组。
compose=(docker compose -p "$project" --env-file "$env_file" "${compose_files[@]}")
"${compose[@]}" run --rm --no-deps --entrypoint node app \
  ./dist/server/main.js --validate-config
# 仅在校验成功后执行；保留当前镜像，不重新构建或重启相邻服务。
"${compose[@]}" up -d --no-build --no-deps --force-recreate app
```

如果目标使用 Podman Compose，沿用已核实的等效入口。只有镜像代码、CA 信任材料或其他构建输入改变时才走重新构建流程。等待容器健康并验证首页、API/SSE、`/web/` 仍可用。

## 5. 真实浏览器验收与故障处理

使用管理员环境中可用且已授权的真实浏览器及验收账号，不限定浏览器产品或自动化工具；有明确浏览器选择时遵循管理员要求。完成原文档的生产验收链路：匿名对话/小文件、进入真实 OA 授权页、登录/授权、callback、回到原对话、显示正确用户、原文件保留、模型/SSE 正常、Dano 登出。检查点击授权后的实际页面错误、重定向和安全诊断，不能等待管理员提供报错截图。

需要验证业务凭据时使用已授权、无副作用的 OA 查询，不提交申请或修改业务数据。持续登录按原文档验证 refresh；无法覆盖的过期场景明确列为未验证，不伪称通过。Dano 登出、token 撤销与 OA 浏览器登出是不同状态，不承诺仅靠这些配置实现双向 SSO 登出同步。

失败先比较成功/失败请求的 method、path、Host、headers、body、Client 认证及业务码，检查 Dano 代码与配置；配置或网关原因必须有证据。尤其注意错误 origin、回调不匹配、重复 `client_id`、丢失 `/admin-api/`、资料接口业务失败和 CA 信任。诊断只输出允许的 stage/errorCode/httpStatus 等脱敏字段，不泄露 code、state、token、cookie 或 Secret，不重放一次性授权码。

新配置导致登录回归时恢复原 `.env`，按原 Compose 清单重建 app 并复查健康及登录；保留运行数据，不通过删除数据修复。既有成功路径异常时先排查并恢复原路径，替代 API 检查不能代替浏览器验收。

交付报告列出目标 OA/Client 的非秘密标识、必填与条件项的配置状态、保留的加密材料、实际激活方式、登录/登出与 API/SSE 证据、未验证项和回滚结果。只有完整通过才报告配置完成；缺权限或输入时明确需要什么，不把部署责任转交给管理员猜测。
