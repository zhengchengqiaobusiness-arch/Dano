"""通用 LLM 文本生成调用。"""

from __future__ import annotations

import structlog


log = structlog.get_logger(__name__)


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}      # 限流/瞬时故障:退避重试,别当成"模型写了烂码"


async def openai_text_spawn(prompt: str, *, timeout_s: float = 300.0, tag: str = "llm",
                            max_attempts: int = 4, json_mode: bool = False) -> str:
    """默认编码 spawn:OpenAI 兼容 /chat/completions(任意 base_url,如 SiliconFlow/DeepSeek)。

    用 settings.pi_base_url + pi_api_key + pi_model。tag 仅用于日志(标识 planner/coder/classify…)。
    **限流/瞬时错(429/5xx/网络/空响应)→ 退避重试**,不要直接返回空(否则上层误判"模型产出烂码",
    白白烧光生成预算——并发批量调用时尤其致命)。401/400 等不可重试错则立即返回空。

    json_mode=True:契约是「只输出 JSON」的调用(分类/拆解/抽取等)开启 `response_format=json_object`,
    让模型更可能吐合法 JSON。**注意:json_object 模式要求顶层是对象**,故数组类输出须包成对象
    (如 {"items":[...]});调用方负责。模型(部分 reasoner)不支持 response_format 而回 400/422 时,
    自动去掉它**降级重试一次**(行为不变,只是少了格式约束),绝不因此阻断接入。
    """
    import asyncio
    import time

    import httpx

    from dano.config import get_settings
    s = get_settings()
    if not (s.pi_api_key or "").strip():
        raise RuntimeError("未配置模型 API Key:请先在前端「运行配置」填写 SiliconFlow Key 并保存")
    base = s.pi_base_url.rstrip("/")
    url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    headers = {"Authorization": f"Bearer {s.pi_api_key.strip()}", "Content-Type": "application/json"}
    payload: dict = {"model": s.pi_model, "temperature": 0,
                     "messages": [{"role": "user", "content": prompt}]}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async def _backoff(attempt: int, retry_after: str | None = None) -> None:
        ra = (retry_after or "").strip()
        delay = float(ra) if ra.replace(".", "", 1).isdigit() else min(2.0 ** attempt, 12.0)
        await asyncio.sleep(delay)

    log.info("llm.request", tag=tag, model=s.pi_model, prompt_chars=len(prompt),
             timeout_s=timeout_s, max_attempts=max_attempts)
    for attempt in range(1, max_attempts + 1):
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as c:
                r = await c.post(url, json=payload, headers=headers)
        except Exception as e:  # noqa: BLE001 - 超时/网络错:可重试
            dur = round(time.monotonic() - t0, 1)
            log.warning("llm.network_error", tag=tag, error=repr(e), dur_s=dur, attempt=attempt)
            if attempt < max_attempts:
                await _backoff(attempt)
                continue
            return ""
        dur = round(time.monotonic() - t0, 1)
        if r.status_code in _RETRYABLE_STATUS:        # 限流/瞬时 → 退避重试(尊重 Retry-After)
            log.warning("llm.http_retry", tag=tag, status=r.status_code, dur_s=dur,
                        attempt=attempt, body=r.text[:200])
            if attempt < max_attempts:
                await _backoff(attempt, r.headers.get("retry-after"))
                continue
            return ""
        if r.status_code >= 400:                       # 401/403/400 等:配置/鉴权错,重试无意义
            if (json_mode and r.status_code in (400, 422)
                    and "response_format" in payload):  # 模型不支持 JSON 模式 → 去掉它降级重试(不计退避)
                payload.pop("response_format")
                log.warning("llm.json_mode_unsupported", tag=tag, status=r.status_code, dur_s=dur)
                continue
            log.warning("llm.http_error", tag=tag, status=r.status_code, body=r.text[:300], dur_s=dur)
            return ""
        try:
            out = r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, ValueError) as e:
            log.warning("llm.bad_response", tag=tag, error=repr(e), body=r.text[:300], dur_s=dur)
            return ""
        if not out.strip() and attempt < max_attempts:  # 空响应(推理型偶发)→ 再试一次
            log.warning("llm.empty_retry", tag=tag, dur_s=dur, attempt=attempt)
            await _backoff(attempt)
            continue
        log.info("llm.response", tag=tag, resp_chars=len(out), dur_s=dur,
                 empty=(not out.strip()), attempt=attempt)
        return out
    return ""


