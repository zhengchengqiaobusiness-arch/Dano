# 当前 OA 的系统来源与 OAuth2 退出能力

研究日期：2026-09-15。范围：本次指定的官方 OA；不复用其他 OA 站点的退出结论。本文不记录部署地址、账号标识或认证材料。

## 结论

当前 OA 是 **RuoYi Office**。官方公开后端沿用芋道（Yudao）模块与认证实现，前端采用 Vue 3 / Vben Admin。它支持 OAuth2 登录和令牌撤销，但当前公开源码与实际部署前端**没有提供可供 Dano 跳转调用、退出 OA 后自动返回 Dano 的单点登出契约**。这不是 OAuth2 登录无法实现，而是登录和浏览器单点登出属于不同能力。[官方后端][backend]、[官方前端][frontend]

目前能做到的是：Dano 退出自己的会话并撤销自己持有的 OAuth 凭据；OA 网页通过自己的退出动作清理 OA 登录状态。不能把前者标为“已同时退出 OA”。若要一键退出两边，应先取得 OA 提供的浏览器退出入口；标准方案是 OIDC RP-Initiated Logout，私有但明确约定的 OA 同源退出页面也可以。只修改 Dano 的接口地址，不能让当前 Bearer-token API 自动变成浏览器退出页面。[令牌撤销实现][revoke]、[OA 退出实现][auth-store]、[OIDC 标准][oidc]

## 系统来源：哪些已经确认

- 官方后端仓库名为 `ruoyi-office`，README 标明产品为 RuoYi Office，采用 Spring Cloud Alibaba / Spring Boot 等技术；目录是 `yudao-framework`、`yudao-module-system` 等，Java 包名沿用 `cn.iocoder.yudao`。这些是源码事实；“芋道体系的扩展项目”是据此作出的系统来源判断，不能简单等同于原版 RuoYi-Vue。[README][readme]、[认证控制器][auth-controller]
- 本次 OAuth 路径由项目自己的 `OAuth2OpenController` / `OAuth2GrantService` / `OAuth2TokenService` 实现，使用 Spring Security 的认证上下文；不是在这里转发到 Keycloak 或调用 Sa-Token 的单点退出接口。不能根据“支持 OAuth2”就套用其他身份系统的 logout URL；也不据此排除部署中存在未公开的其他认证组件。[OAuth 控制器][oauth-controller]、[撤销服务][revoke]
- 官方前端仓库为 `ruoyi-office-vben`。本次实际部署公开入口的构建标识为 Vben Admin 5.7.0，构建日期 2026-09-13；公开模块的退出流程与官方前端源码相符。[前端源码][frontend]、[退出状态管理][auth-store]
- 公开后端核对固定版本：`ad56d726368d8c321644d47898a76d3e86f1b548`；公开前端核对固定版本：`98cab3e08bc7fbf3b69920c009224bc4251a3844`。未取得部署服务器的构建清单，因此不宣称线上后端与该提交逐字相同。

## 三种“退出”不是同一件事

| 操作 | 现有接口 / 行为 | 实际影响 |
| --- | --- | --- |
| Dano 本地退出 | Dano `POST /api/auth/logout` | 结束对应 Dano Login Session、断开相关连接、清理 Dano Cookie，返回匿名状态；配置撤销端点时尝试撤销该 OAuth 凭据 |
| 撤销 Dano 的 OA 授权令牌 | OA `DELETE /system/oauth2/token` | 校验 OAuth Client；删除属于该 Client 的指定 access token 及其关联 refresh token |
| OA 网页退出 | OA 前端携自身 Bearer 调用 `POST /system/auth/logout`，再重置前端状态并跳回 OA 登录页 | 退出这份 OA 浏览器凭据；不是第三方可直接导航的 SSO logout URL |

Dano 行为见 [`oauth-authentication.ts`](../../apps/dano/src/bridge/oauth-authentication.ts) 与 [`oauth-provider.ts`](../../apps/dano/src/bridge/oauth-provider.ts)。OA 行为见 [OAuth 控制器][oauth-controller]、[令牌服务][token-service]、[前端退出 API][auth-api]。

### 撤销是否影响所有用户、所有设备或所有应用

**所核对的撤销调用不是“按用户全部踢下线”。** `revokeToken(clientId, accessToken)` 先查指定令牌，再检查令牌的 Client 与请求的 Client 一致，最后调用单参数 `removeAccessToken(accessToken)`。后者删除这条访问令牌的数据库记录、Redis 缓存及关联刷新令牌。[撤销服务][revoke]、[令牌服务][token-service]

同一服务确实另有按用户删除多份令牌的重载，但 OAuth 撤销控制器和普通登录退出调用的都是**按指定 token 删除**的路径，不能因存在其他重载就推断退出会清理全部登录。[认证服务][auth-service]、[令牌服务][token-service]

OA 网页登录获得的凭据，与授权给 Dano 的凭据，是两次不同流程的产物。Dano 撤销自己的令牌，不会因此取得 OA 网页凭据，也没有源码证据表明会连带撤销它。[前端登录状态管理][auth-store]、[OAuth 授权控制器][oauth-controller]

### 为什么直接跳到 `/system/auth/logout` 不行

这个接口接受 POST；从当前请求的认证头或配置的 token 参数中取凭据，然后删除该凭据。普通页面跳转是 GET，也不会自动携带 OA 前端存储的 Bearer 认证头。将 Dano 持有的 OAuth token 填进去，删除的仍然是 Dano 这份令牌。[认证控制器][auth-controller]、[认证服务][auth-service]

还有一个容易误判的点：该接口在请求没有 token 时也返回成功。因此，“HTTP 200 / 返回 true”本身不能证明 OA 浏览器已经退出。[认证控制器][auth-controller]

## 实际部署前端的只读核验

本次读取的是页面公开加载的 JavaScript，没有执行退出、读取浏览器存储或调用修改接口：

- 入口资产 `jse/index-index-ChcnbE6p.js` 引入 `bootstrap-CM4jA-4R.js`，后者引入 `request-uLFXa0V-.js`。
- 请求模块的退出 API 使用 `POST /system/auth/logout`，请求体为空对象，认证头为调用者传入的 Bearer。
- 认证状态模块取自身 `accessToken` 调用退出 API；即使 API 抛错也继续重置本地状态，随后路由跳到 OA 登录页。被引用的 `src-ooCyixQm.js` 中，重置函数逐个调用 Pinia store 的 `$reset()`。
- 此处 `redirect` 编码的是 OA 当前路由 `fullPath`，作用是下次登录回到原 OA 页面；不是 `post_logout_redirect_uri`，也不是退出后回 Dano 的协议。
- 实际 `routes-NuucHHme.js` 的认证子路由包含登录、验证码登录、二维码登录、社交登录和 `sso-login`；所检查路由和请求模块没有 `sso-logout`、`end_session_endpoint` 或 `post_logout_redirect_uri` 实现。

这些是当前部署的前端证据；公开源码中的对应实现是 [前端 API][auth-api]、[认证状态管理][auth-store]、[基础路由][routes]。资产哈希随发布可能变化，不能当作永久接口。未找到相关字符串是有限范围的否定证据，不排除未公开的后端 / 网关扩展。

本次还只读请求了 `/.well-known/openid-configuration` 和 `/admin-api/.well-known/openid-configuration`，两者均返回 HTTP 403、`text/html`，没有取得 OIDC Metadata。403 可能来自访问控制，不能用来证明服务器不存在 OIDC 退出端点；上述结论主要依据已公开的接口及实际前端行为。

## 有无标准 OAuth2 / OIDC 登出方案

OAuth2 核心定义授权和令牌流程，不保证提供浏览器退出地址。RFC 7009 标准化的是令牌撤销；当前 OA 的 DELETE 接口具有撤销语义，但 HTTP 方法不同，不能称作完整遵循 RFC 7009 的 POST 端点。[RFC 6749][oauth-core]、[RFC 7009][revocation-standard]

OIDC RP-Initiated Logout 才定义“第三方让浏览器跳到身份提供方退出，再回第三方”的操作：使用退出端点，通常通过 `end_session_endpoint` 获得；回跳使用 `post_logout_redirect_uri`，并要求身份提供方验证已登记地址等条件。没有 Discovery 只能说明没有通过该方式发现能力，不能单独证明不存在私有退出 URL。[OIDC 标准][oidc]

当前官方源码中：

- OAuth 控制器公开 `POST /token`、`DELETE /token`、`POST /check-token`、`GET /authorize`、`POST /authorize`，没有在此提供浏览器 end-session 操作。[控制器][oauth-controller]
- OAuth Client 数据模型有授权回调 `redirectUris`，没有专门的登出回调注册字段。不能把登录回调字段自行解释成退出回调。[Client 模型][client-model]
- 前端基础认证路由有 `sso-login`，没有相应单点退出路由；前端 logout 的布尔参数只是是否保留内部返回路由。[基础路由][routes]、[退出状态管理][auth-store]

所以当前可以确认“令牌级退出可用，尚无可用的浏览器联动登出契约”，不能承诺“填一个 OAuth logout URL 就能同时退出”。

## 对 Dano 的处理意见

1. 保留可靠的本地退出及当前 Provider 的令牌撤销；界面不要声称已经退出 OA。
2. 不把 OA 登录页当退出页，不通过复制 OA 浏览器 token、清理跨域存储或猜测 URL 来实现联动退出。
3. 如果 OA 提供已支持的 OIDC 退出端点或私有退出页面，Dano 可以增加通用的浏览器退出跳转适配；OA 页面负责清理自己的状态，Dano 接收退出后的返回。是否需要新配置取决于已证实的端点发现 / 登记契约，而非现在假设。
4. 在目前不能修改 OA 的前提下，公开证据支持的操作仍是“两边各自退出”；一键跨系统退出不能仅靠调整现有撤销端点来补齐。若只想换账号，应先使用 OA 原有退出操作，再发起新授权，不等同于已实现全局退出。

本次仅研究并形成文档；没有实施新登出方案，没有改变当前登录状态、OA 配置或 Dano 运行配置。对线上撤销后的 access / refresh 是否全部失效，以及 OA 浏览器会话是否仍可复用，仍应以另行授权的真实退出验收为准；本文件不把源码分析算成该测试已通过。

[backend]: https://github.com/yuqing2026/ruoyi-office/tree/ad56d726368d8c321644d47898a76d3e86f1b548
[frontend]: https://github.com/yuqing2026/ruoyi-office-vben/tree/98cab3e08bc7fbf3b69920c009224bc4251a3844
[readme]: https://github.com/yuqing2026/ruoyi-office/blob/ad56d726368d8c321644d47898a76d3e86f1b548/README.md
[oauth-controller]: https://github.com/yuqing2026/ruoyi-office/blob/ad56d726368d8c321644d47898a76d3e86f1b548/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/controller/admin/oauth2/OAuth2OpenController.java
[auth-controller]: https://github.com/yuqing2026/ruoyi-office/blob/ad56d726368d8c321644d47898a76d3e86f1b548/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/controller/admin/auth/AuthController.java
[auth-service]: https://github.com/yuqing2026/ruoyi-office/blob/ad56d726368d8c321644d47898a76d3e86f1b548/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/service/auth/AdminAuthServiceImpl.java
[revoke]: https://github.com/yuqing2026/ruoyi-office/blob/ad56d726368d8c321644d47898a76d3e86f1b548/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/service/oauth2/OAuth2GrantServiceImpl.java
[token-service]: https://github.com/yuqing2026/ruoyi-office/blob/ad56d726368d8c321644d47898a76d3e86f1b548/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/service/oauth2/OAuth2TokenServiceImpl.java
[client-model]: https://github.com/yuqing2026/ruoyi-office/blob/ad56d726368d8c321644d47898a76d3e86f1b548/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/dal/dataobject/oauth2/OAuth2ClientDO.java
[auth-api]: https://github.com/yuqing2026/ruoyi-office-vben/blob/98cab3e08bc7fbf3b69920c009224bc4251a3844/apps/web-ele/src/api/core/auth.ts
[auth-store]: https://github.com/yuqing2026/ruoyi-office-vben/blob/98cab3e08bc7fbf3b69920c009224bc4251a3844/apps/web-ele/src/store/auth.ts
[routes]: https://github.com/yuqing2026/ruoyi-office-vben/blob/98cab3e08bc7fbf3b69920c009224bc4251a3844/apps/web-ele/src/router/routes/core.ts
[oauth-core]: https://www.rfc-editor.org/rfc/rfc6749#section-3
[revocation-standard]: https://www.rfc-editor.org/rfc/rfc7009#section-2
[oidc]: https://openid.net/specs/openid-connect-rpinitiated-1_0.html
