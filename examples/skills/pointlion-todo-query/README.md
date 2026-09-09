# PointLion 待办查询：Dano 适配版

来源：用户提供的 `pointlion-todo-query-token-inline.zip`，原版 3.0.0。
原包保存在 `apps/dano/src/bridge/__tests__/fixtures/pointlion-todo-query-token-inline.zip`（仓库根目录相对路径）。
SHA-256：`2685a04b163adfe490740390dd351a02d297786e898ff7abb5181e2b58abf02a`。

适配保留原有 doctor、动态枚举、中文参数、待办分页与字段解析；请求入口改为 Dano 注入的 `dano_provider.request`，移除手填 token 及 JWT tenant 推断。doctor 增加 HTTP/业务码证据。OA origin 始终由服务端决定。

把本目录放入 Dano 的 Skill 目录或当前 Runtime Workspace，通过 OA 登录后由 bash 运行：

```sh
python3 scripts/pointlion_todo.py doctor
python3 scripts/pointlion_todo.py query --page 1 --page-size 1
```

普通宿主机 Python 不能单独获得 Dano 登录能力。无需也不应复制用户 token。

功能与鉴权集成测试位于仓库的 provider-python Vitest 用例；原包的 5 项独立测试只验证原版行为，不能代替适配版或真实 OA 验收。
