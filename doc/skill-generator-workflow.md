# 办理流程：生成期规范

本文档只约束 Skill 4 如何把合同编成可一起执行的办理流程。生成期必须阅读；成品 **不得** 携带本文档。

一个消费者 Skill 是本页/本业务对象的办理流程，不是能力目录。对照阿里云 Agent Skills：命令可单步调用，默认是整段生命周期。

Infer 已经切好的能力不要合并、不要删、不要改字段。Skill 4 只编译顺序和交接。

## 默认路线

用合同五块一起编，禁止只扫 `capabilities[]`：

1. 已确认的 `capability_relations` 与 `links` 连成最长链。
2. 没有值流的 relation 也是 handoff 主路线，不是散点。
3. 没有 relation / links 时，按合同 `capabilities[]` 顺序写成一条人手交接主路线，仍然必须能被 `flow.py` 执行。
4. 每个能力另保留一条原子路线，只服务「只要查一下 / 只要删这条」。

`SKILL.md` 的「选择工作流」**第一行必须是默认完整办理**。原子行放后面。

禁止成品出现：

- 每次只执行一项
- 不得自行串联
- 一页面对应一个 Skill

没有已确认绑定就在交接点停问。不要猜传值，不要把查询结果默认为第一条，不要因此拆成互不相关的 skill。

## `scripts/flow.py`

Skill 4 必须写出 `scripts/flow.py`，按 `references/CONTRACT.json` 的 `routes[]` 执行：

```text
python scripts/flow.py --help
python scripts/flow.py --route <route_id> --input-json '{...}'
python scripts/flow.py --route <route_id> --input-json '{...}' --confirm
```

规则：

- `--route` 缺省时跑默认完整路线（工作流表第一行）。
- 按 `operation_sequence` 顺序调用对应能力脚本。
- 已确认 binding 从上游输出写入下游输入。
- 写步骤必须已带 `--confirm`，否则停在 `need_confirm`。
- 交接点、候选不唯一、用户取消、任一步失败：立即停止，不跑后续写入。
- 打印机器可读 JSON。只依赖 Python、httpx 和冻结的 `client.py`。

## 交接三种

1. 原子：只补该操作缺少的调用方字段。
2. 确认绑定：只有合同列出的 binding 自动带入。
3. 人手交接：先做完前一步，再问下一步必要输入。

写入仍是 preview → confirm → execute。`Done when:` 必须可判定。
