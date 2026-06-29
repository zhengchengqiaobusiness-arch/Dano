"""剧本派生层(P0 地基)单测:FieldRole/StepRole 枚举 + classify_field_role + build_screenplay。
纯函数、零副作用 —— 只从 api_request 派生注释,不改任何现有行为。"""
from __future__ import annotations

from dano.execution.page.screenplay import (
    FieldRole,
    StepRole,
    build_screenplay,
    classify_field_role,
)

# 综合单请求:覆盖 静态枚举 / 用户填 / 活接口 / 名ID配对 / 审批人 / 多选 / 身份 / 系统值 / 常量
_APIR = {
    "method": "POST", "path": "/oa/leave/submit", "url": "http://oa.x/oa/leave/submit",
    "body_template": {
        "leaveType": "{{请假类型}}",                 # 静态枚举(dom)
        "reason": "{{原因}}",                        # 用户填
        "leaderId": "{{领导}}",                      # 活接口(source,无 dom 无 id_path)
        "ywsxList": [{"yyxtmc": "{{应用系统}}", "yyxtid": "ID0"}],   # 名/ID 配对
        "startUserSelectAssignees": {"Activity_09dlq0g": ["{{审批人1}}"]},  # 审批人
        "participants": "{{参会人}}",                # 多选
        "applicant": 123,                            # 身份(运行期重取)
        "createTime": 1700000000000,                 # 系统值
        "processDefKey": "oa_leave",                 # 常量
    },
    "params": ["请假类型", "原因", "领导", "应用系统", "审批人1", "参会人"],
    "field_types": {"请假类型": "enum", "原因": "string", "领导": "enum", "应用系统": "enum",
                    "审批人1": "enum", "参会人": "list-enum"},
    "selects": [
        {"param": "请假类型", "source_url": "/dict", "value_key": "dictValue", "label_key": "dictLabel",
         "options": ["病假", "事假"], "count": 2, "dom_options": True},
        {"param": "领导", "source_url": "/system/user/page", "value_key": "id", "label_key": "nickname",
         "options": ["张三"], "count": 8},
        {"param": "应用系统", "source_url": "/sys/list", "value_key": "id", "label_key": "xtmc",
         "options": ["系统A"], "count": 3, "id_path": "ywsxList[0].yyxtid"},
        {"param": "审批人1", "source_url": "/system/user/page", "value_key": "id", "label_key": "nickname",
         "options": ["李四"], "count": 8},
        {"param": "参会人", "multi": True, "source_url": "/system/user/page", "value_key": "id",
         "label_key": "nickname", "options": ["王五"], "count": 8,
         "element_template": {"id": {"from": "item", "item_key": "id"}}},
    ],
    "identity": [{"path": "applicant", "source": "localStorage:userId"}],
    "system_values": [{"path": "createTime", "kind": "now_ms"}],
}


def _role_of(sp, name):
    return next(f["role"] for f in sp["fields"] if f["name"] == name)


def test_classify_field_role_precedence():
    """单字段分类优先级:步链 > 身份 > 系统 >(非参数=常量)> 审批人 > 多选 > 静态 > 名ID配对 > 活接口 > 用户填。"""
    assert classify_field_role(is_param=True, is_link_target=True) == FieldRole.STEP_CHAINED
    assert classify_field_role(is_param=False, is_identity=True) == FieldRole.IDENTITY
    assert classify_field_role(is_param=False, is_system=True) == FieldRole.SYSTEM_VALUE
    assert classify_field_role(is_param=False) == FieldRole.CONSTANT
    assert classify_field_role(is_param=True, is_assignee=True,
                               select={"source_url": "/u"}) == FieldRole.ASSIGNEE   # 审批人压过活接口
    assert classify_field_role(is_param=True, select={"multi": True}) == FieldRole.LIST_SELECT
    assert classify_field_role(is_param=True, select={"dom_options": True, "options": ["a"]}) == FieldRole.ENUM_STATIC
    assert classify_field_role(is_param=True, select={"source_url": "/u", "id_path": "x"}) == FieldRole.NAME_ID_PAIR
    assert classify_field_role(is_param=True, select={"source_url": "/u"}) == FieldRole.ENUM_LIVE
    assert classify_field_role(is_param=True, select={"options": ["a"]}) == FieldRole.ENUM_STATIC  # 有候选无来源
    assert classify_field_role(is_param=True) == FieldRole.USER_INPUT


def test_build_screenplay_field_roles():
    """整张表派生出的每个字段角色都对。"""
    sp = build_screenplay(_APIR)
    assert _role_of(sp, "请假类型") == "enum_static"
    assert _role_of(sp, "原因") == "user_input"
    assert _role_of(sp, "领导") == "enum_live"
    assert _role_of(sp, "应用系统") == "name_id_pair"
    assert _role_of(sp, "审批人1") == "assignee"
    assert _role_of(sp, "参会人") == "list_select"
    assert _role_of(sp, "applicant") == "identity"
    assert _role_of(sp, "createTime") == "system_value"
    assert _role_of(sp, "processDefKey") == "constant"


def test_build_screenplay_provenance_and_sources():
    """来源/用法 + 选项来源接口聚合。"""
    sp = build_screenplay(_APIR)
    # 活接口字段:来源是接口 + 用法提示实时拉
    leader = next(f for f in sp["fields"] if f["name"] == "领导")
    assert leader["provenance"]["from"]["kind"] == "interface"
    assert leader["provenance"]["from"]["interface"] == "GET /system/user/page"
    assert "--list-options" in leader["provenance"]["usage"]
    # 静态枚举:来源 dom + 带选项
    lt = next(f for f in sp["fields"] if f["name"] == "请假类型")
    assert lt["provenance"]["from"]["kind"] == "dom" and lt["options"] == ["病假", "事假"]
    # 身份 / 系统 / 常量 用法
    assert sp_from(sp, "applicant")["kind"] == "session"
    assert sp_from(sp, "createTime")["kind"] == "system"
    assert sp_from(sp, "processDefKey")["kind"] == "constant"
    # 选项来源接口聚合:/system/user/page 服务了 领导/审批人1/参会人
    src = {o["interface"]: set(o["for_fields"]) for o in sp["option_sources"]}
    assert "GET /system/user/page" in src
    assert {"领导", "审批人1", "参会人"} <= src["GET /system/user/page"]
    assert sp["multi_step"] is False


def sp_from(sp, name):
    return next(f["provenance"]["from"] for f in sp["fields"] if f["name"] == name)


def test_build_screenplay_workflow_steps_and_dataflow():
    """多步工作流:写步骤角色 + 步间数据流(step_chained 字段 + data_flow)。"""
    wf = {"steps": [
        {"method": "POST", "url": "http://oa.x/oa/leave/start", "body_template": {"reason": "{{原因}}"},
         "params": ["原因"], "field_types": {"原因": "string"}, "selects": [], "identity": [],
         "system_values": [], "response_json": {"data": {"taskId": "T1"}}},
        {"method": "POST", "url": "http://oa.x/oa/task/complete",
         "body_template": {"flowTask": {"taskId": "T1"}, "remark": "ok"},
         "params": [], "field_types": {}, "selects": [], "identity": [], "system_values": [],
         "links": [{"target_path": "flowTask.taskId", "source_step": 0, "source_path": "data.taskId"}]},
    ]}
    sp = build_screenplay(wf)
    assert sp["multi_step"] is True and len(sp["write_steps"]) == 2
    # start → workflow_submit;complete(含 task 段)→ 也按路径语义判定
    assert sp["write_steps"][0]["role"] == StepRole.WORKFLOW_SUBMIT.value
    # 步链字段:第2步 flowTask.taskId 取自第1步响应
    chained = next(f for f in sp["fields"] if f["path"] == "flowTask.taskId")
    assert chained["role"] == "step_chained"
    assert chained["provenance"]["from"]["kind"] == "previous_step"
    assert chained["provenance"]["from"]["step"] == 0
    # data_flow 落点
    assert {"to_step": 1, "to": "flowTask.taskId", "from_step": 0, "from": "data.taskId"} in sp["data_flow"]


def test_build_screenplay_handles_seg_and_jsonstr():
    """段拼接(_SEG)与 JSON 字符串 blob(_JSONSTR)里的参数也能被识别出路径/角色。"""
    from dano.execution.page.request_capture import _JSONSTR, _SEG
    apir = {
        "method": "POST", "path": "/x", "url": "http://x/x",
        "body_template": {
            "title": {_SEG: ["请假事由:", {"$p": "原因"}]},          # 段拼接参数
            "blob": {_JSONSTR: {"inner": "{{内层参数}}"}},            # blob 内层参数
        },
        "params": ["原因", "内层参数"], "field_types": {"原因": "string", "内层参数": "string"},
        "selects": [], "identity": [], "system_values": [],
    }
    sp = build_screenplay(apir)
    names = {f["name"]: f for f in sp["fields"]}
    assert names["原因"]["path"] == "title" and names["原因"]["role"] == "user_input"
    assert names["内层参数"]["path"] == "blob.inner" and names["内层参数"]["role"] == "user_input"


def _spec(**kw):
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem
    # 抓请求型**页面** Skill:has_api=False(无连接器 API),api_request 携带提交请求/工作流
    base = dict(skill_id="A-OA.x", subsystem=Subsystem.OA, action="x", risk_level=RiskLevel.L3,
                title="请假申请", has_api=False)
    base.update(kw)
    return SkillSpec(**base)


def test_manifest_attaches_field_role_and_provenance():
    """P1:manifest 给每个字段挂 x-field-role + x-provenance(前端区分 / 导出说明用)。"""
    from dano.catalog.manifest import to_manifest
    sk = _spec(field_types={"请假类型": "enum", "审批人1": "enum", "原因": "string"},
               required_fields=["请假类型", "审批人1", "原因"],
               api_request={"method": "POST", "path": "/oa/submit", "url": "http://x/oa/submit",
                            "body_template": {"leaveType": "{{请假类型}}", "approver": "{{审批人1}}", "reason": "{{原因}}"},
                            "params": ["请假类型", "审批人1", "原因"],
                            "field_types": {"请假类型": "enum", "审批人1": "enum", "原因": "string"},
                            "selects": [
                                {"param": "请假类型", "source_url": "/dict", "value_key": "v", "label_key": "l",
                                 "options": ["病假"], "count": 1, "dom_options": True},
                                {"param": "审批人1", "source_url": "/system/user/page", "value_key": "id",
                                 "label_key": "nickname", "options": ["张三"], "count": 8}],
                            "identity": [], "system_values": []})
    props = to_manifest(sk).parameters["properties"]
    assert props["请假类型"]["x-field-role"] == "enum_static"
    assert props["审批人1"]["x-field-role"] == "assignee"          # approver 路径 → 审批人
    assert props["原因"]["x-field-role"] == "user_input"
    assert props["审批人1"]["x-provenance"]["from"]["interface"] == "GET /system/user/page"


_WF_APIR = {"steps": [
    {"method": "POST", "url": "http://x/oa/leave/start", "body_template": {"reason": "{{原因}}"},
     "params": ["原因"], "field_types": {"原因": "string"}, "selects": [], "identity": [],
     "system_values": [], "response_json": {"data": {"taskId": "T1"}}},
    {"method": "POST", "url": "http://x/oa/task/complete",
     "body_template": {"flowTask": {"taskId": "T1"}, "remark": "ok"},
     "params": [], "field_types": {}, "selects": [], "identity": [], "system_values": [],
     "links": [{"target_path": "flowTask.taskId", "source_step": 0, "source_path": "data.taskId"}]}]}


def test_manifest_flow_carries_orchestration():
    """P1:多步抓请求型 → flow 带 write_steps/option_sources/data_flow/multi_step(供导出接口编排完整回填)。"""
    from dano.catalog.manifest import to_manifest
    sk = _spec(field_types={"原因": "string"}, required_fields=["原因"], api_request=_WF_APIR)
    flow = to_manifest(sk).flow
    assert flow["multi_step"] is True and len(flow["write_steps"]) == 2
    assert flow["write_steps"][0]["role"] == "workflow_submit"
    assert {"to_step": 1, "to": "flowTask.taskId", "from_step": 0, "from": "data.taskId"} in flow["data_flow"]


def test_export_orchestration_section_and_badges():
    """P1:导出 SKILL.md 出「接口编排」段(写步骤 + 数据流);参数表带角色徽章。"""
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _skill_md
    # 多步 + 审批人(活接口)→ 编排段 + 数据流 + 徽章
    sk = _spec(field_types={"原因": "string"}, required_fields=["原因"], api_request=_WF_APIR)
    md = _skill_md(to_manifest(sk), "dano-a-oa-x")
    assert "## 接口编排" in md and "步间数据流" in md and "data.taskId" in md
    # 单步 + 审批人活接口 → 参数表角色徽章 + 选项来源
    sk2 = _spec(field_types={"审批人1": "enum"}, required_fields=["审批人1"],
                api_request={"method": "POST", "path": "/oa/submit", "url": "http://x/oa/submit",
                             "body_template": {"approver": "{{审批人1}}"}, "params": ["审批人1"],
                             "field_types": {"审批人1": "enum"},
                             "selects": [{"param": "审批人1", "source_url": "/system/user/page",
                                          "value_key": "id", "label_key": "nickname", "options": ["张三"], "count": 8}],
                             "identity": [], "system_values": []})
    md2 = _skill_md(to_manifest(sk2), "dano-a-oa-x2")
    assert "[审批人·活接口]" in md2 and "选项来源接口" in md2


def test_screenplay_skeleton_strips_values():
    """P2 红线:喂 LLM 的骨架只留 字段名/角色/来源接口路径/编排,**不含任何值/选项快照/样例/凭证**。"""
    from dano.execution.page.screenplay import screenplay_skeleton
    sp = build_screenplay(_APIR)
    skel = screenplay_skeleton(sp)
    blob = str(skel)
    # 选项值、样例值、识别源(localStorage:userId)一律不得出现
    for leak in ("病假", "事假", "张三", "李四", "王五", "localStorage", "1700000000000", "options"):
        assert leak not in blob, f"骨架泄漏了值:{leak}"
    # 但结构在:字段名 + 角色 + 来源接口路径
    f = {x["name"]: x for x in skel["fields"]}
    assert f["领导"]["role"] == "enum_live" and f["领导"]["interface"] == "GET /system/user/page"
    assert f["请假类型"]["role"] == "enum_static" and f["请假类型"]["source"] == "dom"


async def test_optimize_business_description_llm(monkeypatch):
    """P2:有 client → 用草稿+骨架产出说明;无 client / 异常 → 原样退回草稿(绝不阻断)。"""
    from dano.review import board

    class _FakeClient:
        async def complete_json(self, *, model, system, user, timeout_s):
            assert "病假" not in user and "localStorage" not in user   # 仍守红线:输入无值
            return {"description": "本 skill 用于发起请假申请:审批人需先实时拉取再选。"}

    out = await board.optimize_business_description_llm(_FakeClient(), "m", draft="请假", skeleton={"fields": []})
    assert "请假申请" in out
    # 无 client → 退草稿
    assert await board.optimize_business_description_llm(None, None, draft="原草稿", skeleton={}) == "原草稿"

    class _BoomClient:
        async def complete_json(self, **kw):
            raise RuntimeError("network")
    assert await board.optimize_business_description_llm(_BoomClient(), "m", draft="退回", skeleton={}) == "退回"


def test_manifest_and_export_carry_business_description():
    """P2:business_description 经 SkillSpec→manifest→导出"业务说明"段;空则不出该段。"""
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _skill_md
    sk = _spec(field_types={"原因": "string"}, required_fields=["原因"],
               business_description="本 skill 发起请假申请;审批人来自实时接口,选前先 --list-options。",
               api_request={"method": "POST", "path": "/oa/submit", "url": "http://x/oa/submit",
                            "body_template": {"reason": "{{原因}}"}, "params": ["原因"],
                            "field_types": {"原因": "string"}, "selects": [], "identity": [], "system_values": []})
    m = to_manifest(sk)
    assert m.business_description.startswith("本 skill 发起请假申请")
    md = _skill_md(m, "dano-a-oa-x")
    assert "## 业务说明" in md and "审批人来自实时接口" in md
    # 空描述 → 不产出该段
    sk2 = _spec(field_types={"原因": "string"}, required_fields=["原因"],
                api_request={"method": "POST", "path": "/x", "url": "http://x/x",
                             "body_template": {"reason": "{{原因}}"}, "params": ["原因"],
                             "field_types": {"原因": "string"}, "selects": [], "identity": [], "system_values": []})
    assert "## 业务说明" not in _skill_md(to_manifest(sk2), "dano-a-oa-x2")


def test_provenance_opaque_id_constant_and_source_params():
    """字段来源细化:① 不透明内部 ID 常量(ssbmId/bmId)→ system_preset + 标明"来源未探知,需人工确认";
    ② 活接口字段的来源**带参数**(t/xxxtId)显式列出 + 提示级联(治"源接口参数没考虑/没说明")。"""
    apir = {
        "method": "POST", "path": "/qzqdsl/createQzqdSl", "url": "http://x/qzqdsl/createQzqdSl",
        "body_template": {"ssbmId": "020210601113158904000001010018",   # 不透明预设 ID
                          "processDefKey": "oa_leave",                    # 可读字面量常量
                          "ywsxList": "{{业务事项列表}}"},
        "params": ["业务事项列表"], "field_types": {"业务事项列表": "list-enum"},
        "selects": [{"param": "业务事项列表", "multi": True,
                     "source_url": "http://x/sxqd/getSxqdBmList?t=1782&xxxtId=02021060111",
                     "value_key": "id", "label_key": "lmFid", "options": [], "count": 81}],
        "identity": [], "system_values": [],
    }
    sp = build_screenplay(apir)
    f = {x["name"]: x for x in sp["fields"]}
    # ① 不透明 ID 常量 vs 可读字面量常量
    assert f["ssbmId"]["provenance"]["from"]["kind"] == "system_preset"
    assert "来源未探知" in f["ssbmId"]["provenance"]["usage"]
    assert f["processDefKey"]["provenance"]["from"]["kind"] == "constant"   # oa_leave 是字面量,不误标
    # ② 活接口来源带参数 → 显式列出 + 级联提示
    yp = f["业务事项列表"]["provenance"]["from"]
    assert yp["kind"] == "interface" and yp.get("params") == ["t", "xxxtId"]
    assert "级联" in f["业务事项列表"]["provenance"]["usage"]


def test_export_lists_system_preset_fields():
    """导出「接口编排」段显式列出**系统预设 ID 字段**(ssbmId/bmId:无需填但有来源、跨环境需人工确认)+ 源接口参数。"""
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _skill_md
    sk = _spec(field_types={"业务事项列表": "list-enum"}, required_fields=["业务事项列表"],
               api_request={"method": "POST", "path": "/qzqdsl/createQzqdSl", "url": "http://x/qzqdsl/createQzqdSl",
                            "body_template": {"ssbmId": "020210601113158904000001010018",
                                              "ywsxList": "{{业务事项列表}}"},
                            "params": ["业务事项列表"], "field_types": {"业务事项列表": "list-enum"},
                            "selects": [{"param": "业务事项列表", "multi": True,
                                         "source_url": "http://x/sxqd/getSxqdBmList?t=1782&xxxtId=02021",
                                         "value_key": "id", "label_key": "lmFid", "options": [], "count": 81}],
                            "identity": [], "system_values": []})
    md = _skill_md(to_manifest(sk), "dano-a-oa-x")
    assert "系统预设 ID 字段" in md and "ssbmId" in md and "人工确认" in md
    assert "参数 t/xxxtId" in md          # 源接口参数显式标出(治"源参数没考虑/没说明")


def test_screenplay_skeleton_still_value_free_with_opaque_ids():
    """红线复核:即便有不透明 ID 常量,喂 LLM 的骨架仍不带任何具体值。"""
    from dano.execution.page.screenplay import screenplay_skeleton
    apir = {"method": "POST", "path": "/x", "url": "http://x/x",
            "body_template": {"ssbmId": "020210601113158904000001010018", "r": "{{原因}}"},
            "params": ["原因"], "field_types": {"原因": "string"}, "selects": [],
            "identity": [], "system_values": []}
    blob = str(screenplay_skeleton(build_screenplay(apir)))
    assert "020210601113158904000001010018" not in blob


async def test_request_fields_message_attaches_field_role():
    """P3:录制 request_fields 消息给每个字段挂 field_role(录制 UI 据此渲染统一角色徽章)。"""
    from dano.gateway.app import _request_fields_msg
    submit = ('{"leaveType":"事假","reason":"r","approver":12,'
              '"createTime":1700000000000,"processDefKey":"oa_leave"}')
    chosen = {"method": "POST", "url": "http://oa.x/oa/leave/submit", "post_data": submit}
    reads = [{"url": "http://oa.x/system/user/page",
              "json": {"rows": [{"userId": 12, "nickname": "张三", "deptName": "研发"}]}}]
    msg = await _request_fields_msg(chosen, [chosen], {"请假类型": "事假"}, reads, None, set(), {})
    roles = {f["path"]: f.get("field_role") for f in msg["fields"]}
    assert roles["approver"] == "assignee"            # approver 路径 → 审批人(压过活接口)
    assert roles["createTime"] == "system_value"      # 系统时间戳 → 系统自动填
    assert roles["processDefKey"] == "constant"       # 模板常量
    assert roles["reason"] == "user_input"            # 用户填
    assert all(f.get("field_role") for f in msg["fields"])   # 每个字段都有角色


def test_build_screenplay_empty_and_const_only():
    """无参数(纯常量)请求:不报错,字段全是 constant。"""
    apir = {"method": "POST", "path": "/x", "url": "http://x/x",
            "body_template": {"a": "1", "b": "2"}, "params": [],
            "field_types": {}, "selects": [], "identity": [], "system_values": []}
    sp = build_screenplay(apir)
    assert all(f["role"] == "constant" for f in sp["fields"])
    assert sp["option_sources"] == [] and sp["data_flow"] == []
