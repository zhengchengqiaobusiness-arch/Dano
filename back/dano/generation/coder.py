"""生成器(创造步骤:拆解/编码/修复)。

Coder 是可注入接口:
- 真实路径 `PiCoder`:把方案+驳回原因喂给 pi(goal 模式),取回 run() 源码;闸门由 controller 主导。
- 测试路径:注入 Fake(确定性 buggy→fixed),验证「循环 + 闸门 + 驳回重写」本身。

codegen_prompt 是统一编码/修复提示;驳回时把 reasons 回灌(非一次成型)。
"""

from __future__ import annotations

import json
import re
from functools import partial
from typing import Awaitable, Callable, Protocol

import structlog

from dano.shared.asset_bodies import PlanBody

log = structlog.get_logger(__name__)


class Coder(Protocol):
    async def generate(self, *, plan: PlanBody, feedback: list[str]) -> dict:
        """产出一份 AdapterBody(dict);feedback 为上一轮闸门的驳回原因,须据此修复。"""
        ...


def codegen_prompt(plan: PlanBody, feedback: list[str], code_skeleton: str) -> str:
    """goal 模式编码/修复提示。feedback 非空 = 上一轮被驳回,必须按因修复后重产。"""
    fb = ("\n\n上一轮被**驳回**,本轮必须按以下原因修复:\n- " + "\n- ".join(feedback)) if feedback else ""
    return (
        f"目标:为业务流程「{plan.flow}」编写可执行适配器(Python)。\n"
        f"硬约束:入口函数 run(inputs: dict, creds: dict) -> dict;"
        f"凭证只从 creds 取(如 creds['token']),**任何密钥都不得写进源码**;不得用 eval/exec/os.system。\n"
        f"步骤/契约:{plan.steps}\n成败规则(成功判定):{plan.success_rule}\n"
        f"参考骨架:\n{code_skeleton}\n"
        f"{fb}"
    )


# v3 通用编码骨架(OA 无关:只给签名 + 规约,不含任何具体系统的契约)
GENERIC_SKELETON = (
    "import json\n"
    "import httpx\n\n"
    "def run(inputs, creds):\n"
    "    base = inputs['__base_url__'].rstrip('/')\n"
    "    headers = {'Authorization': 'Bearer ' + creds['token']}   # 凭证仅来自 creds,不得入码\n"
    "    # inputs 是扁平业务字段 + 运行期注入的 __xxx__ 常量(如 __templateId__);\n"
    "    # 按下方【证据】里的真实端点实现本流程的多步调用,每步都看响应体判断成败。\n"
    "    with httpx.Client(timeout=30, verify=False) as c:\n"
    "        ...\n"
    "    return {...}   # 必须带上可核查标识(如单号/id/procInsId),供事实核查\n"
)


def _fmt_evidence(ev: dict, *, budget: int = 14000) -> str:
    """把证据压成紧凑文本喂给模型(端点 + 表单字段 + 样例返回结构),限长。"""
    if not ev:
        return "(无)"
    parts = []
    if ev.get("all_endpoints"):
        parts.append("可用端点全集(name|method|endpoint):")
        parts.extend("  " + e for e in ev["all_endpoints"])
    acts = ev.get("actions") or []
    if acts:
        parts.append("相关端点详情(name|method|endpoint|必填|出参):")
        for a in acts:
            parts.append(f"  {a.get('name')}|{a.get('method')}|{a.get('endpoint')}"
                         f"|必填{a.get('required') or []}|出参{(a.get('params_out') or [])[:8]}")
            ex = a.get("request_example")
            if ex not in (None, {}, []):                  # 请求体示例/嵌套结构:揭示双层嵌套契约,照它填
                parts.append(f"    请求体示例(照此结构/嵌套填): {json.dumps(ex, ensure_ascii=False)[:900]}")
    if ev.get("form_fields"):
        parts.append("表单字段(key|label|type):")
        for f in ev["form_fields"]:
            parts.append(f"  {f.get('key')}|{f.get('label')}|{f.get('type')}")
    if ev.get("sample_reads"):
        parts.append("样例返回结构(端点→路径):")
        for s in ev["sample_reads"]:
            parts.append(f"  {s.get('endpoint')} → {(s.get('output_paths') or [])[:20]}")
    text = "\n".join(parts)
    return text[:budget] + ("\n…(已截断)" if len(text) > budget else "")


def evidence_codegen_prompt(plan: PlanBody, feedback: list[str]) -> str:
    """v3 模型驱动编码提示:给【真实证据 + 步骤 + 真实报错】,模型据实写码,**不给 OA 专用骨架**。"""
    fb = ("\n\n上一轮真打目标系统**失败/被驳回**,以下是**真实报错/响应**,必须据此修正(可能缺步骤/字段/串联错):\n- "
          + "\n- ".join(feedback)) if feedback else ""
    return (
        f"目标:实现业务流程「{plan.flow}」,产出可执行 run(inputs, creds) -> dict,真打目标系统完成它。\n"
        "硬约束:\n"
        "- 入口 run(inputs, creds);凭证只从 creds(如 creds['token']),源码零密钥;不得 eval/exec/os.system。\n"
        "- base_url = inputs['__base_url__'];业务字段与 __xxx__ 常量都在 inputs 里。\n"
        "- 表达式/判断用 **Python 语法**(and/or/not、!=、None),不要 JS 的 &&/||/null/===。\n"
        "- **用到的库必须 import**(如 import httpx、import json)。\n"
        "- **禁止把异常 try/except 吞进返回值**(如 return {'_adapter_error': ...}):要么让它真实抛出、"
        "要么返回目标系统的**真实响应**;吞掉错误会被判失败。\n"
        "- **只调用与本流程相关的端点**;不要为凑数去调无关端点(如缓存监控/节假日/字典),除非确属本流程必需。\n"
        "- 证据里某端点给了**请求体示例**时,**严格照它的字段与嵌套结构填**(尤其双层嵌套、变量子对象);"
        "示例里 <xxx> 占位的值,用前序步骤的真实返回填(如 <startFlow.taskId> 取自发起流程的返回)。\n"
        "- 目标系统可能 HTTP200 也返回失败或**空操作**,务必看响应体;返回里带可核查标识(单号/id 等)。\n"
        f"拆解步骤(参考,可按真实报错调整):{plan.steps}\n"
        f"契约要点:{plan.contract}\n"
        f"【证据】(真实端点 + 表单字段 + 样例返回结构):\n{_fmt_evidence(plan.evidence)}\n"
        f"通用骨架:\n{GENERIC_SKELETON}"
        f"{fb}"
    )


_ADAPTER_TAG = re.compile(r"<ADAPTER>(.*?)</ADAPTER>", re.S)
_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.S)


def extract_source(text: str) -> str | None:
    """从 pi 输出里抽 run() 源码:优先 <ADAPTER>…</ADAPTER>,次选 ```代码块,
    最后兜底处理"只有开标签无闭合"(被截断)的情况——取开标签之后到结尾。"""
    text = text or ""
    m = _ADAPTER_TAG.search(text) or _FENCE.search(text)
    if m:
        return m.group(1).strip()
    if "<ADAPTER>" in text:                       # 有开标签但无闭合(截断)→ 取其后内容
        tail = text.split("<ADAPTER>", 1)[1].split("</ADAPTER>", 1)[0]
        return tail.strip() or None
    if "def run(" in text:                        # 实在没有标签但有函数定义 → 取从 def run 起
        return text[text.index("def run("):].strip()
    return None


# 文本生成式 spawn:async (prompt) -> pi 最终文本。默认实现 spawn run_pi.mjs(需 DANO_PI_API_KEY)。
TextSpawn = Callable[[str], Awaitable[str]]


class PiCoder:
    """真实生成器:把 plan + 骨架 + 驳回原因喂给 pi,取回 run() 源码。

    spawn 可注入(测试用 fake);默认 pi_text_spawn 真起 pi(需 key)。闸门(测试/漏洞/审核)
    仍由 controller 主导——pi 只负责"写/改代码",不能自证发布。
    """

    def __init__(self, *, spawn: TextSpawn | None = None, timeout_s: float = 300.0) -> None:
        self._spawn = spawn or partial(openai_text_spawn, tag="coder")   # 默认 OpenAI 兼容(SiliconFlow 等)
        self._timeout = timeout_s

    async def generate(self, *, plan: PlanBody, feedback: list[str]) -> dict:
        # v3:有证据 → 证据驱动通用提示(无 OA 专用骨架);否则退回策略骨架(读流程/兜底)
        if plan.evidence:
            prompt = evidence_codegen_prompt(plan, feedback)
        else:
            from dano.generation.strategies import get_strategy
            strat = get_strategy(plan.strategy)
            prompt = codegen_prompt(plan, feedback, strat.code_skeleton(plan) if strat else "")
        prompt += "\n\n只输出最终 run() 源码,放在 <ADAPTER> 与 </ADAPTER> 之间,不要其它文字。"
        mode = "evidence" if plan.evidence else "skeleton"
        log.info("coder.generate", flow=plan.flow, mode=mode, fixing=bool(feedback),
                 endpoints=len((plan.evidence or {}).get("actions", [])), prompt_chars=len(prompt))
        text = await self._spawn(prompt)
        src = extract_source(text)
        if not src:
            log.warning("coder.no_source", flow=plan.flow, resp_chars=len(text or ""),
                        resp_head=(text or "")[:160])
            src = "def run(inputs, creds):\n    raise RuntimeError('pi 未产出可解析源码')\n"
        else:
            log.info("coder.source_ready", flow=plan.flow, lines=src.count("\n") + 1)
        return {"action": plan.flow, "strategy": plan.strategy, "source": src, "entry": "run",
                "success_rule": plan.success_rule,
                "fact_check": plan.fact_check.model_dump() if plan.fact_check else None,
                "user_fields": plan.user_fields, "required_fields": plan.required_fields,
                "field_docs": plan.field_docs, "consts": plan.consts, "evidence": plan.evidence}


async def openai_text_spawn(prompt: str, *, timeout_s: float = 300.0, tag: str = "llm") -> str:
    """默认编码 spawn:OpenAI 兼容 /chat/completions(任意 base_url,如 SiliconFlow/DeepSeek)。

    用 settings.pi_base_url + pi_api_key + pi_model;纯文本生成(取 <ADAPTER> 源码),不强制 JSON。
    tag 仅用于日志(标识是 planner/coder/classify… 哪一类调用)。
    """
    import time

    import httpx

    from dano.config import get_settings
    s = get_settings()
    if not (s.pi_api_key or "").strip():
        raise RuntimeError("未配置模型 API Key:请先在前端「运行配置」填写 SiliconFlow Key 并保存")
    base = s.pi_base_url.rstrip("/")
    url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    headers = {"Authorization": f"Bearer {s.pi_api_key.strip()}", "Content-Type": "application/json"}
    payload = {"model": s.pi_model, "temperature": 0,
               "messages": [{"role": "user", "content": prompt}]}
    t0 = time.monotonic()
    log.info("llm.request", tag=tag, model=s.pi_model, prompt_chars=len(prompt), timeout_s=timeout_s)
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(url, json=payload, headers=headers)
    except Exception as e:  # noqa: BLE001 - 超时/网络错 → 当作"没产出",让循环重试而非崩
        log.warning("llm.network_error", tag=tag, error=repr(e), dur_s=round(time.monotonic() - t0, 1))
        return ""
    dur = round(time.monotonic() - t0, 1)
    if r.status_code >= 400:
        log.warning("llm.http_error", tag=tag, status=r.status_code, body=r.text[:300], dur_s=dur)
        return ""
    try:
        out = r.json()["choices"][0]["message"]["content"] or ""
        log.info("llm.response", tag=tag, resp_chars=len(out), dur_s=dur,
                 empty=(not out.strip()))
        return out
    except (KeyError, IndexError, TypeError, ValueError) as e:
        log.warning("llm.bad_response", tag=tag, error=repr(e), body=r.text[:300], dur_s=dur)
        return ""


async def pi_text_spawn(prompt: str, *, timeout_s: float = 300.0) -> str:
    """默认 spawn:起 run_pi.mjs,送 start_run(prompt),读到 run_completed,返回 final_text。

    纯文本生成(pi 不调工具),故无需工具服务;需 DANO_PI_API_KEY(否则 pi 返回 no_model_or_credentials)。
    """
    import asyncio
    import json
    import os
    from pathlib import Path

    back = Path(__file__).resolve().parent.parent.parent       # .../back
    keep = ("PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "windir", "ComSpec",
            "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
            "NUMBER_OF_PROCESSORS", "OS", "HOMEDRIVE", "HOMEPATH")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    for k in ("DANO_PI_API_KEY", "DANO_PI_BASE_URL", "DANO_PI_MODEL", "DANO_PI_PROVIDER"):
        if k in os.environ:
            env[k] = os.environ[k]
    env["PI_STUB"] = "0"
    proc = await asyncio.create_subprocess_exec(
        "node", str(back / "agent" / "run_pi.mjs"), cwd=str(back), env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    run_id = "codegen"
    start = json.dumps({"type": "start_run", "run_id": run_id, "prompt": prompt,
                        "budget": {"timeout_s": int(timeout_s)}}) + "\n"
    proc.stdin.write(start.encode()); await proc.stdin.drain()
    final = ""
    try:
        async def _read():
            nonlocal final
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode().strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "run_completed":
                    final = ev.get("final_text", "") or ""
                    return
        await asyncio.wait_for(_read(), timeout=timeout_s)
    except asyncio.TimeoutError:
        final = ""
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    return final
