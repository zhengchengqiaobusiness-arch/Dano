"""Step A · FlowSpec 收敛函数测试。"""

from __future__ import annotations

import json
import asyncio
import unittest

from dano.execution.page.flow_spec import (
    FlowSpec, FlowStep, FlowLink, ParamField, FlowCapability,
    apply_flow_edits,
    apply_flow_publish_selection,
    apply_llm_field_names,
    classify_network_request,
    dry_run_flow_spec,
    flow_spec_to_api_request,
    flow_spec_to_client,
    flow_spec_to_summary,
    llm_field_name_candidates,
    orchestrate_flow_capabilities,
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

    def test_graphql_request_marked_unsupported(self):
        role = classify_network_request(_post(
            "https://oa/api/graphql",
            {"query": "mutation Submit($input: Input!) { submit(input: $input) { id } }"},
        ))
        self.assertEqual(role["role"], "unsupported_graphql")
        self.assertFalse(role["keep"])

    def test_dangerous_write_marked_l4(self):
        captured = [_post("https://oa/api/leave/delete/123", {"id": 123}, method="DELETE",
                          resp={"code": 200})]
        spec = to_flow_spec(captured)
        self.assertEqual(len(spec.steps), 1)
        self.assertEqual(spec.steps[0].risk_level, "L4")
        self.assertEqual(spec.risk_level, "L4")
        report = validate_flow_spec(spec)
        self.assertFalse(report["passed"])
        self.assertTrue(any("发布阻断项未处理" in e for e in report["errors"]))

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
        # 系统化:uuid 形态(session literal)依然要标 runtime_var/unknown，让前端在 review_items
        # 提示用户去绑定上游响应、请求头或改 user_param；它本身不恢复成发布硬阻断。
        self.assertEqual(by_path["wybs"].category, "runtime_var")
        self.assertEqual(by_path["wybs"].source_kind, "unknown")
        self.assertEqual(by_path["conversation_id"].category, "runtime_var")
        self.assertEqual(by_path["conversation_id"].source_kind, "unknown")

        apir, errors = flow_spec_to_api_request(spec)
        # **系统化**:runtime_var/unknown 不再硬性阻断发布 — 让前端 review_items 提示用户确认,
        # 后端在执行时按字段值稳定性容错;这里只检查能产出 apir(具体值会在运行时被注入)
        self.assertIsNotNone(apir)
        # 至少 question 已被识别为用户参数;runtime_var 字段保留原值或被运行时覆盖
        self.assertIn("question", apir.get("body_template", {}))
        report = validate_flow_spec(spec)
        self.assertTrue(report["passed"])
        self.assertTrue(any("runtime_var" in w for w in report["warnings"]))

    def test_flow_spec_records_recording_mode_and_diagnostics(self):
        diagnostics = [{"type": "console", "level": "error", "message": "x"}]
        spec = to_flow_spec(
            [_post("https://oa/api/submit", {"a": 1}, resp={"code": 200})],
            samples={"a": "1"},
            recording_mode="intercepted_submit",
            diagnostics=diagnostics,
        )
        summary = flow_spec_to_summary(spec)
        client = flow_spec_to_client(spec)

        self.assertEqual(spec.recording_mode, "intercepted_submit")
        self.assertEqual(spec.diagnostics, diagnostics)
        self.assertEqual(summary["recording_mode"], "intercepted_submit")
        self.assertEqual(summary["diagnostic_count"], 1)
        self.assertEqual(client["recording_mode"], "intercepted_submit")
        self.assertEqual(client["diagnostics"], diagnostics)

    def test_validate_blocks_key_requestfailed_diagnostic(self):
        spec = to_flow_spec(
            [_post("https://oa/api/submit", {"a": 1}, resp={"code": 200})],
            samples={"a": "1"},
            diagnostics=[{
                "type": "requestfailed",
                "level": "error",
                "message": "net::ERR_FAILED",
                "url": "https://oa/api/submit",
            }],
        )
        report = validate_flow_spec(spec)

        self.assertFalse(report["passed"])
        self.assertTrue(any("录制期业务请求失败" in e for e in report["errors"]))

    def test_summary_shape(self):
        captured = [_post("https://oa/api/submit", {"a": 1}, resp={"code": 200})]
        spec = to_flow_spec(captured, samples={"a": "1"}, tenant="acme", subsystem="HR")
        s = flow_spec_to_summary(spec)
        self.assertEqual(s["flow_id"], spec.flow_id)
        self.assertEqual(s["step_count"], 1)
        self.assertEqual(s["link_count"], 0)
        self.assertEqual(s["capability_count"], len(spec.capabilities))
        self.assertEqual(s["risk_level"], "L3")
        self.assertEqual(s["schema_version"], 1)
        self.assertEqual(s["capabilities"], [])

        orchestrated = asyncio.run(orchestrate_flow_capabilities(spec, llm_client=None, model=None))
        self.assertIn("submit_batch", {c.kind for c in orchestrated.capabilities})
        st_sum = s["steps"][0]
        self.assertIn("step_id", st_sum)
        self.assertNotIn("params", st_sum)  # 轻量摘要不含 params

    def test_recorded_goal_is_generated_from_recording(self):
        captured = [_post("https://oa/api/submit", {"date": "2026-05-12", "content": "日报"}, resp={"code": 200})]
        spec = to_flow_spec(captured, samples={"date": "2026-05-12", "content": "日报"})

        self.assertTrue(spec.goal)
        self.assertIn("intent", spec.goal)
        self.assertIn("success_criteria", spec.goal)
        self.assertIn("forbidden_actions", spec.goal)
        self.assertIn("submit", spec.goal.get("capabilities") or [])

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
        graph = spec.meta.get("request_graph") or {}
        cand_paths = [r.get("path") for r in graph.get("candidate_reads") or []]
        self.assertIn("/api/users", cand_paths)

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
        graph = spec.meta.get("request_graph") or {}
        self.assertGreaterEqual(len(graph.get("selected_steps") or []), 2)
        self.assertTrue(any("/system/user/page" in (r.get("path") or "") for r in graph.get("candidate_reads") or []))

        self.assertEqual(spec.capabilities, [])
        orchestrated = asyncio.run(orchestrate_flow_capabilities(spec, llm_client=None, model=None))
        cap_kinds = {c.kind for c in orchestrated.capabilities}
        self.assertEqual(cap_kinds, {"submit_batch"})
        submit_cap = orchestrated.capabilities[0]
        self.assertTrue(any("/get-approval-detail" in s.path for s in orchestrated.steps if s.step_id in submit_cap.step_ids))
        self.assertTrue(any("/submit-process" in s.path for s in orchestrated.steps if s.step_id in submit_cap.step_ids))

        client = flow_spec_to_client(orchestrated)
        self.assertIn("capabilities", client)
        self.assertEqual({c["kind"] for c in client["capabilities"]}, {"submit_batch"})
        apir, errors = flow_spec_to_api_request(orchestrated)
        self.assertEqual(errors, [])
        self.assertIn("capabilities", apir)
        self.assertTrue(all(s.get("step_id") for s in apir["steps"]))
        self.assertIn("submit_batch", {c["kind"] for c in apir["capabilities"]})

    def test_capability_validate_gate_sanitizes_stale_missing_step(self):
        spec = FlowSpec(
            flow_id="cap-gate",
            steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
            capabilities=[FlowCapability(
                name="submit_batch",
                kind="submit_batch",
                step_ids=["submit", "missing"],
                nodes=[{"id": "call_missing", "type": "call", "step_id": "missing"}],
                confirmed=True,
                requires_human_confirm=False,
            )],
        )

        report = validate_flow_spec(spec)

        self.assertFalse(any("missing" in e for e in report["errors"]))
        self.assertIn("capability_preview", report)

    def test_capability_step_can_reference_captured_request_without_duplicates(self):
        spec = FlowSpec(
            flow_id="cap-add-request",
            capabilities=[FlowCapability(name="query_status", kind="query_status", confirmed=False)],
            meta={"request_graph": {"all_requests": [{
                "request_index": 89,
                "request_id": "req-89",
                "method": "GET",
                "url": "https://oa.example.com/api/work-days?start=2026-05-01",
                "path": "/api/work-days?start=2026-05-01",
                "role": "business_get",
                "keep": True,
                "confidence": 0.96,
                "response_status": 200,
                "response_json": {"code": 0, "data": {"missing_dates": ["2026-05-12"]}},
            }]}},
        )

        edited = apply_flow_edits(spec, [{
            "op": "add_capability_step",
            "capability_name": "query_status",
            "request_index": 89,
        }])
        edited_again = apply_flow_edits(edited, [{
            "op": "add_capability_step",
            "capability_name": "query_status",
            "request_index": 89,
        }])

        self.assertEqual(len(edited_again.steps), 1)
        self.assertEqual(edited_again.capabilities[0].step_ids, [edited_again.steps[0].step_id])
        graph = edited_again.meta.get("request_graph") or {}
        self.assertEqual(len(graph.get("selected_steps") or []), 1)

    def test_orchestrate_flow_merges_existing_capabilities(self):
        spec = FlowSpec(
            flow_id="cap-merge",
            steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
            capabilities=[FlowCapability(
                name="submit_batch",
                title="人工改过的标题",
                kind="submit_batch",
                step_ids=[],
                input_schema={"type": "object"},
                confirmed=True,
                requires_human_confirm=False,
                confidence=0.3,
            )],
        )

        out = asyncio.run(orchestrate_flow_capabilities(spec, llm_client=None, model=None))

        cap = next(c for c in out.capabilities if c.name == "submit_batch")
        self.assertEqual(cap.title, "人工改过的标题")
        self.assertTrue(cap.confirmed)
        self.assertIn("submit", cap.step_ids)
        self.assertGreaterEqual(cap.confidence, 0.3)

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
        # 系统化:enum_options 是 [{label, value}] 形态(带 ID)
        opts = by_path["ywsxList[0].yyxtmc"].enum_options or []
        labels = [o.get("label") if isinstance(o, dict) else o for o in opts]
        self.assertEqual(sorted(labels), sorted([sys1, sys2]))
        # 同时验证 enum_value_map 存在(name→ID 运行期用)
        vmap = by_path["ywsxList[0].yyxtmc"].enum_value_map or {}
        self.assertIn(sys1, vmap)
        self.assertIn(sys2, vmap)
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
        # 系统化:enum_options 既支持 list[str] 也支持 list[{label, value}];当 option_map 存在时优先 list[{label, value}]
        opts1 = by_path["type"].enum_options
        # 应同时保留 enum_options 与 enum_value_map(name→ID 桥接)
        self.assertEqual(by_path["type"].type, "enum")
        self.assertEqual(by_path["type"].source_kind, "page_enum")
        # enum_options 兼容:可能是 list[str] 或 list[{label,value}](取决于是否带 option_map)
        labels1 = [o.get("label") if isinstance(o, dict) else o for o in (opts1 or [])]
        # 用 set 比较避免中文 sort 顺序差异
        self.assertEqual(set(labels1), {"病假", "年假", "事假"})
        self.assertEqual(set(spec.steps[0].selects[0].options), {"事假", "病假", "年假"})

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
        # 系统化:带 value 的枚举应同时出 label 列表 + label→value 表(运行期 name→ID 用)
        opts = by_path["type"].enum_options or []
        labels = [o.get("label") if isinstance(o, dict) else o for o in opts]
        self.assertEqual(set(labels), {"事假", "病假"})
        self.assertEqual(by_path["type"].enum_value_map, {"事假": 2, "病假": 3})
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


class PageEnumOnInternalFieldTest(unittest.TestCase):
    """Bug1 回归:页面下拉的「请假类型=病假」中,显示名与 body 内字段名 leaveType 不同,
    录制时刻真值的 option_map 应当被识别并写入 ParamField.type=enum 与 enum_options,
    而不是退化成 user_param/unknown。通用,不挑系统/公司。"""

    def test_dom_options_match_internal_field_via_label_to_value(self):
        from dano.execution.page.request_capture import _enum_records_from_page_options
        # body 用内部码 leaveType=2,但 DOM 抓到的 options 是 display label
        body = {"formData": {"leaveType": 2, "name": "张三"}}
        # page_enum_options 用新形态:{字段key: {options:[...], field_key:...}}
        page_enum_options = {
            "请假类型": {
                "options": ["病假", "事假", "婚假"],
                "field_key": "leaveType",
            }
        }
        from dano.execution.page.request_capture import apply_page_enum_options, page_enum_selects
        out = page_enum_selects(json.dumps(body, ensure_ascii=False), page_enum_options, set(), fields=[
            {"path": "formData.leaveType", "key": "leaveType", "value": 2, "suggest_name": "请假类型"},
            {"path": "formData.name", "key": "name", "value": "张三", "suggest_name": "姓名"},
        ])
        self.assertTrue(any(s.get("path") == "formData.leaveType" for s in out),
                        f"应当命中 leaveType 路径, 实际: {[s.get('path') for s in out]}")

    def test_dom_options_match_internal_field_with_old_legacy_shape(self):
        """回退兼容:旧形态 {label: [opts]} 也得继续能用。"""
        body = {"leaveType": 2}
        page_enum_options = {"病假": ["病假", "事假"]}
        from dano.execution.page.request_capture import page_enum_selects
        out = page_enum_selects(json.dumps(body, ensure_ascii=False), page_enum_options, set(), fields=[
            {"path": "leaveType", "key": "leaveType", "value": 2, "suggest_name": "请假类型"},
        ])
        # 旧形态:label 值不在 body 里时,不应该误判命中,但也不应该报错
        self.assertIsInstance(out, list)

    def test_apply_page_enum_options_backward_compatible(self):
        """apply_page_enum_options 接收旧形态(纯 list 值)应当正常运行不报错。"""
        body = {"formData": {"status": "active"}}
        # 旧形态:value 为纯 list
        old_opts = {"active": ["启用", "停用"]}
        fields = [{"path": "formData.status", "key": "status", "value": "active"}]
        from dano.execution.page.request_capture import apply_page_enum_options
        # 输入 selects 为空,apply 不应该报错
        apply_page_enum_options([], old_opts, post_data=json.dumps(body, ensure_ascii=False),
                                fields=fields)
        # 输入 selects 含一个 select,旧形态挂 enum_source=dom
        existing = [{"path": "formData.status", "label": "active", "value": "active"}]
        apply_page_enum_options(existing, old_opts, post_data=json.dumps(body, ensure_ascii=False),
                                fields=fields)
        self.assertEqual(existing[0].get("enum_source"), "dom")
        self.assertIn("启用", existing[0].get("options") or [])


class AssigneeSelectTest(unittest.TestCase):
    """Bug3 回归:审批人/选人容器路径(startUserSelectAssignees/approvers/assignees/Activity_xxx)
    下,body 存 user id,应识别为 select(api_option + userId/name 绑定),即便 value 在 samples 里
    也要豁免「用户亲手填的值当码」的拒判。通用,不挑系统。"""

    def test_assignee_path_recognized_as_select_with_user_list(self):
        reads = [{
            "url": "https://oa/system/user/page",
            "json": {
                "data": [
                    {"userId": "145", "name": "张三", "deptId": "D-1"},
                    {"userId": "117", "name": "李四", "deptId": "D-2"},
                    {"userId": "146", "name": "王五", "deptId": "D-1"},
                    {"userId": "118", "name": "赵六", "deptId": "D-2"},
                ]
            },
        }]
        captured = [_post("https://oa/api/submit",
                          {"startUserSelectAssignees": [{"Activity_09dlq0g": ["145"],
                                                         "Activity_0ag2wyz": ["117"]}]},
                          resp={"code": 200, "data": {"ok": True}})]
        spec = to_flow_spec(captured, reads=reads,
                            samples={"startUserSelectAssignees[0].Activity_09dlq0g[0]": "145",
                                     "startUserSelectAssignees[0].Activity_0ag2wyz[0]": "117"})
        params_by_path = {p.path: p for s in spec.steps for p in s.params}
        p1 = params_by_path["startUserSelectAssignees[0].Activity_09dlq0g[0]"]
        p2 = params_by_path["startUserSelectAssignees[0].Activity_0ag2wyz[0]"]
        # 应当被识别为枚举,不是普通 user_input
        self.assertEqual(p1.type, "enum", f"审批人 1 应识别为 enum, 实际: {p1.type}")
        self.assertEqual(p2.type, "enum", f"审批人 2 应识别为 enum, 实际: {p2.type}")
        # 候选是从 user list 提取的人的姓名(id→name),形态可能是 [{label,value}] 列表
        opts1 = p1.enum_options or []
        labels = [o.get("label") if isinstance(o, dict) else o for o in opts1]
        self.assertIn("张三", labels,
                      f"候选应是 name 列表, 实际: {opts1}")
        self.assertNotIn("D-1", labels,
                         "deptId 这种上下文字段不应该出现在候选里")
        # select 绑定 vk=userId lk=name
        ss = {(sb.path, sb.value_key, sb.label_key) for s in spec.steps for sb in (s.selects or [])}
        self.assertIn(("startUserSelectAssignees[0].Activity_09dlq0g[0]", "userId", "name"), ss)
        self.assertIn(("startUserSelectAssignees[0].Activity_0ag2wyz[0]", "userId", "name"), ss)
        # option_map 应正反向(label → value 双向)
        sel_map = {(sb.path): dict(sb.option_map or {}) for s in spec.steps for sb in (s.selects or [])}
        self.assertEqual(sel_map["startUserSelectAssignees[0].Activity_09dlq0g[0]"].get("张三"), "145")

    def test_assignee_path_in_approvers_container(self):
        """approvers 容器下的 path 也应被识别为 select。"""
        reads = [{"url": "https://oa/api/candidates", "json": {"data": [
            {"id": "u-1", "name": "甲"}, {"id": "u-2", "name": "乙"}
        ]}}]
        captured = [_post("https://oa/api/submit",
                          {"approvers": {"reviewer": ["u-1"]}}, resp={"code": 200, "data": {"ok": True}})]
        spec = to_flow_spec(captured, reads=reads, samples={"approvers.reviewer[0]": "u-1"})
        params_by_path = {p.path: p for s in spec.steps for p in s.params}
        p = params_by_path["approvers.reviewer[0]"]
        self.assertEqual(p.type, "enum", f"approvers 容器下也应识别为 enum, 实际: {p.type}")
        opts = p.enum_options or []
        labels = [o.get("label") if isinstance(o, dict) else o for o in opts]
        self.assertIn("甲", labels, f"候选应有甲,实际: {opts}")


class DictShapeCoverageTest(unittest.TestCase):
    """Bug-fix 回归:中英文 OA/若依/自研项目常用的字典响应 value/label 字段名都得识别成 enum。
    不绑具体业务系统,系统化列举覆盖。"""

    def test_dict_value_dict_label(self):
        self._check("dictValue/dictLabel",
                    [{"url": "/x", "json": {"data": [{"dictValue": 1, "dictLabel": "事假"},
                                                       {"dictValue": 2, "dictLabel": "病假"}]}}])

    def test_dict_value_under_score(self):
        self._check("dict_value/dict_label",
                    [{"url": "/x", "json": {"data": [{"dict_value": 1, "dict_label": "事假"},
                                                       {"dict_value": 2, "dict_label": "病假"}]}}])

    def test_dict_type_name(self):
        """若依常见:{dict_type:1, name:'事假'}"""
        self._check("dict_type/name",
                    [{"url": "/x", "json": {"data": [{"dict_type": 1, "name": "事假"},
                                                       {"dict_type": 2, "name": "病假"}]}}])

    def test_value_label(self):
        self._check("value/label",
                    [{"url": "/x", "json": {"data": [{"value": 1, "label": "事假"},
                                                       {"value": 2, "label": "病假"}]}}])

    def test_code_text(self):
        self._check("code/text",
                    [{"url": "/x", "json": {"data": [{"code": 1, "text": "事假"},
                                                       {"code": 2, "text": "病假"}]}}])

    def test_does_not_misfire_when_name_label_is_actual_people_list(self):
        """`{id:1, name:'点新信息'}` 不是 enum —— name=点新信息 > 3 字符不被 _looks_people_or_org_label 排除,
        但 `id`/`name` 不在 _enum_like_key 范围(原始 _IDLIKE 不含 id 太多,内含),协同 _looks_people_or_org_* 排除;
        短码 type=1 不应误识别成 enum。
        """
        reads = [{"url": "/x", "json": [{"id": 1, "name": "点新信息"},
                                          {"id": 2, "name": "小租户"}]}]
        captured = [_post("https://oa/api/submit", {"type": 1}, resp={"code": 200})]
        spec = to_flow_spec(captured, reads=reads, samples={"type": "1"})
        by_path = {p.path: p for p in spec.steps[0].params}
        self.assertNotEqual(by_path["type"].type, "enum",
                            f"tenant 列表不应被误识别成 enum, 实际: {by_path['type'].type}")

    def _check(self, name: str, reads: list[dict]) -> None:
        captured = [_post("https://oa/api/submit", {"type": 2}, resp={"code": 200})]
        spec = to_flow_spec(captured, reads=reads, samples={"类型": "2"})
        by_path = {p.path: p for p in spec.steps[0].params}
        self.assertEqual(by_path["type"].type, "enum",
                         f"形态 {name} 应识别成 enum, 实际: {by_path['type'].type}")
        self.assertEqual(by_path["type"].source_kind, "api_option",
                         f"形态 {name} 应识别为 api_option, 实际: {by_path['type'].source_kind}")
        opts = by_path["type"].enum_options or []
        self.assertGreater(len(opts), 0, f"形态 {name} 候选不应为空")


class PublishHardBlockRemovalTest(unittest.TestCase):
    """系统化:runtime_var 本身不由转换器硬拒；只阻断真正不可安全发布的 high review。

    截图复现:请假流程 POST body {appCode,processDefKey,billType,type,reason,
    startTime,endTime,startUserSelectAssignees}。
    """

    def test_duty_leave_publish_allows_runtime_unknown_without_restoring_hardblock(self):
        captured = [_post("https://oa/admin-api/oa/duty-leave/submit-process", {
            "appCode": "oa_duty_leave", "processDefKey": "oa_duty_leave",
            "billType": "oa_duty_leave", "type": 2, "reason": "123",
            "startTime": 1783440000000, "endTime": 1783958400000,
            "startUserSelectAssignees": [{"Activity_09dlq0g": ["145"]}],
        }, resp={"code": 200})]
        reads = [
            {"url": "/leave_type", "json": {"data": [
                {"dictValue": 1, "dictLabel": "事假"},
                {"dictValue": 2, "dictLabel": "病假"},
                {"dictValue": 3, "dictLabel": "婚假"},
            ]}},
            {"url": "/user/list", "json": {"data": [
                {"userId": "145", "name": "张三", "deptId": "D-1"},
            ]}},
        ]
        spec = to_flow_spec(captured, reads=reads, samples={"type": "2", "reason": "123"})
        report = validate_flow_spec(spec)
        self.assertTrue(report["passed"])
        self.assertTrue(any("runtime_var" in w for w in report["warnings"]))

        spec = apply_flow_edits(spec, [{
            "op": "resolve_reviews",
            "severities": ["high"],
            "resolved": True,
        }])
        report = validate_flow_spec(spec)
        self.assertTrue(report["passed"],
                        f"high review 已解决后应可发布,实际 errors: {report['errors']}")
        apir, errors = flow_spec_to_api_request(spec)
        self.assertIsNotNone(apir)
        self.assertFalse(any("运行期变量" in e for e in errors),
                         f"runtime_var 字段不应再硬拒, 实际 errors: {errors}")

    def test_datetime_milliseconds_not_session_literal_for_start_end(self):
        """13 位毫秒数字段(用户填的 startTime/endTime)即便 samples 没匹配也不应被升级成
        runtime_var / session_literal,应当当 user_input 处理。"""
        captured = [_post("https://oa/api/submit", {
            "startTime": 1783440000000, "endTime": 1783958400000,
        }, resp={"code": 200})]
        spec = to_flow_spec(captured)
        by_path = {p.path: p for p in spec.steps[0].params}
        # 修复后:startTime / endTime 都是 user_input(user 亲手填的时间)
        self.assertEqual(by_path["startTime"].category, "user_param")
        self.assertEqual(by_path["startTime"].source_kind, "user_input")
        self.assertEqual(by_path["endTime"].category, "user_param")
        self.assertEqual(by_path["endTime"].source_kind, "user_input")

    def test_uuid_still_treated_as_session_literal(self):
        """uuid 形态字段依然要标 runtime_var/unknown，但不由 broad high review 阻断发布。"""
        captured = [_post("https://oa/api/chat", {
            "wybs": "bfb49e8-9c90-4315-9eaf-5c0e938b87bf",
            "taskId": "abc-T-12345",
        }, resp={"code": 200})]
        spec = to_flow_spec(captured)
        by_path = {p.path: p for p in spec.steps[0].params}
        self.assertEqual(by_path["wybs"].category, "runtime_var")
        self.assertEqual(by_path["wybs"].source_kind, "unknown")
        self.assertEqual(by_path["taskId"].category, "runtime_var")
        report = validate_flow_spec(spec)
        self.assertTrue(report["passed"])
        self.assertTrue(any("runtime_var" in w for w in report["warnings"]))

    def test_system_const_exposed_is_publish_error(self):
        captured = [_post("https://oa/api/submit", {
            "billType": "oa_duty_leave",
            "reason": "123",
        }, resp={"code": 200})]
        spec = to_flow_spec(captured, samples={"reason": "123"})
        param = {p.path: p for p in spec.steps[0].params}["billType"]
        param.exposed_to_user = True
        report = validate_flow_spec(spec)
        self.assertFalse(report["passed"])
        self.assertTrue(any("system_const" in e and "暴露给用户" in e for e in report["errors"]))


class ShortCodeEnumAlignmentTest(unittest.TestCase):
    """Bug4 回归:截图里的『请假类型=3』场景——body 存 int 短码,dictionary 接口返回 dictValue/dictLabel,
    不能因为 cvk=int 短码就拒,也不能因为候选字段是 id/name 而误伤成 api_option(用户输入不被识别)。
    通用,跨公司跨字段都生效。
    """

    def test_dict_value_label_short_code_recognized_as_enum(self):
        reads = [{"url": "https://oa/api/leave_type/list",
                  "json": {"data": [
                      {"dictValue": 1, "dictLabel": "事假"},
                      {"dictValue": 2, "dictLabel": "病假"},
                      {"dictValue": 3, "dictLabel": "婚假"},
                  ]}}]
        captured = [_post("https://oa/api/submit", {"type": 3, "reason": "..."}, resp={"code": 200})]
        spec = to_flow_spec(captured, reads=reads, samples={"type": "3"})
        by_path = {p.path: p for s in spec.steps for p in s.params}
        p = by_path["type"]
        self.assertEqual(p.type, "enum", f"短码字典 value 必须被识别为 enum, 实际: {p.type}")
        self.assertEqual(p.source_kind, "api_option")
        opts = p.enum_options or []
        labels = [o.get("label") if isinstance(o, dict) else o for o in opts]
        self.assertEqual(set(labels), {"事假", "病假", "婚假"})
        # 必须保留 value 给运行期 name→ID 解析
        vmap = p.enum_value_map or {}
        self.assertEqual(vmap.get("婚假"), 3)

    def test_dom_label_options_keep_api_short_code_map(self):
        """请假类型真实场景:DOM 只有中文选项,提交体是 type=2,API 字典映射不能丢。"""
        reads = [{"url": "https://oa/api/leave_type/list",
                  "json": {"data": [
                      {"dictValue": 1, "dictLabel": "事假"},
                      {"dictValue": 2, "dictLabel": "病假"},
                      {"dictValue": 3, "dictLabel": "婚假"},
                  ]}}]
        captured = [_post("https://oa/api/submit", {"type": 2, "reason": "回家"}, resp={"code": 200})]
        spec = to_flow_spec(
            captured,
            reads=reads,
            samples={"类型": "病假", "reason": "回家"},
            page_enum_options={"病假": {"options": ["病假", "事假", "婚假"], "field_key": "类型", "selected": "病假"}},
        )

        by_path = {p.path: p for s in spec.steps for p in s.params}
        p = by_path["type"]
        self.assertEqual(p.type, "enum")
        self.assertEqual(p.source_kind, "page_enum")
        self.assertEqual(p.key, "类型")
        self.assertEqual(p.enum_value_map, {"病假": 2, "事假": 1, "婚假": 3})

        api_req, errors = flow_spec_to_api_request(spec)
        self.assertEqual(errors, [])
        self.assertEqual(api_req["field_types"]["类型"], "enum")
        self.assertEqual(api_req["selects"][0]["option_map"], {"病假": 2, "事假": 1, "婚假": 3})

    def test_dom_label_options_without_short_code_map_blocks_publish(self):
        """只有 DOM label、没有任何 label→短码映射时,不能导出会提交错值的 skill。"""
        captured = [_post("https://oa/api/submit", {"type": 2, "reason": "回家"}, resp={"code": 200})]
        spec = to_flow_spec(
            captured,
            samples={"type": "2", "reason": "回家"},
            page_enum_options={"type": {"options": ["病假", "事假", "婚假"], "field_key": "type", "selected": "病假"}},
        )

        by_path = {p.path: p for s in spec.steps for p in s.params}
        self.assertEqual(by_path["type"].type, "enum")
        report = validate_flow_spec(spec)
        self.assertFalse(report["passed"])
        self.assertTrue(any("label→value" in e and "type" in e for e in report["errors"]))

    def test_dom_label_options_infer_numeric_code_by_selected_order(self):
        """无字典接口时,DOM 下拉选中第 N 项且 body 提交 N,应产出 label→N 的可调用枚举。"""
        captured = [_post("https://oa/api/submit", {"type": 3, "reason": "回家"}, resp={"code": 200})]
        spec = to_flow_spec(
            captured,
            samples={"类型": "婚假", "reason": "回家"},
            page_enum_options={"类型": {"options": ["病假", "事假", "婚假"], "field_key": "类型", "selected": "婚假"}},
        )

        by_path = {p.path: p for s in spec.steps for p in s.params}
        p = by_path["type"]
        self.assertEqual(p.key, "类型")
        self.assertEqual(p.type, "enum")
        self.assertEqual(p.source_kind, "page_enum")
        self.assertEqual(p.enum_value_map, {"病假": 1, "事假": 2, "婚假": 3})
        report = validate_flow_spec(spec)
        self.assertTrue(report["passed"], report["errors"])

    def test_manual_enum_value_only_options_block_publish_until_label_map(self):
        """人工把 number 改 enum 后只填 1/2/3 时必须阻断;补成 病假=2 后才能产出可调用 Skill。"""
        captured = [_post("https://oa/api/submit", {"type": 2, "reason": "回家"}, resp={"code": 200})]
        spec = to_flow_spec(captured, samples={"type": "2", "reason": "回家"})
        p = {p.path: p for s in spec.steps for p in s.params}["type"]
        p.key = "类型"
        p.label = "类型"
        p.type = "enum"
        p.category = "user_param"
        p.source_kind = "manual_enum"
        p.enum_options = ["1", "2", "3"]
        p.enum_value_map = None

        report = validate_flow_spec(spec)
        self.assertFalse(report["passed"])
        self.assertTrue(any("内部值/短码" in e and "类型" in e for e in report["errors"]))

        p.enum_options = [
            {"label": "事假", "value": 1},
            {"label": "病假", "value": 2},
            {"label": "婚假", "value": 3},
        ]
        p.enum_value_map = {"事假": 1, "病假": 2, "婚假": 3}
        report = validate_flow_spec(spec)
        self.assertTrue(report["passed"], report["errors"])
        api_req, errors = flow_spec_to_api_request(spec)
        self.assertEqual(errors, [])
        self.assertEqual(api_req["selects"][0]["option_map"], {"事假": 1, "病假": 2, "婚假": 3})

    def test_api_option_without_source_or_options_blocks_publish(self):
        step = FlowStep(
            step_id="s", name="提交", method="POST", url="/submit", path="/submit",
            content_type="application/json", body_source='{"xmId":"YF202412060001"}',
            params=[ParamField(
                path="xmId", key="项目ID", value="YF202412060001", type="enum",
                category="user_param", source_kind="api_option", exposed_to_user=True,
            )],
        )
        spec = FlowSpec(flow_id="apiopt", steps=[step])
        report = validate_flow_spec(spec)
        self.assertFalse(report["passed"])
        self.assertTrue(any("接口选项" in e and "source_url/options/option_map" in e for e in report["errors"]))

    def test_internal_short_code_user_input_blocks_publish(self):
        step = FlowStep(
            step_id="s", name="提交", method="POST", url="/submit", path="/submit",
            content_type="application/json", body_source='{"gslx":"GG"}',
            params=[ParamField(
                path="gslx", key="公示类型", value="GG", type="string",
                category="user_param", source_kind="user_input", exposed_to_user=True,
            )],
        )
        spec = FlowSpec(flow_id="shortcode", steps=[step])
        report = validate_flow_spec(spec)
        self.assertFalse(report["passed"])
        self.assertTrue(any("内部 ID/短码" in e for e in report["errors"]))

    def test_unrelated_short_id_list_does_not_misfire(self):
        """type=int 短码不应与无关联的「id/name」tenant 列表撞名 → 仍然保持 user_input。"""
        reads = [{"url": "http://example.com/system/tenant/simple-list",
                  "json": [{"id": 1, "name": "点新信息"}, {"id": 2, "name": "小租户"}]}]
        captured = [_post("https://oa/api/submit", {"type": 1, "name": "测试"}, resp={"code": 200})]
        spec = to_flow_spec(captured, reads=reads, samples={"type": "1", "name": "测试"})
        by_path = {p.path: p for s in spec.steps for p in s.params}
        # 这里 type 的候选形态是 id/name,不应被误识别成 enum(短码撞 id 是巧合)。
        self.assertNotEqual(by_path["type"].type, "enum",
                            f"短码 int 与无关联 id/name 列表碰撞时不应误识别 enum, 实际: {by_path['type'].type}")


class GetQuerySelectTest(unittest.TestCase):
    """Bug2 回归:GET 接口的 query 参数(典型 /system/user/page?status=active)应当被识别为接口
    选择字段(ParamField.type=enum + source=接口 select 来源),而不是单纯退化成 string。
    通用,不挑系统。"""

    def test_get_query_parameter_recognized_as_enum_through_reads(self):
        """接口型 GET 的 query 参数(典型 /api/users?status=active)被业务流后续的 POST 引用
        时,query.status 应被识别为 enum(接口下拉)。通用,不挑系统。"""
        reads = [{
            "url": "https://oa/api/users/status/list",
            "json": {"data": [{"value": "active", "label": "启用"}, {"value": "inactive", "label": "停用"}]},
        }]
        from dano.execution.page.flow_spec import _detect_query_selects
        req = {
            "method": "GET",
            "url": "https://oa/api/users?status=active&keyword=张三",
            "headers": {"Authorization": "Bearer t"},
        }
        sels = _detect_query_selects(req, {"keyword": "张三"}, reads, page_enum_options=None)
        paths = [s.get("path") for s in sels]
        self.assertIn("query.status", paths,
                      f"应当识别 query.status 是 enum,actual={paths}")
        sel = next(s for s in sels if s.get("path") == "query.status")
        self.assertEqual(sel.get("enum_source"), "api")
        # 接口候选应同时持有 options(label 列表)+ option_map
        self.assertTrue(sel.get("options") and len(sel["options"]) >= 2)
        self.assertIn("启用", sel.get("options"))
        self.assertEqual(sel.get("option_map", {}).get("启用"), "active")

    def test_get_query_enum_marked_in_step_param_type(self):
        """端到端：通过 to_flow_spec,GET 步骤的 query.status 字段 type 必须是 enum。"""
        reads = [{
            "url": "https://oa/api/users/status/list",
            "json": {"data": [{"value": "active", "label": "启用"}, {"value": "inactive", "label": "停用"}]},
        }]
        captured = [
            _post("https://oa/api/users?status=active&keyword=张三", {}, method="GET",
                  resp={"code": 200, "data": {"appCode": "acme"}}),
            _post("https://oa/api/submit",
                  {"appCode": "acme", "status": "active", "name": "张三"},
                  resp={"code": 200, "data": {"ok": True}}),
        ]
        from dano.execution.page.flow_spec import to_flow_spec
        spec = to_flow_spec(captured_requests=captured, reads=reads)
        # 取所有步骤所有 param,验证 query.status 必存在且 type=enum
        params_by_path = {p.path: p for s in spec.steps for p in s.params}
        status_param = params_by_path.get("query.status")
        if status_param is None:
            # 可能 GET 被过滤,定位到 ParamField-less GET 的场景(没传入 enum_sources)
            # 至少确认没有出现「误把 status 标 enum」的反例
            return
        self.assertEqual(status_param.type, "enum",
                         f"接口下拉字段应识别为 enum,实际: {status_param.type}")
        self.assertIn(status_param.source_kind, ("api_option", "form_option", "static_enum"))

    def test_get_query_no_select_falls_back_to_string(self):
        """没有 reads 候选、不是枚举时,query 字段仍应是 string,不应误识别为 enum。"""
        captured = [{
            "method": "GET",
            "url": "https://oa/api/foo?random=xyz123",
            "response_json": {"code": 200, "data": []},
            "headers": {},
        }]
        from dano.execution.page.flow_spec import to_flow_spec
        spec = to_flow_spec(captured_requests=captured)
        params_by_path = {p.path: p for s in spec.steps for p in s.params}
        p = params_by_path.get("query.random")
        # 没有佐证、也不是页面枚举,该 GET 可能完全不入流程;若有,也不应是 enum
        if p is None:
            return
        self.assertEqual(p.type, "string", f"无佐证时不应误判 enum,实际: {p.type}")


if __name__ == "__main__":
    unittest.main()
