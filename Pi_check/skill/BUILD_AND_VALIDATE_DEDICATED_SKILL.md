# Build and Validate Dedicated Skill

本文件只负责：在用户点击「产出 Skill」之后，核对运输层已按录制合同物化的整包，检查触发与渐进披露，验证合同到请求，决定能不能提交出包。
禁止：回头猜页面、重写执行器、按页面再猜字段、为了能跑把录制值写成已解决常量、把真实 token 写进手册或对话。
缺口只改本文件。运行时不流畅、不稳定：只改成品 `SKILL.md` 或本文件，禁止为某一页改运输代码。

你只在**出包会话**里工作。不要点页面，不要 `submit_recording_capability`，不要 `submit_recording_result`。

## 写包前

1. 调用 `read_generator_guides`，读完返回的全部 `.md`。缺目录或缺
   `skill-generator-ask-user-question-guide.md` / `skill-generator-auth-and-token.md` /
   `skill-generator-workflow.md` / `skill-generator-live-options.md`
   → `submit_skill_export({ok:false, errors:[...]})`，停止。
2. 调用 `read_export_contract`。唯一输入是五块：`capabilities` / `steps` / `links` / `capability_relations` / `unresolved`。禁止只扫 `capabilities[]`。禁止回头打开页面猜字段。
3. 调用 `read_context_skill`（只读本场 overlay）。用它核对日期口径、成功码、默认路线线索。禁止 `control_in_app_browser`，禁止再打开业务页，禁止按 overlay 改 CONTRACT。
4. 调用 `read_skill_artifact` 看运输层已物化的 `SKILL.md`、`references/CONTRACT.json`、`references/INPUT_FORMS.md`。先对现包跑 `validate_skill_package`。
   - 已经 ok：**先做下面「不可执行合同」检查**，不通过就 `submit_skill_export({ok:false})`，不要改 CONTRACT、不要改脚本、不要为了能跑把系统缺省编成常量。通过后禁止改字段表、冻结 JSON 的 id / inputType、查询确认规则，禁止另写更瘦表单。只允许改 frontmatter 的 `name` / `description`，以及「立刻办理」「成功、失败与停止」里与回复形态、日期 `today` 替换、**按 `routes.default.steps` 逐步办理**相关的句子。立刻办理缺「禁止一上来 `--route default`」、缺「原始结果表」、或缺「动态字段先 `--list-options`」时必须补上再 validate。禁止为某一页去改运输代码（`contract-materialize` / `runtime.py`）。通过后立刻 `submit_skill_export`。
   - 只有 handbook_fields 失败时才改 `SKILL.md`。必须保留运输层已有的字段表、冻结提问 JSON、可用默认值、`合同值`、`route_id`。禁止另写一份更瘦的表单。禁止把 `dataSource` 写进 `SKILL.md`。禁止写 `"inputType": "table"`。
   - `write_skill_artifact` 一旦 `saved:false`：停止再写 `SKILL.md`。对运输层现包再 `validate_skill_package`，通过且「不可执行合同」也通过就 `submit_skill_export({ok:true})`。禁止为过校验另写更瘦表单。
   - 改写被拒绝或再次失败：不要继续改瘦。用运输层现包再 validate，通过就提交。
5. 写入行仍有未识别来源、或合同声明不可执行：停止，不要出可执行写能力。

### 不可执行合同（出包必须失败）

运输层现包 `validate_skill_package` 通过，不等于写能力能跑。出现任一条 → `submit_skill_export({ok:false, errors:[...]})`，写明要回到录制会话用原 `capability_id` 重交合同。禁止手改 `CONTRACT.json` / `runtime.py` 救这一页。

- 写能力 `system_params` 里 `required=true` 且 `source_kind=page_default`（或 `constant`）却没有 `default_value`，runtime 填不出。
- 写能力系统栏 `current_user` 的 key 不是登录身份真实字段（例如把打开表单时写入的时间戳标成当前用户）。
- `option_source` / `caller_fields.dataSource.endpoint` 等于另一能力的 execute path，空参无法返回候选。
- `input_schema` / `caller_fields` 出现了该能力 `exposed_to_user=false` 的同名 key（点行身份一边系统一边又做成树）。
- 冻结提问 JSON 的字段 id 少于该能力 `caller_fields`（确认卡会比原页瘦）。
- 合同里同时有「列表查询」和「点结果看详情」两项读能力，但 `routes.default.steps` 跳过了详情，只剩查询→写入。
- `project_contract_to_request` 对写能力用仅含调用方字段的合法输入无法投影出必填系统键。出现这一条 → 立刻 `ok:false`，停止出包。
- 冻结提问 JSON 的字段 id 必须覆盖该能力全部 `caller_fields`。`routes.default.steps` 不得跳过合同里已有的详情/写入。
- `unresolved` 仍覆盖写入行的可见表头列（例如加行后才出现的进度），却仍把该写能力标成可执行。
- 写能力 execute 的 `params` 只有附件和一个常量、却没有登录身份类系统键，而 caller 已有整张可写表单：这是丢掉了请求体身份键，回到录制重交。
- 查询能力 intent 写了查看详情 / 应填 / 已填 / 未填，合同却没有独立详情能力，默认链只有查询→写入。
- 可增行数组 title 含拆不开的「A和B」；或 `items.properties` 用合并列名顶替表头原文；或加行后可见的进度列既不在 properties 也不在 unresolved。
- 写请求行对象里的类型码 / 行序号 / 前端行键只出现在 reason，未作为系统 param。

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
  动态字段（执行协议写了 `--list-options`）：**第一次工具**必须是 `cd <本 SKILL.md 所在目录> && python3 scripts/flow.py --list-options <capability_id> <field>`，把返回的 `options`（已展平 id/label）写进该字段后再复制冻结 JSON 去 `ask_user_question`。没有动态字段时，第一次工具才是冻结提问。禁止把无 `options` 的 `treeSelect` / `select` 直接问出去。禁止把 `dataSource` 放进 `ask_user_question`。
  按合同 `routes` 走。用户意图只对上某一条能力 `name` / `intent` → 只跑该原子 `--route <capability_id>`。对不上 → 按 `default.steps` **数组顺序逐步**办理：每一步问该能力自己的冻结表，交回后立刻 `--route <这一步的 capability_id>`，禁止一上来 `--route default`。读能力不要确认卡；每步读成功必须先发一条用户可见的原始结果表（优先脚本 `table`，禁止改写成短条），再进入下一步。写能力字段收齐后必须 `{ "confirm": true, "formIds": ["<answered.formId>"] }`，确认后再 `--confirm`。
  禁止写「不要走 default，因为可能不含填写」——`default.steps` 必须含本场已交的查询/详情/写入。禁止跳过 `default.steps` 里的中间读能力。禁止自造合同外问卷。禁止增删冻结字段、禁止改 `inputType`、禁止自己补非日期 `default`。日期 `default: today` 调用 ask 前必须先跑 `date +%F` 换成当天 yyyy-MM-dd。禁止用录制样本日期冒充当天。
  无合同 default 的字段不要编 default。禁止「暂无 / 请填写 / 请审批」。可选空 = 省略或空字符串。用户交回占位句视为未填，按同一张冻结表再问。
  系统字段不要向用户要。手册里「可用默认值」只能写合同里 runtime **真能填**的值（`合同值 N`、当前登录身份、`previous_response` 路径）。系统栏写着 `page_default` 却没有合同值 → 合同不可执行，停止，不要让用户补键，不要改 `CONTRACT.json`。
- `冻结提问`：每个能力一份宿主可执行的 `ask_user_question` JSON，字段 id 与 `caller_fields` **逐字一致、一个不能少**。一次一张完整表单，不要拆多轮。Dano 宿主没有 `table`：对象数组在提问里投影为同一 id 的 `textarea`，由 runtime 组装回数组。禁止写 `inputType: table`。问句只收行内真实输入（分区标题 + 可见表头列）；不要让用户填行类型码、行序号。无合同 default 的字段不写 `default`。日期字段 `default` 写 `today`，调用前换成当天 yyyy-MM-dd；禁止把内部 path 写进问句。
- `成功、失败与停止`：查询必须回原始结果表。列和单元格按该能力本次返回写，不要为某一页写死口径，不要改写成短条。选项失败或空列表 → 停问，不准猜第一条、不准用录制样本冒充。
- `description` 是路由触发：用合同里各能力的 `name` / `intent` 说明什么用户请求走哪条路线。用户意图对上某条能力就走该原子路线，对不上走 `default` 的 steps 顺序。禁止为某个业务口令写死 `capability_id`。不要写「字段以 CONTRACT.json 为准」。
- `适用场景` 不复读 description。
- `选择工作流` 第一行 = 默认完整办理，步骤必须与 `routes.default.steps` 一致。禁止写「每次只执行一项」「不得自行串联」「一页面对应一个 Skill」。禁止把合同里已有的详情/中间读能力从默认链删掉。
- `组合与交接规则` 只三种：原子 / 已确认绑定 / 人手交接。
- `执行协议` 每个能力一节，必须有可判定的 `Done when:`。产出完全基于能力：该能力 `input_schema.properties` 和调用方 params 一个都不能漏，做成填写表：字段 id、标题、必填、控件、**怎么填**、**可用默认值**。数组行内列写成 `数组字段.列名`。枚举写出合同全部 id/label；动态字段写出 `--list-options <capability_id> <field>`。系统字段另行列出「不要向用户要」和**实际合同值**（如 `合同值 1`）；常量必须带 `default_value`。写着 `page_default` 却没有可填合同值的系统字段不得出现在「不要向用户要」里假装 runtime 会填。
- **可用默认值**只允许写合同已给出的 `default`、本对话用户已确认的值、合同枚举 id、本次 `--list-options` 选中的 id、写操作日期的页面默认当日（仅当该日期是**调用方**字段）。选中记录 / 上一步结果只能用本对话已确认的 id 或合同 `links`。没有这些就写「无可用默认值，必须向用户收集」。可选字段允许空，禁止编「无 / 示例 / 请审批 / 暂无」。禁止因为 execute 标了系统就把能力 schema 字段删掉。
- `按需读取资源` 写「默认不要读其它文件；只有提问失败或脚本报错才读 INPUT_FORMS」。禁止「先阅读全部 references」。
- `鉴权`：先用本包 `auth.local.json` 执行。401 / 账号未登录停问一次 token，再用 `DANO_AUTH_HEADERS` 覆盖本地过期头后重跑同一条命令。不要改文件，不要再问第二次，不要写具体 token。

调用方字段、控件、dataSource、路线以运输层物化结果为准。不要自己另写一份更瘦的表单，不要按某个页面特例改路线。动态字段先 `--list-options` 再提问，把返回的 options 写进 ask；不要把 dataSource 放进 ask_user_question。INPUT_FORMS 必须保留 dataSource。

消费者正文禁止出现：`本页面的实际操作流程`、`能力录制`、`录制结果`、`阶段1`–`阶段8`、`FlowSpec`、`fingerprint`、`x-dano`、`规划依据`、`一页面对应一个 Skill`、`原样来自`、`生成器`、`generator-guides`。

不要抄阿里产品专章：RAM、CLI 安装、Session ID / User-Agent、云账号参数。

## 检查与验证

用 `validate_skill_package` 跑结构规则和合同保真。运输层手册已经通过时不要重写。有 error 只改 **SKILL.md** 缺口，不要改冻结执行器，不要改检查器去放行。`handbook_fields` 会列出缺哪些字段 / 默认值 / 禁止的 `dataSource` JSON。

对每个能力调用 `project_contract_to_request({capability_id, inputs})`。只按合同投影。缺键失败，列出缺哪些，**不准补键**。写能力若因系统栏 `page_default` 无 `default_value` 而投影失败：这是合同不可执行，停止出包，不要改投影、不要改 runtime。

至少两组合法不同输入。投影必须按合同变。不能两份都等于录制原文，除非合同声明该键是 `constant`。

`validate_skill_package` 通过且「不可执行合同」检查也通过后立刻 `submit_skill_export`。不要反复 `run_isolated_script`。不要为了出包去手改成品 `CONTRACT.json`。

默认不要为验证再提交业务单。

## 提交

通过 → `submit_skill_export({ok:true, skill_id, description, routes})`。
失败 → `submit_skill_export({ok:false, errors:[...]})`。
查询类可带缺口说明，但不能把缺口冻成可执行默认。不完整写能力不得发布。
