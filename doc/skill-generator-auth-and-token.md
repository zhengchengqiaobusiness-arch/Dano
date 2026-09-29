# 鉴权

程序写入 config/runtime.json 和 config/auth.local.json。base_url 来自本场请求的 origin。取不到就留空，并在 SKILL.md 的鉴权节写明要调用方提供业务根地址。

三个 Skill 文件不写密钥。

客户端先读 config/auth.local.json 的 headers，再用 DANO_AUTH_HEADERS 覆盖同名头。两者都没有，或 HTTP 401，或响应声明未登录，就停止。不用录制样本顶替。

标了 issues_credential 的请求按证据里的 method 和 url 重放，不另拼 path。可执行代码里这样写，不要只写在注释里：

credential = auth["credential"]
Request(credential["url"], method=credential["method"])

查询串里的凭证留在 url 里。响应带回新的刷新凭证时，只替换这段 url 里原来的查询值，再写回原文件。
