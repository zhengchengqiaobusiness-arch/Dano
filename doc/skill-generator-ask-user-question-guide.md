# 调用方输入

调用方填写的字段写在 `SKILL.md` 参数和 `scripts/client.py` 命令行，不要写成页面怎么点。一张 `title` + `questions[]` 覆盖 `overlay_editable` 的每一项，以及 listed_write_keys 里来源为 caller 的键。不要另造一张更短的表。嵌套数组保持为行参数，不要压成一个字符串。

## 要问调用方

- 继续办理前必须取得的输入
- overlay_editable 上的控件（required 仍空的先问完再提交）
- 页面 combobox / listbox / radio：先跑 listed 里的选项命令，把返回的 id/label 写入 `options`，不要把命令行写成 option
- 页面日期：`inputType: date` 且必须有 `dateFormat`
- textbox：文本
- 需要调用方核对即将提交的值

读：没有调用方槽就立刻执行，不要确认卡。写：同一张 `title` + `questions[]`（不要把字段拆成多张表）。调用方提交该表后，下一轮只发 `{"confirm":true,"formIds":["<本表 formId>"]}`，再跑 `python3` 写命令加 `--confirm`。字段表和确认卡不要写在同一次调用里。不要 `dataSource`。

`SKILL.md` 里放一块可原样复制的 JSON，调用方每轮只调一次 `ask_user_question`：

```json
{
  "title": "办理",
  "questions": [
    {"id": "title", "question": "标题", "inputType": "text", "required": true, "default": " "},
    {"id": "start_date", "question": "开始日期", "inputType": "date", "dateFormat": "yyyy-MM-dd", "required": true, "default": " "},
    {"id": "dept", "question": "部门", "inputType": "select", "required": true, "options": [{"id": "opt-1", "label": "名称一"}, {"id": "opt-2", "label": "名称二"}], "default": "opt-1"}
  ]
}
```

`options` 里的 id/label 必须是本场已跑选项命令或 listed 响应里的值；示例中的 `opt-1` 只说明形状。页面 combobox / listbox / radio / select / tree 的 item 必须有 `options`，不要改成 `inputType: text`。页面日期必须是 `date` + `dateFormat`。`default` 必填且非空，不要填录制当时的业务 id。

## 不要问

- 只读展示、系统计算
- overlay_editable 没有对应控件、只能从 listed 再读的隐藏键（当前账号身份、服务器默认）
- 字段是否存在、如何映射——这些在生成 Skill 时由证据决定
- 只需说明结果，不需要新输入

相关字段放在同一条命令的参数里。每个调用方字段给出当前业务上下文里的非空说明，用父节点或列名，不要用占位字当地名。手册里参数是否必填必须和 argparse 的 required/default 一致。overlay 的 required 是页面事实，argparse required 是调用方能否省略，二者都要和来源一致，不等于页面上填过。不要把录制示例写成默认答案。
