# 页面直驱(Agent 自主操作)接入模式 — 完整修改文档

> 状态:设计定稿,待实现。作者审定决策:① pi 子进程驱动 ② 首跑默认 dry。
> 关联:`PAGE_SKILL_PLAN.md`(流程8 页面脚本基线)、`PROMPTS.md`(pi 提示词全景)、记忆 `dano-page-native-agent`。

---

## 0. 一句话

给定**一个页面 URL + 一句话业务目标 + 测试账号**,让 pi Agent **自己在真实页面上操作、读页面反馈、纠错、跑通**,把**它自己**跑通的成功轨迹**结晶**成确定性、可被前端凭公司 key 快速调用的页面 Skill;页面改版时 Agent **自己重跑再结晶**,人不在回路。

---

## 1. 三种接入模式的定位(务必分清)

| 模式 | 谁是"智能" | 产出方式 | 维护(页面/接口变) |
|---|---|---|---|
| Swagger 导入 | 反推接口 | 解析文档 → 连接器/复合流程 | 重新导入 |
| 屏幕录制(已有) | **人** | 录**人**的点击轨迹 → 固化 | **人重录** |
| **页面直驱(本文档,新增)** | **Agent 自己** | Agent 自主操作真实页面跑通 → 结晶它自己的成功轨迹 | **Agent 自己重跑再结晶** |

**立场**:不分析公司业务逻辑,让**原始网页当业务规则的执行者**;平台只干三件不依赖业务逻辑的事——**让 Agent 操作稳、确认结果真(回查)、固化成可快调的 Skill**。只考虑全面性 / 稳定性。

**关键澄清**:"记录成功轨迹"中的"记录" = 系统记录**智能体自己**的 rollout,**不是录人**。这是本模式区别于屏幕录制的根本点。

---

## 2. 可行性与适用边界(诚实)

- **引擎层完全通用**:纯语义 DOM 驱动(role/label/text),框架无关(Element-UI / Ant / 原生 / 自定义控件一视同仁,不按 class 白名单)。**一套引擎接入任意公司任意表单型系统,不写每系统集成代码。**
- **Skill 层是租户+系统+页面作用域**:A 公司请假 skill 不迁移到 B 公司(页面不同),但 B 公司用同一流水线几分钟产出自己的。复用的是"造 skill 的机器",不是单个 skill。
- **"自动探索"必须目标导向**:给 URL + 业务目标 + 测试账号,成功率才高;纯无目标乱逛不可靠。"自动"指 Agent 自己找点击路径(HOW),不是猜该做什么业务(WHAT)。

| 站点类型 | 首跑可行度 | 归属 |
|---|---|---|
| 标准表单/工单/审批/报销(RuoYi 类后台) | 高 | 本模式主战场 |
| 多步向导、级联下拉、重异步刷新 | 中(首跑可能要几次,自愈兜) | 本模式 |
| Canvas/无语义 DOM、每步验证码、强反爬 | 低 | 退回屏幕录制 / 抓请求 |

**保证级**:产出的 skill **跑不通 / 数据回查验不过 → 诚实不产出,绝不吐垃圾 skill**。能产出的都高可用。

---

## 3. 相较录制模式的优势

1. **零人力造 skill**:录制要人逐条点;Agent 给目标自己跑,可批量接入多系统。
2. **自愈不重录(最大运维优势)**:页面改版录制要人重录,Agent 自主重驱动再结晶。
3. **接入期纠错**:人只录 happy path;Agent 读校验报错自己改,轨迹更鲁棒,还能发现级联字段。
4. **一致性**:同一套语义定位纪律,不因人而异。
5. **互补非替代**:复杂/古怪页面 Agent 可能卡住,**录制留作那 10% 硬骨头的兜底**。

---

## 4. 锁定决策

1. **Agent 大脑 = pi 子进程**:复用 `back/agent` sidecar,新增 4 个 proxyTool + 1 个 pi skill,不另起 agent 路径。
2. **首跑默认 dry**:只填不真提交 → 结晶后资产标 `partially_verified`;只有环境声明可逆 + 测试账号 + `page_write_probe=1` 才允许 Agent 真提交做 before/after 回查升 `verified`。

---

## 5. 总体架构(端到端)

```
首跑(慢,LLM 逐步推理活页面)
  page_session_open(goal, start_url)        # 起常驻浏览器会话(带测试登录态)→ 首观察
  loop until 目标达成 / 判定无法达成:
    page_observe()                          # 当前页是什么 + 页面反馈(校验错误/新字段/跳转/toast)
    LLM 据 goal+观察决定下一步
    page_act(op, locator, value, field)     # 活页面做一步;页面当场执行规则并回应;submit 默认 dry
  page_crystallize(action, ...)             # 成功轨迹 → 泛化具体值为参数 → PageScriptBody 草案
  sandbox_replay(dry) → request_review(三角色) → publish_asset(硬闸门)
        │
运行期(快,零 LLM)
  POST /v1/skills/{id}/invoke {字段} + 公司 key
  PageActionRuntime 起无头浏览器(租户登录态)→ 指纹校验 → 填参数 → 点击/选择
   → L3 提交前确认 → 真点提交(页面自己打它那十几个接口)→ 成功标志 + 回查 → 二态返回
        │
漂移/失败 → 回退 operate-page Agent 重驱动 → 重结晶新版本 → 灰度(复用 resilience/self_heal)
```

**承重铁律(继承)**:LLM 只提议(目标 / 下一步 / 受限修复操作 / 命名);`self_check` + `replay` + read-back diff 承重裁决。二态:跑通 / 跑不通,无模棱两可。

---

## 6. 新增文件

### 6.1 `dano/execution/page/live_session.py`(承载层,M1 核心)

进程内按 `run_id` 持有**常驻**浏览器会话,跨多次工具调用不关。当前唯一缺的承载件(`sessions.py` 只是登录态持久化,`pool.py` 是每次 new_driver 的池)。

```python
class LiveSession:
    driver: PageDriver                 # 池/launch 出的真驱动,或 FakePageDriver(测试)
    goal: str
    trajectory: list[RecordedStep]     # 每次 page_act 成功后追加(带 field 绑定)
    last_obs: dict | None              # 上次观察,用于 observe 的反馈 diff
    base_fingerprint: str              # 开场指纹(结晶时作 dom_fingerprint 基线)
    created_at: float; step_count: int

_SESSIONS: dict[str, LiveSession] = {}    # run_id -> 会话(与网关同进程同事件循环)

async def open_session(run_id, *, driver, goal, start_url) -> dict   # 起会话+首观察+登录墙检测
def get_session(run_id) -> LiveSession                               # 取;不存在抛 ToolError
async def close_session(run_id) -> None                              # 关驱动+清表(finally/超时/TTL)
def sweep_expired(ttl_s) -> None                                     # 后台清扫僵尸会话
```

要点:`page_session_open` 失败 / `page_crystallize` 完成 / TTL 到都 `close_session`;并发上限沿用 `browser_pool_size`。

### 6.2 `dano/execution/page/observe.py`(观察 + 反馈检测)

把"当前页面是什么 / 页面反馈了什么"做成确定性抽取:

```python
async def observe(driver, *, prev: dict | None) -> dict:
    # fields: 复用 scout_dom(label/role/name/placeholder/required)
    # buttons / url / fingerprint
    # feedback(与 prev 对比):
    #   errors:    可见校验错误文本(.el-form-item__error / [class*=error] / role=alert /
    #              含 必填|错误|失败|不能为空|请输入)
    #   new_fields: 本次新出现字段(级联下拉)
    #   url_changed: 多步向导跳转
    #   toast:     成功/失败提示
```

新增一段错误/toast 抽取 JS(语义选择器,绝不用坐标);`new_fields = 本次 fields − prev fields`。

### 6.3 `dano/execution/page/readback.py`(可观测回查,M2)

"数据是否改变"的二态断言,**不读接口**:

```python
async def snapshot_observable(driver, view: dict) -> dict
    # 导航验证视图(列表/详情),抓可观测签名:行数 / 可见记录键集合 / 目标记录在否
async def verify_readback(driver, *, before, expected, view, retries=5, backoff_s=1.5) -> tuple[bool, dict]
    # 轮询 after;断言"带我提交值的记录现在出现 且 计数按预期 +1";多接口异步靠轮询兜
```

dry 跳过 → `partially_verified`;live(可逆+测试账号)才真回查升 `verified`。M1 先留桩。

### 6.4 `agent/skills/operate-page.md`(pi 大脑,M1)

见 §11 全文。

---

## 7. 改动文件

### 7.1 `dano/agent_tools/tools.py`(加 4 工具 + 注册)

紧接现有页面工具段(1026–1140)后加 `page_session_open` / `page_observe` / `page_act` / `page_crystallize`。要点:

- `page_session_open`:`_launch_page_driver(mat)`(复用,带测试登录态)→ `open_session`。
- `page_observe`:`get_session` → `observe(driver, prev=s.last_obs)` → 更新 `s.last_obs`。
- `page_act`:
  - 超 `page_agent_max_steps` → ToolError(防无限循环烧钱)。
  - `op=submit` 且 `not page_write_probe` → **dry**:只 `driver.visible(submit)` 断言键在位,轨迹追加 submit 步,**不真发**。
  - 其余:`_apply_one(driver, op, loc, val)`(抽自 `runtime._do` 的分派);**成功才入轨迹**(失败尝试不污染结晶);带 `field` 的步 `value=None`(→参数),不带 field 的步留 `value`(→常量)。
- `page_crystallize`:取会话轨迹 → `model_dump` → **直接调 `draft_page_script`**(复用其确定性建体:字段对齐/必填拆分/L3 判定/指纹),`dom_fingerprint=s.base_fingerprint` → `finally: close_session`。

注册(1166–1170 段):

```python
    # 页面直驱(Agent 自主操作)
    "page_session_open": page_session_open,
    "page_observe": page_observe,
    "page_act": page_act,
    "page_crystallize": page_crystallize,
```

> **泛化分发**:`/_agent/tools/{name}` 已按 `TOOLS[name]` 分发(`agent_tools/app.py:49`),**进字典即自动暴露给 pi,无需动路由**。
> **结晶天然泛化**:`page_act` 记录的 `RecordedStep` 直接喂 `build_page_script`——带 `field` 的步→Skill 参数,带 `value` 的步→常量,具体值→参数的泛化在记录时已完成,不另写。

### 7.2 `agent/tools.mjs`(加 4 proxyTool)

`customTools` 末尾加(与现有同构):

```js
proxyTool({ name:"page_session_open", label:"打开页面会话",
  description:"打开常驻浏览器会话(测试登录态),返回首个页面观察(字段/按钮/指纹)。",
  parameters: Type.Object({ system_instance_id:Type.String(), start_url:Type.String(),
    goal:Type.Optional(Type.String()), headless:Type.Optional(Type.Boolean()) }) }),
proxyTool({ name:"page_observe", label:"观察页面",
  description:"看当前页面:候选字段/按钮/URL + 自上步以来的反馈(校验错误/新字段/跳转/toast)。",
  parameters: Type.Object({}) }),
proxyTool({ name:"page_act", label:"操作一步",
  description:"在活页面做一步语义操作并返回结果观察。op=fill/select/pick/upload/click/wait/goto/submit;"+
    "填业务字段带 field=<字段名>(→Skill 参数),填固定值不带 field(→常量);submit 默认 dry 不真提交。",
  parameters: Type.Object({ op:Type.String(), locator:Type.Optional(Type.String()),
    value:Type.Optional(Type.String()), field:Type.Optional(Type.String()) }) }),
proxyTool({ name:"page_crystallize", label:"固化为页面Skill",
  description:"把本次成功操作轨迹固化成参数化页面脚本草案,返回 asset_draft_id(再 sandbox_replay→评审→发布)。",
  parameters: Type.Object({ system_instance_id:Type.String(), action:Type.String(),
    title:Type.Optional(Type.String()), start_url:Type.Optional(Type.String()),
    success_marker:Type.Optional(Type.String()) }) }),
```

### 7.3 `dano/config.py`(2 个护栏)

```python
page_agent_max_steps: int = 40       # Agent 回路单次最大步数(防无限循环)
page_session_ttl_s: int = 900        # 活会话 TTL(防僵尸浏览器)
# page_write_probe 已存在 —— 继续作"是否允许真提交"的唯一总闸(默认 False=dry)
```

### 7.4 `dano/gateway/app.py`(对外入口)

加 `POST /onboarding/page-agent`:`{tenant, subsystem, start_url, goal, credentials(测试), headless}` → `materials.register` → 复用现有 pi 子进程拉起机制(`run_pi.mjs`,JSONL `start_run`),prompt=goal,pi 自动选 `operate-page` skill → 跑回路→结晶→发布 → 经 `progress.py` 流式回前端。与 `/onboarding/real` 同构。

### 7.5 `dano/orchestrator/orchestrator.py` — `list_field_options`(Q4 字段来源接口/枚举,M2)

现有实现(543 行)只认抓请求型(`page_asset_id` + `fetch_field_options` 调源接口)。**加页面脚本分支**:页面型 skill → 起浏览器 → 打开 start_url → 定位该字段下拉 → **从活 DOM 抓当前选项**(复用 `scout` 的选项抽取)→ 返回 `{field, options:[{label,value}], count}`。复用现有 `/v1/tools/options` 端点(`app.py:981`)与 manifest 的 `x-options`/`x-options-source`/`enum`(≤50 内联)机制,**不新建端点**。

### 7.6 `agent/run_pi.mjs` / `onboarding/service.py`

基本不动:skill 从 `skills/` 自动加载;service 若有"按场景选 skill"开关,加 `page_agent` 分支即可。

---

## 8. 运行期调用模型(前端如何调,已现成)

```
结晶时:7 个业务字段 → PageScriptBody.user_fields=[f1..f7],每步 value_from='field:fN'
前端:  GET  /v1/skills/{id}        → manifest(参数 + 必填/类型/枚举 x-options)
        POST /v1/tools/options {skill,field}  → 选择型字段的实时选项(读活 DOM)
        POST /v1/skills/{id}/invoke {f1..f7} + X-Tenant-Key
后端:  PageActionRuntime 起无头浏览器(租户登录态)→ 打开 start_url → 指纹校验
        → 逐字段填参数(value_from='field:fN' ← params[fN])→ 录好的点击/选择
        → L3 提交前确认卡 → 真点提交:真实页面跑起来,触发它自己的后端接口,真建单
        → 成功标志 + 回查(M2)→ 二态返回 + 截图证据
```

**关键**:skill **不持目标系统接口凭证、不直接调其接口**,而是以租户**登录态**在真实页面操作,页面自己干活。一次 invoke = 在真页面跑完整条业务。

**运行期前提(诚实)**:需该系统有效登录态(`storageState`/token,存 `credential_ref`,见 `sessions.py`);过期要刷(token_store 运行期覆盖)。这是页面模式的真实运维依赖。

---

## 9. 六个关键技术问题的处理

### Q4 字段来源是接口(下拉选项来自后端)
**运行期不调那个接口,让页面自己去调。** `pick`/`select` 打开下拉 → 页面用它自己的接口拉选项 → 我们按可见文本选中参数对应项(`driver.pick`:点触发框→等弹层→精确文本点选,点不到再输入过滤+回车)。**数据源是页面,我们只负责选哪个。** 前端要提前知道可选项 → §7.5 实时选项(读活 DOM)。

### Q5 页面提交后触发多个接口
**不编排、不处理。** 只点一次"提交",页面自己的 JS 按它的顺序/串联(taskId、request_id、草稿→提交)把那十几个接口全打完——**页面就是编排器**。我们只做:(a) 等多接口落定(networkidle / 成功标志 / loading 消失);(b) 用回查确认业务结果(§6.3),**不解析那 N 个响应**。诚实边界:无任何可观测结果界面可回查 → 停在 `partially_verified`,不谎报。

### Q6 枚举
结晶时该步是 `pick`/`select` + `field` → 标 `field_types[X]='enum'`;选项快照进 manifest `x-options`(≤50 给 `enum`),实时以 §7.5 为准。运行期前端传业务显示值(如"事假"),活页面按文本选中。**最大优势:不用处理 name↔id 配对**——UI 层按显示名选,页面内部自己转 id 提交,我们永不碰那个 id(抓请求模式为此改了一长串,这里问题不存在)。

### 级联下拉
轨迹按序录"选父项 → wait → 选子项";父项变页面自己加载子项选项,靠录好的 `wait`/`assert_visible` 兜时序。运行期照序回放。

### 登录态
运行期与接入期共用 `_launch_page_driver`(base_url + 测试/租户 storageState/token)。过期 → 运行期 token_store 覆盖 / 重录会话。

### 漂移自愈(M3)
回放前指纹校验;漂移 → 不盲跑 → 回退 `operate-page` Agent 重驱动活页面 → 重结晶新版本 → 灰度发布(旧版留到新版通过),复用 `resilience/self_heal`。

---

## 10. pi skill 全文(`agent/skills/operate-page.md`)

```markdown
---
name: operate-page
description: 给一个页面 URL + 业务目标,自主操作真实页面达成目标,把成功轨迹固化为可发布的页面 Skill。只用测试账号,默认不真提交。
---
你是 Dano 的页面操作智能体。给定目标与入口页,**亲自操作真实页面**达成它,再把成功路径固化。
定位只用语义(role/label/placeholder/text),绝不用坐标。

## 回路
1. page_session_open(system_instance_id, start_url, goal) → 拿首个 observation(当前页/字段/按钮/指纹)。
2. 循环(每轮一步,直到目标达成或判定无法达成):
   - 看 observation:当前页是什么?要达成 goal 还缺什么?feedback 有无校验错误/新字段/跳转?
   - page_act(op, locator, value, field) 做**一步**。填业务字段必须带 field=<业务字段名>(将成为 Skill 参数);
     填固定值不带 field(将固化为常量)。
   - 读返回的新 observation:feedback.errors 非空 → 据报错改这一步(改值/换控件),别硬冲。
   - 级联:选完一项后 feedback.new_fields 有新字段,继续填。
   - 提交:op=submit。**默认 dry**(系统只断言提交键在位、不真发),正常,别因"没真提交"判失败。
3. 达成后 page_crystallize(action, title, success_marker) → 拿 asset_draft_id。
4. sandbox_replay(asset_draft_id, sample_inputs) → 回放草案。
5. 写页面 → request_review(asset_draft_id) 三模型评审(成果验收/漏洞检测/合规)。
6. 通过 → publish_asset(asset_draft_id, validation_run_ids, review_run_ids)。一句话汇报。

## 红线
- 只用测试账号。默认 dry,绝不在生产页真提交。
- DOM"提交成功"≠业务成功:写页面默认 L3、保证级 weak,别声称"已确认生效"。
- 不自报通过:发布只能附 sandbox_replay/request_review 返回的 ids(后端重读校验)。
- 指纹是漂移基线,用系统给的别自己编。达不成就如实说卡在哪,别瞎填硬交。
```

---

## 11. 端到端数据流示例(请假单)

```
前端 POST /onboarding/page-agent {start_url:OA请假页, goal:"提交一张请假单", creds:测试token}
 → 注册材料 → spawn pi(operate-page)
 → page_session_open → observe:{fields:[请假类型,开始,结束,事由], submit 按钮, fp}
 → page_act(pick, label=请假类型, 事假, field=leaveType) → feedback 干净 ✓
 → page_act(pick, 开始日期, 2026-07-01, field=startTime) ...(结束/事由同理)
 → page_act(submit) → dry:submit_visible=true(不真发)
 → page_crystallize(action=submit_leave, success_marker=text=提交成功)
     → 轨迹→build_page_script→PageScriptBody(leaveType/startTime/...=参数)→asset_draft_id
 → sandbox_replay(dry) → request_review(3 角色) → publish_asset
 → 资产 published(partially_verified)→ 登记 SkillLifecycle
前端凭 key:GET /v1/skills 见 submit_leave;invoke {leaveType,startTime,...} 真实提交
```

填错触发页面校验 → observe.feedback.errors 非空 → LLM 自己改这步。**页面就是规则执行者。**

---

## 12. 测试矩阵(M1,FakePageDriver 离线,新增 `tests/test_page_agent.py`)

1. **会话持久**:open→act→act→crystallize 中途 `get_session` 同一实例;crystallize 后会话回收。
2. **感知-纠错回路**:fake 注入"首次提交报必填错"→ observe.feedback.errors 非空 → 再 act 补字段 → 通过。
3. **轨迹→结晶**:3 个带 field 的 fill + 1 submit → 结晶出的 `PageScriptBody` 的 `user_fields` 恰为那 3 字段、含 submit、L3。
   注:`build_page_script`/`assign_field_keys` 会把字段对齐到标准词典 key(如 `startTime`→`start_time`),断言用归一后的 key。
4. **dry 闸**:`page_write_probe=False` 时 submit 不真发(driver.ops 无真 submit,只 visible 断言)。
5. **回放闭环**:结晶草案经 `PageActionRuntime`(FakePageDriver)回放 PASSED。
6. **护栏**:超 `page_agent_max_steps` → ToolError;TTL 清扫僵尸会话。

---

## 13. 里程碑与验收

| M | 内容 | 产出 | 依赖 |
|---|---|---|---|
| **M1** | live_session + observe + 4 工具 + operate-page.md + 离线测 | Agent 自主跑通→结晶→dry 发布 | FakePageDriver,无需真浏览器 |
| **M2** ✅核心已落地 | readback 可观测回查(二态)+ list_field_options 页面分支 + /onboarding/page-agent 真链路 | 测试环境真提交→回查→verified;前端实时选项 | 真 Playwright + 测试账号 |
| **M3** ✅核心已落地 | 漂移自愈:回放失败/指纹漂移→回退 operate-page 重驱动→重结晶→灰度 | 页面改版自动修 | 复用 assurance/self_heal |
| **M4** ✅核心已落地 | 前端"页面直驱"向导(URL+目标+测试账号,流式看操作)+ 异步任务进度 | 端到端可视化 | skillfrontend + _spawn_pi progress |

**验收**:
- **M1**:`pytest tests/test_page_agent.py` 全绿;Agent 在 FakePageDriver 上经"报错→纠正→提交(dry)→结晶"产出可回放 `PageScriptBody`,无真提交、无僵尸会话。
- **M2**:真实测试页 + 测试账号,Agent 自主跑通、真提交、回查到新记录 → 资产 `verified`;跑不通/验不过 → 诚实不产出。
- **前端可调**:产出 skill 经 `/v1/skills/{id}/invoke` 凭公司 key 调用成功(已具备,零改动)。

---

## 14. 风险与诚实边界

1. **首跑成功率随站点复杂度下降**:多步/级联/重异步首跑可能要重试;Canvas/反爬基本不行 → 退回录制/抓请求。
2. **运行期依赖登录态**:过期需刷;无有效会话则 invoke 在登录墙失败(已有 `login_wall` 检测,清晰报错不瞎填)。
3. **强异步无回查面**:只能 `partially_verified`,不谎报"已确认生效"。
4. **LLM 成本**:仅首跑/自愈用 LLM;运行期零 LLM。`page_agent_max_steps` 护栏防烧钱。
5. **不碰生产写**:接入期只测试账号、默认 dry;真提交须 `page_write_probe=1` 显式授权 + 可逆环境。

---

## 15. 实现顺序(M1)

1. `dano/execution/page/live_session.py` + `observe.py`
2. `dano/agent_tools/tools.py` 四工具 + `TOOLS` 注册
3. `agent/tools.mjs` 四 proxyTool + `agent/skills/operate-page.md`
4. `dano/config.py` 两护栏
5. `tests/test_page_agent.py` 离线跑绿
6. `dano/gateway/app.py` `POST /onboarding/page-agent`(收尾接通真链路)

全程不碰真浏览器、不动现有路径,可随时回退。
