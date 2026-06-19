"""v2-M1 证据采集(只读):为"理解流程"汇集一份结构化证据,喂给后续画像/LLM 拆解。

不止靠 swagger——除静态解析外,还做**只读真探针**(表单结构 / 样例 GET 返回结构),补足
swagger 给不了的运行时语义(出参真路径、表单真字段)。绝不写、不臆造、凭证不进证据。

探针经注入的 `probe(path)->json` 完成(网关注入真 GET;测试注入假实现),本模块只管"采什么"。
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

import structlog
from pydantic import BaseModel, Field

from dano.capabilities import doc_parser, endpoint_classifier, oa_templates

log = structlog.get_logger(__name__)

# 只读探针:给一个 GET 路径,返回解析后的 JSON(失败/不可用返回 None)。
ProbeFn = Callable[[str], Awaitable[dict | None]]


def make_http_probe(base_url: str, token: str, *, quota: int = 6) -> ProbeFn:
    """造一个**只读 GET** 探针:只 GET、配额上限、超时、空 token 不发头。理解阶段绝不写。"""
    import httpx

    from dano.infra.http import tls_verify
    base = base_url.rstrip("/")
    tok = (token or "").strip()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    state = {"n": 0}

    async def probe(path: str) -> dict | None:
        if state["n"] >= quota:
            return None
        state["n"] += 1
        url = path if path.startswith("http") else base + (path if path.startswith("/") else "/" + path)
        async with httpx.AsyncClient(timeout=20, verify=tls_verify()) as c:
            r = await c.get(url, headers=headers)
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return None
    return probe

_MAX_SAMPLE_READS = 3          # 样例 GET 探针数量上限(只读,仍受配额约束)
_MAX_OUTPUT_PATHS = 40        # 出参结构摘要的路径条数上限
_MAX_DEPTH = 4                # 结构摘要递归深度上限


class ActionEvidence(BaseModel):
    name: str
    method: str
    endpoint: str
    tags: list[str] = Field(default_factory=list)
    summary: str = ""
    required: list[str] = Field(default_factory=list)
    params_in: list[str] = Field(default_factory=list)
    params_out: list[str] = Field(default_factory=list)   # 数据串联信号:谁的出参喂谁的入参
    request_example: Any = None      # 请求体示例 / 嵌套结构骨架:揭示 form/save 这类**双层嵌套**契约


class FormFieldEvidence(BaseModel):
    key: str
    label: str = ""
    type: str = ""


class SampleRead(BaseModel):
    endpoint: str
    output_paths: list[str] = Field(default_factory=list)  # 真实出参结构摘要(只路径+类型,无值)


class TemplateInfo(BaseModel):
    name: str
    success_rule: str | None = None


class FlowEvidence(BaseModel):
    """一条/一组流程的结构化证据;静态恒有,运行时部分按探针可用性尽力补。"""

    template: TemplateInfo | None = None
    actions: list[ActionEvidence] = Field(default_factory=list)
    form_fields: list[FormFieldEvidence] = Field(default_factory=list)
    sample_reads: list[SampleRead] = Field(default_factory=list)
    probes: list[str] = Field(default_factory=list)        # 审计:实际探了哪些只读路径


def _walk_form_fields(node: object, out: list[FormFieldEvidence]) -> None:
    """递归遍历表单设计器结构,凡带字段模型(__vModel__/vModel)的控件收为一个字段。"""
    if isinstance(node, dict):
        vm = node.get("__vModel__") or node.get("vModel")
        if isinstance(vm, str) and vm:
            cfg = node.get("__config__") if isinstance(node.get("__config__"), dict) else {}
            out.append(FormFieldEvidence(key=vm, label=str(cfg.get("label") or node.get("label") or vm),
                                         type=str(cfg.get("tag") or node.get("tag") or "")))
        for v in node.values():
            _walk_form_fields(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_form_fields(v, out)


def _extract_form_fields(form_info: dict | None) -> list[FormFieldEvidence]:
    """从 /biz/form/info 返回里抽表单字段;data.formData 是 JSON 串,内含表单设计器结构。"""
    if not isinstance(form_info, dict) or form_info.get("code") not in (None, 200, 0):
        return []
    data = form_info.get("data") if isinstance(form_info.get("data"), dict) else {}
    raw = data.get("formData")
    conf: object = raw
    if isinstance(raw, str):
        try:
            conf = json.loads(raw)
        except Exception:  # noqa: BLE001
            conf = {}
    schema = conf.get("formData") if isinstance(conf, dict) and "formData" in conf else conf
    fields: list[FormFieldEvidence] = []
    _walk_form_fields(schema, fields)
    seen: set[str] = set()
    return [f for f in fields if not (f.key in seen or seen.add(f.key))]


def _output_paths(node: object, prefix: str = "", out: list[str] | None = None, depth: int = 0) -> list[str]:
    """JSON 结构摘要:收集到叶子的点路径 + 类型(无值),供识别出参可串联点(如 data.taskId:str)。"""
    out = out if out is not None else []
    if len(out) >= _MAX_OUTPUT_PATHS or depth > _MAX_DEPTH:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            _output_paths(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
    elif isinstance(node, list):
        if node:
            _output_paths(node[0], f"{prefix}[]", out, depth + 1)
    else:
        out.append(f"{prefix}:{type(node).__name__}")
    return out


def _op_for(spec: dict, endpoint: str, method: str) -> dict | None:
    """按 path+method 在 spec 里定位 operation 对象。"""
    ops = (spec.get("paths") or {}).get(endpoint)
    op = ops.get((method or "get").lower()) if isinstance(ops, dict) else None
    return op if isinstance(op, dict) else None


def _schema_skeleton(spec: dict, schema: object, depth: int = 0) -> object:
    """请求 schema → 「键→类型/嵌套」骨架(解析 $ref,揭示嵌套形状,无值)。"""
    if depth > 5:
        return "..."
    s = doc_parser._resolve_ref(spec, schema or {})
    if not isinstance(s, dict):
        return "any"
    if s.get("properties"):
        return {k: _schema_skeleton(spec, v, depth + 1) for k, v in s["properties"].items()}
    if s.get("type") == "array":
        return [_schema_skeleton(spec, s.get("items") or {}, depth + 1)]
    return s.get("type") or "any"


def _request_example(spec: dict, op: dict | None) -> Any:
    """取请求体示例(优先 example/examples,含真实嵌套占位值),无则用请求 schema 骨架兜底。"""
    if not op:
        return None
    body = (op.get("requestBody") or {}).get("content", {}).get("application/json", {})
    if not isinstance(body, dict):
        return None
    if body.get("example") is not None:
        return body["example"]
    for v in (body.get("examples") or {}).values():
        if isinstance(v, dict) and "value" in v:
            return v["value"]
    if body.get("schema"):
        return _schema_skeleton(spec, body["schema"])
    return None


async def collect_evidence(spec: dict, *, include_tags: list[str] | None = None,
                           template_id: str = "", probe: ProbeFn | None = None,
                           convention: dict | None = None,
                           include_names: set[str] | None = None) -> FlowEvidence:
    """采集流程证据。静态部分(swagger+模板)恒有;给了 probe 才做只读真探针。

    probe 为 None(或无凭证)= 纯静态,可离线/测试;给了 probe = 额外补表单字段 + 样例出参结构。
    convention(LLM 识别的 {name, success_rule})给定则优先,否则回退确定性 match_template。
    include_names 给定则只保留这些动作名(把证据收窄到本流程相关端点,防 planner prompt 过大超时)。
    """
    tags = set(include_tags or [])
    template = oa_templates.match_template(spec)
    extra = template.infrastructure_patterns() if template else ()
    actions = [a for a in doc_parser.parse_openapi(spec)
               if endpoint_classifier.classify(a, extra_infra=extra) != endpoint_classifier.INFRASTRUCTURE]
    in_scope = [a for a in actions
                if (not tags or (set(a.tags) & tags)) and (not include_names or a.name in include_names)]

    # 模板/成功约定:LLM 识别优先,回退确定性 match_template(任一非空即给出 TemplateInfo)
    t_name = (convention or {}).get("name") or (template.name if template else None)
    t_rule = (convention or {}).get("success_rule") or (template.success_rule() if template else None)
    ev = FlowEvidence(
        template=TemplateInfo(name=t_name, success_rule=t_rule) if (t_name or t_rule) else None,
        actions=[ActionEvidence(name=a.name, method=(a.method or "GET").upper(), endpoint=a.endpoint,
                                tags=list(a.tags), summary=a.summary or "", required=list(a.required_in),
                                params_in=list(a.params_in), params_out=list(a.params_out),
                                request_example=_request_example(spec, _op_for(spec, a.endpoint, a.method)))
                 for a in in_scope],
    )
    if probe is None:
        return ev

    # 只读真探针(尽力而为,任何失败都不影响已采集的静态证据)
    if template_id:
        path = f"/biz/form/info?businessId=&templateId={template_id}"
        try:
            ev.form_fields = _extract_form_fields(await probe(path))
            ev.probes.append(path)
        except Exception as e:  # noqa: BLE001
            log.info("evidence.probe_form_failed", error=str(e))
    for a in [x for x in in_scope if (x.method or "GET").upper() == "GET"][:_MAX_SAMPLE_READS]:
        try:
            data = await probe(a.endpoint)
            if isinstance(data, (dict, list)):
                ev.sample_reads.append(SampleRead(endpoint=a.endpoint, output_paths=_output_paths(data)))
                ev.probes.append(a.endpoint)
        except Exception as e:  # noqa: BLE001
            log.info("evidence.probe_read_failed", endpoint=a.endpoint, error=str(e))
    return ev
