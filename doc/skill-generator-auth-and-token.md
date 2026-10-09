# 鉴权与 token

成品必须能离开录制环境直接打业务接口。token 过期必须停，不得猜、不得用录制样本顶替。

## 包内配置

`config/runtime.json`：`tenant`、`subsystem`、`base_url`。不准写密钥、cookie、password、JWT。

`config/auth.local.json` 形状是 `{ "headers": { ... }, "credential": { ... } }`。真实头由导出写入。运行命令时用 Skill 根目录这份文件；`DANO_AUTH_FILE` / `CABP_AUTH_FILE` 指向它。不要把证据里的 token 写进三文件。

## client 读序

1. 若设置了环境变量 `DANO_AUTH_HEADERS`（JSON 对象），用它覆盖同名本地头
2. 其余头用包内 `config/auth.local.json` 的 `headers`
3. 两者都没有则抛 `AuthExpired`，停止，要求提供 token

`show-config` 只打印 tenant / subsystem / base_url / 是否已有鉴权头，不打印头的值。

HTTP 401，或响应声明账号未登录，同样抛 `AuthExpired`。

## 签发访问凭证的请求

证据里 `issues_credential` 的请求，按 `auth.local.json` 的 `credential` 在业务请求前重放。重放用 `credential.method` 和 `credential.url`，请求头带上 `credential.headers`；没有该字段时带上 `headers` 里除 `Authorization` 以外的头。不要另猜头的值。响应里的访问凭证写入 `Authorization`。配套的刷新凭证按字段名更新 url 查询串里的同名值；`credential.refresh_token` 与查询串里被替换的值应一致。重放失败，或响应 HTTP 401 或正文 code 为 401，就停止。
