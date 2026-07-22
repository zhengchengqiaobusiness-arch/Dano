from __future__ import annotations

import asyncio

from dano.execution.page.flow_spec import (
    FlowSpec,
    FlowStep,
    ParamField,
    _NO_SCHEMA_DEFAULT,
    _schema_default_for_param,
    orchestrate_flow_capabilities,
    to_flow_spec,
)
from dano.export.agent_skills import _upgrade_recorded_skill_for_export
from dano.orchestrator.types import SkillSpec
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
    assert properties["酒店名称"]["default"] == "1"

    item_properties = capability.output_schema["properties"]["records"]["items"]["properties"]
    assert item_properties["id"]["type"] == "string"
    assert item_properties["hotelName"]["type"] == "string"
    assert item_properties["processStatus"]["type"] == "number"


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
    )) == "审批中"
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
    )) == "2026-07-15 09:30:00"


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
    capability = next(cap for cap in spec.capabilities if cap.kind == "submit")
    properties = capability.input_schema["properties"]

    assert set(capability.input_schema["required"]) == {"id", "撤回原因"}
    assert properties["id"]["default"] == "process-1"
    assert "x-dano-apply-default" not in properties["id"]
    assert spec.capability_relations == []


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
    assert properties["酒店名称"]["default"] == "1"
    assert properties["酒店名称"]["x-dano-wire-type"] == "string"
    assert record_properties["id"]["type"] == "string"
