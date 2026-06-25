"""确定性页面接入(流程8,无 API,无 LLM)。

复用已验证的页面工具链:scout_page(真侦察)→ draft_page_script(确定性建体)→
sandbox_replay(写页面 dry 回放)→ request_review(写页面三模型评审)→ publish_asset(发布硬闸门)。

与 pi 驱动的 onboard() 物理隔离:本函数不 spawn pi、不碰 LLM,可独立运行/测试。pi 路径(给未知复杂
页面用)走 agent/skills/onboard-page.md + 同一组工具;两者复用同一确定性建体与发布闸门。
"""

from __future__ import annotations

from uuid import uuid4

import structlog

from dano.agent_tools import materials, tools

log = structlog.get_logger(__name__)


async def scout_page_only(
    *, tenant: str, subsystem: str, start_url: str,
    deploy: dict | None = None, credentials: dict | None = None, headless: bool = True,
) -> dict:
    """仅侦察一个页面(不建体/不发布):返回候选字段 / 提交按钮 / 建议步骤 / 结构指纹。

    供前端向导"先预览发现的字段、再确认字段映射"用。无副作用、不落库。
    """
    run_id = f"page-scout-{uuid4().hex[:8]}"
    sid = subsystem
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant=tenant, system_instance_id=sid, subsystem=sid,
        deploy=deploy or {}, credentials=credentials or {}))
    try:
        return await tools.scout_page(run_id, {"system_instance_id": sid, "start_url": start_url,
                                               "headless": headless})
    finally:
        materials.clear_run(run_id)


async def run_page_onboarding(
    *,
    tenant: str,
    subsystem: str,
    start_url: str,
    action: str,
    title: str = "",
    success_marker: str | None = None,
    deploy: dict | None = None,
    credentials: dict | None = None,
    sample_inputs: dict | None = None,
    headless: bool = True,
    run_id: str | None = None,
    steps: list[dict] | None = None,
    dom_fingerprint: str | None = None,
) -> dict:
    """侦察→建体→回放→(写页面评审)→发布。返回报告 dict({ok, action, risk_level, asset_id, mode, reason})。

    诚实失败:任一步不过即停并如实返回 reason/stage,绝不发布未通过的脚本。
    传入 steps + dom_fingerprint(前端向导改过字段映射后)则跳过侦察、直接用它们建体。
    """
    run_id = run_id or f"page-{uuid4().hex[:8]}"
    sid = subsystem   # sandbox_replay 按 draft.subsystem.value 反查材料,故 system_instance_id 取 subsystem
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant=tenant, system_instance_id=sid, subsystem=sid,
        deploy=deploy or {}, credentials=credentials or {}))
    try:
        if steps is not None and dom_fingerprint is not None:
            use_steps, fp = steps, dom_fingerprint        # 前端已编辑:不再侦察
        else:
            sc = await tools.scout_page(run_id, {"system_instance_id": sid, "start_url": start_url,
                                                 "headless": headless})
            use_steps, fp = sc.get("suggested_steps") or [], sc.get("dom_fingerprint") or ""
        if not use_steps:
            return {"ok": False, "stage": "scout", "reason": "页面未发现可填字段/可操作元素"}

        dr = await tools.draft_page_script(run_id, {
            "system_instance_id": sid, "action": action, "steps": use_steps,
            "dom_fingerprint": fp, "start_url": start_url,
            "success_marker": success_marker, "title": title})

        rp = await tools.sandbox_replay(run_id, {
            "asset_draft_id": dr["asset_draft_id"], "sample_inputs": sample_inputs or {},
            "headless": headless})
        if not rp["passed"]:
            so = rp.get("structured_output") or {}
            if so.get("at_login"):
                why = " —— 停在登录页(登录态无效/过期);用网页录制手动登录后再录,回放会复用该登录态"
            elif so.get("failed_step") is not None:
                why = f":第 {so['failed_step'] + 1} 步【{so.get('op')}】没找到元素 {so.get('locator')}(删掉这步或换页面再试)"
            elif so.get("success_marker") is False:
                why = ":回放后没出现成功标志(提交可能没生效,或成功标志填得不对)"
            else:
                why = ""
            return {"ok": False, "stage": "replay", "action": dr["action"],
                    "reason": f"沙箱回放未通过(mode={rp['mode']}){why}", "detail": so}

        review_ids: list[str] = []
        if dr["needs_review"]:
            rv = await tools.request_review(run_id, {"asset_draft_id": dr["asset_draft_id"]})
            if not rv["all_passed"]:
                return {"ok": False, "stage": "review", "action": dr["action"],
                        "reason": "三模型评审未通过", "verdicts": rv.get("verdicts")}
            review_ids = rv["review_run_ids"]

        pub = await tools.publish_asset(run_id, {
            "asset_draft_id": dr["asset_draft_id"],
            "validation_run_ids": rp["validation_run_ids"], "review_run_ids": review_ids})
        log.info("page_onboard.done", action=dr["action"], published=pub["published"], mode=rp["mode"])
        return {"ok": pub["published"], "stage": "publish", "action": dr["action"],
                "risk_level": dr["risk_level"], "mode": rp["mode"],
                "asset_id": pub.get("asset_id"), "reason": pub.get("reason", "")}
    finally:
        materials.clear_run(run_id)


async def run_page_onboarding_pi(
    *, tenant: str, subsystem: str, start_url: str, action_hint: str = "",
    deploy: dict | None = None, credentials: dict | None = None, timeout_s: float = 600.0,
) -> dict:
    """pi **自主驱动**的页面接入:spawn Node sidecar,pi 按 onboard-page 技能自己
    scout_page→draft_page_script→sandbox_replay→(写页面)request_review→publish_asset。

    与确定性 run_page_onboarding 的区别:由 LLM 决策字段映射/成功标志/动作命名(适合未知复杂页面);
    Python 仍只确定性建体 + 控发布闸门。权威结果 = PG 已发布的 PAGE_SCRIPT(不信 pi 口述)。
    需:Node + 可用的 pi LLM provider(DANO_PI_*)+ 浏览器 + PG。
    """
    import secrets

    from dano.agent_tools import runs
    from dano.assets.repository import AssetRepository
    from dano.onboarding.service import _spawn_pi, _start_tool_server
    from dano.shared.enums import AssetType, Subsystem
    from dano.shared.models import Scope

    run_id = f"page-pi-{uuid4().hex[:8]}"
    sid = subsystem
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant=tenant, system_instance_id=sid, subsystem=sid,
        deploy=deploy or {}, credentials=credentials or {}))
    token = secrets.token_hex(16)
    runs.register(run_id, token)
    server, task, port = await _start_tool_server()
    prompt = (
        f"接入一个**无 API 的页面型系统**(系统实例 {sid})。页面地址 start_url = {start_url}\n"
        f"严格按 onboard-page 技能纪律(只用测试账号、语义定位、绝不坐标、不自报通过):\n"
        f"1) scout_page(system_instance_id={sid}, start_url={start_url}) 侦察,拿 fields / submit_locator / "
        f"dom_fingerprint / suggested_steps。\n"
        f"2) 据 fields 的 label/name 决定字段映射与成功标志,draft_page_script(system_instance_id={sid}, "
        f"action=<英文动作名{(',建议 '+action_hint) if action_hint else ''}>, steps=suggested_steps(按需改 field), "
        f"dom_fingerprint=上一步返回的, start_url={start_url}, success_marker=<提交成功后出现的文本/元素如 "
        f"text=保存成功,不确定可留空>, title=<中文标题>)。\n"
        f"3) sandbox_replay(asset_draft_id, sample_inputs={{字段:测试值}}) 回放,拿 validation_run_ids 与 passed"
        f"(写页面默认 dry 回放、mode=dry 属正常)。\n"
        f"4) 若返回 needs_review 为真(写页面)→ request_review(asset_draft_id) 拿 review_run_ids 与 all_passed;"
        f"查询页面跳过此步、review_run_ids 传空。\n"
        f"5) 回放通过(写页面还需三审通过)→ publish_asset(asset_draft_id, validation_run_ids=回放返回的, "
        f"review_run_ids=评审返回的或[])。\n"
        f"6) 一句话总结发布了哪个页面 Skill;过不了按返回 reasons 修正后重试。"
    )
    try:
        completed = await _spawn_pi(run_id=run_id, token=token, port=port, prompt=prompt,
                                    context={"system_instance_id": sid, "start_url": start_url},
                                    timeout_s=timeout_s)
    finally:
        server.should_exit = True
        await task
        runs.unregister(run_id)
        materials.clear_run(run_id)

    repo = AssetRepository()
    scope = Scope(tenant=tenant, subsystem=Subsystem(sid))
    published = [e.body.get("action", e.asset_key)
                 for e in await repo.list_published(AssetType.PAGE_SCRIPT, scope)]
    log.info("page_onboard.pi.done", run_id=run_id, status=completed.get("status"),
             published=published, tool_events=completed.get("tool_events"))
    return {"pi_status": completed.get("status"), "published_skills": published,
            "tool_events": completed.get("tool_events"), "final_text": completed.get("final_text", ""),
            "error": completed.get("error")}


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


async def run_request_onboarding(
    *, tenant: str, subsystem: str, action: str, title: str = "",
    api_request: dict, sample_inputs: dict | None = None, required: list[str] | None = None,
    deploy: dict | None = None, credentials: dict | None = None, run_id: str | None = None,
    goal: dict | None = None, storage_state: dict | None = None,
) -> dict:
    """抓请求路径:把录制抓到的提交请求(已参数化)落成可执行 Skill → dry 校验 → 发布。

    不走 DOM 回放、不真发(写安全);运行期 invoke 时才带登录态真发。免三模型评审(用户真实提交)。
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
        # 必填=前端标的"变化字段"(没给则全部必填,向后兼容);其余=可选,缺了用录制原值(固定字段不改)
        req_fields = [r for r in (required if required is not None else params) if r in params]
        opt_fields = [p for p in params if p not in req_fields]
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
        # 字段类型:单请求取自身,工作流取最后一步
        ftypes = dict(api_request.get("field_types") or {})
        if not ftypes and api_request.get("steps"):
            ftypes = dict((api_request["steps"][-1] or {}).get("field_types") or {})
        body = PageScriptBody(
            actions=[], dom_fingerprint="", action=action, title=title, api_request=api_request,
            user_fields=params, required_fields=req_fields, optional_fields=opt_fields,
            field_types=ftypes, risk_level=RiskLevel.L3).model_dump()
        d = await T.save_draft(run_id, {"system_instance_id": sid, "asset_type": "page_script",
                                        "asset_key": action, "body": body})
        log.info("ingest.draft_saved", draft_id=d.get("asset_draft_id"))
        # 自适应活体验证:仅当环境可逆沙箱 + 有回查手段(plan=live)且带测试登录态,才真发写 + fact_check → 可升 verified
        from dano.execution.page.request_capture import capture_verification_plan
        plan = capture_verification_plan(deploy, api_request)
        do_live = plan.get("mode") == "live" and storage_state is not None
        log.info("ingest.verification_plan", mode=plan.get("mode"),
                 controllability=plan.get("controllability"), do_live=do_live)
        rp = await T.sandbox_replay(run_id, {"asset_draft_id": d["asset_draft_id"],
                                             "sample_inputs": sample_inputs or {},
                                             "live": do_live, "storage_state": storage_state, "verify": False})
        log.info("ingest.replay", passed=rp.get("passed"), mode=rp.get("mode"))
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
        # 录制抓请求资产 = 用户真人在页面上**亲手提交过**的写请求 → 免三模型评审,直接发布。
        # 评审对录制资产易抖动误判(把固定字段当漏配、把脱敏登录态当缺鉴权,时过时不过),且并未提升
        # 安全(请求本就是用户真发过的);发布闸门 verify_reviewed 对 page_is_capture 同样放行。
        pub = await T.publish_asset(run_id, {"asset_draft_id": d["asset_draft_id"],
                                             "validation_run_ids": rp["validation_run_ids"],
                                             "review_run_ids": []})
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
                "asset_id": pub.get("asset_id"), "mode": "request", "reason": pub.get("reason", ""),
                "warnings": warnings,
                "api": {"method": api_request.get("method"), "path": api_request.get("path"),
                        "params": params}}
    except Exception:
        log.error("ingest.error", exc_info=True)              # 带 traceback,快速定位崩在哪一步
        raise
    finally:
        materials.clear_run(run_id)
        structlog.contextvars.clear_contextvars()
