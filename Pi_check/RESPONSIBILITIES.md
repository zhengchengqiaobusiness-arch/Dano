# 职责冻结

PI 加载且只加载四个 Skill。Investigator 是唯一入口。代码是冻住的运输层。

自动控制。只有登录、验证码、写不进、点了不发网这类阻断才请人。目标页做完且台账齐了就出包定稿，不要等用户说结束。

```text
用户目标 + 入口 URL
        ↓
PI = Business Skill Investigator
├── Control In App Browser
├── Infer Business Contract
└── Build and Validate Dedicated Skill
        ↓
代码：执行动作 / 存证据 / 跑指定检查 / 原样打包
```

| 文件 | 负责 | 禁止 |
| --- | --- | --- |
| `skill/BUSINESS_SKILL_INVESTIGATOR.md` | 目标、台账、下一步、分诊、目标做完即出包定稿 | 字段规则、selector、source_kind |
| `skill/CONTROL_IN_APP_BROWSER.md` | 点、填、选、快照、图像、同源前端 | 交能力、认来源 |
| `skill/INFER_BUSINESS_CONTRACT.md` | 切能力、来源、绑定、信封 | 点页面、写消费者包、冻录制值结案 |
| `skill/BUILD_AND_VALIDATE_DEDICATED_SKILL.md` | 写包、投影、隔离运行、能不能发布 | 回头猜页面 |
| `src/*` / 导出打包 | 动作、证据、形状闸门、指定检查、复制产物 | 认业务 |

缺口只改四份 Skill 之一。以后 diff 出现 `result-gate` / `visible-controls` / `renderer` / `computed.py` 认业务 = 方案作废。

## 什么叫非常必要才改代码

只允许：

- 动作执行错（点 A 打到 B；`choose` / `fill` 没按所请）
- 图像/证据存丢或读丢；图送不进模型
- 代码改写了 PI 信封
- 工具返回与现场不符
- 凭据泄漏
- 新工具类别是任务书要、现 Skill 调用却物理上做不到（加运输，不加识别）

不允许：换页认错、少字段、少分区、导出少业务理解、handbook 触发差。

| 现象 | 只改 |
| --- | --- |
| 目标理解错、该产出却空转、不该发布却定稿、分诊错 | Skill 1 |
| 点不到、选不上、该看图没看、没验证写上 | Skill 2 |
| 切错能力、来源错、绑错、冻录制值、灰框进调用方、假 links、错挂 preflight、分区行不全 | Skill 3 |
| 触发差、披露不对、执行器与合同不一致、不该发布 | Skill 4 |
| 点了没反应、图送不进、证据丢失、投影工具补了键 | 才改代码 |
