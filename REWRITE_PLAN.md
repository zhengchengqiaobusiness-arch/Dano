# Dano 后端重写计划 v2(修订版)

> 目标:在 `E:\python\try\Dano\back` 用 **Python 主体 + Pi SDK Node Sidecar** 重写,**只做阶段一(接入期)+ 阶段三(保障期)**;阶段二(运行期编排)交前端,后端只保留**可信瘦执行**。
> 本版并入对 v1 的 8 点修正:命名(SDK 非 RPC)、前端信任边界、沙箱/生产入口隔离、发布证据不可伪造、JSONL 协议、内部接口防护、模板分流前置、状态模型三分。
> 设计依据:《后端流程设计 v3》(12 流程,本版只落地 1–5 与 10–12)。

---

## 0. 锁定决策

1. **Python 是主系统**:资产模型、数据库、企业连接、验证、发布闸门、执行、审计、生命周期。
2. **Pi 经 Node.js SDK Sidecar 子进程接入**(`createAgentSession`)。Node 只管 AgentSession / LLM 循环 / 自定义 Skills / 工具声明;**真实工具能力全部由 Python 提供**。——**不是 `pi --mode rpc`**。
3. **Python ↔ Node 用带 `run_id` 的 JSONL 控制协议**;Pi 自定义工具经**仅本机 + 临时令牌**的内部接口回调 Python。
4. **后端移除** 自然语言意图识别、多智能体路由、知识检索编排。前端做交互编排 + 选 Skill,但**不得决定** endpoint / 凭证 / 断言 / 发布状态。
5. **后端保留可信瘦执行**:载已发布 Skill → 取 Vault 凭证 → 调企业系统 → 前后置断言 → 事实核查 → 审计 + 失败上报。
6. **阶段一/三只用沙箱 / 测试账号**;正式 `/invoke` 与内部验证入口**物理隔离**。
7. **`publish_asset` 不可绕过**;只认**后端生成、与资产内容哈希绑定**的验证证据(非 Agent 自报布尔值)。

---

## 1. 架构总览

```
企业材料(OpenAPI/制度/部署/测试账号)
        │ POST /onboarding
        ▼
  onboarding/service.py
        │  ① 计算源指纹 → 查模板/已验证资产
        ├──[精确命中]──→ 克隆模板 + 确定性验证 + 发布(不启动 pi)
        └──[未命中/变化]──→ spawn Pi SDK Sidecar 自主生成
                               │ JSONL 控制协议(run_id)
                               ▼
                    Node: createAgentSession
                      customTools(HTTP 代理)→ /_agent/tools/*(仅本机+令牌)
                      skills(ResourceLoader)
                               │ pi agent loop:观察→推理→行动→验证→反思
                               ▼
        Python 工具实活:parse_spec / sandbox_test / write_readback /
                         health_check / fingerprint / asset_store / publish_asset
                               │  硬关卡:validation_run_id 重读校验
                               ▼
                    资产库(PostgreSQL,作用域+源指纹+版本+验证状态+生成报告)
                               │
        ┌──────────────────────┼───────────────────────────┐
   [阶段三]                [对外只读]                  [瘦执行]
   lifecycle 状态机       GET /v1/skills(契约)        POST /v1/skills/{id}/invoke
   failure 分类熔断       GET /assets/published         (载已发布→取Vault→调OA→断言→事实核查)
   self-heal(pi 自愈)                                  └→ 前端编排调用
   POST /assurance/report-failure
```

**控制层(Python 后端)掌握规则、资产、风险、审计;执行层消费已发布资产。** 前端只提交意图层结果,不碰可信边界。

---

## 2. Pi ↔ Python 桥(SDK Sidecar)

### 2.1 进程模型
- Python(`onboarding`/`assurance`)按需 `spawn` 一个 Node 进程:`node back/agent/run_pi.mjs`。
- Node 内 `createAgentSession({ model, customTools, ... })` + 经 `DefaultResourceLoader` 加载 `back/agent/skills/*.md`。
- 每个 `customTool.execute` 只做一件事:带令牌 HTTP POST 到 Python `/_agent/tools/{name}`,拿回结果交还 pi。
- **TS 面压到最小**:`run_pi.mjs`(入口)+ `tools.mjs`(N 个 ~10 行代理)+ markdown skills。其余全 Python。

### 2.2 JSONL 控制协议(stdin/stdout)
**铁律:stdout 只输出 JSONL,所有日志走 stderr;每条消息带 `run_id`;工具调用带 `tool_call_id`。**

Python → Node(stdin):
```json
{"type":"start_run","run_id":"run-001","tenant_id":"a-corp",
 "system_instance_id":"a-oa","skill":"onboard-system",
 "prompt":"解析 RuoYi OA 并生成五类资产",
 "context":{"materials_ref":"mat-001"},
 "budget":{"max_iters":40,"max_tokens":200000,"timeout_s":600}}
{"type":"cancel_run","run_id":"run-001"}
```

Node → Python(stdout,逐行 JSONL):
```json
{"type":"run_started","run_id":"run-001"}
{"type":"tool_call_started","run_id":"run-001","tool_call_id":"tc-1","tool":"parse_spec"}
{"type":"tool_call_finished","run_id":"run-001","tool_call_id":"tc-1","status":"ok"}
{"type":"agent_message","run_id":"run-001","text":"已抽出 7 个动作…"}
{"type":"run_completed","run_id":"run-001","status":"completed",
 "asset_draft_ids":["asset-001","asset-002"],
 "validation_run_ids":["val-001","val-002"]}
```

工具回调(Node `execute` → Python):`POST /_agent/tools/{name}`,体 `{run_id, tool_call_id, tenant_id, params}`,头 `X-Agent-Token`。

### 2.3 内部工具接口安全(`/_agent/tools/*`)
- **只监听 127.0.0.1**;
- **`X-Agent-Token`**:Python 在 spawn 时为本次 run 生成临时随机令牌,run 结束立即作废;
- 校验 `run_id` 与当前活跃 run 匹配、`tool_call_id` 幂等(重复调用返回同结果)、`tenant_id` 与 run 一致;
- 工具白名单(只允许声明的几个);超时 + 载荷大小上限;每次调用落审计。
- **环境变量白名单**:spawn Node 时**不传父进程整套 env**,只给:
  ```
  DANO_AGENT_TOKEN=<临时令牌>
  DANO_AGENT_BASE_URL=http://127.0.0.1:8000
  DANO_AGENT_RUN_ID=run-001
  PI_MODEL / PI_API_KEY(仅 pi 需要的)
  ```
  防止 Node/Pi 经 bash 读到 DB 密码、生产密钥。

### 2.4 边界情况(必须处理)
非法 JSON / 超时 / `cancel_run` / Node 异常退出 / 模型无凭证或额度不足 / Skills 未加载成功 → 一律 **本 run 标 `failed`**,清理子进程。
**不做 mid-run 续跑**:生成是临时、可重放的;失败即重触发,靠 `publish_asset` 内容哈希绑定 + 幂等保证重跑安全(同资产不会重复发布、坏资产发不出)。连跑无僵尸进程是 Phase 0 验收项。

---

## 3. 信任与权限边界

### 3.1 前端能做 / 不能做
**能**:收集输入、选 Skill、排步骤、展示确认卡片、调 `/invoke`、展示进度结果。
**不能**:作为可信执行边界。前端**只**提交:
```json
{"skill_id":"submit_leave","skill_version":"1.2.0",
 "input":{"leaveType":"annual","leaveDays":3},
 "idempotency_key":"...","confirm":true}
```
后端按 `skill_id + version + tenant` 从资产库取回 endpoint / 字段映射 / 凭证引用 / 风险规则 / 前后置断言 / 事实核查策略。**拒绝**前端直传 endpoint / credential_ref / assertions。

### 3.2 沙箱验证入口 vs 正式执行入口(隔离)
| | 内部生成验证 | 正式运行执行 |
|---|---|---|
| 入口 | `POST /_agent/tools/sandbox-test` 等 | `POST /v1/skills/{id}/invoke` |
| 调用方 | 仅 pi(127.0.0.1 + 令牌) | 前端(X-Tenant-Key) |
| 环境 | 强制 `environment=sandbox` | 生产 |
| 凭证 | 强制 `credential_type=test`(`vault://{t}/{s}-test`) | 生产凭证(`vault://{t}/{s}`) |
| 写动作 | `production_write=forbidden` | 允许(过风险闸门) |
| 校验 | run 活跃 + 令牌 | 已发布 + 租户 + actor + 风险 + 幂等 + 确认 |

物理隔离确保 pi 在生成阶段**无法**误触生产写接口。

---

## 4. 发布硬关卡(证据不可伪造)

```
1) pi 起草资产 → asset_store.save_draft → Python 返回 {asset_draft_id, content_hash}
2) pi 调 sandbox_test(asset_draft_id,…) → Python 真跑 HTTP,落 ValidationRun:
   {validation_run_id, asset_draft_id, content_hash, kind, environment=sandbox,
    credential_type=test, request, response, evidence, passed, created_at}
   → 返回 validation_run_id
3) pi 调 publish_asset({asset_draft_id, validation_run_ids:[...]})
4) Python 服务端校验(全过才发布):
   ✓ draft 存在,属于本 run 的 tenant + system_instance
   ✓ 每个 validation_run_id 存在、由后端生成、passed、未过期(如 <1h)
   ✓ environment=sandbox 且 credential_type=test
   ✓ 每条 ValidationRun.content_hash == draft.content_hash(防换草案)
   ✓ 该资产类型要求的验证种类齐全:
       连接器=connect+sandbox;字段映射=readback;环境画像=health;页面=replay
   → 才创建 published 信封
```
**绝不接受** `{"sandbox_passed":true}` 这类 Agent 自报布尔值。

---

## 5. 状态模型(三分,不混淆)

```
资产状态:     draft → validated → published → deprecated
Skill 生命周期: template → bound → testing → pending_release → published → suspended
实例运行状态:  enabled / disabled / degraded
```
- **阶段一完成 = 资产 `published` + Skill `published`**,**不自动 = 运行中**。
- 是否 `enabled/running` 取决于:前端是否接入 + 客户侧 Worker 在线 + Vault 凭证有效 + `/invoke` 可达 → 由独立的**就绪检查**置位,不由接入流程置位。
- (修现有代码:`_register_lifecycle` 当前直接 drive 到"运行中",应停在"已发布"。)

---

## 6. 静态模板分流(pi 启动前,确定性)

```python
# onboarding/service.py
fp = fingerprint(materials)
if template_registry.has_exact_match(fp):
    return onboard_from_template(...)   # 克隆模板 + 确定性验证,降本、去不确定性
else:
    return onboard_with_pi(...)          # 启动 Pi Sidecar 自主生成
```
**不能**已启动 pi 再让模型决定"不用模型"。

---

## 7. `back/` 目录结构

```
back/
├── pyproject.toml
├── dano/
│   ├── shared/          # 端口:5类body + WorkflowSkillBody + 信封 + enums + expr + std_fields
│   ├── assets/          # 端口:PG 仓储 + 内存库 + ValidationRun 表 + asset_draft
│   ├── schemas/         # 新:pi 产物的 Pydantic/JSON Schema 校验(落库前强制)
│   ├── capabilities/    # 端口:doc_parser/oa_templates/endpoint分类/智能抽离0-4/sandbox/写回/health/fingerprint
│   ├── agent_tools/     # 新:/_agent/tools/* —— pi 工具的 Python 实现(薄封装 capabilities)+ 令牌/白名单
│   ├── onboarding/      # 阶段一:模板分流 → spawn pi → 收草案 → 硬关卡发布 → 接入报告
│   ├── assurance/       # 阶段三:lifecycle状态机 + failure分类熔断 + self_heal
│   ├── execution/       # 瘦执行:RealActionExecutor + auth + harness(单skill) + 断言 + 事实核查 + 幂等
│   ├── catalog/         # 对外:/v1/skills 契约 + /assets + /invoke
│   └── gateway/         # FastAPI + CORS + 租户鉴权 + lifespan + run 管理
├── agent/               # 新:pi 桥(TS,极薄)
│   ├── run_pi.mjs       # createAgentSession 入口 + JSONL 协议
│   ├── tools.mjs        # defineTool × N,各自 HTTP 代理到 Python
│   └── skills/          # markdown:generate-{connector,field-mapping,policy-rule,env-profile,page-script}.md
│                        #           onboard-system.md / self-heal.md
├── migrations/          # 端口 001-005 + 新:validation_runs / asset_drafts
├── examples/            # 端口 ruoyi_mock_server / real_oa_server
└── tests/               # pytest(tools/硬关卡/状态机/智能抽离)+ vitest(pi 桥)
```

---

## 8. 分阶段实施(每步怎么做 + 验收)

### Phase 0 · pi 桥打样(闸门,1–2 天)
做:`back/` 脚手架;装 `@earendil-works/pi-coding-agent`;写 `run_pi.mjs`(JSONL 协议)+ 1 个 `parse_spec` 代理工具 + 1 个 skill;Python 临时 `/_agent/tools/parse_spec`。
**验收(10 条,全过才继续)**:
1. Python 能启动并关闭 Node Sidecar;
2. Pi SDK 能正确初始化模型;
3. 自定义 Skill 能被 ResourceLoader 加载;
4. Pi 能主动选择 `parse_spec`;
5. Node 能带 `run_id`/`tool_call_id` 调 Python 工具;
6. Python 返回结构化结果;
7. Pi 能基于结果产出最终结构化输出(`run_completed`);
8. 超时 / 取消 / 异常退出能正确回收子进程;
9. stdout 纯 JSONL,不被普通日志污染;
10. 连续执行 10 次无僵尸进程。

### Phase 1 · 资产底座(端口)
端口 `shared/`、`assets/`、migrations 001–005;**新增** `validation_runs`、`asset_drafts` 表;`schemas/`(pi 产物落库前 Pydantic 校验,不收自由文本)。

### Phase 2 · 能力工具(端口进 agent_tools)
端口 `capabilities/`(doc_parser/oa_templates/endpoint分类/智能抽离0-4/RealSandbox/写回/health/fingerprint);建 `/_agent/tools/*`(令牌+白名单+幂等);`publish_asset` 实现 §4 硬关卡;`sandbox-test`/`write-readback`/`health-check` 强制 sandbox+test。

### Phase 3 · 阶段一 pi 自主生成(核心)
写 5 个生成 skill + `onboard-system.md`(纪律:库中选不自造/声明式/自验证才出库/置信分流);`run_pi.mjs` 挂全工具+skills;`onboarding/service.py` 实现 §6 模板分流 + spawn pi + 收草案 + 复合流程编排(端口 oa_templates workflows)+ §4 发布 + 接入报告;`POST /onboarding`。
**分工铁律**:Pi 决定"**怎么生成**"(读哪些材料/调哪些侦察/草案怎么写/失败补什么);Python 固定"**允许到哪一步、何时可发布**"(工具白名单/最多迭代/预算/超时/发布条件/生命周期/Schema/租户隔离)。

### Phase 4 · 阶段三 保障期(端口为主 + pi 自愈)
端口 lifecycle 状态机(流程12,停在"已发布")、failure 分类+熔断(流程10);`self-heal.md` + `assurance/self_heal.py`(增量再侦察→最小补丁→离线验证→灰度+回滚)。
`POST /assurance/report-failure` 收**统一失败事件**:
```json
{"tenant_id":"a-corp","system_instance_id":"a-oa","skill_id":"submit_leave",
 "skill_version":"1.2.0","failure_type":"field_changed",
 "execution_id":"exec-001","evidence_ids":["ev-001"],"occurred_at":"2026-06-16T10:00:00Z"}
```
来源:瘦 `/invoke` / 前端编排层 / 客户侧 Worker / 定时指纹检测 / 人工。

### Phase 5 · 对外契约 + 瘦执行(给前端)
端口 `/v1/skills` 契约(manifest)+ `/v1/skills/{id}` + `/assets/published` + CORS + X-Tenant-Key;端口瘦 `/invoke`(§3.1 仅收 skill_id+version+input+idempotency;载已发布→服务端字段映射→Vault凭证→API/工作流执行→前后置断言→事实核查→幂等→审计→失败上报)。**砍掉** `handle()`/意图/路由/多智能体。

### Phase 6 · 真实验证
端口 ruoyi_mock_server;真实链路:
```
POST /onboarding → Pi 生成草案 → Python 工具验证 → publish_asset 硬关卡 → PG 落库
→ GET /v1/skills → POST /v1/skills/{id}/invoke → 重查 OA → 事实核查 → 审计落库
```
vitest(pi 桥)+ pytest(tools/硬关卡/状态机/智能抽离)。

---

## 9. 端口 / 新建 / 丢弃

| 处置 | 模块 |
|---|---|
| **端口** | shared/assets/migrations、doc_parser/oa_templates/endpoint分类/智能抽离0-4、sandbox/写回/fingerprint、lifecycle/failure/self_heal、manifest/CORS/租户、瘦 invoke(invoke_skill/_run_api/_run_workflow/harness/断言/事实核查)、ruoyi_mock |
| **新建** | `agent/run_pi.mjs`+`tools.mjs`+`skills/*.md`、`agent_tools/`(/_agent/tools/*)、`schemas/`(pi 产物校验)、`validation_runs`/`asset_drafts` 表 + **publish_asset 证据闸门**、临时令牌/env 白名单、模板分流前置、状态三分 |
| **丢弃** | 5 个硬编码生成器(改 pi+skill 生成)、orchestrator `handle()`/意图分析/多智能体路由/知识检索编排 |

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| pi 桥能否通(SDK Sidecar + 回调) | **Phase 0 闸门**(10 条验收);不通退回"Python 移植 pi 模式" |
| pi 自主生成不确定/想跳验证 | `publish_asset` 证据闸门(§4)不可伪造;skill 写死纪律;沙箱双关挡坏资产;Python 控发布条件 |
| pi 误触生产写 | 沙箱/生产入口物理隔离(§3.2);test 凭证命名空间分离;`production_write=forbidden` |
| 密钥外泄 | env 白名单 + 内部接口仅本机 + 临时令牌(§2.3) |
| 前端越权 | 前端只传 skill_id+input;endpoint/凭证/断言后端取(§3.1) |
| 双语言运维 | TS 面最小(入口+代理);CI 同跑 vitest+pytest |
| LLM 成本/不确定 | 模板精确命中分流不启动 pi(§6) |

---

## 附:核心数据契约

```
asset_draft:      {asset_draft_id, run_id, tenant, system_instance, asset_type, body, content_hash, created_at}
validation_run:   {validation_run_id, asset_draft_id, content_hash, kind(connect|sandbox|readback|health|replay),
                   environment, credential_type, request, response, evidence, passed, created_at, expires_at}
publish 请求:      {asset_draft_id, validation_run_ids:[...]}
invoke 请求(前端): {skill_id, skill_version, input, idempotency_key, confirm?}
failure 事件:      {tenant_id, system_instance_id, skill_id, skill_version, failure_type, execution_id, evidence_ids, occurred_at}
```

---

> **结论**:本版已并入 8 点修正(SDK 命名、前端信任边界、沙箱/生产隔离、证据不可伪造、JSONL 协议、内部接口防护、模板分流前置、状态三分)+ #5 收口(失败即重触发,不做 mid-run 续跑)。可作为重写基线;Phase 0 是不可跳过的可行性闸门。

---

# 附录 B:goal 模式「代码自动生成」重构(M0–M6)

> 背景:声明式/框架 driver 不再作为独立主路径,**主路径统一为「按业务自动生成代码」**;生成本身是 **goal 模式的迭代闭环**(拆解→定方案→编码→审核→漏洞校验→测试,驳回带 reasons 回灌同一 goal 会话续跑,非一次成型)。
>
> 可变(按业务区分)= `dano/generation/strategies/`;不变(共享)= `dano/generation/controller.py` + 现有 审核/沙箱/事实核查/发布闸门 + 隔离 runner。

## 设计不变量
1. goal 模式:给 pi `GoalBrief`(目标流程 + 验收标准 + 工具 + 预算),pi 自主拆解/定方案/编码/自测。
2. 闸门=客观真值,pi 不能自证;发布只认 `verify_publishable` + `verify_reviewed`(回 PG 重读)。
3. 驳回=结构化 reasons 回灌**同一 goal 会话**续跑,有界预算;**不存在一次生成直接发布**。
4. 凭证永不进代码/LLM,运行期注入;每个适配器=PG 按租户版本化产物。

## 里程碑
- **M0 地基**:`AssetType.ADAPTER` + 迁移 + `AdapterBody`/`PlanBody` + `ValidationKind="vuln"` + 隔离 runner(`execution/adapter/runner.py`:子进程隔离、超时、凭证经 stdin 注入、源码零凭证)。证明:能安全跑生成代码。
- **M1 最小 goal 闭环 ★**:`strategies/base.py` + `strategies/simple_http.py` + `controller.py` + goal 会话常驻多轮(`run_pi.mjs` + `pi_session.py`)+ pi 工具 `draft_adapter`/`sandbox_test_adapter`。证明:拆解→编码→测试→驳回→修复→发布 整圈跑通、非一次成型。
- **M2 全关卡**:审核(成果验收+合规)+ 漏洞校验(`generation/vuln.py` + `vuln_scan`)接入 loop;`review/board.py` 切代码评审变体。证明:三关都能驳回。
- **M3 事实核查+多轮反馈**:`execution/fact_check.py` 通用化 + goal 会话多轮 reasons 回灌硬化。证明:空操作必被打回。
- **M4 业务策略**:`workflow_bpmn`(复用若依请假成果)+ `crud_query` + `approval`;`probe_api`/`draft_plan`。证明:按业务区分生成。
- **M5 类别控制+接入改写**:`/onboarding/preview` 选类别 + `onboard` 改走 goal-loop;旧声明式降级为策略。证明:超大 swagger 可控。
- **M6 调用+生命周期+可观测**:`AdapterExecutor` 调用 + 增量自愈 + 迭代追溯。证明:生产可运维。

落地顺序:M0 → M1(首个可演示闭环)→ M2/M3 → M4(复现若依请假)→ M5 → M6。
