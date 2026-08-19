# 阶段 7（机器验证）重构任务书

> 交付对象：Codex（对本仓库无先验上下文）。本文档按 **2026-08-19 工作区现码** 写成，自包含，读完即可动手。
> 仓库根：`E:/python/try/Dano`。后端 `back/`，前端 `skillfrontend/`，Node 侧车 `back/agent/`。
>
> 上一版任务书有多处入口/语义写错。本版已按代码核对纠正。不要再去找 `flow_spec_edit.py`（不存在），不要从 `recording_live.py` 抽回放（回放在 `replay.py`）。

---

## 0. 交付边界（先读）

1. **阶段七只做验证、分诊、确定性结构修补、回放取证。** 不得重编阶段 1–5 的采集/冻结。能力 **name/title/kind/边界** 仍归 Skill（阶段六语义计划）；Python 可以 **重绑 option_source 的 step_id**、把未归属步骤标成内部用途，**不得在阶段七发明新的公开能力**。
2. 不得清空或重建 `FlowSpec.capabilities`。`repair` 里的 `kept_capabilities` 防线必须保留。
3. 凭证只存在于进程内（`MaterialContext` / `token_store` / 捕获请求头 / `storage_state`），**永远不能进入 LLM 上下文或日志**。
4. `back/tests/` 当前磁盘上只有两个文件（均未入库）：`test_recording_field_contracts.py`、`test_recording_stage_seven_rounds.py`。不要恢复历史测试；新行为写新测试。
5. 工作区已有未提交改动，**不得回退**，在其上继续：
   - 已实现「按能力分组修复」第一版：`recording_runtime.py`、`recording_workflow.py`、`tests/test_recording_stage_seven_rounds.py`
   - 他人改动：`flow_spec.py`、`recording_live.py`、`request_capture.py`、`recording_field_evidence.py`、`SKILL.md`、`PageRecorder.tsx`、`doc/recording-pipeline.md`
   - `back/_tmp_*` 是临时调试文件，不要动
6. 生产不变量：`sandbox_test` / `write_readback` / `health_check` 永远 `environment=sandbox`；`publish_asset` 只接受后端签发的证据。
7. **`compile_capabilities` 会在阶段七重跑**（`recording_verify.py::_consume_dependency_executor_evidence`）。任何只改 `capability.nodes[]` 而不改编译器的补丁，下一轮 check 会被冲掉。节点绑定必须改 `capability_compiler.py`。

**运行（在 `back/`）：**

```bash
python -m pytest
python -m pytest tests/test_recording_stage_seven_rounds.py -v
ruff check .
```

---

## 1. 现码：阶段 7 实际怎么跑

### 1.1 调用链

```
WS /onboarding/page/record
  recording_gateway.py
    RecordingWorkflow（recording_workflow.py）
      SelfHealingPipeline._run
        prepare → persist_stage_six → 循环[check → 问人? → repair] → publish
          CanonicalRecordingRuntime（recording_pipeline.py，纯壳）
            ProductionRecordingServices（recording_runtime.py）
              verify  → apply_recorded_evidence_fixes
                       → finalize_verification_state（会 compile_capabilities）
                       → verification_report
              repair  → auto_fix_flow_spec（含 _deterministic_capability_repair_edits）
                       → 按能力分组交给 Pi
              publish → evaluate_recording_release + prepare_flow_release_candidate
```

Pi 在 Node 侧车 `back/agent/run_recording_pi.mjs`，Skill 在
`back/agent/recording-pi/skills/analyze-recording-evidence/SKILL.md`。
Python 工具分发在 `back/dano/agent_tools/tools.py`。

### 1.2 工具真正落点（不要找错文件）

| 工具 | 实现 |
|---|---|
| `replay_request` / `perturb_replay` / `verify_dependency` / `execute_write_with_verify` | `back/dano/execution/page/replay.py`；凭证由 `tools.py::_recording_auth_headers` + `_recording_storage_state` 注入 |
| `submit_recording_repair` 的各 op | `recording_live.py::apply_recording_agent_edit`（`LIVE_RECORDING_AGENT_OPS`）→ `flow_spec.py::apply_recording_agent_submission` |
| `get_validation_report` / `get_recording_state` | `recording_pi.py` / `recording_runtime` 周边 |
| `browser_*` | 仅当场录制会话有浏览器。「开始分析」`resume_verification` **没有浏览器**，禁止让 Skill 用浏览器补采 |

`LIVE_RECORDING_AGENT_OPS` 现有：`set_goal`、`set_request_role`、`set_param_source`、`set_param_type`、`set_param_required`、`set_param_enum`、`rename_field`、`propose_dependency`、`add_pitfall`、`confirm_dependency`、`bind_verify_read`、`attach_enum_options`、`mark_unverified`。

**没有**这些 op（但 `recording_release.py` 会把它们写进 `suggested_operations`，属幽灵建议，Skill 调用会被拒）：`reconcile_capability_membership`、`reconcile_dynamic_structure`。不要再给 Skill 造这两个 op；对应问题用 Python 确定性修补。

### 1.3 待办怎么来

`recording_verify.py::verification_todos` 产出：`dependency`、`dependency_candidate`、`write_verify`、`enum`。  
`_release_issue_todos` 再并入 `evaluate_recording_release` 的 `release_issue`。

`_todo_issue`：todo 未带 `resolver` 时默认 `collect_evidence`。合法值：`machine_repair | collect_evidence | operator | external_blocked`。

`write_verify.candidate_read_request_ids` **现状有缺陷**：几乎所有 GET/HEAD/POST，截断 25 条，含 tenant/字典/IM。Skill 会乱选回读目标。本任务要收窄（见 P0-B）。

### 1.4 退出条件（`SelfHealingPipeline._run`）

- 无 issue → publish
- **任意一条** `resolver=external_blocked` → 立刻 `EDITABLE`，且 `PipelineOutcome.issues` **只带回这些 external 条目**，其它 issue 被丢掉。改预检时必须意识到这一点。
- `_draft_fingerprint(整份 draft JSON)` + issue 签名连续 2 轮无进展且本轮 `report.applied` 为空 → `EDITABLE`（文案「自动处理连续没有产生有效变化」）
- 单操作 1800s / 总 10800s → `FAILED`

注意两套指纹：

| 用途 | 函数 | `fact_check` |
|---|---|---|
| 流水线停滞 | `recording_workflow.py::_draft_fingerprint`（整份 JSON） | **计入** |
| 组内是否算执行变化 | `flow_spec_fingerprint` → `_execution_fingerprint_payload`（约 L16488） | **不计** |

现有逐能力循环已按 `verification_report` 的 open todos 复查，不要退回「只看执行指纹」。

### 1.5 工作区已落地（保留，不要重写）

`recording_runtime.py`：`_open_issue_tokens`、`_issue_capability_id`、`_group_issues_by_capability`、`_capability_brief`、`_capability_repair_prompt`、`ProductionRecordingServices._repair_capability_groups`。

行为：按阶段六能力顺序分组（`capability_id`，否则 `step_id` 反查 `step_ids`/`nodes`）；无归属进最后「整体流程」组；每组注入阶段六契约；提交后立刻重算 todos；`fact_check` 进展保留；单组异常不阻塞后续组。

已知缺口（本任务补）：无每能力预算、无 `meta.capability_verification`、无归属的写步骤（例如日志里 DELETE `4afcfadafd53` 不在 `delete_sale_order.step_ids`）会掉进流程组。

`recording_workflow.py`：`PipelineContext.current_round`；计划文案已是「按能力分组」。

测试：`tests/test_recording_stage_seven_rounds.py`（6 个用例）。

---

## 2. 故障证据（一次真实 ERP 销售订单录制）

四个阶段六能力：`edit_sale_order` / `approve_sale_order` / `reject_sale_order` / `delete_sale_order`。

1. **凭证过期无预检**：`verify_dependency(64f0a725)` 回放 `GET /admin-api/erp/sale-order/get?id=36` → 401「账号未登录」。随后依赖、5 个写回读、枚举全部注定失败，循环仍逐项派发。
2. **`mark_unverified` 被质量门打回**：一次提交里 6 个 `set_param_source` `rejected`（空值不能 `constant`），8 个 `mark_unverified` `rolled_back` + `new_validation_errors`。**这不是整批事务。** repair 模式已经逐 op apply；`_semantic_candidate_gate` 把 `mark_unverified` 当成必须单调改善校验的语义候选，降级声明被当成「引入新错误」。`flow_version` 7→9 是因为 `apply_recording_agent_submission` **无论成败都** `append_flow_version`。
3. **术语不一致**：拒词 `use user_input`；Skill schema 是 `caller_input`。编译后 FlowSpec 内部 `source_kind` 存的是 `user_input`（`_compile_param_source`）。三套名字。
4. **幽灵 machine_repair**：`call 节点未绑定有效接口步骤`（req_46 dict-data、req_95 customer/simple-list、req_96 user/simple-list 无 step_id）。同 path 已有物化步骤 `37f297a79434`、`6f387e4b9b9d` 等。`suggested_operations=["submit_recording_repair"]`，但没有任何 op 能重绑 node。根因在 `capability_compiler.py`：option_source 按 request_id 入列，对不上 step 时 `step_id=""`；`flow_spec.py` 约 L16026 对所有 call 节点强制有效 step_id。
5. **未归属步骤**：`8ecae74210af` page、`9508f562c4a4` get、`07b0838842e4` product/simple-list。建议 op 是不存在的 `reconcile_capability_membership`。
6. **记录选择器误报**：edit/approve/reject 的 `query.id`、delete 的 `query.ids` 被 `_INTERNAL_EXPOSED_PATH_RE` 判内部 ID。UI 已把它们当「调用方选择记录」。
7. **空值 unknown**：PUT update 的 `customerName`/`fileUrl`/`creatorName`/`items[0].taxPercent`/`items[0].remark`/`productNames` 录制为空/null。`apply_recorded_evidence_fixes` 不处理空值。Skill 标 `constant` 被拒；正确内部值是 `user_input`。
8. **日志混乱**：一轮混 4 个能力 + 流程级问题（分组第一版就是为这个，尚未补预算和报告）。

---

## 3. 根因

| # | 缺陷 | 证据 |
|---|---|---|
| D1 | 无回放健康预检 | 1 |
| D2 | 代码能修 / 无 op 可修的问题全部丢给 Skill；编译器绑定漏洞 | 4–7 |
| D3 | `_semantic_candidate_gate` 拦截 `mark_unverified`；拒绝原因不可机读；术语三套 | 2、3 |
| D4 | 修复曾与阶段六脱节（分组第一版已部分修） | 8 |
| D5 | 写回读候选读请求过宽 | 1 的连带混乱 |

---

## 4. 目标执行模型

```
prepare（阶段六已存档；阶段七只改内存工作副本）
  verify:
    apply_recorded_evidence_fixes（含空值 → user_input）
    compile_capabilities（option_source 按 path 绑 step / 无 step 则不生成缺 step_id 的 call）
    预检：回放能力闭包内一条业务 GET
       鉴权失败 → 回放类 todo 标 external_blocked；确定性修补仍要先落地
       非鉴权网络失败 → 记 warning，当预检通过
    分诊 → WorkflowIssue
  若仅剩 external_blocked（或产品选择：有 blocked 就停）→ EDITABLE + 按能力报告
  否则：
    operator → 现有提问通道
    auto_fix_flow_spec / _deterministic_capability_repair_edits
    逐能力闭环（已有第一版 + 预算/结论）
  汇总：全部 verified → 发布；否则 EDITABLE + by_capability 报告
```

原则：**发现问题 → 代码能修就修 → 需要回放才给 Skill → 立刻复查是否消失。** Skill 只处理真正需要语义判断的回放验证。

---

## 5. 分期（按序，每期可独立验收）

### P0-A 回放健康预检

**文件：** `replay.py`（复用 `replay_request`，不要新 HTTP 客户端）、`agent_tools/tools.py`（复用 `_recording_auth_headers` / `_recording_storage_state`）、`recording_runtime.py::verify`、必要时给 `ProductionRecordingServices` 注入「当前录制 session / tenant+subsystem」而不是 `pi`。

1. 探针请求：**不要**选全局「最便宜 GET」。`tenant/get-by-website` 这类请求常不吃同一套 ERP 登录态，会假绿。选能力闭包里 `usage in {execute, preflight}` 的业务 GET；没有则选成员步骤里第一条非 option 的 GET（本录制即 `sale-order/get`）。
2. 调用 `replay_request`，头和 cookie 与 `verify_dependency` 完全相同。
3. `_replay_auth_failed(status, body)`：HTTP 401/403，或 JSON `code==401`，或 msg 含「未登录」「登录已过期」。**不要**把任意含 `token` 的正文都当鉴权失败。
4. 预检失败：把 `dependency` / `dependency_candidate` / `write_verify` / `enum` 的 `resolver` 设为 `external_blocked`。用户文案：「录制会话登录态已过期，回放验证无法进行；请刷新凭证后重新点『开始分析』」。
5. **现有 early-return 会丢掉非 blocked issue。** 本任务要求：预检失败时 **仍先跑完** `apply_recorded_evidence_fixes` 和编译器绑定修补；outcome 的 issues 以 blocked 为主，但 `meta.verification_run` 必须保留完整分诊摘要（含未派发的确定性已修项）。若保持「有 blocked 就停」，要在文档化的验收里写明：401 当轮不进 Pi（这是目标），确定性修补必须发生在 `verify()` 内、return 之前。
6. 每轮 check 重新预检（恢复分析可能已刷新 cookie）；一轮内不重复。非鉴权失败不阻塞。
7. 凭证不得写入 issue.message 以外的任何模型可见字段；message 只说「未登录」，不打印 header。

**验收：** 假回放 401 → 一轮内 `EDITABLE`、Pi `prompt` 次数为 0；探针若换成 tenant URL 的对照测试必须失败（证明选了业务 GET）。网络异常不阻塞。

### P0-B 确定性修补（Python，不进 Skill）

**禁止**在阶段七遍历 `nodes[]` 回填 step_id。必须改源头，并扩展现有挂钩。

#### B1. option_source 绑定 —— `capability_compiler.py`

`_option_source_request_ids` / `_request_ref`：request_id 对不上物化 step 时，按 **METHOD + 归一化 path（去 query）** 匹配已有步骤（req_95 → `37f297a79434` 这类）。命中则 `step_id` 用物化步骤。

仍无步骤（req_46 dict-data/simple-list 从未物化）：**不要**为了过校验硬造公开步骤，也 **不要**生成缺 `step_id` 的 call 节点。二选一（优先 1）：

1. `usage=option_source` 且无 step 时不写入 `nodes` 的 call，只保留 `request_refs` 供枚举运行时拉选项；同时改 `flow_spec.py` 约 L16026，对无 step 的 option_source **不报**「未绑定有效接口步骤」。
2. 仅当该请求已被 `request_facts.usage` 标为需要物化、且能唯一对应时，才物化为内部 `read_option` 步骤（不暴露参数，不进入公开 `step_ids`）。

改完后依赖验证触发的 `compile_capabilities` 重跑必须仍保持绑定。

#### B2. 未归属步骤 —— 扩展 `_deterministic_capability_repair_edits`（`flow_spec.py` 约 L22995）

对 `unassigned_business_step` / `unassigned_materialized_step`：

- 响应经 confirmed link 流入某能力成员 → 该能力 `preflight`（改 `request_refs`/`step_ids` 的内部用途，不改公开 name/kind）
- 是某字段 `selects[].source_request_id` 或 option_sources → 该能力 `option_source`
- 都不是 → **不要问「是否新建查询能力」**。标内部用途或写入 `meta.unverified`，issue 从 Skill 队列消失。独立查询能力只能来自阶段六 Skill 计划。

不要实现 Skill op `reconcile_capability_membership`。

#### B3. 空值来源 —— `apply_recorded_evidence_fixes`

`source_kind=="unknown"` 且录制值空串/null 且无 fill/select/pick 证据 → `_set_param_from_evidence(..., "user_input", exposed=True, ...)`。  
**这里必须是 `user_input`，不是 `caller_input`。** reason：「录制值为空，调用方可选提供，默认空」。

#### B4. 记录选择器 —— `_capability_field_looks_internal` / 其调用点（约 L15886）

若字段是执行锚点的目标记录标识（`query.id` / `query.ids` / 路径 id），且能力 `kind in {update, approve, reject, delete, withdraw}` → 不报 `capability_internal_field_exposed` error（可留 suggestion）。不要把合法「调用方选哪条记录」打成内部 ID。

#### B5. 收窄写回读候选

`verification_todos` 里 `candidate_read_request_ids`：优先同资源 GET（同 path 家族的 get/page，或写响应 id 能对上的 get），排除 tenant/IM/telemetry。仍必须是录制里真实存在的 request_id。

#### B6. 分诊表（模块级常量，唯一权威）

每个 `machine_repair` 的 `check_code` 必须：有对应 **Python** 修补，或显式 `dead_end`（进最终报告，**不派 Skill**）。`capability_validation_failed` 在 B1 修好后应不再出现；若还出现且无 op 可修 → `dead_end`，不要再建议 `submit_recording_repair`。

`field_source_unknown` 在 B3 后应大量消失；剩余无证据的才 `operator`（现有「用户参数/固定值」通道，内部写 `user_input`/`constant`）。

**验收：** 用证据 4–7 形状的 FlowSpec：`evaluate_recording_release` / `verification_report` 不再把这四类丢给 Skill；`compile_capabilities` 重跑后 option_source 仍有合法绑定或合法地没有 call 节点。

### P0-C 编排器兜底 unverified

replayable 在能力预算耗尽后仍未解决 → 代码写入 `spec.meta["unverified"]`（`target_kind`/`target_id`/`reason`），`verification_todos` 已按 `_unverified_targets` 跳过。不依赖 Skill 提交 `mark_unverified`。发布/EDITABLE 报告列出全部 unverified。

无归属写步骤（如第二笔 DELETE）走流程组预算，同样兜底。

### P1 逐能力闭环补完（在 §1.5 上加，禁止重写分组）

1. `PipelineContext.capability_rounds: dict[str, int]`，每能力整个 run 最多派发 **2** 次；超预算 → P0-C。
2. 每组结束写 `spec.meta["capability_verification"][capability_id] = {status: verified|partially_verified|blocked, resolved, pending, reason}`。publish 并入 release；EDITABLE 随快照下发。
3. `finalize_verification_state` 的 summary 增加 `by_capability`。
4. 流程组用固定 key `"__flow__"`。

### P2 质量门、拒绝契约、术语

**文件：** `flow_spec.py::apply_recording_agent_submission`、`_semantic_candidate_gate`、`recording_live.py::_compile_param_source`、`SKILL.md`。

1. **不要重做「整批改逐 op」**——repair 已经逐 op。要做的是：`mark_unverified` 以及只改 `meta`/`link.meta.verified` 的 op **跳过** `_semantic_candidate_gate`（或门控允许「显式降级」）。失败的分类 op 仍只拒自己。
2. 所有 `rejected` 带 `allowed_values`（例 `{"field":"source_kind","allowed":["caller_input","session"]}`）。对 Skill 用 `caller_input`；**不要**把 FlowSpec 存量 `user_input` 批量改名。
3. 把用户可见/模型可见拒词 `use user_input` 改成 `use caller_input`（`recording_live.py` 约 L1179）。全局搜提示语，只改文案。
4. `mark_unverified.target_kind` 增加 `release_issue`（按 issue_id）。P0-C 是主路径，这个是 Skill 仍需要时的后路。
5. SKILL.md：被拒后按 `op_results` 逐项纠正；`mark_unverified` 不是默认策略，预算耗尽由编排器标记。

**验收：** 一批 6 op 中 1 个非法 source_kind → 5 applied、1 rejected 且带 `allowed_values`；同批的 `mark_unverified` **不再**因 `new_validation_errors` 被滚；`append_flow_version` 行为可保留但测试不要把 version+2 当成「事务提交成功」。

### P3（可选）前端

`PageRecorder.tsx`：活动日志按「能力 → 轮次 → 问题」折叠。activity 已有 `round` 和能力名 label。

---

## 6. 现有数据契约（对齐用）

`write_verify` todo：

```json
{"kind":"write_verify","target_id":"<step_id>","issue_id":"write_verify:<step_id>",
 "check_code":"write_verify","step_id":"<step_id>","write_request_id":"req_98",
 "candidate_read_request_ids":["req_93"],
 "suggested_tool":"execute_write_with_verify","completion_op":"bind_verify_read"}
```

收窄后候选应是同资源读，而不是 25 条杂 GET。

`release_issue`（修 B1 前的形状，修完应消失）：

```json
{"kind":"release_issue","check_code":"capability_validation_failed",
 "capability_id":"cap_8025671e6decafe3","resolver":"machine_repair",
 "suggested_operations":["submit_recording_repair"],
 "message":"Capability `edit_sale_order` call 节点 `call_3` 未绑定有效接口步骤"}
```

`WorkflowIssue`：`issue_id / code / message / severity / resolver / target / evidence / allowed_operations`。

术语对照（写代码时钉死）：

| 层 | 调用方提供 |
|---|---|
| Skill op / 拒词 / allowed_values | `caller_input` |
| `ParamField.source_kind` / `_set_param_from_evidence` | `user_input` |
| 操作员回答选项 | 「用户参数」→ 内部 `user_input` |

---

## 7. 测试

| 期 | 文件 | 必须覆盖 |
|---|---|---|
| P0-A | `tests/test_recording_stage_seven_preflight.py` | 业务 GET 401 → 无 Pi；tenant GET 不得当探针；网络错误不阻塞；确定性修补发生在 return 前 |
| P0-B | `tests/test_recording_stage_seven_triage.py` | B1–B5 各一例；`compile_capabilities` 重跑后绑定仍在；空值写成 `user_input` 不是 `caller_input`；id/ids 不再 error；映射表每个 machine_repair code 有修补或 dead_end |
| P0-C/P1 | 扩展 `tests/test_recording_stage_seven_rounds.py` | 预算耗尽自动 unverified；`capability_verification`；流程组 `__flow__`；**不要破坏现有 6 例** |
| P2 | `tests/test_recording_repair_ops_gate.py` | mark_unverified 不被质量门连坐；allowed_values；非法 constant 只拒该 op |

全局：`python -m pytest` 全绿，`ruff check .` 干净。凭证过期 → 1 轮 EDITABLE + 明确文案；凭证有效 → 四类问题零派 Skill；单 op 失败不连坐 mark_unverified。

---

## 8. 文件索引（以现码为准）

| 文件 | 用途 |
|---|---|
| `back/dano/onboarding/recording_workflow.py` | 状态机、SelfHealingPipeline、`_draft_fingerprint`、external_blocked early-return、current_round |
| `back/dano/onboarding/recording_runtime.py` | verify/repair/publish；**已有**逐能力循环 |
| `back/dano/onboarding/recording_verify.py` | todos/report、`apply_recorded_evidence_fixes`、执行器证据消费、**会重跑 compile_capabilities** |
| `back/dano/onboarding/recording_release.py` | `evaluate_recording_release`、ReleaseIssue（含幽灵 suggested_operations） |
| `back/dano/onboarding/recording_pipeline.py` | 编排壳 |
| `back/dano/onboarding/recording_pi.py` | Pi 会话；无浏览器时的阶段七约束 |
| `back/dano/onboarding/recording_gateway.py` | WS、persist_stage_six、resume_verification |
| `back/dano/execution/page/replay.py` | **回放与写回读的唯一 HTTP 执行器** |
| `back/dano/agent_tools/tools.py` | 工具分发、`_recording_auth_headers`、`_recording_storage_state` |
| `back/dano/execution/page/recording_live.py` | Pi op 落地、`LIVE_RECORDING_AGENT_OPS`、`caller_input`→`user_input` 编译 |
| `back/dano/execution/page/capability_compiler.py` | **option_source 绑定必须改这里** |
| `back/dano/execution/page/flow_spec.py` | DSL、`auto_fix_flow_spec`、`_deterministic_capability_repair_edits`、`apply_recording_agent_submission`、`_semantic_candidate_gate`、call 节点校验、内部 ID 启发式、执行指纹 |
| `back/agent/run_recording_pi.mjs`、`.../analyze-recording-evidence/SKILL.md` | 侧车与 Skill 口径 |
| `doc/recording-pipeline.md` | 八阶段文档，改完同步阶段七 |

**不存在、不要创建：** `flow_spec_edit.py`。

---

## 9. 明确不要做的事

- 不要整表重建 FlowSpec / 重新划分能力 / 清空 capabilities
- 不要在阶段七「补一个查询能力」来消化 page/get
- 不要只改 `nodes[].step_id` 而不改编译器
- 不要把 `ParamField.source_kind` 改成 `caller_input`
- 不要另写一套回放 HTTP 客户端
- 不要用全局最小响应 GET 做预检探针
- 不要把预检函数设计成 `_preflight_replay_health(spec, pi)` 并让 Pi 发请求
- 不要恢复已删除的历史测试文件
- 不要回退 §0.5 列出的工作区改动
- 不要在日志或 prompt 里打印 Authorization / cookie / token
