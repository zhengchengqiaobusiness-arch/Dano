# 四 Skill 运行约定

对照仓库：`E:\python\try\Dano`（核心 `Pi_check/`，出包运输在 `Pi_check/src/skill-export/`，禁止引用 `back/dano/export/`）  
录制会话只加载 Skill 1–3。出包会话只加载 Skill 4。Investigator 是录制唯一入口。代码只运输，不认业务。

以后换页、认错字段、handbook 不好、不该发布：默认只改四个 Skill 文件。不要改闸门、导出猜测、采集业务词。

---

## 1. 产品节奏

本场默认自动控制，直接产出能力。人不用先接入，也不用等人说「结束」。

```text
用户目标 + 入口 URL
        ↓
PI = Business Skill Investigator（唯一入口）
├── Control In App Browser          自动点、填、选、取证
├── Infer Business Contract         一项做完立刻交合同
        ↓
submit_recording_result({final:true, use_draft:true}) 交出能力并停
        ↓
用户点击「产出 Skill」→ Skill 4 另开会话写专用包
```

| 情况 | 谁做 |
| --- | --- |
| 打开入口、设查询条件、填表、点查询/保存/提交 | PI 自动做 |
| 某一行已有真实 execute | 立刻交一项能力 |
| 目标页与台账都齐了 | 交出能力并停，不要写消费者包 |
| 用户点击「产出 Skill」或 Skills 目录重导 | 新建 Skill 4 会话，按最新能力出包 |
| 登录、验证码、授权写入 | 阻断，assist 这一处 |
| 写不进、选项看不见、点了不发网、加行后仍无控件 | 阻断，assist 这一格 |
| 「登录了 / 继续 / 好了」 | 阻断解除，自动接着做完并产出 |
| 用户主动说结束或点停录 | 提前收口，交已完成的行 |

不是阻断：人没有说话、人没有点预览、人没有说结束、PI 还想再确认一遍。这些都继续自动做。

做完后禁止为「再验证一下」换页空转到超时。超时只停自动点，不是失败；台账已齐仍应定稿。

---

## 2. 四个文件

| 文件 | 负责 | 禁止 |
| --- | --- | --- |
| `skill/BUSINESS_SKILL_INVESTIGATOR.md` | 目标、台账、下一步、分诊、目标做完即交能力并停 | selector、source_kind、handbook、写消费者包 |
| `skill/CONTROL_IN_APP_BROWSER.md` | 点、填、选、快照、图像、同源前端 | 交能力、认来源 |
| `skill/INFER_BUSINESS_CONTRACT.md` | 切能力、来源、绑定、信封 | 点页面、写消费者包、冻录制值 |
| `skill/BUILD_AND_VALIDATE_DEDICATED_SKILL.md` | 写包、投影、隔离运行、能不能发布 | 回头猜页面、等用户结束 |

每份开头三行：本文件只负责 X；禁止 Y；缺口只改本文件。

录制定稿**不要求**用户先说结束，也不启动 Skill 4。同时满足即可交能力并停：

1. 目标原文要求的每一页、每一行已经做完，或已写入 `unresolved`
2. 台账每行有合同或 `unresolved`
3. 写入行没有「未识别却当可执行」

出包另开会话。Skill 4 必须读完 `doc/` 四份规范，写出流程、鉴权槽位和活选项；运输层只注入冻结 client/auth 并写入 Skills 目录。禁止已有 SKILL.md 就复用。

查询类可以带说明缺口的 `unresolved`。不完整写能力不得发布。

---

## 3. 运输层只做这些

代码执行动作、存证据、跑指定检查、原样打包。它不替 PI 认业务。

入口提示只留协调句：Investigator、目标、入口、自动控制、只有阻断才 assist、目标做完就交能力。不要把字段细则写进 JS。

用户主动停录时，才走「证据已冻结，按 Skill 1 对已完成行收口」。这是提前收口，不是默认前提。

`submit_recording_capability` 的下一项提示：继续调查或交下一项；台账齐了就 `submit_recording_result`。不要再写「等用户结束」。

禁止：

- 闸门加识别正则
- Infer 写「无独立来源按录制原值并已解决」
- `test-skill-contract` 锁 Skill 金句
- 导出按叶子名猜 now / 分页 / create_form
- `_inferred_system_values` 这个名字
- 单独交没有 `request_refs` 的 relations-only 能力

前端第 3 步吃最终提交。PI 自动定稿后，第 3 步自然有结果。不要为了边做边展示去改 PageRecorder。

---

## 4. 操作与信封（摘要）

Skill 2：双通道一直开着，不锁预览。合法 selector 只用 snapshot 当场广告的写法。`choose` 之后 `host_value` 常常是 `"on"`，以随后请求键为准。`fill_fields` 每项只写该项 `ref`。`assist` 只针对这一格，发出后停自动点，直到用户说继续。

Skill 3：一项能力恰好一个不共用的 `execute`。`request_refs` 是对象数组。`steps[].params` 是字段对象数组。不要写 `capabilities[].fields`。没认清来源就 `unresolved`，不要冻录制值。目标先 A 后 B 且没有值流时，`capability_relations` 挂在已有 execute 的能力上，不要单独交一项只有关系的信封。

Skill 4：只读已提交合同。写入仍 unresolved 或投影失败则不发布。消费者正文不要出现录制过程词和阿里专章。

---

## 5. 缺口改哪里

| 现象 | 只改 |
| --- | --- |
| 该产出却空转、不该发布却定稿、目标理解错、分诊错 | Skill 1 |
| 点不到、选不上、`host_value` 误判、该看图没看 | Skill 2 |
| 切错能力、来源错、绑错、冻录制值、单独交 relations | Skill 3 |
| 触发差、执行器与合同不一致、不该发布 | Skill 4 |
| 点了没反应、图送不进、证据丢失、投影工具补了键 | 才改代码 |

非常必要才改代码：动作打到错控件、图像/证据读丢、代码改写了信封、工具返回与现场不符、凭据泄漏、Skill 已写但运输物理上做不到。
