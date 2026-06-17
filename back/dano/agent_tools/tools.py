"""pi 自定义工具的 Python 实现(确定性能力)。

红线:
- sandbox_test/write_readback/health_check 一律 environment=sandbox + credential_type=test,绝不碰生产写。
- publish_asset 走 Phase 1 的 verify_publishable 硬关卡:只认后端生成的证据,不信 agent 自报。
凭证只在进程内(materials),绝不进 LLM 上下文。
"""

from __future__ import annotations

from uuid import UUID

import structlog

from dano.agent_tools import materials
from dano.assets.drafts import REVIEW_REQUIRED_TYPES, DraftStore
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


def set_review_board(board) -> None:  # noqa: ANN001 —— 测试注入 fake 评审委员会
    global _review_board
    _review_board = board


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
            headers = {"Authorization": f"Bearer {token}"}
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
    sid = params["system_instance_id"]
    mat = _mat(run_id, sid)
    spec = mat.openapi or {}
    template = oa_templates.match_template(spec)
    extra = template.infrastructure_patterns() if template else ()
    include = {t for t in (params.get("include_tags") or mat.include_tags or [])}
    actions, categories = [], {}
    for a in doc_parser.parse_openapi(spec):
        role = endpoint_classifier.classify(a, extra_infra=extra)
        if role == endpoint_classifier.INFRASTRUCTURE:
            continue
        for t in (a.tags or ["(未分类)"]):              # 类别统计(供前端按 tag 选)
            categories[t] = categories.get(t, 0) + 1
        if include and not (set(a.tags) & include):     # 类别白名单:超大 swagger 圈定范围
            continue
        actions.append({"name": a.name, "method": a.method, "endpoint": a.endpoint,
                        "role": role, "required_in": a.required_in, "params_in": a.params_in,
                        "params_out": a.params_out, "tags": a.tags,   # 出参/标签:供发现流程依赖
                        "summary": a.summary, "field_docs": a.field_docs})
    return {"system_instance_id": sid, "template": template.name if template else None,
            "success_rule": template.success_rule() if template else None,
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
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in (ops.items() if isinstance(ops, dict) else []):
            if isinstance(op, dict) and (op.get("operationId") == action_name):
                req = (op.get("requestBody", {}).get("content", {})
                       .get("application/json", {}).get("schema"))
                resp = None
                for code, r in (op.get("responses", {}) or {}).items():
                    if str(code).startswith("2") and isinstance(r, dict):
                        resp = r.get("content", {}).get("application/json", {}).get("schema")
                        break
                return {"action": action_name, "method": method.upper(), "endpoint": path,
                        "request_schema": _resolve_tree(spec, req) if req else None,
                        "response_schema": _resolve_tree(spec, resp) if resp else None,
                        "request_example": _first_example(op)}
    raise ToolError(f"接口里无此动作: {action_name}")


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
async def draft_workflow(run_id: str, params: dict) -> dict:
    from dano.capabilities import oa_templates
    from dano.shared.asset_bodies import WorkflowSkillBody, WorkflowStep
    sid = params["system_instance_id"]
    mat = _mat(run_id, sid)
    steps = [WorkflowStep(action=s["action"], inputs=s.get("inputs", {})) for s in params["steps"]]
    # 每个步骤动作须已发布连接器(否则运行期无法执行)
    scope = Scope(tenant=mat.tenant, subsystem=Subsystem(mat.subsystem))
    for st in steps:
        if await _repo.get_published(AssetType.CONNECTOR, scope, asset_key=st.action) is None:
            raise ToolError(f"步骤连接器未发布,不能编排进流程: {st.action}")
    tmpl = oa_templates.match_template(mat.openapi or {})
    # 契约自洽:steps 里所有 field:X 引用到的字段,都并入 required_fields/user_fields
    # (防"用了却没声明必填"导致运行时缺字段失败)
    used_fields = {v[len("field:"):] for s in steps for v in s.inputs.values()
                   if isinstance(v, str) and v.startswith("field:")}
    required_fields = sorted(set(params.get("required_fields", [])) | used_fields)
    user_fields = sorted(set(params.get("user_fields", [])) | used_fields)
    body = WorkflowSkillBody(
        action=params["action"], title=params.get("title", params["action"]),
        steps=steps, user_fields=user_fields, required_fields=required_fields,
        success_rule=tmpl.success_rule() if tmpl else None,
    )
    validate_asset_body(AssetType.WORKFLOW, body.model_dump())
    draft = await _ds.save_draft(run_id=run_id, scope=scope, asset_type=AssetType.WORKFLOW,
                                 asset_key=body.action, body=body.model_dump())
    return {"asset_draft_id": str(draft.asset_draft_id), "action": body.action,
            "steps": [s.action for s in steps]}


# ── 复合流程整条 dry-run(测试账号按序真跑,记 sandbox 证据)──
async def sandbox_test_workflow(run_id: str, params: dict) -> dict:
    from dano.execution.connectors.executor import RealActionExecutor, SystemEndpoint, system_key_for
    from dano.orchestrator.orchestrator import _resolve_step_inputs
    from dano.shared.asset_bodies import AuthConfig, ConnectorBody, WorkflowSkillBody
    draft = await _ds.get_draft(UUID(params["asset_draft_id"]))
    if draft is None or draft.asset_type != AssetType.WORKFLOW:
        raise ToolError("sandbox_test_workflow 仅用于复合流程草案")
    wf = WorkflowSkillBody.model_validate(draft.body)
    mat = _mat(run_id, draft.subsystem.value)
    deploy = mat.deploy or {}
    sub = Subsystem(mat.subsystem)
    endpoints = {system_key_for(sub): SystemEndpoint(
        base_url=deploy.get("base_url", ""), auth=AuthConfig.model_validate(deploy.get("auth", {})))}
    execu = RealActionExecutor(endpoints=endpoints, auth_manager=AuthManager())
    rule = wf.success_rule
    user_fields = params.get("test_input", {})    # 流程级测试输入(测试账号,非生产)
    step_outputs: dict = {}
    trace, ok_all = [], True
    for step in wf.steps:
        env = await _repo.get_published(AssetType.CONNECTOR,
                                        Scope(tenant=mat.tenant, subsystem=sub), asset_key=step.action)
        connector = ConnectorBody.model_validate(env.body)
        body = _resolve_step_inputs(step.inputs, user_fields, step_outputs)
        try:
            resp = await execu.execute(connector.model_dump(), body, mat.credentials)
        except Exception as e:  # noqa: BLE001
            ok_all = False
            trace.append({"step": step.action, "error": str(e)}); break
        ok = 200 <= resp.http < 300
        if ok and rule:
            from dano.shared.expr import safe_eval
            try:
                ok = bool(safe_eval(rule, {"response": resp.body, "http": resp.http}))
            except Exception:  # noqa: BLE001
                ok = False
        step_outputs[step.action] = resp.body
        trace.append({"step": step.action, "http": resp.http, "ok": ok})
        ok_all = ok_all and ok
        if not ok:
            break
    v = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="sandbox",
                                    passed=ok_all, evidence={"trace": trace})
    return {"passed": ok_all, "validation_run_ids": [str(v.validation_run_id)], "trace": trace}


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
                                success_rule=success_rule)
    validate_asset_body(AssetType.CONNECTOR, body.model_dump())
    draft = await _ds.save_draft(run_id=run_id, scope=Scope(tenant=mat.tenant, subsystem=Subsystem(mat.subsystem)),
                                 asset_type=AssetType.CONNECTOR, asset_key=action_name, body=body.model_dump())
    return {"asset_draft_id": str(draft.asset_draft_id), "content_hash": draft.content_hash,
            "action": action_name, "risk_level": body.risk_level.value}


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
    sample = params.get("sample_inputs") or {}
    act = await sb.run_action(draft.body, inputs=sample)
    resp_body = (act.evidence or {}).get("response")
    sandbox_passed = act.passed and _action_business_ok(draft.body, resp_body)   # HTTP + 业务码双关
    v1 = await _ds.record_validation(asset_draft_id=draft.asset_draft_id, kind="connect",
                                     passed=conn.passed, evidence=conn.evidence)
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
    review_run_ids, out = [], []
    for v in verdicts:
        rr = await _ds.record_review(asset_draft_id=draft.asset_draft_id, role=v.role,
                                     model_id=v.model_id, passed=v.passed, reasons=v.reasons)
        review_run_ids.append(str(rr.review_run_id))
        out.append({"role": v.role, "model": v.model_id, "passed": v.passed, "reasons": v.reasons})
    all_passed = bool(verdicts) and all(v.passed for v in verdicts)
    log.info("request_review", draft=str(draft.asset_draft_id), all_passed=all_passed)
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


async def sandbox_test_adapter(run_id: str, params: dict) -> dict:
    """隔离 runner 跑适配器(测试账号),按 success_rule 判成败,记 sandbox 证据。

    二态:run.ok 且(无 success_rule 或表达式为真)→ passed;失败给结构化 reasons 供驳回重写。
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
    if not res.ok:
        reasons.append(f"运行失败: {res.error}")
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
    "draft_policy": draft_policy,
    "test_policy_cases": test_policy_cases,
    "request_review": request_review,
    "publish_asset": publish_asset,
    "draft_adapter": draft_adapter,
    "sandbox_test_adapter": sandbox_test_adapter,
    "vuln_scan": vuln_scan,
}
