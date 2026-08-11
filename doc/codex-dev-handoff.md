# Dano 改造开发交接文档（For Codex，逐阶段执行）

> 你（Codex）的任务：按本文档五个阶段依次开发，每个阶段有明确的后端/前端改动清单与验收标准。
> 基线：git 提交 `7b0a8657`（当前 HEAD）。基线健康度：`cd back && python -m pytest` → 1444 通过 / 1 失败（`tests/test_fact_check_grounding_regression.py::test_fact_check_belongs_to_the_write_not_the_followup_read`，为已知既有问题，阶段三顺带修复，其余测试不得回归）。
> 产物金标准（结构对齐目标）：`_tmp_ref_zip/console-plugin-cdp-bug-pending-review/`（SKILL.md + reference.md + scripts/，脚本直调业务 API、每个写操作配自检脚本）。

---

## 0. 最终目标与验收（不可协商）

| # | 目标 | 验收标准 |
| - | ---- | -------- |
| G1 | 捕获侧泛化：能读取不同业务、不同页面 | 两个互不相关业务系统（若依系 + 非若依系）录制→产出 skill，零改引擎代码 |
| G2 | 准确性：参数来源与依赖判定正确 | 派生参数（`jobId ≠ itemId` 型：详情接口返回的内部 ID 喂后续更新接口）识别全对；评测集出准确率数字 |
| G3 | 完整性：整条链路捕获全 | 读接口、multipart 上传、鉴权头、settle、每个写操作配对 verify 读接口全部进 FlowSpec |
| G4 | 编排性：产出多步 SOP | SKILL.md 含 Transport 表、Steps+Done-when、Branch exit、Pitfalls，通过包校验器 |
| G5 | API 形式产出 skill | 自包含包直调业务系统 API，在不启动 Dano 的机器上凭 token 环境变量跑通「查→写→verify ok」 |
| G6 | 供另一个前端项目调用 | 独立于 skillfrontend 的消费方在目标系统完成「写操作 + verify ok」闭环 |

## 架构铁律（每个阶段都必须遵守）

1. **理解发生在当下，不做录后验尸**：模型从录制开始就在场（目标输入 → 实时因果对齐 → 当场消歧）；录后离线推断只是兜底。
2. **trace 是事实，FlowSpec 是经过验证的结论**：模型可以提任何假设，但只有机器验证回路盖章的结论才能进 FlowSpec 正文；验证记录由执行器生成，模型无法伪造（见阶段一 verification_log 设计）。
3. **人不是质量闸门**：全流程唯一人工介入 = 每个业务系统一次性提供账号会话（验证码登录人工登一次）。不存在人工确认步骤；验证失败 → 带错误喂回模型重试 → 重试仍不过标 `unverified` 降级产出。
4. **录制账号即授权边界**：能用该账号录入，agent 用同一账号做的任何读写重放都在同一权限半径内。不设夹具要求、不设危险写审批。验证写产生的记录事后走删除/撤回接口自动清理，清不掉则保留。
5. **模型永远接触不到明文凭证**：脱敏在事实层完成（复用 `token_store.mask_headers`），执行器代持 token 发请求。新增工具的输出必须过脱敏。
6. **文档零手写**：skill 的 SKILL.md/reference.md/Pitfalls 由模型生成、包校验器把关。唯一手写的是包规范本身（阶段一交付）。
7. **现有启发式规则降级为候选生成器**：不删除，但其输出只作为建议喂给模型，不再直接决定 FlowSpec（阶段三收尾时拆除其直接写入路径）。
8. **运行期零 LLM**：产出的 skill 包脚本是纯 Python 直调 API；模型只在录制/验证/生成阶段使用。

---

## 1. 现有系统事实手册（已核实，直接引用，勿重新考古）

### 1.1 录制 WebSocket：`/onboarding/page/record`（`back/dano/gateway/app.py` `record_ws` ≈L1479）

协议版本 `protocol_version = 2`。租户在首帧 `start.tenant` 里带（此 WS 不走 `X-Tenant-Key`）。

客户端 → 服务端（现有）：

| type | 关键字段 |
| ---- | -------- |
| `start` | `start_url`(必填), `tenant`, `subsystem`, `base_url`, `storage_state`, `token`, `capture_reads`, `resume_action`, `pi_recording_id`, `deploy` |
| `input` | `event.kind` ∈ click/dblclick/right_click/pointer_move/pointer_down/pointer_up/drag/text/key/scroll + 坐标/按键字段 |
| `ping` / `stop` / `reset` / `refresh_flow_spec` / `step_naming` / `business_description` | — |
| `finalize` | `operation_id`, `action`, `title` |
| `flow_update` | `edits[]`, `operation_id`, `expected_fingerprint` |
| `orchestrate_flow` | `operation_id`, `action`, `recording_id`, `expected_fingerprint`, `analysis_screenshots` |
| `auto_fix_flow` | 同上（无截图） |
| `publish_request` | `operation_id`, `action`, `title`, `expected_fingerprint`, `goal?` |
| `console_log_upload` | `entries[]` |

服务端 → 客户端（现有）：`started`、`frame`(base64 JPEG)、`request`(实时请求诊断)、`flow_spec`(含 `operation`/`check_report`)、`result`(发布结果)、`error`、`operation_progress`、`input_error`、`pong`、`stopped`、`reset_ok`。

### 1.2 发布链与 invoke 链

```
发布: record_ws(publish_request)
  → prepare_flow_release_candidate / validate_flow_spec
  → flow_spec_to_api_request
  → RecordingPiSession.prompt + require_publish_review
  → save_token(headers_from_api_request)
  → run_request_onboarding(page_onboard.py:116)   # 草稿→self_check→评审→publish_asset
  → _auto_export → write_skills

invoke: POST /v1/skills/{id}/invoke
  → Orchestrator.invoke_skill(orchestrator.py:198)
  → _run_recording_capability(:502)     # api_request ← 资产 body；get_token_headers → merge_auth_headers
  → invoke_skill_capability(capability_runtime.py:299)
  → execute_api(request_capture.py:4551) → httpx(request_capture.py:3640)
```

Token 端点已存在：`GET /v1/settings/token?tenant=&subsystem=`（打码返回）、`POST /v1/settings/token`。租户 HTTP 鉴权：Header `X-Tenant-Key`。

### 1.3 Pi 会话桥（模型宿主，阶段二/三在此扩展）

- `RecordingPiSession`（`back/dano/onboarding/recording_pi.py:116`）：`start()` 起 loopback uvicorn（127.0.0.1:0，挂 `dano.agent_tools.app.agent_tools_router`）+ node 子进程 `back/agent/run_recording_pi.mjs`，stdin/stdout JSONL 通信；
- Python→Node 命令：`start_session` / `prompt`(text+images) / `cancel` / `close`；Node→Python：`session_started` / `prompt_completed`(含 accepted_submission) / `agent_event` / `session_closed` / `runtime_error`；
- 上下文注入：`bind_flow_spec(spec)`(:296)、`bind_analysis_images(...)`(:257)；模型产出**不走 final_text**，走工具 HTTP 回调 → `apply_submission`；
- 模型配置：`run_recording_pi.mjs` `resolveModel()`(:57-88)，默认 openai-compat `deepseek-ai/DeepSeek-V3.2`，需 `DANO_PI_API_KEY`（可选 `DANO_PI_BASE_URL`）；SYSTEM_PROMPT 内联于 `run_recording_pi.mjs:33-42`；
- **加新工具必须双端**：① Python `agent_tools/tools.py` 的 `TOOLS` 字典（:2541-2565）；② Node `back/agent/recording_tools.mjs` 加 `defineTool` 代理。录制 Pi 目前仅暴露 5 个工具：`get_recording_state` / `submit_recording_plan` / `get_validation_report` / `submit_recording_repair` / `submit_recording_review`；
- 白名单 ops：`_RECORDING_AGENT_ALLOWED_OPS`（`flow_spec.py:19572`，26 个）：rename_step, promote_request, rename_field, bind_response_source, bind_option_source, set_loop_source, set_return_mapping, mark_field_as_system_var, mark_field_as_identity, create_capability, reorder_capability_steps, upsert_capability, upsert_capability_field, upsert_input_field, upsert_request_field, upsert_internal_field, upsert_computed_field, upsert_output_field, bind_dependency, set_map, set_condition, set_output_mapping, set_capability_relation, add_request_to_capability, remove_request_from_capability, reject_dependency。入口 `apply_recording_agent_submission`(:19926)，state/validation 读取 `recording_agent_state`(:19903) / `recording_agent_validation`(:19915)。

### 1.4 录制会话（事实层，保留不动，阶段三给它加 agent 操作方法）

`RecordSession`（`recorder.py:1108`）：`start`(:1197, 支持 storage_state 还原)、`start_screencast`(:1966)、`dispatch_input`(:2106)、`flush_recording`(:2173)、`pause_recording` / `reset` / `storage_state`(:2210)、`captured_all_requests` / `captured_reads` / `recorded_page_events` 等(:1580+)、`stop`(:3085)。
事件入口：DOM 事件走 `expose_binding("__danoRecord")`(:1236)→`_on_record`(:1705)；网络走 `_route`(:1529)/`_on_request`(:1471)/`_on_response`(:1616)。

### 1.5 导出（阶段四改造对象）

`write_skills(tenant, out_dir, *, rich=True, exclude_skill_ids=None)`（`export/agent_skills.py:3395`）；CLI `python -m dano.export.agent_skills --tenant <t> --out <dir>`。当前每 skill 输出：`SKILL.md`、`agents/openai.yaml`、`references/{CONTRACT.json,CAPABILITIES.md,OPTIONS.md}`、`scripts/{dano_call.py,submit.sh,...}`——**`dano_call.py` 是回调 Dano 网关的代理，这正是要改的形态**。

### 1.6 前端（skillfrontend，Vite+React+AntD）

- `PageRecorder.tsx`（4694 行）：WS 收发在 `ws.onmessage`(≈L1940-2133)；Flow Tabs：`abilities`(能力列表, `renderCapabilityComposerPanel`) / `requests` / `json`(≈L3625-3656)；发布前置条件在 L3643-3646（按钮禁用）与 L2382-2385（`!capabilities.length` 拦截）；「生成/优化能力」按钮 L3634 → `orchestrateFlow()`(L2721)；协议版本常量 L1176；
- 写死业务默认值：`Recording.tsx:6`（`subsystem="A-OA"`）、`Onboard.tsx:78/80/84`（seetacloud baseUrl / `A-OA` / swaggerUrl）、`Skills.tsx:11`（导出目录默认值）；
- API 层 `api/skills.ts`：`/v1/skills*`、`/v1/skills/{id}/invoke`、`/export/agent-skills`、`/v1/settings/token` 等。

---

## 2. 开发阶段总览

```
阶段一  执行器工具集 + 包规范校验器            （地基，无 UI 变化）
阶段二  录制会话接入模型：目标输入 + 实时理解 + 侧栏消歧
阶段三  录后即时验证 + 自主补采 + 机器发布闸门  （核心）
阶段四  自包含 skill 包渲染                    （产物形态切换）
阶段五  能力页面降级 + 业务解耦 + 消费闭环      （收尾与验收）
```

每阶段完成的定义：改动清单全部落地 + 该阶段验收全绿 + `cd back && python -m pytest` 无新增失败 + 前端 `npm run build` 通过。

---

## 阶段一：执行器工具集 + 包规范校验器

### 目标
给模型准备可靠的手脚（重放/扰动/值溯源），并立好产物的机器验收标尺。本阶段纯后端 + 一份规范文档，不改前端。

### 后端改动

**1. 新模块 `back/dano/execution/page/replay.py` —— 重放执行器**

```python
async def replay_request(request: dict, *, overrides: dict | None = None,
                         auth_headers: dict, base_url: str = "",
                         storage_state: dict | None = None) -> dict:
    """重放 trace 中任一请求。overrides 支持 url_path/query/body/headers 局部覆盖。
    返回 {status, response(脱敏后), elapsed_ms, replay_id}。"""

async def perturb_replay(chain: list[dict], *, perturb: dict,
                         auth_headers: dict, base_url: str = "") -> dict:
    """按序重放一串请求，对第一个请求施加 perturb 覆盖，
    diff 各响应中随扰动联动变化的值路径。返回 {replays: [...], linked_paths: [...]}。"""
```

- HTTP 发送复用 `request_capture.py` 内已有的 httpx 逻辑（参考 `execute_api` :4551 与 :3640 的客户端构造），不要重写一套连接/超时/Cookie 处理；
- 鉴权：调用方传入 `auth_headers`（来自 `token_store.get_token_headers` 或录制会话的 `extract_auth_headers`），执行器代持，**返回值必须经 `token_store.mask_headers` 思路脱敏**（响应 body 中的 token 形态字段也要打码，可复用 `flow_spec._client_redact_sensitive`）；
- 读写均可重放（铁律 4）。写重放完成后返回 `replay_id` 并把完整记录写入验证日志（见下）。

**2. 新模块 `back/dano/execution/page/verification_log.py` —— 验证记录（防模型伪造）**

```python
def record_verification(*, kind: str, subject: dict, evidence: dict) -> str:
    """kind ∈ {replay_read, perturb_link, write_execute, verify_read, enum_snapshot}
    返回 verification_id（uuid）。记录落 FlowSpec.meta['verification_log'] 或独立存储。"""

def get_verification(verification_id: str) -> dict | None
```

- 只有执行器代码路径可以生成 verification_id；阶段二/三的新 ops 凡携带 `verification_id` 的，`apply_flow_edits` 侧必须回查存在且 subject 匹配，否则拒绝——这是「模型无法伪造验证」的实现机制。

**3. 值溯源候选生成 —— 扩展 `request_capture.py`**

新函数（放 `request_capture.py` 或新文件 `value_tracing.py`，不要塞进已 2 万行的 `flow_spec.py`）：

```python
def discover_value_links(all_requests: list[dict]) -> list[dict]:
    """任意响应叶子值 → 时间序靠后的任意请求输入(URL路径段/query/body/header)。
    强值过滤：len>=6 的数字串、UUID、雪花ID、token形态；排除 _BORING_LINK_VALUES、
    时间戳、布尔、短枚举值。返回候选链 [{source_request_id, source_path, target_request_id,
    target_path, value_sample, occurrences}]，不落库。"""
```

- 现有 `discover_step_links`（:485，只扫写→写）保留不动，新函数覆盖读→写/读→读。

**4. 工具注册（双端）**

Python `agent_tools/tools.py` `TOOLS` 新增（签名统一 `async def x(run_id, params) -> dict`）：

| 工具名 | params | 行为 |
| ------ | ------ | ---- |
| `replay_request` | `request_id`, `overrides?` | 从当前录制会话的 `captured_all_requests()` 取请求 → `replay.replay_request` → 脱敏返回 + 写验证日志 |
| `perturb_replay` | `chain_request_ids[]`, `perturb` | → `replay.perturb_replay` → 返回联动路径 + verification_id |
| `list_link_candidates` | — | → `discover_value_links` 结果 |
| `get_verification` | `verification_id` | 查验证记录 |

Node `back/agent/recording_tools.mjs`：为上述 4 个工具各加 `defineTool` 代理（照抄现有 5 个工具的写法）。

**5. 包规范 + 校验器 `back/dano/export/skill_package/`**

- `spec.md`（唯一手写文档）：定义合格 skill 包 = `SKILL.md`（frontmatter name/description + 必备章节 Transport/Preconditions/Steps(每步含 Done when)/Branch exit/Pitfalls）+ `reference.md`（API chain 小节、每条链标注 verification_id 或 `unverified`）+ `scripts/`（`client.py` + 每能力一脚本 + `verify_*.py`，全部 `--help` 可运行、stdout 输出 JSON）+ 凭证零泄漏（全包 grep 不到 token 形态明文）；
- `validator.py`：`validate_skill_package(pkg_dir: Path) -> {ok, issues[]}`，逐条机器检查上述规则（脚本可运行性用 `subprocess` 跑 `--help`）；
- 单测：用 `_tmp_ref_zip/console-plugin-cdp-bug-pending-review/` 作正样本（允许对参考包缺失项降级为 warning），构造缺章节/漏脚本的负样本。

### 验收
- 单测覆盖 replay/perturb/值溯源/校验器；
- 集成冒烟：对任一已有录制 trace（tests 下已有夹具数据）跑 `list_link_candidates` → 选一条链 `perturb_replay` 走通（可用 tests 中 mock server 或录制回放桩）；
- 凭证零泄漏测试：所有新工具输出中正则扫不到明文 Bearer/长 token；
- 现有 1444 测试零回归。

---

## 阶段二：录制会话接入模型（目标输入 + 实时伴随理解 + 侧栏消歧）

### 目标
录制不再是「哑录制」：录制前一句话目标；录制中模型实时消费「操作↔请求」因果流，当场判定角色/参数来源/依赖假设；模糊处通过侧栏问操作人一句。**录制结束时 FlowSpec 初稿已带模型初判与证据。**

### 后端改动

**1. WS 协议扩展（`gateway/app.py` record_ws）**

客户端→服务端新增/扩展：

| type | 字段 | 说明 |
| ---- | ---- | ---- |
| `start` 扩展 | 新增 `goal_text: str` | 操作人一句话目标；空则录制中模型可主动问 |
| `agent_answer` | `question_id`, `answer` | 回答模型的消歧提问 |

服务端→客户端新增：

| type | 字段 | 说明 |
| ---- | ---- | ---- |
| `agent_question` | `question_id`, `text`, `options?[]`, `context_ref?` | 模型的消歧提问，前端侧栏展示 |
| `agent_insight` | `kind`(role/param_source/link/goal), `text`, `refs[]` | 模型实时判定的可视化流（只读展示） |

**2. 目标解析与 RecordedGoal**

`start` 带 `goal_text` 时：录制 Pi 首个 prompt 即解析目标为结构化 `RecordedGoal`（字段现成，`flow_spec.py:414`），经新 op `set_goal` 写入。`ensure_recorded_goal`(:7043) 已有兜底逻辑，保持兼容。

**3. 实时喂流（成本可控的触发式，不逐事件推送）**

- `RecordingPiSession` 新增方法 `notify_live_batch(delta: dict)`：gateway 在以下时机触发一轮分析 prompt——① 捕获到疑似提交写请求（`classify_request_role` 候选判定为 submit）；② 新请求累计 ≥15 条；③ 录制到带歧义的字段证据；④ `finalize` 前。prompt 文案指示模型调 `get_recording_delta` 拉增量；
- 新工具 `get_recording_delta`：`params {since_seq}` → 返回 seq 之后的新请求（脱敏）、新页面事件、启发式候选（复用阶段一 `list_link_candidates` 的增量视图）。在 `recording_agent_state` 的 facts 基础上做增量投影即可；
- 新工具 `ask_operator`：`params {text, options?}` → gateway 经 WS 发 `agent_question`，await 前端 `agent_answer`，**60 秒超时不阻塞**：超时返回 `{answered: false}`，模型按最优假设继续并标记待验证。

**4. 白名单 ops 扩展（`flow_spec.py` `_RECORDING_AGENT_ALLOWED_OPS` + `apply_flow_edits` 分支）**

| 新 op | 字段 | 语义 |
| ----- | ---- | ---- |
| `set_goal` | `goal`(结构化) | 写 RecordedGoal |
| `set_request_role` | `request_id`, `role`, `reason`, `evidence_refs[]` | 写 RequestAnalysis（覆盖启发式初判，须留 `actor: "agent"`) |
| `set_param_source` | `step_id`, `path`, `source_kind`(user_input/session_header/page_context/chained), `origin_request_id?`, `origin_path?`, `reason` | 参数来源四分类落到 ParamField |
| `propose_dependency` | `source_request_id`, `source_path`, `target_request_id/step_id`, `target_path`, `evidence` | 生成 FlowLink 草稿，`meta.verified=false` |
| `add_pitfall` | `text`, `evidence_ref?` | 追加 `FlowSpec.meta['pitfalls']`（阶段四生成 Pitfalls 章节的素材） |

- 注意既有守卫：`_prune_unsafe_auto_links` 等不得误删带 agent 证据的草稿链——为 agent 链加豁免标记；
- `recording_agent_validation` 扩展：检查 agent 结论均带 evidence/verification 引用。

**5. Node 侧**

- `run_recording_pi.mjs` SYSTEM_PROMPT 更新：角色从「录制后规划」改为「录制伴随分析师」，职责清单 = 因果对齐/角色判定/参数四分类/依赖假设(须配验证计划)/适时 ask_operator（一次只问一个、能自答不问）；
- `recording_tools.mjs` 注册 `get_recording_delta` / `ask_operator` 代理。

### 前端改动（`skillfrontend`）

1. `Recording.tsx` + `PageRecorder.tsx`：录制启动区加「本次录制目标」输入框（textarea，必填提示但允许空），随 `start` 消息发送 `goal_text`；
2. `PageRecorder.tsx` `ws.onmessage` 新增分支：`agent_question` → 右侧「录制助手」面板渲染问题卡片（选项按钮或文本框），回答发 `agent_answer`；`agent_insight` → 同面板只读时间线（角色判定/依赖发现的实时流）；
3. `Recording.tsx:6` 的 `subsystem="A-OA"` 改为 URL 参数/输入框（去写死，顺手完成 G1 的一部分）。

### 验收
- 录一个真实流程（带下拉选择 + 提交）：录制结束时 FlowSpec 中提交请求 role 正确、下拉选项源绑定正确、含至少一条 `propose_dependency` 草稿链，全部带 evidence；
- `ask_operator` 全链路：模型提问 → 前端答 → 结论落 FlowSpec；超时路径不阻塞录制；
- goal_text 为空/非空两种路径均可完成录制；
- 新增 ops 的单测（含 verification/evidence 缺失被拒的负样本）。

---

## 阶段三：录后即时验证 + 自主补采 + 机器发布闸门（核心）

### 目标
`finalize` 后人可离场。agent 自主完成：依赖链扰动验证 → 写操作真实执行 + verify 断言 + 清理 → 枚举/分支/verify 补采 → 验证全绿自动发布。人工确认从发布链路中拆除。

### 后端改动

**1. 验证-补采编排（新模块 `back/dano/onboarding/recording_verify.py`）**

`finalize` 产出 FlowSpec 后、发布前，gateway 启动验证阶段（新 WS 服务端消息 `verify_progress {stage, detail}` 推进度）：

```
loop（上限 N=5 轮）:
  1. agent prompt：读 recording_agent_state + 验证待办（未验证的 propose_dependency、
     无 verify 的写步骤、未拉全的枚举）
  2. agent 调工具执行：perturb_replay 验依赖 / replay_request 验读接口 /
     execute_write_with_verify 验写契约 / browser_* 补采分支与枚举
  3. agent 提交 confirm/bind ops（带 verification_id）
  4. recording_agent_validation 复查 → 仍有待办则下一轮
终态：全部验证通过 → 自动发布；重试耗尽 → 未决项标 unverified，照常发布并在产物标注
```

**2. 新工具（Python TOOLS + Node 代理，均走验证日志）**

| 工具 | params | 行为 |
| ---- | ------ | ---- |
| `execute_write_with_verify` | `write_step_id`, `inputs`, `verify_request_id`, `assertion`, `cleanup_request_id?` | 真实执行写 → settle 等待 → 重放 verify 读 → 断言 → 可选清理 → 返回 verification_id |
| `browser_navigate` | `url` | 复用录制会话仍存活的 Playwright 浏览器（RecordSession 新增方法，见下） |
| `browser_snapshot` | — | 返回当前页可交互元素的语义快照（role/label/text，复用 `_RECORDER_JS` 的语义提取思路） |
| `browser_click` / `browser_fill` / `browser_select` | `locator`(语义定位：role+name/text), `value?` | Playwright locator API 执行；期间网络照常被 `_route`/`_on_request` 捕获进 trace |

RecordSession 新增对应方法：`async agent_navigate(url)`、`async agent_snapshot() -> dict`、`async agent_act(kind, locator, value=None)`（内部用 `page.get_by_role/get_by_text`）。**浏览器生命周期**：`finalize` 后不再立即 `stop()`，进入验证阶段保活，验证结束后关闭（改 gateway 的会话收尾时序）。

> agent-browser CLI 作为该通道的替代实现，本阶段不引入，留接口（工具语义已抽象为 navigate/snapshot/act，后端实现可替换）。

**3. 白名单 ops 再扩展**

| 新 op | 字段 | 守卫 |
| ----- | ---- | ---- |
| `confirm_dependency` | `link_id`, `verification_id` | 回查 verification_log：kind=perturb_link 且 subject 匹配该链，否则拒绝；通过后 FlowLink `meta.verified=true` |
| `bind_verify_read` | `write_step_id`, `read_request_id`, `assertion`, `verification_id` | 同上（kind=write_execute/verify_read）；落 `FlowStep.fact_check` |
| `attach_enum_options` | `step_id`, `path`, `options[]`, `source_request_id`, `verification_id` | 走现有 SelectBinding 合并逻辑（`_bind_option_source` :17478） |
| `mark_unverified` | `target_kind`, `target_id`, `reason` | 重试耗尽时标注，进产物 unverified 章节 |

**4. 机器发布闸门（拆人工）**

- `publish_request` 处理链中：`require_publish_review` 保留（它本来就是 Pi 机器评审）；新增前置=验证阶段完成（或显式 `skip_verify` 供调试）；
- 验证全绿时 gateway **自动触发发布**（复用 publish_request 内部路径，action 名从 RecordedGoal slug 生成），无需前端点按钮；前端手动发布按钮保留为重试入口；
- `_auto_confirm_ready_capabilities`(:19887) 扩为正式路径：验证通过的 capability 自动 `confirmed=true`，不再依赖前端「采纳当前定义」勾选。

**5. 顺带修复**：`test_fact_check_belongs_to_the_write_not_the_followup_read`（fact_check 归属错挂到后续读请求的既有 bug，位于 `suggest_fact_check` :1621 附近归属逻辑）。

**6. 启发式降级收尾**：`classify_network_request` / `suggest_selects` 等的结论进 FlowSpec 时统一标 `actor: "heuristic", confidence`，agent ops 可覆盖；`to_flow_spec` 内直接采纳启发式结论的路径保留（作为无模型可用时的降级），但发布闸门要求关键结论（提交请求角色、依赖链、verify 绑定）必须有 agent+verification 背书，否则标 unverified。

### 前端改动
- `PageRecorder.tsx`：新增 `verify_progress` 消息处理，「录制助手」面板显示验证/补采进度（阶段、当前动作、已确认链数、verify 覆盖数）；
- 发布按钮语义改为「重新验证并发布」（自动发布成功后按钮态更新为已发布）。

### 验收
- 端到端（真实或 tests mock 业务系统）：录制含派生参数的流程 → 人离场 → agent 自动完成依赖确认（扰动验证）+ 写执行 + verify 绑定 + 枚举补全 → 自动发布成功；全程零人工；
- `confirm_dependency` 携带伪造 verification_id 被拒绝的负样本测试；
- 评测集落地：`back/tests/eval_link_accuracy/`，≥10 条人工标注真值的录制 trace，输出准确率/召回率报告脚本（G2 验收工具）；
- 既有失败测试转绿，全量测试无新增失败。

---

## 阶段四：自包含 skill 包渲染（产物形态切换）

### 目标
导出产物从「回调 Dano 的 dano_call.py 代理」切换为「直调业务系统 API 的自包含包」，结构过阶段一校验器。现有代理形态保留共存。

### 后端改动（集中在 `back/dano/export/`）

**1. 新渲染器 `export/skill_package/renderer.py`**

```python
async def write_skill_packages(tenant: str, out_dir: str, *,
                               skill_ids: list[str] | None = None) -> list[str]
```

对每个 PAGE_SCRIPT skill，从资产 `api_request` + FlowSpec（含 links/fact_check/pitfalls/verification_log）生成：

```
<out_dir>/<slug>/
  SKILL.md            # 模型生成正文 + 校验器把关（见下）
  reference.md        # API chain 小节(每条链标 verification_id 或 unverified)、
                      # 业务硬规则、Fallback 浏览器步骤(从 DOM 语义 steps 渲染)
  scripts/
    client.py         # 代码生成：BASE_URL、鉴权装配、http_json、成功规则
                      # (来自步骤 success_rule/infer_success_rule)、settle 等待
    <capability>.py   # 每能力一脚本：argparse 参数=capability 输入 schema；
                      # 按 verified FlowLink 依赖序先调派生源接口再发主请求；
                      # stdout 单行 JSON {ok, ...}（对齐参考 zip 脚本形态）
    verify_<cap>.py   # 从 fact_check/bind_verify_read 生成：读回断言，输出 {ok, issues[]}
```

`client.py` 鉴权解析顺序（生成进模板，运行期零 LLM 零 Dano 强依赖）：
1. env `DANO_AUTH_HEADERS`（JSON 字符串）；
2. 本地缓存 `~/.dano/sessions/<tenant>__<subsystem>.json`；
3. env `DANO_URL` + `DANO_TENANT_KEY` → `GET /v1/settings/token`（需后端新增**不打码**的内部变体：`GET /v1/settings/token/raw`，仅 `X-Tenant-Key` 鉴权，返回真实头；打码版维持原样供 UI）。

**2. 文档正文生成**

- 阶段三验证结束后，同一 Pi 会话追加一个「写文档」prompt：基于 FlowSpec 事实生成 SKILL.md 正文（Transport 表/Steps+Done-when/Branch exit/Pitfalls）与 reference.md 正文，结果经新工具 `submit_skill_docs {skill_md, reference_md}` 存入 `FlowSpec.meta['skill_docs']`；
- 渲染器优先取 `meta['skill_docs']`，缺失时用确定性模板兜底（结构完整但文案朴素），两种路径都必须过 `validate_skill_package`，不过则带 issues 重新生成（上限 3 轮，仍不过用模板兜底并记录）。

**3. 入口**

- `/export/agent-skills` 请求体扩展 `mode: "proxy" | "package" | "both"`（默认 both）；CLI `python -m dano.export.agent_skills --tenant t --out dir --mode package`；
- 发布后 `_auto_export` 同步支持 mode。

### 前端改动
- `Skills.tsx`：导出面板加 mode 选择（默认 both）；`Skills.tsx:11` 默认目录改为相对仓库路径或留空必填（去写死）。

### 验收
- 对阶段三产出的 skill 导出 package 模式 → `validate_skill_package` 全绿；
- 在**不启动 Dano** 的环境（新 venv，仅装 httpx）：设 `DANO_AUTH_HEADERS` → 依次跑 `<capability>.py`（查）、写能力脚本、`verify_*.py` → 全部 `ok: true`（G5 验收）；
- reference.md 中每条 API chain 能溯源到 verification_id；unverified 项如实标注；
- 生成包全文扫不到明文 token。

---

## 阶段五：能力页面降级 + 业务解耦 + 消费闭环（收尾）

### 前端改动（能力页面处理——定论：不删数据，删人工职责，界面降级为观察器）

`PageRecorder.tsx`：

1. **拆除人工编排交互**：`abilities` tab 默认渲染为只读投影（能力/接口/依赖/IO schema/验证记录/unverified 标记均只读）；「新增能力」「删除能力」「加接口/选用途」「上下移」「手改 IO JSON」「采纳当前定义」勾选，全部移入折叠的「维护模式」（一个开关展开，作为模型判错时的例外修正通道，走既有 flow_update ops，不删后端能力）；
2. **拆除发布对人工编排的依赖**：删 L2382-2385 的 `!capabilities.length` 手动拦截与 L3643-3646 按钮禁用中对能力数量的检查（能力生成已是录制/验证阶段自动完成，发布闸门在服务端）；
3. 「生成/优化能力」按钮从主操作降级为「重新分析」重试入口；
4. 保留 `requests`/`json` tab 作调试。

### 后端改动（业务解耦）

1. 新目录 `back/dano/business_packs/`：`loader.py`（按 tenant 加载 JSON/YAML 业务包，缺省空）+ `packs/a-company.yaml`（现 A 公司三件套内容迁入作样例）；
2. 迁移消费点：`orchestrator/skills.py` 的 `ACTION_META`、`catalog/manifest.py` 的 `_ACTION_TITLES`、`shared/std_fields`、`capabilities/oa_templates.py` 若依方言、`export/agent_skills.py` 的 `_PROTOTYPE_SUBSYSTEMS`、gateway resume 默认 `"A-报销"` ——全部改为从业务包读取，包缺失时行为=空（不报错）；
3. 硬断言测试：`back/tests/test_no_business_words_in_engine.py`——对 `back/dano/`（排除 `business_packs/`、tests）grep「请假|报销|工单|A-OA|A-报销|seetacloud|若依|ruoyi」必须零命中；
4. `Onboard.tsx:78/80/84` 写死默认值同步清除（改空+placeholder）。

### 消费闭环（G6 验收物）

- 新目录 `consumer-poc/`（独立最小 Node 或 Python CLI 项目，不依赖 skillfrontend）：读取阶段四导出的 skill 包目录，列出 capabilities（解析 references/CONTRACT.json 或 SKILL.md frontmatter），按输入 schema 组装参数调用 scripts，展示 verify 结果；
- README 写明另一个前端项目的两种接入方式（直调 scripts / 按 capability schema 封装）。

### 验收（同时是全项目终验）
- G1：两个互不相关系统（准备一个若依 demo + 一个任意非若依管理后台，或等效 mock）录制→发布→导出全链路零改码；
- G2：评测集准确率报告（阶段三工具）达标，派生链场景全对；
- G3：终验 skill 中每个写能力有 verify；
- G4：包校验器全绿；
- G5：无 Dano 环境跑通「查→写→verify ok」；
- G6：consumer-poc 在目标系统完成写操作 + verify ok；
- 工程底线：全量 pytest 无失败（含阶段三修复项）、`npm run build` 通过、引擎业务词零命中断言通过。

---

## 3. 禁止事项（做了即返工）

1. 不得重写/替换 Playwright 录制通道（`RecordSession` 的捕获回调体系保留）；
2. 不得删除 `FlowCapability` 数据结构或其后端编辑 ops（前端只是降级展示）；
3. 不得在任何流程中加入人工确认闸门（唯一人工=账号会话提供；`ask_operator` 只在录制中人在场时使用且超时不阻塞）；
4. 不得把明文凭证送入模型上下文、日志或产物（所有新工具输出过脱敏；`/v1/settings/token/raw` 仅供生成包的 client.py 运行期调用）;
5. 不得往 `flow_spec.py` 单文件继续堆新逻辑——所有新模块独立成文件；
6. 不得让模型结论绕过 verification_log 直接写入 verified 状态；
7. 不得破坏 WS 协议版本 2 的既有消息兼容（新增 type 只增不改）；
8. 生成的 skill 包脚本不得依赖 Dano 运行时（mode=package 时）。

## 4. 建议执行顺序与依赖

阶段一 → 阶段二 → 阶段三 → 阶段四 → 阶段五，严格串行（各阶段依赖前序产物）。阶段一内部（执行器 vs 校验器）与阶段五内部（前端降级 vs 业务解耦 vs consumer-poc）可并行。开发环境：后端 `cd back && pip install -e .[page,dev]`（以 `back/pyproject.toml` 为准），Pi 需要 node + `DANO_PI_API_KEY`；前端 `cd skillfrontend && npm i && npm run dev`。
