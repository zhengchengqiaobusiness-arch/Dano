"""Phase A3:上架目录只露业务 skill,复合流程的步骤接口隐藏(纯离线,fake store)。"""
from __future__ import annotations

from uuid import uuid4

import pytest

from dano.orchestrator.skills import SkillRegistry
from dano.shared.asset_bodies import WorkflowSkillBody, WorkflowStep
from dano.shared.enums import AssetType, Subsystem


class _Env:
    def __init__(self, body: dict, asset_key: str) -> None:
        self.body, self.asset_key, self.asset_id, self.version = body, asset_key, uuid4(), 1


def _conn_env(action: str, *, workflow_step: bool = False,
              visibility: str = "catalog", business: str = "") -> _Env:
    return _Env({"action": action, "field_bindings": [], "risk_level": "L1",
                 "workflow_step": workflow_step, "visibility": visibility,
                 "business": business}, action)


class _Store:
    def __init__(self, by_type: dict) -> None:
        self.by_type = by_type

    async def list_published(self, asset_type, scope):  # noqa: ANN001
        return self.by_type.get(asset_type, [])


async def test_workflow_steps_hidden_only_business_skill_shown():
    wf = WorkflowSkillBody(
        action="submit_leave", title="提交请假",
        steps=[WorkflowStep(action="start_leave_flow", inputs={"templateId": "const:t"}),
               WorkflowStep(action="submit_flow_task", inputs={"taskId": "step:start_leave_flow.data.taskId"})],
        user_fields=["leaveDays"], required_fields=["leaveDays"])
    store = _Store({
        AssetType.WORKFLOW: [_Env(wf.model_dump(), "submit_leave")],
        # 两个步骤连接器(应隐藏)+ 一个独立查询连接器(应可见)
        AssetType.CONNECTOR: [_conn_env("start_leave_flow"), _conn_env("submit_flow_task"),
                              _conn_env("query_balance")],
    })
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    actions = {s.action for s in reg.skills}

    assert "submit_leave" in actions                      # 业务 skill 露出
    assert "query_balance" in actions                     # 独立查询露出
    assert "start_leave_flow" not in actions              # 步骤接口隐藏
    assert "submit_flow_task" not in actions              # 步骤接口隐藏

    sl = next(s for s in reg.skills if s.action == "submit_leave")
    assert sl.is_workflow is True


async def test_connector_fact_check_from_body_not_gated_by_action_name():
    """P3:事实核查随**连接器资产体**走 —— 通用动作也能带核查;ACTION_META 退为原型 demo 兜底。"""
    store = _Store({AssetType.CONNECTOR: [
        _Env({"action": "create_customer", "field_bindings": [], "risk_level": "L3",
              "fact_check_query": "query_customer", "fact_check_expr": "response.id != null"},
             "create_customer"),
        _Env({"action": "create_order", "field_bindings": [], "risk_level": "L3"}, "create_order"),
        _Env({"action": "create_leave", "field_bindings": [], "risk_level": "L3"}, "create_leave"),
    ]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem("B-CRM")])
    by = {s.action: s for s in reg.skills}
    # 资产体声明 → 通用连接器(非 demo 动作名)也有事实核查
    assert by["create_customer"].fact_check_query == "query_customer"
    assert by["create_customer"].fact_check_expr == "response.id != null"
    # 通用连接器无声明、非 demo 动作 → 无核查(诚实,不臆造)
    assert by["create_order"].fact_check_query is None
    assert by["create_order"].fact_check_expr is None
    # 原型 demo 动作未在体里声明 → ACTION_META 兜底(向后兼容)
    assert by["create_leave"].fact_check_query == "query_balance"


async def test_registry_exposes_capability_lookup_for_published_assets():
    store = _Store({AssetType.CONNECTOR: [
        _Env({
            "action": "query_daily",
            "field_bindings": [],
            "risk_level": "L1",
            "capabilities": [{"name": "query_status", "kind": "query_status"}],
        }, "query_daily"),
    ]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])

    assert reg.get_by_skill_id("A-OA.query_daily") is not None
    assert reg.get_capability("A-OA.query_daily", "query_status") == {
        "name": "query_status",
        "kind": "query_status",
    }


async def test_workflow_step_connector_never_exposed():
    # 即便某 workflow_step 连接器没有任何复合流程引用(孤儿),也绝不单独露出,不污染目录
    store = _Store({AssetType.CONNECTOR: [_conn_env("query_balance"),
                                          _conn_env("orphan_submit_step", workflow_step=True)]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    actions = {s.action for s in reg.skills}
    assert "query_balance" in actions
    assert "orphan_submit_step" not in actions


# ── 契约层:剔除注入字段 + 数值类型保真(选项 B 治本)──────────────────────────
def test_manifest_strips_flow_internal_and_types_numbers():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(
        skill_id="A-OA.submit_demo_purchase", subsystem=Subsystem.OA, action="submit_demo_purchase",
        risk_level=RiskLevel.L3, title="采购申请提交", is_workflow=True,
        field_docs={"amount": "采购金额(元)", "quantity": "采购数量"},
        required_fields=["title", "quantity", "amount", "reason", "templateId", "procInsId"],
        optional_fields=[])
    props = to_manifest(sk).parameters["properties"]
    assert "templateId" not in props and "procInsId" not in props   # 注入字段被剔除
    assert props["amount"]["type"] == "number"
    assert props["quantity"]["type"] == "number"
    assert props["reason"]["type"] == "string"


def test_field_types_override_wins_over_heuristic():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(skill_id="A-OA.x", subsystem=Subsystem.OA, action="x", risk_level=RiskLevel.L1,
                   field_types={"code": "string", "qty": "integer"},
                   required_fields=["code", "qty"], optional_fields=[])
    props = to_manifest(sk).parameters["properties"]
    assert props["code"]["type"] == "string"     # 信源声明 string,压过名字启发式
    assert props["qty"]["type"] == "integer"


def test_manifest_capability_confirmation_is_capability_scoped():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(
        skill_id="A-OA.submit_form",
        subsystem=Subsystem.OA,
        action="submit_form",
        risk_level=RiskLevel.L3,
        has_api=False,
        capabilities=[
            {"name": "query_status", "kind": "query_status"},
            {"name": "submit_batch", "kind": "submit_batch"},
        ],
    )
    caps = {c["name"]: c for c in to_manifest(sk).capabilities}

    assert caps["query_status"]["requires_confirmation"] is False
    assert caps["submit_batch"]["requires_confirmation"] is True


def test_read_capability_ignores_stale_explicit_confirmation_flag():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(
        skill_id="A-OA.query",
        subsystem=Subsystem.OA,
        action="query",
        risk_level=RiskLevel.L3,
        has_api=False,
        capabilities=[{
            "name": "query_status",
            "kind": "query_status",
            "requires_confirmation": True,
        }],
    )

    assert to_manifest(sk).capabilities[0]["requires_confirmation"] is False


def test_manifest_collapses_stale_enum_description_snapshots():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(
        skill_id="A-OA.submit",
        subsystem=Subsystem.OA,
        action="submit",
        risk_level=RiskLevel.L3,
        has_api=False,
        capabilities=[{
            "name": "submit",
            "kind": "submit",
            "input_schema": {
                "type": "object",
                "properties": {
                    "类型": {
                        "type": "string",
                        "enum": ["病假", "事假", "年假"],
                        "description": "页面枚举选项：病假=2；页面枚举选项：病假=2、事假=1、年假=3",
                    },
                },
            },
        }],
    )

    prop = to_manifest(sk).capabilities[0]["input_schema"]["properties"]["类型"]
    assert prop["description"] == "页面枚举选项：病假=2、事假=1、年假=3"


def test_manifest_exports_recording_release_identity_without_embedded_flow_body():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    skill = SkillSpec(
        skill_id="A-OA.release",
        subsystem=Subsystem.OA,
        action="release",
        risk_level=RiskLevel.L3,
        has_api=False,
        api_request={
            "steps": [{"method": "POST", "path": "/api/submit"}],
            "_release_snapshot": {
                "protocol": "dano.recording_release.v1",
                "release_id": "flow-deadbeef",
                "flow_fingerprint": "deadbeef",
                "flow_spec": {"large": "body"},
                "interface_inventory": [{"name": "submit", "step_ids": ["submit"]}],
            },
        },
    )

    release = to_manifest(skill).flow["release"]

    assert release["release_id"] == "flow-deadbeef"
    assert release["flow_fingerprint"] == "deadbeef"
    assert "flow_spec" not in release


def test_manifest_exports_complete_capability_protocol_requirements():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    skill = SkillSpec(
        skill_id="A-OA.daily_report",
        subsystem=Subsystem.OA,
        action="daily_report",
        risk_level=RiskLevel.L3,
        verification_status="verified",
        verification_basis="fact_check_configured",
        api_request={"fact_check": {"endpoint": "/reports"}},
        capability_relations=[{
            "from_capability": "query_status", "from_output": "missing_dates",
            "to_capability": "submit_batch", "to_input": "entries",
        }],
        capabilities=[{
            "name": "submit_batch", "kind": "submit_batch",
            "input_schema": {
                "type": "object", "properties": {"entries": {
                    "type": "array", "items": {
                        "type": "object", "properties": {
                            "date": {"type": "string", "format": "date"},
                        }, "required": ["date"],
                    },
                }}, "required": ["entries"],
            },
            "output_schema": {
                "type": "object", "properties": {
                    "results": {"type": "array", "items": {"type": "object"}},
                }, "required": ["results"], "additionalProperties": False,
            },
        }],
    )

    manifest = to_manifest(skill)
    cap = manifest.capabilities[0]
    requirements = cap["validation_requirements"]
    assert cap["input_schema"] == cap["parameters"]
    assert cap["call_protocol"]["output_schema"] == cap["output_schema"]
    assert cap["call_protocol"]["requires_confirmation"] is True
    assert requirements["verification_required"] is True
    assert requirements["validate_batch_items_individually"] is True
    assert requirements["partial_success_must_be_reported"] is True
    assert requirements["verification_status"] == "verified"
    assert manifest.capability_relations[0]["automatic"] is False
    assert "partial_success" in manifest.call_protocol["result_statuses"]
    assert manifest.call_protocol["protocol"] == "dano.capability_call.v1"


def test_manifest_upgrades_legacy_output_required_from_explicit_mapping():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    skill = SkillSpec(
        skill_id="A-OA.query_records", subsystem=Subsystem.OA,
        action="query_records", risk_level=RiskLevel.L1,
        capabilities=[{
            "name": "query_status", "kind": "query_status",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {
                "type": "object",
                "properties": {"records": {"type": "array"}, "debug": {"type": "string"}},
                "required": [],
            },
            "output_mapping": [
                {"name": "records", "step_id": "query", "response_path": "data.records"},
            ],
        }],
    )

    manifest = to_manifest(skill)

    assert manifest.capabilities[0]["output_schema"]["required"] == ["records"]


def test_manifest_prefers_business_capability_title_over_technical_endpoint_title():
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    skill = SkillSpec(
        skill_id="A-OA.submit_form",
        subsystem=Subsystem.OA,
        action="submit_form",
        title="submit-process 流程(5 步)",
        risk_level=RiskLevel.L3,
        capabilities=[{"name": "submit", "kind": "submit", "title": "提交请假申请"}],
    )

    assert to_manifest(skill).title == "提交请假申请"


def test_manifest_normalizes_capability_identity_batch_schema_and_relations():
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _skill_md
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    skill = SkillSpec(
        skill_id="A-OA.daily_report",
        subsystem=Subsystem.OA,
        action="daily_report",
        risk_level=RiskLevel.L3,
        has_api=False,
        api_request={"_flow_spec": {"capability_relations": [{
            "type": "external_transform",
            "from_capability": "query_status",
            "from_output": "missing_dates",
            "to_capability": "submit_batch",
            "to_input": "entries",
        }]}},
        capabilities=[
            {"name": "query_status", "kind": "query_status", "title": "查询未填日期"},
            {
                "name": "submit_batch_legacy",
                "kind": "submit_batch",
                "title": "填报日报",
                "input_schema": {"type": "object", "properties": {"entries": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"date": {"type": "string"}}},
                }}},
            },
        ],
    )

    manifest = to_manifest(skill)
    submit = next(cap for cap in manifest.capabilities if cap["kind"] == "submit_batch")
    assert submit["name"] == "submit_batch"
    assert "批量" in submit["title"]
    assert submit["parameters"]["required"] == ["entries"]
    assert submit["parameters"]["additionalProperties"] is False
    assert submit["parameters"]["properties"]["entries"]["items"]["additionalProperties"] is False
    assert manifest.call_protocol["requires_explicit_capability"] is True
    assert manifest.capability_relations[0]["automatic"] is False
    assert "逐项校验" in manifest.capability_relations[0]["caller_responsibility"]
    markdown = _skill_md(manifest, "dano-a-oa-daily-report")
    assert "## 能力关系" in markdown
    assert "external_transform" in markdown
    assert "调用方责任" in markdown


def test_manifest_submit_rejects_pseudo_batch_identity_and_schema_at_export_gate():
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _export_contract_errors
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.fake_batch",
        subsystem=Subsystem.OA,
        action="fake_batch",
        risk_level=RiskLevel.L3,
        capabilities=[{
            "name": "submit_batch2",
            "kind": "submit",
            "title": "批量提交",
            "input_schema": {"type": "object", "properties": {
                "entries": {"type": "array", "items": {"type": "object"}},
            }},
        }],
    ))

    cap = manifest.capabilities[0]
    assert cap["name"] == cap["kind"] == "submit"
    assert cap["title"] == "提交"
    assert any("不能伪装成批量契约" in error for error in _export_contract_errors(manifest))


def test_exported_agent_script_uses_capability_scoped_contracts_and_fact_check(monkeypatch, capsys):
    import json
    import sys

    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _dano_call_py, _skill_md
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    skill = SkillSpec(
        skill_id="A-OA.daily_report",
        subsystem=Subsystem.OA,
        action="daily_report",
        title="日报查询与填报",
        risk_level=RiskLevel.L3,
        has_api=False,
        required_fields=["legacy_submit_field"],
        api_request={"fact_check": {"endpoint": "/report/page"}},
        capabilities=[
            {
                "name": "query_status",
                "kind": "query_status",
                "title": "查询未填日期",
                "input_schema": {
                    "type": "object",
                    "properties": {"month": {"type": "string"}},
                    "required": ["month"],
                },
            },
            {
                "name": "submit_batch",
                "kind": "submit_batch",
                "title": "批量填报日报",
                "input_schema": {
                    "type": "object",
                    "properties": {"entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"date": {"type": "string"}, "content": {"type": "string"}},
                            "required": ["date", "content"],
                        },
                    }},
                    "required": ["entries"],
                },
            },
        ],
    )
    source = _dano_call_py(to_manifest(skill))
    manifest = to_manifest(skill)
    markdown = _skill_md(manifest, "dano-a-oa-daily-report")
    assert "### `query_status` · 查询未填日期" in markdown
    assert "### `submit_batch` · 批量填报日报" in markdown
    assert "entries[].date" in markdown
    assert "多个独立能力" in markdown
    assert "必须显式指定" in markdown
    namespace = {"__name__": "generated_test"}
    exec(compile(source, "<generated-dano-call>", "exec"), namespace)  # noqa: S102

    assert namespace["CAPABILITY"] is None
    assert namespace["CAPABILITIES"]["query_status"]["required"] == ["month"]
    assert namespace["CAPABILITIES"]["submit_batch"]["required"] == ["entries"]
    assert namespace["CAPABILITIES"]["query_status"]["verify_required"] is False
    assert namespace["CAPABILITIES"]["submit_batch"]["verify_required"] is True

    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")
    monkeypatch.setattr(sys, "argv", ["dano_call.py"])
    namespace["main"]()
    selection = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert selection["status"] == "need_select"
    assert {item["name"] for item in selection["candidates"]} == {"query_status", "submit_batch"}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "state": "completed",
                "audit": {"fact_check": {"passed": False}},
                "exec_result": {"structured_output": {"code": 0}},
            }).encode()

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *args, **kwargs: _Response())
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py", "--capability", "query_status", "--month", "2026-05",
    ])
    namespace["main"]()
    query_result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert query_result["status"] == "succeeded"

    monkeypatch.setattr(sys, "argv", [
        "dano_call.py", "--capability", "submit_batch", "--entries",
        '[{"date":"2026-05-12","content":"开发"}]', "--confirm",
    ])
    with pytest.raises(SystemExit) as exc:
        namespace["main"]()
    assert exc.value.code == 1
    failed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert failed["status"] == "failed"
    assert "事实核查" in failed["reason"]


def test_exported_runtime_validates_batch_dates_confirmation_partial_and_output(monkeypatch, capsys):
    import json
    import sys

    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _dano_call_py
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    skill = SkillSpec(
        skill_id="A-OA.daily_report_runtime",
        subsystem=Subsystem.OA,
        action="daily_report_runtime",
        risk_level=RiskLevel.L3,
        capabilities=[{
            "name": "submit_batch", "kind": "submit_batch",
            "input_schema": {
                "type": "object", "properties": {"entries": {
                    "type": "array", "items": {
                        "type": "object", "properties": {
                            "date": {"type": "string", "format": "date"},
                            "content": {"type": "string", "minLength": 1},
                        }, "required": ["date", "content"],
                    },
                }}, "required": ["entries"],
            },
            "output_schema": {
                "type": "object", "properties": {
                    "results": {"type": "array", "items": {
                        "type": "object", "properties": {
                            "index": {"type": "integer"}, "status": {"type": "string"},
                        }, "required": ["index", "status"], "additionalProperties": False,
                    }},
                }, "required": ["results"], "additionalProperties": False,
            },
        }],
    )
    namespace = {"__name__": "generated_test"}
    exec(compile(_dano_call_py(to_manifest(skill)), "<generated-dano-call>", "exec"), namespace)  # noqa: S102
    monkeypatch.setenv("DANO_URL", "http://dano.test")
    monkeypatch.setenv("DANO_TENANT_KEY", "tenant-key")
    calls = []
    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *args, **kwargs: calls.append(args))

    monkeypatch.setattr(sys, "argv", [
        "dano_call.py", "--entries",
        '[{"date":"2026-07-12","content":"ok"},{"date":"2026-02-30","content":"bad"}]',
        "--confirm",
    ])
    with pytest.raises(SystemExit):
        namespace["main"]()
    invalid = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "input.entries[1].date" in invalid["reason"]
    assert calls == []

    monkeypatch.setattr(sys, "argv", [
        "dano_call.py", "--entries", '[{"date":"2026-07-12","content":"ok"}]',
    ])
    namespace["main"]()
    assert json.loads(capsys.readouterr().out.strip())["status"] == "need_confirm"
    assert calls == []

    class _Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    partial_payload = {
        "state": "partially_completed",
        "exec_result": {"structured_output": {"results": [
            {"index": 0, "status": "succeeded"}, {"index": 1, "status": "failed"},
        ]}},
    }
    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *args, **kwargs: _Response(partial_payload))
    monkeypatch.setattr(sys, "argv", [
        "dano_call.py", "--entries", '[{"date":"2026-07-12","content":"ok"}]', "--confirm",
    ])
    namespace["main"]()
    assert json.loads(capsys.readouterr().out.strip())["status"] == "partial_success"

    bad_output = {"state": "completed", "exec_result": {"structured_output": {"unexpected": True}}}
    monkeypatch.setattr(namespace["urllib"].request, "urlopen", lambda *args, **kwargs: _Response(bad_output))
    with pytest.raises(SystemExit):
        namespace["main"]()
    rejected = json.loads(capsys.readouterr().out.strip())
    assert rejected["status"] == "failed"
    assert "output_schema" in rejected["reason"]


def test_manifest_preserves_select_and_datetime_semantics():
    """选择型(enum)/日期(datetime)字段的语义不被塌成裸 string:
    enum → type=string + format=name-ref + 描述提示「传名字→ID」;datetime → format=date-time。
    (修真实导出里 领导/人力 显示成 string、日期丢类型的缺陷。)"""
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _ptype, _select_fields
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(skill_id="A-OA.submit_form", subsystem=Subsystem.OA, action="submit_form",
                   risk_level=RiskLevel.L3,
                   field_types={"领导": "enum", "人力": "enum", "startTime": "datetime", "请假类型": "number"},
                   required_fields=["领导", "人力", "startTime", "请假类型"], optional_fields=[])
    props = to_manifest(sk).parameters["properties"]
    # 选择型:不再是裸 string,带 name-ref 标记 + 「传名字→ID」提示
    assert props["领导"]["type"] == "string" and props["领导"]["format"] == "name-ref"
    assert "查内部 ID" in props["领导"]["description"] and "勿直接传 ID" in props["领导"]["description"]
    # 日期:带 date-time format
    assert props["startTime"]["format"] == "date-time"
    # 导出层「类型」列还原成语义类型,不再显示 string
    assert _ptype("领导", props, set()) == "枚举·名字→ID"
    assert _ptype("startTime", props, set()) == "datetime"
    assert _ptype("请假类型", props, {"请假类型"}) == "number"
    assert _select_fields(props) == ["领导", "人力"]
    # 不再硬塞人名示例『张三』(对选值字段如请假类型是错的);label=纯语义,供 SOP/复述用(简洁、不带约定括号)
    assert "张三" not in props["领导"]["description"]
    assert props["领导"]["label"] == "领导" and "传名字" not in props["领导"]["label"]


def test_manifest_exposes_call_metadata_without_polluting_function_schema():
    from dano.catalog.manifest import to_function_tool, to_manifest
    from dano.export.agent_skills import _dano_call_py
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(
        skill_id="A-OA.submit_form", subsystem=Subsystem.OA, action="submit_form",
        risk_level=RiskLevel.L3,
        field_types={"请假类型": "enum", "startTime": "datetime"},
        required_fields=["请假类型", "startTime"], optional_fields=[],
        call_metadata={"verification_status": "partially_verified", "recording_mode": "intercepted_submit"},
        api_request={"selects": [{
            "param": "请假类型", "source_url": "", "value_key": "value", "label_key": "label",
            "options": [{"label": "事假", "value": 1}, {"label": "病假", "value": 2}],
            "option_map": {"事假": 1, "病假": 2}, "enum_source": "dom", "enum_confirmed": True,
        }]},
    )

    m = to_manifest(sk)
    meta = m.call_metadata

    assert m.verification_status == "partially_verified"
    assert m.recording_mode == "intercepted_submit"
    assert meta["fields"]["请假类型"]["type"] == "enum"
    assert meta["fields"]["请假类型"]["enum_options"] == [{"label": "事假", "value": 1}, {"label": "病假", "value": 2}]
    assert meta["fields"]["请假类型"]["enum_value_map"] == {"事假": 1, "病假": 2}
    assert meta["fields"]["startTime"]["type"] == "datetime"
    assert "call_metadata" not in to_function_tool(m)["function"]["parameters"]
    assert 'NUMERIC = []' in _dano_call_py(m)
    assert '"请假类型"' not in _dano_call_py(m).split("NUMERIC =", 1)[1].splitlines()[0]


def test_manifest_select_metadata_overrides_numeric_body_type():
    """页面下拉提交短码(type=2)时,body 推断可能是 number；select 元数据才是调用契约的权威。"""
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(
        skill_id="A-OA.submit_form", subsystem=Subsystem.OA, action="submit_form",
        risk_level=RiskLevel.L3,
        field_types={"类型": "number"},
        required_fields=["类型"], optional_fields=[],
        api_request={"selects": [{
            "param": "类型", "source_url": "", "value_key": "", "label_key": "",
            "options": ["病假", "事假", "婚假"],
            "option_map": {"病假": 2, "事假": 1, "婚假": 3},
            "enum_source": "dom", "enum_confirmed": True,
        }]},
    )

    m = to_manifest(sk)
    prop = m.parameters["properties"]["类型"]

    assert prop["type"] == "string"
    assert prop["format"] == "name-ref"
    assert prop["enum"] == ["病假", "事假", "婚假"]
    assert prop["x-enum-value-map"] == {"病假": 2, "事假": 1, "婚假": 3}
    assert m.call_metadata["fields"]["类型"]["type"] == "enum"
    assert m.call_metadata["fields"]["类型"]["enum_value_map"] == {"病假": 2, "事假": 1, "婚假": 3}


def test_manifest_does_not_inline_value_only_enum_options():
    """老资产若只存了 1/2/3 这类内部值,manifest 不再把它们当用户可选显示名。"""
    from dano.catalog.manifest import to_manifest
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    sk = SkillSpec(
        skill_id="A-OA.submit_form", subsystem=Subsystem.OA, action="submit_form",
        risk_level=RiskLevel.L3,
        field_types={"类型": "enum"},
        required_fields=["类型"], optional_fields=[],
        api_request={"selects": [{
            "param": "类型", "source_url": "", "value_key": "", "label_key": "",
            "options": ["1", "2", "3"],
            "enum_source": "manual", "enum_confirmed": True,
        }]},
    )

    m = to_manifest(sk)
    prop = m.parameters["properties"]["类型"]

    assert prop["type"] == "string"
    assert prop["format"] == "name-ref"
    assert "enum" not in prop
    assert "x-options" not in prop
    assert "enum_options" not in m.call_metadata["fields"]["类型"]


def test_capability_manifest_removes_hard_enum_for_nested_live_options():
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _options_md
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    skill = SkillSpec(
        skill_id="A-OA.leave",
        subsystem=Subsystem.OA,
        action="leave",
        title="请假查询与提交",
        risk_level=RiskLevel.L3,
        capabilities=[{
            "name": "submit_batch",
            "kind": "submit_batch",
            "title": "批量提交请假",
            "inputs": [{
                "key": "审批人", "path": "[0].approverId", "source_kind": "api_option",
                "source": {"source_step_id": "users", "source_url": "/users"},
            }],
            "input_schema": {
                "type": "object",
                "properties": {"entries": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"审批人": {
                        "type": "string", "enum": ["旧用户"], "x-options": ["旧用户"],
                    }}},
                }},
                "required": ["entries"],
            },
        }],
    )

    manifest = to_manifest(skill)
    field = manifest.capabilities[0]["parameters"]["properties"]["entries"]["items"]["properties"]["审批人"]
    assert field["x-options-source"] is True
    assert "enum" not in field
    assert "x-options" not in field
    assert "--capability submit_batch --list-options 审批人" in (_options_md(manifest) or "")


def test_live_option_marker_discards_corrupted_snapshot_and_multi_title_covers_write():
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _options_md
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.leave", subsystem=Subsystem.OA, action="leave",
        title="查询流程状态", risk_level=RiskLevel.L3,
        capabilities=[
            {"name": "query_status", "kind": "query_status", "title": "查询流程状态"},
            {
                "name": "submit", "kind": "submit", "title": "提交请假申请",
                "input_schema": {"type": "object", "properties": {"请假类型": {
                    "type": "string", "enum": ["冰机", "实际"],
                    "x-options": [{"label": "冰机", "value": 2}],
                    "x-options-source": True,
                    "x-options-source-meta": {"source_url": "/dict/leave-type"},
                }}},
            },
        ],
    ))

    field = manifest.capabilities[1]["parameters"]["properties"]["请假类型"]
    assert "enum" not in field and "x-options" not in field
    assert field["x-options-source"] is True
    assert "冰机" not in field["description"]
    assert "运行期接口实时获取" in field["description"]
    assert "冰机" not in (_options_md(manifest) or "")
    assert manifest.title == "查询流程状态 · 提交请假申请"


def test_export_quality_gate_rejects_routing_only_batch_entries():
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _export_contract_errors
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.bad_batch",
        subsystem=Subsystem.OA,
        action="bad_batch",
        title="请假提交",
        risk_level=RiskLevel.L3,
        capabilities=[{
            "name": "submit_batch",
            "kind": "submit_batch",
            "input_schema": {
                "type": "object",
                "properties": {"entries": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "领导审批人": {"type": "string"},
                        "人力审批人": {"type": "string"},
                    }},
                }},
                "required": ["entries"],
            },
        }],
    ))

    assert any("人员列表误判" in error for error in _export_contract_errors(manifest))


def test_export_quality_gate_rejects_internal_process_id_and_submit_routing_entries():
    from dano.catalog.manifest import to_manifest
    from dano.export.agent_skills import _export_contract_errors, _skill_md
    from dano.orchestrator.types import SkillSpec
    from dano.shared.enums import RiskLevel

    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.bad_submit",
        subsystem=Subsystem.OA,
        action="bad_submit",
        title="查询请假状态",
        risk_level=RiskLevel.L3,
        capabilities=[
            {"name": "query_status", "kind": "query_status", "title": "查询请假状态"},
            {
                "name": "submit", "kind": "submit", "title": "提交请假申请",
                "input_schema": {"type": "object", "properties": {
                    "processDefinitionId": {"type": "string"},
                    "entries": {"type": "array", "items": {"type": "object", "properties": {
                        "审批人1": {"type": "string"}, "审批人2": {"type": "string"},
                    }}},
                }, "required": ["processDefinitionId", "entries"]},
            },
        ],
    ))

    errors = _export_contract_errors(manifest)
    assert any("内部流程字段" in error for error in errors)
    assert any("人员列表误判" in error for error in errors)
    markdown = _skill_md(manifest, "dano-a-oa-bad-submit")
    assert "compatibility:" not in markdown
    assert "提交请假申请" in manifest.title


async def test_page_skill_reads_recording_metadata_from_asset_body():
    from dano.catalog.manifest import to_manifest

    store = _Store({AssetType.PAGE_SCRIPT: [_Env({
        "action": "submit_leave", "title": "提交请假", "actions": [], "dom_fingerprint": "",
        "user_fields": ["请假类型"], "required_fields": ["请假类型"],
        "field_types": {"请假类型": "enum"},
        "verification_status": "verified",
        "capabilities": [
            {
                "name": "query_status",
                "kind": "query_status",
                "step_ids": ["q"],
                "confirmed": True,
                "input_schema": {"type": "object", "properties": {"month": {"type": "string"}}},
                "output_schema": {"type": "object", "properties": {"missing_dates": {"type": "array"}}},
            },
            {
                "name": "submit_batch",
                "kind": "submit_batch",
                "step_ids": ["q", "s"],
                "confirmed": True,
                "input_schema": {"type": "object", "properties": {"entries": {"type": "array"}}},
                "output_schema": {"type": "object", "properties": {"success_dates": {"type": "array"}}},
            },
        ],
        "api_request": {
            "params": ["请假类型"], "recording_mode": "real_submit",
            "selects": [{"param": "请假类型", "options": ["事假", "病假"], "enum_source": "dom"}],
        },
    }, "submit_leave")]})

    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    m = to_manifest(next(s for s in reg.skills if s.action == "submit_leave"))

    assert m.verification_status == "verified"
    assert m.recording_mode == "real_submit"
    assert {c["name"] for c in m.capabilities} == {"query_status", "submit_batch"}
    assert m.capability == "submit_batch"
    assert m.call_protocol["requires_explicit_capability"] is True
    assert m.call_protocol["default_capability"] is None
    by_cap = {c["name"]: c for c in m.capabilities}
    assert by_cap["query_status"]["parameters"]["properties"]["month"]["type"] == "string"
    assert by_cap["query_status"]["output_schema"]["properties"]["missing_dates"]["type"] == "array"
    assert by_cap["query_status"]["call_protocol"]["invoke_path"].endswith(
        "/v1/skills/A-OA.submit_leave/capabilities/query_status/invoke"
    )
    assert m.output_schema["properties"]["success_dates"]["type"] == "array"
    assert m.call_metadata["fields"]["请假类型"]["enum_options"] == [{"label": "事假", "value": "事假"},
                                                                 {"label": "病假", "value": "病假"}]


def test_ruoyi_parses_approval_chain_from_prose():
    from dano.capabilities.oa_templates import match_template
    spec = {
        "paths": {"/workflow/handle/startFlow": {"post": {"description":
            "目录:\n| 流程 | templateId | 审批链 |\n|---|---|---|\n"
            "| 采购申请 | `purchase_template` | 发起人填表 → 直属主管(动态·部门负责人) → "
            "〔金额>5000 时〕行政审批 → 〔金额>30000 时〕总经理审批 → 系统自动记账 → 结束 |\n"}}},
        "components": {"schemas": {"AjaxResult": {}}},
    }
    meta = match_template(spec).parse_approval_chain(spec, "purchase_template")
    assert meta["flow"] == "采购申请"
    steps = [c["step"] for c in meta["approvalChain"]]
    assert "直属主管" in steps and "发起人填表" not in steps and "结束" not in steps
    assert {"field": "amount", "gt": 5000, "adds": "行政审批"} in meta["thresholds"]
    assert {"field": "amount", "gt": 30000, "adds": "总经理审批"} in meta["thresholds"]
    assert any(c.get("condition") == "amount>5000" for c in meta["approvalChain"])  # 金额>5000 不被切坏


async def test_internal_connector_hidden_catalog_visible():
    # 前置查询(visibility=internal)不进目录;普通查询(默认 catalog)正常露出
    store = _Store({AssetType.CONNECTOR: [
        _conn_env("query_my_todo"),                                  # 独立用户级查询 → 露出
        _conn_env("get_biz_form_info", visibility="internal", business="请假"),  # 前置查询 → 隐藏
    ]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    actions = {s.action for s in reg.skills}
    assert "query_my_todo" in actions
    assert "get_biz_form_info" not in actions          # internal 前置查询不泄漏成平级 skill


async def test_connector_carries_business_tag():
    store = _Store({AssetType.CONNECTOR: [_conn_env("query_leave_status", business="请假")]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    sk = next(s for s in reg.skills if s.action == "query_leave_status")
    assert sk.business == "请假"                        # 连接器也带 business,导出可归进同一本剧本


# ── WS4:系统特定(模板清单/表单解析)归 dialect,网关零字面量 ──
def test_ruoyi_dialect_parses_template_list_and_form():
    import json as _json
    from dano.capabilities.oa_templates import RuoYiFlowableTemplate, all_templates
    t = RuoYiFlowableTemplate()
    assert t.template_list_paths()                       # RuoYi 提供模板清单端点
    rows = t.parse_template_list({"code": 200, "rows": [
        {"id": "leave_template", "name": "请假申请", "typeName": "人事", "defKey": "leave", "enableFlag": "0"}]})
    assert rows == [{"templateId": "leave_template", "name": "请假申请", "type": "人事",
                     "defKey": "leave", "enableFlag": "0"}]
    assert t.parse_template_list({"code": 401}) == []    # 鉴权失败 → 空(网关据此提示 token 失效)
    designer = _json.dumps({"formData": {"list": [
        {"__vModel__": "leaveType", "__config__": {"label": "请假类型", "tag": "el-select"}},
        {"__vModel__": "reason", "__config__": {"label": "事由", "tag": "el-input"}}]}})
    fields = t.parse_form_fields({"code": 200, "data": {"formData": designer}})
    assert {f["key"] for f in fields} == {"leaveType", "reason"}
    assert any(d.name == "ruoyi-flowable" for d in all_templates())


def test_form_field_types_from_el_controls():
    # WS6:动态表单控件 = 字段类型的权威信源(比按名字猜更准,且能识别枚举)
    import json as _json
    from dano.capabilities.oa_templates import RuoYiFlowableTemplate
    designer = _json.dumps({"list": [
        {"__vModel__": "leaveType", "__config__": {"label": "请假类型", "tag": "el-select"}},
        {"__vModel__": "leaveDays", "__config__": {"label": "天数", "tag": "el-input-number"}},
        {"__vModel__": "startDate", "__config__": {"label": "开始", "tag": "el-date-picker"}},
        {"__vModel__": "agree", "__config__": {"label": "同意", "tag": "el-switch"}},
        {"__vModel__": "reason", "__config__": {"label": "事由", "tag": "el-input"}}]})
    fs = {f["key"]: f for f in RuoYiFlowableTemplate().parse_form_fields(
        {"code": 200, "data": {"formData": designer}})}
    assert fs["leaveDays"]["json_type"] == "number"
    assert fs["leaveType"]["json_type"] == "string" and fs["leaveType"]["enum"] is True
    assert fs["startDate"]["json_type"] == "string" and fs["startDate"]["enum"] is False
    assert fs["agree"]["json_type"] == "boolean"
    assert fs["reason"]["json_type"] == "string"


def test_base_dialect_no_system_literals():
    from dano.capabilities.oa_templates import OATemplate
    # 通用基类不携带任何系统端点(子类才有)→ 主流程对未知框架不会瞎打端点
    class _Bare(OATemplate):
        def matches(self, spec):  # noqa: ANN001
            return True
    b = _Bare()
    assert b.template_list_paths() == ()
    assert b.parse_template_list({"code": 200, "rows": [{"id": "x"}]}) == []


# ── WS3:复合流程动态发现(零硬编码业务配方)──
def test_discover_flows_composites_are_dynamic_not_hardcoded():
    from dano.onboarding.discovery import discover_flows
    spec = {
        "paths": {
            "/workflow/handle/startFlow": {"post": {"summary": "发起", "description":
                "目录:\n| 流程 | templateId | 审批链 |\n|---|---|---|\n"
                "| 采购申请 | `purchase_template` | 发起人填表 → 直属主管 → 〔金额>5000 时〕行政审批 → 结束 |\n"}},
            "/biz/flow/submit": {"post": {"summary": "提交"}},
        },
        "components": {"schemas": {"AjaxResult": {},
            "StartFlowReq": {"properties": {"templateId": {"enum": ["purchase_template", "custom_xyz_template"]}}}}},
    }
    flows = discover_flows(spec)
    comp = {f["flow"]: f for f in flows if f["kind"] == "composite"}
    # 来自 spec 的 templateId 枚举,而非写死的请假/出差
    assert set(comp) == {"submit_purchase", "submit_custom_xyz"}
    assert "submit_leave" not in comp and "submit_travel" not in comp     # 旧硬编码配方已删
    assert comp["submit_purchase"]["business_meta"].get("approvalChain")  # 审批链动态解析进提案
    assert comp["submit_custom_xyz"]["title"] == "custom_xyz_template"    # 非标模板也能动态发现


def test_discover_flows_bare_crud_no_composite():
    from dano.onboarding.discovery import discover_flows
    bare = {"paths": {"/users/list": {"get": {"summary": "用户列表"}}}, "components": {"schemas": {}}}
    flows = discover_flows(bare)
    assert not any(f["kind"] == "composite" for f in flows)               # 无模板 → 不强造复合流程


# ── P1·WS5:结构化 Goal(据材料动态生成)+ forbiddenSteps grounding ──
_GOAL_SPEC = {
    "paths": {
        "/workflow/handle/startFlow": {"post": {"summary": "发起", "description":
            "| 流程 | templateId | 审批链 |\n|---|---|---|\n"
            "| 采购申请 | `purchase_template` | 发起人填表 → 直属主管 → 〔金额>5000 时〕行政审批 → 结束 |\n"}},
        "/biz/flow/submit": {"post": {"summary": "提交"}},
        "/workflow/handle/admin/terminate": {"post": {"summary": "终止流程"}},
        "/workflow/handle/reject": {"post": {"summary": "驳回"}},
    },
    "components": {"schemas": {"AjaxResult": {},
        "StartFlowReq": {"properties": {"templateId": {"enum": ["purchase_template"]}}}}},
}


def test_build_goal_is_dynamic_and_marks_forbidden():
    from dano.capabilities.oa_templates import RuoYiFlowableTemplate
    from dano.onboarding.goal import build_goal
    steps = ["post_workflow_handle_startFlow", "post_biz_flow_submit"]
    g = build_goal(_GOAL_SPEC, RuoYiFlowableTemplate(), template_id="purchase_template",
                   business="采购申请", title="采购申请提交",
                   required_inputs=["amount"], optional_inputs=["comment"], candidate_steps=steps)
    assert g.selected_template == "purchase_template"
    assert g.candidate_steps == steps
    assert "当前流程已进入有效审批节点" in g.success_criteria      # 有审批链 → 派生该成功标准
    # 危险动作进 forbidden;提交链的正常步骤不进
    assert any("terminate" in f or "reject" in f for f in g.forbidden_steps)
    assert "post_biz_flow_submit" not in g.forbidden_steps
    assert "post_workflow_handle_startFlow" not in g.forbidden_steps


def test_goal_grounding_rejects_forbidden_step():
    from dano.capabilities.oa_templates import RuoYiFlowableTemplate
    from dano.onboarding.goal import build_goal, goal_grounding
    g = build_goal(_GOAL_SPEC, RuoYiFlowableTemplate(), template_id="purchase_template",
                   business="采购申请", candidate_steps=["post_biz_flow_submit"])
    assert goal_grounding(g, ["post_workflow_handle_startFlow", "post_biz_flow_submit"]) == []  # 干净
    bad = goal_grounding(g, ["post_workflow_handle_reject"])                                     # 编入驳回他人
    assert bad and "forbiddenSteps" in bad[0]


def test_forbidden_actions_excludes_normal_submit():
    from dano.onboarding.goal import forbidden_actions
    forb = forbidden_actions(_GOAL_SPEC)
    assert "post_biz_flow_submit" not in forb and "post_workflow_handle_startFlow" not in forb
    assert any("terminate" in f for f in forb)


async def test_workflow_skill_carries_business_meta_to_manifest():
    from dano.catalog.manifest import to_manifest
    wf = WorkflowSkillBody(
        action="submit_purchase", title="采购申请提交",
        steps=[WorkflowStep(action="start_flow", inputs={"templateId": "const:purchase_template"})],
        user_fields=["amount"], required_fields=["amount"],
        business="采购申请",
        business_meta={"approvalChain": [{"step": "直属主管"}], "thresholds": []})
    store = _Store({AssetType.WORKFLOW: [_Env(wf.model_dump(), "submit_purchase")],
                    AssetType.CONNECTOR: [_conn_env("start_flow")]})
    reg = await SkillRegistry.from_store(store, tenant="t", subsystems=[Subsystem.OA])
    sk = next(s for s in reg.skills if s.action == "submit_purchase")
    assert sk.business_meta.get("approvalChain")          # workflow 也带出审批链(原先被丢)
    assert to_manifest(sk).business_meta.get("approvalChain")
