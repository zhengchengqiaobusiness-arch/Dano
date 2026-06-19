"""LLM 端点语义识别(取代关键词硬规则):对动作清单做 业务/基础设施 分类 + 业务分组。

为什么:不同企业接口命名千差万别,硬编码关键词表(login/captcha…)在新企业上必然漏判/误判
——"用代码识别 swagger 内容"本就泛化不了。改让模型读"动作名 + 用途 + 路径 + 标签"做语义判断。

grounded(关键):模型只对**已由代码确定性枚举出的清单**逐个分类,**不枚举、不臆造**接口——
完整性由代码保证(大 swagger 不丢接口),语义判断交给模型。分批并发,避免长清单超 token。
失败兜底:任一动作模型没给/给错 → 调用方对该动作回退确定性 endpoint_classifier,绝不阻断接入。
"""

from __future__ import annotations

import asyncio
import json
import re

import structlog

log = structlog.get_logger(__name__)

ROLES = {"infrastructure", "query", "business_action"}
_BATCH = 60                       # 每批动作数(并发分批,防长清单超 token)

_PROMPT = """你是 OA/接口语义分类器。下面是已抽好的接口清单(每行:name | METHOD path | 用途 | 标签)。
对清单里**每个 name** 判断两件事:
- role: 三选一——
    "infrastructure" = 登录/验证码/令牌/路由/获取用户信息等**管道**接口(不是用户业务能力);
    "query" = 只读查询/列表/详情/导出;
    "business_action" = 发起/提交/审批/撤销等**写**业务动作。
- category: 该接口所属**业务**的简短中文名(如 请假、报销、出差、用印、考勤);
    基础设施或无法归类填 ""。同一业务的接口要给**一致**的 category。

只对清单里出现的 name 分类,**不要新增或臆造**接口。
输出**纯 JSON 对象**:{"name": {"role": "...", "category": "..."}, ...},不要解释、不要代码块。

接口清单:
"""


async def classify_actions(actions: list, *, spawn) -> dict:  # noqa: ANN001
    """LLM 语义识别动作清单 → {name: {"role", "category"}}。分批并发;空清单返回空。

    返回的 map 可能**不含**某些动作(模型漏判/格式错时已剔除),调用方据此对缺失项回退确定性。
    """
    if not actions:
        return {}
    batches = [actions[i:i + _BATCH] for i in range(0, len(actions), _BATCH)]
    results = await asyncio.gather(*(_one_batch(b, spawn) for b in batches))
    merged: dict = {}
    for r in results:
        merged.update(r)
    log.info("llm_classify.done", actions=len(actions), classified=len(merged), batches=len(batches))
    return merged


async def _one_batch(batch: list, spawn) -> dict:  # noqa: ANN001
    names = {a.name for a in batch}
    raw = await spawn(_PROMPT + "\n".join(_line(a) for a in batch))
    data = _extract_json_obj(raw)
    out: dict = {}
    for name, v in (data.items() if isinstance(data, dict) else []):
        if name in names and isinstance(v, dict) and v.get("role") in ROLES:
            out[name] = {"role": v["role"], "category": str(v.get("category") or "")}
    return out


def _line(a) -> str:  # noqa: ANN001
    summary = (a.summary or "")[:60]
    return f"{a.name} | {a.method} {a.endpoint} | {summary} | {','.join(a.tags)}"


def _extract_json_obj(s: str) -> dict:
    """从模型输出抠出 JSON 对象(容忍 ```json 围栏 / 前后噪声)。失败返回 {}。"""
    if not s:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
    body = fenced.group(1) if fenced else None
    if body is None:
        start, end = s.find("{"), s.rfind("}")
        body = s[start:end + 1] if 0 <= start < end else None
    if not body:
        return {}
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}
