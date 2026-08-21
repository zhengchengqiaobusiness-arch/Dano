# 阶段八 Skill 优化方案（发给 codex 实施）

> 目的：让 Dano 录制品导出的 Skill 真正成为「Agent 手册 + 路由 + 组合 + 渐进披露」四件套，且对齐 aliyun alibabacloud-aiops-skills、skills.sh / mattpocock、微信文章的写法纪律，保留 Dano `doc/` 与 `recording-pipeline.md` 的硬性要求。

参考性级别（从高到低）：

1. **aliyun alibabacloud-aiops-skills**（级别最高，决定章节骨架与节奏）
2. **skills.sh / mattpocock writing-great-skills**（决定 description / router / 渐进披露纪律）
3. **微信公众号原文**（抓取失败，保留其「调用组合 + 示例 + 反例」三条主张）
4. **Dano `doc/skill-generator-ask-user-question-guide.md`**（ask_user_question 工具纪律，硬性）
6. **`recording-pipeline.md` 阶段八**（CAPABILITIES.md / OPTIONS.md 命名纪律，硬性）
7. **CLAUDE.md / skillfrontend**（前端能力边界）

---

## 1. 真实参考样例（已克隆到 `/tmp/aliyun-skills`）

样例 1：`skills/aiml/agentloop/alibabacloud-agentloop-evaluation/SKILL.md`（22 KB）
样例 2：`skills/aiml/agentloop/alibabacloud-agentloop-dataset/SKILL.md`
样例 3：`skills/aiml/docmind/alibabacloud-docmind-parse/SKILL.md`

aliyun 风格的章节骨架（按出现顺序）：

| 章节 | 作用 | 关键纪律 |
|---|---|---|
| `# <name>` | H1 标题，与 frontmatter `name` 一致 | 一行命名 |
| `## Scenario Description` | 业务背景 + 架构 + 默认行为 | 「架构」行写明依赖版本；说明默认 dry-run / mutate 需要 `--execute` |
| `## Installation` | CLI / 插件 / 依赖安装与版本 | `[MUST]` 标注前置检查；不要 `curl \| bash` |
| `## Script Dependencies` | 脚本运行环境 | 写明 Python 3.8+ stdlib 或具体依赖 |
| `## Environment Variables` | 运行期变量表 | 必填 / 选填 / 用途 / 默认值 |
| `## Authentication` | 凭证获取 + 安全规则 | 禁止 echo AK/SK；要求运行期 profile |
| `## RAM Policy` | 权限清单 | 失败时跳到权限诊断 skill |
| `## Parameter Confirmation` | 参数确认表（参数名 / 必填 / 描述 / 默认值） | **不假设默认值** |
| `## Mutation Confirmation Protocol` | 三步：Preview → Confirm → Execute | 写明只读操作无需确认 |
| `## Observability` | session-id + `--user-agent` 注入 | 每条 CLI 命令强制带 |
| `## Core Workflow` | **核心工作流**（**含组合调用**） | **子节 `### Select the workflow`（意图分流）+ `### Run the workflow`（步骤）+ 子用例** |
| `## Success Verification Method` | 验证命令清单 | 链到 `references/verification-method.md` |
| `## Cleanup` | 终止 / 删除 / 取消操作 | 强制要求用户授权 |
| `## Best Practices` | 经验法则列表 | 每条 1-2 行，可执行 |
| `## Refresh compatibility` | 插件版本差异时的诊断命令 | 给 `xxx --help` 自检命令 |
| `## Reference Links` | references 索引 | 仅列引用关系，不重复内容 |

写法规律（对比 Dano 现状）：

- **每个 H2 章节都有明确职责**，不堆砌文字。
- **「组合调用」写在 `## Core Workflow` 的 `### Select the workflow` 子节**，每个分支一条人话 + 一段示例命令。
- **`## Examples` 用代码块给真实可跑命令**（不是叙述）。
- **`## Common Mistakes` 单独列出反例**（Dano 当前没有）。
- **`## Best Practices` 是单行可执行清单**（不是大段叙述）。
- **`## Reference Links` 是 references 索引**，避免在主文档重复正文。

---

## 2. skills.sh / mattpocock 的纪律（必须遵守）

抓取到的核心论点（[原文](https://www.skills.sh/mattpocock/skills/writing-great-skills)）：

1. **description 的两个职责**：「skill 是什么」+「应当触发的分支」。每个词都加重 context load，要比正文更克制。
2. **三种调用形态**：model-invoked / user-invoked / router skill。路由层是 user-invoked 的元索引。
3. **根原则**：skill 是「从随机系统挤出确定性」的工具，可预测性高于一切。
4. **router skill**：当用户能调用的 skill 多到记不住，引入一个 user-invoked 的总路由。

Dano 应落地：

- `description` 写两行：第一行「业务是什么」，第二行「该触发的分支 + 拒止分支」。**禁止在 description 里塞完整业务叙述**（参考 aliyun 风格，description 是 30–80 词的紧凑说明）。
- **导出层不强制 router skill**（单包场景无意义），但在 `export/` 根目录允许一个 `router.md`（后续可加），列出各 page-operations 的触发条件。
- **frontmatter 加 `disable-model-invocation` 字段**（model-invoked 默认），显式声明本 skill 是「Agent 可自主触发」。

---

## 3. 微信公众号原文的可用主张（抓取失败，按通用文章主张落地）

微信文章虽未抓到正文，但同类 SKILL.md 写作类公众号文章常见三条主张：

1. **要写「调用组合 + 真实示例」**，不能只写能力清单。
2. **要写「反例 / 常见错误」**，告诉 Agent 什么不能做。
3. **要写「成功判定的具体标准」**，不是抽象描述。

Dano 当前 SKILL.md 有 `## 失败处理` 与 `## 完成标准`，但缺少：

- **`## Common Mistakes` 或 `## 反例`**：当前 `## 失败处理` 是「失败时怎么办」，缺「绝不能这么做」。
- **`## Examples` 真命令示例**：当前 `## 工具` 是能力清单，不是组合示例。

---

## 4. Dano 硬性要求（必须保留）

### 4.1 `doc/skill-generator-ask-user-question-guide.md`（已存在，56 KB）

纪律要点：

- 每次响应最多一次 `ask_user_question`。
- 多字段合并到 `questions[]` 分组表单。
- 写操作整理完参数后另起一次 `confirm:true` 调用。
- `default` 必须是运行期生成的非空推荐值，不允许占位符 / 历史样本。
- `id` 与 `input_schema.properties` 键逐字一致。

### 4.2 `recording-pipeline.md` 阶段八（行 380–395）

明确列出产物：`SKILL.md / CONTRACT.json / CAPABILITIES / INPUT_FORMS / OPTIONS` + 能力脚本 + 验证脚本。

当前 Dano 导出**缺失 `CAPABILITIES.md` 和 `OPTIONS.md`** — 信息散在 `SKILL.md` 和 `OPERATIONS.md`。本次优化必须补齐。

### 4.3 CLAUDE.md / skillfrontend

- 不发明接口；HTTP API 来自录制，不来自 agent。
- 前端 PageRecorder 与导出 Skill 解耦（Skill 不调前端）。
- 鉴权仅来自运行期 `DANO_AUTH_HEADERS`。

---

## 5. 当前导出产物的差距清单

文件：`E:\python\try\Dano\export\dano-admin-dianshixinxi-com-90-erp-372468ecf111-package\`

| 维度 | 当前状态 | 目标状态 | 差距 |
|---|---|---|---|
| `## Scenario Description`（业务背景 + 架构） | ❌ 无 | 一段业务叙述 + 「架构」行（Python 3 + httpx + 鉴权来源） | 缺章节 |
| `## Environment Variables` | ⚠️ 散在 frontmatter `compatibility` | 单独章节 + 表 | 缺章节 |
| `## Authentication` | ⚠️ 仅写在 `compatibility` 行 | 独立章节，禁止 echo 凭证 | 缺章节 |
| `## Parameter Confirmation` | ⚠️ 由 INPUT_FORMS.md 承担（Agent 视角） | SKILL.md 给「参数确认总表」，覆盖写操作必填 / 默认 / 拒止 | 缺章节 |
| `## Mutation Confirmation Protocol`（三步确认） | ⚠️ 「写前确认」散在路由表「写前确认」列 | 独立章节，写明 Preview → Confirm → Execute 与只读豁免 | 缺章节 |
| `## Observability`（会话注入） | ❌ 无 | session-id 注入约定 | 缺章节（Dano 无外部审计可省，但保留 stub） |
| `## Core Workflow` | ⚠️ 「操作步骤」是 5 步硬规则 | 拆成 `### Select the workflow`（意图分流）+ `### Run the workflow`（步骤） | 章节名 + 子节结构 |
| `### 组合路线（必须按这条走）` | ❌ 无 | 显式列出「先 search → 再 edit/approve/unapprove/delete」「先 view → 再 edit」 | **核心空白** |
| `### 自动带入的字段` | ❌ 无 | 列出 edit-sale-order 从 view-sale-order-detail 拿的字段 | **核心空白** |
| `## Examples` 真命令示例 | ⚠️ 「操作步骤」给抽象规则 | 给 5–8 条可跑 bash 命令（含组合示例） | 内容替换 |
| `## Common Mistakes` | ❌ 无 | 列 5–8 条反例 | 缺章节 |
| `## Best Practices` | ⚠️ 「安全边界」承担 | 单行可执行清单（按 aliyun 风格） | 章节名 + 形式 |
| `## Success Verification Method` | ⚠️ 「完成标准」承担 | 单独章节，链到 references 验证脚本清单 | 章节名 + 形式 |
| `## Cleanup` | ❌ 无 | 列出删除 / 反审批的手动步骤 | 缺章节 |
| `## Refresh compatibility` | ❌ 无 | 鉴权 / 路由变更时的自检命令 | 缺章节 |
| `## Reference Links` | ⚠️ 「资源」承担 | 单独章节，明列 references / scripts / 验证脚本 | 章节名 |
| `CAPABILITIES.md` | ❌ 无 | 每能力的 name / intent / kind / 安全 / 依赖 元数据 | **阶段八规范缺失** |
| `OPTIONS.md` | ❌ 无 | 字段候选的运行时来源映射 | **阶段八规范缺失** |
| OPERATIONS.md 后端术语泄露 | ⚠️ `unverified write read-back`、`Business hard rules` | 改写为业务语言 | 需清理 |
| description 长度 | ⚠️ 130+ 词 | 30–80 词（紧凑），业务叙述移到「业务说明」 | 长度控制 |
| frontmatter `disable-model-invocation` | ❌ 无 | 加 `disable-model-invocation: false`（默认 model-invoked） | 缺字段 |
| references/INPUT_FORMS.md | ✅ 合 doc 指南 | 保持 | 达标 |

---

## 6. 目标 SKILL.md 骨架（aliyun 风格重写）

```
---
name: sale-order-operations
description: "30–80 词：销售订单业务；触发的 5 类意图分支；拒止的 3 类场景。"
disable-model-invocation: false
version: "1.0.0"
compatibility: 需要 Python 3.8+ 与 httpx；鉴权只来自运行期 DANO_AUTH_HEADERS 或本地会话缓存。
metadata:
  domain: recorded-business
  category: page-operation
  risk: high
  combinations: ["search→approve", "search→unapprove", "search→edit", "search→delete", "view→edit"]
allowed-tools: Bash Read
---

# 销售订单

## 业务说明
（业务叙述，1 段）

## 适用场景
（触发分支列表）

## 不适用场景
（拒止分支列表）

## 架构与依赖
（Python 3.8+ / httpx / 鉴权来源；不发明接口）

## 环境变量
| 变量 | 必填 | 用途 |
|---|---|---|

## 鉴权与安全
（运行期 profile；禁止 echo 凭证）

## 参数确认
| 参数 | 必填 | 描述 | 默认值 |
|---|---|---|---|

## 写操作确认协议
（Preview → Confirm → Execute；只读豁免）

## 操作路由
（表格：意图 → 操作 / 脚本 / 必填 / 写前确认 / 写后验证）

## 能力关系
（组合路线的自然语言叙事 + 显式「组合路线」列表 + 「自动带入的字段」）

### 组合路线
- 用户没说哪一条但要改/审/反审/删：search-sale-orders → 拿到 id → 走对应写操作
- 用户说「编辑」但只给业务名：view-sale-order-detail → 拿 orderTime 等字段 → edit-sale-order

### 自动带入的字段
- search-sale-orders 的 `id` → approve-sale-order / unapprove-sale-order / edit-sale-order / delete-sale-order 的 `id`
- view-sale-order-detail 的 `data.orderTime` → edit-sale-order 的 `orderTime`（同：`discountPercent` / `depositPrice` / `remark`）

## 操作步骤
### 选工作流（意图分流）
### 跑工作流（5 步硬规则）

## 工具
（每能力脚本一行 + 验证脚本）

## 示例
（5–8 条可跑 bash 命令，含组合示例）

## 反例（Common Mistakes）
（5–8 条「绝不能这么做」）

## 输出
（查询 / 写入 / 组合的输出格式）

## 完成标准 / 成功验证
（链到 verify 脚本清单）

## 失败处理
（5 类失败的处理方式）

## 清理
（删除 / 反审批的手动步骤；强制授权）

## 最佳实践
（单行可执行清单）

## 刷新兼容
（鉴权 / 路由变更时的自检命令）

## 安全边界
（不输出凭证；不跳确认；不发明字段）

## 引用
（references / scripts 索引）
```

---

## 7. CAPABILITIES.md 目标内容

每个能力一段：

```markdown
# <能力名>
- name: <操作 id>
- intent: <用户意图人话>
- kind: read | write | write-with-verify
- safety: <风险等级 + 必填确认>
- depends_on: <上游能力 id 列表>
- script: scripts/<x>.py
- verify: scripts/verify_<x>.py  # 可选
- input_schema_ref: CONTRACT.json#/<x>/input_schema
```

---

## 8. OPTIONS.md 目标内容

每个字段一段：

```markdown
## <字段名>
- 来源: 静态 | 运行时 API
- 端点: <URL>（运行时 API 时填）
- resultPath: <json path>
- idField / labelField: <字段名>
- 候选项: list[{id, label}]  # 静态时填，运行时仅写规则
```

---

## 9. 实施步骤（按顺序）

1. **扩展 renderer.py**：补齐所有缺失章节的渲染函数（参考 `_composition_section` 已有的模式）。
   - `_scenario_section`、`_architecture_section`、`_env_vars_section`、`_auth_section`、`_parameter_confirmation_section`、`_mutation_protocol_section`、`_observability_section`、`_core_workflow_section`（含 `### Select the workflow` 与 `### Run the workflow` 子节）、`_examples_section`、`_common_mistakes_section`、`_cleanup_section`、`_refresh_compat_section`、`_reference_links_section`。
   - 在 `_composition_section` 补 `### 组合路线` 与 `### 自动带入的字段` 子节（即使当前 plan 没有 routes，也用 `spec.capability_relations` 渲染）。
   - 渲染 `CAPABILITIES.md` 与 `OPTIONS.md` 两个文件。

2. **修改 OPERATIONS.md 渲染器**：去掉 `unverified write read-back` 与 `Business hard rules`，改写为业务语言。

3. **缩短 description**：从 130 词压到 30–80 词，业务叙述移到 `## 业务说明` 小节。

4. **加 frontmatter 字段**：`disable-model-invocation: false`、`metadata.combinations`（从 spec 读）。

5. **更新 `renderer.py` 内的「章节固定顺序」**为 aliyun 风格（详见第 6 节）。

6. **写单元测试**：渲染后跑 `validate_skill_documents`，断言新章节存在、`CAPABILITIES.md` / `OPTIONS.md` 存在、description 长度在 30–80。

7. **对已发布包手工回灌**：重跑导出，对 `dano-admin-dianshixinxi-com-90-erp-372468ecf111-package` 重新生成。

---

## 10. 验收清单（codex 完成后逐项打勾）

- [ ] `description` 词数 30–80
- [ ] frontmatter 含 `disable-model-invocation` 与 `metadata.combinations`
- [ ] `SKILL.md` 章节顺序与第 6 节一致
- [ ] `## 能力关系` 含 `### 组合路线` 与 `### 自动带入的字段` 两个子节
- [ ] `## 示例` 含 5–8 条可跑 bash 命令，至少 2 条组合示例
- [ ] `## 反例` 含 5–8 条
- [ ] `CAPABILITIES.md` 与 `OPTIONS.md` 存在
- [ ] `OPERATIONS.md` 不含 `unverified write read-back` / `Business hard rules` / 后端模块名
- [ ] `validate_skill_package` 通过
- [ ] 单测通过