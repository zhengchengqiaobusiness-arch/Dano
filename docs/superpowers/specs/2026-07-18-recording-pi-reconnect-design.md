# 录制能力生成与 Pi Session 重连稳定性修复设计

## 背景与目标

用户完成“停止并分析请求”后，点击“生成/优化能力”应稳定返回能力内容。WebSocket 临时断线、浏览器自动重连或旧连接清理延迟都必须由系统内部处理，不能向用户暴露“同一录制 Pi Session 已在另一个连接中使用”“录制 Pi Session 尚未启动”等流程性错误，也不能丢失已经生成的能力结果。

## 已确认根因

当前网关把 Pi Session 实例绑定在单个 WebSocket handler 上，但录制草稿和 `recording_id` 会跨 WebSocket 重连复用。重连后，新 handler 可能在旧 handler 完成 `finally` 清理前启动同一 scope 的 Pi Session，因此命中独占保护。

此外，`_ensure_recording_pi` 在 `await recording_pi.start()` 成功前就把实例写入局部缓存。启动因 scope 冲突失败时，`RecordingPiSession.start()` 会关闭该实例；后续点击仍复用这个已关闭、没有进程的对象，于是错误从“另一个连接中使用”演变成“尚未启动”。

## 选定方案

采用录制连接所有权接管方案，并保留现有“一条 WebSocket 对应一个 Pi sidecar”的边界。

每个 `(tenant, subsystem, recording_id)` 只有一个当前连接所有者。新连接取得更高 generation 时，必须让上一代 handler 退出并等待其 Pi Session 清理完成，再允许新一代创建同 scope Session。连接所有权只协调生命周期，不改变 FlowSpec、浏览器状态和 operation 回执的现有恢复数据结构。

同时将 Pi Session 的局部缓存改为“启动成功后提交”：先创建候选实例并启动，成功后再赋给 `recording_pi`；失败时确保候选实例关闭并保持缓存为空。后续操作因此可以重新创建健康实例，不会复用失败对象。

## 组件与数据流

### 网关连接所有权

- 在录制恢复状态中保存当前连接 owner 的 generation、关闭请求和释放完成信号。
- 新 handler 收到合法 start 帧并确认 resume key 后，原子替换 owner。
- 如果存在上一代 owner，新 handler 请求其结束，并等待释放完成；等待有明确上限，超时作为服务端流程错误记录日志，而不是把底层 Pi scope 错误直接返回给能力按钮。
- 旧 handler 的 `finally` 仍是唯一资源清理出口：关闭 Pi Session、RecordSession、发送队列并标记 owner 已释放。
- generation 检查继续防止旧 handler 覆盖新一代草稿或登录态。

### Pi Session 创建

- `_ensure_recording_pi` 使用单次启动锁，避免同一 handler 内多个昂贵操作竞态创建。
- 候选 `RecordingPiSession` 只有在 `start()` 成功后才成为可复用实例。
- 启动失败时关闭候选并清空缓存；下一次调用重新创建。
- 对旧连接交接期间的 scope 冲突执行有限、条件化等待/重试；不对模型、协议或普通运行时错误盲目重试。

### 结果恢复与幂等

- `operation_id` 继续作为生成/优化操作幂等键。
- Pi 提交计划后，先将权威 FlowSpec 和成功回执写入录制恢复状态，再向 WebSocket 发送响应。
- 若响应发送前发生 1006，新连接从服务端恢复最新 FlowSpec；相同 `operation_id` 重放已有回执，不重复消耗模型调用。
- 前端保留现有错误清理和 busy 状态逻辑，不新增对底层 Session 错误的特殊兜底；稳定性由后端生命周期保证。

## 错误处理

- 连接接管、旧 owner 清理和候选 Session 启动分别记录结构化日志，包含 recording scope 的非敏感标识、generation 和阶段。
- 仅 scope 所有权冲突可在确认旧 owner 正在释放时重试。
- 模型不可用、工具协议错误、提交限制和超时继续按现有语义返回，不伪装成重连成功。
- 若旧 owner 无法在上限内释放，主动完成其资源清理并返回稳定、可重试的上层错误；不得缓存半启动 Session。

## 测试设计

后端回归测试必须先失败，再实施修复，并覆盖：

1. Pi Session 首次 start 失败后，第二次 ensure 创建新实例并成功启动。
2. 同一 recording scope 的新连接接管旧连接，等待旧 Pi Session 释放后正常生成能力。
3. 旧连接 1006 发生在 Pi 已提交计划、响应尚未送达时，新连接恢复最新 FlowSpec 和幂等回执。
4. 重复 `operation_id` 不重复调用 Pi。
5. 普通 Pi 运行时错误不被所有权重试逻辑吞掉。
6. 现有并发 scope 防护仍拒绝真正独立的重复使用。

验证范围包括相关 pytest、完整 `test_recording_pi_client.py`、`test_gateway_record_ws.py`，以及前端生产构建。

## 非目标

- 不把 Pi Session 改造成跨进程常驻服务。
- 不改变能力生成提示词、FlowSpec 语义或发布审核规则。
- 不增加无上限自动重试。
- 不做与本故障无关的前端重构。
