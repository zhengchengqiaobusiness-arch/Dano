"""方式B 升级:抓提交请求 → 参数化(纯函数,离线)。"""
from __future__ import annotations

from dano.execution.page.request_capture import (
    _response_ok,
    as_list_payload,
    infer_success_rule,
    build_api_request,
    build_api_workflow,
    _extract_total,
    discover_step_links,
    execute_api,
    looks_like_auth_write,
    execute_api_request,
    execute_api_workflow,
    extract_auth_headers,
    flatten_body,
    json_write_requests,
    list_read_requests,
    parameterize_request,
    pick_submit_request,
    resolve_identity_value,
    self_check,
    substitute,
    suggest_fact_check,
    suggest_identity,
    suggest_select_names,
    suggest_selects,
)

_SAMPLES = {"请假类型": "事假", "开始时间": "2026-06-24", "结束时间": "2026-06-26", "原因": "大地色多"}
_SUBMIT = ('{"leaveType":"事假","startTime":"2026-06-24","endTime":"2026-06-26",'
           '"reason":"大地色多","procDefId":"PROC123","draft":false}')
_REQUESTS = [
    {"method": "GET", "url": "http://oa.x/prod-api/getInfo", "post_data": None},
    {"method": "POST", "url": "http://oa.x/prod-api/login", "post_data": '{"u":"admin"}'},     # 噪声:登录
    {"method": "POST", "url": "http://oa.x/prod-api/captcha", "post_data": '{"code":"1"}'},    # 噪声
    {"method": "POST", "url": "http://oa.x/prod-api/oa/leave/start", "post_data": _SUBMIT},     # 真提交
]


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
    assert as_list_payload({"code": 200, "msg": "ok"}) is None              # 无列表
    assert as_list_payload([]) is None                                      # 空列表无意义
    assert as_list_payload("x") is None


def test_list_read_requests_surfaces_select_candidates():
    """P2:从读响应挑出「选领导」这类候选源,给出条数 + 列表项字段名(供 P3 绑定 label/value)。"""
    reads = [
        {"url": "http://oa.x/prod-api/system/user/list",
         "json": {"rows": [{"userId": 12, "nickName": "张经理", "dept": "研发"},
                           {"userId": 34, "nickName": "李总", "dept": "行政"}]}},
        {"url": "http://oa.x/prod-api/getInfo", "json": {"code": 200}},     # 非列表 → 不入选
    ]
    cands = list_read_requests(reads)
    assert len(cands) == 1
    assert cands[0]["url"].endswith("/system/user/list") and cands[0]["count"] == 2
    assert "userId" in cands[0]["item_keys"] and "nickName" in cands[0]["item_keys"]


def test_suggest_selects_binds_field_to_list_source():
    """Q2 选领导:提交体 approverId=12 命中 user/list 里 userId=12 → 建议 select(value=userId,label=nickName)。"""
    submit = '{"reason":"回家","approverId":12,"leaveType":"事假"}'
    reads = [{"url": "http://oa.x/prod-api/system/user/list",
              "json": {"rows": [{"userId": 12, "nickName": "张经理", "deptName": "研发"},
                                {"userId": 34, "nickName": "李总"}]}}]
    s = suggest_selects(submit, reads)
    assert len(s) == 1
    b = s[0]
    assert b["path"] == "approverId" and b["value_key"] == "userId"
    assert b["label_key"] == "nickName" and b["label"] == "张经理"
    assert b["source_url"].endswith("/system/user/list") and b["count"] == 2


def test_suggest_selects_code_dropdown_via_small_dict():
    """代码型下拉:type=2 命中字典小列表 dictValue=2 → 绑 select,agent 传"病假"、运行期换 2。"""
    submit = '{"type":2,"reason":"回家"}'
    dict_read = [{"url": "http://oa.x/system/dict/data/type/leave_type",
                  "json": {"code": 200, "data": [{"dictLabel": "事假", "dictValue": "1"},
                                                 {"dictLabel": "病假", "dictValue": "2"},
                                                 {"dictLabel": "年假", "dictValue": "3"}]}}]
    s = suggest_selects(submit, dict_read)
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
    s = suggest_selects(submit, read)
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
    s = suggest_selects(sub, read)
    assert len(s) == 1
    assert s[0]["value_key"] == "type" and s[0]["label_key"] == "name" and s[0]["label"] == "病假"


def test_suggest_selects_rejects_id_only_list_without_label():
    """只有 ID、没有名字/文字字段的列表不绑(没名字可传 → 不是名字→ID 下拉,防误绑)。"""
    sub = '{"x": "AAAA"}'
    read = [{"url": "/d", "json": {"rows": [{"value": "AAAA"}, {"value": "BBBB"}]}}]
    assert suggest_selects(sub, read) == []


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
    s = suggest_selects(sub, read, {"请假类型": "病假", "原因": "x"})    # 录制选了"病假" → 确认命中
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
    assert apir["selects"] == [{"param": "approver", "source_url": "/system/user/list",
                                "value_key": "userId", "label_key": "nickName"}]
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
    """录到"我的记录"列表(含刚提交的原因)→ 回查源:endpoint + match_field + param。"""
    samples = {"原因": "去北京出差三天", "类型": "事假"}
    reads = [{"url": "http://oa.x/leave/list",
              "json": {"rows": [{"id": 9, "reason": "去北京出差三天", "status": "审批中"}]}}]
    fc = suggest_fact_check(samples, reads)
    assert fc == {"endpoint": "http://oa.x/leave/list", "match_field": "reason", "param": "原因"}


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


def test_build_api_workflow_assembles_steps_links_and_last_params():
    """组装多步:参数落最后一步;步链(taskId)自动挂到目标步;前置步是常量。"""
    writes = [
        {"method": "POST", "url": "http://oa.x/flow/start", "post_data": '{"procDefKey":"oa_leave"}',
         "response_json": {"data": {"taskId": "TASK-5566"}}},
        {"method": "POST", "url": "http://oa.x/flow/submit",
         "post_data": '{"taskId":"TASK-5566","reason":"回家"}', "response_json": {"code": 200}},
    ]
    wf = build_api_workflow(writes, param_map={"reason": "reason"})
    assert len(wf["steps"]) == 2
    assert wf["steps"][0]["body_template"] == {"procDefKey": "oa_leave"}     # 前置步全常量
    assert wf["steps"][1]["body_template"]["reason"] == "{{reason}}"          # 最后一步带用户参数
    assert wf["steps"][1]["params"] == ["reason"]
    assert wf["steps"][1]["links"] == [{"target_path": "taskId", "target_tokens": ["taskId"],
                                        "source_step": 0, "source_path": "data.taskId",
                                        "source_tokens": ["data", "taskId"]}]


async def test_execute_api_dispatches_single_and_workflow():
    """execute_api:无 steps → 单请求(dry);有 steps → 工作流(dry)。"""
    single = {"body_template": {"x": "{{a}}"}, "params": ["a"]}
    out1 = await execute_api(single, {"a": "1"}, send=False)
    assert out1["ok"] and out1.get("dry")
    wf = {"steps": [{"body_template": {"x": "{{a}}"}, "params": ["a"]}]}
    out2 = await execute_api(wf, {"a": "1"}, send=False)
    assert out2["ok"] and out2["steps"] == 1


def test_pick_submit_skips_noise_and_picks_by_value_match():
    req = pick_submit_request(_REQUESTS, _SAMPLES)
    assert req["url"].endswith("/oa/leave/start")          # 含最多用户填的值的写请求,跳过 login/captcha


def test_parameterize_user_values_keep_internal_constants():
    req = pick_submit_request(_REQUESTS, _SAMPLES)
    p = parameterize_request(req, _SAMPLES, base_url="http://oa.x/prod-api")
    assert p["method"] == "POST" and p["path"] == "/oa/leave/start"
    assert set(p["params"]) == {"请假类型", "开始时间", "结束时间", "原因"}   # 4 个填的值都成参数
    assert p["body_template"]["leaveType"] == "{{请假类型}}"
    assert p["body_template"]["reason"] == "{{原因}}"
    assert p["body_template"]["procDefId"] == "PROC123"    # 内部 ID 保持常量
    assert p["body_template"]["draft"] is False            # 布尔常量不动


def test_substitute_fills_params_at_runtime():
    req = pick_submit_request(_REQUESTS, _SAMPLES)
    p = parameterize_request(req, _SAMPLES, base_url="http://oa.x/prod-api")
    body = substitute(p["body_template"], {"请假类型": "病假", "开始时间": "2026-07-01",
                                           "结束时间": "2026-07-02", "原因": "感冒"})
    assert body["leaveType"] == "病假" and body["reason"] == "感冒"
    assert body["procDefId"] == "PROC123" and body["draft"] is False   # 常量原样


def test_substitute_falls_back_to_recorded_default():
    """全选安全网:agent 没传的字段 → 用录制原值(defaults),不留空占位、固定字段不变。"""
    tmpl = {"reason": "{{原因}}", "billType": "{{billType}}", "leaveType": "{{请假类型}}"}
    defaults = {"原因": "录制原因", "billType": "oa_duty_leave", "请假类型": "事假"}
    body = substitute(tmpl, {"原因": "感冒"}, defaults)        # 只传了原因
    assert body["reason"] == "感冒"                            # 传了 → 用新值
    assert body["billType"] == "oa_duty_leave"                # 没传 → 用录制原值(固定字段不变)
    assert body["leaveType"] == "事假"                         # 没传 → 录制原值


def test_non_json_body_returns_none():
    # 纯文本(非 JSON、非表单)→ None;form-urlencoded 现已支持,见 test_parse_body_form_urlencoded
    assert parameterize_request({"method": "POST", "url": "/x", "post_data": "plain text no kv"}, _SAMPLES) is None


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
    t = {f["key"]: f["type"] for f in flatten_body(body)}
    assert t["reason"] == "string"
    assert t["amount"] == "number" and t["days"] == "number"
    assert t["startTime"] == "datetime"        # 13 位毫秒 + 时间类 key
    assert t["checkin"] == "date"              # YYYY-MM-DD 字符串
    assert t["draft"] == "boolean"


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


async def test_capture_submit_request_e2e():
    pytest.importorskip("playwright")
    from dano.execution.page.driver import PlaywrightPageDriver
    from dano.execution.page.recorder import RecordSession
    try:
        d, _ = await PlaywrightPageDriver.launch(headless=True); await d.close()
    except Exception:  # noqa: BLE001
        pytest.skip("chromium 未安装")

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        sess = RecordSession()
        await sess.start(f"http://127.0.0.1:{port}/")
        await sess.page.fill("#reason", "大地色多")
        await sess.page.click("#submit")               # JS fetch POST → 抓到提交请求
        await sess.page.wait_for_timeout(500)
        reqs = sess.captured_requests()
        await sess.stop()
    finally:
        httpd.shutdown()

    req = pick_submit_request(reqs, {"原因": "大地色多"})
    assert req is not None and req["url"].endswith("/prod-api/oa/leave/start")
    p = parameterize_request(req, {"原因": "大地色多"}, base_url=f"http://127.0.0.1:{port}/prod-api")
    assert p["method"] == "POST" and p["path"] == "/oa/leave/start"
    assert p["body_template"]["reason"] == "{{原因}}"      # 用户填的值→参数
    assert p["body_template"]["procDefId"] == "P1"        # 内部常量保留


async def test_capture_reads_e2e():
    """P2 真浏览器:页面加载时拉的「选领导」列表(GET+JSON 数组)被抓为 read 候选源(给 Q2 的 select 用)。"""
    pytest.importorskip("playwright")
    from dano.execution.page.driver import PlaywrightPageDriver
    from dano.execution.page.recorder import RecordSession
    from dano.execution.page.request_capture import list_read_requests
    try:
        d, _ = await PlaywrightPageDriver.launch(headless=True); await d.close()
    except Exception:  # noqa: BLE001
        pytest.skip("chromium 未安装")

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        sess = RecordSession()
        await sess.start(f"http://127.0.0.1:{port}/")
        await sess.page.wait_for_timeout(700)          # 等页面 load 时的 GET 列表回来
        reads = sess.captured_reads()
        await sess.stop()
    finally:
        httpd.shutdown()

    cands = list_read_requests(reads)
    leaders = [c for c in cands if c["url"].endswith("/system/user/list")]
    assert leaders and leaders[0]["count"] == 2                     # 抓到 2 人的候选列表
    assert "userId" in leaders[0]["item_keys"] and "nickName" in leaders[0]["item_keys"]  # 供 P3 绑 value/label


async def test_recorder_captures_required_star_elementui():
    """真浏览器:Element-UI 结构(el-form-item.is-required + label[for])→ 录制捕获 * 必填 + 中文标签。"""
    pytest.importorskip("playwright")
    from dano.execution.page.driver import PlaywrightPageDriver
    from dano.execution.page.recorder import RecordSession
    try:
        d, _ = await PlaywrightPageDriver.launch(headless=True); await d.close()
    except Exception:  # noqa: BLE001
        pytest.skip("chromium 未安装")
    html = ('<!doctype html><html><head><meta charset="utf-8"></head><body><form>'
            '<div class="el-form-item is-required"><label for="dest">目的地</label><input id="dest"></div>'
            '<div class="el-form-item"><label for="remark">备注</label><input id="remark"></div>'
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
        await sess.stop()
    finally:
        httpd.shutdown()
    assert "目的地" in req_labels        # is-required → 必填
    assert "备注" not in req_labels      # 无 is-required → 非必填


async def test_request_onboarding_publish_and_execute(tmp_path):
    """端到端:抓提交请求 → 发布成 Skill → 真发(新参数值,服务器回显验证)。PG+chromium 门控。"""
    pytest.importorskip("playwright")
    pytest.importorskip("asyncpg")
    import socketserver as _ss
    import threading as _th
    from uuid import uuid4

    from dano.assets.repository import AssetRepository
    from dano.execution.page.driver import PlaywrightPageDriver
    from dano.execution.page.recorder import RecordSession
    from dano.execution.page.request_capture import execute_api_request
    from dano.infra.db import close_pool, get_pool, init_pool
    from dano.onboarding.page_onboard import run_request_onboarding
    from dano.orchestrator.skills import SkillRegistry
    from dano.shared.enums import Subsystem

    try:
        await init_pool()
    except Exception:  # noqa: BLE001
        pytest.skip("PG 不可用")
    try:
        d, _ = await PlaywrightPageDriver.launch(headless=True); await d.close()
    except Exception:  # noqa: BLE001
        await close_pool(); pytest.skip("chromium 不可用")

    httpd = _ss.TCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    _th.Thread(target=httpd.serve_forever, daemon=True).start()
    from dano.agent_tools import tools as _T
    _T.set_review_board(_FakeBoard())                       # 录制抓请求免评审;留 fake 板作安全网(绝不触真 LLM)
    tenant = f"req-e2e-{uuid4().hex[:8]}"
    sid = Subsystem.REIMBURSE.value
    try:
        sess = RecordSession()
        await sess.start(f"http://127.0.0.1:{port}/")
        await sess.page.fill("#reason", "大地色多")
        await sess.page.click("#submit")
        await sess.page.wait_for_timeout(500)
        reqs = sess.captured_requests()
        await sess.stop()

        req = pick_submit_request(reqs, {"原因": "大地色多"})
        apir = parameterize_request(req, {"原因": "大地色多"})
        assert apir["body_template"]["reason"] == "{{原因}}"

        rep = await run_request_onboarding(tenant=tenant, subsystem=sid, action="submit_leave",
                                           title="请假", api_request=apir,
                                           sample_inputs=apir["sample_inputs"])
        assert rep["ok"] is True, rep                       # 发布成功(录制抓请求免三模型评审 + self_check)
        assert rep["status"] == "partially_verified"        # capture dry-only:结构已验、活体未验(诚实降级)

        reg = await SkillRegistry.from_store(AssetRepository(), tenant=tenant,
                                             subsystems=[Subsystem.REIMBURSE])
        sk = reg.by_action(Subsystem.REIMBURSE, "submit_leave")
        # 参数都带录制原值兜底 → 都是可选(required 空),原因在 optional/user_fields 里
        assert sk is not None and sk.has_api is False
        assert "原因" in (sk.optional_fields + sk.required_fields)

        # 真发:传新参数值 → 服务器回显应是新值(证明参数化+替换+真发整条通)
        out = await execute_api_request(apir, {"原因": "感冒"}, send=True, verify=False)
        assert out["ok"] and out["status"] == 200
        assert out["response"]["echo"]["reason"] == "感冒"
    finally:
        _T.set_review_board(None)
        httpd.shutdown()
        async with get_pool().acquire() as c:
            await c.execute("DELETE FROM asset_drafts WHERE tenant=$1", tenant)
            await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        await close_pool()


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


async def test_dry_replay_fails_on_self_check():
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


def test_workflow_step_link_taskid_chains_via_tokens():
    """Q3:多步串联带 tokens;link 目标路径可达 → self_check 通过。"""
    writes = [
        {"method": "POST", "url": "http://oa.x/flow/start",
         "post_data": '{"procDefKey":"oa_leave"}', "response_json": {"code": 200, "data": {"taskId": "TASK-5566"}}},
        {"method": "POST", "url": "http://oa.x/flow/submit",
         "post_data": '{"flowTask":{"taskId":"TASK-5566"},"reason":"回家"}', "response_json": {"code": 200}},
    ]
    wf = build_api_workflow(writes, param_map={"reason": "reason"})
    assert wf["steps"][1]["links"][0]["target_tokens"] == ["flowTask", "taskId"]
    assert self_check(wf) == []


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


def test_test_data_tag():
    from dano.execution.page.request_capture import test_data_tag
    assert test_data_tag("run-20260625-001") == "[DANO-TEST-run-20260625-001]"


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
        async with get_pool().acquire() as c:
            await c.execute("DELETE FROM asset_drafts WHERE tenant=$1", tenant)
            await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        srv.shutdown()
        await close_pool()


# ─────────── P3:LLM 字段语义增强(只补确定性没把握的字段,有把握的不覆盖) ───────────
async def test_suggest_field_names_llm_only_unnamed_and_redacts():
    """只把"名字仍=key"的字段送 LLM 命名;且只喂机器名/类型/路径,不带值。"""
    from dano.review.board import suggest_field_names_llm
    fake = _FakeChat({"names": {"applicantId": "申请人", "leaveType": "请假类型"}})
    fields = [
        {"key": "reason", "suggest_name": "原因", "type": "string", "path": "reason", "value": "回家"},  # 已确信
        {"key": "applicantId", "suggest_name": "applicantId", "type": "string", "path": "applicantId", "value": "118"},
        {"key": "leaveType", "suggest_name": "leaveType", "type": "string", "path": "leaveType", "value": "事假"},
    ]
    names = await suggest_field_names_llm(fake, "m", action="submit", fields=fields)
    assert names == {"applicantId": "申请人", "leaveType": "请假类型"}
    # 只送了没命名的两个;确信的 reason 不送;且 user 里没有值"回家"/"118"
    assert "applicantId" in fake.seen["user"] and "leaveType" in fake.seen["user"]
    assert "回家" not in fake.seen["user"] and "118" not in fake.seen["user"]


async def test_suggest_field_names_llm_safe_degrade():
    from dano.review.board import suggest_field_names_llm
    assert await suggest_field_names_llm(None, "m", action="a", fields=[]) == {}
    # 所有字段都已命名 → 不调 LLM
    named = [{"key": "reason", "suggest_name": "原因"}]
    assert await suggest_field_names_llm(_FakeChat({"names": {"x": "y"}}), "m", action="a", fields=named) == {}


def test_merge_llm_field_names_fills_only_keyfallback():
    """LLM 名只补到 suggest_name==key 的字段;确信的 DOM 标签名不被覆盖。"""
    from dano.execution.page.request_capture import merge_llm_field_names
    fields = [
        {"key": "reason", "suggest_name": "原因"},                  # 确信 → 不动
        {"key": "applicantId", "suggest_name": "applicantId"},      # key 兜底 → 补
        {"key": "Activity_09dlq0g", "suggest_name": "Activity_09dlq0g"},  # LLM 也没给 → 保持
    ]
    merge_llm_field_names(fields, {"applicantId": "申请人"})
    by = {f["key"]: f for f in fields}
    assert by["reason"]["suggest_name"] == "原因" and "name_source" not in by["reason"]
    assert by["applicantId"]["suggest_name"] == "申请人" and by["applicantId"]["name_source"] == "llm"
    assert by["Activity_09dlq0g"]["suggest_name"] == "Activity_09dlq0g"   # 没补,保持原 key


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


async def test_onboarding_rejects_dangerous_write():
    """业务相关性门:DELETE/驳回类写请求 → rejected(发布前 return,不连库)。"""
    from dano.onboarding.page_onboard import run_request_onboarding
    out = await run_request_onboarding(tenant="t-x", subsystem="reimburse", action="del",
                                       api_request={"method": "DELETE", "url": "http://x/order/1",
                                                    "body_template": {"id": 1}, "params": []})
    assert out["ok"] is False and out["status"] == "rejected"


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
    assert sub["semanticRole"] == "workflow_submit" and sub["riskLevel"] == "L3"
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
