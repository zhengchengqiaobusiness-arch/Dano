# 生产环境 OA OAuth2 登录配置

本文说明如何为生产 Dano 配置现有 OA 的 OAuth2 登录。所有地址和凭据均使用占位符；实际值只写入生产服务器的 Deploy Control Directory `.env`，不要提交到仓库。

## 登录链路

```text
Dano 浏览器
  -> OA 登录/授权页
  -> https://<DANO_HOST>/api/auth/callback?code=...&state=...
  -> Dano 服务端使用 code 换取 token
  -> Dano 服务端读取 OA 用户身份
  -> 浏览器回到原 Dano 页面，保留登录前的会话和文件
```

授权页必须使用 OA 的真实浏览器 Origin。不要把 OA 页面代理到 Dano 同源地址；OA 内部跳转到 `/login` 等相对路径时，应继续由 OA Origin 处理。

Dano 不感知 OA 的具体用户分类、角色或业务字段。OA 身份接口只需向 Dano 提供稳定且唯一的 `userId`（也兼容字段名 `id`），以及可选的用户名和头像。

## 上线前提

配置前先确认以下条件：

1. Dano 有对外可访问且证书受信任的 HTTPS 地址，例如 `https://<DANO_HOST>`。
2. 用户浏览器可以访问 OA 登录页。
3. Dano 生产服务器可以访问 OA 的 token、身份和撤销接口。
4. OA 中可以创建或维护 OAuth2 Client，并登记精确的 Redirect URI。
5. OA 服务端证书链被 Dano 容器信任；不要关闭全局 TLS 校验。

当前排查曾观察到：同一个 OA API 从本机访问可返回 JSON，从 Dano 生产服务器访问则由 OA nginx 返回 403。是否存在源 IP 白名单尚未得到 OA 服务器配置证据，因此不能直接断言原因。上线前必须由 OA 或网络维护方确认生产服务器到下述 API 的访问路径，并消除 403；修改 Client ID、Client Secret 或 Dano 回调地址不会解决网络层 403。

## 一、在 OA 创建 OAuth2 Client

在 OA 管理端创建一个专供生产 Dano 使用的 confidential client。不要复用本地测试 Client。

| OA Client 配置 | 生产值 |
| --- | --- |
| Client ID | OA 生成的生产 Client ID |
| Client Secret | OA 生成的生产 Secret，只交给部署端保存 |
| 授权模式 | `authorization_code`、`refresh_token` |
| Redirect URI | `https://<DANO_HOST>/api/auth/callback` |
| Scope | OA 实际允许读取当前用户身份的 Scope |
| 状态 | 启用 |
| Access Token 有效期 | 按 OA 运维策略设置 |
| Refresh Token 有效期 | 按 OA 运维策略设置；希望持续登录时应覆盖预期登录周期 |

Redirect URI 必须逐字符一致，包括：

- `https` scheme；
- 主机名；
- 端口（非默认端口时）；
- 固定路径 `/api/auth/callback`；
- 不带末尾 `/`、query 或 fragment。

如果 OA 支持多个 Redirect URI，只登记实际使用的生产地址；本地 `localhost`、临时 relay 和测试地址不要带入生产 Client。

## 二、确认 OA 接口契约

当前已验证的 OA 适配契约如下，部署时用 OA 的实际 Origin 替换占位符：

| 用途 | 地址或行为 |
| --- | --- |
| 浏览器授权页 | `<OA_BROWSER_ORIGIN>/sso` |
| Token 接口 | `<OA_API_ORIGIN>/admin-api/system/oauth2/token` |
| 当前用户身份 | `<OA_API_ORIGIN>/admin-api/system/oauth2/user/get` |
| Token 撤销 | 对 Token 接口发送 `DELETE`，token 放在 query 中，Client 使用 HTTP Basic 认证 |
| 固定 Provider Header | `{"tenant-id":"1"}` |
| Code 换 Token | 除 code 和 Redirect URI 外，需要再次提交本次 callback state |
| 响应包装 | HTTP 200 的 `{code,data}`；`code` 表示 OA 业务结果 |

当前 OA 的浏览器 Origin 和 API Origin 均为 HTTP，因此按当前现状部署时，下面两个 insecure opt-in 都要设为 `true`。如果 OA 后续提供可信 HTTPS，再把对应地址改为 HTTPS 并关闭 opt-in。

`tenant-id` 在这里仅是 OA 接口要求的固定传输 Header，不进入 Dano 用户身份，也不表示 Dano 支持多租户。

OA 身份响应的 `data` 中必须包含非空且稳定的 `userId` 或 `id`。Dano 可选读取 `displayName`、`nickname`、`name`、`username` 之一作为显示名，以及 `avatarUrl` 或 `avatar` 作为头像。

如果生产 OA 的 Client 契约与上表不同，应修改 Dano 环境变量匹配真实契约，而不是复制测试环境值后猜测。

## 三、配置生产 Dano

在生产服务器的 `/opt/dano/deploy/.env` 中配置以下内容。示例不包含真实 OA 地址和秘密：

```dotenv
# OA 服务标识。使用稳定的 OA 服务端 Origin；Dano 不依赖 OIDC discovery。
DANO_OAUTH_ISSUER=<OA_ISSUER_ORIGIN>

# 浏览器从 Dano 跳转到这里登录。
DANO_OAUTH_AUTHORIZATION_ENDPOINT=<OA_BROWSER_ORIGIN>/sso
DANO_OAUTH_ALLOW_INSECURE_AUTHORIZATION_ENDPOINT=true

# 仅由 Dano 服务端访问。
DANO_OAUTH_TOKEN_ENDPOINT=<OA_API_ORIGIN>/admin-api/system/oauth2/token
DANO_OAUTH_IDENTITY_ENDPOINT=<OA_API_ORIGIN>/admin-api/system/oauth2/user/get
DANO_OAUTH_API_ORIGIN=<OA_API_ORIGIN>
DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS=true

# OA 生产 Client。Secret 不得进入源码、镜像层、命令参数或日志。
DANO_OAUTH_CLIENT_ID=<OA_PRODUCTION_CLIENT_ID>
DANO_OAUTH_CLIENT_SECRET=<OA_PRODUCTION_CLIENT_SECRET>
DANO_OAUTH_CLIENT_AUTH_METHOD=client_secret_basic
DANO_OAUTH_SCOPE=<OA_USER_IDENTITY_SCOPE>

# 当前 OA Provider Adapter 的已验证兼容项。
DANO_OAUTH_PROVIDER_HEADERS_JSON={"tenant-id":"1"}
DANO_OAUTH_SEND_STATE_TO_TOKEN_ENDPOINT=true
DANO_OAUTH_REVOCATION_TRANSPORT=delete-query-basic
DANO_OAUTH_REVOCATION_ENDPOINT=<OA_API_ORIGIN>/admin-api/system/oauth2/token

# 必须与 OA Client 中登记的值逐字符一致。
DANO_OAUTH_REDIRECT_URI=https://<DANO_HOST>/api/auth/callback

# Dano 用于加密每个 Login Session 的 Provider Credential。
# Release Build 会初始化并保留这对值；手工 Compose 部署必须提供完整的一对。
DANO_OAUTH_CREDENTIAL_KEY=<32_RANDOM_BYTES_BASE64URL>
DANO_OAUTH_CREDENTIAL_KEY_VERSION=dano-deploy-v1
```

### Client 认证方式

当前 OA 撤销接口的 `delete-query-basic` 固定使用 HTTP Basic。Token 接口的 `DANO_OAUTH_CLIENT_AUTH_METHOD` 必须以生产 OA 的真实契约为准：

- OA Token 接口要求 HTTP Basic 时，使用 `client_secret_basic`；
- OA Token 接口要求在表单中提交 Client 凭据时，使用 `client_secret_post`（Dano 默认值）。

不要通过反复试错生产 Secret 来判断；应从 OA Client 配置或一次经过脱敏的接口请求确认。

### HTTP-only OA

优先为 OA API 配置可信 HTTPS。如果 OA 当前只能提供 HTTP：

- 仅授权页是 HTTP：设置 `DANO_OAUTH_ALLOW_INSECURE_AUTHORIZATION_ENDPOINT=true`；
- issuer、token、身份、撤销或 API Origin 任一是 HTTP：设置 `DANO_OAUTH_ALLOW_INSECURE_SERVER_ENDPOINTS=true`。

第二项意味着 Client Secret 和临时 token 会在 Dano 到 OA 的链路上明文传输，只是现有 OA 的兼容开关，不提供 relay、隧道或额外保护。Dano 的生产 callback 仍必须使用受信任的 HTTPS。

### Credential Encryption Key

`DANO_OAUTH_CREDENTIAL_KEY` 必须是 32 个随机字节的 base64url 编码，不能是 OA Client Secret。正常使用 `pnpm run deploy:release` 时，发布脚本会在 Deploy Control Directory 中首次生成并在后续发布中保留它；发现 key/version 只存在一个时会停止发布。

手工 Compose 部署必须从受控 Secret 来源一次性写入完整 key/version。`.env` 权限设为 `0600`，不要把 key 打印到终端、CI 输出或聊天记录。除非明确要让所有现有 Dano Login Session 失效，否则不要更换该 key。

## 四、部署

先保证 `/opt/dano/deploy/.env` 已包含完整 OAuth 配置，再从同步后的 `main` 执行标准发布：

```bash
DANO_REPO_URL=git@github.com:zhengchengqiaobusiness-arch/Dano.git \
DANO_GIT_REF=main \
pnpm run deploy:release
```

Release Build 会先在新镜像中运行：

```bash
node ./dist/server/main.js --validate-config
```

该 Gate 使用与生产启动相同的配置解析和 Provider TLS 检查。失败时不会替换正在运行的容器。不要为了通过 Gate 而关闭 TLS 校验或临时改成另一个 Redirect URI。

## 五、生产验收

API 健康检查不能代替真实登录验收。使用受控浏览器完成以下完整流程：

1. 打开生产 Dano，保持匿名状态。
2. 先发送一条消息并上传一个小文件，记录当前会话。
3. 在左上角菜单中点击登录。
4. 确认浏览器进入 OA 的真实 Origin；有现存 OA Browser Session 时立即 SSO 回调是正常行为。
5. 确认回调地址是 `https://<DANO_HOST>/api/auth/callback`，随后回到原 Dano 页面。
6. 确认左上角菜单显示 OA 用户名。
7. 确认登录前的对话和文件仍在。
8. 发送普通模型消息，确认 SSE 回复正常。
9. 触发一次需要 OA 业务 API 的 Skill，确认服务端 Provider Credential 可用。
10. 在 token 过期场景确认 Dano 先用 refresh token 刷新；刷新失败时提示重新登录。取消后回到匿名状态，确认后跳转 OA 登录。
11. 从 Dano 登出，确认当前 Dano Login Session 结束且页面可继续匿名使用。

有条件时再用第二个浏览器用户执行相同流程，确认两个登录用户的会话、文件和 Provider Credential 互不共享。

## 六、故障定位

| 现象 | 优先检查 |
| --- | --- |
| 点击登录后立即回 Dano | 可能只是 OA 已有 Browser Session；确认 callback 是否收到 code/state，不要直接判定失败或成功 |
| 回到 Dano 后提示登录失败 | 按 `callback_received`、token exchange、identity 三个阶段定位；只记录 HTTP 状态、OA 业务码和失败阶段 |
| OA 报 Redirect URI 不匹配 | 比较 OA Client 和 `DANO_OAUTH_REDIRECT_URI` 的 scheme、host、port、path，必须逐字符一致 |
| Token/身份接口返回 HTML 404/405 | 检查是否遗漏 `/admin-api`，以及请求是否打到了 OA nginx 的错误 location |
| OA HTTP 200 但登录失败 | 检查响应顶层 `code`；Dano 会读取 `{code,data}`，业务码非成功仍是 Provider 失败 |
| 只有生产服务器返回 nginx 403 | 先查 OA/网络侧访问控制与生产出口 IP；不要通过修改 Secret、关闭 TLS 或增加 relay 掩盖问题 |
| `SELF_SIGNED_CERT_IN_CHAIN` | 把签发 OA 证书的 CA 加入容器信任链并重建容器；不要设置全局跳过 TLS 校验 |
| refresh 失败 | 确认 OA Client 启用 `refresh_token`、refresh token 未过期且固定 Header/Client 认证方式仍正确 |

日志中禁止记录 authorization code、state、Client Secret、access token、refresh token、Cookie、完整 Provider 响应或用户账号。排障信息只保留稳定的阶段名、HTTP 状态和 OA 业务码。

## 七、会话和登出边界

Dano Login Session、OA Browser Session 和 OA token 是三个独立状态：

- Dano 登录成功不代表新建了 OA Browser Session；已有 OA 登录时可以直接 SSO 回调。
- Dano 登出会结束当前 Dano Login Session，并按配置撤销属于该会话的 OA token。
- Dano 登出不会自动清除 OA 域名下的 Browser Session。
- 用户在 OA 页面登出，也不会自动通知已有的 Dano Login Session；只有后续 token 检查或 refresh 失败时，Dano 才提示重新登录。

除非 OA 以后提供并验证 OIDC Session Management、RP-Initiated Logout、Front-Channel Logout 或 Back-Channel Logout，否则不要声称 OA 与 Dano 支持双向同步登出。
