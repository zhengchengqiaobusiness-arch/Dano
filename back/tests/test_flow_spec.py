"""Step A · FlowSpec 收敛函数测试。"""

from __future__ import annotations

import json
import unittest

from dano.execution.page.flow_spec import (
    FlowSpec, FlowStep, FlowLink, ParamField,
    to_flow_spec, flow_spec_to_summary,
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
        self.assertIsNotNone(st.success_rule)
        self.assertEqual(spec.risk_level, "L3")
        self.assertEqual(spec.links, [])

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
        for sv in st.system_values:
            if sv.path == "submitTime":
                self.assertEqual(sv.kind, "now_ms")
            elif sv.path == "createTime":
                self.assertEqual(sv.kind, "now_s")

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


if __name__ == "__main__":
    unittest.main()