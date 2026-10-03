# 提问形状

生成时不调用提问工具。调用方以后要提供的内容，把提问形状写进 `SKILL.md`。

页面上填过、或系统自己写入请求的键，不要问调用方。只有调用方下次执行时必须提供的，才写成问题。

一次相关字段一个 title 加 `questions[]`。每项有 id（与参数名相同）、question、inputType、required。

长文本用 textarea。日期用 date。固定选项用 select 或 radio。多选用 `multiple`。候选项来自现查读命令时，用 dataSource，endpoint 是那条 path。

不把录到的样例写成 default。

写操作在参数收齐后：`confirm: true`。命令行含 `--confirm`。
