# 鉴权与 token：生成期规范

本文档只约束 Skill 4 如何把运行期鉴权写进消费者包。生成期必须阅读；成品 **不得** 携带本文档，也不得要求执行前再读它。

参数名、文件名、环境变量、HTTP 头保持英文原名。

## 包内文件

每个导出的 Skill 必须有：

```text
config/runtime.json
config/auth.local.json
```

`config/runtime.json`（可提交，无密钥）：

```json
{
  "tenant": "<tenant>",
  "subsystem": "<subsystem>",
  "base_url": "<execute step 的绝对 origin，取不到则为空字符串>"
}
```

`base_url` 从合同 `steps[]` 里 `usage=execute` 且带绝对 URL 的 origin 取值。取不到就留空，并在 `SKILL.md` 的鉴权节说明必须由部署方补 `DANO_BUSINESS_BASE_URL` 或改这份文件。

`config/auth.local.json`（本地鉴权，页面可改）：

```json
{
  "headers": {
    "Authorization": "Bearer ...",
    "Tenant-Id": "..."
  }
}
```

没有已存 token 时写成 `{"headers":{}}`。**禁止发明 token。**

## client 读序

冻结的 `scripts/client.py` 按这个顺序取鉴权头，选项接口和业务接口走同一套：

1. 包内 `config/auth.local.json` 的 `headers`
2. 环境变量 `DANO_AUTH_HEADERS`（JSON 对象）
3. 仍连着 Dano 时：`DANO_URL` + `/v1/settings/token/raw`
4. 本机会话缓存

都没有则停止，不要猜头、不要用录制样例里的 Authorization。

`python scripts/client.py --show-config` 只打印 `tenant`、`subsystem`、`base_url` 是否已配置、是否已有鉴权头。**不打印头的值。**

## 页面回写

Skills 页 TokenModal 保存成功后，必须按当前导出目录更新该 `(tenant, subsystem)` 已导出包的 `config/auth.local.json`。只改鉴权文件，不动 handbook。没有已导出包时只写库，不要报失败。

## 禁止

- 把 token、cookie、password、Bearer 明文写进 `SKILL.md`、`CONTRACT.json`、`CAPABILITIES.md`、`OPTIONS.md`、`INPUT_FORMS.md`、路线文件、对话或日志
- 把真实凭证写进 Skill 4 的工具参数或投影输入
- 选项接口走另一套匿名鉴权
