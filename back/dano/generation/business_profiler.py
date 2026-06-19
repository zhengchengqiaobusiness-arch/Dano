"""业务剖析器(Phase 1):把一个业务下的**真实端点**,由 LLM 提炼成「完整流程的操作集」。

为什么:现在"一个业务 = 一条提交 flow"太单薄(只调一个动作就完)。真实业务像 lanxin 那样,
是**一组操作**:办理 + 查我的在途(避免重复)+ 查状态(确认真流转)+ 查待办 + 撤销/催办……

**泛化(关键)**:本模块**不绑定任何特定 OA/swagger**,不写死任何路径或业务。输入是"这个业务
有哪些真实存在的端点"(名/方法/路径/用途),LLM 据语义判断该有哪些操作,每个操作映射到
**本企业实际存在**的端点;某类操作没有对应端点就省略(尽力而为)。不同企业 → 不同操作集。

grounded:操作引用的端点必须在输入清单里,臆造的剔除;空操作剔除。失败/解析不了 → 返回空,
由上层回退"单提交"老路径(不阻断接入)。
"""

from __future__ import annotations

import json
import re

import structlog

log = structlog.get_logger(__name__)

_MAX_OPS = 8                       # 一个业务最多保留几个操作(防膨胀)


_PROMPT = """你在为「{business}」这个业务设计**完整流程的操作集**——像给人用的操作手册:
不只"提交一下",还要能查、能确认、能纠正。下面是本企业**真实存在**的相关接口(name | METHOD path | 用途)。

请据接口语义,提炼这个业务真正需要的操作。常见操作类型(**有对应接口才纳入,没有就省略,绝不臆造**):
- 办理/提交(写):完成这件事的**主操作**。**关键**:如果完成它需要串联多个接口
  (如 发起流程→存表单→提交,这是一条不可分割的链),就把这些接口**全部放进同一个操作**的 endpoints 里,
  **只产出一个办理操作,绝不把这条链拆成多个写操作**(单独的"存表单"无法独立工作)。
- 查我的相关/在途(读):办理前查是否有重复/未完成的单,避免重复办理
- 查状态/流转(读):办理后查这单走到哪一步、在谁那里
- 查我的待办(读):看是否轮到我处理
- 查已办/历史(读)
- 撤销/催办/重办(写):流程生命周期操作(各自独立)

注意:**查询类(读)各自独立成操作;但"办理"那条写链只能是一个操作**。

每个操作输出对象:
  "op": 英文蛇形动作名(如 submit_xxx / query_my_xxx / query_status / cancel_xxx)
  "write": true/false(是否写操作)
  "endpoints": [接口 name,可多个;**只能用下面列出的 name**]
  "purpose": 一句中文说明何时用

**只输出纯 JSON 数组**,不要解释、不要代码块。至少包含那个"办理/提交"主操作。

相关接口:
{lines}
"""


async def profile_business(business: str, actions: list[dict], *, spawn) -> list[dict]:  # noqa: ANN001
    """LLM 把业务的真实端点提炼成操作集 [{op, write, endpoints, purpose}]。

    grounded:endpoints 必须取自 actions.name;臆造/空的剔除。空清单或解析失败 → []。
    """
    if not actions:
        return []
    names = {a.get("name") for a in actions if a.get("name")}
    lines = "\n".join(f"{a.get('name')} | {(a.get('method') or 'GET').upper()} {a.get('endpoint')}"
                      f" | {(a.get('summary') or '')[:50]}" for a in actions)
    raw = await spawn(_PROMPT.format(business=business, lines=lines))
    items = _extract_json_array(raw)
    ops: list[dict] = []
    for it in items:
        if not isinstance(it, dict) or not it.get("op"):
            continue
        eps = [e for e in (it.get("endpoints") or []) if e in names]   # 只保留真实端点
        if not eps:                                                    # 没有可用端点 → 剔除(不臆造)
            continue
        ops.append({"op": str(it["op"]), "write": bool(it.get("write")),
                    "endpoints": eps, "purpose": str(it.get("purpose") or "")})
        if len(ops) >= _MAX_OPS:
            break
    log.info("business_profiler.done", business=business, ops=[o["op"] for o in ops], n_in=len(actions))
    return ops


def _extract_json_array(s: str) -> list:
    if not s:
        return []
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", s, re.DOTALL)
    body = fenced.group(1) if fenced else None
    if body is None:
        start, end = s.find("["), s.rfind("]")
        body = s[start:end + 1] if 0 <= start < end else None
    if not body:
        return []
    try:
        data = json.loads(body)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []
