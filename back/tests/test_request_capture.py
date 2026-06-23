"""方式B 升级:抓提交请求 → 参数化(纯函数,离线)。"""
from __future__ import annotations

from dano.execution.page.request_capture import (
    as_list_payload,
    build_api_request,
    build_api_workflow,
    discover_step_links,
    execute_api,
    execute_api_request,
    execute_api_workflow,
    extract_auth_headers,
    flatten_body,
    json_write_requests,
    list_read_requests,
    parameterize_request,
    pick_submit_request,
    resolve_identity_value,
    substitute,
    suggest_identity,
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
    assert apir["identity"] == [{"path": "applicantId", "source": "localStorage:userInfo.userId"}]


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


def test_discover_step_links_finds_taskid_chain():
    """Q3:第2步 body 的 taskId 来自第1步响应 data.taskId → 自动发现 step 链。"""
    writes = [
        {"post_data": '{"leaveType":"事假"}', "response_json": {"code": 200, "data": {"taskId": "TASK-99887"}}},
        {"post_data": '{"flowTask":{"taskId":"TASK-99887","comment":"同意"}}', "response_json": {"code": 200}},
    ]
    links = discover_step_links(writes)
    assert links == [{"target_step": 1, "target_path": "flowTask.taskId",
                      "source_step": 0, "source_path": "data.taskId"}]


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
    assert wf["steps"][1]["links"] == [{"target_path": "taskId", "source_step": 0, "source_path": "data.taskId"}]


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
    assert parameterize_request({"method": "POST", "url": "/x", "post_data": "a=1&b=2"}, _SAMPLES) is None


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


def test_flatten_order_fallback_labels_dropdown():
    """下拉代码值对不上文本,但"剩下的标签"按顺序补:请假类型→type(用户录了该字段时)。"""
    body = '{"type":2,"reason":"回家"}'
    samples = {"原因": "回家", "请假类型": "事假"}     # 用户录了这两个字段
    p = {f["key"]: f["suggest_name"] for f in flatten_body(body, samples)}
    assert p["reason"] == "原因"            # 文本直接对
    assert p["type"] == "请假类型"          # 值对不上(2≠事假),但剩下的标签「请假类型」按序补给它


def test_flatten_body_non_json_returns_empty():
    assert flatten_body("a=1&b=2") == []
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
        assert rep["ok"] is True, rep                       # 发布成功(免评审,dry 校验过)

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
        httpd.shutdown()
        async with get_pool().acquire() as c:
            await c.execute("DELETE FROM asset_drafts WHERE tenant=$1", tenant)
            await c.execute("DELETE FROM assets WHERE tenant=$1", tenant)
        await close_pool()
