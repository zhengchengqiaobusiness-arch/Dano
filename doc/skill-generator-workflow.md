# 办理流程：可调用三文件 Skill

成品只有 `SKILL.md`、`scripts/client.py`、`references/api.md`。没有 `CONTRACT.json`、`flow.py`。调用方执行手册里的 `python3 scripts/client.py …` 行。`SKILL.md` 必须以 `---` 包住 `name` 和 `description`。

## 谁做什么

- 代码运输当前快照树、clicked、listed（全部 xhr/fetch 索引）、请求全文、overlay_fields（empty / required / readonly）、overlay_editable（当前层非只读可填：有 dialog/menu/listbox 用那一层，没有则是树上非 chrome 的 textbox/combobox）、listed_write_keys、actor=human。
- PI 对着目标原文、当前树、listed、overlay_editable、listed_write_keys 和全文决定点哪个 ref、每个字段谁填、值从哪条证据来、调用方控件用文本还是选项。
- 本指南和另外五份指南是方法。不要把某一页的按钮名、path 写进方法。

## 字段来源

每个写入键必须标明来源，且和命令行谁填一致。`source` 只许：

1. `caller` 调用方填写或选择
2. `other_api` listed 里已录选项/字典请求再读，再让调用方选
3. `current_user` 当前账号身份（overlay_editable 没有对应控件）
4. `now` 系统计算时间
5. `previous_response` 上一步响应
6. `constant` 固定协议常量，必须另写 `constant_reason`，不能是「录到的就是这个」
7. `unknown` 进未解决，不要写进可执行命令

`overlay_editable` 上的控件是 `caller`，或选项接口 `other_api` 再读后调用方选。listed 再读给的隐藏键，若 overlay_editable 没有对应控件，才是 `current_user` / `now` / `previous_response` / `constant`。页面上填过的框不等于调用方必填。overlay 的 required 来自 aria-required 或名字/列名里的 `*`，和 argparse required、问句是否必填必须一致。listed 某键这次 empty/[] 只说明这次传了空；同场另有选项请求则再读后让调用方选，不要冻成空数组。读命令不要写死本场 listed 里的业务 id。身份键从本场身份接口现取，不要问人。基地址只读 `DANO_BASE_URL` 或 `config/runtime.json` 的 `base_url`，不要写死源站。问句覆盖 `overlay_editable` 的每一项和 listed_write_keys 里来源为 caller 的键。同一张 overlay 一张 `questions[]`，不同 overlay 可各一张，调用方每轮只用一张。嵌套数组按列写成可重复命令行参数，问句用一个 textarea，一行一项、列之间用「|」；问句和 default 不写 JSON。

调用方字段形状必须和页面一致：活列表 `options: []`，调用时先跑读命令再填；封闭枚举把 id/label 写进 `options`；不要把命令行写成 option。`inputType` 只许 text、textarea、date、select、radio、treeSelect、checkbox。页面日期用 `date` 和 `dateFormat`；textbox 才是文本。不要 `dataSource`（调用方只接受同源地址）。

SKILL.md 写一段「调用方怎么问」：读不问不确认；写第一次原样整份发 questions[]，已知值放 default 不再另问，查到的业务日期直接作 default；提交后同一轮只发 `{"confirm":true,"formIds":[...]}`，不另造确认题；再跑写命令加 `--confirm`。

## 命令

- 每个可执行命令在 `references/api.md` 指向证据 id。标题可以是 `#` 或 `##`，必须是「字段」「绑定」「未解决」「已验证读命令」。
- `SKILL.md` 里出现的每一条读命令都要真的跑，包括表格单元格和行首的 `python3 scripts/client.py`。
- 每个 `add_parser` 必须 `set_defaults(func=)`。
- 同一 path 若新增和修改条件不同，写成两个命令。
- 只有存在绑定才把上一步的值传入下一步。前一步失败就停。
- 目标含读：把读命令真的跑一遍。
- 目标只要求写入：不要发明 GET。写命令只在页面上已经发生过那次请求时才标已验证。
- 未解决只写 `body_missing`，或证据 id 不在 listed。其余继续查到可调用为止。
