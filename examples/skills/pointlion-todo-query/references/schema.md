# PointLion「我的待办」查询契约

## A. 查询接口

最终 URL（默认）：

```text
GET http://admin.dianshixinxi.com:90/admin-api/bpm/task/todo-page
```

### Query 参数

| 参数 | 类型 | 必填 | 来源/语义 |
|---|---:|---:|---|
| `pageNo` | integer | 是 | 页码，从 1 开始 |
| `pageSize` | integer | 是 | 每页条数 |
| `name` | string | 否 | 页面“任务名称”，即当前任务节点名 |
| `category` | string | 否 | 页面“流程分类”，传分类 `code` |
| `processDefinitionKey` | string | 否 | 页面“所属流程”，传流程定义 `key` |
| `createTime[0]` | datetime | 否 | 页面“发起时间”的开始时间 |
| `createTime[1]` | datetime | 否 | 页面“发起时间”的结束时间 |

`createTime` 两个值应同时出现。日期时间格式：

```text
YYYY-MM-DD HH:mm:ss
```

本 Skill 使用 indexed array 形式：

```text
createTime[0]=2026-09-01 00:00:00
createTime[1]=2026-09-04 23:59:59
```

## B. 流程分类动态枚举

```text
GET /admin-api/bpm/category/simple-list
```

canonical 字段：

| 字段 | 用途 |
|---|---|
| `id` | 内部 ID，可用于识别输入，但不是 todo 查询值 |
| `name` | 页面显示名 |
| `code` | **todo 查询传给 `category` 的值** |

不要把 `name` 直接传给 `category`。

## C. 所属流程动态枚举

```text
GET /admin-api/bpm/process-definition/simple-list
```

canonical 字段：

| 字段 | 用途 |
|---|---|
| `id` | 流程定义 ID，可用于识别输入 |
| `name` | 页面显示名，如“请假申请” |
| `key` | **todo 查询传给 `processDefinitionKey` 的值** |
| `category` | 流程分类 code（如返回） |
| `categoryName` | 流程分类显示名（如返回） |
| `version` | 流程定义版本（如返回） |
| `suspensionState` | 流程定义挂起状态（如返回） |

不同分支的 `simple-list` 可能返回直接数组，也可能返回 `PageResult.list`；脚本两种都支持。

## D. BPM 系统字典枚举

```text
GET /admin-api/system/dict-data/simple-list
```

脚本会读取返回中的 `dictType / value / label`，并动态解析：

| 原始字段 | 字典类型 | 标准化标签字段 |
|---|---|---|
| `task.status` | `bpm_task_status` | `task.statusLabel` |
| `processInstance.status` | `bpm_process_instance_status` | `processInstance.statusLabel` |

流程定义 `suspensionState` 使用 Flowable 固定语义：`1=激活`、`2=挂起`。

如果字典接口临时不可用，主待办查询仍返回，数字原值不会丢；输出中的 `enumWarning` 会明确说明枚举标签未解析。

## E. 待办返回结构

服务端核心语义是“Task + processInstance”。Skill 默认保留标准化对象，也可 `--format raw` 完整保留服务端实际字段。

### 页面列映射

| 页面列 | 首选后端字段 | 兼容回退 |
|---|---|---|
| 单据编号 | `processInstance.billCode` | 仅回退到 `variables.billCode` / `processVariables.billCode`；不会把 Flowable 实例 ID 冒充业务单号 |
| 流程 | `processInstance.name` | `processInstance.processDefinition.name` |
| 摘要 | `processInstance.summary` | `processInstance.variables.summary` / task `summary` |
| 发起人 | `processInstance.startUser.nickname` | `startUser.name` / `startUserNickname` / `startUserName` |
| 发起时间 | `processInstance.createTime` | `processInstance.startTime` |
| 当前任务 | task 顶层 `name` | 无猜测回退 |
| 接收时间 | task 顶层 `createTime` | 无猜测回退 |

### 摘要

新版本常见为：

```json
[
  {"key": "请假类型", "value": "年假"},
  {"key": "天数", "value": 1}
]
```

业务表单也可能出现 `key` 为空、只有 `value` 的项。Skill 两种都能正确格式化；不会因为 `key` 为空就丢弃摘要。

## F. API 通用返回包装

脚本兼容以下成功包装：

```json
{"code": 0, "data": {}}
```

以及某些分支：

```json
{"code": 200, "data": {}}
```

并兼容：

- 直接数组
- `data.list`
- `data.records`
- `data.rows`
- `data.items`

非成功业务 code 会报错，不会把错误页当成空结果。

## G. 安全边界

- OA Token 由 Dano Credential Broker 保管；Python 仅发起相对路径请求。
- 默认只发送 GET。
- 不包含审批、驳回、转办、签收、删除、创建、修改等接口。
- `doctor` 也是只读 GET 检查。
