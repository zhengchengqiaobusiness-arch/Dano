# Issue #66: 任意文件上传并按项目文件引用处理

## Summary

- 支持任意文件类型上传，不做 MIME/扩展名白名单。
- 文件选择或拖入后立即上传；上传未完成前禁止发送消息。
- 后端把文件写入当前 session workspace 的 `uploads/` 目录。
- UI 只在附件区域展示用户选择的原始文件名，不把后端路径插入输入框。
- 发送时保留 `files` 协议字段；后端把已上传文件解析成 Pi 可见的项目文件路径引用。

## Key Changes

- 前端附件选择接受任意文件，移除图片类型过滤和图片专用文案。
- 前端上传前用 Web Crypto 计算 SHA-256，先按 hash 查询；后端已存在则直接返回文件引用，避免重复上传。
- 上传接口不接收 `workspacePath`。后端通过 `clientId` 找当前 connection/adapter，再由 adapter 返回当前 session cwd：selected detached session cwd 优先，否则 live session cwd。
- 后端写入 `<workspace>/uploads/<sha256><原扩展名>`；流式上传时后端重新计算 SHA-256，和前端声明不一致则删除 part 文件并拒绝。
- `RpcUploadedFileRef` 增加 `relativePath`，例如 `uploads/a1b2.pdf`；`name` 保留用户选择的原始文件名供 UI 展示，`path` 保留绝对路径供后端 preview/校验使用。
- 上传成功后，前端只把附件加入附件显示区域；用户不看到 hash 文件名，也不感知新上传或 hash 秒传差异。
- 发送时提交 `message` 和 `files`。`BridgeRpcAdapter` 只 resolve registry、mark referenced，然后把 `relativePath` 注入给 Pi 作为项目文件路径引用。
- 停用旧的 uploaded files 转 base64 image 路径，避免新上传图片继续走 Dano 自定义图片附件协议。

## Test Plan

- 前端：`.pdf/.zip/.png/.txt` 都能触发上传；上传中或上传失败不能发送；上传成功后只显示原始文件名；同 hash 文件走秒传但 UI 无差异。
- 后端：任意 MIME/无 MIME 可上传；文件写入当前 session workspace 的 `uploads/`；hash 不匹配拒绝；重复 hash 返回已有文件引用。
- Adapter：prompt/steer/follow_up 不再把 `files` 转成图片；只把已解析文件的 `relativePath` 作为项目文件路径引用传给 Pi。
- 回归：clientId 校验、preview、上传大小限制仍有效。
- 验证：相关 Vitest 覆盖 `attachments`、`server`、`bridge-rpc-adapter`，再跑 `pnpm run check:web` 和 `pnpm test`。

## Assumptions

- “当前 session 的项目”以后端当前 session cwd 为准，不信任前端路径。
- UI 层只展示原始文件名；后端路径和 hash 文件名是协议/运行时细节，不暴露给用户。
- 不 import 私有/未导出的 pi-tui 或 pi-coding-agent internals；文件最终按 Pi 可读取的项目路径引用进入 prompt。
- 这是用户可见行为变更，bump root `package.json` patch version；不改 `yarn.lock`。
