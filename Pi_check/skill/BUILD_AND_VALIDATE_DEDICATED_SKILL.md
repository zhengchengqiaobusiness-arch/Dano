# Build and Validate Dedicated Skill

本文件只负责：根据唯一合同生成专用 Python 执行器、SKILL.md 和表单；检查触发与渐进披露；验证合同到请求；用变化输入验证转换；验证写入与回读；验证独立环境运行；阻止不完整写能力被发布。  
禁止：回头猜页面、重新推断来源、为了能跑把录制值写成已解决常量。  
缺口只改本文件。

对 PI 的指令以本文件为准。不要读 `spec.md`、阶段号、FlowSpec、generator-guides。

## 唯一输入

只读 Infer 已提交的合同（草稿里的 capabilities / steps / links / capability_relations / unresolved）。禁止回头打开页面猜字段。

写入行仍有未识别来源、投影失败、或合同声明不可执行：停止，退回 Investigator，不要发布。

## 生成专用 Python 执行器

用 `write_skill_artifact` 写入本场导出草稿：

```text
<package>/
  SKILL.md
  scripts/
    client.py          # 不要重写；导出注入冻结副本
    <capability>.py
    verify_<capability>.py   # 仅当写操作需要回读
    wire_format.py     # 不要重写；只声明合同用过的格式
  references/
    CONTRACT.json
    CAPABILITIES.md
    OPTIONS.md
    INPUT_FORMS.md
    routes/
      <route-id>.md
```

每个能力一个命令脚本。只用合同里的 path / formula / element_template / option_source / 已声明的 wire_format。运行时只依赖冻结的 `client.py`（鉴权+HTTP）和 `wire_format.py`。依赖仅 Python + httpx。

每个脚本必须接受 `--help`，打印机器可读 JSON，不依赖 Dano 进程。不准写 token、cookie、password。不准按字段名猜 now。未识别键不准写成可执行默认。

## 生成 SKILL.md 与表单

frontmatter 仅非空 `name` + `description`。不要写 `version`、`compatibility`、`disable-model-invocation: false`。

`description` 是路由触发：做什么、哪些不同用户请求触发它、关键边界。

正文必须有：`适用场景`、`不适用场景`、`选择工作流`、`组合与交接规则`、`执行协议`、`成功、失败与停止`、`按需读取资源`。

- `适用场景` 不复读 description。
- `选择工作流` 是互斥路线表。组合细节链到 `references/routes/<id>.md`。原子路线不准叫 Agent 去读组合文件。
- `组合与交接规则` 只三种：原子 / 已确认绑定 / 人手交接。绑定空、歧义、类型或基数不对就停止自动串联。
- `执行协议` 每步必须有可判定的 `Done when:`。写入：preview → confirm → execute。
- `按需读取资源` 写「何时读哪个文件」。禁止「先阅读全部 references」。

`INPUT_FORMS.md`：只收集调用方字段；不重问系统/已绑定值；写入要确认。  
`CONTRACT.json`：消费者合同，不是录制审计。禁止写入 `capability_id`、request/step id、fingerprint、录制样本、本场人员/单据。  
`CAPABILITIES.md` 是业务能力索引，不抄完整字段表或发现历史。  
`OPTIONS.md` 说明运行时如何取候选。录制样本不是运行时默认。多结果不得默默取第一条。

消费者正文禁止出现：`本页面的实际操作流程`、`能力录制`、`录制结果`、`阶段1`–`阶段8`、`FlowSpec`、`fingerprint`、`capability_id`、`x-dano`、`规划依据`、`原子能力`、`一页面对应一个 Skill`、`原样来自`、`生成器`。

不要抄阿里产品专章：RAM、CLI 安装、Session ID / User-Agent、云账号参数。

## 检查触发与渐进披露

用 `validate_skill_package` 跑结构规则：frontmatter、必需要章节、泄漏词、明文凭据、重复 route_id。这是指定检查，不是认页面。有 error 就改产物，不要改检查器去放行。

## 验证合同 → 请求

对每个能力调用 `project_contract_to_request({capability_id, inputs})`。只按合同投影。缺键失败，列出缺哪些，**不准补键**。退回 Investigator。

## 变化输入

至少两组合法不同输入。投影必须按合同变。不能两份都等于录制原文，除非合同声明该键是 `constant`。

## 写入与回读

仅 Investigator 确认用户已授权写入时，才可投影发送或授权回放。再按 fact_check / links 回读。失败则不发布该写能力。默认不要为验证再提交业务单。

## 独立环境

用 `run_isolated_script`：隔离目录跑 `--help` 和 dry-run。不依赖 Dano。失败则不发布。

## 阻止不完整写能力发布

写入行仍 unresolved、来源未识别、投影失败、validator 失败、隔离运行失败：告诉 Investigator，不得 `submit_recording_result` 把该写能力当可执行发布。查询类可带缺口说明，但不能把缺口冻成可执行默认。
