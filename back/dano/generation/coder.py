"""生成器(创造步骤:拆解/编码/修复)。

Coder 是可注入接口:
- 真实路径 `PiCoder`:把方案+驳回原因喂给 pi(goal 模式),取回 run() 源码;闸门由 controller 主导。
- 测试路径:注入 Fake(确定性 buggy→fixed),验证「循环 + 闸门 + 驳回重写」本身。

codegen_prompt 是统一编码/修复提示;驳回时把 reasons 回灌(非一次成型)。
"""

from __future__ import annotations

import re
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
        self._spawn = spawn or openai_text_spawn       # 默认走 OpenAI 兼容(任意 base_url,如 SiliconFlow)
        self._timeout = timeout_s

    async def generate(self, *, plan: PlanBody, feedback: list[str]) -> dict:
        from dano.generation.strategies import get_strategy
        strat = get_strategy(plan.strategy)
        skeleton = strat.code_skeleton(plan) if strat else ""
        prompt = (codegen_prompt(plan, feedback, skeleton)
                  + "\n\n只输出最终 run() 源码,放在 <ADAPTER> 与 </ADAPTER> 之间,不要其它文字。")
        text = await self._spawn(prompt)
        src = extract_source(text)
        if not src:
            log.warning("pi_coder.no_source", flow=plan.flow)
            src = "def run(inputs, creds):\n    raise RuntimeError('pi 未产出可解析源码')\n"
        return {"action": plan.flow, "strategy": plan.strategy, "source": src, "entry": "run",
                "success_rule": plan.success_rule,
                "fact_check": plan.fact_check.model_dump() if plan.fact_check else None,
                "user_fields": plan.user_fields, "required_fields": plan.required_fields}


async def openai_text_spawn(prompt: str, *, timeout_s: float = 300.0) -> str:
    """默认编码 spawn:OpenAI 兼容 /chat/completions(任意 base_url,如 SiliconFlow/DeepSeek)。

    用 settings.pi_base_url + pi_api_key + pi_model;纯文本生成(取 <ADAPTER> 源码),不强制 JSON。
    """
    import httpx

    from dano.config import get_settings
    s = get_settings()
    base = s.pi_base_url.rstrip("/")
    url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    headers = {"Authorization": f"Bearer {s.pi_api_key}", "Content-Type": "application/json"}
    payload = {"model": s.pi_model, "temperature": 0,
               "messages": [{"role": "user", "content": prompt}]}
    async with httpx.AsyncClient(timeout=timeout_s) as c:
        r = await c.post(url, json=payload, headers=headers)
    if r.status_code >= 400:
        log.warning("openai_text_spawn.http_error", status=r.status_code, body=r.text[:300])
        return ""
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
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
