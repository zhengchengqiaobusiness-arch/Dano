# 兼容债登记

本表只登记为了滚动升级或公开协议消费者而暂留的兼容代码。名称中含
`fallback` 但承担当前跨系统识别、浏览器恢复或发布校验职责的代码，不属于兼容债。

| ID | 原因 | 当前消费者 | 删除条件 | 目标版本 |
|---|---|---|---|---|
| `REC-P8-STEP-MERGE` | 前端实时步骤会去重，服务端独占最后一次防抖输入，两份步骤在 finalize 时可能不同 | 录制 WebSocket finalize | 所有步骤编辑和最后输入均经服务端 typed patch 持久化，且字段样例、必填、页面枚举黄金夹具完全等价 | recording protocol v4 |
| `REC-P6-STEP-IDS` | 滚动升级期间，前端可能收到只有派生 `step_ids`、没有 call nodes 的能力 | PageRecorder 只读展示 | 存量资产完成 nodes 迁移，活跃客户端 `protocol_version >= 3` | recording protocol v4 |
| `REC-P6-NODE-MIGRATION` | P6 前持久化能力可能只有 `step_ids` | FlowSpec 加载、编辑、发布、导出前归一化 | PG 盘点证明所有能力均有 call nodes，且滚动升级流量归零 | recording protocol v4 |
| `REC-INPUT-MOUSE-ALIASES` | 旧录制前端发送 `mouse_*`，当前协议发送 `pointer_*` | `RecordSession.dispatch_input` | 协议遥测确认 `mouse_*` 为零且最低前端版本升级 | recording protocol v4 |
| `API-CAPABILITY-INVOKE` | 已发布 SDK 仍可能通过 `/invoke` 的 body 指定 capability | 公共 Skill invoke 客户端 | v2 path capability 接口流量稳定且 v1 调用为零 | public API v2 |
| `API-TOOL-ARGUMENTS` | function-calling 客户端仍发送 JSON 字符串 `arguments` | `/v1/tools/call` | tool API v2 全部改用对象 `input`，v1 调用为零 | tool API v2 |
| `ONBOARD-LEGACY-NAME` | `_onboard_legacy` 是历史内部名称，但实现已是唯一 Pi 接入路径 | onboard 与保障期全量重跑 | 内部调用和测试完成无兼容别名改名 | onboarding API v2 |

以下明确不是兼容债，不得因名称或多形态处理直接删除：

- enum string/dict/tuple 归一化：跨系统枚举泛化合同。
- `flow_spec_to_api_request` 与 `execute_api`：发布校验和能力运行的共同底座。
- `captured_reads`、`captured_diagnostics`：请求角色、候选源和诊断证据。
- `_active_capability_step_ids` 的“能力尚未建立”状态：发布前工作台校验语义，不是用户运行旁路。
- 浏览器 popup、locator、页面切换 fallback：真实页面框架和多标签兼容能力。
- `computed_fields`：仅用于真正的能力级计算变量，不是请求字段副本。
