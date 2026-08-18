"""pi 自定义工具的 Python 实现(确定性能力)。

红线:
- sandbox_test/write_readback/health_check 一律 environment=sandbox + credential_type=test,绝不碰生产写。
- publish_asset 走 Phase 1 的 verify_publishable 硬关卡:只认后端生成的证据,不信 agent 自报。
凭证只在进程内(materials),绝不进 LLM 上下文。
"""

from __future__ import annotations

from copy import deepcopy
import re
from uuid import UUID, uuid4

import structlog

from dano.agent_tools import materials
from dano.assets.drafts import REVIEW_REQUIRED_TYPES, DraftStore, page_is_write
from dano.assets.repository import AssetRepository
from dano.capabilities import doc_parser, endpoint_classifier, fingerprint, oa_templates
from dano.execution.connectors.auth import AuthManager
from dano.execution.connectors.executor import SystemEndpoint, system_key_for
from dano.capabilities.sandbox import RealSandbox
from dano.schemas import validate_asset_body
from dano.shared.asset_bodies import AuthConfig
from dano.shared.enums import AssetType, Subsystem, ValidationStatus
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
    template = oa_templates.match_template(spec, tenant=mat.tenant)
    extra = template.infrastructure_patterns() if template else ()
    template_name = template.name if template else None
    success_rule = template.success_rule() if template else None
    include = {t for t in (params.get("include_tags") or mat.include_tags or [])}
    all_actions = doc_parser.parse_openapi(spec)              # 确定性枚举:完整、grounded
    # LLM 语义识别(可选):对已枚举清单逐个判 role + 业务 category;另据真实响应判成功约定;失败回退确定性
    llm_map: dict = {}
    if params.get("use_llm_classify") and all_actions:
        from functools import partial

        from dano.infra.llm import openai_text_spawn
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
    from dano.onboarding.dsl_grounding import check_grounding, collect_field_refs
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
    tmpl = oa_templates.match_template(mat.openapi or {}, tenant=mat.tenant)
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
    from dano.onboarding.dsl_grounding import branch_ids, coverage_gaps
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
    template = oa_templates.match_template(spec, tenant=mat.tenant)
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
    if not get_settings().review_enabled:
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
    unavailable = [
        reason
        for verdict in verdicts
        if not verdict.passed
        for reason in (verdict.reasons or [])
        if str(reason).startswith("评审服务不可用:")
    ]
    # 确定性容错(写进 DB 证据,故 verify_reviewed 也认):若本资产是 **dry-only**(无 live health 证据,
    # 即录制路径 by-design 的写安全模式 → partially_verified),评审若**仅因"dry/self_check 未真跑"否决** = 误判该安全模式
    # → 剔除该理由;某维度理由清空即视为通过。**确定性层承重,不让 LLM 抖动阻断按设计的安全行为。**
    from dano.onboarding.repair import is_dry_mode_reason
    dry_only = not any(e.get("kind") == "health" and (e.get("evidence") or {}).get("mode") == "live"
                       for e in evidence)
    review_run_ids, out = [], []
    for v in verdicts:
        passed, reasons = v.passed, list(v.reasons or [])
        if dry_only and not passed:
            kept = [r for r in reasons if not is_dry_mode_reason(r)]
            if len(kept) != len(reasons):
                log.info("request_review.dropped_dry_reason", role=v.role,
                         dropped=len(reasons) - len(kept))
                reasons, passed = kept, (len(kept) == 0)
        rr = await _ds.record_review(asset_draft_id=draft.asset_draft_id, role=v.role,
                                     model_id=v.model_id, passed=passed, reasons=reasons)
        review_run_ids.append(str(rr.review_run_id))
        out.append({"role": v.role, "model": v.model_id, "passed": passed, "reasons": reasons})
    all_passed = bool(out) and all(o["passed"] for o in out)
    log.info("request_review", draft=str(draft.asset_draft_id), all_passed=all_passed)
    return {
        "all_passed": all_passed,
        "verdicts": out,
        "review_run_ids": review_run_ids,
        "review_unavailable": bool(unavailable),
        "retryable": bool(unavailable),
        "review_error": "评审服务暂时未返回有效结果，请稍后重试发布" if unavailable else "",
    }


def _recording_release_snapshot_matches(session, draft) -> tuple[bool, str]:  # noqa: ANN001
    """Bind a recording publish to the exact machine-validated frozen graph."""
    from dano.execution.page.flow_spec import FlowSpec, flow_spec_fingerprint

    if draft is None:
        return False, "录制发布草案不存在"
    current_spec = session.current_flow_spec()
    current_fingerprint = flow_spec_fingerprint(current_spec)
    release = dict((current_spec.meta or {}).get("release_candidate") or {})
    release_fingerprint = str(release.get("flow_fingerprint") or "")
    if not release_fingerprint or release_fingerprint != current_fingerprint:
        return False, "录制发布候选未冻结或已变化"
    api_request = draft.body.get("api_request") if isinstance(draft.body, dict) else None
    snapshot = api_request.get("_release_snapshot") if isinstance(api_request, dict) else None
    if not isinstance(snapshot, dict):
        return False, "录制发布草案缺少冻结 release snapshot"
    if str(snapshot.get("flow_fingerprint") or "") != release_fingerprint:
        return False, "录制发布草案与当前冻结候选不一致"
    snapshot_flow = snapshot.get("flow_spec")
    if not isinstance(snapshot_flow, dict):
        return False, "录制发布草案缺少冻结 FlowSpec"
    try:
        snapshot_fingerprint = flow_spec_fingerprint(FlowSpec.model_validate(snapshot_flow))
    except Exception:  # noqa: BLE001 - persisted data is an untrusted boundary
        return False, "录制发布草案的冻结 FlowSpec 无效"
    if snapshot_fingerprint != release_fingerprint:
        return False, "录制发布草案的冻结 FlowSpec 与当前候选不一致"
    return True, "ok"


# ── 发布硬关卡:后端重读证据校验,通过才入库发布 ──
async def publish_asset(run_id: str, params: dict) -> dict:
    draft_id = UUID(params["asset_draft_id"])
    vrids = [UUID(v) for v in params.get("validation_run_ids", [])]
    rrids = [UUID(v) for v in params.get("review_run_ids", [])]
    direct_recording_export = params.get("recording_direct_export") is True
    if not direct_recording_export:
        ok, reason = await _ds.verify_publishable(draft_id, vrids)
        if not ok:
            return {"published": False, "reason": reason}
    draft = await _ds.get_draft(draft_id)
    if (
        params.get("recording_release_candidate") is True
        or params.get("recording_machine_validated") is True
    ):
        from dano.onboarding.recording_pi import active_recording_session

        session = active_recording_session(run_id)
        if session is None:
            return {"published": False, "reason": "录制机器核验会话不存在或已经关闭"}
        ok_r, reason_r = _recording_release_snapshot_matches(session, draft)
    else:
        # Non-recording assets retain the existing review policy unchanged.
        ok_r, reason_r = await _ds.verify_reviewed(draft_id, rrids)
    if not ok_r:
        return {"published": False, "reason": reason_r}
    validate_asset_body(draft.asset_type, draft.body)     # 再次结构校验
    env = await _repo.create(AssetEnvelope(
        asset_type=draft.asset_type, scope=Scope(tenant=draft.tenant, subsystem=draft.subsystem),
        asset_key=draft.asset_key, version=0, source_fingerprint=draft.content_hash,
        validation_status=(
            ValidationStatus.DRAFT
            if direct_recording_export
            else ValidationStatus.VERIFIED
        ),
        confidence=(0.7 if direct_recording_export else 0.95),
        body=draft.body))
    await _repo.set_status(env.asset_id, ValidationStatus.PUBLISHED)
    log.info("publish_asset.ok", asset_id=str(env.asset_id), action=draft.asset_key)
    return {"published": True, "asset_id": str(env.asset_id), "version": env.version}


# ── 录制 V2 页面资产:self-check / live 校验 ──
async def self_check_recording(run_id: str, params: dict) -> dict:
    """录制 V2 抓请求资产的 self-check / live 校验。"""
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.PAGE_SCRIPT:
        raise ToolError("self_check_recording 仅用于录制页面草案")
    if not draft.body.get("api_request"):
        raise ToolError("录制页面草案缺少 api_request；仅支持录制 V2 api_request 资产")
    from dano.execution.page.request_capture import execute_api   # 单请求/多步工作流(Q3)分派
    apir = draft.body["api_request"]
    sample_inputs = params.get("sample_inputs") or {}
    out = await execute_api(apir, sample_inputs, send=False)        # 承重闸门=确定性 self_check(dry,写安全)
    v = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="self_check",
                                    passed=bool(out.get("ok")), response=out,
                                    evidence={"mode": "self_check", "violations": out.get("self_check") or [],
                                              "request": out})
    log.info("recording.self_check", draft=str(draft.asset_draft_id), passed=bool(out.get("ok")),
             violations=out.get("self_check") or [])
    vrids = [str(v.validation_run_id)]
    if params.get("live") and params.get("storage_state") is not None and out.get("ok"):
        live = await execute_api(apir, sample_inputs, base_url=params.get("base_url", ""),
                                 storage_state=params.get("storage_state"), send=True,
                                 verify=params.get("verify", False))
        live_ok = bool(live.get("ok")) and live.get("fact_check_passed", True) is not False
        vr = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="health",
                                         passed=live_ok, response=live,
                                         evidence={"mode": "live", "fact_check_passed": live.get("fact_check_passed")})
        log.info("recording.live_health", draft=str(draft.asset_draft_id), passed=live_ok,
                 status=live.get("status"), fact_check=live.get("fact_check_passed"))
        vrids.append(str(vr.validation_run_id))
        return {"passed": bool(out.get("ok")) and live_ok, "mode": "live", "live": live,
                "structured_output": out, "validation_run_ids": vrids}
    return {"passed": bool(out.get("ok")), "mode": "self_check",
            "structured_output": out, "validation_run_ids": vrids}


def _recording_session(run_id: str, params: dict):  # noqa: ANN202
    from dano.onboarding.recording_pi import active_recording_session

    session = active_recording_session(run_id)
    if session is None:
        raise ToolError("录制 Pi Session 不存在或已经关闭")
    recording_id = str(params.get("recording_id") or "")
    if recording_id and recording_id != session.recording_id:
        raise ToolError("recording_id 与当前录制会话不匹配")
    # run_id is the authenticated, server-owned session boundary.  Fill the
    # redundant display identity here so a model omission cannot invalidate an
    # otherwise complete (and expensive) screenshot analysis.
    params.setdefault("recording_id", session.recording_id)
    return session


def _strict_recording_params(params: dict, *, required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(params, dict):
        raise ToolError("录制工具参数必须是对象")
    allowed = required | (optional or set())
    missing = sorted(key for key in required if params.get(key) in (None, ""))
    unknown = sorted(set(params) - allowed)
    if missing:
        raise ToolError(f"录制工具缺少参数: {','.join(missing)}")
    if unknown:
        raise ToolError(f"录制工具包含未知参数: {','.join(unknown)}")
    if "base_flow_version" in params and (
        isinstance(params["base_flow_version"], bool)
        or not isinstance(params["base_flow_version"], int)
    ):
        raise ToolError("base_flow_version 必须是整数")


def _recording_facts(spec) -> dict:  # noqa: ANN001
    facts = spec.request_facts.model_dump(mode="json")
    # RequestAnalysis and RequestUsage are explicitly derived projections. A
    # valid plan changes capability membership, so sync_flow_spec_models must
    # be allowed to refresh them. Everything else (including future extra
    # fields) remains immutable recording evidence and is compared fail-closed.
    facts.pop("analysis", None)
    facts.pop("usage", None)
    # field_evidence binding results are likewise server-derived: the sync
    # pipeline (_rebind_saved_field_evidence) deliberately re-evaluates
    # unresolved/heuristic DOM-to-wire bindings against authoritative saved
    # bodies on every apply. After finalize merges the full recorder facts,
    # that repair pass legitimately rewrites bindings; comparing it here made
    # every subsequent plan/repair submission fail as a fake fact violation.
    # Raw captures (requests, page_events, diagnostics, option_sources) stay
    # guarded below.
    facts.pop("field_evidence", None)
    # RequestFacts stores analysis and usage separately from captured HTTP evidence.
    # Compare only fields declared by RequestFact so derived or imported extras cannot
    # trigger a false immutable-evidence violation.
    facts["requests"] = [
        fact.model_dump(
            mode="json",
            include=set(type(fact).model_fields),
        )
        for fact in spec.request_facts.requests
    ]
    return facts


def _restore_recording_session(
    session,  # noqa: ANN001
    before_spec,  # noqa: ANN001
    *,
    last_submission_kind,
) -> None:
    """Restore a failed recording mutation through the session's public bind API."""
    bind = getattr(session, "bind_flow_spec", None)
    if not callable(bind):
        raise RuntimeError("录制 Pi Session 不支持原子回滚")
    bind(before_spec)
    if hasattr(session, "last_submission_kind"):
        session.last_submission_kind = last_submission_kind


async def _apply_recording_submission_atomic(
    session,  # noqa: ANN001
    submission: dict,
    *,
    mode: str,
    base_flow_version: int,
) -> dict:
    before_spec = session.current_flow_spec()
    before_facts = _recording_facts(before_spec)
    before_kind = getattr(session, "last_submission_kind", "")
    try:
        result = await session.apply_submission(
            submission,
            mode=mode,
            base_flow_version=base_flow_version,
        )
        if _recording_facts(session.current_flow_spec()) != before_facts:
            raise ToolError(
                "录制计划不得修改原始 request facts"
                if mode == "plan" else "录制修复不得修改原始 request facts"
            )
        # Submission tools only need an acknowledgement and per-operation
        # outcomes.  The full validation report has its own read tool; feeding
        # it back here duplicates tens of thousands of characters into every
        # live Pi turn and caused later requests to time out.
        return {
            key: deepcopy(result[key])
            for key in (
                "flow_version", "op_results", "all_applied", "must_retry",
                "unresolved_targets", "accepted", "unchanged", "warning",
                "capability_plan_complete", "capability_retry_reasons",
                "submission_complete",
            )
            if key in result
        }
    except Exception as exc:  # noqa: BLE001 - rollback all partial session mutations
        try:
            _restore_recording_session(
                session,
                before_spec,
                last_submission_kind=before_kind,
            )
        except Exception as rollback_exc:  # noqa: BLE001
            raise ToolError(f"录制 {mode} 失败且会话回滚失败: {rollback_exc}") from rollback_exc
        if isinstance(exc, ToolError):
            raise
        if isinstance(exc, (TypeError, ValueError, RuntimeError)):
            raise ToolError(str(exc)) from exc
        raise


async def get_recording_state(run_id: str, params: dict) -> dict:
    _strict_recording_params(params, required=set(), optional={"recording_id", "flow_version"})
    return await _recording_session(run_id, params).get_recording_state()


async def get_recording_delta(run_id: str, params: dict) -> dict:
    _strict_recording_params(
        params,
        required=set(),
        optional={"recording_id", "flow_version", "since_seq", "limit"},
    )
    since_seq = params.get("since_seq", 0)
    if isinstance(since_seq, bool) or not isinstance(since_seq, int) or since_seq < 0:
        raise ToolError("since_seq 必须是非负整数")
    limit = params.get("limit", 25)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ToolError("limit 必须是 1 到 50 的整数")
    return await _recording_session(run_id, params).get_recording_delta(since_seq, limit=limit)


async def ask_recording_operator(run_id: str, params: dict) -> dict:
    _strict_recording_params(
        params,
        required={"text"},
        optional={"recording_id", "flow_version", "options", "context_ref"},
    )
    options = params.get("options") or []
    if not isinstance(options, list) or any(not isinstance(value, str) or not value.strip() for value in options):
        raise ToolError("options 必须是非空字符串数组")
    session = _recording_session(run_id, params)
    text = str(params["text"])
    if re.search(r"(?i)\b(?:recording_id|flow_version|run_id)\b", text):
        return {
            "answered": True,
            "answer": "recording_id 和 flow_version 由服务端管理；调用录制工具时省略这些字段。",
            "reason": "server_owned_recording_context",
        }
    return await session.ask_operator(
        text=text,
        options=options,
        context_ref=str(params.get("context_ref") or ""),
    )


def _captured_recording_requests(session) -> list[dict]:  # noqa: ANN001
    spec = session.current_flow_spec()
    return [request.model_dump(mode="python") for request in spec.request_facts.requests]


def _find_captured_requests(session, request_ids: list[str]) -> list[dict]:  # noqa: ANN001
    requests = _captured_recording_requests(session)
    by_id = {str(request.get("request_id") or ""): request for request in requests}
    missing = [request_id for request_id in request_ids if request_id not in by_id]
    if missing:
        raise ToolError(f"录制请求不存在: {','.join(missing)}")
    return [by_id[request_id] for request_id in request_ids]


async def _recording_auth_headers(session, requests: list[dict]) -> dict:  # noqa: ANN001
    from dano.execution.page.request_capture import extract_auth_headers
    from dano.infra.token_store import get_token_headers, normalize_headers

    # Persisted runtime credentials are only a fallback during recording.  The
    # headers captured from the active browser request are newer and must win.
    headers = normalize_headers(await get_token_headers(session.tenant, session.subsystem))
    for request in requests:
        headers = normalize_headers({
            **headers,
            **extract_auth_headers(request.get("headers")),
        })
    return headers


def _recording_replay_overrides(value: object, *, label: str) -> dict:
    if not isinstance(value, dict):
        raise ToolError(f"{label} 必须是对象")
    allowed = {"url_path", "query", "body", "headers"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ToolError(
            f"{label} 只允许 url_path/query/body/headers，禁止按 request_id 包裹: {','.join(unknown)}"
        )
    if "url_path" in value and not isinstance(value["url_path"], str):
        raise ToolError(f"{label}.url_path 必须是字符串")
    for key in ("query", "body", "headers"):
        if key in value and not isinstance(value[key], dict):
            raise ToolError(f"{label}.{key} 必须是对象")
    return dict(value)


async def replay_recording_request(run_id: str, params: dict) -> dict:
    from dano.execution.page.replay import replay_request

    _strict_recording_params(params, required={"request_id"}, optional={"recording_id", "flow_version", "overrides"})
    session = _recording_session(run_id, params)
    requests = _find_captured_requests(session, [str(params["request_id"])])
    overrides = (
        _recording_replay_overrides(params["overrides"], label="overrides")
        if "overrides" in params else None
    )
    result = await replay_request(
        requests[0],
        overrides=overrides,
        auth_headers=await _recording_auth_headers(session, requests),
    )
    await session.add_verifications([result["verification_id"]])
    return result


async def perturb_recording_replay(run_id: str, params: dict) -> dict:
    from dano.execution.page.replay import perturb_replay

    _strict_recording_params(
        params,
        required={"chain_request_ids", "perturb"},
        optional={"recording_id", "flow_version"},
    )
    request_ids = params["chain_request_ids"]
    if not isinstance(request_ids, list) or not request_ids or not all(isinstance(item, str) and item for item in request_ids):
        raise ToolError("chain_request_ids 必须是非空请求 ID 数组")
    perturb = _recording_replay_overrides(params["perturb"], label="perturb")
    session = _recording_session(run_id, params)
    requests = _find_captured_requests(session, request_ids)
    result = await perturb_replay(
        requests,
        perturb=perturb,
        auth_headers=await _recording_auth_headers(session, requests),
    )
    await session.add_verifications(result["verification_ids"])
    return result


async def verify_recording_dependency(run_id: str, params: dict) -> dict:
    from dano.execution.page.replay import verify_dependency

    _strict_recording_params(
        params,
        required={"link_id"},
        optional={"recording_id", "flow_version"},
    )
    session = _recording_session(run_id, params)
    spec = session.current_flow_spec()
    link_id = str(params["link_id"])
    if not any(str(link.link_id or "") == link_id for link in spec.links):
        # A prior repair may have rejected or replaced the proposed link while
        # the model still holds an older todo. This is a stale task, not an
        # executor failure; refresh the authoritative report instead of
        # spending further turns on an impossible link id.
        return {
            "ok": False,
            "status": "stale_link",
            "link_id": link_id,
            "refresh_required": True,
            "next_tool": "get_validation_report",
            "verification_ids": [],
        }
    requests = _captured_recording_requests(session)
    recorder = getattr(session, "_live_recorder", None)
    storage_state = None
    if recorder is not None and callable(getattr(recorder, "storage_state", None)):
        storage_state = await recorder.storage_state()
    try:
        result = await verify_dependency(
            spec,
            link_id,
            requests,
            auth_headers=await _recording_auth_headers(session, requests),
            storage_state=storage_state,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    await session.add_verifications(result["verification_ids"])
    return result


async def execute_recording_write_with_verify(run_id: str, params: dict) -> dict:
    from dano.execution.page.replay import (
        _validate_assertion_contract,
        execute_write_with_verify,
        verify_existing_write,
    )

    _strict_recording_params(
        params,
        required={"write_step_id", "verify_request_id", "assertion"},
        optional={"recording_id", "flow_version", "cleanup_request_id", "inputs"},
    )
    inputs = params.get("inputs") or {}
    if not isinstance(inputs, dict) or not isinstance(params["assertion"], dict):
        raise ToolError("inputs 和 assertion 必须是对象")
    try:
        # Validation must happen before claim_write_verification: malformed
        # model arguments have not touched the business API and must not burn
        # the step's one real-write opportunity.
        _validate_assertion_contract(params["assertion"])
    except ValueError as exc:
        raise ToolError(f"assertion 契约无效：{exc}") from exc
    session = _recording_session(run_id, params)
    spec = session.current_flow_spec()
    step = next((item for item in spec.steps if item.step_id == str(params["write_step_id"])), None)
    if step is None or (step.method or "").upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        raise ToolError("write_step_id 必须指向当前 FlowSpec 的写步骤")
    write_request_id = str((step.source_meta or {}).get("request_id") or "")
    request_ids = [write_request_id, str(params["verify_request_id"])]
    cleanup_request_id = str(params.get("cleanup_request_id") or "")
    if cleanup_request_id:
        request_ids.append(cleanup_request_id)
    if not write_request_id:
        raise ToolError("写步骤缺少录制 request_id，不能真实执行验证")
    async with session.write_verification_lock(step.step_id):
        existing_attempt = await session.claim_write_verification(step.step_id)
        if existing_attempt is not None:
            records = [
                dict(item)
                for item in (session.current_flow_spec().meta or {}).get("verification_log") or []
                if isinstance(item, dict)
            ]
            existing_record = next((
                item for item in reversed(records)
                if item.get("kind") == "write_execute"
                and str((item.get("subject") or {}).get("write_step_id") or "") == step.step_id
            ), None)
            if existing_record is None:
                raise ToolError(
                    f"写步骤 {step.step_id} 已执行过真实验证且未形成可复用证据，禁止重复执行"
                )
            evidence = dict(existing_record.get("evidence") or {})
            verification_id = str(existing_record.get("verification_id") or "")
            previous_write = evidence.get("write") if isinstance(evidence.get("write"), dict) else {}
            write_succeeded = bool(
                previous_write.get("ok")
                and previous_write.get("application_ok") is not False
                and previous_write.get("verification_status") == "passed"
            )
            if existing_record.get("status") != "passed" and write_succeeded:
                requests = _find_captured_requests(session, request_ids)
                storage_state = None
                recorder = getattr(session, "_live_recorder", None)
                if recorder is not None and callable(getattr(recorder, "storage_state", None)):
                    storage_state = await recorder.storage_state()
                result = await verify_existing_write(
                    requests[1],
                    previous_write=previous_write,
                    write_step_id=step.step_id,
                    write_request_id=write_request_id,
                    inputs=inputs,
                    assertion=params["assertion"],
                    auth_headers=await _recording_auth_headers(session, requests),
                    storage_state=storage_state,
                )
                await session.add_verifications(result["verification_ids"])
                await session.finish_write_verification(
                    step.step_id,
                    status="succeeded",
                    verification_id=str(result.get("verification_id") or ""),
                )
                return {
                    **result,
                    "duplicate": False,
                    "write_executed": False,
                    "readback_retried": True,
                }
            return {
                "ok": existing_record.get("status") == "passed",
                "write": deepcopy(evidence.get("write")),
                "verify": deepcopy(evidence.get("verify")),
                "assertion": deepcopy(evidence.get("assertion")),
                "cleanup": deepcopy(evidence.get("cleanup")),
                "verification_id": verification_id,
                "verification_ids": [verification_id],
                "verify_verification_id": verification_id,
                "duplicate": True,
                "write_executed": False,
                "readback_retried": False,
            }

        try:
            requests = _find_captured_requests(session, request_ids)
            storage_state = None
            recorder = getattr(session, "_live_recorder", None)
            if recorder is not None and callable(getattr(recorder, "storage_state", None)):
                storage_state = await recorder.storage_state()
            result = await execute_write_with_verify(
                requests[0],
                requests[1],
                write_step_id=step.step_id,
                inputs=inputs,
                assertion=params["assertion"],
                auth_headers=await _recording_auth_headers(session, requests),
                cleanup_request=requests[2] if cleanup_request_id else None,
                storage_state=storage_state,
            )
            await session.add_verifications(result["verification_ids"])
        except BaseException:
            await session.finish_write_verification(step.step_id, status="failed")
            raise
        write_result = result.get("write") if isinstance(result.get("write"), dict) else {}
        failed_before_write = bool(
            result.get("ok") is False
            and write_result.get("verification_status") == "failed"
            and write_result.get("application_ok") is False
        )
        await session.finish_write_verification(
            step.step_id,
            status="failed_before_write" if failed_before_write else "succeeded",
            verification_id=str(result.get("verification_id") or ""),
        )
        return {
            **result,
            "duplicate": False,
            "write_executed": True,
            "readback_retried": False,
        }


async def browser_recording_navigate(run_id: str, params: dict) -> dict:
    _strict_recording_params(params, required={"url"}, optional={"recording_id", "flow_version"})
    return await _recording_session(run_id, params).browser_navigate(str(params["url"]))


async def browser_recording_snapshot(run_id: str, params: dict) -> dict:
    _strict_recording_params(params, required=set(), optional={"recording_id", "flow_version"})
    return await _recording_session(run_id, params).browser_snapshot()


async def _browser_recording_act(run_id: str, params: dict, kind: str) -> dict:
    _strict_recording_params(
        params,
        required={"locator"},
        optional={"recording_id", "flow_version", "value"},
    )
    if not isinstance(params["locator"], dict):
        raise ToolError("locator 必须是 role+name 或 text 的对象")
    if kind in {"fill", "select"} and "value" not in params:
        raise ToolError(f"browser_{kind} 需要 value")
    return await _recording_session(run_id, params).browser_act(
        kind,
        params["locator"],
        params.get("value"),
    )


async def browser_recording_click(run_id: str, params: dict) -> dict:
    return await _browser_recording_act(run_id, params, "click")


async def browser_recording_fill(run_id: str, params: dict) -> dict:
    return await _browser_recording_act(run_id, params, "fill")


async def browser_recording_select(run_id: str, params: dict) -> dict:
    return await _browser_recording_act(run_id, params, "select")


async def list_link_candidates(run_id: str, params: dict) -> dict:
    from dano.execution.page.value_tracing import discover_value_links

    _strict_recording_params(params, required=set(), optional={"recording_id", "flow_version"})
    session = _recording_session(run_id, params)
    return {"candidates": discover_value_links(_captured_recording_requests(session))}


async def get_recording_verification(run_id: str, params: dict) -> dict:
    from dano.execution.page.verification_log import find_verification

    _strict_recording_params(params, required={"verification_id"}, optional={"recording_id", "flow_version"})
    session = _recording_session(run_id, params)
    spec = session.current_flow_spec()
    record = find_verification(
        str(params["verification_id"]),
        list((spec.meta or {}).get("verification_log") or []),
    )
    return {"verification": record}


def _normalize_strict_recording_plan_submission(raw_plan: dict, spec) -> dict:  # noqa: ANN001
    """Copy the validated plan and resolve unambiguous live request aliases.

    The TypeBox boundary and ``_validate_strict_recording_plan`` own shape
    validation.  This adapter must not infer memberships, add legacy semantic
    axes, or generate deterministic capabilities after that validation. During
    live analysis there may not be materialized steps yet; ``step_<request_id>``
    is therefore normalized only when its suffix exactly identifies a captured
    request and no real step already owns the submitted identifier.
    """
    semantic = deepcopy(raw_plan.get("semantic_plan") or {})
    fact_request_ids = {
        str(item.request_id or "")
        for item in getattr(getattr(spec, "request_facts", None), "requests", [])
        if str(item.request_id or "")
    }
    materialized_step_ids = {
        str(item.step_id or "")
        for item in getattr(spec, "steps", [])
        if str(item.step_id or "")
    }

    def live_request_id(value: object) -> str:
        identifier = str(value or "").strip()
        if identifier in fact_request_ids or identifier in materialized_step_ids:
            return identifier
        if identifier.startswith("step_"):
            candidate = identifier.removeprefix("step_")
            if candidate in fact_request_ids:
                return candidate
        return identifier

    for capability in semantic.get("capabilities") or []:
        if not isinstance(capability, dict):
            continue
        capability["anchor_step_id"] = live_request_id(capability.get("anchor_step_id"))
        for ref in capability.get("request_refs") or []:
            if isinstance(ref, dict):
                ref["step_id"] = live_request_id(ref.get("step_id"))
    operations = deepcopy(raw_plan.get("ops") or [])
    submission = {
        "semantic_plan": semantic,
        "ops": operations,
    }
    if "_submitted_semantic_keys" in raw_plan:
        submission["_submitted_semantic_keys"] = deepcopy(
            raw_plan.get("_submitted_semantic_keys") or []
        )
    return submission


_STRICT_SEMANTIC_PLAN_KEYS = {
    "business_understanding", "capabilities", "unresolved_items",
}
_STRICT_BUSINESS_UNDERSTANDING_KEYS = {
    "business_name", "summary", "intent", "object", "purpose",
}
_STRICT_CAPABILITY_KEYS = {
    "name", "title", "kind", "anchor_step_id", "request_refs",
}
_STRICT_REQUEST_REF_KEYS = {"step_id", "usage"}
_STRICT_REQUEST_USAGES = {"execute", "preflight", "option_source", "fact_check"}
_STRICT_UNRESOLVED_KEYS = {
    "type", "title", "description", "reason", "status", "severity", "blocking",
    "request_id", "step_id", "wire_path", "evidence_refs",
}
_STRICT_RECORDING_PLAN_KEYS = {
    "semantic_plan", "ops", "_submitted_semantic_keys",
}
_TYPED_RECORDING_OPERATION_NAMES = {
    "set_goal", "set_request_role", "set_param_source", "set_param_type", "set_param_required",
    "set_param_enum", "rename_field", "propose_dependency", "add_pitfall",
    "confirm_dependency", "bind_verify_read", "attach_enum_options", "mark_unverified",
}
_TYPED_RECORDING_OPERATION_KEYS = {
    "set_goal": {"op", "goal"},
    "set_request_role": {"op", "request_id", "role", "reason", "evidence_refs", "confidence"},
    "set_param_source": {
        "op", "request_id", "step_id", "wire_path", "source_kind", "origin_request_id",
        "origin_path", "context_key", "session_key", "strategy", "start_field", "end_field", "output_key",
        "reason", "evidence_refs",
    },
    "set_param_type": {
        "op", "request_id", "step_id", "wire_path", "business_type", "reason",
        "evidence_refs",
    },
    "set_param_required": {
        "op", "request_id", "step_id", "wire_path", "required", "reason", "evidence_refs",
    },
    "set_param_enum": {
        "op", "request_id", "step_id", "wire_path", "dictionary_source", "options",
        "reason", "evidence_refs",
    },
    "rename_field": {
        "op", "request_id", "step_id", "wire_path", "label", "reason", "evidence_refs",
    },
    "propose_dependency": {
        "op", "link_id", "kind", "source_request_id", "source_path", "target_request_id",
        "target_step_id", "target_path", "reason", "confidence", "evidence",
        "source_collection_path", "source_key_path", "source_label_path",
        "target_container_path", "value_binding",
    },
    "add_pitfall": {"op", "text", "evidence_ref"},
    "confirm_dependency": {"op", "link_id", "verification_id"},
    "bind_verify_read": {
        "op", "write_step_id", "read_request_id", "verification_id", "assertion",
    },
    "attach_enum_options": {
        "op", "request_id", "step_id", "wire_path", "source_request_id",
        "verification_id", "options",
    },
    "mark_unverified": {"op", "target_kind", "target_id", "reason"},
}


def _canonicalize_recording_plan_aliases(raw_plan: dict) -> dict:
    """Normalize harmless model transport drift without changing facts.

    Pi occasionally emits descriptive metadata or aliases that carry no
    executable authority.  Canonicalize only representations whose meaning is
    already explicit; unknown contract mutations still fail strict validation.
    """
    plan = deepcopy(raw_plan)
    semantic = plan.get("semantic_plan")
    if isinstance(semantic, dict):
        understanding = semantic.get("business_understanding")
        if isinstance(understanding, str):
            understanding = {"summary": understanding}
            semantic["business_understanding"] = understanding
        if isinstance(understanding, dict):
            # Risk is derived from captured request facts and write semantics;
            # a model annotation here has never been an executable field.
            understanding.pop("risk_level", None)
        capabilities = semantic.get("capabilities")
        if isinstance(capabilities, list):
            for capability in capabilities:
                if not isinstance(capability, dict):
                    continue
                anchor = str(capability.get("anchor_step_id") or "").strip()
                refs = capability.get("request_refs")
                if not refs and anchor:
                    capability["request_refs"] = [{"step_id": anchor, "usage": "execute"}]
                    continue
                if not anchor or not isinstance(refs, list):
                    continue
                # The explicit public anchor is authoritative. Pi sometimes
                # repeats a supporting request as a second execute member;
                # preserve that observed member as preflight so the strict
                # one-anchor contract can compile without another model turn.
                for ref in refs:
                    if (
                        isinstance(ref, dict)
                        and str(ref.get("usage") or "") == "execute"
                        and str(ref.get("step_id") or "").strip() != anchor
                    ):
                        ref["usage"] = "preflight"

    field_operations = {
        "set_param_source", "set_param_type", "set_param_required",
        "set_param_enum", "rename_field", "attach_enum_options",
    }
    evidence_ref_operations = {
        "set_request_role", "set_param_source", "set_param_type",
        "set_param_required", "set_param_enum", "rename_field",
    }
    operations = plan.get("ops")
    if isinstance(operations, list):
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            kind = str(operation.get("op") or "")
            if kind in field_operations and not operation.get("wire_path") and operation.get("path"):
                operation["wire_path"] = operation.pop("path")
            if kind not in evidence_ref_operations or "evidence" not in operation:
                continue
            raw_evidence = operation.pop("evidence")
            if operation.get("evidence_refs"):
                continue
            if isinstance(raw_evidence, (str, dict)):
                raw_evidence = [raw_evidence]
            if isinstance(raw_evidence, list):
                refs = [
                    str(item.get("ref") or item.get("source") or "")
                    if isinstance(item, dict) else str(item)
                    for item in raw_evidence
                    if item not in (None, "", {})
                ]
                if any(refs):
                    operation["evidence_refs"] = [ref for ref in refs if ref]
    return plan


def _validate_typed_recording_operations(operations: object, *, label: str) -> None:
    if not isinstance(operations, list) or any(not isinstance(op, dict) for op in operations):
        raise ToolError(f"{label} 必须是对象数组")
    for index, operation in enumerate(operations):
        kind = str(operation.get("op") or "")
        if kind not in _TYPED_RECORDING_OPERATION_NAMES:
            raise ToolError(f"{label}[{index}] 操作未在强类型契约中声明: {kind or '<empty>'}")
        unknown_keys = sorted(set(operation).difference(_TYPED_RECORDING_OPERATION_KEYS[kind]))
        if unknown_keys:
            raise ToolError(
                f"{label}[{index}] 包含未知字段: " + ", ".join(unknown_keys)
            )
        if kind in {
            "set_param_source", "set_param_type", "set_param_required", "set_param_enum", "rename_field",
            "attach_enum_options",
        }:
            if not str(operation.get("request_id") or operation.get("step_id") or ""):
                raise ToolError(f"{label}[{index}] 字段操作缺少 request_id 或 step_id")
            if not str(operation.get("wire_path") or ""):
                raise ToolError(f"{label}[{index}] 字段操作必须使用 wire_path")
        if kind == "propose_dependency" and operation.get("kind") == "response_key_map":
            required = {
                "source_request_id", "source_collection_path", "source_key_path",
                "source_label_path", "target_container_path", "value_binding", "evidence",
            }
            missing = sorted(key for key in required if not operation.get(key))
            binding = operation.get("value_binding")
            if missing:
                raise ToolError(f"{label}[{index}] response_key_map 缺少字段: {', '.join(missing)}")
            if not isinstance(binding, dict) or binding.get("kind") != "caller_map_by_label" or not binding.get("input_field"):
                raise ToolError(
                    f"{label}[{index}] response_key_map.value_binding 必须声明 caller_map_by_label/input_field"
                )


def _validate_strict_recording_plan(raw_plan: dict) -> None:
    unknown_plan_keys = sorted(set(raw_plan).difference(_STRICT_RECORDING_PLAN_KEYS))
    if unknown_plan_keys:
        if "flow_spec" in unknown_plan_keys:
            raise ToolError("plan 格式错误：禁止提交 flow_spec")
        raise ToolError("plan 包含禁止或未知字段：" + ", ".join(unknown_plan_keys))
    semantic = raw_plan.get("semantic_plan")
    if semantic is None:
        semantic = {}
    if not isinstance(semantic, dict):
        raise ToolError("plan.semantic_plan 必须是对象")
    unknown = sorted(set(semantic).difference(_STRICT_SEMANTIC_PLAN_KEYS))
    if unknown:
        raise ToolError("plan.semantic_plan 包含禁止或未知字段：" + ", ".join(unknown))

    understanding = semantic.get("business_understanding", {})
    if not isinstance(understanding, dict):
        raise ToolError("plan.semantic_plan.business_understanding 必须是对象")
    unknown_understanding = sorted(
        set(understanding).difference(_STRICT_BUSINESS_UNDERSTANDING_KEYS)
    )
    if unknown_understanding:
        raise ToolError(
            "business_understanding 包含未知字段：" + ", ".join(unknown_understanding)
        )

    capabilities = semantic.get("capabilities", [])
    if not isinstance(capabilities, list):
        raise ToolError("plan.semantic_plan.capabilities 必须是数组")
    from dano.execution.page.flow_spec import ALLOWED_CAPABILITY_KINDS
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, dict):
            raise ToolError(f"capabilities[{index}] 必须是对象")
        unknown_capability = sorted(set(capability).difference(_STRICT_CAPABILITY_KEYS))
        if unknown_capability:
            raise ToolError(
                f"capabilities[{index}] 包含禁止或未知字段：" + ", ".join(unknown_capability)
            )
        for key in ("name", "title", "anchor_step_id"):
            if not str(capability.get(key) or "").strip():
                raise ToolError(f"capabilities[{index}].{key} 必填")
        if str(capability.get("kind") or "") not in ALLOWED_CAPABILITY_KINDS:
            raise ToolError(f"capabilities[{index}].kind 不是允许的能力枚举")
        refs = capability.get("request_refs")
        if not isinstance(refs, list) or not refs:
            raise ToolError(f"capabilities[{index}].request_refs 必须是非空数组")
        for ref_index, ref in enumerate(refs):
            if not isinstance(ref, dict) or set(ref).difference(_STRICT_REQUEST_REF_KEYS):
                raise ToolError(f"capabilities[{index}].request_refs[{ref_index}] 格式错误")
            if not str(ref.get("step_id") or "") or str(ref.get("usage") or "") not in _STRICT_REQUEST_USAGES:
                raise ToolError(f"capabilities[{index}].request_refs[{ref_index}] 缺少有效 step_id/usage")
        execute_refs = [
            ref for ref in refs
            if isinstance(ref, dict) and str(ref.get("usage") or "") == "execute"
        ]
        if (
            len(execute_refs) != 1
            or str(execute_refs[0].get("step_id") or "").strip()
            != str(capability.get("anchor_step_id") or "").strip()
        ):
            raise ToolError(
                f"capabilities[{index}] 的唯一 execute 必须等于 anchor_step_id"
            )

    unresolved = semantic.get("unresolved_items", [])
    if not isinstance(unresolved, list):
        raise ToolError("plan.semantic_plan.unresolved_items 必须是数组")
    for index, item in enumerate(unresolved):
        if not isinstance(item, dict) or set(item).difference(_STRICT_UNRESOLVED_KEYS):
            raise ToolError(f"unresolved_items[{index}] 格式错误")
        if not str(item.get("type") or ""):
            raise ToolError(f"unresolved_items[{index}].type 必填")
    _validate_typed_recording_operations(raw_plan.get("ops", []), label="plan.ops")


def _require_complete_submitted_semantic_keys(raw_plan: dict) -> None:
    """Validate incremental transport metadata without forcing empty sections."""
    submitted = raw_plan.get("_submitted_semantic_keys")
    if submitted is None:
        return
    if not isinstance(submitted, list):
        raise ToolError("plan._submitted_semantic_keys 必须是数组")
    if any(not isinstance(key, str) for key in submitted):
        raise ToolError("plan._submitted_semantic_keys 只能包含字符串")


async def submit_recording_plan(run_id: str, params: dict) -> dict:
    _strict_recording_params(
        params,
        required={"base_flow_version"},
        optional={"recording_id", "flow_version", "plan", "submission_error"},
    )
    session = _recording_session(run_id, params)
    refresh_live_evidence = getattr(session, "refresh_live_evidence", None)
    if callable(refresh_live_evidence):
        await refresh_live_evidence()
    raw_plan = params.get("plan")
    if not isinstance(raw_plan, dict):
        if params.get("submission_error") == "model_output_truncated_missing_plan":
            return await session.accept_unchanged_plan(
                base_flow_version=params["base_flow_version"],
                warning=(
                    "结构化计划在模型输出上限前未完成，本次已停止重试；"
                    "当前配置未修改"
                ),
            )
        raise ToolError("plan 必须是对象")
    raw_plan = _canonicalize_recording_plan_aliases(raw_plan)
    _validate_strict_recording_plan(raw_plan)
    _require_complete_submitted_semantic_keys(raw_plan)
    submission = _normalize_strict_recording_plan_submission(
        raw_plan, session.current_flow_spec()
    )
    submission.setdefault("submission_id", str(uuid4()))
    return await _apply_recording_submission_atomic(
        session,
        submission,
        mode="plan",
        base_flow_version=params["base_flow_version"],
    )


async def get_validation_report(run_id: str, params: dict) -> dict:
    _strict_recording_params(params, required=set(), optional={"recording_id", "flow_version"})
    return await _recording_session(run_id, params).get_validation_report()


async def submit_recording_repair(run_id: str, params: dict) -> dict:
    _strict_recording_params(
        params,
        required={"base_flow_version", "operations"},
        optional={"recording_id", "flow_version"},
    )
    operations = params.get("operations")
    _validate_typed_recording_operations(operations, label="operations")
    session = _recording_session(run_id, params)
    refresh_live_evidence = getattr(session, "refresh_live_evidence", None)
    if callable(refresh_live_evidence):
        await refresh_live_evidence()
    return await _apply_recording_submission_atomic(
        session,
        {"ops": operations, "submission_id": str(uuid4())},
        mode="repair",
        base_flow_version=params["base_flow_version"],
    )


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
    "get_recording_state": get_recording_state,
    "get_recording_delta": get_recording_delta,
    "ask_operator": ask_recording_operator,
    "replay_request": replay_recording_request,
    "perturb_replay": perturb_recording_replay,
    "verify_dependency": verify_recording_dependency,
    "execute_write_with_verify": execute_recording_write_with_verify,
    "browser_navigate": browser_recording_navigate,
    "browser_snapshot": browser_recording_snapshot,
    "browser_click": browser_recording_click,
    "browser_fill": browser_recording_fill,
    "browser_select": browser_recording_select,
    "list_link_candidates": list_link_candidates,
    "get_verification": get_recording_verification,
    "submit_recording_plan": submit_recording_plan,
    "get_validation_report": get_validation_report,
    "submit_recording_repair": submit_recording_repair,
    "request_review": request_review,
    "publish_asset": publish_asset,
    "self_check_recording": self_check_recording,
}
