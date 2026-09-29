# 提问形状

生成时不调用 ask_user_question。形状写进 SKILL.md，供以后执行。

只问 source 为 caller 的键。

一次相关字段一个 title 加 questions[]。

每项：id（等于 caller_name）、question、inputType、required。

长文本用 textarea。日期用 date。固定选项用 select 或 radio。多选用 multiple。候选项来自 option_list 读命令时用 dataSource，endpoint 是那条 path。

不写 samples 或 filled_value 当 default。

写操作在参数收齐后：confirm: true 与 formIds。命令行含 --confirm。

文档里的业务例子不写进这份 Skill。
