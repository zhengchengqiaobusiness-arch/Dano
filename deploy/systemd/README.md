# Runtime Token 自动刷新

后端通过目标网页实际调用的登录 API 获取 Token；不要抓取登录页 HTML。登录请求、凭据字段和 Token 提取路径均由 `DANO_TOKEN_REFRESH_SOURCES` 配置。

最小配置示例：

```dotenv
DANO_TOKEN_REFRESH_KEY=<随机长密钥>
DANO_RUNTIME_CREDENTIALS={"aaa/A-OA-login":{"tenant_name":"点狮信息","username":"admin","password":"<新密码>"}}
DANO_VAULT_ADDR=https://vault.example.internal:8200
DANO_VAULT_TOKEN=<通过部署密钥注入>
DANO_REQUIRE_VAULT=true
DANO_TOKEN_REFRESH_SOURCES={"aaa/A-OA":[{"type":"password_http","url":"http://admin.example:90/admin-api/system/auth/login","verify_url":"http://admin.example:90/admin-api/system/auth/get-permission-info","allow_insecure_http":true,"credentials_ref":"vault://aaa/A-OA-login","body":{"tenantName":"{{tenant_name}}","username":"{{username}}","password":"{{password}}"},"token_path":"data.accessToken","header_name":"Authorization","token_prefix":"Bearer ","interval_seconds":1800}]}
```

正式环境应把账号密码放 Vault，并设置 `DANO_REQUIRE_VAULT=true` 禁止回退本地明文；`DANO_RUNTIME_CREDENTIALS` 只用于单机部署或验证。一个系统可配置多个来源，前一个失败时会自动尝试下一个。`type=http` 可用于其他无验证码 HTTP 登录/刷新接口，并支持 `method`、`headers`、`query`、`body`、`encoding=json|form`、`token_path` 或 `token_header`。

新 Token 在写库前必须通过 `verify_url` 的受保护接口验证；可用 `verify_method`、`verify_headers`、`verify_query`、`verify_success_path`、`verify_success_values` 描述验证约定。只有确实没有验证接口时才可显式设置 `allow_unverified_token=true`。登录和验证默认只允许 HTTPS；可信内网使用 HTTP 时必须显式设置 `allow_insecure_http=true`。自动刷新会更新 Skill 接口调用使用的 Header/Cookie，不会替用户续期浏览器页面自身的 `storageState` 登录会话。

安装定时器：

```bash
sudo install -m 0644 deploy/systemd/dano-token-refresh.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/dano-token-refresh.timer /etc/systemd/system/
sudo install -d -o dano -g dano -m 0750 /etc/dano
sudo install -o dano -g dano -m 0600 deploy/systemd/token-refresh.curl.example /etc/dano/token-refresh.curl
sudo systemctl daemon-reload
sudo systemctl enable --now dano-token-refresh.timer
systemctl list-timers dano-token-refresh.timer
```

把 curl 配置里的刷新密钥替换成后端 `DANO_TOKEN_REFRESH_KEY`；服务以 `dano` 用户运行，如部署用户不同需同步修改 unit。定时器直连 `127.0.0.1:8077`。公网 Nginx 必须引用 `deploy/nginx/dano-internal-deny.conf.example` 同等规则，禁止代理 `/internal/`。人工 Token 更新现在使用生产已有代理前缀下的 `POST /v1/settings/token`；旧 `/settings/token` 路由和 PUT 已删除。
