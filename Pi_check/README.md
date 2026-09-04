# PI-only 录制

PI 是唯一语义决策者；旧录制逻辑绝不启动。

本目录是独立录制系统。代码只负责启动浏览器、原样采集证据、把证据交给 PI、原样保存 PI 的最终提交。没有本地能力生成、补齐、编译、修复或回退路径。

## 启动

```bash
cd E:\python\try\Dano\Pi_check
npm install
npx playwright install chromium
```

配置 PI 凭证（任选已支持的环境变量）：

```
ANTHROPIC_API_KEY
OPENAI_API_KEY
PI_API_KEY
PI_PROVIDER
PI_MODEL
PI_FINAL_TIMEOUT_MS
PI_CHECK_PORT=18080
PI_CHECK_HEADED=1
```

现网不要单独占 8077。`uvicorn dano.gateway.app:app --port 8077` 启动时会自动拉起本目录（内部端口 18080），并把 `ws://127.0.0.1:8077/onboarding/page/record` 代理过来。前端 PageRecorder 不用改。

```bash
npm start
```

独立调试时默认端口 `18080`。打开 http://127.0.0.1:18080/ 可看状态页。

最终必须由 PI 提交非空 `capabilities`。没有能力就是失败。识别方法在 `skill/RECORDING_CAPABILITY.md`，职责划分在 `RESPONSIBILITIES.md`。

1. 填写目标页面和录制目标。
2. 开始录制：系统先启动 PI，成功后才打开浏览器。
3. 在业务页面操作。
4. 停止并交给 PI：证据冻结后，必须由 PI 调用 `submit_recording_result`。
5. 只有收到 PI 最终提交才算成功；否则页面显示“PI 未完成，本次录制失败，没有产出能力”。

## 测试

```bash
npm test
```
