# Build and Validate Dedicated Skill

本文件只负责：在用户点击「产出 Skill」之后，核对运输层已按录制合同物化的整包，检查触发与渐进披露，验证合同到请求，决定能不能提交出包。
禁止：回头猜页面、重写执行器、按页面再猜字段、为了能跑把录制值写成已解决常量、把真实 token 写进手册或对话。
缺口只改本文件。

你只在**出包会话**里工作。不要点页面，不要 `submit_recording_capability`，不要 `submit_recording_result`。

## 写包前

1. 调用 `read_generator_guides`，读完返回的全部 `.md`。缺目录或缺
   `skill-generator-ask-user-question-guide.md` / `skill-generator-auth-and-token.md` /
   `skill-generator-workflow.md` / `skill-generator-live-options.md`
   → `submit_skill_export({ok:false, errors:[...]})`，停止。
2. 调用 `read_export_contract`。唯一输入是五块：`capabilities` / `steps` / `links` / `capability_relations` / `unresolved`。禁止只扫 `capabilities[]`。禁止回头打开页面猜字段。
3. 调用 `read_skill_artifact` 看运输层已物化的 `SKILL.md`、`references/CONTRACT.json`、`references/INPUT_FORMS.md`。先对现包跑 `validate_skill_package`。
   - 已经 ok：禁止覆盖 `SKILL.md` 正文。只允许改 frontmatter 的 `name` / `description`；改完必须再 validate。通过后立刻 `submit_skill_export`。禁止为了「写得更清楚」加长或改瘦正文，禁止改查询确认规则。
   - 只有 handbook_fields 失败时才改 `SKILL.md`。必须保留运输层已有的字段表、冻结提问 JSON、可用默认值、`合同值`、`route_id`。禁止另写一份更瘦的表单。禁止把 `dataSource` 写进 `SKILL.md`。禁止写 `"inputType": "table"`。
   - 改写被拒绝或再次失败：不要继续改瘦。用运输层现包再 validate，通过就提交。
4. 写入行仍有未识别来源、或合同声明不可执行：停止，不要出可执行写能力。

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

- `立刻办理`：读完立刻原样复制「冻结提问」JSON 一次问完整表单。不要先 ls、不要先读 `references/`、不要先跑脚本探路、不要改 `inputType`、不要自己补非日期 `default`。查询不要确认卡，写操作才确认。读完禁止再读本文件。系统字段由 runtime 按合同自动填，不要向用户要，不要让用户补合同缺省。
- `冻结提问`：每个能力一份宿主可执行的 `ask_user_question` JSON，字段 id 与 `caller_fields` 一致。一次一张完整表单，不要拆多轮。Dano 宿主没有 `table`：对象数组在提问里投影为同一 id 的 `textarea`，由 runtime 组装回数组。禁止写 `inputType: table`。无合同 default 的字段不写 `default`，不要编「请填写 / 暂无 / 请审批」。写操作日期在问句里说明页面默认当日，不要把 `today` 写进 JSON default。
- `description` 是路由触发：用合同里各能力的 `name` / `intent` 说明什么用户请求走哪条路线。用户意图对上某条能力就走该原子路线，对不上走 `default`。禁止为某个业务口令写死 `capability_id`。不要写「字段以 CONTRACT.json 为准」。
- `适用场景` 不复读 description。
- `选择工作流` 第一行 = 默认完整办理。禁止写「每次只执行一项」「不得自行串联」「一页面对应一个 Skill」。
- `组合与交接规则` 只三种：原子 / 已确认绑定 / 人手交接。
- `执行协议` 每个能力一节，必须有可判定的 `Done when:`。产出完全基于能力：该能力 `input_schema.properties` 和调用方 params 一个都不能漏，做成填写表：字段 id、标题、必填、控件、**怎么填**、**可用默认值**。数组行内列写成 `数组字段.列名`。枚举写出合同全部 id/label；动态字段写出 `--list-options <capability_id> <field>`。系统字段另行列出「不要向用户要」和**实际合同值**（如 `合同值 1`）；常量必须带 `default_value`。
- **可用默认值**只允许写合同已给出的 `default`、本对话用户已确认的值、合同枚举 id、本次 `--list-options` 选中的 id、写操作日期的页面默认当日。选中记录 / 上一步结果只能用本对话已确认的 id。没有这些就写「无可用默认值，必须向用户收集」。禁止编「无 / 示例 / 请审批」。禁止因为 execute 标了系统就把能力 schema 字段删掉。
- `按需读取资源` 写「默认不要读其它文件；只有提问失败或脚本报错才读 INPUT_FORMS」。禁止「先阅读全部 references」。
- `鉴权`：先用本包 `auth.local.json` 执行。401 / 账号未登录停问一次 token，再用 `DANO_AUTH_HEADERS` 覆盖本地过期头后重跑同一条命令。不要改文件，不要再问第二次，不要写具体 token。

调用方字段、控件、dataSource、路线以运输层物化结果为准。不要自己另写一份更瘦的表单，不要按某个页面特例改路线。动态字段先 `--list-options` 再提问，把返回的 options 写进 ask；不要把 dataSource 放进 ask_user_question。INPUT_FORMS 必须保留 dataSource。

消费者正文禁止出现：`本页面的实际操作流程`、`能力录制`、`录制结果`、`阶段1`–`阶段8`、`FlowSpec`、`fingerprint`、`x-dano`、`规划依据`、`一页面对应一个 Skill`、`原样来自`、`生成器`、`generator-guides`。

不要抄阿里产品专章：RAM、CLI 安装、Session ID / User-Agent、云账号参数。

## 检查与验证

用 `validate_skill_package` 跑结构规则和合同保真。运输层手册已经通过时不要重写。有 error 只改 **SKILL.md** 缺口，不要改冻结执行器，不要改检查器去放行。`handbook_fields` 会列出缺哪些字段 / 默认值 / 禁止的 `dataSource` JSON。

对每个能力调用 `project_contract_to_request({capability_id, inputs})`。只按合同投影。缺键失败，列出缺哪些，**不准补键**。

至少两组合法不同输入。投影必须按合同变。不能两份都等于录制原文，除非合同声明该键是 `constant`。

`validate_skill_package` 通过后立刻 `submit_skill_export`。不要反复 `run_isolated_script`。

默认不要为验证再提交业务单。

## 提交

通过 → `submit_skill_export({ok:true, skill_id, description, routes})`。
失败 → `submit_skill_export({ok:false, errors:[...]})`。
查询类可带缺口说明，但不能把缺口冻成可执行默认。不完整写能力不得发布。
