---
name: sale-order-operations
description: "销售订单办理：用户通常会说查订单、看详情、新增、编辑、审核、反审核或删除。办理顺序是先查询或查看，再对选中的订单做编辑、审核、反审核或删除；新增是单独创建。只查询或只查看时不要写入。缺目标时必须让用户选定一条。写完要能确认结果。不要用于其它业务对象或未列出的动作。"
version: "1.0.0"
compatibility: 需要 Python 3 与 httpx。鉴权只来自运行期 DANO_AUTH_HEADERS 或本地会话缓存。
metadata:
  domain: recorded-business
  category: page-operation
  risk: high
allowed-tools: Bash Read
---

# 销售订单办理

执行面是 `scripts/` 里的命令，不要另写流程或编造接口。

## 适用场景

- 销售订单办理：用户通常会说查订单、看详情、新增、编辑、审核、反审核或删除。办理顺序是先查询或查看，再对选中的订单做编辑、审核、反审核或删除；新增是单独创建。只查询或只查看时不要写入。缺目标时必须让用户选定一条。写完要能确认结果。
- 只要搜索/筛选销售订单，不要改也不要写
- 只要查看销售订单详情，不要改也不要写
- 要执行「修改销售订单」且已指定对象或已备齐字段时
- 要执行「审批销售订单」且已指定对象或已备齐字段时
- 要执行「反审销售订单」且已指定对象或已备齐字段时
- 要执行「新建销售订单」且已指定对象或已备齐字段时
- 要执行「删除销售订单」且已指定对象或已备齐字段时
- 帮我查鲜生的单
- 只看看这张订单详情
- 先查出订单再编辑
- 查出后再审核那张
- 新增一张销售订单

## 不适用场景

- 不要用于其它业务对象或未列出的动作。
- 只要查询或查看时，不得执行写入。
- 没有已确认绑定却假装已经串联时停止，先查再问。
- 不得编造字段、接口、输出或未确认关系。

## 能力关系

销售订单办理：用户通常会说查订单、看详情、新增、编辑、审核、反审核或删除。办理顺序是先查询或查看，再对选中的订单做编辑、审核、反审核或删除；新增是单独创建。只查询或只查看时不要写入。缺目标时必须让用户选定一条。写完要能确认结果。

- 组合约定：销售订单办理：用户通常会说查订单、看详情、新增、编辑、审核、反审核或删除。办理顺序是先查询或查看，再对选中的订单做编辑、审核、反审核或删除；新增是单独创建。只查询或只查看时不要写入。缺目标时必须让用户选定一条。写完要能确认结果。
- 只要只读操作时，只执行对应只读操作，不得执行写入。
- 没有已确认绑定，不能自动传值。先后办理就先查再问：先执行只读操作，停下来请用户指定记录，再写。
- 没有自动传值；先后办理就先查再问。

## 操作路由

先把用户意图映射到下表中的一条操作，或「能力关系」里的一条组合路线。

| 用户意图 | 操作 | 脚本 | 必填输入 | 写前确认 | 写后验证 |
|---|---|---|---|---|---|
| 只要搜索/筛选销售订单，不要改也不要写 | `search_sale_orders` | `python scripts/search_sale_orders.py` | 无 | 否 | 否 |
| 只要查看销售订单详情，不要改也不要写 | `get_sale_order` | `python scripts/get_sale_order.py` | 无 | 否 | 否 |
| 要执行「修改销售订单」且已指定对象或已备齐字段时 | `update_sale_order` | `python scripts/update_sale_order.py` | `id` | 是 | 是 |
| 要执行「审批销售订单」且已指定对象或已备齐字段时 | `approve_sale_order` | `python scripts/approve_sale_order.py` | `id` | 是 | 是 |
| 要执行「反审销售订单」且已指定对象或已备齐字段时 | `unapprove_sale_order` | `python scripts/unapprove_sale_order.py` | `id` | 是 | 是 |
| 要执行「新建销售订单」且已指定对象或已备齐字段时 | `create_sale_order` | `python scripts/create_sale_order.py` | `id` | 是 | 是 |
| 要执行「删除销售订单」且已指定对象或已备齐字段时 | `delete_sale_order` | `python scripts/delete_sale_order.py` | `id` | 是 | 是 |

## 输入

- 字段名、类型、必填和候选项以 `references/CONTRACT.json` 的 `input_schema` 为准。
- 已绑定字段不重问，用上一步输出。
- 其余字段按 `references/INPUT_FORMS.md` 原生调用 `ask_user_question`。
- 写前确认。

## 操作步骤

1. 用用户原话对照「操作路由」和「能力关系」，只选一条路线；只读不得升级成写。
   Done when: 选出恰好一条。
2. 若选的是组合或「先查再办」：先跑只读脚本；有绑定则带入；无绑定则停下来问记录。
   Done when: 下一步输入已齐或用户取消。
3. 读当前这条的 `references/OPERATIONS.md` 小节和 `references/CONTRACT.json` 的 `input_schema`，补齐字段。
   Done when: 必填齐或用户取消。
4. 执行对应 `python scripts/<x>.py --input-json '...'`；写操作加确认。
   Done when: stdout 最后一行 JSON `status=succeeded` 且 `ok=true`。
5. 需要验证再跑 `verify_*.py`；列表先 `format_list.py`。
   Done when: 整条路线结束并按输出格式汇报。

## 工具

触发后必须用本包脚本执行，不要用浏览器点击或临时脚本代替。

- 搜索/筛选销售订单：`python scripts/search_sale_orders.py`
- 查看销售订单详情：`python scripts/get_sale_order.py`
- 修改销售订单：`python scripts/update_sale_order.py`；验证 `python scripts/verify_update_sale_order.py`
- 审批销售订单：`python scripts/approve_sale_order.py`；验证 `python scripts/verify_approve_sale_order.py`
- 反审销售订单：`python scripts/unapprove_sale_order.py`；验证 `python scripts/verify_unapprove_sale_order.py`
- 新建销售订单：`python scripts/create_sale_order.py`；验证 `python scripts/verify_create_sale_order.py`
- 删除销售订单：`python scripts/delete_sale_order.py`；验证 `python scripts/verify_delete_sale_order.py`

## 输出

- 查询：返回业务结果；数组用 Markdown 表格，无数据时写“无数据”。
- 写入：报告已执行的操作、关键业务字段和脚本结果；不要把内部 ID 擅自命名为业务编号。
- 组合路线：按步骤汇报每步结果，最后给整条路线的结论。

## 完成标准

- 已走用户意图对应的那条路线，没有多执行未要求的写入。
- 查询已返回业务结果；写入已确认并执行成功。
- 仅当操作路由标明写后验证时，还须验证通过。
- 准确区分成功、待确认、取消和失败。

## 失败处理

- 信息不足：停止并追问缺失字段，不得编造。
- 用户取消或写操作未确认：立即停止，不得执行。
- 脚本或验证失败：立即停止并报告原因；写结果不明时不得重试同一载荷。
- 权限或鉴权失败：停止并说明需要运行期登录凭证，不得伪造身份。
- 用户要求的组合没有已确认绑定：先做查找，再请用户指定记录，不要假装已经串联。

## 安全边界

- 不输出 token、cookie、密码或其他凭证。
- 不跳过写前确认；有写后验证时不得跳过验证，不绕过权限。
- 不发明字段、接口或未确认关系，不把未规划组合当成已确认编排。
- 只使用当前页面已打包操作，不得发明字段、接口或输出。

## 资源

触发后阅读：当前操作的 OPERATIONS 小节；CONTRACT 里该操作 input_schema。
按需阅读：INPUT_FORMS.md；CONTRACT 全文。
