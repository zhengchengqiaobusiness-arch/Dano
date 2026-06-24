"""LLM 识别「API 成功约定 + 框架风格」(取代 match_template 的关键词/结构硬匹配)。

为什么:原来靠硬规则认 RuoYi(路径含 /workflow/、schema 有 AjaxResult)并写死
success_rule=code==200。换一家 OA(非 RuoYi、或 code 字段叫别的)就失灵——"用代码认框架"
本就泛化不了。改让模型读**真实响应字段形状**,判这套 API 怎样表示"业务成功",产出
safe_eval 可求值的判定表达式。

grounded:只喂模型 spec 里**真实出现**的 2xx 响应顶层字段(如 code/msg/data、total/rows),
不臆造。产出的 success_rule 用与 planner 同一套校验(只准属性点取/下标/比较,禁函数调用/JS),
不合规则丢弃 → 调用方回退确定性 match_template。失败绝不阻断接入。
"""

from __future__ import annotations

import structlog

from dano.capabilities.doc_parser import _resolve_ref
from dano.generation.planner import EXPR_RULE_TEXT, _expr_problem
from dano.shared.prompt_utils import extract_json_obj

log = structlog.get_logger(__name__)

_MAX_SHAPES = 14          # 喂给模型的不同响应形状上限(去重后)


async def detect_convention(spec: dict, *, spawn) -> dict | None:  # noqa: ANN001
    """读真实响应字段 → LLM 判 {name, success_rule}。无可用信号/解析失败/规则不合规 → None。

    success_rule 经 safe_eval 校验(同 planner);不合规则置 None(name 仍可保留)。
    """
    shapes = _response_shapes(spec)
    if not shapes:
        return None                                          # 没有任何响应结构 → 无从判断,交回退
    lines = "\n".join(f"{s['endpoint']} -> {', '.join(s['props'])}" for s in shapes)
    raw = await spawn(_PROMPT + lines)
    data = extract_json_obj(raw)
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or "").strip() or None
    rule = str(data.get("success_rule") or "").strip() or None
    if rule and _expr_problem(rule, "success_rule"):         # 不能被 safe_eval 求值 → 丢弃规则
        log.warning("llm_convention.bad_rule", rule=rule)
        rule = None
    if not name and not rule:
        return None
    log.info("llm_convention.detected", name=name, success_rule=rule)
    return {"name": name, "success_rule": rule}


def _response_shapes(spec: dict, limit: int = _MAX_SHAPES) -> list[dict]:
    """收集去重后的 2xx 响应顶层字段集合(每项 {endpoint, props});供模型判成功约定。"""
    shapes: list[dict] = []
    seen: set[tuple] = set()
    for path, ops in (spec.get("paths") or {}).items():
        if not isinstance(ops, dict):
            continue
        for _method, op in ops.items():
            if not isinstance(op, dict):
                continue
            for code, r in (op.get("responses") or {}).items():
                if not str(code).startswith("2") or not isinstance(r, dict):
                    continue
                schema = (r.get("content", {}).get("application/json", {}).get("schema"))
                props = list((_resolve_ref(spec, schema or {}).get("properties") or {}).keys())
                key = tuple(sorted(props))
                if props and key not in seen:
                    seen.add(key)
                    shapes.append({"endpoint": path, "props": props})
                    if len(shapes) >= limit:
                        return shapes
    return shapes


_PROMPT = (
    "你在判断一个 HTTP API 的「业务成功约定」。下面是若干接口 2xx 响应的**顶层字段**\n"
    "(每行:endpoint -> 字段列表)。据此判断:这套 API 用什么字段、什么值表示「业务成功」?\n\n"
    "输出两项:\n"
    "- success_rule:" + EXPR_RULE_TEXT + "\n"
    "  例:统一返回 {code,msg,data} 的框架,业务成功常是 response.code == 200;\n"
    "      列表类接口可能无 code 字段,则放宽:response.code == null or response.code == 200。\n"
    "- name: 这套风格的简短英文标签(如 ruoyi-ajaxresult、rest-envelope、plain-rest);拿不准填 \"generic\"。\n\n"
    "只输出**纯 JSON 对象** {\"name\": \"...\", \"success_rule\": \"...\"},不要解释、不要代码块。\n\n"
    "响应字段:\n"
)
