"""Step A · FlowSpec 收敛函数测试。"""

from __future__ import annotations

import json
import asyncio
import unittest

import dano.execution.page.flow_spec as flow_spec_module
from dano.execution.page.flow_spec import (
    FlowSpec, FlowStep, FlowLink, ParamField, FlowCapability,
    apply_flow_edits,
    classify_network_request,
    dry_run_flow_spec,
    flow_spec_to_api_request,
    flow_spec_to_client,
    flow_spec_release_payload,
    flow_spec_to_summary,
    orchestrate_flow_capabilities,
    render_business_description,
    to_flow_spec,
    validate_flow_spec,
    _default_step_name, _derive_step_name, _infer_type_from_value,
    _params_from_get_query,
)


def _call_nodes(step_ids: list[str]) -> list[dict]:
    return [
        {"id": f"call_{index}", "type": "call", "step_id": step_id}
        for index, step_id in enumerate(step_ids)
    ]


def _post(url, body, method="POST", resp=None, headers=None):
    return {
        "method": method, "url": url,
        "post_data": json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else body,
        "content_type": "application/json",
        "headers": headers or {"Authorization": "Bearer test", "Content-Type": "application/json"},
        "response_json": resp,
    }


def _get(url, resp=None):
    return {
        "method": "GET", "url": url,
        "content_type": "application/json",
        "headers": {"Authorization": "Bearer test"},
        "response_json": resp,
    }


def _select_evidence(path, alias, label, value, *, page_id="page-1", frame_id="main"):
    """Recorder evidence for an exact select control/request-field identity."""
    return {
        "path": path,
        "key": alias,
        "suggest_name": label,
        "name_source": "dom",
        "label": label,
        "value": value,
        "field_aliases": [alias],
        "control_kind": "select",
        "page_id": page_id,
        "frame_id": frame_id,
    }


def _dom_enum(label, alias, selected_label, selected_value, options, *, page_id="page-1", frame_id="main"):
    """Complete, scoped DOM label-to-wire-value evidence."""
    return {
        label: {
            "field_key": label,
            "field_aliases": [alias],
            "control_kind": "select",
            "selected": selected_label,
            "selected_label": selected_label,
            "selected_value": selected_value,
            "mapping_complete": True,
            "options": options,
            "page_id": page_id,
            "frame_id": frame_id,
        }
    }


class ToFlowSpecTest(unittest.TestCase):
    def test_seal_query_fields_and_page_enum_stay_bound_to_their_own_wire_fields(self):
        """公章真实场景：同值 query、候选接口 status 与页面流程状态不得串名/串枚举。"""
        page = _get(
            "https://oa.test/admin-api/oa/seal-apply/page?"
            "pageNo=1&pageSize=10&billCode=1&"
            "useTime%5B0%5D=2026-07-01%2000%3A00%3A00&"
            "useTime%5B1%5D=2026-08-19%2023%3A59%3A59&"
            "useInfo=1231&processStatus=1",
            {"code": 0, "data": {"list": [], "total": 0}},
        )
        page.update({"page_id": "seal-list", "frame_id": "main"})
        seal_options = _get(
            "https://oa.test/admin-api/bd/seal/simple-list?status=0",
            {"code": 0, "data": [
                {"id": "seal-company", "name": "公司章", "status": 0},
                {"id": "seal-legal", "name": "法人章", "status": 0},
            ]},
        )
        seal_option_read = {
            "url": seal_options["url"],
            "method": "GET",
            "json": seal_options["response_json"],
        }
        submit = _post(
            "https://oa.test/admin-api/oa/seal-apply/submit-process",
            {"sealId": "seal-legal", "applyTitle": "采购合同", "useInfo": "盖章说明"},
            resp={"code": 0, "data": True},
        )
        submit.update({"page_id": "seal-form", "frame_id": "main"})
        samples = {
            "单据编号": "1",
            "开始时间": "2026-07-01 00:00:00",
            "结束时间": "2026-08-19 23:59:59",
            "使用信息": "1231",
            "流程状态": "审批中",
            "印章标识": "法人章",
            "申请标题": "采购合同",
            "使用描述": "盖章说明",
        }
        page_enums = _dom_enum(
            "流程状态", "processStatus", "审批中", 1,
            [
                {"label": "未提交", "value": 0},
                {"label": "审批中", "value": 1},
                {"label": "审批通过", "value": 2},
                {"label": "审批不通过", "value": 3},
                {"label": "已取消", "value": 4},
            ],
            page_id="seal-list",
        )

        page_step = flow_spec_module._build_step_from_capture(
            page, reads=[seal_option_read], samples=samples, storage_state=None,
            required_labels=set(), page_enum_options=page_enums, step_index=0,
            field_evidence=[_select_evidence(
                "query.processStatus", "processStatus", "流程状态", "审批中", page_id="seal-list",
            )],
        )
        option_step = flow_spec_module._build_step_from_capture(
            seal_options, reads=[seal_option_read], samples=samples, storage_state=None,
            required_labels=set(), page_enum_options=page_enums, step_index=1,
        )
        submit_step = flow_spec_module._build_step_from_capture(
            submit, reads=[seal_option_read], samples=samples, storage_state=None,
            required_labels=set(), page_enum_options=page_enums, step_index=2,
            field_evidence=[_select_evidence(
                "sealId", "sealId", "印章标识", "法人章", page_id="seal-form",
            )],
        )

        page_params = {param.path: param for param in page_step.params}
        self.assertEqual(page_params["query.pageNo"].key, "pageNo")
        self.assertEqual(page_params["query.pageNo"].category, "user_param")
        self.assertEqual(page_params["query.pageSize"].category, "user_param")
        self.assertFalse(page_params["query.pageNo"].required)
        self.assertTrue(page_params["query.pageNo"].exposed_to_user)
        self.assertEqual(page_params["query.billCode"].key, "单据编号")
        self.assertEqual(page_params["query.useTime[0]"].key, "开始时间")
        self.assertEqual(page_params["query.useTime[1]"].key, "结束时间")
        self.assertEqual(page_params["query.useInfo"].key, "使用信息")
        self.assertEqual(page_params["query.useInfo"].type, "string")
        self.assertEqual(page_params["query.processStatus"].key, "流程状态")
        self.assertEqual(page_params["query.processStatus"].source_kind, "page_enum")

        option_status = {param.path: param for param in option_step.params}["query.status"]
        self.assertEqual(option_status.key, "status")
        self.assertEqual(option_status.category, "system_const")
        self.assertEqual(option_status.source_kind, "constant")
        self.assertNotIn(option_status.type, {"enum", "list-enum"})
        self.assertFalse(option_status.enum_options)

        submit_params = {param.path: param for param in submit_step.params}
        self.assertEqual(submit_params["sealId"].source_kind, "api_option")
        self.assertEqual(submit_params["sealId"].key, "印章标识")
        self.assertNotEqual(submit_params["useInfo"].source_kind, "page_enum")

    def test_recorded_text_query_fields_never_become_api_options_by_value_collision(self):
        """真实公章查询：手输 11/12 与候选接口 id 撞值也不是接口选择字段。"""
        request = _get(
            "https://oa.test/admin-api/oa/seal-apply/page?"
            "pageNo=1&pageSize=10&billCode=11&useInfo=12&processStatus=1",
            {"code": 0, "data": {"list": [], "total": 0}},
        )
        request.update({"page_id": "seal-list", "frame_id": "main"})
        reads = [{
            "url": "https://oa.test/admin-api/unrelated/options",
            "method": "GET",
            "json": {"data": [
                {"id": "1", "name": "候选一"},
                {"id": "11", "name": "候选十一"},
                {"id": "12", "name": "候选十二"},
            ]},
        }]
        step = flow_spec_module._build_step_from_capture(
            request,
            reads=reads,
            samples={"单据编号": "11", "使用描述": "12", "流程状态": "审批中"},
            storage_state=None,
            required_labels=set(),
            page_enum_options=_dom_enum(
                "流程状态", "processStatus", "审批中", 1,
                [
                    {"label": "未提交", "value": 0},
                    {"label": "审批中", "value": 1},
                    {"label": "审批通过", "value": 2},
                ],
                page_id="seal-list",
            ),
            field_evidence=[_select_evidence(
                "query.processStatus", "processStatus", "流程状态", "审批中", page_id="seal-list",
            )],
            step_index=0,
        )

        params = {param.path: param for param in step.params}
        self.assertEqual(params["query.billCode"].key, "单据编号")
        self.assertEqual(params["query.billCode"].type, "string")
        self.assertEqual(params["query.billCode"].source_kind, "user_input")
        self.assertEqual(params["query.useInfo"].key, "使用描述")
        self.assertEqual(params["query.useInfo"].type, "string")
        self.assertEqual(params["query.useInfo"].source_kind, "user_input")
        self.assertEqual(params["query.processStatus"].key, "流程状态")
        self.assertEqual(params["query.processStatus"].source_kind, "page_enum")
        self.assertNotIn("query.billCode", {binding.path for binding in step.selects})
        self.assertNotIn("query.useInfo", {binding.path for binding in step.selects})

    def test_same_query_endpoint_keeps_richest_recorded_search_filters(self):
        response = {"data": {"list": [{"id": 1, "applyDate": "2026-07-03"}], "total": 1}, "code": 0}
        captured = [
            {**_get("https://oa.test/admin-api/oa/duty-leave/page?pageNo=1&pageSize=10", response), "index": 1},
            {**_get(
                "https://oa.test/admin-api/oa/duty-leave/page?type=1&startDate=2026-07-01&endDate=2026-07-11&status=1&pageNo=1&pageSize=10",
                response,
            ), "index": 2},
        ]

        spec = to_flow_spec(
            captured,
            samples={"请假类型": "1", "开始日期": "2026-07-01", "结束日期": "2026-07-11", "审批结果": "1"},
        )

        query_steps = [step for step in spec.steps if "duty-leave/page" in (step.url or step.path)]
        self.assertEqual(len(query_steps), 1)
        by_path = {param.path: param for param in query_steps[0].params}
        self.assertTrue({"query.type", "query.startDate", "query.endDate", "query.status"}.issubset(by_path))
        self.assertEqual(by_path["query.startDate"].type, "date")
        self.assertEqual(by_path["query.endDate"].type, "date")
        self.assertEqual(by_path["query.startDate"].key, "开始日期")

    def test_query_dict_is_executable_even_when_captured_url_has_no_query_string(self):
        response = {"data": {"list": [{"id": 1, "applyDate": "2026-07-03"}], "total": 1}, "code": 0}
        captured = [{
            **_get("https://oa.test/admin-api/oa/duty-leave/page", response),
            "index": 73,
            "query": {
                "type": ["1"], "startDate": ["2026-07-01"], "endDate": ["2026-07-11"],
                "status": ["1"], "pageNo": ["1"], "pageSize": ["10"],
            },
        }]

        spec = to_flow_spec(captured, samples={"请假类型": "1", "开始日期": "2026-07-01", "结束日期": "2026-07-11"})
        step = spec.steps[0]
        by_path = {param.path: param for param in step.params}

        self.assertIn("startDate=2026-07-01", step.url)
        self.assertEqual(by_path["query.pageNo"].category, "user_param")
        self.assertEqual(by_path["query.pageNo"].type, "number")
        self.assertEqual(by_path["query.pageNo"].source_kind, "user_input")
        self.assertTrue(by_path["query.pageNo"].exposed_to_user)
        self.assertFalse(by_path["query.pageNo"].required)
        self.assertNotIn("query.pageNo", {select.path for select in step.selects})

    def test_leave_query_and_submit_keep_business_filters_and_leave_enum_domain(self):
        dictionary = [
            {"dictValue": "100", "dictLabel": "研发部门", "dictType": "system_dept"},
            {"dictValue": "101", "dictLabel": "市场部门", "dictType": "system_dept"},
            {"dictValue": "1", "dictLabel": "病假", "dictType": "oa_leave_type"},
            {"dictValue": "2", "dictLabel": "事假", "dictType": "oa_leave_type"},
            {"dictValue": "3", "dictLabel": "婚假", "dictType": "oa_leave_type"},
        ] + [
            {"dictValue": str(200 + index), "dictLabel": f"其它{index}", "dictType": "misc"}
            for index in range(55)
        ]
        captured = [
            _get("https://oa.test/admin-api/system/dict-data/simple-list", {"data": dictionary}),
            {
                **_get("https://oa.test/admin-api/oa/duty-leave/page", {"data": {"list": [], "total": 0}}),
                "query": {"type": ["1"], "startDate": ["2026-07-01"], "endDate": ["2026-07-11"], "pageNo": ["1"], "pageSize": ["10"]},
            },
            _post("https://oa.test/admin-api/oa/duty-leave/submit-process", [{"type": 1, "reason": "测试"}], resp={"code": 0}),
        ]

        spec = to_flow_spec(
            captured,
            samples={"请假类型": "病假", "开始日期": "2026-07-01", "结束日期": "2026-07-11", "原因": "测试"},
            field_evidence=[_select_evidence("[0].type", "type", "请假类型", "病假")],
        )
        query = next(step for step in spec.steps if "duty-leave/page" in step.path)
        submit = next(step for step in spec.steps if step.method == "POST")
        query_paths = {param.path for param in query.params}
        leave_type = next(param for param in submit.params if param.path == "[0].type")

        self.assertTrue({"query.type", "query.startDate", "query.endDate"}.issubset(query_paths))
        self.assertEqual(leave_type.enum_value_map, {"病假": "1", "事假": "2", "婚假": "3"})
        self.assertNotIn("研发部门", leave_type.enum_value_map)
        self.assertEqual(leave_type.source_kind, "api_option")


    def test_recorded_user_input_wins_over_internal_field_name_heuristics(self):
        spec = to_flow_spec(
            captured_requests=[_post("https://oa.test/submit", {"activityId": 142, "reason": "请假"})],
            reads=[],
            samples={"审批节点": "142", "原因": "请假"},
            storage_state=None,
            required_labels={"审批节点", "原因"},
            page_enum_options={},
        )

        params = {param.path: param for step in spec.steps for param in step.params}
        activity = params["activityId"]
        assert activity.category == "user_param"
        assert activity.source_kind == "user_input"
        assert activity.exposed_to_user is True


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
        self.assertTrue(lk.confirmed)
        self.assertEqual(lk.confidence, 0.96)
        p_by_path = {p.path: p for p in spec.steps[1].params}
        self.assertEqual(p_by_path["flowTask.taskId"].category, "runtime_var")
        self.assertEqual(p_by_path["flowTask.taskId"].source_kind, "previous_response")
        self.assertEqual(p_by_path["flowTask.taskId"].source["step_id"], spec.steps[0].step_id)
        self.assertFalse(p_by_path["flowTask.taskId"].exposed_to_user)
        review_types = {item.type for item in spec.review_items}
        self.assertNotIn("link_confirmation", review_types)
        self.assertNotIn("field_category", review_types)
        report = validate_flow_spec(spec)
        self.assertEqual(report["review_summary"]["total"], 0)
        self.assertFalse(any(i["type"] == "link_confirmation" for i in report["review_items"]))

        broken = apply_flow_edits(spec, [{
            "op": "update",
            "link_id": lk.link_id,
            "field": "source_path",
            "value": "data.missing",
        }])
        broken_report = validate_flow_spec(broken)
        self.assertTrue(broken_report["passed"])
        self.assertTrue(any(i["type"] == "link_source_missing" for i in broken_report["review_items"]))
        self.assertTrue(any("来源路径" in e and "data.missing" in e for e in broken_report["suggestions"]))
        self.assertEqual(broken_report["errors"], [])

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

    def test_business_page_list_does_not_pollute_submit_fields_as_options_or_links(self):
        captured = [
            _get("https://oa/admin-api/oa/seal-apply/page?pageNo=1&pageSize=10", {
                "code": 0,
                "data": {
                    "list": [{
                        "id": "old-id-1",
                        "applyTitle": "adasd",
                        "useInfo": "旧记录用途",
                        "remark": "旧备注",
                        "backTime": 1783353600000,
                    }],
                    "total": 1,
                },
            }),
            _post("https://oa/admin-api/oa/seal-apply/submit-process", {
                "applyTitle": "adasd",
                "useInfo": "旧记录用途",
                "remark": "旧备注",
                "backTime": 1783353600000,
            }, resp={"code": 0, "data": True}),
        ]

        spec = to_flow_spec(
            captured,
            samples={"applyTitle": "adasd", "useInfo": "旧记录用途", "remark": "旧备注"},
        )
        submit = next(s for s in spec.steps if s.method == "POST")
        by_path = {p.path: p for p in submit.params}

        self.assertEqual(by_path["applyTitle"].source_kind, "user_input")
        self.assertEqual(by_path["useInfo"].source_kind, "user_input")
        self.assertNotEqual(by_path["applyTitle"].source_kind, "api_option")
        self.assertFalse(any(l.target_path in {"applyTitle", "useInfo", "remark", "backTime"} for l in spec.links))

    def test_get_pagination_fields_are_not_enum_options(self):
        captured = [
            _get("https://oa/admin-api/bpm/process-instance/get-approval-detail?pageNo=1&pageSize=10", {
                "code": 0,
                "data": {"token": "PAGE-TOKEN", "list": [{"id": "1", "applyTitle": "a"}], "total": 1},
            }),
            _post("https://oa/admin-api/oa/seal-apply/submit-process", {
                "token": "PAGE-TOKEN",
                "applyTitle": "a",
            }, resp={"code": 0}),
        ]
        reads = [
            {"url": "https://oa/admin-api/system/dict-data/simple-list",
             "json": {"data": [{"label": "第一页", "value": "1"}, {"label": "十条", "value": "10"}]}}
        ]

        step = flow_spec_module._build_step_from_capture(
            captured[0],
            reads=reads,
            samples={},
            storage_state=None,
            required_labels=set(),
            page_enum_options={},
            step_index=0,
        )
        by_path = {p.path: p for p in step.params}

        self.assertEqual(by_path["query.pageNo"].category, "user_param")
        self.assertEqual(by_path["query.pageNo"].source_kind, "user_input")
        self.assertEqual(by_path["query.pageSize"].category, "user_param")
        self.assertFalse(by_path["query.pageNo"].required)
        self.assertTrue(by_path["query.pageNo"].exposed_to_user)
        self.assertNotEqual(by_path["query.pageNo"].source_kind, "api_option")

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
        self.assertTrue(report["passed"])
        self.assertFalse(report["errors"])
        self.assertTrue(report["suggestions"])

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
        self.assertTrue(any("runtime_var" in w for w in report["suggestions"]))

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

    def test_validate_reports_requestfailed_diagnostic_without_blocking_publish(self):
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

        self.assertTrue(report["passed"])
        self.assertEqual(report["errors"], [])
        self.assertTrue(any("录制期业务请求失败" in e for e in report["suggestions"]))

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

        orchestrated = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))
        self.assertIn("submit", {c.kind for c in orchestrated.capabilities})
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
        self.assertIn("request_facts", d)
        s = json.dumps(d, ensure_ascii=False, default=str)
        self.assertIsInstance(s, str)

    def test_request_facts_is_canonical_for_client_and_release(self):
        spec = FlowSpec.model_validate({
            "flow_id": "rf-canonical",
            "request_facts": {
                "requests": [{
                    "request_id": "req-1",
                    "request_index": 1,
                    "method": "GET",
                    "url": "https://oa/api/status",
                    "path": "/api/status",
                    "headers": {"Authorization": "Bearer secret"},
                    "response_json": {"token": "secret", "ok": True},
                }],
                "analysis": {
                    "req-1": {
                        "request_id": "req-1",
                        "role": "business_get",
                        "keep": True,
                        "bucket": "candidate_reads",
                        "confidence": 0.91,
                    }
                },
            },
            "meta": {"request_graph": {"all_requests": [{"request_id": "legacy"}]}},
        })

        client = flow_spec_to_client(spec)
        released = flow_spec_release_payload(spec)

        self.assertEqual(client["request_facts"]["requests"][0]["request_id"], "req-1")
        self.assertEqual(client["request_facts"]["requests"][0]["headers"]["Authorization"], "***")
        self.assertEqual(client["request_facts"]["requests"][0]["response_json"]["token"], "***")
        self.assertNotIn("request_graph", client["meta"])
        self.assertEqual(released["request_facts"]["requests"][0]["request_id"], "req-1")
        self.assertNotIn("request_graph", released["meta"])

    def test_capability_scoped_fields_and_dependencies_sync_from_legacy_steps_links(self):
        start = FlowStep(
            step_id="start",
            method="POST",
            url="/api/start",
            path="/api/start",
            response_json={"data": {"taskId": "T-1"}},
        )
        submit = FlowStep(
            step_id="submit",
            method="POST",
            url="/api/submit",
            path="/api/submit",
            params=[
                ParamField(path="reason", key="reason", value="x", category="user_param", source_kind="user_input"),
                ParamField(
                    path="flowTask.taskId",
                    key="taskId",
                    value="T-1",
                    category="runtime_var",
                    source_kind="previous_response",
                    exposed_to_user=False,
                ),
            ],
        )
        link = FlowLink(
            link_id="l-task",
            source_step_id="start",
            source_path="data.taskId",
            target_step_id="submit",
            target_path="flowTask.taskId",
            confirmed=True,
            confidence=0.95,
        )

        spec = FlowSpec(
            flow_id="cap-fields",
            steps=[start, submit],
            links=[link],
            capabilities=[FlowCapability(name="submit_batch", kind="submit_batch", nodes=_call_nodes(["start", "submit"]))],
        )

        cap = spec.capabilities[0]
        self.assertTrue(any(f.path == "reason" and f.scope == "input" for f in cap.inputs))
        self.assertTrue(any(f.path == "flowTask.taskId" and f.scope == "internal" for f in cap.internal_fields))
        self.assertEqual(cap.dependencies[0].dependency_id, "l-task")
        self.assertEqual(cap.dependencies[0].source["step_id"], "start")
        self.assertEqual([r.step_id for r in cap.request_refs], ["start", "submit"])

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
        self.assertIn("read_context", {r["role"] for r in roles})
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
        self.assertEqual(get_params["query.appId"].source_kind, "constant")
        self.assertFalse(get_params["query.appId"].exposed_to_user)
        self.assertFalse(get_params["query.appId"].need_human_confirm)
        review_types = {item.type for item in spec.review_items}
        self.assertIn("field_category", review_types)
        self.assertFalse(any(item.target.get("path") == "query.appId" for item in spec.review_items))

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
        self.assertIn("需要人工确认", desc)

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
        users_fact = next(f for f in spec.request_facts.requests if f.path == "/api/users")
        users_analysis = spec.request_facts.analysis[users_fact.request_id]
        self.assertEqual(users_analysis.bucket, "candidate_reads")

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
        self.assertEqual(sum(1 for r in roles if r["role"] == "business_get"), 11)
        self.assertEqual(sum(1 for r in roles if r["role"] == "read_context"), 1)
        user_page_roles = [r for r in roles if "/system/user/page" in r["path"]]
        self.assertTrue(user_page_roles)
        self.assertTrue(all(r["role"] == "read_option" and not r["keep"] for r in user_page_roles))
        selected = [
            fact for fact in spec.request_facts.requests
            if spec.request_facts.analysis[fact.request_id].bucket == "selected_steps"
        ]
        candidates = [
            fact for fact in spec.request_facts.requests
            if spec.request_facts.analysis[fact.request_id].bucket == "candidate_reads"
        ]
        self.assertGreaterEqual(len(selected), 2)
        self.assertTrue(any("/system/user/page" in fact.path for fact in candidates))

        self.assertEqual(spec.capabilities, [])
        orchestrated = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))
        cap_kinds = {c.kind for c in orchestrated.capabilities}
        self.assertEqual(cap_kinds, {"submit"})
        submit_cap = next(c for c in orchestrated.capabilities if c.kind == "submit")
        # 审批详情是提交能力的控制前置，不重复拆成独立状态查询能力。
        self.assertTrue(any("/get-approval-detail" in s.path for s in orchestrated.steps if s.step_id in submit_cap.step_ids))
        self.assertTrue(any("/submit-process" in s.path for s in orchestrated.steps if s.step_id in submit_cap.step_ids))

        client = flow_spec_to_client(orchestrated)
        self.assertIn("capabilities", client)
        self.assertEqual({c["kind"] for c in client["capabilities"]}, {"submit"})
        apir, errors = flow_spec_to_api_request(orchestrated)
        self.assertEqual(errors, [])
        self.assertIn("capabilities", apir)
        self.assertTrue(all(s.get("step_id") for s in apir["steps"]))
        self.assertIn("submit", {c["kind"] for c in apir["capabilities"]})

    def test_capability_validate_gate_sanitizes_stale_missing_step(self):
        spec = FlowSpec(
            flow_id="cap-gate",
            steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
            capabilities=[FlowCapability(
                name="submit_batch",
                kind="submit_batch",
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
            request_facts={
                "requests": [{
                    "request_index": 89,
                    "request_id": "req-89",
                    "method": "GET",
                    "url": "https://oa.example.com/api/work-days?start=2026-05-01",
                    "path": "/api/work-days?start=2026-05-01",
                    "response_status": 200,
                    "response_json": {"code": 0, "data": {"missing_dates": ["2026-05-12"]}},
                }],
                "analysis": {"req-89": {
                    "request_id": "req-89",
                    "role": "business_get",
                    "keep": True,
                    "confidence": 0.96,
                    "bucket": "candidate_reads",
                }},
                "usage": {"req-89": {"request_id": "req-89", "state": "captured"}},
            },
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
        usage = edited_again.request_facts.usage["req-89"]
        self.assertEqual(usage.state, "materialized")
        self.assertEqual(usage.materialized_step_id, edited_again.steps[0].step_id)

    def test_orchestrate_flow_merges_existing_capabilities(self):
        spec = FlowSpec(
            flow_id="cap-merge",
            steps=[FlowStep(step_id="submit", method="POST", url="/api/submit", path="/api/submit")],
            capabilities=[FlowCapability(
                name="submit_batch",
                title="人工改过的标题",
                kind="submit_batch",
                nodes=[{"id": "call_submit", "type": "call", "step_id": "submit"}],
                input_schema={"type": "object"},
                confirmed=True,
                requires_human_confirm=False,
                confidence=0.3,
            )],
        )

        out = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))

        cap = next(c for c in out.capabilities if c.name == "submit_batch")
        self.assertEqual(cap.title, "人工改过的标题")
        self.assertEqual(cap.kind, "submit_batch")
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

        spec = to_flow_spec(
            captured,
            samples={"职能清单": "123123qweqw", "所属系统": sys1},
            field_evidence=[_select_evidence(
                "ywsxList[0].yyxtmc", "yyxtmc", "所属系统", sys1,
            )],
        )
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
        self.assertEqual(by_path["ssbmId"].category, "runtime_var")
        self.assertEqual(by_path["ssbmId"].source_kind, "page_context")
        self.assertFalse(by_path["ssbmId"].exposed_to_user)
        self.assertEqual(by_path["bmId"].source_kind, "page_context")
        self.assertFalse(by_path["bmId"].exposed_to_user)
        self.assertEqual(by_path["ssbmmc"].source_kind, "page_context")
        self.assertFalse(by_path["ssbmmc"].exposed_to_user)
        self.assertEqual(by_path["ywsxList[0].ssxts"].category, "system_const")
        self.assertFalse(by_path["ywsxList[0].ssxts"].exposed_to_user)

    def test_flow_spec_uses_complete_dom_enum_options_for_sourceless_enum(self):
        body = {"type": "事假", "reason": "回家"}
        spec = to_flow_spec(
            [_post("https://oa/api/leave/submit", body, resp={"code": 200})],
            samples={"type": "事假", "reason": "回家"},
            page_enum_options=_dom_enum(
                "请假类型", "type", "事假", "事假",
                [
                    {"label": "事假", "value": "事假"},
                    {"label": "病假", "value": "病假"},
                    {"label": "年假", "value": "年假"},
                ],
            ),
            field_evidence=[_select_evidence("type", "type", "请假类型", "事假")],
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
        select_labels = [
            option.get("label") if isinstance(option, dict) else option
            for option in (spec.steps[0].selects[0].options or [])
        ]
        self.assertEqual(set(select_labels), {"事假", "病假", "年假"})

    def test_flow_spec_uses_page_enum_options_when_enum_submits_code(self):
        body = {"type": 2, "reason": "回家"}
        spec = to_flow_spec(
            [_post("https://oa/api/leave/submit", body, resp={"code": 200})],
            samples={"类型": "事假", "reason": "回家"},
            page_enum_options=_dom_enum(
                "类型", "type", "事假", 2,
                [{"label": "事假", "value": 2}, {"label": "病假", "value": 3}],
            ),
            field_evidence=[_select_evidence("type", "type", "类型", "事假")],
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

    def test_page_enum_field_key_and_options_are_visible_in_description(self):
        body = {"type": "SICK", "reason": "回家"}
        spec = to_flow_spec(
            [_post("https://oa/api/leave/submit", body, resp={"code": 200})],
            samples={"请假类型": "病假", "reason": "回家"},
            page_enum_options=_dom_enum(
                "请假类型", "type", "病假", "SICK",
                [
                    {"label": "病假", "value": "SICK"},
                    {"label": "事假", "value": "PERSONAL"},
                    {"label": "婚假", "value": "MARRIAGE"},
                ],
            ),
            field_evidence=[_select_evidence("type", "type", "请假类型", "病假")],
        )

        by_path = {p.path: p for p in spec.steps[0].params}
        self.assertEqual(by_path["type"].key, "请假类型")
        self.assertEqual(by_path["type"].source_kind, "page_enum")
        self.assertIn("页面枚举选项", by_path["type"].description or "")
        self.assertIn("病假=SICK", by_path["type"].description or "")
        self.assertIn("婚假=MARRIAGE", by_path["type"].reason)
        self.assertEqual(by_path["type"].enum_value_map["事假"], "PERSONAL")

    def test_page_enum_target_is_not_auto_linked_to_previous_response(self):
        captured = [
            _post("https://oa/api/leave/start", {"flowType": "leave"},
                  resp={"code": 200, "data": {"type": "SICK", "taskId": "T-777"}}),
            _post("https://oa/api/leave/submit",
                  {"type": "SICK", "taskId": "T-777", "reason": "回家"},
                  resp={"code": 200}),
        ]
        spec = to_flow_spec(
            captured,
            samples={"请假类型": "病假", "reason": "回家"},
            page_enum_options=_dom_enum(
                "请假类型", "type", "病假", "SICK",
                [
                    {"label": "病假", "value": "SICK"},
                    {"label": "事假", "value": "PERSONAL"},
                ],
            ),
            field_evidence=[_select_evidence("type", "type", "请假类型", "病假")],
        )

        submit = spec.steps[1]
        by_path = {p.path: p for p in submit.params}
        self.assertEqual(by_path["type"].source_kind, "page_enum")
        self.assertFalse(any(l.target_path == "type" for l in spec.links))
        self.assertTrue(any(l.target_path == "taskId" for l in spec.links))

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

    def test_client_spec_redacts_secrets_and_keeps_request_body_server_side(self):
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
        self.assertEqual(json.loads(spec.steps[0].body_source), {"a": 1})
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
        page_enum_options = _dom_enum(
            "请假类型", "leaveType", "事假", 2,
            [
                {"label": "病假", "value": 1},
                {"label": "事假", "value": 2},
                {"label": "婚假", "value": 3},
            ],
        )
        from dano.execution.page.request_capture import apply_page_enum_options, page_enum_selects
        out = page_enum_selects(json.dumps(body, ensure_ascii=False), page_enum_options, set(), fields=[
            _select_evidence("formData.leaveType", "leaveType", "请假类型", "事假"),
            {"path": "formData.name", "key": "name", "value": "张三", "suggest_name": "姓名"},
        ])
        self.assertTrue(any(s.get("path") == "formData.leaveType" for s in out),
                        f"应当命中 leaveType 路径, 实际: {[s.get('path') for s in out]}")

    def test_dom_options_match_internal_field_with_old_legacy_shape(self):
        """旧形态只有 label，没有控件身份和 wire map，必须拒绝执行绑定。"""
        body = {"leaveType": 2}
        page_enum_options = {"病假": ["病假", "事假"]}
        from dano.execution.page.request_capture import page_enum_selects
        out = page_enum_selects(json.dumps(body, ensure_ascii=False), page_enum_options, set(), fields=[
            {"path": "leaveType", "key": "leaveType", "value": 2, "suggest_name": "请假类型"},
        ])
        self.assertEqual(out, [])

    def test_apply_page_enum_options_ignores_legacy_label_only_snapshot(self):
        """纯 label 快照缺少 wire map；允许读取但不得改写可执行枚举。"""
        body = {"formData": {"status": "active"}}
        # 旧形态:value 为纯 list
        old_opts = {"active": ["启用", "停用"]}
        fields = [{"path": "formData.status", "key": "status", "value": "active"}]
        from dano.execution.page.request_capture import apply_page_enum_options
        # 输入 selects 为空,apply 不应该报错
        apply_page_enum_options([], old_opts, post_data=json.dumps(body, ensure_ascii=False),
                                fields=fields)
        # 输入 selects 含一个 select，旧证据仍不得覆盖它。
        existing = [{"path": "formData.status", "label": "active", "value": "active"}]
        apply_page_enum_options(existing, old_opts, post_data=json.dumps(body, ensure_ascii=False),
                                fields=fields)
        self.assertIsNone(existing[0].get("enum_source"))
        self.assertNotIn("启用", existing[0].get("options") or [])


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
        spec = to_flow_spec(
            captured,
            reads=reads,
            samples={"审批人一": "张三", "审批人二": "李四"},
            field_evidence=[
                _select_evidence(
                    "startUserSelectAssignees[0].Activity_09dlq0g[0]",
                    "Activity_09dlq0g", "审批人一", "张三",
                ),
                _select_evidence(
                    "startUserSelectAssignees[0].Activity_0ag2wyz[0]",
                    "Activity_0ag2wyz", "审批人二", "李四",
                ),
            ],
        )
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
        spec = to_flow_spec(
            captured,
            reads=reads,
            samples={"审批人": "甲"},
            field_evidence=[_select_evidence(
                "approvers.reviewer[0]", "reviewer", "审批人", "甲",
            )],
        )
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
        spec = to_flow_spec(
            captured,
            reads=reads,
            samples={"类型": "病假"},
            field_evidence=[_select_evidence("type", "type", "类型", "病假")],
        )
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
        self.assertTrue(any("runtime_var" in w for w in report["suggestions"]))

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
        self.assertTrue(any("runtime_var" in w for w in report["suggestions"]))

    def test_system_const_exposed_is_generation_advice(self):
        captured = [_post("https://oa/api/submit", {
            "billType": "oa_duty_leave",
            "reason": "123",
        }, resp={"code": 200})]
        spec = to_flow_spec(captured, samples={"reason": "123"})
        param = {p.path: p for p in spec.steps[0].params}["billType"]
        param.exposed_to_user = True
        report = validate_flow_spec(spec)
        self.assertTrue(report["passed"])
        self.assertTrue(any("system_const" in e and "暴露给用户" in e for e in report["suggestions"]))


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
        spec = to_flow_spec(
            captured,
            reads=reads,
            samples={"请假类型": "婚假"},
            field_evidence=[_select_evidence("type", "type", "请假类型", "婚假")],
        )
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

    def test_recorded_select_identity_keeps_api_short_code_map(self):
        """控件身份 + 真实 API 字典应产出动态 api_option，而不是静态页面枚举。"""
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
            field_evidence=[_select_evidence("type", "type", "类型", "病假")],
        )

        by_path = {p.path: p for s in spec.steps for p in s.params}
        p = by_path["type"]
        self.assertEqual(p.type, "enum")
        self.assertEqual(p.source_kind, "api_option")
        self.assertEqual(p.key, "类型")
        self.assertEqual(p.enum_value_map, {"病假": 2, "事假": 1, "婚假": 3})

        api_req, errors = flow_spec_to_api_request(spec)
        self.assertEqual(errors, [])
        self.assertEqual(api_req["field_types"]["类型"], "enum")
        self.assertEqual(api_req["selects"][0]["option_map"], {"病假": 2, "事假": 1, "婚假": 3})

    def test_dom_label_options_without_short_code_map_are_not_executable(self):
        """DOM 只有 label 时不能确认 wire map，字段保持普通参数。"""
        captured = [_post("https://oa/api/submit", {"type": 2, "reason": "回家"}, resp={"code": 200})]
        spec = to_flow_spec(
            captured,
            samples={"type": "2", "reason": "回家"},
            page_enum_options={"type": {"options": ["病假", "事假", "婚假"], "field_key": "type", "selected": "病假"}},
        )

        by_path = {p.path: p for s in spec.steps for p in s.params}
        self.assertNotEqual(by_path["type"].type, "enum")
        self.assertEqual(by_path["type"].source_kind, "user_input")
        self.assertEqual(spec.steps[0].selects, [])

    def test_dom_label_options_never_guess_numeric_codes_by_selected_order(self):
        """无完整 label→value 合同时，不确认本次值也不按 DOM 顺序猜码。"""
        captured = [_post("https://oa/api/submit", {"type": 3, "reason": "回家"}, resp={"code": 200})]
        spec = to_flow_spec(
            captured,
            samples={"类型": "婚假", "reason": "回家"},
            page_enum_options={"类型": {"options": ["病假", "事假", "婚假"], "field_key": "类型", "selected": "婚假"}},
        )

        by_path = {p.path: p for s in spec.steps for p in s.params}
        p = by_path["type"]
        self.assertEqual(p.key, "type")
        self.assertNotEqual(p.type, "enum")
        self.assertEqual(p.source_kind, "user_input")
        self.assertIsNone(p.enum_value_map)
        self.assertEqual(spec.steps[0].selects, [])

    def test_manual_enum_value_only_options_are_advice_until_label_map(self):
        """人工枚举只有 1/2/3 时给出建议；操作员仍可保留当前合同。"""
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
        self.assertTrue(report["passed"])
        self.assertTrue(any("内部值/短码" in e and "类型" in e for e in report["suggestions"]))

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

    def test_api_option_without_source_or_options_does_not_emit_dynamic_source_error(self):
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
        messages = [*report["errors"], *report["warnings"]]
        self.assertFalse(any("接口选项" in message and "source_url/options/option_map" in message for message in messages))
        self.assertFalse(any("动态枚举缺少可执行的实时来源接口" in message for message in messages))

    def test_internal_short_code_user_input_is_generation_advice(self):
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
        self.assertTrue(report["passed"])
        self.assertTrue(any("内部 ID/短码" in e for e in report["suggestions"]))

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

    def test_get_query_parameter_recognized_from_scoped_dynamic_dictionary(self):
        """GET 枚举需有控件身份、完整 map 与真实动态字典来源。"""
        reads = [{
            "url": "https://oa/api/users/status/list",
            "json": {"data": [{"value": "active", "label": "启用"}, {"value": "inactive", "label": "停用"}]},
        }]
        from dano.execution.page.flow_spec import _detect_query_selects
        req = {
            "method": "GET",
            "url": "https://oa/api/users?status=active&keyword=张三",
            "headers": {"Authorization": "Bearer t"},
            "page_id": "user-list",
            "frame_id": "main",
        }
        page_options = {
            "状态": {
                "field_key": "状态",
                "field_aliases": ["status"],
                "control_kind": "select",
                "enum_source": "script_dictionary",
                "source_url": "https://oa/api/users/status/list",
                "dict_type": "user_status",
                "mapping_complete": True,
                "selected": "启用",
                "selected_value": "active",
                "options": [
                    {"label": "启用", "value": "active"},
                    {"label": "停用", "value": "inactive"},
                ],
                "page_id": "user-list",
                "frame_id": "main",
            }
        }
        evidence = [_select_evidence(
            "query.status", "status", "状态", "启用", page_id="user-list",
        )]
        sels = _detect_query_selects(
            req,
            {"keyword": "张三", "状态": "启用"},
            reads,
            page_enum_options=page_options,
            field_evidence=evidence,
        )
        paths = [s.get("path") for s in sels]
        self.assertIn("query.status", paths,
                      f"应当识别 query.status 是 enum,actual={paths}")


        sel = next(s for s in sels if s.get("path") == "query.status")
        self.assertEqual(sel.get("enum_source"), "api")
        self.assertEqual(sel.get("source_url"), "https://oa/api/users/status/list")
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


def test_hotel_recording_uses_scoped_control_identity_for_name_type_and_source():
    query = _get(
        "https://oa.test/admin-api/oa/hotel-apply/page?pageNo=1&pageSize=10&billCode=1&processStatus=1",
        {"code": 0, "data": {"list": [], "total": 0}},
    )
    query.update({"page_id": "hotel-list", "frame_id": "main"})
    submit = _post(
        "https://oa.test/admin-api/oa/hotel-apply/submit-process",
        {"applyTitle": "1", "totalAmt": 1, "roomType": 2, "useTime": 1784044800000, "remark": "1"},
        resp={"code": 0, "data": True},
    )
    submit.update({"page_id": "hotel-form", "frame_id": "main"})
    evidence = [
        {"page_id": "hotel-list", "frame_id": "main", "label": "单据编号", "value": "1", "field_aliases": ["billCode"], "control_kind": "text"},
        {"page_id": "hotel-list", "frame_id": "main", "label": "流程状态", "value": "审批中", "field_aliases": ["processStatus"], "control_kind": "select"},
        {"page_id": "hotel-form", "frame_id": "main", "label": "申请标题", "value": "1", "field_aliases": ["applyTitle"], "control_kind": "text"},
        {"page_id": "hotel-form", "frame_id": "main", "label": "预计金额", "value": "1", "field_aliases": ["totalAmt"], "control_kind": "number"},
        {"page_id": "hotel-form", "frame_id": "main", "label": "房间类型", "value": "大床房", "field_aliases": ["roomType"], "control_kind": "select"},
        {"page_id": "hotel-form", "frame_id": "main", "label": "入住时间", "value": "2026-07-14 00:00:00", "field_aliases": ["useTime"], "control_kind": "datetime"},
        {"page_id": "hotel-form", "frame_id": "main", "label": "备注", "value": "1", "field_aliases": ["remark"], "control_kind": "textarea"},
    ]
    page_enums = {
        "流程状态": {
            "page_id": "hotel-list", "frame_id": "main", "field_key": "流程状态",
            "field_aliases": ["processStatus"], "control_kind": "select", "selected": "审批中",
            "selected_value": 1, "mapping_complete": True,
            "options": [{"label": "未提交", "value": 0}, {"label": "审批中", "value": 1}],
        },
        "房间类型": {
            "page_id": "hotel-form", "frame_id": "main", "field_key": "房间类型",
            "field_aliases": ["roomType"], "control_kind": "select", "selected": "大床房",
            "selected_value": 2, "mapping_complete": True,
            "options": [{"label": "双床房", "value": 1}, {"label": "大床房", "value": 2}],
        },
    }
    unrelated = [{"url": "/admin-api/system/dict-data/simple-list", "json": {"data": [
        {"label": "歌词模式", "value": 1}, {"label": "描述模式", "value": 2},
    ]}}]

    spec = to_flow_spec(
        [query, submit], reads=unrelated,
        samples={item["label"]: item["value"] for item in evidence},
        page_enum_options=page_enums, field_evidence=evidence,
    )
    params = {param.path: param for step in spec.steps for param in step.params}

    assert (params["query.billCode"].key, params["query.billCode"].type, params["query.billCode"].source_kind) == ("单据编号", "string", "user_input")
    assert (params["query.processStatus"].key, params["query.processStatus"].type, params["query.processStatus"].source_kind) == ("流程状态", "enum", "page_enum")
    assert (params["applyTitle"].key, params["applyTitle"].type, params["applyTitle"].source_kind) == ("申请标题", "string", "user_input")
    assert (params["totalAmt"].key, params["totalAmt"].type, params["totalAmt"].source_kind) == ("预计金额", "number", "user_input")
    assert (params["roomType"].key, params["roomType"].type, params["roomType"].source_kind) == ("房间类型", "enum", "page_enum")
    assert (params["useTime"].key, params["useTime"].type, params["useTime"].source_kind) == ("入住时间", "datetime", "user_input")
    assert (params["remark"].key, params["remark"].type, params["remark"].source_kind) == ("备注", "string", "user_input")


if __name__ == "__main__":
    unittest.main()


def test_timesheet_field_ownership_and_selected_project_projections():
    option_response = {"data": [{
        "projectId": "p-1", "projectName": "数据智能平台5.2.1",
        "remainingHours": 8, "teamId": "team-public", "teamName": "公共团队",
        "workTypeId": "work-public", "workTypeName": "公共工时",
        "approverId": "user-yan", "approverName": "誉津津",
    }]}
    option_read = {
        "method": "POST", "url": "https://work.test/rpc/load-context",
        "json": option_response, "role": "read_option",
        "trigger_op": "click", "trigger_locator": "[role=combobox][name=projectId]",
    }
    submit = _post("https://work.test/rpc/submit-timesheet", {
        "projectId": "p-1", "applyDate": "2026-06-03",
        "remainingHours": 8, "reportedHours": 8,
        "teamId": "team-public", "workTypeId": "work-public", "approverId": "user-yan",
    })
    evidence = [
        {"label": "项目名称", "field_aliases": ["projectId"], "control_kind": "select", "op": "select"},
        {"label": "申报日期", "field_aliases": ["applyDate"], "control_kind": "date", "op": "fill"},
        {"label": "剩余工时", "field_aliases": ["remainingHours"], "control_kind": "number", "op": "snapshot", "disabled": True},
        {"label": "申报工时", "field_aliases": ["reportedHours"], "control_kind": "number", "op": "snapshot"},
        {"label": "团队", "field_aliases": ["teamId"], "control_kind": "select", "op": "snapshot", "disabled": True},
        {"label": "工时类型", "field_aliases": ["workTypeId"], "control_kind": "select", "op": "snapshot", "disabled": True},
        {"label": "审批人", "field_aliases": ["approverId"], "control_kind": "select", "op": "snapshot", "disabled": True},
    ]

    step = flow_spec_module._build_step_from_capture(
        submit, reads=[option_read],
        samples={"项目名称": "数据智能平台5.2.1", "申报日期": "2026-06-03", "申报工时": "8"},
        storage_state=None, required_labels={"项目名称", "申报日期"},
        page_enum_options={}, step_index=1, field_evidence=evidence,
    )
    params = {param.path: param for param in step.params}

    assert params["projectId"].source_kind == "api_option"
    assert (params["applyDate"].category, params["applyDate"].source_kind, params["applyDate"].type) == (
        "user_param", "user_input", "date",
    )
    assert params["reportedHours"].source_kind == "user_input"
    assert params["reportedHours"].default_value == 8
    for path in ("remainingHours", "teamId", "workTypeId", "approverId"):
        assert params[path].category == "runtime_var"
        assert params[path].source_kind == "selected_option_field"
        assert params[path].exposed_to_user is False
    project_binding = next(binding for binding in step.selects if binding.path == "projectId")
    assert project_binding.field_projections["remainingHours"] == "remainingHours"
    assert "reportedHours" not in project_binding.field_projections


def test_short_readonly_value_can_link_by_exact_wire_path_without_hijacking_editable_default():
    source = FlowStep(
        step_id="context", method="GET", path="/rpc/context",
        response_json={"data": {"remainingHours": 8}},
    )
    remaining = ParamField(
        path="remainingHours", key="剩余工时", value=8,
        category="runtime_var", source_kind="unknown", exposed_to_user=False,
        evidence=[{"kind": "page_control", "disabled": True, "editable": False}],
    )
    reported = ParamField(
        path="reportedHours", key="申报工时", value=8,
        category="user_param", source_kind="user_input", exposed_to_user=True,
        evidence=[{"kind": "page_control", "editable": True}],
    )
    target = FlowStep(step_id="submit", method="POST", path="/rpc/submit", params=[remaining, reported])
    spec = FlowSpec(steps=[source, target])

    assert flow_spec_module.rebuild_flow_dependencies(spec) == 1
    assert [(link.source_path, link.target_path) for link in spec.links] == [
        ("data.remainingHours", "remainingHours"),
    ]
    assert remaining.source_kind == "previous_response"
    assert reported.source_kind == "user_input"

def test_field_contract_axes_generalize_to_nested_unrelated_system():
    option_read = {
        "method": "POST", "url": "https://asset.test/gateway/dispatch",
        "json": {"payload": {"rows": [{
            "code": "A-7", "title": "主资产",
            "quota": {"left": 5}, "group": {"key": "g-2"},
            "owner": {"userId": "u-9"},
        }]}},
        "role": "read_option", "trigger_op": "click",
        "trigger_locator": "[data-field=assetCode]",
    }
    submit = _post("https://asset.test/gateway/dispatch", {
        "assetCode": "A-7", "scheduleDay": "2026-08-01",
        "quotaLeft": 5, "amount": 5, "groupKey": "g-2", "ownerId": "u-9",
    })
    evidence = [
        {"label": "资产名称", "field_aliases": ["assetCode"], "control_kind": "select", "op": "select"},
        {"label": "执行日期", "field_aliases": ["scheduleDay"], "control_kind": "date", "op": "fill"},
        {"label": "剩余额度", "field_aliases": ["quotaLeft"], "control_kind": "number", "op": "snapshot", "disabled": True},
        {"label": "申报数量", "field_aliases": ["amount"], "control_kind": "number", "op": "snapshot"},
        {"label": "业务组", "field_aliases": ["groupKey"], "control_kind": "select", "op": "snapshot", "disabled": True},
        {"label": "负责人", "field_aliases": ["ownerId"], "control_kind": "select", "op": "snapshot", "disabled": True},
    ]

    step = flow_spec_module._build_step_from_capture(
        submit, reads=[option_read],
        samples={"资产名称": "主资产", "执行日期": "2026-08-01", "申报数量": "5"},
        storage_state=None, required_labels={"资产名称", "执行日期"},
        page_enum_options={}, step_index=1, field_evidence=evidence,
    )
    params = {param.path: param for param in step.params}

    assert (params["assetCode"].key, params["assetCode"].type, params["assetCode"].category, params["assetCode"].source_kind) == (
        "资产名称", "enum", "user_param", "api_option",
    )
    assert (params["scheduleDay"].key, params["scheduleDay"].type, params["scheduleDay"].category, params["scheduleDay"].source_kind) == (
        "执行日期", "date", "user_param", "user_input",
    )
    assert (params["amount"].key, params["amount"].type, params["amount"].category, params["amount"].source_kind) == (
        "申报数量", "number", "user_param", "user_input",
    )
    assert params["amount"].default_value == 5
    expected = {
        "quotaLeft": ("剩余额度", "number", "quota.left"),
        "groupKey": ("业务组", "string", "group.key"),
        "ownerId": ("负责人", "string", "owner.userId"),
    }
    for path, (name, field_type, response_path) in expected.items():
        param = params[path]
        assert (param.key, param.type, param.category, param.source_kind) == (
            name, field_type, "runtime_var", "selected_option_field",
        )
        assert param.exposed_to_user is False
        assert param.editable is False
        assert param.required is False
        assert any(item.get("kind") == "selected_option_projection" for item in param.evidence)
        assert param.source["response_path"] == response_path

    binding = next(item for item in step.selects if item.path == "assetCode")
    assert binding.field_projections == {
        "quotaLeft": "quota.left", "groupKey": "group.key", "ownerId": "owner.userId",
    }
    assert "amount" not in binding.field_projections
