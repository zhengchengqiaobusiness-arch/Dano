# 提示词全景（PROMPTS）

Dano 里所有"喂给 LLM 的文字"集中说明:**在哪、谁的职责、配套什么代码校验、改的时候要同步什么**。
改任何 prompt 前先读本文,尤其是 §5「模型职责 vs 代码校验的边界」和 §6「改 prompt 前的检查清单」。

---

## 1. 两层提示词

| 层 | 位置 | 说明 |
|---|---|---|
| **A. pi 技能提示词** | `agent/skills/*.md` | 喂给 pi(Claude Agent SDK)的 system 级技能定义:接系统 / 接页面的闭环流程 + 红线。硬编码了工具名（改 `agent_tools/tools.py` 的 `TOOLS` 须同步）。|
| **B. Python 内嵌 LLM 调用** | `dano/**/*.py` 里的 `_PROMPT`/`_CONTRACT` 等 | 确定性代码包着的"语义判断"小调用（分类/拆解/抽取/评审/撰写）。每个都配了代码侧校验 + 失败回退。|

## 2. 核心原则（贯穿全部）

1. **Grounding**：模型只对代码确定性枚举出的清单做语义判断，**不枚举、不臆造**接口/字段/端点。
2. **代码校验兜底**：模型产出落库/采用前，必过确定性校验（结构、表达式、端点白名单、静态契约）。
3. **失败回退、绝不阻断**：LLM 失败/超时/格式错 → 回退确定性路径或下一轮重试，不崩接入。
4. `temperature=0`，输出尽量走 **JSON 模式**（见 §4.1）。

## 3. 提示词清单

| 位置 | 用途 | JSON 模式 | 配套校验 / 回退 |
|---|---|---|---|
| `agent/skills/onboard-system.md` | 接系统闭环 + 红线 | — | 后端重读校验（不自报通过）|
| `capabilities/llm_classifier.py:_PROMPT` | 端点分类(基础设施/查询/业务) | ✅ 对象 | `name in names` 过滤幻觉;缺项回退 `endpoint_classifier` |
| `capabilities/llm_template.py:_PROMPT` | 业务成功约定(success_rule) | ✅ 对象 | `_expr_problem` 校验;不合规丢规则,回退 `match_template` |
| `generation/planner.py:_CONTRACT` | 流程拆解 → 结构化 Plan | ✅ 对象 | `validate_plan`(端点/字段白名单 + 表达式);不合规重提示重拆 |
| `generation/business_profiler.py:_PROMPT` | 业务操作集提炼 | ✅ 对象包裹数组 | 端点取自 `actions.name`;臆造剔除 |
| `generation/oa_profile.py:_PROMPT` | OA 通用能力端点推断 | ✅ 对象包裹数组 | 只认 `_CAPABILITY_KINDS` 键 + 探针确认存在 |
| `generation/playbook_writer.py:_LLM_PROMPT` | 撰写六段式 SKILL.md | ❌（要 Markdown）| 事实校验:操作名/flag 须真实;失败回退确定性渲染 |
| `review/board.py:_ROLE_SYSTEM` | 三模型评审(验收/漏洞/合规) | ✅ 对象 | 发布闸门回 PG 重读;调用失败按不通过 |
| `verification/judge.py` | 执行审判 | — | **默认关闭**(见 §7) |
| `onboarding/ingest.py:_DOC_PROMPT` | 非结构化文档抽接口 | ✅ 对象包裹数组 | 抽出后逐项过滤;失败 → 空清单(不臆造) |

## 4. 基础设施（Phase 0–5 产出，改 prompt 前必懂）

### 4.1 JSON 模式 — `openai_text_spawn(..., json_mode=True)`
- 开 `response_format={"type":"json_object"}`，让模型更可能吐合法 JSON。
- **数组类输出必须包成对象**（`json_object` 模式要求顶层是对象）：用 `{"operations":[...]}` / `{"capabilities":[...]}` / `{"endpoints":[...]}`。抽取器对 key 名不敏感（取第一个 list 值）。
- 模型不支持 `response_format`（reasoner 类回 400/422）→ **自动去掉降级重试一次**，不阻断。
- 编码（要 `<ADAPTER>` 源码）和剧本（要 Markdown）**不开** JSON 模式。

### 4.2 共享工具 — `shared/prompt_utils.py`
- `extract_json_obj` / `extract_json_array`：对「裸值 / 对象包裹 / ```json 围栏 / 噪声」都健壮，失败返回空容器。**所有调用点统一用它**，别再各抄一份。
- `wrap_data(label, text)`：把不可信外部输入（接口文档原文、接口清单）包进 `<<<LABEL>>>…<<<END_LABEL>>>` 数据块（轻量 prompt-injection 防护）。已用于 `ingest`(DOC) 与 `llm_classifier`(ACTIONS)。
- `estimate_tokens(text)`：无依赖粗估（CJK≈1/字，其余 4 字符≈1）。字符切片会低估 CJK，故截断按它算。

### 4.3 表达式规则单一来源 — `planner.py:EXPR_RULE_TEXT`
判定表达式（success_rule / assert_expr）的规则文案**只此一处**。`planner._CONTRACT`、`llm_template._PROMPT` 都引用它；校验器 `_expr_problem` 与它同义。**改规则改这一个常量**，避免 prompt 与校验器漂移。

### 4.5 三模型评审硬化 — `review/board.py`
三审各给**逐项清单** + 要求每条理由**点名具体依据**；security/compliance 有**窄域 fail-closed**。同时**保留**反误判豁免（`verify=False`/`base_url`/`fact_check` GET 等"设计如此"项不算问题）——加固不得顶翻这套调优。只缓存"通过"结论（驳回不缓存，给重判新机会）。

### 4.6 证据按 token 预算截断 — `planner._compact_evidence`
旧 `[:16000]` 字符硬切 → 按 token 预算（`_EVIDENCE_TOKEN_BUDGET`）**在行边界**截断，优先级 **写端点 > 表单字段 > 读端点 > 样例返回**，保证流程必需的写端点幸存；截断 `log.warning` 记数 + 文本留标记，**不静默丢**。

## 5. 模型职责 vs 代码校验的边界

| 类别 | 谁负责 | 例子 |
|---|---|---|
| **语义判断** | 模型 | 端点分类、成功约定、流程拆解、业务操作集 |
| **机器强制**（确定性、普适硬错） | 代码校验 | 端点/字段白名单(`validate_plan`)、表达式合法性(`_expr_problem`) |
| **留给提示词 grounding**（因系统而异，硬判会误驳） | 模型 + 证据 | **标识是否拼进 URL 路径**——RESTful 系统 `/users/{id}` 是对的，故**不做静态硬判**,只在提示词里据"请求体示例"引导 |

> 第三类是关键设计:任何"换个企业/系统就可能不成立"的规则**都不能下沉成硬闸门**,否则会误驳正确能力方案。

## 6. 改 prompt 前的检查清单

1. **改判定表达式规则？** 只改 `EXPR_RULE_TEXT`，并确认 `_expr_problem` 仍同义。
2. **改数组类输出的 key 名？** 抽取器取第一个 list 值，key 名随意；但 prompt 里的示例 key 要和说明一致。
3. **改 business_profiler/planner 的 few-shot？** 必须仍通过自己的解析器/校验器——`test_phase51_fewshot.py` 守这条不变量。
4. **改评审 prompt？** 不要加全局"有疑即拒"（会顶翻反误判豁免);窄域 fail-closed + 保留"设计如此"豁免。
5. **新接一个 LLM 调用点？** 用 `partial(openai_text_spawn, tag=..., json_mode=True)`；输出数组就包成对象；抽取用 `prompt_utils`；不可信外部输入用 `wrap_data`。
7. **改技能 .md 里的工具名？** 同步 `agent_tools/tools.py` 的 `TOOLS` 注册名。

## 7. 维护备忘

- **judge 默认关**：`VerificationClosure(judge=None)` 默认不注入 `JudgeAgent`，运行期靠确定性断言 + 事实核查。`judge.py` 是"接好未启用"的可选层,不是死代码（启用方式见其 docstring）。
- **工具名硬编码**：两个技能 `.md` 文件用 HTML 注释标了同步要求。
- **测试安全网**：Phase 0–5 的回归在 `tests/test_phase50..54_*.py`；生成闭环 `_gates` 用注入式 `T` 测，不依赖真实 draft store。
