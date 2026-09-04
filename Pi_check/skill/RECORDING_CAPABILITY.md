# 录制能力识别 Skill

PI 是唯一语义决策者；旧录制逻辑绝不启动。

本 Skill 只指导你如何从原始证据产出完整能力合同。代码不会替你划分能力、推断字段或编排顺序。

你的最终提交必须让现有录制页能直接展示能力。`submit_recording_result.result` 本身就是前端 `draft`。没有非空 `capabilities` 就等于没有产物。

## 代码 / Skill / 模型

- 代码：启动浏览器、原样采集证据、冻结、把你的 result 原样交给前端。只拒收页面读不到的信封、重复的 `capability_id`、两个能力共用同一个 `execute`。不识别页面，不补能力。
- Skill（本文件）：跨页面通用的识别方法。改识别策略就改本 Skill，不要改采集代码，也不要改现有录制页。
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

- `execute`：真正完成该能力的主请求。每个能力必须恰好一个，且不得与其它能力共用。
- `preflight`：打开表单、带出记录、进入可写状态
- `option_source`：选项/枚举来源
- `fact_check`：提交后回读**该业务对象**（例如按 ID 取详情）。不要把“只带分页的列表刷新”挂进来，否则页面会把页码/每页条数画进该能力的系统字段。

## 先建动作台账，再切能力

提交前必须先从 `interaction` 和随后的业务请求列出台账。一项能力 = 操作人可以单独再发起的一个业务动作。

通用动作词（换页面也用这一套，不要写成某系统专用）：

- 搜索 / 查询 / 筛选
- 新增 / 创建 / 保存草稿
- 查看 / 详情
- 进度 / 审批 / 流程
- 编辑 / 修改
- 提交 / 送审
- 撤回 / 撤销
- 删除
- 导出

规则：

1. 台账里每个独立动作，要么有一项能力，要么写入 `unresolved` 并说明缺什么证据。只写在 `business_understanding` 里不算提交。
2. **不要按 URL 或 HTTP 方法合并。** 同一条接口可以服务不同动作。点了「新增」再提交，和点了「编辑」再提交，即使都是同一个 POST，也是两项能力，必须有两个不同的 `capability_id` 和两个不同的 `execute` step。
3. 点了「查看」或「进度」，且随后有返回该记录/该流程的读请求，就是独立读能力，不要并进创建或删除。
4. 同一次动作带出的预填、下拉、打开表单、提交后按 ID 回读，不是新能力；挂到该能力的 `request_refs`。
5. 不要把页面加载时的全部流量都做成能力。
6. 不要把登录、验证码、租户查询、权限菜单做成业务能力的步骤，除非本场录制的业务动作本身就是登录。
7. 不要把同一个动作交两次（两个几乎相同的撤回/删除）。
8. `capability_id`、`name`、`title` 必须能区分动作，禁止两个能力共用一个 `capability_id`。
9. 拿不准时写入 `unresolved`，不要猜一个假能力，也不要丢掉已经点过的真动作。

## 字段必须出现在页面能读到的两个位置

每个业务字段必须同时给出：

- 名称（`key` / `label`）。`input_schema.properties` 的键必须等于对应 param 的 `key`。
- 类型（`string` / `number` / `boolean` / `date` / `datetime` / `enum` / `list-enum` / `object` / `array`）
- 来源（`source_kind`）
- 必填性（`required`）
- 调用方还是系统处理（`exposed_to_user`）
- 线上路径（`path`，例如 `query.billCode`、`body.title`）

并写进：

1. **该字段真正出现的 step 的 `params` 数组**，每项都是对象，至少包含：
   `{ "key", "path", "label", "type", "source_kind", "exposed_to_user", "required", "reason" }`
   `reason` 必须是一句完整处理说明，导出 Skill 只抄这里，不会替你编规则。
2. **只有调用方字段**写进该能力的 `input_schema.properties`。`title` 用页面上的中文标签。分页字段不要放进 `input_schema`。

每个字段的 `reason` + `source` 必须写清「运行时怎么处理」，不能只写来源标签：

- `user_input`：人在哪个控件填写；空值是否仍提交。
- `page_enum`：选项来自页面本身；`enum_options` 必须列出**当场下拉里看到的全部** `{label,value}`。写清是否完整。
- `api_option`：`source` 写 `source_method`、`source_url`、`label_key`、`value_key`；`enum_options` 写本场实际返回的选项。写清 `options_complete=true/false`（只截到一页就标 false）。调用方选显示值，提交接口值。
- `page_default` / 可改的 `previous_response`：默认从哪一步哪条路径来（`from_step_id` + `from_path`），**调用方可以改**。编辑弹层里的日期、下拉、数字都属于这类，即使本场没改。
- 只读回填的 `previous_response`：从哪一步哪条路径来，提交时原样带回，调用方不能改。
- `selected_record_identity`：从列表哪一次点击/当前行哪个字段带出，提交到哪个 path。
- `computed`：计算规则写进 `reason` 和 `source.formula`。用页面标签和字段 key 写关系，例如「明细金额 = 数量 × 产品单价」「税额 = 金额 × 税率 / 100」。证据里看不出公式就写入 `unresolved`，禁止只写“自动计算”。
- `generated`：谁生成、何时生成（例如保存后服务端生成单号）。
- `constant`：固定值是什么、代表什么业务含义（例如 20=已审核）。
- `selected_option_field`：随哪一个选项接口的哪一个字段带出。

栏位在左还是在右不重要，**说明必须能让导出直接用**。缺少公式、缺少选项接口、缺少 `{label,value}`、或把可改字段写成不可改，都算出品不完整。

判断调用方还是系统（看控件，不看你是否刚好改过它）：

- 调用方提供 `exposed_to_user=true`，且**只这些**写进 `input_schema.properties`。
- 系统自动处理 `exposed_to_user=false`，只写在 `steps[].params`，**禁止**再写进 `input_schema`。页面用 `exposed_to_user` 分两栏；写进 schema 又标系统，旧逻辑会把系统字段画进调用方。

看页面上的控件，不看业务名：

- 白底可改的输入、日期、数字、下拉、单选、附件 → 调用方。编辑弹层里同样可改的字段，即使本场只改了备注，仍是调用方；来源用 `page_default` / `previous_response` / `api_option`（上游默认，可修改）。
- 灰底只读、保存时自动生成的单号、合计行、金额/税额/优惠后金额这类算出来的格子 → 系统。来源 `computed` / `generated` / `previous_response`。不要标成“自动计算，可修改”，也不要放进 `input_schema`。
- 列表行点进查看/编辑/删除/审批时带出的主键、明细行 ID、流程实例 ID → `selected_record_identity` 或 `previous_response`，**系统**。不要放进 `input_schema`。
- 选项接口顺便带回的显示名、条码、库存、单位等，人不能单独填 → 系统，`selected_option_field` / `previous_response`。
- 打开编辑时 GET 详情回填、提交时原样带回、表单上根本没有的字段（创建人、创建时间、入库数）→ 系统。
- 登录态、Cookie、分页、流程定义 Key、单据类型 → 系统。

更细的来源：

- 人在输入框里键入 → `user_input`，调用方。
- 人在页面下拉/单选里选、选项写死在页上 → `page_enum`，调用方，必须带 `enum_options`。
- 选项来自当场请求 → `api_option`，调用方；该请求才能挂 `option_source`。
- 日期选择器 → `date` / `datetime`，不要因为请求体是时间戳就写成 `integer`。`input_schema` 的 type 必须和 param 一致。
- 从刚创建/刚查看/列表当前行带出的 `id`、`processInstanceId` → 系统。
- 撤回原因、删除确认之外人另外填写的说明 → 调用方。

完整性：

- execute 请求体/查询里的每个业务字段都要出现在该 step 的 `params`，不要只写人改过的那几个。
- 表单上看得见、请求里也带着的灰框字段，要作为系统字段留下，不要丢。
- 表单上有、请求没带的只读提示（例如“保存时自动生成”）写成 `generated` 系统字段。
- `option_source` 只挂**这个能力的表单**上真实存在的下拉。列表筛选的“创建人”选项不要挂到新增/编辑。页面加载时的权限/字典/菜单不要挂进业务能力。

人在筛选框、表单、下拉里能填或能选的值，必须是调用方字段，不能丢。  
不要把系统字段标成调用方，也不要把调用方必填标成系统自动。  
不要把密码、token、Cookie、Authorization 的真实值写进 result。

## 编排

- `request_refs[].step_id` 必须等于 `steps[].step_id`，不要填 `request_id`。
- `request_id` 只允许出现在 `request_facts.requests`。
- 每个能力恰好一个 `execute`；该 `step_id` 不得出现在另一个能力的 `execute`。
- 能力内顺序：`preflight` → `option_source` → `execute` → `fact_check`。`request_refs[].sequence` 必须按这个实际执行顺序编号；页面按 sequence 展示，不要把取详情的 preflight 排到选项接口后面又把 sequence 写反。
- 跨能力：被依赖的查询/选择/创建在前，写入、撤回、删除在后。
- `links` 必须用 `source_step_id` / `source_path` / `target_step_id` / `target_path` 写出**值怎么流**。例如创建响应里的流程实例 ID 进入撤回参数。不要把常量写成“上游映射”，也不要只写一句“有依赖”。
- 列表刷新如果只带 `pageNo`/`pageSize`、没有新的业务字段：写进 `readback_method`，不要挂进 `request_refs`。

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
          "reason": "页面状态下拉，选项来自页面本身；提交 query.status。",
          "enum_options": [{ "label": "进行中", "value": "1" }],
          "source": { "options_complete": true }
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

`result` 还须包含：业务理解、成功/失败条件、回读方法、证据引用。这些是说明，不能代替上面的信封，也不能代替台账里漏掉的能力。

## 提交前自检

1. 先列出本场点过的独立业务动作。数量必须等于 `capabilities` + 仍缺证据的 `unresolved`。
2. 每个能力都有互不相同的 `capability_id`、`name`、`title`，以及恰好一个不与其它能力共用的 `execute`。
3. 每个能力的 `request_refs` 都是 `{step_id, usage}` 对象，并能在 `steps` 里找到同名 `step_id`。
4. 每个 step 的 `params` 都是数组，数组元素都有 `key` 和 `path`。
5. 结果里没有 `capabilities[].fields`。
6. 人能填/能选的筛选、表单、下拉都在调用方字段里，并且都在 `input_schema`；灰框/计算/自动编号/行主键只在 params 且 `exposed_to_user=false`。
7. 从列表行或上一步响应带出的主键/流程实例 ID 是系统字段，不是调用方输入，不要写进 `input_schema`。
8. 登录态和分页只出现在真正执行查询的那个能力的系统字段里，不要污染撤回/删除。
9. 写过“还做了查看/编辑/进度”却没有对应能力，就是失败，必须补能力或写入 `unresolved`。
10. 每个 `option_source` 都能对上该能力的一个调用方下拉。对不上的选项请求不要挂。
11. 日期字段在 schema 和 params 都是 `date`/`datetime`。execute 体里的业务字段没有漏。
12. 每个 param 都有 `reason`。计算字段有公式，接口枚举有 URL 和本场选项，页面枚举有完整 `{label,value}`，回填字段写明从哪一步哪条路径来、能不能改。

## 泛化

- 本 Skill 不绑定任何具体业务页。
- 只根据本场点击、输入、请求、响应、截图判断。
- 换一个页面也走同一套台账 / 切分 / 编排 / 字段规则。
- 最终 `result` 必须完整、可编排、可展示：每个点过的独立动作都在，关联和顺序能执行，字段名/类型/来源/必填/调用方归属正确。
