from __future__ import annotations

import json
import subprocess
import sys

from dano.catalog.manifest import to_manifest
from dano.export.agent_skills import _export_contract_errors, _options_md, _skill_md, _write_skill
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem


def _hotel_manifest():
    return to_manifest(SkillSpec(
        skill_id="A-OA.hotel_apply",
        subsystem=Subsystem.OA,
        action="hotel_apply",
        title="酒店申请",
        risk_level=RiskLevel.L3,
        capabilities=[
            {
                "name": "query_hotel_apply",
                "kind": "query_status",
                "title": "查询酒店申请记录",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pageNo": {"type": "integer", "default": 1},
                        "pageSize": {"type": "integer", "default": 10},
                        "流程状态": {
                            "type": "string",
                            "format": "name-ref",
                            "enum": ["未提交", "审批中"],
                            "default": "审批中",
                        },
                    },
                    "required": [],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "records": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"id": {"type": "string"}},
                            },
                        },
                    },
                },
            },
            {
                "name": "withdraw_hotel_apply",
                "kind": "submit",
                "title": "撤回酒店申请",
                "requires_human_confirm": True,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "default": "OA-JDSQ-20260713001"},
                        "撤回原因": {"type": "string", "default": "行程变更"},
                    },
                    "required": ["id", "撤回原因"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "message": {"type": "string"},
                    },
                    "required": ["success"],
                },
            },
        ],
    ))


def test_exported_skill_follows_native_question_contract_and_uses_semantic_scope():
    manifest = _hotel_manifest()
    markdown = _skill_md(manifest, "dano-a-oa-hotel-apply")

    assert "`query_hotel_apply`" in markdown
    assert "`withdraw_hotel_apply`" in markdown
    assert "查询酒店申请记录" in markdown
    assert "撤回酒店申请" in markdown
    assert "或相关 A-OA 操作" not in markdown

    assert "原生调用 `ask_user_question`" in markdown
    assert "`questions` 数组" in markdown
    assert "不要逐字段拆成多轮" in markdown
    assert "多个表单" in markdown
    assert "一次性汇总" in markdown
    assert "不得按表单" in markdown
    assert "每次回复" in markdown and "一次" in markdown
    assert "只收集一个非确认字段" in markdown
    assert "业务上确实必填" in markdown
    assert "多题按 questions 的 `id`" in markdown
    assert "非空" in markdown and "占位" in markdown
    assert "空字符串或安全默认值" not in markdown
    assert "录制样例必须保留为推荐值" in markdown
    assert "推荐默认值只用于 `ask_user_question` 展示" in markdown
    assert "`x-dano-apply-default: true`" in markdown
    assert "取消" in markdown and "停止" in markdown
    assert "校验错误" in markdown and "静默" in markdown
    assert "`inputType: \"date\"`" in markdown
    assert "`question` 与 `confirm: true`" in markdown
    assert "`status=answered`" in markdown and "`answer=true`" in markdown
    assert "`partial_success`" not in markdown

    interaction = manifest.call_protocol["interaction_protocol"]
    assert interaction["max_calls_per_assistant_response"] == 1
    assert interaction["non_confirmation_default"]["string_must_be_non_empty"] is True
    assert interaction["confirmation"]["allowed_keys"] == ["question", "confirm"]
    assert interaction["result_statuses"] == ["answered", "cancelled"]
    assert interaction["single_field_collection"]["mode"] == "top_level"
    assert interaction["multi_field_collection"]["top_level_field_configuration_forbidden"] is True
    assert interaction["multi_field_collection"]["aggregate_across"] == [
        "forms", "form_sections", "workflow_steps",
    ]
    assert interaction["multi_field_collection"]["per_form_calls_forbidden"] is True
    assert interaction["field_rules"]["required_default"] is False
    assert interaction["field_rules"]["date"]["dateFormat_required"] is True
    assert interaction["field_rules"]["choices"] == {
        "static": "options",
        "remote": "dataSource",
        "remote_input_types": ["select", "treeSelect"],
    }
    assert interaction["answer_mapping"]["multiple"].startswith("result.answer object")
    assert interaction["validation_error_behavior"].startswith("retry_silently")
    assert all("interaction_protocol" in cap["call_protocol"] for cap in manifest.capabilities)
    assert _export_contract_errors(manifest) == []


def test_exported_skill_renders_schema_defaults_in_tables_and_examples():
    markdown = _skill_md(_hotel_manifest(), "dano-a-oa-hotel-apply")

    assert "推荐默认值" in markdown
    assert "录制推荐值，需用户确认" in markdown
    assert '"pageNo": 1' in markdown
    assert '"pageSize": 10' in markdown
    assert '"流程状态": "审批中"' in markdown
    assert '"撤回原因": "行程变更"' in markdown


def test_options_reference_only_claims_live_lookup_with_grounded_source():
    static_manifest = _hotel_manifest()
    static_markdown = _options_md(static_manifest)
    assert static_markdown is not None
    assert "离线快照" in static_markdown
    assert "Dano 直接调用字段来源接口返回当前" not in static_markdown

    dynamic_skill = SkillSpec(
        skill_id="A-OA.dynamic_options",
        subsystem=Subsystem.OA,
        action="dynamic_options",
        title="动态选项",
        risk_level=RiskLevel.L2,
        capabilities=[{
            "name": "query_people",
            "kind": "query_status",
            "title": "查询人员",
            "inputs": [{
                "key": "申请人",
                "path": "query.userId",
                "source_kind": "api_option",
                "source": {"source_url": "/admin-api/system/user/simple-list"},
            }],
            "input_schema": {
                "type": "object",
                "properties": {"申请人": {"type": "string", "format": "name-ref"}},
                "required": [],
            },
        }],
    )
    dynamic_markdown = _options_md(to_manifest(dynamic_skill))
    assert dynamic_markdown is not None
    assert "实时接口" in dynamic_markdown
    assert "--list-options 申请人" in dynamic_markdown


def test_exported_hotel_skill_has_executable_question_sop_and_table_formatter(tmp_path):
    manifest = to_manifest(SkillSpec(
        skill_id="A-OA.action-c5b324fc580c4d5fb2847a5d5fb6973c",
        subsystem=Subsystem.OA,
        action="action-c5b324fc580c4d5fb2847a5d5fb6973c",
        title="酒店申请",
        risk_level=RiskLevel.L3,
        capabilities=[{
            "name": "submit_hotel_apply",
            "kind": "submit",
            "title": "提交酒店申请",
            "requires_human_confirm": True,
            "inputs": [
                {"key": "hotelName", "display_name": "酒店名称"},
                {"key": "city", "display_name": "城市", "source_kind": "api_option",
                 "source": {"source_url": "/api/cities", "source_method": "GET",
                            "value_key": "id", "label_key": "name"}},
                {"key": "remark", "display_name": "申请说明"},
            ],
            "input_schema": {
                "type": "object",
                "properties": {
                    "hotelName": {
                        "type": "string", "description": "酒店名称", "default": "杭州酒店",
                    },
                    "city": {
                        "type": "string", "format": "name-ref", "description": "城市",
                        "default": "杭州", "x-options-source": True,
                        "x-options-source-meta": {
                            "source_url": "/api/cities", "source_method": "GET",
                            "value_key": "id", "label_key": "name",
                        },
                    },
                    "remark": {
                        "type": "string", "x-dano-business-type": "textarea",
                        "description": "申请说明", "default": "出差住宿",
                    },
                },
                "required": ["hotelName", "city", "remark"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "records": {
                        "type": "array",
                        "items": {"type": "object", "properties": {
                            "id": {"type": "string"}, "status": {"type": "string"},
                        }},
                    },
                },
            },
        }],
    ))

    folder = _write_skill(tmp_path, manifest)
    markdown = (folder / "SKILL.md").read_text(encoding="utf-8")

    assert 'name: "酒店申请"' in markdown
    assert "3. **一次性收集全部表单项。**" in markdown
    assert "`submit_hotel_apply`" in markdown
    assert "| `hotelName` | 酒店名称 | `text`" in markdown
    assert "| `city` | 城市 | `select`" in markdown
    assert '"endpoint": "/api/cities"' in markdown
    assert "| `remark` | 申请说明 | `textarea`" in markdown
    assert "按 `answer` 对象的 `id` 映射为能力参数" in markdown
    assert "Markdown 表格呈现" in markdown

    formatter = folder / "scripts" / "format_list.py"
    result = subprocess.run(
        [sys.executable, str(formatter), "--json", json.dumps({
            "output": {"records": [{"id": "H1", "status": "审批中"}]},
        }, ensure_ascii=False)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "| id | status |" in result.stdout
    assert "| H1 | 审批中 |" in result.stdout
