# 录制能力识别 Skill

PI 是唯一语义决策者；旧录制逻辑绝不启动。

本 Skill 只指导你如何从原始证据产出完整能力合同。代码不会替你划分能力、推断字段或编排顺序。

你的最终提交必须让现有录制页能直接展示能力。`submit_recording_result.result` 本身就是前端 `draft`。没有非空 `capabilities` 就等于没有产物。

## 代码 / Skill / 模型

- 代码：启动浏览器、原样采集证据、冻结、把你的 result 原样交给前端。可提供 `list_recording_index` 这种无分类事实索引。只拒收页面读不到的信封、重复的 `capability_id`、两个能力共用同一个 `execute`。不识别页面，不补能力。
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
- `option_source`：只挂**当前能力表单或筛选条上真实存在的下拉/选择器**的选项接口。附件列表、审批时间线、流程定义、权限菜单、字典总表，都不是 option_source。
- `fact_check`：打开查看后带出的详情、附件、审批进度，或提交后回读**该业务对象**。不要把“只带分页的列表刷新”挂进来，否则页面会把页码/每页条数画进该能力的系统字段。

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

### 用索引建台账，不要靠抽样

`list_recording_manifest` 只有计数。必须先调 `list_recording_index`，看完全场 interaction 文案、xhr/fetch 的 METHOD+path、页面跳转，再按需 `read_evidence_item` 读正文。不要只读前半场。

索引对齐方法（换任何页面都这样做）：

- 把「人点过的独立业务按钮」和「随后真正改数据的请求」收成台账行。看按钮和确认框，不看系统名，不看 URL 长什么样。
- 列表上的搜索/查询/筛选 → 一项查询能力。
- 新增后点「提交」「送审」→ 一项提交能力。同一张表上另有「保存」「存草稿」且请求 path 或效果不同 → 另项能力，不要并进提交。
- 已有单据上再点「保存」，请求体带着已有主键 → 这是更新已有单，不是“新建草稿”。主键是系统字段，不要放进 `input_schema`。
- 「撤回」「撤销」→ 独立能力。
- 「删除」且随后有删除类写请求 → 独立能力。禁止并进撤回或提交，禁止只写在 `business_understanding`。
- 「查看」「详情」「进度」「审批」且随后有读该记录的请求 → 独立读能力。
- 选择器弹层（标题像「选择××」、表格单选+确认）、日期面板、展开收起 → 不是新能力，挂到打开它的那个字段所在能力。
- 同一动作做两遍只保留一项能力。有确认按钮+写请求却没有能力、又没有 `unresolved`，就是失败。没有对应写请求就不要编造能力。
- 证据引用必须是本场真实 seq。不要把后一轮的序号写到前一轮。

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
- `computed`：计算规则写进 `reason` 和 `source.formula`。用页面标签和字段 key 写关系，例如「明细金额 = 数量 × 产品单价」「税额 = 金额 × 税率 / 100」。证据里看不出公式：标系统，`default_value` 用请求原值，不要编公式，也不要写入 `unresolved`。
- `generated`：谁生成、何时生成（例如保存后服务端生成单号）。看不出生成规则时同样按请求原值固定。
- `constant`：页面或登录态固定带上、人不能改的值。有业务含义就写含义；没有含义的附带字段也用 `constant`，`default_value` 必须等于本场请求原值。
- `selected_option_field`：随哪一个选项接口的哪一个字段带出。
- **无独立来源**：请求里有、但页面上没有对应可填控件，也看不出公式或上游映射的字段（前端行键、行序号、行类型判别、空容器、前端时间戳等）。一律系统自动处理，`exposed_to_user=false`，`source_kind=constant`，`default_value` = 本场请求里的原始值。reason 写「无独立来源，按录制请求原值提交」。禁止为此编 `option_source`、公式、上游映射，禁止写入 `unresolved`。

不要追求每个字段都有一套来源规则。只有人能填/能选的控件、以及能从响应或当前行明确带出的主键，才需要来源规则。其余请求字段按原值交给系统。

栏位在左还是在右不重要，**说明必须能让导出直接用**。人能填的控件缺标签/`path`、或把可改字段写成不可改，算出品不完整。看不出公式或来源的请求字段标系统并保留原值，不算残缺。

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

核对的是**处理逻辑**，不是本场样例值。换一场、换一页，同一字段只要仍按同一套规则进出请求，就是同一能力。禁止拿本场填过的字、日期、行内容当合同。

必须按本场**实际发出的那条 execute 请求的形状**建模，不要另编一份看起来更整齐、但执行时发不出去的结构。换任何页面都只认这几条形状规则：

- **重复键**：同一个 query/body 键在请求里出现多次，就建同样多个 param，共用这个 `path`，归属相同，执行时仍发这个重复键。不要丢掉第 2 次及以后，也不要改成线上没有的新键名。
- **数组**：请求体里是一个数组，就只建这一个数组 path。行与行的差别写在行内字段上，不要把一种行拆成另一个调用方数组去抢同一 path。
- **行内字段**：行里对应可填/可选控件的是调用方；行里没有独立来源的判别码、序号、前端行键是系统，按请求原样带上。
- **键名**：execute 的 `path` / `key` 必须能在实际请求里找到。请求没有的键不要编进去；请求有的键不要改名。
- **样例值**：`default_value` 只固定无来源字段怎么提交，不是下次执行必须填的业务值。

完整性：

- execute 请求体/查询里的每个业务字段都要出现在该 step 的 `params`，不要只写人改过的那几个。
- 表单上看得见、请求里也带着的灰框字段，要作为系统字段留下，不要丢。
- 表单上有、请求没带的只读提示（例如“保存时自动生成”）写成 `generated` 系统字段。
- `option_source` 只挂**这个能力的表单**上真实存在的下拉。列表筛选的“创建人”选项不要挂到新增/编辑。页面加载时的权限/字典/菜单不要挂进业务能力。

人在筛选框、表单、下拉里能填或能选的值，必须是调用方字段，不能丢。  
筛选条上看得见的输入框，即使本场空着没进 query，也要留下调用方可选字段。`key`/`path` 必须能从控件的 name、placeholder 或同页已发出的请求看出来；看不出来就写入 `unresolved`，不要假装这个筛选项不存在。  
页面标签用当前页原文。筛选条写「流程状态」就不要改成「审批状态」。  
不要把系统字段标成调用方，也不要把调用方必填标成系统自动。  
不要把密码、token、Cookie、Authorization 的真实值写进 result。

### 控件认法（换页面也用这一套）

认的是**当前这个表单或筛选条上的控件**，不是弹层内部工具栏，也不是另一页的同名字段。

1. **名称**：`label` 和 `input_schema.properties.*.title` 都用当前页可见标签。优先认证据索引里的 `label` / `placeholder`，不要用请求英文字段名改名，也不要用另一页的叫法覆盖本页。点击记录只有 `tag=INPUT`、没有标签时，必须写入 `unresolved`，禁止猜字段名。
2. **类型**：看控件。日期选择器 → `date` / `datetime`；数字框 → `number`；开关 → 用控件上的开/关文案；下拉 → `enum`。`input_schema` 的 type 必须和 param 一致。
3. **必填**：看当前页星号或校验文案，不看你是否刚好填过。没有星号不要标必填，除非校验文案证明必填。`input_schema.required` 必须列出全部 `exposed_to_user=true` 且 `required=true` 的 key。只写一边，页面会显示成全可选。
4. **放大镜 / 表格选择器**：这是一个 `api_option` 调用方字段，提交行主键。`enum_options` 用选项接口返回的 `{label,value}`，value 是行 id，不是表格行号。选择器弹层里的公司/编号/名称筛选属于弹层内部，不要提升成父列表或父表单的调用方字段，除非父页面自己也有这个控件。
5. **选项接口顺便带回、灰底展示的编号/保管人/部门**：`selected_option_field`，系统。
6. **页面加载就自动带上、筛选条上没有的当前用户 / 当前组织**：系统。`reason` 写「运行时取当前登录身份」，不要把本场的 `1`、`101` 写成永远不变的 `constant` 固定值。
7. **开关**的标签用控件原文（开启/关闭），不要抄另一页的是/否。
8. **没打开过的下拉**：不要编 `enum_options`，也不要用列表单元格或另一页的值冒充选项。请求里已经带着的值：留下调用方字段，`options_complete=false`，只写当场看到的项。没进请求、也没打开过：仍按可见筛选项留下调用方可选字段，不要编选项，也不要因此写入 `unresolved`。打开过的必须列当场全部选项，并标 `options_complete=true`。
9. **同一 path 只能有一种归属**。禁止同一个 `query.xxx` / `body.xxx` 既写成调用方又写成系统。重复键可以对应多个 param，但归属必须相同，执行时仍发原键。禁止把一个数组 path 拆成多个调用方字段。
10. **系统主键不要进 `input_schema`**：单据 id、流程实例 id、行 id。即使标了 `exposed_to_user=false`，也不要再放进 `properties`。
11. **确认弹层里的说明**：写进了请求体才建模；没进请求体不要编成调用方字段。

## 编排

- `request_refs[].step_id` 必须等于 `steps[].step_id`，不要填 `request_id`。
- `request_id` 只允许出现在 `request_facts.requests`。
- 每个能力恰好一个 `execute`；该 `step_id` 不得出现在另一个能力的 `execute`。
- 能力内顺序：`preflight` → `option_source` → `execute` → `fact_check`。`request_refs[].sequence` 必须按这个实际执行顺序编号；页面按 sequence 展示，不要把取详情的 preflight 排到选项接口后面又把 sequence 写反。
- 跨能力：被依赖的查询/选择/创建在前，写入、撤回、删除在后。
- `links` 必须用 `source_step_id` / `source_path` / `target_step_id` / `target_path` 写出**值怎么流**。例如创建响应里的流程实例 ID 进入撤回参数。不要把常量写成“上游映射”，也不要只写一句“有依赖”。
- `from_path` / `source_path` 必须能在那个响应里读到。提交返回 `{"data":61}` 就是单据 id，不是流程实例 id；流程实例要从随后的详情回读取。写错路径等于编排错误。
- 搜索能力上的接口下拉必须挂 `option_source`。父表单没有的选项请求不要挂。
- 查看能力里随详情带出的附件、审批进度是 `fact_check`，顺序在 execute 之后。不要标成 `option_source`。
- GET 详情的 execute `params` 只写**这次请求真正提交的** query/body（通常是主键）。响应里只读展示的标题、金额、状态不要再写成 `path=body.xxx` 的请求字段。
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
12. 每个 param 都有 `reason`。人能填的控件才写来源规则；接口枚举有 URL 和本场选项，页面枚举列出当场看到的 `{label,value}`。看不出公式或来源的字段：系统自动处理，`default_value` 用请求原值，不要写入 `unresolved`。回填主键写明从哪一步哪条路径来。
13. 已调用 `list_recording_index`。索引里每个带确认的写入都有能力或 `unresolved`，选择器弹层没有被做成独立能力。
14. `input_schema.required` 与调用方 params 的 `required=true` 一致；`title`/`label` 来自当前页控件，不是另一页。
15. 同一 path 只有一种归属。系统主键没有进 `input_schema`。execute 的键名、重复键次数、数组形状与本场真实请求一致，没有另编一套发不出去的结构。
16. 每条 `from_path` 都能在对应响应里读到。
17. `option_source` 只对应本能力可见下拉。附件/审批/流程定义不是 option_source。
18. 没打开过的下拉里没有编造的 `enum_options`。
19. GET 详情 execute 没有把响应展示字段写成请求 `body.*`。

## 泛化

- 本 Skill 不绑定任何具体业务页、系统名或字段名。上面的例子只说明形状，不是某页的补丁。
- 只根据本场点击、输入、请求、响应、截图判断。
- 换一个页面也走同一套台账 / 切分 / 编排 / 字段形状规则：控件决定调用方，请求形状决定 path/重复键/数组，无来源字段按原值交给系统。
- 最终 `result` 必须完整、可编排、可执行：每个点过的独立动作都在，关联和顺序能执行，字段处理逻辑与真实请求一致。
- 证据不够、台账对不齐、点过的独立动作做不成能力时：写入 `unresolved`，不要猜测成看似可用的残缺能力。导出层只拒绝这种能力缺口。
- 字段来源写不出时：标系统自动处理，`default_value` 用请求原值。不要猜测来源，也不要因此阻止导出。
