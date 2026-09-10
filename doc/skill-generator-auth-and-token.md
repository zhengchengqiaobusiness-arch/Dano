# 鉴权与 token：OA Skill 生成指南

本文档供负责写出消费者 Skill 的模型阅读。参数名、文件名、环境变量和错误 code 保持英文原名，其余说明使用中文。

成品必须能离开 Dano 直接打业务接口和选项接口。token 过期必须停问，不得猜、不得用录制样本顶替。

## 包内必须有的两份配置

`config/runtime.json`：

```json
{
  "tenant": "",
  "subsystem": "oa",
  "base_url": ""
}
```

- `base_url` 从合同 execute step 的绝对 origin 取。取不到就留空，并在 `SKILL.md`「鉴权」节写明：没有 base_url 时停止，要求调用方提供业务根地址。
- 这里不准写密钥、cookie、password、JWT。

`config/auth.local.json`：

```json
{
  "headers": {
    "Authorization": "Bearer ...",
    "Tenant-Id": "..."
  }
}
```

- 形状必须是 `{ "headers": { ... } }`。
- Skill 4 只写空槽位 `{"headers":{}}`。真实头由导出运输写入**完整 token**（登录响应 / vault / 页面保存），禁止把证据里的 `[sealed:…]` 占位符写进仓库或包。
- 选项接口和业务接口走同一套头。不要为 `--list-options` 另做一套鉴权。

## client 读序

冻结的 `scripts/client.py` 按这个顺序取头，先到先用：

1. 包内 `config/auth.local.json` 的 `headers`
2. 环境变量 `DANO_AUTH_HEADERS`（JSON 对象）
3. 仍没有则抛 `AuthExpired`，停止，要求提供 token

`--show-config` 只打印 tenant / subsystem / base_url / 是否已有鉴权头，**不打印头的值**。

HTTP 401，或响应声明账号未登录，同样抛 `AuthExpired`。调用方必须停问要 token，不得重试猜值，不得把录制时的 Authorization 写进手册或脚本常量。

## 手册和对话

`SKILL.md` 必须有「鉴权」节，只写：

- 没有 `auth.local.json` 且没有环境凭证则停止
- 401 / 账号未登录同样停问
- 页面改 token 后会回写已导出包，不必重录

禁止把真实 token、cookie、password 写进：

- `SKILL.md`
- `references/CONTRACT.json`
- `references/CAPABILITIES.md`
- `references/INPUT_FORMS.md`
- `references/OPTIONS.md`
- 路线文档
- 对话或日志

## 页面回写

Dano 页面 Token 保存后，必须回写该 `subsystem` 已导出包的 `config/auth.local.json`。重新导出不是换 token 的前提。
