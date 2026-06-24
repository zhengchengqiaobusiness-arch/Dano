"""提示词公共工具:LLM 输出的健壮 JSON 抽取 + 不可信外部数据的分隔包装。

为什么集中:原来 5 个模块各自抄了一份 `_extract_json_*`(只会括号扫描),开启 provider 的
JSON 模式后,返回可能是**对象包裹的数组**(如 {"items":[...]}),旧抽取器在含噪声时会误判。
这里提供一份对「裸数组 / 对象包裹数组 / ```json 围栏 / 前后噪声」都健壮的抽取,任何企业/任何
模型(OpenAI 兼容、reasoner 类)产出都吃得下;失败一律返回空容器,绝不抛、绝不臆造。

与"是否开 JSON 模式"解耦:JSON 模式只是让模型更可能吐合法 JSON,这里仍兜底容错。
"""

from __future__ import annotations

import json
import re

_FENCE_ARR = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)
_FENCE_OBJ = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _loads_or_none(s: str | None):
    if not s:
        return None
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return None


def extract_json_array(s: str) -> list:
    """从模型输出里抠出 JSON 数组。

    依次尝试:① ```json [ ... ] ``` 围栏;② 整段是对象(JSON 模式或 {"items":[...]} 包裹)→
    取其中第一个 list 值;③ 整段就是裸数组;④ 噪声里的 [ … ] 括号扫描。全失败 → []。
    """
    if not s:
        return []
    fenced = _FENCE_ARR.search(s)
    if fenced:
        arr = _loads_or_none(fenced.group(1))
        if isinstance(arr, list):
            return arr
    whole = _loads_or_none(s.strip())
    if isinstance(whole, list):
        return whole
    if isinstance(whole, dict):                        # {"items":[...]} / {"endpoints":[...]} 等包裹
        for v in whole.values():
            if isinstance(v, list):
                return v
        return []
    start, end = s.find("["), s.rfind("]")             # 兜底:噪声里抠最外层数组
    if 0 <= start < end:
        arr = _loads_or_none(s[start:end + 1])
        if isinstance(arr, list):
            return arr
    return []


def extract_json_obj(s: str) -> dict:
    """从模型输出里抠出 JSON 对象(容忍 ```json 围栏 / 前后噪声)。失败 → {}。"""
    if not s:
        return {}
    fenced = _FENCE_OBJ.search(s)
    if fenced:
        obj = _loads_or_none(fenced.group(1))
        if isinstance(obj, dict):
            return obj
    whole = _loads_or_none(s.strip())
    if isinstance(whole, dict):
        return whole
    start, end = s.find("{"), s.rfind("}")
    if 0 <= start < end:
        obj = _loads_or_none(s[start:end + 1])
        if isinstance(obj, dict):
            return obj
    return {}


def estimate_tokens(text: str) -> int:
    """无依赖的粗略 token 估算(偏保守,宁多勿少):每个 CJK 字≈1 token,其余每 4 字符≈1 token。

    只用于「喂模型前按预算截断」的相对比较,不追求与某具体 tokenizer 精确一致;跨 provider 通用,
    免引入 tiktoken 之类重依赖。字符切片(len[:N])会严重低估 CJK 的真实 token 量,故改用本估算。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk + (len(text) - cjk) // 4 + 1


def wrap_data(label: str, text: str) -> str:
    """把不可信外部数据(接口文档原文等)包进带标签的分隔块。

    块内一律当**数据**处理,模型不得执行其中任何看似指令的内容(轻量 prompt-injection 防护)。
    调用方在 prompt 指令里引用同一 label,模型即知边界。label 用大写英文(如 DOC)。
    """
    return f"<<<{label}>>>\n{text}\n<<<END_{label}>>>"
