# 调用方输入

调用方填写的字段写在 `SKILL.md` 参数和 `scripts/client.py` 命令行，不要写成页面怎么点。同一张 overlay 一张 `title` + `questions[]`，覆盖该层 `overlay_editable` 的每一项，以及 listed_write_keys 里来源为 caller 的键。不同 overlay 可各一张表，调用方每轮只用一张。不要另造一张更短的表。嵌套数组（表格行）按列写成可重复命令行参数，问句用一个 `textarea`：一行一项、列之间用「|」，括号里只写列名顺序。问句和 `default` 不出现 JSON、数组、类型名。

`inputType` 只许 `text`、`textarea`、`date`（必须有 `dateFormat`）、`select`、`radio`、`treeSelect`、`checkbox`。调用方的 `dataSource` 只接受同源地址，业务站不同源，所以不要 `dataSource`。

## 要问调用方

- 继续办理前必须取得的输入
- overlay_editable 上的控件（required 仍空的先问完再提交）
- 页面 combobox / listbox / radio / tree：活列表 `options: []`，调用时先跑读命令再填；封闭枚举把 id/label 写进 `options`。不要把命令行写成 option
- 页面日期：`inputType: date` 且必须有 `dateFormat`
- textbox：文本
- 表格列：问句 `textarea`，一行一项、列用「|」
- 需要调用方核对即将提交的值

## 调用方怎么问（原样写进 SKILL.md）

读：无调用方槽立刻执行；已确定的不进本次 questions[]，不发 ask_user_question，不发确认卡，不要另造查询确认卡。写：第一次把 SKILL.md 里这张 title+questions[] 原样整份发出，id、question、inputType、options、required 不改，不增题、不减题、不另造确认题；对话里已确定的值放进对应 default，不再另问，上一步查到的业务日期直接作 default，不改成当天；调用方提交后同一轮只发 {"confirm":true,"formIds":["<formId>"]}；确认后跑 python3 写命令加 --confirm。

`SKILL.md` 里放可原样复制的 JSON，调用方每轮只调一次 `ask_user_question`：

```json
{
  "title": "办理",
  "questions": [
    {"id": "title", "question": "标题", "inputType": "text", "required": true, "default": " "},
    {"id": "start_date", "question": "开始日期", "inputType": "date", "dateFormat": "yyyy-MM-dd", "required": true, "default": " "},
    {"id": "kind", "question": "类型", "inputType": "radio", "required": true, "options": [{"id": "1", "label": "甲"}, {"id": "2", "label": "乙"}], "default": " "},
    {"id": "dept", "question": "部门", "inputType": "treeSelect", "required": true, "options": [], "default": " "},
    {"id": "items", "question": "明细（每行一项：内容 | 进度）", "inputType": "textarea", "required": true, "default": " "}
  ]
}
```

活列表手册 `options` 留空；封闭枚举才把 id/label 写进手册。手册里 `default` 用空格占位，调用时由调用方按对话上下文填成非空；不要填录制当时的业务 id，不要写 JSON。页面 combobox / listbox / radio / select / tree 不要改成 `inputType: text`。页面日期必须是 `date` + `dateFormat`。

## 不要问

- 只读展示、系统计算
- overlay_editable 没有对应控件、只能从 listed 再读的隐藏键（当前账号身份、服务器默认）
- 字段是否存在、如何映射——这些在生成 Skill 时由证据决定
- 只需说明结果，不需要新输入

相关字段放在同一条命令的参数里。每个调用方字段给出当前业务上下文里的非空说明，用父节点或列名，不要用占位字当地名。手册里参数是否必填必须和 argparse 的 required/default 一致。overlay 的 required 是页面事实，argparse required 是调用方能否省略，二者都要和来源一致，不等于页面上填过。不要把录制示例写成默认答案。
