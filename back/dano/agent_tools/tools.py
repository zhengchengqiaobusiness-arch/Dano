"""pi 自定义工具的 Python 实现(确定性能力)。

红线:
- sandbox_test/write_readback/health_check 一律 environment=sandbox + credential_type=test,绝不碰生产写。
- publish_asset 走 Phase 1 的 verify_publishable 硬关卡:只认后端生成的证据,不信 agent 自报。
凭证只在进程内(materials),绝不进 LLM 上下文。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog

from dano.agent_tools import materials
from dano.assets.drafts import REVIEW_REQUIRED_TYPES, DraftStore, page_is_capture, page_is_write
from dano.assets.repository import AssetRepository
from dano.capabilities import doc_parser, endpoint_classifier, fingerprint, oa_templates
from dano.execution.connectors.auth import AuthManager
from dano.execution.connectors.executor import SystemEndpoint, system_key_for
from dano.capabilities.sandbox import RealSandbox
from dano.schemas import validate_asset_body
from dano.shared.asset_bodies import AuthConfig, PageScriptBody
from dano.shared.enums import AssetType, Outcome, Subsystem, ValidationStatus
from dano.shared.models import AssetEnvelope, Scope

log = structlog.get_logger(__name__)
_ds = DraftStore()
_repo = AssetRepository()
_review_board = None      # 可注入(测试用 fake);None 时按配置从环境构造真实三模型评审
_fix_proposer = None      # 修复器:propose(api_request, findings, goal)->ops。可注入(测试 fake);None 时用 board.client 走 generate_fix_ops


def set_review_board(board) -> None:  # noqa: ANN001 —— 测试注入 fake 评审委员会
    global _review_board
    _review_board = board


def set_fix_proposer(fn) -> None:  # noqa: ANN001 —— 测试注入 fake 修复器(出修复操作)
    global _fix_proposer
    _fix_proposer = fn


_adapter_caller_factory = None    # 可注入(测试用 fake);None 时按 materials 构造真实 httpx 调用


def set_adapter_caller(factory) -> None:  # noqa: ANN001 —— 测试注入 fake 事实核查调用器
    global _adapter_caller_factory
    _adapter_caller_factory = factory


def _adapter_caller(mat):  # noqa: ANN001 —— 返回 fact_check 用的 call(method, path, body)->(http, json)
    if _adapter_caller_factory is not None:
        return _adapter_caller_factory(mat)
    import httpx
    base = (mat.deploy or {}).get("base_url", "").rstrip("/")
    token = (mat.credentials or {}).get("token", "")

    async def call(method: str, path: str, body=None):  # noqa: ANN001
        from dano.infra.http import tls_verify
        async with httpx.AsyncClient(timeout=30, verify=tls_verify()) as c:
            headers = {"Authorization": f"Bearer {token.strip()}"} if (token or "").strip() else {}
            if method.upper() == "GET":
                r = await c.get(base + path, headers=headers)
            else:
                r = await c.request(method, base + path, json=body, headers=headers)
        try:
            return r.status_code, r.json()
        except Exception:  # noqa: BLE001
            return r.status_code, {"raw": r.text}

    return call


class ToolError(ValueError):
    """工具入参/状态错误(回给 pi)。"""


def _mat(run_id: str, system_instance_id: str) -> materials.MaterialContext:
    m = materials.get(run_id, system_instance_id)
    if m is None:
        raise ToolError(f"未登记材料: run={run_id} system={system_instance_id}")
    return m


# ── 侦察:解析接口,智能抽离(过滤基础设施 + 模板识别)──
async def parse_spec(run_id: str, params: dict) -> dict:
    """抽业务动作清单。枚举走确定性(完整、不丢接口);**业务/基础设施识别 + 业务分组**
    在 use_llm_classify=True 时交给 LLM 语义判断(泛化不同企业命名),失败/未启用回退关键词分类。
    """
    sid = params["system_instance_id"]
    mat = _mat(run_id, sid)
    spec = mat.openapi or {}
    template = oa_templates.match_template(spec)              # 仅作确定性兜底(框架名/成败规则/infra 关键词)
    extra = template.infrastructure_patterns() if template else ()
    template_name = template.name if template else None
    success_rule = template.success_rule() if template else None
    include = {t for t in (params.get("include_tags") or mat.include_tags or [])}
    all_actions = doc_parser.parse_openapi(spec)              # 确定性枚举:完整、grounded
    # LLM 语义识别(可选):对已枚举清单逐个判 role + 业务 category;另据真实响应判成功约定;失败回退确定性
    llm_map: dict = {}
    if params.get("use_llm_classify") and all_actions:
        from functools import partial

        from dano.generation.coder import openai_text_spawn
        try:
            from dano.capabilities.llm_classifier import classify_actions
            llm_map = await classify_actions(
                all_actions, spawn=partial(openai_text_spawn, tag="classify", json_mode=True))
        except Exception as e:  # noqa: BLE001 - 识别失败不阻断接入,整体回退确定性
            log.warning("parse_spec.llm_classify_failed", error=str(e))
        try:                                                  # 框架/成功约定:LLM 读真实响应 → 取代关键词硬匹配
            from dano.capabilities.llm_template import detect_convention
            conv = await detect_convention(
                spec, spawn=partial(openai_text_spawn, tag="convention", json_mode=True))
            if conv:
                template_name = conv.get("name") or template_name
                success_rule = conv.get("success_rule") or success_rule
        except Exception as e:  # noqa: BLE001 - 约定识别失败回退确定性 match_template
            log.warning("parse_spec.llm_convention_failed", error=str(e))
    paths = spec.get("paths") or {}
    actions, categories = [], {}
    for a in all_actions:
        info = llm_map.get(a.name)                            # 命中 LLM → 用模型判断,否则确定性兜底
        role = info["role"] if info else endpoint_classifier.classify(a, extra_infra=extra)
        category = info.get("category", "") if info else ""
        if role == endpoint_classifier.INFRASTRUCTURE:
            continue
        groups = [category] if category else (a.tags or ["(未分类)"])  # LLM 业务分组优先,否则按 tag
        for t in groups:                                      # 类别统计(供前端选)
            categories[t] = categories.get(t, 0) + 1
        if include and not (set(a.tags) & include):           # 类别白名单:超大 swagger 圈定范围
            continue
        # x-flow 业务规则(若文档写了):审批链/校验/驳回/记账 → 供生成剧本的前置/错误/确认段。没有就空。
        op = (paths.get(a.endpoint) or {}).get((a.method or "").lower(), {})
        business_meta = op.get("x-flow") if isinstance(op, dict) and isinstance(op.get("x-flow"), dict) else {}
        actions.append({"name": a.name, "method": a.method, "endpoint": a.endpoint,
                        "role": role, "category": category,   # category:LLM 识别的业务分组(可空)
                        "required_in": a.required_in, "params_in": a.params_in,
                        "params_out": a.params_out, "tags": a.tags,   # 出参/标签:供发现流程依赖
                        "summary": a.summary, "field_docs": a.field_docs,
                        "business_meta": business_meta})      # x-flow → 业务规则(可空)
    return {"system_instance_id": sid, "template": template_name,
            "success_rule": success_rule,
            "categories": categories, "include_tags": sorted(include),
            "count": len(actions), "actions": actions}


# ── 打源指纹 ──
async def fingerprint_materials(run_id: str, params: dict) -> dict:
    mat = _mat(run_id, params["system_instance_id"])
    mats = [m for m in ({"kind": "openapi", "content": mat.openapi},
                        {"kind": "deploy_info", "content": mat.deploy}) if m["content"]]
    return {"source_fingerprint": fingerprint.fingerprint_materials(mats)}


# ── 存草案(schema 校验后入库,未发布)──
async def save_draft(run_id: str, params: dict) -> dict:
    sid = params["system_instance_id"]
    mat = _mat(run_id, sid)
    asset_type = AssetType(params["asset_type"])
    body = params["body"]
    validate_asset_body(asset_type, body)            # 结构硬校验,垃圾拒
    scope = Scope(tenant=mat.tenant, subsystem=mat.subsystem)  # type: ignore[arg-type]
    draft = await _ds.save_draft(run_id=run_id, scope=scope, asset_type=asset_type,
                                 asset_key=params["asset_key"], body=body)
    return {"asset_draft_id": str(draft.asset_draft_id), "content_hash": draft.content_hash}


def _real_sandbox(mat: materials.MaterialContext) -> RealSandbox:
    deploy = mat.deploy or {}
    base_url = deploy.get("base_url")
    if not base_url:
        raise ToolError(f"{mat.system_instance_id} 缺 base_url,无法沙箱验证")
    from dano.shared.enums import Subsystem
    sub = Subsystem(mat.subsystem)
    return RealSandbox(
        system_key=system_key_for(sub),
        endpoint=SystemEndpoint(base_url=base_url, auth=AuthConfig.model_validate(deploy.get("auth", {}))),
        test_credentials=mat.credentials, auth_manager=AuthManager(),
    )


# ── 看一个动作的请求/响应结构(含嵌套,供发现流程时构造 io 映射)──
def _resolve_tree(spec: dict, node, _depth=0):  # noqa: ANN001
    """递归解析 $ref,返回 schema 树(供 pi 看清 flowTask.taskId 这类嵌套)。"""
    from dano.capabilities.doc_parser import _resolve_ref
    if _depth > 6 or not isinstance(node, dict):
        return node
    node = _resolve_ref(spec, node)
    if not isinstance(node, dict):
        return node
    out: dict = {}
    if "properties" in node:
        out["properties"] = {k: _resolve_tree(spec, v, _depth + 1)
                             for k, v in node["properties"].items()}
        if node.get("required"):
            out["required"] = node["required"]
    elif "type" in node:
        out["type"] = node["type"]
        if node.get("description"):
            out["description"] = node["description"]
    return out


async def get_action_schema(run_id: str, params: dict) -> dict:
    sid = params["system_instance_id"]
    action_name = params["action"]
    spec = (_mat(run_id, sid).openapi or {})
    # 用与 parse_spec **完全相同**的命名(operationId 或 method_path 切片)定位动作 → 取 endpoint/method。
    # 之前只认 operationId,无 operationId 的 spec 一律找不到,pi 会反复猜名字直到超时。
    actions = doc_parser.parse_openapi(spec)
    action = next((a for a in actions if a.name == action_name), None)
    if action is None:
        raise ToolError(f"接口里无此动作: {action_name}(可用动作:{[a.name for a in actions]})")
    ops = (spec.get("paths") or {}).get(action.endpoint)
    op = ops.get((action.method or "post").lower()) if isinstance(ops, dict) else {}
    op = op if isinstance(op, dict) else {}
    req = (op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema"))
    resp = None
    for code, r in (op.get("responses", {}) or {}).items():
        if str(code).startswith("2") and isinstance(r, dict):
            resp = r.get("content", {}).get("application/json", {}).get("schema")
            break
    return {"action": action_name, "method": (action.method or "POST").upper(), "endpoint": action.endpoint,
            "request_schema": _resolve_tree(spec, req) if req else None,
            "response_schema": _resolve_tree(spec, resp) if resp else None,
            "request_example": _first_example(op)}


def _first_example(op: dict):  # noqa: ANN001
    body = op.get("requestBody", {}).get("content", {}).get("application/json", {})
    if "example" in body:
        return body["example"]
    exs = body.get("examples") or {}
    for v in exs.values():
        if isinstance(v, dict) and "value" in v:
            return v["value"]
    return None


# ── 建复合流程草案(goal 模式:pi 发现流程,给出步骤+io映射)──
def _workflow_template_id(spec: dict, body, tmpl) -> str:  # noqa: ANN001
    """本流程实际用的 templateId:全权委托方言定位(模板枚举/命名约定都在 dialect)。

    主流程零字面量:无方言(通用系统,无模板概念)→ ""。
    """
    if tmpl is None:
        return ""
    import json as _json
    body_json = _json.dumps(body.model_dump(), ensure_ascii=False, default=str)
    return tmpl.template_id_in(spec, body_json)


def _workflow_business_meta(spec: dict, tmpl, tid: str) -> dict:  # noqa: ANN001
    """复合流程的审批链业务规则:x-flow 优先,没写则按 templateId 从发起端点 description 兜底解析。

    解析不出 → {}(不臆造)。
    """
    if not isinstance(spec, dict) or tmpl is None:
        return {}
    paths = spec.get("paths") or {}
    for ep in (tmpl.submit_endpoints() or ()):                  # x-flow 优先
        xf = ((paths.get(ep) or {}).get("post") or {}).get("x-flow")
        if isinstance(xf, dict) and xf:
            return xf
    parse = getattr(tmpl, "parse_approval_chain", None)         # 兜底:散文解析
    return parse(spec, tid) if (callable(parse) and tid) else {}


def _norm_template_id(s: str) -> str:
    """归一 templateId:去掉 `_template` 后缀,使 'purchase' 与 'purchase_template' 等价匹配。"""
    s = (s or "").strip()
    return s[: -len("_template")] if s.endswith("_template") else s


def _walk_variant(spec: dict, root) -> dict:  # noqa: ANN001
    """walk 单个 submit 变体 schema → 叶子字段 {name:{type,description,path,required}}。

    `required` = 该叶子是否在其**直属对象**的 required 列表里(变量层字段的必填以变量对象为准)。
    """
    from dano.capabilities.doc_parser import _resolve_ref
    out: dict = {}

    def _walk(node, prefix="", depth=0):  # noqa: ANN001
        if depth > 6:
            return
        node = _resolve_ref(spec, node)
        if not isinstance(node, dict):
            return
        req = set(node.get("required") or [])
        for k, v in (node.get("properties") or {}).items():
            vr = _resolve_ref(spec, v)
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(vr, dict) and vr.get("properties"):
                _walk(vr, path, depth + 1)
            elif isinstance(vr, dict):
                info = {"path": path, "required": k in req}
                if vr.get("type"):
                    info["type"] = vr["type"]
                if vr.get("description"):
                    info["description"] = vr["description"]
                out[k] = info          # 同名叶子后写覆盖:取更深/更靠后的(变量层 > 顶层 title)
    _walk(root)
    return out


def _submit_leaf_fields(spec: dict, tmpl, tid: str) -> dict:  # noqa: ANN001
    """从提交端点请求体 schema 抽**叶子字段** {name:{type,description,path,required}}(递归 flowTask.variables)。

    oneOf 多模板时**只取本业务那一支**(Submit_<templateId>,容忍 tid 带/不带 `_template` 后缀);
    锁不定具体模板时**绝不跨模板并集**——并集会让 A 模板的字段语义串到 B 模板(如把销假模板的「销假说明」
    安到采购的 reason 上)。退而只保留所有变体中**完全一致**的字段:宁可少给描述,也绝不臆造错描述。
    """
    if not isinstance(spec, dict) or tmpl is None:
        return {}
    eps = tmpl.submit_endpoints() or ()
    if not eps:
        return {}
    op = ((spec.get("paths") or {}).get(eps[-1]) or {}).get("post") or {}
    schema = ((((op.get("requestBody") or {}).get("content") or {})
               .get("application/json") or {}).get("schema")) or {}
    variants = [v for v in (schema.get("oneOf") or [schema]) if isinstance(v, dict)]
    if not variants:
        return {}
    # ① 优先锁定本业务模板那一支(ref 名 Submit_<tid>,容忍 _template 后缀差异)
    want = _norm_template_id(tid)
    chosen = None
    if want:
        for v in variants:
            ref_name = str(v.get("$ref", "")).rsplit("/", 1)[-1]
            if ref_name.startswith("Submit_") and _norm_template_id(ref_name[len("Submit_"):]) == want:
                chosen = v
                break
    if chosen is not None:
        return _walk_variant(spec, chosen)
    if len(variants) == 1:
        return _walk_variant(spec, variants[0])
    # ② 锁不定本业务:只保留所有变体里**完全一致**的字段(避免跨模板串台);冲突字段宁缺毋错
    per = [_walk_variant(spec, v) for v in variants]
    out: dict = {}
    for k in set.intersection(*[set(d) for d in per]):
        infos = [d[k] for d in per]
        first = infos[0]
        if all(i.get("type") == first.get("type") and i.get("description") == first.get("description")
               and i.get("required") == first.get("required") for i in infos):
            out[k] = first
    return out


def _decompose_form_envelopes(steps, user_fields: list[str], leaves: dict) -> list[str]:  # noqa: ANN001
    """整表信封防泄漏:把用户字段里的**序列化信封**(formData 等)拆成提交 schema 的业务叶子,
    并把步骤里 `field:<信封>` 的映射重写成**逐叶子映射到其真实嵌套路径**——信封是一堆业务字段的
    序列化容器,目标系统提交体里根本没有它,暴露给调用方就是个填不进去的黑盒。

    能拆(有叶子)→ 信封换叶子 + 步骤重写;拆不出 → 仅把信封剔出用户字段(绝不暴露黑盒)。
    就地改 steps 的 inputs;返回新的 user_fields(纯函数语义,可离线单测)。
    """
    from dano.shared.std_fields import is_flow_internal, is_form_envelope
    envelopes = {f for f in user_fields if is_form_envelope(f)}
    if not envelopes:
        return user_fields
    leaf_names = [k for k in leaves if not is_flow_internal(k) and not is_form_envelope(k)]
    for s in steps:
        if (getattr(s, "kind", "call") or "call") != "call":
            continue
        hit = [t for t, src in s.inputs.items()
               if isinstance(src, str) and src.startswith("field:") and src[len("field:"):] in envelopes]
        for t in hit:
            del s.inputs[t]
        if hit:                                   # 信封步骤 → 逐叶子映射到真实嵌套点路径
            for ln in leaf_names:
                s.inputs[(leaves[ln].get("path") or ln)] = f"field:{ln}"
    return sorted((set(user_fields) - envelopes) | set(leaf_names))


def _field_mappings(leaves: dict, user_fields: list[str], submit_ep: str, tid: str) -> list[dict]:
    """据 submit schema 叶子,为每个用户字段建**可追溯映射**(§16):标准字段 → 目标点路径 + 类型 + 来源。

    纯函数:只为能在 submit schema 里找到来源的字段建映射(找不到的不臆造,留空由别处声明)。
    """
    ref_base = f"Submit_{tid}" if tid else "Submit"
    out: list[dict] = []
    for f in user_fields:
        info = leaves.get(f)
        if not info:
            continue
        loc = info.get("path") or f
        out.append({
            "standard_field": f,
            "target_field": f,
            "target_location": loc,
            "target_type": info.get("type") or "string",
            "source": {"type": "openapi", "path": submit_ep, "schema_ref": f"{ref_base}.{loc}"},
        })
    return out


def _merge_field_types(user_fields: list[str], leaves: dict, form_types: dict, existing: dict) -> dict:
    """字段类型合并优先级(WS6):**真实动态表单(权威)> submit schema > 已有**。纯函数,可测。

    动态表单是字段类型的权威信源(el-input-number→number、el-select→enum…),压过 schema 与名字启发式。
    """
    ft = dict(existing)
    for f in user_fields:
        if form_types.get(f):
            ft[f] = form_types[f]
        elif not ft.get(f) and (leaves.get(f) or {}).get("type"):
            ft[f] = leaves[f]["type"]
    return ft


async def _probe_form_types(mat, tmpl, tid: str) -> dict:  # noqa: ANN001
    """探目标系统**真实动态表单** → {字段: json_type}(权威类型)。best-effort:无凭证/探不到 → {}。

    只读 GET(表单定义),不写;系统特定路径与解析都走 dialect(form_probe_path + parse_form_fields)。
    """
    if tmpl is None or not tid or mat is None:
        return {}
    base = (mat.deploy or {}).get("base_url", "")
    token = (mat.credentials or {}).get("token", "")
    path = tmpl.form_probe_path(tid)
    if not (base and path):
        return {}
    import httpx

    from dano.infra.http import tls_verify
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = base.rstrip("/") + (path if path.startswith("/") else "/" + path)
    try:
        async with httpx.AsyncClient(timeout=15, verify=tls_verify()) as c:
            r = await c.get(url, headers=headers)
        payload = r.json()
    except Exception:  # noqa: BLE001 - 探不到不阻断建流程
        return {}
    out: dict = {}
    for f in tmpl.parse_form_fields(payload):
        if f.get("key") and f.get("json_type"):
            out[f["key"]] = f["json_type"]
    return out


async def draft_workflow(run_id: str, params: dict) -> dict:
    from dano.capabilities import oa_templates
    from dano.generation.dsl_grounding import check_grounding, collect_field_refs
    from dano.shared.asset_bodies import Invariant, WorkflowSkillBody, WorkflowStep
    sid = params["system_instance_id"]
    mat = _mat(run_id, sid)
    scope = Scope(tenant=mat.tenant, subsystem=Subsystem(mat.subsystem))
    # DSL v2:支持 call/compute/branch/foreach/select + 前置/不变量(模型按 kind 强校验)
    try:
        steps = [WorkflowStep.model_validate(s) for s in params["steps"]]
        preconditions = [Invariant.model_validate(p) for p in params.get("preconditions", [])]
        invariants = [Invariant.model_validate(p) for p in params.get("invariants", [])]
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"流程节点结构非法: {e}") from e
    # 契约自洽:所有 field:X 引用并入 **user_fields**(防"用了却没声明",grounding 认 user_fields)。
    # 但**"被步骤引用 ≠ 必填"**:必填只认 pi 显式声明 + 提交 schema 标 required 的(下方按 leaves 收敛),
    # 绝不把所有引用字段强标必填(否则 spec 明明可选的字段也被拦成必填)。
    used = collect_field_refs(steps)
    user_fields = sorted(set(params.get("user_fields", [])) | used)
    required_fields = sorted(set(params.get("required_fields", [])) & set(user_fields))
    tmpl = oa_templates.match_template(mat.openapi or {})
    body = WorkflowSkillBody(
        action=params["action"], title=params.get("title", params["action"]),
        steps=steps, user_fields=user_fields, required_fields=required_fields,
        preconditions=preconditions, invariants=invariants, preview=bool(params.get("preview", False)),
        success_rule=params.get("success_rule") or (tmpl.success_rule() if tmpl else None),
    )
    # 信源直通(grounded:有据才写,无则空,绝不臆造,任何异常都不阻断建流程):
    # ① 审批链 business_meta(x-flow 优先,散文兜底)② 字段类型/描述从提交端点 schema 抽。
    try:
        spec = mat.openapi or {}
        tid = _workflow_template_id(spec, body, tmpl)
        bmeta = _workflow_business_meta(spec, tmpl, tid)
        if bmeta:
            body.business_meta = bmeta
            body.business = body.business or bmeta.get("flow", "")
        leaves = _submit_leaf_fields(spec, tmpl, tid)
        # 整表信封防泄漏:formData 这类序列化串绝不作用户参数 → 拆成提交 schema 业务叶子 + 重写步骤映射。
        body.user_fields = _decompose_form_envelopes(body.steps, body.user_fields, leaves)
        body.required_fields = sorted(set(body.required_fields) & set(body.user_fields))
        # 必填忠实于提交 schema:叶子标 required 的才必填(并集 pi 显式声明),最终 ⊆ user_fields。
        # 这样"被步骤引用但 schema 可选"的字段不再被强标必填(修"全字段标必填"缺陷)。
        if leaves:
            schema_req = {f for f in body.user_fields if (leaves.get(f) or {}).get("required")}
            body.required_fields = sorted((set(body.required_fields) | schema_req) & set(body.user_fields))
        # WS6:探目标系统真实动态表单 → 字段类型权威信源(best-effort,探不到=空,不阻断)
        form_types = await _probe_form_types(mat, tmpl, tid)
        if leaves or form_types:
            fd = dict(body.field_docs)
            for f in body.user_fields:
                info = leaves.get(f) or {}
                if info.get("description") and not fd.get(f):
                    fd[f] = info["description"]
            body.field_docs = fd
            # 类型合并优先级:真实表单(权威)> submit schema > 已有(名字启发式)
            body.field_types = _merge_field_types(body.user_fields, leaves, form_types, body.field_types)
            # §16 可追溯字段映射:标准字段 → 目标点路径 + 类型 + 来源 schema_ref(找不到来源的不臆造)
            if leaves:
                submit_ep = (tmpl.submit_endpoints()[-1] if tmpl and tmpl.submit_endpoints() else "")
                body.field_mappings = _field_mappings(leaves, body.user_fields, submit_ep, tid)
    except Exception:  # noqa: BLE001 - 兜底:解析失败不阻断建流程
        pass
    # 结构化 Goal(WS5):据材料确定性生成,挂到流程体;并作 grounding 锚——步骤不得命中禁止动作。
    step_actions = [s.action for s in steps if s.kind == "call" and s.action]
    try:
        from dano.onboarding.goal import build_goal, goal_grounding
        goal = build_goal(mat.openapi or {}, tmpl, template_id=tid,
                          business=body.business, title=body.title,
                          required_inputs=body.required_fields,
                          optional_inputs=[f for f in body.user_fields if f not in body.required_fields],
                          candidate_steps=step_actions, risk_level=body.risk_level.value,
                          requires_confirmation=bool(body.preview))
        body.goal = goal.model_dump()
        goal_issues = goal_grounding(goal, step_actions)
    except Exception:  # noqa: BLE001 - Goal 合成失败不阻断;但禁止步校验若已得出则仍生效
        goal_issues = []
    # grounding 硬关卡:动作必须已发布、表达式只准用已声明字段/变量+审计函数、来源必须可追溯。
    # ground 不住 → 拒绝并把问题回给 pi(绝不让臆造逻辑进库)。
    published = {e.body.get("action", e.asset_key)
                 for e in await _repo.list_published(AssetType.CONNECTOR, scope)}
    issues = check_grounding(body, published_actions=published) + goal_issues
    if issues:
        raise ToolError("流程未通过 grounding 校验(请修正后重试):\n- " + "\n- ".join(issues))
    validate_asset_body(AssetType.WORKFLOW, body.model_dump())
    draft = await _ds.save_draft(run_id=run_id, scope=scope, asset_type=AssetType.WORKFLOW,
                                 asset_key=body.action, body=body.model_dump())
    return {"asset_draft_id": str(draft.asset_draft_id), "action": body.action,
            "steps": [s.action for s in steps if s.kind == "call"]}


# ── 复合流程整条 dry-run(测试账号按序真跑,记 sandbox 证据)──
async def sandbox_test_workflow(run_id: str, params: dict) -> dict:
    """用测试账号把复合流程**多用例**经运行期**同一解释器** dry-run,并强制**分支覆盖**(每分支臂≥1 例)。

    cases:用例数组(每个=一组业务字段);兼容旧单个 test_input。**全用例 COMPLETED 且分支全覆盖**才 passed,
    据此才可发布(否则驳回)。记 kind='cases' 证据。与运行期同一引擎 → test == run。
    """
    from uuid import uuid4

    from dano.execution.connectors.executor import RealActionExecutor, SystemEndpoint, system_key_for
    from dano.generation.dsl_grounding import branch_ids, coverage_gaps
    from dano.orchestrator.orchestrator import Orchestrator
    from dano.orchestrator.skills import SkillRegistry
    from dano.orchestrator.types import Intent, SkillSpec
    from dano.shared.asset_bodies import AuthConfig, WorkflowSkillBody
    from dano.shared.enums import TaskState
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.WORKFLOW:
        raise ToolError("sandbox_test_workflow 仅用于复合流程草案")
    wf = WorkflowSkillBody.model_validate(draft.body)
    mat = _mat(run_id, draft.subsystem.value)
    sub = Subsystem(mat.subsystem)
    deploy = mat.deploy or {}
    endpoints = {system_key_for(sub): SystemEndpoint(
        base_url=deploy.get("base_url", ""), auth=AuthConfig.model_validate(deploy.get("auth", {})))}
    execu = RealActionExecutor(endpoints=endpoints, auth_manager=AuthManager())
    # 复用运行期同一编排器/解释器(store 取已发布步骤连接器;测试凭证经 resolve 直给)
    orch = Orchestrator(registry=SkillRegistry([]), store=_repo, harness=None,
                        action_executor=execu, resolve_credentials=lambda refs: mat.credentials)
    steps_dump = [s.model_dump() for s in wf.steps]
    skill = SkillSpec(
        skill_id=f"{mat.subsystem}.{wf.action}", subsystem=sub, action=wf.action,
        risk_level=wf.risk_level, is_workflow=True,
        workflow_steps=steps_dump, workflow_success_rule=wf.success_rule,
        workflow_preconditions=[i.model_dump() for i in wf.preconditions],
        workflow_invariants=[i.model_dump() for i in wf.invariants])
    cases = params.get("cases")
    if not cases:
        cases = [params["test_input"]] if params.get("test_input") is not None else [{}]
    static_ids = branch_ids(steps_dump)
    observed, results, ok_all = [], [], True
    for c in cases:
        out = await orch._run_workflow(uuid4(), mat.tenant, skill, Intent(action_hint=wf.action, fields=c))
        ok = out.state == TaskState.COMPLETED
        ok_all = ok_all and ok
        observed.append(out.audit.get("branches", []))
        results.append({"input": c, "state": out.state.value, "message": out.message})
    gaps = coverage_gaps(static_ids, observed)
    passed = ok_all and not gaps
    v = await _ds.record_validation(
        asset_draft_id=draft.asset_draft_id, kind="cases", passed=passed,
        evidence={"cases": results, "branch_ids": static_ids, "coverage_gaps": gaps})
    return {"passed": passed, "validation_run_ids": [str(v.validation_run_id)],
            "cases": results, "coverage_gaps": gaps}


# ── 建连接器草案(Python 确定性建体,pi 只给动作名)──
async def draft_connector(run_id: str, params: dict) -> dict:
    from dano.agent_tools.connector_builder import build_connector_body
    sid = params["system_instance_id"]
    action_name = params["action"]
    mat = _mat(run_id, sid)
    spec = mat.openapi or {}
    template = oa_templates.match_template(spec)
    success_rule = template.success_rule() if template else None
    action = next((a for a in doc_parser.parse_openapi(spec) if a.name == action_name), None)
    if action is None:
        raise ToolError(f"接口里无此动作: {action_name}")
    body = build_connector_body(action, tenant=mat.tenant, subsystem=mat.subsystem,
                                success_rule=success_rule, as_step=bool(params.get("as_step")),
                                business=str(params.get("business") or ""),
                                internal=bool(params.get("internal")),
                                fact_check_query=params.get("fact_check_query") or None,
                                fact_check_expr=params.get("fact_check_expr") or None)
    validate_asset_body(AssetType.CONNECTOR, body.model_dump())
    draft = await _ds.save_draft(run_id=run_id, scope=Scope(tenant=mat.tenant, subsystem=Subsystem(mat.subsystem)),
                                 asset_type=AssetType.CONNECTOR, asset_key=action_name, body=body.model_dump())
    return {"asset_draft_id": str(draft.asset_draft_id), "content_hash": draft.content_hash,
            "action": action_name, "risk_level": body.risk_level.value,
            "workflow_step": body.workflow_step, "visibility": body.visibility}


def _action_business_ok(connector_body: dict, resp_body) -> bool:
    """按连接器 success_rule 校验响应体业务码(防 AjaxResult 这类 HTTP200+code500 的假通过)。

    success_rule 取自连接器 assertions.post 里 name=success 的表达式;无则只认 HTTP(返 True)。
    """
    if not isinstance(resp_body, dict):
        return True
    posts = (connector_body.get("assertions") or {}).get("post") or []
    rule = next((a.get("expr") for a in posts if a.get("name") == "success"), None)
    if not rule:
        return True
    from dano.shared.expr import safe_eval
    try:
        return bool(safe_eval(rule, {"response": resp_body, "http": 200}))
    except Exception:  # noqa: BLE001
        return False


# ── 连接器自验证:连接测试 + 沙箱试跑(双关),记证据(sandbox/test)──
async def sandbox_test(run_id: str, params: dict) -> dict:
    """sample_inputs:试跑用的有效入参(写接口需带,否则真实系统拒)。沙箱通过=HTTP2xx 且业务码成功。"""
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.CONNECTOR:
        raise ToolError("sandbox_test 仅用于连接器草案")
    sb = _real_sandbox(_mat(run_id, draft.subsystem.value))
    conn = await sb.connection_test(draft.body)
    v1 = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="connect",
                                     passed=conn.passed, evidence=conn.evidence)
    # 工作流步骤(不能独立跑,如提交步需上一步 taskId):只做连接测试,真实沙箱交复合 sandbox_test_workflow 整链验证
    if params.get("as_step") or draft.body.get("workflow_step"):
        return {"connect_passed": conn.passed, "sandbox_passed": None, "step": True,
                "validation_run_ids": [str(v1.validation_run_id)],
                "detail": f"connect={conn.detail}(工作流步骤:业务正确性由复合整链验证)"}
    sample = params.get("sample_inputs") or {}
    act = await sb.run_action(draft.body, inputs=sample)
    resp_body = (act.evidence or {}).get("response")
    sandbox_passed = act.passed and _action_business_ok(draft.body, resp_body)   # HTTP + 业务码双关
    v2 = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="sandbox",
                                     passed=sandbox_passed, response=resp_body, evidence=act.evidence)
    return {"connect_passed": conn.passed, "sandbox_passed": sandbox_passed,
            "validation_run_ids": [str(v1.validation_run_id), str(v2.validation_run_id)],
            "detail": f"connect={conn.detail}; action={act.detail}; business_ok={sandbox_passed}"}


# ── 字段映射写回实测 ──
async def write_readback(run_id: str, params: dict) -> dict:
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.FIELD_MAPPING:
        raise ToolError("write_readback 仅用于字段映射草案")
    sb = _real_sandbox(_mat(run_id, draft.subsystem.value))
    field = params.get("field", "applicant")
    r = await sb.write_read_back(draft.subsystem.value, field, f"probe::{field}")
    v = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="readback",
                                    passed=r.passed, evidence=r.evidence)
    return {"passed": r.passed, "validation_run_ids": [str(v.validation_run_id)], "detail": r.detail}


# ── 环境画像健康检查 ──
async def health_check(run_id: str, params: dict) -> dict:
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.ENV_PROFILE:
        raise ToolError("health_check 仅用于环境画像草案")
    sb = _real_sandbox(_mat(run_id, draft.subsystem.value))
    r = await sb.health_check(draft.body)
    v = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="health",
                                    passed=r.passed, evidence=r.evidence)
    return {"passed": r.passed, "validation_run_ids": [str(v.validation_run_id)], "detail": r.detail}


# ── 制度规则(流程4):拿原文 → 抽声明式规则 → 跑用例(复用运行期闸门求值)──
async def get_policy_doc(run_id: str, params: dict) -> dict:
    """返回该系统实例登记的制度文件原文(供 pi 抽取规则;不进运行期)。"""
    mat = _mat(run_id, params["system_instance_id"])
    return {"policy_text": mat.policy_text or ""}


def _rules_from_spec_xflow(spec: dict) -> list[dict]:
    """从接口文档的 x-flow 扩展抽业务规则(审批链/校验/升级/记账)。

    人工没登记规则时的兜底来源:enriched swagger 写了 x-flow,就把它变成 pi 能 grounding 的规则,
    而不是凭空臆造。生鲜 CRUD swagger(无 x-flow)→ 返回空 → 复合流程就该只有真实步骤,不强加逻辑。
    用法标注(kind):
    - precondition:能用已声明字段表达的校验(如 amount>0)→ pi 做客户端前置,grounding 得住。
    - server_side / approval_chain:服务端行为(升级加签/审批链/自动记账)→ 写进 preview 说明,**不**做客户端分支。
    """
    if not isinstance(spec, dict):
        return []
    paths = spec.get("paths") or {}
    name_of = {(a.endpoint, (a.method or "").lower()): a.name
               for a in doc_parser.parse_openapi(spec)}
    rules: list[dict] = []
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            xf = op.get("x-flow") if isinstance(op, dict) else None
            if not isinstance(xf, dict):
                continue
            action = name_of.get((path, method.lower()), "")
            flow = xf.get("name") or xf.get("defKey") or action
            for v in xf.get("businessValidations") or []:
                if not isinstance(v, dict):
                    continue
                pr = v.get("params") or []
                field = pr[0] if pr else ""
                label = pr[1] if len(pr) > 1 else field
                desc = v.get("desc") or ""
                if v.get("rule") == "positive" and field:          # 可 grounding 的客户端前置
                    rules.append({"action": action, "flow": flow, "kind": "precondition",
                                  "check": f"{field} > 0", "fields": [field],
                                  "message": desc or f"{label}必须大于0"})
                else:                                              # 无对应查询动作 → 服务端校验,仅说明
                    rules.append({"action": action, "flow": flow, "kind": "server_side",
                                  "desc": desc or str(v.get("rule") or "校验")})
            esc = xf.get("escalation")
            if isinstance(esc, dict) and esc.get("when"):
                rules.append({"action": action, "flow": flow, "kind": "server_side",
                              "condition": esc.get("when"),
                              "desc": f"满足条件加签:{esc.get('addApprover') or '上级审批'}(服务端自动,写进 preview,不做客户端分支)"})
            chain = [c.get("step") for c in (xf.get("approvalChain") or []) if isinstance(c, dict) and c.get("step")]
            if chain:
                rules.append({"action": action, "flow": flow, "kind": "approval_chain", "chain": chain})
            if xf.get("rejectBehavior"):
                rules.append({"action": action, "flow": flow, "kind": "server_side", "desc": str(xf["rejectBehavior"])})
    return rules


async def get_business_rules(run_id: str, params: dict) -> dict:
    """返回业务规则(阈值/审批链)+ 日历源,供 pi grounding 分支/前置/不变量(非臆造)。

    优先用人工登记的规则;没登记时**兜底从 swagger 的 x-flow 抽**(enriched 文档写了就用,生鲜 CRUD 文档则空)。
    kind=precondition 的做客户端前置(grounding 得住);kind=server_side/approval_chain 写进 preview 说明。
    """
    mat = _mat(run_id, params["system_instance_id"])
    rules = mat.business_rules or _rules_from_spec_xflow(mat.openapi or {})
    return {"business_rules": rules, "holidays": mat.holidays or [],
            "usage": "kind=precondition→客户端前置(用已声明字段,grounding 得住);"
                     "kind=server_side/approval_chain→服务端行为,写进 preview 说明,不做客户端分支"}


async def get_selected_flows(run_id: str, params: dict) -> dict:
    """返回**人工勾选的业务**(templateId + 测试值)。pi 只针对这些发现/编排流程,
    sandbox_test_workflow 用这些测试值当 cases。空 = 用户没圈定,可对全量业务自主发现。"""
    mat = _mat(run_id, params["system_instance_id"])
    return {"selected_flows": mat.selected_flows or []}


async def draft_policy(run_id: str, params: dict) -> dict:
    """把 pi 抽出的声明式规则存为 policy_rule 草案(作用域内单份,key=policy_rule)。"""
    sid = params["system_instance_id"]
    mat = _mat(run_id, sid)
    body = {"rules": params["rules"]}
    validate_asset_body(AssetType.POLICY_RULE, body)        # 结构硬校验(rule_id/condition/effect)
    scope = Scope(tenant=mat.tenant, subsystem=Subsystem(mat.subsystem))
    draft = await _ds.save_draft(run_id=run_id, scope=scope, asset_type=AssetType.POLICY_RULE,
                                 asset_key=AssetType.POLICY_RULE.value, body=body)
    return {"asset_draft_id": str(draft.asset_draft_id), "rule_count": len(params["rules"])}


async def test_policy_cases(run_id: str, params: dict) -> dict:
    """跑关键用例:用**运行期同一闸门** PolicyGate 判每条用例的 放行/拦截/转审批 是否符合预期。

    用例全通过才记 cases 证据(发布硬关卡要求);任一不符即整体不通过,pi 据 trace 修规则。
    """
    from dano.orchestrator.gate import GateAction, PolicyGate
    from dano.shared.asset_bodies import PolicyRuleBody
    from dano.shared.enums import RiskLevel
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.POLICY_RULE:
        raise ToolError("test_policy_cases 仅用于制度规则草案")
    body = PolicyRuleBody.model_validate(draft.body)
    cases = params.get("cases", [])
    if not cases:
        raise ToolError("至少给一个测试用例(放行/拦截/转审批)")
    expect_to_action = {"放行": GateAction.ALLOW, "拦截": GateAction.REJECT, "转审批": GateAction.CONFIRM}
    gate = PolicyGate()
    trace, ok_all = [], True
    for c in cases:
        expect = c.get("expect")
        if expect not in expect_to_action:
            raise ToolError(f"用例 expect 须为 放行/拦截/转审批,得 {expect}")
        # risk=L1 隔离风险因素,只看制度规则效果(与运行期同一求值)
        decision = gate.decide(risk_level=RiskLevel.L1, fields=c.get("fields", {}), policy=body)
        ok = decision.action == expect_to_action[expect]
        trace.append({"fields": c.get("fields", {}), "expect": expect,
                      "actual": decision.action.value, "ok": ok})
        ok_all = ok_all and ok
    v = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="cases",
                                    passed=ok_all, evidence={"cases": trace})
    return {"passed": ok_all, "validation_run_ids": [str(v.validation_run_id)], "trace": trace}


# ── 三模型评审委员会:沙箱通过后、发布前的硬闸门(成果验收/漏洞检测/合规审核)──
async def request_review(run_id: str, params: dict) -> dict:
    """对草案跑三模型评审,各审独立模型,结论写 review_runs。返回 verdicts 供 pi 看驳回理由。

    免评审类型直接放行。喂给模型的只有声明式 body + 沙箱证据 trace(无凭证)。
    """
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None:
        raise ToolError("草案不存在")
    from dano.config import get_settings
    if not get_settings().review_enabled:        # 运维急停:跳过评审(发布闸门也会放行)
        return {"all_passed": True, "verdicts": [], "review_run_ids": [],
                "note": "评审已临时关闭(降级)"}
    if draft.asset_type not in REVIEW_REQUIRED_TYPES:
        return {"all_passed": True, "verdicts": [], "review_run_ids": [],
                "note": f"{draft.asset_type.value} 免三模型评审"}
    if draft.asset_type == AssetType.CONNECTOR and draft.body.get("workflow_step"):
        return {"all_passed": True, "verdicts": [], "review_run_ids": [],
                "note": "工作流步骤连接器免单独评审(复合流程整体评审)"}
    if draft.asset_type == AssetType.PAGE_SCRIPT and not page_is_write(draft.body):
        return {"all_passed": True, "verdicts": [], "review_run_ids": [],
                "note": "查询类页面免三模型评审"}
    # 录制抓请求页面:不再整体豁免 —— 结构由 self_check 硬卡,这里三模型只判**语义**(业务逻辑/越权/合规),
    # 拿 Goal 当业务方案对照(评审 prompt 见 _CAPTURE_REVIEW_NOTE)。调用方(run_request_onboarding)按风险驳回。
    vals = await _ds.list_validations(draft.asset_draft_id)
    evidence = [{"kind": v.kind, "passed": v.passed, "environment": v.environment,
                 "credential_type": v.credential_type, "evidence": v.evidence, "response": v.response}
                for v in vals]
    board = _review_board
    if board is None:
        from dano.review.board import ReviewBoard
        board = ReviewBoard.from_settings()
    verdicts = await board.review(asset_type=draft.asset_type.value, asset_key=draft.asset_key,
                                  body=draft.body, evidence=evidence)
    # 确定性容错(写进 DB 证据,故 verify_reviewed 也认):若本资产是 **dry-only**(无真跑 replay 证据,
    # 即录制路径 by-design 的写安全模式 → partially_verified),评审若**仅因"dry/self_check 未真跑"否决** = 误判该安全模式
    # → 剔除该理由;某维度理由清空即视为通过。**确定性层承重,不让 LLM 抖动阻断按设计的安全行为。**
    from dano.onboarding.repair import is_dry_mode_reason
    # dry-only 判定:真跑 = kind=replay **且 mode∈{full,live}**(真提交)。页面型 sandbox_replay 即便 dry 也记
    # kind=replay(mode=dry),旧逻辑误判其为真跑 → 漏掉 dry 豁免 → acceptance 因 submitted=false 误杀页面 Skill。
    def _is_real_run(e: dict) -> bool:
        return e.get("kind") == "replay" and (e.get("evidence") or {}).get("mode") in ("full", "live")
    dry_only = not any(_is_real_run(e) for e in evidence)
    review_run_ids, out = [], []
    # 仅**页面直驱**(DOM 操作,无 api_request)享 acceptance 顾问化;**抓请求型**(有 api_request)仍走原评审闸门。
    is_dry_page = dry_only and draft.asset_type == AssetType.PAGE_SCRIPT and not draft.body.get("api_request")
    for v in verdicts:
        passed, reasons = v.passed, list(v.reasons or [])
        if dry_only and not passed:
            kept = [r for r in reasons if not is_dry_mode_reason(r)]
            if len(kept) != len(reasons):
                log.info("request_review.dropped_dry_reason", role=v.role,
                         dropped=len(reasons) - len(kept))
                reasons, passed = kept, (len(kept) == 0)
        # 页面直驱默认 dry:业务"是否真达成"在 dry 下**无法验证**(submitted=false 是设计如此),
        # 故 acceptance 对 dry 页面脚本降为**非阻断顾问**——承重交 self_check + replay(结构/可回放),
        # security/compliance 仍硬卡。否则弱模型 acceptance 反复无常驳回 → draft→replay→review 死循环数分钟。
        if is_dry_page and not passed and v.role == "acceptance":
            log.info("request_review.acceptance_advisory_dry", reasons=reasons[:3])
            passed = True
        rr = await _ds.record_review(asset_draft_id=draft.asset_draft_id, role=v.role,
                                     model_id=v.model_id, passed=passed, reasons=reasons)
        review_run_ids.append(str(rr.review_run_id))
        out.append({"role": v.role, "model": v.model_id, "passed": passed, "reasons": reasons})
    all_passed = bool(out) and all(o["passed"] for o in out)
    log.info("request_review", draft=str(draft.asset_draft_id), all_passed=all_passed)
    try:                                       # 把三维审核结果推给前端(成果验收/漏洞检测/合规),治"盲盒"
        from dano.agent_tools import progress
        progress.emit(run_id, {"tool": "request_review", "all_passed": all_passed,
                               "reviews": [{"role": o["role"], "passed": o["passed"]} for o in out]})
    except Exception:  # noqa: BLE001
        pass
    return {"all_passed": all_passed, "verdicts": out, "review_run_ids": review_run_ids}


# ── 发布硬关卡:后端重读证据校验,通过才入库发布 ──
async def publish_asset(run_id: str, params: dict) -> dict:
    draft_id = UUID(params["asset_draft_id"])
    vrids = [UUID(v) for v in params.get("validation_run_ids", [])]
    rrids = [UUID(v) for v in params.get("review_run_ids", [])]
    ok, reason = await _ds.verify_publishable(draft_id, vrids)
    if not ok:
        return {"published": False, "reason": reason}
    ok_r, reason_r = await _ds.verify_reviewed(draft_id, rrids)   # 三模型评审硬闸门
    if not ok_r:
        return {"published": False, "reason": reason_r}
    draft = await _ds.get_draft(draft_id)
    validate_asset_body(draft.asset_type, draft.body)     # 再次结构校验
    env = await _repo.create(AssetEnvelope(
        asset_type=draft.asset_type, scope=Scope(tenant=draft.tenant, subsystem=draft.subsystem),
        asset_key=draft.asset_key, version=0, source_fingerprint=draft.content_hash,
        validation_status=ValidationStatus.VERIFIED, confidence=0.95, body=draft.body))
    await _repo.set_status(env.asset_id, ValidationStatus.PUBLISHED)
    log.info("publish_asset.ok", asset_id=str(env.asset_id), action=draft.asset_key)
    return {"published": True, "asset_id": str(env.asset_id), "version": env.version}


# ── 代码适配器(goal 模式 M1):草案 + 隔离沙箱测试 ──
async def draft_adapter(run_id: str, params: dict) -> dict:
    """存一份适配器代码草案(goal 模式「编码」产物)。params 字段对应 AdapterBody。"""
    sid = params["system_instance_id"]
    mat = _mat(run_id, sid)
    body = {k: v for k, v in params.items() if k != "system_instance_id"}
    body.setdefault("strategy", "simple_http")
    m = validate_asset_body(AssetType.ADAPTER, body)     # 结构校验(源码零凭证由策略/漏洞校验把关)
    draft = await _ds.save_draft(
        run_id=run_id, scope=Scope(tenant=mat.tenant, subsystem=Subsystem(mat.subsystem)),
        asset_type=AssetType.ADAPTER, asset_key=m.action, body=m.model_dump())
    return {"asset_draft_id": str(draft.asset_draft_id), "action": m.action,
            "content_hash": draft.content_hash}


_SWALLOWED_ERR_KEYS = ("_adapter_error", "_error", "exception", "traceback")


def _adapter_output_error(out: object) -> str | None:
    """适配器"正常返回"但其实把内部异常吞进了返回值(如 {'_adapter_error': ...})→ 返回原因串。

    教训:沙箱曾因此**假通过**——代码 NameError 被自己 try/except 吞成 {'_adapter_error':...},
    runner ok=True,而 success_rule(response.code == null)又把"无 code 的错误对象"判成成功。
    故在判 success_rule **之前**先拦下这类自吞错误,不让成败规则替错误对象背书。
    """
    if not isinstance(out, dict):
        return None
    for k in _SWALLOWED_ERR_KEYS:
        if out.get(k):
            return f"适配器内部异常被吞({k}):{out[k]}"
    return None


async def sandbox_test_adapter(run_id: str, params: dict) -> dict:
    """隔离 runner 跑适配器(测试账号),按 success_rule 判成败,记 sandbox 证据。

    二态:run.ok 且(无自吞错误)且(无 success_rule 或表达式为真)→ passed;失败给结构化 reasons 供驳回重写。
    """
    from dano.execution.adapter import AdapterRunner
    from dano.shared.asset_bodies import AdapterBody
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.ADAPTER:
        raise ToolError("sandbox_test_adapter 仅用于适配器草案")
    body = AdapterBody.model_validate(draft.body)
    mat = _mat(run_id, draft.subsystem.value)
    res = await AdapterRunner().run(source=body.source, inputs=params.get("test_input", {}),
                                    credentials=mat.credentials or {}, entry=body.entry)
    reasons: list[str] = []
    passed = res.ok
    out_err = _adapter_output_error(res.output) if res.ok else None
    if not res.ok:
        reasons.append(f"运行失败: {res.error}")
    elif out_err:                                            # 自吞异常 → 直接判失败(堵假通过)
        passed = False
        reasons.append(out_err)
    elif body.success_rule:
        from dano.shared.expr import safe_eval
        try:
            passed = bool(safe_eval(body.success_rule, {"response": res.output, "http": 200}))
        except Exception as e:  # noqa: BLE001
            passed = False
            reasons.append(f"成败表达式求值出错: {e}")
        if not passed and not reasons:
            reasons.append(f"未满足 success_rule={body.success_rule!r};实得 response={res.output}")
    # 事实核查(流程9 一等公民):声明了 fact_check 就必须过——堵死"操作成功但空操作"
    fc_evidence = None
    if passed and body.fact_check is not None:
        from dano.execution.fact_check import run_fact_check
        ctx = {**(params.get("test_input") or {}),
               **(res.output if isinstance(res.output, dict) else {})}
        try:
            fc_ok, fc_evidence = await run_fact_check(
                body.fact_check, context=ctx, call=_adapter_caller(mat))
        except Exception as e:  # noqa: BLE001
            fc_ok, fc_evidence = False, {"error": str(e)}
        if not fc_ok:
            passed = False
            reasons.append(f"事实核查未过(疑似空操作):{body.fact_check.assert_expr}")
    resp = res.output if isinstance(res.output, dict) else {"value": res.output}
    v = await _ds.record_validation(
        asset_draft_id=draft.asset_draft_id, kind="sandbox", passed=passed, response=resp,
        evidence={"success_rule": body.success_rule, "duration_s": res.duration_s,
                  "stdout": res.stdout[:500], "fact_check": fc_evidence})
    return {"passed": passed, "validation_run_ids": [str(v.validation_run_id)],
            "output": res.output, "reasons": reasons}


async def vuln_scan(run_id: str, params: dict) -> dict:
    """漏洞校验:对适配器源码做确定性静态扫描(危险调用/命令注入/硬编码密钥),记 vuln 证据。

    二态:无 findings → passed;否则 passed=False 且 findings 作驳回原因。语义级深审由三模型 security 角色补。
    """
    from dano.generation.vuln import scan_source
    from dano.shared.asset_bodies import AdapterBody
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.ADAPTER:
        raise ToolError("vuln_scan 仅用于适配器草案")
    body = AdapterBody.model_validate(draft.body)
    findings = scan_source(body.source)
    passed = not findings
    v = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="vuln",
                                    passed=passed, evidence={"findings": findings})
    return {"passed": passed, "validation_run_ids": [str(v.validation_run_id)], "findings": findings}


async def lint_adapter(run_id: str, params: dict) -> dict:
    """编码契约校验:确定性静态检查生成代码(入口签名 / 不吞异常 / 库未 import),记 lint 证据。

    与 vuln_scan(安全)分工:本关查**可执行契约**——普适硬错,跨企业/跨系统通用,零误报优先。
    二态:无 findings → passed;否则作驳回原因回灌编码器修复。
    """
    from dano.generation.coder_lint import scan_source
    from dano.shared.asset_bodies import AdapterBody
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.ADAPTER:
        raise ToolError("lint_adapter 仅用于适配器草案")
    body = AdapterBody.model_validate(draft.body)
    findings = scan_source(body.source)
    passed = not findings
    v = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="lint",
                                    passed=passed, evidence={"findings": findings})
    return {"passed": passed, "validation_run_ids": [str(v.validation_run_id)], "findings": findings}


# ── 页面型 Skill(流程8,无 API):真实浏览器侦察 → 确定性建体 → 沙箱回放 ──
async def _launch_page_driver(mat, *, headless: bool = True):  # noqa: ANN001
    """从材料起一个真实 Playwright 驱动(base_url + 测试登录态)。缺 playwright → ToolError。"""
    try:
        from dano.execution.page.driver import PlaywrightPageDriver
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"页面工具需要 playwright:{e}") from e
    base = (mat.deploy or {}).get("base_url", "") if mat else ""
    creds = (mat.credentials or {}) if mat else {}
    storage = creds.get("storage_state") or None
    token = creds.get("token") or None        # OA Bearer token → 预置登录态(免登录),侦察/回放都用
    drv, _ = await PlaywrightPageDriver.launch(base_url=base, headless=headless, storage_state=storage,
                                               token=token, auth_url=base)
    return drv


async def scout_page(run_id: str, params: dict) -> dict:
    """真实浏览器侦察一个页面:返回候选字段 / 提交按钮 / 结构指纹 / 建议步骤(供 pi 决策或确定性兜底)。"""
    mat = _mat(run_id, params["system_instance_id"])
    start_url = params["start_url"]
    drv = await _launch_page_driver(mat, headless=params.get("headless", True))
    try:
        await drv.open(start_url)
        dom = await drv.scout()
        fp = await drv.fingerprint()
    finally:
        await drv.close()
    from dano.execution.page import to_recorded_steps
    steps, submit = to_recorded_steps(dom)
    return {"start_url": start_url, "dom_fingerprint": fp,
            "fields": dom.get("fields", []), "buttons": dom.get("buttons", []),
            "submit_locator": submit, "suggested_steps": [s.model_dump() for s in steps]}


async def draft_page_script(run_id: str, params: dict) -> dict:
    """pi 给 action + steps(+成功标志/标题)→ 确定性建 PageScriptBody → 存草案。"""
    from dano.agent_tools.page_builder import RecordedStep, build_page_script
    mat = _mat(run_id, params["system_instance_id"])
    steps = [RecordedStep.model_validate(s) for s in params["steps"]]
    body = build_page_script(
        steps, action=params["action"], dom_fingerprint=params["dom_fingerprint"],
        title=params.get("title", ""), start_url=params.get("start_url", ""),
        success_marker=params.get("success_marker"))
    dump = body.model_dump()
    # 页面直驱(operate-page)结晶时带 goal → 存进资产,供漂移自愈时**自主重驱动**(Agent 自己重跑,非 scout 一次)
    if params.get("goal"):
        dump["goal"] = {"intent": params["goal"]}
    validate_asset_body(AssetType.PAGE_SCRIPT, dump)
    scope = Scope(tenant=mat.tenant, subsystem=Subsystem(mat.subsystem))  # type: ignore[arg-type]
    draft = await _ds.save_draft(run_id=run_id, scope=scope, asset_type=AssetType.PAGE_SCRIPT,
                                 asset_key=body.action, body=dump)
    return {"asset_draft_id": str(draft.asset_draft_id), "action": body.action,
            "risk_level": body.risk_level.value, "needs_review": page_is_write(dump),
            "content_hash": draft.content_hash}


def _dry_replay_script(body: dict) -> PageScriptBody:
    """写页面未授权真提交时的 dry 回放:submit 步降为 verify(断言提交按钮可见),去成功标志。

    替代真点提交 —— 证明所有字段可填、提交按钮在位、结构未漂移,但不在测试系统真建单。
    页面写要"真提交+成功标志",须 DANO_PAGE_WRITE_PROBE=1 显式授权(测试账号)。
    """
    b = dict(body)
    acts = []
    for a in b.get("actions", []):
        a = dict(a)
        if a.get("op") == "submit":
            a["op"], a["assert_visible"] = "verify", True
        acts.append(a)
    b["actions"], b["success_marker"] = acts, None
    return PageScriptBody.model_validate(b)


async def sandbox_replay(run_id: str, params: dict) -> dict:
    """测试账号回放页面脚本草案,记 replay 证据(发布闸门要求)。

    写页面默认 **dry 回放**(填字段 + 断言提交按钮可见,不真点提交);
    DANO_PAGE_WRITE_PROBE=1 时才真提交 + 校验成功标志。查询页面始终全程回放。
    """
    from dano.execution.page import PageActionRuntime
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.PAGE_SCRIPT:
        raise ToolError("sandbox_replay 仅用于页面脚本草案")
    # 抓请求路径:dry 校验(参数都填上、请求可构造),不开浏览器、不真发(写安全)
    if draft.body.get("api_request"):
        from dano.execution.page.request_capture import execute_api   # 单请求/多步工作流(Q3)分派
        apir = draft.body["api_request"]
        sample_inputs = params.get("sample_inputs") or {}
        out = await execute_api(apir, sample_inputs, send=False)        # 承重闸门=确定性 self_check(dry,写安全)
        v = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="self_check",
                                        passed=bool(out.get("ok")), response=out,
                                        evidence={"mode": "self_check", "violations": out.get("self_check") or [],
                                                  "request": out})
        log.info("sandbox_replay.self_check", draft=str(draft.asset_draft_id), passed=bool(out.get("ok")),
                 violations=out.get("self_check") or [])
        vrids = [str(v.validation_run_id)]
        # 活体验证(仅当上层判定**可逆沙箱 + 带测试登录态**):真发写 + fact_check(execute_api 内置回查)→ 记 replay 证据。
        # 不可逆/未声明环境不会走到这(plan=structural),所以默认绝不真发,避免污染。
        if params.get("live") and params.get("storage_state") is not None and out.get("ok"):
            live = await execute_api(apir, sample_inputs, base_url=params.get("base_url", ""),
                                     storage_state=params.get("storage_state"), send=True,
                                     verify=params.get("verify", False))
            live_ok = bool(live.get("ok")) and live.get("fact_check_passed", True) is not False
            vr = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="replay",
                                             passed=live_ok, response=live,
                                             evidence={"mode": "live", "fact_check_passed": live.get("fact_check_passed")})
            log.info("sandbox_replay.live", draft=str(draft.asset_draft_id), passed=live_ok,
                     status=live.get("status"), fact_check=live.get("fact_check_passed"))
            vrids.append(str(vr.validation_run_id))
            return {"passed": bool(out.get("ok")) and live_ok, "mode": "live", "live": live,
                    "structured_output": out, "validation_run_ids": vrids}
        return {"passed": bool(out.get("ok")), "mode": "self_check",
                "structured_output": out, "validation_run_ids": vrids}
    mat = _mat(run_id, draft.subsystem.value)
    is_write = page_is_write(draft.body)
    from dano.config import get_settings
    allow_write = get_settings().page_write_probe
    dry = is_write and not allow_write
    script = _dry_replay_script(draft.body) if dry else PageScriptBody.model_validate(draft.body)

    async def factory():  # noqa: ANN202
        return await _launch_page_driver(mat, headless=params.get("headless", True))

    res = await PageActionRuntime(factory).run(
        uuid4(), script, params.get("sample_inputs") or {}, confirm=lambda f: True)
    passed = res.outcome == Outcome.PASSED
    mode = "dry" if dry else "full"
    v = await _ds.record_validation(
        asset_draft_id=draft.asset_draft_id, kind="replay", passed=passed,
        response=res.structured_output,
        evidence={"mode": mode, "screenshots": res.evidence.screenshots,
                  "assertions": [r.model_dump() for r in res.assertion_results]})
    return {"passed": passed, "mode": mode, "structured_output": res.structured_output,
            "validation_run_ids": [str(v.validation_run_id)]}


# 工具注册表(白名单)。验证类工具天然只走 sandbox/test。
TOOLS = {
    "parse_spec": parse_spec,
    "get_action_schema": get_action_schema,
    "fingerprint": fingerprint_materials,
    "draft_connector": draft_connector,
    "draft_workflow": draft_workflow,
    "save_draft": save_draft,
    "sandbox_test": sandbox_test,
    "sandbox_test_workflow": sandbox_test_workflow,
    "write_readback": write_readback,
    "health_check": health_check,
    "get_policy_doc": get_policy_doc,
    "get_business_rules": get_business_rules,
    "get_selected_flows": get_selected_flows,
    "draft_policy": draft_policy,
    "test_policy_cases": test_policy_cases,
    "request_review": request_review,
    "publish_asset": publish_asset,
    "draft_adapter": draft_adapter,
    "sandbox_test_adapter": sandbox_test_adapter,
    "vuln_scan": vuln_scan,
    "lint_adapter": lint_adapter,
    # 页面型 Skill(流程8,无 API)
    "scout_page": scout_page,
    "draft_page_script": draft_page_script,
    "sandbox_replay": sandbox_replay,
}
