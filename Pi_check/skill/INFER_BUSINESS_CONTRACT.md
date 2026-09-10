# Infer Business Contract

本文件只负责：划分原子能力；区分主能力、候选查询和噪声；判定调用方、系统、身份、常量、生成、计算和查询来源；生成动态候选转换；保存证据引用；明确未解决事项；给出下一步最小补证建议。  
禁止：点页面、写消费者 SKILL.md、改导出实现、把未识别来源冻结成录制常量。  
缺口只改本文件。

Investigator 叫你认一项产物时立刻工作。该项已按目标做完且有真实 execute 就交，不要等用户结束，不要攒全场再交。交一项用 `submit_recording_capability`。不准宣布定稿，不准 `submit_recording_result`。

不要单独交没有 `request_refs` 的 relations-only 信封。顺序关系写在已有能力之后的 `capability_relations`，不能当成一项没有 execute 的能力。

## 禁止把未识别来源冻成录制常量

禁止：

```text
来源没识别出来 → system + literal:录制值 → 标记已解决
无独立来源 → source_kind=constant + default_value=录制原值 → 已解决
看不出公式 → 标系统 + 请求原值，不要写入 unresolved
字段来源写不出时：标系统自动处理，不要因此阻止导出
```

没认清来源 → `unresolved`。录制值只可作 `sample_value` 诊断，不能当已解决业务默认，不能标已解决。

允许写成系统/常量的，必须是已经认清的：

- 点「添加××行」产生的行类型码、行序号、前端行键：有加行按钮 + 分区证据 → 系统，reason 写清依据和 seq。
- 登录身份自动带上、表单没有对应可改控件 → `current_user`，禁止写死本场数字。
- 可执行 formula 已从页面/前端交叉核对 → `computed`。
- 每次提交都相同且有业务含义的固定判别值 → `constant`，依据写清。入口路径、标题或目标已经标明单据族（如 `duty_rest`），列表/保存请求却漏了这个判别键：仍写成 `constant`，reason 写清来自入口身份，不要因为首屏请求没带就省略。禁止把别的单据族编号或类型写进这一项。

看不清的键（不知道是调用方漏填、系统生成，还是前端时间戳）：`unresolved`。禁止因叶子名叫 createTime 就写成 now。

表单打开时被前端写进请求、页面没有对应可改控件、也不是登录身份：这是前端时间戳或会话残值。写入 `unresolved`，`sample_value` 可记本场原值。禁止标 `system` / `constant` 并当已解决。

## 一项能力什么时候才算完整

交 `submit_recording_capability` 之前，这一项必须同时满足。缺一条就补证或写入 `unresolved`，禁止交残缺合同。

1. 有本场该次操作的真实 execute，且该响应业务成功。首屏自动请求、上一页列表加载、通知未读，都不是这项的 execute。请求发出了但 HTTP 失败，或 JSON `code` 不是成功、`msg` 带「失败 / 错误」：不要交完整能力；把站点拒绝原文写入 `unresolved`。
2. execute 的每个 query/body 业务键：已认清来源并进该 step 的 `params`，或写入 `unresolved`。禁止标系统并按录制原值结案。
3. 对照**打开该表单或加行之后**的最近一次 `visible_control`。每个可改控件都有调用方字段（目标排除项除外）。宿主 `readonly=true` 或 `disabled=true` 的灰框不进 `input_schema`，只进系统 params。
4. `input_schema` 的 type、param 的 type、线上 query/body 的实际类型必须一致。日期就是 `date` / `datetime`，数字就是 `number`，不要一边 string 一边 number。
5. 可增行：目标要求的每一种「添加××」分区都加过并写过。数组 `title` 用各分区标题原文，用 `/` 或 `；` 连接，并写 `x-dano-section-titles`。`items.properties` 的 `title` 是表头原文，禁止自造「A/B」合并名去替代列名。只在某一个分区表头出现的列，只属于该分区的行，不要写成每一行都有。行类型码、行序号、前端行键只留在系统 params，不进 schema。
6. `preflight` 只挂「进入该表单可写状态」的那次请求。上一页列表加载、通知未读、字典/菜单不是 preflight。没有这样的请求就不要硬挂一条。
7. `links` 只写「另一步或另一能力真正用到的值流」。禁止把 execute 响应指回**同一个** execute 的 `body.id`。回读该业务对象用 `fact_check`。
8. 目标先 A 后 B：有值流写 `links`；没有值流写 `capability_relations`，挂在已有 execute 的能力上。本场已做完的页内典型链必须挂 relation。
9. 目标排除的控件不进调用方。对应空数组不要假装人要填。
10. 该项的 `name` / `title` / `intent` 覆盖这项完整目标，不要压成半场短句。
11. 整份信封的 `recording_goal` 必须是用户目标全文，禁止改写成「查询筛选」这种已交行的短标题。目标还有编辑/提交/撤回/删除时，交完查询或新增不能把目标收口。入口目标是占用图申请时，不要把旁路配置页的保存收成该项可执行能力。

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
- `preflight`：打开**这一张**表单、带出记录、进入可写状态的那一次请求。打开表单附带的空抄送列表、附件计数不是新能力，也不是系统字段。上一页列表、通知未读、整站字典/菜单不是 preflight。
- `option_source`：只挂**当前能力表单或筛选条上真实存在的下拉/选择器**的选项接口。附件列表、审批时间线、流程定义、权限菜单、字典总表，都不是 option_source。
- `fact_check`：打开查看后带出的详情、附件、审批进度，或提交后回读**该业务对象**。不要把“只带分页的列表刷新”挂进来，否则页面会把页码/每页条数画进该能力的系统字段。

页面把该能力**所有 step** 的 `params` 平铺成「系统自动处理」。因此：写入/提交类能力的系统栏**只许来自 execute**。`preflight` / `option_source` 的 `params` 必须是空数组 `[]`。禁止把选项接口或打开表单请求里的页码、每页条数、排序、状态过滤、占位业务 ID 写进该能力任何 step 的 `params`。查询类能力的分页只写在它自己的 execute 上。

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

`list_recording_manifest` 只有计数。必须先调 `list_recording_index`，看完全场 interaction 文案、xhr/fetch 的 METHOD+path、network_response、`visible_control`、截图和页面跳转，再按需 `read_evidence_item` 读正文。请求/响应正文在 `payload.body.text` 或 `body.blob_id`。`read_response_blob` 只接受 `blob_` 开头的 id，不要把 `request_id` 当 blob，也不要编造截图 blob_id。不要只读前半场。也可用 `list_action_timeline` 按时间看人与 PI 点过的交互（带 `actor`），对候选 execute 调 `read_request_shape`。禁止把完整 result 写在对话里。

Investigator 确认该项已按目标做完、并在 `list_action_timeline` 或 `network_since` 里对上那次操作的真实 execute 之后，你才建模并 `submit_recording_capability`。禁止没看到 execute 形状就交空壳。点页面、协助、空表是否保存，由 Investigator / Control 决定。

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

### 陌生页解题步骤（换任何页都走这一遍）

不要套某系统的 URL 或字段名。对本场每个独立动作，按下面五步自己对齐：

1. **切能力**：点过的独立业务按钮 + 随后真正改数据或查询的请求 = 一项能力。一个能力恰好一个 `execute`。打开表单、选项、提交后回读挂到该能力，不要另开能力。
2. **摊开 execute 请求形状**：把这条请求的每个 query/body 键列出来。同一键出现几次就建几个 param，共用这个 path。一个数组只建一个数组 path；行差别写在行内字段。
3. **摊开当前页控件**：读该动作所在页的**最近一次** `visible_control` 和点过的 interaction，并与当场 `snapshot` 对照。每个控件看 `region`（filter/form/table/dialog）、`label`/`placeholder`/`name`、`control_kind`（input/select/date/textarea/upload/button/readonly）、`required_mark`、`readonly`。`region=table` 的输入是加行后出现的行内框；`region=form` 的大段 textarea 是另一份补充说明；`region=dialog` 是确认/选择弹层。打开弹层、切换页签、加行后必须再 snapshot；`read_visible_controls` 不传 seq。不要带着打开弹层之前的 seq 去对字段。
   `readonly`/`disabled` 表示**整个控件当前不能改**（宿主带 disabled / aria-disabled / is-disabled）。自定义下拉、日期、级联、单选组的内部展示框常常带原生 `readonly`，那不是灰框。只有宿主锁死才是系统字段；点一下能出选项或能改选 → 可改 → 调用方。
4. **按目标写上再证明绑定**：Investigator 已按目标把该动作的可见字段写上并点出 execute 之后，再对还没对上的键建议改一个值看哪个请求键变了。值碰巧相等、字段名相似、同一个请求里仅剩两个字段，都不能单独定案。本场没改过、但控件可改：仍是调用方，不要写成 `constant` / 「无独立来源」。无法做这个实验（合法 selector 写不进、未授权写入）：该键写入 `unresolved`。禁止编 path，禁止 invent selector。来源没看清不能说成已经理解业务。录制值只可作 `sample_value`。
5. **逐键对上控件，决定调用方还是系统**：
   - 对得上**可改**控件（input/select/date/textarea/upload，以及树、页签、分段器、单选组，且 `readonly`/`disabled` 都不是 true）→ **调用方**。即使本场没改、这次 query/body 没带这个键，也留下可选调用方字段。页面上已有默认选中（单选默认启用、下拉已有值）只要还能改，仍是调用方，禁止写成 `constant` / 「无独立来源」。`path` 用控件 `name` 或同页已发出请求里的同义键。对不上 path 就写入 `unresolved`，不要假装控件不存在。
   - 一个可见**日期区间**（`range=true` 或一个控件里两个起止输入）对上两个请求键时，两个键都是调用方，不要把起止收成系统。
   - 页面因切换类型/页签自动改了日期，只要日期控件仍能点，仍是调用方，不要当成计算公式。
   - 入口 URL / 上一页带入的默认值：本页对应控件**仍能改** → 调用方，`source_kind=page_default`。`visible_control` 上该控件 `readonly=true` 或 `disabled=true` → **系统**，`source_kind=page_default`，reason 写清从哪次跳转/URL 带入；**禁止**再写进 `input_schema` 当调用方枚举。同一标签若既有只读下拉、又有一份看起来可改的空 input，认只读那条，不要把灰掉的类型收成调用方数字框。
   - 对得上灰框 / 自动编号 / 只读姓名单位 / `readonly=true` / `disabled=true` → **系统**。
   - 请求里有、控件上没有：登录用户/组织且初始加载请求就自动带上 → **系统**，`source_kind=current_user`，reason 写「运行时取当前登录身份」，**禁止**把本场的用户 ID、公司 ID 写成永远不变的 `constant` 固定值。
   - 请求里有、控件上没有：行类型判别、行序号、前端行键 → 仅当有加行按钮和分区证据时标 **系统**，reason 写清依据和 seq。前端时间戳或看不清的键 → `unresolved`，录制值只作 `sample_value`。禁止写成「无独立来源，按录制请求原值提交」并标已解决。
   - 空数组/空对象：有对应可改控件（上传、选人、可增行）且目标没有排除它 → **调用方**，即使本场是空；目标排除该项（例如排除上传）→ 不进 `input_schema`，空数组留在系统 params；没有对应控件 → **系统**，按请求原值。
   - 上一页跳转带进本页 query、本页仍有对应控件 → **调用方**，`source_kind=page_default`，不要因为本场没再搜就收成系统。
   - 确认弹层、二次确认框里的说明/意见：按下面「确认弹层」完整处理，不能因为本场 execute 没带这个键就假装控件不存在。
   - 其余对不上的 **execute** query/body 键 → **系统**，按请求原值提交，不做任何改动，不要猜公式。这些键必须出现在 **execute** step 的 `params` 里，`exposed_to_user=false`。不要把 Cookie、Authorization 或其它请求头编成业务字段。不要把 `preflight` / `option_source` 请求里的键抄进系统栏。

禁止把 `visible_control` 里看得见**且可改**的日期、下拉、树、页签、附件、表格行输入、确认弹层可填意见写成「不可见 / 不可改 / 系统固定」。`readonly=true` 或 `disabled=true` 的灰框除外，那些是系统；不要把自定义下拉内部展示框的原生 readonly 当成灰框。  
点「添加××行」或工具栏加行产生的行类型码不是调用方控件，不要放进 `input_schema`（包括数组 `items.properties`）。  
`input_schema`（含数组 `items.properties`）的每个 key 必须对应某个 `exposed_to_user=true` 的 param.key。params 标系统的 key 禁止再出现在 schema。  
**每个** `exposed_to_user=true` 的 param 都必须出现在该能力 `input_schema.properties`；只写在 params 里等于调用方字段丢失。数组只出现数组自己的 key，行内调用方写在 `items.properties`。  
execute 的 query/body 没有、当前页也没有对应可改控件的键，禁止写进 params 或 schema。可见可改控件即使本场没带，仍要留下调用方可选字段。  
不要编造本场没发出的写请求，也不要把同一数组拆成多行并列字段。  
分页只留在真正执行查询的那个能力的 **execute** 系统字段，`source_kind=page_default`，`exposed_to_user=false`，**禁止**再写进 `input_schema`。写入能力的 execute 没有分页键，就不要出现页码/每页条数。打开表单拉到的空列表、弹层内部翻页，禁止写进该能力 `params`。

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
   选择字段必须把**调用系统能直接用的选项合同**写进同一个 schema 字段，不能只写 `type=number` 再把接口/枚举藏在说明里。只写「从部门树选择」这类说明会被拒收：
   - `api_option`：`x-dano-business-type` 写 `api_option`，并写 `x-dano-option-source`（`source_method`、`source_url`、`label_key`、`value_key`；树再加 `children_key`）。其他系统调用时按这个接口实时查询，选显示值、提交 id。
   - `page_enum`：`x-dano-business-type` 写 `single_enum`，并用 `{label,value}` 列出当场选项。不要只写裸编码 `1/2/3`。
   params 上的 `source_kind` / `source` / `enum_options` 必须和该 schema 字段一致。

每个字段的 `reason` + `source` 必须写清「运行时怎么处理」，不能只写来源标签：

- `user_input`：人在哪个控件填写；空值是否仍提交。
- `page_enum`：选项来自页面本身；`enum_options` 必须列出**当场下拉里看到的全部** `{label,value}`。写清是否完整。
- `api_option`：`source` 写 `source_method`、`source_url`、`label_key`、`value_key`；`enum_options` 写本场实际返回的选项。写清 `options_complete=true/false`（只截到一页就标 false）。调用方选显示值，提交接口值。
- `page_default` / 可改的 `previous_response`：默认从哪一步哪条路径来（`from_step_id` + `from_path`），**调用方可以改**。编辑弹层里的日期、下拉、数字都属于这类，即使本场没改。
- 只读回填的 `previous_response`：`source` 必须写 `from_step_id` + `from_path`，提交时原样带回，调用方不能改。同一条值流还要写进 `links`。导出从这些 `links` 投影传值。目标要求先 A 后 B、但没有值要传时，另写 `capability_relations`（见编排）。不要把本场主键/单号/正文当 `default_value`。
- `selected_record_identity`：从列表哪一次点击/当前行哪个字段带出，提交到哪个 path。
- `computed`：计算规则写进 `reason` 和 `source.formula`。用页面标签和字段 key 写关系，例如「明细金额 = 数量 × 产品单价」。证据里看不出公式：写入 `unresolved`，不要编公式，不要用录制原值冒充已解决。
- `generated`：谁生成、何时生成（例如保存后服务端生成单号）。看不出生成规则时写入 `unresolved`。
- `constant`：已经认清、每次提交都相同且有业务含义的固定值。依据写清。没有认清不要用 `constant`。
- `selected_option_field`：随哪一个选项接口的哪一个字段带出。
- **来源未识别**：请求里有、但页面上没有对应可填控件，也看不出公式或上游映射。写入 `unresolved`，`sample_value` 可记本场原值。禁止 `source_kind=constant` + 录制原值 + 已解决。
- 页面有可见区间日期控件、请求却没带起止：这不是来源未识别，是操作没做完。不要把 startTime/endTime 写成 unresolved 结案。退回补日历弹层（分别填开始/结束日期时间再确定），看到请求真带上再交。后一次同 path 的 execute 已经带上该键：从 unresolved 删掉，不要把前一次漏传留着挡出包。

不要追求每个字段都有一套来源规则。只有已经认清的字段才写成调用方或系统。其余写入 `unresolved`。

栏位在左还是在右不重要，**说明必须能让导出直接用**。人能填的控件缺标签/`path`、或把可改字段写成不可改，算出品不完整。看不出公式或来源的请求字段写入 `unresolved`，不算已解决。

判断调用方还是系统（看控件，不看你是否刚好改过它）：

- 调用方提供 `exposed_to_user=true`，且**只这些**写进 `input_schema.properties`。
- 系统自动处理 `exposed_to_user=false`，只写在 `steps[].params`，**禁止**再写进 `input_schema`。页面用 `exposed_to_user` 分两栏；写进 schema 又标系统，旧逻辑会把系统字段画进调用方。

看页面上的控件，不看业务名：

- 共享列表壳上的筛选文案若与入口单据族矛盾（入口路径/标题已经标明一类单据，壳上却还挂着另一类的类型下拉）：不要收成该项调用方。入口判别键写成 `constant`。禁止把搜到的别族单号写成查询默认值。
- 白底可改的输入、日期、数字、下拉、单选、页签、分段器、树、附件 → 调用方。编辑弹层里同样可改的字段，即使本场只改了备注，仍是调用方；来源用 `page_default` / `previous_response` / `api_option`（上游默认，可修改）。
- 灰底只读、保存时自动生成的单号、合计行、金额/税额/优惠后金额这类算出来的格子 → 系统。来源 `computed` / `generated` / `previous_response`。不要标成“自动计算，可修改”，也不要放进 `input_schema`。
- 列表行点进查看/编辑/删除/审批时带出的主键、明细行 ID、流程实例 ID → `selected_record_identity` 或 `previous_response`，**系统**。不要放进 `input_schema`。
- 选项接口顺便带回的显示名、条码、库存、单位等，人不能单独填 → 系统，`selected_option_field` / `previous_response`。
- 打开编辑时 GET 详情回填、提交时原样带回、表单上根本没有对应可改控件的字段（创建人只读、入库数）→ 已认清则标系统。前端时间戳看不清生成规则 → `unresolved`。
- 登录态、Cookie、分页、流程定义 Key → 系统。页面上能改的类型/状态下拉、日期、附件仍是调用方，不要因为本场没改就收成系统。

更细的来源：

- 人在输入框里键入 → `user_input`，调用方。
- 人在页面下拉/单选里选、选项写死在页上 → `page_enum`，调用方，必须带 `enum_options`。
- 选项来自当场请求 → `api_option`，调用方；该请求才能挂 `option_source`。
- 日期选择器 → `date` / `datetime`，不要因为请求体是时间戳就写成 `integer`。`input_schema` 的 type 必须和 param 一致。
- 树/列表单击提交单值：schema `type` 必须和 param 一致（通常是 string/number），禁止无证据写成 `array` + `multiple`。树单击是单值。
- 从刚创建/刚查看/列表当前行带出的 `id`、`processInstanceId` → 系统。
- 撤回原因、删除确认之外人另外填写的说明 → 调用方。

核对的是**处理逻辑**，不是本场样例值。换一场、换一页，同一字段只要仍按同一套规则进出请求，就是同一能力。禁止拿本场填过的字、日期、行内容当合同。

必须按本场**实际发出的那条 execute 请求的形状**建模，不要另编一份看起来更整齐、但执行时发不出去的结构。换任何页面都只认这几条形状规则：

- **重复键**：同一个 query/body 键在请求里出现多次，就建同样多个 param，共用这个 `path`，归属相同，执行时仍发这个重复键。不要丢掉第 2 次及以后，也不要改成线上没有的新键名。
- **数组**：请求体里是一个数组，就只建这一个数组 path，`input_schema` 也只出现这一个数组 key。行与行的差别写在行内字段上，不要把每行的内容/进度拆成并列调用方字段，也不要把一种行拆成另一个调用方数组去抢同一 path。
- **可增行明细**：调用方输入的是点「添加×× / 新增行」后单独出现的那些输入框，行数可变。`input_schema` 只出现 execute 里那一个数组 key，类型必须是对象数组，不能写成 `string`。`items.properties` 的 `title` 用**表头原文**（不要自造合并名去替代列名），必须覆盖各分区可见表头（序号、操作除外）。数组自己的 `title` 用各分区标题原文，多个分区必须用 `/` 或 `；` 连接，禁止写成「A和B」这种拆不开的合并名。数组 title 能拆出多个分区时，必须写 `x-dano-section-titles`。同一线格式键在不同分区表头不同时，在该 property 上写 `x-dano-section-titles`：`{分区标题: 表头原文}`。也可以把同一份 `{分区标题: 表头原文}` 写在数组自己身上，导出按键认分区、按值认该字符串列的表头。合并成一个数组提交时，按分区标题分组或每行带分区标题，系统按分区补行类型等无独立控件的行字段。只在某一个分区表头出现的列（例如只有一种行有进度），只属于该分区，不要写进所有行的 `items.properties`。`reason` 必须写清：调用方按行填写这些实际输入，可增减；**最后**由系统把各行组装成 execute 里的那一个对象数组，并按分区补上无独立控件的行字段。禁止把整份数组当成调用方一次性粘贴的 JSON。禁止把一个数组拆成多个调用方数组或并列字段去抢同一 path。禁止把行序号写成调用方。
- **同名文本域不是行**：`region=form` 的大段 textarea 与 `region=table` 的行内输入即使标题相近，也是两套控件。有独立 body 键的补充说明单独建模，用它自己的控件标签，不要用表格分区标题去命名这段文本。不要用它代替可增行，也不要把可增行收成一段字符串。
- **行内字段**：行里对应可填/可选控件的是调用方，写进该数组的 `items.properties`；行里没有独立来源的判别码、序号、前端行键是系统，只留在 params，不要进 schema。行类型码来自点了哪个加行按钮或落在哪张表，不是调用方下拉，禁止编成「类型/项目类型」让调用方选。
- **键名**：execute 的 `path` / `key` 必须能在实际 **query/body** 里找到，或能对上当前页可见控件。请求和控件都没有的键不要编进去；请求有的键不要改名。筛选栏写「流程状态」但 execute 发出的是 `status`，path 必须是 `query.status`，不要改成 `flowStatus` / `billStatus` 去迁就文案。`input_schema.properties` 的顶层 key 必须等于某个 `exposed_to_user=true` 的 param.key。不要把请求头写成 `query.*` / `body.*`。
- **页面原名**：`label` / `title` 用当前页原文，去掉星号（星号只表示 `required_mark`，不要把星号写进名字）。有 `section` 时，附件等分区字段优先用分区标题。
- **样例值**：`default_value` 只固定无来源字段怎么提交，不是下次执行必须填的业务值。
- **编排**：`request_refs` / `steps` 只能引用本场真实发出的请求。不要把没发过的 create/update/save 编进执行顺序。

完整性：

- execute 请求体/查询里的每个业务字段都要出现在该 step 的 `params`，不要只写人改过的那几个。没有认清来源的键写入 `unresolved`，录制值只可作 `sample_value`，禁止标系统并按请求原值当作已解决。
- 表单上看得见、请求里也带着的灰框字段，要作为系统字段留下，不要丢。
- 表单上有、请求没带的只读提示（例如“保存时自动生成”）写成 `generated` 系统字段。
- 可增行数组的 `reason` 必须写清「调用方按行填写添加行后出现的输入框，系统再组装成该数组」。
- `option_source` 只挂**这个能力的表单**上真实存在的下拉。列表筛选的“创建人”选项不要挂到新增/编辑。页面加载时的权限/字典/菜单不要挂进业务能力。`option_source` / `preflight` 的 `params` 必须是空数组。

人在筛选框、表单、下拉里能填或能选的值，必须是调用方字段，不能丢。  
筛选条上看得见的输入框，即使本场空着没进 query，也要留下调用方可选字段。`key`/`path` 必须能从控件的 name、placeholder 或同页已发出的请求看出来；看不出来就写入 `unresolved`，不要假装这个筛选项不存在，也不要编一个看起来像业务的 query 键。没打开过的下拉不要编 `enum_options`，也不要把整站无过滤的字典总表挂成该字段的 `option_source`。schema 已列出当场枚举时，params 必须同属 `page_enum`，不要再改口成无类型 `api_option`。  
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
6. **页面加载就自动带上、筛选条和表单都没有对应可改控件的当前用户 / 当前组织**：系统。`source_kind=current_user`，`reason` 写「运行时取当前登录身份」，不要把本场的用户 ID、公司 ID 写成永远不变的 `constant` 固定值，也不要因此写进 `input_schema`。
7. **开关**的标签用控件原文（开启/关闭），不要抄另一页的是/否。
8. **没打开过的下拉**：不要编 `enum_options`，也不要用列表单元格或另一页的值冒充选项。请求里已经带着的值：留下调用方字段，`options_complete=false`，只写当场看到的项。没进请求、也没打开过：仍按可见筛选项留下调用方可选字段，不要编选项，也不要因此写入 `unresolved`。打开过的必须列当场全部选项，并标 `options_complete=true`。
9. **同一 path 只能有一种归属**。禁止同一个 `query.xxx` / `body.xxx` 既写成调用方又写成系统。重复键可以对应多个 param，但归属必须相同，执行时仍发原键。禁止把一个数组 path 拆成多个调用方字段。
10. **系统主键不要进 `input_schema`**：单据 id、流程实例 id、行 id。即使标了 `exposed_to_user=false`，也不要再放进 `properties`。
11. **确认弹层、二次确认框里的说明/意见**：必须完整处理。人能填的意见是调用方控件，不能丢掉。先在同一次确认动作随后发出的写请求里找对应键（该能力 execute，或同动作的其它写请求）。找到了：建成调用方，`label` 用弹层原文，`reason` 写清「人在确认弹层填写，提交到该 path」。意见若在另一条写请求，该请求必须挂进本能力 `request_refs`。弹层只有确认/取消、没有可填内容：不是字段，写在 execute 编排说明里。人填了但本场所有写请求都对不上 path：写入 `unresolved`，写明缺的是意见对应的请求键，不要编造写请求里没有的键，也不要假装这个控件不存在。
12. **`input_schema` 与 params 必须同归属**：schema（含 `items.properties`）只能出现 `exposed_to_user=true` 的 key。每个调用方 param 都必须有同名 schema key。系统字段只留在 params。
13. **跳转带入仍可改**：从上一页带进本页 query 的键，只要本页 `visible_control` 看得到对应控件，就是调用方，不要收成系统。
14. **树 / 页签 / 分段器 / 单选组**：按可改选择控件处理。选项用当场看到的文案做 `page_enum`，或用树/下拉接口做 `api_option`。不要因为快照以前漏过就标系统。
15. **可增行按钮**：`control_kind=button` 且文案像「添加×× / 新增行」只证明行数可变，不是调用方字段。不要把它或它产生的行类型码写进 `input_schema`。
16. **页签 / 折叠头不是字段**：只有文案、点了不改变 execute 形状、也没有独立 name/path 的「单据信息」一类页签或折叠头，不要编进 params 或 schema。
17. **灰框带入值**：宿主锁死的下拉/类型，即使值来自 URL 或上一页，也是系统，不要再做成调用方枚举。

### 弹层选人/选记录的对象数组

选择器弹层里用树、搜索、表格勾选和「确认」选一个或多个人/组织/业务记录，而 execute 在一个数组字段中提交所选行的多个属性时，这是**一个调用方多选字段 + 系统按选项响应组装对象行**，不是可增行明细，也不是让调用方粘贴 JSON。

1. 只保留 execute 里的真实数组容器键和 path。`steps[].params` 为该容器建立一个 `type=list-enum`、`wire_type=array`、`source_kind=api_option`、`exposed_to_user=true` 的字段；必填性只看父表单的星号/校验。禁止新增大小写不同、拼写相近或别名容器，也禁止把同一个选择拆成 `xxxIds`、`xxxList` 等第二条调用方路径。
2. `input_schema.properties.<容器>` 写成 `type=array`，`items={"type":"string","format":"name-ref"}`，并声明 `x-dano-business-type=api_option`、`multiple=true` 和完整 `x-dano-option-source`：`source_method`、`source_url`、请求的 `params`/查询参数、`result_path`、`value_key`、`label_key`；弹层需要展示部门、编号等辅助列时写 `extra_fields`。导出结果必须是多选选择器，不是 `textarea`，也不是逐列可编辑表格。
3. 在 execute step 的 `selects` 中为同一容器写一个绑定：`param` 是容器 key，`path` 是真实 body/query path，`multi=true`，并复用同一个选项来源。`label_subkey` 指向对象行中承载显示名的字段；`element_template` 必须覆盖 execute 对象行的每个键：固定业务判别值写 `{ "const": 录制请求原值 }`，来自所选接口行的值写 `{ "item_key": "响应字段路径" }`。响应字段可以是 `dept.name` 这样的嵌套路径，不能因为它嵌套就冻结成录制样本。
4. `element_template` 的目标键必须逐字等于本场 execute 数组对象的键；`item_key` 必须能在 option_source 响应中读到。禁止复制本场已选中的对象行、人员 ID、姓名或部门作为下次执行的固定数组；固定的只能是每次同样提交的行内判别值。
5. 该选择器的请求挂为当前能力的 `option_source`，顺序在 execute 前。这个 step 的 `params` 必须是空数组 `[]`：弹层内部的姓名/部门搜索框、分页、每页条数、状态过滤是选项接口内部参数，只写进调用方字段的 `x-dano-option-source`，不写进任何 step 的 params，也不提升为父表单字段。其它能力只有自己的表单也存在这个选择器时才关联该 option_source。

通用形状示例（字段名仅说明结构，不绑定具体页面）：

```json
{
  "input_schema": {
    "properties": {
      "recipients": {
        "type": "array",
        "items": { "type": "string", "format": "name-ref" },
        "title": "页面上的选择字段名",
        "multiple": true,
        "x-dano-business-type": "api_option",
        "x-dano-option-source": {
          "source_method": "GET",
          "source_url": "/api/options",
          "params": { "status": "active" },
          "result_path": "rows",
          "value_key": "id",
          "label_key": "name",
          "extra_fields": ["department.name"]
        }
      }
    }
  },
  "selects": [{
    "param": "recipients",
    "path": "body.recipients",
    "source_method": "GET",
    "source_url": "/api/options",
    "value_key": "id",
    "label_key": "name",
    "multi": true,
    "label_subkey": "recipientName",
    "element_template": {
      "businessType": { "const": "recorded_constant" },
      "recipientId": { "item_key": "id" },
      "recipientName": { "item_key": "name" },
      "departmentName": { "item_key": "department.name" }
    }
  }]
}
```

上面 `x-dano-option-source.params` 是调用方 schema 上的选项查询元数据，不是 `steps[].params`。对应 `option_source` step 的 `params` 必须是空数组。

## 编排

- `request_refs[].step_id` 必须等于 `steps[].step_id`，不要填 `request_id`。
- `request_id` 只允许出现在 `request_facts.requests`。
- 每个能力恰好一个 `execute`；该 `step_id` 不得出现在另一个能力的 `execute`。
- 能力内顺序：`preflight` → `option_source` → `execute` → `fact_check`。`request_refs[].sequence` 必须按这个实际执行顺序编号；页面按 sequence 展示，不要把取详情的 preflight 排到选项接口后面又把 sequence 写反。
- 跨能力：被依赖的查询/选择/创建在前，写入、撤回、删除在后。
- `links` 必须用 `source_step_id` / `source_path` / `target_step_id` / `target_path` 写出**值怎么流**。例如创建响应里的流程实例 ID 进入撤回参数。不要把常量写成“上游映射”，也不要只写一句“有依赖”。`source_step_id` 与 `target_step_id` 不能是同一步。禁止「保存响应 data → 同一条保存的 body.id」。没有下一步用这个值：不要写 link；要回读该对象就挂 `fact_check`。
- `option_source` 只声明候选项来源，禁止把选项列表路径（如 `data[].id`）写成值流 `links`。调用方筛选键留在 schema，由调用方选。选项接口只挂 `option_source`，不要再写 `data[].id → query.xxx`。
- 跨能力的回填（保存后的主键进入提交、查询结果进入编辑）只写 `links`。导出从 `links` 投影绑定，不会把本场样例写成下次执行的常量。
- 目标原文要求先做 A 再做 B、但两条能力之间没有值要传（先查询统计、再新增一笔）时，必须另写 `capability_relations`。不要指望导出从目标原文猜顺序。每项用能力的 `capability_id` 或 `name`：
  `{ "type": "suggested_call_chain", "mode": "handoff", "from_capability": "A", "to_capability": "B", "confirmed": true, "reason": "目标要求先 A 后 B" }`
  有值流仍只写 `links`，不要为同一条值流再编一份关系表。没有顺序要求就不要写组合关系；调用方可以单独只查或只增。
  本场已经做完的页内典型链必须挂 relation：查询→新增、查询→编辑、查询→删除、新增→提交。有值流写 `links`；没有值流也要写 handoff。不要只交散点能力。
  `capability_relations` 只能挂在已经交过、带真实 `request_refs` 的能力旁边。禁止单独再交一项只有关系、没有 execute 的能力。
- `from_path` / `source_path` 必须能在那个响应里读到。提交返回 `{"data":61}` 就是单据 id，不是流程实例 id；流程实例要从随后的详情回读取。写错路径等于编排错误。
- 搜索能力上的接口下拉必须挂 `option_source`。父表单没有的选项请求不要挂。
- 查看能力里随详情带出的附件、审批进度是 `fact_check`，顺序在 execute 之后。不要标成 `option_source`。
- GET 详情的 execute `params` 只写**这次请求真正提交的** query/body（通常是主键）。响应里只读展示的标题、金额、状态不要再写成 `path=body.xxx` 的请求字段。
- `preflight` / `option_source` 可以挂进 `request_refs`，但这两个 step 的 `params` 必须是 `[]`。preflight 必须对得上「点开这张表单」之后发出的请求。上一页列表加载不要挂进来。打开写入表单时附带发出的空抄送列表、附件计数、字典/菜单，尽量不挂；挂了也不得把它们的 query 写成该能力的系统字段。
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
          "status": {
            "type": "string",
            "title": "状态",
            "x-dano-business-type": "single_enum",
            "x-enum-options": [{ "label": "进行中", "value": "1" }],
            "x-enum-value-map": { "进行中": "1" }
          }
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
  "links": [],
  "capability_relations": [],
  "request_facts": { "requests": [] },
  "unresolved": []
}
```

`result` 还须包含：业务理解、成功/失败条件、回读方法、证据引用。这些是说明，不能代替上面的信封，也不能代替台账里漏掉的能力。

## 提交前自检

1. 先列出本场点过的独立业务动作。数量必须等于 `capabilities` + 仍缺证据的 `unresolved`。
2. 每个能力都有互不相同的 `capability_id`、`name`、`title`，以及恰好一个不与其它能力共用的 `execute`。
3. 每个能力的 `request_refs` 都是 `{step_id, usage}` 对象，并能在 `steps` 里找到同名 `step_id`。没有单独交只有 `capability_relations`、没有 `request_refs` 的项。
4. 每个 step 的 `params` 都是数组。有元素时每个元素都有 `key` 和 `path`。`preflight` / `option_source` 必须是空数组 `[]`，不要为了凑这条去抄选项接口或打开表单的 query。
5. 结果里没有 `capabilities[].fields`。
6. 人能填/能选的筛选、表单、下拉、树、页签、日期、附件都在调用方字段里，并且都在 `input_schema`；`visible_control` 里可改的控件没有被写成系统。每个 `exposed_to_user=true` 的 param 都能在 schema 里找到同名 key。灰框/计算/自动编号/行主键/行类型码只在 params 且 `exposed_to_user=false`。
7. 从列表行或上一步响应带出的主键/流程实例 ID 是系统字段，不是调用方输入，不要写进 `input_schema`。
8. 登录态和分页只出现在真正执行查询的那个能力的系统字段里，`exposed_to_user=false`，不要进 `input_schema`，不要污染撤回/删除。
9. 写过“还做了查看/编辑/进度”却没有对应能力，就是失败，必须补能力或写入 `unresolved`。
10. 每个 `option_source` 都能对上该能力的一个调用方下拉。对不上的选项请求不要挂。
11. 日期字段在 schema 和 params 都是 `date`/`datetime`。execute 体里的业务字段没有漏。
12. 每个已建模 param 都有 `reason`。人能填的控件才写来源规则；接口枚举有 URL 和本场选项，页面枚举列出当场看到的 `{label,value}`。看不出公式或来源的字段：写入 `unresolved`，不要用请求原值冒充已解决。回填主键写明从哪一步哪条路径来。
13. 已调用 `list_recording_index`。索引里每个带确认的写入都有能力或 `unresolved`，选择器弹层没有被做成独立能力。
14. `input_schema.required` 与调用方 params 的 `required=true` 一致；`title`/`label` 来自当前页控件，不是另一页。
15. 同一 path 只有一种归属。系统主键没有进 `input_schema`。execute 的键名、重复键次数、数组形状与本场真实请求一致，没有另编一套发不出去的结构。
16. 每条 `from_path` 都能在对应响应里读到。跨能力回填写了 `links`。目标要求先 A 后 B 且没有值流时写了 `capability_relations`。没有把本场主键/单号/正文写成合同常量。
17. `option_source` 只对应本能力可见下拉。附件/审批/流程定义不是 option_source。
18. 没打开过的下拉里没有编造的 `enum_options`。
19. GET 详情 execute 没有把响应展示字段写成请求 `body.*`。
20. 已对照 `visible_control`：可改日期/下拉/树/页签/附件都在调用方；一个区间日期对上的起止键都在调用方；登录身份是 `current_user` 不是写死数字；行类型码/行键只在系统 params，且没有出现在 schema（含 `items.properties`）。
21. `input_schema`（含数组 `items.properties`）的每个 key 都能对上某个 `exposed_to_user=true` 的 param；每个调用方 param 都在 schema 里；系统 key 没有进 schema。
22. execute 的 query/body 没有、当前页也没有对应可改控件的键没有写进 params 或 schema。确认弹层可填意见已完整处理：进了写请求的建成调用方，对不上 path 的写入 `unresolved`，没有编造键。请求头没有被写成业务字段。
23. 空数组/空对象若有对应上传、选人或可增行控件，已标调用方，没有因为本场是空就收成系统。
24. 跳转带入但本页看得到对应控件的筛选，已标调用方。
25. 数组保持一个 path，没有把每行拆成并列字段，也没有把可增行收成一段字符串。调用方合同是行内实际输入框；系统再组装成该数组。编排里没有本场没发出的写请求。
26. execute 的每个 query/body 键要么在该 step 的 `params` 里且来源已认清，要么写入 `unresolved`。禁止「无独立来源，按录制请求原值提交」并标已解决。
27. `input_schema.properties` 的每个顶层 key 都能对上某个 `exposed_to_user=true` 的 param.key。没有把一个数组拆成多个调用方数组。
28. `readonly=true` / `disabled=true` 的控件没有进 `input_schema`。
29. 可增行 `items.properties` 的 title 是表头原文，覆盖各分区可见表头（序号、操作除外）；数组 title 是分区标题原文，用 `/` 或 `；` 连接，没有写成拆不开的「A和B」。同一线格式键在不同分区表头不同时写了 `x-dano-section-titles`。没有把可增行收成 string，也没有用分区标题去命名同请求里的 form textarea。
30. 确认弹层可填意见：有请求键则建成调用方，没有则写入 `unresolved`，没有编造写请求里没有的键。
31. 弹层选人/选记录若对应对象数组：调用方 schema 是带实时候选的多选；execute step 有同容器的 `multi + label_subkey + element_template`；每个对象键来自常量或选项响应（含嵌套路径）；没有复制本场人员对象，也没有新增别名容器。
32. 该动作已按目标把可见字段写上并点出 execute；需要证明绑定的键已经改过值，或已写入 `unresolved`。没有用空表保存换请求形状。没有只靠字段名相似、值相等或排除法定绑定。来源没看清没有写成已经理解业务。
33. 写入/提交类能力的系统栏 params 只许来自 execute。`preflight` / `option_source` 的 params 是空数组。没有把页码、每页条数、排序、状态过滤、占位业务 ID 写成该能力的系统字段。
34. 协助之后没有再 click 同名提交/搜索钮。人已经发出 execute 的，按那条请求交，不要再点一遍。
35. 首屏自动加载没有被当成已经查询。目标要求先查询后新增时，查询条件设齐并看到该查询 execute 之前没有进入新增。
36. 没有在表单仍空、或目标要求的加行还没写上时点保存。
37. 选项接口只挂 `option_source`，没有把选项列表路径写成值流 `links`。
38. 同一张表「保存/存草稿」与「提交/送审」若 path 或效果不同，已拆成两项能力，不要并进提交。
39. 树单击是单值；schema type 与 param type 一致。多分区数组写了 `x-dano-section-titles`，合并行时带分区标题。
40. 目标写了先 A 后 B：有值流则 `links` 已写；没有值流则 `capability_relations` 已写。没有把顺序留给导出去猜。没有把关系当成一项没有 execute 的能力单独交。
41. 灰框/disabled 宿主没有进 `input_schema`。从 URL 带入且锁死的类型是系统 `page_default`。
42. 没有自指 links（execute 响应指回同一 execute）。没有下一步用到的 id 没有编成参数传递。
43. preflight 对得上打开该表单的请求，没有把上一页列表加载挂进来。
44. 多分区可增行：分区标题原文、表头原文、`x-dano-section-titles` 已写；只在一个分区出现的列没有写到所有行；行序号不在 schema。
45. 前端时间戳、看不清的打开时写入键已进 `unresolved`，没有标系统结案。
46. schema type 与 param type、线上实际类型一致。该项 title/intent 没有压成半场短句。

## 最小补证

动手回 Investigator → Skill 2。例如：改一个可改字段、换日期、两组明细不同内容、加减行、读同源前端。未授权不得为验证再提交业务单。补完立刻再交该项，不要等用户结束。

每条绑定保留：来源能力或字段、目标字段、定位规则、类型转换、唯一性、证据、不匹配/多匹配处理。值相等、字段名像、排除法不能单独定案。

## 泛化

- 本 Skill 不绑定任何具体业务页、系统名或字段名。上面的例子只说明形状，不是某页的补丁。
- 只根据本场点击、输入、请求、响应、`visible_control`、截图判断。
- 换一个页面也走同一套切分 / 编排 / 字段形状规则：先摊那次真实操作的请求形状，再摊可见控件；值相等、字段名相似、排除法不能单独定案；对得上可改控件就是调用方且必须进 schema；`readonly`/`disabled` 灰框是系统；树/页签/区间日期同样是调用方；树单击是单值；弹层选人用 `element_template` 从实时选项行组装对象；可增行按行填写再组装成一个对象数组 path；选项接口只挂 `option_source`；目标先 A 后 B 且没有值流时写 `capability_relations`；保存与提交若 path 或效果不同必须两项能力；确认弹层可填意见对不上 path 就 unresolved；看不清的 execute 键写入 unresolved，不要冻成录制常量。
- 证据不够、台账对不齐：写入 `unresolved`，不要猜测成看似可用的残缺能力。
- 字段来源写不出时：写入 `unresolved`。不要冻录制值，也不要为了能导出而结案。

以后再碰到识别缺口，先回到本文件补**通用形状规则**，不要改采集/导出代码：

- 点加行才出现的输入框 → 调用方按行提供，系统再组装成一个数组。
- 多分区可增行 → 数组 title 用 `分区A/分区B`，表头不同就写 `x-dano-section-titles`。
- 弹层勾选记录、execute 提交对象数组 → 不能写死本场所选行。
- 同名大段 textarea ≠ 这些行。
- 确认弹层可填意见对不上请求键就 unresolved。
- 分页已认清才标系统，不准进 schema。
- 跨能力有值流 → `links`。目标先 A 后 B 且没有值流 → `capability_relations`，且必须挂在已有 execute 的能力上。
- 请求有、控件无、也看不出公式 → `unresolved`。
- 打开写入表单附带的空列表、弹层内部翻页 → 不进写入能力系统栏；这些 step 的 params 必须是空数组。
- 灰框/disabled 宿主 → 系统，不进 schema；URL 带入且锁死同样处理。
- 只在一个分区出现的列 → 只属于该分区行。
- 前端时间戳、看不清的打开时写入键 → `unresolved`。
- execute 响应不得 link 回同一 execute。
- preflight 只认打开该表单的请求。
- 页签/折叠头没有请求键 → 不是字段。
