"""P3 · 业务剧本规格 PlaybookSpec:把"过程逻辑"组装成机器表示。

把一个业务的:操作集(办理+查询+生命周期)、业务规则(x-flow:校验/审批链/驳回/记账)、
通用错误处置、事后确认、恢复依赖,**纯映射**成一个结构(无任何业务专属分支)。
缺哪块就空哪块(没有 x-flow→无校验/审批段)。供 P5(动态撰写)渲染成 lanxin 深度的 SKILL.md。

数据全部来自证据(manifest 的 business_meta + 参数),不臆造。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 客户端可自检的校验规则(其余如预算/查重需服务端数据,归到"错误处置"靠服务端驳回)
_CLIENT_CHECKABLE = {"positive", "required", "nonempty", "range", "min", "max", "length", "regex"}
# 哪些操作 kind 是"生命周期写"(恢复段),及其依赖的前置标识
_RECOVERY_NEEDS = {"cancel": ("procInsId", "query_in_progress"),
                   "urge": ("taskId", "query_my_todo")}


@dataclass
class Operation:
    op: str
    title: str
    write: bool
    fields: list[dict] = field(default_factory=list)   # [{name, label, required}]
    purpose: str = ""


@dataclass
class PlaybookSpec:
    business: str
    label: str
    subsystem: str
    operations: list[Operation] = field(default_factory=list)
    do: Operation | None = None                        # 办理(主写操作)
    preflight: list[str] = field(default_factory=list)
    preconditions: list[dict] = field(default_factory=list)   # {desc, client_checkable}
    invariants: list[dict] = field(default_factory=list)      # DSL IR 业务不变量 {check, message}(⑤ 渲染)
    errors: list[dict] = field(default_factory=list)          # {when, meaning, action}
    post_check: dict = field(default_factory=dict)            # {stages, ledger, verify}
    recovery: list[dict] = field(default_factory=list)        # {op, needs, prefetch}
    approval_chain: list = field(default_factory=list)
    goal: dict = field(default_factory=dict)                  # 结构化目标(意图/成功判据/禁止步)
    field_mappings: list = field(default_factory=list)        # 可追溯字段映射(§16)

    @property
    def has_write(self) -> bool:
        return any(o.write for o in self.operations)


def _op_fields(manifest) -> list[dict]:  # noqa: ANN001
    props = (getattr(manifest, "parameters", {}) or {}).get("properties", {}) or {}
    required = set((getattr(manifest, "parameters", {}) or {}).get("required", []) or [])
    return [{"name": k, "label": (props[k] or {}).get("description", "") or k,
             "required": k in required} for k in props]


def _label(business: str, manifests: list) -> str:  # noqa: ANN001
    writes = [m for m in manifests if getattr(m, "requires_confirmation", False)]
    for m in writes:
        if getattr(m, "title", ""):
            return m.title
    bm = _business_meta(manifests)                          # x-flow 常带中文流程名(name)→ 比英文 flow 名好
    if bm.get("name"):
        return str(bm["name"])
    s = re.sub(r"^(submit|create|apply|demo|do)[_-]+", "", (business or "").lower())
    return s.replace("_", " ").strip() or business


def _business_meta(manifests: list) -> dict:  # noqa: ANN001
    for m in manifests:
        bm = getattr(m, "business_meta", {}) or {}
        if bm:
            return bm
    return {}


def _first_attr(manifests: list, attr: str):  # noqa: ANN001, ANN201
    """取写操作(办理)manifest 上的 attr(goal/field_mappings 挂在复合流程上),退而取任一非空。"""
    writes = [m for m in manifests if getattr(m, "requires_confirmation", False)]
    for m in writes + list(manifests):
        v = getattr(m, attr, None)
        if v:
            return v
    return None


def build_playbook(subsystem: str, business: str, manifests: list, *,  # noqa: ANN001
                   template_id: str = "", ir_preconditions: list[dict] | None = None,
                   ir_invariants: list[dict] | None = None) -> PlaybookSpec:
    """从该业务的 manifest 列表 + x-flow 业务规则 + **DSL IR 的前置/不变量**,组装 PlaybookSpec。纯映射,缺则空。

    ir_preconditions/ir_invariants:来自声明式 WORKFLOW IR(单一事实源),并入 ②前置 / ⑤事后,
    使剧本反映**真实业务逻辑**(余额校验/天数核对…),而非只靠 x-flow 注解。
    """
    ops = [Operation(op=getattr(m, "action", ""), title=getattr(m, "title", "") or getattr(m, "action", ""),
                     write=bool(getattr(m, "requires_confirmation", False)),
                     fields=_op_fields(m),
                     purpose=getattr(m, "description", "") or "")
           for m in manifests]
    do = next((o for o in ops if o.write), None)
    bm = _business_meta(manifests)
    template_id = template_id or str(bm.get("templateId") or "")    # x-flow 里常带 templateId
    spec = PlaybookSpec(business=business, label=_label(business, manifests),
                        subsystem=subsystem, operations=ops, do=do,
                        goal=_first_attr(manifests, "goal") or {},
                        field_mappings=_first_attr(manifests, "field_mappings") or [])

    # ① preflight(能不能走这条路):Dano 运行时的真实自检项
    spec.preflight = ["网关可达 + 本租户 X-Tenant-Key 已配(scripts/diagnose 自检)",
                      "OA 调用凭证有效(失效会 401,去运行配置换 token)"]
    if template_id:
        spec.preflight.append(f"流程模板 {template_id} 存在、表单字段可探")

    # ② 前置校验 ← x-flow.businessValidations(可本地自检的标 client)
    for v in (bm.get("businessValidations") or []):
        if not isinstance(v, dict):
            continue
        rule = str(v.get("rule") or "")
        spec.preconditions.append({
            "desc": v.get("desc") or rule,
            "client_checkable": rule.lower() in _CLIENT_CHECKABLE,
        })

    # ⑤ 事后确认 ← approvalChain(走到哪)+ autoAccounting(记账)+ 通用回查
    spec.approval_chain = bm.get("approvalChain") or []
    stages = []
    for s in spec.approval_chain:
        if isinstance(s, dict):
            stages.append(str(s.get("step") or s.get("assignee") or s.get("by") or ""))
    acct = bm.get("autoAccounting") or {}
    spec.post_check = {
        "verify": "办理后回查在途/历史,确认返回的 procInsId 真出现在列表(勿因接口 200 就报成功)",
        "stages": [s for s in stages if s],
        "ledger": (f"末位审批通过后自动记账:{acct.get('financeLedgerType')}" if acct else ""),
        "escalation": bm.get("escalation") or {},
    }

    # ④ 错误处置:通用框架错误 + x-flow.rejectBehavior(业务驳回语义)
    spec.errors = [
        {"when": "error_kind=auth / HTTP 401", "meaning": "OA token 失效",
         "action": "去运行配置换 token 后重试"},
        {"when": "code≠200", "meaning": "提交未成功", "action": "把 msg 告知用户,按提示改正重提"},
        {"when": "接口 200 但事实核查未过", "meaning": "疑似空操作(false 200)",
         "action": "勿报成功,把原始返回给用户"},
    ]
    if bm.get("rejectBehavior"):
        spec.errors.insert(1, {"when": "error_kind=rejected", "meaning": "系统驳回:业务校验没过",
                               "action": str(bm["rejectBehavior"])})

    # ⑥ 恢复:生命周期写操作 + 其前置依赖
    present = {o.op for o in ops}
    for o in ops:
        kind = o.op
        if kind in _RECOVERY_NEEDS:
            needs, prefetch = _RECOVERY_NEEDS[kind]
            spec.recovery.append({
                "op": kind, "needs": needs,
                "prefetch": prefetch if prefetch in present else "",
            })

    # DSL IR 单一事实源:前置不变量并入 ②;业务不变量进 ⑤(reflect 真实业务逻辑)
    for inv in (ir_preconditions or []):
        spec.preconditions.append(
            {"desc": inv.get("message") or inv.get("check") or "", "client_checkable": False})
    spec.invariants = [{"check": i.get("check"), "message": i.get("message") or i.get("check")}
                       for i in (ir_invariants or [])]
    return spec
