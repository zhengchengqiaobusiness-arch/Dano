# Pi_check

应用内浏览器 + PI 录制。PI 只加载四个 Skill，代码只运输。

## Skill

- `skill/BUSINESS_SKILL_INVESTIGATOR.md`：唯一入口
- `skill/CONTROL_IN_APP_BROWSER.md`：操作与取证
- `skill/INFER_BUSINESS_CONTRACT.md`：解释证据、写合同
- `skill/BUILD_AND_VALIDATE_DEDICATED_SKILL.md`：只在用户点击「产出 Skill」后按已交能力写调用手册，成品遵守 `doc/` 生成规范，不校验能力

缺一份不准开录。识别、切能力、来源只改 Skill 1–3。调用手册只改 Skill 4。录制会话不加载 Skill 4。

## 代码只做

执行动作、存证据、跑指定检查。出包运输开 Skill 4 会话、按能力注入冻结的 `client.py` / `wire_format.py` / `auth.local.json`、写入 `data/skill-catalog.json`。禁止引用 `back`。能力对不对只在录制合同里解决。交能力时信封不完整（schema 含系统键、`current_user` 无 source）当场拒收。冻结 runtime 按合同执行 preflight 与 links。

详见 `RESPONSIBILITIES.md`。
