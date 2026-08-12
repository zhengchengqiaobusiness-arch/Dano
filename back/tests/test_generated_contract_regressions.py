from __future__ import annotations

import asyncio

from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowCapability,
    FlowStep,
    ParamField,
    SelectBinding,
    _NO_SCHEMA_DEFAULT,
    _apply_output_presentation_evidence,
    _schema_default_for_param,
    _schema_from_response_value,
    _sync_capability_io_schemas,
    orchestrate_flow_capabilities,
    prepare_flow_spec_for_publish,
    to_flow_spec,
)
from dano.export.agent_skills import (
    _question_collection_block,
    _question_request_template,
    _upgrade_recorded_skill_for_export,
)
from dano.orchestrator.types import SkillSpec
from dano.orchestrator.capability_runtime import schema_issues
from dano.shared.enums import RiskLevel, Subsystem


def _get(index: int, url: str, response_json: dict) -> dict:
    return {
        "index": index,
        "sequence": index,
        "method": "GET",
        "url": url,
        "headers": {},
        "response_status": 200,
        "response_json": response_json,
        "trigger_op": "click",
        "trigger_action_id": "query",
        "trigger_locator": "button[type=submit]",
        "trigger_transaction_id": f"txn-query-{index}",
    }


def test_hotel_query_contract_uses_optional_filters_overridable_paging_and_rich_records():
    requests = [
        _get(
            1,
            "https://oa.test/admin-api/oa/hotel-apply/page?pageNo=1&pageSize=10",
            {"code": 0, "data": {"list": [], "total": 0}},
        ),
        _get(
            2,
            "https://oa.test/admin-api/oa/hotel-apply/page?pageNo=1&pageSize=10&hotelName=1",
            {
                "code": 0,
                "data": {
                    "list": [{"id": "process-1", "hotelName": "酒店 A", "processStatus": 1}],
                    "total": 1,
                },
            },
        ),
    ]
    evidence = [{
        "label": "酒店名称",
        "value": "1",
        "field_aliases": ["hotelName"],
        "control_kind": "text",
    }]

    spec = to_flow_spec(
        requests,
        samples={"酒店名称": "1"},
        field_evidence=evidence,
    )
    spec = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))
    capability = next(cap for cap in spec.capabilities if cap.kind == "query_status")
    properties = capability.input_schema["properties"]

    assert capability.input_schema["required"] == []
    assert properties["pageNo"]["default"] == 1
    assert properties["pageNo"]["x-dano-apply-default"] is True
    assert properties["pageSize"]["default"] == 10
    assert properties["酒店名称"]["type"] == "string"
    assert properties["酒店名称"]["x-dano-wire-type"] == "string"
    assert "default" not in properties["酒店名称"]

    item_properties = capability.output_schema["properties"]["records"]["items"]["properties"]
    assert item_properties["id"]["type"] == "string"
    assert item_properties["hotelName"]["type"] == "string"
    assert item_properties["processStatus"]["type"] == "number"


def test_query_output_schema_preserves_recorded_table_presentation():
    response = {
        "records": [{
            "id": "opaque-record-id",
            "processInstanceId": "OA-QJ-2026072500007",
            "processStatus": 1,
            "day": 8,
            "billCode": "QJD202607250007",
            "billType": "oa_duty_leave",
            "processDefKey": "oa_duty_leave",
            "submitTime": 1784955106000,
        }],
    }
    evidence = [{
        "label": "酒店名称",
        "value": "test",
        "field_aliases": ["hotelName"],
        "control_kind": "text",
    }, *[
        {
            "kind": "table_column",
            "label": label,
            "field_aliases": [field],
            "display_order": order,
            "control_kind": "table_column",
            "table_id": "leave-list",
            "table_complete": True,
            **({"value_kind": "datetime"} if field == "submitTime" else {}),
        }
        for order, (field, label) in enumerate([
            ("billCode", "单据编号"),
            ("processStatus", "状态"),
            ("day", "请假天数"),
            ("submitTime", "申请时间"),
        ])
    ]]

    schema = _schema_from_response_value(response)
    _apply_output_presentation_evidence(schema, evidence)
    fields = schema["properties"]["records"]["items"]["properties"]

    assert fields["billCode"]["title"] == "单据编号"
    assert fields["billCode"]["x-dano-display-order"] == 0
    assert fields["processStatus"]["title"] == "状态"
    assert fields["day"]["title"] == "请假天数"
    assert fields["submitTime"]["title"] == "申请时间"
    assert fields["submitTime"]["x-dano-value-format"] == "epoch-auto"
    assert all(fields[name]["x-dano-display"] is False for name in (
        "id", "processInstanceId", "billType", "processDefKey",
    ))


def test_query_output_presentation_matches_visible_samples_without_dom_field_aliases():
    response = {
        "records": [{
            "id": "opaque-record-id",
            "processStatus": 1,
            "day": 8,
            "billCode": "QJD202607250007",
            "submitTime": 1784955106000,
        }],
    }
    evidence = [
        {
            "kind": "table_column",
            "label": label,
            "field_aliases": [],
            "display_order": order,
            "control_kind": "table_column",
            "table_id": "leave-list",
            "table_complete": True,
            "sample_values": [sample],
            **({"sample_epoch_ms": [1784955106000], "value_kind": "datetime"}
               if label == "申请时间" else {}),
        }
        for order, (label, sample) in enumerate([
            ("单据编号", "QJD202607250007"),
            ("状态", "审批中"),
            ("请假天数", "8"),
            ("申请时间", "2026-07-25 12:51:46"),
        ])
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "审批结果": {
                "type": "string",
                "x-flow-path": "query.processStatus",
                "x-enum-value-map": {"审批中": 1, "已撤回": 4},
            },
        },
    }

    schema = _schema_from_response_value(response)
    _apply_output_presentation_evidence(
        schema,
        evidence,
        sample_output=response,
        input_schema=input_schema,
    )
    fields = schema["properties"]["records"]["items"]["properties"]

    assert [fields[name]["title"] for name in (
        "billCode", "processStatus", "day", "submitTime",
    )] == ["单据编号", "状态", "请假天数", "申请时间"]
    assert fields["id"]["x-dano-display"] is False


def test_capability_sync_writes_table_presentation_into_output_contract():
    step = FlowStep(
        step_id="query",
        method="GET",
        path="/api/requests",
        source_meta={"page_id": "page-1", "frame_id": "main", "role": "business_get"},
        response_json={
            "data": {
                "list": [{"id": "internal", "billCode": "REQ-42", "day": 2}],
                "total": 1,
            },
        },
    )
    spec = FlowSpec(
        steps=[step],
        capabilities=[FlowCapability(
            name="query_requests",
            kind="query_status",
            step_ids=["query"],
            nodes=[{"id": "call_query", "type": "call", "step_id": "query"}],
        )],
        meta={"field_evidence": [
            {
                "kind": "table_column",
                "label": "单据编号",
                "field_aliases": ["billCode"],
                "control_kind": "table_column",
                "display_order": 0,
                "table_id": "request-list",
                "table_complete": True,
                "page_id": "page-1",
                "frame_id": "main",
            },
            {
                "kind": "table_column",
                "label": "天数",
                "field_aliases": ["day"],
                "control_kind": "table_column",
                "display_order": 1,
                "table_id": "request-list",
                "table_complete": True,
                "page_id": "page-1",
                "frame_id": "main",
            },
        ]},
    )
    _sync_capability_io_schemas(spec)
    fields = spec.capabilities[0].output_schema["properties"]["records"]["items"]["properties"]

    assert fields["billCode"]["title"] == "单据编号"
    assert fields["day"]["title"] == "天数"
    assert fields["id"]["x-dano-display"] is False


def test_recorded_select_keeps_choice_type_without_inventing_options():
    request = _get(
        1,
        "https://oa.test/admin-api/oa/hotel-apply/page?pageNo=1&pageSize=10&processStatus=1",
        {"code": 0, "data": {"list": [], "total": 0}},
    )
    evidence = [{
        "label": "流程状态",
        "value": "审批中",
        "field_aliases": ["processStatus"],
        "control_kind": "select",
    }]

    spec = to_flow_spec(
        [request],
        samples={"流程状态": "审批中"},
        field_evidence=evidence,
    )
    param = next(
        param for step in spec.steps for param in step.params
        if param.path == "query.processStatus"
    )

    assert param.type == "enum"
    assert param.source_kind == "form_option"
    assert param.enum_options is None
    assert param.need_human_confirm is True
    assert all(select.path != "query.processStatus" for step in spec.steps for select in step.selects)


def test_schema_defaults_are_type_safe_and_never_guess_enum_labels():
    assert _schema_default_for_param(ParamField(
        path="query.pageNo", key="pageNo", type="integer", value="3",
    )) == 3
    assert _schema_default_for_param(ParamField(
        path="query.status", key="状态", type="enum", value="1",
        enum_options=[{"label": "审批中", "value": 1}],
        enum_value_map={"审批中": 1},
    )) is _NO_SCHEMA_DEFAULT
    assert _schema_default_for_param(ParamField(
        path="query.status", key="状态", type="enum", value="9",
        enum_options=[{"label": "审批中", "value": 1}],
        enum_value_map={"审批中": 1},
    )) is _NO_SCHEMA_DEFAULT
    assert _schema_default_for_param(ParamField(
        path="startAt", key="开始时间", type="datetime", value="not-a-date",
    )) is _NO_SCHEMA_DEFAULT
    assert _schema_default_for_param(ParamField(
        path="startAt", key="开始时间", type="datetime", value="2026-07-15 09:30:00",
    )) is _NO_SCHEMA_DEFAULT


def test_withdraw_id_remains_explicit_user_input_and_is_never_silently_defaulted():
    withdraw = FlowStep(
        step_id="withdraw",
        method="DELETE",
        path="/admin-api/bpm/process-instance/cancel-by-start-user",
        params=[
            ParamField(
                path="id", key="id", value="process-1", default_value="process-1",
                type="string", required=True, category="user_param", source_kind="user_input",
            ),
            ParamField(
                path="reason", key="撤回原因", value="行程变更", default_value="行程变更",
                type="string", required=True, category="user_param", source_kind="user_input",
            ),
        ],
        response_json={"code": 0, "message": "success"},
    )

    spec = asyncio.run(orchestrate_flow_capabilities(FlowSpec(steps=[withdraw]), submission={"ops": []}))
    capability = next(cap for cap in spec.capabilities if cap.kind == "withdraw")
    properties = capability.input_schema["properties"]

    assert set(capability.input_schema["required"]) == {"id", "撤回原因"}
    assert properties["id"]["default"] == "process-1"
    assert "x-dano-apply-default" not in properties["id"]
    assert spec.capability_relations == []


def test_withdraw_id_is_grounded_to_the_exact_process_instance_field():
    query = FlowStep(
        step_id="query",
        method="GET",
        path="/admin-api/oa/seal-apply/page",
        source_meta={"role": "business_get", "page_id": "seal-page"},
        response_json={
            "code": 0,
            "data": {
                "list": [{
                    "id": "record-42",
                    "billCode": "GZSY-42",
                    "processInstanceId": "OA-GZSY-42",
                }],
                "total": 1,
            },
        },
    )
    withdraw = FlowStep(
        step_id="withdraw",
        method="DELETE",
        path="/admin-api/bpm/process-instance/cancel-by-start-user",
        source_meta={"role": "business_write", "page_id": "seal-page"},
        params=[
            ParamField(
                path="id", key="单据编号", label="单据编号",
                value="OA-GZSY-42", default_value="OA-GZSY-42",
                type="string", required=True, category="user_param",
                source_kind="user_input",
            ),
            ParamField(
                path="reason", key="撤回原因", label="撤回原因",
                value="填错了", type="string", required=True,
                category="user_param", source_kind="user_input",
            ),
        ],
        response_json={"code": 0, "msg": "success"},
    )
    spec = FlowSpec(
        steps=[query, withdraw],
        capabilities=[
            FlowCapability(
                name="query_seal_apply", title="查询公章使用申请",
                kind="query_status", step_ids=["query"],
                nodes=[{"id": "call_query", "type": "call", "step_id": "query"}],
            ),
            FlowCapability(
                name="withdraw_seal_apply", title="撤回公章使用申请",
                kind="withdraw", step_ids=["withdraw"],
                nodes=[{"id": "call_withdraw", "type": "call", "step_id": "withdraw"}],
                output_mapping=[{
                    "kind": "final_response", "name": "result",
                    "step_id": "withdraw", "response_path": "response",
                }],
            ),
        ],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    query_cap, withdraw_cap = prepared.capabilities
    process_field = (
        query_cap.output_schema["properties"]["records"]["items"]["properties"]
        ["processInstanceId"]
    )
    target_field = withdraw_cap.input_schema["properties"]["单据编号"]

    assert process_field["x-dano-identifier-role"] == "process_instance"
    assert target_field["x-dano-identifier-role"] == "process_instance"
    assert target_field["x-dano-derived-from-query"] is True
    assert target_field["title"] == "流程实例ID"
    assert "default" not in target_field
    assert [
        (
            relation.from_capability,
            relation.from_output,
            relation.to_capability,
            relation.to_input,
        )
        for relation in prepared.capability_relations
    ] == [(
        "query_seal_apply",
        "records[].processInstanceId",
        "withdraw_seal_apply",
        "单据编号",
    )]
    contract = {
        "kind": "withdraw",
        "title": "撤回公章使用申请",
        "field_labels": {"单据编号": "单据编号"},
        "parameters": withdraw_cap.input_schema,
    }
    questions = _question_request_template("withdraw_seal_apply", contract)["questions"]
    field_table = "\n".join(_question_collection_block("withdraw_seal_apply", contract))
    assert [question["question"] for question in questions] == ["撤回原因"]
    assert "OA-GZSY-42" not in field_table
    assert "query_seal_apply.records[].processInstanceId" in field_table


def test_query_defaults_do_not_break_withdraw_identifier_relation():
    query = FlowStep(
        step_id="query",
        method="GET",
        path="/applications/page",
        params=[
            ParamField(
                path="query.pageNo", key="页码", value=1, default_value=1,
                type="number", required=False, source_kind="user_input",
            ),
            ParamField(
                path="query.pageSize", key="每页条数", value=10, default_value=10,
                type="number", required=False, source_kind="user_input",
            ),
            ParamField(
                path="query.processStatus", key="流程状态", value=1, default_value=1,
                type="number", required=False, source_kind="user_input",
            ),
        ],
        response_json={"data": {"list": [{
            "id": "record-42",
            "billCode": "REQ-42",
            "processInstanceId": "PROCESS-42",
        }], "total": 1}},
    )
    withdraw = FlowStep(
        step_id="withdraw",
        method="DELETE",
        path="/process/cancel",
        params=[ParamField(
            path="id", key="id", value="PROCESS-42", default_value="PROCESS-42",
            required=True, source_kind="user_input",
        )],
        response_json={"code": 0},
    )
    prepared = prepare_flow_spec_for_publish(FlowSpec(
        steps=[query, withdraw],
        capabilities=[
            FlowCapability(
                name="query_applications", kind="query_status",
                nodes=[{"id": "query_call", "type": "call", "step_id": "query"}],
            ),
            FlowCapability(
                name="withdraw_application", kind="withdraw",
                nodes=[{"id": "withdraw_call", "type": "call", "step_id": "withdraw"}],
            ),
        ],
    ))

    target = prepared.capabilities[1].input_schema["properties"]["id"]
    assert target["x-dano-derived-from-query"] is True
    assert "default" not in target
    assert prepared.capability_relations[0].from_output == "records[].processInstanceId"
    questions = _question_request_template("withdraw_application", {
        "kind": "withdraw",
        "parameters": prepared.capabilities[1].input_schema,
    })["questions"]
    assert questions == []


def test_api_enum_fields_remain_select_controls_and_long_text_fields_use_textarea():
    submit = FlowStep(
        step_id="submit",
        method="POST",
        path="/hotel/submit",
        params=[
            ParamField(
                path="roomType", key="房间类型", label="房间类型",
                value="standard", type="string", required=True,
                source_kind="user_input",
            ),
            ParamField(
                path="roomLevel", key="房间等级", label="房间等级",
                value="normal", type="string", required=True,
                source_kind="user_input",
            ),
            ParamField(
                path="description", key="描述", label="描述",
                value="出差住宿", type="string", required=True,
                source_kind="user_input",
            ),
            ParamField(
                path="remark", key="备注", label="备注",
                value="靠近会场", type="string", required=False,
                source_kind="user_input",
            ),
        ],
        selects=[
            SelectBinding(
                param="房间类型", path="roomType", source_url="/room/types",
                value_key="id", label_key="name", enum_source="api",
                enum_confirmed=True,
                options=[
                    {"label": "标准间", "value": "standard"},
                    {"label": "大床房", "value": "queen"},
                ],
            ),
            SelectBinding(
                param="房间等级", path="roomLevel", source_url="/room/levels",
                value_key="id", label_key="name", enum_source="api",
                enum_confirmed=True,
                options=[
                    {"label": "标准", "value": "normal"},
                    {"label": "豪华", "value": "luxury"},
                ],
            ),
        ],
        response_json={"code": 0},
    )
    prepared = prepare_flow_spec_for_publish(FlowSpec(
        steps=[submit],
        capabilities=[FlowCapability(
            name="submit_hotel", title="提交酒店申请", kind="submit",
            nodes=[{"id": "submit_call", "type": "call", "step_id": "submit"}],
        )],
    ))
    capability = prepared.capabilities[0]
    contract = {
        "kind": capability.kind,
        "title": capability.title,
        "parameters": capability.input_schema,
    }
    questions = {
        question["question"]: question
        for question in _question_request_template(capability.name, contract)["questions"]
    }

    assert questions["房间类型"]["inputType"] == "select"
    assert questions["房间类型"]["options"] == [
        {"id": "standard", "label": "标准间"},
        {"id": "queen", "label": "大床房"},
    ]
    assert questions["房间等级"]["inputType"] == "select"
    assert questions["房间等级"]["options"] == [
        {"id": "normal", "label": "标准"},
        {"id": "luxury", "label": "豪华"},
    ]
    assert questions["描述"]["inputType"] == "textarea"
    assert questions["备注"]["inputType"] == "textarea"


def test_response_array_schema_accepts_observed_mixed_scalar_types():
    schema = _schema_from_response_value({
        "records": [
            {"deptId": "10", "userId": "20", "name": "甲"},
            {"deptId": 11, "userId": None, "name": "乙"},
        ],
    })
    fields = schema["properties"]["records"]["items"]["properties"]

    assert fields["deptId"] == {
        "anyOf": [{"type": "string"}, {"type": "number"}],
    }
    assert fields["userId"] == {}
    assert schema_issues(
        {"records": [
            {"deptId": "10", "userId": "20", "name": "甲"},
            {"deptId": 11, "userId": None, "name": "乙"},
        ]},
        schema,
        "output",
    ) == []


def test_null_only_recording_sample_does_not_reject_later_identifier_values():
    schema = _schema_from_response_value({
        "records": [{"deptId": None, "userId": None, "name": "甲"}],
    })

    assert schema_issues(
        {"records": [{"deptId": "10", "userId": 20, "name": "乙"}]},
        schema,
        "output",
    ) == []


def test_query_output_reuses_recorded_field_labels_and_hides_transport_ids():
    query = FlowStep(
        step_id="query",
        method="GET",
        path="/hotel/page",
        response_json={"data": {"list": [{
            "userId": "user-1",
            "deptId": "dept-1",
            "applyTitle": "出差住宿",
            "useCity": "杭州",
        }], "total": 1}},
    )
    submit = FlowStep(
        step_id="submit",
        method="POST",
        path="/hotel/submit",
        params=[
            ParamField(
                path="applyTitle", key="申请标题", label="申请标题",
                value="出差住宿", required=True,
            ),
            ParamField(
                path="useCity", key="使用城市", label="使用城市",
                value="杭州", required=True,
            ),
        ],
        response_json={"code": 0},
    )
    prepared = prepare_flow_spec_for_publish(FlowSpec(
        steps=[query, submit],
        capabilities=[
            FlowCapability(
                name="query_hotel", kind="query_status",
                nodes=[{"id": "query_call", "type": "call", "step_id": "query"}],
            ),
            FlowCapability(
                name="submit_hotel", kind="submit",
                nodes=[{"id": "submit_call", "type": "call", "step_id": "submit"}],
            ),
        ],
    ))
    fields = (
        prepared.capabilities[0].output_schema["properties"]["records"]["items"]["properties"]
    )

    assert fields["applyTitle"]["title"] == "申请标题"
    assert fields["useCity"]["title"] == "使用城市"
    assert fields["userId"]["x-dano-display"] is False
    assert fields["deptId"]["x-dano-display"] is False


def test_partial_table_presentation_keeps_unmatched_business_fields_visible():
    response = {
        "records": [{
            "id": "internal",
            "processInstanceId": "OA-HOTEL-1",
            "billCode": "JDSQ202607250001",
            "hotelName": "示例酒店",
            "startTime": 1784908800000,
            "endTime": 1784995200000,
            "totalAmt": 500,
            "processStatus": 1,
        }],
    }
    evidence = [
        {
            "kind": "table_column",
            "control_kind": "table_column",
            "label": label,
            "field_aliases": [field],
            "display_order": order,
            "table_id": "hotel-list",
            "table_complete": True,
        }
        for order, (field, label) in enumerate([
            ("billCode", "单据编号"),
            ("startTime", "入住时间"),
            ("processStatus", "流程状态"),
        ])
    ]

    schema = _schema_from_response_value(response)
    _apply_output_presentation_evidence(schema, evidence)
    fields = schema["properties"]["records"]["items"]["properties"]

    assert fields["id"]["x-dano-display"] is False
    assert fields["processInstanceId"]["x-dano-display"] is False
    assert fields["hotelName"].get("x-dano-display") is not False
    assert fields["endTime"].get("x-dano-display") is not False
    assert fields["totalAmt"].get("x-dano-display") is not False


def test_identifier_relation_uses_record_id_when_that_is_the_exact_match():
    query = FlowStep(
        step_id="query", method="GET", path="/applications/page",
        response_json={"data": {"list": [{
            "id": "record-42",
            "processInstanceId": "process-42",
        }]}},
    )
    delete = FlowStep(
        step_id="delete", method="DELETE", path="/applications/delete",
        params=[ParamField(
            path="id", key="id", value="record-42", default_value="record-42",
            required=True,
        )],
        response_json={"code": 0},
    )
    spec = FlowSpec(
        steps=[query, delete],
        capabilities=[
            FlowCapability(
                name="query_applications", kind="query_status",
                nodes=[{"id": "query_call", "type": "call", "step_id": "query"}],
            ),
            FlowCapability(
                name="delete_application", kind="delete",
                nodes=[{"id": "delete_call", "type": "call", "step_id": "delete"}],
            ),
        ],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    target = prepared.capabilities[1].input_schema["properties"]["id"]

    assert target["x-dano-identifier-role"] == "record"
    assert target["title"] == "记录ID"
    assert prepared.capability_relations[0].from_output == "records[].id"


def test_identifier_relation_is_not_generated_when_recorded_value_is_ambiguous():
    query = FlowStep(
        step_id="query", method="GET", path="/applications/page",
        response_json={"data": {"list": [{
            "id": "same-identifier",
            "processInstanceId": "same-identifier",
        }]}},
    )
    withdraw = FlowStep(
        step_id="withdraw", method="DELETE", path="/process/cancel",
        params=[ParamField(
            path="id", key="id", value="same-identifier",
            default_value="same-identifier", required=True,
        )],
        response_json={"code": 0},
    )
    spec = FlowSpec(
        steps=[query, withdraw],
        capabilities=[
            FlowCapability(
                name="query_applications", kind="query_status",
                nodes=[{"id": "query_call", "type": "call", "step_id": "query"}],
            ),
            FlowCapability(
                name="withdraw_application", kind="withdraw",
                nodes=[{"id": "withdraw_call", "type": "call", "step_id": "withdraw"}],
            ),
        ],
    )

    prepared = prepare_flow_spec_for_publish(spec)
    target = prepared.capabilities[1].input_schema["properties"]["id"]

    assert prepared.capability_relations == []
    assert "x-dano-derived-from-query" not in target
    assert target["default"] == "same-identifier"


def test_identifier_relation_never_binds_an_unrelated_text_field_by_value():
    query = FlowStep(
        step_id="query", method="GET", path="/applications/page",
        response_json={"data": {"list": [{"id": "record-42"}]}},
    )
    update = FlowStep(
        step_id="update", method="PUT", path="/applications/update",
        params=[ParamField(
            path="remark", key="备注", value="record-42",
            default_value="record-42", required=True,
        )],
        response_json={"code": 0},
    )
    spec = FlowSpec(
        steps=[query, update],
        capabilities=[
            FlowCapability(
                name="query_applications", kind="query_status",
                nodes=[{"id": "query_call", "type": "call", "step_id": "query"}],
            ),
            FlowCapability(
                name="update_application", kind="update",
                nodes=[{"id": "update_call", "type": "call", "step_id": "update"}],
            ),
        ],
    )

    prepared = prepare_flow_spec_for_publish(spec)

    assert prepared.capability_relations == []
    assert "x-dano-derived-from-query" not in (
        prepared.capabilities[1].input_schema["properties"]["备注"]
    )


def test_export_rebuilds_lossy_persisted_capabilities_from_frozen_recording_evidence():
    requests = [
        _get(
            1,
            "https://oa.test/admin-api/oa/hotel-apply/page?pageNo=1&pageSize=10",
            {"code": 0, "data": {"list": [{"id": "record-1", "hotelName": "酒店 A"}], "total": 1}},
        ),
        _get(
            2,
            "https://oa.test/admin-api/oa/hotel-apply/page?pageNo=1&pageSize=10&hotelName=1",
            {"code": 0, "data": {"list": [], "total": 0}},
        ),
    ]
    spec = to_flow_spec(
        requests,
        samples={"酒店名称": "1"},
        field_evidence=[{
            "label": "酒店名称",
            "value": "1",
            "field_aliases": ["hotelName"],
            "control_kind": "text",
        }],
    )
    spec = asyncio.run(orchestrate_flow_capabilities(spec, submission={"ops": []}))
    query = next(capability for capability in spec.capabilities if capability.kind == "query_status")
    query.name = "query_hotel_apply"
    # Reproduce the legacy persisted bug: a populated URL was mistaken for
    # requiredness and the top-level capability projection lost record fields.
    for step in spec.steps:
        for param in step.params:
            if param.path == "query.hotelName":
                param.required = True
                param.evidence = [item for item in param.evidence if item.get("kind") != "page_required"]
    query.input_schema["required"] = ["酒店名称"]

    lossy = {
        "name": "query_hotel_apply",
        "kind": "query_status",
        "title": "查询酒店申请记录",
        "input_schema": {
            "type": "object",
            "properties": {"酒店名称": {"type": "string"}},
            "required": ["酒店名称"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"records": {"type": "array", "items": {}}},
        },
    }
    skill = SkillSpec(
        skill_id="A-OA.recorded_hotel",
        subsystem=Subsystem.OA,
        action="recorded_hotel",
        title="酒店申请",
        risk_level=RiskLevel.L1,
        has_api=False,
        capabilities=[lossy],
        api_request={
            "capabilities": [lossy],
            "_release_snapshot": {"flow_spec": spec.model_dump(mode="json")},
        },
    )

    upgraded = _upgrade_recorded_skill_for_export(skill)
    capability = next(item for item in upgraded.capabilities if item["name"] == "query_hotel_apply")
    properties = capability["input_schema"]["properties"]
    record_properties = capability["output_schema"]["properties"]["records"]["items"]["properties"]

    assert capability["input_schema"]["required"] == []
    assert properties["pageNo"]["default"] == 1
    assert properties["pageSize"]["default"] == 10
    assert "default" not in properties["酒店名称"]
    assert properties["酒店名称"]["x-dano-wire-type"] == "string"
    assert record_properties["id"]["type"] == "string"
