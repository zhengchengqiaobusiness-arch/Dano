# 录制代码单轨清理设计

> 状态：旧版设计（Legacy）。本文记录的是 2026-07-18 的 84 提交/142 文件快照，
> 不再作为实施或验收依据。实际完成状态、兼容债和验证结果见
> `2026-07-19-recording-single-path-cleanup-result.md`。

## 目标

从提交 `28b44d277ef5a0314e2d9ced848e8d2069dd9f2b` 起梳理录制代码的演进，删除当前新录制链路不再需要的旧资产兼容、双轨事实同步、无 capability 运行旁路和明确死代码。

清理后只保证当前版本新录制资产，不兼容旧资产或旧 FlowSpec。页面样式、DOM 结构、文案和现有录制交互保持不变。

## 历史脉络

目标范围共有 84 个提交，累计涉及 142 个文件，净差异为 58,306 行新增、4,190 行删除。录制代码经历了以下阶段：

1. **2026-06-23：网页录制初版**
   - 引入 `execution/page`、`RecordSession`、请求捕获、页面会话和 `PageRecorder.tsx`。
   - 录制、字段识别、直接请求执行同时进入代码库。
2. **2026-06-24 至 2026-06-29：捕获与字段推断扩张**
   - 连续增加请求分类、枚举、审批人、多选、身份和修复逻辑。
   - `request_capture.py`、`recorder.py`、Gateway 与前端之间形成多份相似状态。
3. **2026-07-02 至 2026-07-08：FlowSpec 与编排工作台**
   - 新增 `flow_spec.py`，逐步把发布入口收敛到 FlowSpec。
   - 同期保留早期字段勾选、完整工作流执行和后续 capability 投影，产生阶段性双轨。
4. **2026-07-09：第一次大规模剪枝与 P0-P4 能力分层**
   - `258261e` 删除旧 onboarding 路径，共删除约 7,100 行。
   - 随后同日加入 `request_facts`、能力校验、能力编辑、能力执行和前端工作台。
   - 为避免打断旧前端与旧发布链，`request_facts` 与 `meta.request_graph` 被明确设计为双向同步。
5. **2026-07-10 至 2026-07-13：能力模型快速扩张**
   - 增加 capability fields、schemas、nodes、relations、发布检查和执行语义。
   - 旧 steps/links 全量发布路径继续作为兼容底座。
6. **2026-07-14：第二次剪枝与 Pi 收敛**
   - `93e9b29` 再删除约 3,753 行。
   - `d242f55` 引入录制专用 Pi Session、受控工具和统一 capture 语义。
   - 之后持续修复重连、帧渲染、会话恢复和工具提交限制。
7. **2026-07-15：当前链路稳定化**
   - 加固重连、点击响应、最终帧、FlowSpec 与 capability 生成。
   - 仍遗留从未接入的函数、旧资产迁移器和无 capability 执行旁路。

## 当前问题

### 双事实源

同一批捕获请求同时存在于：

- `FlowSpec.request_facts`
- `FlowSpec.meta.request_graph`

后端在两者间双向同步，Gateway 同时恢复两份敏感字段，前端优先读取 `request_facts` 后再回退 `request_graph`。任何编辑、脱敏或派生字段更新都必须照顾两份数据。

### 双运行入口

当前新资产已强制包含 capability，但运行时仍保留：

- 指定 capability 后走 `_run_recording_capability()`；
- 未指定 capability 后走 `_run_recording()`，直接执行完整 `api_request`。

单 capability 资产因此可以通过两条不同运行管道得到结果。

### 阶段性兼容代码

FlowSpec 仍接受并清理旧字段，例如 `pinned`、`page_required`、`required_source`，并可从旧 `meta.request_graph` 重建 RequestFacts。用户已明确无需兼容旧资产，这些路径不再属于需求。

### 明确死代码

静态检查与生产可达性分析确认以下代码未被生产路径调用：

- `PageRecorder.reconnectRecorder()`
- `RecordingPiSession.latest_tool_result()`
- `request_capture.auto_required_fields()`
- `flow_spec.rename_steps_deterministically()`

`flow_spec_canonical_summary()` 虽然不被生产代码调用，但用于当前 recording V3 fixtures 的回归检查，因此保留。

## 唯一主链

清理后的唯一主链为：

```text
PageRecorder
  -> /onboarding/page/record WebSocket
  -> RecordSession
  -> RequestFacts
  -> FlowSpec capabilities
  -> capability runtime
  -> execute_api
```

规则：

- `request_facts` 是捕获请求的唯一持久化和客户端事实源。
- 新录制必须生成并确认至少一个 capability 后才能发布。
- 新录制资产的用户运行入口必须选择一个 capability。
- 只有一个 capability 且调用方未指定时，系统自动选择唯一 capability。
- 多 capability 未指定时继续返回 `NEEDS_SELECT`。
- 发布资产仍可包含完整步骤集合，用于 capability 编译、发布校验和受控 dry-run；它不是用户运行旁路。

## 逐文件设计

### `back/dano/execution/page/flow_spec.py`

删除旧资产迁移和双轨同步：

- `FlowSpec._migrate_request_facts_input()`
- `CapabilityRequestRef.discard_legacy_manual_lock()`
- `CapabilityField.discard_legacy_required_axes()`
- `_request_facts_from_graph()`
- `_request_graph_from_request_facts()`
- `_request_graph_has_entries()`
- `ensure_request_facts()` 的双向同步实现
- 持久化 `meta.request_graph` 的生成、回写、选中状态更新和客户端投影

把所有仍有业务价值的请求规划、提升、修复、校验和 capability 生成逻辑改为直接读取或更新：

- `RequestFacts.requests`
- `RequestFacts.analysis`
- `RequestFacts.usage`
- `RequestFacts.option_sources`
- `RequestFacts.page_events`

删除 `_active_capability_step_ids()` 中无 capability 时返回 `None` 并放行所有步骤的兼容语义。当前发布入口已强制要求 capability，新资产只允许执行 capability 引用的步骤。

删除 `compile_capability_to_api_request()` 在未提供 capability 时转入完整 FlowSpec 编译的分支。`flow_spec_to_api_request()` 本身保留，负责生成发布资产包与发布前检查材料。

删除 `rename_steps_deterministically()`；保留仍被说明生成使用的 `_derive_step_name()`。

### `skillfrontend/src/components/PageRecorder.tsx`

删除：

- `FlowSpecData.meta.request_graph` 类型
- `allCapturedRequests()` 对 `all_requests`、`selected_steps`、`candidate_reads` 的旧回退
- 未使用的 `reconnectRecorder()`

`allCapturedRequests()` 只读取 `request_facts`，继续合并 `analysis` 与 `usage` 派生信息。

不修改 JSX 结构、Ant Design 组件、CSS、文案、按钮位置或页面布局。自动重连继续使用 `scheduleRecorderReconnect()`。

### `back/dano/gateway/app.py`

删除 `_restore_hidden_flow_spec_fields()` 中对 `meta.request_graph` 四个 bucket 的敏感字段恢复。只保留 RequestFacts、FlowStep、select 和 identity 的当前字段恢复。

保留现有 WebSocket 消息类型、断线恢复、指纹冲突检查、Pi 规划/修复/审核和发布流程。

### `back/dano/agent_tools/tools.py`

删除 legacy request graph 派生字段的特殊比较说明与兼容过滤。RequestFacts 原始捕获字段继续 fail-closed 比较，`analysis` 与 `usage` 继续作为可重新计算的派生数据排除。

### `back/dano/orchestrator/orchestrator.py`

在 `invoke_skill()` 中：

- 零 capability：返回 `CAPABILITY_GAP`；
- 一个 capability：未指定时自动选中该 capability；
- 多个 capability：未指定时返回 `NEEDS_SELECT`；
- 选中后统一调用 `_run_recording_capability()`。

删除 `_run_recording()` 及其调用分支。Connector 与 Workflow 的 `_run_api()`、`_run_workflow()` 不在本次范围内。

### `back/dano/execution/page/request_capture.py`

删除 `auto_required_fields()`。

保留 `execute_api()`、capability 裁剪、节点执行、字段替换、选择项查询和事实核查。发布阶段仍可对完整资产做受控 dry-run 或验证；这不构成用户运行入口。

### `back/dano/onboarding/recording_pi.py`

删除从引入起未被调用的 `latest_tool_result()`。

### 测试文件

删除只验证以下旧行为的测试块：

- request graph 迁入 RequestFacts
- RequestFacts 回写 request graph
- 无 capability 时完整执行录制资产
- `auto_required_fields()`
- `rename_steps_deterministically()`

仍有当前业务价值但使用旧 fixture 形态的测试改为直接构造 RequestFacts，不删除业务断言。

## 错误处理

- 新 FlowSpec 缺少有效 RequestFacts 时校验失败，不从 `meta.request_graph` 恢复。
- 发布缺少 capability 时继续返回 `capability_missing`。
- 已发布录制资产缺少 capability 时返回 `CAPABILITY_GAP`，不执行完整步骤。
- capability 不存在、输入缺失、需要确认或输出 schema 不匹配时沿用现有错误状态与文案。
- WebSocket 重连、版本冲突、Pi 审核失败和发布失败行为保持不变。

## 测试与验证

实施使用测试先行：

1. 增加单 capability 自动选择测试，并确认旧代码未走 capability 唯一路径。
2. 增加零 capability 拒绝执行测试，并确认旧代码会落入 `_run_recording()`。
3. 增加只使用 RequestFacts 的客户端投影和编辑测试。
4. 删除旧兼容路径并让上述测试通过。

验证矩阵：

- `test_flow_spec.py`
- `test_flow_spec_edit.py`
- `test_request_capture.py`
- `test_gateway_record_ws.py`
- `test_capability_runtime.py`
- `test_recording_v2_scenarios.py`
- `test_recording_pi_client.py`
- `test_recording_pi_agent_tools.py`
- 当前 recording V3 fixture matrix
- 完整后端 pytest
- Node 录制 Pi self-test
- TypeScript `noUnusedLocals`/`noUnusedParameters` 检查
- 前端生产构建

## 验收标准

- 页面 DOM、组件层级、样式和文案无变化。
- 新录制、停止分析、能力生成、编辑、自动修复、发布与调用结果保持现有行为。
- 单 capability 隐式调用与显式调用进入同一 capability 运行管道。
- 多 capability 未指定时仍要求选择。
- 生产运行时不存在无 capability 的录制执行入口。
- 持久化和客户端协议中不再产生 `meta.request_graph`。
- 静态检查不再发现本设计列出的死代码。
- 最终交付完整 Git diff、删除行数统计和逐文件删除清单；清单包含删除内容、原因、替代主路径和验证测试。

## 非目标

- 不兼容旧录制资产、旧 FlowSpec 或旧 request graph。
- 不修改 Connector、Workflow 或录制范围外的通用 `/invoke` 协议。
- 不拆分大文件、不重命名公共 API、不引入新依赖。
- 不改变页面视觉设计或增加新功能。

## 风险控制

- RequestFacts 替换 request graph 涉及多个内部消费者，必须逐个迁移后再删除同步函数。
- 完整资产编译仍是发布所需，不能把发布编译器误删为运行旁路。
- `execute_api()` 仍被发布检查和 agent tools 使用，不能整体删除无 capability 输入支持；禁止的是已发布资产的用户运行时旁路。
- 任何无法通过现有测试证明等价的兼容块暂不删除，并记录在最终未清理清单中。
