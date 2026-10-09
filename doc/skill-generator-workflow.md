# 办理流程：可调用三文件 Skill

成品只有 `SKILL.md`、`scripts/client.py`、`references/api.md`。没有 `CONTRACT.json`、`flow.py`。调用方执行手册里的 `python scripts/client.py …` 行。`SKILL.md` 必须以 `---` 包住 `name` 和 `description`。

## 谁做什么

- 代码运输当前快照树、clicked、listed（全部 xhr/fetch 索引）、请求全文、overlay_fields（empty / required / readonly）。
- PI 对着目标原文、当前树、listed、overlay_fields 和全文决定点哪个 ref、每个字段谁填、值从哪条证据来。
- 本指南和另外五份指南是方法。不要把某一页的按钮名、path 写进方法。

## 字段来源

每个写入键必须标明来源，且和命令行谁填一致：

1. 调用方填写
2. listed 里已录请求再读（当前账号、部门、权限、默认值）
3. 上一步响应
4. 系统计算
5. 固定协议常量
6. 空（empty 里的键传空）
7. 运行时配置

禁止把录制当时的字面量冻成默认。页面上填过的框不等于调用方必填。系统自动带的、当前账号身份、只读展示不要写成调用方参数。overlay 的 required 来自 aria-required 或名字/列名里的 *，和 argparse required、问句是否必填必须一致。listed 某键这次 empty/[] 只说明这次传了空；同场另有选项请求则再读后让调用方选，不要冻成空数组。基地址用运行时配置或 DANO_BASE_URL，录制宿主只作缺省。问句覆盖 overlay 里调用方填的控件和 adopted 请求的每一个 caller 键；嵌套数组保持为行，不要压成一个字符串，不要另造一张更短的表。

## 命令

- 每个可执行命令在 `references/api.md` 指向证据 id。没有证据 id 的命令不要写进 `SKILL.md`。
- `SKILL.md` 里出现的每一条读命令都要真的跑，包括表格单元格和行首的 `python scripts/client.py`。
- 同一 path 若新增和修改条件不同，写成两个命令。
- 只有存在绑定才把上一步的值传入下一步。前一步失败就停。
- 目标含读：把读命令真的跑一遍。
- 目标只要求写入：不要发明 GET。写命令只在页面上已经发生过那次请求时才标已验证。
- 未解决只写 `body_missing`，或证据 id 不在 listed。其余继续查到可调用为止。
