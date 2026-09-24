# 办理流程：OA Skill 生成指南

本文档供负责核对消费者 Skill 的模型阅读。一个消费者 Skill 是本页办理生命周期，不是能力点目录。

对照公开 Skill 市场的常见形态：可单步，默认整段跑完。

可执行包由运输层从录制合同五块投影。Skill 4 不准重写 `scripts/flow.py` / `scripts/runtime.py` / `references/CONTRACT.json`。

## 默认路线

唯一输入是合同五块：`capabilities` / `steps` / `links` / `capability_relations` / `unresolved`。禁止只扫 `capabilities[]`。

1. 用 `capability_relations` 和已确认 `links` 连成最长默认办理链，`route_id=default`。
2. 只有 relation、没有值流：仍是主路线。交接点停问，不准猜传值，不准拆成两个 Skill。
3. 都没有：按 `capabilities[]` 顺序做人手交接主路线，**仍然要有能跑的 default**。
4. 每个能力另留原子路线，只服务「用户意图对上该能力 name / intent」。禁止为某个业务口令写死 capability_id。

`CONTRACT.json` 必须有 `routes[]`。合同里有 ≥2 个能力时，必须有一条多步默认路线。这些由运输层写出；你只核对，不要另编一份。

## 冻结入口

```text
python3 scripts/flow.py --route default --input-json '{...}' --confirm
python3 scripts/flow.py --list-options <capability_id> <field>
```

- `--route` 选择路线；缺省即 default。
- 已确认 links 自动带值；无绑定停问。
- 写步骤必须 `confirm:true + formIds[]`，执行时加 `--confirm`。
- 任一步失败即停，不得跳过写操作或用查询结果假装办理完成。
- 只认 `client.http_json(query=, body=)`。禁止发明 `client.request`。

## `SKILL.md`「选择工作流」

第一行必须是默认完整办理。后面才是原子行。

禁止成品出现：

- 「每次只执行一项」
- 「不得自行串联」
- 「一页面对应一个 Skill」

「组合与交接规则」只允许三种：原子 / 已确认绑定 / 人手交接。绑定空、歧义、类型或基数不对就停止自动串联。

## 路线文档

每条路线一份 `references/routes/<route-id>.md`：何时用、步骤、自动带入、停问点、是否要确认。不要把 capability_id 当用户可见名称。运输层已写好，不要覆盖成更瘦的版本。
