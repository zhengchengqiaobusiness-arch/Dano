# 鉴权与 token

成品必须能离开录制环境直接打业务接口。token 过期必须停，不得猜、不得用录制样本顶替。

## 包内配置

`config/runtime.json`：`tenant`、`subsystem`、`base_url`。不准写密钥、cookie、password、JWT。

`config/auth.local.json` 形状是 `{ "headers": { ... }, "credential": { ... } }`。`headers` 一定有；`credential` 只有在证据里存在可重放的签发请求时才有 `url` 和 `method`。真实头由导出写入。运行命令时用 Skill 根目录这份文件；`DANO_AUTH_FILE` / `CABP_AUTH_FILE` 指向它。不要把证据里的 token 写进三文件。不要因为缺少 `credential` 对象而退出。业务请求把 `headers` 整袋带上；有任意鉴权头就发，不要只认某一个键名。

## client 读序

1. 若设置了环境变量 `DANO_AUTH_HEADERS`（JSON 对象），`json.loads` 后只覆盖同名本地头，不要按逗号拆，不要用环境变量整袋替换本地头
2. 其余头用包内 `config/auth.local.json` 的 `headers`
3. 两者都没有可用头则抛 `AuthExpired`，停止
4. 只有 `credential.url` 和 `credential.method` 时才重放签发请求；没有这两项就直接用 headers 打业务接口

`show-config` 只打印 tenant / subsystem / base_url / 是否已有鉴权头，不打印头的值。

HTTP 401，或响应声明账号未登录，同样抛 `AuthExpired`。脚本对单次请求失败或 401 立即停止，不要 sleep 循环重试。录制时读命令 401 仍要写完三文件并 `verify_skill`，这不是产出失败。这是打包凭证问题，不要当登录页去 assist，不要把凭证写进对话。基地址只读 `DANO_BASE_URL` 或 `config/runtime.json` 的 `base_url`，不要写死源站。

## 签发访问凭证的请求

只有 `config/auth.local.json` 的 `credential` 同时有 `url` 和 `method` 时才重放。listed 里出现刷新请求只是事实，不是脚本必须重放的条件。不要因为页面过程中签发过 token 就发明一套刷新。只有 headers 时直接打业务接口。重放用 `credential.method` 和 `credential.url`，请求头带上 `credential.headers`；没有该字段时带上 `headers` 里除 `Authorization` 以外的头。不要另猜头的值。响应里的访问凭证写入 `Authorization`。配套的刷新凭证按字段名更新 url 查询串里的同名值；`credential.refresh_token` 与查询串里被替换的值应一致。重放失败，或响应 HTTP 401 或正文 code 为 401，就停止。
