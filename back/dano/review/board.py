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

# 三审 system prompt:各看一维,逐项清单核对 + 每条理由点名依据(防橡皮图章泛泛而谈),统一输出
# JSON {passed, reasons}。**判定纪律**:通过 = 逐项核对且证据支持,不是"没看出问题"就放行;但也要尊重
# 用户消息里【运行架构】列出的"设计如此"项,不要把它们误判成问题(否则空烧生成预算)。
_OUT = "输出 JSON 对象:{\"passed\": true/false, \"reasons\": [\"每条都点名所依据的字段/端点/源码行\"]}。"
_DISCIPLINE = ("判定纪律:**通过 = 你逐项核对过清单且证据支持**,而非『没看出问题』就放行;"
               "每条 reason 必须落到**具体**字段/端点/源码行,禁止泛泛而谈。"
               "同时:用户消息【运行架构】里标注『设计如此』的项不要误判成问题。")
_ROLE_SYSTEM: dict[str, str] = {
    "acceptance": (
        "你是『成果验收』评审员,只判:这份自动生成的 API 接入资产是否**真满足业务意图**。逐项核对:\n"
        "1) 动作语义与字段映射是否对得上业务(看 asset_key、参数/field_bindings、adapter 看 source);\n"
        "2) 成败判定是否**有意义**:success_rule/断言能否真区分业务成功与失败,而非只校验 HTTP 200;\n"
        "3) 沙箱证据(sandbox_evidence)是否自洽、确实跑通了本业务动作;\n"
        "4) 必填项是否齐全。\n" + _DISCIPLINE + _OUT
    ),
    "security": (
        "你是『漏洞检测』评审员,只从安全维度逐行审(adapter 必须逐行审 source)。逐项核对:\n"
        "1) 危险调用:eval/exec/os.system/subprocess shell;\n"
        "2) 注入:外部输入是否未经处理拼进 URL/SQL/shell;\n"
        "3) 鉴权是否缺失或过宽、写操作是否缺幂等键;\n"
        "4) 硬编码密钥/令牌(源码必须零凭证,凭证应从 creds 运行期注入);\n"
        "5) 是否暴露 PII/敏感字段。\n"
        "发现任一**具体**高危即不通过,reason 点名位置(如 `source 第N行`/字段名)。"
        "fail-closed:对你**发现的可疑高危项**,若证据不足以排除,按不通过处理并说明缺什么证据"
        "(注意:这只针对你看出的可疑点,不是没问题也拒)。\n"
        "**设计如此、切勿误判为漏洞**:body 不含明文 token、base_url 为平台注入的可信地址、"
        "企业内网自签证书下 verify=False、fact_check 的 method=GET——详见用户消息【运行架构】/【代码评审要求】。\n"
        + _DISCIPLINE + _OUT
    ),
    "compliance": (
        "你是『合规审核』评审员,只审合规与发布纪律。逐项核对:\n"
        "1) 验证证据是否**全部** environment=sandbox 且 credential_type=test(严禁生产/真凭证);\n"
        "2) 风险分级 risk_level 是否与动作匹配(GET 只读=L1;写=L3 需确认);\n"
        "3) 写/删操作是否要求确认(confirm,由 risk_level 运行期强制);\n"
        "4) 是否指向生产端点、是否违反最小权限。\n"
        "fail-closed(发布红线):第 1 项**证据未能确认全为 sandbox+test 就判不通过**,不得默认放行。\n"
        "**设计如此、不要误判**:confirm 不在 body 字段、单步 field_bindings 为空、fact_check 的 method=GET"
        "——详见用户消息【运行架构】。\n" + _DISCIPLINE + _OUT
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
        elif len(set(models.values())) < 3:           # 配成同一个不报错,但提示盲点相关风险
            log.warning("review.models.not_distinct", models=models,
                        note="三审建议用不同模型,避免三审共享盲点(相关失效削弱硬闸门)")
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


# 录制抓请求页面的审核要求:结构已被 self_check 确定性验过 → 三模型**只判语义**,**拿 Goal 当业务方案对照**,
# 不重判结构/必填/鉴权脱敏(这正是当年评审对录制资产抖动误判的根因)。
_CAPTURE_REVIEW_NOTE = (
    "\n\n【录制抓请求审核要求(本资产=用户真人在页面上**亲手提交过**的写请求,已参数化)】\n"
    "**结构正确性(参数能否替换 / 身份能否覆盖 / 多步串联)已由确定性 self_check 验过**"
    "(见 sandbox_evidence 里 kind=self_check、violations=[]);副作用已由 fact_check 回查。"
    "**你只在语义层判,严禁重判结构 / 必填齐全 / 鉴权脱敏**(那些不归你、且会误判)。三维各判:\n"
    "1) 成果验收(**业务逻辑**):**拿 declarative_body.api_request.goal 当业务方案逐项对照** —— 这个 skill"
    "(action + 参数 + identity)真能实现 `goal.intent` 吗?`goal.required_inputs` 都在参数里吗?"
    "`goal.success_criteria` 与它实际校验/回查的一致吗?**对不上才否决并点名**。\n"
    "2) 漏洞检测:是否触碰 `goal.forbidden_actions`(删除/驳回/代他人审批)、越权、凭证泄漏 —— 确有则否决。\n"
    "3) 合规审核:是否指向生产、是否违反最小权限 —— 确有问题才否决。\n"
    "**只在确有依据时否决,reason 点名具体参数/字段/goal 项;没问题就 passed=true。**"
)


def _build_user(asset_type: str, asset_key: str, body: dict, evidence: list[dict]) -> str:
    """拼评审输入(运行架构上下文 + 声明式信息 + 沙箱证据,**凭证已脱敏**)。

    adapter:把生成源码单列一节并附代码评审要求;录制抓请求页面:附 capture 审核要求(结构已验、只判语义)。
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
    elif asset_type == "page_script" and (body or {}).get("api_request") and not (body or {}).get("actions"):
        user += _CAPTURE_REVIEW_NOTE   # 录制抓请求:三模型只判语义、拿 Goal 当业务方案对照
    return user


# ─────────── P3:录制 skill 的**非阻断**语义顾问(LLM 只提议,不当结构闸门) ───────────
_CAPTURE_ADVISORY_SYSTEM = (
    "你是录制型 API Skill 的**语义顾问**(非硬闸门、不阻断发布)。这份 skill 的**结构正确性"
    "(参数能否替换、身份能否覆盖、多步能否串联)已由确定性 self_check 验过 —— 不归你管、也不要重判**。"
    "你只从**语义**给**建议**,逐项看:\n"
    "1) 参数名是否人类可读、表意(像内部机器标识 Activity_xxx / hash / 纯数字随机码 → 建议起人话名);\n"
    "2) 动作是否疑似越权/危险(删除、驳回、代他人审批),与「用户提交自己的单据」不符;\n"
    "3) method/path 是否疑似指向生产而非测试环境;\n"
    "4) 申请人/当前用户类字段是否**应**标 identity(否则会冻结成录制者)。\n"
    "只就**确有依据**的点提建议、点名具体参数/字段;没问题就给空数组。"
    "输出 JSON 对象:{\"notes\": [\"每条点名所依据的参数/字段/端点\"]}。"
)


async def advisory_capture_review(client: "ChatClient | None", model: str | None, *,
                                  action: str, api_request: dict,
                                  self_check_passed: bool = True) -> list[str]:
    """录制 skill 的**非阻断**语义顾问:只给命名/越权/生产/身份建议,**不判发布**。

    只喂**非敏感元数据**(动作名 + 参数名/类型 + identity 路径 + method/path),**绝不带 body 值/凭证/登录态**。
    未配置 client/model 或调用失败 → 返回 [](顾问失败绝不阻断发布,安全降级)。"""
    if client is None or not model:
        return []
    payload = json.dumps({
        "action": action,
        "params": api_request.get("params"),
        "field_types": api_request.get("field_types"),
        "identity_fields": [i.get("path") for i in (api_request.get("identity") or [])],
        "method": api_request.get("method"), "path": api_request.get("path"),
        "structure_verified_by_self_check": self_check_passed,
    }, ensure_ascii=False)
    try:
        out = await client.complete_json(model=model, system=_CAPTURE_ADVISORY_SYSTEM,
                                         user="【待评 skill 元数据】\n" + payload, timeout_s=30.0)
    except Exception:  # noqa: BLE001 —— 顾问性质,失败不阻断发布
        log.warning("advisory_capture_review.failed", action=action)
        return []
    notes = out.get("notes") if isinstance(out, dict) else None
    if not isinstance(notes, list):
        return []
    return [str(n) for n in notes if str(n).strip()]


# ─────────── P3:LLM 业务 Goal 提炼(只提议,程序校验 + 用户确认才作数) ───────────
_GOAL_SYSTEM = (
    "你是 API 自动化的**业务目标提炼器**。根据一条录制下来的写操作(只给元数据,无 body 值/凭证),"
    "提炼**结构化业务 Goal**。原则:你只**提议**,所有结论后续由程序校验 + 用户确认;"
    "**严禁编造**没有依据的字段/步骤——required_inputs 只能从给定 params 里选。\n"
    "输出 JSON 对象:{\n"
    "  \"intent\": \"一句话业务意图(如:创建并提交采购申请)\",\n"
    "  \"business_type\": \"业务类型(purchase/leave/reimburse/...)\",\n"
    "  \"required_inputs\": [\"必须由调用者提供的参数名,**只能取自给定 params**\"],\n"
    "  \"success_criteria\": [\"可验证的成功标准(如:单据已创建、审批流程已发起)\"],\n"
    "  \"forbidden_actions\": [\"该 Skill 绝不应做的危险动作(删除/驳回/代他人审批/终止流程)\"],\n"
    "  \"risk_level\": \"L1(只读)或 L3(写)\"\n}"
)


async def generate_goal(client: "ChatClient | None", model: str | None, *,
                        action: str, api_request: dict) -> dict:
    """LLM 提炼业务 Goal(**提议**,非定论)。只喂非敏感元数据;未配置/失败 → {}。"""
    if client is None or not model:
        return {}
    steps = api_request.get("steps")
    last = (steps[-1] if steps else {}) or {}
    meta = {
        "action": action, "method": api_request.get("method"), "path": api_request.get("path"),
        "step_count": len(steps) if steps else 1,
        "params": api_request.get("params") or last.get("params"),
        "field_types": api_request.get("field_types") or last.get("field_types"),
        "identity_fields": [i.get("path") for i in (api_request.get("identity") or [])],
        "has_success_rule": bool(api_request.get("success_rule")),
        "has_fact_check": bool(api_request.get("fact_check")),
    }
    try:
        out = await client.complete_json(model=model, system=_GOAL_SYSTEM,
                                         user="【录制写操作元数据】\n" + json.dumps(meta, ensure_ascii=False),
                                         timeout_s=30.0)
    except Exception:  # noqa: BLE001 —— 提炼失败不阻断(无 Goal 仍可走原流程)
        log.warning("generate_goal.failed", action=action)
        return {}
    return out if isinstance(out, dict) else {}


# ─────────── P3:LLM 字段语义增强(只为确定性命名没把握的字段补名,有把握的不覆盖) ───────────
_FIELD_NAME_SYSTEM = (
    "你是表单字段**命名助手**。给定一组**机器字段名**(英文 key / 路径,**无值**),为每个起一个**简短中文业务名**。"
    "原则:只对**能合理推断**的起名(applicantId→申请人、leaveType→请假类型、processDefKey→流程标识);"
    "**像随机码/无意义标识**(Activity_09dlq0g、hash、纯数字)无法推断 → **省略该项**(绝不瞎编)。"
    "输出 JSON 对象:{\"names\": {\"原始key\": \"中文名\"}}(只含你有把握的项)。"
)


async def suggest_field_names_llm(client: "ChatClient | None", model: str | None, *,
                                  action: str, fields: list[dict]) -> dict:
    """为**确定性命名没把握**的字段(suggest_name==key)提议中文名。只喂 key/type/path(**无值**)。失败/无项 → {}。"""
    if client is None or not model:
        return {}
    need = [{"key": f.get("key"), "type": f.get("type"), "path": f.get("path")}
            for f in (fields or []) if f.get("suggest_name") == f.get("key")]
    if not need:
        return {}
    payload = json.dumps({"action": action, "fields": need}, ensure_ascii=False)
    try:
        out = await client.complete_json(model=model, system=_FIELD_NAME_SYSTEM,
                                         user="【待命名字段(仅机器名,无值)】\n" + payload, timeout_s=30.0)
    except Exception:  # noqa: BLE001 —— 命名增强失败不影响录制(退回确定性 key 名)
        log.warning("suggest_field_names_llm.failed", action=action)
        return {}
    names = out.get("names") if isinstance(out, dict) else None
    return {str(k): str(v) for k, v in names.items() if str(v).strip()} if isinstance(names, dict) else {}
