"""P2 · 操作发现:把 OA 层已确认的通用能力(OAProfile)实例化成**本业务**的操作。

分工:贵的"LLM 推断端点 + 探针确认"在 P0(build_oa_profile)做一次、全业务共享;本模块只做
**每业务的实例化**——把共享能力(查待办/已办/在途/草稿/查状态/撤销/催办)落成这个业务的操作,
并合进剖析器从文档实锤出的操作(doc_ops),去重。

泛化 & 不硬编码:能力清单来自 P0 探测结果(LLM+探针),不是写死路径;某业务没有写操作(纯查询)
就不挂"撤销/催办"。缺则跳、有则演,在操作粒度上成立。
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def _synth_action(cap, template_id: str = "") -> dict:  # noqa: ANN001
    """把一项 OA 能力造成一个"动作"字典(供 _expand_business_goals 建 GoalBrief)。"""
    return {
        "name": cap.name or f"cap_{cap.kind}",
        "method": cap.method,
        "endpoint": cap.endpoint,
        "summary": cap.purpose,
        "role": "query" if not cap.write else "business_action",
        "category": "",
        "required_in": [],
        "params_in": [],
        "params_out": [],
        "tags": ["oa_capability"],
        "field_docs": {},
        "business_meta": {},
    }


def complete_operations(doc_ops: list[dict], oa_profile, *, template_id: str = ""):  # noqa: ANN001
    """合并:文档实锤操作 doc_ops + OA 通用能力实例化操作 → (ops, 合成动作 name→dict)。

    - has_write:doc_ops 里有写操作(=能办理)才挂"撤销/催办"等写生命周期能力;纯查询业务不挂。
    - 去重:已被 doc_ops 端点覆盖的能力不重复加;同 kind 只留一个。
    - 返回 (ops, synth_actions):ops 是 [{op,write,endpoints,purpose}];synth_actions 是能力合成的动作。
    """
    ops = list(doc_ops or [])
    synth: dict[str, dict] = {}
    if oa_profile is None or not getattr(oa_profile, "capabilities", None):
        return ops, synth
    has_write = any(o.get("write") for o in ops)
    covered_eps = {e for o in ops for e in (o.get("endpoints") or [])}
    seen_kind = {o.get("op") for o in ops}
    for cap in oa_profile.capabilities:
        if cap.write and not has_write:                    # 没有办理的业务,不挂撤销/催办
            continue
        if cap.kind in seen_kind:                          # 同能力已有(doc 里就有)→ 跳
            continue
        act = _synth_action(cap, template_id)
        if act["name"] in covered_eps:                     # 该端点已被某操作覆盖 → 跳
            continue
        synth[act["name"]] = act
        ops.append({"op": cap.kind, "write": cap.write,
                    "endpoints": [act["name"]], "purpose": cap.purpose})
        seen_kind.add(cap.kind)
    log.info("operation_completer.done", added=[o["op"] for o in ops if o["op"] not in
             {d.get("op") for d in (doc_ops or [])}], total=len(ops))
    return ops, synth
