# CABP 深度优化：给 Cursor 的修改说明

只改 `E:\python\try\playwright_CABP`。不要改 `E:\python\try\Dano`。不要改页面台。

框架留下：同一浏览器、无障碍快照、`fN:eN`、点完看这次请求、三份 Skill 文件、`doc/` 里的规则、`verify_skill`。换页出问题，是因为提示词、工具说明和文档里堆了只对旧页面、旧 OA 合同成立的句子。模型到新页面先对那些句子。这次做删减，不新增页面特例。

做完的标准有两条，而且必须换一个从未写进代码的网站再看，不能只重跑原来的页面：

1. 目标里每一个点到名的操作都做过。做过的依据是这次点击返回里的请求或填写结果，不是模型说做过。
2. 产出 `SKILL.md`、`scripts/client.py`、`references/api.md`。读命令已经跑通。`verify_skill` 的 errors 为空。字段、来源、关联、系统自己填的值，都对得上本场请求，样例没有写成下次的默认值。

---

## 不要再做的事

- 不要为某个点不中的控件加选择器、`data-*-ref`、按可见文字或坐标补 ref。
- 不要在提示词、工具说明、校验文案里追加「这种表格 / 这种弹层 / 这种日期 / 这个 path 必须怎样」。
- 不要把按钮名、业务 path、OA 词写进通用说明。
- 不要恢复 `flow.py`、`CONTRACT.json`、能力五块、闸门。
- 不要把 `doc/` 整篇抄进 `prompt.md`。模型写 Skill 之前用 `read_guide` 读瘦身之后的文档。

某一页失败时只改对应层：快照里没有这项，就把未完成项列在快照开头；请求对不上，就把本场 `changed_keys` 摆出来；说明太长，就删句子。不要加一句例外。

---

## 1. 提示词收成短方法

替换 `src/agent/prompt.md` 全文。不要保留现在第 3 行到第 9 行里关于 merged、same_column、goal_exact、弹层最后一层、不要点旁边数字、focused_unchanged 的句子。那些事实如果还在快照或返回值里，模型自己读，不必再写成裁决。

用下面这篇替换：

```text
你在给一个网页产出可执行 Skill。目标原文整段留下。换一页就重新做，不把上一页的名字、path、字段来源带过来。

工具只用 browser_open、browser_snapshot、browser_act、browser_screenshot、network_list、network_get、evidence_get、read_page_asset、read_guide、assist、write_skill_file、run_skill_command、verify_skill。

1. browser_snapshot。ref 整段照抄这张快照里的 fN:eN@数字。过期或返回 stale_ref，就用返回的新快照，不重试旧 ref。
2. 快照开头的 open 是目标里还没做、这张快照里仍有 ref 的项。先做这些，做完一项再看返回的快照。没有 ref 的项不要改点旁边那个有 ref 的控件。对这一项 browser_screenshot，然后停在该项。
3. browser_act 的返回里已有 clicked、requests、changed、snapshot。用这一份，不要为同一次点击再 snapshot。
4. 写进 Skill 的请求先 network_get。只根据这次的 changed_keys、added_keys、filled_value 和已打开的响应判断。值相等、字段名相似、排除法，都不能单独决定来源。
5. 登录或验证码挡住时调用 assist。人在同一页处理。用返回的快照接着做。不新开浏览器，不猜 token。
6. open 为空，或剩下的项已经 screenshot 确认没有 ref，才写文件。
7. read_guide 按索引读取，第一份是 skill-contract.md。成品只有 SKILL.md、scripts/client.py、references/api.md。
8. 每个采用的请求键写一条字段，或放进未解决。samples 只是样例，不能写成下次的默认参数。
9. SKILL.md 里的读命令用 run_skill_command 跑通。没跑通的只留在未解决。然后 verify_skill。errors 为空才结束。页面上点完不算结束。
```

同步改 `src/agent/pi-session.mjs` 的 `nextRecordingPrompt`。催促只重复两句：open 里还有项就继续点；没有了就按校验错误改三份文件并再跑读命令、再 `verify_skill`。不要把控件形态再写进催促。

---

## 2. 工具说明收短

`src/agent/tools.mjs` 里 `browser_act` 的 description 现在是一整段裁决。改成：

```text
对最近一次快照里的 ref 做 open、click、fill、fill_fields、press、select、upload、screenshot。ref 整段照抄 fN:eN@数字。返回 clicked、requests、changed、snapshot、open。用这份快照，不要为同一次操作再 snapshot。requests 为空表示这一下没有 xhr/fetch。写字段前对要采用的 id 调用 network_get。
```

`browser_snapshot` 的 description 改成：`读取当前页快照。开头的 open 是还没做的项。ref 整段照抄。`

`read_guide` 的 description 删掉「本场没有 CONTRACT」以外的复述。保留：按索引读，先读 `skill-contract.md`。

`src/skillpack/verify.mjs` 里返回给模型的 hint 同样删掉控件形态说明，只保留校验错误本身：缺哪个槽、哪条命令没跑通、哪个样例被写成了 default。

---

## 3. 点击：不漏、不错、快，且换页同一套

行为改 `src/browser/snapshot.mjs` 和 `src/browser/actions.mjs`。规则对所有页面相同。

### 不漏

每次快照和每次 `browser_act` 的返回，开头给出 `open`：

- 从目标原文里取出出现在本张快照名称中的项。
- 去掉本场已经 click、fill、press、select、upload 成功的项。
- 剩下且仍有 ref 的，逐行列出：名字、ref、row、popup。
- 目标里点到了、快照里没有 ref 的，另起一行 `no_ref`，只写名字。不要替它找一个邻近 ref。

现在的 `goal_exact` 并进 `open` 后删除 `goal_exact` 这个名字，避免两份名单。

### 不错

- 点击仍只接受当前快照的 `fN:eN@数字`。对不上就返回 `stale_ref` 和新快照，不执行点击。
- 同名多项时列出全部 ref 和 row。程序不替模型选第一条。
- `changed_keys` / `added_keys` 继续只表示同一 path、同一组键相对上一次的变化。没有变化就不要把它写成这次点击造成的。
- 快照行上已有的 `row`、`popup`、值、`required`、`min`、`max`、`step` 保留，这是事实。提示词里不再解释怎么裁决。

### 快

- `browser_act` 已经返回新快照。模型不需要再调一次 `browser_snapshot`。提示词已写明。
- 去掉为某一页加长的固定等待。只保留一次操作后的网络静默，全页面同一个时限。
- 同一次点击不要截多张图。只有该项 `no_ref` 时才 `browser_screenshot`。

### 没有 ref 的控件

组织树、日期、上传、画布经常不在无障碍树里。处理是停在 `no_ref` 并截一张图，不发明点击。这不是漏做。不要为这类控件加第二套点击。

---

## 4. Skill 正确性：用瘦身之后的 `doc/`

写文件前，`read_guide` 仍读 `doc/` 和 `src/agent/skill-contract.md`。先把文档收短，再让模型读。长文档里的 OA 例子、`flow.py`、`CONTRACT.json`、能力 id 是换系统就错的原因。

成品仍然只有：

- `SKILL.md`
- `scripts/client.py`
- `references/api.md`

`config/auth.local.json` 和 `config/runtime.json` 仍由程序写。模型不写 config，三个文件里不写 token、cookie、password。

### 字段

每个采用的请求键在 `references/api.md` 有一条，槽名保持 `skill-contract.md` 里现有的：`page_name`、`caller_name`、`request_path`、`request_key`、`data_type`、`control`、`source`、`required_kind`、`format`、`group`、`evidence_ids`、`constant_reason`。

对不上的键写入「未解决」，并且不出现在 `SKILL.md` 的命令说明里。

### 来源

`source` 只允许：`caller`、`current_user`、`now`、`previous_response`、`other_api`、`constant`、`unknown`。

判定只看本场事实，顺序写在 `skill-contract.md`，不要在提示词里再写一套：

- 键在 `changed_keys` 或 `added_keys` 里，且这次是填写，`filled_value` 是控件收下的值：`caller`。
- 键变了但不是这次填的：打开全文。新值来自更早一条已打开响应，写 `previous_response` 或 `other_api` 并写绑定。新值是没输入过的时间，写 `now`。新值来自当前账号那次响应，写 `current_user`。对不上写 `unknown`。
- 键没变：不要写成调用方参数，也不要用 `samples` 当默认。只有每条请求都一样、页面上没有对应控件、并且已打开的脚本或响应说明了它为什么固定，才写 `constant` 并写原因。否则 `unknown`。

### 关联

有绑定才把上一步的值传入下一步。绑定写 `from`、`to`、`locate`、`transform`、`unique`、`on_mismatch`、`evidence_ids`。`on_mismatch` 只写 `stop` 或 `ask`。写不出绑定就不要猜，改成 `unknown` 放入未解决。上一步失败就停。

### 系统自己处理

`source` 不是 `caller` 的键，命令自己填，不向调用方提问，不放进 `ask_user_question`。`SKILL.md` 用一句话说明命令会自己填。

### 调用方

只有 `source` 为 `caller` 的键才问。问句 id 与 `caller_name` 逐字相同。`samples` 和 `filled_value` 不能写进 `default`。

### 选项和文件地址

按瘦身之后的 `doc/skill-generator-live-options.md`：`option_list` 写成调用前现查的读命令，给人看名称，提交 id。用不到的路径写进未解决。`url_string` 把返回的字符串写进后续请求里看见它被写入的那一项，并写绑定。

### 鉴权

按瘦身之后的 `doc/skill-generator-auth-and-token.md`：先读 `config/auth.local.json` 的 headers，再用 `DANO_AUTH_HEADERS` 覆盖同名头。401 或未登录就停止。不在三个文件里写密钥。标了 `issues_credential` 的请求按证据里的 method 和 url 重放，不另拼 path。

### 办理顺序

按瘦身之后的 `doc/skill-generator-workflow.md`：`SKILL.md` 先写默认的完整办理，再写单条命令。写操作的命令含 `--confirm`，只有确认之后才发送 POST、PUT、PATCH、DELETE。任一步失败就停。

### 提问形状

按瘦身之后的 `doc/skill-generator-ask-user-question-guide.md`。生成时不调用提问。相关字段合成一次 `title` 加 `questions[]`。每一项有 `id`、`question`、`inputType`、`required`。长文本 textarea，日期 date，固定选项 select 或 radio，候选项来自读命令的用 dataSource。写操作在参数收齐后写 `confirm: true` 和 formIds。

### 跑通才算正确

`SKILL.md` 里的读命令必须 `run_skill_command` 跑通。写命令只在目标要求写入、并且页面上已经发生过那次请求时标成已验证。不为了验证再提交一笔业务。没跑通的只留在未解决。`verify_skill` 的 errors 为空才结束。

---

## 5. 四份文档怎么删

保留文件名和路径，正文换短。`src/agent/guides.mjs` 的 `PREFACE` 与短正文重复的句子删掉，避免同一条规则出现两次。

### `doc/skill-generator-live-options.md`

已是短文。保留现有 9 行。不要加业务例子。

### `doc/skill-generator-workflow.md`

删掉合同五块、`routes[]`、`capability_id`、`flow.py`、`client.http_json`、`route default`。换成下面这些句子：

- 一个 Skill 是这次目标的整段办理，不是能力点目录。
- `SKILL.md` 第一段是默认完整办理，后面才是单条命令。
- 每条命令一行 `python scripts/client.py`。
- 写操作含 `--confirm`。未确认不发送 POST、PUT、PATCH、DELETE。
- 只有绑定才把上一步的值传入下一步。没有绑定就停下来问。
- 任一步失败就停，保留已经完成的结果。
- 不写「每次只执行一项」「不得自行串联」。

### `doc/skill-generator-auth-and-token.md`

删掉 `subsystem: oa`、从合同 execute step 取 origin、Dano 出包语气。保留：

- 程序写入 `config/runtime.json`（`base_url` 来自本场请求的 origin，取不到就留空并在鉴权节写明要调用方提供）和 `config/auth.local.json` 的 headers。
- 三个 Skill 文件不写密钥。
- 客户端先读本地 headers，再用 `DANO_AUTH_HEADERS` 覆盖同名头。
- 401 或未登录就停止，不用录制样本顶替。
- `issues_credential` 按证据 url 和 method 重放。

### `doc/skill-generator-ask-user-question-guide.md`

这份有一千多行，而且写的是 OA 和 Dano。整份换成一页，只留提问形状：

- 生成时不调用 `ask_user_question`。形状写进 `SKILL.md`，供以后执行。
- 只问 `source` 为 `caller` 的键。
- 一次相关字段一个 `title` 加 `questions[]`。
- 每项：`id`（等于 `caller_name`）、`question`、`inputType`、`required`。
- textarea、date、select、radio、`multiple`。候选项来自 `option_list` 读命令时用 dataSource，endpoint 是那条 path。
- 不写 `samples` 或 `filled_value` 当 default。
- 写操作在参数收齐后：`confirm: true` 与 formIds。命令行含 `--confirm`。
- 文档里的业务例子不写进这份 Skill。

不要把原文里的 OA 字段、审批、租户示例留在文件里。

### `src/agent/skill-contract.md`

槽名、来源判定、绑定、三份文件、跑读命令，已经是换页成立的。保留。删掉与瘦身文档重复的长解释。来源判定的 1、2、3、4 步留下，这是字段和来源的唯一标准。

### `src/agent/guides.mjs`

`guidesFor` 的选择逻辑留下：总是读契约和鉴权；有 `option_list` 或 `url_string` 才读活选项；本场业务请求不少于两条才读办理流程。提问文档在要写 `caller` 字段时读取。不要每次把四份长文都塞进上下文。

`writing-for-agents.md` 与 `writing-for-agents-mechanics.md` 若只重复 name 和 description，不必再作为 guide。`SKILL.md` 的 frontmatter 规则留在 `skill-contract.md` 的「手册和脚本」一节即可。

---

## 6. 快照代码里可以删的特例

`src/browser/snapshot.mjs` 里这些是为某一类表格补的裁决，不应当再驱动模型：

- `markMergedCells`、快照上的 `merged`、以及「有不带 merged 的格子就不要点 merged」。
- 点到 `columnheader` 就展开 `same_column` 并要求点其中一行。列名作为单元格上的事实可以留。不要因此自动改点击目标。
- `stripEmbeddedNames` 按按钮后缀裁名字。裁错会让下一名对不上。删掉，名字以快照原文为准。

删之前先改对应测试：`test/snapshot-columns.test.mjs`、`test/browser-ref.test.mjs`、`test/fill-value.test.mjs`。测试改为断言通用事实：ref 过期被拒绝、同名多项都列出、`open` 含未做项、`changed_keys` 只含这次变化的键。不要断言某一页的合并单元格或表头展开。

`src/browser/actions.mjs` 里返回 `same_column` 的分支一并去掉。点击结果保留 `clicked`、`requests`、`changed`、`snapshot`、`open`、`filled_value`。

---

## 7. 改完怎么验收

用两个互相不像的站点各跑一次。第二次之前不许改提示词、工具说明或文档。

每次看这几件事：

- 进展里没有「因为这种控件所以改点旁边」。
- `open` 里的项要么点过，要么标了 `no_ref` 并有截图。
- `references/api.md` 每个采用的键都有 `source` 和 `evidence_ids`。
- `source` 为 `caller` 的才出现在提问里。系统字段有一句话说明命令自己填。
- 有上一步传值的地方写了绑定。没有绑定的在未解决里。
- `samples` 没有出现在 `default` 或脚本常量里，除非 `source` 是 `constant` 并且写了原因。
- 读命令退出成功。`verify_skill` 的 errors 为空。
- 三个文件里没有 token、cookie、password。

第二次站点如果必须改代码才能过，只允许改事实层：快照没列出已有 ref、或 `changed_keys` 没标出这次变化的键。不允许把这个站点的控件名或 path 写进 `prompt.md` 或 `doc/`。
