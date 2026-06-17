# 使用说明:swagger + token → 自动生成代码 Skill → 调用

本文档对应业务流转图的 6 步:**给 swagger 和 token,系统自主写代码实现业务流程、过四道关、发布、上目录、可被调用。**

---

## 1. 需要你提供什么

| 项 | 说明 | 必填 |
|---|---|---|
| **OA token** | 目标系统的 Bearer token(环境变量 `DANO_OA_TOKEN`,**只经 env,不写文件**) | ✅ |
| **OA base_url** | 目标系统 API 根,如 `https://host:8443/prod-api`(`DANO_OA_BASE_URL`) | ✅ |
| **DeepSeek key** | pi 写代码 + 三模型审核(`DANO_PI_API_KEY`,只经 env) | ✅ |
| **PostgreSQL** | 资产/草案/证据/追溯落库(默认 `localhost:5432/dano_back`,`DANO_PG_DSN`) | ✅ |
| **swagger** | 目标系统的 OpenAPI(读流程自动接;写/复合流程需声明) | 读流程✅ |
| **写流程测试输入** | 写操作沙箱需有效入参(如请假的 `templateId` + 字段值),通过 `flows` 声明 | 写流程✅ |

> ⚠ **三模型审核需 ≥3 个不同模型的端点**。若你的 key 只暴露 2 个模型(如本测试端点只有 `deepseek-v4-flash`/`deepseek-v4-pro`),`distinct=3` 硬闸门无法满足,需 `DANO_REVIEW_ENABLED=false` 临时降级(**沙箱 + 静态漏扫 + 事实核查照常**)。

---

## 2. 怎么操作

### 方式 A:命令行一把梭(演示生成 + 调用)

```bash
export DANO_OA_TOKEN="<OA Bearer token>"
export DANO_PI_API_KEY="<DeepSeek key>"
export DANO_OA_BASE_URL="https://host:8443/prod-api"
export DANO_PI_MODEL="deepseek-v4-flash"   # 按你的端点
export DANO_REVIEW_ENABLED="false"          # 仅当端点不足 3 个模型时
python examples/run_full_demo.py
```
脚本会:① 用 goal 循环生成请假 adapter（pi 写代码→隔离跑→事实核查→发布）；② 打印生成的代码存在哪；③ 真实调用该 Skill 再创建一条请假并事实核查。

### 方式 B:HTTP(生产用法)

**① 预览选类别**(超大 swagger 先圈范围,可选):
```
POST /onboarding/preview      { "openapi": {...} }
→ { "categories": [{tag,count}...], "business_action_count": N }
```

**② 接入生成**(主路径 = 代码自动生成):
```jsonc
POST /onboarding
{
  "tenant": "acme", "subsystem": "A-OA",
  "openapi": {...},
  "deploy": { "base_url": "https://host:8443/prod-api", "auth": { "kind": "token" } },
  "credentials": { "token": "<OA token>" },
  "include_tags": ["请假"],                          // 圈类别(可选)
  "flows": [                                          // 写/复合流程:必须给 test_input
    { "flow": "submit_leave", "actions": ["submit_flow_task"],
      "test_input": { "templateId": "leave_template",
                      "values": { "title": "张三年假", "leaveType": "annual",
                                  "leaveDays": 1, "reason": "回家" } } }
  ]
  // use_codegen 默认 true;读流程(GET)会自动逐个生成
}
→ { "status": "completed", "published_skills": ["submit_leave", ...] }
```

**③ 调用**:见第 5 节。

---

## 3. 六步分别在做什么(对照代码)

| 步 | 做什么 | 关键代码 |
|---|---|---|
| ① 导入 | swagger+token+deploy → 进程内 materials(凭证不进 LLM) | `agent_tools/materials.py` |
| ② 选类别 | 按 tag 圈定业务动作 | `gateway` `/onboarding/preview`,`tools.parse_spec` |
| ③ 选策略 | 按业务挑生成策略 | `generation/strategies/select_strategy` |
| ④ goal 循环 | 拆解→定方案→**pi 编码**→测试(隔离跑+事实核查)→漏洞扫描→三模型审核;任一驳回带原因回灌重写 | `generation/controller.GenerationLoop` + `coder.PiCoder` |
| ⑤ 发布闸门 | `verify_publishable`(sandbox+vuln)+ `verify_reviewed`(distinct=3)回 PG 重读,不信自报 | `assets/drafts.DraftStore` |
| ⑥ 上目录 | 已发布 adapter → 目录可选可调 | `orchestrator/skills.SkillRegistry` + `catalog/manifest` |

---

## 4. 生成的代码 / Skill 存在哪

**都在 PostgreSQL,按租户隔离,不在仓库里。**

- **生成的代码**:`assets` 表,`asset_type='adapter'`、`validation_status='published'`,源码在 **`body.source`**(`run(inputs, creds)` 函数),按 `(tenant, subsystem, asset_key, version)` 版本化。
- **Skill 本身** = 这条已发布 adapter 资产;对外目录由 `SkillRegistry` 从它派生,`integration='adapter'`。
- **过程证据/追溯**:`asset_drafts`(草案)、`validation_runs`(sandbox/vuln 证据)、`review_runs`(三模型结论)、`generation_runs`(每次生成的逐轮迭代:谁被哪关以什么理由驳回)。

查看(SQL):
```sql
-- 看某租户已发布的代码 Skill 及其源码
SELECT asset_key, version, body->>'source'
FROM assets
WHERE tenant='acme' AND asset_type='adapter' AND validation_status='published';

-- 看一次生成的逐轮迭代(审计自动写的代码)
SELECT flow, ok, rejections, iterations FROM generation_runs WHERE tenant='acme';
```

---

## 5. 怎么调用

Skill id = `{subsystem}.{action}`,如 `A-OA.submit_leave`。

### HTTP(前端用)
```
POST /v1/skills/A-OA.submit_leave/invoke
Header: X-Tenant-Key: <租户 key>
Body:   { "input": { "templateId": "leave_template",
                      "values": { "title": "李四事假", "leaveType": "personal",
                                  "leaveDays": 2, "reason": "私事" } },
          "confirm": true }     // 写操作 L3 需 confirm=true
→ { "state": "completed", "exec_result": { "structured_output": { ... } }, ... }
```
- `input` 只给**业务字段**;`__base_url__`(系统根)和凭证由后端运行期注入,**前端不传**。
- 后端流程:制度+风险闸门(L3 确认)→ 隔离 runner 执行已发布源码(凭证注入)→ 成败规则 → **事实核查回查**确认真生效 → `completed` / `failed`。

### 代码直调(脚本/服务内)
```python
from dano.orchestrator.orchestrator import Orchestrator
out = await orch.invoke_skill(Subsystem.OA, "submit_leave",
        {"templateId": "leave_template", "values": {...}}, tenant="acme", confirm=True)
# out.state == TaskState.COMPLETED;out.exec_result.structured_output 为返回体
```
凭证经 `Orchestrator(resolve_credentials=...)` 注入(返回 `{"token": <OA token>}`);base_url 取自已发布的环境画像资产。

---

## 6. 注意

- **写操作真的会在目标系统建数据**(请假/报销等)。演示用测试账号。
- 写流程**必须给 `test_input`**(没有效入参没法沙箱)——这是当前未自动推断的部分。
- 事实核查端点按系统而定;`workflow_bpmn` 用 `listProcess?procInstId` 确认"已提交进审批",接新系统时按真实回查口径校准。
- 入口脚本:`examples/run_full_demo.py`(生成+调用)、`examples/run_codegen_leave.py`(仅生成)、`examples/verify_real_leave.py`(仅验证契约)。
