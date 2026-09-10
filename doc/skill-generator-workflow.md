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

## 执行协议必须按路线分支

`SKILL.md` 的「执行协议」不能只写默认完整链。合同里每条 `routes[].route_id` 都要有对应步骤，且每步带可判定的 `Done when:`。

- 用户只要其中一项（例如「日报填写」「只查统计」）：走对应原子路线，**禁止**把其它能力的选项接口或查询步骤当成前置。
- 默认完整路线：按 relation / links 顺序执行；人手交接处停问。查询结果的日期、部门、统计周期**不得**自动当成写入字段。
- 系统只读栏（所属单位、所属部门、由 URL 决定的类型等）不进 `ask_user_question`。
- 可增行对象数组用 `inputType: table`，不要用换行文本冒充数组，也不要因此进入反复追问的死循环。

「按需读取资源」必须写清：完整办理跑 `python scripts/flow.py --route <default>`；只要单步则跑对应 `--route` 或能力脚本。

## 能力脚本只打冻结 client

能力脚本和 `flow.py` 只允许：

```text
from client import http_json
from client import execute_plan
from client import option_choices
```

禁止 `get_session`、自造 Session、`requests`、`provider_request`、翻其它 tenant 的 `~/.dano/sessions/`、改 `client.py`、现场 `pip install`。

有 `option_source` / `dataSource` 的能力脚本必须支持 `--list-options <字段>`，与业务请求走同一套 `auth_headers()`。

隔离验证不能只跑 `--help`：必须检查源码不含 `get_session`，并跑 dry-run（无 `--confirm` → `need_confirm`；有 `--confirm` 但无鉴权 → `authentication unavailable`）。

## 成功、失败与停止

Skill 4 正文必须有「成功、失败与停止」，写清对用户说什么、脚本退出什么：

- 空鉴权（`auth.local.json` 无头、无 `DANO_AUTH_HEADERS`、Dano token API 与**当前 tenant** 会话缓存都没有）：停止。不要继续编表单去提交。
- 没有 httpx：冻结 `client.py` 回退标准库 urllib；不要装包、不要换通道。
- 选项接口失败：停问，不用录制样本。
- 用户取消提问：停止当前流程。
- 写接口业务失败：报告 `msg`，不改用其它 URL 重试。
