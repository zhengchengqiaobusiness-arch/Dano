# 录制能力识别 Skill

PI 是唯一语义决策者；旧录制逻辑绝不启动。

本 Skill 只指导你如何从原始证据产出完整能力合同。代码不会替你划分能力、推断字段或编排顺序。

你的最终提交必须让现有录制页能直接展示能力。`submit_recording_result.result` 本身就是前端 `draft`。没有非空 `capabilities` 就等于没有产物。

## 代码 / Skill / 模型

- 代码：启动浏览器、原样采集证据、冻结、把你的 result 原样交给前端。不识别页面。
- Skill（本文件）：跨页面通用的识别方法。改识别策略就改本 Skill，不要改采集代码。
- 模型（你）：阅读本场证据，判断“这是几个独立业务动作”、每个字段谁提供、请求怎么串起来。不同页面由你现场判断，不要套死某个系统的 URL。

## 现有录制页实际读取的合同

现有页面**不会**改，也**不会**读你私自发明的字段袋。它只从下面这些位置渲染“调用方提供 / 系统自动处理 / 执行编排”：

1. `capabilities[].request_refs`：必须是对象数组，每项至少有 `step_id`、`usage`。可选再写 `method`、`path`、`sequence`。
2. `capabilities[].step_ids`：执行步的 `step_id` 列表，须与 `request_refs` 里 `usage=execute` 的步骤一致。
3. `capabilities[].input_schema.properties`：调用方字段的 JSON Schema。页面用它画“调用方提供”。
4. `steps[].params`：必须是**字段对象数组**。页面用它画“系统自动处理”，并和 `input_schema` 对齐。

页面**完全忽略**：

- `capabilities[].fields`
- 把 `request_refs` 写成请求 ID 字符串（例如 `"req_abc"`）
- 把 `steps[].params` 写成 `{ "billCode": "1" }` 这种键值映射

这三种写法都会让页面显示“调用方提供 0 / 系统自动处理 0 / 编排只有空的关联请求”，即使你在别处写全了字段。**不要写它们。**

`usage` 只允许：

- `execute`：真正完成该能力的主请求
- `preflight`：打开表单、带出记录、进入可写状态
- `option_source`：选项/枚举来源
- `fact_check`：提交后回读或核验

## 能力怎么切

一项能力 = 操作人可以单独发起的一个业务动作。

- 查询、创建、编辑、删除、提交、撤回、导出、审批，通常各是一项能力。
- 同一次动作带出的预填、下拉、刷新、回读，不是新能力；挂到该能力的 `request_refs`。
- 不要按 HTTP 方法或 URL 数量机械切分。
- 不要把页面加载时的全部流量都做成能力。
- 不要把登录、验证码、租户查询、权限菜单做成业务能力的步骤，除非本场录制的业务动作本身就是登录。
- 不要合并两个独立业务动作。
- 拿不准时写入 `unresolved`，不要猜一个假能力。

## 字段必须出现在页面能读到的两个位置

每个业务字段必须同时给出：

- 名称（`key` / `label`）
- 类型（`string` / `number` / `boolean` / `date` / `datetime` / `enum` / `list-enum` / `object` / `array`）
- 来源（`source_kind`）
- 必填性（`required`）
- 调用方还是系统处理（`exposed_to_user`）
- 线上路径（`path`，例如 `query.billCode`、`body.title`）

并写进：

1. **关联 execute（以及真正带上该字段的）step 的 `params` 数组**，每项都是对象：
   `{ "key", "path", "label", "type", "source_kind", "exposed_to_user", "required" }`
   枚举再加 `enum_options: [{ "label", "value" }]`。系统常量可加 `default_value` / `value`。
2. **调用方字段**还要写进该能力的 `input_schema.properties`。`title` 用中文标签。分页字段（`pageNo` / `pageSize` / `limit` / `offset`）不要放进 `input_schema`，只放进 step `params`，且 `exposed_to_user=false`。

判断调用方还是系统：

- 调用方提供 `exposed_to_user=true`：人要输入、选择、确认的值。来源常见 `caller_input` / `user_input` / `page_enum` / `api_option` / `page_default`（可改预填）。
- 系统自动处理 `exposed_to_user=false`：登录态、Cookie、上游响应绑定、计算值、录制原值常量、运行时生成、分页。来源常见 `session` / `constant` / `previous_response` / `computed` / `generated` / `page_default`。

人在筛选框、表单、下拉里填过或选过的值，必须是调用方字段，不能丢。  
不要把系统字段标成调用方，也不要把调用方必填标成系统自动。  
不要把密码、token、Cookie、Authorization 的真实值写进 result。

## 编排

- `request_refs[].step_id` 必须等于 `steps[].step_id`，不要填 `request_id`。
- `request_id` 只允许出现在 `request_facts.requests`。
- 能力内顺序：`preflight` → `option_source` → `execute` → `fact_check`。
- 跨能力：被依赖的查询/选择在前，写入在后。
- `links` 必须用 `source_step_id` / `source_path` / `target_step_id` / `target_path` 写出上游响应如何进入下游参数。不要只写一句“有依赖”。

## 提交形状（必须按这个信封交）

```json
{
  "title": "本场能力标题",
  "capabilities": [
    {
      "capability_id": "cap_example_search",
      "name": "搜索列表示例",
      "title": "搜索列表示例",
      "intent": "按调用方给出的筛选条件查询业务列表",
      "kind": "query",
      "step_ids": ["step_search"],
      "request_refs": [
        { "step_id": "step_options", "usage": "option_source", "method": "GET", "path": "/api/options", "sequence": 1 },
        { "step_id": "step_search", "usage": "execute", "method": "GET", "path": "/api/items", "sequence": 2 }
      ],
      "input_schema": {
        "type": "object",
        "properties": {
          "keyword": { "type": "string", "title": "关键字" },
          "status": { "type": "string", "title": "状态", "enum": ["1"], "x-dano-business-type": "single_enum" }
        },
        "required": []
      }
    }
  ],
  "steps": [
    {
      "step_id": "step_options",
      "name": "加载筛选项",
      "method": "GET",
      "path": "/api/options",
      "params": []
    },
    {
      "step_id": "step_search",
      "name": "搜索列表",
      "method": "GET",
      "path": "/api/items",
      "params": [
        {
          "key": "keyword",
          "path": "query.keyword",
          "label": "关键字",
          "type": "string",
          "source_kind": "user_input",
          "exposed_to_user": true,
          "required": false
        },
        {
          "key": "status",
          "path": "query.status",
          "label": "状态",
          "type": "enum",
          "source_kind": "page_enum",
          "exposed_to_user": true,
          "required": false,
          "enum_options": [{ "label": "进行中", "value": "1" }]
        },
        {
          "key": "pageNo",
          "path": "query.pageNo",
          "label": "页码",
          "type": "number",
          "source_kind": "page_default",
          "exposed_to_user": false,
          "required": true,
          "default_value": 1
        }
      ]
    }
  ],
  "links": [
    {
      "source_step_id": "step_options",
      "source_path": "body.data",
      "target_step_id": "step_search",
      "target_path": "query.status"
    }
  ],
  "request_facts": { "requests": [] },
  "unresolved": []
}
```

`result` 还须包含：业务理解、成功/失败条件、回读方法、证据引用。这些是说明，不能代替上面的信封。

## 提交前自检

1. 每个能力都有 `input_schema.properties`（若该动作确有调用方字段）或明确没有调用方字段。
2. 每个能力的 `request_refs` 都是 `{step_id, usage}` 对象，并能在 `steps` 里找到同名 `step_id`。
3. 每个 step 的 `params` 都是数组，数组元素都有 `key` 和 `path`。
4. 结果里没有 `capabilities[].fields`。
5. 人填过的筛选/表单/下拉都出现在“调用方提供”能读到的位置。
6. 登录态和分页只出现在系统字段里。

## 泛化

- 本 Skill 不绑定任何具体业务页。
- 只根据本场点击、输入、请求、响应、截图判断。
- 换一个页面也走同一套切分/编排/字段规则。
- 最终 `result` 必须是完整、可编排、可展示的能力合同，不能只交一篇分析散文，也不能只交页面读不到的私有结构。
