"""P5 · 动态撰写剧本:把 PlaybookSpec 写成六段式 SKILL.md(对标 lanxin 深度)。

不是"固定模板填空":SKILL.md 的内容(操作清单/前置校验/错误表/事后确认/恢复)**全部来自本业务的
PlaybookSpec**(每业务不同)。两条路径:
- render_playbook_md:确定性渲染(grounded、可靠、可测、永不臆造)——内容随业务变,是动态的。
- write_playbook_md:LLM 据 PlaybookSpec 撰写自然措辞(production 主路径),**只引 spec 内事实**;
  失败/空/丢了操作 → 自动回退确定性渲染。
"""

from __future__ import annotations

import json
import re
from functools import partial

import structlog

from dano.generation.playbook import Operation, PlaybookSpec

log = structlog.get_logger(__name__)

_GLOBAL_FLAGS = {"confirm", "diagnose", "json", "select", "preview", "help"}


def validate_playbook_facts(md: str, *, actions: set[str], fields: set[str]) -> list[str]:
    """事实校验(导出 ⑩):LLM 写的剧本里提到的**操作脚本名 / 参数 flag 必须真实存在**,不准发明。

    - `scripts/<X>.(sh|py|ps1)` 的 X 必须 ∈ actions ∪ {diagnose}
    - `--<flag>` 必须 ∈ fields ∪ 全局 flag(confirm/diagnose/json/select/preview)
    返回问题清单(空=通过)。纯函数,可离线测;有问题则回退确定性渲染(grounded)。
    """
    issues: list[str] = []
    allowed_actions = {a for a in actions} | {"diagnose", "submit", "dano_call"}
    for name in sorted(set(re.findall(r"scripts/([A-Za-z0-9_]+)\.(?:sh|py|ps1)", md))):
        if name not in allowed_actions:
            issues.append(f"剧本引用了不存在的操作脚本: scripts/{name}")
    allowed_flags = {f.lower() for f in fields} | _GLOBAL_FLAGS
    for fl in sorted(set(re.findall(r"(?<!\w)--([A-Za-z][A-Za-z0-9_-]*)", md))):
        if fl.lower() not in allowed_flags:
            issues.append(f"剧本引用了不存在的参数: --{fl}")
    return issues


def _op_flags(op: Operation) -> str:
    return " ".join(f"--{f['name']} <{f['name']}>" for f in op.fields) or ""


def _op_table(spec: PlaybookSpec) -> str:
    reads = [o for o in spec.operations if not o.write]
    writes = [o for o in spec.operations if o.write]
    rows = "\n".join(
        f"| `{o.op}` | `scripts/{o.op}.sh` | {'写·需确认' if o.write else '读'} | {o.title} "
        f"| {_op_flags(o) or '(无)'} |" for o in (reads + writes))
    return "| 操作 | 脚本 | 类型 | 说明 | 参数 |\n|---|---|---|---|---|\n" + rows


def render_playbook_md(spec: PlaybookSpec, slug: str) -> str:
    """确定性渲染六段式剧本。内容来自 spec(每业务不同),缺的段自动省略。"""
    label = spec.label or spec.business
    n = len(spec.operations)
    desc = (f"办理与查询「{label}」的完整业务剧本(共 {n} 个操作)。当用户要办理「{label}」或查询其"
            f"在途/待办/状态、撤销或催办时,**务必使用本 skill**,即使没说出 skill 名或接口名。")
    out = [f"""---
name: {slug}
description: {desc}
compatibility: 需 python3 + 能访问 Dano 网关;每个操作经 Dano 执行真实动作(写操作经确认 + 事实核查)
metadata:
  source: dano:{spec.subsystem} business:{spec.business}
  operations: {n}
---

# {label} · 业务剧本

这是 Dano **一个业务的完整操作剧本**(像操作手册,不只调一个接口)。真正的执行(适配器→目标系统
+ 三模型闸门 + 事实核查)都在 Dano 侧;本端按剧本收集参数、按需调对应操作脚本。

## 操作清单
{_op_table(spec)}

> 每个操作:`bash scripts/<操作>.sh <逐字段 flags>`(写操作加 `--confirm`);自检 `bash scripts/diagnose.sh`。
> `__base_url__`、流程模板、申请人身份(登录凭证)、调用凭证由 Dano 运行期注入,**不需要也不应**由你提供。"""]

    # 目标(Goal:要达成什么 + 成功判据 + 红线)
    g = spec.goal or {}
    if g.get("intent") or g.get("success_criteria"):
        gl = []
        if g.get("intent"):
            gl.append(f"**目标**:{g['intent']}")
        if g.get("success_criteria"):
            gl.append("**成功判据**(全满足才算 succeeded,否则勿报成功):\n"
                      + "\n".join(f"- {s}" for s in g["success_criteria"]))
        if g.get("forbidden_steps"):
            gl.append("**红线**:本业务只提交本人申请,**禁止**编入删除/审批他人/驳回/终止/越权类动作。")
        out.append("## 目标(Goal)\n" + "\n".join(gl))

    # ① 能不能走这条路
    if spec.preflight:
        items = "\n".join(f"- {p}" for p in spec.preflight)
        out.append(f"## ① 能不能走这条路(先自检)\n先 `bash scripts/diagnose.sh`,确认:\n{items}")

    # ② 办理前:前置条件 + 审核
    if spec.has_write:
        pre = ["- **查在途/待办**:先查有没有未完成的同类单,**避免重复办理**(用清单里的查询操作)。"]
        for c in spec.preconditions:
            tag = "(可自动校验)" if c.get("client_checkable") else "(服务端把关,违反会被驳回)"
            pre.append(f"- 审核:{c['desc']} {tag}")
        if spec.do and spec.do.fields:
            req = [f["name"] for f in spec.do.fields if f.get("required")]
            pre.append(f"- 必填字段:{', '.join(req) or '(无)'} —— 缺哪个补哪个。")
        out.append("## ② 办理前(前置条件 + 审核)\n" + "\n".join(pre))

    # ③ 办理
    if spec.do:
        out.append(f"## ③ 办理(写,需确认)\n先向用户**复述将提交的内容并取得同意**,再带 `--confirm`:\n"
                   f"```bash\nbash scripts/{spec.do.op}.sh {_op_flags(spec.do)} --confirm\n```")

    # 字段映射(可追溯,§16):业务字段 → 目标点路径 + 来源 schema(审计用,提交由 Dano 完成)
    if spec.field_mappings:
        rows = "\n".join(
            f"| `{m.get('standard_field')}` | `{m.get('target_location')}` | {m.get('target_type') or ''} "
            f"| `{(m.get('source') or {}).get('schema_ref', '')}` |" for m in spec.field_mappings)
        out.append("## 字段映射(可追溯)\n字段去向有据可查(来自接口 schema,非凭名猜):\n"
                   "| 业务字段 | 目标点路径 | 类型 | 来源 |\n|---|---|---|---|\n" + rows)

    # ④ 错误处置
    if spec.errors:
        rows = "\n".join(f"| {e['when']} | {e['meaning']} | {e['action']} |" for e in spec.errors)
        out.append("## ④ 错误了怎么办(返回 → 动作)\n| 返回 | 含义 | 你该做 |\n|---|---|---|\n" + rows)

    # ⑤ 事后确认(x-flow 审批链/记账 + DSL IR 业务不变量)
    pc = spec.post_check or {}
    lines = []
    if pc:
        if pc.get("verify"):
            lines.append(f"- {pc.get('verify')}")
        if pc.get("stages"):
            lines.append("- 审批链(看走到哪一步):" + " → ".join(pc["stages"]))
        esc = pc.get("escalation") or {}
        if esc.get("when"):
            lines.append(f"- 升级规则:{esc.get('when')} 时增加「{esc.get('addApprover', '')}」审批")
        if pc.get("ledger"):
            lines.append(f"- {pc['ledger']}")
    for inv in spec.invariants:
        lines.append(f"- 核对(业务不变量):{inv.get('message') or inv.get('check')}")
    if lines:
        out.append("## ⑤ 办理后(确认真生效)\n" + "\n".join(lines))

    # ⑥ 缺失 / 恢复
    if spec.recovery:
        lines = []
        for r in spec.recovery:
            pf = f"先用 `{r['prefetch']}` 拿到 {r['needs']}" if r.get("prefetch") else f"需先有 {r['needs']}"
            lines.append(f"- `{r['op']}`:{pf},再执行。")
        out.append("## ⑥ 缺失 / 恢复\n" + "\n".join(lines))

    # 输出契约 + 环境
    out.append("""## 输出契约(每个脚本末行 JSON)
| status | 含义 | 你应做的 |
|---|---|---|
| `succeeded` | 真实执行且(写操作)事实核查通过 | 告知结果,附 `output` 里的单号 / procInsId / 列表 |
| `need_select` | 复合流程消歧:多个候选待选 | 把 `candidates` 给用户选,再用 `--json` 带上选中项的 `bind` 值重跑 |
| `need_confirm` | 写操作未确认被拦 | 向用户确认后,**带 `--confirm` 重跑** |
| `failed` | 失败(见 `reason` / `error_kind`) | 按错误表处置,**勿谎报成功** |

## 运行前置(环境变量,部署方配置,勿写进文件)
- `DANO_URL`:Dano 网关地址,如 `http://localhost:8077`
- `DANO_TENANT_KEY`:本租户 api_key(作 `X-Tenant-Key`)""")
    return "\n\n".join(out) + "\n"


_LLM_PROMPT = """你在为一个业务写一本 agent 用的操作剧本(SKILL.md,中文)。下面是该业务的**结构化规格 JSON**(PlaybookSpec)。
按规格写一本 Markdown 剧本,**严格只用规格里出现的事实**(操作名/字段/校验/错误/审批链/恢复),**不得臆造**任何
端点、字段、规则。保留 frontmatter(name/description/metadata),并包含这些小节(规格里为空的小节就省略):
操作清单(表) / 目标(Goal:intent 意图 + success_criteria 成功判据 + 红线 forbidden) / ① 能不能走(自检) /
② 办理前(前置+审核) / ③ 办理(需确认) / 字段映射(field_mappings:业务字段→目标点路径+来源,可追溯) /
④ 错误处置(表) / ⑤ 办理后确认 / ⑥ 缺失恢复 / 输出契约 / 运行前置。措辞自然、像操作手册。**只输出 Markdown 正文**,不要解释。

name(frontmatter 用):{slug}

规格 JSON:
{spec_json}
"""


def _spec_to_dict(spec: PlaybookSpec) -> dict:
    return {
        "business": spec.business, "label": spec.label, "subsystem": spec.subsystem,
        "operations": [{"op": o.op, "title": o.title, "write": o.write,
                        "fields": o.fields, "purpose": o.purpose} for o in spec.operations],
        "preflight": spec.preflight, "preconditions": spec.preconditions,
        "errors": spec.errors, "post_check": spec.post_check, "recovery": spec.recovery,
        "goal": spec.goal, "field_mappings": spec.field_mappings,
    }


async def write_playbook_md(spec: PlaybookSpec, slug: str, *, spawn=None) -> str:  # noqa: ANN001
    """主路径:LLM 据 PlaybookSpec 动态撰写剧本(grounded);失败/空/漏操作 → 回退确定性渲染。"""
    fallback = render_playbook_md(spec, slug)
    if spawn is None:
        from dano.generation.coder import openai_text_spawn
        spawn = partial(openai_text_spawn, tag="playbook")
    try:
        text = await spawn(_LLM_PROMPT.format(slug=slug, spec_json=json.dumps(
            _spec_to_dict(spec), ensure_ascii=False)))
    except Exception as e:  # noqa: BLE001
        log.warning("playbook_writer.llm_failed", business=spec.business, error=str(e))
        return fallback
    text = (text or "").strip()
    if text.startswith("```"):                              # 去掉可能的 ```markdown 围栏
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    # grounding 守门:非空 + 带 frontmatter + 不丢操作 + **事实校验(不准发明脚本/参数)**;任一不过 → 确定性版
    missing = [o.op for o in spec.operations if o.op not in text]
    actions = {o.op for o in spec.operations}
    fields = {f["name"] for o in spec.operations for f in o.fields}
    fact_issues = validate_playbook_facts(text, actions=actions, fields=fields)
    if len(text) < 120 or "name:" not in text[:200] or missing or fact_issues:
        log.warning("playbook_writer.fallback", business=spec.business,
                    reason=("missing_ops:%s" % missing if missing
                            else ("fact_issues:%s" % fact_issues if fact_issues
                                  else "too_short_or_no_frontmatter")))
        return fallback
    log.info("playbook_writer.llm_ok", business=spec.business, chars=len(text))
    return text
