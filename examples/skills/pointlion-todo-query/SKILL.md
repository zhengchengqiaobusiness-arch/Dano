---
name: pointlion-todo-query
description: 只读查询 PointLion OA「工作台 > 我的待办」。支持任务名称、流程分类、所属流程、发起时间、分页；流程分类和流程定义从服务端动态枚举并自动把中文名称解析为 category code / processDefinitionKey。适用于 admin.dianshixinxi.com:90 的 PointLion/Yudao BPM 接口。
---

# PointLion 我的待办查询 Skill

这是一个**只读 Skill**。只调用 GET 查询接口，不包含审批、驳回、转办、删除、创建或修改操作。

## 1. 在 Dano 中执行

先通过 OA 登录 Dano，再通过受控 bash 运行本 Skill 的 Python 脚本。Dano 在每次执行期间提供 `dano_provider` 客户端；脚本发起的请求由 Credential Broker 使用发起 Assistant Turn 的 Dano Login Session 认证。无需配置 token。

认证失败时提示用户重新登录。保留 Python 的实际接口调用，不用模型逐个调用 provider_request 代替脚本。脚本仅查询四个 GET 接口：待办分页、流程分类、流程定义和系统字典。

## 2. 请求目标

OA origin 由 Dano 的服务端配置决定。业务接口路径和可选 tenant-id 在 `scripts/config.py` 配置；tenant-id 仅用于业务路由，不用于选择登录身份。

## 3. 页面筛选字段与请求参数

必须严格按下面映射，不要猜字段：

| 页面字段 | 请求参数 | 说明 |
|---|---|---|
| 任务名称 | `name` | 当前待办任务/节点名称，例如“领导审批” |
| 流程分类 | `category` | **传分类 code，不传分类中文名** |
| 所属流程 | `processDefinitionKey` | **传流程定义 key，不传流程中文名** |
| 发起时间 | `createTime[0]`, `createTime[1]` | 开始/结束时间，`YYYY-MM-DD HH:mm:ss` |
| 页码 | `pageNo` | 从 1 开始 |
| 每页条数 | `pageSize` | 正整数 |

页面中的“流程”与“当前任务”不是同一个字段：

- “流程”示例：`请假申请`
- “当前任务”示例：`领导审批`

所以“查询请假申请”应优先解析到 `processDefinitionKey`；“查询领导审批节点”才使用 `name`。

## 4. 动态枚举：不要硬编码

流程分类和所属流程都是**服务端实时枚举**，本 Skill 不维护脆弱的静态表。

### 查看所有枚举

```bash
python3 scripts/pointlion_todo.py enums --format table
```

或机器可读 JSON：

```bash
python3 scripts/pointlion_todo.py enums --format json
```

同时查看 `bpm_*` 系统字典：

```bash
python3 scripts/pointlion_todo.py enums --with-dicts --format table
```

枚举语义：

- 流程分类：显示 `name`，查询传 `code`
- 所属流程：显示 `name`，查询传 `key`

### 中文名称自动解析

```bash
python3 scripts/pointlion_todo.py resolve \
  --category '行政流程' \
  --process '请假申请'
```

解析顺序：

1. canonical code/key 精确匹配
2. 中文/显示名称精确匹配
3. ID 精确匹配
4. 规范化匹配（忽略空格、`_`、`-` 等）
5. 唯一模糊包含匹配

如果匹配到多个候选，**直接报错并列出候选，不猜测**。

## 5. 查询示例

### 按中文枚举查询（推荐）

```bash
python3 scripts/pointlion_todo.py query \
  --task-name '领导审批' \
  --category '行政流程' \
  --process '请假申请' \
  --start-time '2026-09-01' \
  --end-time '2026-09-04' \
  --page 1 \
  --page-size 20
```

其中：

- `--category` 会自动查询枚举并转换成 `category=<code>`
- `--process` 会自动查询枚举并转换成 `processDefinitionKey=<key>`
- 只给日期时，开始日期自动补 `00:00:00`，结束日期自动补 `23:59:59`

### 已知 code/key 时跳过枚举解析

```bash
python3 scripts/pointlion_todo.py query \
  --category-code 'OA' \
  --process-key 'leave' \
  --page 1 --page-size 20
```

### 直接使用截图中的中文字段名

Skill 支持中文参数：

```bash
python3 scripts/pointlion_todo.py query \
  --任务名称 '领导审批' \
  --流程分类 '行政流程' \
  --所属流程 '请假申请' \
  --开始时间 '2026-09-01' \
  --结束时间 '2026-09-04'
```

### JSON 条件（适合 Agent 调用）

```bash
python3 scripts/pointlion_todo.py query --query-json '{
  "任务名称":"领导审批",
  "流程分类":"行政流程",
  "所属流程":"请假申请",
  "发起时间":["2026-09-01","2026-09-04"],
  "页码":1,
  "每页":20
}'
```

未知 JSON 字段会报错，不会被静默忽略。

## 6. 返回字段

默认输出会把后端 task 原始结构标准化为：

```text
display.单据编号
display.流程
display.摘要
display.发起人
display.发起时间
display.当前任务
display.接收时间

task.id
task.name
task.status
task.statusLabel
task.taskDefinitionKey
task.createTime
task.endTime
task.durationInMillis
task.reason
task.processInstanceId
task.assigneeUser
task.ownerUser

processInstance.id
processInstance.name
processInstance.billCode
processInstance.summary
processInstance.status
processInstance.statusLabel
processInstance.category
processInstance.categoryName
processInstance.businessKey
processInstance.createTime
processInstance.startTime
processInstance.endTime
processInstance.startUser
processInstance.processDefinition.id
processInstance.processDefinition.key
processInstance.processDefinition.name
processInstance.processDefinition.version
processInstance.processDefinition.suspensionState
processInstance.processDefinition.suspensionStateLabel
```

页面显示字段的优先映射详见 `references/schema.md`。

如果后端增加了新字段、或你需要 100% 原样数据：

```bash
python3 scripts/pointlion_todo.py query --format raw
```

或在标准化结果里同时带上完整原始 task：

```bash
python3 scripts/pointlion_todo.py query --include-raw
```

因此不会因为 Skill 没预先声明某个自定义字段而丢失数据。

## 7. 上线前自检

通过 OA 登录 Dano 后，先执行：

```bash
python3 scripts/pointlion_todo.py doctor
```

`doctor` 只进行 GET 请求，它会检查：

- 当前 Login Session 的认证及可选 tenant-id 是否可用
- 分类枚举接口是否可用，并能识别 `name + code`
- 流程定义接口是否可用，并能识别 `name + key`
- 系统字典接口是否可用，并能识别 BPM 状态枚举
- 待办分页接口是否可用
- 当前服务真实返回的数据能识别哪些页面字段

四个接口全部成功时返回退出码 `0`；任一失败返回 `2`。

## 8. Agent 使用规则

当用户用自然语言要求查询待办：

1. 先识别是否包含“任务名称 / 流程分类 / 所属流程 / 发起时间 / 页码 / 每页条数”。
2. 用户给的是流程分类中文名时，使用 `--category`，让脚本动态解析到 code。
3. 用户给的是流程中文名时，使用 `--process`，让脚本动态解析到 key。
4. 不要把“请假申请”误当作任务名称；除非用户明确说它是当前任务节点。
5. 用户未指定条件时，只传分页，不臆造过滤条件。
6. 用户说“所有”时按分页循环查询；每次保留 `pageNo/pageSize`，直到累计达到 `total`。
7. 展示业务结果时优先使用 `display`；程序处理或字段核验时使用 `--include-raw` / `--format raw`。
8. 枚举解析歧义时，把脚本列出的候选交给用户选择，不擅自选一个。
9. 不调用任何非 GET 接口。

## 9. 验收

在 Dano 实际运行环境中运行 `doctor` 和 `query --page 1 --page-size 1`。通过条件为 doctor 退出码 0、四个接口 HTTP 200 且业务 code 0，以及 query 返回有效的列表和 total。空待办也是有效结果。

保留 doctor 的 responses、脚本退出码以及 bash 结果中的 providerRequests 作为证据，只展示状态与数量。模型直接调用 provider_request 成功不能替代 Python 链路验收。

Python 3.9+，由 Dano 注入客户端，无第三方 Python 依赖。此目录是用户提供的 3.0.0 Skill 的 Dano 适配版；原包与来源见 README。
