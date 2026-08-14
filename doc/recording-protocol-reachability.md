# 录制 V2 协议可达性清单

本清单对应当前录制主流程，用于证明消息、Pi 工具、能力编译、发布与导出路径的唯一性。
状态含义：`canonical` 为当前唯一正式路径；`compatibility` 仅保留旧客户端兼容语义；
`unreachable` 为没有发送方/调用方且已删除；`duplicate` 为曾与正式路径重复且已收敛的旧入口。

## 前端发送与后端处理的 WebSocket 消息

| message type | 前端发送入口 | 后端处理入口 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `start` | WebSocket 建连首帧 | `record_ws` 首帧校验 | canonical | 建立一条录制会话。 |
| `ping` | 20 秒心跳 | `_handle_live_recording_message` | canonical | 只返回 `pong`。 |
| `input` | 画面点击、滚动、键盘、文本输入 | `_handle_live_recording_message` → `_dispatch_recording_input` | canonical | 页面操作与捕获事实共用当前会话。 |
| `agent_answer` | 录制助手人工回答 | `_handle_live_recording_message` → `_resolve_agent_answer` | canonical | 恢复同一个暂停操作。 |
| `reset` | “从这里开始录” | `record_ws` 主循环 | canonical | 清除登录前捕获，保留录制目标。 |
| `finalize` | “停止并分析请求” | `record_ws` 主循环 | canonical | 固化捕获事实并启动统一验证/能力规划。 |
| `flow_update` | FlowSpec 工作台编辑 | `record_ws` 主循环 → `apply_flow_edits` | canonical | 人工编辑仍经过服务端权威 FlowSpec。 |
| `refresh_flow_spec` | 重连或编辑冲突后的同步 | `record_ws` 主循环 | canonical | 只返回当前权威投影。 |
| `orchestrate_flow` | 生成/优化能力、生成整体说明 | `record_ws` 主循环 → `submit_recording_plan` | canonical | 名称、说明、能力统一走同一 plan。 |
| `auto_fix_flow` | Pi 修复 | `record_ws` 主循环 → `_verify_finalized_recording` | canonical | 使用统一 repair/verification 循环，没有第二套修复提示词。 |
| `publish_request` | 手动重新验证并发布；自动规划完成后入队 | `record_ws` 唯一发布分支 | canonical | 自动和手动请求进入相同验证、审核、原子发布函数链。 |
| `console_log_upload` | 页面 console 批量上报 | `record_ws` 主循环 | canonical | 仅记录与当前页面相关的错误摘要。 |
| `terminate` | 一键终止 | `_handle_live_recording_message` → `_terminate_analysis` | canonical | 只取消当前分析、验证、审核及等待，不关闭连接、不清空草稿。 |
| `stop` | 当前前端不再发送 | `_handle_live_recording_message` → `_pause_recording_capture` | compatibility | 只暂停页面捕获并保留工作区；为旧客户端保留。 |
| `business_description` | 已删除，按钮改发 `orchestrate_flow` | 独立分支已删除 | duplicate / unreachable | 统一 plan 的 `business_understanding` 已覆盖。 |
| `step_naming` | 无前端发送方 | 独立分支已删除 | duplicate / unreachable | 统一 plan 和字段命名操作已覆盖。 |

`input`、`ping`、`stop`、`terminate`、`agent_answer` 只在
`_handle_live_recording_message` 实现一次；主循环先调用该处理器，命中后立即 `continue`。

## Pi 录制工具及实际入口

Pi 侧全部工具由 `back/agent/recording_tools.mjs` 的 `recordingTools` 暴露，经
`POST /_agent/tools/{name}` 到 `dano.agent_tools.app.call_tool`，再由
`dano.agent_tools.tools.TOOLS` 白名单分派。不存在浏览器直调数据库或 FlowSpec 的旁路。

| Pi 工具 | 后端实际实现 | 状态 |
| --- | --- | --- |
| `get_recording_state` | `get_recording_state` | canonical |
| `get_recording_delta` | `get_recording_delta` | canonical |
| `ask_operator` | `ask_recording_operator` | canonical |
| `replay_request` | `replay_recording_request` | canonical |
| `perturb_replay` | `perturb_recording_replay` | canonical |
| `verify_dependency` | `verify_recording_dependency` | canonical |
| `execute_write_with_verify` | `execute_recording_write_with_verify` | canonical |
| `browser_navigate` | `browser_recording_navigate` | canonical |
| `browser_snapshot` | `browser_recording_snapshot` | canonical |
| `browser_click` | `browser_recording_click` | canonical |
| `browser_fill` | `browser_recording_fill` | canonical |
| `browser_select` | `browser_recording_select` | canonical |
| `list_link_candidates` | `list_link_candidates` | canonical |
| `get_verification` | `get_recording_verification` | canonical |
| `submit_recording_plan` | `submit_recording_plan` | canonical |
| `get_validation_report` | `get_validation_report` | canonical |
| `submit_recording_repair` | `submit_recording_repair` | canonical |
| `submit_recording_review` | `submit_recording_review` | canonical |

## 能力、FlowSpec、发布与 Skill 导出入口

| 领域 | 触发入口 | 唯一实现 | 状态 |
| --- | --- | --- | --- |
| 初始录制事实 | `finalize` | `to_flow_spec` | canonical |
| 实时/最终语义规划 | 实时 Pi、`orchestrate_flow` | `submit_recording_plan` → `compile_capabilities` | canonical |
| 验证后的能力重编译 | `_verify_finalized_recording` | `compile_capabilities` | canonical |
| Pi 修复 | `auto_fix_flow`、发布反馈 | `_verify_finalized_recording` → `submit_recording_repair` | canonical |
| 人工 FlowSpec 编辑 | `flow_update` | `apply_flow_edits` | canonical |
| 自动发布 | 完整计划完成后入队 `publish_request` | `record_ws` 的唯一 `publish_request` 分支 → `run_request_onboarding` | canonical |
| 手动重新验证发布 | 前端 `publish_request` | 与自动发布相同的分支和 `run_request_onboarding` | canonical |
| 原子 release | 发布分支 | `run_request_onboarding` 维护的当前原子资产发布路径 | canonical |
| 自动 Skill 导出 | 发布成功后的 `_auto_export` | `dano.export.agent_skills.write_exports`，且只传当前 `skill_id` | canonical |
| 手动 Skill 导出 | `/export/agent-skills` | 同一个 `write_exports` | canonical |
| Skill 包渲染 | `write_exports` 内部 | 当前 `skill_package` 渲染器 | canonical（从属实现） |
| `live_skill` / `recording_skill` / `skill_forge_gate` | 无 | 模块已删除并由防复活测试锁定 | unreachable |

能力编译与 Skill 导出严格分离：exporter 只读取已经冻结的 release，不调用
`compile_capabilities`、`to_flow_spec`、`apply_flow_edits` 或 Pi 规划工具。

## 本轮删除证明

| 删除项 | 无发送/调用方 | 无恢复依赖 | 新路径覆盖 | 防复活验证 |
| --- | --- | --- | --- | --- |
| 主循环中重复的 `input/ping/stop/terminate/agent_answer` 分支 | 实际消息先由统一 live handler 消费 | 恢复状态不依赖分支位置 | `_handle_live_recording_message` | 协议清理测试检查唯一实现 |
| `step_naming` 消息及后端 Pi 分支 | 前端无发送方 | FlowSpec 恢复只读取权威草稿 | `orchestrate_flow` + plan ops | 前后端消息防复活测试 |
| `business_description` 消息及后端 Pi 分支 | 原按钮已迁移到 `orchestrate_flow` | `business_description` 字段仍保存在 FlowSpec | plan 的 `business_understanding` | 前后端消息防复活测试 |
| `descBusy` 独立状态 | 只服务于已删除消息 | 不写入恢复快照 | `orchestrateBusy` | 前端源码防复活测试 |

除上表及已经不存在的旧 exporter 外，本阶段没有凭名称删除其他状态、fixture 或兼容字段；
未能同时证明“零调用、零恢复依赖、新路径覆盖”的内容继续保留。
