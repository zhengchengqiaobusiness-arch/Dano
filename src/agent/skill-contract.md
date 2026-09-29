# 写这一份 Skill

成品只有三份文件：`SKILL.md`、`scripts/client.py`、`references/api.md`。程序写 `config/`。不写 `flow.py`、`CONTRACT.json`。

活选项、提问、办理、鉴权以这一场读到的 guide 为准。这里只写字段和来源。

做完的标准：每个采用的请求键都有一条字段，或出现在「未解决」；每条绑定都能指出证据；`SKILL.md` 里的读命令已经跑通；`verify_skill` 的 errors 为空。

## 先看哪些事实

- 目标原文
- `requests`：方法、路径、证据 id、`keys`、`samples`、`changed_keys`、`added_keys`、`changed_by`、`empty`、`option_list`、`records`、`url_string`、`url_path`、`issues_credential`
- `filled`、`filled_value`、`also_changed`
- 快照里控件自己标的 `[required]`、`min`、`max`、`step`

`samples` 是录到的样例。不能写成下次调用的默认参数。

要采用的请求用 `network_get` 打开全文。正文没打开，就不能写这个键的来源。

## 每个键四件事

`references/api.md` 使用标题「字段」「绑定」「未解决」「已验证读命令」。

- page_name
- caller_name：不是调用方提供时，写和 request_key 相同的字
- request_path
- request_key
- data_type
- control：没有就写 none
- source：`caller`、`current_user`、`now`、`previous_response`、`other_api`、`constant`、`unknown` 之一
- required_kind：`page`、`caller_all`、`server_verified`、`server_unknown` 之一
- format
- group
- evidence_ids
- constant_reason：只有 source 为 constant 时写。不能写「录到的就是这个」

一条字段用 `- page_name:` 起头。也可以做成表，表头就是这些英文槽名。`page_name` 和 `request_path` 可以写在表前，后面每一行沿用，直到下一个标题。

同一个 request_path 加 request_key 只写一条。分不清的另一条放未解决。

source 为 current_user、previous_response、other_api 时，绑定的 from 和 to 要不同，并写出这个 request_key，evidence_ids 含这条字段的证据。写不出绑定就改成 unknown，并在未解决里写上这个键。

按下面的观察选 source。一次只对一个键。值相等、字段名相似、排除法，都不能单独决定来源或绑定。

1. 键在 `changed_keys` 或 `added_keys` 里，并且 `changed_by` 的那一下是填写，`filled_value` 是控件收下的值。source 写 `caller`。caller_name 用控件名字。控件那一行有 `[required]`，required_kind 写 `page`。目标原文要求全填，写 `caller_all`。服务端曾经因缺这个键拒绝，才写 `server_verified`。其余写 `server_unknown`。
2. 键变了，但不是这次填进去的。打开全文看新值从哪来。新值来自更早一条已打开响应里的字段，source 写 `previous_response` 或 `other_api`，并写一条绑定。新值是没有输入过的时间，source 写 `now`。新值来自当前账号那次响应，source 写 `current_user`。对不上就 source 写 `unknown`，并在「未解决」里写这个键。
3. 键不在 `changed_keys` 里。不要把它写成调用方参数，也不要用 `samples` 里的字面值当默认。每一条请求都一样、页面上没有对应控件、并且已打开的脚本或响应说明了它为什么固定，source 才写 `constant`，并写原因。否则 source 写 `unknown`，放进「未解决」。
4. 脚本里这个键仍然要发出去。调用方的键来自参数。其余的键在命令里按证据再读：上一步结果、当前用户、当前时间，或 `empty` 里的键传空。引用这条 path 的函数里要逐个写出键名。只写 `data=data` 不算写过。

`unknown` 的键放在「未解决」，并且不出现在 `SKILL.md` 的命令说明里。

## 绑定

一条绑定写 from、to、locate、transform、unique、on_mismatch、evidence_ids。on_mismatch 只写 `stop` 或 `ask`。表头用这些英文槽名。

只有绑定才把上一步的值传入下一步。没有绑定就停下来问。上一步失败就停，保留已经完成的结果。

## 手册和脚本

`SKILL.md` 从 `---` 起行，写 name 和 description，再写一行 `---`。description 写调用方在什么意图下启用，以及这份 Skill 做什么，用目标原文。不用接口路径充当描述。name 用小写短横线。不写 disable-model-invocation。

先写默认的完整办理，再写单条命令。每条命令一行 `python scripts/client.py`。会变的参数写成 `<caller_name>`。同一子命令只写一行。source 为 `caller` 的名字各占调用方要提供的一行。系统自己处理的键用一句话说明命令会自己填，不做成调用方必填。

`scripts/client.py` 只用 Python 标准库。不带其它子命令运行时不发出写入请求。`show-config` 只检查配置，不打印头的值。

读命令用 `run_skill_command` 真的跑。argv 是字符串数组。失败就改脚本再跑。写命令只在目标要求写入、并且页面上已经发生过那次请求时，才标成已验证。不为了验证再提交一笔业务。没跑通的命令只出现在「未解决」，不出现在 `SKILL.md` 的可执行命令里。

每个可执行命令在字段或「已验证读命令」旁写证据 id。没有证据 id 的命令不写进 `SKILL.md`。
