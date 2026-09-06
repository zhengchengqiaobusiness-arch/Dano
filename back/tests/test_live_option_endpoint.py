"""Public dataSource.endpoint must load live options the same way source_url did."""

from __future__ import annotations

import asyncio

import dano.execution.page.request_capture as request_capture
from dano.catalog.manifest import _enum_facts, _field_call_metadata, _select_semantic_type
from dano.execution.page.capability_io import _capability_input_schema
from dano.execution.page.flow_spec_core.models import ParamField
from dano.export.agent_skills import _question_data_source, _schema_option_fields


DEPT_SELECT = {
    "param": "deptId",
    "endpoint": "/admin-api/system/dept/simple-list",
    "method": "GET",
    "value_key": "id",
    "label_key": "name",
}


def test_fetch_select_list_uses_public_endpoint_when_source_url_missing(monkeypatch) -> None:
    seen: dict[str, str] = {}

    async def fake_fetch_list(url, base_url, *_args, **_kwargs):
        seen["url"] = url
        seen["base_url"] = base_url
        items = request_capture._FetchedItems([{"id": 103, "name": "研发部"}])
        items.source_status = 200
        return items

    monkeypatch.setattr(request_capture, "_fetch_list", fake_fetch_list)
    items = asyncio.run(request_capture._fetch_select_list(
        DEPT_SELECT,
        "https://ruoyioffice.com",
        None,
        None,
        True,
        None,
    ))
    assert seen["url"] == "/admin-api/system/dept/simple-list"
    assert seen["base_url"] == "https://ruoyioffice.com"
    assert [item["name"] for item in items] == ["研发部"]


def test_fetch_field_options_accepts_endpoint_only_select(monkeypatch) -> None:
    async def fake_fetch(sel, *_args, **_kwargs):
        items = request_capture._FetchedItems([{"id": 103, "name": "研发部"}])
        items.source_status = 200
        return items

    monkeypatch.setattr(request_capture, "_fetch_select_list", fake_fetch)
    result = asyncio.run(request_capture.fetch_field_options(
        {"steps": [{"selects": [DEPT_SELECT]}]},
        "deptId",
        base_url="https://ruoyioffice.com",
    ))
    assert result["count"] == 1
    assert result["options"] == [{"label": "研发部", "value": 103}]
    assert "没有实时选项来源" not in str(result.get("note") or "")


def test_question_data_source_reads_public_datasource() -> None:
    source = _question_data_source({
        "type": "number",
        "title": "部门",
        "dataSource": {
            "type": "api",
            "endpoint": "/admin-api/system/dept/simple-list",
            "method": "GET",
            "params": {},
            "resultPath": "data",
            "idField": "id",
            "labelField": "name",
            "childrenField": "children",
        },
    })
    assert source is not None
    assert source["endpoint"] == "/admin-api/system/dept/simple-list"
    assert source["childrenField"] == "children"


def test_schema_option_fields_include_datasource() -> None:
    assert _schema_option_fields({
        "type": "object",
        "properties": {
            "deptId": {
                "type": "number",
                "dataSource": {
                    "type": "api",
                    "endpoint": "/admin-api/system/dept/simple-list",
                },
            },
            "endDate": {"type": "string"},
        },
    }) == ["deptId"]


def test_capability_schema_writes_public_datasource() -> None:
    schema = _capability_input_schema(
        [
            ParamField(
                path="query.deptId",
                key="deptId",
                type="number",
                source_kind="api_option",
                exposed_to_user=True,
                source={
                    "source_url": "/admin-api/system/dept/simple-list",
                    "source_method": "GET",
                    "value_key": "id",
                    "label_key": "name",
                    "children_key": "children",
                },
            ),
        ],
        {"step_stats"},
    )
    field = schema["properties"]["deptId"]
    assert field["dataSource"]["endpoint"] == "/admin-api/system/dept/simple-list"
    assert field["dataSource"]["childrenField"] == "children"
    assert field["dataSource"]["labelField"] == "name"


def test_catalog_treats_endpoint_as_live_option_source() -> None:
    _opts, _map, has_source, static = _enum_facts(DEPT_SELECT)
    assert has_source is True
    assert static is False
    assert _select_semantic_type("number", DEPT_SELECT) == "enum"
    meta = _field_call_metadata(
        type("Skill", (), {"field_types": {}})(),
        {"deptId": {"type": "number"}},
        {"deptId": DEPT_SELECT},
    )
    assert meta["deptId"]["options_source"] == "/admin-api/system/dept/simple-list"
