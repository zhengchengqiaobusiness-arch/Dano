"""LLM 修复循环 P0:确定性**执行器** + **检出器**。

设计铁律:LLM 只输出"受限词表"里的修复操作(remap/parameterize/link/drop/reorder/rename/...),
**由本模块确定性执行**,引用必须指向真实存在的 param/path/step,否则该操作被拒(不执行);执行后调用方
重跑 self_check 复验 —— **结构永远错不了**(LLM 改不坏)。检出器给确定性 findings(会话专属常量焊死、占位名)。
"""
from __future__ import annotations

import copy
import re

from dano.execution.page.request_capture import (
    _PATH_MISSING, _leaf_paths, _path_lookup, _set_by_path, _split_path, _tokens_to_str, self_check,
)

_SESSION_ID_RE = re.compile(r"^[A-Za-z]{2,}[-_]\d{4,}")        # SEQ-20260625-2F29 等"前缀+长数字段"码
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-")
_PLACEHOLDER_NAME_RE = re.compile(r"^(请输入|请选择|请填写|如\s|例如|placeholder)")

_FIX_OPS = {"drop_step", "reorder_steps", "set_success_rule", "parameterize",
            "link_step", "rename_param", "remap_field", "set_identity", "bind_placeholder"}


def looks_session_specific(value) -> bool:
    """像"一次性会话值"(任务ID/实例ID/时间戳/uuid/生成码)→ 绝不该当常量焊进 skill。
    稳健:只命中明显的一次性形态,放过 oa_leave 这类稳定业务常量。通用,不挑系统。"""
    s = str(value if value is not None else "").strip()
    if not s:
        return False
    if s.isdigit() and len(s) in (10, 13):                     # 10位秒 / 13位毫秒时间戳
        return True
    if _UUID_RE.match(s):
        return True
    if _SESSION_ID_RE.match(s) and re.search(r"\d{4,}", s):    # 前缀 + 含长数字段(日期/流水)
        return True
    return False


def looks_placeholder_name(name) -> bool:
    """像表单占位文字而非真字段名(请输入.../请选择.../如 X)。"""
    return bool(_PLACEHOLDER_NAME_RE.match(str(name or "").strip()))


def _is_placeholder(v) -> bool:
    return isinstance(v, str) and v.startswith("{{") and v.endswith("}}")


def _find_param_tokens(template, param):
    """在 body_template 里找 {{param}} 占位的 tokens 路径;无则 None。"""
    needle = "{{" + str(param) + "}}"
    for _p, toks, sv, _raw in _leaf_paths(template):
        if sv == needle:
            return toks
    return None


def collect_repair_findings(api_request: dict) -> list[dict]:
    """**确定性** findings(给修复器线索,可单测):self_check 违规 + 会话专属常量焊死 + 占位名参数。
    LLM 审核的语义 findings(字段错配/业务逻辑)在 P1 合并进来。"""
    out: list[dict] = []
    for v in self_check(api_request):
        out.append({"kind": "self_check", "detail": v})
    for si, tgt in enumerate(api_request.get("steps") or [api_request]):
        templ = tgt.get("body_template")
        if isinstance(templ, (dict, list)):
            # 系统时间戳(submitTime/createTime)已标 system_values、运行期填 now → 不是"焊死会话值",免报(否则白拦发布)
            sys_paths = {s.get("path") for s in (tgt.get("system_values") or [])}
            sys_toks = {tuple(s.get("tokens") or []) for s in (tgt.get("system_values") or [])}
            link_paths = {lk.get("target_path") for lk in (tgt.get("links") or []) if lk.get("target_path")}
            link_toks = {tuple(_split_path(p)) for p in link_paths}
            for p, toks, sv, raw in _leaf_paths(templ):
                if p in sys_paths or tuple(toks) in sys_toks or p in link_paths or tuple(toks) in link_toks:
                    continue
                if not _is_placeholder(sv) and looks_session_specific(raw):
                    out.append({"kind": "session_constant", "step": si, "path": toks, "value": sv,
                                "detail": f"常量 `{p}`={sv} 像一次性会话值,不该焊进 skill(应串联/参数化/删步)"})
        for pm in (tgt.get("params") or []):
            if looks_placeholder_name(pm):
                out.append({"kind": "placeholder_name", "step": si, "param": pm,
                            "detail": f"参数名 `{pm}` 是占位文字,需改成真业务名"})
    return out


def _fix_target(apir, step=None):
    """操作目标:工作流取指定步(默认最后一步=提交那步),单请求取自身。"""
    steps = apir.get("steps")
    if steps:
        i = step if isinstance(step, int) else len(steps) - 1
        return steps[i] if 0 <= i < len(steps) else None
    return apir


def apply_fix_ops(api_request: dict, ops: list[dict]) -> tuple[dict, list, list]:
    """**确定性**执行 LLM 出的修复操作(受限词表 _FIX_OPS);引用必须真实存在,否则该操作被拒(不执行)。
    返回 (新 api_request, applied, rejected)。调用方应在其后重跑 self_check 复验。"""
    apir = copy.deepcopy(api_request)
    applied, rejected = [], []
    for op in (ops or []):
        before = copy.deepcopy(apir)
        base = set(self_check(apir))                       # 改前结构基线
        ok, detail = _apply_fix_one(apir, op)
        if not ok:
            rejected.append({**op, "ok": False, "detail": detail})
            continue
        new_bad = set(self_check(apir)) - base             # 这步是否**引入新结构问题**
        if new_bad:                                        # 坏操作 → **逐 op 回滚**(执行后立刻 self_check)
            apir = before
            rejected.append({**op, "ok": False, "detail": "回滚(引入结构问题):" + "; ".join(list(new_bad)[:2])})
        else:
            applied.append({**op, "ok": True, "detail": detail})
    return apir, applied, rejected


def _apply_fix_one(apir, op) -> tuple[bool, str]:  # noqa: C901
    name = op.get("op")
    if name not in _FIX_OPS:
        return False, f"未知操作 {name}"
    steps = apir.get("steps")
    if name == "drop_step":
        if not steps:
            return False, "无 steps"
        i = op.get("step")
        if not (isinstance(i, int) and 0 <= i < len(steps)):
            return False, "step 越界"
        del steps[i]
        for st in steps:                                       # 调整/丢弃受影响的 link
            if st.get("links"):
                nl = []
                for lk in st["links"]:
                    ss = lk.get("source_step")
                    if ss == i:
                        continue
                    if isinstance(ss, int) and ss > i:
                        lk = {**lk, "source_step": ss - 1}
                    nl.append(lk)
                st["links"] = nl
        return True, "ok"
    if name == "reorder_steps":
        if not steps:
            return False, "无 steps"
        order = op.get("order")
        if not (isinstance(order, list) and sorted(order) == list(range(len(steps)))):
            return False, "order 非合法排列"
        old = list(steps)
        pos = {old_i: new_i for new_i, old_i in enumerate(order)}
        steps[:] = [old[k] for k in order]
        for st in steps:
            for lk in (st.get("links") or []):
                if isinstance(lk.get("source_step"), int):
                    lk["source_step"] = pos.get(lk["source_step"], lk["source_step"])
        return True, "ok"
    if name == "set_success_rule":
        tgt = _fix_target(apir, op.get("step"))
        if tgt is None:
            return False, "无目标步"
        tgt["success_rule"] = {"field": op.get("field"), "ok_values": list(op.get("ok_values") or [])}
        return True, "ok"
    if name == "parameterize":
        tgt = _fix_target(apir, op.get("step"))
        templ = (tgt or {}).get("body_template")
        toks, pname = op.get("path"), (op.get("param") or op.get("param_name"))
        if templ is None or not pname:
            return False, "缺 body_template/param"
        cur = _path_lookup(templ, toks)
        if cur is _PATH_MISSING:
            return False, "path 不存在"
        _set_by_path(templ, toks, "{{" + pname + "}}")
        if pname not in tgt.setdefault("params", []):
            tgt["params"].append(pname)
        tgt.setdefault("sample_inputs", {})[pname] = "" if cur is None else str(cur)
        return True, "ok"
    if name == "link_step":
        if not steps:
            return False, "无 steps(单请求不能串联)"
        ti, si = op.get("target_step"), op.get("source_step")
        if not (isinstance(ti, int) and 0 <= ti < len(steps)):
            return False, "target_step 越界"
        if not (isinstance(si, int) and 0 <= si < ti):
            return False, "source_step 须在 target 之前"
        tp = op.get("target_path")
        if _path_lookup(steps[ti].get("body_template"), tp) is _PATH_MISSING:
            return False, "target_path 不存在"
        sp = op.get("source_path")
        if not sp:                                         # source_path 必填,且要在来源步响应里真实存在
            return False, "缺 source_path"
        src_resp = steps[si].get("response_json")
        if src_resp is not None and _path_lookup(src_resp, sp) is _PATH_MISSING:
            return False, "source_path 在来源步响应里不存在(引用必须真实)"
        steps[ti].setdefault("links", []).append({
            "target_path": _tokens_to_str(tp) if isinstance(tp, list) else tp,
            "target_tokens": tp if isinstance(tp, list) else _split_path(tp),
            "source_step": si,
            "source_path": _tokens_to_str(sp) if isinstance(sp, list) else sp,
            "source_tokens": sp if isinstance(sp, list) else _split_path(sp)})
        return True, "ok"
    if name == "bind_placeholder":
        tgt = _fix_target(apir, op.get("step"))
        templ = (tgt or {}).get("body_template")
        param, tp = op.get("param"), op.get("target_path")
        if templ is None or not param:
            return False, "缺 body_template/param"
        if _path_lookup(templ, tp) is _PATH_MISSING:
            return False, "target_path 不存在"
        old = _find_param_tokens(templ, param)             # 把占位绑到 target_path;清掉它在别处的占位(避免一参填多处)
        _set_by_path(templ, tp, "{{" + param + "}}")
        tp_toks = tp if isinstance(tp, list) else _split_path(tp)
        if old is not None and list(old) != list(tp_toks):
            _set_by_path(templ, old, "")
        if param not in tgt.setdefault("params", []):
            tgt["params"].append(param)
        return True, "ok"
    if name == "rename_param":
        old, new = (op.get("old") or op.get("param")), op.get("new")
        if not old or not new:
            return False, "缺 old/new"
        hit = False
        for tgt in (steps or [apir]):
            templ = tgt.get("body_template")
            if isinstance(templ, (dict, list)):
                toks = _find_param_tokens(templ, old)
                if toks is not None:
                    _set_by_path(templ, toks, "{{" + new + "}}")
                    hit = True
            if old in (tgt.get("params") or []):
                tgt["params"] = [new if p == old else p for p in tgt["params"]]
                hit = True
            for k in ("sample_inputs", "field_types"):
                if tgt.get(k) and old in tgt[k]:
                    tgt[k][new] = tgt[k].pop(old)
        return (True, "ok") if hit else (False, f"参数 {old} 不存在")
    if name == "remap_field":
        tgt = _fix_target(apir, op.get("step"))
        templ = (tgt or {}).get("body_template")
        param, tp = op.get("param"), op.get("target_path")
        if templ is None:
            return False, "无 body_template"
        if param not in (tgt.get("params") or []):
            return False, f"param {param} 不存在"
        if _path_lookup(templ, tp) is _PATH_MISSING:
            return False, "target_path 不存在"
        old_toks = _find_param_tokens(templ, param)
        target_old = _path_lookup(templ, tp)
        _set_by_path(templ, tp, "{{" + param + "}}")
        tp_toks = tp if isinstance(tp, list) else _split_path(tp)
        if old_toks is not None and list(old_toks) != list(tp_toks):
            _set_by_path(templ, old_toks, target_old)          # 交换:旧位置放 target 的旧值(治字段错配/互换)
        return True, "ok"
    if name == "set_identity":
        tgt = _fix_target(apir, op.get("step"))
        templ = (tgt or {}).get("body_template")
        p = op.get("path")
        if templ is None or _path_lookup(templ, p) is _PATH_MISSING:
            return False, "path 不存在"
        toks = p if isinstance(p, list) else _split_path(p)
        tgt.setdefault("identity", []).append(
            {"path": _tokens_to_str(toks), "tokens": list(toks), "source": op.get("source", "")})
        return True, "ok"
    return False, "未处理"
