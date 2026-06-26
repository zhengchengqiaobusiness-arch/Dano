"""LLM 修复循环。

findings(确定性检出 + 三模型审核语义)→ propose(LLM 出受限修复操作)→ apply_fix_ops 确定性执行 →
重 self_check/findings,循环到**干净/收敛/限轮**。LLM 只出受限操作、由执行器安全应用 + self_check 复验
—— 不让 LLM 写坏结构。改不动(信息真缺)→ 上层问一个精准问题(非重录)。
"""
from __future__ import annotations

import json
import re

import structlog

log = structlog.get_logger(__name__)

# "dry/self_check 未真跑"——录制路径 by-design 的安全验证模式(零执行、零凭证、零副作用,发布为 partially_verified)。
# 评审若**仅因此**否决,是误判该安全模式;确定性剔除该理由(不阻断、不进修复:本就不该/不能改)。targeted,不误删真问题。
_DRY_MODE_RE = re.compile(
    r"dry\s*=?\s*true|self[_\s]?check|未真[实]?.{0,3}跑|真实跑通|未.{0,2}执行|未真发|仅构造未真发|construct.*not.*sen",
    re.I)


def is_dry_mode_reason(reason) -> bool:
    """该否决理由是否针对'dry/self_check 未真跑'这一按设计的安全模式(非可发布缺陷)。
    request_review 对 dry-only 资产据此剔除误判否决(确定性层承重,不让 LLM 抖动阻断 by-design 安全行为)。"""
    return bool(_DRY_MODE_RE.search(str(reason or "")))

_FIX_SYSTEM = (
    "你是录制型 API Skill 的**修复器**。给定业务目标 goal、请求骨架 skeleton(参数↔路径映射,**无值/无凭证**)、"
    "问题清单 findings,输出**修复操作**让 skill 真正符合 goal。**只能用下列操作,且引用必须是 skeleton 里真实存在的 "
    "param/path/step**(编造的引用会被执行器拒绝):\n"
    '- {"op":"remap_field","param":"X","target_path":[...]}:把参数 X 的占位移到正确字段(治字段错配/互换)\n'
    '- {"op":"rename_param","old":"X","new":"Y"}:占位文字名(请输入.../如X)改成真业务名\n'
    '- {"op":"parameterize","path":[...],"param":"X"}:把硬编码值变参数\n'
    '- {"op":"link_step","target_step":i,"target_path":[...],"source_step":j,"source_path":[...]}:一次性ID(taskId)改成步骤串联\n'
    '- {"op":"drop_step","step":i}:删不服务于 goal 的噪声步(聊天/改旧实体)\n'
    '- {"op":"reorder_steps","order":[...]}:按数据依赖排序\n'
    '- {"op":"set_success_rule","field":"code","ok_values":["0"]}:补成功判定\n'
    '- {"op":"set_identity","path":[...],"source":"localStorage:..."}:标当前用户/申请人字段\n'
    "findings 种类 → 建议操作:\n"
    "- session_constant(一次性值焊死)→ 有来源用 link_step,否则 parameterize;确属噪声步用 drop_step。\n"
    "- placeholder_name(请输入...)→ rename_param 成真业务名。\n"
    "- review_acceptance(业务逻辑不符)→ 按 goal 用 remap_field 修字段映射 / set_success_rule 补成败判定 / drop_step 删噪声步。\n"
    "- review_security / review_compliance(越权/合规)→ 多为 drop_step(删危险步)或 set_identity(标当前用户)。\n"
    "原则:**对照 goal 逐项修**(required_inputs 该映射到哪个字段、forbidden_actions 别碰、success_criteria 怎么判);"
    "**拿不准就别改**(尤其分不清的字段语义、无法识别的内部 ID)—— 留给程序问用户一个精准问题,**绝不瞎编引用或乱猜命名**。"
    "输出 JSON 对象:{\"ops\":[...]}(没把握就空数组)。"
)


def _request_skeleton(api_request: dict) -> dict:
    """给修复器看的"骨架":每步 param↔path 映射 + identity + method/path(**元数据,无 body 值/凭证**)。"""
    from dano.execution.page.request_capture import _leaf_paths

    def tx_ir(ir: dict) -> dict:
        if not isinstance(ir, dict):
            return {}
        return {
            "version": ir.get("version"),
            "inputs": [
                {"name": i.get("name"), "path": i.get("path"), "type": i.get("type"),
                 "submit_mode": i.get("submit_mode"), "source_id": i.get("source_id"),
                 "required": i.get("required"), "evidence": i.get("evidence")}
                for i in (ir.get("inputs") or [])
            ],
            "sources": [
                {"id": s.get("id"), "kind": s.get("kind"), "has_url": bool(s.get("url")),
                 "value_key": s.get("value_key"), "label_key": s.get("label_key"),
                 "count": s.get("count"), "evidence": s.get("evidence")}
                for s in (ir.get("sources") or [])
            ],
            "bindings": [
                {"input": b.get("input"), "target_path": b.get("target_path"),
                 "mode": b.get("mode"), "source_id": b.get("source_id"),
                 "target_key": b.get("target_key"), "expand_fields": b.get("expand_fields")}
                for b in (ir.get("bindings") or [])
            ],
            "derived": [
                {"kind": d.get("kind"), "source_path": d.get("source_path"),
                 "target_path": d.get("target_path"), "param": d.get("param"),
                 "style": d.get("style")}
                for d in (ir.get("derived") or [])
            ],
            "success": ir.get("success") or {},
            "capture": {"capture_hash": (ir.get("capture") or {}).get("capture_hash"),
                        "trace_hash": (ir.get("capture") or {}).get("trace_hash"),
                        "write_event": (ir.get("capture") or {}).get("write_event")},
        }

    def one(req: dict) -> dict:
        templ = req.get("body_template")
        placements = []
        if isinstance(templ, (dict, list)):
            for _p, toks, sv, _raw in _leaf_paths(templ):
                if isinstance(sv, str) and sv.startswith("{{") and sv.endswith("}}"):
                    placements.append({"path": toks, "param": sv[2:-2]})
        return {"method": req.get("method"), "path": req.get("path"), "params": req.get("params"),
                "placements": placements, "identity": [i.get("path") for i in (req.get("identity") or [])]}

    steps = api_request.get("steps")
    skeleton = {"steps": [one(s) for s in steps]} if steps else one(api_request)
    skeleton["transaction_ir"] = tx_ir(api_request.get("transaction_ir") or {})
    return skeleton


def review_findings(verdicts) -> list[dict]:
    """三模型审核 verdicts → findings(只取**未通过**的,带角色 + reason)。verdicts 可为 dict 或对象。"""
    out: list[dict] = []
    for v in (verdicts or []):
        passed = v.get("passed") if isinstance(v, dict) else getattr(v, "passed", True)
        role = v.get("role") if isinstance(v, dict) else getattr(v, "role", "")
        reasons = (v.get("reasons") if isinstance(v, dict) else getattr(v, "reasons", [])) or ["未通过"]
        if not passed:
            out += [{"kind": f"review_{role}", "detail": r} for r in reasons]
    return out


async def generate_fix_ops(client, model, *, goal, api_request, findings) -> list[dict]:
    """LLM 修复器:findings + goal + 骨架 → 受限修复操作清单。只喂元数据;未配置/失败/无 findings → []。"""
    if client is None or not model or not findings:
        return []
    payload = json.dumps({"goal": goal or {}, "skeleton": _request_skeleton(api_request),
                          "findings": findings}, ensure_ascii=False)
    try:
        out = await client.complete_json(model=model, system=_FIX_SYSTEM,
                                         user="【修复输入】\n" + payload, timeout_s=45.0)
    except Exception:  # noqa: BLE001 —— 修复失败不阻断(退回当前产物 + 让上层问)
        log.warning("generate_fix_ops.failed")
        return []
    ops = out.get("ops") if isinstance(out, dict) else None
    return [o for o in ops if isinstance(o, dict) and o.get("op")] if isinstance(ops, list) else []


async def run_repair_loop(api_request, propose, *, goal=None, seed_findings=None, max_rounds: int = 3):
    """返回 (repaired_api_request, rounds, history, remaining_findings)。

    propose(api_request, findings, goal) -> list[op](async)。seed_findings(三模型语义 findings)只并入首轮
    给修复器看(后续轮以确定性 findings 判收敛,不重复跑审核)。收敛:findings 清零→成功;没减少→停;限轮→停。
    """
    from dano.execution.page.repair_ops import apply_fix_ops, collect_repair_findings
    apir = api_request
    history: list[dict] = []
    prev = None
    for r in range(max_rounds):
        findings = collect_repair_findings(apir)
        if r == 0 and seed_findings:
            findings = findings + list(seed_findings)        # 首轮把审核语义 findings 也喂给修复器
        if not findings:
            log.info("repair.converged", round=r)
            return apir, r, history, []
        if prev is not None and len(findings) >= prev:        # 没改少 → 不收敛,停(交上层问)
            log.warning("repair.stalled", round=r, findings=len(findings))
            return apir, r, history, findings
        prev = len(findings)
        ops = await propose(apir, findings, goal) or []
        if not ops:
            log.warning("repair.no_ops", round=r, findings=len(findings))
            return apir, r, history, findings
        apir, applied, rejected = apply_fix_ops(apir, ops)
        log.info("repair.round", round=r, findings=len(findings),
                 applied=len(applied), rejected=len(rejected))
        history.append({"round": r, "findings": len(findings), "applied": applied, "rejected": rejected})
    return apir, max_rounds, history, collect_repair_findings(apir)
