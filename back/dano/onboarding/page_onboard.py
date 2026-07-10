"""录制模式 V2 的抓请求发布入口。"""

from __future__ import annotations

from uuid import uuid4

import structlog

from dano.agent_tools import materials

log = structlog.get_logger(__name__)


async def _advisory_notes(action: str, api_request: dict) -> list[str]:
    """录制 skill 的**非阻断**语义顾问:仅当评审 client 已注入(生产启动注入;测试默认无)才跑。
    任何失败/未配置都返回 [] —— 顾问绝不阻断发布。"""
    try:
        from dano.agent_tools import tools as T
        board = T._review_board
        if board is None:
            return []
        from dano.review.board import advisory_capture_review
        return await advisory_capture_review(board.client, (getattr(board, "models", None) or {}).get("acceptance"),
                                             action=action, api_request=api_request)
    except Exception:  # noqa: BLE001
        return []


async def _auto_goal(action: str, api_request: dict) -> dict:
    """LLM 就绪时自动提炼业务 Goal(随资产存档);未注入/失败 → {}。提议性质,不因 LLM 抖动阻断发布。"""
    try:
        from dano.agent_tools import tools as T
        board = T._review_board
        if board is None:
            return {}
        from dano.review.board import generate_goal
        return await generate_goal(board.client, (getattr(board, "models", None) or {}).get("acceptance"),
                                   action=action, api_request=api_request)
    except Exception:  # noqa: BLE001
        return {}


def _focus_question(action: str, findings: list) -> str:
    """把剩余 findings 聚成**一个**精准问题(而非一长串):取最关键一条,提示还有几项,确认完接着问。"""
    items = [(f.get("detail") if isinstance(f, dict) else str(f)) for f in (findings or [])]
    items = [s for s in items if s]
    if not items:
        return f"为完成「{action}」,请补充缺失信息。"
    q = f"为完成「{action}」,请先确认:{items[0]}"
    return q + (f"(确认后还有 {len(items) - 1} 项)" if len(items) > 1 else "")


def _build_page_body(api_request: dict, action: str, title: str, required):
    """从 api_request 算 params/必填/类型并建 PageScriptBody(修复后参数会变,故抽出可重算重建)。"""
    from dano.shared.asset_bodies import PageScriptBody
    from dano.shared.enums import IngestionStatus, RiskLevel
    params = list(api_request.get("params") or [])
    if not params and api_request.get("steps"):
        params = list((api_request["steps"][-1] or {}).get("params") or [])
    req_fields = [r for r in (required if required is not None else params) if r in params]
    opt_fields = [p for p in params if p not in req_fields]
    ftypes = dict(api_request.get("field_types") or {})
    if not ftypes and api_request.get("steps"):
        ftypes = dict((api_request["steps"][-1] or {}).get("field_types") or {})
    recording_mode = str(api_request.get("recording_mode") or "unknown")
    has_fact_check = bool(api_request.get("fact_check") or any((s or {}).get("fact_check") for s in api_request.get("steps") or []))
    has_success_rule = bool(api_request.get("success_rule") or any((s or {}).get("success_rule") for s in api_request.get("steps") or []))
    verification_basis = (
        "fact_check_configured" if has_fact_check else
        "success_rule_configured" if has_success_rule else
        "structure_only"
    )
    body = PageScriptBody(actions=[], dom_fingerprint="", action=action, title=title, api_request=api_request,
                          user_fields=params, required_fields=req_fields, optional_fields=opt_fields,
                          field_types=ftypes, risk_level=RiskLevel.L3,
                          recording_mode=recording_mode,
                          verification_status=IngestionStatus.PARTIALLY_VERIFIED.value,
                          verification_basis=verification_basis,
                          capabilities=list(api_request.get("capabilities") or [])).model_dump()
    return body, params, req_fields, opt_fields


def _sync_goal_required_inputs(goal: dict | None, api_request: dict) -> dict | None:
    """让发布闸门看到的 Goal.required_inputs 与实际 api_request 参数一致。

    录制工作台允许用户把 `type` 改成 `类型`。如果 goal 仍保留旧 key，validate_goal 会
    把它当成“无来源项/LLM 臆造”阻断发布。这里以 api_request 为事实源同步一次。
    """
    if not goal:
        return goal
    params = list(api_request.get("params") or [])
    for step in api_request.get("steps") or []:
        for name in step.get("params") or []:
            if name not in params:
                params.append(name)
    if not params:
        return goal
    out = dict(goal)
    required_inputs = [name for name in (out.get("required_inputs") or []) if name in params]
    for name in params:
        if name not in required_inputs:
            required_inputs.append(name)
    out["required_inputs"] = required_inputs
    return out


async def run_request_onboarding(
    *, tenant: str, subsystem: str, action: str, title: str = "",
    api_request: dict, sample_inputs: dict | None = None, required: list[str] | None = None,
    deploy: dict | None = None, credentials: dict | None = None, run_id: str | None = None,
    goal: dict | None = None, storage_state: dict | None = None,
    allow_repair: bool = True,
) -> dict:
    """抓请求路径:把录制抓到的提交请求(已参数化)落成可执行 Skill → dry 自检 → 三模型评审+自动修复 → 发布。

    self_check 不真发(写安全);运行期 invoke 时才带登录态真发。
    写抓请求页面**须过三模型评审**(发布层硬闸门,见 verify_reviewed):评审 client(_review_board)由网关启动注入;
    审核出 findings → 可选 LLM 自动修复循环。录制工作台发布传 allow_repair=False，
    保证发布产物与用户确认版本一致；问题会原样返回工作台处理。
    """
    from dano.agent_tools import tools as T
    from dano.shared.asset_bodies import PageScriptBody
    from dano.shared.enums import IngestionStatus, RiskLevel

    run_id = run_id or f"req-{uuid4().hex[:8]}"
    sid = subsystem
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant=tenant, system_instance_id=sid, subsystem=sid,
        deploy=deploy or {}, credentials=credentials or {}))
    try:
        from dano.infra.logging import configure_logging
        configure_logging()                                   # 幂等:直连/离线调用也能看到日志
        structlog.contextvars.bind_contextvars(run_id=run_id, action=action, tenant=tenant, subsystem=str(sid))
        log.info("ingest.start", title=title, has_steps=bool(api_request.get("steps")),
                 method=api_request.get("method"), url=api_request.get("url") or api_request.get("path"))
        # 单请求取自身 params;多步工作流(Q3)取最后一步(用户提交那步)的 params
        params = list(api_request.get("params") or [])
        if not params and api_request.get("steps"):
            params = list((api_request["steps"][-1] or {}).get("params") or [])
        # 没有可参数化的写请求体 → 无法做有意义的自检/真跑 → 诚实标 unsupported,不静默发空 skill
        if not (api_request.get("body_template") or api_request.get("steps")):
            log.warning("ingest.gate.unsupported", reason="no body_template/steps")
            return {"ok": False, "stage": "ingest", "status": IngestionStatus.UNSUPPORTED.value,
                    "action": action, "reason": "没有可参数化的写请求体(无 body_template/steps)—— 无法安全自动化"}
        # 业务相关性门:危险写请求(删除/驳回/终止/撤销)不做自动化录入 → 拒发(避免代他人删单/驳回审批)
        from dano.execution.page.request_capture import classify_request_role, looks_dangerous_write
        log.info("ingest.request_role", **classify_request_role(api_request))   # node 4 语义角色
        if looks_dangerous_write(api_request):
            log.warning("ingest.gate.dangerous_write_rejected",
                        method=api_request.get("method"), url=api_request.get("url") or api_request.get("path"))
            return {"ok": False, "stage": "relevance", "status": IngestionStatus.REJECTED.value,
                    "action": action,
                    "reason": "识别到危险写请求(删除/驳回/终止/撤销)—— 这类不做自动化录入,请人工处理"}
        # 业务 Goal:用户确认的优先;没传则 LLM 就绪时**自动提炼**(随资产存档,Goal 无条件化)。Goal 完整性门:
        #   用户确认的 goal 不过 → 阻断(需澄清);自动提炼的不过 → 仅作建议(不因 LLM 抖动阻断发布)。
        from dano.execution.page.request_capture import goal_needs_confirmation, validate_goal
        user_confirmed = bool(goal)
        if not goal:
            goal = await _auto_goal(action, api_request)
        goal = _sync_goal_required_inputs(goal, api_request)
        goal_issues: list[str] = validate_goal(goal, api_request) if goal else []
        if goal:
            api_request = {**api_request, "goal": goal}
        log.info("ingest.goal", source=("user" if user_confirmed else "auto"),
                 has_goal=bool(goal), intent=(goal or {}).get("intent"), issues=len(goal_issues))
        if user_confirmed and goal_issues:                    # 仅用户确认的 goal 不过才硬拦
            log.warning("ingest.gate.goal_needs_clarify", issues=goal_issues)
            return {"ok": False, "stage": "goal", "status": IngestionStatus.NEEDS_CLARIFICATION.value,
                    "action": action, "clarifications": goal_issues,
                    "reason": "业务 Goal 完整性门未过(意图/必填来源/成功标准/禁止动作/风险)",
                    "goal": goal, "goal_confirmation_required": goal_needs_confirmation(goal)}
        # 编码:算 params/必填/类型并建 body(修复后会重算重建)
        body, params, req_fields, opt_fields = _build_page_body(api_request, action, title, required)
        # 字段语义门:**必填**参数若是内部机器标识(Activity_xxx/hash,非人类名)→ 阻断,让用户命名;可选的仅告警
        from dano.execution.page.request_capture import looks_internal_param_name
        bad_req = [p for p in req_fields if looks_internal_param_name(p)]
        log.info("ingest.fields", params=params, required=req_fields, optional=opt_fields)
        if bad_req:
            log.warning("ingest.gate.field_semantics_needs_clarify", required_internal=bad_req)
            return {"ok": False, "stage": "field_semantics", "status": IngestionStatus.NEEDS_CLARIFICATION.value,
                    "action": action,
                    "clarifications": [f"必填参数 `{p}` 是内部机器标识(非人类名),请在录制界面给它起个业务名"
                                       f"(如审批人/领导)再发布" for p in bad_req],
                    "reason": "字段语义门:必填参数名不可读(内部机器标识),需澄清命名"}
        d = await T.save_draft(run_id, {"system_instance_id": sid, "asset_type": "page_script",
                                        "asset_key": action, "body": body})
        log.info("ingest.draft_saved", draft_id=d.get("asset_draft_id"))
        # 自适应活体验证:仅当环境可逆沙箱 + 有回查手段(plan=live)且带测试登录态,才真发写 + fact_check → 可升 verified
        from dano.execution.page.request_capture import capture_verification_plan
        plan = capture_verification_plan(deploy, api_request)
        do_live = plan.get("mode") == "live" and storage_state is not None
        log.info("ingest.verification_plan", mode=plan.get("mode"),
                 controllability=plan.get("controllability"), do_live=do_live)
        rp = await T.self_check_recording(run_id, {"asset_draft_id": d["asset_draft_id"],
                                                   "sample_inputs": sample_inputs or {},
                                                   "live": do_live, "storage_state": storage_state, "verify": False})
        log.info("ingest.self_check", passed=rp.get("passed"), mode=rp.get("mode"))
        if not rp["passed"]:
            sc = (rp.get("structured_output") or {}).get("self_check") or []
            live = rp.get("live") or {}
            if sc:                                            # 结构自检未过 → 需澄清
                log.warning("ingest.gate.self_check_failed", violations=sc)
                return {"ok": False, "stage": "validate", "status": IngestionStatus.NEEDS_CLARIFICATION.value,
                        "action": action, "clarifications": sc, "reason": "确定性自检未过,需澄清/修正",
                        "detail": rp.get("structured_output")}
            if live and not live.get("ok"):                   # 活体真跑未过业务生效门 → 拒发(不上线坏 skill)
                log.warning("ingest.gate.live_failed", detail=live.get("detail"),
                            fact_check=live.get("fact_check_passed"))
                return {"ok": False, "stage": "live", "status": IngestionStatus.REJECTED.value,
                        "action": action, "reason": "活体真跑未通过业务生效门:" + str(live.get("detail", "")),
                        "live": live, "detail": rp.get("structured_output")}
            log.warning("ingest.gate.validate_failed", reason="leftover placeholders")
            return {"ok": False, "stage": "validate", "status": IngestionStatus.NEEDS_CLARIFICATION.value,
                    "action": action, "clarifications": [], "reason": "请求参数化校验未过(参数没全填上)",
                    "detail": rp.get("structured_output")}
        # LLM 三维审核 + **自动整合修复(repair loop)**:审核出语义 findings + 确定性 findings(会话常量焊死/占位名)
        # → 喂 LLM 出受限修复操作 → 确定性执行 + self_check 复验 → 循环到干净 → 重审核 → 发布;
        # **改不动(信息真缺)才问一个精准问题(非重录)**。修复器/审核 client 没注入(LLM 不可用)则退回原逻辑,不阻断。
        review_run_ids: list = []
        # P2:写抓请求页面发布层**硬要求三模型评审证据**(verify_reviewed)。评审 client 未注入但评审已启用 →
        # 直接 return 可执行指引,**不静默跳过后在 publish 阶段以"缺角色"晦涩失败**(主路径网关启动会注入)。
        from dano.config import get_settings as _get_settings
        if T._review_board is None and _get_settings().review_enabled:
            log.warning("ingest.gate.review_unavailable", review_enabled=True)
            return {"ok": False, "stage": "review", "status": IngestionStatus.NEEDS_CLARIFICATION.value,
                    "action": action,
                    "reason": "评审已启用(review_enabled=true)但未配置审核模型 —— 写操作 skill 过不了发布层三模型评审闸门。"
                              "请配置审核模型(网关启动自动注入;离线直调需先 set_review_board),或运维临时设 "
                              "review_enabled=false 降级发布。"}
        if T._review_board is not None:
            from dano.execution.page.repair_ops import collect_repair_findings
            from dano.onboarding.repair import generate_fix_ops, review_findings, run_repair_loop
            # 注:dry/self_check(录制 by-design 安全模式)的误判否决已在 request_review 内确定性剔除(改 DB 证据),
            # 故此处 verdicts 已是修正后的(评审仅因"未真跑"否决不会误阻断发布)。
            rev = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
            review_run_ids = rev.get("review_run_ids", []) or []
            rev_find = review_findings(rev.get("verdicts"))
            findings = collect_repair_findings(api_request) + rev_find
            log.info("ingest.review", all_passed=rev.get("all_passed"), findings=len(findings))
            board = T._review_board
            client, model = getattr(board, "client", None), (getattr(board, "models", None) or {}).get("acceptance")
            proposer = T._fix_proposer
            if proposer is None and client is not None and model:
                async def proposer(a, f, g, _c=client, _m=model):     # noqa: E306
                    return await generate_fix_ops(_c, _m, goal=g, api_request=a, findings=f)
            if findings and not allow_repair:
                details = [str(f.get("detail") or f.get("message") or f) for f in findings]
                return {
                    "ok": False,
                    "stage": "review",
                    "status": IngestionStatus.NEEDS_CLARIFICATION.value,
                    "action": action,
                    "clarifications": details,
                    "reason": "当前工作台版本未通过发布评审；发布阶段未自动改写，请返回工作台修正",
                }
            if findings and proposer is not None:                     # 有问题 + 有修复器 → 自动修复
                repaired, rounds, _h, remaining = await run_repair_loop(
                    api_request, proposer, goal=goal, seed_findings=rev_find)
                fixed = repaired != api_request and not collect_repair_findings(repaired)
                log.info("ingest.repair", rounds=rounds, remaining=len(remaining), fixed=fixed)
                if fixed:                                             # 修好 → 重建/重存/重自检/重审核
                    api_request = repaired
                    body, params, req_fields, opt_fields = _build_page_body(api_request, action, title, required)
                    d = await T.save_draft(run_id, {"system_instance_id": sid, "asset_type": "page_script",
                                                    "asset_key": action, "body": body})
                    rp = await T.self_check_recording(run_id, {"asset_draft_id": d["asset_draft_id"],
                                                               "sample_inputs": sample_inputs or {}})
                    rev = await T.request_review(run_id, {"asset_draft_id": d["asset_draft_id"]})
                    review_run_ids = rev.get("review_run_ids", []) or []
                    log.info("ingest.repair.revalidated", self_check=rp.get("passed"),
                             review_passed=rev.get("all_passed"))
                    if not rp.get("passed") or not rev.get("all_passed", True):
                        reasons = [f"{v.get('role')}: {r}" for v in (rev.get("verdicts") or [])
                                   if not v.get("passed") for r in (v.get("reasons") or [])]
                        return {"ok": False, "stage": "review", "status": IngestionStatus.NEEDS_CLARIFICATION.value,
                                "action": action, "clarifications": reasons or ["修复后仍未通过,需人工确认"],
                                "reason": "自动修复后仍未通过审核 —— 需澄清(非重录)"}
                else:                                                 # 改不动 → **一个精准问题**(非重录)
                    rem = remaining or findings
                    log.warning("ingest.gate.repair_unresolved", remaining=len(rem))
                    return {"ok": False, "stage": "repair", "status": IngestionStatus.NEEDS_CLARIFICATION.value,
                            "action": action, "question": _focus_question(action, rem),
                            "clarifications": [f.get("detail") for f in rem if f.get("detail")],
                            "reason": "自动修复未能完全解决(无需重录,确认问题即可)"}
            elif not rev.get("all_passed", True):                     # 无修复器 + 审核不过 → 驳回(把理由还回)
                reasons = [f"{v.get('role')}: {r}" for v in (rev.get("verdicts") or [])
                           if not v.get("passed") for r in (v.get("reasons") or ["未通过"])]
                log.warning("ingest.gate.review_rejected", reasons=reasons)
                return {"ok": False, "stage": "review", "status": IngestionStatus.NEEDS_CLARIFICATION.value,
                        "action": action, "question": _focus_question(action, [{"detail": r} for r in reasons]),
                        "clarifications": reasons or ["三模型审核未通过"],
                        "reason": "审核未通过(格式 / 业务逻辑 / 风险合规)"}
        # 发布硬闸门:verify_publishable(self_check 等证据)+ verify_reviewed(capture 仍按既定放行,审核闸门在上方编排层把守)
        pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
                                             "validation_run_ids": rp["validation_run_ids"],
                                             "review_run_ids": review_run_ids})
        # 安全网:**可选**参数里若有"内部机器标识"(必填的已在字段语义门拦下)→ 仅告警(agent 不传时用录制原值)。
        bad = [p for p in opt_fields if looks_internal_param_name(p)]
        warnings = ([f"可选参数 `{p}` 像内部标识(非人类名),建议命名;agent 不传它时用录制原值"
                     for p in bad] if bad else [])
        if warnings:
            log.warning("ingest.warn.optional_internal_names", params=bad)
        published = pub.get("published", False)
        # 结构 + 活体(真跑+fact_check)均过 → verified;只做了 dry 结构验 → partially_verified(诚实降级);发布失败 → rejected
        live_ok = rp.get("mode") == "live" and bool(rp.get("passed"))
        status = (IngestionStatus.REJECTED if not published else
                  IngestionStatus.VERIFIED if live_ok else IngestionStatus.PARTIALLY_VERIFIED)
        log.info("ingest.published", published=published, status=status.value,
                 asset_id=pub.get("asset_id"), live_ok=live_ok, reason=pub.get("reason", ""))
        review_notes = await _advisory_notes(action, api_request) if published else []
        role = classify_request_role(api_request if not api_request.get("steps")
                                     else ((api_request["steps"][-1] or {})))   # node 4 语义角色(确定性)
        return {"ok": published, "stage": "publish", "status": status.value,
                "action": action, "verification_plan": plan, "review_notes": review_notes,
                "request_role": role,
                "goal": goal, "goal_issues": goal_issues,   # 自动提炼 Goal 的完整性问题(建议性,不阻断)
                "asset_id": pub.get("asset_id"), "skill_id": f"{sid}.{action}",
                "verification_status": status.value,
                "verification_basis": body.get("verification_basis", ""),
                "mode": "request", "reason": pub.get("reason", ""),
                "warnings": warnings,
                "api": {"method": api_request.get("method"), "path": api_request.get("path"),
                        "params": params}}
    except Exception:
        log.error("ingest.error", exc_info=True)              # 带 traceback,快速定位崩在哪一步
        raise
    finally:
        materials.clear_run(run_id)
        structlog.contextvars.clear_contextvars()
