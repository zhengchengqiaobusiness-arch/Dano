# 办理流程：OA Skill 生成指南

本文档供负责核对消费者 Skill 的模型阅读。一个消费者 Skill 是本页办理生命周期，不是能力点目录。

对照公开 Skill 市场的常见形态：可单步，默认整段跑完。

可执行包由运输层从录制合同五块投影。Skill 4 不准重写 `scripts/flow.py` / `scripts/runtime.py` / `references/CONTRACT.json`。

## 默认路线

唯一输入是合同五块：`capabilities` / `steps` / `links` / `capability_relations` / `unresolved`。禁止只扫 `capabilities[]`。

1. 用 `capability_relations` 和已确认 `links` 连成最长默认办理链，`route_id=default`。合同里同时有列表查询和点结果看详情时，默认链必须包含详情，禁止压成查询→写入。
2. 只有 relation、没有值流：仍是主路线。交接点停问，不准猜传值，不准拆成两个 Skill。
3. 都没有：按 `capabilities[]` 顺序做人手交接主路线，**仍然要有能跑的 default**。
4. 每个能力另留原子路线。选路：matched = 本包能力里 name / title / intent 被当前说法覆盖到的集合。1 个 → 只跑该原子路线。≥2 个 → 取 `default.steps` 里覆盖全部 matched 的最短连续切片。0 个且已选本 skill → 按 `default.steps` 全程逐步办理。禁止为某个业务口令写死 capability_id。
5. 每步先填槽。读能力禁止确认卡：说法、合同身份、上一步表能填的立刻执行。合同调用方必填尚未确定 → 必须问缺槽，不要停。不要改口问合同已标 current_user / selected_record 的筛选。写能力把该能力冻结提问 JSON 整份带上（禁止删 id，禁止改问句，只改 default），提交后再 `confirm:true + formIds[]`。禁止一上来 `--route default`，也禁止跳过切片外的步骤。

`CONTRACT.json` 必须有 `routes[]`。合同里有 ≥2 个能力时，必须有一条多步默认路线。这些由运输层写出；你只核对，不要另编一份。

`input_schema` / `caller_fields` 不得出现该能力系统 params 的同名键。`dataSource.endpoint` 不得等于另一能力的 execute path。出现即合同不可执行，停止出包，回到录制用原 id 重交。

## 冻结入口

```text
python3 scripts/flow.py --route default --input-json '{...}' --confirm
python3 scripts/flow.py --list-options <capability_id> <field>
```

- `--route` 选择路线；缺省即 default。调用方不要一上来 `--route default`：按 matched / 切片逐步 `--route <capability_id>`。
- 动态字段仅当尚未确定时才跑 `--list-options <capability_id> <field>`，把返回的 `options` 写进该字段后再 `ask_user_question`。说法能唯一对上则直接用 id。禁止把无 `options` 的 `treeSelect` / `select` 直接问出去。不要把 `dataSource` 放进 ask。
- 已确认 links 自动带值；无绑定停问。
- 写步骤必须整份带上冻结提问 JSON（禁止删 id），再 `confirm:true + formIds[]`，执行时加 `--confirm`。读步骤禁止确认卡。
- 任一步失败即停，不得跳过写操作或用查询结果假装办理完成。读步骤成功必须先发原始结果表，再进入下一步。
- 只认 `client.http_json(query=, body=)`。禁止发明 `client.request`。
- 有合法来源必须写入 `default`。读能力：已确定的不问；调用方必填未定必须问，不要停。写能力：无合法值也留在全量确认表里。禁止编「暂无 / 请填写 / 请审批」。
- 系统栏写着 `page_default` 却没有 `default_value`：合同不可执行，停止，不要让用户补键，不要改 `CONTRACT.json`。

## `SKILL.md`「选择工作流」

第一行必须是默认完整办理。后面才是原子行。立刻办理里写 matched / 切片算法。

禁止成品出现：

- 「每次只执行一项」
- 「不得自行串联」
- 「一页面对应一个 Skill」

「组合与交接规则」只允许三种：原子 / 已确认绑定 / 人手交接。绑定空、歧义、类型或基数不对就停止自动串联。

## 路线文档

每条路线一份 `references/routes/<route-id>.md`：何时用、步骤、自动带入、停问点、是否要确认。不要把 capability_id 当用户可见名称。运输层已写好，不要覆盖成更瘦的版本。
