"""P0-2:网络请求角色分类(classify_network_request) + all_requests 自动分类。

验收点(按 P0-2 方案):
1. 登录接口 → role=auth / keep=False
2. 下拉列表 GET → role=read_option / keep=False
3. getappid 业务 GET → role=business_get / keep=True
4. sjws_chat 业务写 → role=business_write 或 submit_anchor / keep=True
5. 危险 DELETE → role=business_write / keep=True（风险在执行/发布层审核）
6. 静态资源 → role=noise / keep=False
7. submit 锚点路径段 → role=submit_anchor / keep=True
8. all_requests 每条都带 role/keep/reason/confidence 字段
"""

from __future__ import annotations

from dano.execution.page.recorder import RecordSession
from dano.execution.page.request_capture import classify_network_request


# ── 1. classify_network_request 纯函数 ──
def test_login_post_is_auth():
    cls = classify_network_request({"method": "POST", "url": "https://x/api/login",
                                    "post_data": '{"u":"a","p":"b"}'})
    assert cls["role"] == "auth"
    assert cls["keep"] is False
    assert cls["confidence"] >= 0.9


def test_static_png_is_noise():
    cls = classify_network_request({"method": "GET", "url": "https://x/logo.png"})
    assert cls["role"] == "noise"
    assert cls["keep"] is False


def test_sse_is_noise():
    cls = classify_network_request({"method": "GET", "url": "https://x/api/events/sse"})
    assert cls["role"] == "noise"
    assert cls["keep"] is False


def test_dangerous_delete_is_preserved_as_business_write():
    cls = classify_network_request({"method": "DELETE", "url": "https://x/api/leave/123"})
    assert cls["role"] == "business_write"
    assert cls["keep"] is True
    assert cls["confidence"] >= 0.9


def test_delete_keyword_path_is_preserved_as_business_write():
    """URL 路径段含 delete/remove/reject 等也保留事实，安全层仍可按 destructive/L4 审核。"""
    cls = classify_network_request({"method": "POST", "url": "https://x/api/leave/reject/123",
                                    "post_data": '{"reason":"x"}'})
    assert cls["role"] == "business_write"
    assert cls["keep"] is True


def test_list_get_is_read_option():
    cls = classify_network_request({
        "method": "GET", "url": "https://x/api/users/list?dept=1",
        "response_json": {"rows": [{"id": 1, "name": "张三"}]},
    })
    assert cls["role"] == "read_option"
    assert cls["keep"] is False
    assert "下拉/选人/字典源" in cls["reason"] or "列表" in cls["reason"]


def test_dict_get_response_is_read_option():
    cls = classify_network_request({
        "method": "GET", "url": "https://x/api/users/list",
        "response_json": {"data": [{"id": 1, "name": "张三"}]},
    })
    assert cls["role"] == "read_option"


def test_daily_report_page_is_business_query_not_option_list():
    cls = classify_network_request({
        "method": "GET",
        "url": "https://x/api/daily-report/page?start=2026-05-01&end=2026-05-31",
        "response_json": {"data": {"list": [
            {"date": "2026-05-01", "content": "开发", "status": "submitted"},
        ]}},
    })

    assert cls["role"] == "business_get"
    assert cls["keep"] is True
    assert cls["confidence"] >= 0.9


def test_getappid_business_get():
    """getappid 返回单值对象 → business_get,保留为主流程候选(P0-3 依赖闭包基于此)。"""
    cls = classify_network_request({
        "method": "GET", "url": "https://x/apigateway/getappid?appId=auto",
        "response_json": {"code": 200, "data": "app-code"},
    })
    assert cls["role"] == "business_get"
    assert cls["keep"] is True
    assert cls["confidence"] >= 0.7


def test_submit_anchor_path():
    cls = classify_network_request({
        "method": "POST", "url": "https://x/dataiq/sjws_chat",
        "post_data": '{"sys_query":"q"}',
        "response_json": {"code": 200},
    })
    assert cls["role"] in ("submit_anchor", "business_write")
    assert cls["keep"] is True


def test_save_chat_list_submit_anchor():
    cls = classify_network_request({
        "method": "POST", "url": "https://x/dataiq/save_dataiq_chat_list",
        "post_data": '{"name":"x"}',
        "response_json": {"code": 200, "data": {"conversation_id": "c-1"}},
    })
    assert cls["role"] in ("submit_anchor", "business_write")
    assert cls["keep"] is True


def test_post_query_list_is_read_option():
    """POST 路径动词是 getXxxList/queryXxx → read_option(下拉源,不当业务写)。"""
    cls = classify_network_request({
        "method": "POST", "url": "https://x/api/getUserList",
        "post_data": '{"dept":1}',
        "response_json": {"rows": [{"id": 1}]},
    })
    assert cls["role"] == "read_option"
    assert cls["keep"] is False


def test_unknown_get_no_response():
    """GET 但 response 没落地 → read_context(低置信,等 P0-3 依赖闭包裁决)。"""
    cls = classify_network_request({
        "method": "GET", "url": "https://x/api/something",
    })
    assert cls["role"] == "read_context"
    assert cls["keep"] is False
    assert cls["confidence"] < 0.7


def test_classify_returns_required_keys():
    """P0-2 约定输出 schema:role / keep / reason / confidence 四键齐全。"""
    cls = classify_network_request({"method": "GET", "url": "https://x/api/x"})
    assert set(cls) >= {"role", "keep", "reason", "confidence"}
    assert isinstance(cls["keep"], bool)
    assert isinstance(cls["confidence"], (int, float))
    assert 0.0 <= cls["confidence"] <= 1.0


# ── 2. all_requests 自动分类 ──
def _new_sess():
    return RecordSession()


def test_all_requests_has_role_keep_reason_confidence():
    s = _new_sess()
    s._record_all("POST", "https://x/dataiq/save_dataiq_chat_list", pd='{"name":"x"}')
    s._record_all("GET", "https://x/apigateway/getappid?appId=auto")
    s._record_all("GET", "https://x/logo.png")
    s._record_all("DELETE", "https://x/api/leave/123")
    s._record_all("POST", "https://x/api/login", pd='{"u":"a","p":"b"}')
    cap = s.captured_all_requests()
    # 每条都带四个分类字段
    for r in cap:
        assert "role" in r, f"role 缺失: {r}"
        assert "keep" in r, f"keep 缺失: {r}"
        assert "reason" in r, f"reason 缺失: {r}"
        assert "confidence" in r, f"confidence 缺失: {r}"


def test_dataiq_three_requests_classified():
    """dataiq 三接口(role 分类符合预期):
    - save_dataiq_chat_list POST → submit_anchor / business_write
    - getappid GET 返回对象 → business_get
    - sjws_chat POST → submit_anchor / business_write
    """
    s = _new_sess()
    s._record_all("POST", "https://x/dataiq/save_dataiq_chat_list",
                  pd='{"user_id":"u1","name":"t"}',
                  response_json={"code": 200, "data": {"conversation_id": "c-1"}},
                  status=200, content_type="application/json")
    s._record_all("GET", "https://x/apigateway/getappid?appId=auto&appName=auto",
                  response_json={"code": 200, "data": "app-code"},
                  status=200, content_type="application/json")
    s._record_all("POST", "https://x/dataiq/sjws_chat",
                  pd='{"sys_query":"q","conversation_id":"c-1","appCode":"app-code"}',
                  response_json={"code": 200}, status=200, content_type="application/json")
    cap = s.captured_all_requests()
    by_url = {r["url"]: r for r in cap}
    # save_dataiq_chat_list + sjws_chat 是写
    assert by_url["https://x/dataiq/save_dataiq_chat_list"]["keep"] is True
    assert by_url["https://x/dataiq/sjws_chat"]["keep"] is True
    # getappid 是业务 GET
    getappid = by_url["https://x/apigateway/getappid?appId=auto&appName=auto"]
    assert getappid["role"] == "business_get"
    assert getappid["keep"] is True


def test_response_reclassification_changes_role():
    """孤立列表即使响应落地，也不能在没有候选因果时被提升为下拉源。"""
    s = _new_sess()
    # 1) 先 _record_all 时无响应 → role 兜底为 read_context
    s._record_all("GET", "https://x/api/foo")
    entry = s.all_requests[0]
    assert entry["role"] == "read_context"
    # 2) 响应落地后仍是 read_context；列表结构本身不是候选源证据。
    s._attach_response(url="https://x/api/foo", method="GET",
                       response_json={"rows": [{"id": 1, "name": "张三"}]},
                       status=200, content_type="application/json")
    assert entry["role"] == "read_context"


def test_tenant_simple_list_with_status_field_remains_option_source():
    """通用管理列表带 status/createTime，不等于业务状态查询能力。"""
    s = _new_sess()
    s._record_all(
        "GET",
        "https://x/admin-api/system/tenant/simple-list",
        response_json={"data": [{"id": 1, "name": "默认租户", "status": 0, "createTime": 1}]},
        status=200,
        content_type="application/json",
    )

    entry = s.captured_all_requests()[0]
    assert entry["role"] == "read_option"
    assert entry["keep"] is False


def test_keep_filter_drops_noise_and_auth():
    """all_requests 保留全量,但 keep=True 的是主流程候选。"""
    s = _new_sess()
    s._record_all("POST", "https://x/api/login", pd='{"u":"a"}')                 # auth
    s._record_all("GET", "https://x/logo.png")                                    # noise
    s._record_all("GET", "https://x/api/users/list",
                  response_json={"rows": [{"id": 1}]})                          # read_option
    s._record_all("POST", "https://x/dataiq/save_dataiq_chat_list",
                  pd='{"name":"x"}',
                  response_json={"code": 200})                                   # submit_anchor
    s._record_all("DELETE", "https://x/api/leave/123")                           # destructive
    cap = s.captured_all_requests()
    keeps = [r["url"] for r in cap if r["keep"]]
    # 主流程只留业务写
    assert any("save_dataiq_chat_list" in u for u in keeps), keeps
    # 噪声/登录/列表源不进主流程；危险业务操作不能在录制层丢失。
    assert not any("login" in u for u in keeps), keeps
    assert not any("logo.png" in u for u in keeps), keeps
    assert not any("users/list" in u for u in keeps), keeps
    assert any("leave/123" in u for u in keeps), keeps


def test_reason_is_human_readable():
    """reason 是给人看的一句话解释,不能是技术黑话。"""
    s = _new_sess()
    s._record_all("POST", "https://x/dataiq/sjws_chat",
                  pd='{"sys_query":"q"}', response_json={"code": 200})
    cap = s.captured_all_requests()
    for r in cap:
        assert r["reason"], f"reason 不能为空: {r}"
        # 中文/英文都可,但要 > 5 字符(避免单字噪声)
        assert len(r["reason"]) >= 5
