# 职责冻结

主录制 PI 只加载 Skill 1–3。Investigator 是唯一入口。监控是并行只读会话，只加载 MONITOR overlay。出包会话只加载 Skill 4。代码是冻住的运输层。

自动控制。只有登录、验证码、写不进、点了不发网这类阻断才请人。目标页做完且台账齐了就交出能力并停止。出包只在用户点击「产出 Skill」后另开 Skill 4 会话完成。

```text
用户目标 + 入口 URL
        ↓
    主 PI = Business Skill Investigator（录制会话，只加载 Skill 1–3）
├── Control In App Browser
├── Infer Business Contract
        ↑ steer 本场 overlay
    监控 PI（MONITOR_PI_CONTEXT_WRITER，换页只读续写 context-skill.md）
        ↓
submit_recording_result 交出能力并停
        ↓
用户点击「产出 Skill」
        ↓
Skill 4 出包会话（只加载 BUILD_AND_VALIDATE_DEDICATED_SKILL，只读 read_context_skill）
        ↓
代码：按录制合同物化整包、注入冻结 runtime/flow/client/auth、本目录保真校验、写入 Skills 目录

Skills 页「导出为 pi 文件式 skill」另走快速原样导出：不开 Skill 4、不做校验；沿用已有 Skill 4 的 SKILL.md，按已有录制合同重写合同/脚本并写入当前 token。
```

| 文件 | 负责 | 禁止 |
| --- | --- | --- |
| `skill/BUSINESS_SKILL_INVESTIGATOR.md` | 目标、台账、下一步、分诊、目标做完即交完整能力并停 | 字段规则、selector、source_kind、写消费者包 |
| `skill/CONTROL_IN_APP_BROWSER.md` | 点、填、选、快照、图像、同源前端 | 交能力、认来源 |
| `skill/INFER_BUSINESS_CONTRACT.md` | 切能力、来源、绑定、信封 | 点页面、写消费者包、冻录制值结案 |
| `skill/BUILD_AND_VALIDATE_DEDICATED_SKILL.md` | 读 doc/、核对手册触发、投影、能不能提交出包 | 回头猜页面、重写执行器、录制期自动出包 |
| `skill/MONITOR_PI_CONTEXT_WRITER.md` | 每页只读 overlay：怎么点、能力怎么切、Skill 怎么写 | click、open_page、交能力、写消费者 SKILL.md |
| `src/*` / 导出运输 | 录制页开 Skill 4 会话并保真校验；目录页沿用已有 Skill 4 手册快速写包（不开 Skill 4、不校验）；冻结 runtime/flow/client/auth；目录同步 | 认业务、按页面猜字段、用运输层手册顶替 Skill 4、引用 back |

缺口：录制/出包改四份 Skill 之一；本场 overlay 只改监控 Skill。以后 diff 出现 `result-gate` / `visible-controls` / `renderer` / `computed.py` 认业务 = 方案作废。

## 什么叫非常必要才改代码

只允许：

- 动作执行错（点 A 打到 B；`choose` / `fill` 没按所请）
- 图像/证据存丢或读丢；图送不进模型
- 代码改写了 PI 信封（omit `unresolved` 清空、同 id 追加 links/relations、`buildRoutes` 后写覆盖丢掉能力）
- 展示契约只检查页面能读到的信封是否自洽（每个 `exposed_to_user` 都在 `input_schema`、对象数组有 `items.properties`、禁自指 links），不认业务、不补字段
- 工具返回与现场不符（监控 snapshot / `network_since` 必须读真实 inspect 与证据）
- 监控运输：换页触发、累积写入 `context-skill.md`、steer 主 PI、出包只读；不交能力、不认业务
- 凭据泄漏
- 新工具类别是任务书要、现 Skill 调用却物理上做不到（加运输，不加识别）

不允许：换页认错、少字段、少分区、导出少业务理解、handbook 触发差。

| 现象 | 只改 |
| --- | --- |
| 目标理解错、该产出却空转、不该交能力却提交、分诊错 | Skill 1 |
| 点不到、选不上、该看图没看、没验证写上 | Skill 2 |
| 切错能力、来源错、绑错、冻录制值、灰框进调用方、假 links、错挂 preflight、分区行不全、可改控件收成系统、系统栏填不出、option_source 挂成另一能力 execute、默认链跳过详情、execute 键被丢掉、schema 与系统 params 打架 | Skill 3 |
| 触发差、披露不对、执行器与合同不一致、不该发布、手册跳过 default.steps、假装 runtime 会填无合同值的系统栏 | Skill 4 |
| 点了没反应、图送不进、证据丢失、投影工具补了键 | 才改代码 |
