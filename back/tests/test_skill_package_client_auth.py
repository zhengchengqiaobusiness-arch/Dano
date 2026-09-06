from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from uuid import UUID

from dano.execution.page.flow_spec import FlowSpec
from dano.export.skill_package.renderer import (
    _CAPABILITY_TEMPLATE,
    _CLIENT_TEMPLATE,
    package_slug,
    render_skill_package,
)
from dano.onboarding.skill_generation.models import SkillGenerationRequest
from dano.onboarding.skill_generation.planner import propose_deterministic_plan
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem


def _render_client(tmp_path: Path, *, tenant: str = "111") -> Path:
    spec = FlowSpec.model_validate({
        "subsystem": "oa",
        "title": "业务办理",
        "capabilities": [{
            "capability_id": "cap_query",
            "name": "query_records",
            "title": "查询记录",
            "kind": "query",
            "step_ids": ["step_query"],
            "request_refs": [{"step_id": "step_query", "usage": "execute"}],
            "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
            "output_schema": {"type": "object"},
        }],
        "steps": [{
            "step_id": "step_query",
            "name": "查询",
            "method": "GET",
            "path": "/api/records",
            "url": "https://example.test/api/records",
        }],
    })
    request = SkillGenerationRequest(title="业务办理", business_description="查询记录。")
    plan = propose_deterministic_plan(spec, request, {"cap_query"}, "fp-auth")
    plan_payload = plan.model_dump(mode="json")
    skill = SkillSpec(
        skill_id="oa.action_abcd1234abcd1234abcd1234abcd1234",
        tenant=tenant,
        subsystem=Subsystem("oa"),
        action="action_abcd1234abcd1234abcd1234abcd1234",
        title="业务办理",
        risk_level=RiskLevel.L3,
        recording_asset_id=UUID(int=0),
        api_request={
            "capabilities": [{
                "capability_id": "cap_query",
                "name": "query_records",
                "title": "查询记录",
                "kind": "query",
                "step_ids": ["step_query"],
                "request_refs": [{"step_id": "step_query", "usage": "execute"}],
                "input_schema": spec.capabilities[0].input_schema,
            }],
            "steps": [{
                "step_id": "step_query",
                "method": "GET",
                "url": "https://example.test/api/records",
                "path": "/api/records",
            }],
            "_skill_plan": plan_payload,
            "_release_snapshot": {
                "skill_plan": plan_payload,
                "flow_spec": spec.model_dump(mode="json"),
            },
        },
        call_metadata={"skill_plan": plan_payload},
    )
    slug = render_skill_package(skill, str(tmp_path), tenant=tenant)
    return tmp_path / slug / "scripts" / "client.py"


def _load_client(path: Path):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location("dano_exported_client", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path.parent))


def test_client_template_reads_tenant_map_and_live_token_before_cache() -> None:
    source = _CLIENT_TEMPLATE
    live_fn = source.index("def _live_headers()")
    cache_fn = source.index("def _cache_headers()")
    auth_fn = source.index("def auth_headers()")
    assert "DANO_TENANT_KEYS_JSON" in source
    assert live_fn < auth_fn
    body = source[auth_fn:]
    assert body.index("_live_headers()") < body.index("_cache_headers()")


def test_exported_operation_exposes_authenticated_list_options_command() -> None:
    assert 'command.add_argument("--list-options"' in _CAPABILITY_TEMPLATE
    assert "option_choices(PLAN, args.list_options, context)" in _CAPABILITY_TEMPLATE


def test_exported_client_lists_fixed_enum_without_dynamic_source(tmp_path: Path) -> None:
    client = _load_client(_render_client(tmp_path))

    options = client.option_choices(
        {
            "input_schema": {
                "properties": {
                    "status": {
                        "enum": ["0", "1"],
                        "x-enum-options": [
                            {"id": "0", "label": "未提交"},
                            {"id": "1", "label": "审批中"},
                        ],
                    },
                },
            },
            "steps": [],
        },
        "status",
    )

    assert options == [
        {"id": "0", "label": "未提交"},
        {"id": "1", "label": "审批中"},
    ]


def test_exported_client_lists_people_with_safe_display_extras(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _load_client(_render_client(tmp_path))
    seen: list[dict[str, object]] = []

    def fake_http_json(method, *, url, **kwargs):  # noqa: ANN001
        query = dict(kwargs.get("query") or {})
        seen.append({"method": method, "url": url, "query": query})
        page = int(query.get("pageNum") or 1)
        person = {
            "userId": 127 + page,
            "nickName": "张段誉" if page == 1 else "王语嫣",
            "dept": {"deptName": "项目管理部"},
            "phonenumber": "must-not-leak",
        }
        return {
            "ok": True,
            "data": {
                "payload": {
                    "members": [person],
                    "total": 2,
                },
            },
        }

    monkeypatch.setattr(client, "http_json", fake_http_json)
    options = client.option_choices(
        {
            "steps": [{
                "selects": [{
                    "param": "ccedList",
                    "path": "body.ccedList",
                    "endpoint": "/prod-api/system/user/list",
                    "method": "GET",
                    "source_params": {"pageNum": 1, "pageSize": 1, "status": "0"},
                    "result_path": "payload.members",
                    "total_path": "payload.total",
                    "search_param": "userName",
                    "page_param": "pageNum",
                    "page_size_param": "pageSize",
                    "page_size": 1,
                    "value_key": "userId",
                    "label_key": "nickName",
                    "extra_fields": ["dept.deptName"],
                }],
            }],
        },
        "ccedList",
        {"userName": "张"},
    )

    assert seen == [
        {
            "method": "GET",
            "url": "/prod-api/system/user/list",
            "query": {"pageNum": 1, "pageSize": 1, "status": "0", "userName": "张"},
        },
        {
            "method": "GET",
            "url": "/prod-api/system/user/list",
            "query": {"pageNum": 2, "pageSize": 1, "status": "0", "userName": "张"},
        },
    ]
    assert options == [
        {
            "id": "128",
            "label": "张段誉",
            "extra": {"dept.deptName": "项目管理部"},
        },
        {
            "id": "129",
            "label": "王语嫣",
            "extra": {"dept.deptName": "项目管理部"},
        },
    ]


def test_exported_client_builds_selected_people_as_recorded_object_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _load_client(_render_client(tmp_path))
    calls: list[tuple[str, str, object]] = []

    def fake_http_json(method, *, url, **_kwargs):  # noqa: ANN001
        calls.append((method, url, _kwargs.get("query")))
        return {
            "ok": True,
            "data": {
                "rows": [{
                    "userId": 128,
                    "nickName": "张段誉",
                    "dept": {"deptName": "项目管理部"},
                }],
            },
        }

    monkeypatch.setattr(client, "http_json", fake_http_json)
    values, projections = client._apply_selects(
        {
            "selects": [{
                "param": "ccedList",
                "path": "body.ccedList",
                "endpoint": "/prod-api/system/user/list",
                "method": "GET",
                "params": {"pageNum": 1, "pageSize": 20, "status": "0"},
                "value_key": "userId",
                "label_key": "nickName",
                "multi": True,
                "label_subkey": "toNickName",
                "element_template": {
                    "billType": {"const": "duty_leave"},
                    "toUserId": {"from": "item", "item_key": "userId"},
                    "toNickName": {"from": "item", "item_key": "nickName"},
                    "toDeptName": {"from": "item", "item_key": "dept.deptName"},
                },
            }],
        },
        {"ccedList": ["张段誉"]},
        {},
    )

    assert calls == [(
        "GET",
        "/prod-api/system/user/list",
        {"pageNum": 1, "pageSize": 20, "status": "0"},
    )]
    assert projections == {}
    assert values["ccedList"] == [{
        "billType": "duty_leave",
        "toUserId": 128,
        "toNickName": "张段誉",
        "toDeptName": "项目管理部",
    }]


def test_exported_client_uses_mapped_key_and_ignores_stale_cache(tmp_path: Path, monkeypatch) -> None:
    client_path = _render_client(tmp_path, tenant="111")
    client = _load_client(client_path)
    monkeypatch.setattr(client.Path, "home", staticmethod(lambda: tmp_path))
    cache = tmp_path / ".dano" / "sessions" / "111__oa.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"headers": {"Authorization": "Bearer stale"}}), encoding="utf-8")
    monkeypatch.setenv("DANO_URL", "https://dano.test")
    monkeypatch.setenv("DANO_TENANT", "111")
    monkeypatch.setenv("DANO_TENANT_KEY", "wrong-single-key")
    monkeypatch.setenv("DANO_TENANT_KEYS_JSON", json.dumps({"111": "key-for-111"}))
    monkeypatch.delenv("DANO_AUTH_HEADERS", raising=False)
    seen: dict[str, object] = {}

    class Response:
        status_code = 200
        is_success = True

        def json(self):
            return {"headers": {"Authorization": "Bearer fresh"}}

        def raise_for_status(self):
            return None

    def fake_get(url, params=None, headers=None, timeout=None):
        seen["url"] = url
        seen["params"] = params
        seen["headers"] = headers
        return Response()

    monkeypatch.setattr(client.httpx, "get", fake_get)

    assert client.auth_headers() == {"Authorization": "Bearer fresh"}
    assert seen["headers"]["X-Tenant-Key"] == "key-for-111"
    assert seen["params"]["tenant"] == "111"
    assert "/v1/settings/token/raw" in str(seen["url"])


def test_exported_client_does_not_fall_back_to_cache_after_forbidden_key(
    tmp_path: Path, monkeypatch,
) -> None:
    client_path = _render_client(tmp_path, tenant="111")
    client = _load_client(client_path)
    monkeypatch.setattr(client.Path, "home", staticmethod(lambda: tmp_path))
    cache = tmp_path / ".dano" / "sessions" / "111__oa.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"headers": {"Authorization": "Bearer stale"}}), encoding="utf-8")
    monkeypatch.setenv("DANO_URL", "https://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "wrong-single-key")
    monkeypatch.delenv("DANO_TENANT_KEYS_JSON", raising=False)
    monkeypatch.delenv("DANO_AUTH_HEADERS", raising=False)

    class Forbidden:
        status_code = 403
        is_success = False

        def json(self):
            return {"detail": "不能读取其他租户的 token"}

        def raise_for_status(self):
            raise RuntimeError("forbidden")

    monkeypatch.setattr(client.httpx, "get", lambda *args, **kwargs: Forbidden())

    try:
        client.auth_headers()
    except RuntimeError as exc:
        assert "forbidden" in str(exc)
        return
    raise AssertionError("wrong tenant key must not silently use the stale cache")
