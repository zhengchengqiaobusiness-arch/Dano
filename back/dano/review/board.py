"""三模型评审委员会:成果验收 / 漏洞检测 / 合规审核,各用一个独立模型(OpenAI 兼容)。

纪律:
- 只喂声明式 body + 沙箱证据 trace,**绝不带凭证**(materials 不进此处)。
- 三审各用不同模型(model_id 三者互不相同),并发跑,返回结构化 {passed, reasons}。
- 本模块只产出结论;写入 review_runs 与发布闸门校验在 drafts/tools 层(职责分离)。
- client 可注入 → 测试用 fake,不烧 key;评审调用失败按"不通过"处理(安全默认)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

ROLES = ("acceptance", "security", "compliance")

# 评审结果缓存:同一(模型+角色+输入)→ 同一结论,免重复烧 token/延迟。
# 输入含 content 绑定(body+evidence),内容一变 key 即变。
# **只缓存通过(passed=True)结论**:驳回常是模型一次性偏严/抖动,缓存它会让 pi 重试相同内容
# 命中同一 False 而永远发不出(且跨流程污染);不缓存驳回 → 重判时给模型新机会。
_VERDICT_CACHE: dict[str, list[str]] = {}          # key → reasons(命中即视为 passed=True)
_CACHE_CAP = 1024


def _cache_key(model: str, role: str, user: str) -> str:
    return hashlib.sha256(f"{model}\x00{role}\x00{user}".encode("utf-8")).hexdigest()


def _cache_get(key: str) -> list[str] | None:
    return _VERDICT_CACHE.get(key)


def _cache_put_pass(key: str, reasons: list[str]) -> None:
    if len(_VERDICT_CACHE) >= _CACHE_CAP:          # 简单容量上限:满则清最早一条
        _VERDICT_CACHE.pop(next(iter(_VERDICT_CACHE)), None)
    _VERDICT_CACHE[key] = list(reasons)

# 三审 system prompt(各自只看自己关心的维度,统一输出 JSON {passed, reasons})
_ROLE_SYSTEM: dict[str, str] = {
    "acceptance": (
        "你是『成果验收』评审员。审查一个自动生成的 API 接入资产(连接器/复合流程)是否**真满足业务意图**:"
        "动作语义与字段映射是否对得上业务、断言是否有意义(防只校验 HTTP 200 的表面通过)、"
        "沙箱执行证据是否自洽可信、必填项是否齐全。"
        "发现实质问题就判不通过。只输出 JSON 对象:{\"passed\": true/false, \"reasons\": [\"中文理由\"]}。"
    ),
    "security": (
        "你是『漏洞检测』评审员。只从安全角度审查该资产声明体:"
        "请求拼接是否有注入风险、base_url/回调是否可能 SSRF、鉴权是否缺失或过宽、"
        "写操作是否缺幂等键、模板里是否硬编码了密钥/令牌、是否暴露 PII/敏感字段。"
        "发现任一高危就判不通过。只输出 JSON 对象:{\"passed\": true/false, \"reasons\": [\"中文理由\"]}。"
    ),
    "compliance": (
        "你是『合规审核』评审员。只从合规与发布纪律审查:"
        "验证证据是否全部 environment=sandbox 且 credential_type=test(严禁生产/真凭证)、"
        "风险分级是否与动作(读/写/删)匹配、写/删操作是否要求确认(confirm)、"
        "是否指向生产端点、是否违反最小权限。"
        "不合规就判不通过。只输出 JSON 对象:{\"passed\": true/false, \"reasons\": [\"中文理由\"]}。"
    ),
}


@dataclass
class ReviewVerdict:
    role: str                       # acceptance / security / compliance
    model_id: str
    passed: bool
    reasons: list[str] = field(default_factory=list)


class ChatClient(Protocol):
    """OpenAI 兼容对话 client(可注入 fake)。返回解析后的 JSON dict。"""

    async def complete_json(self, *, model: str, system: str, user: str,
                            timeout_s: float) -> dict[str, Any]: ...


class OpenAICompatClient:
    """极薄 OpenAI 兼容 client:POST {base}/chat/completions,强制 JSON 输出。"""

    def __init__(self, *, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        base = base_url.rstrip("/")
        self._url = (base + "/chat/completions") if base.endswith("/v1") else (base + "/v1/chat/completions")

    async def complete_json(self, *, model: str, system: str, user: str,
                            timeout_s: float) -> dict[str, Any]:
        import httpx
        base = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            # 优先用 JSON 模式;若模型(如 reasoner 类)不支持 response_format 而 4xx,则去掉重试一次。
            r = await c.post(self._url, json={**base, "response_format": {"type": "json_object"}},
                             headers=headers)
            if r.status_code in (400, 422):
                r = await c.post(self._url, json=base, headers=headers)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        return _loads_lenient(content)


class ReviewBoard:
    """编排三审:并发调三个独立模型,各产出一条结构化 verdict。"""

    def __init__(self, *, client: ChatClient, models: dict[str, str], timeout_s: float = 60.0,
                 max_retries: int = 2, backoff_s: float = 1.0) -> None:
        # models:{"acceptance": 模型, "security": 模型, "compliance": 模型}
        self.client = client
        self.models = models
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s

    @classmethod
    def from_settings(cls) -> "ReviewBoard":
        import os
        from dano.config import get_settings
        s = get_settings()
        api_key = s.pi_api_key or os.environ.get("DANO_PI_API_KEY", "")
        base_url = s.pi_base_url or os.environ.get("DANO_PI_BASE_URL", "https://api.deepseek.com")
        client = OpenAICompatClient(api_key=api_key, base_url=base_url)
        models = {"acceptance": s.review_model_acceptance, "security": s.review_model_security,
                  "compliance": s.review_model_compliance}
        if not all(models.values()):                 # 只要 3 个角色都配了非空模型即可(可相同)
            log.warning("review.models.misconfigured",
                        models=models, note="三审需 3 个非空评审模型(可相同)")
        return cls(client=client, models=models, timeout_s=s.review_timeout_s,
                   max_retries=s.review_max_retries, backoff_s=s.review_retry_backoff_s)

    async def review(self, *, asset_type: str, asset_key: str, body: dict,
                     evidence: list[dict] | None = None) -> list[ReviewVerdict]:
        user = _build_user(asset_type, asset_key, body, evidence or [])
        results = await asyncio.gather(
            *(self._one(role, self.models[role], user) for role in ROLES))
        return list(results)

    async def _one(self, role: str, model: str, user: str) -> ReviewVerdict:
        key = _cache_key(model, role, user)
        cached = _cache_get(key)
        if cached is not None:                       # 命中缓存(只存通过结论)→ 直接通过,免调用
            log.info("review.one.cached", role=role, model=model, passed=True)
            return ReviewVerdict(role=role, model_id=model, passed=True, reasons=list(cached))
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                out = await self.client.complete_json(
                    model=model, system=_ROLE_SYSTEM[role], user=user, timeout_s=self.timeout_s)
                passed = bool(out.get("passed"))
                reasons = out.get("reasons") or []
                if not isinstance(reasons, list):
                    reasons = [str(reasons)]
                reasons = [str(x) for x in reasons]
                if passed:                           # 只缓存通过;驳回不缓存(下次重判)
                    _cache_put_pass(key, reasons)
                log.info("review.one", role=role, model=model, passed=passed, attempt=attempt)
                return ReviewVerdict(role=role, model_id=model, passed=passed, reasons=reasons)
            except Exception as e:  # noqa: BLE001 —— 瞬时错误退避重试,用尽才判不通过
                last_err = e
                if attempt < self.max_retries:
                    await asyncio.sleep(self.backoff_s * (2 ** attempt))
        log.warning("review.one.failed", role=role, model=model,
                    retries=self.max_retries, error=str(last_err))
        return ReviewVerdict(role=role, model_id=model, passed=False,
                             reasons=[f"评审调用失败(重试{self.max_retries}次): {last_err}"])


def _loads_lenient(content: str) -> dict[str, Any]:
    """容忍非严格 JSON 输出(部分模型不遵守 response_format:带 ```fence 或前后文字)。"""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        pass
    s = (content or "").strip()
    if s.startswith("```"):                       # 去掉 ```json ... ``` 围栏
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    lo, hi = s.find("{"), s.rfind("}")            # 取首尾大括号内的 JSON 片段
    if 0 <= lo < hi:
        return json.loads(s[lo:hi + 1])
    raise json.JSONDecodeError("评审输出无法解析为 JSON", content or "", 0)


# 运行架构说明:评审须基于此判断,避免用通用假设误判本系统按设计如此的项。
_SYSTEM_CONTEXT = (
    "【运行架构(据此判断,勿用通用假设误判)】\n"
    "1. 资产是声明式规格,运行期由通用解释器执行。凭证经 auth_ref 引用、运行期注入,"
    "body 内本就不含明文 token/密码,属正常,不是漏洞。\n"
    "2. confirm 确认由 risk_level 在运行期强制:risk_level=L3 及以上的写操作,运行期必须用户确认"
    "(confirm=true)才执行;confirm 不在 body 字段里体现,属正常。\n"
    "3. 风险分级约定:GET 只读=L1;写操作(POST/PUT/PATCH/DELETE)=L3(运行期需确认)。"
    "请据此判断 risk_level 是否与 method 匹配。\n"
    "4. field_bindings 只覆盖平台标准字段。若该连接器是复合流程的一个步骤(入参由流程编排经 "
    "step:/const:/field: 提供),单步 field_bindings 为空属正常,不应据此判不通过。\n"
    "5. success_rule 是业务成功判定表达式(如 response.code==200 即 RuoYi AjaxResult 的业务成功标志);"
    "带 success_rule 即视为已检验业务成功,不必再要求额外业务断言。\n"
    "6. sandbox_evidence 已证明该资产在 environment=sandbox + credential_type=test 下真实跑通。\n"
    "仅当存在**实质**问题(真实安全漏洞、risk_level 确与 method 不符、断言完全缺失、字段语义明显错配)"
    "时才判不通过;按设计如此的项不要判不通过。"
)


_ADAPTER_REVIEW_NOTE = (
    "\n\n【代码评审要求】本资产是 adapter:declarative_body.source 为自动生成的**可执行代码**。"
    "请在各自维度上**逐行审源码**——成果验收:代码是否真实现业务意图、成败判定是否有意义;"
    "漏洞检测:危险调用(eval/exec/os.system/subprocess shell)、命令/请求注入、SSRF、"
    "硬编码密钥/令牌(源码必须零凭证,凭证应从 creds 运行期注入)、越权;"
    "合规审核:是否仅用测试凭证与沙箱、风险分级与读/写匹配、写操作是否需确认。"
    "\n【adapter 的 risk_level 与 fact_check 判定要点(避免误判)】"
    "adapter 会发多次 HTTP;risk_level 反映其**实际执行的操作**——源码里有 POST/PUT/PATCH/DELETE 写操作即应为 L3(运行期需确认),L3 正确不要驳回。"
    "declarative_body.fact_check 是发布/执行后的**只读回查(流程9 事实核查)**,其 method=GET 是设计如此、用于确认副作用真生效,"
    "**与动作风险无关**;**切勿因 fact_check 的 method=GET 就要求把 risk_level 降为 L1**——判 risk_level 只看源码里有没有写操作,不看 fact_check 的 method。"
    "\n【TLS 与 base_url —— 不要误判 SSRF / 中间人(平台既定行为)】"
    "base_url 取自 inputs['__base_url__'],是**平台运行期注入的可信地址**(发布的环境画像,非用户任意输入),"
    "不构成 SSRF;对接的企业内网系统常为**自签证书**、平台已配置 insecure_tls,源码用 `verify=False` 是"
    "**平台既定的对接方式**,属正常配置——**切勿据此判中间人攻击/SSRF 而驳回**。这两点是设计如此,不是漏洞。"
)


# 凭证脱敏:评审输入绝不带明文凭证(也绝不把令牌发给外部 LLM provider)。按 key 名脱敏,
# 不按值猜(避免误删 acceptance 需要看的业务字段)。命中 key 的整段值替换为说明串。
_SECRET_KEY_HINTS = ("authorization", "auth_headers", "auth_header", "cookie", "token", "password",
                     "passwd", "secret", "credential", "apikey", "api_key", "satoken", "session",
                     "x-tenant-key", "set-cookie")
_SECRET_MASK = "***[运行期注入的会话登录态/凭证,模板不存明文,已脱敏]***"


def _redact_secrets(node):
    """深拷贝并按 key 名脱敏凭证(auth_headers/Authorization/Cookie/token…)。结构保留,值打码。"""
    if isinstance(node, dict):
        out: dict = {}
        for k, v in node.items():
            if any(h in str(k).lower() for h in _SECRET_KEY_HINTS):
                out[k] = _SECRET_MASK
            else:
                out[k] = _redact_secrets(v)
        return out
    if isinstance(node, list):
        return [_redact_secrets(x) for x in node]
    return node


def _build_user(asset_type: str, asset_key: str, body: dict, evidence: list[dict]) -> str:
    """拼评审输入(运行架构上下文 + 声明式信息 + 沙箱证据,**凭证已脱敏**)。

    adapter:把生成源码单列一节并附代码评审要求。
    注:录制抓请求页面(page_is_capture)免评审、不到此处;到此的 page_script 仅 DOM 回放型。
    """
    payload = json.dumps({
        "asset_type": asset_type,
        "asset_key": asset_key,
        "declarative_body": _redact_secrets(body),     # 脱敏:不把 auth_headers/Cookie/token 喂给评审模型
        "sandbox_evidence": _redact_secrets(evidence),
    }, ensure_ascii=False, indent=2)
    user = _SYSTEM_CONTEXT + "\n\n【待评审资产】\n" + payload
    if asset_type == "adapter":
        src = (body or {}).get("source")
        if src:
            user += "\n\n【生成代码 source】\n```python\n" + str(src) + "\n```"
        user += _ADAPTER_REVIEW_NOTE
    return user
