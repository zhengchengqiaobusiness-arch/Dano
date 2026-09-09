# PI-only 录制交付说明

PI 是唯一语义决策者；旧录制逻辑绝不启动。

最终必须产出能力。没有非空 `capabilities` 就是失败。`PageRecorder` 预览始终可点：PI 先观察再操作，人也可以点。总入口与识别方法在 `skill/RECORDING_CAPABILITY.md`，页面操作在 `skill/CONTROL_IN_APP_BROWSER.md`，职责在 `RESPONSIBILITIES.md`。

## 变更清单

实现位于 `E:\python\try\Dano\Pi_check`，录制页提示在 `skillfrontend` 的 `PageRecorder`。没有导入、复制或调用原项目录制模块。

新增/重写：

- `src/policy.mjs`：唯一策略常量。明确 PI 是唯一语义权威，旧逻辑绝不启动。
- `src/fs-store.mjs`：事实、草稿、PI 最终结果、独立回执的落盘。结果原样保存，回执不写回 result。
- `src/evidence-store.mjs`：按发生顺序追加证据；冻结后禁止写入。
- `src/result-gate.mjs`：唯一最终提交入口 `submit_recording_result`。支持完整 result 或 `use_draft`。只检查会话、编号、final、非空对象、冻结、是否已提交。
- `src/result-merge.mjs`：单项能力写入草稿，同 `capability_id` 替换。
- `src/evidence-facts.mjs`：动作台账与请求形状，带 `actor`，不分类。
- `src/browser-snapshot.mjs`：给可见控件打 `data-pi-ref`。
- `src/pi-tools.mjs`：清单、台账、请求形状、可见控件、增量、指定证据、分段响应体、截图、冻结状态、单项能力、Control In App Browser、草稿、最终提交。
- `src/pi-session.mjs`：先启动 PI。浏览器就绪后 `beginLiveDrive`。人点预览时 `notifyHumanAct`。代码不补齐结果。
- `src/browser-capture.mjs`：动作队列串行。人 `applyInput` 与 PI `actByRef` 共用同一页。采证带 `actor`。
- `src/recording-controller.mjs`：唯一链路。人始终能点预览。协助是横幅。停录先不停浏览器。没有最终提交则不能成功。
- `src/frontend-bridge.mjs`：人的 input 永不丢弃。分析期继续推帧。PI 自行定稿走同一套推送。
- `src/server.mjs` + `src/public/*`：独立控制页。
- `tests/*`：自动化验收，含双通道、台账、增量提交。

禁止存在的路径：本地能力生成、请求角色推断、字段推断、依赖重建、默认值注入、自动修复、二次编译、旧录制回退、锁死预览的 takeover。

## 启动方法

```bash
cd E:\python\try\Dano\Pi_check
npm install
npm start
```

打开 http://127.0.0.1:18080/

真实业务录制需要 PI 凭证，例如 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `PI_API_KEY` + `PI_PROVIDER` + `PI_MODEL`。没有凭证时 PI 无法启动，浏览器不会打开，系统失败且不产出能力。

## 测试结果

命令：`npm test`

覆盖：

1. PI 启动失败时浏览器不启动
2. PI 中途失败时无最终结果
3. 未调用最终提交则停止失败
4. 空结果失败
5. 错误录制编号失败
6. 闸门：未冻结提交失败；PI 不能自行冻结。用户结束才冻结。调用方键必须出现在 execute 现场请求里
7. 成功提交与 PI 原文一致
8. 代码不增加能力/字段/依赖/默认值
9. 代码不修改 PI 提交中的任何值
10. 拒绝第二个最终结果
11. 依赖扫描：无目录外录制模块
12. 源码扫描：无本地生成/推断/补齐/编译/修复/回退入口
13. 真实浏览器录制通过
14. 真实 `submit_recording_result` 提交通过，原样落盘
15. 人为关闭 PI 后只能失败，能力数量为 0
16. 人点预览不被丢弃，与 PI 点进同一证据
17. 动作台账带 actor；增量提交 `use_draft` 定稿

## 端到端证据

- 控制页与健康检查：`GET /api/health` 返回  
  `{"ok":true,"notice":"PI 是唯一语义决策者；旧录制逻辑绝不启动。"}`
- 页面横幅与文案只允许 PI 决策，不存在“代码处理中 / 自动补齐中 / 本地修复中”。
- 预览始终可点；协助是顶栏，不是锁画布。
- 浏览器 e2e：fixture 页填写并提交表单后，证据数大于 0，且包含 `network_request` 与 `screenshot`/`interaction`。
- PI 关闭 e2e：录制中杀死 PI 后 `status=failed`，`pi-result.json` 不存在，能力数量为 0。
