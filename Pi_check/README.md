# Pi_check

应用内浏览器 + PI 录制。PI 只加载四个 Skill，代码只运输。

## Skill

- `skill/BUSINESS_SKILL_INVESTIGATOR.md`：唯一入口
- `skill/CONTROL_IN_APP_BROWSER.md`：操作与取证
- `skill/INFER_BUSINESS_CONTRACT.md`：解释证据、写合同
- `skill/BUILD_AND_VALIDATE_DEDICATED_SKILL.md`：只在用户点击「产出 Skill」后写专用包并验证

缺一份不准开录。识别、切能力、来源、handbook、能不能发布：只改对应 Skill。录制会话不加载 Skill 4。

## 代码只做

执行动作、存证据、跑指定检查。出包运输开 Skill 4 会话、注入冻结的 `client.py` / `wire_format.py` / `auth.local.json`、校验本目录形状、写入 `data/skill-catalog.json`。禁止引用 `back`。写入仍 unresolved 时拒绝发布可执行写能力。

详见 `RESPONSIBILITIES.md`。
