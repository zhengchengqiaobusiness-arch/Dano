"""方式B 升级:抓提交请求 → 参数化(纯函数,离线)。"""
from __future__ import annotations

from dano.execution.page.request_capture import (
    build_api_request,
    extract_auth_headers,
    flatten_body,
    json_write_requests,
    parameterize_request,
    pick_submit_request,
    substitute,
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
    assert paths["form.reason"]["suggest_name"] == "原因"
    assert paths["form.leaveType"]["suggest_param"] is True        # 像用户数据(非 ID/常量)
    assert paths["variables.procInstId"]["suggest_param"] is False  # 雪花 id → 默认不勾
    assert paths["variables.tenantId"]["suggest_param"] is False    # key 以 id 结尾 → 默认不勾
    assert paths["draft"]["suggest_param"] is False                # 布尔常量 → 不勾


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
         b'\xe6\x8f\x90\xe4\xba\xa4</button></body></html>')


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN001 —— 静默
        pass

    def do_GET(self):
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
        assert sk is not None and sk.has_api is False and "原因" in sk.required_fields

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
