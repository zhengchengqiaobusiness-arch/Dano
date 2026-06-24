"""P0 · OA 层探测(一次接入探一次,全业务共享)。

OAProfile 装这台 OA 的**通用工作流能力**(查待办/已办/在途/草稿/查状态/撤销/催办)真实端点:
靠 **LLM 识别 + 探针确认** 得出(不写死任何路径);供每个业务的剧本复用,避免 N× 重复探测。

泛化:LLM 据"框架名 + 已知端点"推断这类系统**最可能**的能力端点,探针(只 GET 探存在性)
确认存在的才留。换框架/换公司 → LLM 推不同的、探针挡不存在的,自动适配。失败 → 能力为空,
上层照常(每业务仍可单独发现),绝不阻断接入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial

import structlog

from dano.shared.prompt_utils import extract_json_array

log = structlog.get_logger(__name__)

# 一个审批/工作流系统"应该有"的通用能力(canonical kind → 中文用途 + 是否写)。
# 这不是端点路径(那是探出来的),只是"该问 OA 有没有这些能力"的清单。
_CAPABILITY_KINDS: dict[str, tuple[str, bool]] = {
    "query_my_todo": ("我的待办(轮到我处理的)", False),
    "query_my_done": ("我的已办(我处理过的)", False),
    "query_in_progress": ("在途流程实例(正在跑的)", False),
    "query_my_drafts": ("我发起的/草稿", False),
    "query_status": ("查某流程走到哪一步/历史", False),
    "cancel": ("撤销/取回已发起的流程", True),
    "urge": ("催办", True),
}


@dataclass
class Capability:
    """一项通用工作流能力在本 OA 的真实落点(探针确认存在)。"""

    kind: str
    purpose: str
    method: str
    endpoint: str
    write: bool
    name: str = ""        # 命中文档动作时的 action 名(没有则空,合成)


@dataclass
class OAProfile:
    """OA 层共享画像:框架 + 成功约定 + 已确认存在的通用能力。每次接入探一次。"""

    framework: str = ""
    success_rule: str = ""
    capabilities: list[Capability] = field(default_factory=list)

    def by_kind(self, kind: str) -> Capability | None:
        for c in self.capabilities:
            if c.kind == kind:
                return c
        return None


_PROMPT = """这是一个工作流/审批系统(框架:{framework})。下面是已知的真实端点(name | METHOD path | 用途)。
请推断这个系统里下列**通用能力**最可能对应的端点。已知端点里有就用已知的;没有就按该框架惯例推断路径。

需要推断的能力(键不可改;某能力你判断该系统不会有,就**省略**该项,绝不硬凑):
{kinds}

每项输出对象:{{"kind": 能力键, "method": "GET/POST", "endpoint": "/路径", "name": 命中的已知端点name或""}}
**只输出一个 JSON 对象**:{{"capabilities": [ ... ]}}(capabilities 为上述对象的数组),不要解释、不要代码块。

已知端点:
{lines}
"""


async def build_oa_profile(actions: list[dict], *, framework: str = "", success_rule: str = "",
                           probe=None, spawn=None) -> OAProfile:  # noqa: ANN001
    """探一次:LLM 推断通用能力端点 → 探针(只 GET)确认存在 → 组装 OAProfile。

    probe: async (endpoint_path)->status_code|None(只读探针;None=不探,直接信 LLM 推断但仍标未确认)。
    spawn: async (prompt)->text(LLM);None 时用默认 coder spawn。
    任何失败 → 返回 framework/success_rule + 空能力(不阻断)。
    """
    prof = OAProfile(framework=framework or "", success_rule=success_rule or "")
    if spawn is None:
        from dano.generation.coder import openai_text_spawn
        spawn = partial(openai_text_spawn, tag="oa_profile", json_mode=True)
    kinds_txt = "\n".join(f"- {k}:{purpose}{'(写)' if w else '(读)'}"
                          for k, (purpose, w) in _CAPABILITY_KINDS.items())
    ordered = _workflow_first(actions or [])              # 工作流相关端点排前,确保大目录里不被截断掉
    lines = "\n".join(f"{a.get('name')} | {(a.get('method') or 'GET').upper()} {a.get('endpoint')}"
                      f" | {(a.get('summary') or '')[:50]}" for a in ordered)[:9000]
    try:
        raw = await spawn(_PROMPT.format(framework=framework or "未知", kinds=kinds_txt, lines=lines))
    except Exception as e:  # noqa: BLE001 - LLM 失败不阻断
        log.warning("oa_profile.llm_failed", error=str(e))
        return prof
    items = extract_json_array(raw)
    by_name = {a.get("name"): a for a in (actions or []) if a.get("name")}
    confirmed: list[Capability] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "")
        if kind not in _CAPABILITY_KINDS:                  # 只认清单内的能力键,不容 LLM 自造
            continue
        purpose, write = _CAPABILITY_KINDS[kind]
        ep = str(it.get("endpoint") or "").strip()
        method = str(it.get("method") or ("POST" if write else "GET")).upper()
        name = str(it.get("name") or "")
        if name in by_name:                                # 命中已知端点 → 以文档为准
            ep = by_name[name].get("endpoint") or ep
            method = (by_name[name].get("method") or method).upper()
        if not ep:
            continue
        if probe is not None:                              # 探针确认:只 GET 探存在性,404/超时→跳
            status = await _exists(probe, ep)
            if status is False:
                log.info("oa_profile.cap_skipped", kind=kind, endpoint=ep)
                continue
        if any(c.kind == kind for c in confirmed):         # 同能力只留一个
            continue
        confirmed.append(Capability(kind=kind, purpose=purpose, method=method,
                                    endpoint=ep, write=write, name=name))
    prof.capabilities = confirmed
    log.info("oa_profile.done", framework=prof.framework, success_rule=prof.success_rule,
             capabilities=[c.kind for c in confirmed])
    return prof


# 工作流/审批相关的通用词(只为"把相关端点排前",不绑定任何业务;非命中也不丢)
_WF_KEYWORDS = ("todo", "done", "process", "flow", "instance", "task", "draft", "revoke",
                "urge", "monitor", "recycle", "handle", "apply", "/my", "owner", "running", "history")


def _workflow_first(actions: list[dict]) -> list[dict]:
    """把工作流相关端点排到前面(大 swagger 截断时优先保留它们)。"""
    wf, rest = [], []
    for a in actions:
        hay = f"{a.get('endpoint', '')} {a.get('summary', '')}".lower()
        (wf if any(k in hay for k in _WF_KEYWORDS) else rest).append(a)
    return wf + rest


async def _exists(probe, endpoint: str) -> bool:  # noqa: ANN001
    """只读探针判端点是否存在:404/网络失败=不存在(跳),其余(200/401/405/500)=存在。"""
    try:
        status = await probe(endpoint)
    except Exception:  # noqa: BLE001
        return False
    if status is None:                                     # 网络/超时:保守判不存在(宁漏不误建)
        return False
    return status != 404


