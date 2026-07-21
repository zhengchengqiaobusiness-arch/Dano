"""方式B 升级:抓提交请求 → 参数化(纯函数,离线)。"""
from __future__ import annotations

import json

from dano.execution.page.request_capture import (
    _response_ok,
    _resolve_list_selects,
    _resolve_selects,
    as_list_payload,
    classify_request_role,
    classify_network_request as classify_capture_request,
    infer_success_rule,
    build_api_request,
    _extract_total,
    discover_step_links,
    execute_api,
    looks_like_auth_write,
    looks_like_read_request,
    execute_api_request,
    execute_api_workflow,
    extract_auth_headers,
    flatten_body,
    fetch_field_options,
    json_write_requests,
    pick_submit_request,
    resolve_identity_value,
    self_check,
    substitute,
    suggest_fact_check,
    apply_page_enum_options,
    page_enum_selects,
    suggest_assignee_names,
    suggest_identity,
    suggest_list_selects,
    suggest_select_names,
    suggest_selects,
)

# 参会人多选(participants[] = 选了 3 个人;抓到的用户列表里只含其中 1 个 → 测"≥1 命中即识别")
_PART_SUBMIT = ('{"meetingTitle":"1","participants":['
                '{"userId":142,"userName":"亚历山大大帝","userAvatar":"http://a/142.png","participantType":2},'
                '{"userId":118,"userName":"狗蛋","userAvatar":"http://a/118.png","participantType":2},'
                '{"userId":117,"userName":"测试号02","userAvatar":"http://a/117.png","participantType":2}]}')
_USER_READ = [{"url": "/system/user/list",
               "json": {"rows": [{"userId": 117, "nickName": "测试号02", "avatar": "http://a/117.png", "deptId": 1},
                                 {"userId": 200, "nickName": "张三", "avatar": "http://a/200.png", "deptId": 2}]}}]

_SAMPLES = {"请假类型": "事假", "开始时间": "2026-06-24", "结束时间": "2026-06-26", "原因": "大地色多"}
_SUBMIT = ('{"leaveType":"事假","startTime":"2026-06-24","endTime":"2026-06-26",'
           '"reason":"大地色多","procDefId":"PROC123","draft":false}')
_REQUESTS = [
    {"method": "GET", "url": "http://oa.x/prod-api/getInfo", "post_data": None},
    {"method": "POST", "url": "http://oa.x/prod-api/login", "post_data": '{"u":"admin"}'},     # 噪声:登录
    {"method": "POST", "url": "http://oa.x/prod-api/captcha", "post_data": '{"code":"1"}'},    # 噪声
    {"method": "POST", "url": "http://oa.x/prod-api/oa/leave/start", "post_data": _SUBMIT},     # 真提交
]


def _strict_select_field(path: str, label: str) -> dict:
    """Build the minimum recorder evidence required for an API-backed select."""
    leaf = path.split(".")[-1].split("[")[0]
    return {
        "path": path,
        "key": leaf,
        "suggest_name": label,
        "name_source": "dom",
        "field_aliases": [path, leaf],
        "control_kind": "select",
    }


def _strict_dom_enum(path: str, selected_label: str, selected_value, options: list[dict]) -> dict:
    """Build complete, field-owned DOM enum evidence (never a label-only snapshot)."""
    leaf = path.split(".")[-1].split("[")[0]
    return {
        "field_key": leaf,
        "field_aliases": [path, leaf],
        "control_kind": "select",
        "enum_source": "dom",
        "mapping_complete": True,
        "selected_label": selected_label,
        "selected_value": selected_value,
        "options": options,
    }


def test_json_write_requests_lists_all_candidates():
    """候选 = 所有带 JSON body 的写请求(GET / 非JSON 排除),保序;供前端手选用哪个。"""
    cands = json_write_requests(_REQUESTS)
    urls = [c["url"] for c in cands]
    assert urls == ["http://oa.x/prod-api/login", "http://oa.x/prod-api/captcha",
                    "http://oa.x/prod-api/oa/leave/start"]   # 3 个 JSON 写请求,GET 的 getInfo 不在内


def test_as_list_payload_detects_common_shapes():
    """通用列表挖取(P2:select 候选源):裸数组 / rows / data.records 命中;非列表/空 → None。"""
    assert as_list_payload([{"id": 1}]) == [{"id": 1}]                      # 裸数组
    assert as_list_payload({"rows": [{"id": 1}], "total": 1}) == [{"id": 1}]  # rows 包装
    assert as_list_payload({"data": {"records": [{"id": 9}]}}) == [{"id": 9}]  # 两层 data.records
    assert as_list_payload({"payload": {"resultSet": [{"id": 7}]}}) == [{"id": 7}]
    assert as_list_payload({"payload": {"left": [{"id": 1}], "right": [{"id": 2}]}}) is None
    assert as_list_payload({"code": 200, "msg": "ok"}) is None              # 无列表
    assert as_list_payload([]) is None                                      # 空列表无意义
    assert as_list_payload("x") is None


def test_post_read_detection_uses_body_shape_for_custom_paths():
    """泛化:POST 查询接口即使路径不是 get/list/query 开头，也能靠分页/搜索 body 识别为读请求。"""
    assert looks_like_read_request("/api/customEndpoint", '{"pageNo":1,"pageSize":10,"keyword":"张"}')
    assert looks_like_read_request("/api/customEndpoint", {"current": 1, "size": 20})


def test_post_read_detection_does_not_swallow_plain_submit_body():
    """普通业务提交没有分页/过滤结构时不能被误判为读请求。"""
    assert not looks_like_read_request("/api/oa/duty-leave/submit-process", {
        "type": 2,
        "reason": "回家",
        "startTime": 1783440000000,
    })


def test_json_write_requests_excludes_post_query_by_body_shape():
    reads_and_submit = [
        {
            "method": "POST",
            "url": "/api/customEndpoint",
            "post_data": json.dumps({"pageNo": 1, "pageSize": 10, "keyword": "张"}),
        },
        {
            "method": "POST",
            "url": "/api/submit",
            "post_data": json.dumps({"reason": "回家"}),
        },
    ]

    assert [r["url"] for r in json_write_requests(reads_and_submit)] == ["/api/submit"]


def test_multipart_upload_is_explicitly_marked_unsupported():
    role = classify_capture_request({
        "method": "POST",
        "url": "https://oa/api/file/upload",
        "post_data": None,
        "content_type": "multipart/form-data; boundary=abc",
    })
    assert role["role"] == "unsupported_upload"
    assert role["keep"] is False


def test_graphql_request_is_explicitly_marked_unsupported():
    role = classify_capture_request({
        "method": "POST",
        "url": "https://oa/api/graphql",
        "post_data": json.dumps({"query": "mutation Submit($input: Input!) { submit(input: $input) { id } }"}),
        "content_type": "application/json",
    })
    assert role["role"] == "unsupported_graphql"
    assert role["keep"] is False




def test_suggest_selects_binds_nested_name_id_pair_for_business_system():
    """嵌套数组里的显示名/ID 配对：用户选系统名称，运行期必须同步写入对应系统 ID。"""
    post = json.dumps({
        "ywsxList": [{
            "ywsxmc": "123123qweqw",
            "yyxtid": "02026031815271171200000101539137",
            "yyxtmc": "交通信息系统01",
            "tableHcommentList": [],
            "ywsxKbList": [],
        }],
    }, ensure_ascii=False)
    reads = [{
        "url": "/appgateway/dcensus/v1.0/qzqdsl/getXxxtListByBm",
        "json": {
            "data": [
                {"yyxtid": "02026031815271171200000101539137", "yyxtmc": "交通信息系统01"},
                {"yyxtid": "02026031815271171200000101539138", "yyxtmc": "交通信息系统02"},
            ],
        },
    }]

    selects = suggest_selects(
        post,
        reads,
        {"所属系统": "交通信息系统01"},
        fields=[_strict_select_field("ywsxList[0].yyxtmc", "所属系统")],
    )

    assert len(selects) == 1
    sel = selects[0]
    assert sel["path"] == "ywsxList[0].yyxtmc"
    assert sel["value_key"] == "yyxtid"
    assert sel["label_key"] == "yyxtmc"
    assert sel["id_path"] == "ywsxList[0].yyxtid"
    assert sel["options"] == ["交通信息系统01", "交通信息系统02"]


def test_list_selects_does_not_collapse_editable_detail_rows():
    """可新增行明细表含用户填写字段时不能折叠成多选，否则会冻结行内业务文本。"""
    from dano.execution.page.request_capture import suggest_list_selects

    post = json.dumps({
        "ywsxList": [{
            "ywsxmc": "123123qweqw",
            "yyxtid": "02026031815271171200000101539137",
            "yyxtmc": "交通信息系统01",
            "tableHcommentList": [],
            "ywsxKbList": [],
        }],
    }, ensure_ascii=False)
    reads = [{
        "url": "/appgateway/dcensus/v1.0/qzqdsl/getXxxtListByBm",
        "json": {
            "data": [
                {"yyxtid": "02026031815271171200000101539137", "yyxtmc": "交通信息系统01"},
                {"yyxtid": "02026031815271171200000101539138", "yyxtmc": "交通信息系统02"},
            ],
        },
    }]

    assert suggest_list_selects(post, reads, {"职能清单": "123123qweqw", "所属系统": "交通信息系统01"}) == []




def test_suggest_selects_binds_field_to_list_source():
    """Q2 选领导:提交体 approverId=12 命中 user/list 里 userId=12 → 建议 select(value=userId,label=nickName)。"""
    submit = '{"reason":"回家","approverId":12,"leaveType":"事假"}'
    reads = [{"url": "http://oa.x/prod-api/system/user/list",
              "json": {"rows": [{"userId": 12, "nickName": "张经理", "deptName": "研发"},
                                {"userId": 34, "nickName": "李总"}]}}]
    s = suggest_selects(
        submit,
        reads,
        {"审批人": "张经理"},
        fields=[_strict_select_field("approverId", "审批人")],
    )
    assert len(s) == 1
    b = s[0]
    assert b["path"] == "approverId" and b["value_key"] == "userId"
    assert b["label_key"] == "nickName" and b["label"] == "张经理"
    assert b["source_url"].endswith("/system/user/list") and b["count"] == 2


def test_suggest_selects_binds_long_entity_id_without_guessing_text_sibling():
    """只有内部 ID 被提交时，枚举应挂在 ID 字段本身，不能猜相邻标题是显示字段。"""
    seal_id = "f13a450364df1b8a269365f90f44aee0"
    submit = json.dumps({
        "sealId": seal_id,
        "applyTitle": "出差用章申请",
        "remark": "客户材料",
    }, ensure_ascii=False)
    reads = [{
        "url": "/admin-api/system/seal/simple-list",
        "json": {"data": [
            {"id": seal_id, "name": "行政公章"},
            {"id": "d8896f988f51434ea6cdb1a48d71ee99", "name": "合同章"},
        ]},
    }]

    selects = suggest_selects(
        submit,
        reads,
        {"申请标题": "出差用章申请", "备注": "客户材料", "印章": "行政公章"},
        fields=[_strict_select_field("sealId", "印章")],
    )

    assert len(selects) == 1
    assert selects[0]["path"] == "sealId"
    assert "id_path" not in selects[0]
    assert selects[0]["option_map"] == {
        "行政公章": seal_id,
        "合同章": "d8896f988f51434ea6cdb1a48d71ee99",
    }


def test_discover_step_links_includes_get_query_targets():
    process_id = "oa_seal_apply:1:aa840521"
    requests = [
        {
            "method": "GET",
            "url": "/process-definition/get?key=oa_seal_apply",
            "response_json": {"data": {"id": process_id}},
        },
        {
            "method": "GET",
            "url": "/approval-detail?processDefinitionId=oa_seal_apply%3A1%3Aaa840521&activityId=StartUserNode",
            "response_json": {"data": {"node": "StartUserNode"}},
        },
    ]

    links = discover_step_links(requests)

    assert links == [{
        "target_step": 1,
        "target_path": "query.processDefinitionId",
        "target_tokens": ["query", "processDefinitionId"],
        "source_step": 0,
        "source_path": "data.id",
        "source_tokens": ["data", "id"],
    }]


def test_suggest_selects_code_dropdown_via_small_dict():
    """代码型下拉:type=2 命中字典小列表 dictValue=2 → 绑 select,agent 传"病假"、运行期换 2。"""
    submit = '{"type":2,"reason":"回家"}'
    dict_read = [{"url": "http://oa.x/system/dict/data/type/leave_type",
                  "json": {"code": 200, "data": [{"dictLabel": "事假", "dictValue": "1"},
                                                 {"dictLabel": "病假", "dictValue": "2"},
                                                 {"dictLabel": "年假", "dictValue": "3"}]}}]
    s = suggest_selects(
        submit,
        dict_read,
        {"请假类型": "病假"},
        fields=[_strict_select_field("type", "请假类型")],
    )
    assert len(s) == 1
    b = s[0]
    assert b["path"] == "type" and b["value_key"] == "dictValue" and b["label_key"] == "dictLabel"
    assert b["label"] == "病假"                    # type=2 → dictValue 2 → dictLabel 病假
    assert b["source_url"].endswith("/type/leave_type")


def test_suggest_selects_generic_non_ruoyi_shape():
    """泛化证明:换一套完全不同形态(包装键 options、字段 optionCode/caption,非若依 data/dictValue/dictLabel)
    照样识别 → select 靠结构(id 类值字段 + 文字标签字段),不写死任何系统字段名。"""
    submit = '{"category":"VIP"}'
    read = [{"url": "http://other.sys/api/categories",
             "json": {"options": [{"optionCode": "STD", "caption": "标准"},
                                   {"optionCode": "VIP", "caption": "贵宾"}]}}]
    s = suggest_selects(
        submit,
        read,
        {"类别": "贵宾"},
        fields=[_strict_select_field("category", "类别")],
    )
    assert len(s) == 1
    b = s[0]
    assert b["path"] == "category"
    assert b["value_key"] == "optionCode"      # 值字段(code 结尾)= ID 类
    assert b["label_key"] == "caption"         # 没有 name/label 类字段 → 最长文字字段兜底
    assert b["label"] == "贵宾"                 # category=VIP → optionCode VIP → caption 贵宾


def test_suggest_selects_short_code_not_matched_in_big_dict():
    """短码仍不在大字典里乱认:type=2 撞到 1431 项城市字典 → 不绑(避免误报)。"""
    submit = '{"type":2}'
    big = {"data": [{"value": str(i)} for i in range(1431)]}
    assert suggest_selects(submit, [{"url": "/sys/city", "json": big}]) == []


def test_suggest_selects_empty_when_no_list_match():
    submit = '{"reason":"回家","leaveType":"事假"}'
    reads = [{"url": "/u/list", "json": {"rows": [{"userId": 99, "nickName": "王"}]}}]
    assert suggest_selects(submit, reads) == []


def test_suggest_selects_rejects_false_positives():
    """真实表单暴露的误报:短值 't'/'1' 碰巧命中 1431 项大字典 → 不该绑 select。"""
    # 用户每个字段都填了 t/1,大字典每项有 {label, value}
    submit = '{"applyTitle":"t","street":"t","totalAmt":"1","roomType":"1"}'
    big = {"rows": [{"label": "城市A", "value": "t"}, {"label": "城市B", "value": "1"}]
                   + [{"label": f"x{i}", "value": f"v{i}"} for i in range(1429)]}
    assert suggest_selects(submit, [{"url": "/sys/dict", "json": big}]) == []   # 全被挡(过短值)


def test_suggest_selects_drops_overly_generic_source():
    """一个源命中 >3 个不同字段 = 通用字典误命中 → 整源丢弃(即便值不算短)。"""
    submit = '{"aCode":"AAAA","bCode":"BBBB","cCode":"CCCC","dCode":"DDDD"}'
    generic = {"rows": [{"value": v} for v in ("AAAA", "BBBB", "CCCC", "DDDD")]}
    assert suggest_selects(submit, [{"url": "/sys/dict", "json": generic}]) == []


def test_suggest_selects_value_key_not_named_id():
    """泛化:值字段名不带 id/code(如字典 {type,name})也能绑 select —— 靠"小项 + 独立文字标签"结构判定,
    不写死值字段名,多公司/多系统的下拉字典都覆盖。"""
    sub = '{"leaveType":2}'
    read = [{"url": "/dict", "json": {"data": [{"type": 1, "name": "事假"}, {"type": 2, "name": "病假"}]}}]
    s = suggest_selects(
        sub,
        read,
        {"请假类型": "病假"},
        fields=[_strict_select_field("leaveType", "请假类型")],
    )
    assert len(s) == 1
    assert s[0]["value_key"] == "type" and s[0]["label_key"] == "name" and s[0]["label"] == "病假"


def test_suggest_selects_rejects_id_only_list_without_label():
    """只有 ID、没有名字/文字字段的列表不绑(没名字可传 → 不是名字→ID 下拉,防误绑)。"""
    sub = '{"x": "AAAA"}'
    read = [{"url": "/d", "json": {"rows": [{"value": "AAAA"}, {"value": "BBBB"}]}}]
    assert suggest_selects(sub, read) == []


def test_find_field_select_single_and_workflow():
    """find_field_select:单请求 + 多步各步里按参数名找 select 元数据(供实时拉选项)。"""
    from dano.execution.page.request_capture import find_field_select
    apir = {"selects": [{"param": "请假类型", "source_url": "/d", "value_key": "v", "label_key": "l"}]}
    assert find_field_select(apir, "请假类型")["source_url"] == "/d"
    assert find_field_select(apir, "不存在") is None
    wf = {"steps": [{}, {"selects": [{"param": "领导", "source_url": "/u", "value_key": "id", "label_key": "name"}]}]}
    assert find_field_select(wf, "领导")["label_key"] == "name"


async def test_fetch_field_options_live(monkeypatch):
    """问题1 实时拉取:fetch_field_options 直接调来源接口 → {field, options:[{label,value}], count}。
    非选择型/无来源 → options=[] 并说明(不抛,让 agent 退回传名字)。"""
    from dano.execution.page import request_capture as rc
    apir = {"selects": [{"param": "请假类型", "source_url": "/dict/leave",
                         "value_key": "dictValue", "label_key": "dictLabel"}],
            "auth_headers": {}}

    async def fake_fetch(url, base_url, storage_state, token_key, verify, auth_headers):
        assert url == "/dict/leave"
        return [{"dictValue": "1", "dictLabel": "事假"}, {"dictValue": "2", "dictLabel": "病假"}]
    monkeypatch.setattr(rc, "_fetch_list", fake_fetch)
    out = await rc.fetch_field_options(apir, "请假类型")
    assert out["count"] == 2
    assert out["options"] == [{"label": "事假", "value": "1"}, {"label": "病假", "value": "2"}]
    # 非选择型字段 → 空 + 说明
    out2 = await rc.fetch_field_options(apir, "原因")
    assert out2["options"] == [] and "note" in out2


async def test_fetch_field_options_returns_static_enum_snapshot():
    from dano.execution.page import request_capture as rc
    apir = {"selects": [{"param": "请假类型", "source_url": "",
                         "options": ["事假", "病假", "年假"], "enum_source": "dom"}]}

    out = await rc.fetch_field_options(apir, "请假类型")

    assert out["count"] == 3
    assert out["options"] == [
        {"label": "事假", "value": "事假"},
        {"label": "病假", "value": "病假"},
        {"label": "年假", "value": "年假"},
    ]


async def test_fetch_field_options_executes_captured_post_read_contract():
    import http.server as _h
    import json as _j
    import socketserver as _s
    import threading as _t

    seen = {"body": None}

    class Handler(_h.BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: ANN001
            pass

        def do_POST(self):
            size = int(self.headers.get("Content-Length", 0))
            seen["body"] = _j.loads(self.rfile.read(size) or b"{}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(_j.dumps({"data": [{"id": "2", "name": "病假"}]}).encode())

    server = _s.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    _t.Thread(target=server.serve_forever, daemon=True).start()
    try:
        api_request = {
            "selects": [{
                "param": "请假类型",
                "source_url": f"http://127.0.0.1:{port}/api/options/query",
                "source_method": "POST",
                "source_role": "read_option",
                "source_content_type": "application/json",
                "source_body": '{"category":"leave"}',
                "value_key": "id",
                "label_key": "name",
            }],
        }

        out = await fetch_field_options(api_request, "请假类型", verify=False)

        assert seen["body"] == {"category": "leave"}
        assert out["options"] == [{"label": "病假", "value": "2"}]
    finally:
        server.shutdown()


def test_suggest_selects_prefers_confirmed_over_huge_generic_dict():
    """根因:type=2 同时撞**1431 项通用大字典**(未确认,垃圾标签)和**小请假类型字典**(确认命中 事假)→
    必须绑确认的小字典(事假/病假/年假),不能绑通用大字典(治"请假类型绑到歌词模式/OpenAI…")。"""
    sub = '{"type":2}'
    huge = {"data": [{"id": str(i), "name": f"模型{i}"} for i in range(1431)]}
    huge["data"][2] = {"id": "2", "name": "歌词模式"}            # type=2 在大字典里撞到"歌词模式"(垃圾)
    leave = {"data": [{"dictValue": "1", "dictLabel": "事假"},
                      {"dictValue": "2", "dictLabel": "病假"},
                      {"dictValue": "3", "dictLabel": "年假"}]}
    reads = [{"url": "/ai/models", "json": huge},            # 通用大字典**先**出现(旧逻辑会 first-win 绑错)
             {"url": "/dict/leave_type", "json": leave}]
    s = suggest_selects(
        sub,
        reads,
        {"请假类型": "病假"},
        fields=[_strict_select_field("type", "请假类型")],
    )                                                       # 录制选了"病假"
    assert len(s) == 1
    assert s[0]["label"] == "病假" and s[0]["count"] == 3 and s[0]["label_key"] == "dictLabel"
    assert s[0]["options"] == ["事假", "病假", "年假"]           # 选项快照是小字典(不是垃圾大字典)


def test_short_code_never_binds_unconfirmed_department_or_user_source():
    departments = [{"id": 1, "name": "研发部门"}, {"id": 2, "name": "市场部门"}]

    assert suggest_selects(
        '{"type":1}',
        [{"url": "/system/dept/simple-list", "json": {"data": departments}}],
        {"请假类型": "病假"},
        fields=[_strict_select_field("type", "请假类型")],
    ) == []


def test_unique_code_global_dictionary_still_narrows_by_explicit_dict_type():
    items = [
        {"dictValue": str(index), "dictLabel": f"噪声{index}", "dictType": "misc"}
        for index in range(60)
    ] + [
        {"dictValue": "101", "dictLabel": "病假", "dictType": "oa_leave_type"},
        {"dictValue": "102", "dictLabel": "事假", "dictType": "oa_leave_type"},
        {"dictValue": "103", "dictLabel": "婚假", "dictType": "oa_leave_type"},
    ]

    result = suggest_selects(
        '{"type":"101"}',
        [{"url": "/system/dict-data/simple-list", "json": {"data": items}}],
        {"请假类型": "病假"},
        fields=[_strict_select_field("type", "请假类型")],
    )

    assert len(result) == 1
    assert result[0]["options"] == ["病假", "事假", "婚假"]
    assert result[0]["category_key"] == "dictType"


def test_small_mixed_dictionary_narrows_by_explicit_category_key():
    """显式字典分类键本身就是结构证据，即使字典小于 50 项也必须先按类别收窄。"""
    for category_key in ("dictType", "dict_code", "category"):
        items = [
            {"value": "0", "label": "未提交", category_key: "process_status"},
            {"value": "1", "label": "审批中", category_key: "process_status"},
            {"value": "S", "label": "标准间", category_key: "room_type"},
            {"value": "D", "label": "大床房", category_key: "room_type"},
        ]

        result = suggest_selects(
            '{"processStatus":"1"}',
            [{"url": "/system/dict-data/simple-list", "json": {"data": items}}],
            {"流程状态": "审批中"},
            fields=[_strict_select_field("processStatus", "流程状态")],
        )

        assert len(result) == 1
        assert result[0]["options"] == ["未提交", "审批中"]
        assert result[0]["category_key"] == category_key
        assert result[0]["category_value"] == "process_status"


def test_suggest_selects_rejects_ambiguous_sources_without_field_evidence():
    """无控件归属和选中标签佐证时，不按列表大小猜某个 API 属于该字段。"""
    sub = '{"type":"VIP"}'
    big = {"rows": [{"code": f"C{i}", "name": f"X{i}"} for i in range(120)] + [{"code": "VIP", "name": "大字典贵宾"}]}
    small = {"rows": [{"code": "STD", "name": "标准"}, {"code": "VIP", "name": "小字典贵宾"}]}
    assert suggest_selects(
        sub,
        [{"url": "/big", "json": big}, {"url": "/small", "json": small}],
    ) == []


def test_suggest_selects_snapshots_options():
    """问题1:select 把候选**显示名快照**进 entry.options(存进 skill 让 agent 从真实选项里选,不凭空猜)。"""
    sub = '{"type":2}'
    read = [{"url": "/dict", "json": {"data": [{"dictValue": "1", "dictLabel": "事假"},
                                               {"dictValue": "2", "dictLabel": "病假"},
                                               {"dictValue": "3", "dictLabel": "年假"}]}}]
    s = suggest_selects(
        sub,
        read,
        {"请假类型": "病假"},
        fields=[_strict_select_field("type", "请假类型")],
    )
    assert s[0]["options"] == ["事假", "病假", "年假"]


def _agg_dict_items():
    """模拟"按 dictType 聚合的全量字典"(若依 dict_data):同码('00/01/02')在多个 dictType 下重复出现;
    请假类型只是其中一个类目(oa_leave_type=事假/病假/年假),码 L0/L1/L2。共 57 项 > _AGG_MIN_ITEMS。"""
    items = []
    for t in range(15):                                  # 撑到 >50 项,且码在各类目重复(聚合字典的标志)
        for c in ("00", "01", "02"):
            items.append({"dictValue": c, "dictLabel": f"占位{t}_{c}", "dictType": f"misc{t}"})
    groups = {"ai_mode": [("M0", "歌词模式"), ("M1", "描述模式"), ("M2", "聊天")],
              "archive": [("A0", "文书档案"), ("A1", "科技档案"), ("A2", "会计档案")],
              "bank": [("B0", "工商银行"), ("B1", "建设银行"), ("B2", "农业银行")],
              "oa_leave_type": [("L0", "事假"), ("L1", "病假"), ("L2", "年假")]}
    for dt, opts in groups.items():
        for code, lab in opts:
            items.append({"dictValue": code, "dictLabel": lab, "dictType": dt})
    return items


def test_suggest_selects_narrows_aggregate_dict_by_category():
    """根因(治"请假类型绑到 1431 项含歌词模式/档案/银行…全量字典"):来源是按 dictType 聚合的全量字典 +
    录制确认选了"病假" → 按命中项的分类键 dictType 收窄成**只属于请假类型的 3 项**,并把分类过滤随 select 走。"""
    sub = '{"type":"L1"}'                                 # 请假类型存码 L1(=病假)
    read = [{"url": "/system/dict/data/list", "json": {"rows": _agg_dict_items()}}]
    s = suggest_selects(
        sub,
        read,
        {"请假类型": "病假", "原因": "回家"},
        fields=[_strict_select_field("type", "请假类型")],
    )
    assert len(s) == 1
    b = s[0]
    assert b["label"] == "病假" and b["count"] == 3
    assert b["options"] == ["事假", "病假", "年假"]        # 收窄到 oa_leave_type,不是全量垃圾
    assert b["category_key"] == "dictType" and b["category_value"] == "oa_leave_type"


def test_suggest_selects_drops_unconfirmed_aggregate_match():
    """全量聚合字典 + 无录制佐证(判不出属于哪个类目)→ 宁可**不绑**(字段退回普通参数),
    也不把 1431 项垃圾选项塞给 agent。码 '00' 够长(≥2)能过短码闸,但因聚合+未确认被丢。"""
    sub = '{"type":"00"}'
    read = [{"url": "/system/dict/data/list", "json": {"rows": _agg_dict_items()}}]
    assert suggest_selects(sub, read) == []


def test_suggest_selects_big_unique_list_not_treated_as_aggregate():
    """防误伤:大但**码全局唯一**的单字段列表(城市/用户)不是聚合字典 → 照常整列表绑定,不收窄不丢弃。"""
    sub = '{"city":"C100"}'
    big = {"rows": [{"cityId": f"C{i}", "cityName": f"市{i}"} for i in range(200)]}
    s = suggest_selects(
        sub,
        [{"url": "/sys/city", "json": big}],
        {"城市": "市100"},
        fields=[_strict_select_field("city", "城市")],
    )
    assert len(s) == 1 and s[0]["count"] == 200
    assert s[0]["label"] == "市100" and "category_key" not in s[0]


async def test_fetch_field_options_filters_aggregate_category(monkeypatch):
    """运行期实时拉取:select 带 category_key/value → 只列该字段所属类目(与录制快照一致),不返回全量字典。"""
    from dano.execution.page import request_capture as rc
    apir = {"selects": [{"param": "请假类型", "source_url": "/dict/all", "value_key": "dictValue",
                         "label_key": "dictLabel", "category_key": "dictType",
                         "category_value": "oa_leave_type"}], "auth_headers": {}}

    async def fake(url, base_url, storage_state, token_key, verify, auth_headers):
        return [{"dictValue": "M0", "dictLabel": "歌词模式", "dictType": "ai_mode"},
                {"dictValue": "L0", "dictLabel": "事假", "dictType": "oa_leave_type"},
                {"dictValue": "L1", "dictLabel": "病假", "dictType": "oa_leave_type"}]
    monkeypatch.setattr(rc, "_fetch_list", fake)
    out = await rc.fetch_field_options(apir, "请假类型")
    assert out["count"] == 2
    assert out["options"] == [{"label": "事假", "value": "L0"}, {"label": "病假", "value": "L1"}]


async def test_resolve_selects_filters_aggregate_category(monkeypatch):
    """运行期名→ID:聚合字典里**同名跨类目**(两个"病假")时,只在本字段类目内换码,不被别类目串到。"""
    from dano.execution.page import request_capture as rc
    apir = {"selects": [{"param": "请假类型", "source_url": "/dict/all", "value_key": "dictValue",
                         "label_key": "dictLabel", "category_key": "dictType",
                         "category_value": "oa_leave_type"}]}

    async def fake(url, base_url, storage_state, token_key, verify, auth_headers):
        return [{"dictValue": "X1", "dictLabel": "病假", "dictType": "other"},   # 别类目的同名"病假"
                {"dictValue": "L1", "dictLabel": "病假", "dictType": "oa_leave_type"}]
    monkeypatch.setattr(rc, "_fetch_list", fake)
    fields, _ov = await rc._resolve_selects(apir, {"请假类型": "病假"}, base_url="",
                                            storage_state=None, token_key=None, verify=True)
    assert fields["请假类型"] == "L1"                      # 只在 oa_leave_type 里换码,不串到 other 的 X1


def test_suggest_assignee_names_from_flow_definition():
    """治"审批人参数名退回 Activity_09dlq0g 内部节点 ID":抓到的流程定义读响应里有 节点ID→节点名 →
    审批人字段命名成"领导审批""人力审批"(理解后命名,确定性,不靠 LLM 猜不透明 ID)。"""
    sub = ('{"billType":"oa_duty_leave",'
           '"startUserSelectAssignees":{"Activity_09dlq0g":[118],"Activity_0ag2wyz":[117]}}')
    reads = [{"url": "/flowable/start/node/list",
              "json": {"data": [{"nodeId": "Activity_09dlq0g", "nodeName": "领导审批"},
                                 {"nodeId": "Activity_0ag2wyz", "nodeName": "人力审批"}]}}]
    out = suggest_assignee_names(sub, reads)
    assert out["startUserSelectAssignees.Activity_09dlq0g[0]"] == "领导审批"
    assert out["startUserSelectAssignees.Activity_0ag2wyz[0]"] == "人力审批"


def test_suggest_assignee_names_ordinal_fallback():
    """流程定义没抓到节点名时:按审批节点出现序兜底"审批人1/审批人2"(也比裸 Activity_xxx 强)。"""
    sub = '{"startUserSelectAssignees":{"Activity_09dlq0g":[118],"Activity_0ag2wyz":[117]}}'
    out = suggest_assignee_names(sub, [])
    assert out["startUserSelectAssignees.Activity_09dlq0g[0]"] == "审批人1"
    assert out["startUserSelectAssignees.Activity_0ag2wyz[0]"] == "审批人2"


def test_suggest_assignee_names_single_node():
    """只有一个审批节点 → "审批人"(不加序号)。"""
    sub = '{"startUserSelectAssignees":{"Activity_09dlq0g":[118]}}'
    assert suggest_assignee_names(sub, [])["startUserSelectAssignees.Activity_09dlq0g[0]"] == "审批人"


def test_suggest_assignee_names_ignores_normal_list_id_name():
    """防误伤:普通列表的 id+name(非 BPMN 节点 ID 形态)不被当节点名映射;非审批字段不命名。"""
    sub = '{"reason":"回家","deptId":118}'
    reads = [{"url": "/dept/list", "json": {"data": [{"id": 118, "name": "研发部"}]}}]
    assert suggest_assignee_names(sub, reads) == {}


def test_suggest_list_selects_collapses_participants_array():
    """根因(治"选了多个人却被拆成 participants[0].userId… 一堆、前几个还冻成固定值"):对象数组多选 →
    识别成**一个列表参数**,推出元素模板(userId←userId、userName←nickName、头像←avatar、type=常量)。
    抓到的用户列表只含 3 人里的 1 人,仍按"≥1 命中"识别(运行期用完整列表解析全部名字)。"""
    s = suggest_list_selects(_PART_SUBMIT, _USER_READ, {"参会人": "测试号02"})
    assert len(s) == 1
    b = s[0]
    assert b["path"] == "participants" and b["multi"] is True
    assert b["value_key"] == "userId" and b["label_key"] == "nickName" and b["label_subkey"] == "userName"
    assert b["element_template"]["userId"] == {"from": "item", "item_key": "userId"}
    assert b["element_template"]["userName"] == {"from": "item", "item_key": "nickName"}
    assert b["element_template"]["userAvatar"] == {"from": "item", "item_key": "avatar"}
    assert b["element_template"]["participantType"] == {"const": 2}
    assert b["_confirmed"] is True and b["values"] == ["亚历山大大帝", "狗蛋", "测试号02"]


def test_suggest_list_selects_ignores_non_entity_arrays():
    """防误伤:字符串数组(timeRangeList)、无 id 子键的对象数组、对不上任何来源的对象数组 → 不当多选。"""
    assert suggest_list_selects('{"timeRangeList":["06:00-06:30","07:00-07:30"]}', _USER_READ) == []
    assert suggest_list_selects('{"tags":[{"text":"a"},{"text":"b"}]}', _USER_READ) == []   # 无 id 子键
    assert suggest_list_selects(_PART_SUBMIT, []) == []                                       # 无来源可对


def test_list_entity_source_requires_id_and_name_from_the_same_response_row():
    submit = json.dumps({
        "participants": [{
            "userId": 1, "userName": "alice", "participantType": 2,
        }],
    })
    unrelated = {
        "url": "/system/tenant/simple-list",
        "json": {"data": [{"id": 1, "name": "点新信息"}]},
    }
    users = {
        "url": "/system/user/page",
        "json": {"data": {"list": [{"id": 1, "username": "alice"}]}},
    }

    result = suggest_list_selects(submit, [unrelated, users], {"参会人": "alice"})

    assert len(result) == 1
    assert result[0]["source_url"] == "/system/user/page"
    assert result[0]["label_key"] == "username"
    assert suggest_list_selects(submit, [unrelated], {"参会人": "alice"}) == []


def test_flatten_body_collapses_list_select_into_one_field():
    """列表多选接管的数组 → flatten 折叠成**一个** list-enum 字段,前端不再见 participants[0].userId… 一堆。"""
    fields = flatten_body(_PART_SUBMIT, {"参会人": "测试号02"}, collapse_paths=["participants"])
    assert not any(f["path"].startswith("participants[") for f in fields)
    pf = [f for f in fields if f["path"] == "participants"]
    assert len(pf) == 1 and pf[0]["type"] == "list-enum" and pf[0]["suggest_param"] is True
    assert any(f["path"] == "meetingTitle" for f in fields)        # 其它字段照常


def test_build_api_request_list_select_one_param_no_split():
    """build:数组路径在 param_map → 整个数组替成一个 {{参会人}} 占位(不拆元素),并带 multi 元素模板;self_check 通过。"""
    list_sels = suggest_list_selects(_PART_SUBMIT, _USER_READ, {"参会人": "测试号02"})
    pm = {"meetingTitle": "会议主题", "participants": "参会人"}
    apir = build_api_request({"url": "http://oa.x/meeting/apply", "post_data": _PART_SUBMIT}, pm, selects=list_sels)
    assert apir["body_template"]["participants"] == "{{参会人}}"
    assert "参会人" in apir["params"] and apir["field_types"]["参会人"] == "list-enum"
    sm = next(s for s in apir["selects"] if s["param"] == "参会人")
    assert sm["multi"] is True and sm["value_key"] == "userId" and sm["label_subkey"] == "userName"
    assert sm["element_template"]["participantType"] == {"const": 2}
    assert self_check(apir) == []                                   # 无残留占位、参数能进 body


async def test_resolve_list_selects_expands_names_to_objects(monkeypatch):
    """运行期:参会人=名字列表 → 每个名字经来源接口拼成整份元素对象(userId/userName/头像/type)。"""
    from dano.execution.page import request_capture as rc
    apir = {"selects": [{"param": "参会人", "multi": True, "source_url": "/u", "value_key": "userId",
                         "label_key": "nickName", "label_subkey": "userName",
                         "element_template": {"userId": {"from": "item", "item_key": "userId"},
                                              "userName": {"from": "item", "item_key": "nickName"},
                                              "userAvatar": {"from": "item", "item_key": "avatar"},
                                              "participantType": {"const": 2}}}]}

    async def fake(url, base_url, storage_state, token_key, verify, auth_headers):
        return [{"userId": 117, "nickName": "测试号02", "avatar": "http://a/117.png"},
                {"userId": 118, "nickName": "狗蛋", "avatar": "http://a/118.png"},
                {"userId": 142, "nickName": "亚历山大大帝", "avatar": "http://a/142.png"}]
    monkeypatch.setattr(rc, "_fetch_list", fake)
    fields = await rc._resolve_list_selects(apir, {"参会人": ["亚历山大大帝", "狗蛋"]},
                                            base_url="", storage_state=None, token_key=None, verify=True)
    assert fields["参会人"] == [
        {"userId": 142, "userName": "亚历山大大帝", "userAvatar": "http://a/142.png", "participantType": 2},
        {"userId": 118, "userName": "狗蛋", "userAvatar": "http://a/118.png", "participantType": 2}]


def test_manifest_static_dom_enum_inlines_small_options():
    """**静态页面枚举**(enum_source=dom)且 ≤50 → manifest 内置 enum(约束 agent 只能选真实值)。"""
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem
    sk = SkillSpec(skill_id="A-OA.f", subsystem=Subsystem.OA, action="f", risk_level=RiskLevel.L3,
                   field_types={"请假类型": "enum"}, required_fields=["请假类型"],
                   api_request={"selects": [{"param": "请假类型", "source_url": "/d",
                                             "value_key": "dictValue", "label_key": "dictLabel",
                                             "options": ["事假", "病假", "年假"], "count": 3,
                                             "enum_source": "dom"}]})
    p = to_manifest(sk).parameters["properties"]["请假类型"]
    assert p["enum"] == ["事假", "病假", "年假"] and p["x-options"] == ["事假", "病假", "年假"]


def test_manifest_live_directory_enum_no_inline_enum():
    """**活接口目录**(选人/部门/审批人:网络源、无 DOM 固定下拉)→ **不烤 enum、不烤快照**,只暴露 x-options-source,
    让调用方运行期 `--list-options` 现拉(治"审批人烤成 6 个部门、与实际人选不符导致入库失败")。"""
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem
    sk = SkillSpec(skill_id="A-OA.appr", subsystem=Subsystem.OA, action="appr", risk_level=RiskLevel.L3,
                   field_types={"审批人1": "enum"}, required_fields=["审批人1"],
                   api_request={"selects": [{"param": "审批人1", "source_url": "/system/user/list",
                                             "value_key": "userId", "label_key": "nickName",
                                             "options": ["财务部门", "市场部门", "研发部门"], "count": 6}]})
    p = to_manifest(sk).parameters["properties"]["审批人1"]
    assert "enum" not in p and "x-options" not in p           # 不把活目录烤成静态清单
    assert p["x-options-source"] is True and "--list-options" in p["description"]


def test_apply_complete_page_enum_overrides_garbage_dict():
    """字段归属、完整 wire 映射和当前选中值都齐全时，DOM 事实可替换错误 API 推断。"""
    selects = [{"path": "type", "label": "周末加班", "value": "2", "source_url": "/dict",
                "value_key": "dictValue", "label_key": "dictLabel",
                "options": [f"x{i}" for i in range(222)], "count": 222,
                "category_key": "dictType", "category_value": "misc"}]
    apply_page_enum_options(
        selects,
        {"加班类型": _strict_dom_enum("type", "周末加班", 2, [
            {"label": "工作日加班", "value": 1},
            {"label": "周末加班", "value": 2},
            {"label": "节假日加班", "value": 3},
        ])},
        post_data='{"type":2}',
    )
    s = selects[0]
    assert s["options"] == ["工作日加班", "周末加班", "节假日加班"] and s["count"] == 3
    assert s["enum_source"] == "dom" and s["enum_label_source"] == "dom"
    assert "category_key" not in s
    assert "source_url" not in s                                    # 完整 DOM 映射不冒充原 API 来源


def test_apply_page_enum_options_matches_submitted_code_and_keeps_option_map():
    selects = [{"path": "type", "label": "", "value": "", "source_url": "",
                "value_key": "", "label_key": "", "options": [], "count": 0}]
    apply_page_enum_options(
        selects,
        {"类型": _strict_dom_enum("type", "事假", 2, [
            {"label": "事假", "value": 2}, {"label": "病假", "value": 3},
        ])},
        post_data='{"type":2}',
        fields=[_strict_select_field("type", "类型")],
    )

    assert selects[0]["options"] == ["事假", "病假"]
    assert selects[0]["option_map"] == {"事假": 2, "病假": 3}
    assert selects[0]["enum_source"] == "dom"


def test_script_dictionary_enum_preserves_runtime_source_and_category_filter():
    selects = [{"path": "query.processStatus", "label": "审批中", "value": "1"}]
    apply_page_enum_options(
        selects,
        {
            "流程状态": {
                "field_key": "processStatus",
                "field_aliases": ["name:processStatus"],
                "control_kind": "select",
                "options": [
                    {"label": "未提交", "value": 0},
                    {"label": "审批中", "value": 1},
                ],
                "enum_source": "script_dictionary",
                "source_url": "/system/dict-data/simple-list",
                "dict_type": "bpm_process_instance_status",
                "mapping_complete": True,
                "selected_label": "审批中",
                "selected_value": 1,
            },
        },
        post_data='{"query":{"processStatus":1}}',
        fields=[{"path": "query.processStatus", "key": "processStatus", "field_aliases": ["name:processStatus"]}],
    )

    assert selects[0]["source_url"] == "/system/dict-data/simple-list"
    assert selects[0]["category_key"] == "dictType"
    assert selects[0]["category_value"] == "bpm_process_instance_status"
    assert selects[0]["option_map"] == {"未提交": 0, "审批中": 1}
    assert selects[0]["enum_label_source"] == "script_dictionary"


def test_apply_page_enum_options_ignores_label_only_dom_snapshot():
    """DOM 只有 label 时不能覆盖 API 已识别的 label→短码映射。"""
    selects = [{"path": "type", "label": "病假", "value": "2", "source_url": "/dict/leave-type",
                "value_key": "dictValue", "label_key": "dictLabel",
                "options": ["其它类型", "病假"], "count": 2,
                "option_map": {"事假": 1, "病假": 2, "婚假": 3},
                "option_map_source_url": "/dict/leave-type", "enum_source": "api"}]
    apply_page_enum_options(
        selects,
        {"病假": {"options": ["病假", "事假", "婚假"], "field_key": "类型", "selected": "病假"}},
        post_data='{"type":2,"reason":"x"}',
        fields=[{"path": "type", "key": "type", "suggest_name": "类型", "value": "2"}],
    )

    assert selects[0]["options"] == ["其它类型", "病假"]
    assert selects[0]["option_map"] == {"事假": 1, "病假": 2, "婚假": 3}
    assert selects[0]["enum_source"] == "api"
    assert "enum_label_source" not in selects[0]


def test_apply_page_enum_options_prefers_dom_values_and_rejects_foreign_map():
    selects = [{"path": "type", "label": "病假", "value": "SICK",
                "source_url": "/dict/leave-type", "value_key": "value", "label_key": "label",
                "option_map": {"病假": "OLD-SICK", "事假": "OLD-PERSONAL"},
                "option_map_source_url": "/dict/other", "enum_source": "api"}]

    apply_page_enum_options(
        selects,
        {"请假类型": _strict_dom_enum("type", "病假", "SICK", [
            {"label": "病假", "value": "SICK"},
            {"label": "事假", "value": "PERSONAL"},
            {"label": "年假", "value": "ANNUAL"},
        ])},
        post_data='{"type":"SICK"}',
    )

    assert selects[0]["option_map"] == {
        "病假": "SICK", "事假": "PERSONAL", "年假": "ANNUAL",
    }


def test_dynamic_dom_snapshot_is_not_manifest_hard_enum():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem

    selects = [{"path": "type", "label": "病假", "value": "2", "source_url": "/dict/leave",
                "value_key": "value", "label_key": "label", "enum_source": "api",
                "option_map": {"事假": "1", "病假": "2", "年假": "3"},
                "option_map_source_url": "/dict/leave"}]
    apply_page_enum_options(selects, {"病假": ["事假", "病假", "年假"]}, post_data='{"type":"2"}')
    api_request = build_api_request(
        {"url": "http://x/leave", "post_data": '{"type":"2"}'},
        {"type": "请假类型"}, selects=selects,
    )
    skill = SkillSpec(skill_id="A-OA.leave", subsystem=Subsystem.OA, action="leave",
                      risk_level=RiskLevel.L3, field_types={"请假类型": "enum"},
                      required_fields=["请假类型"], api_request=api_request)

    prop = to_manifest(skill).parameters["properties"]["请假类型"]
    assert "enum" not in prop and "x-options" not in prop
    assert prop["x-options-source"] is True


def test_page_enum_selects_creates_sourceless_enum():
    """页面完整记录 label→wire 映射时可生成无网络来源 enum。"""
    sub = '{"overtimeType":"周末加班","reason":"x"}'
    evidence = {"加班类型": _strict_dom_enum("overtimeType", "周末加班", "周末加班", [
        {"label": "工作日加班", "value": "工作日加班"},
        {"label": "周末加班", "value": "周末加班"},
        {"label": "节假日加班", "value": "节假日加班"},
    ])}
    out = page_enum_selects(sub, evidence, set())
    assert len(out) == 1
    s = out[0]
    assert s["path"] == "overtimeType" and s["options"] == ["工作日加班", "周末加班", "节假日加班"]
    assert s["source_url"] == "" and s["enum_source"] == "dom"
    assert page_enum_selects(sub, evidence, {"overtimeType"}) == []   # 已被别的 select 接管 → 不重复造


def test_page_enum_selects_matches_field_label_when_body_stores_code():
    out = page_enum_selects(
        '{"type":2,"reason":"x"}',
        {"类型": _strict_dom_enum("type", "事假", 2, [
            {"label": "事假", "value": 2}, {"label": "病假", "value": 3},
        ])},
        set(),
        fields=[_strict_select_field("type", "类型")],
    )

    assert len(out) == 1
    assert out[0]["path"] == "type"
    assert out[0]["value"] == "2"
    assert out[0]["options"] == ["事假", "病假"]
    assert out[0]["option_map"] == {"事假": 2, "病假": 3}


def test_page_enum_uses_recorded_control_name_without_business_dictionary():
    out = page_enum_selects(
        '{"roomLevel":3,"roomType":2}',
        {"房间等级": {
            "field_key": "房间等级",
            "field_aliases": ["roomLevel"],
            "control_kind": "select",
            "enum_source": "dom",
            "mapping_complete": True,
            "selected_label": "豪华",
            "selected_value": 3,
            "options": [
                {"label": "普通", "value": 1},
                {"label": "豪华", "value": 3},
            ],
        }},
        set(),
    )

    assert len(out) == 1
    assert out[0]["path"] == "roomLevel"
    assert out[0]["option_map"] == {"普通": 1, "豪华": 3}


def test_page_enum_label_only_snapshot_is_not_executable_enum():
    out = page_enum_selects(
        '{"type":2}',
        {"类型": {"options": ["事假", "病假", "年假"], "selected": "病假"}},
        set(),
        fields=[{"path": "type", "key": "type", "suggest_name": "类型", "value": "2"}],
    )

    assert out == []


def test_page_enum_null_values_are_rejected_as_incomplete_mapping():
    """自定义下拉未知 wire value 时不能伪造 label=value，也不能生成可执行枚举。"""
    out = page_enum_selects(
        '{"type":2}',
        {"类型": {
            "options": [
                {"label": "事假"},
                {"label": "病假", "value": None},
                {"label": "年假"},
            ],
            "selected_label": "病假",
            "selected_value": 2,
            "field_key": "类型",
            "field_aliases": ["type"],
            "control_kind": "select",
            "enum_source": "dom",
            "mapping_complete": False,
        }},
        set(),
        fields=[{"path": "type", "key": "type", "suggest_name": "类型", "value": "2"}],
    )

    assert out == []


def test_unrelated_page_enum_does_not_bind_arbitrary_short_code_field():
    out = page_enum_selects(
        '{"type":2,"reason":"请假"}',
        {"所属部门": {"options": ["研发部门", "市场部门"], "selected": "研发部门"}},
        set(),
        fields=[
            {"path": "type", "key": "type", "suggest_name": "请假类型", "value": "2"},
            {"path": "reason", "key": "reason", "suggest_name": "原因", "value": "请假"},
        ],
    )

    assert out == []


def test_api_enum_rejects_ocr_like_or_mismatched_selected_text():
    read = [{
        "url": "/dict/leave",
        "json": {"data": [
            {"value": "1", "label": "事假"},
            {"value": "2", "label": "病假"},
            {"value": "3", "label": "年假"},
        ]},
    }]

    for recorded_text in ("冰机", "实际", "年假"):
        selects = suggest_selects(
            '{"type":"2"}',
            read,
            {"请假类型": recorded_text},
            fields=[_strict_select_field("type", "请假类型")],
        )
        assert selects == []


async def test_resolve_select_uses_current_source_not_stale_option_map(monkeypatch):
    from dano.execution.page import request_capture as rc

    api_request = {"selects": [{
        "param": "请假类型",
        "source_url": "/dict/new",
        "value_key": "value",
        "label_key": "label",
        "option_map": {"病假": "OLD-2"},
        "option_map_source_url": "/dict/old",
        "enum_source": "api",
    }]}

    async def current_source(*args, **kwargs):
        return [{"value": "NEW-2", "label": "病假"}]

    monkeypatch.setattr(rc, "_fetch_select_list", current_source)
    fields, _ = await _resolve_selects(
        api_request, {"请假类型": "病假"}, base_url="", storage_state=None,
        token_key=None, verify=True,
    )
    assert fields["请假类型"] == "NEW-2"


async def test_resolve_select_does_not_fuzzy_map_label(monkeypatch):
    from dano.execution.page import request_capture as rc

    api_request = {"selects": [{
        "param": "请假类型", "source_url": "/dict/leave",
        "value_key": "value", "label_key": "label",
    }]}

    async def current_source(*args, **kwargs):
        return [{"value": "2", "label": "病假"}]

    monkeypatch.setattr(rc, "_fetch_select_list", current_source)
    import pytest as _pt

    with _pt.raises(ValueError, match="不在实时候选接口中"):
        await _resolve_selects(
            api_request, {"请假类型": "病假(年度)"}, base_url="", storage_state=None,
            token_key=None, verify=True,
        )


def test_build_api_request_carries_page_enum_options():
    """发布构建:DOM 覆盖后的 3 项选项随 select 进资产(导出/manifest 就只见 3 项,不是 222)。"""
    selects = [{"path": "type", "label": "周末加班", "value": "2", "source_url": "/dict",
                "value_key": "dictValue", "label_key": "dictLabel",
                "options": ["工作日加班", "周末加班", "节假日加班"],
                "option_map": {"工作日加班": 1, "周末加班": 2, "节假日加班": 3},
                "count": 3, "enum_source": "dom", "enum_confirmed": True,
                "option_map_source_url": "/dict", "enum_label_source": "dom"}]
    apir = build_api_request({"url": "http://x/ot", "post_data": '{"type":"2"}'}, {"type": "加班类型"}, selects=selects)
    sm = next(s for s in apir["selects"] if s["param"] == "加班类型")
    assert sm["options"] == ["工作日加班", "周末加班", "节假日加班"] and apir["field_types"]["加班类型"] == "enum"
    assert sm["option_map"] == {"工作日加班": 1, "周末加班": 2, "节假日加班": 3}
    assert sm["enum_source"] == "dom"
    assert sm["option_map_source_url"] == "/dict" and sm["enum_label_source"] == "dom"


async def test_resolve_static_enum_uses_option_map_without_source_url():
    api_request = {
        "selects": [{
            "param": "类型",
            "source_url": "",
            "options": ["事假", "病假"],
            "option_map": {"事假": 2, "病假": 3},
            "enum_source": "dom",
        }]
    }

    fields, overrides = await _resolve_selects(
        api_request,
        {"类型": "事假"},
        base_url="",
        storage_state=None,
        token_key=None,
        verify=True,
    )

    assert fields["类型"] == 2
    assert overrides == {}


async def test_fetch_field_options_prefers_live_source_over_dom_snapshot(monkeypatch):
    from dano.execution.page import request_capture as rc
    api_request = {
        "selects": [{
            "param": "类型",
            "source_url": "/dict/all",
            "value_key": "value",
            "label_key": "label",
            "options": ["事假", "病假"],
            "option_map": {"事假": 2, "病假": 3},
            "enum_source": "dom",
        }]
    }

    async def fake_fetch(url, base_url, storage_state, token_key, verify, auth_headers):
        return [{"value": 4, "label": "调休"}, {"value": 5, "label": "年假"}]
    monkeypatch.setattr(rc, "_fetch_list", fake_fetch)
    res = await fetch_field_options(api_request, "类型")

    assert res["options"] == [{"label": "调休", "value": 4}, {"label": "年假", "value": 5}]
    assert "实时接口" in res["note"]


async def test_fetch_field_options_falls_back_to_dom_snapshot_when_live_empty(monkeypatch):
    from dano.execution.page import request_capture as rc
    api_request = {
        "selects": [{
            "param": "类型",
            "source_url": "/dict/all",
            "value_key": "value",
            "label_key": "label",
            "options": ["事假", "病假"],
            "option_map": {"事假": 2, "病假": 3},
            "enum_source": "dom",
        }]
    }

    async def fake_fetch(url, base_url, storage_state, token_key, verify, auth_headers):
        return []
    monkeypatch.setattr(rc, "_fetch_list", fake_fetch)
    res = await fetch_field_options(api_request, "类型")
    assert res["options"] == [{"label": "事假", "value": 2}, {"label": "病假", "value": 3}]
    assert "回退录制页面下拉快照" in res["note"]


async def test_resolve_static_multi_enum_uses_option_map():
    api_request = {
        "selects": [{
            "param": "标签",
            "multi": True,
            "source_url": "",
            "options": ["紧急", "重要"],
            "option_map": {"紧急": "A", "重要": "B"},
            "enum_source": "dom",
        }]
    }

    out = await _resolve_list_selects(
        api_request,
        {"标签": ["紧急", "重要"]},
        base_url="",
        storage_state=None,
        token_key=None,
        verify=True,
    )

    assert out["标签"] == ["A", "B"]


def test_manifest_list_enum_live_directory_no_inline_enum():
    """列表多选**选人**(参会人:网络源、人会变)= 活接口目录 → schema=数组,items=name-ref,**不烤 items.enum/快照**,
    只暴露 x-options-source 让调用方运行期 --list-options 现拉(治"前端选了陈旧/错误人名导致入库失败")。"""
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem
    sk = SkillSpec(skill_id="A-OA.m", subsystem=Subsystem.OA, action="m", risk_level=RiskLevel.L3,
                   field_types={"参会人": "list-enum"}, required_fields=["参会人"],
                   api_request={"selects": [{"param": "参会人", "multi": True, "source_url": "/u",
                                             "value_key": "userId", "label_key": "nickName",
                                             "options": ["测试号02", "狗蛋", "张三"], "count": 3}]})
    p = to_manifest(sk).parameters["properties"]["参会人"]
    assert p["type"] == "array" and p["items"]["format"] == "name-ref"
    assert "enum" not in p["items"] and "x-options" not in p   # 活目录:不烤静态清单
    assert p["x-options-source"] is True and "--list-options" in p["description"]


def test_manifest_list_enum_static_dom_inlines():
    """列表多选但来自**固定下拉**(enum_source=dom,如固定标签多选)→ 静态枚举 → items.enum 内置(≤50)。"""
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem
    sk = SkillSpec(skill_id="A-OA.tags", subsystem=Subsystem.OA, action="tags", risk_level=RiskLevel.L3,
                   field_types={"标签": "list-enum"}, required_fields=["标签"],
                   api_request={"selects": [{"param": "标签", "multi": True, "source_url": "",
                                             "value_key": "v", "label_key": "l",
                                             "options": ["紧急", "重要", "常规"], "count": 3,
                                             "enum_source": "dom"}]})
    p = to_manifest(sk).parameters["properties"]["标签"]
    assert p["items"]["enum"] == ["紧急", "重要", "常规"] and p["x-options"] == ["紧急", "重要", "常规"]


def test_manifest_large_static_dom_enum_no_inline_but_snapshot():
    """**静态**页面枚举 >50(enum_source=dom 的大固定下拉)→ 不内置 enum(过大),但仍快照进 x-options。"""
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem
    opts = [f"系统{i}" for i in range(135)]
    sk = SkillSpec(skill_id="A-OA.g", subsystem=Subsystem.OA, action="g", risk_level=RiskLevel.L3,
                   field_types={"应用系统名称": "enum"}, required_fields=["应用系统名称"],
                   api_request={"selects": [{"param": "应用系统名称", "source_url": "/x",
                                             "value_key": "id", "label_key": "xtmc",
                                             "options": opts, "count": 135, "enum_source": "dom"}]})
    p = to_manifest(sk).parameters["properties"]["应用系统名称"]
    assert "enum" not in p and len(p["x-options"]) == 135      # 不内置 enum,但快照全在
    assert p.get("x-options-source") is True                   # 有来源接口 → 可 --list-options 实时拉
    assert "--list-options" in p["description"]


def test_export_options_md_lists_candidates():
    """问题1:导出 references/OPTIONS.md 列出**静态枚举**(DOM 固定下拉)候选值;无候选则不产生该段。"""
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _options_md
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem
    sk = SkillSpec(skill_id="A-OA.f", subsystem=Subsystem.OA, action="f", risk_level=RiskLevel.L3,
                   field_types={"请假类型": "enum"}, required_fields=["请假类型"], title="请假",
                   api_request={"selects": [{"param": "请假类型", "source_url": "/d",
                                             "value_key": "dictValue", "label_key": "dictLabel",
                                             "options": ["事假", "病假"], "count": 2, "enum_source": "dom"}]})
    md = _options_md(to_manifest(sk))
    assert md and "事假" in md and "病假" in md and "请假类型" in md


def test_export_options_md_live_directory_points_to_runtime():
    """活接口目录字段(选人/审批人:无 DOM 固定下拉)→ OPTIONS.md **不列陈旧快照**,只指向运行期 --list-options。"""
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _options_md
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel, Subsystem
    sk = SkillSpec(skill_id="A-OA.appr", subsystem=Subsystem.OA, action="appr", risk_level=RiskLevel.L3,
                   field_types={"审批人1": "enum"}, required_fields=["审批人1"], title="请假",
                   api_request={"selects": [{"param": "审批人1", "source_url": "/system/user/list",
                                             "value_key": "userId", "label_key": "nickName",
                                             "options": ["财务部门", "研发部门"], "count": 6}]})
    md = _options_md(to_manifest(sk))
    assert md and "实时接口" in md and "--list-options 审批人1" in md
    assert "财务部门" not in md                              # 不列陈旧/错误快照


def test_suggest_selects_name_id_pair_detected():
    """名/ID 配对(根治问题4):body 里 yyxtmc=显示名 + 兄弟 yyxtid=内部 id 一次选定 →
    绑 yyxtmc(传名),并带 id_path=yyxtid → 运行期解析后同时写回 id,不冻结。通用,不挑系统。"""
    sub = ('{"ywsxList":[{"yyxtmc":"徐州市审计局_共享交换数据服务应用",'
           '"yyxtid":"02021060111315890400001010018"}]}')
    read = [{"url": "http://oa/api/getXxxtListByBm", "json": {"data": [
        {"id": "02021060111315890400001010018", "xtmc": "徐州市审计局_共享交换数据服务应用"},
        {"id": "99990000", "xtmc": "其它系统"}]}}]
    samples = {"应用系统名称": "徐州市审计局_共享交换数据服务应用"}
    s = suggest_selects(
        sub,
        read,
        samples,
        fields=[_strict_select_field("ywsxList[0].yyxtmc", "应用系统名称")],
    )
    assert len(s) == 1
    b = s[0]
    assert b["path"] == "ywsxList[0].yyxtmc"           # 显示名字段作 select 参数(agent 传名)
    assert b["value_key"] == "id" and b["label_key"] == "xtmc"
    assert b["id_path"] == "ywsxList[0].yyxtid"         # 配对 id 字段(运行期同步)
    assert b["id_tokens"] == ["ywsxList", 0, "yyxtid"]


def test_build_api_request_learns_success_rule_from_own_response():
    """P1:单提交接口**自身响应**(code=200)→ 资产带 success_rule + response_json 证据,
    无需额外 GET 查询读 → acceptance 能验"业务成功",不再报"无法验证"。"""
    req = {"method": "POST", "url": "http://oa/x", "post_data": '{"reason":"回家"}',
           "response_json": {"code": 200, "msg": "ok", "data": {"taskId": "T1"}}}
    apir = build_api_request(req, {"reason": "原因"})
    assert apir["success_rule"] == {"field": "code", "ok_values": ["200"]}
    assert apir["response_json"]["data"]["taskId"] == "T1"




def test_build_api_request_carries_select_id_pair():
    """build:名/ID 配对的 id 字段路径进 sel_meta;id 字段本身是常量(不作参数)。"""
    req = {"method": "POST", "url": "http://oa/x",
           "post_data": '{"ywsxList":[{"yyxtmc":"应用A","yyxtid":"02021060111315890400001010018"}]}'}
    selects = [{"path": "ywsxList[0].yyxtmc", "source_url": "http://oa/list",
                "value_key": "id", "label_key": "xtmc",
                "id_path": "ywsxList[0].yyxtid", "id_tokens": ["ywsxList", 0, "yyxtid"]}]
    apir = build_api_request(req, {"ywsxList[0].yyxtmc": "应用系统名称"}, selects=selects)
    assert apir["params"] == ["应用系统名称"]            # id 字段不是参数(常量)
    sm = apir["selects"][0]
    assert sm["id_tokens"] == ["ywsxList", 0, "yyxtid"]


async def test_resolve_selects_sets_both_name_and_id(monkeypatch):
    """运行期(根治问题4):agent 传应用系统名 → 同时规整显示名 + 写回配对 id 字段(换选项 id 不冻结)。"""
    from dano.execution.page import request_capture as rc
    apir = {"method": "POST", "url": "http://oa/x",
            "selects": [{"param": "应用系统名称", "source_url": "http://oa/list",
                         "value_key": "id", "label_key": "xtmc",
                         "id_path": "ywsxList[0].yyxtid", "id_tokens": ["ywsxList", 0, "yyxtid"]}]}

    async def fake_fetch(*a, **k):
        return [{"id": "ID_NEW_777", "xtmc": "应用B"}, {"id": "ID_A_111", "xtmc": "应用A"}]
    monkeypatch.setattr(rc, "_fetch_list", fake_fetch)
    # agent 选了"应用B"(与录制的"应用A"不同)→ 名字段规整=应用B、配对 id=该项 id(不再是录制的 02021…)
    fields, overrides = await rc._resolve_selects(apir, {"应用系统名称": "应用B"}, base_url="",
                                                  storage_state=None, token_key=None, verify=False)
    assert fields["应用系统名称"] == "应用B"             # 显示名规整成候选规范名
    assert overrides[("ywsxList", 0, "yyxtid")] == "ID_NEW_777"   # 配对 id 同步成新选项的 id


async def test_resolve_selects_single_code_field_unchanged():
    """对照:单码字段(无 id_path)仍是把字段值换成 id(老行为不变)。"""
    from dano.execution.page import request_capture as rc

    async def fake_fetch(*a, **k):
        return [{"userId": 12, "nickName": "张经理"}, {"userId": 34, "nickName": "李总"}]
    import pytest as _pt
    with _pt.MonkeyPatch.context() as mp:
        mp.setattr(rc, "_fetch_list", fake_fetch)
        apir = {"selects": [{"param": "审批人", "source_url": "/u", "value_key": "userId", "label_key": "nickName"}]}
        fields, overrides = await rc._resolve_selects(apir, {"审批人": "张经理"}, base_url="",
                                                      storage_state=None, token_key=None, verify=False)
    assert fields["审批人"] == 12 and overrides == {}   # 字段值换成 id;无配对 id 覆盖


async def test_resolve_selects_static_enum_does_not_fetch(monkeypatch):
    """静态枚举无 source_url:运行期按用户选择的显示值原样提交,不能请求空地址。"""
    from dano.execution.page import request_capture as rc

    async def fail_fetch(*args, **kwargs):
        raise AssertionError("static enum should not call source api")

    monkeypatch.setattr(rc, "_fetch_list", fail_fetch)
    apir = {"selects": [{"param": "请假类型", "source_url": "",
                         "options": ["事假", "病假"], "enum_source": "dom"}]}
    fields, overrides = await rc._resolve_selects(apir, {"请假类型": "病假"}, base_url="",
                                                  storage_state=None, token_key=None, verify=False)

    assert fields == {"请假类型": "病假"}
    assert overrides == {}


async def test_resolve_list_selects_static_string_enum_keeps_names(monkeypatch):
    """静态字符串多选无 source_url/模板:运行期保持名字列表,不能误构造成空对象数组。"""
    from dano.execution.page import request_capture as rc

    async def fail_fetch(*args, **kwargs):
        raise AssertionError("static list enum should not call source api")

    monkeypatch.setattr(rc, "_fetch_list", fail_fetch)
    apir = {"selects": [{"param": "标签", "multi": True, "source_url": "",
                         "options": ["紧急", "重要"], "enum_source": "dom"}]}
    fields = await rc._resolve_list_selects(apir, {"标签": ["紧急", "重要"]}, base_url="",
                                            storage_state=None, token_key=None, verify=False)

    assert fields == {"标签": ["紧急", "重要"]}


async def test_resolve_list_selects_static_template_enum_without_fetch(monkeypatch):
    """静态对象多选无 source_url:按模板生成对象,不请求空接口。"""
    from dano.execution.page import request_capture as rc

    async def fail_fetch(*args, **kwargs):
        raise AssertionError("static template enum should not call source api")

    monkeypatch.setattr(rc, "_fetch_list", fail_fetch)
    apir = {"selects": [{
        "param": "参会人",
        "multi": True,
        "source_url": "",
        "element_template": {"userName": {"item_key": "name"}, "participantType": {"const": "normal"}},
        "label_subkey": "userName",
        "options": ["张三", "李四"],
        "enum_source": "dom",
    }]}
    fields = await rc._resolve_list_selects(apir, {"参会人": ["张三", "李四"]}, base_url="",
                                            storage_state=None, token_key=None, verify=False)

    assert fields == {"参会人": [
        {"userName": "张三", "participantType": "normal"},
        {"userName": "李四", "participantType": "normal"},
    ]}


def test_date_keys_handles_seconds_and_slash_formats():
    """日期跨格式泛化:10 位秒戳、13 位毫秒戳、斜杠/单位数日期串都能抽出 YYYY-MM-DD,供日期字段标签匹配。"""
    from dano.execution.page.request_capture import _date_keys
    assert "2024-06-24" in _date_keys("1719196800")        # 10 位秒级时间戳(原来不支持)
    assert "2026-06-24" in _date_keys("1782230400000")     # 13 位毫秒(原有)
    assert _date_keys("2026/6/24") == {"2026-06-24"}       # 斜杠 + 单位数月日


def test_stringified_json_body_field_unwrapped_and_restringified():
    """若依/工作流把整张表单打成 JSON 字符串塞进 formData → 内层字段可独立参数化;运行期 re-stringify 回字符串。
    通用:任何"请求体里被字符串化的 JSON"都解得开,不挑系统/字段。"""
    import json as _j
    inner = {"formData": {"fields": [{"label": "数量", "value": 5}, {"label": "单价", "value": 120}]}}
    body = {"templateId": "t", "formData": {"taskId": "", "formData": _j.dumps(inner, ensure_ascii=False)}}
    pd = _j.dumps(body, ensure_ascii=False)
    # 内层 value 叶子被拍出来,且按用户填的值对上中文名
    leaves = flatten_body(pd, {"数量": "5", "单价": "120"})
    by_val = {f["value"]: f["suggest_name"] for f in leaves}
    assert by_val.get("5") == "数量" and by_val.get("120") == "单价"
    # 参数化内层"数量" → 运行期填 9 → finalize 后 formData.formData 是字符串且值已变
    from dano.execution.page.request_capture import _finalize_jsonstr
    qpath = next(f["path"] for f in leaves if f["value"] == "5")
    apir = build_api_request({"method": "POST", "url": "http://x/save", "post_data": pd}, {qpath: "数量"})
    out = _finalize_jsonstr(substitute(apir["body_template"], {"数量": 9}, apir["sample_inputs"]))
    fs = out["formData"]["formData"]
    assert isinstance(fs, str) and _j.loads(fs)["formData"]["fields"][0]["value"] == 9
    assert out["formData"]["taskId"] == ""              # 顶层字段不受影响(仍可被串联/identity 注入)


def test_identity_inside_jsonstr_blob_applied_before_restringify():
    """BUG 回归:申请人/串联值在 blob 内层时,必须在 re-stringify 前注入,否则会冻结成录制者。
    substitute(保留标记) → _set_by_path 改 blob 内字段 → _finalize_jsonstr 压回字符串,顺序对 → 值真被改。"""
    import json as _j
    from dano.execution.page.request_capture import _JSONSTR, _finalize_jsonstr, _set_by_path
    body = substitute({"formData": {_JSONSTR: {"applicant": "录制者"}}}, {}, {})
    assert body["formData"] == {_JSONSTR: {"applicant": "录制者"}}     # substitute 后仍是嵌套(未提前压字符串)
    _set_by_path(body, f"formData.{_JSONSTR}.applicant", "当前用户")   # identity 重取(blob 内可达)
    out = _finalize_jsonstr(body)
    assert _j.loads(out["formData"])["applicant"] == "当前用户"        # 不再是录制者 ✓


def test_looks_internal_param_name_flags_machine_ids_only():
    """安全网:产出参数名若漏成内部机器标识(BPM 节点 Activity_xxx / hash)→ 判 True 供告警;
    正常字段名(reason/apply_reason/leave_type/startTime/中文)不误判。"""
    from dano.execution.page.request_capture import looks_internal_param_name as L
    assert L("Activity_09dlq0g") and L("Activity_0ag2wyz") and L("550e8400e29b41d4")
    assert not L("reason") and not L("apply_reason") and not L("leave_type")
    assert not L("startTime") and not L("type") and not L("领导") and not L("请假类型")


def test_suggest_selects_binds_short_code_in_big_dict_when_recorded_confirms():
    """大全局字典(上千项)里短码 type=2:无录制佐证不绑(防误报);录制确实选了『病假』→ 精确绑对 oa_leave_type 那项。
    修"假期类型在全局字典里绑不上"的根因 —— 用录制选中值消歧/确认,不靠列表大小一刀切。"""
    big = ([{"dictType": "sys_yes_no", "value": "2", "label": "否"}]
           + [{"dictType": "oa_leave_type", "value": v, "label": l} for v, l in (("1", "事假"), ("2", "病假"))]
           + [{"dictType": "x", "value": "2", "label": "噪声"} for _ in range(1430)])
    read = [{"url": "/admin-api/system/dict-data/simple-list", "json": {"code": 0, "data": big}}]
    sub = '{"type": 2, "reason": "x"}'
    assert suggest_selects(sub, read) == []                            # 无 samples → 大字典短码不乱绑(原精度)
    s = suggest_selects(
        sub,
        read,
        {"请假类型": "病假", "原因": "x"},
        fields=[_strict_select_field("type", "请假类型")],
    )                                                                  # 录制选了"病假" → 确认命中
    assert len(s) == 1 and s[0]["path"] == "type" and s[0]["label"] == "病假"


def test_pick_label_key_prefers_display_name_over_login():
    """选人列表 {id, username, nickname}:label 取**显示名** nickname(张三),不取登录名 username(zhangsan)。
    否则名字→ID 桥接与运行期解析都对不上(用户选人看的是显示名)。"""
    from dano.execution.page.request_capture import _pick_label_key
    assert _pick_label_key({"id": 138, "username": "zhangsan", "nickname": "张三"}, "id") == "nickname"
    assert _pick_label_key({"userId": 1, "nickName": "张经理", "deptName": "研发"}, "userId") == "nickName"


def test_suggest_select_names_bridges_picker_label_to_param_name():
    """select/选人字段参数名:候选显示名(张三)== 录制样例某字段的值 → 用那字段标签(领导)当参数名,
    修"选人字段参数名漏成内部 key(Activity_xxx/嵌套键)"的根因。通用,不挑字段。"""
    selects = [{"path": "startUserSelectAssignees.Activity_09dlq0g[0]", "label": "张三"},
               {"path": "startUserSelectAssignees.Activity_0ag2wyz[0]", "label": "李四"}]
    samples = {"领导": "张三", "人力": "李四", "原因": "回家"}
    out = suggest_select_names(selects, samples)
    assert out["startUserSelectAssignees.Activity_09dlq0g[0]"] == "领导"
    assert out["startUserSelectAssignees.Activity_0ag2wyz[0]"] == "人力"
    assert suggest_select_names([], samples) == {}              # 无 select → 空,不瞎给


def test_suggest_identity_flags_current_user_fields():
    """Q1 身份坑:提交体 applicantId=118 等于登录态 userInfo.userId → 标 identity(运行期重取,不冻结)。"""
    submit = '{"applicantId":118,"applicant":"赵六","reason":"回家","procDefKey":"oa_leave"}'
    storage = {"origins": [{"localStorage": [
        {"name": "userInfo", "value": '{"userId":118,"nickName":"赵六","dept":"研发"}'}]}],
        "cookies": [{"name": "JSESSIONID", "value": "abc"}]}
    ids = {i["path"]: i["source"] for i in suggest_identity(submit, storage)}
    assert ids["applicantId"] == "localStorage:userInfo.userId"   # 当前用户 id → 运行期重取
    assert ids["applicant"] == "localStorage:userInfo.nickName"   # 当前用户名 → 运行期重取
    assert "reason" not in ids and "procDefKey" not in ids        # 业务/常量不误判


def test_build_api_request_stores_select_and_identity_meta():
    """P4:勾选的 select(path 是参数)记成 param→源/键;identity 记 path→来源,供运行期。"""
    req = {"method": "POST", "url": "http://oa.x/api/leave/submit",
           "post_data": '{"reason":"回家","approverId":12,"applicantId":118}'}
    apir = build_api_request(req, {"reason": "reason", "approverId": "approver"},
                             selects=[{"path": "approverId", "source_url": "/system/user/list",
                                       "value_key": "userId", "label_key": "nickName"}],
                             identity=[{"path": "applicantId", "source": "localStorage:userInfo.userId"}])
    assert apir["body_template"]["approverId"] == "{{approver}}"        # select 字段是参数
    assert apir["body_template"]["applicantId"] == 118                  # identity 留常量,运行期覆盖
    sm = apir["selects"][0]
    assert {k: sm[k] for k in ("param", "source_url", "value_key", "label_key")} == {
        "param": "approver", "source_url": "/system/user/list",
        "value_key": "userId", "label_key": "nickName"}
    assert "options" in sm                                  # 选项快照位(此处无 reads → 空)
    assert apir["identity"] == [{"path": "applicantId", "source": "localStorage:userInfo.userId",
                                 "evidence": ["request://body.applicantId", "identity://localStorage:userInfo.userId"],
                                 "tokens": ["applicantId"]}]   # tokens 反查补全 + 证据来源(node 8)


def test_resolve_identity_value_from_storage():
    storage = {"origins": [{"localStorage": [
        {"name": "userInfo", "value": '{"userId":118,"nickName":"赵六"}'},
        {"name": "token", "value": "raw-token-xyz"}]}],
        "cookies": [{"name": "JSESSIONID", "value": "sid-1"}]}
    assert resolve_identity_value("localStorage:userInfo.userId", storage) == 118
    assert resolve_identity_value("localStorage:userInfo.nickName", storage) == "赵六"
    assert resolve_identity_value("localStorage:token", storage) == "raw-token-xyz"   # 非 JSON 整存
    assert resolve_identity_value("cookie:JSESSIONID", storage) == "sid-1"
    assert resolve_identity_value("localStorage:missing.x", storage) is None


async def test_execute_resolves_select_name_to_id_and_identity(tmp_path):
    """P4 真 HTTP(无需 PG/chromium):传"张经理"→ 查 user/list 换成 ID 12;applicantId 用会话当前用户覆盖。"""
    import http.server as _h
    import json as _j
    import socketserver as _s
    import threading as _t

    received = {}

    class H(_h.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: ANN001
            pass

        def do_GET(self):
            body = _j.dumps({"rows": [{"userId": 12, "nickName": "张经理"},
                                      {"userId": 34, "nickName": "李总"}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            received.update(_j.loads(self.rfile.read(n) or b"{}"))
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(b'{"code":200}')

    httpd = _s.TCPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    _t.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    storage = {"origins": [{"localStorage": [{"name": "userInfo", "value": '{"userId":118}'}]}], "cookies": []}
    try:
        apir = {"method": "POST", "url": f"{base}/leave/submit", "content_type": "application/json",
                "body_template": {"approverId": "{{approver}}", "applicantId": 999, "reason": "{{reason}}"},
                "params": ["approver", "reason"], "auth_headers": {},
                "selects": [{"param": "approver", "source_url": f"{base}/system/user/list",
                             "value_key": "userId", "label_key": "nickName"}],
                "identity": [{"path": "applicantId", "source": "localStorage:userInfo.userId"}]}
        out = await execute_api_request(apir, {"approver": "张经理", "reason": "回家"},
                                        storage_state=storage, send=True, verify=False)
        assert out["ok"] and out["status"] == 200
        assert received["approverId"] == 12          # 名字"张经理"→ 内部 ID 12(Q2)
        assert received["applicantId"] == 118        # 申请人=会话当前用户,非录制的 999(Q1)
        assert received["reason"] == "回家"
    finally:
        httpd.shutdown()


async def test_execute_business_fail_despite_http_200():
    """不信 HTTP 200:服务器回 200 但 body code=500 → 判失败(空操作);code=200 → 成功。通用。"""
    import http.server as _h
    import json as _j
    import socketserver as _s
    import threading as _t

    mode = {"code": 500}

    class H(_h.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: ANN001
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(_j.dumps({"code": mode["code"], "msg": "结果"}).encode())

    httpd = _s.TCPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    _t.Thread(target=httpd.serve_forever, daemon=True).start()
    apir = {"method": "POST", "url": f"http://127.0.0.1:{port}/submit", "content_type": "application/json",
            "body_template": {"reason": "{{原因}}"}, "params": ["原因"], "auth_headers": {}}
    try:
        mode["code"] = 500                                    # HTTP 200 但业务失败
        out = await execute_api_request(apir, {"原因": "x"}, send=True, verify=False)
        assert out["status"] == 200 and out["ok"] is False and out["business_ok"] is False
        mode["code"] = 200                                    # 业务成功
        out2 = await execute_api_request(apir, {"原因": "x"}, send=True, verify=False)
        assert out2["ok"] is True
    finally:
        httpd.shutdown()


def test_suggest_fact_check_finds_records_list():
    """没有写后时序/事务证据的相似列表不能被猜成事实核查。"""
    samples = {"原因": "去北京出差三天", "类型": "事假"}
    reads = [{"url": "http://oa.x/leave/list",
              "json": {"rows": [{"id": 9, "reason": "去北京出差三天", "status": "审批中"}]}}]
    fc = suggest_fact_check(samples, reads)
    assert fc is None


async def test_execute_api_grounded_fact_check():
    """grounded 回查:提交后 GET 记录列表,提交值在记录里 → 真生效;不在 → 判失败(空操作)。"""
    import http.server as _h
    import json as _j
    import socketserver as _s
    import threading as _t

    state = {"persist": True, "records": []}

    class H(_h.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: ANN001
            pass

        def do_POST(self):
            body = _j.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if state["persist"]:
                state["records"].append({"reason": body.get("reason")})
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(b'{"code":200}')

        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(_j.dumps({"rows": state["records"]}).encode())

    httpd = _s.TCPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    _t.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    apir = {"method": "POST", "url": f"{base}/submit", "content_type": "application/json",
            "body_template": {"reason": "{{原因}}"}, "params": ["原因"], "auth_headers": {},
            "fact_check": {"endpoint": f"{base}/list", "match_field": "reason", "param": "原因",
                           "retries": 2, "backoff_s": 0.05}}
    try:
        out = await execute_api(apir, {"原因": "回家A"}, send=True, verify=False)
        assert out["ok"] is True and out["fact_check_passed"] is True   # POST 入库 → 列表含 → 回查过
        state["persist"] = False
        out2 = await execute_api(apir, {"原因": "回家B"}, send=True, verify=False)
        assert out2["fact_check_passed"] is False and out2["ok"] is False  # 不入库 → 列表没有 → 空操作判失败
    finally:
        httpd.shutdown()


def test_response_ok_judges_business_code():
    """业务成功判定(纯函数,通用):code/status/success;无成功字段→靠 HTTP。"""
    assert _response_ok({"code": 200, "msg": "ok"})[0] is True
    assert _response_ok({"code": 500, "msg": "余额不足"})[0] is False
    assert _response_ok({"status": 0})[0] is True
    assert _response_ok({"success": False})[0] is False
    assert _response_ok({"rows": [1, 2], "total": 2})[0] is True   # 列表响应无业务码 → 靠 HTTP
    assert _response_ok("OK")[0] is True


def test_extract_total_detects_pagination_generally():
    """P2:从分页响应抽 total(顶层/一层包装,跨系统),无分页则 None → 回查不据此误判失败。"""
    assert _extract_total({"code": 200, "rows": [1, 2], "total": 57}) == 57
    assert _extract_total({"data": {"records": [1], "total": 120}}) == 120
    assert _extract_total({"rows": [1, 2, 3]}) is None        # 无分页字段
    assert _extract_total({"success": True, "data": [1]}) is None   # bool 不当 total


def test_subsystem_is_open_for_any_system():
    """P0#1:系统标识开放 —— 任意租户任意系统都可作 subsystem,不再限于三件套原型。"""
    from dano.shared.enums import Subsystem
    from dano.shared.models import Scope
    assert Subsystem.OA.value == "A-OA"                       # 原型常量仍在
    x = Subsystem("B-合同审批")                                # 任意系统:不抛 ValueError
    assert x.value == "B-合同审批" and x == "B-合同审批"
    sc = Scope(tenant="acme", subsystem=Subsystem("C-门户"))   # pydantic 字段接受任意系统
    assert sc.subsystem.value == "C-门户"
    assert {Subsystem("新"): 1}[Subsystem("新")] == 1          # 可作字典键
    assert [s.value for s in Subsystem] == ["A-OA", "A-工单", "A-报销"]   # 枚举仍只列原型


def test_pick_submit_excludes_auth_by_content_not_path():
    """P0#3:提交识别不靠系统专属路径名 —— 登录(含 password)按内容排除,业务提交按"带用户值"选中。"""
    reqs = [
        {"method": "POST", "url": "http://x/any/login-action", "post_data": '{"user":"u","password":"p"}'},
        {"method": "POST", "url": "http://x/biz/apply", "post_data": '{"reason":"大地色多","days":2}'},
        {"method": "POST", "url": "http://x/keepalive", "post_data": '{"t":1}'},   # 心跳:不含用户值
    ]
    got = pick_submit_request(reqs, {"原因": "大地色多"})
    assert got is not None and got["url"] == "http://x/biz/apply"
    # 整段匹配避免子串误伤:'lesson' 不因含 'sso' 被当鉴权;'/oauth/token' 命中
    assert looks_like_auth_write("http://x/lesson/submit", '{"reason":"r"}') is False
    assert looks_like_auth_write("http://x/oauth/token", "{}") is True
    assert looks_like_auth_write("http://x/biz/token-apply", '{"reason":"r"}') is False


def test_infer_success_rule_learns_system_convention():
    """P0#2 泛化核心:从本系统真实成功读响应学成功约定,不假设 200。"""
    # 若依:读响应普遍 code=200
    assert infer_success_rule([{"json": {"code": 200, "rows": [1]}},
                               {"json": {"code": 200, "data": [2]}}]) == {"field": "code", "ok_values": ["200"]}
    # 阿里系:code="0" —— 绝不被强加成 200
    assert infer_success_rule([{"json": {"code": "0", "data": {"list": [1]}}}]) == {"field": "code", "ok_values": ["0"]}
    # success 布尔约定
    assert infer_success_rule([{"json": {"success": True, "data": [1]}}]) == {"field": "success", "ok_values": ["true"]}
    # 没有可学的(纯数组/无码字段)→ None
    assert infer_success_rule([{"json": [1, 2, 3]}, {"json": None}]) is None


def test_response_ok_honors_learned_rule_over_200_assumption():
    """P0#2:某系统 code=1 才是成功 → 用学到的规则判对;且 code=200 在该系统反而判失败。"""
    rule = {"field": "code", "ok_values": ["1"]}
    assert _response_ok({"code": 1, "msg": "ok"}, rule)[0] is True
    assert _response_ok({"code": 200}, rule)[0] is False        # 不再无脑认 200
    # 规则字段这次没出现 → 退兜底启发式,不硬判
    assert _response_ok({"status": 0}, rule)[0] is True


def test_discover_step_links_finds_taskid_chain():
    """Q3:第2步 body 的 taskId 来自第1步响应 data.taskId → 自动发现 step 链。"""
    writes = [
        {"post_data": '{"leaveType":"事假"}', "response_json": {"code": 200, "data": {"taskId": "TASK-99887"}}},
        {"post_data": '{"flowTask":{"taskId":"TASK-99887","comment":"同意"}}', "response_json": {"code": 200}},
    ]
    links = discover_step_links(writes)
    assert links == [{"target_step": 1, "target_path": "flowTask.taskId",
                      "target_tokens": ["flowTask", "taskId"],
                      "source_step": 0, "source_path": "data.taskId",
                      "source_tokens": ["data", "taskId"]}]


def test_discover_step_links_ignores_short_constants():
    """短值(0/1/状态码)不连成步链,避免误判。"""
    writes = [{"post_data": '{"x":1}', "response_json": {"code": 1}},
              {"post_data": '{"y":1}', "response_json": {"code": 200}}]
    assert discover_step_links(writes) == []


async def test_execute_api_workflow_chains_taskid_two_steps():
    """Q3 真 HTTP 两步:第1步起流程返回 taskId → 注入第2步提交体(step 链跑通)。"""
    import http.server as _h
    import json as _j
    import socketserver as _s
    import threading as _t

    seen = {"step2": None}

    class H(_h.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: ANN001
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            payload = _j.loads(self.rfile.read(n) or b"{}")
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            if self.path.endswith("/start"):
                self.wfile.write(_j.dumps({"code": 200, "data": {"taskId": "TASK-77"}}).encode())
            else:
                seen["step2"] = payload
                self.wfile.write(b'{"code":200}')

    httpd = _s.TCPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    _t.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        workflow = {"steps": [
            {"method": "POST", "url": f"{base}/flow/start", "content_type": "application/json",
             "body_template": {"leaveType": "{{leaveType}}"}, "auth_headers": {}},
            {"method": "POST", "url": f"{base}/flow/submit", "content_type": "application/json",
             "body_template": {"flowTask": {"taskId": "PLACEHOLDER", "comment": "{{comment}}"}},
             "auth_headers": {},
             "links": [{"target_path": "flowTask.taskId", "source_step": 0, "source_path": "data.taskId"}]},
        ]}
        out = await execute_api_workflow(workflow, {"leaveType": "事假", "comment": "同意"},
                                         send=True, verify=False)
        assert out["ok"] and out["steps"] == 2
        assert seen["step2"]["flowTask"]["taskId"] == "TASK-77"   # 第1步响应的 taskId 串进第2步
        assert seen["step2"]["flowTask"]["comment"] == "同意"
    finally:
        httpd.shutdown()


async def test_execute_api_workflow_dry_uses_recorded_response_for_links():
    """dry-run 不触网时使用录制响应样例串联 GET 前置值,避免 appCode/taskId 仍停在旧值。"""
    workflow = {"steps": [
        {"method": "GET", "url": "http://x/apigateway/getappid",
         "query_template": {"appId": "auto"}, "params": [],
         "response_json": {"code": 200, "data": "APP-CODE"}},
        {"method": "POST", "url": "http://x/dataiq/sjws_chat",
         "content_type": "application/json",
         "body_template": {"sys_query": "{{sys_query}}", "appCode": "OLD"},
         "params": ["sys_query"], "sample_inputs": {"sys_query": "分析销售"},
         "links": [{"target_path": "appCode", "source_step": 0, "source_path": "data"}]},
    ]}

    out = await execute_api_workflow(workflow, {}, send=False)

    assert out["ok"] is True
    assert out["final"]["body"]["appCode"] == "APP-CODE"
    assert out["final"]["body"]["sys_query"] == "分析销售"


def test_self_check_flags_missing_link_source_path_when_response_sample_exists():
    """link 的 source_path 若在上游响应样例里不存在,发布前就要拦住。"""
    workflow = {"steps": [
        {"body_template": {"x": "1"}, "params": [], "response_json": {"data": {"id": "T1"}}},
        {"body_template": {"taskId": "OLD"}, "params": [],
         "links": [{"target_path": "taskId", "source_step": 0, "source_path": "data.missing"}]},
    ]}

    assert any("来源路径" in p and "data.missing" in p for p in self_check(workflow))




async def test_execute_api_dispatches_single_and_workflow():
    """execute_api:无 steps → 单请求(dry);有 steps → 工作流(dry)。"""
    single = {"body_template": {"x": "{{a}}"}, "params": ["a"]}
    out1 = await execute_api(single, {"a": "1"}, send=False)
    assert out1["ok"] and out1.get("dry")
    wf = {"steps": [{"body_template": {"x": "{{a}}"}, "params": ["a"]}]}
    out2 = await execute_api(wf, {"a": "1"}, send=False)
    assert out2["ok"] and out2["steps"] == 1


async def test_execute_api_routes_by_capability_without_running_full_workflow():
    """一个 Skill 多能力:query_status 只跑读步骤,submit 跑完整提交链。"""
    wf = {
        "steps": [
            {
                "step_id": "query",
                "method": "GET",
                "url": "http://x/api/status",
                "path": "/api/status",
                "query_template": {},
                "params": [],
                "response_json": {"code": 200, "data": {"missing": ["2026-06-11"]}},
            },
            {
                "step_id": "submit",
                "method": "POST",
                "url": "http://x/api/submit",
                "path": "/api/submit",
                "body_template": {"reason": "{{reason}}"},
                "params": ["reason"],
                "sample_inputs": {"reason": "日报"},
            },
        ],
        "capabilities": [
            {"name": "query_status", "kind": "query_status", "step_ids": ["query"]},
            {"name": "submit", "kind": "submit", "step_ids": ["query", "submit"]},
        ],
    }

    status = await execute_api(wf, {"__capability": "query_status", "reason": "日报"}, send=False)
    assert status["ok"] is True
    assert status["steps"] == 1
    assert status["final"]["url"].endswith("/api/status")

    submitted = await execute_api(wf, {"__capability": "submit", "reason": "日报"}, send=False)
    assert submitted["ok"] is True
    assert submitted["steps"] == 2
    assert submitted["final"]["body"] == {"reason": "日报"}


async def test_execute_submit_batch_capability_repeats_entries():
    wf = {
        "steps": [{
            "step_id": "submit",
            "method": "POST",
            "url": "http://x/api/submit",
            "path": "/api/submit",
            "body_template": {"date": "{{date}}", "content": "{{content}}", "project": "{{project}}"},
            "params": ["date", "content", "project"],
        }],
        "capabilities": [{
            "name": "submit_batch",
            "kind": "submit_batch",
            "step_ids": ["submit"],
            "execution_contract": {
                "batch": {"enabled": True, "items_field": "entries", "mode": "repeat_selected_workflow"},
            },
        }],
    }

    out = await execute_api(wf, {
        "__capability": "submit_batch",
        "project": "P1",
        "entries": [
            {"date": "2026-05-12", "content": "a"},
            {"date": "2026-05-13", "content": "b"},
        ],
    }, send=False)

    assert out["ok"] is True
    assert out["batch"] is True
    assert out["total"] == 2
    assert out["success_count"] == 2
    assert out["results"][0]["final"]["body"] == {"date": "2026-05-12", "content": "a", "project": "P1"}
    assert out["results"][1]["final"]["body"] == {"date": "2026-05-13", "content": "b", "project": "P1"}


async def test_execute_capability_plan_foreach_and_return_batch_result():
    wf = {
        "steps": [
            {
                "step_id": "query",
                "method": "GET",
                "url": "http://x/api/missing",
                "path": "/api/missing",
                "query_template": {"month": "{{month}}"},
                "params": ["month"],
                "response_json": {"code": 0, "data": {"missing": ["2026-05-12", "2026-05-13"]}},
            },
            {
                "step_id": "submit",
                "method": "POST",
                "url": "http://x/api/submit",
                "path": "/api/submit",
                "body_template": {"date": "{{date}}", "content": "{{content}}", "project": "{{project}}"},
                "params": ["date", "content", "project"],
            },
        ],
        "capabilities": [{
            "name": "submit_batch",
            "kind": "submit_batch",
            "step_ids": ["query", "submit"],
            "preconditions": [{"check": "confirm == true", "message": "提交前必须确认"}],
            "execution_contract": {
                "nodes": [
                    {"id": "call_query", "type": "call", "step_id": "query"},
                    {"id": "foreach_entries", "type": "foreach", "items": "input.entries", "steps": [
                        {"id": "call_submit_each", "type": "call", "step_id": "submit"},
                    ]},
                    {"id": "return_batch_result", "type": "return", "value": "batch_result"},
                ],
                "batch": {"enabled": True, "items_field": "entries"},
            },
            "output_mapping": [
                {"field": "success_dates", "source": "var.batch_result.results[].item.date"},
                {"field": "failed_dates", "source": "var.batch_result.failed_items[].item.date"},
                {"field": "failed_count", "source": "var.batch_result.failed_count"},
            ],
        }],
    }

    blocked = await execute_api(wf, {"__capability": "submit_batch", "month": "2026-05", "entries": []}, send=False)
    assert blocked["blocked"] is True
    assert "必须确认" in blocked["detail"]

    out = await execute_api(wf, {
        "__capability": "submit_batch",
        "confirm": True,
        "month": "2026-05",
        "project": "P1",
        "entries": [
            {"date": "2026-05-12", "content": "a"},
            {"date": "2026-05-13", "content": "b"},
        ],
    }, send=False)

    assert out["ok"] is True
    assert out["plan"] is True
    assert out["response"] == {
        "success_dates": ["2026-05-12", "2026-05-13"],
        "failed_dates": [],
        "failed_count": 0,
    }
    assert out["structured_output"] == out["response"]


async def test_structured_capability_plan_still_runs_grounded_fact_check(monkeypatch):
    import dano.execution.page.request_capture as capture

    workflow = {
        "steps": [{
            "step_id": "submit",
            "method": "POST",
            "url": "http://x/api/submit",
            "path": "/api/submit",
            "body_template": {"reason": "{{reason}}"},
            "params": ["reason"],
        }],
        "fact_check": {"endpoint": "/api/page", "param": "reason", "match_field": "reason"},
        "capabilities": [{
            "name": "submit",
            "kind": "submit",
            "step_ids": ["submit"],
            "nodes": [
                {"id": "call_submit", "type": "call", "step_id": "submit"},
                {"id": "return_submit", "type": "return", "from": "submit", "path": "response"},
            ],
        }],
    }

    async def fake_request(*args, **kwargs):
        return {"ok": True, "response": {"code": 0, "data": {"id": "R-1"}}}

    async def failed_recheck(*args, **kwargs):
        return False, "事实核查未找到新记录"

    monkeypatch.setattr(capture, "execute_api_request", fake_request)
    monkeypatch.setattr(capture, "_grounded_recheck", failed_recheck)

    out = await execute_api(
        workflow,
        {"__capability": "submit", "reason": "日报"},
        send=True,
        base_url="http://x",
    )

    assert out["ok"] is True
    assert "fact_check_passed" not in out


async def test_batch_capability_fact_checks_each_entry(monkeypatch):
    import dano.execution.page.request_capture as capture

    workflow = {
        "steps": [{"step_id": "submit", "method": "POST", "url": "http://x/api/submit"}],
        "fact_check": {"endpoint": "/api/page", "param": "date", "match_field": "date"},
        "capabilities": [{
            "name": "submit_batch",
            "kind": "submit_batch",
            "step_ids": ["submit"],
            "input_schema": {
                "type": "object",
                "properties": {"entries": {"type": "array", "items": {"type": "object"}}},
                "required": ["entries"],
            },
            "execution_contract": {"batch": {"enabled": True, "items_field": "entries"}},
        }],
    }
    checked_dates = []

    async def fake_workflow(*args, **kwargs):
        return {"ok": True, "response": {"code": 0}}

    async def successful_recheck_many(fc, field_sets, **kwargs):
        checked_dates.extend(fields.get("date") for fields in field_sets)
        return [(True, "") for _ in field_sets]

    monkeypatch.setattr(capture, "execute_api_workflow", fake_workflow)
    monkeypatch.setattr(capture, "_grounded_recheck_many", successful_recheck_many)

    out = await execute_api(workflow, {
        "__capability": "submit_batch",
        "entries": [{"date": "2026-05-12"}, {"date": "2026-05-13"}],
    }, send=True)

    assert out["ok"] is True
    assert "fact_check_passed" not in out
    assert checked_dates == []
    assert "fact_check_items" not in out


async def test_capability_map_applies_literal_and_compiled_values_to_request():
    workflow = {
        "steps": [{
            "step_id": "submit",
            "method": "POST",
            "url": "http://x/api/submit",
            "path": "/api/submit",
            "body_template": {
                "billType": "old",
                "processDefKey": "old",
                "activityId": "old",
                "processVariablesStr": "old",
            },
        }],
        "capabilities": [{
            "name": "submit",
            "kind": "submit",
            "step_ids": ["submit"],
            "nodes": [
                {"id": "map_billType", "type": "map", "source": "'oa_duty_leave'", "target": "submit.billType"},
                {"id": "map_processDefKey", "type": "map", "source": '"oa_duty_leave"', "target": "submit.processDefKey"},
                {"id": "map_activityId", "type": "map", "source": "literal:StartUserNode", "target": "submit.activityId"},
                {"id": "map_processVariablesStr", "type": "map", "source": 'computed:{"day":2}', "target": "submit.processVariablesStr"},
                {"id": "call_submit", "type": "call", "step_id": "submit"},
            ],
        }],
    }

    out = await execute_api(workflow, {"__capability": "submit"}, send=False)

    assert out["ok"] is True
    assert out["body"] == {
        "billType": "oa_duty_leave",
        "processDefKey": "oa_duty_leave",
        "activityId": "StartUserNode",
        "processVariablesStr": '{"day":2}',
    }


async def test_execute_capability_output_mapping_for_query_status():
    wf = {
        "steps": [
            {
                "step_id": "query",
                "method": "GET",
                "url": "http://x/api/status",
                "path": "/api/status",
                "response_json": {"code": 0, "data": {"filled": ["1", "2"], "missing": ["3"]}},
            },
            {
                "step_id": "submit",
                "method": "POST",
                "url": "http://x/api/submit",
                "path": "/api/submit",
                "body_template": {"date": "{{date}}"},
                "params": ["date"],
            },
        ],
        "capabilities": [
            {
                "name": "query_status",
                "kind": "query_status",
                "step_ids": ["query"],
                "output_mapping": [
                    {"field": "filled_dates", "step_id": "query", "response_path": "data.filled"},
                    {"field": "missing_dates", "step_id": "query", "response_path": "data.missing"},
                ],
            },
            {"name": "submit", "kind": "submit", "step_ids": ["query", "submit"]},
        ],
    }

    out = await execute_api(wf, {"__capability": "query_status"}, send=False)

    assert out["ok"] is True
    assert out["steps"] == 1
    assert out["response"] == {"filled_dates": ["1", "2"], "missing_dates": ["3"]}
    assert out["structured_output"] == out["response"]
    assert out["final"]["url"].endswith("/api/status")


async def test_capability_output_mapping_uses_current_live_step_response(monkeypatch):
    import dano.execution.page.request_capture as rc

    async def fake_execute(step, fields, **kwargs):
        return {
            "ok": True,
            "status": 200,
            "response": {"data": {"value": "LIVE"}},
            "url": step["url"],
        }

    monkeypatch.setattr(rc, "execute_api_request", fake_execute)
    workflow = {
        "steps": [{
            "step_id": "query",
            "method": "GET",
            "url": "https://example.invalid/api/query",
            "response_json": {"data": {"value": "RECORDED"}},
        }],
        "capabilities": [{
            "name": "query_status",
            "kind": "query_status",
            "step_ids": ["query"],
            "output_mapping": [{
                "field": "value",
                "step_id": "query",
                "response_path": "data.value",
            }],
        }],
    }

    out = await rc.execute_api(workflow, {"__capability": "query_status"}, send=True)

    assert out["response"] == {"value": "LIVE"}
    assert "_responses_by_step" not in out


async def test_execute_api_accepts_capability_alias_fields():
    wf = {
        "steps": [
            {"step_id": "query", "method": "GET", "url": "http://x/api/status", "path": "/api/status"},
            {"step_id": "submit", "method": "POST", "url": "http://x/api/submit", "path": "/api/submit"},
        ],
        "capabilities": [{"name": "query_status", "kind": "query_status", "step_ids": ["query"]}],
    }

    for key in ("__capability", "_capability", "capability"):
        out = await execute_api(wf, {key: "query_status"}, send=False)
        assert out["ok"] is True
        assert out["steps"] == 1
        assert out["final"]["url"].endswith("/api/status")


async def test_execute_capability_plan_condition_can_skip_call():
    wf = {
        "steps": [{
            "step_id": "status",
            "method": "GET",
            "url": "http://x/api/status",
            "path": "/api/status",
            "response_json": {"code": 0, "data": {"status": "ready"}},
        }],
        "capabilities": [{
            "name": "query_status",
            "kind": "query_status",
            "step_ids": ["status"],
            "execution_contract": {
                "nodes": [
                    {"id": "maybe_query", "type": "condition", "condition": "input.should_query == true", "then": [
                        {"id": "call_status", "type": "call", "step_id": "status"},
                    ], "otherwise": [
                        {"id": "return_skip", "type": "return", "value": "'skipped'"},
                    ]},
                ],
            },
        }],
    }

    skipped = await execute_api(wf, {"__capability": "query_status", "should_query": False}, send=False)
    assert skipped["ok"] is True
    assert skipped["response"] == "skipped"
    assert "url" not in (skipped.get("final") or {})

    queried = await execute_api(wf, {"__capability": "query_status", "should_query": True}, send=False)
    assert queried["ok"] is True
    assert queried["final"]["url"].endswith("/api/status")


async def test_execute_get_query_template_dry_run():
    """GET 前置接口无 body 也可发布:query_template 负责把参数和页面常量构造成 URL。"""
    apir = {
        "method": "GET",
        "url": "http://x/api/getappid?fixed=1",
        "query_template": {"q": "{{q}}", "appId": "auto"},
        "params": ["q"],
        "sample_inputs": {"q": "销售分析"},
    }

    assert self_check(apir) == []
    out = await execute_api_request(apir, {}, send=False)

    assert out["ok"] is True
    assert out["body"] is None
    assert out["query"] == {"q": "销售分析", "appId": "auto"}
    assert "fixed=1" in out["url"]
    assert "q=%E9%94%80%E5%94%AE%E5%88%86%E6%9E%90" in out["url"]
    assert "appId=auto" in out["url"]


def test_self_check_flags_query_param_without_template():
    """声明了参数但既没有 body_template 也没有 query_template 时必须拦住。"""
    apir = {"method": "GET", "url": "http://x/api/getappid", "params": ["q"]}
    assert any("query_template" in p for p in self_check(apir))


def test_pick_submit_skips_noise_and_picks_by_value_match():
    req = pick_submit_request(_REQUESTS, _SAMPLES)
    assert req["url"].endswith("/oa/leave/start")          # 含最多用户填的值的写请求,跳过 login/captcha






def test_substitute_falls_back_to_recorded_default():
    """全选安全网:agent 没传的字段 → 用录制原值(defaults),不留空占位、固定字段不变。"""
    tmpl = {"reason": "{{原因}}", "billType": "{{billType}}", "leaveType": "{{请假类型}}"}
    defaults = {"原因": "录制原因", "billType": "oa_duty_leave", "请假类型": "事假"}
    body = substitute(tmpl, {"原因": "感冒"}, defaults)        # 只传了原因
    assert body["reason"] == "感冒"                            # 传了 → 用新值
    assert body["billType"] == "oa_duty_leave"                # 没传 → 用录制原值(固定字段不变)
    assert body["leaveType"] == "事假"                         # 没传 → 录制原值




def test_real_leave_body_fixed_fields_preserved_generally():
    """用户真实请假 body:billType/processDefKey=oa_duty_leave 是实际提交值,两条路径都通用保留;
    审批人嵌套数组([144]/[118])也原样。证明"非参数字段一律原样提交"不是 billType 特例。"""
    raw = ('{"type":2,"reason":"123123123","startTime":1782144000000,"endTime":1782748800000,'
           '"billType":"oa_duty_leave","processDefKey":"oa_duty_leave",'
           '"startUserSelectAssignees":{"Activity_09dlq0g":[144],"Activity_0ag2wyz":[118]}}')
    req = {"method": "POST", "url": "http://oa.x/oa/duty-leave/submit-process", "post_data": raw}

    # 路径A:billType/processDefKey 不作参数(固定字段)→ body_template 里就是常量,原样提交
    a = build_api_request(req, {"reason": "原因"})
    assert a["body_template"]["billType"] == "oa_duty_leave"
    assert a["body_template"]["processDefKey"] == "oa_duty_leave"
    assert a["body_template"]["startUserSelectAssignees"]["Activity_09dlq0g"] == [144]   # 审批人嵌套数组原样
    assert a["body_template"]["startUserSelectAssignees"]["Activity_0ag2wyz"] == [118]
    body_a = substitute(a["body_template"], {"原因": "换个理由"})
    assert body_a["billType"] == "oa_duty_leave" and body_a["reason"] == "换个理由"

    # 路径B:全选(billType/processDefKey 也作参数)→ agent 不传时用录制原值(sample_inputs)
    b = build_api_request(req, {"reason": "原因", "billType": "billType", "processDefKey": "processDefKey"})
    assert b["sample_inputs"]["billType"] == "oa_duty_leave"
    body_b = substitute(b["body_template"], {"原因": "换个理由"}, b["sample_inputs"])
    assert body_b["billType"] == "oa_duty_leave"        # 没传 → 录制原值,不变
    assert body_b["processDefKey"] == "oa_duty_leave"


# ── 新流程:拍平请求体 → 用户按字段勾选(任意 OA / 业务 / 字段都通用,不靠值匹配)──
# 嵌套请求体(很多 OA 把表单包在 form/variables 里):证明深层字段也能拍平+勾选
_NESTED = ('{"form":{"leaveType":"事假","days":3,"reason":"回家","attachments":[]},'
           '"variables":{"procInstId":98765432109876,"tenantId":"000000"},"draft":false}')


def test_flatten_body_lists_all_leaves_with_suggestions():
    fields = flatten_body(_NESTED, {"原因": "回家"})
    paths = {f["path"]: f for f in fields}
    assert set(paths) == {"form.leaveType", "form.days", "form.reason",
                          "variables.procInstId", "variables.tenantId", "draft"}
    assert paths["form.reason"]["suggest_param"] is True          # 对上用户填的值 → 建议参数
    assert paths["form.reason"]["suggest_name"] == "原因"          # 参数名=字段中文名(DOM 标签)
    assert paths["form.leaveType"]["suggest_param"] is True        # 像用户数据(非 ID/常量)
    assert paths["variables.procInstId"]["suggest_param"] is False  # 雪花 id → 默认不勾
    assert paths["variables.tenantId"]["suggest_param"] is False    # key 以 id 结尾 → 默认不勾
    assert paths["draft"]["suggest_param"] is False                # 布尔常量 → 不勾


def test_flatten_date_field_gets_chinese_label_across_formats():
    """日期跨格式:请求体毫秒戳 ↔ 表单显示 2026-06-24 → 参数名拿到中文「开始时间」(不止文本字段)。"""
    body = '{"startTime":1782230400000,"reason":"回家","type":2}'
    samples = {"开始时间": "2026-06-24 00:00:00", "原因": "回家"}
    p = {f["key"]: f["suggest_name"] for f in flatten_body(body, samples)}
    assert p["reason"] == "原因"
    assert p["startTime"] == "开始时间"     # 毫秒戳对上显示日期 → 中文
    assert p["type"] == "type"             # 下拉代码(2)对不上「事假」→ 退原始 key(诚实)


def test_flatten_dropdown_text_value_matches_label():
    """下拉提交的是文字(type=周末)→ 按值对上标签「加班类型」;不靠瞎猜。"""
    body = '{"type":"周末","reason":"回家"}'
    samples = {"加班类型": "周末", "原因": "回家"}      # 录制时选了下拉、填了原因
    p = {f["key"]: f["suggest_name"] for f in flatten_body(body, samples)}
    assert p["type"] == "加班类型"          # 文字直接对上
    assert p["reason"] == "原因"


def test_flatten_no_blind_guess_for_unmatched():
    """对不上的字段(下拉代码 / 没录的字段)退回原始 key,绝不瞎塞剩余标签(避免张冠李戴)。"""
    body = '{"type":2,"reason":"回家"}'
    samples = {"原因": "回家", "加班类型": "周末"}     # 加班类型 的值是「周末」,但 body 里 type=2(代码)
    p = {f["key"]: f["suggest_name"] for f in flatten_body(body, samples)}
    assert p["reason"] == "原因"
    assert p["type"] == "type"             # 2≠周末 → 退 key(不会被错塞成「加班类型」)


def test_flatten_same_value_fields_take_distinct_labels():
    """两个字段都填 123123123(reason/remark)→ 按录制顺序各取一个标签,不抢同一个。"""
    body = '{"reason":"123123123","remark":"123123123"}'
    samples = {"加班原因": "123123123", "备注": "123123123"}   # 录制顺序:先加班原因、后备注
    p = {f["key"]: f["suggest_name"] for f in flatten_body(body, samples)}
    assert p["reason"] == "加班原因"        # 第一个同值字段取第一个标签
    assert p["remark"] == "备注"            # 第二个取下一个(不再都变「备注」)


def test_flatten_infers_field_types():
    """字段类型从值推断(通用):文本/数字/毫秒时间戳→datetime/布尔/数组。"""
    body = '{"reason":"回家","amount":12.5,"days":3,"startTime":1782230400000,"draft":false,"checkin":"2026-06-24"}'
    fields = {f["key"]: f for f in flatten_body(body)}
    t = {key: field["type"] for key, field in fields.items()}
    assert t["reason"] == "string"
    assert t["amount"] == "number" and t["days"] == "number"
    assert t["startTime"] == "datetime"        # 13 位毫秒 + 时间类 key
    assert t["checkin"] == "date"              # YYYY-MM-DD 字符串
    assert t["draft"] == "boolean"
    assert fields["startTime"]["wire_type"] == "number"
    assert fields["checkin"]["wire_type"] == "string"


def test_build_api_request_field_types_with_enum():
    """build_api_request 产出 field_types;select(选领导/代码下拉)→ enum。"""
    req = {"method": "POST", "url": "http://x/s",
           "post_data": '{"reason":"回家","days":3,"amount":100,"approverId":12}'}
    apir = build_api_request(req, {"reason": "reason", "days": "days", "amount": "amount", "approverId": "approver"},
                             selects=[{"path": "approverId", "source_url": "/u",
                                       "value_key": "userId", "label_key": "nickName"}])
    assert apir["field_types"]["reason"] == "string"
    assert apir["field_types"]["days"] == "number" and apir["field_types"]["amount"] == "number"
    assert apir["field_types"]["approver"] == "enum"     # select → 枚举(传名字/文字)


def test_flatten_required_from_form_star():
    """表单 * 必填:录制时标了必填的字段(其标签在 required_labels)→ field.required=True。"""
    body = '{"reason":"回家","street":"中山路","type":"周末"}'
    samples = {"原因": "回家", "所在街道": "中山路", "加班类型": "周末"}
    req_labels = {"原因", "加班类型"}      # 原因/加班类型 有 *,所在街道没有
    fields = {f["key"]: f for f in flatten_body(body, samples, req_labels)}
    assert fields["reason"]["required"] is True and fields["reason"]["suggest_name"] == "原因"
    assert fields["type"]["required"] is True
    assert fields["street"]["required"] is False   # 没 * → 非必填


def test_flatten_required_range_marker_applies_to_both_indexed_values():
    body = '{"useTime":["2026-07-09 00:00:00","2026-08-11 23:59:59"],"remark":"说明"}'
    samples = {
        "使用时间": "2026-07-09 00:00:00",
        "使用时间#2": "2026-08-11 23:59:59",
        "备注": "说明",
    }
    fields = {field["path"]: field for field in flatten_body(body, samples, {"使用时间"})}

    assert fields["useTime[0]"]["required"] is True
    assert fields["useTime[1]"]["required"] is True
    assert fields["remark"]["required"] is False


def test_classify_request_role_aggregates_multistep_write_instead_of_defaulting_to_get():
    role = classify_request_role({
        "method": None,
        "steps": [
            {"method": "GET", "path": "/api/process-definition/get"},
            {"method": "POST", "path": "/api/seal-apply/submit-process", "post_data": '{"title":"x"}'},
        ],
    })

    assert role == {"semanticRole": "workflow_submit", "sideEffect": "write", "risk_level": "L3"}


def test_flatten_required_defaults_all_when_no_star():
    """表单没抓到任何 * 必填标记(required_labels 空)→ 参数字段**默认全部必填**(写操作宁多勿漏,免手动勾选)。"""
    body = '{"reason":"回家","street":"中山路","type":"周末"}'
    samples = {"原因": "回家", "所在街道": "中山路", "加班类型": "周末"}
    fields = {f["key"]: f for f in flatten_body(body, samples)}     # 不传 required_labels
    assert fields["reason"]["required"] is True
    assert fields["street"]["required"] is True
    assert fields["type"]["required"] is True


def test_flatten_required_unconfident_defaults_required():
    """表单区分了必填(有 * ),但某字段值有歧义(同值多字段)映射不确信 → 不敢判可选,默认必填。"""
    body = '{"a":"1","b":"1"}'                 # 两字段同值 1 → 映射不确信
    samples = {"甲": "1", "乙": "1"}
    req_labels = {"甲"}                          # 表单确实区分了必填(甲有 *)
    fields = {f["key"]: f for f in flatten_body(body, samples, req_labels)}
    assert fields["a"]["required"] is True and fields["b"]["required"] is True


def test_flatten_required_nonparam_is_optional():
    """常量/内部 id 不是用户要填的项 → required=False(它本就原样提交,不进必填清单)。"""
    body = '{"reason":"回家","procDefKey":"oa_duty_leave"}'
    fields = {f["key"]: f for f in flatten_body(body, {"原因": "回家"})}
    assert fields["reason"]["required"] is True
    assert fields["procDefKey"]["suggest_param"] is False and fields["procDefKey"]["required"] is False


def test_flatten_body_non_json_returns_empty():
    assert flatten_body("plain text no kv") == []     # 非 JSON 非表单 → 空(form 体现已支持,另有专测)
    assert flatten_body(None) == []


def test_flatten_suggestions_match_real_oa_fields():
    """还原用户真"点狮"OA 请假提交体:slug 标识默认不勾,毫秒时间戳日期要勾。"""
    body = ('{"type":2,"reason":"回家","startTime":1782230400000,"endTime":1782403200000,'
            '"billType":"oa_duty_leave","processDefKey":"oa_duty_leave"}')
    p = {f["key"]: f["suggest_param"] for f in flatten_body(body)}
    assert p["startTime"] is True and p["endTime"] is True   # 13 位毫秒时间戳 = 日期 → 该当参数
    assert p["reason"] is True and p["type"] is True          # 请假原因 / 类型 → 参数
    assert p["billType"] is False                             # snake_case 标识(表单类型)→ 不勾
    assert p["processDefKey"] is False                        # key 以 Key 结尾(流程定义键)→ 不勾


def test_flatten_drops_system_timestamps_not_user_input():
    """治日报 bug:submitTime/createTime 是系统提交时写入的时间戳(用户没填、对不上任何录制样例)
    → 当常量不参数化(否则 agent 会被要求"提供创建时间");用户真选的日期(对上样例)照常当参数。"""
    body = ('{"reportDate":"2026-06-25","todayWorkContent":"1",'
            '"submitTime":1782380760000,"createTime":1782380760000}')
    samples = {"日报日期": "2026-06-25", "今日工作内容": "1"}
    p = {f["key"]: f["suggest_param"] for f in flatten_body(body, samples)}
    assert p["reportDate"] is True            # 用户选的日期(对上样例)→ 参数
    assert p["todayWorkContent"] is True      # 用户填的 → 参数
    assert p["submitTime"] is False           # 系统时间戳,对不上样例 → 不参数化
    assert p["createTime"] is False


def test_user_date_timestamp_never_system_overwritten():
    """治"开始时间/结束时间被标系统值":用户挑的日期字段(startTime/endTime,即便对不上样例)
    **绝不**当系统时间戳被 now 覆盖;只有 create/submit/update 这类系统 key 才填 now。"""
    body = ('{"startTime":1782316800000,"endTime":1782403200000,'
            '"createTime":1782380760000,"submitTime":1782380760000}')
    # 没有任何样例(模拟日期 pick 没被录到)→ startTime/endTime 仍不能被当系统值
    f = {x["key"]: x for x in flatten_body(body, {})}
    assert f["startTime"]["system_value"] is False and f["endTime"]["system_value"] is False
    assert f["createTime"]["system_value"] is True and f["submitTime"]["system_value"] is True
    # build:只有 create/submit 进 system_values(运行期 now);startTime/endTime 不进(用户日期不被覆盖)
    apir = build_api_request({"method": "POST", "url": "http://oa/x", "post_data": body}, {})
    sysp = {s["path"] for s in apir["system_values"]}
    assert sysp == {"createTime", "submitTime"}


def test_flatten_keeps_user_picked_timestamp_dates():
    """对照:用户**真选**的日期即便存成毫秒时间戳(startTime/endTime 对上录制样例)→ 仍是参数(不误杀)。"""
    body = '{"startTime":1782230400000,"endTime":1782403200000}'
    samples = {"开始日期": "2026-06-24", "结束日期": "2026-06-26"}   # 用户选的日期 ↔ 时间戳跨格式对上
    p = {f["key"]: f["suggest_param"] for f in flatten_body(body, samples)}
    assert p["startTime"] is True and p["endTime"] is True


def test_suggest_identity_skips_user_typed_value():
    """治日报2 bug:用户填的值(二级内设机构=2/职能描述=3)恰好撞会话标量(roleLevel=2/orgType=3)→
    不得冻结成 identity(否则运行期被会话值覆盖、且当不了参数、参数名也改不了)。"""
    submit = '{"ercsmc":"2","qzms":"3","applicantId":"118"}'
    storage = {"cookies": [{"name": "roleLevel", "value": "2"}, {"name": "orgType", "value": "3"},
                           {"name": "uid", "value": "118"}], "origins": []}
    samples = {"二级内设机构": "2", "职能描述": "3"}     # 用户亲手填的
    ids = {i["path"] for i in suggest_identity(submit, storage, samples)}
    assert "ercsmc" not in ids and "qzms" not in ids   # 用户填的 → 参数,不是会话身份
    assert "applicantId" in ids                         # 用户没填、=会话 uid → 仍是 identity


def test_build_api_request_param_wins_over_identity():
    """同一字段既被参数化又被判 identity → 参数优先,identity 丢弃(避免运行期覆盖 + 自检冲突)。"""
    req = {"method": "POST", "url": "http://oa/x", "post_data": '{"ercsmc":"2"}'}
    apir = build_api_request(req, {"ercsmc": "二级内设机构"},
                            identity=[{"path": "ercsmc", "source": "cookie:roleLevel"}])
    assert apir["params"] == ["二级内设机构"]
    assert apir["identity"] == []                        # 已参数化 → 不再当 identity


def test_looks_like_read_request_general():
    """POST 形态的读/查询(getXxxList/queryXxx/getKbListByXxxtId)识别为读,不当业务写。"""
    from dano.execution.page.request_capture import looks_like_read_request
    assert looks_like_read_request("http://oa/appgateway/dcensus/v1.0/qzqdsl/getQzqdSlList")
    assert looks_like_read_request("http://oa/appgateway/xzdz/v1.0/nrgl/queryNrxxListForKfmh")
    assert looks_like_read_request("http://oa/api/getKbListByXxxtId?t=1&xxxtId=02021")
    assert not looks_like_read_request("http://oa/appgateway/dcensus/v1.0/qzqdsl/createQzqdSl")
    assert not looks_like_read_request("http://oa/admin-api/oa/daily-report/submit-process")


def test_json_write_requests_excludes_post_reads():
    """候选提交请求里排除 POST 形态的读(getXxxList 等)→ 只剩真正的写(createQzqdSl)。"""
    reqs = [
        {"method": "POST", "url": "http://oa/x/getQzqdSlList", "post_data": '{"page":1}'},
        {"method": "POST", "url": "http://oa/x/queryNrxxListForKfmh", "post_data": '{"k":1}'},
        {"method": "POST", "url": "http://oa/x/createQzqdSl", "post_data": '{"csmc":"1"}'},
    ]
    urls = [c["url"] for c in json_write_requests(reqs)]
    assert urls == ["http://oa/x/createQzqdSl"]


async def test_execute_dry_ok_when_param_lacks_default():
    """治日报3 bug:参数声明正确但**没有录制默认值**(运行期由 agent 提供)→ dry 不该判失败。
    self_check 是唯一承重闸门:它已证明参数结构正确;残留 {{}} 仅因缺默认值,不拦发布。"""
    from dano.execution.page.request_capture import execute_api_request
    # 手工造一个参数声明正确、但 sample_inputs 缺该参数默认值的 api_request
    apir = {"method": "POST", "url": "http://oa/x", "content_type": "application/json",
            "body_template": {"csmc": "{{处室名称}}"}, "params": ["处室名称"], "sample_inputs": {}}
    res = await execute_api_request(apir, {}, send=False)
    assert res["ok"] is True and res["self_check"] == []   # 结构正确 → 通过(不再误报"参数没全填上")
    assert res["leftover_no_default"] is True               # 信息:该参数无默认值(运行期填)


def test_flatten_system_field_does_not_steal_user_value():
    """治日报 bug:processStatus=4 与用户填的 备注=4 同值;系统字段(status 结尾)不得抢走真字段的样例标签
    → processStatus 不作参数(固定值),备注 才拿到"备注"名并作参数。两遍配样例:真字段先认领。"""
    body = '{"processStatus":4,"remark":"4"}'         # processStatus 在前(易抢);remark 是用户填的
    samples = {"备注": "4"}
    f = {x["key"]: x for x in flatten_body(body, samples)}
    assert f["processStatus"]["suggest_param"] is False   # 系统状态码 → 不参数化
    assert f["remark"]["suggest_param"] is True and f["remark"]["suggest_name"] == "备注"


def test_control_identity_maps_repeated_hotel_values_without_guessing_order():
    body = json.dumps({
        "applyTitle": "1",
        "totalAmt": 1,
        "roomType": 1,
        "useTime": 1784044800000,
        "remark": "1",
    })
    samples = {
        "申请标题": "1",
        "预计金额": "1",
        "房间类型": "大床房",
        "入住时间": "2026-07-14 00:00:00",
        "备注": "1",
    }
    evidence = [
        {"label": "申请标题", "value": "1", "field_aliases": ["applyTitle"], "control_kind": "text"},
        {"label": "预计金额", "value": "1", "field_aliases": ["totalAmt"], "control_kind": "number"},
        {"label": "房间类型", "value": "大床房", "field_aliases": ["roomType"], "control_kind": "select"},
        {"label": "入住时间", "value": "2026-07-14 00:00:00", "field_aliases": ["useTime"], "control_kind": "datetime"},
        {"label": "备注", "value": "1", "field_aliases": ["remark"], "control_kind": "textarea"},
    ]

    fields = {
        item["path"]: item
        for item in flatten_body(body, samples, field_evidence=evidence)
    }

    assert (fields["applyTitle"]["suggest_name"], fields["applyTitle"]["type"]) == ("申请标题", "string")
    assert (fields["totalAmt"]["suggest_name"], fields["totalAmt"]["type"]) == ("预计金额", "number")
    assert (fields["roomType"]["suggest_name"], fields["roomType"]["type"]) == ("房间类型", "number")
    assert (fields["useTime"]["suggest_name"], fields["useTime"]["type"]) == ("入住时间", "datetime")
    assert (fields["remark"]["suggest_name"], fields["remark"]["type"]) == ("备注", "string")


def test_strict_page_enum_uses_dom_alias_not_repeated_selected_value():
    body = '{"applyTitle":"1","useTime":"1","processStatus":1}'
    fields = flatten_body(body, {
        "申请标题": "1", "入住时间": "1", "流程状态": "审批中",
    }, field_evidence=[
        {"label": "申请标题", "field_aliases": ["applyTitle"], "control_kind": "text"},
        {"label": "入住时间", "field_aliases": ["useTime"], "control_kind": "datetime"},
        {"label": "流程状态", "field_aliases": ["processStatus"], "control_kind": "select"},
    ])
    out = page_enum_selects(
        body,
        {"流程状态": {
            "field_key": "流程状态",
            "field_aliases": ["processStatus"],
            "control_kind": "select",
            "enum_source": "dom",
            "mapping_complete": True,
            "selected_label": "审批中",
            "selected_value": 1,
            "options": [
                {"label": "未提交", "value": 0},
                {"label": "审批中", "value": 1},
            ],
        }},
        fields=fields,
    )

    assert [item["path"] for item in out] == ["processStatus"]
    assert out[0]["option_map"] == {"未提交": 0, "审批中": 1}


def test_strict_api_option_requires_this_controls_selected_label():
    body = '{"applyTitle":"1","roomType":2}'
    fields = flatten_body(body, {"申请标题": "1", "房间类型": "大床房"}, field_evidence=[
        {"label": "申请标题", "field_aliases": ["applyTitle"], "control_kind": "text"},
        {"label": "房间类型", "field_aliases": ["roomType"], "control_kind": "select"},
    ])
    reads = [{"url": "/dict", "json": {"data": [
        {"value": "1", "label": "无关候选"},
        {"value": 2, "label": "大床房"},
        {"value": 3, "label": "双床房"},
    ]}}]

    selects = suggest_selects(body, reads, {"申请标题": "1", "房间类型": "大床房"}, fields=fields)

    assert [item["path"] for item in selects] == ["roomType"]


def test_flatten_marks_system_timestamp_value():
    """系统时间戳标 system_value=True(前端展示"系统值·运行期自动填"),且不作参数。"""
    body = '{"reportDate":"2026-06-25","submitTime":1782380760000}'
    samples = {"日报日期": "2026-06-25"}
    f = {x["key"]: x for x in flatten_body(body, samples)}
    assert f["submitTime"]["system_value"] is True and f["submitTime"]["suggest_param"] is False
    assert f["reportDate"]["system_value"] is False and f["reportDate"]["suggest_param"] is True


def test_build_api_request_collects_system_timestamps():
    """build:系统时间戳(用户没勾)落 system_values(运行期填 now),不进 params、不焊死会话值。"""
    req = {"method": "POST", "url": "http://oa.x/api/daily/submit",
           "post_data": '{"reportDate":"2026-06-25","submitTime":1782380760000,"createTime":1782380760000}'}
    apir = build_api_request(req, {"reportDate": "日报日期"})
    sysv = {s["path"]: s["kind"] for s in apir["system_values"]}
    assert sysv == {"submitTime": "now_ms", "createTime": "now_ms"}
    assert apir["params"] == ["日报日期"]                # 时间戳不作参数


def test_collect_findings_skips_system_timestamps():
    """检出器:system_values 里的时间戳不报"焊死会话值"(运行期填 now)→ 不白拦发布;别的会话值仍报。"""
    from dano.execution.page.repair_ops import collect_repair_findings
    req = {"method": "POST", "url": "http://oa.x/api/daily/submit",
           "post_data": '{"reportDate":"2026-06-25","submitTime":1782380760000}'}
    apir = build_api_request(req, {"reportDate": "日报日期"})
    kinds = [f["kind"] for f in collect_repair_findings(apir)]
    assert "session_constant" not in kinds              # submitTime 已 system_values → 不报


async def test_execute_fills_system_timestamp_with_now():
    """运行期:dry 校验通过(self_check 不因时间戳挂);真发时 body 里时间戳被填成当前毫秒(非录制旧值)。"""
    import time as _t
    from dano.execution.page.request_capture import execute_api_request
    req = {"method": "POST", "url": "http://oa.x/api/daily/submit",
           "post_data": '{"reportDate":"2026-06-25","submitTime":1782380760000}'}
    apir = build_api_request(req, {"reportDate": "日报日期"})
    res = await execute_api_request(apir, {"日报日期": "2026-06-30"}, send=False)
    assert res["ok"] is True                            # 结构自检通过(时间戳不再拦)
    assert res["body"]["submitTime"] >= int(_t.time() * 1000) - 5000   # 填成"现在",不是 1782380760000


def test_suggest_selects_skips_user_typed_value_colliding_code():
    """治日报 bug:用户把"1"打进文本域(明日工作计划/备注),恰好撞上某状态小字典 value=1 →
    不能误判成"名字→ID 枚举"。用户亲手填的值即自由文本;真下拉录到的样例会是显示名(与提交码不同)。"""
    submit = '{"tomorrowWorkPlan":"1","remark":"1"}'
    samples = {"明日工作计划": "1", "备注": "1"}        # 用户亲手 fill 了 1
    status = [{"url": "http://oa.x/sys/status",
               "json": {"data": [{"label": "草稿", "value": "1"}, {"label": "已提交", "value": "2"}]}}]
    assert suggest_selects(submit, status, samples) == []    # sv 正是录制样例 → 不当下拉


def test_build_api_request_from_user_chosen_paths():
    req = {"method": "POST", "url": "http://oa.x/prod-api/oa/leave/start", "post_data": _NESTED}
    # 用户勾了 3 个深层字段并起名(内部 id 不勾)
    param_map = {"form.leaveType": "leave_type", "form.days": "days", "form.reason": "reason"}
    apir = build_api_request(req, param_map, base_url="http://oa.x/prod-api")
    assert apir["path"] == "/oa/leave/start"
    assert set(apir["params"]) == {"leave_type", "days", "reason"}
    assert apir["body_template"]["form"]["leaveType"] == "{{leave_type}}"
    assert apir["body_template"]["form"]["days"] == "{{days}}"
    assert apir["body_template"]["variables"]["procInstId"] == 98765432109876  # 没勾 → 原样常量
    assert apir["body_template"]["draft"] is False
    assert apir["sample_inputs"] == {"leave_type": "事假", "days": "3", "reason": "回家"}


def test_extract_auth_headers_keeps_app_specific_drops_browser():
    """泛化鉴权:留下任意系统的自定义鉴权/租户头,丢掉浏览器通用头 —— 不写死某个 token key。"""
    raw = {"authorization": "Bearer eyJ...", "satoken": "abc123", "clientid": "web",
           "tenant-id": "000000", "content-type": "application/json", "cookie": "JSESSIONID=x",
           "user-agent": "Mozilla", "sec-fetch-mode": "cors", "accept-encoding": "gzip"}
    out = extract_auth_headers(raw)
    assert out == {"authorization": "Bearer eyJ...", "satoken": "abc123",
                   "clientid": "web", "tenant-id": "000000"}   # 只留应用自定义头


def test_build_api_request_carries_captured_auth_headers():
    """换一套非若依鉴权(satoken,无 Admin-Token):录到的头被带进 api_request,回放原样发。"""
    req = {"method": "POST", "url": "http://oa2.x/api/leave/submit", "post_data": _NESTED,
           "headers": {"satoken": "tok-xyz", "tenant-id": "42", "user-agent": "X", "cookie": "a=b"}}
    apir = build_api_request(req, {"form.reason": "reason"})
    assert apir["auth_headers"] == {"satoken": "tok-xyz", "tenant-id": "42"}   # 自动适配,无需配置


def test_build_api_request_then_substitute_runtime_values():
    req = {"method": "POST", "url": "http://oa.x/prod-api/oa/leave/start", "post_data": _NESTED}
    apir = build_api_request(req, {"form.reason": "reason", "form.days": "days"})
    body = substitute(apir["body_template"], {"reason": "出差", "days": "5"})
    assert body["form"]["reason"] == "出差" and body["form"]["days"] == "5"
    assert body["variables"]["tenantId"] == "000000"   # 未勾字段运行期仍是原常量


# ── 真浏览器 + 真 POST:验证录制时真能抓到提交请求并参数化 ──
import http.server  # noqa: E402
import socketserver  # noqa: E402
import threading  # noqa: E402

import pytest  # noqa: E402


class _FakeVerdict:
    def __init__(self, role: str) -> None:
        self.role, self.model_id, self.passed, self.reasons = role, f"fake-{role}", True, []


class _FakeBoard:
    """三模型评审 fake:三角色全通过(测写页面评审闸门,不烧 LLM)。"""

    async def review(self, *, asset_type, asset_key, body, evidence):  # noqa: ANN001
        return [_FakeVerdict(r) for r in ("acceptance", "security", "compliance")]


_HTML = (b'<!doctype html><html><head><meta charset="utf-8"></head><body>'
         b'<input id="reason">'
         b'<button id="submit" type="button" onclick="fetch(\'/prod-api/oa/leave/start\','
         b'{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},'
         b'body:JSON.stringify({reason:document.getElementById(\'reason\').value,procDefId:\'P1\'})})">'
         b'\xe6\x8f\x90\xe4\xba\xa4</button>'
         b'<script>fetch(\'/prod-api/system/user/list\')</script>'   # 页面加载时拉"选领导"候选(GET)
         b'</body></html>')


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN001 —— 静默
        pass

    def do_GET(self):
        if "/system/user/list" in self.path:                        # "选领导"候选源(JSON 列表)
            import json as _j
            body = _j.dumps({"rows": [{"userId": 12, "nickName": "张经理"},
                                      {"userId": 34, "nickName": "李总"}]}).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(body); return
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(_HTML)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(n)
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b'{"code":200,"echo":' + (raw or b'{}') + b'}')   # 回显收到的 body






async def test_recorder_captures_required_star_elementui():
    """真浏览器:Element-UI 结构(el-form-item.is-required + label[for])→ 录制捕获 * 必填 + 中文标签。"""
    pytest.importorskip("playwright")
    from dano.execution.page.recorder import RecordSession
    from playwright.async_api import async_playwright
    pw = None
    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
    except Exception:  # noqa: BLE001
        pytest.skip("chromium 未安装")
    finally:
        if browser is not None:
            await browser.close()
        if pw is not None:
            await pw.stop()
    html = ('<!doctype html><html><head><meta charset="utf-8"></head><body><form>'
            '<div class="el-form-item is-required"><label for="dest">目的地</label><input id="dest"></div>'
            '<div class="el-form-item"><label for="remark">备注</label><input id="remark"></div>'
            '<div class="el-form-item is-required"><label for="untouched">未操作字段</label>'
            '<div class="el-form-item__content"><input id="untouched"></div></div>'
            '</form></body></html>').encode("utf-8")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: ANN001
            pass

        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            self.wfile.write(html)

    httpd = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        sess = RecordSession()
        await sess.start(f"http://127.0.0.1:{port}/")
        await sess.page.fill("#dest", "北京")
        await sess.page.fill("#remark", "无")
        await sess.page.wait_for_timeout(300)
        req_labels = sess.recorded_required_labels()
        observed_labels = await sess.observed_required_labels()
        await sess.stop()
    finally:
        httpd.shutdown()
    assert "目的地" in req_labels        # is-required → 必填
    assert "备注" not in req_labels      # 无 is-required → 非必填
    assert "未操作字段" in observed_labels  # 页面级扫描不能依赖字段已被操作




# ─────────── P0:发布前确定性自检 self_check + 运行期换身后置审计 ───────────
def test_self_check_clean_request_passes():
    """良构请求:参数有占位、identity 路径可达且来源合法 → 无违规。"""
    apir = {"body_template": {"reason": "{{reason}}", "applicantId": 118},
            "params": ["reason"],
            "identity": [{"path": "applicantId", "source": "localStorage:userInfo.userId"}]}
    assert self_check(apir) == []


def test_self_check_flags_unreachable_identity_path():
    """identity 路径在 body 里不存在 → 命中(运行期换身会冻结成录制者)。"""
    apir = {"body_template": {"reason": "{{reason}}"}, "params": ["reason"],
            "identity": [{"path": "applicantId", "source": "localStorage:userInfo.userId"}]}
    assert any("找不到落点" in p for p in self_check(apir))


def test_self_check_flags_bad_identity_source():
    """identity 路径可达但取值来源非法 → 命中。"""
    apir = {"body_template": {"applicantId": 118}, "params": [],
            "identity": [{"path": "applicantId", "source": "屏幕上看到的"}]}
    assert any("取值来源" in p for p in self_check(apir))


def test_self_check_blob_nested_identity_reachable_passes():
    """blob 内层 identity(path 含 __dano_jsonstr__)可达 → 不误报。"""
    from dano.execution.page.request_capture import _JSONSTR
    apir = {"body_template": {"formData": {_JSONSTR: {"applicant": 118, "reason": "{{reason}}"}}},
            "params": ["reason"],
            "identity": [{"path": f"formData.{_JSONSTR}.applicant",
                          "source": "localStorage:userInfo.userId"}]}
    assert self_check(apir) == []


def test_self_check_flags_param_without_placeholder():
    """声明了参数但模板里没有它的占位 → 值进不了 body(改了不生效)→ 命中。"""
    apir = {"body_template": {"title": "固定值"}, "params": ["title"]}
    assert any("进不了最终请求体" in p for p in self_check(apir))


def test_self_check_flags_leftover_placeholder():
    """模板有 {{ghost}} 但 ghost 不在 params(占位永远填不上)→ 命中残缺。"""
    apir = {"body_template": {"a": "{{ghost}}"}, "params": []}
    assert any("残留 {{}}" in p for p in self_check(apir))


def test_self_check_step_link_unreachable_target_flagged():
    """多步:link 目标路径在目标步 body 里不存在 → 串联会失败 → 命中。"""
    wf = {"steps": [
        {"body_template": {"x": "{{a}}"}, "params": ["a"]},
        {"body_template": {"flowTask": {"taskId": ""}}, "params": [],
         "links": [{"source_step": 0, "source_path": "data.id", "target_path": "missing.taskId"}]},
    ]}
    assert any("串联目标路径" in p and "missing.taskId" in p for p in self_check(wf))


def test_self_check_step_link_reachable_passes():
    """多步:link 目标路径可达 → 不报。"""
    wf = {"steps": [
        {"body_template": {"x": "{{a}}"}, "params": ["a"]},
        {"body_template": {"flowTask": {"taskId": ""}}, "params": [],
         "links": [{"source_step": 0, "source_path": "data.id", "target_path": "flowTask.taskId"}]},
    ]}
    assert self_check(wf) == []


async def test_dry_self_check_fails_on_validation():
    """坏 skill 走 dry(send=False)→ ok=False 且带 self_check 违规清单(发布前被拦)。"""
    apir = {"method": "POST", "url": "http://x/submit",
            "body_template": {"reason": "{{reason}}"}, "params": ["reason"],
            "identity": [{"path": "applicantId", "source": "localStorage:userInfo.userId"}]}
    out = await execute_api_request(apir, {"reason": "回家"}, send=False)
    assert out["ok"] is False and out["self_check"]


async def test_identity_audit_blocks_frozen_submit():
    """换身路径不可达 + 会话能取到值 → 拒发(blocked),且在发网络前就 return(不连网)。"""
    import json as _j
    storage = {"origins": [{"localStorage": [{"name": "userInfo",
                                              "value": _j.dumps({"userId": "999"})}]}]}
    apir = {"method": "POST", "url": "http://127.0.0.1:1/submit",     # 不可达端口:真发会连不上,验证没走到这
            "body_template": {"applicantId": 118}, "params": [],
            "identity": [{"path": "nope.applicantId", "source": "localStorage:userInfo.userId"}]}
    out = await execute_api_request(apir, {}, storage_state=storage, send=True, verify=False)
    assert out.get("blocked") is True and out["ok"] is False and out["identity_issues"]


# ─────────── P0:token 列表路径(B1 根治:键名含 '.'/'[]' 也能无歧义注入) ───────────
def test_dotted_key_identity_injected_via_tokens():
    """键名含点:用 tokens 注入能写进(纯字符串路径会被 _split_path 拆错 → 写不进)。"""
    from dano.execution.page.request_capture import _apply_identity
    body = {"formData": {"user.id": 0}}
    storage = {"origins": [{"localStorage": [{"name": "u", "value": '{"id":"777"}'}]}]}
    apir = {"identity": [{"path": "formData.user.id", "tokens": ["formData", "user.id"],
                          "source": "localStorage:u.id"}]}
    _apply_identity(body, apir, storage)
    assert body["formData"]["user.id"] == "777"          # tokens 注入成功(B1 根治)


def test_self_check_dotted_key_reachable_with_tokens():
    """有 tokens → 自检判定可达,通过。"""
    apir = {"body_template": {"formData": {"user.id": 0}}, "params": [],
            "identity": [{"path": "formData.user.id", "tokens": ["formData", "user.id"],
                          "source": "localStorage:u.id"}]}
    assert self_check(apir) == []


def test_self_check_dotted_key_without_tokens_flagged():
    """无 tokens、只靠点路径 → _split_path 拆错 → 自检如实报不可达(把 B1 从静默变显式)。"""
    apir = {"body_template": {"formData": {"user.id": 0}}, "params": [],
            "identity": [{"path": "formData.user.id", "source": "localStorage:u.id"}]}
    assert any("找不到落点" in p for p in self_check(apir))


def test_suggest_identity_emits_tokens_for_dotted_key():
    """suggest_identity 对嵌套字段输出 tokens(供运行期无歧义注入)。"""
    import json as _j
    storage = {"origins": [{"localStorage": [{"name": "userInfo",
                                              "value": _j.dumps({"userId": "118"})}]}]}
    pd = _j.dumps({"formData": {"applicantId": "118"}})
    out = suggest_identity(pd, storage)
    assert out and out[0]["tokens"] == ["formData", "applicantId"]




async def test_onboarding_unsupported_when_no_writeable_body():
    """录入:没有可参数化的写请求体 → 诚实标 unsupported(发布前 return,不连库,不发空 skill)。"""
    from dano.onboarding.page_onboard import run_request_onboarding
    out = await run_request_onboarding(tenant="t-x", subsystem="reimburse", action="noop",
                                       api_request={"method": "POST", "url": "http://x/y"})
    assert out["ok"] is False and out["status"] == "unsupported"


# ─────────── P0:零依赖属性模糊 —— 对不变量、不对系统,把 B1/B2/B3/blob 各形状一次锁死 ───────────
import json as _json       # noqa: E402
import random as _random   # noqa: E402

_FUZZ_KEYS = ["a", "b", "field", "user.name", "k.k", "中文键", "f_1", "a[0]", "x.y.z", "amount"]


def _fuzz_node(rng, depth, params, idents, toks):
    """随机生成 body 节点;沿途把 (param, tokens) 记入 params、identity 落点 tokens 记入 idents。
    blob 在记录子路径时插入 __dano_jsonstr__ 段(与运行期一致)。"""
    from dano.execution.page.request_capture import _JSONSTR
    if depth <= 0 or rng.random() < 0.4:
        r = rng.random()
        if r < 0.55:
            p = f"p{len(params)}"
            params.append((p, list(toks)))
            return "{{" + p + "}}"
        if r < 0.72 and toks:
            idents.append(list(toks))
            return 0                                       # identity 常量(运行期被换身覆盖)
        return rng.choice([1, "const", True, "oa_x"])      # 固定常量
    kind = rng.choice(["dict", "list", "blob"])
    if kind == "list":
        return [_fuzz_node(rng, depth - 1, params, idents, toks + [i]) for i in range(rng.randint(1, 3))]
    keys = rng.sample(_FUZZ_KEYS, rng.randint(1, 3))
    if kind == "blob":
        return {_JSONSTR: {k: _fuzz_node(rng, depth - 1, params, idents, toks + [_JSONSTR, k]) for k in keys}}
    return {k: _fuzz_node(rng, depth - 1, params, idents, toks + [k]) for k in keys}


def _fuzz_apir(rng):
    from dano.execution.page.request_capture import _tokens_to_str
    params, idents = [], []
    keys = rng.sample(_FUZZ_KEYS, rng.randint(1, 4))
    templ = {k: _fuzz_node(rng, 4, params, idents, [k]) for k in keys}
    apir = {"body_template": templ, "params": [p for p, _t in params],
            "identity": [{"path": _tokens_to_str(t), "tokens": t, "source": "localStorage:u.id"} for t in idents]}
    return apir, params, idents


def test_property_fuzz_pipeline_invariants():
    """对 250 种随机 body 形状断言三条不变量(self_check + 端到端往返当 oracle)。"""
    from dano.execution.page.request_capture import _apply_identity, _finalize_jsonstr, _path_lookup
    storage = {"origins": [{"localStorage": [{"name": "u", "value": '{"id":"ID999"}'}]}]}
    for seed in range(250):
        rng = _random.Random(seed)
        apir, params, idents = _fuzz_apir(rng)
        # ① 良构 skill → self_check 必过(无误报)
        assert self_check(apir) == [], f"seed={seed} self_check 误报: {self_check(apir)}"
        # ② 每个参数值穿过 substitute→finalize 出现在最终 body(B2/blob 往返不丢值)
        probes = {p: f"@@V{i}@@" for i, (p, _t) in enumerate(params)}
        final = _json.dumps(_finalize_jsonstr(substitute(apir["body_template"], probes, {})), ensure_ascii=False)
        for pr in probes.values():
            assert pr in final, f"seed={seed} 参数值丢失: {pr}"
        # ③ identity 按 tokens 落到正确位置(B1/B3:键含点/方括号/blob 内层也准)
        body = substitute(apir["body_template"], {p: "x" for p, _ in params}, {})
        _apply_identity(body, apir, storage)
        for t in idents:
            assert _path_lookup(body, t) == "ID999", f"seed={seed} identity 注入失败 @ {t}"


def test_property_fuzz_self_check_catches_dropped_param():
    """负面:给任意良构 skill 加一个无占位的幽灵参数 → self_check 必报(无漏报)。"""
    for seed in range(150):
        rng = _random.Random(seed)
        apir, _p, _i = _fuzz_apir(rng)
        apir["params"] = apir["params"] + ["__ghost__"]
        assert any("__ghost__" in p for p in self_check(apir)), f"seed={seed} 漏报丢参数"


# ─────────── P1:多编码 —— application/x-www-form-urlencoded 表单(不止 JSON) ───────────
def test_parse_body_form_urlencoded():
    """非 JSON 的 form 体能解析成扁平字段(可参数化),不再整体 unsupported。"""
    from dano.execution.page.request_capture import _parse_body
    assert _parse_body("title=测试&amount=100&applicant=张三") == {
        "title": "测试", "amount": "100", "applicant": "张三"}
    assert _parse_body("not a form, plain text") is None     # 无 '=' 不误判成表单


def test_build_api_request_form_urlencoded_parameterizes():
    """form 体同样按值参数化(扁平字段)。"""
    req = {"method": "POST", "url": "http://oa.x/sys/save",
           "content_type": "application/x-www-form-urlencoded",
           "post_data": "title=旧标题&amount=100"}
    apir = build_api_request(req, {"title": "标题", "amount": "金额"})
    assert apir["body_template"] == {"title": "{{标题}}", "amount": "{{金额}}"}
    assert apir["content_type"] == "application/x-www-form-urlencoded"


async def test_execute_sends_form_urlencoded():
    """form 表单:解析→参数化→真发按 form 编码(不是 JSON),服务器收到正确字段与 Content-Type。"""
    import http.server
    import threading
    import urllib.parse as _up
    received: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            received["form"] = dict(_up.parse_qsl(self.rfile.read(n).decode()))
            received["ct"] = self.headers.get("Content-Type", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"code":200}')

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = {"method": "POST", "url": f"http://127.0.0.1:{port}/save",
               "content_type": "application/x-www-form-urlencoded",
               "post_data": "title=旧标题&amount=100"}
        apir = build_api_request(req, {"title": "标题", "amount": "金额"})
        out = await execute_api_request(apir, {"标题": "新标题", "金额": "200"}, send=True, verify=False)
        assert out["ok"] and out["status"] == 200, out
        assert received["form"] == {"title": "新标题", "amount": "200"}
        assert "form-urlencoded" in received["ct"]
    finally:
        srv.shutdown()


# ─────────── P1:字段置信度打分 + 阈值路由 ───────────
def test_flatten_body_confidence_scoring():
    """字段置信度:值对到 DOM 标签 → 高(auto);内部机器标识 key 无标签 → 低(需澄清)。"""
    body = '{"reason":"回家","Activity_09dlq0g":"待定选项"}'
    fields = {f["key"]: f for f in flatten_body(body, {"原因": "回家"})}
    assert fields["reason"]["confidence_tier"] == "auto"          # 值唯一对到标签"原因"
    act = fields["Activity_09dlq0g"]                              # 无标签 + 像 BPM 节点 ID
    assert act["confidence"] < 0.90 and act["confidence_tier"] in ("clarify", "reject")


def test_confidence_tier_thresholds():
    from dano.execution.page.request_capture import confidence_tier
    assert confidence_tier(0.96) == "auto"
    assert confidence_tier(0.75) == "clarify"
    assert confidence_tier(0.4) == "reject"


# ─────────── P1:B2 子串参数化(值嵌在长串里,只参数化那一段、保留常量前后缀) ───────────
def test_substitute_segments_join():
    from dano.execution.page.request_capture import _SEG
    out = substitute({_SEG: ["请假事由:", {"$p": "原因"}]}, {"原因": "回家"})
    assert out == "请假事由:回家"
    # 没填该参数 → 留 {{}} 占位(供 leftover 检测)
    assert "{{原因}}" in substitute({_SEG: ["x", {"$p": "原因"}]}, {})


def test_build_api_request_substring_keeps_constant_prefix():
    """填写值是叶子真子串 → 段拼接;改参数只动那一段,前缀常量保留。"""
    from dano.execution.page.request_capture import _SEG
    req = {"method": "POST", "url": "http://x/y", "post_data": '{"remark":"请假事由:回家"}'}
    apir = build_api_request(req, {"remark": "原因"}, typed={"原因": "回家"})
    assert apir["body_template"]["remark"] == {_SEG: ["请假事由:", {"$p": "原因"}]}
    out = substitute(apir["body_template"], {"原因": "出差三天"})
    assert out["remark"] == "请假事由:出差三天"               # 前缀保留,只换子串


def test_build_api_request_whole_value_not_split():
    """填写值==整个叶子 → 整值替换(不切段);未标记字段保持常量(不被误切)。"""
    req = {"method": "POST", "url": "http://x/y",
           "post_data": '{"title":"测试采购","note":"采购说明:测试采购"}'}
    apir = build_api_request(req, {"title": "标题"}, typed={"标题": "测试采购"})
    assert apir["body_template"]["title"] == "{{标题}}"       # 整值=填写值 → 整体替换
    assert apir["body_template"]["note"] == "采购说明:测试采购"  # 未标记 → 常量,虽含"测试采购"也不切


def test_self_check_passes_with_segment_template():
    from dano.execution.page.request_capture import _SEG
    apir = {"body_template": {"remark": {_SEG: ["前缀:", {"$p": "原因"}]}}, "params": ["原因"]}
    assert self_check(apir) == []


# ─────────── P2:活体验证自适应策略(可控性分级 + 验证计划 + 测试数据标记) ───────────
def test_env_controllability_classification():
    from dano.execution.page.request_capture import env_controllability
    assert env_controllability({"environment": "sandbox"}) == "reversible"
    assert env_controllability({"reversible": True}) == "reversible"
    assert env_controllability({"environment": "prod"}) == "irreversible"
    assert env_controllability({"reversible": False}) == "irreversible"
    assert env_controllability({}) == "unknown"            # 未声明 → 保守当不可逆
    assert env_controllability(None) == "unknown"


def test_capture_verification_plan_adaptive():
    """自适应闸门:可逆+有回查→live(可 verified);否则 structural(partially_verified)。"""
    from dano.execution.page.request_capture import capture_verification_plan
    live = capture_verification_plan({"environment": "sandbox"}, {"fact_check": {"endpoint": "/my"}})
    assert live["mode"] == "live" and live["controllability"] == "reversible"
    no_fc = capture_verification_plan({"environment": "sandbox"}, {})
    assert no_fc["mode"] == "structural" and no_fc["fact_check"] is False
    prod = capture_verification_plan({"environment": "prod"}, {"fact_check": {"endpoint": "/my"}})
    assert prod["mode"] == "structural" and prod["controllability"] == "irreversible"
    assert capture_verification_plan({}, {"fact_check": {}})["mode"] == "structural"




# ─────────── P3:LLM 非阻断语义顾问(只提议,不当结构闸门;喂元数据不带凭证) ───────────
class _FakeChat:
    def __init__(self, out):
        self.out = out
        self.seen = {}

    async def complete_json(self, *, model, system, user, timeout_s):
        self.seen = {"model": model, "system": system, "user": user}
        return self.out


async def test_advisory_capture_review_returns_notes_and_redacts():
    from dano.review.board import advisory_capture_review
    fake = _FakeChat({"notes": ["参数 Activity_09dlq0g 像内部标识,建议起人话名"]})
    apir = {"params": ["Activity_09dlq0g"], "field_types": {"Activity_09dlq0g": "enum"},
            "identity": [{"path": "applicantId"}], "method": "POST", "path": "/oa/leave/submit"}
    notes = await advisory_capture_review(fake, "m", action="submit_leave", api_request=apir)
    assert notes == ["参数 Activity_09dlq0g 像内部标识,建议起人话名"]
    # 只喂元数据:参数名在,但绝不带 body 值/凭证字样
    assert "Activity_09dlq0g" in fake.seen["user"]
    assert "password" not in fake.seen["user"].lower() and "cookie" not in fake.seen["user"].lower()


def test_is_dry_mode_reason_recognizes_design_safe_mode():
    """识别"dry/self_check 未真跑"类否决理由(录制 by-design 安全模式);真问题理由不误命中。"""
    from dano.onboarding.repair import is_dry_mode_reason
    assert is_dry_mode_reason(
        "sandbox_evidence 中 kind=self_check 的 evidence.request.dry=true,无法验证该请求在 sandbox 环境下真实跑通,"
        "违反【运行架构】第 6 点 'sandbox_evidence 已证明该资产...真实跑通' 的要求。")
    assert is_dry_mode_reason("请求仅构造未真发")
    assert not is_dry_mode_reason("method/path 指向生产端点 admin.prod.com,违反最小权限")
    assert not is_dry_mode_reason("参数 `领导` 像内部机器标识,建议起人话名")


async def test_request_review_scrubs_dry_rejection_publishes_partial():
    """根因修复:评审仅因'dry/self_check 未真跑'否决 **dry-only** 资产(录制 by-design 安全模式)→
    request_review 确定性剔除该理由(改 DB 证据 → verify_reviewed 也认)→ 照常发布为 partially_verified。"""
    import http.server  # noqa: F401 —— 保持与 e2e 同风格;此处用不到真服务器
    from uuid import uuid4
    import pytest
    from dano.infra.db import close_pool, get_pool, init_pool
    from dano.onboarding.page_onboard import run_request_onboarding
    from dano.shared.enums import Subsystem
    try:
        await init_pool()
    except Exception:  # noqa: BLE001
        pytest.skip("PG 不可用")

    class _DryRejectBoard:
        """acceptance/security 过;compliance **只因 dry/未真跑** 否决 → 应被确定性剔除,不阻断。"""
        async def review(self, *, asset_type, asset_key, body, evidence):  # noqa: ANN001
            acc, sec = _FakeVerdict("acceptance"), _FakeVerdict("security")
            comp = _FakeVerdict("compliance")
            comp.passed = False
            comp.reasons = ["sandbox_evidence 中 kind=self_check 的 evidence.request.dry=true,"
                            "无法验证该请求在 sandbox 环境下真实跑通,违反【运行架构】第 6 点。"]
            return [acc, sec, comp]

    tenant = f"dry-scrub-{uuid4().hex[:8]}"
    from dano.agent_tools import tools as _T
    _T.set_review_board(_DryRejectBoard())
    try:
        apir = {"method": "POST", "url": "http://oa.x/submit",
                "body_template": {"reason": "{{原因}}"}, "params": ["原因"],
                "sample_inputs": {"原因": "录制原因"}, "auth_headers": {},
                "success_rule": {"field": "code", "ok_values": ["200"]}}
        rep = await run_request_onboarding(
            tenant=tenant, subsystem=Subsystem.REIMBURSE.value, action="dry_scrub_pub",
            api_request=apir, sample_inputs={"原因": "回家"})   # 无 storage_state → dry-only(do_live=False)
        assert rep["ok"] is True and rep["status"] == "partially_verified", rep
    finally:
        _T.set_review_board(None)
        async with get_pool().acquire() as c:
            await c.execute("DELETE FROM asset_drafts WHERE tenant=$1", tenant)
            await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        await close_pool()


async def test_advisory_capture_review_safe_degrade():
    """无 client / 无 model / 调用抛错 / 返回非法 → 一律 [](顾问绝不阻断发布)。"""
    from dano.review.board import advisory_capture_review
    assert await advisory_capture_review(None, "m", action="a", api_request={}) == []
    assert await advisory_capture_review(_FakeChat({}), "", action="a", api_request={}) == []
    assert await advisory_capture_review(_FakeChat({"notes": "不是数组"}), "m", action="a", api_request={}) == []

    class _Boom:
        async def complete_json(self, **k):
            raise RuntimeError("LLM down")
    assert await advisory_capture_review(_Boom(), "m", action="a", api_request={}) == []


# ─────────── P3:LLM 业务 Goal 提炼 + 确定性 Goal 完整性门 + L3 必确认 ───────────
async def test_generate_goal_proposes_and_redacts():
    from dano.review.board import generate_goal
    fake = _FakeChat({"intent": "创建并提交采购申请", "business_type": "purchase",
                      "required_inputs": ["title", "amount"], "success_criteria": ["单据已创建"],
                      "forbidden_actions": ["删除", "代他人审批"], "risk_level": "L3"})
    apir = {"params": ["title", "amount"], "method": "POST", "path": "/oa/purchase/create",
            "field_types": {"amount": "number"}}
    goal = await generate_goal(fake, "m", action="submit_purchase", api_request=apir)
    assert goal["intent"] == "创建并提交采购申请" and goal["risk_level"] == "L3"
    assert "amount" in fake.seen["user"] and "password" not in fake.seen["user"].lower()


async def test_generate_goal_safe_degrade():
    from dano.review.board import generate_goal
    assert await generate_goal(None, "m", action="a", api_request={}) == {}
    assert await generate_goal(_FakeChat({}), "", action="a", api_request={}) == {}

    class _Boom:
        async def complete_json(self, **k):
            raise RuntimeError("down")
    assert await generate_goal(_Boom(), "m", action="a", api_request={}) == {}


def test_validate_goal_grounded_passes():
    from dano.execution.page.request_capture import validate_goal
    goal = {"intent": "提交采购", "required_inputs": ["title"], "success_criteria": ["已创建"],
            "forbidden_actions": ["删除"], "risk_level": "L3"}
    assert validate_goal(goal, {"params": ["title", "amount"]}) == []


def test_validate_goal_catches_hallucinated_input_and_gaps():
    """LLM 臆造的 required_input(不在实际参数)+ 缺成功标准/禁止动作 → Goal 门拦下。"""
    from dano.execution.page.request_capture import validate_goal
    goal = {"intent": "", "required_inputs": ["ghost_field"], "success_criteria": [],
            "forbidden_actions": [], "risk_level": ""}
    probs = validate_goal(goal, {"params": ["title"]})
    assert any("臆造" in p or "无来源" in p for p in probs)
    assert any("intent" in p for p in probs) and any("success_criteria" in p for p in probs)
    assert any("forbidden_actions" in p for p in probs) and any("risk_level" in p for p in probs)


def test_goal_needs_confirmation_l3_required():
    from dano.execution.page.request_capture import goal_needs_confirmation
    assert goal_needs_confirmation({"risk_level": "L3"}) is True
    assert goal_needs_confirmation({"risk_level": ""}) is True      # 未识别 → 保守要确认
    assert goal_needs_confirmation({"risk_level": "L1"}) is False


# ─────────── P2 收尾:可逆沙箱活体真跑 → verified(本地服务器模拟可控目标系统) ───────────
async def test_onboarding_live_verify_reaches_verified():
    """可逆沙箱 + fact_check + 登录态 → 真发写 + 事实回查通过 → status=verified(而非 partially_verified)。"""
    pytest.importorskip("asyncpg")
    import http.server
    import threading
    from uuid import uuid4

    from dano.infra.db import close_pool, get_pool, init_pool
    from dano.onboarding.page_onboard import run_request_onboarding
    from dano.shared.enums import Subsystem
    try:
        await init_pool()
    except Exception:  # noqa: BLE001
        pytest.skip("PG 不可用")

    store: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):                                   # 写接口:存下提交的 reason,回 code=200
            n = int(self.headers.get("Content-Length", 0))
            store["reason"] = _json.loads(self.rfile.read(n).decode()).get("reason")
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(b'{"code":200}')

        def do_GET(self):                                    # 「我的记录」:返回刚提交的值(供 fact_check 回查)
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(_json.dumps({"rows": [{"reason": store.get("reason")}]}).encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tenant = f"live-e2e-{uuid4().hex[:8]}"
    from dano.agent_tools import tools as _T
    _T.set_review_board(_FakeBoard())                        # capture 写须过评审(发布硬闸门),注 fake 板
    try:
        apir = {"method": "POST", "url": f"http://127.0.0.1:{port}/save",
                "body_template": {"reason": "{{原因}}"}, "params": ["原因"],
                "sample_inputs": {"原因": "录制原因"}, "auth_headers": {},
                "success_rule": {"field": "code", "ok_values": ["200"]},
                "fact_check": {"param": "原因", "match_field": "reason",
                               "endpoint": f"http://127.0.0.1:{port}/my", "retries": 1, "backoff_s": 0}}
        rep = await run_request_onboarding(
            tenant=tenant, subsystem=Subsystem.REIMBURSE.value, action="live_submit",
            api_request=apir, sample_inputs={"原因": "回家真跑"},
            deploy={"environment": "sandbox"}, storage_state={})
        assert rep["status"] == "verified", rep              # 结构 + 活体均验 → verified
        assert store["reason"] == "回家真跑"                  # 真发确实带了新值
    finally:
        _T.set_review_board(None)
        async with get_pool().acquire() as c:
            await c.execute("DELETE FROM asset_drafts WHERE tenant=$1", tenant)
            await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        srv.shutdown()
        await close_pool()


# ─────────── 补齐:业务相关性门 / 字段语义门 / 步骤依赖门(无源) ───────────
def test_looks_dangerous_write():
    from dano.execution.page.request_capture import looks_dangerous_write
    assert looks_dangerous_write({"method": "DELETE", "url": "http://x/api/order/9"}) is True
    assert looks_dangerous_write({"method": "POST", "url": "http://x/bpm/task/reject"}) is True
    assert looks_dangerous_write({"method": "POST", "url": "http://x/flow/terminate"}) is True
    assert looks_dangerous_write({"method": "POST", "url": "http://x/leave/submit"}) is False
    assert looks_dangerous_write({"method": "POST", "url": "http://x/order/cancellation-policy"}) is False  # 整段才算


def test_self_check_step_link_no_source_flagged():
    """步骤依赖门:link 目标可达但**无来源** → 也报(运行期取不到值)。"""
    wf = {"steps": [
        {"body_template": {"x": "{{a}}"}, "params": ["a"]},
        {"body_template": {"flowTask": {"taskId": ""}}, "params": [],
         "links": [{"target_path": "flowTask.taskId"}]},   # 无 source_step / source_path
    ]}
    assert any("无来源" in p for p in self_check(wf))


async def test_onboarding_field_semantics_blocks_internal_required():
    """字段语义门:必填参数是内部机器标识(Activity_xxx)→ needs_clarification(不静默泄漏)。"""
    from dano.onboarding.page_onboard import run_request_onboarding
    apir = {"method": "POST", "url": "http://x/submit",
            "body_template": {"a": "{{Activity_09dlq0g}}"}, "params": ["Activity_09dlq0g"]}
    out = await run_request_onboarding(tenant="t-x", subsystem="reimburse", action="sub",
                                       api_request=apir, required=["Activity_09dlq0g"])
    assert out["status"] == "needs_clarification"
    assert any("Activity_09dlq0g" in c for c in out["clarifications"])


# ─────────── 补齐:请求语义角色(确定性 node 4)+ identity 证据来源(node 8) ───────────
def test_classify_request_role():
    from dano.execution.page.request_capture import classify_request_role
    assert classify_request_role({"method": "DELETE", "url": "http://x/order/1"})["semanticRole"] == "destructive"
    assert classify_request_role({"method": "POST", "url": "http://x/prod-api/login",
                                  "post_data": '{"password":"x"}'})["semanticRole"] == "auth"
    assert classify_request_role({"method": "GET", "url": "http://x/system/user/list"})["semanticRole"] == "enum_options"
    assert classify_request_role({"method": "GET", "url": "http://x/info"})["semanticRole"] == "query"
    sub = classify_request_role({"method": "POST", "url": "http://x/oa/leave/submit"})
    assert sub["semanticRole"] == "workflow_submit" and sub["risk_level"] == "L3"
    assert classify_request_role({"method": "POST", "url": "http://x/api/save"})["semanticRole"] == "business_write"


# ─────────── LLM 三维审核接入:驳回 → needs_clarification + 把理由还回(测试驳回) ───────────
class _RejectVerdict:
    def __init__(self, role, passed, reasons):
        self.role, self.model_id, self.passed, self.reasons = role, f"fake-{role}", passed, reasons


class _RejectBoard:
    """业务逻辑(acceptance)驳回、安全/合规通过 —— 测审核闸门能拦 + 把 reason 还回。"""
    async def review(self, *, asset_type, asset_key, body, evidence):  # noqa: ANN001
        return [_RejectVerdict("acceptance", False, ["参数 amount 与 goal.required_inputs 不符,无法实现业务意图"]),
                _RejectVerdict("security", True, []), _RejectVerdict("compliance", True, [])]


async def test_onboarding_review_gate_rejects_and_returns_reasons():
    """三维审核驳回(业务逻辑不过)→ stage=review · needs_clarification · clarifications 带模型 reason。"""
    from uuid import uuid4

    from dano.agent_tools import tools as _T
    from dano.infra.db import close_pool, get_pool, init_pool
    from dano.onboarding.page_onboard import run_request_onboarding
    from dano.shared.enums import Subsystem
    try:
        await init_pool()
    except Exception:  # noqa: BLE001
        pytest.skip("PG 不可用")
    tenant = f"rev-e2e-{uuid4().hex[:8]}"
    _T.set_review_board(_RejectBoard())
    try:
        apir = {"method": "POST", "url": "http://oa.x/submit", "body_template": {"reason": "{{原因}}"},
                "params": ["原因"], "sample_inputs": {"原因": "录制原因"}}
        out = await run_request_onboarding(tenant=tenant, subsystem=Subsystem.REIMBURSE.value,
                                           action="rev_test", api_request=apir, sample_inputs={"原因": "回家"})
        assert out["ok"] is False and out["status"] == "needs_clarification" and out["stage"] == "review"
        assert any("acceptance" in c and "amount" in c for c in out["clarifications"]), out["clarifications"]
    finally:
        _T.set_review_board(None)
        async with get_pool().acquire() as c:
            await c.execute("DELETE FROM asset_drafts WHERE tenant=$1", tenant)
            await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        await close_pool()


# ─────────── LLM 修复循环 P0:执行器 + 检出器 + 循环骨架(fake propose,离线可测) ───────────
def test_looks_session_specific():
    from dano.execution.page.repair_ops import looks_session_specific as f
    assert f("SEQ-20260625-2F29") is True
    assert f("1782144000000") is True
    assert f("550e8400-e29b-41d4-a716-446655440000") is True
    assert f("oa_leave") is False and f("100") is False and f("事假") is False and f("") is False


def test_looks_placeholder_name():
    from dano.execution.page.repair_ops import looks_placeholder_name as f
    assert f("请输入运行编号") is True and f("请选择类型") is True and f("如 Homo sapiens") is True
    assert f("物种") is False and f("amount") is False


def test_apply_fix_ops_parameterize_and_reject_bad_ref():
    from dano.execution.page.repair_ops import apply_fix_ops
    apir = {"body_template": {"taskId": "SEQ-1", "reason": "{{原因}}"}, "params": ["原因"]}
    out, applied, rejected = apply_fix_ops(apir, [
        {"op": "parameterize", "path": ["taskId"], "param": "任务号"},
        {"op": "remap_field", "param": "鬼", "target_path": ["reason"]},   # param 不存在 → 拒
    ])
    assert out["body_template"]["taskId"] == "{{任务号}}" and "任务号" in out["params"]
    assert len(applied) == 1 and len(rejected) == 1 and rejected[0]["op"] == "remap_field"
    assert apir["body_template"]["taskId"] == "SEQ-1"      # 原对象不被改(深拷贝)


def test_apply_fix_ops_remap_swaps_fields():
    from dano.execution.page.repair_ops import apply_fix_ops
    apir = {"body_template": {"species": "{{a}}", "method": "{{b}}"}, "params": ["a", "b"]}
    out, _applied, _rej = apply_fix_ops(apir, [{"op": "remap_field", "param": "a", "target_path": ["method"]}])
    assert out["body_template"]["method"] == "{{a}}" and out["body_template"]["species"] == "{{b}}"


def test_apply_fix_ops_rename_and_success_rule():
    from dano.execution.page.repair_ops import apply_fix_ops
    apir = {"body_template": {"x": "{{请输入运行编号}}"}, "params": ["请输入运行编号"]}
    out, _a, _r = apply_fix_ops(apir, [
        {"op": "rename_param", "old": "请输入运行编号", "new": "运行编号"},
        {"op": "set_success_rule", "field": "code", "ok_values": ["0"]},
    ])
    assert out["body_template"]["x"] == "{{运行编号}}" and "运行编号" in out["params"]
    assert out["success_rule"] == {"field": "code", "ok_values": ["0"]}


def test_apply_fix_ops_drop_step_fixes_links():
    from dano.execution.page.repair_ops import apply_fix_ops
    apir = {"steps": [
        {"body_template": {"a": 1}, "params": []},
        {"body_template": {"taskId": ""}, "params": [],
         "links": [{"target_path": "taskId", "source_step": 0, "source_path": "data.id"}]},
    ]}
    out, _a, _r = apply_fix_ops(apir, [{"op": "drop_step", "step": 0}])
    assert len(out["steps"]) == 1 and out["steps"][0].get("links") == []   # 源步删 → link 丢弃


def test_collect_repair_findings():
    from dano.execution.page.repair_ops import collect_repair_findings
    apir = {"body_template": {"taskId": "SEQ-20260625-2F29", "x": "{{请输入编号}}"}, "params": ["请输入编号"]}
    kinds = {f["kind"] for f in collect_repair_findings(apir)}
    assert "session_constant" in kinds and "placeholder_name" in kinds


def test_collect_repair_findings_ignores_linked_session_constant():
    from dano.execution.page.repair_ops import collect_repair_findings
    apir = {
        "steps": [
            {"method": "POST", "path": "/create", "body_template": {"name": "{{名称}}"}, "params": ["名称"]},
            {
                "method": "POST",
                "path": "/submit",
                "body_template": {"conversation_id": "26a5509d-4fbb-4241-8924-186ed6bdd3dc"},
                "params": [],
                "links": [{"source_step": 0, "source_path": "data.conversation_id", "target_path": "conversation_id"}],
            },
        ]
    }

    assert collect_repair_findings(apir) == []


async def test_run_repair_loop_converges_with_fake_propose():
    """脏 skill(会话常量焊死 + 占位名参数)→ fake 修复器出 parameterize+rename → 循环后 findings 清零。"""
    from dano.execution.page.repair_ops import collect_repair_findings
    from dano.onboarding.repair import run_repair_loop
    apir = {"body_template": {"taskId": "SEQ-20260625-2F29", "x": "{{请输入编号}}"}, "params": ["请输入编号"]}

    async def fake_propose(a, findings, goal):
        ops = []
        for f in findings:
            if f["kind"] == "session_constant":
                ops.append({"op": "parameterize", "path": f["path"], "param": "任务号"})
            elif f["kind"] == "placeholder_name":
                ops.append({"op": "rename_param", "old": f["param"], "new": "编号"})
        return ops

    repaired, _rounds, _hist, remaining = await run_repair_loop(apir, fake_propose)
    assert remaining == [] and collect_repair_findings(repaired) == []
    assert repaired["body_template"]["taskId"] == "{{任务号}}" and repaired["body_template"]["x"] == "{{编号}}"


# ─────────── 修复循环 P1:LLM 修复器 + 审核 findings 转换 + 接进主流程(自动修复,不重录) ───────────
async def test_generate_fix_ops_redacts_and_returns_ops():
    from dano.onboarding.repair import generate_fix_ops
    fake = _FakeChat({"ops": [{"op": "parameterize", "path": ["taskId"], "param": "任务号"}]})
    apir = {"body_template": {"taskId": "SEQ-1", "reason": "{{原因}}"}, "params": ["原因"],
            "method": "POST", "path": "/x"}
    ops = await generate_fix_ops(fake, "m", goal={"intent": "创建"}, api_request=apir,
                                 findings=[{"kind": "session_constant", "detail": "x"}])
    assert ops == [{"op": "parameterize", "path": ["taskId"], "param": "任务号"}]
    assert "原因" in fake.seen["user"] and "SEQ-1" not in fake.seen["user"]   # 只喂骨架(param↔path),不带 body 值


async def test_generate_fix_ops_safe_degrade():
    from dano.onboarding.repair import generate_fix_ops
    assert await generate_fix_ops(None, "m", goal={}, api_request={}, findings=[{"x": 1}]) == []
    assert await generate_fix_ops(_FakeChat({"ops": []}), "m", goal={}, api_request={}, findings=[]) == []


def test_review_findings_converter():
    from dano.onboarding.repair import review_findings
    vs = [{"role": "acceptance", "passed": False, "reasons": ["业务逻辑不符"]},
          {"role": "security", "passed": True, "reasons": []}]
    assert review_findings(vs) == [{"kind": "review_acceptance", "detail": "业务逻辑不符"}]


async def test_onboarding_repair_loop_fixes_and_publishes():
    """脏 skill(硬编码 task ID 常量)+ 注入修复器(参数化它)→ 自动修复 → 发布(不重录)。"""
    from uuid import uuid4

    from dano.agent_tools import tools as _T
    from dano.infra.db import close_pool, get_pool, init_pool
    from dano.onboarding.page_onboard import run_request_onboarding
    from dano.shared.enums import Subsystem
    try:
        await init_pool()
    except Exception:  # noqa: BLE001
        pytest.skip("PG 不可用")
    tenant = f"rep-e2e-{uuid4().hex[:8]}"
    _T.set_review_board(_FakeBoard())            # 审核全过

    async def fake_propose(api_request, findings, goal):
        ops = []
        for f in findings:
            if f.get("kind") == "session_constant":
                ops.append({"op": "parameterize", "path": f["path"], "param": "任务号"})
        return ops
    _T.set_fix_proposer(fake_propose)
    try:
        apir = {"method": "POST", "url": "http://oa.x/submit",
                "body_template": {"taskId": "SEQ-20260625-2F29", "reason": "{{原因}}"},
                "params": ["原因"], "sample_inputs": {"原因": "录制原因"}}
        out = await run_request_onboarding(tenant=tenant, subsystem=Subsystem.REIMBURSE.value,
                                           action="rep_test", api_request=apir, sample_inputs={"原因": "回家"})
        assert out["ok"] is True, out
        assert "任务号" in (out["api"]["params"] or [])    # 硬编码 task ID 被自动参数化,无需重录
    finally:
        _T.set_review_board(None)
        _T.set_fix_proposer(None)
        async with get_pool().acquire() as c:
            await c.execute("DELETE FROM asset_drafts WHERE tenant=$1", tenant)
            await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        await close_pool()


# ─────────── 多接口自动判流程:提交锚点 + 数据依赖闭包,丢噪声 ───────────
def test_suggest_workflow_steps_drops_noise_keeps_chain():
    from dano.execution.page.request_capture import suggest_workflow_steps
    writes = [
        {"method": "POST", "url": "http://x/task/create", "post_data": '{"name":"x"}',
         "response_json": {"data": {"taskId": "TASK-9988"}}},                       # 0 创建(产 taskId)
        {"method": "PUT", "url": "http://x/old/SEQ-1/status", "post_data": '{"status":"done"}',
         "response_json": {"code": 0}},                                             # 1 改旧实体(噪声)
        {"method": "POST", "url": "http://x/task/submit",
         "post_data": '{"taskId":"TASK-9988","reason":"回家"}', "response_json": {"code": 0}},  # 2 提交
    ]
    assert suggest_workflow_steps(writes, {"原因": "回家"}) == [0, 2]   # 提交+其依赖;噪声步1被丢


def test_suggest_workflow_steps_single_submit():
    from dano.execution.page.request_capture import suggest_workflow_steps
    writes = [{"method": "POST", "url": "http://x/submit", "post_data": '{"reason":"回家"}',
               "response_json": {"code": 0}}]
    assert suggest_workflow_steps(writes, {"原因": "回家"}) == [0]


def test_suggest_workflow_steps_excludes_auth():
    from dano.execution.page.request_capture import suggest_workflow_steps
    writes = [
        {"method": "POST", "url": "http://x/login", "post_data": '{"password":"p"}', "response_json": {}},  # 鉴权,排除
        {"method": "POST", "url": "http://x/submit", "post_data": '{"reason":"回家"}', "response_json": {"code": 0}},
    ]
    assert suggest_workflow_steps(writes, {"原因": "回家"}) == [1]


# ─────────── 审计修复:回滚/source_path/bind_placeholder/多占位/脱敏/聚焦问题 ───────────
def test_redact_keeps_credential_type_and_environment():
    """脱敏 bug 修复:credential_type/environment 是评审元数据,绝不脱敏(否则 compliance fail-closed 误判)。"""
    from dano.review.board import _redact_secrets
    out = _redact_secrets({"credential_type": "test", "environment": "sandbox",
                           "authorization": "Bearer x", "password": "p"})
    assert out["credential_type"] == "test" and out["environment"] == "sandbox"
    assert out["authorization"] != "Bearer x" and out["password"] != "p"   # 真凭证仍脱敏


def test_apply_fix_ops_rolls_back_op_that_breaks_structure():
    """#2 逐 op 回滚:parameterize 把 b 也设成已有参数 X → X 填两处(自检报错)→ 回滚该 op。"""
    from dano.execution.page.repair_ops import apply_fix_ops
    apir = {"body_template": {"a": "{{X}}", "b": "const"}, "params": ["X"]}
    out, applied, rejected = apply_fix_ops(apir, [{"op": "parameterize", "path": ["b"], "param": "X"}])
    assert not applied and rejected and "回滚" in rejected[0]["detail"]
    assert out["body_template"]["b"] == "const"            # 已回滚


def test_apply_fix_ops_link_step_validates_source_path():
    """#3 link_step 的 source_path 必须在来源步响应里真实存在,否则拒。"""
    from dano.execution.page.repair_ops import apply_fix_ops
    apir = {"steps": [
        {"body_template": {"x": "{{a}}"}, "params": ["a"], "response_json": {"data": {"id": "T1"}}},
        {"body_template": {"taskId": ""}, "params": []},
    ]}
    _o1, ap1, _r1 = apply_fix_ops(apir, [{"op": "link_step", "target_step": 1, "target_path": ["taskId"],
                                          "source_step": 0, "source_path": ["data", "id"]}])
    assert ap1                                              # 真实 source_path → 接受
    _o2, ap2, rej2 = apply_fix_ops(apir, [{"op": "link_step", "target_step": 1, "target_path": ["taskId"],
                                           "source_step": 0, "source_path": ["data", "nope"]}])
    assert not ap2 and rej2 and "source_path" in rej2[0]["detail"]   # 不存在 → 拒


def test_apply_fix_ops_bind_placeholder():
    """#4 bind_placeholder:把占位参数绑到正确字段,清掉它在别处的占位。"""
    from dano.execution.page.repair_ops import apply_fix_ops
    apir = {"body_template": {"x": "{{请输入编号}}", "y": "const"}, "params": ["请输入编号"]}
    out, applied, _r = apply_fix_ops(apir, [{"op": "bind_placeholder", "param": "请输入编号", "target_path": ["y"]}])
    assert applied and out["body_template"]["y"] == "{{请输入编号}}" and out["body_template"]["x"] == ""


def test_self_check_flags_param_in_multiple_leaves():
    """#5 同一参数填多处(扁平/嵌套键歧义)→ self_check 报错。"""
    apir = {"body_template": {"a": "{{X}}", "b": "{{X}}"}, "params": ["X"]}
    assert any("同时填入" in p for p in self_check(apir))


def test_focus_question_single():
    """#7 改不动 → 聚成一个精准问题(非一长串)。"""
    from dano.onboarding.page_onboard import _focus_question
    q = _focus_question("提交请假", [{"detail": "参数A语义不清"}, {"detail": "参数B不清"}])
    assert "提交请假" in q and "参数A语义不清" in q and "还有 1 项" in q


def test_suggest_workflow_steps_keeps_user_value_step():
    """多接口优化:含用户填写值的业务写也纳入(非噪声),即便它不数据依赖提交。"""
    from dano.execution.page.request_capture import suggest_workflow_steps
    writes = [
        {"method": "POST", "url": "http://x/draft", "post_data": '{"title":"我的标题"}', "response_json": {"code": 0}},
        {"method": "POST", "url": "http://x/heartbeat", "post_data": '{"t":1}', "response_json": {"code": 0}},  # 噪声
        {"method": "POST", "url": "http://x/submit", "post_data": '{"reason":"回家"}', "response_json": {"code": 0}},
    ]
    out = suggest_workflow_steps(writes, {"标题": "我的标题", "原因": "回家"})
    assert 0 in out and 2 in out and 1 not in out          # draft(含用户值)+提交;心跳(无值)丢


@pytest.mark.asyncio
async def test_resolve_selects_projects_fields_from_selected_option_object(monkeypatch):
    import dano.execution.page.request_capture as rc

    async def current_source(*_args, **_kwargs):
        return [{
            "projectId": "p-1", "projectName": "数据智能平台5.2.1",
            "remainingHours": 8, "teamId": "team-public", "approverId": "user-yan",
        }]

    monkeypatch.setattr(rc, "_fetch_select_list", current_source)
    api_request = {"selects": [{
        "param": "项目名称", "path": "projectId",
        "source_url": "/rpc/project-context", "label_key": "projectName", "value_key": "projectId",
        "id_path": "projectId",
        "field_projections": {
            "remainingHours": "remainingHours", "teamId": "teamId", "approverId": "approverId",
        },
    }]}

    fields, overrides = await _resolve_selects(
        api_request, {"项目名称": "数据智能平台5.2.1"},
        base_url="", storage_state=None, token_key=None, verify=False,
    )

    assert fields["项目名称"] == "数据智能平台5.2.1"
    assert overrides[("projectId",)] == "p-1"
    assert overrides[("remainingHours",)] == 8
    assert overrides[("teamId",)] == "team-public"
    assert overrides[("approverId",)] == "user-yan"

def test_simple_list_with_audit_columns_is_still_option_source():
    request = {
        "method": "GET",
        "url": "https://example.test/admin-api/bd/seal/simple-list?status=0",
        "response_json": {
            "code": 0,
            "data": [
                {
                    "id": "seal-1",
                    "name": "Company Seal",
                    "status": 0,
                    "remark": "",
                    "createTime": 1784419173000,
                },
                {
                    "id": "seal-2",
                    "name": "Finance Seal",
                    "status": 0,
                    "remark": "",
                    "createTime": 1784419174000,
                },
            ],
        },
    }

    role = classify_capture_request(request)

    assert role["role"] == "read_option"
    assert role["keep"] is False

def test_system_user_page_with_audit_columns_is_reference_option_source():
    request = {
        "method": "GET",
        "url": "https://example.test/admin-api/system/user/page?pageNo=1&pageSize=100",
        "response_json": {
            "code": 0,
            "data": {
                "list": [
                    {"id": 149, "nickname": "hunk", "status": 0, "createTime": 1784419173000, "remark": ""},
                    {"id": 144, "nickname": "姜楠", "status": 0, "createTime": 1784419174000, "remark": ""},
                ],
                "total": 2,
            },
        },
    }

    role = classify_capture_request(request)

    assert role["role"] == "read_option"
    assert role["keep"] is False
