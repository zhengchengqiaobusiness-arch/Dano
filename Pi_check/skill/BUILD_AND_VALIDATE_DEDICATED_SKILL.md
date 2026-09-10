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
3. 调用 `read_skill_artifact` 看运输层已物化的 `SKILL.md`、`references/CONTRACT.json`、`references/INPUT_FORMS.md`。
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
python scripts/flow.py --route default --input-json '{...}' --confirm
python scripts/flow.py --list-options <capability_id> <field>
```

## 你只可以改 SKILL.md

frontmatter 仅非空 `name` + `description`。不要写 `version`、`compatibility`、`disable-model-invocation: false`。

`description` 是路由触发：做什么、哪些不同用户请求触发它、关键边界。不要用 action UUID、skill_id、接口路径当描述。

正文必须有：`适用场景`、`不适用场景`、`选择工作流`、`组合与交接规则`、`执行协议`、`成功、失败与停止`、`按需读取资源`、`鉴权`。

- `适用场景` 不复读 description。
- `选择工作流` 第一行 = 默认完整办理。禁止写「每次只执行一项」「不得自行串联」「一页面对应一个 Skill」。
- `组合与交接规则` 只三种：原子 / 已确认绑定 / 人手交接。
- `执行协议` 每步必须有可判定的 `Done when:`，并列出该能力全部 `caller_fields`。
- `按需读取资源` 写「何时读哪个文件」。禁止「先阅读全部 references」。
- `鉴权`：没有 `auth.local.json` 且没有环境凭证则停止，要求提供 token；401 / 账号未登录同样停问。不要写具体 token。

调用方字段、控件、dataSource、路线以运输层物化结果为准。不要自己另写一份更瘦的表单。动态字段先 `--list-options` 再提问，但 INPUT_FORMS 必须保留 dataSource。

消费者正文禁止出现：`本页面的实际操作流程`、`能力录制`、`录制结果`、`阶段1`–`阶段8`、`FlowSpec`、`fingerprint`、`x-dano`、`规划依据`、`一页面对应一个 Skill`、`原样来自`、`生成器`、`generator-guides`。

不要抄阿里产品专章：RAM、CLI 安装、Session ID / User-Agent、云账号参数。

## 检查与验证

用 `validate_skill_package` 跑结构规则和合同保真。有 error 就改 **SKILL.md**，不要改冻结执行器，不要改检查器去放行。

对每个能力调用 `project_contract_to_request({capability_id, inputs})`。只按合同投影。缺键失败，列出缺哪些，**不准补键**。

至少两组合法不同输入。投影必须按合同变。不能两份都等于录制原文，除非合同声明该键是 `constant`。

`validate_skill_package` 通过后立刻 `submit_skill_export`。不要反复 `run_isolated_script`。

默认不要为验证再提交业务单。

## 提交

通过 → `submit_skill_export({ok:true, skill_id, description, routes})`。
失败 → `submit_skill_export({ok:false, errors:[...]})`。
查询类可带缺口说明，但不能把缺口冻成可执行默认。不完整写能力不得发布。
