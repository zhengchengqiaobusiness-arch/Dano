# Codex 任务书：消费者 Skill 产出优化

仓库：`E:\python\try\Dano`  
对照：`Pi_check/RESPONSIBILITIES.md`、`Pi_check/FOUR_SKILL_EXECUTION_PLAN.md`、`doc/skill-generator-ask-user-question-guide.md`  
对照样例：https://skills.aliyun.com/skills?orderBy=install （阿里云 skill 是完整业务生命周期，不是孤立功能点）

本文是一次性工单。未读完第 0–4 节之前，不要改代码，也不要改 `renderer.py` / `planner.py` 去「补业务」。

---

## 0. 听明白了没有

听明白了。

- **消费者 Skill 由 Skill 4 写出来**，不是 Python 模板猜出来的。
- 代码是冻住的运输层：复制草稿、注入冻结客户端、跑指定检查、注入/回写鉴权文件。
- 三条产品缺口（能直调、有流程、遵守前端提问合同）都改 **Skill 4 + `doc/` 生成规范**。只有 Skill 4 物理上做不到的事才加运输。
- 基础数据来自 Infer 交的**整份合同信封**，不是只来自 `capabilities[]`。
- 以后换页、handbook 不好、流程串不起来、表单不像前端合同：默认只改四份 PI Skill 之一。diff 里出现 `renderer.py` / `planner.py` / `result-gate` / `computed.py` 认业务 = 本方案作废。

---

## 1. 当前事实（不要再争论）

### 1.1 谁在出包

```text
Investigator（Skill 1）
  → Infer 交合同（Skill 3）
  → Build 用 write_skill_artifact 写草稿（Skill 4）
      Pi_check/data/<recording_id>/skill-artifacts/
  → export 复制草稿 + 注入冻结 client.py / wire_format.py
```

- 没有 Skill 4 的 `SKILL.md`：导出 409，「没有 Skill 4 产物，拒绝猜编译」。
- `pack_skill4_artifacts` 会覆盖 `scripts/client.py` 和 `scripts/wire_format.py`。Skill 4 已写明「不要重写这两份，导出注入冻结副本」。
- `generate_skill_plan` / `renderer.py` 仍存在。规划器只给资产元数据用；**成品 handbook / 能力脚本 / 路线文件以 Skill 4 草稿为准**。禁止把规划器重新变成出包引擎。
- Skills 页「再导出」若仍走 `render_skill_package` 重生成，属于旧线。本任务优先锁录制主路径；旧线只允许改成「原样再拷 Skill 4 产物 + 回写鉴权」，禁止用 renderer 再猜一包。

### 1.2 基础数据从哪来

Skill 4 **唯一输入**是 Infer 已提交的合同信封（草稿 / `pi-draft.json` / `submit_recording_result(use_draft=true)`）：

| 字段 | 作用 | 是不是「能力」 |
| --- | --- | --- |
| `capabilities[]` | 动作、kind、调用方 `input_schema`、`request_refs` | 是 |
| `steps[]` | method / path / params；系统字段和 option_source 在这里 | **否。能力只通过 step_id 引用** |
| `links[]` | 上一步响应 → 下一步请求 | **否。跨步传值** |
| `capability_relations[]` | 先 A 后 B（有或没有传值） | **否。编排** |
| `unresolved[]` | 没认清、不能当可执行默认 | **否。否决名单** |

能力离开对应 step 执行不起来。页面「调用方 / 系统 / 编排」也按这五块画，不读 `capabilities[].fields`。

导出运输层**另外**注入（不是认业务）：

- `tenant` / `subsystem` / 导出目录
- 冻结 `client.py`、`wire_format.py`
- 运行时鉴权通道（今天是环境变量 / Dano token API / 会话缓存）
- `hydrate_recorded_write_bodies` 可能从本场证据补写入请求体、step URL、`identity_probes`

禁止把本场人员、单号、样例值冻进成品。禁止 Skill 4 回头打开页面猜字段。禁止读 `spec.md`、阶段号、FlowSpec、`generator-guides`。

### 1.3 现在 Skill 4 自己写成了什么

`Pi_check/skill/BUILD_AND_VALIDATE_DEDICATED_SKILL.md` 当前指令：

- 不准写 token / cookie / password
- 每个能力一个命令脚本，没有整段流程执行器
- 不读 `doc/`
- 组合只三种：原子 / 已确认绑定 / 人手交接；没有「本页默认完整办理」
- `INPUT_FORMS` 只收集调用方字段（这点保留）

所以产出「散、不能直调、页面改 token 不管包」，**首先是 Skill 4 指令如此**，不是导出器又回去猜业务。

---

## 2. 要解决的三个产品问题

### 问题 A：产出的 skill 要带 token，能直接调用；页面能改；字段来自接口时该接口也能打

现状：

- Skills 页 `TokenModal` 只把鉴权头写入 Postgres `runtime_token`
- 冻结 `client.py` 不读包内鉴权文件；离开 Dano 进程后经常不能直调
- `pack_skill4_artifacts` 写入的 CONFIG `base_url` 是空串
- 动态选项被写成「先 `--list-options`，再从 question 里删掉 `dataSource`」，页面无法带凭证刷新下拉

目标：

- 包内有本地鉴权配置，脚本默认用它就能打业务接口和选项接口
- 页面改 token 后，已导出包立刻用新凭证
- `dataSource` 留在表单合同里，页面和脚本都能打同一个选项接口
- **token 不准出现在 SKILL.md / CONTRACT.json / 对话 / 日志**

### 问题 B：产出的 skill 要有流程性，能串起来一起执行

对照阿里云 DataHub 类 skill：一个 skill = 一个业务对象的生命周期（查 → 建 → 改 → 订阅读 → 删），命令可单步用，默认是整段流程。

现状：

- Infer 把动作切成可单独再发起的能力（这层切分保留，不要合并能力）
- Skill 4 按能力各写一个脚本；没有 `scripts/flow.py`
- 没有「本页默认办理路线」；关系缺失时等于一堆孤立功能点

目标：

- 一个消费者 Skill = 本页/本业务对象的办理流程
- 默认路线按合同里的 `links` + `capability_relations` + 能力在合同中的顺序串起来
- 提供 `python scripts/flow.py --route <id>` 一次跑完
- 用户只要单步时才走原子路线
- 没有已确认绑定就在交接点停问，**不要因此拆成互不相关的 skill，也不要猜传值**

### 问题 C：不要忘前端提问合同；出包前读完 `doc/`

`E:\python\try\Dano\doc` 是生成期规范目录（`.env` 里 `DANO_SKILL_REFERENCE_DIR=doc`）。

已有：

- `doc/skill-generator-ask-user-question-guide.md`  
  原生 `ask_user_question`、三种调用形状、全部控件、`dataSource`、确认/取消、产出合同第 1–8 节。  
  **这些行为必须继续投影进成品，禁止回退。**

欠缺（本任务要补进 `doc/`）：

- `doc/skill-generator-auth-and-token.md`
- `doc/skill-generator-workflow.md`
- `doc/skill-generator-live-options.md`

规则：

- **生成期**必须读完 `doc/` 下全部 `.md`
- **成品禁止**携带 `references/generator-guides/`，禁止要求执行前先读生成规范
- `agent_skills.py` 若仍把 `doc/` 拷进成品，删掉这段拷贝

---

## 3. 职责铁律（Codex 必须遵守）

| 现象 | 只改 |
| --- | --- |
| handbook 触发差、没有默认流程、表单丢 dataSource、执行器与合同不一致、不该发布却发布 | **Skill 4** |
| 页内典型链没标 handoff / binding，只有散点能力 | **Skill 3**（只补关系信封，不写消费者包） |
| 过早定稿、没等 Skill 4 验证就提交 | **Skill 1** |
| 点不到、选项接口没取到证据 | **Skill 2** |
| PI 读不到 `doc/`；client 读不到包内鉴权；打包不注入 token 文件；页面改 token 不回写包；校验器误杀本地鉴权文件 | **运输代码**（第 7 节白名单） |

禁止：

- 用 `planner.py` 发明路线再让 renderer 写成 SKILL.md
- 在 `renderer.py` 里重写 handbook / 表单 / 流程文案
- 把未确认绑定猜成自动传值
- 把 `doc/` 打进消费者包
- 把 JWT / cookie / password 写进 SKILL.md、CONTRACT.json、CAPABILITIES.md、对话
- 改闸门、采集、`computed.py`、字段来源推断
- 为了能跑把 `unresolved` 冻成录制常量

---

## 4. 终态：一个可直调的流程包

```text
<skill>/
  SKILL.md
  config/
    runtime.json                 # tenant / subsystem / base_url（可提交，无密钥）
    auth.local.json              # 鉴权头；导出注入；页面可改；不进手册
  scripts/
    client.py                    # 冻结运输；优先读 auth.local.json
    flow.py                      # Skill 4 写；按 route 串跑
    <capability>.py              # Skill 4 写；单步仍可调
    verify_<capability>.py       # 仅写操作需要回读时
    format_list.py
    wire_format.py               # 冻结运输
  references/
    CONTRACT.json                # 公开操作 + routes + 每字段 dataSource
    CAPABILITIES.md
    OPTIONS.md
    INPUT_FORMS.md               # 保留完整 dataSource，禁止「先删掉再提问」
    routes/<route-id>.md
```

直调（不依赖 Dano 进程）：

```text
python scripts/client.py --show-config
python scripts/<capability>.py --list-options <字段>
python scripts/flow.py --route <默认路线> --input-json '{...}' --confirm
```

`SKILL.md` 的「选择工作流」第一行必须是本页完整办理；原子行只服务「只要查一下 / 只要删这条」。

---

## 5. 分步工单

按顺序做。每步做完先跑该步验收，再下一步。

### 第 1 步 · 补 `doc/` 生成规范（先做，给 Skill 4 读）

新建三份，全部中文说明 + 保留实现字段的英文原名。不要把 Codex 任务书自己放进 `doc/`。

**`doc/skill-generator-auth-and-token.md`**

- 包内必须有 `config/auth.local.json`，形状：`{"headers": {"Authorization": "Bearer ...", "...": "..."}}`
- `config/runtime.json`：`tenant`、`subsystem`、`base_url`（从合同 execute step 的绝对 origin 取，取不到留空并在 SKILL.md 鉴权节说明）
- client 读序：`auth.local.json` → `DANO_AUTH_HEADERS` → Dano `/v1/settings/token/raw` → 会话缓存
- 页面 TokenModal 保存后必须回写该 subsystem 已导出包的 `auth.local.json`
- 手册、CONTRACT、对话、日志禁止明文 token
- 选项接口和业务接口走同一套鉴权

**`doc/skill-generator-workflow.md`**

- 一个消费者 Skill = 本页办理流程，不是能力目录
- 默认路线：按合同 `capability_relations` 与 `links` 连成最长链；没有关系时按合同 `capabilities[]` 顺序作为人手交接主路线，仍写出 `flow.py` 能跑的 route
- 原子路线保留
- `scripts/flow.py`：`--route`、`--input-json`、写步骤 `--confirm`、交接点停问、失败即停
- 对照阿里云：可单步，默认同跑
- 禁止成品出现「每次只执行一项」「不得自行串联」「一页面对应一个 Skill」

**`doc/skill-generator-live-options.md`**

- 合同里有 option_source 的调用方字段，`INPUT_FORMS.md` **必须保留**完整 `dataSource`（endpoint/method/params/resultPath/idField/labelField/…）
- 禁止「把 options 填进 question 后删除 dataSource」
- Agent 可用 `--list-options`（同一鉴权）预取
- 页面可用 `dataSource` + 当前 token / Dano 代理刷新
- 选项接口打失败：停问，不得默默取第一条、不得用录制样本冒充实时选项

**`doc/skill-generator-ask-user-question-guide.md`**

- 不改已有控件合同、schema、示例
- 只在文首或第 8 节加交叉引用：出包前还须同时遵守另外三份
- 第 8 节自包含清单补上：`config/auth.local.json`、`scripts/flow.py`

### 第 2 步 · 运输：Skill 4 必须能读到全部 `doc/`

现状：PI 只加载四个 Skill 文件，没有读仓库 `doc/` 的工具。这是「Skill 调用却物理上做不到」，允许加运输。

做一件，不要两件都做造成重复灌上下文：

- 新工具 `read_generator_guides`：递归读取 `DANO_SKILL_REFERENCE_DIR`（默认仓库 `doc/`）下全部 `.md`，返回 `{files:[{path, content}]}`。目录不存在或没有任何 md → 明确失败。
- `buildPiInstructions` 的工具清单加上该工具。
- **不要**把 `doc/` 全文永远焊进系统提示词（上下文成本）。Skill 4 写包前调用一次即可。
- 不要把 `doc/` 拷进 `skill-artifacts/references/generator-guides/`。`validate_skill_package` 继续拒绝成品里的该目录。
- `agent_skills.py` 删除 `_write_generation_guides` 这类往成品拷规范的逻辑；生成期仍可读 `doc/` 做校验。

### 第 3 步 · 改 Skill 4（本任务主战场）

只改 `Pi_check/skill/BUILD_AND_VALIDATE_DEDICATED_SKILL.md`。

必须改成：

1. **写包前**：调用 `read_generator_guides`，读完返回的全部文件，按它们约束成品。缺目录或缺第 1 步三份新规范 + 提问指南 → 停止，退回 Investigator，不要发布。
2. **唯一输入仍是合同五块**：`capabilities` / `steps` / `links` / `capability_relations` / `unresolved`。写执行器时必须同时用 step 的 path/params/option_source、link 传值、relation 顺序。禁止只扫 `capabilities[]` 就出包。
3. **鉴权**：写出 `config/runtime.json` 布局和 `config/auth.local.json` 的占位说明。 **不要把真实 token 写进对话或 handbook。** 真实头由打包运输注入（第 4 步）。Skill 4 必须声明 client 读序，并在「鉴权」节告诉执行者：没有 `auth.local.json` 且没有环境凭证则停止。把「不准写 token」改成「不准写进手册；包内必须有本地鉴权文件槽位」。
4. **流程**：
   - 用 relation + links 编默认办理路线；没有值流的 relation 也是 handoff 主路线，不是散点。
   - 写出 `scripts/flow.py`，能按 `CONTRACT.json.routes[]` 执行。
   - `SKILL.md`「选择工作流」第一行 = 默认完整路线。
   - 每个能力脚本仍保留，供单步。
5. **表单**：`INPUT_FORMS.md` 继续遵守提问指南；动态字段保留 `dataSource`；写操作仍要 `confirm + formIds`。
6. **验证**：投影、两套变化输入、隔离 `--help` / dry-run / `flow.py --help`、`validate_skill_package`。失败不发布。
7. 仍禁止回头猜页面、冻录制值、抄阿里 RAM/CLI 专章、把 `doc/` 打进成品。

### 第 4 步 · 运输：鉴权注入、client 读取、页面回写

只改这些，且只做运输：

**`pack_skill4_artifacts`（`back/dano/onboarding/skill_generation/export.py`）**

- 复制 Skill 4 草稿后：
  - 注入冻结 `client.py` / `wire_format.py`（保持）
  - 从合同 execute step 填 `config/runtime.json` 的 `base_url`（不要再写空串）
  - 从 `runtime_token`（tenant + subsystem）写入 `config/auth.local.json`；没有 token 则写 `{"headers":{}}` 并在打包日志记缺口，**不要发明 token**
- 不要重写 Skill 4 的 `SKILL.md`、能力脚本、`flow.py`、`INPUT_FORMS.md`

**冻结 `client.py` 模板（`renderer.py` 里 `_CLIENT_TEMPLATE`，因为打包注入的是它）**

- `auth_headers()` 最前面读包内 `config/auth.local.json`
- 然后才是 `DANO_AUTH_HEADERS` / Dano token API / 会话缓存
- `--show-config` 只打印 tenant/subsystem/base_url/是否已有鉴权头，**不打印头的值**

**校验器**

- 允许且仅允许 `config/auth.local.json` 出现凭证形态
- 其它文件仍报 `credential_leak`
- 新包缺少 `scripts/flow.py` 或缺少默认多步 route（合同里有 ≥2 个能力时）→ error
- 动态字段的 INPUT_FORMS 若写成「删除 dataSource」→ error

**前端 TokenModal**

- 保存成功后：按当前导出目录扫描同 `subsystem` 的已导出包，更新它们的 `config/auth.local.json`
- 只更新鉴权文件，不动 handbook
- 没有已导出包时只写 PG（保持），不要报成失败

**Skills 页再导出**

- 若该 skill 来自录制且磁盘上仍有对应 Skill 4 产物：只允许 pack + 回写鉴权
- 禁止 `render_skill_package` 按 FlowSpec 再猜一包覆盖 Skill 4 草稿

### 第 5 步 · Skill 3 只补编排信封（若默认路线编不出来）

只改 `Pi_check/skill/INFER_BUSINESS_CONTRACT.md`，且只加这一条：

- 目标要求先 A 后 B：有值流写 `links`；没有值流也必须写 `capability_relations`（handoff）
- 页内典型链（查询→新增、查询→编辑、查询→删除、新增→提交）只要本场都做完，必须挂 relation
- 禁止单独交没有 `request_refs` 的「关系能力」
- 不要改 source_kind 规则，不要写消费者 SKILL.md

Skill 1 只补一句：定稿前确认合同里的页内顺序已经交给 Infer；Skill 4 没有默认路线时按 Skill 4 失败分诊，不要自己写包。

### 第 6 步 · 回归锁死

至少加/改这些测试（名称可自定，行为必须锁）：

1. 无 Skill 4 草稿 → 导出仍 409
2. 有草稿 → 成品 SKILL.md / `flow.py` / 能力脚本与草稿一致，不被 renderer 改写
3. 打包后不设 `DANO_URL`，只放 `auth.local.json`，`--list-options` 与 dry-run 走这组头（可用 mock HTTP）
4. TokenModal 保存后，导出包内 `auth.local.json` 更新
5. 成品没有 `references/generator-guides/`
6. `INPUT_FORMS.md` 对动态字段保留 `dataSource`
7. 合同有查询+新增时，`CONTRACT.json.routes` 含一条多步默认路线，且 `flow.py --help` 能列出它
8. handbook / CONTRACT 不含明文 Bearer/JWT
9. 现有 `ask_user_question` 形状、确认、取消回归不回退
10. `read_generator_guides` 能读到 `doc/` 下全部 md；目录空则失败

---

## 6. 建议实现顺序

1. `doc/` 三份新规范 + 提问指南交叉引用  
2. `read_generator_guides` 工具  
3. Skill 4 全文按第 3 步改  
4. 打包注入鉴权 / client 读本地文件 / TokenModal 回写 / 校验器  
5. Skill 3 编排补句（仅当第 4 步后默认路线仍编不出来）  
6. 回归  

鉴权和流程不要对调：`flow.py` 依赖同一套鉴权去打业务接口和选项接口。

---

## 7. 验收（全部勾上才算做完）

- [ ] 录制主路径：Skill 4 写包，export 只复制 + 注入 client/wire_format/鉴权文件  
- [ ] 离开 Dano 进程，仅靠包内 `auth.local.json` 能打业务请求和 `--list-options`  
- [ ] 页面改 token 后，已导出包不用重录就能用新凭证  
- [ ] 字段来自接口时，`dataSource` 仍在 INPUT_FORMS，且该接口用同一鉴权可调用  
- [ ] 有 ≥2 个已验证能力的包，存在默认可执行办理路线，`flow.py` 能按序跑（绑定自动带；无绑定停问）  
- [ ] 用户只要单步时仍可走原子脚本  
- [ ] Skill 4 写包前读完 `doc/`；成品不带 generator-guides  
- [ ] 提问指南的三种形状、控件、确认、取消没有回退  
- [ ] 合同五块都被用到；不是只扫 capabilities  
- [ ] 本场样例值、人员、单号不进成品默认  
- [ ] 没有把规划器/renderer 重新变成出包引擎  
- [ ] `Pi_check/skill/` 仍恰好四份业务 Skill；本任务书不进 `doc/`、不进消费者包  

---

## 8. 给 Codex 的开工句

从第 1 步开始做。先补 `doc/` 三份规范，再加 `read_generator_guides`，再改 Skill 4。不要先改 `planner.py` 或 `renderer.py` 的 handbook 渲染。每一步用第 6 节对应回归证明，再进入下一步。
