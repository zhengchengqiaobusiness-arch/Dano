"""操作步骤(SOP)渲染的泛化门禁:纯函数、随业务/框架自适配、零渲染器黑话。

要点:SOP 内容必须**全部来自 manifest 数据**(flow/parameters/business_meta),渲染器自身
不得写死任何业务/框架词。已知框架(有 dialect)出丰富 SOP,未知框架(单连接器)优雅降级。
"""
from __future__ import annotations

from dano.catalog.manifest import to_manifest
from dano.export.agent_skills import _quality_section, _skill_md, _slug, _sop_section
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem

_PHASES = ["阶段1", "阶段2", "阶段3", "阶段4", "阶段5"]
# 渲染器**不得自己写死**的框架/业务黑话(出现=泄漏);数据里的词(field_docs/business_meta)不受此限
_BLACKTALK = ["两步", "procInsId", "taskId", "startFlow", "发起流程", "OA token", "拆成多笔", "下单"]


def _wf() -> SkillSpec:
    """多步工作流 + 审批链 + 回查(模拟已知框架的丰富资产)。"""
    return SkillSpec(
        skill_id="A-OA.submit_x", subsystem=Subsystem("A-OA"), action="submit_x",
        risk_level=RiskLevel.L3, is_workflow=True, has_api=True, title="提交X",
        field_docs={"f1": "字段一"}, required_fields=[], optional_fields=["f1"],
        business_meta={"approvalChain": [{"step": "主管"}], "thresholds": []},
        workflow_steps=[{"kind": "call", "action": "a1", "inputs": {}},
                        {"kind": "call", "action": "a2", "inputs": {}}],
        workflow_success_rule="response.code==200",
        workflow_invariants=[{"check": "response.code==200", "evidence": {"query_action": "q1"}}])


def _conn() -> SkillSpec:
    """任意框架的单连接器:无 dialect / 无审批 / 无前置(降级路径)。"""
    return SkillSpec(
        skill_id="B-CRM.create_c", subsystem=Subsystem("B-CRM"), action="create_c",
        risk_level=RiskLevel.L3, has_api=True, title="新建C",
        field_docs={"name": "名称"}, required_fields=["name"], optional_fields=[])


def test_sop_has_five_phases_for_any_skill():
    for sk in (_wf(), _conn()):
        sop = _sop_section(to_manifest(sk), "--x <x>", " --confirm")
        for p in _PHASES:
            assert p in sop


def test_known_framework_renders_rich_sop():
    sop = _sop_section(to_manifest(_wf()), "--x", " --confirm")
    assert "2 步受控编排" in sop          # 步数 ← flow.step_count
    assert "业务返回码判定" in sop         # ← workflow_success_rule
    assert "回查确认真生效" in sop         # ← invariant.evidence
    assert "审批走向" in sop               # ← business_meta.approvalChain


def test_unknown_framework_degrades_no_fabrication():
    md = _skill_md(to_manifest(_conn()), _slug("B-CRM.create_c"))
    assert "一步受控调用" in md            # 单步
    assert "受控编排" not in md            # 不臆造多步
    assert "审批路径" not in md and "审批走向" not in md   # 无 business_meta → 不臆造审批流
    for w in ("采购", "请假", "报销"):     # 不冒出他业务的词
        assert w not in md


def test_renderer_emits_no_framework_blacktalk():
    for sk in (_wf(), _conn()):
        md = _skill_md(to_manifest(sk), _slug(sk.skill_id))
        leaks = [w for w in _BLACKTALK if w in md]
        assert not leaks, f"渲染器黑话泄漏: {leaks}"


def _capture_multistep() -> SkillSpec:
    """抓请求型多接口 page_script:api_request 带 steps(草稿→提交两个接口)+ 成功约定 + 回查。"""
    return SkillSpec(
        skill_id="A-OA.submit_form", subsystem=Subsystem("A-OA"), action="submit_form",
        risk_level=RiskLevel.L3, has_api=False, title="提交表单",
        required_fields=["原因"], optional_fields=[],
        api_request={
            "steps": [
                {"method": "POST", "path": "/api/draft/create", "params": []},
                {"method": "POST", "path": "/api/task/submit", "params": ["原因"],
                 "success_rule": "response.code==200"},
            ],
            "success_rule": "response.code==200",
            "fact_check": {"query_action": "q1"},
        })


def test_capture_multistep_step_count_from_api_request():
    """抓请求型:step_count/judged_by_code/verify 从 api_request 还原(不再恒报"一步")。"""
    m = to_manifest(_capture_multistep())
    assert m.flow["step_count"] == 2
    assert m.flow["judged_by_code"] is True       # ← api_request.success_rule
    assert m.flow["verify"] is True               # ← api_request.fact_check
    assert [s["path"] for s in m.flow["step_paths"]] == ["/api/draft/create", "/api/task/submit"]


def test_capture_multistep_sop_lists_interfaces():
    """SOP 阶段4 **显式列出**编排的各接口(用户能看到多接口编排,不再"隐藏")。"""
    sop = _sop_section(to_manifest(_capture_multistep()), "--原因 <原因>", " --confirm")
    assert "2 步受控编排" in sop
    assert "各步对调用方隐藏" not in sop           # 抓请求型:展示而非隐藏
    assert "POST /api/draft/create" in sop
    assert "POST /api/task/submit" in sop
    assert "一次调用即可" in sop


def test_capture_single_step_no_orchestration_text():
    """抓请求型单接口:仍是"一步受控调用",不臆造多步编排。"""
    sk = SkillSpec(
        skill_id="A-OA.submit_one", subsystem=Subsystem("A-OA"), action="submit_one",
        risk_level=RiskLevel.L3, has_api=False, title="提交",
        required_fields=["原因"], api_request={
            "method": "POST", "path": "/api/submit", "params": ["原因"],
            "success_rule": "response.code==200"})
    m = to_manifest(sk)
    assert m.flow["step_count"] == 1 and m.flow["judged_by_code"] is True
    sop = _sop_section(m, "--原因 <原因>", " --confirm")
    assert "一步受控调用" in sop and "受控编排" not in sop


# ── 质量标准(怎样算做好):grounded 验收清单,随业务/框架自适配 ──────────────────
def _wf_goal() -> SkillSpec:
    """工作流 + goal(success_criteria/forbidden)+ 审批 + 前置 + 回查(全量资产)。"""
    sk = _wf()
    sk.workflow_preconditions = [{"check": "amount>0", "message": "金额必须大于0"}]
    sk.goal = {"success_criteria": ["业务单据已创建", "审批流程已发起"],
               "forbidden_steps": ["deleteFlow", "approveOther"]}
    return sk


def test_quality_section_grounded_for_workflow():
    q = _quality_section(to_manifest(_wf_goal()))
    assert "## 质量标准" in q
    assert "金额必须大于0" in q                 # ← preconditions.message(数据)
    assert "已进入正确审批链" in q              # ← business_meta
    assert "事实核查通过" in q                  # ← flow.verify
    assert "业务单据已创建" in q and "审批流程已发起" in q   # ← goal.success_criteria
    assert "deleteFlow" in q and "approveOther" in q        # ← goal.forbidden_steps(红线)


def test_quality_section_degrades_for_plain_connector():
    q = _quality_section(to_manifest(_conn()))   # 无 goal / 无审批
    assert "## 质量标准" in q
    assert "必填字段齐全" in q                    # ← parameters
    assert "审批链" not in q                      # 无 business_meta → 不臆造
    assert "达成:" not in q                       # 无 goal → 不臆造成功标准


def test_quality_section_light_for_readonly_query():
    sk = SkillSpec(skill_id="B-CRM.query_c", subsystem=Subsystem("B-CRM"), action="query_c",
                   risk_level=RiskLevel.L1, has_api=True, title="查询C", optional_fields=["name"])
    q = _quality_section(to_manifest(sk))
    assert "如实反映系统数据" in q               # 只读 → 轻量验收
    assert "提交前" not in q and "红线" not in q  # 不套写操作的验收骨架
