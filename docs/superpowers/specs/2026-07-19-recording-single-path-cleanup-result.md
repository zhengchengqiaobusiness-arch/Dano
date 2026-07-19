# 录制单轨清理实施结果（P0–P10）

## 状态与真实基线

本文是当前实施和验收依据，替代 2026-07-18 的旧设计快照。

- 审计基准：`28b44d277ef5a0314e2d9ced848e8d2069dd9f2b`
- P10 开始时 HEAD：`b8cd459`
- 基准之后：107 个提交、172 个变更文件
- 基准至 P10 开始：63,198 行新增、7,473 行删除
- 原审计快照 `78d9290` 至 P10 开始：43 个文件、4,110 行新增、2,001 行删除
- 上述阶段差异中：测试/夹具净增 1,769 行，迁移净增 229 行；后端生产净增
  279 行，前端生产净减 168 行，生产代码合计净增 111 行。

因此“代码没有明显减少”并不表示旧流程未删除。P1/P2/P3/P4 删除了死代码、整资产
运行旁路、request graph 双写和旧字段协议；新增量主要来自协议快照、跨系统黄金夹具、
P5 typed patch、P7 故障注入和 P9 持久化补偿。清理目标是单一权威路径和可证明行为，
不是以总行数作为验收指标。

## 当前唯一主链

```text
PageRecorder
  -> record WebSocket
  -> RecordSession（all_requests + page evidence）
  -> RequestFacts（唯一请求事实源）
  -> FlowSpec（服务端权威、客户端脱敏投影 + typed patch）
  -> 同一 Recording Pi Session（plan / repair / review）
  -> page_onboard 发布资产
  -> lifecycle outbox / reconcile（派生索引补偿）
  -> invoke_skill
  -> capability-only runtime
  -> execute_api
```

运行合同：零 capability 返回 `CAPABILITY_GAP`；单 capability 未指定时自动选择；多
capability 未指定时返回 `NEEDS_SELECT`；录制资产不存在整份 FlowSpec 的用户运行旁路。

## P0–P10 逐项结果

| 阶段 | 结果 | 提交/处置 |
|---|---|---|
| P0 | 修复 TypeScript 基线、补齐浏览器测试依赖、Ruff 清零、固化协议快照 | `e5bc154` |
| P1 | 删除确认无调用的旧 helper、预算入口和专属测试 | `f4eced9` |
| P2 | 零/单/多 capability 分发统一到 capability runtime，删除整资产运行入口 | `72c7fe5` |
| P3 | RequestFacts 成为唯一请求事实源，迁移并删除 request graph 双写 | `41342f9` |
| P4 | 删除 `request_fields`、字段勾选状态和 publish ghost keys | `edb6621` |
| P5 | 服务端权威 FlowSpec、脱敏客户端投影、typed patch、指纹发布 | `3978d34` |
| P6.1 | nodes 成为能力执行计划，`step_ids/request_refs` 改为派生视图 | `73ed7f2` |
| P6.2 | 字段事实、公开输入、输出合同单向派生 | `668cf55` |
| P6.3 | option_sources → step/param → capability 的单向枚举投影 | `a356685` |
| P6.4 | `all_requests` 唯一原始请求 ledger，统一 `risk_level` | `45feef7` |
| P7 | plan/repair 工具接受时立即 checkpoint；删除 finally salvage | `9d177d4` |
| P8 | 不满足安全删除条件；保留并重命名为录制步骤编辑合并器，增加等价测试 | `378e33f` |
| P9 | 生命周期登记 outbox、幂等补偿、启动恢复和前端 pending 提示 | `b8cd459` |
| P10 | 旧文档退役、兼容债标记、当前基线和验证矩阵固化 | 本提交 |

`3427f60` 是阶段执行期间进入主分支的跨系统语义/场景增强提交，不作为 P 阶段提交，
但已纳入后续全量验证。

## P8 保留结论

P8 没有强行删除 finalize 步骤层，原因是删除前置条件尚未同时成立：

1. 前端接收实时 step 时会做连续重复抑制，服务端 `RecordSession.steps` 不保证同形。
2. 最后一个防抖 fill 只在 finalize 的 server flush 后出现，前端可能尚未收到。
3. 样例、必填和页面枚举仍要以用户看到的步骤顺序/删除结果与最后 flush 合并。

当前唯一保留函数是 `_merge_recording_step_edits`，职责、消费者、删除条件和目标版本已
以 `COMPAT[REC-P8-STEP-MERGE]` 标记，并由字段/枚举等价测试锁定。

## 兼容债与非债务功能

所有暂留兼容块都使用 `COMPAT[ID]` 标记，完整登记见 `docs/compat-debt.md`。当前共 7 项，
每项均包含原因、当前消费者、删除条件和目标版本。

以下多形态逻辑属于当前泛化能力，不列入待删：枚举 string/dict/tuple 归一化、DOM popup
fallback、跨标签活动页恢复、RequestFacts 页面/接口候选证据、发布编译器和 execute_api。

## 最终验证矩阵

自动化验收记录（2026-07-19，P9 后及 P10 最终复验）：

| 检查 | 结果 |
|---|---|
| 后端全量 pytest | 通过，1,009 passed；包含两个 aiohttp + Playwright 真实浏览器测试 |
| Recording Pi self-test | 通过，`status=ok`，persistent session/runtime protocol 均为 true |
| TypeScript `npx tsc --noEmit` | 通过 |
| 前端生产构建 | 通过；仅保留 Vite 大 chunk 非阻断警告 |
| Ruff 生产代码 | 通过，All checks passed |
| `git diff --check` | 通过 |
| 单能力隐式/显式一致、零能力、多能力选择 | 自动化通过 |
| 两类结构不同业务合同 | cross-system acceptance 9 passed；覆盖 `work_hours`、`daily_report/leave/seal` 黄金夹具 |
| P7 断线/代际/连续提交/指纹冲突 | 自动化通过 |
| P9 补偿成功/重复登记/进程重启 | 自动化通过 |

外部人工验收未伪造：仓库环境没有提供两个真实业务系统的登录地址和账号，因此无法声明
“两套外部系统人工点击全链已完成”。上线前仍需在授权环境执行录制、点击/滚动/下拉、
finalize、枚举/关联、能力生成、编辑/修复、断线、发布和真实 API 调用。自动化黄金夹具
证明合同与泛化回归，不能代替生产凭据下的人工验收。

## 回滚边界

- P7、P8、P9 各自独立提交，可按阶段回滚。
- P5 的敏感字段脱敏和服务端权威合同不得因调试回退。
- 出现字段、枚举、关联或重连回归时，只回滚对应阶段，不恢复 request graph、旧字段协议
  或整资产运行旁路。
