# Build and Validate Dedicated Skill

本文件只负责：按已交能力写出给**调用方**用的 Skill。怎么提问、怎么跑 `flow.py`。  
成品必须遵守仓库 `doc/` 目录里**当前全部文件**。增删以目录为准，不要假设固定几份或固定文件名。规范约束的是调用形状，不是去判能力对不对。  
禁止：校验能力对不对、回头猜页面、重写执行器、另编字段/接口、把录制问题当成出包失败、把规范文件打进消费者包。  
能力对不对只在录制合同里解决。这里出了问题，先回到 Infer，不要在本文件补能力。

你只在**出包会话**里工作。不要点页面，不要 `submit_recording_capability`，不要 `submit_recording_result`。

用户点击「产出 Skill」后**运输层**按已交能力和 `doc/` 当前规范物化整包。本 Skill 只核对手册调用形状，不要整篇重写。字段、接口、必填与能力一致，一个不多、一个不少。

## 写包前

1. `read_export_contract`，只认五块能力合同。
2. `read_generator_guides`，读完 `doc/` 目录里当前全部文件。目录空不得出包。成品调用形状必须遵守返回的每一份。以后增删以目录为准。
3. 运输层已按能力和这些规范物化 `SKILL.md`。不要整篇重写，不要读 `scripts/*`，不要把规范正文抄进成品，不要用 `validate_skill_package` / `project_contract_to_request` 去判能力对不对。
4. 手册已满足规范 → 立刻 `submit_skill_export({ok:true})`。
5. 只有规范要求的调用说明缺句才改 `SKILL.md`。只允许改 frontmatter 的 `name` / `description`，或补一句。能力里没有的键不要写进手册。

## 运输层已经写好的包

可执行包由运输层从录制五块投影，不是你手写出来的。已经就位：

```text
<package>/
  SKILL.md
  config/runtime.json
  config/auth.local.json
  scripts/client.py
  scripts/runtime.py
  scripts/flow.py
  scripts/wire_format.py
  scripts/format_list.py
  references/CONTRACT.json
  references/CAPABILITIES.md
  references/OPTIONS.md
  references/INPUT_FORMS.md
  references/routes/<route-id>.md
```

禁止重写：`scripts/client.py`、`scripts/runtime.py`、`scripts/flow.py`、`scripts/wire_format.py`、`scripts/format_list.py`、`references/CONTRACT.json`、`references/INPUT_FORMS.md`、`config/auth.local.json`、`config/runtime.json`。
禁止另开 `oa-xxx/` 子目录再写一套 SKILL.md。
禁止发明 `client.request`、`http_json(params=)`、`http_json(json=)`。冻结 API 只有 `http_json(query=, body=)`。

`flow.py` 的入口：

```text
python3 scripts/flow.py --route default --input-json '{...}' --confirm
python3 scripts/flow.py --list-options <capability_id> <field>
```

## 你只可以改 SKILL.md

frontmatter 仅非空 `name` + `description`。不要写 `version`、`compatibility`、`disable-model-invocation: false`。

`description` 是路由触发：做什么、哪些不同用户请求触发它、关键边界。不要用 action UUID、skill_id、接口路径当描述。

正文必须有：`立刻办理`、`冻结提问`、`适用场景`、`不适用场景`、`选择工作流`、`组合与交接规则`、`执行协议`、`成功、失败与停止`、`按需读取资源`、`鉴权`。

- `立刻办理`：只读这一节就能办。读完禁止再读本文件，禁止 ls / cat / 探路。本 `SKILL.md` 就在本包目录，用**本文件路径**，不要用 `available_skills` 里过期 location。
  冻结提问：读能力不要弹出。写能力把该能力冻结 JSON 整份拿去调用，只改 default，禁止删 id。先按合同填槽，再弹整张写表。
  选路：matched = 本包能力里 name / title / intent 被当前说法覆盖到的集合。1 个 → 只跑该原子路线。≥2 个 → 取 `default.steps` 里覆盖全部 matched 的最短连续切片（夹在中间且有已确认 relation/link 的步骤一起跑，切片外不跑）。0 个且已选本 skill → 按 `default.steps` 全程逐步办理。不要为某个业务口令写死 capability_id。
  每步先填槽。读能力禁止 `ask_user_question`，禁止确认卡：说法、合同身份、上一步原始表能填的立刻 `--route`；填不满必填槽就停并说明缺哪条合同槽，不要改口问部门树或姓名。写能力必须把该能力冻结提问代码块整份拿去调用：questions[] 的 id 集合和顺序与冻结 JSON 完全相同，禁止删 id。只允许改各题 `default`。附件、计划、问题、备注这类空可选也必须出现（省略 default，不要编占位）。少一个 id = 违规。用户提交这张表后，同一轮 `{ "confirm": true, "formIds": ["<answered.formId>"] }`，确认后再 `--confirm`。禁止按口头提到的字段瘦表——宿主确认卡就是上一张分组表的回放。
  读能力已唯一确定的字段不要放进本次 questions[]（整步都不问）。写操作冻结 JSON 里每一题都进 questions[]，已确定的只放 default，禁止删 id。有 default 还把读能力再问一遍 = 违反已确定则不问。
  有合法值必须写入该题 `default`（非空）。禁止空字符串，禁止编「暂无 / 请填写 / 请审批」。读能力无合法值：必填填不满就停，不要问。写能力无合法值：必填省略 default 仍留在表里；可选未提也留在表里（可空）。禁止因为没值就删掉该题。
  动态字段仅当写操作尚未确定时才跑 `cd <本 SKILL.md 所在目录> && python3 scripts/flow.py --list-options <capability_id> <field>`；说法能唯一对上则直接用 id。不要把 dataSource 放进 `ask_user_question`。禁止把无 `options` 的 `treeSelect` / `select` 直接问出去。
  禁止 `--route default` 一次跑完整条链。逐步 `--route <capability_id>`。读成功必须先发原始结果表（优先脚本 `table`，禁止改写成短条），再进入切片下一步。点行身份、附件元数据、登录身份都在系统栏：冻结提问不要问它们。先查某日再决定是否写：写入日期继承该查询日；「今天的内容」填正文槽，不把已确定的业务日期改成当天。
  只有写操作且合同 `page_default=today` 的日期，冻结 JSON 才带 `default: today`，调用 ask 前必须先跑 `date +%F` 换成当天。用户说法已给出日期或上一步同名 id 已确定 → 用那个日期作 default，仍留在全量确认表里，不要改成今天。查询/筛选周期没有当日默认，禁止改成今天。单一日期说法同时填 startDate 与 endDate（同一天区间）。禁止用录制样本日期冒充当天。
  系统字段不要向用户要。用户指向自己（我/替我/本人/当前登录人）时不要问「你是谁」、不要问所在部门；读/写身份键 runtime 按合同 `current_user` 填。selected_record 指向自己时用合同身份主键去上一张原始表匹配，唯一命中直接用，不要用表里其它人名反问。能力缺值不要在出包补，也不要因此判出包失败。
- `冻结提问`：每个能力一份宿主可执行的 `ask_user_question` JSON，字段 id 与 `caller_fields` **逐字一致、目录不能少**。读能力调用时不要弹出这张表。写操作调用必须整份带上该 JSON 的 questions[]（id 集合和顺序相同，禁止删 id；已确定的放 default，没值的题仍出现）。Dano 宿主没有 `table`：对象数组在提问里投影为同一 id 的 `textarea`，由 runtime 组装回数组。禁止写 `inputType: table`。问句只收行内真实输入（分区标题 + 可见表头列）；不要让用户填行类型码、行序号。有合法来源必须写入 `default`。只有写操作且合同 `page_default=today` 的日期才写 `default: today`，调用前换成当天；查询周期日期不要写 today。禁止把内部 path 写进问句。禁止把 `dataSource` 放进 ask。
- `成功、失败与停止`：查询必须回原始结果表。列和单元格按该能力本次返回写，不要为某一页写死口径，不要改写成短条。选项失败或空列表 → 停问，不准猜第一条、不准用录制样本冒充。
- `description` 是路由触发：用合同里各能力的 `name` / `intent` 说明什么用户请求走哪条路线。用户意图只对上某条能力就走该原子路线，同时对上多条则按 `default.steps` 切片。禁止为某个业务口令写死 `capability_id`。不要写「字段以 CONTRACT.json 为准」。
- `适用场景` 不复读 description。
- `选择工作流` 第一行 = 默认完整办理，步骤必须与 `routes.default.steps` 一致。禁止写「每次只执行一项」「不得自行串联」「一页面对应一个 Skill」。禁止把合同里已有的详情/中间读能力从默认链删掉。
- `组合与交接规则` 只三种：原子 / 已确认绑定 / 人手交接。
- `执行协议` 每个能力一节，必须有可判定的 `Done when:`。产出完全基于能力：该能力 `input_schema.properties` 和调用方 params 一个都不能漏，做成填写表：字段 id、标题、必填、控件、**怎么填**、**可用默认值**。数组行内列写成 `数组字段.列名`。枚举写出合同全部 id/label；动态字段写出 `--list-options <capability_id> <field>`。系统字段另行列出「不要向用户要」和能力里已有的合同值。没有合同值就写「能力未给出默认值」，不要假装 runtime 会填。
- **可用默认值**只允许写合同已给出的 `default`、本对话用户已确认的值、合同枚举 id（说法唯一匹配即可）、本次 `--list-options` 返回并唯一对上或被选中的 id、写操作日期的页面默认当日（仅当该日期是**调用方**字段）、合同标明继承上一步或 `links` 的值。有合法来源必须写入 `default`。读能力：没有合法来源则必填填不满就停，不要问。写能力：没有合法来源也留在全量确认表里（必填省略 default，可选可空）。禁止编「无 / 示例 / 请审批 / 暂无」。禁止因为 execute 标了系统就把能力 schema 字段删掉。
- `按需读取资源` 写「默认不要读其它文件；只有提问失败或脚本报错才读 INPUT_FORMS」。禁止「先阅读全部 references」。
- `鉴权`：先用本包 `auth.local.json` 执行。只有脚本 JSON `code=AuthExpired`（或 HTTP 401 / 账号未登录）才停问一次 token，再用 `DANO_AUTH_HEADERS` 覆盖本地过期头后重跑同一条命令。查询刚成功、随后写入报「当前登录用户缺少」不是过期，不要问 token，回到 Infer 改身份 `result_path`。不要改文件，不要再问第二次，不要写具体 token。

调用方字段、控件、dataSource、路线以运输层物化结果为准。不要自己另写一份更瘦的表单，不要按某个页面特例改路线。动态字段仅当尚未确定时 `--list-options`，把返回的 options 写进 ask；不要把 dataSource 放进 ask_user_question。INPUT_FORMS 必须保留 dataSource。

消费者正文禁止出现：`本页面的实际操作流程`、`能力录制`、`录制结果`、`阶段1`–`阶段8`、`FlowSpec`、`fingerprint`、`x-dano`、`规划依据`、`一页面对应一个 Skill`、`原样来自`、`生成器`、`generator-guides`。

不要抄阿里产品专章：RAM、CLI 安装、Session ID / User-Agent、云账号参数。

## 不要校验能力

不要用 `validate_skill_package`、`project_contract_to_request`、`run_isolated_script` 去判断能力对不对。能力问题留在录制合同。`read_generator_guides` 只用来核对调用手册是否满足 `doc/` 规范。手册已满足立刻 `submit_skill_export({ok:true})`。不要整篇重写 `SKILL.md`，不要手改 `CONTRACT.json`。

## 提交

调用手册已按能力写出 → `submit_skill_export({ok:true, skill_id, description, routes})`。
只有运输层文件缺失、无法写出调用说明时才 `submit_skill_export({ok:false, errors:[...]})`。不要因为能力字段来源不完整而失败。
