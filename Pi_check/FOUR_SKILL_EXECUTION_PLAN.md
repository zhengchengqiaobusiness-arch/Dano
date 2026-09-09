# 四 Skill 稳固代码：完整执行方案

对照任务书：`c:\Users\19419\Desktop\从零重写业务 Skill 生成项目：Codex 完整执行任务书.md`  
对照仓库：`E:\python\try\Dano`（核心 `Pi_check/`，导出 `back/dano/export/`，接入 `back/dano/onboarding/`）  
本文是一次性改代码并冻住的工单。做完之后，除非运输坏了，否则只改四个 Skill 文件。

未接到「按第 N 步开工」之前，不要大规模改代码。按第 7 节逐步勾选。

---

## 0. 听明白了没有

听明白了。

- **不是**这次不许改代码。  
- **是**这次把代码一次改够、改成冻住的运输层。  
- **以后**换页、认错字段、handbook 不好、不该发布：默认只改 Skill。  
- 四个角色必须落成四个 PI Skill 文件。Investigator 是唯一入口。Infer 不写字段规则到代码里。Build 从 Python 猜包改成 Skill 写包。  
- 任务书 §9 §10 §11 只写进 Infer，不准用闸门正则、导出猜测、采集业务词当识别器。

上一份把「半成品空窗」和「靠猜撑起来的导出成功率」写成终态下降，说反了。原因见第 1 节。

---

## 1. 为什么会出现「有提升也有下降」那种说法

那不是任务书做完之后应有的终态，是两件事被混在一起。

### 1.1 把假完整当成了要保住的能力

当前能较快导出、请求键看起来齐全，很多不是 Infer 认清了，而是：

| 当前位置 | 它在干什么 |
| --- | --- |
| `RECORDING_CAPABILITY.md` 第 157、205、499 行附近 | 明文允许「无独立来源 → `constant` + 录制原值 → 已解决」 |
| `Pi_check/src/result-gate.mjs` | `SELECTABLE_HINT_RE`、`ROW_ARRAY_HINT_RE`、`PAGINATION_KEY_RE`、`SYSTEM_ROW_KEYS`、分区 title、选项 links：用中文/键名教 PI「这是树/可增行/分页」 |
| `Pi_check/src/pi-session.mjs` 的 `buildLiveDrivePrompt` / `buildFinalAnalysisPrompt` | 把树、分区、`x-dano-section-titles`、调用方规则再抄进 JS |
| `back/dano/export/skill_package/renderer.py` `_inferred_system_values` | 叶子名猜 `now_iso` / `now_ms` / `now_date` |
| `back/dano/execution/page/flow_materialization/field_contracts/computed.py` | 按字段名反推公式 |
| `Pi_check/src/visible-controls.mjs` / `browser-capture.mjs` | 自动点「展开/高级搜索」、业务词「选择用户」、`exerciseListPage` 代点搜索/查看 |

任务书 §9 禁止「来源没识别出来 → system + literal:录制值 → 标记已解决」。  
拿掉这些之后，「导出就能打满请求」的数字会变严。那不是运输变弱，是不再用代码伪造完整。上次把它写成产品下降，说反了。

### 1.2 把改到一半的中间态当成了终态

若只拆 4 个 md、闸门先瘦，而图像/投影/隔离运行还没做，会出现：Skill 写了「去看图 / 读前端 / 验合同」，工具做不到。那是半成品空窗，禁止停在这一步。

当前代码还在**主动剥图**：`pi-tools.mjs` 的 `stripImageFromToolResult` / `compactInspect` 把截图变成 `image_in_conversation: false`；`read_screenshot` 只回元数据。任务书 §3.1 要求截图以 Pi 图像消息进模型。这必须在本次一次补齐，不能写成「以后再加所以会下降」。

### 1.3 改完全部内容之后真正该有的变化

| 变化 | 是不是问题 |
| --- | --- |
| 换页只改 Skill | 目的 |
| 未识别必须 `unresolved`；不完整写不能发布 | 任务书，不是事故 |
| 调查按目标做完再交，比空表换请求慢 | 正确节奏 |
| 双通道、协助、原样信封、按所请点填选 | 必须保持 |
| 半成品期又不能猜又不能验 | 禁止停在这一步 |

---

## 2. 终态铁律

```text
用户目标 + 入口 URL
        ↓
PI = Business Skill Investigator（唯一入口，不写字段规则）
├── Control In App Browser          只操作、只取证
├── Infer Business Contract         只解释证据、写合同
└── Build and Validate Dedicated Skill  只生成专用包并验证
        ↓
代码（已冻住）：执行动作 / 存证据 / 跑指定检查 / 原样打包
        ↓
专用 Skill 包（SKILL.md + 表单 + Python 执行器）
```

硬规则：

1. PI 必须加载且只加载下面 4 个文件，缺一不准开录。  
2. 系统提示词只宣布「你是 Investigator」+ 目标 + 入口 + 工具名。识别口令不准再写进 JS。  
3. 识别、切能力、来源、绑定、handbook、能不能发布：只在 Skill。  
4. 以后 diff 出现 `result-gate` / `visible-controls` / `renderer` / `computed.py` 认业务 = 方案作废。  
5. 只有运输损坏才改代码（第 10 节）。

Skill 文件（只这四个）：

```text
Pi_check/skill/BUSINESS_SKILL_INVESTIGATOR.md
Pi_check/skill/CONTROL_IN_APP_BROWSER.md
Pi_check/skill/INFER_BUSINESS_CONTRACT.md
Pi_check/skill/BUILD_AND_VALIDATE_DEDICATED_SKILL.md
```

删除：`Pi_check/skill/RECORDING_CAPABILITY.md`。

Cursor 侧（给人看的，不进 PI）：

- `.cursor/skills/dano-recording-contract/SKILL.md`：缺口改四份之一，禁止改闸门/导出猜。  
- `.cursor/skills/control-in-app-browser/SKILL.md`：只指向 Skill 2。

旧栈 `back/agent/recording-pi/skills/analyze-recording-evidence`、`run_recording_pi.mjs` 不算第 5 个 Skill，不得接回。sidecar 已写不得再启动。

---

## 3. 四个 Skill 现在缺不缺

| Skill | 现在 | 结论 |
| --- | --- | --- |
| 1 Investigator | 无独立文件。入口在 `RECORDING_CAPABILITY.md`「总入口」+ `pi-session.mjs` 提示词 | **缺失。入口在代码里。** |
| 2 Control | 有 `CONTROL_IN_APP_BROWSER.md` | **有，不够。** 无图像进模型、无同源前端、无 `host_value`；文件指向 RECORDING 写合同；还禁止把图片写进对话。 |
| 3 Infer | 即 `RECORDING_CAPABILITY.md` | **有，不够，且依赖代码。** 和入口/信封挤在一起；闸门/采集/导出在替它认；允许冻录制值结案。 |
| 4 Build/Validate | PI 里没有。在 `export/skill_package/spec.md`（writing-great-skills + 阿里）+ `renderer.py` + `validator.py` | **缺失。生成和校验是 Python 猜包。** |

---

## 4. `RECORDING_CAPABILITY.md` 逐节搬家（执行时按这张表剪）

不要「另写一套」。现文件约 513 行，按节剪切。同一段禁止同时留在两份里（信封形状示例只留 Infer）。

| 现文件位置 | 搬到 | 改写要点 |
| --- | --- | --- |
| 开头「PI 是唯一语义决策者」+「总入口」 | Skill 1 | 改成「你是 Investigator，动手按 2，认产物按 3，出包按 4」 |
| 「目标是剧本」§6 清单 | Skill 1 | 补全任务书九项：目标页、业务能力、能力模式、调用方要提供的信息、全字段或部分、排除项、顺序、最终动作、成功条件。禁止压成 query+create |
| 「工作记忆」 | Skill 1 | 仍写在对话，不新造笔记工具 |
| 「下一步只允许这五种」 | Skill 1 | 五种改成：调 2 观察 / 调 2 按目标操作 / 调 2 协助 / 调 3 交一项 / 用户结束后调 4 再定稿 |
| 「代码 / Skill / 模型」 | 删，改写进 RESPONSIBILITIES | 代码不再运输 `x-dano-section-titles` 作为识别；那是 Infer 写进信封的字段 |
| 「现有录制页实际读取的合同」 | Skill 3 保留「怎么写信封」；闸门只留形状 | 页面不改。形状校验见第 6 节档 1 |
| 「先建动作台账」通用动作词 | Skill 1 维护行；切得对不对归 Skill 3 | Investigator 不按 URL 合并，但也不用正则认「这是树」 |
| 「用索引建台账」+ 读工具用法 | Skill 3 为主；Skill 1 决定何时去读 | `read_response_blob` 只接受 `blob_` 等运输句可留一行在工具 description |
| 第 126 行整段点页面口令（open_page、空表、aN、assist） | **删出 Infer**；节奏归 Skill 1，细则归 Skill 2 | 这是入口和操作挤在 Infer 里的根源 |
| 「陌生页解题五步」 | Skill 3 | 第 4 步删除「或按无独立来源按录制原值处理」 |
| 「字段必须出现在两个位置」到「弹层选人」整段 | Skill 3 | 见第 5.3 必须删除/改写的句子 |
| 「编排」`links` / `capability_relations` | Skill 3 | 导出不再猜顺序 |
| 「提交形状」JSON 示例 | Skill 3 | 形状不变，前端还读这些键 |
| 「提交前自检」1–41 | 拆：形状/台账 → Skill 1 导出条件；字段/绑定 → Skill 3；执行器/handbook → Skill 4 | 第 12、26、499 条「看不出就冻原值」必须改成 unresolved |
| 「泛化」 | 拆到 1/2/3，禁止再写「请求有控件无就原值结案」 | |
| CONTROL 里「字段合同按 RECORDING」 | 删除 | Skill 2 禁止交能力 |

---

## 5. 四个 Skill 必须写成什么

每份开头三行：本文件只负责 X；禁止 Y；缺口只改本文件。

### 5.1 Business Skill Investigator

**负责：** 保存完整目标；建立能力台账；维护调查笔记；决定下一步调查；调用浏览器控制；调用合同推断；处理编译/校验失败；决定是否达到导出条件。

**不负责：** selector 细则、source_kind、SKILL.md 模板、阿里章节名、字段四问。

必须有的节：

1. **完整目标（任务书 §6）**  
   记下：目标页面、业务能力、能力模式、调用方要提供的信息、全字段或部分、排除项、顺序、最终动作、成功条件。

2. **能力台账**  
   一行一个可单独再发起的动作：文案、是否按目标做完、interaction seq、execute seq、合同状态（无/草稿/已推断/未解决）、能否交给 Build。不按 URL 合并。

3. **调查笔记（对话里）**  
   首屏请求、已证明绑定、unresolved、编译/校验失败原文、下一步只选一个。

4. **下一步只调子 Skill**  
   - 观察/操作/协助 → Skill 2 + `control_in_app_browser`  
   - 认一项产物 → Skill 3 + `submit_recording_capability`  
   - 最小补证 → 再调 Skill 2  
   - 出包验证 → Skill 4  
   - 定稿 → 仅导出条件满足且用户已结束，才 `submit_recording_result({final:true, use_draft:true})`

5. **分诊失败（代码只回错误，不分诊）**  
   - 形状错（`fields`、字符串 refs、params 不是数组）→ Skill 3 或 4 重交信封  
   - 缺依据 / 未识别当已解决 → Skill 3  
   - 证据不够 → Skill 2  
   - 点不动、图送不进、证据读丢 → 报程序故障（这才改代码）

6. **导出条件（你决定，代码不猜业务齐了）**  
   同时满足：用户已结束；台账每行有合同或 unresolved；写入行没有「未识别却当可执行」；Skill 4 的投影 / 变化输入 / 隔离运行 / validator 通过。

从现文件原样带走、不要改成字段规则的句子：未结束禁止定稿；人点的也要交；协助后停自动点；首屏不是已查询；空表不点保存；目标要提交就走完确认，不用保存换请求形状。

### 5.2 Control In App Browser

**负责：** 页面/弹窗/frame/控件身份；快照与截图；点击、填写、选择；动作后验证；网络证据；按 ID 读；必要时读同源前端。

**不负责：** 判断业务合同、`submit_recording_*`、划分能力、判定调用方/系统。

**保留：** 合法 selector、`choose`/`fill` 按所请、协助、加行后再 snapshot、不锁预览、双通道。

**必须删除：**

- 「字段合同按 RECORDING」  
- 「`screenshot` 只回摘要，禁止把图片写进对话」  
- 「不要 `include_screenshot`」作为绝对禁令（改为：默认 snapshot；Skill 1 要求看图时用图像消息）

**必须补的节（任务书 §3.1 §7 §8）：**

1. 页面 / 弹窗 / frame / `region` / `ref`。重名返回候选列表，禁止默认第一个。  
2. `snapshot` 默认。`screenshot` + `as_image=true` 以 **Pi 图像消息** 送给模型，禁止只给路径。  
3. 点击、填写、选择、批量填。值由 Investigator 指定，工具不自填样例，不整表自动填充。  
4. 动作后验证（事实）：回报 `host_value`、随后请求摘要。`ok` 但请求没变 = `not_applied`（没写上）。  
5. `not_found` 必须附带最新无图 snapshot。禁止改 CSS/`name=` 盲试。  
6. `network_since` + 按 seq/blob 读（现有读工具，description 写「细节看本 Skill」）。  
7. `read_page_asset`：仅本场已加载同源 URL。禁止读用户其他项目代码。  
8. 动态加行：新 snapshot 的表头/行内格，不当整表一个控件。  
9. `assist` 后停自动点；登录保留会话。  
10. 不改 Vue/React 内部状态。点不出请求就报问题，不造请求。  
11. 失败码分开，禁止用「固定三次」代替判断：  
    `not_found` / `not_writable` / `option_not_seen` / `not_selected` / `not_applied` / `ambiguous` / `assist_hold` / `transport_idle`  
    业务上算哪一种失败，由 Skill 1 定性。

可用 `action` 保持：`open_page` / `list_pages` / `snapshot` / `screenshot` / `click` / `fill` / `select` / `choose` / `press` / `fill_fields` / `network_since` / `assist`。  
新增独立工具或 action：`read_page_asset`。不要发明业务 action。

### 5.3 Infer Business Contract

**负责：** 划分原子能力；区分主能力、候选查询和噪声；判定调用方/系统/身份/常量/生成/计算/查询来源；生成动态候选转换；保存证据引用；明确未解决事项；给出下一步最小补证建议。

**不负责：** 点页面、写消费者 SKILL.md、改导出实现。

**必须删除（与任务书 §9 直接相反，现在就在 RECORDING 里）：**

```text
无独立来源 → source_kind=constant + default_value=录制原值 → 标记已解决
看不出公式 → 标系统 + 请求原值，不要写入 unresolved
字段来源写不出时：标系统自动处理，不要因此阻止导出
```

改成：

```text
没认清来源 → unresolved。录制值只可作 sample_value 诊断，不能当已解决业务默认，不能标已解决。
```

允许写成系统/常量的，必须是**已经认清**的：

- 点「添加××行」产生的行类型码、行序号、前端行键：有加行按钮 + 分区证据 → 系统，reason 写清依据和 seq。  
- 登录身份自动带上、表单没有对应可改控件 → `current_user`，禁止写死本场数字。  
- 可执行 formula 已从页面/前端交叉核对 → `computed`。  
- 每次提交都相同且有业务含义的固定判别值 → `constant`，依据写清。  

看不清的键（不知道是调用方漏填、系统生成，还是前端时间戳）：`unresolved`，不要冻成录制常量。

必须有的节（任务书 §9 §10 §11 + 现文件字段合同，删掉冻原值句）：

1. **划分原子能力**  
   独立业务动作 + 真实 execute。同一 path 服务新增和编辑 = 两项。能力身份 = origin + 资源 + method/path + 操作模式，不能只 method+path。

2. **主能力 / 候选查询 / 噪声**  
   主能力 = 台账行。  
   候选查询 = 本能力表单上真实存在的下拉/树 → `option_source`。  
   噪声 = 通知、菜单、字典总表、只带分页的刷新。  
   打开表单附带空列表不是新能力。选择器弹层不是新能力。

3. **四问 + 证据 seq**  
   它是什么业务字段；调用方要提供什么；系统如何取得或生成真实请求值；依据是什么。  
   合同字段：页面名称、调用输入名、请求路径、数据类型、控件类型、来源、调用方还是系统、必填性及依据、默认值规则、候选及其来源、格式和数值约束、分组/顺序/明细、证据引用、尚未解决事项。  
   必填四态：页面星号 / 用户要求全填 / 服务端已验证 / 未知。  
   来源七态：未识别 / 系统处理 / 常量 / 当前账号 / 当前时间 / 其他接口 / 用户输入转换。  
   「当前时间」必须有生成规则证据，禁止因叶子名叫 createTime 就写成 now。

4. **动态候选**  
   `page_enum` / `api_option` / `element_template`。禁止复制本场人员对象。选项列表路径禁止写成 `links`。

5. **信封怎么写（前端仍读这些位置，形状不变）**  
   见第 6.2。这是运输合同，不是识别器。Infer 必须按这个形状交，闸门只查形状。

6. **绑定（§10）**  
   值相等、字段名像、排除法不能单独定案。每条绑定保留：来源能力或字段、目标字段、定位规则、类型转换、唯一性、证据、不匹配/多匹配处理。

7. **最小补证（动手回 Investigator → Skill 2）**  
   改一个可改字段、换日期、两组明细不同内容、加减行、读同源前端。未授权不得为验证再提交业务单。

8. **编排（§11）**  
   顺序 → `capability_relations`。数据 → `links`。前置失败则停止后续写入。  
   目标先 A 后 B 且没有值流时必须写 relations，不要留给导出去猜。

交一项：`submit_recording_capability`。不准宣布定稿。

现文件里这些识别方法**原样搬进 Infer**（它们是 Skill，不是代码）：控件认法 1–15、确认弹层、可增行分区、`x-dano-section-titles`、弹层选人 `selects`/`element_template`、option_source params 不是 steps.params、树单击是单值、保存与提交拆两项。

### 5.4 Build and Validate Dedicated Skill

**新建。** 把 `back/dano/export/skill_package/spec.md` 里给**消费者**的规则改写成对 PI 的指令。不抄阶段号、FlowSpec、generator-guides、阿里产品专章（RAM、CLI、Session ID）。

**负责：** 根据唯一合同生成专用 Python 执行器；生成 SKILL.md 和表单；检查触发描述与渐进披露；验证合同到请求；用变化输入验证转换；验证写入结果和回读；验证独立环境运行；阻止不完整写能力被发布为可执行能力。

**不负责：** 重新推断来源；为了能跑把录制值写成已解决常量。

必须有的节：

1. **唯一输入**  
   只读 Infer 已提交合同。禁止回头猜页面。

2. **生成专用 Python 执行器**  
   `scripts/<capability>.py`；需回读则 `verify_<capability>.py`。  
   只用合同里的 path / formula / element_template / option_source / 已声明的 wire_format。  
   运行时只依赖冻结的 `client.py`（鉴权+HTTP）和 `wire_format.py`（仅声明过的格式）。  
   不准写 token。不准按字段名猜 now。未识别键不准写成可执行默认。

3. **生成 SKILL.md 与表单（阿里 handbook + writing-great-skills）**  
   frontmatter 仅 `name` + `description`（触发：做什么、哪些请求触发、边界）。  
   正文必须有：`适用场景`、`不适用场景`、`选择工作流`、`组合与交接规则`、`执行协议`、`成功、失败与停止`、`按需读取资源`。  
   `适用场景` 不复读 description。  
   `选择工作流` 是互斥路线表。组合细节链到 `references/routes/<id>.md`。原子路线不准叫 Agent 去读组合文件。  
   `组合与交接规则` 只三种：原子 / 已确认绑定 / 人手交接。绑定空、歧义、类型或基数不对就停止自动串联。  
   `执行协议` 每步必须有可判定的 `Done when:`。  
   写入：preview → confirm → execute。  
   `INPUT_FORMS.md`：只收集调用方字段；不重问系统/已绑定值。  
   `CONTRACT.json`：消费者合同，不是录制审计。禁止写入 `capability_id`、request/step id、fingerprint、录制样本。  
   `CAPABILITIES.md` / `OPTIONS.md` / `references/routes/<id>.md` 按 spec 布局。  
   禁止消费者正文出现 spec 里的泄漏词：`本页面的实际操作流程`、`能力录制`、`阶段N`、`FlowSpec`、`x-dano`、`原子能力`、`生成器` 等。  
   禁止「先阅读全部 references」。按需指针：「何时读哪个文件」。

4. **检查触发与渐进披露**  
   用 `validate_skill_package` 跑现有 `validator.py`（结构、frontmatter、章节、泄漏词、明文凭据、重复 route_id）。这是「执行明确规则」，不是认页面。

5. **验证合同 → 请求**  
   `project_contract_to_request`：只按合同投影。缺键失败，退回 Investigator，**不准补键**。

6. **变化输入**  
   至少两组合法不同输入。投影必须按合同变；不能两份都等于录制原文（除非合同声明 constant）。

7. **写入与回读**  
   仅用户授权写。按合同投影发送或授权回放，再按 fact_check/links 回读。失败则不发布该写能力。

8. **独立环境**  
   `run_isolated_script`：隔离目录 `--help` + dry-run，不依赖 Dano 进程。依赖仅 Python + httpx。

9. **阻止不完整写能力发布**  
   写入行仍 unresolved、来源未识别、投影失败 → 不得 `submit_recording_result` 当可执行写能力发布。查询类可带 unresolved 说明缺口，但不能把缺口冻成可执行默认。

`spec.md` 改成：生成器内部备忘，注明「对 PI 的指令以 Skill 4 为准」。不要让 PI 读 spec.md。

---

## 6. 信封与闸门：形状留下，识别删光

前端 `PageRecorder` **不改**。它只从这些位置画「调用方 / 系统 / 编排」：

1. `capabilities[].request_refs`：对象数组，每项 `step_id` + `usage`  
2. `capabilities[].step_ids`  
3. `capabilities[].input_schema.properties`  
4. `steps[].params`：字段对象数组，每项含 `key`/`path`

页面忽略：`capabilities[].fields`、字符串 refs、params 映射。这三种必须继续拒收——这是管子，不是认业务。

`usage` 仍只允许：`execute` / `preflight` / `option_source` / `fact_check`。谁挂哪一种由 Infer 判断，闸门不认「像不像选项接口」。

### 6.1 闸门终态只拒管子破了（档 1）

`result-gate.mjs` **只留**：

- 会话 / 录制编号 / `final=true` / 冻结 / 非空 result / 非空 capabilities / 只收一次  
- 禁止非空 `capabilities[].fields`  
- `request_refs` 为带 `step_id` 的对象（不能是字符串）  
- `steps[].params` 为带 `key`+`path` 的对象数组（不能是映射）  
- `capability_id` 不重复  
- 每个能力恰好一个不共用的 `execute`  
- `request_refs.step_id` 能在 `steps` 里找到  

**删除整个识别块（从 `SELECTABLE_HINT_RE` 到 `assertOptionCatalogLinks` 里按 path 猜选项的部分）：**

- `SELECTABLE_HINT_RE` / `ROW_ARRAY_HINT_RE` / `PAGINATION_KEY_RE` / `PAGINATION_LABEL_RE` / `SYSTEM_ROW_KEYS`  
- `fieldLooksSelectable` / `looksCollapsedRowArray` / `isPaginationCallerKey` / `isSystemRowKey`  
- 「像树但没写 option-source」「像可增行但收成 string」「title 像多分区但没写 section-titles」「schema 含 itemtype」  
- `schemaConflictsParamType`（类型像不像是 Infer 的事；若只检查「两边都写了 type 且一个 array 一个 scalar」——仍属形状，可留**纯类型冲突**这一条，但文案不准再写「树单击」）  
- `assertOptionCatalogLinks` 里用 `data[]` 字符串判断选项 links  

对应 `tests/test-result-gate.mjs`：部门树、可增行、分区 title、分页进 schema 等拒收用例**全部删**。档 1 用例全留。

`input_schema.properties.key` 必须对应某个 `exposed_to_user=true` 的 param.key：这是信封自洽（页面两栏对得上），**留下**。这不是认「这是不是树」。

### 6.2 Infer 必须交出的信封形状（从现文件「提交形状」原样保留在 Skill 3）

现 `RECORDING_CAPABILITY.md` 第 358–443 行 JSON 示例搬进 Infer，作为**怎么写**，不是代码模板。另须含：`links`、`capability_relations`、`request_facts`、`unresolved`、业务理解、成功/失败条件、回读方法、证据引用。

`reason` 必须是一句完整处理说明。导出/Skill 4 只抄这里，代码不编规则。

### 6.3 合并

`result-merge.mjs`：删除给 `capability_relations` 填 `suggested_call_chain` / `handoff` / `confirmed` 的改写。只按 id 替换、数组合并。Infer 自己写 type/mode。

---

## 7. 一次性把代码稳固成什么

这次改代码的目标：管子齐、脑子空。改完冻住。

### 7.1 加载与提示词 — `Pi_check/src/pi-session.mjs`

- `readSkill(name)` 读第 2 节四个文件；任一空或缺失 → 抛错，不准建会话。  
- `buildPiInstructions` 只拼接：一句「你是 Investigator」+ 四份全文 + 工具名列表。  
- **删除** 现在第 66–71 行信封口令复读。  
- **删除** `buildLiveDrivePrompt` / `buildFinalAnalysisPrompt` 里全部字段/点击细则（约 94–118 行整段识别口令）。  
- `buildUserSteerPrompt` 只留：先回答用户；然后按 Skill 1 继续；未结束禁止定稿。

入口提示只剩：

```text
你是 Business Skill Investigator。按四份 Skill 协调。
目标：{goal}
入口：{url}
人也可以点预览。未接到用户结束，禁止 submit_recording_result。
不要把完整 JSON 写在对话里。
```

定稿提示只剩：用户已结束；最新 seq；按 Skill 1 对台账，3 认产物，4 验证后提交。

空转：`MAX_EMPTY_FINAL_SETTLES` 可留次数防挂死，返回 `code=transport_idle`，由 Skill 1 决定是否继续。禁止把「3 次」写成业务失败。

### 7.2 工具 — `Pi_check/src/pi-tools.mjs`

现有工具（保留，description 改成一行 + 「细节看 Skill N」）：

| 工具 | 之后看 |
| --- | --- |
| `control_in_app_browser` | Skill 2 |
| `list_recording_manifest` / `list_recording_index` / `list_action_timeline` | Skill 1/3 |
| `read_request_shape` / `read_visible_controls` / `read_evidence_delta` / `read_evidence_item` / `read_response_blob` | Skill 2/3 |
| `get_recording_freeze_state` | Skill 1 |
| `submit_recording_capability` | Skill 3 |
| `submit_recording_result` | Skill 1（Skill 4 通过后） |

**删除：** `submit_recording_draft`（工具列表、host、PI tools 数组）。Skill 不用，第二条提交口。

**改 `read_screenshot` / `screenshot`：**  
删除 `stripImageFromToolResult` 对 `as_image=true` 的剥离。实现 Pi 图像 content（`type: image` + mime + data）。默认 `as_image=false` 只回元数据，避免每步灌图。`read_screenshot`：要么同样支持 `as_image`，要么删除并统一走 `screenshot`。禁止再假装模型看见了图。

`submit_recording_result` 的 description **不准再写**树 / option-source / 分区识别。

### 7.3 一次性新工具（不加齐，Skill 2/4 会空转）

| 工具 | 行为 | 谁调用 |
| --- | --- | --- |
| `screenshot` + `as_image=true` | Pi 图像消息，不是路径 | Skill 2 |
| `read_page_asset({url})` | 仅本场已加载同源 URL，否则 `found=false` | Skill 2 |
| `write_skill_artifact({path, content})` | 写入本场导出草稿目录 | Skill 4 |
| `validate_skill_package` | 调现有 `validator.py`，回 issues | Skill 4 |
| `project_contract_to_request({capability_id, inputs})` | 只按合同投影；缺键列出，**绝不补** | Skill 4 |
| `run_isolated_script({script, args})` | 隔离 cwd 跑生成的 py | Skill 4 |
| 授权写回放（若已有 replay） | 默认关；Skill 1 确认用户授权才开 | Skill 4 |

`project_contract_to_request` 实现：复用现有投影（`flow_client_projection` / renderer 里「按合同填 path」的部分），剥掉 `_inferred_system_values` 和按叶子猜 now。合同没写的键不出现。

### 7.4 采集与点击 — 只记事实，不定性

`visible-controls.mjs` / `browser-capture.mjs` / `browser-actions.mjs`：

删除：

- 快照前自动点「展开/高级搜索」  
- 「选择用户」、部门/公司/操作等人名过滤、表头业务黑名单  
- 用中文正则认定「这是加行」  
- `exerciseListPage` / `exercise_list` / `clickFirstVisible` 代点搜索/查看/填 `"1"`  
- `NOISE_NETWORK` 里具体 OA path（`getChatNotRead`、`/oa/myTask/`、`/prod-api/getRouters` 等）。可留：非 xhr/fetch 不进默认索引  

一次性补事实（有 DOM 才记，没有标缺，不定性）：

- 当前显示值 `display_value`  
- 提交相关宿主值 `host_value`  
- 日期可见格式  
- 数字 min/max/step  

`choose` / `fill` 成功或失败时回报 `host_value`。  
定位多个可见命中：回 `ambiguous` + 候选 `ref`/`region`，不默默点第一个。  
`not_found`：附带一次无图 snapshot。

### 7.5 导出改为打包，不再编译业务

文件：

- `back/dano/onboarding/skill_generation/export.py`  
- `back/dano/export/skill_package/renderer.py`  
- `back/dano/execution/page/flow_materialization/field_contracts/computed.py`  
- `back/dano/execution/page/flow_materialization/field_contracts/dynamic_array.py`  
- `back/dano/execution/page/flow_spec_core/request_steps.py`（`_param_source_guess`）

改成：

- `export_recording_skill`：不再用 planning 猜路线。路线来自合同的 relations/links，由 Skill 4 写成 `references/routes/`。  
- `renderer.py`：删除 `_inferred_system_values`。变成复制 Skill 4 写下的文件 + 注入冻结 `client.py` / `wire_format.py`。  
- 删除按字段名反推公式、猜分区、没 formula 从数字猜策略。  
- 发布闸：Skill 4 校验未通过，或写入仍 unresolved → 409，不发布可执行写能力。

`validator.py` **留下**（结构、frontmatter、渐进披露、禁止泄漏录制词、明文凭据）。  
`client.py` / `wire_format.py` **冻住**（HTTP 运行时）。  
前端录制页 **不改**。

### 7.6 必须删除的脚本与入口

```text
Pi_check/scripts/hotel-apply-actions.json
Pi_check/scripts/leave-apply-actions.json
Pi_check/scripts/list-page-actions.json
Pi_check/scripts/record-remaining-pages.mjs
Pi_check/scripts/record-site-matrix.mjs
Pi_check/scripts/inventory-site-pages.mjs
Pi_check/scripts/extract-menus-from-blob.mjs
Pi_check/scripts/extract-full-routes.mjs
```

`POST /api/recordings/:id/act` 若只服务上述脚本则删。  
`record-page-flow.mjs` 只许开一场/停录，不许喂 actions.json。

### 7.7 文档与 Cursor Skill

更新：`Pi_check/RESPONSIBILITIES.md`、`Pi_check/README.md`、`Pi_check/DELIVERY.md`、`Pi_check/skill/CONTROL_IN_APP_BROWSER.md` 职责句、`.cursor/skills/dano-recording-contract`、`.cursor/skills/control-in-app-browser`。  
写成：四 Skill + 代码只运输。缺口改四份之一。

---

## 8. 测试怎么改（避免以后改 Skill 还要改 JS）

| 文件 | 改法 |
| --- | --- |
| `Pi_check/tests/test-skill-contract.mjs` | **重写。** 只断言 4 文件存在、被 `readSkill` 加载、缺一建会话失败。**禁止**再 `assert.match` 锁提示词/Skill 金句（现文件 100+ 条金句锁是「改 Skill 必须改代码」的直接原因） |
| `Pi_check/tests/test-pi-session.mjs` | 入口/定稿提示不再断言树、分区、`current_user`、确认弹层、`不要读 screenshot`。改成断言：含「Investigator」、含目标/入口、不含字段细则、定稿提示不含识别口令 |
| `Pi_check/tests/test-result-gate.mjs` | 只留档 1。删除部门树/可增行/分区/分页拒收 |
| `Pi_check/tests/test-source-scan.mjs` | 禁止源码再出现 `SELECTABLE_HINT`、`exerciseListPage`、`部门树`、`itemtype` 识别表、`_inferred_system_values` |
| `Pi_check/tests/test-control-browser.mjs` | 现断言「图不进对话」必须**反转**：`as_image=true` 测到图像 content；默认仍可无图 |
| 新增 | `read_page_asset` 非同源 `found=false`；`project_contract_to_request` 不补键；缺 skill 文件拒绝开录 |
| 必须一直绿 | 双通道、协助、增量提交、PI 杀死、e2e 浏览器、证据按 ID 读 |

`back/tests/test_skill_export_recording_contract.py` / `test_recording_audit_regressions.py`：删掉「导出必须猜出 now / 公式」的期望；改为「合同没写 now 就不出现」。handbook 质量测试留下（命中/披露/确认写入），不测「业务字段是否被代码发现」。

---

## 9. 一场调查的固定流程（写进 Investigator，代码不另写流程）

```text
1  Investigator 记下完整目标
2  调 Skill 2：open_page → snapshot → network_since（必要时图像）
3  按目标写字段，点该动作自己的查询/保存/提交
4  人可点预览；协助后停自动点
5  该行做完且有真实 execute → 调 Skill 3，submit_recording_capability
6  未识别 → unresolved；要补证 → 回 3（最小实验或 read_page_asset）
7  用户结束 → Investigator 查导出条件
8  调 Skill 4：写执行器+SKILL.md+表单；投影；变化输入；隔离运行；validator
9  失败 → 按错误分诊回 5/6/8，代码不自动修
10 通过 → submit_recording_result；程序只打包已写文件
```

---

## 10. 任务书逐条落地（无遗漏）

| 条款 | 一次性代码 | 之后只改 |
| --- | --- | --- |
| 核心原则：智能体理解，程序不认业务 | 删识别，补工具 | Skill 1–4 |
| §3.1 快照/图/控件/人/变化/网 | **去掉剥图**；图像消息；`host_value` | Skill 2 |
| §3.2 请求/响应；同源前端 | `read_page_asset` | Skill 3 决定是否读 |
| 禁止读其他项目代码 | asset 仅同源本场 | Skill 2/3 |
| §5 PI 全部职责 | 加载 4 Skill | 对应 Skill |
| §5 程序：动作/页面/证据/凭据/结构校验/指定检查/打包 | 闸门档 1 + validator + project（不补键） | 不改 |
| 禁止正则/同义词/值相等代替理解 | 删闸门/导出/采集猜 | — |
| 不盲信：绑定须能指到真实证据 | 投影缺键则失败；证据按 ID 可读 | Skill 3 写依据 |
| §6 目标完整 | — | Skill 1 |
| §7 九条操作 | 图、候选、失败带 snapshot、fill_fields | Skill 2 |
| 重名不准默认第一个 | `ambiguous` + 候选 | Skill 2 |
| 四种失败分开；不以三次代替判断 | 工具 code 分开；空转=`transport_idle` | Skill 1 定性 |
| 不改框架、不造请求 | — | Skill 2 |
| §8 证据清单 | 补显示值/格式/step；图给模型；原始与推断分开（推断只在信封/笔记） | Skill 2/3 |
| 按 ID 读、blob 分页、正文未取得标缺 | 已有，保持 | — |
| 时间接近只是候选 | evidence-facts 保持 | Skill 3 |
| 无关数据不进默认上下文 | 索引 + 按需读，保持 | Skill 2/3 |
| §9 四问；禁冻录制值 | — | **只 Skill 3** |
| §10 绑定与最小实验 | asset + 动作验证 | Skill 3 建议，2 执行 |
| §11 能力与两类编排 | 导出不猜路线 | Skill 3 写，4 编进 handbook |
| 阿里 + writing-great-skills | validator 保留结构规则 | **只 Skill 4** |
| 不完整写不发布 | 发布闸看 unresolved + 投影 | Skill 1/4 |

任务书第 7 节标题在原文是空的 `##`，内容是「至少支持 1–9」。按上表 §7 落地，不要另开第五章。

---

## 11. 什么叫「非常必要」才改代码

只允许：

- 动作执行错（点 A 打到 B；`choose`/`fill` 没按所请）  
- 图像/证据存丢或读丢；图送不进模型  
- 代码改写了 PI 信封  
- 工具返回与现场不符  
- 凭据泄漏  
- 新工具类别是任务书要、现 Skill 调用却物理上做不到（加运输，不加识别）  

不允许：换页认错、少字段、少分区、导出少业务理解、handbook 触发差。

稳固之后出问题改哪一份：

| 现象 | 只改 |
| --- | --- |
| 目标理解错、过早定稿、校验失败分诊错 | Skill 1 |
| 点不到、选不上、该看图没看、没验证写上 | Skill 2 |
| 切错能力、来源错、绑错、冻录制值、分区弹层 | Skill 3 |
| 触发差、披露不对、执行器与合同不一致、不该发布 | Skill 4 |
| 点了没反应、图送不进、证据丢失、投影工具补了键 | 才改代码 |

---

## 12. 按步执行（做完一步勾一步，不要跳、不要并行拆识别和补工具）

### 第 0 步 · 金样（当天）

1. 复制一场现有成功 `pi-result.json` 到 `Pi_check/tests/fixtures/golden-envelope.json`。  
2. 记下该场导出 query/body **键集合**（用来发现「删猜测后少了哪些是猜出来的」，不是把猜当标准）。  
3. `cd Pi_check && npm test` 全绿再往下。  
4. 跑现有 `back` 里录制导出相关测试，记下基线。

### 第 1 步 · 四文件落地 + 加载

1. 按第 4、5 节新建/改写 4 个 md。Infer 必须明文禁止未识别→录制常量→已解决。  
2. 删除 `RECORDING_CAPABILITY.md`。  
3. 改 `pi-session.mjs` 加载 4 文件，提示词按 7.1。  
4. 重写 `test-skill-contract.mjs`；收瘦 `test-pi-session.mjs` 提示词断言。  
5. 更新 RESPONSIBILITIES / README / Cursor skills。  

验收：缺一个 skill 文件时创建会话失败；按已改测试绿。

### 第 2 步 · 拆识别代码

1. 闸门 6.1。  
2. merge 6.3。  
3. 工具描述 7.2，删 draft。  
4. 采集/代点/噪音 7.4。  
5. 删站点脚本 7.6。  
6. source-scan 加上识别禁词。  

验收：双通道/协助/增量提交/PI 杀死仍绿。金样仍被档 1 接受。

### 第 3 步 · 补齐运输工具（必须和第 2 步同一迭代做完，禁止只拆不补）

按 7.3 全部实现并写单测。空转改 `transport_idle`。去掉剥图。

验收：不传新参数时旧 e2e 不变；`as_image` 测到图像 content；非同源 asset 为 false；投影不补键。

### 第 4 步 · 导出改打包

按 7.5。Skill 4 用 `write_skill_artifact` 写包，export 只复制+注入 client。

验收：无合同外 now/公式；写入 unresolved 时拒绝发布写能力。

### 第 5 步 · 冻代码

1. 在 RESPONSIBILITIES 写入第 11 节「非常必要」清单。  
2. 用一个从未打过补丁的页面走完整场。失败只改 4 个 md。  
3. 此后业务问题禁止改 JS/Python。  
4. 最终验收清单（第 13 节）全部勾上。

---

## 13. 最终验收

- [ ] `Pi_check/skill/` 恰好 4 个文件，无 `RECORDING_CAPABILITY.md`  
- [ ] 缺一文件不能开录  
- [ ] `buildLiveDrivePrompt` / `buildFinalAnalysisPrompt` / `buildPiInstructions` 不含字段规则、空表、aN、section-titles、部门树  
- [ ] 代码搜不到 `SELECTABLE_HINT`、`exerciseListPage`、`_inferred_system_values`、叶子 `now_ms` 猜测、`部门树` 识别  
- [ ] Infer 明文禁止未识别→录制常量→已解决  
- [ ] 截图能以图像进 PI；默认不每步灌图  
- [ ] 同源前端可读；非同源拒绝  
- [ ] Skill 4 能写 py + SKILL.md + 表单，并走投影/validator/隔离运行  
- [ ] 不完整写能力不能发布  
- [ ] `test-skill-contract.mjs` 不再锁 Skill 金句  
- [ ] npm test 与导出结构测试绿  
- [ ] 双通道、协助、按所请 fill/choose、原样信封仍在  
- [ ] 陌生页问题的修复 PR 只含 `Pi_check/skill/*.md`

---

## 14. 一句话

这次把代码改成冻住的手：会点、会存、会跑检查、会打包，不会认业务。  
四个 Skill 才是大脑。改完全部内容之后，换页只改 Skill。  
上次说的「下降」来自半成品和假完整；不是终态。
