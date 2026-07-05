"""Step A · FlowSpec 收敛函数测试。"""

from __future__ import annotations

import json
import unittest

from dano.execution.page.flow_spec import (
    FlowSpec, FlowStep, FlowLink, ParamField,
    apply_flow_edits,
    apply_flow_publish_selection,
    apply_llm_field_names,
    classify_network_request,
    dry_run_flow_spec,
    flow_spec_to_api_request,
    flow_spec_to_client,
    flow_spec_to_summary,
    llm_field_name_candidates,
    render_business_description,
    to_flow_spec,
    validate_flow_spec,
    _default_step_name, _derive_step_name, _infer_type_from_value,
    _params_from_get_query, _is_business_get,
)


def _post(url, body, method="POST", resp=None, headers=None):
    return {
        "method": method, "url": url,
        "post_data": json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else body,
        "content_type": "application/json",
        "headers": headers or {"Authorization": "Bearer test", "Content-Type": "application/json"},
        "response_json": resp,
    }


class ToFlowSpecTest(unittest.TestCase):

    def test_single_submit_step(self):
        captured = [_post(
            "https://oa/api/leave/submit",
            {"leaveType": "事假", "days": 3, "reason": "回家", "userId": 12345},
            resp={"code": 200, "data": {"taskId": "T-999"}},
        )]
        samples = {"leaveType": "事假", "days": "3", "reason": "回家"}
        spec = to_flow_spec(
            captured_requests=captured, reads=[], samples=samples,
            storage_state={"origins": [{"localStorage": [{"name": "userInfo", "value": json.dumps({"userId": 12345})}]}]},
            required_labels={"leaveType", "days", "reason"},
        )
        self.assertIsInstance(spec, FlowSpec)
        self.assertEqual(len(spec.steps), 1)
        st = spec.steps[0]
        self.assertEqual(st.method, "POST")
        self.assertIn("/api/leave/submit", st.path)
        paths = {p.path for p in st.params}
        self.assertIn("leaveType", paths)
        self.assertIn("days", paths)
        self.assertIn("reason", paths)
        iden_paths = {i.path for i in st.identity}
        self.assertIn("userId", iden_paths)
        by_path = {p.path: p for p in st.params}
        self.assertEqual(by_path["userId"].category, "runtime_var")
        self.assertEqual(by_path["userId"].source_kind, "current_user")
        self.assertFalse(by_path["userId"].exposed_to_user)
        self.assertEqual(by_path["reason"].category, "user_param")
        self.assertEqual(by_path["reason"].source_kind, "user_input")
        self.assertTrue(by_path["reason"].exposed_to_user)
        self.assertIsNotNone(st.success_rule)
        self.assertEqual(spec.risk_level, "L3")
        self.assertEqual(spec.links, [])
        self.assertEqual(spec.meta.get("current_version"), 1)
        self.assertEqual(spec.meta["versions"][0]["action"], "recorded")

    def test_multi_step_workflow_with_link(self):
        captured = [
            _post("https://oa/api/leave/start", {"flowType": "leave"},
                  resp={"code": 200, "data": {"taskId": "T-777"}}),
            _post("https://oa/api/leave/submit",
                  {"flowTask": {"taskId": "T-777"}, "applicant": "张三", "type": "事假", "applicantId": 12345},
                  resp={"code": 200, "data": {"processId": "P-1"}}),
        ]
        samples = {"applicant": "张三", "type": "事假"}
        storage_state = {"origins": [{"localStorage": [
            {"name": "userInfo", "value": json.dumps({"userId": 12345})}
        ]}]}
        spec = to_flow_spec(captured, reads=[], samples=samples, storage_state=storage_state)
        self.assertEqual(len(spec.steps), 2)
        self.assertEqual(len(spec.links), 1, "应自动探测到 taskId 串联")
        lk = spec.links[0]
        self.assertEqual(lk.source_step_id, spec.steps[0].step_id)
        self.assertEqual(lk.target_step_id, spec.steps[1].step_id)
        self.assertFalse(lk.confirmed)
        p_by_path = {p.path: p for p in spec.steps[1].params}
        self.assertEqual(p_by_path["flowTask.taskId"].category, "runtime_var")
        self.assertEqual(p_by_path["flowTask.taskId"].source_kind, "previous_response")
        self.assertEqual(p_by_path["flowTask.taskId"].source["step_id"], spec.steps[0].step_id)
        self.assertFalse(p_by_path["flowTask.taskId"].exposed_to_user)
        review_types = {item.type for item in spec.review_items}
        self.assertIn("link_confirmation", review_types)
        self.assertIn("field_category", review_types)
        report = validate_flow_spec(spec)
        self.assertGreaterEqual(report["review_summary"]["total"], 2)
        self.assertTrue(any(i["type"] == "link_confirmation" for i in report["review_items"]))

        broken = apply_flow_edits(spec, [{
            "op": "update",
            "link_id": lk.link_id,
            "field": "source_path",
            "value": "data.missing",
        }])
        broken_report = validate_flow_spec(broken)
        self.assertFalse(broken_report["passed"])
        self.assertTrue(any(i["type"] == "link_source_missing" for i in broken_report["review_items"]))
        self.assertTrue(any("来源路径" in e and "data.missing" in e for e in broken_report["errors"]))

        confirmed = apply_flow_edits(spec, [{
            "op": "update",
            "link_id": lk.link_id,
            "field": "confirmed",
            "value": True,
        }])
        confirmed_types = {item.type for item in confirmed.review_items}
        self.assertNotIn("link_confirmation", confirmed_types)
        confirmed_param = {p.path: p for p in confirmed.steps[1].params}["flowTask.taskId"]
        self.assertEqual(confirmed_param.source_kind, "previous_response")
        self.assertFalse(confirmed_param.need_human_confirm)

    def test_no_business_writes_returns_empty_spec(self):
        spec = to_flow_spec(captured_requests=[_post("https://oa/api/login", {"u": "x", "p": "y"})], reads=[])
        self.assertEqual(len(spec.steps), 0)
        self.assertIn("未捕获", spec.title)

    def test_auth_requests_filtered_out(self):
        captured = [
            _post("https://oa/api/login", {"username": "u", "password": "p"}),
            _post("https://oa/api/submit", {"a": 1}, resp={"code": 200}),
        ]
        spec = to_flow_spec(captured, samples={"a": "1"})
        self.assertEqual(len(spec.steps), 1)
        self.assertIn("/api/submit", spec.steps[0].path)

    def test_dangerous_write_marked_l4(self):
        captured = [_post("https://oa/api/leave/delete/123", {"id": 123}, method="DELETE",
                          resp={"code": 200})]
        spec = to_flow_spec(captured)
        self.assertEqual(len(spec.steps), 1)
        self.assertEqual(spec.steps[0].risk_level, "L4")
        self.assertEqual(spec.risk_level, "L4")

    def test_system_values_detected(self):
        captured = [_post("https://oa/api/submit",
                          {"a": 1, "submitTime": 1719849600000, "createTime": 1719849600},
                          resp={"code": 200})]
        spec = to_flow_spec(captured, samples={"a": "1"})
        st = spec.steps[0]
        sys_paths = {sv.path for sv in st.system_values}
        self.assertIn("submitTime", sys_paths)
        self.assertIn("createTime", sys_paths)
        by_path = {p.path: p for p in st.params}
        self.assertEqual(by_path["submitTime"].category, "runtime_var")
        self.assertEqual(by_path["submitTime"].source_kind, "system_time")
        self.assertFalse(by_path["submitTime"].exposed_to_user)
        for sv in st.system_values:
            if sv.path == "submitTime":
                self.assertEqual(sv.kind, "now_ms")
            elif sv.path == "createTime":
                self.assertEqual(sv.kind, "now_s")

    def test_token_field_can_come_from_request_header(self):
        captured = [_post(
            "https://oa/api/submit",
            {"token": "DPORTAL20260623102834", "reason": "请假"},
            headers={"token": "DPORTAL20260623102834", "Content-Type": "application/json"},
            resp={"code": 200},
        )]
        spec = to_flow_spec(captured, samples={"reason": "请假"})
        param = {p.path: p for p in spec.steps[0].params}["token"]
        self.assertEqual(param.category, "runtime_var")
        self.assertEqual(param.source_kind, "request_header")
        self.assertEqual(param.source["header"], "token")
        self.assertFalse(param.exposed_to_user)
        self.assertFalse(param.need_human_confirm)
        self.assertFalse(any(i.target.get("path") == "token" for i in spec.review_items))

        apir, errors = flow_spec_to_api_request(spec)
        self.assertEqual(errors, [])
        self.assertTrue(any(i["path"] == "token" and i["source"] == "requestHeader:token" for i in apir["identity"]))

    def test_session_literals_are_runtime_unknown_not_constants(self):
        captured = [_post(
            "https://oa/api/chat",
            {
                "wybs": "bfb49e8-9c90-4315-9eaf-5c0e938b87bf",
                "conversation_id": "26a5509d-4fbb-4241-8924-186ed6bdd3dc",
                "question": "你好",
            },
            resp={"code": 200},
        )]
        spec = to_flow_spec(captured, samples={"question": "你好"})

        by_path = {p.path: p for p in spec.steps[0].params}
        self.assertEqual(by_path["wybs"].category, "runtime_var")
        self.assertEqual(by_path["wybs"].source_kind, "unknown")
        self.assertEqual(by_path["conversation_id"].category, "runtime_var")
        self.assertEqual(by_path["conversation_id"].source_kind, "unknown")

        apir, errors = flow_spec_to_api_request(spec)
        self.assertIsNone(apir)
        self.assertTrue(any("wybs" in e for e in errors))
        self.assertTrue(any("conversation_id" in e for e in errors))
        report = validate_flow_spec(spec)
        self.assertFalse(report["passed"])
        self.assertTrue(any("运行期变量" in e for e in report["errors"]))

    def test_summary_shape(self):
        captured = [_post("https://oa/api/submit", {"a": 1}, resp={"code": 200})]
        spec = to_flow_spec(captured, samples={"a": "1"}, tenant="acme", subsystem="HR")
        s = flow_spec_to_summary(spec)
        self.assertEqual(s["flow_id"], spec.flow_id)
        self.assertEqual(s["step_count"], 1)
        self.assertEqual(s["link_count"], 0)
        self.assertEqual(s["risk_level"], "L3")
        self.assertEqual(s["schema_version"], 1)
        st_sum = s["steps"][0]
        self.assertIn("step_id", st_sum)
        self.assertNotIn("params", st_sum)  # 轻量摘要不含 params

    def test_summary_json_serializable(self):
        spec = to_flow_spec([_post("https://oa/api/submit", {"a": 1}, resp={"code": 200})])
        s = json.dumps(flow_spec_to_summary(spec), ensure_ascii=False)
        self.assertIsInstance(s, str)
        self.assertEqual(json.loads(s)["flow_id"], spec.flow_id)

    def test_pydantic_models_serializable(self):
        spec = to_flow_spec([_post("https://oa/api/submit", {"a": 1, "b": "x"}, resp={"code": 200})],
                            samples={"a": "1", "b": "x"})
        d = spec.model_dump()
        self.assertIn("steps", d)
        self.assertIn("links", d)
        s = json.dumps(d, ensure_ascii=False, default=str)
        self.assertIsInstance(s, str)

    def test_default_step_name(self):
        self.assertEqual(_default_step_name({"method": "POST", "url": "https://oa/api/submit"}), "POST_submit")
        self.assertEqual(_default_step_name({"method": "PUT", "url": "https://oa/"}), "PUT_未命名")

    def test_derive_step_name_with_params(self):
        st = FlowStep(method="POST", url="/api/submit", path="/api/submit",
                      params=[ParamField(path="x", key="x", value="1", type="string", required=True)])
        self.assertIn("含1字段", _derive_step_name(st))


class GetBusinessStepTest(unittest.TestCase):
    """Bug4: 业务型 GET 也入 spec(响应被后续 step 引用)。"""

    def test_business_get_in_spec(self):
        captured = [
            _post("https://x/dataiq/save_dataiq_chat_list",
                  {"user_id": "u1", "name": "test"},
                  resp={"code": 200, "data": {"conversation_id": "c-123"}}),
            {"method": "GET",
             "url": "https://x/apigateway/getappid?appId=auto&appName=auto",
             "response_json": {"code": 200, "data": "app-code"}},
            _post("https://x/dataiq/sjws_chat",
                  {"sys_query": "q", "conversation_id": "c-123", "appCode": "app-code"},
                  resp={"code": 200}),
        ]
        spec = to_flow_spec(captured_requests=captured)
        paths = [s.path for s in spec.steps]
        self.assertTrue(any("/getappid" in p for p in paths),
                        f"GET 业务流程步必须进 spec, actual: {paths}")
        self.assertEqual(len(spec.steps), 3)
        roles = spec.meta.get("request_roles") or []
        self.assertEqual(len(roles), 3)
        self.assertIn("business_get", {r["role"] for r in roles})
        get_role = next(r for r in roles if "/getappid" in r["path"])
        self.assertTrue(get_role["keep"])
        self.assertIn("后续业务请求引用", get_role["reason"])
        by_step = {s.path: s for s in spec.steps}
        send_step = next(s for s in spec.steps if "/sjws_chat" in s.path)
        send_params = {p.path: p for p in send_step.params}
        self.assertEqual(send_params["conversation_id"].category, "runtime_var")
        self.assertEqual(send_params["conversation_id"].source_kind, "previous_response")
        self.assertEqual(send_params["appCode"].category, "runtime_var")
        self.assertEqual(send_params["appCode"].source_kind, "previous_response")
        get_step = next(s for s in spec.steps if "/getappid" in s.path)
        get_params = {p.path: p for p in get_step.params}
        self.assertEqual(get_params["query.appId"].category, "system_const")
        self.assertEqual(get_params["query.appId"].source_kind, "page_context")
        self.assertFalse(get_params["query.appId"].exposed_to_user)
        self.assertTrue(get_params["query.appId"].need_human_confirm)
        review_types = {item.type for item in spec.review_items}
        self.assertIn("request_role", review_types)
        self.assertIn("field_category", review_types)
        self.assertTrue(any(item.target.get("path") == "query.appId" for item in spec.review_items))

        desc = render_business_description(spec)
        for heading in [
            "## 1. 业务目的",
            "## 2. 用户需要提供的参数",
            "## 3. 系统自动处理的变量",
            "## 4. 固定系统常量",
            "## 5. 执行步骤",
            "## 6. 接口依赖关系",
            "## 7. 成功判断",
            "## 8. 风险与注意事项",
            "## 9. 需要人工确认的问题",
        ]:
            self.assertIn(heading, desc)
        self.assertIn("sys_query", desc)
        self.assertIn("conversation_id", desc)
        self.assertIn("appCode", desc)
        self.assertIn("getappid", desc)
        self.assertIn("待确认", desc)

        apir, errors = flow_spec_to_api_request(spec)
        self.assertEqual(errors, [])
        self.assertEqual(len(apir["steps"]), 3)
        get_api_step = next(s for s in apir["steps"] if "/getappid" in s["path"])
        self.assertEqual(get_api_step["method"], "GET")
        self.assertEqual(get_api_step["query_template"]["appId"], "auto")
        self.assertEqual(get_api_step["query_template"]["appName"], "auto")
        self.assertEqual(get_api_step["response_json"]["data"], "app-code")
        send_api_step = next(s for s in apir["steps"] if "/sjws_chat" in s["path"])
        self.assertTrue(any(l["target_path"] == "conversation_id" for l in send_api_step["links"]))
        self.assertTrue(any(l["target_path"] == "appCode" for l in send_api_step["links"]))

        dry = dry_run_flow_spec(spec)
        self.assertTrue(dry["ok"])
        self.assertEqual(dry["request_count"], 3)
        get_preview = next(p for p in dry["request_previews"] if "/getappid" in p["path"])
        self.assertEqual(get_preview["query_preview"]["appId"], "auto")
        self.assertEqual(get_preview["query_preview"]["appName"], "auto")

    def test_get_query_params_extracted(self):
        fields = _params_from_get_query({
            "method": "GET",
            "url": "https://x/apigateway/getappid?appId=xxx&appName=yyy",
        })
        keys = [f["key"] for f in fields]
        self.assertIn("appId", keys)
        self.assertIn("appName", keys)
        self.assertTrue(all(f["path"].startswith("query.") for f in fields))

    def test_get_no_query_returns_empty(self):
        self.assertEqual(_params_from_get_query({"method": "GET", "url": "https://x/api/foo"}), [])

    def test_runtime_query_field_is_not_exposed_as_user_param(self):
        """GET query 中的运行期变量应由依赖/上下文处理，不应要求 Skill 调用者手填。"""
        src = FlowStep(
            step_id="s1",
            name="GET_token",
            method="GET",
            url="https://oa/api/token",
            path="/api/token",
            response_json={"data": {"token": "T-1"}},
            risk_level="L1",
            semantic_role="business_get",
        )
        dst = FlowStep(
            step_id="s2",
            name="GET_detail",
            method="GET",
            url="https://oa/api/detail?token=T-OLD",
            path="/api/detail",
            params=[
                ParamField(
                    path="query.token",
                    key="token",
                    value="T-OLD",
                    category="runtime_var",
                    source_kind="previous_response",
                    source={"step_id": "s1", "path": "data.token"},
                    exposed_to_user=False,
                )
            ],
            risk_level="L1",
            semantic_role="business_get",
        )
        spec = FlowSpec(
            title="query runtime",
            steps=[src, dst],
            links=[
                FlowLink(
                    source_step_id="s1",
                    source_path="data.token",
                    target_step_id="s2",
                    target_path="query.token",
                    confidence=0.95,
                    confirmed=True,
                )
            ],
        )

        apir, errors = flow_spec_to_api_request(spec)
        self.assertEqual(errors, [])
        detail_step = next(s for s in apir["steps"] if s["path"] == "/api/detail")
        self.assertEqual(detail_step["query_template"]["token"], "T-OLD")
        self.assertNotIn("token", detail_step.get("params") or [])
        self.assertTrue(any(l["target_path"] == "query.token" for l in detail_step["links"]))

    def test_list_response_not_business_get(self):
        """返回 list 的 GET 是下拉源,不入 spec。"""
        captured = [
            _post("https://x/dataiq/submit", {"a": 1}, resp={"code": 200}),
            {"method": "GET", "url": "https://x/api/users",
             "response_json": [{"id": 1, "name": "张三"}]},  # 列表 → 不入
        ]
        spec = to_flow_spec(captured, samples={"a": "1"})
        self.assertEqual(len(spec.steps), 1)
        self.assertNotIn("/api/users", spec.steps[0].path)
        roles = spec.meta.get("request_roles") or []
        users_role = next(r for r in roles if "/api/users" in r["path"])
        self.assertEqual(users_role["role"], "read_option")
        self.assertFalse(users_role["keep"])

    def test_repeated_preread_gets_are_deduped_and_option_reads_stay_out(self):
        """录制中反复触发的审批详情 GET 只保留最后一次；选人列表不进入主流程。"""
        process_definition_id = "oa_duty_leave:4:f92ff5fc-75e0-11f1-9f05-7683e518321b"
        approval_null = (
            "https://oa/admin-api/bpm/process-instance/get-approval-detail"
            "?processDefinitionId=oa_duty_leave%3A4%3Af92ff5fc-75e0-11f1-9f05-7683e518321b"
            "&activityId=StartUserNode&processVariablesStr=%7B%22day%22%3Anull%7D"
        )
        approval_day_1 = approval_null.replace("%22day%22%3Anull", "%22day%22%3A1")
        approval_resp = {
            "code": 0,
            "data": {
                "processDefinition": {"id": process_definition_id, "key": "oa_duty_leave"},
                "startUserNode": {"id": "StartUserNode"},
            },
        }
        captured = [
            {"method": "GET", "url": approval_null, "response_json": approval_resp},
            {"method": "GET", "url": approval_null, "response_json": approval_resp},
            *[
                {"method": "GET", "url": approval_day_1, "response_json": approval_resp}
                for _ in range(10)
            ],
            {"method": "GET",
             "url": "https://oa/admin-api/system/user/page?pageNo=1&pageSize=10",
             "response_json": {"code": 0, "data": {"list": [{"id": "u-139", "nickname": "梅玄"}]}}},
            {"method": "GET",
             "url": "https://oa/admin-api/system/user/page?pageNo=1&pageSize=10",
             "response_json": {"code": 0, "data": {"list": [{"id": "u-139", "nickname": "梅玄"}]}}},
            _post("https://oa/admin-api/oa/duty-leave/submit-process", {
                "processDefinitionId": process_definition_id,
                "activityId": "StartUserNode",
                "day": 1,
                "approver": "u-139",
            }, resp={"code": 0, "data": {"id": "pi-1"}}),
        ]

        spec = to_flow_spec(captured_requests=captured)

        self.assertEqual(len(spec.steps), 2)
        paths = [s.path for s in spec.steps]
        self.assertTrue(any("/get-approval-detail" in p for p in paths), paths)
        self.assertTrue(any("/submit-process" in p for p in paths), paths)
        self.assertFalse(any("/system/user/page" in p for p in paths), paths)
        approval_step = next(s for s in spec.steps if "/get-approval-detail" in s.path)
        self.assertIn("%22day%22%3A1", approval_step.path)
        self.assertEqual(spec.meta["captured_preread_candidates_before_dedupe"], 12)
        self.assertEqual(spec.meta["captured_preread_candidates"], 1)

        roles = spec.meta.get("request_roles") or []
        self.assertEqual(sum(1 for r in roles if r["role"] == "business_get"), 12)
        user_page_roles = [r for r in roles if "/system/user/page" in r["path"]]
        self.assertTrue(user_page_roles)
        self.assertTrue(all(r["role"] == "read_option" and not r["keep"] for r in user_page_roles))

    def test_nested_detail_row_select_pair_uses_captured_read_options(self):
        """captured_requests 里的候选读接口应能绑定明细行内 name/id 字段，且不折叠整行。"""
        sys1 = "交通信息系统01"
        sys2 = "交通信息系统02"
        body = {
            "ssbmId": "02021060111315890400000101001838",
            "bmId": "02021060111315890400000101001838",
            "ssbmmc": "徐州市交通运输局",
            "csmc": "123123",
            "ywsxList": [{
                "ywsxmc": "123123qweqw",
                "yyxtid": "02026031815271171200000101539137",
                "ssxts": "",
                "catalogStatus": "",
                "yyxtmc": sys1,
                "tableHcommentList": [],
                "ywsxKbList": [],
            }],
        }
        captured = [
            {
                "method": "GET",
                "url": "https://oa/appgateway/dcensus/v1.0/qzqdsl/getXxxtListByBm",
                "response_json": {
                    "data": [
                        {"yyxtid": "02026031815271171200000101539137", "yyxtmc": sys1},
                        {"yyxtid": "02026031815271171200000101539138", "yyxtmc": sys2},
                    ],
                },
            },
            _post("https://oa/appgateway/dcensus/v1.0/qzqdsl/createQzqdSl", body, resp={"code": 200}),
        ]

        spec = to_flow_spec(captured, samples={"职能清单": "123123qweqw", "所属系统": sys1})
        step = spec.steps[0]

        self.assertEqual(len(step.selects), 1)
        sel = step.selects[0]
        self.assertEqual(sel.path, "ywsxList[0].yyxtmc")
        self.assertEqual(sel.value_key, "yyxtid")
        self.assertEqual(sel.label_key, "yyxtmc")
        self.assertEqual(sel.id_path, "ywsxList[0].yyxtid")
        self.assertEqual(sel.options, [sys1, sys2])

        by_path = {p.path: p for p in step.params}
        self.assertEqual(by_path["ywsxList[0].ywsxmc"].category, "user_param")
        self.assertTrue(by_path["ywsxList[0].ywsxmc"].exposed_to_user)
        self.assertEqual(by_path["ywsxList[0].yyxtmc"].source_kind, "api_option")
        self.assertEqual(by_path["ywsxList[0].yyxtmc"].type, "enum")
        self.assertEqual(by_path["ywsxList[0].yyxtmc"].enum_options, [sys1, sys2])
        self.assertTrue(by_path["ywsxList[0].yyxtmc"].exposed_to_user)
        self.assertEqual(by_path["ywsxList[0].yyxtid"].category, "runtime_var")
        self.assertEqual(by_path["ywsxList[0].yyxtid"].source_kind, "api_option")
        self.assertFalse(by_path["ywsxList[0].yyxtid"].exposed_to_user)
        self.assertEqual(by_path["ssbmId"].category, "system_const")
        self.assertFalse(by_path["ssbmId"].exposed_to_user)
        self.assertEqual(by_path["bmId"].source_kind, "page_context")
        self.assertFalse(by_path["bmId"].exposed_to_user)
        self.assertEqual(by_path["ssbmmc"].source_kind, "page_context")
        self.assertFalse(by_path["ssbmmc"].exposed_to_user)
        self.assertEqual(by_path["ywsxList[0].ssxts"].category, "system_const")
        self.assertFalse(by_path["ywsxList[0].ssxts"].exposed_to_user)

    def test_flow_spec_uses_page_enum_options_for_sourceless_enum(self):
        body = {"type": "事假", "reason": "回家"}
        spec = to_flow_spec(
            [_post("https://oa/api/leave/submit", body, resp={"code": 200})],
            samples={"type": "事假", "reason": "回家"},
            page_enum_options={"事假": ["事假", "病假", "年假"]},
        )

        by_path = {p.path: p for p in spec.steps[0].params}
        self.assertEqual(by_path["type"].type, "enum")
        self.assertEqual(by_path["type"].source_kind, "page_enum")
        self.assertEqual(by_path["type"].enum_options, ["事假", "病假", "年假"])
        self.assertEqual(spec.steps[0].selects[0].options, ["事假", "病假", "年假"])

    def test_flow_spec_uses_page_enum_options_when_enum_submits_code(self):
        body = {"type": 2, "reason": "回家"}
        spec = to_flow_spec(
            [_post("https://oa/api/leave/submit", body, resp={"code": 200})],
            samples={"类型": "2", "reason": "回家"},
            page_enum_options={"类型": [{"label": "事假", "value": 2}, {"label": "病假", "value": 3}]},
        )

        by_path = {p.path: p for p in spec.steps[0].params}
        self.assertEqual(by_path["type"].key, "类型")
        self.assertEqual(by_path["type"].type, "enum")
        self.assertEqual(by_path["type"].source_kind, "page_enum")
        self.assertEqual(by_path["type"].enum_options, ["事假", "病假"])
        self.assertEqual(spec.steps[0].selects[0].option_map, {"事假": 2, "病假": 3})

    def test_flow_spec_does_not_mark_enum_without_real_options(self):
        spec = to_flow_spec(
            [_post("https://oa/api/leave/submit", {"type": 2, "reason": "回家"}, resp={"code": 200})],
            samples={"类型": "2", "reason": "回家"},
            page_enum_options={"类型": []},
        )

        by_path = {p.path: p for p in spec.steps[0].params}
        self.assertNotEqual(by_path["type"].type, "enum")
        self.assertNotIn(by_path["type"].source_kind, {"api_option", "page_enum", "static_enum", "manual_enum", "form_option"})

    def test_flow_spec_does_not_bind_short_code_to_unrelated_api_option(self):
        body = {"type": 1, "name": "测试"}
        tenant_read = {
            "url": "http://admin.example.com/system/tenant/simple-list",
            "json": [
                {"id": 1, "name": "点新信息"},
                {"id": 2, "name": "小租户"},
            ],
        }

        spec = to_flow_spec(
            [_post("https://oa/api/submit", body, resp={"code": 200})],
            reads=[tenant_read],
            samples={"租户": "点新信息", "type": "1", "name": "测试"},
        )

        by_path = {p.path: p for p in spec.steps[0].params}
        self.assertNotEqual(by_path["type"].type, "enum")
        self.assertNotEqual(by_path["type"].source_kind, "api_option")
        self.assertEqual(spec.steps[0].selects, [])

    def test_apply_llm_field_names_only_updates_machine_auto_names(self):
        spec = to_flow_spec([
            _post("https://oa/api/leave/submit",
                  {"type": "事假", "reason": "回家", "days": 1},
                  resp={"code": 200})
        ], samples={"type": "事假", "reason": "回家", "days": "1"})
        spec.steps[0].params[2].key = "天数"
        spec.steps[0].params[2].label = "天数"
        spec.steps[0].params[2].name_source = "manual"

        candidates = llm_field_name_candidates(spec)
        self.assertEqual({c["key"] for c in candidates}, {"type", "reason"})

        renamed = apply_llm_field_names(spec, {"type": "请假类型", "reason": "原因", "days": "请假天数"})
        by_path = {p.path: p for p in renamed.steps[0].params}
        self.assertEqual(by_path["type"].key, "请假类型")
        self.assertEqual(by_path["reason"].key, "原因")
        self.assertEqual(by_path["days"].key, "天数")
        self.assertEqual(renamed.steps[0].sample_inputs["请假类型"], "事假")
        self.assertEqual(renamed.steps[0].sample_inputs["原因"], "回家")
        self.assertNotIn("type", renamed.steps[0].sample_inputs)


class RequestRoleTest(unittest.TestCase):
    def test_auth_request_is_filtered(self):
        role = classify_network_request(
            _post("https://oa/api/login", {"username": "u", "password": "p"}),
            samples={},
        )
        self.assertEqual(role["role"], "auth")
        self.assertFalse(role["keep"])
        self.assertTrue(role["filter_reason"])

    def test_static_noise_is_filtered(self):
        role = classify_network_request({"method": "GET", "url": "https://oa/assets/app.js"})
        self.assertEqual(role["role"], "noise")
        self.assertFalse(role["keep"])

    def test_write_with_sample_is_submit_anchor(self):
        req = _post("https://oa/api/chat/send", {"sys_query": "分析销售数据"})
        role = classify_network_request(req, [req], samples={"sys_query": "分析销售数据"})
        self.assertEqual(role["role"], "submit_anchor")
        self.assertTrue(role["keep"])
        self.assertEqual(role["evidence"]["sample_hits"], 1)

    def test_get_referenced_later_is_business_get(self):
        trace = [
            {"method": "GET", "url": "https://x/apigateway/getappid?appId=auto",
             "response_json": {"code": 200, "data": "app-code"}},
            _post("https://x/dataiq/sjws_chat", {"appCode": "app-code", "sys_query": "q"}),
        ]
        role = classify_network_request(trace[0], trace)
        self.assertEqual(role["role"], "business_get")
        self.assertTrue(role["keep"])
        self.assertEqual(role["evidence"]["source_path"], "data")


class TypeInferenceTest(unittest.TestCase):
    def test_number(self):
        self.assertEqual(_infer_type_from_value("123"), "number")
        self.assertEqual(_infer_type_from_value("123.45"), "number")

    def test_boolean(self):
        self.assertEqual(_infer_type_from_value("true"), "boolean")
        self.assertEqual(_infer_type_from_value("false"), "boolean")

    def test_date(self):
        self.assertEqual(_infer_type_from_value("2024-01-01"), "date")

    def test_datetime(self):
        self.assertEqual(_infer_type_from_value("2024-01-01T12:00:00"), "datetime")

    def test_string(self):
        self.assertEqual(_infer_type_from_value("hello"), "string")

    def test_empty(self):
        self.assertEqual(_infer_type_from_value(None), "string")
        self.assertEqual(_infer_type_from_value(""), "string")


class FlowSpecPublishTest(unittest.TestCase):
    def test_publish_selection_turns_checked_fields_into_api_params(self):
        spec = to_flow_spec([
            _post("https://oa/api/leave/submit",
                  {"reason": "回家", "days": 3},
                  resp={"code": 200})
        ], samples={"reason": "回家", "days": "3"})

        spec = apply_flow_publish_selection(
            spec,
            {"reason": "leave_reason"},
            selected_scope_paths={"reason", "days"},
        )
        apir, errors = flow_spec_to_api_request(spec)

        self.assertEqual(errors, [])
        self.assertIsNotNone(apir)
        self.assertEqual(apir["params"], ["leave_reason"])
        self.assertEqual(apir["body_template"]["reason"], "{{leave_reason}}")
        self.assertEqual(apir["body_template"]["days"], 3)
        params = {p.path: p for p in spec.steps[0].params}
        self.assertEqual(params["days"].category, "system_const")
        self.assertEqual(params["days"].source_kind, "constant")
        self.assertFalse(params["days"].exposed_to_user)
        self.assertTrue(validate_flow_spec(spec)["passed"])

    def test_dry_run_constructs_single_request(self):
        spec = to_flow_spec([
            _post("https://oa/api/leave/submit",
                  {"reason": "回家", "days": 3},
                  resp={"code": 200})
        ], samples={"reason": "回家", "days": "3"})

        dry = dry_run_flow_spec(spec)

        self.assertTrue(dry["ok"])
        self.assertEqual(dry["request_count"], 1)
        self.assertEqual(dry["self_check"], [])
        self.assertEqual(dry["request_previews"][0]["body_preview"]["reason"], "回家")
        self.assertEqual(dry["fact_check"]["configured"], False)

    def test_publish_workflow_preserves_flow_links(self):
        spec = to_flow_spec([
            _post("https://oa/api/leave/start",
                  {"flowType": "leave"},
                  resp={"code": 200, "data": {"taskId": "T-777"}}),
            _post("https://oa/api/leave/submit",
                  {"flowTask": {"taskId": "T-777"}, "type": "事假"},
                  resp={"code": 200}),
        ], samples={"type": "事假"})

        apir, errors = flow_spec_to_api_request(spec)

        self.assertEqual(errors, [])
        self.assertIn("steps", apir)
        self.assertEqual(len(apir["steps"]), 2)
        self.assertEqual(apir["steps"][0]["response_json"]["data"]["taskId"], "T-777")
        links = apir["steps"][1].get("links") or []
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["source_step"], 0)
        self.assertEqual(links[0]["target_path"], "flowTask.taskId")

    def test_client_spec_redacts_secrets_and_raw_body(self):
        spec = to_flow_spec([
            _post("https://oa/api/submit", {"a": 1},
                  resp={"code": 200, "data": {"accessToken": "secret-token", "answer": "ok"}},
                  headers={"Authorization": "Bearer secret", "X-Tenant": "acme"})
        ])

        client = flow_spec_to_client(spec)
        step = client["steps"][0]

        self.assertEqual(step["headers"]["Authorization"], "***")
        self.assertEqual(step["headers"]["X-Tenant"], "***")
        self.assertEqual(step["body_source"], "")
        self.assertEqual(step["response_json"]["data"]["accessToken"], "***")
        self.assertEqual(step["response_json"]["data"]["answer"], "ok")


if __name__ == "__main__":
    unittest.main()
